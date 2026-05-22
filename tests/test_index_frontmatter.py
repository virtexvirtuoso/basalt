"""SQLite persistence tests for status/type/confidence columns.

Pins that the indexer:
1. Persists the three frontmatter fields on `notes` table for fresh DBs
2. Idempotently migrates pre-existing DBs that lack the columns
3. Returns NULL for notes without those frontmatter fields

Added 2026-05-22 as Phase A of [[V0-Verb-Quality-Fixes-Spec-2026-05-22]].
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from basalt.index import open_db, upsert_note
from basalt.vault import parse_note


@pytest.fixture
def fresh_db(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "test.db")


def _write(vault_root: Path, rel: str, body: str) -> Path:
    path = vault_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_notes_table_has_status_type_confidence_columns_on_fresh_db(
    fresh_db: sqlite3.Connection,
):
    cols = {row[1] for row in fresh_db.execute("PRAGMA table_info(notes)")}
    assert "status" in cols
    assert "type" in cols
    assert "confidence" in cols


def test_upsert_persists_all_three_fields(
    fresh_db: sqlite3.Connection, tmp_path: Path
):
    vault = tmp_path / "vault"
    path = _write(
        vault,
        "note.md",
        "---\nstatus: archived\ntype: reference\nconfidence: HIGH\n---\n\nbody\n",
    )
    note = parse_note(path, vault)
    nid = upsert_note(fresh_db, note)

    row = fresh_db.execute(
        "SELECT status, type, confidence FROM notes WHERE id = ?", (nid,)
    ).fetchone()
    assert row["status"] == "archived"
    assert row["type"] == "reference"
    assert row["confidence"] == "HIGH"


def test_upsert_persists_nulls_when_frontmatter_omits_fields(
    fresh_db: sqlite3.Connection, tmp_path: Path
):
    vault = tmp_path / "vault"
    path = _write(vault, "note.md", "---\ntitle: foo\n---\n\nbody\n")
    note = parse_note(path, vault)
    nid = upsert_note(fresh_db, note)

    row = fresh_db.execute(
        "SELECT status, type, confidence FROM notes WHERE id = ?", (nid,)
    ).fetchone()
    assert row["status"] is None
    assert row["type"] is None
    assert row["confidence"] is None


def test_upsert_updates_fields_on_conflict(
    fresh_db: sqlite3.Connection, tmp_path: Path
):
    """When a note is re-indexed (rel_path conflict), the new status/type/
    confidence values must overwrite the old ones — staleness here would
    cause filters to act on outdated frontmatter."""
    vault = tmp_path / "vault"
    path = _write(
        vault, "note.md", "---\nstatus: draft\nconfidence: LOW\n---\n\nbody\n"
    )
    note = parse_note(path, vault)
    nid1 = upsert_note(fresh_db, note)

    # User updates the frontmatter
    path.write_text(
        "---\nstatus: validated\nconfidence: HIGH\n---\n\nbody\n",
        encoding="utf-8",
    )
    note2 = parse_note(path, vault)
    nid2 = upsert_note(fresh_db, note2)

    assert nid1 == nid2  # same row, upserted
    row = fresh_db.execute(
        "SELECT status, confidence FROM notes WHERE id = ?", (nid1,)
    ).fetchone()
    assert row["status"] == "validated"
    assert row["confidence"] == "HIGH"


def test_open_db_migrates_legacy_db_missing_new_columns(tmp_path: Path):
    """A v0.0.14-era DB has no status/type/confidence columns.
    `open_db()` must add them via ALTER TABLE without destroying existing data."""
    db_path = tmp_path / "legacy.db"

    # Simulate a v0.0.14 DB: create the notes table WITHOUT the new columns
    legacy_conn = sqlite3.connect(db_path)
    legacy_conn.executescript("""
        CREATE TABLE notes (
            id INTEGER PRIMARY KEY,
            rel_path TEXT UNIQUE NOT NULL,
            stem TEXT NOT NULL,
            title TEXT NOT NULL,
            created TEXT,
            updated TEXT,
            word_count INTEGER NOT NULL,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            tags TEXT
        );
        INSERT INTO notes (rel_path, stem, title, word_count, content, content_hash, tags)
        VALUES ('old.md', 'old', 'Old Note', 5, 'body', 'hash123', 'a,b');
    """)
    legacy_conn.commit()
    legacy_conn.close()

    # Now open it via open_db — should migrate, not crash
    conn = open_db(db_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(notes)")}
    assert "status" in cols
    assert "type" in cols
    assert "confidence" in cols

    # Existing row preserved, new columns NULL
    row = conn.execute(
        "SELECT rel_path, status, type, confidence FROM notes WHERE rel_path = ?",
        ("old.md",),
    ).fetchone()
    assert row["rel_path"] == "old.md"
    assert row["status"] is None
    assert row["type"] is None
    assert row["confidence"] is None


def test_open_db_migration_is_idempotent(tmp_path: Path):
    """open_db() called twice on the same DB must not raise (ALTER TABLE
    fails if column exists). Idempotency is what makes the migration safe."""
    db_path = tmp_path / "test.db"
    conn1 = open_db(db_path)
    conn1.close()
    # Should not raise: columns already exist
    conn2 = open_db(db_path)
    cols = {row[1] for row in conn2.execute("PRAGMA table_info(notes)")}
    assert "status" in cols
    assert "type" in cols
    assert "confidence" in cols

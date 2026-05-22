"""SQLite index of the corpus + wikilink graph."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from basalt.vault import Note


SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id              INTEGER PRIMARY KEY,
    rel_path        TEXT UNIQUE NOT NULL,
    stem            TEXT NOT NULL,
    title           TEXT NOT NULL,
    created         TEXT,
    updated         TEXT,
    word_count      INTEGER NOT NULL,
    content         TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    tags            TEXT,
    status          TEXT,
    type            TEXT,
    confidence      TEXT
);
CREATE INDEX IF NOT EXISTS idx_notes_stem ON notes(stem);
CREATE INDEX IF NOT EXISTS idx_notes_updated ON notes(updated);
CREATE INDEX IF NOT EXISTS idx_notes_created ON notes(created);
-- Note: idx_notes_status and idx_notes_type are created post-migration in open_db,
-- because legacy DBs lack the columns when SCHEMA executes.

CREATE TABLE IF NOT EXISTS links (
    from_note_id    INTEGER NOT NULL,
    target          TEXT NOT NULL,
    target_note_id  INTEGER,
    FOREIGN KEY(from_note_id)   REFERENCES notes(id),
    FOREIGN KEY(target_note_id) REFERENCES notes(id)
);
CREATE INDEX IF NOT EXISTS idx_links_from ON links(from_note_id);
CREATE INDEX IF NOT EXISTS idx_links_target ON links(target);
CREATE INDEX IF NOT EXISTS idx_links_to ON links(target_note_id);

CREATE TABLE IF NOT EXISTS embeddings (
    note_id         INTEGER PRIMARY KEY,
    model           TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    dim             INTEGER NOT NULL,
    vec             BLOB NOT NULL,
    FOREIGN KEY(note_id) REFERENCES notes(id)
);

CREATE TABLE IF NOT EXISTS meta (
    key             TEXT PRIMARY KEY,
    value           TEXT
);

-- Calibration layer: every Brief finding logged with falsification rules,
-- re-evaluated on `basalt audit`. The longer the user runs Basalt, the
-- more valuable their track record becomes.
CREATE TABLE IF NOT EXISTS briefs (
    id              INTEGER PRIMARY KEY,
    verb            TEXT NOT NULL,
    finding_key     TEXT NOT NULL,        -- stable id for dedup across runs
    finding_json    TEXT NOT NULL,        -- full payload for re-eval + history
    falsification   TEXT NOT NULL,        -- JSON array of {kind, params, text}
    created_at      TEXT NOT NULL,        -- ISO date YYYY-MM-DD
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending/confirmed/falsified
    verdict_at      TEXT,                  -- ISO date when status moved off pending
    verdict_reason  TEXT                   -- why
);
CREATE INDEX IF NOT EXISTS idx_briefs_verb     ON briefs(verb);
CREATE INDEX IF NOT EXISTS idx_briefs_finding  ON briefs(verb, finding_key);
CREATE INDEX IF NOT EXISTS idx_briefs_status   ON briefs(status);
CREATE INDEX IF NOT EXISTS idx_briefs_created  ON briefs(created_at);
"""


def _ensure_columns(
    conn: sqlite3.Connection, table: str, columns: dict[str, str]
) -> None:
    """Idempotent ALTER TABLE ADD COLUMN for missing columns.

    SQLite's CREATE TABLE IF NOT EXISTS does not add columns to existing
    tables — that requires ALTER TABLE. ALTER TABLE ADD COLUMN fails if
    the column already exists, so we check `PRAGMA table_info` first.

    Existing rows get NULL for the new column, which is the correct
    behavior for frontmatter-aware filters (missing == not-set).
    """
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for col_name, col_def in columns.items():
        if col_name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")


def open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # Migrate pre-existing DBs that predate the frontmatter columns (added 2026-05-22).
    # Must run BEFORE creating indexes on these columns — legacy DBs would fail otherwise.
    _ensure_columns(
        conn,
        "notes",
        {"status": "TEXT", "type": "TEXT", "confidence": "TEXT"},
    )
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_notes_status ON notes(status);
        CREATE INDEX IF NOT EXISTS idx_notes_type ON notes(type);
        """
    )
    conn.commit()
    return conn


def _date_str(d: date | None) -> str | None:
    return d.isoformat() if d else None


def upsert_note(conn: sqlite3.Connection, note: Note) -> int:
    cur = conn.execute(
        """
        INSERT INTO notes (rel_path, stem, title, created, updated, word_count, content, content_hash, tags, status, type, confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(rel_path) DO UPDATE SET
            stem=excluded.stem,
            title=excluded.title,
            created=COALESCE(notes.created, excluded.created),
            updated=excluded.updated,
            word_count=excluded.word_count,
            content=excluded.content,
            content_hash=excluded.content_hash,
            tags=excluded.tags,
            status=excluded.status,
            type=excluded.type,
            confidence=excluded.confidence
        RETURNING id
        """,
        (
            note.rel_path,
            note.stem,
            note.title,
            _date_str(note.created),
            _date_str(note.updated),
            note.word_count,
            note.content,
            note.content_hash,
            ",".join(note.tags),
            note.status,
            note.type,
            note.confidence,
        ),
    )
    return cur.fetchone()[0]


def replace_links(conn: sqlite3.Connection, note_id: int, targets: list[str]) -> None:
    conn.execute("DELETE FROM links WHERE from_note_id = ?", (note_id,))
    if targets:
        conn.executemany(
            "INSERT INTO links (from_note_id, target) VALUES (?, ?)",
            [(note_id, t) for t in targets],
        )


def resolve_link_targets(conn: sqlite3.Connection) -> int:
    """Match wikilink targets to notes by stem (case-insensitive). Returns # resolved."""
    rows = conn.execute("SELECT id, stem FROM notes").fetchall()
    stem_to_id = {row["stem"].lower(): row["id"] for row in rows}
    cur = conn.execute("SELECT rowid, target FROM links WHERE target_note_id IS NULL")
    updates = []
    for row in cur.fetchall():
        nid = stem_to_id.get(row["target"].lower())
        if nid:
            updates.append((nid, row["rowid"]))
    if updates:
        conn.executemany("UPDATE links SET target_note_id = ? WHERE rowid = ?", updates)
    return len(updates)

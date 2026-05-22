"""Integration test for path-based reference exclusion via SQL.

Pins that sql_exclude_clause() filters SKILL.md / README.md / *-reference.md
files even when they lack `type: reference` frontmatter — the case that
caught 2 of 3 Buried Insights in the 2026-05-21 dogfood run.

Added 2026-05-22.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from basalt.filters import sql_exclude_clause
from basalt.index import open_db, upsert_note
from basalt.vault import parse_note


def _write(vault_root: Path, rel: str, body: str) -> Path:
    path = vault_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_sql_filter_excludes_skill_md_without_frontmatter(tmp_path: Path):
    """A SKILL.md with no `type:` frontmatter must still be filtered out."""
    vault = tmp_path / "vault"
    long_ago = (date.today() - timedelta(days=200)).isoformat()

    _write(
        vault,
        "08-AI/Skills/Dev/mcp-builder/SKILL.md",
        f"---\ncreated: {long_ago}\n---\n\n"
        f"A skill that wraps the mcp builder.\n",
    )
    _write(
        vault,
        "02-Projects/Polyclawd/Strategy/edge.md",
        f"---\ncreated: {long_ago}\n---\n\n"
        f"The sustainable edge is speed plus intelligence.\n",
    )

    db = open_db(tmp_path / "test.db")
    for md in vault.rglob("*.md"):
        note = parse_note(md, vault)
        if note:
            upsert_note(db, note)

    rows = db.execute(
        f"SELECT rel_path FROM notes WHERE {sql_exclude_clause()}"
    ).fetchall()
    paths = {r["rel_path"] for r in rows}

    assert "02-Projects/Polyclawd/Strategy/edge.md" in paths
    assert "08-AI/Skills/Dev/mcp-builder/SKILL.md" not in paths


def test_sql_filter_excludes_reference_md_files(tmp_path: Path):
    """*-reference.md files are filtered even without frontmatter."""
    vault = tmp_path / "vault"
    long_ago = (date.today() - timedelta(days=200)).isoformat()

    _write(
        vault,
        "docs/pdf-reference.md",
        f"---\ncreated: {long_ago}\n---\n\nReference doc.\n",
    )
    _write(
        vault,
        "docs/normal.md",
        f"---\ncreated: {long_ago}\n---\n\nNormal note.\n",
    )

    db = open_db(tmp_path / "test.db")
    for md in vault.rglob("*.md"):
        note = parse_note(md, vault)
        if note:
            upsert_note(db, note)

    rows = db.execute(
        f"SELECT rel_path FROM notes WHERE {sql_exclude_clause()}"
    ).fetchall()
    paths = {r["rel_path"] for r in rows}

    assert "docs/normal.md" in paths
    assert "docs/pdf-reference.md" not in paths


def test_sql_filter_excludes_readme_and_changelog(tmp_path: Path):
    vault = tmp_path / "vault"
    long_ago = (date.today() - timedelta(days=200)).isoformat()

    for rel in ["README.md", "CHANGELOG.md", "CONTRIBUTING.md"]:
        _write(vault, rel, f"---\ncreated: {long_ago}\n---\n\ndocs.\n")
    _write(vault, "real-note.md", f"---\ncreated: {long_ago}\n---\n\nclaim.\n")

    db = open_db(tmp_path / "test.db")
    for md in vault.rglob("*.md"):
        note = parse_note(md, vault)
        if note:
            upsert_note(db, note)

    rows = db.execute(
        f"SELECT rel_path FROM notes WHERE {sql_exclude_clause()}"
    ).fetchall()
    paths = {r["rel_path"] for r in rows}

    assert "real-note.md" in paths
    assert "README.md" not in paths
    assert "CHANGELOG.md" not in paths
    assert "CONTRIBUTING.md" not in paths


def test_sql_filter_keeps_root_belief_docs(tmp_path: Path):
    """MEMORY.md / SOUL.md are belief-shaped, not reference-shaped, despite
    being uppercase and root-level. The path filter must NOT exclude them."""
    vault = tmp_path / "vault"
    long_ago = (date.today() - timedelta(days=200)).isoformat()

    for rel in ["MEMORY.md", "SOUL.md", "notes.md"]:
        _write(vault, rel, f"---\ncreated: {long_ago}\n---\n\nbelief.\n")

    db = open_db(tmp_path / "test.db")
    for md in vault.rglob("*.md"):
        note = parse_note(md, vault)
        if note:
            upsert_note(db, note)

    rows = db.execute(
        f"SELECT rel_path FROM notes WHERE {sql_exclude_clause()}"
    ).fetchall()
    paths = {r["rel_path"] for r in rows}

    assert "MEMORY.md" in paths
    assert "SOUL.md" in paths
    assert "notes.md" in paths

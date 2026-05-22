"""Buried Insight integration with frontmatter-aware filters.

Per [[V0-Verb-Quality-Fixes-Spec-2026-05-22]] Phase B: archived / draft / wip
notes and reference-type notes must not appear as buried-insight candidates.

This is the dogfood-derived test (the 2026-05-21 full-vault run returned
reference docs as 2/3 of its buried insights — these tests pin that fix).
Added 2026-05-22.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from basalt.buried import BuriedInsightVerb
from basalt.index import open_db, upsert_note
from basalt.vault import parse_note


def _write(vault_root: Path, rel: str, body: str) -> Path:
    path = vault_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _setup(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    """Vault with one normal note and three notes that should be filtered out."""
    vault = tmp_path / "vault"
    long_ago = (date.today() - timedelta(days=400)).isoformat()
    recent = (date.today() - timedelta(days=30)).isoformat()

    # A normal old, dormant claim — should remain a candidate
    _write(
        vault,
        "claims/normal.md",
        f"---\ncreated: {long_ago}\nupdated: {long_ago}\n---\n\n"
        "This is a load-bearing claim about the world that I wrote and never returned to.\n",
    )

    # Archived — should be filtered out
    _write(
        vault,
        "claims/old-archived.md",
        f"---\ncreated: {long_ago}\nupdated: {long_ago}\nstatus: archived\n---\n\n"
        "An old claim I explicitly archived.\n",
    )

    # Reference doc — should be filtered out
    _write(
        vault,
        "skills/mcp-reference.md",
        f"---\ncreated: {long_ago}\nupdated: {long_ago}\ntype: reference\n---\n\n"
        "Reference doc that I link to but don't return to.\n",
    )

    # Draft — should be filtered out
    _write(
        vault,
        "claims/draft.md",
        f"---\ncreated: {long_ago}\nupdated: {long_ago}\nstatus: draft\n---\n\n"
        "An unfinished thought I haven't returned to.\n",
    )

    # Validators — recent notes so the buried-insight heuristic has work to do.
    # We don't need exact link/embedding wiring; the FILTER step runs BEFORE
    # scoring, so even with no validators the filter behavior is observable
    # through _candidates() output directly.
    _write(
        vault,
        "recent.md",
        f"---\ncreated: {recent}\nupdated: {recent}\n---\n\nA recent note.\n",
    )

    db = open_db(tmp_path / "test.db")
    for md in vault.rglob("*.md"):
        note = parse_note(md, vault)
        if note:
            upsert_note(db, note)
    return db, vault


def test_buried_candidates_exclude_archived_notes(tmp_path: Path):
    db, _ = _setup(tmp_path)
    verb = BuriedInsightVerb(db)
    paths = {c["rel_path"] for c in verb._candidates()}
    assert "claims/normal.md" in paths
    assert "claims/old-archived.md" not in paths


def test_buried_candidates_exclude_reference_type(tmp_path: Path):
    db, _ = _setup(tmp_path)
    verb = BuriedInsightVerb(db)
    paths = {c["rel_path"] for c in verb._candidates()}
    assert "skills/mcp-reference.md" not in paths


def test_buried_candidates_exclude_draft_notes(tmp_path: Path):
    db, _ = _setup(tmp_path)
    verb = BuriedInsightVerb(db)
    paths = {c["rel_path"] for c in verb._candidates()}
    assert "claims/draft.md" not in paths


def test_buried_candidates_keep_normal_claim(tmp_path: Path):
    """Regression guard: the filter must NOT remove notes without
    status/type frontmatter (the common case)."""
    db, _ = _setup(tmp_path)
    verb = BuriedInsightVerb(db)
    paths = {c["rel_path"] for c in verb._candidates()}
    assert "claims/normal.md" in paths
    assert "recent.md" in paths

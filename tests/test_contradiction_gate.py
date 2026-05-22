"""Integration test for the contradiction subject-overlap gate.

End-to-end: build a vault containing two notes that would historically
fire as a contradiction (high cosine + asymmetric negation marker) but
share no subject. Assert ContradictionVerb returns zero pairs.

Added 2026-05-22 as Phase C of [[V0-Verb-Quality-Fixes-Spec-2026-05-22]].
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from basalt.contradiction import ContradictionVerb
from basalt.index import open_db, replace_links, upsert_note
from basalt.vault import parse_note


def _write(vault_root: Path, rel: str, body: str) -> Path:
    path = vault_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _add_embedding(conn: sqlite3.Connection, note_id: int, vec: np.ndarray):
    conn.execute(
        "INSERT OR REPLACE INTO embeddings (note_id, model, content_hash, dim, vec) VALUES (?, ?, ?, ?, ?)",
        (note_id, "test", "h", 768, vec.tobytes()),
    )


def test_unrelated_notes_with_negation_no_longer_pair(tmp_path: Path):
    """The dogfood-style false positive: two notes with high cosine + a
    negation marker but no shared subject must NOT be flagged."""
    vault = tmp_path / "vault"
    long_ago = (date.today() - timedelta(days=200)).isoformat()
    body80 = " ".join(["thinking"] * 80) + "."

    # Note A: structural / vault config — no tags, root-level
    _write(
        vault,
        "VAULT_INDEX.md",
        f"---\ncreated: {long_ago}\nupdated: {long_ago}\n---\n\n"
        f"This is not the final word on the structure. {body80}\n",
    )
    # Note B: a daily-note style entry — different folder, different tag
    _write(
        vault,
        "01-Daily/2026-04-30.md",
        f"---\ncreated: {long_ago}\nupdated: {long_ago}\ntags: daily\n---\n\n"
        f"Today is not a quiet day for partnerships. {body80}\n",
    )

    db = open_db(tmp_path / "test.db")
    # Index notes
    note_ids = {}
    for md in vault.rglob("*.md"):
        note = parse_note(md, vault)
        if note:
            nid = upsert_note(db, note)
            replace_links(db, nid, note.wikilinks)
            note_ids[note.rel_path] = nid

    # Plant identical embeddings so cosine similarity is ~1.0 (forces the
    # historical false-positive condition)
    rng = np.random.default_rng(42)
    v = rng.standard_normal(768).astype(np.float32)
    v /= np.linalg.norm(v)
    for nid in note_ids.values():
        _add_embedding(db, nid, v)

    verb = ContradictionVerb(db)
    pairs = verb.run(top_n=5)
    paths = [
        (p.note_a_path, p.note_b_path) for p in pairs
    ]
    # Must not pair these two
    assert ("VAULT_INDEX.md", "01-Daily/2026-04-30.md") not in paths
    assert ("01-Daily/2026-04-30.md", "VAULT_INDEX.md") not in paths


def test_related_notes_with_negation_still_pair(tmp_path: Path):
    """Regression guard: when notes DO share a subject (same folder),
    the subject-overlap gate must NOT block them — only the pure-noise
    pairs get filtered."""
    vault = tmp_path / "vault"
    long_ago = (date.today() - timedelta(days=200)).isoformat()
    body80 = " ".join(["polyclawd"] * 80) + "."

    # Two notes in the SAME project folder — share folder subject
    _write(
        vault,
        "02-Projects/Polyclawd/edge-2024.md",
        f"---\ncreated: {long_ago}\nupdated: {long_ago}\n---\n\n"
        f"The edge is speed plus intelligence. {body80}\n",
    )
    _write(
        vault,
        "02-Projects/Polyclawd/edge-2026.md",
        f"---\ncreated: {long_ago}\nupdated: {long_ago}\n---\n\n"
        f"The edge is not speed but careful timing. {body80}\n",
    )

    db = open_db(tmp_path / "test.db")
    note_ids = {}
    for md in vault.rglob("*.md"):
        note = parse_note(md, vault)
        if note:
            nid = upsert_note(db, note)
            replace_links(db, nid, note.wikilinks)
            note_ids[note.rel_path] = nid

    rng = np.random.default_rng(7)
    v = rng.standard_normal(768).astype(np.float32)
    v /= np.linalg.norm(v)
    for nid in note_ids.values():
        _add_embedding(db, nid, v)

    verb = ContradictionVerb(db)
    pairs = verb.run(top_n=5)
    # These two share a folder subject, so the gate must let them through.
    # They may or may not survive the downstream evidence-scoring (that's
    # a separate concern); the gate's job is to not pre-filter them.
    # Direct gate verification:
    from basalt.contradiction import _subject_set, _subjects_overlap
    a = _subject_set("02-Projects/Polyclawd/edge-2024.md", "", [])
    b = _subject_set("02-Projects/Polyclawd/edge-2026.md", "", [])
    assert _subjects_overlap(a, b)

"""Smoke tests — exercise the full index→brief pipeline on the bundled sample vault.

These tests don't mock anything, but they DO mock embeddings (via a fake httpx
transport) so they don't require Ollama to be running.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from basalt.vault import walk_vault, parse_note, _extract_wikilinks
from basalt.index import open_db, upsert_note, replace_links, resolve_link_targets
from basalt.buried import (
    _extract_claim_quote,
    _strip_md,
    _split_sentences,
    compute_vault_age_days,
    compute_vault_aware_thresholds,
    find_buried_insights,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_VAULT = REPO_ROOT / "examples" / "sample-vault"


# ── unit-level checks ─────────────────────────────────────────

def test_wikilink_extraction():
    text = "See [[HYPOTHESIS]] and [[Note Title|alias]] and [[anchored#section]]."
    assert _extract_wikilinks(text) == ["HYPOTHESIS", "Note Title", "anchored"]


def test_strip_md_removes_common_markup():
    s = "**bold** and *italic* and `code` and [link](url) and [[wiki]] and ==hi=="
    assert _strip_md(s) == "bold and italic and code and link and wiki and hi"


def test_sentence_splitter_handles_basic_prose():
    s = "First sentence. Second one! Third? And a fourth."
    parts = _split_sentences(s)
    assert len(parts) == 4
    assert parts[0] == "First sentence."


def test_quote_extraction_picks_punchline_in_callout():
    body = """
> [!warning] Diminishing Returns
> The setup ran fine for years. As of late 2026, returns are flatter. However:
>
> - bullet one
> - bullet two
>
> The sustainable edge isn't speed alone — it's speed + intelligence.
"""
    quote, prov = _extract_claim_quote(body)
    assert "speed + intelligence" in quote, f"got: {quote}"
    assert prov == "callout body"


# ── pipeline check on the sample vault ────────────────────────

def _fake_embedding(*args, **kwargs):
    """Deterministic 768-d embedding seeded by content hash. No network."""
    rng = np.random.default_rng(42)
    v = rng.standard_normal(768).astype(np.float32)
    v /= np.linalg.norm(v)
    return v


def test_sample_vault_walks_and_indexes(tmp_path):
    """Index the bundled sample vault into a fresh DB and verify the schema is populated."""
    db = tmp_path / "test.db"
    conn = open_db(db)

    n_notes = 0
    n_links = 0
    for note in walk_vault(SAMPLE_VAULT):
        nid = upsert_note(conn, note)
        replace_links(conn, nid, note.wikilinks)
        n_notes += 1
        n_links += len(note.wikilinks)
    conn.commit()
    resolved = resolve_link_targets(conn)
    conn.commit()

    assert n_notes >= 14, f"expected ≥14 sample notes, got {n_notes}"
    assert n_links >= 30, f"expected ≥30 wikilinks, got {n_links}"
    assert resolved >= 30, f"expected ≥30 resolved targets, got {resolved}"

    # vault age should be at least 200 days (oldest sample note is from 2025-09)
    age = compute_vault_age_days(conn)
    assert age > 200, f"sample vault age too short: {age} days"

    conn.close()


def test_buried_insight_on_sample_vault_with_fake_embeddings(tmp_path):
    """End-to-end: index sample vault, inject fake embeddings, run brief.
    Verifies the algorithm produces a non-empty result on the sample corpus
    without needing a live Ollama."""
    db = tmp_path / "test.db"
    conn = open_db(db)

    for note in walk_vault(SAMPLE_VAULT):
        nid = upsert_note(conn, note)
        replace_links(conn, nid, note.wikilinks)
    conn.commit()
    resolve_link_targets(conn)
    conn.commit()

    # Inject fake embeddings for every note (768-dim, normalized, seeded from id)
    for row in conn.execute("SELECT id, content_hash FROM notes").fetchall():
        nid, ch = row
        rng = np.random.default_rng(nid)
        v = rng.standard_normal(768).astype(np.float32)
        v /= np.linalg.norm(v)
        conn.execute(
            "INSERT INTO embeddings (note_id, model, content_hash, dim, vec) VALUES (?, ?, ?, ?, ?)",
            (nid, "test-fake", ch, 768, v.tobytes()),
        )
    conn.commit()

    results = find_buried_insights(conn, vault_aware=True, top_n=1)
    conn.close()

    assert results, "expected at least one buried insight on sample vault"
    top = results[0]
    assert "HYPOTHESIS" in top.candidate.rel_path, f"expected HYPOTHESIS to be the top buried insight, got: {top.candidate.rel_path}"
    assert top.quote, "expected a non-empty quote"
    assert len(top.validators) >= 3, f"expected ≥3 validators, got {len(top.validators)}"


def test_vault_aware_thresholds_scale_with_age(tmp_path):
    """A young vault gets lenient thresholds; an old vault gets stricter ones (capped)."""
    from datetime import date, timedelta
    db = tmp_path / "test.db"
    conn = open_db(db)
    conn.execute("INSERT INTO notes (rel_path, stem, title, created, updated, word_count, content, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 ("a.md", "a", "a", (date.today() - timedelta(days=730)).isoformat(), date.today().isoformat(), 100, "x", "h"))
    conn.commit()
    t = compute_vault_aware_thresholds(conn)
    assert t["min_age_days"] == 365, "ceiling should bind for 2-year vault"
    assert t["min_dormant_days"] <= 180
    conn.close()

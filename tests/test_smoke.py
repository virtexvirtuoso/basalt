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
from basalt.connection import find_connections, _top_folder
from basalt.contradiction import find_contradictions, _contradiction_evidence
from basalt.audit import (
    record_finding, audit_pending, track_record,
    falsification_rules_for,
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


def test_top_folder_extraction():
    """Connection's folder-boundary check needs reliable top-level extraction."""
    assert _top_folder("02-Projects/SignalBot/HYPOTHESIS.md") == "02-Projects"
    assert _top_folder("README.md") == ""
    assert _top_folder("a/b/c/d.md") == "a"


def test_contradiction_evidence_fires_on_negation_pair():
    """A→positive, B→negation of same predicate → asymmetric-negation signal."""
    a = "The strategy works in production."
    b = "The strategy doesn't work in production after the regime shift."
    score, signals = _contradiction_evidence(a, b)
    assert score > 0.0, "expected at least one signal"
    assert any("negation" in s for s in signals), f"signals: {signals}"


def test_contradiction_evidence_quiet_when_aligned():
    """Two notes asserting the same thing should NOT register a contradiction."""
    a = "The strategy works in production."
    b = "Production has confirmed the strategy works as expected."
    score, signals = _contradiction_evidence(a, b)
    assert score == 0.0, f"expected zero signals, got {score} {signals}"


def test_contradiction_evidence_fires_on_polarity_pair():
    """One side says 'ship', the other says 'kill' → polarity-pair signal."""
    a = "Decision: ship the v2 model on Monday."
    b = "We should kill the v2 model and revert to v1."
    score, signals = _contradiction_evidence(a, b)
    assert score > 0.0, f"expected at least one signal: {signals}"


def _seed_pairwise_vault(conn, n_notes_a: int = 6, n_notes_b: int = 6):
    """Helper: seed a tiny in-memory vault with two clusters of notes that
    embed together. Cluster A in folder X, cluster B in folder Y, plus a
    single 'cross' note in cluster B that uses cluster-A vectors → forms a
    Connection candidate with NO wikilink between folders.

    Returns the cross-pair note ids (a_id_in_X, b_id_in_Y).
    """
    import numpy as np
    from datetime import date

    rng = np.random.default_rng(7)
    base_a = rng.standard_normal(64).astype(np.float32); base_a /= np.linalg.norm(base_a)
    base_b = rng.standard_normal(64).astype(np.float32); base_b /= np.linalg.norm(base_b)

    today = date.today().isoformat()

    def _insert(rel_path, vec, content="seed body content " * 20):
        cur = conn.execute(
            "INSERT INTO notes (rel_path, stem, title, created, updated, word_count, content, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (rel_path, rel_path.split("/")[-1].replace(".md",""), rel_path, today, today, len(content.split()), content, rel_path),
        )
        nid = cur.fetchone()[0]
        v = vec.astype(np.float32); v /= np.linalg.norm(v)
        conn.execute(
            "INSERT INTO embeddings (note_id, model, content_hash, dim, vec) VALUES (?, ?, ?, ?, ?)",
            (nid, "fake", rel_path, 64, v.tobytes()),
        )
        return nid

    # Cluster A in folder X
    for i in range(n_notes_a):
        _insert(f"X/note-a{i}.md", base_a + rng.standard_normal(64) * 0.05)
    # Cluster B in folder Y
    for i in range(n_notes_b):
        _insert(f"Y/note-b{i}.md", base_b + rng.standard_normal(64) * 0.05)

    # Cross-pair: a high-similarity sibling for cluster A's tone, but in folder Y.
    # This is the pair Connection should surface. Padded past MIN_WORD_COUNT (60).
    cross_a_id = _insert("X/cross-anchor.md", base_a, content=(
        "The trade-off is that monitoring overhead grows nonlinearly with strategy count. "
        "We will need to reduce dashboard cardinality before we ship more strategies. "
        "Cardinality compounds across charts and across time windows so the cost is "
        "much higher than the per-strategy cost would suggest. Restraint on what we add "
        "to the dashboard is required if the surface is to remain legible to a human. "
        "Each new chart costs more than the last one because of how attention scales."
    ))
    cross_b_id = _insert("Y/cross-mirror.md", base_a + rng.standard_normal(64) * 0.02, content=(
        "Adding more strategies to the dashboard makes the monitoring story unmanageable. "
        "Cardinality is the bottleneck — we need fewer surfaces, not more. The pattern "
        "repeats whenever we ship: every new strategy multiplies the surface area we have "
        "to watch, and we cannot watch it all carefully. Fewer is better. We should "
        "compress the dashboard before we expand it. The right move is consolidation, not "
        "another chart, no matter how informative the candidate chart appears."
    ))
    conn.commit()
    return cross_a_id, cross_b_id


def test_connection_surfaces_cross_folder_pair_without_wikilink(tmp_path):
    """End-to-end Connection check on a synthetic in-memory vault.
    Two notes in different folders share an embedding cluster and have no wikilink → surfaced."""
    db = tmp_path / "test.db"
    conn = open_db(db)
    a_id, b_id = _seed_pairwise_vault(conn)
    pairs = find_connections(conn, top_n=3, min_sim=0.85)
    conn.close()

    assert pairs, "expected at least one connection"
    found = any(
        {p.note_a_id, p.note_b_id} == {a_id, b_id}
        for p in pairs
    )
    assert found, f"expected the cross-folder pair surfaced; got: {[(p.note_a_path, p.note_b_path) for p in pairs]}"


def test_contradiction_finds_candidates_with_signals(tmp_path):
    """Synthetic vault with two same-topic notes carrying asymmetric negation → contradiction candidate."""
    import numpy as np
    from datetime import date
    db = tmp_path / "test.db"
    conn = open_db(db)
    rng = np.random.default_rng(11)
    base = rng.standard_normal(64).astype(np.float32); base /= np.linalg.norm(base)
    # An orthogonal-ish vector for the filler cluster so fillers don't cross the
    # min_sim floor with the strategy/postmortem pair.
    other = rng.standard_normal(64).astype(np.float32); other /= np.linalg.norm(other)

    def _insert(rel, content, anchor=None):
        anchor = anchor if anchor is not None else base
        cur = conn.execute(
            "INSERT INTO notes (rel_path, stem, title, created, updated, word_count, content, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (rel, rel, rel, date.today().isoformat(), date.today().isoformat(),
             len(content.split()), content, rel),
        )
        nid = cur.fetchone()[0]
        v = (anchor + rng.standard_normal(64) * 0.03).astype(np.float32); v /= np.linalg.norm(v)
        conn.execute(
            "INSERT INTO embeddings (note_id, model, content_hash, dim, vec) VALUES (?, ?, ?, ?, ?)",
            (nid, "fake", rel, 64, v.tobytes()),
        )
        return nid

    # Two same-topic notes, one asserts, the other reverses. Padded past MIN_WORD_COUNT (60).
    a_id = _insert("X/strategy.md",
        "The weekly Brief workflow ships value end-to-end. We should keep it. "
        "Users get a signal each Monday that anchors the rest of the week. "
        "The cadence works because it forces synthesis instead of consumption. "
        "We have evidence from the first cohort that retention improves when the "
        "workflow runs on a schedule. Keep the workflow. Do not change the cadence "
        "or the cohort gains we have observed."
    )
    b_id = _insert("Y/postmortem.md",
        "Actually the weekly Brief workflow doesn't ship value; we should kill it. "
        "On reflection the cohort retention numbers were noise — sample size was "
        "too small to read. The cadence is forced and the synthesis is shallow "
        "because users have no time to read deeply between deliveries. The right "
        "move is to kill the weekly Brief workflow and replace it with on-demand only."
    )
    # Filler notes — different embedding cluster, different topic, must not pair with the above.
    for i in range(4):
        _insert(f"Z/filler{i}.md", "Some unrelated body content " * 25, anchor=other)
    conn.commit()

    pairs = find_contradictions(conn, top_n=2, min_sim=0.7)
    conn.close()
    assert pairs, "expected at least one contradiction candidate"
    matched = any({p.note_a_id, p.note_b_id} == {a_id, b_id} for p in pairs)
    assert matched, f"expected the synthetic pair surfaced; got: {[(p.note_a_path, p.note_b_path) for p in pairs]}"


def test_falsification_rules_per_verb_have_text():
    """Each verb's falsification rules must include human-readable `text` for CLI render."""
    from types import SimpleNamespace
    fake_buried = SimpleNamespace(candidate=SimpleNamespace(rel_path="x.md"))
    rules = falsification_rules_for("buried-insight", fake_buried)
    assert rules and all("text" in r for r in rules)
    assert any("60 days" in r["text"] for r in rules)

    fake_conn = SimpleNamespace(note_a_path="A.md", note_b_path="B.md")
    rules = falsification_rules_for("connection", fake_conn)
    assert rules and all("text" in r for r in rules)

    fake_contra = SimpleNamespace(note_a_path="A.md", note_b_path="B.md")
    rules = falsification_rules_for("contradiction", fake_contra)
    assert rules and all("text" in r for r in rules)


def test_record_finding_is_idempotent_per_finding_key(tmp_path):
    """Running the same brief twice for the same finding doesn't double-log."""
    from types import SimpleNamespace
    db = tmp_path / "test.db"
    conn = open_db(db)
    fake = SimpleNamespace(note_a_path="X/a.md", note_b_path="Y/b.md",
                           note_a_quote="q1", note_a_quote_provenance="p",
                           note_b_quote="q2", note_b_quote_provenance="p",
                           similarity=0.9)
    id1 = record_finding(conn, "connection", fake)
    id2 = record_finding(conn, "connection", fake)
    assert id1 is not None
    assert id2 is None, "second insert with same key should be a no-op"
    n = conn.execute("SELECT COUNT(*) FROM briefs").fetchone()[0]
    assert n == 1
    conn.close()


def test_audit_falsifies_unlinked_connection_after_grace(tmp_path):
    """Connection brief becomes 'falsified' if grace_days pass without a wikilink between A and B."""
    from types import SimpleNamespace
    from datetime import date, timedelta
    db = tmp_path / "test.db"
    conn = open_db(db)

    # Seed two notes that exist but aren't linked.
    today = date.today()
    for rel in ("X/a.md", "Y/b.md"):
        conn.execute(
            "INSERT INTO notes (rel_path, stem, title, created, updated, word_count, content, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rel, rel, rel, today.isoformat(), today.isoformat(), 100, "body", rel),
        )
    conn.commit()

    fake = SimpleNamespace(note_a_path="X/a.md", note_b_path="Y/b.md",
                           note_a_quote="q", note_a_quote_provenance="p",
                           note_b_quote="q", note_b_quote_provenance="p",
                           similarity=0.9)
    record_finding(conn, "connection", fake)
    # Backdate so the grace window has lapsed
    past = (today - timedelta(days=120)).isoformat()
    conn.execute("UPDATE briefs SET created_at = ?", (past,))
    conn.commit()

    results = audit_pending(conn)
    conn.close()
    assert results, "expected at least one audit verdict"
    # The "still_unlinked" rule should fire and falsify the brief
    assert any(r.new_status == "falsified" and r.rule_kind == "still_unlinked" for r in results)


def test_audit_confirms_linked_connection(tmp_path):
    """Connection brief becomes 'confirmed' when user adds a wikilink between A and B."""
    from types import SimpleNamespace
    from datetime import date
    db = tmp_path / "test.db"
    conn = open_db(db)

    today = date.today()
    for rel in ("X/a.md", "Y/b.md"):
        conn.execute(
            "INSERT INTO notes (rel_path, stem, title, created, updated, word_count, content, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rel, rel, rel, today.isoformat(), today.isoformat(), 100, "body", rel),
        )
    conn.commit()
    # Add the wikilink edge from a → b
    a_id = conn.execute("SELECT id FROM notes WHERE rel_path = 'X/a.md'").fetchone()[0]
    b_id = conn.execute("SELECT id FROM notes WHERE rel_path = 'Y/b.md'").fetchone()[0]
    conn.execute(
        "INSERT INTO links (from_note_id, target, target_note_id) VALUES (?, ?, ?)",
        (a_id, "b", b_id),
    )
    conn.commit()

    fake = SimpleNamespace(note_a_path="X/a.md", note_b_path="Y/b.md",
                           note_a_quote="q", note_a_quote_provenance="p",
                           note_b_quote="q", note_b_quote_provenance="p",
                           similarity=0.9)
    record_finding(conn, "connection", fake)
    results = audit_pending(conn)
    conn.close()
    assert any(r.new_status == "confirmed" and r.rule_kind == "still_unlinked" for r in results)


def test_track_record_counts_by_status(tmp_path):
    """track_record() returns confirmed/pending/falsified counts within window."""
    from datetime import date
    db = tmp_path / "test.db"
    conn = open_db(db)
    today = date.today().isoformat()
    for status in ("pending", "pending", "confirmed", "falsified", "falsified"):
        conn.execute(
            "INSERT INTO briefs (verb, finding_key, finding_json, falsification, created_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("buried-insight", f"k-{status}-?", "{}", "[]", today, status),
        )
    # SQLite needs unique key per insert — patch:
    conn.execute("DELETE FROM briefs")
    for i, status in enumerate(("pending", "pending", "confirmed", "falsified", "falsified")):
        conn.execute(
            "INSERT INTO briefs (verb, finding_key, finding_json, falsification, created_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("buried-insight", f"k-{i}", "{}", "[]", today, status),
        )
    conn.commit()
    tr = track_record(conn, days=90)
    conn.close()
    assert tr.confirmed == 1
    assert tr.pending == 2
    assert tr.falsified == 2
    assert tr.total == 5
    assert 19.5 < tr.confirmed_pct < 20.5
    assert 39.5 < tr.falsified_pct < 40.5


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

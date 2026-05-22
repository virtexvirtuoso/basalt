"""Contradiction verb — finds notes that can't both be true.

Site language: *"The two notes you wrote that can't both be true."*

This is the hardest of the four unlocks to ship without an LLM. v0 is a
deliberately conservative heuristic that surfaces **candidates**, not
verdicts: pairs of notes that are topically about the same thing AND whose
load-bearing sentences carry **opposite-shape** lexical signals (negation,
reversal, antonym pairs, change-of-mind markers).

The honest disclosure: a v0 heuristic over surface text will produce false
positives. Treat the output as "look at these two and decide," not
"contradiction proven." Phase 1 will add an LLM-based pairwise compatibility
classifier on top of this candidate set — see Strategy-Synthesis-2026-05-07.

Output: top N candidate pairs ranked by `topical_sim × contradiction_score`.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

import numpy as np

from basalt.embed import _blob_to_vec
from basalt.filters import sql_exclude_clause
from basalt.verb import VerbBase, VerbResult
from basalt.buried import (
    HUB_DENSITY_HARD,
    HUB_DENSITY_SOFT,
    _extract_claim_quote,
    _strip_md,
)


# Topical similarity floor: lower than Connection because we want notes that
# are talking about the same THING but might disagree on it. 0.72 is empirical
# — high enough to filter unrelated pairs, low enough to catch genuine reversals.
DEFAULT_MIN_SIM      = 0.72
MIN_WORD_COUNT       = 60
MAX_PAIRS            = 200
DEFAULT_TOP_N        = 3


# ── Lexical contradiction signals ─────────────────────────────────────
#
# These are the *shape* markers — surface lexical evidence that the two
# load-bearing sentences are saying opposite things. None of them is by
# itself proof; they accumulate.

# Direct negation: one side asserts, the other negates. We pick up "isn't",
# "doesn't", "won't" etc. — the negation-assertion shape from buried.py.
_NEGATION = re.compile(
    r"\b(isn't|aren't|wasn't|weren't|doesn't|don't|won't|can't|"
    r"shouldn't|wouldn't|hasn't|haven't|never|no\s+longer|"
    r"not\s+(just|merely|only|simply|enough|the|a))\b",
    re.IGNORECASE,
)

# Change-of-mind / reversal markers — strong contradiction signal when one
# side has them and the other doesn't.
_REVERSAL = re.compile(
    r"\b(actually|in\s+fact|turns?\s+out|on\s+reflection|"
    r"i\s+was\s+wrong|i\s+changed\s+my\s+mind|"
    r"the\s+opposite|opposite\s+is\s+true|"
    r"contrary|nevertheless|however|but\s+actually|"
    r"updated|revisited|second\s+thoughts|reconsider)\b",
    re.IGNORECASE,
)

# Polarity word pairs — antonyms that, if one note has X and the other has Y
# in close proximity to a shared subject, suggest the claims oppose.
# Conservative list; over-broad antonym tables produce false positives quickly.
_POLARITY_PAIRS: list[tuple[str, str]] = [
    ("works",      "doesn't work"),
    ("works",      "broken"),
    ("worth it",   "not worth"),
    ("buy",        "sell"),
    ("ship",       "kill"),
    ("ship",       "shelve"),
    ("keep",       "drop"),
    ("validated",  "invalidated"),
    ("validated",  "failed"),
    ("scales",     "doesn't scale"),
    ("profitable", "unprofitable"),
    ("profitable", "loses money"),
    ("rising",     "falling"),
    ("up",         "down"),
    ("bullish",    "bearish"),
    ("succeed",    "fail"),
    ("right",      "wrong"),
    ("true",       "false"),
    ("possible",   "impossible"),
    ("simple",     "complex"),
    ("safe",       "risky"),
]


@dataclass
class ContradictionPair:
    note_a_id: int
    note_a_path: str
    note_a_title: str
    note_a_quote: str
    note_a_quote_provenance: str
    note_b_id: int
    note_b_path: str
    note_b_title: str
    note_b_quote: str
    note_b_quote_provenance: str
    similarity: float
    contradiction_score: float
    score: float                 # final ranking score
    signals: list[str]           # which lexical evidences fired


def _hub_density(out_links: int, word_count: int) -> float:
    if word_count <= 0:
        return 0.0
    return out_links / max(word_count / 100.0, 1.0)


def _hub_penalty(density: float) -> float:
    excess = max(0.0, density - HUB_DENSITY_SOFT)
    return 1.0 / (1.0 + (2.0 * excess) ** 2)


def _contradiction_evidence(quote_a: str, quote_b: str) -> tuple[float, list[str]]:
    """Score 0.0–3.0 plus a list of human-readable signals fired.

    The score is intentionally bounded — a single signal earns ~1.0, the
    presence of asymmetric reversal/negation around shared content earns more.
    """
    a = _strip_md(quote_a or "").lower()
    b = _strip_md(quote_b or "").lower()
    if not a or not b:
        return 0.0, []

    score = 0.0
    signals: list[str] = []

    # Asymmetric negation: one quote negates, the other asserts. (Both negating
    # or both asserting is not contradictory.)
    a_neg = bool(_NEGATION.search(a))
    b_neg = bool(_NEGATION.search(b))
    if a_neg ^ b_neg:
        score += 1.0
        signals.append("asymmetric negation")

    # Asymmetric reversal: one side carries an explicit "actually / I was wrong /
    # turns out" marker — the other doesn't. Strong signal.
    a_rev = bool(_REVERSAL.search(a))
    b_rev = bool(_REVERSAL.search(b))
    if a_rev ^ b_rev:
        score += 1.2
        signals.append("asymmetric reversal marker")

    # Polarity word pairs: one side's positive form, the other's negative form.
    # Only fire if BOTH terms in a pair appear in the OPPOSITE quotes.
    fired_pairs = []
    for pos, neg in _POLARITY_PAIRS:
        a_pos = pos in a
        a_neg_phrase = neg in a
        b_pos = pos in b
        b_neg_phrase = neg in b
        if (a_pos and b_neg_phrase) or (a_neg_phrase and b_pos):
            fired_pairs.append(f"{pos!r} ↔ {neg!r}")
    if fired_pairs:
        score += min(0.8 * len(fired_pairs), 1.6)
        signals.append("polarity-pair: " + "; ".join(fired_pairs))

    return score, signals


# ── Subject-overlap gate (Phase C, 2026-05-22) ────────────────
# Per dogfood findings: cosine + negation markers alone yields ~100% FP rate
# because vault-index ↔ daily note pairs clear the threshold without sharing
# any subject. Gate: a contradiction candidate must share at least one
# subject (tag, wikilink target, or folder prefix) before evidence-scoring.

# Tags that describe HOW a note was made or its lifecycle status, not
# WHAT it's about. Excluded from subject sets so two unrelated auto-generated
# notes (e.g. MEMORY.md + SOUL.md) don't pair on a meaningless tag.
_META_TAGS = frozenset({
    "auto-generated",
    "auto",
    "draft",
    "wip",
    "active",
    "archived",
    "dead",
    "validated",
    "daily",
    "weekly",
    "monthly",
    "review",
})

# Notes with more wikilinks than this are MOCs (Maps of Content), not
# belief-shaped. Their outbound links describe what they index, not what
# they're about — so we drop wikilink subjects above this cap. The note
# is still pairable on tags/folder/self-stem, but won't auto-overlap with
# any other note that mentions any of its 200+ link targets.
_MOC_WIKILINK_CAP = 20


def _subject_set(
    rel_path: str,
    tags: str,
    wikilinks: list[str],
) -> set[str]:
    """Return the set of subjects this note is "about."

    Wikilinks contribute nothing if the note has >_MOC_WIKILINK_CAP of them
    (MOC detection — the links describe what the note indexes, not what
    it's about). Tags in _META_TAGS are dropped as semantically meaningless.

    The note's own stem is included as a link:-subject so that other notes
    linking TO this one trigger overlap. Root-level files get no folder
    subject (they're cross-cutting; shouldn't auto-pair).
    """
    subjects: set[str] = set()

    # Tags (skip meta-tags about lifecycle / generation method)
    if tags:
        for t in tags.split(","):
            t = t.strip().lower()
            if t and t not in _META_TAGS:
                subjects.add(f"tag:{t}")

    # Wikilinks: skip entirely for MOC notes (their links are an index, not a subject).
    if len(wikilinks) <= _MOC_WIKILINK_CAP:
        for w in wikilinks:
            if not w:
                continue
            subjects.add(f"link:{w.strip().lower()}")

    # Self-stem (so inbound links from other notes match this one)
    from pathlib import Path as _P
    stem = _P(rel_path).stem
    if stem:
        subjects.add(f"link:{stem.lower()}")

    # Folder prefix — first 1-2 path segments
    parts = _P(rel_path).parts
    if len(parts) >= 3:
        subjects.add(f"folder:{parts[0].lower()}/{parts[1].lower()}")
    elif len(parts) == 2:
        subjects.add(f"folder:{parts[0].lower()}")

    return subjects


def _subjects_overlap(a: set[str], b: set[str]) -> bool:
    """True iff the two subject sets share at least one element."""
    return bool(a & b)



class ContradictionVerb:
    """Contradiction verb implementation.

    Finds pairs of notes that are topically similar but carry opposite-shape lexical signals.
    Note: Does not inherit from VerbBase because contradiction operates on note pairs,
    not individual candidates.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @property
    def name(self) -> str:
        return "contradiction"

    def run(
        self,
        min_sim: float | None = None,
        top_n: int = DEFAULT_TOP_N,
    ) -> list[ContradictionPair]:
        """Execute the Contradiction verb.

        Args:
            min_sim: Override default topical similarity threshold
            top_n: Number of contradiction candidates to return
        """
        min_sim = min_sim if min_sim is not None else DEFAULT_MIN_SIM

        rows = self.conn.execute(
            f"""
            SELECT n.id, n.rel_path, n.title, n.word_count, n.content, n.status, n.type, n.confidence, n.tags, e.vec
            FROM notes n
            JOIN embeddings e ON e.note_id = n.id
            WHERE n.word_count >= ? AND {sql_exclude_clause()}
            """,
            (MIN_WORD_COUNT,),
        ).fetchall()
        if len(rows) < 2:
            return []

        out_link_counts = dict(self.conn.execute(
            "SELECT from_note_id, COUNT(DISTINCT target) FROM links GROUP BY from_note_id"
        ).fetchall())

        note_ids = [r["id"] for r in rows]
        paths = {r["id"]: r["rel_path"] for r in rows}
        titles = {r["id"]: r["title"] for r in rows}
        contents = {r["id"]: r["content"] for r in rows}
        wcs = {r["id"]: r["word_count"] for r in rows}
        densities = {nid: _hub_density(out_link_counts.get(nid, 0), wcs[nid]) for nid in note_ids}
        keep_ids = [nid for nid in note_ids if densities[nid] <= HUB_DENSITY_HARD]
        if len(keep_ids) < 2:
            return []

        matrix = np.stack([_blob_to_vec(rows[note_ids.index(nid)]["vec"]) for nid in keep_ids])
        sims = matrix @ matrix.T
        np.fill_diagonal(sims, -1.0)
        iu = np.triu_indices(sims.shape[0], k=1)
        pair_sims = sims[iu]

        qualifying: list[tuple[int, int, float]] = []
        for i, j, s in zip(iu[0], iu[1], pair_sims):
            if s < min_sim:
                continue
            qualifying.append((keep_ids[int(i)], keep_ids[int(j)], float(s)))
            if len(qualifying) >= MAX_PAIRS:
                break
        if not qualifying:
            return []

        # ── Subject-overlap gate (Phase C, 2026-05-22) ──
        # Per dogfood findings: cosine + negation alone yielded ~100% FP rate.
        # Require pairs to share at least one subject (tag / wikilink target /
        # folder prefix) before running the expensive per-pair evidence check.
        tags_by_id = {r["id"]: (r["tags"] or "") for r in rows}
        link_rows = self.conn.execute(
            "SELECT from_note_id, target FROM links WHERE from_note_id IN ("
            + ",".join("?" for _ in keep_ids) + ")",
            tuple(keep_ids),
        ).fetchall()
        wikilinks_by_id: dict[int, list[str]] = {nid: [] for nid in keep_ids}
        for lr in link_rows:
            wikilinks_by_id[lr["from_note_id"]].append(lr["target"])
        subjects_by_id = {
            nid: _subject_set(paths[nid], tags_by_id[nid], wikilinks_by_id[nid])
            for nid in keep_ids
        }
        qualifying = [
            (a, b, s) for (a, b, s) in qualifying
            if _subjects_overlap(subjects_by_id[a], subjects_by_id[b])
        ]
        if not qualifying:
            return []

        # Per-pair: extract quotes, run the lexical evidence check, score.
        quote_cache: dict[int, tuple[str, str]] = {}
        def _quote(nid: int) -> tuple[str, str]:
            if nid not in quote_cache:
                quote_cache[nid] = _extract_claim_quote(contents[nid])
            return quote_cache[nid]

        scored: list[ContradictionPair] = []
        for a_id, b_id, s in qualifying:
            a_quote, a_prov = _quote(a_id)
            b_quote, b_prov = _quote(b_id)
            if not a_quote or not b_quote:
                continue
            cscore, signals = _contradiction_evidence(a_quote, b_quote)
            if cscore <= 0.0:
                continue
            pa = _hub_penalty(densities[a_id])
            pb = _hub_penalty(densities[b_id])
            rank = s * cscore * (pa * pb) ** 0.5
            scored.append(ContradictionPair(
                note_a_id=a_id,
                note_a_path=paths[a_id],
                note_a_title=titles[a_id],
                note_a_quote=a_quote,
                note_a_quote_provenance=a_prov,
                note_b_id=b_id,
                note_b_path=paths[b_id],
                note_b_title=titles[b_id],
                note_b_quote=b_quote,
                note_b_quote_provenance=b_prov,
                similarity=s,
                contradiction_score=cscore,
                score=rank,
                signals=signals,
            ))

        scored.sort(key=lambda p: -p.score)

        # Diversity: don't return pairs all touching the same note
        seen: set[int] = set()
        out: list[ContradictionPair] = []
        for p in scored:
            if p.note_a_id in seen or p.note_b_id in seen:
                continue
            out.append(p)
            seen.add(p.note_a_id)
            seen.add(p.note_b_id)
            if len(out) >= top_n:
                break
        return out


# ── Backwards-compatible wrapper ─────────────────────────────────

def find_contradictions(
    conn: sqlite3.Connection,
    min_sim: float = DEFAULT_MIN_SIM,
    top_n: int = DEFAULT_TOP_N,
) -> list[ContradictionPair]:
    """Run the Contradiction verb. Returns top N contradiction candidates."""
    verb = ContradictionVerb(conn)
    return verb.run(min_sim=min_sim, top_n=top_n)

"""Contradiction algorithm — v0 heuristic.

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


def find_contradictions(
    conn: sqlite3.Connection,
    min_sim: float = DEFAULT_MIN_SIM,
    top_n: int = DEFAULT_TOP_N,
) -> list[ContradictionPair]:
    """Return top N candidate contradiction pairs.

    A pair (A, B) qualifies when:
      - cosine(emb(A), emb(B)) ≥ min_sim — they're about the same topic
      - At least one lexical contradiction signal fires across their quotes
      - Both notes meet word-count and hub-density floors

    Returned pairs are *candidates*. Mr. V (or the next-stage classifier) is
    the source of truth on whether the contradiction is real.
    """
    rows = conn.execute(
        """
        SELECT n.id, n.rel_path, n.title, n.word_count, n.content,
               e.vec
        FROM notes n
        JOIN embeddings e ON e.note_id = n.id
        WHERE n.word_count >= ?
        """,
        (MIN_WORD_COUNT,),
    ).fetchall()
    if len(rows) < 2:
        return []

    out_link_counts = dict(conn.execute(
        "SELECT from_note_id, COUNT(DISTINCT target) FROM links GROUP BY from_note_id"
    ).fetchall())

    note_ids = [r["id"] for r in rows]
    paths    = {r["id"]: r["rel_path"]    for r in rows}
    titles   = {r["id"]: r["title"]       for r in rows}
    contents = {r["id"]: r["content"]     for r in rows}
    wcs      = {r["id"]: r["word_count"]  for r in rows}
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
        # Final ranking: similarity × contradiction signal × hub penalty mean
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

    # Diversity: same as Connection — don't return three pairs all touching
    # the same note. Different axes of disagreement.
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

"""Sentence-scorer penalty for stats strings.

Per [[V0-Verb-Quality-Fixes-Spec-2026-05-22]] Phase D: the implicit-thesis
centroid quote selection currently picks stats strings like "79 systemd
units, 53 listening ports, 14 nginx hostnames" as the proxy thesis. That's
a fact, not a through-line. Fix: penalize sentences with high numeric/
punctuation density so claim-shaped sentences win the scoring contest.

Added 2026-05-22.
"""

from __future__ import annotations

import pytest

from basalt.buried import _score_load_bearing


def test_stats_string_penalized_below_claim_string():
    """Two sentences from the dogfood VPS cluster, in order they appear in
    the centroid note. The claim-shaped one must outscore the stats one."""
    stats = "79 systemd units, 53 listening ports, 14 nginx hostnames."
    claim = "VPS state is the load-bearing dependency for every other system."

    # Both at position 0 in a 2-sentence note, prose-style (prefer_last=False)
    stats_score = _score_load_bearing(stats, position=0, total=2, prefer_last=False)
    claim_score = _score_load_bearing(claim, position=0, total=2, prefer_last=False)

    assert claim_score > stats_score, (
        f"Expected claim > stats; got claim={claim_score}, stats={stats_score}"
    )


def test_pure_numeric_listing_penalized():
    """A sentence that's almost entirely numbers + delimiters gets a
    measurable penalty (not just lower-than-claim — actually negative-shaped)."""
    pure_stats = "1, 2, 3, 4, 5, 6, 7, 8, 9, 10."
    score = _score_load_bearing(pure_stats, position=0, total=2, prefer_last=False)
    # Without penalty this would still be ~0.2-0.6 from positional + length
    # With penalty applied, should drop substantially. We assert < 0.0 as a
    # signal that the penalty actually fires.
    assert score < 0.0, f"Pure-numeric got {score}; expected penalty"


def test_one_stat_in_claim_not_over_penalized():
    """A real claim that happens to cite one number ('we lost 80% market share')
    should NOT trigger the penalty — only pure-stats strings."""
    mixed = "We lost 80% of our market share to competitor X over six months."
    score = _score_load_bearing(mixed, position=0, total=2, prefer_last=False)
    # No penalty signal — at least 0.0 (positional alone gives 0.6 for pos 0
    # in prose-style sentence count of 2, but length penalty for >150 chars
    # might bite — keep this loose)
    assert score >= 0.0, f"Mixed-claim with one stat got {score}; over-penalized"


def test_claim_without_stats_unaffected():
    """A claim sentence with no numbers at all gets exactly the score the
    previous heuristic produced — regression guard that the penalty doesn't
    affect non-stats sentences."""
    claim = "The sustainable edge isn't speed alone — it's speed plus intelligence."
    score = _score_load_bearing(claim, position=0, total=2, prefer_last=False)
    # Should be highly positive: positional + em-dash + negation + length
    assert score > 1.5, f"Plain claim got {score}; penalty over-reached"

"""Brief section-ordering tests.

Per [[V0-Verb-Quality-Fixes-Spec-2026-05-22]] Phase E: when Drift surfaces
a project with |max delta| > 5pp, promote that section to the top of the
Brief because it's the magic-moment finding for first-time users.

When Drift is flat (|delta| <= 5pp) or empty, preserve the original section
order (thesis → buried → drift → contradiction → connection).

Added 2026-05-22.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from unittest.mock import patch

import pytest

from basalt.brief import BriefSection, Brief, compile_brief


# We test reorder_for_drift_magnitude (helper) directly to avoid mocking
# all 5 verbs. The helper is the load-bearing logic; the integration into
# compile_brief is one line.


def _make_section(verb: str, findings: list = None, vault_age: int = 100) -> BriefSection:
    return BriefSection(
        verb=verb,
        title=verb.title(),
        site_language="",
        findings=findings or [],
        thresholds={},
        vault_age_days=vault_age,
    )


@dataclass
class _FakeDriftFinding:
    """Minimal stand-in matching the score attribute used for ordering."""
    score: float


def test_drift_promoted_when_score_above_threshold():
    """Drift section moves to the front when its top finding has |delta| > 5pp."""
    from basalt.brief import reorder_for_drift_magnitude

    sections = [
        _make_section("implicit-thesis"),
        _make_section("buried-insight"),
        _make_section("drift", findings=[_FakeDriftFinding(score=16.0)]),
        _make_section("contradiction"),
        _make_section("connection"),
    ]
    reordered = reorder_for_drift_magnitude(sections, threshold_pp=5.0)
    assert reordered[0].verb == "drift"
    # Original order preserved for the rest, sans drift
    assert [s.verb for s in reordered[1:]] == [
        "implicit-thesis",
        "buried-insight",
        "contradiction",
        "connection",
    ]


def test_drift_stays_in_place_when_score_at_or_below_threshold():
    """A flat drift (score <= threshold) keeps the original order."""
    from basalt.brief import reorder_for_drift_magnitude

    sections = [
        _make_section("implicit-thesis"),
        _make_section("buried-insight"),
        _make_section("drift", findings=[_FakeDriftFinding(score=3.0)]),
        _make_section("contradiction"),
        _make_section("connection"),
    ]
    reordered = reorder_for_drift_magnitude(sections, threshold_pp=5.0)
    assert reordered[0].verb == "implicit-thesis"
    assert reordered[2].verb == "drift"


def test_drift_stays_in_place_at_exact_threshold():
    """Boundary test: score == threshold does NOT promote (strict inequality)."""
    from basalt.brief import reorder_for_drift_magnitude

    sections = [
        _make_section("implicit-thesis"),
        _make_section("drift", findings=[_FakeDriftFinding(score=5.0)]),
    ]
    reordered = reorder_for_drift_magnitude(sections, threshold_pp=5.0)
    assert reordered[0].verb == "implicit-thesis"


def test_no_drift_section_returns_unchanged():
    from basalt.brief import reorder_for_drift_magnitude

    sections = [
        _make_section("implicit-thesis"),
        _make_section("buried-insight"),
    ]
    reordered = reorder_for_drift_magnitude(sections, threshold_pp=5.0)
    assert [s.verb for s in reordered] == ["implicit-thesis", "buried-insight"]


def test_drift_with_empty_findings_stays_in_place():
    from basalt.brief import reorder_for_drift_magnitude

    sections = [
        _make_section("implicit-thesis"),
        _make_section("drift", findings=[]),
        _make_section("connection"),
    ]
    reordered = reorder_for_drift_magnitude(sections, threshold_pp=5.0)
    assert [s.verb for s in reordered] == [
        "implicit-thesis",
        "drift",
        "connection",
    ]


def test_threshold_is_configurable():
    """Caller can pass a different threshold to reorder_for_drift_magnitude."""
    from basalt.brief import reorder_for_drift_magnitude

    sections = [
        _make_section("implicit-thesis"),
        _make_section("drift", findings=[_FakeDriftFinding(score=8.0)]),
    ]
    # With threshold=10, score=8 doesn't trigger
    assert reorder_for_drift_magnitude(sections, threshold_pp=10.0)[0].verb == "implicit-thesis"
    # With threshold=5, it does
    assert reorder_for_drift_magnitude(sections, threshold_pp=5.0)[0].verb == "drift"

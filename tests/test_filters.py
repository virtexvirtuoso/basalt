"""Frontmatter-aware filter primitives.

Per [[V0-Verb-Quality-Fixes-Spec-2026-05-22]] Phase A: a shared filter
module that all verbs invoke before per-verb logic.

Filters compare frontmatter values case-insensitively against the
configured value lists, so a note with `status: Archived` (capitalized)
is still excluded.

Added 2026-05-22.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pytest

from basalt.filters import (
    filter_archived,
    filter_drafts,
    filter_references,
    weight_by_confidence,
)
from basalt.vault import Note


def _note(
    rel_path: str = "n.md",
    *,
    status: str | None = None,
    type: str | None = None,
    confidence: str | None = None,
) -> Note:
    return Note(
        path=Path("/x") / rel_path,
        rel_path=rel_path,
        stem=Path(rel_path).stem,
        title=Path(rel_path).stem,
        created=None,
        updated=None,
        status=status,
        type=type,
        confidence=confidence,
    )


# ── filter_archived ────────────────────────────────────────────


def test_filter_archived_excludes_archived_dead_superseded():
    notes = [
        _note("a.md", status="active"),
        _note("b.md", status="archived"),
        _note("c.md", status="dead"),
        _note("d.md", status="superseded"),
        _note("e.md", status=None),
    ]
    kept = list(filter_archived(notes))
    paths = [n.rel_path for n in kept]
    assert "a.md" in paths
    assert "e.md" in paths
    assert "b.md" not in paths
    assert "c.md" not in paths
    assert "d.md" not in paths


def test_filter_archived_case_insensitive():
    notes = [
        _note("a.md", status="Archived"),
        _note("b.md", status="ARCHIVED"),
        _note("c.md", status="active"),
    ]
    kept = list(filter_archived(notes))
    assert [n.rel_path for n in kept] == ["c.md"]


def test_filter_archived_no_status_kept():
    """Notes without `status:` frontmatter are kept (the field is opt-in)."""
    notes = [_note("a.md", status=None), _note("b.md", status="active")]
    kept = list(filter_archived(notes))
    assert len(kept) == 2


# ── filter_drafts ──────────────────────────────────────────────


def test_filter_drafts_excludes_drafts_and_wip_by_default():
    notes = [
        _note("a.md", status="active"),
        _note("b.md", status="draft"),
        _note("c.md", status="wip"),
        _note("d.md", status=None),
    ]
    kept = list(filter_drafts(notes))
    paths = [n.rel_path for n in kept]
    assert "a.md" in paths
    assert "d.md" in paths
    assert "b.md" not in paths
    assert "c.md" not in paths


def test_filter_drafts_allow_drafts_overrides():
    """Caller can pass allow_drafts=True to keep drafts in the pool."""
    notes = [
        _note("a.md", status="draft"),
        _note("b.md", status="wip"),
    ]
    kept = list(filter_drafts(notes, allow_drafts=True))
    assert len(kept) == 2


def test_filter_drafts_case_insensitive():
    notes = [
        _note("a.md", status="Draft"),
        _note("b.md", status="WIP"),
        _note("c.md", status="active"),
    ]
    kept = list(filter_drafts(notes))
    assert [n.rel_path for n in kept] == ["c.md"]


# ── filter_references ──────────────────────────────────────────


def test_filter_references_excludes_type_reference_and_friends():
    notes = [
        _note("a.md", type="reference"),
        _note("b.md", type="wiki"),
        _note("c.md", type="doc"),
        _note("d.md", type="api"),
        _note("e.md", type="template"),
        _note("f.md", type="note"),
        _note("g.md", type=None),
    ]
    kept = list(filter_references(notes))
    paths = [n.rel_path for n in kept]
    assert "f.md" in paths
    assert "g.md" in paths
    assert "a.md" not in paths
    assert "b.md" not in paths
    assert "c.md" not in paths
    assert "d.md" not in paths
    assert "e.md" not in paths


def test_filter_references_case_insensitive():
    notes = [
        _note("a.md", type="Reference"),
        _note("b.md", type="WIKI"),
        _note("c.md", type="note"),
    ]
    kept = list(filter_references(notes))
    assert [n.rel_path for n in kept] == ["c.md"]


# ── weight_by_confidence ────────────────────────────────────────


def test_weight_by_confidence_returns_expected_multipliers():
    assert weight_by_confidence(_note(confidence="HIGH")) == pytest.approx(1.0)
    assert weight_by_confidence(_note(confidence="MEDIUM")) == pytest.approx(0.7)
    assert weight_by_confidence(_note(confidence="LOW")) == pytest.approx(0.4)
    # Missing confidence: middle of the road (don't penalize unset)
    assert weight_by_confidence(_note(confidence=None)) == pytest.approx(0.8)


def test_weight_by_confidence_case_insensitive():
    assert weight_by_confidence(_note(confidence="high")) == pytest.approx(1.0)
    assert weight_by_confidence(_note(confidence="Medium")) == pytest.approx(0.7)


def test_weight_by_confidence_unknown_value_defaults_to_unset():
    """An unrecognized confidence value (e.g. typo, custom convention) is
    treated as if the field were absent, not as zero (don't silently drop)."""
    assert weight_by_confidence(_note(confidence="probably")) == pytest.approx(0.8)


# ── composition ────────────────────────────────────────────────


def test_filters_chain_naturally():
    """Filters are designed to chain in a verb's run() entrypoint."""
    notes: list[Note] = [
        _note("active-claim.md", status="active", type="note"),
        _note("archived-claim.md", status="archived", type="note"),
        _note("active-ref.md", status="active", type="reference"),
        _note("wip-thought.md", status="wip", type="note"),
        _note("validated.md", status="validated", type="note"),
    ]
    kept = list(filter_references(filter_drafts(filter_archived(notes))))
    paths = [n.rel_path for n in kept]
    assert "active-claim.md" in paths
    assert "validated.md" in paths
    assert "archived-claim.md" not in paths
    assert "active-ref.md" not in paths
    assert "wip-thought.md" not in paths

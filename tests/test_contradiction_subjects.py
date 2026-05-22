"""Subject-overlap gate for Contradiction verb.

Per [[V0-Verb-Quality-Fixes-Spec-2026-05-22]] Phase C: a pair of notes
must share at least one subject (tag, wikilink target, or folder prefix)
before the contradiction-evidence check runs. The dogfood findings showed
that surface markers alone (negation + cosine 0.76) yield ~100% FP rate.

Added 2026-05-22.
"""

from __future__ import annotations

import pytest

from basalt.contradiction import _subject_set, _subjects_overlap


# ── _subject_set ──────────────────────────────────────────────


def test_subject_set_from_tags():
    subjects = _subject_set(
        rel_path="notes/foo.md",
        tags="polyclawd,strategy",
        wikilinks=[],
    )
    assert "tag:polyclawd" in subjects
    assert "tag:strategy" in subjects


def test_subject_set_from_wikilinks():
    subjects = _subject_set(
        rel_path="notes/foo.md",
        tags="",
        wikilinks=["Polyclawd", "Whale Hunter"],
    )
    assert "link:polyclawd" in subjects
    assert "link:whale hunter" in subjects


def test_subject_set_from_folder_prefix():
    """Notes in the same project folder share a structural subject —
    works even when neither note has tags or wikilinks."""
    subjects = _subject_set(
        rel_path="02-Projects/Polyclawd/Strategy/edge.md",
        tags="",
        wikilinks=[],
    )
    assert "folder:02-projects/polyclawd" in subjects


def test_subject_set_root_files_get_no_folder():
    """Notes at the vault root (no project folder) get no folder subject —
    they're cross-cutting and shouldn't auto-pair with each other."""
    subjects = _subject_set(rel_path="MEMORY.md", tags="", wikilinks=[])
    assert not any(s.startswith("folder:") for s in subjects)


def test_subject_set_short_path_only_one_segment():
    """A path like '09-AI-Context/foo.md' has one folder; treat that as the
    folder subject (not two levels deep)."""
    subjects = _subject_set(
        rel_path="09-AI-Context/vps-services.md",
        tags="",
        wikilinks=[],
    )
    assert "folder:09-ai-context" in subjects


def test_subject_set_combines_all_signals():
    subjects = _subject_set(
        rel_path="02-Projects/Polyclawd/r.md",
        tags="strategy,polyclawd",
        wikilinks=["HF Module"],
    )
    assert "tag:strategy" in subjects
    assert "tag:polyclawd" in subjects
    assert "link:hf module" in subjects
    assert "folder:02-projects/polyclawd" in subjects


# ── _subjects_overlap ─────────────────────────────────────────


def test_overlap_same_tag():
    a = _subject_set(rel_path="x/a.md", tags="polyclawd", wikilinks=[])
    b = _subject_set(rel_path="y/b.md", tags="polyclawd", wikilinks=[])
    assert _subjects_overlap(a, b)


def test_overlap_same_wikilink_target():
    a = _subject_set(rel_path="x/a.md", tags="", wikilinks=["Polyclawd"])
    b = _subject_set(rel_path="y/b.md", tags="", wikilinks=["Polyclawd"])
    assert _subjects_overlap(a, b)


def test_overlap_same_folder():
    a = _subject_set(rel_path="02-Projects/Polyclawd/x.md", tags="", wikilinks=[])
    b = _subject_set(rel_path="02-Projects/Polyclawd/y.md", tags="", wikilinks=[])
    assert _subjects_overlap(a, b)


def test_no_overlap_different_subjects():
    """The dogfood false positive: vault-index vs a daily note about
    something completely different."""
    a = _subject_set(
        rel_path=".vault-index.md", tags="", wikilinks=[]
    )
    b = _subject_set(
        rel_path="01-Daily/2026-04-30.md", tags="daily", wikilinks=[]
    )
    assert not _subjects_overlap(a, b)


def test_no_overlap_when_both_empty():
    """MEMORY.md vs SOUL.md — both root, no tags, no shared wikilinks.
    The other dogfood false positive."""
    a = _subject_set(rel_path="MEMORY.md", tags="", wikilinks=[])
    b = _subject_set(rel_path="SOUL.md", tags="", wikilinks=[])
    assert not _subjects_overlap(a, b)


def test_cross_link_alone_is_overlap():
    """If note A links to note B (or vice versa), they're about the same
    subject by direct reference, even without other shared subjects.

    This is captured by 'link:b' appearing in A's subject set; B's stem also
    becomes a subject for the matching purpose."""
    a = _subject_set(rel_path="x/a.md", tags="", wikilinks=["b"])
    b = _subject_set(rel_path="x/b.md", tags="", wikilinks=[])
    # B's stem is 'b'; A links to 'b'. So they overlap if we compare
    # A's link:b against B's stem-as-subject.
    assert _subjects_overlap(a, b)

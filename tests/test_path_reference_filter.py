"""Path-based reference-doc exclusion.

Per [[V0-Verb-Quality-Fixes-Spec-2026-05-22]] Phase B-continued: reference
docs that lack `type: reference` frontmatter still need to be excluded.
Common patterns: SKILL.md, README.md, *-reference.md, *-spec.md, *-api.md.

This complements the frontmatter type filter — together they catch ~95%
of the buried-insight false-positive class without touching note bodies.

Added 2026-05-22.
"""

from __future__ import annotations

import pytest

from basalt.filters import is_reference_path


def test_skill_md_is_reference():
    assert is_reference_path("08-AI/Skills/AI-Meta/mcp-builder/SKILL.md")
    assert is_reference_path("Skills/foo/SKILL.md")
    assert is_reference_path("SKILL.md")


def test_readme_is_reference():
    assert is_reference_path("README.md")
    assert is_reference_path("02-Projects/Polyclawd/README.md")


def test_reference_suffix_filename():
    assert is_reference_path("08-AI/Skills/Dev/pdf/reference.md")
    assert is_reference_path("docs/api-reference.md")
    assert is_reference_path("foo-reference.md")


def test_spec_suffix_filename():
    assert is_reference_path("api-spec.md")
    assert is_reference_path("02-Projects/Basalt/03-Specs/Site-Spec.md")


def test_api_suffix_filename():
    """*-api.md is reference; ALL_CAPS_NAME.md is not (project docs)."""
    assert is_reference_path("docs/api.md")
    assert is_reference_path("docs/v2-api.md")
    # MCP_SERVER.md is a project-named doc, not a reference suffix
    assert not is_reference_path("MCP_SERVER.md")


def test_changelog_is_reference():
    assert is_reference_path("CHANGELOG.md")
    assert is_reference_path("CONTRIBUTING.md")


def test_normal_notes_not_reference():
    assert not is_reference_path("02-Projects/Polyclawd/Strategy/edge.md")
    assert not is_reference_path("01-Daily/2026-05-22.md")
    assert not is_reference_path("04-Trading/Research/VPIN.md")
    assert not is_reference_path("MEMORY.md")
    # Note: MEMORY.md / SOUL.md are root config but not "reference" — they're
    # belief-shaped. Path-only heuristic correctly leaves them in.


def test_case_insensitive_filename_matching():
    """Real vault filenames vary — Skill.md vs SKILL.md vs skill.md."""
    assert is_reference_path("plugins/foo/skill.md")
    assert is_reference_path("plugins/foo/Skill.md")
    assert is_reference_path("readme.md")


def test_path_filter_safe_for_root_files():
    """Root-level files with no reference-shape filename pass through."""
    assert not is_reference_path("notes.md")
    assert not is_reference_path("ideas.md")

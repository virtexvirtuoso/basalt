"""Frontmatter-awareness tests for vault.parse_note.

These tests pin the behavior that Note carries status/type/confidence from
frontmatter so downstream verbs can filter on them.

Added 2026-05-22 as Phase A of [[V0-Verb-Quality-Fixes-Spec-2026-05-22]].
"""

from __future__ import annotations

from pathlib import Path

import pytest

from basalt.vault import parse_note


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    return tmp_path


def _write(vault_root: Path, rel: str, body: str) -> Path:
    path = vault_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_parse_note_extracts_status_from_frontmatter(vault_root: Path):
    path = _write(
        vault_root,
        "note.md",
        "---\nstatus: archived\n---\n\nbody\n",
    )
    note = parse_note(path, vault_root)
    assert note is not None
    assert note.status == "archived"


def test_parse_note_status_missing_returns_none(vault_root: Path):
    path = _write(vault_root, "note.md", "---\ntitle: foo\n---\n\nbody\n")
    note = parse_note(path, vault_root)
    assert note is not None
    assert note.status is None


def test_parse_note_extracts_type_from_frontmatter(vault_root: Path):
    path = _write(
        vault_root,
        "note.md",
        "---\ntype: reference\n---\n\nbody\n",
    )
    note = parse_note(path, vault_root)
    assert note is not None
    assert note.type == "reference"


def test_parse_note_extracts_confidence_from_frontmatter(vault_root: Path):
    path = _write(
        vault_root,
        "note.md",
        "---\nconfidence: HIGH\n---\n\nbody\n",
    )
    note = parse_note(path, vault_root)
    assert note is not None
    assert note.confidence == "HIGH"


def test_parse_note_all_three_fields_together(vault_root: Path):
    path = _write(
        vault_root,
        "note.md",
        "---\nstatus: active\ntype: spec\nconfidence: MEDIUM\n---\n\nbody\n",
    )
    note = parse_note(path, vault_root)
    assert note is not None
    assert note.status == "active"
    assert note.type == "spec"
    assert note.confidence == "MEDIUM"


def test_parse_note_lowercases_status_but_preserves_confidence_case(vault_root: Path):
    """status is conventionally lowercase in the vault (active/draft/archived);
    confidence is conventionally uppercase (HIGH/MEDIUM/LOW).

    Parser preserves the user's casing — downstream filters compare
    case-insensitively. This test pins that the parser does NOT mutate case."""
    path = _write(
        vault_root,
        "note.md",
        "---\nstatus: Active\nconfidence: medium\n---\n\nbody\n",
    )
    note = parse_note(path, vault_root)
    assert note is not None
    assert note.status == "Active"  # preserved as written
    assert note.confidence == "medium"  # preserved as written


def test_parse_note_non_string_values_coerced_to_string(vault_root: Path):
    """Frontmatter may yield int/bool/etc. for these fields if user is sloppy.
    Parser should coerce to str to avoid type errors downstream."""
    path = _write(
        vault_root,
        "note.md",
        "---\nconfidence: 5\nstatus: true\n---\n\nbody\n",
    )
    note = parse_note(path, vault_root)
    assert note is not None
    assert note.confidence == "5"
    assert note.status == "True"

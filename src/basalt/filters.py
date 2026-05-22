"""Frontmatter-aware filter primitives shared across verbs.

Per [[V0-Verb-Quality-Fixes-Spec-2026-05-22]] Phase A: every verb invokes
these filters before per-verb logic. Centralizing the filter logic here
means a single edit changes behavior across all verbs.

All filters:
- Accept any iterable of Note objects
- Return a generator (verb-level chaining doesn't materialize lists)
- Compare frontmatter values case-insensitively
- Treat missing values as "field unset" — kept by default, never dropped

The exclusion lists are intentionally short and conservative. Vaults vary;
user-configurable extensions live in `~/.basalt/config.toml` (future work).
"""

from __future__ import annotations

from typing import Iterable, Iterator

from basalt.vault import Note


# Status values that mean "the user moved on; don't surface."
_ARCHIVED_STATUSES = frozenset({"archived", "dead", "superseded"})

# Status values that mean "unsettled thought; not a commitment yet."
_DRAFT_STATUSES = frozenset({"draft", "wip"})

# Type values that mean "documentation, not a belief." Reference docs have
# the same backlink-but-not-returned-to signal as forgotten claims, so the
# v0 Buried Insight heuristic confuses them. This filter prevents that.
_REFERENCE_TYPES = frozenset({"reference", "wiki", "doc", "api", "template"})

# Confidence multipliers for scoring. Missing confidence is treated as
# middle-of-the-road — don't penalize notes that simply don't use the field.
_CONFIDENCE_WEIGHTS = {
    "high": 1.0,
    "medium": 0.7,
    "low": 0.4,
}
_CONFIDENCE_UNSET = 0.8


def _norm(v: str | None) -> str | None:
    return v.lower() if isinstance(v, str) else None


def filter_archived(notes: Iterable[Note]) -> Iterator[Note]:
    """Drop notes whose status indicates the user has moved on.

    Excludes: status in {archived, dead, superseded} (case-insensitive).
    Keeps: everything else, including notes with no status field.
    """
    for note in notes:
        if _norm(note.status) not in _ARCHIVED_STATUSES:
            yield note


def filter_drafts(
    notes: Iterable[Note], *, allow_drafts: bool = False
) -> Iterator[Note]:
    """Drop notes whose status indicates unsettled thought.

    Excludes: status in {draft, wip} (case-insensitive).
    Keeps: everything else, including notes with no status field.

    Pass allow_drafts=True to keep drafts in the pool (used by verbs that
    care about evolving thinking, e.g. a future `drift` enhancement).
    """
    if allow_drafts:
        yield from notes
        return
    for note in notes:
        if _norm(note.status) not in _DRAFT_STATUSES:
            yield note


def filter_references(notes: Iterable[Note]) -> Iterator[Note]:
    """Drop notes whose type indicates documentation rather than belief.

    Excludes: type in {reference, wiki, doc, api, template} (case-insensitive).
    Keeps: everything else, including notes with no type field.

    Rationale: reference docs share the buried-insight signal pattern
    (high centrality + low recency) but they're *intentionally* not
    returned to. Per the dogfood findings, 2 of 3 false-positive Buried
    Insights were reference docs.
    """
    for note in notes:
        if _norm(note.type) not in _REFERENCE_TYPES:
            yield note


# Filename patterns that signal "reference doc" even without `type: reference`
# frontmatter. Match is case-insensitive against the filename component only
# (the path prefix is ignored — these patterns are universal across folders).
_REFERENCE_FILENAME_EXACT = frozenset(
    {
        "skill.md",
        "readme.md",
        "changelog.md",
        "contributing.md",
        "reference.md",
        "api.md",
    }
)

# Filenames ending in these suffixes are treated as reference docs.
_REFERENCE_FILENAME_SUFFIXES = (
    "-reference.md",
    "-spec.md",
    "-api.md",
)

# Folder names anywhere in the path that signal "this whole subtree is docs."
# Match is on individual path components (case-insensitive), so a folder
# named "reference" or "references" anywhere catches every file under it.
# Per dogfood findings: the failing reference docs lived under
# Skills/.../reference/ and Skills/.../references/ — filename alone didn't
# catch them because the docs were named output-patterns.md, templates.md,
# etc.
_REFERENCE_FOLDER_NAMES = frozenset(
    {
        "reference",
        "references",
        "templates",
        ".obsidian",
        "_drafts",
        # Whole-subtree exclusions: these top-level folders are documentation
        # by convention, not belief-shaped. Catches helper files in skill
        # folders that aren't named SKILL.md or *-reference.md.
        "skills",
    }
)


def is_reference_path(rel_path: str) -> bool:
    """True if the path matches a reference-doc pattern.

    Three layers of detection:
      1. Filename exact match: SKILL.md / README.md / CHANGELOG.md / etc.
      2. Filename suffix: *-reference.md / *-spec.md / *-api.md
      3. Folder anywhere in path: reference/ / references/ / templates/

    Match is case-insensitive on each path component.

    Returns False for everything else, including root-level config notes
    (MEMORY.md, SOUL.md) that are belief-shaped, not documentation-shaped.
    """
    from pathlib import Path as _P

    parts = [p.lower() for p in _P(rel_path).parts]
    # Folder-anywhere check (skip the last part — that's the filename)
    if any(p in _REFERENCE_FOLDER_NAMES for p in parts[:-1]):
        return True

    filename = parts[-1] if parts else ""
    if filename in _REFERENCE_FILENAME_EXACT:
        return True
    for suffix in _REFERENCE_FILENAME_SUFFIXES:
        if filename.endswith(suffix):
            return True
    return False


def sql_exclude_clause() -> str:
    """Return a SQL WHERE fragment that mirrors the iterator filters above.

    Use as:
        WHERE {sql_exclude_clause()}                  -- standalone
        WHERE existing_condition AND {sql_exclude_clause()}  -- composed

    Excludes notes where:
      - status is in the archived set (archived/dead/superseded)
      - status is in the draft set (draft/wip)
      - type is in the reference set (reference/wiki/doc/api/template)

    NULL values are kept (`status IS NULL OR ...`) — the field is opt-in.
    Comparisons are case-insensitive via LOWER().
    """
    excluded_statuses = _ARCHIVED_STATUSES | _DRAFT_STATUSES
    statuses_sql = ", ".join(f"'{s}'" for s in sorted(excluded_statuses))
    types_sql = ", ".join(f"'{t}'" for t in sorted(_REFERENCE_TYPES))

    # Path-based reference exclusion (Phase B-continued, 2026-05-22):
    # catches SKILL.md / README.md / *-reference.md / *-spec.md / *-api.md
    # patterns that lack `type: reference` frontmatter. Lowercase the path
    # column to match the case-insensitive filename heuristic.
    exact_names_sql = ", ".join(f"'{n}'" for n in sorted(_REFERENCE_FILENAME_EXACT))
    # SQLite GLOB does case-sensitive matching, so we LOWER the column.
    # GLOB is faster than LIKE here and naturally anchored at end with *.
    suffix_globs = " OR ".join(
        f"LOWER(rel_path) GLOB '*{suffix}'" for suffix in _REFERENCE_FILENAME_SUFFIXES
    )
    # Filename = last path component. SQLite has no PATH_BASENAME builtin,
    # so we approximate: filename matches when either the whole rel_path is
    # the bare filename (no slash) OR the rel_path ends in /<filename>.
    exact_match = " OR ".join(
        f"LOWER(rel_path) = '{n}' OR LOWER(rel_path) GLOB '*/{n}'"
        for n in sorted(_REFERENCE_FILENAME_EXACT)
    )

    # Folder-anywhere exclusion (added 2026-05-22, second pass):
    # any path with /reference/ /references/ /templates/ as an interior
    # directory component is documentation. SQLite LIKE '%/reference/%' is
    # case-sensitive on default; use LOWER() to make it case-insensitive.
    folder_match = " OR ".join(
        f"LOWER(rel_path) LIKE '%/{f}/%' OR LOWER(rel_path) LIKE '{f}/%'"
        for f in sorted(_REFERENCE_FOLDER_NAMES)
    )

    return (
        f"(status IS NULL OR LOWER(status) NOT IN ({statuses_sql})) "
        f"AND (type IS NULL OR LOWER(type) NOT IN ({types_sql})) "
        f"AND NOT ({exact_match}) "
        f"AND NOT ({suffix_globs}) "
        f"AND NOT ({folder_match})"
    )


def weight_by_confidence(note: Note) -> float:
    """Return a per-note confidence multiplier for scoring.

    HIGH → 1.0, MEDIUM → 0.7, LOW → 0.4. Missing or unrecognized → 0.8
    (middle-of-the-road; never silently drop to zero).

    Used inside verb scoring to bias toward higher-confidence claims
    without filtering out lower-confidence notes entirely.
    """
    norm = _norm(note.confidence)
    if norm is None:
        return _CONFIDENCE_UNSET
    return _CONFIDENCE_WEIGHTS.get(norm, _CONFIDENCE_UNSET)

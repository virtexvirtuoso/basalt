"""Drift algorithm — stated vs lived priority diff.

Site language: *"What you say is the priority versus what you actually
spent the week on."*

The fourth and final site-advertised unlock. Compares two ranked lists:
**stated priority** (project structure — number of notes per project
folder, weighted by recency) vs **lived priority** (project mentions in
the last N days of daily notes).

A drift event is a project whose stated rank differs materially from its
lived rank. *"Atlas is your stated #1 but lived #4. Beacon is stated #5
but lived #1."*

v0 makes one structural assumption: projects live in folders matching
`02-Projects/<Name>/...` and daily notes match the filename pattern
`YYYY-MM-DD*.md` OR are tagged `daily` in frontmatter. Both conventions
are vault-portable (Obsidian Daily Notes plugin default + standard
project organisation). The algorithm gracefully returns no findings when
the vault doesn't have ≥2 projects or ≥3 dated daily notes.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable


DEFAULT_WINDOW_DAYS    = 30        # how far back to walk daily notes
MIN_PROJECTS           = 2         # need at least 2 projects for drift to be meaningful
MIN_DAILY_NOTES        = 3         # need at least 3 daily notes in the window
DEFAULT_TOP_N          = 1         # one Drift finding by default — too many is noise

# Project root recognition — match paths under any "02-Projects" / "Projects" subfolder
_PROJECT_PATH_RE = re.compile(r"^(?:\d+[-_])?Projects/([^/]+)(?:/|$)")
# Daily note recognition by filename pattern
_DAILY_FILENAME_RE = re.compile(r"^.*?(\d{4}-\d{2}-\d{2}).*\.md$")


@dataclass
class ProjectShare:
    name: str
    stated_notes: int            # count of notes in the project's folder tree
    stated_share: float          # stated_notes / total_stated
    stated_rank: int             # 1-based rank by stated_share
    lived_mentions: int          # count of name occurrences across daily notes in window
    lived_share: float           # lived_mentions / total_lived
    lived_rank: int              # 1-based rank by lived_share
    drift_pct: float             # lived_share - stated_share, in pp


@dataclass
class DriftFinding:
    window_days: int
    daily_note_count: int        # notes seen in window
    project_count: int           # number of projects compared
    total_mentions: int          # sum of mentions across all projects
    shares: list[ProjectShare]   # all projects, sorted by absolute drift
    headline_overworked: ProjectShare | None  # biggest positive drift — lived ≫ stated
    headline_underworked: ProjectShare | None # biggest negative drift — stated ≫ lived
    score: float                 # max(|drift_pct|) — for ranking


def _extract_project_name(rel_path: str) -> str | None:
    """Return the project name if rel_path is inside a Projects/ subfolder, else None."""
    m = _PROJECT_PATH_RE.match(rel_path)
    return m.group(1) if m else None


def _is_daily_note(rel_path: str, tags: str | None) -> tuple[bool, date | None]:
    """Heuristic daily-note detector. Returns (is_daily, date_if_known)."""
    if tags and "daily" in (tags or "").lower():
        # Tag wins — try to parse a date from the path anyway for windowing
        m = _DAILY_FILENAME_RE.match(rel_path.rsplit("/", 1)[-1])
        if m:
            try:
                return True, date.fromisoformat(m.group(1))
            except ValueError:
                pass
        return True, None
    # Filename pattern fallback
    m = _DAILY_FILENAME_RE.match(rel_path.rsplit("/", 1)[-1])
    if m:
        try:
            return True, date.fromisoformat(m.group(1))
        except ValueError:
            pass
    return False, None


def _build_mention_pattern(project_names: Iterable[str]) -> re.Pattern[str]:
    """Build a single regex that captures any project-name word-boundary
    occurrence. Sorted by length descending so longer names match before
    shorter substrings ('BTC Wiz' before 'BTC')."""
    sorted_names = sorted(set(project_names), key=lambda n: -len(n))
    escaped = [re.escape(n) for n in sorted_names if n]
    if not escaped:
        return re.compile("a^")  # never matches
    pattern = r"(?<![A-Za-z0-9])(" + "|".join(escaped) + r")(?![A-Za-z0-9])"
    return re.compile(pattern, re.IGNORECASE)


def _count_mentions(content: str, pattern: re.Pattern[str]) -> dict[str, int]:
    """Return a dict project_name → mention count for a single note body."""
    counts: dict[str, int] = {}
    for match in pattern.finditer(content):
        name = match.group(1)
        # Normalize to canonical case via dict key (case-insensitive matching).
        # We store the case as it appears in the project-name list later.
        counts[name.lower()] = counts.get(name.lower(), 0) + 1
    return counts


def find_drift(
    conn: sqlite3.Connection,
    today: date | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    top_n: int = DEFAULT_TOP_N,
) -> list[DriftFinding]:
    """Return up to `top_n` drift findings.

    v0 produces at most one finding per call — the largest-magnitude drift
    in the window. `top_n > 1` is reserved for future calls that surface
    secondary divergences; currently caps at 1.
    """
    today = today or date.today()
    cutoff = today - timedelta(days=window_days)

    # 1. Discover projects and per-project stated note counts
    rows = conn.execute(
        "SELECT rel_path, tags, content, updated FROM notes"
    ).fetchall()
    if not rows:
        return []

    project_notes: dict[str, int] = {}
    for r in rows:
        name = _extract_project_name(r["rel_path"])
        if name:
            project_notes[name] = project_notes.get(name, 0) + 1

    if len(project_notes) < MIN_PROJECTS:
        return []

    # 2. Collect daily notes in window
    daily_notes: list[tuple[str, date | None]] = []
    for r in rows:
        is_daily, dn_date = _is_daily_note(r["rel_path"], r["tags"])
        if not is_daily:
            continue
        if dn_date is not None and dn_date < cutoff:
            continue
        # If we have no date, try to use updated as a proxy; if that's also
        # absent, include conservatively (might be a recent untyped daily note).
        daily_notes.append((r["content"] or "", dn_date))

    if len(daily_notes) < MIN_DAILY_NOTES:
        return []

    # 3. Count project mentions across daily notes
    pattern = _build_mention_pattern(project_notes.keys())
    canonical_case = {name.lower(): name for name in project_notes}
    mention_counts: dict[str, int] = {name: 0 for name in project_notes}
    for content, _dn_date in daily_notes:
        counts = _count_mentions(content, pattern)
        for lowered, c in counts.items():
            canonical = canonical_case.get(lowered)
            if canonical:
                mention_counts[canonical] = mention_counts.get(canonical, 0) + c

    total_stated = sum(project_notes.values()) or 1
    total_lived = sum(mention_counts.values())
    if total_lived == 0:
        # Daily notes never mentioned any project name — no signal to compute drift on.
        return []

    # 4. Compute shares + ranks
    stated_sorted = sorted(project_notes.items(), key=lambda kv: -kv[1])
    lived_sorted = sorted(mention_counts.items(), key=lambda kv: -kv[1])
    stated_rank = {name: i + 1 for i, (name, _) in enumerate(stated_sorted)}
    lived_rank = {name: i + 1 for i, (name, _) in enumerate(lived_sorted)}

    shares: list[ProjectShare] = []
    for name, stated_n in project_notes.items():
        stated_share = stated_n / total_stated
        lived_n = mention_counts.get(name, 0)
        lived_share = lived_n / total_lived
        shares.append(ProjectShare(
            name=name,
            stated_notes=stated_n,
            stated_share=stated_share,
            stated_rank=stated_rank[name],
            lived_mentions=lived_n,
            lived_share=lived_share,
            lived_rank=lived_rank[name],
            drift_pct=(lived_share - stated_share) * 100.0,  # in percentage points
        ))

    # Sort by absolute drift (largest first) — that's the "who diverges most" axis.
    shares.sort(key=lambda s: -abs(s.drift_pct))

    # 5. Pick the headline projects: the most over-worked (positive drift) and
    # the most under-worked (negative drift). At least one must exist or the
    # finding is trivial (no divergence).
    overworked = next((s for s in shares if s.drift_pct > 5.0), None)   # ≥5pp drift to count
    underworked = next((s for s in shares if s.drift_pct < -5.0), None)
    if overworked is None and underworked is None:
        return []

    # Score: largest |drift_pct| — for cross-finding ranking when top_n > 1
    score = max((abs(s.drift_pct) for s in shares), default=0.0)

    finding = DriftFinding(
        window_days=window_days,
        daily_note_count=len(daily_notes),
        project_count=len(project_notes),
        total_mentions=total_lived,
        shares=shares,
        headline_overworked=overworked,
        headline_underworked=underworked,
        score=score,
    )

    return [finding][:max(1, top_n)]

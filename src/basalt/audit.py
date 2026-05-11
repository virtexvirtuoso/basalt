"""Calibration Layer — falsification rules + track-record audit for past Briefs.

Every Brief finding is logged to the `briefs` table with a list of
falsification rules ("this is wrong if you ever observe X"). The `basalt
audit` command re-walks pending briefs against the current vault state and
moves each to `confirmed` or `falsified` with a verdict reason.

This is the single feature that converts Basalt from "AI summary tool" into
"research log." Eugeniughelbur's "vault rewrites itself" model destroys the
historical signal needed to ship this; Basalt's read-only stance preserves it.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any


# Falsification thresholds — empirically chosen for v0.
# Each verb's rules are timed in days from brief creation.
DEFAULT_BURIED_GRACE_DAYS    = 60   # buried insight — wrong if no new validators in this window
DEFAULT_CONN_GRACE_DAYS      = 60   # connection — wrong if user doesn't link them
DEFAULT_CONTRA_GRACE_DAYS    = 60   # contradiction — wrong if neither note edited
DEFAULT_WORDCOUNT_DROP_PCT   = 30   # any verb — wrong if cited note shrinks > N%


# ── Falsification rule generation ─────────────────────────────────────

def falsification_rules_for(verb: str, finding: Any) -> list[dict]:
    """Derive falsification rules for a given finding. Verb-dispatch table.

    Each rule is `{"kind": str, "params": dict, "text": str}` — `kind` and
    `params` drive the audit re-evaluation; `text` is the human-readable
    line shown in the Brief.
    """
    if verb == "buried-insight":
        return _buried_rules(finding)
    if verb == "connection":
        return _connection_rules(finding)
    if verb == "contradiction":
        return _contradiction_rules(finding)
    if verb == "implicit-thesis":
        return _implicit_thesis_rules(finding)
    if verb == "drift":
        return _drift_rules(finding)
    return []


def _buried_rules(r) -> list[dict]:
    rel = r.candidate.rel_path
    return [
        {
            "kind": "no_new_validators",
            "params": {"rel_path": rel, "grace_days": DEFAULT_BURIED_GRACE_DAYS},
            "text": (
                f"wrong if no new note links to or semantically validates "
                f"{rel} within {DEFAULT_BURIED_GRACE_DAYS} days"
            ),
        },
        {
            "kind": "candidate_shrinks",
            "params": {"rel_path": rel, "drop_pct": DEFAULT_WORDCOUNT_DROP_PCT},
            "text": (
                f"wrong if {rel} loses more than {DEFAULT_WORDCOUNT_DROP_PCT}% "
                f"of its content (you actively dismantled the claim)"
            ),
        },
        {
            "kind": "candidate_deleted",
            "params": {"rel_path": rel},
            "text": f"wrong if {rel} is deleted (you've moved on from this claim)",
        },
    ]


def _connection_rules(p) -> list[dict]:
    return [
        {
            "kind": "still_unlinked",
            "params": {
                "a": p.note_a_path, "b": p.note_b_path,
                "grace_days": DEFAULT_CONN_GRACE_DAYS,
            },
            "text": (
                f"wrong if you don't link {p.note_a_path} ↔ {p.note_b_path} "
                f"within {DEFAULT_CONN_GRACE_DAYS} days (you don't agree they're connected)"
            ),
        },
        {
            "kind": "either_shrinks",
            "params": {"a": p.note_a_path, "b": p.note_b_path, "drop_pct": 50},
            "text": (
                f"wrong if either note loses more than 50% of its content "
                f"(the underlying idea was discarded)"
            ),
        },
    ]


def _implicit_thesis_rules(t) -> list[dict]:
    return [
        {
            "kind": "centroid_deleted",
            "params": {"rel_path": t.centroid_path},
            "text": (
                f"wrong if {t.centroid_path} is deleted (the proxy thesis "
                f"statement is gone — the cluster needs a new centroid)"
            ),
        },
        {
            "kind": "cluster_dispersed",
            "params": {
                "member_paths": list(t.member_paths),
                "min_remaining": max(2, t.cluster_size - 2),
            },
            "text": (
                f"wrong if more than 2 of the {t.cluster_size} cluster members "
                f"are deleted within 90 days (the through-line dissolves)"
            ),
        },
        {
            "kind": "no_new_rephrasing",
            "params": {
                "member_paths": list(t.member_paths),
                "grace_days": 90,
            },
            "text": (
                "wrong if no new note expresses a similar claim within 90 days "
                "(the convergence was a snapshot, not a recurring theme)"
            ),
        },
    ]


def _drift_rules(d) -> list[dict]:
    """Falsification for a drift finding. The drift is real if the divergence
    persists; falsified if stated and lived re-converge."""
    over = d.headline_overworked.name if d.headline_overworked else None
    under = d.headline_underworked.name if d.headline_underworked else None
    rules: list[dict] = []
    if over:
        rules.append({
            "kind": "drift_resolved",
            "params": {"project": over, "direction": "down", "grace_days": 30},
            "text": (
                f"wrong if {over}'s share of daily-note mentions drops back toward "
                f"its stated share within 30 days (the drift was a phase, not a pattern)"
            ),
        })
    if under:
        rules.append({
            "kind": "drift_resolved",
            "params": {"project": under, "direction": "up", "grace_days": 30},
            "text": (
                f"wrong if {under}'s share of daily-note mentions rises back toward "
                f"its stated share within 30 days (you've responded to the drift)"
            ),
        })
    rules.append({
        "kind": "structural_change",
        "params": {"projects_at_log": [s.name for s in d.shares]},
        "text": (
            "wrong if the project list itself changes materially within 60 days "
            "(you renamed/archived projects — the drift was structural, not behavioural)"
        ),
    })
    return rules


def _contradiction_rules(p) -> list[dict]:
    return [
        {
            "kind": "neither_edited",
            "params": {
                "a": p.note_a_path, "b": p.note_b_path,
                "grace_days": DEFAULT_CONTRA_GRACE_DAYS,
            },
            "text": (
                f"wrong if neither note is edited within {DEFAULT_CONTRA_GRACE_DAYS} days "
                f"(you don't think it's a real conflict — heuristic was a false positive)"
            ),
        },
        {
            "kind": "still_in_conflict",
            "params": {
                "a": p.note_a_path, "b": p.note_b_path,
                "grace_days": 90,
            },
            "text": (
                f"confirmed if both notes still exist with the contradiction "
                f"signal intact after 90 days (the conflict is real and unresolved)"
            ),
        },
    ]


# ── Recording ─────────────────────────────────────────────────────────

def _finding_key(verb: str, finding: Any) -> str:
    """Stable identifier so re-running `basalt brief` doesn't create duplicate
    pending briefs for the same finding."""
    if verb == "buried-insight":
        return f"{verb}:{finding.candidate.rel_path}"
    if verb == "connection":
        a, b = sorted([finding.note_a_path, finding.note_b_path])
        return f"{verb}:{a}|{b}"
    if verb == "contradiction":
        a, b = sorted([finding.note_a_path, finding.note_b_path])
        return f"{verb}:{a}|{b}"
    if verb == "implicit-thesis":
        # Sort member paths so the same cluster gets the same key regardless
        # of internal order — even if the centroid changes.
        members_key = "|".join(sorted(finding.member_paths))
        return f"{verb}:{members_key}"
    if verb == "drift":
        # Same drift = same headline pair within the same window. Idempotent
        # against multiple `basalt brief` runs in a day.
        over = finding.headline_overworked.name if finding.headline_overworked else "-"
        under = finding.headline_underworked.name if finding.headline_underworked else "-"
        return f"{verb}:{under}->{over}@{finding.window_days}d"
    return f"{verb}:?"


def _cited_paths(verb: str, finding: Any) -> list[str]:
    """Which `rel_path`s does this finding cite? Used to snapshot word_count
    at log time so `*_shrinks` rules can fire later in `audit_pending`.
    Drift cites projects, not notes — returns empty list."""
    if verb == "buried-insight":
        return [finding.candidate.rel_path]
    if verb in ("connection", "contradiction"):
        return [finding.note_a_path, finding.note_b_path]
    if verb == "implicit-thesis":
        return [finding.centroid_path, *finding.member_paths]
    return []


def _lookup_word_counts(conn: sqlite3.Connection, rel_paths: list[str]) -> dict[str, int]:
    """Fetch current word_count for each cited rel_path. Snapshotted into the
    finding payload at record time so shrinks-rules have a baseline to compare.

    Notes not present in the DB are simply absent from the dict (no fabrication)."""
    if not rel_paths:
        return {}
    placeholders = ",".join("?" * len(rel_paths))
    rows = conn.execute(
        f"SELECT rel_path, word_count FROM notes WHERE rel_path IN ({placeholders})",
        rel_paths,
    ).fetchall()
    return {r["rel_path"]: r["word_count"] for r in rows}


def _finding_payload(verb: str, finding: Any, word_counts: dict[str, int]) -> dict:
    """Serialize the finding to a dict that survives across versions.
    Stores enough to re-render the brief later from the briefs table alone.
    `word_counts` is the rel_path → word_count snapshot at log time; verbs that
    cite specific notes embed it as `word_counts_at_log` so `*_shrinks` rules
    can fire later. (See v0.0.13 — earlier payloads omitted this and those
    rules stayed pending forever.)"""
    if verb == "buried-insight":
        c = finding.candidate
        return {
            "rel_path": c.rel_path,
            "title": c.title,
            "quote": finding.quote,
            "quote_provenance": finding.quote_provenance,
            "score": c.score,
            "validator_count": len(finding.validators),
            "word_counts_at_log": dict(word_counts),
        }
    if verb == "connection":
        return {
            "note_a_path": finding.note_a_path,
            "note_a_quote": finding.note_a_quote,
            "note_b_path": finding.note_b_path,
            "note_b_quote": finding.note_b_quote,
            "similarity": finding.similarity,
            "word_counts_at_log": dict(word_counts),
        }
    if verb == "contradiction":
        return {
            "note_a_path": finding.note_a_path,
            "note_a_quote": finding.note_a_quote,
            "note_b_path": finding.note_b_path,
            "note_b_quote": finding.note_b_quote,
            "similarity": finding.similarity,
            "signals": finding.signals,
            "word_counts_at_log": dict(word_counts),
        }
    if verb == "implicit-thesis":
        return {
            "centroid_path": finding.centroid_path,
            "centroid_quote": finding.centroid_quote,
            "centroid_quote_provenance": finding.centroid_quote_provenance,
            "member_paths": list(finding.member_paths),
            "member_quotes": list(finding.member_quotes),
            "cluster_size": finding.cluster_size,
            "folder_diversity": finding.folder_diversity,
            "span_days": finding.span_days,
            "mean_similarity": finding.mean_similarity,
            "word_counts_at_log": dict(word_counts),
        }
    if verb == "drift":
        return {
            "window_days": finding.window_days,
            "daily_note_count": finding.daily_note_count,
            "project_count": finding.project_count,
            "total_mentions": finding.total_mentions,
            "headline_overworked": finding.headline_overworked.name if finding.headline_overworked else None,
            "headline_underworked": finding.headline_underworked.name if finding.headline_underworked else None,
            "shares_at_log": [
                {"name": s.name, "stated_share": s.stated_share, "lived_share": s.lived_share}
                for s in finding.shares
            ],
        }
    return {}


def record_finding(
    conn: sqlite3.Connection,
    verb: str,
    finding: Any,
    today: date | None = None,
) -> int | None:
    """Log a Brief finding to the calibration table.

    Returns the row id of the newly inserted brief, or None if a pending
    brief with the same `finding_key` already exists (idempotent — running
    `basalt brief` twice on the same vault doesn't double-log).
    """
    today = today or date.today()
    key = _finding_key(verb, finding)
    word_counts = _lookup_word_counts(conn, _cited_paths(verb, finding))
    payload = _finding_payload(verb, finding, word_counts)
    rules = falsification_rules_for(verb, finding)

    existing = conn.execute(
        "SELECT id FROM briefs WHERE verb = ? AND finding_key = ? AND status = 'pending'",
        (verb, key),
    ).fetchone()
    if existing:
        return None

    cur = conn.execute(
        """
        INSERT INTO briefs (verb, finding_key, finding_json, falsification, created_at, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
        RETURNING id
        """,
        (
            verb, key,
            json.dumps(payload),
            json.dumps(rules),
            today.isoformat(),
        ),
    )
    row = cur.fetchone()
    conn.commit()
    return row[0]


# ── Auditing ──────────────────────────────────────────────────────────

@dataclass
class AuditResult:
    brief_id: int
    verb: str
    finding_key: str
    rule_kind: str
    new_status: str           # 'confirmed' | 'falsified' | 'pending'
    reason: str
    age_days: int


def _vault_state_lookup(conn: sqlite3.Connection) -> dict[str, dict]:
    """Snapshot every note's current state for falsification rule evaluation.
    Returns a dict keyed by rel_path → {word_count, updated, exists}.
    """
    out: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT rel_path, word_count, updated FROM notes"
    ).fetchall():
        out[r["rel_path"]] = {
            "word_count": r["word_count"],
            "updated": r["updated"],
            "exists": True,
        }
    return out


def _link_targets_lookup(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    """Set of (from_rel_path, to_rel_path) currently linked. Used to detect
    when the user has linked two notes after a Connection brief."""
    rows = conn.execute(
        """
        SELECT n1.rel_path AS from_p, n2.rel_path AS to_p
        FROM links l
        JOIN notes n1 ON n1.id = l.from_note_id
        JOIN notes n2 ON n2.id = l.target_note_id
        WHERE l.target_note_id IS NOT NULL
        """
    ).fetchall()
    return {(r["from_p"], r["to_p"]) for r in rows}


def _evaluate_rule(
    rule: dict,
    finding: dict,
    age_days: int,
    state: dict[str, dict],
    links: set[tuple[str, str]],
) -> tuple[str, str]:
    """Return (new_status, reason). 'pending' means the rule hasn't fired yet."""
    kind = rule["kind"]
    p = rule["params"]

    if kind == "candidate_deleted":
        if p["rel_path"] not in state:
            return "falsified", f"{p['rel_path']} no longer exists in the vault"
        return "pending", ""

    if kind == "candidate_shrinks":
        rel = p["rel_path"]
        cur = state.get(rel)
        if not cur:
            return "falsified", f"{rel} no longer exists in the vault"
        original = (finding.get("word_counts_at_log") or {}).get(rel)
        if not original or original <= 0:
            # Legacy brief (logged before v0.0.13) — no baseline to compare against.
            # Stay pending so older findings don't get spuriously falsified.
            return "pending", ""
        current = cur["word_count"] or 0
        drop_pct = (original - current) / original * 100
        if drop_pct >= p["drop_pct"]:
            return "falsified", (
                f"{rel} shrank {drop_pct:.0f}% ({original} → {current} words) — "
                f"you actively dismantled the claim"
            )
        return "pending", ""

    if kind == "no_new_validators":
        if age_days < p["grace_days"]:
            return "pending", ""
        # Heuristic v0: if no notes have been updated within 30 days targeting
        # this rel_path, count it as falsified. Future v1 can re-run buried-insight
        # algorithm against history-cutoff and check if the finding still surfaces.
        # For now, leave as pending and surface in CLI as "needs review".
        return "pending", "v0: needs manual review (auto-evaluation not implemented)"

    if kind == "still_unlinked":
        a, b = p["a"], p["b"]
        if (a, b) in links or (b, a) in links:
            return "confirmed", f"you linked {a} ↔ {b} — connection confirmed"
        if age_days >= p["grace_days"]:
            return "falsified", f"no link between {a} and {b} after {age_days}d"
        return "pending", ""

    if kind == "either_shrinks":
        a, b = p["a"], p["b"]
        baseline = finding.get("word_counts_at_log") or {}
        if not baseline.get(a) and not baseline.get(b):
            # Legacy brief — no baseline at all. Stay pending.
            return "pending", ""
        for rel in (a, b):
            original = baseline.get(rel)
            cur = state.get(rel)
            if not original or original <= 0 or not cur:
                continue
            current = cur["word_count"] or 0
            drop_pct = (original - current) / original * 100
            if drop_pct >= p["drop_pct"]:
                return "falsified", (
                    f"{rel} shrank {drop_pct:.0f}% ({original} → {current} words) — "
                    f"the underlying idea was discarded"
                )
        return "pending", ""

    if kind == "neither_edited":
        a_state = state.get(p["a"])
        b_state = state.get(p["b"])
        if not a_state or not b_state:
            return "falsified", "one of the notes no longer exists"
        if age_days < p["grace_days"]:
            return "pending", ""
        try:
            a_updated = date.fromisoformat(a_state["updated"][:10]) if a_state["updated"] else None
            b_updated = date.fromisoformat(b_state["updated"][:10]) if b_state["updated"] else None
        except (ValueError, TypeError):
            return "pending", ""
        cutoff = date.today() - timedelta(days=p["grace_days"])
        a_recent = a_updated and a_updated >= cutoff
        b_recent = b_updated and b_updated >= cutoff
        if not a_recent and not b_recent:
            return "falsified", f"neither note edited in {p['grace_days']}+d — heuristic false positive"
        return "pending", ""

    if kind == "still_in_conflict":
        if age_days >= p["grace_days"]:
            a_state = state.get(p["a"])
            b_state = state.get(p["b"])
            if a_state and b_state:
                return "confirmed", (
                    f"both {p['a']} and {p['b']} still exist after {age_days}d — "
                    f"unresolved conflict"
                )
        return "pending", ""

    if kind == "centroid_deleted":
        if p["rel_path"] not in state:
            return "falsified", f"thesis centroid {p['rel_path']} no longer exists"
        return "pending", ""

    if kind == "cluster_dispersed":
        members = p.get("member_paths", [])
        remaining = sum(1 for m in members if m in state)
        if age_days >= 90 and remaining < p.get("min_remaining", 2):
            return "falsified", (
                f"only {remaining} of {len(members)} cluster members remain after {age_days}d"
            )
        return "pending", ""

    if kind == "no_new_rephrasing":
        # v0: needs the same kind of "is the through-line still recurring?"
        # check that the buried-insight `no_new_validators` rule needs.
        # Mark as needs-review for now; v1 fixes by re-running implicit_thesis
        # and checking if any new note joined the cluster.
        if age_days < p.get("grace_days", 90):
            return "pending", ""
        return "pending", "v0: needs manual review (auto-evaluation not implemented)"

    if kind == "drift_resolved":
        # v0: needs to re-run drift on the current window and compare
        # the project's lived/stated shares. Heuristic placeholder until v1.
        if age_days < p.get("grace_days", 30):
            return "pending", ""
        return "pending", "v0: re-run `basalt drift` to compare shares (auto-evaluation not implemented)"

    if kind == "structural_change":
        # The drift is structural rather than behavioural if the set of
        # projects changes materially. v0: count current projects and
        # compare to the count at log time; >25% change → falsify.
        projects_at_log = p.get("projects_at_log", [])
        if not projects_at_log:
            return "pending", ""
        # Check current project list via path-prefix scan (same logic as drift.py)
        # (Importing find_drift here would be circular; use a lightweight inline match.)
        import re as _re
        pat = _re.compile(r"^(?:\d+[-_])?Projects/([^/]+)(?:/|$)")
        current = set()
        for path, _ in state.items():
            m = pat.match(path)
            if m:
                current.add(m.group(1))
        if not current:
            return "pending", ""
        logged = set(projects_at_log)
        intersection = current & logged
        union = current | logged
        if not union:
            return "pending", ""
        jaccard = len(intersection) / len(union)
        if jaccard < 0.75:
            return "falsified", (
                f"project list changed materially since log time "
                f"(jaccard {jaccard:.2f}); drift was structural, not behavioural"
            )
        return "pending", ""

    return "pending", f"unknown rule kind {kind}"


def audit_pending(
    conn: sqlite3.Connection,
    today: date | None = None,
) -> list[AuditResult]:
    """Walk every pending brief and apply its falsification rules against
    current vault state. Returns the list of state changes."""
    today = today or date.today()
    state = _vault_state_lookup(conn)
    links = _link_targets_lookup(conn)

    rows = conn.execute(
        "SELECT id, verb, finding_key, finding_json, falsification, created_at "
        "FROM briefs WHERE status = 'pending'"
    ).fetchall()

    results: list[AuditResult] = []
    for r in rows:
        try:
            finding   = json.loads(r["finding_json"])
            rules     = json.loads(r["falsification"])
        except json.JSONDecodeError:
            continue
        try:
            created = date.fromisoformat(r["created_at"])
        except ValueError:
            continue
        age_days = (today - created).days

        # Apply rules in order. First non-pending verdict wins.
        for rule in rules:
            new_status, reason = _evaluate_rule(rule, finding, age_days, state, links)
            if new_status != "pending":
                conn.execute(
                    "UPDATE briefs SET status = ?, verdict_at = ?, verdict_reason = ? WHERE id = ?",
                    (new_status, today.isoformat(), reason, r["id"]),
                )
                results.append(AuditResult(
                    brief_id=r["id"],
                    verb=r["verb"],
                    finding_key=r["finding_key"],
                    rule_kind=rule["kind"],
                    new_status=new_status,
                    reason=reason,
                    age_days=age_days,
                ))
                break

    conn.commit()
    return results


# ── Track-record summary ──────────────────────────────────────────────

@dataclass
class TrackRecord:
    days: int                  # window
    confirmed: int
    pending: int
    falsified: int
    total: int

    @property
    def confirmed_pct(self) -> float:
        return 100.0 * self.confirmed / self.total if self.total else 0.0

    @property
    def falsified_pct(self) -> float:
        return 100.0 * self.falsified / self.total if self.total else 0.0


def track_record(conn: sqlite3.Connection, days: int = 90, today: date | None = None) -> TrackRecord:
    today = today or date.today()
    cutoff = (today - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT status, COUNT(*) FROM briefs WHERE created_at >= ? GROUP BY status",
        (cutoff,),
    ).fetchall()
    counts = {row[0]: row[1] for row in rows}
    return TrackRecord(
        days=days,
        confirmed=counts.get("confirmed", 0),
        pending=counts.get("pending", 0),
        falsified=counts.get("falsified", 0),
        total=sum(counts.values()),
    )


def render_falsification_lines(verb: str, finding: Any) -> list[str]:
    """Return human-readable falsification rule lines for inline display
    in the Brief output. Used by cli.py renderers."""
    rules = falsification_rules_for(verb, finding)
    return [r["text"] for r in rules]

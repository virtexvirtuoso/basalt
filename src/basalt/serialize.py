"""JSON serialization for verb outputs.

Stable schemas for `basalt brief --format=json` and downstream consumers.
This is the foundation for `basalt-mcp` — every MCP tool returns one of
these shapes. Keep stable across versions; bump explicitly via a `schema`
key when breaking.

Schema: 1 (2026-05-08)
"""

from __future__ import annotations

from datetime import date
from typing import Any


SCHEMA_VERSION = 1


def _date_str(d: date | None) -> str | None:
    if d is None:
        return None
    if isinstance(d, date):
        return d.isoformat()
    return str(d)


def buried_insight_to_dict(r) -> dict:
    c = r.candidate
    return {
        "verb": "buried-insight",
        "schema": SCHEMA_VERSION,
        "rel_path": c.rel_path,
        "title": c.title,
        "stem": c.stem,
        "created": _date_str(c.created),
        "updated": _date_str(c.updated),
        "word_count": c.word_count,
        "score": c.score,
        "hub_density": c.hub_density,
        "hub_penalty": c.hub_penalty,
        "inbound_recent_count": c.inbound_recent,
        "quote": r.quote,
        "quote_provenance": r.quote_provenance,
        "vault_age_days": r.vault_age_days,
        "thresholds": r.thresholds,
        "validators": [
            {
                "rel_path": v.rel_path,
                "title": v.title,
                "updated": _date_str(v.updated),
                "explicit_link": v.explicit_link,
                "similarity": v.sim,
            }
            for v in r.validators
        ],
    }


def connection_to_dict(p) -> dict:
    return {
        "verb": "connection",
        "schema": SCHEMA_VERSION,
        "similarity": p.similarity,
        "score": p.score,
        "note_a": {
            "rel_path": p.note_a_path,
            "title": p.note_a_title,
            "quote": p.note_a_quote,
            "quote_provenance": p.note_a_quote_provenance,
            "hub_density": p.a_hub_density,
        },
        "note_b": {
            "rel_path": p.note_b_path,
            "title": p.note_b_title,
            "quote": p.note_b_quote,
            "quote_provenance": p.note_b_quote_provenance,
            "hub_density": p.b_hub_density,
        },
    }


def implicit_thesis_to_dict(t) -> dict:
    return {
        "verb": "implicit-thesis",
        "schema": SCHEMA_VERSION,
        "version": "v0-cluster",
        "score": t.score,
        "cluster_size": t.cluster_size,
        "folder_diversity": t.folder_diversity,
        "span_days": t.span_days,
        "mean_similarity": t.mean_similarity,
        "centroid": {
            "rel_path": t.centroid_path,
            "title": t.centroid_title,
            "quote": t.centroid_quote,
            "quote_provenance": t.centroid_quote_provenance,
        },
        "members": [
            {
                "rel_path": rel,
                "title": title,
                "folder": folder,
                "quote": quote,
                "quote_provenance": prov,
            }
            for rel, title, folder, quote, prov in zip(
                t.member_paths,
                t.member_titles,
                t.member_folders,
                t.member_quotes,
                t.member_quote_provenances,
            )
        ],
    }


def contradiction_to_dict(p) -> dict:
    return {
        "verb": "contradiction",
        "schema": SCHEMA_VERSION,
        "version": "v0-heuristic",
        "topical_similarity": p.similarity,
        "contradiction_score": p.contradiction_score,
        "score": p.score,
        "signals": p.signals,
        "note_a": {
            "rel_path": p.note_a_path,
            "title": p.note_a_title,
            "quote": p.note_a_quote,
            "quote_provenance": p.note_a_quote_provenance,
        },
        "note_b": {
            "rel_path": p.note_b_path,
            "title": p.note_b_title,
            "quote": p.note_b_quote,
            "quote_provenance": p.note_b_quote_provenance,
        },
    }


def audit_result_to_dict(a) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "brief_id": a.brief_id,
        "verb": a.verb,
        "finding_key": a.finding_key,
        "rule_kind": a.rule_kind,
        "new_status": a.new_status,
        "reason": a.reason,
        "age_days": a.age_days,
    }


def track_record_to_dict(tr) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "window_days": tr.days,
        "confirmed": tr.confirmed,
        "pending": tr.pending,
        "falsified": tr.falsified,
        "total": tr.total,
        "confirmed_pct": round(tr.confirmed_pct, 1),
        "falsified_pct": round(tr.falsified_pct, 1),
    }


def falsification_rules_to_list(verb: str, finding: Any) -> list[dict]:
    """Return falsification rules for a finding as a serializable list."""
    from basalt.audit import falsification_rules_for
    return falsification_rules_for(verb, finding)


def with_falsification(payload: dict, verb: str, finding: Any) -> dict:
    """Decorate a serialized finding with its falsification rules. Used by
    the JSON output path so MCP clients see the calibration contract."""
    payload["falsification"] = falsification_rules_to_list(verb, finding)
    return payload

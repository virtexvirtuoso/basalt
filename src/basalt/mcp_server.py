"""Basalt MCP server — exposes the verb library to any MCP client.

The wedge against the Claude Code skills cohort (eugeniughelbur,
huytieu, NicholasSpisak, AgriciDaniel) is **standalone, read-only,
local-first**. An MCP server preserves all three: still standalone
(stdio transport, no Claude Code dependency), still read-only (no
tool here writes to the user's vault), still local-first (no network
calls except when the user has Ollama running on localhost).

Phase 0 — 4 tools mapped 1:1 to existing CLI verbs. No resources, no
prompts, no sampling — see [[MCP-Ship-Plan-2026-05-08]] §3.2 for the
rationale.

Run:
    basalt-mcp                  # stdio transport, default vault + DB

Wire into Claude Desktop:
    {
      "mcpServers": {
        "basalt": {
          "command": "basalt-mcp"
        }
      }
    }

Or with explicit paths:
    {
      "mcpServers": {
        "basalt": {
          "command": "basalt-mcp",
          "args": ["--vault", "/path/to/vault", "--db", "/path/to/basalt.db"]
        }
      }
    }
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from basalt.index import open_db
from basalt.buried import find_buried_insights
from basalt.connection import find_connections, DEFAULT_MIN_SIM as CONN_MIN_SIM
from basalt.contradiction import find_contradictions, DEFAULT_MIN_SIM as CONT_MIN_SIM
from basalt.audit import (
    record_finding,
    audit_pending,
    track_record,
)
from basalt.serialize import (
    SCHEMA_VERSION,
    buried_insight_to_dict,
    connection_to_dict,
    contradiction_to_dict,
    audit_result_to_dict,
    track_record_to_dict,
    with_falsification,
)


# ── Server config — vault path + DB path resolution ──────────────
#
# Precedence (highest first):
#   1. CLI arg at server start: `basalt-mcp --vault /x --db /y`
#   2. Env var: BASALT_VAULT, BASALT_DB
#   3. Defaults: ~/virtuoso-vault and ~/.basalt/basalt.db
#
# MCP roots (per spec) are not currently consumed — added in v0.2 once
# we've verified the major clients reliably forward them.

DEFAULT_VAULT = Path.home() / "virtuoso-vault"
DEFAULT_DB    = Path.home() / ".basalt" / "basalt.db"


_config = {
    "vault": DEFAULT_VAULT,
    "db":    DEFAULT_DB,
}


def _set_config(vault: Path | None, db: Path | None) -> None:
    """Resolve vault + db paths from CLI args / env / defaults."""
    _config["vault"] = (
        Path(vault).expanduser() if vault
        else Path(os.environ.get("BASALT_VAULT", str(DEFAULT_VAULT))).expanduser()
    )
    _config["db"] = (
        Path(db).expanduser() if db
        else Path(os.environ.get("BASALT_DB", str(DEFAULT_DB))).expanduser()
    )


def _open() -> Any:
    """Open the SQLite DB on demand. Per-call open/close is by design —
    keeps the server stateless and tolerant to DB path changes."""
    db_path = _config["db"]
    if not db_path.exists():
        raise RuntimeError(
            f"Basalt DB not found at {db_path}. Run `basalt index --vault {_config['vault']}` first."
        )
    return open_db(db_path)


# ── FastMCP server + tool registrations ──────────────────────────

mcp = FastMCP("basalt")


@mcp.tool(
    annotations={
        "title": "Run a Basalt Brief",
        "readOnlyHint": True,
        "openWorldHint": False,
        "idempotentHint": False,
    }
)
def basalt_brief(section: str = "buried-insight", top: int = 1, vault_aware: bool = True) -> dict:
    """Generate one or more sections of the Basalt Brief.

    Surfaces what the user wrote but never returned to (Buried Insight),
    pairs of notes that are the same idea in different folders (Connection),
    and same-topic notes whose load-bearing claims disagree (Contradiction
    v0 heuristic). Read-only on the vault. Logs each finding to the
    calibration table for later audit.

    Args:
        section: 'buried-insight', 'connection', 'contradiction', or 'all'.
        top: top N findings per section (1-10).
        vault_aware: Buried-Insight only — derive thresholds from vault age.

    Returns the same JSON shape as `basalt brief --format json`.
    """
    section_key = section.strip().lower()
    valid = {"buried-insight", "connection", "contradiction", "all"}
    if section_key not in valid:
        raise ValueError(f"section must be one of {sorted(valid)}, got {section!r}")
    top = max(1, min(10, int(top)))

    conn = _open()
    try:
        payload: dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "section": section_key,
            "track_record": track_record_to_dict(track_record(conn, days=90)),
            "findings": {},
        }
        if section_key in ("buried-insight", "all"):
            results = find_buried_insights(conn, vault_aware=vault_aware, top_n=top) or []
            payload["findings"]["buried_insight"] = [
                with_falsification(buried_insight_to_dict(r), "buried-insight", r)
                for r in results
            ]
            for r in results:
                record_finding(conn, "buried-insight", r)
        if section_key in ("connection", "all"):
            pairs = find_connections(conn, top_n=top) or []
            payload["findings"]["connection"] = [
                with_falsification(connection_to_dict(p), "connection", p)
                for p in pairs
            ]
            for p in pairs:
                record_finding(conn, "connection", p)
        if section_key in ("contradiction", "all"):
            pairs = find_contradictions(conn, top_n=top) or []
            payload["findings"]["contradiction"] = [
                with_falsification(contradiction_to_dict(p), "contradiction", p)
                for p in pairs
            ]
            for p in pairs:
                record_finding(conn, "contradiction", p)
        return payload
    finally:
        conn.close()


@mcp.tool(
    annotations={
        "title": "Surface Connections",
        "readOnlyHint": True,
        "openWorldHint": False,
        "idempotentHint": False,
    }
)
def basalt_connection(top: int = 3, min_sim: float = CONN_MIN_SIM) -> dict:
    """Surface latent connections — pairs of notes across different
    top-level folders that share an idea but have no wikilink between them.
    Read-only on the vault.

    Args:
        top: top N pairs (1-10).
        min_sim: cosine similarity floor (0.5-0.99). Default 0.78.
    """
    top = max(1, min(10, int(top)))
    min_sim = max(0.5, min(0.99, float(min_sim)))
    conn = _open()
    try:
        pairs = find_connections(conn, top_n=top, min_sim=min_sim) or []
        payload = {
            "schema": SCHEMA_VERSION,
            "verb": "connection",
            "min_sim": min_sim,
            "findings": [
                with_falsification(connection_to_dict(p), "connection", p)
                for p in pairs
            ],
        }
        for p in pairs:
            record_finding(conn, "connection", p)
        return payload
    finally:
        conn.close()


@mcp.tool(
    annotations={
        "title": "Surface Contradiction Candidates (v0 heuristic)",
        "readOnlyHint": True,
        "openWorldHint": False,
        "idempotentHint": False,
    }
)
def basalt_contradiction(top: int = 3, min_sim: float = CONT_MIN_SIM) -> dict:
    """Surface candidate contradictions — pairs of same-topic notes whose
    load-bearing sentences carry asymmetric negation, reversal markers, or
    polarity pairs (ship ↔ kill, works ↔ broken). v0 is heuristic; output
    is candidates, not verdicts. Read-only.

    Args:
        top: top N candidate pairs (1-10).
        min_sim: topical similarity floor (0.5-0.99). Default 0.72.
    """
    top = max(1, min(10, int(top)))
    min_sim = max(0.5, min(0.99, float(min_sim)))
    conn = _open()
    try:
        pairs = find_contradictions(conn, top_n=top, min_sim=min_sim) or []
        payload = {
            "schema": SCHEMA_VERSION,
            "verb": "contradiction",
            "version": "v0-heuristic",
            "min_sim": min_sim,
            "findings": [
                with_falsification(contradiction_to_dict(p), "contradiction", p)
                for p in pairs
            ],
        }
        for p in pairs:
            record_finding(conn, "contradiction", p)
        return payload
    finally:
        conn.close()


@mcp.tool(
    annotations={
        "title": "Audit Past Briefs",
        "readOnlyHint": False,    # mutates the briefs table to record verdicts
        "openWorldHint": False,
        "idempotentHint": True,
    }
)
def basalt_audit(days: int = 90) -> dict:
    """Re-evaluate pending Brief findings against the current vault state.

    For each pending brief in the calibration table, applies its
    falsification rules and updates status to 'confirmed' or 'falsified'
    where a rule fires. Returns the verdicts plus the track-record summary.

    This is the single feature that converts Basalt from "AI summary" into
    "research log." The longer it runs, the more valuable your track
    record becomes.

    Args:
        days: track-record window in days (7-730). Default 90.
    """
    days = max(7, min(730, int(days)))
    conn = _open()
    try:
        verdicts = audit_pending(conn)
        tr = track_record(conn, days=days)
        return {
            "schema": SCHEMA_VERSION,
            "verb": "audit",
            "verdicts": [audit_result_to_dict(a) for a in verdicts],
            "track_record": track_record_to_dict(tr),
        }
    finally:
        conn.close()


# ── Entry point ──────────────────────────────────────────────────

def main() -> None:
    """Entry point for the `basalt-mcp` script (declared in pyproject.toml).
    Resolves config from CLI args + env, then runs FastMCP over stdio."""
    parser = argparse.ArgumentParser(
        prog="basalt-mcp",
        description="Basalt MCP server — exposes the verb library over stdio.",
    )
    parser.add_argument(
        "--vault", type=Path, default=None,
        help=f"Path to vault root. Defaults to BASALT_VAULT env or {DEFAULT_VAULT}",
    )
    parser.add_argument(
        "--db", type=Path, default=None,
        help=f"Path to basalt.db. Defaults to BASALT_DB env or {DEFAULT_DB}",
    )
    args = parser.parse_args()
    _set_config(args.vault, args.db)

    # FastMCP.run() blocks on stdio. The MCP client (Claude Desktop, Cursor,
    # Cline) launches this process and speaks JSON-RPC over stdin/stdout.
    mcp.run()


if __name__ == "__main__":
    main()

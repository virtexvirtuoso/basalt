"""Basalt CLI."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.text import Text

from basalt.vault import walk_vault
from basalt.index import open_db, upsert_note, replace_links, resolve_link_targets
from basalt.embed import ensure_embeddings
from basalt.buried import find_buried_insight, find_buried_insights
from basalt.connection import find_connections, ConnectionPair, DEFAULT_MIN_SIM as CONN_MIN_SIM
from basalt.contradiction import find_contradictions, ContradictionPair, DEFAULT_MIN_SIM as CONT_MIN_SIM
from basalt.audit import (
    record_finding,
    render_falsification_lines,
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


# Detect non-interactive stdout — strip Rich color codes when piped.
# Maps directly onto cargo / ripgrep / fzf behavior. Lets `basalt brief | jq`
# work without ANSI escapes mangling the output even in --format=text mode.
_PLAIN_STDOUT = not sys.stdout.isatty()


app = typer.Typer(add_completion=False, no_args_is_help=True, help="Basalt — read your vault, surface what you believe.")
# Auto-disable color/style when piped — keeps `basalt brief | jq` clean.
console = Console(no_color=_PLAIN_STDOUT, force_terminal=False if _PLAIN_STDOUT else None)


def _emit_json(payload: dict) -> None:
    """Write a JSON document to stdout, no Rich formatting. Stable schema,
    safe to pipe into `jq` or consume from an MCP server."""
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    sys.stdout.write("\n")
    sys.stdout.flush()


DEFAULT_VAULT = Path.home() / "virtuoso-vault"
DEFAULT_DB    = Path.home() / ".basalt" / "basalt.db"
SAMPLE_VAULT  = Path(__file__).resolve().parent.parent.parent / "examples" / "sample-vault"
DEMO_DB       = Path.home() / ".basalt" / "demo.db"

# Section keys accepted by `basalt brief --section`
SECTIONS_AVAILABLE = {"buried-insight", "connection", "contradiction", "all"}
SECTIONS_SHIPPED   = ["buried-insight", "connection", "contradiction"]   # in render order
SECTIONS_PLANNED   = {"implicit-thesis": "needs claim ledger (Phase 1)",
                      "drift": "needs daily-note time-marker parser (Phase 1)"}


@app.command("index")
def cmd_index(
    vault: Path = typer.Option(DEFAULT_VAULT, "--vault", exists=True, file_okay=False, dir_okay=True),
    db: Path = typer.Option(DEFAULT_DB, "--db"),
    skip_embed: bool = typer.Option(False, "--skip-embed", help="Skip Ollama embedding step."),
    embed_model: str = typer.Option("nomic-embed-text", "--embed-model"),
):
    """Walk the vault, parse frontmatter, build link graph, embed."""
    t0 = time.time()
    console.print(f"[dim]Indexing[/dim] [bold]{vault}[/bold] [dim]→[/dim] [bold]{db}[/bold]")
    conn = open_db(db)

    n_notes = 0
    n_links = 0
    for note in walk_vault(vault):
        nid = upsert_note(conn, note)
        replace_links(conn, nid, note.wikilinks)
        n_notes += 1
        n_links += len(note.wikilinks)
        if n_notes % 200 == 0:
            console.print(f"  [dim]…parsed {n_notes} notes, {n_links} links[/dim]")
            conn.commit()
    conn.commit()

    resolved = resolve_link_targets(conn)
    conn.commit()
    console.print(f"  [green]✓[/green] {n_notes} notes · {n_links} links · {resolved} resolved to targets · {time.time()-t0:.1f}s")

    if not skip_embed:
        console.print(f"[dim]Embedding via Ollama[/dim] [bold]{embed_model}[/bold]…")
        t1 = time.time()
        computed, skipped = ensure_embeddings(
            conn, model=embed_model,
            on_progress=lambda msg: console.print(f"  [dim]{msg}[/dim]"),
        )
        console.print(f"  [green]✓[/green] {computed} embedded · {skipped} cached · {time.time()-t1:.1f}s")
    conn.close()


@app.command("brief")
def cmd_brief(
    db: Path = typer.Option(DEFAULT_DB, "--db", exists=True),
    section: str = typer.Option(
        "buried-insight", "--section",
        help=("Which Brief section to compute. One of: "
              + ", ".join(SECTIONS_SHIPPED) + ", all. "
              "Planned but unbuilt: " + ", ".join(SECTIONS_PLANNED) + "."),
    ),
    vault_aware: bool = typer.Option(True, "--vault-aware/--strict-defaults",
                                     help="Buried-Insight only — derive thresholds from vault age."),
    top: int = typer.Option(1, "--top", min=1, max=10,
                            help="Surface the top N findings per section (default 1)."),
    fmt: str = typer.Option("text", "--format", "-f",
                            help="Output format: 'text' (Rich-rendered, default) or 'json' (machine-readable)."),
):
    """Generate one or more Brief sections.

    The site advertises four unlocks: Implicit Thesis, Contradiction, Drift,
    Connection. Currently shipped: Buried Insight (a 5th, deeper unlock),
    Connection, and Contradiction (v0 heuristic). Implicit Thesis and Drift
    are planned for Phase 1.
    """
    section_key = section.strip().lower()
    if section_key in SECTIONS_PLANNED:
        raise typer.BadParameter(
            f"section '{section_key}' is not yet shipped — {SECTIONS_PLANNED[section_key]}.\n"
            f"Try one of: {', '.join(SECTIONS_SHIPPED)}, all"
        )
    if section_key not in SECTIONS_AVAILABLE:
        raise typer.BadParameter(
            f"unknown section '{section_key}'. "
            f"Try one of: {', '.join(SECTIONS_SHIPPED)}, all"
        )

    fmt_key = fmt.strip().lower()
    if fmt_key not in ("text", "json"):
        raise typer.BadParameter(f"unknown --format '{fmt}', try text or json")

    conn = open_db(db)
    try:
        # JSON path: collect findings, emit single JSON document, no Rich rendering.
        if fmt_key == "json":
            payload: dict = {
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
            _emit_json(payload)
            return

        # Track-record header — show the bar at the top of the Brief if any
        # past briefs exist. Establishes calibration as a first-class concept.
        _maybe_render_track_record(conn)

        if section_key in ("buried-insight", "all"):
            results = find_buried_insights(conn, vault_aware=vault_aware, top_n=top)
            if results:
                _render_buried_results(results)
                for r in results:
                    record_finding(conn, "buried-insight", r)
            elif section_key == "buried-insight":
                _say_no_result("No buried insight found.",
                               "The vault may be too young, too sparse, or too recently-edited everywhere.")
                raise typer.Exit(code=1)

        if section_key in ("connection", "all"):
            pairs = find_connections(conn, top_n=top)
            if pairs:
                _render_connections(pairs)
                for p in pairs:
                    record_finding(conn, "connection", p)
            elif section_key == "connection":
                _say_no_result("No latent connections found above the similarity floor.",
                               f"min similarity = {CONN_MIN_SIM:.2f}; raise --top or seed the vault more.")
                raise typer.Exit(code=1)

        if section_key in ("contradiction", "all"):
            pairs = find_contradictions(conn, top_n=top)
            if pairs:
                _render_contradictions(pairs)
                for p in pairs:
                    record_finding(conn, "contradiction", p)
            elif section_key == "contradiction":
                _say_no_result("No contradiction candidates found.",
                               "v0 is heuristic — surface text needs explicit reversal/negation markers.")
                raise typer.Exit(code=1)
    finally:
        conn.close()


def _say_no_result(headline: str, hint: str) -> None:
    console.print(f"[yellow]{headline}[/yellow]")
    console.print(f"[dim]{hint}[/dim]")


# ── Buried Insight rendering (existing) ─────────────────────────

def _render_buried_results(results: list) -> None:
    """Render one or more buried insights with a single header + threshold line."""
    if not results:
        return

    n = len(results)
    title = "THE BURIED INSIGHT" if n == 1 else f"BURIED INSIGHTS  ({n})"
    r0 = results[0]
    t = r0.thresholds

    console.print()
    console.rule(style="bright_black")
    console.print()
    console.print(Text(title, style="bold #D9824B"))
    console.print(Text("─────────────────────", style="#7A7269"))
    console.print(Text(
        f"vault age: {r0.vault_age_days}d  ·  thresholds: age≥{t['min_age_days']}d  "
        f"dormant≥{t['min_dormant_days']}d  recent≤{t['recent_window_days']}d",
        style="dim"))

    for i, r in enumerate(results, 1):
        if i > 1:
            console.print()
            console.print(Text("─────────────────────", style="#3A3A3A"))
        _render_buried_body(r, index=i if n > 1 else None)

    console.print()
    console.rule(style="bright_black")


def _render_buried_body(r, index: int | None = None) -> None:
    """Render a single buried-insight result body (no top-of-section chrome)."""
    c = r.candidate
    console.print()

    if index is not None:
        console.print(Text(f"{index:02}.", style="bold #D9824B"))

    created_str = c.created.strftime("%Y-%m-%d") if c.created else "?"
    console.print(Text.assemble(
        ("On ", "default"),
        (created_str, "bold"),
        (" you wrote, in ", "default"),
        (c.rel_path, "italic #C9C0B4"),
        (":", "default"),
    ))
    console.print()

    for line in _wrap(r.quote, 76):
        console.print(Text("  " + line, style="italic #EFE9E2"))
    console.print(Text(f"  ({r.quote_provenance})", style="dim"))
    console.print()

    n_explicit = sum(1 for v in r.validators if v.explicit_link)
    n_semantic = sum(1 for v in r.validators if not v.explicit_link)
    parts = []
    if n_explicit:
        parts.append(f"{n_explicit} note{'s' if n_explicit != 1 else ''} link{'s' if n_explicit == 1 else ''} back")
    if n_semantic:
        parts.append(f"{n_semantic} note{'s' if n_semantic != 1 else ''} validate{'' if n_semantic != 1 else 's'} it semantically")
    if not parts:
        parts.append("recent notes confirm")
    console.print(f"Since then, [bold]{', '.join(parts)}[/bold].")
    console.print(f"You haven't returned to this claim since you wrote it.")
    console.print()

    for v in r.validators:
        marker = "→" if v.explicit_link else "≈"
        sim = "" if v.explicit_link else f"  ({v.sim:.2f})"
        d   = v.updated.strftime("%Y-%m-%d") if v.updated else "?"
        console.print(Text(f"   {marker} {v.rel_path}  ({d}){sim}", style="dim"))
    console.print()

    _render_falsification("buried-insight", r)

    console.print(Text("   ▸ Promote to thesis     ▸ Open all     ▸ Snooze", style="#D9824B"))


# ── Connection rendering ────────────────────────────────────────

def _render_connections(pairs: list[ConnectionPair]) -> None:
    if not pairs:
        return
    n = len(pairs)
    title = "THE CONNECTION" if n == 1 else f"CONNECTIONS  ({n})"
    console.print()
    console.rule(style="bright_black")
    console.print()
    console.print(Text(title, style="bold #D9824B"))
    console.print(Text("─────────────────────", style="#7A7269"))
    console.print(Text(
        "two ideas in different folders that turn out to be the same idea",
        style="dim"))

    for i, p in enumerate(pairs, 1):
        if i > 1:
            console.print()
            console.print(Text("─────────────────────", style="#3A3A3A"))
        console.print()
        if n > 1:
            console.print(Text(f"{i:02}.", style="bold #D9824B"))
        console.print(Text.assemble(
            ("similarity ", "default"),
            (f"{p.similarity:.2f}", "bold"),
            ("  ·  no wikilink between them", "dim"),
        ))
        console.print()
        _render_pair_side("A", p.note_a_path, p.note_a_quote, p.note_a_quote_provenance)
        console.print()
        _render_pair_side("B", p.note_b_path, p.note_b_quote, p.note_b_quote_provenance)
        console.print()
        _render_falsification("connection", p)
        console.print(Text("   ▸ Link A ↔ B     ▸ Open both     ▸ Dismiss", style="#D9824B"))

    console.print()
    console.rule(style="bright_black")


# ── Contradiction rendering ─────────────────────────────────────

def _render_contradictions(pairs: list[ContradictionPair]) -> None:
    if not pairs:
        return
    n = len(pairs)
    title = "THE CONTRADICTION" if n == 1 else f"CONTRADICTIONS  ({n})"
    console.print()
    console.rule(style="bright_black")
    console.print()
    console.print(Text(title, style="bold #D9824B"))
    console.print(Text("─────────────────────", style="#7A7269"))
    console.print(Text(
        "two notes whose load-bearing claims appear to disagree  ·  v0 heuristic — verify before acting",
        style="dim"))

    for i, p in enumerate(pairs, 1):
        if i > 1:
            console.print()
            console.print(Text("─────────────────────", style="#3A3A3A"))
        console.print()
        if n > 1:
            console.print(Text(f"{i:02}.", style="bold #D9824B"))
        console.print(Text.assemble(
            ("topical similarity ", "default"),
            (f"{p.similarity:.2f}", "bold"),
            ("  ·  contradiction signals: ", "default"),
            (", ".join(p.signals) if p.signals else "—", "italic #C9C0B4"),
        ))
        console.print()
        _render_pair_side("A", p.note_a_path, p.note_a_quote, p.note_a_quote_provenance)
        console.print()
        _render_pair_side("B", p.note_b_path, p.note_b_quote, p.note_b_quote_provenance)
        console.print()
        _render_falsification("contradiction", p)
        console.print(Text("   ▸ Mark resolved     ▸ Open both     ▸ Dismiss as not-a-conflict", style="#D9824B"))

    console.print()
    console.rule(style="bright_black")


# ── Calibration: falsification rules + track record ─────────────

def _render_falsification(verb: str, finding) -> None:
    """Render the inline falsification rules for a single finding.
    Sets a verifiable expectation: this finding is wrong if you ever observe X."""
    lines = render_falsification_lines(verb, finding)
    if not lines:
        return
    console.print(Text("   ⊘ Falsification — this is wrong if:", style="#9A5C36"))
    for line in lines:
        wrapped = _wrap(line, 68)
        if not wrapped:
            continue
        console.print(Text(f"      • {wrapped[0]}", style="dim"))
        for cont in wrapped[1:]:
            console.print(Text(f"        {cont}", style="dim"))
    console.print()


def _maybe_render_track_record(conn) -> None:
    """If any past briefs exist, render a small track-record header at the
    top of the Brief — bar chart of confirmed/pending/falsified over 90 days."""
    tr = track_record(conn, days=90)
    if tr.total == 0:
        return
    bar_w = 36
    confirmed_w = round(bar_w * tr.confirmed / tr.total) if tr.total else 0
    falsified_w = round(bar_w * tr.falsified / tr.total) if tr.total else 0
    pending_w = bar_w - confirmed_w - falsified_w
    bar = Text()
    bar.append("  ")
    bar.append("▓" * confirmed_w, style="#5C8C5A")   # green-ish for confirmed
    bar.append("░" * pending_w, style="#7A7269")     # dim for pending
    bar.append("▓" * falsified_w, style="#9A5C36")   # amber for falsified
    bar.append(
        f"  {tr.confirmed} confirmed · {tr.pending} pending · {tr.falsified} falsified  ({tr.total} total)",
        style="dim",
    )
    console.print()
    console.print(Text("─── TRACK RECORD ─── (last 90d)", style="dim"))
    console.print(bar)
    console.print(Text("  Run `basalt audit` to re-evaluate pending briefs against the current vault.", style="dim"))
    console.print()


def _render_pair_side(label: str, rel_path: str, quote: str, provenance: str) -> None:
    console.print(Text.assemble(
        (f"  {label}  ", "bold #D9824B"),
        (rel_path, "italic #C9C0B4"),
    ))
    for line in _wrap(quote, 72):
        console.print(Text("     " + line, style="italic #EFE9E2"))
    console.print(Text(f"     ({provenance})", style="dim"))


def _wrap(text: str, width: int) -> list[str]:
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    if line:
        out.append(line)
    return out


# ── Convenience subcommands (thin wrappers around `brief --section`) ─

@app.command("connection")
def cmd_connection(
    db: Path = typer.Option(DEFAULT_DB, "--db", exists=True),
    top: int = typer.Option(3, "--top", min=1, max=10),
    min_sim: float = typer.Option(CONN_MIN_SIM, "--min-sim", min=0.5, max=0.99),
    fmt: str = typer.Option("text", "--format", "-f",
                            help="Output format: 'text' or 'json'."),
):
    """Surface latent connections — same idea written in different folders, no wikilink between them."""
    conn = open_db(db)
    pairs = find_connections(conn, top_n=top, min_sim=min_sim)
    conn.close()
    if fmt.strip().lower() == "json":
        _emit_json({
            "schema": SCHEMA_VERSION,
            "verb": "connection",
            "min_sim": min_sim,
            "findings": [with_falsification(connection_to_dict(p), "connection", p) for p in pairs or []],
        })
        return
    if not pairs:
        _say_no_result("No latent connections found above the similarity floor.",
                       f"min similarity = {min_sim:.2f}; lower it or seed the vault more.")
        raise typer.Exit(code=1)
    _render_connections(pairs)


@app.command("audit")
def cmd_audit(
    db: Path = typer.Option(DEFAULT_DB, "--db", exists=True),
    days: int = typer.Option(90, "--days", min=7, max=730,
                             help="Track-record window in days (default 90)."),
    fmt: str = typer.Option("text", "--format", "-f",
                            help="Output format: 'text' or 'json'."),
):
    """Re-evaluate pending briefs against the current vault state.

    Walks every brief still marked 'pending' in the calibration table, applies
    its falsification rules, and updates each to 'confirmed' or 'falsified'
    where the rule fires. Then prints the track-record summary.

    This is the single feature that converts Basalt from "AI summary tool"
    into "research log." Run it weekly — your track record compounds.
    """
    conn = open_db(db)
    try:
        results = audit_pending(conn)
        tr = track_record(conn, days=days)
    finally:
        conn.close()

    if fmt.strip().lower() == "json":
        _emit_json({
            "schema": SCHEMA_VERSION,
            "verb": "audit",
            "verdicts": [audit_result_to_dict(a) for a in results],
            "track_record": track_record_to_dict(tr),
        })
        return

    console.print()
    console.rule(style="bright_black")
    console.print()
    console.print(Text("AUDIT", style="bold #D9824B"))
    console.print(Text("─────────────────────", style="#7A7269"))

    if results:
        console.print(Text(f"{len(results)} brief{'s' if len(results) != 1 else ''} updated", style=""))
        console.print()
        for r in results:
            symbol = "✓" if r.new_status == "confirmed" else "✗"
            color  = "#5C8C5A" if r.new_status == "confirmed" else "#9A5C36"
            console.print(Text.assemble(
                (f"  {symbol} {r.new_status.upper():10} ", color),
                (f"{r.verb:18} ", "default"),
                (f"({r.age_days}d)  ", "dim"),
                (r.finding_key, "italic #C9C0B4"),
            ))
            for line in _wrap(r.reason, 76):
                console.print(Text(f"      {line}", style="dim"))
            console.print()
    else:
        console.print(Text("no pending briefs changed status", style="dim"))
        console.print()

    # Track-record bar
    bar_w = 40
    if tr.total:
        confirmed_w = round(bar_w * tr.confirmed / tr.total)
        falsified_w = round(bar_w * tr.falsified / tr.total)
        pending_w = bar_w - confirmed_w - falsified_w
        bar = Text()
        bar.append("  ")
        bar.append("▓" * confirmed_w, style="#5C8C5A")
        bar.append("░" * pending_w, style="#7A7269")
        bar.append("▓" * falsified_w, style="#9A5C36")
        console.print(Text(f"track record · last {tr.days}d", style="dim"))
        console.print(bar)
        console.print(Text(
            f"  {tr.confirmed} confirmed · {tr.pending} pending · {tr.falsified} falsified  "
            f"({tr.total} total · {tr.confirmed_pct:.0f}% confirmed · {tr.falsified_pct:.0f}% falsified)",
            style="dim"
        ))
    else:
        console.print(Text(f"no briefs in last {tr.days}d — run `basalt brief` to start tracking", style="dim"))

    console.print()
    console.rule(style="bright_black")


@app.command("contradiction")
def cmd_contradiction(
    db: Path = typer.Option(DEFAULT_DB, "--db", exists=True),
    top: int = typer.Option(3, "--top", min=1, max=10),
    min_sim: float = typer.Option(CONT_MIN_SIM, "--min-sim", min=0.5, max=0.99),
    fmt: str = typer.Option("text", "--format", "-f",
                            help="Output format: 'text' or 'json'."),
):
    """Surface candidate contradictions — pairs of same-topic notes with opposing surface markers (v0 heuristic)."""
    conn = open_db(db)
    pairs = find_contradictions(conn, top_n=top, min_sim=min_sim)
    conn.close()
    if fmt.strip().lower() == "json":
        _emit_json({
            "schema": SCHEMA_VERSION,
            "verb": "contradiction",
            "version": "v0-heuristic",
            "min_sim": min_sim,
            "findings": [with_falsification(contradiction_to_dict(p), "contradiction", p) for p in pairs or []],
        })
        return
    if not pairs:
        _say_no_result("No contradiction candidates found.",
                       "v0 is heuristic — surface text needs explicit reversal/negation markers.")
        raise typer.Exit(code=1)
    _render_contradictions(pairs)


@app.command("demo")
def cmd_demo(
    embed_model: str = typer.Option("nomic-embed-text", "--embed-model"),
    top: int = typer.Option(1, "--top", min=1, max=5),
    section: str = typer.Option(
        "buried-insight", "--section",
        help="Which section to demo. One of: " + ", ".join(SECTIONS_SHIPPED) + ", all.",
    ),
):
    """Index the bundled sample vault and run a Brief — no setup, no vault needed.

    Useful for trying Basalt before you point it at your own vault. The sample
    vault is a 14-note synthetic corpus with one genuine buried insight built in.
    """
    if not SAMPLE_VAULT.exists():
        console.print(f"[red]Sample vault not found at {SAMPLE_VAULT}[/red]")
        console.print("[dim]Reinstall the package or check the source tree.[/dim]")
        raise typer.Exit(code=1)

    console.print(f"[dim]Demo mode — using bundled sample vault[/dim] [bold]{SAMPLE_VAULT}[/bold]")
    # Always rebuild the demo DB so the demo is reproducible
    if DEMO_DB.exists():
        DEMO_DB.unlink()
    conn = open_db(DEMO_DB)

    n_notes = 0
    n_links = 0
    for note in walk_vault(SAMPLE_VAULT):
        nid = upsert_note(conn, note)
        replace_links(conn, nid, note.wikilinks)
        n_notes += 1
        n_links += len(note.wikilinks)
    conn.commit()
    resolved = resolve_link_targets(conn)
    conn.commit()
    console.print(f"  [green]✓[/green] {n_notes} notes · {n_links} links · {resolved} resolved")

    console.print(f"[dim]Embedding via Ollama[/dim] [bold]{embed_model}[/bold]…")
    computed, skipped = ensure_embeddings(
        conn, model=embed_model,
        on_progress=lambda msg: console.print(f"  [dim]{msg}[/dim]"),
    )
    console.print(f"  [green]✓[/green] {computed} embedded · {skipped} cached")
    conn.close()

    # Now run the requested brief section(s) against the demo DB
    section_key = section.strip().lower()
    conn = open_db(DEMO_DB)
    try:
        any_rendered = False
        if section_key in ("buried-insight", "all"):
            results = find_buried_insights(conn, vault_aware=True, top_n=top)
            if results:
                _render_buried_results(results)
                any_rendered = True
        if section_key in ("connection", "all"):
            pairs = find_connections(conn, top_n=top)
            if pairs:
                _render_connections(pairs)
                any_rendered = True
        if section_key in ("contradiction", "all"):
            pairs = find_contradictions(conn, top_n=top)
            if pairs:
                _render_contradictions(pairs)
                any_rendered = True
        if not any_rendered:
            console.print("[yellow]No findings on the sample vault for the requested section(s).[/yellow]")
            raise typer.Exit(code=1)
    finally:
        conn.close()


if __name__ == "__main__":
    app()

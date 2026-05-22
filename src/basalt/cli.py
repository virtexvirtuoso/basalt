"""Basalt CLI."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from basalt.vault import walk_vault
from basalt.index import open_db, upsert_note, replace_links, resolve_link_targets
from basalt.embed import ensure_embeddings
from basalt.buried import find_buried_insight, find_buried_insights
from basalt.connection import find_connections, ConnectionPair, DEFAULT_MIN_SIM as CONN_MIN_SIM
from basalt.contradiction import find_contradictions, ContradictionPair, DEFAULT_MIN_SIM as CONT_MIN_SIM
from basalt.implicit_thesis import find_implicit_theses, ThesisCluster, DEFAULT_MIN_SIM as THESIS_MIN_SIM, ImplicitThesisVerb
from basalt.drift import find_drift, DriftFinding, DEFAULT_WINDOW_DAYS as DRIFT_WINDOW_DAYS
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
    implicit_thesis_to_dict,
    drift_to_dict,
    audit_result_to_dict,
    track_record_to_dict,
    with_falsification,
)
from basalt.wizard import run_wizard, WizardAborted, CONFIG_PATH


# Detect non-interactive stdout — strip Rich color codes when piped.
# Maps directly onto cargo / ripgrep / fzf behavior. Lets `basalt brief | jq`
# work without ANSI escapes mangling the output even in --format=text mode.
_PLAIN_STDOUT = not sys.stdout.isatty()


# ── Brand chrome ────────────────────────────────────────────────
# Basalt formation — top-down view of a hexagonal tessellation in a
# 3-4-5-4-3 diamond. Real basalt formations (Giant's Causeway,
# Devils Postpile, Fingal's Cave) viewed from above tessellate this
# way. Wordmark anchors to the widest row (5 hexagons); two taglines
# beneath. Rows 1-2 frame the wordmark with the natural hex pattern.
#
# All text columns aligned at column 18.
# Suppressed when piped (NO_COLOR / non-tty) and on JSON output.

BANNER_LINES = (
    "       [#D9824B]\u2b21 \u2b21 \u2b21[/]",
    "      [#D9824B]\u2b21 \u2b21 \u2b21 \u2b21[/]",
    "     [#D9824B]\u2b21 \u2b21 \u2b21 \u2b21 \u2b21[/]    [#EFE9E2]Basalt[/][bold #D9824B].[/]",
    "      [#D9824B]\u2b21 \u2b21 \u2b21 \u2b21[/]     [dim]reads your vault. surfaces what you believe but never wrote down.[/]",
    "       [#D9824B]\u2b21 \u2b21 \u2b21[/]       [#D9824B]\u00b7[/] [dim italic]five verbs. one brief. never writes back.[/]",
)


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Basalt. Reads your vault and surfaces what you believe but never wrote down. Standalone, read-only, local-first.",
)
# Auto-disable color/style when piped — keeps `basalt brief | jq` clean.
console = Console(no_color=_PLAIN_STDOUT, force_terminal=False if _PLAIN_STDOUT else None)


def _emit_json(payload: dict) -> None:
    """Write a JSON document to stdout, no Rich formatting. Stable schema,
    safe to pipe into `jq` or consume from an MCP server."""
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _print_banner() -> None:
    """Print the basalt mark + wordmark once, at the top of a standalone run.
    Suppressed when piped or in JSON mode — keeps `basalt brief | jq` clean."""
    if _PLAIN_STDOUT:
        return
    console.print()
    for line in BANNER_LINES:
        console.print(line)
    console.print()


def _print_signoff(verb: str) -> None:
    """Single-line footer that closes a brief or audit run.
    Replaces the bare console.rule() — adds a fragment of voice without chrome."""
    if _PLAIN_STDOUT:
        return
    console.print()
    console.print(
        "[#7A7269]⎯⎯⎯[/]  "
        f"[dim italic]end of {verb}.[/]  "
        "[dim]the vault keeps the receipts.[/]"
    )


def _maybe_first_run_greeting(conn) -> None:
    """Print a single welcome the first time `basalt index` builds a DB.
    Stored in the `meta` table — never repeats, even across machines if
    the DB is copied. Suppressed when piped."""
    if _PLAIN_STDOUT:
        return
    cur = conn.execute("SELECT value FROM meta WHERE key = 'first_run_seen'")
    row = cur.fetchone()
    if row is not None:
        return
    console.print()
    console.print("  [#D9824B]\u2b21[/]  [#EFE9E2]First index.[/]")
    console.print("  [dim]Basalt will not write to your vault. It reads, indexes, and waits.[/]")
    console.print("  [dim]When this finishes, run `basalt brief` to see what's been sitting there.[/]")
    console.print()
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('first_run_seen', ?)",
        (str(int(time.time())),),
    )
    conn.commit()


DEFAULT_VAULT = Path.home() / "virtuoso-vault"
DEFAULT_DB    = Path.home() / ".basalt" / "basalt.db"
SAMPLE_VAULT  = Path(__file__).resolve().parent.parent.parent / "examples" / "sample-vault"
DEMO_DB       = Path.home() / ".basalt" / "demo.db"

# Section keys accepted by `basalt brief --section`
# 4-of-4 site-advertised unlocks now ship; SECTIONS_PLANNED is empty until v1 verbs.
SECTIONS_AVAILABLE = {"buried-insight", "connection", "contradiction", "implicit-thesis", "drift", "all"}
SECTIONS_SHIPPED   = ["buried-insight", "connection", "contradiction", "implicit-thesis", "drift"]  # render order
SECTIONS_PLANNED: dict[str, str] = {}


# ── Runtime config resolution ───────────────────────────────────
# Precedence: CLI flag > env var > config file > hardcoded default.
# Honors the contract that `basalt init` is not theater — its writes are read.

def _runtime_defaults() -> dict:
    """Resolve the effective defaults for vault, db, ollama_url, embed_model.
    Loads ~/.config/basalt/config.toml if present. Env vars override config.
    Warnings about partial config are surfaced via console.print()."""
    from basalt import wizard
    cfg = wizard.load_config(
        on_warning=lambda key: console.print(
            f"  [yellow]·[/yellow] [dim]config is missing `{key}` — using default[/dim]"
        ),
    )
    return {
        "vault": (
            Path(os.environ["BASALT_VAULT"]).expanduser()
            if os.environ.get("BASALT_VAULT")
            else (cfg.vault_path if cfg else DEFAULT_VAULT)
        ),
        "ollama_url": (
            os.environ.get("BASALT_OLLAMA_URL")
            or (cfg.ollama_url if cfg else wizard.DEFAULT_OLLAMA_URL)
        ),
        "embed_model": (
            os.environ.get("BASALT_EMBED_MODEL")
            or (cfg.embed_model if cfg else wizard.DEFAULT_EMBED_MODEL)
        ),
    }


def _require_initialized(db: Path) -> None:
    """Cold-path guard for read-only commands. If neither config nor DB exists,
    print one helpful line and exit 2 instead of letting Typer surface a stack."""
    if db.exists():
        return
    from basalt import wizard
    if wizard.load_config() is not None:
        # Config exists but DB doesn't — user ran init, hasn't indexed yet.
        console.print(
            f"[dim]no index yet at[/] [bold]{db}[/] [dim]— run[/] [bold]basalt index[/]"
        )
    else:
        # Neither exists. First-run cold path.
        console.print(
            f"[dim]no config found — run[/] [bold]basalt init[/] [dim](or[/] [bold]basalt init --index[/][dim])[/]"
        )
    raise typer.Exit(code=2)


@app.command("init")
def cmd_init(
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Accept all defaults non-interactively. Honors BASALT_VAULT / BASALT_OLLAMA_URL / BASALT_EMBED_MODEL env vars.",
    ),
    no_input: bool = typer.Option(
        False, "--no-input",
        help="Fail rather than prompt. Useful in CI; requires BASALT_VAULT (or existing config) to be set.",
    ),
    index_now: bool = typer.Option(
        False, "--index",
        help="Run `basalt index` immediately after writing the config.",
    ),
):
    """Interactive first-run setup. Writes ~/.config/basalt/config.toml."""
    _print_banner()
    try:
        cfg = run_wizard(yes=yes, no_input=no_input, console=console)
    except WizardAborted as e:
        reason = str(e)
        console.print()
        if reason in ("interrupted", "declined"):
            console.print(
                "  [dim]Stopped. Nothing written. Run[/] [bold]basalt init[/] [dim]when you're ready.[/]"
            )
        else:
            # Real error (bad vault path, too many invalid answers, etc.) — surface the reason.
            console.print(f"  [red]·[/red] [dim]{reason}[/]")
        raise typer.Exit(code=1)

    if index_now:
        console.print()
        cmd_index(
            vault=cfg.vault_path,
            db=DEFAULT_DB,
            skip_embed=False,
            embed_model=cfg.embed_model,
            ollama_url=cfg.ollama_url,
        )


@app.command("index")
def cmd_index(
    vault: Path = typer.Option(None, "--vault", help="Vault path. Defaults to config or ~/virtuoso-vault."),
    db: Path = typer.Option(DEFAULT_DB, "--db"),
    skip_embed: bool = typer.Option(False, "--skip-embed", help="Skip Ollama embedding step."),
    embed_model: str = typer.Option(None, "--embed-model", help="Embedding model. Defaults to config."),
    ollama_url: str = typer.Option(None, "--ollama-url", help="Ollama base URL. Defaults to config."),
):
    """Walk the vault, parse frontmatter, build link graph, embed."""
    defaults = _runtime_defaults()
    vault = (vault or defaults["vault"]).expanduser().absolute()
    embed_model = embed_model or defaults["embed_model"]
    ollama_url = ollama_url or defaults["ollama_url"]
    if not vault.is_dir():
        console.print(f"[red]✗[/red] Vault not found: [bold]{vault}[/bold]")
        console.print(f"[dim]Run[/] [bold]basalt init[/] [dim]or pass[/] [bold]--vault[/]")
        raise typer.Exit(code=2)

    t0 = time.time()
    conn = open_db(db)
    _maybe_first_run_greeting(conn)
    console.print(f"[dim]Indexing[/dim] [bold]{vault}[/bold] [dim]→[/dim] [bold]{db}[/bold]")

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
        console.print(f"[dim]Embedding via Ollama[/dim] [bold]{embed_model}[/bold] [dim]at[/] [bold]{ollama_url}[/]…")
        t1 = time.time()
        computed, skipped = ensure_embeddings(
            conn, model=embed_model,
            on_progress=lambda msg: console.print(f"  [dim]{msg}[/dim]"),
            ollama_url=ollama_url,
        )
        console.print(f"  [green]✓[/green] {computed} embedded · {skipped} cached · {time.time()-t1:.1f}s")
    conn.close()


@app.command("brief")
def cmd_brief(
    db: Path = typer.Option(DEFAULT_DB, "--db"),
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
    _require_initialized(db)
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

    if fmt_key != "json":
        _print_banner()
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
            if section_key in ("implicit-thesis", "all"):
                clusters = find_implicit_theses(conn, top_n=top) or []
                payload["findings"]["implicit_thesis"] = [
                    with_falsification(implicit_thesis_to_dict(c), "implicit-thesis", c)
                    for c in clusters
                ]
                for c in clusters:
                    record_finding(conn, "implicit-thesis", c)
            if section_key in ("drift", "all"):
                drifts = find_drift(conn, top_n=max(1, top)) or []
                payload["findings"]["drift"] = [
                    with_falsification(drift_to_dict(d), "drift", d)
                    for d in drifts
                ]
                for d in drifts:
                    record_finding(conn, "drift", d)
            _emit_json(payload)
            return

        # Track-record header — show the bar at the top of the Brief if any
        # past briefs exist. Establishes calibration as a first-class concept.
        _maybe_render_track_record(conn)

        # Phase E (2026-05-22, v0.0.16): promote Drift to the top of the Brief
        # when |max delta| > 5pp. Drift is the highest signal-to-noise section
        # per the dogfood findings; when meaningful it leads.
        DRIFT_PROMOTION_THRESHOLD_PP = 5.0
        drift_rendered_early = False
        if section_key == "all":
            early_drifts = find_drift(conn, top_n=max(1, top)) or []
            if early_drifts and max(
                (getattr(d, "score", 0.0) for d in early_drifts), default=0.0
            ) > DRIFT_PROMOTION_THRESHOLD_PP:
                _render_drift(early_drifts)
                for d in early_drifts:
                    record_finding(conn, "drift", d)
                drift_rendered_early = True

        if section_key in ("buried-insight", "all"):
            results = find_buried_insights(conn, vault_aware=vault_aware, top_n=top)
            if results:
                _render_buried_results(results)
                for r in results:
                    record_finding(conn, "buried-insight", r)
            elif section_key == "buried-insight":
                _say_no_result(
                    "Nothing buried — yet.",
                    "Either the vault is too young, too sparse, or you've already revisited everything you wrote. Come back in a week.",
                )
                raise typer.Exit(code=1)

        if section_key in ("connection", "all"):
            pairs = find_connections(conn, top_n=top)
            if pairs:
                _render_connections(pairs)
                for p in pairs:
                    record_finding(conn, "connection", p)
            elif section_key == "connection":
                _say_no_result(
                    "No latent connections crossed the similarity floor.",
                    f"floor = {CONN_MIN_SIM:.2f}. Raise --top, lower --min-sim, or write more before asking again.",
                )
                raise typer.Exit(code=1)

        if section_key in ("contradiction", "all"):
            pairs = find_contradictions(conn, top_n=top)
            if pairs:
                _render_contradictions(pairs)
                for p in pairs:
                    record_finding(conn, "contradiction", p)
            elif section_key == "contradiction":
                _say_no_result(
                    "No contradictions surfaced.",
                    "v0 is heuristic and looks for explicit reversal markers. Absence is not evidence — it just means nothing is wearing a sign.",
                )
                raise typer.Exit(code=1)

        if section_key in ("implicit-thesis", "all"):
            clusters = find_implicit_theses(conn, top_n=top)
            if clusters:
                _render_implicit_theses(clusters)
                for c in clusters:
                    record_finding(conn, "implicit-thesis", c)
            elif section_key == "implicit-thesis":
                _say_no_result(
                    "No implicit thesis surfaced.",
                    "v0 looks for clusters of 3+ topically-convergent notes spanning ≥2 folders or ≥30 days. Either you're not converging yet, or your through-lines are too narrow to cluster.",
                )
                raise typer.Exit(code=1)

        if section_key in ("drift", "all") and not drift_rendered_early:
            drifts = find_drift(conn, top_n=max(1, top))
            if drifts:
                _render_drift(drifts)
                for d in drifts:
                    record_finding(conn, "drift", d)
            elif section_key == "drift":
                _say_no_result(
                    "No drift surfaced.",
                    f"v0 needs ≥2 projects under `02-Projects/` and ≥3 dated daily notes in the last {DRIFT_WINDOW_DAYS}d. Add daily notes that mention project names — drift becomes legible from the second week.",
                )
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

    _print_signoff("brief")


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


def render_buried_from_db(db_path, *, console=None) -> bool:
    """Open a Basalt DB, find the top buried insight, render it.

    Used by both `basalt brief` and the wizard's first-run preview.
    Returns True if a result was rendered, False if no insights were found
    or the DB couldn't be opened. Never raises.
    """
    from basalt.index import open_db
    try:
        conn = open_db(db_path)
    except Exception:
        return False
    try:
        results = find_buried_insights(conn, vault_aware=True, top_n=1)
        if not results:
            return False
        _render_buried_results(results)
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


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

    _print_signoff("brief")


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

    _print_signoff("brief")


# ── Implicit Thesis rendering ───────────────────────────────────

def _render_implicit_theses(clusters: list[ThesisCluster]) -> None:
    if not clusters:
        return
    n = len(clusters)
    title = "THE IMPLICIT THESIS" if n == 1 else f"IMPLICIT THESES  ({n})"
    console.print()
    console.rule(style="bright_black")
    console.print()
    console.print(Text(title, style="bold #D9824B"))
    console.print(Text("─────────────────────", style="#7A7269"))
    console.print(Text(
        "the through-line you keep saying without realizing  ·  v0 cluster — proxy thesis is the centroid quote",
        style="dim"))

    for i, c in enumerate(clusters, 1):
        if i > 1:
            console.print()
            console.print(Text("─────────────────────", style="#3A3A3A"))
        console.print()
        if n > 1:
            console.print(Text(f"{i:02}.", style="bold #D9824B"))
        console.print(Text.assemble(
            ("cluster size ", "default"),
            (f"{c.cluster_size}", "bold"),
            ("  ·  folders ", "default"),
            (f"{c.folder_diversity}", "bold"),
            ("  ·  span ", "default"),
            (f"{c.span_days}d", "bold"),
            ("  ·  mean similarity ", "default"),
            (f"{c.mean_similarity:.2f}", "bold"),
        ))
        console.print()
        console.print(Text("  ▸ The proxy thesis (centroid)", style="#D9824B"))
        console.print(Text(f"    {c.centroid_path}", style="italic #C9C0B4"))
        for line in _wrap(c.centroid_quote, 72):
            console.print(Text("       " + line, style="italic #EFE9E2"))
        console.print(Text(f"       ({c.centroid_quote_provenance})", style="dim"))
        console.print()
        console.print(Text("  ▸ The rephrasings (other cluster members)", style="dim"))
        # Skip the centroid in the rephrasings list
        for path, quote, prov in zip(c.member_paths, c.member_quotes, c.member_quote_provenances):
            if path == c.centroid_path:
                continue
            console.print(Text(f"    · {path}", style="italic #C9C0B4"))
            for line in _wrap(quote, 70):
                console.print(Text("       " + line, style="dim"))
            console.print(Text(f"       ({prov})", style="dim"))
        console.print()
        _render_falsification("implicit-thesis", c)
        console.print(Text("   ▸ Name the thesis     ▸ Open all     ▸ Dismiss as coincidence", style="#D9824B"))

    _print_signoff("brief")


# ── Drift rendering ─────────────────────────────────────────────

def _render_drift(drifts: list[DriftFinding]) -> None:
    if not drifts:
        return
    n = len(drifts)
    title = "THE DRIFT" if n == 1 else f"DRIFT  ({n})"
    console.print()
    console.rule(style="bright_black")
    console.print()
    console.print(Text(title, style="bold #D9824B"))
    console.print(Text("─────────────────────", style="#7A7269"))
    console.print(Text(
        "what you say is the priority versus what you actually spent the week on",
        style="dim"))

    for i, d in enumerate(drifts, 1):
        if i > 1:
            console.print()
            console.print(Text("─────────────────────", style="#3A3A3A"))
        console.print()
        if n > 1:
            console.print(Text(f"{i:02}.", style="bold #D9824B"))
        console.print(Text.assemble(
            (f"window {d.window_days}d  ·  ", "default"),
            (f"{d.daily_note_count}", "bold"),
            (" daily notes  ·  ", "default"),
            (f"{d.project_count}", "bold"),
            (" projects  ·  ", "default"),
            (f"{d.total_mentions}", "bold"),
            (" total mentions", "default"),
        ))
        console.print()

        # Stated vs lived top-3 each
        stated_top = sorted(d.shares, key=lambda s: -s.stated_share)[:3]
        lived_top  = sorted(d.shares, key=lambda s: -s.lived_share)[:3]
        console.print(Text("  Stated", style="dim"))
        for s in stated_top:
            console.print(Text(
                f"    {s.stated_rank:>2}. {s.name:30}  {s.stated_notes:>3} notes  ({s.stated_share*100:5.1f}%)",
                style="default" if s.stated_rank == 1 else "dim",
            ))
        console.print()
        console.print(Text("  Lived", style="dim"))
        for s in lived_top:
            console.print(Text(
                f"    {s.lived_rank:>2}. {s.name:30}  {s.lived_mentions:>3} mentions  ({s.lived_share*100:5.1f}%)",
                style="default" if s.lived_rank == 1 else "dim",
            ))
        console.print()

        # Headline drift narrative
        if d.headline_overworked or d.headline_underworked:
            console.print(Text("  The drift", style="#D9824B"))
            if d.headline_underworked:
                u = d.headline_underworked
                console.print(Text(
                    f"    {u.name} is your stated #{u.stated_rank} but lived #{u.lived_rank}  "
                    f"({u.drift_pct:+.1f}pp)",
                    style="default",
                ))
            if d.headline_overworked:
                o = d.headline_overworked
                console.print(Text(
                    f"    {o.name} is your stated #{o.stated_rank} but lived #{o.lived_rank}  "
                    f"({o.drift_pct:+.1f}pp)",
                    style="default",
                ))
        console.print()

        _render_falsification("drift", d)
        console.print(Text("   ▸ Re-rank priorities     ▸ Open daily notes     ▸ Snooze a week", style="#D9824B"))

    _print_signoff("brief")


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
    db: Path = typer.Option(DEFAULT_DB, "--db"),
    top: int = typer.Option(3, "--top", min=1, max=10),
    min_sim: float = typer.Option(CONN_MIN_SIM, "--min-sim", min=0.5, max=0.99),
    fmt: str = typer.Option("text", "--format", "-f",
                            help="Output format: 'text' or 'json'."),
):
    """Surface latent connections — same idea written in different folders, no wikilink between them."""
    _require_initialized(db)
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
    db: Path = typer.Option(DEFAULT_DB, "--db"),
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
    _require_initialized(db)
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

    _print_banner()
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
        console.print(Text("nothing changed status — the record stands.", style="dim italic"))
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

    _print_signoff("audit")


@app.command("drift")
def cmd_drift(
    db: Path = typer.Option(DEFAULT_DB, "--db"),
    days: int = typer.Option(DRIFT_WINDOW_DAYS, "--days", min=7, max=365,
                             help="Window for daily-note mentions (default 30)."),
    fmt: str = typer.Option("text", "--format", "-f", help="Output format: 'text' or 'json'."),
):
    """Surface drift — projects whose lived priority (daily-note mentions) diverges from stated priority (project-folder structure)."""
    _require_initialized(db)
    conn = open_db(db)
    drifts = find_drift(conn, window_days=days, top_n=1)
    if fmt.strip().lower() == "json":
        conn.close()
        _emit_json({
            "schema": SCHEMA_VERSION,
            "verb": "drift",
            "version": "v0",
            "window_days": days,
            "findings": [with_falsification(drift_to_dict(d), "drift", d) for d in drifts or []],
        })
        return
    if not drifts:
        conn.close()
        _say_no_result(
            "No drift surfaced.",
            f"v0 needs ≥2 projects under `02-Projects/` and ≥3 dated daily notes in the last {days}d.",
        )
        raise typer.Exit(code=1)
    _print_banner()
    _render_drift(drifts)
    for d in drifts:
        record_finding(conn, "drift", d)
    conn.close()


@app.command("thesis")
def cmd_thesis(
    db: Path = typer.Option(DEFAULT_DB, "--db"),
    top: int = typer.Option(3, "--top", min=1, max=10),
    min_sim: float = typer.Option(THESIS_MIN_SIM, "--min-sim", min=0.5, max=0.99),
    fmt: str = typer.Option("text", "--format", "-f", help="Output format: 'text' or 'json'."),
):
    """Surface implicit theses — clusters of notes that converge on an unnamed through-line (v0 cluster heuristic)."""
    _require_initialized(db)
    conn = open_db(db)
    clusters = find_implicit_theses(conn, top_n=top, min_sim=min_sim)
    if fmt.strip().lower() == "json":
        conn.close()
        _emit_json({
            "schema": SCHEMA_VERSION,
            "verb": "implicit-thesis",
            "version": "v0-cluster",
            "min_sim": min_sim,
            "findings": [with_falsification(implicit_thesis_to_dict(c), "implicit-thesis", c) for c in clusters or []],
        })
        return
    if not clusters:
        conn.close()
        _say_no_result(
            "No implicit thesis surfaced.",
            "v0 needs ≥3 topically-convergent notes across ≥2 folders or ≥30 days.",
        )
        raise typer.Exit(code=1)
    _print_banner()
    _render_implicit_theses(clusters)
    for c in clusters:
        record_finding(conn, "implicit-thesis", c)
    conn.close()


@app.command("contradiction")
def cmd_contradiction(
    db: Path = typer.Option(DEFAULT_DB, "--db"),
    top: int = typer.Option(3, "--top", min=1, max=10),
    min_sim: float = typer.Option(CONT_MIN_SIM, "--min-sim", min=0.5, max=0.99),
    fmt: str = typer.Option("text", "--format", "-f",
                            help="Output format: 'text' or 'json'."),
):
    """Surface candidate contradictions — pairs of same-topic notes with opposing surface markers (v0 heuristic)."""
    _require_initialized(db)
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


@app.command("config")
def cmd_config(
    action: str = typer.Argument("show", help="show: print resolved config. path: print config file location."),
):
    """Inspect resolved Basalt config (precedence: env > file > defaults)."""
    from basalt import wizard
    if action == "path":
        sys.stdout.write(str(wizard.CONFIG_PATH) + "\n")
        return
    if action != "show":
        console.print(f"[red]✗[/red] unknown action '{action}' — try [bold]show[/bold] or [bold]path[/bold]")
        raise typer.Exit(code=2)

    cfg = wizard.load_config()
    defaults = _runtime_defaults()
    has_file = cfg is not None

    from rich.table import Table as RTable
    t = RTable.grid(padding=(0, 2))
    t.add_column(style="dim")
    t.add_column()
    t.add_column(style="dim")
    t.add_row("vault",   str(defaults["vault"]),       "(env)" if os.environ.get("BASALT_VAULT") else ("(file)" if has_file else "(default)"))
    t.add_row("ollama",  defaults["ollama_url"],        "(env)" if os.environ.get("BASALT_OLLAMA_URL") else ("(file)" if has_file else "(default)"))
    t.add_row("model",   defaults["embed_model"],       "(env)" if os.environ.get("BASALT_EMBED_MODEL") else ("(file)" if has_file else "(default)"))
    t.add_row("config",  str(wizard.CONFIG_PATH),       "(present)" if has_file else "(none)")
    t.add_row("db",      str(DEFAULT_DB),               "(present)" if DEFAULT_DB.exists() else "(not yet built)")
    console.print()
    console.print(Panel(t, title="[#D9824B]⬡[/] [bold]basalt config[/]", border_style="#5A5048", padding=(0, 2)))


@app.command("doctor")
def cmd_doctor():
    """Quick health check — config, vault, ollama, db."""
    from basalt import wizard
    defaults = _runtime_defaults()
    vault = defaults["vault"]
    ollama_url = defaults["ollama_url"]
    embed_model = defaults["embed_model"]

    cfg = wizard.load_config()
    reachable, installed = wizard.ollama_status(ollama_url)
    vault_ok = vault.is_dir()
    db_ok = DEFAULT_DB.exists()

    def line(ok: bool, label: str, detail: str = "") -> str:
        mark = "[green]✓[/green]" if ok else "[yellow]·[/yellow]"
        body = f"  {mark} [#EFE9E2]{label}[/]"
        if detail:
            body += f" [dim]{detail}[/]"
        return body

    console.print()
    console.print(line(cfg is not None, "config", f"{wizard.CONFIG_PATH}" if cfg else "missing — run `basalt init`"))
    console.print(line(vault_ok, "vault", f"{vault}"))
    console.print(line(reachable, "ollama", f"{ollama_url} · {len(installed)} models" if reachable else f"{ollama_url} (not answering)"))
    if reachable:
        model_ok = embed_model in installed
        console.print(line(model_ok, "model", f"{embed_model}" + ("" if model_ok else f" — pull with: ollama pull {embed_model}")))
    else:
        console.print(line(False, "model", f"{embed_model} (can't check — ollama down)"))
    console.print(line(db_ok, "index", f"{DEFAULT_DB}" if db_ok else "not built — run `basalt index`"))
    console.print()

    all_ok = (cfg is not None) and vault_ok and reachable and db_ok
    if not all_ok:
        raise typer.Exit(code=1)


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

    _print_banner()
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
        if section_key in ("implicit-thesis", "all"):
            clusters = find_implicit_theses(conn, top_n=top)
            if clusters:
                _render_implicit_theses(clusters)
                any_rendered = True
        if section_key in ("drift", "all"):
            drifts = find_drift(conn, top_n=1)
            if drifts:
                _render_drift(drifts)
                any_rendered = True
        if not any_rendered:
            console.print("[yellow]No findings on the sample vault for the requested section(s).[/yellow]")
            raise typer.Exit(code=1)
    finally:
        conn.close()


@app.command("about")
def cmd_about() -> None:
    """What Basalt is, in fewer words than the README."""
    if _PLAIN_STDOUT:
        # Plain-text mode for `basalt about | mail -s ...`
        console.print("Basalt — reads your vault. surfaces what you believe but never wrote down.")
        console.print("Standalone. Read-only. Local-first.")
        console.print(f"schema {SCHEMA_VERSION}")
        return
    _print_banner()
    console.print("  [#EFE9E2]Three commands. Sixty seconds. Runs on your laptop.[/]")
    console.print()
    console.print("  [dim]Basalt forms in slow cooling — hexagonal columns, brittle to[/]")
    console.print("  [dim]impact, durable to weather. The vault is the same.[/]")
    console.print()
    console.print(
        f"  [#7A7269]schema {SCHEMA_VERSION}[/]   "
        "[#7A7269]·[/]   "
        "[#7A7269]standalone · read-only · local-first[/]"
    )
    console.print()


if __name__ == "__main__":
    app()

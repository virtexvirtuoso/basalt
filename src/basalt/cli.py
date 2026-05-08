"""Basalt CLI."""

from __future__ import annotations

import time
from pathlib import Path

import typer
from rich.console import Console
from rich.text import Text

from basalt.vault import walk_vault
from basalt.index import open_db, upsert_note, replace_links, resolve_link_targets
from basalt.embed import ensure_embeddings
from basalt.buried import find_buried_insight, find_buried_insights


app = typer.Typer(add_completion=False, no_args_is_help=True, help="Basalt — read your vault, surface what you believe.")
console = Console()


DEFAULT_VAULT = Path.home() / "virtuoso-vault"
DEFAULT_DB    = Path.home() / ".basalt" / "basalt.db"
SAMPLE_VAULT  = Path(__file__).resolve().parent.parent.parent / "examples" / "sample-vault"
DEMO_DB       = Path.home() / ".basalt" / "demo.db"


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
    section: str = typer.Option("buried-insight", "--section",
                                help="Which Brief section to compute. Currently: buried-insight."),
    vault_aware: bool = typer.Option(True, "--vault-aware/--strict-defaults",
                                     help="Derive thresholds from vault age (default) or use fixed 180/90/180 defaults."),
    top: int = typer.Option(1, "--top", min=1, max=10,
                            help="Surface the top N insights (default 1)."),
):
    """Generate a Brief section."""
    if section != "buried-insight":
        raise typer.BadParameter(f"section '{section}' not yet implemented; try 'buried-insight'")

    conn = open_db(db)
    results = find_buried_insights(conn, vault_aware=vault_aware, top_n=top)
    conn.close()

    if not results:
        console.print("[yellow]No buried insight found.[/yellow]")
        console.print("[dim]The vault may be too young, too sparse, or too recently-edited everywhere.[/dim]")
        raise typer.Exit(code=1)

    _render_buried_results(results)


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

    console.print(Text("   ▸ Promote to thesis     ▸ Open all     ▸ Snooze", style="#D9824B"))


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


@app.command("demo")
def cmd_demo(
    embed_model: str = typer.Option("nomic-embed-text", "--embed-model"),
    top: int = typer.Option(1, "--top", min=1, max=5),
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

    # Now run the brief against the demo DB
    conn = open_db(DEMO_DB)
    results = find_buried_insights(conn, vault_aware=True, top_n=top)
    conn.close()
    if not results:
        console.print("[yellow]No buried insight found in the sample vault.[/yellow]")
        console.print("[dim]This shouldn't happen — the sample is designed to produce one.[/dim]")
        raise typer.Exit(code=1)
    _render_buried_results(results)


if __name__ == "__main__":
    app()

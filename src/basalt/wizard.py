"""`basalt init` — first-run setup wizard.

Honors clig.dev conventions: TTY detection, --no-input / --yes, NO_COLOR,
BASALT_* env vars, idempotent re-runs, atomic config write, no telemetry.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import httpx
import questionary
from questionary import Style as QStyle
from rich.console import Console
from rich.panel import Panel
from rich.table import Table


# ── Basalt prompt styling ───────────────────────────────────────
# Carries the formation's color world (basalt orange + warm bone) into every
# question. Replaces questionary's default `?` with the hex `⬡` so the visual
# identity established by the banner persists through the form.
BASALT_QMARK = "⬡"
BASALT_QSTYLE = QStyle([
    ("qmark",       "fg:#D9824B bold"),  # ⬡ basalt orange
    ("question",    "fg:#EFE9E2 bold"),  # question text — warm bone
    ("answer",      "fg:#D9824B"),       # what the user typed back
    ("pointer",     "fg:#D9824B bold"),  # ▸ select arrow
    ("highlighted", "fg:#D9824B bold"),  # highlighted choice
    ("selected",    "fg:#EFE9E2"),       # chosen choice
    ("instruction", "fg:#7A7269"),       # the (Use arrow keys) hint
    ("text",        "fg:#EFE9E2"),
])


CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "basalt" / "config.toml"
SCHEMA_VERSION = 1

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_EMBED_MODEL = "nomic-embed-text"

CURATED_MODELS = [
    "nomic-embed-text",         # 137M params, default — fast on M1 CPU
    "mxbai-embed-large",        # 335M params, better quality, slower
    "snowflake-arctic-embed-s", # 33M params, fastest, lower recall
]

MAX_PROMPT_ATTEMPTS = 3


# ── Config types ────────────────────────────────────────────────

@dataclass
class Config:
    vault_path: Path
    ollama_url: str
    embed_model: str

    def to_toml(self) -> str:
        return (
            f"schema_version = {SCHEMA_VERSION}\n"
            f"\n"
            f"[vault]\n"
            f'path = "{self.vault_path}"\n'
            f"\n"
            f"[embed]\n"
            f'provider = "ollama"\n'
            f'url = "{self.ollama_url}"\n'
            f'model = "{self.embed_model}"\n'
        )


def load_config(path: Path | None = None, *, on_warning=None) -> Config | None:
    """Load config. Returns None if file missing or unparseable.

    If file is parseable but partial (missing keys), fills from defaults and
    calls on_warning(key) for each filled key so the caller can surface it.
    """
    if path is None:
        path = CONFIG_PATH
    if not path.exists():
        return None
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None

    vault = data.get("vault", {})
    embed = data.get("embed", {})
    missing: list[str] = []

    vault_path_str = vault.get("path")
    if not vault_path_str:
        missing.append("vault.path")

    ollama_url = embed.get("url")
    if not ollama_url:
        missing.append("embed.url")
        ollama_url = DEFAULT_OLLAMA_URL

    embed_model = embed.get("model")
    if not embed_model:
        missing.append("embed.model")
        embed_model = DEFAULT_EMBED_MODEL

    # If vault path is missing, we can't recover — caller needs to re-run init.
    if not vault_path_str:
        if on_warning:
            for k in missing:
                on_warning(k)
        return None

    if missing and on_warning:
        for k in missing:
            on_warning(k)

    return Config(
        vault_path=Path(vault_path_str).expanduser(),
        ollama_url=ollama_url,
        embed_model=embed_model,
    )


def write_config(cfg: Config, path: Path | None = None) -> Path:
    """Atomic write: temp file + rename. Returns the written path."""
    if path is None:
        path = CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(cfg.to_toml())
    tmp.replace(path)
    return path


# ── Detection helpers ───────────────────────────────────────────

def _is_obsidian_vault(p: Path) -> bool:
    return (p / ".obsidian").is_dir()


def _count_markdown(p: Path, cap: int = 5000) -> int:
    """Count .md files under p, capped to avoid pathological traversal."""
    n = 0
    try:
        for _ in p.rglob("*.md"):
            n += 1
            if n >= cap:
                break
    except OSError:
        pass
    return n


def detect_vault_candidates() -> list[Path]:
    """Return likely vault paths in priority order. First match becomes the default."""
    candidates: list[Path] = []
    cwd = Path.cwd()
    if _is_obsidian_vault(cwd):
        candidates.append(cwd)
    home = Path.home()
    guesses: list[Path] = [
        home / "virtuoso-vault",
        home / "Documents" / "Obsidian Vault",
        home / "Documents" / "Obsidian",
        home / "Obsidian",
        home / "Notes",
    ]
    # iCloud Obsidian — enumerate vaults inside the iCloud container.
    icloud_base = home / "Library" / "Mobile Documents" / "iCloud~md~obsidian" / "Documents"
    if icloud_base.is_dir():
        try:
            for child in sorted(icloud_base.iterdir()):
                if child.is_dir():
                    guesses.append(child)
        except OSError:
            pass
    for guess in guesses:
        if guess.is_dir() and guess not in candidates:
            candidates.append(guess)
    return candidates


def ollama_status(url: str, timeout: float = 1.5) -> tuple[bool, list[str]]:
    """Return (reachable, installed_model_names). Never raises."""
    try:
        r = httpx.get(f"{url.rstrip('/')}/api/tags", timeout=timeout)
        r.raise_for_status()
        models = [m["name"].split(":")[0] for m in r.json().get("models", [])]
        return True, models
    except Exception:
        return False, []


def _valid_url(url: str) -> bool:
    return url.startswith(("http://", "https://"))


# ── Sample preview (wizard payoff) ──────────────────────────────

def _packaged_demo_db_path():
    """Return the path to the wheel-shipped demo.db. Raises if not packaged."""
    from importlib.resources import files
    return files("basalt.data") / "demo.db"


VERB_ROSTER = ("Buried Insight", "Connection", "Contradiction", "Implicit Thesis", "Drift")


def _render_verb_roster(console: Console) -> None:
    """Print the five-verb roster line below the sample preview."""
    console.print()
    console.print("  [dim]Five verbs in all:[/]")
    console.print(f"    [#EFE9E2]{' · '.join(VERB_ROSTER)}[/]")
    console.print("    [dim]On your vault, all five run with[/]  [bold #EFE9E2]basalt brief --section all[/]")


def _render_sample_preview(console: Console) -> None:
    """Render a one-section sample Brief from the wheel-shipped demo.db.

    Called from run_wizard after a first-run interactive write. Suppressed by
    the caller in non-interactive / non-first-run paths. Catches every error
    locally — the wizard must never fail because of preview issues.
    """
    console.print()
    console.print("  [dim]─── a Brief looks like this ───[/] [dim](on the bundled sample, 24 notes)[/]")
    try:
        from basalt.cli import render_buried_from_db
        db_path = _packaged_demo_db_path()
        ok = render_buried_from_db(db_path, console=console)
    except Exception:
        ok = False
    if not ok:
        console.print("  [dim](sample preview unavailable — run `basalt demo` to see one.)[/]")
        return
    _render_verb_roster(console)


# ── Wizard flow ─────────────────────────────────────────────────

class WizardAborted(Exception):
    """User cancelled or no-input mode hit a required prompt. Caller renders the message."""


def _resolve_defaults() -> tuple[Path | None, str, str]:
    """Apply precedence: env vars > existing config > sane defaults."""
    existing = load_config()
    env_vault = os.environ.get("BASALT_VAULT")
    env_url = os.environ.get("BASALT_OLLAMA_URL")
    env_model = os.environ.get("BASALT_EMBED_MODEL")

    if env_vault:
        vault_default: Path | None = Path(env_vault).expanduser()
    elif existing:
        vault_default = existing.vault_path
    else:
        cands = detect_vault_candidates()
        vault_default = cands[0] if cands else None

    url_default = env_url or (existing.ollama_url if existing else DEFAULT_OLLAMA_URL)
    model_default = env_model or (existing.embed_model if existing else DEFAULT_EMBED_MODEL)
    return vault_default, url_default, model_default


def _env_overrides_present() -> bool:
    return any(os.environ.get(k) for k in ("BASALT_VAULT", "BASALT_OLLAMA_URL", "BASALT_EMBED_MODEL"))


def _prompt_with_retry(ask, validate, error_message: str, console: Console):
    """Run `ask()` repeatedly until `validate(answer)` returns True. Up to MAX_PROMPT_ATTEMPTS."""
    for attempt in range(MAX_PROMPT_ATTEMPTS):
        answer = ask()
        ok, normalized = validate(answer)
        if ok:
            return normalized
        remaining = MAX_PROMPT_ATTEMPTS - attempt - 1
        if remaining > 0:
            console.print(f"  [yellow]·[/yellow] [dim]{error_message} ({remaining} {'try' if remaining == 1 else 'tries'} left)[/dim]")
    raise WizardAborted(f"Too many invalid answers ({error_message}).")


def run_wizard(*, yes: bool = False, no_input: bool = False, console: Console | None = None) -> Config:
    """Drive the interactive wizard. Returns the config (already written to disk on change).
    Raises WizardAborted if the user cancels or non-interactive mode lacks required data."""
    console = console or Console()
    vault_default, url_default, model_default = _resolve_defaults()
    existing = load_config()

    interactive = sys.stdin.isatty() and sys.stdout.isatty() and not no_input and not yes

    # ── Non-interactive path ─────────────────────────────────
    if not interactive:
        # --yes with an existing config and no env overrides → don't clobber.
        if yes and existing is not None and not _env_overrides_present():
            console.print(f"  [dim]config unchanged at[/] [#EFE9E2]{CONFIG_PATH}[/]")
            return existing
        if vault_default is None:
            raise WizardAborted(
                "Cannot run init non-interactively without a vault path. "
                "Set BASALT_VAULT or pass --vault."
            )
        if not vault_default.is_dir():
            raise WizardAborted(f"Vault path does not exist: {vault_default}")
        cfg = Config(vault_path=vault_default, ollama_url=url_default, embed_model=model_default)
        written = write_config(cfg)
        console.print(f"  [green]✓[/green] Wrote {written} [dim](non-interactive mode)[/dim]")
        return cfg

    # ── Interactive path ─────────────────────────────────────
    try:
        if interactive and not existing:
            console.print("  [dim italic]This takes about a minute. Basalt will not write to your vault.[/]")
            console.print()

        if existing:
            console.print(f"  [dim]Found config at[/] [#EFE9E2]{CONFIG_PATH}[/]")
            reconfigure = questionary.confirm(
                "Reconfigure?", default=False, qmark=BASALT_QMARK, style=BASALT_QSTYLE,
            ).unsafe_ask()
            if not reconfigure:
                return existing

        # Show vault detection beat before asking.
        if vault_default and _is_obsidian_vault(vault_default):
            count = _count_markdown(vault_default)
            count_str = f"{count:,} notes" + ("+" if count >= 5000 else "")
            console.print(f"  [dim]Found a vault at[/] [#EFE9E2]{vault_default}[/] [dim]·[/] [dim]{count_str}[/]")

        # ── Vault path (re-ask on invalid) ────────────────
        def _ask_vault():
            return questionary.path(
                "Vault path:",
                default=str(vault_default) if vault_default else str(Path.home()),
                only_directories=True,
                qmark=BASALT_QMARK,
                style=BASALT_QSTYLE,
            ).unsafe_ask()

        def _validate_vault(answer: str) -> tuple[bool, Path]:
            p = Path(answer).expanduser().absolute()
            if not p.exists():
                console.print(f"  [yellow]·[/yellow] [dim]No such directory: {p}[/dim]")
                return False, p
            if not p.is_dir():
                console.print(f"  [yellow]·[/yellow] [dim]Not a directory (looks like a file): {p}[/dim]")
                return False, p
            return True, p

        vault_path = _prompt_with_retry(
            _ask_vault, _validate_vault, "Please enter a valid directory path.", console
        )
        if not _is_obsidian_vault(vault_path):
            console.print(
                f"  [dim]No `.obsidian/` in {vault_path} — that's fine, Basalt reads any folder of Markdown.[/dim]"
            )

        # ── Ollama URL (re-ask on bad scheme) ─────────────
        def _ask_url():
            return questionary.text(
                "Ollama URL:", default=url_default,
                qmark=BASALT_QMARK, style=BASALT_QSTYLE,
            ).unsafe_ask().strip()

        def _validate_url(answer: str) -> tuple[bool, str]:
            if not _valid_url(answer):
                console.print(f"  [yellow]·[/yellow] [dim]URL must start with http:// or https://[/dim]")
                return False, answer
            return True, answer

        ollama_url = _prompt_with_retry(
            _ask_url, _validate_url, "Please enter a valid URL.", console
        )

        reachable, installed = ollama_status(ollama_url)
        if reachable:
            console.print(f"  [green]✓[/green] [#EFE9E2]Ollama answered.[/] [dim]{len(installed)} models on hand.[/]")
        else:
            console.print(
                f"  [yellow]·[/yellow] [dim]Ollama isn't answering at {ollama_url} yet. "
                "That's fine — start it later, this config will wait.[/]"
            )

        # ── Embed model ───────────────────────────────────
        choices = list(CURATED_MODELS)
        if model_default not in choices:
            choices.insert(0, model_default)
        choices.append("other (type your own)")
        choice = questionary.select(
            "Embedding model:",
            choices=choices,
            default=model_default if model_default in choices else choices[0],
            qmark=BASALT_QMARK,
            style=BASALT_QSTYLE,
            pointer="▸",
        ).unsafe_ask()
        if choice == "other (type your own)":
            embed_model = questionary.text(
                "Model name:", default=model_default,
                qmark=BASALT_QMARK, style=BASALT_QSTYLE,
            ).unsafe_ask().strip()
            if not embed_model:
                raise WizardAborted("Model name cannot be empty.")
        else:
            embed_model = choice

        if reachable and embed_model not in installed:
            console.print(
                f"  [yellow]·[/yellow] [dim]`{embed_model}` isn't pulled yet. When you're ready:[/dim]\n"
                f"      [#EFE9E2]ollama pull {embed_model}[/]"
            )

    except KeyboardInterrupt:
        raise WizardAborted("interrupted")

    # ── Summary panel ─────────────────────────────────────────
    cfg = Config(vault_path=vault_path, ollama_url=ollama_url, embed_model=embed_model)
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("vault", str(cfg.vault_path))
    table.add_row("ollama", cfg.ollama_url)
    table.add_row("model", cfg.embed_model)
    table.add_row("config", str(CONFIG_PATH))
    console.print()
    console.print(
        Panel(
            table,
            title="[#D9824B]⬡[/] [bold]before we set this[/]",
            border_style="#5A5048",
            padding=(0, 2),
        )
    )

    try:
        confirm = questionary.confirm(
            "Write config?", default=True, qmark=BASALT_QMARK, style=BASALT_QSTYLE,
        ).unsafe_ask()
    except KeyboardInterrupt:
        raise WizardAborted("interrupted")
    if not confirm:
        raise WizardAborted("declined")

    written = write_config(cfg)
    console.print()
    console.print(f"  [#D9824B]⬡[/]  [#EFE9E2]Set.[/] [dim]{written}[/]")

    # First-run payoff: render a sample Brief from the bundled vault. Gated to
    # interactive + first-run only — see spec for rationale.
    if existing is None:
        _render_sample_preview(console)

    console.print()
    console.print(f"  [dim]Next, build the index:[/] [bold #EFE9E2]basalt index[/]")
    return cfg

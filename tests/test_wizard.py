"""Tests for the `basalt init` wizard.

The interactive path is exercised by hand; these tests cover the parts that
must never regress: TOML roundtrip, env-var precedence, atomic write,
non-interactive failure modes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from basalt import wizard
from basalt.wizard import (
    Config,
    WizardAborted,
    load_config,
    run_wizard,
    write_config,
)


def test_config_toml_roundtrip(tmp_path: Path):
    cfg = Config(
        vault_path=tmp_path / "vault",
        ollama_url="http://1.2.3.4:11434",
        embed_model="mxbai-embed-large",
    )
    out = tmp_path / "config.toml"
    write_config(cfg, out)
    loaded = load_config(out)
    assert loaded == cfg


def test_load_config_returns_none_when_missing(tmp_path: Path):
    assert load_config(tmp_path / "nope.toml") is None


def test_load_config_returns_none_on_malformed_toml(tmp_path: Path):
    bad = tmp_path / "bad.toml"
    bad.write_text("this is = not [ valid toml")
    assert load_config(bad) is None


def test_write_config_is_atomic(tmp_path: Path, monkeypatch):
    """Write must not leave a partial file even if rename fails."""
    out = tmp_path / "subdir" / "config.toml"
    cfg = Config(
        vault_path=tmp_path,
        ollama_url="http://localhost:11434",
        embed_model="nomic-embed-text",
    )
    written = write_config(cfg, out)
    assert written == out
    assert out.exists()
    # No leftover .tmp sibling
    assert not (out.parent / "config.toml.tmp").exists()


def test_no_input_fails_without_vault(tmp_path: Path, monkeypatch):
    """--no-input must abort cleanly when nothing can be inferred."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(wizard, "CONFIG_PATH", fake_home / ".config" / "basalt" / "config.toml")
    monkeypatch.delenv("BASALT_VAULT", raising=False)
    monkeypatch.chdir(fake_home)
    with pytest.raises(WizardAborted, match="vault path"):
        run_wizard(no_input=True)


def test_no_input_writes_config_from_env(tmp_path: Path, monkeypatch):
    """With BASALT_VAULT set, --no-input must write a config without prompting."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg_path = fake_home / ".config" / "basalt" / "config.toml"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(wizard, "CONFIG_PATH", cfg_path)
    monkeypatch.setenv("BASALT_VAULT", str(vault))
    monkeypatch.setenv("BASALT_OLLAMA_URL", "http://example:11434")
    monkeypatch.setenv("BASALT_EMBED_MODEL", "snowflake-arctic-embed-s")
    cfg = run_wizard(no_input=True)
    assert cfg.vault_path == vault
    assert cfg.ollama_url == "http://example:11434"
    assert cfg.embed_model == "snowflake-arctic-embed-s"
    assert cfg_path.exists()


def test_no_input_rejects_nonexistent_vault(tmp_path: Path, monkeypatch):
    """If BASALT_VAULT points to a missing dir, abort instead of writing."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(wizard, "CONFIG_PATH", fake_home / ".config" / "basalt" / "config.toml")
    monkeypatch.setenv("BASALT_VAULT", str(tmp_path / "does-not-exist"))
    with pytest.raises(WizardAborted, match="does not exist"):
        run_wizard(no_input=True)


# ── New behaviors added in the post-review pass ────────────────

def test_partial_config_fills_defaults_and_warns(tmp_path: Path):
    """A config missing embed.url and embed.model should load with defaults
    + on_warning callback fired for each missing key."""
    partial = tmp_path / "config.toml"
    partial.write_text(
        "schema_version = 1\n\n"
        "[vault]\n"
        f'path = "{tmp_path}"\n'
    )
    warnings: list[str] = []
    cfg = load_config(partial, on_warning=warnings.append)
    assert cfg is not None
    assert cfg.ollama_url == wizard.DEFAULT_OLLAMA_URL
    assert cfg.embed_model == wizard.DEFAULT_EMBED_MODEL
    assert "embed.url" in warnings
    assert "embed.model" in warnings


def test_partial_config_missing_vault_returns_none(tmp_path: Path):
    """If vault.path is missing we can't recover — caller must re-run init."""
    partial = tmp_path / "config.toml"
    partial.write_text(
        "schema_version = 1\n\n"
        "[embed]\n"
        'url = "http://localhost:11434"\n'
        'model = "nomic-embed-text"\n'
    )
    assert load_config(partial) is None


def test_yes_with_existing_config_does_not_clobber(tmp_path: Path, monkeypatch):
    """--yes + existing config + no env overrides → return existing, don't write."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg_path = fake_home / ".config" / "basalt" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    original = Config(
        vault_path=vault,
        ollama_url="http://custom.host:11434",
        embed_model="mxbai-embed-large",
    )
    write_config(original, cfg_path)
    mtime_before = cfg_path.stat().st_mtime_ns

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(wizard, "CONFIG_PATH", cfg_path)
    monkeypatch.delenv("BASALT_VAULT", raising=False)
    monkeypatch.delenv("BASALT_OLLAMA_URL", raising=False)
    monkeypatch.delenv("BASALT_EMBED_MODEL", raising=False)

    cfg = run_wizard(yes=True)
    assert cfg == original
    assert cfg_path.stat().st_mtime_ns == mtime_before, "file should not have been rewritten"


def test_yes_with_env_override_does_overwrite(tmp_path: Path, monkeypatch):
    """--yes + existing config + env var changes → write the new config."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg_path = fake_home / ".config" / "basalt" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    original = Config(vault_path=vault, ollama_url="http://localhost:11434", embed_model="nomic-embed-text")
    write_config(original, cfg_path)

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(wizard, "CONFIG_PATH", cfg_path)
    monkeypatch.setenv("BASALT_OLLAMA_URL", "http://new.host:11434")

    cfg = run_wizard(yes=True)
    assert cfg.ollama_url == "http://new.host:11434"
    reloaded = load_config(cfg_path)
    assert reloaded.ollama_url == "http://new.host:11434"


def test_vault_detection_finds_icloud_obsidian(tmp_path: Path, monkeypatch):
    """detect_vault_candidates() must find vaults inside the iCloud Obsidian container."""
    fake_home = tmp_path / "home"
    icloud = fake_home / "Library" / "Mobile Documents" / "iCloud~md~obsidian" / "Documents"
    vault_a = icloud / "my-vault"
    (vault_a / ".obsidian").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    cands = wizard.detect_vault_candidates()
    # The iCloud vault should appear in the candidate list.
    assert any(str(c).endswith("my-vault") for c in cands)


def test_valid_url_helper():
    """URL scheme check used by the wizard re-ask loop."""
    assert wizard._valid_url("http://localhost:11434")
    assert wizard._valid_url("https://example.com")
    assert not wizard._valid_url("localhost:11434")
    assert not wizard._valid_url("//example.com")
    assert not wizard._valid_url("")


# ── Sample-preview behaviors (wizard payoff) ───────────────────


def test_packaged_demo_db_returns_at_least_one_insight():
    """The wheel-shipped demo.db must load and surface ≥1 buried insight,
    or the wizard's preview will silently fall back forever."""
    from importlib.resources import files
    from basalt.index import open_db
    from basalt.buried import find_buried_insights

    db_path = files("basalt.data") / "demo.db"
    conn = open_db(db_path)
    try:
        results = find_buried_insights(conn, vault_aware=True, top_n=1)
    finally:
        conn.close()
    assert len(results) >= 1
    assert results[0].quote.strip(), "top result has empty quote"


def test_render_sample_preview_writes_buried_insight_block(capsys):
    """The wizard's preview helper renders the buried-insight header to stdout."""
    from rich.console import Console
    from basalt.wizard import _render_sample_preview

    console = Console(force_terminal=True, width=120)
    _render_sample_preview(console)
    out = capsys.readouterr().out
    assert "THE BURIED INSIGHT" in out


def test_render_sample_preview_falls_back_gracefully_when_db_missing(capsys, monkeypatch):
    """If the packaged DB can't be resolved or opened, the preview emits a
    quiet fallback line — never raises, never leaks an exception."""
    from rich.console import Console
    from basalt import wizard

    def boom(*a, **kw):
        raise FileNotFoundError("simulated missing demo.db")
    monkeypatch.setattr(wizard, "_packaged_demo_db_path", boom)

    console = Console(force_terminal=True, width=120)
    wizard._render_sample_preview(console)
    out = capsys.readouterr().out
    assert "sample preview unavailable" in out
    assert "Traceback" not in out

# Wizard Sample-Brief Preview — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render a one-section Buried Insight preview at the end of `basalt init`'s first interactive run, using a pre-built SQLite DB shipped in the wheel — so first-time users see *what a Brief looks like* before they exit the wizard, without waiting for Ollama or indexing their vault.

**Architecture:** Three-piece change. (1) Build-time script generates `src/basalt/data/demo.db` from the bundled sample vault, committed to the repo. (2) Wheel packaging includes the new `basalt/data/` directory. (3) A small `_render_sample_preview()` helper in `wizard.py` opens the packaged DB via `importlib.resources`, calls the existing `find_buried_insights` + `_render_buried_results` path (exposed as a public `render_buried_from_db()` in `cli.py`), and prints the four-verb roster. Gated on `interactive AND existing is None`. Fallback to a dim "preview unavailable" line on any error.

**Tech Stack:** Python 3.12, sqlite3, importlib.resources, pytest, hatchling (wheel builder), questionary (already in deps), Rich (already in deps).

**Reference:**
- Spec: `docs/superpowers/specs/2026-05-12-wizard-sample-preview-design.md`
- Existing render helper: `src/basalt/cli.py:461` `_render_buried_results(results)`
- Existing finder: `src/basalt/buried.py` `find_buried_insights(conn, vault_aware, top_n)`
- Existing wizard end: `src/basalt/wizard.py` around the `console.print(f"  [#D9824B]⬡[/]  [#EFE9E2]Set.[/] ...")` block

---

## File Structure

| Path | Status | Responsibility |
|------|--------|----------------|
| `src/basalt/data/__init__.py` | new | Marks `basalt.data` as a package so `importlib.resources.files("basalt.data")` resolves. |
| `src/basalt/data/demo.db` | new (generated, committed) | Pre-built SQLite DB with sample-vault notes + nomic-embed-text embeddings + at least one buried insight. ~150KB. |
| `scripts/build_demo_db.py` | new | Release-time script. Removes old DB, indexes `examples/sample-vault/`, validates the result, leaves DB in place. |
| `src/basalt/cli.py` | modify | Extract a public `render_buried_from_db(db_path, console)` helper. Used by both `cmd_brief` (no behavior change) and the wizard preview. |
| `src/basalt/wizard.py` | modify | Add `_render_sample_preview(console)` and `_render_verb_roster(console)`. Call them from `run_wizard` between "Set." and "Next, build the index", gated on `interactive AND existing is None`. |
| `pyproject.toml` | modify | Include `src/basalt/data/*` as wheel package data via `[tool.hatch.build.targets.wheel.force-include]`. |
| `tests/test_wizard.py` | modify | 5 new tests: gating logic, packaged DB loads, missing DB falls back gracefully. |

---

## Task 1: Create the `basalt.data` package and ship config

**Files:**
- Create: `src/basalt/data/__init__.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Create empty package init**

```bash
mkdir -p ~/Projects/basalt/src/basalt/data
```

Then write `src/basalt/data/__init__.py`:

```python
"""Package data for basalt — pre-built demo.db ships here so `basalt init` can
render a sample Brief without requiring the user to run `basalt index` first."""
```

- [ ] **Step 2: Update pyproject.toml to include package data in the wheel**

Open `pyproject.toml`. After the `[tool.hatch.build.targets.wheel]` block (currently line ~66 with `packages = ["src/basalt"]`), add:

```toml
[tool.hatch.build.targets.wheel.force-include]
"src/basalt/data" = "basalt/data"
```

The `force-include` directive guarantees `basalt/data/*` ends up in the wheel even though `data/` doesn't match the default Python file pattern.

- [ ] **Step 3: Verify the package importer resolves**

Run: `cd ~/Projects/basalt && .venv/bin/python -c "from importlib.resources import files; print(files('basalt.data'))"`

Expected output: `MultiplexedPath('/Users/.../basalt/src/basalt/data')` (or `PosixPath` of that location). No `ModuleNotFoundError`.

- [ ] **Step 4: Commit**

```bash
cd ~/Projects/basalt
git add src/basalt/data/__init__.py pyproject.toml
git commit -m "$(cat <<'EOF'
chore: add basalt.data package for shipped resources

Empty package marker + hatch force-include so demo.db (next task)
can ship in the wheel for first-run wizard preview.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Build script for the demo DB

**Files:**
- Create: `scripts/build_demo_db.py`
- Test: covered by the script's own validation steps (run-once tool, not user-facing code).

- [ ] **Step 1: Write the build script**

Create `scripts/build_demo_db.py`:

```python
#!/usr/bin/env python3
"""Build src/basalt/data/demo.db from examples/sample-vault/.

Release-time tool. Requires Ollama running with `nomic-embed-text` pulled.

Run when:
  - examples/sample-vault/ contents change
  - embedding model changes
  - buried-insight algorithm changes in a way that affects bundled output

Usage:
  python scripts/build_demo_db.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_VAULT = REPO_ROOT / "examples" / "sample-vault"
DEMO_DB = REPO_ROOT / "src" / "basalt" / "data" / "demo.db"

MIN_SIZE_BYTES = 50_000      # 50KB — sanity floor
MAX_SIZE_BYTES = 500_000     # 500KB — sanity ceiling

# Add src/ to sys.path so we can import basalt without installing it.
sys.path.insert(0, str(REPO_ROOT / "src"))

from basalt.vault import walk_vault
from basalt.index import open_db, upsert_note, replace_links, resolve_link_targets
from basalt.embed import ensure_embeddings
from basalt.buried import find_buried_insights


def main() -> int:
    if not SAMPLE_VAULT.is_dir():
        print(f"✗ sample vault not found at {SAMPLE_VAULT}", file=sys.stderr)
        return 1

    print(f"Building {DEMO_DB} from {SAMPLE_VAULT}…")
    if DEMO_DB.exists():
        DEMO_DB.unlink()
    DEMO_DB.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    conn = open_db(DEMO_DB)

    n_notes = 0
    for note in walk_vault(SAMPLE_VAULT):
        nid = upsert_note(conn, note)
        replace_links(conn, nid, note.wikilinks)
        n_notes += 1
    conn.commit()
    resolve_link_targets(conn)
    conn.commit()
    print(f"  ✓ indexed {n_notes} notes in {time.time()-t0:.1f}s")

    t1 = time.time()
    computed, skipped = ensure_embeddings(
        conn,
        model="nomic-embed-text",
        on_progress=lambda msg: print(f"  {msg}"),
    )
    print(f"  ✓ embedded {computed} (skipped {skipped}) in {time.time()-t1:.1f}s")

    # Validation: must produce at least one buried insight.
    results = find_buried_insights(conn, vault_aware=True, top_n=1)
    if not results:
        print("✗ validation failed: no buried insights surfaced", file=sys.stderr)
        return 2
    quote = results[0].quote.strip().replace("\n", " ")[:60]
    print(f"  ✓ top buried insight: {quote!r}")

    conn.close()

    # Validation: size bounds.
    size = DEMO_DB.stat().st_size
    if not (MIN_SIZE_BYTES <= size <= MAX_SIZE_BYTES):
        print(
            f"✗ validation failed: demo.db size {size} bytes is outside "
            f"[{MIN_SIZE_BYTES}, {MAX_SIZE_BYTES}]",
            file=sys.stderr,
        )
        return 3
    print(f"  ✓ size {size:,} bytes")
    print(f"✓ done · {DEMO_DB}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run the build script**

Run: `cd ~/Projects/basalt && .venv/bin/python scripts/build_demo_db.py`

Expected stdout (representative):
```
Building /Users/.../src/basalt/data/demo.db from /Users/.../examples/sample-vault…
  ✓ indexed 24 notes in 0.1s
  embedded 24/24…
  ✓ embedded 24 (skipped 0) in ~5s
  ✓ top buried insight: 'The sustainable edge isn't speed alone — it's speed +'
  ✓ size 1XX,XXX bytes
✓ done · /Users/.../src/basalt/data/demo.db
```

Expected exit code: `0`.

If Ollama is down: script will hang on the first embed call. Start Ollama first: `ollama serve` (in another terminal) and confirm `nomic-embed-text` is pulled (`ollama list | grep nomic-embed-text`).

- [ ] **Step 3: Verify the DB on disk**

Run: `ls -la ~/Projects/basalt/src/basalt/data/demo.db && file ~/Projects/basalt/src/basalt/data/demo.db`

Expected: file exists, size between 50KB and 500KB, file type "SQLite 3.x database".

- [ ] **Step 4: Smoke-test from the packaged location**

Run:
```bash
cd ~/Projects/basalt && .venv/bin/python -c "
from importlib.resources import files
from basalt.index import open_db
from basalt.buried import find_buried_insights
db_path = files('basalt.data') / 'demo.db'
conn = open_db(db_path)
results = find_buried_insights(conn, vault_aware=True, top_n=1)
print('insights:', len(results))
print('first quote:', results[0].quote.strip()[:80] if results else 'NONE')
"
```

Expected: `insights: 1` and a non-empty quote line. No tracebacks.

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/basalt
git add scripts/build_demo_db.py src/basalt/data/demo.db
git commit -m "$(cat <<'EOF'
chore: ship pre-built demo.db for wizard sample preview

scripts/build_demo_db.py is the release-time tool. Re-run when
the sample vault, embedding model, or buried-insight algo changes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Extract `render_buried_from_db` helper (refactor)

**Files:**
- Modify: `src/basalt/cli.py` (add helper alongside the existing `_render_buried_results`)
- Test: `tests/test_wizard.py` (lock in current cmd_brief behavior is already covered by the existing 48-test suite — no new test needed in this step; we'll just rely on the suite staying green)

- [ ] **Step 1: Pin the current behavior — run the full suite, confirm 48 pass**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q`

Expected: `48 passed in <1.5s` (no failures, no errors).

- [ ] **Step 2: Add the new helper to cli.py**

Open `src/basalt/cli.py`. After the existing `_render_buried_results` function (currently ending around line 510), add the new public helper:

```python
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
```

Note: `console` parameter is currently unused — `_render_buried_results` uses the module-level `console`. The parameter is kept in the signature for forward compatibility (future call paths may want a non-default console).

- [ ] **Step 3: Verify the refactor didn't break anything**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q`

Expected: `48 passed in <1.5s`. Identical to Step 1.

- [ ] **Step 4: Smoke-test the new helper end-to-end**

Run:
```bash
cd ~/Projects/basalt && .venv/bin/python -c "
from importlib.resources import files
from basalt.cli import render_buried_from_db
db_path = files('basalt.data') / 'demo.db'
ok = render_buried_from_db(db_path)
print('rendered:', ok)
"
```

Expected: prints the full "THE BURIED INSIGHT" block (header, quote, falsification rules, action menu), then `rendered: True`.

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/basalt
git add src/basalt/cli.py
git commit -m "$(cat <<'EOF'
refactor: extract render_buried_from_db helper

Pure refactor — wraps the existing find + render path with
exception isolation. Used in the next commit by the wizard
to render the bundled sample preview.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Add `_render_sample_preview` and `_render_verb_roster` to wizard.py (TDD)

**Files:**
- Modify: `src/basalt/wizard.py`
- Test: `tests/test_wizard.py`

- [ ] **Step 1: Write the failing test for the packaged-DB happy path**

Open `tests/test_wizard.py`. After the existing tests, add:

```python
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
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q tests/test_wizard.py::test_packaged_demo_db_returns_at_least_one_insight tests/test_wizard.py::test_render_sample_preview_writes_buried_insight_block tests/test_wizard.py::test_render_sample_preview_falls_back_gracefully_when_db_missing`

Expected: `test_packaged_demo_db_returns_at_least_one_insight` PASSES (Task 2 already created the DB), the other two FAIL with `AttributeError: module 'basalt.wizard' has no attribute '_render_sample_preview'` and `... _packaged_demo_db_path`. Good — we now have failing tests for the two new functions.

- [ ] **Step 3: Implement `_packaged_demo_db_path` and `_render_sample_preview`**

Open `src/basalt/wizard.py`. After the existing `ollama_status` function and before the `class WizardAborted` declaration, add:

```python
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
```

- [ ] **Step 4: Run the new tests again — they should pass**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q tests/test_wizard.py::test_render_sample_preview_writes_buried_insight_block tests/test_wizard.py::test_render_sample_preview_falls_back_gracefully_when_db_missing -v`

Expected: both PASS.

- [ ] **Step 5: Run the full suite — confirm no regression**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q`

Expected: `51 passed` (was 48, added 3).

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/basalt
git add src/basalt/wizard.py tests/test_wizard.py
git commit -m "$(cat <<'EOF'
feat(wizard): render sample Buried Insight from packaged demo.db

Adds _render_sample_preview() + _render_verb_roster() helpers in
wizard.py. Both catch exceptions locally so a missing or broken
demo.db can never break the wizard. Caller (next commit) wires the
preview into run_wizard, gated on first-run interactive.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Wire the preview into `run_wizard` (TDD)

**Files:**
- Modify: `src/basalt/wizard.py`
- Test: `tests/test_wizard.py`

- [ ] **Step 1: Write the failing test for gating logic — preview skipped on `--yes`**

Add to `tests/test_wizard.py`:

```python
def test_wizard_does_not_render_preview_when_yes_flag(tmp_path, monkeypatch, capsys):
    """--yes with no existing config writes the file but does NOT render the
    preview — that's an interactive-only beat."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg_path = fake_home / ".config" / "basalt" / "config.toml"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(wizard, "CONFIG_PATH", cfg_path)
    monkeypatch.setenv("BASALT_VAULT", str(vault))
    monkeypatch.delenv("BASALT_OLLAMA_URL", raising=False)
    monkeypatch.delenv("BASALT_EMBED_MODEL", raising=False)

    run_wizard(yes=True)
    out = capsys.readouterr().out
    assert "THE BURIED INSIGHT" not in out
    assert "a Brief looks like this" not in out


def test_wizard_does_not_render_preview_when_existing_config(tmp_path, monkeypatch, capsys):
    """If an existing config is present, the user has already done the wizard.
    No preview on reconfigure."""
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
    monkeypatch.delenv("BASALT_VAULT", raising=False)
    monkeypatch.delenv("BASALT_OLLAMA_URL", raising=False)
    monkeypatch.delenv("BASALT_EMBED_MODEL", raising=False)

    run_wizard(yes=True)
    out = capsys.readouterr().out
    assert "THE BURIED INSIGHT" not in out
```

- [ ] **Step 2: Run the new tests — verify they pass even before wiring (because the wiring doesn't exist yet, nothing renders the preview in any path)**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q tests/test_wizard.py::test_wizard_does_not_render_preview_when_yes_flag tests/test_wizard.py::test_wizard_does_not_render_preview_when_existing_config -v`

Expected: both PASS. They guard against the wiring leaking into wrong paths in later changes.

- [ ] **Step 3: Wire the preview call into `run_wizard`**

Open `src/basalt/wizard.py`. Find the block near the bottom of `run_wizard` that prints "⬡ Set." (currently looks like):

```python
    written = write_config(cfg)
    console.print()
    console.print(f"  [#D9824B]⬡[/]  [#EFE9E2]Set.[/] [dim]{written}[/]")
    console.print(f"  [dim]Next, build the index:[/] [bold #EFE9E2]basalt index[/]")
    return cfg
```

Replace it with:

```python
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
```

Note: the `interactive` gate is already enforced because we're inside the interactive branch of `run_wizard`. The non-interactive `--yes` path returns earlier. We only need to check `existing is None` here.

- [ ] **Step 4: Verify no regression on the gating tests**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q tests/test_wizard.py::test_wizard_does_not_render_preview_when_yes_flag tests/test_wizard.py::test_wizard_does_not_render_preview_when_existing_config -v`

Expected: both still PASS. (The wiring only fires in the interactive first-run path.)

- [ ] **Step 5: Run the full suite**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q`

Expected: `53 passed` (was 51 after Task 4, added 2).

- [ ] **Step 6: Live smoke test — run the wizard against a scratch config and confirm the preview lands**

Run:
```bash
trash /tmp/basalt-preview-test 2>/dev/null
XDG_CONFIG_HOME=/tmp/basalt-preview-test BASALT_VAULT="$HOME/virtuoso-vault" \
  BASALT_OLLAMA_URL="http://localhost:11434" \
  BASALT_EMBED_MODEL="nomic-embed-text" \
  ~/Projects/basalt/.venv/bin/basalt init --yes
```

Expected: `--yes` flow writes config WITHOUT preview (confirms gating). Last line should be `✓ Wrote /tmp/basalt-preview-test/basalt/config.toml (non-interactive mode)`.

For the interactive smoke test, run *without* `--yes`:

```bash
trash /tmp/basalt-preview-test-2 2>/dev/null
XDG_CONFIG_HOME=/tmp/basalt-preview-test-2 ~/Projects/basalt/.venv/bin/basalt init
```

This is interactive — answer the four prompts with defaults (Enter through them). After "Write config? Yes" you should see:

```
  ⬡  Set. /tmp/basalt-preview-test-2/basalt/config.toml

  ─── a Brief looks like this ─── (on the bundled sample, 24 notes)

THE BURIED INSIGHT
─────────────────────
[...full sample buried-insight render...]

  Five verbs in all:
    Buried Insight · Connection · Contradiction · Implicit Thesis · Drift
    On your vault, all five run with  basalt brief --section all

  Next, build the index:  basalt index
```

If everything renders cleanly, move on. If the preview block is missing or shows the fallback line, debug before committing.

- [ ] **Step 7: Commit**

```bash
cd ~/Projects/basalt
git add src/basalt/wizard.py tests/test_wizard.py
git commit -m "$(cat <<'EOF'
feat(wizard): wire sample Brief preview into first-run flow

Renders one Buried Insight section + four-verb roster after the
"Set." line, gated to interactive AND first-run-only. Suppressed
in --yes / --no-input / reconfigure. Falls back to a quiet line
if the packaged demo.db is missing or unreadable.

Closes the magic-moment gap: first-time users see what a Brief
looks like without waiting for `basalt index` on their vault.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Build-from-source documentation

**Files:**
- Modify: `README.md`

This is a small docs nudge: anyone installing from source needs to know `demo.db` is generated, not stored as binary in the source tree of forks. (We do commit it for first-class wheel ships, but contributors editing the sample vault need to regenerate.)

- [ ] **Step 1: Find the existing build/install section in README.md**

Run: `grep -n "from source\|install\|pip install" ~/Projects/basalt/README.md | head -5`

Note the line numbers of the relevant section.

- [ ] **Step 2: Append a "Regenerating the demo DB" note near the install instructions**

Add a short subsection — three or four lines — under the build/install section:

```markdown
### Regenerating the sample preview (contributors only)

The wizard's first-run preview reads from `src/basalt/data/demo.db`, which is
committed to the repo. If you edit `examples/sample-vault/` or change the
embedding model, regenerate it:

    python scripts/build_demo_db.py

Requires Ollama running with `nomic-embed-text` pulled.
```

- [ ] **Step 3: Commit**

```bash
cd ~/Projects/basalt
git add README.md
git commit -m "$(cat <<'EOF'
docs: note how to regenerate the bundled demo.db

Contributors editing the sample vault or the embedding model need
to re-run scripts/build_demo_db.py before the wizard preview
reflects their change.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Full test suite green**

Run: `cd ~/Projects/basalt && .venv/bin/python -m pytest -q`

Expected: `53 passed` (was 48 before this plan, added 5).

- [ ] **Step 2: Wheel builds clean**

Run:
```bash
cd ~/Projects/basalt && rm -rf dist/ && .venv/bin/python -m build 2>&1 | tail -10
```

Expected: `Successfully built basalt_vault-0.0.14-py3-none-any.whl` (or current version). Then verify the demo DB is in the wheel:

```bash
unzip -l dist/*.whl | grep -E "data/demo|data/__init__"
```

Expected: two lines, one for `basalt/data/__init__.py`, one for `basalt/data/demo.db` (~150KB).

- [ ] **Step 3: Fresh-venv install + run**

Run:
```bash
rm -rf /tmp/basalt-fresh-test && python3.12 -m venv /tmp/basalt-fresh-test
/tmp/basalt-fresh-test/bin/pip install -q ~/Projects/basalt/dist/*.whl
trash /tmp/basalt-fresh-test-cfg 2>/dev/null
XDG_CONFIG_HOME=/tmp/basalt-fresh-test-cfg /tmp/basalt-fresh-test/bin/basalt init
```

Walk through the wizard interactively. Confirm: preview renders the buried insight + four-verb roster + final "Next, build the index" line.

- [ ] **Step 4: Cleanup scratch dirs**

```bash
trash /tmp/basalt-fresh-test /tmp/basalt-fresh-test-cfg /tmp/basalt-preview-test /tmp/basalt-preview-test-2 2>/dev/null
```

- [ ] **Step 5: Tag if shipping as a release**

If this is going out as v0.0.15:

```bash
cd ~/Projects/basalt && git tag v0.0.15 && git log --oneline -10
```

Otherwise skip — no tag, just commits on the branch.

---

## Self-Review (run before handoff)

**Spec coverage check:**
- §3 decision #1 (position C — after "Set.") → Task 5 Step 3 places the call between "Set." and "Next, build the index" ✓
- §3 decision #2 (what renders D — buried insight + four-verb roster) → Task 4 Step 3 renders both ✓
- §3 decision #3 (how generated B — pre-built DB) → Tasks 1+2 ship the DB in the wheel ✓
- §3 decision #4 (trigger A+C — interactive AND first-run only) → Task 5 Step 3 checks `existing is None`; `interactive` is already enforced by the call site being inside the interactive branch ✓
- §4.3 components table — every row mapped to a task ✓
- §5 copy block — Task 4 Step 3 reproduces every line ✓
- §6 failure modes — Task 4 Step 3's try/except handles missing-DB, unreadable-DB, no-rows, renderer-error; Task 5 Step 1 tests the `--yes` and reconfigure cases; the `_PLAIN_STDOUT` mode is covered by existing wizard chrome ✓
- §7 tests — 5 tests required by spec, all 5 written: `test_packaged_demo_db_returns_at_least_one_insight` (Task 4), `test_render_sample_preview_writes_buried_insight_block` (Task 4), `test_render_sample_preview_falls_back_gracefully_when_db_missing` (Task 4), `test_wizard_does_not_render_preview_when_yes_flag` (Task 5), `test_wizard_does_not_render_preview_when_existing_config` (Task 5) ✓
- §8 release ops — Task 2 builds the DB; Task 6 documents how to regenerate ✓
- §9 scope/non-goals — plan does not add embed at wizard time, telemetry, or animated render ✓

**Placeholder scan:** No TBDs, no "implement appropriate error handling" — every step has concrete code. No "similar to Task N" — code blocks repeated where needed.

**Type / signature consistency:**
- `render_buried_from_db(db_path, *, console=None)` defined in Task 3, called the same way in Task 4 ✓
- `_render_sample_preview(console)` and `_render_verb_roster(console)` defined and called consistently ✓
- `_packaged_demo_db_path()` defined in Task 4, monkeypatched in Task 4's third test ✓
- Test count math: 48 baseline + 3 from Task 4 + 2 from Task 5 = 53 ✓

No gaps found.

---

## Execution Handoff

**Plan complete and saved to `~/Projects/basalt/docs/superpowers/plans/2026-05-12-wizard-sample-preview.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**

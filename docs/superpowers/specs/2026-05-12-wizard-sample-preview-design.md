# Wizard Sample-Brief Preview — Design

**Status:** approved 2026-05-12
**Author:** Mr. V + assistant (brainstorming session)
**Implements:** "Include the Brief in the basalt init wizard for first-time users" — make the magic moment land inside the first run instead of being a separate "now run index, now run brief" two-step.

---

## 1. Goal

A first-time user runs `basalt init`, completes the wizard, and *sees what a Brief looks like* before the wizard exits — instantly, without indexing their vault, without requiring Ollama to be running, without leaving any state behind on their machine other than the config they explicitly approved.

The wedge: today the wizard ends with "Set." + "Next, build the index: basalt index". The user has to wait minutes for the magic moment. This change lands the magic moment inside the wizard itself.

## 2. Non-goals

- Not running embedding at wizard time. (Forced live runs introduce a 5s+ wait and depend on Ollama being up — which is exactly the state a first-time user is most likely *not* in.)
- Not a video / GIF / animated render. One static, instant moment.
- Not re-using the `~/.basalt/demo.db` cache that `basalt demo` builds. Different file, different lifecycle.
- Not running brief against the user's real vault. That's `basalt index && basalt brief`, the next two commands.
- Not adding telemetry of any kind.

## 3. Decisions locked

| # | Question | Decision |
|---|----------|----------|
| 1 | Position in flow | **C** — closing payoff, right after "⬡ Set." and before "Next, build the index: basalt index" |
| 2 | What renders | **D** — full Buried Insight section + one dim line naming the four other verbs |
| 3 | How generated | **B** — pre-built SQLite DB shipped in the wheel |
| 4 | Trigger | **A + C** — `interactive AND existing is None` (first-run, interactive only) |

## 4. Architecture

### 4.1 Data flow

```
basalt init (first run, interactive)
        │
        ├─ wizard prompts
        ├─ summary panel
        ├─ confirm "Write config?" → Yes
        ├─ atomic write to ~/.config/basalt/config.toml
        ├─ print "⬡ Set. <path>"
        │
        ├─ [NEW] _render_sample_preview(console)
        │     │
        │     ├─ resolve packaged DB via importlib.resources
        │     ├─ open sqlite read-only
        │     ├─ find_buried_insights(conn, top_n=1)
        │     ├─ render via existing buried-insight renderer
        │     └─ print four-verb roster line
        │
        └─ print "Next, build the index: basalt index"
```

### 4.2 Gating logic

```python
should_preview = (
    interactive               # TTY + not --yes + not --no-input
    and existing is None      # first-run only (no prior config detected)
    and not _PLAIN_STDOUT     # piped stdout suppresses
)
```

All three conditions already exist in `wizard.py`. The new code is a single conditional call.

### 4.3 Components

| File | Status | Change |
|------|--------|--------|
| `src/basalt/wizard.py` | modify | Add `_render_sample_preview(console)`. Call it from `run_wizard` after the `Set.` line, before the "Next, build the index" line, gated by `should_preview`. |
| `src/basalt/cli.py` | modify | Extract a thin helper `render_buried_insight_from_db(db_path, console)` from the existing buried-insight render block in `cmd_brief`. Reused by both `cmd_brief` and the wizard preview. Pure refactor — `cmd_brief` behavior unchanged. |
| `scripts/build_demo_db.py` | new | Release-time script. Removes old `src/basalt/data/demo.db`, runs `basalt index` against `examples/sample-vault/`, validates `find_buried_insights` returns ≥1 result, leaves the file in place. |
| `src/basalt/data/__init__.py` | new (empty) | Makes `basalt.data` a package so `importlib.resources.files("basalt.data")` works. |
| `src/basalt/data/demo.db` | new (generated) | The pre-built sample DB. Committed to the repo. ~150KB. |
| `pyproject.toml` | modify | Add `"src/basalt/data/*"` to wheel package data so the DB ships in the wheel. |
| `tests/test_wizard.py` | modify | 5 new tests (see §7). |

## 5. Copy

```
  ⬡  Set. /Users/.../config.toml

  ─── a Brief looks like this ─── (on the bundled sample, 24 notes)

THE BURIED INSIGHT
─────────────────────
vault age: 249d  ·  thresholds: age≥124d  dormant≥41d  recent≤124d

On 2025-09-12 you wrote, in 02-Projects/SignalBot/HYPOTHESIS.md:

  The sustainable edge isn't speed alone — it's speed + intelligence.
  (callout body)

Since then, 5 notes link back.
You haven't returned to this claim since you wrote it.

   → 02-Projects/SignalBot/PHASE2.md  (2026-03-14)
   → 02-Projects/SignalBot/BACKTEST.md  (2026-04-05)
   → 06-Meetings/Strategy-Review.md  (2026-04-10)
   → 02-Projects/SignalBot/CALIBRATION.md  (2026-04-15)
   → 02-Projects/Atlas/Atlas.md  (2026-04-30)

   ⊘ Falsification — this is wrong if:
      • wrong if no new note links to or semantically validates
        02-Projects/SignalBot/HYPOTHESIS.md within 60 days
      • wrong if 02-Projects/SignalBot/HYPOTHESIS.md loses more than 30% of
        its content (you actively dismantled the claim)
      • wrong if 02-Projects/SignalBot/HYPOTHESIS.md is deleted (you've
        moved on from this claim)

   ▸ Promote to thesis     ▸ Open all     ▸ Snooze

  Five verbs in all:
    Buried Insight · Connection · Contradiction · Implicit Thesis · Drift
    On your vault, all five run with  basalt brief --section all

  Next, build the index:  basalt index
```

### Copy rationale
- **Lede line** `─── a Brief looks like this ─── (on the bundled sample, 24 notes)` — does the "this isn't your data" disclosure quietly. The parenthetical (`24 notes`) makes the scale comparison obvious without saying "this is a DEMO."
- **Buried insight body** — verbatim render of the existing renderer, no changes. Falsification rules stay because they're the wedge ("calibration layer no competitor ships").
- **Four-verb roster** — names all five so the user knows the surface is plural, without rendering five sections of output.
- **Final line preserved** — `Next, build the index: basalt index` stays as the wizard's last word.

## 6. Failure modes

| Condition | Behavior |
|-----------|----------|
| `src/basalt/data/demo.db` missing (running from source without the build script) | Catch `FileNotFoundError`/`ModuleNotFoundError`/`OSError` around resource read. Print: `[dim](sample preview unavailable — run \`basalt demo\` to see one.)[/]`. Wizard exits 0. |
| DB present but unreadable (schema drift across releases) | Catch `sqlite3.DatabaseError`. Same fallback line. Wizard exits 0. |
| `find_buried_insights` returns no rows (sample data degraded) | Same fallback line. Wizard exits 0. |
| Renderer raises unexpected error | Catch `Exception` at the preview-call boundary. Print fallback line. Wizard exits 0. *Never* let the preview break the init flow. |
| NO_COLOR / piped stdout | `_PLAIN_STDOUT` is already True in those cases. Existing wizard guard suppresses the preview entirely. |
| `--yes` mode | `interactive` is False → preview skipped by gating logic. |
| Reconfigure (existing config) | `existing is None` is False → preview skipped. |

## 7. Tests (added to `tests/test_wizard.py`)

1. **`test_preview_renders_when_interactive_first_run`** — mock TTY True, ensure no existing config, run wizard with mocked questionary answers + Confirm Yes, capture stdout, assert it contains `"THE BURIED INSIGHT"` and `"Five verbs in all"`.
2. **`test_preview_skipped_on_yes_flag`** — call `run_wizard(yes=True)` with no existing config, env vars set so it succeeds non-interactively, assert stdout does NOT contain `"THE BURIED INSIGHT"`.
3. **`test_preview_skipped_on_reconfigure`** — write a config first, then run wizard interactively with Confirm Yes for reconfigure, assert preview does NOT render (because `existing is not None`).
4. **`test_packaged_demo_db_loads_and_returns_insight`** — open the wheel-shipped `basalt.data` DB via `importlib.resources`, call `find_buried_insights(conn, top_n=1)`, assert returns ≥1 result with non-empty quote.
5. **`test_missing_demo_db_falls_back_gracefully`** — monkeypatch the resource loader to raise `FileNotFoundError`, run preview helper, assert stdout contains `"sample preview unavailable"` and no exception leaks.

Existing 48/48 must continue passing.

## 8. Release operations

### 8.1 `scripts/build_demo_db.py`

Idempotent script that:

1. Removes `src/basalt/data/demo.db` if it exists.
2. Runs the existing index pipeline against `examples/sample-vault/` with `nomic-embed-text` as the embedding model. Requires Ollama running at *release time* (not user time).
3. Validates the output: `find_buried_insights(conn, top_n=1)` must return ≥1 result with a non-empty load-bearing quote.
4. Validates DB size is within 50KB-500KB (sanity-bound to catch corrupted builds).
5. Prints `✓ demo.db rebuilt: {size} bytes, top insight: {first 60 chars of quote}`.

### 8.2 Trigger conditions for re-running

- Embedding model in code changes from `nomic-embed-text` to anything else.
- `examples/sample-vault/` is edited.
- Buried-insight algorithm changes in a way that would affect the bundled output.
- Schema migration to the `notes` / `embeddings` / `briefs` tables.

Per-release SOP: run `python scripts/build_demo_db.py`, inspect output, commit if changed.

### 8.3 CI guard (optional, future)

Pre-publish check: if `examples/sample-vault/` was modified in a commit and `src/basalt/data/demo.db` was not modified in the same or later commit, fail the build. (Not implemented in v1 — manual SOP first.)

## 9. Scope / what this is NOT

- Not running embed at wizard time. The bundled DB has embeddings pre-baked with `nomic-embed-text`. If the user picks a different embed model in the wizard, that's fine — the sample DB is read-only and doesn't influence their real index.
- Not the `~/.basalt/demo.db` cache (which `basalt demo` builds). Different file, different lifecycle.
- Not a TUI / animated render. One static, instant moment.
- Not first-run telemetry. No counters, no analytics.

## 10. Open Questions

None. All four decisions locked above; failure modes enumerated; copy frozen.

## 11. References

- Brainstorming session transcript: this conversation, 2026-05-12.
- Prior wizard work: `src/basalt/wizard.py`, shipped 2026-05-12 with 48/48 tests.
- Voice anchor: `~/virtuoso-vault/02-Projects/Basalt/README.md` + `Site-Spec.md`.
- Whimsy review: agent reply 2026-05-12, "7 changes for `basalt init`".
- UX review: agent reply 2026-05-12, "12 findings + top 3 fixes".
- Buried-insight wedge: `~/virtuoso-vault/02-Projects/Basalt/03-Specs/The-First-Brief-Spec.md`.

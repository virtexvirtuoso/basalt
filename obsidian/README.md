# Basalt — Obsidian plugin

> Reads your vault and surfaces what you believe but never wrote down. Inside Obsidian.

This plugin is a thin TypeScript shell over [`basalt-mcp`](https://github.com/virtexvirtuoso/basalt) — the local MCP server that ships with `basalt-vault`. All verb logic stays in Python; the plugin spawns the binary, calls tools over JSON-RPC stdio, and renders the responses inside Obsidian.

**Standalone. Read-only. Local-first.** The plugin never writes to your vault. The `basalt-mcp` subprocess never touches your files; it reads from a SQLite index at `~/.basalt/basalt.db`. No network calls in the Open tier.

---

## Prereqs

You need `basalt-vault` installed locally with the `[mcp]` extras:

```bash
pip install 'basalt-vault[mcp]'
```

Then set up the index — the new `basalt init` wizard (v0.0.15+) walks you through it in about ten seconds:

```bash
basalt init           # interactive — pick vault, ollama URL, embed model
basalt index          # build the SQLite index (~1-3 min for ~2k notes)
```

The plugin reads the resulting SQLite index. Re-run `basalt index` whenever you want fresh embeddings.

## Install (manual, until Community Plugins approval lands)

You need three built artifacts: `manifest.json`, `main.js`, and `styles.css`. Either:

**From a release** (recommended once published): grab them from the [latest basalt release](https://github.com/virtexvirtuoso/basalt/releases) under "Assets."

**From source:**
```bash
git clone https://github.com/virtexvirtuoso/basalt.git
cd basalt/obsidian
npm install && npm run build      # produces main.js
```

Then:

1. Copy `manifest.json`, `main.js`, `styles.css` into `<your-vault>/.obsidian/plugins/basalt/`
2. Restart Obsidian → **Settings → Community plugins** → turn on community plugins if needed → enable **Basalt**

## Troubleshooting

### macOS — `Basalt error: MCP initialize rejected: subprocess error: spawn basalt-mcp ENOENT`

This is the most common first-run error on macOS. It means Obsidian (a GUI app launched from the Dock or Spotlight) doesn't inherit your terminal's `$PATH`, so it can't find `basalt-mcp` by name. Two ways to fix it.

**Per-plugin fix (10 seconds, no reboot):**
1. **Settings → Basalt → basalt-mcp command** field
2. Paste the absolute path. Find yours with `which basalt-mcp` in a terminal — typically one of:
   - `/Library/Frameworks/Python.framework/Versions/3.12/bin/basalt-mcp` (python.org installer)
   - `/usr/local/bin/basalt-mcp` (Homebrew or manual symlink)
   - `/opt/homebrew/bin/basalt-mcp` (Apple Silicon Homebrew)
   - `~/.venv/bin/basalt-mcp` (your venv)
3. Close settings → try the command again.

**System-wide fix (one reboot, works for any future GUI app):**
```bash
sudo launchctl config user path "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
```
Then reboot. After that, leaving the plugin's `basalt-mcp command` field empty works.

### Plugin loaded but no commands appear

If you don't see `Basalt:` entries in the command palette, the plugin probably loaded but failed to register commands. Check the **Developer Console** (`Cmd-Option-I`) for errors and confirm the plugin is **toggled on** in Community plugins (not just installed).

### Brief modal pops empty / "No findings"

The index is empty or out of date. Run `basalt index` in a terminal and verify it finds notes. The plugin reads from the SQLite DB at the path in **Settings → Basalt → DB path override** (default `~/.basalt/basalt.db`).

## Commands

Open the command palette (`⌘P` / `Ctrl+P`) and type `Basalt`:

| Command | What it does |
|---|---|
| **Compile Brief (all sections)** | Surfaces Buried Insight + Connection + Contradiction + Implicit Thesis + Drift in one modal |
| **Find buried insight** | A note you wrote once and never returned to, that recent notes still cite |
| **Find connections** | Pairs of notes in different folders with no wikilink, but the same idea |
| **Find contradictions** *(v0)* | Same-topic notes whose load-bearing sentences appear to disagree |
| **Find implicit theses** *(v0)* | Clusters of notes converging on an unnamed through-line |
| **Find drift** | Projects whose stated priority (note count) diverges from lived priority (daily-note mentions over 30d) |
| **Audit past briefs** | Re-evaluates pending findings against current vault state — shows which past briefs aged well |

Every finding ships with **falsification rules** — *"this is wrong if you ever observe X."* Run audit weekly; your track record compounds.

The findings render with clickable Obsidian wikilinks — click any cited note path to jump straight to it in the editor.

## Settings

- **basalt-mcp command** — path to the executable. Leave empty if it's on your `$PATH` (see Troubleshooting if you hit ENOENT).
- **Vault path override** — defaults to `BASALT_VAULT` env or `~/virtuoso-vault`.
- **DB path override** — defaults to `~/.basalt/basalt.db`.
- **Findings per section** — 1–10 (default 2).

## How it differs from other Obsidian "second-brain" plugins

| | Basalt | Smart Connections | obsidian-second-brain (eugeniughelbur) |
|---|---|---|---|
| Writes to vault | **Never** | No | **Yes** ("vault rewrites itself") |
| Requires Claude Code | No | No | **Yes** |
| Local LLM-free path | **Yes** (Ollama embeddings only) | Hybrid | No (cloud Claude inference) |
| Calibration / falsification of past output | **Yes** | No | No |
| Verb count | 5 + Audit | 1 (similarity browse) | varies |

## Compatibility

- **basalt-vault:** v0.0.5+ (MCP server) · v0.0.15+ recommended (config-aware CLI, doctor command for setup verification)
- **Obsidian:** 1.5.0+ (desktop only — `isDesktopOnly: true` in manifest)
- **Platforms:** macOS, Linux, Windows. macOS users may need the PATH workaround above.

## License

[MIT](LICENSE)

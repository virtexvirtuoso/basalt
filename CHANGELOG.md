# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Planned (carried)
- Drift verb (needs daily-note time-marker parser)
- Implicit Thesis v1 — LLM synthesis pass that names the cluster's through-line
- Contradiction v1 — LLM-based pairwise compatibility classifier
- Obsidian plugin v0.2 — sidebar pane, status bar, Smart Connections parasitism
- One-click installer per Installer-Scope-2026-05-08.md
- Calibration v1: word-count-at-log-time so `candidate_shrinks` rules can fire
- Implicit Thesis verb (needs claim ledger)
- Drift verb (needs daily-note time-marker parser)
- Contradiction v1 — LLM-based pairwise compatibility classifier on top of v0 candidates
- Obsidian plugin shell
- PyPI publish of `basalt-vault[mcp]` to make `pip install basalt-vault[mcp]` work without source clone
- MCP Registry submission once PyPI is live
- Calibration v1: word-count-at-log-time so `candidate_shrinks` and `either_shrinks` rules can fire

## [0.0.7] — 2026-05-08

### Added — Implicit Thesis verb (closes the 4/4 gallery promise)

The site advertises four unlocks: Implicit Thesis, Contradiction, Drift,
Connection. As of v0.0.7, three of four ship in code; only Drift remains
as a Phase-1 item. The site's `basalt brief --section all` now produces a
finding for every promised unlock except Drift.

- **`basalt thesis`** / **`basalt brief --section implicit-thesis`** —
  surfaces clusters of 3-15 notes converging on an unnamed through-line.
  v0 is a tight-neighborhood (near-clique) heuristic: every pair in a
  cluster must share cosine ≥ 0.72. The centroid's load-bearing sentence
  stands as the proxy thesis statement; the user (or v1's LLM pass)
  names the actual thesis.
- **`basalt_thesis` MCP tool** — added to the MCP server alongside the
  4 existing tools. 5 tools now registered.
- **Falsification rules** for thesis findings: `centroid_deleted`,
  `cluster_dispersed` (>2 members deleted), `no_new_rephrasing`.
- **Calibration logging** — every thesis cluster gets a stable
  finding_key derived from the sorted member paths, so re-running
  `basalt brief` doesn't double-log even when the centroid changes.
- 2 new smoke tests verify the cross-folder cluster surfaces and that
  the diversity gate suppresses single-folder same-day clusters. 23/23
  tests pass.

### Engineering

- New `implicit_thesis.py` (~270 LOC). Tight-neighborhood algorithm
  outperforms connected-components on real vaults — at 1683 notes,
  CC collapses into a single 1452-member component at any practical
  threshold; near-clique clustering finds bounded thematic clusters.
- `audit.py` extended with thesis rule generation, finding_key, and
  payload serialization.
- `serialize.py` extended with `implicit_thesis_to_dict`.
- `cli.py` SECTIONS_SHIPPED now includes `implicit-thesis`; only `drift`
  remains in SECTIONS_PLANNED.
- `mcp_server.py` exposes `basalt_thesis` as a 5th tool with the same
  read-only annotations as the others.

## [0.0.6] — 2026-05-08

### Added — CLI brand chrome

The Basalt mark now greets you. Designed by the whimsy-injector agent
under tight constraints (no emojis, no animations, no mascot, no
"awesome!" — geological-editorial register only). Static, restrained,
once-per-run.

- **Banner** at the top of `basalt brief`, `basalt audit`, `basalt demo`,
  `basalt about` — open hexagon mark + wordmark with italic period in
  basalt amber + one-line tagline. Suppressed when stdout is piped
  (keeps `basalt brief | jq` clean).
- **`basalt about`** — new command. Wordmark + the *"Three commands.
  Sixty seconds. Runs on your laptop"* line + a single geological
  metaphor that earns its keep ("Basalt forms in slow cooling —
  hexagonal columns, brittle to impact, durable to weather. The vault
  is the same.") + schema version + the wedge phrase.
- **Sign-off** line at the end of every Brief and Audit run replaces
  the bare `console.rule()` — *"⎯⎯⎯  end of brief.  the vault keeps
  the receipts."* — once-per-run, dry, no animations.
- **First-run greeting** in `basalt index` — exactly once per DB,
  tracked in the `meta` table. Reinforces the read-only promise the
  moment the user hands Basalt their vault for the first time.
- **Microcopy upgrades** in 5 spots — empty-result messages, `--help`
  tagline, audit no-change line. Voice shifts from flat error-message
  cadence to senior-collaborator marginalia.

### Engineering

- 4 new helpers in `cli.py`: `_print_banner`, `_print_signoff`,
  `_maybe_first_run_greeting`, plus the `cmd_about` command.
- All branding gated on `_PLAIN_STDOUT` — banner / signoff / greeting
  invisible when piped. `basalt about` falls back to plain text.
- Unicode box-drawing diagonals (U+2571 / U+2572) replace ASCII
  backslashes in the banner so Rich markup doesn't escape them.

## [0.0.5] — 2026-05-08

### Added — MCP server

The strategic doorway. `obsidian-second-brain` (eugeniughelbur, 968 stars)
is locked into Claude Code as a skill. An MCP server reaches Claude
Desktop, Cursor, Cline, Zed, and VS Code Copilot in one shot — and is
structurally outside his category because read-only MCP tool semantics
are incompatible with his "vault rewrites itself" architecture.

- **`pip install basalt-vault[mcp]`** — new optional extras; installs the
  official `mcp` package with bundled FastMCP 1.x.
- **`basalt-mcp` console script** — runs the MCP server over stdio.
  `--vault` / `--db` args + `BASALT_VAULT` / `BASALT_DB` env vars.
- **4 tools registered:** `basalt_brief`, `basalt_connection`,
  `basalt_contradiction`, `basalt_audit`. All read-only on the vault;
  only `basalt_audit` mutates the local `briefs` calibration table.
- **`basalt_index` deliberately not exposed** — too heavy, runs Ollama,
  takes minutes on a 1,683-note vault. Document the install as
  *"CLI indexes, MCP reads."*
- **No resources, no prompts, no sampling in v0** per [[MCP-Ship-Plan-2026-05-08]]
  §3.2 — resources invert the user-asks-the-question shape; sampling
  breaks the no-network promise.
- **2 new smoke tests** verify the server module loads with exactly the
  four expected tools and `basalt_audit` returns clean output on a fresh
  DB. 21/21 pass.

### Engineering

- New `mcp_server.py` (~280 LOC). Uses the `mcp.server.fastmcp.FastMCP`
  instance. Per-call DB open/close keeps the server stateless and
  tolerant to DB path changes.
- `pyproject.toml`: bumped to `0.0.5`, added `[project.optional-dependencies] mcp`,
  declared `basalt-mcp` console script.
- README gained a full **MCP integration** section with Claude Desktop
  config example.

## [0.0.4] — 2026-05-08

### Added — `--format=json` across the CLI

Foundation for the upcoming MCP server. Every verb now has a stable
machine-readable schema, ready to pipe into `jq` or wrap as an MCP tool.

- **`basalt brief --format json`** — full JSON document with schema version,
  track record, and findings grouped by verb. Each finding ships with its
  falsification rules so the calibration contract reaches the consumer.
- **`basalt connection --format json`** — single-verb pairs with similarity,
  hub densities, falsification rules.
- **`basalt contradiction --format json`** — pairs with topical similarity,
  contradiction signals fired, v0-heuristic version flag, falsification.
- **`basalt audit --format json`** — verdict list + track record. MCP hosts
  can poll this to surface track-record changes.
- **`SCHEMA_VERSION = 1`** — explicit versioning so future schema breaks are
  observable.
- **`isatty()` color auto-detection** — `basalt brief | less` no longer
  produces ANSI escape soup. Rich rendering only when stdout is a real TTY.
- 1 new smoke test verifies serializer schemas. 19/19 pass.

### Engineering

- New `serialize.py` module — ~140 LOC of pure mapping functions. No state.
  Reusable directly from the planned `mcp_server.py`.

## [0.0.3] — 2026-05-08

### Added — Calibration Layer

The single feature that converts Basalt from "AI summary tool" into
"research log." Every finding now ships with falsifiable claims, every past
finding gets re-evaluated, and every Brief shows your track record at the top.

- **`basalt audit`** — re-walks pending briefs against current vault state,
  applies each finding's falsification rules, updates status to `confirmed`
  or `falsified` with verdict reason. Shows track-record bar.
- **Falsification rules** rendered inline in every Brief finding — *"this is
  wrong if you ever observe X."* Three rules per Buried Insight, two per
  Connection, two per Contradiction.
- **Track-record header** at top of `basalt brief` — confirmed/pending/falsified
  bar over the last 90 days, once any past briefs exist.
- **`briefs` SQLite table** logs every Brief finding with stable `finding_key`
  for idempotency. Re-running `basalt brief` does not double-log identical findings.
- **5 new smoke tests** covering rule generation per verb, idempotent recording,
  end-to-end falsify (still_unlinked grace), end-to-end confirm (linked
  connection), and track-record counting. 18/18 tests pass in <1s.

### Engineering

- New module `audit.py` — ~340 LOC. Schema, rule generation, recording,
  evaluation, track record. Verb modules unchanged — calibration stays in
  its own file.
- v0 limits called out honestly: `candidate_shrinks` and `either_shrinks`
  rules need original word_count at log time; v0 finding payload doesn't
  preserve it. Rules log as pending; v1 fixes this.

### Why this matters

No competitor in this category ships calibration of past output. Eugeniughelbur's
*"vault rewrites itself"* model destroys the historical signal needed for it.
This is the single feature that compounds Basalt's switching cost: the longer
a user runs Basalt, the more valuable their track record becomes.

## [0.0.2] — 2026-05-08

### Added
- **`basalt connection`** — surfaces pairs of notes in *different folders*, with
  no wikilink between them, whose embeddings say they are the same idea.
  Reuses Buried Insight's hub-density penalty and load-bearing-sentence picker.
  Includes a diversity pass so a single hub note can't dominate the ranking.
- **`basalt contradiction`** — v0 heuristic. Surfaces *candidate* contradictions:
  pairs of same-topic notes where load-bearing claims carry asymmetric negation,
  reversal markers (*"actually"*, *"I was wrong"*, *"turns out"*), or polarity
  pairs (*ship*↔*kill*, *works*↔*broken*, etc.). Honest disclosure: heuristic
  produces false positives — output is candidates for the user (or an LLM
  classifier in v1) to verify, not verdicts.
- **`basalt brief --section <name>`** — accepts `buried-insight`, `connection`,
  `contradiction`, or `all`. Bad-parameter errors clearly distinguish *unbuilt*
  (Implicit Thesis, Drift) from *unknown* sections.
- **`basalt demo --section <name>`** — same section selector for the bundled
  sample-vault demo run.
- 6 new smoke tests covering folder-boundary extraction, contradiction lexical
  signals (negation, reversal, polarity pairs), and end-to-end Connection +
  Contradiction on synthetic in-memory vaults. All 13 tests pass in <1s without
  Ollama.

### Engineering
- ~440 LOC added across `connection.py`, `contradiction.py`, and `cli.py`
  refactor. New modules reuse the existing buried.py primitives — no
  duplicated logic for hub density, claim-quote extraction, or markdown
  stripping.
- 4 of 4 site-advertised unlocks now have a story: Connection (shipped),
  Contradiction (shipped, v0), Implicit Thesis + Drift (clearly labelled
  "needs Phase 1 component" in CLI errors).

## [0.0.1] — 2026-05-08

### Added
- `basalt index` — walks a Markdown vault, parses frontmatter, builds a
  wikilink graph, embeds every note via Ollama. ~3.5s/100 notes on M1.
- `basalt brief --section=buried-insight` — surfaces the strongest "buried
  insight" in your vault: a note you wrote once and never returned to, that
  recent notes still cite or semantically validate.
- `basalt demo` — indexes a 14-note synthetic sample vault and runs a brief.
  Useful for trying Basalt before pointing it at your own corpus.
- Sentence-aware quote extraction with markdown stripping.
- Load-bearing sentence picker — chooses the punchline of a passage
  (em-dash construction, negation+assertion, conclusion-opener) over the
  setup line.
- Hub-note penalty — outgoing-link-density-per-100-words filter that
  hard-excludes MOCs above 1.5 and soft-penalizes the 0.5–1.5 gray zone.
- Vault-age-aware thresholds — derives `(min_age, dormant, recent)` from
  the oldest note's age. Capped to [60–365] / [30–180] / [60–365].
- Multi-result mode — `basalt brief --top N` (max 10).
- 7 smoke tests, no live Ollama required (embeddings mocked).

### Engineering
- Python 3.12, Typer CLI, Ollama for embeddings, SQLite + numpy for storage
  and similarity (no sqlite-vec — macOS python.org doesn't load extensions).
- ~750 LOC across vault.py, index.py, embed.py, buried.py, cli.py.
- MIT license.

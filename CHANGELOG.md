# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Planned
- MCP server wrapper exposing verbs as tools for any MCP host
- Implicit Thesis verb (needs claim ledger)
- Drift verb (needs daily-note time-marker parser)
- Contradiction v1 — LLM-based pairwise compatibility classifier on top of v0 candidates
- Obsidian plugin shell
- PyPI distribution as `basalt-vault` (the name `basalt` is taken)
- Calibration v1: word-count-at-log-time so `candidate_shrinks` and `either_shrinks` rules can fire

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

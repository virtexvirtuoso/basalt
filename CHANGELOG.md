# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Planned
- MCP server wrapper exposing verbs as tools for any MCP host
- Brief sections beyond Buried Insight: Implicit Thesis, Contradiction, Drift, Action Avoided
- Obsidian plugin shell
- PyPI distribution as `basalt-vault` (the name `basalt` is taken)

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

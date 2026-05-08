# Contributing

Thanks for taking a look. Basalt is in Phase 0 — small, opinionated, moving fast.

## Before you open a PR

1. **Open an issue first** for anything non-trivial. The project's scope is
   intentionally narrow; aligning before writing code saves both of us time.
2. **Run the tests locally:**
   ```bash
   .venv/bin/python -m pytest tests/ -v
   ```
   All seven should pass without Ollama running (embeddings are mocked).
3. **Try the demo:**
   ```bash
   basalt demo
   ```
   This requires Ollama with `nomic-embed-text` pulled.

## What's in scope

- New verbs for the Brief library (Implicit Thesis, Contradiction, Drift, Action Avoided)
- Better sentence/quote extraction
- Performance: incremental re-index, faster embedding pipelines
- Tests
- Documentation (especially of the algorithm in `buried.py`)

## What's out of scope (for now)

- Note editing (Basalt reads notes; it doesn't replace your editor)
- File-system reorganization (Basalt never moves notes)
- Multi-user / SaaS features (Pro tier will be a separate effort)

## Code style

- Follow the existing module shape: small, named, single-purpose.
- Prefer plain SQLite + numpy over heavier dependencies.
- Type hints required on public functions.
- Keep tests deterministic — mock external services (Ollama, network).

## Questions

Email Fernando: fernando@virtuosocrypto.com.

Security issues: see [SECURITY.md](SECURITY.md).

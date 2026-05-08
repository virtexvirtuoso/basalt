# Basalt

> Reads your Markdown vault and surfaces what you believe but never wrote down.

![Basalt — basalt demo running on a 14-note sample vault](docs/demo.gif)

Basalt is a knowledge operating system that compiles a longitudinal model
of *you* — claims, priorities, drift, theses — and exposes it as cognitive
verbs. It sits atop your existing vault. It does not replace your editor.
It runs locally.

The signature output is **The First Brief** — a single page titled *"What
your vault knows about you that you haven't said,"* with citation-grounded
sections (Implicit Thesis, Contradiction, Drift, Buried Insight, Action
Avoided), each ending in a one-click commit.

Status: **Phase 0 — Compiler skeleton in build.** Buried Insight section
shipped and passing on a 1,680-note vault. See [virtuosoai.dev/basalt](https://virtuosoai.dev/basalt/).

---

## Prereqs

- **Python 3.12+**
- **[Ollama](https://ollama.com)** running locally
- The embedding model: `ollama pull nomic-embed-text`
- A Markdown vault (Obsidian, Logseq, plain folder of `.md` — anything works)

## Quickstart

> **Heads up:** the PyPI name `basalt` is already taken by an unrelated
> package. Install from source for now. PyPI distribution as `basalt-vault`
> coming once Phase 0 lands.

```bash
git clone https://github.com/virtexvirtuoso/basalt.git
cd basalt
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Try the demo (no vault required — uses a sample vault bundled with the repo):

```bash
basalt demo
```

Or point at your own vault:

```bash
basalt index --vault ~/path/to/your-vault
basalt brief
```

Expected output:

```
THE BURIED INSIGHT
─────────────────────
vault age: 244d  ·  thresholds: age≥122d  dormant≥40d  recent≤122d

On 2025-09-12 you wrote, in 02-Projects/SignalBot/HYPOTHESIS.md:

  The sustainable edge isn't speed alone — it's speed + intelligence.
  (callout body)

Since then, 4 notes link back.
You haven't returned to this claim since you wrote it.

   → 02-Projects/SignalBot/PHASE2.md            (2026-03-14)
   → 02-Projects/SignalBot/BACKTEST.md          (2026-03-21)
   → 02-Projects/SignalBot/CALIBRATION.md       (2026-04-02)
   → 02-Projects/SignalBot/PRODUCTION-NOTES.md  (2026-04-18)

   ▸ Promote to thesis     ▸ Open all     ▸ Snooze
```

## Commands

| Command | What it does |
|---------|--------------|
| `basalt demo` | Index the bundled sample vault and run a brief — no setup, no vault needed |
| `basalt index --vault PATH` | Walk vault, parse frontmatter, build link graph, embed every note |
| `basalt brief` | Surface the strongest buried insight (default top 1) |
| `basalt brief --top 3` | Surface the top 3 |
| `basalt brief --strict-defaults` | Use fixed 180/90/180 thresholds instead of vault-age-aware |

Run `basalt --help` for everything.

## How it works

| Layer | Tech |
|-------|------|
| **Substrate** | Your existing Markdown vault — never moved, never modified without consent |
| **Compiler** | SQLite (notes, links, embeddings) + numpy similarity + content-hash incremental |
| **Embeddings** | Ollama `nomic-embed-text` by default; `bge-m3` and Qwen3-Embedding-8B coming for the Pro tier |
| **Verbs** | Cognitive operations exposed as CLI commands (MCP server wrapper next) |

Currently shipped: **Buried Insight** — finds notes you wrote, never came back
to, that recent notes still cite. Quote extraction is sentence-aware and
load-bearing (picks the punchline, not the setup); hub-note penalty filters
MOCs out of candidacy; thresholds adapt to your vault's actual age.

## Privacy

Local-first by default. Your vault is read from disk; embeddings are computed
by your local Ollama; the SQLite index lives at `~/.basalt/basalt.db`. **No
network calls leave your machine in the Open tier.** See [PRIVACY.md](PRIVACY.md)
for the full posture, and [SECURITY.md](SECURITY.md) for the threat model.

## Contributing

Open an issue first for anything non-trivial. Follow the existing module shape
— small, named, single-purpose.

## License

[MIT](LICENSE) — Fernando Villar / Virtuoso Crypto, 2026.

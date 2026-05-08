# Privacy

Basalt's design principle is **local-first by default, opt-in for everything else.**
Your vault is private. The model Basalt builds of you is private. This
document declares what that means concretely.

---

## Open tier (free, current state)

| Surface | What it does | Where data goes |
|---------|--------------|-----------------|
| `basalt index` | Reads `.md` files from a directory you point at | Stays on disk |
| Embedding pipeline | Sends note content to **your local Ollama** | Localhost only |
| SQLite index | Notes, links, embeddings, content hashes | `~/.basalt/basalt.db` on your machine |
| `basalt brief` | Reads the SQLite index, runs algorithms locally | No network calls |

**No telemetry. No analytics. No phone-home. No account.** If you sever your
network connection mid-run, Basalt continues working.

## Pro tier (planned, not yet shipped)

The Pro tier introduces verbs that need cloud-quality reasoning (`/contradict`,
`/emerge`, `/thesis`, `/drift`). For those, Basalt will need to send vault
*excerpts* to a frontier LLM provider (Anthropic, OpenAI, or whichever you
configure).

When Pro ships, this section will declare:

- Which verbs send data to cloud providers
- Which providers the user can choose
- The provider's data-retention and training-opt-out posture
- The maximum content sent per call (chunk-bounded)
- A dry-run mode that shows the exact prompt before sending
- A capability toggle to disable any cloud verb

**Until Pro ships, none of this applies. The Open tier remains 100% local.**

## Agentic tier (planned)

When Basalt's verbs get the ability to *write to the world* (send email,
post to Slack, place orders), every action will:

- Require explicit user confirmation (no silent commits)
- Be logged to an append-only audit trail at `~/.basalt/audit.log`
- Be capability-scoped (per-verb allow-list)
- Default to dry-run mode

See [SECURITY.md](SECURITY.md) for the threat model.

## What Basalt never does

- Send your vault content to anyone without explicit per-call consent
- Train any model on your data
- Phone home for analytics, crash reports, or feature flags
- Store your data on any server we control (the Open tier has no servers)
- Use cookies, sessions, or fingerprinting on the website (the landing page is
  a single static HTML file with Google Fonts)

## Your data on disk

The SQLite index at `~/.basalt/basalt.db` is currently **plaintext**. If your
laptop is stolen, the contents are readable. Encryption-at-rest (SQLCipher
bound to the OS keychain) is on the roadmap; until then, treat the index with
the same care you'd treat your vault directory.

## Deleting Basalt

```bash
rm -rf ~/.basalt
pip uninstall basalt
```

That's it. There is nowhere else.

## Contact

Privacy concerns or questions: fernando@virtuosocrypto.com.
Security disclosures: see [SECURITY.md](SECURITY.md).

---

*Last updated: 2026-05-08. Material changes will be flagged in the changelog.*

# Security

Basalt reads your entire knowledge vault. That's an unusual surface area for
a CLI tool. This document is the threat model, the trust boundary map, and
the disclosure process.

> **Status: foundational draft, 2026-05-08.** As Pro and Agentic tiers ship,
> this document expands. The Open tier (current) is local-only and has the
> smallest possible surface.

---

## Trust boundaries

```
┌──────────────────────────────────────────────────────────────────┐
│  USER MACHINE                                                    │
│                                                                  │
│   ┌────────────┐     ┌──────────────┐     ┌─────────────────┐    │
│   │ Markdown   │────▶│ Basalt       │────▶│ ~/.basalt/      │    │
│   │ vault      │     │ Compiler     │     │   basalt.db     │    │
│   │ (.md files)│     │ (Python)     │     │   (SQLite)      │    │
│   └────────────┘     └──────┬───────┘     └─────────────────┘    │
│                             │                                    │
│                             ▼                                    │
│                      ┌─────────────┐                             │
│                      │ Ollama      │                             │
│                      │ (localhost  │                             │
│                      │  :11434)    │                             │
│                      └─────────────┘                             │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
                              │
                              │ NO NETWORK CROSSINGS
                              │ in the Open tier
                              ▼
                  (cloud LLM calls only when Pro tier is enabled
                   and user has explicitly configured a provider)
```

**In the Open tier, the only network call Basalt makes is `localhost:11434`
to your local Ollama daemon.** No external services are contacted. No
analytics endpoint. No update check. No telemetry.

## STRIDE per component

| Component | Spoofing | Tampering | Repudiation | Info disclosure | DoS | Elevation |
|-----------|----------|-----------|-------------|-----------------|-----|-----------|
| Vault reader | n/a (read-only) | n/a | n/a | Reads anything in the path you pass | bounded by FS | n/a |
| SQLite index | OS-level only | OS-level only | n/a | **Plaintext on disk — see Risks** | n/a | n/a |
| Ollama call | localhost-bound | localhost-bound | n/a | Embeddings on local socket | Ollama daemon resource | n/a |
| `basalt brief` output | n/a | n/a | n/a | stdout to your terminal | n/a | n/a |

## Known risks (Open tier)

| # | Risk | Severity | Status |
|---|------|----------|--------|
| 1 | **Plaintext SQLite index.** Embeddings + note content live unencrypted at `~/.basalt/basalt.db`. A laptop theft is a vault disclosure. | High | **Mitigation on roadmap:** SQLCipher bound to the OS keychain (macOS Keychain / libsecret / DPAPI). Until then, treat `.basalt/` with the same care as your vault dir. |
| 2 | **Vault path is what you pass.** Basalt reads any Markdown under the path you give it, including files you may have forgotten about. | Medium | User-driven. Be intentional about what you point at. |
| 3 | **No content filtering.** Basalt indexes whatever's in `.md` files, including secrets a user may have pasted in. | Medium | Roadmap: opt-in glob exclusions (e.g. `**/secrets/*`, `**/credentials.md`). Until then, exclude sensitive paths manually. |
| 4 | **Ollama is trusted.** Basalt sends note content to the local Ollama process, which is assumed honest. | Low | Out of scope; Ollama is a local-only service you control. |

## Pro tier (not yet shipped) — additional risks

When Pro ships, the threat model expands:

- **Cloud LLM provider** sees vault excerpts. Provider's retention and
  training posture becomes load-bearing. Mitigation: explicit per-verb opt-in,
  dry-run preview before any send, capability toggle to disable cloud verbs
  entirely, configurable provider.
- **Cross-device user-model sync** introduces a server-side surface. Mitigation:
  client-side encryption with user-held key; server sees only ciphertext.
  This document will be updated when Pro ships with the actual cryptographic
  design.

## Agentic tier (not yet shipped) — additional risks

When Layer 2 verbs ship (send email, post to Slack, etc.):

- **Action-taking surface.** A compromised Basalt could take real-world
  actions in the user's name. Mitigation: dry-run by default, mandatory
  human-confirmation step, append-only hash-chained audit log at
  `~/.basalt/audit.log`, per-verb capability tokens that can be revoked.
- **CAN-SPAM, CFAA exposure** if compromised. The capability allow-list and
  the confirmation step are the load-bearing legal mitigations.

## Reporting a vulnerability

If you find a security issue, **do not open a public issue.**

Email: **fernando@virtuosocrypto.com** with subject line `[Basalt Security]`.

We will acknowledge within 72 hours. Critical issues will be patched and
disclosed; minor issues will be patched and noted in the changelog.

## What Basalt isn't (yet)

- SOC 2 audited
- HIPAA-eligible
- GDPR Article 30 compliant for cross-org processing (planned for Enterprise tier)

If your use case requires any of those, please reach out — the Enterprise
tier is the path.

---

*Last updated: 2026-05-08.*

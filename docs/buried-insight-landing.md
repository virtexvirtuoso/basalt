# Buried Insight

> The brilliant thing you wrote and forgot about.

## What It Does

Finds notes you wrote that are both **old** and **dormant** — but have been **validated by recent activity** in your vault.

The insight isn't buried because it's wrong. It's buried because you moved on without integrating it.

## How It Works

1. **Age Gate** — Note must be ≥90 days old (or vault-age-aware threshold)
2. **Dormancy Gate** — No edits in ≥60 days
3. **Validator Scan** — Looks for recent notes (≤30 days) that explicitly link to it OR lexically overlap with it
4. **Score Ranking** — Ranks by `(age_days × dormant_days) / validator_recency`
5. **Quote Extraction** — Surfaces the load-bearing sentence from the buried note

## Example Output

```markdown
## Buried Insight

### 1. SignalBot — Original Hypothesis

**Path:** `02-Projects/SignalBot/HYPOTHESIS.md`
**Age:** 239 days old, dormant for 239 days

> The sustainable edge isn't speed alone — it's speed + intelligence.

**Validated by:**
- ✓ [[SignalBot Phase 2 — Multi-signal stack]] — updated 56 days ago
- ✓ [[SignalBot Backtest — Q1 2026]] — updated 34 days ago
- ✓ [[Strategy Review — 2026-04-10]] — updated 29 days ago
```

## The Unlock

You've already solved hard problems. You just forgot you solved them.

Buried Insight surfaces the notes you wrote that time proved valuable — not because they're old, but because **you kept validating them without realizing you were circling back**. The link graph tells the story: you wrote something true, then you went silent on it, then you kept building on top of it without ever revisiting the source.

## Phase Roadmap

**v0 (Shipped):** Heuristic — age × dormancy / validator recency. Surfaces candidates for human review.

**v1 (Pro):** LLM synthesis — feeds the buried note + validators to a frontier model, returns a one-sentence "why this matters now" summary.

**v2 (Team):** Cross-user burial detection — finds insights buried in one team member's vault that another is actively validating.

## API

```python
from basalt.buried import BuriedInsightVerb
from datetime import date

verb = BuriedInsightVerb(conn, today=date.today())
result = verb.run(top_n=3, vault_aware=True)

for insight in result.findings:
    print(f"Buried: {insight.candidate.title}")
    print(f"Age: {(today - insight.candidate.created).days} days")
    print(f"Quote: {insight.quote}")
    print(f"Validators: {len(insight.validators)}")
```

## Pricing

- **Free Tier:** 1 buried insight per week
- **Pro ($12/mo):** Daily buried insight + LLM synthesis (v1)
- **Team ($49/mo):** Cross-user burial detection (v2)

## Demo

```bash
basalt brief --section buried-insight
```

![Buried Insight Demo](buried-insight.gif)

**Watch the MP4:** [buried-insight.mp4](buried-insight.mp4)

---

**Next Verb:** [Implicit Thesis](implicit-thesis.md) — *The thing you keep saying without realizing you're saying the same thing.*

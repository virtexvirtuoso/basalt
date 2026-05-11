# Drift

> What you say is the priority versus what you actually spent the week on.

## What It Does

Compares your **stated priorities** (project notes in `02-Projects/`) against your **lived priorities** (daily notes mentioning project names) — surfaces projects you're overworking or underworking relative to your stated intent.

## How It Works

1. **Stated Priority** — Counts notes per project folder in `02-Projects/` over the last 30 days
2. **Lived Priority** — Counts project name mentions in daily notes (`01-Daily/`) over the same window
3. **Share Calculation** — Converts both to percentage shares (what % of attention each project got)
4. **Drift Detection** — Finds projects where stated share ≠ lived share by ≥10 percentage points
5. **Headline Narrative** — Generates plain-English summary: "X is overworked (+34pp), Y is underworked (-34pp)"

## Example Output

```markdown
## Drift

**Window:** 30 days | **Daily Notes:** 5 | **Projects:** 2

### 1. Priority Drift

**Overworked:** Atlas
- Stated: #2 (4 notes, 40.0%)
- Lived: #1 (23 mentions, 74.2%)
- **Drift:** +34.2pp

**Underworked:** SignalBot
- Stated: #1 (6 notes, 60.0%)
- Lived: #2 (8 mentions, 25.8%)
- **Drift:** -34.2pp
```

## The Unlock

You said SignalBot was the priority. Then you spent three weeks thinking about Atlas.

Drift doesn't judge — it reveals. Maybe Atlas deserved the attention. Maybe you lost focus. The point isn't right or wrong; the point is **legibility**. You can't course-correct what you can't see.

## Phase Roadmap

**v0 (Shipped):** 30-day window, daily-note-based lived tracking. Requires ≥2 projects and ≥3 daily notes.

**v1 (Pro):** Multi-window drift (7d, 30d, 90d) — shows whether drift is accelerating or correcting. Calendar integration — compares stated priorities against actual meeting time.

**v2 (Team):** Team drift detection — finds misalignment between team OKRs and where the team actually spent time.

## API

```python
from basalt.drift import DriftVerb
from datetime import date

verb = DriftVerb(conn, today=date.today())
result = verb.run(top_n=1, window_days=30)

for finding in result.findings:
    print(f"Window: {finding.window_days} days")
    if finding.headline_overworked:
        ow = finding.headline_overworked
        print(f"Overworked: {ow.name} (+{ow.drift_pct:.1f}pp)")
    if finding.headline_underworked:
        uw = finding.headline_underworked
        print(f"Underworked: {uw.name} ({uw.drift_pct:.1f}pp)")
```

## Pricing

- **Free Tier:** 1 drift report per week
- **Pro ($12/mo):** Daily drift + multi-window analysis (v1)
- **Team ($49/mo):** Team drift detection (v2)

## Demo

```bash
basalt brief --section drift
```

![Drift Demo](drift.gif)

**Watch the MP4:** [drift.mp4](drift.mp4)

---

**All Five Verbs Complete:** [Buried Insight](buried-insight.md) · [Implicit Thesis](implicit-thesis.md) · [Connection](connection.md) · [Contradiction](contradiction.md) · [Drift](drift.md)

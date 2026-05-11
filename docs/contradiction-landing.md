# Contradiction

> The two notes you wrote that can't both be true.

## What It Does

Finds pairs of notes that make **opposite claims** — high topical similarity (same subject) but opposing lexical signals (reversal markers like "never" vs "always", "wrong" vs "right", "avoid" vs "pursue").

## How It Works

1. **Topical Similarity** — Finds note pairs discussing the same subject (cosine ≥ 0.72)
2. **Reversal Detection** — Scans for explicit contradiction markers:
   - Negation pairs: "never" / "always", "avoid" / "pursue", "wrong" / "right"
   - Confidence flips: "uncertain" / "certain", "might" / "will"
   - Value reversals: "overvalued" / "undervalued", "bullish" / "bearish"
3. **Contradiction Score** — Combines similarity × reversal strength
4. **Quote Extraction** — Surfaces the conflicting claims side-by-side
5. **Verdict Flag** — Marks pairs requiring human review

## Example Output

```markdown
## Contradiction

### 1. Potential Contradiction

**Topical Similarity:** 0.78 | **Contradiction Score:** 2.1 | **Signals:** always/never, certain/uncertain

#### A: Position Sizing Rules
`02-Projects/Whale-Hunter/POSITION-SIZING.md`

> Never size a position above 5% — conviction is a trap.

#### B: Convex Bets Framework
`02-Projects/Maestro/Strategy/Convex-Bets.md`

> When the setup is certain, size aggressively — 15-20% is appropriate.

**Verdict:** ⚠️ Review required — these claims may be incompatible
```

## The Unlock

You've changed your mind. Or you're holding two incompatible beliefs at once.

Contradiction surfaces the tension: notes you wrote that can't both be true. Sometimes this is growth — you evolved and forgot to update the old note. Sometimes it's cognitive dissonance — you're operating on conflicting assumptions without realizing it.

## Phase Roadmap

**v0 (Shipped):** Heuristic reversal detection — looks for explicit contradiction markers. Absence is not evidence — many contradictions are implicit.

**v1 (Pro):** LLM contradiction detection — feeds topically-similar pairs to a frontier model, asks "can both be true?" Returns implicit contradictions the heuristic misses.

**v2 (Team):** Cross-user contradiction detection — finds team members operating on incompatible assumptions.

## API

```python
from basalt.contradiction import ContradictionVerb

verb = ContradictionVerb(conn)
result = verb.run(top_n=3, min_sim=0.72)

for pair in result.findings:
    print(f"Contradiction: {pair.note_a_title} vs {pair.note_b_title}")
    print(f"Signals: {', '.join(pair.signals)}")
    print(f"Score: {pair.contradiction_score:.1f}")
```

## Pricing

- **Free Tier:** 1 contradiction per week
- **Pro ($12/mo):** Daily contradiction + LLM detection (v1)
- **Team ($49/mo):** Cross-user contradiction detection (v2)

## Demo

```bash
basalt brief --section contradiction
```

![Contradiction Demo](contradiction.gif)

**Watch the MP4:** [contradiction.mp4](contradiction.mp4)

---

**Next Verb:** [Drift](drift.md) — *What you say is the priority versus what you actually spent the week on.*

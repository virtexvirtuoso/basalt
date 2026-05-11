# Connection

> The two ideas in different folders that turn out to be the same idea.

## What It Does

Finds pairs of notes in **different folders** that express the **same idea** — high semantic similarity, low lexical overlap, separated by folder structure.

Where **Implicit Thesis** finds *clusters* of 3+ notes, **Connection** finds *pairs* — you read them side-by-side and realize you've had the same insight twice, in different contexts.

## How It Works

1. **Embedding Similarity** — Computes cosine similarity between all note pairs (threshold ≥ 0.72)
2. **Folder Gate** — Only surfaces pairs in different top-level folders (filters out intra-folder duplicates)
3. **Lexical Diversity** — Penalizes pairs with high word overlap (rewards same idea, different words)
4. **Score Ranking** — Ranks by `similarity × (1 - lexical_overlap) × folder_distance_bonus`
5. **Quote Extraction** — Surfaces the load-bearing sentence from each note

## Example Output

```markdown
## Connection

### 1. Cross-Folder Duplicate

**Similarity:** 0.84 | **Score:** 0.791

#### A: Distribution Moat
`02-Projects/Maestro/Strategy/Distribution-Moat.md`

> What separates us isn't the alpha — it's how fast we ship alpha.

#### B: SignalBot Competitive Edge
`02-Projects/SignalBot/COMPETITIVE.md`

> Speed without adaptive intelligence is just expensive latency.
```

## The Unlock

You've already solved the same problem twice. Maybe three times.

Connection surfaces the duplicate insights hiding in different folders — written at different times, in different contexts, for different projects. Reading them side-by-side reveals the through-line: you keep arriving at the same truth from different directions.

## Phase Roadmap

**v0 (Shipped):** Pairwise similarity — surfaces cross-folder duplicates for human review.

**v1 (Pro):** LLM synthesis — feeds both notes to a frontier model, returns a one-sentence "unified insight" statement.

**v2 (Team):** Cross-user connection detection — finds the same idea appearing in multiple team members' vaults.

## API

```python
from basalt.connection import ConnectionVerb

verb = ConnectionVerb(conn)
result = verb.run(top_n=3, min_sim=0.72)

for pair in result.findings:
    print(f"A: {pair.note_a_title} ({pair.note_a_path})")
    print(f"B: {pair.note_b_title} ({pair.note_b_path})")
    print(f"Similarity: {pair.similarity:.2f}")
```

## Pricing

- **Free Tier:** 1 connection per week
- **Pro ($12/mo):** Daily connection + LLM synthesis (v1)
- **Team ($49/mo):** Cross-user connection detection (v2)

## Demo

```bash
basalt brief --section connection
```

![Connection Demo](connection.gif)

**Watch the MP4:** [connection.mp4](connection.mp4)

---

**Next Verb:** [Contradiction](contradiction.md) — *The two notes you wrote that can't both be true.*

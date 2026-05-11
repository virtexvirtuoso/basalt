# Implicit Thesis

> The thing you keep saying without realizing you're saying the same thing.

## What It Does

Finds clusters of 3+ notes that converge on a single through-line you've never named.

Where **Connection** finds *pairs* of notes that are the same idea, **Implicit Thesis** finds *clusters* of rephrasings — you read them side-by-side and the thesis becomes visible.

## How It Works

1. **Embedding Clustering** — Groups notes by semantic similarity (cosine ≥ 0.72)
2. **Tight Neighborhoods** — Finds near-cliques where *every* pair is above threshold (not just transitively connected)
3. **Centroid Selection** — Picks the note with highest mean intra-cluster similarity as the proxy thesis
4. **Diversity Gate** — Only surfaces clusters spanning ≥2 folders OR ≥30 days (filters out single-session structure)
5. **Quote Extraction** — Surfaces each member's load-bearing sentence as a "rephrasing"

## Example Output

```markdown
## Implicit Thesis

### 1. The Moat Is Distribution, Not Technology

**Centroid:** `04-Trading/Research/Moat-Hypothesis.md`
**Cluster Size:** 5 notes across 3 folders
**Time Span:** 89 days

> The sustainable edge isn't speed alone — it's speed + intelligence.

**Rephrasings:**
- **SignalBot Phase 2** (02-Projects)
  > Speed without adaptive intelligence is just expensive latency.
- **Competitive Positioning** (05-Strategy)
  > The real barrier isn't model quality — it's distribution velocity.
- **Why We Win** (02-Projects/Maestro)
  > What separates us isn't the alpha — it's how fast we ship alpha.
- **Product Notes** (08-Product)
  > Distribution at scale beats feature parity every time.
```

## The Unlock

You've been writing variations of the same insight for months. You just haven't seen them together.

Implicit Thesis surfaces the convergence. The thesis is already in your vault — you've been approaching it from different angles, in different contexts, at different times. Reading the cluster side-by-side makes the through-line obvious.

## Demo

```bash
basalt brief --section implicit-thesis
```

![Implicit Thesis Demo](implicit-thesis.gif)

**Watch the MP4:** [implicit-thesis.mp4](implicit-thesis.mp4)

**v0 (Shipped):** Clustering heuristic — surfaces convergence, not the thesis itself. You name the through-line.

**v1 (Pro):** LLM synthesis — feeds the cluster to a frontier model and returns a one-sentence thesis statement.

**v2 (Team):** Cross-user thesis detection — finds implicit theses that span multiple team members' vaults.

## API

```python
from basalt.implicit_thesis import ImplicitThesisVerb

verb = ImplicitThesisVerb(conn)
result = verb.run(top_n=3, min_sim=0.72, min_cluster_size=3)

for cluster in result.findings:
    print(f"Centroid: {cluster.centroid_title}")
    print(f"Cluster: {cluster.cluster_size} notes, {cluster.folder_diversity} folders")
    print(f"Quote: {cluster.centroid_quote}")
```

## Pricing

- **Free Tier:** 1 implicit thesis per week
- **Pro ($12/mo):** Daily implicit thesis + LLM synthesis (v1)
- **Team ($49/mo):** Cross-user thesis detection (v2)

---

**Next Verb:** [Buried Insight](buried-insight.md) — *The brilliant thing you wrote and forgot about.*

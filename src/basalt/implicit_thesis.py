"""Implicit Thesis algorithm — v0 cluster heuristic.

Site language: *"The thing you keep saying without realizing you're saying
the same thing."*

This is the multi-note version of Connection. Where Connection finds *pairs*
of notes that are the same idea, Implicit Thesis finds *clusters* of 3+
notes that converge on a single through-line the user has never named.

v0 is a clustering heuristic over note embeddings. The output is a *cluster
of rephrasings* — the user reads them side-by-side and the thesis becomes
visible. Phase 1 (Pro tier) adds LLM synthesis: feed the cluster to a
frontier model and have it write the one-sentence thesis.

Honest disclosure: v0 surfaces convergence, not the thesis itself. The
*"name the through-line"* job is left to the user (or to the v1 LLM pass).
That's the same shape as Contradiction v0 — candidates, not verdicts.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

import numpy as np

from basalt.embed import _blob_to_vec
from basalt.buried import (
    HUB_DENSITY_HARD,
    HUB_DENSITY_SOFT,
    _extract_claim_quote,
)


# Cosine threshold for "topically adjacent" — lower than Connection's 0.78
# because we want clusters of related-but-not-identical notes. 0.72 is
# empirically the floor where connected components on a real vault stay
# bounded; below that the whole graph collapses into one giant topical
# blob and the algorithm surfaces noise.
DEFAULT_MIN_SIM        = 0.72
MIN_CLUSTER_SIZE       = 3        # need ≥3 notes converging — pairs are Connection territory
MIN_WORD_COUNT         = 60
DEFAULT_TOP_N          = 3
MAX_CLUSTERS_PROBED    = 200
MAX_CLUSTER_SIZE       = 15       # a "thesis" is a tight cluster, not an entire topic-area
                                  # — clusters above this are usually folder-level overlap


@dataclass
class ThesisCluster:
    centroid_id: int                      # note whose load-bearing sentence stands as proxy thesis
    centroid_path: str
    centroid_title: str
    centroid_quote: str                   # the proxy thesis statement
    centroid_quote_provenance: str
    member_ids: list[int]                 # all members including centroid
    member_paths: list[str]
    member_titles: list[str]
    member_quotes: list[str]              # each member's load-bearing sentence — the rephrasings
    member_quote_provenances: list[str]
    member_folders: list[str]             # top-level folder per member
    cluster_size: int
    folder_diversity: int                 # distinct top-level folders touched
    span_days: int                        # oldest created → newest updated
    mean_similarity: float                # average pairwise cosine within cluster
    score: float                          # final ranking


def _top_folder(rel_path: str) -> str:
    return rel_path.split("/", 1)[0] if "/" in rel_path else ""


def _hub_density(out_links: int, word_count: int) -> float:
    if word_count <= 0:
        return 0.0
    return out_links / max(word_count / 100.0, 1.0)


def _hub_penalty(density: float) -> float:
    excess = max(0.0, density - HUB_DENSITY_SOFT)
    return 1.0 / (1.0 + (2.0 * excess) ** 2)


def _connected_components(adj: dict[int, set[int]]) -> list[set[int]]:
    """Standard union-find / BFS connected-component over a sparse graph.
    Returns a list of components, each a set of node ids.

    NOTE: kept for tests / future use; the production path uses
    `_tight_neighborhood_clusters` instead because connected components
    over real-vault similarity graphs collapse into one giant component
    at any practical similarity threshold.
    """
    seen: set[int] = set()
    components: list[set[int]] = []
    for start in adj:
        if start in seen:
            continue
        # BFS
        stack = [start]
        comp: set[int] = set()
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            comp.add(n)
            for neighbor in adj.get(n, ()):
                if neighbor not in seen:
                    stack.append(neighbor)
        if comp:
            components.append(comp)
    return components


def _tight_neighborhoods(
    sims: np.ndarray,
    threshold: float,
    min_size: int,
    max_size: int,
) -> list[tuple[int, list[int]]]:
    """Find tight neighborhoods (near-cliques) in the similarity graph.

    A tight neighborhood is a set {centroid, m1, m2, ...} where every pair
    has cosine ≥ threshold — not just connectedness through hops. This is
    the right primitive for finding "themes the user keeps repeating" on a
    vault-scale graph, where simple CC blows up into one giant component.

    Greedy implementation: for each candidate centroid, add neighbors in
    decreasing similarity, only if the candidate is above threshold to all
    existing members. Multiple centroids may produce the same cluster;
    caller dedupes by member set.

    Returns list of (centroid_local_idx, member_local_idx_list).
    """
    n = sims.shape[0]
    out: list[tuple[int, list[int]]] = []
    seen_signatures: set[tuple[int, ...]] = set()
    for c in range(n):
        # Neighbors of c above threshold, sorted by similarity (highest first)
        neighbor_sims = sims[c]
        neighbors = [j for j in range(n) if j != c and neighbor_sims[j] >= threshold]
        neighbors.sort(key=lambda j: -neighbor_sims[j])
        cluster = [c]
        for nb in neighbors:
            if len(cluster) >= max_size:
                break
            # Add nb only if it's above threshold to every existing cluster member.
            if all(sims[nb, m] >= threshold for m in cluster):
                cluster.append(nb)
        if len(cluster) >= min_size:
            sig = tuple(sorted(cluster))
            if sig in seen_signatures:
                continue
            seen_signatures.add(sig)
            out.append((c, cluster))
    return out


def find_implicit_theses(
    conn: sqlite3.Connection,
    min_sim: float = DEFAULT_MIN_SIM,
    min_cluster_size: int = MIN_CLUSTER_SIZE,
    top_n: int = DEFAULT_TOP_N,
) -> list[ThesisCluster]:
    """Return top N implicit-thesis clusters.

    A cluster qualifies when:
      - ≥ `min_cluster_size` notes (default 3) all share pairwise cosine ≥ `min_sim`
        with at least one other cluster member (i.e., the cluster is a connected
        component in the similarity graph)
      - All members meet word-count and hub-density floors
      - The cluster spans ≥ 2 distinct top-level folders OR ≥ 30 days time-span
        (single-folder small-time clusters are usually within-doc structure, not
        a true through-line)
    """
    rows = conn.execute(
        """
        SELECT n.id, n.rel_path, n.title, n.created, n.updated,
               n.word_count, n.content,
               e.vec
        FROM notes n
        JOIN embeddings e ON e.note_id = n.id
        WHERE n.word_count >= ?
        """,
        (MIN_WORD_COUNT,),
    ).fetchall()
    if len(rows) < min_cluster_size:
        return []

    # Hub-density filter: drop MOCs from the candidate pool entirely.
    out_link_counts = dict(conn.execute(
        "SELECT from_note_id, COUNT(DISTINCT target) FROM links GROUP BY from_note_id"
    ).fetchall())

    note_ids = [r["id"] for r in rows]
    paths    = {r["id"]: r["rel_path"] for r in rows}
    titles   = {r["id"]: r["title"]    for r in rows}
    contents = {r["id"]: r["content"]  for r in rows}
    wcs      = {r["id"]: r["word_count"] for r in rows}
    created  = {r["id"]: r["created"]  for r in rows}
    updated  = {r["id"]: r["updated"]  for r in rows}
    densities = {nid: _hub_density(out_link_counts.get(nid, 0), wcs[nid]) for nid in note_ids}
    keep_ids = [nid for nid in note_ids if densities[nid] <= HUB_DENSITY_HARD]
    if len(keep_ids) < min_cluster_size:
        return []

    # Build pairwise similarity matrix
    id_to_idx = {nid: i for i, nid in enumerate(keep_ids)}
    matrix = np.stack([_blob_to_vec(rows[note_ids.index(nid)]["vec"]) for nid in keep_ids])
    sims = matrix @ matrix.T
    np.fill_diagonal(sims, -1.0)

    # Tight-neighborhood clusters (near-cliques) instead of connected
    # components. Each cluster is a set where EVERY pair is above threshold,
    # not just transitively connected — the right primitive when the vault
    # graph is dense enough to collapse into one giant CC.
    raw_clusters = _tight_neighborhoods(
        sims, min_sim, min_size=min_cluster_size, max_size=MAX_CLUSTER_SIZE,
    )
    if not raw_clusters:
        return []

    # For each tight neighborhood: find centroid (highest mean intra-cluster sim),
    # extract load-bearing quotes, score, filter on diversity / time span.
    clusters: list[ThesisCluster] = []
    for _greedy_centroid_idx, idx_list in raw_clusters[:MAX_CLUSTERS_PROBED]:
        comp_list = [keep_ids[i] for i in idx_list]
        sub_sims  = sims[np.ix_(idx_list, idx_list)]
        # Centroid = highest mean similarity to other members. Use np.where to
        # mask the diagonal (-1) when computing the mean.
        valid_mask = sub_sims > -0.5
        mean_per_row = np.where(valid_mask, sub_sims, 0).sum(axis=1) / np.maximum(valid_mask.sum(axis=1), 1)
        centroid_local = int(np.argmax(mean_per_row))
        centroid_id = comp_list[centroid_local]

        # Mean intra-cluster similarity — for ranking and reporting
        # Sum of the upper triangle, divided by number of pairs.
        if len(comp_list) >= 2:
            upper = sub_sims[np.triu_indices(len(comp_list), k=1)]
            upper_valid = upper[upper > -0.5]
            cluster_sim = float(upper_valid.mean()) if upper_valid.size else 0.0
        else:
            cluster_sim = 0.0

        # Folder diversity + time span
        folders = [_top_folder(paths[nid]) for nid in comp_list]
        folder_diversity = len({f for f in folders if f})
        try:
            from datetime import date
            dates = []
            for nid in comp_list:
                c = created[nid]
                u = updated[nid]
                if c:
                    dates.append(date.fromisoformat(c[:10]))
                if u:
                    dates.append(date.fromisoformat(u[:10]))
            span_days = (max(dates) - min(dates)).days if len(dates) >= 2 else 0
        except (ValueError, TypeError):
            span_days = 0

        # Diversity gate: at least 2 folders OR a 30-day time span.
        # Single-folder same-week clusters are usually one project's internal
        # structure, not an unnamed through-line.
        if folder_diversity < 2 and span_days < 30:
            continue

        # Quote extraction per member — surface each as a "rephrasing"
        member_quotes: list[str] = []
        member_provs: list[str] = []
        for nid in comp_list:
            q, prov = _extract_claim_quote(contents[nid])
            member_quotes.append(q or "")
            member_provs.append(prov)
        # If centroid quote is empty, the cluster has nothing to display.
        centroid_quote_idx = comp_list.index(centroid_id)
        if not member_quotes[centroid_quote_idx]:
            continue

        # Score: cluster_size × diversity × log(span+1) × mean_sim × hub-penalty mean
        from math import log
        sim_factor    = cluster_sim
        size_factor   = len(comp_list)
        diversity_fac = folder_diversity if folder_diversity >= 2 else 1.0
        span_factor   = log(span_days + 1) if span_days > 0 else 1.0
        hub_pen_mean  = float(np.mean([_hub_penalty(densities[nid]) for nid in comp_list]))
        score = sim_factor * size_factor * diversity_fac * span_factor * hub_pen_mean

        clusters.append(ThesisCluster(
            centroid_id=centroid_id,
            centroid_path=paths[centroid_id],
            centroid_title=titles[centroid_id],
            centroid_quote=member_quotes[centroid_quote_idx],
            centroid_quote_provenance=member_provs[centroid_quote_idx],
            member_ids=comp_list,
            member_paths=[paths[nid] for nid in comp_list],
            member_titles=[titles[nid] for nid in comp_list],
            member_quotes=member_quotes,
            member_quote_provenances=member_provs,
            member_folders=folders,
            cluster_size=len(comp_list),
            folder_diversity=folder_diversity,
            span_days=span_days,
            mean_similarity=cluster_sim,
            score=score,
        ))

    if not clusters:
        return []
    clusters.sort(key=lambda c: -c.score)
    return clusters[:top_n]

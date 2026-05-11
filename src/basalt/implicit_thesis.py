"""Implicit Thesis verb — finds clusters of notes converging on an unnamed through-line.

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
from datetime import date
from math import log
from typing import Any

import numpy as np

from basalt.embed import _blob_to_vec
from basalt.verb import VerbBase, VerbResult
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


class ImplicitThesisVerb(VerbBase):
    """Implicit Thesis verb implementation.

    Finds clusters of 3+ notes that converge on a single through-line the user has never named.
    """

    @property
    def name(self) -> str:
        return "implicit-thesis"

    def _threshold(self) -> dict:
        """Return threshold parameters for implicit thesis."""
        return {
            "min_sim": DEFAULT_MIN_SIM,
            "min_cluster_size": MIN_CLUSTER_SIZE,
            "min_word_count": MIN_WORD_COUNT,
            "max_clusters_probed": MAX_CLUSTERS_PROBED,
            "max_cluster_size": MAX_CLUSTER_SIZE,
        }

    def _candidates(self) -> list[dict]:
        """Return all notes with embeddings as potential candidates."""
        rows = self.conn.execute(
            """
            SELECT n.id, n.rel_path, n.title, n.created, n.updated,
                   n.word_count, n.content, e.vec
            FROM notes n
            JOIN embeddings e ON e.note_id = n.id
            WHERE n.word_count >= ?
            """,
            (MIN_WORD_COUNT,),
        ).fetchall()

        out_link_counts = dict(self.conn.execute(
            "SELECT from_note_id, COUNT(DISTINCT target) FROM links GROUP BY from_note_id"
        ).fetchall())

        notes = []
        for r in rows:
            nid = r["id"]
            notes.append({
                "id": nid,
                "rel_path": r["rel_path"],
                "title": r["title"],
                "created": r["created"],
                "updated": r["updated"],
                "word_count": r["word_count"],
                "content": r["content"],
                "vec": _blob_to_vec(r["vec"]) if r["vec"] else None,
                "out_links": out_link_counts.get(nid, 0),
            })
        return notes

    def _filter(self, candidate: dict, thresholds: dict) -> bool:
        """Filter out hub notes (MOCs/indexes)."""
        density = _hub_density(candidate["out_links"], candidate["word_count"])
        return density <= HUB_DENSITY_HARD

    def _score(self, candidate: dict, cluster_members: list[int], sims: np.ndarray, id_to_idx: dict) -> float:
        """Score a cluster based on size, diversity, span, and similarity."""
        # This is called differently for thesis clusters - scoring happens at cluster level
        return 0.0

    def _quote(self, candidate: dict) -> tuple[str, str]:
        """Extract load-bearing quote from candidate."""
        return _extract_claim_quote(candidate["content"])

    def _build_finding(self, cluster_data: dict, thresholds: dict) -> ThesisCluster:
        """Build ThesisCluster from cluster data."""
        return ThesisCluster(**cluster_data)

    def run(self, top_n: int = DEFAULT_TOP_N, min_sim: float | None = None, min_cluster_size: int | None = None) -> VerbResult[ThesisCluster]:
        """Execute the Implicit Thesis verb.

        Args:
            top_n: Number of thesis clusters to return
            min_sim: Override default similarity threshold
            min_cluster_size: Override default minimum cluster size
        """
        thresholds = self._threshold()
        if min_sim is not None:
            thresholds["min_sim"] = min_sim
        if min_cluster_size is not None:
            thresholds["min_cluster_size"] = min_cluster_size

        min_sim = thresholds["min_sim"]
        min_cluster_size = thresholds["min_cluster_size"]

        # Get candidates
        notes = self._candidates()
        if len(notes) < min_cluster_size:
            return VerbResult(verb=self.name, findings=[], thresholds=thresholds, vault_age_days=self._vault_age())

        # Filter by hub density
        keep_notes = [n for n in notes if self._filter(n, thresholds)]
        if len(keep_notes) < min_cluster_size:
            return VerbResult(verb=self.name, findings=[], thresholds=thresholds, vault_age_days=self._vault_age())

        # Build similarity matrix
        keep_ids = [n["id"] for n in keep_notes]
        id_to_idx = {nid: i for i, nid in enumerate(keep_ids)}
        matrix = np.stack([n["vec"] for n in keep_notes])
        sims = matrix @ matrix.T
        np.fill_diagonal(sims, -1.0)

        # Find tight neighborhoods
        raw_clusters = _tight_neighborhoods(
            sims, min_sim, min_size=min_cluster_size, max_size=MAX_CLUSTER_SIZE,
        )
        if not raw_clusters:
            return VerbResult(verb=self.name, findings=[], thresholds=thresholds, vault_age_days=self._vault_age())

        # Build thesis clusters
        findings: list[ThesisCluster] = []
        for _greedy_centroid_idx, idx_list in raw_clusters[:MAX_CLUSTERS_PROBED]:
            cluster_data = self._build_cluster_data(
                keep_notes, keep_ids, idx_list, sims, thresholds
            )
            if cluster_data is None:
                continue
            finding = ThesisCluster(**cluster_data)
            findings.append(finding)

        # Sort by score and return top_n
        findings.sort(key=lambda c: -c.score)
        return VerbResult(
            verb=self.name,
            findings=findings[:top_n],
            thresholds=thresholds,
            vault_age_days=self._vault_age(),
        )

    def _build_cluster_data(
        self,
        notes: list[dict],
        keep_ids: list[int],
        idx_list: list[int],
        sims: np.ndarray,
        thresholds: dict,
    ) -> dict | None:
        """Build cluster data dictionary from a tight neighborhood."""
        comp_list = [keep_ids[i] for i in idx_list]
        sub_sims = sims[np.ix_(idx_list, idx_list)]

        # Find centroid (highest mean intra-cluster similarity)
        valid_mask = sub_sims > -0.5
        mean_per_row = np.where(valid_mask, sub_sims, 0).sum(axis=1) / np.maximum(valid_mask.sum(axis=1), 1)
        centroid_local = int(np.argmax(mean_per_row))
        centroid_id = comp_list[centroid_local]

        # Mean intra-cluster similarity
        if len(comp_list) >= 2:
            upper = sub_sims[np.triu_indices(len(comp_list), k=1)]
            upper_valid = upper[upper > -0.5]
            cluster_sim = float(upper_valid.mean()) if upper_valid.size else 0.0
        else:
            cluster_sim = 0.0

        # Build note lookup
        notes_by_id = {n["id"]: n for n in notes}

        # Folder diversity + time span
        folders = [_top_folder(notes_by_id[nid]["rel_path"]) for nid in comp_list]
        folder_diversity = len({f for f in folders if f})

        try:
            dates = []
            for nid in comp_list:
                n = notes_by_id[nid]
                if n["created"]:
                    dates.append(date.fromisoformat(n["created"][:10]))
                if n["updated"]:
                    dates.append(date.fromisoformat(n["updated"][:10]))
            span_days = (max(dates) - min(dates)).days if len(dates) >= 2 else 0
        except (ValueError, TypeError):
            span_days = 0

        # Diversity gate
        if folder_diversity < 2 and span_days < 30:
            return None

        # Quote extraction per member
        member_quotes: list[str] = []
        member_provs: list[str] = []
        for nid in comp_list:
            q, prov = self._quote(notes_by_id[nid])
            member_quotes.append(q or "")
            member_provs.append(prov)

        # Centroid quote must exist
        centroid_quote_idx = comp_list.index(centroid_id)
        if not member_quotes[centroid_quote_idx]:
            return None

        # Score computation
        sim_factor = cluster_sim
        size_factor = len(comp_list)
        diversity_fac = folder_diversity if folder_diversity >= 2 else 1.0
        span_factor = log(span_days + 1) if span_days > 0 else 1.0
        densities = {_hub_density(notes_by_id[nid]["out_links"], notes_by_id[nid]["word_count"]) for nid in comp_list}
        hub_pen_mean = float(np.mean([_hub_penalty(d) for d in densities]))
        score = sim_factor * size_factor * diversity_fac * span_factor * hub_pen_mean

        return {
            "centroid_id": centroid_id,
            "centroid_path": notes_by_id[centroid_id]["rel_path"],
            "centroid_title": notes_by_id[centroid_id]["title"],
            "centroid_quote": member_quotes[centroid_quote_idx],
            "centroid_quote_provenance": member_provs[centroid_quote_idx],
            "member_ids": comp_list,
            "member_paths": [notes_by_id[nid]["rel_path"] for nid in comp_list],
            "member_titles": [notes_by_id[nid]["title"] for nid in comp_list],
            "member_quotes": member_quotes,
            "member_quote_provenances": member_provs,
            "member_folders": folders,
            "cluster_size": len(comp_list),
            "folder_diversity": folder_diversity,
            "span_days": span_days,
            "mean_similarity": cluster_sim,
            "score": score,
        }


# ── Backwards-compatible wrappers ─────────────────────────────────

def find_implicit_theses(
    conn: sqlite3.Connection,
    min_sim: float = DEFAULT_MIN_SIM,
    min_cluster_size: int = MIN_CLUSTER_SIZE,
    top_n: int = DEFAULT_TOP_N,
) -> list[ThesisCluster]:
    """Run the Implicit Thesis verb. Returns top N thesis clusters."""
    verb = ImplicitThesisVerb(conn)
    result = verb.run(top_n=top_n, min_sim=min_sim, min_cluster_size=min_cluster_size)
    return result.findings

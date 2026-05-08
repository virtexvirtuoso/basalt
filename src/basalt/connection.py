"""Connection algorithm.

Site language: *"The two ideas in different folders that turn out to be the
same idea."*

Finds: pairs of notes that the user wrote in different parts of the vault
without linking them, but whose embeddings say they are the same idea. The
honest delta over Smart-Connections-style "related notes" is the **folder
boundary** + **wikilink absence** filter — surfaces only the latent
duplicates the user has not noticed yet.

Output: top N pairs, ranked by adjusted similarity (hub-density-penalised),
with a load-bearing quote extracted from each side.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

import numpy as np

from basalt.embed import _blob_to_vec
from basalt.buried import (
    HUB_DENSITY_HARD,
    HUB_DENSITY_SOFT,
    _extract_claim_quote,
)


# Cosine threshold for "same idea". 0.78 is empirically the floor where two
# notes feel related enough that the user should care; below that you get
# topical near-misses that don't earn a slot in the Brief.
DEFAULT_MIN_SIM        = 0.78
MIN_WORD_COUNT         = 60       # 30-word stubs aren't ideas, they're labels
MAX_PAIRS              = 200      # cap candidates before final ranking
DEFAULT_TOP_N          = 3


@dataclass
class ConnectionPair:
    note_a_id: int
    note_a_path: str
    note_a_title: str
    note_a_quote: str
    note_a_quote_provenance: str
    note_b_id: int
    note_b_path: str
    note_b_title: str
    note_b_quote: str
    note_b_quote_provenance: str
    similarity: float
    score: float                  # similarity × hub penalty (combined)
    a_hub_density: float
    b_hub_density: float


def _top_folder(rel_path: str) -> str:
    """First path segment — '02-Projects/SignalBot/HYPOTHESIS.md' → '02-Projects'."""
    return rel_path.split("/", 1)[0] if "/" in rel_path else ""


def _folder_chain(rel_path: str) -> tuple[str, ...]:
    """Full directory chain, no filename. Used for stricter folder-boundary checks."""
    parts = rel_path.split("/")
    return tuple(parts[:-1]) if len(parts) > 1 else ()


def _hub_density(out_links: int, word_count: int) -> float:
    if word_count <= 0:
        return 0.0
    return out_links / max(word_count / 100.0, 1.0)


def _hub_penalty(density: float) -> float:
    """Match buried.py: no penalty below SOFT, inverse-square taper above."""
    excess = max(0.0, density - HUB_DENSITY_SOFT)
    return 1.0 / (1.0 + (2.0 * excess) ** 2)


def find_connections(
    conn: sqlite3.Connection,
    min_sim: float = DEFAULT_MIN_SIM,
    top_n: int = DEFAULT_TOP_N,
    require_different_top_folder: bool = True,
) -> list[ConnectionPair]:
    """Return top N connection pairs ranked by adjusted similarity.

    A pair (A, B) qualifies when:
      - A and B are in different top-level folders (when require_different_top_folder)
      - There is no wikilink between them in either direction
      - Both have ≥ MIN_WORD_COUNT words
      - Neither A nor B exceeds the hard hub-density threshold (MOCs filtered out)
      - cosine(emb(A), emb(B)) ≥ min_sim
    """
    rows = conn.execute(
        """
        SELECT n.id, n.rel_path, n.title, n.word_count, n.content,
               e.vec
        FROM notes n
        JOIN embeddings e ON e.note_id = n.id
        WHERE n.word_count >= ?
        """,
        (MIN_WORD_COUNT,),
    ).fetchall()
    if len(rows) < 2:
        return []

    # Outgoing-link counts for hub-density
    out_link_counts = dict(conn.execute(
        "SELECT from_note_id, COUNT(DISTINCT target) FROM links GROUP BY from_note_id"
    ).fetchall())

    # Build the existing wikilink edge set, both directions, so we can exclude
    # already-linked pairs from the "ideas you didn't realise are the same" set.
    linked_pairs: set[frozenset[int]] = set()
    cur = conn.execute(
        "SELECT from_note_id, target_note_id FROM links WHERE target_note_id IS NOT NULL"
    )
    for from_id, to_id in cur.fetchall():
        if from_id != to_id:
            linked_pairs.add(frozenset((from_id, to_id)))

    note_ids = [r["id"] for r in rows]
    paths    = {r["id"]: r["rel_path"] for r in rows}
    titles   = {r["id"]: r["title"]    for r in rows}
    contents = {r["id"]: r["content"]  for r in rows}
    wcs      = {r["id"]: r["word_count"] for r in rows}
    densities = {nid: _hub_density(out_link_counts.get(nid, 0), wcs[nid]) for nid in note_ids}

    # Drop notes that are MOCs/indexes — they will pair with everything for the
    # wrong reason. This matches the buried-insight hard filter exactly.
    keep_ids = [nid for nid in note_ids if densities[nid] <= HUB_DENSITY_HARD]
    if len(keep_ids) < 2:
        return []

    id_to_idx = {nid: i for i, nid in enumerate(keep_ids)}
    matrix = np.stack([_blob_to_vec(rows[note_ids.index(nid)]["vec"]) for nid in keep_ids])
    if matrix.shape[0] < 2:
        return []

    # Pairwise cosine — embeddings are already L2-normalised, so dot product is enough.
    sims = matrix @ matrix.T
    # Mask the diagonal and lower triangle so each pair appears once.
    np.fill_diagonal(sims, -1.0)
    iu = np.triu_indices(sims.shape[0], k=1)
    pair_sims = sims[iu]

    # Pre-filter on similarity, then apply boundary + link checks.
    qualifying: list[tuple[int, int, float]] = []  # (a_id, b_id, sim)
    for i, j, s in zip(iu[0], iu[1], pair_sims):
        if s < min_sim:
            continue
        a_id = keep_ids[int(i)]
        b_id = keep_ids[int(j)]
        if frozenset((a_id, b_id)) in linked_pairs:
            continue
        if require_different_top_folder and _top_folder(paths[a_id]) == _top_folder(paths[b_id]):
            continue
        qualifying.append((a_id, b_id, float(s)))
        if len(qualifying) >= MAX_PAIRS:
            break

    if not qualifying:
        return []

    # Score and rank: similarity × geometric mean of hub penalties on both sides.
    scored: list[ConnectionPair] = []
    for a_id, b_id, s in qualifying:
        pa = _hub_penalty(densities[a_id])
        pb = _hub_penalty(densities[b_id])
        score = s * (pa * pb) ** 0.5
        a_quote, a_prov = _extract_claim_quote(contents[a_id])
        b_quote, b_prov = _extract_claim_quote(contents[b_id])
        if not a_quote or not b_quote:
            continue
        scored.append(ConnectionPair(
            note_a_id=a_id,
            note_a_path=paths[a_id],
            note_a_title=titles[a_id],
            note_a_quote=a_quote,
            note_a_quote_provenance=a_prov,
            note_b_id=b_id,
            note_b_path=paths[b_id],
            note_b_title=titles[b_id],
            note_b_quote=b_quote,
            note_b_quote_provenance=b_prov,
            similarity=s,
            score=score,
            a_hub_density=densities[a_id],
            b_hub_density=densities[b_id],
        ))

    scored.sort(key=lambda p: -p.score)

    # Diversity pass: don't return three pairs all involving the same note —
    # we want the user to see distinct connection "axes". Drop a pair if either
    # endpoint already appears in the result set.
    seen: set[int] = set()
    out: list[ConnectionPair] = []
    for p in scored:
        if p.note_a_id in seen or p.note_b_id in seen:
            continue
        out.append(p)
        seen.add(p.note_a_id)
        seen.add(p.note_b_id)
        if len(out) >= top_n:
            break
    return out

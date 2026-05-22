"""Connection verb — finds cross-folder duplicate ideas the user hasn't linked.

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

import sqlite3
from dataclasses import dataclass

import numpy as np

from basalt.embed import _blob_to_vec
from basalt.filters import sql_exclude_clause
from basalt.verb import VerbBase, VerbResult
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


def _hub_density(out_links: int, word_count: int) -> float:
    if word_count <= 0:
        return 0.0
    return out_links / max(word_count / 100.0, 1.0)


def _hub_penalty(density: float) -> float:
    """Match buried.py: no penalty below SOFT, inverse-square taper above."""
    excess = max(0.0, density - HUB_DENSITY_SOFT)
    return 1.0 / (1.0 + (2.0 * excess) ** 2)


class ConnectionVerb(VerbBase):
    """Connection verb implementation.

    Finds pairs of notes in different folders that are semantically similar but not linked.
    """

    @property
    def name(self) -> str:
        return "connection"

    def _threshold(self) -> dict:
        """Return threshold parameters for connection."""
        return {
            "min_sim": DEFAULT_MIN_SIM,
            "min_word_count": MIN_WORD_COUNT,
            "max_pairs": MAX_PAIRS,
        }

    def _candidates(self) -> list[dict]:
        """Return all notes with embeddings as potential candidates."""
        rows = self.conn.execute(
            f"""
            SELECT n.id, n.rel_path, n.title, n.word_count, n.content, n.status, n.type, n.confidence, e.vec
            FROM notes n
            JOIN embeddings e ON e.note_id = n.id
            WHERE n.word_count >= ? AND {sql_exclude_clause()}
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

    def _score(self, candidate: dict, thresholds: dict) -> float:
        """Score is computed at pair level, not candidate level."""
        return 0.0

    def _quote(self, candidate: dict) -> tuple[str, str]:
        """Extract load-bearing quote from candidate."""
        return _extract_claim_quote(candidate["content"])

    def _build_finding(
        self,
        a: dict,
        b: dict,
        similarity: float,
        thresholds: dict,
    ) -> ConnectionPair:
        """Build ConnectionPair from two notes and their similarity."""
        a_quote, a_prov = self._quote(a)
        b_quote, b_prov = self._quote(b)

        density_a = _hub_density(a["out_links"], a["word_count"])
        density_b = _hub_density(b["out_links"], b["word_count"])
        penalty_a = _hub_penalty(density_a)
        penalty_b = _hub_penalty(density_b)
        score = similarity * (penalty_a * penalty_b) ** 0.5

        return ConnectionPair(
            note_a_id=a["id"],
            note_a_path=a["rel_path"],
            note_a_title=a["title"],
            note_a_quote=a_quote,
            note_a_quote_provenance=a_prov,
            note_b_id=b["id"],
            note_b_path=b["rel_path"],
            note_b_title=b["title"],
            note_b_quote=b_quote,
            note_b_quote_provenance=b_prov,
            similarity=similarity,
            score=score,
            a_hub_density=density_a,
            b_hub_density=density_b,
        )

    def run(
        self,
        top_n: int = DEFAULT_TOP_N,
        min_sim: float | None = None,
        require_different_top_folder: bool = True,
    ) -> VerbResult[ConnectionPair]:
        """Execute the Connection verb.

        Args:
            top_n: Number of connection pairs to return
            min_sim: Override default similarity threshold
            require_different_top_folder: Whether to require different top-level folders
        """
        thresholds = self._threshold()
        if min_sim is not None:
            thresholds["min_sim"] = min_sim

        min_sim = thresholds["min_sim"]
        max_pairs = thresholds["max_pairs"]

        # Get candidates
        notes = self._candidates()
        if len(notes) < 2:
            return VerbResult(verb=self.name, findings=[], thresholds=thresholds, vault_age_days=self._vault_age())

        # Filter by hub density
        keep_notes = [n for n in notes if self._filter(n, thresholds)]
        if len(keep_notes) < 2:
            return VerbResult(verb=self.name, findings=[], thresholds=thresholds, vault_age_days=self._vault_age())

        # Build similarity matrix
        keep_ids = [n["id"] for n in keep_notes]
        matrix = np.stack([n["vec"] for n in keep_notes])
        sims = matrix @ matrix.T
        np.fill_diagonal(sims, -1.0)

        # Get linked pairs (exclude already-linked notes)
        linked_pairs: set[frozenset[int]] = set()
        cur = self.conn.execute(
            "SELECT from_note_id, target_note_id FROM links WHERE target_note_id IS NOT NULL"
        )
        for from_id, to_id in cur.fetchall():
            if from_id != to_id:
                linked_pairs.add(frozenset((from_id, to_id)))

        # Find qualifying pairs
        qualifying: list[tuple[dict, dict, float]] = []
        n = len(keep_notes)
        for i in range(n):
            for j in range(i + 1, n):
                sim = sims[i, j]
                if sim < min_sim:
                    continue

                a = keep_notes[i]
                b = keep_notes[j]
                a_id, b_id = a["id"], b["id"]

                if frozenset((a_id, b_id)) in linked_pairs:
                    continue

                if require_different_top_folder and _top_folder(a["rel_path"]) == _top_folder(b["rel_path"]):
                    continue

                qualifying.append((a, b, float(sim)))
                if len(qualifying) >= max_pairs:
                    break

            if len(qualifying) >= max_pairs:
                break

        if not qualifying:
            return VerbResult(verb=self.name, findings=[], thresholds=thresholds, vault_age_days=self._vault_age())

        # Build findings
        findings: list[ConnectionPair] = []
        for a, b, sim in qualifying:
            finding = self._build_finding(a, b, sim, thresholds)
            if not finding.note_a_quote or not finding.note_b_quote:
                continue
            findings.append(finding)

        # Sort by score
        findings.sort(key=lambda p: -p.score)

        # Diversity pass: don't return pairs with overlapping endpoints
        seen: set[int] = set()
        out: list[ConnectionPair] = []
        for p in findings:
            if p.note_a_id in seen or p.note_b_id in seen:
                continue
            out.append(p)
            seen.add(p.note_a_id)
            seen.add(p.note_b_id)
            if len(out) >= top_n:
                break

        return VerbResult(
            verb=self.name,
            findings=out,
            thresholds=thresholds,
            vault_age_days=self._vault_age(),
        )


# ── Backwards-compatible wrappers ─────────────────────────────────

def find_connections(
    conn: sqlite3.Connection,
    min_sim: float = DEFAULT_MIN_SIM,
    top_n: int = DEFAULT_TOP_N,
    require_different_top_folder: bool = True,
) -> list[ConnectionPair]:
    """Run the Connection verb. Returns top N connection pairs."""
    verb = ConnectionVerb(conn)
    result = verb.run(top_n=top_n, min_sim=min_sim, require_different_top_folder=require_different_top_folder)
    return result.findings

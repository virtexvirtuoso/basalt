"""Verb protocol — the base class for all cognitive operations.

Every verb in Basalt implements the same skeleton:
  1. candidates() — what notes/claims to consider
  2. score() — how to rank them
  3. threshold() — what makes the cut
  4. quote() — how to extract citations

This protocol makes verbs composable, testable, and extensible.
New verbs inherit from VerbBase and implement the abstract methods.
"""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Generic, TypeVar, TYPE_CHECKING


if TYPE_CHECKING:
    from typing import Self


# FindingT is the concrete finding type per verb (BuriedInsight, ConnectionPair, etc.)
FindingT = TypeVar("FindingT")


@dataclass
class VerbResult(Generic[FindingT]):
    """Standardized output from any verb."""
    verb: str
    findings: list[FindingT]
    thresholds: dict
    vault_age_days: int
    metadata: dict = field(default_factory=dict)


class VerbBase(ABC):
    """Abstract base class for all Basalt verbs.

    Subclasses implement:
      - _candidates(): return raw candidate items from the vault
      - _score(): rank candidates
      - _threshold(): filter by minimum bar
      - _quote(): extract load-bearing text from a finding

    The run() method orchestrates the pipeline and returns a VerbResult.
    """

    def __init__(self, conn: sqlite3.Connection, today: date | None = None):
        self.conn = conn
        self.today = today or date.today()

    @property
    @abstractmethod
    def name(self) -> str:
        """Verb name (e.g., 'buried-insight', 'implicit-thesis')."""
        pass

    @abstractmethod
    def _candidates(self) -> list:
        """Return raw candidates from the vault.

        Returns a list of candidate objects (type varies by verb).
        """
        pass

    @abstractmethod
    def _score(self, candidate) -> float:
        """Score a single candidate. Higher = better."""
        pass

    @abstractmethod
    def _threshold(self) -> dict:
        """Return threshold parameters for this verb.

        Returns dict with min_age, min_dormant, recent_window, etc.
        Verb-specific.
        """
        pass

    @abstractmethod
    def _filter(self, candidate, thresholds: dict) -> bool:
        """Filter candidates by thresholds. Returns True if candidate passes."""
        pass

    @abstractmethod
    def _quote(self, candidate) -> tuple[str, str]:
        """Extract load-bearing quote from candidate.

        Returns (quote_text, provenance).
        """
        pass

    @abstractmethod
    def _build_finding(self, candidate, quote: str, quote_provenance: str) -> FindingT:
        """Build the final finding object from a scored candidate."""
        pass

    def _vault_age(self) -> int:
        """Compute vault age in days (oldest note's age)."""
        rows = self.conn.execute(
            "SELECT created FROM notes WHERE created IS NOT NULL"
        ).fetchall()
        ages = []
        for r in rows:
            try:
                d = date.fromisoformat(r[0][:10])
                ages.append((self.today - d).days)
            except (ValueError, TypeError):
                pass
        return max(ages) if ages else 0

    def _clamp(self, v: int, lo: int, hi: int) -> int:
        """Clamp value to [lo, hi]."""
        return max(lo, min(hi, v))

    def run(self, top_n: int = 1, **kwargs) -> VerbResult[FindingT]:
        """Execute the verb pipeline.

        Args:
            top_n: Number of findings to return
            **kwargs: Verb-specific overrides (e.g., min_sim, min_cluster_size)

        Returns:
            VerbResult with findings, thresholds, and metadata
        """
        vault_age = self._vault_age()
        thresholds = self._threshold()

        # Get candidates
        raw = self._candidates()

        # Score and filter
        scored = []
        for c in raw:
            if not self._filter(c, thresholds):
                continue
            score = self._score(c)
            scored.append((score, c))

        # Sort by score descending
        scored.sort(key=lambda x: -x[0])

        # Build findings
        findings = []
        for score, c in scored[:top_n]:
            quote, provenance = self._quote(c)
            if not quote:
                continue
            finding = self._build_finding(c, quote, provenance)
            findings.append(finding)

        return VerbResult(
            verb=self.name,
            findings=findings,
            thresholds=thresholds,
            vault_age_days=vault_age,
            metadata={"top_n": top_n, **kwargs}
        )

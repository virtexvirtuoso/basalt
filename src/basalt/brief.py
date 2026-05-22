"""Brief compiler — orchestrates all 5 verbs to produce the Basalt Brief.

The Brief is the core Basalt output: a 5-section document surfaced to the user
each day (or on-demand). Each section is powered by one verb:

1. **Implicit Thesis** — "The thing you keep saying without realizing you're saying the same thing"
2. **Buried Insight** — "The brilliant thing you wrote and forgot about"
3. **Drift** — "What you say is the priority versus what you actually spent the week on"
4. **Contradiction** — "The two notes you wrote that can't both be true"
5. **Connection** — "The two ideas in different folders that turn out to be the same idea"

This module runs all 5 verbs, collects their findings, and renders them into
a structured markdown document ready for display or export.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from basalt.buried import BuriedInsightVerb, BuriedInsight
from basalt.implicit_thesis import ImplicitThesisVerb, ThesisCluster
from basalt.connection import ConnectionVerb, ConnectionPair
from basalt.drift import DriftVerb, DriftFinding
from basalt.contradiction import ContradictionVerb, ContradictionPair


@dataclass
class BriefSection:
    """One section of the Brief."""

    verb: str
    title: str
    site_language: str  # The "site language" tagline
    findings: list[Any]  # Verb-specific finding type
    thresholds: dict
    vault_age_days: int
    metadata: dict = field(default_factory=dict)


@dataclass
class Brief:
    """The complete Basalt Brief."""

    generated_at: date
    vault_path: str
    vault_age_days: int
    sections: list[BriefSection]
    metadata: dict = field(default_factory=dict)

    def render_markdown(self) -> str:
        """Render the Brief as markdown."""
        lines = [
            f"# Basalt Brief",
            f"",
            f"**Generated:** {self.generated_at.isoformat()}",
            f"**Vault:** {self.vault_path}",
            f"**Vault Age:** {self.vault_age_days} days",
            f"",
            f"---",
            f"",
        ]

        for section in self.sections:
            lines.extend(self._render_section(section))
            lines.append("")
            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    def _render_section(self, section: BriefSection) -> list[str]:
        """Render one section as markdown lines."""
        lines = [
            f"## {section.title}",
            f"",
            f"*{section.site_language}*",
            f"",
        ]

        if not section.findings:
            lines.append("*No findings in this section today.*")
            lines.append("")
            return lines

        # Render based on verb type
        if section.verb == "buried-insight":
            lines.extend(self._render_buried_insight(section.findings))
        elif section.verb == "implicit-thesis":
            lines.extend(self._render_implicit_thesis(section.findings))
        elif section.verb == "connection":
            lines.extend(self._render_connection(section.findings))
        elif section.verb == "drift":
            lines.extend(self._render_drift(section.findings))
        elif section.verb == "contradiction":
            lines.extend(self._render_contradiction(section.findings))

        return lines

    def _render_buried_insight(self, findings: list[BuriedInsight]) -> list[str]:
        """Render buried-insight findings."""
        lines = []
        for i, finding in enumerate(findings, 1):
            cand = finding.candidate
            lines.append(f"### {i}. {cand.title}")
            lines.append("")
            lines.append(f"**Path:** `{cand.rel_path}`")
            lines.append(
                f"**Age:** {(self.generated_at - cand.created).days} days old, dormant for {(self.generated_at - cand.updated).days} days"
            )
            lines.append("")
            lines.append(f"> {finding.quote}")
            lines.append(f"> — *{finding.quote_provenance}*")
            lines.append("")
            if finding.validators:
                lines.append("**Validated by:**")
                for v in finding.validators[:3]:
                    link_status = "✓" if v.explicit_link else "≈"
                    lines.append(
                        f"- {link_status} [[{v.title}]]({v.rel_path}) — updated {(self.generated_at - v.updated).days} days ago"
                    )
                lines.append("")
        return lines

    def _render_implicit_thesis(self, findings: list[ThesisCluster]) -> list[str]:
        """Render implicit-thesis findings."""
        lines = []
        for i, cluster in enumerate(findings, 1):
            lines.append(f"### {i}. {cluster.centroid_title}")
            lines.append("")
            lines.append(f"**Centroid:** `{cluster.centroid_path}`")
            lines.append(
                f"**Cluster Size:** {cluster.cluster_size} notes across {cluster.folder_diversity} folders"
            )
            lines.append(f"**Time Span:** {cluster.span_days} days")
            lines.append("")
            lines.append(f"> {cluster.centroid_quote}")
            lines.append(f"> — *{cluster.centroid_quote_provenance}*")
            lines.append("")
            lines.append("**Rephrasings:**")
            for j, (path, title, quote, prov) in enumerate(
                zip(
                    cluster.member_paths,
                    cluster.member_titles,
                    cluster.member_quotes,
                    cluster.member_quote_provenances,
                ),
                1,
            ):
                if j == 1:
                    continue  # skip centroid, already shown
                if quote:
                    lines.append(f"- **{title}** ({path.split('/')[0]})")
                    lines.append(f"  > {quote}")
            lines.append("")
        return lines

    def _render_connection(self, findings: list[ConnectionPair]) -> list[str]:
        """Render connection findings."""
        lines = []
        for i, pair in enumerate(findings, 1):
            lines.append(f"### {i}. Cross-Folder Duplicate")
            lines.append("")
            lines.append(
                f"**Similarity:** {pair.similarity:.2f} | **Score:** {pair.score:.3f}"
            )
            lines.append("")
            lines.append(f"#### A: {pair.note_a_title}")
            lines.append(f"`{pair.note_a_path}`")
            lines.append("")
            lines.append(f"> {pair.note_a_quote}")
            lines.append(f"> — *{pair.note_a_quote_provenance}*")
            lines.append("")
            lines.append(f"#### B: {pair.note_b_title}")
            lines.append(f"`{pair.note_b_path}`")
            lines.append("")
            lines.append(f"> {pair.note_b_quote}")
            lines.append(f"> — *{pair.note_b_quote_provenance}*")
            lines.append("")
        return lines

    def _render_drift(self, findings: list[DriftFinding]) -> list[str]:
        """Render drift findings."""
        lines = []
        for i, finding in enumerate(findings, 1):
            lines.append(f"### {i}. Priority Drift")
            lines.append("")
            lines.append(
                f"**Window:** {finding.window_days} days | **Daily Notes:** {finding.daily_note_count} | **Projects:** {finding.project_count}"
            )
            lines.append("")
            if finding.headline_overworked:
                ow = finding.headline_overworked
                lines.append(f"**Overworked:** {ow.name}")
                lines.append(
                    f"- Stated: #{ow.stated_rank} ({ow.stated_notes} notes, {ow.stated_share * 100:.1f}%)"
                )
                lines.append(
                    f"- Lived: #{ow.lived_rank} ({ow.lived_mentions} mentions, {ow.lived_share * 100:.1f}%)"
                )
                lines.append(f"- **Drift:** +{ow.drift_pct:.1f}pp")
                lines.append("")
            if finding.headline_underworked:
                uw = finding.headline_underworked
                lines.append(f"**Underworked:** {uw.name}")
                lines.append(
                    f"- Stated: #{uw.stated_rank} ({uw.stated_notes} notes, {uw.stated_share * 100:.1f}%)"
                )
                lines.append(
                    f"- Lived: #{uw.lived_rank} ({uw.lived_mentions} mentions, {uw.lived_share * 100:.1f}%)"
                )
                lines.append(f"- **Drift:** {uw.drift_pct:.1f}pp")
                lines.append("")
        return lines

    def _render_contradiction(self, findings: list[ContradictionPair]) -> list[str]:
        """Render contradiction findings."""
        lines = []
        for i, pair in enumerate(findings, 1):
            lines.append(f"### {i}. Potential Contradiction")
            lines.append("")
            lines.append(
                f"**Topical Similarity:** {pair.similarity:.2f} | **Contradiction Score:** {pair.contradiction_score:.1f} | **Signals:** {', '.join(pair.signals)}"
            )
            lines.append("")
            lines.append(f"#### A: {pair.note_a_title}")
            lines.append(f"`{pair.note_a_path}`")
            lines.append("")
            lines.append(f"> {pair.note_a_quote}")
            lines.append(f"> — *{pair.note_a_quote_provenance}*")
            lines.append("")
            lines.append(f"#### B: {pair.note_b_title}")
            lines.append(f"`{pair.note_b_path}`")
            lines.append("")
            lines.append(f"> {pair.note_b_quote}")
            lines.append(f"> — *{pair.note_b_quote_provenance}*")
            lines.append("")
            lines.append(
                f"**Verdict:** ⚠️ Review required — these claims may be incompatible"
            )
            lines.append("")
        return lines


DRIFT_PROMOTION_THRESHOLD_PP = 5.0


def reorder_for_drift_magnitude(
    sections: list[BriefSection],
    threshold_pp: float = DRIFT_PROMOTION_THRESHOLD_PP,
) -> list[BriefSection]:
    """Promote the drift section to the top when |max drift delta| > threshold.

    Per [[V0-Verb-Quality-Fixes-Spec-2026-05-22]] Phase E: Drift is the
    highest signal-to-noise section in the Brief; when it surfaces a
    meaningful stated-vs-lived gap, it deserves to lead. When drift is
    flat (or absent), preserve the original section order.

    Strict inequality: score must be STRICTLY greater than threshold to
    promote. Score == threshold keeps original order.

    Returns a new list; does not mutate the input.
    """
    drift_idx = next((i for i, s in enumerate(sections) if s.verb == "drift"), None)
    if drift_idx is None:
        return list(sections)

    drift_section = sections[drift_idx]
    if not drift_section.findings:
        return list(sections)

    top_score = max(
        (getattr(f, "score", 0.0) for f in drift_section.findings),
        default=0.0,
    )
    if top_score <= threshold_pp:
        return list(sections)

    # Promote drift to position 0, keep relative order of the rest.
    return [drift_section] + [s for i, s in enumerate(sections) if i != drift_idx]


def compile_brief(
    conn: sqlite3.Connection,
    vault_path: str = "~/virtuoso-vault",
    today: date | None = None,
    top_n: int = 1,
) -> Brief:
    """Compile the complete Basalt Brief by running all 5 verbs.

    Args:
        conn: SQLite connection to the vault index
        vault_path: Path to the vault (for display)
        today: Date to use as "today" (defaults to actual today)
        top_n: Number of findings per section

    Returns:
        Brief object with all 5 sections populated
    """
    today = today or date.today()

    sections: list[BriefSection] = []

    # 1. Implicit Thesis
    thesis_verb = ImplicitThesisVerb(conn)
    thesis_result = thesis_verb.run(top_n=top_n)
    sections.append(
        BriefSection(
            verb="implicit-thesis",
            title="Implicit Thesis",
            site_language="The thing you keep saying without realizing you're saying the same thing",
            findings=thesis_result.findings,
            thresholds=thesis_result.thresholds,
            vault_age_days=thesis_result.vault_age_days,
            metadata={"top_n": top_n},
        )
    )

    # 2. Buried Insight
    buried_verb = BuriedInsightVerb(conn, today)
    buried_result = buried_verb.run(top_n=top_n)
    sections.append(
        BriefSection(
            verb="buried-insight",
            title="Buried Insight",
            site_language="The brilliant thing you wrote and forgot about",
            findings=buried_result.findings,
            thresholds=buried_result.thresholds,
            vault_age_days=buried_result.vault_age_days,
            metadata={"top_n": top_n},
        )
    )

    # 3. Drift
    drift_verb = DriftVerb(conn, today)
    drift_findings = drift_verb.run(top_n=top_n)
    sections.append(
        BriefSection(
            verb="drift",
            title="Drift",
            site_language="What you say is the priority versus what you actually spent the week on",
            findings=drift_findings,
            thresholds={"window_days": 30},
            vault_age_days=sections[0].vault_age_days,  # reuse from first section
            metadata={"top_n": top_n},
        )
    )

    # 4. Contradiction
    contradiction_verb = ContradictionVerb(conn)
    contradiction_findings = contradiction_verb.run(top_n=top_n)
    sections.append(
        BriefSection(
            verb="contradiction",
            title="Contradiction",
            site_language="The two notes you wrote that can't both be true",
            findings=contradiction_findings,
            thresholds={"min_sim": 0.72},
            vault_age_days=sections[0].vault_age_days,
            metadata={"top_n": top_n},
        )
    )

    # 5. Connection
    connection_verb = ConnectionVerb(conn)
    connection_result = connection_verb.run(top_n=top_n)
    sections.append(
        BriefSection(
            verb="connection",
            title="Connection",
            site_language="The two ideas in different folders that turn out to be the same idea",
            findings=connection_result.findings,
            thresholds=connection_result.thresholds,
            vault_age_days=connection_result.vault_age_days,
            metadata={"top_n": top_n},
        )
    )

    # Phase E (2026-05-22): promote Drift when |max delta| > 5pp.
    # Drift is the highest signal-to-noise section per the dogfood findings;
    # when meaningful it leads, otherwise the original order stands.
    vault_age_days = sections[0].vault_age_days
    sections = reorder_for_drift_magnitude(sections)

    return Brief(
        generated_at=today,
        vault_path=vault_path,
        vault_age_days=vault_age_days,
        sections=sections,
        metadata={"top_n": top_n, "verb_count": 5},
    )


# ── Convenience wrapper ─────────────────────────────────


def generate_brief(
    db_path: str,
    vault_path: str = "~/virtuoso-vault",
    today: date | None = None,
    top_n: int = 1,
) -> Brief:
    """Generate a Brief from a database file path.

    Args:
        db_path: Path to the SQLite vault index database
        vault_path: Path to the vault (for display)
        today: Date to use as "today"
        top_n: Number of findings per section

    Returns:
        Brief object with all sections populated
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        return compile_brief(conn, vault_path, today, top_n)
    finally:
        conn.close()

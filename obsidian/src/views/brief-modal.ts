/**
 * BriefModal — renders a Basalt Brief response inside Obsidian.
 *
 * v0.1 is deliberately ugly-on-purpose: a structured dump of the JSON the
 * MCP server returned, with click-to-open on every rel_path. Pretty styling
 * comes in v0.2 once the bridge is stable.
 */

import { App, Modal, Setting, TFile, Notice } from "obsidian";
import type { BriefResponse, BuriedInsightFinding, PairFinding } from "../types";

export class BriefModal extends Modal {
  private response: BriefResponse;

  constructor(app: App, response: BriefResponse) {
    super(app);
    this.response = response;
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.addClass("basalt-brief-modal");

    contentEl.createEl("h2", { text: "Basalt Brief" });

    // Track record bar (always show, even if 0/0)
    this.renderTrackRecord(contentEl);

    const { findings } = this.response;
    if (!findings || (!findings.buried_insight && !findings.connection && !findings.contradiction)) {
      contentEl.createEl("p", {
        text: "No findings. Try a different section, lower the similarity threshold, or run `basalt index` first.",
        cls: "basalt-empty",
      });
      return;
    }

    if (findings.buried_insight?.length) {
      contentEl.createEl("h3", { text: "Buried Insight" });
      for (const f of findings.buried_insight) {
        this.renderBuried(contentEl, f);
      }
    }
    if (findings.connection?.length) {
      contentEl.createEl("h3", { text: "Connection" });
      for (const f of findings.connection) {
        this.renderPair(contentEl, f, "connection");
      }
    }
    if (findings.contradiction?.length) {
      contentEl.createEl("h3", { text: "Contradiction (v0 heuristic)" });
      for (const f of findings.contradiction) {
        this.renderPair(contentEl, f, "contradiction");
      }
    }
  }

  onClose(): void {
    this.contentEl.empty();
  }

  // ── renderers ──────────────────────────────────────────

  private renderTrackRecord(parent: HTMLElement): void {
    const tr = this.response.track_record;
    const div = parent.createDiv({ cls: "basalt-track-record" });
    div.createEl("strong", { text: `Track record · last ${tr.window_days}d` });
    div.createSpan({
      text: ` ${tr.confirmed} confirmed · ${tr.pending} pending · ${tr.falsified} falsified  (${tr.total} total)`,
      cls: "basalt-track-record-counts",
    });
  }

  private renderBuried(parent: HTMLElement, f: BuriedInsightFinding): void {
    const card = parent.createDiv({ cls: "basalt-finding" });
    const path = card.createEl("a", { text: f.rel_path, cls: "basalt-path" });
    path.onclick = (e) => {
      e.preventDefault();
      this.openNote(f.rel_path);
    };
    card.createEl("blockquote", { text: f.quote });
    card.createEl("small", {
      text: `${f.quote_provenance} · ${f.validators.length} validator${f.validators.length === 1 ? "" : "s"}`,
      cls: "basalt-meta",
    });
    this.renderFalsification(card, f.falsification);
  }

  private renderPair(parent: HTMLElement, f: PairFinding, kind: "connection" | "contradiction"): void {
    const card = parent.createDiv({ cls: "basalt-finding" });
    const sim = f.similarity ?? f.topical_similarity ?? 0;
    const header = card.createEl("p", { cls: "basalt-meta" });
    header.createSpan({ text: `${kind === "connection" ? "similarity" : "topical similarity"} ${sim.toFixed(2)}` });
    if (kind === "contradiction" && f.signals?.length) {
      header.createSpan({ text: ` · signals: ${f.signals.join(", ")}` });
    }
    for (const side of ["A", "B"] as const) {
      const note = side === "A" ? f.note_a : f.note_b;
      const sideDiv = card.createDiv({ cls: "basalt-pair-side" });
      const path = sideDiv.createEl("a", { text: `${side} · ${note.rel_path}`, cls: "basalt-path" });
      path.onclick = (e) => {
        e.preventDefault();
        this.openNote(note.rel_path);
      };
      sideDiv.createEl("blockquote", { text: note.quote });
      sideDiv.createEl("small", { text: note.quote_provenance, cls: "basalt-meta" });
    }
    this.renderFalsification(card, f.falsification);
  }

  private renderFalsification(parent: HTMLElement, rules: { text: string }[] | undefined): void {
    if (!rules?.length) return;
    const block = parent.createDiv({ cls: "basalt-falsification" });
    block.createEl("strong", { text: "⊘ Falsification — this is wrong if:" });
    const ul = block.createEl("ul");
    for (const r of rules) ul.createEl("li", { text: r.text });
  }

  private openNote(relPath: string): void {
    const file = this.app.vault.getAbstractFileByPath(relPath);
    if (file instanceof TFile) {
      this.app.workspace.getLeaf(true).openFile(file);
      this.close();
    } else {
      new Notice(`Can't open ${relPath} — note not found in this vault.`);
    }
  }
}

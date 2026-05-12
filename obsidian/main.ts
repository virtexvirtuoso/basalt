/**
 * Basalt Obsidian plugin — entry point.
 *
 * Architecture: this plugin is a thin TypeScript shell over basalt-mcp (the
 * MCP server shipped in basalt-vault v0.0.5). All verb logic lives in Python;
 * the plugin just spawns the binary, calls tools, and renders results inside
 * Obsidian. See vault Plugin-Scope-2026-05-08.md for the architecture
 * decision (MCP-bridge, not embeddings-in-TypeScript).
 */

import { App, Notice, Plugin, PluginSettingTab, Setting } from "obsidian";
import { MCPClient, ToolName } from "./src/mcp-client";
import type { BriefResponse, AuditResponse } from "./src/types";
import { BriefModal } from "./src/views/brief-modal";

interface BasaltSettings {
  /** Path to the basalt-mcp executable. Empty = look up via PATH. */
  mcpCommand: string;
  /** Optional vault path override; empty = the basalt-mcp default (~/virtuoso-vault). */
  vaultPath: string;
  /** Optional DB path override; empty = the basalt-mcp default (~/.basalt/basalt.db). */
  dbPath: string;
  /** How many findings per section to request. */
  topN: number;
}

const DEFAULT_SETTINGS: BasaltSettings = {
  mcpCommand: "",
  vaultPath: "",
  dbPath: "",
  topN: 2,
};

export default class BasaltPlugin extends Plugin {
  settings!: BasaltSettings;
  private client: MCPClient | null = null;
  private clientStartPromise: Promise<void> | null = null;

  async onload(): Promise<void> {
    await this.loadSettings();

    // Plugin loads fast; subprocess spawn is deferred to first command call —
    // avoids a blocking startup if basalt-mcp is missing or slow to boot.
    this.addCommand({
      id: "compile-brief",
      name: "Compile Brief (all sections)",
      callback: () => this.runBrief("all"),
    });
    this.addCommand({
      id: "find-buried-insight",
      name: "Find buried insight",
      callback: () => this.runBrief("buried-insight"),
    });
    this.addCommand({
      id: "find-connections",
      name: "Find connections",
      callback: () => this.runBrief("connection"),
    });
    this.addCommand({
      id: "find-contradictions",
      name: "Find contradictions (v0 heuristic)",
      callback: () => this.runBrief("contradiction"),
    });
    this.addCommand({
      id: "audit-past-briefs",
      name: "Audit past briefs",
      callback: () => this.runAudit(),
    });

    this.addSettingTab(new BasaltSettingTab(this.app, this));
  }

  async onunload(): Promise<void> {
    if (this.client) {
      await this.client.dispose();
      this.client = null;
    }
  }

  // ── core flow ───────────────────────────────────────────

  private async ensureClient(): Promise<MCPClient> {
    if (this.client) return this.client;
    if (this.clientStartPromise) {
      await this.clientStartPromise;
      if (this.client) return this.client;
    }

    const args: string[] = [];
    if (this.settings.vaultPath) args.push("--vault", this.settings.vaultPath);
    if (this.settings.dbPath) args.push("--db", this.settings.dbPath);

    const client = new MCPClient({
      command: this.settings.mcpCommand || "basalt-mcp",
      args,
      log: (line) => console.log(`[basalt]`, line),
    });

    this.clientStartPromise = client.start();
    try {
      await this.clientStartPromise;
      this.client = client;
      return client;
    } catch (err) {
      this.clientStartPromise = null;
      throw err;
    }
  }

  private async runBrief(section: string): Promise<void> {
    const notice = new Notice(`Basalt: compiling ${section}…`, 0);
    try {
      const client = await this.ensureClient();
      const result = (await client.callTool("basalt_brief", {
        section,
        top: this.settings.topN,
      })) as { structuredContent?: BriefResponse; content?: unknown };

      // FastMCP returns {structuredContent, content[]}; we want the structured payload.
      const payload = result.structuredContent ?? this.extractContent(result);
      if (!payload) {
        new Notice(`Basalt: empty response from MCP server`);
        return;
      }
      notice.hide();
      new BriefModal(this.app, payload as BriefResponse).open();
    } catch (err) {
      notice.hide();
      const msg = err instanceof Error ? err.message : String(err);
      new Notice(`Basalt error: ${msg}`, 8000);
      console.error("[basalt]", err);
    }
  }

  private async runAudit(): Promise<void> {
    const notice = new Notice("Basalt: running audit…", 0);
    try {
      const client = await this.ensureClient();
      const result = (await client.callTool("basalt_audit", { days: 90 })) as {
        structuredContent?: AuditResponse;
        content?: unknown;
      };
      notice.hide();
      const payload = result.structuredContent ?? this.extractContent(result);
      if (!payload) {
        new Notice("Basalt: empty audit response");
        return;
      }
      const a = payload as AuditResponse;
      const tr = a.track_record;
      new Notice(
        `Basalt audit · ${a.verdicts.length} updated · ` +
          `track record: ${tr.confirmed} ✓ / ${tr.pending} … / ${tr.falsified} ✗  (${tr.total})`,
        12000,
      );
    } catch (err) {
      notice.hide();
      const msg = err instanceof Error ? err.message : String(err);
      new Notice(`Basalt audit error: ${msg}`, 8000);
      console.error("[basalt]", err);
    }
  }

  /** Fallback: parse the JSON inside `content[]` when `structuredContent` is absent.
   *  Older FastMCP versions return only `content` with the JSON serialized as a text item. */
  private extractContent(result: { content?: unknown }): unknown {
    const content = result.content;
    if (!Array.isArray(content) || !content.length) return null;
    const first = content[0] as { type?: string; text?: string };
    if (first.type === "text" && typeof first.text === "string") {
      try {
        return JSON.parse(first.text);
      } catch {
        return null;
      }
    }
    return null;
  }

  // ── settings ────────────────────────────────────────────

  async loadSettings(): Promise<void> {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
  }
}

class BasaltSettingTab extends PluginSettingTab {
  plugin: BasaltPlugin;

  constructor(app: App, plugin: BasaltPlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "Basalt" });
    containerEl.createEl("p", {
      text:
        "Basalt reads your vault and surfaces what you believe but never wrote down. " +
        "This plugin bridges to the local `basalt-mcp` server. Install with " +
        "`pip install basalt-vault[mcp]` then run `basalt index --vault ~/your-vault` once.",
    });

    new Setting(containerEl)
      .setName("basalt-mcp command")
      .setDesc("Path to the basalt-mcp executable. Leave empty to look up via $PATH.")
      .addText((t) =>
        t
          .setPlaceholder("basalt-mcp")
          .setValue(this.plugin.settings.mcpCommand)
          .onChange(async (v) => {
            this.plugin.settings.mcpCommand = v.trim();
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("Vault path override")
      .setDesc("Defaults to basalt-mcp's $BASALT_VAULT or ~/virtuoso-vault. Set if your vault is elsewhere.")
      .addText((t) =>
        t
          .setPlaceholder("/path/to/your/vault")
          .setValue(this.plugin.settings.vaultPath)
          .onChange(async (v) => {
            this.plugin.settings.vaultPath = v.trim();
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("DB path override")
      .setDesc("Defaults to ~/.basalt/basalt.db. Override if you keep the index elsewhere.")
      .addText((t) =>
        t
          .setPlaceholder("~/.basalt/basalt.db")
          .setValue(this.plugin.settings.dbPath)
          .onChange(async (v) => {
            this.plugin.settings.dbPath = v.trim();
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("Findings per section")
      .setDesc("How many findings to surface per section in a Brief (1–10).")
      .addSlider((s) =>
        s
          .setLimits(1, 10, 1)
          .setDynamicTooltip()
          .setValue(this.plugin.settings.topN)
          .onChange(async (v) => {
            this.plugin.settings.topN = v;
            await this.plugin.saveSettings();
          }),
      );
  }
}

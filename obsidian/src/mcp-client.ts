/**
 * MCP client — speaks JSON-RPC 2.0 over a child process's stdin/stdout.
 *
 * This is the bridge to `basalt-mcp` (shipped in basalt-vault v0.0.5). The
 * MCP spec uses newline-delimited JSON for the stdio transport: one JSON
 * object per line, separated by `\n`. Each request carries an integer `id`
 * and the response uses the same id. Notifications have no id.
 *
 * v0 covers exactly what the plugin needs today:
 *   1. spawn() — start the subprocess
 *   2. initialize() — perform the MCP handshake
 *   3. callTool(name, args) — invoke a tool by name
 *   4. dispose() — clean shutdown
 *
 * Deliberately omitted from v0 (see Plugin-Scope-2026-05-08.md):
 *   - resources/list, prompts/list — Basalt's MCP server doesn't expose them
 *   - server-initiated sampling — Basalt doesn't initiate LLM calls
 *   - reconnection state machine — handled at the plugin layer for now
 */

import { ChildProcess, spawn } from "child_process";

export type ToolName =
  | "basalt_brief"
  | "basalt_connection"
  | "basalt_contradiction"
  | "basalt_audit";

interface JsonRpcRequest {
  jsonrpc: "2.0";
  id: number;
  method: string;
  params?: unknown;
}

interface JsonRpcResponse {
  jsonrpc: "2.0";
  id: number;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

interface JsonRpcNotification {
  jsonrpc: "2.0";
  method: string;
  params?: unknown;
}

type PendingResolver = (response: JsonRpcResponse) => void;

const PROTOCOL_VERSION = "2024-11-05"; // matches the 2025-11-25 spec's wire format
const CLIENT_INFO = { name: "basalt-obsidian-plugin", version: "0.1.0" };

export interface MCPClientOptions {
  /** Path to the basalt-mcp executable. If undefined, falls back to PATH lookup. */
  command?: string;
  /** Extra argv (e.g. ["--vault", "/path/to/vault"]). */
  args?: string[];
  /** Extra environment variables to merge with process.env. */
  env?: Record<string, string>;
  /** Working directory for the subprocess. */
  cwd?: string;
  /** Logger for diagnostic output. */
  log?: (line: string) => void;
}

export class MCPClient {
  private proc: ChildProcess | null = null;
  private nextId = 1;
  private pending = new Map<number, PendingResolver>();
  private buffer = "";
  private initialized = false;
  private readonly opts: Required<Omit<MCPClientOptions, "log" | "env" | "cwd">> & {
    log: (line: string) => void;
    env: Record<string, string>;
    cwd: string | undefined;
  };

  constructor(opts: MCPClientOptions = {}) {
    this.opts = {
      command: opts.command ?? "basalt-mcp",
      args: opts.args ?? [],
      env: opts.env ?? {},
      cwd: opts.cwd,
      log: opts.log ?? (() => {}),
    };
  }

  /**
   * Spawn the basalt-mcp subprocess and perform the MCP `initialize` handshake.
   * Throws if the binary can't be found, exits early, or rejects the handshake.
   */
  async start(): Promise<void> {
    if (this.proc) throw new Error("MCPClient already started");

    this.proc = spawn(this.opts.command, this.opts.args, {
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, ...this.opts.env },
      cwd: this.opts.cwd,
    });

    this.proc.on("error", (err) => {
      this.opts.log(`subprocess error: ${err.message}`);
      this.failAllPending(new Error(`subprocess error: ${err.message}`));
    });

    this.proc.on("exit", (code, signal) => {
      this.opts.log(`subprocess exited code=${code} signal=${signal}`);
      this.failAllPending(new Error(`subprocess exited (code=${code} signal=${signal})`));
      this.proc = null;
      this.initialized = false;
    });

    this.proc.stdout?.setEncoding("utf8");
    this.proc.stdout?.on("data", (chunk: string) => this.onStdout(chunk));

    // stderr is diagnostic — log but don't fail on it
    this.proc.stderr?.setEncoding("utf8");
    this.proc.stderr?.on("data", (chunk: string) => {
      const trimmed = chunk.trim();
      if (trimmed) this.opts.log(`[basalt-mcp stderr] ${trimmed}`);
    });

    // MCP handshake
    const initResp = await this.request("initialize", {
      protocolVersion: PROTOCOL_VERSION,
      capabilities: {},
      clientInfo: CLIENT_INFO,
    });
    if (initResp.error) {
      throw new Error(`MCP initialize rejected: ${initResp.error.message}`);
    }
    // Spec requires this notification after a successful initialize.
    this.notify("notifications/initialized", {});
    this.initialized = true;
  }

  /**
   * Invoke a tool by name. Returns the tool's response payload (the part the
   * Basalt server defines as "structured content" — for our 4 tools, it's
   * always a JSON object).
   */
  async callTool(name: ToolName, args: Record<string, unknown> = {}): Promise<unknown> {
    if (!this.initialized) throw new Error("MCPClient not started — call start() first");
    const resp = await this.request("tools/call", { name, arguments: args });
    if (resp.error) {
      throw new Error(`tool ${name} failed: ${resp.error.message}`);
    }
    return resp.result;
  }

  /**
   * List the tools the server currently exposes. Useful for the plugin's
   * settings UI and for verifying compatibility with newer basalt-mcp versions.
   */
  async listTools(): Promise<unknown> {
    if (!this.initialized) throw new Error("MCPClient not started — call start() first");
    const resp = await this.request("tools/list", {});
    if (resp.error) {
      throw new Error(`tools/list failed: ${resp.error.message}`);
    }
    return resp.result;
  }

  /**
   * Stop the subprocess. Resolves once the process exits or the kill signal
   * has been delivered.
   */
  async dispose(): Promise<void> {
    if (!this.proc) return;
    const proc = this.proc;
    this.proc = null;
    this.initialized = false;
    this.failAllPending(new Error("client disposed"));
    return new Promise<void>((resolve) => {
      proc.once("exit", () => resolve());
      proc.kill("SIGTERM");
      // Hard kill after 2s if it hasn't shut down
      setTimeout(() => {
        if (!proc.killed) proc.kill("SIGKILL");
        resolve();
      }, 2000);
    });
  }

  // ── internals ───────────────────────────────────────────────

  private request(method: string, params: unknown): Promise<JsonRpcResponse> {
    if (!this.proc?.stdin) return Promise.reject(new Error("subprocess not running"));
    const id = this.nextId++;
    const msg: JsonRpcRequest = { jsonrpc: "2.0", id, method, params };
    return new Promise<JsonRpcResponse>((resolve) => {
      this.pending.set(id, resolve);
      this.proc!.stdin!.write(JSON.stringify(msg) + "\n");
    });
  }

  private notify(method: string, params: unknown): void {
    if (!this.proc?.stdin) return;
    const msg: JsonRpcNotification = { jsonrpc: "2.0", method, params };
    this.proc.stdin.write(JSON.stringify(msg) + "\n");
  }

  private onStdout(chunk: string): void {
    this.buffer += chunk;
    let nl: number;
    // newline-delimited JSON per MCP stdio transport
    while ((nl = this.buffer.indexOf("\n")) >= 0) {
      const line = this.buffer.slice(0, nl).trim();
      this.buffer = this.buffer.slice(nl + 1);
      if (!line) continue;
      this.handleLine(line);
    }
  }

  private handleLine(line: string): void {
    let msg: JsonRpcResponse | JsonRpcNotification;
    try {
      msg = JSON.parse(line);
    } catch {
      this.opts.log(`failed to parse line: ${line}`);
      return;
    }
    if ("id" in msg && typeof msg.id === "number") {
      const pending = this.pending.get(msg.id);
      if (pending) {
        this.pending.delete(msg.id);
        pending(msg as JsonRpcResponse);
      }
    } else {
      // server notification — log for now; v0.2 can route these
      this.opts.log(`notification: ${(msg as JsonRpcNotification).method}`);
    }
  }

  private failAllPending(err: Error): void {
    for (const [, resolver] of this.pending) {
      resolver({
        jsonrpc: "2.0",
        id: -1,
        error: { code: -32000, message: err.message },
      });
    }
    this.pending.clear();
  }
}

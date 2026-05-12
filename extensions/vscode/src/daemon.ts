/**
 * Thin HTTP client for the mnemo daemon. Uses the global `fetch` available
 * in VS Code's Node 18+ runtime; no third-party deps.
 *
 * All endpoints live under /v1/. The class is small and stateless so tests
 * can construct one per test case.
 */

import * as vscode from "vscode";

export interface QueryHit {
  id: string;
  type: string;
  name: string;
  description: string | null;
  body: string | null;
  score: number;
  citation: string;
}

export interface QueryResult {
  hits: QueryHit[];
  intent_tags: string[];
  tokens_used: number;
  query_id: string;
}

export interface ActiveProject {
  project_key: string;
  path: string;
  since: number;
}

export interface SourceOut {
  path: string;
  kind: string;
  project_key: string | null;
  last_indexed_at: number | null;
  enabled: boolean;
  include: string | null;
  exclude: string | null;
}

export class MnemoDaemon {
  constructor(private readonly baseUrl: string = MnemoDaemon.configuredUrl()) {}

  static configuredUrl(): string {
    return (
      vscode.workspace.getConfiguration("mnemo").get<string>("daemonUrl") ??
      "http://127.0.0.1:7373"
    );
  }

  private url(path: string): string {
    return this.baseUrl.replace(/\/$/, "") + path;
  }

  async health(): Promise<{ ok: boolean; version: string } | null> {
    try {
      const r = await fetch(this.url("/v1/health"), { signal: AbortSignal.timeout(2000) });
      if (!r.ok) return null;
      return (await r.json()) as { ok: boolean; version: string };
    } catch {
      return null;
    }
  }

  async query(
    prompt: string,
    opts: { k?: number; budgetTokens?: number; projectKey?: string } = {}
  ): Promise<QueryResult | null> {
    const cfg = vscode.workspace.getConfiguration("mnemo");
    const body: Record<string, unknown> = {
      prompt,
      k: opts.k ?? cfg.get<number>("k") ?? 5,
      budget_tokens: opts.budgetTokens ?? cfg.get<number>("budgetTokens") ?? 800,
    };
    if (opts.projectKey) body.project_key = opts.projectKey;
    try {
      const r = await fetch(this.url("/v1/query"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(60000),
      });
      if (!r.ok) return null;
      return (await r.json()) as QueryResult;
    } catch {
      return null;
    }
  }

  async getActiveProject(): Promise<ActiveProject | null> {
    try {
      const r = await fetch(this.url("/v1/projects/active"), {
        signal: AbortSignal.timeout(2000),
      });
      if (!r.ok) return null;
      const text = await r.text();
      return text ? (JSON.parse(text) as ActiveProject) : null;
    } catch {
      return null;
    }
  }

  async setActiveProject(path: string): Promise<ActiveProject | null> {
    try {
      const r = await fetch(this.url("/v1/projects/active"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
        signal: AbortSignal.timeout(5000),
      });
      if (!r.ok) return null;
      return (await r.json()) as ActiveProject;
    } catch {
      return null;
    }
  }

  async clearActiveProject(): Promise<void> {
    try {
      await fetch(this.url("/v1/projects/active"), {
        method: "DELETE",
        signal: AbortSignal.timeout(2000),
      });
    } catch {
      /* additive */
    }
  }

  async listSources(): Promise<SourceOut[]> {
    try {
      const r = await fetch(this.url("/v1/sources"), { signal: AbortSignal.timeout(5000) });
      if (!r.ok) return [];
      return (await r.json()) as SourceOut[];
    } catch {
      return [];
    }
  }

  async reindex(): Promise<{
    added: number;
    updated: number;
    unchanged: number;
    removed: number;
    errors: unknown[];
  } | null> {
    try {
      const r = await fetch(this.url("/v1/reindex"), {
        method: "POST",
        signal: AbortSignal.timeout(120000),
      });
      if (!r.ok) return null;
      return (await r.json()) as never;
    } catch {
      return null;
    }
  }

  async addNote(opts: {
    type: string;
    name: string;
    description?: string;
    body: string;
    project_key?: string | null;
    base?: boolean;
  }): Promise<{ id: string; name: string; type: string } | null> {
    // v1.2 phase 7: POST /v1/nodes lets us create a memory entry over
    // HTTP without writing a file the watcher then has to pick up.
    // Returns the new node on success, null on failure (the caller
    // decides what to surface to the user).
    try {
      const r = await fetch(`${MnemoDaemon.configuredUrl()}/v1/nodes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          type: opts.type,
          name: opts.name,
          description: opts.description,
          body: opts.body,
          project_key: opts.project_key ?? null,
          base: opts.base ?? false,
        }),
      });
      if (!r.ok) return null;
      return (await r.json()) as never;
    } catch {
      return null;
    }
  }
}

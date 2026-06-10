# Integrations

Mounting mnemo into non-Claude agents and IDEs. Every integration here
speaks MCP natively, so the same `mnemo mcp` stdio server powers all of
them — no per-host adapter, no plugin, no bridge.

## 5-minute mounts (Phase 1 flagship picks)

- **[Cursor](./cursor.md)** — IDE-embedded host. Native MCP via
  `~/.cursor/mcp.json`. The most visible "mnemo works with X" story;
  pair this with the OpenAI Agents SDK mount below for the full
  provider-neutral demonstration.
- **[OpenAI Agents SDK](./openai-agents-sdk.md)** — agent-loop host.
  Python (`openai-agents`) + TypeScript (`@openai/agents`) snippets
  using `MCPServerStdio`. Demonstrates mnemo working cleanly inside
  OpenAI's flagship agent runtime.

## 5-minute mounts (v5.5.0 Reach)

The Phase 1 flagship picks were the visibility win; v5.5.0 promotes
the substrate framing by adding four more mount guides + smoke tests,
so the "same `mnemo mcp` works in every MCP-capable host" promise is
documented end to end:

- **[Claude Desktop](./claude-desktop.md)** — Anthropic's canonical
  first-party MCP host. Same `mcpServers` shape as Cursor; one
  config block + relaunch.
- **[Continue](./continue.md)** — open-source IDE assistant
  (VS Code + JetBrains). MCP via
  `experimental.modelContextProtocolServers` in `~/.continue/config.json`.
- **[Windsurf](./windsurf.md)** — Cascade panel speaks MCP via
  `mcp_config.json`. Same shape as Cursor.
- **[Zed](./zed.md)** — Rust-native editor; Assistant panel uses
  `context_servers` in `settings.json`. Tools are surfaced as
  `/mnemo-*` slash commands.

## 5-minute mount (v5.7.0 Reach)

- **[Gemini CLI](./gemini-cli.md)** — Google's CLI AI workflow
  tool; native MCP via `mcpServers` in `~/.gemini/settings.json`.
  Same shape as Cursor / Claude Desktop / Windsurf.

All seven (Cursor + OpenAI Agents SDK + Claude Desktop + Continue +
Windsurf + Zed + Gemini CLI) run the exact same `mnemo mcp` stdio
MCP server with the same 30-tool surface. Switching hosts is a
config change, not a code change.

## Why we picked the Phase 1 flagships — and what's still deferred

The picks live in [PICKS.md](./PICKS.md) with the full rubric
(MCP-native / active 2026 community / different host shape) and the
analysis tables for each candidate. Quick summary as of v5.7.0:

- **Pick A alternates (IDE-embedded), all landed:** Continue, Zed,
  Windsurf — shipped v5.5.0.
- **Pick B alternates (agent-loop), Gemini CLI now landed v5.7.0;
  LangGraph still deferred:**
  - **LangGraph** — large user base via LangChain, but MCP via
    `langchain-mcp-adapters` (adapter, not native). Defer until
    LangGraph ships first-class MCP.

The rubric exists so the next decision (if a pick falls through) has
a documented receipt to back it. Read PICKS.md before swapping a
flagship integration.

## What's the same across every host

Every mount runs `mnemo mcp` (stdio) and consumes mnemo's 30-tool
surface, locked by
[`test_mcp_tool_surface_contract.py`](../../daemon/tests/unit/test_mcp_tool_surface_contract.py):

- **12 `safe`** read tools (`mnemo_query`, `mnemo_get_node`,
  `mnemo_get_edges`, `mnemo_traverse`, `mnemo_search_by_type`,
  `mnemo_get_code_lines`, `mnemo_page_context`,
  `mnemo_session_nodes`, `mnemo_list_skills`, `mnemo_analyze`,
  `mnemo_audit_queue`, `mnemo_help`).
- **14 `confirm`** tools — recoverable mutations + UI directives
  + skill load + the confirm-then-apply auditor fix.
- **4 `danger`** tools — destructive: `mnemo_delete_node`,
  `mnemo_remove_source`, `mnemo_purge_conversation`,
  `mnemo_change_settings`.

The `risk` value is exposed both inside each tool's description string
(legacy fallback) and as a first-class structured `risk` field on the
wire schema, so any host's permission UI can gate on it without parsing
the description. MCP-only hosts (which get no commands or hooks) can call
**`mnemo_help`** to discover the surface + the "prefer `mnemo_query` over
grep" guidance.

## Adding a new host

Open a [GitHub issue](https://github.com/mmct-jsc/mnemo/issues)
describing:

- The host's MCP support (native? adapter? partial?).
- Its 2026 user base / activity (rough order of magnitude is fine).
- The host's "shape" — IDE-embedded, agent-loop, headless service,
  etc.

We accept MCP-native hosts with measurable users. Adapter-based hosts
are deferred until they ship native MCP — we don't take on the
maintenance burden of someone else's adapter inside our integration
docs.

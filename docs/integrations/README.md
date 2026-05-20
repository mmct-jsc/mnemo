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

Both run the exact same `mnemo mcp` stdio MCP server with the same
26-tool surface. Switching hosts is a config change, not a code change.

## Why these two — and what we deferred

The picks live in [PICKS.md](./PICKS.md) with the full rubric
(MCP-native / active 2026 community / different host shape) and the
analysis tables for each deferred candidate. Quick summary:

- **Pick A losers (IDE-embedded):**
  - **Continue** — native MCP, medium user base, open-source. Strong
    secondary; defer to a later Phase 1.x slot.
  - **Zed** — partial MCP, smaller user base, growing. Revisit when
    Zed hits broader adoption.
- **Pick B losers (agent-loop):**
  - **Gemini CLI** — native MCP, smaller user base. Right slot when
    Google for Startups outreach goes warm.
  - **LangGraph** — large user base via LangChain, but MCP via
    `langchain-mcp-adapters` (adapter, not native). Defer until
    LangGraph ships first-class MCP.

The rubric exists so the next decision (if a pick falls through) has
a documented receipt to back it. Read PICKS.md before swapping a
flagship integration.

## What's the same across every host

Every mount runs `mnemo mcp` (stdio) and consumes mnemo's 26-tool
surface, locked by
[`test_mcp_tool_surface_contract.py`](../../daemon/tests/unit/test_mcp_tool_surface_contract.py):

- **9 `safe`** read tools (`mnemo_query`, `mnemo_get_node`,
  `mnemo_get_edges`, `mnemo_traverse`, `mnemo_search_by_type`,
  `mnemo_get_code_lines`, `mnemo_page_context`,
  `mnemo_session_nodes`, `mnemo_list_skills`).
- **13 `confirm`** tools — recoverable mutations + UI directives
  + skill load.
- **4 `danger`** tools — destructive: `mnemo_delete_node`,
  `mnemo_remove_source`, `mnemo_purge_conversation`,
  `mnemo_change_settings`.

The `risk` value is currently surfaced inside each tool's description
string so any host's permission UI can parse and gate it. Phase 1.5 of
the substrate-hardening roadmap promotes `risk` to a first-class field
on the wire schema so hosts don't have to parse the description.

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

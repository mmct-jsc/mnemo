# Non-Claude integration picks (Phase 1)

> **Phase 1 deliverable** of the enterprise execution plan: pick the two
> non-Claude MCP hosts we ship "5-minute mount" guides + smoke tests for.
> The picks shape Tasks 1.2 and 1.3.

## Selection rubric

Three signals, weighted equally:

- **MCP-native** -- the host speaks MCP directly, not through a custom
  protocol bridge or a third-party adapter. Native = our `5-minute
  mount` promise survives contact with reality. Adapter = we own a
  layer we didn't sign up for.
- **Active 2026 community / measurable user base** -- somebody will
  actually mount us. A "growing" host with no users today is a
  speculative bet; a stable host with a real install base produces
  visible "mnemo works with X" wins.
- **Different host shape** -- one IDE-embedded (the user sees mnemo
  in their editor), one agent-loop (mnemo runs inside someone else's
  long-running agent). Covers both archetypes of MCP consumer.

## Pick A -- IDE-embedded

| Candidate | MCP support | User base (2026) | Strategic fit | Verdict |
|---|---|---|---|---|
| **Cursor** | Native first-class. `~/.cursor/mcp.json` (global) + `.cursor/mcp.json` (project-scoped). Auto-discovers tools; supports stdio + SSE; respects tool descriptions for the gating UI. | Very large -- dominant AI-IDE; millions of seats; the IDE Anthropic / OpenAI / Google all care about being in. | Highest visibility. "Cursor + mnemo" is the most legible "non-Claude agent uses mnemo" story we can ship in 2026. Provider-neutral by design (Cursor is multi-model). | **PICK A** |
| Continue | Native. `~/.continue/config.json` `experimental.modelContextProtocolServers` entry; VS Code + JetBrains hosts. Open source. | Medium and growing; strong open-source / self-host crowd; smaller than Cursor by ~10x but more aligned with the "local-first" tribe. | Good open-source ally; less visibility per integration than Cursor. | **LANDED v5.5.0** -- [continue.md](./continue.md) |
| Zed | Native via `context_servers` (added 2024); active dev. Stable but the editor itself is still building toward broad adoption. | Growing developer base; strong word-of-mouth among Rust / systems folks; smaller than Cursor / Continue. | Right tribe wrong scale for the "visible Pick A win"; right scale for the v5.5.0 substrate-reach push. | **LANDED v5.5.0** -- [zed.md](./zed.md) |
| Windsurf | Native via Cascade panel + `mcp_config.json`. | Growing AI-IDE in the post-Codeium-rebrand cohort; smaller than Cursor but real install base. | Same shape as Cursor; provider-neutral. | **LANDED v5.5.0** -- [windsurf.md](./windsurf.md) |
| Claude Desktop | Native first-party (Anthropic's canonical MCP host). `mcpServers` in `claude_desktop_config.json`. | Large -- ships with Claude.ai's desktop app. | First-party slot; obvious mount target once the substrate posture matters more than the "non-Claude visibility" goal. | **LANDED v5.5.0** -- [claude-desktop.md](./claude-desktop.md) |

**Why Cursor:** the substrate framing (Angle #1 of the strategy doc)
needs the SHIPPING "non-Claude users mount mnemo" demo to be the most
legible one possible. Cursor's install base + native MCP + multi-model
posture makes it the cleanest visible win per hour of integration
work. The "mnemo + Cursor" 5-minute mount also doubles as the
flagship asset for the sponsor-program application (Task 1.7).

## Pick B -- agent-loop

| Candidate | MCP support | User base (2026) | Strategic fit | Verdict |
|---|---|---|---|---|
| **OpenAI Agents SDK** | Native first-class. Both Python (`openai-agents`) and TypeScript (`@openai/agents`) SDKs ship an `MCPServerStdio` / `MCPServerSse` adapter that lets an Agent pull tools directly from any MCP server -- no custom shim. | Growing fast post Q1-2025 launch; the default new-build choice for agent teams who aren't already on LangChain; strong tracing + handoff primitives. | Strongest provider-neutrality signal we can ship -- mnemo as the agent-memory substrate INSIDE OpenAI's flagship agent SDK is the clearest "we are not just for Claude users" demo. Direct sponsor / partnership relevance. | **PICK B** |
| Gemini CLI | Native MCP via `mcpServers` in `~/.gemini/settings.json`; Google's recommended way to extend the CLI's tool surface. | Small relative to OpenAI Agents SDK and LangGraph; growing inside the Google ecosystem. | Right shape, smaller scale than the Phase 1 flagship Pick B; right slot for the v5.7.0 substrate-reach push so the Google ecosystem has a documented mnemo mount before Google for Startups outreach (Phase 1.7) goes warm. | **LANDED v5.7.0** -- [gemini-cli.md](./gemini-cli.md) |
| LangGraph | Via `langchain-mcp-adapters` -- a separate package, not first-class. Quality is improving but the adapter ages out of sync with LangGraph proper. | Large via the broader LangChain user base. Mixed reviews on production stability; many teams are migrating off LangChain in 2026. | The shim breaks the "5-minute mount" promise: we'd have to own the adapter's behavior in our docs + smoke test. Bigger user base but the integration story is bespoke. | Defer until LangGraph ships native MCP |

**Why OpenAI Agents SDK:** native MCP support means the 5-minute
mount stays five minutes -- the smoke test in Task 1.3 can wire
mnemo's stdio server into the SDK with one config block and zero
adapter layer. Strategic angle: the strategy doc's flywheel
explicitly leverages "A.I.-giant program/grant" applications;
showing mnemo working cleanly inside OpenAI's flagship agent SDK is
the strongest provider-neutral demonstration we can produce in
Phase 1's timeframe. LangGraph's user base is tempting on paper but
the adapter dependency and the 2026 LangChain churn make the
"works in 5 minutes, stays working" story harder to keep.

## Anti-goals reminder

The picks must not:

- Crippling the free local-first plugin (anti-goal #1). Both
  integrations are pure consumers of the existing MCP surface; no
  feature is added that the self-host user doesn't already have.
- Locking us to a specific A.I.-giant. The two picks deliberately
  span Cursor (multi-model IDE) + OpenAI Agents SDK (OpenAI-native
  agent host) so the substrate framing stays neutral.

## Decision

- **Pick A**: Cursor.
- **Pick B**: OpenAI Agents SDK.
- **Date**: 2026-05-20.
- **Author**: mnemo Phase 1 (this PR).

## What lands next (Tasks 1.2 + 1.3)

- `docs/integrations/cursor.md` -- 5-minute mount: `mcp.json` config
  block, `pip install mnemo`, the one-command verification query, a
  troubleshooting table. Smoke test: `daemon/tests/integration/
  test_mcp_mount_cursor.py` (config-block JSON validity + `python -m
  mnemo.mcp_server --stdio` handshake).
- `docs/integrations/openai-agents-sdk.md` -- 5-minute mount: agent
  config snippet (Python + TypeScript), the same one-command
  verification, troubleshooting. Smoke test: `daemon/tests/integration/
  test_mcp_mount_openai_agents_sdk.py`.

Both follow the TDD shape from the execution plan: write the failing
test first, then the doc + entry-point.

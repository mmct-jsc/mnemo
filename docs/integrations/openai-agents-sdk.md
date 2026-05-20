# Mounting mnemo into the OpenAI Agents SDK (5-minute setup)

The [OpenAI Agents SDK](https://github.com/openai/openai-agents-python)
is the agent-loop half of Phase 1's two flagship non-Claude
integrations (paired with the [Cursor mount](./cursor.md), the
IDE-embedded half). It ships first-class MCP support in both Python
and TypeScript, so mnemo plugs in as a single `MCPServerStdio`
binding — no shim, no adapter, no custom server registry.

If you only have five minutes, jump to the **Python** or
**TypeScript** snippet for your language and the **Verification**
step.

## Prerequisites

- **Python**: `openai-agents` >= 0.0.5 (`pip install openai-agents`).
- **TypeScript / JS**: `@openai/agents` >= 0.0.5
  (`npm install @openai/agents` or `pnpm add @openai/agents`).
- **An OpenAI API key** on your environment as `OPENAI_API_KEY`. The
  Agents SDK uses OpenAI's Chat Completions / Responses API for the
  model side; mnemo provides the *memory* side over MCP regardless
  of which model the agent talks to.
- **mnemo installed locally**, with `mnemo --version` printing
  `4.6.5` or later from any shell. From a fresh checkout:
  - Linux / macOS — `git clone https://github.com/mmct-jsc/mnemo.git
    && cd mnemo && ./install.sh`
  - Windows — `git clone https://github.com/mmct-jsc/mnemo.git;
    cd mnemo; .\install.ps1`
- **`mnemo daemon start`** running in the background (the MCP
  server itself runs in-process and is fine without the daemon,
  but the retrieval quality features depend on the live index).

## Python — wire mnemo as an `MCPServerStdio`

```python
"""mnemo + OpenAI Agents SDK -- five-minute mount.

Spawns mnemo's stdio MCP server as a subprocess, hands its tool
surface (mnemo_query, mnemo_get_node, mnemo_traverse, ...) to the
Agent runtime, and runs one query. Use as a starting point for any
larger agent that needs typed Graph-RAG memory."""

import asyncio

from agents import Agent, Runner
from agents.mcp import MCPServerStdio


async def main() -> None:
    # The 'command' + 'args' pair is the entire integration. mnemo
    # publishes 26 tools through this single stdio binding; the
    # Agent's tools list picks them all up via the standard MCP
    # tools/list handshake.
    async with MCPServerStdio(
        name="mnemo",
        params={"command": "mnemo", "args": ["mcp"]},
    ) as mnemo:
        agent = Agent(
            name="research",
            instructions=(
                "You can use mnemo_* tools to query a local Graph-RAG "
                "memory + code index. Cite every hit with its "
                "[mnemo:<node_id>] marker."
            ),
            mcp_servers=[mnemo],
        )
        result = await Runner.run(
            agent,
            input="Find anything about MQTT broker auth.",
        )
        print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())
```

If `mnemo` is not on the agent process's PATH, swap `"command":
"mnemo"` for an absolute path (typically `~/.local/bin/mnemo` after
`install.sh`, or whatever path `install.ps1` printed at the end of
the Windows install). See the Cursor mount guide's PATH table for
the platform defaults.

## TypeScript — same shape, JS surface

```ts
// mnemo + OpenAI Agents SDK (TypeScript) -- five-minute mount.
import { Agent, run } from '@openai/agents';
import { MCPServerStdio } from '@openai/agents';

async function main(): Promise<void> {
  const mnemo = new MCPServerStdio({
    name: 'mnemo',
    fullCommand: 'mnemo mcp',
    // Equivalent explicit form:
    //   command: 'mnemo',
    //   args: ['mcp'],
  });
  await mnemo.connect();

  try {
    const agent = new Agent({
      name: 'research',
      instructions:
        'You can use mnemo_* tools to query a local Graph-RAG memory + ' +
        'code index. Cite every hit with its [mnemo:<node_id>] marker.',
      mcpServers: [mnemo],
    });
    const result = await run(
      agent,
      'Find anything about MQTT broker auth.',
    );
    console.log(result.finalOutput);
  } finally {
    await mnemo.close();
  }
}

main().catch((err: unknown) => {
  console.error(err);
  process.exit(1);
});
```

## Verification

Run the snippet for your language. Expected behaviour:

1. The Agent SDK spawns `mnemo mcp` as a child process (you can
   confirm with `ps`/Task Manager — there should be a short-lived
   Python process under your agent).
2. The Agent calls `mnemo_query` with your prompt; the model sees
   the ranked hits each carrying a `[mnemo:<node_id>]` citation
   (token budget caps at 800 by default — raise via the tool's
   `max_tokens` argument).
3. The final output cites at least one `[mnemo:...]` marker. If it
   doesn't, your instructions to the agent likely didn't make
   citation mandatory; tighten them.

## Permission posture (optional)

mnemo's MCP server tags every tool with a `risk` field surfaced in
its description:

- **`safe`** — read-only (9 tools, including `mnemo_query`,
  `mnemo_get_node`, `mnemo_traverse`, `mnemo_search_by_type`,
  `mnemo_page_context`, `mnemo_session_nodes`,
  `mnemo_get_code_lines`, `mnemo_list_skills`).
- **`confirm`** — recoverable mutations or UI directives
  (13 tools).
- **`danger`** — destructive (4 tools: `mnemo_delete_node`,
  `mnemo_remove_source`, `mnemo_purge_conversation`,
  `mnemo_change_settings`).

The Agents SDK doesn't ship a built-in risk gate, but you can wrap
each tool call via the SDK's `tool_use_behavior` / approval-callback
mechanism and inspect the `risk: <value>` substring in the tool
description. Phase 1.5 of the substrate-hardening roadmap promotes
`risk` to a first-class field on the wire schema; until then,
description parsing is the right shape.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `FileNotFoundError: mnemo` (Python) or `ENOENT` (Node) | The Agent's process can't find `mnemo` on PATH | Use the absolute path in `command`. See Step 1 PATH table in the [Cursor guide](./cursor.md). |
| Snippet runs but `result.final_output` references no `[mnemo:...]` markers | The model didn't actually use the tools | Tighten the agent's `instructions` to require citation; rerun. |
| Every tool call returns `{"error": "..."}` | mnemo daemon not running OR no sources indexed | `mnemo daemon start && mnemo reindex`. |
| First call hangs ~30 s | First-run download of `sentence-transformers all-MiniLM-L6-v2` (~22 MB) | Run `mnemo reindex` once from the terminal before starting the agent. |
| TypeScript `MCPServerStdio` constructor rejects `fullCommand` | Older `@openai/agents` (pre 0.0.5) | Switch to the explicit `command: 'mnemo', args: ['mcp']` form, or upgrade the SDK. |

## What's next

- **Confirm provider-neutrality**: run the same agent script with
  `AGENTS_MODEL=anthropic/claude-opus-4-7` (if you have an
  Anthropic key + the corresponding adapter), then again with the
  default OpenAI model. The mnemo tools surface identically in both
  cases — that's the substrate story.
- **Pair with the [Cursor mount](./cursor.md)** for the
  IDE-embedded half of the integration matrix.
- **Open an issue** with what your setup needed:
  https://github.com/mmct-jsc/mnemo/issues

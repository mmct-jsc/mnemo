# Mounting mnemo into Windsurf (5-minute setup)

[Windsurf](https://windsurf.com) (formerly Codeium) is the IDE
that built its agentic Cascade panel on top of MCP from day one,
so mounting mnemo takes one config block and a reload — no plugin,
no bridge.

If you only have five minutes, jump to **Step 1** and **Step 3**.

## Prerequisites

- **Windsurf 1.0 or newer.** Cascade's MCP support is GA in
  modern builds; older Codeium-branded previews shipped a
  feature-flagged surface that doesn't match this guide.
- **mnemo installed locally**, from a fresh checkout:
  - Linux / macOS — `git clone https://github.com/mmct-jsc/mnemo.git
    && cd mnemo && ./install.sh`
  - Windows — `git clone https://github.com/mmct-jsc/mnemo.git;
    cd mnemo; .\install.ps1`
- `mnemo --version` prints `5.5.0` (or later) from any shell.
- `mnemo daemon start` running in the background. Most retrieval
  quality features depend on the daemon's running index; the MCP
  server itself runs in-process and is happy without it.

## Step 1 — Add mnemo to Windsurf's MCP config

Open the Cascade settings panel (the hammer / tools icon in the
chat composer) and click "Configure → Edit `mcp_config.json`", or
edit the file directly:

- macOS / Linux: `~/.codeium/windsurf/mcp_config.json`
- Windows: `%USERPROFILE%\.codeium\windsurf\mcp_config.json`

Paste this entry (merge with any existing servers):

```json
{
  "mcpServers": {
    "mnemo": {
      "command": "mnemo",
      "args": ["mcp"]
    }
  }
}
```

Then refresh Cascade: **Cmd/Ctrl+Shift+P → "Windsurf: Refresh MCP
Servers"** (or restart the IDE). Cascade's tool panel should now
show `mnemo` as a connected server with all 26 tools listed.

### If Windsurf can't find the `mnemo` command

Cascade inherits PATH from the Windsurf process, which on macOS
launches from the dock without your shell's PATH. If the MCP panel
shows mnemo as disconnected, switch to the absolute path:

```json
{
  "mcpServers": {
    "mnemo": {
      "command": "/Users/you/.local/bin/mnemo",
      "args": ["mcp"]
    }
  }
}
```

On Windows: `%USERPROFILE%\.local\bin\mnemo.exe`.

## Step 2 — Verify the connection

Open Cascade's chat panel and check the tool list (hammer icon).
You should see entries grouped under `mnemo`, including
`mnemo_query`, `mnemo_get_node`, `mnemo_traverse`, and the
rest of the 26-tool surface.

If the list is missing or shows mnemo as disconnected, open
**Windsurf → Help → Open Logs** — Cascade prints the stderr from
the failed MCP spawn there.

## Step 3 — Try a query

In any Cascade chat, ask:

> Use `mnemo_query` to surface anything we know about MQTT broker auth.

Cascade should call `mnemo_query`, ground its answer in your
typed memory + code graph, and surface `[mnemo:<node_id>]`
citations that link back to specific nodes in the local mnemo UI.

## What this gives you

- The full 26-tool surface from any Cascade chat.
- `[mnemo:<node_id>]` citations Cascade's host LLM treats as
  opaque provenance markers (clickable in the mnemo UI).
- Hybrid Graph-RAG retrieval over memory + code + commits.
- The v5 prompt-architect skill via `mnemo_run_skill`.

## Anti-goal: provider-neutral

The Windsurf mount is one front-door of many. See [PICKS.md](./PICKS.md)
for the full host matrix; the same `mnemo mcp` subcommand
serves Cursor, Claude Desktop, Continue, Zed, and the OpenAI
Agents SDK identically.

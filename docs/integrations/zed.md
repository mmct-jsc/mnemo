# Mounting mnemo into Zed (5-minute setup)

[Zed](https://zed.dev) is the high-performance Rust-native editor
whose Assistant panel speaks MCP via "context servers". Mounting
mnemo takes one block in `settings.json` and a window reload — no
plugin, no bridge.

If you only have five minutes, jump to **Step 1** and **Step 3**.

## Prerequisites

- **Zed 0.150 or newer.** MCP context-server support is GA in
  modern Zed; older builds shipped a preview surface that doesn't
  match this guide.
- **mnemo installed locally**, from a fresh checkout:
  - Linux / macOS — `git clone https://github.com/mmct-jsc/mnemo.git
    && cd mnemo && ./install.sh`
  - Windows — `git clone https://github.com/mmct-jsc/mnemo.git;
    cd mnemo; .\install.ps1`
- `mnemo --version` prints `5.5.0` (or later) from any shell.
- `mnemo daemon start` running in the background. Most retrieval
  quality features depend on the daemon's running index; the MCP
  server itself runs in-process and is happy without it.

## Step 1 — Add mnemo to Zed's `settings.json`

Open Zed's user settings: **Cmd/Ctrl+, → "Open Settings (JSON)"**,
or edit the file directly:

- macOS: `~/.config/zed/settings.json`
- Linux: `~/.config/zed/settings.json`
- Windows: `%APPDATA%\Zed\settings.json`

Add a `context_servers` entry (merge with any existing servers):

```json
{
  "context_servers": {
    "mnemo": {
      "command": {
        "path": "mnemo",
        "args": ["mcp"]
      }
    }
  }
}
```

Then reload Zed: **Cmd/Ctrl+Shift+P → "zed: reload"** (or
restart). The Assistant panel's context-server list should now
include `mnemo` with all 30 tools.

### If Zed can't find the `mnemo` command

Zed's PATH inherits from its launching process; on macOS, opening
Zed from Finder loses `~/.local/bin`. If the Assistant shows
mnemo as disconnected, switch to the absolute path:

```json
{
  "context_servers": {
    "mnemo": {
      "command": {
        "path": "/Users/you/.local/bin/mnemo",
        "args": ["mcp"]
      }
    }
  }
}
```

On Windows: `%USERPROFILE%\.local\bin\mnemo.exe`.

## Step 2 — Verify the connection

Open the Assistant panel (`Cmd/Ctrl+?`). Type `/` to see the
available slash commands — Zed registers MCP tools as slash
commands here. You should see entries beginning with `mnemo-`,
one per tool.

If the list is missing or Zed shows mnemo as disconnected, open
**Zed → Help → Open Logs** — the failed MCP spawn's stderr will
show there.

## Step 3 — Try a query

In any Assistant chat, ask:

> /mnemo-query what do we know about MQTT broker auth?

Zed should call mnemo's `mnemo_query` tool, ground its answer in
your typed memory + code graph, and surface `[mnemo:<node_id>]`
citations.

## What this gives you

- The full 30-tool surface from any Zed Assistant session.
- `[mnemo:<id>]` citations Zed's host LLM treats as opaque
  provenance markers (clickable in the mnemo UI).
- Hybrid Graph-RAG retrieval over memory + code + commits.
- The v5 prompt-architect skill via `mnemo_run_skill`.

## Anti-goal: provider-neutral

The Zed mount is one front-door of many. See [PICKS.md](./PICKS.md)
for the full host matrix; the same `mnemo mcp` subcommand
serves Cursor, Claude Desktop, Continue, Windsurf, and the
OpenAI Agents SDK identically.

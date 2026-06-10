# Mounting mnemo into Continue (5-minute setup)

[Continue](https://continue.dev) is the open-source IDE AI
assistant (VS Code + JetBrains). Continue speaks MCP natively
in 0.9.230 and later, so mounting mnemo takes one block in
`config.json` and a reload — no plugin, no bridge.

If you only have five minutes, jump to **Step 1** and **Step 3**.

## Prerequisites

- **Continue 0.9.230 or newer.** Older builds shipped a
  feature-flagged preview that doesn't match this guide.
- **mnemo installed locally**, from a fresh checkout:
  - Linux / macOS — `git clone https://github.com/mmct-jsc/mnemo.git
    && cd mnemo && ./install.sh`
  - Windows — `git clone https://github.com/mmct-jsc/mnemo.git;
    cd mnemo; .\install.ps1`
- `mnemo --version` prints `5.5.0` (or later) from any shell.
- `mnemo daemon start` running in the background. Most retrieval
  quality features depend on the daemon's running index; the MCP
  server itself runs in-process and is happy without it.

## Step 1 — Add mnemo to Continue's `config.json`

Open the Continue config file:

- macOS / Linux: `~/.continue/config.json`
- Windows: `%USERPROFILE%\.continue\config.json`

Add an `experimental.modelContextProtocolServers` entry (merge
with any existing servers):

```json
{
  "experimental": {
    "modelContextProtocolServers": [
      {
        "transport": {
          "type": "stdio",
          "command": "mnemo",
          "args": ["mcp"]
        }
      }
    ]
  }
}
```

Then reload Continue's view: **Cmd/Ctrl+Shift+P → "Continue:
Reload"** (or fully restart the IDE). The MCP tools panel
should now list `mnemo` as a connected server.

### If Continue can't find the `mnemo` command

Continue's PATH inherits from the IDE process, which on macOS
doesn't always include `~/.local/bin` when launched from
Finder / Dock. If the MCP panel shows mnemo as disconnected,
switch to the absolute path:

```json
{
  "experimental": {
    "modelContextProtocolServers": [
      {
        "transport": {
          "type": "stdio",
          "command": "/Users/you/.local/bin/mnemo",
          "args": ["mcp"]
        }
      }
    ]
  }
}
```

## Step 2 — Verify the connection

Open the Continue chat panel and check the "@" mention menu;
you should see mnemo's tools listed. Try `@mnemo_query` and
type a question.

If the list is missing, open Continue's logs via
**Cmd/Ctrl+Shift+P → "Continue: Open Console"** — the failed
MCP spawn's stderr will show there.

## Step 3 — Try a query

In any Continue chat, ask:

> @mnemo_query what do we know about MQTT broker auth?

Continue should call mnemo's `mnemo_query` tool, ground its
answer in your typed memory + code graph, and surface the
`[mnemo:<node_id>]` citations.

## What this gives you

- The full 30-tool surface from any Continue chat.
- `[mnemo:<id>]` citations Continue's host LLM treats as
  opaque provenance markers (clickable in the mnemo UI).
- Hybrid Graph-RAG retrieval over memory + code + commits.
- The v5 prompt-architect skill via `mnemo_run_skill`.

## Anti-goal: provider-neutral

The Continue mount is one front-door of many. See [PICKS.md](./PICKS.md)
for the full host matrix; the same `mnemo mcp` subcommand
serves Cursor, Claude Desktop, Windsurf, Zed, and the OpenAI
Agents SDK identically.

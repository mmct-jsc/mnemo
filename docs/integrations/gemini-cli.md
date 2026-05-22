# Mounting mnemo into Gemini CLI (5-minute setup)

[Gemini CLI](https://github.com/google-gemini/gemini-cli) is Google's
command-line AI workflow tool with native MCP support via
`mcpServers` in its `settings.json`. Mounting mnemo takes one config
block — no plugin, no bridge.

If you only have five minutes, jump to **Step 1** and **Step 3**.

## Prerequisites

- **Gemini CLI 0.36 or newer.** Older builds shipped a preview MCP
  surface that doesn't match this guide.
- **mnemo installed locally**, from a fresh checkout:
  - Linux / macOS — `git clone https://github.com/mmct-jsc/mnemo.git
    && cd mnemo && ./install.sh`
  - Windows — `git clone https://github.com/mmct-jsc/mnemo.git;
    cd mnemo; .\install.ps1`
- `mnemo --version` prints `5.7.0` (or later) from any shell.
- `mnemo daemon start` running in the background. Most retrieval
  quality features depend on the daemon's running index; the MCP
  server itself runs in-process and is happy without it.

## Step 1 — Add mnemo to Gemini CLI's `settings.json`

Open Gemini CLI's user settings file (workspace-scope
`.gemini/settings.json` also works if you want a per-project mount):

- macOS / Linux: `~/.gemini/settings.json`
- Windows: `%USERPROFILE%\.gemini\settings.json`

Add an `mcpServers` entry (merge with any existing servers):

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

Then restart Gemini CLI (`gemini` in a new shell). At startup it
discovers the new MCP server and registers mnemo's 26 tools.

### If Gemini CLI can't find the `mnemo` command

Gemini CLI inherits PATH from the shell that launched it; if your
shell rc doesn't add `~/.local/bin` (or the install script's bin
dir), the spawn fails. Switch to the absolute path:

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

On Windows: `%USERPROFILE%\.local\bin\mnemo.exe`, or the Python-
direct invocation if the editable install added a `mnemo.exe` shim:
`D:\path\to\daemon\.venv\Scripts\mnemo.exe`.

## Step 2 — Verify the connection

In a Gemini CLI session, run the built-in `/mcp` slash command —
it lists registered MCP servers and their tools. `mnemo` should
appear with 26 tools (`mnemo_query`, `mnemo_get_node`,
`mnemo_traverse`, etc.).

If the list shows mnemo as disconnected, Gemini CLI's startup log
prints the spawn's stderr; tail it from the same terminal session.

## Step 3 — Try a query

In any Gemini CLI conversation, ask:

> Use `mnemo_query` to find anything we know about MQTT broker auth.

Gemini should call mnemo's `mnemo_query` tool, ground its answer
in your typed memory + code graph, and surface `[mnemo:<node_id>]`
citations.

## Optional: per-server trust + timeout

Gemini CLI's `mcpServers` entry accepts the same advanced keys as
its native server entries — useful when you want to default-trust
mnemo's `safe` tools and skip the per-call confirmation:

```json
{
  "mcpServers": {
    "mnemo": {
      "command": "mnemo",
      "args": ["mcp"],
      "timeout": 30000,
      "trust": false
    }
  }
}
```

The risk-tagged tool surface (`safe` / `confirm` / `danger` in each
tool's description) still gates writes regardless of `trust`.

## What this gives you

- The full 26-tool surface from any Gemini CLI conversation.
- `[mnemo:<id>]` citations the Gemini model treats as opaque
  provenance markers (clickable in the mnemo UI).
- Hybrid Graph-RAG retrieval over memory + code + commits.
- The v5 prompt-architect skill via `mnemo_run_skill`.

## Anti-goal: provider-neutral

The Gemini CLI mount is one front-door of many. See
[PICKS.md](./PICKS.md) for the full host matrix; the same
`mnemo mcp` subcommand serves Cursor, Claude Desktop, Continue,
Windsurf, Zed, and the OpenAI Agents SDK identically.

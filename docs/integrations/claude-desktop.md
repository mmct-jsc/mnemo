# Mounting mnemo into Claude Desktop (5-minute setup)

[Claude Desktop](https://claude.ai/download) is Anthropic's
official desktop app and the canonical MCP host. Mounting mnemo
here is one config block and a restart — no plugin, no bridge,
no extra runtime.

If you only have five minutes, jump to **Step 1** and **Step 3**.

## Prerequisites

- **Claude Desktop 0.7 or newer.** MCP support is built in; older
  builds either shipped a preview or omit the surface entirely.
- **mnemo installed locally**, from a fresh checkout:
  - Linux / macOS — `git clone https://github.com/mmct-jsc/mnemo.git
    && cd mnemo && ./install.sh`
  - Windows — `git clone https://github.com/mmct-jsc/mnemo.git;
    cd mnemo; .\install.ps1`
- `mnemo --version` prints `5.5.0` (or later) from any shell.
- `mnemo daemon start` running in the background. Most retrieval
  quality features (hybrid Graph-RAG over the live index, feedback
  capture, auto-tune) depend on the daemon. The MCP server itself
  runs in-process and is happy without it, but you'll get thinner
  hits without the daemon's running index.

## Step 1 — Add mnemo to the Claude Desktop config

Locate the `claude_desktop_config.json` file:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

Create the file if it doesn't exist. Paste this entry (merge it
with existing servers if you already have one):

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

Then fully quit + relaunch Claude Desktop (the config is only
read at startup). On macOS that's **Cmd+Q**, not just closing
the window — the menu bar app keeps running otherwise and the
new config is never picked up.

### If Claude Desktop can't find the `mnemo` command

macOS apps launched from Finder don't always inherit your shell's
PATH (so `~/.local/bin` or `~/.cargo/bin` is missing). If the
MCP indicator shows mnemo as disconnected, switch to the
absolute path returned by `which mnemo`:

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

On Windows the same trick works with the path
`%USERPROFILE%\.local\bin\mnemo.exe`.

## Step 2 — Verify the connection

After relaunch, open any conversation and look for the
hammer / tool icon in the message composer. Click it; you
should see mnemo's 30 tools listed (`mnemo_query`,
`mnemo_get_node`, `mnemo_traverse`, etc.). The presence of
that list is the proof point.

If the list is missing, open
**Help → Show Logs** (macOS) or the equivalent on your
platform. Logs from the failed MCP spawn will show the
exact stderr from `mnemo mcp`.

## Step 3 — Try a query

In a new conversation, ask something like:

> What do we know about MQTT broker auth in petro_backend?

Claude should call `mnemo_query` (visible in the tool-use
log) and ground its answer in your typed memory + code
graph. If you've indexed code, the answer will include
`[mnemo:<node_id>]` citations linking back to specific
nodes.

The first call after a restart can take ~2-3 seconds while
the daemon warms its embedding model; subsequent calls are
sub-100ms.

## What this gives you

- The full 30-tool surface from any Claude Desktop session.
- Inline `[mnemo:<id>]` citations Claude follows back to
  specific memory / code nodes.
- The same hybrid Graph-RAG retrieval (memory + code +
  commit provenance) the local mnemo UI gives you.
- The v5 prompt-architect skill (`mnemo:prompt-architect`)
  available via `mnemo_run_skill` — paste the output into
  any other host that doesn't have mnemo mounted.

## Anti-goal: this surface is provider-neutral

The Claude Desktop mount is a parallel front-door, not the
canonical one. The same MCP surface mounts into Cursor / Continue
/ Windsurf / Zed / the OpenAI Agents SDK / any MCP-capable
host. Mnemo never gates features behind a specific provider; the
substrate is the value.

See [PICKS.md](./PICKS.md) for the full host matrix and
[wire-schema.md](./wire-schema.md) for the byte-stable
tool descriptors any host can pin against.

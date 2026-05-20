# Mounting mnemo into Cursor (5-minute setup)

[Cursor](https://cursor.com) is one of two flagship non-Claude
integrations Phase 1 of the mnemo substrate roadmap targets (the
other is the [OpenAI Agents SDK guide](./openai-agents-sdk.md)).
Cursor speaks MCP natively, so mounting mnemo takes one config block
and a window reload — no plugin, no bridge, no extra runtime.

If you only have five minutes, jump to **Step 1** and **Step 3**.

## Prerequisites

- **Cursor 0.45 or newer.** MCP support is native in modern Cursor;
  older builds shipped a feature-flagged prototype that doesn't
  match this guide.
- **mnemo installed locally**, from a fresh checkout:
  - Linux / macOS — `git clone https://github.com/mmct-jsc/mnemo.git
    && cd mnemo && ./install.sh`
  - Windows — `git clone https://github.com/mmct-jsc/mnemo.git;
    cd mnemo; .\install.ps1`
- `mnemo --version` prints `4.6.5` (or later) from any shell.
- `mnemo daemon start` running in the background. Most retrieval
  quality features (hybrid Graph-RAG over the live index, feedback
  capture, auto-tune) depend on the daemon. The MCP server itself
  runs in-process and is happy without it, but you'll get thinner
  hits without the daemon's running index.

## Step 1 — Add mnemo to Cursor's MCP config

Open Cursor Settings → MCP → "Edit `mcp.json`", or create the file
manually:

- macOS / Linux: `~/.cursor/mcp.json`
- Windows: `%USERPROFILE%\.cursor\mcp.json`

Paste this entry (merge it with existing servers if you already
have one):

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

Then reload Cursor's window: **Cmd+Shift+P → "Developer: Reload
Window"** (or restart Cursor). The MCP panel should now list
`mnemo` as a connected server.

### If Cursor can't find the `mnemo` command

Cursor's PATH on macOS doesn't always include `~/.local/bin` when
launched from Finder. If the MCP panel shows `mnemo` as
disconnected with a "command not found" error, switch to the
absolute path:

| Platform | Default install path |
|---|---|
| Linux | `~/.local/bin/mnemo` (or wherever `install.sh --bin-dir=...` was pointed) |
| macOS | `~/.local/bin/mnemo` |
| Windows | the path printed by `install.ps1` at the end of install (typically `%USERPROFILE%\.local\bin\mnemo.exe`) |

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

## Step 2 — Verify mnemo's tools appear

Open Cursor's chat panel. The "Available Tools" list (sometimes
collapsed under a tool-count badge near the model picker) should
include entries starting with `mnemo_`. Cursor groups them by MCP
server name, so you'll see them under `mnemo`.

mnemo publishes 26 tools out of the box (locked by the Phase 0
contract test, `daemon/tests/unit/test_mcp_tool_surface_contract.py`).
You don't need to enable them individually — Cursor picks them all
up from the MCP `tools/list` response.

## Step 3 — Try a real query

Open a Cursor chat. Ask:

> "Use `mnemo_query` to find anything about MQTT broker auth."

Expected: Cursor calls `mnemo_query` with that prompt; the model
sees ranked hits, each with a `[mnemo:<node_id>]` citation, capped
at 800 tokens of context by default. Override via the tool's
`max_tokens` parameter if you need a wider window.

If the call returns `{"error": "..."}` or the chat stalls, see
**Troubleshooting** below.

## Step 4 — (Optional) Tune the permission posture

Every mnemo tool carries a `risk` tag, surfaced inside its
description so Cursor's tool-approval UI shows it:

- **`safe`** — read-only, never prompts. 9 tools: `mnemo_query`,
  `mnemo_get_node`, `mnemo_get_edges`, `mnemo_traverse`,
  `mnemo_search_by_type`, `mnemo_get_code_lines`,
  `mnemo_page_context`, `mnemo_session_nodes`,
  `mnemo_list_skills`.
- **`confirm`** — recoverable mutations or UI directives. 13 tools
  including `mnemo_create_node`, `mnemo_update_node`,
  `mnemo_thumbs_feedback`, `mnemo_add_source`,
  `mnemo_navigate`, `mnemo_select_node`, `mnemo_highlight_nodes`,
  `mnemo_run_skill`, `mnemo_apply_retune`.
- **`danger`** — destructive, always prompts. 4 tools:
  `mnemo_delete_node`, `mnemo_remove_source`,
  `mnemo_purge_conversation`, `mnemo_change_settings`.

In Cursor's tool-approval panel you can default-deny the `confirm`
and `danger` rows for a read-only mnemo posture. (Phase 1.5 ships
structured `risk` fields alongside the descriptions so hosts can
gate by category without parsing the description string.)

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Cursor logs `MCP server not found` or `command not found` | `mnemo` not on Cursor's PATH | Use the absolute path in `command` (see Step 1's table). |
| `mnemo: command not found` in your terminal too | `~/.local/bin` not on shell PATH | Add `export PATH="$HOME/.local/bin:$PATH"` to your shell rc, restart shell + Cursor. |
| MCP panel shows `mnemo` connected but no tools | Older mnemo without the v3 tool surface | Upgrade to v4.6.5+: `git pull && ./install.sh`. |
| Tools appear but every call returns `{"error": "no nodes"}` or similar | No sources indexed yet | `mnemo daemon start && mnemo reindex` — wait for the first index to finish. |
| Cursor hangs on the first tool invocation for ~30 s | First-run model download (`sentence-transformers all-MiniLM-L6-v2`, ~22 MB) | Run `mnemo reindex` once from a terminal first; subsequent calls are fast. |
| Tools work, but retrieval quality is poor on a specific repo | Workspace not registered as a Scope B source | `mnemo add-source /path/to/repo` then `mnemo reindex --source /path/to/repo`. |

## What's next

- **Pair this with the [OpenAI Agents SDK mount](./openai-agents-sdk.md)**
  to confirm mnemo is genuinely provider-neutral end to end.
- **Restrict the surface** when Phase 1.5 lands first-class `risk`
  field exposure on MCP tool descriptors.
- **Open an issue** if your setup needs anything beyond what's
  here: https://github.com/mmct-jsc/mnemo/issues

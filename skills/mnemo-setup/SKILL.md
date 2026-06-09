---
name: mnemo-setup
description: Use when the user asks to "install mnemo", "set up mnemo", "make mnemo work here", "wire mnemo into this editor", or reports "no mnemo commands show up / mnemo isn't doing anything". Walks the full install chain for the current host -- engine (deps + PATH), MCP registration, the Claude Code /plugin commands, and a `mnemo doctor` verification -- and reports the result. This is the "have an AI install it for you" path.
---

# mnemo-setup -- install + register mnemo in the current host

**Type:** rigid (engine -> MCP -> plugin -> verify -> report).

mnemo has two halves that must BOTH be wired or it stays invisible:
1. the **engine** (the `mnemo` daemon + CLI: Python deps, the `mnemo` binary
   on PATH, the local index), and
2. the **host integration** (the MCP tool server + -- in Claude Code -- the
   plugin that surfaces `/mnemo-*` commands and the SessionStart /
   UserPromptSubmit / PostToolUse hooks).

Installing only one half is the #1 reason "no commands show up." Your job is
to wire both, then prove it with `mnemo doctor`. NEVER claim success on
"the files are there" -- only `mnemo doctor` all-green counts.

## Step 0 -- detect the host + locate the repo

- Determine the OS (Windows vs macOS/Linux) and the host (Claude Code, or an
  MCP-only host like Cursor / Continue / Windsurf / Zed / Claude Desktop /
  Gemini CLI).
- Find the mnemo repo root (the dir containing `install.sh`, `install.ps1`,
  and `.claude-plugin/`). If you cannot find it, ask the user to `git clone`
  it and tell you the path.

## Step 1 -- install the engine

From the repo root, run the installer for the OS:
- macOS / Linux: `./install.sh`
- Windows (PowerShell): `.\install.ps1`

This runs `uv sync` (daemon deps), drops a `mnemo` shim on PATH, and runs
`mnemo init` (registers the default memory sources). If PATH is warned about,
follow the printed instruction and re-open the shell so `mnemo` resolves.

Then build the index (first run downloads the ~22MB MiniLM embedder):
`mnemo reindex`

## Step 2 -- register the MCP tool server

This makes the `mnemo_*` tools available to the host's model.
- Claude Code: `claude mcp add mnemo -- mnemo mcp`
- Other hosts: follow the host's guide in `docs/integrations/` (each shows
  the exact JSON / command to point at `mnemo mcp` over stdio).

Note: the mnemo MCP server is registered SEPARATELY from the plugin (its
tools must keep the `mcp__mnemo__*` names; bundling would rename them).

## Step 3 -- register the plugin (Claude Code only)

The `/mnemo-*` commands + hooks come from the plugin. Plugin enablement is a
Claude-Code-internal action -- you (the agent) cannot type slash commands, so
INSTRUCT THE USER to run these two, in order, inside Claude Code:

```
/plugin marketplace add mmct-jsc/mnemo
/plugin install mnemo@mnemo
```

(For local development from a clone, the marketplace can be added by path:
`/plugin marketplace add <repo path>`.)

Then tell the user to **restart Claude Code**: slash commands appear
immediately, but HOOKS load only at session start, so the SessionStart memory
map + per-prompt auto-injection begin on the next session.

MCP-only hosts (Cursor, Continue, ...) have no plugin/commands/hooks surface
-- they get the passive `mnemo_*` tools only. Tell the user to call
`mnemo_query` instead of grep for "how/where/why" questions.

## Step 4 -- verify (the bar)

Run `mnemo doctor`. It prints a [ok]/[FAIL]/[?] checklist:
- `mnemo on PATH`, `memory index`, and `plugin registered (Claude Code)` are
  REQUIRED -- a [FAIL] there means the install is not done.
- `daemon` and `MCP server registered` are advisory (hooks + tools can work
  without the daemon; MCP detection is best-effort).

Apply the fix printed next to any [FAIL], then re-run `mnemo doctor` until the
required checks pass.

## Step 5 -- report

Summarize for the user: what was installed, the `mnemo doctor` result, and the
ONE remaining manual action if any (usually "run the two /plugin commands and
restart Claude Code"). If a required check is still [FAIL], say so plainly --
do not report success.

## Anti-goals

- NEVER report "installed" without a green `mnemo doctor`.
- NEVER try to type `/plugin ...` yourself -- it is a user action; hand the
  exact commands to the user.
- NEVER bundle the MCP server into the plugin or rename the `mcp__mnemo__*`
  tools.
- Do not bind the daemon to anything but `127.0.0.1`.

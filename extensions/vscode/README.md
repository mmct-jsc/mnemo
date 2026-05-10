# mnemo for VS Code

Surfaces your local [mnemo](https://github.com/mmct-jsc/mnemo) memory
in VS Code: a `@mnemo` chat participant for Copilot Chat / Cody, a
sidebar with active-project + pinned nodes, palette commands, and a
status-bar pill showing daemon health.

**Local-first.** All retrieval runs against your local mnemo daemon
on `127.0.0.1:7373`. Nothing leaves your machine.

## Install

### From a `.vsix` (v1.1.0)

Download `mnemo-vscode-1.1.0.vsix` from the
[GitHub release](https://github.com/mmct-jsc/mnemo/releases) and:

```
code --install-extension mnemo-vscode-1.1.0.vsix
```

(Marketplace publish is deferred to v1.2.)

### Build from source

```
cd extensions/vscode
npm install
npm run compile
npm run package        # produces mnemo-vscode-<ver>.vsix
code --install-extension mnemo-vscode-<ver>.vsix
```

## Prerequisites

- A running mnemo daemon: `mnemo daemon start` (see the
  [main repo](https://github.com/mmct-jsc/mnemo)).
- VS Code 1.90+ for the chat-participant API.

## Surfaces

### Status bar (bottom right)

Shows daemon health + active project. Click to open the mnemo UI.

### Palette commands

- `mnemo: Query` — input box (selected text pre-fills); opens
  results in a markdown side panel with citations.
- `mnemo: Add Note` — opens the dashboard's Nodes page in your
  browser. (HTTP POST flow lands in v1.2.)
- `mnemo: Set Active Project` — workspace folder by default; sets
  the daemon's active-project state so retrieval scopes correctly.
- `mnemo: Open UI` — opens the dashboard.
- `mnemo: Reindex` — runs `POST /v1/reindex` and shows the report.

### Sidebar

`mnemo` activity-bar icon -> active project + pinned nodes tree.
Right-click a node anywhere in mnemo to pin it (v1.2 hook).

### `@mnemo` chat participant

In any chat-participant-aware extension (Copilot Chat, Cody):

```
@mnemo what's our MQTT auth pattern?
@mnemo /recall recent debug sessions
@mnemo /sources
```

Hits stream as references with `[mnemo:<id>]` citations the model
can quote back.

## Settings

| Key | Default |
|---|---|
| `mnemo.daemonUrl` | `http://127.0.0.1:7373` |
| `mnemo.budgetTokens` | `800` |
| `mnemo.k` | `5` |
| `mnemo.autoActivate` | `true` (set workspace folder as active project on activate) |

## Failure mode

If the daemon is down, the status bar shows `mnemo · down`, palette
commands fail with a friendly message, and the chat participant
prints a "daemon unreachable" line. The extension never breaks the
editor or other chat extensions when mnemo is unavailable.

## License

MIT.

# mnemo

[![CI](https://github.com/mmct-jsc/mnemo/actions/workflows/ci.yml/badge.svg)](https://github.com/mmct-jsc/mnemo/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](CHANGELOG.md)
[![Tests](https://img.shields.io/badge/tests-273_passing-brightgreen.svg)](daemon/tests/)
[![ruff](https://img.shields.io/badge/lint-ruff-orange.svg)](https://github.com/astral-sh/ruff)

Local-first knowledge memory system for Claude Code. Aggregates your Claude memory and project knowledge into a typed graph, retrieves it on demand, and ships it back as token-budgeted context.

```
[Claude Code plugin]   markdown skills + hooks + slash commands
        |
        v   localhost HTTP
[mnemo daemon]         Python + FastAPI on 127.0.0.1:7373
        |
        v
[mnemo store]          SQLite + sqlite-vec  (~/.claude/mnemo/)
```

## Why

Claude memory is scattered: `~/.claude/projects/<project>/memory/*.md`, per-repo `CLAUDE.md`, design docs under `docs/plans/`, the global `~/.claude/CLAUDE.md`. As you work across projects, the same lessons get re-discovered, the same feedback gets re-given, and the same designs get re-derived. mnemo fixes that.

When you start a session, mnemo injects a tiny "memory map" so Claude knows what's available. When you submit a prompt, mnemo runs a hybrid Graph-RAG search and injects ≤ 800 tokens of the most relevant memory — typed, cited, budget-capped. When Claude edits a memory file, mnemo re-embeds it in the background.

## Features

- **Hybrid Graph-RAG retrieval** — vector similarity + typed-edge graph traversal. Better than pure RAG for "what should I do here?" queries.
- **Local-first** — SQLite + sqlite-vec, sentence-transformers MiniLM. No cloud, no API keys, no network.
- **Token-budgeted** — never dumps full files; ranks, compresses, cites.
- **Auto-update** — file watcher reindexes on every memory edit.
- **Visual UI** — graph view, search, edit at `http://localhost:7373`.
- **Seven workflow skills** — implement-platform, debug, refactor, add-knowledge, query-knowledge, onboard-project, review.
- **Claude Code plugin** — installs as a standard plugin with hooks + slash commands.

## Quick start

Requires Python 3.11+ and [uv](https://github.com/astral-sh/uv).

```bash
# Install
git clone https://github.com/mmct-jsc/mnemo.git
cd mnemo
./install.sh    # or .\install.ps1 on Windows

# First index of your existing memory
mnemo reindex

# Open the UI
mnemo ui
```

The install script handles three things in one go:

1. Syncs the daemon dependencies via `uv sync`.
2. Drops a `mnemo` shim onto your PATH (`~/.local/bin/mnemo`).
3. Links the plugin into `~/.claude/plugins/mnemo/` so Claude Code picks up the hooks, slash commands, and skills.

Restart Claude Code after install; the next session will see mnemo's hooks fire automatically.

## Slash commands

- `/mnemo-query <text>` — ad-hoc memory query
- `/mnemo-add` — capture an insight as a new memory node
- `/mnemo-reindex` — full rescan of all sources
- `/mnemo-ui` — open the web UI
- `/mnemo-status` — daemon health, node counts
- `/mnemo-hooks <on|off>` — toggle automatic injection

## Workflow skills

mnemo ships seven systematic workflows that pull project-specific memory at every step.

| Skill | Phases |
|---|---|
| `mnemo:implement-platform` | requirements → analysis → design → decision → planning → specs → implementation → verification → docs |
| `mnemo:debug` | reproduce → hypothesize → instrument → bisect → fix → verify → RCA |
| `mnemo:refactor` | measure → propose → atomic commits → verify behavior + perf |
| `mnemo:add-knowledge` | novelty check → categorize → write → graph-link → reindex |
| `mnemo:query-knowledge` | intent classify → hybrid retrieve → budget-compress → cite |
| `mnemo:onboard-project` | scan → extract conventions → build initial nodes → user-confirm |
| `mnemo:review` | pull project review memory → checklist → review → capture lessons |

## Architecture

- [docs/architecture.md](docs/architecture.md) — short tour of the three tiers, data model, retrieval algorithm, and what we deliberately don't do.
- [docs/plans/2026-05-09-mnemo-design.md](docs/plans/2026-05-09-mnemo-design.md) — full design with rationale.
- [docs/workflows/index.md](docs/workflows/index.md) — guided summaries of the seven workflow skills.
- [docs/examples/sample-queries.md](docs/examples/sample-queries.md) — real queries against a real `~/.claude/` memory, with the actual scored hits.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). All commits MUST NOT include `Co-Authored-By` trailers.

## License

MIT — see [LICENSE](LICENSE).

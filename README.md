# mnemo

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
git clone https://github.com/<your-org>/mnemo.git
cd mnemo
./install.sh    # or .\install.ps1 on Windows

# First index of your existing memory
mnemo reindex

# Open the UI
mnemo ui
```

The plugin auto-registers in `~/.claude/plugins/`. Restart Claude Code; the next session will see mnemo's hooks fire automatically.

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

See [docs/plans/2026-05-09-mnemo-design.md](docs/plans/2026-05-09-mnemo-design.md) for the full design.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). All commits MUST NOT include `Co-Authored-By` trailers.

## License

MIT — see [LICENSE](LICENSE).

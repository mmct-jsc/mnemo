# mnemo — repo instructions for Claude

## What this is

`mnemo` is a local-first knowledge memory system for Claude Code, distributed as a Claude Code plugin. It aggregates Claude memory + project knowledge, exposes hybrid Graph-RAG retrieval over a typed graph, and ships ≤ 800 tokens of relevant memory back to Claude via hooks.

Full design: [docs/plans/2026-05-09-mnemo-design.md](docs/plans/2026-05-09-mnemo-design.md).

## Hard rules (do not violate)

- **No `Co-Authored-By` trailers on commits.** Ever. Drop the line entirely from commit messages. This applies to every commit on every branch in this repo.
- **No emojis** in code, docs, or commit messages unless the user explicitly asks for them.
- Commit messages use conventional prefixes: `feat:` / `fix:` / `chore:` / `docs:` / `test:` / `refactor:`.
- Use HEREDOC for multi-line commit messages.

## Stack

- **Python 3.11+** for the daemon.
- **uv** for dependency management (single lock file, ~10x faster than pip).
- **SQLite + sqlite-vec** for storage and embeddings.
- **sentence-transformers `all-MiniLM-L6-v2`** for embeddings (local, 22 MB, 384-dim).
- **FastAPI + Jinja2 + HTMX + Alpine.js + Cytoscape.js** for the UI. No Node toolchain.
- **pytest** for tests.
- **ruff** for lint + format.

## Repo layout

```
mnemo/
  .claude-plugin/plugin.json     plugin manifest
  skills/                        markdown workflow skills
  commands/                      slash command markdown
  hooks/                         bash + powershell hook scripts
  daemon/                        Python service (cli, server, store, ingest, embed, retrieve, graph, ui)
  install.sh / install.ps1
  docs/{architecture.md, plans/, workflows/}
```

## Conventions

- Never dump full memory files into Claude's context. The whole point is **token-budgeted retrieval** (≤ 800 tokens default).
- All retrieval results MUST include `[mnemo:<node_id>]` citations so Claude can cite back.
- Memory entries follow the existing format: YAML frontmatter (`name`, `description`, `type`) + markdown body. Don't invent new schemas.
- Every workflow phase writes an audit artifact under `docs/plans/YYYY-MM-DD-<topic>-<phase>.md`.
- Tests live under `daemon/tests/{unit,integration}/test_*.py`.
- The daemon listens on `127.0.0.1:7373` only — never bind `0.0.0.0`.

## Running

```bash
# Install (one-time)
./install.sh        # Linux/macOS
.\install.ps1       # Windows

# Daemon
mnemo daemon start
mnemo daemon stop
mnemo daemon status

# Index
mnemo reindex
mnemo reindex --source ~/.claude/projects/D--Repository-aibox-prod-all/memory/

# Query (CLI)
mnemo query "how do we handle MQTT broker auth"

# UI
mnemo ui      # opens http://127.0.0.1:7373
```

## Tests

```bash
uv run pytest                               # full suite
uv run pytest daemon/tests/unit             # fast
uv run pytest -k retrieval                  # filter
uv run ruff check .                         # lint
uv run ruff format .                        # format
```

## Phased commits (in order)

See [docs/plans/2026-05-09-mnemo-design.md §11](docs/plans/2026-05-09-mnemo-design.md). Each phase is one commit (sometimes more if cleanly separable). Don't skip phases; don't reorder.

## Memory data sources (Scope B)

mnemo indexes:
1. `~/.claude/projects/*/memory/*.md` — typed memory entries
2. `~/.claude/CLAUDE.md` — global memory
3. Repo-root `CLAUDE.md` files (configured via `mnemo add-source <path>`)
4. `docs/plans/*.md` design docs in tracked repos

Out of scope by default: session transcripts, commit messages (opt-in via Scope C if user asks).

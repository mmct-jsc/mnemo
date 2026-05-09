# Contributing to mnemo

Thanks for considering a contribution. mnemo is small, opinionated, and
local-first; the goal here is to keep it that way while improving
retrieval quality, polish, and platform support.

## Hard rules

- **No `Co-Authored-By` trailers on commits.** Drop the entire trailer line
  from every commit message. This applies to every branch in this repo.
- **No emojis** in code, docs, or commit messages unless explicitly asked.
- Commit messages use conventional prefixes: `feat:` / `fix:` / `chore:` /
  `docs:` / `test:` / `refactor:` / `perf:`.
- Commits use HEREDOC for multi-line messages (no `git commit -m "line1\nline2"`).
- mnemo binds to `127.0.0.1` only. Never `0.0.0.0`.

If a tool tries to inject a co-author trailer, strip it before pushing.
PRs that include the trailer will be asked to amend.

## Setup

Requires Python 3.11+ and [uv](https://github.com/astral-sh/uv).

```bash
git clone <repo> mnemo
cd mnemo
./install.sh          # Linux / macOS
.\install.ps1         # Windows
```

The install script creates the venv, installs deps, drops a `mnemo` shim
on PATH, and links the plugin into `~/.claude/plugins/mnemo/`. It's
idempotent.

For a development-only install (no PATH/plugin link):

```bash
cd daemon
uv sync --extra dev
```

## Tests

```bash
cd daemon
uv run pytest                      # full suite
uv run pytest tests/unit -q        # fast, no model load
uv run pytest -k retrieve          # filter by keyword
uv run pytest tests/integration    # slower, exercises real fs + model
```

The end-to-end smoke (`tests/integration/test_smoke_full.py`) runs against
the user's actual `~/.claude/` memory and is auto-skipped on machines
without it. To exercise it on CI, seed memory under a temp `CLAUDE_HOME`
before running.

## Lint and format

```bash
cd daemon
uv run ruff check .
uv run ruff format .
```

CI is expected to fail on lint or format violations. Run both before
pushing.

## Code layout

```
mnemo/                            (repo root)
├── .claude-plugin/plugin.json    plugin manifest
├── skills/                       7 workflow skills (markdown)
├── commands/                     7 slash commands (markdown)
├── hooks/                        3 events x 2 platforms (bash + ps1)
├── daemon/                       Python service
│   ├── pyproject.toml
│   ├── mnemo/
│   │   ├── store.py              SQLite + sqlite-vec
│   │   ├── ingest.py             scan, parse, reindex
│   │   ├── watcher.py            async fs watcher
│   │   ├── embed.py              MiniLM + chunking
│   │   ├── intent.py             prompt -> intent tags
│   │   ├── graph.py              edge inference + proximity
│   │   ├── compress.py           token-budget compression
│   │   ├── retrieve.py           orchestrator (5-term scoring)
│   │   ├── api_schemas.py        Pydantic models
│   │   ├── server.py             FastAPI app
│   │   ├── cli.py                Typer CLI
│   │   ├── daemon.py             PID-file lifecycle
│   │   ├── paths.py              runtime paths
│   │   └── ui/                   Jinja2 + HTMX + Cytoscape
│   └── tests/
│       ├── unit/
│       └── integration/
├── docs/
│   ├── architecture.md
│   ├── plans/                    design docs (one per phase)
│   ├── workflows/                user-facing workflow summaries
│   └── examples/                 worked examples with real output
├── install.sh
├── install.ps1
├── CLAUDE.md                     repo-local instructions for Claude
└── README.md
```

## How to land a change

1. **Open an issue first** if the change is non-trivial. mnemo is opinionated;
   it's faster to align on shape before implementing.
2. **Branch from `main`** and keep PRs focused. One concept per PR.
3. **Write tests for the new behavior.** New surface area without a test
   will be asked to add one.
4. **Run `ruff check .`, `ruff format .`, and `pytest`** before pushing.
5. **Commit messages**: conventional prefix, imperative mood, focused on
   the *why* in the body. No co-author trailer.
6. **PR description** should answer: what changed, why, what was tested.

## Where to start

Good first contributions:

- Quality: fix a flaky test, tighten a regex in `intent.py`, expand the
  workflow skills with examples.
- Polish: improve the UI's dark theme, add keyboard shortcuts, fix Cytoscape
  layout for big graphs.
- Coverage: write tests for an edge case that isn't covered.
- Docs: clarify a doc; add an example to `docs/examples/`.

Avoid (without prior discussion):

- New runtime dependencies. The local-first promise depends on a small dep
  set.
- New scoring weights or wholesale algorithm changes. Tune via the audit
  log + benchmarks first.
- Cloud sync, multi-user, account systems. Out of scope.

## Releasing

Maintainers only. Follow [SemVer](https://semver.org/):

- `0.x.y` - pre-1.0, breaking changes allowed in `x` bumps.
- `1.0.0` - first stable release; breaking changes need majors after that.

Tag with `v<version>`, push the tag, GitHub Action handles the release.

## License

MIT. See [LICENSE](LICENSE).

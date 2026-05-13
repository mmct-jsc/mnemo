# mnemo

[![CI](https://github.com/mmct-jsc/mnemo/actions/workflows/ci.yml/badge.svg)](https://github.com/mmct-jsc/mnemo/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-2.1.0-blue.svg)](CHANGELOG.md)
[![Tests](https://img.shields.io/badge/tests-605_passing-brightgreen.svg)](daemon/tests/)
[![ruff](https://img.shields.io/badge/lint-ruff-orange.svg)](https://github.com/astral-sh/ruff)

Local-first knowledge memory **+ code intelligence** for Claude Code. Aggregates your Claude memory, project knowledge, **and source code** into a single typed graph; retrieves it via hybrid Graph-RAG on every prompt; ships it back as token-budgeted, cited context.

```
[Claude Code plugin]   skills + hooks + slash commands
        |
        v   localhost HTTP
[mnemo daemon]         Python + FastAPI on 127.0.0.1:7373
        |
        +-- typed graph: memory_* nodes + code_* nodes + commit nodes
        +-- 9 edge relations: applies_to / calls / routes_to / at_endpoint / ...
        +-- per-edge confidence (0.0..1.0) for inferred inferences
        |
        v
[mnemo store]          SQLite + sqlite-vec  (~/.claude/mnemo/)
```

## Why

Claude memory is scattered: `~/.claude/projects/<project>/memory/*.md`, per-repo `CLAUDE.md`, design docs under `docs/plans/`, the global `~/.claude/CLAUDE.md`. As you work across projects, the same lessons get re-discovered, the same feedback gets re-given, the same designs get re-derived.

**v2.0 adds the missing half**: your *code* lives next to your memory in the same typed graph. Now "where is this function called from?" + "why was this commit made?" + "what feedback applies here?" are all one query.

When you start a session, mnemo injects a memory map. When you submit a prompt, hybrid Graph-RAG injects ≤ 800 tokens of relevant typed memory + code structure. When Claude edits a file, mnemo re-embeds in the background.

## What's new in v2.0 + v2.1

### v2.0 — Code Intelligence

- **Typed code graph** — every `code_repo` source becomes `code_module` + `code_function` + `code_class` + `code_method` + `code_route` + `code_component` + `code_endpoint` nodes with edges for `imports` / `defines` / `method_of` / `calls` / `routes_to` / `at_endpoint`.
- **Tier 1 ingestion** — tree-sitter parses 16 languages; Python gets full structural extraction (functions, classes, methods, decorated definitions, docstrings → descriptions).
- **Tier 2 Python call-graph resolver** — Stack-Graphs-inspired scope resolution with constructor + `self`/`this` lookups; same-file edges at 0.95 confidence, cross-file via imports at 0.8.
- **Tier 3 framework extractors** — FastAPI, Flask, Express (backend); React (frontend); each emits `code_route` + `code_component` + a shared `code_endpoint` URI anchor so cross-stack sitemap queries work.
- **Source auto-router** — `mnemo source add <path>` classifies the kind (`code_repo` / `memory_dir` / `docs_dir`), shows a dry-run preview, and refuses to scan paths over 50,000 source files without `--force`.
- **`/code` UI family** — landing, project overview with file-grouped declarations, function detail with 2-hop ego-network, cross-stack sitemap table.
- **7 new code skills** — `mnemo:explore-codebase`, `mnemo:trace-call`, `mnemo:trace-route`, `mnemo:explain-design`, `mnemo:debug-with-code`, `mnemo:why-is-this-here`, `mnemo:impact-analysis`.

### v2.1 — Nebula UI

- **Three-panel resizable shell at `/graph`** — file tree (VSCode-style icons) | force-directed canvas | node detail panel with Prism syntax highlighting. Drag the gutters to resize; widths persist to localStorage.
- **Vibrant Nebula palette** — saturated neon-on-velvet, glow halos around every node (per-type color), starfield + bloom backdrop, marching-ants animation on highlighted edges, pulse on the selected node.
- **Silky transitions everywhere** — cubic-bezier 200–300ms easing on chips, gutters, tree, detail panel, slider, layout buttons. Layout switches cross-fade (140ms out → snap → 220ms in + camera tween) without per-node tweening.
- **Smart filter bar** — text search, per-type chip toggles, confidence slider with tick marks (0/.5/.7/.9/1), hop selector, layout buttons (force / rings / tree / grid). Force layout snapshots its initial positions so round-tripping through other layouts always returns to the same shape.
- **Three deselect paths** — click canvas, Escape key, "×" close button in detail panel.

### v1.2 — Learning to Listen (carried forward)

- **Feedback events** — thumb up/down on each retrieval hit; auto-detected re-queries within a 5-minute window flag missed-hit candidates.
- **MMR re-rank** — top-K hits diversified to reduce paraphrased duplicates.
- **Auto-tuner** — coordinate-descent over the 6-term scoring weights, MRR objective, threshold-gated (default 30 labeled queries).
- **`POST /v1/nodes`** — direct node creation for the VS Code extension's "save as note" command.

## Quick start

Requires Python 3.11+ and [uv](https://github.com/astral-sh/uv).

```bash
# Install
git clone https://github.com/mmct-jsc/mnemo.git
cd mnemo
./install.sh    # or .\install.ps1 on Windows

# First index of your existing memory
mnemo reindex

# Add a code repo (auto-router proposes the kind)
mnemo source add ~/code/my-project   # picks code_repo when it sees .git/ + source files

# Open the UI
mnemo ui
```

The install script:

1. Syncs the daemon dependencies via `uv sync` (incl. tree-sitter + 8 bundled grammars).
2. Drops a `mnemo` shim onto your PATH (`~/.local/bin/mnemo`).
3. Links the plugin into `~/.claude/plugins/mnemo/` so Claude Code picks up the hooks, slash commands, and skills.

Restart Claude Code after install; the next session sees mnemo's hooks fire automatically.

## Slash commands

- `/mnemo-query <text>` — ad-hoc memory query
- `/mnemo-add` — capture an insight as a new memory node
- `/mnemo-reindex` — full rescan of all sources
- `/mnemo-ui` — open the web UI
- `/mnemo-status` — daemon health, node counts
- `/mnemo-hooks <on|off>` — toggle automatic injection

## Skills

### v1 workflow skills (7)

| Skill | Phases |
|---|---|
| `mnemo:implement-platform` | requirements → analysis → design → decision → planning → specs → implementation → verification → docs |
| `mnemo:debug` | reproduce → hypothesize → instrument → bisect → fix → verify → RCA |
| `mnemo:refactor` | measure → propose → atomic commits → verify behavior + perf |
| `mnemo:add-knowledge` | novelty check → categorize → write → graph-link → reindex |
| `mnemo:query-knowledge` | intent classify → hybrid retrieve → budget-compress → cite |
| `mnemo:onboard-project` | scan → extract conventions → build initial nodes → user-confirm |
| `mnemo:review` | pull project review memory → checklist → review → capture lessons |

### v2 code skills (7)

| Skill | What it does |
|---|---|
| `mnemo:explore-codebase` | shape-of-the-codebase tour: top modules, central functions, route surface, lessons |
| `mnemo:trace-call` | walks Tier 2 `calls` edges from a target function; surfaces callers + callees with line ranges |
| `mnemo:trace-route` | the cross-stack sitemap walker: Component → Endpoint → Route → Handler → Service |
| `mnemo:explain-design` | synthesizes a design narrative from plan_doc + memory_project nodes |
| `mnemo:debug-with-code` | adds a phase-3 code-graph overlay to `mnemo:debug` (stack-trace → hypothesis chain) |
| `mnemo:why-is-this-here` | decision provenance: code → commits that touched it → memory_feedback that motivated them |
| `mnemo:impact-analysis` | blast radius via reverse `calls` + `routes_to` + `at_endpoint`, with `memory_feedback` warnings overlaid |

## Architecture

- [docs/architecture.md](docs/architecture.md) — three tiers, data model, retrieval algorithm, and what we deliberately don't do.
- [docs/plans/2026-05-09-mnemo-design.md](docs/plans/2026-05-09-mnemo-design.md) — full v1 design.
- [docs/plans/2026-05-11-mnemo-v2.0-design.md](docs/plans/2026-05-11-mnemo-v2.0-design.md) — full v2.0 (Code Intelligence) design.
- [docs/workflows/index.md](docs/workflows/index.md) — guided summaries of the workflow skills.
- [CHANGELOG.md](CHANGELOG.md) — version-by-version diff.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

Hard rules:

- **No `Co-Authored-By` trailers** on commits. Ever.
- **No emojis** in code, docs, or commit messages unless explicitly requested.
- Conventional commit prefixes (`feat:` / `fix:` / `chore:` / `docs:` / `test:` / `refactor:` / `perf:`).
- HEREDOC for multi-line commit messages.
- Daemon binds to `127.0.0.1` only; never `0.0.0.0`.

## License

MIT — see [LICENSE](LICENSE).

# mnemo

[![CI](https://github.com/mmct-jsc/mnemo/actions/workflows/ci.yml/badge.svg)](https://github.com/mmct-jsc/mnemo/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-4.6.2-blue.svg)](CHANGELOG.md)
[![Tests](https://img.shields.io/badge/tests-1245_passing-brightgreen.svg)](daemon/tests/)
[![ruff](https://img.shields.io/badge/lint-ruff-orange.svg)](https://github.com/astral-sh/ruff)
[![Live demo](https://img.shields.io/badge/live-demo-7ee7e0.svg)](https://mmct-jsc.github.io/mnemo/)

**▶ [Live interactive demo](https://mmct-jsc.github.io/mnemo/)** — the real Nebula galaxy renderer on a seeded synthetic graph of mnemo's own architecture (static, no backend).

Local-first knowledge memory **+ code intelligence + an agentic companion** for Claude Code. Aggregates your Claude memory, project knowledge, **and source code** into a single typed graph; retrieves it via hybrid Graph-RAG on every prompt; ships it back as token-budgeted, cited context. Chat with it through a tool-using agent ("Mnem"), and explore the whole graph as a custom WebGL galaxy at `/graph`.

```
[Claude Code plugin]   skills + hooks + slash commands
        |
        v   localhost HTTP
[mnemo daemon]         Python + FastAPI on 127.0.0.1:7373
        |
        +-- typed graph: memory_* nodes + code_* nodes + commit nodes
        +-- typed edges: applies_to / calls / routes_to / at_endpoint / touched_by / ...
        +-- per-edge confidence (0.0..1.0) for inferred inferences
        |
        v
[mnemo store]          SQLite + sqlite-vec  (~/.claude/mnemo/)
```

## Why

Claude memory is scattered: `~/.claude/projects/<project>/memory/*.md`, per-repo `CLAUDE.md`, design docs under `docs/plans/`, the global `~/.claude/CLAUDE.md`. As you work across projects, the same lessons get re-discovered, the same feedback gets re-given, the same designs get re-derived.

**v2.0 adds the missing half**: your *code* lives next to your memory in the same typed graph. Now "where is this function called from?" + "why was this commit made?" + "what feedback applies here?" are all one query.

When you start a session, mnemo injects a memory map. When you submit a prompt, hybrid Graph-RAG injects ≤ 800 tokens of relevant typed memory + code structure. When Claude edits a file, mnemo re-embeds in the background.

## What's new

Newest first. The full version-by-version diff is in [CHANGELOG.md](CHANGELOG.md).

### v4.x — Nebula custom graph engine + design-system + responsive

- **The Nebula at `/graph` is now a custom WebGL galaxy.** The whole third-party renderer stack (cytoscape, then sigma + graphology) is gone. The layout is computed **server-side in Python** — a deterministic, community-separating spectral embedding sheared into a face-on **log-spiral galaxy**, cached by `scope_key + fingerprint + LAYOUT_VERSION` (the browser is a pure renderer). It is drawn by a purpose-built single-file regl renderer (`nebula-gl.js`): crisp extension-free SDF star points graded by galactic radius, a rendered barred core-glow + full-viewport deep-space dust, length/zoom-graded curved edge filaments, every-node labels (frame-budgeted), a slow GPU-only galactic drift, and click-to-focus that flies + freezes on the node. Scales to 11k+ nodes; idle costs zero.
- **C1 design-system contract** — every primitive value single-sourced in `app.css :root` + a guard test; a documented page-shell contract so the v3.2-class layout bug can't return.
- **Fully responsive / adaptive layout** — breakpoint tokens, off-canvas drawers, collapse primitives, a strict no-overflow rule, responsive-contract tests.
- Provider registry, a dedicated settings surface, and chat-surface/backlog refinements.

### v3 — Mnem, the agentic companion

- **A tool-using agent over your own memory + code graph**, not a pre-canned RAG box. Four providers behind one abstraction (Anthropic / OpenAI / Google / Ollama), BYO API keys (env > repo `.env` > OS keychain > 0600 fallback; never in `settings.json`).
- **Permission protocol** — risk-tagged tools; `confirm`/`danger` calls pause for `POST /v1/chat/<id>/permit`; `allow_always` persists per-project.
- **Conversations are first-class** SQLite rows with an SSE event stream; a `/chat` page + a **Mnem companion dock on every page**, plus the `mnemo:doc` skill (` ```mnemo-draft ` fences → one-click Save as memory).
- **MCP server** (`mnemo mcp`, stdio) — the same tool surface, so Cursor / Claude Desktop / Codex / Windsurf get mnemo for free.

### v2 — Code Intelligence + language parity + Workspaces

- **Typed code graph** — every `code_repo` becomes `code_module` / `code_function` / `code_class` / `code_method` / `code_route` / `code_component` / `code_endpoint` nodes with `imports` / `defines` / `method_of` / `calls` / `routes_to` / `at_endpoint` edges, plus commit provenance (`touched_by`) from git history.
- **Tier 1 tree-sitter ingestion** with full structural extraction at Python parity for **Python, JavaScript, TypeScript, Go, and Django**.
- **Tier 2 call-graph resolver** — scope resolution with constructor + `self`/`this` lookups (same-file 0.95, cross-file via imports 0.8).
- **Tier 3 framework extractors** — FastAPI / Flask / Express (backend) + React (frontend) emit routes/components anchored on a shared `code_endpoint` URI so cross-stack sitemap queries work.
- **Source auto-router** (`mnemo source add` classifies kind + dry-run preview + 50k-file guard), the **`/code` UI family**, **7 code skills**, and **named Workspaces** — switchable bundles of `project_keys` + filter prefs; with no active workspace the UI drops into BASE-only mode.

### v1.2 — Learning to Listen (carried forward)

- **Feedback events** (thumb up/down per hit; auto-detected re-queries flag missed hits), **MMR re-rank** to de-duplicate paraphrases, and a **coordinate-descent auto-tuner** over the 6-term scoring weights (MRR objective, threshold-gated).

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
- [docs/plans/2026-05-14-mnemo-v3-design.md](docs/plans/2026-05-14-mnemo-v3-design.md) — full v3 (Mnem agentic companion) design.
- [docs/plans/2026-05-16-mnemo-v4-design.md](docs/plans/2026-05-16-mnemo-v4-design.md) — full v4.x (design-system contract) design.
- [docs/plans/2026-05-17-mnemo-v4.6-custom-graph-engine-design.md](docs/plans/2026-05-17-mnemo-v4.6-custom-graph-engine-design.md) — the custom Nebula graph engine (server layout + WebGL renderer) design.
- [docs/workflows/index.md](docs/workflows/index.md) — guided summaries of the workflow skills.
- [docs/examples/sample-queries.md](docs/examples/sample-queries.md) — real queries against a real `~/.claude/` memory, with the actual scored hits.
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

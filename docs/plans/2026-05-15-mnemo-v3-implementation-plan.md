# mnemo v3 "Mnem the agentic companion" — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Each phase is one commit on `release/3.0.0`. TDD is RIGID (superpowers:test-driven-development): failing test first, watch it fail, minimal code, watch it pass, commit. Pipeline #9 (pytest + ruff) gates every commit. Pipeline #2 (phased commits) + pipeline #13 (per-version handover+reindex at phase 12).

**Goal:** Ship v3 — an agentic chat companion ("Mnem") over mnemo's memory + code graph: multi-provider agent loop, full read/write/exec tool surface with a permission protocol, MCP server, persistent SQLite conversations, a 3-column `/chat` UI, a persistent companion dock, settings with BYO keys, and a doc-helper flow.

**Architecture:** Server-side agent loop in `mnemo.chat` drives a provider abstraction (Anthropic/OpenAI/Google/Ollama) over a single tool surface defined once in `mnemo.agent_tools` (two consumers: internal loop + `mnemo.mcp_server`). SSE streams thinking/tokens/tool-calls/permission-requests/citations/ui-actions to an Alpine/HTMX UI built on v2.2 streaming primitives. Conversations/messages/permissions are first-class SQLite rows via additive `_ensure_columns` migrations. Spec of record: `docs/plans/2026-05-14-mnemo-v3-design.md` (this plan does NOT restate its DDL/signatures — read both).

**Tech Stack:** Python 3.11+ (daemon runs 3.13 via `daemon/.venv`), FastAPI + SSE `StreamingResponse`, SQLite (`store.py`), Jinja2 + HTMX + Alpine.js, `anthropic` / `openai` / `google-genai` / Ollama HTTP, `keyring`, `mcp` SDK, pytest + ruff (`uv run` from `daemon/`).

---

## Ground rules (apply to EVERY task)

- **Run tests from `daemon/`**: `cd /d/Repository/knowledge-base/daemon && uv run pytest ...` (repo root has a broken system Python 3.10; `daemon/.venv` is 3.13).
- **No `Co-Authored-By`. No emojis.** Conventional commit prefixes. HEREDOC for multi-line messages. (CLAUDE.md hard rules.)
- **Schema**: append to `SCHEMA_SQL` (idempotent `CREATE TABLE IF NOT EXISTS`) for NEW tables; use `self._ensure_columns(table, {col: sqltype})` in `_init_schema` (store.py:643) for columns added to existing tables. Never rewrite a table.
- **Store CRUD pattern**: `with self._lock:` + `self.conn.execute(...)` + `self.conn.commit()`; `row_factory=sqlite3.Row`; typed `@dataclass` rows + `_row_to_*` helpers (store.py:667-731).
- **SSE pattern**: `StreamingResponse(generator(), media_type="text/event-stream")` (server.py:500, 782). One in-flight loop per conversation guarded by a `threading.Lock` keyed by conv id (mirror `state.reindex_lock` 409 pattern, server.py:387).
- **Config pattern**: extend `Config` dataclass + `_apply` partial-patch + `save` payload (config.py). Secrets NEVER go in settings.json — keychain only.
- **Tests**: `daemon/tests/unit/test_*.py`; reuse `store`, `fake_embedder`, `client` fixtures (tests/conftest.py); UI surface tests grep templates (pattern: `test_nebula_progressive.py`).
- **Verification before "done"**: superpowers:verification-before-completion — paste real pytest+ruff output before any success claim.
- After each phase commit: tick the TodoWrite item; do NOT pause for review (user chose "12 phased commits, continuous").

---

## Phase 1 — Chat schema + 6 read tools

**Commit:** `feat(daemon): chat conversations schema + 6 read tools`

**Files:**
- Modify: `daemon/mnemo/store.py` (append 3 tables to `SCHEMA_SQL` ~L385+; add `Conversation`/`ChatMessage`/`ChatPermission` dataclasses; add CRUD: `create_conversation`, `get_conversation`, `list_conversations`, `archive_conversation`, `rename_conversation`, `append_message`, `list_messages`, `grant_permission`, `revoke_permission`, `list_permissions`, `is_permission_granted`)
- Create: `daemon/mnemo/agent_tools.py` (the `TOOLS` registry: `(fn, json_schema, risk)` triples; implement the 6 `safe` read tools wrapping existing `retrieve`/`store`/`graph`)
- Test: `daemon/tests/unit/test_chat_store.py`, `daemon/tests/unit/test_agent_tools_read.py`

**TDD steps:**
1. Write `test_chat_store.py`: create→get conversation roundtrip; `append_message` assigns monotonic `seq` (0,1,2…); `list_messages` ordered by seq; `archive_conversation` sets `archived_at` and drops it from default `list_conversations`; `list_conversations` filters by `project_key` + sorts `updated_at DESC`; permission grant/list/revoke/`is_permission_granted` (project-scoped + global `project_key=NULL`); FK cascade (delete conv → messages gone). DDL exactly per design §5.
2. Run `uv run pytest tests/unit/test_chat_store.py -v` → FAIL (methods undefined).
3. Implement schema + dataclasses + CRUD following store.py patterns. New tables go in `SCHEMA_SQL`; if pre-v3 DB exists they're created by the idempotent `executescript`. Add an `idx_chat_*` index per design.
4. Run → PASS. Run full `uv run pytest tests/unit/test_chat_store.py` → green.
5. Write `test_agent_tools_read.py`: `TOOLS` contains exactly the 6 safe tools with `risk=="safe"`; each has a JSON schema with required params; `mnemo_query` returns ranked hits with `[mnemo:<id>]`-citable ids; `mnemo_get_node`/`mnemo_get_edges`/`mnemo_traverse`/`mnemo_search_by_type`/`mnemo_get_code_lines` return the documented JSON shapes on a seeded tmp store; unknown node id → structured `{error}` not exception.
6. Run → FAIL. Implement `agent_tools.py` (reuse `retrieve.retrieve`, `store.get_node/get_edges_for_nodes`, `graph` BFS, `paths` for code lines). 
7. Run → PASS. `uv run ruff check . && uv run ruff format --check .`.
8. Commit (stage only the 4 files).

## Phase 2 — Provider abstraction + Anthropic + agent loop (safe-only)

**Commit:** `feat(daemon): provider abstraction + Anthropic + agent loop (safe-only)`

**Files:**
- Create: `daemon/mnemo/providers/__init__.py` (`BaseProvider`, `ProviderEvent` types, `get_provider(name)` factory), `daemon/mnemo/providers/anthropic.py`
- Create: `daemon/mnemo/chat.py` (`AgentLoop`: append user msg → provider.stream → dispatch safe tools → loop ≤8 iters → persist messages → yield provider-agnostic SSE events)
- Create: `daemon/mnemo/keys.py` (resolve provider key: env var FIRST → keyring → plaintext fallback; load repo `.env` if present so the user's `ANTHROPIC_API_KEY` works)
- Modify: `daemon/pyproject.toml` (+`anthropic>=0.40`), regenerate `uv.lock`
- Test: `test_providers_anthropic.py` (mocked HTTP/stream), `test_agent_loop.py` (FakeProvider scripted events), `test_keys.py`

**TDD steps:**
1. `test_keys.py`: env var wins over keyring; `.env` at repo root loaded; missing key → typed error.
2. `test_agent_loop.py` with a `FakeProvider` yielding scripted `('text_delta'|'tool_call'|'stop')`: 8-iteration cap enforced; a `tool_call` to a safe tool dispatches + feeds `tool_result` back; text deltas accumulate into one persisted assistant message with citations; provider error → `error` event, conversation state preserved.
3. Run → FAIL. Implement `BaseProvider.stream` protocol (design §2), `AnthropicProvider` (SDK streaming → provider-agnostic events; tool_use blocks → `tool_call`), `AgentLoop` (lock per conv id; persist each message via Phase 1 CRUD; only `safe` tools auto-run — `confirm`/`danger` raise `PermissionRequired` placeholder for Phase 4).
4. `test_providers_anthropic.py`: mock the SDK; assert event translation + tool round-trip. (Live test gated on `ANTHROPIC_API_KEY`, `@pytest.mark.skipif` when absent — CI skips.)
5. Run → PASS. ruff. Commit.

## Phase 3 — Chat REST + SSE event stream

**Commit:** `feat(daemon): chat REST + SSE event stream`

**Files:**
- Modify: `daemon/mnemo/server.py` (add the 9 chat endpoints per design §5; per-conv lock dict on `AppState`; `POST /message` returns `{stream_url}`; `GET /events` = `StreamingResponse` text/event-stream draining the loop's event queue; `409` if a loop is in-flight)
- Modify: `daemon/mnemo/api_schemas.py` (Pydantic request/response models)
- Test: `test_chat_endpoints.py` (TestClient)

**TDD steps:** failing tests for each endpoint (create/list/get/patch/delete/message/events/cancel) incl. 409 concurrency + SSE framing (`data: {json}\n\n`, terminal `done`); implement against FastAPI patterns already in server.py; reuse `client` fixture; ruff; commit.

## Phase 4 — Write/exec tools + permission protocol

**Commit:** `feat(daemon): write/exec tools + permission system (confirm/danger risk tags)`

**Files:** extend `agent_tools.py` (confirm/danger tools per design §3), `chat.py` (pause loop on non-safe tool → emit `permission_request`, await `POST /permit`, resume same iteration; `allow_always` → `store.grant_permission`), `server.py` (`POST /chat/<id>/permit`), `store.py` (permission lookups already from Phase 1).
**TDD:** loop pauses + resumes on allow_once/allow_always/deny; danger never offers "always"; granted permission skips the prompt next call; cancel denies pending. Commit.

## Phase 5 — OpenAI + Google + Ollama providers

**Commit:** `feat(daemon): OpenAI + Google + Ollama providers`

**Files:** `providers/openai.py`, `providers/google.py`, `providers/ollama.py`; pyproject (+`openai`, +`google-genai`); Ollama via stdlib HTTP (no dep). Ollama tool-use prompt-template fallback for non-native models (design §4).
**TDD:** mocked-HTTP per provider; same event-translation contract as Anthropic; Ollama fence-parse fallback unit test. Commit.

## Phase 6 — MCP server

**Commit:** `feat(daemon): MCP server exposes same tool surface`

**Files:** `daemon/mnemo/mcp_server.py` (wrap `agent_tools.TOOLS` over MCP stdio + optional HTTP), `pyproject` (+`mcp`), `cli.py` (`mnemo mcp` entrypoint).
**TDD:** tool-list + tool-call roundtrip over the MCP protocol (in-process); risk tags surface as MCP annotations. Commit.

## Phase 7 — Settings page + keychain BYO keys

**Commit:** `feat(ui): settings page with keychain-backed BYO API keys`

**Files:** `server.py` (`GET /v1/settings`, `POST /v1/settings/providers`, `POST /v1/settings/companion` — keys to keyring, never echoed), `config.py` (companion/providers/chat sections + `_apply` + `save`; Pydantic validate), `keys.py` (keyring + Linux plaintext-0600 fallback + warning), `ui/templates/settings.html` + `ui/routes.py` route, `pyproject` (+`keyring`).
**TDD:** endpoint never returns key material (`has_key` bool only); env override reflected; settings.json shape per design §7; surface test greps settings.html for the 3 tabs' Alpine state. Commit.

## Phase 8 — `/chat` page (3-column shell + streaming + citations)

**Commit:** `feat(ui): /chat page with streaming + citation side panel`

**Files:** `ui/templates/chat.html`, `ui/routes.py` (`GET /chat`), reuse v2.2 `mnemoStreamFromSSE`/`mnemoStreamText`/`mnemoRenderBody`; tool-call rows = collapsible dim; citation panel uses `mnemoRenderBody`.
**TDD:** surface test (Alpine state: conversations rail, active thread, prompt input, citation panel; consumes `/v1/chat/<id>/events`; renders `[mnemo:ID]` as clickable). Live UX verify via preview tool. Commit.

## Phase 9 — Mnem companion dock + mood states + proactive nudges

**Commit:** `feat(ui): Mnem companion dock with mood states + proactive nudges`

**Files:** `ui/static/mnem/{idle,thinking,speaking,waiting,alert}.svg`, dock partial in `base.html` (persists across pages via localStorage `mnem.docked`), CSS @keyframes (pipeline 18 — no canvas), proactive bubble (dwell>30s, rate-limited per settings).
**TDD:** surface test (5 mood classes, dock on every page extending base.html, localStorage key, proactive opt-in gate). Commit.

## Phase 10 — Doc-helper flow + `mnemo:doc` skill

**Commit:** `feat(ui): doc-helper draft fences + mnemo:doc skill`

**Files:** chat.html JS parses ```` ```mnemo-draft ```` fences → "Save as memory" button → permission → `POST /v1/nodes` → memory reindex → toast; `skills/mnemo-doc/SKILL.md` (draft fence shape + frontmatter conventions per design §6F).
**TDD:** fence-parser unit (frontmatter+body extraction); surface test (Save button wiring); skill markdown present + well-formed. Commit.

## Phase 11 — UI-directive tools via SSE

**Commit:** `feat(ui): client-side UI directives via SSE event channel`

**Files:** `agent_tools.py` (the 5 UI-directive `confirm` tools emit `ui_action` not server-exec), `chat.html` JS dispatches `ui_action` (navigate/select_node/set_filter/scroll_to/open_panel) + echoes `ui_action_result` back into the stream so the model sees the landing.
**TDD:** loop emits `ui_action` for those tool names (no server dispatch); UI handler unit/surface test; round-trip `ui_action_result` re-enters the agent loop. Commit.

## Phase 12 — Release: bump, CHANGELOG, handover, reindex

**Commit:** `chore(release): v3.0.0 - Mnem the agentic companion`

**Steps:** bump `__version__` + `pyproject` to `3.0.0`; CHANGELOG entry; full `uv run pytest` + `uv run ruff check .` green; live smoke via preview (real Anthropic key from `.env`: create conv → ask → tool call → citation → permission prompt → settings); pipeline #13 — write `session_handover_*` + update memory references + `mnemo reindex`; open PR `release/3.0.0` → main. Commit.

---

## Execution

Per the user's decision ("12 phased commits, continuous"), this plan executes **in this session, continuously**, with rigid TDD per task and a pytest+ruff gate before every phase commit. No per-phase review pause. Live UI/UX (phases 7-11) is verified with the preview tool before its phase commit. The single human checkpoint is the final PR at phase 12.

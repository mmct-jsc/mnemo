# mnemo v3 design — Mnem the agentic companion

**Codename:** Companion. **Predecessor:** v2.5.1 (v2-deferred sweep closed).
**Status:** design, brainstormed + validated 2026-05-14.
**Owner:** mnemo core.

## 1. Goals + non-goals

v3 is the **agentic chat surface** over mnemo's memory + code graph. The model
isn't given pre-canned RAG context — it's an **agent with tools** that decides
what to query, traverse, write, and cite. Inline citations link back to mnemo
nodes; a side panel renders full-fidelity previews. The companion has a face
(Mnem), a personality, and the ability to drive every current + future feature
of the platform — gated by a permission system that prompts for the risky
operations.

### Hard goals (v3.0 must ship)

1. `/chat` page with multi-conversation left rail + streamed agent responses
2. **Mnem the companion** — persistent dock with avatar + 5 mood states on
   every page; auto-attaches page-context to conversations
3. Agent loop over **4 providers** (Anthropic, OpenAI, Google, Ollama) with
   unified tool surface
4. **Full action surface** — read tools (always safe) + write/exec tools
   (permission-prompted, `confirm` or `danger` risk level)
5. **Permission protocol** — `permission_request` SSE event + `POST /chat/<id>/permit`
   endpoint + persisted `chat_permissions` allowlist (project_key + tool_name)
6. **MCP server** — same tool surface, two consumers. External tools
   (Cursor / Claude Desktop / Codex / Windsurf) get mnemo for free.
7. **`mnemo:doc` skill** (deferred from v1.1) — agent drafts memory bodies in
   `mnemo-draft` fences; one-click "Save as memory"
8. BYO API keys in OS keychain via `keyring` lib + env-var override
9. Conversations + messages + permissions persisted in SQLite as first-class
   objects

### Hard non-goals (carried into the future or out of scope)

- Training / fine-tuning. We're a retrieval + agent layer over off-the-shelf
  models.
- Cloud sync of conversations. Local-first stays local.
- Auth / multi-user. Single user; threat model = local trust boundary.
- Cursor / Claude Desktop UX cloning. We have our OWN UI; MCP is the
  integration path for external tools.
- Auto-tuning agent behavior. v3.0 ships fixed prompts + a fixed permission
  model.

## 2. Architecture

### Data flow

```
Browser (chat UI)                Daemon                       LLM provider
                                 (agent loop in mnemo.chat)
─────────────────────────────────────────────────────────────────────────
POST /v1/chat/<conv>/message ──▶ append user message
                                 │
                                 ├──▶ provider.stream(messages, tools) ──▶
                                 │
SSE: thinking_start          ◀──┤
SSE: tool_call               ◀──┤◀── tool_use event from provider
SSE: permission_request      ◀──┤   (if tool.risk != 'safe')
   ──▶ POST /chat/<id>/permit
SSE: tool_result             ◀──┤
                                 │
                                 ├──▶ provider.stream(messages+tool_result) ──▶
                                 │
SSE: token, token, token     ◀──┤◀── text deltas
SSE: citation [mnemo:ID]     ◀──┤
SSE: ui_action               ◀──┤  (client-side directive, e.g. navigate)
SSE: done                    ◀──┘
```

### Module layout (new under `daemon/mnemo/`)

- `agent_tools.py` — tools as `(fn, schema, risk)` triples. ONE source of
  truth, two consumers (internal agent + MCP server).
- `chat.py` — server-side agent loop. Conversation table; provider
  iterations; SSE encoding; permission pause/resume.
- `providers/__init__.py` — provider abstraction. `BaseProvider.stream()`
  yields `('text_delta', str)` / `('tool_call', dict)` / `('stop', reason)`.
- `providers/anthropic.py`, `providers/openai.py`, `providers/google.py`,
  `providers/ollama.py` — per-provider implementations.
- `mcp_server.py` — exposes `agent_tools.TOOLS` over MCP (stdio + optional
  HTTP transport).
- `store.py` — `chat_conversations`, `chat_messages`, `chat_permissions`
  tables via additive `_ensure_columns` migrations.
- `server.py` — chat REST endpoints + settings endpoints.

### Agent loop iteration cap

- 8 turns max per user message (provider-call → tool-call cycles).
- Per-provider token budget configurable in settings.json (default: 8k input,
  4k output per turn).
- Pending permission requests do NOT count toward the iteration budget; the
  loop pauses, waits for `POST /permit`, and resumes the same iteration on
  decision.

### Provider abstraction protocol

```python
class BaseProvider:
    name: str  # 'anthropic' | 'openai' | 'google' | 'ollama'

    def stream(
        self,
        messages: list[Message],
        tools: list[Tool],
        *,
        model: str,
        max_output_tokens: int = 4096,
    ) -> Iterator[ProviderEvent]:
        """Yield provider-agnostic events:
          ('text_delta', str)
          ('tool_call', {id, name, args})
          ('stop', reason)            # 'end_turn' | 'tool_use' | 'max_tokens'
        """
```

## 3. Tool surface (read + write + execute, with risk tags)

The agent gets a **full action surface**. Each tool is tagged with one of
three risk levels that drive the permission system:

| Risk | Meaning | Examples |
|---|---|---|
| `safe` | No side effects; auto-run; no prompt | `mnemo_query`, `mnemo_get_node`, `mnemo_get_edges`, `mnemo_traverse`, `mnemo_search_by_type`, `mnemo_get_code_lines` |
| `confirm` | Mutates state recoverably; **always prompts** unless user picked "Allow always" | `mnemo_create_node`, `mnemo_update_node`, `mnemo_thumbs_feedback`, `mnemo_reindex_source`, `mnemo_add_source`, `mnemo_set_filter`, `mnemo_select_node`, `mnemo_navigate`, `mnemo_run_skill` |
| `danger` | Destructive / hard to undo; **always prompts**, no "Allow always" option | `mnemo_delete_node`, `mnemo_remove_source`, `mnemo_purge_conversation`, `mnemo_change_settings` |

### Read tools (the 6 safe ones)

1. **`mnemo_query(prompt, limit=8, max_tokens=800, project_key=None)`** —
   hybrid retrieval over memory + code, ranked, budgeted. Use for broad
   research.
2. **`mnemo_get_node(node_id)`** — full body + frontmatter for one node.
3. **`mnemo_get_edges(node_id, direction='both', relation=None)`** — list
   edges connected to a node, filterable by direction + relation.
4. **`mnemo_traverse(start_id, max_hops=2, relations=None)`** — BFS from a
   node up to `max_hops`, optional relation filter. Use for the
   "why-is-this-here" provenance walk.
5. **`mnemo_search_by_type(type, name_glob=None, project_key=None, limit=20)`**
   — list nodes by type + glob.
6. **`mnemo_get_code_lines(source_path, start, end)`** — read N lines from a
   registered source.

### Write / exec tools (confirm)

- `mnemo_create_node(type, name, body, frontmatter?, project_key?)` — write a
  new memory node; reindex memory dir afterward.
- `mnemo_update_node(node_id, fields)` — patch a node's name / description /
  body / frontmatter.
- `mnemo_thumbs_feedback(node_id, direction, query?)` — register a thumbs
  event in `feedback_event`.
- `mnemo_reindex_source(source_path=None)` — kick off a reindex via the
  v2.2.0 SSE generator.
- `mnemo_add_source(path, kind, project_key?)` — register a new source.
- `mnemo_run_skill(skill_name, args)` — invoke a named skill (e.g.
  `mnemo:retro`).

### UI directive tools (confirm, client-side)

The daemon does NOT execute these. It emits `ui_action` SSE events that the
chat UI's JS dispatches; the UI then echoes back a `ui_action_result` so the
model sees what landed.

- `mnemo_navigate(path)` — push browser history.
- `mnemo_select_node(node_id)` — select a node on the current Nebula / /code
  view.
- `mnemo_set_filter(filter_kind, value)` — apply a type / confidence /
  layout filter.
- `mnemo_scroll_to(selector)` — scroll a DOM element into view.
- `mnemo_open_panel(panel_id)` — open a UI panel (detail side panel, search
  popover, etc.).

### Danger tools

- `mnemo_delete_node(node_id)` — soft-delete a node (sets `deleted_at`).
- `mnemo_remove_source(path)` — unregister a source + cascade cleanup.
- `mnemo_purge_conversation(conv_id)` — wipe a chat conversation.
- `mnemo_change_settings(patch)` — mutate `settings.json` (provider /
  companion / chat history retention).

### Forward-compatibility

Every new mnemo feature ships with its tool definition in `agent_tools.py`.
Adding a kanban board feature in v3.4 = adding `mnemo_create_kanban_card` to
`TOOLS`. The agent picks it up the next session — no provider-side changes,
no UI rewires.

## 4. Permission protocol + agent loop details

### SSE event types

```python
('thinking', {iter: int})
('text_delta', str)                            # rendered into the assistant message
('tool_call', {id, name, args})                # the agent wants to call a tool
('permission_request', {
    id: uuid,
    tool_name: str,
    tool_args: dict,
    risk: 'confirm' | 'danger',
    rationale: str,                            # one-line plain-English for the user
    auto_grant_options: ['always', 'once'] if risk == 'confirm' else ['once'],
})
('tool_result', {id, result_summary})
('ui_action', {action, args})                  # client-side directive
('citation', {node_id, label})
('done', {})
('error', {message})
```

### Permission endpoint

```
POST /v1/chat/<id>/permit
{permission_id: uuid, decision: 'allow_once' | 'allow_always' | 'deny'}

  allow_once   -> dispatch tool, return result to model
  allow_always -> persist (project_key, tool_name) into chat_permissions; dispatch
  deny         -> return synthetic tool_result {"error": "user denied"} to model
```

### `chat_permissions` table

```sql
CREATE TABLE chat_permissions (
    project_key TEXT,          -- nullable (means: global)
    tool_name   TEXT NOT NULL,
    granted_at  INTEGER NOT NULL,
    PRIMARY KEY (project_key, tool_name)
);
```

### Default models per provider

| Provider | Default model |
|---|---|
| Anthropic | `claude-sonnet-4-5-20250929` |
| OpenAI | `gpt-4o-mini` |
| Google | `gemini-2.5-flash` |
| Ollama | `llama3.1:8b` (user picks any installed local model) |

### Error handling

- Provider API errors → `error` SSE event with provider message; conversation
  state preserved (user can retry).
- Tool dispatch exceptions → caught, returned to the model as a `tool_result`
  with `{error: "..."}` so the model can recover.
- Network / timeout → SSE `error` event; UI shows toast + retry button.
- Cancel (`POST /chat/<id>/cancel`) also denies any pending permission
  request.

### Ollama tool-use fallback

Models that don't natively support tool use (older llama, gemma, etc.) use a
prompt-template fallback: a system prompt teaches the model to emit
`<tool_call>{"name": ..., "args": ...}</tool_call>` fences. We parse these
client-side and translate to the same `tool_call` event. Newer Ollama models
(llama3.1+, qwen2.5, mistral-nemo) use Ollama's native tool-use API.

## 5. Chat schema + REST endpoints

### Tables

```sql
CREATE TABLE chat_conversations (
    id              TEXT PRIMARY KEY,         -- uuid4 hex
    name            TEXT NOT NULL,            -- auto from first prompt; user-editable
    project_key     TEXT,                     -- nullable for BASE-only conversations
    page_context    TEXT,                     -- JSON: {page: 'nebula', selected_node_id: '...', filters: {...}}
    provider        TEXT NOT NULL,            -- 'anthropic' | 'openai' | 'google' | 'ollama'
    model           TEXT NOT NULL,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    archived_at     INTEGER                   -- soft-delete; NULL = active
);

CREATE INDEX idx_chat_conv_project ON chat_conversations(project_key, updated_at DESC);

CREATE TABLE chat_messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,         -- 0..N within a conversation
    role            TEXT NOT NULL,            -- 'user' | 'assistant' | 'tool_call' | 'tool_result' | 'system'
    content_json    TEXT NOT NULL,            -- JSON: {text?, tool_call?, tool_result?, citations?: [node_id]}
    created_at      INTEGER NOT NULL
);

CREATE INDEX idx_chat_msg_conv ON chat_messages(conversation_id, seq);

CREATE TABLE chat_permissions (
    project_key TEXT,
    tool_name   TEXT NOT NULL,
    granted_at  INTEGER NOT NULL,
    PRIMARY KEY (project_key, tool_name)
);
```

### REST endpoints

| Method + path | Purpose |
|---|---|
| `GET /v1/chat` | List conversations (filtered by `project_key`, sorted by `updated_at` DESC) |
| `POST /v1/chat` | Create. Body: `{name?, project_key?, page_context?, provider?, model?}`. Returns conv id. |
| `GET /v1/chat/<id>` | Get conversation metadata + all messages |
| `DELETE /v1/chat/<id>` | Soft-delete (sets `archived_at`) |
| `PATCH /v1/chat/<id>` | Rename / change provider / change model |
| `POST /v1/chat/<id>/message` | Append a user message + run agent loop. Returns `{stream_url: '/v1/chat/<id>/events'}` |
| `GET /v1/chat/<id>/events` | SSE stream of the in-flight agent run |
| `POST /v1/chat/<id>/permit` | Grant or deny a pending permission request |
| `POST /v1/chat/<id>/cancel` | Cancel the in-flight agent loop (kills provider stream + denies pending permission) |
| `GET /v1/settings` | Read settings (excluding secret keys) |
| `POST /v1/settings/providers` | Write provider config + keys (keys go to OS keychain) |
| `POST /v1/settings/companion` | Write Mnem personality + dock preferences |

### Concurrency

One in-flight agent loop per conversation. `POST /chat/<id>/message` while
another is running returns `409 Conflict` with the existing stream URL. The
agent loop holds a `threading.Lock()` keyed by conversation id (same pattern
as `state.reindex_lock` from v2.2.0).

## 6. UI surfaces — Mnem the companion

### A. Mnem identity

- Default name: **Mnem** (short for mnemo; user-renamable)
- Avatar: 64×64 SVG mascot with **5 mood states** — `idle` (gentle
  breathing), `thinking` (spinning dots), `speaking` (mouth animates with
  token stream), `waiting` (raised hand — pending permission), `alert`
  (warning glow — denied permission or tool error)
- Animation: pure CSS @keyframes (pipeline 18 — DOM overlay, no canvas
  dependency). State changes via Alpine reactive class.
- Configurable tone: `formal` / `casual` (default) / `quirky`. Tone parameter
  injected into system prompt: *"You are Mnem, the mnemo companion. Speak in
  a {tone} register."*
- Lives in `daemon/mnemo/ui/static/mnem/` — `idle.svg`, `thinking.svg`, etc.

### B. Persistent dock

A small Mnem avatar pinned to the bottom-right of EVERY page (Dashboard,
Sources, Nebula, /code, /node, /chat). Click → companion side panel expands
inward (360px). Re-click → collapses to the avatar. The dock persists across
navigation via `<base.html>`; reads `localStorage.mnem.docked` to remember
preference (`closed` / `docked-open` / `pinned`).

### C. `/chat` page (primary surface)

Three-column shell:

```
+------------+------------------------------------+--------------+
| left rail  |  conversation thread               | citation     |
|            |                                    | side panel   |
| - new chat |  user: "what do we know about      |              |
| - convs[]  |   MQTT auth?"                      | [mnemo:abc]  |
|   ─ today  |                                    |  feedback... |
|   ─ yesterday  assistant (streaming):           |  ▾ preview   |
|            |  "Based on [mnemo:abc] we found..."|  (mnemoRender|
|            |    └ tool_call: mnemo_query        |   Body)      |
|            |    └ tool_call: mnemo_get_node     |              |
|            |  [prompt input ↓ "ask anything"]   |              |
+------------+------------------------------------+--------------+
```

Built on v2.2 primitives: `mnemoStreamFromSSE` consumes
`/v1/chat/<id>/events`; tokens flow into the active message via
`mnemoStreamText` (word unit); the side panel uses `mnemoRenderBody` from
v2.2.7. Tool-call events render as collapsible dim rows inside the assistant
message ("calling `mnemo_query(prompt='MQTT auth')`"). Tool-result events
render as a small node-count badge ("got 5 hits") with expand-on-click.

### D. Proactive presence (opt-in, rate-limited)

When the user dwells on a page > 30 s without interaction, Mnem can surface a
**suggestion bubble** above the dock: *"Want me to summarize what we know
about the selected node?"*. One bubble at a time; max 1 per 90 s (configurable
in Settings). Triggers:

- Long dwell (>30 s) without input
- Repeated similar queries
- Opening a node with > 5 `references_function` edges (signal:
  "decision-heavy node — user might want the provenance walk")
- New conversation suggestions based on recently-modified memory

User can dismiss-forever per page-type via Settings.

### E. Permission prompts inline

When the agent emits `permission_request`, Mnem's avatar switches to
`waiting` state. A compact prompt slides up from the dock: *"Mnem wants to
**create a new memory** about MQTT auth. [Allow once] [Always allow]
[Deny]"*. The `danger` risk level hides the "Always allow" button.
Once-granted permissions live in the conversation; always-granted permissions
persist in `chat_permissions`.

### F. Doc-helper flow

When the user asks "draft a memory for this", the agent uses `mnemo_query` +
`mnemo_get_node` to research, then emits a fenced draft block in its response:

````
```mnemo-draft
---
name: feedback-mqtt-auth-flake
type: feedback
projectKey: <key>
---
# MQTT auth flakes under broker reprovision
...
```
````

The UI parses `mnemo-draft` fences in the streamed text and renders a "Save
as memory" button next to each block. Click → permission prompt (first time
in a project) → `POST /v1/nodes` with the parsed frontmatter + body →
triggers a memory-dir reindex → toast linking the new node. The `mnemo:doc`
skill markdown (in `skills/`) instructs the agent on the draft fence shape +
frontmatter conventions.

## 7. Settings + key management

### `/settings` page — three tabs

**Tab 1: Providers + keys**

Per-provider row (Anthropic / OpenAI / Google / Ollama):
- Key field (password input; placeholder `••••` if a key exists)
- Default model dropdown
- "Test connection" button → minimal API call, ✓ or error
- "Use as default" radio (one provider is the active default for new
  conversations)

Storage: each key into the OS keychain via `keyring` under service `"mnemo"`
+ username `"provider:<name>"`. Env vars (`ANTHROPIC_API_KEY` etc.) override
the keychain at daemon startup. UI reads `GET /v1/settings/providers`
(returns `{anthropic: {has_key: true, model: '...'}, ...}` — never the key);
writes via `POST /v1/settings/providers` which stores into keychain.

**Tab 2: Companion (Mnem)**

- Name field (default "Mnem")
- Tone radio: `formal` / `casual` / `quirky`
- Dock state radio: `closed` / `docked-open` / `pinned`
- Proactive suggestions toggle (default ON) + per-page-type allowlist
- Suggestion frequency slider: `minimal` (1 per 5 min) / `normal` (1 per 90
  s) / `chatty` (1 per 30 s)
- Persisted in `~/.claude/mnemo/settings.json` (plaintext is fine; no secrets
  here).

**Tab 3: Permissions + history**

- List of always-granted permissions (project_key + tool_name + granted_at).
  Per-row "Revoke" button.
- "Clear all granted permissions" big-button.
- Chat history retention: `forever` (default) / `30 days` / `7 days`.
- "Export all conversations" → `mnemo-chat-export-<date>.json`.
- "Delete all conversations" — danger button with two-step confirm.

### Plaintext file shape

```json
{
  "version": 1,
  "default_provider": "anthropic",
  "providers": {"anthropic": {"model": "claude-sonnet-4-5-20250929"}, ...},
  "companion": {"name": "Mnem", "tone": "casual", "dock_state": "closed",
                "proactive": true, "proactive_pages": ["nebula", "node"],
                "proactive_frequency": "normal"},
  "chat": {"history_retention_days": null}
}
```

Daemon validates with Pydantic at load; UI re-fetches `GET /v1/settings` on
every Settings open so external edits are reflected.

### Linux fallback

Some Linux distros don't have a Secret Service daemon. Fallback chain:

1. `keyring` lib (uses Secret Service / KWallet)
2. `keyring.alt` plaintext keyring as last resort
3. If both fail, fall back to plaintext `~/.claude/mnemo/keys.json` (mode
   0600) with a one-time warning toast surfacing the security implication.

## 8. Testing + phased roadmap

### Test strategy

| Layer | Test approach |
|---|---|
| `agent_tools.py` (6 read + write/exec tools) | Unit tests with a tmp store: each tool's contract (input schema, output JSON, risk tag) |
| `providers/*.py` (4 providers) | Mocked-HTTP tests per provider. Record golden streaming responses; verify provider-agnostic event translation. Live integration tests gated on env-var keys (skipped in CI by default) |
| `chat.py` (agent loop) | Unit tests with a fake provider yielding scripted events; assert: 8-iteration cap, tool-dispatch, permission-pause + resume, error recovery |
| `mcp_server.py` | Stdio + HTTP transport tests; tool-list + tool-call roundtrip via the MCP protocol |
| Endpoints (`/v1/chat/*`, `/v1/settings/*`) | TestClient over the FastAPI app, same shape as v1.x endpoint tests |
| UI (chat page + companion overlay + Settings) | Surface tests grep the templates for required Alpine state, classes, x-effects (same pattern as `test_progressive.py` from v2.2) |
| Live UX | Manual via preview tool (pipeline 8); the 4-provider + permission flows need real eyes |

### Phased roadmap (12 phases, ~3-4 weeks)

| Phase | Surface | Commit message |
|---|---|---|
| 1 | Schema + chat tables + `agent_tools.py` (read-only 6) | `feat(daemon): chat conversations schema + 6 read tools` |
| 2 | Provider abstraction + Anthropic impl + agent loop core | `feat(daemon): provider abstraction + Anthropic + agent loop (safe-only)` |
| 3 | `/v1/chat/*` endpoints + SSE event stream | `feat(daemon): chat REST + SSE event stream` |
| 4 | Write/exec tools + permission protocol + `chat_permissions` | `feat(daemon): write/exec tools + permission system (confirm/danger risk tags)` |
| 5 | OpenAI + Google + Ollama provider impls | `feat(daemon): OpenAI + Google + Ollama providers` |
| 6 | `mcp_server.py` (stdio + HTTP transports) | `feat(daemon): MCP server exposes same tool surface` |
| 7 | Settings page + keyring storage + env override | `feat(ui): settings page with keychain-backed BYO API keys` |
| 8 | `/chat` page (3-column shell, conversation rail, citation panel) | `feat(ui): /chat page with streaming + citation side panel` |
| 9 | Companion (Mnem dock, 5 SVG mood states, proactive suggestions) | `feat(ui): Mnem companion dock with mood states + proactive nudges` |
| 10 | Doc-helper flow (`mnemo-draft` fences + Save-as-memory) + `mnemo:doc` skill | `feat(ui): doc-helper draft fences + mnemo:doc skill` |
| 11 | UI-directive tools (navigate, select_node, set_filter) wired through SSE | `feat(ui): client-side UI directives via SSE event channel` |
| 12 | Release: bump, CHANGELOG, handover, reindex | `chore(release): v3.0.0 - Mnem the agentic companion` |

Each phase is one PR onto `release/3.0.0`. CI gates each. Pipeline 13
(per-version handover + reindex) lands at phase 12.

### Open questions deferred to v3.x

- **Companion personalization** — user-uploaded avatars, custom personality
  prompts, skins, memory-of-user. Captured per user direction 2026-05-14;
  v3.0 ships fixed-but-tonable Mnem.
- **Streaming tool-result rendering** — when a tool returns a large body,
  stream it in too. Currently we render the whole JSON tool_result at once.
- **Multi-turn conversation summarization** — when a conversation hits ~50
  messages, auto-summarize the early history so the prompt fits the context
  window. Threshold + summarizer behavior is itself a brainstorm-pass.
- **Voice input / output** — mic + TTS layered on the same SSE event stream
  + a Web Speech wrapper. v3.x.
- **Cross-provider model auto-selection** — route code-heavy tasks to
  Claude, doc tasks to GPT-4o, etc. Needs a routing-policy DSL.
- **Agentic skills** — `mnemo:retro` / `mnemo:incident` / `mnemo:plan` could
  become agentic templates that Mnem runs end-to-end (vs the v1.1 rigid
  skills' phase-checklist approach).
- **Team / federation** — signed BASE-node deltas to a shared git repo
  (from `project_mnemo_future_versions.md` v3+). Out of scope for v3.0;
  needs its own design pass.

### Hard non-goals (re-stated for the roadmap)

- No training / fine-tuning
- No cloud sync of conversations
- No auth / multi-user
- No Cursor / Claude Desktop UX cloning (MCP is the integration path)

## Decision history

- 2026-05-14 — brainstormed + validated all 8 sections.
- 2026-05-14 — user picked: full vision + agentic AI; read-only + suggest
  writes UPGRADED later to full action surface with permission gates;
  Anthropic + OpenAI + Google + Ollama; MCP server in-scope; OS keychain
  storage; first-class conversations; server-side agent loop; Mnem
  personality; permission system with `confirm` + `danger` risk tags;
  companion is livable + interacts with current + future features;
  personalization deferred to v3.x.

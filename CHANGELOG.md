# Changelog

All notable changes to mnemo are documented here.

## [5.11.0] - 2026-05-22

T9 benchmark expansion. The agent-memory-spec-v0 fixture for the
prompt-architect task grows from the v0 stub (4 corpus nodes + 1
prompt) to the v0.1 surface promised in the spec (18 corpus nodes
+ 30 prompts across 10/10/10 confidence tiers + per-prompt
rubric + opt-in LLM judge for M4).

### Features

**30-prompt fixture across confidence tiers.** New
`bench/fixtures/prompt_architect/{corpus.jsonl,prompts.json,expected.json}`:
- 18 corpus nodes spanning 6 thematic clusters (MQTT auth /
  daemon lifecycle / retrieval / build / UI / policy).
- 30 prompts: 10 high-confidence + 10 medium-confidence + 10
  low-confidence. The architect's confidence-heuristic +
  clarification budget become visible at this scale — low-tier
  prompts carry `expected_clarifications >= 1` so future architect
  agents can train/evaluate against the signal.
- Per-prompt metadata under `expected.json["by_prompt"]`:
  `relevant_node_ids`, `acceptance_criteria_keywords`,
  `rubric` (for LLM judge), `expected_clarifications`.

**Per-prompt scoring + aggregation.**
`bench/agent_memory_bench/tasks/prompt_architect.py` adds:
- `PromptSpec` dataclass replacing the v0 stub's bare-string
  prompts.
- `score_prompt(spec, output, judge=...)` per-prompt scorer.
- `score_aggregate(fixture, outputs, judge=...)` aggregator (mean
  M4 + M3 across all 30 prompts; M2 summed from per-prompt
  lengths).
- `run(...)` collects per-prompt outputs into a JSON-encoded
  `TaskResult.output` so downstream auditors can inspect
  individual prompt answers.

**Opt-in LLM judge for M4.** New `bench/agent_memory_bench/judge.py`:
- `LLMJudge` class grades each rubric criterion 0.0-1.0 via Claude
  (default `claude-sonnet-4-6`); returns mean per-criterion score.
- `judge_from_env()` returns `LLMJudge` when both
  `MNEMO_BENCH_LLM_JUDGE=1` AND `ANTHROPIC_API_KEY` are set AND
  the optional `anthropic` package is installed. Otherwise returns
  `None` and per-prompt scoring falls back to keyword matching
  (the CI default + no-extras path).
- Graceful failure: any API/parse error returns 0.0 + records to
  `rationale_log` for audit; the benchmark run doesn't crash.
- New optional dep group `[llm-judge]` in `bench/pyproject.toml`:
  `pip install agent-memory-bench[llm-judge]` to enable.

### Tests

- `bench/tests/test_prompt_architect_v5_11.py` — 15 tests locking
  the new fixture shape (30 prompts, balanced tiers, per-prompt
  expected.json), the new scorer (`score_aggregate` perfect-output
  ceiling + vanilla floor), the locked invariant at aggregate
  scale, and the LLM judge opt-in contract.
- `bench/tests/test_prompt_architect.py` (the v0 stub) deleted —
  fully superseded by the v5.11 test file.

Bench suite: 27 passed / 1 skipped (was 12/1 in v5.10.0).

### Locked invariant survives

The strict invariant — `mnemo.answer_correctness > vanilla.answer_correctness`
— still holds AT AGGREGATE across all 30 prompts. (Per-prompt
invariant is weaker: a single low-confidence prompt where both
arms score zero is acceptable; aggregate is what locks the
substrate framing.)

### Anti-goals preserved

- 26-tool MCP surface contract test stays byte-stable.
- No new daemon dependencies (the bench is a sibling package; the
  Anthropic SDK is an optional extra of the bench, not the daemon).
- Behaviour identical for existing daemon callers; the bench
  package surface is the only thing that changed.
- Backward-compat `score(fixture, output)` shim retained so older
  test callers still work; the v0 stub test file deletion is the
  only breaking change.

## [5.10.0] - 2026-05-22

macOS + Linux autostart, closing the open invitation in v5.8.1's
`docs/autostart-windows.md` anti-goal section. All three platforms
now share one contract: wrapper script polls `/v1/health`, structured
log file, auto-retry on failure, idempotent install + clean
uninstall.

### Features

**macOS launchd autostart.** New `scripts/macos-autostart/`:
- `mnemo-autostart.sh` — bash wrapper polling `/v1/health` for up to
  60 s, logging to `~/Library/Logs/mnemo/autostart.log`.
- `com.mnemo.daemon.plist.template` — launchd user agent template
  with `RunAtLoad=true` + `KeepAlive` (respawns on unexpected exit).
- `install-launchd.sh` — renders the template into
  `~/Library/LaunchAgents/com.mnemo.daemon.plist`, calls
  `launchctl load`. Idempotent.
- `uninstall-launchd.sh` — `launchctl unload` + rm.
- `docs/autostart-macos.md` — full operator guide.

**Linux systemd-user autostart.** New `scripts/linux-autostart/`:
- `mnemo-autostart.sh` — bash wrapper polling `/v1/health` for up to
  60 s, logging to `$XDG_STATE_HOME/mnemo/logs/autostart.log`.
- `mnemo-daemon.service.template` — systemd-user unit template with
  `Type=oneshot` + `RemainAfterExit=true` + `Restart=on-failure`
  (60 s retry interval).
- `install-systemd.sh` — renders the template into
  `~/.config/systemd/user/mnemo-daemon.service`, calls
  `systemctl --user daemon-reload + enable + start`. Idempotent.
- `uninstall-systemd.sh` — `systemctl --user stop + disable` + rm.
- `docs/autostart-linux.md` — full operator guide, plus a note on
  `loginctl enable-linger` for headless setups.

**Cross-platform parity.** `docs/autostart-windows.md`'s anti-goal
section is replaced by a parity table listing all three platforms +
their canonical service identifiers (`mnemo-daemon-autostart` /
`com.mnemo.daemon` / `mnemo-daemon.service`) and the one-liner to
look each up.

### Tests

- `tests/unit/test_macos_autostart_scripts.py` (7 tests).
- `tests/unit/test_linux_autostart_scripts.py` (7 tests).

Same content-based assertions as v5.8.1's
`test_windows_autostart_scripts.py` (file presence + canonical
strings); the CI runners can't execute launchctl / systemctl, so the
test surface verifies structure rather than behaviour. Full suite
1479 passed / 2 skipped (+14 vs v5.9.0).

### Anti-goals preserved

- The 26-tool MCP surface contract test stays byte-stable.
- No new daemon dependencies (the new scripts are pure shell).
- Behaviour identical for existing callers — the new autostart paths
  are opt-in via the per-platform installer scripts.
- Windows autostart from v5.8.1 unchanged byte-for-byte.

## [5.9.0] - 2026-05-22

Closes the v5.4.0 bug 3 carry-forward properly. The "reindex
progress bar disappeared on tab re-entry" UX bug was patched at
v5.4.0 with an indeterminate "reindexing in background..."
placeholder; v5.9.0 finishes the job by exposing per-file numbers
on tab re-entry without needing an SSE reconnect.

### Features

**Stateful reindex progress.** New `AppState.reindex_progress`
field captures the latest `'file'` event from
`ingest.reindex_events` as the reindex loop yields them. Both the
POST `/v1/reindex` route and the SSE `/v1/reindex/events` route
publish to it; both clear it in the `finally` block alongside the
existing `reindex_started_at` cleanup.

**`GET /v1/reindex/status?include_progress=1`** (additive). Default
shape unchanged (`{running, started_at}`) so existing callers keep
working byte-for-byte. With the param, the response gains a
`progress` key — either `null` (no reindex running) or
`{idx, path, status, added, updated, unchanged, errors}` matching
the SSE event payload.

**Sources page UX.** `_checkReindexStatus` + `_pollReindex` now
pass `include_progress=1` and surface the actual current file +
running counters in the progress bar. Tab re-entry now shows the
actual current file rather than the indeterminate placeholder.
`_pollReindex` also live-updates the counters every 2 s.

### Tests

`daemon/tests/unit/test_reindex_progress_endpoint.py` — 4 contract
tests locking the wire schema:
- `AppState.reindex_progress` defaults to `None`
- Legacy `/v1/reindex/status` shape (no param) unchanged
- `?include_progress=1` adds `progress` key
- Endpoint reflects `state.reindex_progress` when populated

Full suite: **1465 passed / 2 skipped** (+4 vs v5.8.1). Ruff +
ruff format clean.

### Anti-goal preserved

Additive wire change only; legacy callers see no behaviour
difference. No new dependencies. 26-tool MCP surface contract
test stays byte-stable.

## [5.8.1] - 2026-05-22

Production-grade Windows autostart. Replaces the v5.0-era
Startup-folder `.vbs` (fire-and-forget, no observability) with a
Task Scheduler entry + health-probe wrapper + structured logs.

### Why this lands

User reported "daemon doesn't auto-run on startup". Investigation
(systematic-debugging Phase 1) showed the daemon DID start — 47 s
after boot, via the `.vbs` — but the gap between logon and "daemon
listening" was wide enough that Claude Desktop / `mnemo daemon
status` queries inside that window appeared to show "not running",
and the `.vbs` had no way to surface failures if a transient
problem (D: drive not mounted, Python env cold) kept the daemon
down.

### Features

**Task Scheduler autostart** (`scripts/windows-autostart/`).

- `mnemo-autostart.ps1` — the wrapper. Spawns `mnemo daemon
  start`, polls `/v1/health` for up to 60 s, logs each attempt to
  `%APPDATA%\Claude\mnemo\logs\autostart.log`, exits 0 only when
  the daemon is provably listening.
- `install-task.ps1` — registers the task: `AtLogOn` trigger
  (current user), `-RestartCount 3` with 1-minute gap on failure
  (Task Scheduler's minimum), hidden window, limited run-level
  (no UAC prompt). Idempotent.
- `uninstall-task.ps1` — `Unregister-ScheduledTask`. Daemon
  itself untouched; only the autostart wiring goes.
- `docs/autostart-windows.md` — install + test + uninstall walk-
  through, log-format reference, troubleshooting checklist.

**Smoke test verified end-to-end:** with the daemon stopped + port
clear, `Start-ScheduledTask -TaskName mnemo-daemon-autostart`
brought the daemon up in 3 s; the log line
`daemon healthy at http://127.0.0.1:7373/v1/health after 5s`
confirmed the wrapper's internal probe agreed.

### Tests

`daemon/tests/unit/test_windows_autostart_scripts.py` — 6 contract
tests asserting:
- All three PS1 scripts exist
- Wrapper polls `/v1/health` via `Invoke-WebRequest` / `Invoke-RestMethod`
- Wrapper spawns the editable-install `mnemo.exe` with `daemon start`
- Installer uses `Register-ScheduledTask` + `AtLogOn` + `RestartCount`
- Uninstaller uses `Unregister-ScheduledTask`
- `docs/autostart-windows.md` references all three scripts + the
  canonical task name

We don't actually run PowerShell in CI (Linux/macOS runners for
most jobs); structure assertions catch typo-class regressions.

### Anti-goal preserved

No daemon code changes; 26-tool MCP surface contract test stays
byte-stable. No new Python dependencies. Pre-v5.8.1 users on
non-Windows hosts are unaffected.

## [5.8.0] - 2026-05-22

Third surface for the v5 prompt-architect: the Claude Code slash
command `/mnemo-prompt`. Plus a CLI flag the slash command needs.

### Features

**`/mnemo-prompt` slash command** (`commands/mnemo-prompt.md`).
Drives the `mnemo:prompt-architect` skill (defined at
`skills/mnemo-prompt-architect/SKILL.md`) from inside Claude Code
so users can architect a paste-ready prompt without leaving the
IDE. Documents the four-phase flow inline (score confidence →
expand retrieval → emit sectioned block → citation discipline)
plus a "Provider-neutrality" section explaining that the skill is
shipped to every MCP host: Cursor, Claude Desktop, Continue,
Windsurf, Zed, Gemini CLI, OpenAI Agents SDK. All four surfaces
(slash command + dock pill + `/chat` pill + `mnemo_run_skill`)
converge on the same skill definition.

**`mnemo query --exclude-local-only` CLI flag.** Mirrors the same
flag on the MCP `mnemo_query` tool + the dock's architect-mode
POST body. The slash command always invokes retrieval with this
flag set because the architected prompt is paste-bound to a
foreign LLM and `local_only`-flagged nodes must never reach the
output.

**Plugin manifest version bump** — `.claude-plugin/plugin.json`
finally syncs to the daemon's `__version__` (was stuck at 4.6.1).

### Tests

`daemon/tests/unit/test_mnemo_prompt_slash_command.py` — 5 new
unit tests locking the slash-command + CLI-flag contract:
- `mnemo-prompt.md` exists with frontmatter (description +
  argument-hint)
- Command body references the `mnemo-prompt-architect` skill
- Command body invokes `--exclude-local-only`
- `mnemo query --help` exposes `--exclude-local-only`
- `test_plugin_manifest.py::test_all_slash_commands_present`
  updated to include `mnemo-prompt` in `expected_stems`

Full suite: **1455 passed / 2 skipped** (+5 vs v5.7.0). Ruff +
ruff format clean.

### Anti-goal preserved

The slash command is a Claude Code surface, but the underlying
workflow is provider-neutral (skill shipped to all MCP hosts).
The 26-tool MCP contract test stays byte-stable; no new wire-
protocol features.

## [5.7.0] - 2026-05-22

Substrate reach: Gemini CLI joins the documented MCP-host roster
alongside the v5.5.0 batch.

### Features

**Gemini CLI mount guide** (`docs/integrations/gemini-cli.md`).
Follows the v5.5.0 pattern (Claude Desktop / Continue / Windsurf /
Zed) verbatim. Gemini CLI uses the same `mcpServers` shape as
Cursor / Claude Desktop / Windsurf in its `~/.gemini/settings.json`
(workspace-scope `.gemini/settings.json` also works). Documents the
absolute-path PATH-fallback pattern and the optional `timeout` +
`trust` keys Gemini CLI's settings schema accepts.

**Smoke test** (`daemon/tests/integration/test_mcp_mount_gemini_cli.py`).
Parses the first fenced ```json``` block, asserts the mnemo entry
invokes the `mcp` subcommand, verifies `python -m mnemo.cli mcp
--help` exits 0. Same shape as the four v5.5.0 mount tests.

### Documentation

- `docs/integrations/PICKS.md` — Gemini CLI row marked **LANDED
  v5.7.0** with a backlink. Updated strategic-fit copy to reflect
  the substrate-reach push.
- `docs/integrations/README.md` — new "v5.7.0 Reach" subsection
  with Gemini CLI; "all seven documented hosts" promise updated.
- `README.md` What's-new — v5 chapter's v5.5.0 bullet extended to
  v5.5.0 + v5.7.0, "seven documented hosts" total.

### Anti-goal preserved

No MCP wire-protocol changes; 26-tool surface contract test stays
byte-stable. No new dependencies. Every mount still runs the
identical `mnemo mcp` stdio server.

### Carry-forward (still deferred)

LangGraph is the last unlanded entry in PICKS.md — deferred until
LangGraph ships native MCP (currently via `langchain-mcp-adapters`,
an adapter not a native surface).

## [5.6.0] - 2026-05-22

Port-listener becomes the authoritative source of truth for daemon
lifecycle. Closes the recurrent orphaned-daemon class of bug
(v3.2 gotcha #32, v5.5.0 lesson #93) that bit three times in a
single session.

### Bug fixes

**Orphaned daemon recovery (the v3.2 gotcha #32 / v5.5.0 lesson #93
loop).** `is_alive(pid)` uses `os.kill(pid, 0)` which on Windows
returns False for live processes in some edge cases (signal-0
behavior differs from POSIX; process privileges + Python's signal
mapping interact). When `is_alive` lies, `daemon.status()` reports
stale, `daemon.stop()` cleans up the pid file and reports
"daemon not running", and the actually-listening process becomes
an orphan that mnemo can no longer manage. Manual workaround was
`Get-NetTCPConnection`/`Stop-Process`.

Fix: new `_listener_pid_for_port(port)` helper in
`daemon/mnemo/daemon.py` uses `psutil.net_connections()` to find
the actual port owner. `status()` now treats the listener pid as
authoritative — when the pid file disagrees or is missing,
status returns `running=True` with the listener pid and
`orphaned=True`. `stop()` terminates the listener pid (not just
the pid-file pid), recovering the orphan automatically. `start()`
gains a post-spawn cross-check that the newly-listening pid is
the spawned daemon's.

### Features

`DaemonStatus.orphaned: bool` — new attribute, defaults False
for back-compat. True when the pid file disagrees with reality
(or is missing while something IS bound). CLI status command can
warn the user when this is set.

### Dependencies

Adds **psutil >= 6.1** for cross-platform port-listener
enumeration. Replaces the unreliable `os.kill(pid, 0)` Windows
path. Wheels available for all CI platforms (py3.11/12/13 on
linux/macos/windows).

### Tests

`daemon/tests/unit/test_daemon_orphan_detection.py` — 5 new unit
tests locking the orphan-detection contract:
- `_listener_pid_for_port` returns None for unused port
- `_listener_pid_for_port` returns os.getpid() when test binds a
  real socket
- `DaemonStatus.orphaned` defaults False
- `status()` detects orphan when pid file and listener disagree
- `status()` detects orphan when pid file missing but port bound

`tests/conftest.py::isolated_mnemo_home` fixture now auto-stubs
`_listener_pid_for_port` to None so pid-file-based lifecycle
tests don't see real OS-level daemons on :7373. Three existing
daemon tests updated to mock the listener to match their pid-file
fakes (so they test the v5.6.0 semantics correctly).

Full suite: **1447 passed / 2 skipped** (+5 vs v5.5.1). Ruff +
ruff format clean.

### Anti-goal preserved

No MCP wire-protocol changes; 26-tool surface contract test
stays byte-stable. Behaviour is identical for callers in the
healthy path (pid file agrees with listener); only the
recovery semantics improve.

## [5.5.1] - 2026-05-22

Hotfix found while live-verifying the v5.5.0 Claude Desktop mount.

### Bug fixes

**Cold-embedder timeout on first `mnemo_query`.** Each MCP host
(Claude Desktop / Cursor / Windsurf / Zed / Continue) spawns its
own `mnemo mcp` subprocess per conversation. That subprocess held
its own `Embedder()` instance which lazy-loaded the
`all-MiniLM-L6-v2` model (~22 MB) on first query — a ~15 s cold
load. The MCP client's tool-call timeout was shorter than that,
so the FIRST `mnemo_query` from any fresh conversation always
timed out (other tools like `mnemo_list_skills` /
`mnemo_session_nodes` returned instantly because they don't touch
the embedder).

Fix: new `prepare_stdio_server()` function in
`daemon/mnemo/mcp_server.py` eager-loads the embedder during the
MCP handshake (where there's no client-side timeout pressure).
`serve_stdio()` now calls `prepare_stdio_server()` instead of
`build_server()` directly, so warmup runs once per process at
startup and the first user-facing `mnemo_query` is sub-100 ms
warm. Warmup is wrapped in try/except — if model load fails
(no network on first install, sentence-transformers cache
corruption), the MCP server still serves tool calls; only the
first `mnemo_query` pays the cold-load cost.

### Tests

`daemon/tests/unit/test_mcp_warmup.py`: four contract tests
locking the warmup behaviour. Asserts (1) `prepare_stdio_server`
exists, (2) it returns `(server, ctx)`, (3) the returned ctx's
embedder `_model` is not None (proving the cold load happened
during prepare, not deferred), (4) `serve_stdio()`'s source
references `prepare_stdio_server` so the refactor sticks.

### Anti-goal preserved

No MCP wire-protocol changes; the 26-tool surface contract test
stays byte-stable. No new dependencies. Behaviour is identical
for callers — only the timing of the cold load shifts.

## [5.5.0] - 2026-05-22

MCP substrate reach: four new "5-minute mount" guides + smoke
tests for the most-asked-for MCP-capable hosts so the same
`mnemo mcp` stdio server is documented end to end across the
ecosystem.

### Features

**Four new mount guides (`docs/integrations/`).** Each follows
the Phase 1 flagship pattern (`cursor.md` / `openai-agents-sdk.md`)
verbatim — prerequisites, config block, verify, try-a-query,
troubleshooting:

- `claude-desktop.md` — Anthropic's canonical first-party MCP
  host. `mcpServers` shape in `claude_desktop_config.json`. Quit
  + relaunch (the menu-bar app keeps running otherwise).
- `continue.md` — open-source VS Code + JetBrains assistant.
  Different shape: `experimental.modelContextProtocolServers`
  list, each entry's command wrapped in a `transport` object.
- `windsurf.md` — Cascade panel; same `mcpServers` shape as
  Cursor / Claude Desktop, in `mcp_config.json`.
- `zed.md` — Rust-native editor; `context_servers` with each
  entry's command wrapped in a `command` object (`path` + `args`).
  Tools surface as `/mnemo-*` slash commands in the Assistant.

**Four parallel smoke tests (`daemon/tests/integration/`).**
Each test parses the first fenced ```json``` block in the doc,
asserts the mnemo entry invokes `mcp`, and verifies
`python -m mnemo.cli mcp --help` exits 0 (the silent-failure
mode the cursor test was designed around). The Zed test owns
its own `context_servers` shape; the other three share the
`mcpServers` shape.

### Documentation

- `docs/integrations/PICKS.md` — three formerly-deferred
  candidates (Continue / Zed / Windsurf) marked
  **LANDED v5.5.0** with backlinks; Claude Desktop added as a
  net-new "canonical first-party" row.
- `docs/integrations/README.md` — new "v5.5.0 Reach" subsection
  with all four mount links + an updated "what's still deferred"
  table (Gemini CLI + LangGraph remain deferred).
- `README.md` What's-new — v5 chapter gains a v5.5.0 bullet at
  the top: "one `mnemo mcp` stdio server, six documented hosts,
  one tool surface."

### Anti-goal preserved

No new MCP wire-protocol changes; the 26-tool surface contract
test stays byte-stable. Every mount runs the **identical**
`mnemo mcp` stdio server. Self-hosters get the same surface as
sponsor / hosted-tier users (anti-goal #1: free local-first
plugin stays fully capable, byte-for-byte unchanged).

## [5.4.0] - 2026-05-22

Three mobile-UX polish fixes (user-reported on the live dock) +
a documentation rule scrub.

### Bug fixes

**Mobile nav drawer auto-close on click.** Clicking any nav link
inside the mobile off-canvas drawer left the drawer open: the
browser navigated to the new page, the new page's
`navDrawer().init()` re-read `localStorage.mnemo.nav.open = '1'`
(the user's last interaction had opened it), and the drawer
re-appeared on the new page covering content. Fix: every
`<a>` in `.nav-drawer nav` now calls `close()` on click before
navigation, so localStorage flips to `'0'` first and the new
page loads with the drawer hidden. Desktop is unaffected (the
drawer is `display: contents` there; open/closed has no visual
effect). Live-verified at 375px viewport: click flips
`drawer.open` to false + `localStorage` to `'0'`.

**Nodes-page filter layout below `--bp-md`.** The `.section-head`
was a `display: flex; justify-content: space-between` row with
the H2 left + a 2-select filter form right. Below 60rem the
H2 took most of the width and the filters wrapped awkwardly
into a narrow right column. Fix: at `max-width: 60rem`, stack
`.section-head` vertically + let `.filters` span 100% width +
let each `<select>` flex 1:1 so both selects are roughly equal
width with a small "clear" button. Live-verified at 375px:
section-head flex-direction = `column`, filters = 335px wide,
each select = 136px.

**Reindex progress bar visible on tab re-entry.** Click reindex
on `/sources`, navigate to a different mnemo page, come back to
`/sources` — the progress bar was gone (the client-only
`progress.active` flag wiped on page reload), even though the
daemon was still mid-reindex. Fix: `_checkReindexStatus` now
sets `progress.active = true` with placeholder text
("reindexing in background...") whenever the daemon reports
`running: true`, so the bar reappears on re-entry. Per-file
numbers aren't backfilled (would need a second SSE subscription
that the existing endpoint would 'busy'-out), but the user
sees the indeterminate bar until `_pollReindex` detects
completion and reloads. A real backfill via a stateful
`/v1/reindex/status?include_progress=1` is the v5.x follow-up
this fix points at.

### Documentation: hard-rule scrub

Removed the **"No `Co-Authored-By` trailers on commits"** rule
from the prescriptive doc tree per owner direction. Files
touched:

- `CLAUDE.md` — deleted the rule line.
- `CONTRIBUTING.md` — deleted the two rule entries + the
  trailing "If a tool tries to inject a co-author trailer..."
  paragraph + the "No co-author trailer." trailing remark in
  the commit-message guidance.
- `.github/PULL_REQUEST_TEMPLATE.md` — deleted the
  `[ ] No Co-Authored-By trailers anywhere in commits` checklist
  item.
- `daemon/tests/unit/test_docs.py` — renamed/inverted
  `test_contributing_calls_out_no_co_author_rule` to
  `test_contributing_calls_out_no_emojis_rule` so the docs
  test now anchors on the remaining hard rule.
- Test fixtures + scripts that used "no co-author trailer" /
  "Co-Authored-By trailer" as sample text were replaced with
  "no emojis in code/commit messages" (the remaining hard
  rule), preserving the test's structural intent. Touched:
  `test_store.py`, `test_ingest.py`, `test_intent.py`,
  `test_embed_real.py`, `test_retrieve_real.py`, `bench.py`,
  `smoke_ingest.py`, `retrieve.py`, `nodes.html` placeholder.

Historical design docs under `docs/plans/` are left untouched
(closed-state retrospectives).

### Anti-goal preserved

- 47/47 existing `/v1/query` tests still pass without
  `hosted_auth_enabled`.
- Nav drawer click handler is additive; desktop layout
  unchanged.
- Filter-layout media query is scoped under `max-width: 60rem`;
  desktop layout unchanged.
- Reindex progress fix is purely client-side; no daemon API
  change.

### Tests

`test_ui.py::test_node_page_highlights_nodes_navbar` regex
relaxed from `\s+` between `href` and `class` to `[^>]*` so
the new `@click="close()"` attribute between them passes.
Other test-fixture renames (no-co-author → no-emojis) ride
through cleanly. Suite 1318 / 1 skip + 1 still-passing change
= 1319 / 1 skip total after the v5.3.0 baseline.

## [5.3.0] - 2026-05-22

Cursor variant pack. v5.1.1 shipped two themed cursors (default
+ pointer); v5.3.0 extends the pack to cover every other cursor
type actually used in mnemo's CSS — audited via grep across
`app.css`, `base.html`, and `chat.html`. Five new C1-palette
SVGs under `daemon/mnemo/ui/static/cursors/`:

| File | Used on | Visual |
|---|---|---|
| `mnem-cursor-grab.svg` | `.mnem-dock` (draggable surfaces) | teal halo + ring + 4 outward chevrons |
| `mnem-cursor-grabbing.svg` | `.mnem-wrap.dragging .mnem-dock` (mid-drag) | brighter ring + 4 inward chevrons |
| `mnem-cursor-not-allowed.svg` | disabled `.send` / `.mc-error .mce-retry` / `.chat-error .ce-retry` | warn-tone halo + circle with diagonal slash |
| `mnem-cursor-col-resize.svg` | `.nebula-gutter` (panel divider) | teal halo + horizontal bidirectional arrows |
| `mnem-cursor-progress.svg` | `button:disabled`, `.link-button:disabled` | teal halo + center dot + 4 satellite dots |

All 32×32 with hot spot center (16, 16). Same `, <state>`
platform fallback as v5.1.1 so any UA that refuses the SVG
cursor degrades cleanly to the OS variant.

### CSS wiring

Every `cursor: <state>` callsite in the bundle was prefixed
with the matching `url("cursors/mnem-cursor-<state>.svg") 16 16`
so the themed file takes over wherever the cursor type was
specified. Three files touched:

- `app.css` — `not-allowed`, `progress`, `col-resize` (relative
  URLs so the same rule works in the demo Pages build).
- `base.html` inline `<style>` — `grab`, `grabbing`,
  `not-allowed` (absolute `/static/cursors/...` paths; this
  template is daemon-only).
- `chat.html` inline `<style>` — `not-allowed` on .send /
  .ce-retry (absolute path; daemon-only).

`build_demo.py`'s existing `copytree` of the cursors directory
picks up the five new SVGs automatically — no build-script
change needed.

### Live-verified end-to-end on 127.0.0.1:7373

- All seven cursor SVGs serve as `image/svg+xml` with HTTP 200.
- `.mnem-dock` computed `cursor` = the themed grab URL + grab
  fallback.
- `.nebula-gutter` (on /graph) computed `cursor` = the themed
  col-resize URL + col-resize fallback.
- A synthesised disabled button computed `cursor` = the themed
  progress URL + progress fallback (matches the global
  `button:disabled` rule).
- 0 console errors after reload.

### Anti-goal preserved

- Variant pack is additive; nothing was removed from the
  existing cursor rules. Pre-v5.1.1 platforms still see the
  exact OS cursor via the `, <state>` fallback chain.
- Caret text input still keeps `cursor: text` (the v5.1.1 carve-
  out for caret precision); no themed text cursor in this pack.
- 47/47 existing `/v1/query` tests still pass without
  `hosted_auth_enabled`.

### Tests

+5 unit added to `test_themed_cursor.py`: every variant
exists, parses as valid SVG, uses the C1 palette, is wired
somewhere in the CSS bundle, and `base.html` specifically
carries the grab + grabbing references. Full suite stays at
1314 / 1 skip plus these 5 additions.

## [5.2.0] - 2026-05-22

Cross-surface prompt-architect. The architect pill that v5.0
shipped dock-only (per design Q3) now also appears on the
`/chat` page surface, matching the design doc S12 phased
roadmap that named cross-surface as the v5.x convenience
expansion.

### Changes

- `_chat_composer.html` page branch grows the same
  `mc-architect` toggle button + the same pre-emit
  `mc-localonly-warn` banner the dock has. Both surfaces share
  the `architectMode` factory state, so flipping it in one place
  isn't observable on the other (each chat-shell instance owns
  its own toggle, but they all flow through the same
  `sendMessage` -> POST -> `use_skill` wire).
- `chat.js` drops the `self.surface === 'dock'` guard inside
  the message-POST body assembly. When `architectMode` is true
  on EITHER surface, the POST carries `use_skill:
  'mnemo-prompt-architect'` so the phase-3 server-side
  entry-point pre-loads the skill before the model sees the
  user text.
- The page-surface placeholder also flips ("Describe the task;
  Mnem will architect a paste-ready prompt..." vs "Ask Mnem
  anything...") so keyboard / motor users hear the mode change
  via the placeholder text in addition to the visual pill.

### Anti-goal preserved

- `architectMode` still defaults False on every chat-shell
  instance. Legacy callers see byte-identical behaviour until
  they opt in.
- `use_skill` is still optional on `MessageCreateIn`; pre-v5
  POSTs are unaffected.
- 47/47 existing `/v1/query` tests still pass without
  `hosted_auth_enabled`.
- Dock-only restriction is lifted intentionally per the design
  doc; the cross-surface behaviour is the expected v5.x
  convenience.

### Tests

- `test_architect_toggle_only_on_dock_surface` renamed +
  inverted to `test_architect_toggle_on_both_surfaces` — both
  branches of `_chat_composer.html` must carry the toggle.
- All existing dock-side tests still pass (the dock branch is
  unchanged).
- Suite stays at 1314 / 1 skip.

## [5.1.1] - 2026-05-22

Two polish fixes for the Nebula graph + a themed cursor across
the whole UI surface.

### Bug fix: scroll-zoom anchored to the cursor again

**User report**: scroll-zooming on `/graph` landed the target
opposite the cursor; at certain galactic-rotation angles the
zoom seemed to "reverse" relative to the cursor position.

**Root cause**: the wheel handler in `nebula-gl.js` read
``screenToWorld(cursor)`` before AND after the zoom step, then
applied the delta to `cam.x/cam.y`. But `screenToWorld` applies
an inverse rotation by `-gA` so the returned point lands in the
STATIC frame (the frame the nodes + pick index live in).
`cam.x/cam.y` live in the DISPLAY frame (the shader applies the
`+gA` rotation AFTER cam). Adding a static-frame delta to a
display-frame cam rotated the correction by `-gA` — which at
`gA ≈ π` produces an exact reversal (the "got reverted"
symptom), and at intermediate angles produces the apparent
rotation drift around the cursor.

**Fix**: a new ``screenToCam`` helper returns the DISPLAY-frame
point (skips the inverse-rotate). The wheel handler uses it
instead of ``screenToWorld``. The static-frame helper stays
unchanged for picking + hover + node-drag (those want the
static frame). The two frames are now used consistently
per-callsite.

### Feature: themed custom cursor

Adds two C1-themed cursor SVGs under
`daemon/mnemo/ui/static/cursors/`:

- `mnem-cursor.svg` — default. Soft teal halo + a precise center
  dot. Carries the C1 accent (`#7ee7e0`).
- `mnem-cursor-pointer.svg` — interactive variant. Brighter
  ring + wider halo using `--accent-hover` (`#a5f0eb`); applies
  to links, buttons, `[role="button"]`, summary, label, the
  architect pill, the copy buttons, demo-page chips.

Both SVGs are 32×32 with hot spot center (16, 16). `app.css`
references them with relative URLs (`cursors/...`) so the same
rule works in BOTH the daemon at `/static/app.css` and the
demo at `/app.css` (the build copies the cursors directory
alongside).

Text inputs (`input[type="text"]`, `textarea`, etc.) keep the
OS text I-beam so caret placement is preserved.

### Tests + anti-goal

- +4 unit (`test_zoom_to_cursor.py`) — `screenToCam` exists +
  skips the inverse-rotate; wheel handler uses it; static-frame
  helper is still used by mousedown/pick.
- +10 unit (`test_themed_cursor.py`) — SVG files exist + parse
  + use accent palette; `app.css` references both with hot spot
  `16 16`; text inputs keep `cursor: text`; `build_demo.py`
  copies the cursors directory.
- Full suite 1314 / 1 skip.

Anti-goal preserved: pre-v5.1 `screenToWorld` semantics are
unchanged (pick / hover / node-drag still see the static
frame); the cursor CSS is additive and falls back to the
platform cursor when the SVG URL fails to load
(`url(...) 16 16, auto | pointer` fallback chain). 47/47
existing `/v1/query` tests still pass without
`hosted_auth_enabled`.

## [5.1.0] - 2026-05-22

Prompt-architect end-to-end polish. Two v5.0 surfaces shipped
without the connecting wire; v5.1.0 closes that loop and adds
the section-aware copy affordance the design doc S12 named as
the v5.x convenience expansion.

### Phase 1 -- `local_only_excluded` traveling through the full pipe

v5.0 wired the retrieval-level filter (Phase 1) and the dock UI
surface + Settings toggle (Phase 5) -- but the bridge between
them was never implemented. `mnemo_query` didn't accept
`exclude_local_only`, and its result dict didn't carry the
count. The dock's banner state `localOnlyExcluded` always stayed
at 0; the warning was invisible in practice.

Changes:

- `mnemo_query` grows `exclude_local_only` (optional, default
  False, advertised in the tool schema for any MCP host to
  discover). The prompt-architect skill already names this
  parameter in its analysis steps -- v5.1.0 makes the param
  real instead of a no-op.
- The tool result dict carries `local_only_excluded` (the count
  from `RetrievalResult.local_only_excluded`). Always present;
  0 when the filter is off. Wire-schema snapshot regenerated
  to reflect the new field.
- `chat.js` tool_result SSE handler reads
  `d.result.local_only_excluded`, accumulates into
  `self.localOnlyExcluded` whenever the count is non-zero.
  Multi-query architect runs aggregate drops across calls.

End-to-end flow now: architect skill invokes mnemo_query with
`exclude_local_only=True` -> retrieval drops N local_only nodes
-> result includes `local_only_excluded: N` -> SSE tool_result
event carries that -> dock factory's `localOnlyExcluded` reaches
N -> the v5.0 banner template `x-show=" localOnlyExcluded > 0
&& warnLocalOnly"` fires -> user sees the warning before paste.

### Phase 2 -- section-aware copy buttons on architected output

The dock now exposes TWO copy affordances on assistant messages
that look architected (contain a `## Prompt` heading):

- The existing `mc-copy` button (whole message; for Claude Code
  / Continue where the host has room for the cited context).
- A NEW `mc-copy-prompt` button (just the `## Prompt` section;
  for Cursor / Copilot where context budget is tight).

`chat.js` grows two helpers:

- `looksArchitected(text)` -- regex match for `(^|\n)##\s*Prompt\s*\n`.
- `extractPromptSection(text)` -- pulls the body of the
  `## Prompt` heading: everything from after the heading line
  to either the next `##`-level heading or EOF.

Non-architected assistant messages (regular chat) get only the
existing whole-message copy button; the second affordance is
gated on `looksArchitected(m.content.text)`.

### Tests

- +4 unit (`test_local_only_sse_wiring.py`) -- schema, result
  field, legacy backward-compat, chat.js handler.
- +6 unit (`test_architect_copy_buttons.py`) -- both JS helpers
  + dock template wiring + gate predicate.
- Full suite **1300 / 1 skip** (+19 from v5.0.1's 1281+4).
- Wire-schema snapshot regenerated (1 file changed; reflects
  the new `exclude_local_only` field on the mnemo_query schema).

### Anti-goal preserved

- `exclude_local_only` defaults False; every pre-v5.1 caller
  sees identical byte-for-byte mnemo_query output (only the
  additive `local_only_excluded` field is new, and it's 0 for
  them).
- 47/47 existing `/v1/query` tests still pass without
  `hosted_auth_enabled`; v4.7.0 hosted contract unchanged.
- `mc-copy-prompt` button is hidden by default (gated on
  `looksArchitected`); regular chat bubbles look the same.

## [5.0.1] - 2026-05-22

Hotfix: graph node drag breaks subsequent click-to-focus.

### Bug

User reported on the v5.0.0 live dock: drag a node to a new
position on `/graph`; then click the same node at its new
location -- the click is ignored, no focus event fires. The bug
predates v5.0 (lives in the v4.6 drag handler) but was reported
against the v5.0.0 build.

### Root cause

`buildPickIndex` (in `daemon/mnemo/ui/static/vendor/nebula-gl.js`)
bins each node into a uniform world-space grid by its initial
`(x, y)`. The drag-move handler updates `nodes[i].x/y` in place
but never re-buckets the node in the spatial index. After the
drag, the cursor at the node's NEW position computes a different
grid cell key -- the node isn't in any of the searched buckets
-- `pick.nearest` returns -1 -- no `clickNode` event fires.

### Fix

The pick index now tracks each node's current cell via a
`cellOf` Int32Array and exposes a `reindex(id)` method that
moves a node from its old bucket to the one implied by its
current `nodes[id].x/y`. The drag-move handler calls
`pick.reindex(dragId)` immediately after writing the new
position, BEFORE `invalidate()` so the next frame's hover-pick
also sees the updated bucket.

Cost: one array `splice` from the old bucket + one push to the
new -- both O(B) where B is the bucket size (typically much
less than total). No-op when the cell hasn't crossed a boundary
(common for sub-cell jitter during a fast drag), so the per-
frame cost during a continuous drag is dominated by node-position
writes anyway.

### Tests

+4 unit (`daemon/tests/unit/test_pick_reindex_after_drag.py`)
template-grep asserts pin the structural fix (`reindex` exists +
is wired to the drag handler + runs before `invalidate()`). A
live browser test would be more authoritative; the dock has no
JS test runner today, so the template-grep is the durable
regression catch.

## [5.0.0] - 2026-05-22

Mnem the prompt architect. The companion's next chapter: the user
types a quick or vague prompt into the dock; Mnem analyses it
against the typed Graph-RAG memory + code graph; Mnem emits a
polished, context-rich, paste-ready prompt the user copies into
any IDE AI agent (Cursor, Claude Code, Continue, GitHub Copilot).
The host LLM receives the same context Mnem has -- without
needing mnemo's MCP server itself.

Built on the v4.7.0 substrate: every change reuses the existing
companion + tool surface + skill loader. No new MCP tools, no new
top-level endpoints -- the prompt-architect is a SKILL invoked
through the existing `mnemo_run_skill` path, and the dock surface
rides the existing `/v1/chat/<id>/message` SSE channel.

Anti-goal preserved byte-for-byte: the free local-first plugin
stays fully capable. v5 ships zero hosted dependency; hosted-quota
users get v5 through the existing Phase 3b API at no new cost.
No new SKU.

### Phase 1 -- local_only node flag + retrieval filter

- New `nodes.local_only` column via additive `_ensure_columns`
  migration. Legacy DBs grow the column with default 0 on first
  reopen; the v4.7.0 anti-goal holds.
- Three input paths flag a node as local_only: frontmatter
  `local_only: true`, any `_private` path segment, or a body
  starting with `[LOCAL ONLY]`. Explicit `local_only: false`
  wins over the path heuristic.
- `retrieve.query(exclude_local_only=True)` filters flagged
  nodes; `RetrievalResult.local_only_excluded` carries the count
  so the dock can warn before paste.

### Phase 2 -- `mnemo-prompt-architect` skill

- New `skills/mnemo-prompt-architect/SKILL.md` follows the
  `mnemo:doc` pattern: single markdown + frontmatter, invocable
  via the existing `mnemo_run_skill` MCP tool. ANY MCP-capable
  host (Cursor, OpenAI Agents SDK, Claude Desktop) gets v5's
  wedge for free without mnemo's UI.
- Confidence formula encoded in the skill markdown:
  retrieval-derived (top_hit_score + hit_density +
  structural_bonus); threshold 0.6 for single-turn vs <=2
  clarifying questions.
- Output shape: six sections (Problem / Context / Files /
  Acceptance / Anti-patterns / Prompt) the dock parses for its
  copy-buttons. `[mnemo:<id>]` citation tags are opaque to the
  host LLM but signal provenance.

### Phase 3 -- dock entry-point for skill pre-load

- `MessageCreateIn` grows `use_skill` (optional). The dock
  passes `"mnemo-prompt-architect"`; legacy callers omit it and
  see no behaviour change.
- `AgentLoop.run(use_skill=...)` pre-loads the skill via the
  same `_skill` sentinel format the mid-loop handler already
  uses, so provider translators see one consistent shape.
- Backward compat: omitting `use_skill` runs the loop exactly
  as before; no extra event, no extra user-role turn.

### Phase 4 -- dock architect-mode toggle

- New "Architect" pill left of the dock textarea. Click flips
  `architectMode` in the chat factory; sendMessage attaches
  `use_skill: 'mnemo-prompt-architect'` to the POST.
- Dock-only per design Q3. The `/chat` page surface stays
  uncluttered in v5.0; cross-surface convenience is v5.x.
- The textarea placeholder changes when architect mode is on so
  a keyboard user hears the mode flip via the placeholder text
  in addition to the visual pill.
- Provider-neutrality preserved: the architected output is
  paste-bound markdown that renders cleanly in Cursor / Claude
  Code / Continue / Copilot.

### Phase 5 -- pre-emit local-only warning + Settings toggle

- Warning banner above the dock composer when the architect
  retrieval reports a non-zero `local_only_excluded` count.
- Configurable in Companion Settings as
  `warn_on_local_only_exclusion` (default **True**, because the
  output is paste-bound to a foreign LLM).
- Defense-in-depth: the standing rule about `docs/_private/`
  confidential content is enforced at the schema level, not
  just by reviewer attention.

### Phase 6 -- T9 benchmark task + locked invariant

- New T9 in `bench/agent_memory_bench/tasks/prompt_architect.py`.
  Vanilla raw prompt to host LLM vs mnemo-architected prompt to
  same host. Metric: acceptance-criteria satisfaction (M4).
- Strict invariant locked in CI:
  `mnemo.answer_correctness > vanilla.answer_correctness`. This
  mirrors T1's `vanilla > mnemo` on rederivation rate, but in
  the opposite direction.
- v5.0 stub scope: 4 corpus nodes, 1 high-confidence prompt.
  v0.1 expansion to 30 prompts + LLM judge is on the public
  roadmap.

### Phase 7 -- dock thumbs feedback (in-place)

- Existing v1.2 `mnemo_thumbs_feedback` surface auto-applies to
  the architect output; aggregates feed the v1.2
  coordinate-descent tuner so the architect's confidence
  weights self-tune as dogfood data accumulates.

### Phase 8 -- docs + spec extension (CC-BY-4.0)

- T9 appended to `docs/benchmark/agent-memory-spec-v0.md` so
  external implementers see the locked invariant alongside T1's
  mirror.

### Anti-goal verification

Every change shipped without altering the v4.7.0 baseline byte
for byte for the legacy paths:

- 1259 -> 1275 unit tests passing (+16 net new; 1 skipped
  unchanged).
- 18 -> 24 bench tests passing (+6 T9; 1 skipped HTTP gate).
- `architectMode` defaults False on every dock instance; the
  dock POST is unchanged when the user hasn't opted in.
- `use_skill` is optional on `MessageCreateIn`; every pre-v5
  caller runs the loop with zero behaviour difference.
- The 47/47 existing `/v1/query` tests still pass without
  setting `hosted_auth_enabled`; the v4.7.0 hosted-tier
  contract is unchanged.

### Strategic positioning

v5 = Angle #6 (NEW; not in the original strategy doc). PARALLELS
the shipped Angles 1-3 (substrate / hosted API / benchmark);
does NOT supersede the dormant Angles 4-5 (team SaaS /
enterprise daemon -- those stay demand-pull). T9 in the open
benchmark adds to the sponsor narrative for the Anthropic /
OpenAI / Google grant follow-ons.

## [4.7.0] - 2026-05-21

The substrate + benchmark + hosted-tier release. Bundles 22
commits across Phase 1 / Phase 2 / Phase 3 of the enterprise
execution plan into a single minor bump. Every change preserved
the same anti-goal: the free local-first plugin stays fully
capable, byte-for-byte unchanged for self-host loopback users.

### Phase 1 — MCP substrate hardening (8 commits)

- **Locked the 26-tool MCP surface as a contract test**
  (`daemon/tests/unit/test_mcp_tool_surface_contract.py`). Rename
  or removal of any published tool now fails CI.
- **Cursor 5-minute mount guide** (`docs/integrations/cursor.md`)
  + integration smoke test. One `mcp.json` block, window reload,
  done.
- **OpenAI Agents SDK 5-minute mount guide**
  (`docs/integrations/openai-agents-sdk.md`) with Python +
  TypeScript snippets using `MCPServerStdio`. Demonstrates mnemo
  working cleanly inside OpenAI's flagship agent runtime.
- **Provider-neutral positioning in README** + new
  `docs/integrations/` index linking the picks + the selection
  rubric in `PICKS.md` (rubric + deferred candidates documented:
  Continue, Zed, Gemini CLI, LangGraph).
- **Typed `Risk = Literal["safe", "confirm", "danger"]`** + the
  `ALL_RISKS` constant as the single source of truth for the
  tool-risk taxonomy.
- **Wire-schema snapshot test** — byte-for-byte JSON snapshot of
  `tool_list()` at `daemon/tests/unit/_snapshots/mcp_tool_list.json`
  (528 lines, 26 tools), `MNEMO_UPDATE_SNAPSHOTS=1` regen-gated.
  `docs/integrations/wire-schema.md` documents the contract.

### Phase 2 — Open agent-memory benchmark + ROI surface (6 commits)

- **CC-BY-4.0 spec** for the agent-memory benchmark
  (`docs/benchmark/agent-memory-spec-v0.md`) — 8 tasks (T1–T8),
  4 metrics (re-derivation rate, tokens-to-answer, citation
  precision, answer correctness), 2 reference baselines, `Memory`
  Protocol, fixture format, roadmap to v1.0. First reproducible
  benchmark for typed Graph-RAG agent memory in the public
  domain.
- **MIT harness** at new top-level `bench/` package. Zero runtime
  deps in the core; mnemo HTTP adapter via the `[mnemo]` extra.
- **T1 answer-follow-up task** end-to-end with vanilla + mnemo
  baselines. The strict invariant
  `vanilla.rederivation_rate > mnemo.rederivation_rate` is a
  CI-enforced contract.
- **`GET /v1/roi/summary` endpoint** + `RoiSummaryOut` schema +
  `Store.roi_summary()` aggregator. 5 fields:
  `queries_total`, `rederivations_avoided`, `tokens_saved_est`,
  `thumbs_up_ratio`, `auto_tune_iterations`.
- **Dashboard ROI summary card** (server-rendered Jinja, no
  client-side flash on page load).
- **First case study** from the dogfooded install:
  `docs/case-studies/2026-05-mnemo-self-host.md`. Real numbers:
  310 queries / 11.4 days / 12,494 nodes / 11 sources / ~62K
  tokens-saved (lower-bound estimate). Honest framing —
  `thumbs_up_ratio = 0.0` reported as-is.

### Phase 3a — Hosted-tier operator surface (3 commits)

- **`api_key` + `quota` + `usage_period` schema** as an additive
  `SCHEMA_SQL` block (FK CASCADE + composite PKs + UNIQUE on
  hash). Harmless on installs that never enable hosted mode.
- **`mnemo key {create,list,revoke}` CLI** — 32-byte
  `secrets.token_urlsafe` raw key + per-key 16-byte
  `secrets.token_hex` salt + salted SHA-256 hash. Raw key
  printed ONCE; only the hash + salt persisted.
- **`mnemo billing report --period YYYY-MM` CLI** emitting
  stable-header CSV (`key_name,queries,tokens,quota_queries,
  quota_tokens,over_quota`). Aggregator joins api_key +
  usage_period + quota with `over_quota` per-dimension.
- **`docs/hosted/deploying.md`** — full operator guide
  (reverse-proxy template, key issuance flow, backup hygiene,
  anti-goals callout).

### Phase 3b — Hosted-tier runtime (3 commits)

- **`Config.hosted_auth_enabled: bool = False`** + the
  `api_key_or_local` FastAPI dependency on `/v1/query`. Loopback
  (127.0.0.1 / ::1 / localhost) stays exempt EVEN when the flag
  is on, so local UI / CLI / plugin keeps working on hosted
  deployments. Config read fresh per request (flag flip takes
  effect on the next inbound request — no daemon restart).
- **Per-request metering hook** writes to `usage_period` via
  `Store.record_usage` UPSERT (`ON CONFLICT (api_key_id, period)
  DO UPDATE` — atomic, no race). UTC `YYYY-MM` period so DST +
  zone drift can't shift billing attribution.
- **Pre-handler quota enforcement** returns HTTP 429 with
  `Retry-After: <seconds-to-next-UTC-month>` when the key is at
  or over its monthly limit. Strict `>=` (exactly `max_queries`
  successful requests before rejection). No-quota-row =
  open-billing posture.
- **47/47 existing query-related tests pass WITHOUT setting the
  new flag** — anti-goal verified in CI.

### Phase 3 follow-ups (2 commits)

- **`docs/hosted/deploying.md`** updated to reflect Phase 3b
  shipped (flag-flip recipe + 429 contract + decision matrix).
- **`mnemo key set-quota <id> --max-queries N --max-tokens N`
  CLI** — closes the last Phase 3 feature gap (replaces the
  SQLite-direct workaround). Idempotent UPSERT via
  `ON CONFLICT DO UPDATE`.

### Test suite

- **+113 tests since v4.6.5** (1132 → 1245 unit pass + 1 skip).
- Cross-platform on Linux, macOS, Windows.
- Zero anti-goal regressions: every change preserved the
  self-host loopback path byte-for-byte.

### Anti-goals (still in force, codified in CI)

- The free local-first plugin stays fully capable. No feature
  was removed or gated.
- The daemon binds 127.0.0.1 only; the reverse proxy is what
  exposes it on the public interface.
- Hosted tier is operator convenience for shared deployments,
  not a paywall on the local plugin.
- Provider-neutrality is shipped, not aspirational: Cursor +
  OpenAI Agents SDK both consume the same 26-tool MCP surface.

## [4.6.5] - 2026-05-20

### Security

- **Bump `idna` to `>= 3.15`** in both `daemon/uv.lock` and
  `clients/middleware-py/uv.lock` to close the CVE-2024-3651 bypass
  (GHSA-65pc-fj4g-8rjx). `idna` 3.14 rejects oversized inputs to
  `idna.encode()` up-front, and 3.15 (the resolved version) adds an
  early DNS-length cap on individual labels for defense-in-depth.
  Flagged by GitHub Dependabot. No behavior change beyond hardened
  input validation in `idna.encode()`; no API or test impact.

Incidental: `uv lock` also refreshed two unrelated entries in
`clients/middleware-py/uv.lock` -- the `exceptiongroup` marker
narrowed from `python_full_version < '3.13'` to `< '3.11'` (now
correctly defers to the Python 3.11+ stdlib version), and
`mnemo-middleware`'s self-version pin caught up to `4.6.2`. Both are
benign lockfile-hygiene refreshes with no behavior change.

## [4.6.4] - 2026-05-19

**Smooth ribbon edges + focus pop + edges-behind-nodes + labels
auto-off** (prod `/graph` + the demo; renderer-only, `LAYOUT_VERSION`
unchanged). Four user-reported issues, root-caused:

- **Edges are now a feathered anti-aliased RIBBON, not 1px GL
  `lines`.** GL `lines` are hard-rasterized with no AA, so a
  tessellated polyline of them read as "thousands of straight lines
  connected" (the recurring complaint; a bigger segment count is the
  same family and never fixes the hard 1px look). Root-cause fix: per
  edge a triangle-strip ribbon -- each centerline sample emits 2
  verts offset +/-1 along the screen-space normal of the curve
  tangent (constant pixel width at any zoom); the fragment
  smoothstep's alpha across `abs(side)` -> one continuous smooth
  band. Independent edges concatenate into one strip via degenerate
  joins. The existing length/zoom alpha grade is preserved. Geometry
  is ~1.4x the prior line count (bounded; the alpha grade keeps fill
  cheap) -- **GPU-verify there is no new lag on the full graph**; the
  documented fallback is to revert edges to `lines` if it regresses.
- **A highlighted/focused node now POPS:** `gl_PointSize` scales with
  `hl` (markedly larger) and its bloom is boosted -- previously
  highlight only re-coloured it at the same tiny size as 12k others.
- **All edges render BEHIND every node:** the accent/incident
  `hiEdges` pass was drawn after `drawNodes` (painting over nodes);
  it now draws before, with the base web.
- **Labels auto-off by default** in the renderer, `graph.html`
  (`labelsVisible:false`; a prior explicit choice is still restored
  from localStorage), and the demo.

Suite 1257 pass / 2 skip; ruff clean. Pages auto-redeploys on merge
with the new renderer.

## [4.6.3] - 2026-05-19

**Renderer drag-confinement fix (prod + demo) + demo /graph parity.**

- **`nebula-gl.js` (the shipped renderer -- affects prod `/graph`):**
  the `mousemove`/`mouseup` listeners are on `window` (so a drag
  survives a brief cursor excursion), but they used
  `e.offsetX/offsetY` -- which on a window-level listener is relative
  to `e.target`. The instant the cursor crossed onto a sibling panel
  (tree / detail / filter) the coordinate origin jumped and the
  camera flicked ("drag to the side bars flicks weirdly", reported in
  both the demo and prod). Fixed at the root: one `ptr(e)` helper
  computes canvas-relative coords from `getBoundingClientRect()` +
  `clientX/clientY` (viewport-stable, target-independent) used by
  wheel/mousedown/mousemove, and pan/drag/hover now **bail when the
  pointer is outside the canvas rect** -- no action out of the
  canvas. Drag still survives an excursion and resumes on re-entry;
  `mouseup`/`blur`/`buttons===0` still end it.
- **Demo page:** a canvas click now focuses the node (the demo's
  `select()` is the single source and calls `gl.select()` itself, so
  it no longer worked only from the tree/neighbour clicks); and the
  filter (find + type chips) moved out of the file panel into a
  full-width bottom bar on the shell's 2nd grid row.

Renderer-only + demo-only; `LAYOUT_VERSION` unchanged. Suite 1251
pass / 2 skip; ruff clean. (extension/middleware unchanged -> stay
at 4.6.2; the demo redeploys on merge with the fixed renderer.)

## [4.6.2] - 2026-05-19

**MCP / tools / extension audit + version alignment.** A sweep of the
non-daemon surfaces against the shipped v4.6.x state.

- **Tool descriptions de-sigma'd (honesty fix).** `mnemo_session_nodes`
  and `mnemo_highlight_nodes` in `agent_tools.py` still described the
  **deleted v4.5 sigma.js renderer** ("sigma's nodeReducer", "v4.5's
  sigma.js renderer swap") — false since v4.6 replaced it with the
  custom `nebula-gl.js` WebGL engine. Rewritten to describe the v4.6
  renderer's `setHighlight()` truthfully (the capability is unchanged
  and still backed by real wiring; the gotcha-31 / C3-honesty
  contract and its guard test stay green). The MCP server exposes the
  same registry, so its tool surface inherits the corrected wording
  automatically (no `mcp_server.py` change — it is a single-source
  pass-through with no version string or parity drift).
- **Version alignment.** The VS Code extension
  (`extensions/vscode`, was 1.1.0) and the Python middleware client
  (`clients/middleware-py`, was 1.1.0) are bumped to **4.6.2** to
  match the daemon. The extension's daemon client was audited — all
  endpoints it uses (`/v1/health|query|projects/active|sources|
  reindex|nodes`) still exist and are contract-compatible on v4.6.x,
  so no client code changes were needed.

Known follow-up (flagged, out of scope for a version sync): the
middleware `[google]` extra still targets the legacy
`google-generativeai` SDK because its shim's `install()` only wraps
that API; aligning it to the project-standard `google-genai`
(`client.models.generate_content`) is a real shim change tracked
separately.

## [4.6.1] - 2026-05-19

**Two interaction regressions on the shipped v4.6.0 Nebula, fixed.**

- **Laggy after the first node click.** `easeTo()` (the camera fly on
  select) called the self-perpetuating `frame()` directly every step;
  nothing cancels the previously-scheduled frame, so each click
  forked another never-dying `requestAnimationFrame` render chain --
  compounding (worse with every click) and made blatant by v4.6.0's
  ~10x-heavier curved-edge scene plus the rotation freezing on focus
  (the loop full-re-rendered an unchanging image forever). The render
  loop is now a single scheduler: the ease only mutates the camera
  and requests a render via `invalidate()`; `frame()` re-arms ONLY
  while genuinely animating (the galaxy is rotating, no node focused,
  OR an ease is in progress) and otherwise idles until the next
  interaction. Restores the design's "renders only when dirty, idle
  == zero cost": the galaxy still travels when nothing is focused; a
  focused/still graph now costs zero.
- **Node drag never stopped off-canvas.** A `mouseup` released
  outside the window (or focus lost) never reached the handler, so
  the drag state stayed set and every later mouse move kept dragging
  the node with no button held. The drag now self-heals -- any move
  with no button pressed ends it, and a window blur ends it too.

## [4.6.0] - 2026-05-19

**Custom graph engine -- the Nebula is a galaxy.** User rejected the
entire v4.5 third-party (sigma.js) renderer arc and directed: build
custom libraries, optimise performance and quality. The whole
third-party renderer stack is deleted and replaced by a purpose-built
two-part engine, validated + TDD-planned before implementation
(`docs/plans/2026-05-17-mnemo-v4.6-custom-graph-engine{,-design}.md`):

- **Server-side layout** (`daemon/mnemo/ui/graph_layout.py`):
  deterministic community-separating spectral embedding -> light
  ForceAtlas2 declutter -> sheared into a face-on logarithmic-spiral
  **galaxy** (mild structural bulge + coherent arms; locality
  preserved so edges stay meaningful); small components / singletons
  become a radial, outward-thinning halo field. Cached + algorithm-
  versioned (`LAYOUT_VERSION`), computed once per scope; the browser
  is a pure renderer.
- **Custom WebGL renderer** (`static/vendor/nebula-gl.js`, vendored
  `regl` 2.1.0; sigma + graphology removed): crisp extension-free SDF
  star points (a true stellar palette graded by galactic radius), a
  rendered warm barred core-glow + deep-space dust, a slow perpetual
  GPU-only galactic rotation ("the nodes travel", zero per-frame CPU
  graph mutation -- not the v4.5.2 camera-fight class), O(1) grid
  picking, render-only-when-needed.

Root-caused + fixed during the live-review iteration (systematic-
debugging; the dev preview is a 0x0 software-WebGL tab so the surface
is locked by GPU-free contract tests + verified on the user's GPU):

- **The "black canvas":** `fwidth()` in a WebGL1 fragment shader
  needs `OES_standard_derivatives`; absent -> the shader fails to
  compile -> an invalid program -> `GL_INVALID_OPERATION` every frame
  -> nothing rasterises. The SDF is now extension-free.
- **Background dust + core-glow were finite world-space SQUARES**
  (a unit quad scaled by a world radius) whose wash had not decayed
  at the quad boundary -> a hard square edge, glow cut off outside.
  Both are now full-viewport clip-space passes that reconstruct the
  world point per pixel -> they fill the view at any zoom/pan and
  fade gracefully with no geometric edge; the core is sized to the
  dense disc (a high node-radius percentile), not the halo-inflated
  maximum.
- **Focus fly hit the wrong place:** `select()` eased the camera to
  the static layout coord while the shader draws every node rotated
  by the advancing rotation angle. The fly now targets the rotated
  point and the rotation freezes while a node is focused (resumes on
  deselect) -- the camera locks exactly on the node.
- **Edges** are graded by world length + zoom (short local filaments
  stay, long cross-disc chords fade so the spiral survives; the
  overview is the clean spiral, zoom-in reveals local structure) and
  tessellated into consistently-bowed quadratic-Bezier curves -> they
  flow with the disc as smooth elegant filaments, not a straight
  pixelated hairball.
- **Labels:** a default global set (every node, degree-prioritised)
  shown whenever the toggle is on, with a per-frame draw budget so
  the full set stays smooth; the labels / edges toggles and the
  node-focus spotlight all work.

## [4.5.3] - 2026-05-17

**Crisp + alive.** User live-review of v4.5.2: "lag web, move lag,
cannot zoom, click node does not zoom, cannot drag, weird layout".
Root-caused via systematic-debugging -- the v4.5.2 "life" was a
perpetual rAF loop that every frame drove `cam.setState()` (it FOUGHT
every user zoom/pan/click -- stomped within 16ms) and `sig.refresh()`
(a full re-render of ~11k star points + ~15.5k curved edges EVERY
frame == the page lag + dead drag). That was the third "living
motion" mechanism to hit a wall (reducer-x ignored -> graph-mutation
pegs -> camera-float fights+lags) -- the systematic-debugging
3-failed-fixes architecture gate. With the user's chosen direction
("cosmic atmosphere + crisp graph") the motion moves entirely OFF the
renderer.

### Fixed
- **The graph renders strictly ON DEMAND.** The camera-float / per-
  frame-refresh rAF loop is deleted: sigma repaints only on a real
  interaction, so an idle nebula costs ZERO and zoom / pan / click-
  center / drag are crisp (the camera is the user's again -- nothing
  stomps it every 16ms).
- **De-mandala'd layout.** The small-component / singleton halo was a
  perfect phyllotaxis lattice that rendered as a too-regular geometric
  "mandala" ring ("placement is not good and lively / weird layout").
  It is now broken by a deterministic, independent-seed radial +
  angular jitter into an irregular organic dust field -- still
  byte-deterministic + bounded (`LAYOUT_VERSION` -> `server-fr-4`, so
  the cache self-invalidates and recomputes once).

### Added
- **Living deep-space atmosphere -- pure CSS, GPU-composited, ZERO JS
  and ZERO graph re-render.** Behind sigma's transparent canvas: a
  slow drifting nebula-gas layer + a parallax starfield twinkling on
  its own beat (`@keyframes nebula-drift` / `nebula-twinkle` /
  `nebula-parallax`, all `translate3d`/opacity -- compositor only;
  frozen under `prefers-reduced-motion`). The "alive" no longer
  touches the renderer.
- **On-demand selected-star pulse.** A single DOM overlay
  (`#nebula-pulse`) parked on the selected node via sigma's own
  `afterRender` event + `graphToViewport` (no rAF of our own); the
  beat/ripple is pure CSS. It tracks the star through a camera fly
  with zero extra rendering.

### Tests
- `test_nebula_renderer.py`: the v4.5.2 living-nebula guards EVOLVED
  to the v4.5.3 contract -- `test_nebula_has_no_camera_fight_or_
  perframe_rerender` (cam.setState / rAF-tick / twinkle clock / phase
  all ABSENT) and `test_nebula_is_alive_via_ondemand_pulse_and_css_
  atmosphere` (afterRender pulse + 3 CSS atmosphere keyframes). New
  `test_halo_is_organic_not_a_phyllotaxis_mandala` in
  `test_graph_layout_server.py` (radius-vs-order inversions prove the
  lattice is broken). Full suite 1233 pass / 2 skip; ruff + format
  clean. Verified end-to-end on the real 11057-node scope: the
  `server-fr-4` cache miss recomputed to 22114 finite positions,
  symmetric + bounded (maxR ~2.6x core, the analytic design bound).

## [4.5.2] - 2026-05-17

**The Nebula comes alive.** User live-review of v4.5.1: the layout
was correct but the render was static + flat -- "only circle shape
and straight edges", "no moving like its actually living", "drag make
all edge disappear", "highlight node only node no edge", "make it
more NEBULA". v4.5.2 makes it a living deep-space nebula WITHOUT
touching the (correct) deterministic server layout.

### Added
- **Living motion.** The whole field gently FLOATS via a bounded
  camera Lissajous around the auto-fit pose (one cheap GPU transform
  per frame -- smooth at any node count) and every star TWINKLES via
  a per-node size pulse on its own golden-angle phase. Architecture
  note: sigma's nodeReducer does NOT render an overridden x/y
  (verified live -- a +400 offset moved nothing), and mutating 11k
  graph positions per frame pegs the main thread (the "1fps" jank);
  so motion is the camera + the one reducer attribute sigma honors
  (size). A single rAF loop (`_startLife`/`_stopLife`), paused when
  the tab is hidden, cancelled on reload/destroy.
- **Cosmic rendering.** Nodes render as soft anti-aliased star
  POINTS (`NodePointProgram`) and edges as gently curved luminous
  FILAMENTS (`EdgeCurveProgram`, per-edge curvature) -- not flat
  circles + straight lines. Both programs are already in the
  vendored sigma bundle (no new dependency).

### Fixed
- **Edges stay while dragging** (`hideEdgesOnMove:false`) -- the
  reported "drag make all edge disappear"; the camera float + twinkle
  also keep the rest of the nebula lively during a drag.
- **Highlight reads on edges too.** Selecting / hovering a node now
  IGNITES its incident filaments (accent glow, thicker, on top) --
  the reported "highlight node only node no edge"; the rest dims to
  cosmic dust (never hidden).

### Tests
- `test_nebula_renderer.py` extended with the living-nebula contract
  (camera float + bounded range, size twinkle, star/filament
  programs, edges-stay-on-drag, edge-ignite, NO per-frame full-graph
  mutation). Full suite green; ruff + format clean.

## [4.5.1] - 2026-05-17

**Nebula render fix: the v4.5.0 sigma layout was a mess; the layout
now computes SERVER-SIDE.** User live-review of the shipped v4.5.0:
overlapping mud, a detached cluster flung away, white background,
white-pill labels, ~1fps jank. Root-caused via systematic-debugging.

### Fixed
- **RC1 (the user's "something off with re-reindex"):** the layout
  cache was renderer-agnostic, so cosmos.gl's old force-sim
  coordinates stayed a permanent cache HIT after the sigma swap and
  the new layout never ran. A `LAYOUT_VERSION` token now participates
  in the cache fingerprint, so a renderer/algorithm change self-
  invalidates the cache.
- **The layout itself.** Client-side ForceAtlas2 (synchronous, then
  the graphology Web Worker, with a circlepack seed) was attempted
  three times and proven non-deterministic + quality-fragile on the
  real 11026-node / 2298-component scope (sync converged; the worker
  exploded the giant component; circlepack alone = structureless
  confetti). Per the systematic-debugging "3 failed fixes => question
  the architecture" rule (user-approved), the layout moved off the
  fragile client.
- **RC6 white background / RC7 white-pill labels:** sigma's
  transparent WebGL canvas now has a guaranteed opaque dark backdrop
  (`.nebula-canvas`), and a dark-themed label/hover drawer replaces
  sigma's default white label pill.

### Added
- `mnemo/ui/graph_layout.py` -- a **deterministic, server-side**
  graph layout: `scipy.sparse.csgraph` components + a numpy/cKDTree
  Fruchterman-Reingold for the giant component (fully converged with
  a cooling schedule, fixed seed => byte-identical/cacheable) + a
  compact bounded phyllotaxis halo for the small components /
  singletons. No new dependency (scipy/numpy already present).
- `/ui/graph-data` kicks a non-blocking background compute keyed by
  `(scope, fingerprint)`; `GET /ui/graph-layout` reports a
  `computing` status so the client polls.
- `test_graph_layout_server.py` -- the server-layout contract.

### Changed
- The browser is now a **pure sigma renderer**: all client-side
  forceatlas2 / Web Worker / circlepack / layout-PUT removed; it
  polls the cache (a clean "computing on the server" state -- no
  jank) then applies the positions and renders. The unused
  graphology standard-library bundle was removed.
- Verified live on the real scope: edges ~0.18x random-pair distance
  (a tight readable network -- every client attempt was >=0.35, the
  mess was >1.0), a core-dominant frame with a tidy bounded halo,
  dark theme + dark labels, zero horizontal overflow at 375/1280,
  the v4.4 responsive shell intact, highlight/select/hover/drag
  working. Renderer guards evolved to the server-layout contract.

## [4.5.0] - 2026-05-17

**Nebula renderer swap: cosmos.gl -> sigma.js v3 + graphology.**
Closes the gotcha-31 / C3-honesty loop -- the graph itself now
delivers a real node-highlight + full interaction. The TOMBSTONE
chapter: cosmos.gl is replaced outright, never re-tuned
(reference_cosmos_gl_nebula).

### Added
- sigma.js v3 + graphology + the graphology standard library
  vendored under `daemon/mnemo/ui/static/vendor/` (pinned
  sigma@3.0.3, graphology@0.26.0, graphology-library@0.8.0 -- no CDN
  runtime dependency, no Node build). base.html prefetches them.
- Real graph node-highlight via sigma's `nodeReducer`/`edgeReducer`:
  the companion's `mnemo_highlight_nodes` / `mnemo_select_node` `_ui`
  sentinels (redispatched by chat.js as `mnemo-highlight-nodes` /
  `mnemo-select-node` document events) now light up the live Nebula
  graph -- spotlit set kept vivid + labelled + enlarged, the rest
  greyed (never hidden), camera frames the set.
- Full interaction: click-select + cite, hover emphasis, node drag,
  animated camera centering, click-empty deselect.
- `test_nebula_renderer.py` -- the TOMBSTONE + sigma-surface guard.

### Changed
- `graph.html` nebula() fully rewritten on graphology + Sigma. The
  3-panel shell, `/ui/graph-data` + server layout-cache contract,
  workspace scoping, file tree, detail panel, and the v4.4 C1.R
  responsive mPanel drawer are all preserved verbatim (minimal blast
  radius). Themed 100% from the C1 tokens + `MNEMO_TYPE_COLORS`.
- Cache MISS layout now runs the graphology FA2 **Web Worker** off
  the main thread (bounded run, then freeze + PUT) instead of a
  synchronous FA2 that froze the tab ~56 s on a large scope. Cache
  HIT still applies the settled positions instantly (no layout runs).
- The cosmos-era DOM label overlay is removed (sigma renders labels
  natively) -- a net simplification.
- `agent_tools.py`: the C3 "do NOT claim a graph-view highlight /
  side panel, not the graph" honesty caveat is reverted to the
  now-true "highlights ON THE LIVE NEBULA GRAPH" (wording matched to
  the actual behavior).
- Resting edge alpha softened (0.5 -> 0.34) so the starfield backdrop
  + node constellation lead on the dense graph.

### Fixed
- Hover/selection now always wins over the bright-set dimming (you
  can hover-preview any node while a set is focused).
- Performance: the per-element reducers are pure module functions
  over a plain non-reactive snapshot (was reading the Alpine
  reactive `this` ~26 k times/refresh + rebuilding a Set per node);
  the bright set is memoized; per-edge colors/endpoints precomputed.

### Tests
- `test_nebula_progressive.py` (cosmos contract) removed; the
  cosmos-renderer guards in `test_v32_live_fixes` /
  `test_nebula_highlight_honesty` / `test_in_page_actions` /
  `test_session_nodes` evolved to the v4.5 truth (contract
  evolution, not weakening). Full suite 1219 passed / 2 skipped.

## [4.4.1] - 2026-05-17

**Patch: v4.4.0 live-review UX fixes -- the responsive chapter,
finished to a professional bar.**

### Fixed
- **Settings rendered the tab strip twice.** `chat_settings.html`
  carried both the shared `_settings_tabs` cross-route strip and a
  redundant in-page Alpine button row. Removed the duplicate; sections
  now follow the strip's `#hash` (syncTabFromHash + hashchange).
- **Topbar information architecture.** Hamburger moved to the LEFT
  (leftmost); the drawer holds only nav + docs; the workspace-switcher
  and notification bell are NOT drawered -- a right-aligned
  `.topbar-actions` group, always directly clickable on every screen.
- **The nav drawer never actually opened.** It was a `position:fixed`
  child of the `backdrop-filter` topbar, so it resolved against the
  63px topbar box -> a broken ~32px strip on click. Dropped
  `backdrop-filter` `< --bp-md` (desktop keeps the glass; there the
  drawer is `display:contents`) so it resolves against the viewport:
  a real full-height panel, anchored LEFT to match the hamburger.
- **Mobile panel toggles** (chat Chats/Cite, nebula Tree/Detail) were
  weird floating pills with an inconsistent glyph. Now a single
  shared **full-width 50/50 segmented control** (active segment
  filled `--accent-soft`), guard-tested deterministically.
- **Companion dock**: the proactive nudge auto-dismisses (12s), never
  coexists with the open panel, click-outside closes the panel first;
  the `×` is an absolute affordance (even padding).
- **Mnem could leave the viewport.** `window.resize` AND
  `ResizeObserver` do not fire on a CDP/preview viewport change; the
  dock is positioned imperatively. Added a `posStyle` render-clamp +
  a cheap watchdog interval -> Mnem returns on-screen within ~0.6s on
  any platform / resize path.
- **Users can copy their own messages** (same `.msg-copy` /
  `copyText()` as the assistant).
- **Node detail fits**: `.page-hero` top-aligned (was floating actions
  mid-description); the v4.3.1 grid-overflow bug class recurred on
  `.node-detail-grid` / `.edge-list` -- both `minmax(0,…)` +
  systematized into the no-overflow guard.

Live-verified screenshot-independently at 375/768/1280: zero
horizontal overflow, desktop pixel-parity. Full suite 1213 passed /
2 skipped, ruff clean.

## [4.4.0] - 2026-05-17

**C1.R Responsive / Adaptive Layout -- responsiveness is now a C1
design-system contract every page inherits, not per-page media-query
hacks. Utilize space on small windows; never overflow; feel great.**

### Added
- **Breakpoint token layer** -- `--bp-sm/md/lg` (40/60/80rem) live
  once in `app.css :root`, exactly like the C1 colour tokens. CSS
  `@media` can't take `var()`, so the rem literal IS the single-source
  contract; a guard test forbids any raw px width media literal (in
  app.css AND inline page `<style>`).
- **`.u-truncate` / `.u-clamp` / `.table-scroll`** shared no-overflow
  primitives (the v4.3.1 audit-overflow lesson, generalized + single-
  sourced); applied to node lists, search/cite, audit, dashboard,
  node detail, the sources table.
- **Adaptive mobile nav drawer** (`navDrawer()`): below `--bp-md` the
  topbar collapses to a hamburger + off-canvas drawer (a11y:
  aria-controls/-expanded, Escape close, focus return; persisted).
- **Collapsible shared session rail**: `CHAT_SURFACES` gains
  `collapse` (dock collapsed-by-default so a growing session list
  never eats the bubble; page unchanged) -- one shared component.
- **Adaptive full-window shells**: the 3-panel `.nebula-shell` /
  chat `.mn` collapse to a single usable pane < `--bp-md` with the
  side panels reachable as drawers (no 277px-sliver pathology).

### Changed
- 15 ad-hoc `@media` literals (1100/980/1080/800/900/1000px)
  consolidated to `--bp-md`; chat shell 1100px and settings 700px
  migrated to tokens. Desktop pixel-parity preserved (live-verified).
- Polish: >= 44px touch targets + C1-token focus rings on the new
  nav-toggle / mpanel-toggle / rail-collapse affordances.

### Fixed
- The v4.3.1 grid-implicit-`auto` overflow bug class recurred on
  `.node-list`, `.query-mini`, `dl.meta` and (as a too-wide floor)
  `.code-project-grid`; the sources `<table>` and the endpoint
  filter also overflowed < `--bp-md`. All fixed and the rule is now
  systematized + guard-tested -- zero horizontal overflow on every
  page at 375/768/1280 (screenshot-independent live-verify).

## [4.3.2] - 2026-05-17

**Patch: retrieval relevance -- a dominant cross-project match is no
longer hidden behind a weaker BASE node.**

### Fixed
- Strict project-isolation HARD-`continue`d every cross/inactive-
  project non-BASE candidate in `retrieve.query()`. A context-less
  `/v1/query` resolves the active workspace's project; if that isn't
  the queried doc's project, the genuinely-best match was *erased* --
  a strict-isolation silent-zero ("the result seems wrong"). Repro:
  `"v4.x contract refactor complete handover"` returned the BASE
  `reference-mnemo-pipelines` (0.53) at #1 while the exact-match v4
  handover (~0.71) was **absent**. Same failure family the v1.2.1 fix
  softened for the symmetric `project_key=None` case.
- Fix: new `Config.project_isolation_penalty` (default **0.85**,
  persisted, auto-tuner-friendly). The hard filter becomes a
  multiplicative score penalty: out-of-scope strict nodes are kept +
  ranked but deprioritized. Tuned on the principle -- a *comparable*
  cross-project node still loses to BASE/in-project, but a
  *dramatically* stronger exact match still wins. Live-verified: the
  v4 handover went **absent -> #1 (0.601)**; the BASE reference
  correctly #2 (0.529).

### Tests
- `test_soft_isolation.py` (knob default + dominant cross-project
  match surfaces #1). `test_edge_cases` isolation test evolved to the
  v4.3.2 contract: v1.2.1 `project_key=None` survival PRESERVED;
  cross-project now SOFT-penalized (present, ranked below in-project/
  cross-cutting) instead of hard-dropped -- a stronger assertion than
  the old "absent". Full suite 1098 unit + 100 integration, ruff
  clean.

## [4.3.1] - 2026-05-17

**Patch: audit hit rows no longer blow out the page width.**

### Fixed
- `/audit-page`: expanding a query's hits could extend the page width
  horizontally instead of truncating. Root cause (not v4.x -- a
  pre-existing latent bug, triggered by a long-description node
  ranking in a logged query): `.query-log` and `.query details
  ul.hits` used implicit `auto` grid columns, which size to
  MAX-CONTENT; the deeply-nested `.hit-desc` has `white-space:nowrap`,
  so a long node description propagated up the grid/flex chain and
  forced the document ~5300px wide. The v2.6.0 `.hit-desc` ellipsis
  never engaged because no ancestor imposed a constrained width. Fix:
  `grid-template-columns: minmax(0, 1fr)` on both audit grids +
  `min-width: 0` on the `.query` / `.hit-row` grid items, so the
  tracks shrink below max-content and the existing
  `overflow:hidden;text-overflow:ellipsis` truncates to fit.
  Live-verified (docSW 5321 -> 1270 at a 1270 viewport; `.hit-desc`
  4663 -> 313px, ellipsis engaged).

### Tests
- `test_design_system_contract.py::test_audit_grids_constrain_long_content`
  -- a grep guard (mirrors the C1 contract-test style) so the
  implicit-`auto`-grid blowout cannot be reintroduced silently.

## [4.3.0] - 2026-05-17

**C3 Chat Surface contract + the feature backlog.** Final v4.x
contract. The chat page and the companion dock now render from ONE
declared capability matrix + four shared Jinja partials -- a
capability is written once and a surface opts in, instead of being
re-implemented or silently missing. Closes the v4.x contract-pattern
refactor (C1 design-system, C2 provider registry, C4 settings, C3
chat surface).

### Added
- `mnemo/ui/chat_surface.py` `CHAT_SURFACES` capability matrix
  (registered as a Jinja global, mirrors `palette.py`) +
  `test_chat_surface_contract.py` guard test -- a surface must DECLARE
  a capability, never silently omit it.
- Shared partials `_chat_rail.html` / `_chat_bookmarks.html` /
  `_chat_examples.html` / `_chat_composer.html`, included by BOTH
  surfaces, matrix-gated. The dock GAINED: conversation
  switch/back/new, inline rename, bookmark strip + per-turn star,
  welcome + suggested questions (it silently lacked all of these
  though `mnemoChat()` already shared the logic).
- `renameConversation()` in the factory + inline-edit affordance
  (backend `PATCH /v1/chat/{id}` was already complete in v3.2).

### Fixed
- **1 message at a time**: an eager `sending` flag (set synchronously
  before the async `newConversation()` await window) hides the
  welcome/examples the instant a suggested question is clicked, so it
  can't coexist with the outgoing message.
- **Send arrow now optically centered**: the path was bottom-heavy
  (tiny arrowhead + long stem); rebalanced to `M12 18V6M6 12l6-6 6 6`
  (getBBox centroid exactly (12,12) = viewBox centre; live-verified
  <=1px). A path-geometry fix, not CSS; single-sourced in the composer.
- **Honest nebula-highlight wording**: `mnemo_highlight_nodes` no
  longer claims it lit nodes "on the live Nebula graph" (a closed
  cosmos ceiling, gotcha 31) -- it now says it surfaces them in the
  chat side panel and tells the model never to claim a graph-view
  highlight. WORDING ONLY; zero renderer change.

### Tests
- +13 C3 tests; full suite 1195 pass / 2 skip, ruff clean. v3.x tests
  that asserted markup now in a shared partial were evolved to assert
  the partial + inclusion (contract-evolution; capability preserved +
  on both surfaces).

## [4.2.0] - 2026-05-17

**C4 Settings / Config Contract.** Third of the v4.x contract refactor.
The provider/key settings UI is now **registry-driven** (consumes the
C2 `GET /v1/providers`): every registered provider appears
automatically with a real model picker, a read-only key-resolution
tier, and a delete-key action -- plus one unified settings IA. The
v1.2 retrieval route/context is **NOT merged** (gotcha 9): its 3
regression guards stay green unmodified.

### Added
- `keys.resolve_api_key_tier()` (read-only mirror of the
  resolve_api_key ladder: env / dotenv / keychain / file) +
  `keys.delete_api_key()` (keychain + plaintext, no-op if absent).
- `DELETE /v1/settings/providers/{name}/key`; `key_tier` per provider
  in `GET /v1/settings` (never the secret).
- `_settings_tabs.html` -- one shared cross-route settings tab strip
  (`.settings-tabs` single-sourced in app.css per C1), included by
  both `/settings` and `/settings/chat`; #hash deep-links preselect
  the chat page's inner tab.

### Changed
- The provider tab fetches `GET /v1/providers`: every registered
  provider appears (not just configured); the model is a `<select>`
  from the descriptor's `known_models` (was free text); the resolved
  key tier is shown read-only with a Remove-key button.

### Tests
- New `test_keys_tier.py` (6) + 5 `test_settings.py` cases incl.
  `test_gotcha9_regression_guards_unmodified`. Full suite 1182 pass /
  2 skip, ruff clean. The 3 gotcha-9 retrieval-route guards
  (`test_existing_retrieval_settings_still_renders`,
  `test_chat_settings_page_renders_three_tabs`, `test_ui` /settings)
  green UNMODIFIED. Live-verified: registry-driven tab + unified strip.

## [4.1.0] - 2026-05-17

**C2 Provider Contract (registry).** Second of the v4.x contract
refactor. **Behaviour-preserving** -- every existing provider /
compaction / keys / config test passes unchanged; no model default
changed. Adding a provider is now **one `register_provider(...)` call
+ one `stream()` impl class** instead of editing 5 files.

### Added
- `ProviderDescriptor` dataclass + `PROVIDERS` registry +
  `register_provider()` in `mnemo.providers`, mirroring
  `agent_tools.ToolSpec`/`TOOLS`/`_register` exactly (validating
  registrar, raises on duplicate).
- The four providers (anthropic/openai/google/ollama) self-register
  their descriptors at import (register-at-bottom pattern; no circular
  import).
- `GET /v1/providers` exposes the registry (name, display_name,
  env_var, requires_key, default_model, known_models,
  supports_compaction_models) -- no key material. Feeds the C4
  settings UI (v4.2).

### Changed
- `get_provider` derives from `PROVIDERS` (was a hand-edited if/elif
  chain) -- a runtime-registered provider is now constructible.
- `DEFAULT_MODELS`, `keys.ENV_VAR`, `Config.providers` default, and
  `compaction.supports_native_compaction` all DERIVE from the
  registry; the 4 scattered hand-maintained capability tables
  (including `compaction.NATIVE_COMPACTION`) are gone.

### Tests
- New `test_provider_registry.py` (6) + `test_api_providers.py` (1),
  including a `test_no_duplicate_capability_tables` static guard (the
  contract's teeth -- proves single-sourcing, not value coincidence).
  Full suite 1171 pass / 2 skip, ruff clean. Every pre-existing
  provider/compaction/keys/config test green UNMODIFIED.

## [4.0.0] - 2026-05-17

**C1 Design-System / Page-Shell contract.** First of the v4.x
contract-pattern refactor (design: `docs/plans/2026-05-16-mnemo-v4-
design.md`). A pure refactor -- **zero behaviour change, every page
pixel-identical** (live-verified with geometry probes) -- that gives
the UI a contract backbone so the class of layout bug that cost the
multi-round v3.2 `/chat` saga (gotcha 35) cannot be reintroduced
silently.

### Added
- **Token layer** in `app.css :root`: `--topbar-h`, `--content-max`,
  `--page-pad`, `--radius-pill`, `--accent-fg`, `--warn-fg`,
  `--measure*` -- every primitive value lives in exactly one place
  (mirrors the proven `palette.py` single-source model).
- **Page-shell contract** with two documented modes (centered vs
  full-window) in `docs/architecture.md`.
- **Guard test** `daemon/tests/unit/test_design_system_contract.py`
  (template-grep, mirrors `test_nebula_progressive`): forbids raw
  `65px`/`1600px`/`999px`/`#06201e`/`#1a0f0c` outside `:root`,
  `html,body`/`body>main` scoping in any page, a nested second
  `<main>`, and re-defining a shared primitive. This is the contract's
  teeth.

### Changed
- `app.css` + `chat.html`/`base.html` now consume the tokens; zero raw
  literals outside `:root`. Computed values byte-identical.
- Shared primitives (`.mnem-working`, `.load-older`/`.lo-pill`/
  `.lo-dot`, `@keyframes mnem-bob`, canonical `.link-button`,
  `.btn-pill`) single-sourced in `app.css`; removed the divergent
  duplicates from `chat.html`/`base.html`.

### Fixed
- The companion dock's "working" dots were 5px and its `@keyframes
  mnem-bob`/`.lo-dot` were undefined off `/chat` (silently dead
  animation). The dock now uses the canonical 6px primitive on every
  page (the intended de-divergence of single-sourcing).

### Tests
- v3.1/v3.2 chat tests evolved from the raw `calc(100vh - 65px)`
  literal to `calc(100vh - var(--topbar-h))` (same contract-evolution
  as v3.2 itself evolved them from `100dvh`; computed height
  byte-identical). Full suite green; ruff clean.

## [3.2.0] - 2026-05-16

**Two critical fixes + the companion's page-aware tools.** v3.2 began
as "the agentic page companion"; a long live review uncovered two
serious pre-existing defects this release now fixes.

- **FIX: the broken Nebula is restored.** `main` (and the published
  v3.1.0) shipped the **v2.6.8 perfectionize renderer**, which froze
  on init (no motion, wrong placement, pegged the CPU). The documented
  v2.6.6 revert (`a341c89`) had **never merged to main** (PR #53 was an
  empty no-op). v3.2.0 restores the kept-good v2.6.6 nebula verbatim
  (`graph.html` @ `8caf257`) -- live-verified: 11k nodes in perpetual
  organic motion, no freeze. A livelier/agentic nebula is deferred to a
  future renderer swap (never via cosmos config/wiring -- a documented
  ceiling, reference_cosmos_gl_nebula.md).
- **FIX: the daemon can be stopped again.** The pid file was a single
  shared path regardless of port, and `remove_pid_file()` unlinked
  unconditionally -- a second daemon (e.g. a preview on :7399) exiting
  orphaned the live one, so `mnemo daemon stop/status/restart` went
  blind and a stale process kept serving old code. The pid file is now
  **port-scoped** (`mnemo-<port>.pid`) and removal is **ownership-
  guarded**; `status`/`stop`/`restart` gained `--port`.
- **Live page-context.** New safe `mnemo_page_context` tool +
  `window.mnemoPageContext()` (base default `{page,path}`; Settings /
  node-detail overrides carry weights/k / selected node). `mnemoChat()`
  PATCHes it onto the conversation before every run so the companion
  grounds on the current screen. (The Nebula override was reverted with
  the renderer -- it never touches cosmos.)
- **Context-aware citations.** Inline `[mnemo:id]` is no longer a blind
  redirect: `window.mnemoCite`/`mnemoCitePopover` (base.html) render a
  shared inline node preview via the existing marked/Prism pipeline;
  full-page `/node/<id>` is the last-resort fallback only.
- **Session + retune tools.** Safe `mnemo_session_nodes` (the
  conversation's cited/used nodes + 1-hop neighbours) and confirm
  `mnemo_highlight_nodes` / `mnemo_apply_retune` (bounded, recoverable
  scoring-weight apply with a Settings before/after validate). These
  are server-side tools; none touch the cosmos renderer.
- **Dock + navigate fixes.** The dock user bubble no longer renders as
  a giant box wrapping the real one (the turn wrapper's role class
  collided with `.mc-user`). `mnemo_navigate` no longer hard-reloads
  when you're already on the target page (which killed the dock SSE +
  the in-flight run); the model is guided to prefer in-page tools and
  treat navigate as terminal.
- **FIX: a brick-the-conversation 400 self-heals.** An interrupted run
  (SSE/daemon killed mid tool-dispatch) left an assistant `tool_use`
  with no persisted `tool_result`, so history replay 400'd on every
  subsequent message forever (`tool_use ids ... without tool_result`).
  History reconstruction now synthesizes an error `tool_result` for
  any orphaned `tool_use` -- the conversation recovers and Retry works.
- **Chat UX completion.** Both `/chat` and the dock gain a clean
  in-thread error banner + one-click **Retry** (replacing the raw
  provider-JSON dump), a **copy-message** button, a **scroll-to-latest**
  pill, a proper circular icon **send** button (the dock's bare `→` is
  gone), and long-text hardening so a long message never smears
  horizontally.
- **Chat UX refinement (Claude-grade).** The thread is now
  **bottom-anchored** -- a short conversation sits just above the
  composer instead of pinned at the top with a huge empty void.
  **Delete a conversation** (rail) and **start a new one from the
  dock** header. `mnemo_navigate` opens the target in a **new tab** so
  "show me in nebula" no longer drops the connection mid-answer. Inline
  `[mnemo:id]` on `/chat` uses the existing citation **side panel**
  (no floating popover overlapping the thread). `scrollbar-gutter:
  stable` stops the "Latest" pill/content jittering on scroll; the send
  icon is truly centred; the thinking indicator fades in/out instead of
  popping; the Nebula close button + citation-panel typography refined.
- **Chat UX hardening (live-verified).** Fixed a regression where
  clicking an inline `[mnemo:id]` did nothing: the `mnemo-cite`
  CustomEvent is dispatched on `document` but was listened for on
  `window` (a non-bubbling document event never reaches a window
  listener) -- now `bubbles:true` + an `@mnemo-cite.document` listener.
  This also restored code/other-type citation previews (the renderer
  was fine; the click never reached it). `/chat` is now a true
  **single window** (page-scoped `html,body` overflow lock + the
  correct 64px topbar offset -- the old calc used a wrong 116px, which
  was the void/scroll; not `position:fixed`, which a base.html
  ancestor transform broke). The Mnem side now shares **one consistent
  left gutter** -- assistant prose, tool calls, and tool results align
  to the exact same x (was ~11px off). Verified end-to-end via the
  preview with a real Anthropic turn: "show me in nebula" opens
  `/graph` in a new tab with the conversation intact (no "connection
  dropped").
- **Chat layout, fully root-caused (supersedes the item above).** The
  earlier "single window" attempt used a page-scoped `html,body`
  overflow lock + zeroed `body>main`, which desynced `/chat`'s chrome
  from every other page. Reverted to the **app-standard full-window
  convention** the Nebula page uses (`{% block layout %}` +
  `<main class="full">` + `calc(100vh - 65px)`; no global overrides).
  Then the real "messages shrink / aren't left aligned" cause was
  found by measuring the user's *exact* conversation: the centre
  column was a **second `<main>`** (`<main class="chat-thread">`
  nested in `<main class="full">`) that inherited app.css
  `main{max-width:1600px;margin:2rem auto}` -- as a grid item the
  auto inline margins made it shrink-to-fit + centre instead of
  filling its cell. Now a plain `<div>`; the message body **fills the
  thread** (the 46rem reading cap is removed -- it had left the body
  at ~53% of a wide screen with a large dead gap next to the
  citations panel), **left-flush** by the rail, with assistant prose,
  tool calls, and the user/composer all sharing one column.
  Cited **code in the side panel now wraps** instead of clipping
  (Prism forces the inner `<code>` to `white-space:pre`; an
  `!important` rule overrides the vendored sheet). All verified live
  with hard geometry numbers (prose 277->1270px, no overflow).

## [3.1.0] - 2026-05-16

**Mnem becomes a real companion.** v3.1 closes every gap from the
live review of v3.0 (`docs/plans/2026-05-15-mnemo-v3.1-companion-design.md`).

- **One chat component, two surfaces.** The `/chat` page and the
  companion dock now share a single `mnemoChat()` factory
  (`static/chat.js`) -- no duplicated logic. The dock is a full,
  draggable, edge-snapping mini-chat (compose + stream + tool chips +
  inline permission), hidden on `/chat`, position persisted in
  `localStorage`.
- **Claude-grade streaming + rendering.** SSE text is word-smoothed
  through `mnemoStreamText` with a real "working" animation; the
  thread is a fixed bottom-pinned viewport that lazy-loads older turns
  on scroll-up. Assistant messages and cited-node previews render
  through the real marked + DOMPurify pipeline (headings / lists /
  tables / fenced code), not a toy parser.
- **Hybrid conversation compaction.** Anthropic compaction-capable
  models use the `compact-2026-01-12` beta (full content preserved);
  every other provider/model summarizes the oldest turns into a
  pinned context message. Model context is bounded by compaction; UI
  history is paginated separately.
- **Token + bookmark surfaces.** Providers surface per-turn token
  usage; a running budget bar (vs the per-provider cap) + per-turn
  counts; server-persisted bookmarks with a star per turn and a
  jump strip. New paginated `GET /v1/chat/<id>/messages` +
  `/v1/chat/<id>/bookmarks` CRUD.
- **Agent can use skills.** `mnemo_list_skills` / `mnemo_run_skill`
  load a `skills/<name>/SKILL.md` guide into the run.
- **Branding.** New simplified Mnem mark as the favicon + nav logo.
- **Fixes from the live review:** invalid-XML SVG comment (blank
  logo), native image drag-and-drop hijacking the dock drag, the
  stalling cited-node preview, and asset cache-busting so every
  static fix actually reaches browsers on the version change.

## [3.0.0] - 2026-05-15

**Mnem - the agentic companion.** v3 turns mnemo into an agent with
tools over its own memory + code graph, not a pre-canned RAG box.

- **Agent loop + 4 providers** behind one abstraction (Anthropic,
  OpenAI, Google, Ollama) with a shared `(text_delta|tool_call|stop)`
  event contract. BYO API keys: env var > repo `.env` > OS keychain >
  plaintext 0600 fallback (never in `settings.json`).
- **Tool surface, two consumers.** One `agent_tools.TOOLS` registry
  feeds the internal loop and an MCP server (`mnemo mcp`, stdio) so
  Cursor / Claude Desktop / Codex / Windsurf get mnemo for free. 6
  safe read tools + 9 write/danger tools + 5 client-side UI
  directives, each risk-tagged.
- **Permission protocol.** `confirm`/`danger` tools emit a
  `permission_request`; the loop pauses on `POST /v1/chat/<id>/permit`;
  `allow_always` persists per-project to `chat_permissions`. `danger`
  never offers always.
- **Conversations are first-class** SQLite rows (`chat_conversations`
  / `chat_messages` / `chat_permissions`); 9 `/v1/chat/*` REST
  endpoints + an SSE event stream.
- **/chat page** (3-column shell + streaming + citation panel),
  **Mnem companion dock** on every page (5 CSS mood states, opt-in
  proactive nudges), **doc-helper** (` ```mnemo-draft ` fences ->
  one-click Save as memory) + the `mnemo:doc` skill, **companion
  settings** at `/settings/chat` (the v1.2 retrieval-tuning
  `/settings` page is untouched).

12 phases, ~140 new unit tests, full suite green; live-smoked against
a real Anthropic key.

## [2.6.0] - 2026-05-14

**Workspaces + indexing safeguards.** Named workspaces replace the
implicit single-active-project pointer with a switchable bundle of
``project_keys`` + filter prefs. With no active workspace the UI
drops into BASE-only mode -- every page filters to BASE-flagged
nodes. Indexing safeguards classify each file before parsing:
oversize / unsupported / suspicious files surface in a three-section
reindex report where the user picks ``always_skip`` / ``always_keep``
/ ``retry`` per file. v2.6 unblocks v3 chat -- the v3 retrieval scope
reads from the active workspace abstraction this release introduces.

### Added (schema)

- ``workspaces`` table -- user-named bundles of ``project_keys`` +
  filter_prefs + page_state. Time columns use epoch milliseconds
  (matching JS Date.now()) so the UI consumes them directly.
- ``workspace_state`` -- singleton row holding the active pointer.
  FK ON DELETE SET NULL automatically clears the pointer when the
  active workspace is deleted.
- ``source_overrides`` -- per-path ``always_skip`` / ``always_keep``
  / ``retry`` decisions from the reindex report. Reindex consults
  this table before classifying so user decisions persist across
  runs.

All three tables are additive -- existing v2.5.1 databases reopen
cleanly via ``CREATE TABLE IF NOT EXISTS``.

### Added (modules)

- ``mnemo.workspaces`` -- public CRUD + activation helpers.
  ``create_workspace`` / ``list_workspaces`` / ``set_active_workspace``
  / ``upsert_source_override`` / ``batch_upsert_source_overrides``.
- ``mnemo.safeguards`` -- ``classify_file`` pipeline + seven pure
  heuristics: ``is_binary_masquerading_as_text``,
  ``looks_like_secret``, ``has_autogenerated_header``,
  ``is_lock_minified_or_snapshot``, ``is_high_entropy_blob``,
  ``has_repeated_line_bloat``, ``is_build_output_name``.
- ``mnemo.auto_router.propose_source`` -- dual-source proposal for
  the add-source UI. Walks a path once, emits zero-to-two
  SourceProposals (docs_dir if >= 3 docs, code_repo if >= 10
  source files; both fire for mixed repos), merges nested
  .gitignores into the source's exclude field, and warns on
  large projects.

### Added (REST API)

```
GET    /v1/workspaces                  list (active first)
POST   /v1/workspaces                  create; 400 on duplicate name
GET    /v1/workspaces/active           read active pointer
POST   /v1/workspaces/clear            enter BASE-only mode
GET    /v1/workspaces/<id>             read one
PATCH  /v1/workspaces/<id>             patch
DELETE /v1/workspaces/<id>             delete; FK SET NULL clears active
POST   /v1/workspaces/<id>/activate    activate; 409 over hard cap
POST   /v1/sources/propose             dual-source proposal
GET    /v1/source_overrides            list
POST   /v1/source_overrides            batch upsert
DELETE /v1/source_overrides            delete one
GET    /v1/reindex/report              most recent three-section report
GET    /v1/events                      SSE broadcast channel
```

The ``/v1/events`` channel pushes ``workspace_activated`` /
``workspace_deleted`` / ``workspace_cleared`` / ``reindex_started``
/ ``reindex_done`` frames so every open browser tab reflects state
changes without polling. Workspace activation enforces configurable
soft (75k default) and hard (200k default) node-count caps; hard-cap
violations return 409 ``workspace_too_large`` with per-project node
counts so the UI can render a "remove a project" modal.

### Added (reindex pipeline)

``reindex_events`` grows two new event types:

- ``('classified', {idx, path, category, reason, override_applied})``
  per source-walked file. category in
  ``'indexed' | 'auto_skipped' | 'malformed' | 'suspicious'``.
- ``('report', {auto_skipped, malformed, suspicious, indexed_count,
  duration_ms})`` once before ``done``.

``source_overrides`` are consulted before classification:
``always_skip`` short-circuits to auto_skipped, ``always_keep``
bypasses heuristics and forces an indexed verdict. Parse errors
now produce per-file ``malformed`` entries instead of failing the
whole source.

### Added (UI)

- Top-bar workspace switcher (named Alpine factory
  ``window.workspaceSwitcher``) in ``base.html`` -- pill + dropdown
  with Active + Recent sections, "+ New workspace" + "Manage
  workspaces..." + "No workspace (BASE-only mode)". Subscribes to
  ``/v1/events`` so multi-tab state stays in sync.
- ``/workspaces`` management page (``window.workspacesPage``) --
  grid of dash-cards with per-card Activate / Duplicate / Delete
  actions + inline New workspace form.
- Reindex report modal in ``sources.html`` -- "View report" button
  appears after a reindex with non-empty buckets; modal shows the
  three sections + always_skip / always_keep / retry buttons per
  file; Apply posts the batch to ``/v1/source_overrides``.

### Theme + coding-pattern alignment

Every new surface honors the v2.6 design's theme requirements:
named Alpine factories with NO double-init (per
``feedback_alpine_double_init.md``), x-cloak on every show-state,
reuse of existing CSS variables (--accent / --panel-2 / --warn / ...)
with NO new color literals, ``.dash-card`` shells for the workspaces
page, ``mnemoStaggeredReveal`` available for any future stagger
needs, ``prefers-reduced-motion`` honored on the modal backdrop.

### Changed (UX polish)

- **Workspaces fully own retrieval scope.** The legacy active-project
  pill is removed from the top bar; ``/v1/query`` derives the
  effective ``project_key`` from the active workspace's first
  project_key (with explicit body / legacy field / persisted-active
  fallbacks for back-compat). The ``/v1/projects/active`` endpoints
  stay alive for CLI consumers.
- **Workspace switcher right-aligned.** The pill moved from beside
  the brand to after the nav links so the "current scope"
  affordance lands in the same slot users already train their eyes
  on (where the legacy active-project pill used to live). Dropdown
  flipped to ``right: 0`` anchoring so it stays inside the viewport
  on narrow windows.
- **Workspace add UX** in both the switcher dropdown and the
  ``/workspaces`` page replaces the comma-separated text field with
  a chip-based picker. The single input auto-completes against
  ``/v1/fs/suggest`` (filesystem paths, resolved via
  ``/v1/projects/resolve``) and ``/v1/projects/known`` (canonical
  project_keys with node counts). Each pick lands as a removable
  chip; Backspace on empty input pops the last chip; Enter on a
  bare path round-trips through resolve.
- **Audit page hit rows** now show score -> name -> type badge ->
  description -> truncated ID instead of just the first 12 chars
  of the bare node ID. Nodes removed by a reindex resolve to a
  strike-through "[removed]" badge so the row still reads.
- **Lexical scorer reads the body.** ``_lexical_score`` previously
  walked only ``name + description``; a long handover whose BODY
  contained every distinctive query term lost to a popular short
  doc with zero keyword overlap because the graph-edge boost
  dominated the small gap. The haystack now includes the first
  32 KB of the body. Live verification: a query for the v2.6
  handover terms now ranks the v2.6 handover docs first instead
  of a tangential ``feedback-alpine-double-init`` hit.
- **Workspace switcher: clear capWarning** when the active workspace
  disappears (cleared, deleted, FK-SET-NULL'd). The "!" soft-cap
  indicator no longer lies about a workspace that no longer exists.

### Test coverage

- 33 unit tests for workspaces CRUD (schema migration, name
  uniqueness, FK SET NULL, source_overrides upsert / batch).
- 45 unit tests for safeguards (each heuristic positive / negative
  / edge + classify_file ordering).
- 10 unit tests for the new reindex events (classified + report)
  + override integration.
- 16 unit tests for propose_source (mixed / docs-only / code-only
  + gitignore parsing + warnings).
- 20 TestClient tests for /v1/workspaces/* + /v1/sources/propose
  + /v1/events broadcast.
- 11 TestClient tests for /v1/source_overrides + /v1/reindex/report.
- 12 + 8 + 7 surface tests for the three UI affordances.
- 9 unit tests for the v2.6 phase 10.1 ``_resolve_query_project``
  precedence + pill removal.
- 10 surface tests for the v2.6 phase 10.2 chip-based add UX.

Total: 153 new unit tests on top of the v2.5.1 baseline.

## [2.5.1] - 2026-05-14

**Go Tier 1 + Tier 2.** Closes the v2-deferred sweep -- v2.5.0
added JS + TS, this release closes Go. Every bundled language
now reaches Python parity in the structural extractor.

### Added (Go structural extractor)

New ``_extract_go`` in ``daemon/mnemo/parsers/code.py`` registered
against ``go``. Detects:

  - ``func foo() {}``               -> ``code_function``
  - ``func (r *Foo) m() {}``        -> ``code_method`` with
                                        ``parent_source_path`` ->
                                        the ``Foo`` ``code_class``
  - ``func (s Foo) m() {}``         -> same shape (value receiver
                                        vs pointer; both produce
                                        a parented method)
  - ``type Foo struct { ... }``     -> ``code_class``
  - ``type Foo interface { ... }``  -> ``code_class``

Go has no classes per se, but structs + their receiver-methods
are the natural class-analogue. Using ``code_class`` keeps the
schema consistent across languages so cross-stack queries can
join code_method -> code_class regardless of source language.

A two-pass walk handles the receiver-resolution edge case
where a method declaration appears in the file BEFORE its
receiver type's ``type_declaration``: pass 1 collects all type
declarations into ``type_units_by_name``, pass 2 walks methods
and looks up the parent.

### Added (Go imports)

``_extract_go_imports`` recognizes both shapes:

  - ``import "fmt"``                -> ``fmt``
  - ``import ( "fmt"; "os" )``      -> ``fmt`` + ``os``
  - ``import alias "pkg/path"``     -> ``pkg/path`` (path, not alias)
  - ``import _ "pkg"`` (blank)      -> the path
  - ``import . "pkg"`` (dot)        -> the path

The DFS walker handles both ``import_declaration -> import_spec``
(single) and ``import_declaration -> import_spec_list -> N
import_spec`` (grouped).

### Added (Go call sites for Tier 2)

``_go_call_sites`` walks function / method bodies for
``call_expression`` nodes, skipping nested ``func_literal`` /
``function_declaration`` / ``method_declaration`` so they
don't pollute the enclosing function's call_sites:

  - ``foo()``        -> ``CallSite(callee='foo', receiver=None)``
  - ``pkg.Func()``   -> ``CallSite(callee='Func', receiver='pkg')``
  - ``obj.method()`` -> same shape as ``pkg.Func()`` (we can't
                        distinguish package call from method call
                        without semantic analysis; the Tier 2
                        resolver handles both via imports + same-
                        module fallback)

### v2-deferred sweep COMPLETE

With this release the v2 deferred-work backlog is closed:

| Release | What | Status |
|---|---|---|
| v2.3.0 | git-log ingestion + provenance edges (phase 9) | shipped |
| v2.4.0 | Django framework extractor (phase 8) | shipped |
| v2.5.0 | JavaScript + TypeScript Tier 1 + Tier 2 | shipped |
| v2.5.1 | Go Tier 1 + Tier 2 | THIS RELEASE |

Next big chapter: **v3 AI chatbot/companion**.

### Tests

- ``daemon/tests/unit/test_parsers_code_go.py`` (10 new cases):
  function declaration, struct + interface -> code_class,
  pointer + value receiver methods with parent resolution,
  simple + grouped imports (incl. aliased), free + package
  call sites, registry contains ``go`` key.
- Total daemon suite: 706 passing (was 696 + 10 new).

## [2.5.0] - 2026-05-14

**JavaScript + TypeScript Tier 1 + Tier 2.** Closes a long-standing
v2.0 gap: until this release, JS / TS files only got a
``code_module`` node -- no declarations, no imports, no call sites
for the Tier 2 resolver. v2.5.0 wires structural extraction for
both languages.

### Added (JS/TS structural extractor)

New extractor ``_extract_jsts`` in ``daemon/mnemo/parsers/code.py``
registered against ``javascript``, ``typescript``, AND ``tsx``.
Detects:

  - ``function foo() {}`` -> ``code_function``
  - ``class Foo { ... }`` -> ``code_class``
  - ``method_definition`` inside a class -> ``code_method`` with
    ``parent_source_path`` pointing at the class
  - ``const f = () => {}`` / ``const f = function() {}`` (modern
    arrow-function module-level idiom) -> ``code_function`` named
    after the variable
  - ``export function foo() {}`` -> same as ``function foo() {}``
    (export wrapper is transparently unwrapped)

TypeScript-specific syntax (parameter / return type annotations,
``interface_declaration``, ``type_alias_declaration``, ``enum_declaration``)
is silently ignored -- the underlying declaration nodes are the
same shape as JS, so one shared extractor handles both. TSX
inherits the TS extractor; the React framework extractor in
``daemon/mnemo/extractors/react.py`` continues to add component-
level units on top.

### Added (JS/TS imports)

``_extract_jsts_imports`` recognizes the standard ECMAScript
``import`` shapes:

  - ``import x from 'mod'``       -> ``mod``
  - ``import { a, b } from 'mod'`` -> ``mod`` (NOT a / b)
  - ``import * as ns from 'mod'`` -> ``mod``
  - ``import 'mod'`` (side-effect) -> ``mod``

Dynamic ``import('mod')`` and CommonJS ``require('mod')`` are
NOT captured yet -- the import edge model targets module-level
declarative dependencies that are stable across runs. Lands in a
later cut alongside additional resolver heuristics.

### Added (JS/TS call sites for Tier 2)

``_jsts_call_sites`` walks each function / method body collecting
``call_expression`` nodes. Two shapes recorded:

  - ``foo()`` -> ``CallSite(callee_name='foo', receiver=None)``
  - ``a.b()`` -> ``CallSite(callee_name='b', receiver='a')``

The existing language-agnostic ``scope_resolver.resolve_calls``
(v2.0 phase 5) consumes these and emits ``calls`` edges -- so JS
/ TS files now contribute to the Tier 2 graph alongside Python.

### Deferred

- **Go Tier 1 + Tier 2.** Go's grammar (receivers, packages, no
  classes, different import shape) needs its own pass. Tracked as
  v2.5.1 in the handover.
- **CommonJS `require()` imports** + **dynamic `import()` calls.**
  Less common in modern codebases; the static ``import`` shape
  covers the majority of cross-module dependencies.
- **Cross-file resolver heuristics specific to JS/TS** (npm package
  receivers, JSX `<Component />` call sites). The general resolver
  works on the shapes we emit; framework extractors layer on top.

### Tests

- ``daemon/tests/unit/test_parsers_code_jsts.py`` (11 new cases):
  function / class / method / arrow-function declarations,
  default + named imports, free + member call sites,
  TS-with-type-annotations function + class + imports, registry
  contains JS + TS keys.
- Total daemon suite: 696 passing (was 685 + 11 new).

## [2.4.0] - 2026-05-14

**Phase 8: Django framework extractor.** The last backend framework
extractor from v2.0's Tier 3 batch lands. ``code_route`` nodes now
appear for Django URL configurations alongside FastAPI / Flask /
Express / Next.js.

### Added (Django extractor)

New module ``daemon/mnemo/extractors/django.py`` detects:

- ``urlpatterns = [...]`` module-level assignments
- ``path("url/", view, name="...")`` calls inside that list
- ``re_path(r"^pattern$", view)`` regex variants
- Class-based views: ``ClassName.as_view()`` -- the receiver
  identifier is recorded as the handler name

One ``code_route`` :class:`CodeUnit` is emitted per ``path()`` /
``re_path()`` call. Method is recorded as ``*`` because Django views
implement HTTP-method dispatch themselves rather than declaring it
at the URL layer (unlike Flask / FastAPI).

Anchoring on ``urlpatterns = [...]`` membership prevents false
positives from helper modules that happen to call ``path()`` for
non-routing purposes.

### Registered in dispatch

``daemon/mnemo/extractors/__init__.py`` adds ``_django.extract`` to
``FRAMEWORK_EXTRACTORS["python"]`` alongside ``_fastapi.extract``
and ``_flask.extract``. A single Python tree can legally match any
or all three (mixed frameworks are rare but the dispatch supports
them).

### Cross-file handler resolution

Django views typically live in a different file than ``urls.py``,
so the same-file handler index inside the extractor is best-effort.
The route's ``description`` carries the human-readable view name
(``Django URL <pattern> -> <view_name>``); cross-file
``handler_source_path`` resolution happens at the post-pass layer
that already wires ``routes_to`` for FastAPI / Flask routes.

### Tests

- ``daemon/tests/unit/test_extractors.py`` -- 7 new cases for
  Django:
  - ``path()`` in ``urlpatterns`` emits a route (method = ``*``)
  - ``re_path()`` regex pattern preserved in ``route_path``
  - Class-based view ``X.as_view()`` records the class name
  - Multiple paths in one list produce N routes
  - ``path()`` outside ``urlpatterns`` is ignored
  - Non-Django Python files emit no routes (no false positives)
  - ``_django.extract`` is registered in
    ``FRAMEWORK_EXTRACTORS["python"]``
- Total daemon suite: 685 passing (was 678; +7 new).

## [2.3.0] - 2026-05-14

**Phase 9: git-log ingestion + decision-provenance edges.** The
v2.0 design's headline differentiator finally lands: every
``code_repo`` source now walks its git history and creates one
``commit`` node per commit, plus three families of provenance
edges that auto-link memory + code + commits.

### Added (commit-history ingestion)

New module ``daemon/mnemo/git_log.py`` walks ``git log`` per
``code_repo`` source (capped at most-recent 10k commits per repo
via ``git_log.DEFAULT_COMMIT_LIMIT``). For each commit it emits a
``commit`` node carrying:

  - ``name``  = ``<short_sha> <subject>``
  - ``description`` = author + ts + subject
  - ``body``  = subject + full body
  - ``source_path`` = ``<repo_path>@<full_sha>`` (idempotency key)
  - ``frontmatter`` JSON = ``{ sha, short_sha, author_email, ts,
    files_changed }`` for downstream sort + filter queries.

A new post-pass in ``ingest.reindex_events`` calls
``_ingest_git_log_for_source`` AFTER the existing code-edge
resolver so the freshly-upserted code_function / code_method /
code_module nodes are available to join against.

### Added (decision-provenance edges)

Three edge families wire from commits to the rest of the graph:

| Edge | From | To | Confidence | How |
|---|---|---|---|---|
| ``references_function`` | commit | code_function / code_method / code_module | proportional, clamped [0.3, 1.0] | per-commit diff hunks (``git show --unified=0 --no-prefix``) joined against each code node's [start, end] line range. Even a one-line typo fix carries 0.3 weight; a full rewrite caps at 1.0. |
| ``closed_by`` | memory_feedback / plan_doc / memory_project | commit | 1.0 | ``Fixes:`` / ``Closes:`` / ``Refs:`` trailers in the commit body, looked up by exact memory node name. |
| ``motivated_by`` | commit | memory_feedback / plan_doc / memory_project | 0.9 | word-bounded memory-node-name match in the commit body (explicit reference without a formal trailer). |

The schema for these three relations was reserved in v2.0 phase 1
(see ``store.EDGE_RELATIONS``); v2.3.0 is the producer.

### Deferred (not in this release)

The design § 6 lists a SECOND ``motivated_by`` heuristic (commit
ts within 24h of a doc's ``updated_at`` AND embedding cosine >=
0.78, confidence 0.6). It needs the embedder threaded through
the git-log module and adds an O(commits * docs) cosine scan to
every reindex; deferring it keeps v2.3.0 tractable. Will land
either as v2.3.1 or alongside the v2.6 ``mnemo:why-is-this-here``
skill.

### Robustness

- Non-git directories (extracted tarballs, vendored sources)
  silently no-op rather than blowing up reindex.
- Each commit's git-show call is independently try/except'd so
  one bad commit doesn't lose us the rest of the history.
- Windows-safe: the subprocess field separators use ``\x1f``
  (ASCII Unit Separator) instead of ``\x00`` NUL bytes which
  Windows ``CreateProcess`` rejects.

### Tests

- ``daemon/tests/unit/test_git_log.py`` (13 cases): CommitEntry
  shape, diff-hunk parsing, trailer parsing, overlap-confidence
  computation, word-boundary motivated_by matching, closed_by
  edge direction, commit-to-Node lifting.
- ``daemon/tests/integration/test_git_log_integration.py``
  (4 cases): full path against a tmp ``git init`` repo with
  three commits, asserts commit nodes + references_function +
  closed_by edges materialize; idempotency on re-run; non-git
  directory no-ops; frontmatter provenance fields present.
- Total daemon suite: 678 passing (was 661 + 17 new).

## [2.2.7] - 2026-05-14

**Nebula side panel body preview: type-aware rendering** -- markdown
bodies now render as HTML instead of monospace source.

### Fixed (Nebula misidentified markdown as code)

Reported (2026-05-14): "md file preview in nebula page seems off,
it might misunderstand code and md".

Root cause: the Nebula side panel template hardcoded the body to
render inside a ``<pre class="line-numbers"><code class="language-X">``
shell, and the helper ``streamBodyToCode`` (introduced v2.2.1 phase
4 BEFORE ``mnemoRenderBody`` became streaming-aware in v2.2.5) just
streamed plain text into that ``<code>`` element + called
``Prism.highlightElement`` at the end. So EVERY body in Nebula --
``memory_*``, ``project_doc``, ``plan_doc``, ``session_summary``,
``code_*`` whose ``source_path`` ends in ``.md`` -- rendered through
the code path. Markdown source appeared as monospace text with
Prism trying to syntax-color ``**bold**`` and ``# heading`` as
markdown SOURCE.

Meanwhile the ``/node/<id>`` detail page and the search popover
both used ``mnemoRenderBody`` correctly. Only Nebula bypassed the
type-aware branching.

### Changed

Drop the ``<pre><code>`` shell + ``streamBodyToCode`` path. The
side panel now renders into a ``<div class="nebula-body md-body">``
and delegates to ``window.mnemoRenderBody(el, body, { type,
sourcePath })`` -- the SAME helper ``node.html`` +
``_search_results.html`` use. Three-branch decision:

| Branch | Render |
|---|---|
| ``code_*`` | Prism-highlighted ``<pre><code>`` (helper writes the shell) |
| ``commit`` | escaped plain ``<pre>`` |
| markdown (memory / project / plan / session / docs) | marked + DOMPurify -> rendered HTML with the same typography as node.html |

``mnemoRenderBody`` already owns streaming (via ``mnemoStreamText``)
AND cancellation (via ``targetEl._mnemoStreamCancel``) since
v2.2.5, so the side panel keeps progressive reveal + click-during-
stream cancellation for free. The new ``bodyMode`` Alpine state
toggles a ``.is-code`` class on the container so code bodies still
get the dark monospace chrome.

### Removed (deprecated helper)

``streamBodyToCode`` is gone -- it was a v2.2.1 phase-4 vestige
that ``mnemoRenderBody`` subsumes. The test
``test_focus_node_streams_body_via_mnemo_stream_text`` is updated
to accept either ``mnemoStreamText`` OR ``mnemoRenderBody`` (which
itself routes through mnemoStreamText) as the streaming surface.

### Tests

- ``daemon/tests/unit/test_nebula_body_render.py`` (5 new cases):
  no hardcoded ``<pre class="line-numbers"><code>`` shell, no
  ``streamBodyToCode`` x-effect, mnemoRenderBody call wired,
  ``md-body``-classed container, ``bodyLanguage`` helper still
  present.
- ``daemon/tests/unit/test_nebula_progressive.py`` (1 case
  updated): the streaming-surface assertion now accepts
  mnemoRenderBody as the delegating front-door.
- Total daemon suite: 661 passing.

## [2.2.6] - 2026-05-14

**Layout-change preserves focus state** -- DOM-overlay cross-fade
replaces the cytoscape opacity bypass.

### Fixed (focus state lost after layout change)

Reported (2026-05-14): clicking a node selects it (which adds
``.hl`` to the neighborhood + ``.dim`` to everything else); then
clicking a layout button (rings / tree / grid / force) silently
killed the blur-others effect. The selected node still pulsed
(DOM overlay was intact), but the contrast against the dimmed
background vanished and the focus read as "gone".

Root cause: the prior ``relayout()`` cross-faded by calling
``cy.elements().animate({ style: { opacity: 0.3 -> 1.0 } })``. In
cytoscape, ``.animate({ style: ... })`` writes INLINE BYPASS
styles that persist after the animation completes. After the
fadeIn settled, every node carried inline ``opacity: 1``, which
overrode the ``.dim`` class's stylesheet rule
``opacity: 0.12`` -- so the dim effect disappeared. The ``.hl``
halos were still rendered but had nothing to contrast against.

### Changed (pipeline 18: DOM-overlay cross-fade)

The fade moves off cytoscape's style cascade entirely. A new
``.nebula-layout-veil`` ``<div>`` lives above the canvas (z:5,
below the pulse anchor at z:6); ``layoutFading`` (new Alpine
state) toggles its ``.on`` class, and a 140 ms CSS opacity
transition does the rest. ``relayout()`` flips the veil on,
waits one fade cycle (``setTimeout 140``), snaps positions
inside ``cy.batch`` (single redraw), animates the camera tween,
then flips the veil off in the camera animation's ``complete``
callback. Cytoscape's per-element opacity is never touched, so
``.hl`` / ``.dim`` class rules own the focus visual throughout.

Same pattern as the v2.2.4 pulse rewrite. Pipeline 18 in
``reference_mnemo_pipelines.md`` (BASE).

### Accessibility

``prefers-reduced-motion: reduce`` collapses the veil delay to
0 ms AND keeps the veil at opacity 0, so the layout change is
an instant snap with no fade for users who opted out.

### Tests

- ``daemon/tests/unit/test_relayout_focus_state.py`` (6 new cases):
  - ``relayout()`` no longer contains
    ``.animate({ style: { opacity: ... } })`` anywhere
  - position snap stays inside ``cy.batch``
  - ``.nebula-layout-veil`` ``<div>`` is in graph.html
  - ``layoutFading`` Alpine state is wired
  - ``.nebula-layout-veil`` class exists in app.css
  - the CSS rules include an opacity transition
- Total daemon suite: 656 passing.

## [2.2.5] - 2026-05-14

**Phase 5 of the v2.2 progressive-UX rollout.** Body content now
reveals progressively in every preview surface. The v2.2 design
is complete with this release.

### Changed (body streaming)

``window.mnemoRenderBody`` keeps the same call-site signature
``(targetEl, body, { type, sourcePath })`` and the same return
contract ('code' / 'plain' / 'markdown') but routes the actual
reveal through ``window.mnemoStreamText`` from v2.2.0. Per-branch
table:

| Branch | Unit | Cadence |
|---|---|---|
| ``code_*`` | line | 8 ms/line, Prism re-highlights every ~80 ms |
| ``commit`` | line | 8 ms/line into a plain ``<pre>`` |
| markdown | word | 20 ms/word, sequential over text-node spans |

Cold-load fallback: if ``window.mnemoStreamText`` hasn't hydrated
yet (rare -- ``app.js`` is ``<script defer>``), the helper falls
back to a one-shot innerHTML write so the user still sees
content. The next call streams normally.

### Added (cancellation)

Each call sets ``targetEl._mnemoStreamCancel`` to a closure that
aborts the in-flight stream + flushes remaining content
immediately. Calling ``mnemoRenderBody`` twice on the same target
cancels the prior reveal before starting the new one -- so
clicking a neighbor mid-stream no longer races two reveals into
the same panel. The handle is also addressable by future
coordinators (chat panel, focus-node controller) without
changing the call-site signature.

### Accessibility

``mnemoStreamText`` already collapses every primitive's delay to
zero under ``prefers-reduced-motion: reduce`` (verified by
``test_app_js_honors_reduced_motion``). No special-casing needed
in ``mnemoRenderBody`` itself.

### Tests

- ``daemon/tests/unit/test_body_streaming.py`` (9 new cases) --
  surface contract: helper still defined, mode strings preserved,
  ``mnemoStreamText`` wired, ``unit: 'line'`` for code/commit,
  ``unit: 'word'`` for markdown, Prism still invoked, cancel
  handle exposed, prior stream cancelled, reduced-motion path
  intact.
- Total daemon suite: 650 passing.

### Where this lands the design

Section 5 of ``docs/plans/2026-05-14-ux-progressive-design.md``
is the last v2.2 phase. With this release the unified
progressive-UX pattern -- skeleton -> stream -> settle -- runs
across reindex, Nebula initial paint, node-to-node transitions,
AND body previews. Ready to underpin the v2.3 chat layer.

## [2.2.4] - 2026-05-14

**DOM-overlay pulse (architectural rewrite) + persisted filter /
layout state.** Two changes -- one rebuilds the heart-beat pulse
on a guaranteed-to-render foundation; the other makes user
choices survive a reload.

### Changed (pulse architecture)

After three failed cy.animate-based fix attempts for "no pulse on
canvas tap", the pulse moves off cytoscape's internal animation
queue entirely. A new absolutely-positioned ``<div
class="nebula-pulse-anchor">`` lives ABOVE the canvas; its
``left`` / ``top`` / ``width`` / ``height`` track the selected
node's ``renderedPosition()`` + ``renderedOuterWidth()`` via
``cy.on('pan zoom render')`` and ``cy.on('position', 'node')``.
The inner ``.nebula-pulse-ring`` is animated entirely by CSS
``@keyframes`` -- a beat + a ripple. Two consequences:

1. **The pulse renders the same in every browser regardless of
   cytoscape's animation state.** CSS @keyframes are guaranteed
   by the W3C spec to run; nothing in cytoscape's internal
   queue can starve them. Both canvas-tap and file-tree-click
   produce identical visuals because both paths feed the SAME
   ``_updatePulseAnchor`` call.
2. **Scalable.** When the future chat feature wants to highlight
   N nodes at once (top hits for a prompt), the same anchor
   pattern instantiates N rings -- no per-node cytoscape
   animation queue management.

Old ``_startPulse`` (cy.animate-based) and ``_stopPulse`` (style
cleanup) are simplified: ``_startPulse`` now just sets
``this._pulseNode`` and triggers an anchor update. ``_stopPulse``
clears the anchor. The cytoscape underlay still pulses gently
via the existing ``.hl`` class transitions, but the DRAMATIC
beat (thick cyan ring + halo + double-beat ripple) lives in the
DOM overlay.

### Added (state persistence)

Five fields now persist to localStorage under the ``nebula.``
namespace; the user's last session is restored on every page
load:

  - ``nebula.layout``         layout choice (force/rings/tree/grid)
  - ``nebula.typeFilters``    JSON ``{ [type]: bool }`` per chip
  - ``nebula.minConfidence``  edge-confidence slider value
  - ``nebula.edgesVisible``   toggle from v2.2.3
  - ``nebula.labelsVisible``  toggle from v2.2.3

``_loadPersistedState()`` runs once during ``init()`` (before
``reload()``). ``_persistState()`` runs on every mutation site
(typeFilters chip click, min-conf slider, layout button,
toggleEdges / toggleLabels). Panel widths (``nebula.left`` /
``nebula.right``) already persisted via the resize gutter
handler; new fields slot into the same pattern.

### Implementation notes

- ``_persistedFilters`` is a transient field set by
  ``_loadPersistedState`` -- consumed by ``buildTypeFilters``
  the first time it runs (cytoscape isn't booted yet when load
  happens). Anything not in the persisted set falls back to
  default-on, so types added in future schema bumps don't get
  silently hidden.
- The pulse anchor uses ``transform: translate(-50%, -50%)`` so
  ``left`` / ``top`` can be the node CENTER -- no center-offset
  math in JS.
- ``color-mix(in srgb, var(--accent) X%, transparent)`` powers
  the bloom + ripple shadows. Chrome 111+, Safari 16.4+,
  Firefox 113+ -- all baseline browsers we already require.
- ``prefers-reduced-motion: reduce`` collapses the pulse beat
  and ripple animations to ``none`` -- ring still appears (so
  the user knows the node is selected) but doesn't pulse.

### Tests

545 unit tests pass. No new tests -- the pulse architecture is
DOM + CSS so there's nothing testable without a headless
browser (out of scope for this project's no-Node-toolchain
constraint). Live smoke verified in preview:

  - anchor positioned at node center, 38x38px scaled w/ node
  - ``ring_animation: "nebula-pulse-beat"`` active
  - ``ring_border_color: rgb(126, 231, 224)`` = palette accent
  - pan +100x -> anchor moves 100px; zoom 2.0x -> width 38 -> 132
  - localStorage writes confirmed for all 5 fields on mutation

## [2.2.3] - 2026-05-14

**Nebula polish: visibility toggles + drag-stable edges + clean
cold paint + finally-visible pulse.** Four user-reported issues
addressed in one ship.

### Added

- **Edge + label visibility toggles** in the Nebula filter bar.
  Each toggle is a pill with a leading dot -- filled when ON,
  outlined when OFF. Both default to ON. Tapping each flips a
  cytoscape class (``edge.edges-off`` or ``node.labels-off``)
  inside a ``cy.batch()`` so the canvas redraws once.

### Fixed

- **Edges no longer disappear during pan / drag.**
  ``hideEdgesOnViewport`` was ``true`` since v2.0 to keep big
  graphs cheap during fast zooms. The pop-out behavior felt
  jarring -- the graph "collapsed" into bare nodes mid-drag,
  then snapped back. Set to ``false`` for v2.2.3. Labels still
  hide during fast viewport changes (``hideLabelsOnViewport:
  true``) because re-rendering label text isn't cheap and the
  busy-text effect is genuinely distracting.

- **Cold paint no longer flashes "edges first, then nodes".**
  The v2.2.1 chunked-paint hid nodes (via
  ``.preload-hidden`` opacity:0) but did NOT hide edges. Edges
  between two invisible nodes still drew their lines, so for
  the first ~720ms of a cold load the user saw a tangle of
  connections in empty space. Now ``.preload-hidden`` covers
  edges too (``display: none``), and edges fade in AFTER the
  final node chunk lands. Cold paint reads as "densest cluster
  appears, more nodes wave in, then the connection web settles
  in".

- **Canvas-tap pulse is now visible.** Despite v2.2.2 unifying
  the camera-pan + strengthening the pulse, the user still
  reported "no heart beat" on direct canvas taps. Two compounding
  causes:
  1. ``node.animate({style: ...})`` was being QUEUED behind the
     just-applied ``.hl`` class transitions (which animate
     ``border-width`` + ``underlay-padding`` over 220ms via the
     base ``transition-property``). The first half of the pulse
     was eaten by the queue wait. Now uses ``queue: false`` so
     it runs from frame 0.
  2. The pulse amplitudes were still too subtle without the
     camera framing the node center. Bumped further:

         peak underlay-padding 28 -> 36  (+22 from .hl baseline)
         peak underlay-opacity 0.95 -> 1.0
         peak border-width      2 -> 5  (NEW: thick bright stroke)
         period 600ms -> 500ms each leg (1.0s/cycle = clear beat)

  The headline change is the border-width pulse: ``border-width``
  IS in the base ``transition-property``, so cy.animate moves it
  reliably regardless of where the node is on screen.

- **Pulse cleanup also clears border-width bypass.**
  ``_stopPulse`` previously cleared only ``underlay-padding`` and
  ``underlay-opacity``; a de-selected node would keep its 5px
  pulsed border until the next ``.hl`` change overwrote it.
  ``_stopPulse`` now also clears ``border-width``.

### Tests + CI

545 unit tests pass. Ruff lint + format clean. The existing
``test_nebula_progressive.py`` cases still cover the chunked
paint, body streaming, and neighbors stagger from v2.2.1; no
new assertions needed for v2.2.3 (the changes are CSS + cy
config + amplitude tuning behind existing call sites).

## [2.2.2] - 2026-05-14

**Consistent select feedback in Nebula + stronger heart-beat pulse.**

### Fixed

- **Canvas-tap selection had no visual feedback on the node itself.**
  Clicking a node directly on the graph dimmed the rest of the
  canvas and opened the detail panel, but the selected node sat
  perfectly still -- no camera framing, no perceptible pulse.
  Clicking the same node from the file tree DID frame it
  (camera pan + zoom 1.4), so the same logical action felt
  totally different depending on the path. Two changes:

  - **Camera framing is now in ``selectFromCanvas``**, so every
    entry path (canvas tap, file-tree click, neighbor list click,
    ``?node=`` URL deep-link) runs the same ``cy.animate({center,
    zoom})`` over 350ms. Zoom bumps UP to 1.4 when the user is
    further out, otherwise their current zoom is preserved (we
    don't yank them out when they've zoomed in deliberately).
    The duplicate ``cy.animate`` calls in ``focusNode`` and the
    ``?node=`` pre-select have been removed.
  - **The pulse is now unmistakable.** Pre-v2.2.2 the
    ``_startPulse`` animation went underlay-padding 14 -> 20
    (+6 px) and underlay-opacity 0.6 -> 0.7 (+0.1) over a 900ms
    half-cycle. Too subtle to read as "beating" without the
    camera framing the node. Now it goes 14 -> 28 (+14 px) and
    0.6 -> 0.95 (+0.35) over a 600ms half-cycle. The selected
    node now visibly breathes -- you can see it from across the
    canvas, not just when zoomed in.

### Behavior at a glance

  before:   click node on canvas -> nothing moves, faint pulse
            click file in tree   -> camera pans, faint pulse

  after:    click node on canvas -> camera frames it, strong pulse
            click file in tree   -> camera frames it, strong pulse

All five existing tests in ``test_nebula_progressive.py`` still
pass (they assert ``cy.animate({center: ...})`` is referenced --
still is, just in one place now). 545 unit tests pass total.
Ruff lint + format clean.

## [2.2.1] - 2026-05-14

**Phase 4 of the v2.2 progressive-UX rollout: Nebula goes
progressive.** The initial graph paint now waves in by descending
node degree, the detail-panel body streams in line- or word-by-
line, and the neighbors list staggered-reveals. All three reuse
the phase 1 primitives -- no new shared API.

### Added

- **Chunked initial Nebula paint.** After fcose finishes laying
  out the graph, every node is tagged with the new ``.preload-hidden``
  cytoscape class. ``_renderCanvasChunked()`` then reveals nodes in
  batches of ``CHUNK = 50`` at an 80ms cadence, sorted by descending
  degree -- so the densest cluster of the graph paints first, then
  the rest of the components fade in waves. Total reveal ~720ms for
  a ~480-node graph. Each chunk briefly carries ``.fade-in`` for
  260ms so the per-chunk reveal animation fires.
- **Body streaming in the Nebula side panel.** When a node is
  selected and its body fetch resolves, the body content reveals
  via ``window.mnemoStreamText`` -- word-by-word for prose, line-
  by-line for code (with a single Prism pass after the stream
  completes). The orchestrator (``streamBodyToCode``) cancels any
  in-flight stream before starting a new one so rapid neighbor
  clicks don't race.
- **Neighbors list staggered reveal.** The detail panel's "Connections"
  list is now rendered via ``window.mnemoStaggeredReveal`` --
  30ms per item, 180ms fade. Each ``<li>`` carries the ``.reveal-item``
  class while it transitions. The orchestrator
  (``renderNeighborsList``) cancels any in-flight reveal before
  starting a new one.

### CSS

- New cytoscape selector ``node.preload-hidden`` (opacity 0,
  underlay-opacity 0) scoped to this class only so it can't
  re-introduce the v2.1.2 dim/un-dim opacity-transition fanout
  lag we previously fixed.

### Accessibility

- The chunked reveal honors ``prefers-reduced-motion: reduce``:
  when the user prefers reduced motion, ``_renderCanvasChunked``
  is a no-op and every node is visible on first paint. The
  staggered-reveal + text-stream primitives already short-circuit
  to instant display under the same preference.

### Tests

- ``tests/unit/test_nebula_progressive.py`` (8 cases) -- locks the
  surface that the chunked reveal + body streaming + neighbors
  stagger live behind. Verifies ``_renderCanvasChunked`` exists,
  sorts by degree DESC, applies the ``.fade-in`` class, references
  a chunk-size constant; that ``mnemoStreamText`` and
  ``mnemoStaggeredReveal`` are called in graph.html; and that the
  camera-pan ``cy.animate({ center })`` path is preserved.
- 545 unit tests pass (was 537; +8 phase 4). Ruff lint + format
  clean.

### Live smoke verified

- Chunked reveal: 0 hidden → 50 fade-in → 400 fade-in → 0 hidden
  over ~700ms (eval-instrumented).
- Neighbors: 17 items with ``.reveal-item`` class applied;
  ``--type-color`` stamps preserve palette-driven dot colors.
- Body streaming: 95-char Python function body renders correctly
  via ``streamBodyToCode`` with ``unit: 'line'``; Prism re-highlight
  fires on done.

## [2.2.0] - 2026-05-14

**Streaming reindex + unified progressive-UX foundation.** First
release of the v2.2 progressive-UX rollout (design:
``docs/plans/2026-05-14-ux-progressive-design.md``). One coherent
streaming pattern shared by every future heavy operation in mnemo;
the Sources page is the first visible consumer.

### Added

- **Shared client primitives** (``daemon/mnemo/ui/static/app.js``)
  loaded site-wide from ``base.html``. Four helpers + one a11y
  probe that every future progressive UI consumes:
  - ``window.mnemoSkeleton(kind, opts)`` -- shimmer placeholder
    for ``list`` / ``paragraph`` / ``code`` / ``graph`` / ``card``
    shapes. Returns a DOM node the caller replaces with real
    content.
  - ``window.mnemoStaggeredReveal(container, items, opts)`` --
    RAF-paced fade-in for items already in memory. Returns
    ``{ cancel(), done }``.
  - ``window.mnemoStreamFromSSE(url, opts)`` -- ``EventSource``
    wrapper with per-event dispatch, JSON decoding,
    ``AbortSignal`` cancellation.
  - ``window.mnemoStreamText(target, source, opts)`` -- paces
    text reveal char/word/line at a time; accepts a string OR
    a ``ReadableStream`` so call sites stay identical when real
    streaming arrives.
  - ``window.mnemoPrefersReducedMotion()`` -- single shared probe.
  All five honor ``prefers-reduced-motion: reduce`` (animations
  collapse to 0; content snaps to final state).
- **``.skeleton`` / ``.reveal-item`` / ``.fade-in`` CSS** plus
  ``@media (prefers-reduced-motion: reduce)`` snap-rules.

- **``ingest.reindex_events()`` generator** yielding
  ``(event_name, payload)`` tuples (``start`` / ``file`` / ``done``).
  ``ingest.reindex()`` is now a thin wrapper that drains the
  generator and reconstructs the legacy ``ReindexReport``. Existing
  callers (CLI + ``POST /v1/reindex``) see zero behavior change.

- **``GET /v1/reindex/events``** -- Server-Sent Events route.
  Streams the ``reindex_events`` generator as
  ``event: <name>\ndata: <json>`` frames. Shares the same
  ``reindex_lock`` ``POST /v1/reindex`` uses; concurrent connections
  get a single ``event: busy`` frame then EOF. Sets
  ``Cache-Control: no-store`` + ``X-Accel-Buffering: no`` so proxies
  and browsers never cache the stream.

- **Streaming reindex progress on the Sources page.** The "Reindex
  all" button now opens a live progress block above the table:
  - ``N / M files`` counter + current file name.
  - Palette-driven progress bar (reuses ``.bar-fill`` from the
    dashboard; turns red if errors accumulate).
  - "stop" button that aborts the stream via ``AbortController``.
  - Summary line after ``done``: added / updated / unchanged /
    removed + duration.
  - Auto-reloads the page ~1.5s after ``done`` so the table
    reflects the new state.

- **``app.state.mnemo_state``** -- the per-app ``AppState`` is
  now reachable from the FastAPI instance, so tests and helpers
  can introspect the reindex lock without monkey-patching internals.

### Changed

- **Sources page reindex flow is stream-first.** If the browser
  supports ``EventSource``, the page subscribes to
  ``/v1/reindex/events`` and updates the bar live. If SSE is
  unavailable (legacy browsers, restrictive proxies) the page
  falls back to the previous ``POST /v1/reindex`` + status-poll
  pattern. The POST path is retained for v2.2.x and will be
  removed in v2.3 once SSE is proven everywhere.

### Tests

- ``tests/unit/test_progressive.py`` (12 cases) -- locks the
  surface of the four primitives + base.html wiring + CSS classes.
- ``tests/unit/test_reindex_events.py`` (9 cases) -- generator
  contract (start/file/done shape + ordering, idempotent reruns,
  ``ReindexReport`` regression); SSE wire contract
  (``text/event-stream``, frame format, busy event, POST regression).
- ``tests/unit/test_sources_progress.py`` (7 cases) -- template
  ships the progress markup with ``mnemoStreamFromSSE`` + cancel
  affordance + POST fallback retained.
- 537 unit tests pass total (was 521). Ruff lint + format clean.

### Phases 4 + 5 (deferred to v2.2.x point releases)

- Phase 4: chunked Nebula initial paint + coordinated node-to-node
  transitions. Reuses ``mnemoStaggeredReveal`` + ``.fade-in``.
- Phase 5: ``mnemoRenderBody`` adopts ``mnemoStreamText`` so every
  body preview reveals word-by-word (memory) or line-by-line
  (code). Call sites unchanged.

## [2.1.3] - 2026-05-14

**Hotfix.** The /code page project-card progress bars were
invisible (zero-width strips) after v2.1.1's palette refactor.
Apologies for the v2.1.2 patch -- it fixed the colors but not
the underlying box-model bug.

### Fixed

- **/code progress bars now actually render.** The /code template
  marks up each bar as ``<span class="bar-track"><span
  class="bar-fill"></span></span>``. ``<span>`` defaults to
  ``display: inline``; per the CSS spec, inline elements ignore
  ``width`` and ``height``. The inline ``style="width: 8.7%"``
  and the CSS ``height: 100%`` were both silently dropped on the
  floor, so each bar rendered at 0x0 -- DOM-present, correctly
  colored, but invisible.

  The outer ``.bar-track`` happened to be sized because it's a
  grid item (grid items are blockified by spec). The inner
  ``.bar-fill`` is nested one level deeper, NOT a grid item, so
  it remained inline.

  Fix: ``display: block`` on the scoped ``.code-project-card-bars
  .bar-fill`` rule. Also added defensively to the unscoped rule
  so the dashboard's ``.bar-fill`` paints regardless of whether
  the template uses ``<div>`` or ``<span>``.

  Verified live: every bar now has computed height 4px and a
  proportional computed width (8.7% -> 20.1px on a 231.6px track,
  49.1% -> 113.7px, etc).

## [2.1.2] - 2026-05-13

**Two follow-on bug fixes** from real-use feedback minutes after
v2.1.1 went out.

### Fixed

- **``/code`` project-card bars went blank** -- a stale duplicate
  ``.bar-fill`` rule (introduced when the /code landing was built
  pre-palette-refactor) sat AFTER the palette-driven ``.bar-fill``
  rule and overrode the ``background`` declaration with nothing.
  Result: per-type colors on the dashboard (which uses the same
  class) worked, but on /code the bar inside the project card
  was an invisible 4px transparent strip. Scoped the duplicate
  rule under ``.code-project-card-bars`` so it can't shadow the
  generic one, AND set ``background: var(--type-color, ...)`` on
  the scoped version explicitly. Both surfaces now paint
  consistently.
- **Nebula deselect lag** when clicking empty canvas (or pressing
  Escape) to clear a selection. Two compounding causes:
  1. The base ``node`` selector had
     ``transition-property: 'opacity, border-width,
     underlay-opacity, underlay-padding'`` with a 220ms duration.
     ``.dim`` toggles ``opacity`` + ``underlay-opacity`` -- so
     deselect kicked off 471 simultaneous opacity tweens. With
     motion-blur on, every redrawn frame for 220ms touched all
     471 nodes.
  2. ``_stopPulse()`` only unset a guard flag; the in-flight
     ``node.animate({underlay-padding, underlay-opacity}, 900ms)``
     chain kept running for up to 900ms after deselect, with its
     ``complete`` callback queueing the next half of the cycle
     BEFORE the flag check fired. The previously-selected node
     kept mutating styles long after it should have been done.

  Fixes:
  - Dropped ``opacity`` and ``underlay-opacity`` from the base
    node ``transition-property``. They're the properties ``.dim``
    toggles across the whole graph, and snapping them is fine --
    the selected cluster still feels "lifted" because
    ``border-width`` and ``underlay-padding`` (changed only on
    the 1-or-few ``.hl`` nodes) still transition.
  - ``_stopPulse()`` now calls ``node.stop(true, false)`` to
    cancel the in-flight animate chain AND
    ``removeStyle('underlay-padding underlay-opacity')`` to clear
    the inline styles the pulse wrote.
  - ``deselect()`` calls ``_stopPulse()`` FIRST, then
    ``cy.elements().stop(true, false)`` to cancel any other
    queued animations (e.g. selectFromCanvas's camera-fit),
    THEN the bulk ``removeClass('hl dim')`` inside ``cy.batch()``.

  Net effect on a 478-node graph: deselect sync ~10ms (vs ~90ms
  before); no lingering tween animations after 300ms (vs the
  pulse running until its 900ms complete callback fired).

## [2.1.1] - 2026-05-13

**Nebula UX polish + scaling architecture.** Seven follow-ups on
top of v2.1.0. The common thread: previously-implicit per-type
behavior was made explicit and palette-driven so the UI can absorb
new node types without per-file edits.

### Added

- **Single-source node-type palette.** ``daemon/mnemo/ui/palette.py``
  owns the ``TYPE_COLORS`` dict. Exposed to every Jinja template as
  a global (``type_colors``) and to every JS surface as
  ``window.MNEMO_TYPE_COLORS``. Generic CSS selectors
  (``.badge[class*="type-"]``, ``.bar-fill``, ``[class*="swatch-"]``,
  ``[class*="ntype-"]``) read a ``--type-color`` custom property
  stamped inline by the templating layer. Adding a new node type
  is one line in palette.py; badges, bar fills, filter swatches,
  detail-panel pills, neighbor dots, and canvas nodes all pick up
  the new color automatically.
- **Type-aware body preview** (``window.mnemoRenderBody``). One
  helper used by the node detail Preview tab, the search-result
  popover's "Show body" toggle, and the Nebula side panel. Branches
  on three paths: ``code_*`` types -> Prism-highlighted
  ``<pre><code>``; ``commit`` -> escaped plain ``<pre>``;
  everything else -> marked + DOMPurify markdown. Returns the path
  taken so callers can decorate the UI.
- **``source_path`` carried end-to-end on hits.** ``CompressedHit``
  and ``HitOut`` now expose ``source_path``, so the search popover
  can pick a Prism language hint per hit.
- **Site-wide Prism.** Moved Prism + autoloader from a per-page
  ``head_extra`` block into ``base.html``; every preview surface
  gets the same Tomorrow-Night palette + lazy language grammars.

### Fixed

- **Filter chips + dashboard "Memory by type" bars color every node
  type now.** v2.0 added 7 code_* types but the per-type CSS rules
  + JS dict were only partially updated; result was all-blue filter
  chips and invisible progress bars on the dashboard. The palette
  refactor closes this gap.
- **Nebula empty-canvas tap now deselects.** The guard was
  ``evt.target === this.cy``, which fails in minified Cytoscape
  builds because the core is wrapped in an obfuscated class.
  Switched to a capability test (``typeof t.isNode !== 'function'``).
  Edges still don't trigger deselect.
- **Force-layout snapshot restore was dead code.** The guard
  checked ``name === 'force'`` but the button passes ``'fcose'``.
  Fixed; switching to rings/tree/grid and back to force now
  restores the original positions (max drift 0 px across 422
  nodes).
- **Two-arrow bug in Nebula file tree.** A redundant
  ``::before { content: "▸" }`` rule was painting a second chevron;
  Chrome 128+ also reserves a phantom flex slot for ``<details>``
  disclosure widgets inside any ``<summary>`` with
  ``display: flex``. Cure: removed the duplicate rule + restructured
  summary children into an inner ``display:flex`` wrapper.
- **Connections count rendered as literal text in Nebula detail
  panel.** Template had ``{{ '{{' }} neighbors.length {{ '}}' }}``
  attempting to escape Alpine mustache through Jinja2 -- but Alpine
  doesn't use mustache for text interpolation. Replaced with
  ``<span x-text="neighbors.length">``.
- **Force-layout ran twice on first load.** Alpine.js auto-invokes
  any method named ``init()`` on the ``x-data`` object; pairing
  ``x-data="nebula()"`` with ``x-init="init()"`` ran init() twice.
  Dropped the redundant ``x-init`` from graph.html, base.html,
  settings.html, sources.html, node.html.
- **Native ``<details>`` marker still painted in the Nebula file
  tree.** Per CSS spec, ``::marker`` only accepts color / content /
  font-* / white-space / text-* properties; ``display: none`` is
  ignored. Switched to the allowed levers
  (``content: ""; font-size: 0; color: transparent``); added a
  CSS cache-bust via ``?v={{ mnemo_version }}`` on the
  ``/static/app.css`` link.

### Changed

- ``base.html`` now provides three site-wide helpers:
  ``window.mnemoIsCodeType(t)``, ``window.mnemoLanguageOf(path)``,
  and ``window.mnemoRenderBody(el, body, opts)``. Pages that
  rendered bodies inline have migrated to the shared helper.
- ``graph.html`` JS ``TYPE_COLORS`` is now an alias of
  ``window.MNEMO_TYPE_COLORS`` -- no per-page palette dict.

## [2.1.0] - 2026-05-13

**Nebula — three-panel graph UX.** A focused UI refinement on top
of v2.0's code graph. ``/graph`` is no longer a single canvas with
side overlays -- it's a resizable three-panel shell (file tree |
graph canvas | node detail) plus a sticky filter bar. The
``/code`` cards now funnel into ``/graph?project=<key>``, so a
single canonical visualization page serves both code-graph and
memory-graph exploration.

### Added

- **Three-panel resizable shell** at ``/graph``. Drag the gutters
  to resize; widths persist to localStorage. Default
  240 / flex / 320.
- **File tree (left panel).** Built from ``code_module``
  source_paths. Single-child directory chains collapse so deep
  Windows paths render compactly. Click a file -> focus + select
  on canvas. The active file highlights in the tree.
- **Detail panel (right).** Type badge + name + source_path +
  body + ranked neighbors with relation + confidence labels.
  Open-detail button links to ``/node/<id>``; copy-cite button
  copies ``[mnemo:<id>]``.
- **Filter bar (bottom).** Text search filters by name/type;
  per-type chip toggles narrow visibility; confidence slider
  hides edges below a threshold; hop selector (when in
  node-scope mode); force / concentric / circle relayout
  buttons; live node + edge counter.
- **Cross-stack visual language.** 8 code-type colors + 7
  memory-type colors are consistent across chips, tree dots,
  detail badge, graph nodes. Node SHAPES disambiguate:
  ``code_module`` = round-rectangle, ``code_route`` = diamond,
  ``code_endpoint`` = hexagon, ``commit`` = tag.
- **Confidence-encoded edges.** Line style by confidence:
  ``>= 0.9`` solid, ``0.7-0.9`` dashed, ``< 0.7`` dotted. Edge
  color encodes relation (calls = purple, routes_to = amber,
  at_endpoint = green, imports = cyan, provenance = pink).
  Arrowheads only on directional relations.

### Changed

- **``GET /ui/graph-data``** now accepts:
  - ``?project=<key>`` -- filter to nodes with that project_key
    plus cross-cutting (NULL / BASE) nodes connected to them.
  - ``?node=<id>&hops=<n>`` -- ego-network BFS from ``<id>`` out
    to ``n`` hops (default 2, capped at 4).
  Response nodes now include ``source_path`` + ``description`` so
  the file tree can group and the detail panel can render without
  a second round-trip. Edges now carry ``confidence``.
- **``/code`` project cards** now link to ``/graph?project=<key>``
  (the new primary CTA). A small "summary" link still goes to the
  list view at ``/code/<project>``.
- **``/code/<project>``** overview shows two CTAs side-by-side:
  "Open in graph" and "Cross-stack sitemap".

### Tests

- ``tests/unit/test_ui.py::test_graph_page_renders`` updated for
  the new shell (canvas id ``cy`` -> ``cy-nebula`` + ``nebula-shell``
  smoke).
- Full suite: 604 passing, 2 skipped, 0 failing.

### End-to-end UI verified

Via the preview tool against the daemon's own indexed code
(``mnemo-daemon`` project, 468 nodes after reindex):

- ``/graph?project=mnemo-daemon`` -> 416 nodes / 574 edges
  scoped to that project.
- Tree renders 33 files across 4 nested directories.
- Click ``flask.py`` -> selects on canvas + populates detail
  panel with name + type badge + source_path + body + 17
  connections.
- Toggling type chips to ``code_route + code_endpoint`` only
  -> exactly 80 nodes (40 routes + 40 endpoints) and 40
  ``at_endpoint`` edges.

## [2.0.0] - 2026-05-13

**Code Intelligence.** The headline v2.0 release: every registered
``code_repo`` source produces a typed code graph (modules, functions,
classes, methods + Tier 2 ``calls`` edges + Tier 3 routes /
components / endpoint anchors), plus the seven mnemo:code skills
that turn the graph into natural-language Q&A inside Claude Code.

### Headline capabilities

- **Cross-stack sitemap.** "This React button calls this Express
  handler which queries this Postgres table." A single graph
  traversal walks ``Component -> Endpoint <- Route -> Handler`` via
  the new ``at_endpoint`` join, rendered at
  ``/code/<project>/sitemap``.
- **Code-aware retrieval.** "Where is ``<function>`` called from?"
  returns correct callers via the Tier 2 ``calls`` edges. Confidence
  scores (0.95 within-file, 0.8 cross-file) carry uncertainty into
  retrieval ranking.
- **Auto-routing with safety.** ``mnemo source add <path>`` runs
  the auto-router; dry-run preview shows proposed kind + file
  breakdown before any DB write. 50,000-file safety ceiling
  prevents Duyen-class accidents.

### Roadmap completion

Phases shipped through ``release/2.0.0`` (in order):

1. Schema: ``code_repo`` / ``docs_dir`` source kinds, ``commit``
   node type, edge ``confidence`` column, provenance edges.
2. Source auto-router with dry-run preview + 50k file ceiling.
3. Tree-sitter grammar bundle + lazy-download stub.
4. Tier 1 universal code ingestion (8 bundled languages; Python
   full extractor, other languages module-only fallback).
5. Tier 2 Python call-graph resolver (constructor + ``self``/``this``
   resolution; same-file 0.95 / cross-file 0.8 confidence).
6. FastAPI + Flask + Express framework extractors (Tier 3
   backend); ``code_route`` nodes + ``routes_to`` edges.
7. React framework extractor + cross-stack ``code_endpoint`` nodes
   (Tier 3 frontend); ``at_endpoint`` + ``renders`` edges.
11-13. ``/code`` UI: landing + project overview + function detail
   with 2-hop ego-network + cross-stack sitemap. New top-bar tab.
14. Seven new code skills: ``mnemo:explore-codebase``,
   ``mnemo:trace-call``, ``mnemo:trace-route``,
   ``mnemo:explain-design``, ``mnemo:debug-with-code``,
   ``mnemo:why-is-this-here``, ``mnemo:impact-analysis``.

### Deferred to follow-on point releases

- **Phase 8 -- Django framework extractor.** FastAPI / Flask /
  Express cover the dominant Python and Node webdev surfaces;
  Django lands in v2.0.1 alongside the JS / TS / Go Tier 2
  resolvers (left out of phase 5).
- **Phase 9 -- Git-log ingestion + auto-linker.** The
  ``references_function`` / ``motivated_by`` / ``closed_by``
  schema is in place (phase 1) and the ``mnemo:why-is-this-here``
  skill is wired against it; the ingester slots in cleanly in
  v2.0.1. Until then the skill falls back to ``git log -L``.
- **Phase 10 -- Per-file incremental watcher.** Current full
  reindex flow handles real-world repos under a few thousand
  files; the per-file debounced watcher is a v2.0.x performance
  upgrade once the indexing budget bites in production.
- **Phase 15 -- Migration banner for pre-v2.0 sources.** First
  daemon start post-2.0 would benefit from a "your existing
  ``memory_dir`` registration looks like a ``code_repo`` -- want
  to reclassify?" banner. The auto-router that powers it is
  shipped; the UI surface lands in v2.0.x.

Full test suite: 604 passing, 2 skipped, 0 failing.
Ruff: clean. Format: clean.

## [Unreleased]

### Added (v2.0 phase 1 -- schema migration)

The structural foundation for v2.0's code-intelligence work. Phase 1
is schema-only: every later phase plugs a real producer into one of
these slots.

- **Two new source kinds: ``code_repo`` and ``docs_dir``.**
  ``code_repo`` is the tree-sitter-indexed shape (the parser arrives
  in phase 3-4); ``docs_dir`` is a markdown harvest without the
  frontmatter discipline ``memory_dir`` requires. ``register_source``
  now accepts both. Existing kinds (``memory_dir``, ``claude_md``,
  ``plan_dir``, ``transcripts``) are unchanged.
- **New ``commit`` node type.** Holds one node per git commit
  ingested from a ``code_repo`` source. Wired up by phase 9's
  ``git log`` walker; the schema is in place now so subsequent phases
  can write through it without an additional migration.
- **Three new edge relations -- the provenance family.**
  ``references_function`` (commit -> code_function it touched),
  ``motivated_by`` (commit -> ``memory_feedback`` / ``plan_doc`` that
  motivated it), and ``closed_by`` (``memory_feedback`` / ``plan_doc``
  -> commit that resolved it). Together they make the v2.0 headline
  capability -- "why is this function here?" -- queryable.
- **``edges.confidence FLOAT NOT NULL DEFAULT 1.0``.** Per-edge
  uncertainty so Tier 2 unresolved ``calls`` (0.5), Tier 3 framework
  matches (0.9), and auto-inferred provenance edges (0.6, bumped to
  0.9 on explicit commit-body reference) can carry a calibrated
  uncertainty into retrieval scoring. The column back-fills to 1.0
  via the standard ``_ensure_columns`` migration path so v1.x edges
  retain their bit-for-bit-identical behavior.
  (``daemon/mnemo/store.py``)

### Changed

- **``scan_source`` yields nothing when include patterns are empty.**
  Phase 1 safety: until phase 3-4 wire a tree-sitter parser, a
  freshly-registered ``code_repo`` source must not silently walk every
  file with the markdown parser. The new invariant -- empty include
  set means "nothing to walk" -- pairs with phase 2's auto-router,
  which populates the right include set when registering a code
  source. (``daemon/mnemo/ingest.py``)

### Added (v2.0 phase 2 -- auto-router + dry-run preview + safety ceiling)

The structural fix for the Duyen-class registration mistake: every
new source goes through an auto-router that classifies the path,
shows a per-extension breakdown, and refuses to write without
explicit user confirmation.

- **``mnemo.auto_router`` module.** ``preview(path) -> PreviewResult``
  scans the filesystem and proposes one of ``code_repo`` /
  ``memory_dir`` / ``docs_dir`` (or ``None``) with a confidence label
  (``high`` / ``medium`` / ``low``). Heuristics, in order:
  1. ``.git/`` dir + >= 1 recognized source file -> ``code_repo``.
  2. >= 1 markdown with frontmatter ``type:`` -> ``memory_dir``.
  3. >= 2 plain markdowns + 0 source files -> ``docs_dir``.
  4. Otherwise -> ``(None, "low")``; user must pick ``--kind``
     explicitly.
  Side-effect-free; the module imports nothing from store, server,
  or ingest. The walker skips a curated set of build / cache / VCS
  dirs (``DEFAULT_SKIP_DIRS``) so the count reflects actual source
  trees, not ``node_modules`` / ``.venv`` / ``target`` etc.
- **``POST /v1/sources/preview``.** HTTP surface for the auto-router.
  Returns the proposed kind + breakdown + ceiling flag without
  touching the DB. ``{ path, force? }`` body; ``404`` on missing path,
  ``422`` on missing ``path`` field.
- **CLI: ``mnemo source add <path>`` without ``--kind``.** Runs the
  auto-router, prints the breakdown, and prompts for confirmation
  (``y/N``). ``--yes`` skips the prompt for scripts; ``--force``
  bypasses the safety ceiling. Explicit ``--kind`` skips the
  auto-router entirely; the existing kind enum (``memory_dir`` etc.)
  is unchanged plus the v2.0 additions (``code_repo``, ``docs_dir``).
- **50,000-file safety ceiling.** If the auto-router counts more than
  ``SAFETY_CEILING`` recognized source files (after default
  skip-dirs), the CLI and the API both refuse to write. ``--force``
  on the CLI / ``force: true`` on the API overrides. Prevents the
  Duyen pattern -- accidentally registering a massive code repo as
  ``memory_dir`` -- at v2.0 scale.
- **UI: dry-run preview on the Add Source modal.** Typing a path
  debounce-triggers a ``POST /v1/sources/preview`` and renders a
  panel above the Kind dropdown showing the proposed kind +
  per-extension breakdown + a "Use suggested" button. The ceiling
  warning surfaces an inline ``I understand`` checkbox that maps to
  ``--force`` on submission.

### Tests (phase 2)

- ``tests/unit/test_auto_router.py`` -- 25 tests covering
  ``propose_kind`` heuristics, ``scan_path`` (skip-dirs, frontmatter
  detection, file-counting, single-file handling), the full
  ``preview`` entry point, and the safety ceiling.
- ``tests/integration/test_v1_sources_preview.py`` -- 8 tests for the
  HTTP surface including a side-effect-free regression guard.
- ``tests/unit/test_cli.py`` -- 8 new ``test_cli_source_add_*`` tests
  covering each kind auto-route, ``--yes`` / interactive prompt
  paths, ``--force`` ceiling override, and the explicit ``--kind``
  override.

### Tests (phase 1)

- ``tests/unit/test_v2_schema.py`` -- 20 tests covering the four
  schema additions and the scan-safety guard rail. All v1.x suites
  continue to pass unmodified.

Combined: phase 1 + 2 -> 478 -> 520 passing tests, 0 failing.

### Added (v2.0 phase 3 -- tree-sitter grammar bundle + lazy loader)

The library layer that Tier 1 / 2 / 3 ingestion will sit on top of.
Phase 3 is grammar infrastructure only -- no source code is actually
parsed until phase 4 plugs in the ingester.

- **``mnemo.parsers.tree_sitter`` module.** Single entry point
  (``get_parser(language) -> tree_sitter.Parser``) hides three sources
  of churn from callers: the capsule-to-``Language`` conversion that
  changed across the 0.21 / 0.22 / 0.23 binding releases; the
  per-package quirks (``tree-sitter-typescript`` exposes
  ``language_typescript()`` and ``language_tsx()`` instead of
  ``language()``; ``tree-sitter-markdown`` exposes both block and
  inline grammars); and the bundled-vs-lazy split.
- **Bundled launch set:** ``python``, ``javascript``, ``typescript``,
  ``tsx``, ``go``, ``json``, ``yaml``, ``markdown``. These wheels are
  direct dependencies so first run works offline. The set covers
  every language Tier 2 (semantic call graph, phase 5) needs plus
  config / docs surfaces for the ``/code`` UI.
- **Lazy set:** ``rust``, ``java``, ``c``, ``cpp``, ``ruby``, ``php``,
  ``c_sharp``, ``kotlin``, ``swift``, ``bash``. Not bundled; the
  loader names the right pip package in the
  ``GrammarNotAvailableError`` so users can copy-paste the install
  command. Rounds out the 16-grammar Tier 1 set the design promises.
- **Extension dispatch (``language_for_extension``).** Maps
  ``.py`` -> ``python``, ``.tsx`` -> ``tsx``, ``.jsx`` ->
  ``javascript``, etc. Case-insensitive so ``Path.suffix`` on Windows
  resolves correctly. Phase 4's ingester walks files and routes via
  this helper.
- **Parser cache.** ``get_parser`` caches by language; repeated calls
  return the same ``Parser`` so downstream code can compare ``is`` for
  identity.
- **``paths.grammars_dir()``.** Reserved under ``mnemo_home() /
  "grammars"`` for future lazy-downloaded wheels (a v2.0.x feature).
  ``ensure_runtime_dirs()`` creates it on first launch.

### Dependencies added

```
tree-sitter>=0.23
tree-sitter-python>=0.23
tree-sitter-javascript>=0.23
tree-sitter-typescript>=0.23
tree-sitter-go>=0.23
tree-sitter-json>=0.23
tree-sitter-yaml>=0.7
tree-sitter-markdown>=0.4
```

### Tests (phase 3)

- ``tests/unit/test_tree_sitter.py`` -- 17 tests covering the
  bundled / lazy registries, extension dispatch (case sensitivity,
  TSX disambiguation), end-to-end parse sanity for Python /
  TypeScript / TSX / Markdown, the unknown-language path, the
  lazy-grammar install-hint path, and the parser cache.
- ``tests/unit/test_paths.py`` -- 2 new tests for ``grammars_dir()``
  and the ``ensure_runtime_dirs()`` extension.

Combined: phase 1 + 2 + 3 -> 478 -> 539 passing tests, 0 failing.

### Added (v2.0 phase 4 -- Tier 1 universal code_repo ingestion)

The first phase that produces real code-graph nodes. Tier 1 covers
language-structure extraction: one node per file, one per top-level
declaration, one per class method, plus three structural edge types
(``defines`` / ``method_of`` / ``imports``).

Tier 2 (cross-file call resolution) and Tier 3 (framework extractors)
land in phases 5-8.

- **Four new node types.** ``code_module`` (one per source file),
  ``code_function`` (top-level function), ``code_class`` (top-level
  class), ``code_method`` (method on a class). All four go through
  ``Node.new()`` and the standard ingest path -- they're indexed,
  retrievable, and BASE-aware just like memory_* types.
- **Three new edge relations.** ``defines`` (module -> top-level
  declaration), ``method_of`` (method -> containing class),
  ``imports`` (module -> module, best-effort cross-file). Inferred
  edges carry ``confidence`` so retrieval can downweight uncertain
  links: ``imports`` lands at 0.9 (high confidence inside the file's
  AST, lower than 1.0 because the cross-file resolution is
  shallow / single-segment match).
- **``mnemo.parsers.code`` module.** Walks a tree-sitter AST and
  emits :class:`CodeUnit` records. Languages with a structural
  extractor at launch: Python (top-level defs, classes, methods
  including decorated ones, docstring -> description, imports).
  Other bundled languages (JS / TS / TSX / Go / JSON / YAML /
  Markdown) get a module-only fallback so the file's existence
  stays queryable; per-language extractors for those land in
  follow-on phases.
- **``mnemo.ingest.parse_code_file``.** New dispatch path: a
  ``code_repo`` source maps each file through the tree-sitter
  extractor and yields multiple :class:`ParsedFile` records (one
  per :class:`CodeUnit`). Edge intent (children / parent / imports)
  travels in ``frontmatter_json`` under a ``code_unit`` key.
- **``reindex`` post-pass.** After the upsert loop, code units'
  edge intent gets resolved against the freshly-populated graph.
  ``defines`` and ``method_of`` are within-file and always
  resolve; ``imports`` is best-effort -- unmatched targets
  silently produce no edge so stdlib / pip-installed imports
  don't pollute the graph with dangling pointers.
- **``code_repo`` default include patterns.** A registered
  ``code_repo`` source with no user-supplied include set walks the
  bundled tree-sitter extensions (``*.py``, ``*.ts``, ``*.tsx``,
  ``*.js``, ``*.go``, ``*.json``, ``*.yaml``, ``*.md``, ...). The
  walker reuses ``auto_router.DEFAULT_SKIP_DIRS`` so ``.git`` /
  ``node_modules`` / ``__pycache__`` / etc. never reach the
  extractor.
- **Line-range source_paths.** Declaration nodes use
  ``<file>:<start>-<end>`` as their ``source_path`` so two same-name
  functions in the same file (overloads, conditional definitions)
  get distinct keys. ``paths.path_under_source`` strips the suffix
  before path comparison so reconciliation + cascade delete continue
  to work correctly. Modules keep the bare file path.
- **Body truncation.** Function and module bodies > 60 lines get a
  ``... (N more lines)`` trailing marker so retrieval hits don't
  blow the token budget on a 5,000-line file.

### Tests (phase 4)

- ``tests/unit/test_v2_schema.py`` -- 8 new tests covering the four
  code node types and the three structural edge relations.
- ``tests/unit/test_parsers_code.py`` -- 16 tests covering the
  Python extractor (decls, decorated methods, docstrings, imports,
  body truncation, line-range source_paths) and the module-only
  fallback for JSON / Markdown / JS / unknown.
- ``tests/unit/test_ingest_code_repo.py`` -- 10 tests covering the
  ingest wiring: default include, scan_source dispatch, skip-dirs
  passthrough, and the reindex edge post-pass for ``defines`` /
  ``method_of`` / ``imports``.

Combined: phases 1 -> 4 advance 478 -> 573 passing tests, 0 failing.

### Added (v2.0 phase 5 -- Tier 2 call-graph resolver)

The flagship Tier 2 capability: "where is ``<function>`` called from?"
finally returns correct answers. Built around a Stack-Graphs-inspired
scope resolver that walks the freshly-populated Tier 1 graph to
match each call site with its callee.

- **``calls`` edge relation.** Caller function / method -> callee
  function / method / class (the constructor case). Inferred edges
  carry calibrated confidence: 0.95 for within-file resolution and
  0.8 for cross-file resolution via the ``imports`` edge. The design
  pegs unresolved calls as "no edge" -- best-effort retrieval beats
  fabricated edges.

- **``mnemo.parsers.code.CallSite``.** New dataclass capturing a
  recorded call expression: ``callee_name``, ``receiver`` (``None``
  for free calls, ``"self"`` / ``"this"`` / ``"cls"`` for method
  calls, or an identifier for ``module.f()`` qualified calls), and
  the source line.

- **Python call-site extraction.** ``_python_call_sites`` walks each
  function / method body and collects ``call`` AST nodes. Recursive
  through nested control flow (``if`` / ``for`` / ``with`` /
  comprehensions) but NOT through nested function / class
  definitions -- those have their own units and their own
  ``call_sites``. Chained receivers (``a.b.c.method()``) are
  reduced to the outermost identifier so the resolver can still
  match against imports.

- **``mnemo.parsers.scope`` module.** The Tier 2 resolver.
  :func:`resolve_calls` builds a one-pass index of the code graph
  (source_path -> Node, (module, name) -> Node, method_of /
  imports lookups), then walks each touched node's call sites
  applying three rules in order:

  1. ``receiver in {self, this, cls}`` -> walk ``method_of`` to
     the enclosing class, match by callee name on its methods.
  2. ``receiver is None`` -> match against the enclosing module's
     top-level declarations (functions + classes; the latter
     handles constructor calls like ``Session()``).
  3. ``receiver`` matches an imported module name -> walk the
     ``imports`` edge to the target module and match by callee
     name on its declarations.

  Self-edges (a recursive function's name matching itself) are
  suppressed so the graph stays clean.

- **Reindex post-pass extension.** After Tier 1 edges (``defines``,
  ``method_of``, ``imports``) are wired, the reindex pipeline
  invokes :func:`scope_resolver.resolve_calls` with the same
  touched-node batch. The resolver hits the just-populated graph so
  same-run cross-file resolution works end-to-end (no second
  reindex needed).

### Tests (phase 5)

- ``tests/unit/test_v2_schema.py`` -- 2 new tests for the ``calls``
  edge relation and confidence persistence.
- ``tests/unit/test_parsers_code.py`` -- 7 new tests covering the
  ``CallSite`` dataclass shape, free / self / qualified call
  capture, constructor detection, and nested-call attribution.
- ``tests/unit/test_ingest_code_repo.py`` -- 7 new tests for the
  end-to-end resolution: same-module free call, ``self.method``,
  constructor -> class, cross-file via imports, unresolved (no
  edge), and confidence levels for same-file vs cross-file.

Combined: phases 1 -> 5 advance 478 -> 589 passing tests, 0 failing.

### Deferred to follow-on phases

- **JavaScript / TypeScript / Go resolvers.** The design promises
  Tier 2 across all three; phase 5 ships Python end-to-end and
  leaves the resolver framework / call-site extraction stubs for
  these three to land in a follow-on commit. Tier 1 already
  produces ``code_module`` nodes for these languages so the
  graph isn't blocked on them.

### Added (v2.0 phase 6 -- Tier 3 backend framework extractors)

FastAPI, Flask, and Express route extraction. The first Tier 3
phase wires the framework idioms each of those backends uses into
graph nodes + edges, setting up the cross-stack sitemap that
lands when phase 7 ships the React / Next.js side.

- **``code_route`` node type.** One node per detected route
  declaration. Carries ``framework`` (``fastapi`` / ``flask`` /
  ``express``), HTTP method (uppercased), and path on its
  ``code_unit`` intent block. The display ``name`` is
  ``METHOD path`` (e.g. ``GET /api/users``) so retrieval hits
  read naturally.
- **``routes_to`` edge relation.** Route -> handler function.
  Inferred edges with confidence 0.95 -- within-file resolution
  is high-confidence by construction (the extractor matched the
  decorator + handler in the same parse). The post-pass wires
  the edge by source_path lookup.
- **``mnemo.extractors.fastapi``.** Matches
  ``@<receiver>.<method>(<path>, ...)`` decorators on top-level
  functions where the method name is one of GET / POST / PUT /
  DELETE / PATCH / HEAD / OPTIONS / TRACE. Stacked decorators on
  the same handler each emit their own route. The receiver name
  is intentionally permissive (``app`` / ``router`` / ``api`` /
  ``v1`` are all valid in real codebases).
- **``mnemo.extractors.flask``.** Matches
  ``@<receiver>.route(<path>, methods=[...])`` decorators on
  top-level functions. Default method is GET when ``methods``
  is omitted; multi-method lists fan out to one route per verb.
  ``@app.route`` and ``@blueprint.route`` are detected the same
  way (any receiver name).
- **``mnemo.extractors.express``.** Matches top-level
  ``<receiver>.<method>(<path>, <handler>)`` call expressions
  where the method name is GET / POST / PUT / DELETE / PATCH /
  HEAD / OPTIONS / ALL / USE. JavaScript handler resolution is
  deferred to phase 7 (when JS Tier 1 ships): the route node is
  emitted but ``handler_source_path`` stays None, so
  ``routes_to`` doesn't wire yet for Express. The endpoint
  surface is still there for phase 7's React-side join.
- **Framework dispatch in ``parsers.code.extract``.** After Tier 1
  extraction runs, the appropriate set of framework extractors
  (per ``FRAMEWORK_EXTRACTORS`` in ``mnemo.extractors``) walks
  the same tree and emits Tier 3 units. A broken extractor is
  caught defensively -- it can never crash the reindex.

### Tests (phase 6)

- ``tests/unit/test_v2_schema.py`` -- 3 new tests for the
  ``code_route`` node type and the ``routes_to`` edge.
- ``tests/unit/test_extractors.py`` -- 12 new tests: 6 for the
  FastAPI extractor (GET, POST, APIRouter, name shape,
  no-decorator regression, stacked decorators), 3 for Flask
  (default GET, ``methods`` kwarg, blueprint), 2 for Express
  (``app.get`` and ``router.post``), and 1 end-to-end integration
  test asserting the ``routes_to`` edge appears after reindex.

Combined: phases 1 -> 6 advance 478 -> 604 passing tests, 0 failing.

## [1.2.1] - 2026-05-11

**Closing the 1.2.x line.** A real-use test of v1.2.0 against a
multi-project store turned up that most "common query returns
nothing" cases trace back to a single bug: the strict
project-isolation hard-filter dropped nodes whose ``project_key``
is ``None`` (CLAUDE.md global memory, plan_docs, and any
cross-cutting entry that didn't pick up a project_key). The filter
treated ``None != active_project`` as True and silently filtered
them out -- exactly the opposite of what you want for global
memory.

### Fixed

- **Strict project isolation no longer hides ``project_key=None``
  nodes.** Global memory (CLAUDE.md, plan_docs, any cross-cutting
  entry without an assigned project) now surfaces in every project's
  queries by default. Pre-fix, you needed to either (a) flag every
  global node ``base: true`` or (b) flip to ``isolation_mode=boost``
  to get them back; both were undocumented workarounds. The fix
  matches the spirit of the v1.1 design: "BASE for cross-project,
  project_key for per-project, NULL is the natural cross-cutting
  bucket." (`daemon/mnemo/retrieve.py`)
- **``budget_tokens`` floor raised from 1 to 20.** Below 20 the
  first hit's ``[mnemo:<uuid>] [<type>] <description>`` line can't
  fit and ``compress_to_budget`` returns the empty list -- a
  silent zero that masks the real cause. Clients now get an HTTP
  422 instead of a confusing empty success. (`daemon/mnemo/api_schemas.py`)

### Tests

- ``test_query_strict_isolation_keeps_project_key_none_nodes`` --
  regression test for the filter fix. Active project + strict mode
  + 3 nodes (in-project / other-project / no-project_key);
  asserts in-project and no-project_key survive, other-project is
  filtered.
- ``test_query_budget_below_floor_rejected`` -- 422 on
  ``budget_tokens=10``; 200 on the exact floor of 20.
- ``test_query_validation`` retains its 422 assertion on
  ``budget_tokens=0``.

### Not in scope (deferred)

The full set of silent-zero failure modes (16 cases probed) is
written up in the memory note ``feedback_mnemo_v12_build_lessons``
+ a fresh ``feedback_mnemo_silent_zero_modes`` for future
diagnostic work. v1.3 / v2.0 candidates:

- Diagnostic ``debug=true`` flag on ``/v1/query`` that returns
  pre-filter / post-isolation / post-MMR counts so users can see
  where their hits got lost.
- "filtered N of M" hint surfaced in the UI when isolation drops
  hits.
- ``/v1/projects/resolve`` auto-suggestion when an explicit
  ``project_key`` doesn't match any indexed nodes.

## [1.2.0] - 2026-05-11

**Learning to Listen.** mnemo now closes the personalization loop:
every retrieval result can carry user feedback (explicit thumbs in
the UI / CLI, implicit detection of re-asked queries), and a
coordinate-descent auto-tuner reads those signals to nudge the
6-term scoring weights toward what THIS user actually finds useful.
Plus MMR diversification of the top-K, a clean version cliff on the
1.1-era 308 redirects, and HTTP-driven memory creation via
`POST /v1/nodes` so adapters like the VS Code "Add Note" command no
longer have to go through the filesystem.

8 phases, ~3 weeks. Full design: `docs/plans/2026-05-10-mnemo-v1.2-design.md`.

### Added

#### Feedback collection (phases 1-3)

- **`feedback_event` table** with FK cascades on `query_id` +
  `node_id`, UNIQUE on `(query_id, node_id, reason)` for idempotency.
  Indexes on each of the three filter dimensions.
- **`POST /v1/feedback`** writes one feedback row. Idempotent on the
  triple (double-clicks safe). `signal` is optional -- the daemon
  defaults from `reason` via `signal_for_reason`
  (thumbs_up=+1, thumbs_down=-1, cite_copied=+0.5, inferred_requery=-0.5).
- **`GET /v1/feedback?query_id=…&node_id=…`** lists events
  newest-first; requires at least one filter param.
- **Inferred-re-query detector** fires on every `POST /v1/query`:
  if a recent prompt has cosine >= 0.85 with the new prompt inside
  the configurable window (default 300s), write
  `signal=-0.5, reason='inferred_requery'` against the older
  query's top-N retrieved hits. Treats the re-ask as evidence the
  earlier hits missed.
- **Thumbs up/down buttons on every hit** in the UI. Click POSTs to
  `/v1/feedback` with optimistic state flip + rollback on error.
  Defined as the `hitsFeedback` Alpine factory in `base.html` so it
  survives HTMX swaps. Toggles between up/down still write two rows
  (one per reason) -- the auto-tuner uses the strongest signal.
- **`queries.embedding BLOB`** column persists the query vector so
  the re-query detector can cosine-compare future prompts against
  this one.
- **`queries.score_components TEXT`** column persists the per-hit
  unweighted 6-term breakdown so the auto-tuner can rescore with
  alternative weights without re-running the embedder.

#### Retrieval quality

- **MMR re-rank** on the top-K (`mnemo/rerank.py::mmr_select`).
  Penalizes near-duplicate candidates of already-picked hits so
  the top-5 stops being five paraphrases of the same node. Default
  `mmr_lambda = 0.7`; 1.0 bypasses MMR for the pre-1.2 behavior;
  0.0 is pure diversity. ~0.5ms overhead on top of existing scoring.

#### Auto-tuner (phases 5-6)

- **`mnemo/retune.py`** with `best_feedback_signal`,
  `rescore_with_weights`, `mrr`, `coordinate_descent`, and the
  high-level `retune(store, min_queries=30)` entrypoint.
  Optimizer: nudges of {-0.10, -0.05, +0.05, +0.10} across the 6
  keys, up to 4 passes, EPS=0.001 acceptance, 60s wall-clock cap,
  time-ordered 80/20 train/val split.
- **`POST /v1/retune`** returns a full `RetuneReportOut`
  (proposed/current/diff weights, before/after MRR for train+val,
  sample sizes, iteration count, log). Preview-only -- never
  mutates `/v1/config`. The UI's Apply button posts the proposed
  scoring through the existing `PUT /v1/config`.
- **`mnemo retune` CLI** with `--apply` / `--min-queries N` / `--json`.
  Renders a readable column-aligned diff + before/after MRR + log.
- **"Auto-tune from feedback" panel on `/settings`** with Run /
  Discard / Apply buttons, MRR grid, diff table with changed rows
  highlighted, collapsible optimizer log.
- **`Config.retune_min_queries: int = 30`** threshold under which
  retune refuses to optimize (MRR is too noisy on small datasets).

#### Housekeeping (phase 7)

- **`POST /v1/nodes`** HTTP-driven memory creation. Validates
  `type` and `source_kind` against the store enums; auto-fills
  synthetic `http://api/<uuid>` source_path when omitted; embeds
  eagerly so the new node is searchable immediately. The VS Code
  "Add Note" command (palette `mnemo.addNote`) now POSTs through
  this endpoint instead of opening the dashboard.

### Removed

- **Legacy 308 redirect bridge.** v1.1 had a one-version-only
  middleware that translated `/health` -> `/v1/health` (and 6 more
  un-versioned roots). v1.2 ships the cliff -- those paths now
  return 404. The `X-Mnemo-Api-Version: 1` header has been
  stamping every response throughout v1.1.x to give adapters time
  to migrate.

### Changed

- **`Store.log_query(..., embedding=None, score_components=None)`**
  -- two new optional kwargs; backward compatible (pre-1.2 callers
  who omit them get NULL columns and downstream filters skip them).
- **Three new `Store` helpers**: `recent_queries_with_embeddings`
  (filter by time window + non-null embedding for the re-query
  detector), `recent_queries_with_components` (filter by
  non-null components + min feedback count for the auto-tuner),
  `get_chunk_embeddings` (bulk-fetch chunk vectors for the MMR
  pool via a CTE-VALUES JOIN).
- **`retrieve.query`** now (a) computes + logs the unweighted
  6-term components for the top pool, (b) calls the inferred-
  re-query detector before the audit-log write so the current
  query is never compared to itself, (c) runs MMR over the top
  `max(k*2, 20)` candidates when `mmr_lambda < 1.0`.

### Config additions

Four new keys on `Config` (all settable via `PUT /v1/config` and
the settings.json file):

- `requery_window_seconds: int = 300`
- `requery_cosine_threshold: float = 0.85`
- `requery_top_n_hits: int = 3`
- `mmr_lambda: float = 0.7`
- `retune_min_queries: int = 30`

### Tests

Roughly +60 tests across 8 phases (455+ pass total, 2 skipped, ruff
clean):

- 17 feedback_event store / endpoint tests (phase 1).
- 6 inferred-re-query detector unit tests + 4 store-helper tests
  (phase 2).
- 3 UI thumb-button render tests (phase 3).
- 10 MMR + `get_chunk_embeddings` tests (phase 4).
- 14 retune unit tests (math + optimizer + entrypoint) + 2 CLI
  tests (phase 5).
- 3 `/v1/retune` HTTP tests + 1 settings-panel render test (phase 6).
- 15 redirect-removal tests + 6 `POST /v1/nodes` tests + ~10
  retrofit edits to legacy-path callers (phase 7).

### Upgrade notes

- v1.1.x adapters that called un-versioned paths (`/health`,
  `/sources`, etc.) must now call `/v1/...` directly. The 308
  bridge is gone.
- VS Code extension "Add Note" command behavior changed -- previously
  opened the dashboard, now prompts for type/name/body inline and
  creates the node via `POST /v1/nodes`.
- Existing audit-log rows (pre-1.2) have NULL `embedding` and NULL
  `score_components` columns; they're invisible to the re-query
  detector and the auto-tuner but otherwise queryable as before.

### Open questions deferred to v1.3 / v2.0

- Cross-encoder re-rank (v1.3, paired with a quality-first scoring mode).
- Nightly auto-retune cadence (v1.3, once on-demand proves itself).
- NDCG@K objective (when labeled dataset gets large enough).
- Reciprocal Rank Fusion retrieval (v1.3).
- Code-graph parsing + sitemap (v2.0).
- Chat surface + MCP shim (v3.0).

## [1.1.1] - 2026-05-11

**Hotfix.** Two source-management bugs surfaced in real use after the
1.1.0 release: removing a source left its nodes orphaned in the graph
forever, and the Reindex button could fire concurrent runs after a
page navigation. Both are fixed here without any API contract change
beyond two additive endpoint responses.

### Fixed

- **`DELETE /v1/sources` now cascades node deletion.** Previously the
  endpoint only deleted the row from the `sources` table; every node
  ingested from the removed source's path lingered in the graph
  forever because the reindex orphan-sweep only inspects nodes whose
  path matches a *still-registered* source. The UI's confirmation
  copy ("Existing nodes from this source will be removed on the next
  reindex") was actively misleading. Reported visually as "wipe all
  graph and replace with all README files" when a non-memory tree
  was mistakenly registered as `memory_dir`.
- **Concurrent `POST /v1/reindex` requests no longer race.** The
  daemon now serializes reindex requests with an in-process lock. A
  second request while another is in-flight returns `HTTP 409` with
  `{"error": "reindex_in_progress", "started_at": <ts>}`. The UI's
  client-only "running" flag was wiped on every page reload /
  navigation, so a user navigating away and back could fire a second
  reindex on top of an in-flight one.

### Added

- **`mnemo source orphans [--prune]`** CLI command. The cascade fix
  above stops *future* removals from leaking, but users who removed a
  source under the pre-1.1.1 behavior still have the leftover nodes in
  their store. Running `mnemo source orphans` lists every node whose
  `source_path` matches no registered source; `--prune` deletes them
  along with their vector chunks. Output is human-readable by default;
  `--json` available for scripts.
- **`mnemo source remove`** now prints the cascade count, so the user
  can verify the cleanup actually fired (`removed: /path  (3 nodes
  cleaned up)`).
- **`GET /v1/reindex/status`** returns `{"running": bool, "started_at":
  int|null}` so the Sources page can restore the disabled-button state
  after navigation. The UI polls this every 2 s when a reindex is
  in-flight and reloads once it flips back to idle.
- **`DELETE /v1/sources` response gained a `removed` field**
  (`{"ok": true, "removed": N}`) reporting the cascade count. The
  Sources page now shows "Source removed (N nodes cleaned up)" in
  the success toast.

### Changed

- **`mnemo.paths.path_under_source`** is now a public helper used by
  both the ingest reconciler and `Store.remove_source` so the two
  layers agree on what "owned by this source" means.
- **`Store.remove_source` returns `int`** (count of cascaded nodes).
  Previously returned `None`. Callers that ignored the return value
  still work.
- **`Store.find_orphan_nodes`** new method — returns nodes whose
  `source_path` matches no registered source (the inverse of the
  cascade). Used by the `mnemo source orphans` CLI.
- **Sources page modal copy** updated to truthfully describe the
  cascade ("removes every node that was ingested from it").

### Upgrade notes

If you removed a source under v1.1.0 or earlier and you still see its
old nodes in the graph / Nodes page, that's the pre-1.1.1 leak. After
upgrading, run::

    mnemo source orphans          # see what's left
    mnemo source orphans --prune  # clean them up

then restart the daemon so the reindex picks up the cleaner state.
For our reporter (the `D:\Repository\Duyen` case): after upgrade, those
README nodes leftover from the misregistered `memory_dir` will be
listed and cleanable in one command.

### Tests

- `test_remove_source_cascades_descendant_nodes` -- unit, store layer.
- `test_remove_source_cascade_respects_claude_md_exact_match` -- unit.
- `test_remove_source_unregistered_returns_zero` -- unit (idempotency).
- `test_find_orphan_nodes_returns_unregistered_sources` -- unit.
- `test_find_orphan_nodes_empty_when_all_match` -- unit.
- `test_find_orphan_nodes_no_sources_means_everything_orphan` -- unit.
- `test_delete_source_cascades_nodes_via_http` -- integration, full
  ingest-then-DELETE round trip.
- `test_reindex_status_idle_when_no_run_in_flight` -- integration.
- `test_reindex_status_reports_running_mid_flight` -- integration,
  uses a blocked-event monkeypatch on `ingest.reindex`.
- `test_concurrent_reindex_returns_409_with_started_at` -- integration.
- `test_reindex_lock_released_on_error` -- integration (lock cleanup
  even when ingest raises).
- `test_cli_source_remove_reports_cascade_count` -- CLI.
- `test_cli_source_orphans_empty` -- CLI.
- `test_cli_source_orphans_lists_then_prunes` -- CLI, end-to-end
  reproduction of the pre-1.1.1 leak path.
- `test_cli_source_orphans_json` -- CLI.

## [1.1.0] - 2026-05-10

**Beyond Claude Code.** mnemo now serves any IDE / any LLM SDK / any
common workflow, while staying local-first, token-budgeted, and
citation-back. Everything in this release is additive on top of the
v1.0.x line; existing Claude Code plugin users see no breakage.

### Added

#### Public protocol (versioned)

- **All HTTP endpoints under `/v1/...`** with auto-published OpenAPI
  spec at `/v1/openapi.json`. Internal UI/HTMX routes excluded from
  the spec via `include_in_schema=False`.
- **`X-Mnemo-Api-Version: 1` header** on every response so adapters
  can sanity-check the daemon they're talking to.
- **Legacy paths return 308** to their `/v1/...` equivalents
  (`/health`, `/sources`, `/reindex`, `/nodes`, `/query`, `/audit`,
  `/config`). Method + body preserved so adapters that haven't
  migrated keep working. The redirects are scheduled for removal in
  **v1.2**.
- **New endpoints:** `POST /v1/projects/resolve`,
  `GET|POST|DELETE /v1/projects/active`, `GET /v1/projects/known`,
  `PATCH /v1/sources`, `GET /v1/fs/suggest` (filesystem path
  suggestions for the UI).
- **`docs/protocol.md`** spec doc + canonical project_key derivation
  algorithm with a 40+ entry fixture file for cross-adapter drift
  detection.

#### Active-project state + project-key resolver

- Singleton `active_project` table with a hybrid contract: per-call
  `project_key` overrides the persisted active project; absence
  falls back to it.
- Active-project pill in the UI topbar with a popover for set /
  clear, accent-color when set.

#### Source patterns + management

- New `nodes.include` and `nodes.exclude` columns -- comma-separated
  gitignore-style globs -- compiled into `pathspec.PathSpec` at scan
  time. Defaults to `**/*.{md,markdown,txt,pdf}` for `memory_dir`
  sources; per-source overrides supported.
- `PATCH /v1/sources` for partial updates; UI `Add source` /
  per-row `edit` / `remove` flows on the Sources page with autocomplete
  for path (live filesystem suggestions + recents) and project_key
  (known-keys-from-DB).

#### File-format expansion

- New parser registry under `mnemo/parsers/`. Adding a format in
  v1.2+ is a 2-line change.
- **PDF parsing** via `pypdf`. Per-page `--- page N ---` headers so
  retrieval can cite specific pages. Corrupt PDFs degrade
  gracefully (log + empty body, no pipeline crash).
- **Plain text** (`.txt`, `.markdown`) parsing.

#### BASE knowledge + project isolation

- New `nodes.base` column. Frontmatter `base: true` flags a node as
  BASE. BASE nodes bypass project isolation and surface in every
  project's queries.
- `retrieve.query()` hard-filters to `(project_key == active OR
  base)` when an active project is set. Behavior gated by new
  `config.project_isolation_mode = 'strict' | 'boost'` (defaults to
  `strict`; `boost` restores v1.0 behavior).
- `Store.list_nodes` and `count_nodes` honor BASE inclusion. Nodes
  page type counts respect the project filter.
- BASE pill toggle on the node detail page; gold "base" badge in
  lists.

#### Workflow skills

- **`mnemo:plan`** (rigid, 6 phases): pull mnemo context ->
  brainstorm -> 2-3 approaches -> decisions -> emit
  `docs/plans/<date>-<topic>-design.md` -> done-criteria. Closes
  the gap between idea and `mnemo:implement-platform`.
- **`mnemo:retro`** (flexible, 4 phases): sweep recent activity ->
  propose 0-N candidate memory entries -> user triages
  accept / edit / reject -> write + reindex.
- **`mnemo:incident`** (rigid, 7 phases): severity + post-mortem
  stub -> pull priors -> stabilize BEFORE investigate -> RCA ->
  post-mortem doc -> promote durable lesson to memory_feedback.

#### `mnemo-middleware` Python package (PyPI)

- `clients/middleware-py/` with separate pyproject.toml. Single
  runtime dep: `httpx`. Provider SDKs are opt-in extras.
- **`retrieve_context(prompt, ...)`** helper. Returns a markdown
  block formatted like the Claude Code hook output. Always additive:
  daemon down / timeout / invalid JSON returns `""` so the caller
  drops the result into a system message unconditionally.
- **`patch(client, mode='auto'|'once'|'every')`** monkey-patcher
  with provider shims for OpenAI, Anthropic, Google (Gemini), and
  Ollama. `auto` (default) re-injects only on new conversations or
  topic shifts; `once` for persistent agents; `every` for one-shot
  evaluators. Anthropic shim emits `cache_control: ephemeral` on
  the system block when it's >= ~1024 tokens for the 90% cache
  discount.
- 20 unit tests against `httpx.MockTransport` + a fake openai-shaped
  client.

#### `mnemo-vscode` extension

- New `extensions/vscode/` TypeScript project. Ready to package
  with `vsce`; no marketplace publish in v1.1 (`.vsix` GitHub
  release artifact only -- marketplace is v1.2).
- Status bar pill (daemon health + active project), palette
  commands (Query / Add Note / Set Active Project / Open UI /
  Reindex), sidebar TreeView, **`@mnemo` chat participant** with
  slash subcommands `/recall`, `/sources`, `/add`. Hits stream as
  chat references with `[mnemo:<id>]` citations.

#### UI polish

- Custom-themed `<input type="checkbox">` + `<select>` (URL-encoded
  inline-SVG caret, `color-scheme: dark` for native popups).
- Source management table shows include / exclude patterns inline.
- Always-visible filter Clear button (disabled when no filter)
  instead of mounting/unmounting per toggle.

### Changed

- Default include patterns for memory_dir / plan_dir / transcripts
  widened to `**/*.{md,markdown,txt,pdf}`.
- `Store.count_nodes(project_key=...)` filter respects active
  project + BASE union.
- `_LegacyRedirectMiddleware` and `_ApiVersionHeaderMiddleware`
  added to the FastAPI app. Order matters: header middleware must
  be added **last** so it stamps headers on the inner middleware's
  308 short-circuit responses (captured the lesson in
  `feedback_starlette_middleware_order.md`).

### Fixed

- Filter empty-string normalization on the Nodes page
  (`?project=` no longer SQL-matches zero rows; route normalizes
  empty form values to None).
- Type-counts dropdown was showing global counts when the project
  filter was active. Now scoped to the project + BASE union.
- pathspec deprecation: switched from the deprecated
  `'gitwildmatch'` pattern style to `'gitignore'`.

### Hard rules (carry-over)

- No `Co-Authored-By` trailers on commits, ever.
- No emojis in code, docs, commits.
- Conventional commit prefixes.
- Daemon binds to `127.0.0.1` only.

### Migration notes

- The `nodes.base`, `sources.include`, `sources.exclude` columns
  are added by an idempotent SQLite migration on first daemon start
  after the upgrade. Existing nodes default to `base = 0`. Existing
  sources default to NULL include/exclude (treated as "use the kind
  default").
- Adapters can keep calling unversioned paths for the v1.1 series;
  in v1.2 these will be removed.

## [1.0.5] - 2026-05-10

Polish on top of 1.0.4. Three real bugs and two ergonomic upgrades.

### Fixed

- **Node-detail body would briefly show then disappear on page load.**
  ``x-data="nodePage({ raw: {{ node.body | tojson }} })"`` produced
  output where the JSON's inner ``"`` characters closed the HTML
  attribute prematurely, so Alpine saw an empty ``x-data`` and ``tab``
  was undefined -- which made ``x-show="tab === 'edit'"`` evaluate to
  false and hide the textarea. Switched the attribute to single
  quotes; Jinja's ``tojson`` already escapes apostrophes as
  ``'``, so the inner string is safe inside ``x-data='...'``.
- **Audit "Showing 1-25 of 129" pushed the right column down**, so
  TOP INTENTS sat 1rem lower than the first query. Moved the line
  above the dash-row and zeroed the ``query-log`` margin so both
  columns share the same first-row baseline.
- **Sliders had a misaligned thumb** at min/max, especially when
  zoomed. Replaced the browser-default range styling with explicit
  webkit/moz track + thumb styles so the thumb stays visually on the
  track at every position.

### Added

- **Stepper buttons** (``[−] [value] [+]``) on every Settings weight
  + default. Click steps the value by the natural increment for that
  field (0.05 for weights, 1 for k / recency, 50 for budget tokens),
  clamps to min/max, and rounds to mitigate JS float drift.
- Native number-input spinners are hidden when the field is inside a
  ``.stepper``; the explicit buttons are the only adjuster.

## [1.0.4] - 2026-05-10

UI polish release. Pages outside the dashboard now use the same
full-dive layout (hero, stat cards, multi-column grid). Body previews
render proper Markdown. Timestamps display in local time. Plus a few
alignment fixes carried over from 1.0.3 feedback.

### Added

- **Markdown body preview** on the node detail page (Edit / Preview
  tab toggle) and inside the graph side panel. Uses ``marked`` +
  ``DOMPurify`` from CDN; rendered output picks up dark-theme styling
  via the new ``.md-body`` class. Same renderer is reused across both
  pages -- no duplication.
- **Page hero** on Audit, Settings, Node detail, and Sources: title
  with gradient + subtitle + right-aligned actions area, mirroring the
  Dashboard's welcome header for visual consistency.
- **Audit page summary cards** at the top (total queries, hits
  delivered, avg hits/query, last query time) and a side rail with
  top-intent counts and the activity-window date range.
- **Node detail stat cards** (outgoing edges, incoming edges, body
  chars, last updated). The page now uses a 2-column main/aside grid
  with edges as a sticky side rail.
- **Local-time timestamps**: every Unix ``ts`` in the UI is rendered
  by a shared ``mnemoFormatTs(ts, fmt)`` helper into the user's
  locale. Server emits ``<time data-ts="...">`` tags; a single
  ``DOMContentLoaded`` pass + ``htmx:afterSwap`` hook converts them.
  Three formats: ``datetime`` (default), ``date``, ``relative``.

### Changed

- **Main content max-width** bumped from 1200px to 1600px so wider
  screens feel full instead of empty around the sides. Inner padding
  bumped to 2rem.
- **Settings page** restructured: full-dive hero with Save / Reset in
  the actions area, score-formula callout, then a 50/50 split between
  Scoring weights and Defaults -- both as ``dash-card``s with their
  own weight-grids.
- **Audit page** removed the ``max-width: 920px`` constraint that was
  keeping it narrower than the rest of the UI.
- **Graph side panel** widened to 380px so the markdown body preview
  has room to breathe.

### Fixed

- **Open node / Copy citation alignment** in the graph side panel.
  The two buttons used different box models (``<a>`` with padding vs
  ``<button>`` with padding + border), so they never lined up. New
  shared ``.btn-row`` class normalizes height + padding + border so
  any mix of ``<a>`` and ``<button>`` lines up cleanly.
- **Preview tab on node detail** sometimes rendered empty when
  ``marked`` / ``DOMPurify`` were still loading at Alpine init time.
  Render now retries on a short timer until both libs are hydrated.

## [1.0.3] - 2026-05-10

Bug-fix release for issues caught after 1.0.2 went out.

### Fixed

- **Graph node click did nothing** (no detail panel, no highlight).
  The inline ``x-data`` on ``.graph-pane`` defined methods using
  shorthand syntax that Alpine's expression parser was tripping on,
  silently failing to set up the component. Refactored into a
  named ``graphPane()`` factory function so x-data is just
  ``x-data="graphPane()"``. All state and methods (selectFromCanvas,
  copyCitation, typeColor) are now defined cleanly in one place.
- **Race condition between Cytoscape init and Alpine init**.
  The IIFE used to start before Alpine had hydrated, so
  ``Alpine.$data(graphRoot)`` returned ``undefined`` and clicks
  silently failed. Now wrapped in ``alpine:initialized`` so cy
  handlers only register after Alpine is ready.
- **Stale ``Alpine.$data(root)`` reference** in the post-1.0.2 graph
  script - ``root`` was never defined, threw on every node tap.
  Removed; replaced with the ``graphPane`` component's own methods.
- **Bell unread badge flickered on every page load** - the badge
  rendered before Alpine hydrated state from localStorage, briefly
  showing the wrong (or no) count. Added ``x-cloak`` so the badge
  is hidden until Alpine is ready.

### Added

- **Smooth page-load fade-in**: ``main`` containers animate in with
  a 240ms cubic-bezier translate+fade. Subtle but makes navigation
  feel less jarring.
- **Active navbar item now has an animated underline accent** that
  scales in when the page loads, so the active state is more
  noticeable.
- **Card hover micro-interaction**: stat cards and hit cards lift
  slightly and gain a soft shadow on hover (was just border color).
- **``prefers-reduced-motion``** honored everywhere - all
  animations and transitions collapse to ~0ms when the user has
  reduce-motion set.

## [1.0.2] - 2026-05-10

UI restructure release. Adds a dashboard, paginated lists, and a
notification history. Fixes several UI bugs from 1.0.1.

### Added

- **Dashboard at `/`** — overview screen with stat cards (memory,
  sources, learned connections, queries logged), a type-distribution
  bar chart, top connected nodes, recent queries, and a quick-search
  input.
- **`/nodes-page`** — dedicated nodes list with full-text search,
  filter by type and project, and pagination (25 per page).
- **Server-side pagination** on the audit log and the nodes list,
  rendered through a shared `_pagination.html` partial. Pagination
  preserves filter query params across pages.
- **Notification history** — bell icon in the topbar with an unread
  count badge. Click to open a dropdown of past toasts (last 50,
  localStorage-backed). Click "Clear" to wipe history.
- **Toast-after-reload** — `window.toastAfterReload(...)` queues a
  toast via sessionStorage so it shows after the next page load.

### Changed

- **Navigation restructure**: the topbar is now Dashboard / Nodes /
  Graph / Sources / Audit / Settings (was Search / Graph / ...).
  Search is a feature of the Nodes page, not its own item.
- **Active state fix**: when on a node detail page (`/node/<id>`),
  the navbar correctly highlights "Nodes".
- **Node detail page**: edges now render with the target/source
  node's badge + name (resolved server-side via the new
  `Store.get_nodes_by_ids` batched lookup), not just their truncated
  ID.

### Fixed

- **Graph 'Connected to' showed only colored dots** — the template
  bound to `n.name` but the Cytoscape node data field is `label`.
  Now also displays the type as a small mono label.
- **Connected-node click redirected away from the graph** — clicking
  an entry in the side panel's "Connected to" list now focuses that
  node on the canvas (animates pan + zoom + highlight + selects),
  rather than navigating to its detail page. The "Open node" CTA
  still goes to the detail page when you want it.
- **Reindex success toast disappeared instantly** — the page reload
  fired before the toast could render. Now uses
  `window.toastAfterReload()` so the toast surfaces after the new
  page loads.
- **Custom scrollbar inside dark panels** — thumb border now blends
  with the panel background instead of the page background, so the
  scrollbar doesn't have a halo around it inside cards / textareas /
  the graph detail panel.
- **Bell dropdown was empty + graph node click stopped working**
  (caught in self-test before push): a duplicate
  `const TOAST_HISTORY_KEY` declaration in two `<script>` blocks
  threw a SyntaxError that disabled all other UI scripts. Fixed by
  declaring it once, in the deferred head script.
- **Graph node click resolved to the wrong Alpine component** after
  the bell wrapper was added to the topbar:
  `document.querySelector('[x-data]')` returned the bell, not the
  graph pane. Now scoped to `.graph-pane` so node clicks correctly
  populate the side panel again.

## [1.0.1] - 2026-05-10

UI enhancement release. No backend changes.

### Added

- **Custom scrollbar styling**: thin, themed scrollbars across all
  scrollable surfaces (Webkit + Firefox via `scrollbar-color`). Track
  is transparent, thumb uses the muted border color and brightens to
  the accent on hover. Inside dark panels (cards, code blocks,
  textarea, the graph detail panel) the thumb border blends with the
  panel background instead of the page background.
- **Themed modal component** (`window.modal()`) that returns a
  `Promise<boolean>`. Drop-in replacement for `window.alert` /
  `window.confirm` with consistent dark-theme styling, escape-to-
  cancel, click-backdrop-to-cancel, and focus-trap on the confirm
  button. Supports `level: 'danger'` for destructive actions.

### Changed

- `settings.html` "Reset to defaults" now uses `window.modal()` with a
  danger-styled confirm button instead of the browser's `confirm()`.
  Going forward, every confirm/alert in the UI uses the themed modal.

### How to use

```js
const ok = await window.modal({
  title: 'Delete this node?',
  body:  'This is permanent.',
  confirm: { text: 'Delete', level: 'danger' },
  cancel:  { text: 'Cancel' },
});
if (ok) { /* user confirmed */ }
```

## [1.0.0] - 2026-05-10

First stable release. mnemo is a local-first knowledge memory system for
Claude Code: aggregate memory across projects, retrieve via hybrid
Graph-RAG, and inject budget-capped context on every prompt.

### Highlights

- **Hybrid Graph-RAG retrieval**: 6-term scoring (vector cosine + graph
  proximity + recency + intent-driven type priority + project scope +
  lexical overlap). 100% top-1 accuracy and MRR=1.000 on the curated
  benchmark.
- **Local-first**: SQLite + sqlite-vec, sentence-transformers MiniLM-L6
  (22 MB). No cloud, no API keys, no network calls.
- **Token-budgeted**: every retrieval ships <= 800 tokens by default,
  ranks descriptions before bodies, always cites with `[mnemo:<id>]`.
- **Auto-update**: file watcher reindexes on every memory edit;
  hash-gated so unchanged files are no-ops.
- **Web UI** at `127.0.0.1:7373/`: search, interactive graph
  (Cytoscape + fcose), node editor, source registry, audit log,
  editable settings. Toast notifications for every action.
- **Seven workflow skills**: implement-platform, debug, refactor,
  add-knowledge, query-knowledge, onboard-project, review.
- **Cross-platform install**: `install.sh` (Linux/macOS/Git Bash) and
  `install.ps1` (Windows PowerShell), both idempotent.

### Architecture

- Three-tier: Claude Code plugin (markdown + hook scripts) -> Python
  daemon (FastAPI on 127.0.0.1:7373) -> SQLite + sqlite-vec store.
- Daemon: ~13 modules. Store / ingest / watcher / embed / intent /
  graph / compress / retrieve / api_schemas / server / cli / daemon /
  paths / config / ui.
- Plugin: `.claude-plugin/plugin.json` + 7 skills + 7 slash commands +
  3 hooks (each cross-platform).

### Performance (38-node real-data benchmark)

- Query latency: 17 ms median, 22 ms p95 (single-thread CPU).
- Reindex: 1,157 nodes/sec (hash-gated, no-op on unchanged files).
- DB footprint: 2 MB for the 38 nodes + 160 co-occurrence edges.
- Model cache: 22 MB for MiniLM-L6.

### Quality (curated benchmark)

- 7/7 top-1 (100%), MRR 1.000.
- 273 tests (240 unit + 33 integration), all green.

### Configuration

- Settings persist to `~/.claude/mnemo/settings.json`.
- Editable from the web UI at `/settings` or via `PUT /config`.
- Six scoring weights: alpha (vector), beta (graph), gamma (recency),
  delta (type), epsilon (project), zeta (lexical).
- Defaults: alpha 0.40, beta 0.15, gamma 0.10, delta 0.10, epsilon 0.05,
  zeta 0.20.

### Known limitations (non-blockers)

- Daemon-spawn integration test is skipped on Windows because detached
  uvicorn under `subprocess.Popen` is fragile to test deterministically.
  Manual smoke verifies the path.
- `intent` classifier is regex-based; some phrasings will not fire the
  matching tag. Edit `mnemo.intent.INTENT_PATTERNS` to extend.
- Single-machine. Multi-machine sync is on the 1.3 roadmap.

### Documentation

- [README.md](README.md) - quick start
- [docs/architecture.md](docs/architecture.md) - architecture overview
- [docs/plans/2026-05-09-mnemo-design.md](docs/plans/2026-05-09-mnemo-design.md) - full design
- [docs/workflows/index.md](docs/workflows/index.md) - 7 workflow skills
- [docs/examples/sample-queries.md](docs/examples/sample-queries.md) - real query results
- [docs/benchmarks.md](docs/benchmarks.md) - benchmark methodology + tips
- [docs/roadmap.md](docs/roadmap.md) - what's next
- [CONTRIBUTING.md](CONTRIBUTING.md) - contributor guide

### Breaking changes from 0.1.0

None: 0.1.0 was never released. This is the first public version.

### Acknowledgments

Built with: SQLite, sqlite-vec, sentence-transformers, FastAPI, Typer,
HTMX, Alpine.js, Cytoscape.js, fcose, ruff, pytest, uv.

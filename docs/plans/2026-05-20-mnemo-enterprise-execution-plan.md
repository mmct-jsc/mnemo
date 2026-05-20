# mnemo Enterprise / Revenue Execution Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Each task block is bite-sized (~2-5 min per step). Stay rigidly TDD on the code tasks; the non-code tasks adapt the TDD shape (acceptance criterion -> draft -> execute -> verify -> record).

**Goal:** Execute the validated strategy menu in `docs/plans/2026-05-19-mnemo-enterprise-revenue-design.md` as a phased, TDD-driven implementation plan. High detail for the active flywheel (Angle #1 substrate + MCP -> Angle #3 ROI + open benchmark -> Angle #2 hosted API). Outlines only for the demand-pull endpoints (Angle #4 team SaaS, Angle #5 enterprise daemon) -- they trigger on explicit signal, not on this plan.

**Architecture:** All work is *additive* on top of the published v4.6.4 daemon -- the free local-first plugin must remain free and fully capable (anti-goal #1 of the strategy doc). MCP hardening lives in `daemon/mnemo/mcp_server.py` + `daemon/mnemo/agent_tools.py` (shared tool registry already exposes 9 tools). The benchmark + ROI dashboard reuse `daemon/mnemo/feedback.py` + `daemon/mnemo/retune.py` telemetry. The hosted API is a metering + key-issuance layer over the existing `clients/middleware-py/mnemo_middleware/` and the `/v1/query` path in the daemon, NOT a new product.

**Tech Stack:** Python 3.11+, uv, FastAPI, SQLite + sqlite-vec, sentence-transformers, pytest, ruff. MCP via `mcp>=1.2` (already in `pyproject.toml`). Non-code artifacts under `docs/sponsor/`, `docs/benchmark/`, `docs/integrations/`, `docs/case-studies/`, `docs/design-partners/` (created as needed).

**Repo hard rules (apply to every commit):** no `Co-Authored-By` trailers; no emojis; conventional commit prefixes (`feat:`/`fix:`/`chore:`/`docs:`/`test:`/`refactor:`); HEREDOC for multi-line messages; daemon binds `127.0.0.1` only; one-branch-per-feature/minor; tests live in `daemon/tests/{unit,integration}/test_*.py`; run `uv run pytest` + `uv run ruff check .` before every commit; never gate the free local-first plugin.

**Verification commands used throughout:**

```bash
cd daemon && uv run pytest daemon/tests/unit -q
cd daemon && uv run pytest -k <substring> -v
cd daemon && uv run ruff check . && uv run ruff format --check .
cd daemon && uv run python -c "import frontmatter; ..."   # frontmatter validity
```

---

## Phase 0 -- Preconditions

These run once before any other phase. Tiny.

### Task 0.1: Branch off latest main for Phase 1

**Files:** none new.

**Steps:**

1. Verify main is at `0f2129e` (the v4.6.4 merge):
   ```bash
   git checkout main && git pull --ff-only && git log --oneline -1
   ```
   Expected: `0f2129e Merge pull request #80 from mmct-jsc/release/4.6.4`.
2. Create the Phase 1 branch:
   ```bash
   git checkout -b feat/mcp-substrate-hardening
   ```
3. No commit yet; this is just the branch.

### Task 0.2: Lock the current MCP tool surface as a contract test

This is the safety net for every later MCP change.

**Files:**
- Test: `daemon/tests/unit/test_mcp_tool_surface_contract.py` (create)

**Step 1: Write the failing test**

```python
# daemon/tests/unit/test_mcp_tool_surface_contract.py
"""Lock the published MCP tool surface so accidental rename/removal breaks CI.

Adding a new tool is fine (extend EXPECTED + ship a docs entry).
Renaming or removing an existing tool requires an intentional update here.
"""
from mnemo.agent_tools import TOOLS


EXPECTED_TOOLS = {
    "mnemo_query",
    "mnemo_get_node",
    "mnemo_get_edges",
    "mnemo_traverse",
    "mnemo_search_by_type",
    "mnemo_get_code_lines",
    "mnemo_page_context",
    "mnemo_session_nodes",
}


def test_mcp_tool_surface_is_locked():
    actual = {t.name for t in TOOLS}
    missing = EXPECTED_TOOLS - actual
    assert not missing, f"removed/renamed MCP tools: {missing}"
```

**Step 2: Run it -- expect PASS already** (we are codifying the surface, not changing it):
```bash
cd daemon && uv run pytest daemon/tests/unit/test_mcp_tool_surface_contract.py -v
```
Expected: PASS.

**Step 3: Commit**
```bash
git add daemon/tests/unit/test_mcp_tool_surface_contract.py
git commit -m "test(mcp): lock published tool surface as a contract"
```

---

## Phase 1 -- Angle #1: Provider-neutral agent-memory substrate + MCP

**Goal:** make mnemo's MCP server the cleanest drop-in typed Graph-RAG memory for any agent, ship two non-Claude integrations, and submit one A.I.-giant program/grant application. This phase is the strategy doc's #1 (highest sponsor-attraction).

**Acceptance signals at phase end:**
- 2 non-Claude agents documented with working "5-minute mount" guides + green integration tests.
- 1 sponsor/grant application submitted, tracked in `docs/sponsor/`.
- MCP tool surface stable, server documented as provider-neutral.

### Task 1.1: Pick the two non-Claude clients (decision artifact)

Non-code; gates everything below.

**Files:**
- Create: `docs/integrations/PICKS.md`

**Steps:**

1. Draft `docs/integrations/PICKS.md` with this template:
   ```markdown
   # Non-Claude integration picks (Phase 1)

   ## Selection rubric
   - MCP-native (no custom protocol bridge)
   - Active 2026 community / measurable user base
   - Different host shape (one IDE-embedded, one agent-loop)

   ## Pick A (IDE-embedded)
   | Candidate | MCP support | Users | Verdict |
   |---|---|---|---|
   | Cursor | yes (native) | large | ... |
   | Continue | yes | medium | ... |
   | Zed | partial | growing | ... |

   ## Pick B (agent-loop)
   | Candidate | MCP support | Users | Verdict |
   |---|---|---|---|
   | OpenAI Agents SDK | yes (MCP adapter) | growing | ... |
   | Gemini CLI | yes | small | ... |
   | LangGraph | adapter | large | ... |

   ## Decision
   Pick A: <name>. Pick B: <name>. Date: 2026-05-20.
   ```
2. Fill the rubric rows from public docs (use `context7-plugin:documentation-lookup` per candidate). Choose one A and one B.
3. Commit:
   ```bash
   git add docs/integrations/PICKS.md
   git commit -m "docs(integrations): pick two non-Claude MCP clients for Phase 1"
   ```

### Task 1.2: Integration #1 (Pick A) -- 5-minute-mount doc

**Files:**
- Create: `docs/integrations/<pickA>.md` (e.g. `cursor.md`)
- Test: `daemon/tests/integration/test_mcp_mount_<pickA>.py`

**Step 1: Write the failing integration test**

```python
# daemon/tests/integration/test_mcp_mount_<pickA>.py
"""Smoke test the documented mount flow for <pickA>.

This DOES NOT launch the IDE; it asserts the documented JSON config
shape is a valid MCP server entry and that mnemo's server responds
to it over stdio (the transport <pickA> uses).
"""
import json, subprocess, sys, pathlib

DOC = pathlib.Path("docs/integrations/<pickA>.md")


def test_documented_config_block_is_valid_json():
    text = DOC.read_text()
    # extract the first fenced ```json block
    start = text.index("```json") + len("```json")
    end = text.index("```", start)
    cfg = json.loads(text[start:end])
    assert "mcpServers" in cfg or "servers" in cfg


def test_mnemo_stdio_server_starts():
    proc = subprocess.Popen(
        [sys.executable, "-m", "mnemo.mcp_server", "--stdio"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    )
    try:
        # send minimal initialize handshake; expect a response within 2s
        ...
    finally:
        proc.terminate()
```

**Step 2: Run -- expect FAIL** (doc + entry-point may not exist yet):
```bash
cd daemon && uv run pytest daemon/tests/integration/test_mcp_mount_<pickA>.py -v
```
Expected: FAIL ("docs/integrations/<pickA>.md not found" or "no mcp_server -m entry").

**Step 3: Write the minimal doc + CLI entry**

Create `docs/integrations/<pickA>.md` with the host-specific config block, the install command (`pip install mnemo` or the plugin path), the verification step (a query that should return cited results), and a troubleshooting table.

Verify `daemon/mnemo/mcp_server.py` has a `python -m mnemo.mcp_server --stdio` entry point. If missing, add a tiny `if __name__ == "__main__":` block + a `--stdio` flag that calls `serve_stdio()`.

**Step 4: Run -- expect PASS:**
```bash
cd daemon && uv run pytest daemon/tests/integration/test_mcp_mount_<pickA>.py -v
```

**Step 5: Commit**
```bash
git add docs/integrations/<pickA>.md daemon/mnemo/mcp_server.py daemon/tests/integration/test_mcp_mount_<pickA>.py
git commit -m "feat(mcp): <pickA> 5-minute mount + integration smoke test"
```

### Task 1.3: Integration #2 (Pick B) -- same shape as 1.2

Copy the 1.2 task verbatim for `<pickB>`. New test file `test_mcp_mount_<pickB>.py`. Same 5-step cycle. Same commit shape.

### Task 1.4: Provider-neutral positioning in README + landing doc

**Files:**
- Modify: `README.md` (add "Use mnemo from any MCP-capable agent" section near the top)
- Create: `docs/integrations/README.md` (index)
- Test: `daemon/tests/unit/test_readme_links_to_integrations.py`

**Step 1: Write the failing test**

```python
# daemon/tests/unit/test_readme_links_to_integrations.py
"""README must link to the integrations index so non-Claude users find us."""
import pathlib
README = pathlib.Path("README.md")
INDEX = pathlib.Path("docs/integrations/README.md")


def test_readme_links_to_integrations_index():
    assert "docs/integrations" in README.read_text()


def test_integrations_index_lists_each_integration():
    body = INDEX.read_text()
    assert "cursor" in body.lower() or "<pickA>" in body
    assert "openai" in body.lower() or "<pickB>" in body
```

**Step 2: Run -- FAIL.**

**Step 3: Add README section + create the index:** the README section is one paragraph + a 3-line code block ("mount mnemo into any MCP client"). The index links to both per-client docs.

**Step 4: Run -- PASS.** Watch the existing `test_readme_links_to_docs` closed-set test (see `feedback_mnemo_ci_doc_link_test.md`) -- the new docs/integrations link is ADDITIVE; no existing link removed.

**Step 5: Commit**
```bash
git add README.md docs/integrations/README.md daemon/tests/unit/test_readme_links_to_integrations.py
git commit -m "docs: provider-neutral positioning, link to integrations index"
```

### Task 1.5: Risk + capability tags on MCP tools (host-gating support)

The existing tool registry already folds risk-tags into descriptions; the substrate story benefits from STRUCTURED tags (so hosts like Cursor can gate writes by default).

**Files:**
- Modify: `daemon/mnemo/agent_tools.py` (add `risk: Literal['read','write','admin']` to `ToolSpec`)
- Modify: `daemon/mnemo/mcp_server.py` (`tool_list()` exposes `risk` in the descriptor)
- Test: `daemon/tests/unit/test_mcp_tool_risk_tags.py`

**Step 1: Write the failing test**

```python
def test_every_tool_has_risk_tag():
    from mnemo.agent_tools import TOOLS
    valid = {"read", "write", "admin"}
    for t in TOOLS:
        assert t.risk in valid, f"{t.name}: risk={t.risk!r}"


def test_mcp_tool_list_exposes_risk():
    from mnemo.mcp_server import tool_list
    for desc in tool_list():
        assert "risk" in desc, desc["name"]
```

**Steps 2-5:** standard TDD cycle. Add `risk` field; classify each tool (`mnemo_query`/`get_*`/`search_*`/`page_context`/`session_nodes` = `read`; write tools = `write`; any node deletion = `admin`). Bump `tool_list()`. Commit `feat(mcp): risk tags on tool descriptors for host-side gating`.

### Task 1.6: Cap and document the MCP wire schema

So later changes can't silently break mounted clients.

**Files:**
- Create: `docs/integrations/wire-schema.md`
- Test: `daemon/tests/unit/test_mcp_wire_schema_snapshot.py` (snapshot test of `tool_list()` JSON)

Standard 5-step TDD. The snapshot file is `daemon/tests/unit/_snapshots/mcp_tool_list.json`; the test asserts that JSON equals the live output. Updating it requires re-running with `MNEMO_UPDATE_SNAPSHOTS=1` and an explicit code review.

Commit: `test(mcp): snapshot the wire schema; docs index`.

### Task 1.7: Submit one sponsor program application

Non-code. The application IS the deliverable.

**Files:**
- Create: `docs/sponsor/<program-name>.md` (e.g. `anthropic-startups.md`)

**Steps:**

1. Pick the program (Anthropic startup program, OpenAI Startup Fund, Google for Startups Cloud Program -- whichever currently has open applications; check public pages this week).
2. Draft `docs/sponsor/<program-name>.md` with: program URL, the application's required questions, the answers (use the strategy doc as the source of truth: provider-neutral substrate, MCP-native, the live demo URL, the v4.6.4 release, the upcoming open benchmark from Phase 2), submission date, expected decision date.
3. Submit the application (manual; via the program's online form).
4. Update the doc with `Submitted: YYYY-MM-DD` + the confirmation number.
5. Commit:
   ```bash
   git add docs/sponsor/<program-name>.md
   git commit -m "docs(sponsor): submit <program-name> application"
   ```

### Task 1.8: Phase 1 PR

Run the full suite + ruff, push the branch, open the PR.

```bash
cd daemon && uv run pytest -q && uv run ruff check . && uv run ruff format --check .
git push -u origin feat/mcp-substrate-hardening
gh pr create --title "feat(mcp): substrate hardening + 2 non-Claude integrations + sponsor #1" --body "$(cat <<'EOF'
## Summary
- Locks the MCP tool surface as a contract (Task 0.2)
- Documents mounting mnemo into <pickA> and <pickB> (Tasks 1.2-1.3) with smoke tests
- Provider-neutral README + integrations index (Task 1.4)
- Risk tags on tool descriptors for host-side gating (Task 1.5)
- Wire-schema snapshot test (Task 1.6)
- One A.I.-giant program application submitted (Task 1.7)

Strategy doc: docs/plans/2026-05-19-mnemo-enterprise-revenue-design.md (Angle #1).

## Test plan
- [ ] `uv run pytest daemon/tests/unit -q` green (new contract + risk + schema + readme tests)
- [ ] `uv run pytest daemon/tests/integration -q` green (mcp_mount smokes)
- [ ] `uv run ruff check .` clean
EOF
)"
```

---

## Phase 2 -- Angle #3: ROI analytics + open agent-memory benchmark

**Goal:** publish a credible open benchmark for typed Graph-RAG agent memory, make mnemo the reference implementation, and produce one shipped ROI case study from existing telemetry.

**Branch:** `feat/agent-memory-benchmark` off latest main after Phase 1 merges.

**Acceptance signals:** benchmark spec + harness published; one case study committed; numbers cited in the next sponsor application.

### Task 3.1: Benchmark spec doc

**Files:**
- Create: `docs/benchmark/agent-memory-spec-v0.md`

**Steps:**

1. Draft the spec with sections:
   - **Problem statement** (agents re-derive context every turn; cost + latency penalty; no shared benchmark exists).
   - **Tasks** (e.g. "answer follow-up referencing material from turn 1"; "navigate a code-symbol chain across 5 turns"; "recover after a session resume"). Aim for 8-12 tasks.
   - **Inputs** (prompt sequences, optional memory seed corpora).
   - **Metrics** (re-derivation rate %, tokens-to-answer, citation precision, answer correctness via reference rubric).
   - **Reference implementation** (mnemo's `/v1/query` + the middleware shim).
   - **Baseline** (vanilla agent, no memory).
   - **License** (CC-BY for the spec, MIT for the harness).
2. Commit `docs(benchmark): v0 spec for typed agent-memory eval`.

### Task 3.2: Benchmark harness scaffold (failing test first)

**Files:**
- Create: `bench/` (new top-level dir, NOT inside `daemon/` -- the benchmark is product-agnostic)
- Create: `bench/pyproject.toml`, `bench/agent_memory_bench/__init__.py`, `bench/agent_memory_bench/runner.py`
- Test: `bench/tests/test_runner_skeleton.py`

**Step 1: Write the failing test**

```python
# bench/tests/test_runner_skeleton.py
from agent_memory_bench.runner import run_task, TaskResult

def test_runner_returns_result_with_required_fields():
    result = run_task(task_id="echo-1", agent=lambda p: "ok", memory=None)
    assert isinstance(result, TaskResult)
    assert result.task_id == "echo-1"
    assert result.metrics.tokens_in >= 0
    assert result.metrics.tokens_out >= 0
    assert 0.0 <= result.metrics.rederivation_rate <= 1.0
```

**Step 2:** Run from `bench/`: `uv run pytest tests/ -v` -> FAIL (no module).

**Step 3:** Minimal `runner.py` with `TaskResult` + `Metrics` dataclasses + a trivial `run_task` impl that calls `agent(prompt)` once and returns zeros.

**Step 4:** PASS.

**Step 5:** Commit `feat(bench): runner skeleton with TaskResult + Metrics`.

### Task 3.3: One real benchmark task end-to-end

Pick the simplest task from the spec (e.g. "answer-follow-up").

**Files:**
- Create: `bench/agent_memory_bench/tasks/answer_follow_up.py`
- Create: `bench/agent_memory_bench/agents/{vanilla,mnemo}.py`
- Test: `bench/tests/test_answer_follow_up.py`

**Step 1: failing test** asserts `mnemo` agent beats `vanilla` agent on `rederivation_rate` for this task with a fixed seeded corpus.

**Steps 2-4:** standard cycle. The vanilla agent is a deterministic mock that always re-asks. The mnemo agent calls the local daemon `/v1/query` (skipped if `MNEMO_DAEMON_URL` env not set; use `pytest.skip` to keep CI portable).

**Step 5:** commit `feat(bench): answer-follow-up task + vanilla & mnemo agents`.

### Task 3.4: ROI dashboard endpoint

Surface the already-collected feedback + retune telemetry as a public-shaped JSON.

**Files:**
- Modify: `daemon/mnemo/server.py` (add `GET /v1/roi/summary`)
- Test: `daemon/tests/unit/test_roi_summary_endpoint.py`

**Step 1: failing test**

```python
def test_roi_summary_returns_expected_keys(daemon_client, seeded_feedback):
    r = daemon_client.get("/v1/roi/summary?project=demo")
    assert r.status_code == 200
    body = r.json()
    for k in ("queries_total", "rederivations_avoided",
             "tokens_saved_est", "thumbs_up_ratio",
             "auto_tune_iterations"):
        assert k in body, k
```

**Steps 2-4:** TDD cycle; compute fields from `feedback_event` + the `RetuneReport` history table. Project-scoped via the existing `active_project` resolver.

**Step 5:** commit `feat(api): GET /v1/roi/summary aggregates feedback + retune telemetry`.

### Task 3.5: Render ROI in the existing UI

**Files:**
- Modify: `daemon/mnemo/ui/templates/dashboard.html` (add ROI summary card)
- Modify: `daemon/mnemo/ui/static/dashboard.js` (fetch /v1/roi/summary; render)
- Test: `daemon/tests/unit/test_dashboard_roi_card.py`

Standard 5-step cycle. Reuse the existing card shell, the palette via `palette.py`, and the v2.6.7 pagination patterns where applicable. Commit `feat(ui): ROI summary card on the dashboard`.

### Task 3.6: One case study from real data

**Files:**
- Create: `docs/case-studies/2026-05-mnemo-self-host.md`

**Steps:** select a tracked mnemo-host repo (the dogfooded knowledge-base repo itself is fine), pull the actual numbers from `GET /v1/roi/summary` for the period since v4.0.0 shipped, write a 1-page case study with the numbers + screenshots of the dashboard card, commit `docs(case-study): mnemo self-host ROI since v4.0.0`.

### Task 3.7: Publish the benchmark

**Files:**
- Modify: `README.md` (add "Benchmark" section linking to the spec + harness)
- Create: `bench/README.md`

Commit + push branch + open PR titled `feat: agent-memory benchmark v0 + ROI dashboard + first case study`.

---

## Phase 3 -- Angle #2: Hosted context API on the middleware

**Goal:** ship a paid hosted endpoint of mnemo's retrieval over the existing middleware: API keys, per-key quotas, metering, simple billing reporter, and a CLI to issue design-partner keys.

**Branch:** `feat/hosted-context-api` off latest main.

**Anti-goal reminder:** the self-host path stays fully capable AND free. The hosted API is a *convenience* tier, never a crippled free tier.

### Task 2.1: API key + quota schema (additive migration)

**Files:**
- Modify: `daemon/mnemo/store.py` -- add `api_key`, `quota`, `usage_period` tables via the existing `_ensure_columns` / `SCHEMA_SQL` pattern (see `reference_mnemo_pipelines.md` #5)
- Test: `daemon/tests/unit/test_api_key_schema.py`

**Step 1: failing test**

```python
def test_api_key_schema_present(store):
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(api_key)")}
    assert {"id", "hash", "name", "created_at", "revoked_at"} <= cols
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(quota)")}
    assert {"api_key_id", "period", "max_queries", "max_tokens"} <= cols
```

**Steps 2-4:** add tables to `SCHEMA_SQL` (always-run, idempotent); no migration script needed thanks to `_ensure_columns`. Commit `feat(store): api_key + quota + usage_period tables`.

### Task 2.2: Key issuance CLI

**Files:**
- Modify: `daemon/mnemo/cli.py` -- add `mnemo key {create,list,revoke}` subcommands
- Test: `daemon/tests/unit/test_cli_key_commands.py`

Standard cycle. Hash with `hashlib.sha256` + a per-key 16-byte salt. Print the raw key ONCE on create; never store it. Commit `feat(cli): mnemo key {create,list,revoke}`.

### Task 2.3: API-key auth dependency on /v1/query

**Files:**
- Modify: `daemon/mnemo/server.py` -- new `Depends(api_key_or_local)` that allows local-loopback unauthenticated (preserving self-host UX) but requires a valid key for external mounts (off by default, gated by a config flag)
- Test: `daemon/tests/unit/test_query_api_key_auth.py`

The flag default is `false` (self-host unaffected). Hosted deployment flips it. Tests cover: local loopback OK without key; non-loopback rejected without key; valid key accepted; revoked key rejected. Commit `feat(api): optional api-key auth on /v1/query, gated by config`.

### Task 2.4: Metering hook on /v1/query

**Files:**
- Modify: `daemon/mnemo/server.py` -- post-handler hook writes to `usage_period`
- Modify: `daemon/mnemo/store.py` -- `record_usage(api_key_id, period, queries, tokens)` upsert
- Test: `daemon/tests/unit/test_metering_increments_usage.py`

Standard cycle. The period is `YYYY-MM` from the request timestamp. Commit `feat(api): meter queries + tokens per api-key per month`.

### Task 2.5: Quota enforcement

**Files:**
- Modify: `daemon/mnemo/server.py` -- 429 with a `Retry-After` header when over quota
- Test: `daemon/tests/unit/test_quota_enforcement.py`

Standard cycle. Off by default; hosted deployments set per-key quotas via the CLI. Commit `feat(api): 429 when api-key is over its monthly quota`.

### Task 2.6: Billing report CLI

**Files:**
- Modify: `daemon/mnemo/cli.py` -- `mnemo billing report --period YYYY-MM`
- Test: `daemon/tests/unit/test_billing_report.py`

CSV out: `key_name,queries,tokens,quota_queries,quota_tokens,over_quota`. Commit `feat(cli): mnemo billing report --period`.

### Task 2.7: Hosted deployment doc

**Files:**
- Create: `docs/hosted/deploying.md`

Covers: enabling api-key auth, issuing the first key, setting quotas, running behind a reverse proxy, the (already-strict) `127.0.0.1` binding + the gateway pattern. Commit `docs(hosted): deployment guide for hosted-API mode`.

### Task 2.8: 5 design-partner outreach (non-code)

**Files:**
- Create: `docs/design-partners/LOG.md` (table: name, contact, ICP, agreed?, key-issued?, first-usage?)

**Steps:** identify 10 candidates (teams already using mnemo locally OR plausible ICP from the substrate integrations Phase 1 shipped), DM/email with the value prop + a free trial key, target 5 acceptances. Track in the log. Commit on each update with `docs(design-partners): outreach update`.

### Task 2.9: Phase 3 PR

Run suite, push, open PR `feat: hosted context API (keys + quotas + metering + billing report)`.

---

## Phase 4 -- Angle #4 OUTLINE: Team/org memory SaaS (DEMAND-PULL ONLY)

**Trigger condition:** >=3 design partners from Phase 3 ask for a shared graph + RBAC AND state a willingness-to-pay number. **Until this triggers, do NOT start.**

**Files when triggered:**
- Create: `docs/plans/YYYY-MM-DD-team-saas-design.md` (run `superpowers:brainstorming` first)

### Task 4.1 (outline)
Shared-graph data model spike: 1-page design doc covering scope_key extension to `org_id + workspace_id`, the existing per-project isolation soft-penalty (`Config.project_isolation_penalty`, see `feedback_mnemo_silent_zero_modes.md`) repurposed for cross-org isolation, and migration cost from single-tenant.

### Task 4.2 (outline)
RBAC role/permission schema: read/write/admin per workspace; identity from an external IdP (SAML/OIDC), NOT mnemo-managed accounts initially.

### Task 4.3 (outline)
Private-beta gate: 3 teams pulling + payment intent confirmed -> green-light a `release/5.x` SaaS branch with executing-plans + writing-plans.

---

## Phase 5 -- Angle #5 OUTLINE: Self-hosted enterprise daemon (INBOUND-ONLY)

**Trigger condition:** named inbound from a regulated org with a contract value. **Until this triggers, do NOT start.** Capacity is "very heavy" -- a SOC2 program is a year of work for a small team.

### Task 5.1 (outline)
Document the current on-prem deployment posture (`docs/enterprise/posture.md`): the `127.0.0.1` binding, the `test_demo_build` no-secret guard, the redaction safeguards, what's missing (no central audit log shipper, no SAML, no SOC2 controls).

### Task 5.2 (outline)
Compliance gap analysis: SOC2 Type 1 path estimate (controls, evidence, audit cost) -- KEEP IT A DOC until inbound demand justifies the spend.

### Task 5.3 (outline)
Inbound-only criterion locked: any "enterprise daemon" branch creation must reference a contract value > $X (TBD) in its PR description.

---

## Phase 6 -- Cross-cutting / ongoing

### Task 6.1: Leading-indicator dashboard (weekly review)

**Files:**
- Create: `docs/strategy/indicators.md`

Weekly entries with: external-integration count, sponsor-application status, accepted-program count, design-partner count, benchmark citations seen in the wild, ROI dashboard usage. Commit each week: `docs(strategy): indicators week of YYYY-MM-DD`.

### Task 6.2: Sponsor re-application cadence

If the Phase 1.7 application is rejected, reapply quarterly (or apply to a different program). Track every submission + outcome in `docs/sponsor/`. Each submission = one commit.

### Task 6.3: Anti-goal review (quarterly)

Skim the strategy doc's anti-goals every quarter; explicit "still holding" / "violation found" note in `docs/strategy/anti-goal-reviews.md`. Catch drift before it metastasizes (paywalled free tier; provider exclusivity creep).

---

## Phase 7 -- Release shape per phase

Each phase ends with the standard release pipeline (`reference_mnemo_pipelines` #4 + #10 + #13):

1. Bump version (Phase 1 -> v4.7.0 minor; Phase 2 -> v4.8.0; Phase 3 -> v4.9.0). MINOR per feature phase; PATCH for fixes only.
2. `chore(release): vX.Y.0` final commit on the branch.
3. Push branch, open PR, wait for CI green.
4. Merge to main (NOT squash; merge commit) -- `release.yml` auto-publishes tag + GitHub release + redeploys Demo Pages.
5. `git checkout main && git pull`; restart the daemon (gotcha-32: kill the real `:7373` PID, start ONE, verify `/v1/health` reads the new version); verify the live demo.
6. pipeline-13: per-version handover in `~/.claude/projects/D--Repository-knowledge-base/memory/session_handover_v4_X_Y_shipped.md`; update `MEMORY.md` (keep < 24.4 KB; 0 joined index lines); local `mnemo reindex`; verify the new handover is the #1 hit.

---

## Cross-phase verification checklist (before claiming any phase "done")

- [ ] All new tests pass: `cd daemon && uv run pytest -q`
- [ ] Lint clean: `cd daemon && uv run ruff check . && uv run ruff format --check .`
- [ ] No `Co-Authored-By` in any commit on the branch: `git log <range> --format=%B | grep -i co-authored-by` returns nothing.
- [ ] No emojis added to code/docs/commits.
- [ ] No new tracked file places a secret on disk (the existing `test_demo_build` no-secret guard catches a subset; eyeball new `docs/sponsor/`/`docs/design-partners/` content before committing).
- [ ] If the change touches the MCP wire schema, `test_mcp_wire_schema_snapshot.py` was intentionally updated AND the new snapshot was reviewed.
- [ ] The free local-first plugin still works without any new flag set: `mnemo daemon start` + `mnemo query "..."` returns budget-capped cited results.
- [ ] Phase PR description links to this plan + the strategy doc.

---

## Decision points / when to STOP this plan

This plan executes the bets the strategy doc ranks **above the line**. STOP and reconvene with `superpowers:brainstorming` if:

- Phase 1 sponsor application is rejected twice in a row (re-evaluate the substrate framing, not just the writing).
- Phase 2 benchmark gets no external citations after 3 months (re-evaluate the spec/positioning, not the harness).
- Phase 3 hits <2 paying design partners after 5 outreaches AND no clear "willingness-to-pay" signal (the hosted API may be a year early; pause).
- A giant ships native typed Graph-RAG agent memory that obsoletes the substrate angle (the existential risk in the strategy doc). Re-evaluate the moat.

Otherwise execute phases 1 -> 2 -> 3 in order; 4 and 5 stay dormant until their explicit triggers fire.

# Agent-Memory Benchmark — Spec v0

> **Status: v0 draft.** Published under CC-BY-4.0 (see License at end).
> Reference harness ships separately under MIT at `bench/` in this
> repo. Feedback, additional implementations, and benchmark
> contributions welcome via GitHub issues / PRs.
>
> **Authors**: mnemo contributors. **Last updated**: 2026-05-21.

## TL;DR

A reproducible benchmark for **typed Graph-RAG agent memory** —
the missing layer between "naïve RAG" and "tool-use without memory"
that every modern AI coding agent reinvents in private. v0 defines
8 measurable tasks, 4 metrics, a reference vanilla-agent baseline,
and a reference mnemo implementation. The harness is intentionally
agent-shape-agnostic: any agent that can be wrapped as
`fn(prompt: str, memory: Optional[Memory]) -> str` can be measured.

## Problem statement

AI coding agents (Claude Code, Cursor, OpenAI Agents SDK, Continue,
Zed, Aider, Devin, ...) all face the same memory problem:

- They are amnesiac across sessions. Every prompt re-derives
  context the user already provided last turn.
- The same lessons get re-discovered, the same feedback gets
  re-given, the same architectural decisions get re-derived in
  every new conversation.
- The cost is paid three times: in tokens (wider context
  windows), in latency (re-grounding the model on prior state),
  and in *user trust* (the agent appearing to "forget" things the
  user just told it).

Two existing approaches both miss the mark:

1. **Naïve RAG**: ranks text chunks by embedding similarity,
   returns paragraphs. No type, no structure, no provenance, no
   feedback loop. Wastes context budget on noise; can't tell the
   agent why a hit matters.
2. **Tool-use without memory**: lets the agent query live (search
   the codebase, list files), but every answer is fresh. No
   accumulated learning, no feedback signal, no growth over
   sessions.

The missing primitive is a **typed graph of memory + code +
decisions, retrieved with citations, capped at a small token
budget per request, and improved by user feedback over time**.

The community has no shared, reproducible way to measure who's
solving this well. **That is the gap this benchmark fills.**

## Goals (and non-goals)

### Goals

- **Reproducible**: anyone can re-run the harness against any
  agent and get the same numbers.
- **Agent-shape-agnostic**: works for in-process Python agents,
  subprocess agents, HTTP agents, MCP-served agents.
- **Measures the right things**: re-derivation rate (the cost),
  tokens-to-answer (the budget), citation precision (the
  trustworthiness), answer correctness (the quality). Not raw
  retrieval recall — that's a vector-DB benchmark, not an agent-
  memory benchmark.
- **Includes a baseline**: every claim is relative to a vanilla
  no-memory agent on the same task.

### Non-goals (v0)

- Multi-modal memory (images, PDFs, audio). v0 is text-only.
- Multi-user / multi-tenant memory. v0 is single-user.
- Latency below the agent loop. We measure tokens, not wall-clock
  end-to-end (too dependent on the agent's own runtime).
- "Winning" leaderboard. v0 publishes spec + harness + baseline.
  External implementations may publish their own results; we do
  not rank.

## Reference Memory interface

The harness expects agents that take an *optional* `Memory` handle:

```python
class Memory(Protocol):
    """The minimum surface a benchmark agent's memory must expose."""
    def query(self, prompt: str, max_tokens: int = 800) -> "Retrieval": ...
    def feedback(self, hit_id: str, direction: Literal["up", "down"]) -> None: ...
    def cite(self, retrieval: "Retrieval") -> list[str]: ...
```

```python
@dataclass
class Retrieval:
    text: str            # the budgeted context block
    hit_ids: list[str]   # node / chunk identifiers, in order returned
    tokens_used: int     # actual token count of `text`
```

mnemo's `/v1/query` is the reference implementation. A vanilla
agent just sets `memory=None` and the tasks score it accordingly.

## Tasks (v0, 8 tasks)

Each task is a (seed_corpus, prompt_sequence, expected_behavior)
triple. The seed corpus is a fixed JSONL fixture under
`bench/fixtures/<task_id>/`; the prompt sequence is a list of
user messages; expected_behavior is rubric-scored against the
agent's final output.

### T1 — Answer follow-up referencing material from turn 1

- **Setup**: corpus contains 3 memory_feedback nodes about MQTT
  broker auth.
- **Prompt 1**: "How do we handle MQTT broker auth?" — agent
  should retrieve the 3 memory nodes; cite them.
- **Prompt 2**: "What's the testing approach for that?" — agent
  must use prior turn's memory (not re-derive). A no-memory
  baseline must re-ask; a memory-equipped agent should answer
  by referring back to the cited material.
- **Metric drivers**: re-derivation rate (boolean: did the agent
  re-issue the same query?), tokens-to-answer.

### T2 — Navigate code-symbol chain across 5 turns

- **Setup**: corpus contains a typed code graph of ~50
  symbols across 5 modules with `calls` / `defines` /
  `routes_to` edges.
- **Prompt sequence (5 turns)**: trace from a Component → its
  Endpoint → the Route handler → a Service → a leaf utility. Each
  turn asks for the next hop.
- **Metric drivers**: citation precision (did the agent cite the
  correct symbol at each hop?), tokens-to-answer.

### T3 — Recover after a session resume

- **Setup**: corpus from T1; user simulates a session restart
  mid-conversation.
- **Prompt 1**: same as T1 prompt 1.
- **Prompt 2 (post-restart)**: "Continue from where we left off."
  — agent must surface the prior conversation state from
  memory. A no-memory baseline fails outright.
- **Metric drivers**: re-derivation rate, answer correctness.

### T4 — Reject a stale / superseded memory

- **Setup**: corpus contains both an OLD memory_feedback ("we use
  Redis for caching") and a NEW memory_feedback ("Redis was
  replaced by Postgres LISTEN/NOTIFY in 2026-03").
- **Prompt**: "What's our caching layer?" — agent must cite the
  NEW memory, not the old one.
- **Metric drivers**: citation precision (correct node?), answer
  correctness (no Redis recommendation).

### T5 — Honor a permission boundary

- **Setup**: corpus mixes `safe` and `confirm` / `danger` tools.
- **Prompt**: "Delete the test_x.py file." — agent must surface
  the appropriate gate (no autonomous delete) and ask before
  proceeding.
- **Metric drivers**: answer correctness (binary: did the agent
  pause for confirmation?).

### T6 — Apply feedback within a single session

- **Setup**: corpus contains 5 memory_feedback nodes, 2 of which
  are highly relevant, 3 of which are tangential.
- **Prompt 1**: ask a question that pulls all 5.
- **User thumbs-up**: on the 2 relevant.
- **Prompt 2 (follow-up)**: similar question — agent should now
  rank the 2 thumbed-up nodes higher / cite them first.
- **Metric drivers**: citation precision (did re-rank actually
  happen?), answer correctness.

### T7 — Cross-project isolation

- **Setup**: corpus contains material from projects A and B with
  overlapping vocabulary but project-specific facts. Active
  project = A.
- **Prompt**: "What's the auth setup?" — agent must NOT surface
  project B's auth memory.
- **Metric drivers**: citation precision (no project-B hits),
  re-derivation rate (clean cite from project A).

### T8 — Budget compliance

- **Setup**: corpus is artificially huge (10k memory + 5k code
  nodes).
- **Prompt**: a broad question that could surface 50+ hits.
- **Constraint**: agent must keep retrieved context under
  `max_tokens=800` while still answering correctly.
- **Metric drivers**: tokens-to-answer (≤ 800 hard cap;
  budget-compliant or not), answer correctness.

### T9 — Prompt architect satisfies more acceptance criteria

- **Setup**: corpus contains 3-5 memory nodes carrying the
  acceptance criteria + an anti-pattern note for a specific
  coding task.
- **Prompt**: a short raw user prompt ("fix the MQTT auth bug")
  that omits the criteria.
- **Two arms compared**:
  - *Vanilla*: send the raw prompt directly to a host LLM. The
    host has no access to the corpus.
  - *Mnemo (prompt-architect)*: the typed Graph-RAG memory
    assembles a sectioned markdown block (Problem / Context /
    Files / Acceptance / Anti-patterns / Prompt) with explicit
    citations, then sends THAT to the same host LLM.
- **Metric drivers**: acceptance-criteria satisfaction (M4) +
  citation precision (M3). The raw arm cannot surface criteria
  it never saw; the architected arm explicitly inlines them.
- **Strict invariant** (mirror of T1's locked invariant in the
  opposite direction):

  ```
  mnemo.answer_correctness > vanilla.answer_correctness
  ```

  T1 says "vanilla re-derives MORE"; T9 says "the architected
  output satisfies MORE acceptance criteria". If either
  inverts, the substrate framing ("typed Graph-RAG context is
  the wedge") has broken.

- **v0 stub** scope: 4 corpus nodes, 1 high-confidence prompt.
- **v0.1 expansion**: 30 prompts (10 high / 10 medium / 10 low
  confidence) + opt-in LLM judge for M4. The prompt-architect's
  confidence-heuristic + clarification budget become visible at
  this scale (low-confidence prompts trigger ≤2 clarifying
  questions).

## Metrics (4)

All four are computed per-task and aggregated to a single per-agent
benchmark report.

### M1 — Re-derivation rate (%)

> Fraction of turns where the agent re-issued a query semantically
> equivalent to a previous turn's query (when memory should have
> made it unnecessary).

Computed via: pairwise cosine similarity of the agent's
intermediate tool-call args (or the search/list invocations a
no-memory agent would emit) against the previous-turn args. A
threshold of 0.85 cosine on a sentence-transformers MiniLM
embedding flags "this is the same question re-asked."

Lower is better. A vanilla no-memory agent should score ~100%
on T1 / T3 (every follow-up re-derives). A perfect memory-
equipped agent scores ~0%.

### M2 — Tokens-to-answer

> Total input tokens consumed across all turns of the task to
> reach a correct answer. Sum of model-input tokens; output
> tokens are excluded.

Lower is better. A no-memory baseline pays the corpus cost on
every turn; a memory-equipped agent pays it once and amortizes
over the conversation.

### M3 — Citation precision (%)

> Of the hits the agent cites in its final answer, what fraction
> correspond to a node the task's expected_behavior identifies
> as relevant?

Higher is better. A `[mnemo:X]` citation pointing to a node not
on the task's expected list is counted as wrong, even if the
answer itself is correct. This catches "confident but
hallucinated grounding."

### M4 — Answer correctness (0-1)

> Rubric-graded by an LLM judge (Claude Opus 4.6 default; the
> harness lets you swap judges). Each task's
> `expected_behavior` includes a rubric with 3-5 graded criteria.

Higher is better. The judge is prompted to grade strictly +
return both a score and a rationale; rationales are logged for
manual auditing.

## Inputs (fixtures)

Each task ships its fixture under `bench/fixtures/<task_id>/`:

- `corpus.jsonl` — one node per line, the seed memory + code
  graph (matches mnemo's node JSON schema; portable to any other
  memory implementation that can ingest from a JSONL stream).
- `prompts.json` — the prompt sequence the harness drives.
- `expected.json` — `{relevant_node_ids: [...], rubric: [...]}`
  the judge + metric calculators consume.

Fixtures are fully checked in — no live LLM call during fixture
construction. v0 fixtures are mnemo-synthetic (derived from the
demo's seeded graph) so they're CC-BY-shareable; v1 may add
real-world anonymized fixtures from opted-in contributors.

## Baseline implementations (2)

The harness ships two reference agents:

### Baseline A — `agent_vanilla`

A pure-LLM agent with no memory. Just sends each prompt + the
seed corpus (truncated to fit) to the model. This is the
"naïve RAG with infinite re-derivation" worst-case baseline that
every memory-equipped agent must beat.

### Baseline B — `agent_mnemo`

A reference memory-equipped agent that calls mnemo's local
daemon (`/v1/query`) per turn, ingests cited results, and feeds
thumbs-up feedback when the user message references prior
material approvingly.

External contributors implementing the `Memory` Protocol can
register their agents in the harness and produce comparable
numbers.

## Reference implementation

[mnemo](https://github.com/mmct-jsc/mnemo) is the reference
implementation. Specifically:

- **Memory ingestion**: `mnemo reindex` loads the JSONL corpus
  into the live SQLite store.
- **Query**: `POST /v1/query` returns the `Retrieval` shape
  directly (`text`, `hits`, `tokens_used`).
- **Feedback**: `POST /v1/feedback/thumbs` records up/down per
  hit.
- **MCP**: the same surface is exposed via `mnemo mcp` (stdio)
  so MCP-native agents (Cursor, OpenAI Agents SDK) can be
  benchmarked through their own host's MCP adapter.

The mnemo `agent_mnemo` baseline lives in
`bench/agent_memory_bench/agents/mnemo.py` once Task 3.2-3.3 of
the execution plan lands.

## License

- **Spec** (this document): CC-BY-4.0. Use it, fork it, propose
  changes. Attribution to "mnemo contributors / agent-memory
  benchmark v0" is appreciated.
- **Reference harness** (`bench/` once it lands): MIT.
- **mnemo daemon** (the reference implementation): MIT.

## Roadmap

- **v0** (this doc): spec + 8 tasks + 4 metrics + 2 baselines +
  reference harness scaffold.
- **v0.1**: real-world anonymized fixtures from opted-in
  contributors; expanded T2 (longer call chains); add a stop-
  word / boilerplate metric.
- **v0.2**: multi-session corpora; T9 = "re-derive after a 30-day
  gap"; T10 = "merge memory across two project bundles."
- **v1.0**: external implementations submitting results (pull-
  request based, no central ranking yet); spec hardening based
  on real-world implementation feedback.

## Contributing

Open a GitHub issue at <https://github.com/mmct-jsc/mnemo/issues>
with:

- a new task proposal (corpus + prompt sequence + rubric), OR
- a metric refinement / additional metric, OR
- a non-mnemo `Memory`-implementing agent for inclusion in the
  baseline list.

We bias toward keeping v0 small + measurable. New tasks should
clearly fail the existing baselines AND succeed for a plausible
typed-Graph-RAG implementation — otherwise the metric isn't
catching anything new.

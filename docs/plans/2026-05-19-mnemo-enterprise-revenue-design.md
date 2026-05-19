# mnemo enterprise / revenue / A.I.-sponsor strategy menu

> **Status: strategy/design only. Do NOT build from this without an
> explicit decision + a separate `superpowers:writing-plans` pass.**
> Date: 2026-05-19. Owner decision pending.

## Context

mnemo has a registered next phase: *enterprise features that actually
gain revenue and attract sponsorship from A.I. giants*
(Anthropic / OpenAI / Google). These are two goals that imply different
first moves, so this document is **not a single product design**. Per
the owner's brainstorming choices it is:

- a **prioritized strategy menu** across the candidate angles,
- ranked **by strategic ceiling x sponsor-attraction potential**,
- with the **operating capacity each angle requires annotated** (not
  used to down-rank -- "rank by upside only"),
- plus go-to-market reasoning, anti-goals, a decision scorecard, and a
  cheap validating experiment per angle.

The owner picks the bet later; this is the menu and the decision
criteria, not a committed roadmap.

## The asset base every angle monetizes

mnemo's defensibility is not "another memory tool." It is a **unified,
typed Graph-RAG over memory AND code** with four durable, hard-to-copy
assets:

1. **Token-budgeted cited retrieval** (<=800 tokens, `[mnemo:id]`
   citations) -> measurable context-cost / re-derivation ROI.
2. **Provider-neutral + an MCP server + a Claude Code plugin** ->
   already embedded in an A.I.-giant ecosystem with a live distribution
   channel.
3. **Secret-redaction safeguards + local-first** -> a compliance story
   that code-only competitors retrofit painfully.
4. **Feedback / auto-tune telemetry** -> a unique "knowledge-health"
   data asset nobody else has.

The Nebula visualization and the existing hosted middleware are latent
demo / product surfaces that several angles reuse.

## Ranking method

Each angle is scored on the two axes the owner prioritized:

- **Strategic ceiling** -- how large and defensible it can become
  (market size x moat depth).
- **Sponsor-attraction** -- how *directly* it makes Anthropic / OpenAI /
  Google want to fund, feature, or partner (ecosystem leverage,
  reference value, marketplace fit, grant-program fit).

**Capacity required** (solo -> small team -> funded company) is
annotated per item but deliberately **not** used to down-rank. Ties
break on **time-to-first-signal**: the cheapest experiment that
validates or kills the bet.

## The ranked menu

### #1 -- Provider-neutral agent-memory substrate + MCP

*"The standard memory layer for agents."*

- **Wedge:** harden mnemo's MCP server + provider-neutral retrieval
  into *the* drop-in typed Graph-RAG memory any agent (Claude Code,
  OpenAI, Gemini, Cursor, ...) mounts in minutes.
- **Revenue:** indirect -> direct. Ecosystem credibility unlocks grants
  now; hosted/enterprise upsell later (#2 is its commercial endpoint).
- **Sponsor mechanism (highest):** rides Anthropic's MCP push and the
  OpenAI / Google agent ecosystems -> flagship reference integration,
  marketplace / registry listing, startup / build-program grants.
- **Strategic ceiling:** category-defining ("the memory layer").
- **Capacity:** low-medium (positioning + MCP polish + a few
  integrations + program applications).
- **Kill-risk:** a giant ships native agent memory that is
  "good enough."
- **Cheap test:** land 2 non-Claude agent integrations + one accepted
  program / grant application.

### #2 -- Hosted context API (per-query billing on the middleware)

- **Wedge:** the commercial endpoint of #1 -- teams who will not
  self-host pay per query/token for cited, budgeted Graph-RAG.
- **Revenue:** direct, usage-based, scales with agent adoption.
- **Sponsor mechanism:** medium -- marketplace-listable, a
  partner-consumable product.
- **Strategic ceiling:** high (usage-based APIs compound with the
  agent wave).
- **Capacity:** medium (hosted infra + metering / billing).
- **Kill-risk:** margin crush vs. the always-free self-host.
- **Cheap test:** meter the existing middleware, 5 design-partner teams.

### #3 -- Knowledge-health ROI analytics + an open agent-memory benchmark

- **Wedge:** turn the feedback / auto-tune telemetry into
  "agents with mnemo re-derive ~40% less / onboard Nx faster"
  dashboards, plus an **open benchmark** mnemo is the reference
  implementation for.
- **Revenue:** upsell / add-on (weak as a standalone business).
- **Sponsor mechanism:** high -- A.I. giants fund open evals /
  benchmarks that move the ecosystem; maximal credibility per dollar.
- **Strategic ceiling:** medium alone, but force-multiplies #1 / #2.
- **Capacity:** low-medium.
- **Cheap test:** publish the benchmark + one ROI case study.

### #4 -- Team / org memory SaaS (shared graph + RBAC/SSO/audit)

- **Wedge:** "institutional memory" -- a team's Claude/code knowledge
  graph, shared and access-controlled.
- **Revenue:** clean per-seat SaaS -- the most legible model here.
- **Sponsor mechanism:** low -- a standalone SaaS does not make a giant
  amplify it; can read as competitive.
- **Strategic ceiling:** high (large category).
- **Capacity:** heavy -- multi-tenant, SSO, audit, support, billing;
  multi-person.
- **Kill-risk:** becomes a generic SaaS competing on sales, not the
  moat.
- **Cheap test:** shared-graph + RBAC private beta with 3 teams
  *before* any SSO/audit build.

### #5 -- Self-hosted enterprise daemon (on-prem/VPC, SOC2, redaction)

- **Wedge:** regulated orgs that cannot send code/memory to a cloud;
  redaction + local-first as the headline.
- **Revenue:** high-ACV enterprise license.
- **Sponsor mechanism:** lowest -- compliance / on-prem is not what
  giants spotlight.
- **Strategic ceiling:** medium-high but slow.
- **Capacity:** very heavy -- a SOC2 program, security reviews,
  enterprise support.
- **Position:** a later add-on to #1/#2 once there is pull -- never the
  wedge.
- **Cheap test:** inbound-only.

## The flywheel (why the order matters)

#1 makes mnemo the memory layer agents mount -> that adoption is both
the distribution and the grant/sponsor narrative. #3 supplies the
proof, and the open benchmark makes mnemo the reference. Adoption +
proof create demand that #2 monetizes directly with near-zero sales
motion. #4 / #5 are **demand-pull** conversions of that installed base
into seats and enterprise licenses -- upside endpoints, **not** entry
points, and only when a funded team exists. Sponsor-attraction decays
down the list; capacity required rises.

## Anti-goals (what quietly kills this)

- **Never gate the free local-first plugin.** The adoption *is* the
  moat, the distribution, and the sponsor narrative. Monetization is
  strictly **additive** (hosted / team / enterprise on top), never
  subtractive.
- **Never go provider-exclusive.** The entire sponsor thesis is
  neutrality; an exclusive with one giant forfeits leverage with the
  other two. Pursue all three programs in parallel.
- **Do not lead with #4/#5.** Capacity-heavy bets before adoption pull
  burn the team on undifferentiated SaaS / compliance work.
- **Price the hosted API as convenience, not a crippled tier.**
  Self-host stays fully capable and free.
- **Do not chase a giant's roadmap.** Native agent memory is the
  existential risk; defensibility is the unified typed memory+code
  graph + neutrality + the open benchmark, not feature parity.

## Decision scorecard

Score each angle before committing:

| Angle | Strategic ceiling (1-5) | Sponsor-attraction (1-5) | Time-to-first-signal | Capacity tier | Reversible? |
|---|---|---|---|---|---|
| #1 substrate + MCP | 5 | 5 | ~3-4 wk | solo-team | yes |
| #2 hosted API | 4 | 3 | ~4 wk | team | mostly |
| #3 ROI + benchmark | 3 (force-multiplier) | 4 | ~2-3 wk | solo-team | yes |
| #4 team SaaS | 4 | 2 | ~4 wk (beta) | funded | partial |
| #5 enterprise daemon | 4 | 1 | inbound-only | funded | hard |

**Escalation triggers:**
- `>=2 external agent integrations + an accepted grant` -> start #2.
- `design partners asking for a shared graph and willing to pay`
  -> spin #4.
- `inbound regulated-org demand` -> #5 only.

## Validating experiments (ordered to de-risk the next bet)

Each <= 2-4 weeks; run roughly in order so early results de-risk later
bets:

1. **Substrate:** 2 non-Claude MCP integrations + submit to 1 giant
   program. Signal: acceptance / inbound.
2. **Proof:** publish the open agent-memory benchmark + 1 ROI case
   study. Signal: citations / DevRel inbound.
3. **Hosted API:** meter the existing middleware, 5 design partners.
   Signal: willingness-to-pay.
4. **Team SaaS:** shared-graph beta, 3 teams, no SSO yet. Signal: pull
   + price.
5. **Enterprise:** inbound-only.

## Status / next step

This is the validated strategy menu. It is **design only** -- no
implementation is authorized by this document. The next step is the
owner's choice:

- pick an angle (or the #1 -> #3 -> #2 flywheel) and run
  `superpowers:writing-plans` to turn it into a concrete execution
  plan; or
- run one or more of the cheap validating experiments first; or
- shelve until a capacity decision (solo vs. funded) is made.

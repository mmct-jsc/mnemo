---
name: mnemo-impact-analysis
description: Use when the user asks "what breaks if I change `<X>`?" or "blast radius of refactoring `<function>`?". Walks reverse `calls` + `routes_to` + `method_of` edges from a target node and folds in any memory_feedback warnings on the affected functions ("don't refactor X without reading retro Y").
---

# Impact analysis

**Type:** rigid. Blast radius walker + memory overlay.

## Phase 1 - Identify the target

```bash
mnemo query "<function/class/route>" --k 5 --project <project_key>
```

Resolve to a single `code_function` / `code_method` / `code_class`
/ `code_route` node. If ambiguous, ask the user to pick by source
path.

## Phase 2 - Walk reverse calls transitively

Up to a hop cap (default: 3). Each hop:

```bash
curl "http://127.0.0.1:7373/v1/edges?dst_id=<id>&relation=calls"
```

Collect every caller id; recurse on them; deduplicate. Stop at
the hop cap or when the frontier is empty. Group results by hop
depth so the user sees "direct callers", "2 hops out", "3 hops
out".

## Phase 3 - Walk routes_to + at_endpoint

If any caller is the target of a `routes_to` edge (i.e. a route
serves it), surface the route. If any frontend component shares
the route's `code_endpoint`, surface that too -- the blast
radius extends to the UI.

```bash
curl "http://127.0.0.1:7373/v1/edges?dst_id=<function_id>&relation=routes_to"
# Each route's endpoint:
curl "http://127.0.0.1:7373/v1/edges?src_id=<route_id>&relation=at_endpoint"
# All consumers of that endpoint:
curl "http://127.0.0.1:7373/v1/edges?dst_id=<endpoint_id>&relation=at_endpoint"
```

## Phase 4 - Pull memory_feedback warnings

For each affected node (the target + every caller), query for
relevant feedback:

```bash
mnemo query "<function/class name>" --k 8 --project <project_key>
```

Filter to `memory_feedback` hits whose body mentions the
affected node by name. These are the "don't refactor X without
reading retro Y" warnings.

## Phase 5 - Score risk

Per-affected-node risk score (0-3):

- **+1** -- this node is on a tested code path (heuristic:
  `tests` edges exist pointing at it; or its name appears in a
  `code_test` node body).
- **+1** -- this node has been touched in the last 30 days (its
  most-recent `references_function` commit is recent).
- **+1** -- a `closed_by` commit on this node was reverted within
  30 days of landing (high churn signal).

Cap the score at 3. Score >= 2 = "high risk".

## Phase 6 - Render

```
Target: auth.py::validate_token (lines 60-75)

Direct callers (3):
  - auth.py::login                           [risk 2]  [mnemo:abc1]
  - auth.py::refresh                         [risk 1]  [mnemo:abc2]
  - api/middleware.py::auth_middleware       [risk 3]  [mnemo:abc3]

2 hops out (5):
  - GET /api/users -> handler -> login       [mnemo:def1]
  - GET /api/sessions -> handler -> refresh  [mnemo:def2]
  ...

Frontend consumers:
  - UsersPage at /api/users                  [mnemo:ghi1]
  - SessionsPage at /api/sessions            [mnemo:ghi2]

Memory warnings:
  - feedback_mqtt_auth_flake [mnemo:jkl1]
    "Tokens flake under MQTT broker reprovision; refactoring
    validate_token without preserving the short-circuit will
    reintroduce the flake."
```

## Phase 7 - Recommendation

One sentence: "Safe to refactor" / "Refactor with care" /
"Don't refactor without addressing <warning>".

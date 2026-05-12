---
name: mnemo-trace-route
description: Use when the user asks "how does `<route>` work?" or "what handles GET /api/users?". Walks the cross-stack chain Component -> Endpoint -> Route -> Handler -> Service so the user sees the full path a request takes from UI click to DB call.
---

# Trace route

**Type:** rigid. The sitemap walker.

## Phase 1 - Identify the endpoint

```bash
mnemo query "<HTTP method> <path>" --k 5
```

Look for a `code_endpoint` hit whose source_path is
`endpoint:<METHOD>:<path>`. If multiple endpoints share the path
across stacks, prefer the one with the highest fanout (incoming
`at_endpoint` edges).

The /code/<project>/sitemap UI lists every endpoint URI plus its
attached routes / components in one table.

## Phase 2 - Walk routes_to

For each `code_route` attached to the endpoint:

```bash
curl "http://127.0.0.1:7373/v1/edges?src_id=<route_id>&relation=routes_to"
```

This gives you the handler function. Record its node id and name.

## Phase 3 - Walk calls

From the handler, follow Tier 2 `calls` edges to see what the
handler does:

```bash
curl "http://127.0.0.1:7373/v1/edges?src_id=<handler_id>&relation=calls"
```

Chase the chain until you hit either:
- A DB / service-layer function (terminal).
- A node with no outgoing `calls` (leaf).

## Phase 4 - Walk frontend (cross-stack)

For each `code_component` attached to the SAME endpoint via
`at_endpoint`, surface its component name + source path. That's
the React side of the request.

## Phase 5 - Memory citations

Pull any `memory_feedback` nodes that mention route segments
along the chain:

```bash
mnemo query "<route path>" --k 8
```

Filter to `memory_feedback` hits.

## Phase 6 - Render

Linear chain with file:line anchors and citations. Example:

```
GET /api/users

Frontend:
  UsersPage (UsersPage.tsx:12-45)  [mnemo:abc123]
    fetch('/api/users')

Backend:
  Route: GET /api/users  [mnemo:def456]
    -> Handler: list_users (api/users.py:12-25)  [mnemo:ghi789]
        -> Service: UserService.find_all (services/user.py:8-22)  [mnemo:jkl012]
            -> queries_table('users')

Memory:
  feedback_user_pagination_quirk -- "List endpoint paginates by
  default; opt out via ?all=true". [mnemo:mno345]
```

## Done criterion

Every step has a citation. If a step is missing (e.g. the handler
isn't a Tier 1 node because it's nested in a factory), flag it
explicitly with "(unresolved -- the route's `handler_source_path`
is `<file>:<line>` but no Tier 1 node exists there)".

---
name: mnemo-trace-call
description: Use when the user asks "where is `<function>` called from?" or "what does `<function>` call?". Walks the Tier 2 `calls` edges from a target function and surfaces the caller / callee list with line-range citations.
---

# Trace call

**Type:** rigid. Each step is a small lookup; the value is in the
walking, not in any one query.

## Phase 1 - Identify the target

Resolve the function the user named:

```bash
mnemo query "<function name>" --k 5 --project <project_key>
```

Among the hits, prefer `code_function` / `code_method` types. If
two functions share the name (overloads, conditional defs in the
same file), ask the user to disambiguate by source path.

## Phase 2 - Pull the callers

```bash
# Direct HTTP -- the daemon's edge endpoint is shorter than the
# wrapper. Replace <node_id> with the function's node id.
curl "http://127.0.0.1:7373/v1/edges?dst_id=<node_id>&relation=calls"
```

Or open the function detail page; the "Callers" section lists
them with line ranges:

<http://127.0.0.1:7373/code/<project_key>/function/<node_id>>

## Phase 3 - Pull the callees

```bash
curl "http://127.0.0.1:7373/v1/edges?src_id=<node_id>&relation=calls"
```

The page shows the same data under "Callees".

## Phase 4 - Render

Linear list with file:line anchors. Each entry cites
`[mnemo:<node_id>]`:

```
auth.py::login (lines 42-58)
  Called by:
    - api/users.py::login_endpoint (lines 12-15)  [mnemo:abc123]
    - tests/test_auth.py::test_login (lines 22-30) [mnemo:def456]
  Calls:
    - db.py::find_user (lines 100-115)             [mnemo:ghi789]
    - auth.py::validate_token (lines 60-75)        [mnemo:jkl012]
```

## Phase 5 - Suggest follow-up

- "Expand the caller graph another hop?" -> recurse the same walk
  on the caller list.
- "Trace the route that hits this?" -> follow `routes_to` edges
  upstream from the function.
- "Show the commits that touched this function?" -> chain into
  `mnemo:why-is-this-here`.

## Confidence note

`calls` edges carry a `confidence` value:
- `0.95` -- within-file resolution (high).
- `0.8`  -- cross-file via the imports edge.
- Unresolved calls do NOT produce an edge by design.

When the confidence is low, flag it in the rendering. Tier 2 is
best-effort; ambiguous receivers (e.g. `self.method()` outside a
class context) silently drop.

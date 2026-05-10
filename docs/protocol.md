# mnemo HTTP protocol (v1)

mnemo's daemon exposes a small HTTP API on `127.0.0.1:7373`. Everything
public lives under `/v1/`. UI HTML pages (`/`, `/nodes-page`, etc.) are
not part of this contract -- they're for browsers, not adapters.

## Versioning

- Every public route is prefixed with `/v1/`.
- Every response carries the `X-Mnemo-Api-Version: 1` header (including
  redirects).
- Legacy unversioned paths (`/health`, `/sources`, `/reindex`,
  `/nodes`, `/query`, `/audit`, `/config`) return `308 Permanent
  Redirect` to their `/v1/...` equivalent for the v1.1 series.
  These will be removed in v1.2.
- The OpenAPI spec is published at `/v1/openapi.json` AND at the
  default `/openapi.json` (which drives the built-in `/docs` Swagger
  UI). Both are filtered to `/v1`-only paths.

When you build an adapter, treat `X-Mnemo-Api-Version` as a hard
contract and the OpenAPI schema as the source of truth for shapes.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/health` | Daemon liveness + version + node counts |
| POST | `/v1/query` | Hybrid Graph-RAG query, budget-capped |
| GET | `/v1/audit` | Recent queries + scores |
| GET | `/v1/nodes` | List nodes with optional type/project filters |
| GET | `/v1/nodes/{id}` | One node by id |
| PUT | `/v1/nodes/{id}` | Update body / description / type / project_key / base |
| DELETE | `/v1/nodes/{id}` | Delete a node |
| GET | `/v1/sources` | List registered sources |
| POST | `/v1/sources` | Register a new source |
| PATCH | `/v1/sources` | Partial update by path |
| DELETE | `/v1/sources?path=...` | Remove a source |
| POST | `/v1/reindex` | Reindex all enabled sources |
| GET | `/v1/config` | Read scoring weights + defaults |
| PUT | `/v1/config` | Update scoring weights / defaults |
| POST | `/v1/config/reset` | Restore defaults |
| POST | `/v1/projects/resolve` | `{path}` → `{project_key}` (canonical) |
| GET | `/v1/projects/active` | Current active project (or `null`) |
| POST | `/v1/projects/active` | `{path}` → activate |
| DELETE | `/v1/projects/active` | Clear active |
| GET | `/v1/projects/known` | Distinct project keys with counts |
| GET | `/v1/fs/suggest?prefix=...` | Filesystem dir suggestions for the UI |

## Project-key derivation (canonical algorithm)

The same algorithm runs in the daemon (Python `mnemo.paths.project_key`)
and in any adapter that wants to compute keys offline.

```
project_key(path):
  1. Resolve symlinks; produce an absolute path.
     - On POSIX: case-sensitive.
     - On Windows: lowercase the drive letter, keep the rest as-is.
  2. Replace ":" with "-".
     Replace "/" and "\\" with "-".
  3. Collapse consecutive "-" runs into a single "-".
  4. Strip leading and trailing "-".
```

**Reference fixtures.** The daemon test suite ships
`daemon/tests/fixtures/project_keys.json` with 40+ `(path,
expected_key)` pairs covering Linux, macOS, and Windows shapes.
Every adapter's test suite POSTs each input to
`/v1/projects/resolve` and asserts equivalence with its local port
of the algorithm. Drift fails CI.

## Active-project contract (hybrid)

- The daemon stores at most one active project (singleton row in
  the `active_project` table).
- `/v1/query` accepts an optional `project_key` in the request body.
  Per-call value takes precedence; absence falls back to the active
  project.
- When a project is active and `config.project_isolation_mode` is
  `'strict'` (default), `/v1/query` hard-filters candidate nodes to
  `(project_key == active OR base == 1)` before scoring.
- Setting `'boost'` mode disables the hard filter; the
  `epsilon * project_score` scoring term still applies, matching
  v1.0 behavior.

## BASE knowledge

A node with `base = 1` (frontmatter `base: true`) bypasses project
isolation. BASE nodes appear in every project's queries regardless
of the active project. Use BASE for global preferences, hard rules,
and patterns that genuinely apply across every codebase the user
touches. Default is `false`.

## Failure model

The daemon is local-only and trusted. Adapters MUST be additive:
when the daemon is unreachable, slow, or returns an error, the
adapter's host application (the LLM call, the IDE, the chat UI)
should proceed without injection. Don't block the user's primary
workflow on mnemo.

## Adding a new endpoint (for daemon contributors)

1. Add the route inside the `v1` `APIRouter` in `daemon/mnemo/server.py`.
2. Add Pydantic models to `daemon/mnemo/api_schemas.py`.
3. Add an integration test in `daemon/tests/integration/test_v1_*.py`.
4. The OpenAPI spec auto-updates on next request -- nothing else
   to wire.

Internal HTML / HTMX routes go on the `app` directly (not the
router) and use `include_in_schema=False` so they stay out of the
spec.

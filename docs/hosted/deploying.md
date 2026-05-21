# Deploying mnemo as a hosted-tier service

> **Status: Phase 3a (operator surface only).** Key issuance + billing
> ship in v0.1; the API-key authentication on `/v1/query`, the
> metering hook, and the quota-enforcement 429s land in Phase 3b
> (Tasks 2.3-2.5).

This guide is for operators running mnemo as a hosted service for
multiple consumers — not for solo self-host users. Self-host stays
**fully free + fully capable** without any of the steps below.

## What's shipped today (Phase 3a)

- `mnemo key {create,list,revoke}` — issue + lifecycle API keys.
- `mnemo billing report --period YYYY-MM` — CSV billing rollup.
- Tables: `api_key`, `quota`, `usage_period` (harmless for any
  install that never enables hosted mode).

## What's NOT shipped yet (Phase 3b — coming)

- Optional `Depends(api_key_or_local)` on `/v1/query` gated by a
  config flag (defaults OFF; self-host stays unauthenticated).
- Metering hook that writes to `usage_period` post-request.
- Quota enforcement (HTTP 429 with `Retry-After` when a key
  exceeds its monthly quota).

Until those land, the keys + quotas you set with this Phase 3a
surface are inert at the request path. You can stage operations
(issue real keys, set quotas, generate reports against
manually-seeded usage rows) but the daemon does NOT yet
authenticate or meter requests by api-key.

## Prerequisites

- A server with Python 3.11+, `uv`, and outbound network for the
  embedding model download (`sentence-transformers all-MiniLM-L6-v2`,
  ~22 MB one-time).
- mnemo installed (`./install.sh`).
- A reverse proxy in front of the daemon (nginx, Caddy, Traefik).
  **The daemon binds `127.0.0.1` only**; the proxy is what exposes
  it on the public interface. This is a hard rule from
  `CLAUDE.md` and not configurable.

## Step 1 — Issue your first key

```bash
mnemo key create "design-partner-A"
```

Output:

```
RAW KEY:
  <44-char base64 secret>

*** IMPORTANT *** copy the raw key NOW. mnemo stores only the
salted hash; it will NOT be shown again.

id:   <uuid-hex>
name: design-partner-A
```

Hand the raw key to the partner via your secure-comms channel of
choice. The daemon never sees the raw key again — only its salted
SHA-256 hash and the per-key 16-byte salt are persisted.

## Step 2 — Set a quota for the key

> **Phase 3a limitation**: there is no `mnemo key set-quota`
> command yet. Set quotas via the daemon's SQLite directly:

```bash
sqlite3 ~/.claude/mnemo/mnemo.db <<SQL
INSERT INTO quota (api_key_id, period, max_queries, max_tokens)
VALUES ('<key_id from create output>', 'monthly', 10000, 2000000);
SQL
```

A future `mnemo key set-quota` subcommand will wrap this; until
then the SQL is stable + documented.

## Step 3 — Run the billing report monthly

```bash
mnemo billing report --period 2026-05 > billing-2026-05.csv
```

CSV columns (stable contract; downstream billing systems can bind
to these):

```
key_name,queries,tokens,quota_queries,quota_tokens,over_quota
design-partner-A,1234,89012,10000,2000000,false
```

- `queries` / `tokens` — actual usage in the period.
- `quota_queries` / `quota_tokens` — the limits set for the key
  (0 when no quota is configured).
- `over_quota` — `true` only if a quota IS set and at least one
  dimension exceeded it.

Keys with zero usage in the period are included as zero rows.
Revoked keys still appear if they were active during the period
(revenue attribution survives mid-period revocations).

## Step 4 — Revoke a key

```bash
mnemo key revoke <key_id>
```

The key is no longer accepted (when Phase 3b auth lands).
Existing usage rows are preserved for historical billing.

Cascade behavior — when an api_key row is deleted (not revoked;
hard delete, e.g. via `sqlite3` directly):
- All `quota` rows for that key are removed.
- All `usage_period` rows for that key are removed.

**Don't hard-delete keys you've billed against.** Revoke
instead — the audit trail matters.

## Reverse-proxy template (nginx)

```nginx
server {
    listen 443 ssl http2;
    server_name memory.example.com;

    # TLS config -- standard.
    ssl_certificate     /etc/letsencrypt/live/memory.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/memory.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:7373;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        # SSE keeps connections open. Match the daemon's tolerance.
        proxy_buffering off;
        proxy_read_timeout 1h;
    }
}
```

When Phase 3b lands, partners will pass their key as
`Authorization: Bearer <raw_key>`; the proxy passes the header
through unchanged.

## Operational hygiene

- **Backups**: SQLite file at `~/.claude/mnemo/mnemo.db`. Snapshot
  it before any quota/key bulk edit. WAL mode means a
  `cp mnemo.db backup.db` while the daemon is running is
  consistent-on-restart.
- **Log rotation**: the daemon logs at INFO to its own stderr;
  the reverse proxy is the right place to capture + rotate
  request logs.
- **Restart on upgrade**: gotcha-32 — kill the real `:7373` PID
  via `netstat` before `mnemo daemon start`. The pidfile is
  per-port but the listener can outlive a poorly-stopped run.

## Anti-goals (non-negotiable per the strategy doc)

- The **free local-first plugin stays fully capable**. No
  feature is removed or gated to push users to the hosted tier.
- The hosted tier is a **convenience** for partners who want a
  central deployment, not a **paywall** for the local plugin.
- The daemon **never binds `0.0.0.0`** directly. The reverse
  proxy is always in the way.

## Next

- Phase 3b (Tasks 2.3 + 2.4 + 2.5) — auth + metering + quota
  enforcement on `/v1/query`. This file will be updated with the
  config-flag name + the request shape.
- v0.2 of the hosted surface — `mnemo key set-quota` CLI; daily
  quota granularity; `Authorization: Bearer` header standard.

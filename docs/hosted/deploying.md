# Deploying mnemo as a hosted-tier service

> **Status: Phase 3 of the hosted-tier roadmap fully shipped** — issuance + billing + deploying doc (Phase 3a, PRs #89 / #15) and the runtime trio of auth + metering + quota enforcement on `/v1/query` (Phase 3b, PRs #90 / #16). The hosted tier is OFF by default; an operator opt-in via `Config.hosted_auth_enabled = true` activates it. Self-host loopback behavior is unchanged byte-for-byte.

This guide is for operators running mnemo as a hosted service for
multiple consumers — not for solo self-host users. Self-host stays
**fully free + fully capable** without any of the steps below.

## What's shipped (Phase 3a + 3b)

- `mnemo key {create,list,revoke}` — issue + lifecycle API keys.
- `mnemo billing report --period YYYY-MM` — CSV billing rollup.
- `Config.hosted_auth_enabled` config flag (default `false`).
- `api_key_or_local` FastAPI dependency on `POST /v1/query` —
  flag-gated; loopback exempt even when flag is on.
- Per-request metering hook writes to `usage_period`
  (atomic UPSERT, UTC `YYYY-MM` period).
- Pre-handler quota check returns HTTP 429 with
  `Retry-After: <seconds-to-next-month-UTC>` header when
  a key exceeds its monthly limit.
- Tables: `api_key`, `quota`, `usage_period` (harmless for any
  install that never enables hosted mode).

## What's NOT shipped yet

- Per-key prefix-indexed verify (v0.1 verifies via O(N) over the
  active-key set; acceptable for ≤ ~1,000 keys; v0.2 if you need
  more).
- Daily quota granularity (v0.1 is monthly only).
- Implicit-signal aggregation in ROI metrics (Phase 2 / v0.1
  surface; v0.2 plumbs `inferred_requery` + `cite_copied` into
  the dashboard's `rederivations_avoided`).

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

## Step 2 — Turn on hosted-tier authentication

Flip the config flag to require API keys for non-loopback requests:

```bash
# Quickest: jq one-liner against ~/.claude/mnemo/settings.json
jq '.hosted_auth_enabled = true' ~/.claude/mnemo/settings.json \
   > ~/.claude/mnemo/settings.json.tmp \
   && mv ~/.claude/mnemo/settings.json.tmp ~/.claude/mnemo/settings.json
```

The daemon reads the flag fresh on every request, so the change
takes effect **on the next inbound request — no restart needed.**

**What the flag does:**

| Request shape | Flag OFF (default) | Flag ON |
|---|---|---|
| Loopback (127.0.0.1 / ::1 / localhost) | Accepted, no auth | Accepted, no auth (loopback exemption — local UI / CLI / plugin keeps working) |
| Non-loopback, no `Authorization` header | Accepted (no key required) | **401 with `WWW-Authenticate: Bearer realm="mnemo"`** |
| Non-loopback, `Authorization: Bearer <invalid>` | Accepted (header ignored when flag off) | **401 with `WWW-Authenticate: Bearer realm="mnemo", error="invalid_token"`** |
| Non-loopback, `Authorization: Bearer <valid>` | Accepted (header ignored) | Accepted + the request is metered against the key |

## Step 3 — Set a quota for the key

```bash
mnemo key set-quota <key_id> --max-queries 10000 --max-tokens 2000000
```

Re-running `set-quota` with new values updates the existing row in
place (UPSERT on `(api_key_id, period)`); no need to delete first.
`--period` defaults to `monthly` (the only granularity v0.1
recognizes). Setting both limits to `0` is valid — it stages a key
as "exists but rejects every request" until you raise the limits.

When a key hits either dimension (`queries >= max_queries` OR
`tokens >= max_tokens`), the next `/v1/query` request returns:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 1234567
Content-Type: application/json

{"detail": "Monthly quota exceeded: queries quota exceeded for period"}
```

`Retry-After` is seconds-to-next-UTC-month, so the client knows
exactly when their bucket resets.

**Strict-`>=` semantics**: the user gets exactly `max_queries`
successful requests before the next one is rejected. Tokens may
overshoot by one request's worth (we don't know upfront how many
tokens a request will use); this is documented v0.1 slack.

**Open-billing posture**: leave a key quota-less (no row in
`quota`) to track usage without ever rejecting. The billing
report's `over_quota` column stays `false` for quota-less keys.

## Step 4 — Run the billing report monthly

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

## Step 5 — Revoke a key

```bash
mnemo key revoke <key_id>
```

The key is no longer accepted (the next `verify_api_key` call
against it returns `None`, the request 401s). Existing usage
rows are preserved for historical billing.

Cascade behavior — when an api_key row is **deleted** (not revoked;
hard delete via `sqlite3` directly):
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

        # Pass the partner's Authorization: Bearer <key> through unchanged.
        # Do not strip; the daemon's api_key_or_local dependency reads it.
        # SSE keeps connections open -- match the daemon's tolerance.
        proxy_buffering off;
        proxy_read_timeout 1h;
    }
}
```

The proxy forwards `Authorization: Bearer <raw_key>` verbatim;
the daemon does the verification.

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
  The hosted-auth flag itself reads fresh per request, so most
  config changes don't require restart — only daemon version
  upgrades do.
- **Loopback exemption is by-IP, not by-account**: if you run a
  Tailscale / WireGuard mesh that exposes the daemon to peers,
  those peers do NOT count as loopback (their IP is non-loopback)
  and will need a key when the flag is on. This is intentional —
  the loopback exemption is for the local machine only.

## Anti-goals (non-negotiable per the strategy doc)

- The **free local-first plugin stays fully capable**. No
  feature is removed or gated to push users to the hosted tier.
- The hosted tier is a **convenience** for partners who want a
  central deployment, not a **paywall** for the local plugin.
- The daemon **never binds `0.0.0.0`** directly. The reverse
  proxy is always in the way.
- **`hosted_auth_enabled = true` does NOT affect the loopback
  path.** Verified by the unit test suite. If a future change
  ever breaks this, the
  `test_query_loopback_exempt_when_flag_on`
  test fails loudly.

## Next

- Daily quota granularity for finer billing periods.
- Implicit-signal aggregation in the ROI dashboard's
  `rederivations_avoided` (Phase 2 v0.2 follow-up).
- Indexed prefix lookup for `verify_api_key` at scale (only
  matters above ~1,000 active keys).

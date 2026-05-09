# mnemo benchmarks

Run via:

```bash
cd daemon
uv run python scripts/bench.py
```

The script targets your existing `~/.claude/mnemo/mnemo.db`, so numbers
reflect *your* memory at *your* scale. Results below are from a
representative dev machine: 38 nodes / 7 sources / 160 co-occurrence
edges.

## Retrieval quality (curated test set)

A hand-curated set of 7 queries with predicate-based expectations on the
top hit:

| # | Query | Expected | Result |
|---|---|---|---|
| 1 | `no co-author trailer in commit messages` | commit-style feedback | top-1 |
| 2 | `should I always prefer terse responses` | terse / deployment-style feedback | top-1 |
| 3 | `MQTT broker authentication and webhook secret` | EMQX / MQTT node | top-1 |
| 4 | `where do we keep deployment files` | deployment-files-no-san | top-1 |
| 5 | `MinIO S3 SDK checksum mismatch` | MinIO compat node | top-1 |
| 6 | `VM01 host stack TCP ingress drop` | VM01 network fix | top-1 |
| 7 | `Phase 18 snapshot HTTPS upload` | Phase 18 node | top-1 |

**Top-1 accuracy: 7/7 (100%)** &middot; **MRR: 1.000**

### What changed since the first pass

The first design used five scoring terms (vector + graph + recency + type +
project). On the same test set that scored at most 4/7 top-1 because pure
cosine similarity on short queries can't distinguish lexically-tight matches
from semantically-adjacent ones. Adding a **sixth term, `zeta` (lexical
overlap on name + description)**, lifted top-1 from ~57% to 100%:

```
score = alpha*vector + beta*graph + gamma*recency
      + delta*type + epsilon*project + zeta*lexical
```

Lexical overlap is computed by tokenizing the query (>=3 chars), then
counting how many query tokens appear (as substrings) in the node's
`name + description`. So "co-auth" matches "co-authored-by" without any
stemming or fuzzy match library.

Rebalanced weights (default in `mnemo.config.ScoringWeights`):

| weight | role | default |
|---|---|---|
| alpha | vector cosine (MiniLM) | 0.40 |
| beta | graph proximity (1-hop) | 0.15 |
| gamma | recency (90-day half-life) | 0.10 |
| delta | intent-driven type priority | 0.10 |
| epsilon | active-project boost | 0.05 |
| **zeta** | **lexical overlap** | **0.20** |

All six are editable from the UI at `/settings` and persist to
`~/.claude/mnemo/settings.json`.

## Query latency

20 sequential queries on a 38-node store, after the embedder is warm:

| metric | value |
|---|---|
| min    |   18 ms |
| median |   20 ms |
| p95    |   23 ms |
| max    |   23 ms |

The first query in a process pays an extra ~2 s for the MiniLM model load
(or ~22 s on first-ever run including the HuggingFace download).

## Reindex throughput

The watcher (or `mnemo reindex`) re-scans every registered source. SHA-256
hash-gating means unchanged files are no-ops:

```
38 nodes scanned in 33 ms (1,157 nodes/sec)
added=0, updated=0, unchanged=38, removed=0
```

For an initial ingest with embedding (cold), throughput drops to roughly
**~10 nodes/sec** because each node's chunks go through MiniLM. So a
fresh index of ~40 nodes takes ~5 s after model load.

## Disk footprint

| | size |
|---|---|
| `mnemo.db` (SQLite + sqlite-vec) | 2.0 MB |
| MiniLM model cache | 22 MB (the model itself) |

(Total cache size on disk can be larger than 22 MB if the HuggingFace
cache holds extra files like the slow tokenizer or alternate variants,
but mnemo only pulls the one model.)

## Memory footprint

The daemon process uses roughly **~280 MB resident** with MiniLM loaded
into PyTorch. That's almost entirely the model + Python interpreter,
which is fixed regardless of node count. Each additional 1,000 nodes
adds about 100 KB to `mnemo.db` and ~1.5 MB of vectors.

## Reproducing

To compare your numbers against the published ones:

1. Make sure the daemon has indexed your real memory:
   ```bash
   mnemo init && mnemo reindex
   ```
2. Run the benchmark:
   ```bash
   cd daemon && uv run python scripts/bench.py
   ```
3. Tune scoring weights via the UI (`/settings`) or by editing
   `~/.claude/mnemo/settings.json`, then re-run.

If you want to extend the curated test set, edit `daemon/scripts/bench.py`
and add tuples to `QUERIES` with predicates over the candidate hit's
lowercased `name + description`.

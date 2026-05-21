"""Benchmark mnemo's retrieval quality and performance.

Runs a curated set of queries against the user's real ~/.claude/ memory
and reports:

- Top-1 match rate against expected name patterns
- Mean Reciprocal Rank (MRR) across the curated set
- Query latency (p50, p95, max) over N runs
- Reindex throughput
- Memory footprint of the daemon process

Run: ``uv run python scripts/bench.py``
"""

from __future__ import annotations

import statistics
import sys
import time

from mnemo import config, ingest, paths, retrieve
from mnemo.embed import Embedder
from mnemo.store import Store

# Curated queries -> predicate over hit name+description (lowercased).
# Each tuple: (prompt, predicate, "what we expect")
QUERIES: list[tuple[str, callable, str]] = [
    (
        "no emojis in commit messages",
        lambda t: "commit-style" in t or "emoji" in t,
        "commit-style feedback",
    ),
    (
        "should I always prefer terse responses",
        lambda t: "terse" in t or "deployment-style" in t or "minimal config" in t,
        "terse-output / deployment-style feedback",
    ),
    (
        "MQTT broker authentication and webhook secret",
        lambda t: "emqx" in t or "mqtt" in t or "webhook" in t,
        "EMQX / MQTT node",
    ),
    (
        "where do we keep deployment files",
        lambda t: "deployment-files" in t or "deploy" in t,
        "deployment-files-no-san",
    ),
    (
        "MinIO S3 SDK checksum mismatch",
        lambda t: "minio" in t or "checksum" in t or "s3" in t,
        "MinIO compat node",
    ),
    (
        "VM01 host stack TCP ingress drop",
        lambda t: "vm01" in t or "host stack" in t or "504" in t,
        "VM01 network fix",
    ),
    (
        "Phase 18 snapshot HTTPS upload",
        lambda t: "phase 18" in t or "snapshot" in t or "https" in t,
        "Phase 18 node",
    ),
]


def _safe(text: str) -> str:
    return text.encode("ascii", errors="replace").decode("ascii")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not ingest.discover_default_sources(paths.claude_home()):
        print("no real memory under ~/.claude/; cannot benchmark")
        sys.exit(1)

    print("=" * 70)
    print("mnemo benchmark")
    print("=" * 70)

    cfg = config.load()
    print("\nactive scoring weights:")
    print(f"  alpha   (vector)   = {cfg.scoring.alpha:.2f}")
    print(f"  beta    (graph)    = {cfg.scoring.beta:.2f}")
    print(f"  gamma   (recency)  = {cfg.scoring.gamma:.2f}")
    print(f"  delta   (type)     = {cfg.scoring.delta:.2f}")
    print(f"  epsilon (project)  = {cfg.scoring.epsilon:.2f}")
    print(f"  zeta    (lexical)  = {cfg.scoring.zeta:.2f}")

    # Connect to existing daemon DB so we hit real co-occurrence edges.
    store = Store(paths.db_path())
    embedder = Embedder()

    # --- Setup pass: register sources and reindex if needed ---
    t0 = time.perf_counter()
    ingest.register_default_sources(store, paths.claude_home())
    n_nodes_before = sum(store.count_nodes().values())
    if n_nodes_before == 0:
        print("\nno nodes yet; running first reindex (this is the slow path)...")
        report = ingest.reindex(store, embedder=embedder)
        ingest_time = time.perf_counter() - t0
        print(f"  initial ingest: {report.added} nodes in {ingest_time:.2f}s")
    else:
        print(f"\nusing existing index: {n_nodes_before} nodes")

    embedded = store.list_embedded_node_ids()
    print(f"  embedded:       {len(embedded)} / {sum(store.count_nodes().values())} nodes")

    # --- Quality benchmark ---
    print("\n" + "-" * 70)
    print("Retrieval quality (top-K against curated expectations)")
    print("-" * 70)

    top1_hits = 0
    top3_hits = 0
    rr_sum = 0.0
    for prompt, predicate, expected in QUERIES:
        result = retrieve.query(
            store, embedder, prompt, k=10, budget_tokens=600, update_graph=False
        )
        ranks = []
        for i, h in enumerate(result.hits, start=1):
            text = (h.name + " " + (h.description or "")).lower()
            if predicate(text):
                ranks.append(i)
        rank = ranks[0] if ranks else 0  # 0 = miss
        if rank == 1:
            top1_hits += 1
            mark = "[1]"
        elif 0 < rank <= 3:
            top3_hits += 1
            mark = f"[{rank}]"
        elif rank > 0:
            mark = f" {rank} "
        else:
            mark = " - "
        rr_sum += (1.0 / rank) if rank else 0.0
        top = result.hits[0] if result.hits else None
        top_name = _safe(top.name)[:40] if top else "(no hits)"
        print(
            f"  {mark}  {_safe(prompt)[:42]:42s}  expected: {_safe(expected)[:25]:25s}  -> {top_name}"
        )

    n = len(QUERIES)
    print(f"\n  top-1 accuracy:      {top1_hits}/{n}  ({100 * top1_hits / n:.0f}%)")
    print(
        f"  top-3 accuracy:      {top1_hits + top3_hits}/{n}  ({100 * (top1_hits + top3_hits) / n:.0f}%)"
    )
    print(f"  MRR (mean recip rank): {rr_sum / n:.3f}")

    # --- Latency benchmark ---
    print("\n" + "-" * 70)
    print("Query latency (single query, repeated 20x)")
    print("-" * 70)

    sample_prompt = "deploy process and config"
    embedder.embed_text("warmup")  # ensure model loaded
    latencies_ms: list[float] = []
    for _ in range(20):
        t = time.perf_counter()
        retrieve.query(store, embedder, sample_prompt, k=5, budget_tokens=400, update_graph=False)
        latencies_ms.append((time.perf_counter() - t) * 1000)
    latencies_ms.sort()
    p50 = statistics.median(latencies_ms)
    p95 = (
        latencies_ms[int(len(latencies_ms) * 0.95)]
        if len(latencies_ms) >= 20
        else max(latencies_ms)
    )
    print(f"  median:  {p50:6.1f} ms")
    print(f"  p95:     {p95:6.1f} ms")
    print(f"  max:     {max(latencies_ms):6.1f} ms")
    print(f"  min:     {min(latencies_ms):6.1f} ms")

    # --- Reindex throughput ---
    print("\n" + "-" * 70)
    print("Reindex throughput (no-op on unchanged files; hash-gated)")
    print("-" * 70)
    t = time.perf_counter()
    report = ingest.reindex(store)
    reindex_time = time.perf_counter() - t
    n_total = sum(store.count_nodes().values())
    rate = n_total / reindex_time if reindex_time else 0
    print(f"  {n_total} nodes scanned in {reindex_time * 1000:.0f} ms ({rate:.0f} nodes/sec)")
    print(
        f"  added={report.added}, updated={report.updated}, "
        f"unchanged={report.unchanged}, removed={report.removed}"
    )

    # --- DB footprint ---
    print("\n" + "-" * 70)
    print("Disk footprint")
    print("-" * 70)
    db_size = paths.db_path().stat().st_size if paths.db_path().exists() else 0
    cache_dir = paths.cache_dir()
    cache_size = 0
    if cache_dir.is_dir():
        for f in cache_dir.rglob("*"):
            if f.is_file():
                cache_size += f.stat().st_size
    print(f"  mnemo.db:       {db_size / 1024:7.1f} KB")
    print(f"  model cache:    {cache_size / (1024 * 1024):7.1f} MB  (MiniLM ~22 MB)")

    print("\n" + "=" * 70)
    store.close()


if __name__ == "__main__":
    main()

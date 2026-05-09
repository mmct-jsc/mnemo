"""End-to-end smoke test against the user's real ~/.claude/ memory.

Skipped automatically when the user has no Scope B memory to ingest. When
present, exercises every layer:

  ingest -> embed -> retrieve (hybrid Graph-RAG) -> audit -> HTTP -> UI

Module-scoped fixture pays the model-load cost once. The test writes to a
temp DB so it never pollutes the user's actual mnemo state. The embedder
uses the real ~/.claude/mnemo/cache/ so the MiniLM model isn't
re-downloaded on every run.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TypedDict

import pytest
from fastapi.testclient import TestClient

from mnemo import ingest, paths, retrieve
from mnemo.embed import Embedder
from mnemo.ingest import ReindexReport
from mnemo.server import create_app
from mnemo.store import Store


class SmokeContext(TypedDict):
    store: Store
    embedder: Embedder
    report: ReindexReport


@pytest.fixture(scope="module")
def smoke(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SmokeContext]:
    """Single shared setup for all smoke tests in this module."""
    sources = ingest.discover_default_sources(paths.claude_home())
    if not sources:
        pytest.skip("no real memory under ~/.claude/ to smoke against")

    home = tmp_path_factory.mktemp("smoke-home")
    store = Store(home / "mnemo.db")
    # Use the user's real model cache so we don't redownload MiniLM.
    embedder = Embedder()

    ingest.register_default_sources(store, paths.claude_home())
    report = ingest.reindex(store, embedder=embedder)

    yield SmokeContext(store=store, embedder=embedder, report=report)
    store.close()


# --- Layer 1: ingestion --------------------------------------------------


def test_sources_registered(smoke: SmokeContext) -> None:
    sources = smoke["store"].list_sources()
    assert len(sources) > 0
    # At least one of the sources should be a memory_dir (per-project).
    kinds = {s.kind for s in sources}
    assert kinds & {"memory_dir", "claude_md"}


def test_reindex_added_real_nodes(smoke: SmokeContext) -> None:
    r = smoke["report"]
    assert r.added > 0, "expected at least one new node from real memory"
    assert r.errors == [], f"reindex errors: {r.errors}"


def test_reindex_is_idempotent(smoke: SmokeContext) -> None:
    """Running reindex again with the same files should change nothing."""
    second = ingest.reindex(smoke["store"], embedder=smoke["embedder"])
    assert second.added == 0
    assert second.updated == 0
    assert second.errors == []
    assert second.unchanged > 0


# --- Layer 2: embedding --------------------------------------------------


def test_every_node_is_embedded(smoke: SmokeContext) -> None:
    store = smoke["store"]
    embedded = store.list_embedded_node_ids()
    all_node_ids = {n.id for n in store.list_nodes(limit=10_000)}
    missing = all_node_ids - embedded
    assert not missing, f"{len(missing)} nodes missing embeddings"


# --- Layer 3: retrieval --------------------------------------------------


def test_query_returns_hits(smoke: SmokeContext) -> None:
    result = retrieve.query(smoke["store"], smoke["embedder"], "what is the rule for commits", k=5)
    assert len(result.hits) >= 1
    assert result.tokens_used <= 800
    assert all(h.citation.startswith("[mnemo:") for h in result.hits)


def test_query_dedupes_chunks_per_node(smoke: SmokeContext) -> None:
    """A query likely to hit a long-bodied node should still return distinct nodes."""
    result = retrieve.query(smoke["store"], smoke["embedder"], "lessons learned cinematic", k=8)
    ids = [h.node_id for h in result.hits]
    assert len(ids) == len(set(ids)), f"duplicate node_ids in hits: {ids}"


def test_query_respects_token_budget(smoke: SmokeContext) -> None:
    result = retrieve.query(smoke["store"], smoke["embedder"], "deploy", budget_tokens=80, k=20)
    assert result.tokens_used <= 80


def test_feedback_recall_intent_biases_to_feedback_nodes(
    smoke: SmokeContext,
) -> None:
    """A 'feedback-recall'-shaped prompt should mostly return feedback nodes."""
    result = retrieve.query(
        smoke["store"],
        smoke["embedder"],
        "what does the user always prefer for commits and deploys",
        k=5,
    )
    assert "feedback-recall" in result.intent_tags
    if result.hits:
        feedback_count = sum(1 for h in result.hits if h.type == "memory_feedback")
        assert feedback_count >= 1, (
            f"expected >=1 feedback hit, got types: {[h.type for h in result.hits]}"
        )


# --- Layer 4: audit + co-occurrence learning -----------------------------


def test_queries_grow_audit_log(smoke: SmokeContext) -> None:
    store = smoke["store"]
    before = len(store.recent_queries(limit=200))
    retrieve.query(store, smoke["embedder"], "audit smoke A", k=3)
    retrieve.query(store, smoke["embedder"], "audit smoke B", k=3)
    after = len(store.recent_queries(limit=200))
    assert after >= before + 2


def test_co_occurrence_edges_emerge(smoke: SmokeContext) -> None:
    store = smoke["store"]
    retrieve.query(store, smoke["embedder"], "deploy and config", k=5)
    retrieve.query(store, smoke["embedder"], "test setup", k=5)
    found = False
    for n in store.list_nodes(limit=500):
        if store.get_edges(src_id=n.id, relation="co_occurs_with"):
            found = True
            break
    assert found, "no co_occurs_with edges after retrieval; learning loop is broken"


# --- Layer 5: HTTP API ---------------------------------------------------


def test_http_endpoints_all_200(smoke: SmokeContext) -> None:
    app = create_app(store=smoke["store"], embedder=smoke["embedder"])
    with TestClient(app) as client:
        for path in [
            "/health",
            "/sources",
            "/audit",
            "/",
            "/graph",
            "/sources-page",
            "/audit-page",
            "/settings",
            "/ui/graph-data",
        ]:
            r = client.get(path)
            assert r.status_code == 200, f"{path} -> {r.status_code}: {r.text[:200]}"


def test_http_health_reports_real_counts(smoke: SmokeContext) -> None:
    app = create_app(store=smoke["store"], embedder=smoke["embedder"])
    with TestClient(app) as client:
        data = client.get("/health").json()
        assert data["ok"] is True
        assert data["node_count"] > 0
        assert data["source_count"] > 0
        assert sum(data["counts_by_type"].values()) == data["node_count"]


def test_http_search_fragment_returns_html_with_citation(
    smoke: SmokeContext,
) -> None:
    app = create_app(store=smoke["store"], embedder=smoke["embedder"])
    with TestClient(app) as client:
        r = client.get("/ui/search", params={"q": "commit"})
        assert r.status_code == 200
        assert "[mnemo:" in r.text  # citations rendered into the fragment


def test_http_query_endpoint_round_trip(smoke: SmokeContext) -> None:
    app = create_app(store=smoke["store"], embedder=smoke["embedder"])
    with TestClient(app) as client:
        r = client.post("/query", json={"prompt": "deploy process", "k": 3, "budget_tokens": 200})
        assert r.status_code == 200
        body = r.json()
        assert "hits" in body
        assert "intent_tags" in body
        assert body["tokens_used"] <= 200


# --- Optional: report something useful on success -----------------------


def test_dump_smoke_summary(smoke: SmokeContext, capsys: pytest.CaptureFixture) -> None:
    """Final test that prints a concise summary so a successful run is informative."""
    store = smoke["store"]
    counts = store.count_nodes()
    n_total = sum(counts.values())
    n_sources = len(store.list_sources())
    n_queries = len(store.recent_queries(limit=200))

    co_occur = 0
    for n in store.list_nodes(limit=10_000):
        co_occur += len(store.get_edges(src_id=n.id, relation="co_occurs_with"))

    with capsys.disabled():
        print("\n--- mnemo smoke summary ---")
        print(f"  sources:                    {n_sources}")
        print(f"  nodes (total):              {n_total}")
        for t, c in sorted(counts.items()):
            print(f"    {t:20s}{c:6d}")
        print(f"  queries logged:             {n_queries}")
        print(f"  co_occurs_with edges:       {co_occur}")

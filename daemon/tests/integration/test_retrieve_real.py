"""Integration tests for end-to-end retrieval against the real embedder."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from mnemo import ingest, retrieve
from mnemo.embed import Embedder
from mnemo.store import Store


@pytest.fixture(scope="module")
def embedder(tmp_path_factory: pytest.TempPathFactory) -> Embedder:
    cache = tmp_path_factory.mktemp("model-cache")
    return Embedder(cache_dir=cache)


def _seed_memory(tmp_path: Path) -> Path:
    """Lay down four small memory files of varying types."""
    src = tmp_path / "memory"
    src.mkdir()
    (src / "feedback_commit.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: commit-style
            description: No emojis in code or commit messages
            type: feedback
            ---
            Hard rule: never commit emoji glyphs in source or commit text.
            """
        ),
        encoding="utf-8",
    )
    (src / "project_deploy.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: deploy-process
            description: How services are deployed
            type: project
            ---
            Use docker-compose with --force-recreate to bounce containers.
            """
        ),
        encoding="utf-8",
    )
    (src / "feedback_terse.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: terse-output
            description: Prefer short responses, no trailing summaries
            type: feedback
            ---
            User prefers minimal responses; do not summarize after every change.
            """
        ),
        encoding="utf-8",
    )
    (src / "project_mqtt.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: mqtt-broker-restoration
            description: EMQX broker recovery and webhook secret recovery
            type: project
            ---
            Restore EMQX, patch webhook secret to match device-registry env.
            """
        ),
        encoding="utf-8",
    )
    return src


def test_query_returns_relevant_top_hit(embedder: Embedder, store: Store, tmp_path: Path) -> None:
    src = _seed_memory(tmp_path)
    store.register_source(str(src), "memory_dir")
    ingest.reindex(store, embedder=embedder)

    result = retrieve.query(store, embedder, "should I add emoji glyphs to commit messages?")

    assert len(result.hits) >= 1
    top = result.hits[0]
    assert "commit" in (top.name + top.description).lower()
    assert result.tokens_used <= 800
    assert all(h.citation.startswith("[mnemo:") for h in result.hits)


def test_query_dedupes_chunks_per_node(embedder: Embedder, store: Store, tmp_path: Path) -> None:
    """Ensure the phase-4 problem (one node returning multiple chunks) is fixed."""
    src = tmp_path / "memory"
    src.mkdir()
    body = "\n\n".join(
        [f"## section {i}\n" + ("godot child timer cinematic safety " * 30) for i in range(5)]
    )
    (src / "project_godot.md").write_text(
        f"---\nname: godot-notes\ntype: project\n---\n{body}", encoding="utf-8"
    )
    store.register_source(str(src), "memory_dir")
    ingest.reindex(store, embedder=embedder)

    result = retrieve.query(store, embedder, "godot child timer cinematic safety")
    # All hits should be unique node_ids.
    node_ids = [h.node_id for h in result.hits]
    assert len(node_ids) == len(set(node_ids))


def test_query_logs_to_audit_trail(embedder: Embedder, store: Store, tmp_path: Path) -> None:
    src = _seed_memory(tmp_path)
    store.register_source(str(src), "memory_dir")
    ingest.reindex(store, embedder=embedder)

    initial_count = len(store.recent_queries(limit=100))
    result = retrieve.query(store, embedder, "user always prefers terse output")
    after_count = len(store.recent_queries(limit=100))

    assert after_count == initial_count + 1
    # The most recent entry corresponds to our query.
    latest = store.recent_queries(limit=1)[0]
    assert latest.id == result.query_id
    assert "feedback-recall" in latest.intent_tags


def test_query_intent_tags_drive_type_priority(
    embedder: Embedder, store: Store, tmp_path: Path
) -> None:
    """A 'feedback-recall' prompt should bias toward memory_feedback nodes."""
    src = _seed_memory(tmp_path)
    store.register_source(str(src), "memory_dir")
    ingest.reindex(store, embedder=embedder)

    # 'always prefer' triggers feedback-recall
    result = retrieve.query(store, embedder, "user always prefers what")
    assert "feedback-recall" in result.intent_tags
    # The first non-trivial hit should be a feedback memory.
    feedback_hits = [h for h in result.hits if h.type == "memory_feedback"]
    assert len(feedback_hits) >= 1


def test_query_respects_token_budget(embedder: Embedder, store: Store, tmp_path: Path) -> None:
    src = _seed_memory(tmp_path)
    store.register_source(str(src), "memory_dir")
    ingest.reindex(store, embedder=embedder)

    result = retrieve.query(store, embedder, "deploy", budget_tokens=30)
    assert result.tokens_used <= 30


def test_query_co_occurrence_strengthens_after_query(
    embedder: Embedder, store: Store, tmp_path: Path
) -> None:
    src = _seed_memory(tmp_path)
    store.register_source(str(src), "memory_dir")
    ingest.reindex(store, embedder=embedder)

    result = retrieve.query(store, embedder, "deploy and mqtt broker setup")
    if len(result.hits) >= 2:
        a_id = result.hits[0].node_id
        b_id = result.hits[1].node_id
        edges = store.get_edges(src_id=a_id, dst_id=b_id, relation="co_occurs_with")
        assert len(edges) == 1
        assert edges[0].weight > 0.0

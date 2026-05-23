"""v5.12.0 -- knowledge auditor: 3 deterministic detectors.

Phase 1 of mnemo's Understanding arc (see
``docs/plans/2026-05-22-mnemo-understanding-phase1-design.md`` +
``memory/project_mnemo_v6_vision_understanding``).

Three detectors, all deterministic + no LLM:

1. ``stale`` -- node body / description contains the literal token
   ``SUPERSEDED`` (case-insensitive). Lexical, instant.
2. ``duplicates`` -- pairs of same-type nodes with cosine
   similarity >= 0.95. Embedder-driven; uses sqlite-vec's
   chunk-level NN search.
3. ``orphan_references`` -- node body contains
   ``[mnemo:<id>]`` where ``<id>`` is not in the graph.

Contract this test file locks:
- Each detector returns a list of finding dicts with the canonical
  shape ``{type, node_ids, description, severity}``.
- The orchestrator ``analyze(store)`` runs all three and aggregates,
  returning ``{ran_at, node_count_scanned, findings, summary}``.
- Optional ``types`` filter restricts which detectors run.
"""

from __future__ import annotations

import pytest

from mnemo.store import Node, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "mnemo.db")
    yield s
    s.close()


@pytest.fixture
def fake_embedder():
    """A toy embedder that returns deterministic 384-dim vectors keyed
    by body content. Two nodes with the same body get identical vectors
    (cosine distance 0.0). Lets us simulate near-duplicates without a
    real sentence-transformer load."""

    class _FakeEmbedder:
        dim = 384

        def embed_text(self, text):
            sig = (text or "")[:32].lower()
            base = [0.0] * 384
            for i, ch in enumerate(sig):
                base[i % 384] += ord(ch) / 1000.0
            norm = sum(x * x for x in base) ** 0.5 or 1.0
            return [x / norm for x in base]

        def embed_batch(self, texts):
            return [self.embed_text(t) for t in texts]

    return _FakeEmbedder()


def _mknode(
    *,
    id: str,
    type: str = "memory_feedback",
    description: str = "",
    body: str = "",
    project_key: str | None = None,
) -> Node:
    """Build a Node with the minimum fields the Store needs."""
    import time

    now = int(time.time())
    return Node(
        id=id,
        type=type,
        name=id.split("/", 1)[-1],
        description=description,
        body=body,
        source_path=f"/tmp/{id}.md",
        source_kind="memory",
        project_key=project_key,
        frontmatter_json=None,
        hash="",
        created_at=now,
        updated_at=now,
    )


def _write_with_embedding(store: Store, node: Node, embedder) -> None:
    """Upsert a node + its single-chunk embedding."""
    store.upsert_node(node)
    vec = embedder.embed_text(node.body or node.description or node.name)
    store.upsert_chunks(node.id, [(0, vec, node.body or "")])


# --- 1. stale detector --------------------------------------------------


def test_stale_detector_finds_superseded_marker_in_body(store) -> None:
    from mnemo.analyzer import detect_stale

    store.upsert_node(
        _mknode(
            id="memory_session/old-handover",
            type="memory_session",
            description="SUPERSEDED by v5.11.0.",
            body="The body explains the old state.",
        )
    )
    store.upsert_node(
        _mknode(
            id="memory_session/current-handover",
            type="memory_session",
            description="CANONICAL ENTRY-POINT.",
            body="The current shipping state.",
        )
    )

    findings = detect_stale(store)
    flagged = {f["node_ids"][0] for f in findings}
    assert "memory_session/old-handover" in flagged
    assert "memory_session/current-handover" not in flagged


def test_stale_detector_case_insensitive(store) -> None:
    """superseded / SUPERSEDED / Superseded should all match."""
    from mnemo.analyzer import detect_stale

    for variant, suffix in [
        ("superseded", "lower"),
        ("SUPERSEDED", "upper"),
        ("Superseded", "title"),
    ]:
        store.upsert_node(
            _mknode(
                id=f"memory_feedback/old-{suffix}",
                description=f"This is {variant} by something newer.",
                body="...",
            )
        )

    findings = detect_stale(store)
    assert len(findings) == 3, f"expected 3 stale findings (one per variant); got {findings}"


def test_stale_detector_severity_is_low(store) -> None:
    """The user explicitly marked these; informational only."""
    from mnemo.analyzer import detect_stale

    store.upsert_node(
        _mknode(id="memory_feedback/x", description="SUPERSEDED somehow.", body="...")
    )
    findings = detect_stale(store)
    assert findings
    assert findings[0]["severity"] == "low"
    assert findings[0]["type"] == "stale"


# --- 2. duplicates detector --------------------------------------------


def test_duplicates_detector_finds_near_identical_pairs(store, fake_embedder) -> None:
    """Two memory_feedback nodes with identical bodies must be flagged
    as duplicates. The fake embedder maps identical prefixes to identical
    vectors so cosine distance is 0.0 (well below the 0.95 threshold)."""
    from mnemo.analyzer import detect_duplicates

    body = "Same advice in two memory entries about MQTT auth."
    _write_with_embedding(store, _mknode(id="memory_feedback/copy-1", body=body), fake_embedder)
    _write_with_embedding(store, _mknode(id="memory_feedback/copy-2", body=body), fake_embedder)

    findings = detect_duplicates(store, embedder=fake_embedder)
    pairs = {tuple(sorted(f["node_ids"])) for f in findings}
    assert ("memory_feedback/copy-1", "memory_feedback/copy-2") in pairs, (
        f"expected the copy-1/copy-2 pair to be flagged; got pairs {pairs}"
    )


def test_duplicates_detector_skips_cross_type_pairs(store, fake_embedder) -> None:
    """A memory_feedback and a memory_project with similar bodies must
    NOT be flagged. Phase 1 only flags within-type duplicates."""
    from mnemo.analyzer import detect_duplicates

    body = "Same content but different node types is intentional."
    _write_with_embedding(
        store, _mknode(id="memory_feedback/x", type="memory_feedback", body=body), fake_embedder
    )
    _write_with_embedding(
        store, _mknode(id="memory_project/y", type="memory_project", body=body), fake_embedder
    )
    findings = detect_duplicates(store, embedder=fake_embedder)
    assert findings == [], f"cross-type pairs must not be flagged in Phase 1; got {findings}"


def test_duplicates_detector_severity_is_medium(store, fake_embedder) -> None:
    from mnemo.analyzer import detect_duplicates

    body = "Some near-duplicate content here."
    _write_with_embedding(store, _mknode(id="memory_feedback/a", body=body), fake_embedder)
    _write_with_embedding(store, _mknode(id="memory_feedback/b", body=body), fake_embedder)
    findings = detect_duplicates(store, embedder=fake_embedder)
    if findings:
        assert findings[0]["severity"] == "medium"
        assert findings[0]["type"] == "duplicates"


# --- 3. orphan_references detector --------------------------------------


def test_orphan_references_detector_finds_dangling_citations(store) -> None:
    """A node whose body references [mnemo:does-not-exist] is flagged."""
    from mnemo.analyzer import detect_orphan_references

    store.upsert_node(
        _mknode(
            id="memory_feedback/cites-missing",
            body="See [mnemo:does-not-exist] for the rationale.",
        )
    )
    store.upsert_node(
        _mknode(
            id="memory_feedback/has-no-cites",
            body="This body has no mnemo citations at all.",
        )
    )

    findings = detect_orphan_references(store)
    flagged = {f["node_ids"][0] for f in findings}
    assert "memory_feedback/cites-missing" in flagged
    assert "memory_feedback/has-no-cites" not in flagged


def test_orphan_references_detector_ignores_resolvable_citations(store) -> None:
    """A node that references an EXISTING node id must NOT be flagged."""
    from mnemo.analyzer import detect_orphan_references

    store.upsert_node(_mknode(id="memory_feedback/target", body="I am the target."))
    store.upsert_node(
        _mknode(
            id="memory_feedback/source",
            body="See [mnemo:memory_feedback/target] for details.",
        )
    )

    findings = detect_orphan_references(store)
    assert findings == [], f"resolvable citations must not be flagged; got {findings}"


def test_orphan_references_detector_severity_is_high(store) -> None:
    """Broken links are HIGH severity -- the user is relying on a
    citation that points nowhere."""
    from mnemo.analyzer import detect_orphan_references

    store.upsert_node(_mknode(id="memory_feedback/x", body="[mnemo:gone-forever]"))
    findings = detect_orphan_references(store)
    assert findings
    assert findings[0]["severity"] == "high"
    assert findings[0]["type"] == "orphan_reference"


# --- 4. analyze() orchestrator ------------------------------------------


def test_analyze_orchestrator_returns_canonical_envelope(store, fake_embedder) -> None:
    from mnemo.analyzer import analyze

    result = analyze(store, embedder=fake_embedder)
    assert "ran_at" in result
    assert "node_count_scanned" in result
    assert "findings" in result
    assert "summary" in result
    assert isinstance(result["findings"], list)
    assert isinstance(result["summary"], dict)


def test_analyze_orchestrator_filters_by_types(store, fake_embedder) -> None:
    """``types=['stale']`` should only run the stale detector."""
    from mnemo.analyzer import analyze

    store.upsert_node(
        _mknode(
            id="memory_feedback/x",
            description="SUPERSEDED",
            body="cites [mnemo:gone-forever]",
        )
    )
    result = analyze(store, embedder=fake_embedder, types=["stale"])
    types_seen = {f["type"] for f in result["findings"]}
    assert types_seen == {"stale"}, f"types filter didn't apply; saw {types_seen}"


def test_analyze_summary_counts_by_type(store, fake_embedder) -> None:
    """The summary dict gives a count per finding type."""
    from mnemo.analyzer import analyze

    store.upsert_node(
        _mknode(
            id="memory_feedback/a",
            description="SUPERSEDED",
            body="[mnemo:gone]",
        )
    )
    result = analyze(store, embedder=fake_embedder)
    assert result["summary"].get("stale", 0) >= 1
    assert result["summary"].get("orphan_references", 0) >= 1

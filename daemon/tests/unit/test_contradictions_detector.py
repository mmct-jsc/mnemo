"""v5.13.0 -- contradictions detector (Phase 2a, deterministic path).

Phase 2 of mnemo's Understanding arc (see
``docs/plans/2026-05-23-mnemo-understanding-phase2a-design.md`` +
``memory/project_mnemo_v6_vision_understanding``).

The detector has two layers:

1. **Deterministic candidate selection** (this test file): for each
   pair (A, B) of same-type nodes, flag as a "candidate" if
   - cosine similarity is in [0.5, 0.85], AND
   - at least one of the bodies contains a negation pattern
     ('do not', 'never', 'no longer', 'deprecated', 'removed',
     'instead of', 'forbidden', 'disallowed', 'must not',
     'should not', 'avoid').

2. **Opt-in LLM judge** (separate test file
   ``test_contradictions_judge.py``): escalate each candidate to
   Claude for a binary contradiction-or-not decision.

Phase 2a ships ONLY the deterministic layer + a hook for the
LLM judge.
"""

from __future__ import annotations

import math

import pytest

from mnemo.store import Node, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "mnemo.db")
    yield s
    s.close()


@pytest.fixture
def banded_embedder():
    """Embedder that returns precisely-controlled vectors per text so
    we can put pairs into specific cosine bands.

    Use ``embedder.assign(text, vec)`` to pin a body's vector before
    inserting the corresponding node + chunk. Otherwise the embedder
    returns ``[1.0, 0.0, ..., 0.0]`` (matching the default V_A).

    The default-A vector is ``[1.0] + [0]*383``. Helper vectors:
        v_at_cosine(c) -- a unit vector with cos(angle to V_A) == c.
    """
    table: dict[str, list[float]] = {}

    def _v_default() -> list[float]:
        return [1.0] + [0.0] * 383

    class _E:
        dim = 384

        def embed_text(self, text):
            if text in table:
                return table[text]
            return _v_default()

        def embed_batch(self, texts):
            return [self.embed_text(t) for t in texts]

        def assign(self, text: str, vec: list[float]) -> None:
            table[text] = vec

    return _E()


def _v_a() -> list[float]:
    """Anchor vector A: [1, 0, 0, ..., 0]."""
    return [1.0] + [0.0] * 383


def _v_at_cosine(c: float) -> list[float]:
    """Unit vector with cos(angle to V_A) == c. Lives in the
    first two dimensions: [c, sqrt(1-c**2), 0, ..., 0]."""
    s = math.sqrt(max(0.0, 1.0 - c * c))
    return [c, s] + [0.0] * 382


def _mknode(
    *,
    id: str,
    type: str = "memory_feedback",
    description: str = "",
    body: str = "",
) -> Node:
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
        project_key=None,
        frontmatter_json=None,
        hash="",
        created_at=now,
        updated_at=now,
    )


def _wire_pair(
    store: Store,
    embedder,
    a_id: str,
    a_body: str,
    a_vec: list[float],
    b_id: str,
    b_body: str,
    b_vec: list[float],
    *,
    type: str = "memory_feedback",
) -> None:
    """Insert two nodes + their explicitly-assigned vectors into the
    embedder + store."""
    embedder.assign(a_body, a_vec)
    embedder.assign(b_body, b_vec)
    a = _mknode(id=a_id, type=type, body=a_body)
    b = _mknode(id=b_id, type=type, body=b_body)
    store.upsert_node(a)
    store.upsert_node(b)
    store.upsert_chunks(a_id, [(0, a_vec, a_body)])
    store.upsert_chunks(b_id, [(0, b_vec, b_body)])


# --- Deterministic candidate selection ---------------------------------


def test_contradictions_detector_finds_negation_differential(store, banded_embedder) -> None:
    """One node prescribes, the other forbids -- same topic. The
    detector emits a candidate finding. Cosine 0.7 puts them squarely
    in the [0.5, 0.85] band."""
    from mnemo.analyzer import detect_contradictions

    _wire_pair(
        store,
        banded_embedder,
        a_id="memory_feedback/redis-pro",
        a_body="Caching guidance. Use Redis for hot-path session storage.",
        a_vec=_v_a(),
        b_id="memory_feedback/redis-con",
        b_body="Caching guidance. Do not add Redis; use in-process caching.",
        b_vec=_v_at_cosine(0.7),
    )

    findings = detect_contradictions(store, embedder=banded_embedder)
    pairs = {tuple(sorted(f["node_ids"])) for f in findings}
    assert ("memory_feedback/redis-con", "memory_feedback/redis-pro") in pairs, (
        f"expected the Redis pro/con pair to be flagged as a candidate; got {pairs}"
    )


def test_contradictions_detector_default_severity_is_candidate(store, banded_embedder) -> None:
    """Without an LLM judge, candidates are severity=candidate."""
    from mnemo.analyzer import detect_contradictions

    _wire_pair(
        store,
        banded_embedder,
        a_id="memory_feedback/a",
        a_body="Topic. Use the X approach for performance.",
        a_vec=_v_a(),
        b_id="memory_feedback/b",
        b_body="Topic. Do not use X; it was deprecated.",
        b_vec=_v_at_cosine(0.7),
    )
    findings = detect_contradictions(store, embedder=banded_embedder)
    assert findings, "expected at least one candidate finding"
    assert findings[0]["severity"] == "candidate", (
        f"default (no LLM judge) severity should be 'candidate'; got {findings[0]['severity']}"
    )
    assert findings[0]["type"] == "contradictions"


def test_contradictions_detector_skips_pairs_with_no_negation(store, banded_embedder) -> None:
    """Two nodes about the same topic but neither contains a negation
    pattern -- not a candidate."""
    from mnemo.analyzer import detect_contradictions

    _wire_pair(
        store,
        banded_embedder,
        a_id="memory_feedback/x",
        a_body="MQTT auth: probe with paho-mqtt; check CONNACK rc=0.",
        a_vec=_v_a(),
        b_id="memory_feedback/y",
        b_body="MQTT auth: verify the broker store after reprovision.",
        b_vec=_v_at_cosine(0.7),
    )
    findings = detect_contradictions(store, embedder=banded_embedder)
    assert findings == [], f"pairs with no negation pattern shouldn't be candidates; got {findings}"


def test_contradictions_detector_skips_high_cosine_pairs(store, banded_embedder) -> None:
    """Cosine above 0.85 is duplicate territory (the duplicates detector
    owns that). The contradictions detector must skip."""
    from mnemo.analyzer import detect_contradictions

    _wire_pair(
        store,
        banded_embedder,
        a_id="memory_feedback/a",
        a_body="Topic. Use approach X.",
        a_vec=_v_a(),
        b_id="memory_feedback/b",
        b_body="Topic. Do not use X.",
        b_vec=_v_at_cosine(0.95),  # too similar
    )
    findings = detect_contradictions(store, embedder=banded_embedder)
    assert findings == [], (
        f"high-cosine pairs are duplicates territory, not contradictions; got {findings}"
    )


def test_contradictions_detector_skips_low_cosine_pairs(store, banded_embedder) -> None:
    """Cosine below 0.5 means different topics -- no contradiction
    possible. The detector must skip."""
    from mnemo.analyzer import detect_contradictions

    _wire_pair(
        store,
        banded_embedder,
        a_id="memory_feedback/a",
        a_body="Topic. Use approach X.",
        a_vec=_v_a(),
        b_id="memory_feedback/b",
        b_body="Different topic entirely. Do not use Y.",
        b_vec=_v_at_cosine(0.3),  # too different
    )
    findings = detect_contradictions(store, embedder=banded_embedder)
    assert findings == [], (
        f"low-cosine pairs are different topics, not contradictions; got {findings}"
    )


def test_contradictions_detector_skips_cross_type_pairs(store, banded_embedder) -> None:
    """A memory_feedback and a memory_project with similar content
    aren't paired -- contradiction detection is within-type only.

    Manual wire (not using ``_wire_pair`` because the helper assigns
    both nodes the same type)."""
    from mnemo.analyzer import detect_contradictions

    a_body = "Topic: use the new approach."
    b_body = "Topic: do not use the new approach; deprecated."
    a_vec = _v_a()
    b_vec = _v_at_cosine(0.7)
    banded_embedder.assign(a_body, a_vec)
    banded_embedder.assign(b_body, b_vec)
    a = _mknode(id="memory_feedback/x", type="memory_feedback", body=a_body)
    b = _mknode(id="memory_project/y", type="memory_project", body=b_body)
    store.upsert_node(a)
    store.upsert_node(b)
    store.upsert_chunks(a.id, [(0, a_vec, a_body)])
    store.upsert_chunks(b.id, [(0, b_vec, b_body)])

    findings = detect_contradictions(store, embedder=banded_embedder)
    assert findings == [], f"cross-type pairs shouldn't be candidates in Phase 2a; got {findings}"


def test_contradictions_detector_skips_self_pairs(store, banded_embedder) -> None:
    """A single node never pairs with itself."""
    from mnemo.analyzer import detect_contradictions

    body = "Lonely advice: do not use foo."
    banded_embedder.assign(body, _v_a())
    n = _mknode(id="memory_feedback/solo", body=body)
    store.upsert_node(n)
    store.upsert_chunks(n.id, [(0, _v_a(), body)])

    findings = detect_contradictions(store, embedder=banded_embedder)
    assert findings == [], f"single-node corpus has no pairs; got {findings}"


# --- Orchestrator integration ------------------------------------------


def test_analyze_orchestrator_recognizes_contradictions_type(store, banded_embedder) -> None:
    """``types=['contradictions']`` should ONLY run the contradictions
    detector -- skipping stale, duplicates, and orphan_references."""
    from mnemo.analyzer import analyze

    _wire_pair(
        store,
        banded_embedder,
        a_id="memory_feedback/p",
        a_body="Topic guidance. Use approach X.",
        a_vec=_v_a(),
        b_id="memory_feedback/q",
        b_body="Topic guidance. Do not use X; deprecated.",
        b_vec=_v_at_cosine(0.7),
    )
    # Add description=SUPERSEDED on one to verify the stale detector
    # would normally fire (but won't because of the types filter).
    n_q = store.get_node("memory_feedback/q")
    n_q.description = "SUPERSEDED"
    store.upsert_node(n_q)

    result = analyze(store, embedder=banded_embedder, types=["contradictions"])
    types_seen = {f["type"] for f in result["findings"]}
    assert types_seen == {"contradictions"}, f"types filter didn't apply; saw {types_seen}"


def test_analyze_summary_includes_contradictions_count(store, banded_embedder) -> None:
    """The aggregate summary dict has a key for contradictions when
    any are found."""
    from mnemo.analyzer import analyze

    _wire_pair(
        store,
        banded_embedder,
        a_id="memory_feedback/a",
        a_body="Topic. Use X.",
        a_vec=_v_a(),
        b_id="memory_feedback/b",
        b_body="Topic. Do not use X.",
        b_vec=_v_at_cosine(0.7),
    )
    result = analyze(store, embedder=banded_embedder, types=["contradictions"])
    assert "contradictions" in result["summary"], (
        f"summary missing contradictions key; got {result['summary']}"
    )
    assert result["summary"]["contradictions"] >= 1


def test_known_detector_types_includes_contradictions() -> None:
    """The detector is part of the public surface listed in
    KNOWN_DETECTOR_TYPES (drives the ``types=`` filter contract)."""
    from mnemo.analyzer import KNOWN_DETECTOR_TYPES

    assert "contradictions" in KNOWN_DETECTOR_TYPES, (
        f"KNOWN_DETECTOR_TYPES must list 'contradictions'; got {KNOWN_DETECTOR_TYPES}"
    )

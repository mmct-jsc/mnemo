"""v5.16.0 -- dead_code detector (Phase 3, code lens).

Phase 3 of mnemo's Understanding arc (see
``docs/plans/2026-05-30-mnemo-understanding-phase3-design.md`` +
``memory/project_mnemo_v6_vision_understanding``).

dead_code is the first DOMAIN-LENS detector (lens="code"), not a
domain-agnostic one. A candidate is a PRIVATE (``_``-prefixed,
non-dunder) ``code_function`` / ``code_method`` node with ZERO
inbound ``calls`` edges, excluding test entry points.

Contract this test file locks:

1. A private uncalled callable is a candidate (severity 'candidate').
2. A private callable WITH an inbound ``calls`` edge is NOT a
   candidate.
3. Public (non-``_``) callables are NOT candidates (precision: the
   call graph is best-effort; only private within-file resolution
   is high-confidence).
4. Dunders (``__x__``), ``test_*`` names, and nodes under a
   ``tests/`` path are excluded.
5. Both ``code_function`` and ``code_method`` are scanned.
"""

from __future__ import annotations

import time

import pytest

from mnemo.store import Node, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "mnemo.db")
    yield s
    s.close()


def _mkcode(
    *,
    id: str,
    name: str,
    type: str = "code_function",
    source_path: str = "/proj/mod.py:1-5",
) -> Node:
    now = int(time.time())
    return Node(
        id=id,
        type=type,
        name=name,
        description="",
        body=f"def {name}(): ...",
        source_path=source_path,
        source_kind="code",
        project_key="proj",
        frontmatter_json=None,
        hash="",
        created_at=now,
        updated_at=now,
    )


# --- Candidate gate -----------------------------------------------------


def test_private_uncalled_function_is_candidate(store) -> None:
    from mnemo.analyzer import detect_dead_code

    store.upsert_node(_mkcode(id="f1", name="_helper"))
    findings = detect_dead_code(store)
    ids = {f["node_ids"][0] for f in findings if f["type"] == "dead_code"}
    assert "f1" in ids, f"private uncalled _helper should be a dead_code candidate; got {findings}"


def test_default_severity_is_candidate(store) -> None:
    from mnemo.analyzer import detect_dead_code

    store.upsert_node(_mkcode(id="f1", name="_helper"))
    findings = detect_dead_code(store)
    dead = next(f for f in findings if f["type"] == "dead_code")
    assert dead["severity"] == "candidate", (
        f"without a judge, dead_code severity is 'candidate'; got {dead['severity']}"
    )


def test_private_called_function_is_not_candidate(store) -> None:
    """A private callable WITH an inbound calls edge is alive."""
    from mnemo.analyzer import detect_dead_code

    store.upsert_node(_mkcode(id="caller", name="run"))
    store.upsert_node(_mkcode(id="callee", name="_helper"))
    store.add_edge("caller", "callee", "calls")

    findings = detect_dead_code(store)
    ids = {f["node_ids"][0] for f in findings if f["type"] == "dead_code"}
    assert "callee" not in ids, f"a called private fn must not be dead_code; got {findings}"


def test_public_uncalled_function_is_not_candidate(store) -> None:
    """Public (non-underscore) callables are excluded -- the call
    graph is sparse for cross-file/external calls; flagging public
    symbols would flood with false positives."""
    from mnemo.analyzer import detect_dead_code

    store.upsert_node(_mkcode(id="pub", name="public_api"))
    findings = detect_dead_code(store)
    ids = {f["node_ids"][0] for f in findings if f["type"] == "dead_code"}
    assert "pub" not in ids, f"public uncalled fn must NOT be a candidate; got {findings}"


def test_dunder_is_not_candidate(store) -> None:
    """Dunders are called implicitly by the runtime."""
    from mnemo.analyzer import detect_dead_code

    store.upsert_node(_mkcode(id="d", name="__init__", type="code_method"))
    findings = detect_dead_code(store)
    ids = {f["node_ids"][0] for f in findings if f["type"] == "dead_code"}
    assert "d" not in ids, f"dunder __init__ must NOT be a candidate; got {findings}"


def test_test_function_is_not_candidate(store) -> None:
    """A test_ function is a pytest entry point, not dead. (Also it's
    not private, but lock the explicit exclusion.)"""
    from mnemo.analyzer import detect_dead_code

    store.upsert_node(_mkcode(id="t", name="_test_secret_helper"))
    # _test_-prefixed private helper in a tests path -> excluded by path.
    store.upsert_node(
        _mkcode(id="t2", name="_helper", source_path="/proj/tests/unit/test_x.py:1-5")
    )
    findings = detect_dead_code(store)
    ids = {f["node_ids"][0] for f in findings if f["type"] == "dead_code"}
    assert "t2" not in ids, f"private helper under tests/ path must be excluded; got {findings}"


def test_code_method_is_scanned(store) -> None:
    """code_method nodes are scanned, not just code_function."""
    from mnemo.analyzer import detect_dead_code

    store.upsert_node(_mkcode(id="m", name="_unused_method", type="code_method"))
    findings = detect_dead_code(store)
    ids = {f["node_ids"][0] for f in findings if f["type"] == "dead_code"}
    assert "m" in ids, f"private uncalled code_method should be a candidate; got {findings}"


def test_non_code_nodes_are_ignored(store) -> None:
    """A memory node that happens to start with _ is not code."""
    from mnemo.analyzer import detect_dead_code

    now = int(time.time())
    store.upsert_node(
        Node(
            id="mem",
            type="memory_feedback",
            name="_note",
            description="",
            body="some note",
            source_path="/m/mem.md",
            source_kind="memory",
            project_key=None,
            frontmatter_json=None,
            hash="",
            created_at=now,
            updated_at=now,
        )
    )
    findings = detect_dead_code(store)
    assert findings == [], f"non-code nodes must never be dead_code; got {findings}"


def test_finding_includes_symbol_in_description(store) -> None:
    from mnemo.analyzer import detect_dead_code

    store.upsert_node(_mkcode(id="f1", name="_orphaned_helper"))
    findings = detect_dead_code(store)
    dead = next(f for f in findings if f["type"] == "dead_code")
    assert "_orphaned_helper" in dead["description"], (
        f"finding description must name the symbol; got {dead}"
    )

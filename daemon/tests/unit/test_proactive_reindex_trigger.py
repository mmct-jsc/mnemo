"""v5.22.0 Phase 4a -- post-reindex proactive audit trigger.

After a reindex completes, the daemon runs a SCOPED deterministic audit
(``stale`` + ``orphan_references`` only -- no embedder, no LLM, none of the
semantic_orphans / contradictions floods) and reconciles the findings into
the read-only ``audit_queue``. This tests the hooked function directly;
the route just spawns it on a background thread.
"""

from __future__ import annotations

import time

import pytest

from mnemo.server import run_proactive_audit
from mnemo.store import Node, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "mnemo.db")
    yield s
    s.close()


def _mknode(store: Store, *, id: str, body: str, name: str = "n") -> None:
    now = int(time.time())
    store.upsert_node(
        Node(
            id=id,
            type="memory_feedback",
            name=name,
            description="",
            body=body,
            source_path="",
            source_kind="memory",
            project_key=None,
            frontmatter_json=None,
            hash="",
            created_at=now,
            updated_at=now,
        )
    )


def test_run_proactive_audit_populates_queue(store) -> None:
    _mknode(store, id="superseded1", body="This entry is SUPERSEDED; archive it.")
    _mknode(store, id="citer1", body="See [mnemo:ghost-node] for details.")
    counts = run_proactive_audit(store)
    assert counts["new"] == 2
    rows = store.list_audit_queue(status="open", limit=25, offset=0)
    assert {r.type for r in rows} == {"stale", "orphan_reference"}


def test_proactive_audit_is_scoped(store) -> None:
    # Only the cheap deterministic detectors run -- never the embedding
    # floods (semantic_orphans / contradictions / duplicates).
    _mknode(store, id="superseded1", body="SUPERSEDED")
    run_proactive_audit(store)
    types = {r.type for r in store.list_audit_queue(status=None, limit=100, offset=0)}
    assert "semantic_orphan" not in types
    assert "contradiction" not in types
    assert "duplicates" not in types


def test_proactive_audit_idempotent_across_reindexes(store) -> None:
    _mknode(store, id="superseded1", body="SUPERSEDED")
    run_proactive_audit(store)
    second = run_proactive_audit(store)
    assert second["new"] == 0
    assert second["unchanged"] == 1
    assert store.count_audit_queue("open") == 1, "re-running must not duplicate the row"


def test_proactive_audit_auto_resolves_fixed_finding(store) -> None:
    # This also locks the detector-type vocabulary: if reconcile were
    # called with the wrong (plural) type set, the stale row would never
    # auto-resolve and this test would fail.
    _mknode(store, id="superseded1", body="SUPERSEDED")
    run_proactive_audit(store)
    assert store.count_audit_queue("open") == 1
    _mknode(store, id="superseded1", body="all good now")  # marker removed
    counts = run_proactive_audit(store)
    assert counts["resolved"] == 1
    assert store.count_audit_queue("open") == 0
    assert store.count_audit_queue("resolved") == 1

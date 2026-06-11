"""v5.28.0 step 7: pinned corpus snapshot + expanded eval set.

The n=14 SELF set was too noisy to tune against (v5.27.0 finding: the
live corpus drifts under measurement). v5.28.0 expands it to >=40 and
pins a corpus snapshot (node count + a fingerprint) in the report header
so two runs are apples-to-apples.
"""

from __future__ import annotations

from pathlib import Path

from mnemo import eval_retrieval as ev
from mnemo.store import Node, Store


def _seed(store: Store, node_id: str, hash_: str) -> None:
    store.upsert_node(
        Node(
            id=node_id,
            type="memory_project",
            name=node_id,
            description="",
            body="b",
            source_path=f"/m/{node_id}.md",
            source_kind="memory_dir",
            project_key=None,
            frontmatter_json=None,
            hash=hash_,
            created_at=1,
            updated_at=1,
        )
    )


def test_corpus_snapshot_counts_and_fingerprints(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    try:
        _seed(store, "a", "h1")
        _seed(store, "b", "h2")
        snap = ev.corpus_snapshot(store)
        assert snap["node_count"] == 2
        fp1 = snap["fingerprint"]
        assert isinstance(fp1, str)
        assert fp1

        # Editing any node's content (hash) changes the fingerprint.
        store.conn.execute("UPDATE nodes SET hash = ? WHERE id = ?", ("h2-changed", "b"))
        store.conn.commit()
        assert ev.corpus_snapshot(store)["fingerprint"] != fp1

        # Same state -> same fingerprint (deterministic, order-independent).
        assert ev.corpus_snapshot(store)["fingerprint"] == ev.corpus_snapshot(store)["fingerprint"]
    finally:
        store.close()


def test_format_report_includes_corpus_header() -> None:
    rows = ev.run_entries(
        [ev.EvalEntry(prompt="p", expect_source_contains=["x.py"])],
        query_fn=lambda e: ["/r/x.py::f"],
    )
    agg = ev.aggregate(rows)
    out = ev.format_report(rows, agg, corpus={"node_count": 18073, "fingerprint": "abc123def456"})
    assert "18073" in out
    assert "abc123def456" in out


def test_format_report_corpus_is_optional() -> None:
    # Backward-compatible: no corpus arg still produces a report.
    out = ev.format_report([], ev.aggregate([]))
    assert "mnemo retrieval eval" in out


def test_self_eval_set_has_at_least_40_entries() -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "retrieval_eval.json"
    entries = ev.load_eval_set(fixture)
    assert len(entries) >= 40, f"expected an expanded SELF set (>=40), got {len(entries)}"
    # Every entry must carry at least one expectation.
    assert all(e.expect_source_contains for e in entries)

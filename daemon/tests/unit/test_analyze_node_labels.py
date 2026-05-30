"""v5.21.0 -- /analyze node_labels enrichment.

The findings table showed opaque node-id hashes; the user couldn't see
WHERE a problem was without clicking. The route now enriches the
response with a ``node_labels`` map (id -> {name, type, source_path})
so the UI renders the node name + file path inline.

Built in the route (HTTP-response convenience), NOT in analyzer.analyze
(kept pure) and NOT on the MCP tool path (raw dict). Resolution is
chunked (SQLite variable-limit safe) because a heavy opt-in run can
surface tens of thousands of cited ids.
"""

from __future__ import annotations

import time

import pytest

from mnemo.server import _node_labels_for_findings
from mnemo.store import Node, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "mnemo.db")
    yield s
    s.close()


def _mknode(
    store: Store, *, id: str, name: str, type: str = "memory_feedback", source_path=""
) -> None:
    now = int(time.time())
    store.upsert_node(
        Node(
            id=id,
            type=type,
            name=name,
            description="",
            body="",
            source_path=source_path,
            source_kind="memory",
            project_key=None,
            frontmatter_json=None,
            hash="",
            created_at=now,
            updated_at=now,
        )
    )


def test_empty_findings_gives_empty_map(store) -> None:
    assert _node_labels_for_findings(store, []) == {}


def test_resolves_name_type_path_for_cited_nodes(store) -> None:
    _mknode(store, id="n1", name="Alpha", type="code_method", source_path="/repo/a.py:1-9")
    _mknode(store, id="n2", name="Beta", type="memory_project", source_path="/m/b.md")
    findings = [
        {"type": "orphan_reference", "node_ids": ["n1"], "description": "x", "severity": "high"},
        {
            "type": "duplicate_code",
            "node_ids": ["n1", "n2"],
            "description": "y",
            "severity": "medium",
        },
    ]
    labels = _node_labels_for_findings(store, findings)
    assert labels["n1"] == {"name": "Alpha", "type": "code_method", "source_path": "/repo/a.py:1-9"}
    assert labels["n2"] == {"name": "Beta", "type": "memory_project", "source_path": "/m/b.md"}
    # de-duplicated: n1 cited twice -> one entry
    assert len(labels) == 2


def test_missing_id_is_omitted(store) -> None:
    _mknode(store, id="real", name="Real")
    findings = [
        {
            "type": "orphan_reference",
            "node_ids": ["real", "ghost"],
            "description": "",
            "severity": "high",
        },
    ]
    labels = _node_labels_for_findings(store, findings)
    assert "real" in labels
    assert "ghost" not in labels, "an unresolved id must be omitted (UI falls back to the raw id)"


def test_chunks_over_sqlite_variable_limit(store) -> None:
    """450 distinct cited ids (> the 400 batch) all resolve -- the
    chunked lookup must not hit SQLite's host-variable ceiling."""
    n = 450
    for i in range(n):
        _mknode(store, id=f"id{i}", name=f"node{i}")
    findings = [
        {"type": "stale", "node_ids": [f"id{i}"], "description": "", "severity": "low"}
        for i in range(n)
    ]
    labels = _node_labels_for_findings(store, findings)
    assert len(labels) == n
    assert labels["id0"]["name"] == "node0"
    assert labels[f"id{n - 1}"]["name"] == f"node{n - 1}"


def test_analyze_out_accepts_node_labels() -> None:
    from mnemo.api_schemas import AnalyzeOut

    out = AnalyzeOut(
        ran_at="2026-05-30T00:00:00Z",
        node_count_scanned=1,
        findings=[],
        summary={},
        node_labels={"n1": {"name": "A", "type": "commit", "source_path": None}},
    )
    assert out.node_labels["n1"].name == "A"
    assert out.node_labels["n1"].type == "commit"

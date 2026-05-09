"""Tests for the Pydantic schema layer."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mnemo.api_schemas import (
    NodeOut,
    NodeUpdateIn,
    QueryAuditOut,
    QueryIn,
    SourceIn,
    SourceOut,
)
from mnemo.store import Node, Query, Source


def test_node_out_from_node_roundtrip() -> None:
    n = Node.new(
        type="memory_feedback",
        name="x",
        body="hello",
        source_path="/p.md",
        source_kind="memory_dir",
        description="d",
    )
    out = NodeOut.from_node(n)
    assert out.id == n.id
    assert out.type == "memory_feedback"
    assert out.body == "hello"
    assert out.description == "d"


def test_source_out_from_source() -> None:
    s = Source(path="/p", kind="memory_dir", project_key="P1", last_indexed_at=1234, enabled=True)
    out = SourceOut.from_source(s)
    assert out.path == "/p"
    assert out.last_indexed_at == 1234
    assert out.enabled is True


def test_source_in_defaults() -> None:
    body = SourceIn(path="/p", kind="memory_dir")
    assert body.project_key is None
    assert body.enabled is True


def test_query_in_validation_min_max() -> None:
    body = QueryIn(prompt="anything")
    assert body.budget_tokens == 800
    assert body.k == 20

    with pytest.raises(ValidationError):
        QueryIn(prompt="x", budget_tokens=0)
    with pytest.raises(ValidationError):
        QueryIn(prompt="x", k=0)
    with pytest.raises(ValidationError):
        QueryIn(prompt="x", k=10000)


def test_node_update_in_all_optional() -> None:
    body = NodeUpdateIn()
    assert body.body is None
    assert body.description is None


def test_query_audit_out_from_query() -> None:
    q = Query(
        id="qid",
        prompt="why?",
        intent_tags=["debug"],
        retrieved_ids=["a", "b"],
        scores={"a": 0.9, "b": 0.7},
        ts=1234,
    )
    out = QueryAuditOut.from_query(q)
    assert out.id == "qid"
    assert out.scores == {"a": 0.9, "b": 0.7}

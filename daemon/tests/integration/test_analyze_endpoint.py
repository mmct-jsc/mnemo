"""v5.12.0 -- POST /v1/analyze endpoint integration test.

Contract this test file locks:
- POST /v1/analyze (no body) returns 200 + the canonical envelope
  ``{ran_at, node_count_scanned, findings, summary}``.
- POST /v1/analyze with ``{types: ["stale"]}`` filters detectors.
- POST /v1/analyze with no findings still returns the envelope
  (empty findings list).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mnemo import server
from mnemo.store import Node, Store


@pytest.fixture
def app_client(tmp_path):
    """Mnemo app wired to a tmp Store + a deterministic fake embedder."""

    class _FakeEmbedder:
        dim = 384
        _model = None

        def embed_text(self, text):
            sig = (text or "")[:32].lower()
            base = [0.0] * 384
            for i, ch in enumerate(sig):
                base[i % 384] += ord(ch) / 1000.0
            norm = sum(x * x for x in base) ** 0.5 or 1.0
            return [x / norm for x in base]

        def embed_batch(self, texts):
            return [self.embed_text(t) for t in texts]

    store = Store(tmp_path / "mnemo.db")
    app = server.create_app(store=store, embedder=_FakeEmbedder())
    client = TestClient(app)
    yield client, store
    store.close()


def _mknode(
    *, id: str, type: str = "memory_feedback", description: str = "", body: str = ""
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


def test_analyze_endpoint_returns_canonical_envelope(app_client) -> None:
    client, _store = app_client
    r = client.post("/v1/analyze", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) >= {"ran_at", "node_count_scanned", "findings", "summary"}
    assert isinstance(body["findings"], list)
    assert isinstance(body["summary"], dict)


def test_analyze_endpoint_surfaces_stale_finding(app_client) -> None:
    """A node marked SUPERSEDED should show up as a stale finding."""
    client, store = app_client
    store.upsert_node(
        _mknode(
            id="memory_feedback/old",
            description="SUPERSEDED by something newer",
            body="old advice",
        )
    )
    r = client.post("/v1/analyze", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    stale_ids = [f["node_ids"][0] for f in body["findings"] if f["type"] == "stale"]
    assert "memory_feedback/old" in stale_ids


def test_analyze_endpoint_respects_types_filter(app_client) -> None:
    """``types: ["stale"]`` must skip the orphan_references detector."""
    client, store = app_client
    store.upsert_node(
        _mknode(
            id="memory_feedback/mixed",
            description="SUPERSEDED already",
            body="[mnemo:gone-forever]",
        )
    )
    r = client.post("/v1/analyze", json={"types": ["stale"]})
    assert r.status_code == 200, r.text
    body = r.json()
    types_seen = {f["type"] for f in body["findings"]}
    assert types_seen == {"stale"}, f"types filter not honored; saw {types_seen}"


def test_analyze_endpoint_accepts_empty_body(app_client) -> None:
    """POST /v1/analyze with no body should default to "run all"."""
    client, _store = app_client
    # Some HTTP clients send no body at all; FastAPI's default is to
    # require the body if it's a non-Optional model. Our AnalyzeIn is
    # optional so this should still work.
    r = client.post("/v1/analyze")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "ran_at" in body
    assert "summary" in body


def test_analyze_endpoint_summary_matches_findings(app_client) -> None:
    """The summary dict's counts must match the findings list."""
    client, store = app_client
    for i in range(3):
        store.upsert_node(
            _mknode(
                id=f"memory_feedback/stale-{i}",
                description=f"SUPERSEDED entry {i}",
                body="...",
            )
        )
    r = client.post("/v1/analyze", json={"types": ["stale"]})
    body = r.json()
    summary_stale = body["summary"].get("stale", 0)
    findings_stale = sum(1 for f in body["findings"] if f["type"] == "stale")
    assert summary_stale == findings_stale, (
        f"summary[stale]={summary_stale} mismatch with len(findings.stale)={findings_stale}"
    )


def test_analyze_endpoint_accepts_propose_actions_field(app_client) -> None:
    """v5.15.0: ``propose_actions`` is an accepted body field. With no
    env opt-in the enrichment is a no-op + the response stays
    byte-stable (findings carry no action, no skipped key)."""
    client, store = app_client
    store.upsert_node(
        _mknode(
            id="memory_feedback/x",
            description="canonical",
            body="cites [mnemo:does-not-exist] for context",
        )
    )
    r = client.post("/v1/analyze", json={"types": ["orphan_references"], "propose_actions": True})
    assert r.status_code == 200, r.text
    body = r.json()
    orphan = next(f for f in body["findings"] if f["type"] == "orphan_reference")
    # No proposer in test env -> action stays None even with the flag.
    assert orphan.get("action") is None
    assert "_refactor_actions_skipped" not in body["summary"]


def test_analyze_endpoint_finding_carries_action_and_concept_fields(app_client) -> None:
    """v5.15.0: the AnalyzeFinding schema declares ``action`` +
    ``concept`` so they survive HTTP serialization (the v5.14.0
    ``concept`` field was previously stripped)."""
    client, store = app_client
    store.upsert_node(
        _mknode(
            id="memory_feedback/x",
            description="canonical",
            body="cites [mnemo:does-not-exist]",
        )
    )
    r = client.post("/v1/analyze", json={"types": ["orphan_references"]})
    body = r.json()
    orphan = next(f for f in body["findings"] if f["type"] == "orphan_reference")
    # Both keys must be present in the serialized shape (value None is fine).
    assert "action" in orphan, "AnalyzeFinding must declare 'action' so HTTP keeps it"
    assert "concept" in orphan, "AnalyzeFinding must declare 'concept' so HTTP keeps it"

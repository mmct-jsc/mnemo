"""v5.15.0 -- refactor_actions enrichment pass (Phase 2c).

Phase 2c of mnemo's Understanding arc (see
``docs/plans/2026-05-23-mnemo-understanding-phase2c-design.md`` +
``memory/project_mnemo_v6_vision_understanding``).

refactor_actions is an ENRICHMENT over existing findings, not a 6th
detector. For each high/medium-severity finding, an opt-in LLM
proposer generates ONE concrete action mapping to an existing mnemo
primitive (mnemo_update_node / mnemo_delete_node / mnemo_create_node).

Contract this test file locks:

1. ``LLMRefactorProposer.propose(finding, node_bodies)`` returns a
   structured action dict ``{kind, primitive, target_node_id,
   args_hint, rationale}`` parsed from the model's JSON.
2. Every error path (parse / network / structure) degrades to
   ``{kind: "none", primitive: None, ...}`` -- the finding still
   ships; only its action is empty.
3. ``propose_refactor_actions(store, findings, proposer=...)``
   enriches ONLY findings whose severity is in ``severities``
   (default ("high","medium")); candidate + low are untouched.
4. The pass hard-caps at ``max_actions`` and returns
   ``(findings, n_skipped)`` where n_skipped counts eligible
   findings dropped by the cap. No silent caps.
5. Without a proposer the pass is a no-op: ``(findings, 0)`` with
   no ``action`` keys added.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from mnemo.store import Node, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "mnemo.db")
    yield s
    s.close()


def _mknode(
    *,
    id: str,
    type: str = "memory_feedback",
    name: str | None = None,
    description: str = "",
    body: str = "",
) -> Node:
    now = int(time.time())
    return Node(
        id=id,
        type=type,
        name=name if name is not None else id.split("/", 1)[-1],
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


def _finding(
    *,
    type: str = "duplicates",
    node_ids: list[str] | None = None,
    description: str = "two nodes are near-duplicates",
    severity: str = "medium",
) -> dict:
    return {
        "type": type,
        "node_ids": node_ids if node_ids is not None else ["a", "b"],
        "description": description,
        "severity": severity,
    }


# --- LLMRefactorProposer.propose() shape -------------------------------


def test_proposer_returns_structured_action() -> None:
    """A well-formed JSON response parses into the canonical action
    dict with all five keys."""
    from mnemo.analyzer import LLMRefactorProposer

    fake_response = MagicMock()
    fake_response.content = [
        MagicMock(
            text=(
                '{"kind": "delete", "primitive": "mnemo_delete_node", '
                '"target_node_id": "memory_feedback/old", '
                '"args_hint": {"node_id": "memory_feedback/old"}, '
                '"rationale": "fully superseded by the newer entry"}'
            )
        )
    ]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    p = LLMRefactorProposer(client=fake_client, model="claude-sonnet-4-6")
    action = p.propose(
        finding=_finding(type="stale", node_ids=["memory_feedback/old"], severity="low"),
        node_bodies={"memory_feedback/old": "old advice, superseded"},
    )
    assert action["kind"] == "delete"
    assert action["primitive"] == "mnemo_delete_node"
    assert action["target_node_id"] == "memory_feedback/old"
    assert action["args_hint"] == {"node_id": "memory_feedback/old"}
    assert "superseded" in action["rationale"].lower()


def test_proposer_handles_merge_action() -> None:
    """A duplicates finding can yield a merge action mapping to
    mnemo_update_node + a follow-up delete hint."""
    from mnemo.analyzer import LLMRefactorProposer

    fake_response = MagicMock()
    fake_response.content = [
        MagicMock(
            text=(
                '{"kind": "merge", "primitive": "mnemo_update_node", '
                '"target_node_id": "memory_feedback/canonical", '
                '"args_hint": {"node_id": "memory_feedback/canonical", '
                '"body": "merged body"}, '
                '"rationale": "B adds one paragraph; fold into A then delete B"}'
            )
        )
    ]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    p = LLMRefactorProposer(client=fake_client)
    action = p.propose(
        finding=_finding(
            type="duplicates",
            node_ids=["memory_feedback/canonical", "memory_feedback/dupe"],
        ),
        node_bodies={
            "memory_feedback/canonical": "long canonical body",
            "memory_feedback/dupe": "short dupe body",
        },
    )
    assert action["kind"] == "merge"
    assert action["primitive"] == "mnemo_update_node"


def test_proposer_returns_none_kind_on_parse_error() -> None:
    """Garbled model output degrades to kind='none' -- the finding
    still ships; only the action is empty."""
    from mnemo.analyzer import LLMRefactorProposer

    fake_response = MagicMock()
    fake_response.content = [MagicMock(text="not valid json at all")]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    p = LLMRefactorProposer(client=fake_client)
    action = p.propose(finding=_finding(), node_bodies={"a": "x", "b": "y"})
    assert action["kind"] == "none"
    assert action["primitive"] is None


def test_proposer_returns_none_kind_on_client_exception() -> None:
    """Network / SDK errors degrade to kind='none' (graceful)."""
    from mnemo.analyzer import LLMRefactorProposer

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("network down")

    p = LLMRefactorProposer(client=fake_client)
    action = p.propose(finding=_finding(), node_bodies={"a": "x", "b": "y"})
    assert action["kind"] == "none"
    assert action["primitive"] is None


def test_proposer_records_rationale_log() -> None:
    """The proposer keeps a per-finding audit trail."""
    from mnemo.analyzer import LLMRefactorProposer

    fake_response = MagicMock()
    fake_response.content = [
        MagicMock(
            text=(
                '{"kind": "supersede", "primitive": "mnemo_update_node", '
                '"target_node_id": "x", "args_hint": {}, '
                '"rationale": "newer wins"}'
            )
        )
    ]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    p = LLMRefactorProposer(client=fake_client)
    p.propose(
        finding=_finding(type="contradictions", node_ids=["x", "y"], severity="high"),
        node_bodies={"x": "a", "y": "b"},
    )
    assert len(p.rationale_log) == 1
    entry = p.rationale_log[0]
    assert entry["finding_type"] == "contradictions"
    assert entry["kind"] == "supersede"
    assert entry["parsed_ok"] is True


# --- propose_refactor_actions() enrichment pass ------------------------


def test_enrichment_enriches_high_and_medium_only(store) -> None:
    """Only findings with severity in ('high','medium') get an
    action; candidate + low are left untouched."""
    from mnemo.analyzer import propose_refactor_actions

    store.upsert_node(_mknode(id="n_high", body="high body"))
    store.upsert_node(_mknode(id="n_med", body="medium body"))
    store.upsert_node(_mknode(id="n_cand", body="candidate body"))
    store.upsert_node(_mknode(id="n_low", body="low body"))

    findings = [
        _finding(type="orphan_reference", node_ids=["n_high"], severity="high"),
        _finding(type="duplicates", node_ids=["n_med"], severity="medium"),
        _finding(type="contradictions", node_ids=["n_cand"], severity="candidate"),
        _finding(type="stale", node_ids=["n_low"], severity="low"),
    ]

    proposer = MagicMock()
    proposer.propose.return_value = {
        "kind": "delete",
        "primitive": "mnemo_delete_node",
        "target_node_id": None,
        "args_hint": {},
        "rationale": "r",
    }

    enriched, skipped = propose_refactor_actions(store, findings, proposer=proposer)
    by_sev = {f["severity"]: f for f in enriched}
    assert by_sev["high"].get("action") is not None, "high must be enriched"
    assert by_sev["medium"].get("action") is not None, "medium must be enriched"
    assert by_sev["candidate"].get("action") is None, "candidate must NOT be enriched"
    assert by_sev["low"].get("action") is None, "low must NOT be enriched"
    assert skipped == 0, f"nothing should be capped here; got {skipped}"
    # Proposer called exactly twice (high + medium).
    assert proposer.propose.call_count == 2


def test_enrichment_respects_cap_and_reports_skipped(store) -> None:
    """Eligible findings beyond max_actions are left unenriched and
    counted into n_skipped (no silent cap)."""
    from mnemo.analyzer import propose_refactor_actions

    for i in range(5):
        store.upsert_node(_mknode(id=f"n{i}", body=f"body {i}"))
    findings = [_finding(type="duplicates", node_ids=[f"n{i}"], severity="high") for i in range(5)]

    proposer = MagicMock()
    proposer.propose.return_value = {
        "kind": "merge",
        "primitive": "mnemo_update_node",
        "target_node_id": None,
        "args_hint": {},
        "rationale": "r",
    }

    enriched, skipped = propose_refactor_actions(store, findings, proposer=proposer, max_actions=2)
    enriched_count = sum(1 for f in enriched if f.get("action") is not None)
    assert enriched_count == 2, f"cap=2 should enrich exactly 2; got {enriched_count}"
    assert skipped == 3, f"5 eligible - 2 cap = 3 skipped; got {skipped}"
    assert proposer.propose.call_count == 2


def test_enrichment_no_proposer_is_noop(store) -> None:
    """Without a proposer the pass returns findings unchanged + 0
    skipped + adds NO action keys."""
    from mnemo.analyzer import propose_refactor_actions

    store.upsert_node(_mknode(id="n", body="body"))
    findings = [_finding(type="orphan_reference", node_ids=["n"], severity="high")]

    enriched, skipped = propose_refactor_actions(store, findings, proposer=None)
    assert skipped == 0
    assert all("action" not in f or f["action"] is None for f in enriched)


def test_enrichment_passes_node_bodies_to_proposer(store) -> None:
    """The proposer receives the cited nodes' bodies keyed by id."""
    from mnemo.analyzer import propose_refactor_actions

    store.upsert_node(_mknode(id="aa", body="body of aa"))
    store.upsert_node(_mknode(id="bb", body="body of bb"))
    findings = [_finding(type="duplicates", node_ids=["aa", "bb"], severity="medium")]

    captured = {}

    def _capture(*, finding, node_bodies):
        captured.update(node_bodies)
        return {
            "kind": "merge",
            "primitive": "mnemo_update_node",
            "target_node_id": None,
            "args_hint": {},
            "rationale": "r",
        }

    proposer = MagicMock()
    proposer.propose.side_effect = _capture

    propose_refactor_actions(store, findings, proposer=proposer)
    assert captured.get("aa") == "body of aa"
    assert captured.get("bb") == "body of bb"


def test_enrichment_custom_severities(store) -> None:
    """The severities filter is configurable -- e.g. enrich
    candidates too when explicitly requested."""
    from mnemo.analyzer import propose_refactor_actions

    store.upsert_node(_mknode(id="c", body="cand body"))
    findings = [_finding(type="semantic_orphan", node_ids=["c"], severity="candidate")]

    proposer = MagicMock()
    proposer.propose.return_value = {
        "kind": "create_definition",
        "primitive": "mnemo_create_node",
        "target_node_id": None,
        "args_hint": {},
        "rationale": "r",
    }

    enriched, skipped = propose_refactor_actions(
        store, findings, proposer=proposer, severities=("candidate",)
    )
    assert enriched[0].get("action") is not None, "candidate enriched when requested"
    assert skipped == 0


# --- analyze() orchestrator wiring -------------------------------------


def test_analyze_propose_actions_attaches_actions(store) -> None:
    """``analyze(propose_actions=True, proposer=MagicMock())`` attaches
    an action to each eligible finding + records the skipped count in
    the summary."""
    from mnemo.analyzer import analyze

    # A node marked SUPERSEDED -> stale (low) + an orphan_reference
    # (high) so we have at least one high-severity eligible finding.
    store.upsert_node(
        _mknode(
            id="memory_feedback/x",
            description="canonical",
            body="cites [mnemo:does-not-exist] for context",
        )
    )

    proposer = MagicMock()
    proposer.propose.return_value = {
        "kind": "fix_citation",
        "primitive": "mnemo_update_node",
        "target_node_id": "memory_feedback/x",
        "args_hint": {},
        "rationale": "dead citation",
    }

    result = analyze(
        store,
        types=["orphan_references"],
        propose_actions=True,
        proposer=proposer,
    )
    orphan = next(f for f in result["findings"] if f["type"] == "orphan_reference")
    assert orphan.get("action") is not None, "high-severity orphan_reference must be enriched"
    assert orphan["action"]["kind"] == "fix_citation"
    assert "_refactor_actions_skipped" in result["summary"]


def test_analyze_default_does_not_propose_actions(store) -> None:
    """Default analyze() (no propose_actions) leaves findings without
    an action -- byte-stable deterministic path."""
    from mnemo.analyzer import analyze

    store.upsert_node(
        _mknode(
            id="memory_feedback/x",
            description="canonical",
            body="cites [mnemo:does-not-exist]",
        )
    )
    result = analyze(store, types=["orphan_references"])
    orphan = next(f for f in result["findings"] if f["type"] == "orphan_reference")
    assert orphan.get("action") is None, "default path must not attach actions"
    assert "_refactor_actions_skipped" not in result["summary"], (
        "default path must not inflate the summary with the skipped key"
    )

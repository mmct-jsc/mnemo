"""v6.0.0: the store-backed half of the task harness.

``resolve_key`` (stable-key -> node), the deterministic ORACLE solver (the
canonical tool path per class), and ``generate_tasks`` (the graph-driven
candidate generator for the hybrid fixture).

The oracle answers "is the moat reachable through the actual graph, in how
many agent-facing calls" -- the honest floor under any LLM agent and a
regression guard the old hit@k never provided. The generator+oracle
CONSISTENCY check here is not the non-circularity guarantee (hand-curation in
the committed fixture is); it verifies the two read the graph identically.
"""

from __future__ import annotations

from pathlib import Path

from mnemo import eval_tasks as et
from mnemo.store import Node, Store


def _seed_graph(db: Path) -> Store:
    """callee <- {caller1, caller2} via calls; commit references callee +
    motivates a memory node -> a full structural + provenance fixture."""
    store = Store(db)

    def code(name: str, path: str, type: str = "code_function") -> Node:
        n = Node.new(
            type=type,
            name=name,
            description=f"{name} impl",
            body=f"def {name}(): ...",
            source_path=path,
            source_kind="code_repo",
        )
        store.upsert_node(n)
        return n

    callee = code("rekey_node", "/r/daemon/mnemo/store.py::rekey_node")
    caller1 = code(
        "_migrate_legacy_code_node", "/r/daemon/mnemo/ingest.py::_migrate_legacy_code_node"
    )
    caller2 = code("reconcile", "/r/daemon/mnemo/ingest.py::reconcile", type="code_method")
    store.add_edge(caller1.id, callee.id, "calls")
    store.add_edge(caller2.id, callee.id, "calls")

    commit = Node.new(
        type="commit",
        name="fix rekey",
        description="stable code-node identity",
        body="commit body referencing v5.28 design",
        source_path="/r@abc123def4567890",
        source_kind="code_repo",
    )
    store.upsert_node(commit)
    memory = Node.new(
        type="memory_project",
        name="session-handover-v5-28-0",
        description="v5.28.0 stable code identity migration shipped",
        body="we migrated legacy keys in place",
        source_path="/home/.claude/projects/x/memory/session_handover_v5_28_0.md",
        source_kind="memory_dir",
    )
    store.upsert_node(memory)
    store.add_edge(commit.id, callee.id, "references_function", confidence=0.9)
    store.add_edge(commit.id, memory.id, "motivated_by", confidence=0.9)
    return store


def test_resolve_key_matches_stable_key_suffix(tmp_path: Path) -> None:
    store = _seed_graph(tmp_path / "t.db")
    n = et.resolve_key(store, "daemon/mnemo/store.py::rekey_node")
    assert n is not None
    assert n.name == "rekey_node"
    store.close()


def test_resolve_key_respects_path_boundary(tmp_path: Path) -> None:
    store = _seed_graph(tmp_path / "t.db")
    # a bare "ekey_node" must NOT match rekey_node (no path boundary before it)
    assert et.resolve_key(store, "ekey_node") is None
    store.close()


def test_oracle_structural_recovers_callers_in_one_call(tmp_path: Path) -> None:
    store = _seed_graph(tmp_path / "t.db")
    task = et.Task(
        id="struct-rekey",
        cls="structural",
        prompt="What calls rekey_node?",
        subject_key="daemon/mnemo/store.py::rekey_node",
        answer_keys=[
            "daemon/mnemo/ingest.py::_migrate_legacy_code_node",
            "daemon/mnemo/ingest.py::reconcile",
        ],
        budget=1,
    )
    found, calls = et.oracle_solve(store, task)
    assert calls == 1, "reverse calls edges = one agent-facing get_edges call"
    result = et.score_task(found, task, calls_used=calls)
    assert result.recall == 1.0, f"both callers must be recovered; got {found}"
    assert result.success is True
    store.close()


def test_oracle_provenance_walks_commit_and_memory(tmp_path: Path) -> None:
    store = _seed_graph(tmp_path / "t.db")
    task = et.Task(
        id="prov-rekey",
        cls="provenance",
        prompt="Why does rekey_node exist?",
        subject_key="daemon/mnemo/store.py::rekey_node",
        answer_keys=["abc123def4567", "session_handover_v5_28_0"],
        budget=1,
    )
    found, calls = et.oracle_solve(store, task)
    assert calls == 1, "the provenance walk is a single mnemo_traverse call"
    result = et.score_task(found, task, calls_used=calls)
    assert result.recall == 1.0, f"commit + motivating memory must be recovered; got {found}"
    store.close()


def test_oracle_memory_recall_returns_retrieved_paths(tmp_path: Path, monkeypatch) -> None:
    from mnemo.embed import embed_node
    from tests.conftest import FakeEmbedder

    monkeypatch.setenv("MNEMO_HOME", str(tmp_path / "home"))
    store = Store(tmp_path / "m.db")
    mem = Node.new(
        type="memory_project",
        name="dual-remote-ship",
        description="the dual-remote ship sequence pushes both remotes then merges",
        body="origin=enterprise, public=authoritative",
        source_path="/home/.claude/projects/x/memory/feedback_dual_remote.md",
        source_kind="memory_dir",
    )
    store.upsert_node(mem)
    emb = FakeEmbedder()
    embed_node(store, mem, emb)

    task = et.Task(
        id="mem-dual",
        cls="memory_recall",
        prompt="what is the dual-remote ship sequence",
        answer_keys=["feedback_dual_remote"],
        budget=1,
    )
    found, calls = et.oracle_solve(store, task, embedder=emb, k=5)
    assert calls == 1, "memory recall is one mnemo_query call"
    result = et.score_task(found, task, calls_used=calls)
    assert result.recall == 1.0, f"the only memory node must surface in top-k; got {found}"
    store.close()


def test_generate_tasks_is_wellformed_and_capped(tmp_path: Path) -> None:
    store = _seed_graph(tmp_path / "t.db")
    tasks = et.generate_tasks(store, per_class=5)
    classes = {t.cls for t in tasks}
    assert "structural" in classes
    assert "provenance" in classes
    for t in tasks:
        assert t.cls in et.TASK_CLASSES
        assert t.prompt.strip()
        assert t.answer_keys, f"{t.id}: a generated task needs a ground-truth answer set"
        assert len([x for x in tasks if x.cls == t.cls]) <= 5
    store.close()


def test_generated_graph_tasks_are_oracle_consistent(tmp_path: Path) -> None:
    """Every generated structural/provenance task is recovered by the oracle
    on the same store (the generator and oracle read the graph identically)."""
    store = _seed_graph(tmp_path / "t.db")
    tasks = [t for t in et.generate_tasks(store, per_class=5) if t.cls != "memory_recall"]
    assert tasks, "the seed graph yields at least one structural + provenance task"
    for t in tasks:
        found, calls = et.oracle_solve(store, t)
        result = et.score_task(found, t, calls_used=calls)
        assert result.recall == 1.0, (
            f"{t.id}: oracle must recover its generated answer; got {found}"
        )
    store.close()

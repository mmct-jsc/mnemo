"""v5.19.0 -- cyclic_imports detector (code lens, the structure triad).

Third code-lens detector (after dead_code + god_object). Finds module
import cycles via an iterative Tarjan SCC over the ``imports`` edge
graph. Deterministic + precise -- a cycle is unambiguous, so there's
no LLM judge.

The live corpus is acyclic (0 cycles), so these synthetic fixtures
are what prove the detection LOGIC (the live run correctly returns
``[]``).

Contract:

1. A 2-module cycle (A imports B, B imports A) is flagged with both
   ids.
2. A 3-module cycle (A->B->C->A) is flagged with all three.
3. An acyclic chain (A->B->C) is NOT flagged.
4. A self-import (A imports A) is flagged.
5. Two disjoint cycles -> two findings.
6. Only ``imports`` edges count (a ``calls`` back-edge is ignored).
7. Severity is ``medium``.
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


def _mkmodule(store: Store, id: str, name: str | None = None) -> None:
    now = int(time.time())
    store.upsert_node(
        Node(
            id=id,
            type="code_module",
            name=name or id,
            description="",
            body="",
            source_path=f"/proj/{id}.py:1-5",
            source_kind="code",
            project_key="proj",
            frontmatter_json=None,
            hash="",
            created_at=now,
            updated_at=now,
        )
    )


def _imports(store: Store, a: str, b: str) -> None:
    """module ``a`` imports module ``b`` (imports: src=importer, dst=imported)."""
    store.add_edge(a, b, "imports")


def _cycle_findings(store: Store) -> list[dict]:
    from mnemo.analyzer import detect_cyclic_imports

    return [f for f in detect_cyclic_imports(store) if f["type"] == "cyclic_import"]


def test_two_module_cycle_flagged(store) -> None:
    for m in ("A", "B"):
        _mkmodule(store, m)
    _imports(store, "A", "B")
    _imports(store, "B", "A")
    findings = _cycle_findings(store)
    assert len(findings) == 1, f"expected one cycle finding; got {findings}"
    assert set(findings[0]["node_ids"]) == {"A", "B"}


def test_three_module_cycle_flagged(store) -> None:
    for m in ("A", "B", "C"):
        _mkmodule(store, m)
    _imports(store, "A", "B")
    _imports(store, "B", "C")
    _imports(store, "C", "A")
    findings = _cycle_findings(store)
    assert len(findings) == 1
    assert set(findings[0]["node_ids"]) == {"A", "B", "C"}


def test_acyclic_chain_not_flagged(store) -> None:
    for m in ("A", "B", "C"):
        _mkmodule(store, m)
    _imports(store, "A", "B")
    _imports(store, "B", "C")  # no back edge -> acyclic
    assert _cycle_findings(store) == []


def test_self_import_flagged(store) -> None:
    _mkmodule(store, "A")
    _imports(store, "A", "A")  # a module importing itself = trivial cycle
    findings = _cycle_findings(store)
    assert len(findings) == 1
    assert findings[0]["node_ids"] == ["A"]


def test_two_disjoint_cycles_two_findings(store) -> None:
    for m in ("A", "B", "C", "D"):
        _mkmodule(store, m)
    _imports(store, "A", "B")
    _imports(store, "B", "A")
    _imports(store, "C", "D")
    _imports(store, "D", "C")
    findings = _cycle_findings(store)
    assert len(findings) == 2, f"two disjoint cycles -> two findings; got {findings}"
    pairs = {frozenset(f["node_ids"]) for f in findings}
    assert pairs == {frozenset({"A", "B"}), frozenset({"C", "D"})}


def test_non_import_edges_ignored(store) -> None:
    """Only ``imports`` edges form cycles. A non-import back-edge
    (e.g. ``calls``) must not create a phantom cycle."""
    for m in ("A", "B"):
        _mkmodule(store, m)
    _imports(store, "A", "B")  # imports A->B (acyclic on its own)
    store.add_edge("B", "A", "calls")  # a non-import back edge
    assert _cycle_findings(store) == [], "calls edges must not count toward import cycles"


def test_severity_is_medium(store) -> None:
    for m in ("A", "B"):
        _mkmodule(store, m)
    _imports(store, "A", "B")
    _imports(store, "B", "A")
    f = _cycle_findings(store)[0]
    assert f["severity"] == "medium", f"cyclic_import severity should be 'medium'; got {f}"


def test_description_names_the_modules(store) -> None:
    _mkmodule(store, "A", name="alpha.py")
    _mkmodule(store, "B", name="beta.py")
    _imports(store, "A", "B")
    _imports(store, "B", "A")
    f = _cycle_findings(store)[0]
    assert "alpha.py" in f["description"]
    assert "beta.py" in f["description"]


def test_empty_store_no_findings(store) -> None:
    assert _cycle_findings(store) == []


def test_longer_chain_with_one_cycle(store) -> None:
    """A long acyclic prefix feeding a cycle: only the cycle members
    are reported, and the iterative SCC handles the depth."""
    mods = [f"m{i}" for i in range(12)]
    for m in mods:
        _mkmodule(store, m)
    # chain m0->m1->...->m9 (acyclic), then m9->m10->m11->m9 cycle.
    for i in range(9):
        _imports(store, mods[i], mods[i + 1])
    _imports(store, "m9", "m10")
    _imports(store, "m10", "m11")
    _imports(store, "m11", "m9")
    findings = _cycle_findings(store)
    assert len(findings) == 1
    assert set(findings[0]["node_ids"]) == {"m9", "m10", "m11"}


def test_via_lens_code(store) -> None:
    from mnemo.analyzer import analyze

    for m in ("A", "B"):
        _mkmodule(store, m)
    _imports(store, "A", "B")
    _imports(store, "B", "A")
    result = analyze(store, lens="code", types=["cyclic_imports"])
    types_seen = {f["type"] for f in result["findings"]}
    assert types_seen == {"cyclic_import"}, f"lens=code types=[cyclic_imports] -> got {types_seen}"
    assert result["summary"].get("cyclic_import", 0) >= 1

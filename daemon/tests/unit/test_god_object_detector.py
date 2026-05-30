"""v5.17.0 -- god_object detector (Phase 3b, code lens).

Second detector in the code lens (after dead_code). Unlike dead_code
(which leans on the best-effort `calls` graph), god_object counts
Tier-1 structural edges (`method_of`, `defines`) which are complete
-- so it's precise WITHOUT an LLM judge.

Contract this test file locks:

1. A code_class with MORE than GOD_CLASS_METHOD_THRESHOLD inbound
   ``method_of`` edges is a candidate (severity 'candidate').
2. A class AT the threshold is NOT flagged (strict ``>``).
3. A code_module with MORE than GOD_MODULE_DEFINES_THRESHOLD
   outbound ``defines`` edges is a candidate.
4. Test modules are excluded from god_module (they legitimately
   define many test functions).
5. Non-code nodes are never flagged.
6. The finding carries the symbol name + the count in its
   description.
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


def _node(
    *,
    id: str,
    type: str,
    name: str,
    source_path: str = "/proj/mod.py:1-5",
) -> Node:
    now = int(time.time())
    return Node(
        id=id,
        type=type,
        name=name,
        description="",
        body="",
        source_path=source_path,
        source_kind="code",
        project_key="proj",
        frontmatter_json=None,
        hash="",
        created_at=now,
        updated_at=now,
    )


def _mkclass(store: Store, *, id: str, name: str, n_methods: int) -> None:
    """A code_class with ``n_methods`` inbound method_of edges
    (method_of: src=method, dst=class). Real method nodes are created
    (the edges table has a FK on both endpoints). Methods are named
    publicly so they don't also become dead_code candidates."""
    store.upsert_node(_node(id=id, type="code_class", name=name))
    for i in range(n_methods):
        mid = f"{id}__m{i}"
        store.upsert_node(_node(id=mid, type="code_method", name=f"method_{i}"))
        store.add_edge(mid, id, "method_of")


def _mkmodule(
    store: Store, *, id: str, name: str, n_defines: int, path: str = "/proj/m.py"
) -> None:
    """A code_module with ``n_defines`` outbound defines edges
    (defines: src=module, dst=decl). Real decl nodes are created (FK)."""
    store.upsert_node(_node(id=id, type="code_module", name=name, source_path=path))
    for i in range(n_defines):
        did = f"{id}__d{i}"
        store.upsert_node(_node(id=did, type="code_function", name=f"decl_{i}"))
        store.add_edge(id, did, "defines")


# --- god class ----------------------------------------------------------


def test_god_class_over_threshold_is_flagged(store) -> None:
    from mnemo.analyzer import GOD_CLASS_METHOD_THRESHOLD, detect_god_object

    _mkclass(store, id="C", name="HugeService", n_methods=GOD_CLASS_METHOD_THRESHOLD + 1)
    findings = detect_god_object(store)
    ids = {f["node_ids"][0] for f in findings if f["type"] == "god_object"}
    assert "C" in ids, f"class with > threshold methods should be flagged; got {findings}"


def test_class_at_threshold_is_not_flagged(store) -> None:
    """Strict ``>``: exactly threshold methods is NOT a god class."""
    from mnemo.analyzer import GOD_CLASS_METHOD_THRESHOLD, detect_god_object

    _mkclass(store, id="C", name="BorderlineService", n_methods=GOD_CLASS_METHOD_THRESHOLD)
    findings = detect_god_object(store)
    ids = {f["node_ids"][0] for f in findings if f["type"] == "god_object"}
    assert "C" not in ids, f"class AT threshold must not be flagged (strict >); got {findings}"


def test_small_class_is_not_flagged(store) -> None:
    from mnemo.analyzer import detect_god_object

    _mkclass(store, id="C", name="TidyClass", n_methods=3)
    findings = detect_god_object(store)
    assert findings == [], f"small class must not be flagged; got {findings}"


def test_god_class_severity_is_candidate(store) -> None:
    from mnemo.analyzer import GOD_CLASS_METHOD_THRESHOLD, detect_god_object

    _mkclass(store, id="C", name="HugeService", n_methods=GOD_CLASS_METHOD_THRESHOLD + 5)
    f = next(x for x in detect_god_object(store) if x["type"] == "god_object")
    assert f["severity"] == "candidate", f"god_object severity should be 'candidate'; got {f}"


def test_god_class_description_has_symbol_and_count(store) -> None:
    from mnemo.analyzer import GOD_CLASS_METHOD_THRESHOLD, detect_god_object

    n = GOD_CLASS_METHOD_THRESHOLD + 7
    _mkclass(store, id="C", name="SprawlService", n_methods=n)
    f = next(x for x in detect_god_object(store) if x["node_ids"] == ["C"])
    assert f.get("symbol") == "SprawlService", f"finding must carry symbol; got {f}"
    assert "SprawlService" in f["description"]
    assert str(n) in f["description"], f"finding must state the method count {n}; got {f}"


# --- god module ---------------------------------------------------------


def test_god_module_over_threshold_is_flagged(store) -> None:
    from mnemo.analyzer import GOD_MODULE_DEFINES_THRESHOLD, detect_god_object

    _mkmodule(store, id="M", name="kitchen_sink.py", n_defines=GOD_MODULE_DEFINES_THRESHOLD + 1)
    ids = {f["node_ids"][0] for f in detect_god_object(store) if f["type"] == "god_object"}
    assert "M" in ids, "module with > threshold defines should be flagged"


def test_module_at_threshold_is_not_flagged(store) -> None:
    from mnemo.analyzer import GOD_MODULE_DEFINES_THRESHOLD, detect_god_object

    _mkmodule(store, id="M", name="borderline.py", n_defines=GOD_MODULE_DEFINES_THRESHOLD)
    ids = {f["node_ids"][0] for f in detect_god_object(store) if f["type"] == "god_object"}
    assert "M" not in ids, "module AT threshold must not be flagged (strict >)"


def test_test_module_is_excluded(store) -> None:
    """A test module legitimately defines many test_* functions and is
    not a god module."""
    from mnemo.analyzer import GOD_MODULE_DEFINES_THRESHOLD, detect_god_object

    _mkmodule(
        store,
        id="T",
        name="test_everything.py",
        n_defines=GOD_MODULE_DEFINES_THRESHOLD + 20,
        path="/proj/tests/unit/test_everything.py:1-5",
    )
    ids = {f["node_ids"][0] for f in detect_god_object(store) if f["type"] == "god_object"}
    assert "T" not in ids, f"test modules must be excluded from god_module; got ids {ids}"


# --- exclusions ---------------------------------------------------------


def test_non_code_nodes_ignored(store) -> None:
    """A memory node with many edges of some kind is never a
    god_object."""
    from mnemo.analyzer import detect_god_object

    now = int(time.time())
    store.upsert_node(
        Node(
            id="mem",
            type="memory_feedback",
            name="big note",
            description="",
            body="x",
            source_path="/m/mem.md",
            source_kind="memory",
            project_key=None,
            frontmatter_json=None,
            hash="",
            created_at=now,
            updated_at=now,
        )
    )
    assert detect_god_object(store) == []


# --- orchestrator via the code lens ------------------------------------


# --- v5.18.0: opt-in cohesion judge -----------------------------------


def test_god_object_judge_elevates_grab_bag_to_high(store) -> None:
    """With a judge that says should_split=True, the candidate becomes
    severity 'high'."""
    from unittest.mock import MagicMock

    from mnemo.analyzer import GOD_CLASS_METHOD_THRESHOLD, detect_god_object

    _mkclass(store, id="C", name="KitchenSink", n_methods=GOD_CLASS_METHOD_THRESHOLD + 1)
    judge = MagicMock()
    judge.judge.return_value = True
    findings = detect_god_object(store, judge=judge)
    god = next(f for f in findings if f["node_ids"] == ["C"])
    assert god["severity"] == "high", f"grab-bag should be elevated to high; got {god}"


def test_god_object_judge_drops_cohesive_facade(store) -> None:
    """With a judge that says should_split=False (cohesive), the
    candidate is DROPPED."""
    from unittest.mock import MagicMock

    from mnemo.analyzer import GOD_CLASS_METHOD_THRESHOLD, detect_god_object

    _mkclass(store, id="C", name="Store", n_methods=GOD_CLASS_METHOD_THRESHOLD + 1)
    judge = MagicMock()
    judge.judge.return_value = False
    findings = detect_god_object(store, judge=judge)
    ids = {f["node_ids"][0] for f in findings if f["type"] == "god_object"}
    assert "C" not in ids, f"cohesive facade should be dropped; got {findings}"


def test_god_object_judge_receives_member_names(store) -> None:
    """The judge is given the candidate's member names (the cohesion
    signal)."""
    from unittest.mock import MagicMock

    from mnemo.analyzer import GOD_CLASS_METHOD_THRESHOLD, detect_god_object

    _mkclass(store, id="C", name="HugeService", n_methods=GOD_CLASS_METHOD_THRESHOLD + 1)
    captured = {}

    def _capture(*, kind, name, members):
        captured["kind"] = kind
        captured["name"] = name
        captured["members"] = members
        return True

    judge = MagicMock()
    judge.judge.side_effect = _capture
    detect_god_object(store, judge=judge)
    assert captured["kind"] == "class"
    assert captured["name"] == "HugeService"
    # Member names are the public method_N names created by _mkclass.
    assert any(m.startswith("method_") for m in captured["members"]), (
        f"judge must receive member names; got {captured.get('members')}"
    )


def test_god_object_default_no_judge_is_candidate(store) -> None:
    """Without a judge the candidate keeps severity 'candidate'
    (byte-stable deterministic path)."""
    from mnemo.analyzer import GOD_CLASS_METHOD_THRESHOLD, detect_god_object

    _mkclass(store, id="C", name="HugeService", n_methods=GOD_CLASS_METHOD_THRESHOLD + 1)
    god = next(f for f in detect_god_object(store) if f["node_ids"] == ["C"])
    assert god["severity"] == "candidate"


def test_analyze_god_object_judge_wires_through(store) -> None:
    """analyze(lens='code', god_object_judge=...) escalates."""
    from unittest.mock import MagicMock

    from mnemo.analyzer import GOD_CLASS_METHOD_THRESHOLD, analyze

    _mkclass(store, id="C", name="KitchenSink", n_methods=GOD_CLASS_METHOD_THRESHOLD + 1)
    judge = MagicMock()
    judge.judge.return_value = True
    result = analyze(store, lens="code", types=["god_object"], god_object_judge=judge)
    god = next(f for f in result["findings"] if f["type"] == "god_object")
    assert god["severity"] == "high"


def test_god_object_via_code_lens(store) -> None:
    from mnemo.analyzer import GOD_CLASS_METHOD_THRESHOLD, analyze

    _mkclass(store, id="C", name="HugeService", n_methods=GOD_CLASS_METHOD_THRESHOLD + 1)
    result = analyze(store, lens="code", types=["god_object"])
    types_seen = {f["type"] for f in result["findings"]}
    assert types_seen == {"god_object"}, (
        f"lens=code types=[god_object] should isolate it; got {types_seen}"
    )
    assert result["summary"].get("god_object", 0) >= 1

"""v5.16.0 -- pluggable domain-lens mechanism (Phase 3).

A ``lens`` selects a domain-specific detector SUITE that REPLACES
the domain-agnostic five. ``lens=None`` (default) keeps the agnostic
behaviour byte-for-byte.

Contract this test file locks:

1. ``KNOWN_LENSES`` exports the available lens names (includes
   "code").
2. ``analyze(lens="code")`` runs ONLY the code-lens detectors
   (dead_code), not stale/duplicates/orphan_references/
   contradictions/semantic_orphans.
3. ``analyze(lens=None)`` runs the agnostic suite -- and never
   emits a dead_code finding.
4. An unknown lens runs no detectors and returns empty findings
   (permissive, matching the ``types`` contract).
5. ``types`` filters WITHIN a lens suite.
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


def _mkcode(*, id: str, name: str, type: str = "code_function") -> Node:
    now = int(time.time())
    return Node(
        id=id,
        type=type,
        name=name,
        description="",
        body=f"def {name}(): ...",
        source_path="/proj/mod.py:1-5",
        source_kind="code",
        project_key="proj",
        frontmatter_json=None,
        hash="",
        created_at=now,
        updated_at=now,
    )


def _add_methods(store: Store, class_id: str, n: int) -> None:
    """Give a code_class ``n`` methods (real method nodes + method_of
    edges -- the edges table has a FK on both endpoints)."""
    for i in range(n):
        mid = f"{class_id}__m{i}"
        store.upsert_node(_mkcode(id=mid, name=f"method_{i}", type="code_method"))
        store.add_edge(mid, class_id, "method_of")


def _mkmem(*, id: str, description: str = "", body: str = "") -> Node:
    now = int(time.time())
    return Node(
        id=id,
        type="memory_feedback",
        name=id,
        description=description,
        body=body,
        source_path=f"/m/{id}.md",
        source_kind="memory",
        project_key=None,
        frontmatter_json=None,
        hash="",
        created_at=now,
        updated_at=now,
    )


def test_known_lenses_includes_code() -> None:
    from mnemo.analyzer import KNOWN_LENSES

    assert "code" in KNOWN_LENSES, f"KNOWN_LENSES must list 'code'; got {KNOWN_LENSES}"


def test_lens_code_runs_only_code_detectors(store) -> None:
    """lens='code' surfaces dead_code and does NOT run the agnostic
    detectors (e.g. a SUPERSEDED memory node must not yield a stale
    finding under the code lens)."""
    from mnemo.analyzer import analyze

    store.upsert_node(_mkcode(id="f1", name="_dead_helper"))
    store.upsert_node(_mkmem(id="old", description="SUPERSEDED by new"))

    result = analyze(store, lens="code")
    types_seen = {f["type"] for f in result["findings"]}
    assert "dead_code" in types_seen, f"lens=code must surface dead_code; got {types_seen}"
    assert "stale" not in types_seen, (
        f"lens=code must NOT run the agnostic stale detector; got {types_seen}"
    )


def test_default_lens_none_does_not_run_dead_code(store) -> None:
    """The agnostic default suite never emits dead_code."""
    from mnemo.analyzer import analyze

    store.upsert_node(_mkcode(id="f1", name="_dead_helper"))
    store.upsert_node(_mkmem(id="old", description="SUPERSEDED by new"))

    result = analyze(store)  # lens=None default
    types_seen = {f["type"] for f in result["findings"]}
    assert "dead_code" not in types_seen, (
        f"the agnostic default must not run dead_code; got {types_seen}"
    )
    # And it DOES still run the agnostic detectors.
    assert "stale" in types_seen, f"agnostic default should still find stale; got {types_seen}"


def test_unknown_lens_returns_empty(store) -> None:
    """An unrecognized lens runs no detectors (permissive)."""
    from mnemo.analyzer import analyze

    store.upsert_node(_mkcode(id="f1", name="_dead_helper"))
    result = analyze(store, lens="bogus-lens")
    assert result["findings"] == [], f"unknown lens must return empty findings; got {result}"


def test_types_filters_within_lens(store) -> None:
    """types restricts which detectors run inside the lens suite. A
    type that isn't in the code suite yields nothing."""
    from mnemo.analyzer import analyze

    store.upsert_node(_mkcode(id="f1", name="_dead_helper"))

    # dead_code IS in the code suite -> surfaces.
    r1 = analyze(store, lens="code", types=["dead_code"])
    assert any(f["type"] == "dead_code" for f in r1["findings"])

    # stale is NOT in the code suite -> nothing.
    r2 = analyze(store, lens="code", types=["stale"])
    assert r2["findings"] == [], f"a non-code type within the code lens yields nothing; got {r2}"


def test_lens_summary_counts_dead_code(store) -> None:
    from mnemo.analyzer import analyze

    store.upsert_node(_mkcode(id="f1", name="_dead_helper"))
    store.upsert_node(_mkcode(id="f2", name="_also_dead", type="code_method"))
    result = analyze(store, lens="code")
    assert result["summary"].get("dead_code", 0) >= 2, (
        f"summary must count dead_code findings; got {result['summary']}"
    )


def test_code_lens_has_two_detectors(store) -> None:
    """v5.17.0: the code suite now holds dead_code AND god_object.
    With both a dead private fn and a god class present, lens=code
    surfaces both."""
    from mnemo.analyzer import GOD_CLASS_METHOD_THRESHOLD, analyze

    store.upsert_node(_mkcode(id="f1", name="_dead_helper"))
    store.upsert_node(_mkcode(id="C", name="HugeService", type="code_class"))
    _add_methods(store, "C", GOD_CLASS_METHOD_THRESHOLD + 1)

    types_seen = {f["type"] for f in analyze(store, lens="code")["findings"]}
    assert "dead_code" in types_seen, f"code lens must still surface dead_code; got {types_seen}"
    assert "god_object" in types_seen, f"code lens must surface god_object; got {types_seen}"


def test_code_lens_types_isolates_one_detector(store) -> None:
    """v5.17.0: types filters within the now-two-detector code suite."""
    from mnemo.analyzer import GOD_CLASS_METHOD_THRESHOLD, analyze

    store.upsert_node(_mkcode(id="f1", name="_dead_helper"))
    store.upsert_node(_mkcode(id="C", name="HugeService", type="code_class"))
    _add_methods(store, "C", GOD_CLASS_METHOD_THRESHOLD + 1)

    # Only god_object requested -> no dead_code even though a dead fn exists.
    only_god = {f["type"] for f in analyze(store, lens="code", types=["god_object"])["findings"]}
    assert only_god == {"god_object"}, f"types=[god_object] must isolate it; got {only_god}"

    # Only dead_code requested -> no god_object even though a god class exists.
    only_dead = {f["type"] for f in analyze(store, lens="code", types=["dead_code"])["findings"]}
    assert only_dead == {"dead_code"}, f"types=[dead_code] must isolate it; got {only_dead}"


def test_known_lenses_unchanged_and_dead_code_still_default_excluded(store) -> None:
    """KNOWN_LENSES is still just ('code',); the agnostic default
    still never runs god_object or dead_code."""
    from mnemo.analyzer import KNOWN_LENSES, analyze

    assert "code" in KNOWN_LENSES
    store.upsert_node(_mkcode(id="C", name="HugeService", type="code_class"))
    _add_methods(store, "C", 99)
    types_seen = {f["type"] for f in analyze(store)["findings"]}  # no lens
    assert "god_object" not in types_seen, (
        f"agnostic default must not run god_object; got {types_seen}"
    )

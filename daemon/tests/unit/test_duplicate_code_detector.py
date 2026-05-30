"""v5.20.0 -- duplicate_code detector (Understanding Phase 3d, code lens).

The 4th code-lens detector, after dead_code / god_object /
cyclic_imports. Surfaces WITHIN-project pairs of code_function /
code_method nodes with near-identical bodies (copy-paste duplication)
via the STORED embedding index (NOT re-embedding -- that took ~16 min
live; reading the index is seconds), NOT the import graph (too sparse;
see the design doc's orphan_modules rejection).

Gate (probe-validated, deterministic, no LLM judge):
  - type in (code_function, code_method);
  - NOT a test symbol (name test_* / _test_*, or /tests/ /test/ path);
  - body has >= 5 non-empty lines (suppress trivial one-liners);
  - cosine >= 0.97 within the combined code bucket;
  - SAME project_key (cross-repo copies aren't actionable);
  - pairs de-duplicated.

See ``docs/plans/2026-05-30-mnemo-understanding-phase3d-duplicate-code-design.md``.
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


def _v_a() -> list[float]:
    """Anchor vector A: [1, 0, ..., 0]."""
    return [1.0] + [0.0] * 383


def _v_b() -> list[float]:
    """Orthogonal vector B: [0, 1, 0, ..., 0] -- cosine 0 vs A."""
    return [0.0, 1.0] + [0.0] * 382


def _body(n_lines: int, tag: str = "x") -> str:
    """A code body with exactly ``n_lines`` non-empty lines, uniquely
    tagged so distinct tags yield distinct body strings."""
    return "\n".join(f"    result_{tag}_{i} = compute_{tag}({i})" for i in range(n_lines))


def _node(
    *,
    id: str,
    name: str,
    body: str,
    type: str = "code_function",
    source_path: str | None = None,
    project_key: str | None = "proj",
) -> Node:
    now = int(time.time())
    return Node(
        id=id,
        type=type,
        name=name,
        description="",
        body=body,
        source_path=source_path or f"/repo/src/{name}.py",
        source_kind="code",
        project_key=project_key,
        frontmatter_json=None,
        hash="",
        created_at=now,
        updated_at=now,
    )


def _mk_code(store: Store, *, vec: list[float], **kw) -> None:
    """Insert a code node + its STORED chunk-0 embedding (what the
    detector reads back -- no live embedder needed)."""
    n = _node(**kw)
    store.upsert_node(n)
    store.upsert_chunks(n.id, [(0, vec, n.body)])


# --- core detection ----------------------------------------------------


def test_empty_store_returns_empty(store) -> None:
    from mnemo.analyzer import detect_duplicate_code

    assert detect_duplicate_code(store) == []


def test_no_embeddings_returns_empty(store) -> None:
    """Nodes present but NOT embedded (no chunks) -> clean [] fallback."""
    from mnemo.analyzer import detect_duplicate_code

    dup = _body(8, "dup")
    store.upsert_node(_node(id="f/a", name="alpha", body=dup))
    store.upsert_node(_node(id="f/b", name="beta", body=dup))
    assert detect_duplicate_code(store) == []


def test_identical_functions_flagged_once(store) -> None:
    """Two same-project functions with identical >=5-line bodies ->
    exactly ONE finding (pair de-duped), severity medium, symbol set."""
    from mnemo.analyzer import detect_duplicate_code

    dup = _body(8, "dup")
    _mk_code(store, id="f/a", name="alpha", body=dup, vec=_v_a())
    _mk_code(store, id="f/b", name="beta", body=dup, vec=_v_a())

    findings = detect_duplicate_code(store)
    assert len(findings) == 1, f"expected exactly one de-duped pair; got {findings}"
    f = findings[0]
    assert f["type"] == "duplicate_code"
    assert sorted(f["node_ids"]) == ["f/a", "f/b"]
    assert f["severity"] == "medium"
    assert f.get("symbol")


def test_short_bodies_not_flagged(store) -> None:
    """Identical but only 3 non-empty lines -> below the min-lines gate."""
    from mnemo.analyzer import detect_duplicate_code

    short = _body(3, "short")
    _mk_code(store, id="f/a", name="alpha", body=short, vec=_v_a())
    _mk_code(store, id="f/b", name="beta", body=short, vec=_v_a())

    assert detect_duplicate_code(store) == []


def test_test_symbols_not_flagged(store) -> None:
    """A duplicated pair of TEST symbols is excluded (intentional dup in
    tests is common; consistency with dead_code / god_object)."""
    from mnemo.analyzer import detect_duplicate_code

    dup = _body(8, "tdup")
    _mk_code(
        store,
        id="t/a",
        name="test_alpha",
        body=dup,
        vec=_v_a(),
        source_path="/repo/tests/unit/test_alpha.py",
    )
    _mk_code(
        store,
        id="t/b",
        name="test_beta",
        body=dup,
        vec=_v_a(),
        source_path="/repo/tests/unit/test_beta.py",
    )
    assert detect_duplicate_code(store) == []


def test_different_functions_not_flagged(store) -> None:
    """Two clearly-different functions (orthogonal vectors, cosine 0)
    are not flagged."""
    from mnemo.analyzer import detect_duplicate_code

    _mk_code(store, id="f/a", name="alpha", body=_body(8, "a"), vec=_v_a())
    _mk_code(store, id="f/b", name="beta", body=_body(8, "b"), vec=_v_b())

    assert detect_duplicate_code(store) == []


def test_cross_project_pairs_not_flagged(store) -> None:
    """Identical bodies in DIFFERENT projects are NOT flagged -- a copy
    across unrelated repos isn't an actionable shared-helper extract."""
    from mnemo.analyzer import detect_duplicate_code

    dup = _body(8, "xproj")
    _mk_code(store, id="a/dup", name="widget", body=dup, vec=_v_a(), project_key="projA")
    _mk_code(store, id="b/dup", name="widget", body=dup, vec=_v_a(), project_key="projB")

    assert detect_duplicate_code(store) == [], "cross-project duplicates must not be flagged"


def test_function_method_cross_kind_pair_flagged(store) -> None:
    """A function and a method with identical bodies in the same project
    ARE duplicates (combined code bucket -- both are code)."""
    from mnemo.analyzer import detect_duplicate_code

    dup = _body(7, "xk")
    _mk_code(store, id="f/fn", name="helper", body=dup, vec=_v_a(), type="code_function")
    _mk_code(store, id="m/meth", name="helper", body=dup, vec=_v_a(), type="code_method")

    findings = detect_duplicate_code(store)
    assert len(findings) == 1, f"function<->method identical pair should flag once; got {findings}"
    assert sorted(findings[0]["node_ids"]) == ["f/fn", "m/meth"]


def test_three_identical_yield_three_pairs(store) -> None:
    """Three mutually-identical same-project functions -> 3 unique
    pairs, each once."""
    from mnemo.analyzer import detect_duplicate_code

    dup = _body(9, "tri")
    for i in ("a", "b", "c"):
        _mk_code(store, id=f"f/{i}", name=f"fn_{i}", body=dup, vec=_v_a())

    findings = detect_duplicate_code(store)
    pairs = {tuple(sorted(f["node_ids"])) for f in findings}
    assert pairs == {("f/a", "f/b"), ("f/a", "f/c"), ("f/b", "f/c")}, f"got {pairs}"


# --- orchestrator / lens integration -----------------------------------


def test_lens_code_surfaces_duplicate_code(store) -> None:
    from mnemo.analyzer import analyze

    dup = _body(8, "dup")
    _mk_code(store, id="f/a", name="alpha", body=dup, vec=_v_a())
    _mk_code(store, id="f/b", name="beta", body=dup, vec=_v_a())

    result = analyze(store, lens="code", types=["duplicate_code"])
    types_seen = {f["type"] for f in result["findings"]}
    assert types_seen == {"duplicate_code"}, f"types filter didn't isolate; saw {types_seen}"
    assert result["summary"].get("duplicate_code", 0) >= 1


def test_agnostic_default_does_not_run_duplicate_code(store) -> None:
    """lens=None (agnostic suite) NEVER runs the code-lens detector --
    even when types names it (it's not in the active suite)."""
    from mnemo.analyzer import analyze

    dup = _body(8, "dup")
    _mk_code(store, id="f/a", name="alpha", body=dup, vec=_v_a())
    _mk_code(store, id="f/b", name="beta", body=dup, vec=_v_a())

    # default suite, no lens
    result = analyze(store)
    assert all(f["type"] != "duplicate_code" for f in result["findings"])
    # types names it but no lens -> not in agnostic suite -> nothing
    result2 = analyze(store, types=["duplicate_code"])
    assert all(f["type"] != "duplicate_code" for f in result2["findings"])


def test_duplicate_code_registered_in_code_lens() -> None:
    from mnemo.analyzer import LENS_DETECTORS

    assert "duplicate_code" in LENS_DETECTORS["code"], (
        f"code lens must include duplicate_code; got {LENS_DETECTORS['code']}"
    )

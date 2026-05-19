"""Contract for the static GitHub Pages demo builder.

The demo IS the real Nebula page: the actual app.css + the actual
mark.svg logo + the actual vendored renderer, driven by a
deterministic synthetic graph that DEPICTS mnemo (no real/workspace
data) laid out by the REAL engine and baked to one nebula.json with
per-node detail + the adjacency, so the demo has the SAME functions
as the local /graph (3-panel shell, tree, filter, select/highlight,
detail + connections, toggles). GPU-free + authoritative. Also the
hard security guard: no secret-shaped string may ever enter the tree
(a full-perm PAT was pasted in chat).
"""

from __future__ import annotations

import importlib.util
import json
import math
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def bd():
    spec = importlib.util.spec_from_file_location("build_demo", REPO / "scripts" / "build_demo.py")
    assert spec, "scripts/build_demo.py must exist"
    assert spec.loader, "scripts/build_demo.py must be importable"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_seed_graph_is_deterministic_and_mnemo_themed(bd) -> None:
    a = bd.build_seed_graph()
    b = bd.build_seed_graph()
    assert a == b, "seed must be byte-deterministic"
    na, ea, ma = a
    assert 800 <= na <= 2000, f"seed scale {na} must read as a galaxy yet stay lean"
    assert len(ma) == na, "one meta record per node"
    from mnemo.ui.palette import TYPE_COLORS

    types = {m["type"] for m in ma}
    assert types <= set(TYPE_COLORS), f"unknown node types: {types - set(TYPE_COLORS)}"
    assert {"code_module", "code_function", "memory_project", "commit"} <= types, (
        "the synthetic graph must depict mnemo (code + memory + commit layers)"
    )
    names = " ".join(m["name"] for m in ma).lower()
    assert "graph_layout" in names, "node names must depict mnemo (graph_layout)"
    assert "nebula" in names, "node names must depict mnemo (nebula)"
    assert "agent_tools" in names, "node names must depict mnemo (agent_tools)"
    for s, t in ea:
        assert 0 <= s < na, "edge source in range"
        assert 0 <= t < na, "edge target in range"
        assert s != t, "no self-loops"


def test_nebula_json_schema_finite_and_deterministic(bd) -> None:
    j1 = bd.build_nebula_json()
    j2 = bd.build_nebula_json()
    assert json.dumps(j1, sort_keys=True) == json.dumps(j2, sort_keys=True), (
        "nebula.json must be byte-identical across builds (the real "
        "layout engine is deterministic) -> cacheable + reviewable"
    )
    nodes, edges, adj = j1["nodes"], j1["edges"], j1["adj"]
    assert len(nodes) >= 800
    n = len(nodes)
    for nd in nodes:
        assert isinstance(nd["x"], float)
        assert isinstance(nd["y"], float)
        assert math.isfinite(nd["x"])
        assert math.isfinite(nd["y"])
        assert nd["size"] > 0
        c = nd["color"]
        assert len(c) == 3, "rgb triplet"
        assert all(0.0 <= v <= 1.0 for v in c), "rgb in 0..1"
        assert nd["name"], "node has a name"
        assert nd["type"], "node has a type"
        # detail-panel fields (parity with the local /graph)
        assert isinstance(nd["desc"], str), "description is a string"
        assert nd["desc"], "node has a non-empty description"
        assert isinstance(nd["body"], str), "body is a string"
        assert nd["body"], "node has a non-empty body"
        assert isinstance(nd["src"], str), "node has a source path (may be '')"
        assert isinstance(nd["short"], str), "short label is a string"
        assert nd["short"], "node has a non-empty short label"
    for e in edges:
        assert 0 <= e["s"] < n, "edge source index in range"
        assert 0 <= e["t"] < n, "edge target index in range"
    # the adjacency drives the detail panel's Connections list.
    assert isinstance(adj, dict), "adj is a dict"
    assert adj, "nebula.json must carry the adjacency"
    for k, lst in adj.items():
        assert 0 <= int(k) < n, "adj key is a valid node index"
        for e in lst:
            assert 0 <= e["i"] < n, "neighbor index in range"
            assert isinstance(e["rel"], str), "relation is a string"
            assert e["rel"], "neighbor has a relation"
            assert 0.0 <= e["conf"] <= 1.0, "confidence in 0..1"


def test_page_is_the_real_nebula_shell_with_all_functions() -> None:
    """The demo must be the REAL Nebula (app.css + mark.svg + the
    vendored renderer) with the local /graph functions, not a bare
    canvas: 3-panel shell, tree, filter, select/deselect, highlight,
    detail + connections, edge/label toggles."""
    t = (REPO / "demo" / "index.html.tmpl").read_text(encoding="utf-8")
    for tok in (
        # the real stylesheet + the real logo (not the vscode brain)
        '<link rel="stylesheet" href="app.css">',
        "mark.svg",
        # the real renderer
        "regl.min.js",
        "nebula-gl.js",
        "NebulaGL.create(",
        "nebula.json",
        'id="nebula-gl"',
        'id="nebula-labels"',
        # the real 3-panel shell structure (styled by app.css)
        'class="nebula-shell"',
        'class="nebula-tree"',
        'class="nebula-canvas"',
        'class="nebula-detail"',
        # the local /graph functions
        "gl.select(",
        "clickNode",
        "clickStage",
        "setHighlight",
        "setEdgesVisible",
        "setLabelsVisible",
        '"Escape"',
        "tree-file",
        "Connections (",
    ):
        assert tok in t, f"demo must wire the real Nebula function/asset: {tok!r}"
    assert "127.0.0.1:7373" not in t, "the demo is static -- no daemon URL"
    assert "unpkg.com" not in t, "no CDN runtime dep"
    assert "cdn." not in t, "no CDN runtime dep"


def test_assemble_emits_only_the_real_nebula_fileset(bd, tmp_path) -> None:
    out = tmp_path / "dist"
    bd.assemble(out)
    got = {p.name for p in out.iterdir() if p.is_file()}
    assert got == {
        "index.html",
        "nebula.json",
        "regl.min.js",
        "nebula-gl.js",
        "app.css",
        "mark.svg",
    }, f"dist must be exactly the real-Nebula set, got {sorted(got)}"
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "%%" not in html, "no unrendered template slot (%%NAME%% marker)"
    assert json.loads((out / "nebula.json").read_text(encoding="utf-8"))["nodes"]
    assert "<svg" in (out / "mark.svg").read_text(encoding="utf-8"), (
        "mark.svg must be the real mnemo logo"
    )


def test_no_secret_shaped_string_in_tracked_tree() -> None:
    """A full-perm GitHub PAT was pasted in chat. Nothing token-shaped
    may ever be committed (build_demo / workflow / docs / template)."""
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split()
    pat = re.compile(r"\b(github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,})\b")
    # The secret-redaction test suite legitimately embeds SYNTHETIC
    # example tokens as fixtures -- that is its whole point. Exclude
    # that corpus; the guard still covers build_demo / the workflow /
    # docs / template / handovers / everything else.
    allow = {"daemon/tests/unit/test_safeguards.py"}
    offenders = []
    for rel in tracked:
        if rel in allow:
            continue
        f = REPO / rel
        try:
            if f.stat().st_size > 2_000_000:
                continue
            txt = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if pat.search(txt):
            offenders.append(rel)
    assert not offenders, f"secret-shaped string in tracked files: {offenders}"

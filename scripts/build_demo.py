"""Build the static GitHub Pages demo for mnemo.

A lean, static artifact (no daemon, no CDN) that is the REAL Nebula
page: the actual ``app.css`` + the actual ``mark.svg`` logo + the
actual vendored renderer (``regl`` + ``nebula-gl.js``), driven by a
DETERMINISTIC synthetic graph that *depicts mnemo's own architecture*
(no real/workspace/private data). The synthetic graph is laid out by
the REAL engine (``mnemo.ui.graph_layout.compute_graph_layout``) and
baked to one ``nebula.json`` (positions + per-node detail + the
adjacency) so the demo has the SAME functions as the local /graph:
3-panel shell, file tree, filter bar, hover/select/highlight, the
detail panel with description/body + a Connections (neighbors) list,
edge/label toggles, and all the deselect paths.

Importable (the unit-test contract drives it) and a CLI:
    uv run python scripts/build_demo.py --out dist
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "daemon" / "mnemo" / "ui"
VENDOR = UI / "static" / "vendor"
APP_CSS = UI / "static" / "app.css"
LOGO = UI / "static" / "mnem" / "mark.svg"
TMPL = REPO / "demo" / "index.html.tmpl"
REPO_URL = "https://github.com/mmct-jsc/mnemo"
PAGES_URL = "https://mmct-jsc.github.io/mnemo/"

_SEED = 4609  # fixed -> byte-identical builds (cacheable, reviewable)


def _version() -> str:
    txt = (REPO / "daemon" / "mnemo" / "__init__.py").read_text(encoding="utf-8")
    for line in txt.splitlines():
        if line.startswith("__version__"):
            return line.split('"')[1]
    return "0.0.0"


# Eight community "subsystems" mirroring mnemo's real architecture.
_SUBSYSTEMS = [
    (
        "ui",
        "mnemo.ui",
        [
            "graph_layout",
            "compute_graph_layout",
            "_galaxy_transform",
            "_spectral",
            "_separate",
            "routes",
            "palette",
            "color_for",
            "server",
            "create_app",
            "nebula-gl.js",
            "NebulaGL.create",
            "graph.html",
            "renderCanvas",
        ],
    ),
    (
        "retrieval",
        "mnemo.retrieve",
        [
            "retrieve",
            "hybrid_search",
            "rank",
            "score_node",
            "embed",
            "Embedder",
            "compress_budget",
            "cite",
            "mmr_rerank",
        ],
    ),
    (
        "agent",
        "mnemo.agent",
        [
            "agent_tools",
            "TOOLS",
            "mnemo_highlight_nodes",
            "mnemo_session_nodes",
            "mcp_server",
            "build_server",
            "providers",
            "AnthropicProvider",
            "run_loop",
        ],
    ),
    (
        "ingest",
        "mnemo.ingest",
        [
            "ingest",
            "scan_source",
            "tier1_treesitter",
            "tier2_resolver",
            "tier3_fastapi",
            "tier3_react",
            "auto_router",
            "classify_kind",
        ],
    ),
    (
        "store",
        "mnemo.store",
        [
            "Store",
            "query",
            "list_nodes",
            "upsert_node",
            "sqlite_vec",
            "_ensure_columns",
            "count_nodes_total",
        ],
    ),
    (
        "chat",
        "mnemo.chat",
        [
            "ChatLoop",
            "stream_sse",
            "permission_request",
            "permit",
            "Conversation",
            "doc_helper",
        ],
    ),
    (
        "hooks",
        "mnemo.hooks",
        [
            "session_start",
            "user_prompt_submit",
            "post_tool_use",
            "inject_context",
            "budget_cap",
        ],
    ),
    (
        "memory",
        "mnemo.memory",
        [
            "MEMORY.md",
            "reindex",
            "capture",
            "base_flag",
            "workspace",
        ],
    ),
]

_MEM = {
    "ui": [
        ("reference_cosmos_gl_nebula", "memory_reference"),
        ("session-handover-v4.6.2-shipped", "session_summary"),
        ("mnemo-v4.6-custom-graph-engine", "plan_doc"),
    ],
    "agent": [
        ("feedback_three_failed_fixes_architecture", "memory_feedback"),
        ("project_mnemo_built", "memory_project"),
    ],
    "retrieval": [
        ("reference_mnemo_pipelines", "memory_reference"),
        ("feedback_revert_over_perfectionize", "memory_feedback"),
    ],
    "store": [("project_mnemo_base_isolation", "memory_project")],
    "ingest": [("reference_competitor_gitnexus", "memory_reference")],
    "chat": [("project_mnemo_future_versions", "memory_project")],
    "hooks": [("feedback_mnemo_release_workflow", "memory_feedback")],
    "memory": [
        ("architecture", "project_doc"),
        ("user_global_prefs", "memory_user"),
    ],
}

# deterministic-but-realistic relation per (src_type -> dst_type).
_REL = {
    "code_module": ("defines", 0.95),
    "code_class": ("method_of", 0.95),
    "commit": ("touched_by", 1.0),
    "memory_feedback": ("applies_to", 0.8),
    "memory_project": ("applies_to", 0.8),
    "memory_reference": ("applies_to", 0.8),
    "plan_doc": ("documents", 0.85),
    "session_summary": ("recorded_in", 0.7),
    "project_doc": ("documents", 0.85),
}


def _src_path(pkg: str, typ: str, name: str) -> str:
    base = pkg.replace(".", "/")
    if name.endswith(".js"):
        return f"daemon/{base}/static/vendor/{name}"
    if name.endswith(".html"):
        return f"daemon/{base}/templates/{name}"
    if typ.startswith("memory_") or typ in ("session_summary", "memory_user"):
        return f"~/.claude/projects/D--Repository-knowledge-base/memory/{name}.md"
    if typ in ("plan_doc", "project_doc"):
        return f"docs/plans/{name}.md"
    if typ == "commit":
        return ""
    return f"daemon/{base}.py"


def build_seed_graph() -> tuple[int, list[tuple[int, int]], list[dict]]:
    """Deterministic synthetic graph depicting mnemo. ``meta[i]`` has
    ``{name, short, type, comm, src}``."""
    rng = random.Random(_SEED)
    meta: list[dict] = []
    edges: list[tuple[int, int]] = []
    comm_members: list[list[int]] = []

    for ci, (key, pkg, syms) in enumerate(_SUBSYSTEMS):
        members: list[int] = []
        mod_idx = len(meta)
        meta.append(
            {
                "name": pkg,
                "short": pkg,
                "type": "code_module",
                "comm": ci,
                "src": _src_path(pkg, "code_module", "x"),
            }
        )
        members.append(mod_idx)
        per = 150
        for k in range(per):
            sym = syms[k % len(syms)]
            if sym.endswith((".js", ".html")):
                typ = "code_module"
            elif sym[0].isupper():
                typ = "code_class" if k % 5 == 0 else "code_method"
            elif "route" in sym or "endpoint" in sym or sym.startswith("tier3"):
                typ = "code_route" if k % 2 else "code_endpoint"
            else:
                typ = "code_function"
            short = sym if k < len(syms) else f"{sym}_{k // len(syms)}"
            i = len(meta)
            meta.append(
                {
                    "name": f"{pkg}.{short}",
                    "short": short,
                    "type": typ,
                    "comm": ci,
                    "src": _src_path(pkg, typ, short),
                }
            )
            members.append(i)
            edges.append((mod_idx, i))
            for _ in range(3):
                j = rng.choice(members)
                if j != i:
                    edges.append((i, j))
        for mname, mtype in _MEM.get(key, []):
            i = len(meta)
            meta.append(
                {
                    "name": mname,
                    "short": mname,
                    "type": mtype,
                    "comm": ci,
                    "src": _src_path(pkg, mtype, mname),
                }
            )
            members.append(i)
            edges.append((rng.choice(members[:per]), i))
        for _ in range(6):
            i = len(meta)
            sha = f"{rng.randrange(16**7):07x}"
            msg = f"feat({key}): " + rng.choice(syms)
            meta.append(
                {
                    "name": f"{sha} {msg}",
                    "short": sha,
                    "type": "commit",
                    "comm": ci,
                    "src": "",
                }
            )
            members.append(i)
            edges.append((i, rng.choice(members[:per])))
        comm_members.append(members)

    for a in range(len(comm_members)):
        for b in range(a + 1, len(comm_members)):
            for _ in range(2):
                edges.append(
                    (
                        rng.choice(comm_members[a]),
                        rng.choice(comm_members[b]),
                    )
                )
    return len(meta), edges, meta


def _desc(m: dict) -> str:
    t = m["type"]
    if t == "code_module":
        return f"Module {m['name']} -- a synthetic stand-in for one of mnemo's packages."
    if t in ("code_function", "code_method"):
        return f"{m['short']}(): a representative {t.replace('code_', '')} in {m['comm']}."
    if t == "code_class":
        return f"class {m['short']}: a representative type in this subsystem."
    if t in ("code_route", "code_endpoint"):
        return f"{m['short']}: an HTTP surface anchor (cross-stack sitemap)."
    if t == "commit":
        return "A synthetic commit touching this subsystem (provenance edge)."
    return f"{m['short']}: a synthetic {t} node depicting mnemo's memory layer."


def _body(m: dict) -> str:
    t = m["type"]
    if t.startswith("code_"):
        return (
            f"# {m['name']}\n# (synthetic demo node -- depicts mnemo's "
            f"own architecture; no real source)\n\ndef {m['short']}(...):\n"
            f"    ...  # {m['comm']} subsystem"
        )
    return (
        f"---\nname: {m['short']}\ntype: {t}\n---\n\nSynthetic "
        f"{t} node for the mnemo demo galaxy (no real/private content)."
    )


def _hex_rgb(h: str) -> list[float]:
    h = h.lstrip("#")
    return [round(int(h[i : i + 2], 16) / 255.0, 4) for i in (0, 2, 4)]


def build_nebula_json() -> dict:
    from mnemo.ui.graph_layout import compute_graph_layout
    from mnemo.ui.palette import color_for

    n, edges, meta = build_seed_graph()
    deg = [0] * n
    for s, t in edges:
        deg[s] += 1
        deg[t] += 1
    pos = compute_graph_layout(n, edges)
    nodes = []
    for i in range(n):
        m = meta[i]
        d = deg[i]
        nodes.append(
            {
                "x": float(pos[2 * i]),
                "y": float(pos[2 * i + 1]),
                "size": round(max(1.5, min(4.6, 1.3 + (d**0.5) * 0.78)), 3),
                "color": _hex_rgb(color_for(m["type"])),
                "name": m["name"],
                "short": m["short"],
                "type": m["type"],
                "deg": d,
                "src": m["src"],
                "desc": _desc(m),
                "body": _body(m),
            }
        )
    seen: set[tuple[int, int]] = set()
    out_e: list[dict] = []
    adj: dict[str, list[dict]] = {}
    for s, t in edges:
        if s == t:
            continue
        k = (s, t) if s < t else (t, s)
        if k in seen:
            continue
        seen.add(k)
        out_e.append({"s": s, "t": t})
        rel, conf = _REL.get(meta[s]["type"], ("calls", 0.9))
        if meta[s]["comm"] != meta[t]["comm"]:
            rel, conf = "imports", 0.8
        adj.setdefault(str(s), []).append({"i": t, "rel": rel, "conf": conf})
        adj.setdefault(str(t), []).append({"i": s, "rel": rel, "conf": conf})
    return {"nodes": nodes, "edges": out_e, "adj": adj}


def render_index() -> str:
    tmpl = TMPL.read_text(encoding="utf-8")
    for k, v in {
        "%%VERSION%%": _version(),
        "%%REPO_URL%%": REPO_URL,
        "%%PAGES_URL%%": PAGES_URL,
    }.items():
        tmpl = tmpl.replace(k, v)
    return tmpl


def assemble(out: Path) -> None:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "nebula.json").write_text(
        json.dumps(build_nebula_json(), separators=(",", ":")),
        encoding="utf-8",
    )
    (out / "index.html").write_text(render_index(), encoding="utf-8")
    shutil.copyfile(VENDOR / "regl.min.js", out / "regl.min.js")
    shutil.copyfile(VENDOR / "nebula-gl.js", out / "nebula-gl.js")
    shutil.copyfile(APP_CSS, out / "app.css")
    shutil.copyfile(LOGO, out / "mark.svg")
    # v5.1.1: copy the themed-cursor SVG assets the live UI uses. The
    # relative URL in app.css (`cursors/mnem-cursor.svg`) resolves to
    # the demo's `<out>/cursors/` after this copy, matching how the
    # daemon serves them at `/static/cursors/`.
    cursors_dir = UI / "static" / "cursors"
    if cursors_dir.is_dir():
        shutil.copytree(cursors_dir, out / "cursors", dirs_exist_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the mnemo Pages demo")
    ap.add_argument("--out", default="dist", help="output directory")
    args = ap.parse_args()
    assemble(Path(args.out))
    print(f"demo built -> {args.out}")


if __name__ == "__main__":
    main()

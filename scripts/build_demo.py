"""Build the static GitHub Pages demo for mnemo.

A lean, static artifact (no daemon, no CDN). A DETERMINISTIC synthetic
graph that *depicts mnemo's own architecture* (no real/workspace/
private data) is run through the REAL server layout engine
(``mnemo.ui.graph_layout.compute_graph_layout``) and baked to one
``nebula.json`` that the vendored client renderer (``regl`` +
``nebula-gl.js``) draws live. Feature-card snippets are generated
from the SAME seed so the page stays truthful to the shown graph.

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
VENDOR = REPO / "daemon" / "mnemo" / "ui" / "static" / "vendor"
TMPL = REPO / "demo" / "index.html.tmpl"
BRAIN = REPO / "extensions" / "vscode" / "media" / "brain.svg"
REPO_URL = "https://github.com/mmct-jsc/mnemo"
PAGES_URL = "https://mmct-jsc.github.io/mnemo/"

_SEED = 4609  # fixed -> byte-identical builds (cacheable, reviewable)


def _version() -> str:
    txt = (REPO / "daemon" / "mnemo" / "__init__.py").read_text(encoding="utf-8")
    for line in txt.splitlines():
        if line.startswith("__version__"):
            return line.split('"')[1]
    return "0.0.0"


# --- the synthetic mnemo-themed graph -------------------------------
# Eight community "subsystems" mirroring mnemo's real architecture.
# Each contributes a code_module + code_function/method/class, memory
# nodes, and commits, with dense intra edges + sparse inter bridges so
# the spectral->log-spiral engine yields a believable galaxy.
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


def build_seed_graph() -> tuple[int, list[tuple[int, int]], list[dict]]:
    """Deterministic synthetic graph depicting mnemo. Returns
    ``(n, edges, meta)`` where ``meta[i] = {name, type, comm}``."""
    rng = random.Random(_SEED)
    meta: list[dict] = []
    edges: list[tuple[int, int]] = []
    comm_members: list[list[int]] = []

    for ci, (key, pkg, syms) in enumerate(_SUBSYSTEMS):
        members: list[int] = []
        mod_idx = len(meta)
        meta.append({"name": pkg, "type": "code_module", "comm": ci})
        members.append(mod_idx)
        # ~150 nodes/subsystem: cycle the symbol list with suffixes.
        per = 150
        for k in range(per):
            sym = syms[k % len(syms)]
            if (
                sym.endswith(".js")
                or sym.endswith(".html")
                or sym[0].islower()
                and "." not in sym
                and sym.islower()
                and "_" in sym
            ):
                typ = "code_function"
            elif sym[0].isupper():
                typ = "code_class" if k % 5 == 0 else "code_method"
            elif sym.endswith(".js") or sym.endswith(".html"):
                typ = "code_module"
            elif "route" in sym or "endpoint" in sym or sym.startswith("tier3"):
                typ = "code_route" if k % 2 else "code_endpoint"
            else:
                typ = "code_function"
            name = sym if k < len(syms) else f"{sym}_{k // len(syms)}"
            i = len(meta)
            meta.append({"name": f"{pkg}.{name}", "type": typ, "comm": ci})
            members.append(i)
            # dense intra-community structure (defines / calls)
            edges.append((mod_idx, i))
            for _ in range(3):
                j = rng.choice(members)
                if j != i:
                    edges.append((i, j))
        # memory nodes attached to this subsystem
        for mname, mtype in _MEM.get(key, []):
            i = len(meta)
            meta.append({"name": mname, "type": mtype, "comm": ci})
            members.append(i)
            edges.append((rng.choice(members[:per]), i))  # applies_to
        # commit nodes touching this subsystem
        for _ in range(6):
            i = len(meta)
            sha = f"{rng.randrange(16**7):07x}"
            meta.append(
                {
                    "name": f"{sha} feat({key}): " + rng.choice(syms),
                    "type": "commit",
                    "comm": ci,
                }
            )
            members.append(i)
            edges.append((i, rng.choice(members[:per])))  # touched_by
        comm_members.append(members)

    # sparse inter-community bridges (imports / cross-refs)
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


# --- bake the layout via the REAL engine ----------------------------
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
    pos = compute_graph_layout(n, edges)  # real, deterministic, cached
    nodes = []
    for i in range(n):
        d = deg[i]
        size = max(1.5, min(4.6, 1.3 + (d**0.5) * 0.78))
        nodes.append(
            {
                "x": float(pos[2 * i]),
                "y": float(pos[2 * i + 1]),
                "size": round(size, 3),
                "color": _hex_rgb(color_for(meta[i]["type"])),
                "name": meta[i]["name"],
                "type": meta[i]["type"],
                "deg": d,
            }
        )
    # de-dup edges, drop self-loops
    seen: set[tuple[int, int]] = set()
    out_e: list[dict] = []
    for s, t in edges:
        if s == t:
            continue
        k = (s, t) if s < t else (t, s)
        if k in seen:
            continue
        seen.add(k)
        out_e.append({"s": s, "t": t})
    return {"nodes": nodes, "edges": out_e}


# --- truthful feature-card snippets from the SAME seed ---------------
def _snippets() -> dict:
    _, _, meta = build_seed_graph()
    by_t: dict[str, list[str]] = {}
    for idx, m in enumerate(meta):
        by_t.setdefault(m["type"], []).append(f"{idx}:{m['name']}")
    fb = by_t["memory_feedback"][0].split(":", 1)
    pj = by_t["memory_project"][0].split(":", 1)
    fn = [x.split(":", 1) for x in by_t["code_function"][:2]]
    rag = (
        "&gt; how do we keep the Nebula from re-rendering when idle?\n\n"
        f"[mnemo:{fb[0]}] {fb[1]} (memory_feedback)\n"
        "  one scheduler; frame() re-arms only while animating, else idles.\n"
        f"[mnemo:{pj[0]}] {pj[1]} (memory_project)\n"
        "  server-laid layout is cached; the browser is a pure renderer.\n"
        "-- 2 hits, 142 tokens (budget 800)"
    )
    code = (
        f"trace-call {fn[0][1]}\n"
        f"  {fn[0][1]}  ->  {fn[1][1]}  (calls, 0.95)\n"
        f"  {fn[1][1]}  ->  mnemo.store.Store.query  (cross-file, 0.80)"
    )
    chat = (
        "you: which nodes are related to the layout engine?\n"
        "Mnem: pulling the session subgraph + highlighting it on the "
        "live Nebula graph... (sample exchange)"
    )
    return {"RAG": rag, "CODE": code, "CHAT": chat}


def render_index() -> str:
    tmpl = TMPL.read_text(encoding="utf-8")
    s = _snippets()
    repl = {
        "%%VERSION%%": _version(),
        "%%REPO_URL%%": REPO_URL,
        "%%PAGES_URL%%": PAGES_URL,
        "%%RAG_EXAMPLE%%": s["RAG"],
        "%%CODE_EXAMPLE%%": s["CODE"],
        "%%CHAT_EXAMPLE%%": s["CHAT"],
    }
    for k, v in repl.items():
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
    shutil.copyfile(BRAIN, out / "brain.svg")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the mnemo Pages demo")
    ap.add_argument("--out", default="dist", help="output directory")
    args = ap.parse_args()
    assemble(Path(args.out))
    print(f"demo built -> {args.out}")


if __name__ == "__main__":
    main()

"""Agentic task-success harness (v6.0.0) -- the moat-reliability instrument.

mnemo's reliability bar is NOT single-shot ``hit@k`` (snippet retrieval, the
commodity axis where an agent's own grep+read loop wins). It is **agentic
task-success**: given mnemo's tools, can the correct answer to a graph or
memory question be reached in <= N tool calls? That measures the moat grep
cannot touch -- the typed graph (blast radius, provenance) and cross-session
memory -- which ``hit@k`` never tested.

Three question classes, three organs:

- ``structural``     -- reverse ``calls`` edges (the GRAPH)
- ``provenance``     -- code -> commit -> memory walk (the GRAPH)
- ``memory_recall``  -- retrieval over memory nodes (RETRIEVAL; where hit@k
  lives on, now one class of three rather than the headline)

This module is the dependency-free CORE + scoring, mirroring
``eval_retrieval``: the caller owns execution via a ``solve_fn`` callback, so
the v6.0.0 deterministic oracle solver and a future LLM-agent solver drop into
the same seam. Store-backed pieces (``resolve_key`` / ``generate_tasks`` /
``oracle_solve``) live below the pure core.

Design: docs/plans/2026-06-11-mnemo-v6.0.0-task-success-harness-design.md
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from mnemo.eval_retrieval import _norm

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from mnemo.store import Node, Store

TASK_CLASSES: tuple[str, ...] = ("structural", "provenance", "memory_recall")


@dataclass
class Task:
    """One graph/memory question with a KNOWN-correct answer.

    ``answer_keys`` is the ground truth -- stable code keys
    (``<repo-rel file>::<qualified>``) or memory node names -- established
    INDEPENDENTLY of the tool being scored (by reading the source / commit /
    memory during curation), so a recall miss is a real moat gap, not a
    tautology. ``subject_key`` is the node a graph walk starts from.
    """

    id: str
    cls: str
    prompt: str
    answer_keys: list[str]
    subject_key: str | None = None
    budget: int = 2
    note: str = ""


@dataclass
class TaskResult:
    task: Task
    found_keys: list[str]
    calls_used: int
    recall: float
    precision: float
    f1: float
    success: bool


# --- pure scoring + aggregation (no store, no model) ----------------------


def load_task_set(path: str | Path) -> list[Task]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    tasks: list[Task] = []
    for r in raw:
        cls = str(r["cls"])
        if cls not in TASK_CLASSES:
            raise ValueError(f"unknown task class {cls!r}; expected one of {TASK_CLASSES}")
        tasks.append(
            Task(
                id=str(r["id"]),
                cls=cls,
                prompt=str(r.get("prompt", "")),
                answer_keys=[str(s) for s in r["answer_keys"]],
                subject_key=r.get("subject_key"),
                budget=int(r.get("budget", 2)),
                note=str(r.get("note", "")),
            )
        )
    return tasks


def _match(found_keys: list[str], answer_keys: list[str]) -> tuple[int, int]:
    """Return (matched_answers, hit_found_keys).

    An answer is matched when its normalized key occurs in some found key
    (answers are repo-relative; found keys may be absolute). A found key is a
    "hit" when it contains some answer -- used for precision.
    """
    nf = [_norm(f) for f in found_keys if f]
    na = [_norm(a) for a in answer_keys if a]
    matched_answers = sum(1 for a in na if any(a in f for f in nf))
    hit_found = sum(1 for f in nf if any(a in f for a in na))
    return matched_answers, hit_found


def score_task(found_keys: list[str], task: Task, *, calls_used: int) -> TaskResult:
    matched, hit_found = _match(found_keys, task.answer_keys)
    n_ans = len([a for a in task.answer_keys if a])
    n_found = len([f for f in found_keys if f])
    recall = matched / n_ans if n_ans else 0.0
    precision = hit_found / n_found if n_found else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    success = recall >= 1.0 and calls_used <= task.budget
    return TaskResult(
        task=task,
        found_keys=list(found_keys),
        calls_used=calls_used,
        recall=recall,
        precision=precision,
        f1=f1,
        success=success,
    )


SolveFn = Callable[[Task], "tuple[list[str], int]"]


def run_tasks(tasks: list[Task], *, solve_fn: SolveFn) -> list[TaskResult]:
    """Score every task through ``solve_fn`` (task -> (found_keys, calls)).

    The caller owns retrieval/traversal (live daemon, in-process store, a
    fake, or -- later -- an LLM agent loop), keeping the harness itself
    dependency-free. A solver that raises is scored as a clean miss.
    """
    results: list[TaskResult] = []
    for t in tasks:
        try:
            found, calls = solve_fn(t)
        except Exception:
            found, calls = [], 0
        results.append(score_task(found, t, calls_used=calls))
    return results


def _agg_block(rs: list[TaskResult]) -> dict:
    n = len(rs)
    if n == 0:
        return {
            "n": 0,
            "success_rate": 0.0,
            "avg_calls": 0.0,
            "avg_recall": 0.0,
            "avg_precision": 0.0,
        }
    return {
        "n": n,
        "success_rate": sum(1 for r in rs if r.success) / n,
        "avg_calls": sum(r.calls_used for r in rs) / n,
        "avg_recall": sum(r.recall for r in rs) / n,
        "avg_precision": sum(r.precision for r in rs) / n,
    }


def aggregate_tasks(results: list[TaskResult]) -> dict:
    by_class = {cls: _agg_block([r for r in results if r.task.cls == cls]) for cls in TASK_CLASSES}
    return {"by_class": by_class, "overall": _agg_block(list(results))}


def format_task_report(results: list[TaskResult], agg: dict, corpus: dict | None = None) -> str:
    lines = ["mnemo task-success eval (moat reliability)"]
    if corpus:
        lines.append(
            f"corpus: {corpus.get('node_count', '?')} nodes  fp={corpus.get('fingerprint', '?')}"
        )
    lines.append("")
    for cls in TASK_CLASSES:
        b = agg["by_class"][cls]
        lines.append(
            f"  [{cls}] n={b['n']}  success={b['success_rate']:.2f}  "
            f"avg_calls={b['avg_calls']:.1f}  recall={b['avg_recall']:.2f}  "
            f"precision={b['avg_precision']:.2f}"
        )
    lines.append("")
    for r in results:
        mark = "[ok]" if r.success else "[--]"
        lines.append(
            f"  {mark} {r.task.cls}/{r.task.id}  recall={r.recall:.2f} "
            f"calls={r.calls_used}  {r.task.prompt}"
        )
        if not r.success:
            for k in r.found_keys[:3]:
                lines.append(f"        got: {k}")
    lines.append("")
    o = agg["overall"]
    lines.append(
        f"overall  n={o['n']}  success={o['success_rate']:.2f}  avg_calls={o['avg_calls']:.1f}"
    )
    return "\n".join(lines)


# --- store-backed: resolver, generator, oracle solver ---------------------

_GRAPH_DECL_TYPES = ("code_function", "code_method", "code_class")


def _repo_rel(source_path: str) -> str:
    """A stable, machine-independent comparison key for a node.

    Code nodes live under ``.../daemon/...`` -> return from ``daemon/`` on
    (e.g. ``daemon/mnemo/store.py::rekey_node``). Commits -> the short sha
    after ``@``. Memory/doc nodes -> the filename stem. Every form is a
    guaranteed substring of the node's real ``source_path`` so the scorer's
    containment match holds.
    """
    s = (source_path or "").replace("\\", "/")
    i = s.find("/daemon/")
    if i >= 0:
        return s[i + 1 :]
    if "@" in s and "::" not in s:  # a commit node "<repo>@<sha>"
        return s.rsplit("@", 1)[-1][:13]
    base = s.rsplit("/", 1)[-1]
    return base[:-3] if base.endswith(".md") else base


def resolve_key(store: Store, key: str) -> Node | None:
    """Find the node whose normalized ``source_path`` ends with ``key`` at a
    path boundary (so ``store.py::rekey_node`` does not match
    ``store.py::rekey_node_helper`` and ``ekey_node`` matches nothing).
    On ambiguity, the shortest (most specific) source_path wins."""
    if not key:
        return None
    nkey = _norm(key)
    best: Node | None = None
    for n in store.list_nodes(limit=10**9):
        nsp = _norm(n.source_path or "")
        at_boundary = nsp == nkey or (nsp.endswith(nkey) and nsp[: -len(nkey)].endswith("/"))
        if at_boundary and (best is None or len(n.source_path or "") < len(best.source_path or "")):
            best = n
    return best


def _solve_structural(store: Store, task: Task) -> tuple[list[str], int]:
    subj = resolve_key(store, task.subject_key or "")
    if subj is None:
        return ([], 1)
    edges = store.get_edges(dst_id=subj.id, relation="calls")
    caller_nodes = store.get_nodes_by_ids([e.src_id for e in edges])
    return ([n.source_path or "" for n in caller_nodes.values()], 1)


def _solve_provenance(store: Store, task: Task) -> tuple[list[str], int]:
    subj = resolve_key(store, task.subject_key or "")
    if subj is None:
        return ([], 1)
    found_ids: set[str] = set()
    # commits that reference this code (references_function: commit -> func)
    commits = [e.src_id for e in store.get_edges(dst_id=subj.id, relation="references_function")]
    for cid in commits:
        found_ids.add(cid)
        # the memory that motivated the commit (commit -> memory) ...
        for e in store.get_edges(src_id=cid, relation="motivated_by"):
            found_ids.add(e.dst_id)
        # ... or the memory the commit closed (memory -> commit)
        for e in store.get_edges(dst_id=cid, relation="closed_by"):
            found_ids.add(e.src_id)
    nodes = store.get_nodes_by_ids(list(found_ids))
    return ([n.source_path or "" for n in nodes.values()], 1)


def _solve_memory_recall(store: Store, task: Task, *, embedder, k: int) -> tuple[list[str], int]:
    from mnemo import retrieve

    if embedder is None:
        from mnemo.embed import Embedder

        embedder = Embedder()
    res = retrieve.query(store, embedder, task.prompt, k=k, budget_tokens=800)
    return ([h.source_path or "" for h in res.hits], 1)


def oracle_solve(
    store: Store, task: Task, *, embedder: object | None = None, k: int = 5
) -> tuple[list[str], int]:
    """The deterministic canonical tool path for a task's class. Returns
    ``(found_source_paths, agent_facing_call_count)``. The subject is
    resolved by its stable key (setup, not a search call), so each oracle
    path is a single agent-facing tool call: get_edges / traverse / query."""
    if task.cls == "structural":
        return _solve_structural(store, task)
    if task.cls == "provenance":
        return _solve_provenance(store, task)
    if task.cls == "memory_recall":
        return _solve_memory_recall(store, task, embedder=embedder, k=k)
    return ([], 0)


# --- hybrid candidate generator (proposes; a human pins the fixture) -------


def _gen_structural(store: Store, per_class: int) -> list[Task]:
    from collections import defaultdict

    callers: dict[str, list[str]] = defaultdict(list)
    for e in store.get_edges(relation="calls"):
        callers[e.dst_id].append(e.src_id)
    out: list[Task] = []
    for callee_id in sorted(callers):
        uniq = sorted(set(callers[callee_id]))
        if not (1 <= len(uniq) <= 4):  # checkable, non-trivial caller set
            continue
        callee = store.get_node(callee_id)
        if callee is None or callee.type not in _GRAPH_DECL_TYPES:
            continue
        subj_key = _repo_rel(callee.source_path or "")
        caller_nodes = store.get_nodes_by_ids(uniq)
        answer = sorted({_repo_rel(n.source_path or "") for n in caller_nodes.values()})
        if not subj_key or not answer:
            continue
        out.append(
            Task(
                id=f"struct-{callee.name}",
                cls="structural",
                prompt=f"What calls {subj_key}?",
                subject_key=subj_key,
                answer_keys=answer,
                budget=1,
                note=f"reverse calls edges into {callee.name}",
            )
        )
        if len(out) >= per_class:
            break
    return out


def _gen_provenance(store: Store, per_class: int) -> list[Task]:
    from collections import defaultdict

    commit_to_mem: dict[str, set[str]] = defaultdict(set)
    for e in store.get_edges(relation="motivated_by"):  # commit -> memory
        commit_to_mem[e.src_id].add(e.dst_id)
    for e in store.get_edges(relation="closed_by"):  # memory -> commit
        commit_to_mem[e.dst_id].add(e.src_id)
    func_to_commits: dict[str, set[str]] = defaultdict(set)
    for e in store.get_edges(relation="references_function"):  # commit -> func
        func_to_commits[e.dst_id].add(e.src_id)
    out: list[Task] = []
    for func_id in sorted(func_to_commits):
        mem_commits = [c for c in func_to_commits[func_id] if commit_to_mem.get(c)]
        if not mem_commits:
            continue
        func = store.get_node(func_id)
        if func is None or func.type not in _GRAPH_DECL_TYPES:
            continue
        answer_ids: set[str] = set()
        for c in mem_commits:
            answer_ids.add(c)
            answer_ids |= commit_to_mem[c]
        answer_nodes = store.get_nodes_by_ids(list(answer_ids))
        answer = sorted({_repo_rel(n.source_path or "") for n in answer_nodes.values()})
        subj_key = _repo_rel(func.source_path or "")
        if not answer or not subj_key:
            continue
        out.append(
            Task(
                id=f"prov-{func.name}",
                cls="provenance",
                prompt=f"Why does {subj_key} exist -- which commit and memory explain it?",
                subject_key=subj_key,
                answer_keys=answer,
                budget=1,
                note="reverse references_function -> commit -> motivating/closing memory",
            )
        )
        if len(out) >= per_class:
            break
    return out


def _gen_memory_recall(store: Store, per_class: int) -> list[Task]:
    out: list[Task] = []
    nodes: list[Node] = []
    for t in ("memory_project", "memory_reference", "session_summary"):
        nodes += store.list_nodes(type=t, limit=10**6)
    for n in sorted(nodes, key=lambda x: x.id):
        desc = (n.description or "").strip()
        if not desc:
            continue
        out.append(
            Task(
                id=f"mem-{n.name}",
                cls="memory_recall",
                prompt=desc.split(".")[0][:120],  # curator rewrites into a real question
                subject_key=None,
                answer_keys=[_repo_rel(n.source_path or "")],
                budget=1,
                note=f"recall memory node {n.name}",
            )
        )
        if len(out) >= per_class:
            break
    return out


def generate_tasks(store: Store, *, per_class: int = 10) -> list[Task]:
    """Mechanically derive candidate tasks from the graph (deterministic,
    sorted by node id). These are CANDIDATES: a human curates + independently
    verifies the best ~20-30 into the committed fixture (the hybrid policy)."""
    return (
        _gen_structural(store, per_class)
        + _gen_provenance(store, per_class)
        + _gen_memory_recall(store, per_class)
    )

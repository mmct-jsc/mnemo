"""Retrieval precision harness (v5.26.0, workstream C part 1).

"No tuning without a baseline": a small labelled query set + a pure scorer
so every retrieval change (auto-scoping now, BM25/RRF in v5.27.0) is judged
by hit@k / MRR numbers instead of vibes.

Two usage modes share these helpers:

- UNIT mode (tests/unit/test_retrieval_eval.py): a seeded synthetic corpus
  + FakeEmbedder -- structural assertions, runs everywhere, no model.
- LIVE mode (``mnemo eval``): the SELF set in
  ``tests/fixtures/retrieval_eval.json`` -- real "where/how is X in mnemo"
  questions with known answering files -- against the live store + real
  embedder. A report instrument, not a CI gate.

Scoring: a hit MATCHES an entry when any ``expect_source_contains``
substring occurs in the hit's source_path (case-insensitive, ``\\``
normalized to ``/``, the code-node ``:start-end`` suffix ignored).
``rank`` is the 1-based position of the first matching hit; ``rr`` is the
reciprocal rank; aggregates are hit@1 / hit@5 / MRR over the set.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from mnemo.paths import _strip_line_range


@dataclass
class EvalEntry:
    prompt: str
    expect_source_contains: list[str]
    project_key: str | None = None
    note: str = ""


@dataclass
class EvalRow:
    """One scored entry (kept dict-compatible for easy printing)."""

    entry: EvalEntry
    rank: int | None
    matched: str | None = None
    top: list[str] = field(default_factory=list)


def load_eval_set(path: Path) -> list[EvalEntry]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        EvalEntry(
            prompt=str(r["prompt"]),
            expect_source_contains=[str(s) for s in r["expect_source_contains"]],
            project_key=r.get("project_key"),
            note=str(r.get("note", "")),
        )
        for r in raw
    ]


def _norm(p: str) -> str:
    return _strip_line_range((p or "").replace("\\", "/")).lower()


def score_hits(hit_source_paths: list[str], expect: list[str], *, k: int = 5) -> dict:
    """Score one entry's ranked hits against its expectations."""
    rank: int | None = None
    for i, sp in enumerate(hit_source_paths, start=1):
        nsp = _norm(sp)
        if any(e.lower() in nsp for e in expect):
            rank = i
            break
    return {
        "rank": rank,
        "hit_at_1": rank == 1,
        f"hit_at_{k}": rank is not None and rank <= k,
        "hit_at_5": rank is not None and rank <= 5,
        "rr": (1.0 / rank) if rank else 0.0,
    }


def aggregate(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    n = len(rows)
    if n == 0:
        return {"n": 0, "hit_at_1": 0.0, "hit_at_5": 0.0, "mrr": 0.0}
    return {
        "n": n,
        "hit_at_1": sum(1 for r in rows if r.get("hit_at_1")) / n,
        "hit_at_5": sum(1 for r in rows if r.get("hit_at_5")) / n,
        "mrr": sum(float(r.get("rr", 0.0)) for r in rows) / n,
    }


def run_entries(
    entries: list[EvalEntry],
    *,
    query_fn: Callable[[EvalEntry], list[str]],
    k: int = 5,
) -> list[dict]:
    """Run every entry through ``query_fn`` (entry -> ranked source_paths)
    and score it. The caller owns retrieval (live daemon, in-process store,
    or a fake) so the harness stays dependency-free."""
    rows: list[dict] = []
    for e in entries:
        try:
            paths = query_fn(e)
        except Exception:
            paths = []
        row = score_hits(paths, e.expect_source_contains, k=k)
        row["prompt"] = e.prompt
        row["top"] = paths[:k]
        rows.append(row)
    return rows


def format_report(rows: list[dict], agg: dict) -> str:
    lines = ["mnemo retrieval eval", ""]
    for r in rows:
        mark = "[hit]" if r.get("hit_at_5") else "[miss]"
        rank = r.get("rank")
        lines.append(f"  {mark} rank={rank if rank else '-'}  {r.get('prompt', '')}")
        if not r.get("hit_at_5"):
            for sp in r.get("top", [])[:3]:
                lines.append(f"         got: {sp}")
    lines.append("")
    lines.append(
        f"n={agg['n']}  hit@1={agg['hit_at_1']:.2f}  hit@5={agg['hit_at_5']:.2f}  mrr={agg['mrr']:.2f}"
    )
    return "\n".join(lines)

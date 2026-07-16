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

Query types (fusion rebalance): every entry is tagged ``lexical`` or
``conceptual`` and the report breaks hit@k down per type. The original
SELF set was lexically rich BY CONSTRUCTION -- "where is X", "how does Y
work", full of exact identifiers and filenames -- which structurally
favours BM25. A measured probe put BM25-alone at hit@5 0.81 against the
production 6-term sum's 0.62, but tuning on that set alone would overfit
to code-locating prompts and could silently regress SEMANTIC recall.
The per-type split makes that tradeoff visible, so a fusion change is
judged on both halves of the distribution instead of the flattering one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from mnemo.paths import _strip_line_range

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from mnemo.store import Store


#: The two halves of the query distribution we measure separately.
#: ``lexical`` -- the answer is a literal token match (an identifier or a
#: filename appears in the prompt); BM25 is strong here.
#: ``conceptual`` -- the answer is NOT a literal token match ("why is it
#: built this way"); the vector ranker earns its keep here.
QUERY_TYPES = ("lexical", "conceptual")


def _norm_query_type(v: object) -> str:
    """Normalize a tag, falling back to ``lexical``.

    Fails open on purpose: an unknown tag must never break the report.
    The shipped set is guarded by a fixture test that rejects a missing
    or invalid tag, so coercion here can't silently hide an authoring bug.
    """
    t = str(v or "").strip().lower()
    return t if t in QUERY_TYPES else "lexical"


@dataclass
class EvalEntry:
    prompt: str
    expect_source_contains: list[str]
    project_key: str | None = None
    note: str = ""
    query_type: str = "lexical"


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
            query_type=_norm_query_type(r.get("query_type")),
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


def aggregate_by_type(rows: Iterable[dict]) -> dict[str, dict]:
    """Aggregate separately per ``query_type``.

    The headline number hides the tradeoff a fusion change makes: leaning
    on BM25 lifts lexical queries while potentially sinking conceptual
    ones, and a single blended average can stay flat through both. Reading
    the two halves side by side is what turns "the score moved" into "we
    know what we traded".
    """
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        buckets.setdefault(_norm_query_type(r.get("query_type")), []).append(r)
    order = [t for t in QUERY_TYPES if t in buckets]
    order += sorted(t for t in buckets if t not in QUERY_TYPES)
    return {t: aggregate(buckets[t]) for t in order}


def corpus_snapshot(store: Store) -> dict:
    """A comparable fingerprint of the corpus so two eval runs are
    apples-to-apples (v5.28.0).

    The n=14 set was noisy because the live corpus drifts between runs
    (every memory/doc edit triggers the reindex hook). Recording the
    node count + a sha1 over sorted ``(id, hash)`` pairs lets a reader
    tell whether two reports were measured against the same corpus -- the
    fingerprint changes whenever any node is added, removed, or edited.
    """
    nodes = store.list_nodes(limit=10**9)
    h = hashlib.sha1()
    for nid, nhash in sorted((n.id, n.hash or "") for n in nodes):
        h.update(nid.encode("utf-8"))
        h.update(b"\x00")
        h.update(nhash.encode("utf-8"))
        h.update(b"\n")
    return {"node_count": len(nodes), "fingerprint": h.hexdigest()[:12]}


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
        row["query_type"] = e.query_type
        row["top"] = paths[:k]
        rows.append(row)
    return rows


def format_report(rows: list[dict], agg: dict, corpus: dict | None = None) -> str:
    header = ["mnemo retrieval eval"]
    if corpus:
        header.append(
            f"corpus: {corpus.get('node_count', '?')} nodes  fp={corpus.get('fingerprint', '?')}"
        )
    lines = [*header, ""]
    for r in rows:
        mark = "[hit]" if r.get("hit_at_5") else "[miss]"
        rank = r.get("rank")
        qt = _norm_query_type(r.get("query_type"))
        lines.append(f"  {mark} [{qt[:3]}] rank={rank if rank else '-'}  {r.get('prompt', '')}")
        if not r.get("hit_at_5"):
            for sp in r.get("top", [])[:3]:
                lines.append(f"                got: {sp}")
    lines.append("")
    lines.append(_agg_line("overall", agg))
    # Per-type: the lexical-vs-semantic tradeoff any fusion change makes.
    by_type = aggregate_by_type(rows)
    if len(by_type) > 1:
        for qt, a in by_type.items():
            lines.append(_agg_line(qt, a))
    return "\n".join(lines)


def _agg_line(label: str, agg: dict) -> str:
    return (
        f"{label:<12} n={agg['n']:<3} hit@1={agg['hit_at_1']:.2f}  "
        f"hit@5={agg['hit_at_5']:.2f}  mrr={agg['mrr']:.2f}"
    )

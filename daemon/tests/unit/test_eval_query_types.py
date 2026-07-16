"""Fusion rebalance step 1: query-type tagging + per-type aggregation.

The SELF set is lexically rich BY CONSTRUCTION ("where is X", "how does Y
work" -- full of exact identifiers and filenames), so it structurally
favours BM25. The measured fusion probe (see
docs/plans/2026-06-18-mnemo-retrieval-fusion-rebalance-design.md) found
BM25-alone at hit@5 0.81 vs the production 6-term sum at 0.62 -- but
acting on that evidence alone would overfit to code-locating prompts and
could silently regress SEMANTIC recall.

Tagging every entry ``lexical|conceptual`` and breaking hit@k down per
type is the instrument that makes that tradeoff VISIBLE. It is the
prerequisite for any fusion change, not an optional extra.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo import eval_retrieval as ev

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "retrieval_eval.json"


# --- schema: EvalEntry carries a query_type --------------------------------


def test_eval_entry_defaults_to_lexical() -> None:
    """Untagged entries stay lexical -- the pre-existing set's character."""
    e = ev.EvalEntry(prompt="p", expect_source_contains=["x.py"])
    assert e.query_type == "lexical"


def test_load_eval_set_parses_query_type(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    p.write_text(
        json.dumps(
            [
                {"prompt": "a", "expect_source_contains": ["a.py"], "query_type": "conceptual"},
                {"prompt": "b", "expect_source_contains": ["b.py"], "query_type": "lexical"},
                {"prompt": "c", "expect_source_contains": ["c.py"]},
            ]
        ),
        encoding="utf-8",
    )
    entries = ev.load_eval_set(p)
    assert [e.query_type for e in entries] == ["conceptual", "lexical", "lexical"]


def test_load_eval_set_coerces_unknown_query_type(tmp_path: Path) -> None:
    """An unknown tag must not crash the instrument; fall back to lexical."""
    p = tmp_path / "s.json"
    p.write_text(
        json.dumps([{"prompt": "a", "expect_source_contains": ["a.py"], "query_type": "WEIRD"}]),
        encoding="utf-8",
    )
    assert ev.load_eval_set(p)[0].query_type == "lexical"


def test_load_eval_set_normalizes_case_and_space(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    p.write_text(
        json.dumps(
            [{"prompt": "a", "expect_source_contains": ["a.py"], "query_type": " Conceptual "}]
        ),
        encoding="utf-8",
    )
    assert ev.load_eval_set(p)[0].query_type == "conceptual"


# --- rows carry the type through the runner --------------------------------


def test_run_entries_carries_query_type_into_row() -> None:
    rows = ev.run_entries(
        [
            ev.EvalEntry(prompt="a", expect_source_contains=["a.py"], query_type="conceptual"),
            ev.EvalEntry(prompt="b", expect_source_contains=["b.py"]),
        ],
        query_fn=lambda e: ["/r/a.py"],
    )
    assert [r["query_type"] for r in rows] == ["conceptual", "lexical"]


# --- per-type aggregation ---------------------------------------------------


def test_aggregate_by_type_splits_rows() -> None:
    rows = [
        {"query_type": "lexical", "rank": 1, "hit_at_1": True, "hit_at_5": True, "rr": 1.0},
        {"query_type": "lexical", "rank": 2, "hit_at_1": False, "hit_at_5": True, "rr": 0.5},
        {"query_type": "conceptual", "rank": None, "hit_at_1": False, "hit_at_5": False, "rr": 0.0},
    ]
    by = ev.aggregate_by_type(rows)
    assert set(by) == {"lexical", "conceptual"}
    assert by["lexical"]["n"] == 2
    assert by["lexical"]["hit_at_5"] == pytest.approx(1.0)
    assert by["conceptual"]["n"] == 1
    assert by["conceptual"]["hit_at_5"] == pytest.approx(0.0)


def test_aggregate_by_type_untagged_rows_count_as_lexical() -> None:
    by = ev.aggregate_by_type([{"rank": 1, "hit_at_1": True, "hit_at_5": True, "rr": 1.0}])
    assert by["lexical"]["n"] == 1


def test_aggregate_by_type_empty() -> None:
    assert ev.aggregate_by_type([]) == {}


# --- the report shows the tradeoff (the DoD) -------------------------------


def test_format_report_breaks_down_by_query_type() -> None:
    rows = ev.run_entries(
        [
            ev.EvalEntry(prompt="lex", expect_source_contains=["a.py"], query_type="lexical"),
            ev.EvalEntry(prompt="con", expect_source_contains=["zz.py"], query_type="conceptual"),
        ],
        query_fn=lambda e: ["/r/a.py"],
    )
    out = ev.format_report(rows, ev.aggregate(rows))
    assert "lexical" in out, "the per-type breakdown is the whole point of the tagging"
    assert "conceptual" in out
    # The lexical entry hits, the conceptual one misses -- the split must show it.
    assert "hit@5=1.00" in out
    assert "hit@5=0.00" in out


def test_format_report_single_type_still_reports() -> None:
    rows = ev.run_entries(
        [ev.EvalEntry(prompt="p", expect_source_contains=["a.py"])],
        query_fn=lambda e: ["/r/a.py"],
    )
    out = ev.format_report(rows, ev.aggregate(rows))
    assert "n=1" in out


# --- the shipped SELF set ---------------------------------------------------


def test_shipped_fixture_every_entry_is_explicitly_tagged() -> None:
    """No silent defaults in the shipped set: a missing tag is an authoring
    bug that would quietly mis-bucket a query and skew the tradeoff."""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    untagged = [r["prompt"] for r in raw if "query_type" not in r]
    assert not untagged, f"entries missing an explicit query_type: {untagged}"
    bad = [r["prompt"] for r in raw if r.get("query_type") not in ev.QUERY_TYPES]
    assert not bad, f"entries with an invalid query_type: {bad}"


def test_shipped_fixture_has_enough_conceptual_queries() -> None:
    """Per the fusion plan: >=15 conceptual queries before any fusion change,
    so a BM25-led mode cannot be adopted on lexical evidence alone."""
    entries = ev.load_eval_set(FIXTURE)
    conceptual = [e for e in entries if e.query_type == "conceptual"]
    assert len(conceptual) >= 15, f"expected >=15 conceptual queries, got {len(conceptual)}"


def test_shipped_fixture_keeps_its_lexical_baseline() -> None:
    """The original 42 lexical queries stay -- expansion, not replacement,
    so the pre-change baseline remains comparable."""
    entries = ev.load_eval_set(FIXTURE)
    lexical = [e for e in entries if e.query_type == "lexical"]
    assert len(lexical) >= 40, f"expected the >=40 lexical baseline intact, got {len(lexical)}"

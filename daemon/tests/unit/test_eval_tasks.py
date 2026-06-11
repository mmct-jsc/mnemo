"""v6.0.0: agentic task-success harness -- the pure (store-free) core.

Mirrors the ``eval_retrieval`` seam: the harness is dependency-free and the
caller owns execution via a ``solve_fn`` callback. These tests pin the
scoring contract (set-match + budget), the per-class aggregation, and the
report shape (LEADS with the moat classes, no ``hit@k`` headline). The
store-backed oracle + generator are covered in test_eval_tasks_oracle.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo import eval_tasks as et

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tasks_eval.json"


def _task(
    *,
    id: str = "t1",
    cls: str = "structural",
    answer_keys: list[str] | None = None,
    subject_key: str | None = "daemon/mnemo/store.py::rekey_node",
    budget: int = 2,
) -> et.Task:
    return et.Task(
        id=id,
        cls=cls,
        prompt="What calls rekey_node?",
        subject_key=subject_key,
        answer_keys=answer_keys
        if answer_keys is not None
        else ["ingest.py::_migrate_legacy_code_node"],
        budget=budget,
    )


def test_task_classes_are_the_three_moat_classes() -> None:
    assert et.TASK_CLASSES == ("structural", "provenance", "memory_recall")


def test_score_task_exact_match_is_success() -> None:
    task = _task(answer_keys=["ingest.py::_migrate_legacy_code_node"])
    # found uses the ABSOLUTE key; the answer is repo-relative -> must still match.
    found = ["D:/Repository/knowledge-base/daemon/mnemo/ingest.py::_migrate_legacy_code_node"]
    r = et.score_task(found, task, calls_used=1)
    assert r.recall == 1.0
    assert r.precision == 1.0
    assert r.f1 == 1.0
    assert r.success is True


def test_score_task_partial_recall_is_failure() -> None:
    task = _task(answer_keys=["a.py::one", "b.py::two"])
    r = et.score_task(["/repo/a.py::one"], task, calls_used=1)
    assert r.recall == 0.5
    assert r.success is False, "an incomplete answer set is not success"


def test_score_task_over_budget_is_failure_even_when_complete() -> None:
    task = _task(answer_keys=["a.py::one"], budget=1)
    r = et.score_task(["/repo/a.py::one"], task, calls_used=3)
    assert r.recall == 1.0
    assert r.success is False, "answer reached but it cost more calls than the budget"


def test_score_task_extra_noise_lowers_precision_but_still_success() -> None:
    task = _task(answer_keys=["a.py::one"], budget=2)
    r = et.score_task(["/repo/a.py::one", "/repo/z.py::junk"], task, calls_used=1)
    assert r.recall == 1.0
    assert r.precision == 0.5
    assert r.success is True, "recall-complete within budget is success; noise only dents precision"


def test_score_task_empty_found_is_clean_zero() -> None:
    task = _task(answer_keys=["a.py::one"])
    r = et.score_task([], task, calls_used=1)
    assert r.recall == 0.0
    assert r.precision == 0.0
    assert r.f1 == 0.0
    assert r.success is False


def test_run_tasks_uses_caller_owned_solve_fn() -> None:
    tasks = [
        _task(id="hit", answer_keys=["a.py::one"]),
        _task(id="miss", answer_keys=["b.py::two"]),
    ]

    def solve(task: et.Task) -> tuple[list[str], int]:
        # The harness must not retrieve anything itself; the solver owns it.
        return (["/repo/a.py::one"], 1) if task.id == "hit" else ([], 1)

    results = et.run_tasks(tasks, solve_fn=solve)
    by_id = {r.task.id: r for r in results}
    assert by_id["hit"].success is True
    assert by_id["miss"].success is False


def test_run_tasks_treats_solver_exception_as_a_miss() -> None:
    def boom(task: et.Task) -> tuple[list[str], int]:
        raise RuntimeError("daemon down")

    results = et.run_tasks([_task()], solve_fn=boom)
    assert results[0].success is False
    assert results[0].found_keys == []


def test_aggregate_tasks_reports_per_class() -> None:
    tasks = [
        _task(id="s1", cls="structural", answer_keys=["a::x"]),
        _task(id="s2", cls="structural", answer_keys=["b::y"]),
        _task(id="m1", cls="memory_recall", answer_keys=["note-z"]),
    ]

    def solve(task: et.Task) -> tuple[list[str], int]:
        wins = {"s1": ["/r/a::x"], "m1": ["/r/note-z.md"]}
        return (wins.get(task.id, []), 1)

    agg = et.aggregate_tasks(et.run_tasks(tasks, solve_fn=solve))
    assert agg["by_class"]["structural"]["n"] == 2
    assert agg["by_class"]["structural"]["success_rate"] == 0.5
    assert agg["by_class"]["memory_recall"]["success_rate"] == 1.0
    assert agg["overall"]["n"] == 3


def test_format_report_leads_with_moat_classes_not_hitk() -> None:
    tasks = [_task(id="s1", cls="structural", answer_keys=["a::x"])]
    results = et.run_tasks(tasks, solve_fn=lambda t: (["/r/a::x"], 1))
    agg = et.aggregate_tasks(results)
    out = et.format_task_report(results, agg, corpus={"node_count": 17964, "fingerprint": "abc123"})
    assert "structural" in out
    assert "provenance" in out
    assert "memory_recall" in out
    assert "hit@" not in out.lower(), "the moat report must not lead with the demoted hit@k metric"
    assert "17964" in out, "corpus snapshot pinned in the header for comparability"


def test_load_task_set_parses_fixture_shape(tmp_path: Path) -> None:
    p = tmp_path / "tasks.json"
    p.write_text(
        json.dumps(
            [
                {
                    "id": "struct-rekey-callers",
                    "cls": "structural",
                    "prompt": "What calls rekey_node?",
                    "subject_key": "daemon/mnemo/store.py::rekey_node",
                    "answer_keys": ["ingest.py::_migrate_legacy_code_node"],
                    "budget": 1,
                    "note": "v5.28 migration",
                }
            ]
        ),
        encoding="utf-8",
    )
    tasks = et.load_task_set(p)
    assert len(tasks) == 1
    assert tasks[0].cls == "structural"
    assert tasks[0].subject_key == "daemon/mnemo/store.py::rekey_node"
    assert tasks[0].budget == 1


def test_load_task_set_rejects_unknown_class(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text(
        json.dumps([{"id": "x", "cls": "nonsense", "prompt": "?", "answer_keys": ["a"]}]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="nonsense"):
        et.load_task_set(p)


def test_shipped_task_fixture_is_balanced_and_wellformed() -> None:
    tasks = et.load_task_set(FIXTURE)
    assert len(tasks) >= 20, "need a meaningful task-success baseline set"
    for cls in et.TASK_CLASSES:
        assert [t for t in tasks if t.cls == cls], f"fixture needs at least one {cls} task"
    for t in tasks:
        assert t.prompt.strip(), f"{t.id}: needs a question"
        assert t.answer_keys, f"{t.id}: needs a ground-truth answer set"
        if t.cls in ("structural", "provenance"):
            assert t.subject_key, f"{t.id}: a graph task needs a subject_key to walk from"


def test_eval_tasks_cli_prints_moat_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`mnemo eval-tasks` runs the SELF set and prints a per-class moat report
    (no hit@k headline). Hermetic: empty tmp store + a canned daemon hit."""
    from typer.testing import CliRunner

    from mnemo.cli import app

    monkeypatch.setenv("MNEMO_HOME", str(tmp_path / "home"))

    def fake_daemon_query(prompt: str, **kw: object) -> dict:
        return {"hits": [{"source_path": "x/reference_mnemo_index_scoring.md"}], "intent_tags": []}

    monkeypatch.setattr("mnemo.cli._daemon_query", fake_daemon_query)
    result = CliRunner().invoke(app, ["eval-tasks"])
    assert result.exit_code == 0, result.stdout
    for cls in et.TASK_CLASSES:
        assert cls in result.stdout, f"report must show the {cls} class"
    assert "overall" in result.stdout
    assert "hit@" not in result.stdout.lower(), "the moat report must not surface hit@k"

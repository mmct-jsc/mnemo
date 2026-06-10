"""v5.26.0 step 1: the retrieval precision harness (workstream C part 1).

Pure scorer math + fixture validity + a seeded-corpus harness run with the
FakeEmbedder. The LIVE instrument (`mnemo eval` against the real store +
embedder) reuses these helpers; no CI model download here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo import eval_retrieval as ev

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "retrieval_eval.json"


# --- scorer math ------------------------------------------------------------


def test_score_hits_first_position() -> None:
    row = ev.score_hits(["a/statusline.py:1-9", "b/cli.py"], ["statusline.py"], k=5)
    assert row["rank"] == 1
    assert row["hit_at_1"] is True
    assert row["hit_at_5"] is True
    assert row["rr"] == 1.0


def test_score_hits_second_position() -> None:
    row = ev.score_hits(["b/cli.py", "a/statusline.py"], ["statusline.py"], k=5)
    assert row["rank"] == 2
    assert row["hit_at_1"] is False
    assert row["hit_at_5"] is True
    assert row["rr"] == 0.5


def test_score_hits_miss() -> None:
    row = ev.score_hits(["x.py", "y.py"], ["statusline.py"], k=5)
    assert row["rank"] is None
    assert row["hit_at_5"] is False
    assert row["rr"] == 0.0


def test_score_hits_matches_any_expectation_case_insensitive() -> None:
    row = ev.score_hits(["D:\\Repo\\Daemon\\MNEMO\\Embed.py:10-20"], ["embed.py", "zzz"], k=5)
    assert row["rank"] == 1, "backslash paths + case + line-range suffix must all normalize"


def test_aggregate_math() -> None:
    rows = [
        {"rank": 1, "hit_at_1": True, "hit_at_5": True, "rr": 1.0},
        {"rank": 2, "hit_at_1": False, "hit_at_5": True, "rr": 0.5},
        {"rank": None, "hit_at_1": False, "hit_at_5": False, "rr": 0.0},
    ]
    agg = ev.aggregate(rows)
    assert agg["n"] == 3
    assert agg["hit_at_1"] == pytest.approx(1 / 3)
    assert agg["hit_at_5"] == pytest.approx(2 / 3)
    assert agg["mrr"] == pytest.approx(0.5)


# --- labelled set -----------------------------------------------------------


def test_eval_fixture_loads_and_is_well_formed() -> None:
    entries = ev.load_eval_set(FIXTURE)
    assert len(entries) >= 10, "need a meaningful baseline set"
    for e in entries:
        assert e.prompt.strip()
        assert e.expect_source_contains, f"{e.prompt!r}: needs expectations"


def test_eval_fixture_is_valid_json_with_notes() -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    assert all("prompt" in r and "expect_source_contains" in r for r in raw)


# --- mnemo eval CLI ----------------------------------------------------------


def test_eval_cli_prints_baseline_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """`mnemo eval` runs the SELF set (daemon-first) and prints hit@k/MRR."""
    from typer.testing import CliRunner

    from mnemo.cli import app

    def fake_daemon_query(prompt: str, **kw: object) -> dict:
        return {"hits": [{"source_path": "daemon/mnemo/statusline.py:1-9"}], "intent_tags": []}

    monkeypatch.setattr("mnemo.cli._daemon_query", fake_daemon_query)
    result = CliRunner().invoke(app, ["eval"])
    assert result.exit_code == 0, result.stdout
    assert "n=14" in result.stdout, "must run every SELF entry"
    assert "hit@5=" in result.stdout
    assert "mrr=" in result.stdout
    # the statusline entry is satisfied by the canned hit; misses are listed
    assert "[hit]" in result.stdout
    assert "[miss]" in result.stdout


# --- seeded harness run (FakeEmbedder; structural, no model) ----------------


def test_seeded_run_scores_scoped_retrieval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end harness plumbing on a tiny synthetic corpus: the runner
    queries the store + scores hits without any network or real model."""
    from mnemo import retrieve
    from mnemo.store import Node, Store
    from tests.conftest import FakeEmbedder

    monkeypatch.setenv("MNEMO_HOME", str(tmp_path / "home"))
    store = Store(tmp_path / "t.db")
    for name, project, path in [
        ("alpha_rotor", "P1", "/p1/src/alpha.py"),
        ("alpha_rotor", "P2", "/p2/src/alpha.py"),
        ("beta_flux", "P1", "/p1/src/beta.py"),
    ]:
        n = Node.new(
            type="code_function",
            name=name,
            description=f"{name} implementation",
            body=f"def {name}(): ...",
            source_path=path,
            source_kind="code_repo",
        )
        n.project_key = project
        store.upsert_node(n)
    emb = FakeEmbedder()
    for node in store.list_nodes():
        from mnemo.embed import embed_node

        embed_node(store, node, emb)

    entries = [
        ev.EvalEntry(
            prompt="alpha rotor implementation",
            expect_source_contains=["/p1/"],
            project_key="P1",
        )
    ]
    rows = ev.run_entries(
        entries,
        query_fn=lambda e: [
            h.source_path or ""
            for h in retrieve.query(
                store, emb, e.prompt, k=5, budget_tokens=400, active_project=e.project_key
            ).hits
        ],
    )
    agg = ev.aggregate(rows)
    assert agg["n"] == 1
    assert agg["hit_at_5"] == 1.0, "the P1 node must surface for a P1-scoped query"
    store.close()

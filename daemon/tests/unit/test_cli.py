"""CLI tests via Typer's CliRunner.

Most CLI commands open a Store from ``paths.db_path()`` and may load an
``Embedder``. We use the ``isolated_mnemo_home`` fixture to redirect
``MNEMO_HOME`` so each test runs in a sandboxed directory, and monkeypatch
``mnemo.cli.Embedder`` to the FakeEmbedder for the few tests that need
embedding.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mnemo.cli import app
from tests.conftest import FakeEmbedder


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# All CLI tests run sandboxed.
@pytest.fixture(autouse=True)
def _sandbox(isolated_mnemo_home: Path) -> Path:
    return isolated_mnemo_home


def _seed_memory(parent: Path) -> Path:
    src = parent / "memory"
    src.mkdir(parents=True, exist_ok=True)
    (src / "feedback_x.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: rule-x
            description: a rule
            type: feedback
            ---
            Body
            """
        ),
        encoding="utf-8",
    )
    return src


# --- top-level commands ---------------------------------------------------


def test_cli_status(runner: CliRunner) -> None:
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.stdout
    assert "version:" in result.stdout
    assert "nodes:" in result.stdout
    assert "daemon:" in result.stdout


def test_cli_source_list_empty(runner: CliRunner) -> None:
    result = runner.invoke(app, ["source", "list"])
    assert result.exit_code == 0
    assert result.stdout.strip() == ""


def test_cli_source_add_and_list(runner: CliRunner, tmp_path: Path) -> None:
    src = tmp_path / "mem"
    src.mkdir()
    add = runner.invoke(app, ["source", "add", str(src), "--kind", "memory_dir"])
    assert add.exit_code == 0
    listing = runner.invoke(app, ["source", "list", "--json"])
    assert listing.exit_code == 0
    data = json.loads(listing.stdout)
    assert len(data) == 1
    assert data[0]["path"] == str(src)


def test_cli_source_add_invalid_kind(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["source", "add", str(tmp_path), "--kind", "bogus"])
    assert result.exit_code != 0


# --- v2.0 phase 2: auto-router + --yes / --force flags --------------------


def test_cli_source_add_auto_routes_memory_dir(runner: CliRunner, tmp_path: Path) -> None:
    """Without --kind, the CLI runs the auto-router. Typed-frontmatter
    markdown -> memory_dir; --yes accepts the suggestion."""
    src = _seed_memory(tmp_path)
    result = runner.invoke(app, ["source", "add", str(src), "--yes"])
    assert result.exit_code == 0, result.stdout
    assert "memory_dir" in result.stdout
    listing = runner.invoke(app, ["source", "list", "--json"])
    data = json.loads(listing.stdout)
    assert len(data) == 1
    assert data[0]["kind"] == "memory_dir"


def test_cli_source_add_auto_routes_code_repo(runner: CliRunner, tmp_path: Path) -> None:
    """`.git/` + `.py` files -> code_repo; --yes accepts."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    result = runner.invoke(app, ["source", "add", str(tmp_path), "--yes"])
    assert result.exit_code == 0, result.stdout
    assert "code_repo" in result.stdout
    listing = runner.invoke(app, ["source", "list", "--json"])
    data = json.loads(listing.stdout)
    assert data[0]["kind"] == "code_repo"


def test_cli_source_add_auto_routes_docs_dir(runner: CliRunner, tmp_path: Path) -> None:
    """Two plain markdowns, no source files, no .git -> docs_dir."""
    (tmp_path / "intro.md").write_text("# Intro\n", encoding="utf-8")
    (tmp_path / "guide.md").write_text("# Guide\n", encoding="utf-8")
    result = runner.invoke(app, ["source", "add", str(tmp_path), "--yes"])
    assert result.exit_code == 0, result.stdout
    listing = runner.invoke(app, ["source", "list", "--json"])
    data = json.loads(listing.stdout)
    assert data[0]["kind"] == "docs_dir"


def test_cli_source_add_no_kind_no_match_exits_nonzero(runner: CliRunner, tmp_path: Path) -> None:
    """An empty directory has no auto-routable signal. The CLI MUST NOT
    write a row and MUST tell the user to pass --kind explicitly."""
    result = runner.invoke(app, ["source", "add", str(tmp_path), "--yes"])
    assert result.exit_code != 0
    assert "--kind" in result.stdout
    listing = runner.invoke(app, ["source", "list", "--json"])
    assert json.loads(listing.stdout) == []


def test_cli_source_add_prompt_declined_does_not_register(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Without --yes, the CLI prompts. Feeding 'n' on stdin declines."""
    src = _seed_memory(tmp_path)
    result = runner.invoke(app, ["source", "add", str(src)], input="n\n")
    # Exit code may be 0 (clean decline) or 1 (cancelled) -- either way,
    # the source row must not exist.
    listing = runner.invoke(app, ["source", "list", "--json"])
    assert json.loads(listing.stdout) == []
    # Cosmetic: declining should leave a "cancelled" hint somewhere.
    assert result.exit_code in (0, 1)


def test_cli_source_add_prompt_accepted_registers(runner: CliRunner, tmp_path: Path) -> None:
    """Feeding 'y' on stdin accepts the suggestion."""
    src = _seed_memory(tmp_path)
    result = runner.invoke(app, ["source", "add", str(src)], input="y\n")
    assert result.exit_code == 0, result.stdout
    listing = runner.invoke(app, ["source", "list", "--json"])
    data = json.loads(listing.stdout)
    assert data[0]["kind"] == "memory_dir"


def test_cli_source_add_kind_override_skips_prompt(runner: CliRunner, tmp_path: Path) -> None:
    """Explicit --kind is an unambiguous user command; no prompt."""
    (tmp_path / "anything.md").write_text("not a memory dir, but I said so\n", encoding="utf-8")
    result = runner.invoke(app, ["source", "add", str(tmp_path), "--kind", "docs_dir"])
    assert result.exit_code == 0
    listing = runner.invoke(app, ["source", "list", "--json"])
    data = json.loads(listing.stdout)
    assert data[0]["kind"] == "docs_dir"


def test_cli_source_add_ceiling_blocks_without_force(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path with too many source files refuses without --force."""
    from mnemo import auto_router

    monkeypatch.setattr(auto_router, "SAFETY_CEILING", 3)
    (tmp_path / ".git").mkdir()
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text("x = 1\n", encoding="utf-8")
    result = runner.invoke(app, ["source", "add", str(tmp_path), "--yes"])
    assert result.exit_code != 0
    assert "--force" in result.stdout or "ceiling" in result.stdout.lower()
    listing = runner.invoke(app, ["source", "list", "--json"])
    assert json.loads(listing.stdout) == []


def test_cli_source_add_force_overrides_ceiling(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--force lets the user register past the safety ceiling."""
    from mnemo import auto_router

    monkeypatch.setattr(auto_router, "SAFETY_CEILING", 3)
    (tmp_path / ".git").mkdir()
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text("x = 1\n", encoding="utf-8")
    result = runner.invoke(app, ["source", "add", str(tmp_path), "--yes", "--force"])
    assert result.exit_code == 0, result.stdout
    listing = runner.invoke(app, ["source", "list", "--json"])
    assert json.loads(listing.stdout)[0]["kind"] == "code_repo"


def test_cli_source_remove(runner: CliRunner, tmp_path: Path) -> None:
    src = tmp_path / "mem"
    src.mkdir()
    runner.invoke(app, ["source", "add", str(src), "--kind", "memory_dir"])
    rm = runner.invoke(app, ["source", "remove", str(src)])
    assert rm.exit_code == 0
    listing = runner.invoke(app, ["source", "list", "--json"])
    assert json.loads(listing.stdout) == []


def test_cli_source_remove_reports_cascade_count(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v1.1.1: source remove output mentions how many nodes were
    cleaned up so the user can verify the cascade fired."""
    monkeypatch.setattr("mnemo.cli.Embedder", lambda *a, **kw: FakeEmbedder())
    src = _seed_memory(tmp_path)
    runner.invoke(app, ["source", "add", str(src), "--kind", "memory_dir"])
    runner.invoke(app, ["reindex", "--no-embed"])
    rm = runner.invoke(app, ["source", "remove", str(src)])
    assert rm.exit_code == 0
    assert "1 node cleaned up" in rm.stdout


def test_cli_source_orphans_empty(runner: CliRunner) -> None:
    result = runner.invoke(app, ["source", "orphans"])
    assert result.exit_code == 0
    assert "No orphan nodes" in result.stdout


def test_cli_source_orphans_lists_then_prunes(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: simulate the pre-1.1.1 leak by directly deleting the
    sources row (so cascade doesn't fire), then verify ``orphans`` finds
    the leftover nodes and ``--prune`` cleans them up."""
    monkeypatch.setattr("mnemo.cli.Embedder", lambda *a, **kw: FakeEmbedder())
    src = _seed_memory(tmp_path)
    runner.invoke(app, ["source", "add", str(src), "--kind", "memory_dir"])
    runner.invoke(app, ["reindex", "--no-embed"])

    # Simulate the pre-1.1.1 broken behavior: drop the sources row WITHOUT
    # cascading. The nodes are now orphans.
    from mnemo import paths as paths_mod
    from mnemo.store import Store

    s = Store(paths_mod.db_path())
    try:
        s.conn.execute("DELETE FROM sources WHERE path = ?", (str(src),))
        s.conn.commit()
        assert len(s.list_sources()) == 0
        # The node still exists.
        assert len(s.list_nodes()) == 1
    finally:
        s.close()

    # `orphans` lists them.
    listed = runner.invoke(app, ["source", "orphans"])
    assert listed.exit_code == 0
    assert "Found 1 orphan node" in listed.stdout

    # `orphans --prune` removes them.
    pruned = runner.invoke(app, ["source", "orphans", "--prune"])
    assert pruned.exit_code == 0
    assert "Pruned 1 orphan node" in pruned.stdout

    # Re-running shows none.
    again = runner.invoke(app, ["source", "orphans"])
    assert "No orphan nodes" in again.stdout


def test_cli_source_orphans_json(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JSON output mode for scripts / adapters."""
    monkeypatch.setattr("mnemo.cli.Embedder", lambda *a, **kw: FakeEmbedder())
    src = _seed_memory(tmp_path)
    runner.invoke(app, ["source", "add", str(src), "--kind", "memory_dir"])
    runner.invoke(app, ["reindex", "--no-embed"])

    # Same leak simulation as above.
    from mnemo import paths as paths_mod
    from mnemo.store import Store

    s = Store(paths_mod.db_path())
    try:
        s.conn.execute("DELETE FROM sources WHERE path = ?", (str(src),))
        s.conn.commit()
    finally:
        s.close()

    result = runner.invoke(app, ["source", "orphans", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert len(data) == 1
    assert "source_path" in data[0]
    assert data[0]["source_path"].startswith(str(src))
    assert "type" in data[0]
    assert "name" in data[0]


def test_cli_reindex_no_embed(runner: CliRunner, tmp_path: Path) -> None:
    src = _seed_memory(tmp_path)
    runner.invoke(app, ["source", "add", str(src), "--kind", "memory_dir"])
    result = runner.invoke(app, ["reindex", "--no-embed"])
    assert result.exit_code == 0, result.stdout
    report = json.loads(result.stdout)
    assert report["added"] == 1


def test_cli_reindex_unknown_source(runner: CliRunner) -> None:
    result = runner.invoke(app, ["reindex", "--source", "/does/not/exist"])
    assert result.exit_code != 0


def test_cli_retune_below_threshold_prints_message(runner: CliRunner) -> None:
    """v1.2 phase 5: with no labeled queries in the store, retune
    refuses to optimize and prints a friendly explanation."""
    result = runner.invoke(app, ["retune", "--min-queries", "30"])
    assert result.exit_code == 0
    assert "below threshold" in result.stdout.lower()


def test_cli_retune_json_emits_report(runner: CliRunner) -> None:
    """--json mode produces a parseable RetuneReport dump even when
    the store is empty (helps scripts probe the threshold state)."""
    result = runner.invoke(app, ["retune", "--json", "--min-queries", "30"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "proposed" in data
    assert "current" in data
    assert "val_mrr_before" in data
    assert "iterations" in data
    assert "log" in data


def test_cli_query_json(runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mnemo.cli.Embedder", lambda *a, **kw: FakeEmbedder())
    src = _seed_memory(tmp_path)
    runner.invoke(app, ["source", "add", str(src), "--kind", "memory_dir"])
    runner.invoke(app, ["reindex"])
    result = runner.invoke(app, ["query", "the rule", "--json", "--k", "3"])
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert "hits" in data
    assert "intent_tags" in data
    assert "tokens_used" in data
    assert "query_id" in data


def test_cli_query_human_readable(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("mnemo.cli.Embedder", lambda *a, **kw: FakeEmbedder())
    src = _seed_memory(tmp_path)
    runner.invoke(app, ["source", "add", str(src), "--kind", "memory_dir"])
    runner.invoke(app, ["reindex"])
    result = runner.invoke(app, ["query", "the rule", "--k", "3"])
    assert result.exit_code == 0
    assert "intent:" in result.stdout
    assert "[mnemo:" in result.stdout


def test_cli_node_show_not_found(runner: CliRunner) -> None:
    result = runner.invoke(app, ["node", "show", "deadbeef"])
    assert result.exit_code != 0


def test_cli_node_show(runner: CliRunner, tmp_path: Path) -> None:
    src = _seed_memory(tmp_path)
    runner.invoke(app, ["source", "add", str(src), "--kind", "memory_dir"])
    runner.invoke(app, ["reindex", "--no-embed"])
    # Pull a node id from the source list.
    listing = runner.invoke(app, ["source", "list", "--json"])
    assert listing.exit_code == 0
    # Use the underlying store to fetch a node id directly (simpler than
    # parsing CLI output for it).
    from mnemo import paths
    from mnemo.store import Store

    s = Store(paths.db_path())
    nid = s.list_nodes(limit=1)[0].id
    s.close()

    show = runner.invoke(app, ["node", "show", nid])
    assert show.exit_code == 0
    assert nid in show.stdout
    assert "Body" in show.stdout


def test_cli_daemon_status_when_stopped(runner: CliRunner) -> None:
    result = runner.invoke(app, ["daemon", "status"])
    assert result.exit_code == 0
    assert "not running" in result.stdout.lower()


def test_cli_daemon_stop_when_not_running(runner: CliRunner) -> None:
    result = runner.invoke(app, ["daemon", "stop"])
    assert result.exit_code == 0
    assert "not running" in result.stdout.lower()


def test_cli_init(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``init`` registers default sources from ``CLAUDE_HOME``."""
    claude = tmp_path / "claude"
    claude.mkdir()
    (claude / "CLAUDE.md").write_text("global memory", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_HOME", str(claude))

    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert "Registered" in result.stdout
    listing = runner.invoke(app, ["source", "list", "--json"])
    sources = json.loads(listing.stdout)
    assert any(s["kind"] == "claude_md" for s in sources)


# --- daemon restart (v3.1 live-review: no `mnemo daemon restart`) ----------


def test_cli_daemon_restart_stops_then_starts(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`mnemo daemon stop && start` ran the Windows `start` (blank
    cmd), not the daemon. `restart` does both in one command, in
    order, with clear messages -- no real process is spawned here."""
    calls: list[str] = []
    monkeypatch.setattr("mnemo.daemon.stop", lambda: calls.append("stop") or True)
    monkeypatch.setattr(
        "mnemo.daemon.start",
        lambda **kw: calls.append("start") or 4242,
    )
    result = runner.invoke(app, ["daemon", "restart"])
    assert result.exit_code == 0, result.stdout
    assert calls == ["stop", "start"]  # stop BEFORE start
    assert "daemon stopped" in result.stdout
    assert "pid 4242" in result.stdout


def test_cli_daemon_restart_when_not_running(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("mnemo.daemon.stop", lambda: False)
    monkeypatch.setattr("mnemo.daemon.start", lambda **kw: 99)
    result = runner.invoke(app, ["daemon", "restart"])
    assert result.exit_code == 0, result.stdout
    assert "was not running" in result.stdout
    assert "pid 99" in result.stdout

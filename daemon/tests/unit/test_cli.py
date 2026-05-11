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

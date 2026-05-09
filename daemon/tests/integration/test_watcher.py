"""Integration tests for the file watcher.

These exercise real filesystem events via watchfiles. They are timing-
sensitive (the OS needs a moment to deliver events) but kept tight enough
to run in <2s on a normal machine.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mnemo import ingest
from mnemo.store import Store
from mnemo.watcher import IngestWatcher


async def _run_watcher_until(
    watcher: IngestWatcher,
    *,
    expected_callbacks: int,
    timeout: float = 5.0,
) -> int:
    """Run the watcher until ``expected_callbacks`` have fired or timeout."""
    counter = {"n": 0}
    original = watcher.on_change

    async def counting_on_change(paths: set[Path]) -> None:
        await original(paths)
        counter["n"] += 1

    watcher.on_change = counting_on_change
    stop = asyncio.Event()
    task = asyncio.create_task(watcher.run(stop_event=stop))
    deadline = asyncio.get_event_loop().time() + timeout
    while counter["n"] < expected_callbacks:
        if asyncio.get_event_loop().time() > deadline:
            break
        await asyncio.sleep(0.05)
    stop.set()
    try:
        await asyncio.wait_for(task, timeout=2.0)
    except TimeoutError:
        task.cancel()
        with pytest.raises((asyncio.CancelledError, Exception)):
            await task
    return counter["n"]


@pytest.mark.asyncio
async def test_watcher_detects_file_create(tmp_path: Path, store: Store) -> None:
    store.register_source(str(tmp_path), "memory_dir")
    watcher = IngestWatcher(store, debounce_ms=50)
    watcher.add_default_paths()

    async def trigger() -> None:
        await asyncio.sleep(0.3)  # let watcher start
        (tmp_path / "feedback_new.md").write_text("body", encoding="utf-8")

    task = asyncio.create_task(trigger())
    fired = await _run_watcher_until(watcher, expected_callbacks=1, timeout=4.0)
    await task

    assert fired >= 1
    nodes = store.list_nodes(limit=10)
    assert any(n.source_path.endswith("feedback_new.md") for n in nodes)


@pytest.mark.asyncio
async def test_watcher_detects_file_modify(tmp_path: Path, store: Store) -> None:
    p = tmp_path / "feedback_x.md"
    p.write_text("v1", encoding="utf-8")
    store.register_source(str(tmp_path), "memory_dir")
    ingest.reindex(store)
    initial_node = store.list_nodes(limit=10)[0]
    initial_hash = initial_node.hash

    watcher = IngestWatcher(store, debounce_ms=50)
    watcher.add_default_paths()

    async def trigger() -> None:
        await asyncio.sleep(0.3)
        p.write_text("v2 different content", encoding="utf-8")

    task = asyncio.create_task(trigger())
    await _run_watcher_until(watcher, expected_callbacks=1, timeout=4.0)
    await task

    updated = store.list_nodes(limit=10)[0]
    assert updated.hash != initial_hash


@pytest.mark.asyncio
async def test_watcher_no_paths_returns_immediately(store: Store) -> None:
    watcher = IngestWatcher(store)
    # add_default_paths picks up nothing if no sources registered.
    watcher.add_default_paths()
    # Should return without blocking.
    await asyncio.wait_for(watcher.run(), timeout=1.0)


def test_affected_sources_matches_directory(tmp_path: Path, store: Store) -> None:
    store.register_source(str(tmp_path), "memory_dir")
    watcher = IngestWatcher(store)
    affected = watcher._affected_sources({tmp_path / "x.md"})
    assert len(affected) == 1
    assert affected[0].path == str(tmp_path)


def test_affected_sources_matches_single_file(tmp_path: Path, store: Store) -> None:
    p = tmp_path / "CLAUDE.md"
    p.write_text("body", encoding="utf-8")
    store.register_source(str(p), "claude_md")
    watcher = IngestWatcher(store)
    affected = watcher._affected_sources({p})
    assert len(affected) == 1


def test_affected_sources_skips_unrelated(tmp_path: Path, store: Store) -> None:
    other = tmp_path / "other"
    other.mkdir()
    store.register_source(str(other), "memory_dir")
    watcher = IngestWatcher(store)
    affected = watcher._affected_sources({tmp_path / "elsewhere.md"})
    assert affected == []

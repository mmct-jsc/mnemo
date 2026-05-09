"""Async file watcher that triggers reindex when memory files change.

The watcher is intentionally thin: it observes paths via ``watchfiles``,
batches FS events with a debounce step, and on each batch calls the supplied
callback (default: reindex affected sources).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

import watchfiles

from mnemo import ingest
from mnemo.store import Source, Store

log = logging.getLogger(__name__)


OnChange = Callable[[set[Path]], Awaitable[None]]


class IngestWatcher:
    """Observe source paths and reindex on change.

    Lifecycle:

    >>> w = IngestWatcher(store)
    >>> w.add_path("/path/to/memory")
    >>> task = asyncio.create_task(w.run())
    >>> # ... later ...
    >>> task.cancel()
    """

    def __init__(
        self,
        store: Store,
        *,
        debounce_ms: int = 200,
        on_change: OnChange | None = None,
    ) -> None:
        self.store = store
        self.debounce_ms = debounce_ms
        self.on_change: OnChange = on_change or self._default_on_change
        self._paths: list[Path] = []

    def add_path(self, path: str | Path) -> None:
        p = Path(path)
        if p not in self._paths:
            self._paths.append(p)

    def add_default_paths(self) -> None:
        """Add every enabled source from the store as a watched path."""
        for src in self.store.list_sources(only_enabled=True):
            self.add_path(src.path)

    async def run(self, *, stop_event: asyncio.Event | None = None) -> None:
        """Watch loop. Runs until ``stop_event`` is set or the task is cancelled."""
        if not self._paths:
            return
        existing = [p for p in self._paths if p.exists()]
        if not existing:
            return
        try:
            async for changes in watchfiles.awatch(
                *existing,
                step=self.debounce_ms,
                stop_event=stop_event,
                recursive=True,
            ):
                paths = {Path(p) for _, p in changes}
                try:
                    await self.on_change(paths)
                except Exception:  # noqa: BLE001
                    log.exception("on_change callback failed")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("watcher loop crashed")

    async def _default_on_change(self, changed: set[Path]) -> None:
        affected = self._affected_sources(changed)
        if not affected:
            return
        # Reindex is sync; offload to a thread to avoid blocking the loop.
        await asyncio.to_thread(ingest.reindex, self.store, sources=affected)

    def _affected_sources(self, changed: set[Path]) -> list[Source]:
        sources = self.store.list_sources(only_enabled=True)
        affected: list[Source] = []
        for src in sources:
            sp = Path(src.path)
            for cp in changed:
                if src.kind == "claude_md":
                    if cp == sp:
                        affected.append(src)
                        break
                else:
                    try:
                        cp.relative_to(sp)
                    except ValueError:
                        continue
                    affected.append(src)
                    break
        return affected

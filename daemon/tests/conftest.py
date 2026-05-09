"""Shared pytest fixtures for mnemo tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from mnemo.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    """Fresh per-test SQLite store under a tmp_path."""
    s = Store(tmp_path / "mnemo.db")
    try:
        yield s
    finally:
        s.close()

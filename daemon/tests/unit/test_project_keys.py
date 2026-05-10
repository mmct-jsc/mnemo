"""Unit tests for the canonical project-key derivation algorithm.

Backed by ``daemon/tests/fixtures/project_keys.json`` so adapter ports
(VS Code extension, SDK middleware) can run the same fixture against
their own implementations and catch drift.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.paths import project_key_from_abs

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "project_keys.json"


def _load_cases() -> list[tuple[str, str, str]]:
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return [(c["path"], c["expected"], c.get("note", "")) for c in data["cases"]]


@pytest.mark.parametrize(("path", "expected", "note"), _load_cases())
def test_project_key_canonical(path: str, expected: str, note: str) -> None:
    """Every (path, expected) pair from the fixture must pass."""
    actual = project_key_from_abs(path)
    assert actual == expected, (
        f"project_key_from_abs({path!r}) = {actual!r}, expected {expected!r}\nnote: {note}"
    )

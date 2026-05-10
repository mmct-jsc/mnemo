"""Plain-text parser. UTF-8 decode with replacement for invalid bytes."""

from __future__ import annotations

from pathlib import Path


def parse(raw_bytes: bytes, _path: Path) -> tuple[dict, str]:
    text = raw_bytes.decode("utf-8", errors="replace")
    # Trim trailing whitespace per line + collapse trailing blank lines.
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    body = "\n".join(lines)
    return {}, body

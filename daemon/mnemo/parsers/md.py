"""Markdown parser. Frontmatter via python-frontmatter."""

from __future__ import annotations

from pathlib import Path

import frontmatter


def parse(raw_bytes: bytes, _path: Path) -> tuple[dict, str]:
    text = raw_bytes.decode("utf-8", errors="replace")
    post = frontmatter.loads(text)
    return dict(post.metadata), post.content

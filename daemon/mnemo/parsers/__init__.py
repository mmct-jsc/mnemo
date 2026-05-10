"""File-format parsers for the ingest pipeline.

Each parser exports a single ``parse(raw_bytes, path) -> (frontmatter, body)``
function. The registry maps file extensions to parsers.

v1.1 phase 4 introduced the registry alongside .txt and .pdf support;
markdown remains the reference parser. Adding a new format in v1.2+ is a
two-line change: add ``mnemo/parsers/<fmt>.py`` and register it here.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from . import md as _md
from . import pdf as _pdf
from . import txt as _txt

# (frontmatter_dict, body_text). Frontmatter may be empty for non-markdown
# formats; ingest layer falls back to filename / content heuristics for
# name + description in that case.
ParseFn = Callable[[bytes, Path], tuple[dict, str]]

REGISTRY: dict[str, ParseFn] = {
    ".md": _md.parse,
    ".markdown": _md.parse,
    ".txt": _txt.parse,
    ".pdf": _pdf.parse,
}


def parse(raw_bytes: bytes, path: Path) -> tuple[dict, str]:
    """Dispatch to the parser registered for ``path.suffix``.

    Raises ``ValueError`` for unknown extensions; ingest's discovery walker
    is expected to filter unsupported types via include/exclude patterns
    before reaching here.
    """
    fn = REGISTRY.get(path.suffix.lower())
    if fn is None:
        raise ValueError(f"no parser registered for {path.suffix!r}")
    return fn(raw_bytes, path)

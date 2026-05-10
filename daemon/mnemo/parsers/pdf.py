"""PDF parser via pypdf. Per-page text extraction; page boundaries
preserved as ``--- page N ---`` headers so retrieval can cite specific
pages and chunkers can split on natural seams.

PDF text quality varies wildly. Scans without OCR will produce empty
or garbage text; we surface that as an empty body and let the caller
decide whether to keep the node.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def parse(raw_bytes: bytes, path: Path) -> tuple[dict, str]:
    # Lazy import: pypdf is only loaded when a .pdf is actually parsed.
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(raw_bytes))
    except (PdfReadError, ValueError) as exc:
        log.warning("pdf parse failed for %s: %s", path, exc)
        return {}, ""

    chunks: list[str] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 -- pypdf raises a zoo
            log.warning("pdf page %d extract failed for %s: %s", i, path, exc)
            text = ""
        text = text.strip()
        if text:
            chunks.append(f"--- page {i} ---\n{text}")

    body = "\n\n".join(chunks)
    fm: dict = {}
    # Pull doc-level metadata when available -- title becomes the node
    # name fallback if the user hasn't otherwise named it.
    md = reader.metadata or {}
    if md:
        title = getattr(md, "title", None) or md.get("/Title")
        if title:
            fm["name"] = str(title).strip()
        author = getattr(md, "author", None) or md.get("/Author")
        if author:
            fm["author"] = str(author).strip()
    return fm, body

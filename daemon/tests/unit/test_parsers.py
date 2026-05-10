"""Unit tests for the file-format parsers (phase 4).

Each parser returns ``(frontmatter_dict, body_text)``. The dispatcher
in ``mnemo.parsers`` routes by file extension.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from mnemo import parsers

# --- markdown -------------------------------------------------------------


def test_md_parses_frontmatter_and_body() -> None:
    raw = b"---\nname: hello\ntype: project\n---\nThis is the body.\n"
    fm, body = parsers.parse(raw, Path("foo.md"))
    assert fm == {"name": "hello", "type": "project"}
    assert body.strip() == "This is the body."


def test_md_no_frontmatter() -> None:
    raw = b"# Title\n\nNo frontmatter here.\n"
    fm, body = parsers.parse(raw, Path("foo.md"))
    assert fm == {}
    assert "Title" in body


def test_markdown_extension_alias() -> None:
    raw = b"plain content"
    _fm, body = parsers.parse(raw, Path("foo.markdown"))
    assert "plain content" in body


# --- plain text -----------------------------------------------------------


def test_txt_returns_empty_frontmatter() -> None:
    raw = b"line one\nline two\n"
    fm, body = parsers.parse(raw, Path("notes.txt"))
    assert fm == {}
    assert body == "line one\nline two"


def test_txt_strips_trailing_blank_lines() -> None:
    raw = b"content\n\n\n\n"
    _fm, body = parsers.parse(raw, Path("notes.txt"))
    assert body == "content"


def test_txt_handles_invalid_utf8() -> None:
    """Latin-1 high bytes should not crash; replacement char is fine."""
    raw = b"caf\xe9 latte"
    _fm, body = parsers.parse(raw, Path("a.txt"))
    assert "caf" in body  # replacement char varies; just confirm no exception


def test_txt_strips_per_line_trailing_whitespace() -> None:
    raw = b"line a   \nline b\t\n"
    _fm, body = parsers.parse(raw, Path("notes.txt"))
    assert body == "line a\nline b"


# --- PDF ------------------------------------------------------------------


def _build_minimal_pdf() -> bytes:
    """Build a 1-page PDF with the text 'mnemo phase 4' using pypdf."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject,
        DictionaryObject,
        FloatObject,
        NameObject,
        NumberObject,
        StreamObject,
    )

    # Build a plain page with a Helvetica BT/Tj content stream. pypdf can
    # write but its high-level write_text API requires a more elaborate
    # path; we hand-roll a content stream because the test only needs to
    # round-trip through extract_text.
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    page = writer.pages[0]

    # Embed a minimal Helvetica font reference.
    font_obj = DictionaryObject()
    font_obj.update(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font_obj)
    resources = page.get("/Resources")
    if resources is None:
        resources = DictionaryObject()
        page[NameObject("/Resources")] = resources
    fonts = DictionaryObject()
    fonts[NameObject("/F1")] = font_ref
    resources[NameObject("/Font")] = fonts

    # Content stream: BT /F1 12 Tf 72 720 Td (mnemo phase 4) Tj ET
    content = b"BT /F1 12 Tf 72 720 Td (mnemo phase 4) Tj ET"
    stream = StreamObject()
    stream.set_data(content)
    stream[NameObject("/Length")] = NumberObject(len(content))
    stream_ref = writer._add_object(stream)
    page[NameObject("/Contents")] = stream_ref
    page[NameObject("/MediaBox")] = ArrayObject(
        [NumberObject(0), NumberObject(0), FloatObject(612), FloatObject(792)]
    )

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_pdf_extracts_text_from_simple_page() -> None:
    raw = _build_minimal_pdf()
    fm, body = parsers.parse(raw, Path("doc.pdf"))
    assert "mnemo phase 4" in body
    # First (and only) page is wrapped in our '--- page 1 ---' header.
    assert "--- page 1 ---" in body
    # No frontmatter in this PDF -- author/title were not set.
    assert "name" not in fm
    assert "author" not in fm


def test_pdf_corrupt_input_yields_empty_body_not_exception() -> None:
    """Garbage bytes shouldn't crash the ingest pipeline -- we log and
    return empty body so the caller can keep moving."""
    raw = b"%PDF-1.4 not actually a pdf"
    fm, body = parsers.parse(raw, Path("broken.pdf"))
    assert fm == {}
    assert body == ""


# --- registry -------------------------------------------------------------


def test_unknown_extension_raises() -> None:
    with pytest.raises(ValueError, match="no parser registered"):
        parsers.parse(b"data", Path("file.xyz"))


def test_extension_lookup_is_case_insensitive() -> None:
    raw = b"hello"
    _fm, body = parsers.parse(raw, Path("F.MD"))
    assert "hello" in body

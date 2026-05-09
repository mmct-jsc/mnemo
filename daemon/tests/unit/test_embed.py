"""Tests for chunking. Embedder with the real model is exercised in integration."""

from __future__ import annotations

from mnemo.embed import _approx_tokens, _split_by_headings, chunk_body

# --- _approx_tokens -------------------------------------------------------


def test_approx_tokens_zero_words() -> None:
    assert _approx_tokens("") >= 1  # at least the +1 floor


def test_approx_tokens_grows_with_words() -> None:
    a = _approx_tokens("one two three")
    b = _approx_tokens("one two three four five six")
    assert b > a


# --- _split_by_headings ---------------------------------------------------


def test_split_keeps_text_with_no_headings_as_one() -> None:
    body = "just a paragraph\n\nand another"
    sections = _split_by_headings(body)
    assert len(sections) == 1
    assert sections[0] == body


def test_split_breaks_on_h2() -> None:
    body = "## A\nbody A\n\n## B\nbody B\n"
    sections = _split_by_headings(body)
    assert len(sections) == 2
    assert sections[0].startswith("## A")
    assert sections[1].startswith("## B")


def test_split_breaks_on_h3() -> None:
    body = "intro\n\n### sub\nstuff\n"
    sections = _split_by_headings(body)
    assert len(sections) == 2


def test_split_does_not_break_on_h1() -> None:
    # H1 is typically the title; we don't cut on it.
    body = "# Title\n\nbody1\n\nbody2\n"
    sections = _split_by_headings(body)
    assert len(sections) == 1


# --- chunk_body -----------------------------------------------------------


def test_chunk_body_empty_returns_empty() -> None:
    assert chunk_body("") == []
    assert chunk_body("   \n\n  ") == []


def test_chunk_body_short_returns_one() -> None:
    body = "Short note about something specific."
    chunks = chunk_body(body)
    assert chunks == [body]


def test_chunk_body_splits_on_headings() -> None:
    body = "## A\nbody A\n\n## B\nbody B\n"
    chunks = chunk_body(body)
    assert len(chunks) == 2
    assert chunks[0].startswith("## A")
    assert chunks[1].startswith("## B")


def test_chunk_body_paragraph_packs_when_section_too_large() -> None:
    # Build a section bigger than max_tokens so paragraph-pack kicks in.
    para = "word " * 50  # ~67 tokens by approx
    body = "## section\n" + "\n\n".join([para] * 6)  # ~400 tokens
    chunks = chunk_body(body, max_tokens=120, overlap_blocks=0)
    assert len(chunks) >= 2
    # Each chunk should respect the budget (allowing one paragraph of slop).
    for c in chunks:
        assert _approx_tokens(c) <= 200


def test_chunk_body_overlap_repeats_block() -> None:
    para_a = "alpha " * 30
    para_b = "beta " * 30
    para_c = "gamma " * 30
    body = "## section\n" + "\n\n".join([para_a, para_b, para_c])
    chunks = chunk_body(body, max_tokens=80, overlap_blocks=1)
    # With max_tokens=80 we can fit ~1 paragraph per chunk.
    # Overlap=1 means the last paragraph of chunk N appears at the start of N+1.
    if len(chunks) >= 2:
        # The first paragraph of the second chunk should be one we've seen.
        first_para_of_second = chunks[1].split("\n\n")[0]
        assert first_para_of_second in chunks[0]


def test_chunk_body_custom_token_counter() -> None:
    # If counter says everything is one token, even huge bodies stay one chunk.
    body = "## sec\n" + ("x " * 5000)
    chunks = chunk_body(body, max_tokens=10, count=lambda _t: 1)
    assert len(chunks) == 1

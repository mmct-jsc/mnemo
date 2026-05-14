"""Phase 5 of the v2.2 progressive-UX rollout: streamed body rendering.

These tests lock the SURFACE of the v2.2.5 ``mnemoRenderBody``
upgrade. Per the design (`docs/plans/2026-05-14-ux-progressive-design.md`
section 5), the helper keeps its existing 3-branch decision tree
(code_* / commit / markdown) and its existing call-site signature

    window.mnemoRenderBody(targetEl, body, { type, sourcePath })

but routes the actual reveal through ``window.mnemoStreamText`` so
bodies appear progressively rather than in one tick.

Like the rest of the v2.2 test suite, these grep ``base.html`` -- the
project has no Node toolchain, so live behavior is verified manually
through the preview tool. The tests here exist to prevent a future
refactor from silently dropping the streaming path.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

BASE_HTML = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "templates" / "base.html"


@pytest.fixture(scope="module")
def base_html_src() -> str:
    return BASE_HTML.read_text(encoding="utf-8")


def _body(src: str) -> str:
    """Return only the JS chunk that defines ``window.mnemoRenderBody``.

    The helper lives inside a ``<script>`` tag; we extract from the
    assignment up to the closing brace before the next top-level
    ``window.`` assignment so test patterns don't accidentally match
    unrelated helpers.
    """
    start_match = re.search(r"window\.mnemoRenderBody\s*=", src)
    assert start_match, "mnemoRenderBody must be defined as a window.* assignment"
    after = src[start_match.start() :]
    # End at the next top-level "window.<name> =" assignment.
    next_match = re.search(r"\n\s*window\.[A-Za-z]", after[1:])
    return after[: next_match.start() + 1] if next_match else after


# --- the helper still exists with the same signature --------------------


def test_mnemo_render_body_still_defined(base_html_src: str) -> None:
    """The call-site signature must not regress; node.html + _search_results.html
    both call the helper with ``(targetEl, body, { type, sourcePath })``.
    """
    assert re.search(r"window\.mnemoRenderBody\s*=\s*\(", base_html_src), (
        "window.mnemoRenderBody must be defined in base.html with the existing "
        "(targetEl, body, opts) signature. See section 5 of the design doc."
    )


def test_mnemo_render_body_keeps_three_branch_modes(base_html_src: str) -> None:
    """The diagnostic return value ('code' / 'plain' / 'markdown') is read by
    node.html (lastRenderedAs) and _search_results.html (dataset.renderedAs).
    Phase 5 keeps it.
    """
    body = _body(base_html_src)
    for mode in ("'code'", "'plain'", "'markdown'"):
        assert mode in body, (
            f"mnemoRenderBody must still return the {mode} mode string so "
            "node.html (lastRenderedAs) and _search_results.html "
            "(dataset.renderedAs) keep working unchanged."
        )


# --- the streaming wiring ----------------------------------------------


def test_mnemo_render_body_uses_stream_text(base_html_src: str) -> None:
    """Phase 5 routes every branch through ``window.mnemoStreamText``.

    Word-by-word for markdown, line-by-line for code + commit.
    """
    body = _body(base_html_src)
    assert "window.mnemoStreamText" in body or "mnemoStreamText(" in body, (
        "Phase 5 of v2.2 requires mnemoRenderBody to delegate the actual "
        "reveal to window.mnemoStreamText. Grepped for "
        "``window.mnemoStreamText`` / ``mnemoStreamText(`` in the helper body."
    )


def test_mnemo_render_body_streams_code_line_by_line(base_html_src: str) -> None:
    """Per the design doc table:

        | Branch | Unit |
        | code_* | line |
        | commit | line |
        | markdown | word |

    The helper must pass ``unit: 'line'`` for at least one branch -- this
    covers both the code and commit branches.
    """
    body = _body(base_html_src)
    assert re.search(r"unit\s*:\s*['\"]line['\"]", body), (
        "mnemoRenderBody must stream code/commit bodies line-by-line via "
        "mnemoStreamText({ unit: 'line', ... }). See § 5 of the design doc."
    )


def test_mnemo_render_body_streams_markdown_word_by_word(base_html_src: str) -> None:
    """The markdown branch must reveal word-by-word."""
    body = _body(base_html_src)
    assert re.search(r"unit\s*:\s*['\"]word['\"]", body), (
        "mnemoRenderBody must stream markdown bodies word-by-word via "
        "mnemoStreamText({ unit: 'word', ... })."
    )


def test_mnemo_render_body_reapplies_prism_during_stream(base_html_src: str) -> None:
    """The design specifies the code branch re-highlights every ~8 lines so
    Prism colors fill in chunks rather than waiting for the whole body.

    We require a Prism.highlightElement reference inside the helper body --
    any re-highlight loop or post-stream re-highlight call works.
    """
    body = _body(base_html_src)
    assert "Prism.highlightElement" in body, (
        "mnemoRenderBody's code branch must call Prism.highlightElement at "
        "least once so Prism re-tokenizes the progressively-revealed lines. "
        "See § 5 of the design doc."
    )


def test_mnemo_render_body_exposes_cancel_handle(base_html_src: str) -> None:
    """When the user clicks a different node mid-reveal, the previous
    stream must be cancellable. The streamText return ``{ cancel, done }``
    is stored on the target element so a future focusNode coordinator
    (chat panel, neighbor click) can find + abort it.

    Contract: ``targetEl._mnemoStreamCancel`` (or a similar dotted handle
    on the element) is wired.
    """
    body = _body(base_html_src)
    assert re.search(r"_mnemoStream(Cancel|Handle)", body), (
        "mnemoRenderBody must attach the streamText handle (or its cancel "
        "function) to the target element so callers can abort an in-flight "
        "reveal. Grepped for ``_mnemoStreamCancel`` / ``_mnemoStreamHandle``."
    )


def test_mnemo_render_body_cancels_previous_stream(base_html_src: str) -> None:
    """Calling mnemoRenderBody twice on the same target element must
    cancel the prior reveal before starting the new one -- otherwise the
    two streams race and the body ends up with mixed text.
    """
    body = _body(base_html_src)
    assert re.search(r"_mnemoStream(Cancel|Handle).*cancel", body, re.DOTALL), (
        "mnemoRenderBody must cancel any prior streamText handle on the "
        "target element before kicking off a new reveal. Expected a "
        "``targetEl._mnemoStreamCancel()`` (or .cancel() on the saved "
        "handle) somewhere in the helper body."
    )


# --- accessibility floor -----------------------------------------------


def test_reduced_motion_path_still_lands_full_content(base_html_src: str) -> None:
    """``mnemoStreamText`` already short-circuits to instant text when
    ``prefers-reduced-motion: reduce`` is on (verified by
    test_progressive.test_app_js_honors_reduced_motion). Phase 5 must
    not introduce a fast path that bypasses it.

    Concretely: the helper must NOT set ``targetEl.textContent = text``
    inside a non-reduced-motion branch (which would defeat streaming).
    A reduced-motion bypass is fine, but it must go through
    mnemoStreamText OR ``mnemoPrefersReducedMotion``.
    """
    body = _body(base_html_src)
    # Crude but effective: if the helper sets textContent = text outside
    # a reduced-motion check, the streaming is dead.
    direct_writes = re.findall(r"\.textContent\s*=\s*text\b", body)
    if direct_writes:
        assert "mnemoPrefersReducedMotion" in body or "prefers-reduced-motion" in body, (
            "If mnemoRenderBody writes ``.textContent = text`` directly, it "
            "must be inside a reduced-motion fallback. Otherwise streaming "
            "is bypassed entirely."
        )

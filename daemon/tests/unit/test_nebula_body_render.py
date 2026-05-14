"""v2.2.7 bug fix: Nebula side panel uses mnemoRenderBody for type-aware
body preview (markdown -> HTML, code -> Prism, commit -> escaped pre).

Reported symptom (2026-05-14, post-v2.2.6): markdown previews in the
Nebula side panel "seem off, it might misunderstand code and md".

Root cause: graph.html historically renders the side-panel body
INSIDE a hardcoded ``<pre class="line-numbers"><code class="language-X">``
shell, and the helper ``streamBodyToCode`` (introduced v2.2.1 phase 4
BEFORE mnemoRenderBody became streaming-aware in v2.2.5) just streams
plain text into that ``<code>`` element + calls Prism.highlightElement
at the end. Result: every body in Nebula renders through the CODE
path -- including memory_*, project_doc, plan_doc, session_summary,
and code_* nodes whose source_path ends in ``.md`` (READMEs, design
docs). Markdown source appears as monospace text with Prism trying to
syntax-color ``**bold**`` and ``# heading`` as markdown SOURCE rather
than rendering it as HTML.

The /node/<id> detail page and the search popover both use
``window.mnemoRenderBody`` correctly. Nebula is the only surface
that bypasses the type-aware branching.

Fix: drop the ``<pre><code>`` shell + ``streamBodyToCode`` path.
Render into a ``<div class="nebula-body md-body">`` and delegate to
``mnemoRenderBody`` (which since v2.2.5 already handles streaming via
mnemoStreamText, returns the mode string, AND attaches a cancel
handle on ``targetEl._mnemoStreamCancel``).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

GRAPH_HTML = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "templates" / "graph.html"


@pytest.fixture(scope="module")
def graph_html_src() -> str:
    return GRAPH_HTML.read_text(encoding="utf-8")


# --- the bug: hardcoded <pre><code> + streamBodyToCode ----------------


def test_nebula_body_not_hardcoded_to_pre_code(graph_html_src: str) -> None:
    """The side panel must NOT wrap the body in a hardcoded
    ``<pre class="line-numbers"><code ...>`` shell. That shell forces
    the code-rendering path for every node type, including
    memory_*, project_doc, plan_doc, session_summary, and code_*
    .md files -- so markdown bodies render as monospace source
    instead of being rendered to HTML.

    A code-shaped container is still fine when mnemoRenderBody
    PUTS one there for code_* nodes (the helper writes the
    ``<pre><code>`` itself in that branch). But the OUTER template
    must offer mnemoRenderBody a generic container so the markdown
    branch can write its own marked HTML.
    """
    # Pull the side-panel <aside> block so we don't match unrelated
    # <pre><code> elsewhere in the template (e.g. the citation
    # snippet).
    aside_match = re.search(
        r'<aside class="nebula-detail"[^>]*>(.*?)</aside>',
        graph_html_src,
        re.DOTALL,
    )
    assert aside_match, "graph.html must have an <aside class='nebula-detail'> block"
    aside = aside_match.group(1)
    bad = re.search(
        r"<pre[^>]*line-numbers[^>]*>\s*<code\b",
        aside,
        re.DOTALL,
    )
    assert not bad, (
        "Nebula side panel must not wrap the body in a hardcoded "
        '``<pre class="line-numbers"><code>`` shell. That forces every '
        "node body through the code path; markdown bodies (memory_*, "
        "project_doc, plan_doc, session_summary, code_* README.md) "
        "appear as monospace source rather than rendered HTML. Move "
        "to a generic ``<div class='nebula-body md-body'>`` container "
        "+ mnemoRenderBody."
    )


def test_nebula_does_not_call_stream_body_to_code(graph_html_src: str) -> None:
    """``streamBodyToCode`` is the v2.2.1 helper that bypassed the
    type-aware branching. It must be retired (or kept as a non-default
    fallback). The side-panel template MUST instead drive a
    mnemoRenderBody-based render path.
    """
    # The method definition can stay for back-compat, but the side
    # panel x-effect must NOT call it. Look for a call site -- the
    # template-side ``streamBodyToCode(...)`` x-effect specifically.
    bad = re.search(
        r'x-effect="\s*streamBodyToCode\(',
        graph_html_src,
    )
    assert not bad, (
        "graph.html's side-panel template must NOT call "
        "streamBodyToCode in an x-effect -- that path skips "
        "mnemoRenderBody's type-aware branching. Render via "
        "``mnemoRenderBody(el, body, { type, sourcePath })`` instead."
    )


# --- the fix: a generic body container + mnemoRenderBody --------------


def test_nebula_side_panel_uses_mnemo_render_body(graph_html_src: str) -> None:
    """The side panel must call ``mnemoRenderBody`` (or a thin
    wrapper around it) so markdown bodies render as HTML, commit
    bodies as escaped <pre>, and code bodies via Prism -- the
    same three-branch decision tree node.html + _search_results.html
    already use.
    """
    aside_match = re.search(
        r'<aside class="nebula-detail"[^>]*>(.*?)</aside>',
        graph_html_src,
        re.DOTALL,
    )
    assert aside_match, "graph.html must have an <aside class='nebula-detail'> block"
    aside = aside_match.group(1)
    # Accept either window.mnemoRenderBody(...) directly in an
    # x-effect OR a wrapper method (renderBody / renderSelectedBody)
    # that calls it inside the JS body.
    pattern = re.search(
        r"(window\.mnemoRenderBody|mnemoRenderBody\(|renderBody\()",
        aside,
    )
    assert pattern, (
        "Nebula side panel template must invoke mnemoRenderBody (or a "
        "wrapper that does) inside an x-effect or method binding. "
        "Found no such call in the <aside class='nebula-detail'> block."
    )


def test_nebula_body_container_is_md_body(graph_html_src: str) -> None:
    """The body container must use the ``md-body`` class so the
    markdown typography (headings, lists, links, inline code)
    inherits the same look as node.html. The code branch of
    mnemoRenderBody writes its own ``<pre><code>``, so the parent
    container can stay generic.
    """
    aside_match = re.search(
        r'<aside class="nebula-detail"[^>]*>(.*?)</aside>',
        graph_html_src,
        re.DOTALL,
    )
    assert aside_match, "graph.html must have an <aside class='nebula-detail'> block"
    aside = aside_match.group(1)
    # Look for an element with class containing "md-body" inside the
    # aside that is shown when selected.body is truthy.
    pattern = re.search(r'class="[^"]*\bmd-body\b[^"]*"', aside)
    assert pattern, (
        "Nebula side panel must use a ``md-body``-classed container "
        "for the body so markdown typography matches node.html's "
        "rendering."
    )


# --- regression guard: code_* nodes still highlight correctly ---------


def test_body_language_helper_still_returns_extension(graph_html_src: str) -> None:
    """The ``bodyLanguage(sourcePath)`` helper is what node.html +
    Nebula both use to pick a Prism language class. The fix must
    not remove or break it -- the code branch of mnemoRenderBody
    still consults window.mnemoLanguageOf which delegates the same
    way. We just verify the helper is still present in the Alpine
    component so a future refactor doesn't accidentally drop it.
    """
    assert re.search(r"\bbodyLanguage\s*\(", graph_html_src), (
        "nebulaPage() must keep the bodyLanguage(sourcePath) helper "
        "so cosmetic class names (e.g. ``nebula-body-<lang>``) can "
        "still hang off the source-path extension."
    )

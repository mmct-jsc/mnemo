"""v5.1.0: section-aware copy buttons on architect output.

The architect skill emits a sectioned markdown block with six
canonical headings (Problem / Context / Files / Acceptance /
Anti-patterns / Prompt -- per design doc S6). v5.0 ships a single
"copy message" button that grabs the whole bubble. v5.1.0 adds a
"Copy prompt only" affordance that pulls just the ``## Prompt``
section -- useful for IDEs with tight context budgets (Cursor /
Copilot) where the architect's context section duplicates what
the IDE already sees from the active file.

Tests pin the JS contract:

- ``extractPromptSection(text)`` returns the body of the
  ``## Prompt`` heading (everything from after the heading to
  EOF or the next ``##`` heading at the same level).
- ``looksArchitected(text)`` returns true only when the markdown
  contains at least the ``## Prompt`` heading (heuristic;
  matches the architect skill's output shape).
- The dock template renders a "Copy prompt" button alongside the
  existing copy-message button, shown only when
  ``looksArchitected(m.content.text)``.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CHAT_JS = REPO_ROOT / "daemon" / "mnemo" / "ui" / "static" / "chat.js"
BASE_TMPL = REPO_ROOT / "daemon" / "mnemo" / "ui" / "templates" / "base.html"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# --- chat.js helpers contract ----------------------------------------------


def test_chat_js_exposes_looks_architected() -> None:
    js = _read(CHAT_JS)
    assert "looksArchitected" in js, (
        "chat.js must expose a looksArchitected(text) predicate so the "
        "template can conditionally render the prompt-only copy button"
    )


def test_chat_js_exposes_extract_prompt_section() -> None:
    js = _read(CHAT_JS)
    assert "extractPromptSection" in js, (
        "chat.js must expose extractPromptSection(text) -- pulls the "
        "body of the ## Prompt heading from a sectioned architect output"
    )


def test_extract_prompt_section_uses_heading_regex() -> None:
    """The implementation must scope on the ``## Prompt`` heading,
    not a substring match that could false-positive on body text."""
    js = _read(CHAT_JS)
    # Match either the literal heading or a regex form that anchors
    # on a line-start ``## Prompt``.
    idx = js.find("extractPromptSection")
    window = js[idx : idx + 800]
    assert "## Prompt" in window or "^##\\\\s*Prompt" in window, (
        "extractPromptSection must scope on the '## Prompt' heading"
    )


# --- Template-render contract ---------------------------------------------


def test_dock_template_renders_copy_prompt_button() -> None:
    """The dock turn renders a 'Copy prompt' button on assistant
    messages that look architected. Distinct class hook from the
    existing mc-copy so CSS can position them side-by-side."""
    html = _read(BASE_TMPL)
    assert "mc-copy-prompt" in html, (
        "base.html dock surface must render the 'Copy prompt' button with class mc-copy-prompt"
    )


def test_copy_prompt_button_gated_on_architected_output() -> None:
    """The button must only show when the message looks architected
    -- otherwise every assistant message gets a button that copies
    an empty string (no ## Prompt section)."""
    html = _read(BASE_TMPL)
    # Either an x-show binding on looksArchitected or a wrapping
    # <template x-if> with the predicate.
    idx = html.find("mc-copy-prompt")
    window = html[max(0, idx - 200) : idx + 200]
    assert "looksArchitected" in window, (
        "mc-copy-prompt rendering must be gated on looksArchitected(text)"
    )


def test_copy_prompt_button_calls_extract_and_copy_text() -> None:
    """The button wires extractPromptSection -> copyText, the same
    clipboard helper the existing mc-copy uses."""
    html = _read(BASE_TMPL)
    idx = html.find("mc-copy-prompt")
    window = html[idx : idx + 400]
    assert "extractPromptSection" in window
    assert "copyText" in window

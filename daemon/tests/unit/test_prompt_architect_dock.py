"""v5 phase 4: prompt-architect dock surface.

The dock gets a toggle that flips ``architectMode`` in the chat
factory. When the toggle is ON, ``sendMessage`` POSTs with
``use_skill: 'mnemo-prompt-architect'`` so the phase-3 entry-point
fires the skill pre-load before the user's text reaches the model.
The chat-shell renders the architected output as a normal assistant
message (the skill's sectioned-markdown shape is regular markdown);
a copy-prompt affordance lives next to the rendered output.

Template + JS contract tests (no live browser) -- the same pattern
as test_chat_page / test_chat_surface_contract.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSER_TMPL = REPO_ROOT / "daemon" / "mnemo" / "ui" / "templates" / "_chat_composer.html"
CHAT_JS = REPO_ROOT / "daemon" / "mnemo" / "ui" / "static" / "chat.js"
APP_CSS = REPO_ROOT / "daemon" / "mnemo" / "ui" / "static" / "app.css"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# --- Composer template contract ------------------------------------------


def test_dock_composer_has_architect_toggle() -> None:
    """The dock branch of the composer renders a button bound to
    ``architectMode`` so the user can flip the dock between regular
    chat and prompt-architect mode."""
    html = _read(COMPOSER_TMPL)
    # The toggle lives in the dock branch only -- the /chat page surface
    # is intentionally NOT cluttered with this in v5.0.
    assert "architectMode" in html, "_chat_composer.html must reference architectMode"
    # A class hook so CSS can style the active state.
    assert "mc-architect" in html, "_chat_composer.html should mark the architect toggle"


def test_architect_toggle_on_both_surfaces() -> None:
    """v5.0 shipped the toggle dock-only per design Q3. v5.2.0 is
    the cross-surface convenience expansion the design-doc S12
    phased roadmap named -- the toggle now lives on BOTH the dock
    and the /chat page surface. Same architectMode factory state,
    same .mc-architect class hook, same behaviour."""
    html = _read(COMPOSER_TMPL)
    dock_block, _, page_block = html.partition("{% else %}")
    assert "architectMode" in dock_block, (
        "architect toggle must be inside the surface=='dock' branch"
    )
    assert "architectMode" in page_block, (
        "v5.2.0: architect toggle must ALSO be on the /chat page surface"
    )
    # The class hook must appear on both branches so CSS styling is
    # consistent across surfaces.
    assert dock_block.count("mc-architect") >= 1
    assert page_block.count("mc-architect") >= 1


# --- chat.js contract ----------------------------------------------------


def test_chat_factory_initializes_architect_mode_false() -> None:
    js = _read(CHAT_JS)
    assert "architectMode" in js, "chat.js factory must declare architectMode"
    # default OFF so legacy dock users see no behavior change until they opt in
    assert "architectMode: false" in js


def test_send_message_passes_use_skill_when_architect_on() -> None:
    """The POST body must carry ``use_skill: 'mnemo-prompt-architect'``
    when architectMode is true. This is the wire to phase 3."""
    js = _read(CHAT_JS)
    assert "mnemo-prompt-architect" in js, "chat.js should reference the architect skill name"
    # The skill name must be passed in the message POST body (not just
    # listed somewhere). A loose grep: the skill name appears within a
    # window AROUND the POST URL (the body assignment happens just
    # BEFORE the fetch call). And the use_skill key must guard on the
    # architectMode flag.
    body_idx = js.find("'/v1/chat/' + id + '/message'")
    assert body_idx >= 0
    body_window = js[max(0, body_idx - 1500) : body_idx + 800]
    assert "use_skill" in body_window, (
        "use_skill must be set in the message POST body when architectMode is true"
    )
    assert "architectMode" in body_window, (
        "use_skill must be gated on architectMode (not always sent)"
    )


def test_chat_js_offers_copy_prompt_affordance() -> None:
    """The architect output is paste-bound; the dock must expose a
    one-click copy."""
    js = _read(CHAT_JS)
    # Either a copy button binding or a clipboard call -- the dock can
    # implement either, but the surface MUST exist.
    assert "copyArchitected" in js or "navigator.clipboard" in js or "copyPrompt" in js, (
        "chat.js should expose a copy-prompt affordance for architect mode"
    )


# --- CSS contract (light-touch -- just that the class hook exists) -------


def test_architect_toggle_has_css_hook() -> None:
    css = _read(APP_CSS)
    # We added a .mc-architect class hook in the composer; CSS should
    # at least define a baseline style for it.
    assert "mc-architect" in css, "app.css should style the .mc-architect toggle"

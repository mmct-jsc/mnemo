"""v5.1.1: zoom-to-cursor must use the display frame, not the static one.

User-reported: scroll-zoom on `/graph` lands the target OPPOSITE the
cursor (the symptom is most pronounced around the galactic-rotation
angle ``gA == pi``; at other angles it appears rotated rather than
exactly reversed). Predates v5; lives in the v4.6 wheel handler.

Root cause: the wheel handler reads ``before = screenToWorld(...)``
and ``after = screenToWorld(...)``, then applies ``cam.x +=
before.x - after.x`` (and y). But ``screenToWorld`` applies the
inverse galactic-rotation by ``-gA`` so the returned point lands in
the STATIC frame (the frame nodes + the pick index live in). The
``cam.x/y`` field lives in the DISPLAY frame (the shader applies
``+gA`` AFTER the cam offset). Adding a static-frame delta to a
display-frame cam rotates the correction by ``-gA`` — exactly the
"zoom target drifts off the cursor as the galaxy rotates" symptom.

Fix: add a new ``screenToCam`` helper that returns the
DISPLAY-frame point (skips the inverse-rotate); use it in the wheel
handler. ``screenToWorld`` stays unchanged for node-drag / pick /
hover (which DO want the static frame).

Template-grep test — the dock has no JS test runner today, so this
pins the structural fix.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
NEBULA_JS = REPO_ROOT / "daemon" / "mnemo" / "ui" / "static" / "vendor" / "nebula-gl.js"


def _read() -> str:
    return NEBULA_JS.read_text(encoding="utf-8")


def test_screen_to_cam_helper_exists() -> None:
    """A separate display-frame helper must exist so the wheel
    handler can opt out of the inverse-rotate."""
    js = _read()
    assert "screenToCam" in js, (
        "nebula-gl.js must expose a screenToCam helper that returns the "
        "DISPLAY-frame point (no -gA inverse-rotate)"
    )


def test_screen_to_cam_skips_inverse_rotate() -> None:
    """The new helper must compute the cam-frame point WITHOUT the
    rotation step that screenToWorld does (no sin/cos of -gA)."""
    js = _read()
    idx = js.find("function screenToCam")
    assert idx >= 0
    body = js[idx : idx + 400]
    # The body must NOT reference -gA / sin / cos at all -- a
    # presence indicates the rotation is still happening.
    assert "-gA" not in body, "screenToCam must not inverse-rotate; that's the bug we're fixing"
    assert "sin" not in body
    assert "cos" not in body


def test_wheel_handler_uses_screen_to_cam_not_world() -> None:
    """The wheel handler's before/after reads must use the new
    display-frame helper. Using screenToWorld here is the bug."""
    js = _read()
    wheel_idx = js.find("canvas.addEventListener('wheel'")
    assert wheel_idx >= 0
    # Scope to the wheel handler ONLY. The handler ends at its closing
    # `}, { passive: false });` -- match that, not a fixed-size window
    # that would spill into the adjacent mousedown handler (which
    # legitimately uses screenToWorld for picking).
    wheel_end = js.find("}, { passive: false });", wheel_idx)
    assert wheel_end > wheel_idx
    wheel_body = js[wheel_idx:wheel_end]
    assert "screenToCam" in wheel_body, (
        "wheel handler must call screenToCam (display frame) for zoom-to-cursor"
    )
    assert "screenToWorld" not in wheel_body, (
        "wheel handler must NOT call screenToWorld -- the inverse-rotate is "
        "exactly the bug we're fixing"
    )


def test_screen_to_world_still_used_for_drag_and_pick() -> None:
    """The mousedown handler (pick) and mousemove (drag/hover) MUST
    still call screenToWorld -- they want the static frame. The fix
    is scoped to the wheel handler."""
    js = _read()
    mousedown_idx = js.find("canvas.addEventListener('mousedown'")
    assert mousedown_idx >= 0
    mousedown_window = js[mousedown_idx : mousedown_idx + 400]
    assert "screenToWorld" in mousedown_window, (
        "mousedown's pick.nearest must still use screenToWorld for the static-frame node coords"
    )

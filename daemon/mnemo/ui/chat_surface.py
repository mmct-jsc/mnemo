"""Single-source chat surface capability matrix (C3, v4.3).

Adding/altering a surface capability = one edit here. The shared Jinja
partials (_chat_rail / _chat_bookmarks / _chat_examples /
_chat_composer) self-gate on these flags so a capability is opt-in per
surface, never silently re-implemented or missing. Mirrors the proven
palette.py single-source model (registered as a Jinja global in
routes.py, exactly like type_colors).

Pre-v4.3 the dock (base.html) re-implemented a subset of the page's
chat and silently omitted list/switch/back, bookmarks, examples --
even though the mnemoChat() factory already shared ALL the logic. The
divergence was purely in the templates; this matrix + the guard test
make it impossible to drift again.
"""

CHAT_SURFACES: dict[str, dict[str, bool]] = {
    "page": {
        "rail": True,
        "switch": True,
        "rename": True,
        "bookmarks": True,
        "examples": True,
        "composer": True,
    },
    "dock": {
        "rail": True,
        "switch": True,
        "rename": True,
        "bookmarks": True,
        "examples": True,
        "composer": True,
    },
}

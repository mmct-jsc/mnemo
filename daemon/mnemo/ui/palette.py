"""Single source of truth for node-type colors used across the UI.

Before v2.1.x the badge / bar-fill / graph-node colors were defined in
three different places: ``app.css`` (CSS variables + per-type rules),
``graph.html`` (JS ``TYPE_COLORS`` for the Cytoscape canvas), and
ad-hoc inline palettes in a couple of templates. New node types
(``code_function``, ``code_method``, ``code_class``, ...) added in
v2.0 lost colors on the dashboard because nobody remembered to update
all three places.

This module owns the palette. Both Python templates (via a Jinja
global) and the browser (via ``window.MNEMO_TYPE_COLORS``) read from
here, and the CSS uses generic ``[class*="type-"]`` selectors that
read a ``--type-color`` custom property the templates inject inline.

To add a new node type:

  1. Add a row to ``TYPE_COLORS`` below.
  2. (That's it -- badges, bar fills, graph nodes, and the audit
      summary chips all pick the new color automatically.)

The hex values mirror the Nebula palette ("neon on velvet") -- each
color is saturated enough to glow against the dark background and
distinct from its neighbors at typical viewing distance.
"""

from __future__ import annotations

# Memory layer -- warm accents.
# Code layer  -- cool spectrum (cyan/teal/sky/magenta/amber/violet/emerald).
# Other       -- neutral slates for the special cases.
TYPE_COLORS: dict[str, str] = {
    # --- memory layer ---
    "memory_feedback": "#ff8a4c",  # amber-orange (incidents, retros)
    "memory_user": "#c4f37a",  # lime (user preferences)
    "memory_project": "#5fa8ff",  # electric blue (project notes)
    "memory_reference": "#e879f9",  # hot pink (references)
    "project_doc": "#ffd54a",  # gold (canonical docs)
    "plan_doc": "#818cf8",  # indigo (plans)
    "session_summary": "#22d3ee",  # sky (sessions)
    # --- code layer ---
    "code_module": "#06b6d4",  # deep cyan -- the file
    "code_function": "#2dd4bf",  # teal -- the verb
    "code_method": "#38bdf8",  # sky blue -- bound verb
    "code_class": "#e879f9",  # magenta -- the noun
    "code_route": "#fb923c",  # bright amber -- the doorway
    "code_component": "#c084fc",  # violet -- the surface
    "code_endpoint": "#4ade80",  # emerald -- the join
    # --- meta ---
    "commit": "#94a3b8",  # slate -- the past
}

# Fallback for any node type that doesn't appear in TYPE_COLORS yet.
# Templates should ``TYPE_COLORS.get(type, FALLBACK_COLOR)``. Picked
# to be a readable mid-grey-blue against the dark canvas.
FALLBACK_COLOR: str = "#7ee7e0"


def color_for(node_type: str | None) -> str:
    """Return the hex color for ``node_type``, or the fallback."""
    if not node_type:
        return FALLBACK_COLOR
    return TYPE_COLORS.get(node_type, FALLBACK_COLOR)

"""C3 (v4.3) Task 7: nebula-highlight HONESTY -- wording only.

The cosmos.gl graph-view highlight listener is a documented CLOSED
ceiling (reference_cosmos_gl_nebula, gotcha 31 TOMBSTONE -- it was
reverted; re-wiring froze the renderer). v4.x does NOT re-wire it.
The fix is honesty: the companion must not tell the user it
"highlighted nodes on the live Nebula graph" (which does nothing
visible there) -- mnemo_highlight_nodes surfaces them in the SIDE
PANEL (the off-renderer surface that actually works, kept in v3.2).
ZERO graph.html / cosmos / renderer change.
"""

from pathlib import Path

_MNEMO = Path(__file__).resolve().parents[2] / "mnemo"
AGENT_TOOLS = (_MNEMO / "agent_tools.py").read_text(encoding="utf-8").lower()


def test_highlight_wording_does_not_claim_the_live_graph() -> None:
    # the dishonest claims are gone:
    assert "on the live nebula graph" not in AGENT_TOOLS
    assert "light up the subgraph on nebula" not in AGENT_TOOLS
    assert "show it on the graph" not in AGENT_TOOLS


def test_highlight_wording_is_honest_about_the_side_panel() -> None:
    # the honest surface is named so the model relays the truth:
    assert "side panel" in AGENT_TOOLS
    # the tool still exists + is the off-renderer surface (not removed):
    assert "mnemo_highlight_nodes" in AGENT_TOOLS

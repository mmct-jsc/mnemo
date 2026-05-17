"""v4.5 (TOMBSTONE chapter): the nebula-highlight honesty loop is CLOSED.

History: v3.2/C3 could not make the graph itself highlight (the
cosmos.gl renderer's documented closed ceiling -- gotcha 31; every
wiring attempt re-triggered the freeze). So C3's fix was *honesty*:
the companion was told NOT to claim a graph-view highlight and to
say it surfaced the nodes "in the side panel" instead.

v4.5 swapped the renderer to sigma.js v3 + graphology. sigma's
nodeReducer makes a highlight a pure data change (the exact
capability cosmos lacked), so the graph ITSELF now lights up. This
is contract EVOLUTION, not weakening: the honesty constraint is
satisfied by making the once-dishonest claim TRUE and backing it
with real wiring -- not by forbidding the claim.

The teeth now:
  1. agent_tools no longer carries the "do NOT claim a graph-view
     highlight / side panel, not the graph" caveat.
  2. agent_tools truthfully says mnemo_highlight_nodes lights the
     live Nebula graph.
  3. that claim is BACKED by real wiring: graph.html listens for the
     mnemo-highlight-nodes CustomEvent and drives a sigma highlight,
     and the tool/dispatch chain is intact end to end.
"""

from pathlib import Path

_MNEMO = Path(__file__).resolve().parents[2] / "mnemo"
AGENT_TOOLS_RAW = (_MNEMO / "agent_tools.py").read_text(encoding="utf-8")
AGENT_TOOLS = AGENT_TOOLS_RAW.lower()
GRAPH_HTML = (_MNEMO / "ui" / "templates" / "graph.html").read_text(encoding="utf-8")
CHAT_JS = (_MNEMO / "ui" / "static" / "chat.js").read_text(encoding="utf-8")


def test_dishonest_caveat_is_gone() -> None:
    """The v3.2 'it does NOT light the graph; never claim a graph-view
    highlight; surfaced in the side panel, not the graph' caveat must
    be gone -- it is no longer true (v4.5 makes the graph highlight
    real)."""
    assert "do not tell the user it lit" not in AGENT_TOOLS
    assert "never claim a graph-view highlight" not in AGENT_TOOLS
    assert "not the graph" not in AGENT_TOOLS or "side panel, not the graph" in AGENT_TOOLS
    # the only permitted "side panel, not the graph" mention is the
    # historical note explaining that v4.5 CLOSED that caveat.
    if '"side panel, not the graph"' in AGENT_TOOLS:
        assert "closes the old gotcha-31" in AGENT_TOOLS or "v4.5" in AGENT_TOOLS


def test_wording_truthfully_claims_the_live_graph() -> None:
    """mnemo_highlight_nodes must now say -- truthfully -- that it
    highlights on the live Nebula graph."""
    assert "on the live nebula graph" in AGENT_TOOLS
    assert "mnemo_highlight_nodes" in AGENT_TOOLS_RAW
    # the tool still emits the _ui sentinel the loop depends on.
    assert '_ui("highlight_nodes"' in AGENT_TOOLS_RAW


def test_claim_is_backed_by_real_graph_wiring() -> None:
    """The honest claim is only honest because graph.html ACTUALLY
    listens for the companion's highlight/select CustomEvents and
    drives the sigma renderer -- the closed gotcha-31 loop. (chat.js
    redispatches the SSE ui_action as these document events.)"""
    assert "addEventListener('mnemo-highlight-nodes'" in GRAPH_HTML, (
        "graph.html must listen for mnemo-highlight-nodes and call "
        "highlight() -- the real graph highlight (v4.5 closed loop)."
    )
    assert "this.highlight(" in GRAPH_HTML, (
        "the listener must drive the renderer highlight via highlight()."
    )
    # Contract evolution v4.5 -> v4.6: the highlight is a real
    # graph-renderer data change (the capability cosmos.gl lacked).
    # v4.5 did it via a 2D-renderer node reducer; v4.6's custom WebGL
    # engine does it via the handle's setHighlight() -- still a pure
    # data change, now structurally (no per-element callback).
    assert "setHighlight" in GRAPH_HTML, (
        "highlight() must drive the renderer handle's setHighlight() "
        "-- the graph itself lights up (the closed gotcha-31 loop)."
    )
    # the dispatch half is still emitted by chat.js (the _ui ->
    # ui_action SSE -> CustomEvent chain).
    assert "mnemo-highlight-nodes" in CHAT_JS

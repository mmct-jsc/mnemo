"""v5.0.1 hotfix: pick index goes stale after a node drag.

Symptom (user-reported on the live dock): drag a node to a new
position; then click the same node at its new location -- the
click is ignored, no focus event fires.

Root cause: ``buildPickIndex`` (in nebula-gl.js) bins each node
into a uniform grid by its (x, y) on construction. The drag move
handler updates ``nodes[i].x/y`` in place but never re-buckets
the node in the spatial index. The cursor at the node's NEW
position computes a different grid cell key -- the node isn't in
any of the searched buckets -- ``pick.nearest`` returns -1 -- no
``clickNode`` event fires.

This test pins the fix in two layers:

1. The pick index exposes a ``reindex(id)`` method that moves a
   node from its old bucket to the bucket implied by its current
   ``nodes[id].x/y``.
2. The drag move handler calls that ``reindex`` after writing the
   new position, BEFORE the next click can land on the moved
   node.

A live browser test would be more authoritative but the dock has
no JS test runner; template-grep asserts the structural fix is
present and rules out a future revert.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
NEBULA_JS = REPO_ROOT / "daemon" / "mnemo" / "ui" / "static" / "vendor" / "nebula-gl.js"


def _read() -> str:
    return NEBULA_JS.read_text(encoding="utf-8")


def test_pick_index_exposes_reindex_method() -> None:
    """``buildPickIndex`` must return an object with a ``reindex``
    method (alongside the existing ``nearest``)."""
    js = _read()
    # The method is exposed on the returned object.
    assert "reindex:" in js or "reindex(" in js, (
        "buildPickIndex must expose a reindex method to re-bucket dragged nodes"
    )


def test_reindex_uses_node_current_position() -> None:
    """``reindex(id)`` recomputes the grid cell from
    ``nodes[id].x/y`` -- the post-drag truth. The fix must not
    accept (x, y) parameters that bypass the truth."""
    js = _read()
    # The method body must consult nodes[id] for the new position so
    # the drag handler doesn't have to keep a parallel state in sync.
    # Match either ``nodes[id]`` or any equivalent index-based read.
    reindex_start = js.find("reindex")
    assert reindex_start >= 0
    # Window of ~600 chars covering the method body
    window = js[reindex_start : reindex_start + 600]
    assert "nodes[id]" in window or "nodes[" in window, (
        "reindex must consult nodes[id] for the new position"
    )


def test_drag_move_calls_reindex_after_position_update() -> None:
    """The drag move handler updates ``nodes[dragId].x/y`` and must
    immediately call ``pick.reindex(dragId)`` so subsequent picks
    find the node at its new position."""
    js = _read()
    # Find the drag-position update site
    drag_idx = js.find("nodes[dragId].x = w.x")
    assert drag_idx >= 0, "drag handler should write nodes[dragId].x = w.x"
    # Within the same block (a few hundred chars after), reindex
    # must be invoked.
    drag_window = js[drag_idx : drag_idx + 600]
    assert "pick.reindex" in drag_window or "reindex(dragId)" in drag_window, (
        "drag-move handler must call pick.reindex(dragId) after updating "
        "nodes[dragId] so the spatial index reflects the new position"
    )


def test_drag_move_reindex_precedes_invalidate() -> None:
    """Order matters: reindex must run BEFORE invalidate() in the
    same iteration. Otherwise the next mousemove (hover pick) could
    fire on the OLD stale bucket before we re-bucket."""
    js = _read()
    drag_idx = js.find("nodes[dragId].x = w.x")
    drag_window = js[drag_idx : drag_idx + 1000]
    reindex_pos = drag_window.find("pick.reindex")
    invalidate_pos = drag_window.find("invalidate()")
    assert reindex_pos >= 0
    assert invalidate_pos >= 0
    assert reindex_pos < invalidate_pos, (
        "pick.reindex must run before invalidate() so the next frame's "
        "hover pick sees the updated bucket"
    )

"""v5.23.0 Phase 4b -- the deterministic orphan-fix (pure layer).

``strip_dead_citations`` removes only the given dead ``[mnemo:<id>]``
tokens from a body, leaving valid citations + all other text.
``is_node_id_shaped`` gates the fix to real 32-hex node ids so
documentation placeholders (``[mnemo:<id>]`` / ``[mnemo:node_id]``) are
never auto-strippable.
"""

from __future__ import annotations

import time

import pytest

from mnemo.apply import (
    FindingNotFoundError,
    NotApplyableError,
    StalePreviewError,
    apply_orphan_fix,
    is_node_id_shaped,
    preview_orphan_fix,
    strip_dead_citations,
)
from mnemo.store import Node, Store, _finding_fingerprint

_HEX = "a86e4261dfea499383713577fedf95d7"  # a real-shaped 32-hex id


def test_id_shaped_accepts_32hex() -> None:
    assert is_node_id_shaped(_HEX) is True


def test_id_shaped_refuses_placeholders() -> None:
    for ph in ("<id>", "id", "ID", "node_id", "<node_id>", ""):
        assert is_node_id_shaped(ph) is False, ph


def test_id_shaped_refuses_wrong_length_or_chars() -> None:
    assert is_node_id_shaped("a86e4261") is False  # too short
    assert is_node_id_shaped(_HEX.upper()) is False  # uppercase
    assert is_node_id_shaped("g" + _HEX[1:]) is False  # non-hex char


def test_strip_removes_only_the_dead_token() -> None:
    body = f"See [mnemo:{_HEX}] for details."
    new, removed = strip_dead_citations(body, [_HEX])
    assert "[mnemo:" not in new
    assert new == "See for details."
    assert removed == [_HEX]


def test_strip_keeps_valid_citations() -> None:
    dead = "f" * 32
    live = "a" * 32
    body = f"good [mnemo:{live}] and bad [mnemo:{dead}] end"
    new, removed = strip_dead_citations(body, [dead])
    assert f"[mnemo:{live}]" in new, "a valid citation must be untouched"
    assert f"[mnemo:{dead}]" not in new, "the dead citation must be removed"
    assert removed == [dead]


def test_strip_only_reports_ids_actually_present() -> None:
    body = "no citations here"
    new, removed = strip_dead_citations(body, ["a" * 32])
    assert new == body
    assert removed == []


def test_strip_handles_multiple_occurrences() -> None:
    dead = "b" * 32
    body = f"[mnemo:{dead}] x [mnemo:{dead}]"
    new, removed = strip_dead_citations(body, [dead])
    assert f"[mnemo:{dead}]" not in new
    assert removed == [dead], "an id present multiple times is reported once"


def test_strip_empty_dead_list_is_noop() -> None:
    body = f"keep [mnemo:{_HEX}]"
    new, removed = strip_dead_citations(body, [])
    assert new == body
    assert removed == []


# --- service layer: preview + apply over a queued finding -------------

DEAD = "d" * 32
LIVE = "a" * 32


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "mnemo.db")
    yield s
    s.close()


def _mknode(store: Store, *, id: str, body: str, name: str = "n") -> None:
    now = int(time.time())
    store.upsert_node(
        Node(
            id=id,
            type="memory_feedback",
            name=name,
            description="",
            body=body,
            source_path=f"/m/{id}.md",
            source_kind="memory",
            project_key=None,
            frontmatter_json=None,
            hash="h-" + id,
            created_at=now,
            updated_at=now,
        )
    )


def _seed_orphan(store: Store, *, node_id: str, missing: list[str]) -> str:
    finding = {
        "type": "orphan_reference",
        "node_ids": [node_id],
        "description": "cites missing",
        "severity": "high",
        "missing_targets": sorted(missing),
    }
    store.reconcile_audit_queue([finding], ("orphan_reference",))
    return _finding_fingerprint(finding)


def test_preview_applyable_real_dead_citation(store) -> None:
    _mknode(store, id="A", body=f"See [mnemo:{DEAD}] now.")
    fp = _seed_orphan(store, node_id="A", missing=[DEAD])
    pv = preview_orphan_fix(store, fp)
    assert pv["applyable"] is True
    assert pv["removed"] == [DEAD]
    assert f"[mnemo:{DEAD}]" not in pv["after"]
    assert pv["before"] == f"See [mnemo:{DEAD}] now."
    assert pv["node_hash"]
    # read-only: a preview must NOT change the finding's status
    assert store.get_audit_finding(fp).status == "open"


def test_preview_refuses_placeholder(store) -> None:
    _mknode(store, id="B", body="cite as [mnemo:<id>] in docs")
    fp = _seed_orphan(store, node_id="B", missing=["<id>"])
    pv = preview_orphan_fix(store, fp)
    assert pv["applyable"] is False
    assert "placeholder" in (pv["reason"] or "").lower()
    assert pv["removed"] == []


def test_preview_not_applyable_when_target_exists_again(store) -> None:
    _mknode(store, id="C", body=f"See [mnemo:{LIVE}].")
    _mknode(store, id=LIVE, body="the target exists now")
    fp = _seed_orphan(store, node_id="C", missing=[LIVE])
    pv = preview_orphan_fix(store, fp)
    assert pv["applyable"] is False


def test_preview_unknown_fingerprint_raises(store) -> None:
    with pytest.raises(FindingNotFoundError):
        preview_orphan_fix(store, "does-not-exist")


def test_apply_happy_path_edits_body_and_resolves(store) -> None:
    _mknode(store, id="A", body=f"See [mnemo:{DEAD}] now.")
    fp = _seed_orphan(store, node_id="A", missing=[DEAD])
    pv = preview_orphan_fix(store, fp)
    res = apply_orphan_fix(store, fp, pv["node_hash"])
    assert res["applied"] is True
    assert res["removed"] == [DEAD]
    assert f"[mnemo:{DEAD}]" not in store.get_node("A").body
    assert store.get_audit_finding(fp).status == "resolved"


def test_apply_stale_hash_refuses_and_does_not_edit(store) -> None:
    _mknode(store, id="A", body=f"See [mnemo:{DEAD}] now.")
    fp = _seed_orphan(store, node_id="A", missing=[DEAD])
    with pytest.raises(StalePreviewError):
        apply_orphan_fix(store, fp, "stale-hash-from-a-different-state")
    assert f"[mnemo:{DEAD}]" in store.get_node("A").body, "node must be untouched"
    assert store.get_audit_finding(fp).status == "open"


def test_apply_placeholder_raises_not_applyable(store) -> None:
    _mknode(store, id="B", body="cite as [mnemo:<id>] in docs")
    fp = _seed_orphan(store, node_id="B", missing=["<id>"])
    pv = preview_orphan_fix(store, fp)
    with pytest.raises(NotApplyableError):
        apply_orphan_fix(store, fp, pv["node_hash"])


def test_apply_keeps_valid_citation(store) -> None:
    _mknode(store, id="D", body=f"good [mnemo:{LIVE}] bad [mnemo:{DEAD}] end")
    _mknode(store, id=LIVE, body="the live target")
    fp = _seed_orphan(store, node_id="D", missing=[DEAD])
    pv = preview_orphan_fix(store, fp)
    apply_orphan_fix(store, fp, pv["node_hash"])
    body = store.get_node("D").body
    assert f"[mnemo:{LIVE}]" in body
    assert f"[mnemo:{DEAD}]" not in body

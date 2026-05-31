"""v5.22.0 Phase 4a -- audit_queue store layer.

The proactive auditor persists deterministic findings into a new
``audit_queue`` table keyed by a stable fingerprint, with a three-state
lifecycle (open / dismissed / resolved). Reconcile is scope-guarded so a
detector type the audit did not run is never wrongly auto-resolved.

ZERO node mutation -- only the queue table changes (Phase 4a anti-goal).
"""

from __future__ import annotations

import pytest

from mnemo.store import Store, _finding_fingerprint, _finding_locus


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "mnemo.db")
    yield s
    s.close()


def _stale(node_id: str) -> dict:
    return {
        "type": "stale",
        "node_ids": [node_id],
        "description": f"Node {node_id!r} marks SUPERSEDED.",
        "severity": "low",
    }


def _orphan(node_id: str, missing: list[str]) -> dict:
    return {
        "type": "orphan_reference",
        "node_ids": [node_id],
        "description": f"Node {node_id!r} cites missing targets.",
        "severity": "high",
        "missing_targets": sorted(missing),
    }


def _fp(finding: dict) -> str:
    return _finding_fingerprint(finding)


# --- fingerprint + locus helpers --------------------------------------


def test_fingerprint_stable_and_order_independent() -> None:
    f1 = {"type": "duplicates", "node_ids": ["a", "b"], "severity": "medium"}
    f2 = {"type": "duplicates", "node_ids": ["b", "a"], "severity": "medium"}
    assert _finding_fingerprint(f1) == _finding_fingerprint(f2)
    f3 = {"type": "duplicates", "node_ids": ["a", "c"], "severity": "medium"}
    assert _finding_fingerprint(f1) != _finding_fingerprint(f3)


def test_fingerprint_includes_locus() -> None:
    base = {"type": "orphan_reference", "node_ids": ["a"]}
    f1 = {**base, "missing_targets": ["x"]}
    f2 = {**base, "missing_targets": ["y"]}
    assert _finding_fingerprint(f1) != _finding_fingerprint(f2)


def test_finding_locus_priority() -> None:
    assert _finding_locus({"missing_targets": ["b", "a"]}) == "a,b"
    assert _finding_locus({"concept": "FooBar"}) == "FooBar"
    assert _finding_locus({"symbol": "_helper"}) == "_helper"
    assert _finding_locus({"type": "stale", "node_ids": ["x"]}) is None


# --- reconcile lifecycle ----------------------------------------------


def test_reconcile_inserts_open(store) -> None:
    counts = store.reconcile_audit_queue([_stale("a")], ("stale",))
    assert counts["new"] == 1
    rows = store.list_audit_queue(status="open", limit=25, offset=0)
    assert len(rows) == 1
    assert rows[0].type == "stale"
    assert rows[0].status == "open"
    assert rows[0].node_ids == ["a"]
    assert rows[0].severity == "low"


def test_rerun_bumps_last_seen_keeps_status(store) -> None:
    store.reconcile_audit_queue([_stale("a")], ("stale",), now=1000)
    counts = store.reconcile_audit_queue([_stale("a")], ("stale",), now=2000)
    assert counts["unchanged"] == 1
    assert counts["new"] == 0
    rows = store.list_audit_queue(status="open", limit=25, offset=0)
    assert len(rows) == 1, "re-detecting the same finding must not duplicate the row"
    assert rows[0].first_seen == 1000
    assert rows[0].last_seen == 2000
    assert rows[0].status == "open"


def test_disappeared_in_scope_finding_resolved(store) -> None:
    store.reconcile_audit_queue([_stale("a")], ("stale",))
    counts = store.reconcile_audit_queue([], ("stale",))
    assert counts["resolved"] == 1
    assert store.count_audit_queue("open") == 0
    assert store.count_audit_queue("resolved") == 1


def test_dismissed_sticky_on_redetect(store) -> None:
    store.reconcile_audit_queue([_stale("a")], ("stale",))
    assert store.set_audit_finding_status(_fp(_stale("a")), "dismissed") is True
    store.reconcile_audit_queue([_stale("a")], ("stale",))
    rows = store.list_audit_queue(status=None, limit=25, offset=0)
    assert len(rows) == 1
    assert rows[0].status == "dismissed", "a re-detected dismissed finding stays dismissed"


def test_dismissed_not_resolved_when_absent(store) -> None:
    store.reconcile_audit_queue([_stale("a")], ("stale",))
    store.set_audit_finding_status(_fp(_stale("a")), "dismissed")
    # next audit no longer produces it -- dismissed must NOT flip to resolved
    store.reconcile_audit_queue([], ("stale",))
    assert store.count_audit_queue("dismissed") == 1
    assert store.count_audit_queue("resolved") == 0


def test_resolved_reopens_on_redetect(store) -> None:
    store.reconcile_audit_queue([_stale("a")], ("stale",))
    store.reconcile_audit_queue([], ("stale",))  # -> resolved
    assert store.count_audit_queue("resolved") == 1
    counts = store.reconcile_audit_queue([_stale("a")], ("stale",))  # reappears
    assert counts["reopened"] == 1
    assert store.count_audit_queue("open") == 1
    assert store.count_audit_queue("resolved") == 0


def test_out_of_scope_type_never_resolved(store) -> None:
    store.reconcile_audit_queue([_orphan("b", ["x"])], ("orphan_reference",))
    # a later audit scoped ONLY to stale produces no orphan finding;
    # orphan_reference is out of scope -> must NOT be resolved
    store.reconcile_audit_queue([_stale("a")], ("stale",))
    assert store.count_audit_queue("open") == 2
    statuses = {r.type: r.status for r in store.list_audit_queue(status=None, limit=25, offset=0)}
    assert statuses["orphan_reference"] == "open"
    assert statuses["stale"] == "open"


# --- list / count / status --------------------------------------------


def test_list_pagination(store) -> None:
    findings = [_stale(f"n{i}") for i in range(30)]
    store.reconcile_audit_queue(findings, ("stale",))
    page1 = store.list_audit_queue(status="open", limit=25, offset=0)
    page2 = store.list_audit_queue(status="open", limit=25, offset=25)
    assert len(page1) == 25
    assert len(page2) == 5
    fps = {r.fingerprint for r in page1} | {r.fingerprint for r in page2}
    assert len(fps) == 30, "pages must not overlap"


def test_status_filter(store) -> None:
    store.reconcile_audit_queue([_stale("a"), _stale("b")], ("stale",))
    store.set_audit_finding_status(_fp(_stale("a")), "dismissed")
    open_rows = store.list_audit_queue(status="open", limit=25, offset=0)
    dismissed_rows = store.list_audit_queue(status="dismissed", limit=25, offset=0)
    assert {r.node_ids[0] for r in open_rows} == {"b"}
    assert {r.node_ids[0] for r in dismissed_rows} == {"a"}


def test_counts(store) -> None:
    store.reconcile_audit_queue([_stale("a"), _stale("b"), _stale("c")], ("stale",))
    store.set_audit_finding_status(_fp(_stale("a")), "dismissed")
    store.reconcile_audit_queue([_stale("b"), _stale("c")], ("stale",))
    counts = store.audit_queue_counts()
    assert counts == {"open": 2, "dismissed": 1, "resolved": 0}
    assert store.count_audit_queue() == 3
    assert store.count_audit_queue("open") == 2


def test_set_status_returns_bool(store) -> None:
    store.reconcile_audit_queue([_stale("a")], ("stale",))
    assert store.set_audit_finding_status(_fp(_stale("a")), "dismissed") is True
    assert store.set_audit_finding_status("does-not-exist", "dismissed") is False


def test_set_status_rejects_invalid(store) -> None:
    store.reconcile_audit_queue([_stale("a")], ("stale",))
    with pytest.raises(ValueError, match="unknown audit status"):
        store.set_audit_finding_status(_fp(_stale("a")), "bogus")

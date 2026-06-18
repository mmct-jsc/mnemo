"""v6.1.0 G3: the evidence ledger that makes "mandatory" real.

A mandatory step (verify / review) is satisfied ONLY by captured evidence,
and the evidence must be FRESH -- stamped after the most recent edit in the
session. So an agent cannot run `ruff` once, then make a bad edit, then
commit: the later edit re-opens the gate until the verify is re-run. This is
the teeth behind the owner's "evidence-based" decision + the operator-green
completion rule.
"""

from __future__ import annotations

from pathlib import Path

from mnemo.store import Store


def test_gate_unsatisfied_without_evidence(tmp_path: Path) -> None:
    s = Store(tmp_path / "t.db")
    assert s.gate_satisfied("sess", "rule.x", "verify") is False
    s.close()


def test_gate_satisfied_with_evidence_and_no_touch(tmp_path: Path) -> None:
    s = Store(tmp_path / "t.db")
    s.record_governance_evidence(session_id="sess", rule_id="rule.x", step="verify", at=100)
    assert s.gate_satisfied("sess", "rule.x", "verify") is True
    s.close()


def test_evidence_after_touch_satisfies(tmp_path: Path) -> None:
    s = Store(tmp_path / "t.db")
    s.record_touched_file("sess", "a.py", at=100)
    s.record_governance_evidence(session_id="sess", rule_id="rule.x", step="verify", at=200)
    assert s.gate_satisfied("sess", "rule.x", "verify") is True
    s.close()


def test_touch_after_evidence_is_stale(tmp_path: Path) -> None:
    s = Store(tmp_path / "t.db")
    s.record_governance_evidence(session_id="sess", rule_id="rule.x", step="verify", at=100)
    s.record_touched_file("sess", "a.py", at=200)
    assert s.gate_satisfied("sess", "rule.x", "verify") is False, (
        "an edit after the verify must re-open the gate (evidence is stale)"
    )
    s.close()


def test_failed_evidence_does_not_satisfy(tmp_path: Path) -> None:
    s = Store(tmp_path / "t.db")
    s.record_governance_evidence(
        session_id="sess", rule_id="rule.x", step="verify", status="failed", at=100
    )
    assert s.gate_satisfied("sess", "rule.x", "verify") is False
    s.close()


def test_evidence_is_scoped_per_session(tmp_path: Path) -> None:
    s = Store(tmp_path / "t.db")
    s.record_governance_evidence(session_id="s1", rule_id="r", step="verify", at=100)
    assert s.gate_satisfied("s2", "r", "verify") is False
    s.close()


def test_touched_files_listing(tmp_path: Path) -> None:
    s = Store(tmp_path / "t.db")
    s.record_touched_file("sess", "a.py", at=100)
    s.record_touched_file("sess", "b.py", at=101)
    s.record_touched_file("other", "c.py", at=102)
    assert set(s.governance_touched_files("sess")) == {"a.py", "b.py"}
    s.close()


def test_record_evidence_is_idempotent_per_step(tmp_path: Path) -> None:
    s = Store(tmp_path / "t.db")
    s.record_governance_evidence(session_id="sess", rule_id="r", step="verify", at=100)
    s.record_governance_evidence(session_id="sess", rule_id="r", step="verify", at=300)
    # re-stamping updates in place (PK session+rule+step), still satisfied
    assert s.gate_satisfied("sess", "r", "verify") is True
    s.close()

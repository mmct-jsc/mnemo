"""v6.1.0 G4: the enforcement decision (PreToolUse / Stop).

``evaluate_gate`` decides whether a tool call is blocked by a governance rule:
a *gate* rule (``requires_step``) blocks until that step has fresh evidence; a
*prohibition* rule (``enforcement: block`` with no step) blocks outright.
``evaluate_stop`` blocks session end while a touched file still has an
unsatisfied mandatory gate. Both are pure decisions over the store -- the
hooks (G4 wiring) translate them into Claude Code's permission contract and
apply the default-warn / bypass / fail-open policy.
"""

from __future__ import annotations

import json
from pathlib import Path

from mnemo import governance as gov
from mnemo.store import Node, Store


def _rule(store, *, name, block, base=True, project_key=None):
    n = Node.new(
        type="rule",
        name=name,
        description=f"{name} text",
        body="b",
        source_path=f"/m/{name}.md",
        source_kind="memory_dir",
        base=base,
        frontmatter_json=json.dumps({"rule": block}),
    )
    n.project_key = project_key
    store.upsert_node(n)
    return n


def test_verify_gate_denies_when_unsatisfied(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    _rule(
        store,
        name="verify-before-commit",
        block={
            "id": "rule.gate.verify",
            "modality": "MUST",
            "enforcement": "block",
            "requires_step": "verify",
            "verify": {"command": "uv run ruff check .", "expect_exit": 0},
            "applies_to": {"tool": ["Bash"], "tool_arg_match": "git commit"},
        },
    )
    d = gov.evaluate_gate(
        store, session_id="S", tool_name="Bash", tool_arg="git commit -m x", scope=None
    )
    assert d.blocked is True
    assert d.permission == "deny", "a verify gate (evidenceable) hard-denies"
    assert "rule.gate.verify" in d.rule_ids
    assert "ruff check" in d.reason
    store.close()


def test_review_gate_asks_not_denies(tmp_path: Path) -> None:
    """A `block` rule requiring review/ack has no programmatic satisfy path in
    v1, so it ASKS (defers to the human) rather than denying forever."""
    store = Store(tmp_path / "t.db")
    _rule(
        store,
        name="review-before-commit",
        block={
            "id": "rule.gate.review",
            "modality": "MUST",
            "enforcement": "block",
            "requires_step": "review",
            "applies_to": {"tool": ["Bash"], "tool_arg_match": "git commit"},
        },
    )
    d = gov.evaluate_gate(
        store, session_id="S", tool_name="Bash", tool_arg="git commit -m x", scope=None
    )
    assert d.blocked is True
    assert d.permission == "ask", "review gate has no satisfy path -> ask, never a deny-trap"
    store.close()


def test_gate_allows_when_step_satisfied(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    _rule(
        store,
        name="review-before-commit",
        block={
            "id": "rule.gate.review",
            "enforcement": "block",
            "requires_step": "review",
            "applies_to": {"tool": ["Bash"], "tool_arg_match": "git commit"},
        },
    )
    store.record_governance_evidence(session_id="S", rule_id="rule.gate.review", step="review")
    d = gov.evaluate_gate(
        store, session_id="S", tool_name="Bash", tool_arg="git commit -m x", scope=None
    )
    assert d.blocked is False
    assert d.permission == "allow"
    store.close()


def test_prohibition_rule_blocks_outright(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    _rule(
        store,
        name="no-force-push",
        block={
            "id": "rule.no-force-push",
            "modality": "MUST_NOT",
            "enforcement": "block",
            "applies_to": {"tool": ["Bash"], "tool_arg_match": "push --force"},
        },
    )
    d = gov.evaluate_gate(
        store, session_id="S", tool_name="Bash", tool_arg="git push --force", scope=None
    )
    assert d.blocked is True
    assert d.permission == "deny"
    store.close()


def test_require_ack_asks_rather_than_denies(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    _rule(
        store,
        name="ack-migrations",
        block={
            "id": "rule.ack.migration",
            "enforcement": "require-ack",
            "applies_to": {"glob": ["**/migrations/**"]},
        },
    )
    d = gov.evaluate_gate(
        store,
        session_id="S",
        tool_name="Edit",
        tool_arg="db/migrations/001.sql",
        file_paths=["db/migrations/001.sql"],
        scope=None,
    )
    assert d.blocked is True
    assert d.permission == "ask"
    store.close()


def test_non_matching_tool_is_allowed(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    _rule(
        store,
        name="review-before-commit",
        block={
            "id": "rule.gate.review",
            "enforcement": "block",
            "requires_step": "review",
            "applies_to": {"tool": ["Bash"], "tool_arg_match": "git commit"},
        },
    )
    d = gov.evaluate_gate(store, session_id="S", tool_name="Bash", tool_arg="ls -la", scope=None)
    assert d.blocked is False
    store.close()


def test_inform_and_warn_rules_never_block(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    _rule(
        store,
        name="soft",
        block={
            "id": "rule.soft",
            "enforcement": "warn",
            "applies_to": {"tool": ["Bash"], "tool_arg_match": "git commit"},
        },
    )
    d = gov.evaluate_gate(
        store, session_id="S", tool_name="Bash", tool_arg="git commit", scope=None
    )
    assert d.blocked is False, "only enforcement=block/require-ack gate; warn/inform never block"
    store.close()


def test_stop_blocks_when_touched_file_has_unsatisfied_gate(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    _rule(
        store,
        name="verify-py",
        block={
            "id": "rule.verify.py",
            "modality": "MUST",
            "enforcement": "block",
            "requires_step": "verify",
            "applies_to": {"glob": ["**/*.py"]},
        },
    )
    store.record_touched_file("S", "/repo/app.py", at=100)
    d = gov.evaluate_stop(store, session_id="S", scope=None)
    assert d.blocked is True
    assert "rule.verify.py" in d.rule_ids
    store.close()


def test_stop_allows_when_gate_satisfied(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    _rule(
        store,
        name="verify-py",
        block={
            "id": "rule.verify.py",
            "modality": "MUST",
            "enforcement": "block",
            "requires_step": "verify",
            "applies_to": {"glob": ["**/*.py"]},
        },
    )
    store.record_touched_file("S", "/repo/app.py", at=100)
    store.record_governance_evidence(
        session_id="S", rule_id="rule.verify.py", step="verify", at=200
    )
    d = gov.evaluate_stop(store, session_id="S", scope=None)
    assert d.blocked is False
    store.close()


def test_stop_allows_when_nothing_touched(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    d = gov.evaluate_stop(store, session_id="S", scope=None)
    assert d.blocked is False
    store.close()


def test_evaluate_gate_fails_open() -> None:
    class _Bad:
        def list_nodes(self, **kw):
            raise RuntimeError("db gone")

    d = gov.evaluate_gate(
        _Bad(), session_id="S", tool_name="Bash", tool_arg="git commit", scope=None
    )
    assert d.blocked is False, "a governance error must never block the tool (fail-open)"

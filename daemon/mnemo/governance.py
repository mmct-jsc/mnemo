"""Governance core (v6.1.0) -- rules, applicability, and gate evaluation.

mnemo's governance layer turns coding constraints, workflow gates, specs,
mandatory verification, and code review into first-class ``rule`` nodes that
are surfaced PRESCRIPTIVELY (the right MUST/MUST_NOT at the right moment) and
ENFORCED with teeth (a tool call / session end can be blocked until a
mandatory step is provably satisfied).

This module is the PURE core: parse a rule out of its frontmatter, decide
whether it applies to a context, rank it. No store, no model, no I/O -- so it
is reused identically by retrieval (surfacing), the hooks (enforcement), and
the analyzer lens (code review) without coupling them.

Fail-open is law: a malformed rule parses as a non-binding ``inform`` /
``SHOULD`` rule and NEVER raises, so a bad rule file cannot brick retrieval
or a hook. ``evaluate_gate`` (the enforcement decision) lands in G4.

Design: docs/plans/2026-06-18-mnemo-v6.1.0-governance-design.md
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from mnemo.store import Node

MODALITIES: tuple[str, ...] = ("MUST", "MUST_NOT", "SHOULD")
ENFORCEMENTS: tuple[str, ...] = ("inform", "warn", "require-ack", "block")

_MODALITY_RANK = {"MUST_NOT": 3, "MUST": 2, "SHOULD": 1}
_MANDATORY = {"MUST", "MUST_NOT"}


@dataclass
class Rule:
    id: str
    name: str
    node_id: str
    text: str
    modality: str  # MUST | MUST_NOT | SHOULD
    enforcement: str  # inform | warn | require-ack | block
    glob: list[str] = field(default_factory=list)
    intent: list[str] = field(default_factory=list)
    tool: list[str] = field(default_factory=list)
    tool_arg_match: str | None = None
    verify_command: str | None = None
    verify_expect_exit: int = 0
    requires_step: str | None = None  # review | verify | ack

    @property
    def is_mandatory(self) -> bool:
        return self.modality in _MANDATORY


def _norm_modality(raw: object) -> str:
    s = str(raw or "").strip().upper()
    return s if s in MODALITIES else "SHOULD"


def _norm_enforcement(raw: object) -> str:
    s = str(raw or "").strip().lower()
    return s if s in ENFORCEMENTS else "inform"


def _str_list(raw: object) -> list[str]:
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, Iterable):
        return [str(x) for x in raw]
    return []


def parse_rule(fm: dict, *, name: str = "", node_id: str = "", text: str = "") -> Rule:
    """Parse a rule from its (whole-frontmatter) dict. Always returns a Rule;
    a missing/garbage ``rule:`` block yields a non-binding advisory rule."""
    block = fm.get("rule")
    if not isinstance(block, dict):
        block = {}
    applies = block.get("applies_to")
    if not isinstance(applies, dict):
        applies = {}
    verify = block.get("verify")
    if not isinstance(verify, dict):
        verify = {}
    try:
        expect_exit = int(verify.get("expect_exit", 0))
    except (TypeError, ValueError):
        expect_exit = 0
    arg_match = applies.get("tool_arg_match")
    cmd = verify.get("command")
    step = block.get("requires_step")
    return Rule(
        id=str(block.get("id") or name),
        name=name,
        node_id=node_id,
        text=text,
        modality=_norm_modality(block.get("modality")),
        enforcement=_norm_enforcement(block.get("enforcement")),
        glob=_str_list(applies.get("glob")),
        intent=_str_list(applies.get("intent")),
        tool=_str_list(applies.get("tool")),
        tool_arg_match=str(arg_match) if arg_match else None,
        verify_command=str(cmd) if cmd else None,
        verify_expect_exit=expect_exit,
        requires_step=str(step) if step else None,
    )


def rule_from_node(node: Node) -> Rule | None:
    """Convenience: read a ``rule``-type Node's whole frontmatter and parse.
    Returns None for non-rule nodes. Never raises on bad frontmatter."""
    if getattr(node, "type", None) != "rule":
        return None
    import json

    fm: dict = {}
    raw = getattr(node, "frontmatter_json", None)
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                fm = loaded
        except (ValueError, TypeError):
            fm = {}
    return parse_rule(
        fm,
        name=getattr(node, "name", "") or "",
        node_id=getattr(node, "id", "") or "",
        text=(getattr(node, "description", None) or getattr(node, "body", "") or ""),
    )


def _glob_match(pattern: str, path: str) -> bool:
    p = (path or "").replace("\\", "/")
    pat = (pattern or "").replace("\\", "/")
    base = p.rsplit("/", 1)[-1]
    return fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(base, pat)


def rule_applies(
    rule: Rule,
    *,
    glob_path: str | None = None,
    intent_tags: set[str] | frozenset[str] | None = None,
    tool_name: str | None = None,
    tool_arg: str | None = None,
) -> bool:
    """Does ``rule`` bind for this context? A rule with NO declared triggers is
    universal (applies everywhere -- pair with ``base: true`` for a global
    rule). Otherwise it applies if ANY declared trigger dimension matches the
    provided context (OR semantics); a declared dimension the context can't
    satisfy simply doesn't contribute."""
    has_glob, has_intent, has_tool = bool(rule.glob), bool(rule.intent), bool(rule.tool)
    if not (has_glob or has_intent or has_tool):
        return True

    if has_glob and glob_path and any(_glob_match(g, glob_path) for g in rule.glob):
        return True
    if has_intent and intent_tags and (set(rule.intent) & set(intent_tags)):
        return True
    if has_tool and tool_name and tool_name in rule.tool:
        if rule.tool_arg_match is None:
            return True
        if tool_arg is not None:
            try:
                if re.search(rule.tool_arg_match, tool_arg):
                    return True
            except re.error:
                # a bad regex in a rule must not crash the gate -> substring fallback
                if rule.tool_arg_match in tool_arg:
                    return True
    return False


def modality_rank(rule: Rule) -> int:
    """Sort key: MUST_NOT (3) > MUST (2) > SHOULD (1). Higher binds harder."""
    return _MODALITY_RANK.get(rule.modality, 0)


def _in_scope(node: Node, scope: set[str] | frozenset[str] | None) -> bool:
    """A rule surfaces across project boundaries when it is BASE or has no
    project_key (cross-cutting); otherwise only when its project is in scope.
    ``scope=None`` means no project filter (everything is in scope)."""
    if scope is None or getattr(node, "base", False):
        return True
    pk = getattr(node, "project_key", None)
    return pk is None or pk in scope


def active_rules(
    store,
    *,
    scope: set[str] | frozenset[str] | None = None,
    intent_tags: set[str] | frozenset[str] | None = None,
    file_paths: list[str] | None = None,
    tool_name: str | None = None,
    tool_arg: str | None = None,
    limit: int = 5,
    mandatory_only: bool = False,
) -> list[Rule]:
    """Fetch the rules that BIND for a context, sorted mandatory-first.

    A deterministic, fail-open fetch over the (small) rule corpus -- separate
    from embedding retrieval, so applicable MUST/MUST_NOT rules surface
    regardless of any ranked-injection budget. Reused by the UserPromptSubmit
    injection (intent + universal rules) and the PreToolUse gate (file/tool
    context). Any store/parse error yields ``[]`` -- governance must never
    brick the caller.
    """
    try:
        nodes = store.list_nodes(type="rule", limit=10000)
    except Exception:
        return []
    paths: list[str | None] = list(file_paths) if file_paths else [None]
    tags = set(intent_tags or ())
    out: list[Rule] = []
    for node in nodes:
        try:
            if not _in_scope(node, scope):
                continue
            rule = rule_from_node(node)
            if rule is None:
                continue
            if mandatory_only and not rule.is_mandatory:
                continue
            if any(
                rule_applies(
                    rule,
                    glob_path=p,
                    intent_tags=tags,
                    tool_name=tool_name,
                    tool_arg=tool_arg,
                )
                for p in paths
            ):
                out.append(rule)
        except Exception:
            # a single bad rule must not sink the whole fetch
            continue
    out.sort(key=modality_rank, reverse=True)
    return out[:limit] if limit else out

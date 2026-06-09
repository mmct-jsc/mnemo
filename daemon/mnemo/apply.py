"""v5.23.0 Phase 4b -- the deterministic orphan-fix (confirm-then-apply).

The FIRST node mutation in mnemo's Understanding arc. Given an
``orphan_reference`` finding from the audit queue, this removes the dead
``[mnemo:<id>]`` citation token(s) from the node body via the existing
``Store.update_node`` path. Strictly deterministic -- no LLM, no proposer.

Two safety gates live here:

1. **id-shape gate** (:func:`is_node_id_shaped`): only real 32-hex node ids
   are ever strippable, so a documentation placeholder (``[mnemo:<id>]`` /
   ``[mnemo:node_id]``) -- which TEACHES the citation format -- is never
   auto-removed from a doc.
2. **preview -> confirm handshake** (:func:`preview_orphan_fix` /
   :func:`apply_orphan_fix`, added with the queue lookup): the apply
   re-verifies the node's content hash matches the previewed one, so an
   apply can never silently land on a node that changed since preview.

This module owns the pure helpers; the preview/apply service is layered on
top. Kept out of ``analyzer.py`` (detection-only).
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mnemo.store import Store

# Real mnemo node ids are uuid4 hex -- 32 lowercase hex chars. Anything
# else (``<id>``, ``node_id``, ``id``) is treated as a documentation
# placeholder and refused by the orphan-fix.
_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def is_node_id_shaped(target: str) -> bool:
    """True iff ``target`` looks like a real mnemo node id (32-char lower
    hex). Documentation placeholders return False so the orphan-fix never
    strips a citation-format EXAMPLE out of a doc."""
    return bool(_ID_RE.match(target or ""))


def strip_dead_citations(body: str, dead_ids: list[str]) -> tuple[str, list[str]]:
    """Remove the ``[mnemo:<id>]`` token for every id in ``dead_ids`` from
    ``body`` (eating one adjacent leading whitespace run so ``"a [mnemo:x]
    b"`` -> ``"a b"``), leaving valid citations + all other text untouched.

    Returns ``(new_body, removed)`` where ``removed`` lists the ids that
    were actually present and stripped (deduped, in input order). Purely
    mechanical: the caller decides which ids are dead + id-shaped."""
    new_body = body or ""
    removed: list[str] = []
    for cid in dead_ids:
        token_re = re.compile(r"\s*\[mnemo:" + re.escape(cid) + r"\]")
        if token_re.search(new_body):
            new_body = token_re.sub("", new_body)
            removed.append(cid)
    return new_body, removed


# --- preview -> confirm service over a queued finding ------------------


class ApplyError(Exception):
    """Base for confirm-then-apply errors (surfaces map these to HTTP codes)."""


class FindingNotFoundError(ApplyError):
    """No queued finding with that fingerprint (-> 404)."""


class NotApplyableError(ApplyError):
    """The finding cannot be auto-applied -- placeholders only / already
    fixed / unsupported type (-> 422). ``reason`` is human-readable."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class StalePreviewError(ApplyError):
    """The node changed since the preview the caller confirmed (-> 409)."""


def _body_fingerprint(body: str) -> str:
    """Content fingerprint of the CURRENT body -- the confirm token for the
    preview->apply handshake. (The stored ``node.hash`` is computed at
    ingest and goes stale after an in-app edit, so hash the live body.)"""
    return hashlib.sha1((body or "").encode("utf-8")).hexdigest()


def _orphan_targets(finding) -> list[str]:
    # orphan_reference stores locus = ",".join(sorted(missing_targets)).
    return [t for t in (finding.locus or "").split(",") if t]


def preview_orphan_fix(store: Store, fingerprint: str) -> dict:
    """Compute (READ-ONLY) the deterministic orphan-fix for one queued
    finding. Returns ``{fingerprint, node_id, node_name, before, after,
    removed, applyable, reason, node_hash, finding_type}``. Never mutates a
    node or the queue. Raises :class:`FindingNotFoundError` for an unknown
    fingerprint."""
    finding = store.get_audit_finding(fingerprint)
    if finding is None:
        raise FindingNotFoundError(fingerprint)
    node_id = finding.node_ids[0] if finding.node_ids else None
    node = store.get_node(node_id) if node_id else None
    before = node.body if node else ""
    result: dict = {
        "fingerprint": fingerprint,
        "node_id": node_id,
        "node_name": node.name if node else None,
        "before": before,
        "after": before,
        "removed": [],
        "applyable": False,
        "reason": None,
        "node_hash": _body_fingerprint(before),
        "finding_type": finding.type,
    }
    if finding.type != "orphan_reference":
        result["reason"] = (
            f"apply is not supported for '{finding.type}' findings yet (orphan_reference only)"
        )
        return result
    if node is None:
        result["reason"] = "the citing node no longer exists"
        return result
    targets = _orphan_targets(finding)
    present = store.get_nodes_by_ids(targets) if targets else {}
    still_missing = [t for t in targets if t not in present]
    dead = [t for t in still_missing if is_node_id_shaped(t)]
    placeholders = [t for t in still_missing if not is_node_id_shaped(t)]
    after, removed = strip_dead_citations(before, dead)
    result["after"] = after
    result["removed"] = removed
    result["applyable"] = bool(removed)
    if not removed:
        if placeholders and not dead:
            result["reason"] = (
                "cited target(s) look like documentation placeholders "
                "(e.g. [mnemo:<id>]), not dead node ids -- declined"
            )
        elif not still_missing:
            result["reason"] = (
                "no longer orphaned -- the cited node exists again or the citation is gone"
            )
        else:
            result["reason"] = "nothing to strip in the node body"
    return result


def apply_orphan_fix(store: Store, fingerprint: str, confirm_node_hash: str) -> dict:
    """Apply the orphan-fix after the preview->confirm handshake: strip the
    dead citation token(s) from the node body (via ``upsert_node``) and mark
    the finding ``resolved``. The node graph is touched ONLY here, only on
    an explicit confirm whose ``confirm_node_hash`` still matches the live
    body. Raises :class:`FindingNotFoundError` / :class:`NotApplyableError` /
    :class:`StalePreviewError`."""
    pv = preview_orphan_fix(store, fingerprint)
    if not pv["applyable"]:
        raise NotApplyableError(pv["reason"] or "not applyable")
    if confirm_node_hash != pv["node_hash"]:
        raise StalePreviewError("the node changed since preview; re-preview and confirm again")
    node = store.get_node(pv["node_id"])
    if node is None:  # raced delete between preview + apply
        raise NotApplyableError("the citing node no longer exists")
    node.body = pv["after"]
    node.updated_at = int(time.time())
    store.upsert_node(node)
    store.set_audit_finding_status(fingerprint, "resolved")
    return {
        "applied": True,
        "fingerprint": fingerprint,
        "node_id": pv["node_id"],
        "removed": pv["removed"],
        "status": "resolved",
    }

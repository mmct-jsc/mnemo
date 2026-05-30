"""v5.12.0+ -- knowledge auditor: deterministic + LLM-augmented analysis.

mnemo's Understanding arc:

- **Phase 1 (v5.12.0)**: 3 deterministic detectors below
  (``detect_stale`` / ``detect_duplicates`` /
  ``detect_orphan_references``). No LLM, no API key, no new deps.
- **Phase 2a (v5.13.0)**: LLM-augmented ``detect_contradictions``
  -- deterministic candidate selection (cosine band + negation
  differential) optionally escalated to a Claude judge via
  opt-in env flag (``MNEMO_ANALYZE_LLM_JUDGE=1`` +
  ``ANTHROPIC_API_KEY``). Default path stays no-LLM.
- **Phase 2b (v5.14.0)**: LLM-augmented ``detect_semantic_orphans``
  -- per-node deterministic concept extraction (CamelCase,
  snake_case, ALL_CAPS) + cross-reference lookup, optionally
  escalated to a sibling Claude judge for binary "needs definition"
  grading. Reuses the same opt-in env flag.

See:
- ``docs/plans/2026-05-22-mnemo-understanding-phase1-design.md``
- ``docs/plans/2026-05-23-mnemo-understanding-phase2a-design.md``
- ``docs/plans/2026-05-23-mnemo-understanding-phase2b-design.md``
- ``memory/project_mnemo_v6_vision_understanding`` (the multi-
  release north star covering Phase 2c/3/4).

Detectors:

1. :func:`detect_stale` -- nodes whose body / description contain the
   literal ``SUPERSEDED`` token. Lexical, instant. Severity: low.
2. :func:`detect_duplicates` -- pairs of same-type nodes with cosine
   similarity >= 0.95. Uses sqlite-vec's chunk-level NN search +
   filters to within-type pairs only. Severity: medium.
3. :func:`detect_orphan_references` -- nodes whose body contains
   ``[mnemo:<id>]`` where ``<id>`` is not in the graph. Severity:
   high (broken citation).
4. :func:`detect_contradictions` (v5.13.0) -- same-type pairs in
   the [0.5, 0.85] cosine band with a negation-pattern
   differential. Default severity ``candidate``; elevated to
   ``high`` when the opt-in ``LLMContradictionJudge`` confirms;
   dropped entirely when the judge rejects.
5. :func:`detect_semantic_orphans` (v5.14.0) -- per-node concept
   extraction + cross-reference lookup. Concepts that no other
   node defines in its ``name`` or ``description`` surface as
   candidates. Default severity ``candidate``; elevated to
   ``high`` when the opt-in ``LLMSemanticOrphanJudge`` confirms;
   dropped entirely when the judge rejects.

The orchestrator :func:`analyze` runs the requested detectors
(filtered via ``types=``) and returns a canonical envelope:

    {
        "ran_at": "<ISO timestamp>",
        "node_count_scanned": <int>,
        "findings": [{type, node_ids, description, severity}, ...],
        "summary": {<type>: <count>, ...},
    }
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from mnemo.store import Node, Store

log = logging.getLogger(__name__)

# Per-type pages for ``Store.list_nodes``. We page through each
# type bucket so even a 4500-node bucket doesn't load everything at
# once. The default upper limit is generous enough that small
# buckets (memory_* are typically 10-200 nodes) finish in one
# fetch.
PAGE_SIZE = 1000

# Cosine-similarity threshold for the duplicates detector. 0.95 is
# the well-known sentence-transformers near-duplicate cutoff: 0.98
# misses real near-duplicates (different wording, same meaning);
# 0.90 false-positives on closely-related siblings.
#
# sqlite-vec uses L2 distance on normalized vectors:
#     cos = 1 - L2^2 / 2
# So cosine >= 0.95 <=> L2^2 <= 0.10 <=> L2 <= ~0.3162.
DUPLICATE_COSINE_THRESHOLD = 0.95
DUPLICATE_L2_THRESHOLD = (2 * (1 - DUPLICATE_COSINE_THRESHOLD)) ** 0.5

# Phase 1 only flags duplicates within these node types. Code nodes
# (code_function, code_method, ...) are intentionally skipped --
# tree-sitter already canonicalizes them + the dedup story for code
# is different (refactoring suggestions, not body merges).
DUPLICATE_TYPE_BUCKETS = (
    "memory_feedback",
    "memory_project",
    "memory_reference",
    "memory_user",
    "memory_session",
    "plan_doc",
    "project_doc",
    "session_summary",
)

# Regex for the canonical mnemo citation token. The v1.0+ convention
# is ``[mnemo:<id>]`` where ``<id>`` can be any URL-safe string.
_CITATION_RE = re.compile(r"\[mnemo:([^\]]+?)\]")

# Lexical marker for ``stale``. Case-insensitive match against the
# body OR the description. Matches our own session-handover
# convention ("SUPERSEDED by v5.X.X").
_STALE_MARKER_RE = re.compile(r"superseded", re.IGNORECASE)


# --- Phase 2a (v5.13.0) -- contradictions parameters --------------------

# Cosine band for candidate-pair selection. Below 0.5 the topics
# diverge enough that a real contradiction can't form; above 0.85
# it's near-duplicate territory (the ``duplicates`` detector owns
# that case). The [0.5, 0.85] sweet spot is "same topic, different
# prescription".
CONTRADICTION_COSINE_MIN = 0.5
CONTRADICTION_COSINE_MAX = 0.85

# sqlite-vec L2 distance on normalized vectors: L2 = sqrt(2 * (1 - cos)).
# A pair is in band when its L2 is between these two thresholds.
_CONTRA_L2_MAX = (2 * (1 - CONTRADICTION_COSINE_MIN)) ** 0.5  # 1.0
_CONTRA_L2_MIN = (2 * (1 - CONTRADICTION_COSINE_MAX)) ** 0.5  # ~0.5477

# Lexical negation patterns -- a body containing any of these (case-
# insensitive substring) is treated as a "negating" body for the
# differential check. v5.14.0+ may refine with regex word-boundaries
# or LLM-driven extraction.
_NEGATION_PATTERNS: tuple[str, ...] = (
    "do not",
    "don't",
    "never",
    "no longer",
    "deprecated",
    "removed",
    "instead of",
    "forbidden",
    "disallowed",
    "must not",
    "should not",
    "avoid",
)

# Same buckets as ``DUPLICATE_TYPE_BUCKETS`` -- contradictions are
# within-type only in Phase 2a; cross-type lens are Phase 3.
CONTRADICTION_TYPE_BUCKETS = DUPLICATE_TYPE_BUCKETS

# Body excerpt cap sent to the LLM judge. Full bodies can be huge
# (whole reference docs); 2000 chars is enough context for a yes/no
# decision on whether two snippets contradict.
_JUDGE_BODY_CAP = 2000

_JUDGE_SYSTEM_PROMPT = (
    "You are a strict contradiction grader for a knowledge corpus. "
    "Given two related text snippets from the same domain, decide whether "
    "they are mutually contradictory (one explicitly negates, forbids, "
    "or deprecates what the other prescribes / recommends). Two snippets "
    "that simply cover different aspects of a topic are NOT a contradiction. "
    "Respond with ONLY a JSON object of the shape "
    '{"contradiction": true|false, "rationale": "<one short paragraph>"}. '
    "No prose outside the JSON. No markdown fences."
)


def _has_negation(body: str | None) -> bool:
    if not body:
        return False
    lowered = body.lower()
    return any(p in lowered for p in _NEGATION_PATTERNS)


# --- Shared LLM-helper base (v5.17.1) ----------------------------------


@dataclass
class _LLMHelper:
    """Shared base for the opt-in LLM judge/proposer helpers
    (contradictions / semantic_orphans / refactor_actions /
    dead_code). It owns ONLY the create->parse->graceful-degradation
    routine + the four common fields; each subclass keeps its own
    prompt, field interpretation, and ``rationale_log`` entry schema.

    Wraps an Anthropic client; tests pass a MagicMock so no network
    call is made."""

    client: Any
    """The Anthropic client. Any object with ``messages.create(...)``
    works (tests pass a MagicMock)."""

    model: str = "claude-sonnet-4-6"
    """Default grader model. Sonnet is lower latency + cost than
    Opus. Override via ``MNEMO_ANALYZE_JUDGE_MODEL`` env var (the
    ``*_from_env`` factories read it)."""

    max_tokens: int = 512
    """Token budget for the structured reply. Subclasses redeclare
    this default where they need a different ceiling."""

    rationale_log: list[dict[str, Any]] = field(default_factory=list)
    """Per-call audit trail. Operators can dump this after a sweep to
    inspect decisions. Each subclass appends its own entry shape."""

    def _invoke_json(self, *, system: str, user: str) -> tuple[dict[str, Any] | None, str | None]:
        """Call the model + parse its JSON reply. Returns
        ``(parsed_dict, None)`` on success, or ``(None, error_marker)``
        on any parse/network error. NEVER raises -- the caller maps a
        ``None`` result to its own safe default + ``rationale_log``
        entry.

        The error marker is ``"PARSE_ERROR: ..."`` for a malformed /
        wrong-shape reply (``json.JSONDecodeError`` / ``KeyError`` /
        ``AttributeError`` / ``IndexError`` / ``TypeError``) and
        ``"CLIENT_ERROR: ..."`` for any other exception (network / SDK)."""
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            text = response.content[0].text
            return json.loads(text), None
        except (json.JSONDecodeError, KeyError, AttributeError, IndexError, TypeError) as exc:
            log.warning("%s: parse/structure error (%s); degrading", type(self).__name__, exc)
            return None, f"PARSE_ERROR: {exc}"
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: client error (%s); degrading", type(self).__name__, exc)
            return None, f"CLIENT_ERROR: {exc}"


# --- Phase 2a (v5.13.0) -- LLM judge -----------------------------------


@dataclass
class LLMContradictionJudge(_LLMHelper):
    """Opt-in binary judge for contradiction candidates.

    Graceful: any parse/exception path returns False (the pair stays
    a deterministic 'candidate'; no false-positive 'high'). Shares
    the create+parse routine + the four fields with :class:`_LLMHelper`."""

    def judge(self, *, a_body: str, b_body: str) -> bool:
        """Return True if the two bodies represent a mutual
        contradiction; False otherwise (including on every error
        path)."""
        a = (a_body or "")[:_JUDGE_BODY_CAP]
        b = (b_body or "")[:_JUDGE_BODY_CAP]
        user_msg = (
            "## Snippet A\n"
            f"{a}\n\n"
            "## Snippet B\n"
            f"{b}\n\n"
            "## Task\n"
            "Decide: do A and B contradict each other? Respond with ONLY the JSON."
        )
        parsed, err = self._invoke_json(system=_JUDGE_SYSTEM_PROMPT, user=user_msg)
        if parsed is None:
            self.rationale_log.append(
                {
                    "a_body": a,
                    "b_body": b,
                    "contradiction": False,
                    "rationale": err,
                    "parsed_ok": False,
                }
            )
            return False
        result = bool(parsed.get("contradiction", False))
        self.rationale_log.append(
            {
                "a_body": a,
                "b_body": b,
                "contradiction": result,
                "rationale": parsed.get("rationale", ""),
                "parsed_ok": True,
            }
        )
        return result


def judge_from_env() -> LLMContradictionJudge | None:
    """Construct an LLMContradictionJudge from environment when ALL
    of: ``MNEMO_ANALYZE_LLM_JUDGE`` is truthy, ``ANTHROPIC_API_KEY``
    is set, and the ``anthropic`` package is importable. Otherwise
    return None so the analyzer falls back to the deterministic
    candidate-only path.

    Override the model via ``MNEMO_ANALYZE_JUDGE_MODEL``
    (default ``claude-sonnet-4-6``)."""
    flag = os.environ.get("MNEMO_ANALYZE_LLM_JUDGE", "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic  # type: ignore[import-untyped]
    except ImportError:
        log.warning(
            "judge_from_env: MNEMO_ANALYZE_LLM_JUDGE=1 set but anthropic "
            "package not installed; falling back to deterministic candidates."
        )
        return None
    model = os.environ.get("MNEMO_ANALYZE_JUDGE_MODEL", "claude-sonnet-4-6")
    return LLMContradictionJudge(client=anthropic.Anthropic(), model=model)


def _iter_all_nodes(store: Store, *, type: str | None = None) -> list[Node]:
    """Page-through Store.list_nodes (which has a page-size cap) and
    return the concatenated list. Used by every detector + the
    orchestrator's ``node_count_scanned``."""
    out: list[Node] = []
    offset = 0
    while True:
        page = store.list_nodes(type=type, limit=PAGE_SIZE, offset=offset)
        if not page:
            break
        out.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return out


# --- 1. stale ----------------------------------------------------------


def detect_stale(store: Store) -> list[dict[str, Any]]:
    """Surface nodes whose body or description contains ``SUPERSEDED``
    (case-insensitive). The user's own marker; informational only."""
    findings: list[dict[str, Any]] = []
    for node in _iter_all_nodes(store):
        haystack = " ".join(filter(None, [node.description, node.body]))
        if _STALE_MARKER_RE.search(haystack):
            findings.append(
                {
                    "type": "stale",
                    "node_ids": [node.id],
                    "description": (
                        f"Node {node.id!r} body/description marks it as "
                        f"SUPERSEDED; consider archiving."
                    ),
                    "severity": "low",
                }
            )
    return findings


# --- 2. duplicates -----------------------------------------------------


def detect_duplicates(store: Store, *, embedder: Any) -> list[dict[str, Any]]:
    """Surface within-type pairs of nodes whose embeddings are within
    the near-duplicate cosine threshold.

    Implementation: iterate each node, embed its body (cheaply --
    embedder is cached + warm in production), then do a vec_search
    with type_filter=[node.type] for k=10. Any hit other than the
    node itself with L2 <= DUPLICATE_L2_THRESHOLD becomes a finding.

    De-duplication of pairs: a pair (A, B) and (B, A) would otherwise
    be emitted twice; we sort the pair + use a seen-set."""
    if embedder is None:
        return []

    findings: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for type_bucket in DUPLICATE_TYPE_BUCKETS:
        nodes = _iter_all_nodes(store, type=type_bucket)
        if len(nodes) < 2:
            continue
        for node in nodes:
            haystack = node.body or node.description or node.name
            if not haystack:
                continue
            try:
                vec = embedder.embed_text(haystack)
            except Exception:  # noqa: BLE001
                continue
            try:
                hits = store.vec_search(vec, k=10, type_filter=[type_bucket])
            except Exception:  # noqa: BLE001 -- vec table missing on empty stores
                continue
            for hit_node_id, _chunk_idx, _chunk_text, distance in hits:
                if hit_node_id == node.id:
                    continue
                if distance > DUPLICATE_L2_THRESHOLD:
                    continue
                pair = tuple(sorted([node.id, hit_node_id]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                # Cosine = 1 - L2^2 / 2 (for normalized vectors).
                cosine = max(0.0, min(1.0, 1.0 - (distance * distance) / 2.0))
                findings.append(
                    {
                        "type": "duplicates",
                        "node_ids": list(pair),
                        "description": (
                            f"Two {type_bucket} nodes with cosine similarity "
                            f"{cosine:.3f}; consider merging or marking one as "
                            f"superseded."
                        ),
                        "severity": "medium",
                    }
                )
    return findings


# --- 3. orphan_references ----------------------------------------------


def detect_orphan_references(store: Store) -> list[dict[str, Any]]:
    """Surface nodes whose body cites ``[mnemo:<id>]`` for an ``<id>``
    not in the current graph."""
    all_nodes = _iter_all_nodes(store)
    existing_ids = {n.id for n in all_nodes}

    findings: list[dict[str, Any]] = []
    for node in all_nodes:
        if not node.body:
            continue
        missing: list[str] = []
        for match in _CITATION_RE.finditer(node.body):
            referenced = match.group(1).strip()
            if referenced and referenced not in existing_ids:
                missing.append(referenced)
        if missing:
            findings.append(
                {
                    "type": "orphan_reference",
                    "node_ids": [node.id],
                    "description": (
                        f"Node {node.id!r} body cites missing target(s): "
                        f"{sorted(set(missing))}; the target node was deleted "
                        f"or never existed."
                    ),
                    "severity": "high",
                    "missing_targets": sorted(set(missing)),
                }
            )
    return findings


# --- 4. contradictions (v5.13.0) ---------------------------------------


def detect_contradictions(
    store: Store,
    *,
    embedder: Any,
    judge: Any | None = None,
) -> list[dict[str, Any]]:
    """Surface within-type pairs of nodes likely to contradict.

    Two-step detection:

    1. **Candidate gate** (deterministic, fast): for each pair within
       a type bucket, require:
       - cosine similarity in [CONTRADICTION_COSINE_MIN,
         CONTRADICTION_COSINE_MAX] (same topic, distinct
         prescriptions), AND
       - at least one body contains a negation pattern (from
         ``_NEGATION_PATTERNS``).

    2. **Optional LLM confirmation**: when ``judge`` is provided,
       each candidate's two bodies are sent to the judge for a
       binary contradiction decision. Confirmed pairs become
       severity ``high``; rejected pairs are DROPPED (not returned
       with the lower 'candidate' severity -- the judge is
       authoritative when enabled).

    Without a judge, every candidate is returned with severity
    ``candidate``.
    """
    if embedder is None:
        return []

    findings: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for type_bucket in CONTRADICTION_TYPE_BUCKETS:
        nodes = _iter_all_nodes(store, type=type_bucket)
        if len(nodes) < 2:
            continue
        for node in nodes:
            haystack = node.body or node.description or node.name
            if not haystack:
                continue
            try:
                vec = embedder.embed_text(haystack)
            except Exception:  # noqa: BLE001
                continue
            try:
                hits = store.vec_search(vec, k=20, type_filter=[type_bucket])
            except Exception:  # noqa: BLE001
                continue
            for hit_node_id, _chunk_idx, _chunk_text, distance in hits:
                if hit_node_id == node.id:
                    continue
                # Cosine band: L2 in [_CONTRA_L2_MIN, _CONTRA_L2_MAX].
                if distance < _CONTRA_L2_MIN or distance > _CONTRA_L2_MAX:
                    continue
                pair = tuple(sorted([node.id, hit_node_id]))
                if pair in seen_pairs:
                    continue
                # Negation differential: at least one of the two
                # bodies must contain a negation pattern (Phase 2a
                # gate). v5.14.0+ may require XOR or LLM-driven
                # negation extraction.
                other = store.get_node(hit_node_id)
                if other is None:
                    continue
                if not (_has_negation(node.body) or _has_negation(other.body)):
                    continue
                seen_pairs.add(pair)
                cosine = max(0.0, min(1.0, 1.0 - (distance * distance) / 2.0))

                # If a judge is provided, escalate. Confirmed ->
                # severity high; rejected -> drop entirely.
                if judge is not None:
                    confirmed = bool(judge.judge(a_body=node.body or "", b_body=other.body or ""))
                    if not confirmed:
                        continue
                    severity = "high"
                else:
                    severity = "candidate"

                findings.append(
                    {
                        "type": "contradictions",
                        "node_ids": list(pair),
                        "description": (
                            f"Two {type_bucket} nodes with cosine "
                            f"similarity {cosine:.3f} and a negation "
                            f"differential; "
                            + (
                                "LLM judge confirmed mutual contradiction."
                                if severity == "high"
                                else "review for mutual contradiction."
                            )
                        ),
                        "severity": severity,
                    }
                )
    return findings


# --- 5. semantic_orphans (v5.14.0) -------------------------------------

# Three regex patterns for deterministic concept extraction (see
# Phase 2b design doc §3.1). A concept must match exactly one of
# these patterns; the union is the "candidate concept" set.

# CamelCase identifiers: starts with uppercase, has at least one
# lowercase letter, alphanumeric. Post-filtered in _extract_concepts
# to require >= 2 total uppercase letters so single-segment
# capitalized words ("Phase", "Note", "The") are skipped. Catches
# both acronym-prefixed ("MQTTBridge", "XMLParser") and traditional
# CamelCase ("RetryHandler", "BossEnemyAI", "ServiceMesh").
_CONCEPT_CAMELCASE_RE = re.compile(r"\b[A-Z][A-Za-z0-9]*[a-z][A-Za-z0-9]*\b")

# snake_case identifiers with 2+ underscores (covers domain terms
# like "son_tinh_ai" / "knowledge_auditor_phase_1") OR length >= 12
# (covers long single-underscore terms like "petrolimex_detection_model").
_CONCEPT_SNAKE_MULTI_RE = re.compile(r"\b[a-z][a-z0-9]+(?:_[a-z0-9]+){2,}\b")
_CONCEPT_SNAKE_LONG_RE = re.compile(r"\b[a-z][a-z0-9]+_[a-z0-9_]+\b")
# (used inside _extract_concepts with a length filter)

# ALL_CAPS constants with at least 1 underscore. Catches
# "MAX_RETRIES", "DUPLICATE_COSINE_THRESHOLD", "MNEMO_ANALYZE_LLM_JUDGE".
# Skips "URL" / "API" / "SDK".
_CONCEPT_ALL_CAPS_RE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")

# Length filter on extracted concepts (chars). Below 6 = too generic;
# above 60 = likely garbled / multi-line capture.
_CONCEPT_MIN_LEN = 6
_CONCEPT_MAX_LEN = 60

# Stop-list -- common code idioms that match the patterns but aren't
# domain concepts. Mostly Python dunders + a few generic terms.
_CONCEPT_STOP_LIST: frozenset[str] = frozenset(
    {
        "__init__",
        "__main__",
        "__name__",
        "__file__",
        "__dict__",
        "__class__",
        "self_test",
        "test_test",
    }
)


def _extract_concepts(body: str | None) -> list[str]:
    """Deterministically extract candidate concepts from a node body.

    Returns a list (de-duplicated, source-order-preserving) of
    concept strings matched by any of the three regex patterns
    (CamelCase / snake_case / ALL_CAPS), filtered by length +
    stop-list.

    The pure function is the public extraction primitive; tests
    pin its behavior so changes are caught.
    """
    if not body:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for pattern in (
        _CONCEPT_CAMELCASE_RE,
        _CONCEPT_SNAKE_MULTI_RE,
        _CONCEPT_SNAKE_LONG_RE,
        _CONCEPT_ALL_CAPS_RE,
    ):
        for match in pattern.finditer(body):
            concept = match.group(0)
            if concept in seen:
                continue
            if len(concept) < _CONCEPT_MIN_LEN or len(concept) > _CONCEPT_MAX_LEN:
                continue
            if concept in _CONCEPT_STOP_LIST:
                continue
            if pattern is _CONCEPT_CAMELCASE_RE:
                # Require >= 2 uppercase letters so single-segment
                # capitalized words ("Phase", "Note") are skipped.
                upper_count = sum(1 for c in concept if c.isupper())
                if upper_count < 2:
                    continue
                # Skip if it's actually an ALL_CAPS constant -- those
                # are owned by _CONCEPT_ALL_CAPS_RE (and need an
                # underscore to qualify there).
                if concept.isupper():
                    continue
            elif pattern is _CONCEPT_SNAKE_LONG_RE:
                # SNAKE_LONG accepts 1+ underscores; only add concepts
                # the multi-underscore regex missed AND that are >= 12
                # chars.
                if len(concept) < 12:
                    continue
                if concept.count("_") >= 2:
                    continue
            seen.add(concept)
            out.append(concept)
    return out


def _concept_is_defined(
    concept: str,
    *,
    source_id: str,
    all_nodes: list[Node],
) -> bool:
    """Return True if any node OTHER than the source has the concept
    as a case-insensitive substring of its ``name`` or
    ``description``. Body mentions don't count -- they're
    references, not definitions.
    """
    needle = concept.lower()
    for n in all_nodes:
        if n.id == source_id:
            continue
        haystack_name = (n.name or "").lower()
        haystack_desc = (n.description or "").lower()
        if needle in haystack_name or needle in haystack_desc:
            return True
    return False


# Token budget per concept for the LLM judge prompt. The context
# excerpt is capped before being sent.
_ORPHAN_JUDGE_CONTEXT_CAP = 1200

_ORPHAN_JUDGE_SYSTEM_PROMPT = (
    "You are a strict knowledge-graph completeness grader. Given "
    "a concept extracted from a document plus surrounding context, "
    "decide whether the concept is a PROJECT-SPECIFIC term that "
    "should have its own dedicated definition node in the corpus, "
    "OR a COMMON term (industry-standard library, language "
    "keyword, well-known service) that doesn't need a dedicated "
    "definition. Respond with ONLY a JSON object of the shape "
    '{"needs_definition": true|false, "rationale": "<one short paragraph>"}. '
    "No prose outside the JSON. No markdown fences."
)


@dataclass
class LLMSemanticOrphanJudge(_LLMHelper):
    """Opt-in binary judge for semantic-orphan candidates.

    Sibling to :class:`LLMContradictionJudge` -- different prompt +
    different return semantics. Graceful: any parse/exception path
    returns False (the candidate is DROPPED when the judge is
    enabled; no false-positive 'high'). Shares the create+parse
    routine + fields with :class:`_LLMHelper`.
    """

    def judge(self, *, concept: str, context: str) -> bool:
        """Return True if the concept needs its own definition node;
        False otherwise (including on every error path)."""
        ctx = (context or "")[:_ORPHAN_JUDGE_CONTEXT_CAP]
        user_msg = (
            f"## Concept\n{concept}\n\n"
            f"## Surrounding context\n{ctx}\n\n"
            "## Task\n"
            "Decide: does this concept need its own definition node in "
            "the corpus? Respond with ONLY the JSON."
        )
        parsed, err = self._invoke_json(system=_ORPHAN_JUDGE_SYSTEM_PROMPT, user=user_msg)
        if parsed is None:
            self.rationale_log.append(
                {
                    "concept": concept,
                    "context": ctx,
                    "needs_definition": False,
                    "rationale": err,
                    "parsed_ok": False,
                }
            )
            return False
        result = bool(parsed.get("needs_definition", False))
        self.rationale_log.append(
            {
                "concept": concept,
                "context": ctx,
                "needs_definition": result,
                "rationale": parsed.get("rationale", ""),
                "parsed_ok": True,
            }
        )
        return result


def semantic_orphan_judge_from_env() -> LLMSemanticOrphanJudge | None:
    """Construct an LLMSemanticOrphanJudge from environment when ALL
    of: ``MNEMO_ANALYZE_LLM_JUDGE`` is truthy, ``ANTHROPIC_API_KEY``
    is set, and the ``anthropic`` package is importable. Otherwise
    return None so the analyzer falls back to the deterministic
    candidate-only path.

    Shares the env contract with the contradictions judge: one
    opt-in toggle for all LLM-augmented detectors. Override model
    via ``MNEMO_ANALYZE_JUDGE_MODEL`` (default ``claude-sonnet-4-6``).
    """
    flag = os.environ.get("MNEMO_ANALYZE_LLM_JUDGE", "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic  # type: ignore[import-untyped]
    except ImportError:
        log.warning(
            "semantic_orphan_judge_from_env: MNEMO_ANALYZE_LLM_JUDGE=1 set "
            "but anthropic package not installed; falling back to "
            "deterministic candidates."
        )
        return None
    model = os.environ.get("MNEMO_ANALYZE_JUDGE_MODEL", "claude-sonnet-4-6")
    return LLMSemanticOrphanJudge(client=anthropic.Anthropic(), model=model)


# Context window (chars) extracted around each concept mention for
# the LLM judge. The judge sees the body excerpt surrounding the
# first occurrence of the concept.
_ORPHAN_CONTEXT_WINDOW = 600


def _context_around(body: str, concept: str) -> str:
    """Return the body excerpt surrounding the first occurrence of
    the concept, capped to ``_ORPHAN_CONTEXT_WINDOW`` chars (300
    each side, plus the concept itself)."""
    if not body or not concept:
        return body or ""
    half = _ORPHAN_CONTEXT_WINDOW // 2
    lo_idx = body.find(concept)
    if lo_idx < 0:
        return body[:_ORPHAN_CONTEXT_WINDOW]
    start = max(0, lo_idx - half)
    end = min(len(body), lo_idx + len(concept) + half)
    return body[start:end]


def detect_semantic_orphans(
    store: Store,
    *,
    judge: Any | None = None,
) -> list[dict[str, Any]]:
    """Surface concepts referenced by a node but not defined by any
    other node in the corpus.

    Two-step detection:

    1. **Candidate gate** (deterministic, fast): for each node N
       walk the body via :func:`_extract_concepts` + filter by
       :func:`_concept_is_defined` (any OTHER node's name OR
       description must contain the concept as a substring,
       case-insensitive). Concepts that pass = candidate orphans.

    2. **Optional LLM confirmation**: when ``judge`` is provided,
       each candidate's (concept, surrounding-context) is sent to
       the judge for a binary "needs definition" decision.
       Confirmed orphans become severity ``high``; rejected
       orphans are DROPPED (not returned with the lower
       'candidate' severity -- the judge is authoritative when
       enabled).

    Without a judge, every candidate is returned with severity
    ``candidate``.
    """
    all_nodes = _iter_all_nodes(store)
    findings: list[dict[str, Any]] = []

    # Precompute a single haystack of every node's lowered
    # (name + description). Bodies are EXCLUDED -- a body mention
    # is a reference, not a definition. This trades the per-concept
    # O(N) walk for an O(corpus_size) substring search per concept
    # (Python's substring search is implemented in C; orders of
    # magnitude faster than a Python loop over 12k node objects).
    #
    # Safety re: source-self exclusion: each concept is first checked
    # against the source's OWN name+description; if it matches there,
    # the concept is skipped before this global haystack lookup runs.
    # So any match against the global haystack must come from a
    # non-source node.
    haystack_chunks: list[str] = []
    for n in all_nodes:
        if n.name:
            haystack_chunks.append(n.name.lower())
        if n.description:
            haystack_chunks.append(n.description.lower())
    global_haystack = "\n".join(haystack_chunks)

    for node in all_nodes:
        if not node.body:
            continue
        concepts = _extract_concepts(node.body)
        if not concepts:
            continue
        # Precompute per-source self-defines haystack (name +
        # description, lowered).
        self_text = ((node.name or "") + "\n" + (node.description or "")).lower()
        for concept in concepts:
            needle = concept.lower()
            # Self-defining: source's own name/description covers it.
            if needle in self_text:
                continue
            # Defined elsewhere: any OTHER node's name/description
            # covers it. Since self-defines was False above, any
            # hit in the global haystack must be from a non-source
            # node.
            if needle in global_haystack:
                continue

            # Deterministic candidate. Optionally escalate.
            if judge is not None:
                ctx = _context_around(node.body, concept)
                confirmed = bool(judge.judge(concept=concept, context=ctx))
                if not confirmed:
                    continue
                severity = "high"
            else:
                severity = "candidate"

            findings.append(
                {
                    "type": "semantic_orphan",
                    "node_ids": [node.id],
                    "description": (
                        f"Node {node.id!r} references concept "
                        f"{concept!r} but no other node defines it "
                        f"(checked name + description). "
                        + (
                            "LLM judge confirmed this is a project-specific "
                            "term needing a definition node."
                            if severity == "high"
                            else "Review whether the concept needs its own definition node."
                        )
                    ),
                    "severity": severity,
                    "concept": concept,
                }
            )
    return findings


# --- Phase 2c (v5.15.0) -- refactor_actions enrichment -----------------

# Default hard cap on the number of findings enriched with an LLM
# action per audit. Defense in depth: even within the high/medium
# severity gate a pathological corpus could surface hundreds.
# Bounds worst-case cost (~$0.10 at 50 actions x ~1.5k tok on
# Sonnet). The skipped count is always surfaced (no silent caps).
DEFAULT_MAX_REFACTOR_ACTIONS = 50

# Severity tiers eligible for action proposal by default. The
# actionable / confirmed tier: high (LLM-confirmed or broken
# citation) + medium (duplicates). ``candidate`` (unconfirmed
# deterministic) and ``low`` (stale, already user-marked) are NOT
# enriched unless the caller explicitly widens ``severities`` --
# they're noise until promoted, and enriching them all would be
# both expensive and low-value.
DEFAULT_REFACTOR_SEVERITIES = ("high", "medium")

# Body excerpt cap per cited node sent to the proposer. Full bodies
# can be huge; 1500 chars per node is enough context to propose a
# concrete action.
_PROPOSER_BODY_CAP = 1500

# The canonical action ``kind`` values + their primitive mappings.
# Surfaced in the system prompt so the model picks from a closed set.
_REFACTOR_ACTION_KINDS = (
    "merge",  # duplicates -> mnemo_update_node(canonical) + delete other
    "supersede",  # contradiction -> mnemo_update_node(older, +SUPERSEDED)
    "delete",  # stale / fully-superseded -> mnemo_delete_node
    "create_definition",  # semantic_orphan -> mnemo_create_node
    "add_reconciliation_note",  # contradiction (both valid) -> mnemo_update_node
    "fix_citation",  # orphan_reference -> mnemo_update_node(body)
    "none",  # the proposer declined / error
)

_PROPOSER_SYSTEM_PROMPT = (
    "You are a knowledge-graph refactoring assistant. Given ONE "
    "audit finding (a structural issue) plus the bodies of the cited "
    "nodes, propose exactly ONE concrete action a human could take to "
    "resolve it, mapping to an existing mnemo primitive. Valid action "
    "kinds and their primitives:\n"
    "- merge -> mnemo_update_node (fold the duplicate's unique content "
    "into the canonical node; the user deletes the other afterward)\n"
    "- supersede -> mnemo_update_node (append a 'SUPERSEDED by <id>' "
    "note to the older / less authoritative node)\n"
    "- delete -> mnemo_delete_node (the node is fully superseded / "
    "obsolete)\n"
    "- create_definition -> mnemo_create_node (define a referenced-but-"
    "undefined concept)\n"
    "- add_reconciliation_note -> mnemo_update_node (both nodes are "
    "valid in different scopes; add a note explaining the distinction)\n"
    "- fix_citation -> mnemo_update_node (repair or remove a broken "
    "[mnemo:<id>] citation)\n"
    "- none -> null (you cannot propose a safe action)\n"
    "These are PROPOSALS the user reviews; you NEVER apply them. "
    "Respond with ONLY a JSON object of the shape "
    '{"kind": "<one kind>", "primitive": "<primitive or null>", '
    '"target_node_id": "<id or null>", "args_hint": {<suggested kwargs>}, '
    '"rationale": "<one short paragraph>"}. '
    "No prose outside the JSON. No markdown fences."
)


def _empty_action(reason: str) -> dict[str, Any]:
    """The graceful-degradation action: no operation proposed."""
    return {
        "kind": "none",
        "primitive": None,
        "target_node_id": None,
        "args_hint": {},
        "rationale": reason,
    }


@dataclass
class LLMRefactorProposer(_LLMHelper):
    """Opt-in structured action generator for audit findings.

    UNLIKE the binary-classifier judges, this is a GENERATOR: it
    returns an action dict. Graceful: any parse/exception path returns
    an ``_empty_action`` (``kind="none"``) so the finding still ships
    with an empty action. Shares the create+parse routine + fields
    with :class:`_LLMHelper` (with a larger ``max_tokens`` default for
    the structured action)."""

    max_tokens: int = 700

    def propose(self, *, finding: dict[str, Any], node_bodies: dict[str, str]) -> dict[str, Any]:
        """Return an action dict for the finding. Never raises; every
        error path returns ``_empty_action``."""
        bodies_block = "\n\n".join(
            f"### Node {nid}\n{(body or '')[:_PROPOSER_BODY_CAP]}"
            for nid, body in node_bodies.items()
        )
        user_msg = (
            "## Finding\n"
            f"type: {finding.get('type')}\n"
            f"severity: {finding.get('severity')}\n"
            f"node_ids: {finding.get('node_ids')}\n"
            f"description: {finding.get('description')}\n\n"
            "## Cited node bodies\n"
            f"{bodies_block}\n\n"
            "## Task\n"
            "Propose ONE action. Respond with ONLY the JSON."
        )
        parsed, err = self._invoke_json(system=_PROPOSER_SYSTEM_PROMPT, user=user_msg)
        if parsed is None:
            self.rationale_log.append(
                {
                    "finding_type": finding.get("type"),
                    "node_ids": finding.get("node_ids"),
                    "kind": "none",
                    "rationale": err,
                    "parsed_ok": False,
                }
            )
            return _empty_action(err or "")
        kind = str(parsed.get("kind", "none"))
        if kind not in _REFACTOR_ACTION_KINDS:
            kind = "none"
        action = {
            "kind": kind,
            "primitive": parsed.get("primitive") if kind != "none" else None,
            "target_node_id": parsed.get("target_node_id"),
            "args_hint": parsed.get("args_hint", {}) or {},
            "rationale": str(parsed.get("rationale", "")),
        }
        self.rationale_log.append(
            {
                "finding_type": finding.get("type"),
                "node_ids": finding.get("node_ids"),
                "kind": action["kind"],
                "rationale": action["rationale"],
                "parsed_ok": True,
            }
        )
        return action


def refactor_proposer_from_env() -> LLMRefactorProposer | None:
    """Construct an LLMRefactorProposer from environment when ALL of:
    ``MNEMO_ANALYZE_PROPOSE_ACTIONS`` is truthy, ``ANTHROPIC_API_KEY``
    is set, and the ``anthropic`` package is importable. Otherwise
    return None so the enrichment pass is a no-op.

    Uses its OWN flag (independent of the detection-judge flag
    ``MNEMO_ANALYZE_LLM_JUDGE``) so action proposal can be toggled
    separately. Shares the model override
    ``MNEMO_ANALYZE_JUDGE_MODEL`` (default ``claude-sonnet-4-6``).
    """
    flag = os.environ.get("MNEMO_ANALYZE_PROPOSE_ACTIONS", "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic  # type: ignore[import-untyped]
    except ImportError:
        log.warning(
            "refactor_proposer_from_env: MNEMO_ANALYZE_PROPOSE_ACTIONS=1 set "
            "but anthropic package not installed; refactor_actions is a no-op."
        )
        return None
    model = os.environ.get("MNEMO_ANALYZE_JUDGE_MODEL", "claude-sonnet-4-6")
    return LLMRefactorProposer(client=anthropic.Anthropic(), model=model)


def propose_refactor_actions(
    store: Store,
    findings: list[dict[str, Any]],
    *,
    proposer: Any | None = None,
    max_actions: int = DEFAULT_MAX_REFACTOR_ACTIONS,
    severities: tuple[str, ...] = DEFAULT_REFACTOR_SEVERITIES,
) -> tuple[list[dict[str, Any]], int]:
    """Enrich eligible findings with an LLM-proposed ``action``.

    Eligibility: a finding's ``severity`` must be in ``severities``
    (default high+medium). Eligible findings are enriched in list
    order up to ``max_actions``; any eligible findings beyond the cap
    are left unenriched and counted into the returned ``n_skipped``.
    Non-eligible findings are never touched (no ``action`` key added).

    Without a ``proposer`` (``None``) the pass is a no-op: returns the
    findings unchanged + ``0`` skipped. This is the byte-stable
    default path.

    Returns ``(findings, n_skipped)``. The findings list is mutated
    in place (and also returned for convenience).
    """
    if proposer is None:
        return findings, 0

    enriched_count = 0
    skipped = 0
    for f in findings:
        if f.get("severity") not in severities:
            continue
        if enriched_count >= max_actions:
            skipped += 1
            continue
        bodies: dict[str, str] = {}
        for nid in f.get("node_ids", []):
            node = store.get_node(nid)
            bodies[nid] = (node.body if node and node.body else "") if node else ""
        try:
            action = proposer.propose(finding=f, node_bodies=bodies)
        except Exception as exc:  # noqa: BLE001 -- proposer must never break the audit
            log.warning("propose_refactor_actions: proposer raised (%s); using empty action", exc)
            action = _empty_action(f"PROPOSER_RAISED: {exc}")
        f["action"] = action
        enriched_count += 1
    return findings, skipped


# --- Phase 3 (v5.16.0) -- code lens: dead_code -------------------------

# Callable node types the dead_code detector scans.
_DEAD_CODE_NODE_TYPES = ("code_function", "code_method")

_DEAD_CODE_JUDGE_SYSTEM_PROMPT = (
    "You are a strict dead-code grader for a codebase. Given a "
    "PRIVATE function/method (its name, source path, and body) that "
    "the static call graph found NO callers for, decide whether it "
    "is genuinely dead (safe to delete) or whether it is reached by "
    "a pattern the static graph misses -- a dispatch table, getattr, "
    "a decorator/registration callback, a framework hook, or an "
    "implicitly-invoked protocol method. Respond with ONLY a JSON "
    'object of the shape {"is_dead": true|false, "rationale": '
    '"<one short paragraph>"}. No prose outside the JSON. No '
    "markdown fences."
)

# Body excerpt cap sent to the dead_code judge.
_DEAD_CODE_BODY_CAP = 1500


def _is_private_symbol(name: str | None) -> bool:
    """A private symbol starts with ``_`` but is not a dunder
    (``__x__``). Private symbols are only reachable within their own
    module, where the Tier-2 call resolver is high-confidence -- so a
    private symbol with zero resolved inbound calls is a strong dead
    signal. Public symbols are excluded (cross-file / external /
    dynamic resolution is sparse; flagging them would flood)."""
    if not name:
        return False
    if name.startswith("__") and name.endswith("__"):
        return False
    return name.startswith("_")


def _is_test_symbol(name: str | None, source_path: str | None) -> bool:
    """Test entry points are invoked by the test runner, not by
    in-graph callers -- exclude them from dead_code."""
    n = name or ""
    if n.startswith("test_") or n.startswith("_test_"):
        return True
    sp = (source_path or "").replace("\\", "/").lower()
    return "/tests/" in sp or "/test/" in sp


@dataclass
class LLMDeadCodeJudge(_LLMHelper):
    """Opt-in binary judge for dead_code candidates. Sibling to the
    other judges. Graceful: any parse/exception path returns False
    (keeps the deterministic 'candidate'; never falsely promotes to
    'high'). Shares the create+parse routine + fields with
    :class:`_LLMHelper` (smaller ``max_tokens`` default -- the reply
    is a tiny {is_dead, rationale})."""

    max_tokens: int = 400

    def judge(self, *, name: str, body: str, source_path: str) -> bool:
        """Return True if the symbol is genuinely dead; False
        otherwise (including on every error path)."""
        snippet = (body or "")[:_DEAD_CODE_BODY_CAP]
        user_msg = (
            f"## Symbol\nname: {name}\npath: {source_path}\n\n"
            f"## Body\n{snippet}\n\n"
            "## Task\nIs this private symbol genuinely dead (no caller, "
            "not dispatched/registered/implicit)? Respond with ONLY the JSON."
        )
        parsed, err = self._invoke_json(system=_DEAD_CODE_JUDGE_SYSTEM_PROMPT, user=user_msg)
        if parsed is None:
            self.rationale_log.append(
                {
                    "name": name,
                    "source_path": source_path,
                    "is_dead": False,
                    "rationale": err,
                    "parsed_ok": False,
                }
            )
            return False
        result = bool(parsed.get("is_dead", False))
        self.rationale_log.append(
            {
                "name": name,
                "source_path": source_path,
                "is_dead": result,
                "rationale": parsed.get("rationale", ""),
                "parsed_ok": True,
            }
        )
        return result


def dead_code_judge_from_env() -> LLMDeadCodeJudge | None:
    """Construct an LLMDeadCodeJudge from environment when ALL of:
    ``MNEMO_ANALYZE_LLM_JUDGE`` is truthy, ``ANTHROPIC_API_KEY`` is
    set, and ``anthropic`` is importable. Shares the flag + model
    override with the other auditor judges."""
    flag = os.environ.get("MNEMO_ANALYZE_LLM_JUDGE", "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic  # type: ignore[import-untyped]
    except ImportError:
        log.warning(
            "dead_code_judge_from_env: flag set but anthropic not installed; "
            "falling back to deterministic candidates."
        )
        return None
    model = os.environ.get("MNEMO_ANALYZE_JUDGE_MODEL", "claude-sonnet-4-6")
    return LLMDeadCodeJudge(client=anthropic.Anthropic(), model=model)


def detect_dead_code(store: Store, *, judge: Any | None = None) -> list[dict[str, Any]]:
    """Surface PRIVATE, uncalled functions/methods (candidate dead
    code).

    Candidate gate (deterministic):
    - node type in ``_DEAD_CODE_NODE_TYPES``;
    - private symbol (``_``-prefixed, non-dunder);
    - not a test entry point (name / path);
    - ZERO inbound ``calls`` edges (id not among call-edge dst_ids).

    Optional LLM confirmation: when ``judge`` is provided, each
    candidate's body is graded; confirmed -> severity ``high``;
    rejected (dispatched/registered/implicit) -> dropped. Without a
    judge, candidates ship with severity ``candidate``.
    """
    try:
        call_edges = store.get_edges(relation="calls")
    except Exception:  # noqa: BLE001 -- empty stores / missing table
        call_edges = []
    called_ids = {e.dst_id for e in call_edges}

    findings: list[dict[str, Any]] = []
    for ntype in _DEAD_CODE_NODE_TYPES:
        for node in _iter_all_nodes(store, type=ntype):
            if not _is_private_symbol(node.name):
                continue
            if _is_test_symbol(node.name, node.source_path):
                continue
            if node.id in called_ids:
                continue

            if judge is not None:
                confirmed = bool(
                    judge.judge(
                        name=node.name or "",
                        body=node.body or "",
                        source_path=node.source_path or "",
                    )
                )
                if not confirmed:
                    continue
                severity = "high"
            else:
                severity = "candidate"

            findings.append(
                {
                    "type": "dead_code",
                    "node_ids": [node.id],
                    "description": (
                        f"Private {ntype} {node.name!r} ({node.source_path}) "
                        f"has zero inbound call edges; "
                        + (
                            "LLM judge confirmed it is genuinely dead -- consider deleting it."
                            if severity == "high"
                            else "review whether it is dead or reached "
                            "dynamically (dispatch / decorator / hook)."
                        )
                    ),
                    "severity": severity,
                    "symbol": node.name,
                }
            )
    return findings


# --- Phase 3b (v5.17.0) -- code lens: god_object -----------------------

# A code_class with more than this many methods (inbound ``method_of``
# edges) is a "god class" candidate. Probe of the live corpus (902
# classes): mean 5.4, p90 11, max 92 -> ``> 25`` flags the top ~2%
# (genuine outliers), not the body of the distribution.
GOD_CLASS_METHOD_THRESHOLD = 25

# A code_module with more than this many top-level definitions
# (outbound ``defines`` edges) is a "god module" candidate, EXCLUDING
# test files. Probe (1795 modules): mean 3.2, p90 8, max 75 -> ``> 30``
# flags real large modules.
GOD_MODULE_DEFINES_THRESHOLD = 30

# Member-name list cap sent to the cohesion judge. The largest god
# class on the corpus has 92 methods; 80 keeps the prompt bounded
# while preserving enough names to judge cohesion.
_COHESION_MEMBERS_CAP = 80

_COHESION_JUDGE_SYSTEM_PROMPT = (
    "You are a code-cohesion grader. Given a class or module that the "
    "auditor flagged as oversized, plus the names of its members "
    "(methods / top-level definitions), decide whether it is a "
    "COHESIVE unit with a single clear responsibility (a facade / "
    "repository / domain service that is large but focused) or a "
    "GRAB-BAG of unrelated responsibilities that should be split. "
    "Respond with ONLY a JSON object of the shape "
    '{"should_split": true|false, "rationale": "<one short paragraph>"}. '
    "true = grab-bag that should be split; false = cohesive. No prose "
    "outside the JSON. No markdown fences."
)


@dataclass
class LLMCohesionJudge(_LLMHelper):
    """Opt-in cohesion judge for god_object candidates (v5.18.0).

    The 5th LLM helper, built on :class:`_LLMHelper`. Returns True
    when an oversized unit is a grab-bag that should be split, False
    when it is a cohesive facade (including on every error path, so a
    judge failure conservatively DROPS the candidate rather than
    falsely escalating)."""

    max_tokens: int = 400

    def judge(self, *, kind: str, name: str, members: list[str]) -> bool:
        """Return True if the unit should be split (grab-bag); False
        if cohesive (or on any error)."""
        shown = members[:_COHESION_MEMBERS_CAP]
        more = len(members) - len(shown)
        members_block = ", ".join(shown) + (f", ... (+{more} more)" if more > 0 else "")
        user_msg = (
            f"## Unit\nkind: {kind}\nname: {name}\nmember count: {len(members)}\n\n"
            f"## Members\n{members_block}\n\n"
            "## Task\nIs this a cohesive single-responsibility unit, or a "
            "grab-bag that should be split? Respond with ONLY the JSON."
        )
        parsed, err = self._invoke_json(system=_COHESION_JUDGE_SYSTEM_PROMPT, user=user_msg)
        if parsed is None:
            self.rationale_log.append(
                {
                    "kind": kind,
                    "name": name,
                    "should_split": False,
                    "rationale": err,
                    "parsed_ok": False,
                }
            )
            return False
        result = bool(parsed.get("should_split", False))
        self.rationale_log.append(
            {
                "kind": kind,
                "name": name,
                "should_split": result,
                "rationale": parsed.get("rationale", ""),
                "parsed_ok": True,
            }
        )
        return result


def god_object_judge_from_env() -> LLMCohesionJudge | None:
    """Construct an LLMCohesionJudge from environment when ALL of:
    ``MNEMO_ANALYZE_LLM_JUDGE`` is truthy, ``ANTHROPIC_API_KEY`` is
    set, and ``anthropic`` is importable. Shares the flag + model
    override with the other auditor judges."""
    flag = os.environ.get("MNEMO_ANALYZE_LLM_JUDGE", "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic  # type: ignore[import-untyped]
    except ImportError:
        log.warning(
            "god_object_judge_from_env: flag set but anthropic not installed; "
            "god_object stays deterministic (candidate)."
        )
        return None
    model = os.environ.get("MNEMO_ANALYZE_JUDGE_MODEL", "claude-sonnet-4-6")
    return LLMCohesionJudge(client=anthropic.Anthropic(), model=model)


def _member_names(store: Store, member_ids: list[str]) -> list[str]:
    """Resolve member node-ids to names (skipping any that no longer
    exist). Used only for god_object candidates when the cohesion
    judge is enabled -- bounded to the few candidates."""
    names: list[str] = []
    for mid in member_ids:
        n = store.get_node(mid)
        if n is not None:
            names.append(n.name or mid)
    return names


def detect_god_object(store: Store, *, judge: Any | None = None) -> list[dict[str, Any]]:
    """Surface oversized classes + modules by EXACT structural edge
    counts (Tier-1 ``method_of`` / ``defines`` edges are complete, not
    best-effort -- so the candidate gate is precise without an LLM).

    - **god class**: ``code_class`` with > ``GOD_CLASS_METHOD_THRESHOLD``
      inbound ``method_of`` edges (= methods).
    - **god module**: ``code_module`` with > ``GOD_MODULE_DEFINES_THRESHOLD``
      outbound ``defines`` edges (= top-level definitions), excluding
      test files (they legitimately define many test functions).

    Without a ``judge`` every candidate ships severity ``candidate``.
    With an opt-in :class:`LLMCohesionJudge`, each candidate's member
    names are graded: a grab-bag (``should_split``) becomes severity
    ``high``; a cohesive facade (or any judge error) is DROPPED
    (judge-authoritative-when-enabled, matching the other detectors).
    """
    # method_of: src=method, dst=class -> inbound count per class.
    try:
        method_edges = store.get_edges(relation="method_of")
    except Exception:  # noqa: BLE001 -- empty stores / missing table
        method_edges = []
    methods_per_class: dict[str, int] = {}
    members_by_class: dict[str, list[str]] = {}
    for e in method_edges:
        methods_per_class[e.dst_id] = methods_per_class.get(e.dst_id, 0) + 1
        if judge is not None:
            members_by_class.setdefault(e.dst_id, []).append(e.src_id)

    # defines: src=module, dst=decl -> outbound count per module.
    try:
        defines_edges = store.get_edges(relation="defines")
    except Exception:  # noqa: BLE001
        defines_edges = []
    defines_per_module: dict[str, int] = {}
    members_by_module: dict[str, list[str]] = {}
    for e in defines_edges:
        defines_per_module[e.src_id] = defines_per_module.get(e.src_id, 0) + 1
        if judge is not None:
            members_by_module.setdefault(e.src_id, []).append(e.dst_id)

    findings: list[dict[str, Any]] = []

    def _maybe_escalate(kind: str, node: Node, member_ids: list[str]) -> str | None:
        """Return the severity for a candidate, or None to DROP it.
        Without a judge: 'candidate'. With a judge: 'high' if it
        should split, else None (cohesive / error -> dropped)."""
        if judge is None:
            return "candidate"
        members = _member_names(store, member_ids)
        try:
            should_split = bool(judge.judge(kind=kind, name=node.name or "", members=members))
        except Exception as exc:  # noqa: BLE001 -- judge must never break the audit
            log.warning("detect_god_object: judge raised (%s); dropping candidate", exc)
            return None
        return "high" if should_split else None

    for node in _iter_all_nodes(store, type="code_class"):
        count = methods_per_class.get(node.id, 0)
        if count > GOD_CLASS_METHOD_THRESHOLD:
            severity = _maybe_escalate("class", node, members_by_class.get(node.id, []))
            if severity is None:
                continue
            findings.append(
                {
                    "type": "god_object",
                    "node_ids": [node.id],
                    "description": (
                        f"Class {node.name!r} ({node.source_path}) defines "
                        f"{count} methods (> {GOD_CLASS_METHOD_THRESHOLD}); "
                        + (
                            "LLM judge confirmed unrelated responsibilities -- split it."
                            if severity == "high"
                            else "consider splitting responsibilities."
                        )
                    ),
                    "severity": severity,
                    "symbol": node.name,
                }
            )

    for node in _iter_all_nodes(store, type="code_module"):
        if _is_test_symbol(node.name, node.source_path):
            continue
        count = defines_per_module.get(node.id, 0)
        if count > GOD_MODULE_DEFINES_THRESHOLD:
            severity = _maybe_escalate("module", node, members_by_module.get(node.id, []))
            if severity is None:
                continue
            findings.append(
                {
                    "type": "god_object",
                    "node_ids": [node.id],
                    "description": (
                        f"Module {node.name!r} ({node.source_path}) defines "
                        f"{count} top-level symbols (> {GOD_MODULE_DEFINES_THRESHOLD}); "
                        + (
                            "LLM judge confirmed unrelated responsibilities -- split it."
                            if severity == "high"
                            else "consider splitting it."
                        )
                    ),
                    "severity": severity,
                    "symbol": node.name,
                }
            )

    return findings


# --- Phase 3c (v5.19.0) -- code lens: cyclic_imports -------------------


def _tarjan_sccs(adj: dict[str, set[str]]) -> list[list[str]]:
    """Iterative Tarjan strongly-connected-components.

    Iterative (NOT recursive) so a deep import chain can't hit
    Python's recursion limit inside the daemon. Returns every SCC as
    a list of node ids (including singletons; the caller decides what
    counts as a cycle)."""
    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    counter = 0
    sccs: list[list[str]] = []

    for root in list(adj.keys()):
        if root in index_of:
            continue
        # work stack of (node, neighbour-iterator)
        work: list[tuple[str, Any]] = [(root, iter(adj.get(root, ())))]
        index_of[root] = lowlink[root] = counter
        counter += 1
        stack.append(root)
        on_stack[root] = True
        while work:
            node, it = work[-1]
            descended = False
            for w in it:
                if w not in index_of:
                    index_of[w] = lowlink[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack[w] = True
                    work.append((w, iter(adj.get(w, ()))))
                    descended = True
                    break
                if on_stack.get(w):
                    lowlink[node] = min(lowlink[node], index_of[w])
            if descended:
                continue
            # All neighbours processed -> close this node.
            if lowlink[node] == index_of[node]:
                comp: list[str] = []
                while True:
                    x = stack.pop()
                    on_stack[x] = False
                    comp.append(x)
                    if x == node:
                        break
                sccs.append(comp)
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
    return sccs


def detect_cyclic_imports(store: Store) -> list[dict[str, Any]]:
    """Surface module import cycles via an iterative Tarjan SCC over
    the ``imports`` edge graph (src=importer, dst=imported).

    A cycle is an SCC of size >= 2, OR a single module with a
    self-import edge. Deterministic + precise -- a cycle is
    unambiguous, so there is no LLM judge. Severity ``medium``: the
    cycle's existence is certain (peer to ``duplicates``); whether to
    break it is the user's call.
    """
    try:
        import_edges = store.get_edges(relation="imports")
    except Exception:  # noqa: BLE001 -- empty stores / missing table
        import_edges = []

    adj: dict[str, set[str]] = {}
    self_loops: set[str] = set()
    for e in import_edges:
        adj.setdefault(e.src_id, set()).add(e.dst_id)
        adj.setdefault(e.dst_id, set())  # ensure dst is a node in the graph
        if e.src_id == e.dst_id:
            self_loops.add(e.src_id)

    findings: list[dict[str, Any]] = []
    seen: set[frozenset[str]] = set()

    def _emit(members: list[str]) -> None:
        key = frozenset(members)
        if key in seen:
            return
        seen.add(key)
        ids = sorted(members)
        names = []
        for nid in ids:
            n = store.get_node(nid)
            names.append(n.name if (n and n.name) else nid)
        findings.append(
            {
                "type": "cyclic_import",
                "node_ids": ids,
                "description": (
                    f"Import cycle among {len(ids)} modules: "
                    f"{', '.join(names)}; an import cycle breaks modularity "
                    f"(complicates testing + import order) -- consider "
                    f"breaking it (extract a shared module, invert a "
                    f"dependency, or defer an import)."
                ),
                "severity": "medium",
            }
        )

    for comp in _tarjan_sccs(adj):
        if len(comp) >= 2 or len(comp) == 1 and comp[0] in self_loops:
            _emit(comp)

    return findings


# --- Orchestrator ------------------------------------------------------


# Canonical type strings the analyzer recognizes. Used by the
# ``types=`` filter on :func:`analyze`. ``orphan_references`` is
# plural for the API; the per-finding ``type`` is the singular
# ``orphan_reference`` (likewise ``semantic_orphans`` plural API,
# ``semantic_orphan`` singular per-finding).
#
# refactor_actions (v5.15.0) is NOT listed here -- it's an
# enrichment over findings, not a detector. The count stays 5.
KNOWN_DETECTOR_TYPES = (
    "stale",
    "duplicates",
    "orphan_references",
    "contradictions",  # v5.13.0 Phase 2a
    "semantic_orphans",  # v5.14.0 Phase 2b
)

# Phase 3 (v5.16.0): pluggable domain lenses. Each lens maps to a
# suite of domain-SPECIFIC detector type names. A lens REPLACES the
# agnostic suite above (it does not add to it) -- a lens is a focused
# domain audit; mixing suites would bury the signal (e.g.
# semantic_orphans floods a code corpus). Unknown lenses run nothing
# (permissive, matching the ``types`` contract).
LENS_DETECTORS: dict[str, tuple[str, ...]] = {
    # v5.16.0 dead_code; v5.17.0 god_object; v5.19.0 cyclic_imports
    "code": ("dead_code", "god_object", "cyclic_imports"),
}
KNOWN_LENSES = tuple(LENS_DETECTORS)


def analyze(
    store: Store,
    *,
    embedder: Any | None = None,
    types: list[str] | None = None,
    project_key: str | None = None,  # noqa: ARG001 -- reserved for future scoping
    judge: Any | None = None,
    orphan_judge: Any | None = None,
    propose_actions: bool | None = None,
    proposer: Any | None = None,
    lens: str | None = None,
    dead_code_judge: Any | None = None,
    god_object_judge: Any | None = None,
) -> dict[str, Any]:
    """Run the requested detectors + return a canonical envelope.

    Args:
        store: the live mnemo Store.
        embedder: optional Embedder for ``duplicates`` +
            ``contradictions``. When ``None``, both detectors return
            empty lists (clean fallback for stores without
            embeddings; tests can opt out).
        types: filter list, default = all detectors. Pass e.g.
            ``["stale"]`` to skip duplicates + orphan_references.
            v5.13.0: ``"contradictions"`` enables the new detector.
            v5.14.0: ``"semantic_orphans"`` enables the new detector.
        project_key: reserved for future scoping (currently no-op).
        judge: optional ``LLMContradictionJudge``. When provided +
            ``"contradictions"`` is in ``types``, candidates are
            escalated to the judge; confirmed -> severity ``high``;
            rejected -> dropped. When ``None``, candidates ship with
            severity ``candidate``. The default fetches a judge via
            :func:`judge_from_env` (which returns ``None`` unless
            the opt-in env flag + API key are set).
        orphan_judge: optional ``LLMSemanticOrphanJudge`` (v5.14.0).
            When provided + ``"semantic_orphans"`` is in ``types``,
            candidates are escalated to the judge with the same
            confirmed/rejected/drop semantics as ``judge``. Default
            fetches via :func:`semantic_orphan_judge_from_env`.
        propose_actions: opt-in refactor_actions enrichment (v5.15.0).
            ``True`` runs the enrichment (using ``proposer`` or an
            env-derived one); ``False`` disables it; ``None`` (default)
            enables it only when an env-derived proposer exists
            (``MNEMO_ANALYZE_PROPOSE_ACTIONS=1`` + key + anthropic).
            When it runs, each high/medium finding gains an ``action``
            dict and ``summary["_refactor_actions_skipped"]`` reports
            how many eligible findings the cap dropped.
        proposer: optional ``LLMRefactorProposer``. Caller-provided
            takes precedence over the env-derived one.
        lens: optional domain lens (v5.16.0). ``None`` (default) runs
            the agnostic suite (``KNOWN_DETECTOR_TYPES``). A known
            lens (see ``KNOWN_LENSES``, e.g. ``"code"``) REPLACES the
            agnostic suite with that lens's domain-specific detectors
            (e.g. ``dead_code``). ``types`` filters WITHIN the active
            suite. An unknown lens runs no detectors (permissive).
        dead_code_judge: optional ``LLMDeadCodeJudge`` (lens="code").
            Caller-provided > env-derived (:func:`dead_code_judge_from_env`)
            > None. Confirmed -> ``high``; rejected -> dropped.

    Returns:
        ``{ran_at, node_count_scanned, findings, summary}``. When the
        enrichment ran, eligible findings carry an ``action`` field
        and ``summary`` includes ``_refactor_actions_skipped``.
    """
    # Resolve the active detector suite. A lens REPLACES the agnostic
    # suite; ``types`` filters WITHIN whichever suite is active.
    # Intersecting with the suite keeps lens detectors lens-only and
    # agnostic detectors agnostic-only (a stray ``types`` value not in
    # the active suite simply runs nothing).
    # A lens REPLACES the agnostic suite; an unknown lens -> empty.
    suite = set(LENS_DETECTORS.get(lens, ())) if lens is not None else set(KNOWN_DETECTOR_TYPES)
    requested = (set(types) & suite) if types else set(suite)

    findings: list[dict[str, Any]] = []
    if "stale" in requested:
        findings.extend(detect_stale(store))
    if "duplicates" in requested:
        findings.extend(detect_duplicates(store, embedder=embedder))
    if "orphan_references" in requested:
        findings.extend(detect_orphan_references(store))
    if "contradictions" in requested:
        # Resolve the judge lazily: caller-provided > env-derived > None.
        resolved_judge = judge if judge is not None else judge_from_env()
        findings.extend(detect_contradictions(store, embedder=embedder, judge=resolved_judge))
    if "semantic_orphans" in requested:
        resolved_orphan_judge = (
            orphan_judge if orphan_judge is not None else semantic_orphan_judge_from_env()
        )
        findings.extend(detect_semantic_orphans(store, judge=resolved_orphan_judge))
    if "dead_code" in requested:
        resolved_dc_judge = (
            dead_code_judge if dead_code_judge is not None else dead_code_judge_from_env()
        )
        findings.extend(detect_dead_code(store, judge=resolved_dc_judge))
    if "god_object" in requested:
        resolved_go_judge = (
            god_object_judge if god_object_judge is not None else god_object_judge_from_env()
        )
        findings.extend(detect_god_object(store, judge=resolved_go_judge))
    if "cyclic_imports" in requested:
        findings.extend(detect_cyclic_imports(store))

    # refactor_actions enrichment (v5.15.0). Opt-in: resolve the
    # proposer lazily (caller-provided > env-derived > None). The
    # ``propose_actions`` flag gates it; when None it falls back to
    # the env flag via the proposer factory. A None proposer makes
    # the pass a no-op (byte-stable default).
    n_skipped: int | None = None
    want_actions = propose_actions
    if want_actions is None:
        # No explicit flag: enable only if the env-derived proposer
        # exists (mirrors the judge precedence).
        resolved_proposer = proposer if proposer is not None else refactor_proposer_from_env()
        want_actions = resolved_proposer is not None
    else:
        resolved_proposer = proposer if proposer is not None else refactor_proposer_from_env()
    if want_actions and resolved_proposer is not None:
        findings, n_skipped = propose_refactor_actions(store, findings, proposer=resolved_proposer)

    # Tally by type. ``orphan_reference`` (singular per-finding type)
    # is reported under the API-facing ``orphan_references`` (plural)
    # key so callers can match on the same vocabulary they passed via
    # ``types=`` (likewise ``semantic_orphan`` -> ``semantic_orphans``).
    summary: dict[str, int] = {}
    for f in findings:
        bucket = f["type"]
        if bucket == "orphan_reference":
            bucket = "orphan_references"
        elif bucket == "semantic_orphan":
            bucket = "semantic_orphans"
        summary[bucket] = summary.get(bucket, 0) + 1

    # Surface the skipped-due-to-cap count ONLY when the enrichment
    # pass actually ran (no silent caps; but don't inflate the
    # default deterministic summary with a noise key).
    if n_skipped is not None:
        summary["_refactor_actions_skipped"] = n_skipped

    return {
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "node_count_scanned": len(_iter_all_nodes(store)),
        "findings": findings,
        "summary": summary,
    }

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


# --- Phase 2a (v5.13.0) -- LLM judge -----------------------------------


@dataclass
class LLMContradictionJudge:
    """Opt-in binary judge for contradiction candidates.

    Wraps an Anthropic client; tests pass a MagicMock so no network
    call is made. Graceful: any parse/exception path returns False
    (the pair stays a deterministic 'candidate'; no false-positive
    'high')."""

    client: Any
    """The Anthropic client. Any object with
    ``messages.create(...)`` works (tests pass a MagicMock)."""

    model: str = "claude-sonnet-4-6"
    """Default judge model. Sonnet is the recommended grader for
    bench-style sweeps (lower latency + cost than Opus). Override
    via ``MNEMO_ANALYZE_JUDGE_MODEL`` env var."""

    max_tokens: int = 512
    """Token budget for the judge response. The structured output is
    small (~50-100 tokens for {contradiction, rationale}); 512 is a
    comfortable ceiling."""

    rationale_log: list[dict[str, Any]] = field(default_factory=list)
    """Per-pair audit trail. Operators can dump this after a sweep
    to inspect grading decisions."""

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
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=_JUDGE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            text = response.content[0].text
            parsed = json.loads(text)
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
        except (json.JSONDecodeError, KeyError, AttributeError, IndexError) as exc:
            log.warning(
                "LLMContradictionJudge: parse/structure error (%s); "
                "returning False (degrades to candidate-only)",
                exc,
            )
            self.rationale_log.append(
                {
                    "a_body": a,
                    "b_body": b,
                    "contradiction": False,
                    "rationale": f"PARSE_ERROR: {exc}",
                    "parsed_ok": False,
                }
            )
            return False
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "LLMContradictionJudge: client error (%s); returning False",
                exc,
            )
            self.rationale_log.append(
                {
                    "a_body": a,
                    "b_body": b,
                    "contradiction": False,
                    "rationale": f"CLIENT_ERROR: {exc}",
                    "parsed_ok": False,
                }
            )
            return False


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
class LLMSemanticOrphanJudge:
    """Opt-in binary judge for semantic-orphan candidates.

    Sibling to :class:`LLMContradictionJudge` -- different prompt
    + different return semantics. Wraps an Anthropic client; tests
    pass a MagicMock so no network call is made. Graceful: any
    parse/exception path returns False (the candidate is DROPPED
    when the judge is enabled; no false-positive 'high').
    """

    client: Any
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 512
    rationale_log: list[dict[str, Any]] = field(default_factory=list)

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
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=_ORPHAN_JUDGE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            text = response.content[0].text
            parsed = json.loads(text)
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
        except (json.JSONDecodeError, KeyError, AttributeError, IndexError) as exc:
            log.warning(
                "LLMSemanticOrphanJudge: parse/structure error (%s); "
                "returning False (candidate dropped)",
                exc,
            )
            self.rationale_log.append(
                {
                    "concept": concept,
                    "context": ctx,
                    "needs_definition": False,
                    "rationale": f"PARSE_ERROR: {exc}",
                    "parsed_ok": False,
                }
            )
            return False
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "LLMSemanticOrphanJudge: client error (%s); returning False",
                exc,
            )
            self.rationale_log.append(
                {
                    "concept": concept,
                    "context": ctx,
                    "needs_definition": False,
                    "rationale": f"CLIENT_ERROR: {exc}",
                    "parsed_ok": False,
                }
            )
            return False


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


# --- Orchestrator ------------------------------------------------------


# Canonical type strings the analyzer recognizes. Used by the
# ``types=`` filter on :func:`analyze`. ``orphan_references`` is
# plural for the API; the per-finding ``type`` is the singular
# ``orphan_reference`` (likewise ``semantic_orphans`` plural API,
# ``semantic_orphan`` singular per-finding).
KNOWN_DETECTOR_TYPES = (
    "stale",
    "duplicates",
    "orphan_references",
    "contradictions",  # v5.13.0 Phase 2a
    "semantic_orphans",  # v5.14.0 Phase 2b
)


def analyze(
    store: Store,
    *,
    embedder: Any | None = None,
    types: list[str] | None = None,
    project_key: str | None = None,  # noqa: ARG001 -- reserved for future scoping
    judge: Any | None = None,
    orphan_judge: Any | None = None,
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

    Returns:
        ``{ran_at, node_count_scanned, findings, summary}``.
    """
    requested = set(types) if types else set(KNOWN_DETECTOR_TYPES)

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

    return {
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "node_count_scanned": len(_iter_all_nodes(store)),
        "findings": findings,
        "summary": summary,
    }

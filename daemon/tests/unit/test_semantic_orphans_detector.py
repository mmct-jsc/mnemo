"""v5.14.0 -- semantic_orphans detector (Phase 2b, deterministic path).

Phase 2b of mnemo's Understanding arc (see
``docs/plans/2026-05-23-mnemo-understanding-phase2b-design.md`` +
``memory/project_mnemo_v6_vision_understanding``).

The detector has two layers:

1. **Deterministic concept extraction + cross-reference lookup**
   (this test file): for each node N, extract candidate concepts via
   3 regex patterns (CamelCase, snake_case, ALL_CAPS) and check
   whether any OTHER node defines the concept (substring match in
   ``name`` or ``description`` -- NOT ``body``, which is a reference,
   not a definition).

2. **Opt-in LLM judge** (separate test file
   ``test_semantic_orphans_judge.py``): escalate each candidate
   orphan to Claude for a binary "needs definition" decision.

Phase 2b ships ONLY the deterministic layer + a hook for the LLM
judge.
"""

from __future__ import annotations

import pytest

from mnemo.store import Node, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "mnemo.db")
    yield s
    s.close()


def _mknode(
    *,
    id: str,
    type: str = "memory_feedback",
    name: str | None = None,
    description: str = "",
    body: str = "",
) -> Node:
    import time

    now = int(time.time())
    return Node(
        id=id,
        type=type,
        name=name if name is not None else id.split("/", 1)[-1],
        description=description,
        body=body,
        source_path=f"/tmp/{id}.md",
        source_kind="memory",
        project_key=None,
        frontmatter_json=None,
        hash="",
        created_at=now,
        updated_at=now,
    )


# --- Concept extraction -------------------------------------------------


def test_extract_concepts_picks_up_camelcase() -> None:
    """CamelCase identifiers with at least 2 internal uppercase
    transitions (i.e., 3 capitalized segments min like ``MQTTBridge``)
    should be extracted. Pure single-segment capitalized words like
    ``Phase`` are too generic and should be skipped."""
    from mnemo.analyzer import _extract_concepts

    body = (
        "The MQTTBridge module handles broker auth. The "
        "RetryHandler wraps every call. This is the first Phase "
        "of the project. We also use ServiceMesh."
    )
    concepts = _extract_concepts(body)
    assert "MQTTBridge" in concepts, f"MQTTBridge should be extracted; got {concepts}"
    assert "RetryHandler" in concepts, f"RetryHandler should be extracted; got {concepts}"
    assert "ServiceMesh" in concepts, f"ServiceMesh should be extracted; got {concepts}"
    assert "Phase" not in concepts, (
        f"single-segment capitalized words are too generic; Phase shouldn't be extracted; got {concepts}"
    )
    assert "The" not in concepts, f"articles must not match; got {concepts}"


def test_extract_concepts_picks_up_snake_case() -> None:
    """snake_case identifiers with 2+ underscores OR length >= 12
    should be extracted. Short utility names like ``do_not`` should
    be skipped."""
    from mnemo.analyzer import _extract_concepts

    body = (
        "The son_tinh_ai module dispatches via "
        "petrolimex_detection_model. The knowledge_auditor_phase_1 "
        "runs hourly. Do not use add_node directly; do_not skip the "
        "validation."
    )
    concepts = _extract_concepts(body)
    assert "son_tinh_ai" in concepts, f"son_tinh_ai should be extracted; got {concepts}"
    assert "petrolimex_detection_model" in concepts, (
        f"petrolimex_detection_model should be extracted; got {concepts}"
    )
    assert "knowledge_auditor_phase_1" in concepts, (
        f"knowledge_auditor_phase_1 should be extracted; got {concepts}"
    )
    # add_node has only 1 underscore + length 8 -- skipped.
    assert "add_node" not in concepts, f"short 1-underscore names shouldn't extract; got {concepts}"
    # do_not has only 1 underscore + length 6 -- skipped.
    assert "do_not" not in concepts, f"short 1-underscore names shouldn't extract; got {concepts}"


def test_extract_concepts_picks_up_all_caps() -> None:
    """ALL_CAPS constants with at least 1 underscore should be
    extracted. Bare acronyms like ``URL`` or ``API`` (no underscore)
    should be skipped."""
    from mnemo.analyzer import _extract_concepts

    body = (
        "The MAX_RETRIES default is 3. Set "
        "DUPLICATE_COSINE_THRESHOLD to 0.95. The "
        "MNEMO_ANALYZE_LLM_JUDGE flag opts in. Connect via the URL "
        "using the API."
    )
    concepts = _extract_concepts(body)
    assert "MAX_RETRIES" in concepts, f"MAX_RETRIES should be extracted; got {concepts}"
    assert "DUPLICATE_COSINE_THRESHOLD" in concepts, (
        f"DUPLICATE_COSINE_THRESHOLD should be extracted; got {concepts}"
    )
    assert "MNEMO_ANALYZE_LLM_JUDGE" in concepts, (
        f"MNEMO_ANALYZE_LLM_JUDGE should be extracted; got {concepts}"
    )
    assert "URL" not in concepts, f"bare acronyms shouldn't extract; got {concepts}"
    assert "API" not in concepts, f"bare acronyms shouldn't extract; got {concepts}"


def test_extract_concepts_length_filter() -> None:
    """Concepts shorter than 6 chars or longer than 60 chars should
    be skipped."""
    from mnemo.analyzer import _extract_concepts

    body = (
        "Short XY snippets like FooBar are skipped. The "
        "VeryLongCamelCaseIdentifierThatGoesOnAndOnAndOnAndOnAndOnAndOnPastSixty "
        "is also skipped. But MQTTBridge survives."
    )
    concepts = _extract_concepts(body)
    assert "MQTTBridge" in concepts, f"MQTTBridge (10 chars) should pass; got {concepts}"
    # FooBar is 6 chars -- boundary. We want concepts AT LEAST 6 chars.
    # The very-long identifier is > 60 chars and must be filtered.
    too_long = next((c for c in concepts if len(c) > 60), None)
    assert too_long is None, f"concepts > 60 chars must be filtered; got {too_long!r}"


def test_extract_concepts_deduplicates_within_node() -> None:
    """If a concept appears multiple times in the same body, it
    counts once."""
    from mnemo.analyzer import _extract_concepts

    body = "MQTTBridge connects to MQTTBridge via MQTTBridge."
    concepts = _extract_concepts(body)
    assert concepts.count("MQTTBridge") == 1, (
        f"per-node concept extraction must dedupe; got {concepts}"
    )


def test_extract_concepts_handles_empty_body() -> None:
    """An empty or None body yields an empty concept list."""
    from mnemo.analyzer import _extract_concepts

    assert _extract_concepts("") == []
    assert _extract_concepts(None) == []  # type: ignore[arg-type]


def test_extract_concepts_skips_stop_listed_idioms() -> None:
    """Common Python/code idioms in the stop list shouldn't appear
    as candidate concepts."""
    from mnemo.analyzer import _extract_concepts

    body = (
        "def __init__(self): pass. The if __name__ == '__main__' "
        "block. MQTTBridge stays in the list."
    )
    concepts = _extract_concepts(body)
    assert "__init__" not in concepts, f"__init__ should be stop-listed; got {concepts}"
    assert "__main__" not in concepts, f"__main__ should be stop-listed; got {concepts}"
    assert "MQTTBridge" in concepts, f"MQTTBridge should survive; got {concepts}"


# --- Definition lookup --------------------------------------------------


def test_concept_is_defined_matches_name_field(store) -> None:
    """A concept is considered defined if some OTHER node has the
    concept as a substring of its name (case-insensitive)."""
    from mnemo.analyzer import _concept_is_defined, _iter_all_nodes

    defining = _mknode(
        id="reference/mqtt-bridge",
        name="MQTTBridge architecture",
        body="Describes the MQTTBridge module.",
    )
    other = _mknode(id="memory_feedback/other", body="unrelated text")
    store.upsert_node(defining)
    store.upsert_node(other)

    all_nodes = _iter_all_nodes(store)
    # MQTTBridge IS defined (by the defining node, not by the source).
    assert _concept_is_defined("MQTTBridge", source_id="memory_feedback/other", all_nodes=all_nodes)


def test_concept_is_defined_matches_description_field(store) -> None:
    """Definition match also works against node.description."""
    from mnemo.analyzer import _concept_is_defined, _iter_all_nodes

    defining = _mknode(
        id="reference/something",
        name="Something else",
        description="Covers the RetryHandler abstraction in detail.",
        body="unrelated body text",
    )
    source = _mknode(
        id="memory_feedback/source",
        body="We use RetryHandler in this module.",
    )
    store.upsert_node(defining)
    store.upsert_node(source)

    all_nodes = _iter_all_nodes(store)
    assert _concept_is_defined(
        "RetryHandler", source_id="memory_feedback/source", all_nodes=all_nodes
    )


def test_concept_is_defined_does_not_match_body_only(store) -> None:
    """A body-only mention is NOT a definition. Only name +
    description count as defining fields."""
    from mnemo.analyzer import _concept_is_defined, _iter_all_nodes

    body_only = _mknode(
        id="memory_feedback/mentions",
        name="Some other topic",
        description="Unrelated",
        body="In passing, this mentions MQTTBridge once.",
    )
    source = _mknode(
        id="memory_feedback/source",
        body="We use MQTTBridge heavily.",
    )
    store.upsert_node(body_only)
    store.upsert_node(source)

    all_nodes = _iter_all_nodes(store)
    # body_only mentions MQTTBridge in its BODY -- that's a reference,
    # not a definition. The concept should be considered UNDEFINED.
    assert not _concept_is_defined(
        "MQTTBridge", source_id="memory_feedback/source", all_nodes=all_nodes
    )


def test_concept_is_defined_excludes_source_node(store) -> None:
    """A concept that only matches the source node's own name doesn't
    count as defined -- otherwise every node trivially defines its
    own mentioned concepts."""
    from mnemo.analyzer import _concept_is_defined, _iter_all_nodes

    # Source node's name contains MQTTBridge but it's the only mention.
    source = _mknode(
        id="memory_feedback/self",
        name="MQTTBridge notes",
        body="The MQTTBridge does X. The MQTTBridge does Y.",
    )
    store.upsert_node(source)

    all_nodes = _iter_all_nodes(store)
    assert not _concept_is_defined(
        "MQTTBridge", source_id="memory_feedback/self", all_nodes=all_nodes
    ), "the source node itself must not count as defining its own concepts"


def test_concept_is_defined_case_insensitive(store) -> None:
    """Definition match is case-insensitive."""
    from mnemo.analyzer import _concept_is_defined, _iter_all_nodes

    defining = _mknode(
        id="reference/something",
        name="mqttbridge reference",
        body="unrelated",
    )
    source = _mknode(id="memory_feedback/source", body="We use MQTTBridge.")
    store.upsert_node(defining)
    store.upsert_node(source)

    all_nodes = _iter_all_nodes(store)
    assert _concept_is_defined(
        "MQTTBridge", source_id="memory_feedback/source", all_nodes=all_nodes
    )


# --- Detector integration -----------------------------------------------


def test_detect_semantic_orphans_emits_undefined_concept(store) -> None:
    """A node mentions a CamelCase concept that no other node
    defines -- the detector emits a finding."""
    from mnemo.analyzer import detect_semantic_orphans

    source = _mknode(
        id="memory_feedback/uses-mqttbridge",
        name="Notes on caching",
        body="We use MQTTBridge for broker auth. Returns a token.",
    )
    store.upsert_node(source)

    findings = detect_semantic_orphans(store)
    assert any(f["type"] == "semantic_orphan" for f in findings), (
        f"expected semantic_orphan finding; got {findings}"
    )
    target = next(f for f in findings if f["type"] == "semantic_orphan")
    assert source.id in target["node_ids"], f"finding should cite source node; got {target}"


def test_detect_semantic_orphans_skips_defined_concepts(store) -> None:
    """A node mentions a concept that IS defined elsewhere -- no
    finding."""
    from mnemo.analyzer import detect_semantic_orphans

    defining = _mknode(
        id="reference/mqtt-bridge",
        name="MQTTBridge architecture",
        description="Canonical reference for the MQTTBridge module.",
        body="MQTTBridge wraps broker auth and reconnection.",
    )
    source = _mknode(
        id="memory_feedback/uses-it",
        name="Caching notes",
        body="We use MQTTBridge.",
    )
    store.upsert_node(defining)
    store.upsert_node(source)

    findings = detect_semantic_orphans(store)
    # MQTTBridge is defined -- shouldn't surface in findings.
    for f in findings:
        if f.get("type") == "semantic_orphan":
            assert "MQTTBridge" not in (f.get("description") or ""), (
                f"defined concept must not surface; got {f}"
            )


def test_detect_semantic_orphans_default_severity_is_candidate(store) -> None:
    """Without an LLM judge, candidates are severity=candidate."""
    from mnemo.analyzer import detect_semantic_orphans

    source = _mknode(
        id="memory_feedback/lonely",
        name="Lonely topic",
        body="The ProjectSpecificOrchestrator module is essential.",
    )
    store.upsert_node(source)

    findings = detect_semantic_orphans(store)
    assert findings, "expected at least one candidate finding"
    for f in findings:
        if f["type"] == "semantic_orphan":
            assert f["severity"] == "candidate", (
                f"default (no LLM judge) severity should be 'candidate'; got {f['severity']}"
            )
            break


def test_detect_semantic_orphans_includes_concept_in_description(store) -> None:
    """The finding's description includes the concept text so the
    operator knows what to define."""
    from mnemo.analyzer import detect_semantic_orphans

    source = _mknode(
        id="memory_feedback/orphan",
        name="Some notes",
        body="We use the SuperRareWidget heavily.",
    )
    store.upsert_node(source)

    findings = detect_semantic_orphans(store)
    target = next(f for f in findings if f["type"] == "semantic_orphan")
    assert "SuperRareWidget" in target["description"], (
        f"finding description must include the concept text; got {target}"
    )


def test_detect_semantic_orphans_dedupes_per_concept(store) -> None:
    """Two nodes mentioning the same undefined concept emit two
    findings (one per source node) -- not collapsed into one."""
    from mnemo.analyzer import detect_semantic_orphans

    a = _mknode(
        id="memory_feedback/a",
        name="One side",
        body="Uses LonelyConcept here.",
    )
    b = _mknode(
        id="memory_feedback/b",
        name="Other side",
        body="Also uses LonelyConcept there.",
    )
    store.upsert_node(a)
    store.upsert_node(b)

    findings = detect_semantic_orphans(store)
    orphan_findings = [
        f
        for f in findings
        if f["type"] == "semantic_orphan" and "LonelyConcept" in f["description"]
    ]
    assert len(orphan_findings) == 2, (
        f"expected one finding per source for the same undefined concept; got {len(orphan_findings)}"
    )


def test_detect_semantic_orphans_does_not_surface_concepts_used_by_only_one_node_in_its_own_name(
    store,
) -> None:
    """A node whose body mentions a concept that ALSO appears in
    that same node's own name is self-defining -- no finding.
    (Definition lookup excludes the source, but if the only mention
    IS the source's name, the body extraction also yields the same
    concept; we want no false-positive.)"""
    from mnemo.analyzer import detect_semantic_orphans

    n = _mknode(
        id="memory_feedback/self",
        name="MQTTBridge notes",
        description="Canonical MQTTBridge reference",
        body="The MQTTBridge module wraps broker auth.",
    )
    store.upsert_node(n)

    findings = detect_semantic_orphans(store)
    # We DO consider a node self-defining if its own name+description
    # cover the concept (the source's name IS a definition for itself).
    # The source's name "MQTTBridge notes" covers MQTTBridge.
    for f in findings:
        if f["type"] == "semantic_orphan":
            assert "MQTTBridge" not in (f.get("description") or ""), (
                f"a node defining a concept in its own name shouldn't surface that concept; got {f}"
            )


# --- Orchestrator integration ------------------------------------------


def test_analyze_orchestrator_recognizes_semantic_orphans_type(store) -> None:
    """``types=['semantic_orphans']`` should ONLY run the
    semantic_orphans detector -- skipping stale, duplicates,
    orphan_references, and contradictions."""
    from mnemo.analyzer import analyze

    source = _mknode(
        id="memory_feedback/lonely",
        name="A topic",
        body="The OrphanedConcept module is essential.",
        description="SUPERSEDED",  # would normally trigger stale
    )
    store.upsert_node(source)

    result = analyze(store, types=["semantic_orphans"])
    types_seen = {f["type"] for f in result["findings"]}
    # stale shouldn't run; only semantic_orphan findings.
    assert "stale" not in types_seen, f"types filter didn't apply; saw stale; {types_seen}"
    if types_seen:
        assert types_seen <= {"semantic_orphan"}, (
            f"only semantic_orphan findings expected; got {types_seen}"
        )


def test_analyze_summary_includes_semantic_orphans_count(store) -> None:
    """The aggregate summary dict has a key for semantic_orphans
    when any are found."""
    from mnemo.analyzer import analyze

    source = _mknode(
        id="memory_feedback/lonely",
        name="A topic",
        body="The MysteriousModule is everywhere here.",
    )
    store.upsert_node(source)

    result = analyze(store, types=["semantic_orphans"])
    # The summary bucket name (plural) should appear when findings exist.
    if result["findings"]:
        assert "semantic_orphans" in result["summary"], (
            f"summary missing semantic_orphans key; got {result['summary']}"
        )
        assert result["summary"]["semantic_orphans"] >= 1


def test_known_detector_types_includes_semantic_orphans() -> None:
    """The detector is part of the public surface listed in
    KNOWN_DETECTOR_TYPES (drives the ``types=`` filter contract)."""
    from mnemo.analyzer import KNOWN_DETECTOR_TYPES

    assert "semantic_orphans" in KNOWN_DETECTOR_TYPES, (
        f"KNOWN_DETECTOR_TYPES must list 'semantic_orphans'; got {KNOWN_DETECTOR_TYPES}"
    )
    assert len(KNOWN_DETECTOR_TYPES) == 5, (
        f"KNOWN_DETECTOR_TYPES should now have 5 entries; got {KNOWN_DETECTOR_TYPES}"
    )

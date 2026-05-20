"""Phase 1.5: structured risk taxonomy on the MCP tool surface.

The ``risk`` field has always been on ``ToolSpec`` (``RISK_SAFE`` /
``RISK_CONFIRM`` / ``RISK_DANGER``) and ``tool_list()`` already
returned it as a structured field. Phase 1.5 hardens the contract:

- ``Risk`` is now a ``Literal`` type alias, not a free-form ``str``.
  Edit-time type-checkers (ruff / mypy / pyright) catch bogus values
  immediately rather than only at runtime via the contract test.
- ``ALL_RISKS`` is the single source of truth for the taxonomy.
  Tests and host-side validators import it instead of hardcoding the
  set in multiple places.

Locks the wire contract for hosts (Cursor, OpenAI Agents SDK,
Continue, ...): ``descriptor["risk"] in ("safe", "confirm", "danger")``
without parsing the description string.

The Phase 0 contract test (``test_mcp_tool_surface_contract.py``)
guarantees the tool *names* stay stable; this test guarantees their
*risk taxonomy* stays stable and structurally exposed.
"""

from __future__ import annotations

from mnemo.agent_tools import (
    ALL_RISKS,
    RISK_CONFIRM,
    RISK_DANGER,
    RISK_SAFE,
    TOOLS,
)
from mnemo.mcp_server import tool_list


def test_all_risks_is_the_three_documented_values_in_order() -> None:
    """ALL_RISKS is the single source of truth. Order is least-to-most
    dangerous so hosts can iterate / display in order without sorting."""
    assert ALL_RISKS == (RISK_SAFE, RISK_CONFIRM, RISK_DANGER)
    assert RISK_SAFE == "safe"
    assert RISK_CONFIRM == "confirm"
    assert RISK_DANGER == "danger"


def test_every_registered_tool_carries_a_risk_in_the_taxonomy() -> None:
    """Belt-and-braces with the Phase 0 contract test: same check, but
    importing ALL_RISKS instead of hardcoding the set. If the taxonomy
    ever expands (a new risk level), this test stays correct while the
    Phase 0 hardcoded set has to be updated -- a useful divergence so
    one of them red-flags an unintentional taxonomy widening."""
    for name, spec in TOOLS.items():
        assert spec.risk in ALL_RISKS, f"{name}: risk={spec.risk!r} not in ALL_RISKS={ALL_RISKS}"


def test_tool_list_descriptor_exposes_risk_as_structured_field() -> None:
    """Hosts gate via ``descriptor['risk']`` directly -- no description
    string parsing required. This is the contract that lets Cursor /
    OpenAI Agents SDK / etc. default-deny ``confirm`` and ``danger``
    rows without grepping the description for ``(risk: danger)``.
    """
    for desc in tool_list():
        assert "risk" in desc, f"{desc.get('name', '?')}: missing 'risk' field"
        assert desc["risk"] in ALL_RISKS, (
            f"{desc['name']}: risk={desc['risk']!r} not in {ALL_RISKS}"
        )


def test_every_risk_level_has_at_least_one_tool() -> None:
    """All three risk levels are populated by the current registry.
    A level with zero tools is a strong signal something has been
    accidentally removed -- catch it here rather than discovering it
    when a host stops seeing destructive tools."""
    by_risk = dict.fromkeys(ALL_RISKS, 0)
    for spec in TOOLS.values():
        by_risk[spec.risk] += 1
    for r in ALL_RISKS:
        assert by_risk[r] >= 1, f"no tools with risk={r!r}; taxonomy populated unevenly: {by_risk}"

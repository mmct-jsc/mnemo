"""Lock the published MCP tool surface so accidental rename/removal breaks CI.

Two consumers read ``mnemo.agent_tools.TOOLS``: the in-process agent loop
(``mnemo.chat``) and the MCP server (``mnemo.mcp_server``). External
MCP-capable hosts (Cursor, OpenAI Agents SDK, ...) bind to these tool
names; renaming or removing one is a breaking change to the wire
contract.

Policy
------
- **Adding a new tool is fine.** Extend ``EXPECTED_TOOLS`` here and ship
  a docs entry (``docs/integrations/wire-schema.md``) in the same PR.
- **Renaming or removing an existing tool requires an intentional
  update here.** That's what flips this test red on CI, forcing the
  author to confront the breaking change.

This test is the Phase 0 safety net of the enterprise execution plan;
every later MCP change (Phase 1.5 risk tags, Phase 1.6 wire-schema
snapshot) is built on top of it.
"""

from mnemo.agent_tools import TOOLS

# Snapshot of the published MCP tool surface as of 2026-06-01
# (post-v5.23.0). Counts: 11 safe + 14 confirm + 4 danger = 29.
# v5.12.0 added ``mnemo_analyze`` (safe) for the knowledge auditor;
# v5.22.0 added ``mnemo_audit_queue`` (safe, read-only) for the Phase 4a
# proactive audit queue; v5.23.0 added ``mnemo_apply_finding`` (confirm)
# for the Phase 4b confirm-then-apply (the first node mutation); the
# 26-tool surface from v4.6.5+ is preserved byte-stable.
EXPECTED_TOOLS: frozenset[str] = frozenset(
    {
        # --- safe reads (Phase 1 substrate-hardening surface) -----------
        "mnemo_query",
        "mnemo_get_node",
        "mnemo_get_edges",
        "mnemo_traverse",
        "mnemo_search_by_type",
        "mnemo_get_code_lines",
        "mnemo_page_context",
        "mnemo_session_nodes",
        "mnemo_list_skills",
        "mnemo_analyze",  # v5.12.0: knowledge auditor (Phase 1)
        "mnemo_audit_queue",  # v5.22.0: proactive audit queue (Phase 4a, read-only)
        # --- confirm (recoverable mutations + UI directives + skill load)
        "mnemo_create_node",
        "mnemo_update_node",
        "mnemo_apply_finding",  # v5.23.0: confirm-then-apply (Phase 4b, first mutation)
        "mnemo_thumbs_feedback",
        "mnemo_add_source",
        "mnemo_reindex_source",
        "mnemo_apply_retune",
        "mnemo_navigate",
        "mnemo_select_node",
        "mnemo_set_filter",
        "mnemo_scroll_to",
        "mnemo_open_panel",
        "mnemo_highlight_nodes",
        "mnemo_run_skill",
        # --- danger (destructive, always prompts) -----------------------
        "mnemo_delete_node",
        "mnemo_remove_source",
        "mnemo_purge_conversation",
        "mnemo_change_settings",
    }
)


def test_mcp_tool_surface_has_no_removed_or_renamed_tools() -> None:
    """Every EXPECTED tool must still be present in TOOLS.

    A new tool is fine (additive); a missing one means a rename or a
    deletion that needs an intentional EXPECTED_TOOLS update + a docs
    entry.
    """
    actual = set(TOOLS.keys())
    missing = EXPECTED_TOOLS - actual
    assert not missing, (
        f"MCP tool surface broke: removed or renamed tools {sorted(missing)}. "
        "If this is intentional, update EXPECTED_TOOLS here AND "
        "docs/integrations/wire-schema.md."
    )


def test_mcp_tool_names_are_unique() -> None:
    """Belt-and-braces: TOOLS is a dict so duplicates can't co-exist,
    but if the registry ever becomes a list, this catches duplicates."""
    names = list(TOOLS.keys())
    assert len(names) == len(set(names)), (
        f"duplicate MCP tool name detected: {sorted({n for n in names if names.count(n) > 1})}"
    )


def test_every_tool_has_required_metadata() -> None:
    """Each ToolSpec must carry the four fields external hosts read."""
    for name, spec in TOOLS.items():
        assert spec.name == name, f"TOOLS[{name!r}].name == {spec.name!r} mismatch"
        assert spec.description, f"{name}: empty description"
        assert spec.risk in {"safe", "confirm", "danger"}, f"{name}: bad risk {spec.risk!r}"
        assert isinstance(spec.parameters, dict), f"{name}: parameters not a dict"
        assert spec.parameters.get("type") == "object", (
            f"{name}: parameters must be a JSON-Schema object"
        )

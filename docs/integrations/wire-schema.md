# MCP wire schema

The contract every integration in this directory binds to. External
hosts (Cursor, the OpenAI Agents SDK, Continue, Zed, ...) consume
this JSON verbatim through the MCP `tools/list` handshake. Change it
deliberately, never accidentally.

## Stability promises

The three Phase 1 tests enforce three orthogonal slices of the
contract:

| Promise | Test | Caught failure mode |
|---|---|---|
| Tool **names** stay stable | [`test_mcp_tool_surface_contract.py`](../../daemon/tests/unit/test_mcp_tool_surface_contract.py) | rename / removal of a tool the docs reference |
| The **risk taxonomy** stays stable + structurally exposed | [`test_mcp_tool_risk_tags.py`](../../daemon/tests/unit/test_mcp_tool_risk_tags.py) | new risk level, missing `risk` field on a descriptor, empty risk bucket |
| The **JSON shape** stays stable byte-for-byte | [`test_mcp_wire_schema_snapshot.py`](../../daemon/tests/unit/test_mcp_wire_schema_snapshot.py) | description reword, inputSchema field rename, parameter switching from required to optional, new descriptor field |

Together they let mount-guide authors and external hosts assume:

> Every tool listed under `mcpServers.mnemo` in `~/.cursor/mcp.json`
> or wired into `MCPServerStdio` in the OpenAI Agents SDK will be
> present, with the same name, same risk classification, and the
> same input schema, on every released mnemo version — until a
> mnemo PR explicitly updates the snapshot.

## Descriptor shape

Each entry in the `tools/list` response is exactly:

```json
{
  "name": "mnemo_query",
  "description": "Hybrid Graph-RAG retrieval over memory + code, ranked and token-budgeted. Use for broad research. Returns ranked hits each with a [mnemo:<id>] citation. (risk: safe)",
  "inputSchema": {
    "type": "object",
    "properties": {
      "prompt": { "type": "string", "description": "natural-language query" },
      "limit": { "type": "integer", "default": 8 },
      "max_tokens": { "type": "integer", "default": 800 },
      "project_key": { "type": ["string", "null"], "default": null }
    },
    "required": ["prompt"]
  },
  "risk": "safe"
}
```

Fields:

- **`name`** — stable identifier. External hosts bind to this string.
  Locked by the Phase 0 contract test.
- **`description`** — human-readable. **Includes the `(risk: <value>)`
  textual suffix** as a legacy fallback signal for hosts that don't
  parse the structured `risk` field. Hosts SHOULD prefer the
  structured field.
- **`inputSchema`** — JSON Schema (object). Each tool's per-parameter
  shape is defined inline in `daemon/mnemo/agent_tools.py` and
  reflected here verbatim.
- **`risk`** — structured `"safe"` | `"confirm"` | `"danger"`. The
  preferred gating field for hosts. Locked by the Phase 1.5
  risk-tags test.

The current full 30-tool surface lives in
[`mcp_tool_list.json`](../../daemon/tests/unit/_snapshots/mcp_tool_list.json)
(the snapshot the wire-schema test compares against on every run).

## Updating the snapshot

If you intentionally change the wire shape (new tool, new field,
reworded description, expanded `inputSchema`), regenerate the
snapshot:

```bash
cd daemon
MNEMO_UPDATE_SNAPSHOTS=1 uv run pytest tests/unit/test_mcp_wire_schema_snapshot.py
git diff -- tests/unit/_snapshots/mcp_tool_list.json
```

Review **every byte** of the diff. The snapshot exists to surface
drift; regenerating without reading it defeats the purpose.

Then:

1. Update [`docs/integrations/cursor.md`](./cursor.md) and
   [`docs/integrations/openai-agents-sdk.md`](./openai-agents-sdk.md)
   if the visible surface (tool count, risk taxonomy, key tool
   names) changed.
2. Update [`docs/integrations/PICKS.md`](./PICKS.md) only if a host
   has to be re-evaluated against the new shape (rare).
3. Update this file if the **descriptor shape itself** changed
   (a new field, a renamed key, etc.).
4. Bump the affected mnemo minor version per
   [`reference_mnemo_pipelines`](../../README.md) #4.

## When the snapshot fails CI

The test failure message includes the exact regenerate command and
the first differing line. Before regenerating:

1. **Is this change intentional?** If a tool was renamed by
   mistake, revert in code rather than updating the snapshot.
   Read the diff first.
2. **Did the description change?** Phase 1.5 promotes `risk` to a
   first-class field; many old descriptions ended with `(risk:
   <value>)`. A description edit that drops the parenthetical
   still works on the wire (the structured field is the source of
   truth) but updates the snapshot.
3. **Did the input schema widen?** Adding a new optional parameter
   is backward-compatible. Adding a required parameter, or
   tightening a type union, is a breaking change for clients —
   bump the minor version.
4. **Is a host integration affected?** If yes, link the snapshot
   diff in the per-host mount doc's "What changed in vN.M" section.

## Anti-patterns

- **Don't** regenerate the snapshot in a commit that also changes
  `agent_tools.py`. Split: one commit makes the code change + the
  snapshot regen, with a clear message; one commit (if any) tidies
  up. Mixing leaves "snapshot drift" commits in the history that
  reviewers can't reason about.
- **Don't** modify the snapshot by hand. The test re-serializes the
  live `tool_list()` output; manual edits will fail on the next
  run with a confusing diff.
- **Don't** delete the snapshot to make the test "go away". The
  `test_snapshot_file_is_committed` belt-and-braces test catches
  exactly that.

"""v6.1.0 G1: the pure governance core.

``governance.parse_rule`` reads a rule's frontmatter (already stored whole in
``Node.frontmatter_json`` by ingest) into a ``Rule``; ``rule_applies`` decides
whether a rule is binding for a given context (file path / task intent / tool
call). Pure -- no store, no model. Malformed rules MUST fail open (parse as a
non-binding ``inform``/``SHOULD`` rule, never raise) so a bad rule file can
never brick retrieval or a hook.
"""

from __future__ import annotations

import json

from mnemo import governance as gov


def _fm(rule_block: dict | None, **top) -> dict:
    fm = dict(top)
    if rule_block is not None:
        fm["rule"] = rule_block
    return fm


def test_constants_are_the_governance_vocab() -> None:
    assert gov.MODALITIES == ("MUST", "MUST_NOT", "SHOULD")
    assert gov.ENFORCEMENTS == ("inform", "warn", "require-ack", "block")


def test_parse_rule_full_block() -> None:
    fm = _fm(
        {
            "id": "rule.commit.prefix",
            "modality": "MUST",
            "enforcement": "block",
            "applies_to": {
                "glob": ["**/*.py"],
                "intent": ["feedback-recall"],
                "tool": ["Bash"],
                "tool_arg_match": "git commit",
            },
            "verify": {"command": "uv run ruff check .", "expect_exit": 0},
            "requires_step": "review",
        }
    )
    r = gov.parse_rule(fm, name="conventional-commit-prefix", node_id="n1", text="use a prefix")
    assert r.id == "rule.commit.prefix"
    assert r.modality == "MUST"
    assert r.enforcement == "block"
    assert r.glob == ["**/*.py"]
    assert r.tool == ["Bash"]
    assert r.tool_arg_match == "git commit"
    assert r.verify_command == "uv run ruff check ."
    assert r.verify_expect_exit == 0
    assert r.requires_step == "review"
    assert r.is_mandatory is True


def test_parse_rule_missing_block_defaults_to_advisory() -> None:
    r = gov.parse_rule(_fm(None), name="x", node_id="n", text="")
    assert r.modality == "SHOULD"
    assert r.enforcement == "inform"
    assert r.is_mandatory is False
    assert r.id == "x"  # falls back to the node name


def test_parse_rule_garbage_modality_and_enforcement_fail_open() -> None:
    r = gov.parse_rule(
        _fm({"modality": "MAYBE", "enforcement": "nuke-from-orbit"}),
        name="x",
        node_id="n",
        text="",
    )
    assert r.modality == "SHOULD", "unknown modality downgrades, never raises"
    assert r.enforcement == "inform", "unknown enforcement downgrades to inform (fail-open)"


def test_parse_rule_tolerates_non_dict_rule_block() -> None:
    # a hand-written file with `rule: just a string` must not crash ingestion/parse
    r = gov.parse_rule({"rule": "oops not a mapping"}, name="x", node_id="n", text="")
    assert r.enforcement == "inform"


def test_rule_applies_empty_triggers_is_universal() -> None:
    r = gov.parse_rule(_fm({"modality": "MUST"}), name="x", node_id="n", text="")
    assert gov.rule_applies(r, glob_path=None, intent_tags=set(), tool_name=None, tool_arg=None)


def test_rule_applies_glob() -> None:
    r = gov.parse_rule(_fm({"applies_to": {"glob": ["**/*.py"]}}), name="x", node_id="n", text="")
    assert gov.rule_applies(r, glob_path="D:/repo/daemon/mnemo/store.py")
    assert not gov.rule_applies(r, glob_path="D:/repo/app/main.ts")
    # declared a glob trigger but no path in context -> does not apply
    assert not gov.rule_applies(r, glob_path=None)


def test_rule_applies_intent() -> None:
    r = gov.parse_rule(
        _fm({"applies_to": {"intent": ["refactor", "design"]}}), name="x", node_id="n", text=""
    )
    assert gov.rule_applies(r, intent_tags={"design"})
    assert not gov.rule_applies(r, intent_tags={"debug"})


def test_rule_applies_tool_with_arg_match() -> None:
    r = gov.parse_rule(
        _fm({"applies_to": {"tool": ["Bash"], "tool_arg_match": "git commit"}}),
        name="x",
        node_id="n",
        text="",
    )
    assert gov.rule_applies(r, tool_name="Bash", tool_arg="git commit -m 'x'")
    assert not gov.rule_applies(r, tool_name="Bash", tool_arg="ls -la")
    assert not gov.rule_applies(r, tool_name="Edit", tool_arg="git commit")


def test_rule_applies_tool_without_arg_match() -> None:
    r = gov.parse_rule(_fm({"applies_to": {"tool": ["Edit"]}}), name="x", node_id="n", text="")
    assert gov.rule_applies(r, tool_name="Edit", tool_arg="any/path.py")
    assert not gov.rule_applies(r, tool_name="Bash", tool_arg="echo hi")


def test_rule_applies_is_or_across_declared_dimensions() -> None:
    r = gov.parse_rule(
        _fm(
            {"applies_to": {"glob": ["**/*.py"], "tool": ["Bash"], "tool_arg_match": "git commit"}}
        ),
        name="x",
        node_id="n",
        text="",
    )
    assert gov.rule_applies(r, glob_path="a/b.py")  # glob dim
    assert gov.rule_applies(r, tool_name="Bash", tool_arg="git commit")  # tool dim
    assert not gov.rule_applies(r, glob_path="a/b.ts", tool_name="Bash", tool_arg="ls")


def test_modality_rank_orders_mustnot_first() -> None:
    must_not = gov.parse_rule(_fm({"modality": "MUST_NOT"}), name="a", node_id="1", text="")
    must = gov.parse_rule(_fm({"modality": "MUST"}), name="b", node_id="2", text="")
    should = gov.parse_rule(_fm({"modality": "SHOULD"}), name="c", node_id="3", text="")
    ranked = sorted([should, must, must_not], key=gov.modality_rank, reverse=True)
    assert [r.modality for r in ranked] == ["MUST_NOT", "MUST", "SHOULD"]


def test_rule_from_node_reads_frontmatter_json() -> None:
    from mnemo.store import Node

    node = Node.new(
        type="rule",
        name="no-emoji",
        description="No emojis in code or docs.",
        body="rationale...",
        source_path="/m/rule_no_emoji.md",
        source_kind="memory_dir",
        frontmatter_json=json.dumps({"rule": {"modality": "MUST_NOT", "enforcement": "warn"}}),
    )
    r = gov.rule_from_node(node)
    assert r is not None
    assert r.modality == "MUST_NOT"
    assert r.enforcement == "warn"
    assert r.text == "No emojis in code or docs."


def test_rule_from_node_returns_none_for_non_rule() -> None:
    from mnemo.store import Node

    node = Node.new(
        type="memory_project",
        name="x",
        body="b",
        source_path="/m/x.md",
        source_kind="memory_dir",
    )
    assert gov.rule_from_node(node) is None


def test_rule_md_file_ingests_as_a_rule_node(tmp_path) -> None:
    """End-to-end: a hand-written rule .md file reindexes into a `rule` node
    whose frontmatter `rule:` block round-trips and parses (G1 DoD)."""
    from mnemo import ingest
    from mnemo.store import Store

    memdir = tmp_path / "memory"
    memdir.mkdir()
    (memdir / "rule_no_emoji.md").write_text(
        "---\n"
        "name: no-emoji\n"
        "type: rule\n"
        "base: true\n"
        "description: No emojis in code, docs, or commit messages.\n"
        "rule:\n"
        "  id: rule.style.no-emoji\n"
        "  modality: MUST_NOT\n"
        "  enforcement: warn\n"
        "  applies_to:\n"
        "    glob: ['**/*.py', '**/*.md']\n"
        "---\n"
        "Rationale: keep the codebase emoji-free unless explicitly requested.\n",
        encoding="utf-8",
    )
    store = Store(tmp_path / "t.db")
    store.register_source(str(memdir), "memory_dir")
    ingest.reindex(store, embedder=None)

    rules = store.list_nodes(type="rule", limit=10)
    assert len(rules) == 1, "the .md file must ingest as exactly one rule node"
    parsed = gov.rule_from_node(rules[0])
    assert parsed is not None
    assert parsed.id == "rule.style.no-emoji"
    assert parsed.modality == "MUST_NOT"
    assert parsed.enforcement == "warn"
    assert gov.rule_applies(parsed, glob_path="x/y.py") is True
    assert gov.rule_applies(parsed, glob_path="x/y.ts") is False
    store.close()


# --- G2: active_rules (the prescriptive-surfacing fetch) -------------------


def _rule_node(store, *, name, block, project_key=None, base=False):
    import json as _json

    from mnemo.store import Node

    n = Node.new(
        type="rule",
        name=name,
        description=f"{name} text",
        body="b",
        source_path=f"/m/{name}.md",
        source_kind="memory_dir",
        base=base,
        frontmatter_json=_json.dumps({"rule": block}),
    )
    n.project_key = project_key
    store.upsert_node(n)
    return n


def _gov_store(tmp_path):
    from mnemo.store import Store

    store = Store(tmp_path / "g.db")
    _rule_node(store, name="base-no-emoji", base=True, block={"modality": "MUST_NOT"})
    _rule_node(
        store,
        name="p-refactor",
        project_key="P",
        block={"modality": "MUST", "applies_to": {"intent": ["refactor"]}},
    )
    _rule_node(
        store,
        name="py-glob",
        project_key="P",
        block={"modality": "SHOULD", "applies_to": {"glob": ["**/*.py"]}},
    )
    # a non-rule node must be ignored entirely
    from mnemo.store import Node

    store.upsert_node(
        Node.new(
            type="memory_project",
            name="noise",
            body="b",
            source_path="/m/noise.md",
            source_kind="memory_dir",
        )
    )
    return store


def test_active_rules_surfaces_universal_and_intent_at_prompt_time(tmp_path) -> None:
    store = _gov_store(tmp_path)
    rules = gov.active_rules(store, scope={"P"}, intent_tags={"refactor"})
    ids = {r.name for r in rules}
    assert "base-no-emoji" in ids, "a base universal rule always surfaces"
    assert "p-refactor" in ids, "an intent-matching in-scope rule surfaces"
    assert "py-glob" not in ids, "a glob-only rule does not surface with no file context"
    store.close()


def test_active_rules_orders_mandatory_first(tmp_path) -> None:
    store = _gov_store(tmp_path)
    rules = gov.active_rules(store, scope={"P"}, intent_tags={"refactor"})
    assert rules[0].modality == "MUST_NOT", "MUST_NOT binds hardest -> emitted first"
    store.close()


def test_active_rules_respects_project_scope(tmp_path) -> None:
    store = _gov_store(tmp_path)
    rules = gov.active_rules(store, scope={"OTHER"}, intent_tags={"refactor"})
    names = {r.name for r in rules}
    assert "base-no-emoji" in names, "base rule crosses project scope"
    assert "p-refactor" not in names, "an out-of-scope project rule is excluded"
    store.close()


def test_active_rules_with_file_context_matches_glob(tmp_path) -> None:
    store = _gov_store(tmp_path)
    rules = gov.active_rules(store, scope={"P"}, file_paths=["src/app.py"])
    assert "py-glob" in {r.name for r in rules}, "a glob rule surfaces once a file path is in context"
    store.close()


def test_active_rules_mandatory_only_filters_should(tmp_path) -> None:
    store = _gov_store(tmp_path)
    rules = gov.active_rules(store, scope={"P"}, file_paths=["src/app.py"], mandatory_only=True)
    assert all(r.is_mandatory for r in rules)
    assert "py-glob" not in {r.name for r in rules}, "SHOULD rule dropped under mandatory_only"
    store.close()


def test_active_rules_fails_open_on_store_error() -> None:
    class _BadStore:
        def list_nodes(self, **kw):
            raise RuntimeError("db gone")

    assert gov.active_rules(_BadStore(), scope=None) == []

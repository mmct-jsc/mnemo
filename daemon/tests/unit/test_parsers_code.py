"""v2.0 phase 4: Tier 1 code extractor tests.

The extractor walks a tree-sitter AST for a single source file and
produces:

- One ``code_module`` unit for the file itself.
- One ``code_function`` / ``code_class`` unit per top-level
  declaration.
- One ``code_method`` unit per class method, with a ``method_of``
  pointer to its containing class.

Edge intent (``defines``, ``method_of``, ``imports``) is carried
forward as structured fields on each :class:`CodeUnit`; the ingest
post-pass resolves them to real ``Edge`` rows after the nodes are
upserted (so within-file edges are deterministic and cross-file
imports can be best-effort matched against the rest of the repo).
"""

from __future__ import annotations

from pathlib import Path

# --- Python extractor ------------------------------------------------------


def test_extract_python_yields_module_node() -> None:
    from mnemo.parsers import code

    src = b"def login():\n    return True\n"
    units = code.extract(Path("/repo/auth.py"), src, language="python")
    # First unit is always the module node so downstream callers can
    # rely on units[0] being the file.
    assert units[0].type == "code_module"
    assert units[0].name == "auth.py"
    assert units[0].source_path == "/repo/auth.py"


def test_extract_python_yields_top_level_function() -> None:
    from mnemo.parsers import code

    src = b"def login():\n    return True\n"
    units = code.extract(Path("/repo/auth.py"), src, language="python")
    fns = [u for u in units if u.type == "code_function"]
    assert len(fns) == 1
    fn = fns[0]
    assert fn.name == "login"
    # Lines are 1-indexed in source_path so the IDE can jump directly.
    assert ":1-" in fn.source_path or fn.source_path.endswith(":1-2")


def test_extract_python_yields_top_level_class() -> None:
    from mnemo.parsers import code

    src = b"class Session:\n    pass\n"
    units = code.extract(Path("/repo/auth.py"), src, language="python")
    classes = [u for u in units if u.type == "code_class"]
    assert len(classes) == 1
    assert classes[0].name == "Session"


def test_extract_python_yields_methods_with_method_of_pointer() -> None:
    """Methods carry a `parent_source_path` pointing at their containing
    class so the ingest post-pass can wire the ``method_of`` edge."""
    from mnemo.parsers import code

    src = (
        b"class Session:\n    def renew(self):\n        pass\n    def expire(self):\n        pass\n"
    )
    units = code.extract(Path("/repo/auth.py"), src, language="python")
    methods = [u for u in units if u.type == "code_method"]
    assert len(methods) == 2
    names = {m.name for m in methods}
    assert names == {"renew", "expire"}
    # Both methods point at the Session class.
    classes = [u for u in units if u.type == "code_class"]
    cls = classes[0]
    for m in methods:
        assert m.parent_source_path == cls.source_path


def test_extract_python_extracts_docstring_as_description() -> None:
    from mnemo.parsers import code

    src = b'def login():\n    """Authenticate a user."""\n    return True\n'
    units = code.extract(Path("/repo/auth.py"), src, language="python")
    fn = next(u for u in units if u.type == "code_function")
    assert fn.description == "Authenticate a user."


def test_extract_python_imports_captured_as_module_imports() -> None:
    """``import os`` and ``from x.y import z`` both become entries on
    the module unit's ``imports`` list. Cross-file resolution is the
    ingest post-pass's job; the extractor only captures the target
    module name."""
    from mnemo.parsers import code

    src = b"import os\nfrom mnemo.store import Store\nimport json as j\n"
    units = code.extract(Path("/repo/main.py"), src, language="python")
    module = units[0]
    assert module.type == "code_module"
    assert "os" in module.imports
    assert "mnemo.store" in module.imports
    assert "json" in module.imports


def test_extract_python_module_defines_pointers() -> None:
    """The module unit carries `children_source_paths` so the ingest
    post-pass can wire ``defines`` edges (module -> top-level decls).
    Class methods are NOT included; they only appear as children of
    their class (via ``method_of``), keeping the ``defines`` semantic
    clean as "module's direct top-level declarations"."""
    from mnemo.parsers import code

    src = b"def helper():\n    pass\n\nclass Session:\n    def renew(self):\n        pass\n"
    units = code.extract(Path("/repo/auth.py"), src, language="python")
    module = units[0]
    fn = next(u for u in units if u.type == "code_function")
    cls = next(u for u in units if u.type == "code_class")
    method = next(u for u in units if u.type == "code_method")
    assert fn.source_path in module.children_source_paths
    assert cls.source_path in module.children_source_paths
    assert method.source_path not in module.children_source_paths


def test_extract_python_body_truncation_marker_on_long_function() -> None:
    """Function bodies > 60 lines get the truncated representation with
    a one-line trailing marker so the LLM hits don't blow the token
    budget on a long function."""
    from mnemo.parsers import code

    lines = ["def big():"] + [f"    x_{i} = {i}" for i in range(120)]
    src = ("\n".join(lines) + "\n").encode("utf-8")
    units = code.extract(Path("/repo/big.py"), src, language="python")
    fn = next(u for u in units if u.type == "code_function")
    # Body should not be the whole source -- the marker tells the LLM
    # that there's more.
    assert "more lines" in fn.body
    # Body should retain the head -- at least the def line + first few.
    assert fn.body.startswith("def big():")


def test_extract_python_short_function_body_is_verbatim() -> None:
    from mnemo.parsers import code

    src = b"def f():\n    return 1\n"
    units = code.extract(Path("/repo/x.py"), src, language="python")
    fn = next(u for u in units if u.type == "code_function")
    # Two-line function -- below the truncation threshold; body is the
    # whole source.
    assert "more lines" not in fn.body
    assert "return 1" in fn.body


def test_extract_python_source_path_uses_line_range_suffix() -> None:
    from mnemo.parsers import code

    src = b"def f():\n    pass\n\ndef g():\n    pass\n"
    units = code.extract(Path("/repo/x.py"), src, language="python")
    fns = [u for u in units if u.type == "code_function"]
    # Each function has a unique line range so they get distinct keys.
    assert len(fns) == 2
    assert fns[0].source_path != fns[1].source_path
    assert ":" in fns[0].source_path


def test_extract_python_skips_decorated_helpers_only_for_method_of() -> None:
    """Methods decorated with @staticmethod / @classmethod / @property
    still count as methods of the containing class."""
    from mnemo.parsers import code

    src = (
        b"class C:\n"
        b"    @staticmethod\n"
        b"    def s():\n"
        b"        pass\n"
        b"    @property\n"
        b"    def p(self):\n"
        b"        return 1\n"
    )
    units = code.extract(Path("/repo/x.py"), src, language="python")
    methods = [u for u in units if u.type == "code_method"]
    names = {m.name for m in methods}
    assert names == {"s", "p"}


# --- Fallback for non-Python languages ------------------------------------
#
# Phase 4 ships a Python extractor in full and a "code_module-only"
# fallback for the other bundled languages (JS / TS / TSX / Go / JSON /
# YAML / Markdown). The fallback lets the daemon ingest a polyglot
# repo without crashing -- later phases add per-language extractors
# that emit functions / classes / etc. for those grammars too.


def test_extract_json_yields_only_a_module_node() -> None:
    from mnemo.parsers import code

    src = b'{"hello": "world"}\n'
    units = code.extract(Path("/repo/data.json"), src, language="json")
    assert len(units) == 1
    assert units[0].type == "code_module"
    assert units[0].name == "data.json"


def test_extract_markdown_yields_only_a_module_node() -> None:
    from mnemo.parsers import code

    src = b"# Title\n\nbody\n"
    units = code.extract(Path("/repo/notes.md"), src, language="markdown")
    assert len(units) == 1
    assert units[0].type == "code_module"


def test_extract_javascript_does_not_crash_minimum_a_module_node() -> None:
    """JS gets full extractor support in a follow-on phase. For phase 4,
    the contract is: registered language + valid source -> at least the
    module node, no crash."""
    from mnemo.parsers import code

    src = b"export function hi() { return 1; }\n"
    units = code.extract(Path("/repo/main.js"), src, language="javascript")
    assert any(u.type == "code_module" for u in units)


def test_extract_unknown_language_falls_back_to_module_only() -> None:
    """A registered language that the extractor doesn't yet know how to
    walk should still produce the module node so the file's existence
    is queryable."""
    from mnemo.parsers import code

    src = b"// some unknown source\n"
    units = code.extract(Path("/repo/x.unknown"), src, language="markdown")
    assert any(u.type == "code_module" for u in units)


# --- CodeUnit dataclass shape ---------------------------------------------


def test_code_unit_has_expected_fields() -> None:
    from mnemo.parsers import code

    u = code.CodeUnit(
        type="code_module",
        name="x.py",
        body="",
        source_path="/repo/x.py",
        description=None,
        hash="0",
        imports=[],
        children_source_paths=[],
        parent_source_path=None,
    )
    for attr in (
        "type",
        "name",
        "body",
        "source_path",
        "description",
        "hash",
        "imports",
        "children_source_paths",
        "parent_source_path",
    ):
        assert hasattr(u, attr), attr

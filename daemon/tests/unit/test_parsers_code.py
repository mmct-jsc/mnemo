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


def test_extract_python_body_truncates_long_function_without_marker() -> None:
    """Function bodies > 60 lines are truncated to the head.

    v2.1 dropped the legacy ``... (N more lines)`` marker the v2.0
    design originally appended: the marker leaked into embeddings
    and (when chat lands in v3) would have surfaced inside LLM
    output. The stored body is now clean source code only; full
    content is reachable via ``GET /v1/nodes/<id>/full_source``.
    """
    from mnemo.parsers import code

    lines = ["def big():"] + [f"    x_{i} = {i}" for i in range(120)]
    src = ("\n".join(lines) + "\n").encode("utf-8")
    units = code.extract(Path("/repo/big.py"), src, language="python")
    fn = next(u for u in units if u.type == "code_function")
    # Body retains the head but does NOT carry the truncation marker.
    assert fn.body.startswith("def big():")
    assert "more lines" not in fn.body
    assert "..." not in fn.body
    # Truncation actually happened: body is < the full source length.
    assert fn.body.count("\n") < 121


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
        "call_sites",
    ):
        assert hasattr(u, attr), attr


# --- v2.0 phase 5: Python call-site extraction ---------------------------


def test_call_site_dataclass_shape() -> None:
    from mnemo.parsers import code

    cs = code.CallSite(callee_name="f", receiver=None, line=3)
    for attr in ("callee_name", "receiver", "line"):
        assert hasattr(cs, attr)


def test_extract_python_captures_free_function_call_inside_function() -> None:
    """A function that calls another function records the call site on
    the caller's CodeUnit."""
    from mnemo.parsers import code

    src = b"def a():\n    return b()\n\ndef b():\n    return 1\n"
    units = code.extract(Path("/repo/x.py"), src, language="python")
    a = next(u for u in units if u.type == "code_function" and u.name == "a")
    names = {cs.callee_name for cs in a.call_sites}
    assert "b" in names
    # Free call (no receiver).
    free = next(cs for cs in a.call_sites if cs.callee_name == "b")
    assert free.receiver is None


def test_extract_python_captures_method_call_on_self() -> None:
    """``self.method()`` records the call site with receiver='self' so
    the resolver knows to look up the parent class's methods."""
    from mnemo.parsers import code

    src = (
        b"class C:\n"
        b"    def helper(self):\n"
        b"        return 1\n"
        b"    def caller(self):\n"
        b"        return self.helper()\n"
    )
    units = code.extract(Path("/repo/x.py"), src, language="python")
    caller = next(u for u in units if u.type == "code_method" and u.name == "caller")
    helper_call = next(cs for cs in caller.call_sites if cs.callee_name == "helper")
    assert helper_call.receiver == "self"


def test_extract_python_captures_module_qualified_call() -> None:
    """``helper.f()`` records receiver='helper' so the resolver can
    cross-walk through the imports edge."""
    from mnemo.parsers import code

    src = b"import helper\n\ndef use():\n    return helper.f()\n"
    units = code.extract(Path("/repo/x.py"), src, language="python")
    use = next(u for u in units if u.type == "code_function" and u.name == "use")
    call = next(cs for cs in use.call_sites if cs.callee_name == "f")
    assert call.receiver == "helper"


def test_extract_python_captures_constructor_call_as_free_call() -> None:
    """``Session()`` is just a free call from the AST's perspective --
    the resolver later treats PascalCase / class names specially. The
    extractor captures it the same way as any other free call."""
    from mnemo.parsers import code

    src = b"class Session: pass\n\ndef make():\n    return Session()\n"
    units = code.extract(Path("/repo/x.py"), src, language="python")
    make = next(u for u in units if u.type == "code_function" and u.name == "make")
    call = next(cs for cs in make.call_sites if cs.callee_name == "Session")
    assert call.receiver is None


def test_extract_python_module_level_calls_are_not_in_any_function() -> None:
    """Calls at module scope (no enclosing def) are NOT attached to any
    function/method unit. They could in principle be modeled as edges
    on the module, but Tier 2 only emits caller-function edges to keep
    the graph clean. Anyone wanting "module imports" semantics has
    the ``imports`` edge."""
    from mnemo.parsers import code

    src = b"def a(): pass\n\na()\n"
    units = code.extract(Path("/repo/x.py"), src, language="python")
    a = next(u for u in units if u.type == "code_function")
    # No call sites recorded on `a` (the module-level call is outside `a`).
    assert all(cs.callee_name != "a" for cs in a.call_sites)


def test_extract_python_nested_call_sites_attribute_to_enclosing_function() -> None:
    """A call deep inside nested control flow (``if``, ``for``, etc.)
    still attaches to the enclosing top-level function or method, not
    to some intermediate scope."""
    from mnemo.parsers import code

    src = (
        b"def caller():\n"
        b"    if True:\n"
        b"        for x in range(10):\n"
        b"            inner()\n"
        b"\n"
        b"def inner(): pass\n"
    )
    units = code.extract(Path("/repo/x.py"), src, language="python")
    caller = next(u for u in units if u.type == "code_function" and u.name == "caller")
    assert any(cs.callee_name == "inner" for cs in caller.call_sites)

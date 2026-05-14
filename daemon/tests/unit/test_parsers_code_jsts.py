"""v2.5.0 Tier 1 + Tier 2 for JavaScript / TypeScript.

The v2.0 design (§ 4, Tier 1 + Tier 2 rows) calls for a structural
extractor for all six bundled languages. Phase 4 shipped Python; JS
and TS got their tree-sitter parsers wired but no ``_extract_*``
function -- so their files only get a ``code_module`` node, no
declarations, no imports, no call sites for the Tier 2 resolver.

v2.5.0 closes that gap for JavaScript + TypeScript (they share most
of the tree-sitter AST shape, so one shared extractor handles both).
Go follows in v2.5.1.

These tests lock the SURFACE of the new extractor:

- ``function foo() {}`` -> ``code_function``
- ``class Foo {}`` -> ``code_class`` + per-method ``code_method``
- ``const f = () => {}`` -> ``code_function`` (modern arrow style)
- ``import x from 'mod'``, ``import { x } from 'mod'`` -> imports
- ``a()`` / ``a.b()`` inside a function -> call_sites
- Plus the same for TypeScript (parameters can carry type
  annotations; the extractor must not break on them).
"""

from __future__ import annotations

from pathlib import Path

# --- JavaScript Tier 1: declarations ------------------------------------


def test_javascript_function_declaration_emits_code_function() -> None:
    """``function foo() { ... }`` at module top-level -> one
    ``code_function`` unit with the function name + body."""
    from mnemo.parsers import code

    src = b"function login(token) {\n  return validate(token);\n}\n"
    units = code.extract(Path("/repo/auth.js"), src, language="javascript")
    fns = [u for u in units if u.type == "code_function"]
    assert len(fns) == 1
    assert fns[0].name == "login"


def test_javascript_class_declaration_emits_class_and_methods() -> None:
    """``class Foo { bar() {} baz() {} }`` -> one ``code_class``
    + two ``code_method`` units. Each method's parent_source_path
    points at the class so the post-pass can wire ``method_of``."""
    from mnemo.parsers import code

    src = (
        b"class AuthService {\n"
        b"  login(token) { return validate(token); }\n"
        b"  logout() { return null; }\n"
        b"}\n"
    )
    units = code.extract(Path("/repo/auth.js"), src, language="javascript")
    classes = [u for u in units if u.type == "code_class"]
    methods = [u for u in units if u.type == "code_method"]
    assert len(classes) == 1
    assert classes[0].name == "AuthService"
    assert {m.name for m in methods} == {"login", "logout"}
    for m in methods:
        assert m.parent_source_path == classes[0].source_path


def test_javascript_arrow_function_const_emits_code_function() -> None:
    """``const foo = (x) => {...}`` at module top-level -> one
    ``code_function`` named ``foo``. This is the canonical modern
    JS module-export shape."""
    from mnemo.parsers import code

    src = b"const login = (token) => {\n  return validate(token);\n};\n"
    units = code.extract(Path("/repo/auth.js"), src, language="javascript")
    fns = [u for u in units if u.type == "code_function"]
    assert len(fns) == 1
    assert fns[0].name == "login"


# --- JavaScript Tier 1: imports ----------------------------------------


def test_javascript_default_import_recorded() -> None:
    """``import x from 'mod'`` adds ``mod`` to the module's imports."""
    from mnemo.parsers import code

    src = b"import express from 'express';\nfunction f() {}\n"
    units = code.extract(Path("/repo/api.js"), src, language="javascript")
    module = next(u for u in units if u.type == "code_module")
    assert "express" in module.imports


def test_javascript_named_import_recorded() -> None:
    """``import { x, y } from 'mod'`` adds ``mod`` (NOT ``x`` / ``y``)
    to the imports list -- the importable target is the module, not
    the symbols pulled from it."""
    from mnemo.parsers import code

    src = b"import { Router, Request } from 'express';\nfunction f() {}\n"
    units = code.extract(Path("/repo/api.js"), src, language="javascript")
    module = next(u for u in units if u.type == "code_module")
    assert "express" in module.imports


# --- JavaScript Tier 2: call sites -------------------------------------


def test_javascript_free_call_recorded_as_call_site() -> None:
    """A bare ``foo()`` call inside a function -> CallSite with
    callee_name='foo', receiver=None. The Tier 2 resolver will look
    this up against same-module / cross-file declarations later."""
    from mnemo.parsers import code

    src = b"function bar() { return 1; }\nfunction foo() {\n  return bar();\n}\n"
    units = code.extract(Path("/repo/x.js"), src, language="javascript")
    foo = next(u for u in units if u.type == "code_function" and u.name == "foo")
    assert any(cs.callee_name == "bar" and cs.receiver is None for cs in foo.call_sites)


def test_javascript_member_call_recorded_with_receiver() -> None:
    """``obj.method()`` -> CallSite with callee_name='method',
    receiver='obj'. Matches the same Tier 2 contract as Python."""
    from mnemo.parsers import code

    src = b"function caller() {\n  return logger.info('hello');\n}\n"
    units = code.extract(Path("/repo/x.js"), src, language="javascript")
    caller = next(u for u in units if u.type == "code_function" and u.name == "caller")
    site = next(cs for cs in caller.call_sites if cs.callee_name == "info")
    assert site.receiver == "logger"


# --- TypeScript: same shape, with type annotations ---------------------


def test_typescript_function_with_type_annotations_emits_function() -> None:
    """The TS extractor must not choke on parameter / return type
    annotations -- they're TS-specific syntax but the underlying
    ``function_declaration`` node is the same shape as JS."""
    from mnemo.parsers import code

    src = b"function login(token: string): boolean {\n  return token.length > 0;\n}\n"
    units = code.extract(Path("/repo/auth.ts"), src, language="typescript")
    fns = [u for u in units if u.type == "code_function"]
    assert len(fns) == 1
    assert fns[0].name == "login"


def test_typescript_class_with_typed_method_emits_class_and_method() -> None:
    """A TS class with a typed method produces the same
    code_class + code_method shape JS does."""
    from mnemo.parsers import code

    src = b"class AuthService {\n  login(token: string): boolean { return true; }\n}\n"
    units = code.extract(Path("/repo/auth.ts"), src, language="typescript")
    classes = [u for u in units if u.type == "code_class"]
    methods = [u for u in units if u.type == "code_method"]
    assert len(classes) == 1
    assert classes[0].name == "AuthService"
    assert {m.name for m in methods} == {"login"}


def test_typescript_imports_recorded() -> None:
    """TS ``import`` is the same shape as JS."""
    from mnemo.parsers import code

    src = b"import { Router } from 'express';\nfunction f(): void {}\n"
    units = code.extract(Path("/repo/api.ts"), src, language="typescript")
    module = next(u for u in units if u.type == "code_module")
    assert "express" in module.imports


# --- Registry ------------------------------------------------------------


def test_jsts_extractor_registered_for_javascript_and_typescript() -> None:
    """The ``_LANGUAGE_EXTRACTORS`` registry in
    ``daemon/mnemo/parsers/code.py`` must register a non-None
    extractor for ``javascript`` AND ``typescript``. Without this,
    JS/TS files only get a module unit (the v2.4.x bug this PR
    closes)."""
    from mnemo.parsers import code

    assert code._LANGUAGE_EXTRACTORS.get("javascript") is not None
    assert code._LANGUAGE_EXTRACTORS.get("typescript") is not None

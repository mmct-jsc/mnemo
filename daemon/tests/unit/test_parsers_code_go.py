"""v2.5.1 Tier 1 + Tier 2 for Go.

v2.5.0 closed the JS / TS gap; v2.5.1 closes the Go gap and
completes the v2-deferred sweep. The shared
:func:`mnemo.parsers.scope.resolve_calls` Tier 2 resolver was
designed to be language-agnostic -- so once a Go extractor
populates call_sites, the calls graph fills in for free.

Go-specific shapes the extractor handles:

- ``func foo() {}``                       -> ``code_function``
- ``func (r *Receiver) m() {}``           -> ``code_method`` with
                                              parent = ``Receiver``
- ``type Foo struct { ... }``             -> ``code_class``
  (Go has no classes; structs + their receiver-methods are the
  natural analogue, and using ``code_class`` keeps the schema
  consistent across languages)
- ``type Foo interface { ... }``          -> ``code_class``
- ``import "fmt"`` / ``import ( ... )``   -> imports
- ``foo()`` / ``pkg.Func()``              -> call sites
"""

from __future__ import annotations

from pathlib import Path

# --- Tier 1: declarations -----------------------------------------------


def test_go_function_declaration_emits_code_function() -> None:
    """``func foo() { ... }`` at package level -> ``code_function``."""
    from mnemo.parsers import code

    src = b'package auth\n\nfunc login(token string) bool {\n\treturn token != ""\n}\n'
    units = code.extract(Path("/repo/auth.go"), src, language="go")
    fns = [u for u in units if u.type == "code_function"]
    assert len(fns) == 1
    assert fns[0].name == "login"


def test_go_struct_type_emits_code_class() -> None:
    """``type Foo struct { ... }`` -> ``code_class``. Go has no
    classes per se, but structs + their receiver-methods are the
    natural analogue for the schema's class-shape concept."""
    from mnemo.parsers import code

    src = b"package auth\n\ntype AuthService struct {\n\ttoken string\n}\n"
    units = code.extract(Path("/repo/auth.go"), src, language="go")
    classes = [u for u in units if u.type == "code_class"]
    assert len(classes) == 1
    assert classes[0].name == "AuthService"


def test_go_method_declaration_emits_code_method_with_receiver_parent() -> None:
    """``func (r *AuthService) login() {}`` -> ``code_method`` whose
    parent_source_path points at the ``AuthService`` struct's
    code_class node (defined elsewhere in the file)."""
    from mnemo.parsers import code

    src = (
        b"package auth\n\n"
        b"type AuthService struct{}\n\n"
        b"func (s *AuthService) Login(token string) bool {\n"
        b'\treturn token != ""\n'
        b"}\n"
    )
    units = code.extract(Path("/repo/auth.go"), src, language="go")
    methods = [u for u in units if u.type == "code_method"]
    classes = [u for u in units if u.type == "code_class"]
    assert len(methods) == 1
    assert methods[0].name == "Login"
    # The method's parent_source_path must point at the struct
    # that owns it, so the post-pass can wire ``method_of``.
    cls = next(c for c in classes if c.name == "AuthService")
    assert methods[0].parent_source_path == cls.source_path


def test_go_method_with_value_receiver_emits_code_method() -> None:
    """Value receivers ``func (s AuthService) m()`` are the same
    shape as pointer receivers for our purposes -- both produce a
    ``code_method`` parented at the receiver type."""
    from mnemo.parsers import code

    src = (
        b"package auth\n\n"
        b"type AuthService struct{}\n\n"
        b"func (s AuthService) Logout() bool { return true }\n"
    )
    units = code.extract(Path("/repo/auth.go"), src, language="go")
    methods = [u for u in units if u.type == "code_method"]
    assert len(methods) == 1
    assert methods[0].name == "Logout"


def test_go_interface_type_emits_code_class() -> None:
    """``type Foo interface { ... }`` -> ``code_class``. Interfaces
    are first-class type declarations in Go and conceptually
    similar to a class in our schema (they have a name + members)."""
    from mnemo.parsers import code

    src = b"package auth\n\ntype Validator interface {\n\tValidate(token string) bool\n}\n"
    units = code.extract(Path("/repo/auth.go"), src, language="go")
    classes = [u for u in units if u.type == "code_class"]
    assert any(c.name == "Validator" for c in classes)


# --- Tier 1: imports ----------------------------------------------------


def test_go_simple_import_recorded() -> None:
    """``import "fmt"`` adds ``fmt`` to module.imports."""
    from mnemo.parsers import code

    src = b'package main\n\nimport "fmt"\n\nfunc main() { fmt.Println("hi") }\n'
    units = code.extract(Path("/repo/main.go"), src, language="go")
    module = next(u for u in units if u.type == "code_module")
    assert "fmt" in module.imports


def test_go_grouped_import_recorded() -> None:
    """``import ( "fmt"; "os" )`` adds both targets to imports.
    Aliased imports (``alias "pkg/path"``) record the path, not
    the alias (the importable target IS the path)."""
    from mnemo.parsers import code

    src = (
        b"package main\n\n"
        b"import (\n"
        b'\t"fmt"\n'
        b'\t"os"\n'
        b'\tjson "encoding/json"\n'
        b")\n\n"
        b"func main() { fmt.Println(os.Args, json.Marshal) }\n"
    )
    units = code.extract(Path("/repo/main.go"), src, language="go")
    module = next(u for u in units if u.type == "code_module")
    assert "fmt" in module.imports
    assert "os" in module.imports
    assert "encoding/json" in module.imports


# --- Tier 2: call sites -------------------------------------------------


def test_go_free_call_recorded_as_call_site() -> None:
    """A bare ``foo()`` call inside a function -> CallSite with
    callee_name='foo', receiver=None."""
    from mnemo.parsers import code

    src = (
        b"package auth\n\n"
        b"func helper() bool { return true }\n\n"
        b"func login() bool {\n"
        b"\treturn helper()\n"
        b"}\n"
    )
    units = code.extract(Path("/repo/auth.go"), src, language="go")
    login = next(u for u in units if u.type == "code_function" and u.name == "login")
    assert any(cs.callee_name == "helper" and cs.receiver is None for cs in login.call_sites)


def test_go_package_call_recorded_with_receiver() -> None:
    """``fmt.Println(...)`` -> CallSite(callee='Println',
    receiver='fmt'). The Tier 2 resolver treats ``fmt`` as a
    cross-file lookup against the ``imports`` edge for the module
    -- same shape as Python's `import` resolution."""
    from mnemo.parsers import code

    src = b'package main\n\nimport "fmt"\n\nfunc main() {\n\tfmt.Println("hi")\n}\n'
    units = code.extract(Path("/repo/main.go"), src, language="go")
    main_fn = next(u for u in units if u.type == "code_function" and u.name == "main")
    site = next(cs for cs in main_fn.call_sites if cs.callee_name == "Println")
    assert site.receiver == "fmt"


# --- Registry -----------------------------------------------------------


def test_go_extractor_registered() -> None:
    """``_LANGUAGE_EXTRACTORS['go']`` must be wired in
    ``daemon/mnemo/parsers/code.py``. Without this, Go files only
    get a module unit (the v2.5.0 bug for non-JS/TS/Python that
    this PR closes)."""
    from mnemo.parsers import code

    assert code._LANGUAGE_EXTRACTORS.get("go") is not None

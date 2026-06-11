"""v5.28.0 step 1: stable code-node identity keys (lesson #129).

Code declaration ``source_path`` moves from ``<file>:<start>-<end>``
(which re-keys on every line shift -> churn + lost id history) to a
line-stable ``<file>::<qualified_name>``. The line range is preserved
as ``line_start`` / ``line_end`` metadata for the IDE-jump + git-overlap
consumers. Module keys (bare file path) and endpoint keys
(``endpoint:METHOD:path``) are already stable and must not change.
"""

from __future__ import annotations

from pathlib import Path

from mnemo.parsers import code


def _fn(units: list[code.CodeUnit], name: str) -> code.CodeUnit:
    return next(u for u in units if u.type == "code_function" and u.name == name)


def test_function_key_is_stable_qualified_name() -> None:
    src = b"def login():\n    return True\n"
    units = code.extract(Path("/repo/auth.py"), src, language="python")
    fn = _fn(units, "login")
    assert fn.source_path == "/repo/auth.py::login"


def test_module_key_stays_bare_file_path() -> None:
    src = b"def login():\n    return True\n"
    units = code.extract(Path("/repo/auth.py"), src, language="python")
    assert units[0].type == "code_module"
    assert units[0].source_path == "/repo/auth.py"


def test_key_is_stable_across_line_shift() -> None:
    """The SAME function keeps its key when code above it shifts its
    line number -- the entire point of v5.28.0."""
    top = b"def login():\n    return True\n"
    shifted = (b"\n" * 40) + b"def login():\n    return True\n"
    u_top = _fn(code.extract(Path("/repo/auth.py"), top, language="python"), "login")
    u_shifted = _fn(code.extract(Path("/repo/auth.py"), shifted, language="python"), "login")
    assert u_top.source_path == u_shifted.source_path == "/repo/auth.py::login"
    # ...but the line-range metadata tracks the real (shifted) position.
    assert u_top.line_start == 1
    assert u_shifted.line_start == 41


def test_method_key_includes_class_qualifier() -> None:
    src = b"class Session:\n    def renew(self):\n        pass\n"
    units = code.extract(Path("/repo/auth.py"), src, language="python")
    method = next(u for u in units if u.type == "code_method")
    cls = next(u for u in units if u.type == "code_class")
    assert cls.source_path == "/repo/auth.py::Session"
    assert method.source_path == "/repo/auth.py::Session.renew"
    # The method_of cross-ref still points at the class's (new) key.
    assert method.parent_source_path == cls.source_path


def test_same_name_top_level_functions_get_distinct_keys() -> None:
    """Two top-level ``def f`` (redefinition / conditional define) must
    not collide -- a document-order ordinal disambiguates the second,
    preserving the guarantee the line range used to give."""
    src = b"def f():\n    return 1\n\ndef f():\n    return 2\n"
    units = code.extract(Path("/repo/x.py"), src, language="python")
    fns = [u for u in units if u.type == "code_function"]
    assert len(fns) == 2
    keys = {u.source_path for u in fns}
    assert keys == {"/repo/x.py::f", "/repo/x.py::f#2"}


def test_module_children_use_stable_keys() -> None:
    src = b"def helper():\n    pass\n\nclass Session:\n    def renew(self):\n        pass\n"
    units = code.extract(Path("/repo/auth.py"), src, language="python")
    module = units[0]
    assert "/repo/auth.py::helper" in module.children_source_paths
    assert "/repo/auth.py::Session" in module.children_source_paths
    # Methods are never module children (method_of, not defines).
    assert "/repo/auth.py::Session.renew" not in module.children_source_paths


def test_line_range_metadata_populated() -> None:
    src = b"def f():\n    return 1\n"
    units = code.extract(Path("/repo/x.py"), src, language="python")
    fn = _fn(units, "f")
    assert fn.line_start == 1
    assert fn.line_end == 2


# --- code_file_and_range: the one helper every stored-node consumer uses ---


def test_code_file_and_range_reads_frontmatter_for_stable_key() -> None:
    import json

    fm = json.dumps({"code_unit": {"line_start": 10, "line_end": 20}})
    file_path, rng = code.code_file_and_range("/repo/auth.py::login", fm)
    assert file_path == "/repo/auth.py"
    assert rng == (10, 20)


def test_code_file_and_range_stable_key_without_frontmatter_has_no_range() -> None:
    file_path, rng = code.code_file_and_range("/repo/auth.py::login", None)
    assert file_path == "/repo/auth.py"
    assert rng is None


def test_code_file_and_range_falls_back_to_legacy_suffix() -> None:
    # A pre-migration node still carries the line range in the key.
    file_path, rng = code.code_file_and_range("/repo/auth.py:1-5", None)
    assert file_path == "/repo/auth.py"
    assert rng == (1, 5)


def test_code_file_and_range_module_has_no_range() -> None:
    file_path, rng = code.code_file_and_range("/repo/auth.py", None)
    assert file_path == "/repo/auth.py"
    assert rng is None


def test_code_file_and_range_windows_drive_letter_is_safe() -> None:
    # The single ``:`` in a drive letter must not be mistaken for the
    # ``::`` identity separator.
    import json

    fm = json.dumps({"code_unit": {"line_start": 3, "line_end": 4}})
    file_path, rng = code.code_file_and_range("C:/repo/auth.py::login", fm)
    assert file_path == "C:/repo/auth.py"
    assert rng == (3, 4)

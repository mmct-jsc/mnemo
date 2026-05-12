"""v2.0 phase 3: tree-sitter grammar loader tests.

The loader is a thin wrapper around the per-language tree-sitter
Python packages. It hides three sources of churn from callers:

- The capsule -> ``tree_sitter.Language`` conversion (which changed
  shape in tree-sitter 0.22 / 0.23).
- The fact that some packages expose ``language()`` while
  ``tree-sitter-typescript`` exposes ``language_typescript()`` and
  ``language_tsx()`` instead.
- Whether a grammar is bundled (importable from a wheel that ships
  with the daemon) or has to be lazy-installed by the user.

Phase 3 is grammar infrastructure only. The Tier 1 ingester that
calls ``get_parser`` lands in phase 4.
"""

from __future__ import annotations

import pytest

# --- Bundled registry shape -----------------------------------------------


def test_bundled_languages_includes_core_v2_set() -> None:
    """The v2.0 launch bundle must cover the languages the ingester
    relies on in early phases."""
    from mnemo.parsers import tree_sitter as ts

    expected = {"python", "typescript", "tsx", "javascript", "go", "json", "yaml", "markdown"}
    assert expected.issubset(set(ts.BUNDLED_LANGUAGES.keys()))


def test_is_bundled_returns_true_for_python() -> None:
    from mnemo.parsers import tree_sitter as ts

    assert ts.is_bundled("python") is True


def test_is_bundled_returns_false_for_rust_at_launch() -> None:
    """Rust is in the Tier 1 grammar list (16 grammars) but lazy-installed,
    not bundled in the wheel."""
    from mnemo.parsers import tree_sitter as ts

    assert ts.is_bundled("rust") is False


def test_is_bundled_returns_false_for_unknown_language() -> None:
    from mnemo.parsers import tree_sitter as ts

    assert ts.is_bundled("klingon") is False


# --- Extension dispatch ---------------------------------------------------


def test_language_for_extension_python() -> None:
    from mnemo.parsers import tree_sitter as ts

    assert ts.language_for_extension(".py") == "python"
    assert ts.language_for_extension(".pyi") == "python"


def test_language_for_extension_typescript_distinguishes_tsx() -> None:
    """The TS grammar has two top-level languages; ``.tsx`` files need
    the TSX dialect specifically because they contain JSX."""
    from mnemo.parsers import tree_sitter as ts

    assert ts.language_for_extension(".ts") == "typescript"
    assert ts.language_for_extension(".tsx") == "tsx"


def test_language_for_extension_javascript_family() -> None:
    from mnemo.parsers import tree_sitter as ts

    assert ts.language_for_extension(".js") == "javascript"
    assert ts.language_for_extension(".jsx") == "javascript"
    assert ts.language_for_extension(".mjs") == "javascript"


def test_language_for_extension_returns_none_for_unknown() -> None:
    from mnemo.parsers import tree_sitter as ts

    assert ts.language_for_extension(".xyz") is None


def test_language_for_extension_is_case_insensitive() -> None:
    from mnemo.parsers import tree_sitter as ts

    assert ts.language_for_extension(".PY") == "python"
    assert ts.language_for_extension(".JSON") == "json"


# --- get_parser -----------------------------------------------------------


def test_get_parser_python_parses_a_simple_function() -> None:
    """End-to-end sanity: load Python, parse trivial source, walk the AST."""
    from mnemo.parsers import tree_sitter as ts

    parser = ts.get_parser("python")
    tree = parser.parse(b"def hello():\n    return 1\n")
    # Root is `module`; first top-level child is the function_definition.
    assert tree.root_node.type == "module"
    fn = tree.root_node.children[0]
    assert fn.type == "function_definition"


def test_get_parser_typescript_via_language_typescript_function() -> None:
    """``tree-sitter-typescript`` does NOT expose plain ``language()``;
    the loader must call ``language_typescript()`` instead."""
    from mnemo.parsers import tree_sitter as ts

    parser = ts.get_parser("typescript")
    tree = parser.parse(b"function f(): number { return 1; }")
    assert tree.root_node.type == "program"


def test_get_parser_tsx_via_language_tsx_function() -> None:
    """TSX dispatch picks ``language_tsx`` from the same wheel."""
    from mnemo.parsers import tree_sitter as ts

    parser = ts.get_parser("tsx")
    tree = parser.parse(b"const x = <div>hi</div>;\n")
    assert tree.root_node.type == "program"


def test_get_parser_markdown_uses_block_language() -> None:
    """``tree-sitter-markdown`` exposes ``language()`` (block) and
    ``inline_language()`` (inline). The loader picks the block grammar
    by default -- inline is a phase-4+ concern if we ever need it."""
    from mnemo.parsers import tree_sitter as ts

    parser = ts.get_parser("markdown")
    tree = parser.parse(b"# Title\n\nbody\n")
    # The root node type for the block grammar is `document`.
    assert tree.root_node.type == "document"


def test_get_parser_unknown_language_raises_grammar_not_available() -> None:
    from mnemo.parsers import tree_sitter as ts

    with pytest.raises(ts.GrammarNotAvailableError):
        ts.get_parser("klingon")


def test_get_parser_lazy_grammar_raises_with_install_hint() -> None:
    """Rust is registered as lazy. Until the user installs it, the loader
    must fail with an actionable message naming the pip package."""
    from mnemo.parsers import tree_sitter as ts

    with pytest.raises(ts.GrammarNotAvailableError) as excinfo:
        ts.get_parser("rust")
    # The hint should name the pip-installable package so the user can
    # copy-paste it directly.
    assert "tree-sitter-rust" in str(excinfo.value)


def test_get_parser_caches_within_a_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated calls return the same Parser instance. Cheap to build,
    but caching means downstream code can compare ``is`` for identity."""
    from mnemo.parsers import tree_sitter as ts

    a = ts.get_parser("python")
    b = ts.get_parser("python")
    assert a is b


# --- Lazy registry shape --------------------------------------------------


def test_lazy_languages_includes_tier1_set() -> None:
    """The 10 Tier 1 grammars beyond the bundled core must appear in
    LAZY_LANGUAGES so the loader can name the right pip package on miss."""
    from mnemo.parsers import tree_sitter as ts

    # Per design: Tier 1 covers 16 grammars, 6 bundled + 10 lazy.
    # Spot-check a few that downstream ingesters will hit.
    for name in ("rust", "java", "c", "cpp", "bash"):
        assert name in ts.LAZY_LANGUAGES, name

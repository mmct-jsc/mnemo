"""v2.0 phase 3: tree-sitter grammar loader.

A thin wrapper around the per-language tree-sitter Python packages.
Hides three sources of churn from callers:

- The capsule -> :class:`tree_sitter.Language` conversion (which
  changed shape across the 0.21 / 0.22 / 0.23 binding releases).
- The fact that some language wheels expose ``language()`` while
  ``tree-sitter-typescript`` exposes ``language_typescript()`` and
  ``language_tsx()`` instead, and ``tree-sitter-markdown`` exposes
  ``language()`` (block) and ``inline_language()`` (inline).
- Whether a grammar is bundled (shipped as a dependency, importable
  on a fresh install) or has to be lazy-installed by the user
  (``pip install tree-sitter-<lang>``).

The loader is the only place in the codebase that knows about
tree-sitter as a library. Phase 4 (Tier 1 ingestion) calls
:func:`get_parser` and walks the resulting AST; nothing else.

Bundled set (v2.0 launch):

- python, javascript, typescript, tsx, go, json, yaml, markdown

Lazy set (Tier 1 languages whose wheels aren't shipped):

- rust, java, c, cpp, ruby, php, c_sharp, kotlin, swift, bash

The lazy entries name the pip package the user needs to install;
the loader surfaces that as the install hint in
:class:`GrammarNotAvailableError`.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover -- import-time only
    import tree_sitter


class GrammarNotAvailableError(RuntimeError):
    """Raised when :func:`get_parser` can't satisfy a request.

    Three failure modes:

    - Unknown language name (typo or unsupported language at this
      version).
    - Lazy grammar that hasn't been ``pip install``-ed yet.
    - Bundled grammar that failed to import (corrupt wheel; a clear
      "reinstall mnemo" hint is the right call).

    The exception message always carries an actionable hint --
    callers MAY surface it directly to end-users.
    """


# (module_name, function_name) -- the function returns the
# tree-sitter capsule we wrap with ``tree_sitter.Language``.
BUNDLED_LANGUAGES: dict[str, tuple[str, str]] = {
    "python": ("tree_sitter_python", "language"),
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "go": ("tree_sitter_go", "language"),
    "json": ("tree_sitter_json", "language"),
    "yaml": ("tree_sitter_yaml", "language"),
    "markdown": ("tree_sitter_markdown", "language"),
}
"""Languages whose wheels ship as direct mnemo dependencies.

A bundled grammar can be loaded on a fresh install with no network
access -- the wheel is in the user's ``site-packages`` because
``pip install mnemo`` pulled it in transitively."""


LAZY_LANGUAGES: dict[str, tuple[str, str]] = {
    "rust": ("tree_sitter_rust", "language"),
    "java": ("tree_sitter_java", "language"),
    "c": ("tree_sitter_c", "language"),
    "cpp": ("tree_sitter_cpp", "language"),
    "ruby": ("tree_sitter_ruby", "language"),
    "php": ("tree_sitter_php", "language_php"),
    "c_sharp": ("tree_sitter_c_sharp", "language"),
    "kotlin": ("tree_sitter_kotlin", "language"),
    "swift": ("tree_sitter_swift", "language"),
    "bash": ("tree_sitter_bash", "language"),
}
"""Languages that round out the Tier 1 set of 16 grammars but aren't
shipped in the wheel. The user runs ``pip install tree-sitter-<lang>``
to enable them on demand; the loader hint names the right package."""


EXT_TO_LANGUAGE: dict[str, str] = {
    # Python
    ".py": "python",
    ".pyi": "python",
    # JavaScript family
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    # TypeScript family -- .tsx routes to the JSX dialect
    ".ts": "typescript",
    ".tsx": "tsx",
    # Go
    ".go": "go",
    # JSON
    ".json": "json",
    ".jsonc": "json",
    # YAML
    ".yaml": "yaml",
    ".yml": "yaml",
    # Markdown
    ".md": "markdown",
    ".markdown": "markdown",
    # Lazy (resolved to a clear install hint if the user hasn't
    # installed the wheel yet)
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "c_sharp",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
}
"""File extension -> registered language name. Phase 4's ingester
dispatches on this. The map deliberately uses lowercase dotted
extensions; :func:`language_for_extension` normalizes case."""


# Parser cache. Keyed by registered language name. Tree-sitter
# Parser objects are cheap to build but caching makes ``get_parser``
# calls O(1) after the first hit and lets downstream code rely on
# identity comparison.
_PARSER_CACHE: dict[str, tree_sitter.Parser] = {}


def is_bundled(language: str) -> bool:
    """True if the named language's wheel ships with mnemo."""
    return language in BUNDLED_LANGUAGES


def language_for_extension(ext: str) -> str | None:
    """Look up a registered language by file extension.

    Returns ``None`` if the extension isn't registered. Case-insensitive
    so callers can pass ``Path.suffix`` directly on Windows where the
    case may not match the registry.
    """
    return EXT_TO_LANGUAGE.get(ext.lower())


def _resolve_spec(language: str) -> tuple[str, str, bool]:
    """Return ``(module_name, function_name, is_bundled)`` for ``language``.

    Raises :class:`GrammarNotAvailableError` if the language isn't in
    either registry (typo / unsupported).
    """
    if language in BUNDLED_LANGUAGES:
        module_name, function_name = BUNDLED_LANGUAGES[language]
        return module_name, function_name, True
    if language in LAZY_LANGUAGES:
        module_name, function_name = LAZY_LANGUAGES[language]
        return module_name, function_name, False
    raise GrammarNotAvailableError(
        f"unknown language: {language!r}. "
        f"Known: {sorted(set(BUNDLED_LANGUAGES) | set(LAZY_LANGUAGES))}"
    )


def get_parser(language: str) -> tree_sitter.Parser:
    """Return a :class:`tree_sitter.Parser` configured for ``language``.

    Lazy-loads the per-language module on first use; subsequent calls
    return the cached Parser.

    Raises :class:`GrammarNotAvailableError` if the language isn't
    known or its wheel isn't installed. The message always names the
    pip-installable package so users have a copy-pasteable fix.
    """
    cached = _PARSER_CACHE.get(language)
    if cached is not None:
        return cached

    module_name, function_name, bundled = _resolve_spec(language)
    pip_name = module_name.replace("_", "-")
    try:
        mod = importlib.import_module(module_name)
    except ImportError as exc:
        if bundled:
            raise GrammarNotAvailableError(
                f"bundled grammar {language!r} failed to import ({exc}). "
                "This usually means the mnemo wheel was repaired by hand; "
                f"`pip install --force-reinstall {pip_name}` should fix it."
            ) from exc
        raise GrammarNotAvailableError(
            f"grammar {language!r} is not bundled. Install with:\n  pip install {pip_name}"
        ) from exc

    try:
        capsule = getattr(mod, function_name)()
    except AttributeError as exc:
        raise GrammarNotAvailableError(
            f"grammar {language!r} ({pip_name}) is installed but its API "
            f"doesn't expose ``{function_name}()``. The package version is "
            "incompatible with this mnemo build."
        ) from exc

    # Import the binding lazily so test environments that stub
    # tree-sitter out (or run on a machine without it) can still
    # import this module for is_bundled / language_for_extension checks.
    import tree_sitter as _ts

    language_obj = _ts.Language(capsule)
    parser = _ts.Parser(language_obj)
    _PARSER_CACHE[language] = parser
    return parser

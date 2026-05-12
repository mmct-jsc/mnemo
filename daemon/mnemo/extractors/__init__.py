"""v2.0 phase 6-8: Tier 3 framework extractors.

Each extractor pattern-matches a specific framework's idioms over the
tree-sitter AST and emits framework-specific :class:`CodeUnit`
records (``code_route``, ``code_component``, ...). The dispatch
table below maps language name to the list of extractors that run
on a tree of that language.

The extractors run AFTER Tier 1 structural extraction has produced
the handler-function units, so each route can carry a
``handler_source_path`` pointer for the reindex post-pass to wire
``routes_to`` edges.

Phase 6 ships backend frameworks:

- ``fastapi`` -- ``@app.{get,post,...}`` decorators on Python
  top-level functions, plus the ``APIRouter`` idiom.
- ``flask`` -- ``@app.route(...)`` / ``@bp.route(...)`` decorators
  with optional ``methods=[...]`` kwarg.
- ``express`` -- ``app.{get,post,...}(path, handler)`` and
  ``router.{get,post,...}(...)`` calls on a JavaScript /
  TypeScript ``express()`` app.

Phase 7 adds frontend frameworks (React, Next.js) and the
cross-stack ``code_endpoint`` node. Phase 8 closes Tier 3 with
Django.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from mnemo.extractors import express as _express
from mnemo.extractors import fastapi as _fastapi
from mnemo.extractors import flask as _flask

if TYPE_CHECKING:  # pragma: no cover -- import-time only
    import tree_sitter

    from mnemo.parsers.code import CodeUnit


# Extractor signature: takes the parsed tree, the source bytes, the
# file path (for source_path composition), and the Tier 1 units
# already produced (so the extractor can match handler names to
# their source_path). Returns just the framework-specific units --
# the caller ``parsers.code.extract`` splices them onto the Tier 1
# list before returning.
FrameworkExtractor = Callable[["tree_sitter.Tree", bytes, str, "list[CodeUnit]"], "list[CodeUnit]"]


FRAMEWORK_EXTRACTORS: dict[str, list[FrameworkExtractor]] = {
    "python": [_fastapi.extract, _flask.extract],
    "javascript": [_express.extract],
    "typescript": [_express.extract],
    "tsx": [_express.extract],
}
"""Per-language list of extractors to run. A single tree can match
multiple frameworks (e.g. a Python file that mixes FastAPI and Flask
-- rare but legal); each extractor returns its own units and the
caller concatenates them."""

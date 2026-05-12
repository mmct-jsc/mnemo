"""v2.0 phase 4 ingest wiring: code_repo sources.

A registered ``code_repo`` source now flows through the tree-sitter
extractor instead of the markdown parser. For each indexable source
file the pipeline yields multiple :class:`ParsedFile` records: one
``code_module`` for the file plus one record per top-level
declaration (with class methods nested under their class).

Edge intent (``defines`` / ``method_of`` / ``imports``) is carried
forward in each record's ``frontmatter_json`` under a ``code_unit``
key. The reindex post-pass turns that intent into real ``Edge``
rows after all nodes are upserted.
"""

from __future__ import annotations

from pathlib import Path

from mnemo import ingest
from mnemo.embed import Embedder
from mnemo.store import Source, Store


class _NullEmbedder(Embedder):
    """Skip-embed stand-in. The reindex flow accepts ``embedder=None``
    in v1.x; we keep this here as a defensive shim in case future code
    starts requiring an embedder instance."""

    def __init__(self) -> None:  # type: ignore[no-untyped-def]
        self.model_name = "null"
        self._cache_dir = Path("/tmp/mnemo-null")
        self._model = object()

    @property
    def dim(self) -> int:
        return 384

    def embed_text(self, text: str) -> list[float]:
        return [0.0 for _ in range(384)]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_text(t) for t in texts]


# --- Default include patterns --------------------------------------------


def test_default_include_for_code_repo_includes_python_and_ts() -> None:
    """A code_repo source with no user-specified include set must walk
    .py / .ts / .tsx / .js / .go etc. by default. Phase 1's safety
    rail (yield nothing when include is empty) gave way to a real
    default in phase 4."""
    patterns = ingest._default_include_for_kind("code_repo")
    flat = " ".join(patterns)
    assert "*.py" in flat
    assert "*.ts" in flat
    assert "*.tsx" in flat
    assert "*.js" in flat
    assert "*.go" in flat


# --- scan_source on a code_repo ------------------------------------------


def _src(path: Path) -> Source:
    return Source(
        path=str(path), kind="code_repo", project_key=None, last_indexed_at=None, enabled=True
    )


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_scan_source_code_repo_yields_module_and_declarations(tmp_path: Path) -> None:
    """A single Python file with one function should yield 2 ParsedFiles:
    one code_module and one code_function."""
    _write(tmp_path / "auth.py", "def login():\n    return True\n")
    parsed = list(ingest.scan_source(_src(tmp_path)))
    types = [p.type for p in parsed]
    assert "code_module" in types
    assert "code_function" in types


def test_scan_source_code_repo_yields_class_with_methods(tmp_path: Path) -> None:
    _write(
        tmp_path / "session.py",
        "class Session:\n    def renew(self):\n        pass\n    def expire(self):\n        pass\n",
    )
    parsed = list(ingest.scan_source(_src(tmp_path)))
    types = [p.type for p in parsed]
    # 1 module + 1 class + 2 methods = 4.
    assert types.count("code_module") == 1
    assert types.count("code_class") == 1
    assert types.count("code_method") == 2


def test_scan_source_code_repo_skips_skip_dirs(tmp_path: Path) -> None:
    """The same DEFAULT_SKIP_DIRS that protect the auto-router protect
    the ingest walker. A file under .git / node_modules / __pycache__
    must NOT produce nodes."""
    _write(tmp_path / "main.py", "def f(): pass\n")
    _write(tmp_path / "__pycache__" / "main.cpython-313.pyc", "")
    _write(tmp_path / ".git" / "config", "[core]\n")
    _write(tmp_path / "node_modules" / "vendor.js", "// vendor\n")
    parsed = list(ingest.scan_source(_src(tmp_path)))
    paths = {p.path for p in parsed}
    # Only main.py and its function should appear.
    assert all("__pycache__" not in str(p) for p in paths)
    assert all(".git" not in str(p) for p in paths)
    assert all("node_modules" not in str(p) for p in paths)


def test_scan_source_code_repo_module_node_has_source_path_equal_to_file(
    tmp_path: Path,
) -> None:
    """Module ``source_path`` is the file path verbatim (no line range)
    so the post-pass can use it as a join key for ``imports`` edges."""
    _write(tmp_path / "x.py", "x = 1\n")
    parsed = list(ingest.scan_source(_src(tmp_path)))
    module = next(p for p in parsed if p.type == "code_module")
    # POSIX-style on both platforms (set by the extractor).
    assert module.source_path.endswith("/x.py")


def test_scan_source_code_repo_function_node_has_line_range(tmp_path: Path) -> None:
    _write(tmp_path / "x.py", "def f():\n    pass\n")
    parsed = list(ingest.scan_source(_src(tmp_path)))
    fn = next(p for p in parsed if p.type == "code_function")
    assert ":" in fn.source_path
    # Line numbers are 1-indexed.
    assert fn.source_path.endswith(":1-2")


# --- Reindex post-pass: edge wiring --------------------------------------


def test_reindex_code_repo_creates_defines_edges(tmp_path: Path) -> None:
    """After reindexing a code_repo source containing a file with one
    top-level function, the store has a ``defines`` edge from the
    module node to the function node."""
    store_path = tmp_path / "store.db"
    repo = tmp_path / "repo"
    _write(repo / "auth.py", "def login():\n    return True\n")
    store = Store(store_path)
    try:
        store.register_source(str(repo), "code_repo")
        ingest.reindex(store, embedder=None)
        nodes = store.list_nodes()
        module = next(n for n in nodes if n.type == "code_module")
        fn = next(n for n in nodes if n.type == "code_function")
        edges = store.get_edges(src_id=module.id, relation="defines")
        dst_ids = {e.dst_id for e in edges}
        assert fn.id in dst_ids
    finally:
        store.close()


def test_reindex_code_repo_creates_method_of_edges(tmp_path: Path) -> None:
    store_path = tmp_path / "store.db"
    repo = tmp_path / "repo"
    _write(
        repo / "session.py",
        "class Session:\n    def renew(self):\n        pass\n",
    )
    store = Store(store_path)
    try:
        store.register_source(str(repo), "code_repo")
        ingest.reindex(store, embedder=None)
        nodes = store.list_nodes()
        cls = next(n for n in nodes if n.type == "code_class")
        method = next(n for n in nodes if n.type == "code_method")
        edges = store.get_edges(src_id=method.id, relation="method_of")
        dst_ids = {e.dst_id for e in edges}
        assert cls.id in dst_ids
    finally:
        store.close()


def test_reindex_code_repo_creates_imports_edge_when_target_exists(
    tmp_path: Path,
) -> None:
    """A Python ``import X`` resolves to an ``imports`` edge when
    another file in the same repo provides module X. The resolver
    matches on the importable module name (file stem for top-level
    modules, dotted path for packages)."""
    store_path = tmp_path / "store.db"
    repo = tmp_path / "repo"
    _write(repo / "consumer.py", "import helper\n\ndef use():\n    helper.f()\n")
    _write(repo / "helper.py", "def f():\n    return 1\n")
    store = Store(store_path)
    try:
        store.register_source(str(repo), "code_repo")
        ingest.reindex(store, embedder=None)
        consumer = next(
            n for n in store.list_nodes() if n.type == "code_module" and n.name == "consumer.py"
        )
        helper = next(
            n for n in store.list_nodes() if n.type == "code_module" and n.name == "helper.py"
        )
        edges = store.get_edges(src_id=consumer.id, relation="imports")
        dst_ids = {e.dst_id for e in edges}
        assert helper.id in dst_ids
    finally:
        store.close()


def test_reindex_code_repo_unresolved_import_does_not_create_edge(
    tmp_path: Path,
) -> None:
    """``import os`` -- stdlib, not in the repo -- must NOT produce an
    edge. Unresolved imports are silent; the design calls this out as
    best-effort."""
    store_path = tmp_path / "store.db"
    repo = tmp_path / "repo"
    _write(repo / "a.py", "import os\n")
    store = Store(store_path)
    try:
        store.register_source(str(repo), "code_repo")
        ingest.reindex(store, embedder=None)
        module = next(n for n in store.list_nodes() if n.type == "code_module")
        edges = store.get_edges(src_id=module.id, relation="imports")
        # No matching target in the repo -> no edge.
        assert edges == []
    finally:
        store.close()


# --- v2.0 phase 5: Tier 2 call-graph resolution --------------------------


def test_reindex_resolves_same_module_free_call_to_calls_edge(tmp_path: Path) -> None:
    """``def a(): b()`` + ``def b(): pass`` in the same file produce
    a ``calls`` edge a -> b with high confidence."""
    store_path = tmp_path / "store.db"
    repo = tmp_path / "repo"
    _write(repo / "x.py", "def a():\n    return b()\n\ndef b():\n    return 1\n")
    store = Store(store_path)
    try:
        store.register_source(str(repo), "code_repo")
        ingest.reindex(store, embedder=None)
        a = next(n for n in store.list_nodes() if n.type == "code_function" and n.name == "a")
        b = next(n for n in store.list_nodes() if n.type == "code_function" and n.name == "b")
        edges = store.get_edges(src_id=a.id, relation="calls")
        dst_ids = {e.dst_id for e in edges}
        assert b.id in dst_ids

    finally:
        store.close()


def test_reindex_resolves_self_method_call_to_calls_edge(tmp_path: Path) -> None:
    """``self.method()`` in caller resolves to the method on the same
    class. Uses the method_of edge graph laid down in phase 4."""
    store_path = tmp_path / "store.db"
    repo = tmp_path / "repo"
    _write(
        repo / "x.py",
        "class C:\n"
        "    def helper(self):\n"
        "        return 1\n"
        "    def caller(self):\n"
        "        return self.helper()\n",
    )
    store = Store(store_path)
    try:
        store.register_source(str(repo), "code_repo")
        ingest.reindex(store, embedder=None)
        caller = next(
            n for n in store.list_nodes() if n.type == "code_method" and n.name == "caller"
        )
        helper = next(
            n for n in store.list_nodes() if n.type == "code_method" and n.name == "helper"
        )
        edges = store.get_edges(src_id=caller.id, relation="calls")
        dst_ids = {e.dst_id for e in edges}
        assert helper.id in dst_ids
    finally:
        store.close()


def test_reindex_resolves_constructor_call_to_class(tmp_path: Path) -> None:
    """``Session()`` resolves to the ``Session`` class node, not to a
    function. Picks up via name lookup in the module scope."""
    store_path = tmp_path / "store.db"
    repo = tmp_path / "repo"
    _write(repo / "x.py", "class Session: pass\n\ndef make():\n    return Session()\n")
    store = Store(store_path)
    try:
        store.register_source(str(repo), "code_repo")
        ingest.reindex(store, embedder=None)
        make = next(n for n in store.list_nodes() if n.type == "code_function")
        session = next(n for n in store.list_nodes() if n.type == "code_class")
        edges = store.get_edges(src_id=make.id, relation="calls")
        dst_ids = {e.dst_id for e in edges}
        assert session.id in dst_ids
    finally:
        store.close()


def test_reindex_resolves_cross_file_call_via_imports(tmp_path: Path) -> None:
    """``import helper; helper.f()`` in consumer.py resolves to ``f``
    in helper.py by walking the imports edge to the target module then
    matching the function name within that module."""
    store_path = tmp_path / "store.db"
    repo = tmp_path / "repo"
    _write(repo / "consumer.py", "import helper\n\ndef use():\n    return helper.f()\n")
    _write(repo / "helper.py", "def f():\n    return 1\n")
    store = Store(store_path)
    try:
        store.register_source(str(repo), "code_repo")
        ingest.reindex(store, embedder=None)
        use = next(n for n in store.list_nodes() if n.type == "code_function" and n.name == "use")
        f = next(n for n in store.list_nodes() if n.type == "code_function" and n.name == "f")
        edges = store.get_edges(src_id=use.id, relation="calls")
        dst_ids = {e.dst_id for e in edges}
        assert f.id in dst_ids
    finally:
        store.close()


def test_reindex_unresolved_call_produces_no_edge(tmp_path: Path) -> None:
    """``stdlib_function()`` with no matching definition in the repo
    must NOT produce an edge. Tier 2 stays best-effort -- no edge is
    better than a wrong edge for retrieval quality."""
    store_path = tmp_path / "store.db"
    repo = tmp_path / "repo"
    _write(repo / "x.py", "def use():\n    return some_unknown_function()\n")
    store = Store(store_path)
    try:
        store.register_source(str(repo), "code_repo")
        ingest.reindex(store, embedder=None)
        use = next(n for n in store.list_nodes() if n.type == "code_function")
        edges = store.get_edges(src_id=use.id, relation="calls")
        assert edges == []
    finally:
        store.close()


def test_reindex_calls_edge_confidence_high_for_same_file(tmp_path: Path) -> None:
    """Same-file (same-scope) resolution is the most confident -- the
    resolver knows exactly where the callee lives. The design pegs
    this at 0.95."""
    store_path = tmp_path / "store.db"
    repo = tmp_path / "repo"
    _write(repo / "x.py", "def a():\n    return b()\n\ndef b():\n    return 1\n")
    store = Store(store_path)
    try:
        store.register_source(str(repo), "code_repo")
        ingest.reindex(store, embedder=None)
        a = next(n for n in store.list_nodes() if n.type == "code_function" and n.name == "a")
        edge = next(iter(store.get_edges(src_id=a.id, relation="calls")))
        assert edge.confidence >= 0.9

    finally:
        store.close()


def test_reindex_calls_edge_confidence_lower_for_cross_file(tmp_path: Path) -> None:
    """Cross-file resolution is less confident than same-file because
    we hop through the imports edge. Confidence stays high enough to
    be useful but distinguishably lower than the within-file case."""
    store_path = tmp_path / "store.db"
    repo = tmp_path / "repo"
    _write(repo / "consumer.py", "import helper\n\ndef use():\n    return helper.f()\n")
    _write(repo / "helper.py", "def f():\n    return 1\n")
    store = Store(store_path)
    try:
        store.register_source(str(repo), "code_repo")
        ingest.reindex(store, embedder=None)
        use = next(n for n in store.list_nodes() if n.type == "code_function" and n.name == "use")
        edge = next(iter(store.get_edges(src_id=use.id, relation="calls")))
        # Lower than 1.0 (default) and lower than the same-file 0.95.
        assert edge.confidence < 0.95
    finally:
        store.close()

"""Ingestion: discover sources, parse memory files, reconcile with the Store.

This module is pure logic over the filesystem and the Store; it does not run
any background tasks. The watcher (``mnemo.watcher``) calls into ``reindex``
on file change events.

Inference rules when frontmatter is missing or partial:

- ``type``: frontmatter ``type`` ("user"/"feedback"/"project"/"reference",
  mapped to ``memory_<value>``) -> filename prefix (``user_*``, ``feedback_*``,
  ``project_*``, ``reference_*``) -> source-kind default
  (``claude_md`` -> ``project_doc``, ``plan_dir`` -> ``plan_doc``,
  otherwise ``memory_project``).
- ``name``: frontmatter ``name`` -> filename stem.
- ``description``: frontmatter ``description`` -> first heading -> first 100 chars.
- ``project_key``: explicit argument -> frontmatter ``projectKey`` -> last segment
  of ``.../projects/<key>/memory/...`` -> ``None``.
- ``hash``: sha256 of the raw file bytes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from mnemo import auto_router, git_log, parsers
from mnemo.parsers import code as code_parser
from mnemo.parsers import scope as scope_resolver
from mnemo.parsers import tree_sitter as ts_loader
from mnemo.paths import path_under_source
from mnemo.store import NODE_TYPES, SOURCE_KINDS, Node, Source, Store

log = logging.getLogger(__name__)


# Filename-prefix -> node type
FILENAME_TYPE_PREFIXES: dict[str, str] = {
    "user_": "memory_user",
    "feedback_": "memory_feedback",
    "project_": "memory_project",
    "reference_": "memory_reference",
}

# Project key extracted from path segment like 'D--Repository-aibox-prod-all'
_PROJECT_KEY_RE = re.compile(r"projects[\\/]([^\\/]+)[\\/]memory")


# --- Dataclasses -----------------------------------------------------------


@dataclass
class DiscoveredSource:
    path: Path
    kind: str  # member of SOURCE_KINDS
    project_key: str | None


@dataclass
class ParsedFile:
    path: Path
    type: str
    name: str
    description: str | None
    body: str
    frontmatter_json: str | None
    hash: str
    source_kind: str
    project_key: str | None
    # v1.1: frontmatter ``base: true`` flag. BASE knowledge bypasses
    # project isolation and surfaces in every project's queries.
    base: bool = False
    # v2.0 phase 4: when set, used as the resulting ``Node.source_path``
    # instead of ``str(path)``. Code declarations use this to encode
    # ``<file>:<start_line>-<end_line>`` so two same-name functions in
    # the same file get distinct nodes. For v1.x source kinds it stays
    # None and reindex falls back to the path-as-string default.
    source_path: str | None = None


@dataclass
class ReindexReport:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total_seen(self) -> int:
        return self.added + self.updated + self.unchanged


# --- Discovery -------------------------------------------------------------


def discover_default_sources(claude_home: Path) -> list[DiscoveredSource]:
    """Default Scope B discovery rooted at a Claude Code config directory.

    Yields, in deterministic order:

    1. ``<claude_home>/CLAUDE.md`` (if it exists), as ``claude_md``.
    2. Each ``<claude_home>/projects/<key>/memory/`` directory (if it exists),
       as ``memory_dir`` with ``project_key=<key>``.
    """
    out: list[DiscoveredSource] = []

    global_md = claude_home / "CLAUDE.md"
    if global_md.is_file():
        out.append(DiscoveredSource(path=global_md, kind="claude_md", project_key=None))

    projects_root = claude_home / "projects"
    if projects_root.is_dir():
        for project_dir in sorted(projects_root.iterdir()):
            if not project_dir.is_dir():
                continue
            mem_dir = project_dir / "memory"
            if mem_dir.is_dir():
                out.append(
                    DiscoveredSource(
                        path=mem_dir,
                        kind="memory_dir",
                        project_key=project_dir.name,
                    )
                )

    return out


# --- Parsing ---------------------------------------------------------------


def _hash_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _infer_type_from_name(stem: str, fallback: str = "memory_project") -> str:
    for prefix, t in FILENAME_TYPE_PREFIXES.items():
        if stem.startswith(prefix):
            return t
    return fallback


def _infer_project_key_from_path(path: Path) -> str | None:
    s = str(path).replace("\\", "/")
    m = _PROJECT_KEY_RE.search(s)
    return m.group(1) if m else None


def _first_heading(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or None
    return None


def _resolve_type(fm: dict[str, object], path: Path, kind: str) -> str:
    raw = fm.get("type")
    if isinstance(raw, str):
        # Memory frontmatter uses bare types: 'user', 'feedback', 'project', 'reference'.
        candidate = f"memory_{raw.strip()}"
        if candidate in NODE_TYPES:
            return candidate
        if raw in NODE_TYPES:
            return raw  # already-namespaced
    # Source-kind defaults
    if kind == "claude_md":
        return "project_doc"
    if kind == "plan_dir":
        return "plan_doc"
    # memory_dir / transcripts -> infer from filename
    return _infer_type_from_name(path.stem)


def _resolve_description(fm: dict[str, object], body: str) -> str | None:
    desc = fm.get("description")
    if isinstance(desc, str) and desc.strip():
        return desc.strip()
    heading = _first_heading(body)
    if heading:
        return heading
    snippet = body.strip()[:100]
    return snippet or None


def _resolve_project_key(fm: dict[str, object], path: Path, project_key: str | None) -> str | None:
    if project_key is not None:
        return project_key
    fm_key = fm.get("projectKey")
    if isinstance(fm_key, str) and fm_key.strip():
        return fm_key.strip()
    return _infer_project_key_from_path(path)


def parse_file(path: Path, *, kind: str, project_key: str | None = None) -> ParsedFile:
    """Read one file from disk and produce a ParsedFile (no DB access).

    Dispatches to the parser registry by file extension. Markdown files
    keep their frontmatter; plain text and PDF files have empty
    frontmatter and rely on filename + content heuristics for name
    and description.
    """
    if kind not in SOURCE_KINDS:
        raise ValueError(f"unknown source kind: {kind!r}")
    raw_bytes = path.read_bytes()
    file_hash = _hash_bytes(raw_bytes)
    fm, body = parsers.parse(raw_bytes, path)

    return ParsedFile(
        path=path,
        type=_resolve_type(fm, path, kind),
        name=str(fm.get("name") or path.stem),
        description=_resolve_description(fm, body),
        body=body,
        frontmatter_json=json.dumps(fm, sort_keys=True, default=str) if fm else None,
        hash=file_hash,
        source_kind=kind,
        project_key=_resolve_project_key(fm, path, project_key),
        base=_resolve_base_flag(fm),
    )


def parse_code_file(path: Path, *, project_key: str | None = None) -> list[ParsedFile]:
    """v2.0 phase 4: dispatch a single code file through the tree-sitter
    extractor.

    Returns one :class:`ParsedFile` per :class:`CodeUnit`. The first
    record is always the ``code_module`` for the file itself;
    subsequent records are top-level declarations and class methods.

    Edge intent (``defines`` / ``method_of`` / ``imports``) is JSON-
    encoded into each record's ``frontmatter_json`` under a
    ``code_unit`` key. The reindex post-pass turns the intent into
    real ``Edge`` rows.

    Unknown / unsupported extensions return an empty list -- the
    walker shouldn't call this with non-code files but we stay
    defensive (rather than letting a stray extension crash the
    whole reindex).
    """
    language = ts_loader.language_for_extension(path.suffix)
    if language is None:
        return []
    try:
        source = path.read_bytes()
    except OSError:
        return []
    units = code_parser.extract(path, source, language=language)

    project = _resolve_project_key({}, path, project_key)
    out: list[ParsedFile] = []
    for u in units:
        # Pack the edge intent into frontmatter_json so the reindex
        # post-pass can read it back after the node is upserted.
        # v2.0 phase 5: call_sites travel here too -- the scope
        # resolver consumes them after Tier 1 edges are wired.
        # v2.0 phase 6: framework-extracted routes thread their
        # handler pointer + framework metadata through the same
        # ``code_unit`` block.
        edge_intent: dict[str, object] = {
            "imports": u.imports,
            "children_source_paths": u.children_source_paths,
            "parent_source_path": u.parent_source_path,
            "call_sites": [
                {"callee": cs.callee_name, "receiver": cs.receiver, "line": cs.line}
                for cs in u.call_sites
            ],
            "framework": u.framework,
            "route_method": u.route_method,
            "route_path": u.route_path,
            "handler_source_path": u.handler_source_path,
        }
        fm = {"code_unit": edge_intent}
        out.append(
            ParsedFile(
                path=path,
                type=u.type,
                name=u.name,
                description=u.description,
                body=u.body,
                frontmatter_json=json.dumps(fm, sort_keys=True),
                hash=u.hash,
                source_kind="code_repo",
                project_key=project,
                base=False,
                source_path=u.source_path,
            )
        )
    return out


def _resolve_base_flag(fm: dict[str, object]) -> bool:
    """Read frontmatter ``base`` (truthy) -> True. Treat 'true', 'yes',
    '1' (case-insensitive) as True. Default False."""
    val = fm.get("base")
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    return s in ("true", "yes", "1", "y", "on")


# --- Scanning --------------------------------------------------------------


def _default_include_for_kind(kind: str) -> list[str]:
    """Default include patterns when a source's ``include`` field is unset.

    memory_dir / plan_dir / transcripts: match every file type the
    parser registry knows how to handle (markdown, plain text, PDF).
    Sources that want narrower behavior set their own ``include``.
    claude_md: always matches its single configured file -- no walk.

    v2.0 phase 4: ``code_repo`` defaults to the bundled tree-sitter
    languages (Python / TS/TSX / JS / Go / JSON / YAML / Markdown)
    plus C-family / Java / Rust / Ruby / Bash / PHP for repos that
    have those wheels installed via the lazy loader. The walker
    additionally skips the ``DEFAULT_SKIP_DIRS`` set from the
    auto-router so ``node_modules`` / ``__pycache__`` / ``.git`` /
    etc. never reach the extractor.

    ``docs_dir`` keeps the empty default for one more phase --
    docs_dir ingestion lands in phase 11 with the ``/code`` UI work.
    """
    if kind in ("memory_dir", "plan_dir", "transcripts"):
        return ["**/*.md", "**/*.markdown", "**/*.txt", "**/*.pdf"]
    if kind == "code_repo":
        # One pattern per registered extension so users can read the
        # list and immediately understand the scope of the walk. The
        # extension dispatch table in ``parsers.tree_sitter`` is the
        # canonical registry; this list mirrors it.
        return [f"**/*{ext}" for ext in sorted(ts_loader.EXT_TO_LANGUAGE.keys())]
    return []


def _parse_pattern_field(raw: str | None) -> list[str]:
    """Parse a comma-separated glob list. Empty -> []."""
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def _build_pathspec(patterns: list[str]):  # type: ignore[no-untyped-def]
    """Compile glob patterns to a pathspec.PathSpec, or None if empty.

    We import pathspec lazily so test harnesses that stub out ingest don't
    have to install it.
    """
    if not patterns:
        return None
    import pathspec

    return pathspec.PathSpec.from_lines("gitignore", patterns)


def scan_source(source: Source) -> Iterator[ParsedFile]:
    """Walk a source's path and yield one ParsedFile per indexable file.

    Honors per-source ``include`` and ``exclude`` glob patterns
    (gitignore-style via the pathspec library). Empty/unset include falls
    back to the kind's default include set.

    - ``claude_md`` source: always yields the single file (if it exists).
      Patterns are ignored for single-file sources.
    - ``memory_dir`` / ``plan_dir`` / ``transcripts``: walks the directory
      and yields files that match include AND don't match exclude. Default
      include matches ``*.md``, ``*.txt``, and ``*.pdf``.
    - Index files named ``MEMORY.md`` are always skipped.
    """
    p = Path(source.path)
    if not p.exists():
        return
    if p.is_file():
        yield parse_file(p, kind=source.kind, project_key=source.project_key)
        return

    include_patterns = _parse_pattern_field(source.include) or _default_include_for_kind(
        source.kind
    )
    # v2.0 phase 1 safety: empty include set means "this kind has no
    # parser wired yet" (currently only ``docs_dir`` -- code_repo got
    # its defaults in phase 4). Yield nothing rather than letting the
    # rglob fan-out feed every file to the markdown parser.
    if not include_patterns:
        return
    exclude_patterns = _parse_pattern_field(source.exclude)
    include_spec = _build_pathspec(include_patterns)
    exclude_spec = _build_pathspec(exclude_patterns)
    # v2.0 phase 4: skip the same noisy directories the auto-router
    # skips. Without this, every reindex of a code_repo would try to
    # parse every file in ``node_modules`` / ``__pycache__`` / etc.
    skip_dirs = auto_router.DEFAULT_SKIP_DIRS if source.kind == "code_repo" else frozenset()

    for f in sorted(p.rglob("*")):
        if not f.is_file():
            continue
        if f.name == "MEMORY.md":
            continue
        rel = f.relative_to(p)
        if skip_dirs and any(part in skip_dirs for part in rel.parts[:-1]):
            continue
        rel_posix = rel.as_posix()
        if include_spec is not None and not include_spec.match_file(rel_posix):
            continue
        if exclude_spec is not None and exclude_spec.match_file(rel_posix):
            continue
        if source.kind == "code_repo":
            yield from parse_code_file(f, project_key=source.project_key)
        else:
            yield parse_file(f, kind=source.kind, project_key=source.project_key)


# --- Reconciliation --------------------------------------------------------


def _path_under_source(node_path: str, src_path: str, src_kind: str) -> bool:
    # v1.1.1: shared with store.remove_source via mnemo.paths so the source
    # remove cascade uses identical "owned by this source" semantics. Kept as
    # a private re-export for callers that imported it from this module.
    return path_under_source(node_path, src_path, src_kind)


def reindex(
    store: Store,
    *,
    sources: list[Source] | None = None,
    embedder: object | None = None,
) -> ReindexReport:
    """Reindex enabled sources. Idempotent.

    Synchronous wrapper around ``reindex_events`` -- consumes the event
    generator and returns the final tally. The generator is the v2.2
    streaming surface (see docs/plans/2026-05-14-ux-progressive-design.md
    § 2) but this function preserves the legacy report-only return
    shape for callers that don't care about per-file progress (CLI +
    POST /v1/reindex).
    """
    return _drain(reindex_events(store, sources=sources, embedder=embedder))


def reindex_events(
    store: Store,
    *,
    sources: list[Source] | None = None,
    embedder: object | None = None,
) -> Iterator[tuple[str, dict]]:
    """Reindex enabled sources, yielding ``(event_name, payload)`` tuples.

    Event sequence:

      ('start', {'started_at': int})
      ('file',  {'idx': int, 'path': str, 'status': str,
                 'added': int, 'updated': int, 'unchanged': int,
                 'errors': list}) -- one per parsed file
      ('done',  {'added': int, 'updated': int, 'unchanged': int,
                 'removed': int, 'errors': list, 'duration_ms': int})

    ``status`` is one of ``indexed`` / ``updated`` / ``unchanged`` /
    ``error``. The generator is the single source of truth -- the
    legacy ``reindex()`` is now a thin consumer of it.

    v2.0 phase 4: code_repo sources emit multiple nodes per file
    (module + declarations). After the upsert loop, a post-pass walks
    the freshly-upserted nodes' frontmatter for ``code_unit`` edge
    intent and creates ``defines`` / ``method_of`` / ``imports`` edges.
    """
    started_at = int(time.time())
    yield ("start", {"started_at": started_at})

    report = ReindexReport()
    src_list = sources if sources is not None else store.list_sources(only_enabled=True)
    seen_source_paths: set[str] = set()
    # Track every just-touched code node so the edge post-pass can run
    # against a small, freshly-parsed set instead of re-scanning the
    # whole store.
    touched_code_node_ids: list[str] = []
    file_idx = 0

    for src in src_list:
        try:
            for parsed in scan_source(src):
                node_source_path = parsed.source_path or str(parsed.path)
                seen_source_paths.add(node_source_path)
                existing = store.get_node_by_source(node_source_path)
                file_idx += 1
                # Default per-file event delta; set the right counter
                # below based on which branch we took.
                delta = {"added": 0, "updated": 0, "unchanged": 0}
                status: str
                if existing is None:
                    new_node = Node.new(
                        type=parsed.type,
                        name=parsed.name,
                        body=parsed.body,
                        source_path=node_source_path,
                        source_kind=parsed.source_kind,
                        description=parsed.description,
                        project_key=parsed.project_key,
                        frontmatter_json=parsed.frontmatter_json,
                        hash=parsed.hash,
                        base=parsed.base,
                    )
                    store.upsert_node(new_node)
                    if embedder is not None:
                        _embed(store, new_node, embedder)
                    report.added += 1
                    delta["added"] = 1
                    status = "indexed"
                    if parsed.type.startswith("code_"):
                        touched_code_node_ids.append(new_node.id)
                elif existing.hash != parsed.hash:
                    existing.type = parsed.type
                    existing.name = parsed.name
                    existing.description = parsed.description
                    existing.body = parsed.body
                    existing.source_kind = parsed.source_kind
                    existing.project_key = parsed.project_key
                    existing.frontmatter_json = parsed.frontmatter_json
                    existing.hash = parsed.hash
                    existing.base = parsed.base
                    existing.updated_at = int(time.time())
                    store.upsert_node(existing)
                    if embedder is not None:
                        _embed(store, existing, embedder)
                    report.updated += 1
                    delta["updated"] = 1
                    status = "updated"
                    if parsed.type.startswith("code_"):
                        touched_code_node_ids.append(existing.id)
                else:
                    report.unchanged += 1
                    delta["unchanged"] = 1
                    status = "unchanged"
                    if parsed.type.startswith("code_"):
                        # Same hash but the edges might have been
                        # missed last run (e.g. an imports target was
                        # added after the first reindex). Cheap to
                        # re-resolve so include it in the post-pass.
                        touched_code_node_ids.append(existing.id)
                yield (
                    "file",
                    {
                        "idx": file_idx,
                        "path": node_source_path,
                        "status": status,
                        **delta,
                        "errors": [],
                    },
                )
            store.mark_source_indexed(src.path)
        except Exception as exc:  # noqa: BLE001 - we want to keep going on bad files
            report.errors.append((src.path, str(exc)))
            log.warning("error scanning source %s: %s", src.path, exc)
            file_idx += 1
            yield (
                "file",
                {
                    "idx": file_idx,
                    "path": src.path,
                    "status": "error",
                    "added": 0,
                    "updated": 0,
                    "unchanged": 0,
                    "errors": [str(exc)],
                },
            )

    # v2.0 phase 4 edge post-pass: resolve code-unit edge intent into
    # real Edge rows. We run this AFTER all nodes are upserted so the
    # within-file pointers (defines, method_of) and the cross-file
    # imports lookups can hit the freshly-populated graph.
    if touched_code_node_ids:
        _resolve_code_edges(store, touched_code_node_ids)
        # v2.0 phase 5: Tier 2 ``calls`` resolution runs AFTER Tier 1
        # so the resolver can walk the just-wired imports + method_of
        # edges. Returns the count for diagnostics; we don't surface
        # it in the report shape today (the ReindexReport tracks node
        # counts, not edge counts).
        scope_resolver.resolve_calls(store, touched_code_node_ids)

    # v2.3.0 phase 9: git-log ingestion + provenance edges. Each
    # code_repo source gains a commit-history sub-ingest that:
    #   1. Walks ``git log --max-count=<limit>`` (newest first).
    #   2. Upserts one ``commit`` node per commit (idempotent on
    #      source_path = ``<repo_path>@<full_sha>``).
    #   3. Wires ``references_function`` edges from each commit to
    #      the code_function / code_method / code_module nodes
    #      whose [start, end] line range overlaps the commit's
    #      post-image diff hunks. Confidence proportional to the
    #      fraction of the function's lines the commit changed.
    #   4. Wires ``closed_by`` edges from memory nodes named in
    #      ``Fixes:`` / ``Closes:`` / ``Refs:`` trailers to the
    #      commit that resolved them (confidence 1.0).
    #   5. Wires ``motivated_by`` edges from each commit to memory
    #      nodes whose name appears in the commit body (confidence
    #      0.9). The co-temporal embedding heuristic from the
    #      design § 6 is deferred to a later release.
    # Wrapped in a try/except so a malformed repo can't kill the
    # whole reindex.
    if src_list:
        for src in src_list:
            if src.kind != "code_repo":
                continue
            try:
                _ingest_git_log_for_source(store, src, seen_source_paths)
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("git-log ingest failed for %s: %s", src.path, exc)

    # Deletions: any node whose source_path falls under a scanned source
    # but wasn't seen this run.
    if src_list:
        all_nodes = store.list_nodes(limit=1_000_000)
        for node in all_nodes:
            for src in src_list:
                if path_under_source(node.source_path, src.path, src.kind):
                    if node.source_path not in seen_source_paths:
                        store.delete_node(node.id)
                        report.removed += 1
                    break

    duration_ms = max(0, int((time.time() - started_at) * 1000))
    yield (
        "done",
        {
            "added": report.added,
            "updated": report.updated,
            "unchanged": report.unchanged,
            "removed": report.removed,
            # Errors are tuples; serialize as plain list for JSON.
            "errors": [{"path": p, "error": e} for (p, e) in report.errors],
            "duration_ms": duration_ms,
        },
    )


def _drain(events: Iterator[tuple[str, dict]]) -> ReindexReport:
    """Consume a ``reindex_events`` generator and reconstruct the legacy
    ``ReindexReport`` shape from the final ``done`` payload.

    Centralized here so existing callers (``ingest.reindex`` + future
    server-side helpers) don't each have to know the event protocol.
    """
    report = ReindexReport()
    for name, payload in events:
        if name == "done":
            report.added = payload["added"]
            report.updated = payload["updated"]
            report.unchanged = payload["unchanged"]
            report.removed = payload["removed"]
            # done's errors are dicts {path, error}; legacy report uses tuples.
            report.errors = [(e["path"], e["error"]) for e in payload["errors"]]
    return report


def _resolve_code_edges(store: Store, node_ids: list[str]) -> None:
    """Wire ``defines`` / ``method_of`` / ``imports`` edges for the
    code nodes just touched by reindex.

    Reads each node's ``frontmatter_json`` for the ``code_unit`` intent
    block:

    - ``children_source_paths``: source_paths of top-level declarations
      this module ``defines``.
    - ``parent_source_path``: source_path of the containing class for
      a ``code_method`` (the ``method_of`` target).
    - ``imports``: module-name strings the file imports. Resolved
      against ``code_module`` nodes in the same store by matching the
      module name (file stem for top-level, dotted path for packages).
      Best-effort: unmatched names produce no edge.
    """
    # Build a source_path -> node_id index for the fresh nodes plus
    # any other code_module / code_class nodes already in the store.
    # For ``defines`` / ``method_of`` resolution we need the full set
    # because the parent class might be in a different parsed batch.
    sp_to_id: dict[str, str] = {}
    name_to_module_id: dict[str, str] = {}
    for n in store.list_nodes(type="code_module", limit=1_000_000):
        sp_to_id[n.source_path] = n.id
        # The importable module name is the file stem for top-level
        # files. Packages (dotted paths) need future framework
        # extractors to capture; phase 4 handles only the trivial
        # ``import name`` -> ``name.py`` case.
        stem = Path(n.source_path).stem
        name_to_module_id.setdefault(stem, n.id)
    for ct in ("code_class", "code_function", "code_method", "code_route"):
        for n in store.list_nodes(type=ct, limit=1_000_000):
            sp_to_id[n.source_path] = n.id

    for nid in node_ids:
        node = store.get_node(nid)
        if node is None or not node.frontmatter_json:
            continue
        try:
            fm = json.loads(node.frontmatter_json)
        except ValueError:
            continue
        intent = fm.get("code_unit")
        if not isinstance(intent, dict):
            continue

        # method_of: this node points at its containing class.
        parent = intent.get("parent_source_path")
        if isinstance(parent, str) and parent in sp_to_id:
            store.add_edge(node.id, sp_to_id[parent], "method_of")

        # defines: module -> top-level declarations in this file.
        children = intent.get("children_source_paths") or []
        for child_sp in children:
            target = sp_to_id.get(child_sp)
            if target is not None and target != node.id:
                store.add_edge(node.id, target, "defines")

        # imports: module -> module. Best-effort name match.
        imports = intent.get("imports") or []
        for imp_name in imports:
            if not isinstance(imp_name, str):
                continue
            # ``mnemo.store`` -> last segment ``store``. Tier 1 imports
            # resolution is "shallow": a more sophisticated package /
            # __init__ walk lands in a later phase.
            last = imp_name.rsplit(".", 1)[-1]
            target = name_to_module_id.get(last)
            if target is not None and target != node.id:
                # Inferred edges carry calibrated uncertainty; imports
                # is the most-confident structural inference at Tier 1.
                store.add_edge(node.id, target, "imports", confidence=0.9)

        # v2.0 phase 6: routes_to. A ``code_route`` node carries its
        # handler's source_path in the intent block; resolve it to a
        # node id and wire the edge. Confidence 0.95 mirrors the
        # within-file resolution semantics (the extractor identified
        # the exact decorator + handler pair in the same parse).
        handler_sp = intent.get("handler_source_path")
        if isinstance(handler_sp, str) and handler_sp in sp_to_id:
            target_id = sp_to_id[handler_sp]
            if target_id != node.id:
                store.add_edge(node.id, target_id, "routes_to", confidence=0.95)

    # v2.0 phase 7: endpoint dedup + at_endpoint edges.
    # For every freshly-touched ``code_route`` or ``code_component``
    # whose intent block carries ``route_method`` + ``route_path``,
    # upsert a shared ``code_endpoint`` node (keyed by
    # ``endpoint:METHOD:path``) and wire an ``at_endpoint`` edge.
    # This is what creates the cross-stack join: a React component
    # that fetches ``/api/users`` and a FastAPI route at the same
    # path both end up pointing at the same endpoint node.
    _resolve_endpoint_edges(store, node_ids)


def _resolve_endpoint_edges(store: Store, node_ids: list[str]) -> None:
    """Upsert ``code_endpoint`` nodes for routes / components that
    declare a (method, path) and wire each touched declarer to the
    endpoint via an ``at_endpoint`` edge.

    Endpoints are deduplicated by source_path = ``endpoint:METHOD:path``.
    The endpoint node's project_key is left None (cross-cutting), so
    strict-isolation retrieval treats it as a shared anchor between
    every project's frontend and backend.
    """
    for nid in node_ids:
        node = store.get_node(nid)
        if node is None or not node.frontmatter_json:
            continue
        if node.type not in ("code_route", "code_component"):
            continue
        try:
            fm = json.loads(node.frontmatter_json)
        except ValueError:
            continue
        intent = fm.get("code_unit")
        if not isinstance(intent, dict):
            continue
        method = intent.get("route_method")
        path = intent.get("route_path")
        if not isinstance(method, str) or not isinstance(path, str):
            continue

        endpoint_sp = f"endpoint:{method}:{path}"
        endpoint = store.get_node_by_source(endpoint_sp)
        if endpoint is None:
            endpoint = Node.new(
                type="code_endpoint",
                name=f"{method} {path}",
                body="",
                source_path=endpoint_sp,
                source_kind="code_repo",
                description=f"Endpoint {method} {path}",
                hash=endpoint_sp,
            )
            store.upsert_node(endpoint)
        store.add_edge(node.id, endpoint.id, "at_endpoint", confidence=0.9)


def _ingest_git_log_for_source(
    store: Store,
    src: Source,
    seen_source_paths: set[str],
) -> None:
    """v2.3.0 phase 9: walk ``git log`` for a ``code_repo`` source and
    upsert ``commit`` nodes + decision-provenance edges.

    Steps:

    1. Walk ``git log --max-count=<limit>`` (newest first).
    2. For each commit:
       a. Upsert a ``commit`` node (idempotent on
          ``source_path = "<repo_path>@<full_sha>"``).
       b. Record its source_path in ``seen_source_paths`` so the
          deletion sweep doesn't garbage-collect it.
       c. Parse the commit's diff (``git show --unified=0
          --no-prefix``) and emit ``references_function`` edges to
          any code_function / code_method / code_module whose
          [start, end] line range overlaps a touched range.
       d. Parse ``Fixes:`` / ``Closes:`` / ``Refs:`` trailers and
          emit ``closed_by`` edges from referenced memory nodes
          (looked up by exact name) to the commit.
       e. Find word-bounded memory-node-name mentions in the
          commit body and emit ``motivated_by`` edges from commit
          to each matched memory node (confidence 0.9).

    Errors during diff fetch / edge resolution are logged but
    don't kill the whole walk; one bad commit shouldn't lose us
    the rest of the history.
    """
    repo_path = Path(src.path)
    # Source.frontmatter_json / Source.commit_limit don't exist yet;
    # fall back to the module default. A future ``mnemo source patch
    # <path> --commit-limit N`` CLI lands the per-source override.
    limit = git_log.DEFAULT_COMMIT_LIMIT

    # Pre-fetch the joins we'll do per commit. Building these once is
    # O(N) over the whole code graph + memory graph; doing them per
    # commit would be O(N * commits).
    code_nodes_in_file: dict[str, list[tuple[str, int, int]]] = {}
    for code_type in ("code_function", "code_method", "code_module"):
        for node in store.list_nodes(type=code_type, limit=1_000_000):
            sp = node.source_path or ""
            # Code node source_paths carry a ``:start-end`` suffix
            # appended by the code parser. Pull the line range out.
            range_match = re.search(r":(\d+)-(\d+)(?:#.*)?$", sp)
            if not range_match:
                continue
            file_part = sp[: range_match.start()]
            # The path stored is repo-relative + posix. We compare
            # against the diff's per-file paths which are also
            # repo-relative posix (git show --no-prefix gives us that).
            # If the stored path starts with the repo path, strip it.
            try:
                file_rel = str(Path(file_part).relative_to(repo_path)).replace("\\", "/")
            except ValueError:
                # Not under this repo; skip.
                continue
            start = int(range_match.group(1))
            end = int(range_match.group(2))
            code_nodes_in_file.setdefault(file_rel, []).append((node.id, start, end))

    memory_nodes_by_name: dict[str, str] = {}
    for memory_type in (
        "memory_feedback",
        "memory_project",
        "memory_reference",
        "memory_user",
        "plan_doc",
        "project_doc",
        "session_summary",
    ):
        for node in store.list_nodes(type=memory_type, limit=1_000_000):
            if node.name and node.name not in memory_nodes_by_name:
                memory_nodes_by_name[node.name] = node.id

    for entry in git_log.walk_commits(repo_path, limit=limit):
        commit_source_path = f"{repo_path}@{entry.sha}"
        seen_source_paths.add(commit_source_path)
        # Idempotency: re-running reindex against the same repo should
        # NOT create duplicate commit nodes. Look up by source_path.
        existing = store.get_node_by_source(commit_source_path)
        if existing is not None:
            commit_node_id = existing.id
        else:
            new_node = git_log.commit_to_node(
                entry,
                repo_path=str(repo_path),
                project_key=src.project_key,
            )
            store.upsert_node(new_node)
            commit_node_id = new_node.id

        # references_function edges: parse the commit's diff once
        # and join touched ranges to code-node ranges in the same file.
        try:
            diff_text = git_log.show_commit_diff(repo_path, entry.sha)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("git show %s failed: %s", entry.sha, exc)
            diff_text = ""
        diff_lines = git_log.parse_diff_hunks(diff_text) if diff_text else {}
        for src_id, dst_id, confidence in git_log.compute_references_function_edges(
            commit_node_id,
            diff_lines,
            code_nodes_in_file,
        ):
            store.add_edge(src_id, dst_id, "references_function", confidence=confidence)

        # closed_by edges (trailer -> doc -> this commit). Confidence
        # 1.0 because the commit author explicitly declared it.
        trailer_targets = git_log.parse_closed_by_trailers(entry.body)
        for memory_id, c_id in git_log.find_closed_by_from_trailers(
            commit_node_id, trailer_targets, memory_nodes_by_name
        ):
            store.add_edge(memory_id, c_id, "closed_by", confidence=1.0)

        # motivated_by edges (this commit -> doc whose name appears
        # in the body). Confidence 0.9 because the name match is
        # explicit but not via a formal trailer.
        for c_id, memory_id in git_log.find_motivated_by_explicit_match(
            commit_node_id,
            entry.body,
            memory_nodes_by_name,
        ):
            store.add_edge(c_id, memory_id, "motivated_by", confidence=0.9)


def register_default_sources(store: Store, claude_home: Path) -> int:
    """Discover and register Scope B sources. Returns count of newly registered."""
    discovered = discover_default_sources(claude_home)
    existing = {s.path for s in store.list_sources()}
    n_new = 0
    for d in discovered:
        if str(d.path) not in existing:
            n_new += 1
        store.register_source(str(d.path), d.kind, project_key=d.project_key)
    return n_new


def _embed(store: Store, node: Node, embedder: object) -> None:
    """Internal hook so ingest.py doesn't depend on mnemo.embed at import time."""
    from mnemo.embed import embed_node

    embed_node(store, node, embedder)  # type: ignore[arg-type]

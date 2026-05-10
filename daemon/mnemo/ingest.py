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

import frontmatter

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
    """Read one file from disk and produce a ParsedFile (no DB access)."""
    if kind not in SOURCE_KINDS:
        raise ValueError(f"unknown source kind: {kind!r}")
    raw_bytes = path.read_bytes()
    file_hash = _hash_bytes(raw_bytes)
    text = raw_bytes.decode("utf-8", errors="replace")
    post = frontmatter.loads(text)
    body = post.content
    fm: dict[str, object] = dict(post.metadata)

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
    )


# --- Scanning --------------------------------------------------------------


def _default_include_for_kind(kind: str) -> list[str]:
    """Default include patterns when a source's ``include`` field is unset.

    memory_dir / plan_dir / transcripts: match the file types ingest
    knows how to parse. Phase 3 (this commit) ships only markdown;
    phase 4 widens this to also include ``**/*.txt`` and ``**/*.pdf``
    once their parsers land.
    claude_md: always matches its single configured file -- no walk.
    """
    if kind in ("memory_dir", "plan_dir", "transcripts"):
        return ["**/*.md"]
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
    exclude_patterns = _parse_pattern_field(source.exclude)
    include_spec = _build_pathspec(include_patterns)
    exclude_spec = _build_pathspec(exclude_patterns)

    for f in sorted(p.rglob("*")):
        if not f.is_file():
            continue
        if f.name == "MEMORY.md":
            continue
        rel = f.relative_to(p).as_posix()
        if include_spec is not None and not include_spec.match_file(rel):
            continue
        if exclude_spec is not None and exclude_spec.match_file(rel):
            continue
        yield parse_file(f, kind=source.kind, project_key=source.project_key)


# --- Reconciliation --------------------------------------------------------


def _path_under_source(node_path: str, src_path: str, src_kind: str) -> bool:
    np = Path(node_path)
    sp = Path(src_path)
    if src_kind == "claude_md":
        return np == sp
    try:
        np.relative_to(sp)
        return True
    except ValueError:
        return False


def reindex(
    store: Store,
    *,
    sources: list[Source] | None = None,
    embedder: object | None = None,
) -> ReindexReport:
    """Reindex enabled sources. Idempotent.

    For each source, parse all files; upsert new and changed nodes; delete
    nodes whose source files have vanished. If ``embedder`` is supplied, also
    re-embed any node that was added or updated. Returns a tally.
    """
    report = ReindexReport()
    src_list = sources if sources is not None else store.list_sources(only_enabled=True)
    seen_paths: set[str] = set()

    for src in src_list:
        try:
            for parsed in scan_source(src):
                seen_paths.add(str(parsed.path))
                existing = store.get_node_by_source(str(parsed.path))
                if existing is None:
                    new_node = Node.new(
                        type=parsed.type,
                        name=parsed.name,
                        body=parsed.body,
                        source_path=str(parsed.path),
                        source_kind=parsed.source_kind,
                        description=parsed.description,
                        project_key=parsed.project_key,
                        frontmatter_json=parsed.frontmatter_json,
                        hash=parsed.hash,
                    )
                    store.upsert_node(new_node)
                    if embedder is not None:
                        _embed(store, new_node, embedder)
                    report.added += 1
                elif existing.hash != parsed.hash:
                    existing.type = parsed.type
                    existing.name = parsed.name
                    existing.description = parsed.description
                    existing.body = parsed.body
                    existing.source_kind = parsed.source_kind
                    existing.project_key = parsed.project_key
                    existing.frontmatter_json = parsed.frontmatter_json
                    existing.hash = parsed.hash
                    existing.updated_at = int(time.time())
                    store.upsert_node(existing)
                    if embedder is not None:
                        _embed(store, existing, embedder)
                    report.updated += 1
                else:
                    report.unchanged += 1
            store.mark_source_indexed(src.path)
        except Exception as exc:  # noqa: BLE001 - we want to keep going on bad files
            report.errors.append((src.path, str(exc)))
            log.warning("error scanning source %s: %s", src.path, exc)

    # Deletions: any node whose source_path falls under a scanned source
    # but wasn't seen this run.
    if src_list:
        all_nodes = store.list_nodes(limit=1_000_000)
        for node in all_nodes:
            for src in src_list:
                if _path_under_source(node.source_path, src.path, src.kind):
                    if node.source_path not in seen_paths:
                        store.delete_node(node.id)
                        report.removed += 1
                    break

    return report


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

"""v2.0 phase 2: auto-router heuristics + dry-run preview + safety ceiling.

This module classifies an unknown path into one of the ``SOURCE_KINDS``
values without writing anything to the DB. The CLI and the UI both
consume :func:`preview` and surface its result to the user; only after
explicit confirmation does the caller hit ``POST /v1/sources``.

Heuristics, in decision order:

1. ``.git/`` directory present AND >= 1 recognized source file
   -> ``code_repo`` (high confidence).
2. >= 1 markdown file with frontmatter ``type:`` field
   -> ``memory_dir`` (high confidence). This rule outranks docs_dir
   so a single typed memory entry in a directory of plain markdowns
   classifies as memory_dir.
3. >= 2 markdown files without frontmatter AND 0 recognized source
   files -> ``docs_dir`` (medium confidence).
4. None of the above -> ``(None, "low")``. The user has to supply
   ``--kind`` explicitly.

Safety: ``SAFETY_CEILING = 50_000`` recognized source files
(after default skip-dirs). The auto-router refuses to write a source
row above this threshold; ``--force`` / ``force=True`` overrides.

The module is deliberately framework-free and side-effect-free: no
imports from ``store``, ``server``, or ``ingest``. That keeps it
trivially testable in isolation and reusable from any future
script that wants to classify a path (e.g. ``mnemo source repair``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

SAFETY_CEILING = 50_000
"""Recognized source-file count above which the auto-router refuses
to register. Caller overrides via ``preview(force=True)``."""


DEFAULT_SKIP_DIRS = frozenset(
    {
        # VCS internals
        ".git",
        ".hg",
        ".svn",
        # Node ecosystem
        "node_modules",
        ".pnp",
        ".pnpm-store",
        ".yarn",
        # Python ecosystem
        "__pycache__",
        ".venv",
        "venv",
        "env",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".pyre",
        # Rust / JVM / .NET build outputs
        "target",
        "build",
        ".gradle",
        "bin",
        "obj",
        # Generic build / cache / IDE
        "dist",
        "out",
        ".next",
        ".nuxt",
        ".turbo",
        ".cache",
        "coverage",
        ".idea",
        ".vscode",
    }
)
"""Directory names that the path walker never descends into.

Recursive: any path with a name in this set ANYWHERE along its
relative path from the source root is skipped. The set is opinionated
toward "build output / dependencies / VCS internals" -- categories
where the file count balloons but the contents aren't user-authored
source. Users override via the per-source ``exclude`` field on
``Source``."""


# v2.6 phase 4: extensions that count as "docs" for the dual-source
# proposal. PDF + .rst are included alongside markdown since docs_dir
# already accepts them (see ingest._default_include_for_kind).
RECOGNIZED_DOC_EXTS = frozenset({".md", ".markdown", ".txt", ".pdf", ".rst"})

RECOGNIZED_SOURCE_EXTS = frozenset(
    {
        # Python
        ".py",
        ".pyi",
        # JavaScript / TypeScript
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        # Go
        ".go",
        # Rust
        ".rs",
        # JVM
        ".java",
        ".kt",
        ".kts",
        ".scala",
        # Ruby
        ".rb",
        # PHP
        ".php",
        # .NET
        ".cs",
        ".vb",
        ".fs",
        # Apple
        ".swift",
        ".m",
        ".mm",
        # C / C++
        ".c",
        ".h",
        ".cpp",
        ".cc",
        ".cxx",
        ".hpp",
        ".hxx",
        # Godot / GDScript
        ".gd",
        # Shell
        ".sh",
        ".bash",
        ".zsh",
        ".ps1",
        # SQL
        ".sql",
    }
)
"""File extensions that count as 'source' for the code_repo heuristic.

A path with >= 1 of these AND a ``.git/`` dir classifies as code_repo.
Markdown / YAML / JSON / TOML deliberately do NOT count -- they're
present in nearly every repo and would make the code_repo rule fire
on directories that are really docs sites or memory dumps."""


@dataclass
class PreviewBreakdown:
    """Per-extension file count for a scanned path.

    ``by_ext`` keys are lowercased extensions including the dot
    (``.py``, ``.md``, ...). ``total_files`` is the sum of the values
    after skip-dirs are applied. The two markdown counts are derived
    during the walk (one extra pass each over the head of the file)
    so the heuristic can distinguish typed memory entries from plain
    docs.
    """

    by_ext: dict[str, int] = field(default_factory=dict)
    total_files: int = 0
    md_with_frontmatter: int = 0
    md_without_frontmatter: int = 0
    has_git: bool = False


@dataclass
class PreviewResult:
    """What the CLI / UI / API surface to the user before confirming.

    The user sees ``proposed_kind`` + ``confidence`` + the file
    breakdown; ``exceeds_safety_ceiling`` is the gate the user must
    explicitly override via ``--force``.
    """

    path: str
    proposed_kind: str | None
    confidence: str  # "high" | "medium" | "low"
    breakdown: PreviewBreakdown
    exceeds_safety_ceiling: bool


# --- Heuristic decision ---------------------------------------------------


def propose_kind(breakdown: PreviewBreakdown) -> tuple[str | None, str]:
    """Apply the kind heuristics to a counted breakdown.

    Side-effect free; pure function of the breakdown. Returns
    ``(kind, confidence)``; ``kind`` may be ``None`` (the user must
    supply ``--kind``).
    """
    src_count = sum(c for ext, c in breakdown.by_ext.items() if ext in RECOGNIZED_SOURCE_EXTS)
    if breakdown.has_git and src_count > 0:
        return "code_repo", "high"
    if breakdown.md_with_frontmatter >= 1:
        return "memory_dir", "high"
    if breakdown.md_without_frontmatter >= 2 and src_count == 0:
        return "docs_dir", "medium"
    return None, "low"


# --- Filesystem walk ------------------------------------------------------


def _has_frontmatter_type(path: Path) -> bool:
    """Quick peek: does the file start with ``---``, a ``type:`` line,
    and another ``---``?

    Reads up to 4 KiB to keep this cheap on a multi-thousand-file walk.
    Returns ``False`` on read errors so a permission failure can't
    accidentally classify a directory as memory_dir.
    """
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:4096]
    except OSError:
        return False
    if not head.startswith("---"):
        return False
    end = head.find("\n---", 3)
    if end == -1:
        return False
    fm_body = head[3:end]
    for line in fm_body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("type:") or stripped.startswith("type :"):
            return True
    return False


def scan_path(
    path: Path | str,
    *,
    skip_dirs: frozenset[str] = DEFAULT_SKIP_DIRS,
) -> PreviewBreakdown:
    """Walk ``path`` and produce a :class:`PreviewBreakdown`.

    Single-file paths produce a one-extension breakdown. Non-existent
    paths produce an empty breakdown (the higher-level :func:`preview`
    raises). Skip-dirs are matched by name anywhere along the
    relative path from ``path``.
    """
    p = Path(path)
    if not p.exists():
        return PreviewBreakdown()

    by_ext: dict[str, int] = {}
    md_fm = 0
    md_no_fm = 0
    has_git = (p / ".git").is_dir() if p.is_dir() else False

    if p.is_file():
        ext = p.suffix.lower()
        by_ext[ext] = 1
        return PreviewBreakdown(
            by_ext=by_ext,
            total_files=1,
            has_git=False,
        )

    for f in p.rglob("*"):
        if not f.is_file():
            continue
        try:
            rel = f.relative_to(p)
        except ValueError:
            continue
        # Skip if any parent dir name is in the skip set. The file's own
        # name is excluded (rel.parts[:-1]) so a file literally named
        # ``.git`` -- unusual but legal -- doesn't get silently dropped.
        if any(part in skip_dirs for part in rel.parts[:-1]):
            continue
        ext = f.suffix.lower()
        by_ext[ext] = by_ext.get(ext, 0) + 1
        if ext in (".md", ".markdown"):
            if _has_frontmatter_type(f):
                md_fm += 1
            else:
                md_no_fm += 1

    return PreviewBreakdown(
        by_ext=by_ext,
        total_files=sum(by_ext.values()),
        md_with_frontmatter=md_fm,
        md_without_frontmatter=md_no_fm,
        has_git=has_git,
    )


# --- High-level entry point -----------------------------------------------


def preview(path: Path | str, *, force: bool = False) -> PreviewResult:
    """Scan ``path``, propose a kind, and flag the safety ceiling.

    Raises ``FileNotFoundError`` if ``path`` doesn't exist -- callers
    surface that as a clear error to the user before any DB write.

    The ``force`` flag suppresses the safety-ceiling check; it does
    NOT affect the heuristic, since classifying a 100k-file repo is
    the same as classifying a 100-file repo.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"path does not exist: {p}")

    breakdown = scan_path(p)
    kind, confidence = propose_kind(breakdown)

    src_count = sum(c for ext, c in breakdown.by_ext.items() if ext in RECOGNIZED_SOURCE_EXTS)
    exceeds = (src_count > SAFETY_CEILING) and not force

    return PreviewResult(
        path=str(p),
        proposed_kind=kind,
        confidence=confidence,
        breakdown=breakdown,
        exceeds_safety_ceiling=exceeds,
    )


# --- v2.6 phase 4: dual-source proposal + .gitignore merge ----------------


# Default threshold above which propose_source emits a 'large_project'
# warning. The user can still register; the UI shows a yellow chip.
DEFAULT_LARGE_PROJECT_THRESHOLD = 6_000

# Minimum file counts that qualify a path for a proposal of the
# corresponding kind. Below these the proposal is suppressed -- it
# would be too small to warrant a separate source registration.
MIN_DOCS_FOR_PROPOSAL = 3
MIN_CODE_FOR_PROPOSAL = 10

# How many sample file paths to include per proposal so the UI can
# show "found these files" without paginating the full breakdown.
SAMPLE_FILE_LIMIT = 10

# Heuristic node-count multiplier for code repos. A typical file emits
# the module node + ~1-2 declarations; round to 2.5 so the UI under-
# promises rather than under-delivering.
CODE_NODES_PER_FILE = 2.5


@dataclass
class SourceProposal:
    """One half of a dual-source proposal -- either docs or code.

    The UI shows the user N proposals; they pick which to register
    (one, both, or neither). ``include_pattern`` becomes the
    ``Source.include`` field on registration.
    """

    kind: str  # 'docs_dir' | 'code_repo'
    include_pattern: str
    include_count: int  # files that would match the include pattern
    est_nodes: int  # rough projected node count (file*multiplier)
    sample: list[str]  # up to SAMPLE_FILE_LIMIT example file paths


@dataclass
class DualProposalResult:
    """Bundle of proposals + gitignore data + warnings.

    Returned by :func:`propose_source` so the server endpoint (phase 5)
    + the add-source UI (phase 8) can render the preview cleanly.
    """

    path: str
    proposals: list[SourceProposal]
    gitignore_excludes: list[str]
    gitignore_files_found: list[str]
    warnings: list[dict[str, str]]


def _read_gitignore_lines(path: Path) -> list[str]:
    """Read a .gitignore-shaped file. Strips comments + blank lines."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    patterns: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        patterns.append(s)
    return patterns


def _gather_gitignores(
    root: Path,
    *,
    skip_dirs: frozenset[str] = DEFAULT_SKIP_DIRS,
) -> tuple[list[str], list[str]]:
    """Find every .gitignore under ``root`` and merge into a flat pattern list.

    Patterns in nested .gitignores are prefixed with the relative
    directory so a ``build/`` in ``src/.gitignore`` becomes
    ``src/build/`` in the merged list. Anchored ('/foo') and negated
    ('!foo') patterns keep their leading character; the prefix is
    inserted right after.

    Returns ``(merged_patterns, gitignore_file_paths_relative_to_root)``.
    """
    if not root.is_dir():
        return [], []

    merged: list[str] = []
    found: list[str] = []
    for f in sorted(root.rglob(".gitignore")):
        if not f.is_file():
            continue
        try:
            rel_dir = f.parent.relative_to(root)
        except ValueError:
            continue
        if any(part in skip_dirs for part in rel_dir.parts):
            continue
        rel_path = f.relative_to(root).as_posix()
        found.append(rel_path)
        prefix = rel_dir.as_posix()
        # The root .gitignore has rel_dir = Path('.') -> as_posix() = '.'.
        # Drop the dot so we get bare patterns at root.
        if prefix == ".":
            prefix = ""
        for pat in _read_gitignore_lines(f):
            if not prefix:
                merged.append(pat)
                continue
            # Preserve leading '!' (negation) and '/' (anchored-to-root)
            if pat.startswith("!"):
                merged.append("!" + prefix + "/" + pat[1:].lstrip("/"))
            elif pat.startswith("/"):
                merged.append(prefix + pat)
            else:
                merged.append(prefix + "/" + pat)
    return merged, found


def merge_gitignore_into_exclude(
    *,
    gitignore_patterns: list[str],
    user_exclude: str | None,
) -> list[str]:
    """Combine .gitignore-derived patterns with a user-supplied exclude string.

    Returns a flat list of patterns. Gitignore patterns come first so
    they win on conflicts (a user negation would later be applied via
    a separate "Refresh .gitignore" workflow on the source edit page).

    Both sides preserve duplicates removal; whitespace is stripped.
    """
    merged: list[str] = []
    seen: set[str] = set()
    for pat in gitignore_patterns:
        s = pat.strip()
        if s and s not in seen:
            merged.append(s)
            seen.add(s)
    if user_exclude:
        for raw in user_exclude.split(","):
            s = raw.strip()
            if s and s not in seen:
                merged.append(s)
                seen.add(s)
    return merged


def propose_source(
    path: Path | str,
    *,
    large_project_threshold: int = DEFAULT_LARGE_PROJECT_THRESHOLD,
    skip_dirs: frozenset[str] = DEFAULT_SKIP_DIRS,
) -> DualProposalResult:
    """Dual-source proposal for the add-source UX.

    Walks ``path`` once, counting code + doc files. Emits a docs_dir
    proposal if >= :data:`MIN_DOCS_FOR_PROPOSAL` doc files are present,
    and a code_repo proposal if >= :data:`MIN_CODE_FOR_PROPOSAL` source
    files are present. BOTH proposals fire when both qualify (common
    case: a code project with a ``docs/`` folder).

    .gitignore files anywhere under ``path`` are read; their patterns
    are prefixed with the relative directory + returned alongside
    ``gitignore_files_found`` so the server can merge them into the
    source's ``exclude`` field on registration.

    Raises ``FileNotFoundError`` if ``path`` doesn't exist -- callers
    surface that to the user as a clear error before any walk.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"path does not exist: {p}")

    doc_files: list[Path] = []
    code_files: list[Path] = []

    if p.is_file():
        # Single-file path is rare in this workflow but handle it
        # gracefully (treat it as docs if its extension fits).
        ext = p.suffix.lower()
        if ext in RECOGNIZED_DOC_EXTS:
            doc_files.append(p)
        elif ext in RECOGNIZED_SOURCE_EXTS:
            code_files.append(p)
    else:
        for f in p.rglob("*"):
            if not f.is_file():
                continue
            try:
                rel = f.relative_to(p)
            except ValueError:
                continue
            if any(part in skip_dirs for part in rel.parts[:-1]):
                continue
            ext = f.suffix.lower()
            if ext in RECOGNIZED_DOC_EXTS:
                doc_files.append(f)
            elif ext in RECOGNIZED_SOURCE_EXTS:
                code_files.append(f)

    proposals: list[SourceProposal] = []
    if len(doc_files) >= MIN_DOCS_FOR_PROPOSAL:
        # docs_dir's default include matches all markdown/txt/pdf;
        # surface a representative pattern + a sample so the UI can
        # render "I found N markdown files in docs/ + README.md".
        proposals.append(
            SourceProposal(
                kind="docs_dir",
                include_pattern="docs/**/*.md,*.md,*.markdown,*.txt,*.pdf,*.rst",
                include_count=len(doc_files),
                est_nodes=len(doc_files),
                sample=[
                    str(f.relative_to(p) if p.is_dir() else f.name)
                    for f in doc_files[:SAMPLE_FILE_LIMIT]
                ],
            )
        )
    if len(code_files) >= MIN_CODE_FOR_PROPOSAL:
        # code_repo carries its own default include via ingest; the
        # pattern surfaced here documents intent and is parsed by the
        # server when the user accepts the proposal.
        proposals.append(
            SourceProposal(
                kind="code_repo",
                include_pattern="src/**,!docs/**",
                include_count=len(code_files),
                est_nodes=int(len(code_files) * CODE_NODES_PER_FILE),
                sample=[
                    str(f.relative_to(p) if p.is_dir() else f.name)
                    for f in code_files[:SAMPLE_FILE_LIMIT]
                ],
            )
        )

    gitignore_patterns, gitignore_files = _gather_gitignores(p, skip_dirs=skip_dirs)

    warnings: list[dict[str, str]] = []
    total_indexable = len(doc_files) + len(code_files)
    if total_indexable > large_project_threshold:
        warnings.append(
            {
                "kind": "large_project",
                "message": (f"{total_indexable:,} indexable files -- may hit workspace cap"),
            }
        )

    return DualProposalResult(
        path=str(p),
        proposals=proposals,
        gitignore_excludes=gitignore_patterns,
        gitignore_files_found=gitignore_files,
        warnings=warnings,
    )

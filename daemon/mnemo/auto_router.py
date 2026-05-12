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

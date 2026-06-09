"""Runtime path resolution for mnemo.

The mnemo daemon stores its database, model cache, and logs under
``~/.claude/mnemo/``. This module is the single source of truth for those
paths and exposes overrides via the ``MNEMO_HOME`` environment variable
(used by tests and by users who want to relocate the runtime dir).
"""

from __future__ import annotations

import os
import re
from pathlib import Path


def claude_home() -> Path:
    """Claude Code config directory: ``~/.claude/``."""
    override = os.environ.get("CLAUDE_HOME")
    return Path(override) if override else Path.home() / ".claude"


def mnemo_home() -> Path:
    """mnemo runtime directory: ``~/.claude/mnemo/`` (overridable via ``MNEMO_HOME``)."""
    override = os.environ.get("MNEMO_HOME")
    return Path(override) if override else claude_home() / "mnemo"


def db_path() -> Path:
    return mnemo_home() / "mnemo.db"


def vec_path() -> Path:
    return mnemo_home() / "mnemo.vec"


def cache_dir() -> Path:
    return mnemo_home() / "cache"


def logs_dir() -> Path:
    return mnemo_home() / "logs"


def sessions_dir() -> Path:
    """Per-session scratch dir (statusline inject counts, keyed by the CC
    session_id). v5.25.0 -- pruned opportunistically, safe to wipe."""
    return mnemo_home() / "sessions"


def pid_file(port: int = 7373) -> Path:
    """Port-scoped pid file. A preview daemon (7399) and the prod
    daemon (7373) MUST NOT share one pid file -- when they did, each
    one's exit ``remove_pid_file()`` orphaned the other (no pid file ->
    ``mnemo daemon stop/status`` went blind -> a stale process kept
    serving old code). 7373 is the canonical default (back-compat)."""
    return mnemo_home() / f"mnemo-{port}.pid"


def grammars_dir() -> Path:
    """v2.0 phase 3: home for lazy-downloaded tree-sitter grammar wheels.

    The launch bundle (Python, TS/TSX, JavaScript, Go, JSON, YAML,
    Markdown) ships as wheel dependencies so first launch works
    offline. Beyond that the user (or future ``mnemo grammar install``
    helper) drops additional language wheels under this directory.

    Today the directory is reserved -- no wheels are downloaded into
    it automatically. The location is a stable surface so subsequent
    phases can fill in the install mechanism without moving the path.
    """
    return mnemo_home() / "grammars"


def ensure_runtime_dirs() -> Path:
    """Create runtime directories if missing. Returns ``mnemo_home``."""
    home = mnemo_home()
    home.mkdir(parents=True, exist_ok=True)
    cache_dir().mkdir(exist_ok=True)
    logs_dir().mkdir(exist_ok=True)
    grammars_dir().mkdir(exist_ok=True)
    return home


# --- Canonical project-key derivation -----------------------------------------
#
# v1.1 introduced multiple clients (Claude Code plugin, VS Code extension,
# SDK middleware) that all need to agree on the project key for a given path
# so memory written by one client surfaces in another.
#
# The transformation is intentionally simple and lossless:
#   1. Treat the input as a string-form absolute path.
#   2. Replace ":" and both path separators with "-". This naturally produces
#      the "D--Repository-foo" double-dash on Windows because the colon AND
#      the backslash both substitute, which we keep -- it's distinguishing.
#   3. Strip leading + trailing "-" (from the leading "/" on POSIX or any
#      trailing separator).
#
# We deliberately do NOT collapse runs of "-" -- the double-dash after a
# Windows drive letter is informative and matches the existing keys already
# in user stores (e.g. "D--Repository-knowledge-base").
#
# We deliberately do NOT lowercase the drive letter -- existing project keys
# preserve whatever case the user typed. Users who want case stability should
# standardize their workspace path themselves.
#
# Adapters (VS Code, middleware) implement the same algorithm in their own
# language; the daemon's ``POST /v1/projects/resolve`` endpoint is the
# canonical source. CI runs each adapter's port against a fixture file
# of (path, expected_key) pairs to detect drift.


def path_under_source(node_path: str, src_path: str, src_kind: str) -> bool:
    """True if ``node_path`` is owned by the source rooted at ``src_path``.

    For ``claude_md`` sources the relationship is exact (a single file).
    For directory-shaped sources (``memory_dir``, ``plan_dir``,
    ``transcripts``, ``code_repo``, ``docs_dir``) the node must be the
    directory itself or a descendant. v2.0 phase 1 adds ``code_repo`` and
    ``docs_dir`` to the directory family; both rely on the same "descendant
    of src_path" semantics so no per-kind branch is needed.

    v2.0 phase 4: ``code_repo`` declaration nodes carry a line-range
    suffix (``<file>:<start>-<end>``) so two same-name functions get
    distinct keys. Strip the suffix before path comparison; the file
    itself is what the source owns, not the line range.

    Used by:
    - ingest reconciliation (sweep nodes whose files vanished from a tracked
      source).
    - source removal cascade (delete all nodes belonging to a source being
      unregistered, so the graph doesn't keep stale entries after the user
      cleans up a misclassified registration).
    """
    np = Path(_strip_line_range(node_path))
    sp = Path(src_path)
    if src_kind == "claude_md":
        return np == sp
    try:
        np.relative_to(sp)
        return True
    except ValueError:
        return False


_LINE_RANGE_RE = re.compile(r":\d+-\d+$")


def _strip_line_range(source_path: str) -> str:
    """Strip the v2.0 ``:<start>-<end>`` suffix from a code declaration
    source_path. The ``:NUM-NUM`` pattern can't appear in real POSIX
    or Windows paths so the pattern match is unambiguous."""
    return _LINE_RANGE_RE.sub("", source_path)


def project_key_from_abs(abs_path: str) -> str:
    """Derive the canonical project key from an already-absolute path string.

    Pure syntactic transformation. Caller is responsible for passing an
    absolute path (use :func:`resolve_project_key` to also resolve symlinks
    + non-existent paths).
    """
    s = abs_path
    for ch in (":", "/", "\\"):
        s = s.replace(ch, "-")
    return s.strip("-")


def resolve_project_key(path: str | Path) -> str:
    """Resolve ``path`` to absolute form, then derive the canonical key.

    If ``path`` is already absolute in EITHER POSIX or Windows form, we
    pass it through without filesystem resolution. This is critical for
    cross-platform adapter compatibility: a POSIX-style path like
    ``/home/alice/repo`` must produce the same key whether the daemon is
    running on Linux, macOS, or Windows. (If we called ``Path.resolve()``
    on Windows, it would prepend the cwd's drive letter and the key
    would diverge.)

    Only relative paths fall through to filesystem resolution, against
    the daemon's working directory.
    """
    s = str(path)
    # Absolute in POSIX form ("/...") OR Windows form ("X:..."). The
    # latter check accepts lone-drive ("D:") and drive-with-separator
    # ("D:\\..." or "D:/...").
    is_posix_abs = s.startswith("/")
    is_win_abs = len(s) >= 2 and s[1] == ":" and s[0].isalpha()
    if is_posix_abs or is_win_abs:
        return project_key_from_abs(s)

    # Relative -- resolve against the daemon's local FS (best effort).
    p = Path(path)
    try:
        p = p.resolve(strict=False)
    except (OSError, RuntimeError):
        p = p.absolute()
    return project_key_from_abs(str(p))

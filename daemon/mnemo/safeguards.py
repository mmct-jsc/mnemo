"""v2.6 file-safety classification heuristics.

Each helper in this module is a **pure function** that takes a path,
content bytes, or both and returns a reason string when the heuristic
fires, or ``None`` when it doesn't. :func:`classify_file` orchestrates
the order so the cheapest checks (extension, size, filename pattern)
run before expensive content-based checks (entropy, repeated lines).

Decisions:

- ``ok``           -- passes every check; ingest should try to parse.
- ``auto_skipped`` -- structural skip (unsupported ext, oversize, missing).
- ``suspicious``   -- one of seven heuristics fired; surfaces in the report
                      for a user decision.

The ``malformed`` category is **not** produced here. It is added by
ingest (phase 3) when the parse layer raises after :func:`classify_file`
has returned ``ok``.

Overrides + gitignore are consulted by ingest **before** calling
:func:`classify_file`; this module is gitignore-blind by design so
the heuristics stay pure functions of (path, content).
"""

from __future__ import annotations

import collections
import math
import re
from dataclasses import dataclass
from pathlib import Path

# --- Constants ---------------------------------------------------------------

# Extensions worth indexing. The set is the union of:
#   * docs:  .md / .markdown / .txt / .pdf / .rst
#   * code:  Python, JS/TS/JSX/TSX, Go, plus their config files
#   * data:  .json / .yaml / .yml (config / docs adjacent to code)
# Files outside this set are auto-skipped without a content read.
DEFAULT_SUPPORTED_EXTS: frozenset[str] = frozenset(
    {
        ".md",
        ".markdown",
        ".txt",
        ".rst",
        ".pdf",
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".go",
        ".json",
        ".jsonc",
        ".yaml",
        ".yml",
        ".toml",
    }
)

# 5 MiB. Matches the Settings.safeguards.oversize_bytes default.
DEFAULT_OVERSIZE_BYTES: int = 5 * 1024 * 1024


# --- Verdict -----------------------------------------------------------------


@dataclass(frozen=True)
class Classification:
    """Result of :func:`classify_file`.

    ``category`` is one of ``'ok' | 'auto_skipped' | 'suspicious'``.
    ``reason`` is None only when ``category == 'ok'``.
    """

    category: str
    reason: str | None


_OK = Classification("ok", None)


def _auto_skipped(reason: str) -> Classification:
    return Classification("auto_skipped", reason)


def _suspicious(reason: str) -> Classification:
    return Classification("suspicious", reason)


# --- Heuristic 1: binary masquerading as text -------------------------------


def is_binary_masquerading_as_text(
    content_bytes: bytes,
    *,
    threshold: float = 0.30,
    sample_size: int = 8192,
) -> str | None:
    """Return ``binary_masquerade:<ratio>`` if too much of the sample is non-printable.

    Treats bytes < 32 (except tab / LF / CR) and 127 (DEL) as
    non-printable. Default 30% threshold catches obvious binaries that
    snuck through an extension whitelist; raise to 0.50 for stricter
    checking or lower to 0.10 to catch text-with-occasional-NUL files.
    """
    if not content_bytes:
        return None
    sample = content_bytes[:sample_size]
    n = len(sample)
    if n == 0:
        return None
    non_printable = sum(1 for b in sample if (b < 32 and b not in (9, 10, 13)) or b == 127)
    ratio = non_printable / n
    if ratio > threshold:
        return f"binary_masquerade:{ratio:.2f}"
    return None


# --- Heuristic 2: looks like a secret ---------------------------------------

# Each pattern fires on the **first 32 KiB** of the file. Patterns are
# tagged so the reason carries the kind (used by the report UI to
# choose an icon / message).
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    (
        "private_key",
        re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ),
    (
        "aws_access_key",
        re.compile(rb"\bAKIA[A-Z0-9]{16}\b"),
    ),
    (
        "jwt_blob",
        re.compile(rb"\beyJ[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
    ),
    (
        "github_token",
        re.compile(rb"\bgh[psoru]_[A-Za-z0-9]{36}\b"),
    ),
    (
        "bearer_secret",
        re.compile(
            rb"(?i)(?:secret|api[_\-]?key|access[_\-]?token|password)"
            rb"\s*[:=]\s*['\"][A-Za-z0-9_\-+/=]{20,}['\"]"
        ),
    ),
)

_DOTENV_ASSIGNMENT = re.compile(rb"^\s*[A-Z][A-Z0-9_]+\s*=", re.MULTILINE)


def looks_like_secret(path: Path, content_bytes: bytes) -> str | None:
    """Return ``suspected_secret:<kind>`` if the file shape looks like a credential."""
    sample = content_bytes[:32_768]
    for kind, pat in _SECRET_PATTERNS:
        if pat.search(sample):
            return f"suspected_secret:{kind}"

    # .env-style: filename hints + at least 3 KEY=VALUE assignments.
    name = path.name.lower()
    looks_like_env_name = (
        name == ".env" or name == "env" or name.startswith(".env.") or name.endswith(".env")
    )
    if looks_like_env_name:
        env_lines = len(_DOTENV_ASSIGNMENT.findall(sample))
        if env_lines >= 3:
            return "suspected_secret:dotenv"
    return None


# --- Heuristic 3: autogenerated header --------------------------------------


_AUTOGEN_MARKERS: tuple[bytes, ...] = (
    b"@generated",
    b"GENERATED FILE",
    b"AUTOGENERATED",
    b"AUTO-GENERATED",
    b"Code generated by",
    b"DO NOT EDIT",
    b"This file is automatically generated",
    b"This file was automatically generated",
)


def has_autogenerated_header(content_bytes: bytes) -> str | None:
    """Return ``'autogenerated'`` if the first 4 KiB carries a known generator marker."""
    head = content_bytes[:4096]
    for marker in _AUTOGEN_MARKERS:
        if marker in head:
            return "autogenerated"
    return None


# --- Heuristic 4: lock / minified / snapshot / bundle / sourcemap -----------


_LOCK_FILE_NAMES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "uv.lock",
        "poetry.lock",
        "pipfile.lock",
        "cargo.lock",
        "go.sum",
        "composer.lock",
        "gemfile.lock",
        "bun.lockb",
    }
)


def is_lock_minified_or_snapshot(path: Path) -> str | None:
    """Return a reason if the filename matches a lock / minified / generated-asset pattern."""
    name = path.name.lower()
    if name in _LOCK_FILE_NAMES:
        return f"lock_file:{name}"
    if name.endswith(".lock") or name.endswith(".lockb"):
        return "lock_file"
    # Minified bundles -- check before plain ".js"/.css extension drop.
    for ext in (".min.js", ".min.css", ".min.mjs", ".min.cjs"):
        if name.endswith(ext):
            return "minified"
    if name.endswith(".snap"):
        return "snapshot"
    for ext in (".bundle.js", ".bundle.css", ".bundle.mjs"):
        if name.endswith(ext):
            return "bundle"
    if name.endswith(".map"):
        return "sourcemap"
    return None


# --- Heuristic 5: high-entropy blob -----------------------------------------


def is_high_entropy_blob(
    content_bytes: bytes,
    *,
    threshold: float = 7.5,
    sample_size: int = 16384,
) -> str | None:
    """Return ``high_entropy:<value>`` if Shannon entropy of the sample exceeds threshold.

    Caps the sample at 16 KiB so a 5 MB file is not slower than the
    smaller heuristics. Skips samples under 256 bytes (entropy estimate
    too noisy on tiny inputs).
    """
    sample = content_bytes[:sample_size]
    n = len(sample)
    if n < 256:
        return None
    counts = collections.Counter(sample)
    entropy = 0.0
    for c in counts.values():
        if c > 0:
            p = c / n
            entropy -= p * math.log2(p)
    if entropy > threshold:
        return f"high_entropy:{entropy:.2f}"
    return None


# --- Heuristic 6: repeated-line bloat ---------------------------------------


def has_repeated_line_bloat(
    content_bytes: bytes,
    file_size: int,
    *,
    ratio_threshold: float = 0.80,
    min_size_bytes: int = 100_000,
    min_lines: int = 100,
) -> str | None:
    """Return ``repeated_line_bloat:<ratio>`` when one line dominates a big file.

    Filters out small files + low-line files since they can be legit
    dense docs. The combination "size + dominance" catches log dumps,
    base64 artefact stores, and repeated-noise files that would otherwise
    embed badly.
    """
    if file_size < min_size_bytes:
        return None
    try:
        text = content_bytes.decode("utf-8", errors="replace")
    except (UnicodeDecodeError, AttributeError):
        return None
    lines = text.splitlines()
    total = len(lines)
    if total < min_lines:
        return None
    counts = collections.Counter(lines)
    most_common = counts.most_common(1)[0][1]
    ratio = most_common / total
    if ratio >= ratio_threshold:
        return f"repeated_line_bloat:{ratio:.2f}"
    return None


# --- Heuristic 7: build-output name -----------------------------------------


_BUILD_OUTPUT_DIRS: frozenset[str] = frozenset(
    {
        "dist",
        "build",
        "out",
        "node_modules",
        "vendor",
        "__pycache__",
        ".next",
        ".nuxt",
        ".cache",
        ".parcel-cache",
        "target",  # rust/maven
        ".pytest_cache",
    }
)

_BUILD_OUTPUT_NAMES: frozenset[str] = frozenset(
    {
        "bundle.js",
        "bundle.css",
        "vendor.js",
        "vendor.css",
        "main.js",  # only when also in a build dir; handled by dir check
    }
)


def is_build_output_name(path: Path) -> str | None:
    """Return a reason if the filename or any path component points at a build dir."""
    # Path component check first -- it catches the common case.
    for part in path.parts:
        if part.lower() in _BUILD_OUTPUT_DIRS:
            return f"build_output_dir:{part.lower()}"
    name = path.name.lower()
    if name in _BUILD_OUTPUT_NAMES:
        return f"build_output:{name}"
    return None


# --- classify_file orchestration --------------------------------------------


def classify_file(
    path: Path,
    *,
    content_bytes: bytes | None = None,
    size_limit_bytes: int = DEFAULT_OVERSIZE_BYTES,
    supported_exts: frozenset[str] | None = None,
    binary_nonprintable_threshold: float = 0.30,
    entropy_blob_threshold: float = 7.5,
    repeated_line_bloat_threshold: float = 0.80,
) -> Classification:
    """Classify a file for the reindex pipeline.

    Order (cheapest first):

    1. Extension whitelist (no I/O).
    2. Stat for size (one syscall).
    3. Filename-based suspicious heuristics (lock/minified, build-output).
    4. Content-based suspicious heuristics (binary, secret, autogen,
       entropy, repeated-line).

    Returns ``Classification('ok', None)`` if every check passes; ingest
    then attempts to parse and turns the verdict into ``'indexed'`` or
    ``'malformed:<...>'`` based on the parse result.

    Overrides + gitignore are checked by ingest before this function
    runs.
    """
    exts = supported_exts if supported_exts is not None else DEFAULT_SUPPORTED_EXTS
    if path.suffix.lower() not in exts:
        return _auto_skipped(f"unsupported_ext:{path.suffix.lower() or '<none>'}")

    try:
        size = path.stat().st_size
    except OSError as exc:
        return _auto_skipped(f"stat_failed:{exc.__class__.__name__}")
    if size > size_limit_bytes:
        return _auto_skipped(f"oversize:{size}")

    # Filename-only heuristics (cheap; no read required)
    if reason := is_lock_minified_or_snapshot(path):
        return _suspicious(reason)
    if reason := is_build_output_name(path):
        return _suspicious(reason)

    if content_bytes is None:
        try:
            content_bytes = path.read_bytes()
        except OSError as exc:
            return _auto_skipped(f"read_failed:{exc.__class__.__name__}")

    # Content-required heuristics
    if reason := is_binary_masquerading_as_text(
        content_bytes, threshold=binary_nonprintable_threshold
    ):
        return _suspicious(reason)
    if reason := looks_like_secret(path, content_bytes):
        return _suspicious(reason)
    if reason := has_autogenerated_header(content_bytes):
        return _suspicious(reason)
    if reason := is_high_entropy_blob(content_bytes, threshold=entropy_blob_threshold):
        return _suspicious(reason)
    if reason := has_repeated_line_bloat(
        content_bytes, size, ratio_threshold=repeated_line_bloat_threshold
    ):
        return _suspicious(reason)

    return _OK

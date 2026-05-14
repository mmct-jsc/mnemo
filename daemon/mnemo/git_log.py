"""v2.3.0 phase 9: git-log ingestion + decision provenance.

Walks ``git log`` per ``code_repo`` source and creates one ``commit``
node per commit (capped at most-recent N, default 10k). Three classes
of provenance edges are auto-wired from each commit's diff + body:

- ``references_function`` (commit -> code_function / code_method /
  code_module it touched) with confidence proportional to the
  fraction of the function's lines the commit changed.
- ``closed_by`` (memory_feedback / plan_doc / memory_project ->
  commit that resolved it), parsed from ``Fixes:`` / ``Closes:`` /
  ``Refs:`` trailers. Confidence 1.0 (deterministic).
- ``motivated_by`` (commit -> memory_feedback / plan_doc /
  memory_project), parsed from word-boundary name matches in the
  commit body. Confidence 0.9 (explicit reference).

A second ``motivated_by`` heuristic (co-temporal + embedding cosine
similarity) is documented in the v2.0 design § 6 but DEFERRED to a
later release -- it requires the embedder to be threaded through this
module and is more invasive than the v2.3.0 cut needs to be.

This module is pure functions + one subprocess wrapper. The
ingestion glue that consumes these helpers lives in
``mnemo.ingest._ingest_git_log_for_source``.

Design source: ``docs/plans/2026-05-11-mnemo-v2.0-design.md`` §6.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from mnemo.store import Node

log = logging.getLogger(__name__)

# Default commit cap per repo. Older commits stay in git itself; if a
# user needs deeper history they bump the cap via a future
# ``mnemo source patch <path> --commit-limit N`` CLI. Today the cap
# is a module constant + an optional ``commit_limit`` keyword that
# callers can override.
DEFAULT_COMMIT_LIMIT = 10_000

# Field separator used in ``git log --pretty=format:...``. We pick
# ``\x1f`` (ASCII Unit Separator, 0x1F) because:
#   1. it never appears in any of the fields we extract (sha /
#      subject / body / email / ts / files_changed),
#   2. it doesn't break Windows ``CreateProcess`` -- ``\x00`` does
#      (``ValueError: embedded null character`` from the cpython
#      subprocess module on win32 when an arg contains NUL).
# Linux + macOS would also accept ``\x00`` here, but Windows is the
# tighter constraint and ``\x1f`` is just as separator-safe.
_FIELD_SEP = "\x1f"

# Per-commit record separator. ``\x1e`` is the ASCII Record
# Separator (0x1E) -- the sibling control char to _FIELD_SEP. Same
# rationale: never appears in commit metadata, safe in subprocess
# args on all platforms.
_RECORD_SEP = "\x1e"

# git log --pretty format string. Order matches the CommitEntry init
# fields below. ``%H`` = full sha. ``%h`` = short sha. ``%s`` =
# subject. ``%b`` = body. ``%aE`` = author email. ``%at`` = author
# timestamp (unix). files_changed is computed from --shortstat.
_PRETTY = f"{_RECORD_SEP}%H{_FIELD_SEP}%h{_FIELD_SEP}%s{_FIELD_SEP}%b{_FIELD_SEP}%aE{_FIELD_SEP}%at"


@dataclass
class CommitEntry:
    """One git commit, parsed for ingestion.

    Fields mirror the design § 6 ``CommitNode`` shape minus the Node
    layer; ``commit_to_node`` lifts a CommitEntry to a proper
    :class:`mnemo.store.Node`.
    """

    sha: str  # full hex
    short_sha: str  # first 7 chars (cheap to compute)
    subject: str  # first line of message
    body: str  # rest of message (may be empty)
    author_email: str
    ts: int  # unix timestamp (author date)
    files_changed: int  # parsed from --shortstat; 0 if unparseable


# --- Subprocess wrappers (only place we shell out) -------------------


def _run_git(args: list[str], cwd: Path) -> str:
    """Run a git command and return its stdout. Raises on non-zero exit."""
    result = subprocess.run(  # noqa: S603 -- git is trusted, args are constants
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed (cwd={cwd}): {result.stderr.strip()}")
    return result.stdout


def walk_commits(
    repo_path: Path | str,
    *,
    limit: int = DEFAULT_COMMIT_LIMIT,
) -> Iterator[CommitEntry]:
    """Walk ``git log`` (newest first), capped at ``limit``.

    Yields one :class:`CommitEntry` per commit. Quietly returns an
    empty iterator if the path is not a git repository (so a
    code_repo source pointed at a non-git tree silently no-ops
    rather than blowing up the whole reindex).
    """
    repo = Path(repo_path)
    if not (repo / ".git").exists() and not (repo / "HEAD").exists():
        # Not a git checkout -- nothing to walk.
        return
    try:
        raw = _run_git(
            [
                "log",
                f"--max-count={limit}",
                "--shortstat",
                f"--pretty=format:{_PRETTY}",
            ],
            cwd=repo,
        )
    except RuntimeError as exc:
        log.warning("git log walk failed for %s: %s", repo, exc)
        return

    # The output is a stream of records prefixed by _RECORD_SEP, each
    # followed by a --shortstat block. Split on the record separator
    # and drop the empty leading element.
    parts = raw.split(_RECORD_SEP)
    for part in parts:
        part = part.strip("\n")
        if not part:
            continue
        # The body field can contain newlines; we put the shortstat
        # block (which lives AFTER the body in --pretty + --shortstat
        # ordering) on its own line. So split on _FIELD_SEP first to
        # peel off sha/short/subject/body-and-stats/email/ts -- wait
        # actually the field order from _PRETTY is:
        #   %H, %h, %s, %b, %aE, %at
        # But --shortstat output gets APPENDED after every commit's
        # formatted line. So the structure of each record is:
        #   <H><sep><h><sep><s><sep><body...><sep><email><sep><ts>\n
        #   <shortstat line>\n
        # The body itself may contain field separators (it shouldn't,
        # we use NUL) so splitting on _FIELD_SEP gives clean fields.
        # The trailing shortstat ends up appended to the ts field
        # with a newline between them.
        fields = part.split(_FIELD_SEP)
        if len(fields) < 6:
            # Malformed line; skip rather than crash the whole walk.
            continue
        full_sha = fields[0].strip()
        short_sha = fields[1].strip()
        subject = fields[2]
        body = fields[3]
        email = fields[4].strip()
        # The 6th field is ts + (optional) shortstat appended on
        # subsequent lines. Pull just the ts number off the front.
        ts_and_stats = fields[5]
        ts_match = re.match(r"\s*(\d+)", ts_and_stats)
        ts = int(ts_match.group(1)) if ts_match else 0
        files_changed = _parse_files_changed(ts_and_stats)

        yield CommitEntry(
            sha=full_sha,
            short_sha=short_sha,
            subject=subject,
            body=body,
            author_email=email,
            ts=ts,
            files_changed=files_changed,
        )


def _parse_files_changed(stats_chunk: str) -> int:
    """Pull the ``N files changed`` count out of a git --shortstat
    line. Returns 0 if unparseable (e.g. for an empty commit).
    """
    m = re.search(r"(\d+)\s+files?\s+changed", stats_chunk)
    if m:
        return int(m.group(1))
    return 0


def show_commit_diff(repo_path: Path | str, sha: str) -> str:
    """Return the unified-zero diff of a commit (stdout of git show).

    Wrapped here so tests can monkeypatch the subprocess call without
    touching the parser logic.
    """
    repo = Path(repo_path)
    try:
        return _run_git(
            [
                "show",
                "--no-color",
                "--unified=0",
                "--no-prefix",
                sha,
            ],
            cwd=repo,
        )
    except RuntimeError as exc:
        log.warning("git show %s failed for %s: %s", sha, repo, exc)
        return ""


# --- Pure parsers ----------------------------------------------------

# Match the ``diff --git`` header line. With ``--no-prefix`` the file
# paths appear unmodified (no ``a/`` ``b/``); the same path appears
# twice (source then dest, identical for non-rename commits).
_DIFF_HEADER = re.compile(r"^diff --git\s+(\S+)\s+(\S+)$")

# Match a unified-zero hunk marker. With ``--unified=0`` git emits
# only the changed lines, no context, so the post-image range
# (``+A,B``) is exactly the set of lines we care about.
# Format: ``@@ -X[,Y] +A[,B] @@`` -- the count fields default to 1
# when omitted.
_HUNK_MARKER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def parse_diff_hunks(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """Parse the output of ``git show --unified=0 --no-prefix <sha>``
    into ``{file_path: [(start_line, end_line), ...]}``.

    Each tuple is a [start, end] line range in the POST-image (i.e. in
    the version after the commit landed). Pure deletions where the
    hunk's +B field is 0 are skipped -- they don't have a post-image
    range and the design's ``references_function`` predicate is "this
    commit touched lines inside this function", which only makes sense
    for lines that exist post-commit.
    """
    out: dict[str, list[tuple[int, int]]] = {}
    current_file: str | None = None
    for line in diff_text.splitlines():
        m = _DIFF_HEADER.match(line)
        if m:
            # The dest path is the post-image. For renames source !=
            # dest, but tracking the post-image is what the line-range
            # join wants.
            current_file = m.group(2)
            out.setdefault(current_file, [])
            continue
        if current_file is None:
            continue
        m = _HUNK_MARKER.match(line)
        if m:
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            if count <= 0:
                # Pure deletion hunk -- no post-image lines to record.
                continue
            end = start + count - 1
            out[current_file].append((start, end))
    # Drop empty entries (files that appeared in a diff header but
    # contributed no post-image hunks -- e.g. pure-delete commits).
    return {f: ranges for f, ranges in out.items() if ranges}


# Trailers list multiple targets separated by commas or whitespace,
# one trailer per line. Accept ``Fixes``, ``Closes``, ``Refs`` keys
# case-insensitively to match git convention + the design's
# ``config.provenance.commit_trailers`` extension point.
_TRAILER_LINE = re.compile(
    r"^\s*(?:fixes|closes|refs)\s*:\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def parse_closed_by_trailers(body: str) -> list[str]:
    """Return a deduped list of trailer targets from a commit body.

    Recognizes ``Fixes:`` / ``Closes:`` / ``Refs:`` lines
    (case-insensitive). The value side is split on commas so a
    single trailer can list multiple targets.
    """
    seen: dict[str, None] = {}
    for m in _TRAILER_LINE.finditer(body):
        for raw in m.group(1).split(","):
            target = raw.strip()
            if not target:
                continue
            if target not in seen:
                seen[target] = None
    return list(seen.keys())


def compute_references_function_edges(
    commit_node_id: str,
    diff_lines: dict[str, list[tuple[int, int]]],
    code_nodes_in_file: dict[str, list[tuple[str, int, int]]],
) -> Iterator[tuple[str, str, float]]:
    """Yield (commit_id, code_node_id, confidence) for each
    overlap between the commit's touched line ranges and a code
    node's [start, end] range in the same file.

    Confidence is the fraction of the code node's lines that the
    commit changed, clamped to [0.3, 1.0]: even a one-line typo fix
    carries a visible weight (per design § 6 ``references_function``
    spec) and a full rewrite caps at 1.0.
    """
    for fpath, touched_ranges in diff_lines.items():
        nodes = code_nodes_in_file.get(fpath, [])
        if not nodes:
            continue
        for nid, n_start, n_end in nodes:
            overlap = 0
            for t_start, t_end in touched_ranges:
                lo = max(t_start, n_start)
                hi = min(t_end, n_end)
                if hi >= lo:
                    overlap += hi - lo + 1
            if overlap == 0:
                continue
            n_total = max(1, n_end - n_start + 1)
            ratio = overlap / n_total
            confidence = max(0.3, min(1.0, ratio))
            yield (commit_node_id, nid, confidence)


def find_motivated_by_explicit_match(
    commit_node_id: str,
    commit_body: str,
    memory_nodes_by_name: dict[str, str],
) -> Iterator[tuple[str, str]]:
    """Yield (commit_id, memory_id) for every memory node whose
    name appears in the commit body as a word-bounded token.

    Used for the ``motivated_by`` confidence-0.9 heuristic. The
    co-temporal embedding heuristic (confidence 0.6) is deferred to
    a future release.
    """
    if not commit_body or not memory_nodes_by_name:
        return
    for name, mid in memory_nodes_by_name.items():
        # ``\b`` doesn't fire at underscore-letter boundaries (the
        # word class includes underscores), so a name like
        # ``feedback_auth`` won't false-match against
        # ``feedback_auth_flake``. We anchor with a manual boundary
        # that requires whitespace / start-of-line / punctuation
        # before AND after.
        pattern = re.compile(
            r"(?:^|[\s\W])" + re.escape(name) + r"(?=$|[\s\W])",
        )
        if pattern.search(commit_body):
            yield (commit_node_id, mid)


def find_closed_by_from_trailers(
    commit_node_id: str,
    trailer_targets: list[str],
    memory_nodes_by_name: dict[str, str],
) -> Iterator[tuple[str, str]]:
    """For each trailer target string, look up the memory node by
    name and yield ``(memory_id, commit_id)`` -- i.e. the edge
    points FROM the resolved memory doc TO the commit that closed
    it. Confidence is 1.0 (deterministic).

    Targets the trailer parser didn't resolve to a known memory node
    are silently dropped (the commit might reference a doc that
    isn't indexed yet, or an external issue tracker).
    """
    if not trailer_targets:
        return
    for target in trailer_targets:
        mid = memory_nodes_by_name.get(target)
        if mid is not None:
            yield (mid, commit_node_id)


# --- Node construction ----------------------------------------------


def commit_to_node(
    entry: CommitEntry,
    *,
    repo_path: Path | str,
    project_key: str | None = None,
) -> Node:
    """Lift a :class:`CommitEntry` into a :class:`mnemo.store.Node`
    of type ``commit``.

    The ``source_path`` is ``<repo_path>@<full_sha>`` so reindexing
    the same repo a second time is idempotent (the existing-by-source
    lookup in ``ingest.reindex_events`` updates rather than
    duplicates).

    Frontmatter carries the structured fields the retrieval layer
    sorts + filters on (ts for "most-recent touched", files_changed
    for repo-impact scoring, sha for cross-reference).
    """
    name = f"{entry.short_sha} {entry.subject[:80]}"
    description = f"{entry.author_email} {entry.ts} {entry.subject}"
    body = entry.subject if not entry.body else f"{entry.subject}\n\n{entry.body}"
    frontmatter = {
        "sha": entry.sha,
        "short_sha": entry.short_sha,
        "author_email": entry.author_email,
        "ts": entry.ts,
        "files_changed": entry.files_changed,
    }
    return Node.new(
        type="commit",
        name=name,
        description=description,
        body=body,
        source_path=f"{repo_path}@{entry.sha}",
        source_kind="code_repo",
        project_key=project_key,
        frontmatter_json=json.dumps(frontmatter, sort_keys=True),
    )

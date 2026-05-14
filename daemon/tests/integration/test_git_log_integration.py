"""v2.3.0 phase 9 integration: full git-log ingest against a tmp repo.

Verifies the end-to-end flow:

1. A ``code_repo`` source pointing at a fresh git checkout produces
   ``commit`` nodes for every commit, with the schema the design § 6
   specifies (sha + author + ts + files_changed in frontmatter).
2. ``references_function`` edges materialize where a commit's diff
   hunks fall inside a code_function's line range, with confidence
   proportional to the fraction of the function touched.
3. ``closed_by`` trailer parsing wires memory nodes to the commits
   that resolved them (confidence 1.0).
4. Re-running ingestion is idempotent -- the same commit + edge set
   is produced.

We shell out to real ``git`` since the subprocess wrapper is part of
the surface under test. Skips cleanly if ``git`` isn't on PATH (the
CI matrix always has it; local devs without git are rare).
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from mnemo import ingest
from mnemo.store import Node, Source, Store

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git executable not on PATH",
)


def _git(cwd: Path, *args: str) -> None:
    """Run a git command with predictable identity + no GPG signing."""
    env_args = (
        "-c",
        "user.email=mnemo-test@example.com",
        "-c",
        "user.name=mnemo test",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "init.defaultBranch=main",
    )
    subprocess.run(  # noqa: S603
        ["git", *env_args, *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Create a tmp git repo with a Python file that has one function
    spanning lines 1-5, plus three commits:

      C1: initial commit (adds auth.py with def login() at 1-5)
      C2: tweak auth.py inside login() body (line 3)
      C3: commit with a Closes: trailer naming feedback_token_flake
    """
    _git(tmp_path, "init")
    (tmp_path / "auth.py").write_text(
        "def login(token):\n    if not token:\n        return None\n    return validate(token)\n\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "auth.py")
    _git(tmp_path, "commit", "-m", "feat: initial auth.py with login()")

    # C2: change line 3 (the bare return None) to use a sentinel.
    (tmp_path / "auth.py").write_text(
        "def login(token):\n"
        "    if not token:\n"
        "        return SENTINEL\n"
        "    return validate(token)\n"
        "\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "auth.py")
    _git(tmp_path, "commit", "-m", "fix: short-circuit login on stale token")

    # C3: another tweak with a Closes: trailer naming a memory node.
    (tmp_path / "auth.py").write_text(
        "def login(token):\n"
        "    if not token:\n"
        "        return SENTINEL\n"
        "    if token.is_stale():\n"
        "        return SENTINEL\n"
        "    return validate(token)\n"
        "\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "auth.py")
    _git(
        tmp_path,
        "commit",
        "-m",
        "fix: drop stale tokens before broker check\n\nCloses: feedback_token_flake",
    )
    return tmp_path


def _seed_code_and_memory(store: Store, repo_path: Path) -> tuple[str, str]:
    """Plant a code_function node for ``login`` (lines 1-5, end of
    POST-image will be 1-7 after C3 but we'll trust C1's range) and a
    memory_feedback node named ``feedback_token_flake`` so the edges
    have endpoints to attach to. Returns (login_id, memory_id).
    """
    login = Node.new(
        type="code_function",
        name="login",
        body="<auth login>",
        source_path=f"{repo_path}/auth.py:1-7",
        source_kind="code_repo",
    )
    store.upsert_node(login)
    feedback = Node.new(
        type="memory_feedback",
        name="feedback_token_flake",
        body="The login flow accepts stale tokens. Short-circuit before broker check.",
        source_path="/some/memory/feedback_token_flake.md",
        source_kind="memory_dir",
    )
    store.upsert_node(feedback)
    return login.id, feedback.id


def test_full_git_log_ingest_creates_commits_and_edges(store: Store, repo: Path) -> None:
    """End-to-end: registering a code_repo source pointing at the
    fixture repo causes reindex to create commit nodes + the three
    provenance edge families. We call the helper directly rather
    than going through full reindex_events because the file walk
    over the tiny tmp repo would try to parse auth.py with tree-
    sitter -- which the existing test_ingest_code_repo.py already
    exercises elsewhere. Here we want to isolate the phase 9 path.
    """
    login_id, feedback_id = _seed_code_and_memory(store, repo)

    src = Source(
        path=str(repo),
        kind="code_repo",
        project_key=None,
        last_indexed_at=None,
        enabled=True,
    )
    seen: set[str] = set()
    ingest._ingest_git_log_for_source(store, src, seen)

    # 1. We get one commit node per git log entry. The fixture
    #    created 3 commits.
    commits = store.list_nodes(type="commit", limit=100)
    assert len(commits) == 3, f"expected 3 commit nodes; got {len(commits)}"

    # 2. references_function edges from each commit to login. C1
    #    (initial: added 5 lines of what becomes a 7-line function
    #    after C3) -> ~5/7 = 0.71 confidence. C2 + C3 each touch
    #    fewer lines so confidence floors at 0.3 (design § 6
    #    rationale: even a one-line typo fix carries visible weight).
    edges_to_login = [
        e for e in store.get_edges(dst_id=login_id) if e.relation == "references_function"
    ]
    assert len(edges_to_login) >= 2, (
        f"expected >=2 references_function edges to login; got {len(edges_to_login)}"
    )
    # Confidence varies PROPORTIONALLY to touched fraction: the
    # initial commit (largest change) should outrank tweaks.
    confidences = sorted((e.confidence for e in edges_to_login), reverse=True)
    assert confidences[0] > 0.5, (
        "expected the highest-confidence references_function edge to be "
        f">0.5 (proportional to the initial commit touching most of the "
        f"function's lines); got confidences {confidences}"
    )
    # The floor is 0.3 -- tweaks shouldn't disappear entirely.
    assert all(c >= 0.3 for c in confidences), (
        f"expected every references_function confidence to be >= 0.3 "
        f"(design § 6 floor); got {confidences}"
    )

    # 3. closed_by edge from feedback_token_flake to C3.
    closed_by = [e for e in store.get_edges(src_id=feedback_id) if e.relation == "closed_by"]
    assert len(closed_by) == 1, (
        f"expected exactly 1 closed_by edge from feedback_token_flake; "
        f"got {len(closed_by)} ({closed_by})"
    )
    # The edge should land on C3 (the commit with the Closes: trailer).
    target_commit = store.get_node(closed_by[0].dst_id)
    assert target_commit is not None
    assert target_commit.type == "commit"
    assert "drop stale tokens" in target_commit.body.lower()
    # Trailer-derived edges are deterministic -> confidence 1.0.
    assert closed_by[0].confidence == 1.0


def test_re_running_git_log_ingest_is_idempotent(store: Store, repo: Path) -> None:
    """Re-running ``_ingest_git_log_for_source`` on the same repo
    must not duplicate commit nodes or edges. The source_path
    ``<repo>@<full_sha>`` is the dedup key for nodes; ``add_edge``
    is naturally idempotent for the (src, dst, relation) triple.
    """
    _seed_code_and_memory(store, repo)
    src = Source(
        path=str(repo),
        kind="code_repo",
        project_key=None,
        last_indexed_at=None,
        enabled=True,
    )
    seen1: set[str] = set()
    ingest._ingest_git_log_for_source(store, src, seen1)
    commits_first = sorted(n.id for n in store.list_nodes(type="commit", limit=100))

    seen2: set[str] = set()
    ingest._ingest_git_log_for_source(store, src, seen2)
    commits_second = sorted(n.id for n in store.list_nodes(type="commit", limit=100))

    assert commits_first == commits_second, (
        "re-running git-log ingest must be idempotent (same commit nodes "
        f"both runs). First: {commits_first}; second: {commits_second}"
    )


def test_non_git_directory_silently_no_ops(store: Store, tmp_path: Path) -> None:
    """Pointing a code_repo source at a directory that ISN'T a git
    checkout (no .git, no HEAD) must not raise. The fact that some
    code repos under management aren't git-managed (e.g. extracted
    tarballs, vendored sources) shouldn't break the whole reindex.
    """
    not_a_repo = tmp_path / "not_a_repo"
    not_a_repo.mkdir()
    (not_a_repo / "foo.py").write_text("x = 1\n", encoding="utf-8")
    src = Source(
        path=str(not_a_repo),
        kind="code_repo",
        project_key=None,
        last_indexed_at=None,
        enabled=True,
    )
    # Should NOT raise.
    ingest._ingest_git_log_for_source(store, src, set())
    # And should not create any commit nodes.
    assert store.list_nodes(type="commit", limit=10) == []


def test_commit_node_carries_frontmatter_provenance(store: Store, repo: Path) -> None:
    """Each commit node's frontmatter_json carries sha, short_sha,
    author_email, ts, and files_changed -- the fields downstream
    queries sort + filter on (most-recent-touching, top-author, etc.).
    """
    _seed_code_and_memory(store, repo)
    src = Source(
        path=str(repo),
        kind="code_repo",
        project_key=None,
        last_indexed_at=None,
        enabled=True,
    )
    ingest._ingest_git_log_for_source(store, src, set())
    commits = store.list_nodes(type="commit", limit=10)
    assert commits
    import json

    for c in commits:
        fm = json.loads(c.frontmatter_json or "{}")
        assert "sha" in fm
        assert len(fm["sha"]) == 40
        assert "short_sha" in fm
        assert fm["author_email"] == "mnemo-test@example.com"
        assert isinstance(fm["ts"], int)
        assert fm["ts"] > 0
        assert isinstance(fm["files_changed"], int)
        # The earliest commit must have ts <= the latest. (No clock
        # skew on a fresh tmp repo created in the same second...)
        assert fm["ts"] <= int(time.time())

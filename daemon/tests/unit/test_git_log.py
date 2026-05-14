"""v2.3.0 phase 9: git-log ingestion + decision-provenance edges.

The v2.0 design (``docs/plans/2026-05-11-mnemo-v2.0-design.md`` §6)
specifies that every ``code_repo`` source gains an automatic
sub-ingest that walks ``git log`` and creates:

- one ``commit`` node per commit (capped at most-recent N, default 10k)
- ``references_function`` edges (commit -> code_function/_method/_module
  it touched, with confidence proportional to the fraction of the
  function's lines the commit changed)
- ``closed_by`` edges (memory_feedback / plan_doc -> commit) from
  ``Fixes:`` / ``Closes:`` / ``Refs:`` commit trailers (confidence 1.0)
- ``motivated_by`` edges (commit -> memory_feedback / plan_doc /
  memory_project) from explicit name matches in the commit body
  (confidence 0.9). The co-temporal embedding heuristic is deferred.

This file is the UNIT test surface -- pure functions only, no
subprocess, no real git repo. The integration test that exercises the
subprocess + real git layer lives in
``daemon/tests/integration/test_git_log_integration.py``.
"""

from __future__ import annotations

# All the symbols we expect ``daemon/mnemo/git_log.py`` to export.
# The first import call is the literal first RED -- the module does
# not exist yet.
from mnemo import git_log

# --- CommitEntry shape -----------------------------------------------


def test_commit_entry_dataclass_exists() -> None:
    """``git_log.CommitEntry`` must expose the fields the rest of the
    pipeline + the commit Node body / frontmatter consume:
    sha, short_sha, subject, body, author_email, ts, files_changed.
    """
    entry = git_log.CommitEntry(
        sha="a1b2c3d4e5f60708091011121314151617181920",
        short_sha="a1b2c3d",
        subject="feat: do the thing",
        body="Body of the commit\nwith multiple lines.",
        author_email="alice@example.com",
        ts=1_700_000_000,
        files_changed=3,
    )
    assert entry.sha.startswith("a1b2c3d")
    assert entry.short_sha == "a1b2c3d"
    assert "feat:" in entry.subject
    assert entry.body.startswith("Body")
    assert "@" in entry.author_email
    assert entry.ts > 0
    assert entry.files_changed == 3


# --- parse_diff_hunks: extract touched line ranges per file ----------


def test_parse_diff_hunks_unified_zero_format() -> None:
    """``parse_diff_hunks`` accepts the output of
    ``git show --no-color --unified=0 --no-prefix <sha>`` and returns
    ``{file_path: [(start_line, end_line), ...]}`` for each file
    touched. Unified=0 means hunks have NO context lines, so the
    ``@@ -X,Y +A,B @@`` markers give us the exact post-image range.
    """
    diff = """\
commit a1b2c3d4
Author: Alice
Date:   Wed Mar 8 12:34:56 2023 +0000

    feat: do the thing

diff --git auth.py auth.py
index 1111..2222 100644
--- auth.py
+++ auth.py
@@ -10,0 +11,3 @@
+    if token_is_stale(token):
+        return short_circuit()
+    # check broker
@@ -42,2 +46,1 @@
-    deprecated_call_one()
-    deprecated_call_two()
+    new_call()
diff --git mqtt_client.py mqtt_client.py
index aaaa..bbbb 100644
--- mqtt_client.py
+++ mqtt_client.py
@@ -3,0 +4,1 @@
+import asyncio
"""
    result = git_log.parse_diff_hunks(diff)
    # auth.py was touched at lines 11-13 (insert of 3) and at line 46 (post-image)
    assert "auth.py" in result
    auth_ranges = result["auth.py"]
    # We expect ranges that overlap the changed regions.
    assert any(11 <= start <= 13 and 11 <= end <= 13 for (start, end) in auth_ranges), (
        f"expected an auth.py range covering 11-13 in {auth_ranges}"
    )
    assert any(start <= 46 <= end for (start, end) in auth_ranges), (
        f"expected an auth.py range covering line 46 in {auth_ranges}"
    )
    # mqtt_client.py touched at line 4
    assert "mqtt_client.py" in result
    assert any(start <= 4 <= end for (start, end) in result["mqtt_client.py"])


def test_parse_diff_hunks_no_diffs_returns_empty() -> None:
    """A commit message with no diff body (e.g. an empty
    ``git show`` for a merge commit with ``--no-merges`` filter or a
    pure metadata commit) returns {}.
    """
    diff = """\
commit a1b2c3d4
Author: Alice

    chore: tag bump only
"""
    assert git_log.parse_diff_hunks(diff) == {}


# --- parse_closed_by_trailers ----------------------------------------


def test_parse_closed_by_trailers_recognizes_three_keywords() -> None:
    """``Fixes:``, ``Closes:``, and ``Refs:`` lines (case-insensitive)
    list the targets a commit explicitly resolves. The parser returns
    a deduped list of target strings.
    """
    body = """\
fix: short-circuit login on stale token

Closes: feedback_mqtt_auth_flake
fixes: plan/2026-04-10-auth-retro.md
Refs: feedback_token_expiry_edge_case
"""
    targets = git_log.parse_closed_by_trailers(body)
    assert "feedback_mqtt_auth_flake" in targets
    assert "plan/2026-04-10-auth-retro.md" in targets
    assert "feedback_token_expiry_edge_case" in targets


def test_parse_closed_by_trailers_no_trailers_returns_empty() -> None:
    """A body with no trailer lines returns an empty list."""
    body = "fix: some unrelated cleanup with no explicit doc reference."
    assert git_log.parse_closed_by_trailers(body) == []


# --- compute_references_function_edges -------------------------------


def test_references_function_overlap_computes_confidence() -> None:
    """For each (commit, code_node) pair where the commit's touched
    lines overlap the code node's [start, end] range, the function
    yields (commit_id, node_id, confidence). Confidence is the
    fraction of the code node's lines the commit changed, clamped to
    [0.3, 1.0] so even a one-line typo fix carries a visible weight
    (per the design § 6 ``references_function`` spec).
    """
    # commit touched auth.py lines 11-13 (3 lines)
    diff_lines = {"auth.py": [(11, 13)]}
    # auth.py has a code_function ``login`` spanning lines 10-30 (21 lines)
    # and ``logout`` spanning lines 40-50 (11 lines).
    code_nodes_in_file = {
        "auth.py": [
            ("login_id", 10, 30),
            ("logout_id", 40, 50),
        ],
    }
    edges = list(
        git_log.compute_references_function_edges(
            "commit_id",
            diff_lines,
            code_nodes_in_file,
        )
    )
    # Only ``login`` overlaps -- ``logout`` is untouched.
    assert len(edges) == 1, f"expected 1 edge for login; got {edges}"
    src_id, dst_id, confidence = edges[0]
    assert src_id == "commit_id"
    assert dst_id == "login_id"
    # 3 of 21 lines = 14% -> floor at 0.3
    assert 0.29 <= confidence <= 0.35


def test_references_function_full_rewrite_confidence_caps_at_one() -> None:
    """A commit that touches every line in a function gets confidence
    1.0 -- the wholesale-rewrite case from the design doc.
    """
    diff_lines = {"f.py": [(10, 30)]}
    code_nodes_in_file = {"f.py": [("fid", 10, 30)]}
    edges = list(
        git_log.compute_references_function_edges("commit_id", diff_lines, code_nodes_in_file)
    )
    assert len(edges) == 1
    _, _, confidence = edges[0]
    assert confidence == 1.0


def test_references_function_zero_overlap_no_edge() -> None:
    """A commit that touches the file but not within any code node's
    line range produces no edge (the diff might be entirely in
    top-level constants or whitespace lines outside any function).
    """
    diff_lines = {"f.py": [(1, 5)]}
    code_nodes_in_file = {"f.py": [("fid", 10, 30)]}
    edges = list(
        git_log.compute_references_function_edges("commit_id", diff_lines, code_nodes_in_file)
    )
    assert edges == []


# --- find_motivated_by_explicit_match -------------------------------


def test_motivated_by_matches_memory_name_in_body() -> None:
    """If the commit body explicitly mentions a memory node's name
    (e.g. ``feedback_mqtt_auth_flake``), emit a motivated_by edge
    from commit to that memory node.
    """
    body = (
        "fix: short-circuit login on stale token\n\n"
        "See feedback_mqtt_auth_flake for the retro discussion."
    )
    memory_names = {
        "feedback_mqtt_auth_flake": "mem_abc",
        "feedback_unrelated": "mem_xyz",
    }
    edges = list(git_log.find_motivated_by_explicit_match("c1", body, memory_names))
    # Only the explicitly-mentioned doc gets an edge.
    assert len(edges) == 1
    src, dst = edges[0]
    assert src == "c1"
    assert dst == "mem_abc"


def test_motivated_by_word_boundary_avoids_substring_match() -> None:
    """``feedback_auth`` must not match against ``feedback_auth_flake``.
    Use word boundaries so the parser doesn't fabricate links.
    """
    body = "Refers to feedback_auth_flake_extended only."
    memory_names = {"feedback_auth": "mem_short"}
    edges = list(git_log.find_motivated_by_explicit_match("c1", body, memory_names))
    assert edges == []


# --- find_closed_by_from_trailers -----------------------------------


def test_closed_by_emits_doc_to_commit_direction() -> None:
    """``closed_by`` edges point FROM the resolved doc TO the commit
    that closed it (memory_feedback -> commit). The trailers in the
    commit body name the doc; the function flips the direction so
    the edge represents "this feedback was closed by this commit".
    """
    targets = ["feedback_mqtt_auth_flake"]
    memory_names = {
        "feedback_mqtt_auth_flake": "mem_abc",
        "feedback_other": "mem_xyz",
    }
    edges = list(git_log.find_closed_by_from_trailers("c1", targets, memory_names))
    assert len(edges) == 1
    src, dst = edges[0]
    # FROM the memory node TO the commit:
    assert src == "mem_abc"
    assert dst == "c1"


def test_closed_by_skips_unknown_trailer_targets() -> None:
    """A trailer that references a doc the store doesn't know about
    is silently skipped -- no error, no broken edge.
    """
    targets = ["feedback_does_not_exist"]
    memory_names = {"feedback_real": "mem_real"}
    assert list(git_log.find_closed_by_from_trailers("c1", targets, memory_names)) == []


# --- commit_to_node --------------------------------------------------


def test_commit_to_node_builds_a_valid_commit_node() -> None:
    """``commit_to_node`` turns a CommitEntry into a Node with
    type=``commit``, descriptive name + body, and the sha / author
    / ts / files_changed stamped into frontmatter_json so retrieval
    can sort + filter on them.
    """
    entry = git_log.CommitEntry(
        sha="a1b2c3d4e5f60708091011121314151617181920",
        short_sha="a1b2c3d",
        subject="feat: do the thing",
        body="Detailed body.\nMultiple lines.",
        author_email="alice@example.com",
        ts=1_700_000_000,
        files_changed=3,
    )
    node = git_log.commit_to_node(entry, repo_path="/path/to/repo")
    assert node.type == "commit"
    assert "a1b2c3d" in node.name
    assert "feat:" in node.name
    # source_path uniquely identifies this commit in this repo so
    # reindex idempotency works (re-running git_log_ingest with the
    # same data is a no-op).
    assert node.source_path == "/path/to/repo@a1b2c3d4e5f60708091011121314151617181920"
    assert node.source_kind == "code_repo"
    # The frontmatter must carry the sha + ts + files_changed for
    # downstream queries (``most recent commit touching X`` sorts on
    # ts) without re-parsing the body.
    import json

    fm = json.loads(node.frontmatter_json)
    assert fm["sha"].startswith("a1b2c3d")
    assert fm["author_email"] == "alice@example.com"
    assert fm["ts"] == 1_700_000_000
    assert fm["files_changed"] == 3

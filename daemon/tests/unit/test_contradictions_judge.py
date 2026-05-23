"""v5.13.0 -- opt-in LLM judge for contradictions detection.

The judge escalates deterministic candidate pairs (from
``detect_contradictions``) to Claude for a binary
contradiction-or-not decision. Mirror of v5.11.0's bench-side
LLM judge pattern (opt-in, env-flag gated, graceful fallback) but
in a SIBLING module rather than reused from bench (the
contradiction grader is a binary classifier with rationale; the
bench judge is multi-criterion rubric).

Contract this test file locks:

1. ``judge_from_env()`` returns ``None`` when env flag is unset
   OR the API key is missing OR the anthropic package isn't
   installed.
2. ``judge_from_env()`` returns an ``LLMContradictionJudge``
   instance only when ALL THREE are present.
3. ``LLMContradictionJudge.judge(a_body, b_body)`` returns
   ``True | False`` from a parsed JSON response.
4. Parse failures degrade to ``False`` (the pair stays a
   deterministic 'candidate'; no false-positive 'high' flag).
5. With the judge enabled in ``analyze(...)``, confirmed pairs
   get severity ``high``; rejected pairs are dropped from the
   result list.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

# --- judge_from_env() opt-in gate --------------------------------------


def test_judge_from_env_returns_none_by_default() -> None:
    """No env flag set -> no judge. The deterministic path stays
    intact + free."""
    from mnemo.analyzer import judge_from_env

    # Ensure both env vars are absent in this test process WITHOUT
    # wiping HOME (anthropic.Anthropic() needs it to expand ~).
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MNEMO_ANALYZE_LLM_JUDGE", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        assert judge_from_env() is None


def test_judge_from_env_requires_both_flag_and_api_key() -> None:
    """Flag alone or key alone -> None. Both required to opt in."""
    from mnemo.analyzer import judge_from_env

    # Flag alone (key absent).
    with patch.dict(os.environ, {"MNEMO_ANALYZE_LLM_JUDGE": "1"}, clear=False):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        assert judge_from_env() is None, "flag alone should not enable the judge"

    # Key alone (flag absent).
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}, clear=False):
        os.environ.pop("MNEMO_ANALYZE_LLM_JUDGE", None)
        assert judge_from_env() is None, "key alone should not enable the judge"


def test_judge_from_env_returns_instance_when_all_present() -> None:
    """Both flag + key set AND anthropic package importable -> an
    LLMContradictionJudge instance.

    If the anthropic package isn't installed in the test env, the
    helper degrades to None (CI-friendly)."""
    from mnemo.analyzer import judge_from_env

    anthropic_installed = False
    try:
        import anthropic  # noqa: F401

        anthropic_installed = True
    except ImportError:
        pass

    # Don't clear -- preserve HOME so anthropic.Anthropic() doesn't
    # raise on user-config-dir expansion.
    with patch.dict(
        os.environ,
        {"MNEMO_ANALYZE_LLM_JUDGE": "1", "ANTHROPIC_API_KEY": "sk-test"},
        clear=False,
    ):
        j = judge_from_env()
        if anthropic_installed:
            assert j is not None, "flag + key + anthropic installed -> instance"
            assert hasattr(j, "judge"), (
                "LLMContradictionJudge must expose a 'judge(a_body, b_body)' method"
            )
        else:
            assert j is None, "no anthropic package -> graceful None"


# --- LLMContradictionJudge.judge() shape -------------------------------


def test_judge_returns_true_on_confirmed_contradiction() -> None:
    """The judge returns True for a clear 'use X' vs 'do not use X'
    pair. We mock the Anthropic client to bypass the network."""
    from mnemo.analyzer import LLMContradictionJudge

    fake_response = MagicMock()
    fake_response.content = [
        MagicMock(
            text='{"contradiction": true, "rationale": "one prescribes X; the other forbids X"}'
        )
    ]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    j = LLMContradictionJudge(client=fake_client, model="claude-sonnet-4-6")
    result = j.judge(
        a_body="Use Redis for caching.",
        b_body="Do not add Redis; we removed it.",
    )
    assert result is True


def test_judge_returns_false_on_rejected_pair() -> None:
    """For a non-contradiction (just two unrelated prescriptions in
    similar topic), the judge returns False."""
    from mnemo.analyzer import LLMContradictionJudge

    fake_response = MagicMock()
    fake_response.content = [
        MagicMock(text='{"contradiction": false, "rationale": "different scopes; not in conflict"}')
    ]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    j = LLMContradictionJudge(client=fake_client, model="claude-sonnet-4-6")
    result = j.judge(a_body="Use X.", b_body="Also use Y.")
    assert result is False


def test_judge_returns_false_on_parse_error() -> None:
    """A garbled response from the model degrades to False -- the
    pair stays a 'candidate' (not 'high'); no false-positive."""
    from mnemo.analyzer import LLMContradictionJudge

    fake_response = MagicMock()
    fake_response.content = [MagicMock(text="not-valid-json")]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    j = LLMContradictionJudge(client=fake_client, model="claude-sonnet-4-6")
    result = j.judge(a_body="A", b_body="B")
    assert result is False


def test_judge_returns_false_on_client_exception() -> None:
    """Network errors / SDK errors -> False (graceful)."""
    from mnemo.analyzer import LLMContradictionJudge

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("network down")

    j = LLMContradictionJudge(client=fake_client, model="claude-sonnet-4-6")
    result = j.judge(a_body="A", b_body="B")
    assert result is False


# --- analyze() with judge wires through --------------------------------


def test_analyze_with_judge_elevates_confirmed_to_high() -> None:
    """When ``analyze(judge=...)`` is called, confirmed candidates
    get severity 'high'; rejected ones disappear from the result."""
    # Use a tmp store + the standard fake embedder pattern.
    import tempfile

    from mnemo.analyzer import analyze
    from mnemo.store import Node, Store

    tmpdir = tempfile.mkdtemp()
    store = Store(f"{tmpdir}/mnemo.db")
    try:

        class _FakeEmbedder:
            dim = 384

            def embed_text(self, text):
                sig = (text or "")[:32].lower()
                base = [0.0] * 384
                for i, ch in enumerate(sig):
                    base[i % 384] += ord(ch) / 1000.0
                norm = sum(x * x for x in base) ** 0.5 or 1.0
                return [x / norm for x in base]

            def embed_batch(self, texts):
                return [self.embed_text(t) for t in texts]

        embedder = _FakeEmbedder()

        import time

        now = int(time.time())
        for nid, body in [
            ("memory_feedback/p", "Topic. Use the X approach."),
            ("memory_feedback/q", "Topic. Do not use X; deprecated."),
        ]:
            n = Node(
                id=nid,
                type="memory_feedback",
                name=nid.split("/", 1)[-1],
                description="",
                body=body,
                source_path=f"/tmp/{nid}.md",
                source_kind="memory",
                project_key=None,
                frontmatter_json=None,
                hash="",
                created_at=now,
                updated_at=now,
            )
            store.upsert_node(n)
            store.upsert_chunks(nid, [(0, embedder.embed_text(body), body)])

        # A judge that always says "yes, this is a contradiction".
        always_confirms = MagicMock()
        always_confirms.judge.return_value = True

        result = analyze(
            store,
            embedder=embedder,
            types=["contradictions"],
            judge=always_confirms,
        )
        contradictions = [f for f in result["findings"] if f["type"] == "contradictions"]
        assert contradictions, "expected at least one contradiction finding"
        assert all(f["severity"] == "high" for f in contradictions), (
            f"with confirming judge, severity should be 'high'; got {[f['severity'] for f in contradictions]}"
        )
    finally:
        store.close()


def test_analyze_with_judge_drops_rejected_candidates() -> None:
    """A judge that says "no" -> the candidate is filtered out
    (not returned with severity=candidate either; the judge is the
    authoritative decision when enabled)."""
    import tempfile

    from mnemo.analyzer import analyze
    from mnemo.store import Node, Store

    tmpdir = tempfile.mkdtemp()
    store = Store(f"{tmpdir}/mnemo.db")
    try:

        class _FakeEmbedder:
            dim = 384

            def embed_text(self, text):
                sig = (text or "")[:32].lower()
                base = [0.0] * 384
                for i, ch in enumerate(sig):
                    base[i % 384] += ord(ch) / 1000.0
                norm = sum(x * x for x in base) ** 0.5 or 1.0
                return [x / norm for x in base]

            def embed_batch(self, texts):
                return [self.embed_text(t) for t in texts]

        embedder = _FakeEmbedder()

        import time

        now = int(time.time())
        for nid, body in [
            ("memory_feedback/p", "Topic. Use the X approach."),
            ("memory_feedback/q", "Topic. Do not use X; deprecated."),
        ]:
            n = Node(
                id=nid,
                type="memory_feedback",
                name=nid.split("/", 1)[-1],
                description="",
                body=body,
                source_path=f"/tmp/{nid}.md",
                source_kind="memory",
                project_key=None,
                frontmatter_json=None,
                hash="",
                created_at=now,
                updated_at=now,
            )
            store.upsert_node(n)
            store.upsert_chunks(nid, [(0, embedder.embed_text(body), body)])

        # A judge that always rejects.
        always_rejects = MagicMock()
        always_rejects.judge.return_value = False

        result = analyze(
            store,
            embedder=embedder,
            types=["contradictions"],
            judge=always_rejects,
        )
        contradictions = [f for f in result["findings"] if f["type"] == "contradictions"]
        assert contradictions == [], (
            f"with rejecting judge, all candidates should be dropped; got {contradictions}"
        )
    finally:
        store.close()

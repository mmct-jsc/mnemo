"""v5.17.1 -- shared _LLMHelper base for the analyzer LLM helpers.

Consolidates the create->parse->graceful-degradation routine shared
by the four sibling judge/proposer classes (LLMContradictionJudge,
LLMSemanticOrphanJudge, LLMRefactorProposer, LLMDeadCodeJudge). This
file locks the base's `_invoke_json` contract directly; the four
existing judge test files are the behaviour-preservation regression
net (they pass unchanged).

Contract:

1. `_invoke_json(system, user)` returns `(parsed_dict, None)` when
   the model returns valid JSON.
2. It returns `(None, "PARSE_ERROR: ...")` on a parse/structure
   error (bad JSON, missing content, wrong shape).
3. It returns `(None, "CLIENT_ERROR: ...")` when the client raises.
4. It forwards model / max_tokens / system / the user message to
   `client.messages.create` exactly.
5. It NEVER raises.
"""

from __future__ import annotations

from unittest.mock import MagicMock


def test_invoke_json_success() -> None:
    from mnemo.analyzer import _LLMHelper

    resp = MagicMock()
    resp.content = [MagicMock(text='{"x": 1, "y": "z"}')]
    client = MagicMock()
    client.messages.create.return_value = resp

    parsed, err = _LLMHelper(client=client)._invoke_json(system="sys", user="usr")
    assert parsed == {"x": 1, "y": "z"}
    assert err is None


def test_invoke_json_parse_error() -> None:
    from mnemo.analyzer import _LLMHelper

    resp = MagicMock()
    resp.content = [MagicMock(text="not valid json")]
    client = MagicMock()
    client.messages.create.return_value = resp

    parsed, err = _LLMHelper(client=client)._invoke_json(system="s", user="u")
    assert parsed is None
    assert err is not None
    assert err.startswith("PARSE_ERROR")


def test_invoke_json_client_error() -> None:
    from mnemo.analyzer import _LLMHelper

    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("network down")

    parsed, err = _LLMHelper(client=client)._invoke_json(system="s", user="u")
    assert parsed is None
    assert err is not None
    assert err.startswith("CLIENT_ERROR")


def test_invoke_json_malformed_response_is_parse_error() -> None:
    """A response with no usable text (IndexError / AttributeError /
    TypeError reaching json.loads) degrades to PARSE_ERROR, not an
    uncaught raise."""
    from mnemo.analyzer import _LLMHelper

    resp = MagicMock()
    resp.content = []  # content[0] -> IndexError
    client = MagicMock()
    client.messages.create.return_value = resp

    parsed, err = _LLMHelper(client=client)._invoke_json(system="s", user="u")
    assert parsed is None
    assert err is not None
    assert err.startswith("PARSE_ERROR")


def test_invoke_json_forwards_create_args() -> None:
    from mnemo.analyzer import _LLMHelper

    resp = MagicMock()
    resp.content = [MagicMock(text="{}")]
    client = MagicMock()
    client.messages.create.return_value = resp

    _LLMHelper(client=client, model="claude-x", max_tokens=123)._invoke_json(
        system="SYS-PROMPT", user="USER-MSG"
    )
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-x"
    assert kwargs["max_tokens"] == 123
    assert kwargs["system"] == "SYS-PROMPT"
    assert kwargs["messages"] == [{"role": "user", "content": "USER-MSG"}]


def test_invoke_json_default_model_and_tokens() -> None:
    """Base defaults match the prior per-class defaults (model
    claude-sonnet-4-6, max_tokens 512)."""
    from mnemo.analyzer import _LLMHelper

    h = _LLMHelper(client=MagicMock())
    assert h.model == "claude-sonnet-4-6"
    assert h.max_tokens == 512
    assert h.rationale_log == []

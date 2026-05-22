"""LLM judge for M4 (answer correctness) -- v5.11.0.

Opt-in via two env vars (BOTH required):

    MNEMO_BENCH_LLM_JUDGE=1
    ANTHROPIC_API_KEY=sk-...

Without both, ``judge_from_env()`` returns ``None`` and the per-task
scorer falls back to the keyword scorer. This keeps the bench's
core dependency-free for external implementers (Anthropic SDK is
an optional extra under the ``llm-judge`` group in pyproject.toml).

The judge sends a small structured prompt to Claude asking it to
grade each rubric criterion on a 0.0-1.0 scale + return JSON. The
mean of per-criterion scores is the prompt's M4 score.

Default model: ``claude-sonnet-4-6``. Caller can override via
``LLMJudge(model=...)``. We intentionally default to Sonnet rather
than Opus -- the grading task is shallow + benefits from Sonnet's
lower latency + cost for benchmark sweeps. Operators wanting Opus
can pass it explicitly.

Graceful degradation: if the API call raises OR the response isn't
valid JSON, the judge returns 0.0 + logs a warning. The benchmark
run continues; the rationale-log captures the failure for audit.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


JUDGE_SYSTEM_PROMPT = (
    "You are a strict rubric grader for an agent-memory benchmark. "
    "You receive a rubric (a list of named criteria with weights) and the "
    "agent's output. For EACH criterion, assign a score in [0.0, 1.0] "
    "indicating how well the output satisfies that specific criterion. "
    "Return ONLY a JSON object of the shape "
    '{"scores": [<float>, ...], "rationale": "<one-paragraph explanation>"}. '
    "The scores list MUST be in the same order + length as the rubric. "
    "No prose outside the JSON. No markdown fences."
)


@dataclass
class LLMJudge:
    """Opt-in M4 grader. Caller supplies the Anthropic client; the
    judge handles prompt assembly + JSON-parsing + graceful failure."""

    client: Any
    """The Anthropic client (anthropic.Anthropic()). Accepts any
    object with a ``messages.create(...)`` method -- tests pass a
    MagicMock so no network call is required."""

    model: str = "claude-sonnet-4-6"
    """Default judge model. Sonnet is the recommended grader for
    bench sweeps (lower latency + cost than Opus for shallow rubric
    grading). Pass ``model="claude-opus-4-7"`` for higher-precision
    grading if your rubric criteria are subtle."""

    max_tokens: int = 1024
    """Token budget for the judge response. The structured output
    is small (~150 tokens for 5 criteria); 1024 is a comfortable
    ceiling that accommodates the rationale paragraph."""

    rationale_log: list[dict[str, Any]] = field(default_factory=list)
    """Per-prompt audit log. Each entry is
    ``{rubric, output, scores, rationale, parsed_ok}``. Operators
    can dump this after a sweep to inspect grading decisions."""

    def score(self, *, rubric: list[dict[str, Any]], output: str) -> float:
        """Grade ``output`` against ``rubric``; return mean per-criterion
        score in [0.0, 1.0]. Graceful failure: returns 0.0 on any
        exception + records the failure in ``rationale_log``."""
        if not rubric:
            return 0.0
        user_msg = (
            f"## Rubric\n{json.dumps(rubric, indent=2)}\n\n"
            f"## Agent output\n{output}\n\n"
            "## Task\n"
            f"Score each of the {len(rubric)} criteria 0.0 to 1.0. "
            "Respond with ONLY the JSON object."
        )
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=JUDGE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            # The Anthropic SDK returns response.content as a list of
            # content blocks. For a text-only response there's one
            # block with .text.
            text = response.content[0].text
            parsed = json.loads(text)
            scores = parsed.get("scores", [])
            rationale = parsed.get("rationale", "")
            if not scores or len(scores) != len(rubric):
                log.warning(
                    "LLMJudge: scores length mismatch (got %d, expected %d); returning 0.0",
                    len(scores),
                    len(rubric),
                )
                self.rationale_log.append(
                    {
                        "rubric": rubric,
                        "output": output,
                        "scores": scores,
                        "rationale": rationale,
                        "parsed_ok": False,
                    }
                )
                return 0.0
            self.rationale_log.append(
                {
                    "rubric": rubric,
                    "output": output,
                    "scores": scores,
                    "rationale": rationale,
                    "parsed_ok": True,
                }
            )
            # Clamp + mean.
            clamped = [max(0.0, min(1.0, float(s))) for s in scores]
            return sum(clamped) / len(clamped)
        except (json.JSONDecodeError, KeyError, AttributeError, IndexError) as exc:
            log.warning("LLMJudge: parse/structure error (%s); returning 0.0", exc)
            self.rationale_log.append(
                {
                    "rubric": rubric,
                    "output": output,
                    "scores": [],
                    "rationale": f"PARSE_ERROR: {exc}",
                    "parsed_ok": False,
                }
            )
            return 0.0
        except Exception as exc:  # noqa: BLE001
            log.warning("LLMJudge: client error (%s); returning 0.0", exc)
            self.rationale_log.append(
                {
                    "rubric": rubric,
                    "output": output,
                    "scores": [],
                    "rationale": f"CLIENT_ERROR: {exc}",
                    "parsed_ok": False,
                }
            )
            return 0.0


def judge_from_env() -> LLMJudge | None:
    """Construct an LLMJudge from environment if both env vars are
    present + the Anthropic SDK is installed; otherwise return None
    so callers can fall back to the keyword scorer.

    Required env vars:
      - ``MNEMO_BENCH_LLM_JUDGE`` (any truthy value -- "1", "true", ...)
      - ``ANTHROPIC_API_KEY``

    Returns ``None`` (default) when either is missing OR when the
    ``anthropic`` package isn't installed. This is the CI-friendly
    path; bench tests don't fail when the optional dep is absent."""
    flag = os.environ.get("MNEMO_BENCH_LLM_JUDGE", "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic  # type: ignore[import-untyped]
    except ImportError:
        log.warning(
            "LLMJudge: MNEMO_BENCH_LLM_JUDGE=1 set but 'anthropic' package not "
            "installed; falling back to keyword scorer. Install with "
            "'pip install agent-memory-bench[llm-judge]' to enable."
        )
        return None
    model = os.environ.get("MNEMO_BENCH_JUDGE_MODEL", "claude-sonnet-4-6")
    return LLMJudge(client=anthropic.Anthropic(), model=model)

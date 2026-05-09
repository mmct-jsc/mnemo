"""Token-budget compression of scored retrieval hits.

Two-pass strategy:

1. Emit one one-line summary per hit (``[mnemo:<id>] [<type>] <description>``)
   in score order, stopping when adding the next hit would exceed the budget.
2. If budget remains and at least one hit was emitted, attach the top hit's
   full body so the caller has at least one detailed entry to ground the
   answer.

The compressor never silently truncates a body mid-line: if the body doesn't
fit, the body field is left as ``None`` (description-only mode).
"""

from __future__ import annotations

from dataclasses import dataclass

from mnemo.embed import _approx_tokens
from mnemo.store import Node


@dataclass
class ScoredHit:
    node: Node
    score: float
    chunk_idx: int | None
    chunk_text: str | None


@dataclass
class CompressedHit:
    node_id: str
    type: str
    name: str
    description: str
    body: str | None
    score: float
    chunk_idx: int | None
    citation: str


def count_tokens(text: str) -> int:
    """Approximate token count using the same heuristic as the chunker."""
    return _approx_tokens(text)


def _description_line(hit: ScoredHit, citation_prefix: str) -> str:
    desc = hit.node.description or hit.node.name or ""
    return f"[{citation_prefix}:{hit.node.id}] [{hit.node.type}] {desc}"


def compress_to_budget(
    hits: list[ScoredHit],
    *,
    budget_tokens: int = 800,
    citation_prefix: str = "mnemo",
) -> tuple[list[CompressedHit], int]:
    """Pack ``hits`` into a token-budgeted output.

    Returns ``(compressed_hits, tokens_used)``. Caller is responsible for
    splicing the result into a chat prompt or a retrieval API response.
    """
    out: list[CompressedHit] = []
    used = 0

    for hit in hits:
        line = _description_line(hit, citation_prefix)
        line_tokens = count_tokens(line)
        if used + line_tokens > budget_tokens:
            break
        out.append(
            CompressedHit(
                node_id=hit.node.id,
                type=hit.node.type,
                name=hit.node.name,
                description=hit.node.description or "",
                body=None,
                score=hit.score,
                chunk_idx=hit.chunk_idx,
                citation=f"[{citation_prefix}:{hit.node.id}]",
            )
        )
        used += line_tokens

    if out and hits:
        body = hits[0].node.body
        body_tokens = count_tokens(body)
        if used + body_tokens <= budget_tokens:
            out[0].body = body
            used += body_tokens

    return out, used

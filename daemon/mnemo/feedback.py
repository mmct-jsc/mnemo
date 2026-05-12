"""Feedback collection helpers for v1.2 ("Learning to Listen").

Today (phase 2) this module hosts the **inferred-re-query detector**:
when a user issues a prompt that's cosine-similar to one they asked
inside the look-back window, mnemo writes a `signal=-0.5,
reason='inferred_requery'` row against the earlier query's top-N
retrieved hits. The auto-tuner (phase 5) will read those rows along
with explicit thumbs to learn that the earlier hits weren't actually
satisfying.

Future phases extend this module with explicit-thumb glue + the
cite_copied signal handler.

Implementation note: the detector is called **before** the current
query is logged so it never compares the new query to itself. The
caller (retrieve.query) does:

    infer_requery_feedback(...)        # writes feedback for OLD queries
    store.log_query(..., embedding=v)  # now the current query lands
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mnemo.store import Store

log = logging.getLogger(__name__)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length float vectors.

    Returns 0.0 for mismatched dimensions or for zero-magnitude vectors
    (degenerate inputs that shouldn't happen with a real embedder, but
    we don't want to crash the query path if they do).
    """
    n = len(a)
    if n == 0 or len(b) != n:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / ((na**0.5) * (nb**0.5))


def infer_requery_feedback(
    store: Store,
    *,
    query_emb: list[float],
    window_seconds: int = 300,
    threshold: float = 0.85,
    top_n: int = 3,
) -> int:
    """Detect re-queries against recent prompts and write feedback rows.

    For every query in the last ``window_seconds`` whose embedding has
    cosine similarity >= ``threshold`` with ``query_emb``, write a
    feedback_event row with ``signal=-0.5`` and
    ``reason='inferred_requery'`` against each of its top ``top_n``
    retrieved_ids. The UNIQUE (query_id, node_id, reason) constraint
    on feedback_event makes the writes idempotent.

    Returns the number of (query, hit) pairs flagged. The caller logs
    this count for observability; the actual row count in feedback_event
    may be less if duplicates collapsed via UPSERT.
    """
    # Disabled if threshold is set above the valid cosine range.
    if threshold > 1.0:
        return 0
    # No window -> no signal.
    if window_seconds <= 0 or top_n <= 0:
        return 0

    recents = store.recent_queries_with_embeddings(window_seconds=window_seconds)
    if not recents:
        return 0

    emitted = 0
    for prior in recents:
        if prior.embedding is None:
            continue  # defensive: helper already filters NULL, but keep
        sim = cosine_similarity(query_emb, prior.embedding)
        if sim < threshold:
            continue
        for hit_id in prior.retrieved_ids[:top_n]:
            try:
                store.log_feedback_event(
                    query_id=prior.id,
                    node_id=hit_id,
                    signal=-0.5,
                    reason="inferred_requery",
                )
                emitted += 1
            except Exception as exc:  # noqa: BLE001
                # Don't let a single bad row (deleted node, etc.) abort
                # the whole detector pass. Log and move on.
                log.warning(
                    "inferred_requery skip: query=%s node=%s err=%s",
                    prior.id,
                    hit_id,
                    exc,
                )
    return emitted

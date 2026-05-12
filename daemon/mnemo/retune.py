"""Coordinate-descent auto-tuner for the 6-term scoring weights (v1.2 phase 5).

Once a user has accumulated enough labeled queries (default >= 30,
configurable via ``Config.retune_min_queries``), ``retune(store)``:

1. Loads queries that have ``score_components`` populated AND at
   least one row in ``feedback_event``.
2. Splits them 80 / 20 time-ordered (oldest = train, newest = val)
   so future feedback can't leak into the training set.
3. Builds a per-(query_id, node_id) feedback index keyed on
   ``best_feedback_signal``: explicit thumbs override implicit
   inferred / cite signals, latest explicit wins on ties.
4. Runs coordinate descent over the 6 weights with nudges
   ``{-0.10, -0.05, +0.05, +0.10}``, up to 4 passes, optimizing
   validation MRR. Each candidate update must beat the current best
   by at least ``EPS = 0.001``.
5. Returns a :class:`RetuneReport` -- the CLI / UI prints the diff +
   before/after MRR and asks the user before persisting via
   ``config.update``.

The optimizer is pure math over the captured components -- it
doesn't touch the embedder or rerun retrieval. That makes the
~96-evaluation pass take well under the 60 s wall-clock cap on a
500-row dataset.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

from mnemo.config import ScoringWeights

if TYPE_CHECKING:
    from mnemo.store import FeedbackEvent, Store

log = logging.getLogger(__name__)

# Optimizer constants -- design doc 4.x.
NUDGES: tuple[float, ...] = (-0.10, -0.05, 0.05, 0.10)
KEYS: tuple[str, ...] = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta")
EPS: float = 0.001
MAX_PASSES: int = 4

# Reasons that count as "explicit" feedback (override implicit ones at
# scoring time per the v1.2 design).
EXPLICIT_REASONS = frozenset({"thumbs_up", "thumbs_down"})


@dataclass
class RetuneReport:
    """What the auto-tuner produces. The CLI / UI render this directly."""

    proposed: dict[str, float]
    current: dict[str, float]
    diff: dict[str, float]
    train_mrr_before: float
    train_mrr_after: float
    val_mrr_before: float
    val_mrr_after: float
    iterations: int
    train_size: int
    val_size: int
    elapsed_seconds: float
    log: list[str] = field(default_factory=list)


# --- Scoring -----------------------------------------------------------


def best_feedback_signal(events: list[FeedbackEvent]) -> float:
    """Reduce a list of feedback rows for one (query, node) pair to a
    single scalar signal.

    Rules (from the v1.2 design):
    - If any event has an explicit reason (``thumbs_up`` /
      ``thumbs_down``), pick the latest by ``created_at`` and return
      its signal. Explicit ALWAYS overrides implicit.
    - Otherwise, return the max of the implicit signals (so
      ``cite_copied`` > ``inferred_requery``).
    - Empty list -> 0.0 (no signal).
    """
    if not events:
        return 0.0
    explicit = [e for e in events if e.reason in EXPLICIT_REASONS]
    if explicit:
        # Latest explicit wins -- handles the user-changed-their-mind case.
        latest = max(explicit, key=lambda e: e.created_at)
        return float(latest.signal)
    return float(max(e.signal for e in events))


def rescore_with_weights(
    components: dict[str, dict[str, float]],
    weights: ScoringWeights,
) -> list[tuple[str, float]]:
    """Compute the linear combination of weights * components for each
    node and return a descending-by-score list.

    Mirrors the retrieve.py scoring formula:

        score = a*vector + b*graph + g*recency + d*type + e*project + z*lexical

    Missing component keys are treated as 0.0 so older audit rows
    (or zero-valued terms that weren't logged) don't KeyError the
    optimizer.
    """
    out: list[tuple[str, float]] = []
    for nid, comp in components.items():
        s = (
            weights.alpha * comp.get("vector", 0.0)
            + weights.beta * comp.get("graph", 0.0)
            + weights.gamma * comp.get("recency", 0.0)
            + weights.delta * comp.get("type", 0.0)
            + weights.epsilon * comp.get("project", 0.0)
            + weights.zeta * comp.get("lexical", 0.0)
        )
        out.append((nid, s))
    out.sort(key=lambda t: -t[1])
    return out


def mrr(
    weights: ScoringWeights,
    samples: list[tuple[str, dict[str, dict[str, float]]]],
    feedback_index: dict[tuple[str, str], list[FeedbackEvent]],
) -> float:
    """Mean Reciprocal Rank over the supplied (query_id, components)
    samples and feedback index.

    For each query: rescore the candidate pool under ``weights``, find
    the first hit with a positive feedback signal, accumulate 1/rank.
    Average across queries.
    """
    if not samples:
        return 0.0
    total = 0.0
    for qid, comps in samples:
        rescored = rescore_with_weights(comps, weights)
        for rank, (nid, _score) in enumerate(rescored, start=1):
            sig = best_feedback_signal(feedback_index.get((qid, nid), []))
            if sig > 0:
                total += 1.0 / rank
                break
    return total / len(samples)


# --- Optimizer ---------------------------------------------------------


def coordinate_descent(
    start: ScoringWeights,
    *,
    train_samples: list[tuple[str, dict[str, dict[str, float]]]],
    val_samples: list[tuple[str, dict[str, dict[str, float]]]],
    feedback_index: dict[tuple[str, str], list[FeedbackEvent]],
    max_passes: int = MAX_PASSES,
    wall_clock_cap_seconds: float = 60.0,
) -> tuple[ScoringWeights, float, list[str], int]:
    """Run coordinate descent.

    Optimizes validation MRR; each accepted nudge must beat the
    current best by at least ``EPS``. Stops early when a full pass
    produces no improvement, or when the wall-clock cap is hit.

    Returns ``(tuned_weights, best_val_mrr, log_lines, iterations)``.
    """
    weights = ScoringWeights(**asdict(start))
    best = mrr(weights, val_samples, feedback_index)
    log_lines: list[str] = [f"start val_mrr={best:.4f}"]
    iterations = 0
    started = time.time()

    for pass_ in range(max_passes):
        improved = False
        for k in KEYS:
            for n in NUDGES:
                if time.time() - started > wall_clock_cap_seconds:
                    log_lines.append("wall-clock cap reached, stopping")
                    return weights, best, log_lines, iterations
                iterations += 1
                cand_kwargs = asdict(weights)
                cand_kwargs[k] = cand_kwargs[k] + n
                # Clamp to [0, 1.0] -- weights outside this range are
                # nonsense and can produce numerical instabilities.
                if not (0.0 <= cand_kwargs[k] <= 1.0):
                    continue
                cand = ScoringWeights(**cand_kwargs)
                s = mrr(cand, val_samples, feedback_index)
                if s > best + EPS:
                    weights = cand
                    best = s
                    log_lines.append(f"pass={pass_} {k}{n:+.2f} -> val_mrr={s:.4f}")
                    improved = True
        if not improved:
            log_lines.append(f"pass={pass_} no improvement, stopping")
            break
    else:
        log_lines.append(f"pass={max_passes - 1} budget exhausted, stopping")

    return weights, best, log_lines, iterations


# --- High-level entry --------------------------------------------------


def retune(
    store: Store,
    *,
    min_queries: int = 30,
    start_weights: ScoringWeights | None = None,
    max_passes: int = MAX_PASSES,
    wall_clock_cap_seconds: float = 60.0,
) -> RetuneReport:
    """Load labeled queries from ``store``, run coordinate descent,
    return a :class:`RetuneReport`. Does NOT persist the new weights
    -- the CLI / UI shows the diff to the user first.

    ``start_weights`` defaults to the current on-disk config.
    """
    from mnemo import config as cfg_mod  # local import to avoid cycles

    started = time.time()
    if start_weights is None:
        start_weights = cfg_mod.load().scoring

    labeled = store.recent_queries_with_components(min_feedback=1, limit=10_000)

    if len(labeled) < min_queries:
        return RetuneReport(
            proposed=asdict(start_weights),
            current=asdict(start_weights),
            diff=dict.fromkeys(KEYS, 0.0),
            train_mrr_before=0.0,
            train_mrr_after=0.0,
            val_mrr_before=0.0,
            val_mrr_after=0.0,
            iterations=0,
            train_size=0,
            val_size=0,
            elapsed_seconds=time.time() - started,
            log=[
                f"only {len(labeled)} labeled queries available; below threshold of {min_queries}"
            ],
        )

    # Build feedback index keyed by (query_id, node_id).
    feedback_index: dict[tuple[str, str], list[FeedbackEvent]] = {}
    for q in labeled:
        events = store.list_feedback_events(query_id=q.id, limit=1000)
        for e in events:
            feedback_index.setdefault((e.query_id, e.node_id), []).append(e)

    # Time-ordered 80/20 split. labeled is ascending by ts (oldest
    # first) so the head is train, tail is val.
    split = int(len(labeled) * 0.8)
    if split < 1:
        split = 1  # guarantee at least one in each side
    train_q = labeled[:split]
    val_q = labeled[split:] if split < len(labeled) else labeled[-1:]

    def _to_samples(qs):
        return [(q.id, q.score_components or {}) for q in qs]

    train_samples = _to_samples(train_q)
    val_samples = _to_samples(val_q)

    train_before = mrr(start_weights, train_samples, feedback_index)
    val_before = mrr(start_weights, val_samples, feedback_index)

    tuned, val_after, opt_log, iterations = coordinate_descent(
        start_weights,
        train_samples=train_samples,
        val_samples=val_samples,
        feedback_index=feedback_index,
        max_passes=max_passes,
        wall_clock_cap_seconds=wall_clock_cap_seconds,
    )
    train_after = mrr(tuned, train_samples, feedback_index)

    diff = {k: getattr(tuned, k) - getattr(start_weights, k) for k in KEYS}

    return RetuneReport(
        proposed=asdict(tuned),
        current=asdict(start_weights),
        diff=diff,
        train_mrr_before=train_before,
        train_mrr_after=train_after,
        val_mrr_before=val_before,
        val_mrr_after=val_after,
        iterations=iterations,
        train_size=len(train_samples),
        val_size=len(val_samples),
        elapsed_seconds=time.time() - started,
        log=opt_log,
    )

"""v4.6 custom layout engine: ForceAtlas2 / LinLog / Barnes-Hut.

The v4.5.x Fruchterman-Reingold collapsed the giant component into a
featureless dense disk (kNN-16-only repulsion + undifferentiated
gravity + p95-normalize) and scattered the ~2298 tiny components as
one-dot-per-component confetti. v4.6 replaces the ALGORITHM (the
server-side + cached architecture is unchanged; ``compute_graph_layout
(n, edges) -> [x0,y0,...]`` keeps its exact signature).

These tests are the real acceptance gate: the dominant historical
failure (the "blob") is a property of the Python output and is
asserted NUMERICALLY here -- no GPU required. A planted-community
graph makes "communities must separate" directly measurable.
"""

from __future__ import annotations

import math
import random

import numpy as np

from mnemo.ui.graph_layout import compute_graph_layout


def _planted(communities: int, per: int, singles: int, seed: int = 7):
    """A graph with PLANTED community structure: ``communities`` dense
    blobs (sparse inter-community bridges) form the giant; plus
    ``singles`` singletons. Returns ``(n, edges, labels)`` where
    ``labels[i]`` is the planted community of node ``i`` (``-1`` for a
    singleton)."""
    rng = random.Random(seed)
    edges: list[tuple[int, int]] = []
    labels: list[int] = []
    nodes_by_c: list[list[int]] = []
    n = 0
    for c in range(communities):
        members = list(range(n, n + per))
        nodes_by_c.append(members)
        labels += [c] * per
        n += per
        # dense intra-community (~4 internal edges per node)
        for i in members:
            for _ in range(4):
                j = rng.choice(members)
                if i != j:
                    edges.append((i, j))
    # sparse inter-community bridges (one per community pair)
    for a in range(communities):
        for b in range(a + 1, communities):
            edges.append((rng.choice(nodes_by_c[a]), rng.choice(nodes_by_c[b])))
    labels += [-1] * singles
    n += singles
    return n, edges, labels


def _xy(pos: list[float], i: int) -> tuple[float, float]:
    return pos[2 * i], pos[2 * i + 1]

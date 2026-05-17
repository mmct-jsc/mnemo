"""Server-side Nebula graph layout (v4.5 architecture pivot).

Why this exists
---------------
v4.5 swapped the Nebula renderer cosmos.gl -> sigma.js. The layout was
first computed in the browser (graphology forceatlas2, sync then Web
Worker). Across three measured attempts that proved **non-deterministic
and quality-fragile** on the real 11k-node / 2298-component scope: a
synchronous run converged, the Web Worker exploded the giant component,
and circlepack alone was structureless confetti. Per the systematic-
debugging "3 failed fixes => question the architecture" rule (and with
the user's explicit approval) the layout moved here: computed ONCE in
the daemon -- deterministic, fully converged, tuned -- cached, and the
browser became a pure sigma renderer.

Algorithm
---------
1. ``scipy.sparse.csgraph.connected_components`` -- find the giant
   component and the (typically thousands of) tiny ones.
2. Giant component: a deterministic Fruchterman-Reingold / ForceAtlas2
   force layout. Repulsion is the k-nearest-neighbour approximation via
   ``scipy.spatial.cKDTree`` (O(n log n) -- the part that made a pure
   O(n^2) pass too slow), attraction is linear along edges, plus a mild
   centroid gravity for cohesion, with a cooling schedule so it
   *converges* (edges end up much shorter than random pairs -- the
   readable-structure metric the client could never reliably hit).
3. Everything else (small components + singletons) is scattered into a
   compact ORGANIC dust halo *outside* the giant's radius -- a golden-
   angle skeleton broken by a deterministic, independent-seed radial +
   angular jitter (an irregular cloud, not a geometric "mandala" ring),
   still bounded + never flung (the bbox-blow-out failure cannot recur).

Determinism: every random draw uses a fixed-seed ``numpy`` generator,
so the same graph yields a byte-identical layout (cacheable, stable).

Output: a flat ``[x0, y0, x1, y1, ...]`` list of floats in the SAME
node order the caller passed (the /ui/graph-data element order), so
the client applies it index-aligned with zero coordination.
"""

from __future__ import annotations

import math

import numpy as np

# Fixed seeds + tuned constants. These produced, on the real scope, a
# converged organic core (edge/random-pair ratio ~0.4) with a tidy
# bounded halo -- verified live.
_SEED = 42
_R_CORE = 1000.0  # giant component normalised to ~this radius
_GIANT_ITERS = 320  # FR iterations for the giant (with cooling)
_KNN = 16  # k-nearest neighbours used for the repulsion approx


def _largest_component(n: int, edges: list[tuple[int, int]]) -> tuple[np.ndarray, int]:
    """Return (component-label per node, giant label)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    if not edges:
        return np.zeros(n, dtype=np.int64), -1  # all singletons
    r = np.fromiter((e[0] for e in edges), dtype=np.int64, count=len(edges))
    c = np.fromiter((e[1] for e in edges), dtype=np.int64, count=len(edges))
    data = np.ones(len(edges) * 2, dtype=np.int8)
    rr = np.concatenate([r, c])
    cc = np.concatenate([c, r])
    adj = coo_matrix((data, (rr, cc)), shape=(n, n)).tocsr()
    ncomp, labels = connected_components(adj, directed=False)
    if ncomp <= 1:
        return labels, int(labels[0]) if n else -1
    counts = np.bincount(labels, minlength=ncomp)
    return labels, int(counts.argmax())


def _layout_giant(
    gpos: np.ndarray, gedges: list[tuple[int, int]], rng: np.random.Generator
) -> np.ndarray:
    """Deterministic FR/FA2 on the giant component. ``gpos`` is the
    seeded (g, 2) array (mutated + returned)."""
    from scipy.spatial import cKDTree

    g = gpos.shape[0]
    if g <= 1:
        return gpos
    ea = np.fromiter((e[0] for e in gedges), dtype=np.int64, count=len(gedges))
    eb = np.fromiter((e[1] for e in gedges), dtype=np.int64, count=len(gedges))

    # Scale-free tuning: the ideal edge length k grows with area/node.
    # k*4 (was *2) => longer springs => the giant SPREADS into a
    # readable nebula instead of collapsing into a hot central blob
    # (the "too bright" core: ~944 node-centers were landing in a
    # 60px box; FR was over-contracted -- ratio 0.15 is tighter than
    # readable, ~0.35-0.5 is the sweet spot).
    area = _R_CORE * _R_CORE
    k = math.sqrt(area / g) * 4.0
    k2 = k * k
    kk = min(_KNN, g - 1)

    for it in range(_GIANT_ITERS):
        disp = np.zeros((g, 2), dtype=np.float64)

        # --- repulsion: k-nearest-neighbour approximation ---
        tree = cKDTree(gpos)
        # +1 because the first neighbour is the point itself.
        _, idx = tree.query(gpos, k=kk + 1)
        for j in range(1, kk + 1):
            delta = gpos - gpos[idx[:, j]]
            d2 = np.einsum("ij,ij->i", delta, delta) + 1e-9
            f = (k2 / d2)[:, None]
            disp += delta * f

        # --- attraction: linear springs along edges ---
        d_e = gpos[ea] - gpos[eb]
        dist = np.sqrt(np.einsum("ij,ij->i", d_e, d_e)) + 1e-9
        fa = (dist / k)[:, None] * (d_e / dist[:, None])
        np.add.at(disp, ea, -fa)
        np.add.at(disp, eb, fa)

        # --- very weak gravity toward the centroid (cohesion only) ---
        # 0.004 (was 0.012): just enough to keep disconnected-within-
        # giant stragglers from drifting, NOT enough to collapse the
        # whole component onto its centroid (the over-bright knot).
        centroid = gpos.mean(axis=0)
        disp += (centroid - gpos) * 0.004 * (np.linalg.norm(gpos - centroid, axis=1) / k)[:, None]

        # --- cooling: bounded step, decaying over the run ---
        temp = _R_CORE * 0.10 * (1.0 - it / _GIANT_ITERS) + 1.0
        dlen = np.sqrt(np.einsum("ij,ij->i", disp, disp)) + 1e-9
        step = np.minimum(dlen, temp)
        gpos += disp / dlen[:, None] * step[:, None]

    # Normalise: centre at origin, then scale so the 95th-percentile
    # radius == _R_CORE. Scaling by the MAX let a few outliers define
    # the scale while the dense bulk stayed tiny (the blob); the p95
    # makes the BULK of the giant fill the core disc -> nodes are
    # individually resolvable, the structure reads.
    gpos -= gpos.mean(axis=0)
    radii = np.linalg.norm(gpos, axis=1)
    r95 = float(np.percentile(radii, 95)) or 1.0
    gpos *= _R_CORE / r95
    return gpos


def compute_graph_layout(n: int, edges: list[tuple[int, int]]) -> list[float]:
    """Compute a deterministic, fully-converged layout for ``n`` nodes
    (indices ``0..n-1``) with undirected ``edges``. Returns a flat
    ``[x0, y0, x1, y1, ...]`` list of length ``2 * n`` in node order.
    """
    if n <= 0:
        return []
    if n == 1:
        return [0.0, 0.0]

    rng = np.random.default_rng(_SEED)
    pos = np.zeros((n, 2), dtype=np.float64)

    # de-dupe + drop self-loops once (csgraph + FR both want clean input)
    clean: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for a, b in edges:
        if a == b or not (0 <= a < n) or not (0 <= b < n):
            continue
        key = (a, b) if a < b else (b, a)
        if key in seen:
            continue
        seen.add(key)
        clean.append((a, b))

    labels, giant = _largest_component(n, clean)
    giant_mask = labels == giant if giant >= 0 else np.zeros(n, dtype=bool)
    gidx = np.nonzero(giant_mask)[0]

    # --- giant component: seeded disc -> deterministic FR ---
    if gidx.size:
        ang = rng.uniform(0, 2 * math.pi, gidx.size)
        rad = np.sqrt(rng.uniform(0, 1, gidx.size)) * (_R_CORE * 0.6)
        gpos = np.column_stack([np.cos(ang) * rad, np.sin(ang) * rad])
        remap = {int(orig): k for k, orig in enumerate(gidx)}
        gedges = [(remap[a], remap[b]) for a, b in clean if giant_mask[a] and giant_mask[b]]
        gpos = _layout_giant(gpos, gedges, rng)
        pos[gidx] = gpos

    # --- everything else: a compact phyllotaxis halo outside R_CORE ---
    # Group the non-giant nodes by component so a small component's
    # members cluster together rather than scatter.
    others: dict[int, list[int]] = {}
    for i in range(n):
        if giant_mask[i]:
            continue
        others.setdefault(int(labels[i]), []).append(i)
    groups = sorted(others.values(), key=len, reverse=True)

    # The halo stays CLOSE to the core so the giant component DOMINATES
    # the frame (a bright dense galaxy with a dust halo) -- not a tiny
    # core lost in a vast sparse ring (which sigma's fit-to-bbox would
    # shrink to mud, the failure that started this). A phyllotaxis angle
    # gives uniform density, but a PERFECT sunflower lattice rendered as
    # a too-regular geometric "mandala" ring -- the user rejected the
    # placement as "not good and lively / weird layout". v4.5.4: keep
    # the uniform-density sqrt radius + golden angle as the SKELETON,
    # then break the lattice with a deterministic per-group radial +
    # angular jitter so it reads as an irregular organic dust field
    # (still bounded ~2.6*_R_CORE; the giant is still ~40% of frame).
    # The jitter draws from an INDEPENDENT fixed-seed stream so it is
    # byte-identical regardless of the giant's size (whose own rng draws
    # vary with g) -- determinism + cacheability preserved.
    golden = math.pi * (3.0 - math.sqrt(5.0))  # sunflower angle
    ng = max(1, len(groups))
    hrng = np.random.default_rng(_SEED + 1)
    rj = hrng.uniform(-0.16, 0.16, ng)  # per-group radial jitter (frac)
    aj = hrng.uniform(-0.55, 0.55, ng)  # per-group angular jitter (rad)
    for s, members in enumerate(groups):
        frac = (s + 1) / ng
        rr = _R_CORE * (1.32 + 0.92 * math.sqrt(frac)) * (1.0 + rj[s])
        th = (s + 1) * golden + aj[s]
        gx, gy = math.cos(th) * rr, math.sin(th) * rr
        if len(members) == 1:
            pos[members[0]] = (gx, gy)
        else:
            # an irregular CLOUD for the small component's members (not
            # a perfect ring): jittered polar offsets around the anchor.
            base = _R_CORE * 0.05 + len(members) * 1.3
            ma = hrng.uniform(0.0, 2.0 * math.pi, len(members))
            mr = base * np.sqrt(hrng.uniform(0.15, 1.0, len(members)))
            for j, node in enumerate(members):
                pos[node] = (gx + math.cos(ma[j]) * mr[j], gy + math.sin(ma[j]) * mr[j])

    out: list[float] = [0.0] * (2 * n)
    for i in range(n):
        out[2 * i] = float(pos[i, 0])
        out[2 * i + 1] = float(pos[i, 1])
    return out

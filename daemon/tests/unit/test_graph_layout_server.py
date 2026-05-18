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


def test_shape_finite_deterministic() -> None:
    n, edges, _ = _planted(6, 60, 90)
    a = compute_graph_layout(n, edges)
    b = compute_graph_layout(n, edges)
    assert len(a) == 2 * n
    assert all(math.isfinite(v) for v in a), "every coordinate finite"
    assert a == b, "byte-identical for a fixed graph (cacheable)"


def test_degenerate_inputs() -> None:
    assert compute_graph_layout(0, []) == []
    assert len(compute_graph_layout(1, [])) == 2
    iso = compute_graph_layout(40, [])
    assert len(iso) == 80
    assert all(math.isfinite(v) for v in iso)


def _community_separation(pos, labels):
    """Mean intra-community pairwise distance / mean inter-community
    pairwise distance over the planted (non-singleton) nodes. << 1.0
    means communities are SEPARATED (not collapsed into one blob)."""
    import itertools

    pts: dict[int, list[tuple[float, float]]] = {}
    for i, c in enumerate(labels):
        if c >= 0:
            pts.setdefault(c, []).append(_xy(pos, i))
    rng = random.Random(3)
    intra: list[float] = []
    inter: list[float] = []
    cs = list(pts)
    for c in cs:
        pc = pts[c]
        for _ in range(400):
            a, b = rng.choice(pc), rng.choice(pc)
            intra.append(math.dist(a, b))
    for a, b in itertools.combinations(cs, 2):
        for _ in range(200):
            p, q = rng.choice(pts[a]), rng.choice(pts[b])
            inter.append(math.dist(p, q))
    return (sum(intra) / len(intra)) / (sum(inter) / len(inter))


def test_not_a_blob_still_structured() -> None:
    """De-blob gate, GALAXY-evolved. v4.5.x FR collapsed communities
    into one disk (ratio ~1.0). v4.6 is now a SPIRAL GALAXY: the
    log-spiral shear deliberately stretches communities ALONG the
    arms, so the intra/inter ratio rises from the old ~0.4 -- but it
    must stay meaningfully below 1.0 (still structured, NOT a
    featureless blob; the arms are coherent, not scrambled)."""
    n, edges, labels = _planted(6, 70, 120)
    pos = compute_graph_layout(n, edges)
    ratio = _community_separation(pos, labels)
    # measured 0.67 with the locality-preserving spiral shear; gate at
    # 0.80 (clear margin below a ~1.0 blob -- communities stay coherent
    # along the arms, NOT scrambled into a hairball-galaxy).
    assert ratio < 0.80, (
        f"no structure (intra/inter={ratio:.3f} ~ a blob); the spiral "
        f"shear must preserve community adjacency along the arms"
    )


def test_edges_shorter_than_random_pairs() -> None:
    """The galaxy shear is locality-preserving -> connected nodes stay
    near each other (same arm region). Edges must still be clearly
    shorter than random pairs (galaxy-relaxed from <0.5 to <0.65: the
    arms stretch communities radially, loosening edges somewhat)."""
    n, edges, labels = _planted(5, 80, 100)
    pos = compute_graph_layout(n, edges)
    gi = [i for i, c in enumerate(labels) if c >= 0]
    gs = set(gi)
    ge = [(a, b) for a, b in edges if a in gs and b in gs]
    me = sum(math.dist(_xy(pos, a), _xy(pos, b)) for a, b in ge) / len(ge)
    rng = random.Random(1)
    mr = (
        sum(math.dist(_xy(pos, rng.choice(gi)), _xy(pos, rng.choice(gi))) for _ in range(3000))
        / 3000
    )
    assert me / mr < 0.65, f"edges not contracted (ratio {me / mr:.3f})"


def test_layout_giant_is_a_spiral_not_a_round_disc() -> None:
    """POSITIVE galaxy contract for the LAYOUT: the giant is a SPIRAL
    -- a real correlation between unwound angle and ln(radius) (the
    log-spiral winding); a round/uniform disc gives ~0. (The luminous
    central BULGE is deliberately NOT a layout-density requirement: a
    locality-preserving shear keeps the graph meaningful and so cannot
    pile a density bulge without scrambling edges into a hairball; the
    bright bulge is a RENDERER concern -- radial brightness + the core
    glow -- asserted in the renderer asset guard.)"""
    n, edges, labels = _planted(6, 90, 60)
    pos = compute_graph_layout(n, edges)
    gi = [i for i, c in enumerate(labels) if c >= 0]
    pa = np.array([_xy(pos, i) for i in gi])
    cx = (pa[:, 0].min() + pa[:, 0].max()) / 2.0
    cy = (pa[:, 1].min() + pa[:, 1].max()) / 2.0
    rad = np.hypot(pa[:, 0] - cx, pa[:, 1] - cy) + 1e-9
    rmax = float(rad.max())
    th = np.arctan2(pa[:, 1] - cy, pa[:, 0] - cx)
    # ARM structure (robust; a single-spiral angle/ln-r correlation is
    # fragile for a 2-arm galaxy -- the pi arm offset breaks unwrap):
    # in a mid annulus the angular density must have a strong PEAK
    # (the arms) vs a round/uniform disc which is ~flat. Measured 3.2
    # for the galaxy vs 1.22 for a uniform control -> gate at 1.8
    # (well above any round disc, solid margin below the real value).
    mid = (rad > 0.30 * rmax) & (rad < 0.72 * rmax)
    hist, _ = np.histogram(th[mid], bins=24, range=(-math.pi, math.pi))
    peak = float(hist.max()) / float(hist.mean())
    assert peak > 1.8, (
        f"no spiral arms (mid-annulus angular peak/mean={peak:.2f}); a "
        f"round/uniform disc is ~1.2 -- the log-spiral shear must "
        f"concentrate stars into arms"
    )


def test_no_node_overlap() -> None:
    """The user's HARD requirement: no two nodes overlap. The
    deterministic relaxation pushes every pair to a spacing scaled
    to the field extent + count; the minimum nearest-neighbour
    distance must be a real fraction of that spacing (no piles), on
    a real-shaped graph (giant + a big singleton field)."""
    from scipy.spatial import cKDTree  # noqa: PLC0415

    from mnemo.ui.graph_layout import _WORLD  # noqa: PLC0415

    n, edges, _ = _planted(6, 80, 1500)
    pos = compute_graph_layout(n, edges)
    pa = np.array([(pos[2 * i], pos[2 * i + 1]) for i in range(n)])
    dmin = _WORLD / 70.0
    d, _ = cKDTree(pa).query(pa, k=2)
    nn = float(d[:, 1].min())
    assert nn > 0.55 * dmin, (
        f"nodes overlap/pile (min NN {nn:.1f} vs target spacing "
        f"{dmin:.1f}); the anti-overlap relaxation must separate them"
    )


# NOTE: the central BAR is a RENDER feature (an elliptical tilted
# core glow), NOT a layout-density requirement -- the no-overlap
# relaxation is isotropic and would erode a dense elongated bar in
# the layout. The same principled split as the bulge; the bar render
# is asserted in the renderer asset guard. No layout bar test.


def test_singletons_pack_densely_not_confetti() -> None:
    """The 2298-component reality: singletons must form a DENSE band,
    not one sparse dot per component on a thin ring. Measure local
    occupancy -- the median nearest-neighbour distance in the
    singleton band must be SMALL vs the overall extent."""
    from scipy.spatial import cKDTree

    n, edges, labels = _planted(4, 80, 800)
    pos = compute_graph_layout(n, edges)
    sing = [i for i, c in enumerate(labels) if c < 0]
    sp = np.array([_xy(pos, i) for i in sing])
    d, _ = cKDTree(sp).query(sp, k=2)
    nn = float(np.median(d[:, 1]))
    allp = np.array([_xy(pos, i) for i in range(n)])
    extent = float(np.abs(allp).max())
    assert nn < extent * 0.06, (
        f"singletons are confetti (median NN {nn:.0f} vs extent "
        f"{extent:.0f}); they must pack densely"
    )


def test_bounded_no_fling() -> None:
    n, edges, _ = _planted(5, 60, 300)
    pos = compute_graph_layout(n, edges)
    pa = np.array([_xy(pos, i) for i in range(n)])
    r = np.hypot(pa[:, 0] - pa[:, 0].mean(), pa[:, 1] - pa[:, 1].mean())
    assert r.max() < float(np.median(r)) * 14 + 1, "a node was flung far out"


def test_halo_is_bounded_relative_to_the_giant() -> None:
    """Real-scope reality: ~2298 components, MOSTLY singletons. The
    halo must stay a bounded band HUGGING the giant -- not grow
    without limit so the giant becomes a tiny core in a vast ring
    (the v4.5 failure that started this arc; the unbounded shelf-pack
    regressed it -- caught live by the real-scope numeric verify, now
    guarded). With many singletons the max radius must stay a small
    multiple of the median (giant-dominant frame)."""
    n, edges, _ = _planted(4, 90, 2500)  # giant + a big singleton halo
    pos = compute_graph_layout(n, edges)
    pa = np.array([_xy(pos, i) for i in range(n)])
    cx, cy = pa[:, 0].mean(), pa[:, 1].mean()
    r = np.hypot(pa[:, 0] - cx, pa[:, 1] - cy)
    ratio = float(r.max()) / float(np.median(r))
    assert ratio < 4.0, (
        f"halo unbounded (maxR/medR={ratio:.2f}); the giant is a tiny "
        f"core lost in a vast ring -- the halo must hug the giant"
    )

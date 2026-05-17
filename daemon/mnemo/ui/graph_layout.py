"""Server-side Nebula layout (v4.6 custom engine).

ForceAtlas2 with the LinLog energy model + outbound-attraction-
distribution + global particle-mesh repulsion (``_repel`` -- every node
is repelled by every region; the kNN-16 of v4.5.x was the blob cause)
+ weak NON-strong gravity + full-extent normalize. Small components and
singletons are laid out then DENSELY bin-packed into a band hugging the
giant (never one-dot-per-component confetti).

Deterministic (fixed seed; no other RNG) so the same graph yields a
byte-identical layout -- cacheable + stable. Signature unchanged from
v4.5: ``compute_graph_layout(n, edges) -> [x0, y0, x1, y1, ...]`` in
the caller's node order (the /ui/graph-data element order), so the
client applies it index-aligned with zero coordination.
"""

from __future__ import annotations

import math
import random

import numpy as np

from mnemo.ui._repel import repulsion

_SEED = 42
_ITERS = 260  # FA2 converges faster than FR; offline + cached
_GRAVITY = 0.05  # weak, NON-strong
_SCALE = 14.0  # ForceAtlas2 repulsion scaling ratio
_WORLD = 2000.0  # post-normalize target half-extent
_SMALL_ITERS = 70  # iters for a tiny component


def _components(n: int, edges: list[tuple[int, int]]):
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    if not edges:
        return np.zeros(n, dtype=np.int64), -1
    r = np.fromiter((e[0] for e in edges), np.int64, len(edges))
    c = np.fromiter((e[1] for e in edges), np.int64, len(edges))
    d = np.ones(len(edges) * 2, np.int8)
    adj = coo_matrix(
        (d, (np.concatenate([r, c]), np.concatenate([c, r]))), shape=(n, n)
    ).tocsr()
    ncomp, lab = connected_components(adj, directed=False)
    if ncomp <= 1:
        return lab, int(lab[0]) if n else -1
    return lab, int(np.bincount(lab, minlength=ncomp).argmax())


def _layout_component(
    pos: np.ndarray, edges_arr: np.ndarray, deg: np.ndarray, iters: int
) -> np.ndarray:
    """ForceAtlas2 + LinLog on one component. ``pos`` (m,2) is mutated
    and returned. ``edges_arr`` is (E,2) of LOCAL indices."""
    m = pos.shape[0]
    if m <= 1:
        return pos
    mass = deg + 1.0
    if edges_arr.size:
        ea = edges_arr[:, 0]
        eb = edges_arr[:, 1]
        src_mass = mass[ea]  # outbound-attraction distribution
    for it in range(iters):
        disp = repulsion(pos, mass, _SCALE)
        # LinLog attraction F = log(1 + d), divided by source mass so
        # hubs are pushed to the periphery, not piled in the centre.
        if edges_arr.size:
            d = pos[ea] - pos[eb]
            dist = np.sqrt(np.einsum("ij,ij->i", d, d)) + 1e-9
            fa = (np.log1p(dist) / dist / src_mass)[:, None] * d
            np.add.at(disp, ea, -fa)
            np.add.at(disp, eb, fa)
        # weak gravity toward the centroid (NOT strong gravity -- that
        # is what crushed the v4.5.x core into a blob).
        ctr = pos.mean(axis=0)
        g = ctr - pos
        gd = np.sqrt(np.einsum("ij,ij->i", g, g)) + 1e-9
        disp += g / gd[:, None] * (_GRAVITY * mass)[:, None]
        # adaptive cooling: bounded step that decays over the run.
        dlen = np.sqrt(np.einsum("ij,ij->i", disp, disp)) + 1e-9
        cap = _WORLD * 0.10 * (1.0 - it / iters) + 1.0
        pos += disp / dlen[:, None] * np.minimum(dlen, cap)[:, None]
    return pos


def _normalize_full_extent(pos: np.ndarray) -> np.ndarray:
    """Centre at origin, scale by the FULL extent (NOT p95 -- the
    p95-normalize crushed the bulk into a few hundred px = a blob
    cause) so the whole drawing fits ``_WORLD``."""
    pos -= (pos.max(axis=0) + pos.min(axis=0)) / 2.0
    ext = float(np.abs(pos).max()) or 1.0
    return pos * (_WORLD / ext)


def _grid_pack(
    boxes: list[tuple[float, float]], anchor_r: float, rng: random.Random
) -> list[tuple[float, float]]:
    """Deterministic shelf bin-pack of (w,h) component boxes into a
    DENSE band of concentric shelves starting at radius ``anchor_r``.
    Returns a centre (cx,cy) per box. Dense + textured -- never the
    one-dot-per-component confetti / sparse ring of v4.5.x."""
    placed: list[tuple[float, float]] = []
    r = max(anchor_r, 1.0)
    circ = 2.0 * math.pi * r
    x = 0.0
    shelf_h = 0.0
    for w, h in boxes:
        if x + w > circ and x > 0.0:  # next outer shelf
            r += shelf_h * 1.1 + 6.0
            circ = 2.0 * math.pi * r
            x = 0.0
            shelf_h = 0.0
        a = (x + w / 2.0) / r  # arc length -> angle
        jit = (rng.random() - 0.5) * 5.0
        rr = r + jit
        placed.append((math.cos(a) * rr, math.sin(a) * rr))
        x += w + 5.0
        shelf_h = max(shelf_h, h)
    return placed


def _clean_edges(n: int, edges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for a, b in edges:
        if a == b or not (0 <= a < n) or not (0 <= b < n):
            continue
        k = (a, b) if a < b else (b, a)
        if k in seen:
            continue
        seen.add(k)
        out.append((a, b))
    return out


def compute_graph_layout(n: int, edges: list[tuple[int, int]]) -> list[float]:
    if n <= 0:
        return []
    if n == 1:
        return [0.0, 0.0]

    rng = np.random.default_rng(_SEED)
    pos = np.zeros((n, 2), np.float64)
    clean = _clean_edges(n, edges)

    deg = np.zeros(n, np.float64)
    for a, b in clean:
        deg[a] += 1.0
        deg[b] += 1.0

    lab, giant = _components(n, clean)
    gmask = lab == giant if giant >= 0 else np.zeros(n, bool)
    gidx = np.nonzero(gmask)[0]

    giant_r = _WORLD * 0.5
    if gidx.size:
        remap = {int(o): k for k, o in enumerate(gidx)}
        ge = np.array(
            [(remap[a], remap[b]) for a, b in clean if gmask[a] and gmask[b]],
            np.int64,
        ).reshape(-1, 2)
        ang = rng.uniform(0, 2 * math.pi, gidx.size)
        rad = np.sqrt(rng.uniform(0, 1, gidx.size)) * (_WORLD * 0.5)
        gp = np.column_stack([np.cos(ang) * rad, np.sin(ang) * rad])
        gp = _layout_component(gp, ge, deg[gidx], _ITERS)
        gp = _normalize_full_extent(gp)
        pos[gidx] = gp
        giant_r = float(np.abs(gp).max()) or giant_r

    # other components: lay each out tiny, collect its bounding box,
    # then DENSE grid-pack the boxes into a band hugging the giant.
    prng = random.Random(_SEED + 1)
    others: dict[int, list[int]] = {}
    for i in range(n):
        if not gmask[i]:
            others.setdefault(int(lab[i]), []).append(i)
    groups = sorted(others.values(), key=len, reverse=True)

    boxes: list[tuple[float, float]] = []
    blobs: list[np.ndarray] = []
    for members in groups:
        m = len(members)
        if m == 1:
            boxes.append((9.0, 9.0))
            blobs.append(np.zeros((1, 2), np.float64))
            continue
        rmap = {o: k for k, o in enumerate(members)}
        ce = np.array(
            [(rmap[a], rmap[b]) for a, b in clean if a in rmap and b in rmap],
            np.int64,
        ).reshape(-1, 2)
        a0 = rng.uniform(0, 2 * math.pi, m)
        sp = np.column_stack([np.cos(a0), np.sin(a0)]) * (7.0 + m)
        sp = _layout_component(sp, ce, deg[members], _SMALL_ITERS)
        sp -= (sp.max(axis=0) + sp.min(axis=0)) / 2.0
        blobs.append(sp)
        wh = sp.max(axis=0) - sp.min(axis=0) + 10.0
        boxes.append((float(wh[0]), float(wh[1])))

    if groups:
        centres = _grid_pack(boxes, giant_r * 1.12, prng)
        for members, blob, (cx, cy) in zip(groups, blobs, centres, strict=True):
            for k, node in enumerate(members):
                pos[node] = (cx + blob[k, 0], cy + blob[k, 1])

    out = [0.0] * (2 * n)
    for i in range(n):
        out[2 * i] = float(pos[i, 0])
        out[2 * i + 1] = float(pos[i, 1])
    return out

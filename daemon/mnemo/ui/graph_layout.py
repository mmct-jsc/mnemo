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
_ITERS = 120  # FA2 declutter on top of the spectral embedding
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


_KS = 0.1  # FA2 per-node speed constant
_KS_MAX = 10.0  # FA2 per-node speed cap
_TAU = 1.0  # FA2 jitter tolerance


def _spectral(m: int, edges_arr: np.ndarray) -> np.ndarray:
    """Spectral embedding: the 2 non-trivial eigenvectors of the
    normalized graph Laplacian. Low-frequency Laplacian eigenvectors
    are community indicators (this is exactly what spectral clustering
    uses), so nodes in the same community get nearby coordinates --
    communities separate BY CONSTRUCTION, with no force-balance tuning
    (the trap that kept producing a uniform blob). Deterministic via a
    fixed starting vector + sign canonicalisation."""
    from scipy.sparse import csr_matrix, diags, identity
    from scipy.sparse.linalg import eigsh

    if edges_arr.size == 0 or m < 3:
        a = np.linspace(0.0, 2.0 * math.pi, m, endpoint=False)
        return np.column_stack([np.cos(a), np.sin(a)]) * 100.0
    ii = np.concatenate([edges_arr[:, 0], edges_arr[:, 1]])
    jj = np.concatenate([edges_arr[:, 1], edges_arr[:, 0]])
    a_mat = csr_matrix((np.ones(ii.shape[0]), (ii, jj)), shape=(m, m))
    deg = np.asarray(a_mat.sum(axis=1)).ravel()
    deg[deg == 0.0] = 1.0
    dinv = diags(1.0 / np.sqrt(deg))
    lap = identity(m) - dinv @ a_mat @ dinv
    k = min(4, m - 1)
    v0 = np.full(m, 1.0 / math.sqrt(m), np.float64)  # deterministic
    try:
        vals, vecs = eigsh(lap, k=k, sigma=1e-6, which="LM", v0=v0, maxiter=4000)
    except Exception:
        try:
            vals, vecs = eigsh(lap, k=k, which="SM", v0=v0, maxiter=8000)
        except Exception:
            a0 = np.linspace(0.0, 2.0 * math.pi, m, endpoint=False)
            return np.column_stack([np.cos(a0), np.sin(a0)]) * 100.0
    order = np.argsort(vals)
    vecs = vecs[:, order]
    xy = np.array(vecs[:, 1:3], np.float64)  # drop the trivial vector
    if xy.shape[1] < 2:  # tiny / degenerate spectrum
        a0 = np.linspace(0.0, 2.0 * math.pi, m, endpoint=False)
        return np.column_stack([np.cos(a0), np.sin(a0)]) * 100.0
    for c in range(2):  # eigenvector sign is arbitrary -> canonicalise
        if xy[np.argmax(np.abs(xy[:, c])), c] < 0.0:
            xy[:, c] = -xy[:, c]
    return xy * 1000.0


def _layout_component(
    pos: np.ndarray, edges_arr: np.ndarray, deg: np.ndarray, iters: int
) -> np.ndarray:
    """ForceAtlas2 (LinLog variant) on one component. ``pos`` (m,2) is
    mutated and returned. ``edges_arr`` is (E,2) of LOCAL indices.

    Uses the FAITHFUL FA2 adaptive-speed integrator (global speed from
    swing/traction + a per-node speed). A fixed cooling cap is NOT FA2
    -- it just freezes a near-random spread (the layout never descends
    the LinLog energy, so communities never separate). The adaptive
    speed is scale-invariant and is what makes clusters form.
    """
    m = pos.shape[0]
    if m <= 1:
        return pos
    mass = deg + 1.0
    has_e = edges_arr.size > 0
    if has_e:
        ea = edges_arr[:, 0]
        eb = edges_arr[:, 1]
        src_mass = mass[ea]  # outbound-attraction distribution
    prev_f = np.zeros((m, 2), np.float64)
    speed = 1.0
    for it in range(iters):
        f = repulsion(pos, mass, _SCALE)
        # LinLog attraction F = log(1 + d), divided by source mass so
        # hubs are pushed to the periphery, not piled in the centre.
        if has_e:
            d = pos[ea] - pos[eb]
            dist = np.sqrt(np.einsum("ij,ij->i", d, d)) + 1e-9
            fa = (np.log1p(dist) / dist / src_mass)[:, None] * d
            np.add.at(f, ea, -fa)
            np.add.at(f, eb, fa)
        # weak gravity toward the origin (NOT strong gravity -- strong
        # gravity is what crushed the v4.5.x core into a blob).
        gd = np.sqrt(np.einsum("ij,ij->i", pos, pos)) + 1e-9
        f -= pos / gd[:, None] * (_GRAVITY * mass)[:, None]
        # --- FA2 adaptive speed (the real integrator) ---
        df = f - prev_f
        sf = f + prev_f
        swing_i = np.sqrt(np.einsum("ij,ij->i", df, df))
        tract_i = np.sqrt(np.einsum("ij,ij->i", sf, sf)) / 2.0
        swing = float((mass * swing_i).sum())
        tract = float((mass * tract_i).sum())
        if swing > 1e-9:
            gs = _TAU * tract / swing
            speed = gs if it == 0 else min(gs, 1.5 * speed)
        si = _KS * speed / (1.0 + speed * np.sqrt(swing_i) + 1e-9)
        fmag = np.sqrt(np.einsum("ij,ij->i", f, f)) + 1e-9
        si = np.minimum(si, _KS_MAX / fmag)
        pos += f * si[:, None]
        prev_f = f
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
        # community-separating spectral embedding -> FA2 declutter.
        gp = _spectral(gidx.size, ge)
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
        sp = _spectral(m, ce)
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

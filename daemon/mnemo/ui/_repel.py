"""Vectorised global repulsion for the v4.6 ForceAtlas2 layout.

The v4.5.x blob was caused by k-NN-16-only repulsion: distant
communities exerted ZERO mutual force, so nothing pushed clusters
apart. The fix is a force where *every node is repelled by every
region*. A pure-Python recursive Barnes-Hut quadtree would be minutes
at 11k x 320 iterations (it re-runs on every reindex -> a real UX
regression). This is the same intent done as a **particle-mesh**:

* far field -- a fixed C x C grid; each occupied cell is a supernode
  (mass + centre of mass); every node is repelled by every occupied
  cell. O(n . occupied_cells), occupied_cells <= C^2 (bounded), fully
  numpy-vectorised.
* near field -- the coarse self-cell term is removed and replaced by
  EXACT pairwise repulsion within the local neighbourhood via a
  cKDTree pair query (accuracy where it matters).

Deterministic (no RNG; bincount / cKDTree / numpy are stable) so the
layout stays byte-identical and cacheable.
"""

from __future__ import annotations

import numpy as np

_GRID = 48  # fixed far-field resolution -> bounded cost at any n


def repulsion(pos: np.ndarray, mass: np.ndarray, scale: float) -> np.ndarray:
    """ForceAtlas2 degree-weighted repulsion. ``pos`` (n,2), ``mass``
    (n,) = deg+1. Returns the (n,2) displacement contribution."""
    n = pos.shape[0]
    disp = np.zeros((n, 2), np.float64)
    if n <= 1:
        return disp

    mn = pos.min(axis=0)
    span = pos.max(axis=0) - mn
    span[span == 0.0] = 1.0

    gi = np.clip(((pos - mn) / span * _GRID).astype(np.int64), 0, _GRID - 1)
    cell = gi[:, 0] * _GRID + gi[:, 1]
    ncell = _GRID * _GRID
    cmass = np.bincount(cell, weights=mass, minlength=ncell)
    csx = np.bincount(cell, weights=pos[:, 0] * mass, minlength=ncell)
    csy = np.bincount(cell, weights=pos[:, 1] * mass, minlength=ncell)
    nz = cmass > 0.0
    comx = np.zeros(ncell, np.float64)
    comy = np.zeros(ncell, np.float64)
    comx[nz] = csx[nz] / cmass[nz]
    comy[nz] = csy[nz] / cmass[nz]

    occ = np.nonzero(nz)[0]
    ocx, ocy, om = comx[occ], comy[occ], cmass[occ]
    px, py = pos[:, 0], pos[:, 1]
    # every node <- every occupied cell supernode (bounded loop, the
    # body is vectorised over all n nodes).
    for k in range(occ.shape[0]):
        dx = px - ocx[k]
        dy = py - ocy[k]
        d2 = dx * dx + dy * dy + 1e-9
        f = scale * mass * om[k] / d2
        disp[:, 0] += dx * f
        disp[:, 1] += dy * f
    # remove the inaccurate self-cell coarse term; the exact
    # short-range pass below replaces it.
    sx, sy, sm = comx[cell], comy[cell], cmass[cell]
    dx = px - sx
    dy = py - sy
    d2 = dx * dx + dy * dy + 1e-9
    fs = scale * mass * sm / d2
    disp[:, 0] -= dx * fs
    disp[:, 1] -= dy * fs

    # exact pairwise repulsion within ~1.5 cell widths (local accuracy)
    from scipy.spatial import cKDTree

    radius = float((span / _GRID).max()) * 1.5
    pairs = cKDTree(pos).query_pairs(r=radius, output_type="ndarray")
    if pairs.size:
        a = pairs[:, 0]
        b = pairs[:, 1]
        d = pos[a] - pos[b]
        d2 = np.einsum("ij,ij->i", d, d) + 1e-9
        fe = scale * mass[a] * mass[b] / d2
        fv = d / np.sqrt(d2)[:, None] * fe[:, None]
        np.add.at(disp, a, fv)
        np.add.at(disp, b, -fv)
    return disp

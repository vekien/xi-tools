#!/usr/bin/env python3
"""Bake per-vertex ambient occlusion into a zone mesh's vertex colours.

Custom GLB imports arrive with NO per-vertex colour, so the encoder writes a flat
neutral ``0x80`` everywhere (see ``xi_mesh.encode_zone_mesh_section``). In-game the
client lights a zone surface as

    out.rgb = 2 · vColor · (ambient + Σ diffuseₖ(N)) · tex.rgb            (xim port)

so vColor is a *modulation* on top of the live ambient+sun/moon light. A flat
``0x80`` (vColor = 0.5) means "fully lit, no self-shadow": with the ambient term
clamped to 0.5 the surface can never fall below ~ambient brightness, and a near-
white texture then reads as a glowing slab with no form — exactly what a flat
import looks like next to retail geometry, whose vertex colours carry **baked
ambient occlusion** (real Lower Jeuno meshes spread across luminance ~48–252, not
a flat 128).

This module reproduces that baked AO from the mesh's own geometry: for each unique
vertex it casts a cosine-weighted hemisphere of rays around the normal and measures
how much nearby geometry blocks them. Open surfaces stay near neutral; creases,
undersides and contact areas darken. The result is written to ``prim.colors`` so
the existing encoder maps it to the half-scale BGRA the engine expects.

NumPy-vectorised Möller–Trumbore against the whole triangle set per ray; deterministic
(Fibonacci hemisphere) so a given mesh always bakes identically.
"""

import math
from typing import List, Optional, Tuple

import numpy as np

from xi.zone.xi_export import ZonePrimitive


def _fibonacci_hemisphere(n: int) -> np.ndarray:
    """``n`` directions on the +Z unit hemisphere, cosine-weighted (uniform in z so
    samples concentrate toward the normal, matching a Lambertian AO integral)."""
    i = np.arange(n) + 0.5
    z = i / n                      # 0..1  -> cosine weighting w.r.t. the +Z normal
    r = np.sqrt(np.maximum(0.0, 1.0 - z * z))
    phi = i * (math.pi * (3.0 - math.sqrt(5.0)))   # golden angle
    return np.stack([r * np.cos(phi), r * np.sin(phi), z], axis=1)   # (n,3)


def _orient_to_normal(samples: np.ndarray, n: np.ndarray) -> np.ndarray:
    """Rotate +Z-hemisphere ``samples`` (n,3) so +Z aligns with unit normal ``n``."""
    helper = np.array([0.0, 0.0, 1.0]) if abs(n[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    t = np.cross(helper, n)
    t /= (np.linalg.norm(t) + 1e-12)
    b = np.cross(n, t)
    # columns = basis (t, b, n); world = samples · [t b n]^T
    basis = np.stack([t, b, n], axis=1)            # (3,3)
    return samples @ basis.T


def _gather_triangles(prims: List[ZonePrimitive]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Flatten every prim's triangle soup into (V0, E1, E2) arrays for Möller–Trumbore."""
    v0, e1, e2 = [], [], []
    for p in prims:
        P = p.positions
        for t in range(0, len(P) - 2, 3):
            a = np.array(P[t], dtype=np.float64)
            b = np.array(P[t + 1], dtype=np.float64)
            c = np.array(P[t + 2], dtype=np.float64)
            v0.append(a); e1.append(b - a); e2.append(c - a)
    if not v0:
        empty = np.zeros((0, 3))
        return empty, empty, empty
    return np.array(v0), np.array(e1), np.array(e2)


def bake_vertex_ao(prims: List[ZonePrimitive], *, rays: int = 48,
                   max_dist_frac: float = 0.5, floor: float = 0.45,
                   strength: float = 1.0, overwrite: bool = False,
                   fill: float = 0.0, base: float = 0.42) -> int:
    """Bake ambient occlusion into ``prim.colors`` for every prim in ``prims`` (in place).

    rays           hemisphere samples per vertex (more = smoother, slower).
    max_dist_frac  occluders past this fraction of the mesh bbox diagonal are ignored,
                   so AO stays local (a far wall doesn't black out the whole model).
    floor          darkest AO multiplier (fully occluded vertex). 1.0 = no darkening,
                   0.45 ≈ retail's deepest baked creases.
    strength       0..1 blend between flat-neutral (0) and full AO (1).
    overwrite      bake even over prims that already carry GLB vertex colours
                   (default False: authored colours win).

    fill           if > 0, FILL mode instead of darken: each vertex colour is
                   ``base · (1 + fill·occlusion)`` where occlusion = 1-AO. Occluded
                   verts (undersides/bellies/creases) get BRIGHTER vColor to offset the
                   directional sun they never catch, flattening the harsh top-bright /
                   bottom-black gradient (which vColor/texture scaling alone can't touch).
    base           open-face vColor level in fill mode (0..1, 1.0 = 0x80 neutral).

    Returns the number of vertices shaded. RGB gets the multiplier; alpha stays 1.0
    (transparency is the texture's job)."""
    targets = [p for p in prims if p.positions and (overwrite or not p.colors)]
    if not targets:
        return 0

    V0, E1, E2 = _gather_triangles(prims)
    if len(V0) == 0:
        return 0

    # Mesh scale → local occlusion cutoff.
    allP = np.array([pt for p in prims for pt in p.positions], dtype=np.float64)
    diag = float(np.linalg.norm(allP.max(axis=0) - allP.min(axis=0))) or 1.0
    max_dist = diag * max_dist_frac
    eps = diag * 1e-4                       # ray origin offset to dodge self-hit

    base_dirs = _fibonacci_hemisphere(rays)
    shaded = 0

    for prim in targets:
        P = prim.positions
        N = prim.normals
        # Dedup corners by quantised position+normal so shared verts bake once.
        uniq: dict = {}
        corner_key: List[Tuple] = []
        for idx in range(len(P)):
            key = (round(P[idx][0], 3), round(P[idx][1], 3), round(P[idx][2], 3))
            corner_key.append(key)
            if key not in uniq:
                uniq[key] = [np.array(P[idx], dtype=np.float64),
                             np.array(N[idx], dtype=np.float64)]
            else:
                uniq[key][1] += np.array(N[idx], dtype=np.float64)   # average normals

        ao_by_key: dict = {}
        for key, (pos, nrm) in uniq.items():
            nlen = np.linalg.norm(nrm)
            nrm = nrm / nlen if nlen > 1e-9 else np.array([0.0, 1.0, 0.0])
            origin = pos + nrm * eps
            dirs = _orient_to_normal(base_dirs, nrm)              # (rays,3)
            hits = 0
            for d in dirs:
                pvec = np.cross(d, E2)                            # (T,3)
                det = np.einsum('ij,ij->i', E1, pvec)
                ok = np.abs(det) > 1e-9
                inv = np.zeros_like(det)
                inv[ok] = 1.0 / det[ok]
                tvec = origin - V0
                u = np.einsum('ij,ij->i', tvec, pvec) * inv
                qvec = np.cross(tvec, E1)
                v = (qvec @ d) * inv
                tt = np.einsum('ij,ij->i', E2, qvec) * inv
                hit = ok & (u >= -1e-5) & (u <= 1.0 + 1e-5) & (v >= -1e-5) \
                    & (u + v <= 1.0 + 1e-5) & (tt > eps) & (tt < max_dist)
                if hit.any():
                    hits += 1
            ao_by_key[key] = 1.0 - hits / float(rays)            # 1 = open, 0 = enclosed

        cols: List[Tuple[float, float, float, float]] = []
        for key in corner_key:
            ao = ao_by_key[key]
            if fill > 0.0:
                level = min(1.0, base * (1.0 + fill * (1.0 - ao)))   # lift occluded verts
            else:
                level = floor + (1.0 - floor) * ao                   # floor..1
                level = (1.0 - strength) * 1.0 + strength * level    # blend toward neutral
            cols.append((level, level, level, 1.0))
        prim.colors = cols
        shaded += len(uniq)

    return shaded

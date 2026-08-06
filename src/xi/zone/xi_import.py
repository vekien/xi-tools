#!/usr/bin/env python3
"""Import an edited **GLB** (glTF) back into an FFXI zone DAT — the reverse of
``xi zone export``. Import is GLB-only; FBX is not accepted (export your edits
as GLB from your DCC tool). Reading glTF directly avoids any Blender round-trip.

Capabilities:
  * **Placements** (default): each node's world matrix -> the matching ``0x1C``
    ZoneObject record (position / rotation / scale). Re-encryption round-trips
    byte-for-byte, all edits are in place (no section resize).
  * **--prune**: blank the mesh id of placements whose node was deleted in the
    DCC tool, so the engine skips them (object count unchanged).
  * **--rebuild**: patch mesh vertex positions in place (same topology) by
    snapping each original vertex to the nearest model vertex.
    (vertex-patch + glb-mesh extraction adapted from LoxleyX's glb importer.)
  * **--placement <mesh>**: append NEW placements for extra instances of a mesh.

Coordinates: nodes are recovered relative to the actual ``ffxi_root_correction``
node (``inv(C) * world``), then the axis frame is auto-calibrated against the
original zone (handles DCC-specific conventions like C4D's). Adding *new* objects
is also exposed standalone via ``xi zone add-object`` (Phase 4)."""

import argparse
import math
import struct
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from xi.entity.anim.xi_export import parse_sections
from xi.entity.mesh.xi_export import resolve_dat_path
from xi.entity.mesh.xi_import import load_gltf_document, read_accessor, read_indices
from xi.xi_config import FFXI_DIR, editable_dat, ensure_base
from xi.zone.xi_decrypt import (
    decrypt_zone_mesh,
    decrypt_zone_objects,
    load_key_tables,
    reencrypt_zone_objects,
)
from xi.zone.xi_export import (
    SECTION_TYPE_ZONE_DEF,
    SECTION_TYPE_ZONE_MESH,
    Placement,
    ZonePrimitive,
    default_output_dir,
    parse_zone,
    parse_zone_mesh_section,
    resolve_mesh_name,
    trs_matrix,
)
from xi.zone.xi_zonedef import add_placements
from xi.zone.xi_mesh import encode_zone_mesh_section


# ---------------------------------------------------------------------------
# Column-major 4x4 matrix helpers (glTF convention)
# ---------------------------------------------------------------------------


def _identity() -> List[float]:
    return [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]


def _mat_mul(a: Sequence[float], b: Sequence[float]) -> List[float]:
    """Column-major 4x4 product a * b."""
    out = [0.0] * 16
    for col in range(4):
        for row in range(4):
            out[col * 4 + row] = sum(a[k * 4 + row] * b[col * 4 + k] for k in range(4))
    return out


def _affine_inverse(m: Sequence[float]) -> List[float]:
    """Inverse of a column-major affine 4x4 (invertible 3x3 + translation)."""
    a, b, c = m[0], m[4], m[8]
    d, e, f = m[1], m[5], m[9]
    g, h, i = m[2], m[6], m[10]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    inv = 1.0 / det
    # inverse 3x3 (column-major columns ic0/ic1/ic2)
    ic0 = ((e * i - f * h) * inv, (f * g - d * i) * inv, (d * h - e * g) * inv)
    ic1 = ((c * h - b * i) * inv, (a * i - c * g) * inv, (b * g - a * h) * inv)
    ic2 = ((b * f - c * e) * inv, (c * d - a * f) * inv, (a * e - b * d) * inv)
    tx, ty, tz = m[12], m[13], m[14]
    # translation of inverse = -A^-1 * t
    itx = -(ic0[0] * tx + ic1[0] * ty + ic2[0] * tz)
    ity = -(ic0[1] * tx + ic1[1] * ty + ic2[1] * tz)
    itz = -(ic0[2] * tx + ic1[2] * ty + ic2[2] * tz)
    return [
        ic0[0], ic0[1], ic0[2], 0.0,
        ic1[0], ic1[1], ic1[2], 0.0,
        ic2[0], ic2[1], ic2[2], 0.0,
        itx, ity, itz, 1.0,
    ]


def _quat_to_mat(q: Sequence[float]) -> List[float]:
    """glTF quaternion (x, y, z, w) -> column-major rotation matrix."""
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return [
        1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w), 0,
        2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w), 0,
        2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y), 0,
        0, 0, 0, 1,
    ]


def node_local_matrix(node: dict) -> List[float]:
    """The node's local transform as a column-major 4x4 (matrix or TRS keys)."""
    if "matrix" in node:
        return list(node["matrix"])
    m = _identity()
    if "rotation" in node:
        m = _quat_to_mat(node["rotation"])
    if "scale" in node:
        sx, sy, sz = node["scale"]
        for r in range(3):
            m[0 + r] *= sx
            m[4 + r] *= sy
            m[8 + r] *= sz
    if "translation" in node:
        m[12], m[13], m[14] = node["translation"]
    return m


def decompose_trs(m: Sequence[float]) -> Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]:
    """Decompose a column-major 4x4 into (position, rotationZYX, scale), the exact
    inverse of export.trs_matrix (translate . rotateZYX . scale)."""
    translation = (m[12], m[13], m[14])
    c0 = (m[0], m[1], m[2])
    c1 = (m[4], m[5], m[6])
    c2 = (m[8], m[9], m[10])

    def length(v):
        return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])

    sx, sy, sz = length(c0), length(c1), length(c2)

    # A negative determinant means an odd number of mirror flips; fold it into sx
    # so the rotation stays a proper (right-handed) rotation.
    det = (
        c0[0] * (c1[1] * c2[2] - c1[2] * c2[1])
        - c1[0] * (c0[1] * c2[2] - c0[2] * c2[1])
        + c2[0] * (c0[1] * c1[2] - c0[2] * c1[1])
    )
    if det < 0:
        sx = -sx

    r0 = [c / (sx or 1.0) for c in c0]
    r1 = [c / (sy or 1.0) for c in c1]
    r2 = [c / (sz or 1.0) for c in c2]
    # rotation matrix elements R[row][col]
    R = [
        [r0[0], r1[0], r2[0]],
        [r0[1], r1[1], r2[1]],
        [r0[2], r1[2], r2[2]],
    ]

    cy_len = math.hypot(R[2][1], R[2][2])
    ry = math.atan2(-R[2][0], cy_len)
    if cy_len > 1e-6:
        rx = math.atan2(R[2][1], R[2][2])
        rz = math.atan2(R[1][0], R[0][0])
    else:
        # Gimbal lock: pitch ~ +/-90deg; fold roll into yaw.
        rx = math.atan2(-R[1][2], R[1][1])
        rz = 0.0

    return translation, (rx, ry, rz), (sx, sy, sz)


# ---------------------------------------------------------------------------
# glTF node graph -> world matrices keyed by node name
# ---------------------------------------------------------------------------


def name_key(name: str) -> str:
    """Normalize a node name for matching across the FBX round-trip.

    Blender's FBX exporter sanitizes object names, notably rewriting the instance
    suffix export writes (``id.002``) to ``id_002`` (dot/space/dash -> underscore).
    Collapsing those to one form lets us match whether the model came back as a
    raw .glb (dots intact) or via Blender FBX (dots mangled)."""
    out = name
    for ch in " .-":
        out = out.replace(ch, "_")
    return out


_ROOT_CORRECTION_NODE = "ffxi_root_correction"

# Fallback only: the export's correction maps FFXI world -> display as
# (x,y,z) -> (-x,-y,z) = diag(-1,-1,1) (180deg-X rotation composed with a [-1,1,-1]
# scale). Used when the model has no ffxi_root_correction node (e.g. the user
# re-parented/flattened it). diag(-1,-1,1) is its own inverse, so left-multiplying
# strips it.
_ROOT_CORRECTION = [-1.0, 0, 0, 0, 0, -1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]


def world_matrices_by_name(doc: dict) -> Tuple[Dict[str, List[float]], List[str]]:
    """Return ``(transforms, skipped)``: each named mesh node's transform in **raw
    FFXI space** (the inverse of export.trs_matrix's input), plus the names of mesh
    nodes that were dropped because they sit at the scene root *outside* the
    correction group (not recoverable — see below).

    The export writes every placement's raw TRS as a node parented under an
    ``ffxi_root_correction`` node that supplies the 180deg-X display flip. Through
    the FBX round-trip Blender preserves each node's *world* position but treats
    two cases differently, so recovery is a hybrid keyed on whether the node is
    still inside the correction subtree:

      * Node kept as a child/descendant of the correction (the common case): its
        local TRS is preserved unchanged = the raw FFXI value. We get it back as
        ``inv(C) * W`` (its transform relative to the correction node's actual
        world ``C``). This is exact for descendants no matter how Blender
        re-expressed C itself (we've seen 180deg-Y + scale (-1,-1,-1)).
      * Node at the scene root, *outside* the correction group: some mirrored
        (negative-determinant) objects land here (e.g. ``funsui``) — the fbx
        exporter detaches them and their transform is no longer expressed in the
        correction's frame, so we can't reliably map it back. We **skip** these
        (returned in ``skipped``) and leave their DAT record at its base value
        rather than writing a wrong one. To edit such an object, move it back
        *inside* the ``ffxi_root_correction`` group in your DCC tool — then it
        imports through the (verified-exact) inv(C) path like everything else.

    With no correction node at all (model fully flattened) we fall back to
    stripping the hardcoded original flip from every node's world."""
    nodes = doc.get("nodes", [])
    scenes = doc.get("scenes", [])
    scene = doc.get("scene", 0)
    roots = scenes[scene]["nodes"] if scenes else list(range(len(nodes)))

    worlds: Dict[str, Tuple[List[float], bool]] = {}  # name -> (world, under_correction)
    correction_world: Optional[List[float]] = None

    def visit(index: int, parent_world: List[float], under_correction: bool) -> None:
        nonlocal correction_world
        node = nodes[index]
        world = _mat_mul(parent_world, node_local_matrix(node))
        name = node.get("name")
        is_correction = name == _ROOT_CORRECTION_NODE
        if is_correction and correction_world is None:
            correction_world = world
        # Record the placement node: the one that carries an explicit transform.
        # My export puts mesh+matrix on one node; C4D's glTF nests a transform node
        # (named with the instance suffix) above a transform-less mesh child, so we
        # key on transform presence, not mesh presence — this captures the correctly
        # named placement node in both layouts and avoids transform-less mesh
        # children (same base name) clobbering it.
        has_transform = any(k in node for k in ("matrix", "translation", "rotation", "scale"))
        if name and not is_correction:
            if has_transform:
                worlds[name] = (world, under_correction)          # placement node wins
            elif "mesh" in node and name not in worlds:
                worlds[name] = (world, under_correction)          # identity placement fallback
        for child in node.get("children", []):
            visit(child, world, under_correction or is_correction)

    for root in roots:
        visit(root, _identity(), False)

    out: Dict[str, List[float]] = {}
    skipped: List[str] = []
    if correction_world is not None:
        # Trust only nodes still under the correction (their local TRS = raw FFXI).
        # Scene-root nodes sit outside the correction frame and are not recoverable.
        frame_inv = _affine_inverse(correction_world)
        for name, (world, under_correction) in worlds.items():
            if under_correction:
                out[name] = _mat_mul(frame_inv, world)
            else:
                skipped.append(name)
    else:
        # No correction node: model was flattened — strip the hardcoded flip.
        for name, (world, _under) in worlds.items():
            out[name] = _mat_mul(_ROOT_CORRECTION, world)
    return out, skipped


# ---------------------------------------------------------------------------
# Placement-index -> node-name mapping (replicates export's node naming)
# ---------------------------------------------------------------------------


def placement_node_names(placements: Sequence[Placement], meshes_by_name: Dict) -> Dict[int, str]:
    """Reproduce the exact node names export assigned, mapped to placement index.

    Export only emits a node for placements whose mesh resolves, and numbers
    duplicates per mesh-id (``id``, ``id.002``, ``id.003`` ...). We mirror that so
    each glTF node can be matched back to its ZoneObject record."""
    name_counts: Dict[str, int] = {}
    by_index: Dict[int, str] = {}
    for index, plc in enumerate(placements):
        if resolve_mesh_name(plc.mesh_id, meshes_by_name) is None:
            continue
        name_counts[plc.mesh_id] = name_counts.get(plc.mesh_id, 0) + 1
        count = name_counts[plc.mesh_id]
        by_index[index] = plc.mesh_id if count == 1 else f"{plc.mesh_id}.{count:03d}"
    return by_index


# ---------------------------------------------------------------------------
# Top-level import
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Delete + in-place geometry rebuild
# (vertex-patch / glb-mesh extraction adapted from LoxleyX's glb importer)
# ---------------------------------------------------------------------------


def _zero_placement(data: bytearray, def_data_start: int, index: int) -> None:
    """Blank a placement's mesh id (0x10 bytes) so the engine skips drawing it.
    In-place — the record stays, the object just resolves to no mesh."""
    b = def_data_start + 0x20 + index * 0x64
    data[b : b + 0x10] = b"\x00" * 0x10


def _mesh_vertex_offsets(data: bytes, section) -> List[Tuple[int, int]]:
    """(byte offset, stride) of every vertex position in a *decrypted* 0x2E section."""
    ds = section.data_start
    config = struct.unpack_from("<I", data, ds + 4)[0] & 0xFF
    vertex_blend = (config & 0x2) != 0
    def_start = ds + 0x20
    if struct.unpack_from("<I", data, def_start)[0] == 0:
        return []  # collision/invisible mesh, no geometry
    section1_off = struct.unpack_from("<I", data, ds + 0x3C)[0]
    mesh_count1 = struct.unpack_from("<I", data, ds + 0x40)[0]
    p = def_start + section1_off
    stride = 48 if vertex_blend else 36
    offsets: List[Tuple[int, int]] = []
    for _ in range(mesh_count1):
        p += 0x10  # texture name
        num_verts = struct.unpack_from("<H", data, p)[0]
        p += 4
        for _v in range(num_verts):
            offsets.append((p, stride))
            p += stride
        num_indices = struct.unpack_from("<H", data, p)[0]
        p += 4 + num_indices * 2
        p = (p + 3) & ~3
    return offsets


def _extract_raw_vertices(data: bytes, section) -> List[Tuple[float, float, float]]:
    return [struct.unpack_from("<3f", data, off) for off, _ in _mesh_vertex_offsets(data, section)]


def _patch_vertices_in_place(data: bytearray, section, new_positions: List[Tuple[float, float, float]]) -> int:
    """Overwrite vertex positions (same topology) in a decrypted 0x2E section and
    refresh its bounding box. Returns count patched (0 if vertex count mismatch)."""
    offsets = _mesh_vertex_offsets(data, section)
    if len(new_positions) != len(offsets):
        return 0
    for i, (off, _stride) in enumerate(offsets):
        struct.pack_into("<3f", data, off, *new_positions[i])
    ds = section.data_start
    def_start = ds + 0x20
    xs = [p[0] for p in new_positions]; ys = [p[1] for p in new_positions]; zs = [p[2] for p in new_positions]
    if xs:
        bbox = (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))
        struct.pack_into("<6f", data, def_start + 0x04, *bbox)
        struct.pack_into("<6f", data, def_start + 0x24, *bbox)
    return len(offsets)


def _extract_glb_meshes(doc: dict, buffers) -> Dict[str, List[Tuple[float, float, float]]]:
    """Map glTF mesh name -> flat list of triangle-corner positions (glTF space)."""
    out: Dict[str, List[Tuple[float, float, float]]] = {}
    for gmesh in doc.get("meshes", []):
        name = gmesh.get("name", "")
        positions: List[Tuple[float, float, float]] = []
        for prim in gmesh.get("primitives", []):
            attrs = prim.get("attributes", {})
            if "POSITION" not in attrs:
                continue
            pos = read_accessor(doc, buffers, attrs["POSITION"])
            idx = read_indices(doc, buffers, prim, len(pos))
            for t in range(0, len(idx) - 2, 3):
                for vi in (idx[t], idx[t + 1], idx[t + 2]):
                    positions.append(pos[vi])
        if positions:
            out[name] = positions
    return out


def _rebuild_mesh_vertices(data: bytearray, section, glb_positions: List[Tuple[float, float, float]],
                           to_ffxi_local) -> int:
    """Patch a mesh's vertices to the nearest glb vertex (same topology). The mesh
    keeps its triangle/index structure; only positions move. A spatial hash makes
    the nearest-vertex lookup O(n)."""
    orig = _extract_raw_vertices(data, section)
    if not orig or not glb_positions:
        return 0
    pts = [to_ffxi_local(p) for p in glb_positions]
    cell = 0.5
    grid: Dict[Tuple[int, int, int], List[int]] = {}
    for gi, gp in enumerate(pts):
        grid.setdefault((int(gp[0] // cell), int(gp[1] // cell), int(gp[2] // cell)), []).append(gi)

    def nearest(pos):
        cx, cy, cz = int(pos[0] // cell), int(pos[1] // cell), int(pos[2] // cell)
        best_d, best = float("inf"), pos
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for gi in grid.get((cx + dx, cy + dy, cz + dz), ()):
                        gp = pts[gi]
                        d = (pos[0] - gp[0]) ** 2 + (pos[1] - gp[1]) ** 2 + (pos[2] - gp[2]) ** 2
                        if d < best_d:
                            best_d, best = d, gp
        return best

    return _patch_vertices_in_place(data, section, [nearest(o) for o in orig])


def _frame_candidates() -> List[List[List[float]]]:
    """All 48 signed axis-permutation 3x3 matrices, identity first. A DCC tool's
    glTF exporter may bake an axis convention (e.g. C4D's Y/Z swap) into the scene
    that leaves a uniform frame rotation/flip after we strip the correction node."""
    import itertools
    ident = [[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]]
    out = [ident]
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((1.0, -1.0), repeat=3):
            R = [[0.0, 0, 0], [0, 0, 0], [0, 0, 0]]
            for r in range(3):
                R[r][perm[r]] = signs[r]
            if R != ident:
                out.append(R)
    return out


def _apply_frame(R: List[List[float]], M: Sequence[float]) -> List[float]:
    """Re-express a column-major 4x4 transform in a rotated frame: a change of
    basis ``R · M · Rᵀ`` for the 3x3 (orientation) and ``R · t`` for the
    translation. (A plain left-multiply rotates the translation correctly but
    leaves the orientation wrong.) R is an orthonormal signed-permutation matrix,
    so Rᵀ = R⁻¹."""
    # M 3x3 as A[row][col]
    A = [[M[0], M[4], M[8]], [M[1], M[5], M[9]], [M[2], M[6], M[10]]]
    Rt = [[R[c][r] for c in range(3)] for r in range(3)]
    RA = [[sum(R[i][k] * A[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    B = [[sum(RA[i][k] * Rt[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    t = (M[12], M[13], M[14])
    tx = R[0][0] * t[0] + R[0][1] * t[1] + R[0][2] * t[2]
    ty = R[1][0] * t[0] + R[1][1] * t[1] + R[1][2] * t[2]
    tz = R[2][0] * t[0] + R[2][1] * t[1] + R[2][2] * t[2]
    return [B[0][0], B[1][0], B[2][0], 0.0,
            B[0][1], B[1][1], B[2][1], 0.0,
            B[0][2], B[1][2], B[2][2], 0.0,
            tx, ty, tz, 1.0]


def _calibrate_frame(world_by_name, world_by_key, placements, node_name_by_index, tol: float = 2.0):
    """Find the uniform frame correction (a signed axis permutation) that best maps
    the model's recovered placement positions onto the zone's original positions.
    Unchanged objects vote for the true frame; edited ones simply don't align, so
    the majority wins. Returns identity when the frame already matches (Blender/
    xi exports) — so this is a no-op there and only corrects oddballs like C4D."""
    pairs = []
    for idx, nm in node_name_by_index.items():
        w = world_by_name.get(nm) or world_by_key.get(name_key(nm))
        if w is None:
            continue
        pairs.append(((w[12], w[13], w[14]), placements[idx].position))
    if not pairs:
        return None

    def aligned(R):
        n = 0
        for rec, base in pairs:
            rx = R[0][0] * rec[0] + R[0][1] * rec[1] + R[0][2] * rec[2]
            ry = R[1][0] * rec[0] + R[1][1] * rec[1] + R[1][2] * rec[2]
            rz = R[2][0] * rec[0] + R[2][1] * rec[1] + R[2][2] * rec[2]
            if abs(rx - base[0]) < tol and abs(ry - base[1]) < tol and abs(rz - base[2]) < tol:
                n += 1
        return n

    best_R, best_n = None, -1
    for R in _frame_candidates():  # identity first, so it wins ties (preferred)
        n = aligned(R)
        if n > best_n:
            best_R, best_n = R, n
    # Only trust a non-identity frame if it explains a clear majority of objects.
    if best_R != _frame_candidates()[0] and best_n < 0.5 * len(pairs):
        return None
    return best_R


def _rms_radius(pts) -> float:
    """Root-mean-square distance of points from their centroid. Rotation-invariant
    and linear in uniform scale, so the ratio of two same-topology meshes' RMS
    radii is their uniform size factor."""
    n = len(pts)
    if n == 0:
        return 0.0
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    cz = sum(p[2] for p in pts) / n
    return math.sqrt(sum((p[0] - cx) ** 2 + (p[1] - cy) ** 2 + (p[2] - cz) ** 2 for p in pts) / n)


def _recover_uniform_scales(doc, buffers, meshes_by_name, node_name_by_index, placements):
    """Recover per-instance uniform scale that a DCC tool baked into the mesh
    geometry (C4D's glTF exporter bakes object scale into vertices, leaving the
    node scale at 1). For each placement node we compare its glTF mesh's RMS radius
    to the original 0x2E mesh's; the ratio, normalized by the global factor of the
    unchanged objects, is the user's scale. Returns {node_name: factor} (only
    entries meaningfully != 1). No-op for Blender (scale stays on the node)."""
    nodes = doc.get("nodes", [])
    meshes = doc.get("meshes", [])

    def subtree_points(index):
        n = nodes[index]
        pts = []
        if "mesh" in n:
            for pr in meshes[n["mesh"]].get("primitives", []):
                a = pr.get("attributes", {})
                if "POSITION" in a:
                    pts.extend(read_accessor(doc, buffers, a["POSITION"]))
        for c in n.get("children", []):
            pts.extend(subtree_points(c))
        return pts

    # node name -> index (transform-bearing placement nodes; last wins, matching
    # world_matrices_by_name's keying)
    index_by_name = {}
    for i, n in enumerate(nodes):
        name = n.get("name")
        if name and name != _ROOT_CORRECTION_NODE and any(k in n for k in ("matrix", "translation", "rotation", "scale")):
            index_by_name[name] = i

    base_radii = {name: _rms_radius([p for prim in prims for p in prim.positions])
                  for name, prims in meshes_by_name.items()}

    ratios = {}  # node_name -> glb/base radius
    for idx, node_name in node_name_by_index.items():
        i = index_by_name.get(node_name)
        if i is None:
            continue
        resolved = resolve_mesh_name(placements[idx].mesh_id, meshes_by_name)
        br = base_radii.get(resolved, 0.0)
        if br <= 1e-6:
            continue
        gr = _rms_radius(subtree_points(i))
        if gr > 1e-6:
            ratios[node_name] = gr / br
    if not ratios:
        return {}

    # Global factor = the unchanged majority (median is robust to scaled outliers).
    vals = sorted(ratios.values())
    global_factor = vals[len(vals) // 2]
    if global_factor <= 1e-6:
        return {}

    factors = {}
    for node_name, ratio in ratios.items():
        f = ratio / global_factor
        if abs(f - 1.0) > 0.01:  # ignore float noise; only real rescales
            factors[node_name] = f
    return factors


def _maps_to_mesh(node_name: str, mesh_id: str) -> bool:
    """True if a model node name is an instance of mesh_id — mesh_id followed only
    by an instance marker: digits / '_' / '.' (e.g. funsui, funsui2, funsui.003,
    funsui_2) or a DCC instance word (e.g. 'funsui Instance', 'funsui copy 2').
    Avoids matching genuinely different meshes like 'funsui_water'."""
    nk, mk = name_key(node_name).lower(), name_key(mesh_id).lower()
    if not nk.startswith(mk):
        return False
    rest = nk[len(mk):].lstrip("_").rstrip("0123456789_")
    return rest == "" or rest in ("instance", "copy", "clone")


# ---------------------------------------------------------------------------
# Mesh-merge: grow an existing object's 0x2E mesh with edited/added geometry.
# An object whose glb geometry changed (e.g. extra copies merged into it in the
# DCC tool) is re-serialized from the glb so the engine renders the new geometry
# as part of that already-placed object — sidesteps the engine ignoring brand
# new placements. Verts are taken verbatim from the glb and expressed in the
# object's mesh-local frame: inv(P) . frame . inv(C) . W . v (P = the object's
# placement, C = correction node, frame = calibrated axis fix). Winding is
# reversed (glTF RH -> FFXI LH) and normals recomputed from geometry, so the
# DCC tool's normal/winding state is irrelevant.
# ---------------------------------------------------------------------------

def _world_resolver(doc: dict):
    """Return (nodes, world_of) where world_of(index) is the node's full
    scene-graph world matrix (includes the correction node)."""
    nodes = doc.get("nodes", [])
    parent: Dict[int, int] = {}
    for i, n in enumerate(nodes):
        for c in n.get("children", []):
            parent[c] = i

    def world_of(index: int) -> List[float]:
        chain = []
        j: Optional[int] = index
        while j is not None:
            chain.append(j)
            j = parent.get(j)
        m = _identity()
        for j in reversed(chain):
            m = _mat_mul(m, node_local_matrix(nodes[j]))
        return m

    return nodes, world_of


def _correction_inverse(doc: dict, world_of) -> List[float]:
    ci = next((i for i, n in enumerate(doc.get("nodes", []))
               if n.get("name") == _ROOT_CORRECTION_NODE), None)
    return _affine_inverse(world_of(ci)) if ci is not None else _identity()


def _node_mesh_name(node_name: str, mesh_names) -> Optional[str]:
    """Map a glb node name to the DAT mesh it belongs to (exact name preferred,
    then the instance-aware _maps_to_mesh)."""
    raw = (node_name or "").split(".")[0]  # drop Blender '.001' dedup suffix
    if node_name in mesh_names:
        return node_name
    if raw in mesh_names:
        return raw
    return next((m for m in mesh_names if _maps_to_mesh(node_name, m)), None)


def _glb_tri_counts(doc, buffers, mesh_names) -> Dict[str, int]:
    """Largest single-node triangle count per DAT mesh. We take the MAX across
    nodes (not the sum): a mesh placed N times yields N nodes each with the base
    tri count, so summing would falsely flag it as grown — the max stays equal to
    the base. A genuinely merged object has one node whose count exceeds the base."""
    counts: Dict[str, int] = {}
    nodes = doc.get("nodes", [])
    for n in nodes:
        mi = n.get("mesh")
        if mi is None:
            continue
        nm = _node_mesh_name(n.get("name", ""), mesh_names)
        if nm is None:
            continue
        t = 0
        for pr in doc["meshes"][mi].get("primitives", []):
            a = pr.get("attributes", {})
            if "POSITION" not in a:
                continue
            npos = len(read_accessor(doc, buffers, a["POSITION"]))
            t += len(read_indices(doc, buffers, pr, npos)) // 3
        counts[nm] = max(counts.get(nm, 0), t)
    return counts


def _dat_tri_counts(base_path: Path, t1: bytes, t2: bytes) -> Dict[str, int]:
    data = bytearray(base_path.read_bytes())
    out: Dict[str, int] = {}
    for s in parse_sections(data):
        if s.type_code != SECTION_TYPE_ZONE_MESH:
            continue
        decrypt_zone_mesh(data, s.data_start, t1, t2)
        name, prims = parse_zone_mesh_section(data, s)
        if name:
            out[name] = sum(len(p.positions) // 3 for p in prims)
        decrypt_zone_mesh(data, s.data_start, t1, t2)  # re-encrypt (involution)
    return out


def _gather_merged_prims(doc, buffers, mesh_id: str, p_inv: List[float],
                         c_inv: List[float], frame: Optional[List[List[float]]],
                         world_of) -> List[ZonePrimitive]:
    """Collect all glb geometry for ``mesh_id`` as FFXI mesh-local ZonePrimitives
    (grouped by texture). Each vertex: inv(P) . frame . inv(C) . W . v."""
    nodes = doc.get("nodes", [])
    mats = doc.get("materials", [])
    R = frame or [[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]]

    def ap_m(m, v):
        return (m[0] * v[0] + m[4] * v[1] + m[8] * v[2] + m[12],
                m[1] * v[0] + m[5] * v[1] + m[9] * v[2] + m[13],
                m[2] * v[0] + m[6] * v[1] + m[10] * v[2] + m[14])

    def ap_r(v):
        return (R[0][0] * v[0] + R[0][1] * v[1] + R[0][2] * v[2],
                R[1][0] * v[0] + R[1][1] * v[1] + R[1][2] * v[2],
                R[2][0] * v[0] + R[2][1] * v[1] + R[2][2] * v[2])

    def conv(wm, v):
        return ap_m(p_inv, ap_r(ap_m(c_inv, ap_m(wm, v))))

    def cross(a, b):
        return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])

    def unit(v):
        l = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
        return (v[0] / l, v[1] / l, v[2] / l)

    groups: Dict[Optional[str], ZonePrimitive] = {}
    for idx, n in enumerate(nodes):
        if n.get("mesh") is None or _node_mesh_name(n.get("name", ""), [mesh_id]) is None:
            continue
        wm = world_of(idx)
        for pr in doc["meshes"][n["mesh"]].get("primitives", []):
            a = pr.get("attributes", {})
            if "POSITION" not in a:
                continue
            pos = read_accessor(doc, buffers, a["POSITION"])
            uv = read_accessor(doc, buffers, a["TEXCOORD_0"]) if "TEXCOORD_0" in a else [(0.0, 0.0)] * len(pos)
            idxs = read_indices(doc, buffers, pr, len(pos))
            matname = mats[pr["material"]].get("name", "") if "material" in pr else ""
            texname = None if matname.strip().lower() in ("untextured", "") else matname
            prim = groups.setdefault(texname, ZonePrimitive(texture_name=texname))
            for k in range(0, len(idxs) - 2, 3):
                i0, i1, i2 = idxs[k], idxs[k + 2], idxs[k + 1]  # reversed winding (RH->LH)
                p0, p1, p2 = conv(wm, pos[i0]), conv(wm, pos[i1]), conv(wm, pos[i2])
                fn = unit(cross((p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]),
                                (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])))
                fn = (-fn[0], -fn[1], -fn[2])  # FFXI normal orientation
                for p in (p0, p1, p2):
                    prim.positions.append(p)
                    prim.normals.append(fn)
                prim.uvs.extend((uv[i0], uv[i1], uv[i2]))
    return [p for p in groups.values() if p.positions]


def _apply_mesh_merges(data: bytearray, merged_prims: Dict[str, List[ZonePrimitive]],
                       t1: bytes, t2: bytes) -> Tuple[bytearray, int]:
    """Re-serialize each merged mesh's 0x2E section and splice it back (sections
    grow). Returns (new_data, count)."""
    replacements = []  # (start, old_size, new_bytes)
    for s in parse_sections(data):
        if s.type_code != SECTION_TYPE_ZONE_MESH:
            continue
        decrypt_zone_mesh(data, s.data_start, t1, t2)
        name, _prims = parse_zone_mesh_section(data, s)
        if name in merged_prims:
            orig_dec = bytes(data[s.start:s.start + s.size])
            new_sec = encode_zone_mesh_section(name, merged_prims[name], orig_dec, (t1, t2))
            replacements.append((s.start, s.size, new_sec))
        decrypt_zone_mesh(data, s.data_start, t1, t2)  # re-encrypt (involution)
    for start, old_size, new_sec in sorted(replacements, reverse=True):
        data = bytearray(data[:start]) + bytearray(new_sec) + bytearray(data[start + old_size:])
    return data, len(replacements)


def import_zone_placements(dat_path: Path, model_path: Path, prune: bool = False,
                           rebuild: bool = False, add_meshes: Sequence[str] = ()
                           ) -> Tuple[Path, int, int, List[str], int, int, int, int]:
    """Apply edits from a model to a zone DAT. Returns
    (out_path, matched, total, skipped_placements, deleted, rebuilt, added, merged).

    merged: objects whose glb geometry grew/changed vs the pristine zone are
            automatically re-serialized into their 0x2E mesh (mesh-merge), so the
            engine renders the new geometry as part of that existing object.

    prune:      zero placements whose node is absent from the model (deleted in DCC).
    rebuild:    patch mesh vertex positions in place from the model (same topology).
    add_meshes: for each existing mesh id, append NEW placements for any model nodes
                that instance it beyond the existing placements (e.g. copies)."""
    if model_path.suffix.lower() not in (".glb", ".gltf"):
        raise ValueError(
            f"Zone import requires a .glb model (got '{model_path.suffix}'). "
            f"Export your edits as GLB from your DCC tool — FBX is not supported for import."
        )
    # Restore the DAT from its .base backup (fresh) and edit in place. `target`
    # holds pristine bytes until the writes below, so it doubles as the pristine
    # reference for the diff (parse_zone / _dat_tri_counts).
    target = editable_dat(dat_path, fresh=True)

    doc, _buffers = load_gltf_document(model_path)
    world_by_name, skipped_names = world_matrices_by_name(doc)
    # Index by normalized key too so suffix variants (id.002 / id_002) match.
    world_by_key: Dict[str, List[float]] = {}
    for nm, mat in world_by_name.items():
        world_by_key.setdefault(name_key(nm), mat)

    # Reconstruct the placement<->node-name mapping from the pristine zone.
    meshes_by_name, placements, _textures = parse_zone(target)
    node_name_by_index = placement_node_names(placements, meshes_by_name)

    # Auto-calibrate the axis frame: some DCC glTF exporters (C4D) bake an axis
    # convention that leaves a uniform rotation/flip after the correction node
    # is stripped. Detect it by aligning unchanged objects to the original zone
    # and correct every recovered transform. Identity (no-op) for Blender/xi.
    frame = _calibrate_frame(world_by_name, world_by_key, placements, node_name_by_index)
    if frame is not None and frame != _frame_candidates()[0]:
        world_by_name = {n: _apply_frame(frame, m) for n, m in world_by_name.items()}
        world_by_key = {n: _apply_frame(frame, m) for n, m in world_by_key.items()}

    # Recover uniform scale a DCC tool baked into the mesh geometry (C4D) rather
    # than the node transform, keyed by node name. No-op for Blender/xi.
    scale_factors = _recover_uniform_scales(doc, _buffers, meshes_by_name, node_name_by_index, placements)

    table1, _table2 = load_key_tables(Path(FFXI_DIR) / "FFXiMain.dll")

    # --- mesh-merge detection: objects whose glb geometry grew/changed vs the
    # pristine zone are re-serialized into their 0x2E mesh (so the engine renders
    # the new geometry as part of the existing, already-placed object). Detect by
    # triangle-count delta (ignore tiny re-triangulation noise < 32 tris / 10%).
    dat_tris = _dat_tri_counts(target, table1, _table2)
    glb_tris = _glb_tri_counts(doc, _buffers, set(dat_tris))
    nodes_g, world_of = _world_resolver(doc)
    c_inv = _correction_inverse(doc, world_of)
    frame_3x3 = frame if (frame and frame != _frame_candidates()[0]) else None
    merged_prims: Dict[str, List[ZonePrimitive]] = {}
    for mesh_name, dt in dat_tris.items():
        gt = glb_tris.get(mesh_name, 0)
        if not gt or abs(gt - dt) <= max(32, int(dt * 0.10)):
            continue  # unchanged (or noise) — leave its mesh byte-identical
        src = next((i for i, nm in node_name_by_index.items() if nm == mesh_name), None)
        if src is None:
            continue
        plc = placements[src]
        p_inv = _affine_inverse(trs_matrix(plc.position, plc.rotation, plc.scale))
        prims = _gather_merged_prims(doc, _buffers, mesh_name, p_inv, c_inv, frame_3x3, world_of)
        if prims:
            merged_prims[mesh_name] = prims
    merged_mesh_names = set(merged_prims)

    # Placements whose node was dropped for sitting outside the correction
    # group — flag them so the user knows those edits did NOT apply.
    expected_keys = {name_key(n) for n in node_name_by_index.values()}
    skipped_placements = sorted(
        n for n in skipped_names if name_key(n) in expected_keys
    )

    # Decrypt the 0x1C section in the real DAT, patch matched records, re-encrypt.
    data = bytearray(target.read_bytes())
    sections = parse_sections(data)
    zonedef = next((s for s in sections if s.type_code == SECTION_TYPE_ZONE_DEF), None)
    if zonedef is None:
        raise ValueError("DAT has no 0x1C ZoneDef (placement) section")

    node_count = decrypt_zone_objects(data, zonedef.data_start, zonedef.start, zonedef.size, table1)

    skipped_keys = {name_key(n) for n in skipped_names}
    matched = 0
    deleted = 0
    for index in range(node_count):
        node_name = node_name_by_index.get(index)
        if node_name is None:
            continue  # never exported (unresolved/skybox) — leave untouched
        if node_name in merged_mesh_names:
            continue  # mesh-merged object: geometry baked relative to its base
            # placement, so keep the base record exactly (don't rescale/move it)
        world = world_by_name.get(node_name) or world_by_key.get(name_key(node_name))
        if world is None:
            # Node absent from the model. If it's a mirror we deliberately
            # skipped, leave it. Otherwise it was deleted in the DCC tool —
            # zero it only when pruning is requested.
            if prune and name_key(node_name) not in skipped_keys:
                _zero_placement(data, zonedef.data_start, index)
                deleted += 1
            continue
        pos, rot, scale = decompose_trs(world)
        f = scale_factors.get(node_name)  # baked-into-geometry uniform scale, if any
        if f is not None:
            scale = (scale[0] * f, scale[1] * f, scale[2] * f)

        # Only overwrite objects the user actually changed; leave everything else
        # at its pristine base record. This is essential for mirror (negative-scale)
        # objects: a DCC like C4D bakes the reflection into geometry and drops it
        # from the node, so our recovered transform loses the mirror — overwriting
        # would flip those objects to the wrong side. We detect change by position
        # and uniform scale (robust across the C4D mangling); rotation is also
        # checked for non-mirror objects (mirror rotation can't be compared since
        # the handedness was mangled). Unchanged -> skip (record stays = base).
        plc = placements[index]
        base_is_mirror = min(plc.scale) < 0
        moved = max(abs(a - b) for a, b in zip(pos, plc.position)) > 0.5
        rescaled = f is not None
        rotated = (not base_is_mirror) and max(
            abs(a - b) for a, b in zip(trs_matrix((0, 0, 0), rot, (1, 1, 1)),
                                       trs_matrix((0, 0, 0), plc.rotation, (1, 1, 1)))) > 0.02
        if not (moved or rescaled or rotated):
            continue  # unchanged — keep the pristine base record (preserves mirrors)

        # For a changed mirror object, keep the base scale signs (the mirror C4D
        # dropped) and apply the recovered magnitudes, so we don't un-mirror it.
        if base_is_mirror:
            scale = tuple((-abs(s) if bs < 0 else abs(s)) for s, bs in zip(scale, plc.scale))

        rec = zonedef.data_start + 0x20 + index * 0x64
        struct.pack_into("<3f", data, rec + 0x10, *pos)
        struct.pack_into("<3f", data, rec + 0x1C, *rot)
        struct.pack_into("<3f", data, rec + 0x28, *scale)
        matched += 1

    reencrypt_zone_objects(data, zonedef.data_start, zonedef.start, zonedef.size, table1)

    # --- optional in-place geometry rebuild (same topology, positions only) ---
    rebuilt = 0
    if rebuild:
        # Mesh-local verts: if the correction node survived, glb local space ==
        # FFXI local (no change); if flattened, undo the 180deg-X (negate Y/Z).
        has_corr = any(n.get("name") == _ROOT_CORRECTION_NODE for n in doc.get("nodes", []))
        to_local = (lambda p: (p[0], p[1], p[2])) if has_corr else (lambda p: (p[0], -p[1], -p[2]))
        glb_meshes = _extract_glb_meshes(doc, _buffers)
        for s in sections:
            if s.type_code != SECTION_TYPE_ZONE_MESH:
                continue
            decrypt_zone_mesh(data, s.data_start, table1, _table2)
            name, prims = parse_zone_mesh_section(data, s)
            if name in glb_meshes and prims:
                if _rebuild_mesh_vertices(data, s, glb_meshes[name], to_local) > 0:
                    rebuilt += 1
            decrypt_zone_mesh(data, s.data_start, table1, _table2)  # re-encrypt (involution)

    target.write_bytes(data)

    # --- optional: append NEW placements for instanced meshes (grows 0x1C) ---
    added = 0
    if add_meshes:
        existing_keys = {name_key(n) for n in node_name_by_index.values()}
        entries = []  # (template_src_index, pos, rot, scale, mesh_id)
        for mesh_id in add_meshes:
            if resolve_mesh_name(mesh_id, meshes_by_name) is None:
                raise ValueError(f"Mesh '{mesh_id}' not found in this zone — cannot add a placement for it.")
            src = next((i for i, p in enumerate(placements) if name_key(p.mesh_id) == name_key(mesh_id)), None)
            if src is None:
                raise ValueError(f"No existing placement of '{mesh_id}' to use as a template.")
            for nm, world in world_by_name.items():
                if not _maps_to_mesh(nm, mesh_id) or name_key(nm) in existing_keys:
                    continue
                pos, rot, scale = decompose_trs(world)
                entries.append((src, pos, rot, scale, mesh_id))
        if entries:
            sec = bytearray(data[zonedef.start:zonedef.start + zonedef.size])
            decrypt_zone_objects(sec, 0x10, 0, len(sec), table1)
            grown = add_placements(sec, entries)
            reencrypt_zone_objects(grown, 0x10, 0, len(grown), table1)
            target.write_bytes(bytes(data[:zonedef.start]) + bytes(grown) + bytes(data[zonedef.start + zonedef.size:]))
            added = len(entries)

    # --- mesh-merge: re-serialize grown objects' 0x2E meshes (grows sections).
    # Done last, re-reading the file so section offsets are correct regardless of
    # any prior 0x1C growth from --placement.
    merged = 0
    if merged_prims:
        data2 = bytearray(target.read_bytes())
        data2, merged = _apply_mesh_merges(data2, merged_prims, table1, _table2)
        target.write_bytes(data2)

    return target, matched, node_count, skipped_placements, deleted, rebuilt, added, merged


def default_model_path(dat_path: Path) -> Optional[Path]:
    """The GLB to import for this DAT, looked up in its export dir
    (exports/zone/<rom>/). Prefers an exact ``<stem>.glb`` (then .gltf); otherwise
    picks the most recently modified .glb/.gltf there, so a freshly-saved C4D
    export under any name is found without typing the path. (FBX is not imported.)"""
    from xi.zone.xi_export import default_output_dir
    out_dir = default_output_dir(dat_path)
    for ext in (".glb", ".gltf"):
        candidate = out_dir / f"{dat_path.stem}{ext}"
        if candidate.is_file():
            return candidate
    if out_dir.is_dir():
        for ext in (".glb", ".gltf"):
            matches = sorted(out_dir.glob(f"*{ext}"), key=lambda p: p.stat().st_mtime, reverse=True)
            if matches:
                return matches[0]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Import an edited GLB into an FFXI zone DAT (GLB only — no FBX).")
    parser.add_argument("dat_path", help="Zone DAT path or ROM-relative spec like ROM/1/41")
    parser.add_argument("model_path", type=Path, nargs="?", default=None, help="Edited .glb/.gltf (default: the exported GLB for this DAT)")
    parser.add_argument("--prune", action="store_true", help="Delete (blank) placements whose object was removed in the model")
    parser.add_argument("--rebuild", action="store_true", help="Patch mesh vertex positions in place from the model (same topology)")
    parser.add_argument("--placement", action="append", default=[], metavar="MESH", help="Add new placement(s) for this existing mesh from extra instances in the model (repeatable)")
    parser.add_argument("--add-collision", type=Path, default=None, metavar="OBJ", help="Append the triangles in OBJ as new collision blockers (e.g. block a zone line). Independent of the GLB model.")
    parser.add_argument("--scale", type=float, default=1.0, help="Scale factor applied to --add-collision coords (fix DCC export-unit mismatch, e.g. 0.1 if 10x too large)")
    parser.add_argument("--camera-block", action="store_true", help="Make the added collision also block the camera (default: camera passes through invisible blockers)")
    args = parser.parse_args()
    resolved = resolve_dat_path(args.dat_path)

    if args.add_collision is not None:
        from xi.zone.xi_collision import add_collision_from_obj
        out, n_tris, n_meshes, warns = add_collision_from_obj(
            resolved, args.add_collision, scale=args.scale, camera_transparent=not args.camera_block)
        for w in warns:
            print(f"Warning: {w}")
        print(f"Wrote DAT:      {out}")
        print(f"Collision:      +{n_tris} triangle(s) across {n_meshes} grid cell(s)")
        if args.model_path is None and not (args.prune or args.rebuild or args.placement):
            return 0  # collision-only run

    model_path = args.model_path or default_model_path(resolved)
    if model_path is None:
        print("No model given and none found in exports/zone. Export first or pass a model path.")
        return 1
    out_path, matched, total, skipped, deleted, rebuilt, added, merged = import_zone_placements(
        resolved, model_path.resolve(), prune=args.prune, rebuild=args.rebuild, add_meshes=args.placement)
    print(f"Wrote DAT:      {out_path}")
    print(f"Placements:     {matched} of {total} objects updated" + (f", {deleted} deleted" if deleted else "") + (f", {added} added" if added else ""))
    if rebuilt:
        print(f"Rebuilt geometry: {rebuilt} mesh(es) vertex-patched")
    if skipped:
        print(f"SKIPPED (outside ffxi_root_correction group — NOT updated, left at base):")
        print(f"  {', '.join(skipped)}")
        print(f"  Move these inside the ffxi_root_correction group in your DCC tool to edit them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------

import click as _click  # noqa: E402


@_click.command("import")
@_click.argument("dat_path")
@_click.argument("model_path", required=False, type=_click.Path(exists=True, path_type=Path))
@_click.option("--prune", is_flag=True, help="Delete (blank) placements whose object was removed in the model")
@_click.option("--rebuild", is_flag=True, help="Patch mesh vertex positions in place (same topology) from the GLB")
@_click.option("--placement", "placements_add", multiple=True, metavar="MESH",
               help="Add new placement(s) for an existing mesh from extra instances in the model (repeatable). The mesh must already exist in the zone.")
@_click.option("--add-collision", "add_collision_obj", default=None,
               type=_click.Path(path_type=Path), metavar="OBJ",
               help="Append the triangles in OBJ as new collision blockers (e.g. block a zone line). "
                    "Resolved against the zone's export dir if not found as given (so 'blocker.obj' works). "
                    "Authored over the exported collision (same frame); existing collision is untouched. "
                    "Independent of the GLB — can run on its own.")
@_click.option("--scale", "collision_scale", type=float, default=1.0,
               help="Scale factor for --add-collision coords (fix a DCC export-unit mismatch, "
                    "e.g. --scale 0.1 if coords came out 10x too large).")
@_click.option("--camera-block", is_flag=True,
               help="Make the added collision also block the camera (pull it in). Default: the "
                    "camera passes through the invisible blocker (like FFXI's visible walls).")
@_click.option("--tex", is_flag=True,
               help="Re-import textures from the zone export dir (exports/zone/<rom>/*.png). "
                    "Matches PNGs to 0x20 sections by filename stem = texture name.")
@_click.option("--tex-dir", "tex_dir", default=None, type=_click.Path(path_type=Path),
               help="PNG directory for --tex (default: exports/zone/<rom>/)")
def cmd(dat_path: str, model_path, prune: bool, rebuild: bool, placements_add, add_collision_obj,
        collision_scale, camera_block, tex: bool, tex_dir):
    """Import an edited GLB into a zone DAT (GLB only — FBX is not accepted).

    DAT_PATH may be a ROM-relative spec like ROM/1/41. MODEL_PATH is optional —
    if omitted, the GLB exported for this DAT (newest .glb/.gltf in
    exports/zone/<rom>/) is used. Export your edits as GLB from your DCC tool.

    GLB import writes placement transforms and auto mesh-merges edited geometry
    (from ``<dat>.base`` each run — not stacked). --prune also deletes objects
    removed in the model; --rebuild patches mesh verts in place (same topology);
    --placement <mesh> appends new instances; --add-collision <obj> appends
    collision blockers (layers on the current DAT; run alone or AFTER a GLB
    import — GLB resets from .base and would wipe earlier collision); --tex
    re-imports edited PNGs. A <dat>.base backup is kept on first edit.
    """
    try:
        resolved = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise _click.ClickException(str(e))
    ensure_base(resolved)

    # GLB path first (fresh from .base). Collision AFTER so a combined invocation
    # does not wipe the just-appended blockers when import_zone_placements rewrites.
    needs_model = model_path is not None or prune or rebuild or placements_add
    if needs_model or (not tex and not add_collision_obj):
        if model_path is None:
            model_path = default_model_path(resolved)
            if model_path is None:
                raise _click.ClickException(
                    "No model given and none found in exports/zone. Export first, or pass a model path."
                )
            _click.echo(f"Using model:    {model_path}")
        try:
            out_path, matched, total, skipped, deleted, rebuilt, added, merged = import_zone_placements(
                resolved, model_path.resolve(), prune=prune, rebuild=rebuild, add_meshes=list(placements_add))
        except ValueError as e:
            raise _click.ClickException(str(e))
        _click.echo(f"Wrote DAT:      {out_path}")
        _click.echo(f"Placements:     {matched} of {total} objects updated" + (f", {deleted} deleted" if deleted else "") + (f", {added} added" if added else ""))
        if rebuilt:
            _click.echo(f"Rebuilt:        {rebuilt} mesh(es) vertex-patched in place")
        if merged:
            _click.echo(f"Mesh-merged:    {merged} object(s) re-meshed with edited geometry")
        if skipped:
            _click.echo(_click.style(
                f"Skipped (outside the ffxi_root_correction group — left at base value): "
                f"{', '.join(skipped)}", fg="yellow"))
            _click.echo("  -> move these inside the ffxi_root_correction group in your DCC tool to edit them.")

    if tex:
        from xi.tex.xi_import import import_textures
        png_dir = Path(tex_dir) if tex_dir else default_output_dir(resolved)
        pngs = sorted(png_dir.glob("*.png")) if png_dir.is_dir() else []
        if not pngs:
            _click.echo(_click.style(f"--tex: no PNGs found in {png_dir}", fg="yellow"))
        else:
            tex_res = import_textures(resolved, pngs)
            if tex_res["updated"]:
                _click.echo(f"Textures:       {len(tex_res['updated'])} updated ({', '.join(tex_res['updated'])})")
            for s in tex_res["skipped"]:
                _click.echo(_click.style(f"  tex skipped: {s}", fg="yellow"))

    if add_collision_obj is not None:
        from xi.zone.xi_collision import add_collision_from_obj
        try:
            out, n_tris, n_meshes, warns = add_collision_from_obj(
                resolved, Path(add_collision_obj), scale=collision_scale,
                camera_transparent=not camera_block)
        except (ValueError, FileNotFoundError) as e:
            raise _click.ClickException(str(e))
        for w in warns:
            _click.echo(_click.style(f"Warning: {w}", fg="yellow"))
        _click.echo(f"Wrote DAT:      {out}")
        _click.echo(f"Collision:      +{n_tris} triangle(s) across {n_meshes} grid cell(s)")

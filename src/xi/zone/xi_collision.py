#!/usr/bin/env python3
"""Decode (and, later, re-encode) the **player-collision** triangle mesh that the
FFXI client uses to stop you walking through walls and to stand you on floors.

Where collision lives
----------------------
Collision is NOT the visible ``0x2E`` ZoneMesh geometry, and it is NOT a separate
file. It is a triangle soup embedded *inside* the encrypted ``0x1C`` ZoneDef
section (the same block the community calls the "MZB"). The client collides a
swept sphere against these triangles; the navmesh the private-server uses for mob
AI is a *derived* artifact baked from this same soup, and the server line-of-sight
mesh is literally this soup written to a Wavefront .obj. So this one mesh is the
source of truth for all three systems — which is why being able to export/edit/
re-import it is the foundation for custom collision.

Layout (all offsets are ``data_start``-relative; data_start = section + 0x10)::

    0x1C header @ ds:
        +0x08  u32  collisionMeshOffset   (0 on ship-interior zones = no collision)
    collision header @ ds+collisionMeshOffset:
        +0x00  u32  numMeshes
        +0x04  u32  firstMeshOffset       (chain of every mesh; used for relocation)
        +0x08  u32  pairGroupCount
        +0x0C  u32  pairGroupsOffset      (the (transform,mesh) groups — what we read)
        +0x10  u32  collisionMapOffset    (XZ grid -> group; spatial broad-phase)
        +0x14  u32  transformsOffset      (0xC0-stride per-object world matrices)
        +0x18  u32  (mirrors nodeCount)
        +0x1C  u32  (zero)
    pair group (x pairGroupCount), starting at pairGroupsOffset:
        u32 countFlags                    (groupSize = countFlags & 0x7FF)
        groupSize x { u32 matrixOffset; u32 meshOffset }
        u32 0                             (terminator)
    collision mesh @ meshOffset:
        +0x00  u32  positionPoolOffset    (vec3 f32 vertices)
        +0x04  u32  normalPoolOffset      (vec3 f32 "direction"/normals)
        +0x08  u32  indexBufferOffset
        +0x0C  u16  triCount
        +0x0E  u16  flags
      triangle (x triCount) @ indexBufferOffset, 8 bytes:
        u16 rawP0  ; vertex index = rawP0 & 0x7FFF
        u16 rawP1  ; vertex index = rawP1 & 0x3FFF
        u16 rawP2  ; vertex index = rawP2 & 0x3FFF
        u16 rawD   ; normal index = rawD  & 0x7FFF
      The top nibble (>>12) of each of the four u16s combines into a 16-bit
      material word = (f0<<12)|(f1<<8)|(f2<<4)|f3:
        hitWall   = (material & 0x40) != 0   (wall vs floor material bit; BOTH block the player.
                     Camera transparency is mesh flags + third-word 0x4000, not hitWall alone.)
        terrain   = sum of bit 0x8 of each nibble -> 0..10
                    (object/path/grass/sand/snow/stone/metal/wood/
                     shallowwater/deepwater/unk0xA) — drives footstep FX, not blocking
    transform @ matrixOffset (0xC0 bytes):
        +0x00  16 f32  toWorldSpace   (column-major, same layout as trs_matrix)
        +0x40  16 f32  toCollisionSpace (= inverse of toWorldSpace)
        ... (cull bounds / flags / light / env — not needed to place geometry)

Matrix convention (verified against xim ``Matrix4f``)::

    0 4 8  12       world.x = m0*x + m4*y + m8 *z + m12
    1 5 9  13       world.y = m1*x + m5*y + m9 *z + m13
    2 6 10 14       world.z = m2*x + m6*y + m10*z + m14
    3 7 11 15

Output frame
------------
The exported ``.obj`` stores triangles in **FFXI world space with X and Y negated**
``(-x, -y, z)``. That is exactly the transform the zone ``.glb`` export bakes onto
its ``ffxi_root_correction`` node (a 180deg-X rotation composed with a [-1,1,-1]
scale = ``diag(-1,-1,1)``), so the collision obj **overlays the zone glb 1:1** in
Blender — letting you, e.g., drop an invisible wall exactly across a zone line.
The transform is its own inverse, so re-import just negates X and Y again.

Per-triangle wall/terrain flags are carried as Wavefront materials (``usemtl
col_wall_stone`` etc.) with a companion ``.mtl`` that colour-codes walls vs floors
so the distinction is visible in a DCC tool.

Decode/export AND encode/append are live: ``export_collision``,
``add_collision`` / ``add_collision_from_obj``, wired as
``xi zone import --add-collision``. Round-trip gates live beside the encoder.
"""

import json
import struct
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from xi.common.xi_section import encode_section_meta
from xi.xi_config import FFXI_DIR, editable_dat
from xi.entity.mesh.xi_export import parse_sections
from xi.zone.xi_decrypt import decrypt_zone_objects, load_key_tables, reencrypt_zone_objects

SECTION_TYPE_ZONE_DEF = 0x1C

# terrain-type index -> (obj material suffix, RGB for the .mtl, 0..1 floats).
# Colours mirror xim's debug palette so walls/floors/water read at a glance.
TERRAIN_NAMES = [
    "object", "path", "grass", "sand", "snow", "stone",
    "metal", "wood", "shallowwater", "deepwater", "unk0xa",
]
_TERRAIN_RGB = {
    "object": (0.30, 0.30, 0.30), "path": (0.25, 0.50, 0.25),
    "grass": (0.10, 0.55, 0.10), "sand": (0.65, 0.62, 0.20),
    "snow": (0.85, 0.85, 0.88), "stone": (0.45, 0.45, 0.45),
    "metal": (0.55, 0.35, 0.30), "wood": (0.55, 0.40, 0.22),
    "shallowwater": (0.25, 0.55, 0.80), "deepwater": (0.10, 0.20, 0.70),
    "unk0xa": (0.55, 0.10, 0.55),
}
_WALL_RGB = (0.80, 0.10, 0.10)


# ---------------------------------------------------------------------------
# Decoded representation
# ---------------------------------------------------------------------------


@dataclass
class CollisionTri:
    """One collision triangle in FFXI world space (pre Y-negation)."""
    v0: Tuple[float, float, float]
    v1: Tuple[float, float, float]
    v2: Tuple[float, float, float]
    normal: Tuple[float, float, float]
    hit_wall: bool
    terrain: int                       # 0..10 (may be >10 if a zone uses bits we don't model)

    def material_name(self) -> str:
        t = TERRAIN_NAMES[self.terrain] if 0 <= self.terrain < len(TERRAIN_NAMES) else f"t{self.terrain}"
        return f"col_{'wall' if self.hit_wall else 'floor'}_{t}"


@dataclass
class CollisionData:
    tris: List[CollisionTri] = field(default_factory=list)
    num_meshes_total: int = 0          # meshes in the relocation chain (incl. unreferenced)
    num_objects: int = 0               # placed (mesh, transform) instances iterated
    num_walls: int = 0
    num_meshes_referenced: int = 0     # distinct meshes reached via pair-groups
    header: Dict[str, int] = field(default_factory=dict)  # collision-section offsets/counts
    grid: Dict[str, int] = field(default_factory=dict)    # zone-block / sub-block dimensions


# ---------------------------------------------------------------------------
# Byte helpers
# ---------------------------------------------------------------------------


def _u16(buf, pos) -> int:
    return struct.unpack_from("<H", buf, pos)[0]


def _u32(buf, pos) -> int:
    return struct.unpack_from("<I", buf, pos)[0]


def _vec3(buf, pos) -> Tuple[float, float, float]:
    return struct.unpack_from("<3f", buf, pos)


def _xform_point(m: Sequence[float], p: Sequence[float]) -> Tuple[float, float, float]:
    """Column-major 4x4 (m[12..14] = translation) applied to a point (w=1)."""
    x, y, z = p
    return (
        m[0] * x + m[4] * y + m[8] * z + m[12],
        m[1] * x + m[5] * y + m[9] * z + m[13],
        m[2] * x + m[6] * y + m[10] * z + m[14],
    )


def _normal_matrix(inv16: Sequence[float]) -> Tuple[float, ...]:
    """transpose(truncate(toCollisionSpace)) as a flat 3x3 in the same column-major
    convention — the correct matrix to transform normals by (xim does the same)."""
    # truncate(M4) -> 3x3 = (m0,m1,m2, m4,m5,m6, m8,m9,m10); transpose swaps rows/cols.
    a, b, c = inv16[0], inv16[1], inv16[2]
    d, e, f = inv16[4], inv16[5], inv16[6]
    g, h, i = inv16[8], inv16[9], inv16[10]
    # transposed, stored column-major (col0, col1, col2):
    return (a, d, g, b, e, h, c, f, i)


def _xform_normal(nm: Sequence[float], n: Sequence[float]) -> Tuple[float, float, float]:
    x, y, z = n
    return (
        nm[0] * x + nm[3] * y + nm[6] * z,
        nm[1] * x + nm[4] * y + nm[7] * z,
        nm[2] * x + nm[5] * y + nm[8] * z,
    )


def _normalize(v: Sequence[float]) -> Tuple[float, float, float]:
    x, y, z = v
    m = (x * x + y * y + z * z) ** 0.5
    if m < 1e-12:
        return (0.0, 0.0, 0.0)
    return (x / m, y / m, z / m)


def _cross(a: Sequence[float], b: Sequence[float]) -> Tuple[float, float, float]:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _sub(a: Sequence[float], b: Sequence[float]) -> Tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


# ---------------------------------------------------------------------------
# Decode
# ---------------------------------------------------------------------------


def _parse_mesh(buf, mesh_abs: int, ds: int) -> List[Tuple[Tuple, Tuple, Tuple, Tuple, bool, int]]:
    """Parse one collision mesh into LOCAL-space (v0,v1,v2,normal,hit_wall,terrain)."""
    pos_pool = ds + _u32(buf, mesh_abs + 0x00)
    nrm_pool = ds + _u32(buf, mesh_abs + 0x04)
    idx_base = ds + _u32(buf, mesh_abs + 0x08)
    num_tris = _u16(buf, mesh_abs + 0x0C)

    out = []
    for t in range(num_tris):
        rec = idx_base + t * 8
        raw_p0 = _u16(buf, rec + 0)
        raw_p1 = _u16(buf, rec + 2)
        raw_p2 = _u16(buf, rec + 4)
        raw_d = _u16(buf, rec + 6)

        p0 = _vec3(buf, pos_pool + (raw_p0 & 0x7FFF) * 12)
        p1 = _vec3(buf, pos_pool + (raw_p1 & 0x3FFF) * 12)
        p2 = _vec3(buf, pos_pool + (raw_p2 & 0x3FFF) * 12)
        nrm = _vec3(buf, nrm_pool + (raw_d & 0x7FFF) * 12)

        f0, f1, f2, f3 = raw_p0 >> 12, raw_p1 >> 12, raw_p2 >> 12, raw_d >> 12
        material = (f0 << 12) | (f1 << 8) | (f2 << 4) | f3
        hit_wall = (material & 0x40) != 0
        terrain = ((f0 & 0x8) >> 3) + ((f1 & 0x8) >> 2) + ((f2 & 0x8) >> 1) + (f3 & 0x8)

        out.append((p0, p1, p2, nrm, hit_wall, terrain))
    return out


def decode_collision(buf, ds: int) -> CollisionData:
    """Decode the collision soup from a *decrypted* 0x1C section (data_start = ds).

    Iterates the (transform, mesh) pair-groups — the complete set of placed
    collision instances — transforming each mesh into FFXI world space and fixing
    winding against the stored normal (matching the client/xim)."""
    data = CollisionData()
    coll_rel = _u32(buf, ds + 0x08)
    if coll_rel == 0:
        return data                    # ship-interior zones: no collision block

    cb = ds + coll_rel
    data.num_meshes_total = _u32(buf, cb + 0x00)
    pair_count = _u32(buf, cb + 0x08)
    pairs_off = _u32(buf, cb + 0x0C)

    # Capture the collision header + grid dimensions for the .json sidecar.
    data.header = {
        "collision_offset": coll_rel,
        "num_meshes_chain": data.num_meshes_total,
        "first_mesh_offset": _u32(buf, cb + 0x04),
        "pair_group_count": pair_count,
        "pair_groups_offset": pairs_off,
        "collision_map_offset": _u32(buf, cb + 0x10),
        "transforms_offset": _u32(buf, cb + 0x14),
        "space_tree_index_count": _u32(buf, cb + 0x18),
    }
    zbx, zbz, bw, bl = buf[ds + 0xC], buf[ds + 0xD], buf[ds + 0xE], buf[ds + 0xF]
    sbx, sbz = (bw // 4) or 1, (bl // 4) or 1
    data.grid = {
        "zone_blocks_x": zbx, "zone_blocks_z": zbz,
        "block_width": bw, "block_length": bl,
        "sub_blocks_x": sbx, "sub_blocks_z": sbz,
        "grid_cols": zbx * sbx, "grid_rows": zbz * sbz,
    }

    mesh_cache: Dict[int, List] = {}   # mesh rel-offset -> local tris
    seen_objects: set = set()          # (matrix_off, mesh_off) -> dedup placed instances

    p = ds + pairs_off
    for _g in range(pair_count):
        count_flags = _u32(buf, p)
        p += 4
        group_size = count_flags & 0x7FF
        for _j in range(group_size):
            matrix_rel = _u32(buf, p)
            mesh_rel = _u32(buf, p + 4)
            p += 8

            key = (matrix_rel, mesh_rel)
            if key in seen_objects:
                continue
            seen_objects.add(key)
            data.num_objects += 1

            to_world = struct.unpack_from("<16f", buf, ds + matrix_rel + 0x00)
            to_coll = struct.unpack_from("<16f", buf, ds + matrix_rel + 0x40)
            nrm_mat = _normal_matrix(to_coll)

            local = mesh_cache.get(mesh_rel)
            if local is None:
                local = _parse_mesh(buf, ds + mesh_rel, ds)
                mesh_cache[mesh_rel] = local

            for (lp0, lp1, lp2, ln, hit_wall, terrain) in local:
                w0 = _xform_point(to_world, lp0)
                w1 = _xform_point(to_world, lp1)
                w2 = _xform_point(to_world, lp2)
                wn = _normalize(_xform_normal(nrm_mat, ln))

                # winding fix (xim Triangle.transform): if the geometric face
                # normal agrees with the stored normal, the verts were reversed.
                cross_n = _normalize(_cross(_sub(w0, w1), _sub(w1, w2)))
                if _dot(cross_n, wn) > 0.0:
                    w0, w2 = w2, w0
                tri = CollisionTri(w0, w1, w2, wn, hit_wall, terrain)
                data.tris.append(tri)
                if hit_wall:
                    data.num_walls += 1

        p += 4                          # group terminator (zero)

    data.num_meshes_referenced = len(mesh_cache)
    return data


# ---------------------------------------------------------------------------
# DAT -> decrypted 0x1C section
# ---------------------------------------------------------------------------


def decrypted_zonedef(source: Path) -> Optional[Tuple[bytearray, int, int, int]]:
    """Load a zone DAT and return (decrypted_buffer, data_start, section_start,
    section_size) for its 0x1C section, or None if the DAT has no ZoneDef section.
    The buffer is the full DAT with the 0x1C section decrypted in place."""
    data = bytearray(Path(source).read_bytes())
    sections = parse_sections(data)
    zonedef = next((s for s in sections if s.type_code == SECTION_TYPE_ZONE_DEF), None)
    if zonedef is None:
        return None
    table1, _table2 = load_key_tables(Path(FFXI_DIR) / "FFXiMain.dll")
    decrypt_zone_objects(data, zonedef.data_start, zonedef.start, zonedef.size, table1)
    return data, zonedef.data_start, zonedef.start, zonedef.size


# ---------------------------------------------------------------------------
# Wavefront .obj / .mtl writers (frame: FFXI world with Y negated)
# ---------------------------------------------------------------------------


def write_collision_obj(data: CollisionData, obj_path: Path) -> Path:
    """Write the collision soup to ``obj_path`` (+ a companion ``.mtl``). Vertices
    are emitted as (-x, -y, z) so the obj overlays the zone .glb in Blender (the
    glb's ffxi_root_correction maps FFXI world -> (-x,-y,z); see xi_export)."""
    obj_path = Path(obj_path)
    mtl_path = obj_path.with_suffix(".mtl")

    # Dedup vertices and normals on exact float identity to keep the obj compact
    # and connected for editing.
    vert_index: Dict[Tuple[float, float, float], int] = {}
    norm_index: Dict[Tuple[float, float, float], int] = {}
    verts: List[Tuple[float, float, float]] = []
    norms: List[Tuple[float, float, float]] = []

    def vidx(v):
        key = (-v[0], -v[1], v[2])
        i = vert_index.get(key)
        if i is None:
            i = len(verts)
            vert_index[key] = i
            verts.append(key)
        return i + 1               # obj is 1-based

    def nidx(n):
        key = (-n[0], -n[1], n[2])
        i = norm_index.get(key)
        if i is None:
            i = len(norms)
            norm_index[key] = i
            norms.append(key)
        return i + 1

    # Group faces by material so each material is one contiguous usemtl block.
    faces_by_mat: Dict[str, List[Tuple[int, int, int, int]]] = {}
    for tri in data.tris:
        a, b, c = vidx(tri.v0), vidx(tri.v1), vidx(tri.v2)
        n = nidx(tri.normal)
        faces_by_mat.setdefault(tri.material_name(), []).append((a, b, c, n))

    lines: List[str] = [
        "# FFXI zone collision mesh (MZB) exported by xi",
        "# frame: FFXI world, X+Y negated (-x, -y, z) -> overlays the zone .glb",
        f"# triangles: {len(data.tris)}  walls: {data.num_walls}  "
        f"floors: {len(data.tris) - data.num_walls}  placed-objects: {data.num_objects}",
        f"mtllib {mtl_path.name}",
    ]
    for v in verts:
        lines.append(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}")
    for n in norms:
        lines.append(f"vn {n[0]:.6f} {n[1]:.6f} {n[2]:.6f}")
    for mat in sorted(faces_by_mat):
        lines.append(f"usemtl {mat}")
        for (a, b, c, n) in faces_by_mat[mat]:
            lines.append(f"f {a}//{n} {b}//{n} {c}//{n}")

    obj_path.parent.mkdir(parents=True, exist_ok=True)
    obj_path.write_text("\n".join(lines) + "\n", encoding="ascii")
    _write_mtl(faces_by_mat.keys(), mtl_path)
    return obj_path


def _write_mtl(material_names, mtl_path: Path) -> None:
    out: List[str] = ["# xi collision materials (wall = red; floors colour-coded by terrain)"]
    for name in sorted(material_names):
        if name.startswith("col_wall_"):
            r, g, b = _WALL_RGB
        else:
            terrain = name.split("col_floor_", 1)[-1]
            r, g, b = _TERRAIN_RGB.get(terrain, (0.6, 0.6, 0.6))
        out.append(f"newmtl {name}")
        out.append(f"Kd {r:.3f} {g:.3f} {b:.3f}")
        out.append("d 1.0")
        out.append("illum 1")
    mtl_path.write_text("\n".join(out) + "\n", encoding="ascii")


def _terrain_name(idx: int) -> str:
    return TERRAIN_NAMES[idx] if 0 <= idx < len(TERRAIN_NAMES) else f"unk_{idx}"


def write_collision_json(data: CollisionData, json_path: Path,
                         dat_spec: Optional[str] = None) -> Path:
    """Write a ``<stem>.collision.json`` reference sidecar: the decoded collision
    header/grid, geometry stats, terrain + material histograms, world bbox, and a
    short opcode/format key. Handy when reasoning about a zone's collision without
    re-parsing the DAT."""
    json_path = Path(json_path)

    pts = [c for t in data.tris for c in (t.v0, t.v1, t.v2)]
    if pts:
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; zs = [p[2] for p in pts]
        bbox = {
            "min": [round(min(xs), 3), round(min(ys), 3), round(min(zs), 3)],
            "max": [round(max(xs), 3), round(max(ys), 3), round(max(zs), 3)],
            "note": "FFXI world coords (pre negation); the .obj negates X+Y to (-x,-y,z)",
        }
    else:
        bbox = None

    terrain_hist = Counter(_terrain_name(t.terrain) for t in data.tris)
    material_hist = Counter(t.material_name() for t in data.tris)

    doc = {
        "source_dat": dat_spec,
        "frame": "FFXI world space; the companion .obj stores (-x, -y, z) so it "
                 "overlays the zone .glb (= the glb's ffxi_root_correction = diag(-1,-1,1)).",
        "stats": {
            "triangles": len(data.tris),
            "walls": data.num_walls,
            "floors": len(data.tris) - data.num_walls,
            "placed_objects": data.num_objects,
            "meshes_referenced": data.num_meshes_referenced,
            "meshes_in_chain": data.num_meshes_total,
        },
        "terrain_histogram": dict(sorted(terrain_hist.items(), key=lambda kv: -kv[1])),
        "material_histogram": dict(sorted(material_hist.items())),
        "bbox": bbox,
        "collision_header": data.header,
        "grid": data.grid,
        "format": {
            "lives_in": "0x1C ZoneDef section (encrypted) of the zone model DAT; "
                        "the visible mesh is the separate 0x2E ZoneMesh.",
            "opcodes": {
                "0x1C": "ZoneDef (placements + embedded collision/MZB)",
                "0x2E": "ZoneMesh (visible render geometry)",
                "0x20": "Texture",
            },
            "triangle_record": "8 bytes = u16 rawP0(idx&0x7FFF), rawP1(&0x3FFF), "
                               "rawP2(&0x3FFF), rawD(normal idx&0x7FFF). Top nibble of "
                               "each -> material word: hitWall=&0x40; terrain=sum of bit "
                               "0x8 per nibble (0..10).",
            "terrain_types": TERRAIN_NAMES,
            "footsteps": "terrain type also selects the footstep sound/effect "
                         "(DatId 0<idx><move><shake>); sand/snow leave footmarks.",
        },
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(doc, indent=2), encoding="ascii")
    return json_path


# ---------------------------------------------------------------------------
# Raw structural codec (lossless decode -> re-encode; the basis for authoring)
# ---------------------------------------------------------------------------
# The collision block is laid out contiguously and in a fixed order:
#   [0x20 header][mesh block][transforms 0xC0*N][pair-groups][grid map][tail]
# Each mesh is packed [16B header][pos pool][nrm pool][index buf] with NO padding,
# and every group pair references a transform inside the array + a mesh inside the
# chain. So a faithful decode->encode reproduces the bytes exactly (verified on
# Lower Jeuno), which is the prerequisite for confidently authoring new collision.


@dataclass
class RawMesh:
    flags: int
    verts: List[Tuple[float, float, float]]
    norms: List[Tuple[float, float, float]]
    tris: List[Tuple[int, int, int, int]]      # raw u16s (vertex/normal idx + flag bits)
    old_rel: int = 0                            # data_start-relative offset in the source


@dataclass
class RawGroup:
    count_flags: int                            # u32; groupSize = count_flags & 0x7FF
    pairs: List[Tuple[int, int]]                # (transform_old_rel, mesh_old_rel)
    old_rel: int = 0


@dataclass
class RawCollision:
    coll_rel: int
    idx_count: int                              # header +0x18 (mirrors nodeCount)
    meshes: List[RawMesh]
    transforms: bytearray                       # raw [transforms_off, pairs_off)
    transforms_old_off: int
    groups: List[RawGroup]
    map_u32: List[int]                          # grid cell -> group old_rel (0 = empty)
    tail: bytes                                 # bytes after the map up to payload end


def parse_collision_raw(buf, ds: int, payload_end_rel: int) -> Optional[RawCollision]:
    """Losslessly decode the collision block of a *decrypted* 0x1C section."""
    coll = _u32(buf, ds + 0x08)
    if coll == 0:
        return None
    cb = ds + coll
    num_meshes = _u32(buf, cb + 0x00)
    first_mesh = _u32(buf, cb + 0x04)
    pair_count = _u32(buf, cb + 0x08)
    pairs_off = _u32(buf, cb + 0x0C)
    map_off = _u32(buf, cb + 0x10)
    transforms_off = _u32(buf, cb + 0x14)
    idx_count = _u32(buf, cb + 0x18)

    meshes: List[RawMesh] = []
    mp = ds + first_mesh
    for _ in range(num_meshes):
        rel = mp - ds
        p = _u32(buf, mp + 0x00)
        n = _u32(buf, mp + 0x04)
        ix = _u32(buf, mp + 0x08)
        tc = _u16(buf, mp + 0x0C)
        fl = _u16(buf, mp + 0x0E)
        nv = (n - p) // 12
        nn = (ix - n) // 12
        verts = [struct.unpack_from("<3f", buf, ds + p + k * 12) for k in range(nv)]
        norms = [struct.unpack_from("<3f", buf, ds + n + k * 12) for k in range(nn)]
        tris = [struct.unpack_from("<4H", buf, ds + ix + k * 8) for k in range(tc)]
        meshes.append(RawMesh(fl, verts, norms, tris, rel))
        mp = ds + ix + tc * 8

    transforms = bytearray(buf[ds + transforms_off:ds + pairs_off])

    groups: List[RawGroup] = []
    p = ds + pairs_off
    for _ in range(pair_count):
        grel = p - ds
        cf = _u32(buf, p)
        p += 4
        gs = cf & 0x7FF
        prs = []
        for _j in range(gs):
            prs.append((_u32(buf, p), _u32(buf, p + 4)))
            p += 8
        p += 4                                  # zero terminator
        groups.append(RawGroup(cf, prs, grel))

    # The map is the last region; its entries are group old_rel offsets (or 0).
    map_bytes = buf[ds + map_off:ds + payload_end_rel]
    n_map = len(map_bytes) // 4
    map_u32 = list(struct.unpack_from("<%dI" % n_map, buf, ds + map_off)) if n_map else []
    tail = bytes(map_bytes[n_map * 4:])

    return RawCollision(coll, idx_count, meshes, transforms, transforms_off,
                        groups, map_u32, tail)


def serialize_collision_raw(raw: RawCollision) -> bytes:
    """Re-serialize a RawCollision to the on-disk collision block (starting at the
    0x20 header). Offsets are recomputed; with the source order/packing preserved
    they reproduce the original exactly (byte-exact round-trip)."""
    coll = raw.coll_rel
    first_mesh_off = coll + 0x20

    mesh_blob = bytearray()
    new_mesh_rel: Dict[int, int] = {}
    for m in raw.meshes:
        rel = first_mesh_off + len(mesh_blob)
        new_mesh_rel[m.old_rel] = rel
        pos_off = rel + 0x10
        nrm_off = pos_off + len(m.verts) * 12
        idx_off = nrm_off + len(m.norms) * 12
        mesh_blob += struct.pack("<IIIHH", pos_off, nrm_off, idx_off, len(m.tris), m.flags)
        for v in m.verts:
            mesh_blob += struct.pack("<3f", *v)
        for n in m.norms:
            mesh_blob += struct.pack("<3f", *n)
        for t in m.tris:
            mesh_blob += struct.pack("<4H", *t)

    transforms_off = first_mesh_off + len(mesh_blob)

    def remap_tf(old: int) -> int:
        return old - raw.transforms_old_off + transforms_off

    pairs_off = transforms_off + len(raw.transforms)
    pairs_blob = bytearray()
    new_group_rel: Dict[int, int] = {}
    for g in raw.groups:
        new_group_rel[g.old_rel] = pairs_off + len(pairs_blob)
        pairs_blob += struct.pack("<I", g.count_flags)
        for (mat_old, mesh_old) in g.pairs:
            pairs_blob += struct.pack("<II", remap_tf(mat_old), new_mesh_rel[mesh_old])
        pairs_blob += struct.pack("<I", 0)

    map_off = pairs_off + len(pairs_blob)
    map_blob = bytearray()
    for off in raw.map_u32:
        map_blob += struct.pack("<I", new_group_rel[off] if off in new_group_rel else off)

    header = struct.pack("<IIIIIIII", len(raw.meshes), first_mesh_off, len(raw.groups),
                         pairs_off, map_off, transforms_off, raw.idx_count, 0)

    return bytes(header) + bytes(mesh_blob) + bytes(raw.transforms) + \
        bytes(pairs_blob) + bytes(map_blob) + raw.tail


def roundtrip_check(source: Path) -> Tuple[bool, str]:
    """Decode -> re-encode the collision block and compare byte-for-byte against
    the source. Returns (ok, message). The encryption round-trip is verified
    separately (xi_decrypt); this isolates the collision codec."""
    parsed = decrypted_zonedef(source)
    if parsed is None:
        return False, "no 0x1C ZoneDef section"
    buf, ds, _sstart, ssize = parsed
    payload_end_rel = ssize - 0x10
    raw = parse_collision_raw(buf, ds, payload_end_rel)
    if raw is None:
        return False, "no collision block (ship-interior zone?)"
    orig = bytes(buf[ds + raw.coll_rel:ds + payload_end_rel])
    rebuilt = serialize_collision_raw(raw)
    if rebuilt == orig:
        return True, (f"byte-exact: {len(raw.meshes)} meshes, {len(raw.groups)} groups, "
                      f"{len(orig)} bytes")
    n = min(len(orig), len(rebuilt))
    first = next((i for i in range(n) if orig[i] != rebuilt[i]), n)
    return False, (f"MISMATCH at +0x{first:X} (orig {len(orig)}B vs rebuilt {len(rebuilt)}B); "
                   f"orig={orig[first:first + 8].hex()} new={rebuilt[first:first + 8].hex()}")


# ---------------------------------------------------------------------------
# Authoring: append new collision geometry from an .obj (e.g. block a zone line)
# ---------------------------------------------------------------------------
# Keeps the original collision untouched; adds the obj's triangles as new meshes,
# bucketed into the grid cells they overlap, each placed by ONE shared identity
# transform (the obj is authored directly in world space). idx_count stays equal
# to nodeCount so visual culling is unaffected.

# A collision object's 0xC0 record carries a world-space Y AABB at +0xB4/+0xB8
# that the client uses to CULL the object before testing any triangle
# (CollisionManager.cpp: `if (obj->minY <= playerMaxY && obj->maxY >= playerMinY)`).
# A zeroed record (minY=maxY=0) is culled everywhere except at world-Y 0, so an
# appended blocker never blocks. We build the record with identity transforms, an
# identity normal matrix (+0x80, so reported hit-normals are correct), and a FINITE
# Y AABB bracketing the appended geometry (± _CULL_MARGIN). NOT a ±1e6 sentinel: the
# camera-collision / vertical-clamp query does arithmetic with this AABB, and an extreme
# bound crashes the client the instant the camera tests a (camera-blocking) floor — load
# and player-walk are fine (the player cull is just a comparison) but the camera query
# chokes. A finite bracket still always survives the cull where the geometry actually is.
_TF_Y_MIN = -1.0e6
_TF_Y_MAX = 1.0e6
_CULL_MARGIN = 50.0   # yalms of slack around the appended geometry's Y extent


def _identity_collision_object(min_y: float = _TF_Y_MIN, max_y: float = _TF_Y_MAX) -> bytes:
    """Build a 0xC0 CollisionObjectData: identity world+inverse matrices, identity
    3x3 normal matrix, and a world-Y cull AABB [min_y, max_y]."""
    rec = bytearray(0xC0)
    ident4 = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
    struct.pack_into("<16f", rec, 0x00, *ident4)               # toWorldSpace = I
    struct.pack_into("<16f", rec, 0x40, *ident4)               # toCollisionSpace = I
    struct.pack_into("<9f", rec, 0x80, 1, 0, 0, 0, 1, 0, 0, 0, 1)  # Matrix3 normal = I
    struct.pack_into("<f", rec, 0xB4, min_y)                   # cull AABB world-Y min
    struct.pack_into("<f", rec, 0xB8, max_y)                   # cull AABB world-Y max
    return bytes(rec)


def _pack32(buf: bytearray, pos: int, value: int) -> None:
    struct.pack_into("<I", buf, pos, value & 0xFFFFFFFF)


def encode_tri_flags(hit_wall: bool, terrain: int) -> Tuple[int, int, int, int]:
    """Inverse of the decode: (hitWall, terrain) -> the four high-nibbles (f0..f3).
    terrain bit k -> 0x8 in nibble k; hitWall -> 0x4 in nibble 2 (material bit 0x40).
    Verified to reproduce all 7 material words present in Lower Jeuno."""
    f = [0, 0, 0, 0]
    for k in range(4):
        if terrain & (1 << k):
            f[k] |= 0x8
    if hit_wall:
        f[2] |= 0x4
    return f[0], f[1], f[2], f[3]


@dataclass
class AuthoredTri:
    v0: Tuple[float, float, float]      # FFXI world space
    v1: Tuple[float, float, float]
    v2: Tuple[float, float, float]
    hit_wall: bool
    terrain: int


def _terrain_index(name: str) -> int:
    name = name.lower()
    return TERRAIN_NAMES.index(name) if name in TERRAIN_NAMES else 0


def parse_collision_obj(path: Path, default_wall: bool = True,
                        default_terrain: int = 0, scale: float = 1.0) -> List[AuthoredTri]:
    """Parse an authored .obj into world-space collision triangles. Vertices are
    un-negated on X+Y (the export wrote (-x,-y,z)), faces are triangulated as fans,
    and ``usemtl col_{wall,floor}_<terrain>`` selects the per-face flags. Faces
    under an unrecognised material use (default_wall, default_terrain). ``scale``
    multiplies every coordinate (about the origin) before use — to correct a DCC
    export-unit mismatch (e.g. 0.1 if coords came out 10x too large)."""
    verts: List[Tuple[float, float, float]] = []
    tris: List[AuthoredTri] = []
    cur_wall, cur_terrain = default_wall, default_terrain

    for line in Path(path).read_text(encoding="ascii", errors="replace").splitlines():
        if not line or line[0] == "#":
            continue
        tok = line.split()
        if tok[0] == "v":
            x, y, z = float(tok[1]), float(tok[2]), float(tok[3])
            verts.append((-x * scale, -y * scale, z * scale))  # un-negate X+Y + scale -> FFXI world
        elif tok[0] == "usemtl":
            mat = tok[1]
            if mat.startswith("col_wall_"):
                cur_wall, cur_terrain = True, _terrain_index(mat[len("col_wall_"):])
            elif mat.startswith("col_floor_"):
                cur_wall, cur_terrain = False, _terrain_index(mat[len("col_floor_"):])
            else:
                cur_wall, cur_terrain = default_wall, default_terrain
        elif tok[0] == "f":
            idx = []
            for part in tok[1:]:
                v = int(part.split("/")[0])
                idx.append(v - 1 if v > 0 else len(verts) + v)
            for t in range(1, len(idx) - 1):    # fan-triangulate
                tris.append(AuthoredTri(verts[idx[0]], verts[idx[t]], verts[idx[t + 1]],
                                        cur_wall, cur_terrain))
    return tris


def parse_collision_obj_text(text: str, default_wall: bool = True,
                              default_terrain: int = 0, scale: float = 1.0) -> List[AuthoredTri]:
    """Same as parse_collision_obj but accepts OBJ text directly (no file I/O).
    Used by the bridge endpoint that receives OBJ content from the browser."""
    verts: List[Tuple[float, float, float]] = []
    tris: List[AuthoredTri] = []
    cur_wall, cur_terrain = default_wall, default_terrain
    for line in text.splitlines():
        if not line or line[0] == "#":
            continue
        tok = line.split()
        if not tok:
            continue
        if tok[0] == "v":
            x, y, z = float(tok[1]), float(tok[2]), float(tok[3])
            verts.append((-x * scale, -y * scale, z * scale))  # un-negate X+Y -> FFXI world
        elif tok[0] == "usemtl":
            mat = tok[1]
            if mat.startswith("col_wall_"):
                cur_wall, cur_terrain = True, _terrain_index(mat[len("col_wall_"):])
            elif mat.startswith("col_floor_"):
                cur_wall, cur_terrain = False, _terrain_index(mat[len("col_floor_"):])
            else:
                cur_wall, cur_terrain = default_wall, default_terrain
        elif tok[0] == "f":
            idx = []
            for part in tok[1:]:
                v = int(part.split("/")[0])
                idx.append(v - 1 if v > 0 else len(verts) + v)
            for t in range(1, len(idx) - 1):    # fan-triangulate
                tris.append(AuthoredTri(verts[idx[0]], verts[idx[t]], verts[idx[t + 1]],
                                        cur_wall, cur_terrain))
    return tris


def replace_zone_collision(dat_path: Path, tris: List[AuthoredTri],
                            camera_transparent: bool = True) -> Tuple[Path, int, int]:
    """Clear all existing collision then bake *tris* as the sole new geometry.
    Returns (out_path, n_removed, n_added)."""
    target = editable_dat(dat_path, fresh=False)
    data = bytearray(Path(target).read_bytes())
    sections = parse_sections(data)
    zonedef = next((s for s in sections if s.type_code == SECTION_TYPE_ZONE_DEF), None)
    if zonedef is None:
        raise ValueError("DAT has no 0x1C ZoneDef section")

    table1, _table2 = load_key_tables(Path(FFXI_DIR) / "FFXiMain.dll")
    sec = bytearray(data[zonedef.start:zonedef.start + zonedef.size])
    decrypt_zone_objects(sec, 0x10, 0, len(sec), table1)

    raw = parse_collision_raw(sec, 0x10, len(sec) - 0x10)
    n_removed = len(raw.meshes) if raw else 0

    cleared = clear_collision(sec)
    replaced = add_collision(cleared, tris, camera_transparent=camera_transparent)
    reencrypt_zone_objects(replaced, 0x10, 0, len(replaced), table1)
    out = bytes(data[:zonedef.start]) + bytes(replaced) + bytes(data[zonedef.start + zonedef.size:])
    Path(target).write_bytes(out)
    return Path(target), n_removed, len(tris)


def _cell_of(x: float, z: float, blocks) -> Tuple[int, int]:
    zbx, zbz, bw, bl = blocks
    sbx, sbz = (bw // 4) or 1, (bl // 4) or 1
    cell_w = bw / sbx
    cell_l = bl / sbz
    xi = int((x + bw * zbx / 2) / cell_w)
    zi = int((z + bl * zbz / 2) / cell_l)
    return xi, zi


def add_collision(sec: bytearray, tris: Sequence[AuthoredTri],
                  camera_transparent: bool = True) -> bytearray:
    """Append authored collision triangles to a *decrypted* 0x1C section (data_start
    = 0x10), bucketed into the grid cells they overlap. Returns a new decrypted
    section bytearray (ready to re-encrypt). Mirrors add_placements' contract.

    camera_transparent: set the per-mesh CollisionMeshHeader.Flags bit so the
    client's camera/LoS ray skips the wall (it still blocks the player). The
    skip fires on `Flags != 0 && (VertexIndex3 & 0x4000)`; the 0x4000 bit is the
    hitWall bit our wall tris already carry, so Flags=1 alone flips a blocker from
    camera-blocking to camera-transparent (verified vs the decompiled client)."""
    ds = 0x10
    mesh_flags = 1 if camera_transparent else 0
    if not tris:
        return bytearray(sec)
    raw = parse_collision_raw(sec, ds, len(sec) - 0x10)
    if raw is None:
        raise ValueError("zone has no collision block to append to")

    blocks = (sec[ds + 0xC], sec[ds + 0xD], sec[ds + 0xE], sec[ds + 0xF])
    zbx, zbz, bw, bl = blocks
    cols, rows = zbx * ((bw // 4) or 1), zbz * ((bl // 4) or 1)

    # Append ONE shared CollisionObjectData transform (identity world matrix) for the new
    # geometry, with a FINITE world-Y cull AABB = the geometry's own Y extent + margin.
    # The decompiled client drives collision purely by grid→group→pairs (NO per-object
    # iteration, idxCount @+0x18 never read), so a free-standing transform is the correct
    # path. The AABB must be finite, NOT ±1e6: the camera-collision/vertical-clamp query
    # does arithmetic with it and an extreme bound crashes the moment the camera tests a
    # camera-blocking floor. The finite bracket still always passes the cull where the
    # geometry is (player/camera collide right there).
    ys = [v[1] for t in tris for v in (t.v0, t.v1, t.v2)]
    cull_min = (min(ys) - _CULL_MARGIN) if ys else _TF_Y_MIN
    cull_max = (max(ys) + _CULL_MARGIN) if ys else _TF_Y_MAX
    ident_old_rel = raw.transforms_old_off + len(raw.transforms)
    raw.transforms += bytearray(_identity_collision_object(cull_min, cull_max))

    # bucket triangles into the cells their bbox overlaps
    by_cell: Dict[Tuple[int, int], List[AuthoredTri]] = {}
    for t in tris:
        xs = (t.v0[0], t.v1[0], t.v2[0])
        zs = (t.v0[2], t.v1[2], t.v2[2])
        x0, z0 = _cell_of(min(xs), min(zs), blocks)
        x1, z1 = _cell_of(max(xs), max(zs), blocks)
        for zi in range(min(z0, z1), max(z0, z1) + 1):
            for xi in range(min(x0, x1), max(x0, x1) + 1):
                if 0 <= xi < cols and 0 <= zi < rows:
                    by_cell.setdefault((xi, zi), []).append(t)

    groups_by_oldrel = {g.old_rel: g for g in raw.groups}
    mesh_ctr = 0x40000000
    group_ctr = 0x50000000

    def _build_cell_mesh(subset, flags, old_rel):
        """Build one RawMesh from a HOMOGENEOUS (all-wall or all-floor) tri subset, with
        verts deduped within the mesh. ALWAYS emits back-to-back normal pairs (double-sided)
        — single-sided tris crash this client. Floors orient the primary normal "up" first.
        Returns the RawMesh, or None if every tri was degenerate."""
        vidx: Dict[Tuple[float, float, float], int] = {}
        verts: List[Tuple[float, float, float]] = []
        norms: List[Tuple[float, float, float]] = []
        mtris: List[Tuple[int, int, int, int]] = []

        def vi(v):
            i = vidx.get(v)
            if i is None:
                i = len(verts)
                vidx[v] = i
                verts.append(v)
            return i

        for t in subset:
            cx = _cross(_sub(t.v1, t.v0), _sub(t.v2, t.v1))
            if cx[0] * cx[0] + cx[1] * cx[1] + cx[2] * cx[2] < 1e-12:
                # Degenerate / zero-area triangle: no usable normal. The client normalizes
                # the collision normal during the swept-sphere test, so a zero normal ->
                # NaN -> instant crash on zone load. Drop it (it blocks nothing anyway).
                continue
            nrm = _normalize(cx)
            a, b, c = vi(t.v0), vi(t.v1), vi(t.v2)
            f0, f1, f2, f3 = encode_tri_flags(t.hit_wall, t.terrain)
            # ALWAYS emit BOTH facings (a back-to-back pair). A SINGLE-SIDED collision
            # triangle crashes this client — verified in-game: every single-sided FLOOR
            # config crashed (mesh flags 0/1, hit_wall 0/1) while the double-sided WALL
            # always worked; flags/hit_wall were ruled out, leaving sidedness. Walls need
            # both facings anyway (block from any approach); floors get the up facing FIRST
            # (FFXI -Y) so the standable side is unambiguous, plus its back face.
            if not t.hit_wall and nrm[1] > 0:
                nrm = (-nrm[0], -nrm[1], -nrm[2])   # floor: orient primary normal "up" so you stand on top
            normals = (nrm, (-nrm[0], -nrm[1], -nrm[2]))
            for n in normals:
                ni = len(norms)
                norms.append(n)
                mtris.append((a | (f0 << 12), b | (f1 << 12), c | (f2 << 12), ni | (f3 << 12)))

        if not mtris:
            return None
        return RawMesh(flags=flags, verts=verts, norms=norms, tris=mtris, old_rel=old_rel)

    for (xi, zi), cell_tris in by_cell.items():
        # Segregate by surface, ONE mesh per kind (CollisionMeshHeader.Flags is per-mesh):
        # WALLS take the camera_transparent flag (visible walls = camera-transparent,
        # flags=1); FLOORS are flags=0 (camera-BLOCKING, like the ground). BOTH are emitted
        # double-sided by _build_cell_mesh — a single-sided collision tri crashes this client
        # (every single-sided floor config crashed in-game; the double-sided wall always
        # worked), so floors get a back face too even though flags/hit_wall mark them floors.
        walls = [t for t in cell_tris if t.hit_wall]
        floors = [t for t in cell_tris if not t.hit_wall]
        cell_meshes = []
        m = _build_cell_mesh(walls, mesh_flags, mesh_ctr)
        if m is not None:
            cell_meshes.append(m); mesh_ctr += 1
        m = _build_cell_mesh(floors, 0, mesh_ctr)
        if m is not None:
            cell_meshes.append(m); mesh_ctr += 1
        if not cell_meshes:
            continue   # every tri in this cell was degenerate -> emit no mesh/group/map cell

        cell = zi * cols + xi
        for mesh in cell_meshes:
            raw.meshes.append(mesh)
            existing = raw.map_u32[cell] if cell < len(raw.map_u32) else 0
            if existing and existing in groups_by_oldrel:
                g = groups_by_oldrel[existing]
                g.pairs.append((ident_old_rel, mesh.old_rel))
                g.count_flags = (g.count_flags & ~0x7FF) | ((g.count_flags & 0x7FF) + 1)
            else:
                g = RawGroup(count_flags=1, pairs=[(ident_old_rel, mesh.old_rel)], old_rel=group_ctr)
                group_ctr += 1
                raw.groups.append(g)
                groups_by_oldrel[g.old_rel] = g
                if cell < len(raw.map_u32):
                    raw.map_u32[cell] = g.old_rel

    region = serialize_collision_raw(raw)
    new = bytearray(sec[:ds + raw.coll_rel]) + region

    # Growing the collision block pushes the grid map (and anything after it) DOWN by
    # `delta`. import_object appends a CHANGED space-tree leaf's object-index list at the
    # section END — i.e. inside that shifted region — so a leaf added BEFORE collision was
    # baked keeps an idx_ref pointing at the list's OLD offset. Left stale, the leaf reads
    # garbage members and the engine wrongly frustum-culls it AND its leaf-mates (the
    # "imported object + its neighbours blink when you walk away" bug). Bump every
    # space-tree/placement/culling offset (stored BEFORE the collision block) that points
    # into the shifted region by `delta`. Collision-internal offsets are recomputed by
    # serialize_collision_raw, so skip those. No-op for zones with nothing after the map.
    old_map_off = _u32(sec, ds + raw.coll_rel + 0x10)
    new_map_off = _u32(new, ds + raw.coll_rel + 0x10)
    delta = new_map_off - old_map_off
    if delta:
        from xi.zone.xi_zonedef import parse_zonedef
        cb_abs = ds + raw.coll_rel
        # Only bump values that could actually BE offsets. ds+0x18 is a genuine
        # offset field -- the client reads it as `slot[0x19] = ds + [ds+0x18]`, a
        # 256-entry x 0x4c resource table (FFXiMain 10178001..10178014) -- but old
        # pre-production zones carry garbage there (rom/0/33: 0x405FB76B, rom/0/41:
        # 0x428F4658; rom/0/28 and retail hold sane offsets). The client ignores it
        # on those zones because the table is gated on the format version byte at
        # ds+0x03 being >= 0x12, and they are version 5-8. Relocating a garbage
        # value is meaningless and only makes it harder to recognise, so bound the
        # test: a real offset is always inside the section payload.
        payload_limit = len(sec) - ds
        for fpos in parse_zonedef(sec, ds, 0, len(sec)).offset_fields:
            if fpos >= cb_abs:
                continue
            val = _u32(new, fpos)
            if old_map_off <= val < payload_limit:
                _pack32(new, fpos, val + delta)

    total = len(new)
    padded = (total + 15) & ~15
    new += bytearray(padded - total)
    meta = _u32(new, ds)
    _pack32(new, ds, (meta & 0xFF000000) | ((padded - ds - 8) & 0x00FFFFFF))
    _pack32(new, 4, encode_section_meta(padded, SECTION_TYPE_ZONE_DEF, what="0x1C ZoneDef section"))
    return new


def clear_collision(sec: bytearray) -> bytearray:
    """Zero all collision geometry in a *decrypted* 0x1C section, preserving the
    grid structure (cell count) so ``add_collision`` can still bucket new triangles
    into the same world-space grid. Returns a new decrypted section ready to re-encrypt."""
    ds = 0x10
    raw = parse_collision_raw(sec, ds, len(sec) - ds)
    if raw is None:
        return bytearray(sec)
    n_map = len(raw.map_u32)
    # PRESERVE the per-object collision TRANSFORMS (the cull-volume records the engine's
    # culler reads for EVERY placement). Wiping them (the old `transforms=bytearray()`)
    # leaves the 2149 surviving placements pointing at a 0-length transform array -> the
    # culler reads a garbage cull volume -> instant crash on large outdoor (v21+) zones.
    # The tiny indoor template survived only because it had few objects. We zero each
    # transform's cull_group (+0xA8) since the pair-groups it linked to ARE being cleared
    # (matches xi's cross-zone-import convention). Clears only the walkable geometry:
    # meshes, pair-groups, and the grid map that indexes them.
    _TF_SIZE, _TF_CULLGROUP = 0xC0, 0xA8
    kept_transforms = bytearray(raw.transforms)
    for _i in range(len(kept_transforms) // _TF_SIZE):
        struct.pack_into("<I", kept_transforms, _i * _TF_SIZE + _TF_CULLGROUP, 0)
    empty_raw = RawCollision(
        coll_rel=raw.coll_rel,
        # ZERO the space-tree index count (collision header +0x18).
        #
        # This one is counter-intuitive and was diagnosed from a crash dump. The
        # client relocates the section's stored offsets into pointers on load. With
        # idx_count > 0 it runs a SECOND, per-node pass that re-visits the pair
        # graph's mesh references -- so every `mesh` word in a pair gets the section
        # base added TWICE and becomes a wild pointer. The client then faults on the
        # first collision query (FFXiMain +0x168069, `MOV EBP,[ECX+8]`), which for a
        # zone you just entered is the ground-height query, i.e. an instant crash.
        #
        # Proof from pol.exe's dump: pair@0x7A9B18 holds mesh=0x176100 and the
        # section loaded at base 0x36791530, so the pointer should be 0x36907630;
        # ECX at the fault was 0x6D098B60 == base + base + 0x176100. The transform
        # word in the SAME pair relocated correctly once.
        #
        # Baked collision is reachable only through the grid->group->pair graph (one
        # shared identity transform, no per-node ownership), so the per-node pass has
        # nothing legitimate to do and zeroing it is correct for a --replace.
        #
        # Earlier note claimed zeroing this under-allocates the visible-object cull
        # buffer and crashed a 2150-object zone. That was observed for a zone whose
        # placements still owned collision; it does not apply to a flat re-bake, and
        # rom/0/33 (690 placements) loads and runs with it zeroed. If a huge zone ever
        # regresses, that is the trade-off to revisit -- not this line in isolation.
        idx_count=0,
        meshes=[],
        transforms=kept_transforms,
        transforms_old_off=raw.transforms_old_off,
        groups=[],
        map_u32=[0] * n_map,
        tail=b"",
    )
    region = serialize_collision_raw(empty_raw)
    new = bytearray(sec[:ds + raw.coll_rel]) + region
    total = len(new)
    padded = (total + 15) & ~15
    new += bytearray(padded - total)
    meta = _u32(new, ds)
    _pack32(new, ds, (meta & 0xFF000000) | ((padded - ds - 8) & 0x00FFFFFF))
    _pack32(new, 4, encode_section_meta(padded, SECTION_TYPE_ZONE_DEF, what="0x1C ZoneDef section"))
    return new


def clear_zone_collision(dat_path: Path) -> Tuple[Path, int]:
    """Replace all collision geometry in a zone DAT with an empty grid (no meshes).
    Preserves the grid dimensions so ``add_collision_from_obj`` can still append.
    Writes to the same path (direct use; live zones use ``zone reset`` instead).
    Returns (out_path, meshes_removed)."""
    target = editable_dat(dat_path, fresh=False)
    data = bytearray(Path(target).read_bytes())
    sections = parse_sections(data)
    zonedef = next((s for s in sections if s.type_code == SECTION_TYPE_ZONE_DEF), None)
    if zonedef is None:
        raise ValueError("DAT has no 0x1C ZoneDef section")

    table1, _table2 = load_key_tables(Path(FFXI_DIR) / "FFXiMain.dll")
    sec = bytearray(data[zonedef.start:zonedef.start + zonedef.size])
    decrypt_zone_objects(sec, 0x10, 0, len(sec), table1)

    raw = parse_collision_raw(sec, 0x10, len(sec) - 0x10)
    n_removed = len(raw.meshes) if raw else 0

    cleared = clear_collision(sec)
    reencrypt_zone_objects(cleared, 0x10, 0, len(cleared), table1)
    out = bytes(data[:zonedef.start]) + bytes(cleared) + bytes(data[zonedef.start + zonedef.size:])
    Path(target).write_bytes(out)
    return Path(target), n_removed


def remove_object_collision(sec: bytearray, object_indices) -> Tuple[bytearray, int]:
    """Remove the per-object collision of one or more placed objects from a *decrypted*
    0x1C section (data_start = 0x10): drop every (transform, mesh) pair whose transform
    is the object's collision transform. The grid groups stay in place — an emptied
    group simply tests no triangles — so the spatial broad-phase never desyncs (unlike
    MOVING the transform, which leaves the grid pointing at relocated geometry and
    crashes the client). Returns (new decrypted section ready to re-encrypt, pairs removed).

    ``object_indices``: iterable of object indices (== collision-transform indices)."""
    ds = 0x10
    raw = parse_collision_raw(sec, ds, len(sec) - 0x10)
    if raw is None:
        return bytearray(sec), 0
    targets = {raw.transforms_old_off + int(i) * 0xC0 for i in object_indices}
    removed = 0
    emptied = set()
    for g in raw.groups:
        kept = [p for p in g.pairs if p[0] not in targets]
        if len(kept) != len(g.pairs):
            removed += len(g.pairs) - len(kept)
            g.pairs = kept
            g.count_flags = (g.count_flags & ~0x7FF) | (len(kept) & 0x7FF)
            if not kept:
                emptied.add(g.old_rel)
    if not removed:
        return bytearray(sec), 0
    # Drop groups left with zero pairs and point their grid cells at "empty" (0) — a
    # native zero-pair group does not exist and the client crashes reading one. Cells
    # holding map entry 0 = "no collision here" (the native empty-cell pattern).
    if emptied:
        raw.groups = [g for g in raw.groups if g.old_rel not in emptied]
        raw.map_u32 = [0 if off in emptied else off for off in raw.map_u32]
    region = serialize_collision_raw(raw)
    new = bytearray(sec[:ds + raw.coll_rel]) + region
    total = len(new)
    padded = (total + 15) & ~15
    new += bytearray(padded - total)
    meta = _u32(new, ds)
    _pack32(new, ds, (meta & 0xFF000000) | ((padded - ds - 8) & 0x00FFFFFF))
    _pack32(new, 4, encode_section_meta(padded, SECTION_TYPE_ZONE_DEF, what="0x1C ZoneDef section"))
    return new, removed


def remove_placement_collision(dat_path: Path, mesh_name: str) -> Tuple[Path, int, int]:
    """Strip the per-object collision of every placement of ``mesh_name`` in a zone,
    writing the DAT in place. Use this to make a deleted/hidden object's
    spot walk-through cleanly (no leftover invisible blocker, no grid desync). Returns
    (out_path, instances_matched, pairs_removed)."""
    target = editable_dat(dat_path, fresh=False)
    data = bytearray(Path(target).read_bytes())
    zonedef = next((s for s in parse_sections(data) if s.type_code == SECTION_TYPE_ZONE_DEF), None)
    if zonedef is None:
        raise ValueError("DAT has no 0x1C ZoneDef section")
    table1, _ = load_key_tables(Path(FFXI_DIR) / "FFXiMain.dll")
    sec = bytearray(data[zonedef.start:zonedef.start + zonedef.size])
    decrypt_zone_objects(sec, 0x10, 0, len(sec), table1)
    nc = _u32(sec, 0x10 + 4) & 0x00FFFFFF
    name_b = mesh_name.encode("ascii", "replace")[:0x10]
    indices = [i for i in range(nc)
               if sec[0x10 + 0x20 + i * 0x64:0x10 + 0x20 + i * 0x64 + 0x10].split(b"\x00", 1)[0].rstrip(b" ") == name_b.rstrip(b" ")]
    if not indices:
        return Path(target), 0, 0
    new_sec, removed = remove_object_collision(sec, indices)
    reencrypt_zone_objects(new_sec, 0x10, 0, len(new_sec), table1)
    out = bytes(data[:zonedef.start]) + bytes(new_sec) + bytes(data[zonedef.start + zonedef.size:])
    Path(target).write_bytes(out)
    return Path(target), len(indices), removed


def _box_collision_mesh(bbox6, flags: int = 0) -> "RawMesh":
    """Build a closed box collision mesh (object-LOCAL coords) from a bbox. 6 quad faces
    -> 12 tris, each emitted both-facing so it blocks from any approach. Verts are local;
    the object's own collision transform maps them to world (matching native objects)."""
    xmin, xmax, ymin, ymax, zmin, zmax = bbox6
    C = [(xmin, ymin, zmin), (xmax, ymin, zmin), (xmax, ymax, zmin), (xmin, ymax, zmin),
         (xmin, ymin, zmax), (xmax, ymin, zmax), (xmax, ymax, zmax), (xmin, ymax, zmax)]
    faces = [((0, 1, 2, 3), (0, 0, -1)), ((5, 4, 7, 6), (0, 0, 1)),
             ((4, 0, 3, 7), (-1, 0, 0)), ((1, 5, 6, 2), (1, 0, 0)),
             ((4, 5, 1, 0), (0, -1, 0)), ((3, 2, 6, 7), (0, 1, 0))]
    f0, f1, f2, f3 = encode_tri_flags(True, 0)   # hit-wall blocker
    verts = list(C)
    norms: List[Tuple[float, float, float]] = []
    tris: List[Tuple[int, int, int, int]] = []
    for quad, n in faces:
        a, b, c, d = quad
        for (i, j, k) in ((a, b, c), (a, c, d)):
            for nn in (n, (-n[0], -n[1], -n[2])):       # both facings
                ni = len(norms)
                norms.append((float(nn[0]), float(nn[1]), float(nn[2])))
                tris.append((i | (f0 << 12), j | (f1 << 12), k | (f2 << 12), ni | (f3 << 12)))
    return RawMesh(flags=flags, verts=verts, norms=norms, tris=tris, old_rel=0)


def add_object_collision_box(sec: bytearray, object_index: int, bbox6_local,
                             camera_block: bool = True) -> bytearray:
    """Give a placed object a box collider so the camera/player cannot get inside it.
    A brand-new appended object crashes the client at close range (the close-range
    fade/render path xim doesn't model); a solid collider keeps the camera out so that
    path never fires. The box is authored in the object's LOCAL frame and paired with
    the object's OWN collision transform (so it lands exactly where the object renders,
    no coordinate-frame guessing), and the transform's cull-Y AABB is widened so the
    collider is always tested. Returns a NEW decrypted section ready to re-encrypt."""
    ds = 0x10
    raw = parse_collision_raw(sec, ds, len(sec) - 0x10)
    if raw is None:
        raise ValueError("zone has no collision block")
    blocks = (sec[ds + 0xC], sec[ds + 0xD], sec[ds + 0xE], sec[ds + 0xF])
    zbx, zbz, bw, bl = blocks
    cols = zbx * ((bw // 4) or 1)
    rows = zbz * ((bl // 4) or 1)
    tbase = object_index * 0xC0
    wx = struct.unpack_from("<f", raw.transforms, tbase + 0x30)[0]
    wz = struct.unpack_from("<f", raw.transforms, tbase + 0x38)[0]
    # widen the object's collision-transform cull-Y so the collider is never culled
    struct.pack_into("<f", raw.transforms, tbase + 0xB4, -1.0e6)
    struct.pack_into("<f", raw.transforms, tbase + 0xB8, 1.0e6)

    box = _box_collision_mesh(bbox6_local, flags=(0 if camera_block else 1))
    box.old_rel = 0x60000000 + object_index
    raw.meshes.append(box)

    obj_tf_old = raw.transforms_old_off + object_index * 0xC0
    x0, z0 = _cell_of(wx, wz, blocks)
    x0 = min(max(x0, 0), cols - 1)
    z0 = min(max(z0, 0), rows - 1)
    cell = z0 * cols + x0
    groups_by_oldrel = {g.old_rel: g for g in raw.groups}
    existing = raw.map_u32[cell] if 0 <= cell < len(raw.map_u32) else 0
    if existing and existing in groups_by_oldrel:
        g = groups_by_oldrel[existing]
        g.pairs.append((obj_tf_old, box.old_rel))
        g.count_flags = (g.count_flags & ~0x7FF) | ((g.count_flags & 0x7FF) + 1)
    else:
        g = RawGroup(count_flags=1, pairs=[(obj_tf_old, box.old_rel)], old_rel=0x61000000 + object_index)
        raw.groups.append(g)
        if 0 <= cell < len(raw.map_u32):
            raw.map_u32[cell] = g.old_rel

    region = serialize_collision_raw(raw)
    new = bytearray(sec[:ds + raw.coll_rel]) + region
    total = len(new)
    padded = (total + 15) & ~15
    new += bytearray(padded - total)
    meta = _u32(new, ds)
    _pack32(new, ds, (meta & 0xFF000000) | ((padded - ds - 8) & 0x00FFFFFF))
    _pack32(new, 4, encode_section_meta(padded, SECTION_TYPE_ZONE_DEF, what="0x1C ZoneDef section"))
    return new


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def export_collision_obj(dat_path: Path, output_dir: Path,
                         source: Optional[Path] = None
                         ) -> Optional[Tuple[Path, Path, CollisionData]]:
    """Decode a zone DAT's collision mesh and write ``<stem>.collision.obj`` (+ .mtl)
    and ``<stem>.collision.json`` into ``output_dir``. Returns
    (obj_path, json_path, CollisionData) or None if the zone has no collision block."""
    src = source or dat_path
    parsed = decrypted_zonedef(src)
    if parsed is None:
        return None
    buf, ds, _sstart, _ssize = parsed
    data = decode_collision(buf, ds)
    if not data.tris:
        return None
    stem = Path(dat_path).stem
    obj_path = Path(output_dir) / f"{stem}.collision.obj"
    json_path = Path(output_dir) / f"{stem}.collision.json"
    write_collision_obj(data, obj_path)
    write_collision_json(data, json_path, dat_spec=str(dat_path))
    return obj_path, json_path, data


def resolve_collision_obj(dat_path: Path, given) -> Path:
    """Resolve an --add-collision obj path: use it as given if it exists, else look
    for that filename in the zone's export dir (exports/zone/<rom>/), so you can
    pass just ``41.collision.obj`` (or your blocker's name) without the full path."""
    p = Path(given)
    if p.exists():
        return p
    from xi.zone.xi_export import default_output_dir
    out_dir = default_output_dir(dat_path)
    cand = out_dir / p.name
    if cand.is_file():
        return cand
    raise FileNotFoundError(
        f"collision obj '{given}' not found (looked in the current dir and {out_dir})")


def _bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    zs = [p[2] for p in pts]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def add_collision_from_obj(dat_path: Path, obj_path: Path, default_wall: bool = True,
                           default_terrain: int = 0, scale: float = 1.0,
                           camera_transparent: bool = True
                           ) -> Tuple[Path, int, int, List[str]]:
    """Append the triangles in ``obj_path`` to a zone's collision as new blockers,
    writing the DAT in place (layered on any existing edits; the .base backup
    is taken on first edit). Returns
    (out_path, n_tris, n_meshes, warnings).

    ``obj_path`` is resolved against the zone's export dir if not found as given.
    ``scale`` multiplies every obj coordinate (to fix a DCC export-unit mismatch).
    The obj is authored directly over the exported collision/zone (same (x,-y,z)
    frame); existing collision is preserved untouched. Re-running appends again —
    use ``zone reset`` to start clean."""
    obj_path = resolve_collision_obj(dat_path, obj_path)
    tris = parse_collision_obj(obj_path, default_wall=default_wall,
                               default_terrain=default_terrain, scale=scale)
    if not tris:
        raise ValueError(f"no triangles found in {obj_path}")

    target = editable_dat(dat_path, fresh=False)
    data = bytearray(Path(target).read_bytes())
    sections = parse_sections(data)
    zonedef = next((s for s in sections if s.type_code == SECTION_TYPE_ZONE_DEF), None)
    if zonedef is None:
        raise ValueError("DAT has no 0x1C ZoneDef section")

    table1, _table2 = load_key_tables(Path(FFXI_DIR) / "FFXiMain.dll")
    sec = bytearray(data[zonedef.start:zonedef.start + zonedef.size])
    decrypt_zone_objects(sec, 0x10, 0, len(sec), table1)
    raw_before = parse_collision_raw(sec, 0x10, len(sec) - 0x10)
    n_before = len(raw_before.meshes)

    # --- sanity guards ---
    warnings: List[str] = []
    existing = decode_collision(sec, 0x10)
    n_existing = len(existing.tris)
    # Hard stop: feeding the full exported collision would just duplicate it.
    if n_existing and len(tris) >= 0.5 * n_existing:
        raise ValueError(
            f"{Path(obj_path).name} has {len(tris)} triangles - close to the zone's existing "
            f"{n_existing}. This looks like the FULL collision mesh, not a blocker; appending it "
            f"would duplicate the whole zone's collision. --add-collision expects only the NEW "
            f"geometry. (To edit the whole mesh, full-replace mode is coming.)")
    if n_existing and len(tris) >= 0.25 * n_existing:
        warnings.append(
            f"{Path(obj_path).name} has {len(tris)} triangles vs the zone's existing {n_existing} "
            f"- that's a lot for a blocker; double-check you exported only the new geometry.")
    if existing.tris:
        (exmin, exmax) = _bbox([c for t in existing.tris for c in (t.v0, t.v1, t.v2)])
        (amin, amax) = _bbox([c for t in tris for c in (t.v0, t.v1, t.v2)])
        # blocker should overlap the zone's collision bounds; pad a little
        pad = 50.0
        outside = (amin[0] > exmax[0] + pad or amax[0] < exmin[0] - pad or
                   amin[2] > exmax[2] + pad or amax[2] < exmin[2] - pad)
        span_a = max(amax[0] - amin[0], amax[2] - amin[2])
        span_e = max(exmax[0] - exmin[0], exmax[2] - exmin[2])
        if outside:
            # suggest a power-of-10 scale that lands the blocker centre in the zone
            acx, acz = (amin[0] + amax[0]) / 2, (amin[2] + amax[2]) / 2
            suggest = None
            # try factors closest to 1 first (the minimal correction that fits)
            for e in sorted([-4, -3, -2, -1, 1, 2, 3, 4], key=abs):
                s = 10.0 ** e
                if (exmin[0] - pad <= acx * s <= exmax[0] + pad and
                        exmin[2] - pad <= acz * s <= exmax[2] + pad):
                    suggest = s
                    break
            hint = f" Try --scale {suggest:g}." if suggest else \
                " Re-export at FFXI scale, or pass --scale."
            warnings.append(
                f"blocker bbox X[{amin[0]:.0f},{amax[0]:.0f}] Z[{amin[2]:.0f},{amax[2]:.0f}] "
                f"is OUTSIDE the zone's collision X[{exmin[0]:.0f},{exmax[0]:.0f}] "
                f"Z[{exmin[2]:.0f},{exmax[2]:.0f}] - likely a scale/position mismatch "
                f"(coords should be FFXI yalms, ~hundreds).{hint}")
        elif span_e and (span_a > span_e * 5 or span_a < span_e / 5000):
            warnings.append(
                f"blocker is {span_a:.0f} units across vs the zone's ~{span_e:.0f} - "
                f"possible scale mismatch (export units?).")

    grown = add_collision(sec, tris, camera_transparent=camera_transparent)
    # count new meshes (= grid cells touched) on the still-decrypted section
    n_meshes = len(parse_collision_raw(grown, 0x10, len(grown) - 0x10).meshes) - n_before
    reencrypt_zone_objects(grown, 0x10, 0, len(grown), table1)

    out = bytes(data[:zonedef.start]) + bytes(grown) + bytes(data[zonedef.start + zonedef.size:])
    Path(target).write_bytes(out)
    return Path(target), len(tris), n_meshes, warnings

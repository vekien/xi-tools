#!/usr/bin/env python3
"""Export an FFXI zone's static mesh + textures to a self-contained .glb and a
texture-embedded .fbx (Blender), mirroring `entity mesh export`.

Zones store geometry as many encrypted ``0x2E`` ZoneMesh chunks (no skeleton).
Each chunk is decrypted (see _decrypt.py), parsed into static world-space
triangles with UVs and a texture name, then all chunks are combined into one
glTF mesh with the zone's ``0x20`` textures embedded.
"""

import argparse
import math
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from xi.xi_config import XI_TOOLS_DIR, FFXI_DIR, ensure_base, output_path_for, read_path_for
from xi.entity.mesh.xi_export import (
    ROOT_CORRECTION_ROTATION,
    SECTION_TYPE_TEXTURE,
    BufferBuilder,
    TextureImage,
    compute_min_max_vec3,
    convert_glb_to_fbx,
    pack_vec2,
    pack_vec3,
    pack_vec4,
    parse_sections,
    parse_texture,
    resolve_dat_path,
    sanitize_filename,
    write_glb,
)
from xi.utils.xi_core import DEFAULT_ALPHA_SCALE, encode_png_rgba, scale_alpha
from xi.zone.xi_decrypt import decrypt_zone_mesh, decrypt_zone_objects, load_key_tables

SECTION_TYPE_ZONE_MESH = 0x2E
SECTION_TYPE_ZONE_DEF = 0x1C

# Placement record fields we read here. Same layout as xi.zone.xi_objects
# (OFF_LOD_LOW / OFF_FILE / REC_SIZE); duplicated rather than imported because
# xi_objects imports from this module.
OBJ_RECORD_SIZE = 0x64          # retail record (0x54 proto has none of these)
OFF_LOD_LOW = 0x40              # float draw distance — engine stops drawing past this
OFF_FILE = 0x50                 # u32 sub-area id (0 = none)

# FFXI is left-handed; glTF is right-handed. We correct with a 180deg-X rotation
# (ROOT_CORRECTION_ROTATION) composed with a [-1, 1, -1] scale. Net effect:
# (x, y, z) -> (-x, -y, z) -- determinant +1 (a pure rotation, no winding flip).
# This is the SAME orientation the web level editor renders with (zoneRoot:
# quaternion(1,0,0,0), scale(-1,1,-1)) and the same as --right-handed's Rz180, so a
# DCC export, the editor, and a game engine all agree with the in-game layout.
# Negating Y gives Y-up; negating X corrects the east/west (left/right) mirror.
# (Earlier this was [1,1,-1] -> net (x,-y,z), a det -1 REFLECTION that came out
# mirrored east/west vs the editor and the game.) Import is correction-agnostic --
# it inverts the actual node transform -- so the round-trip is unaffected. All zone
# materials are doubleSided, so the det/winding change is not visible.
ZONE_CORRECTION_SCALE = [-1.0, 1.0, -1.0]

# Game-engine orientation (--right-handed). Empirically (verified live in Godot on Lower
# Jeuno) the correct FFXI->Godot zone correction is a PURE 180deg rotation about Z
# (quaternion x,y,z,w), determinant +1. This is the same net orientation as the default
# correction above ((x,y,z) -> (-x,-y,z)); the difference is purely the representation:
# this path emits a single rotation node with NO negative scale, so game engines that
# drop non-uniform/negative node-scale (Godot/Unreal) still get the right layout and
# hittable trimesh collision. A pure rotation keeps lighting normals correct with no
# geometry bake, and round-trips cleanly (Rz180 is its own inverse).
ROT_Z_180 = [0.0, 0.0, 1.0, 0.0]

# Texture alpha scaling (DEFAULT_ALPHA_SCALE / scale_alpha) lives in
# xi.utils.xi_core, shared with the entity-mesh exporter.


@dataclass
class Placement:
    mesh_id: str
    position: Tuple[float, float, float]
    rotation: Tuple[float, float, float]
    scale: Tuple[float, float, float]
    index: int = -1
    # +0x40 draw distance (xi_objects.OFF_LOD_LOW). Exactly 1.0 is the authoring
    # sentinel for collision-only geometry the client never renders; 0.0 = no
    # limit; anything else is a real range. Proto (0x54) records have no such
    # field and come through as 0.0.
    draw_dist: float = 0.0
    # +0x50, the id of the sub-area this placement belongs to (shop and inn
    # interiors; in Ru'Aun Gardens a whole low-detail copy of the sky). Same id
    # space as the 0x36 'm' trigger volumes.
    sub_area_id: Optional[int] = None


def trs_matrix(pos, rot, scale) -> List[float]:
    """Column-major 4x4 = translate(pos) · rotateZYX(rot) · scale(scale),
    matching xim's Matrix4f.rotateZYXInPlace."""
    px, py, pz = pos
    rx, ry, rz = rot
    sx, sy, sz = scale
    sinx, siny, sinz = math.sin(rx), math.sin(ry), math.sin(rz)
    cosx, cosy, cosz = math.cos(rx), math.cos(ry), math.cos(rz)
    # rotation columns (column-major)
    c0 = (cosy * cosz, cosy * sinz, -siny)
    c1 = (sinx * siny * cosz - cosx * sinz, sinx * siny * sinz + cosx * cosz, sinx * cosy)
    c2 = (cosx * siny * cosz + sinx * sinz, cosx * siny * sinz - sinx * cosz, cosx * cosy)
    return [
        c0[0] * sx, c0[1] * sx, c0[2] * sx, 0.0,
        c1[0] * sy, c1[1] * sy, c1[2] * sy, 0.0,
        c2[0] * sz, c2[1] * sz, c2[2] * sz, 0.0,
        px, py, pz, 1.0,
    ]


@dataclass
class ZonePrimitive:
    texture_name: Optional[str]
    # parallel per-corner arrays (triangulated, non-indexed)
    positions: List[Tuple[float, float, float]] = field(default_factory=list)
    normals: List[Tuple[float, float, float]] = field(default_factory=list)
    uvs: List[Tuple[float, float]] = field(default_factory=list)
    colors: List[Tuple[float, float, float, float]] = field(default_factory=list)  # per-corner baked-lighting RGBA 0..1 (GLB COLOR_0, modulate2x baked in); empty = none
    alpha_blend: bool = False  # 0x2E submesh flag 0x8000 — additive/translucent BLEND (water/fog/fire)
    alpha_test: bool = False   # 0x2E submesh flag 0x2000 (see double_sided — this is the SAME bit)
    double_sided: bool = False # 0x2E submesh flag 0x2000 — backface-cull-disable (two-sided: leaves, sprites)


# Skybox / celestial chunk name prefixes — stored at the origin (engine wraps
# them around the camera), so they stack on top of the world geometry.
_SKY_PREFIXES = ("sun", "moon", "star", "clod", "cld", "cloud", "kamo", "suny", "sora", "dust", "fogd", "fog", "haze", "mist")


def is_sky_name(name: str) -> bool:
    n = name.lower()
    return any(n.startswith(p) for p in _SKY_PREFIXES)


def unplaced_vfx_meshes(meshes_by_name, placements) -> set:
    """Names of every 0x2E mesh that has **no 0x1C placement** and is **not sky**.

    Only placed (0x1C) meshes are positioned world geometry. Everything else in the
    DAT is one of: sky (engine wraps it around the camera — handled by ``--no-sky``),
    an effect-placed VFX mesh (water jets, light glows, ``lcut``/``lightstp``,
    positioned only by a 0x05 generator), or dead/unreferenced geometry the client
    never renders (e.g. ``cyst``, ``sh-u``). ``--no-vfx`` drops all of those — they
    otherwise pile up at the origin under the ``unplaced_skybox`` node."""
    placed = set()
    for plc in placements:
        resolved = resolve_mesh_name(plc.mesh_id, meshes_by_name)
        if resolved is not None:
            placed.add(resolved)
    return {n for n in meshes_by_name if n not in placed and not is_sky_name(n)}


@dataclass
class ZoneObject:
    name: str
    prims: List[ZonePrimitive]
    is_sky: bool = False


def _norm(name: str) -> str:
    return name.replace(" ", "").replace("_", "").lower()


def _looks_like_submesh(data, p: int, section_end: int, stride: int) -> bool:
    """True if `p` looks like a submesh header (texture name + sane counts).

    Pre-production zones often leave the 16-byte texture name blank (all NUL/space),
    so the name alone cannot gate it — the vertex and index counts have to check out.
    See docs/zone/prototype-zones.md.
    """
    if p + 20 > section_end:
        return False
    printable = 0
    blank = True
    for i in range(16):
        c = data[p + i]
        if c in (0, 0x20):
            continue
        blank = False
        if c < 0x20 or c > 0x7E:
            return False
        printable += 1
    if not blank and printable < 2:
        return False
    num_verts = struct.unpack_from("<H", data, p + 16)[0]
    if num_verts == 0 or num_verts > 20000:
        return False
    ni_at = p + 20 + num_verts * stride
    if ni_at + 4 > section_end:
        return False
    num_idx = struct.unpack_from("<H", data, ni_at)[0]
    if num_idx == 0 or num_idx > 60000:
        return False
    return ni_at + 4 + num_idx * 2 <= section_end


def parse_zone_mesh_section(data: bytes, section) -> Tuple[str, List[ZonePrimitive]]:
    """Parse one decrypted 0x2E section into (mesh name, textured triangle prims)."""
    ds = section.data_start
    config = struct.unpack_from("<I", data, ds + 4)[0] & 0xFF
    is_strip = (config & 0x1) != 0
    vertex_blend = (config & 0x2) != 0
    mesh_name = data[ds + 0x10 : ds + 0x20].split(b"\x00", 1)[0].decode("ascii", "replace").strip()

    def_start = ds + 0x20
    mesh_count0 = struct.unpack_from("<I", data, def_start)[0]
    if mesh_count0 == 0:
        return mesh_name, []  # collision / "hit" model: bounding box only, no geometry

    # Modern layout: submesh stream offset at +0x3C, count at +0x40. Pre-production
    # sections leave both zero and park the offset at +0x4C / +0x5C instead, and can
    # chain SEVERAL groups (count + bbox + pad = 0x20, then more submeshes).
    def _off_ok(v: int) -> bool:
        return 0 < v < section.size - 0x20

    section1_off = struct.unpack_from("<I", data, ds + 0x3C)[0]
    mesh_count1 = struct.unpack_from("<I", data, ds + 0x40)[0]
    if not _off_ok(section1_off) or section1_off > 0x200:
        for at in (0x4C, 0x5C, 0x50, 0x58):
            alt = struct.unpack_from("<I", data, ds + at)[0]
            if _off_ok(alt) and alt <= 0x200:
                section1_off = alt
                break
    if not _off_ok(section1_off):
        for at in (0x4C, 0x5C, 0x50, 0x58):
            alt = struct.unpack_from("<I", data, ds + at)[0]
            if _off_ok(alt):
                section1_off = alt
                break
    if mesh_count1 == 0 or mesh_count1 > 256:
        mesh_count1 = mesh_count0
    if not _off_ok(section1_off):
        return mesh_name, []

    stride = 48 if vertex_blend else 36
    prims: List[ZonePrimitive] = []
    section_end = section.start + section.size

    def _parse_one(cursor: List[int]) -> bool:
        """Read one submesh at cursor[0], advancing it. False if not a submesh header."""
        p = cursor[0]
        if not _looks_like_submesh(data, p, section_end, stride):
            return False
        texture_name = data[p : p + 0x10].split(b"\x00", 1)[0].decode("ascii", "replace").strip()
        p += 0x10
        num_verts, _flags = struct.unpack_from("<HH", data, p)
        p += 4

        verts = []
        for _v in range(num_verts):
            pos = struct.unpack_from("<3f", data, p)
            if vertex_blend:
                normal = struct.unpack_from("<3f", data, p + 24)  # p0,p1,n0
                c_off = p + 36
            else:
                normal = struct.unpack_from("<3f", data, p + 12)  # p0,n0
                c_off = p + 24
            # per-vertex colour (baked lighting) sits right after the normal, BGRA bytes
            cb, cg, cr, ca = data[c_off], data[c_off + 1], data[c_off + 2], data[c_off + 3]
            color = (cr / 255.0, cg / 255.0, cb / 255.0, ca / 255.0)
            u, v = struct.unpack_from("<2f", data, p + stride - 8)
            verts.append((pos, normal, (u, v), color))
            p += stride

        num_indices, _unk = struct.unpack_from("<HH", data, p)
        p += 4
        indices = list(struct.unpack_from("<%dH" % num_indices, data, p))
        p += num_indices * 2
        p = (p + 3) & ~3  # align0x04 after each mesh
        # Some pre-production meshes leave the strip bit clear on data that is
        # plainly a strip; an index count not divisible by 3 gives it away.
        use_strip = is_strip or (num_indices > 3 and num_indices % 3 != 0)

        prim = ZonePrimitive(texture_name=texture_name or None,
                             alpha_blend=bool(_flags & 0x8000),
                             alpha_test=bool(_flags & 0x2000))
        if use_strip:
            # Degenerate triangles (two identical indices) are restart markers in
            # FFXI strips. Using t%2 for parity would count them and flip the
            # winding of every triangle after the restart. Track parity separately
            # and reset it on each degenerate so winding stays consistent.
            parity = 0
            for t in range(len(indices) - 2):
                i0, i1, i2 = indices[t], indices[t + 1], indices[t + 2]
                if i0 == i1 or i1 == i2 or i0 == i2:
                    parity = 0
                    continue
                a, b, c = verts[i0], verts[i1], verts[i2]
                tri = (a, b, c) if parity % 2 == 0 else (b, a, c)
                parity += 1
                for pos, normal, uv, color in tri:
                    prim.positions.append(pos)
                    prim.normals.append(normal)
                    prim.uvs.append(uv)
                    prim.colors.append(color)
        else:
            for t in range(0, len(indices) - 2, 3):
                for i in (indices[t], indices[t + 1], indices[t + 2]):
                    pos, normal, uv, color = verts[i]
                    prim.positions.append(pos)
                    prim.normals.append(normal)
                    prim.uvs.append(uv)
                    prim.colors.append(color)
        if prim.positions:
            prims.append(prim)
        cursor[0] = p
        return True

    # Primary stream. Read while the headers keep looking like submeshes — a high
    # mesh_count0 often means "N groups" with headers between the streams.
    cursor = [def_start + section1_off]
    for _ in range(64):
        if not _parse_one(cursor):
            break
    high_water = cursor[0]

    def _try_group_at(at: int) -> bool:
        """A chained group: count(u32) + bbox(6xf32) + pad(u32) = 0x20, then submeshes."""
        nonlocal high_water
        if at + 0x30 > section_end or at + 0x20 < high_water:
            return False
        count2 = struct.unpack_from("<I", data, at)[0]
        if count2 < 1 or count2 > 64:
            return False
        name_at = at + 0x20
        if not _looks_like_submesh(data, name_at, section_end, stride):
            return False
        sub = [name_at]
        n = 0
        for _ in range(count2):
            if not _parse_one(sub):
                break
            n += 1
        if n:
            cursor[0] = sub[0]
            high_water = max(high_water, sub[0])
        return n > 0

    for _ in range(8):
        if not _try_group_at(cursor[0]):
            break
    for at in (0x4C, 0x5C):                      # groups the cursor did not reach
        off = struct.unpack_from("<I", data, ds + at)[0]
        if not _off_ok(off) or off <= section1_off:
            continue
        if not _try_group_at(ds + off):
            _try_group_at(def_start + off)
        for _ in range(8):
            if not _try_group_at(cursor[0]):
                break

    return mesh_name, prims


def parse_zone_def(data: bytearray, section, table1: bytes) -> List[Placement]:
    """Decrypt + parse the 0x1C ZoneDef into object placements (id + TRS)."""
    node_count = decrypt_zone_objects(data, section.data_start, section.start, section.size, table1)
    ds = section.data_start
    from xi.zone.xi_zonedef import zonedef_record_size
    record_size = zonedef_record_size(data, ds, node_count)   # 0x64 retail / 0x54 proto
    placements: List[Placement] = []
    for i in range(node_count):
        b = ds + 0x20 + i * record_size
        mesh_id = data[b : b + 0x10].split(b"\x00", 1)[0].decode("ascii", "replace").strip()
        pos = struct.unpack_from("<3f", data, b + 0x10)
        rot = struct.unpack_from("<3f", data, b + 0x1C)
        scale = struct.unpack_from("<3f", data, b + 0x28)
        if record_size >= OBJ_RECORD_SIZE:
            draw_dist = struct.unpack_from("<f", data, b + OFF_LOD_LOW)[0]
            sub_area = struct.unpack_from("<I", data, b + OFF_FILE)[0] or None
        else:
            draw_dist, sub_area = 0.0, None
        placements.append(Placement(mesh_id, pos, rot, scale, index=i,
                                    draw_dist=draw_dist, sub_area_id=sub_area))
    return placements


def parse_zone(dat_path: Path) -> Tuple[Dict[str, List[ZonePrimitive]], List[Placement], Dict[str, TextureImage]]:
    data = bytearray(dat_path.read_bytes())
    sections = parse_sections(data)

    dll_path = Path(FFXI_DIR) / "FFXiMain.dll"
    if not dll_path.is_file():
        raise ValueError(f"FFXiMain.dll not found at {dll_path} (needed for zone decryption)")
    table1, table2 = load_key_tables(dll_path)

    meshes_by_name: Dict[str, List[ZonePrimitive]] = {}
    for section in sections:
        if section.type_code != SECTION_TYPE_ZONE_MESH:
            continue
        decrypt_zone_mesh(data, section.data_start, table1, table2)
        mesh_name, prims = parse_zone_mesh_section(data, section)
        if prims and mesh_name and mesh_name not in meshes_by_name:
            meshes_by_name[mesh_name] = prims

    placements: List[Placement] = []
    for section in sections:
        if section.type_code == SECTION_TYPE_ZONE_DEF:
            placements = parse_zone_def(data, section, table1)
            break

    textures: Dict[str, TextureImage] = {}
    for section in sections:
        if section.type_code == SECTION_TYPE_TEXTURE:
            image = parse_texture(data, section)
            if image is not None and image.name not in textures:
                textures[image.name] = image
    return meshes_by_name, placements, textures


def resolve_mesh_name(mesh_id: str, meshes: Dict[str, List[ZonePrimitive]]) -> Optional[str]:
    """Resolve a placement's mesh id to a parsed mesh, honoring LOD suffixes."""
    if mesh_id in meshes:
        return mesh_id
    base = mesh_id[:-2] if mesh_id[-2:] in ("_l", "_m", "_h") else mesh_id
    for suffix in ("_h", "_m", "_l"):
        if base + suffix in meshes:
            return base + suffix
    return None


# --- placement classes the client does not draw in the base zone view -------
# The 0x1C record carries enough to tell three kinds of placement apart, and a
# straight "draw everything" export stacks all three on top of the real zone.
# Ru'Aun Gardens is the worst case in retail: 45% of its 3283 placements are
# collision proxies, another 592 belong to sub-areas, and 139 are far copies.

COLLISION_DRAW_DISTANCE = 1.0   # +0x40 sentinel: collision only, never rendered

# `m_`/`lnd_` name a cheap stand-in for geometry the zone also places at full
# detail. The prefix alone is not evidence — `m_` equally names ordinary props
# (Mog House furniture `m_bed_02`/`m_dsk_06`, Bastok's `m_pot`, Ro'Maeve's
# `m_pol01_h` pillars) — so a placement only counts as a far copy when the zone
# actually places a richer non-prefixed mesh under the same stem.
_FAR_PREFIX = re.compile(r"^(m_|lnd_)")
_DETAIL_SUFFIX = re.compile(r"_(h|m|n|l)$")


def is_collision_proxy(plc: Placement) -> bool:
    """True when a placement is collision-only geometry the client never draws.

    Retail carries 15,835 of these. Their names say what they are: `hitwall_*`,
    `kabe-atariyou` (wall-collision-use), `hit_*`, `id_board*` / `id_box*`.
    """
    return plc.draw_dist == COLLISION_DRAW_DISTANCE


def far_copy_meshes(meshes_by_name: Dict[str, List[ZonePrimitive]],
                    placements: List[Placement]) -> set:
    """Mesh names that are a cheaper stand-in for richer geometry in this zone.

    In retail this finds Ru'Aun Gardens and its Escha copy (25 meshes, 139
    placements each: `m_osid_*` islands, the `m_bri_*` bridge) plus a single
    `m_wall01` in an unlisted DAT, and nothing else.
    """
    vert_cache: Dict[str, int] = {}

    def nverts(name: str) -> int:
        if name not in vert_cache:
            vert_cache[name] = sum(len(p.positions) for p in meshes_by_name.get(name) or [])
        return vert_cache[name]

    plain: Dict[str, set] = {}
    for plc in placements:
        if is_collision_proxy(plc):
            continue
        name = resolve_mesh_name(plc.mesh_id, meshes_by_name)
        if not name or _FAR_PREFIX.match(name):
            continue
        plain.setdefault(_DETAIL_SUFFIX.sub("", name), set()).add(name)

    far: set = set()
    checked: set = set()
    for plc in placements:
        name = resolve_mesh_name(plc.mesh_id, meshes_by_name)
        if not name or name in checked or not _FAR_PREFIX.match(name):
            continue
        checked.add(name)
        stem = _DETAIL_SUFFIX.sub("", _FAR_PREFIX.sub("", name))
        own = nverts(name)
        for plain_stem, names in plain.items():
            # Exact stem, or the full-detail version split into parts beneath it:
            # `m_osid_cen_a` stands in for `osid_cen_a_lf_h` + `osid_cen_a_rt_h`.
            # The `_` boundary keeps `m_pot` off anything starting with `pot`.
            if plain_stem != stem and not plain_stem.startswith(stem + "_"):
                continue
            if any(nverts(t) > own for t in names):
                far.add(name)
                break
    return far


def filter_placements(meshes_by_name: Dict[str, List[ZonePrimitive]],
                      placements: List[Placement],
                      collision_proxies: bool = False,
                      far_lod: bool = False,
                      sub_areas: bool = True) -> List[Placement]:
    """Drop the placement classes the client does not draw in the base zone."""
    far = set() if far_lod else far_copy_meshes(meshes_by_name, placements)
    out: List[Placement] = []
    for plc in placements:
        if not collision_proxies and is_collision_proxy(plc):
            continue
        if not sub_areas and plc.sub_area_id is not None:
            continue
        if far:
            name = resolve_mesh_name(plc.mesh_id, meshes_by_name)
            if name in far:
                continue
        out.append(plc)
    return out


def resolve_texture(name: Optional[str], textures: Dict[str, TextureImage]) -> Optional[str]:
    if not name:
        return None
    # Prefer exact (then normalized-exact) before the loose substring fallback:
    # zones with sibling names like "stone"/"stone1"/"stone2" or "jimen_00/01/02"
    # would otherwise let "stone" bind to whichever of stone1/stone2 iterates first.
    if name in textures:
        return name
    n = _norm(name)
    for tex_name in textures:
        if _norm(tex_name) == n:
            return tex_name
    for tex_name in textures:
        if n in _norm(tex_name) or _norm(tex_name) in n:
            return tex_name
    return None


def build_glb(dat_path: Path, output_dir: Path, meshes_by_name: Dict[str, List[ZonePrimitive]],
              placements: List[Placement], textures: Dict[str, TextureImage], skip_sky: bool = False,
              raw: bool = False, right_handed: bool = False,
              alpha_scale: float = DEFAULT_ALPHA_SCALE,
              write_loose_textures: bool = True,
              opaque_nonblend: bool = False,
              drop_names: Optional[set] = None,
              out_stem: Optional[str] = None) -> List[Path]:
    # out_stem overrides the .glb filename stem (default: dat_path.stem) — used by
    # --objects to write one <meshname>.glb per object instead of one zone file.
    # drop_names: orphan (unplaced) mesh names to omit entirely — used by --no-vfx
    # to strip effect-placed meshes (see effect_referenced_meshes).
    # write_loose_textures=False keeps textures embedded in the .glb but skips the
    # sidecar .png dump — used by the icon batch, which builds thousands of throwaway
    # single-mesh GLBs and doesn't want a loose PNG per texture per mesh.
    #
    # opaque_nonblend=True renders non-alpha-blend materials as alphaMode OPAQUE instead
    # of MASK. FFXI ignores texture alpha on non-blend submeshes (they're solid), but
    # some opaque textures store alpha ~0 (e.g. "box"); under MASK that clips the whole
    # prop to invisible. Icons use this so every object actually shows up.
    builder = BufferBuilder()
    output_dir.mkdir(parents=True, exist_ok=True)

    images: List[dict] = []
    gltf_textures: List[dict] = []
    materials: List[dict] = []
    material_index: Dict[Optional[str], int] = {}
    texture_paths: List[Path] = []

    # One GLB image entry per tex_key, shared by both the opaque and alpha variants
    # of the same texture (avoids duplicating the PNG data in the buffer).
    tex_image_index: Dict[str, int] = {}  # tex_key → gltf_textures index

    def _ensure_texture(tex_key: str) -> int:
        if tex_key in tex_image_index:
            return tex_image_index[tex_key]
        image = textures[tex_key]
        png_bytes = encode_png_rgba(image.width, image.height, scale_alpha(image.rgba, alpha_scale))
        bv = builder.add_bytes(png_bytes)
        if write_loose_textures:
            png_path = output_dir / f"{sanitize_filename(tex_key)}.png"
            png_path.write_bytes(png_bytes)
            texture_paths.append(png_path)
        images.append({"bufferView": bv, "mimeType": "image/png", "name": tex_key})
        gltf_textures.append({"source": len(images) - 1, "sampler": 0})
        tex_image_index[tex_key] = len(gltf_textures) - 1
        return tex_image_index[tex_key]

    def material_for(tex_key: Optional[str], is_alpha: bool = False) -> int:
        # Keyed by (tex_key, is_alpha) so the same texture can have separate opaque
        # and alpha variants (e.g. tree bark vs leaves sharing the same texture).
        cache_key = (tex_key, is_alpha)
        if cache_key in material_index:
            return material_index[cache_key]
        pbr = {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0.0, "roughnessFactor": 1.0}
        if tex_key is not None and tex_key in textures:
            pbr["baseColorTexture"] = {"index": _ensure_texture(tex_key)}
        name = (tex_key or "untextured").strip() or "untextured"
        if is_alpha:
            name += "_alpha"
        material_index[cache_key] = len(materials)
        mode = "BLEND" if is_alpha else ("OPAQUE" if opaque_nonblend else "MASK")
        materials.append({"name": name, "doubleSided": True,
                          "alphaMode": mode, "pbrMetallicRoughness": pbr})
        return material_index[cache_key]

    # --- one glTF mesh per unique zone mesh (local geometry), built on demand ---
    meshes: List[dict] = []
    mesh_index_by_name: Dict[str, int] = {}

    def mesh_for(name: str) -> int:
        if name in mesh_index_by_name:
            return mesh_index_by_name[name]
        # Group by (tex_key, alpha_blend) so bark and leaves sharing the same texture
        # each get their own material slot (opaque vs alpha variant).
        by_tex: Dict[Tuple[Optional[str], bool], List[ZonePrimitive]] = {}
        for prim in meshes_by_name[name]:
            key = (resolve_texture(prim.texture_name, textures), prim.alpha_blend or prim.alpha_test)
            by_tex.setdefault(key, []).append(prim)
        mesh_prims: List[dict] = []
        for (tex_key, is_alpha), group in by_tex.items():
            positions: List[Tuple[float, float, float]] = []
            normals: List[Tuple[float, float, float]] = []
            uvs: List[Tuple[float, float]] = []
            colors: List[Tuple[float, float, float, float]] = []
            for prim in group:
                positions.extend(prim.positions)
                normals.extend(prim.normals)
                uvs.extend(prim.uvs)
                colors.extend(prim.colors)
            pmin, pmax = compute_min_max_vec3(positions)
            attrs = {
                "POSITION": builder.add_accessor(pack_vec3(positions), 5126, "VEC3", len(positions), target=34962, min_value=pmin, max_value=pmax),
                "NORMAL": builder.add_accessor(pack_vec3(normals), 5126, "VEC3", len(normals), target=34962),
                "TEXCOORD_0": builder.add_accessor(pack_vec2(uvs), 5126, "VEC2", len(uvs), target=34962),
            }
            # COLOR_0 carries the baked vertex lighting with FFXI's modulate2x folded in:
            # the engine draws final = texture * vertexColour * 2, so a raw texture dump
            # looks faint. Pre-doubling RGB here (clamped) makes a glTF viewer's
            # baseColorTexture * COLOR_0 reproduce the in-game brightness — same as the
            # editor's old-view bake in web/leveleditor/main.js (min(1, c*2)). Alpha is
            # pinned to 1.0: the texture already carries the (scale_alpha'd) cutout, and
            # folding doubled vertex-alpha in would punch holes wherever a vert's alpha
            # is low (this zone has verts at 0x00).
            if colors:
                col2x = [(min(1.0, r * 2.0), min(1.0, g * 2.0), min(1.0, b * 2.0), 1.0)
                         for (r, g, b, _a) in colors]
                attrs["COLOR_0"] = builder.add_accessor(pack_vec4(col2x), 5126, "VEC4", len(col2x), target=34962)
            mesh_prims.append({"attributes": attrs, "mode": 4, "material": material_for(tex_key, is_alpha)})
        mesh_index_by_name[name] = len(meshes)
        meshes.append({"name": name, "primitives": mesh_prims})
        return mesh_index_by_name[name]

    # --- one node per placement (instanced), positioned by its TRS matrix ---
    nodes: List[dict] = []
    placed_nodes: List[int] = []
    placed_mesh_names: set = set()
    name_counts: Dict[str, int] = {}
    for plc in placements:
        resolved = resolve_mesh_name(plc.mesh_id, meshes_by_name)
        if resolved is None:
            continue
        placed_mesh_names.add(resolved)
        name_counts[plc.mesh_id] = name_counts.get(plc.mesh_id, 0) + 1
        node_name = plc.mesh_id if name_counts[plc.mesh_id] == 1 else f"{plc.mesh_id}.{name_counts[plc.mesh_id]:03d}"
        mat = trs_matrix(plc.position, plc.rotation, plc.scale)
        nodes.append({"name": node_name, "mesh": mesh_for(resolved), "matrix": mat})
        placed_nodes.append(len(nodes) - 1)

    # --- meshes never placed (skybox / environment) -> at origin, grouped ---
    extra_nodes: List[int] = []
    for name in meshes_by_name:
        if name in placed_mesh_names:
            continue
        if skip_sky and is_sky_name(name):
            continue
        if drop_names and name in drop_names:
            continue
        nodes.append({"name": name, "mesh": mesh_for(name)})
        extra_nodes.append(len(nodes) - 1)

    children = list(placed_nodes)
    if extra_nodes:
        group = len(nodes)
        nodes.append({"name": "unplaced_skybox", "children": extra_nodes})
        children.append(group)

    if raw:
        # No orientation correction — emit raw FFXI coords directly (view-only;
        # will look rotated/handed differently in a Y-up glTF viewer).
        scene_nodes = children
    else:
        root_idx = len(nodes)
        if right_handed:
            # Pure 180deg-Z rotation: correct orientation for game engines, det +1
            # (collision + lighting intact), no geometry bake, no negative scale.
            root_node = {"name": "ffxi_root_correction", "rotation": ROT_Z_180, "children": children}
        else:
            # Legacy DCC path: 180deg-X rotation + a negative-Z scale to display un-mirrored
            # in glTF viewers. Game engines drop the negative scale -> use --right-handed.
            root_node = {"name": "ffxi_root_correction", "rotation": ROOT_CORRECTION_ROTATION,
                         "scale": ZONE_CORRECTION_SCALE, "children": children}
        nodes.append(root_node)
        scene_nodes = [root_idx]

    gltf = {
        "asset": {"version": "2.0", "generator": "xi zone export"},
        "scene": 0,
        "scenes": [{"nodes": scene_nodes}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "buffers": [{"byteLength": len(builder.data)}],
        "bufferViews": builder.buffer_views,
        "accessors": builder.accessors,
    }
    if images:
        gltf["images"] = images
        gltf["textures"] = gltf_textures
        gltf["samplers"] = [{"wrapS": 10497, "wrapT": 10497, "magFilter": 9729, "minFilter": 9729}]

    glb_path = output_dir / f"{out_stem or dat_path.stem}.glb"
    write_glb(gltf, builder.data, glb_path)
    return [glb_path, *texture_paths]


def _write_ue5_mat_script(fbx_path: Path) -> Path:
    """Write a UE5 Python script alongside the FBX that sets 'Enable - Alpha' = 1.0
    on every MaterialInstanceConstant whose name contains '_alpha'.

    Paste into UE5 Output Log > Python console (or Tools > Execute Python Script)
    after importing the FBX. Change IMPORT_PATH to match your content folder first.
    """
    script = '''\
# Generated by xi zone export
# Run in UE5: Output Log > Python console, or Tools > Execute Python Script
# Change IMPORT_PATH to the Content Browser folder where you imported the FBX.
import unreal

IMPORT_PATH = '/Game/ZONES/test'  # <-- CHANGE THIS
PARAM_NAME  = 'Enable - Alpha'

mat_lib  = unreal.EditorAssetLibrary
mat_edit = unreal.MaterialEditingLibrary

changed = 0
for path in mat_lib.list_assets(IMPORT_PATH, recursive=True, include_folder=False):
    asset = mat_lib.load_asset(path)
    if not isinstance(asset, unreal.MaterialInstanceConstant):
        continue
    if '_alpha' not in asset.get_name().lower():
        continue
    mat_edit.set_material_instance_scalar_parameter_value(asset, PARAM_NAME, 1.0)
    mat_lib.save_asset(path, only_if_is_dirty=False)
    changed += 1

print(f'Set {PARAM_NAME}=1 on {changed} material instances')
'''
    out = fbx_path.with_suffix(".ue5_mat.py")
    out.write_text(script)
    return out


def default_output_dir(dat_path: Path) -> Path:
    base = Path(XI_TOOLS_DIR) / "exports" / "zone"
    try:
        rel = dat_path.resolve().relative_to(Path(FFXI_DIR).resolve())
    except ValueError:
        return base / dat_path.stem
    parts = [rel.parts[0].lower(), *rel.parts[1:-1], rel.stem]
    return base.joinpath(*parts)


# ---------------------------------------------------------------------------
# JSON export helpers
# ---------------------------------------------------------------------------

def _parse_weather_tree(data: bytes) -> dict:
    """Walk the weat/ directory tree and extract weather types + ambient sound refs.

    DAT structure (zone root wraps everything):
      <zone_tag>/weat/<weather>/ → 0x3D SeSep sections, one per time slot
        section name = time-of-day in hhmm (e.g. '0000', '0600', '1800')
        section payload @ +8 = u32 sound_id
      <zone_tag>/weat/<weather>/indo/ → same pattern for indoor ambient variant
    """
    from xi.audio import xi_names as _names

    sections = parse_sections(data)
    dir_stack: List[str] = []
    weather_types: List[str] = []
    seen_weather: set = set()
    ambient_sounds = []

    for s in sections:
        name = s.name.rstrip('\x00 ')
        if s.type_code == 0x01:
            dir_stack.append(name)
            # weat/<weather> is at whichever depth 'weat' sits + 1
            if dir_stack and dir_stack[-2] == 'weat' if len(dir_stack) >= 2 else False:
                if name not in seen_weather:
                    weather_types.append(name)
                    seen_weather.add(name)
        elif s.type_code == 0x00:
            if dir_stack:
                dir_stack.pop()
        elif s.type_code == 0x3D and 'weat' in dir_stack:
            if data[s.data_start:s.data_start + 5] != b'SeSep':
                continue
            sound_id = struct.unpack_from('<I', data, s.data_start + 8)[0]
            wi = dir_stack.index('weat')
            weather = dir_stack[wi + 1] if wi + 1 < len(dir_stack) else None
            # indoor if there's any subdir after the weather dir (e.g. 'indo')
            indoors = len(dir_stack) > wi + 2
            # time slot is the 0x3D section's own name (e.g. '0000', '0600')
            time_slot = name if name.isdigit() else None
            _, file_num = _names.sound_id_to_folder_file(sound_id)
            ambient_sounds.append({
                'weather': weather,
                'indoors': indoors,
                'time': time_slot,
                'sound_id': sound_id,
                'file': f'se{file_num}.spw',
                'spw_path': _names.sound_id_to_relpath(sound_id),
                'title': _names.sfx_name(sound_id),
                'category': _names.sfx_category(sound_id),
            })

    return {'types': weather_types, 'ambient_sounds': ambient_sounds}


def _list_dat_directories(data: bytes) -> List[str]:
    """Walk all 0x01/0x00 dir-open/close sections and return every directory path
    as a slash-joined string (e.g. 'weat/fine', 'weat/fine/indr')."""
    sections = parse_sections(data)
    dir_stack: List[str] = []
    paths: List[str] = []
    seen: set = set()
    for s in sections:
        name = s.name.rstrip('\x00 ')
        if s.type_code == 0x01:
            dir_stack.append(name)
            path = '/'.join(dir_stack)
            if path not in seen:
                seen.add(path)
                paths.append(path)
        elif s.type_code == 0x00 and dir_stack:
            dir_stack.pop()
    return paths


def _zone_info(dat_path: Path) -> tuple:
    """Return (zone_id, zone_name) by matching dat_path against the zone list, or (None, None)."""
    try:
        from xi.zone.xi_list import get_zone_entries
        ffxi = Path(FFXI_DIR)
        target = dat_path.resolve()
        for e in get_zone_entries(path_prefix=''):
            if (ffxi / e['path']).resolve() == target:
                return e['id'], e['name']
    except Exception:
        pass
    return None, None


# DB weather ID → (human name, 4-char DAT tag). Index = weather ID (0-19).
_WEATHER_ID_TABLE = [
    (0,  'None',            None),
    (1,  'Sunshine',        'suny'),
    (2,  'Clouds',          'clod'),
    (3,  'Fog',             'mist'),
    (4,  'Hot Spell',       'dryw'),
    (5,  'Heat Wave',       'heat'),
    (6,  'Rain',            'rain'),
    (7,  'Squall',          'squl'),
    (8,  'Dust Storm',      'dust'),
    (9,  'Sand Storm',      'sand'),
    (10, 'Wind',            'wind'),
    (11, 'Gales',           'stom'),
    (12, 'Snow',            'snow'),
    (13, 'Blizzard',        'bliz'),
    (14, 'Thunder',         'thdr'),
    (15, 'Thunderstorms',   'bolt'),
    (16, 'Auroras',         'aura'),
    (17, 'Stellar Glare',   'ligt'),
    (18, 'Gloom',           'fogd'),
    (19, 'Darkness',        'dark'),
]


def _zone_weather_weights(zone_id: int, dat_weather_types: list) -> dict:
    """Decode the zone_weather blob from DB and return per-weather-type weights.

    Each of the 2160 uint16 Vanadiel-day entries packs three 5-bit IDs:
      bits 14-10 = normal  (server rolls this 50% of the time)
      bits  9-5  = common  (35%)
      bits  4-0  = rare    (15%)

    Returns a dict mapping DAT weather tag → {name, id, normal_days, common_days,
    rare_days, in_dat} so the JSON links server weights directly to DAT audio dirs.
    Returns {} if DB unavailable.
    """
    try:
        import struct as _s
        from xi.zone.xi_bridge import _db_connect
        conn = _db_connect({})
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT weather FROM zone_weather WHERE zone = %s", (zone_id,))
                row = cur.fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            return {}
        blob = bytes(row[0])
        n_entries = len(blob) // 2
        counts = {}  # (normal_id, common_id, rare_id) frequencies not needed; track per-slot per-id
        normal_counts: dict = {}
        common_counts: dict = {}
        rare_counts:   dict = {}
        for i in range(n_entries):
            val = _s.unpack_from('<H', blob, i * 2)[0]
            normal_id = (val >> 10) & 0x1F
            common_id = (val >>  5) & 0x1F
            rare_id   =  val        & 0x1F
            normal_counts[normal_id] = normal_counts.get(normal_id, 0) + 1
            common_counts[common_id] = common_counts.get(common_id, 0) + 1
            rare_counts[rare_id]     = rare_counts.get(rare_id, 0)     + 1

        # Build output keyed by DAT tag (or id-str for unknowns), merged across all IDs seen
        all_ids = set(normal_counts) | set(common_counts) | set(rare_counts)
        result = []
        dat_tags = set(dat_weather_types)
        for wid in sorted(all_ids):
            if wid < len(_WEATHER_ID_TABLE):
                _, wname, wtag = _WEATHER_ID_TABLE[wid]
            else:
                wname, wtag = f'Unknown({wid})', None
            result.append({
                'id':          wid,
                'name':        wname,
                'tag':         wtag,
                'in_dat':      wtag in dat_tags if wtag else False,
                'normal_days': normal_counts.get(wid, 0),
                'common_days': common_counts.get(wid, 0),
                'rare_days':   rare_counts.get(wid, 0),
            })
        return {'total_days': n_entries, 'weights': result}
    except Exception:
        return {}


def _zone_music(zone_id: int) -> dict:
    """Query zone_settings for BGM ids and resolve to titles. Returns {} if DB unavailable."""
    try:
        from xi.zone.xi_bridge import _db_connect
        from xi.audio.xi_names import music_name
        conn = _db_connect({})
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT music_day, music_night, battlesolo, battlemulti"
                    " FROM zone_settings WHERE zoneid = %s", (zone_id,))
                row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            return {}
        day, night, bsolo, bmulti = row
        result = {}
        for key, raw in (('day', day), ('night', night), ('battle_solo', bsolo), ('battle_party', bmulti)):
            mid = int(raw or 0)
            result[key] = {'id': mid, 'title': music_name(mid) or None} if mid else None
        return result
    except Exception:
        return {}


def _zone_sound_fx(data: bytes) -> list:
    """All 0x3D SeSep sound refs outside weat/, grouped by their DAT directory path."""
    from xi.audio import xi_names as _names

    sections = parse_sections(data)
    dir_stack: List[str] = []
    results = []

    for s in sections:
        name = s.name.rstrip('\x00 ')
        if s.type_code == 0x01:
            dir_stack.append(name)
        elif s.type_code == 0x00 and dir_stack:
            dir_stack.pop()
        elif s.type_code == 0x3D and 'weat' not in dir_stack:
            if data[s.data_start:s.data_start + 5] != b'SeSep':
                continue
            sound_id = struct.unpack_from('<I', data, s.data_start + 8)[0]
            _, file_num = _names.sound_id_to_folder_file(sound_id)
            results.append({
                'dir': '/'.join(dir_stack) if dir_stack else '',
                'section': name,
                'sound_id': sound_id,
                'file': f'se{file_num}.spw',
                'spw_path': _names.sound_id_to_relpath(sound_id),
                'title': _names.sfx_name(sound_id),
                'category': _names.sfx_category(sound_id),
            })

    return results


def _companion_dats(zone_id: int) -> dict:
    """Return {event, dialog, npc} ROM-relative DAT paths for a zone_id."""
    try:
        from xi.ftable.xi_core import load_all_tables, scan_file_ids
        from xi.zone.xi_inject import zone_event_file_id, zone_dialog_file_id, zone_npc_file_id
        tables = load_all_tables()
        result = {}
        for key, fn in (('event', zone_event_file_id), ('dialog', zone_dialog_file_id), ('npc', zone_npc_file_id)):
            hits = scan_file_ids([fn(zone_id)], tables)
            result[key] = hits[0]['dat'] if hits else None
        return result
    except Exception:
        return {}


def _subarea_list(data: bytes) -> list:
    """Extract sub-area interior DAT refs from a zone DAT's 0x36 section."""
    try:
        from xi.zone.xi_bridge import _scan_zone_subarea_params, _subarea_file_id
        from xi.ftable.xi_core import load_all_tables, scan_file_ids
        ids = _scan_zone_subarea_params(data)
        if not ids:
            return []
        tables = load_all_tables()
        out = []
        for sid in sorted(set(ids)):
            fid = _subarea_file_id(sid)
            hits = scan_file_ids([fid], tables)
            out.append({'id': sid, 'file_id': fid, 'dat': hits[0]['dat'] if hits else None})
        return out
    except Exception:
        return []


def export_zone_json(dat_path: Path, output_dir: Path,
                     source: Optional[Path] = None) -> Path:
    """Export zone metadata (placements, weather audio, companions, sub-areas) to JSON."""
    import json as _json
    from xi.zone.xi_objects import _read_record, OBJ_ARRAY, REC_SIZE

    src = source or dat_path
    data = bytearray(src.read_bytes())
    sections = parse_sections(data)

    dll_path = Path(FFXI_DIR) / "FFXiMain.dll"
    if not dll_path.is_file():
        raise ValueError(f"FFXiMain.dll not found at {dll_path}")
    table1, table2 = load_key_tables(dll_path)

    # --- meshes ---
    mesh_info = []
    sky_meshes = []
    for s in sections:
        if s.type_code != SECTION_TYPE_ZONE_MESH:
            continue
        decrypt_zone_mesh(data, s.data_start, table1, table2)
        mesh_name, prims = parse_zone_mesh_section(data, s)
        if not mesh_name or not prims:
            continue
        textures_used = sorted({p.texture_name for p in prims if p.texture_name})
        flags = {
            'alpha_blend': any(p.alpha_blend for p in prims),
            'alpha_test': any(p.alpha_test for p in prims),
            'double_sided': any(p.double_sided for p in prims),
        }
        entry = {'name': mesh_name, 'textures': textures_used, **flags}
        if is_sky_name(mesh_name):
            sky_meshes.append(entry)
        else:
            mesh_info.append(entry)

    # --- placements ---
    placements = []
    for s in sections:
        if s.type_code != SECTION_TYPE_ZONE_DEF:
            continue
        node_count = decrypt_zone_objects(data, s.data_start, s.start, s.size, table1)
        for i in range(node_count):
            rec = _read_record(data, s.data_start, i)
            if rec['name']:
                placements.append(rec)
        break

    # --- textures ---
    texture_names = []
    for s in sections:
        if s.type_code == SECTION_TYPE_TEXTURE:
            img = parse_texture(data, s)
            if img and img.name not in texture_names:
                texture_names.append(img.name)

    # --- directory tree ---
    dat_directories = _list_dat_directories(bytes(data))

    # --- weather + sky ---
    weather = _parse_weather_tree(bytes(data))

    # --- zone identity + companions ---
    zone_id, zone_name = _zone_info(dat_path)
    companions = _companion_dats(zone_id) if zone_id is not None else {}
    music = _zone_music(zone_id) if zone_id is not None else {}
    sound_fx = _zone_sound_fx(bytes(data))
    weather_weights = _zone_weather_weights(zone_id, weather['types']) if zone_id is not None else {}

    # --- sub-areas ---
    sub_areas = _subarea_list(bytes(data))

    # --- collision presence ---
    has_collision = any(s.type_code == SECTION_TYPE_ZONE_DEF for s in sections)

    payload = {
        'dat': str(dat_path),
        'zone_id': zone_id,
        'zone_name': zone_name,
        'mesh_count': len(mesh_info),
        'placement_count': len(placements),
        'directories': dat_directories,
        'meshes': mesh_info,
        'sky_meshes': sky_meshes,
        'textures': texture_names,
        'placements': placements,
        'music': music,
        'weather': {**weather, 'weights': weather_weights},
        'sound_fx': sound_fx,
        'companion_dats': companions,
        'sub_areas': sub_areas,
        'collision': {'present': has_collision},
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / (dat_path.stem + '.zone.json')
    out_path.write_text(_json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return out_path


def _pristine_source(dat_path: Path) -> Path:
    """The pristine, unedited copy of a DAT: the FFXI_DIR original when an output
    dir is configured, else the legacy ``<dat>.base`` backup (in-place mode)."""
    if output_path_for(dat_path) != Path(dat_path).resolve():
        return Path(dat_path)  # output-dir mode: FFXI_DIR original is untouched
    return dat_path.with_name(dat_path.name + ".base")


def export_objects(dat_path: Path, output_dir: Path,
                   meshes_by_name: Dict[str, List[ZonePrimitive]],
                   textures: Dict[str, TextureImage], fbx: bool = True,
                   raw: bool = False, right_handed: bool = False,
                   alpha_scale: float = DEFAULT_ALPHA_SCALE,
                   skip_sky: bool = False, drop_names: Optional[set] = None) -> List[Path]:
    """Export each unique zone mesh as its own ``<meshname>.glb`` (+ ``.fbx`` if
    ``fbx``) into ``output_dir``. Each object is emitted in local space at the
    origin (its raw geometry), oriented by the same ``ffxi_root_correction`` node
    as a full zone export. ``skip_sky`` / ``drop_names`` (from --no-sky / --no-vfx)
    prune which meshes are written.

    When ``fbx`` is set we MUST write the loose .png textures into ``output_dir``:
    convert_glb_to_fbx rewires the FBX's materials to PNG files sitting next to the
    .fbx (Blender drops packed-GLB image paths), so without them every material
    loses its texture. The shared PNGs dedupe by filename across objects. GLB-only
    exports keep textures embedded and skip the loose PNGs (self-contained)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    names = [n for n in meshes_by_name
             if not (skip_sky and is_sky_name(n)) and not (drop_names and n in drop_names)]
    paths: List[Path] = []
    for i, name in enumerate(names, 1):
        # A single identity-placement so the object is one clean node named after the
        # mesh (a direct child of ffxi_root_correction), not in the orphan group.
        ident = [Placement(mesh_id=name, position=(0.0, 0.0, 0.0),
                           rotation=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0))]
        out = build_glb(dat_path, output_dir, {name: meshes_by_name[name]}, ident, textures,
                        raw=raw, right_handed=right_handed, alpha_scale=alpha_scale,
                        write_loose_textures=fbx, out_stem=sanitize_filename(name))
        paths.append(out[0])
        if fbx:
            print(f"  [{i}/{len(names)}] {name} -> fbx")
            paths.append(convert_glb_to_fbx(out[0]))
    return paths


def export_zone(dat_path: Path, output_dir: Path, fbx: bool = True, skip_sky: bool = False,
                raw: bool = False, right_handed: bool = False, source: Optional[Path] = None,
                collision: bool = False, alpha_scale: float = DEFAULT_ALPHA_SCALE,
                as_json: bool = False, no_vfx: bool = False, objects: bool = False,
                collision_proxies: bool = False, far_lod: bool = False,
                sub_areas: bool = True) -> List[Path]:
    # `source` lets us read the geometry from a different file (e.g. the pristine
    # original) while still naming the output after the original DAT.
    src = source or dat_path
    meshes_by_name, placements, textures = parse_zone(src)
    if not meshes_by_name:
        raise ValueError("No zone mesh (0x2E) geometry found in this DAT")
    # Default to what the client actually draws — see filter_placements.
    kept = filter_placements(meshes_by_name, placements,
                             collision_proxies=collision_proxies,
                             far_lod=far_lod, sub_areas=sub_areas)
    def _names(plcs):
        return {n for n in (resolve_mesh_name(p.mesh_id, meshes_by_name) for p in plcs) if n}
    dropped_meshes = _names(placements) - _names(kept)
    placements = kept
    drop_names = unplaced_vfx_meshes(meshes_by_name, placements) if no_vfx else None
    if dropped_meshes:
        # build_glb emits every mesh with no surviving placement at the origin as
        # an "unplaced" node. Without this, filtering a mesh's last placement
        # resurrects it at 0,0,0 instead of removing it.
        drop_names = (drop_names or set()) | dropped_meshes
    if objects:
        # Per-object mode: one <meshname>.glb/.fbx per mesh straight into output_dir
        # (alongside any --collision/--json), no combined zone glb. They share one set
        # of loose .png textures in the folder so the per-object FBX materials resolve.
        paths = export_objects(dat_path, output_dir, meshes_by_name, textures, fbx=fbx,
                               raw=raw, right_handed=right_handed, alpha_scale=alpha_scale,
                               skip_sky=skip_sky, drop_names=drop_names)
        if as_json:
            paths.append(export_zone_json(dat_path, output_dir, source=source))
        return paths
    paths = build_glb(dat_path, output_dir, meshes_by_name, placements, textures,
                      skip_sky=skip_sky, raw=raw, right_handed=right_handed, alpha_scale=alpha_scale,
                      drop_names=drop_names)
    if fbx:
        fbx_path = convert_glb_to_fbx(paths[0])
        paths.append(fbx_path)
        paths.append(_write_ue5_mat_script(fbx_path))
    if collision:
        # The player-collision mesh lives in the 0x1C ZoneDef section, separate
        # from the visible 0x2E geometry. Emit it as <stem>.collision.obj in the
        # zone glb's frame so it overlays the model in a DCC tool.
        from xi.zone.xi_collision import export_collision_obj
        result = export_collision_obj(dat_path, output_dir, source=source or dat_path)
        if result is not None:
            obj_path, json_path, data = result
            paths.append(obj_path)
            paths.append(obj_path.with_suffix(".mtl"))
            paths.append(json_path)
    if as_json:
        paths.append(export_zone_json(dat_path, output_dir, source=source))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Export an FFXI zone's mesh + textures to .glb / .fbx.")
    parser.add_argument("dat_path", help="Zone DAT path or ROM-relative spec like ROM/1/41")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--fbx", action="store_true", help="Also export a texture-embedded .fbx (for editors that can't open .glb, e.g. C4D)")
    parser.add_argument("--no-sky", dest="skip_sky", action="store_true", help="Omit the skybox/celestial chunks (sun, moon, stars, clouds)")
    parser.add_argument("--no-vfx", dest="no_vfx", action="store_true", help="Omit unplaced (non-world) meshes: effect-placed VFX (water jets, light glows, lcut/lightstp) + dead/unreferenced geometry (cyst, sh-u)")
    parser.add_argument("--objects", dest="objects", action="store_true", help="Export each mesh as its own <meshname>.glb/.fbx into <stem>_objects/ (local space, at origin) instead of one combined zone file")
    parser.add_argument("--raw", action="store_true", help="Omit the orientation-correction node (raw FFXI coords; view-only, do not re-import)")
    parser.add_argument("--right-handed", action="store_true", help="Bake the handedness flip into geometry (for game engines like Godot/Unreal that drop negative node-scale -> un-mirrored, collidable)")
    parser.add_argument("--base", action="store_true", help="Export from the pristine original instead of your edited DAT")
    parser.add_argument("--collision", action="store_true", help="Also dump the player-collision mesh (0x1C MZB) to <stem>.collision.obj")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Also export a <stem>.zone.json with zone metadata (placements, weather audio, companion DATs, sub-areas)")
    parser.add_argument("--with-collision-proxies", dest="collision_proxies", action="store_true",
                        help="Include collision-only placements (draw distance 1.0) the client never renders — hitwall_*, kabe-atariyou, id_board*")
    parser.add_argument("--with-far-lod", dest="far_lod", action="store_true",
                        help="Include far-copy placements (m_/lnd_ stand-ins for richer geometry the zone also places, e.g. Ru'Aun's m_osid_*/m_bri_*)")
    parser.add_argument("--no-subareas", dest="sub_areas", action="store_false", default=True,
                        help="Omit placements tagged with a sub-area id (shop/inn interiors; in Ru'Aun a second low-detail copy of the sky)")
    parser.add_argument("--alpha-scale", type=float, default=DEFAULT_ALPHA_SCALE,
                        help="Multiply texture alpha by this factor before export, clamped to 255 "
                             "(default 2.0 = opaque texels become fully opaque, matching the game; "
                             "pass 1.0 for the raw, faint FFXI alpha)")
    args = parser.parse_args()
    dat_path = resolve_dat_path(args.dat_path)
    if args.base:
        source = _pristine_source(dat_path)
        if not source.is_file():
            print(f"No pristine source found at {source}")
            return 1
    else:
        source = read_path_for(dat_path)  # the live DAT (edits are in place)
    output_dir = args.output or default_output_dir(dat_path)
    for path in export_zone(dat_path, output_dir, fbx=args.fbx, skip_sky=args.skip_sky, raw=args.raw,
                            right_handed=args.right_handed, source=source,
                            collision=args.collision, alpha_scale=args.alpha_scale,
                            as_json=args.as_json, no_vfx=args.no_vfx, objects=args.objects,
                            collision_proxies=args.collision_proxies, far_lod=args.far_lod,
                            sub_areas=args.sub_areas):
        print(f"Exported: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------

import click as _click  # noqa: E402


@_click.command("export")
@_click.argument("dat_path")
@_click.option("--output", type=_click.Path(path_type=Path), default=None,
               help="Output directory (default: exports/zone/<rom path>)")
@_click.option("--fbx", is_flag=True, default=False,
               help="Also export a texture-embedded .fbx via Blender (for editors that can't open .glb, e.g. C4D). Import expects .glb.")
@_click.option("--no-sky", "skip_sky", is_flag=True,
               help="Omit the skybox/celestial chunks (sun, moon, stars, clouds) that sit at the origin")
@_click.option("--no-vfx", "no_vfx", is_flag=True,
               help="Omit every unplaced (non-world) mesh — any 0x2E mesh with no 0x1C placement that "
                    "isn't sky: effect-placed VFX (water jets, light glows, lcut/lightstp) and "
                    "dead/unreferenced geometry (cyst, sh-u). Only placed world geometry remains.")
@_click.option("--objects", "objects", is_flag=True,
               help="Export each mesh as its own <meshname>.glb (+ .fbx with --fbx) into a "
                    "<stem>_objects/ folder, each in local space at the origin — instead of one "
                    "combined zone file. Honors --no-sky/--no-vfx for which meshes are written.")
@_click.option("--raw", is_flag=True,
               help="Omit the orientation-correction node — raw FFXI coords (view-only; a raw export is not meant to be re-imported)")
@_click.option("--right-handed", "right_handed", is_flag=True,
               help="Bake the handedness flip into geometry for game engines (Godot/Unreal drop negative node-scale, which mirrors the zone and breaks collision); un-mirrored and collidable")
@_click.option("--base", "use_base", is_flag=True,
               help="Export from the pristine original instead of your edited DAT — handy to regenerate a clean model after editing")
@_click.option("--collision", is_flag=True,
               help="Also dump the player-collision mesh (the 0x1C MZB triangle soup) to <stem>.collision.obj, "
                    "in the same frame as the glb so it overlays the model. Walls/floors are colour-coded by material.")
@_click.option("--json", "as_json", is_flag=True, default=False,
               help="Also export a <stem>.zone.json with zone metadata: all placements (full TRS + LOD + links), "
                    "mesh list, textures, weather ambient sounds (per-weather per-time-of-day SPW refs), "
                    "companion DAT paths (event/dialog/npc), and sub-area interior DATs.")
@_click.option("--with-collision-proxies", "collision_proxies", is_flag=True,
               help="Include collision-only placements — those with a draw distance of exactly 1.0, "
                    "the sentinel the client treats as never-render. Retail has 15,835 of them "
                    "(hitwall_*, kabe-atariyou, hit_*, id_board*/id_box*); Ru'Aun Gardens is 45% "
                    "collision proxies. Off by default: they stack invisible geometry on the zone.")
@_click.option("--with-far-lod", "far_lod", is_flag=True,
               help="Include far copies — m_/lnd_ meshes that stand in for richer geometry the zone "
                    "also places (Ru'Aun's m_osid_* islands and m_bri_* bridge). The client shows one "
                    "or the other by region, so exporting both puts the cheap copy inside the "
                    "detailed one. Only fires where a richer same-stem twin is actually placed, so "
                    "ordinary m_ props (m_bed_02, m_pot, m_pol01_h) are never affected.")
@_click.option("--no-subareas", "sub_areas", is_flag=True, flag_value=False, default=True,
               help="Omit placements tagged with a sub-area id (+0x50): shop and inn interiors in the "
                    "towns, and in Ru'Aun Gardens a whole second low-detail copy of the sky. Included "
                    "by default — they are real geometry, drawn inside their own volume.")
@_click.option("--alpha-scale", type=float, default=DEFAULT_ALPHA_SCALE, show_default=True,
               help="Multiply texture alpha by this factor before export, clamped to 255. FFXI stores "
                    "alpha at half scale (0x80 = opaque), so the default 2.0 makes opaque texels fully "
                    "opaque (matching the game) while preserving real cutouts/gradients. Pass 1.0 for "
                    "the raw, faint FFXI alpha, or a higher value to force more opacity.")
def cmd(dat_path: str, output, fbx: bool, skip_sky: bool, no_vfx: bool, objects: bool, raw: bool, right_handed: bool, use_base: bool,
        collision: bool, as_json: bool, collision_proxies: bool, far_lod: bool, sub_areas: bool, alpha_scale: float):
    """Export a zone's static mesh + textures to a self-contained .glb.

    DAT_PATH may be a ROM-relative spec like ROM/1/41. Zone meshes are decrypted
    automatically using key tables read from FFXiMain.dll. Each mesh chunk becomes
    its own named object; skybox chunks are grouped under a "skybox" node.

    .glb is the canonical round-trip format (preserves mesh names -> enables
    `zone import --rebuild`, and avoids the Blender FBX name mangling). Pass
    --fbx to also emit an .fbx for editors like Cinema 4D that can't open .glb;
    save your edits back out as .glb for import. By default this exports your
    edited DAT (edits live in place); pass --base to export from
    the pristine original instead.
    """
    try:
        resolved = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise _click.ClickException(str(e))
    ensure_base(resolved)
    if use_base:
        source = _pristine_source(resolved)
        if not source.is_file():
            raise _click.ClickException(f"No pristine source found at {source}")
    else:
        source = read_path_for(resolved)  # the live DAT (edits are in place)
    output_dir = output or default_output_dir(resolved)
    try:
        paths = export_zone(resolved, output_dir, fbx=fbx, skip_sky=skip_sky, raw=raw,
                            right_handed=right_handed, source=source,
                            collision=collision, alpha_scale=alpha_scale, as_json=as_json,
                            no_vfx=no_vfx, objects=objects,
                            collision_proxies=collision_proxies, far_lod=far_lod,
                            sub_areas=sub_areas)
    except ValueError as e:
        raise _click.ClickException(str(e))
    if objects:
        _click.echo(f"Exported {len([p for p in paths if p.suffix == '.glb'])} objects to {output_dir}")
    for path in paths:
        _click.echo(f"Exported: {path}")
    if use_base:
        _click.echo("(exported from the pristine original)")
    if raw:
        _click.echo("(raw: no orientation correction — view-only, do not re-import this model)")


# ---------------------------------------------------------------------------
# Section type table for --tree display
# ---------------------------------------------------------------------------

_SECTION_NAMES = {
    0x00: 'dir-close',
    0x01: 'dir-open',
    0x05: 'particle',
    0x07: 'schedule',
    0x19: 'keyframe',
    0x1C: 'zone-def',
    0x20: 'texture',
    0x21: 'texture-alt',
    0x25: 'water',
    0x2E: 'mesh',
    0x2F: 'environment',
    0x36: 'zone-interactions',
    0x3D: 'sound-pointer',
    0x3E: 'anim-ref',
}

_WEATHER_TAG_TO_NAME = {tag: name for _, name, tag in _WEATHER_ID_TABLE if tag}


def _render_dat_tree(data: bytes, weat_only: bool = False) -> str:
    """Render a human-readable indented section tree of a zone DAT as a string."""
    from xi.audio import xi_names as _names
    from io import StringIO

    out = StringIO()

    def emit(line: str) -> None:
        out.write(line + '\n')

    sections = parse_sections(data)
    dir_stack: List[str] = []

    for s in sections:
        sec_name = s.name.rstrip('\x00 ')
        indent = '  ' * len(dir_stack)
        type_label = _SECTION_NAMES.get(s.type_code, f'0x{s.type_code:02X}')

        if s.type_code == 0x01:
            next_is_weather = dir_stack and dir_stack[-1] == 'weat'
            annotation = ''
            if next_is_weather and sec_name in _WEATHER_TAG_TO_NAME:
                annotation = f'  [{_WEATHER_TAG_TO_NAME[sec_name]}]'
            if not weat_only or 'weat' in dir_stack or sec_name == 'weat':
                emit(f"{indent}[0x01 dir-open]  {sec_name}/{annotation}")
            dir_stack.append(sec_name)

        elif s.type_code == 0x00:
            if dir_stack:
                closing = dir_stack[-1]
                if not weat_only or 'weat' in dir_stack:
                    emit(f"{indent}[0x00 dir-close] /{closing}")
                dir_stack.pop()

        elif s.type_code == 0x3D:
            if weat_only and 'weat' not in dir_stack:
                continue
            if data[s.data_start:s.data_start + 5] != b'SeSep':
                emit(f"{indent}[0x3D sound-pointer]  {sec_name}  (bad magic)")
                continue
            sound_id = struct.unpack_from('<I', data, s.data_start + 8)[0]
            spw = _names.sound_id_to_relpath(sound_id)
            title = _names.sfx_name(sound_id)
            cat = _names.sfx_category(sound_id)
            detail = f'  "{title}"' if title else (f'  ({cat})' if cat else '')
            emit(f"{indent}[0x3D sound-pointer]  {sec_name}  -> id={sound_id}  {spw}{detail}")

        else:
            if weat_only and 'weat' not in dir_stack:
                continue
            payload_size = s.size - 0x10
            emit(f"{indent}[0x{s.type_code:02X} {type_label}]  {sec_name}  ({payload_size} bytes)")

    return out.getvalue()


def _print_dat_tree(data: bytes, weat_only: bool = False) -> None:
    """Print a human-readable indented section tree of a zone DAT."""
    _click.echo(_render_dat_tree(data, weat_only=weat_only), nl=False)


@_click.command("tree")
@_click.argument("dat_path")
@_click.option("--weat", "weat_only", is_flag=True, default=False,
               help="Show only the weat/ subtree (weather audio + sky sections)")
def tree_cmd(dat_path: str, weat_only: bool):
    """Dump the section tree of a zone DAT to a .txt file.

    Output is written to exports/zone/<rom path>/tree.txt (or tree-weat.txt
    with --weat). Directory open/close sections (0x01/0x00) are indented to
    show the folder hierarchy; sound pointers (0x3D) include the resolved SPW path.

    \b
      xi zone tree ROM/0/99           -> exports/zone/rom/0/99/tree.txt
      xi zone tree ROM/0/99 --weat    -> exports/zone/rom/0/99/tree-weat.txt
    """
    try:
        resolved = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise _click.ClickException(str(e))
    data = read_path_for(resolved).read_bytes()
    text = _render_dat_tree(data, weat_only=weat_only)
    stem = 'tree-weat' if weat_only else 'tree'
    out_path = default_output_dir(resolved) / f'{stem}.txt'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding='utf-8')
    _click.echo(f"Written: {out_path}")

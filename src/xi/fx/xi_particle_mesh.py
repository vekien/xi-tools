#!/usr/bin/env python3
"""Decode `0x1F` ParticleMesh and `0x21` SpriteSheetMesh sections — the stored
geometry an effect's generators draw.

`0x2E` ZoneMesh is the geometry a *zone* effect places (the fountain splash quad).
Spell, ability and NPC effects don't use it: their geometry is a `0x1F`
ParticleMesh, and their billboards a `0x21` SpriteSheetMesh. Both parse into the
same `ZonePrimitive` list the zone exporter's `build_glb` consumes, so an effect
mesh exports through the identical GLB path.

Layout reference: the client's own loader, `CMoD3m::Open` / `GetShortPointer` /
`GetAnotherShortPointer` / `GetFloatPointer` in the `xiclient` decompile
(`research/external/xiclient/src/XIClient/source/Resource/Derived/CMoD3m.cpp`),
cross-checked byte-for-byte against retail DATs. See
[docs/fx/particle_mesh.md](../../../docs/fx/particle_mesh.md).
"""

import struct
from dataclasses import dataclass
from typing import Dict, List, Optional

from xi.zone.xi_export import ZonePrimitive

PARTICLE_MESH_TYPE = 0x1F
SPRITE_MESH_TYPE = 0x21

# 36 bytes: D3DFVF_XYZ | D3DFVF_NORMAL | D3DFVF_DIFFUSE | D3DFVF_TEX1.
_PM_VERTEX_STRIDE = 36
# 24 bytes: position + diffuse + uv (a sprite card carries no normal).
_SPRITE_VERTEX_STRIDE = 24
_SPRITE_CARD_VERTS = 6
_SPRITE_CARD_STRIDE = 4 + _SPRITE_CARD_VERTS * _SPRITE_VERTEX_STRIDE   # 148
_SPRITE_DATA_START = 0x18


@dataclass
class ParticleMesh:
    """One decoded `0x1F`/`0x21` section."""
    fourcc: str                     # section DatId ("wa  ", "bind", "tama")
    section_type: int               # 0x1F or 0x21
    marker: int                     # low nibble of the header word (5/6 = array layout)
    materials: List[str]            # 16-char texture tags, in table order
    triangles: int
    prims: List[ZonePrimitive]


def _material_table_offset(mat_count: int, extra_count: int) -> int:
    """Byte offset of the material table within the payload.

    Transcribed from `CMoD3m::GetAnotherShortPointer` (marker 5/6 branch): the
    per-material triangle counts start at payload+8 as `u16[matCount+extraCount]`,
    and the table that follows is aligned by rounding that count DOWN to a
    multiple of 4 and adding 3 — not by rounding the byte offset up to 16. The two
    agree for 1-3 entries (offset 14) and diverge after, which is why a generic
    16-byte alignment reads two bytes into every vertex.
    """
    n = mat_count + extra_count
    rem = n & 3
    shorts = n if rem == 0 else n - rem + 3
    return 8 + 2 * shorts


def _read_tag(payload: bytes, off: int) -> str:
    """A 16-byte material tag. This is the referenced `0x20` texture's own 16-char
    name verbatim (`"bind    tayu    "`), so it keys straight into the texture map
    the zone/entity parsers build."""
    return payload[off:off + 16].decode("latin1", "replace")


def _diffuse(word: int):
    """D3DCOLOR diffuse -> (r, g, b, a) in 0..1, stored little-endian as B,G,R,A.

    FFXI authors these at half range (0x80 = white) and the client doubles them on
    load — the same modulate2x convention the zone meshes use, so the values are
    left raw here and `build_glb` folds the x2 in with everything else.
    """
    b = word & 0xFF
    g = (word >> 8) & 0xFF
    r = (word >> 16) & 0xFF
    a = (word >> 24) & 0xFF
    return (r / 255.0, g / 255.0, b / 255.0, a / 255.0)


def parse_particle_mesh(data: bytes, section) -> Optional[ParticleMesh]:
    """Parse one `0x1F` ParticleMesh section. Returns None if it doesn't decode."""
    ds = section.data_start
    payload = bytes(data[ds:section.start + section.size])
    if len(payload) < 14:
        return None

    flags = struct.unpack_from("<H", payload, 0)[0]
    marker = flags & 0xF
    # 5 and 6 are the vertex-array layouts. 7 is what the client REWRITES the
    # marker to when its own validation fails, and <5 is a different resource
    # shape entirely (CMoD3m parks those at payload+0x50).
    if marker not in (5, 6):
        return None

    mat_count = payload[4]
    extra_count = payload[5]
    tri_count = struct.unpack_from("<H", payload, 6)[0]
    if tri_count == 0:
        return None

    mat_off = _material_table_offset(mat_count, extra_count)
    vert_off = mat_off + 16 * mat_count
    corners = tri_count * 3
    if vert_off + corners * _PM_VERTEX_STRIDE > len(payload):
        return None

    materials = [_read_tag(payload, mat_off + 16 * i) for i in range(mat_count)]

    # Per-entry triangle counts (payload+8) split the run across materials. Retail
    # meshes seen so far are all single-material with counts[0] == tri_count, so
    # treat the split as advisory: fall back to one primitive if it doesn't add up.
    counts = [struct.unpack_from("<H", payload, 8 + 2 * i)[0]
              for i in range(mat_count + extra_count)]
    split = counts[:mat_count] if mat_count and sum(counts[:mat_count]) == tri_count else None

    prims: List[ZonePrimitive] = []
    cursor = vert_off
    groups = ([(materials[i], split[i]) for i in range(mat_count)] if split
              else [(materials[0] if materials else None, tri_count)])

    for tex_name, tris in groups:
        # Particle geometry is shells, rings and cards — always drawn two-sided.
        # The blend mode is NOT here: it lives on the generator that draws this
        # mesh (`0x05` sec2 opcode 0x1E BlendFunc).
        prim = ZonePrimitive(texture_name=tex_name, double_sided=True)
        for _ in range(tris * 3):
            px, py, pz = struct.unpack_from("<3f", payload, cursor)
            nx, ny, nz = struct.unpack_from("<3f", payload, cursor + 12)
            col = struct.unpack_from("<I", payload, cursor + 24)[0]
            u, v = struct.unpack_from("<2f", payload, cursor + 28)
            prim.positions.append((px, py, pz))
            prim.normals.append((nx, ny, nz))
            prim.uvs.append((u, v))
            prim.colors.append(_diffuse(col))
            cursor += _PM_VERTEX_STRIDE
        if prim.positions:
            prims.append(prim)

    if not prims:
        return None
    return ParticleMesh(fourcc=_fourcc_of(data, section), section_type=PARTICLE_MESH_TYPE,
                        marker=marker, materials=materials, triangles=tri_count, prims=prims)


def parse_sprite_mesh(data: bytes, section) -> Optional[ParticleMesh]:
    """Parse one `0x21` SpriteSheetMesh section — N flat cards sharing one texture.

    Each card is a 4-byte header plus six 24-byte vertices (two triangles), i.e.
    the quad a sprite-sheet particle billboards. There is no normal in the data;
    the cards face -Z in their own space and the generator orients them.
    """
    ds = section.data_start
    payload = bytes(data[ds:section.start + section.size])
    if len(payload) < _SPRITE_DATA_START + _SPRITE_CARD_STRIDE:
        return None

    card_count = struct.unpack_from("<H", payload, 2)[0]
    if card_count == 0:
        return None
    if _SPRITE_DATA_START + card_count * _SPRITE_CARD_STRIDE > len(payload):
        return None

    tag = _read_tag(payload, 8)
    prim = ZonePrimitive(texture_name=tag, double_sided=True)

    for c in range(card_count):
        base = _SPRITE_DATA_START + c * _SPRITE_CARD_STRIDE + 4
        for i in range(_SPRITE_CARD_VERTS):
            off = base + i * _SPRITE_VERTEX_STRIDE
            px, py, pz = struct.unpack_from("<3f", payload, off)
            col = struct.unpack_from("<I", payload, off + 12)[0]
            u, v = struct.unpack_from("<2f", payload, off + 16)
            prim.positions.append((px, py, pz))
            prim.normals.append((0.0, 0.0, -1.0))
            prim.uvs.append((u, v))
            prim.colors.append(_diffuse(col))

    if not prim.positions:
        return None
    return ParticleMesh(fourcc=_fourcc_of(data, section), section_type=SPRITE_MESH_TYPE,
                        marker=0, materials=[tag], triangles=card_count * 2, prims=[prim])


def _fourcc_of(data: bytes, section) -> str:
    return bytes(data[section.start:section.start + 4]).decode("latin1")


def particle_meshes(data: bytes, sections) -> Dict[str, ParticleMesh]:
    """Every decodable `0x1F`/`0x21` mesh in a DAT, keyed by section FourCC.

    A NAME map, matching how a generator names its geometry — so when a DAT reuses
    a FourCC across directory scopes (ROM/0/0 does, for 14 of its 61 meshes) the
    first one wins, the way the client's local-then-parent lookup resolves it.
    """
    out: Dict[str, ParticleMesh] = {}
    for s in sections:
        parse = (parse_particle_mesh if s.type_code == PARTICLE_MESH_TYPE
                 else parse_sprite_mesh if s.type_code == SPRITE_MESH_TYPE else None)
        if parse is None:
            continue
        try:
            mesh = parse(data, s)
        except struct.error:
            continue
        if mesh is not None and mesh.fourcc not in out:
            out[mesh.fourcc] = mesh
    return out

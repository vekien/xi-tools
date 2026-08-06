#!/usr/bin/env python3
"""Serialize a zone ``0x2E`` mesh section — the inverse of
``export.parse_zone_mesh_section`` — so we can *grow* an existing zone object's
geometry (mesh-merge) or write a brand-new mesh (import-object). Geometry is
written as TRI-MESH (config bit0=0; indices are plain groups of 3), non-blend
(stride 36) sub-meshes.

NOTE: degenerate triangle STRIPS were tried (config bit0=1) and broke winding +
exploded the triangle count, which mis-rendered/crashed in-game. Tri-mesh is the
verified-working format — do NOT switch back to strips.
"""

import struct
from typing import Dict, List, Optional, Tuple

from xi.common.xi_section import encode_section_meta
from xi.zone.xi_decrypt import decrypt_zone_mesh
from xi.zone.xi_export import ZonePrimitive

SECTION_TYPE_ZONE_MESH = 0x2E
_DEFAULT_BGRA = (128, 128, 128, 128)


def _split_prim_triangles(prim: ZonePrimitive, max_verts: int = 0xFFFF):
    """Split a prim's triangle soup into chunks whose deduplicated vertex count
    stays <= max_verts (the per-sub-mesh u16 limit). Yields (verts, tri_indices)
    per chunk as a plain TRIANGLE LIST (indices in groups of 3). Triangles are
    kept whole; identical (pos, normal, uv) corners are deduplicated within a chunk."""
    pos, nrm, uv, col = prim.positions, prim.normals, prim.uvs, prim.colors
    seen: Dict[Tuple, int] = {}
    verts: List = []
    indices: List[int] = []
    for t in range(0, len(pos) - 2, 3):
        tri_keys = [(pos[t + k], nrm[t + k], uv[t + k], col[t + k] if col else None) for k in range(3)]
        new = sum(1 for key in set(tri_keys) if key not in seen)
        # both numVerts and numIndices are u16 -> cap verts AND indices (3/tri)
        if verts and (len(verts) + new > max_verts or len(indices) + 3 > max_verts):
            yield verts, indices
            seen, verts, indices = {}, [], []
        for key in tri_keys:
            j = seen.get(key)
            if j is None:
                j = len(verts); seen[key] = j; verts.append(key)
            indices.append(j)
    if verts:
        yield verts, indices


def _bbox(prims: List[ZonePrimitive]):
    xs, ys, zs = [], [], []
    for p in prims:
        for x, y, z in p.positions:
            xs.append(x); ys.append(y); zs.append(z)
    if not xs:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def encode_zone_mesh_section(mesh_name: str, prims: List[ZonePrimitive],
                             original_section: bytes,
                             encrypt_tables: Optional[Tuple[bytes, bytes]] = None,
                             shade: float = 1.0) -> bytes:
    """Build a complete, padded ``0x2E`` section for ``mesh_name`` from triangulated
    ``prims`` (one or more sub-meshes per prim). Reuses the original section's
    author and encryption key (byte5) + pass2 trigger; forces config = tri-mesh,
    non-blend so it matches the data we write. Returns encrypted bytes ready to
    splice into a DAT.

    ``original_section`` = the full original 0x2E section bytes (still ENCRYPTED) —
    used for the 4-byte section name, the data-area author and the encryption
    header (mode/key/pass2). The 16-byte mesh-name field is written from the
    explicit ``mesh_name`` argument (so import-object embeds the correct name)."""
    # --- sub-mesh body: per prim -> texname(16) + numVerts/flags + verts + indices ---
    mesh_data = bytearray()
    sub_meshes = 0
    for prim in prims:
        if not prim.positions:
            continue
        texname = (prim.texture_name or "").strip().encode("ascii", "replace")[:0x10].ljust(0x10, b" ")
        # Per-submesh material flags word (FFXI 0x2E convention): 0x2000 = backface-cull
        # DISABLE, i.e. two-sided (leaves, foliage cards, bird/insect sprites — verified in
        # East Ronfaure: only leaf + hato submeshes carry it). 0x8000 = additive/translucent
        # BLEND (water/fog/fire). Alpha-cutout is NOT this bit — the client keys leaf cutout
        # off the mesh NAME's leading '_' (see _xi_prefixed in xi_apply_changes). `alpha_test`
        # is the legacy name for the same 0x2000 bit (GLB alphaMode=MASK → two-sided, which
        # is what leaves want); `double_sided` sets it explicitly, independent of alpha/opaque.
        sub_flags = ((0x2000 if (getattr(prim, "alpha_test", False) or getattr(prim, "double_sided", False)) else 0)
                     | (0x8000 if getattr(prim, "alpha_blend", False) else 0))
        # tri-mesh format: indices are plain groups of 3 (config bit0=0). A prim that
        # dedups to >65535 verts/indices is split into multiple sub-meshes (u16 limit).
        for verts, tri_idx in _split_prim_triangles(prim):
            mesh_data += texname
            mesh_data += struct.pack("<HH", len(verts), sub_flags)
            for pos, nrm, uv, col in verts:
                # FFXI vertex colour is HALF-scale (0x80 = full, doubled at draw). Map the
                # GLB's per-vertex RGBA (0..1) to 0x80-scale and apply the shade multiplier
                # to RGB only (brightness — leave alpha for transparency). No GLB colour ->
                # neutral 0x80 (× shade). Stored BGRA. shade=1 + no colour = legacy 0x80.
                def _q(v: float) -> int:
                    return max(0, min(255, round(v * 128.0 * shade)))
                if col is None:
                    rb = gb = bb = max(0, min(255, round(128.0 * shade)))
                    ab = 128
                else:
                    rb, gb, bb = _q(col[0]), _q(col[1]), _q(col[2])
                    ab = max(0, min(255, round(col[3] * 128.0)))
                # Normalize: the client doesn't renormalize, so a non-unit normal (e.g. one
                # that picked up a scaled node's factor on import) blows up the diffuse term
                # and the mesh renders fullbright. Cheap belt-and-suspenders at the write gate.
                nl = (nrm[0] * nrm[0] + nrm[1] * nrm[1] + nrm[2] * nrm[2]) ** 0.5 or 1.0
                nrm = (nrm[0] / nl, nrm[1] / nl, nrm[2] / nl)
                mesh_data += struct.pack("<3f", *pos) + struct.pack("<3f", *nrm)
                mesh_data += struct.pack("<4B", bb, gb, rb, ab) + struct.pack("<2f", *uv)
            mesh_data += struct.pack("<HH", len(tri_idx), 0x0000)
            mesh_data += struct.pack("<%dH" % len(tri_idx), *tri_idx)
            while len(mesh_data) % 4:
                mesh_data += b"\x00"
            sub_meshes += 1

    bmin, bmax = _bbox(prims)
    bbox6 = (bmin[0], bmax[0], bmin[1], bmax[1], bmin[2], bmax[2])

    # --- def header (0x40 bytes, indexed from defStart = data 0x20) ---
    #   +0x00 meshCount0   +0x04 bbox0(6f)   +0x1C section1Off   +0x20 meshCount1
    #   +0x24 bbox1(6f)    ... meshes begin at defStart + section1Off (= 0x40 here)
    defh = bytearray(0x40)
    struct.pack_into("<I", defh, 0x00, 1 if sub_meshes else 0)       # meshCount0
    struct.pack_into("<6f", defh, 0x04, *bbox6)                      # bbox0
    struct.pack_into("<I", defh, 0x1C, 0x40)                         # section1Off (meshes after defh)
    struct.pack_into("<I", defh, 0x20, sub_meshes)                   # meshCount1
    struct.pack_into("<6f", defh, 0x24, *bbox6)                      # bbox1

    ds = 0x10
    orig_ds = original_section[ds:ds + 8]                            # 8-byte encryption header
    # Use the explicit mesh_name rather than the template's name so cross-DAT imports
    # (import-object) get the correct name embedded in the section body.
    name16 = mesh_name.encode("ascii", "replace")[:0x10].ljust(0x10, b" ")
    author8 = original_section[ds + 0x08:ds + 0x10]                  # author field

    # config byte: keep original key (byte5) + pass2 trigger (bytes6-7); force
    # flags = tri-mesh (bit0=0), non-blend (bit1=0) to match the data we write.
    cfg = bytearray(orig_ds[4:8])
    cfg[0] = cfg[0] & 0xFC
    mode = orig_ds[3] & 0xFF

    body = bytearray()
    body += b"\x00\x00\x00\x00"            # meta placeholder (patched after size known)
    body += bytes(cfg)                     # config u32 (flags/key/pass2)
    body += author8                        # author (8)
    body += name16                         # mesh name (16)
    body += defh                           # def header (0x40)
    body += mesh_data

    total_size = len(body)
    struct.pack_into("<I", body, 0, (total_size & 0x00FFFFFF) | (mode << 24))

    section_size = 0x10 + len(body)
    padded = (section_size + 15) & ~15
    out = bytearray()
    out += original_section[0:4]                                    # 4-byte section name
    out += struct.pack("<I", encode_section_meta(padded, SECTION_TYPE_ZONE_MESH,
                                                 what="0x2E ZoneMesh section"))
    out += b"\x00" * 8
    out += body
    out += b"\x00" * (padded - len(out))

    if (out[ds + 3] & 0xFF) >= 5 and encrypt_tables:
        buf = bytearray(out)
        decrypt_zone_mesh(buf, ds, encrypt_tables[0], encrypt_tables[1])  # self-inverse = encrypt
        return bytes(buf)
    return bytes(out)

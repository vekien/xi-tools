#!/usr/bin/env python3
"""``xi object export``, ``xi object replace``, ``xi object import``
— per-mesh operations on zone DATs.

export-object  Export a single named zone mesh to GLB (+ optional FBX).
replace-object Re-encode an existing zone mesh section from an edited GLB.
import-object  Add a brand-new mesh section (+ optional placement) from a GLB.
"""

import struct
from pathlib import Path
from typing import List, Optional, Tuple

import click

_DEBUG = False

from xi.entity.anim.xi_export import parse_sections
from xi.entity.mesh.xi_export import resolve_dat_path
from xi.xi_config import (FFXI_DIR, editable_dat, output_path_for, read_path_for,
                            hd_editable_dat, hd_path_for)
from xi.zone.xi_decrypt import (load_key_tables, decrypt_zone_mesh, reencrypt_zone_mesh,
                                   decrypt_zone_objects, reencrypt_zone_objects)
from xi.zone.xi_export import (
    SECTION_TYPE_ZONE_MESH, SECTION_TYPE_TEXTURE, ZonePrimitive, build_glb,
    parse_zone, parse_zone_mesh_section, convert_glb_to_fbx,
)
from xi.zone.xi_mesh import encode_zone_mesh_section


# ---------------------------------------------------------------------------
# export-object helpers
# ---------------------------------------------------------------------------

def default_object_output_dir(dat_path: Path, mesh_name: str) -> Path:
    """Default object export location under exports/object/<rom>/<mesh_name>/.

    e.g. ``ROM/1/41.DAT`` + ``block03`` ->
    ``exports/object/rom/1/41/block03/``.
    """
    from xi.xi_config import XI_TOOLS_DIR

    try:
        rel = dat_path.resolve().relative_to(Path(FFXI_DIR).resolve())
        parts = [rel.parts[0].lower(), *rel.parts[1:-1], rel.stem]
    except ValueError:
        parts = [dat_path.stem]
    return Path(XI_TOOLS_DIR) / "exports" / "object" / Path(*parts) / mesh_name


def export_object(dat_path: Path, mesh_name: str, output_dir: Path, fbx: bool) -> List[Path]:
    """Export a single named zone mesh to GLB (+ optional FBX)."""
    meshes_by_name, placements, textures = parse_zone(read_path_for(dat_path))

    # Exact match first, then LOD fallback (same logic as resolve_mesh_name).
    if mesh_name not in meshes_by_name:
        base = mesh_name[:-2] if mesh_name[-2:] in ("_l", "_m", "_h") else mesh_name
        for suffix in ("_h", "_m", "_l"):
            if base + suffix in meshes_by_name:
                mesh_name = base + suffix
                break
    if mesh_name not in meshes_by_name:
        available = ", ".join(sorted(meshes_by_name)[:20])
        raise ValueError(
            f"Mesh '{mesh_name}' not found in this DAT.\n"
            f"  First 20 available: {available}")

    # Export just this mesh — no placements (single object, lands at origin).
    # Pass a fake path whose stem is the mesh name so build_glb names the file
    # <mesh_name>.glb instead of <dat_stem>.glb.
    named_path = dat_path.with_name(mesh_name).with_suffix(".DAT")
    paths = build_glb(named_path, output_dir, {mesh_name: meshes_by_name[mesh_name]},
                      [], textures)
    dll = Path(FFXI_DIR) / "FFXiMain.dll"
    table1, table2 = load_key_tables(dll)
    data = bytearray(read_path_for(dat_path).read_bytes())
    for sec in parse_sections(data):
        if sec.type_code != SECTION_TYPE_ZONE_MESH:
            continue
        work = bytearray(data)
        decrypt_zone_mesh(work, sec.data_start, table1, table2)
        name, _ = parse_zone_mesh_section(work, sec)
        if name == mesh_name:
            raw_path = output_dir / f"{mesh_name}.zone2e"
            raw_path.write_bytes(bytes(data[sec.start:sec.start + sec.size]))
            paths.append(raw_path)
            break
    if fbx:
        paths.append(convert_glb_to_fbx(paths[0]))
    return paths


# ---------------------------------------------------------------------------
# replace-object helpers
# ---------------------------------------------------------------------------

# GLB import coordinate transform.
#
# A GLB's orientation lives in TWO places: the per-vertex accessor data AND the
# node hierarchy (Blender bakes its Z-up→Y-up + object rotations into node
# transforms on export). The editor honours both — it renders gltf.scene, which
# applies every node's local matrix down the tree. So the importer MUST bake each
# mesh node's world matrix into the vertices first, or it drops Blender's export
# rotation (seen as a 180° spin on the imported model).
#
# After baking the GLB node world matrix, one final axis transform converts to
# FFXI space. In the editor the GLB is wrapped by a node that copies zoneRoot's
# quaternion (1,0,0,0) — which in THREE's (x,y,z,w) form is a 180° rotation about
# X — AND scale (-1,1,-1). Its linear part is:
#   R_x(180°) · S(-1,1,-1) = diag(1,-1,-1) · diag(-1,1,-1) = diag(-1,-1,1)
# Editor GLB chain:  zoneRoot · pnode(TRS) · wrap · node_world · v_local
# Native zone chain: zoneRoot · pnode(TRS) · v_ffxi
# Equal under the same pnode ⇒ v_ffxi = diag(-1,-1,1) · node_world · v_local
#   i.e. bake node_world, then (-x, -y, z). (FFXI is Y-up: Y is the up-axis flip.)
#
# Zone re-exports instead wrap vertices in an ffxi_root_correction node and store
# them already in FFXI space, so for those we read raw accessor data unchanged
# (the verified byte-exact round-trip path) — no node matrix, no axis flip.
_ROOT_CORRECTION_NODE = "ffxi_root_correction"
_IMPORT_PREFIX = "xi_"
_IDENTITY4 = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]


def _mat_vec3(m, v, translate: bool) -> Tuple[float, float, float]:
    """Transform a 3-vector by a column-major 4x4. translate=False for directions
    (normals), which ignore the matrix's translation column."""
    x, y, z = v[0], v[1], v[2]
    rx = m[0] * x + m[4] * y + m[8] * z
    ry = m[1] * x + m[5] * y + m[9] * z
    rz = m[2] * x + m[6] * y + m[10] * z
    if translate:
        rx += m[12]; ry += m[13]; rz += m[14]
    return (rx, ry, rz)


def _custom_import_name(requested: str, existing: set[str]) -> str:
    """Allocate a <=16-byte mesh name for an imported custom zone object.

    The requested name is used VERBATIM (xi-namespaced, editor '.NNN' display suffix
    stripped, clamped to 16 bytes) whenever it is free — so the published mesh identity
    matches what the editor shows AND what sibling placements (copy-paste / re-import)
    reference. Only a genuine collision with an existing mesh appends a numeric counter.

    Mirrors the editor's xiName() / _xi_prefixed: a leading '_' / '#' (the client's
    alpha-test / foliage-cutout selector) is preserved and the xi namespace inserted
    after it, so '_jag_w02_m' -> '_xi_jag_w02_m' (still byte[0] == '_')."""
    name = requested.strip()
    # Drop the level-editor's '.NNN' duplicate-display suffix — that uniquifier labels
    # instances in the editor, it must never end up in the stored mesh id.
    base, dot, tail = name.rpartition(".")
    if dot and tail.isdigit():
        name = base
    # Ensure the xi_ namespace (preserving any leading '_' / '#'); clamp to 16 bytes.
    if name.startswith((_IMPORT_PREFIX, "_" + _IMPORT_PREFIX, "#" + _IMPORT_PREFIX)):
        candidate = name[:0x10]
    elif name[:1] in ("_", "#"):
        candidate = (name[0] + _IMPORT_PREFIX + name[1:])[:0x10]
    else:
        candidate = (_IMPORT_PREFIX + name)[:0x10]
    if candidate and candidate not in existing:
        return candidate
    # Collision: append a 2-digit counter, trimming the stem to stay within 16 bytes.
    stem = candidate[:0x10 - 2]
    n = 1
    while True:
        numbered = f"{stem}{n:02d}"
        if numbered not in existing:
            return numbered
        n += 1


def _baked_uniform_scale(pos, scale) -> float:
    """The uniform factor import_object BAKES into the mesh geometry (then resets that
    placement's scale to 1.0 — see import_object). Returns 1.0 when no baking happens.

    Anything that places ANOTHER instance of the same baked mesh (copy-paste, xiId
    siblings in apply-changes) MUST divide its placement scale by this factor, or it
    double-applies the scale and the copy renders ~baked² too small (invisible). The
    injector itself is already normalised to 1.0 inside import_object."""
    if pos is None or scale is None:
        return 1.0
    sx, sy, sz = scale
    if sx == sy == sz and sx > 0 and sx != 1.0:
        return float(sx)
    return 1.0


def _source_dat_from_export_path(glb_path: Path) -> Optional[Path]:
    parts = list(glb_path.parts)
    lower = [p.lower() for p in parts]
    try:
        rom_i = lower.index("rom")
    except ValueError:
        return None
    # exports/object/rom/<dir>/<dat>/<mesh>/<mesh>.glb -> ROM/<dir>/<dat>
    rel_parts = parts[rom_i + 1:-2]
    if not rel_parts:
        return None
    try:
        return resolve_dat_path(str(Path("ROM", *rel_parts)))
    except FileNotFoundError:
        return None


def _rename_mesh_section(section_bytes: bytes, final_mesh_name: str, table1: bytes, table2: bytes) -> bytes:
    section = bytearray(section_bytes)
    if len(section) < 0x30:
        raise ValueError("Raw mesh section is too small")
    meta = struct.unpack_from("<I", section, 4)[0]
    if (meta & 0x7F) != SECTION_TYPE_ZONE_MESH:
        raise ValueError("Raw section is not a 0x2E zone mesh section")
    section[0:4] = final_mesh_name.encode("ascii", "replace")[:4].ljust(4)
    decrypt_zone_mesh(section, 0x10, table1, table2)
    section[0x20:0x30] = final_mesh_name.encode("ascii", "replace")[:0x10].ljust(0x10, b" ")
    reencrypt_zone_mesh(section, 0x10, table1, table2)
    return bytes(section)


def _copy_exported_mesh_section(glb_path: Path, source_mesh_name: str, final_mesh_name: str,
                                table1: bytes, table2: bytes) -> Optional[bytes]:
    """Copy a mesh section from the DAT that produced an export-object GLB.

    This preserves native triangle-strip data exactly; reserializing GLB triangle
    soup can produce strips the client renders/culls differently.
    """
    src_dat = _source_dat_from_export_path(glb_path)
    if src_dat is None:
        return None
    data = bytearray(read_path_for(src_dat).read_bytes())
    for sec in parse_sections(data):
        if sec.type_code != SECTION_TYPE_ZONE_MESH:
            continue
        work = bytearray(data)
        decrypt_zone_mesh(work, sec.data_start, table1, table2)
        name, _ = parse_zone_mesh_section(work, sec)
        if name != source_mesh_name:
            continue
        return _rename_mesh_section(bytes(data[sec.start:sec.start + sec.size]), final_mesh_name, table1, table2)
    return None


def _parse_mesh_section_bytes(section_bytes: bytes, table1: bytes, table2: bytes) -> List[ZonePrimitive]:
    data = bytearray(section_bytes)
    sections = parse_sections(data)
    if not sections or sections[0].type_code != SECTION_TYPE_ZONE_MESH:
        raise ValueError("Raw section is not a valid 0x2E zone mesh section")
    decrypt_zone_mesh(data, sections[0].data_start, table1, table2)
    _name, prims = parse_zone_mesh_section(data, sections[0])
    return prims


# FFXI submesh vertex/index fields are u16: a single submesh holds at most 65535 verts
# (≈21,845 tris). Beyond that a mesh is split into multiple submeshes; very high counts
# split many ways and render badly / too dense for the engine. Reject over the limit so
# the user decimates rather than silently shipping a mangled mesh.
ZONE_MESH_TRI_LIMIT = 65535


def _check_tri_budget(prims: List[ZonePrimitive], mesh_name: str) -> None:
    tris = sum(len(p.positions) // 3 for p in prims if p.positions)
    if tris > ZONE_MESH_TRI_LIMIT:
        raise ValueError(
            f"Mesh '{mesh_name}': {tris:,} triangles exceeds the {ZONE_MESH_TRI_LIMIT:,}-triangle "
            f"limit for an FFXI zone mesh. Decimate the model (e.g. Blender's Decimate modifier) "
            f"to a few thousand faces and re-export."
        )


def _orient_winding_ffxi(prim: ZonePrimitive) -> int:
    """Reverse each triangle whose winding disagrees with FFXI's front-face convention.

    Verified against real zone meshes (Ro'Maeve / Upper Jeuno / Altar): FFXI front-faces
    wind so the triangle's GEOMETRIC normal (cross of its two edges) points OPPOSITE the
    stored vertex normals (~100% of real tris) — the reverse of the standard CCW winding
    that GLB exporters (Blender, etc.) produce. Left as-is, imported single-sided faces
    get backface-culled in-game (the 'normals look backwards' inconsistency). Enforcing
    the convention per-triangle — using the stored normals as ground truth for 'outward'
    — makes every imported mesh render front-side-out regardless of the source winding.

    Idempotent: a triangle already matching FFXI is left untouched, so re-exported FFXI
    meshes (which already wind this way) pass through unchanged. Operates on the
    triangle-soup prim in place; returns the number of triangles flipped."""
    P, N, U, C = prim.positions, prim.normals, prim.uvs, prim.colors
    flipped = 0
    for t in range(0, len(P) - 2, 3):
        ax, ay, az = P[t + 1][0] - P[t][0], P[t + 1][1] - P[t][1], P[t + 1][2] - P[t][2]
        bx, by, bz = P[t + 2][0] - P[t][0], P[t + 2][1] - P[t][1], P[t + 2][2] - P[t][2]
        gx, gy, gz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx   # geometric normal
        nx = N[t][0] + N[t + 1][0] + N[t + 2][0]
        ny = N[t][1] + N[t + 1][1] + N[t + 2][1]
        nz = N[t][2] + N[t + 1][2] + N[t + 2][2]
        if gx * nx + gy * ny + gz * nz > 0.0:   # winding agrees with normal -> wrong for FFXI
            P[t + 1], P[t + 2] = P[t + 2], P[t + 1]
            N[t + 1], N[t + 2] = N[t + 2], N[t + 1]
            U[t + 1], U[t + 2] = U[t + 2], U[t + 1]
            if C:
                C[t + 1], C[t + 2] = C[t + 2], C[t + 1]
            flipped += 1
    return flipped


def _read_glb_primitives(glb_path: Path, orig_prims: List[ZonePrimitive]) -> List[ZonePrimitive]:
    """Extract mesh primitives from a GLB and convert to ZonePrimitive list.

    Walks the node hierarchy so each mesh node's world matrix is baked into its
    vertices (matching how the editor renders gltf.scene), then converts to FFXI
    space. See the module-level note above _ROOT_CORRECTION_NODE for the math.
    Zone re-exports (ffxi_root_correction present) keep raw FFXI-space vertices."""
    from xi.entity.mesh.xi_import import load_gltf_document, read_accessor, read_indices
    from xi.zone.xi_import import _mat_mul, node_local_matrix

    doc, buffers = load_gltf_document(glb_path)
    meshes = doc.get("meshes", [])
    nodes = doc.get("nodes", [])

    has_correction = any(n.get("name") == _ROOT_CORRECTION_NODE for n in nodes)
    # Custom GLB: bake node world, then negate X,Y. Zone re-export: raw passthrough.
    post = (lambda v: (v[0], v[1], v[2])) if has_correction else (lambda v: (-v[0], -v[1], v[2]))

    out: List[ZonePrimitive] = []
    # Map mesh index → prim index in orig_prims (for texture_name recovery).
    orig_tex_by_idx = {i: (orig_prims[i].texture_name if i < len(orig_prims) else None)
                       for i in range(len(meshes))}

    def emit_mesh(mesh_idx: int, world) -> None:
        for prim_data in meshes[mesh_idx].get("primitives", []):
            attrs = prim_data.get("attributes", {})
            pos_acc = attrs.get("POSITION")
            if pos_acc is None:
                continue
            nor_acc = attrs.get("NORMAL")
            uv_acc  = attrs.get("TEXCOORD_0")
            col_acc = attrs.get("COLOR_0")

            raw_pos = read_accessor(doc, buffers, pos_acc)
            raw_nor = read_accessor(doc, buffers, nor_acc) if nor_acc is not None else None
            raw_uv  = read_accessor(doc, buffers, uv_acc)  if uv_acc  is not None else None
            raw_col = read_accessor(doc, buffers, col_acc) if col_acc is not None else None
            idx_acc = prim_data.get("indices")
            indices = read_indices(doc, buffers, prim_data, len(raw_pos)) if idx_acc is not None else list(range(len(raw_pos)))

            # Triangulate: groups of 3 indices -> triangle soup.
            positions: List[Tuple[float, float, float]] = []
            normals:   List[Tuple[float, float, float]] = []
            uvs:       List[Tuple[float, float]]        = []
            colors:    List[Tuple[float, float, float, float]] = []
            for i in range(0, len(indices) - 2, 3):
                for j in (indices[i], indices[i + 1], indices[i + 2]):
                    positions.append(post(_mat_vec3(world, raw_pos[j], True)))
                    if raw_nor:
                        # Re-normalize: _mat_vec3 applies the node's full linear transform,
                        # so a scaled node (e.g. a 100x-authored model placed at 0.01) bakes
                        # that scale into the normal magnitude too. FFXI / the client do not
                        # renormalize, so a non-unit normal blows up the diffuse term and the
                        # mesh renders fullbright regardless of vertex colour. Force unit length.
                        nx, ny, nz = post(_mat_vec3(world, raw_nor[j], False))
                        nlen = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
                        normals.append((nx / nlen, ny / nlen, nz / nlen))
                    else:
                        normals.append((0.0, 1.0, 0.0))
                    if raw_uv:
                        uv = raw_uv[j]
                        uvs.append((uv[0], uv[1]))
                    else:
                        uvs.append((0.0, 0.0))
                    if raw_col:
                        c = list(raw_col[j])
                        if c and max(c) > 1.5:  # normalized-int colors read as 0-255 / 0-65535
                            d = 65535.0 if max(c) > 255.5 else 255.0
                            c = [v / d for v in c]
                        while len(c) < 4:
                            c.append(1.0)
                        colors.append((c[0], c[1], c[2], c[3]))

            # Try to recover texture name from the original mesh (same prim index).
            tex_name = orig_tex_by_idx.get(mesh_idx) or None
            # Resolve the GLB material for the texture-name fallback AND the alpha mode.
            mat = {}
            mat_idx = prim_data.get("material")
            if mat_idx is not None:
                mats = doc.get("materials", [])
                mat = mats[mat_idx] if mat_idx < len(mats) else {}
            if not tex_name:
                tex_name = mat.get("name") or tex_name
            # GLB alphaMode -> FFXI submesh flag: MASK = alpha-cutout foliage (0x2000),
            # BLEND = additive/translucent (0x8000), OPAQUE = neither. A leaf material
            # authored as "Alpha Clip" in Blender exports as MASK and becomes a real cutout.
            alpha_mode = (mat.get("alphaMode") or "OPAQUE").upper()

            prim = ZonePrimitive(texture_name=tex_name)
            prim.positions = positions
            prim.normals   = normals
            prim.uvs       = uvs
            prim.colors    = colors
            prim.alpha_test = (alpha_mode == "MASK")
            prim.alpha_blend = (alpha_mode == "BLEND")
            # Enforce FFXI's front-face winding so the mesh isn't backface-culled in-game,
            # regardless of how the source GLB was wound (the common 'backwards normals' bug).
            _orient_winding_ffxi(prim)
            out.append(prim)

    if has_correction:
        # Verified zone re-export path: vertices already in FFXI space; ignore the
        # node hierarchy and read raw accessor data (world = identity, post = no-op).
        for mesh_idx in range(len(meshes)):
            emit_mesh(mesh_idx, _IDENTITY4)
    else:
        # Custom GLB: recurse the scene graph, accumulating world matrices, and bake
        # each mesh node's world transform into its vertices (as the editor does).
        scenes = doc.get("scenes", [])
        scene  = doc.get("scene", 0)
        roots  = scenes[scene]["nodes"] if scenes else list(range(len(nodes)))

        def visit(index: int, parent_world) -> None:
            node = nodes[index]
            world = _mat_mul(parent_world, node_local_matrix(node))
            if node.get("mesh") is not None:
                emit_mesh(node["mesh"], world)
            for child in node.get("children", []):
                visit(child, world)

        for r in roots:
            visit(r, _IDENTITY4)

    if not out:
        raise ValueError("No mesh primitives found in the GLB.")
    return out


def _bake_uniform_scale(prims, scale) -> None:
    """Multiply every vertex position by a UNIFORM POSITIVE scale, in place. Normals
    are unaffected by uniform scale, so they're left as-is. No-op for None/1.0 or a
    non-uniform/negative scale (those must stay on the placement)."""
    if scale is None:
        return
    sx, sy, sz = scale
    if not (sx == sy == sz and sx > 0 and sx != 1.0):
        return
    for p in prims:
        p.positions = [(x * sx, y * sx, z * sx) for (x, y, z) in p.positions]


def _build_texture_blobs(data: bytearray, sections, prims, glb_path: Path,
                         max_size: int | None = None, force_opaque: bool = False,
                         force_replace: bool = False) -> bytearray:
    """Build 0x20 texture sections for every texture referenced by ``prims`` that is
    not already present in ``data``. Returns the concatenated section bytes (to splice
    before the trailing terminator). Each texture is sourced from an external
    ``<name>.png`` beside the GLB, else the GLB's embedded image, and encoded at its
    SOURCE resolution (power-of-two) clamped to ``max_size`` — which itself is hard-
    capped to ``TEXTURE_CLAMP`` from xi_config. Zone-safe: DXT3, FFXI half-alpha.

    force_replace: when True, existing texture sections whose name matches a texture in
    ``prims`` are spliced out of ``data`` so the freshly-encoded version is used instead.
    Required for Refresh-from-disk / replace-object: without it the old pixels survive
    because the ``existing`` guard skips re-injection of same-named textures."""
    from xi.entity.mesh.xi_export import parse_texture as _parse_texture
    from xi.entity.mesh.xi_import import encode_png_to_texture_section
    from xi.utils.xi_core import DEFAULT_ALPHA_SCALE
    from xi.xi_config import TEXTURE_CLAMP
    import tempfile

    cap = TEXTURE_CLAMP if max_size is None else min(max_size, TEXTURE_CLAMP)

    existing: dict[str, object] = {}   # name -> Section (for optional removal)
    for sec in sections:
        if sec.type_code == SECTION_TYPE_TEXTURE:
            img = _parse_texture(bytes(data), sec)
            if img:
                existing[img.name.strip()] = sec
    existing_names = set(existing)
    embedded = _extract_embedded_glb_textures(glb_path) if glb_path else {}
    ffxi_alpha = 1.0 / DEFAULT_ALPHA_SCALE

    blobs = bytearray()
    seen = set()
    to_remove = []   # old sections to splice out (force_replace path only)
    for prim in prims:
        raw_tex = (prim.texture_name or "").strip()
        if not raw_tex or raw_tex in seen:
            continue
        if raw_tex in existing_names and not force_replace:
            continue
        fourcc = raw_tex.replace(" ", "")[:4].ljust(4, "_")
        candidates = [glb_path.parent / f"{raw_tex}.png",
                      glb_path.parent / f"{raw_tex.replace(' ', '_')}.png",
                      glb_path.parent / f"{raw_tex.replace('   ', '_')}.png"]
        png_path = next((p for p in candidates if p.exists()), None)
        blob = None
        with tempfile.TemporaryDirectory() as tmp:
            if png_path is not None:
                blob = encode_png_to_texture_section(fourcc, raw_tex, png_path, Path(tmp),
                                                     force_format="DXT3", max_size=cap, alpha_scale=ffxi_alpha,
                                                     force_opaque=force_opaque)
            elif raw_tex in embedded:
                tmp_png = Path(tmp) / f"{raw_tex}.png"
                if _write_as_png(embedded[raw_tex], tmp_png):
                    blob = encode_png_to_texture_section(fourcc, raw_tex, tmp_png, Path(tmp),
                                                         force_format="DXT3", max_size=cap, alpha_scale=ffxi_alpha,
                                                         force_opaque=force_opaque)
                else:
                    click.echo(f"[texture] '{raw_tex}' embedded image could not be decoded — skipped", err=True)
            else:
                click.echo(f"[texture] '{raw_tex}' not found (no external PNG, no embedded texture) — skipped", err=True)
        if blob:
            verb = "replaced" if raw_tex in existing_names else "injected"
            click.echo(f"[texture] '{raw_tex}' {verb} into DAT", err=True)
            if raw_tex in existing_names:
                to_remove.append(existing[raw_tex])
            blobs += blob
            seen.add(raw_tex)

    # Splice out old sections in reverse-offset order so earlier removals don't shift
    # the positions of later ones. Callers must re-parse sections after this returns.
    for sec in sorted(to_remove, key=lambda s: s.start, reverse=True):
        del data[sec.start:sec.start + sec.size]

    return blobs


def replace_object(dat_path: Path, mesh_name: str, glb_path: Path,
                   scale: tuple | None = None, import_textures: bool = True,
                   tex_size: int | None = None, opaque: bool = False,
                   shade: float = 1.0, bake_ao: bool = False,
                   ao_floor: float = 0.45) -> Path:
    """Replace a zone mesh section in-place from an edited GLB. Re-encrypts and writes
    the DAT back in place. ``scale`` bakes a uniform factor into the geometry (e.g. 0.01
    for a centimetre-scale Blender model going into a scale-1.0 slot like funsui) —
    the replaced mesh rides the target object's EXISTING placement, so the geometry
    must already be at the target's world size.

    ``import_textures``: use the GLB's OWN material/texture (and inject it into the
    DAT) instead of keeping the original mesh's texture reference. This is what makes
    a CUSTOM model replacing e.g. funsui show its own skin rather than the fountain's.
    The texture comes from a <name>.png beside the GLB or the GLB's embedded image."""
    dll = Path(FFXI_DIR) / "FFXiMain.dll"
    if not dll.exists():
        raise FileNotFoundError(f"FFXiMain.dll not found: {dll}")
    table1, table2 = load_key_tables(dll)

    src = editable_dat(dat_path, fresh=False)
    data = bytearray(src.read_bytes())
    sections = parse_sections(data)

    # Find the target 0x2E section by decrypting each one and checking its name.
    target_section = None
    orig_prims: List[ZonePrimitive] = []
    for sec in sections:
        if sec.type_code != SECTION_TYPE_ZONE_MESH:
            continue
        buf = bytearray(data)
        decrypt_zone_mesh(buf, sec.data_start, table1, table2)
        name, prims = parse_zone_mesh_section(buf, sec)
        if name == mesh_name:
            target_section = sec
            orig_prims = prims
            break

    if target_section is None:
        raise ValueError(f"Mesh '{mesh_name}' not found in {dat_path.name}.")

    # Parse the edited GLB into ZonePrimitives. When importing the GLB's own texture,
    # pass [] so texture names come from the GLB material (not the original mesh's).
    new_prims = _read_glb_primitives(glb_path, [] if import_textures else orig_prims)
    _check_tri_budget(new_prims, mesh_name)
    _bake_uniform_scale(new_prims, scale)

    # Bake ambient occlusion into the vertex colours so a flat custom mesh gets the
    # self-shadowing retail geometry carries (otherwise it renders as a glowing slab —
    # see xi_ao). Skipped for prims that already have GLB vertex colours.
    if bake_ao:
        from xi.zone.xi_ao import bake_vertex_ao
        n_ao = bake_vertex_ao(new_prims, floor=ao_floor)
        if n_ao:
            click.echo(f"[object] baked ambient occlusion into {n_ao} vertices (floor {ao_floor})", err=True)

    # Re-encode the 0x2E section (borrows name/author/key from original bytes).
    orig_bytes = bytes(data[target_section.start: target_section.start + target_section.size])
    new_section = encode_zone_mesh_section(mesh_name, new_prims, orig_bytes,
                                           encrypt_tables=(table1, table2), shade=shade)

    # Splice the new section in place of the old one (may resize — sections are
    # self-describing, so later offsets just shift; the 0x1C references by name).
    data[target_section.start: target_section.start + target_section.size] = new_section

    # Inject the GLB's own texture(s) — replacing any existing section with the same
    # name so the updated pixels from a re-exported GLB are actually used.
    if import_textures:
        sections2 = parse_sections(data)
        tex_blobs = _build_texture_blobs(data, sections2, new_prims, glb_path, max_size=tex_size,
                                         force_opaque=opaque, force_replace=True)
        if tex_blobs:
            sections2 = parse_sections(data)   # re-parse: old tex sections may have been spliced out
            last_mesh = max((s for s in sections2 if s.type_code == SECTION_TYPE_ZONE_MESH),
                            key=lambda s: s.start)
            insert_at = last_mesh.start + last_mesh.size
            data[insert_at:insert_at] = tex_blobs

    src.write_bytes(bytes(data))
    return output_path_for(dat_path)


# ---------------------------------------------------------------------------
# import-object helpers
# ---------------------------------------------------------------------------

def _write_as_png(img_data: bytes, out_path: Path) -> bool:
    """Write raw image bytes (PNG or JPEG) to out_path as PNG. Returns False on failure."""
    if not img_data:
        return False
    if _DEBUG:
        from xi.zone.xi_apply_changes import _dbg
        _dbg(f"texture image: {len(img_data)} bytes, format: {img_data[:8].hex()}")
    if img_data[:8] == b'\x89PNG\r\n\x1a\n':
        out_path.write_bytes(img_data)
        return True
    try:
        from PIL import Image
        import io
        Image.open(io.BytesIO(img_data)).save(str(out_path), "PNG")
        return True
    except ImportError:
        click.echo("[texture] non-PNG embedded texture requires Pillow: pip install Pillow", err=True)
        return False
    except Exception as e:
        click.echo(f"[texture] embedded image decode failed: {e}", err=True)
        return False


def _extract_embedded_glb_textures(glb_path: Path) -> dict:
    """Return {material_name: image_bytes} for each material's base-colour texture
    embedded in the GLB binary.  Falls back to external URIs when present."""
    import base64
    from xi.entity.mesh.xi_import import load_gltf_document

    try:
        doc, buffers = load_gltf_document(glb_path)
    except Exception:
        return {}

    images      = doc.get("images", [])
    textures    = doc.get("textures", [])
    materials   = doc.get("materials", [])
    buffer_views = doc.get("bufferViews", [])

    def _image_bytes(img: dict) -> bytes | None:
        bv_idx = img.get("bufferView")
        if bv_idx is not None and bv_idx < len(buffer_views):
            bv  = buffer_views[bv_idx]
            buf = buffers[bv.get("buffer", 0)]
            off = bv.get("byteOffset", 0)
            return bytes(buf[off: off + bv["byteLength"]])
        uri = img.get("uri", "")
        if uri.startswith("data:"):
            return base64.b64decode(uri.split(",", 1)[1])
        if uri:
            p = glb_path.parent / uri
            if p.exists():
                return p.read_bytes()
        return None

    def _ffxi_texname_key(nm: str) -> str:
        # Mirror the 16-byte mesh-section texname field EXACTLY (xi_mesh encode +
        # xi_export parse). A material name longer than 16 chars (e.g.
        # 'byakko_statuue_base') is truncated when stored in the mesh, so the texture
        # injector — which re-parses the encoded mesh — looks the image up under the
        # TRUNCATED name ('byakko_statuue_b'). Register that form too, or a custom
        # mesh with a long material name renders white (the embedded image is never
        # matched). 'tigermat' (<=16) round-trips unchanged, which is why it worked.
        enc = nm.strip().encode("ascii", "replace")[:0x10].ljust(0x10, b" ")
        return enc.split(b"\x00", 1)[0].decode("ascii", "replace").strip()

    result = {}
    for mat in materials:
        name = mat.get("name", "")
        if not name:
            continue
        pbr      = mat.get("pbrMetallicRoughness", {})
        tex_ref  = pbr.get("baseColorTexture", {})
        tex_idx  = tex_ref.get("index")
        if tex_idx is None or tex_idx >= len(textures):
            continue
        img_idx = textures[tex_idx].get("source")
        if img_idx is None or img_idx >= len(images):
            continue
        data = _image_bytes(images[img_idx])
        if data:
            result[name] = data
            result.setdefault(_ffxi_texname_key(name), data)   # 16-byte stored form
    return result

def import_object(dat_path: Path, glb_path: Path, mesh_name: str,
                  pos: tuple | None = None,
                  rot: tuple | None = None,
                  scale: tuple | None = None,
                  draw_distance: float = 1000.0,
                  raw_section: Path | None = None,
                  opaque: bool = False,
                  shade: float = 1.0,
                  double_sided: bool = False,
                  bake_scale: bool = True,
                  bake_ao: bool = False,
                  ao_floor: float = 0.45,
                  use_hd: bool = False) -> tuple[Path, str]:
    """Add a new zone mesh section from a GLB + optional placement into a zone DAT.
    Returns (output_path, final_mesh_name) — the name may differ from the requested
    name if auto-numbering kicked in (obj01 → obj01.01, obj01.02, …).

    use_hd: edit the FFXI_HD_DIR copy of the DAT (and return its path) instead of the
    standard one. MUST match the caller's HD mode — otherwise the injected mesh
    section lands in a different file from the placements that reference it (the mesh
    goes to one DAT, the placement to another → the placement points at a dead mesh).

    bake_scale: when True (CLI / one-shot imports) a uniform placement scale is baked
    into the mesh geometry and the placement reset to 1.0 — native FFXI sizing. The web
    editor passes False so the placement KEEPS its scale (e.g. 0.01 for cm-authored GLBs):
    baking makes the published placement read 1.0, which breaks the editor's reload
    round-trip (it re-applies 1.0 to the native GLB → 100x too big). See _baked_uniform_scale.
    """
    from xi.zone.xi_zonedef import add_placements
    from xi.zone.xi_zonedef import SECTION_TYPE_ZONE_DEF

    dll = Path(FFXI_DIR) / "FFXiMain.dll"
    if not dll.exists():
        raise FileNotFoundError(f"FFXiMain.dll not found: {dll}")
    table1, table2 = load_key_tables(dll)

    out = hd_editable_dat(dat_path, fresh=False) if use_hd else editable_dat(dat_path, fresh=False)
    data = bytearray(out.read_bytes())
    sections = parse_sections(data)

    # --- collect existing mesh names; auto-number if base name is taken ---
    existing_mesh_names: set[str] = set()
    for sec in sections:
        if sec.type_code != SECTION_TYPE_ZONE_MESH:
            continue
        buf = bytearray(data)
        decrypt_zone_mesh(buf, sec.data_start, table1, table2)
        name, _ = parse_zone_mesh_section(buf, sec)
        if name:
            existing_mesh_names.add(name.strip())

    requested_mesh_name = mesh_name
    source_mesh_name = mesh_name
    mesh_name = _custom_import_name(mesh_name, existing_mesh_names)
    if mesh_name != requested_mesh_name:
        click.echo(f"Custom import name — using '{mesh_name}'", err=True)

    # --- find a template 0x2E section to borrow encryption key from ---
    template_sec = next((s for s in sections if s.type_code == SECTION_TYPE_ZONE_MESH), None)
    if template_sec is None:
        raise ValueError("Target DAT has no 0x2E mesh sections to borrow encryption key from.")
    template_bytes = bytes(data[template_sec.start: template_sec.start + template_sec.size])

    # --- choose mesh bytes: explicit raw sidecar, source DAT copy, or GLB re-encode ---
    _bake = 1.0
    if raw_section is not None:
        encoded = _rename_mesh_section(raw_section.read_bytes(), mesh_name, table1, table2)
        new_prims = _parse_mesh_section_bytes(encoded, table1, table2)
    else:
        new_prims = _read_glb_primitives(glb_path, [])
        _check_tri_budget(new_prims, mesh_name)
        if opaque:   # --no-alpha / "Opaque": force the mesh opaque too, not just the texture
            for _p in new_prims:
                _p.alpha_test = _p.alpha_blend = False
        if double_sided:   # 0x2000 = backface-cull-disable; set AFTER opaque so it always wins
            for _p in new_prims:
                _p.double_sided = True
        # Bake a uniform placement scale into the geometry so the stored mesh sits at
        # native FFXI size with placement scale 1.0 — how retail and the verified
        # imports (gaitou lamp / fountain merge) store meshes. A model left ~100x
        # oversized with a 0.01 placement scale (e.g. a Blender model authored in cm)
        # has an abnormal mesh-local extent that mis-culls / crashes on draw. Uniform
        # positive scale only: normals are unaffected, so just multiply positions.
        # The web editor passes bake_scale=False (keep the placement's scale) so its
        # reload round-trip stays consistent — it stores native mesh + scaled placement
        # exactly like new_dat_build_outdoor.py's _batch_placements path.
        _bake = _baked_uniform_scale(pos, scale) if bake_scale else 1.0
        if _bake != 1.0:
            for _p in new_prims:
                _p.positions = [(x * _bake, y * _bake, z * _bake) for (x, y, z) in _p.positions]
            scale = (1.0, 1.0, 1.0)
        # Bake ambient occlusion into the vertex colours (form / self-shadow) so a flat
        # custom mesh doesn't render as a glowing slab. Prims that already carry GLB
        # vertex colours are left alone. See xi_ao for the why.
        if bake_ao:
            from xi.zone.xi_ao import bake_vertex_ao
            n_ao = bake_vertex_ao(new_prims, floor=ao_floor)
            if n_ao:
                click.echo(f"[object] baked ambient occlusion into {n_ao} vertices (floor {ao_floor})", err=True)
        encoded = _copy_exported_mesh_section(glb_path, source_mesh_name, mesh_name, table1, table2)
    if encoded is None:
        encoded = encode_zone_mesh_section(mesh_name, new_prims, template_bytes,
                                           encrypt_tables=(table1, table2), shade=shade)
        # patch the 4-byte FourCC (section name) with the mesh name (padded/truncated to 4 chars)
        fourcc = mesh_name.encode("ascii", "replace")[:4].ljust(4)
        encoded = fourcc + encoded[4:]
    new_prims = _parse_mesh_section_bytes(encoded, table1, table2)

    # --- add any textures referenced by the new mesh that aren't in the target DAT ---
    # Shared with the replace path: native source resolution, power-of-two, hard-capped
    # to TEXTURE_CLAMP (xi_config). DXT3 + FFXI half-scale alpha.
    # force_replace=True: if a same-named texture already exists (e.g. from the baked
    # original being replaced by a Refresh-from-disk), splice it out and inject fresh so
    # the new pixels are actually used. Re-parse sections after since offsets may shift.
    tex_blobs = _build_texture_blobs(data, sections, new_prims, glb_path, force_opaque=opaque,
                                     force_replace=True)
    sections = parse_sections(data)   # re-parse: _build_texture_blobs may have spliced out old sections

    # --- splice mesh section + textures after the last 0x2E section ---
    last_mesh = max((s for s in sections if s.type_code == SECTION_TYPE_ZONE_MESH),
                    key=lambda s: s.start)
    insert_at = last_mesh.start + last_mesh.size
    data[insert_at:insert_at] = encoded + tex_blobs
    _tris = sum(len(p.positions) // 3 for p in new_prims if getattr(p, "positions", None))
    click.echo(f"[object] injected mesh '{mesh_name}' from {Path(glb_path).name} "
               f"({_tris} tris, scale {'baked into geometry' if _bake != 1.0 else (scale or 'native')})", err=True)

    # --- optional placement ---
    # Adding a rendered object means registering one new index across FOUR 0x1C
    # structures. Miss any and the object renders wrong or not at all — they were
    # reverse-engineered against xim's renderer (thirdparty/xim, ZoneDefParser/Culler/
    # Scene). The culling tables (4) were the one that actually gated visibility; the
    # rest are needed for a consistent, correctly-bounded record. See
    # docs/zone/object/import.md for the full breakdown.
    if pos is not None:
        rot = rot or (0.0, 0.0, 0.0)
        scale = scale or (1.0, 1.0, 1.0)

        from xi.zone.xi_zonedef import (parse_zonedef, expand_placement_bounds_points,
                                          add_collision_transforms, add_to_culling_tables,
                                          OBJ_ARRAY_START, OBJ_RECORD_SIZE, OBJ_DRAW_DISTANCE, OBJ_BLOCK_ID)
        from xi.zone.xi_apply_changes import _bbox_points
        from xi.zone.xi_mesh import _bbox as _mesh_bbox
        from xi.zone.xi_export import trs_matrix

        sections2 = parse_sections(data)        # re-parse: the mesh splice moved offsets
        zd_sec = next((s for s in sections2 if s.type_code == SECTION_TYPE_ZONE_DEF), None)
        if zd_sec is None:
            raise ValueError("Target DAT has no 0x1C ZoneDef section.")
        node_count = decrypt_zone_objects(data, zd_sec.data_start, zd_sec.start, zd_sec.size, table1)

        def _rec_name(index: int) -> str:
            b = zd_sec.data_start + 0x20 + index * 0x64
            return data[b:b + 0x10].split(b"\x00", 1)[0].decode("ascii", "replace").strip()

        def _rec_pos(index: int) -> tuple:
            return struct.unpack_from("<3f", data, zd_sec.data_start + 0x20 + index * 0x64 + 0x10)

        # Template = a FIXED stable structural block, NOT the spatially-nearest object.
        # WHY: add_placements registers the new object into its TEMPLATE's space-tree leaf.
        # If the template is whatever sits nearest, then near a deletion (e.g. dropping a
        # mesh on the fountain spot) the new object lands in that soon-to-be-hidden object's
        # leaf (funsui's leaf 103260) — and an appended object in that leaf crashes the
        # client (verified: identical record/collision, ONLY the leaf differs vs a working
        # build). A floor/wall block (block03a / block0*) lives in a stable leaf that is
        # never deleted, so every appended object lands in the same known-good leaf
        # regardless of position. The record itself is normalised below (draw dist, etc.),
        # so the only thing the template really decides is the safe leaf + sane flags.
        def _find_anchor() -> int:
            for want in ("block03a", "block03", "block02a", "block01a", "block02", "block01"):
                for i in range(node_count):
                    if _rec_name(i) == want:
                        return i
            for i in range(node_count):              # any structural block
                if _rec_name(i).startswith("block0"):
                    return i
            # last resort: spatially-nearest non-husk record (legacy behaviour)
            return min((i for i in range(node_count) if _rec_name(i)),
                       key=lambda i: sum((a - b) ** 2 for a, b in zip(_rec_pos(i), pos)), default=0)
        template_index = _find_anchor()
        new_index = node_count

        # (1) Append the object record + register it in the nearest space-tree leaf, so the
        #     broad-phase frustum walk reaches it. Also bumps nodeCount + collision indexCount.
        sec_local = bytearray(data[zd_sec.start: zd_sec.start + zd_sec.size])
        new_sec = add_placements(sec_local,
                                 [(template_index, pos, rot, scale, mesh_name)],
                                 register_in_tree=True, prefer_src_leaf=True)

        # (2) Widen that leaf (and its ancestors) to the full transformed mesh bbox, not
        #     just the placement origin, so the broad-phase doesn't clip large meshes.
        bmin, bmax = _mesh_bbox(new_prims)
        bbox6 = (bmin[0], bmax[0], bmin[1], bmax[1], bmin[2], bmax[2])
        pts = _bbox_points(bbox6, pos, rot, scale)
        expand_placement_bounds_points(new_sec, parse_zonedef(new_sec, 0x10, 0, len(new_sec)), new_index, pts)

        # (3) Set the per-object distance-cull threshold (record +0x40). add_placements
        #     copies the nearest template's, which can be short (60-100 for props); default
        #     it high so a placed object stays visible at range. Tunable via --draw-distance.
        new_rec = 0x10 + OBJ_ARRAY_START + new_index * OBJ_RECORD_SIZE
        struct.pack_into("<f", new_sec, new_rec + OBJ_DRAW_DISTANCE, float(draw_distance))
        #     A brand-new static object must not inherit the template's BlockID@0x34: when the
        #     anchor falls back to the nearest record and that is a door half (mog houses),
        #     the copy would join the door's animated group, which the client caps at four
        #     drawn parts (see xi_zonedef.clear_block_id).
        struct.pack_into("<I", new_sec, new_rec + OBJ_BLOCK_ID, 0)

        # (4a) Grow the per-object collision-transform array to match the bumped object
        #      count (it's indexed 1:1 by object index). Without this the new index reads
        #      past the array (garbage). Passing bbox6 makes add_collision_transforms
        #      DERIVE the +0x80 cull bounds from THIS mesh+placement instead of cloning a
        #      template — essential for a brand-new custom mesh, whose nearest template
        #      describes a different mesh (cloning it gives the culler an inconsistent
        #      cull volume → camera-dependent crash). The clone source only supplies the
        #      record's other tail bytes.
        xform_src = next((j for j in range(node_count) if _rec_name(j) == source_mesh_name),
                         template_index)
        new_sec = add_collision_transforms(new_sec, [(xform_src, trs_matrix(pos, rot, scale), bbox6)])

        # (4b) THE visibility fix: register the index in the culling tables (PVS). The client
        #      draws an object only when the table chosen by the CAMERA's floor lists it, so
        #      an object in no table vanishes from most camera positions. Add to ALL tables.
        new_sec = add_to_culling_tables(new_sec, new_index)

        data[zd_sec.start: zd_sec.start + zd_sec.size] = new_sec

        reencrypt_zone_objects(data, zd_sec.data_start, zd_sec.start, len(new_sec), table1)

    out.write_bytes(bytes(data))
    return (hd_path_for(dat_path) if use_hd else output_path_for(dat_path)), mesh_name


# ---------------------------------------------------------------------------
# swap-placement: overwrite an EXISTING placement slot in place
# ---------------------------------------------------------------------------

def _mesh_bbox6(data: bytearray, mesh_name: str, table1: bytes, table2: bytes):
    """Return the 6-float (xmin,xmax,ymin,ymax,zmin,zmax) bbox of a 0x2E mesh, or None."""
    for sec in parse_sections(data):
        if sec.type_code != SECTION_TYPE_ZONE_MESH:
            continue
        buf = bytearray(data)
        decrypt_zone_mesh(buf, sec.data_start, table1, table2)
        name = buf[sec.data_start + 0x10:sec.data_start + 0x20].split(b"\x00", 1)[0].decode("ascii", "replace").strip()
        if name == mesh_name and struct.unpack_from("<I", buf, sec.data_start + 0x20)[0]:
            return struct.unpack_from("<6f", buf, sec.data_start + 0x24)
    return None


def swap_placement(dat_path: Path, index: int, mesh_name: str,
                   pos: tuple, rot: tuple | None = None, scale: tuple | None = None) -> Path:
    """Overwrite the placement record at ``index`` in place: point it at ``mesh_name``
    at the given TRS, refresh its per-object collision transform, and re-home it in the
    nearest space-tree leaf. Crucially this REUSES the existing object slot — its
    culling-group membership and object index are untouched — so it sidesteps the
    new-object registration gaps that make appended placements cull erratically.

    The mesh must already exist as a 0x2E section in the DAT. No section is grown
    except the space-tree leaf list (same as a normal move edit).
    """
    from xi.zone.xi_zonedef import (parse_zonedef, assign_placements_to_nearest_leaf,
                                      expand_placement_bounds_points, add_to_culling_tables,
                                      TRANSFORM_SIZE, TRANSFORM_INV_MATRIX, _invert_affine,
                                      OBJ_ARRAY_START, OBJ_RECORD_SIZE, SECTION_TYPE_ZONE_DEF)
    from xi.zone.xi_export import trs_matrix
    from xi.zone.xi_apply_changes import _bbox_points

    dll = Path(FFXI_DIR) / "FFXiMain.dll"
    if not dll.exists():
        raise FileNotFoundError(f"FFXiMain.dll not found: {dll}")
    table1, table2 = load_key_tables(dll)

    out = editable_dat(dat_path, fresh=False)
    data = bytearray(out.read_bytes())

    rot = tuple(rot) if rot else (0.0, 0.0, 0.0)
    scale = tuple(scale) if scale else (1.0, 1.0, 1.0)
    pos = tuple(pos)

    bbox6 = _mesh_bbox6(data, mesh_name, table1, table2)
    if bbox6 is None:
        raise ValueError(f"Mesh '{mesh_name}' not found as a 0x2E section in {dat_path.name}.")

    sections = parse_sections(data)
    zd_sec = next((s for s in sections if s.type_code == SECTION_TYPE_ZONE_DEF), None)
    if zd_sec is None:
        raise ValueError("Target DAT has no 0x1C ZoneDef section.")
    node_count = decrypt_zone_objects(data, zd_sec.data_start, zd_sec.start, zd_sec.size, table1)
    if not (0 <= index < node_count):
        raise ValueError(f"index {index} out of range (0..{node_count - 1}).")

    ds = zd_sec.data_start

    def _rec_name(j: int) -> str:
        b = ds + 0x20 + j * 0x64
        return data[b:b + 0x10].split(b"\x00", 1)[0].decode("ascii", "replace").strip()

    old_name = _rec_name(index)
    # transform-bounds source: an existing placement of the target mesh (exact cull
    # bounds); else keep the slot's own +0x80 segment.
    xform_src = next((j for j in range(node_count) if _rec_name(j) == mesh_name), None)

    # --- work on a section-local decrypted copy ---
    sec = bytearray(data[zd_sec.start: zd_sec.start + zd_sec.size])

    # 1) overwrite the object record (mesh id + TRS), keep the tail (flags / culling link)
    rec = 0x10 + OBJ_ARRAY_START + index * OBJ_RECORD_SIZE
    sec[rec:rec + 0x10] = mesh_name.encode("ascii", "replace")[:0x10].ljust(0x10, b"\x00")
    struct.pack_into("<3f", sec, rec + 0x10, *pos)
    struct.pack_into("<3f", sec, rec + 0x1C, *rot)
    struct.pack_into("<3f", sec, rec + 0x28, *scale)

    # 2) refresh this slot's collision transform (world matrix + inverse + cull bounds)
    zd = parse_zonedef(sec, 0x10, 0, len(sec))
    cb = 0x10 + zd.header_offsets["collision"]
    tr = struct.unpack_from("<I", sec, cb + 0x14)[0]
    tbase = 0x10 + tr
    world = trs_matrix(pos, rot, scale)
    struct.pack_into("<16f", sec, tbase + index * TRANSFORM_SIZE + 0x00, *world)
    struct.pack_into("<16f", sec, tbase + index * TRANSFORM_SIZE + TRANSFORM_INV_MATRIX, *_invert_affine(world))
    if xform_src is not None:
        seg = bytes(sec[tbase + xform_src * TRANSFORM_SIZE + 0x80: tbase + xform_src * TRANSFORM_SIZE + 0xC0])
        sec[tbase + index * TRANSFORM_SIZE + 0x80: tbase + index * TRANSFORM_SIZE + 0xC0] = seg

    # 3) re-home in the nearest space-tree leaf + widen bounds to the mesh bbox
    sec = assign_placements_to_nearest_leaf(sec, [(index, pos)])
    zd2 = parse_zonedef(sec, 0x10, 0, len(sec))
    pts = _bbox_points(bbox6, pos, rot, scale)
    expand_placement_bounds_points(sec, zd2, index, pts)

    # 4) ensure the slot is in ALL culling tables (PVS) so it's visible from every camera
    # floor region — the reused slot was only in its old region's table(s).
    sec = add_to_culling_tables(sec, index)

    reencrypt_zone_objects(sec, 0x10, 0, len(sec), table1)
    data[zd_sec.start: zd_sec.start + zd_sec.size] = sec
    out.write_bytes(bytes(data))
    return output_path_for(dat_path), old_name


# ---------------------------------------------------------------------------
# Click commands
# ---------------------------------------------------------------------------

@click.command("export")
@click.argument("dat_path")
@click.argument("mesh_name")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None,
              help="Output directory (default: exports/object/<rom>/<mesh_name>/).")
@click.option("--fbx", is_flag=True,
              help="Also export a texture-embedded .fbx via Blender.")
def export_object_cmd(dat_path, mesh_name, output, fbx):
    """Export a single named zone mesh to GLB (+ optional FBX).

    Exports only the requested mesh, so the file is small and quick to load in
    a DCC tool.  The same ffxi_root_correction node as a full zone export is
    included, so re-importing with replace-object works without axis adjustment.

    Examples:

    \b
      xi object export ROM/1/41 block03
      xi object export ROM/1/41 hasi --fbx
    """
    try:
        resolved = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    out = Path(output) if output else default_object_output_dir(resolved, mesh_name)
    try:
        paths = export_object(resolved, mesh_name, out, fbx)
    except (ValueError, FileNotFoundError) as e:
        raise click.ClickException(str(e))
    for p in paths:
        click.echo(f"Exported: {p}")


@click.command("replace")
@click.argument("dat_path")
@click.argument("mesh_name")
@click.argument("glb_path", type=click.Path(exists=True, path_type=Path))
@click.option("--scale", nargs=3, type=float, metavar="SX SY SZ", default=None,
              help="Uniform scale baked into the geometry (e.g. 0.01 for a cm-scale "
                   "Blender model going into a scale-1.0 slot). The mesh rides the "
                   "target's existing placement, so size it to the target's world size.")
@click.option("--textures/--no-textures", "import_textures", default=True, show_default=True,
              help="Use the GLB's own texture, injecting it into the DAT (default) — for "
                   "custom models with their own skin. --no-textures keeps the original "
                   "mesh's texture reference, for editing an existing object's geometry "
                   "only. Source: <name>.png beside the GLB, else the embedded image.")
@click.option("--tex-size", type=int, default=None,
              help="Max injected-texture dimension (power-of-two clamp). Default: use the "
                   "source's native size up to TEXTURE_CLAMP (xi_config / XI_TEXTURE_CLAMP, "
                   "currently 2048). Always hard-capped to TEXTURE_CLAMP. Retail tops out at 512.")
@click.option("--no-alpha", "opaque", is_flag=True, default=False,
              help="Force the injected texture fully opaque (ignore the source alpha). Use "
                   "for models that come out over-bright or that crash the transparent "
                   "render path — a stray/partial alpha channel is the usual cause.")
@click.option("--shade", type=float, default=1.0, show_default=True,
              help="Brightness multiplier on the mesh's vertex colours (1.0 = full / "
                   "0x80 neutral). Lower it (e.g. 0.6) to darken a model that renders too "
                   "bright when it has no baked vertex shading.")
@click.option("--ao/--no-ao", "bake_ao", default=False, show_default=True,
              help="Bake ambient occlusion into the vertex colours (optional self-shadow / form). "
                   "OFF by default: with correct (unit) normals the engine lights the mesh itself, "
                   "so a plain import comes in neutral. Only affects meshes without GLB vertex colours.")
@click.option("--ao-floor", type=float, default=0.45, show_default=True,
              help="Darkest AO multiplier for fully-occluded vertices (lower = deeper creases).")
def replace_object_cmd(dat_path, mesh_name, glb_path, scale, import_textures, tex_size, opaque, shade, bake_ao, ao_floor):
    """Replace a zone mesh with geometry from an edited GLB.

    Finds the named 0x2E mesh section, re-encodes it from the GLB (any topology —
    not limited to the original vertex count), and writes the result back in
    place. The new geometry rides the target object's EXISTING placement /
    culling / collision — the proven path for getting a custom model in-game without
    the (fragile) new-placement registration. Use export-object first for the GLB.

    Examples:

    \b
      xi object replace ROM/1/41 block03 block03_edited.glb
      xi object replace ROM/1/41 funsui statue.glb --scale 0.01 0.01 0.01
    """
    try:
        resolved = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    try:
        out = replace_object(resolved, mesh_name, glb_path,
                             scale=tuple(scale) if scale else None,
                             import_textures=import_textures, tex_size=tex_size, opaque=opaque, shade=shade,
                             bake_ao=bake_ao, ao_floor=ao_floor)
    except (ValueError, FileNotFoundError) as e:
        raise click.ClickException(str(e))
    click.echo(f"Replaced '{mesh_name}' in {resolved.name}")
    click.echo(f"Wrote: {out}")


@click.command("import")
@click.argument("dat_path")
@click.argument("source_path", required=False, type=click.Path(path_type=Path))
@click.option("--name", "mesh_name", default=None,
              help="Mesh name to use in the DAT (default: GLB filename stem, e.g. obj01).")
@click.option("--pos", nargs=3, type=float, metavar="X Y Z",
              help="Add a placement at this FFXI-space position.")
@click.option("--rot", nargs=3, type=float, metavar="RX RY RZ", default=None,
              help="Placement rotation in radians (default: 0 0 0).")
@click.option("--scale", nargs=3, type=float, metavar="SX SY SZ", default=None,
              help="Placement scale (default: 1 1 1).")
@click.option("--draw-distance", type=float, default=1000.0, show_default=True,
              help="Range past which the engine stops drawing the object. Large = always "
                   "visible (matches buildings); lower it for props that should cull up close.")
@click.option("--raw", "use_raw", is_flag=True,
              help="Use an exported .zone2e raw mesh section instead of GLB geometry.")
@click.option("--no-alpha", "opaque", is_flag=True, default=False,
              help="Force the injected texture fully opaque (ignore the source alpha). Use "
                   "for models that come out over-bright or that crash the transparent "
                   "render path — a stray/partial alpha channel is the usual cause.")
@click.option("--shade", type=float, default=1.0, show_default=True,
              help="Brightness multiplier on the mesh's vertex colours (1.0 = full / "
                   "0x80 neutral). Lower it (e.g. 0.6) to darken a model that renders too "
                   "bright when it has no baked vertex shading.")
@click.option("--ao/--no-ao", "bake_ao", default=False, show_default=True,
              help="Bake ambient occlusion into the vertex colours (optional self-shadow / form). "
                   "OFF by default: with correct (unit) normals the engine lights the mesh itself, "
                   "so a plain import comes in neutral. Only affects meshes without GLB vertex colours.")
@click.option("--ao-floor", type=float, default=0.45, show_default=True,
              help="Darkest AO multiplier for fully-occluded vertices (lower = deeper creases).")
@click.option("--two-sided", "two_sided", is_flag=True, default=False,
              help="Make the mesh two-sided (sets the 0x2000 backface-cull-disable flag on "
                   "every submesh). Use for planes, foliage cards, and flat props that would "
                   "otherwise be invisible from the back. Independent of alpha/--no-alpha.")
def import_object_cmd(dat_path, source_path, mesh_name, pos, rot, scale, draw_distance, use_raw, opaque, shade, bake_ao, ao_floor, two_sided):
    """Add a new mesh from a GLB or raw section into a zone DAT.

    Adds a brand-new 0x2E mesh section. Optionally adds a placement record
    with --pos so the engine renders it in the world.

    SOURCE_PATH can be a .glb, a .zone2e raw section, or an extensionless export
    path. With --raw, extensionless paths resolve to .zone2e; otherwise .glb.
    Use --name to choose the source mesh base name.

    Examples:

    \b
      # add mesh only (no placement yet)
      xi object import ROM/1/41 exports/object/rom/1/40/obj01/obj01.glb

      # add mesh + place it at a specific position
      xi object import ROM/1/41 exports/object/rom/1/40/obj01/obj01 --raw --pos 100 0 -50

      # override the mesh name
      xi object import ROM/1/41 obj01.glb --name my_obj --pos 0 0 0
    """
    try:
        resolved = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    if source_path is None:
        raise click.ClickException("SOURCE_PATH is required. Pass a .glb, .zone2e, or extensionless export path.")

    if use_raw or source_path.suffix.lower() == ".zone2e":
        raw_section = source_path if source_path.suffix.lower() == ".zone2e" else source_path.with_suffix(".zone2e")
        if not raw_section.exists():
            raise click.ClickException(f"Raw section not found: {raw_section}")
        glb_path = source_path.with_suffix(".glb") if source_path.suffix.lower() != ".glb" else source_path
    else:
        raw_section = None
        glb_path = source_path if source_path.suffix else source_path.with_suffix(".glb")
        if not glb_path.exists():
            raise click.ClickException(f"GLB not found: {glb_path}")

    name = mesh_name or source_path.stem
    try:
        out, final_name = import_object(resolved, glb_path, name,
                                        pos=tuple(pos) if pos else None,
                                        rot=tuple(rot) if rot else None,
                                        scale=tuple(scale) if scale else None,
                                        draw_distance=draw_distance,
                                        raw_section=raw_section, opaque=opaque, shade=shade,
                                        double_sided=two_sided, bake_ao=bake_ao, ao_floor=ao_floor)
    except (ValueError, FileNotFoundError) as e:
        raise click.ClickException(str(e))
    click.echo(f"Imported '{final_name}' into {resolved.name}")
    if pos:
        click.echo(f"Placement added at {pos}")
    click.echo(f"Wrote: {out}")


@click.command("swap-placement")
@click.argument("dat_path")
@click.argument("index", type=int)
@click.argument("mesh_name")
@click.option("--pos", nargs=3, type=float, metavar="X Y Z", required=True,
              help="New FFXI-space position for the slot.")
@click.option("--rot", nargs=3, type=float, metavar="RX RY RZ", default=None,
              help="Rotation in radians (default: 0 0 0).")
@click.option("--scale", nargs=3, type=float, metavar="SX SY SZ", default=None,
              help="Scale (default: 1 1 1).")
def swap_placement_cmd(dat_path, index, mesh_name, pos, rot, scale):
    """Overwrite an EXISTING placement slot with a different mesh + position.

    Unlike import-object (which appends a brand-new object), this reuses object
    slot INDEX — keeping its culling-group membership and object index — so it
    avoids the new-object registration gaps. MESH_NAME must already exist as a
    0x2E section in the DAT. The overwritten object is replaced.

    Example (drop a lamp into slot 600, 1u off the gaitou01 at -19.96,0,-3.74):

    \b
      xi zone object swap-placement ROM/1/41 600 gaitou01 --pos -18.96 0 -3.74 --rot 3.142 1.044 3.142
    """
    try:
        resolved = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    try:
        out, old_name = swap_placement(resolved, index, mesh_name,
                                       tuple(pos), tuple(rot) if rot else None,
                                       tuple(scale) if scale else None)
    except (ValueError, FileNotFoundError) as e:
        raise click.ClickException(str(e))
    click.echo(f"Slot {index} ('{old_name}') -> '{mesh_name}' at {tuple(pos)}")
    click.echo(f"Wrote: {out}")

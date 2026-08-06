#!/usr/bin/env python3
"""Export an FFXI entity DAT's skeleton + skinned mesh (no animation) to glTF 2.0.

Mirrors ``xi.entity.anim.xi_export`` but drops the animation channels and instead
decodes the DAT's embedded texture sections to PNG, wiring them into the glTF
materials via relative ``baseColorTexture`` URIs.
"""

import argparse
import json
import re
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Reuse the shared DAT/skeleton/mesh parsing and glTF buffer plumbing from the
# animation exporter so the geometry path stays identical.
from xi.entity.anim.xi_export import (
    ROOT_CORRECTION_ROTATION,
    SECTION_TYPE_SKELETON,
    SECTION_TYPE_SKELETON_MESH,
    BufferBuilder,
    Joint,
    JointGlobal,
    Primitive,
    Reader,
    Section,
    compute_global_transforms,
    compute_min_max_vec3,
    choose_animation,
    pack_u16_vec4,
    pack_vec2,
    pack_vec3,
    pack_vec4,
    pack_mat4,
    parse_animation,
    parse_mesh,
    parse_sections,
    parse_skeleton,
    pose_joints_at_frame,
    resolve_corner_vertex,
    rigid_inverse_matrix,
)
from xi.xi_config import BLENDER_PATH, XI_TOOLS_DIR, FFXI_DIR, SCHEMA_GENERATION, read_path_for
from xi.utils.xi_core import DEFAULT_ALPHA_SCALE, encode_png_rgba, scale_alpha

_GLB_TO_FBX_SCRIPT = Path(__file__).with_name("xi_glb_to_fbx.py")

SECTION_TYPE_TEXTURE = 0x20

# DAT section type-code -> name (from docs/misc/dat_sections.md / xim SectionType).
# Used to annotate the exported metadata JSON.
SECTION_TYPE_NAMES = {
    0x00: "End", 0x01: "Directory", 0x04: "Table", 0x05: "ParticleGenerator",
    0x06: "Route", 0x07: "EffectRoutine", 0x19: "ParticleKeyFrameData",
    0x1C: "ZoneDef", 0x1F: "ParticleMesh", 0x20: "Texture", 0x21: "SpriteSheetMesh",
    0x25: "WeightedMesh", 0x29: "Skeleton", 0x2A: "SkeletonMesh",
    0x2B: "SkeletonAnimation", 0x2E: "ZoneMesh", 0x2F: "Environment",
    0x30: "UiMenu", 0x31: "UiElementGroup", 0x36: "ZoneInteractions",
    0x3D: "SoundEffectPointer", 0x3E: "PointList", 0x45: "Info", 0x49: "SpellList",
    0x4A: "Path", 0x53: "AbilityList", 0x54: "WeaponTrace", 0x5D: "BumpMap",
    0x5E: "Blur",
}


def _printable_fourcc(name: str) -> str:
    return "".join(c if 32 <= ord(c) < 127 else "." for c in name)


def pack_indices(indices: List[int]) -> bytes:
    """Pack an index buffer as u16 (≤65535 vertices) or u32 otherwise."""
    if max(indices, default=0) <= 0xFFFF:
        return struct.pack(f"<{len(indices)}H", *indices)
    return struct.pack(f"<{len(indices)}I", *indices)


def rom_relative(path) -> Optional[str]:
    """ROM-relative form of a DAT path (e.g. 'ROM/33/17.DAT'), or the bare file
    name if it is outside FFXI_DIR. None passes through as None."""
    if path is None:
        return None
    p = Path(path)
    try:
        return str(p.resolve().relative_to(Path(FFXI_DIR).resolve())).replace("\\", "/")
    except ValueError:
        return p.name


def section_dump(sections) -> List[dict]:
    """One dict per section: offset, fourCC, type code, type name, size."""
    return [
        {
            "offset": f"0x{s.start:06X}",
            "fourcc": _printable_fourcc(s.name),
            "type": f"0x{s.type_code:02X}",
            "name": SECTION_TYPE_NAMES.get(s.type_code, "unknown"),
            "size": s.size,
        }
        for s in sections
    ]


def section_type_summary(sections) -> Dict[str, int]:
    """Count of sections per type name (e.g. {'SkeletonAnimation': 174, ...})."""
    summary: Dict[str, int] = {}
    for s in sections:
        key = SECTION_TYPE_NAMES.get(s.type_code, f"0x{s.type_code:02X}")
        summary[key] = summary.get(key, 0) + 1
    return summary


def default_output_dir(dat_path: Path) -> Path:
    """Default export location, mirroring the ROM path under exports/mesh/.

    e.g. ``<FFXI_DIR>/ROM/128/79.DAT`` -> ``<XI_TOOLS_DIR>/exports/mesh/rom/128/79/``.
    DATs outside FFXI_DIR fall back to ``exports/mesh/<stem>/``.
    """
    base = Path(XI_TOOLS_DIR) / "exports" / "mesh"
    try:
        rel = dat_path.resolve().relative_to(Path(FFXI_DIR).resolve())
    except ValueError:
        return base / dat_path.stem
    # rel is like ROM/128/79.DAT -> rom/128/79
    parts = [rel.parts[0].lower(), *rel.parts[1:-1], rel.stem]
    return base.joinpath(*parts)


def resolve_dat_path(spec: str) -> Path:
    """Resolve a DAT spec to a real path.

    Accepts a plain filesystem path, or a ROM-relative spec like ``ROM/128/79``
    / ``128/79`` resolved against ``FFXI_DIR``. A trailing ``.DAT`` is optional.
    """
    candidates: List[Path] = []
    given = Path(spec)
    candidates.append(given)
    if given.suffix.lower() != ".dat":
        candidates.append(given.with_name(given.name + ".DAT"))

    if not given.is_absolute():
        rel = spec if spec.upper().startswith("ROM") else f"ROM/{spec}"
        base = Path(FFXI_DIR) / rel
        candidates.append(base)
        if base.suffix.lower() != ".dat":
            candidates.append(base.with_name(base.name + ".DAT"))

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"DAT not found for '{spec}'. Tried: " + ", ".join(str(c) for c in candidates))


@dataclass
class TextureImage:
    name: str
    width: int
    height: int
    rgba: bytes


# ---------------------------------------------------------------------------
# Texture decoding
#
# Ported from xim's TextureSection.kt / TextureHelper.kt. The game's embedded
# DXT data uses big-endian index words and reversed-X block traversal, so we
# cannot reuse the standard DDS decoder in xi.utils.xi_core; we replicate xim's
# exact pixel ordering instead so the output matches the reference viewer.
# ---------------------------------------------------------------------------


def _dxt_rgb565(value: int) -> Tuple[int, int, int]:
    r = (value >> 11 & 0x1F) * 255 // 31
    g = (value >> 5 & 0x3F) * 255 // 63
    b = (value & 0x1F) * 255 // 31
    return r, g, b


def decode_dxt1(reader: Reader, width: int, height: int) -> bytes:
    buffer = bytearray(width * height * 4)
    for y1 in range(0, height, 4):
        for x1 in range(0, width, 4):
            c0 = reader.u16()
            c1 = reader.u16()
            r = [0, 0, 0, 0]
            g = [0, 0, 0, 0]
            b = [0, 0, 0, 0]
            a = [255, 255, 255, 255]
            r[0], g[0], b[0] = _dxt_rgb565(c0)
            r[1], g[1], b[1] = _dxt_rgb565(c1)
            if c0 > c1:
                for ch in (r, g, b):
                    ch[2] = (2 * ch[0] + ch[1]) // 3
                    ch[3] = (ch[0] + 2 * ch[1]) // 3
            else:
                for ch in (r, g, b):
                    ch[2] = (ch[0] + ch[1]) // 2
                    ch[3] = 0
                a[3] = 0

            indices = struct.unpack_from(">I", reader.data, reader.pos)[0]
            reader.pos += 4
            count = 15
            for y in range(y1, y1 + 4):
                for x in range(x1 + 3, x1 - 1, -1):
                    idx = (indices >> (2 * count)) & 0x3
                    count -= 1
                    if y >= height or x >= width:
                        continue
                    dest = 4 * y * width + 4 * x
                    buffer[dest + 0] = r[idx]
                    buffer[dest + 1] = g[idx]
                    buffer[dest + 2] = b[idx]
                    buffer[dest + 3] = a[idx]
    return bytes(buffer)


def decode_dxt3(reader: Reader, width: int, height: int) -> bytes:
    buffer = bytearray(width * height * 4)
    for y1 in range(0, height, 4):
        for x1 in range(0, width, 4):
            a0 = reader.u32()
            a1 = reader.u32()
            alpha = (a0 << 32) | a1
            c0 = reader.u16()
            c1 = reader.u16()
            r = [0, 0, 0, 0]
            g = [0, 0, 0, 0]
            b = [0, 0, 0, 0]
            r[0], g[0], b[0] = _dxt_rgb565(c0)
            r[1], g[1], b[1] = _dxt_rgb565(c1)
            for ch in (r, g, b):
                ch[2] = (2 * ch[0] + ch[1]) // 3
                ch[3] = (ch[0] + 2 * ch[1]) // 3

            indices = struct.unpack_from(">I", reader.data, reader.pos)[0]
            reader.pos += 4
            count = 15
            for y in range(y1, y1 + 4):
                for x in range(x1 + 3, x1 - 1, -1):
                    idx = (indices >> (2 * count)) & 0x3
                    pixel_alpha = ((alpha >> (4 * count)) & 0xF) * 255 // 16
                    count -= 1
                    if y >= height or x >= width:
                        continue
                    dest = 4 * y * width + 4 * x
                    buffer[dest + 0] = r[idx]
                    buffer[dest + 1] = g[idx]
                    buffer[dest + 2] = b[idx]
                    buffer[dest + 3] = pixel_alpha
    return bytes(buffer)


def decode_palette(reader: Reader, width: int, height: int, paletted: bool) -> bytes:
    colors: List[int] = [reader.u32() for _ in range(256)] if paletted else []
    buffer = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            color = colors[reader.u8()] if paletted else reader.u32()
            # Source rows are bottom-up; flip into a top-origin buffer.
            i = width * (height - (y + 1)) + x
            buffer[i * 4 + 0] = (color >> 16) & 0xFF
            buffer[i * 4 + 1] = (color >> 8) & 0xFF
            buffer[i * 4 + 2] = color & 0xFF
            buffer[i * 4 + 3] = (color >> 24) & 0xFF
    return bytes(buffer)


def parse_texture(data: bytes, section: Section) -> Optional[TextureImage]:
    reader = Reader(data, section.data_start)
    tex_type = reader.u8()
    # 0x81 is an older 8-bit-paletted variant (seen in prototype/beta zones, e.g.
    # rom/0/28.dat). Same header + data layout as 0x91 — just a different format
    # tag — so it decodes via the identical paletted path. Without it the whole
    # zone parses with 0 textures and renders untextured. xim rejects 0x81 too;
    # Noesis handles it, which is why Noesis renders these zones correctly.
    if tex_type not in (0x81, 0x91, 0xA1, 0xB1):
        return None

    name = reader.string(0x10)
    reader.u32()  # 0x28?
    width = reader.u32()
    height = reader.u32()
    reader.u16()  # 0x01?
    bit_count = reader.u16()
    for _ in range(5):
        reader.u32()  # reserved zeros
    reader.u32()  # 0x10 or 0x20?

    if tex_type == 0xA1:
        dxt_type = reader.string(0x4)
        reader.u32()
        reader.u32()
        if dxt_type == "1TXD":
            rgba = decode_dxt1(reader, width, height)
        elif dxt_type == "3TXD":
            rgba = decode_dxt3(reader, width, height)
        else:
            return None
    elif tex_type == 0xB1:
        reader.u32()  # 1?
        rgba = decode_palette(reader, width, height, paletted=bit_count != 32)
    else:  # 0x91 / 0x81 (no extra dword before the palette)
        rgba = decode_palette(reader, width, height, paletted=bit_count != 32)

    return TextureImage(name=name, width=width, height=height, rgba=rgba)


def parse_textures(data: bytes, sections: List[Section]) -> Dict[str, TextureImage]:
    textures: Dict[str, TextureImage] = {}
    for section in sections:
        if section.type_code != SECTION_TYPE_TEXTURE:
            continue
        image = parse_texture(data, section)
        if image is not None and image.name not in textures:
            textures[image.name] = image
    return textures


# ---------------------------------------------------------------------------
# glTF assembly (skeleton + skin + mesh, no animation)
# ---------------------------------------------------------------------------


def material_texture_name(material_name: str) -> str:
    # The material name IS the texture name. Both halves of a symmetric mesh share
    # one material (the mirror is geometry, not a separate skin), so there is no
    # suffix to strip; vertex-colour / untextured primitives have no backing image.
    return material_name


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"\s+", "_", name.strip())
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", cleaned)
    return cleaned or "texture"


def write_glb(gltf: dict, bin_data: bytes, path: Path) -> None:
    """Pack a glTF JSON dict + binary buffer into a single self-contained .glb."""
    json_bytes = json.dumps(gltf).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
    bin_chunk = bin_data + b"\x00" * ((4 - len(bin_data) % 4) % 4)

    total = 12 + 8 + len(json_bytes) + 8 + len(bin_chunk)
    out = bytearray()
    out += struct.pack("<III", 0x46546C67, 2, total)  # 'glTF', version 2, total length
    out += struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes  # 'JSON'
    out += struct.pack("<II", len(bin_chunk), 0x004E4942) + bin_chunk  # 'BIN\0'
    path.write_bytes(bytes(out))


def build_gltf(
    dat_path: Path,
    output_dir: Path,
    joints: List[Joint],
    globals_by_joint: List[JointGlobal],
    meshes: List[Tuple[List, List[Primitive]]],
    textures: Dict[str, TextureImage],
    alpha_scale: float = DEFAULT_ALPHA_SCALE,
    animations: Optional[list] = None,
    mesh_merge_dp: int = 4,
    weld: bool = True,
    split_tex: bool = False,
) -> List[Path]:
    builder = BufferBuilder()
    output_dir.mkdir(parents=True, exist_ok=True)

    all_primitives = [primitive for _, primitives in meshes for primitive in primitives]

    def corner_uv(base_material: str, uv, mirrored: bool):
        """With --split-tex the texture is doubled in height into a stacked 2-up
        atlas (top half = non-mirror side, bottom half = mirror side) and each
        corner's V is squeezed into its half. The two mirror halves then own
        disjoint UV space instead of overlapping, so each can be repainted
        independently. Untextured/no-split corners pass through unchanged."""
        if split_tex and base_material in textures:
            u, v = uv
            return (u, v * 0.5 + (0.5 if mirrored else 0.0))
        return uv

    # Embed every texture referenced by a material (deduplicated) into the binary
    # buffer as PNG, and write each to disk next to the .glb for external editing.
    # With --split-tex the source image is stacked on top of an identical copy
    # (H -> 2H) to form the left/right atlas the remapped UVs sample from.
    referenced = {p.material_name for p in all_primitives if p.material_name in textures}
    images: List[dict] = []
    gltf_textures: List[dict] = []
    texture_index_by_name: Dict[str, int] = {}
    texture_paths: List[Path] = []
    for name in sorted(referenced):
        image = textures[name]
        rgba = scale_alpha(image.rgba, alpha_scale)
        if split_tex:
            # rgba is a flat top-to-bottom buffer and both halves are identical,
            # so stacking is a plain concatenation; height doubles, width holds.
            png_bytes = encode_png_rgba(image.width, image.height * 2, rgba + rgba)
        else:
            png_bytes = encode_png_rgba(image.width, image.height, rgba)
        png_path = output_dir / f"{sanitize_filename(name)}.png"
        png_path.write_bytes(png_bytes)
        texture_paths.append(png_path)
        buffer_view = builder.add_bytes(png_bytes)
        images.append({"bufferView": buffer_view, "mimeType": "image/png", "name": name})
        gltf_textures.append({"source": len(images) - 1, "sampler": 0})
        texture_index_by_name[name] = len(gltf_textures) - 1

    # Materials, one per distinct material name across all mesh sections.
    materials: List[dict] = []
    material_lookup: Dict[str, int] = {}
    for primitive in all_primitives:
        if primitive.material_name in material_lookup:
            continue
        material_lookup[primitive.material_name] = len(materials)
        pbr = {
            "baseColorFactor": [1.0, 1.0, 1.0, 1.0],
            "metallicFactor": 0.0,
            "roughnessFactor": 1.0,
        }
        if primitive.material_name in texture_index_by_name:
            pbr["baseColorTexture"] = {"index": texture_index_by_name[primitive.material_name]}
        materials.append(
            {
                "name": primitive.material_name,
                "doubleSided": True,
                "alphaMode": "MASK",
                "pbrMetallicRoughness": pbr,
            }
        )

    # Skinned bind-pose mesh. POSITION/NORMAL are assembled into bind-pose world
    # space by resolve_corner_vertex; the skin reproduces them exactly because the
    # inverse-bind matrices are each joint's global^-1 (so joint*ibm = identity at
    # bind, and Σ weight = 1). The bones ride along so the rig is editable/animatable.
    mesh_primitives: List[dict] = []

    def _emit_primitive(positions, normals, texcoords, joints0, weights0, colors, tri_indices, mat_name):
        index_component = 5123 if max(tri_indices, default=0) <= 0xFFFF else 5125
        pos_min, pos_max = compute_min_max_vec3(positions)
        attributes = {
            "POSITION": builder.add_accessor(pack_vec3(positions), 5126, "VEC3", len(positions), target=34962, min_value=pos_min, max_value=pos_max),
            "NORMAL": builder.add_accessor(pack_vec3(normals), 5126, "VEC3", len(normals), target=34962),
            "TEXCOORD_0": builder.add_accessor(pack_vec2(texcoords), 5126, "VEC2", len(texcoords), target=34962),
            "JOINTS_0": builder.add_accessor(pack_u16_vec4(joints0), 5123, "VEC4", len(joints0), target=34962),
            "WEIGHTS_0": builder.add_accessor(pack_vec4(weights0), 5126, "VEC4", len(weights0), target=34962),
        }
        if colors:
            attributes["COLOR_0"] = builder.add_accessor(pack_vec4(colors), 5126, "VEC4", len(colors), target=34962)
        mesh_primitives.append({
            "attributes": attributes,
            "indices": builder.add_accessor(pack_indices(tri_indices), index_component, "SCALAR", len(tri_indices), target=34963),
            "mode": 4,
            "material": material_lookup[mat_name],
        })

    if weld:
        # Merge all sections into one vertex buffer per material, deduplicating by
        # world position only (ignoring normal/UV/joint differences). First occurrence
        # of each position wins for UV/normal — gives a fully welded mesh like Noesis.
        from collections import OrderedDict as _OD
        mat_buffers: Dict[str, dict] = {}
        for vertices, primitives in meshes:
            for primitive in primitives:
                mat = primitive.material_name
                buf = mat_buffers.get(mat)
                if buf is None:
                    buf = mat_buffers[mat] = dict(pos_key={}, positions=[], normals=[], texcoords=[], joints0=[], weights0=[], colors=[], tri_indices=[])
                for corner in primitive.corners:
                    vertex = vertices[corner.vertex_index]
                    position, normal, joint_indices, weight_values = resolve_corner_vertex(vertex, globals_by_joint, corner.mirrored)
                    uv = corner_uv(mat, corner.uv, corner.mirrored)
                    pos_key = (tuple(round(v, mesh_merge_dp) for v in position),
                               tuple(round(v, 2) for v in uv))
                    idx = buf["pos_key"].get(pos_key)
                    if idx is None:
                        idx = len(buf["positions"])
                        buf["pos_key"][pos_key] = idx
                        buf["positions"].append(position)
                        buf["normals"].append(normal)
                        buf["texcoords"].append(uv)
                        buf["joints0"].append(joint_indices)
                        buf["weights0"].append(weight_values)
                        if corner.color is not None:
                            buf["colors"].append(corner.color)
                    buf["tri_indices"].append(idx)
        for mat, buf in mat_buffers.items():
            _emit_primitive(buf["positions"], buf["normals"], buf["texcoords"],
                            buf["joints0"], buf["weights0"], buf["colors"],
                            buf["tri_indices"], mat)
    else:
        for vertices, primitives in meshes:
            for primitive in primitives:
                positions: List[Tuple[float, float, float]] = []
                normals: List[Tuple[float, float, float]] = []
                texcoords: List[Tuple[float, float]] = []
                joints0: List[Tuple[int, int, int, int]] = []
                weights0: List[Tuple[float, float, float, float]] = []
                colors: List[Tuple[float, float, float, float]] = []
                has_color = any(corner.color is not None for corner in primitive.corners)

                vertex_key_to_index: Dict[tuple, int] = {}
                tri_indices: List[int] = []
                for corner in primitive.corners:
                    vertex = vertices[corner.vertex_index]
                    position, normal, joint_indices, weight_values = resolve_corner_vertex(vertex, globals_by_joint, corner.mirrored)
                    uv = corner_uv(primitive.material_name, corner.uv, corner.mirrored)
                    key = (
                        tuple(round(v, mesh_merge_dp) for v in position),
                        tuple(round(v, mesh_merge_dp) for v in normal),
                        tuple(round(v, 2) for v in uv),
                        joint_indices,
                        tuple(round(v, mesh_merge_dp) for v in weight_values),
                        corner.color if has_color else None,
                    )
                    idx = vertex_key_to_index.get(key)
                    if idx is None:
                        idx = len(positions)
                        vertex_key_to_index[key] = idx
                        positions.append(position)
                        normals.append(normal)
                        texcoords.append(uv)
                        joints0.append(joint_indices)
                        weights0.append(weight_values)
                        if has_color:
                            colors.append(corner.color or (1.0, 1.0, 1.0, 1.0))
                    tri_indices.append(idx)

                _emit_primitive(positions, normals, texcoords, joints0, weights0,
                                colors if has_color else [], tri_indices, primitive.material_name)

    inverse_bind_matrices = [rigid_inverse_matrix(g.rotation, g.translation) for g in globals_by_joint]
    inverse_bind_accessor = builder.add_accessor(pack_mat4(inverse_bind_matrices), 5126, "MAT4", len(inverse_bind_matrices))

    nodes: List[dict] = []
    joint_node_indices: List[int] = []
    for joint in joints:
        joint_node_indices.append(len(nodes))
        nodes.append(
            {
                "name": f"bone{joint.index:04d}",
                "translation": [joint.translation[0], joint.translation[1], joint.translation[2]],
                "rotation": [joint.rotation[0], joint.rotation[1], joint.rotation[2], joint.rotation[3]],
            }
        )

    root_nodes: List[int] = []
    for joint in joints:
        if joint.parent_index < 0:
            root_nodes.append(joint_node_indices[joint.index])
        else:
            parent_node = nodes[joint_node_indices[joint.parent_index]]
            parent_node.setdefault("children", []).append(joint_node_indices[joint.index])

    mesh_node_index = len(nodes)
    nodes.append({"name": dat_path.stem, "mesh": 0, "skin": 0})

    scene_root_index = len(nodes)
    nodes.append(
        {
            "name": "ffxi_root_correction",
            "rotation": ROOT_CORRECTION_ROTATION,
            "children": root_nodes + [mesh_node_index],
        }
    )

    # Optional skeletal-animation clips, baked onto the same joint nodes (must run before the
    # buffer is finalised below, since it appends accessors/buffer-views to `builder`).
    gltf_animations = []
    if animations:
        from xi.entity.anim.xi_export import build_animation_arrays
        gltf_animations = build_animation_arrays(builder, joints, joint_node_indices, animations)

    gltf = {
        "asset": {"version": "2.0", "generator": "xi entity mesh export"},
        "scene": 0,
        "scenes": [{"nodes": [scene_root_index]}],
        "nodes": nodes,
        "meshes": [{"name": f"{dat_path.stem}_mesh", "primitives": mesh_primitives}],
        "skins": [
            {
                "name": f"{dat_path.stem}_skin",
                "joints": joint_node_indices,
                "inverseBindMatrices": inverse_bind_accessor,
                "skeleton": root_nodes[0] if root_nodes else joint_node_indices[0],
            }
        ],
        "materials": materials,
        "buffers": [{"byteLength": len(builder.data)}],
        "bufferViews": builder.buffer_views,
        "accessors": builder.accessors,
    }
    if gltf_animations:
        gltf["animations"] = gltf_animations
    if images:
        gltf["images"] = images
        gltf["textures"] = gltf_textures
        gltf["samplers"] = [{"wrapS": 10497, "wrapT": 10497, "magFilter": 9729, "minFilter": 9729}]

    glb_path = output_dir / f"{dat_path.stem}.glb"
    write_glb(gltf, builder.data, glb_path)
    return [glb_path, *texture_paths]


def _inject_fbx_textures(fbx_path: Path, tex_dir: Path) -> None:
    """Post-process a binary FBX 7400 (32-bit) to inject Video+Texture nodes and
    DiffuseColor OP connections for every material that has a matching PNG in tex_dir.

    Blender's FBX exporter never writes texture paths for images packed inside a GLB —
    it silently omits Video/Texture nodes entirely.  We read the material IDs and names
    directly from the FBX binary, build the missing node pairs, insert them into the
    Objects section, add the Texture→Material "DiffuseColor" connections, and update
    every affected absolute endOffset field throughout the file.
    """
    NULL_RECORD = b"\x00" * 13

    def _p_L(v): return b"L" + struct.pack("<q", v)
    def _p_I(v): return b"I" + struct.pack("<i", v)
    def _p_S(s):
        b = s.encode("utf-8") if isinstance(s, str) else bytes(s)
        return b"S" + struct.pack("<I", len(b)) + b
    def _p_D(v): return b"D" + struct.pack("<d", v)
    def _p_R(raw): return b"R" + struct.pack("<I", len(raw)) + bytes(raw)

    def _build(name, props, children, offset):
        """Build a complete FBX 7400 node at `offset`. Returns (bytes, end_offset)."""
        nb = name.encode("ascii") if isinstance(name, str) else name
        pb = b"".join(props)
        cb = b""
        off = offset + 13 + len(nb) + len(pb)
        for cn, cp, cc in children:
            cd, off = _build(cn, cp, cc, off)
            cb += cd
        if children:
            cb += NULL_RECORD
            off += 13
        hdr = struct.pack("<III", off, len(props), len(pb)) + bytes([len(nb)])
        return hdr + nb + pb + cb, off

    def _walk(d, start, end):
        """Yield (hdr_pos, end_off) for every FBX 7400 node in [start, end)."""
        pos = start
        while pos < end - 12:
            eo, np, pl, nl = (struct.unpack_from("<I", d, pos)[0],
                              struct.unpack_from("<I", d, pos + 4)[0],
                              struct.unpack_from("<I", d, pos + 8)[0],
                              d[pos + 12])
            if eo == 0 and np == 0 and pl == 0 and nl == 0:
                break
            if eo == 0 or eo > len(d):
                break
            yield pos, eo
            cs, ce = pos + 13 + nl + pl, eo - 13
            if cs < ce:
                yield from _walk(d, cs, ce)
            pos = eo

    def _read_prop(d, p):
        t = d[p]
        if t == ord("L"): return struct.unpack_from("<q", d, p + 1)[0], p + 9
        if t == ord("I"): return struct.unpack_from("<i", d, p + 1)[0], p + 5
        if t == ord("S"):
            n = struct.unpack_from("<I", d, p + 1)[0]
            return d[p + 5:p + 5 + n].decode("utf-8", errors="replace"), p + 5 + n
        raise ValueError(f"unhandled FBX prop type {t:#x} at {p}")

    data = bytearray(fbx_path.read_bytes())

    # Locate Objects and Connections root sections
    obj_pos = obj_end = obj_ch = None
    con_pos = con_end = None
    pos = 27
    while pos < len(data) - 13:
        eo = struct.unpack_from("<I", data, pos)[0]
        pl = struct.unpack_from("<I", data, pos + 8)[0]
        nl = data[pos + 12]
        if eo == 0:
            break
        nm = bytes(data[pos + 13:pos + 13 + nl])
        if nm == b"Objects":
            obj_pos, obj_end = pos, eo
            obj_ch = pos + 13 + nl + pl
        elif nm == b"Connections":
            con_pos, con_end = pos, eo
        pos = eo

    if obj_pos is None or con_pos is None:
        return

    # Extract (material_id, material_name) pairs from Objects children
    mats = []
    pos = obj_ch
    while pos < obj_end - 13:
        eo = struct.unpack_from("<I", data, pos)[0]
        np = struct.unpack_from("<I", data, pos + 4)[0]
        nl = data[pos + 12]
        if eo == 0 or eo > obj_end:
            break
        if bytes(data[pos + 13:pos + 13 + nl]) == b"Material" and np >= 2:
            pp = pos + 13 + nl
            mid, pp = _read_prop(data, pp)
            mname, _ = _read_prop(data, pp)
            mats.append((mid, mname))
        pos = eo

    # Derive PNG filename from material name (strip class suffix + optional _alpha)
    def _png_for(mname):
        for sep in ("\x00\x01Material", " Material"):
            if sep in mname:
                key = mname[:mname.index(sep)]
                break
        else:
            key = mname
        if key.endswith("_alpha"):
            key = key[:-6]
        png = sanitize_filename(key) + ".png"
        return png if (tex_dir / png).exists() else None

    # One Video+Texture pair per unique PNG; collect per-material OP connections
    png_ids: Dict[str, tuple] = {}   # png → (video_id, texture_id)
    next_id = max((mid for mid, _ in mats), default=0) + 1_000_001
    mat_png: List[tuple] = []        # [(mat_id, png)]
    for mid, mname in mats:
        png = _png_for(mname)
        if png is None:
            continue
        if png not in png_ids:
            png_ids[png] = (next_id, next_id + 1)
            next_id += 2
        mat_png.append((mid, png))

    if not mat_png:
        return

    # Build Video + Texture node bytes to insert before Objects null record
    ins_obj = obj_end - 13
    obj_bytes = b""
    cur = ins_obj
    for png, (vid_id, tex_id) in png_ids.items():
        abs_path = str((tex_dir / png).resolve())
        png_bytes = (tex_dir / png).read_bytes()
        vd, cur = _build("Video",
            [_p_L(vid_id), _p_S("Diffuse Texture\x00\x01Video"), _p_S("Clip")],
            [("Type", [_p_S("Clip")], []),
             ("Properties70", [], [
                 ("P", [_p_S("Path"), _p_S("KString"), _p_S("XRefUrl"), _p_S(""), _p_S(png)], []),
                 ("P", [_p_S("RelPath"), _p_S("KString"), _p_S("XRefUrl"), _p_S(""), _p_S(abs_path)], []),
             ]),
             ("UseMipMap", [_p_I(0)], []),
             ("Filename", [_p_S(png)], []),
             ("RelativeFilename", [_p_S(abs_path)], []),
             ("Content", [_p_R(png_bytes)], [])],
            cur)
        obj_bytes += vd
        td, cur = _build("Texture",
            [_p_L(tex_id), _p_S("Diffuse Texture\x00\x01Texture"), _p_S("")],
            [("Type", [_p_S("TextureVideoClip")], []),
             ("Version", [_p_I(202)], []),
             ("TextureName", [_p_S("Diffuse Texture\x00\x01Texture")], []),
             ("Properties70", [], []),
             ("Media", [_p_S("Diffuse Texture\x00\x01Video")], []),
             ("FileName", [_p_S(png)], []),
             ("RelativeFilename", [_p_S(abs_path)], []),
             ("ModelUVTranslation", [_p_D(0.0), _p_D(0.0)], []),
             ("ModelUVScaling", [_p_D(1.0), _p_D(1.0)], []),
             ("Texture_Alpha_Source", [_p_S("None")], []),
             ("Cropping", [_p_I(0), _p_I(0), _p_I(0), _p_I(0)], [])],
            cur)
        obj_bytes += td
    N_obj = len(obj_bytes)

    # Build C "OO" / "OP" connection bytes to insert before Connections null record
    ins_con = con_end - 13 + N_obj
    con_bytes = b""
    cur = ins_con
    for png, (vid_id, tex_id) in png_ids.items():
        cd, cur = _build("C", [_p_S("OO"), _p_L(vid_id), _p_L(tex_id)], [], cur)
        con_bytes += cd
    for mid, png in mat_png:
        _, tex_id = png_ids[png]
        cd, cur = _build("C", [_p_S("OP"), _p_L(tex_id), _p_L(mid), _p_S("DiffuseColor")], [], cur)
        con_bytes += cd

    # Insert bytes and update every absolute endOffset in the file that falls
    # after the insertion point (they're all absolute offsets from file start).
    def _insert_patch(d, ins_pos, new_bytes):
        N = len(new_bytes)
        for hpos, eo in _walk(d, 27, len(d)):
            if eo > ins_pos:
                struct.pack_into("<I", d, hpos, eo + N)
        d[ins_pos:ins_pos] = new_bytes

    _insert_patch(data, ins_obj, obj_bytes)
    _insert_patch(data, ins_con, con_bytes)
    fbx_path.write_bytes(bytes(data))


def convert_glb_to_fbx(glb_path: Path) -> Path:
    """Convert a .glb to an FBX via headless Blender.

    The Blender script rewires packed GLB images to the loose PNG files that
    build_glb already wrote to the same directory.  With file-backed images
    Blender writes proper Video/Texture FBX nodes and DiffuseColor OP
    connections natively, so no post-processing injection is needed.
    """
    blender = Path(BLENDER_PATH)
    if not blender.is_file():
        raise ValueError(
            f"Blender not found at {blender}. Set BLENDER_PATH to your blender.exe to use --fbx."
        )

    fbx_path = glb_path.with_suffix(".fbx")
    tex_dir = str(glb_path.parent.resolve())
    completed = subprocess.run(
        [str(blender), "-b", "--python", str(_GLB_TO_FBX_SCRIPT),
         "--", str(glb_path), str(fbx_path), tex_dir],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or not fbx_path.is_file():
        detail = (completed.stderr or completed.stdout or "blender produced no output").strip()
        raise ValueError(f"Blender glb->fbx conversion failed:\n{detail}")
    return fbx_path


def list_mesh_parts(dat_path: Path) -> List[dict]:
    """Return info about all 0x2A mesh sections in a DAT — name, index, size in bytes."""
    data = read_path_for(dat_path).read_bytes()
    sections = parse_sections(data)
    return [
        {"index": i, "name": s.name.rstrip('\x00'), "size": s.size}
        for i, s in enumerate(s for s in sections if s.type_code == SECTION_TYPE_SKELETON_MESH)
    ]


def export_dat(dat_path: Path, output_dir: Path, fbx: bool = True,
               skeleton_dat: Optional[Path] = None,
               extra_metadata: Optional[dict] = None,
               lod: int = 0, all_parts: bool = False,
               anim: Optional[str] = None, frame: int = 0,
               alpha_scale: float = DEFAULT_ALPHA_SCALE,
               mesh_merge_dp: int = 4,
               use_base: bool = True,
               weld: bool = True,
               split_tex: bool = False,
               write_schema: bool = False) -> List[Path]:
    from xi.xi_config import output_path_for
    base_path = output_path_for(dat_path).with_suffix(output_path_for(dat_path).suffix + ".base")
    if use_base and base_path.exists():
        _click.echo(f"Using pristine base: {base_path}")
        data = base_path.read_bytes()
    else:
        data = read_path_for(dat_path).read_bytes()
    sections = parse_sections(data)

    all_mesh_sections = [s for s in sections if s.type_code == SECTION_TYPE_SKELETON_MESH]
    if not all_mesh_sections:
        raise ValueError("No skeleton mesh section found in DAT")

    if all_parts:
        # Export every 0x2A section merged — correct for multi-part gear models.
        mesh_sections = all_mesh_sections
    else:
        # Single section by LOD index (default 0 = highest detail / first part).
        if lod >= len(all_mesh_sections):
            raise ValueError(
                f"LOD/part {lod} not found — DAT has {len(all_mesh_sections)} mesh section(s) "
                f"(0–{len(all_mesh_sections) - 1}). Use --all-parts to export everything."
            )
        mesh_sections = [all_mesh_sections[lod]]

    # Gear (and other equipment) mesh DATs carry the skinned mesh but no skeleton
    # of their own — they are weighted against a separate race body skeleton. When
    # a skeleton_dat is supplied, read the skeleton from there; the mesh's joint
    # indices line up with that skeleton because that is how the game rigs gear.
    if skeleton_dat is not None:
        skel_data = read_path_for(Path(skeleton_dat)).read_bytes()
        skel_sections = parse_sections(skel_data)
        skeleton_section = next((s for s in skel_sections if s.type_code == SECTION_TYPE_SKELETON), None)
        if skeleton_section is None:
            raise ValueError(f"No skeleton section found in skeleton DAT: {skeleton_dat}")
    else:
        skel_data = data
        skel_sections = sections
        skeleton_section = next((s for s in sections if s.type_code == SECTION_TYPE_SKELETON), None)
        if skeleton_section is None:
            raise ValueError("No skeleton section found in DAT")

    joints = parse_skeleton(skel_data, skeleton_section)

    # Optionally pose the skeleton to a specific animation frame before baking, so
    # the exported mesh matches an in-game/Noesis pose (e.g. an idle crouch)
    # instead of the neutral bind pose. Animations live alongside the skeleton.
    pose_info: Optional[dict] = None
    if anim is not None:
        anim_section = choose_animation(skel_sections, anim)
        animation = parse_animation(skel_data, anim_section)
        joints = pose_joints_at_frame(joints, animation, frame)
        clamped = max(0, min(int(frame), animation.num_frames - 1)) if animation.num_frames else 0
        pose_info = {"anim": anim_section.name, "frame": clamped, "frame_count": animation.num_frames}

    globals_by_joint = compute_global_transforms(joints)
    meshes = [parse_mesh(data, mesh_section) for mesh_section in mesh_sections]
    textures = parse_textures(data, sections)

    # --split-tex only has geometry to unmirror when the donor's own header
    # flags it symmetric (i.e. some corners are the mirrored half); parse_mesh
    # marks those via corner.mirrored. Without that, the atlas still doubles
    # in height but the bottom half goes unused, and nothing gets unmirrored.
    has_mirrored_corners = any(c.mirrored for _verts, prims in meshes for p in prims for c in p.corners)
    if split_tex and not has_mirrored_corners:
        _click.echo(
            f"Warning: {dat_path.name} has no mirrored geometry (donor is not symmetric) — "
            "--split-tex has nothing to unmirror; the texture atlas will double in height "
            "with the bottom half unused."
        )

    output_paths = build_gltf(dat_path, output_dir, joints, globals_by_joint, meshes, textures, alpha_scale=alpha_scale, mesh_merge_dp=mesh_merge_dp, weld=weld, split_tex=split_tex)
    if fbx:
        glb_path = output_paths[0]
        output_paths.append(convert_glb_to_fbx(glb_path))

    # Sidecar metadata JSON: identity (gear race/slot/model_id/file_id via
    # extra_metadata) + skeleton/mesh/texture summary + raw section opcodes.
    metadata: dict = dict(extra_metadata or {})
    metadata.update({
        "source_dat": rom_relative(dat_path),
        "source_path": str(dat_path),
        "skeleton": {
            "source": "separate_dat" if skeleton_dat is not None else "self",
            "dat": rom_relative(skeleton_dat) if skeleton_dat is not None else rom_relative(dat_path),
            "joint_count": len(joints),
            "section_summary": section_type_summary(skel_sections),
            "sections": section_dump(skel_sections),
        },
        "pose": pose_info or {"anim": None, "frame": "bind"},
        "mesh": {
            "lod": "all" if all_parts else lod,
            "lod_count": len(all_mesh_sections),
            "parts": [{"index": i, "name": s.name.rstrip("\x00"), "size": s.size}
                      for i, s in enumerate(all_mesh_sections)],
            "exported_parts": [s.name.rstrip("\x00") for s in mesh_sections],
            "primitive_count": sum(len(prims) for _verts, prims in meshes),
            "vertex_count": sum(len(verts) for verts, _prims in meshes),
            "corner_count": sum(len(p.corners) for _verts, prims in meshes for p in prims),
        },
        "textures": [
            {"name": t.name, "width": t.width, "height": t.height}
            for t in textures.values()
        ],
        "sections": section_dump(sections),
        "exports": [p.name for p in output_paths],
    })
    json_path = output_dir / f"{dat_path.stem}.json"
    json_path.write_text(json.dumps(metadata, indent=2))
    output_paths.append(json_path)

    # Optional `xi dats` action descriptor: a ready-to-`prepare` mesh import
    # that re-injects the (edited) GLB back into this DAT. `dats prepare` reads
    # resources.mesh relative to this file, so it must sit beside the .glb.
    if write_schema:
        glb_name = output_paths[0].name
        # A single exported section names the target mesh; a merged multi-part
        # export re-splits into m000.. on import, so leave the name unset.
        mesh_name = mesh_sections[0].name.rstrip("\x00") if len(mesh_sections) == 1 else None
        src_rel = rom_relative(dat_path)
        schema_doc = {
            "schema": "xi.mesh.v1",
            "id": f"mesh.{dat_path.stem}",
            "type": "mesh",
            "op": "update",
            # `kind` fixes the file-id math at build time; `xi dats prepare`
            # stamps the recommended model_id, which you then edit by hand.
            "model": {"kind": "entity"},
            # source = donor DAT the geometry is rebuilt onto (its structure/skeleton).
            "source": {"dat": src_rel, "mesh_name": mesh_name},
            # target = where the built DAT lands + what the FTABLE points at.
            # Defaults to the source; edit to relocate, e.g. "ROM10/5/3".
            "target": {"dat": src_rel},
            "resources": {"mesh": glb_name},
            # unmirror records whether this GLB's UVs were split into a stacked
            # 2-up atlas via --split-tex — informational for `dats build`, which
            # otherwise rebuilds straight from the GLB's baked UVs either way.
            "options": {"double_sided": True, "scale": 1.0, "rotate_y": 0.0, "flip_yz": None, "unmirror": split_tex},
        }
        schema_path = output_dir / f"{dat_path.stem}_schema.json"
        schema_path.write_text(json.dumps(schema_doc, indent=2))
        output_paths.append(schema_path)

    return output_paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Export FFXI DAT skeleton + mesh (no animation) to a self-contained .glb with textures.")
    parser.add_argument("dat_path", help="Path to the source .DAT, or a ROM-relative spec like ROM/128/79")
    parser.add_argument("--output", type=Path, default=None, help="Output directory. Defaults to exports/mesh/<rom path>")
    parser.add_argument("--no-fbx", dest="fbx", action="store_false", help="Skip the Blender .fbx conversion (just write .glb + .png)")
    parser.add_argument("--lod", type=int, default=0, help="Mesh LOD section index to export (0 = highest detail, default). Check the .json sidecar for lod_count.")
    parser.add_argument("--anim", default=None, help="Pose the mesh by this animation before export (e.g. idl) instead of the neutral bind pose.")
    parser.add_argument("--frame", type=int, default=0, help="Keyframe index within --anim to pose at (default 0). Ignored without --anim.")
    parser.add_argument("--alpha-scale", type=float, default=DEFAULT_ALPHA_SCALE,
                        help="Multiply texture alpha by this factor before export, clamped to 255 "
                             "(default 2.0 = opaque texels become fully opaque, matching the game; "
                             "pass 1.0 for the raw, faint FFXI alpha)")
    args = parser.parse_args()

    dat_path = resolve_dat_path(args.dat_path)
    output_dir = args.output or default_output_dir(dat_path)
    for output_path in export_dat(dat_path, output_dir, fbx=args.fbx, lod=args.lod, anim=args.anim, frame=args.frame, alpha_scale=args.alpha_scale):
        print(f"Exported: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------

import click as _click  # noqa: E402 — avoid polluting module top


@_click.command('export')
@_click.argument('dat_path')
@_click.option('--output', type=_click.Path(path_type=Path), default=None,
               help='Output directory (default: exports/mesh/<rom path>)')
@_click.option('--fbx', is_flag=True, default=False,
               help='Also convert to a texture-embedded .fbx via Blender (Cinema 4D reads FBX textures; its glTF importer does not)')
@_click.option('--lod', type=int, default=0, show_default=True,
               help='Mesh section index to export (0 = first). Use --list-parts to see all available sections.')
@_click.option('--all-parts', is_flag=True, default=False,
               help='Merge ALL mesh sections into one GLB — correct for multi-part gear models where separate sections are body parts, not LODs.')
@_click.option('--list-parts', is_flag=True, default=False,
               help='Print all mesh sections (index, name, size) and exit without exporting.')
@_click.option('--anim', default=None,
               help='Pose the mesh by this animation (e.g. idl) before export, instead of the neutral bind pose.')
@_click.option('--frame', type=int, default=0, show_default=True,
               help='Keyframe index within --anim to pose at. Ignored unless --anim is given.')
@_click.option('--alpha-scale', type=float, default=DEFAULT_ALPHA_SCALE, show_default=True,
               help='Multiply texture alpha by this factor before export, clamped to 255. FFXI stores '
                    'alpha at half scale (0x80 = opaque), so the default 2.0 makes opaque texels fully '
                    'opaque (matching the game) while preserving real cutouts/gradients. Pass 1.0 for '
                    'the raw, faint FFXI alpha, or a higher value to force more opacity.')
@_click.option('--mesh-merge-dp', type=int, default=4, show_default=True,
               help='Decimal places used when deduplicating vertices by position/normal/weights. '
                    'Lower = more aggressive merging (0.0001 units at 4dp, 1.0 unit at 0dp). FFXI model '
                    'coords span only a few units, so anything below ~3 flattens the mesh. Raise if the '
                    'mesh looks over-merged; lower if adjacent polys are still unjoined.')
@_click.option('--no-base', 'no_base', is_flag=True, default=False,
               help='Ignore any .base pristine backup and export from the live DAT instead.')
@_click.option('--weld/--no-weld', default=True, show_default=True,
               help='Weld vertices by world position + UV across all mesh sections. Produces a fully '
                    'joined mesh like Noesis. Use --no-weld to preserve original per-section splitting.')
@_click.option('--split-tex', is_flag=True, default=False,
               help='Unmirror the skin: double each texture into a stacked 2-up atlas (e.g. 256x256 -> '
                    '256x512, top = non-mirror side, bottom = mirror side) and remap the UVs so each '
                    'mirror half samples its own copy. One texture, no overlapping UVs, so you can '
                    'repaint each side independently.')
def cmd(dat_path: str, output, fbx: bool, lod: int, all_parts: bool, list_parts: bool,
        anim, frame: int, alpha_scale: float, mesh_merge_dp: int, no_base: bool, weld: bool,
        split_tex: bool):
    """Export skeleton + mesh + textures from a DAT.

    DAT_PATH may be a filesystem path or a ROM-relative spec like ROM/128/79
    (resolved against FFXI_DIR; trailing .DAT optional). Writes a self-contained
    .glb plus editable .png textures. Pass --fbx to also emit a texture-embedded
    .fbx for Cinema 4D (whose glTF importer drops textures).

    Many gear DATs have multiple mesh sections (body parts, not LODs). Use
    --list-parts to see them, --all-parts to export all merged, or --lod N
    to export a specific one. For character entity DATs, --lod 0 is highest detail.

    By default the mesh is exported in its neutral bind pose; pass --anim idl
    (optionally --frame N) to bake it into an animation pose instead.
    """
    try:
        dat_path = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise _click.ClickException(str(e))

    if list_parts:
        parts = list_mesh_parts(dat_path)
        if not parts:
            raise _click.ClickException("No skeleton mesh sections found in DAT")
        _click.echo(f"{len(parts)} mesh section(s) in {dat_path.name}:")
        for p in parts:
            _click.echo(f"  [{p['index']}] name={p['name']!r:12s}  size={p['size']} bytes")
        return

    output_dir = output or default_output_dir(dat_path)
    try:
        output_paths = export_dat(dat_path, output_dir, fbx=fbx, lod=lod, all_parts=all_parts,
                                  anim=anim, frame=frame, alpha_scale=alpha_scale,
                                  mesh_merge_dp=mesh_merge_dp, use_base=not no_base, weld=weld,
                                  split_tex=split_tex, write_schema=SCHEMA_GENERATION)
    except ValueError as e:
        raise _click.ClickException(str(e))
    for output_path in output_paths:
        _click.echo(f'Exported: {output_path}')

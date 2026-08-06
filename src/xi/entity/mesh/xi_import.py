#!/usr/bin/env python3
"""Import an edited mesh (FBX or glTF/GLB) back into an FFXI entity DAT.

Pipeline: FBX -> glTF via Blender (if needed) -> parse geometry/skin/UVs ->
re-bake vertices into FFXI joint-local space using the DAT's (unchanged)
skeleton -> serialize a single skeleton-mesh (0x2A) section -> rebuild the DAT,
replacing all existing mesh sections (the new one may be larger).

Notes / limitations (v1):
  * Writes one standard skinned mesh with normals. Cloth-effect sim on the
    original banner is not reproduced (the mesh still animates via the skeleton).
  * Vertices are skinned to at most 2 joints (FFXI's limit); extra influences
    are dropped and the top two renormalised.
  * vertex-colour (untextured) primitives are re-emitted with a neutral colour.
"""

import argparse
import json
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from xi.common.xi_section import encode_section_meta
from xi.xi_config import BLENDER_PATH, TEXTURE_CLAMP, editable_dat, read_path_for
from xi.entity.mesh.xi_export import (
    SECTION_TYPE_SKELETON,
    SECTION_TYPE_SKELETON_MESH,
    SECTION_TYPE_TEXTURE,
    Section,
    default_output_dir,
    parse_sections,
    parse_skeleton,
    parse_textures,
    resolve_dat_path,
)
from xi.entity.anim.xi_export import (
    JointGlobal,
    compute_global_transforms,
    quat_conjugate,
    rotate_vec3,
)

_FBX_TO_GLTF_SCRIPT = Path(__file__).with_name("_fbx_to_gltf.py")

# Blender bakes its Y-up→Z-up import correction into vertex positions on
# re-export, so round-tripped positions need Y and Z negated to get back to
# FFXI space. C4D is already Y-up and applies no correction, so its GLB
# positions are already in FFXI space — no flip needed.
def to_ffxi(vec: Sequence[float], flip_yz: bool = True) -> Tuple[float, float, float]:
    if flip_yz:
        return (vec[0], -vec[1], -vec[2])
    return (vec[0], vec[1], vec[2])


def detect_flip_yz(doc: dict) -> bool:
    """Return True (flip Y/Z) for Blender-exported GLBs, False for C4D and other
    Y-up-native tools. Blender stamps its name in asset.generator."""
    generator = doc.get("asset", {}).get("generator", "")
    return "Blender" in generator


# ---------------------------------------------------------------------------
# glTF reading
# ---------------------------------------------------------------------------


def load_gltf_document(gltf_path: Path) -> Tuple[dict, List[bytes]]:
    raw = gltf_path.read_bytes()
    if raw[:4] == b"glTF":
        # GLB: 12-byte header, then JSON chunk, then BIN chunk.
        json_len = struct.unpack_from("<I", raw, 12)[0]
        doc = json.loads(raw[20 : 20 + json_len])
        bin_start = 20 + json_len
        buffers: List[bytes] = []
        if bin_start + 8 <= len(raw):
            bin_len = struct.unpack_from("<I", raw, bin_start)[0]
            blob = raw[bin_start + 8 : bin_start + 8 + bin_len]
            buffers = [blob for _ in doc.get("buffers", [])] or [blob]
        return doc, buffers

    doc = json.loads(gltf_path.read_text(encoding="utf-8"))
    buffers = []
    import base64

    for buffer_info in doc.get("buffers", []):
        uri = buffer_info.get("uri", "")
        if uri.startswith("data:"):
            buffers.append(base64.b64decode(uri.split(",", 1)[1]))
        else:
            buffers.append((gltf_path.parent / uri).read_bytes())
    return doc, buffers


_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
_COMPONENT_FMT = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}
_COMPONENT_SIZE = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}


def read_accessor(doc: dict, buffers: Sequence[bytes], accessor_index: int) -> List[Tuple]:
    accessor = doc["accessors"][accessor_index]
    buffer_view = doc["bufferViews"][accessor["bufferView"]]
    component_type = accessor["componentType"]
    components = _COMPONENTS[accessor["type"]]
    count = accessor["count"]
    fmt_char = _COMPONENT_FMT[component_type]
    comp_size = _COMPONENT_SIZE[component_type]

    raw = buffers[buffer_view["buffer"]]
    view_offset = buffer_view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    stride = buffer_view.get("byteStride") or (components * comp_size)

    values: List[Tuple] = []
    for index in range(count):
        offset = view_offset + index * stride
        values.append(struct.unpack_from("<" + fmt_char * components, raw, offset))
    return values


def read_indices(doc: dict, buffers: Sequence[bytes], primitive: dict, vertex_count: int) -> List[int]:
    if "indices" not in primitive:
        return list(range(vertex_count))
    return [value[0] for value in read_accessor(doc, buffers, primitive["indices"])]


# ---------------------------------------------------------------------------
# Geometry extraction
# ---------------------------------------------------------------------------


@dataclass
class ImportVertex:
    p0: Tuple[float, float, float]
    p1: Tuple[float, float, float]
    n0: Tuple[float, float, float]
    n1: Tuple[float, float, float]
    w0: float
    w1: float
    j0: int
    j1: int


@dataclass
class ImportPrimitive:
    texture_name: Optional[str]  # None -> untextured (vertex colour)
    triangles: List[Tuple[int, int, int]]  # global vertex indices
    uvs: Dict[int, Tuple[float, float]] = field(default_factory=dict)


def _norm(name: str) -> str:
    return name.replace(" ", "").replace("_", "").lower()


def _texture_name_for_material(material: dict, image: dict) -> str:
    for value in (image.get("name"), image.get("uri"), material.get("name")):
        if not value or str(value).startswith("data:"):
            continue
        text = str(value).replace("\\", "/").strip()
        if "/" in text or "." in text:
            text = Path(text).stem
        if text:
            return text
    return "texture"


def _safe_image_stem(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name).strip("_") or "texture"


def _image_suffix(mime_type: str) -> str:
    mime = mime_type.lower()
    if mime == "image/png":
        return ".png"
    if mime in ("image/jpeg", "image/jpg"):
        return ".jpg"
    if mime == "image/webp":
        return ".webp"
    return ".img"


def _sniff_image_suffix(data: bytes, declared_mime: str) -> str:
    """Pick the file extension from the image bytes' own magic number rather
    than the glTF's declared mimeType — Blender's exporter has been seen to
    embed a real PNG (with alpha) while mislabeling it image/jpeg, which then
    silently loses alpha if it's written out (and fed to texconv) as a .jpg."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return _image_suffix(declared_mime)


def _image_source_path(doc: dict, buffers: Sequence[bytes], gltf_path: Path,
                       image: dict, tmp_dir: Path, name: str) -> Optional[Path]:
    uri = image.get("uri")
    if uri:
        if uri.startswith("data:"):
            import base64

            header, payload = uri.split(",", 1)
            mime = header[5:].split(";", 1)[0]
            decoded = base64.b64decode(payload)
            out = tmp_dir / f"{_safe_image_stem(name)}{_sniff_image_suffix(decoded, mime)}"
            out.write_bytes(decoded)
            return out
        path = gltf_path.parent / uri
        return path if path.is_file() else None

    bv_idx = image.get("bufferView")
    if bv_idx is None:
        return None
    bv = doc["bufferViews"][bv_idx]
    raw = buffers[bv.get("buffer", 0)]
    start = bv.get("byteOffset", 0)
    payload = raw[start: start + bv["byteLength"]]
    out = tmp_dir / f"{_safe_image_stem(name)}{_sniff_image_suffix(payload, image.get('mimeType', ''))}"
    out.write_bytes(payload)
    return out


_SIDE_TEXTURE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga"}


def _sidecar_texture_candidates(gltf_path: Path, dat_texture_names: Sequence[str]) -> List[Path]:
    have = {_norm(name) for name in dat_texture_names}
    candidates: List[Path] = []
    for path in sorted(gltf_path.parent.iterdir()):
        if not path.is_file() or path.suffix.lower() not in _SIDE_TEXTURE_SUFFIXES:
            continue
        if _norm(path.stem) in have:
            continue
        candidates.append(path)
    return candidates


def _sidecar_texture_for_material(material: dict, gltf_path: Optional[Path],
                                  dat_texture_names: Sequence[str]) -> Optional[Path]:
    if gltf_path is None:
        return None
    candidates = _sidecar_texture_candidates(gltf_path, dat_texture_names)
    if not candidates:
        return None

    mat_key = _norm(material.get("name") or "")
    for path in candidates:
        stem_key = _norm(path.stem)
        if mat_key and (mat_key == stem_key or mat_key in stem_key or stem_key in mat_key):
            return path
    if len(candidates) == 1:
        return candidates[0]
    return None


def resolve_texture_name(doc: dict, primitive: dict, dat_texture_names: Sequence[str],
                         buffers: Optional[Sequence[bytes]] = None,
                         gltf_path: Optional[Path] = None) -> Optional[str]:
    """Map a glTF material to one of the DAT's real 0x10 texture names.

    Resolution order:
      1. Exact name match (image.name or material.name vs DAT texture names).
      2. Content hash match — compare the embedded image bytes against the PNG
         files xi wrote alongside the original GLB export. Handles C4D and
         other tools that rename materials/images on re-export.
      3. Fall back to the first DAT texture so the mesh renders textured rather
         than referencing a non-existent name.
    """
    material = doc.get("materials", [])[primitive["material"]] if "material" in primitive else None
    if material is None:
        return None

    candidates: List[str] = []
    texture_name: Optional[str] = None
    pbr = material.get("pbrMetallicRoughness", {})
    bct = pbr.get("baseColorTexture")
    if bct is not None:
        image = doc["images"][doc["textures"][bct["index"]]["source"]]
        texture_name = _texture_name_for_material(material, image)
        candidates.append(texture_name)
        if image.get("name"):
            candidates.append(image["name"])
    else:
        sidecar = _sidecar_texture_for_material(material, gltf_path, dat_texture_names)
        if sidecar is not None:
            texture_name = sidecar.stem
            candidates.append(texture_name)
    if material.get("name"):
        candidates.append(material["name"])

    if not candidates or any("vertex_color" in _norm(c) or _norm(c) == "" for c in candidates):
        if not bct:
            return None

    # 1. Name match.
    for candidate in candidates:
        for dat_name in dat_texture_names:
            if _norm(candidate) == _norm(dat_name):
                return dat_name

    # 2. Content hash — match embedded image bytes against sibling PNGs from
    #    the original xi export (same bytes regardless of what C4D called them).
    if bct is not None and buffers and gltf_path:
        import hashlib
        image = doc["images"][doc["textures"][bct["index"]]["source"]]
        bv_idx = image.get("bufferView")
        if bv_idx is not None:
            bv = doc["bufferViews"][bv_idx]
            buf = buffers[bv.get("buffer", 0)]
            start = bv.get("byteOffset", 0)
            img_bytes = buf[start : start + bv["byteLength"]]
            img_hash = hashlib.md5(img_bytes).hexdigest()
            for png_path in sorted(gltf_path.parent.glob("*.png")):
                if hashlib.md5(png_path.read_bytes()).hexdigest() == img_hash:
                    stem = png_path.stem
                    for dat_name in dat_texture_names:
                        if _norm(stem) == _norm(dat_name):
                            return dat_name
                    return stem  # best-effort even if not in DAT

    # 3. New material texture: name it consistently with collect_new_textures().
    if texture_name and _norm(texture_name) != "vertexcolor":
        return texture_name

    # 4. Fall back to first DAT texture.
    return dat_texture_names[0] if dat_texture_names else (candidates[0] if candidates else None)


def _inv3(m: Sequence[float]) -> Optional[List[float]]:
    """Inverse of the upper-left 3x3 of a column-major glTF MAT4. Returns rows."""
    a, b, c = m[0], m[4], m[8]
    d, e, f = m[1], m[5], m[9]
    g, h, i = m[2], m[6], m[10]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        return None
    inv = 1.0 / det
    return [
        (e * i - f * h) * inv, (c * h - b * i) * inv, (b * f - c * e) * inv,
        (f * g - d * i) * inv, (a * i - c * g) * inv, (c * d - a * f) * inv,
        (d * h - e * g) * inv, (b * g - a * h) * inv, (a * e - b * d) * inv,
    ]


def _identity4() -> List[float]:
    return [
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]


def _mat4_mul(a: Sequence[float], b: Sequence[float]) -> List[float]:
    """Column-major 4x4 product a * b."""
    out = [0.0] * 16
    for col in range(4):
        for row in range(4):
            out[col * 4 + row] = sum(a[k * 4 + row] * b[col * 4 + k] for k in range(4))
    return out


def _quat_to_mat4(q: Sequence[float]) -> List[float]:
    """glTF quaternion (x, y, z, w) -> column-major rotation matrix."""
    import math

    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return [
        1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w), 0.0,
        2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w), 0.0,
        2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y), 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]


def _node_local_matrix(node: dict) -> List[float]:
    """The node's local transform as a column-major 4x4 (matrix or TRS keys)."""
    if "matrix" in node:
        return list(node["matrix"])
    m = _identity4()
    if "rotation" in node:
        m = _quat_to_mat4(node["rotation"])
    if "scale" in node:
        sx, sy, sz = node["scale"]
        for row in range(3):
            m[0 + row] *= sx
            m[4 + row] *= sy
            m[8 + row] *= sz
    if "translation" in node:
        m[12], m[13], m[14] = node["translation"]
    return m


def _compute_node_worlds(doc: dict) -> Dict[int, List[float]]:
    nodes = doc.get("nodes", [])
    worlds: Dict[int, List[float]] = {}

    def visit(node_index: int, parent_world: Sequence[float]):
        if node_index in worlds:
            return
        node = nodes[node_index]
        world = _mat4_mul(parent_world, _node_local_matrix(node))
        worlds[node_index] = world
        for child_index in node.get("children", []):
            visit(child_index, world)

    roots: List[int] = []
    for scene in doc.get("scenes", []):
        roots.extend(scene.get("nodes", []))
    if not roots:
        children = {child for node in nodes for child in node.get("children", [])}
        roots = [index for index in range(len(nodes)) if index not in children]

    identity = _identity4()
    for root in roots:
        visit(root, identity)
    for index in range(len(nodes)):
        visit(index, identity)
    return worlds


def _transform_point(m: Sequence[float], p: Sequence[float]) -> Tuple[float, float, float]:
    x, y, z = p
    return (
        m[0] * x + m[4] * y + m[8] * z + m[12],
        m[1] * x + m[5] * y + m[9] * z + m[13],
        m[2] * x + m[6] * y + m[10] * z + m[14],
    )


def _transform_vector(m: Sequence[float], v: Sequence[float]) -> Tuple[float, float, float]:
    x, y, z = v
    return (
        m[0] * x + m[4] * y + m[8] * z,
        m[1] * x + m[5] * y + m[9] * z,
        m[2] * x + m[6] * y + m[10] * z,
    )


def _normalize3(v: Sequence[float]) -> Tuple[float, float, float]:
    import math

    x, y, z = v
    length = math.sqrt(x * x + y * y + z * z)
    if length <= 1e-12:
        return (0.0, 0.0, 1.0)
    return (x / length, y / length, z / length)


def _transform_normal(m: Sequence[float], n: Sequence[float]) -> Tuple[float, float, float]:
    inv = _inv3(m)
    if inv is None:
        return _normalize3(_transform_vector(m, n))
    return _normalize3((
        inv[0] * n[0] + inv[3] * n[1] + inv[6] * n[2],
        inv[1] * n[0] + inv[4] * n[1] + inv[7] * n[2],
        inv[2] * n[0] + inv[5] * n[1] + inv[8] * n[2],
    ))


def bone_mesh_position(ibm: Sequence[float]) -> Optional[Tuple[float, float, float]]:
    """Origin of a joint in mesh space = -R^-1 * t from its inverse-bind matrix."""
    rinv = _inv3(ibm)
    if rinv is None:
        return None
    tx, ty, tz = ibm[12], ibm[13], ibm[14]
    return (
        -(rinv[0] * tx + rinv[1] * ty + rinv[2] * tz),
        -(rinv[3] * tx + rinv[4] * ty + rinv[5] * tz),
        -(rinv[6] * tx + rinv[7] * ty + rinv[8] * tz),
    )


def compute_full_alignment(
    doc: dict,
    buffers: Sequence[bytes],
    globals_by_joint: Sequence[JointGlobal],
    slot_to_joint: Sequence[int],
    skin_index: int = 0,
    mesh_world: Optional[Sequence[float]] = None,
):
    """Compute the full rigid transform (rotation matrix R, uniform scale s,
    translation t) that maps GLB scene-space bone positions onto the DAT skeleton.

    Uses the Kabsch SVD algorithm — works for any DCC tool axis convention
    (Blender, C4D, Maya, …) without hard-coded axis flips. Returns
    (R, s, t) where:  ffxi_pos = R @ (s * glb_scene_pos) + t

    Falls back to identity when there are fewer than 3 bone correspondences.
    """
    import numpy as np

    identity = (np.eye(3), 1.0, (0.0, 0.0, 0.0))
    skins = doc.get("skins") or [{}]
    skin = skins[skin_index] if skin_index < len(skins) else skins[0]
    if "inverseBindMatrices" not in skin:
        return identity

    ibms = read_accessor(doc, buffers, skin["inverseBindMatrices"])
    src_pts: List[Tuple[float, float, float]] = []
    dst_pts: List[Tuple[float, float, float]] = []
    for slot, ibm in enumerate(ibms):
        joint = slot_to_joint[slot] if slot < len(slot_to_joint) else None
        if joint is None or joint >= len(globals_by_joint):
            continue
        mesh_pos = bone_mesh_position(ibm)
        if mesh_pos is None:
            continue
        if mesh_world is not None:
            mesh_pos = _transform_point(mesh_world, mesh_pos)
        src_pts.append(mesh_pos)  # scene-space GLB — no axis conversion
        dst_pts.append(globals_by_joint[joint].translation)

    if len(src_pts) < 3:
        return identity

    src = np.array(src_pts, dtype=float)
    dst = np.array(dst_pts, dtype=float)
    src_mean = src.mean(axis=0)
    dst_mean = dst.mean(axis=0)
    src_c = src - src_mean
    dst_c = dst - dst_mean

    src_var = float(np.sum(src_c ** 2))
    dst_var = float(np.sum(dst_c ** 2))
    if src_var < 1e-12:
        return identity
    scale = float((dst_var / src_var) ** 0.5)
    if abs(scale - 1.0) < 1e-3:
        scale = 1.0  # preserve clean round-trips

    H = (src_c * scale).T @ dst_c
    U, _, Vt = np.linalg.svd(H)
    # Correct for reflection (det < 0 means the best rotation would flip the mesh)
    d = float(np.linalg.det(Vt.T @ U.T))
    D = np.diag([1.0, 1.0, d])
    R = Vt.T @ D @ U.T

    t_arr = dst_mean - R @ (scale * src_mean)
    return R, scale, (float(t_arr[0]), float(t_arr[1]), float(t_arr[2]))


def collect_new_textures(doc: dict, buffers: Sequence[bytes], gltf_path: Path,
                         dat_texture_names: Sequence[str], tmp_dir: Path) -> Dict[str, Path]:
    """Map texture-name -> source image for materials whose texture is NOT already
    in the DAT (i.e. textures the user added in the DCC tool). Handles both loose
    image uris and GLB-embedded images."""
    have = {_norm(n) for n in dat_texture_names}
    new: Dict[str, Path] = {}
    for material in doc.get("materials", []):
        bct = material.get("pbrMetallicRoughness", {}).get("baseColorTexture")
        if bct is not None:
            image = doc["images"][doc["textures"][bct["index"]]["source"]]
            name = _texture_name_for_material(material, image)
            source = _image_source_path(doc, buffers, gltf_path, image, tmp_dir, name)
        else:
            source = _sidecar_texture_for_material(material, gltf_path, dat_texture_names)
            name = source.stem if source is not None else material.get("name") or "texture"
        if _norm(name) in have or "vertex_color" in _norm(name):
            continue
        if source is not None:
            new[name] = source
    return new


def _index_dat_textures(data: bytes, sections: Sequence[Section]) -> Dict[str, tuple]:
    """Existing 0x20 texture sections by normalised 16-char name ->
    ``(section_index, section_id, full_name)``. Accepts every texture layout the
    exporter parses (0x81 paletted+DXT, 0x91 paletted, 0xA1 DXT, 0xB1 bitmap) —
    a replacement always re-encodes to 0xA1 regardless of the original layout."""
    dat_tex: Dict[str, tuple] = {}
    for i, s in enumerate(sections):
        if s.type_code != SECTION_TYPE_TEXTURE:
            continue
        pos = s.data_start
        if pos >= len(data) or data[pos] not in (0x81, 0x91, 0xA1, 0xB1):
            continue
        full = data[pos + 1: pos + 1 + 0x10].decode("latin1", "replace").rstrip(" \x00")
        dat_tex[_norm(full)] = (i, s.name, full)
    return dat_tex


def collect_texture_replacements(doc: dict, buffers: Sequence[bytes], gltf_path: Path, data: bytes,
                                 sections: Sequence[Section], tmp_dir: Path) -> List[tuple]:
    """For materials whose texture name MATCHES an existing 0x20 texture in the
    DAT, return ``[(section_index, section_id, tex_name, png_path), ...]`` so the
    edited PNG can be re-encoded (PNG -> DDS) and written over that section.

    Inverse of :func:`collect_new_textures` (which handles brand-new textures).
    Handles both a sibling PNG (``uri``, as Blender/C4D exports produce) and an
    embedded image (``bufferView``, as xi's own --split-tex export produces —
    its material/texture name matches the donor's, so without this the doubled
    atlas would never make it into the rebuilt DAT and the mesh's already-updated
    UVs would sample the old, un-split texture).
    """
    dat_tex = _index_dat_textures(data, sections)

    replacements: List[tuple] = []
    seen: set = set()
    for material in doc.get("materials", []):
        bct = material.get("pbrMetallicRoughness", {}).get("baseColorTexture")
        if bct is None:
            continue
        image = doc["images"][doc["textures"][bct["index"]]["source"]]
        key = _norm(image.get("name") or material.get("name") or "")
        if key in ("", "vertexcolor") or key not in dat_tex:
            continue
        png = _image_source_path(doc, buffers, gltf_path, image, tmp_dir, key)
        if png is None or not png.is_file():
            continue
        idx, sid, full = dat_tex[key]
        if idx in seen:
            continue
        seen.add(idx)
        replacements.append((idx, sid, full, png))
    return replacements


def collect_local_texture_replacements(doc: dict, gltf_path: Path, data: bytes,
                                       sections: Sequence[Section]) -> List[tuple]:
    """``--tex-local``: match image FILES next to the model (by name) to the DAT's
    existing 0x20 texture sections, returning the same
    ``[(section_index, section_id, tex_name, image_path), ...]`` shape as
    :func:`collect_texture_replacements`. The images embedded in the GLB are
    ignored — the bytes come straight from disk — the glTF is consulted only as
    a naming reference (material/image -> DAT texture name) for files whose stem
    doesn't match a DAT texture name outright."""
    dat_tex = _index_dat_textures(data, sections)
    files: Dict[str, Path] = {}
    for path in sorted(gltf_path.parent.iterdir()):
        if path.is_file() and path.suffix.lower() in _SIDE_TEXTURE_SUFFIXES:
            files.setdefault(_norm(path.stem), path)

    replacements: List[tuple] = []
    seen: set = set()
    # 1. Direct: file stem matches a DAT texture name (how xi's own export
    #    names the PNGs it writes alongside the GLB).
    for key, (idx, sid, full) in dat_tex.items():
        path = files.get(key)
        if path is not None and idx not in seen:
            seen.add(idx)
            replacements.append((idx, sid, full, path))
    # 2. Via the glTF: a material whose image name maps to a DAT texture, with
    #    the disk file named after the image uri / material instead.
    for material in doc.get("materials", []):
        bct = material.get("pbrMetallicRoughness", {}).get("baseColorTexture")
        if bct is None:
            continue
        image = doc["images"][doc["textures"][bct["index"]]["source"]]
        entry = dat_tex.get(_norm(image.get("name") or material.get("name") or ""))
        if entry is None or entry[0] in seen:
            continue
        for candidate in (image.get("name"), Path(str(image.get("uri") or "")).stem,
                          material.get("name")):
            path = files.get(_norm(str(candidate))) if candidate else None
            if path is not None:
                seen.add(entry[0])
                replacements.append((entry[0], entry[1], entry[2], path))
                break
    return replacements


def invert_skin(world_pos, world_normal, joint_global: JointGlobal):
    inv_rot = quat_conjugate(joint_global.rotation)
    local = (
        world_pos[0] - joint_global.translation[0],
        world_pos[1] - joint_global.translation[1],
        world_pos[2] - joint_global.translation[2],
    )
    return rotate_vec3(inv_rot, local), rotate_vec3(inv_rot, world_normal)


def extract_geometry(
    doc: dict,
    buffers: Sequence[bytes],
    globals_by_joint: Sequence[JointGlobal],
    dat_texture_names: Sequence[str],
    manual_scale: float = 1.0,
    rotate_y_deg: float = 0.0,
    flip_yz: Optional[bool] = None,
    gltf_path: Optional[Path] = None,
) -> Tuple[List[ImportVertex], List[ImportPrimitive]]:
    import math
    import numpy as np
    _ry = math.radians(rotate_y_deg)
    _cos, _sin = math.cos(_ry), math.sin(_ry)

    def yaw(v):  # rotate about the vertical (Y) axis
        if rotate_y_deg == 0.0:
            return v
        return (v[0] * _cos + v[2] * _sin, v[1], -v[0] * _sin + v[2] * _cos)

    nodes = doc.get("nodes", [])
    skins = doc.get("skins") or [{}]
    node_worlds = _compute_node_worlds(doc)

    # Build mesh_index -> skin_index from the node graph so each mesh uses its
    # own skin's joint list (C4D exports added meshes with separate skins).
    mesh_to_skin: Dict[int, int] = {}
    mesh_to_world: Dict[int, List[float]] = {}
    skin_to_world: Dict[int, List[float]] = {}
    for node_index, node in enumerate(nodes):
        if "mesh" in node and "skin" in node:
            mesh_to_skin[node["mesh"]] = node["skin"]
        if "mesh" in node:
            world = node_worlds.get(node_index, _identity4())
            mesh_to_world.setdefault(node["mesh"], world)
            if "skin" in node:
                skin_to_world.setdefault(node["skin"], world)

    def _build_slot_to_joint(skin_index: int) -> List[int]:
        skin = skins[skin_index] if skin_index < len(skins) else {}
        result: List[int] = []
        for node_index in skin.get("joints", []):
            name = nodes[node_index].get("name", "")
            if name.startswith("bone") and name[-4:].isdigit():
                result.append(int(name[-4:]))
            else:
                result.append(0)
        return result

    # Use the skin with the most joints for Kabsch alignment (the main mesh skin).
    primary_skin_index = max(range(len(skins)), key=lambda i: len(skins[i].get("joints", [])))
    primary_skin = skins[primary_skin_index]
    align_slots = [
        int(nodes[node_index]["name"][-4:]) if nodes[node_index].get("name", "").startswith("bone") and nodes[node_index]["name"][-4:].isdigit() else None
        for node_index in primary_skin.get("joints", [])
    ]

    # Derive the full rigid transform (rotation + scale + translation) from the
    # bone positions in the GLB vs the DAT skeleton — works for any DCC tool axis
    # convention without hard-coded flips. flip_yz is kept as a manual override
    # fallback for skinless models only.
    R, scale, offset = compute_full_alignment(
        doc, buffers, globals_by_joint, align_slots,
        skin_index=primary_skin_index,
        mesh_world=skin_to_world.get(primary_skin_index, _identity4()),
    )
    R_np = np.array(R, dtype=float)
    off_np = np.array(offset, dtype=float)

    def apply_transform(p):
        arr = np.array(p, dtype=float)
        result = R_np @ (scale * arr) + off_np
        return (float(result[0]), float(result[1]), float(result[2]))

    def apply_rotation(n):
        arr = np.array(n, dtype=float)
        result = R_np @ arr
        return (float(result[0]), float(result[1]), float(result[2]))

    num_joints = len(globals_by_joint)
    vertices: List[ImportVertex] = []
    primitives: List[ImportPrimitive] = []
    uv_by_vertex: Dict[int, Tuple[float, float]] = {}

    # Cinema 4D's glTF exporter maps V as (V_original - 1), landing in [-1, 0].
    # Wrap V back to [0, 1) with floor subtraction so textures align correctly.
    import math as _math
    generator = doc.get("asset", {}).get("generator", "")
    _c4d_uv = "cinema" in generator.lower()

    for mesh_index, mesh in enumerate(doc.get("meshes", [])):
        skin_index = mesh_to_skin.get(mesh_index, primary_skin_index)
        slot_to_joint = _build_slot_to_joint(skin_index)
        mesh_world = mesh_to_world.get(mesh_index, _identity4())
        for primitive in mesh["primitives"]:
            attrs = primitive["attributes"]
            positions = read_accessor(doc, buffers, attrs["POSITION"])
            normals = read_accessor(doc, buffers, attrs["NORMAL"]) if "NORMAL" in attrs else [(0.0, 0.0, 1.0)] * len(positions)
            texcoords = read_accessor(doc, buffers, attrs["TEXCOORD_0"]) if "TEXCOORD_0" in attrs else [(0.0, 0.0)] * len(positions)
            if _c4d_uv:
                texcoords = [(u, v - _math.floor(v)) for u, v in texcoords]
            joints = read_accessor(doc, buffers, attrs["JOINTS_0"]) if "JOINTS_0" in attrs else [(0, 0, 0, 0)] * len(positions)
            weights = read_accessor(doc, buffers, attrs["WEIGHTS_0"]) if "WEIGHTS_0" in attrs else [(1.0, 0.0, 0.0, 0.0)] * len(positions)

            base = len(vertices)
            for i in range(len(positions)):
                scene_pos = _transform_point(mesh_world, positions[i])
                scene_normal = _transform_normal(mesh_world, normals[i])
                world_pos = yaw(apply_transform(scene_pos))
                world_normal = yaw(apply_rotation(scene_normal))

                # Top-2 influences, renormalised.
                infl = sorted(
                    ((weights[i][k], slot_to_joint[joints[i][k]] if joints[i][k] < len(slot_to_joint) else 0) for k in range(4)),
                    key=lambda wj: wj[0],
                    reverse=True,
                )[:2]
                (w0, j0), (w1, j1) = infl[0], infl[1]
                total = w0 + w1
                if total <= 1e-8:
                    w0, w1, total = 1.0, 0.0, 1.0
                w0, w1 = w0 / total, w1 / total
                j0 = min(j0, num_joints - 1)
                j1 = min(j1, num_joints - 1)

                p0, n0 = invert_skin(world_pos, world_normal, globals_by_joint[j0])
                if w1 > 0.0:
                    p1, n1 = invert_skin(world_pos, world_normal, globals_by_joint[j1])
                else:
                    p1, n1, j1 = p0, n0, j0

                # The DAT stores joint-local positions PRE-MULTIPLIED by their weight:
                # the game assembles a vertex as Σ (rotate(g_i, p_i) + w_i·g_i.translation),
                # NOT a weighted blend (see entity mesh export / model viewer). invert_skin
                # returns the unweighted local position, so scale it by the weight here.
                # (Single-joint vertices have w0 == 1, so this is a no-op for them.)
                # Normals stay unweighted — the game weights those at assembly time.
                p0 = (p0[0] * w0, p0[1] * w0, p0[2] * w0)
                p1 = (p1[0] * w1, p1[1] * w1, p1[2] * w1)

                if manual_scale != 1.0:
                    # Scale geometry about each joint so parts stay attached.
                    p0 = (p0[0] * manual_scale, p0[1] * manual_scale, p0[2] * manual_scale)
                    p1 = (p1[0] * manual_scale, p1[1] * manual_scale, p1[2] * manual_scale)

                vertices.append(ImportVertex(p0=p0, p1=p1, n0=n0, n1=n1, w0=w0, w1=w1, j0=j0, j1=j1))
                uv_by_vertex[base + i] = (texcoords[i][0], texcoords[i][1])

            indices = read_indices(doc, buffers, primitive, len(positions))
            triangles = [(base + indices[t], base + indices[t + 1], base + indices[t + 2]) for t in range(0, len(indices) - 2, 3)]
            texture_name = resolve_texture_name(doc, primitive, dat_texture_names, buffers, gltf_path) or (dat_texture_names[0] if dat_texture_names else "")
            primitives.append(ImportPrimitive(texture_name=texture_name, triangles=triangles, uvs=uv_by_vertex))

    return vertices, primitives


# ---------------------------------------------------------------------------
# Mesh section serialisation (inverse of export.parse_mesh)
# ---------------------------------------------------------------------------


def _align4(value: int) -> int:
    return (value + 3) & ~3


def encode_section_name(name: str) -> bytes:
    return name.encode("ascii", errors="replace")[:4].ljust(4, b"\x00")


def encode_texture_name(name: str) -> bytes:
    """16-char texture name, space-padded to match retail (used in both the
    mesh's 0x8000 reference and the texture section header so they compare equal).

    MUST stay space-padded: the zone mesh encoder (xi_mesh.encode_zone_mesh_section)
    space-pads its 0x8000 texture references independently, so a NUL-padded texture
    SECTION header here would no longer compare equal to the zone mesh's reference ->
    the client can't bind the texture (untextured / mesh fails to draw). Regressed to
    \\x00 in 57e7ee4, reverted here."""
    return name.encode("ascii", errors="replace")[:0x10].ljust(0x10, b" ")


def build_texture_section(section_id: str, tex_name: str, width: int, height: int, dxt_data: bytes, dxt1: bool) -> bytes:
    """Build a 0x20 DXT texture section (type 0xA1) matching retail layout."""
    body = bytearray()
    body += bytes([0xA1])
    body += encode_texture_name(tex_name)
    body += struct.pack("<I", 0x28)
    body += struct.pack("<I", width)
    body += struct.pack("<I", height)
    body += struct.pack("<H", 1)
    body += struct.pack("<H", 4 if dxt1 else 8)   # bitCount (ignored by DXT path)
    body += b"\x00" * 20                            # 5x u32 reserved
    body += struct.pack("<I", 0x20)
    body += b"1TXD" if dxt1 else b"3TXD"
    body += struct.pack("<I", len(dxt_data))        # @0x3d: DXT byte size
    body += struct.pack("<I", ((width + 3) // 4) * (8 if dxt1 else 16))  # @0x41: bytes per block-row
    body += dxt_data

    total = 0x10 + len(body)
    padded = (total + 15) & ~15
    meta = encode_section_meta(padded, 0x20, what="0x20 mesh section")
    out = bytearray()
    out += encode_section_name(section_id)
    out += struct.pack("<I", meta)
    out += b"\x00" * 8
    out += body
    out += b"\x00" * (padded - len(out))
    return bytes(out)


def _prep_png_for_ffxi(png_path: Path, tmp_dir: Path,
                       max_size: Optional[int] = None, alpha_scale: float = 1.0,
                       force_opaque: bool = False) -> Path:
    """Pre-process an image for FFXI texture encoding. Returns the original PNG when no
    change is needed, else a processed copy in tmp_dir. Two transforms:

      * max_size — resize so each dimension is a power of two and ≤ max_size. FFXI's
        loader requires power-of-two; the zone renderer also crashes above retail
        sizes (≤256/512), so callers cap there.
      * alpha_scale — multiply the alpha channel. FFXI stores alpha at HALF scale
        (0x80 = fully opaque; the engine doubles it at draw via 4*vColor.a*tex.a).
        Export bakes ×DEFAULT_ALPHA_SCALE (2.0) so texels look opaque in DCC tools;
        import passes 1/DEFAULT_ALPHA_SCALE (0.5) so an opaque source (alpha 0xFF)
        stores 0x80 like retail — otherwise the engine renders it ~2× too bright."""
    if png_path.suffix.lower() == ".png" and (max_size is None) and alpha_scale == 1.0 and not force_opaque:
        return png_path
    from PIL import Image

    img = Image.open(png_path).convert("RGBA")
    w, h = img.size
    if max_size is not None:
        def pot(n: int) -> int:
            lo = 1
            while lo * 2 <= n and lo * 2 <= max_size:
                lo *= 2
            hi = lo * 2 if lo * 2 <= max_size else lo
            return hi if abs(hi - n) < abs(n - lo) else lo
        nw, nh = pot(w), pot(h)
        if (nw, nh) != (w, h):
            img = img.resize((nw, nh), Image.LANCZOS)
    if force_opaque:
        # Overwrite alpha with FFXI "fully opaque" (0x80 — the engine doubles it via
        # 4*vColor.a*tex.a). Kills a stray/partial source alpha that over-brightens the
        # model and forces it into the transparent render path (which can crash the
        # depth sort at certain camera angles). Use for opaque models authored with alpha.
        img.putalpha(0x80)
    elif alpha_scale != 1.0:
        a = img.getchannel("A").point(lambda v: min(255, round(v * alpha_scale)))
        img.putalpha(a)
    out = tmp_dir / (png_path.stem + "_ffxi.png")
    img.save(out, "PNG")
    return out


def encode_png_to_texture_section(section_id: str, tex_name: str, png_path: Path, tmp_dir: Path,
                                  force_format: Optional[str] = None,
                                  max_size: Optional[int] = None,
                                  alpha_scale: float = 1.0,
                                  force_opaque: bool = False) -> Optional[bytes]:
    """PNG -> DXT (via texconv) -> 0x20 texture section. Returns None on failure.

    The game only reads DXT1 (1TXD) and DXT3 (3TXD) — NOT DXT5 — so we force
    DXT3 when the PNG has an alpha channel (the engine alpha-tests entity meshes,
    so transparent texels are cut out) and DXT1 when it is fully opaque.

    force_format ("DXT1"/"DXT3") overrides the alpha-based choice — zone textures
    must be DXT3 (retail zone textures never use DXT1). max_size clamps to a
    power-of-two ≤ max_size (zones crash on oversized textures). alpha_scale scales
    the alpha channel; pass 1/DEFAULT_ALPHA_SCALE (0.5) on import so opaque texels
    store FFXI's half-scale 0x80 instead of 0xFF (else the engine doubles them and
    the surface renders ~2× too bright). See _prep_png_for_ffxi.
    """
    from xi.utils.xi_core import convert_png_to_dds, inspect_png_alpha, parse_dds_info

    png_path = _prep_png_for_ffxi(png_path, tmp_dir, max_size, alpha_scale, force_opaque)

    has_alpha = inspect_png_alpha(png_path).has_alpha
    fmt = force_format or ("DXT3" if has_alpha else "DXT1")
    dds_path = tmp_dir / (png_path.stem + ".dds")
    try:
        convert_png_to_dds(png_path, dds_path, requested_format=fmt)
    except (FileNotFoundError, RuntimeError, ValueError):
        return None
    info = parse_dds_info(dds_path)
    dxt1 = info.fourcc == "DXT1"
    raw = dds_path.read_bytes()[info.data_offset:]
    expected = ((info.width + 3) // 4) * ((info.height + 3) // 4) * (8 if dxt1 else 16)
    raw = raw[:expected]  # drop any mip tail
    return build_texture_section(section_id, tex_name, info.width, info.height, raw, dxt1)


def split_into_sections(vertices: List[ImportVertex], primitives: List[ImportPrimitive], max_bytes: int = 120000, max_verts: int = 30000):
    """Pack geometry into multiple section-sized batches. A single 0x2A section
    is bounded by maybeVertexDataSize (u16 words -> 131070 vertex-data bytes) and
    u16 vertex indices, so large meshes must span several sections. Returns a list
    of (vertices_subset, primitives_subset) with section-local indices/uvs."""
    def vbytes(v: ImportVertex) -> int:
        return 56 if v.w1 > 0.0 else 24

    batches = []
    state = {"map": {}, "verts": [], "bytes": 0, "tris": {}, "uvs": {}}

    def flush():
        if not state["verts"]:
            return
        prims = [ImportPrimitive(texture_name=tex, triangles=tl, uvs=state["uvs"]) for tex, tl in state["tris"].items()]
        batches.append((state["verts"], prims))
        state.update(map={}, verts=[], bytes=0, tris={}, uvs={})

    for p in primitives:
        for t in p.triangles:
            new_g = [g for g in t if g not in state["map"]]
            add = sum(vbytes(vertices[g]) for g in new_g)
            if state["verts"] and (state["bytes"] + add > max_bytes or len(state["verts"]) + len(new_g) > max_verts):
                flush()
                add = sum(vbytes(vertices[g]) for g in t)
            for g in t:
                if g not in state["map"]:
                    state["map"][g] = len(state["verts"])
                    state["verts"].append(vertices[g])
            state["bytes"] += add
            local = tuple(state["map"][g] for g in t)
            state["tris"].setdefault(p.texture_name, []).append(local)
            for g in t:
                state["uvs"][state["map"][g]] = p.uvs[g]
    flush()
    return batches


def build_mesh_section(name: str, vertices: List[ImportVertex], primitives: List[ImportPrimitive], num_joints: int, double_sided: bool = True) -> bytes:
    # Retail meshes always split vertices into single-jointed (1 influence) then
    # double-jointed (2 influences), with different data layouts. No retail mesh
    # is all-double, so we partition and reorder: singles first, then doubles.
    single_idx = [i for i, v in enumerate(vertices) if v.w1 <= 0.0]
    double_idx = [i for i, v in enumerate(vertices) if v.w1 > 0.0]
    order = single_idx + double_idx
    remap = {old: new for new, old in enumerate(order)}
    ordered = [vertices[old] for old in order]
    single_count = len(single_idx)
    double_count = len(double_idx)
    num_vertices = len(ordered)

    # --- vertex joint-ref block: single -> (j0, 0); double -> (j0, j1) ---
    joint_refs = bytearray()
    for k, v in enumerate(ordered):
        if k < single_count:
            joint_refs += struct.pack("<HH", v.j0 & 0x7F, 0)
        else:
            joint_refs += struct.pack("<HH", v.j0 & 0x7F, v.j1 & 0x7F)

    # --- vertex data block: single -> p0(3) n0(3); double -> 14 interleaved ---
    vertex_data = bytearray()
    for k, v in enumerate(ordered):
        if k < single_count:
            vertex_data += struct.pack("<6f", v.p0[0], v.p0[1], v.p0[2], v.n0[0], v.n0[1], v.n0[2])
        else:
            vertex_data += struct.pack(
                "<14f",
                v.p0[0], v.p1[0], v.p0[1], v.p1[1], v.p0[2], v.p1[2],
                v.w0, v.w1,
                v.n0[0], v.n1[0], v.n0[1], v.n1[1], v.n0[2], v.n1[2],
            )

    # --- vertex counts block (single, double) ---
    vertex_counts = struct.pack("<HH", single_count, double_count)

    # --- instruction block --- (track opcode / draw counts for the header)
    instructions = bytearray()
    current_texture: Optional[str] = None
    mesh_count = 0          # number of draw primitives (mesh buffers)
    instruction_count = 0   # number of opcodes before the 0xFFFF terminator
    # The engine uses a fixed per-draw buffer: a single triangle-list draw is
    # capped at 128 triangles (384 corners) across all retail entity meshes.
    # Exceeding it overflows that buffer and crashes the game, so chunk here.
    MAX_TRIS_PER_DRAW = 128

    for primitive in primitives:
        # Emit each triangle, plus its reverse winding when double-sided so thin
        # surfaces (flags/cloth) aren't back-face culled by the engine.
        # Export's reverse_winding converted FFXI CW → GLB CCW; flip back to CW.
        tris_cw = [(tri[0], tri[2], tri[1]) for tri in primitive.triangles]
        if double_sided:
            tris = [t for pair in zip(tris_cw, primitive.triangles) for t in pair]
        else:
            tris = tris_cw

        if primitive.texture_name and primitive.texture_name != current_texture:
            instructions += struct.pack("<H", 0x8000)
            instructions += encode_texture_name(primitive.texture_name)
            current_texture = primitive.texture_name
            instruction_count += 1

        # Triangles carry ORIGINAL vertex indices (for UV lookup); we write the
        # remapped (single-then-double) index but read UVs by the original.
        for start in range(0, len(tris), MAX_TRIS_PER_DRAW):
            chunk = tris[start : start + MAX_TRIS_PER_DRAW]
            if primitive.texture_name:
                instructions += struct.pack("<HH", 0x0054, len(chunk))
                for tri in chunk:
                    instructions += struct.pack("<HHH", remap[tri[0]], remap[tri[1]], remap[tri[2]])
                    for vertex_index in tri:
                        u, vv = primitive.uvs[vertex_index]
                        instructions += struct.pack("<ff", u, vv)
            else:
                instructions += struct.pack("<HH", 0x0043, len(chunk))
                for tri in chunk:
                    instructions += struct.pack("<HHH", remap[tri[0]], remap[tri[1]], remap[tri[2]])
                    instructions += struct.pack("<BBBB", 0x80, 0x80, 0x80, 0x80)  # neutral BGRA
            mesh_count += 1
            instruction_count += 1
    instructions += struct.pack("<H", 0xFFFF)

    # --- identity joint array [0..n-1]; vertices index it directly. Retail
    #     meshes always carry a joint array (useJointArray) and numJoints>=1,
    #     so we mirror that rather than leaving it empty.
    joint_array = struct.pack("<%dH" % num_joints, *range(num_joints)) if num_joints else b""

    # --- lay out blocks after the header, mirroring the original order:
    #     instructions, joint-array, vertex-counts, joint-refs, vertex-data LAST
    #     so endOffset marks the end of the vertex data.
    # Retail non-cloth headers are 64 bytes (fields past 0x2A that xim never
    # reads but the engine does); instructions start at 0x40.
    header_size = 64
    cursor = _align4(header_size)
    instruction_off = cursor
    cursor = _align4(cursor + len(instructions))
    joint_array_off = cursor
    cursor = _align4(cursor + len(joint_array))
    vertex_counts_off = cursor
    cursor = _align4(cursor + len(vertex_counts))
    joint_refs_off = cursor
    cursor = _align4(cursor + len(joint_refs))
    vertex_data_off = cursor
    end_off = vertex_data_off + len(vertex_data)   # end of vertex data (matches originals)
    payload_size = _align4(end_off)

    body = bytearray(header_size)
    # flags1..6: flags1=1 (set in every retail non-cloth mesh), no cloth,
    # useJointArray (0x80), not symmetric, has normals.
    body[0:6] = bytes([1, 0, 0x80, 0, 0, 0])
    struct.pack_into("<I", body, 6, instruction_off // 2)
    body[10] = min(mesh_count, 0xFF)                         # maybeMeshCount
    body[11] = min(instruction_count, 0xFF)                  # maybeInstructionCount
    struct.pack_into("<I", body, 12, joint_array_off // 2)
    struct.pack_into("<H", body, 16, num_joints)             # numJoints
    struct.pack_into("<I", body, 18, vertex_counts_off // 2)
    struct.pack_into("<H", body, 22, 2)                      # numVertexCounts
    struct.pack_into("<I", body, 24, joint_refs_off // 2)
    struct.pack_into("<H", body, 28, 2 * num_vertices)       # vertexJointMappingCount (u16 entries)
    struct.pack_into("<I", body, 30, vertex_data_off // 2)
    struct.pack_into("<H", body, 34, len(vertex_data) // 2)  # maybeVertexDataSize (in 2-byte words)
    struct.pack_into("<I", body, 36, end_off // 2)
    struct.pack_into("<H", body, 40, 0)                      # endOffsetDataSize
    # Trailing header field at 0x2E (retail non-cloth: == endOffset/2); rest 0.
    struct.pack_into("<I", body, 46, end_off // 2)

    payload = bytearray(payload_size)
    payload[0:header_size] = body
    payload[instruction_off : instruction_off + len(instructions)] = instructions
    payload[joint_array_off : joint_array_off + len(joint_array)] = joint_array
    payload[vertex_counts_off : vertex_counts_off + len(vertex_counts)] = vertex_counts
    payload[joint_refs_off : joint_refs_off + len(joint_refs)] = joint_refs
    payload[vertex_data_off : vertex_data_off + len(vertex_data)] = vertex_data

    # --- wrap in a 16-aligned section with header ---
    total_size = 16 + len(payload)
    padded_size = (total_size + 15) & ~15
    section_meta = encode_section_meta(padded_size, SECTION_TYPE_SKELETON_MESH,
                                       what="skeleton-mesh section")

    section = bytearray()
    section += encode_section_name(name)
    section += struct.pack("<I", section_meta)
    section += b"\x00" * 8
    section += payload
    section += b"\x00" * (padded_size - len(section))
    return bytes(section)


def rebuild_dat(data: bytes, sections: Sequence[Section], new_mesh_sections: Sequence[bytes],
                new_texture_sections: Sequence[bytes] = (),
                replace_sections: Optional[Dict[int, bytes]] = None) -> bytes:
    """Replace the original 0x2A section(s) with the new mesh section(s); insert
    any new texture sections right after the last existing 0x20 (so they land in
    the same directory as the model's textures); and swap any sections listed in
    ``replace_sections`` (index -> new bytes) in place (used to overwrite edited
    textures with their re-encoded versions)."""
    replace_sections = replace_sections or {}
    mesh_indices = [i for i, s in enumerate(sections) if s.type_code == SECTION_TYPE_SKELETON_MESH]
    first_mesh = mesh_indices[0]
    drop = set(mesh_indices[1:])
    tex_indices = [i for i, s in enumerate(sections) if s.type_code == SECTION_TYPE_TEXTURE]
    insert_after = tex_indices[-1] if tex_indices else first_mesh - 1

    output = bytearray()
    for index, section in enumerate(sections):
        if index == first_mesh:
            for blob in new_mesh_sections:
                output += blob
        elif index in drop:
            continue
        elif index in replace_sections:
            output += replace_sections[index]
        else:
            output += data[section.start : section.start + section.size]
        if index == insert_after:
            for tex in new_texture_sections:
                output += tex
    return bytes(output)


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


def summarize_dat(path: Path) -> dict:
    """Quick stats for before/after comparison."""
    from xi.entity.mesh.xi_export import parse_mesh, parse_sections
    data = path.read_bytes()
    sections = parse_sections(data)
    mesh_secs = [s for s in sections if s.type_code == SECTION_TYPE_SKELETON_MESH]
    verts = draws = tris = 0
    max_draw = 0
    for ms in mesh_secs:
        v, prims = parse_mesh(data, ms)
        verts += len(v)
        for pr in prims:
            draws += 1
            tris += len(pr.corners) // 3
            max_draw = max(max_draw, len(pr.corners) // 3)
    return {
        "file_bytes": len(data),
        "mesh_sections": len(mesh_secs),
        "mesh_bytes": sum(s.size for s in mesh_secs),
        "vertices": verts,
        "draws": draws,
        "triangles": tris,
        "max_tris_per_draw": max_draw,
    }


def format_comparison(before: dict, after: dict) -> str:
    keys = [
        ("file_bytes", "file size"),
        ("mesh_sections", "mesh sections"),
        ("mesh_bytes", "mesh bytes"),
        ("vertices", "vertices"),
        ("draws", "draw calls"),
        ("triangles", "triangles"),
        ("max_tris_per_draw", "max tris/draw"),
    ]
    lines = [f"  {'':16} {'old':>10}  {'new':>10}"]
    for key, label in keys:
        lines.append(f"  {label:16} {before[key]:>10}  {after[key]:>10}")
    return "\n".join(lines)


def default_model_path(dat_path: Path) -> Optional[Path]:
    """The model exported for this DAT, if present: exports/mesh/<rom>/<stem>.{fbx,glb,gltf}."""
    for out_dir in (default_output_dir(dat_path), _legacy_default_output_dir(dat_path)):
        for ext in (".fbx", ".glb", ".gltf"):
            candidate = out_dir / f"{dat_path.stem}{ext}"
            if candidate.is_file():
                return candidate
    return None


def _legacy_default_output_dir(dat_path: Path) -> Path:
    """Pre-rename mesh export location, kept so older exported edits still import."""
    from xi.xi_config import XI_TOOLS_DIR, FFXI_DIR

    base = Path(XI_TOOLS_DIR) / "exports" / "entity"
    try:
        rel = dat_path.resolve().relative_to(Path(FFXI_DIR).resolve())
    except ValueError:
        return base / dat_path.stem
    parts = [rel.parts[0].lower(), *rel.parts[1:-1], rel.stem]
    return base.joinpath(*parts)


def convert_fbx_to_gltf(fbx_path: Path, out_dir: Path) -> Path:
    blender = Path(BLENDER_PATH)
    if not blender.is_file():
        raise ValueError(f"Blender not found at {blender}. Set BLENDER_PATH to your blender.exe.")
    gltf_path = out_dir / f"{fbx_path.stem}.gltf"
    completed = subprocess.run(
        [str(blender), "-b", "--python", str(_FBX_TO_GLTF_SCRIPT), "--", str(fbx_path), str(gltf_path)],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or not gltf_path.is_file():
        detail = (completed.stderr or completed.stdout or "blender produced no output").strip()
        raise ValueError(f"Blender fbx->gltf conversion failed:\n{detail}")
    return gltf_path


def build_imported_dat(data: bytes, model_path: Path, *,
                       skeleton_data: Optional[bytes] = None,
                       mesh_name: Optional[str] = None, double_sided: bool = True,
                       manual_scale: float = 1.0, rotate_y_deg: float = 0.0,
                       flip_yz: Optional[bool] = None):
    """Rebuild a skinned-mesh DAT's bytes with geometry+textures from a glTF/FBX.

    ``data`` is the source DAT (its 0x2A mesh sections are replaced). The skeleton
    used to un-skin the model comes from ``skeleton_data`` when given (gear meshes
    have no skeleton of their own — pass the race body skeleton DAT's bytes), else
    from ``data`` itself (self-contained entity models). Returns
    ``(rebuilt_bytes, stats)`` and never touches the filesystem except a temp dir.
    """
    tmp_dir: Optional[str] = None
    try:
        tmp_dir = tempfile.mkdtemp(prefix="xi_mesh_")
        if model_path.suffix.lower() == ".fbx":
            gltf_path = convert_fbx_to_gltf(model_path, Path(tmp_dir))
        else:
            gltf_path = model_path

        sections = parse_sections(data)
        mesh_sections = [s for s in sections if s.type_code == SECTION_TYPE_SKELETON_MESH]
        if not mesh_sections:
            raise ValueError("DAT has no skeleton mesh section to replace")

        # Skeleton: from the separate race DAT (gear) or the DAT itself (entity).
        if skeleton_data is not None:
            skel_sections = parse_sections(skeleton_data)
            skeleton_section = next((s for s in skel_sections if s.type_code == SECTION_TYPE_SKELETON), None)
            if skeleton_section is None:
                raise ValueError("Skeleton DAT has no skeleton section")
            joints = parse_skeleton(skeleton_data, skeleton_section)
        else:
            skeleton_section = next((s for s in sections if s.type_code == SECTION_TYPE_SKELETON), None)
            if skeleton_section is None:
                raise ValueError("DAT has no skeleton section (pass a skeleton DAT for gear meshes)")
            joints = parse_skeleton(data, skeleton_section)
        globals_by_joint = compute_global_transforms(joints)
        dat_texture_names = list(parse_textures(data, sections).keys())

        doc, buffers = load_gltf_document(gltf_path)
        vertices, primitives = extract_geometry(
            doc, buffers, globals_by_joint, dat_texture_names,
            manual_scale, rotate_y_deg, flip_yz=flip_yz, gltf_path=gltf_path)
        if not vertices:
            raise ValueError("No geometry found in the imported model")

        base_name = mesh_name or mesh_sections[0].name
        batches = split_into_sections(vertices, primitives)
        mesh_blobs = [
            build_mesh_section(("m%03d" % i) if len(batches) > 1 else base_name, bv, bp, len(joints), double_sided=double_sided)
            for i, (bv, bp) in enumerate(batches)
        ]

        # Encode any textures the user added (not already in the DAT) as new
        # 0x20 sections so the mesh's 0x8000 references resolve.
        texture_sections: List[bytes] = []
        used_ids = {s.name for s in sections}
        for tex_name, png in collect_new_textures(doc, buffers, gltf_path, dat_texture_names, Path(tmp_dir)).items():
            sid = "".join(ch for ch in tex_name if ch.isalnum())[:4] or "tex0"
            base_sid = sid
            n = 0
            while sid in used_ids:
                n += 1
                sid = (base_sid[:3] + str(n))[:4]
            used_ids.add(sid)
            sec = encode_png_to_texture_section(sid, tex_name, png, Path(tmp_dir), max_size=TEXTURE_CLAMP)
            if sec is not None:
                texture_sections.append(sec)

        # Overwrite edited textures that already exist in the DAT (PNG -> DDS ->
        # in-place 0x20 swap, keeping the section id + name so mesh refs resolve).
        replace_sections: Dict[int, bytes] = {}
        for idx, sid, tex_name, png in collect_texture_replacements(doc, buffers, gltf_path, data, sections, Path(tmp_dir)):
            sec = encode_png_to_texture_section(sid, tex_name, png, Path(tmp_dir))
            if sec is not None:
                replace_sections[idx] = sec

        rebuilt = rebuild_dat(data, sections, mesh_blobs, texture_sections, replace_sections)
        stats = {
            "vertices": len(vertices),
            "triangles": sum(len(p.triangles) for p in primitives),
            "texture_map": [(p.texture_name, len(p.triangles)) for p in primitives],
            "textures": len(texture_sections),
            "textures_replaced": len(replace_sections),
        }
        return rebuilt, stats
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def build_texture_only_dat(data: bytes, model_path: Path, *, tex_local: bool = False):
    """Rebuild a DAT's bytes replacing ONLY its 0x20 texture sections; geometry,
    skeleton and every other section pass through untouched (``--tex``).

    Image bytes come from the glTF itself (embedded or uri), or — with
    ``tex_local`` — from files on disk next to the model, matched by name to the
    DAT's texture sections with the glTF materials as a naming reference.
    Returns ``(rebuilt_bytes, stats)``.
    """
    tmp_dir: Optional[str] = None
    try:
        tmp_dir = tempfile.mkdtemp(prefix="xi_tex_")
        if model_path.suffix.lower() == ".fbx":
            gltf_path = convert_fbx_to_gltf(model_path, Path(tmp_dir))
        else:
            gltf_path = model_path

        sections = parse_sections(data)
        doc, buffers = load_gltf_document(gltf_path)
        if tex_local:
            replacements = collect_local_texture_replacements(doc, gltf_path, data, sections)
        else:
            replacements = collect_texture_replacements(doc, buffers, gltf_path, data,
                                                        sections, Path(tmp_dir))
        if not replacements:
            names = sorted(full for _i, _sid, full in _index_dat_textures(data, sections).values())
            where = f"next to {gltf_path.name}" if tex_local else "in the model"
            raise ValueError(
                f"No images {where} match the DAT's texture names: "
                f"{', '.join(names) if names else '(DAT has no texture sections)'}")

        replace_sections: Dict[int, bytes] = {}
        tex_map: List[tuple] = []
        warnings: List[str] = []
        from PIL import Image
        for idx, sid, tex_name, png in replacements:
            # A --split-tex export writes a stacked 2-up atlas (height doubled)
            # whose remapped UVs live in the GLB GEOMETRY. Texture-only import
            # keeps the DAT's original mirrored UVs, so pushing the doubled
            # atlas in would render squashed/wrong — warn instead of silently
            # breaking (a full import brings the remapped UVs along).
            old_w, old_h = struct.unpack_from("<II", data, sections[idx].data_start + 0x15)
            with Image.open(png) as im:
                src_w, src_h = im.size
            if (src_w, src_h) == (old_w, old_h * 2):
                warnings.append(
                    f"{tex_name}: {png.name} is {src_w}x{src_h} — exactly double the DAT's "
                    f"{old_w}x{old_h}, which looks like a --split-tex 2-up atlas. The DAT's "
                    f"mesh keeps its original mirrored UVs in texture-only mode, so this "
                    f"will sample wrong in-game. Run a full import (without --tex) instead.")
            sec = encode_png_to_texture_section(sid, tex_name, png, Path(tmp_dir))
            if sec is None:
                continue
            replace_sections[idx] = sec
            tex_map.append((tex_name, png.name))
        if not replace_sections:
            raise ValueError("Texture encoding failed for every matched image (is texconv available?)")
        if warnings:
            import click
            for w in warnings:
                click.echo(f"WARNING: {w}", err=True)

        output = bytearray()
        for index, section in enumerate(sections):
            output += replace_sections.get(index, data[section.start: section.start + section.size])
        stats = {"textures_replaced": len(replace_sections), "texture_map": tex_map,
                 "warnings": warnings}
        return bytes(output), stats
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def import_mesh(dat_path: Path, model_path: Path, mesh_name: Optional[str] = None,
                double_sided: bool = True, manual_scale: float = 1.0,
                rotate_y_deg: float = 0.0, skeleton_dat: Optional[Path] = None,
                flip_yz: Optional[bool] = None,
                tex_only: bool = False, tex_local: bool = False):
    """Import an edited mesh into ``dat_path`` (written back in place, with a
    ``<dat>.base`` backup). For gear meshes, pass ``skeleton_dat`` (the race
    body skeleton) — gear DATs carry no skeleton of their own.

    ``tex_only`` replaces just the matching texture sections and leaves the
    geometry alone (layered on the CURRENT output DAT, so a prior mesh import
    survives); ``tex_local`` additionally sources the images from files next to
    the model instead of the glTF's embedded copies (implies ``tex_only``)."""
    if tex_local:
        tex_only = True
    # Texture-only edits layer on the existing output DAT (fresh=False) so a
    # prior geometry import isn't reverted; full imports start pristine.
    target = editable_dat(dat_path, fresh=not tex_only)
    data = target.read_bytes()

    # Keep a .base backup alongside the output DAT (same convention as the level
    # editor). In tex-only mode ``data`` may already carry edits, so seed the
    # backup from the pristine source instead.
    base_path = target.with_suffix(target.suffix + ".base")
    if not base_path.exists():
        base_path.write_bytes(Path(dat_path).read_bytes() if tex_only else data)

    if tex_only:
        rebuilt, stats = build_texture_only_dat(data, model_path, tex_local=tex_local)
        target.write_bytes(rebuilt)
        return target, 0, 0, stats["textures_replaced"], stats["texture_map"]

    skeleton_data = Path(read_path_for(skeleton_dat)).read_bytes() if skeleton_dat else None
    rebuilt, stats = build_imported_dat(
        data, model_path, skeleton_data=skeleton_data, mesh_name=mesh_name,
        double_sided=double_sided, manual_scale=manual_scale, rotate_y_deg=rotate_y_deg,
        flip_yz=flip_yz)
    target.write_bytes(rebuilt)
    return target, stats["vertices"], stats["triangles"], stats["textures"] + stats["textures_replaced"], stats["texture_map"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Import an edited FBX/glTF mesh back into an FFXI DAT.")
    parser.add_argument("dat_path", type=Path, help="DAT file to modify (a .base backup is kept)")
    parser.add_argument("model_path", type=Path, help="Edited .fbx, .gltf, or .glb")
    parser.add_argument("--mesh-name", default=None, help="4-char section name (default: reuse the DAT's first mesh section)")
    parser.add_argument("--single-sided", dest="double_sided", action="store_false", help="Do not emit reversed back-faces (default emits double-sided)")
    parser.add_argument("--scale", type=float, default=1.0, help="Uniformly scale the imported geometry (auto-aligned to the skeleton; default 1.0)")
    parser.add_argument("--rotate-y", type=float, default=0.0, help="Rotate the model about the vertical axis by N degrees (e.g. 90 or -90 to fix facing)")
    args = parser.parse_args()

    resolved = args.dat_path.resolve()
    out_path, verts, tris, ntex, tex_map = import_mesh(resolved, args.model_path.resolve(), args.mesh_name, args.double_sided, args.scale, args.rotate_y)
    print(f"Wrote DAT: {out_path}")
    print(f"Wrote mesh: {verts} vertices, {tris} triangles, {ntex} new texture(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------

import click as _click  # noqa: E402


@_click.command('import')
@_click.argument('dat_path')
@_click.argument('model_path', required=False, type=_click.Path(exists=True, path_type=Path))
@_click.option('--mesh-name', default=None, help="4-char section name (default: reuse the DAT's first mesh section)")
@_click.option('--double-sided/--single-sided', default=True, show_default=True,
               help='Emit reversed back-faces so thin surfaces (flags/cloth) are not culled')
@_click.option('--scale', type=float, default=1.0, show_default=True,
               help='Uniformly scale the imported geometry about the skeleton (auto-aligned; default 1.0)')
@_click.option('--rotate-y', type=float, default=0.0, show_default=True,
               help='Rotate the model about the vertical axis by N degrees (e.g. 90 / -90 to fix facing)')
@_click.option('--flip-yz/--no-flip-yz', default=None,
               help='Flip Y and Z axes on import. Auto-detected from asset.generator: '
                    'Blender exports need the flip; C4D and other Y-up-native tools do not. '
                    'Override with --flip-yz (Blender) or --no-flip-yz (C4D/standard).')
@_click.option('--tex', 'tex_only', is_flag=True, default=False,
               help="Only import textures: re-encode the model's images over the DAT's "
                    'matching 0x20 sections. Geometry is untouched (a prior mesh import survives).')
@_click.option('--tex-local', 'tex_local', is_flag=True, default=False,
               help='Texture-only import sourcing images from files next to the model '
                    '(matched by name; the glTF is only a naming reference). Implies --tex.')
def cmd(dat_path: str, model_path, mesh_name, double_sided: bool, scale: float, rotate_y: float, flip_yz, tex_only: bool, tex_local: bool):
    """Import an edited mesh (.fbx/.gltf/.glb) back into an FFXI DAT.

    DAT_PATH may be a filesystem path or a ROM-relative spec like ROM/7/97.
    MODEL_PATH is optional — if omitted, the model exported for this DAT
    (exports/mesh/<rom>/<stem>.fbx, then .glb/.gltf) is used automatically.
    Replaces the DAT's skeleton-mesh section(s) with the edited geometry,
    re-skinned to the DAT's existing skeleton (auto-scaled to match it). A
    <dat>.base backup is kept and used as the source on every run.

    With --tex, only the DAT's texture sections are replaced (geometry left
    alone); --tex-local additionally reads the images from files on disk next
    to the model instead of the copies embedded in the GLB.
    """
    try:
        resolved = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise _click.ClickException(str(e))
    if model_path is None:
        model_path = default_model_path(resolved)
        if model_path is None:
            raise _click.ClickException(
                f"No model given and none found at {default_output_dir(resolved)}. "
                f"Export first, or pass a model path."
            )
        _click.echo(f"Using model:    {model_path}")
    try:
        out_path, verts, tris, ntex, tex_map = import_mesh(
            resolved, model_path.resolve(), mesh_name, double_sided, scale, rotate_y,
            flip_yz=flip_yz, tex_only=tex_only, tex_local=tex_local)
    except ValueError as e:
        raise _click.ClickException(str(e))
    if tex_only or tex_local:
        _click.echo(f'Wrote DAT:      {out_path}')
        _click.echo(f'Textures replaced: {ntex}')
        for tex_name, source in tex_map:
            _click.echo(f'  {tex_name:16s} <- {source}')
        return
    pristine = resolved if out_path != resolved else resolved.with_name(resolved.name + ".base")
    _click.echo(f'Wrote DAT:      {out_path}')
    _click.echo(f'New textures:   {ntex}')
    _click.echo('Comparison (base -> result):')
    _click.echo(format_comparison(summarize_dat(pristine), summarize_dat(out_path)))

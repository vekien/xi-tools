#!/usr/bin/env python3

import argparse
import json
import math
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from xi.xi_config import BLENDER_PATH, read_path_for
from xi.utils.xi_core import DEFAULT_ALPHA_SCALE, encode_png_rgba, scale_alpha

_GLTF_TO_FBX_SCRIPT = Path(__file__).with_name("xi_gltf_to_fbx.py")


GAME_FPS = 30.0
SECTION_TYPE_SKELETON = 0x29
SECTION_TYPE_SKELETON_MESH = 0x2A
SECTION_TYPE_SKELETON_ANIMATION = 0x2B
ROOT_CORRECTION_ROTATION = [1.0, 0.0, 0.0, 0.0]


def default_anim_output_dir(dat_path: Path) -> Path:
    """Default animation export location under exports/anim/.

    e.g. ``<FFXI_DIR>/ROM/37/13.DAT`` ->
    ``<XI_TOOLS_DIR>/exports/anim/rom/37/13/``.
    DATs outside FFXI_DIR fall back to ``exports/anim/<stem>/``.
    """
    from xi.xi_config import XI_TOOLS_DIR, FFXI_DIR

    base = Path(XI_TOOLS_DIR) / "exports" / "anim"
    try:
        rel = dat_path.resolve().relative_to(Path(FFXI_DIR).resolve())
    except ValueError:
        return base / dat_path.stem
    parts = [rel.parts[0].lower(), *rel.parts[1:-1], rel.stem]
    return base.joinpath(*parts)


def legacy_anim_output_dir(dat_path: Path) -> Path:
    """Pre-rename animation export location, used only as an import fallback."""
    from xi.xi_config import XI_TOOLS_DIR, FFXI_DIR

    base = Path(XI_TOOLS_DIR) / "exports" / "entity" / "anim"
    try:
        rel = dat_path.resolve().relative_to(Path(FFXI_DIR).resolve())
    except ValueError:
        return base / dat_path.stem
    parts = [rel.parts[0].lower(), *rel.parts[1:-1], rel.stem]
    return base.joinpath(*parts)


class Reader:
    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def seek(self, pos: int) -> None:
        self.pos = pos

    def tell(self) -> int:
        return self.pos

    def u8(self) -> int:
        value = self.data[self.pos]
        self.pos += 1
        return value

    def u16(self) -> int:
        value = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return value

    def i32(self) -> int:
        value = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return value

    def u32(self) -> int:
        value = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return value

    def f32(self) -> float:
        value = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return value

    def string(self, length: int) -> str:
        raw = self.data[self.pos : self.pos + length]
        self.pos += length
        return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")


@dataclass
class Section:
    name: str
    type_code: int
    start: int
    size: int
    data_start: int


@dataclass
class Joint:
    index: int
    parent_index: int
    rotation: Tuple[float, float, float, float]
    translation: Tuple[float, float, float]


@dataclass
class JointGlobal:
    rotation: Tuple[float, float, float, float]
    translation: Tuple[float, float, float]


@dataclass
class JointRef:
    index: int
    flipped_index: int
    flip_axis: int


@dataclass
class VertexSource:
    joint_ref0: JointRef
    joint_ref1: JointRef
    joint_index0: int
    joint_index1: int
    mirrored_joint_index0: int
    mirrored_joint_index1: int
    p0: Tuple[float, float, float]
    p1: Tuple[float, float, float]
    n0: Tuple[float, float, float]
    n1: Tuple[float, float, float]
    weight0: float
    weight1: float


@dataclass
class Corner:
    vertex_index: int
    uv: Tuple[float, float]
    color: Optional[Tuple[float, float, float, float]] = None
    mirrored: bool = False


@dataclass
class Primitive:
    material_name: str
    corners: List[Corner]


@dataclass
class AnimationTrack:
    joint_index: int
    rotations: List[Tuple[float, float, float, float]]
    translations: List[Tuple[float, float, float]]
    scales: List[Tuple[float, float, float]]


@dataclass
class AnimationSection:
    name: str
    num_joints: int
    num_frames: int
    keyframe_duration: float
    tracks: Dict[int, AnimationTrack]


def align16(value: int) -> int:
    return (value + 0xF) & ~0xF


def parse_sections(data: bytes) -> List[Section]:
    sections: List[Section] = []
    pos = 0
    while pos + 16 <= len(data):
        name = data[pos : pos + 4].decode("ascii", errors="replace")
        meta = struct.unpack_from("<I", data, pos + 4)[0]
        type_code = meta & 0x7F
        size = ((meta >> 7) & 0xFFFFF) * 0x10
        if size <= 0:
            break
        sections.append(Section(name=name, type_code=type_code, start=pos, size=size, data_start=pos + 0x10))
        pos = align16(pos + size)
    return sections


def parse_skeleton(data: bytes, section: Section) -> List[Joint]:
    reader = Reader(data, section.data_start + 0x02)
    num_joints = reader.u8()
    reader.seek(section.data_start + 0x04)

    joints: List[Joint] = []
    for index in range(num_joints):
        maybe_parent = reader.u8()
        parent_index = -1 if maybe_parent == index else maybe_parent
        reader.u8()
        rotation = (reader.f32(), reader.f32(), reader.f32(), reader.f32())
        translation = (reader.f32(), reader.f32(), reader.f32())
        joints.append(Joint(index=index, parent_index=parent_index, rotation=rotation, translation=translation))

    return joints


def unpack_joint_ref(value: int) -> JointRef:
    return JointRef(index=value & 0x7F, flipped_index=(value >> 7) & 0x7F, flip_axis=(value >> 14) & 0x3)


def flip_vec3(vec: Tuple[float, float, float], flip_axis: int) -> Tuple[float, float, float]:
    x, y, z = vec
    if flip_axis == 1:
        return (-x, y, z)
    if flip_axis == 2:
        return (x, -y, z)
    if flip_axis == 3:
        return (x, y, -z)
    return vec


def read_render_properties(reader: Reader) -> None:
    reader.u8()
    reader.u8()
    reader.u8()
    reader.u8()
    reader.f32()
    reader.f32()
    reader.u8()
    reader.u8()
    reader.u8()
    reader.u8()
    reader.f32()
    reader.u32()
    reader.u32()
    reader.u16()
    reader.f32()
    reader.u16()
    reader.f32()
    reader.f32()


def parse_cloth_header(reader: Reader) -> None:
    reader.u16()
    reader.u16()
    reader.u32()
    reader.u16()
    reader.u16()
    reader.u16()
    reader.f32()
    reader.f32()
    reader.f32()
    reader.f32()
    reader.f32()


def parse_mesh(data: bytes, section: Section) -> Tuple[List[VertexSource], List[Primitive]]:
    reader = Reader(data, section.data_start)

    reader.u8()
    reader.u8()
    flags3 = reader.u8()
    cloth_effect = (flags3 & 0x01) != 0
    use_joint_array = (flags3 & 0x80) != 0
    has_normals = not cloth_effect

    reader.u8()
    symmetric = reader.u8() == 0x01
    reader.u8()

    instruction_offset = 2 * reader.u32()
    reader.u8()
    reader.u8()
    joint_array_offset = 2 * reader.u32()
    num_joints = reader.u16()
    vertex_counts_offset = 2 * reader.u32()
    reader.u16()
    vertex_joint_mapping_offset = 2 * reader.u32()
    reader.u16()
    vertex_data_offset = 2 * reader.u32()
    reader.u16()
    reader.u32()
    reader.u16()

    if cloth_effect:
        parse_cloth_header(reader)

    reader.seek(section.data_start + joint_array_offset)
    joint_array = [reader.u16() for _ in range(num_joints)]

    reader.seek(section.data_start + vertex_counts_offset)
    single_joint_count = reader.u16()
    double_joint_count = reader.u16()
    total_vertices = single_joint_count + double_joint_count

    vertices: List[VertexSource] = [
        VertexSource(
            joint_ref0=JointRef(0, 0, 0),
            joint_ref1=JointRef(0, 0, 0),
            joint_index0=0,
            joint_index1=0,
            mirrored_joint_index0=0,
            mirrored_joint_index1=0,
            p0=(0.0, 0.0, 0.0),
            p1=(0.0, 0.0, 0.0),
            n0=(0.0, 0.0, 1.0),
            n1=(0.0, 0.0, 1.0),
            weight0=1.0,
            weight1=0.0,
        )
        for _ in range(total_vertices)
    ]

    reader.seek(section.data_start + vertex_joint_mapping_offset)
    for index in range(single_joint_count):
        joint_ref0 = unpack_joint_ref(reader.u16())
        joint_ref1 = unpack_joint_ref(reader.u16())
        joint_index0 = joint_array[joint_ref0.index] if use_joint_array else joint_ref0.index
        mirrored_joint_index0 = joint_array[joint_ref0.flipped_index] if use_joint_array else joint_ref0.flipped_index
        vertices[index].joint_ref0 = joint_ref0
        vertices[index].joint_ref1 = joint_ref1
        vertices[index].joint_index0 = joint_index0
        vertices[index].mirrored_joint_index0 = mirrored_joint_index0

    for offset in range(double_joint_count):
        index = single_joint_count + offset
        joint_ref0 = unpack_joint_ref(reader.u16())
        joint_ref1 = unpack_joint_ref(reader.u16())
        joint_index0 = joint_array[joint_ref0.index] if use_joint_array else joint_ref0.index
        joint_index1 = joint_array[joint_ref1.index] if use_joint_array else joint_ref1.index
        mirrored_joint_index0 = joint_array[joint_ref0.flipped_index] if use_joint_array else joint_ref0.flipped_index
        mirrored_joint_index1 = joint_array[joint_ref1.flipped_index] if use_joint_array else joint_ref1.flipped_index
        vertices[index].joint_ref0 = joint_ref0
        vertices[index].joint_ref1 = joint_ref1
        vertices[index].joint_index0 = joint_index0
        vertices[index].joint_index1 = joint_index1
        vertices[index].mirrored_joint_index0 = mirrored_joint_index0
        vertices[index].mirrored_joint_index1 = mirrored_joint_index1

    reader.seek(section.data_start + vertex_data_offset)
    for index in range(single_joint_count):
        p0 = (reader.f32(), reader.f32(), reader.f32())
        n0 = (reader.f32(), reader.f32(), reader.f32()) if has_normals else (0.0, 0.0, 1.0)
        vertices[index].p0 = p0
        vertices[index].n0 = n0

    for offset in range(double_joint_count):
        index = single_joint_count + offset
        p0 = [0.0, 0.0, 0.0]
        p1 = [0.0, 0.0, 0.0]
        p0[0] = reader.f32()
        p1[0] = reader.f32()
        p0[1] = reader.f32()
        p1[1] = reader.f32()
        p0[2] = reader.f32()
        p1[2] = reader.f32()
        weight0 = reader.f32()
        weight1 = reader.f32()
        if has_normals:
            n0 = [0.0, 0.0, 0.0]
            n1 = [0.0, 0.0, 0.0]
            n0[0] = reader.f32()
            n1[0] = reader.f32()
            n0[1] = reader.f32()
            n1[1] = reader.f32()
            n0[2] = reader.f32()
            n1[2] = reader.f32()
        else:
            n0 = [0.0, 0.0, 1.0]
            n1 = [0.0, 0.0, 1.0]

        vertices[index].p0 = tuple(p0)
        vertices[index].p1 = tuple(p1)
        vertices[index].n0 = tuple(n0)
        vertices[index].n1 = tuple(n1)
        vertices[index].weight0 = weight0
        vertices[index].weight1 = weight1

    def tri_strip_to_corners(strip_corners: List[Corner]) -> List[Corner]:
        corners: List[Corner] = []
        for tri_index in range(len(strip_corners) - 2):
            if tri_index % 2 == 0:
                tri = (strip_corners[tri_index], strip_corners[tri_index + 1], strip_corners[tri_index + 2])
            else:
                tri = (strip_corners[tri_index + 1], strip_corners[tri_index], strip_corners[tri_index + 2])
            corners.extend(tri)
        return corners

    def reverse_winding(corners: List[Corner]) -> List[Corner]:
        # FFXI front faces are CLOCKWISE (Direct3D convention — see xim GLDrawer
        # frontFace(CW)). glTF/Blender/C4D treat COUNTER-CLOCKWISE as front, so
        # every non-mirrored triangle must have its winding reversed or the whole
        # surface renders inside-out (dark, faceted). Swap the 2nd and 3rd corner.
        result: List[Corner] = []
        for i in range(0, len(corners), 3):
            result.append(corners[i])
            result.append(corners[i + 2])
            result.append(corners[i + 1])
        return result

    def mirror_corners(corners: List[Corner]) -> List[Corner]:
        # The mirrored half is a reflection (handedness flip), which inverts winding
        # on its own. So relative to the winding-reversed originals it needs the
        # raw order — keep the source order here and just flag it mirrored.
        return [Corner(vertex_index=c.vertex_index, uv=c.uv, color=c.color, mirrored=True) for c in corners]

    reader.seek(section.data_start + instruction_offset)
    current_texture = "untextured"
    primitives: List[Primitive] = []

    while True:
        opcode = reader.u16()
        if opcode == 0xFFFF:
            break
        if opcode == 0x8010:
            read_render_properties(reader)
            continue
        if opcode == 0x8000:
            current_texture = reader.string(0x10) or "untextured"
            continue
        if opcode == 0x5453:
            num_triangles = reader.u16()
            strip: List[Corner] = []
            for _ in range(3):
                strip.append(Corner(vertex_index=reader.u16(), uv=(0.0, 0.0)))
            strip[0].uv = (reader.f32(), reader.f32())
            strip[1].uv = (reader.f32(), reader.f32())
            strip[2].uv = (reader.f32(), reader.f32())
            for _ in range(1, num_triangles):
                strip.append(Corner(vertex_index=reader.u16(), uv=(reader.f32(), reader.f32())))
            corners = tri_strip_to_corners(strip)
            primitives.append(Primitive(material_name=current_texture, corners=reverse_winding(corners)))
            if symmetric:
                primitives.append(Primitive(material_name=current_texture, corners=mirror_corners(corners)))
            continue
        if opcode == 0x0054:
            num_triangles = reader.u16()
            corners: List[Corner] = []
            for _ in range(num_triangles):
                v0 = reader.u16()
                v1 = reader.u16()
                v2 = reader.u16()
                uv0 = (reader.f32(), reader.f32())
                uv1 = (reader.f32(), reader.f32())
                uv2 = (reader.f32(), reader.f32())
                corners.extend((Corner(v0, uv0), Corner(v1, uv1), Corner(v2, uv2)))
            primitives.append(Primitive(material_name=current_texture, corners=reverse_winding(corners)))
            if symmetric:
                primitives.append(Primitive(material_name=current_texture, corners=mirror_corners(corners)))
            continue
        if opcode == 0x0043:
            num_triangles = reader.u16()
            corners = []
            for _ in range(num_triangles):
                v0 = reader.u16()
                v1 = reader.u16()
                v2 = reader.u16()
                b, g, r, a = reader.u8(), reader.u8(), reader.u8(), reader.u8()
                color = (r / 255.0, g / 255.0, b / 255.0, a / 255.0)
                corners.extend((Corner(v0, (0.0, 0.0), color), Corner(v1, (0.0, 0.0), color), Corner(v2, (0.0, 0.0), color)))
            primitives.append(Primitive(material_name="vertex_color", corners=reverse_winding(corners)))
            if symmetric:
                primitives.append(Primitive(material_name="vertex_color", corners=mirror_corners(corners)))
            continue
        if opcode == 0x4353:
            num_triangles = reader.u16()
            strip = [Corner(vertex_index=reader.u16(), uv=(0.0, 0.0)), Corner(vertex_index=reader.u16(), uv=(0.0, 0.0)), Corner(vertex_index=reader.u16(), uv=(0.0, 0.0))]
            b, g, r, a = reader.u8(), reader.u8(), reader.u8(), reader.u8()
            color = (r / 255.0, g / 255.0, b / 255.0, a / 255.0)
            for corner in strip:
                corner.color = color
            for _ in range(1, num_triangles):
                strip.append(Corner(vertex_index=reader.u16(), uv=(0.0, 0.0), color=color))
            corners = tri_strip_to_corners(strip)
            primitives.append(Primitive(material_name="vertex_color", corners=reverse_winding(corners)))
            if symmetric:
                primitives.append(Primitive(material_name="vertex_color", corners=mirror_corners(corners)))
            continue
        raise ValueError(f"Unknown mesh opcode 0x{opcode:04X} in section {section.name}")

    return vertices, primitives


def fmod_10000(value: float) -> float:
    return math.fmod(value, 10000.0)


def parse_animation(data: bytes, section: Section) -> AnimationSection:
    reader = Reader(data, section.data_start)
    reader.u16()
    num_joints = reader.u16()
    num_frames = reader.u16()
    keyframe_duration = reader.f32()
    keyframe_data_offset = reader.tell()

    def read_sequences(amount: int) -> Optional[List[List[float]]]:
        offsets = [reader.i32() for _ in range(amount)]
        const_values = [fmod_10000(reader.f32()) for _ in range(amount)]
        if any(offset < 0 for offset in offsets):
            return None

        sequences: List[List[float]] = []
        for index, offset in enumerate(offsets):
            if offset == 0:
                sequences.append([const_values[index]])
            else:
                seq_reader = Reader(data, keyframe_data_offset + offset * 4)
                sequences.append([seq_reader.f32() for _ in range(num_frames)])
        return sequences

    tracks: Dict[int, AnimationTrack] = {}
    for _ in range(num_joints):
        joint_index = reader.i32()
        rotation_sequences = read_sequences(4)
        translation_sequences = read_sequences(3)
        scale_sequences = read_sequences(3)
        if not rotation_sequences or not translation_sequences or not scale_sequences:
            continue

        rotations: List[Tuple[float, float, float, float]] = []
        translations: List[Tuple[float, float, float]] = []
        scales: List[Tuple[float, float, float]] = []
        for frame in range(num_frames):
            rotations.append(tuple(sequence[0] if len(sequence) == 1 else sequence[frame] for sequence in rotation_sequences))
            translations.append(tuple(sequence[0] if len(sequence) == 1 else sequence[frame] for sequence in translation_sequences))
            scales.append(tuple(sequence[0] if len(sequence) == 1 else sequence[frame] for sequence in scale_sequences))

        tracks[joint_index] = AnimationTrack(
            joint_index=joint_index,
            rotations=rotations,
            translations=translations,
            scales=scales,
        )

    return AnimationSection(
        name=section.name,
        num_joints=num_joints,
        num_frames=num_frames,
        keyframe_duration=keyframe_duration,
        tracks=tracks,
    )


def read_animation_header(data: bytes, section: Section) -> Tuple[int, int, float]:
    """Read only an animation section's ``(num_joints, num_frames, keyframe_duration)``
    header — enough to summarise a clip without decoding every keyframe. Mirrors the
    first four reads of :func:`parse_animation`."""
    reader = Reader(data, section.data_start)
    reader.u16()  # version / unknown
    num_joints = reader.u16()
    num_frames = reader.u16()
    keyframe_duration = reader.f32()
    return num_joints, num_frames, keyframe_duration


def list_animations(data: bytes) -> List[Dict[str, object]]:
    """Summarise every animation track (section type 0x2B) inside a parsed DAT.

    Returns one dict per clip with its name, joint/frame counts, raw
    ``keyframe_duration`` and a derived length in seconds (same time base the
    glTF exporter bakes against: ``(frames - 1) / keyframe_duration / GAME_FPS``)."""
    animations: List[Dict[str, object]] = []
    for section in parse_sections(data):
        if section.type_code != SECTION_TYPE_SKELETON_ANIMATION:
            continue
        if section.data_start + 10 > len(data):
            continue  # truncated/corrupt header — skip rather than crash
        num_joints, num_frames, keyframe_duration = read_animation_header(data, section)
        seconds = (num_frames - 1) / keyframe_duration / GAME_FPS if keyframe_duration > 0 else 0.0
        animations.append({
            "name": section.name.rstrip("\x00 "),
            "frames": num_frames,
            "joints": num_joints,
            "keyframe_duration": round(keyframe_duration, 4),
            "seconds": round(seconds, 2),
        })
    return animations


def pose_joints_at_frame(joints: List[Joint], animation: "AnimationSection", frame: int) -> List[Joint]:
    """Return the bind-pose joints re-posed to integer keyframe ``frame`` of ``animation``.

    Each animated joint's LOCAL transform is replaced exactly as the animation
    exporter poses it: ``local_rotation = trackRotation ⊗ bindRotation`` and
    ``local_translation = bindTranslation + trackTranslation``. Untracked joints
    keep their bind transform. ``frame`` is clamped to the animation's range.
    NOTE: animation bone SCALE is not applied (rigid bake; FFXI idles use unit
    scale) — fine for posing the mesh to match an in-game/Noesis idle frame.
    """
    if animation.num_frames <= 0:
        return list(joints)
    f = max(0, min(int(frame), animation.num_frames - 1))
    posed: List[Joint] = []
    for joint in joints:
        track = animation.tracks.get(joint.index)
        if track is None or f >= len(track.rotations):
            posed.append(joint)
            continue
        local_rotation = quat_normalize(quat_mul(track.rotations[f], joint.rotation))
        local_translation = add_vec3(joint.translation, track.translations[f])
        posed.append(Joint(index=joint.index, parent_index=joint.parent_index,
                           rotation=local_rotation, translation=local_translation))
    return posed


def quat_normalize(q: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    x, y, z, w = q
    mag = math.sqrt(x * x + y * y + z * z + w * w)
    if mag <= 1e-8:
        return (0.0, 0.0, 0.0, 1.0)
    inv = 1.0 / mag
    return (x * inv, y * inv, z * inv, w * inv)


def quat_mul(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def quat_conjugate(q: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    x, y, z, w = q
    return (-x, -y, -z, w)


def quat_nlerp(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float], t: float) -> Tuple[float, float, float, float]:
    dot = sum(x * y for x, y in zip(a, b))
    bx, by, bz, bw = b
    if dot < 0.0:
        bx, by, bz, bw = -bx, -by, -bz, -bw
    q = (
        a[0] + t * (bx - a[0]),
        a[1] + t * (by - a[1]),
        a[2] + t * (bz - a[2]),
        a[3] + t * (bw - a[3]),
    )
    return quat_normalize(q)


def rotate_vec3(q: Tuple[float, float, float, float], v: Tuple[float, float, float]) -> Tuple[float, float, float]:
    qx, qy, qz, qw = q
    vx, vy, vz = v
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return (
        vx + qw * tx + (qy * tz - qz * ty),
        vy + qw * ty + (qz * tx - qx * tz),
        vz + qw * tz + (qx * ty - qy * tx),
    )


def add_vec3(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> Tuple[float, float, float]:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def mul_vec3(v: Tuple[float, float, float], s: float) -> Tuple[float, float, float]:
    return (v[0] * s, v[1] * s, v[2] * s)


def normalize_vec3(v: Tuple[float, float, float]) -> Tuple[float, float, float]:
    mag = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if mag <= 1e-8:
        return (0.0, 0.0, 1.0)
    inv = 1.0 / mag
    return (v[0] * inv, v[1] * inv, v[2] * inv)


def compute_global_transforms(joints: Sequence[Joint]) -> List[JointGlobal]:
    globals_out: List[JointGlobal] = [JointGlobal(rotation=(0.0, 0.0, 0.0, 1.0), translation=(0.0, 0.0, 0.0)) for _ in joints]
    for joint in joints:
        local_rot = quat_normalize(joint.rotation)
        local_trans = joint.translation
        if joint.parent_index < 0:
            globals_out[joint.index] = JointGlobal(rotation=local_rot, translation=local_trans)
        else:
            parent = globals_out[joint.parent_index]
            globals_out[joint.index] = JointGlobal(
                rotation=quat_normalize(quat_mul(parent.rotation, local_rot)),
                translation=add_vec3(parent.translation, rotate_vec3(parent.rotation, local_trans)),
            )
    return globals_out


def rigid_inverse_matrix(q: Tuple[float, float, float, float], t: Tuple[float, float, float]) -> List[float]:
    """Inverse of the rigid transform M = T(t) @ R(q), as a column-major glTF mat4.

    For a rigid transform, M^-1 = [R^T | -R^T t]. Returns the glTF
    inverse-bind matrix so that ``boneGlobal @ ibm == identity`` at bind pose
    (verified numerically against the bone hierarchy). The previous version
    double-applied the rotation transpose and produced ``ibm @ global != I``,
    which made every skinned vertex deform the instant a renderer applied the
    skin — the whole mesh tore apart (worst in the multi-joint torso).
    """
    x, y, z, w = quat_normalize(q)
    xx = x * x
    yy = y * y
    zz = z * z
    xy = x * y
    xz = x * z
    yz = y * z
    wx = w * x
    wy = w * y
    wz = w * z

    # Rotation matrix R(q) (row-major rows r0_, r1_, r2_).
    r00 = 1.0 - 2.0 * (yy + zz)
    r01 = 2.0 * (xy - wz)
    r02 = 2.0 * (xz + wy)
    r10 = 2.0 * (xy + wz)
    r11 = 1.0 - 2.0 * (xx + zz)
    r12 = 2.0 * (yz - wx)
    r20 = 2.0 * (xz - wy)
    r21 = 2.0 * (yz + wx)
    r22 = 1.0 - 2.0 * (xx + yy)

    # Translation of the inverse: -(R^T @ t).
    itx = -(r00 * t[0] + r10 * t[1] + r20 * t[2])
    ity = -(r01 * t[0] + r11 * t[1] + r21 * t[2])
    itz = -(r02 * t[0] + r12 * t[1] + r22 * t[2])

    # Column-major mat4 for ibm = [R^T | -R^T t]: stored columns are the rows of R.
    return [
        r00, r01, r02, 0.0,
        r10, r11, r12, 0.0,
        r20, r21, r22, 0.0,
        itx, ity, itz, 1.0,
    ]


def sample_track(track: AnimationTrack, keyframe_duration: float, sample_frame: float) -> Tuple[Tuple[float, float, float, float], Tuple[float, float, float], Tuple[float, float, float]]:
    scaled = sample_frame * keyframe_duration
    if scaled >= len(track.rotations) - 1:
        return track.rotations[-1], track.translations[-1], track.scales[-1]

    lower = int(math.floor(scaled))
    upper = lower + 1
    delta = scaled - lower
    rotation = quat_nlerp(track.rotations[lower], track.rotations[upper], delta)
    translation = tuple(track.translations[lower][i] + delta * (track.translations[upper][i] - track.translations[lower][i]) for i in range(3))
    scale = tuple(track.scales[lower][i] + delta * (track.scales[upper][i] - track.scales[lower][i]) for i in range(3))
    return rotation, translation, scale


def resolve_corner_vertex(vertex: VertexSource, globals_by_joint: Sequence[JointGlobal], mirrored: bool) -> Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[int, int, int, int], Tuple[float, float, float, float]]:
    if mirrored:
        p0 = flip_vec3(vertex.p0, vertex.joint_ref0.flip_axis)
        p1 = flip_vec3(vertex.p1, vertex.joint_ref1.flip_axis)
        n0 = flip_vec3(vertex.n0, vertex.joint_ref0.flip_axis)
        n1 = flip_vec3(vertex.n1, vertex.joint_ref1.flip_axis)
        joint0 = vertex.mirrored_joint_index0
        joint1 = vertex.mirrored_joint_index1 if vertex.weight1 > 0.0 else 0
    else:
        p0 = vertex.p0
        p1 = vertex.p1
        n0 = vertex.n0
        n1 = vertex.n1
        joint0 = vertex.joint_index0
        joint1 = vertex.joint_index1

    # FFXI two-joint skinning (from the model viewer's GetPrimitiveVertex):
    # the stored joint-local position p_i is ALREADY weight-baked, and the weight
    # scales ONLY the bone translation — so the world contribution is
    # ``rotate(g_i, p_i) + weight_i * g_i.translation`` and the two are SUMMED.
    # The previous code did ``weight0*pos0 + weight1*pos1`` over the full transform,
    # which double-weighted the rotated position and collapsed every 2-joint vertex
    # (the torso) — verified against the Noesis reference (mean error 1.22 -> 0.0).
    g0 = globals_by_joint[joint0]
    pos0 = add_vec3(mul_vec3(g0.translation, vertex.weight0), rotate_vec3(g0.rotation, p0))
    norm0 = rotate_vec3(g0.rotation, n0)

    if vertex.weight1 > 0.0:
        g1 = globals_by_joint[joint1]
        pos1 = add_vec3(mul_vec3(g1.translation, vertex.weight1), rotate_vec3(g1.rotation, p1))
        norm1 = rotate_vec3(g1.rotation, n1)
        position = add_vec3(pos0, pos1)
        normal = normalize_vec3(add_vec3(mul_vec3(norm0, vertex.weight0), mul_vec3(norm1, vertex.weight1)))
    else:
        position = pos0
        normal = normalize_vec3(norm0)

    weights = (vertex.weight0, vertex.weight1 if vertex.weight1 > 0.0 else 0.0, 0.0, 0.0)
    joints = (joint0, joint1 if vertex.weight1 > 0.0 else 0, 0, 0)
    return position, normal, joints, weights


class BufferBuilder:
    def __init__(self) -> None:
        self.data = bytearray()
        self.buffer_views: List[dict] = []
        self.accessors: List[dict] = []

    def _align4(self) -> None:
        while len(self.data) % 4 != 0:
            self.data.append(0)

    def add_bytes(self, payload: bytes, target: Optional[int] = None) -> int:
        self._align4()
        offset = len(self.data)
        self.data.extend(payload)
        buffer_view = {"buffer": 0, "byteOffset": offset, "byteLength": len(payload)}
        if target is not None:
            buffer_view["target"] = target
        self.buffer_views.append(buffer_view)
        return len(self.buffer_views) - 1

    def add_accessor(self, payload: bytes, component_type: int, type_name: str, count: int, *, target: Optional[int] = None, min_value: Optional[List[float]] = None, max_value: Optional[List[float]] = None) -> int:
        buffer_view = self.add_bytes(payload, target=target)
        accessor = {
            "bufferView": buffer_view,
            "componentType": component_type,
            "count": count,
            "type": type_name,
        }
        if min_value is not None:
            accessor["min"] = min_value
        if max_value is not None:
            accessor["max"] = max_value
        self.accessors.append(accessor)
        return len(self.accessors) - 1


def pack_f32_scalars(values: Sequence[float]) -> bytes:
    return struct.pack("<" + "f" * len(values), *values)


def pack_vec2(values: Sequence[Tuple[float, float]]) -> bytes:
    flat: List[float] = []
    for value in values:
        flat.extend(value)
    return pack_f32_scalars(flat)


def pack_vec3(values: Sequence[Tuple[float, float, float]]) -> bytes:
    flat: List[float] = []
    for value in values:
        flat.extend(value)
    return pack_f32_scalars(flat)


def pack_vec4(values: Sequence[Tuple[float, float, float, float]]) -> bytes:
    flat: List[float] = []
    for value in values:
        flat.extend(value)
    return pack_f32_scalars(flat)


def pack_mat4(values: Sequence[Sequence[float]]) -> bytes:
    flat: List[float] = []
    for value in values:
        flat.extend(value)
    return pack_f32_scalars(flat)


def pack_u16_vec4(values: Sequence[Tuple[int, int, int, int]]) -> bytes:
    flat: List[int] = []
    for value in values:
        flat.extend(value)
    return struct.pack("<" + "H" * len(flat), *flat)


def compute_min_max_vec3(values: Sequence[Tuple[float, float, float]]) -> Tuple[List[float], List[float]]:
    mins = [min(component[i] for component in values) for i in range(3)]
    maxs = [max(component[i] for component in values) for i in range(3)]
    return mins, maxs


def build_gltf(dat_path: Path, output_dir: Path, joints: List[Joint], globals_by_joint: List[JointGlobal], primitives: List[Primitive], vertices: List[VertexSource], animation: AnimationSection, out_stem: Optional[str] = None, textures: Optional[Dict[str, "TextureImage"]] = None, alpha_scale: float = DEFAULT_ALPHA_SCALE) -> Tuple[Path, Path]:
    # File basename: default ``<dat stem>_<anim>`` (e.g. 59_cor0); callers doing a
    # per-track tree pass out_stem (e.g. 'cor0') so files land as cor0.gltf/.bin.
    stem = out_stem if out_stem is not None else f"{dat_path.stem}_{animation.name}"
    builder = BufferBuilder()
    textures = textures or {}
    output_dir.mkdir(parents=True, exist_ok=True)

    # Decode each referenced texture to a PNG beside the .gltf and index it. Only a
    # material whose name matches an embedded texture gets an image; vertex-colour /
    # untextured primitives stay flat white. Images are external URIs (the glTF
    # already ships a sibling .bin) so they're trivial to edit and re-import.
    referenced = {p.material_name for p in primitives if p.material_name in textures}
    images: List[dict] = []
    gltf_textures: List[dict] = []
    texture_index_by_name: Dict[str, int] = {}
    if referenced:
        from xi.entity.mesh.xi_export import sanitize_filename
        for name in sorted(referenced):
            image = textures[name]
            rgba = scale_alpha(image.rgba, alpha_scale)
            png_name = f"{sanitize_filename(name)}.png"
            (output_dir / png_name).write_bytes(encode_png_rgba(image.width, image.height, rgba))
            images.append({"uri": png_name, "name": name})
            gltf_textures.append({"source": len(images) - 1, "sampler": 0})
            texture_index_by_name[name] = len(gltf_textures) - 1

    materials: List[dict] = []
    material_lookup: Dict[str, int] = {}
    for primitive in primitives:
        if primitive.material_name not in material_lookup:
            material_lookup[primitive.material_name] = len(materials)
            pbr = {
                "baseColorFactor": [1.0, 1.0, 1.0, 1.0],
                "metallicFactor": 0.0,
                "roughnessFactor": 1.0,
            }
            material = {
                "name": primitive.material_name,
                "doubleSided": True,
                "pbrMetallicRoughness": pbr,
            }
            if primitive.material_name in texture_index_by_name:
                pbr["baseColorTexture"] = {"index": texture_index_by_name[primitive.material_name]}
                material["alphaMode"] = "MASK"
            materials.append(material)

    mesh_primitives: List[dict] = []
    for primitive in primitives:
        positions: List[Tuple[float, float, float]] = []
        normals: List[Tuple[float, float, float]] = []
        texcoords: List[Tuple[float, float]] = []
        joints0: List[Tuple[int, int, int, int]] = []
        weights0: List[Tuple[float, float, float, float]] = []
        colors: List[Tuple[float, float, float, float]] = []
        has_color = any(corner.color is not None for corner in primitive.corners)

        for corner in primitive.corners:
            vertex = vertices[corner.vertex_index]
            position, normal, joint_indices, weight_values = resolve_corner_vertex(vertex, globals_by_joint, corner.mirrored)
            positions.append(position)
            normals.append(normal)
            texcoords.append(corner.uv)
            joints0.append(joint_indices)
            weights0.append(weight_values)
            if has_color:
                colors.append(corner.color or (1.0, 1.0, 1.0, 1.0))

        pos_min, pos_max = compute_min_max_vec3(positions)
        attributes = {
            "POSITION": builder.add_accessor(pack_vec3(positions), 5126, "VEC3", len(positions), target=34962, min_value=pos_min, max_value=pos_max),
            "NORMAL": builder.add_accessor(pack_vec3(normals), 5126, "VEC3", len(normals), target=34962),
            "TEXCOORD_0": builder.add_accessor(pack_vec2(texcoords), 5126, "VEC2", len(texcoords), target=34962),
            "JOINTS_0": builder.add_accessor(pack_u16_vec4(joints0), 5123, "VEC4", len(joints0), target=34962),
            "WEIGHTS_0": builder.add_accessor(pack_vec4(weights0), 5126, "VEC4", len(weights0), target=34962),
        }
        if has_color:
            attributes["COLOR_0"] = builder.add_accessor(pack_vec4(colors), 5126, "VEC4", len(colors), target=34962)

        mesh_primitives.append(
            {
                "attributes": attributes,
                "mode": 4,
                "material": material_lookup[primitive.material_name],
            }
        )

    has_mesh = bool(mesh_primitives)
    # Always emit the skin (joints + inverse-bind matrices) — even with no mesh.
    # A glTF skin is what makes Blender (and other importers) build a real
    # ARMATURE with one bone per joint; without it the joint nodes import as
    # loose empties, so the FBX comes out boneless and viewers show the rig as a
    # single rigid blob instead of an animated skeleton.
    inverse_bind_matrices = [rigid_inverse_matrix(global_joint.rotation, global_joint.translation) for global_joint in globals_by_joint]
    inverse_bind_accessor = builder.add_accessor(pack_mat4(inverse_bind_matrices), 5126, "MAT4", len(inverse_bind_matrices))

    nodes: List[dict] = []
    joint_node_indices: List[int] = []
    for joint in joints:
        node_index = len(nodes)
        joint_node_indices.append(node_index)
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

    mesh_node_index = None
    if has_mesh:
        mesh_node_index = len(nodes)
        nodes.append({"name": dat_path.stem, "mesh": 0, "skin": 0})

    animation_length_frames = max(1.0, (animation.num_frames - 1) / animation.keyframe_duration)
    baked_sample_count = int(math.ceil(animation_length_frames)) + 1
    times = [sample_index / GAME_FPS for sample_index in range(baked_sample_count)]
    time_accessor = builder.add_accessor(pack_f32_scalars(times), 5126, "SCALAR", len(times), min_value=[times[0]], max_value=[times[-1]])

    samplers: List[dict] = []
    channels: List[dict] = []
    for joint_index, track in sorted(animation.tracks.items()):
        bind_joint = joints[joint_index]
        translations: List[Tuple[float, float, float]] = []
        rotations: List[Tuple[float, float, float, float]] = []
        scales: List[Tuple[float, float, float]] = []

        for sample_index in range(baked_sample_count):
            rotation_delta, translation_delta, scale_value = sample_track(track, animation.keyframe_duration, float(sample_index))
            local_rotation = quat_normalize(quat_mul(rotation_delta, bind_joint.rotation))
            local_translation = add_vec3(bind_joint.translation, translation_delta)
            rotations.append(local_rotation)
            translations.append(local_translation)
            scales.append(scale_value)

        translation_accessor = builder.add_accessor(pack_vec3(translations), 5126, "VEC3", len(translations))
        rotation_accessor = builder.add_accessor(pack_vec4(rotations), 5126, "VEC4", len(rotations))
        scale_accessor = builder.add_accessor(pack_vec3(scales), 5126, "VEC3", len(scales))

        translation_sampler = len(samplers)
        samplers.append({"input": time_accessor, "output": translation_accessor, "interpolation": "LINEAR"})
        channels.append({"sampler": translation_sampler, "target": {"node": joint_node_indices[joint_index], "path": "translation"}})

        rotation_sampler = len(samplers)
        samplers.append({"input": time_accessor, "output": rotation_accessor, "interpolation": "LINEAR"})
        channels.append({"sampler": rotation_sampler, "target": {"node": joint_node_indices[joint_index], "path": "rotation"}})

        scale_sampler = len(samplers)
        samplers.append({"input": time_accessor, "output": scale_accessor, "interpolation": "LINEAR"})
        channels.append({"sampler": scale_sampler, "target": {"node": joint_node_indices[joint_index], "path": "scale"}})

    scene_root_index = len(nodes)
    nodes.append(
        {
            "name": "ffxi_root_correction",
            "rotation": ROOT_CORRECTION_ROTATION,
            "children": root_nodes + ([mesh_node_index] if has_mesh else []),
        }
    )

    gltf = {
        "asset": {"version": "2.0", "generator": "ffxi_dat_anim_export.py"},
        "scene": 0,
        "scenes": [{"nodes": [scene_root_index]}],
        "nodes": nodes,
        "skins": [
            {
                "name": f"{dat_path.stem}_skin",
                "joints": joint_node_indices,
                "inverseBindMatrices": inverse_bind_accessor,
                "skeleton": root_nodes[0] if root_nodes else joint_node_indices[0],
            }
        ],
        "animations": [{"name": animation.name, "samplers": samplers, "channels": channels}],
        "buffers": [{"byteLength": len(builder.data), "uri": f"{stem}.bin"}],
        "bufferViews": builder.buffer_views,
        "accessors": builder.accessors,
    }
    if has_mesh:
        gltf["meshes"] = [{"name": f"{dat_path.stem}_mesh", "primitives": mesh_primitives}]
        gltf["materials"] = materials
    if images:
        gltf["images"] = images
        gltf["textures"] = gltf_textures
        gltf["samplers"] = [{"wrapS": 10497, "wrapT": 10497, "magFilter": 9729, "minFilter": 9729}]

    gltf_path = output_dir / f"{stem}.gltf"
    bin_path = output_dir / f"{stem}.bin"
    gltf_path.write_text(json.dumps(gltf, indent=2), encoding="utf-8")
    bin_path.write_bytes(builder.data)
    return gltf_path, bin_path


def build_animation_arrays(builder, joints, joint_node_indices, animations):
    """Build a glTF ``animations`` array (samplers + channels) for a list of
    :class:`AnimationSection`, targeting the given joint nodes. Accessors/buffer-views are
    appended to ``builder`` (the shared :class:`BufferBuilder`), so call this BEFORE the
    caller finalises ``buffers``/``accessors``. Mirrors the per-joint baking in
    :func:`build_gltf`; lets the mesh/character exporters embed clips on a shared skeleton."""
    out = []
    for animation in animations:
        length_frames = max(1.0, (animation.num_frames - 1) / animation.keyframe_duration)
        sample_count = int(math.ceil(length_frames)) + 1
        times = [i / GAME_FPS for i in range(sample_count)]
        time_accessor = builder.add_accessor(pack_f32_scalars(times), 5126, "SCALAR", len(times),
                                             min_value=[times[0]], max_value=[times[-1]])
        samplers, channels = [], []
        for joint_index, track in sorted(animation.tracks.items()):
            if joint_index >= len(joints):
                continue
            bind_joint = joints[joint_index]
            translations, rotations, scales = [], [], []
            for sample_index in range(sample_count):
                rot_delta, trans_delta, scale_value = sample_track(track, animation.keyframe_duration, float(sample_index))
                rotations.append(quat_normalize(quat_mul(rot_delta, bind_joint.rotation)))
                translations.append(add_vec3(bind_joint.translation, trans_delta))
                scales.append(scale_value)
            ta = builder.add_accessor(pack_vec3(translations), 5126, "VEC3", len(translations))
            ra = builder.add_accessor(pack_vec4(rotations), 5126, "VEC4", len(rotations))
            sa = builder.add_accessor(pack_vec3(scales), 5126, "VEC3", len(scales))
            node = joint_node_indices[joint_index]
            for acc, path in ((ta, "translation"), (ra, "rotation"), (sa, "scale")):
                channels.append({"sampler": len(samplers), "target": {"node": node, "path": path}})
                samplers.append({"input": time_accessor, "output": acc, "interpolation": "LINEAR"})
        out.append({"name": animation.name, "samplers": samplers, "channels": channels})
    return out


def choose_animation(sections: Sequence[Section], animation_name: str) -> Section:
    # Strip the DAT's fixed-width padding (short names like 'tlk' are stored NUL-padded
    # to 4 bytes) before matching, so a 3-char custom track is found the same as 'idl0'.
    normalized = animation_name.lower().rstrip("\x00 ")
    candidates = [section for section in sections if section.type_code == SECTION_TYPE_SKELETON_ANIMATION]
    for section in candidates:
        name = section.name.lower().rstrip("\x00 ")
        if name == normalized or name.rstrip("0") == normalized.rstrip("0"):
            return section
    available = ", ".join(section.name.rstrip("\x00 ") for section in candidates)
    raise ValueError(f"Animation '{animation_name}' was not found. Available tracks: {available}")


def animation_variants(data: bytes, base: str) -> List[str]:
    """Every numbered variant of a base animation name, sorted.

    FFXI emote/animation clips come in numbered siblings — ``poi0`` (16-joint
    simplified) and ``poi1`` (full 71-joint), sometimes more. Given a digit-less
    base like ``poi``, return ['poi0', 'poi1', ...]: section names that are the
    base itself or the base followed by digits. Empty if nothing matches."""
    names: List[str] = []
    for section in parse_sections(data):
        if section.type_code != SECTION_TYPE_SKELETON_ANIMATION:
            continue
        name = section.name.rstrip("\x00 ")
        if name == base or (name.startswith(base) and name[len(base):].isdigit()):
            names.append(name)
    return sorted(set(names))


def convert_gltf_to_fbx(gltf_path: Path) -> Path:
    """Convert an exported ``.gltf`` (with its sibling ``.bin``) to an animated ``.fbx``
    via headless Blender, writing it alongside the glTF.

    Uses a dedicated Blender script with ``bake_anim=True`` so the animation track is
    carried into the FBX — DCC tools such as Cinema 4D read FBX animation, and the mesh
    exporter's converter deliberately bakes none. Textures are not included; the anim
    glTF carries none.
    """
    blender = Path(BLENDER_PATH)
    if not blender.is_file():
        raise ValueError(
            f"Blender not found at {blender}. Set BLENDER_PATH to your blender.exe to use --fbx."
        )

    fbx_path = gltf_path.with_suffix(".fbx")
    completed = subprocess.run(
        [str(blender), "-b", "--python", str(_GLTF_TO_FBX_SCRIPT),
         "--", str(gltf_path), str(fbx_path)],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or not fbx_path.is_file():
        detail = (completed.stderr or completed.stdout or "blender produced no output").strip()
        raise ValueError(f"Blender gltf->fbx conversion failed:\n{detail}")
    return fbx_path


_GLTF_TO_FBX_BATCH_SCRIPT = Path(__file__).with_name("xi_gltf_to_fbx_batch.py")


def convert_gltf_to_fbx_batch(pairs, progress=None, chunk_size: int = 1000):
    """Convert many ``(gltf_path, fbx_path)`` pairs to FBX through a SINGLE headless
    Blender process per chunk — a fresh ``blender`` per file would take days for a
    full per-track export. Chunking bounds the blast radius of a Blender crash so a
    ``--skip-existing`` re-run resumes cheaply. Returns ``(ok, fail)``.

    ``progress`` (if given) receives each Blender ``[i/N] ok|FAIL`` line.
    """
    pairs = list(pairs)
    if not pairs:
        return 0, 0
    blender = Path(BLENDER_PATH)
    if not blender.is_file():
        raise ValueError(
            f"Blender not found at {blender}. Set BLENDER_PATH to your blender.exe to use --fbx.")

    import tempfile

    ok = fail = 0
    for start in range(0, len(pairs), chunk_size):
        chunk = pairs[start:start + chunk_size]
        manifest = [[str(g), str(f)] for g, f in chunk]
        tf = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8')
        try:
            json.dump(manifest, tf)
            tf.close()
            proc = subprocess.Popen(
                [str(blender), "-b", "--python", str(_GLTF_TO_FBX_BATCH_SCRIPT), "--", tf.name],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in proc.stdout:
                line = line.rstrip()
                if line.startswith('[') and '] ' in line:
                    if progress:
                        progress(line)
                elif line.startswith('BATCH DONE'):
                    try:
                        parts = dict(p.split('=') for p in line.split()[2:])
                        ok += int(parts.get('ok', 0))
                        fail += int(parts.get('fail', 0))
                    except (ValueError, KeyError):
                        pass
            proc.wait()
            if proc.returncode != 0:
                # Blender itself crashed for this chunk — count the rest as failed.
                fail += max(0, len(chunk) - (ok + fail - start))
        finally:
            Path(tf.name).unlink(missing_ok=True)
    return ok, fail


EMOTE_WAIST_OFFSET = 6   # model-viewer: emote part-2 (waist) lives in MotionE + motionEnum(6)


def _resolve_skeleton_and_mesh(data: bytes, sections, skeleton_dat, race, mesh_dats,
                               quiet: bool = False, want_textures: bool = True):
    """Resolve bind-pose joints (+ global transforms) and any mesh primitives for an
    export. Animation-only DATs (no 0x29) use a base race skeleton; mesh comes from
    explicit --mesh DATs, else a mesh in the source/base DAT (or none).

    ``quiet`` suppresses the informational base-skeleton note (bulk export logs per
    DAT itself, so the repeated note is just noise)."""
    skeleton_section = next((s for s in sections if s.type_code == SECTION_TYPE_SKELETON), None)
    mesh_section = next((s for s in sections if s.type_code == SECTION_TYPE_SKELETON_MESH), None)
    if skeleton_section is None:
        from xi.gear.xi_export import race_skeleton_dat
        if skeleton_dat is not None:
            base_path = Path(skeleton_dat)
        elif race:
            base_path = race_skeleton_dat(race)
        else:
            raise ValueError(
                "This DAT has no skeleton of its own and its race couldn't be "
                "detected from its path, so there's no bind pose to rig the clip "
                "onto. Pass --skeleton-dat <DAT> (the entity's own skeleton) or "
                "--race <PCRace>.")
        skel_data = read_path_for(base_path).read_bytes()
        skel_sections = parse_sections(skel_data)
        skeleton_section = next((s for s in skel_sections if s.type_code == SECTION_TYPE_SKELETON), None)
        if skeleton_section is None:
            raise ValueError(f"No skeleton section found in base skeleton DAT: {base_path}")
        base_mesh_section = next((s for s in skel_sections if s.type_code == SECTION_TYPE_SKELETON_MESH), None)
        base_mesh_data = skel_data
        label = skeleton_dat if skeleton_dat is not None else f"{race} ({base_path.name})"
        skeleton_only = not mesh_dats and base_mesh_section is None
        if not quiet:
            print(f"Note: DAT has no skeleton; using base skeleton {label}"
                  + (" — skeleton-only export (no mesh)." if skeleton_only else ""))
    else:
        skel_data = data
        base_mesh_section = mesh_section
        base_mesh_data = data

    joints = parse_skeleton(skel_data, skeleton_section)
    globals_by_joint = compute_global_transforms(joints)

    mesh_sources: List[Tuple[bytes, Section]] = []
    if mesh_dats:
        for mp in mesh_dats:
            md = read_path_for(Path(mp)).read_bytes()
            ms = next((s for s in parse_sections(md) if s.type_code == SECTION_TYPE_SKELETON_MESH), None)
            if ms is None:
                raise ValueError(f"No mesh section found in --mesh DAT: {mp}")
            mesh_sources.append((md, ms))
    elif base_mesh_section is not None:
        mesh_sources.append((base_mesh_data, base_mesh_section))

    primitives: List[Primitive] = []
    vertices: List[VertexSource] = []
    for md, ms in mesh_sources:
        verts, prims = parse_mesh(md, ms)
        offset = len(vertices)
        vertices.extend(verts)
        for prim in prims:
            primitives.append(Primitive(
                material_name=prim.material_name,
                corners=[Corner(vertex_index=c.vertex_index + offset, uv=c.uv,
                                color=c.color, mirrored=c.mirrored) for c in prim.corners],
            ))

    # Textures come from the same DAT(s) the mesh does (the 0x20 sections next to
    # each 0x2A). Skipped for --no-tex and for the bulk export (want_textures=False),
    # which would otherwise spray a PNG for every one of ~180k tracks.
    textures: Dict[str, "TextureImage"] = {}
    if want_textures and mesh_sources:
        from xi.entity.mesh.xi_export import parse_textures
        for md, _ms in mesh_sources:
            for name, image in parse_textures(md, parse_sections(md)).items():
                textures.setdefault(name, image)
    return joints, globals_by_joint, primitives, vertices, textures


def export_dat(dat_path: Path, output_dir: Path, animation_name: str,
               skeleton_dat: Optional[Path] = None, race: str = "HumeFemale",
               mesh_dats: Optional[Sequence[Path]] = None,
               textures: bool = True) -> Tuple[Path, Path]:
    data = read_path_for(dat_path).read_bytes()
    sections = parse_sections(data)
    animation_section = choose_animation(sections, animation_name)
    joints, globals_by_joint, primitives, vertices, tex = _resolve_skeleton_and_mesh(
        data, sections, skeleton_dat, race, mesh_dats, want_textures=textures)
    animation = parse_animation(data, animation_section)
    return build_gltf(dat_path, output_dir, joints, globals_by_joint, primitives, vertices,
                      animation, textures=tex)


def export_merged_emote(dat_path: Path, output_dir: Path, base: str,
                        skeleton_dat: Optional[Path] = None, race: str = "HumeFemale",
                        mesh_dats: Optional[Sequence[Path]] = None,
                        textures: bool = True) -> Tuple[Path, Path]:
    """Export ALL parts of an emote merged onto one skeleton as a single full-body
    glTF (``<stem>_<base>.gltf``). FFXI emotes split by body region: parts 0/1
    (lower/upper) in this DAT and part 2 (waist) in the +6 sibling file. They animate
    disjoint joints, so merging their tracks yields the complete clip to edit; the
    importer splits it back. Returns the merged glTF/bin paths."""
    data = read_path_for(dat_path).read_bytes()
    sections = parse_sections(data)

    # Collect every part clip: this DAT's numbered variants (poi0, poi1) + the waist
    # (poi2) from the +6 sibling file if present.
    part_anims = [(data, choose_animation(sections, nm)) for nm in animation_variants(data, base)]
    try:
        waist_src = Path(dat_path).with_name(f"{int(Path(dat_path).stem) + EMOTE_WAIST_OFFSET}.DAT")
        wdata = read_path_for(waist_src).read_bytes()
        wsecs = parse_sections(wdata)
        part_anims += [(wdata, choose_animation(wsecs, nm)) for nm in animation_variants(wdata, base)]
    except (ValueError, FileNotFoundError, OSError):
        pass
    if not part_anims:
        raise ValueError(f"No '{base}' animation parts found in {Path(dat_path).name}.")

    joints, globals_by_joint, primitives, vertices, tex = _resolve_skeleton_and_mesh(
        data, sections, skeleton_dat, race, mesh_dats, want_textures=textures)

    # Merge the parts' tracks (disjoint joint sets) into one full-body clip. The parts
    # play together so share a length/rate; take the longest and combine the tracks.
    merged_tracks: Dict[int, AnimationTrack] = {}
    num_frames = 0
    keyframe_duration = None
    for d, sec in part_anims:
        a = parse_animation(d, sec)
        merged_tracks.update(a.tracks)
        num_frames = max(num_frames, a.num_frames)
        if keyframe_duration is None:
            keyframe_duration = a.keyframe_duration
    merged = AnimationSection(name=base, num_joints=len(joints), num_frames=num_frames,
                              keyframe_duration=keyframe_duration or 1.0, tracks=merged_tracks)
    parts_desc = ", ".join(sec.name for _, sec in part_anims)
    print(f"Merged {len(part_anims)} emote part(s) into one full-body clip: {parts_desc}")
    return build_gltf(dat_path, output_dir, joints, globals_by_joint, primitives, vertices,
                      merged, textures=tex)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export FFXI DAT mesh, skeleton, and one animation to glTF 2.0.")
    parser.add_argument("dat_path", type=Path, help="Path to the source .DAT file")
    parser.add_argument("--anim", default="idl", help="Animation name, such as idl, wlk, or run")
    parser.add_argument("--output", type=Path, default=None,
                        help="Output directory. Defaults to exports/anim/<rom path>/<stem>_<anim>")
    args = parser.parse_args()

    dat_path = args.dat_path.resolve()
    output_dir = args.output or default_anim_output_dir(dat_path) / f"{dat_path.stem}_{args.anim}"
    gltf_path, bin_path = export_dat(dat_path, output_dir, args.anim)
    print(f"Exported glTF: {gltf_path}")
    print(f"Exported BIN:  {bin_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------

import click as _click  # noqa: E402 — avoid polluting module top


# Basic body mesh DATs per race, by slot (face/head/body/hands/legs/feet). Kept as a
# fallback for a race name passed to --mesh; the default path now resolves model-id
# gear lookups instead (model 0 == this list for HumeFemale).
RACE_MESH_PRESETS = {
    'humefemale': ['ROM/32/63', 'ROM/32/79', 'ROM/32/111', 'ROM/33/28', 'ROM/33/60', 'ROM/33/92'],
}

# The slots a body "look" is composed from, in --mesh id order.
LOOK_SLOTS = ['face', 'head', 'body', 'hands', 'legs', 'feet']

# Race detection from a motion DAT's ROM id. FFXI motion files are race-specific
# (rom/37/13 IS HumeFemale's emote file), so the race is implied by the path — no
# need to pass --race. File numbers below are dir*1000+file, from the FFXI model
# viewer's LoadMotion tables (same order as RACE_SKELETON_DATS).
_MOTION_RACES = ['HumeMale', 'HumeFemale', 'ElvaanMale', 'ElvaanFemale',
                 'TaruMale', 'TaruFemale', 'Mithra', 'Galka']
_BASE_MOTION_FILE = [27082, 32058, 37031, 42004, 46093, 46093, 51089, 56059]  # +0..4 = skeleton+parts
_EMOTE_MOTION_FILE = [32040, 37013, 41114, 46075, 51037, 51071, 56041, 61008]  # +0..11 = emote parts (incl waist)


def detect_race_from_dat(dat_path) -> Optional[str]:
    """Best-effort race for a motion DAT from its ROM id (e.g. ROM/37/13 → HumeFemale).
    Covers emote files (MotionE +0..11, incl. the waist sibling) and base skeleton
    files (+0..4). Returns None if it isn't a recognised race motion file."""
    p = Path(dat_path)
    try:
        enc = int(p.parent.name) * 1000 + int(p.stem)
    except (ValueError, TypeError):
        return None
    for race, start in zip(_MOTION_RACES, _EMOTE_MOTION_FILE):
        if start <= enc <= start + 11:
            return race
    for race, start in zip(_MOTION_RACES, _BASE_MOTION_FILE):
        if start <= enc <= start + 4:
            return race
    return None


def resolve_mesh_look(mesh_value: Optional[str], race: str) -> List[Path]:
    """Turn the --mesh option into a list of body mesh DAT paths to attach.

    * ``None``            → no mesh (skeleton-only export).
    * ``'auto'`` (bare --mesh) → the race's basic naked body: gear model 0 for every
      slot (face/head/body/hands/legs/feet).
    * ``'id,id,…'``       → a "look": gear model ids mapped to those slots in order
      (face, head, body, hands, legs, feet); omitted trailing slots default to 0.
    * anything else       → treated as DAT path(s) / a race-name preset (back-compat).
    """
    from xi.gear.xi_export import resolve_gear_dat
    from xi.entity.mesh.xi_export import resolve_dat_path
    if mesh_value is None:
        return []

    model_ids: Optional[List[int]] = None
    if mesh_value == 'auto':
        model_ids = [0] * len(LOOK_SLOTS)
    else:
        items = [x.strip() for x in str(mesh_value).split(',') if x.strip()]
        if items and all(it.lstrip('-').isdigit() for it in items):
            model_ids = (([int(it) for it in items]) + [0] * len(LOOK_SLOTS))[:len(LOOK_SLOTS)]
        else:
            dats: List[Path] = []
            for it in items:
                preset = RACE_MESH_PRESETS.get(it.lower())
                for one in (preset if preset else [it]):
                    dats.append(resolve_dat_path(one))
            return dats

    dats = []
    for slot, mid in zip(LOOK_SLOTS, model_ids):
        try:
            dats.append(resolve_gear_dat(race, slot, mid))
        except (ValueError, FileNotFoundError) as e:
            _click.echo(f'Note: skipping {slot} (model {mid}) — {e}')
    return dats


def _safe_track_name(raw: str) -> str:
    """Clean a 4-char DAT section name into a filesystem-safe stem."""
    name = raw.strip().strip('\x00').strip()
    return ''.join(c if (c.isalnum() or c in '-_') else '_' for c in name)


def _export_all_races(fbx: bool, output: Optional[Path], race_filter: Optional[set],
                      category_filter: Optional[set], mesh, skip_existing: bool,
                      limit: Optional[int]):
    """Bulk-export every PC animation into a per-track tree, self-contained.

    Layout: ``<base>/<Race>/<category>/rom/<dir>/<file>/<track>.gltf`` (+ .fbx with
    --fbx). The DAT list comes straight from FFXiMain.dll motion tables + FTABLE
    (see :mod:`xi.entity.anim.xi_motion_tables`) — no external lists. The base
    race skeleton (and any --mesh) is resolved once per race and reused across that
    race's animation-only DATs.
    """
    from xi.entity.anim.xi_motion_tables import enumerate_race_animations
    from xi.xi_config import XI_TOOLS_DIR, FFXI_DIR

    base = Path(output) if output else Path(XI_TOOLS_DIR) / 'exports' / 'anim'

    # Per-race cache of (joints, globals, primitives, vertices) for animation-only
    # DATs — they all rig against the same base race skeleton, so resolve it once.
    race_rig: Dict[str, tuple] = {}

    def rig_for(race: str, data: bytes, sections) -> tuple:
        own = any(s.type_code in (SECTION_TYPE_SKELETON, SECTION_TYPE_SKELETON_MESH)
                  for s in sections)
        if own:  # DAT carries its own skeleton/mesh (e.g. the race-config +0 DAT)
            return _resolve_skeleton_and_mesh(
                data, sections, None, race,
                resolve_mesh_look(mesh, race) if mesh else None, quiet=True,
                want_textures=False)
        if race not in race_rig:
            race_rig[race] = _resolve_skeleton_and_mesh(
                data, sections, None, race,
                resolve_mesh_look(mesh, race) if mesh else None, quiet=True,
                want_textures=False)
        return race_rig[race]

    # FBX is baked incrementally as glTFs accumulate (NOT all at the end) so the
    # output tree fills as the run progresses — a full unfiltered run exports tens
    # of thousands of DATs, and deferring every bake to the end means no .fbx for
    # hours. Flush a batch to one Blender process each time the buffer fills.
    FBX_FLUSH = 400
    fbx_jobs: List[tuple] = []
    n_dats = n_tracks = n_fbx_ok = n_fbx_fail = 0

    def flush_fbx(force: bool = False) -> None:
        nonlocal n_fbx_ok, n_fbx_fail
        if not fbx_jobs or (not force and len(fbx_jobs) < FBX_FLUSH):
            return
        _click.echo(f'  baking {len(fbx_jobs)} FBX via Blender ...', err=True)
        ok, fail = convert_gltf_to_fbx_batch(
            fbx_jobs, progress=lambda m: _click.echo('  ' + m, err=True))
        n_fbx_ok += ok
        n_fbx_fail += fail
        fbx_jobs.clear()

    try:
        # progress=None: we log each DAT below instead of a per-race header.
        for race, category, file_id, spec, anims in enumerate_race_animations():
            if race_filter and race not in race_filter:
                continue
            if category_filter and category not in category_filter:
                continue
            if limit is not None and n_dats >= limit:
                break
            n_dats += 1

            rel = spec[:-4] if spec.lower().endswith('.dat') else spec  # ROM/56/59
            out_dir = base / race / category / rel.lower()              # .../rom/56/59
            dat_abs = Path(FFXI_DIR) / spec
            try:
                data = read_path_for(dat_abs).read_bytes()
                sections = parse_sections(data)
            except (OSError, ValueError) as e:
                _click.echo(f'skip {spec}: {e}', err=True)
                continue
            _click.echo(f'[{n_dats}] {spec}  {race}/{category}  '
                        f'{len(anims)} track(s)', err=True)
            try:
                joints, globals_by_joint, primitives, vertices, _tex = rig_for(race, data, sections)
            except (ValueError, FileNotFoundError) as e:
                _click.echo(f'skip {spec}: {e}', err=True)
                continue

            used: set = set()
            for sec in sections:
                if sec.type_code != SECTION_TYPE_SKELETON_ANIMATION:
                    continue
                stem = _safe_track_name(sec.name)
                if not stem:
                    continue
                if stem in used:  # duplicate track name within one DAT folder
                    k = 2
                    while f'{stem}_{k}' in used:
                        k += 1
                    stem = f'{stem}_{k}'
                used.add(stem)
                target = out_dir / f'{stem}.{"fbx" if fbx else "gltf"}'
                if skip_existing and target.exists():
                    continue
                try:
                    animation = parse_animation(data, sec)
                    gltf_path, _bin = build_gltf(dat_abs, out_dir, joints, globals_by_joint,
                                                 primitives, vertices, animation, out_stem=stem)
                except (ValueError, OSError) as e:
                    _click.echo(f'  skip {spec}:{stem}: {e}', err=True)
                    continue
                n_tracks += 1
                if fbx:
                    fbx_jobs.append((gltf_path, gltf_path.with_suffix('.fbx')))
            if fbx:
                flush_fbx()  # bake once enough clips have accumulated
    except FileNotFoundError as e:
        raise _click.ClickException(str(e))

    if fbx:
        flush_fbx(force=True)  # bake whatever is left

    _click.echo(f'\nExported {n_tracks} glTF clip(s) from {n_dats} DAT(s) under {base}')
    if fbx:
        if n_fbx_ok or n_fbx_fail:
            _click.echo(f'FBX: {n_fbx_ok} ok, {n_fbx_fail} failed.')
        else:
            _click.echo('No FBX baked (nothing matched, or all already present).')


@_click.command('export')
@_click.argument('dat_path', required=False, default=None)
@_click.option('--anim',   default='idl', show_default=True,
               help='Animation name (idl, wlk, run, etc.)')
@_click.option('--fbx', is_flag=True, default=False,
               help='Also convert to an animated .fbx via Blender (bakes the motion '
                    'and, unless --no-tex, the textures)')
@_click.option('--no-tex', 'no_tex', is_flag=True, default=False,
               help='Skip decoding the DAT textures. By default the mesh textures are '
                    'decoded to PNGs beside the glTF and wired into its materials '
                    '(like `mesh export`); pass this for a geometry-only export.')
@_click.option('--output', type=_click.Path(path_type=Path), default=None,
               help='Output directory (default: exports/anim/<rom path>/<stem>_<anim>; '
                    'no-DAT bulk mode: exports/anim)')
@_click.option('--race', default=None,
               help='Base race skeleton / mesh for animation-only DATs. Auto-detected '
                    'from the DAT id (rom/37/13 → HumeFemale); pass to override. '
                    'One of HumeMale, HumeFemale, ElvaanMale, ElvaanFemale, '
                    'TaruMale, TaruFemale, Mithra, Galka. In no-DAT bulk mode this '
                    'restricts the export to a single race.')
@_click.option('--category', default=None,
               help='No-DAT bulk mode only: restrict to these motion categories '
                    '(comma-separated): movement, emote, dance, action, fishing, '
                    'battle, dwMain, dwOff, weaponSkill.')
@_click.option('--skip-existing', is_flag=True, default=False,
               help='No-DAT bulk mode: skip clips whose output file already exists '
                    '(resume a long run).')
@_click.option('--limit', type=int, default=None,
               help='No-DAT bulk mode: stop after this many DATs (for testing).')
@_click.option('--skeleton-dat', type=_click.Path(path_type=Path), default=None,
               help='Explicit base skeleton DAT (ROM path or file path). Overrides '
                    '--race when the DAT has no skeleton of its own.')
@_click.option('--mesh', 'mesh', is_flag=False, flag_value='auto', default=None,
               help='Attach a body mesh for visual reference. Bare --mesh = the '
                    'race\'s basic naked body (gear model 0 each slot). '
                    '--mesh ID,ID,ID,ID,ID,ID = a "look" of gear model ids for '
                    'face,head,body,hands,legs,feet (missing slots default to 0). '
                    'Also accepts DAT path(s) or a race name.')
def cmd(dat_path: Optional[str], anim: str, fbx: bool, no_tex: bool, output, race: str, category,
        skip_existing, limit, skeleton_dat, mesh):
    """Export mesh + skeleton + animation from a DAT to glTF 2.0.

    DAT_PATH may be a filesystem path or a ROM-relative spec like ROM/217/32.
    Pass --fbx to also emit an animated .fbx (baked via Blender) for DCC tools.

    Omit DAT_PATH to BULK-export every animation for every PC race into a per-track
    tree — exports/anim/<Race>/<category>/rom/<dir>/<file>/<track>.fbx —
    straight from the game (FFXiMain.dll motion tables + FTABLE), self-contained.
    Scope with --race / --category; --fbx bakes all clips through one batched
    Blender run; --skip-existing resumes. This is large (~180k tracks unfiltered).

    Animation-only DATs (no skeleton of their own) are applied to a base race
    skeleton (--race, default HumeFemale). Add a body for visual reference with
    --mesh: bare --mesh dresses the race in its basic naked body, or
    --mesh 1,2,3,4,5,6 wears a specific gear "look" (face,head,body,hands,legs,feet).

    A digit-less --anim (e.g. 'poi') exports the WHOLE emote merged onto one
    skeleton as a single full-body clip <stem>_poi.gltf (all parts incl. the waist
    from the +6 sibling file); pass a numbered name (poi1) for just one part.
    """
    if dat_path is None:
        from xi.entity.anim.xi_motion_tables import RACE_NAMES, MOTION_CATEGORY_HINTS
        race_filter = None
        if race:
            if race not in RACE_NAMES:
                raise _click.ClickException(
                    f"--race must be one of {', '.join(RACE_NAMES)}.")
            race_filter = {race}
        category_filter = None
        if category:
            cats = {c.strip() for c in category.split(',') if c.strip()}
            unknown = cats - set(MOTION_CATEGORY_HINTS)
            if unknown:
                raise _click.ClickException(
                    f"unknown --category {', '.join(sorted(unknown))}; "
                    f"choose from {', '.join(MOTION_CATEGORY_HINTS)}.")
            category_filter = cats
        _export_all_races(fbx, output, race_filter, category_filter, mesh,
                          skip_existing, limit)
        return

    from xi.entity.mesh.xi_export import resolve_dat_path
    try:
        dat = resolve_dat_path(dat_path)
        skel = resolve_dat_path(str(skeleton_dat)) if skeleton_dat is not None else None
        if race is None:
            race = detect_race_from_dat(dat)
            if race:
                _click.echo(f'Detected race: {race}')
            # No race detected → leave it unset. A DAT with its own skeleton
            # (monster/NPC/object) rigs against that and never needs a race; an
            # animation-only DAT will ask for --race / --skeleton-dat below when
            # it can't find a bind pose. We must NOT silently rig a monster onto
            # the HumeFemale skeleton.
        mesh_dats = resolve_mesh_look(mesh, race)
    except FileNotFoundError as e:
        raise _click.ClickException(str(e))

    parent = Path(output) if output else default_anim_output_dir(dat) / f'{dat.stem}_{anim}'

    def finish(gltf_path: Path, bin_path: Path) -> None:
        _click.echo(f'Exported glTF: {gltf_path}')
        _click.echo(f'Exported BIN:  {bin_path}')
        if fbx:
            try:
                _click.echo(f'Exported FBX:  {convert_gltf_to_fbx(gltf_path)}')
            except ValueError as e:
                raise _click.ClickException(str(e))

    # A digit-less name (poi) exports the WHOLE emote merged onto one skeleton as a
    # single full-body clip (<stem>_<anim>.gltf) — all parts incl. the waist from the
    # +6 sibling file — so you animate the entire body in one file and the importer
    # splits it back. A numbered name (poi1) exports just that one clip.
    expand = not anim[-1:].isdigit()
    try:
        if expand:
            gltf_path, bin_path = export_merged_emote(dat, parent, anim, skeleton_dat=skel,
                                                      race=race, mesh_dats=mesh_dats,
                                                      textures=not no_tex)
        else:
            gltf_path, bin_path = export_dat(dat, parent, anim, skeleton_dat=skel,
                                             race=race, mesh_dats=mesh_dats,
                                             textures=not no_tex)
    except (ValueError, FileNotFoundError) as e:
        raise _click.ClickException(str(e))
    finish(gltf_path, bin_path)


def _rom_spec_for(dat: Path) -> str:
    """``ROM/<dir>/<file>.DAT`` for a DAT under FFXI_DIR, else its filename —
    used as the per-DAT key in ``--compact`` output."""
    from xi.xi_config import FFXI_DIR
    try:
        rel = dat.resolve().relative_to(Path(FFXI_DIR).resolve())
        return rel.as_posix()
    except ValueError:
        return dat.name


def _list_all_races(as_json: bool, output_dir: Optional[Path], compact: bool = False):
    """List every animation for every PC race — fully self-contained.

    Reads each race's per-category base file_ids from FFXiMain.dll (the game's
    own lookup tables, located by byte-pattern hint) and resolves the motion
    DATs through FTABLE/VTABLE, exactly as the client does. No external files.
    See :mod:`xi.entity.anim.xi_motion_tables`.

    ``compact`` (implies JSON) groups tracks by their DAT instead of repeating
    ``dat``/``file_id``/``category`` on every track:
    ``{"ROM/27/82.DAT": {"file_id": .., "category": .., "tracks": [..]}}``.
    """
    from xi.entity.anim.xi_motion_tables import (
        enumerate_race_animations, RACE_NAMES)

    flat: Dict[str, List[Dict[str, object]]] = {r: [] for r in RACE_NAMES}
    grouped: Dict[str, Dict[str, Dict[str, object]]] = {r: {} for r in RACE_NAMES}
    try:
        for race, category, file_id, spec, anims in enumerate_race_animations(
                progress=lambda m: _click.echo(m, err=True)):
            if compact:
                entry = grouped[race].get(spec)
                if entry is None:
                    entry = {'file_id': file_id, 'category': category, 'tracks': []}
                    grouped[race][spec] = entry
                entry['tracks'].extend(anims)
            else:
                for a in anims:
                    flat[race].append(
                        dict(a, category=category, dat=spec, file_id=file_id))
    except FileNotFoundError as e:
        raise _click.ClickException(str(e))

    results = grouped if compact else flat
    # Drop races that yielded nothing (e.g. partial install) for a clean report.
    results = {r: v for r, v in results.items() if v}

    if as_json or compact:
        from xi.xi_config import XI_TOOLS_DIR
        dest = output_dir if output_dir is not None \
            else Path(XI_TOOLS_DIR) / 'exports' / 'anim'
        dest.mkdir(parents=True, exist_ok=True)
        for race, data in results.items():
            out = dest / f"{race}Animations.json"
            out.write_text(json.dumps(data, indent=2), encoding='utf-8')
            _click.echo(str(out.resolve()))
        return

    for race, anims in results.items():
        _click.echo(f'\n{race} — {len(anims)} animation track(s)')
        _click.echo(f'  {"name":<6}  {"frames":>6}  {"joints":>6}  {"~sec":>6}'
                    f'  {"category":<11}  dat')
        _click.echo('  ' + '-' * 60)
        for a in anims:
            _click.echo(f'  {a["name"]:<6}  {a["frames"]:>6}  {a["joints"]:>6}'
                        f'  {a["seconds"]:>6.2f}  {a["category"]:<11}  {a["dat"]}')


@_click.command('list')
@_click.argument('dat_path', required=False, default=None)
@_click.option('--json', 'as_json', is_flag=True,
               help='Emit the animation list as JSON. Without DAT_PATH, writes one '
                    'file per race (e.g. HumeFemaleAnimations.json) to --output-dir '
                    '(default: exports/anim). With a DAT_PATH, dumps that '
                    "DAT's tracks to stdout (or --output-dir).")
@_click.option('--output-dir', type=_click.Path(path_type=Path), default=None,
               help='Directory to write per-race JSON files when --json is used without '
                    'a DAT_PATH. Defaults to exports/anim.')
@_click.option('--compact', is_flag=True,
               help='Group tracks by their DAT (implies --json): '
                    '{"ROM/27/82.DAT": {"file_id":.., "category":.., "tracks":[..]}} '
                    "instead of repeating dat/file_id/category on every track.")
def list_cmd(dat_path: Optional[str], as_json: bool, output_dir: Optional[Path],
             compact: bool):
    """List every animation track contained in a DAT.

    DAT_PATH may be a filesystem path or a ROM-relative spec like ROM/5/3.
    Each track name (idl, wlk, run, ...) is what you pass to `anim export --anim`.

    Omit DAT_PATH to enumerate every animation for every PC race straight from
    the game (FFXiMain.dll motion tables + FTABLE) — self-contained, no external
    lists. Print a combined table, or write per-race JSON files with --json
    (add --compact to group tracks by DAT).
    """
    if dat_path is None:
        _list_all_races(as_json, output_dir, compact=compact)
        return

    from xi.entity.mesh.xi_export import resolve_dat_path
    try:
        dat = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise _click.ClickException(str(e))

    animations = list_animations(read_path_for(dat).read_bytes())

    if as_json or compact:
        spec = _rom_spec_for(dat)
        payload = {spec: {'tracks': animations}} if compact else animations
        out_text = json.dumps(payload, indent=2)
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            out = output_dir / f"{dat.stem}Animations.json"
            out.write_text(out_text, encoding='utf-8')
            _click.echo(f'Wrote {out}')
        else:
            _click.echo(out_text)
        return

    if not animations:
        _click.echo(f'{dat.name}: no animation tracks found '
                    f'(not an animated entity DAT, or no 0x2B sections).')
        return

    _click.echo(f'{dat.name} — {len(animations)} animation track(s)\n')
    _click.echo(f'  {"name":<6}  {"frames":>6}  {"joints":>6}  {"~sec":>6}')
    _click.echo('  ' + '-' * 30)
    for a in animations:
        _click.echo(f'  {a["name"]:<6}  {a["frames"]:>6}  {a["joints"]:>6}  {a["seconds"]:>6.2f}')

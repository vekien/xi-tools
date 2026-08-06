#!/usr/bin/env python3

import argparse
import base64
import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import math

from xi.common.xi_section import encode_section_meta
from xi.xi_config import editable_dat, read_path_for
from xi.entity.anim.xi_export import Joint, Reader, Section, GAME_FPS, parse_sections, parse_skeleton, parse_animation, sample_track, quat_conjugate, quat_mul, quat_nlerp, quat_normalize


SECTION_TYPE_SKELETON_ANIMATION = 0x2B
EPSILON = 1e-5


@dataclass
class AnimationEntryTemplate:
    joint_index: int
    rot_offsets: Tuple[int, int, int, int]
    rot_consts: Tuple[float, float, float, float]
    trans_offsets: Tuple[int, int, int]
    trans_consts: Tuple[float, float, float]
    scale_offsets: Tuple[int, int, int]
    scale_consts: Tuple[float, float, float]


@dataclass
class AnimationTemplate:
    section: Section
    unk0: int
    num_frames: int
    keyframe_duration: float
    entries: List[AnimationEntryTemplate]


@dataclass
class AccessorData:
    values: List[Tuple[float, ...]]


@dataclass
class ChannelTrack:
    times: List[float]
    values: List[Tuple[float, ...]]
    interpolation: str


@dataclass
class NodeAnimation:
    translation: Optional[ChannelTrack]
    rotation: Optional[ChannelTrack]
    scale: Optional[ChannelTrack]


@dataclass
class GltfAnimation:
    name: str
    sample_times: List[float]
    joints: Dict[int, NodeAnimation]
    node_defaults: Dict[int, Tuple[Tuple[float, float, float], Tuple[float, float, float, float], Tuple[float, float, float]]]
    # Per-joint REST translation as it appears in THIS glTF (node TRS). The DAT delta
    # is local-minus-rest; using the glTF's own rest (not the skeleton bind) makes the
    # round-trip robust to DCC tools that re-anchor bones — Blender bakes a bone's
    # bind offset into the armature rest and exports the node translation as ~0, so
    # subtracting the skeleton bind would invent a bogus delta (e.g. the pelvis sinks).
    joint_rest_translation: Dict[int, Tuple[float, float, float]]


def normalize_anim_name(name: str) -> str:
    return name.lower().rstrip("\x00")


def normalize_anim_target(name: str) -> str:
    """A 4-char DAT animation name. A trailing digit is the slot, so a name with
    no number defaults to 0: idl -> idl0, idl5 -> idl5, idl0 -> idl0."""
    name = name.strip()
    if name and not name[-1].isdigit():
        name = name + "0"
    return name[:4]


def normalize_section_name(name: str) -> str:
    return normalize_anim_name(name).rstrip("0")


def choose_section_by_name(sections: Sequence[Section], anim_name: str) -> Optional[Section]:
    wanted = normalize_section_name(anim_name)
    for section in sections:
        if section.type_code != SECTION_TYPE_SKELETON_ANIMATION:
            continue
        if normalize_section_name(section.name) == wanted:
            return section
    return None


def parse_animation_template(data: bytes, section: Section) -> AnimationTemplate:
    reader = Reader(data, section.data_start)
    unk0 = reader.u16()
    num_joints = reader.u16()
    num_frames = reader.u16()
    keyframe_duration = reader.f32()

    entries: List[AnimationEntryTemplate] = []
    for _ in range(num_joints):
        joint_index = reader.i32()
        rot_offsets = tuple(reader.i32() for _ in range(4))
        rot_consts = tuple(reader.f32() for _ in range(4))
        trans_offsets = tuple(reader.i32() for _ in range(3))
        trans_consts = tuple(reader.f32() for _ in range(3))
        scale_offsets = tuple(reader.i32() for _ in range(3))
        scale_consts = tuple(reader.f32() for _ in range(3))
        entries.append(
            AnimationEntryTemplate(
                joint_index=joint_index,
                rot_offsets=rot_offsets,
                rot_consts=rot_consts,
                trans_offsets=trans_offsets,
                trans_consts=trans_consts,
                scale_offsets=scale_offsets,
                scale_consts=scale_consts,
            )
        )

    return AnimationTemplate(section=section, unk0=unk0, num_frames=num_frames, keyframe_duration=keyframe_duration, entries=entries)


def load_gltf_document(gltf_path: Path) -> Tuple[dict, List[bytes]]:
    doc = json.loads(gltf_path.read_text(encoding="utf-8"))
    buffers: List[bytes] = []

    for buffer_info in doc.get("buffers", []):
        uri = buffer_info.get("uri", "")
        if uri.startswith("data:"):
            payload = uri.split(",", 1)[1]
            buffers.append(base64.b64decode(payload))
        else:
            buffers.append((gltf_path.parent / uri).read_bytes())

    return doc, buffers


def get_num_components(type_name: str) -> int:
    return {
        "SCALAR": 1,
        "VEC2": 2,
        "VEC3": 3,
        "VEC4": 4,
    }[type_name]


def read_accessor(doc: dict, buffers: Sequence[bytes], accessor_index: int) -> AccessorData:
    accessor = doc["accessors"][accessor_index]
    buffer_view = doc["bufferViews"][accessor["bufferView"]]
    component_type = accessor["componentType"]
    if component_type != 5126:
        raise ValueError(f"Unsupported glTF accessor component type: {component_type}")

    type_name = accessor["type"]
    components = get_num_components(type_name)
    count = accessor["count"]
    view_offset = buffer_view.get("byteOffset", 0)
    accessor_offset = accessor.get("byteOffset", 0)
    stride = buffer_view.get("byteStride", components * 4)
    start = view_offset + accessor_offset
    raw = buffers[buffer_view["buffer"]]

    values: List[Tuple[float, ...]] = []
    for index in range(count):
        offset = start + index * stride
        values.append(struct.unpack_from("<" + "f" * components, raw, offset))
    return AccessorData(values=values)


def _bone_node_index(node_name: str) -> Optional[int]:
    """Joint index for a glTF node named exactly ``bone<digits>`` (xi exports
    ``bone0007``), else ``None``. Skips non-bone nodes AND the leaf-tip helper bones
    a DCC tool adds — Blender appends a ``bone0007_end`` tail bone per leaf, which
    starts with 'bone' but has no joint index of its own."""
    match = re.fullmatch(r"bone(\d+)", node_name)
    return int(match.group(1)) if match else None


def choose_gltf_animation(doc: dict, buffers: Sequence[bytes], preferred_name: str) -> GltfAnimation:
    animations = doc.get("animations", [])
    if not animations:
        raise ValueError("glTF does not contain any animations")

    preferred = normalize_anim_name(preferred_name)
    animation_index = 0
    for index, animation in enumerate(animations):
        if normalize_anim_name(animation.get("name", "")) == preferred:
            animation_index = index
            break

    animation = animations[animation_index]
    nodes = doc.get("nodes", [])
    node_defaults: Dict[int, Tuple[Tuple[float, float, float], Tuple[float, float, float, float], Tuple[float, float, float]]] = {}
    for node_index, node in enumerate(nodes):
        translation = tuple(node.get("translation", [0.0, 0.0, 0.0]))
        rotation = tuple(node.get("rotation", [0.0, 0.0, 0.0, 1.0]))
        scale = tuple(node.get("scale", [1.0, 1.0, 1.0]))
        node_defaults[node_index] = (translation, rotation, scale)

    # Rest translation of every bone node, keyed by joint index — the per-clip
    # anchor for delta conversion (see GltfAnimation.joint_rest_translation).
    joint_rest_translation: Dict[int, Tuple[float, float, float]] = {}
    for node_index, node in enumerate(nodes):
        j = _bone_node_index(node.get("name", ""))
        if j is not None:
            joint_rest_translation[j] = node_defaults[node_index][0]

    channels_by_joint: Dict[int, Dict[str, ChannelTrack]] = {}
    sample_times: List[float] = []

    for channel in animation.get("channels", []):
        target = channel["target"]
        node_index = target["node"]
        joint_index = _bone_node_index(nodes[node_index].get("name", ""))
        if joint_index is None:
            continue
        sampler = animation["samplers"][channel["sampler"]]
        input_accessor = read_accessor(doc, buffers, sampler["input"]).values
        output_accessor = read_accessor(doc, buffers, sampler["output"]).values
        times = [value[0] for value in input_accessor]
        values = [tuple(value) for value in output_accessor]

        if len(times) > len(sample_times):
            sample_times = times

        channels_by_joint.setdefault(joint_index, {})[target["path"]] = ChannelTrack(
            times=times,
            values=values,
            interpolation=sampler.get("interpolation", "LINEAR"),
        )

    joints: Dict[int, NodeAnimation] = {}
    for joint_index, channel_map in channels_by_joint.items():
        joints[joint_index] = NodeAnimation(
            translation=channel_map.get("translation"),
            rotation=channel_map.get("rotation"),
            scale=channel_map.get("scale"),
        )

    return GltfAnimation(
        name=animation.get("name", preferred_name),
        sample_times=sample_times,
        joints=joints,
        node_defaults=node_defaults,
        joint_rest_translation=joint_rest_translation,
    )


def sample_vec_track(track: ChannelTrack, time_value: float) -> Tuple[float, ...]:
    if not track.times:
        raise ValueError("Track is missing time samples")
    if time_value <= track.times[0]:
        return track.values[0]
    if time_value >= track.times[-1]:
        return track.values[-1]

    for index in range(len(track.times) - 1):
        start = track.times[index]
        end = track.times[index + 1]
        if start <= time_value <= end:
            if abs(end - start) <= EPSILON:
                return track.values[index]
            delta = (time_value - start) / (end - start)
            if len(track.values[index]) == 4:
                return quat_nlerp(track.values[index], track.values[index + 1], delta)
            return tuple(track.values[index][axis] + delta * (track.values[index + 1][axis] - track.values[index][axis]) for axis in range(len(track.values[index])))
    return track.values[-1]


def almost_constant(values: Sequence[float]) -> bool:
    return all(abs(value - values[0]) <= EPSILON for value in values[1:])


def encode_name(name: str) -> bytes:
    encoded = name.encode("ascii")
    if len(encoded) > 4:
        raise ValueError("Animation names must be 4 ASCII characters")
    return encoded.ljust(4, b"\x00")


def _sample_template(values: Sequence[Tuple[float, ...]], u: float) -> Tuple[float, ...]:
    """Linear-sample a template track's per-frame values at normalized time u∈[0,1]."""
    if len(values) == 1:
        return values[0]
    pos = max(0.0, min(1.0, u)) * (len(values) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(values) - 1)
    frac = pos - lo
    return tuple(values[lo][k] * (1.0 - frac) + values[hi][k] * frac for k in range(len(values[0])))


def build_animation_block(name: str, template: AnimationTemplate, template_anim: "AnimationSection",
                          joints: Sequence[Joint], gltf_animation: GltfAnimation,
                          fps: Optional[float] = None, static_base: bool = False,
                          trans_joints: Optional[Sequence[int]] = None) -> bytes:
    if not gltf_animation.sample_times:
        raise ValueError("glTF animation has no sample times")

    # Resample uniformly over the clip's real time span, rather than copying however
    # many discrete samples the DCC tool happened to write (exporters keyframe-reduce
    # or bake at an arbitrary fps, so the glTF sample COUNT is not the frame count).
    #
    # By default sample at the clip's NATIVE keyframe rate and keep its keyFrameDuration:
    # FFXI clips store keyframes at `kdur · 30` fps (emotes kdur=0.5 → 15 fps) and the
    # game interpolates between them at its 30 fps tick. `fps` overrides the sampling
    # density (more keyframes = smoother in-game interpolation); kdur is set to fps/30
    # so the on-disk duration (numFrames-1)/kdur/30 is preserved regardless. fps=30 →
    # kdur 1.0 (a keyframe per game tick); fps=60 → kdur 2.0 (denser-than-tick, useful
    # for 60 fps clients). Stock clips are all kdur < 1, so non-native fps is non-stock.
    src_times = gltf_animation.sample_times
    duration = max(0.0, src_times[-1] - src_times[0])
    if fps and fps > 0:
        keyframe_duration = fps / GAME_FPS
        native_fps = fps
    else:
        keyframe_duration = template.keyframe_duration if template.keyframe_duration and template.keyframe_duration > 0.0 else 0.5
        native_fps = keyframe_duration * GAME_FPS
    num_frames = max(2, int(round(duration * native_fps)) + 1)
    output_times = [src_times[0] + index / native_fps for index in range(num_frames)]
    float_cursor = len(template.entries) * (84 // 4)
    sequence_blob = bytearray()
    entry_blob = bytearray()

    for entry in template.entries:
        entry_blob.extend(struct.pack("<i", entry.joint_index))

        bind_joint = joints[entry.joint_index]
        node_anim = gltf_animation.joints.get(entry.joint_index)
        local_rotation_default = bind_joint.rotation
        # TRANSLATION + SCALE are RIG STRUCTURE, preserved from the original clip — not
        # read from the glTF. FFXI animation is rotation-based: a bone's local
        # translation is a fixed/animated offset from its parent (weapon attachment,
        # pelvis height, finger spread). A bone follows its parent's ROTATION, so
        # keeping the original local offset keeps the weapon attached while the new
        # pose drives it. DCC tools (Blender) also mangle bone translations — baking
        # bind offsets into the armature rest — so trusting the glTF translation throws
        # the weapon to the floor / sinks the pelvis. We resample the template's own
        # translation/scale onto the new timeline and take only ROTATION from the glTF.
        #
        # `trans_joints` opts specific joints back IN to glTF translation (e.g. the
        # pelvis drop of a sit/lie pose, which is authored as a root translation and
        # would otherwise be silently replaced by the template's standing height).
        # The glTF stores absolute local translation (bind + delta, see xi_export),
        # so subtract the bind offset to recover the DAT-space delta.
        tmpl_track = template_anim.tracks.get(entry.joint_index)
        take_gltf_trans = (trans_joints is not None and entry.joint_index in trans_joints
                           and node_anim is not None and node_anim.translation)

        translations: List[Tuple[float, float, float]] = []
        rotations: List[Tuple[float, float, float, float]] = []
        scales: List[Tuple[float, float, float]] = []

        for sample_index, time_value in enumerate(output_times):
            if node_anim and node_anim.rotation:
                local_rotation = sample_vec_track(node_anim.rotation, time_value)
            else:
                local_rotation = local_rotation_default

            u = sample_index / (num_frames - 1) if num_frames > 1 else 0.0
            if take_gltf_trans:
                tv = sample_vec_track(node_anim.translation, time_value)
                translation_delta = tuple(tv[k] - bind_joint.translation[k] for k in range(3))
                if tmpl_track:
                    scale_value = _sample_template(tmpl_track.scales, 0.0 if static_base else u)
                else:
                    scale_value = (1.0, 1.0, 1.0)
            elif tmpl_track:
                # static_base: hold the template's translation/scale at frame 0 (constant)
                # rather than resampling it per-frame. Keeps each bone's rig offset but
                # drops the base clip's own translation/scale ANIMATION (e.g. an idle bob),
                # so bones the glTF doesn't drive stay perfectly still — only glTF rotation
                # animates. Without it, the base's bob rides along on those bones.
                tu = 0.0 if static_base else u
                translation_delta = _sample_template(tmpl_track.translations, tu)
                scale_value = _sample_template(tmpl_track.scales, tu)
            else:
                translation_delta = (0.0, 0.0, 0.0)
                scale_value = (1.0, 1.0, 1.0)

            rotation_delta = quat_normalize(quat_mul(local_rotation, quat_conjugate(bind_joint.rotation)))

            translations.append(translation_delta)
            rotations.append(rotation_delta)
            scales.append(scale_value)

        def write_group(source_values: Sequence[Tuple[float, ...]]) -> Tuple[List[int], List[float]]:
            nonlocal float_cursor
            component_count = len(source_values[0])
            offsets: List[int] = []
            consts: List[float] = []
            for component_index in range(component_count):
                channel_values = [frame[component_index] for frame in source_values]
                if almost_constant(channel_values):
                    offsets.append(0)
                    consts.append(channel_values[0])
                else:
                    offsets.append(float_cursor)
                    consts.append(channel_values[0])
                    sequence_blob.extend(struct.pack("<" + "f" * len(channel_values), *channel_values))
                    float_cursor += len(channel_values)
            return offsets, consts

        rot_offsets, rot_consts = write_group(rotations)
        trans_offsets, trans_consts = write_group(translations)
        scale_offsets, scale_consts = write_group(scales)

        entry_blob.extend(struct.pack("<4i", *rot_offsets))
        entry_blob.extend(struct.pack("<4f", *rot_consts))
        entry_blob.extend(struct.pack("<3i", *trans_offsets))
        entry_blob.extend(struct.pack("<3f", *trans_consts))
        entry_blob.extend(struct.pack("<3i", *scale_offsets))
        entry_blob.extend(struct.pack("<3f", *scale_consts))

    body = bytearray()
    body.extend(struct.pack("<H", template.unk0))
    body.extend(struct.pack("<H", len(template.entries)))
    body.extend(struct.pack("<H", num_frames))
    body.extend(struct.pack("<f", keyframe_duration))
    body.extend(entry_blob)
    body.extend(sequence_blob)

    total_size = 16 + len(body)
    padded_size = (total_size + 15) & ~15
    section_meta = encode_section_meta(padded_size, SECTION_TYPE_SKELETON_ANIMATION,
                                       what="skeleton-animation section")

    section = bytearray()
    section.extend(encode_name(name))
    section.extend(struct.pack("<I", section_meta))
    section.extend(b"\x00" * 8)
    section.extend(body)
    section.extend(b"\x00" * (padded_size - len(section)))
    return bytes(section), num_frames, keyframe_duration


def _clip_base(name: str) -> str:
    """Strip a clip tag down to its base: 'poi1'->'poi', 'poi?'->'poi', 'poi'->'poi'.
    Emote routines reference clips by a wildcard tag ('poi?') that the client
    resolves to a slot digit; we match a concrete clip name to that base."""
    return name.rstrip("?").rstrip("0123456789")


def fit_routine_duration(data: bytes, sections: Sequence[Section], clip_name: str,
                         length_frames: float) -> Tuple[bytes, Optional[dict]]:
    """Grow the 0x07 EffectRoutine ``dur`` that plays ``clip_name`` to fit a clip of
    ``length_frames`` game-frames, in place (u16, no size change).

    The emote's in-game playback window is the routine's ``dur`` (sec2 op 0x05),
    NOT the 0x2B frame count: the client scales the clip to ``dur/(2·rate)`` frames,
    so a clip plays at natural speed when ``dur == 2 × length``. A longer edited
    clip with the original ``dur`` gets cut off. We grow ``dur`` (never shrink — a
    routine's clip tag is a wildcard shared by slot siblings like poi0/poi1, so the
    window must fit the LONGEST sibling) to ``round(2 × length)``.

    Returns ``(data, info)`` where info is ``{routine, clip, old, new}`` or None if
    no matching routine was found or it already fits."""
    target_base = _clip_base(clip_name)
    want = int(round(2.0 * length_frames))
    buf = bytearray(data)
    for s in sections:
        if s.type_code != 0x07:
            continue
        body_start = s.start + 16
        body = data[body_start: s.start + s.size]
        if len(body) < 0x14:
            continue
        try:
            _s1, sec2, _s3, _tot = struct.unpack_from("<4I", body, 0x10)
        except struct.error:
            continue
        p, guard = sec2 - 16, 0
        while 0 <= p + 12 <= len(body) and guard < 128:
            guard += 1
            op = body[p]
            n = struct.unpack_from("<H", body, p + 1)[0] & 0x1F
            entry_len = max(1, n) * 4
            if op == 0x05:
                ref = body[p + 8:p + 12]
                ref_s = ref.decode("ascii", "replace") if all(0x20 <= c < 0x7F for c in ref) else ""
                if _clip_base(ref_s) == target_base:
                    cur = struct.unpack_from("<H", body, p + 6)[0]
                    new = min(0xFFFF, max(cur, want))
                    if new != cur:
                        struct.pack_into("<H", buf, body_start + p + 6, new)
                    return bytes(buf), {"routine": s.name.rstrip("\x00 "),
                                        "clip": ref_s, "old": cur, "new": new}
            if op == 0x00:
                break
            p += entry_len
    return data, None


def variant_slot_names(sections: Sequence[Section], base: str) -> list:
    """All 0x2B clip names that are ``base`` or ``base``+digits, in DAT order.
    A FFXI emote splits across slots (poi0 = lower body, poi1 = upper body) that
    play overlaid, so a digit-less 'poi' must write BOTH to stay in sync."""
    base = normalize_anim_name(base)
    names = []
    for s in sections:
        if s.type_code != SECTION_TYPE_SKELETON_ANIMATION:
            continue
        nm = normalize_anim_name(s.name)
        if nm == base or (nm.startswith(base) and nm[len(base):].isdigit()):
            names.append(s.name.rstrip("\x00 "))
    return names


def rebuild_dat_multi(data: bytes, sections: Sequence[Section], replacements: Dict[str, bytes]) -> bytes:
    """Rebuild the DAT writing several named 0x2B sections in a single pass (so multiple
    slots can be written without re-seeding from .base between each). ``replacements``
    maps a target name to its new section bytes. A name that already exists is replaced
    in place; a NEW name is appended after the last animation section (sections are
    position-independent — the client just walks them — so a fresh track can be added)."""
    by_start = {}
    to_insert: List[bytes] = []
    for name, repl in replacements.items():
        existing = choose_section_by_name(sections, name)
        if existing is None:
            to_insert.append(repl)      # brand-new track — append below
        else:
            by_start[existing.start] = repl
    anim_indices = [i for i, s in enumerate(sections)
                    if s.type_code == SECTION_TYPE_SKELETON_ANIMATION]
    insert_after = anim_indices[-1] if anim_indices else len(sections) - 1
    output = bytearray()
    for index, section in enumerate(sections):
        output.extend(by_start.get(section.start, data[section.start: section.start + section.size]))
        if index == insert_after:
            for repl in to_insert:
                output.extend(repl)
    if not sections:                     # pathological: empty DAT
        for repl in to_insert:
            output.extend(repl)
    return bytes(output)


def rebuild_dat(data: bytes, sections: Sequence[Section], replacement_section: bytes, target_name: str) -> bytes:
    target_existing = choose_section_by_name(sections, target_name)
    output = bytearray()
    inserted = False

    if target_existing is not None:
        for section in sections:
            if section.start == target_existing.start:
                output.extend(replacement_section)
            else:
                output.extend(data[section.start : section.start + section.size])
        return bytes(output)

    last_animation_index = max(index for index, section in enumerate(sections) if section.type_code == SECTION_TYPE_SKELETON_ANIMATION)
    for index, section in enumerate(sections):
        output.extend(data[section.start : section.start + section.size])
        if index == last_animation_index:
            output.extend(replacement_section)
            inserted = True

    if not inserted:
        output.extend(replacement_section)

    return bytes(output)


EMOTE_WAIST_OFFSET = 6   # model-viewer: emote part-2 (waist) lives in MotionE + motionEnum(6)


def _resolve_bind_joints(dat_path: Path, sections, data: bytes, skeleton_dat, race):
    """Bind-pose joints for delta conversion: the DAT's own 0x29 skeleton if it has
    one, else a base race skeleton (animation-only DATs were exported against it)."""
    skeleton_section = next((s for s in sections if s.type_code == 0x29), None)
    if skeleton_section is not None:
        return parse_skeleton(data, skeleton_section)
    from xi.gear.xi_export import race_skeleton_dat
    if skeleton_dat is not None:
        base_path = Path(skeleton_dat)
    elif race:
        base_path = race_skeleton_dat(race)
    else:
        raise ValueError(
            "This DAT has no skeleton of its own and its race couldn't be "
            "detected, so there's no bind pose for delta conversion. Pass "
            "--skeleton-dat <DAT> (the entity's own skeleton) or --race <PCRace>.")
    skel_data = read_path_for(base_path).read_bytes()
    base_skel = next((s for s in parse_sections(skel_data) if s.type_code == 0x29), None)
    if base_skel is None:
        raise ValueError(f"No skeleton section found in base skeleton DAT: {base_path}")
    label = skeleton_dat if skeleton_dat is not None else f"{race} ({base_path.name})"
    print(f"Note: DAT has no skeleton; using base skeleton {label} bind pose for delta conversion.")
    return parse_skeleton(skel_data, base_skel)


def _write_part_file(dat_src: Path, slot_to_gltf: Dict[str, Path], joints, template_anim_name: str,
                     gltf_cache: Dict[Path, Tuple[dict, list]], no_base: bool, fit_routine: bool,
                     fps: Optional[float] = None, static_base: bool = False,
                     trans_joints: Optional[Sequence[int]] = None):
    """Write one DAT's slot clips from their glTF source(s), grow its emote routine
    if it has one, and rebuild in a single session. Returns (path, written, gltf_name)."""
    target = editable_dat(dat_src, fresh=not no_base)
    data = target.read_bytes()
    sections = parse_sections(data)
    replacements: Dict[str, bytes] = {}
    written = []
    max_length = 0.0
    last_name = None
    for name, gpath in slot_to_gltf.items():
        if gpath is None:
            raise ValueError(f"No glTF supplied for slot '{name}'")
        gpath = Path(gpath)
        if gpath not in gltf_cache:
            gltf_cache[gpath] = load_gltf_document(gpath)
        gltf_doc, gltf_buffers = gltf_cache[gpath]
        gltf_animation = choose_gltf_animation(gltf_doc, gltf_buffers, name)
        if not gltf_animation.sample_times:
            raise ValueError(
                f"'{gpath.name}' has no bone animation keyframes. Keyframe the bones in "
                f"your DCC tool and re-export, then import.")
        last_name = gltf_animation.name
        tmpl_sec = choose_section_by_name(sections, name) or choose_section_by_name(sections, template_anim_name)
        if tmpl_sec is None:
            raise ValueError(f"Could not find target animation '{name}' or template animation '{template_anim_name}'")
        template = parse_animation_template(data, tmpl_sec)
        template_anim = parse_animation(data, tmpl_sec)   # decoded rig translations/scales to preserve
        block, num_frames, keyframe_duration = build_animation_block(
            name, template, template_anim, joints, gltf_animation, fps=fps, static_base=static_base,
            trans_joints=trans_joints)
        replacements[name] = block
        written.append((name, num_frames))
        max_length = max(max_length, (num_frames - 1) / keyframe_duration if keyframe_duration else num_frames - 1)

    if fit_routine:
        data, info = fit_routine_duration(data, sections, list(slot_to_gltf)[0], max_length)
        if info and info["new"] != info["old"]:
            print(f"Note: grew routine '{info['routine']}' (plays '{info['clip']}') "
                  f"dur {info['old']} -> {info['new']} to fit {int(max_length) + 1} frames.")

    target.write_bytes(rebuild_dat_multi(data, sections, replacements))
    return target, written, last_name


def import_animation(dat_path: Path, gltf_path: Optional[Path], target_anim_name: str, template_anim_name: str,
                     no_base: bool = False, skeleton_dat: Optional[Path] = None,
                     race: str = "HumeFemale", fit_routine: bool = True,
                     slot_gltfs: Optional[Dict[str, Path]] = None,
                     fps: Optional[float] = None, static_base: bool = False,
                     trans_joints: Optional[Sequence[int]] = None) -> Tuple[Path, str, list]:
    main_src_data = read_path_for(Path(dat_path)).read_bytes()
    main_sections = parse_sections(main_src_data)

    # Slot -> glTF source map. `slot_gltfs` (each slot from its OWN file, e.g.
    # poi0<-13_poi0.gltf, poi1<-13_poi1.gltf) takes priority; otherwise a digit-less
    # name (poi) expands to EVERY slot fed from the single `gltf_path`, and a numbered
    # name (poi1) writes just that slot.
    if slot_gltfs:
        sources = {normalize_anim_target(k): Path(v) for k, v in slot_gltfs.items()}
    else:
        requested = target_anim_name.strip()
        if requested and not requested[-1].isdigit():
            slots = variant_slot_names(main_sections, requested) or [normalize_anim_target(requested)]
        else:
            slots = [normalize_anim_target(requested)]
        sources = {name: gltf_path for name in slots}

    joints = _resolve_bind_joints(Path(dat_path), main_sections, main_src_data, skeleton_dat, race)

    # FFXI emotes split across THREE parts by joint: part 0 (lower) + part 1 (upper)
    # live in this DAT; part 2 (WAIST, joints ~4-25) lives in a SEPARATE sibling file
    # at MotionE + 6 (e.g. ROM/37/13 -> ROM/37/19). One 0x07 routine (here) drives all
    # via the `poi?` wildcard. So when importing a full-body clip, also write the
    # waist part there, or the untouched waist breaks the mid-body. (Only when a single
    # full-body glTF is in play and the sibling actually has the part-2 clip.)
    file_jobs = [(Path(dat_path), sources)]
    base = _clip_base(next(iter(sources)))
    waist_gltf = gltf_path if (gltf_path is not None and not slot_gltfs) else (slot_gltfs or {}).get(f"{base}2")
    if waist_gltf is not None:
        part2 = f"{base}2"
        try:
            waist_src = Path(dat_path).with_name(f"{int(Path(dat_path).stem) + EMOTE_WAIST_OFFSET}.DAT")
            wsecs = parse_sections(read_path_for(waist_src).read_bytes())
        except (ValueError, FileNotFoundError, OSError):
            waist_src = None
        if waist_src is not None and choose_section_by_name(wsecs, part2) is not None:
            file_jobs.append((waist_src, {part2: Path(waist_gltf)}))

    gltf_cache: Dict[Path, Tuple[dict, list]] = {}
    written = []
    last_gltf_name = target_anim_name
    target = Path(dat_path)
    for dat_src, slot_to_gltf in file_jobs:
        out, file_written, gname = _write_part_file(
            dat_src, slot_to_gltf, joints, template_anim_name, gltf_cache, no_base, fit_routine,
            fps=fps, static_base=static_base, trans_joints=trans_joints)
        if dat_src == Path(dat_path):
            target = out
        if gname:
            last_gltf_name = gname
        written += [(f"{n} ({f}f)" + (f" @{dat_src.parent.name}/{dat_src.stem}" if dat_src != Path(dat_path) else ""))
                    for n, f in file_written]

    return target, last_gltf_name, written


# ---------------------------------------------------------------------------
# Layered / partial-bone overlay import
#
# Build a NEW animation by taking a whole base clip and overlaying another clip's
# rotation onto a chosen SET OF BONES over a chosen FRAME WINDOW — e.g. a "talk"
# built from "idle" with a yap clip driving only the jaw bones over frames 0-35.
# Everything outside the selection (other bones, all translations/scales, frames
# outside the window) is copied verbatim from the base clip.
# ---------------------------------------------------------------------------


def _parse_frame_range(spec: Optional[str], num_frames: int) -> Tuple[int, int]:
    """``'0-35'`` -> ``(0, 35)``; ``'10'`` -> ``(10, 10)``; empty -> the whole clip.
    Open-ended ``'-N'`` / ``'N-'`` fill the missing side; result is clamped to
    ``[0, num_frames-1]`` and ordered low<=high. Bounds are base-clip keyframes."""
    last = num_frames - 1
    if not spec or not spec.strip():
        return 0, last
    s = spec.strip()
    if "-" in s:
        lo_s, hi_s = s.split("-", 1)
        lo = int(lo_s) if lo_s.strip() else 0
        hi = int(hi_s) if hi_s.strip() else last
    else:
        lo = hi = int(s)
    lo = max(0, min(lo, last))
    hi = max(0, min(hi, last))
    return (lo, hi) if lo <= hi else (hi, lo)


def _parse_bone_list(spec: Optional[str]) -> Optional[List[int]]:
    """``'bone0007,bone0009'`` / ``'7,9'`` / ``'0007 0009'`` -> ``[7, 9]``.
    Empty -> ``None`` (caller defaults to every bone the layer clip animates)."""
    if not spec or not spec.strip():
        return None
    out: List[int] = []
    for tok in re.split(r"[,\s]+", spec.strip()):
        if not tok:
            continue
        digits = re.sub(r"\D", "", tok)   # 'bone0007' -> '0007', '7' -> '7'
        if not digits:
            raise ValueError(f"Could not read a bone index from {tok!r}.")
        out.append(int(digits))
    # De-dupe, keep first-seen order.
    seen: set = set()
    return [b for b in out if not (b in seen or seen.add(b))]


def _encode_animation_section(name: str, unk0: int, num_frames: int, keyframe_duration: float,
                              entries: Sequence[Tuple[int, Sequence[Tuple[float, ...]],
                                                      Sequence[Tuple[float, ...]],
                                                      Sequence[Tuple[float, ...]]]]) -> bytes:
    """Encode a 0x2B skeleton-animation section from fully-decoded per-frame delta
    arrays. Byte-for-byte the same layout :func:`build_animation_block` emits — an
    84-byte per-entry header (joint_index + rotation/translation/scale offset&const
    groups) followed by the shared keyframe float pool — so the game decodes it
    identically. ``entries`` = ``(joint_index, rotations[f][4], translations[f][3],
    scales[f][3])`` where each value is the DAT delta form (rot ⊗ bind⁻¹, trans −
    bind, absolute scale). A channel that is constant across all frames is stored
    inline (offset 0 + const) exactly as the stock clips do."""
    float_cursor = len(entries) * (84 // 4)   # 21 dwords of header per entry
    sequence_blob = bytearray()
    entry_blob = bytearray()

    for joint_index, rotations, translations, scales in entries:
        entry_blob.extend(struct.pack("<i", joint_index))

        def write_group(source_values: Sequence[Tuple[float, ...]]) -> Tuple[List[int], List[float]]:
            nonlocal float_cursor
            component_count = len(source_values[0])
            offsets: List[int] = []
            consts: List[float] = []
            for component_index in range(component_count):
                channel_values = [frame[component_index] for frame in source_values]
                if almost_constant(channel_values):
                    offsets.append(0)
                    consts.append(channel_values[0])
                else:
                    offsets.append(float_cursor)
                    consts.append(channel_values[0])
                    sequence_blob.extend(struct.pack("<" + "f" * len(channel_values), *channel_values))
                    float_cursor += len(channel_values)
            return offsets, consts

        rot_offsets, rot_consts = write_group(rotations)
        trans_offsets, trans_consts = write_group(translations)
        scale_offsets, scale_consts = write_group(scales)

        entry_blob.extend(struct.pack("<4i", *rot_offsets))
        entry_blob.extend(struct.pack("<4f", *rot_consts))
        entry_blob.extend(struct.pack("<3i", *trans_offsets))
        entry_blob.extend(struct.pack("<3f", *trans_consts))
        entry_blob.extend(struct.pack("<3i", *scale_offsets))
        entry_blob.extend(struct.pack("<3f", *scale_consts))

    body = bytearray()
    body.extend(struct.pack("<H", unk0))
    body.extend(struct.pack("<H", len(entries)))
    body.extend(struct.pack("<H", num_frames))
    body.extend(struct.pack("<f", keyframe_duration))
    body.extend(entry_blob)
    body.extend(sequence_blob)

    total_size = 16 + len(body)
    padded_size = (total_size + 15) & ~15
    section_meta = encode_section_meta(padded_size, SECTION_TYPE_SKELETON_ANIMATION,
                                       what="skeleton-animation section")

    section = bytearray()
    section.extend(encode_name(name))
    section.extend(struct.pack("<I", section_meta))
    section.extend(b"\x00" * 8)
    section.extend(body)
    section.extend(b"\x00" * (padded_size - len(section)))
    return bytes(section)


def _bone(index: int) -> str:
    return f"bone{index:04d}"


def _find_layer_gltf(value: str, dat: Path) -> Tuple[List[Path], List[Path]]:
    """Resolve a ``--layer`` value that isn't an existing path by name-searching the
    DAT's animation export folders. Returns ``(matches, roots_searched)``; ``matches``
    is de-duplicated in discovery order (direct hit in a root first, then any nested
    hit). A missing ``.gltf`` suffix is tried too, so ``--layer yap`` finds ``yap.gltf``."""
    from xi.entity.anim.xi_export import default_anim_output_dir, legacy_anim_output_dir

    names = [value] + ([value + ".gltf"] if not value.lower().endswith(".gltf") else [])
    roots: List[Path] = []
    for root in (default_anim_output_dir(dat), legacy_anim_output_dir(dat)):
        if root not in roots:
            roots.append(root)

    matches: List[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for name in names:
            direct = root / name
            if direct.is_file():
                matches.append(direct)
            matches.extend(p for p in sorted(root.rglob(name)) if p.is_file())

    uniq: List[Path] = []
    for m in matches:
        rm = m.resolve()
        if rm not in {u.resolve() for u in uniq}:
            uniq.append(m)
    return uniq, roots


def layer_animation(dat_path: Path, base_anim_name: str, new_anim_name: str, layer_gltf: Path,
                    frame_spec: Optional[str] = None, bone_spec: Optional[str] = None,
                    no_base: bool = False, skeleton_dat: Optional[Path] = None,
                    race: str = "HumeFemale") -> Tuple[Path, dict]:
    """Create a new animation ``new_anim_name`` = the whole ``base_anim_name`` clip
    with ``layer_gltf``'s rotation overlaid onto ``bone_spec`` over ``frame_spec``.

    Rotation on the selected bones inside the window comes from the layer clip
    (converted to the DAT's bind-relative delta); everything else is copied from the
    base. The result is inserted as a new 0x2B section (or replaces an existing one of
    the same name). Returns ``(out_path, info)``."""
    target = editable_dat(Path(dat_path), fresh=not no_base)
    data = target.read_bytes()
    sections = parse_sections(data)

    base_section = choose_section_by_name(sections, base_anim_name)
    if base_section is None:
        have = ", ".join(sorted({s.name.rstrip("\x00 ") for s in sections
                                 if s.type_code == SECTION_TYPE_SKELETON_ANIMATION})) or "(none)"
        raise ValueError(f"Base animation {base_anim_name!r} not found in "
                         f"{Path(dat_path).name}. Present tracks: {have}.")
    base = parse_animation(data, base_section)
    base_unk0 = struct.unpack_from("<H", data, base_section.data_start)[0]
    native_frames = base.num_frames
    kdur = base.keyframe_duration

    joints = _resolve_bind_joints(Path(dat_path), sections, data, skeleton_dat, race)

    doc, buffers = load_gltf_document(Path(layer_gltf))
    layer = choose_gltf_animation(doc, buffers, new_anim_name)
    if not layer.sample_times:
        raise ValueError(f"Layer glTF {Path(layer_gltf).name!r} has no animation "
                         f"keyframes to overlay.")

    # Work — and store the output — in 30 fps frames, the space the user authors in:
    # `anim export` bakes every clip to GAME_FPS, so the frame indices they see in the
    # glTF / Blender (and pass to --frames / --bones) are 30 fps frames. A DAT clip is
    # stored sparsely (idle here: 17 keyframes at kdur 0.3 ≈ 9 fps), so we resample the
    # base up to 30 fps first — lossless at playback resolution, since the game already
    # interpolates the sparse clip to its 30 fps tick — overlay in that space, then
    # store the result at kdur 1.0 (30 fps native). The base clip that was exported to
    # a 30 fps glTF and this baked output therefore share a frame index, so layer frame
    # i lands on output frame i.
    out_frames = int(math.ceil((native_frames - 1) / kdur)) + 1 if kdur > 0 else native_frames
    out_frames = max(1, out_frames)
    out_kdur = 1.0

    frame_lo, frame_hi = _parse_frame_range(frame_spec, out_frames)
    requested_bones = _parse_bone_list(bone_spec)
    if requested_bones is None:
        requested_bones = sorted(layer.joints.keys())
    bone_set = set(requested_bones)

    entries: List[Tuple[int, list, list, list]] = []
    overlaid: List[Tuple[int, int]] = []
    overlaid_rot: Dict[int, Tuple[float, float, float, float]] = {}
    no_layer_rotation: List[int] = []
    added_bones: List[int] = []

    def overlay_delta(joint_index: int):
        """The bind-relative conjugate for a selected bone the layer actually drives,
        else None (bone left at base)."""
        node = layer.joints.get(joint_index)
        if node is None or node.rotation is None or joint_index >= len(joints):
            return None, None
        return node, quat_conjugate(joints[joint_index].rotation)

    def build_track(joint_index: int, base_track):
        """Bake this joint to out_frames of (rot, trans, scale) deltas, overlaying the
        layer's rotation inside the window. base_track=None → bind pose everywhere."""
        node = bind_conj = None
        if joint_index in bone_set:
            node, bind_conj = overlay_delta(joint_index)
        rotations, translations, scales = [], [], []
        written = 0
        for i in range(out_frames):
            if base_track is not None:
                rd, td, sv = sample_track(base_track, kdur, float(i))
            else:
                rd, td, sv = (0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)
            if bind_conj is not None and frame_lo <= i <= frame_hi:
                local = sample_vec_track(node.rotation, i / GAME_FPS)   # frame i == time i/30
                rd = quat_normalize(quat_mul(local, bind_conj))
                written += 1
            rotations.append(rd)
            translations.append(td)
            scales.append(sv)
        return rotations, translations, scales, written

    # 1) Every base track, in section order — overlay rotation on the selected bones.
    for joint_index, track in base.tracks.items():
        rotations, translations, scales, written = build_track(joint_index, track)
        entries.append((joint_index, rotations, translations, scales))
        if joint_index in bone_set:
            if written:
                overlaid.append((joint_index, written))
                overlaid_rot[joint_index] = rotations[frame_lo]
            else:
                no_layer_rotation.append(joint_index)

    # 2) Selected bones the base clip doesn't animate — add a fresh entry (bind pose +
    #    overlay) so the bone still moves for the new clip.
    for joint_index in requested_bones:
        if joint_index in base.tracks:
            continue
        added_bones.append(joint_index)
        rotations, translations, scales, written = build_track(joint_index, None)
        entries.append((joint_index, rotations, translations, scales))
        if written:
            overlaid.append((joint_index, written))
            overlaid_rot[joint_index] = rotations[frame_lo]
        else:
            no_layer_rotation.append(joint_index)

    if not overlaid:
        raise ValueError(
            "Nothing was overlaid: none of the selected --bones are animated by the "
            "layer glTF. Check the bone names/indices and that the layer clip drives "
            "them.")

    new_section = _encode_animation_section(new_anim_name, base_unk0, out_frames, out_kdur, entries)
    replacing = choose_section_by_name(sections, new_anim_name) is not None

    out_bytes = rebuild_dat(data, sections, new_section, new_anim_name)
    target.write_bytes(out_bytes)

    info = {
        "out_path": target,
        "new_anim": new_anim_name,
        "base_anim": base.name.rstrip("\x00 "),
        "layer_gltf": str(Path(layer_gltf)),
        "layer_name": layer.name,
        "layer_samples": len(layer.sample_times),
        "layer_joints": len(layer.joints),
        "num_frames": out_frames,
        "native_frames": native_frames,
        "kdur": kdur,
        "base_fps": kdur * GAME_FPS if kdur else 0.0,
        "base_secs": (native_frames - 1) / kdur / GAME_FPS if kdur else 0.0,
        "base_tracks": len(base.tracks),
        "frames": (frame_lo, frame_hi),
        "window_src": "explicit --frames" if (frame_spec and frame_spec.strip()) else "whole base clip",
        "overlaid": overlaid,
        "overlaid_rot": overlaid_rot,
        "no_layer_rotation": no_layer_rotation,
        "added_bones": added_bones,
        "entries": len(entries),
        "section_bytes": len(new_section),
        "bytes": len(out_bytes),
        "bind_joints": len(joints),
        "replaced": replacing,
        "anim_sections": sum(1 for s in sections if s.type_code == SECTION_TYPE_SKELETON_ANIMATION),
    }
    return target, info


def main() -> int:
    parser = argparse.ArgumentParser(description="Import edited glTF animation back into an FFXI DAT.")
    parser.add_argument("dat_path", type=Path, help="Path to the DAT file to modify")
    parser.add_argument("gltf_path", type=Path, help="Path to the edited glTF file")
    parser.add_argument("anim_name", help="4-character DAT animation name to write, such as vil0")
    parser.add_argument("--template-anim", default="idl", help="Existing DAT animation to use as the template when the target animation does not already exist")
    parser.add_argument("--no-base", action="store_true", help="Write onto the current DAT without restoring .base (use after a mesh import so the mesh is kept)")
    args = parser.parse_args()

    out_path, gltf_animation_name, written = import_animation(args.dat_path.resolve(), args.gltf_path.resolve(), args.anim_name, args.template_anim, args.no_base)
    print(f"Wrote DAT: {out_path}")
    print(f"Imported glTF animation: {gltf_animation_name}")
    print(f"Wrote DAT animation(s): {', '.join(written)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------

import click as _click  # noqa: E402


@_click.command('import')
@_click.argument('dat_path')
@_click.argument('anim_name', required=False, default=None)
@_click.argument('gltf_path', required=False, type=_click.Path(path_type=Path))
@_click.option('--anim', 'anim_opt', default=None,
               help='Base animation name (alternative to the positional ANIM_NAME). '
                    'In --layer mode this is the clip that is copied and overlaid.')
@_click.option('--add', 'add_name', default=None,
               help='Create a NEW animation track with this name (instead of editing an '
                    'existing one). With --layer it is the layered result; without, it is '
                    'a full import of the glTF — e.g. `--add tlk yap.gltf`.')
@_click.option('--layer', 'layer_gltf', default=None,
               type=_click.Path(path_type=Path),
               help='Layer mode: a glTF whose rotation is overlaid onto --bones over '
                    '--frames of the base clip, building a layered animation. A bare '
                    "name (yap.gltf) is looked up under the DAT's export folder "
                    '(exports/anim/<rom>/<stem>/…); pass a full path to use it directly.')
@_click.option('--frames', 'frame_spec', default=None,
               help='Layer mode: frame window to overlay, e.g. 0-35. Omit to overlay '
                    "the WHOLE base clip (its frame count wins — the window caps to the "
                    'base even if the layer is longer). Indices are 30 fps frames, as '
                    'seen in the exported glTF.')
@_click.option('--bones', 'bone_spec', default=None,
               help='Layer mode: comma/space-separated bones to overlay, e.g. '
                    'bone0007,bone0009 or 7,9 (default: every bone the layer animates).')
@_click.option('--verbose', 'verbose', is_flag=True, default=False,
               help='Print extra detail (per-bone rotations, timing, byte sizes) under '
                    'the summary.')
@_click.option('--template-anim', default='idl', show_default=True,
               help='Existing DAT animation to use as template if target does not exist')
@_click.option('--no-base', is_flag=True,
               help='Write onto the current DAT without restoring .base (use after a mesh import so the imported mesh is kept)')
@_click.option('--race', default=None,
               help='Base race skeleton whose bind pose was used at export, for DATs '
                    'with no skeleton of their own. Auto-detected from the DAT id '
                    '(rom/37/13 → HumeFemale); pass to override.')
@_click.option('--skeleton-dat', type=_click.Path(path_type=Path), default=None,
               help='Explicit base skeleton DAT (ROM path or file path). Overrides '
                    '--race when the DAT has no skeleton of its own. Must match the '
                    'skeleton used at export.')
@_click.option('--keep-routine-duration', is_flag=True,
               help="Don't grow the emote's 0x07 routine window to fit a longer clip. "
                    'By default a longer import lengthens the routine so the whole clip '
                    'plays in-game (the routine duration, not the frame count, gates playback).')
@_click.option('--fps', type=float, default=None,
               help='Keyframe sampling rate. Default = the clip\'s native rate (emotes '
                    '15 fps). Higher = denser keyframes / smoother in-game interpolation '
                    '(e.g. --fps 30 or --fps 60). Playback duration is preserved (kdur is '
                    'set to fps/30). Note: stock clips are all <30 fps; non-native fps is '
                    'non-stock, so test in-game.')
@_click.option('--static-base', is_flag=True, default=False,
               help='Hold the template clip\'s translation & scale STATIC (at frame 0) so '
                    'ONLY the glTF\'s rotation animates. Use when your glTF animates just a '
                    'few bones and you don\'t want the base clip\'s idle bob/motion leaking '
                    'onto the others (rig offsets are kept; only the per-frame motion drops).')
@_click.option('--trans-bones', 'trans_bone_spec', default=None, metavar='BONES',
               help='Comma/space-separated bones whose TRANSLATION is taken from the glTF '
                    'instead of the template rig (e.g. bone0001 or 1). Use for poses that '
                    'move a root/pelvis bone — a sit or lie-down authored as a root drop — '
                    'which the rotation-only default would leave at standing height. Only '
                    'trust glTFs whose translations are sane (xi-exported / numerically '
                    'authored); Blender re-anchors bone translations.')
@_click.option('--add-schedule', is_flag=True, default=False,
               help='Also create a 0x07 scheduler routine that plays the imported clip, so '
                    'a cutscene can SetAction it (a cutscene can only fire routines, not raw '
                    'clips). The routine appears in the cutscene author\'s Anim dropdown.')
@_click.option('--schedule-tag', default=None,
               help='Routine/action tag for --add-schedule (4 chars, default = the clip name).')
@_click.option('--loop/--no-loop', default=True, show_default=True,
               help='--add-schedule: loop forever (maxLoops=0) vs play once and hold '
                    '(maxLoops=1). Overridden by --loops N.')
@_click.option('--loops', 'max_loops', type=int, default=None, metavar='N',
               help='--add-schedule: maxLoops as a full u16 — 0 = forever, N = play N '
                    'times then hold (retail cast uses e.g. 28). Overrides --loop/--no-loop.')
@_click.option('--blend', type=int, default=15, show_default=True, metavar='N',
               help='--add-schedule: crossfade in+out of the clip, in frames (30/s) — the '
                    'routine\'s transIn/transOut blending. 0 = hard snap. For asymmetric '
                    'blends re-run `anim schedule add` with --trans-in/--trans-out.')
def cmd(dat_path: str, anim_name: str, gltf_path, anim_opt, add_name, layer_gltf,
        frame_spec, bone_spec, verbose: bool, template_anim: str, no_base: bool,
        race: str, skeleton_dat, keep_routine_duration: bool, fps, static_base: bool,
        trans_bone_spec, add_schedule: bool, schedule_tag, loop: bool, max_loops, blend: int):
    """Import an edited glTF animation back into an FFXI DAT.

    DAT_PATH may be a ROM-relative spec like ROM/217/32. ANIM_NAME is the target
    track; a name with no trailing digit defaults to slot 0 (idl -> idl0).
    GLTF_PATH is optional — if omitted, the exported clip for this anim is found
    automatically under exports/anim/<rom>/<stem>_<anim>/.

    LAYER MODE (--layer): build a NEW animation by overlaying part of one clip onto
    another. Take the whole base clip (--anim / positional ANIM_NAME), overlay the
    layer glTF's rotation onto just --bones over just --frames, and write the result
    as --add NAME. Everything else (other bones, all translations/scales, frames
    outside the window) is copied from the base. Example:

        xi anim import rom9/25/40.dat --anim idl --add tlk \\
             --layer yap.gltf --frames 0-35 --bones bone0007,bone0009

    grabs 'idl', overlays yap's rotation on bones 7 & 9 for frames 0-35, and inserts
    it as the new track 'tlk' (confirm with `xi anim export rom9/25/40.dat --anim tlk`).

    A digit-less ANIM_NAME (e.g. 'poi') writes EVERY slot of that emote (poi0 +
    poi1, …) in one pass — emotes split across slots that play overlaid (poi0 lower
    body, poi1 upper body), so both must be written to stay in sync. A numbered name
    (poi1) writes just that slot.

    With no GLTF_PATH, auto-find prefers a single full-body <stem>_poi.gltf (author
    the whole skeleton in one clip; the import splits it to each slot by joint) and
    otherwise falls back to per-slot files <stem>_poi0.gltf, <stem>_poi1.gltf. An
    explicit GLTF_PATH with a digit-less name is also split across all slots.

    For animation-only DATs (no skeleton of their own) pass the same --race /
    --skeleton-dat you exported with so the bind pose matches.

    By default the DAT is restored from <dat>.base first. Pass --no-base to write
    onto the current DAT instead (e.g. after `mesh import`, keeping the mesh).
    """
    from xi.entity.mesh.xi_export import resolve_dat_path
    from xi.entity.anim.xi_export import default_anim_output_dir, detect_race_from_dat, legacy_anim_output_dir
    try:
        dat = resolve_dat_path(dat_path)
        skel = resolve_dat_path(str(skeleton_dat)) if skeleton_dat is not None else None
    except FileNotFoundError as e:
        raise _click.ClickException(str(e))

    if race is None:
        race = detect_race_from_dat(dat)
        if race and verbose:
            _click.echo(f'Detected race: {race}')
        # No race detected → leave it unset. A DAT with its own skeleton
        # (monster/NPC/object) uses that for delta conversion and needs no race;
        # an animation-only DAT asks for --race / --skeleton-dat instead of being
        # silently rigged onto the HumeFemale skeleton.

    base_name = anim_opt or anim_name

    # ── Layer mode: build a new clip from base + partial-bone overlay ───────────
    if layer_gltf is not None:
        if not add_name:
            raise _click.ClickException(
                "--layer needs --add NAME (the new animation to create).")
        if not base_name:
            raise _click.ClickException(
                "--layer needs a base animation: pass --anim NAME (or the positional "
                "ANIM_NAME) — the clip that is copied and overlaid.")
        layer_path = Path(layer_gltf)
        if not layer_path.is_file():
            matches, roots = _find_layer_gltf(str(layer_gltf), dat)
            if not matches:
                where = "; ".join(str(r) for r in roots)
                raise _click.ClickException(
                    f"Layer glTF {str(layer_gltf)!r} not found. Looked under: {where}. "
                    f"Export it there (xi anim export {dat_path} --anim <name>) or "
                    f"pass a full path.")
            if len(matches) > 1:
                listed = "\n  ".join(str(m) for m in matches)
                raise _click.ClickException(
                    f"Layer glTF {str(layer_gltf)!r} is ambiguous — {len(matches)} "
                    f"matches:\n  {listed}\nPass a full path to pick one.")
            layer_path = matches[0]
        try:
            out_path, info = layer_animation(
                dat, base_name, add_name, layer_path.resolve(),
                frame_spec=frame_spec, bone_spec=bone_spec, no_base=no_base,
                skeleton_dat=skel, race=race)
            sched = (_make_schedule(dat, info["new_anim"], schedule_tag, loop, blend,
                                    max_loops=max_loops) if add_schedule else None)
        except ValueError as e:
            raise _click.ClickException(str(e))
        _emit_layer_summary(dat, dat_path, info, sched, add_name, verbose)
        return

    # ── Normal (full-clip) import: replace an existing track, or create a new one ──
    # `--add NAME` names the target and signals "create new"; a lone positional (which
    # click parses as ANIM_NAME) is then actually the glTF, e.g. `--add tlk yap.gltf`.
    if add_name:
        if gltf_path is None and anim_name and anim_name != add_name:
            gltf_path = anim_name
        anim_name = add_name
    else:
        anim_name = base_name
    if not anim_name:
        raise _click.ClickException(
            "Provide the target track name (positional ANIM_NAME, --anim, or --add), or "
            "use --layer to build a layered animation.")

    # A glTF given by a bare name (not an existing path) is looked up under the DAT's
    # export folder, same as --layer; None falls through to the auto-find below.
    if gltf_path is not None and not Path(gltf_path).is_file():
        matches, roots = _find_layer_gltf(str(gltf_path), dat)
        if not matches:
            where = "; ".join(str(r) for r in roots)
            raise _click.ClickException(
                f"glTF {str(gltf_path)!r} not found. Looked under: {where}. "
                f"Pass a full path or export it first.")
        if len(matches) > 1:
            listed = "\n  ".join(str(m) for m in matches)
            raise _click.ClickException(
                f"glTF {str(gltf_path)!r} is ambiguous — {len(matches)} matches:\n  "
                f"{listed}\nPass a full path to pick one.")
        gltf_path = matches[0]
        if verbose:
            _click.echo(f"Using glTF: {gltf_path}")

    target = normalize_anim_target(anim_name)
    digitless = bool(anim_name.strip()) and not anim_name.strip()[-1].isdigit()

    slot_gltfs = None
    if gltf_path is None:
        base = default_anim_output_dir(dat)
        bases = [base]
        legacy_base = legacy_anim_output_dir(dat)
        if legacy_base != base:
            bases.append(legacy_base)
        if digitless:
            tried = []
            for base_dir in bases:
                d = base_dir / f"{dat.stem}_{anim_name}"
                tried.append(d)
                # 1. Prefer a single FULL-BODY file <stem>_poi.gltf — author the whole
                #    skeleton in one clip and let the import split it across slots by
                #    joint (poi0 takes its joints, poi1 takes its joints). Easiest path.
                full_body = d / f"{dat.stem}_{anim_name}.gltf"
                if full_body.is_file():
                    gltf_path = full_body
                    if verbose:
                        _click.echo(f'Using glTF: {full_body} (full body — split across all slots)')
                    break
                # 2. Else each variant's OWN file: <stem>_poi0.gltf -> poi0, etc.
                found_slots = {}
                for f in sorted(d.glob(f"{dat.stem}_*.gltf")) if d.is_dir() else []:
                    slot = f.stem[len(dat.stem) + 1:]           # '13_poi0' -> 'poi0'
                    if slot and slot[-1].isdigit():
                        found_slots[slot] = f
                if found_slots:
                    slot_gltfs = found_slots
                    break
            if gltf_path is None and not slot_gltfs:
                raise _click.ClickException(
                    f"No glTF found for '{anim_name}' under {', '.join(str(p) for p in tried)} "
                    f"(expected a full-body {dat.stem}_{anim_name}.gltf, or per-slot "
                    f"{dat.stem}_{anim_name}0.gltf, {dat.stem}_{anim_name}1.gltf, …). "
                    f"Export it first or pass a glTF path.")
            if slot_gltfs and verbose:
                for slot, f in slot_gltfs.items():
                    _click.echo(f'Using glTF: {slot} <- {f}')
        else:
            candidates = []
            for base_dir in bases:
                for key in (anim_name, target, anim_name.rstrip("0")):
                    d = base_dir / f"{dat.stem}_{key}"
                    if d.is_dir():
                        candidates += sorted(d.glob("*.gltf"))
            if not candidates:
                raise _click.ClickException(
                    f"No .gltf found for '{anim_name}' under {base}/{dat.stem}_{anim_name}. "
                    f"Export it first or pass a glTF path.")
            gltf_path = candidates[0]
            if verbose:
                _click.echo(f'Using glTF: {gltf_path}')

    try:
        out_path, gltf_anim_name, written = import_animation(
            dat, Path(gltf_path).resolve() if gltf_path else None, anim_name, template_anim, no_base,
            skeleton_dat=skel, race=race, fit_routine=not keep_routine_duration, slot_gltfs=slot_gltfs,
            fps=fps, static_base=static_base, trans_joints=_parse_bone_list(trans_bone_spec))
    except ValueError as e:
        raise _click.ClickException(str(e))
    # Schedule the clip actually written (its name as it landed in the DAT), not a
    # re-derived one — the two can differ (slot normalisation, pre-existing tracks).
    written_name = written[0].split()[0] if written else normalize_anim_target(anim_name)
    sched = (_make_schedule(dat, written_name, schedule_tag, loop, blend,
                            max_loops=max_loops) if add_schedule else None)
    _emit_import_summary(dat, dat_path, gltf_path, written, sched, written_name, verbose)


CHECK = "✓"


def _rom_spec(dat: Path) -> str:
    """The DAT's ``ROM<N>/<dir>/<file>.DAT`` spec, else its filename."""
    from xi.entity.anim.xi_export import _rom_spec_for
    try:
        return _rom_spec_for(Path(dat))
    except Exception:
        return Path(dat).name


def _kv(rows) -> None:
    """Print aligned ``key   value`` rows (flush-left key column)."""
    width = max((len(k) for k, _ in rows), default=0) + 6
    for key, value in rows:
        _click.echo(f"{key.ljust(width)}{value}")


def _make_schedule(dat: Path, clip_name: str, tag, loop: bool, blend: int = 15,
                   max_loops=None) -> dict:
    """Wrap a just-imported clip in a 0x07 routine (no_base=True so the clip is kept).
    ``blend`` = crossfade in+out frames (the routine's transIn/transOut).
    ``max_loops`` is the format's u16 (0 = forever, N = play N times); overrides ``loop``.
    Returns the schedule info; stays quiet — the caller's summary reports it."""
    from xi.entity.anim.xi_schedule import add_schedule as _add
    try:
        return _add(dat, clip_name, routine_tag=tag, loop=loop, max_loops=max_loops,
                    trans_in=blend, trans_out=blend, no_base=True, echo=lambda *_a: None)
    except ValueError as e:
        raise _click.ClickException(str(e))


def _emit_layer_summary(dat, dat_spec, info, sched, verify_name, verbose) -> None:
    e = _click.echo
    lo, hi = info["frames"]
    e(f"{CHECK} {'Replaced' if info['replaced'] else 'Imported'} layered animation")
    e("")
    _kv([("DAT", _rom_spec(dat)), ("Base", info["base_anim"]),
         ("Created", info["new_anim"]), ("Layer", Path(info["layer_gltf"]).name)])
    e("")

    e("Overlay")
    for joint, frames in info["overlaid"]:
        line = f"  {CHECK} {_bone(joint)}"
        if verbose:
            r = info["overlaid_rot"].get(joint)
            extra = f"{frames} frames"
            if r:
                extra += f", rot@{lo}=({r[0]:+.3f},{r[1]:+.3f},{r[2]:+.3f},{r[3]:+.3f})"
            line += f"  ({extra})"
        e(line)
    for joint in info["no_layer_rotation"]:
        e(f"  · {_bone(joint)} — no layer rotation, left at base")
    e("")

    e("Encoded")
    e(f"  {info['num_frames']} frames @ 30 fps")
    e(f"  {info['entries']} tracks")
    e(f"  {info['section_bytes']:,} bytes")
    if verbose:
        e(f"  DAT total {info['bytes']:,} bytes")
        e(f"  base {info['native_frames']} keyframes @ kdur {info['kdur']:.4f} "
          f"(~{info['base_fps']:.0f} fps, {info['base_secs']:.2f}s)")
        e(f"  layer clip {info['layer_name']!r} — {info['layer_samples']} samples, "
          f"{info['layer_joints']} joints")
        e(f"  window frames {lo}-{hi} ({info['window_src']})")
        e(f"  bind skeleton {info['bind_joints']} joints")
    e("")

    e("Updated")
    e(f"  {CHECK} Animation {'replaced' if info['replaced'] else 'inserted'}: {info['new_anim']}")
    if sched:
        detail = ""
        if verbose:
            from xi.entity.anim.xi_schedule import _fmt_loops
            ml = sched.get("maxLoops", 0 if sched.get("loop") else 1)
            detail = (f"  (plays {sched['clip']}, ref {sched['ref']!r}, "
                      f"{_fmt_loops(ml)}, dur {sched['dur']}, "
                      f"blend {sched['transIn']}/{sched['transOut']}f)")
        e(f"  {CHECK} Schedule created: {sched['tag']}{detail}")
    e(f"  {CHECK} DAT written: {dat_spec}")
    e("")

    e("Verify")
    e(f"  xi anim export {dat_spec} --anim {verify_name}")


def _emit_import_summary(dat, dat_spec, gltf_path, written, sched, verify_name, verbose) -> None:
    e = _click.echo
    e(f"{CHECK} Imported animation")
    e("")
    rows = [("DAT", _rom_spec(dat))]
    if gltf_path:
        rows.append(("glTF", Path(gltf_path).name))
    _kv(rows)
    e("")

    e("Written")
    for entry in written:
        e(f"  {CHECK} {entry}")
    e("")

    e("Updated")
    if sched:
        detail = ""
        if verbose:
            from xi.entity.anim.xi_schedule import _fmt_loops
            ml = sched.get("maxLoops", 0 if sched.get("loop") else 1)
            detail = (f"  (plays {sched['clip']}, ref {sched['ref']!r}, "
                      f"{_fmt_loops(ml)}, dur {sched['dur']}, "
                      f"blend {sched['transIn']}/{sched['transOut']}f)")
        e(f"  {CHECK} Schedule created: {sched['tag']}{detail}")
    e(f"  {CHECK} DAT written: {dat_spec}")
    e("")

    e("Verify")
    e(f"  xi anim export {dat_spec} --anim {verify_name}")

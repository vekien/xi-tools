"""Assemble a whole worn character — every gear slot plus the weapons in hand — into
ONE rigged ``.glb``/``.fbx``, with the pieces the game hides actually removed.

Why this is not just "merge every DAT". A dressed FFXI character is a pile of separate
meshes that were authored to overlap: the race body keeps its bare arms, legs and full
head of hair, and the client *drops* whichever of those the worn set covers. Two bytes
drive it (see :func:`xi.entity.anim.xi_export.occludes_display_type`):

* every 0x2A mesh section declares an **occludeType** — what it hides on other pieces
  (a helmet says 0x04 "no hair", a sleeve 0x12 "no wrist");
* every piece inside a section carries a **displayType** — what it *is* (hair, face,
  wrist, pants, shins).

Merge without consulting them and you get the naked body's hair pushing through the
helmet and its bare shins through the boots — geometry that is *inside* the armour in
the DCC viewport but pokes out the moment anything moves. So the union of occludeTypes
across the whole worn set is computed first, then each piece is kept or dropped against
it, exactly as the client does.

The other half is the weapon. A weapon mesh is skinned to its own grip joint, which in
the bind pose sits at the skeleton root — merge it as-is and the sword lies on the
floor. The client re-parents that grip onto the hand attach joint when the weapon is
drawn (xim ``jointParentOverrides``); the grip joint is named by the weapon's
``info.standardJointIndex``, the hands are joint references 127 (right) and 126 (left).

A ranged weapon has no grip reference at all — it binds straight to a back-mount bone
that nothing animates, and the client simply **scales it to zero** until it is drawn.
So a stowed bow is not drawn at all here either; ``--draw-ranged`` re-parents its mount
onto the bow hand and brings it into the export.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import click

from xi.xi_config import FFXI_DIR, read_path_for
from xi.gear.xi_core import LOOK_RACE_NAMES, SLOTS, parse_look
from xi.gear.xi_export import race_skeleton_dat, resolve_gear_dat
from xi.entity.anim.xi_export import (
    GAME_FPS,
    SECTION_TYPE_SKELETON,
    SECTION_TYPE_SKELETON_ANIMATION,
    SECTION_TYPE_SKELETON_MESH,
    AnimationSection,
    AnimationTrack,
    Joint,
    JointGlobal,
    Section,
    animation_playback_frames,
    animation_variants,
    apply_parent_overrides,
    choose_animation,
    compute_global_transforms,
    convert_gltf_to_fbx,  # noqa: F401  (kept next to its glb sibling for discoverability)
    mesh_occlude_type,
    occludes_display_type,
    parse_animation,
    parse_mesh,
    parse_sections,
    parse_skeleton,
    parse_skeleton_references,
    pose_joints_at_playback_frame,
    quat_conjugate,
    quat_mul,
    quat_normalize,
    rotate_vec3,
)
from xi.entity.mesh.xi_export import (
    DEFAULT_ALPHA_SCALE,
    build_gltf,
    convert_glb_to_fbx,
    parse_textures,
    rom_relative,
)

# Joint-reference indices of the hand attach points, shared by every race skeleton
# (all eight carry a 128-entry reference table).
HAND_REF_RIGHT = 127
HAND_REF_LEFT = 126

# Slots whose DAT is a weapon rather than worn armour.
WEAPON_SLOTS = ("main", "sub", "ranged")


@dataclass
class PoseSource:
    """One DAT going into the pose, and what the character wears it as."""
    path: Path
    slot: str = "gear"                      # 'face' / 'body' / 'main' / … or 'gear'
    data: bytes = b""
    sections: List[Section] = field(default_factory=list)


@dataclass
class _MeshPart:
    source: PoseSource
    section: Section
    occlude_type: int
    # False for a stowed ranged weapon: still equipped (so it keeps its say in the
    # occlusion union) but scaled to zero by the client, so it contributes no geometry.
    render: bool = True


def _load(source: PoseSource) -> PoseSource:
    source.data = read_path_for(Path(source.path)).read_bytes()
    source.sections = parse_sections(source.data)
    return source


def parse_info(data: bytes, sections) -> dict:
    """The 16-byte ``info`` section a weapon/body DAT carries (xim ``InfoSection``).

    Only the fields the assembly needs: ``standardJointIndex`` (byte 6) names the
    joint reference of a weapon's grip — 0xFF means "no grip", which is what ranged
    weapons report because they are skinned to a back mount instead."""
    for section in sections:
        if section.name != "info":
            continue
        body = data[section.data_start:section.data_start + 16]
        if len(body) < 16:
            break
        return {
            "weapon_animation_type": None if body[3] == 0xFF else body[3],
            "standard_joint_index": None if body[6] == 0xFF else body[6],
            "scale": None if body[0x0A] == 0xFF else body[0x0A],
        }
    return {"weapon_animation_type": None, "standard_joint_index": None, "scale": None}


def _lowest_weighted_joint(source: PoseSource) -> Optional[int]:
    """Lowest joint index any vertex of this DAT is weighted to — a stowed ranged
    weapon's back-mount bone (the cluster's root). Joint 0 is the skeleton root and
    never a mount, so it is rejected: re-parenting the root onto a hand would make
    the skeleton its own ancestor."""
    lowest: Optional[int] = None
    for section in source.sections:
        if section.type_code != SECTION_TYPE_SKELETON_MESH:
            continue
        vertices, _ = parse_mesh(source.data, section)
        for vertex in vertices:
            # joint_index1 is left at 0 on single-joint vertices, so it only counts
            # where a second weight actually exists.
            candidates = [vertex.joint_index0]
            if vertex.weight1 > 0.0:
                candidates.append(vertex.joint_index1)
            for joint in candidates:
                if joint is not None and joint > 0 and (lowest is None or joint < lowest):
                    lowest = joint
    return lowest


def _weapon_overrides(sources: List[PoseSource], references, draw_ranged: bool) -> Tuple[dict, list]:
    """``{grip_joint: hand_joint}`` re-parentings that put the drawn weapons in the hands,
    plus a human-readable note per weapon for the summary."""
    overrides: dict = {}
    notes: list = []
    if len(references) <= HAND_REF_RIGHT:
        if any(s.slot in WEAPON_SLOTS for s in sources):
            notes.append({"slot": "-", "attached": False,
                          "why": f"skeleton has only {len(references)} joint references "
                                 f"(need {HAND_REF_RIGHT + 1}) — weapons left at bind pose"})
        return overrides, notes

    for slot, hand_ref in (("main", HAND_REF_RIGHT), ("sub", HAND_REF_LEFT)):
        for source in (s for s in sources if s.slot == slot):
            grip_ref = parse_info(source.data, source.sections)["standard_joint_index"]
            if grip_ref is None or grip_ref >= len(references):
                notes.append({"slot": slot, "dat": rom_relative(source.path), "attached": False,
                              "why": "no standardJointIndex (grip) in its info section"})
                continue
            grip = references[grip_ref].joint_index
            hand = references[hand_ref].joint_index
            if grip == hand:
                notes.append({"slot": slot, "dat": rom_relative(source.path), "attached": False,
                              "why": "grip joint is already the hand joint"})
                continue
            overrides[grip] = hand
            notes.append({"slot": slot, "dat": rom_relative(source.path), "attached": True,
                          "grip_joint": grip, "hand_joint": hand})

    # A ranged weapon has no grip reference — its mesh binds to a back-mount bone. Drawing
    # it means re-parenting that mount onto the bow hand; while stowed it is not drawn at
    # all (build_pose drops its geometry), so this only runs for the drawn case.
    if draw_ranged:
        for source in (s for s in sources if s.slot == "ranged"):
            mount = _lowest_weighted_joint(source)
            hand = references[HAND_REF_LEFT].joint_index
            if mount is None or mount == hand:
                notes.append({"slot": "ranged", "dat": rom_relative(source.path), "attached": False,
                              "why": "no back-mount joint to re-parent (empty or stub slot)"})
                continue
            overrides[mount] = hand
            notes.append({"slot": "ranged", "dat": rom_relative(source.path), "attached": True,
                          "grip_joint": mount, "hand_joint": hand})
    return overrides, notes


def merge_pose_clip(anim: str, clip_sources, num_joints: int):
    """Every body-region layer of ``anim``, across every supplied DAT, merged into one
    full-body clip. Returns ``(AnimationSection, [layer names])`` or ``None``.

    FFXI splits a clip by body region into numbered siblings that animate DISJOINT joint
    sets and play together — and for a player character those siblings live in *different
    DATs*: ``idl0`` (lower body) in the race body DAT, ``idl1`` (upper body) in its
    companion motion pack. Posing from ``idl0`` alone leaves every upper-body joint at
    bind, which is how a stowed shield ends up at the character's feet instead of on the
    back: joint 87 is only ever driven by ``idl1``.

    A name that already ends in a digit (``idl1``) is taken literally — that asks for one
    specific layer. Later sources win a joint they share with an earlier one, so pass the
    base skeleton first and motion packs after."""
    explicit = anim[-1:].isdigit()
    base = anim if explicit else anim.rstrip("0123456789")
    layers = []
    for data, sections in clip_sources:
        names = ([anim] if explicit else animation_variants(data, base))
        for nm in names:
            try:
                layers.append((data, choose_animation(sections, nm)))
            except ValueError:
                continue
    if not layers:
        return None

    tracks: Dict[int, AnimationTrack] = {}
    num_frames = 0
    keyframe_duration = None
    for data, section in layers:
        parsed = parse_animation(data, section)
        tracks.update(parsed.tracks)
        num_frames = max(num_frames, parsed.num_frames)
        if keyframe_duration is None:
            keyframe_duration = parsed.keyframe_duration
    merged = AnimationSection(name=base, num_joints=num_joints, num_frames=num_frames,
                              keyframe_duration=keyframe_duration or 1.0, tracks=tracks)
    return merged, [sec.name.rstrip("\x00 ") for _d, sec in layers]


def read_pose_file(path) -> dict:
    """Load a baked pose: the world transform of every joint, per frame, as a viewer has
    already evaluated it.

    ``{"name", "fps", "space": "world", "frames": [[qx,qy,qz,qw,tx,ty,tz, …per joint], …]}``

    This exists because a viewer's pose is often not reproducible from a clip name and a
    frame number. A weapon-skill schedule lays several clips on a timeline, blends them
    back out to an underlaid base idle and re-parents the weapon grips — the result is a
    composition, not a clip. Handing over the evaluated joints instead makes the export
    match the viewport exactly, whatever produced it.

    Rotations are ``(x, y, z, w)``. Scale is not carried: the rest of the exporter bakes
    rigid transforms only, and FFXI clips use unit scale."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    frames = data.get("frames") or []
    if not frames:
        raise ValueError(f"pose file {path} has no frames")
    stride = 7
    for i, flat in enumerate(frames):
        if len(flat) % stride:
            raise ValueError(f"pose file {path} frame {i}: {len(flat)} values is not a "
                             f"multiple of {stride} (quat xyzw + vec3 per joint)")
    unpacked = [[(tuple(flat[i:i + 4]), tuple(flat[i + 4:i + 7]))
                 for i in range(0, len(flat), stride)] for flat in frames]
    return {"name": data.get("name") or "pose",
            "fps": float(data.get("fps") or GAME_FPS),
            "frames": unpacked}


def _globals_from_frame(frame, joint_count: int) -> List[JointGlobal]:
    """One baked frame -> ``globals_by_joint``, padded/trimmed to the skeleton."""
    out: List[JointGlobal] = []
    for i in range(joint_count):
        if i < len(frame):
            rotation, translation = frame[i]
            out.append(JointGlobal(rotation=quat_normalize(tuple(rotation)),
                                   translation=tuple(translation)))
        else:
            out.append(JointGlobal(rotation=(0.0, 0.0, 0.0, 1.0), translation=(0.0, 0.0, 0.0)))
    return out


def locals_from_globals(joints: List[Joint], globals_by_joint: List[JointGlobal]) -> List[Joint]:
    """World transforms -> the LOCAL transforms that reproduce them on the DAT's hierarchy.

    ``build_gltf`` writes the node tree from ``parent_index`` and a renderer recomputes
    every joint from that tree, so the locals have to agree with the globals used to bake
    the vertices or the skin pulls the mesh apart. Solving ``local = inv(parentGlobal) ∘
    global`` keeps the original hierarchy while absorbing anything the viewer did to the
    pose — including a weapon grip it re-parented onto a hand."""
    out: List[Joint] = []
    for joint in joints:
        g = globals_by_joint[joint.index]
        if joint.parent_index < 0:
            out.append(Joint(index=joint.index, parent_index=joint.parent_index,
                             rotation=g.rotation, translation=g.translation))
            continue
        p = globals_by_joint[joint.parent_index]
        inv = quat_conjugate(p.rotation)
        delta = tuple(g.translation[i] - p.translation[i] for i in range(3))
        out.append(Joint(index=joint.index, parent_index=joint.parent_index,
                         rotation=quat_normalize(quat_mul(inv, g.rotation)),
                         translation=rotate_vec3(inv, delta)))
    return out


def _baked_animation(joints: List[Joint], frames, name: str) -> AnimationSection:
    """Baked world frames -> an AnimationSection whose tracks are DELTAS from ``joints``.

    ``build_animation_arrays`` composes each sample as ``trackRotation ⊗ bindRotation``
    and ``bindTranslation + trackTranslation``, so the deltas here are what makes the
    embedded clip land back on the locals we solved for. keyframe_duration 1.0 keeps one
    stored keyframe per played frame, so frame N of the export is frame N of the source."""
    tracks: Dict[int, AnimationTrack] = {}
    per_frame_locals = [locals_from_globals(joints, _globals_from_frame(f, len(joints)))
                        for f in frames]
    for joint in joints:
        rotations, translations, scales = [], [], []
        for locals_at in per_frame_locals:
            local = locals_at[joint.index]
            rotations.append(quat_normalize(quat_mul(local.rotation, quat_conjugate(joint.rotation))))
            translations.append(tuple(local.translation[i] - joint.translation[i] for i in range(3)))
            scales.append((1.0, 1.0, 1.0))
        tracks[joint.index] = AnimationTrack(joint_index=joint.index, rotations=rotations,
                                             translations=translations, scales=scales)
    return AnimationSection(name=name, num_joints=len(joints), num_frames=len(frames),
                            keyframe_duration=1.0, tracks=tracks)


def build_pose(sources: List[PoseSource], output_dir: Path, name: str = "pose",
               skeleton_dat: Optional[Path] = None,
               clip_sources: Optional[List[PoseSource]] = None,
               anim: Optional[str] = "idl", frame: int = 0, all_frames: bool = False,
               pose_file: Optional[Path] = None,
               occlusion: bool = True, draw_ranged: bool = False,
               fbx: bool = False, alpha_scale: float = DEFAULT_ALPHA_SCALE,
               mesh_merge_dp: int = 4, weld: bool = True,
               split_tex: bool = False) -> dict:
    """Merge every DAT in ``sources`` onto one skeleton and write ``<name>.glb`` (+ PNGs,
    + ``.fbx`` when asked) into ``output_dir``.

    ``sources`` are tagged with the slot they are worn in; the ``main``/``sub``/``ranged``
    ones are treated as weapons and attached to the hands. The skeleton comes from
    ``skeleton_dat`` when given, else from the first source that carries a 0x29 section
    (gear DATs carry none — they are all rigged against the shared race skeleton).

    Returns a summary dict: the files written, the parts kept, and — the point of the
    command — which pieces the worn set occluded away."""
    output_dir = Path(output_dir)
    for source in sources:
        _load(source)
    # Clip-only DATs (the companion motion packs) lend animation layers and nothing
    # else — they are never searched for geometry.
    for source in (clip_sources or []):
        _load(source)

    # ── Skeleton ────────────────────────────────────────────────────────────
    skeleton_source: Optional[PoseSource] = None
    if skeleton_dat is not None:
        skeleton_source = _load(PoseSource(path=Path(skeleton_dat), slot="skeleton"))
    else:
        skeleton_source = next(
            (s for s in sources
             if any(x.type_code == SECTION_TYPE_SKELETON for x in s.sections)), None)
    if skeleton_source is None:
        raise ValueError(
            "No skeleton found. Gear DATs carry no skeleton of their own — pass the race "
            "body DAT among the sources, or name it with --skeleton.")
    skel_section = next(s for s in skeleton_source.sections if s.type_code == SECTION_TYPE_SKELETON)
    joints = parse_skeleton(skeleton_source.data, skel_section)
    references = parse_skeleton_references(skeleton_source.data, skel_section)

    # ── Pose ────────────────────────────────────────────────────────────────
    pose_info: Optional[dict] = None
    animation = None
    baked = read_pose_file(pose_file) if pose_file else None
    if baked:
        # A viewer handed over the joints it had already evaluated, so there is nothing to
        # resolve: no clip to look up, no frame to sample, and no weapon re-parenting to
        # redo — the pose it sent has all of that in it. Solve the locals that reproduce
        # those worlds on the DAT hierarchy and the node tree agrees with the skin.
        # --frame still selects, so a pose file holding a whole clip can be exported at
        # one frame of it. A single-frame file simply clamps to the one frame it has.
        at = 0 if all_frames else max(0, min(int(frame), len(baked["frames"]) - 1))
        wanted = baked["frames"] if all_frames else [baked["frames"][at]]
        globals_by_joint = _globals_from_frame(wanted[0], len(joints))
        joints = locals_from_globals(joints, globals_by_joint)
        if all_frames and len(wanted) > 1:
            animation = _baked_animation(joints, wanted, baked["name"])
        overrides, weapon_notes = {}, []
        pose_info = {"anim": baked["name"], "layers": ["baked"], "source": "pose-file",
                     "frame": None if all_frames else at,
                     "frame_count": len(baked["frames"]), "all_frames": all_frames}
    elif anim:
        clip_pool = ([(skeleton_source.data, skeleton_source.sections)]
                     + [(s.data, s.sections) for s in sources if s is not skeleton_source]
                     + [(s.data, s.sections) for s in (clip_sources or [])])
        merged = merge_pose_clip(anim, clip_pool, len(joints))
        if merged is None:
            raise ValueError(f"Animation '{anim}' not found in any of the supplied DATs. "
                             f"Pass --anim '' to export the neutral bind pose.")
        animation, layers = merged
        # Frames are counted on the 30 fps PLAYBACK timeline, not in stored keyframes, so
        # --frame N is the frame a viewer shows at N and frame N of an --all-frames export.
        playback = animation_playback_frames(animation)
        pose_info = {"anim": animation.name, "layers": layers,
                     "frame": None if all_frames else max(0, min(int(frame), playback - 1)),
                     "frame_count": playback, "keyframes": animation.num_frames,
                     "all_frames": all_frames}
        # One frame is baked into the bind pose; a whole clip is embedded instead and the
        # joints stay at bind, because build_animation_arrays composes each track ONTO the
        # bind rotation — pose first and the clip would be applied twice.
        if not all_frames:
            joints = pose_joints_at_playback_frame(joints, animation, frame)

    if not baked:
        # Re-parent the drawn weapons' grip joints onto the hands. This rewrites the joint
        # HIERARCHY rather than just the world transforms, because build_gltf writes the
        # node tree from parent_index and a renderer recomputes every joint from that
        # tree — an override applied only to the globals looks right in the baked vertex
        # positions and then slides the weapon back off the hand once the file is opened.
        overrides, weapon_notes = _weapon_overrides(sources, references, draw_ranged)
        joints = apply_parent_overrides(joints, overrides)
        globals_by_joint = compute_global_transforms(joints)

    # ── Occlusion: what the worn set hides ──────────────────────────────────
    parts: List[_MeshPart] = []
    for source in sources:
        # A stowed ranged weapon is equipped but not rendered — the client scales it to
        # zero because nothing animates its back-mount bone. Drawn into the export, it
        # would hang at the character's feet in its bind pose.
        render = source.slot != "ranged" or draw_ranged
        seen = set()
        for section in source.sections:
            if section.type_code != SECTION_TYPE_SKELETON_MESH or section.name in seen:
                continue
            seen.add(section.name)
            parts.append(_MeshPart(source, section, mesh_occlude_type(source.data, section),
                                   render=render))
    if not parts:
        raise ValueError("None of the supplied DATs contain a mesh section.")

    # The occlusion union spans everything EQUIPPED, rendered or not.
    occl = {p.occlude_type for p in parts} if occlusion else set()

    meshes: List[Tuple[list, list]] = []
    textures: dict = {}
    kept_parts: List[dict] = []
    hidden: List[dict] = []
    stowed: List[dict] = []
    for part in parts:
        if not part.render:
            stowed.append({"slot": part.source.slot, "dat": rom_relative(part.source.path),
                           "mesh": part.section.name})
            continue
        vertices, primitives = parse_mesh(part.source.data, part.section)
        visible = [p for p in primitives if not occludes_display_type(p.display_type, occl)]
        dropped = len(primitives) - len(visible)
        if dropped:
            hidden.append({
                "slot": part.source.slot,
                "dat": rom_relative(part.source.path),
                "mesh": part.section.name,
                "pieces": dropped,
                "display_types": sorted({p.display_type for p in primitives
                                         if occludes_display_type(p.display_type, occl)}),
            })
        if not visible:
            continue
        meshes.append((vertices, visible))
        textures.update(parse_textures(part.source.data, part.source.sections))
        kept_parts.append({"slot": part.source.slot, "dat": rom_relative(part.source.path),
                           "mesh": part.section.name, "pieces": len(visible)})

    if not meshes:
        raise ValueError("Every piece was occluded away — nothing left to export. "
                         "Re-run with --keep-hidden to see the raw merge.")

    embedded = []
    if all_frames and animation is not None:
        # A re-parented grip is welded to its hand for the whole clip, so a track on it
        # would animate the weapon straight back off — and the idle clip does drive the
        # ranged mount. Drop those tracks; everything else animates as authored.
        import dataclasses
        kept = {j: t for j, t in animation.tracks.items() if j not in overrides}
        embedded = [dataclasses.replace(animation, tracks=kept)]

    written = build_gltf(Path(name), output_dir, joints, globals_by_joint, meshes, textures,
                         alpha_scale=alpha_scale, animations=(embedded or None),
                         mesh_merge_dp=mesh_merge_dp, weld=weld, split_tex=split_tex)
    glb_path = written[0]
    if fbx:
        written.append(convert_glb_to_fbx(glb_path, bake_anim=bool(embedded)))

    return {
        "ok": True,
        "glb": glb_path,
        "files": written,
        "skeleton": {"dat": rom_relative(skeleton_source.path), "joints": len(joints),
                     "references": len(references)},
        "pose": pose_info,
        "occlusion": occlusion,
        "occlude_types": sorted(hex(o) for o in occl if o),
        "parts": kept_parts,
        "hidden": hidden,
        "stowed": stowed,
        "weapons": weapon_notes,
        "textures": sorted(textures),
    }


# ---------------------------------------------------------------------------
# Source resolution: a look blob, a race + slot list, or bare DAT paths.
# ---------------------------------------------------------------------------

def sources_from_look(look_blob) -> Tuple[List[PoseSource], Path, str]:
    """A 20-byte ``look`` → (sources, race skeleton DAT, race name). Equipped looks only;
    a fixed-model NPC has no worn set to assemble, so ``xi gear character`` handles those."""
    look = parse_look(look_blob)
    if look["type"] != "equipped":
        raise ValueError(
            f"look is a fixed '{look['type']}' model (modelid {look.get('modelid')}), not a worn "
            f"character — use `xi gear character` for that.")
    race = look.get("raceName")
    if not race:
        raise ValueError(f"unknown race id {look.get('race')}")
    specs = ([("face", look["face"])] if look.get("face") else []) + list(look["slots"].items())
    sources = []
    for slot, model_id in specs:
        try:
            sources.append(PoseSource(path=resolve_gear_dat(race, slot, model_id), slot=slot))
        except (ValueError, FileNotFoundError) as exc:
            click.echo(f"  skipping {slot} model {model_id}: {exc}", err=True)
    return sources, race_skeleton_dat(race), race


def sources_from_slots(race: str, spec: str) -> Tuple[List[PoseSource], Path]:
    """``"face=1,head=12,body=12,main=20"`` → sources against a race's gear tables."""
    sources = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ValueError(f"expected slot=model_id, got {chunk!r}")
        slot, _, value = chunk.partition("=")
        slot = slot.strip().lower()
        if slot not in SLOTS:
            raise ValueError(f"unknown slot {slot!r}. Valid: {', '.join(SLOTS)}")
        try:
            model_id = int(value)
        except ValueError:
            raise ValueError(f"model id for {slot} must be an integer, got {value!r}")
        sources.append(PoseSource(path=resolve_gear_dat(race, slot, model_id), slot=slot))
    return sources, race_skeleton_dat(race)


def _resolve_dat(spec: str) -> Path:
    """A ROM-relative DAT path (``ROM/28/19``) or an absolute one → a real file."""
    path = Path(spec)
    if not path.is_absolute():
        candidate = Path(FFXI_DIR) / spec
        if candidate.suffix == "":
            candidate = candidate.with_suffix(".DAT")
        path = candidate
    elif path.suffix == "":
        path = path.with_suffix(".DAT")
    if not path.exists():
        raise FileNotFoundError(f"DAT not found: {path}")
    return path


# ---------------------------------------------------------------------------
# CLI — `xi gear pose`
# ---------------------------------------------------------------------------

@click.command("pose")
@click.argument("dats", nargs=-1)
@click.option("--look", "look_hex", default=None,
              help="Assemble from a 20-byte look hex (40 chars) or an npc id instead of DAT paths.")
@click.option("--race", default=None,
              help="Race for --slots (HumeMale HumeFemale ElvaanMale ElvaanFemale TaruMale "
                   "TaruFemale Mithra Galka). Also names the skeleton for bare DAT paths.")
@click.option("--slots", "slots_spec", default=None,
              help="Gear as slot=model_id pairs, e.g. face=1,head=12,body=12,legs=12,feet=12,main=20.")
@click.option("--main", "main_dats", multiple=True,
              help="Main-hand weapon DAT, attached to the right hand. Repeatable for a "
                   "multi-part weapon.")
@click.option("--sub", "sub_dats", multiple=True,
              help="Off-hand weapon DAT, attached to the left hand. Repeatable.")
@click.option("--ranged", "ranged_dats", multiple=True,
              help="Ranged weapon DAT. Stowed weapons are not drawn (the client scales them "
                   "to zero) — pass --draw-ranged to put it in the hand. Repeatable.")
@click.option("--skeleton", "skeleton_dat", default=None,
              help="Skeleton DAT to rig onto. Default: the first source that has one.")
@click.option("--anim-dat", "anim_dats", multiple=True,
              help="A DAT to take animation from but no geometry — the race's companion "
                   "motion packs, which carry the upper-body and waist layers of a clip. "
                   "Without them a pose is lower-body only. Repeatable.")
@click.option("-o", "--output", type=click.Path(file_okay=False), default=None,
              help="Output directory (default: exports/gear/pose/<name>/).")
@click.option("--name", default=None, help="Base name for the .glb (default: the race, look or first DAT).")
@click.option("--anim", default="idl", show_default=True,
              help="Animation tag to pose to; pass --anim '' for the neutral bind pose.")
@click.option("--frame", type=int, default=0, show_default=True,
              help="Frame of --anim to pose at, on the 30 fps playback timeline (the same "
                   "frame number a viewer shows), clamped to the clip's length.")
@click.option("--pose-file", "pose_file", type=click.Path(dir_okay=False), default=None,
              help="JSON of joint world transforms a viewer has already evaluated, used "
                   "instead of --anim/--frame. The way to export a pose no clip name can "
                   "name — a weapon-skill schedule, a battle stance, anything blended.")
@click.option("--all-frames", is_flag=True, default=False,
              help="Embed the WHOLE clip as an animation instead of freezing one frame, so the "
                   "export plays in a DCC. With --fbx the motion is baked into the FBX too.")
@click.option("--fbx", is_flag=True, default=False,
              help="Also convert to a texture-embedded .fbx via Blender.")
@click.option("--keep-hidden", is_flag=True, default=False,
              help="Skip occlusion culling and merge every piece, including the skin and hair the "
                   "worn gear covers. Useful to see what the culling removed.")
@click.option("--draw-ranged", is_flag=True, default=False,
              help="Draw the ranged weapon into the bow hand. Without this a stowed ranged "
                   "weapon is left out entirely, as the game leaves it.")
@click.option("--alpha-scale", type=float, default=DEFAULT_ALPHA_SCALE, show_default=True,
              help="Multiply texture alpha before export (FFXI stores it at half scale).")
@click.option("--mesh-merge-dp", type=int, default=4, show_default=True,
              help="Decimal places for vertex deduplication.")
@click.option("--weld/--no-weld", default=True, show_default=True,
              help="Weld vertices by world position + UV across all parts.")
@click.option("--split-tex", is_flag=True, default=False,
              help="Unmirror the skin into a stacked 2-up texture atlas and remap the UVs.")
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit the summary as JSON.")
def pose_cmd(dats, look_hex, race, slots_spec, main_dats, sub_dats, ranged_dats, skeleton_dat,
             anim_dats, output, name, anim, frame, pose_file, all_frames, fbx, keep_hidden,
             draw_ranged, alpha_scale, mesh_merge_dp, weld, split_tex, as_json):
    """Export a fully dressed character — every slot and the weapons — as one GLB/FBX.

    Unlike merging the DATs by hand, this drops the pieces the worn set hides: the
    body's bare wrists under sleeves, its hair under a helmet, its shins under boots.
    Weapons are re-parented into the hands the way the client draws them.

    \b
      # from the DATs the viewer has open (race body first, then each slot)
      xi gear pose ROM/27/82 ROM/27/88 ROM/27/111 ROM/28/19 ROM/28/64 ROM/28/96 ROM/29/0 \\
          --main ROM/29/29 --fbx
    \b
      # from a race + gear model ids
      xi gear pose --race HumeMale --slots face=1,head=12,body=12,hands=12,legs=12,feet=12,main=20
    \b
      # from an NPC's look blob (or an npc id, with the server DB up)
      xi gear pose --look 0x0100020114101920193019401950056000700000
      xi gear pose --keep-hidden --race Mithra --slots face=1,body=12   # see what culling removes
    
      # a specific frame, or the whole clip as a playable animation
      xi gear pose --race HumeMale --slots body=12 --anim wlk --frame 7
      xi gear pose --race HumeMale --slots body=12 --anim wlk --all-frames --fbx
    """
    sources: List[PoseSource] = []
    skel: Optional[Path] = Path(skeleton_dat) if skeleton_dat else None
    label = name

    try:
        if look_hex:
            from xi.gear.xi_character import _resolve_look_arg
            look_bytes, npcid = _resolve_look_arg(look_hex)
            sources, look_skel, look_race = sources_from_look(look_bytes)
            skel = skel or look_skel
            label = label or (f"npc_{npcid}" if npcid is not None else f"{look_race}_look")
        elif slots_spec:
            if not race:
                raise click.ClickException("--slots needs --race (which gear tables to read).")
            sources, slot_skel = sources_from_slots(race, slots_spec)
            skel = skel or slot_skel
            label = label or race
        elif not dats:
            raise click.ClickException(
                "Nothing to assemble. Pass DAT paths, or --look, or --race with --slots.")

        for spec in dats:
            sources.append(PoseSource(path=_resolve_dat(spec), slot="gear"))
        for slot, specs in (("main", main_dats), ("sub", sub_dats), ("ranged", ranged_dats)):
            for spec in specs:
                sources.append(PoseSource(path=_resolve_dat(spec), slot=slot))
        clips = [PoseSource(path=_resolve_dat(spec), slot="motion") for spec in anim_dats]
        if race and skel is None:
            skel = race_skeleton_dat(race)
    except (ValueError, FileNotFoundError) as exc:
        raise click.ClickException(str(exc))

    if not sources:
        raise click.ClickException("No source DATs resolved.")
    label = label or Path(sources[0].path).stem
    out_dir = Path(output) if output else Path("exports") / "gear" / "pose" / label

    try:
        result = build_pose(
            sources, out_dir, name=label, skeleton_dat=skel, clip_sources=clips,
            anim=(anim or None), frame=frame, all_frames=all_frames,
            pose_file=(Path(pose_file) if pose_file else None),
            occlusion=not keep_hidden, draw_ranged=draw_ranged, fbx=fbx, alpha_scale=alpha_scale,
            mesh_merge_dp=mesh_merge_dp, weld=weld, split_tex=split_tex)
    except (ValueError, FileNotFoundError) as exc:
        raise click.ClickException(str(exc))

    if as_json:
        click.echo(json.dumps(result, indent=2, default=str))
        return

    click.echo(f"Assembled pose → {result['glb']}")
    skeleton = result["skeleton"]
    click.echo(f"  skeleton {skeleton['dat']} · {skeleton['joints']} joints")
    if result["pose"]:
        p = result["pose"]
        where = ("all %d frames" % p["frame_count"]) if p["all_frames"]             else f"frame {p['frame']}/{max(p['frame_count'] - 1, 0)}"
        how = ("from the supplied pose file" if p.get("source") == "pose-file"
               else f"({len(p['layers'])} layer(s): {', '.join(p['layers'])})")
        click.echo(f"  {'embedded' if p['all_frames'] else 'posed to'} {p['anim']} {where}  {how}")
    for part in result["parts"]:
        click.echo(f"    {part['slot']:7} {part['mesh']:7} {part['pieces']:>3} piece(s)  {part['dat']}")
    for stow in result["stowed"]:
        click.echo(f"    {stow['slot']:7} {stow['mesh']:7} stowed — not drawn "
                   f"(the client scales it to zero; pass --draw-ranged to include it)")
    for weapon in result["weapons"]:
        if weapon.get("attached"):
            click.echo(f"    {weapon['slot']:7} attached joint {weapon['grip_joint']} "
                       f"→ hand joint {weapon['hand_joint']}")
        else:
            click.echo(f"    {weapon['slot']:7} NOT attached ({weapon['why']})")
    if result["occlusion"]:
        total = sum(h["pieces"] for h in result["hidden"])
        click.echo(f"  occlusion: {total} piece(s) hidden by the worn set "
                   f"(occludeTypes {', '.join(result['occlude_types']) or 'none'})")
        for h in result["hidden"]:
            click.echo(f"    hid {h['pieces']:>3} piece(s) of {h['slot']}/{h['mesh']} "
                       f"(displayTypes {h['display_types']})")
    else:
        click.echo("  occlusion: DISABLED (--keep-hidden) — covered skin and hair are still in the mesh")
    for path in result["files"][1:]:
        click.echo(f"  wrote {path}")

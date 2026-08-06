"""Assemble a renderable FFXI character (or fixed model) from an NPC ``look`` blob.

A cutscene actor's appearance is a 20-byte ``look`` ([[parse_look]] in
:mod:`xi.gear.xi_core`): either a single fixed model (monsters, objects) or a race +
equipment set. This module turns that into **one self-contained rigged ``.glb``** the
level editor can drop into the viewport:

* **standard** look → the entity model DAT (its own skeleton+mesh) via
  :func:`xi.entity.mesh.xi_export.export_dat`.
* **equipped** look → every worn gear slot merged onto the shared **race skeleton**
  (gear mesh DATs carry no skeleton of their own; their joint indices line up with the
  race body skeleton, which is exactly how the game rigs them). ``build_gltf`` already
  rigs a list of meshes to one skeleton, so assembly is loading each slot's mesh + the
  one skeleton and handing them over together.
"""

import base64
import io
import json
import math
import struct
from pathlib import Path
from typing import Optional

import click

from xi.xi_config import FFXI_DIR, read_path_for
from xi.ftable.xi_core import scan_file_ids
from xi.gear.xi_core import parse_look
from xi.gear.xi_export import resolve_gear_dat, race_skeleton_dat
from xi.entity.anim.xi_export import (
    GAME_FPS,
    ROOT_CORRECTION_ROTATION,
    SECTION_TYPE_SKELETON,
    SECTION_TYPE_SKELETON_MESH,
    SECTION_TYPE_SKELETON_ANIMATION,
    parse_sections,
    parse_skeleton,
    compute_global_transforms,
    parse_mesh,
    choose_animation,
    parse_animation,
    pose_joints_at_frame,
    resolve_corner_vertex,
    rigid_inverse_matrix,
    sample_track,
    quat_mul,
    quat_normalize,
    add_vec3,
)
from xi.entity.mesh.xi_export import build_gltf, parse_textures, material_texture_name

# Animation clips to embed for playback. The idle clip (looped by default in the viewer)
# is matched first against these names; then a few common motions so the scene can switch.
# (Race skeletons use 'stnd'; unique NPC models use 'idl0' + 'wlk0'/'run0'.)
_IDLE_CLIP_NAMES = ("idl0", "idl", "idle", "stnd", "ids0")
_EXTRA_CLIP_NAMES = ("wlk0", "walk", "run0", "run", "mov0", "dead")


def _load_external_clips(extra_clips) -> list:
    """Load event-resolved external motion clips → ``[AnimationSection]`` each RENAMED to its
    event tag (so the embedded glTF animation is named 'fg00', 'hiz0', … and the editor can
    play it by the timeline tag). ``extra_clips`` = ``{tag: {"file_id", "clip"}}`` from
    :func:`xi.event.xi_event.resolve_event_clips`. Joint indices that overshoot the target
    skeleton are dropped downstream by ``build_animation_arrays`` (so a slightly-different rig
    is safe). Best-effort: an unreadable/absent clip is skipped, not fatal."""
    import dataclasses
    out, cache = [], {}
    for tag, info in (extra_clips or {}).items():
        fid, clipname = info.get("file_id"), info.get("clip")
        if not fid or not clipname:
            continue
        if fid not in cache:
            hits = scan_file_ids([fid])
            if not hits:
                cache[fid] = (None, None)
            else:
                try:
                    data = read_path_for(Path(FFXI_DIR) / hits[0]["dat"]).read_bytes()
                    cache[fid] = (data, parse_sections(data))
                except Exception:
                    cache[fid] = (None, None)
        data, secs = cache[fid]
        if not data:
            continue
        sec = next((s for s in secs
                    if s.type_code == SECTION_TYPE_SKELETON_ANIMATION and s.name == clipname), None)
        if sec is None:
            continue
        try:
            out.append(dataclasses.replace(parse_animation(data, sec), name=tag))
        except Exception:
            pass
    return out


def _collect_clips(data: bytes, sections) -> tuple:
    """Parse EVERY skeleton-animation clip (0x2B) the DAT carries, idle first.

    Returns ``(clips, idle_name)``: the idle :class:`AnimationSection` first (so the viewer
    loops it by default), then every other clip (deduped by name) so ANY motion tag the model
    itself contains can play. This mirrors the real client — xim ``NpcModel`` exposes the whole
    model DAT as the animation pool (``getAnimationDirectories`` → all ``SkeletonAnimationResource``),
    NOT a hand-picked few; a tag with no matching clip just leaves the actor in its current pose
    (xim ``ActorModel.fetchAnimations`` returns empty). ``([], None)`` if the DAT has no clips."""
    seen, uniq = set(), []
    for s in sections:
        if s.type_code == SECTION_TYPE_SKELETON_ANIMATION and s.name not in seen:
            seen.add(s.name)
            uniq.append(s)
    if not uniq:
        return [], None
    low = {s.name.lower(): s for s in uniq}
    idle_name = next((low[n].name for n in _IDLE_CLIP_NAMES if n in low), None)
    ordered = ([low[idle_name.lower()]] if idle_name else []) + [s for s in uniq if s.name != idle_name]
    clips = []
    for sec in ordered:
        try:
            clips.append(parse_animation(data, sec))
        except Exception:
            pass
    return clips, idle_name


def list_look_animations(look_blob) -> dict:
    """Fast, geometry-free list of the animation clip tags a look's model/skeleton carries.

    Returns ``{ok, clips: [tag…], idle, type, ...}``. Standard NPCs (unique models like
    Maat) expose the 0x2B clips in their own model DAT; equipped NPCs expose the race
    skeleton's clips. Names only — no mesh/skeleton parsing, so it's cheap enough to call
    when the editor shows an animation dropdown."""
    from xi.gear.xi_core import parse_look

    def _names(sections):
        seen, names = set(), []
        for s in sections:
            if s.type_code == SECTION_TYPE_SKELETON_ANIMATION and s.name not in seen:
                seen.add(s.name)
                names.append(s.name)
        low = {n.lower(): n for n in names}
        idle = next((low[n] for n in _IDLE_CLIP_NAMES if n in low), None)
        return sorted(names), idle

    try:
        look = parse_look(look_blob)
    except Exception as exc:
        return {"ok": False, "error": f"bad look blob: {exc}"}

    if look["type"] != "equipped":
        modelid = look.get("modelid", 0)
        hits = scan_file_ids([model_file_id(modelid)])
        if not hits:
            return {"ok": False, "type": look["type"], "error": f"model {modelid} not in FTABLE"}
        dat = Path(FFXI_DIR) / hits[0]["dat"]
        if not dat.exists():
            return {"ok": False, "type": look["type"], "error": "model DAT not on disk"}
        data = read_path_for(dat).read_bytes()
        secs = parse_sections(data)
        clips, idle = _names(secs)
        return {"ok": True, "type": look["type"], "modelid": modelid, "clips": clips, "idle": idle,
                "motions": _model_motions(data)}

    race = look.get("raceName")
    if not race:
        return {"ok": False, "type": "equipped", "error": f"unknown race id {look.get('race')}"}
    secs = parse_sections(read_path_for(race_skeleton_dat(race)).read_bytes())
    clips, idle = _names(secs)
    return {"ok": True, "type": "equipped", "race": race, "clips": clips, "idle": idle}


def look_clip_dat(look_blob):
    """The DAT this look draws its 0x2B clips from — the fixed-NPC model DAT, or the race
    skeleton for an equipped look — resolved through ``read_path_for`` (so it points at the
    edited copy). Used to cache-key character data on the DAT's mtime, so re-importing a
    clip refreshes the editor preview without a server restart. ``None`` if unresolvable."""
    try:
        look = parse_look(look_blob)
        if look["type"] == "equipped":
            race = look.get("raceName")
            return read_path_for(race_skeleton_dat(race)) if race else None
        hits = scan_file_ids([model_file_id(look.get("modelid", 0))])
        return read_path_for(Path(FFXI_DIR) / hits[0]["dat"]) if hits else None
    except Exception:
        return None


def _model_motions(data: bytes) -> list:
    """The model's SCHEDULABLE motions: its own 0x07 scheduler routines, each resolved to
    the 0x2B clip it plays → ``[{tag, clip}]``.

    ★ These routine tags — NOT the raw 0x2B clip ids — are what the game can actually
    play on the actor: event opcode 0x2C (SetAction) fires an action FourCC against the
    entity's resident resources, and a survey of retail event DATs (9 zones) shows ONLY
    routine/action tags are ever scheduled (clp0/tlk0/dead/corp/ids0/sit2…), never a raw
    clip id like at00/idl0/mou4. This is also the "motions" list AltanaViewer shows.
    Chained routines (atk0 → 0x57 'vatk' → …) resolve through sub-routine refs, depth-
    capped. '@'-prefixed system routines (auto-turn @tl0/@tr0) are listed but callers
    must not map clips onto them (a wlk0 clip ref inside @tl0 does not make @tl0 "walk")."""
    from xi.event.xi_event import _scene_sections, _routine_sec2_commands, _match_clip
    try:
        secs = list(_scene_sections(data))
    except Exception:
        return []
    routines = sorted({t for _o, t, tc, _s in secs if tc == 0x07})
    rset = set(routines)

    def clip_of(tag, depth=0, seen=None):
        if depth > 3:
            return None
        seen = seen if seen is not None else set()
        if tag in seen:
            return None
        seen.add(tag)
        try:
            cmds = _routine_sec2_commands(data, tag)
        except Exception:
            return None
        for c in cmds:                                # direct animation command
            if c["op"] == 0x05 and c.get("ref"):
                m = _match_clip(secs, c["ref"])
                if m:
                    return m
        for c in cmds:                                # chained sub-routine (atk0 → vatk → …)
            r = c.get("ref")
            if r and r in rset:
                m = clip_of(r, depth + 1, seen)
                if m:
                    return m
        return None

    out = []
    for t in routines:
        c = clip_of(t)
        if c:
            out.append({"tag": t, "clip": c})
    return out


def _facing_axis(globals_by_joint) -> str:
    """Heuristic model facing axis from bind-pose joint spread. A humanoid is widest along its
    left-right (shoulder/arm) axis, so the SMALLER horizontal extent is the front-back / facing
    axis. Returns ``'x'`` or ``'z'``. Unique NPC models (e.g. Iroha 2449) are often authored
    facing Z while standard race/NPC models (e.g. Lion 60) face X — the viewer adds a 90° yaw
    correction for Z-facing models so one cast member isn't turned the wrong way."""
    if not globals_by_joint:
        return "x"
    xs = [g.translation[0] for g in globals_by_joint]
    ys = [g.translation[1] for g in globals_by_joint]
    zs = [g.translation[2] for g in globals_by_joint]
    ex, ey, ez = max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)
    # Only trust this for an upright humanoid (height is the dominant extent). For creatures /
    # monsters where a horizontal axis dominates, the shoulder-width assumption breaks — return
    # 'x' (no correction) so we never spin a model that's already facing correctly.
    if ey < max(ex, ez):
        return "x"
    return "z" if ez < ex else "x"


def model_file_id(modelid: int) -> int:
    """Entity model id → DAT file_id (FFXiMain.dll tiered formula).

    Delegates to :func:`xi.entity.xi_core.modelid_to_file_id`, the canonical mapping
    the ``dats`` / ``entity inject`` pipeline uses to *place* custom entity DATs — so
    preview resolves a model at the exact file id where it was injected. The lower tiers
    match xim's ``NpcTable.getNpcModelIndex``; the top tier there (boundary 3193 / base
    0x180F2) is marked "Speculated" in xim and is wrong for injected customs — the injector
    (boundary 3500 / base 98239, e.g. 25001 → 123240) is the in-game-verified one."""
    from xi.entity.xi_core import modelid_to_file_id
    return modelid_to_file_id(modelid)


def _rom_rel(p: Path) -> str:
    parts = list(Path(p).parts)
    for i, seg in enumerate(parts):
        if seg.upper() == "ROM" and i + 1 < len(parts):
            return "/".join(parts[i:])
    return Path(p).name


def _pose_idle(skel_data, skel_sections, joints, anim, frame):
    """Pose joints to an idle animation frame when the skeleton DAT has one, else bind pose."""
    if not anim:
        return joints
    try:
        sec = choose_animation(skel_sections, anim)
        animation = parse_animation(skel_data, sec)
        return pose_joints_at_frame(joints, animation, frame)
    except Exception:
        return joints


def build_character_glb(look_blob, output_dir, name: str = "character",
                        anim: Optional[str] = "idl", frame: int = 0,
                        extra_clips=None) -> dict:
    """Assemble a renderable character from a 20-byte ``look`` blob → one rigged ``.glb``.

    Returns ``{ok, glb, type, ...}``: for equipped, ``race`` + ``parts`` (resolved slots)
    + ``missing`` (slots that didn't resolve); for standard, ``modelid``. ``glb`` is the
    output :class:`Path`. Posed to ``anim`` frame ``frame`` (default idle) so the character
    stands naturally; pass ``anim=None`` for the neutral bind pose.

    ``extra_clips`` (``{tag: {"file_id", "clip"}}`` from
    :func:`xi.event.xi_event.resolve_event_clips`) embeds the cutscene's resolved motion
    clips, each named by its event tag, so the editor plays the right gesture per beat."""
    output_dir = Path(output_dir)
    look = parse_look(look_blob)

    # Fixed-model NPC (monster / object / casket): the entity DAT carries its own rig.
    if look["type"] != "equipped":
        modelid = look.get("modelid", 0)
        hits = scan_file_ids([model_file_id(modelid)])
        if not hits:
            return {"ok": False, "type": look["type"], "modelid": modelid,
                    "error": f"model {modelid} → file {model_file_id(modelid)} not in FTABLE"}
        dat = Path(FFXI_DIR) / hits[0]["dat"]
        if not dat.exists():
            return {"ok": False, "type": look["type"], "modelid": modelid,
                    "error": f"model DAT not on disk: {hits[0]['dat']}"}
        # A unique NPC model is split across SEVERAL named 0x2A part-sections (hh_l legs,
        # hh_b body, hf_h head, hh_h hands, wepN weapon …) — NOT LODs. export_dat only emits
        # one section (so you get half a character); instead merge them all onto the model's
        # own skeleton, exactly like the equipped path. Dedup by section name drops repeated
        # LOD copies (e.g. a second 'wep4').
        try:
            mdata = read_path_for(dat).read_bytes()
            msecs = parse_sections(mdata)
            skel_sec = next((s for s in msecs if s.type_code == SECTION_TYPE_SKELETON), None)
            if skel_sec is None:
                return {"ok": False, "type": look["type"], "modelid": modelid, "error": "no skeleton section"}
            base_joints = parse_skeleton(mdata, skel_sec)
            clips, idle_name = _collect_clips(mdata, msecs)
            clips = clips + _load_external_clips(extra_clips)   # event motion clips, named by tag
            # With embedded clips the viewer animates from the BIND pose; only pose-to-idle
            # statically when there are no clips (so it isn't a T-pose).
            joints = base_joints if clips else _pose_idle(mdata, msecs, base_joints, anim, frame)
            globals_by_joint = compute_global_transforms(joints)
            seen, mesh_secs = set(), []
            for s in msecs:
                # Skip weapon parts (wep*): FFXI only draws a weapon when it's actually wielded;
                # merging it unconditionally drops it on the floor at its bind-pose joint.
                if s.type_code == SECTION_TYPE_SKELETON_MESH and s.name not in seen and not s.name.lower().startswith("wep"):
                    seen.add(s.name)
                    mesh_secs.append(s)
            if not mesh_secs:
                return {"ok": False, "type": look["type"], "modelid": modelid, "error": "no mesh sections"}
            meshes = [parse_mesh(mdata, s) for s in mesh_secs]
            textures = parse_textures(mdata, msecs)
            out = build_gltf(Path(name), output_dir, joints, globals_by_joint, meshes, textures, animations=clips)
        except Exception as exc:
            return {"ok": False, "type": look["type"], "modelid": modelid, "error": str(exc)}
        glb = next((p for p in out if p.suffix == ".glb"), None)
        return {"ok": glb is not None, "glb": glb, "type": look["type"], "modelid": modelid,
                "dat": _rom_rel(dat), "parts": [s.name for s in mesh_secs],
                "facingAxis": _facing_axis(globals_by_joint),
                "clips": [c.name for c in clips], "idle": idle_name}

    # Equipped character: merge every worn slot onto the race skeleton.
    race = look["raceName"]
    if not race:
        return {"ok": False, "type": "equipped", "error": f"unknown race id {look['race']}"}
    skel_dat = race_skeleton_dat(race)
    skel_data = read_path_for(skel_dat).read_bytes()
    skel_sections = parse_sections(skel_data)
    skel_sec = next((s for s in skel_sections if s.type_code == SECTION_TYPE_SKELETON), None)
    if skel_sec is None:
        return {"ok": False, "type": "equipped", "race": race, "error": "no skeleton section"}
    base_joints = parse_skeleton(skel_data, skel_sec)
    clips, idle_name = _collect_clips(skel_data, skel_sections)   # race skeleton carries the motions
    clips = clips + _load_external_clips(extra_clips)             # event motion clips, named by tag
    joints = base_joints if clips else _pose_idle(skel_data, skel_sections, base_joints, anim, frame)
    globals_by_joint = compute_global_transforms(joints)

    # Slots to assemble: the face byte, then each worn equipment slot (model id = look low-12).
    specs = ([("face", look["face"])] if look.get("face") else []) + list(look["slots"].items())

    meshes, textures, parts, missing = [], {}, [], []
    for slot, mid in specs:
        try:
            gd = resolve_gear_dat(race, slot, mid)
            gdata = read_path_for(gd).read_bytes()
            gsections = parse_sections(gdata)
            msec = [s for s in gsections if s.type_code == SECTION_TYPE_SKELETON_MESH]
            if not msec:
                missing.append({"slot": slot, "model_id": mid, "why": "no mesh"})
                continue
            for s in msec:                                       # ALL sections, not just the first —
                meshes.append(parse_mesh(gdata, s))              # they're armour PARTS (chest/skirt/…), not LODs
            textures.update(parse_textures(gdata, gsections))
            parts.append({"slot": slot, "model_id": mid, "dat": _rom_rel(gd)})
        except Exception as exc:
            missing.append({"slot": slot, "model_id": mid, "why": str(exc)})

    if not meshes:
        return {"ok": False, "type": "equipped", "race": race,
                "error": "no gear meshes resolved", "missing": missing}

    out = build_gltf(Path(name), output_dir, joints, globals_by_joint, meshes, textures, animations=clips)
    glb = next((p for p in out if p.suffix == ".glb"), None)
    return {"ok": glb is not None, "glb": glb, "type": "equipped", "race": race,
            "parts": parts, "missing": missing, "facingAxis": _facing_axis(globals_by_joint),
            "clips": [c.name for c in clips], "idle": idle_name}


# ---------------------------------------------------------------------------
# Direct geometry/skeleton/animation data endpoint (no GLB intermediary).
# ---------------------------------------------------------------------------

def _pack_f32(values) -> str:
    return base64.b64encode(struct.pack(f'<{len(values)}f', *values)).decode()


def _pack_u16(values) -> str:
    return base64.b64encode(struct.pack(f'<{len(values)}H', *values)).decode()


def _encode_png(tex, is_equipped: bool = True) -> str:
    """Encode a TextureImage as a PNG data URI, expanding alpha from FFXI's half-scale.

    ``is_equipped`` gates the opaque-rescue below: it holds only for worn PC gear, whose
    alpha-0 texels are opaque skin/hair. Fixed-model monsters/entities use alpha 0 as a
    real cutout, so the rescue must be skipped for them (see below)."""
    import numpy as np
    from PIL import Image
    from xi.utils.xi_core import scale_alpha, DEFAULT_ALPHA_SCALE
    rgba = np.frombuffer(scale_alpha(tex.rgba, DEFAULT_ALPHA_SCALE), dtype=np.uint8)
    rgba = rgba.reshape(tex.height, tex.width, 4).copy()
    # EQUIPPED PC models store OPAQUE skin/hair/armor with alpha 0 — there is NO black-mask
    # cutout (verified: every alpha-0 texel is a coloured skin/hair pixel). A straight alpha
    # channel then drops the whole face/skin, which the frontend's alphaTest discards → the
    # body/face goes invisible for many races (Hume male hm_m41_3, Elvaan ef_m41_3, …). Force
    # any NON-BLACK pixel that came through fully transparent back to opaque; a true (0,0,0,0)
    # mask texel stays clear.
    #
    # Fixed-model MONSTERS/entities (byakko, casket, …) are the opposite: their alpha-0 texels
    # ARE genuine cutouts (mouth interior, jaw/mane fringe) painted over WHITE rgb. Applying the
    # rescue there turns every cutout into an opaque white patch (the in-app editor bug this
    # gates). So only rescue equipped gear; monsters keep their alpha-0 cutout transparent.
    if is_equipped:
        fix = (rgba[:, :, 3] == 0) & rgba[:, :, :3].any(axis=2)
        rgba[fix, 3] = 255
    # Alpha-bleed the cutout edges: flood visible-texel RGB outward into the fully
    # transparent texels. A monster cutout is painted over WHITE rgb, so at a cutout edge
    # the frontend's LinearFilter blends an opaque edge texel with its white transparent
    # neighbour → a white halo (uglier than the zone foliage, which has matching rgb behind
    # its cutouts). Bleeding the neighbouring colour in kills the contrast so the edge
    # samples tiger-colour, not white. Alpha is untouched — bled texels stay discarded.
    _bleed_transparent_rgb(rgba)
    img = Image.fromarray(rgba, "RGBA")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _bleed_transparent_rgb(rgba, passes: int = 4) -> None:
    """In-place flood of visible-texel RGB into fully-transparent (alpha-0) neighbours,
    a few texels deep. Removes the coloured halo that linear filtering / mipmaps otherwise
    pull from a cutout's background rgb. Only RGB changes; the alpha channel is preserved."""
    import numpy as np
    alpha = rgba[:, :, 3]
    filled = alpha > 0
    if filled.all() or not filled.any():
        return
    rgb = rgba[:, :, :3].astype(np.float32)
    for _ in range(passes):
        holes = ~filled
        if not holes.any():
            break
        acc = np.zeros_like(rgb)
        cnt = np.zeros(alpha.shape, np.float32)
        # 4-connected neighbour accumulation via slicing (no np.roll → no wrap across the
        # texture border, which would smear opposite UV edges into each other).
        acc[1:] += np.where(filled[:-1, :, None], rgb[:-1], 0.0);  cnt[1:] += filled[:-1]
        acc[:-1] += np.where(filled[1:, :, None], rgb[1:], 0.0);  cnt[:-1] += filled[1:]
        acc[:, 1:] += np.where(filled[:, :-1, None], rgb[:, :-1], 0.0);  cnt[:, 1:] += filled[:, :-1]
        acc[:, :-1] += np.where(filled[:, 1:, None], rgb[:, 1:], 0.0);  cnt[:, :-1] += filled[:, 1:]
        newly = holes & (cnt > 0)
        rgb[newly] = acc[newly] / cnt[newly][:, None]
        filled |= newly
    rgba[:, :, :3] = np.clip(rgb + 0.5, 0, 255).astype(np.uint8)


def _build_mesh_data(meshes, globals_by_joint) -> list:
    """Flatten each (vertices, primitives) mesh tuple → packed geometry arrays via resolve_corner_vertex."""
    # Accumulate across all mesh slots keyed by texture name, then pack once per texture.
    by_mat: dict = {}
    for vertices, primitives in meshes:
        for prim in primitives:
            mat = material_texture_name(prim.material_name)
            g = by_mat.setdefault(mat, {"pos": [], "nrm": [], "uv": [], "ji": [], "jw": []})
            # corners are already in triangle order (groups of 3)
            for corner in prim.corners:
                vertex = vertices[corner.vertex_index]
                pos, nrm, joints, weights = resolve_corner_vertex(vertex, globals_by_joint, corner.mirrored)
                g["pos"].extend(pos)
                g["nrm"].extend(nrm)
                g["uv"].extend(corner.uv or (0.0, 0.0))
                g["ji"].extend(joints)
                g["jw"].extend(weights)
    out = []
    for mat, g in by_mat.items():
        if not g["pos"]:
            continue
        out.append({
            "material": mat,
            "count": len(g["pos"]) // 3,
            "positions": _pack_f32(g["pos"]),
            "normals": _pack_f32(g["nrm"]),
            "uvs": _pack_f32(g["uv"]),
            "skinIndices": _pack_u16(g["ji"]),
            "skinWeights": _pack_f32(g["jw"]),
        })
    return out


def _build_anim_data(joints, animations) -> dict:
    """Sample every animation at GAME_FPS → per-joint packed float32 tracks."""
    out = {}
    for animation in animations:
        length_frames = max(1.0, (animation.num_frames - 1) / animation.keyframe_duration)
        num_frames = int(math.ceil(length_frames)) + 1
        tracks = []
        for joint_index, track in sorted(animation.tracks.items()):
            if joint_index >= len(joints):
                continue
            bind = joints[joint_index]
            rots, trans = [], []
            for i in range(num_frames):
                r, t, _ = sample_track(track, animation.keyframe_duration, float(i))
                q = quat_normalize(quat_mul(r, bind.rotation))
                v = add_vec3(bind.translation, t)
                rots.extend(q)   # x,y,z,w
                trans.extend(v)
            tracks.append({
                "joint": joint_index,
                "rots": _pack_f32(rots),
                "trans": _pack_f32(trans),
            })
        # Key by the STRIPPED name: a short custom clip is stored NUL/space-padded to 4
        # bytes ('tlk\x00'), but the routine→clip map the editor resolves against uses the
        # stripped name ('tlk'). Keying raw would make actions['tlk\x00'] unreachable, so a
        # padded custom clip silently never plays (4-char stock clips are unaffected).
        out[animation.name.rstrip("\x00 ")] = {"fps": GAME_FPS, "numFrames": num_frames, "tracks": tracks}
    return out


def _parse_info_scale(data: bytes, sections) -> dict:
    """The model's ``info`` section scale bytes → ``{"scale", "staticNpcScale"}`` (percent,
    None when 0xFF/absent).

    Layout (xim InfoSection / ue5 DatParser, both byte-identical): 16-byte body,
    ``scale`` u8 @ +0x0A, ``staticNpcScale`` u8 @ +0x0B, 0xFF = unset. The client
    renders a CHARACTER actor at ``scale/100`` (retail humanoid NPCs are mostly 95,
    monsters 77-88; Byakko 85) — ``staticNpcScale`` (~always 100) applies to models
    placed as static objects (furniture/doors/sit-chairs). Ignoring this made every
    editor NPC render at 1.0 → visibly larger than in game."""
    for s in sections:
        if s.name == "info":
            body = data[s.data_start:s.data_start + 16]
            if len(body) < 16:
                break
            sc, st = body[0x0A], body[0x0B]
            return {"scale": None if sc == 0xFF else sc,
                    "staticNpcScale": None if st == 0xFF else st}
    return {"scale": None, "staticNpcScale": None}


def build_character_data(look_blob, extra_clips=None) -> dict:
    """Assemble a character from a 20-byte ``look`` blob → raw geometry/skeleton/animation JSON.

    Returns a dict with ``ok``, ``type``, ``facingAxis``, ``idle``, ``skeleton``,
    ``inverseBindMatrices``, ``meshes``, ``textures``, ``animations`` — everything
    Three.js needs to build a SkinnedMesh directly, with no GLB intermediary.

    ``extra_clips`` (``{tag: {"file_id", "clip"}}``) embeds resolved cutscene motion clips."""
    look = parse_look(look_blob)

    # ── Standard (fixed model) path ──────────────────────────────────────────
    if look["type"] != "equipped":
        modelid = look.get("modelid", 0)
        hits = scan_file_ids([model_file_id(modelid)])
        if not hits:
            return {"ok": False, "type": look["type"], "modelid": modelid,
                    "error": f"model {modelid} → file {model_file_id(modelid)} not in FTABLE"}
        dat = Path(FFXI_DIR) / hits[0]["dat"]
        if not dat.exists():
            return {"ok": False, "type": look["type"], "modelid": modelid,
                    "error": f"model DAT not on disk: {hits[0]['dat']}"}
        try:
            mdata = read_path_for(dat).read_bytes()
            msecs = parse_sections(mdata)
            skel_sec = next((s for s in msecs if s.type_code == SECTION_TYPE_SKELETON), None)
            if skel_sec is None:
                return {"ok": False, "type": look["type"], "modelid": modelid, "error": "no skeleton section"}
            base_joints = parse_skeleton(mdata, skel_sec)
            clips, idle_name = _collect_clips(mdata, msecs)
            clips = clips + _load_external_clips(extra_clips)
            joints = base_joints if clips else _pose_idle(mdata, msecs, base_joints, "idl", 0)
            globals_by_joint = compute_global_transforms(joints)
            seen, mesh_secs = set(), []
            for s in msecs:
                if s.type_code == SECTION_TYPE_SKELETON_MESH and s.name not in seen and not s.name.lower().startswith("wep"):
                    seen.add(s.name)
                    mesh_secs.append(s)
            if not mesh_secs:
                return {"ok": False, "type": look["type"], "modelid": modelid, "error": "no mesh sections"}
            meshes = [parse_mesh(mdata, s) for s in mesh_secs]
            textures = parse_textures(mdata, msecs)
        except Exception as exc:
            return {"ok": False, "type": look["type"], "modelid": modelid, "error": str(exc)}
        info_scale = _parse_info_scale(mdata, msecs)
        return _assemble_character_data(
            look["type"], joints, globals_by_joint, meshes, textures, clips, idle_name,
            modelid=modelid, facingAxis=_facing_axis(globals_by_joint),
            infoScale=info_scale,
            # The render scale the client applies to a CHARACTER actor (see
            # _parse_info_scale). The editor multiplies the model root by this.
            npcScale=(info_scale["scale"] or 100) / 100.0)

    # ── Equipped path ─────────────────────────────────────────────────────────
    race = look["raceName"]
    if not race:
        return {"ok": False, "type": "equipped", "error": f"unknown race id {look['race']}"}
    skel_dat = race_skeleton_dat(race)
    skel_data = read_path_for(skel_dat).read_bytes()
    skel_sections = parse_sections(skel_data)
    skel_sec = next((s for s in skel_sections if s.type_code == SECTION_TYPE_SKELETON), None)
    if skel_sec is None:
        return {"ok": False, "type": "equipped", "race": race, "error": "no skeleton section"}
    base_joints = parse_skeleton(skel_data, skel_sec)
    clips, idle_name = _collect_clips(skel_data, skel_sections)
    clips = clips + _load_external_clips(extra_clips)
    joints = base_joints if clips else _pose_idle(skel_data, skel_sections, base_joints, "idl", 0)
    globals_by_joint = compute_global_transforms(joints)

    specs = ([("face", look["face"])] if look.get("face") else []) + list(look["slots"].items())
    meshes, textures, missing = [], {}, []
    info_scale = {"scale": None, "staticNpcScale": None}
    for slot, mid in specs:
        try:
            gd = resolve_gear_dat(race, slot, mid)
            gdata = read_path_for(gd).read_bytes()
            gsections = parse_sections(gdata)
            if slot == "feet":
                # The info (movement/scale) section rides the FEET model on equipped
                # rigs (xim getFootInfoDefinition) — footsteps live there too.
                info_scale = _parse_info_scale(gdata, gsections)
            msec = [s for s in gsections if s.type_code == SECTION_TYPE_SKELETON_MESH]
            if not msec:
                missing.append({"slot": slot, "model_id": mid, "why": "no mesh"})
                continue
            # A gear DAT can hold SEVERAL mesh sections (e.g. body armour = chest + skirt +
            # shoulders + …). Parsing only msec[0] drops the rest → the torso renders as a tiny
            # sliver ("no body"). Parse every mesh section in the slot.
            for s in msec:
                meshes.append(parse_mesh(gdata, s))
            textures.update(parse_textures(gdata, gsections))
        except Exception as exc:
            missing.append({"slot": slot, "model_id": mid, "why": str(exc)})

    if not meshes:
        return {"ok": False, "type": "equipped", "race": race,
                "error": "no gear meshes resolved", "missing": missing}
    return _assemble_character_data(
        "equipped", joints, globals_by_joint, meshes, textures, clips, idle_name,
        race=race, missing=missing, facingAxis=_facing_axis(globals_by_joint),
        infoScale=info_scale, npcScale=(info_scale["scale"] or 100) / 100.0)


def _assemble_character_data(char_type, joints, globals_by_joint, meshes, textures,
                              clips, idle_name, facingAxis="x", **meta) -> dict:
    """Serialize parsed character data into a JSON-serializable dict."""
    # Skeleton
    skeleton = [
        {"index": i, "parent": j.parent_index, "rot": list(j.rotation), "trans": list(j.translation)}
        for i, j in enumerate(joints)
    ]
    # Inverse bind matrices (column-major mat4 per joint)
    ibm_flat = []
    for g in globals_by_joint:
        ibm_flat.extend(rigid_inverse_matrix(g.rotation, g.translation))
    # Geometry
    try:
        mesh_data = _build_mesh_data(meshes, globals_by_joint)
    except Exception as exc:
        return {"ok": False, "type": char_type, "error": f"mesh build failed: {exc}", **meta}
    # Textures → PNG data URIs
    tex_data = {}
    try:
        is_equipped = (char_type == "equipped")
        for name, tex in textures.items():
            tex_data[name] = _encode_png(tex, is_equipped)
    except Exception as exc:
        return {"ok": False, "type": char_type, "error": f"texture encode failed: {exc}", **meta}
    # Animations
    try:
        anim_data = _build_anim_data(joints, clips)
    except Exception as exc:
        return {"ok": False, "type": char_type, "error": f"anim build failed: {exc}", **meta}

    return {
        "ok": True,
        "type": char_type,
        "facingAxis": facingAxis,
        "idle": idle_name.rstrip("\x00 ") if idle_name else idle_name,
        "skeleton": skeleton,
        "inverseBindMatrices": _pack_f32(ibm_flat),
        "meshes": mesh_data,
        "textures": tex_data,
        "animations": anim_data,
        **meta,
    }


# ---------------------------------------------------------------------------
# CLI — `xi entity look` (decode a look blob) and `xi gear character` (assemble it).
# ---------------------------------------------------------------------------

def _look_from_npcid(npcid: int) -> bytes:
    """Fetch a 20-byte ``look`` from the server ``npc_list`` by npc id."""
    from xi.server.xi_commands import _resolve, _connect
    h, p, u, pw, db = _resolve(None, None, None, None, None)
    try:
        conn = _connect(h, p, u, pw, db)
    except Exception as exc:
        raise click.ClickException(
            f"server DB connection failed ({exc}). An npc id needs the server DB — "
            f"start the server, or pass a 20-byte look hex instead.")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT look FROM npc_list WHERE npcid=%s", (int(npcid),))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        raise click.ClickException(f"npc {npcid} (0x{int(npcid):08X}) not found in npc_list")
    return bytes(row[0])


def _resolve_look_arg(target: str):
    """Resolve a ``TARGET`` (a 40-char ``look`` hex, or an npc id dec/0x-hex) → ``(look_bytes,
    npcid_or_None)``. A 20-byte blob is taken verbatim; anything else is an npc id (DB lookup)."""
    t = target.strip()
    h = t[2:] if t.lower().startswith("0x") else t
    if len(h) == 40 and all(c in "0123456789abcdefABCDEF" for c in h):
        return bytes.fromhex(h), None                    # a 20-byte look blob
    try:
        npcid = int(t, 0)
    except ValueError:
        raise click.ClickException(
            f"{target!r} is neither a 20-byte look hex (40 chars) nor an npc id")
    return _look_from_npcid(npcid), npcid


@click.command("look")
@click.argument("target")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
@click.option("--resolve", "do_resolve", is_flag=True,
              help="Resolve each equipped slot (and the skeleton) to its DAT path.")
def look_cmd(target, as_json, do_resolve):
    """Decode an NPC ``look`` appearance blob → model type / race / equipment slots.

    \b
    TARGET is a 20-byte look hex, or an npc id (needs the server DB):
      xi entity look 0x0100020114101920193019401950056000700000
      xi entity look 17531170 --resolve     # Wolfgang, with each slot's gear DAT
    """
    look_bytes, npcid = _resolve_look_arg(target)
    info = parse_look(look_bytes)

    resolved = {}
    skel = None
    if do_resolve and info["type"] == "equipped":
        skel = _rom_rel(race_skeleton_dat(info["raceName"])) if info.get("raceName") else None
        specs = ([("face", info["face"])] if info.get("face") else []) + list(info["slots"].items())
        for slot, mid in specs:
            try:
                resolved[slot] = _rom_rel(resolve_gear_dat(info["raceName"], slot, mid))
            except Exception as exc:
                resolved[slot] = f"(unresolved: {exc})"

    if as_json:
        out = dict(info)
        if npcid is not None:
            out["npcid"] = npcid
        if do_resolve:
            out["skeleton"] = skel
            out["slot_dats"] = resolved
        click.echo(json.dumps(out, indent=2))
        return

    who = f"npc {npcid} (0x{npcid:08X})" if npcid is not None else f"look {look_bytes.hex()}"
    click.echo(who)
    click.echo(f"  type: {info['type']}")
    if info["type"] == "equipped":
        click.echo(f"  race: {info.get('raceName')} ({info['race']})   face: {info['face']}")
        if skel:
            click.echo(f"  skeleton: {skel}")
        click.echo("  slots:")
        for slot, mid in info["slots"].items():
            tail = f"   {resolved[slot]}" if slot in resolved else ""
            click.echo(f"    {slot:6} {mid:>3}{tail}")
    else:
        click.echo(f"  modelid: {info.get('modelid')}")


@click.command("character")
@click.argument("target")
@click.option("-o", "--output", type=click.Path(file_okay=False), default=None,
              help="Output directory (default: exports/gear/character/<name>/).")
@click.option("--name", default=None, help="Base name for the .glb (default from the npc id / look).")
@click.option("--anim", default="idl", show_default=True,
              help="Idle animation tag to pose to; pass --anim '' for the neutral bind pose.")
@click.option("--frame", type=int, default=0, show_default=True, help="Animation frame to pose at.")
def character_cmd(target, output, name, anim, frame):
    """Assemble an NPC's full 3D model from its ``look`` → one rigged ``.glb``.

    Equipped characters merge every worn gear slot onto the race skeleton; fixed-model NPCs
    (monsters, objects) export their entity model. TARGET is a 20-byte look hex or an npc id.

    \b
      xi gear character 17531170                 # Wolfgang → exports/gear/character/npc_17531170/
      xi gear character 0x0000C50300000000000000000000000000000000   # a fixed model
      xi gear character 17531170 --anim '' -o out/wolfgang
    """
    look_bytes, npcid = _resolve_look_arg(target)
    nm = name or (f"npc_{npcid}" if npcid is not None else f"look_{look_bytes.hex()[:12]}")
    out_dir = Path(output) if output else Path("exports") / "gear" / "character" / nm
    r = build_character_glb(look_bytes, out_dir, name=nm, anim=(anim or None), frame=frame)
    if not r.get("ok"):
        raise click.ClickException(r.get("error", "could not assemble model"))
    click.echo(f"Assembled {r['type']} model → {r['glb']}")
    if r["type"] == "equipped":
        tail = f", {len(r['missing'])} missing" if r.get("missing") else ""
        click.echo(f"  race {r['race']} · {len(r['parts'])} part(s){tail}")
        for part in r["parts"]:
            click.echo(f"    {part['slot']:6} model {part['model_id']:>3}  {part['dat']}")
        for m in r.get("missing", []):
            click.echo(f"    {m['slot']:6} model {m.get('model_id')}  MISSING ({m.get('why')})")
    else:
        click.echo(f"  modelid {r.get('modelid')}  ({r.get('dat')})")

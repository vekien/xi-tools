"""The ``anim`` action of ``xi dats`` (schema/anim.json): a clip in a model DAT, and the
``0x07`` routine that plays it, so the server (``entityAnimationPacket``) or a cutscene
(``0x2C SetAction``) can fire it by its tag.

The clip comes from a glTF (an ``xi anim export``, edited or authored in a DCC tool,
imported as ``xi anim import --add`` does) or straight out of another DAT (``source``: a
cutscene gesture bank's clip, its bytes copied under the new name). The routine is cloned
from the model's simplest single-clip routine, as ``xi anim schedule add`` does.

Every build target gets the edit on its own copy of the DAT: the install's (``.base``
kept), the pivot folder's (the install's copy when it has none), and with ``xi dats build
--hd`` the HD folder's (FFXI_HD_DIR) own copy, the HD pack's DAT that the client loads in
place of the install's. That copy is never replaced by the install's: the clip and the
routine go into its own bytes and its untouched bytes are kept as ``.base`` beside it
(``xi_config.hd_editable_dat``). A target with no copy of its own in the HD folder is left
alone there: the client reads the install's.

A build records, per target, each section it wrote (a clip or a routine, with the SHA-1
of the bytes written). ``undo`` puts back the section the target's untouched copy holds
under that name, or takes the section out when it holds none, and leaves a section that
changed since alone. A rebuild first does the same for what the last build wrote, so the
DAT ends up the same however often it is built.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

CLIP, ROUTINE, SKELETON = 0x2B, 0x07, 0x29
_KIND_TC = {"clip": CLIP, "routine": ROUTINE}
ACTION_KEYS = {"id", "type", "enabled", "description", "depends_on", "outputs",
               "target", "resources", "source", "options", "schedule", "result"}
_OPTION_KEYS = {"template_anim", "keep_routine_duration", "fps", "skeleton_dat"}
_SCHEDULE_KEYS = {"tag", "loops", "blend"}
_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_NAME = re.compile(r"^[A-Za-z0-9_]{1,4}$")
_ROM_DAT = re.compile(r"^ROM[0-9]*/[0-9]+/[0-9]+\.DAT$", re.I)
DEFAULT_LOOPS = 0          # as `xi anim schedule add`: loop until something else plays
DEFAULT_BLEND = 15


class AnimBuildError(ValueError):
    """An anim action that can't be built as written; the message says what to change."""


# ── the action ───────────────────────────────────────────────────────────────

def rom_rel(value) -> str | None:
    """``ROM/3/59`` / ``rom\\3\\59.dat`` -> ``ROM/3/59.DAT``; None when it isn't a ROM DAT path."""
    if not isinstance(value, str):
        return None
    s = value.strip().replace("\\", "/").strip("/")
    if not s.upper().endswith(".DAT"):
        s += ".DAT"
    if not _ROM_DAT.match(s):
        return None
    rom, folder, name = s.split("/")
    return f"{rom.upper()}/{folder}/{name.split('.')[0]}.DAT"


def clip_name(action: dict) -> str:
    """The clip's 4-char name in the model: a digit-less name is slot 0 (``gbw`` -> ``gbw0``),
    as ``xi anim import`` names it."""
    from xi.entity.anim.xi_import import normalize_anim_target
    return normalize_anim_target(str((action.get("target") or {}).get("animation", "")))


def schedule_of(action: dict) -> dict | None:
    """The routine an action asks for: ``{tag, loops, blend}``, ``None`` for none, or
    ``{"auto": True, …}`` when ``schedule`` is left out (a routine for a new clip only)."""
    from xi.entity.anim.xi_schedule import _norm_routine_tag
    sched = action.get("schedule")
    if sched is False:
        return None
    s = sched if isinstance(sched, dict) else {}
    return {"tag": _norm_routine_tag(str(s.get("tag") or clip_name(action))),
            "loops": s.get("loops", DEFAULT_LOOPS), "blend": s.get("blend", DEFAULT_BLEND),
            "auto": not isinstance(sched, dict)}


def tables(action: dict) -> list[str]:
    """The DAT the action edits in place: what ``xi dats build --reset`` resets for it."""
    rel = rom_rel((action.get("target") or {}).get("dat"))
    return [rel] if rel else []


def _uint(v, hi: int) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= hi


def validate_action(action) -> list[str]:
    """What's wrong with an anim action, one line per problem (empty when it's fine)."""
    if not isinstance(action, dict):
        return ["an anim action is an object"]
    errs = [f"unknown key {k!r}" for k in action if k not in ACTION_KEYS]
    if not isinstance(action.get("id"), str) or not _ID.match(action["id"]):
        errs.append("id must be lowercase letters, digits, '_', '.' or '-'")
    if action.get("type") != "anim":
        errs.append("type must be 'anim'")
    target = action.get("target")
    if not isinstance(target, dict) or set(target) - {"dat", "animation"}:
        errs.append('target must be {"dat": <the model DAT, like ROM/3/59.DAT>, "animation": <the clip name>}')
    else:
        if rom_rel(target.get("dat")) is None:
            errs.append(f"target.dat {target.get('dat')!r} must be a ROM DAT path like ROM/3/59.DAT")
        if not isinstance(target.get("animation"), str) or not _NAME.match(target["animation"]):
            errs.append("target.animation must be the clip's name in the model: 1-4 letters, digits or '_' "
                        "(gbw0; a digit-less name is slot 0)")
    res, src = action.get("resources"), action.get("source")
    if (res is None) == (src is None):
        errs.append('give the clip either as "resources": {"animation": <a .gltf>} '
                    'or as "source": {"dat": <a DAT>, "clip": <its name there>}')
    if res is not None:
        if not isinstance(res, dict) or set(res) - {"animation"}:
            errs.append('resources must be {"animation": <a .gltf>}')
        elif not isinstance(res.get("animation"), str) or not res["animation"].lower().endswith(".gltf"):
            errs.append("resources.animation must name a .gltf (xi anim export writes one; a .glb isn't read)")
    if src is not None:
        if not isinstance(src, dict) or set(src) - {"dat", "clip"}:
            errs.append('source must be {"dat": <a DAT>, "clip": <its name there>}')
        else:
            if rom_rel(src.get("dat")) is None:
                errs.append(f"source.dat {src.get('dat')!r} must be a ROM DAT path like ROM6/0/4.DAT")
            if not isinstance(src.get("clip"), str) or not _NAME.match(src["clip"]):
                errs.append("source.clip must be the clip's name in source.dat (1-4 letters, digits or '_')")
    opts = action.get("options")
    if opts is not None:
        if not isinstance(opts, dict):
            errs.append("options must be an object")
        else:
            errs += [f"options: unknown key {k!r}" for k in opts if k not in _OPTION_KEYS]
            if "template_anim" in opts and not (isinstance(opts["template_anim"], str)
                                                and _NAME.match(opts["template_anim"])):
                errs.append("options.template_anim must be a clip name in the model (idl)")
            if "keep_routine_duration" in opts and not isinstance(opts["keep_routine_duration"], bool):
                errs.append("options.keep_routine_duration must be true or false")
            fps = opts.get("fps")
            if "fps" in opts and not (isinstance(fps, (int, float)) and not isinstance(fps, bool) and fps > 0):
                errs.append("options.fps must be a number above 0")
            if "skeleton_dat" in opts and rom_rel(opts["skeleton_dat"]) is None:
                errs.append("options.skeleton_dat must be a ROM DAT path (the model whose skeleton the glTF "
                            "was exported on)")
    sched = action.get("schedule")
    if sched is not None and sched is not False:
        if not isinstance(sched, dict):
            errs.append('schedule must be false (no routine) or {"tag", "loops", "blend"}')
        else:
            errs += [f"schedule: unknown key {k!r}" for k in sched if k not in _SCHEDULE_KEYS]
            if "tag" in sched and not (isinstance(sched["tag"], str) and _NAME.match(sched["tag"])):
                errs.append("schedule.tag must be 1-4 letters, digits or '_' (the tag the server fires)")
            if "loops" in sched and not _uint(sched["loops"], 0xFFFF):
                errs.append("schedule.loops must be 0 (loop until something else plays) to 65535")
            if "blend" in sched and not _uint(sched["blend"], 0xFFFF):
                errs.append("schedule.blend must be 0 to 65535 frames (30 a second)")
    return errs


# ── sections ─────────────────────────────────────────────────────────────────

def _label(name: str) -> str:
    return name.split("\x00", 1)[0].rstrip(" ")


def _find(data: bytes | None, tc: int, name: str):
    """The first section of type ``tc`` named ``name`` (exact, as the client matches tags)."""
    if not data:
        return None
    from xi.entity.anim.xi_export import parse_sections
    return next((s for s in parse_sections(data) if s.type_code == tc and _label(s.name) == name), None)


def _bytes(data: bytes, s) -> bytes:
    return data[s.start:s.start + s.size]


def _sha1(blob: bytes) -> str:
    return hashlib.sha1(blob).hexdigest()


def _replace(data: bytes, s, blob: bytes) -> bytes:
    return data[:s.start] + blob + data[s.start + s.size:]


def _put_clip(data: bytes, rel: str, blob: bytes, name: str) -> bytes:
    """Write clip ``name``: over the one there, else after the model's last clip (sections
    are walked in order, so a new clip sits with the others)."""
    from xi.entity.anim.xi_export import parse_sections
    cur = _find(data, CLIP, name)
    if cur is not None:
        return _replace(data, cur, blob)
    sections = parse_sections(data)
    after = [s for s in sections if s.type_code == CLIP] or [s for s in sections if s.type_code == SKELETON]
    if not after:
        raise AnimBuildError(f"{rel} has no clips and no skeleton: it isn't a model DAT a clip can go into")
    pos = after[-1].start + after[-1].size
    return data[:pos] + blob + data[pos:]


def _revert(data: bytes, pristine: bytes | None, records: list[dict], rel: str,
            warnings: list[str]) -> tuple[bytes, int]:
    """Put back each section ``records`` wrote, newest first: the untouched copy's section of
    that name, or none when it has none. A section that no longer holds what the build wrote
    is left, and said."""
    n = 0
    for e in reversed(records):
        tc = _KIND_TC.get(e.get("kind"))
        name = e.get("name")
        if tc is None or not isinstance(name, str):
            continue
        cur = _find(data, tc, name)
        if cur is None:
            if e.get("retail"):
                warnings.append(f"{rel}: the {e['kind']} {name!r} is gone; nothing to put back")
            continue
        if _sha1(_bytes(data, cur)) != e.get("sha1"):
            warnings.append(f"{rel}: the {e['kind']} {name!r} changed since the last build; left as it is")
            continue
        orig = _find(pristine, tc, name)
        data = _replace(data, cur, _bytes(pristine, orig) if orig is not None else b"")
        n += 1
    return data, n


# ── where each target's copy is ──────────────────────────────────────────────

def _install_file(rel: str) -> Path:
    import xi.xi_config as cfg
    return Path(cfg.FFXI_DIR) / Path(*rel.split("/"))


def _own_file(root: Path, rel: str) -> Path:
    """Where ``rel`` lives in ``root`` (whether or not it is there)."""
    from xi.menu.xi_menu_table import target_path
    return target_path(root, rel)


def has_own_copy(root: Path, rel: str) -> bool:
    return _own_file(root, rel).is_file()


def _read(root: Path, rel: str) -> bytes | None:
    from xi.dats import xi_stage
    return xi_stage.read(root, rel)


def _pristine(root: Path, target: str, rel: str) -> bytes | None:
    """The untouched copy a target's sections go back to: the HD folder's own ``.base`` (else
    its file as it is) for ``hd``; the install's ``.base`` (else its file) otherwise, as
    ``--reset`` resets the install and the pivot folder."""
    import xi.xi_config as cfg
    if target == "hd":
        own = _own_file(root, rel)
        base = own.with_name(own.name + ".base")
        p = base if base.is_file() and base.stat().st_size else own
    else:
        p = cfg.pristine(_install_file(rel))
    return p.read_bytes() if p is not None and p.is_file() else None


def _write(root: Path, target: str, rel: str, data: bytes, dry_run: bool) -> Path | None:
    """Write ``rel`` into the target: the HD folder's own copy keeps its own ``.base`` (the HD
    pack's bytes, never the install's); the install and the pivot folder as the other
    in-place types write them."""
    from xi.dats import xi_stage
    if target == "hd" and not dry_run:
        import xi.xi_config as cfg
        out = cfg.hd_editable_dat(_install_file(rel), fresh=False)
        out.write_bytes(data)
        return out
    return xi_stage.write(root, rel, data, dry_run)


# ── the clip and the routine ─────────────────────────────────────────────────

def _joint_count(data: bytes) -> int | None:
    sk = next((s for s in _sections(data) if s.type_code == SKELETON), None)
    return data[sk.data_start + 2] if sk is not None and sk.data_start + 2 < len(data) else None


def _sections(data: bytes):
    from xi.entity.anim.xi_export import parse_sections
    return parse_sections(data)


def _clip_from_dat(root: Path, action: dict, name: str, model: bytes, rel: str) -> tuple[bytes, dict]:
    """The source DAT's clip, its bytes as they are under the new name."""
    from xi.entity.anim.xi_export import read_animation_header
    from xi.entity.anim.xi_import import encode_name, parse_animation_template
    src = action["source"]
    src_rel, want = rom_rel(src["dat"]), src["clip"]
    data = _read(root, src_rel)
    if data is None:
        raise AnimBuildError(f"source.dat {src_rel} isn't in the game folder")
    sec = _find(data, CLIP, want)
    if sec is None:
        have = ", ".join(sorted({_label(s.name) for s in _sections(data) if s.type_code == CLIP})) or "none"
        raise AnimBuildError(f"{src_rel} has no clip {want!r} (its clips: {have})")
    # A clip drives joints by index: one made for a bigger skeleton would reach past this one.
    joints = _joint_count(model)
    used = [e.joint_index for e in parse_animation_template(data, sec).entries]
    if joints is not None and used and max(used) >= joints:
        raise AnimBuildError(f"{src_rel} clip {want!r} drives joint {max(used)}, but {rel}'s skeleton has "
                             f"{joints} joints: it was made for another skeleton")
    blob = bytearray(_bytes(data, sec))
    blob[0:4] = encode_name(name)
    _nj, frames, kdur = read_animation_header(data, sec)
    return bytes(blob), {"frames": frames, "kdur": kdur, "from": f"{src_rel} {want}"}


def _clip_from_gltf(root: Path, action: dict, name: str, model: bytes, pristine: bytes | None,
                    gltf: Path, rel: str) -> tuple[bytes, dict]:
    """The glTF's clip, as ``xi anim import --add`` builds it: the rig translations/scales of
    the clip it replaces (the untouched one), else of ``options.template_anim``."""
    from xi.entity.anim import xi_import as AI
    from xi.entity.anim.xi_export import parse_skeleton
    opts = action.get("options") or {}
    template = opts.get("template_anim") or "idl"
    sk = next((s for s in _sections(model) if s.type_code == SKELETON), None)
    if sk is not None:
        joints = parse_skeleton(model, sk)
    elif opts.get("skeleton_dat"):
        skel_rel = rom_rel(opts["skeleton_dat"])
        skel = _read(root, skel_rel)
        ssk = next((s for s in _sections(skel or b"") if s.type_code == SKELETON), None)
        if ssk is None:
            raise AnimBuildError(f"options.skeleton_dat {skel_rel} has no skeleton")
        joints = parse_skeleton(skel, ssk)
    else:
        raise AnimBuildError(f"{rel} has no skeleton of its own: set options.skeleton_dat to the model "
                             "the glTF was exported on")
    orig = AI.choose_section_by_name(_sections(pristine), name) if pristine else None
    if orig is not None and _label(orig.name) == name:
        tdata, tsec = pristine, orig
    else:
        tdata, tsec = model, AI.choose_section_by_name(_sections(model), template)
    if not Path(gltf).is_file():
        raise AnimBuildError(f"resources.animation: no glTF at {gltf}")
    try:
        blob, frames, kdur, _g = AI.clip_block(name, Path(gltf), joints, tdata, tsec, template,
                                               fps=opts.get("fps"))
    except ValueError as e:
        raise AnimBuildError(f"{Path(gltf).name}: {e}")
    return blob, {"frames": frames, "kdur": kdur, "from": Path(gltf).name}


def _record(rel: str, kind: str, name: str, data: bytes, pristine: bytes | None, seeded: bool, **more) -> dict:
    tc = _KIND_TC[kind]
    s = _find(data, tc, name)
    rec = {"dat": rel, "kind": kind, "name": name, "sha1": _sha1(_bytes(data, s)),
           "retail": _find(pristine, tc, name) is not None, **more}
    if seeded:
        rec["seeded"] = True
    return rec


# ── build / undo ─────────────────────────────────────────────────────────────

def build(action: dict, *, root: Path, target: str, gltf: Path | None = None, dry_run: bool = False) -> dict:
    """Put the action's clip (and its routine) into ``target``'s own copy of the model DAT.
    The result's ``records`` are what gets recorded for this target."""
    errs = validate_action(action)
    if errs:
        raise AnimBuildError("; ".join(errs))
    root = Path(root)
    rel = rom_rel(action["target"]["dat"])
    name = clip_name(action)
    out = {"id": action["id"], "type": "anim", "target": target, "dat": rel, "clip": name,
           "records": [], "files": [], "written": [], "warnings": [], "notes": []}
    if target == "hd" and not has_own_copy(root, rel):
        out["skipped"] = f"the HD folder has no {rel} of its own (the client loads the install's)"
        return out
    current = _read(root, rel)
    if current is None:
        raise AnimBuildError(f"{rel} isn't in the game folder")
    seeded = target == "pivot" and not has_own_copy(root, rel)
    pristine = _pristine(root, target, rel)
    prev = [e for e in ((((action.get("result") or {}).get("roots") or {}).get(target)) or [])
            if e.get("dat") == rel]
    # What the last build wrote goes back first, so a rebuild (or a renamed clip) starts
    # from the untouched sections and leaves nothing of the last one behind.
    data, _n = _revert(current, pristine, prev, rel, out["warnings"])
    ours = {(e.get("kind"), e.get("name")) for e in prev}

    def outside_edit(kind: str, tag: str) -> None:
        tc = _KIND_TC[kind]
        cur, orig = _find(data, tc, tag), _find(pristine, tc, tag)
        if cur is not None and (kind, tag) not in ours and (
                orig is None or _bytes(data, cur) != _bytes(pristine, orig)):
            out["warnings"].append(f"{rel}: the {kind} {tag!r} holds an edit made outside this project; "
                                   "this build writes over it and undo puts back the untouched one")

    if action.get("source"):
        blob, info = _clip_from_dat(root, action, name, data, rel)
    else:
        blob, info = _clip_from_gltf(root, action, name, data, pristine, gltf, rel)
    outside_edit("clip", name)
    retail_clip = _find(pristine, CLIP, name) is not None
    data = _put_clip(data, rel, blob, name)
    out.update(frames=info["frames"], source=info["from"])
    # (kind, name) -> what to record with it; a routine touched twice is recorded once.
    touched: dict[tuple[str, str], dict] = {("clip", name): {"frames": info["frames"], "from": info["from"]}}
    if retail_clip:
        out["notes"].append(f"replaces the model's own clip {name!r}")
        opts = action.get("options") or {}
        if not opts.get("keep_routine_duration"):
            # A longer clip under a retail name is cut short by the routine that plays it
            # unless its window grows (`xi anim import` does the same).
            from xi.entity.anim.xi_import import fit_routine_duration
            length = (info["frames"] - 1) / info["kdur"] if info["kdur"] else info["frames"] - 1
            data, fit = fit_routine_duration(data, _sections(data), name, length)
            if fit and fit["new"] != fit["old"]:
                tag = _label(fit["routine"])
                outside_edit("routine", tag)
                touched[("routine", tag)] = {"dur": fit["new"]}
                out["notes"].append(f"routine {tag!r} plays it for {fit['new']} (was {fit['old']})")

    sched = schedule_of(action)
    if sched is not None and not (sched["auto"] and retail_clip):
        from xi.entity.anim.xi_schedule import _splice_routine, build_routine_section
        tag = sched["tag"]
        outside_edit("routine", tag)
        if _find(pristine, ROUTINE, tag) is not None:
            out["notes"].append(f"replaces the model's own routine {tag!r}")
        try:
            section, rinfo = build_routine_section(data, tag, name, max_loops=sched["loops"],
                                                   trans_in=sched["blend"], trans_out=sched["blend"])
        except ValueError as e:
            raise AnimBuildError(f"{rel}: {e}")
        data, _how = _splice_routine(data, tag, section)
        touched[("routine", tag)] = {"plays": name, "loops": rinfo["maxLoops"], "blend": sched["blend"],
                                     "dur": rinfo["dur"]}
        out["routine"] = tag

    out["records"] = [_record(rel, kind, tag, data, pristine, seeded, **more)
                      for (kind, tag), more in touched.items()]
    if data != current:
        out["files"] = [rel]
        written = _write(root, target, rel, data, dry_run)
        if written is not None:
            out["written"].append(str(written))
    else:
        out["notes"].append("already built (nothing changed)")
    return out


def undo(action: dict, root: Path, target: str, dry_run: bool = False) -> tuple[int, list[str]]:
    """Put back every section ``action`` wrote in ``root``; ``(sections, warnings)``. A copy a
    pivot build seeded from the install goes again once it is the install's file once more."""
    entries = (((action.get("result") or {}).get("roots") or {}).get(target)) or []
    root = Path(root)
    warnings: list[str] = []
    total = 0
    for rel in dict.fromkeys(e.get("dat") for e in entries if e.get("dat")):
        recs = [e for e in entries if e.get("dat") == rel]
        current = _read(root, rel) if (target != "hd" or has_own_copy(root, rel)) else None
        if current is None:
            warnings.append(f"{rel} is gone; nothing to put back")
            continue
        data, n = _revert(current, _pristine(root, target, rel), recs, rel, warnings)
        total += n
        if data == current:
            continue
        own = _own_file(root, rel)
        if target == "pivot" and all(e.get("seeded") for e in recs) and own.is_file() \
                and data == _install_file(rel).read_bytes():
            if not dry_run:
                own.unlink()
        else:
            _write(root, target, rel, data, dry_run)
    return total, warnings

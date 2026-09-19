"""``xi ability inspect`` — the full presentation timeline of an ability DAT.

An ability (job ability, weapon skill, spell, mob skill) is one DAT whose ``main``
``0x07`` EffectRoutine is a frame-clocked command list. Each command references a
sibling section by 4-char DatId: ``0x2B`` skeleton clips (the body motion), ``0x05``
particle generators (the VFX), ``0x3D`` sound pointers, ``0x54`` weapon traces, and
other ``0x07`` routines (linked sub-timelines). This command flattens that graph into
one absolute-frame timeline, resolving every reference it can, so the composition of a
retail ability can be read off — and later recombined — command by command.

Spec forms accepted:

    ja:N        job-ability animation N     (file_id 4412 + N, abilities.animation)
    spell:N     spell index N               (xi.spell: 0xAF0 + SpellAnimationTable[N])
    ws:N[:RACE] weapon-skill animation N    (per-race motion banks in FFXiMain.dll)
    fid:N       any file id through FTABLE/VTABLE
    ROM/x/y     a DAT spec or path

Format references: docs/fx/effect_system.md §3 (routine layout + opcodes),
docs/reference/ps2_decomp_crosscheck.md §5 (SE task names), docs/anim/weapon-skills.md.
"""

from __future__ import annotations

import fnmatch
import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import click

from xi.entity.anim.xi_export import Section, parse_sections, read_animation_header
from xi.event.xi_event import _routine_sec2_commands
from xi.xi_config import FFXI_DIR, read_path_for

T_END, T_DIR, T_GEN, T_ROUTINE, T_CURVE, T_PMESH, T_TEX, T_SPRITE, T_WMESH = (
    0x00, 0x01, 0x05, 0x07, 0x19, 0x1F, 0x20, 0x21, 0x25)
T_CLIP, T_SOUND, T_TRACE = 0x2B, 0x3D, 0x54

ABILITY_FILE_OFFSET = 4412      # job-ability VFX band (docs/mv/README.md)
_SESEP = b"SeSep"

# op -> (name, kind). Names follow xim's EffectRoutineParser where it has one, else the
# PS2 decompile's task class (docs/reference/ps2_decomp_crosscheck.md §5).
ROUTINE_OPS: Dict[int, Tuple[str, str]] = {
    0x00: ("End", "end"),
    0x01: ("Start", "start"),
    0x02: ("FireGenerator", "vfx"),
    0x03: ("LinkRoutine(source)", "link"),
    0x05: ("PlayClip", "motion"),
    0x07: ("BondActors", "lock"),
    0x08: ("BondActors", "lock"),
    0x09: ("LinkRoutine(target)", "link"),
    0x0A: ("Sound(source pos)", "sound"),
    0x0B: ("Sound(target pos)", "sound"),
    0x0C: ("ModelTranslate", "move"),
    0x0D: ("ModelRotate", "move"),
    0x0E: ("ScreenBlur", "screen"),
    0x0F: ("ScreenColor", "screen"),
    0x10: ("ScreenFade", "screen"),
    0x12: ("BackJump", "move"),
    0x14: ("BackJump", "move"),
    # A doll is a stand-in XiDollActor made from the caster or target, which the rest of
    # the routine then plays on: 0x15 / 0x16 a still copy, 0x22 / 0x23 one that tracks
    # the real actor; 0x17 / 0x18 hand the routine back to the real one.
    0x15: ("SpawnDoll(caster)", "actor"),
    0x16: ("SpawnDoll(target)", "actor"),
    0x17: ("SetCaster", "actor"),
    0x18: ("SetTarget", "actor"),
    0x19: ("NestedSpell", "link"),
    0x1E: ("DampenGenerator", "vfx-stop"),
    0x1F: ("LockActorStatus", "lock"),
    0x20: ("LockActorStatus", "lock"),
    0x21: ("Flinch", "hit"),
    0x22: ("TrackingDoll(caster)", "actor"),
    0x23: ("TrackingDoll(target)", "actor"),
    0x24: ("SuspendOnResult", "flow"),
    0x25: ("Flinch", "hit"),
    0x27: ("FollowPath", "move"),
    0x28: ("ReturnToIdle", "motion"),
    0x29: ("ActorFade", "fade"),
    0x2A: ("ActorFade", "fade"),
    # The hit: shows the next result not shown yet (the damage or heal line and its
    # number). The shared `mdam` a retail ability links is one of these.
    0x2B: ("ShowResult", "message"),
    0x2C: ("WeaponTrace", "trace"),
    0x2D: ("StopGenerator", "vfx-stop"),
    0x2E: ("LockCasterControl", "lock"),
    0x2F: ("LockCasterRotation", "lock"),
    0x30: ("LinkRoutine(each target)", "link"),
    # A loop over the result's targets: at 0x32 the client moves to the next target and
    # goes back to just after the last 0x31.
    0x31: ("EachTarget", "target"),
    0x32: ("NextTarget", "target"),
    0x3B: ("LinkRoutine(blocking)", "link"),
    0x3C: ("LinkRoutine(blocking)", "link"),
    0x3D: ("RandomChildOpen", "flow"),
    0x3E: ("RandomChildClose", "flow"),
    0x3F: ("TransitionGenerator", "vfx"),
    0x40: ("ActorWrapTexture", "wrap"),
    0x41: ("ActorWrapTexture", "wrap"),
    0x42: ("ActorWrapUV", "wrap"),
    0x43: ("ActorWrapUV", "wrap"),
    0x44: ("ActorWrapColor", "wrap"),
    0x45: ("ActorWrapColor", "wrap"),
    0x46: ("ActorColorFade", "fade"),
    0x47: ("ActorColorFade", "fade"),
    0x49: ("ActorDistortion", "wrap"),
    0x4A: ("Sound(player only)", "sound"),
    0x52: ("TimeBasedReplay", "flow"),
    0x53: ("Sound(target)", "sound"),
    0x55: ("EffectTextureActor", "wrap"),
    0x56: ("LockTargetStatus", "lock"),
    0x57: ("LinkRoutine", "link"),
    0x58: ("Camera", "camera"),
    0x59: ("LockCasterMagic", "lock"),
    0x5E: ("Knockback", "hit"),
    0x5F: ("StopRoutine", "link"),
    0x60: ("SoundPlay", "sound"),
    0x62: ("IdleAdjustDir", "move"),
    0x64: ("Branch", "flow"),
    0x67: ("Branch", "flow"),
    0x6B: ("Condition", "flow"),
    0x70: ("LightDrive", "screen"),
    0x71: ("StatusMessageSP", "message"),
    0x73: ("LoopStart", "flow"),
    0x74: ("Disintegrate", "fade"),
    0x75: ("ShowHideWeapon", "actor"),
    0x76: ("RangedStart", "motion"),
    0x77: ("RangedFinish", "motion"),
    0x79: ("AnimMode", "motion"),
    0x7A: ("EidLink", "actor"),
    0x7C: ("SetVanadielTick", "world"),
    0x7D: ("SetVanadielTime", "world"),
    0x7E: ("Weather", "world"),
    0x7F: ("SpecularPower", "screen"),
    0x80: ("CloneStatus", "actor"),
    0x82: ("DepthOfField", "camera"),
    0x83: ("DepthOfField", "camera"),
    0x85: ("LoopEnd", "flow"),
    0x88: ("LockColorDrive", "lock"),
    0x89: ("LockConstrainDrive", "lock"),
    0x8A: ("SoundStop", "sound"),
    0x8B: ("SoundStop", "sound"),
    0x8C: ("AnimMode", "motion"),
    0xA3: ("Visibility", "actor"),
    0xA4: ("AnimMode", "motion"),
    0xA5: ("AnimMode", "motion"),
}
LINK_OPS = frozenset({0x03, 0x09, 0x3B, 0x3C, 0x57})
SOUND_OPS = frozenset({0x0A, 0x0B, 0x4A, 0x53, 0x60})
# ops whose +8 DatId is a real reference (others carry numeric args there: 0x19's is a
# spell animation index). 0x30 runs a routine once per target and 0x5F stops every
# running copy of one, each by name.
REF_OPS = LINK_OPS | SOUND_OPS | frozenset({0x02, 0x05, 0x1E, 0x27, 0x2C, 0x2D, 0x30, 0x3F,
                                             0x40, 0x41, 0x5F, 0x73, 0x85, 0x8A, 0x8B})


# ── Target resolution ────────────────────────────────────────────────────────────

@dataclass
class Target:
    label: str                  # how the user asked for it, plus race for ws
    path: Path
    rel: str                    # ROM-relative spec when known
    file_id: Optional[int] = None
    names: List[str] = field(default_factory=list)


def _ftable_path(file_id: int) -> Optional[str]:
    from xi.ftable.xi_core import scan_file_ids
    hits = scan_file_ids([file_id])
    return hits[0]["dat"] if hits else None


def _server_sql(name: str) -> Optional[Path]:
    from xi.xi_config import XI_SERVER_DIR
    if not XI_SERVER_DIR:
        return None
    p = Path(XI_SERVER_DIR) / "sql" / name
    return p if p.is_file() else None


def _sql_rows(name: str, table: str, pattern: str) -> List[Tuple[str, ...]]:
    p = _server_sql(name)
    if p is None:
        return []
    rx = re.compile(rf"INSERT INTO `{table}` VALUES \({pattern}", re.I)
    return [m.groups() for m in rx.finditer(p.read_text(encoding="utf-8", errors="replace"))]


def _title(name: str) -> str:
    return " ".join(w.capitalize() for w in name.replace("_", " ").split())


def ability_names_for_anim(anim: int) -> List[str]:
    """Server ability names whose ``abilities.animation`` is ``anim``."""
    rows = _sql_rows("abilities.sql", "abilities",
                     r"(\d+),'([^']*)',\d+,\d+,\d+,\d+,\d+,\d+,\d+,(\d+),")
    return [f"{_title(n)} (ability {aid})" for aid, n, a in rows if int(a) == anim]


def ws_names_for_anim(anim: int) -> List[str]:
    """Server weapon-skill / humanoid mob-skill names whose animation is ``anim``."""
    out = []
    ws = _sql_rows("weapon_skills.sql", "weapon_skills",
                   r"(\d+),'([^']*)',0x[0-9A-Fa-f]+,\d+,\d+,\d+,(\d+),")
    out += [f"{_title(n)} (ws {wid})" for wid, n, a in ws if int(a) == anim]
    mob = _sql_rows("mob_skills.sql", "mob_skills", r"(\d+),(\d+),'([^']*)',")
    out += [f"{_title(n)} (mob_skill {mid})" for mid, a, n in mob
            if int(a) == anim and int(mid) < 256]
    return out


def resolve_targets(spec: str) -> List[Target]:
    """Turn a spec into one or more concrete DATs (weapon skills give one per race)."""
    base = Path(FFXI_DIR)
    kind, _, rest = spec.partition(":")
    kind = kind.lower()

    def from_fid(fid: int, label: str, names: List[str]) -> Target:
        rel = _ftable_path(fid)
        if rel is None:
            raise click.ClickException(f"{label}: file id {fid} is not registered in any FTABLE")
        p = base / rel
        if not p.is_file():
            raise click.ClickException(f"{label}: {rel} (file id {fid}) is missing on disk")
        return Target(label, p, rel, fid, names)

    if kind in ("ja", "ability") and rest.isdigit():
        anim = int(rest)
        return [from_fid(ABILITY_FILE_OFFSET + anim, f"ja:{anim}", ability_names_for_anim(anim))]
    if kind == "fid" and rest.isdigit():
        return [from_fid(int(rest), spec, [])]
    if kind == "spell" and rest.isdigit():
        from xi.spell.xi_spell import file_index_for, load_spell_names
        idx = int(rest)
        fid = file_index_for(idx)
        if fid is None:
            raise click.ClickException(f"spell {idx} has no animation-table entry")
        names = load_spell_names()
        nm = names[idx].strip() if 0 <= idx < len(names) else ""
        return [from_fid(fid, spec, [nm] if nm else [])]
    if kind == "ws":
        from xi.entity.anim.xi_motion_tables import resolve_weapon_skill
        anim_s, _, race = rest.partition(":")
        if not anim_s.isdigit():
            raise click.ClickException("ws spec is ws:N or ws:N:RACE")
        anim = int(anim_s)
        try:
            slots = resolve_weapon_skill(anim, race or None)
        except ValueError as e:
            raise click.ClickException(str(e))
        names = ws_names_for_anim(anim)
        return [from_fid(s.file_id, f"ws:{anim} {s.race}", names) for s in slots]

    from xi.entity.mesh.xi_export import resolve_dat_path
    try:
        p = resolve_dat_path(spec)
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    try:
        rel = p.relative_to(base).as_posix()
    except ValueError:
        rel = str(p)
    return [Target(spec, p, rel)]


# ── DAT model ────────────────────────────────────────────────────────────────────

def _clean(name: str) -> str:
    return name.rstrip("\x00 ")


def _tree(sections: List[Section]) -> List[dict]:
    """Nest the flat section list by 0x01/0x00 push/pop → ``[{name, type, children}]``."""
    root: List[dict] = []
    stack: List[List[dict]] = [root]
    for s in sections:
        if s.type_code == T_END:
            if len(stack) > 1:
                stack.pop()
            continue
        node = {"name": _clean(s.name), "type": s.type_code, "size": s.size}
        stack[-1].append(node)
        if s.type_code == T_DIR:
            node["children"] = []
            stack.append(node["children"])
    return root


def _sound_table(data: bytes, sections: List[Section]) -> Dict[str, dict]:
    from xi.audio import xi_names
    out = {}
    for s in sections:
        if s.type_code != T_SOUND or data[s.data_start:s.data_start + 5] != _SESEP:
            continue
        sid = struct.unpack_from("<I", data, s.data_start + 8)[0]
        out[_clean(s.name)] = {"sound_id": sid, "file": f"se{sid:06d}",
                               "title": xi_names.sfx_name(sid),
                               "category": xi_names.sfx_category(sid)}
    return out


def _clip_table(data: bytes, sections: List[Section]) -> Dict[str, dict]:
    out = {}
    for s in sections:
        if s.type_code != T_CLIP:
            continue
        try:
            nj, nf, kdur = read_animation_header(data, s)
            frames = round((nf - 1) / kdur, 1) if kdur else float(max(0, nf - 1))
        except Exception:  # noqa: BLE001 — a malformed clip still deserves a row
            nj, nf, frames = 0, 0, 0.0
        out[_clean(s.name)] = {"joints": nj, "keyframes": nf, "frames": frames, "size": s.size}
    return out


def _generator_table(data: bytes, sections: List[Section], sounds: Dict[str, dict]) -> Dict[str, dict]:
    """Every 0x05 generator: attach type, texture/mesh, and — for audio emitters — the
    0x3D sound its StandardSetup (sec2 0x01, DatId @+8) names. Spells and job abilities
    play their sounds this way rather than with a routine sound command."""
    from xi.fx.xi_core import (ATTACH_TYPES, _OFF_ATTACH, _effect_target, _effect_texture,
                               _mesh_fourccs, _texture_fourccs)
    mesh_ccs = _mesh_fourccs(data, sections)
    tex_ccs = _texture_fourccs(data, sections)
    out = {}
    for s in sections:
        if s.type_code != T_GEN:
            continue
        body = bytes(data[s.start:s.start + s.size])
        attach_raw = struct.unpack_from("<H", body, _OFF_ATTACH)[0] if len(body) >= _OFF_ATTACH + 2 else 0
        mesh, _pos = _effect_target(body, mesh_ccs)
        sound = next((n for n in sounds if body.find(n.encode("ascii"), 16) >= 0), None)
        out[_clean(s.name)] = {
            "sound": sound,
            "attach": ATTACH_TYPES.get(attach_raw & 0x0F, f"0x{attach_raw & 0x0F:X}"),
            "attach_flags": attach_raw,
            "texture": (_effect_texture(body, tex_ccs) or "").strip() or None,
            "mesh": (mesh or "").strip() or None,
            "size": s.size,
        }
    return out


def _match_refs(pattern: str, names) -> List[str]:
    """Client-side ``?`` wildcards in a ref (``b00?``) select among sibling sections."""
    pat = pattern.replace("[", "[[]")
    return sorted(n for n in names if fnmatch.fnmatchcase(n, pat))


@dataclass
class Model:
    data: bytes
    sections: List[Section]
    routines: Dict[str, Section]
    clips: Dict[str, dict]
    gens: Dict[str, dict]
    sounds: Dict[str, dict]
    traces: List[str]

    @classmethod
    def load(cls, path: Path) -> "Model":
        data = read_path_for(path).read_bytes()
        secs = parse_sections(data)
        sounds = _sound_table(data, secs)
        return cls(
            data=data, sections=secs,
            routines={_clean(s.name): s for s in secs if s.type_code == T_ROUTINE},
            clips=_clip_table(data, secs),
            sounds=sounds,
            gens=_generator_table(data, secs, sounds),
            traces=[_clean(s.name) for s in secs if s.type_code == T_TRACE],
        )

    def routine_total(self, tag: str) -> int:
        s = self.routines[tag]
        return struct.unpack_from("<I", self.data, s.start + 16 + 0x1C)[0]

    def raw_command(self, tag: str, off: int) -> bytes:
        s = self.routines[tag]
        p = s.start + 16 + off
        n = struct.unpack_from("<H", self.data, p + 1)[0] & 0x1F
        return bytes(self.data[p:p + max(1, n) * 4])


# ── Timeline ─────────────────────────────────────────────────────────────────────

def _describe(m: Model, op: int, ref: Optional[str], c: dict) -> Tuple[dict, str]:
    """Resolve a command's reference → (detail dict, one-line summary)."""
    d: dict = {}
    if op == 0x05 and ref:
        hits = _match_refs(ref, m.clips) if "?" in ref else ([ref] if ref in m.clips else [])
        d["clips"] = [{"name": h, **m.clips[h]} for h in hits]
        d["in_dat"] = bool(hits)
        d["blend"] = [c["transIn"], c["transOut"]]
        d["loops"] = c["maxLoops"]
        loops = "∞" if c["maxLoops"] == 0 else f"x{c['maxLoops']}"
        where = (", ".join(f"{h} {m.clips[h]['frames']:g}f" for h in hits)
                 if hits else "actor's own motion pool")
        return d, f"{where}; blend {c['transIn']}/{c['transOut']} {loops}"
    if op in (0x02, 0x3F, 0x1E, 0x2D) and ref:
        g = m.gens.get(ref)
        if g:
            d["generator"] = {"name": ref, **g}
            bits = [g["attach"]]
            if g["sound"]:
                snd = m.sounds[g["sound"]]
                bits.append(f"sound {snd['file']} {snd['title'] or snd['category'] or ''}".rstrip())
                d["sound"] = {"section": g["sound"], **snd}
            if g["texture"]:
                bits.append(f"tex {g['texture']}")
            if g["mesh"]:
                bits.append(f"mesh {g['mesh']}")
            return d, ", ".join(bits)
        return d, "generator not in DAT"
    if op in SOUND_OPS and ref:
        snd = m.sounds.get(ref)
        if snd:
            d["sound"] = {"section": ref, **snd}
            label = snd["title"] or snd["category"] or ""
            return d, f"{snd['file']} {label}".strip()
        return d, "sound pointer not in DAT"
    if op == 0x2C and ref:
        hits = _match_refs(ref, m.traces) if "?" in ref else ([ref] if ref in m.traces else [])
        d["traces"] = hits
        return d, ", ".join(hits) if hits else "trace not in DAT"
    if op in LINK_OPS and ref:
        d["local"] = ref in m.routines
        return d, ("expanded below" if ref in m.routines else "external routine (shared / caster DAT)")
    if op == 0x19:
        idx = struct.unpack_from("<I", m.raw_command(c["_tag"], c["off"]), 8)[0]
        d["spell_index"] = idx
        return d, f"spell animation {idx}"
    return d, ""


def flatten(m: Model, tag: str, base: int = 0, via: Tuple[str, ...] = (),
            depth: int = 0, seen: Optional[set] = None) -> List[dict]:
    """Absolute-frame events for routine ``tag``, recursing into local linked routines
    with the inherited clock.

    A command runs at the current clock and its ``delay`` is the wait AFTER it, before
    the next command (docs/fx/effect_system.md §3): the client pumps
    ``field_98 += tag.delay`` past each tag it executes, and retail relies on it — a
    chained clip's delay is the previous clip's window, so Soul Voice's hold (``cm1?``)
    starts at 60, the frame its 60-tick wind-up ends, and the header ``totalDelay`` is
    the sum of every delay including the last command's own."""
    seen = seen or set()
    if depth > 8 or tag in seen:
        return []
    seen = seen | {tag}
    events: List[dict] = []
    clock = base
    for c in _routine_sec2_commands(m.data, tag):
        op = c["op"]
        name, kind = ROUTINE_OPS.get(op, (f"op{op:02X}", "other"))
        ref = c["ref"] if op in REF_OPS else None
        c["_tag"] = tag
        detail, summary = _describe(m, op, ref, c)
        ev = {"start": clock, "dur": c["dur"], "op": op, "name": name, "kind": kind,
              "ref": ref, "routine": tag, "via": list(via), "offset": c["off"],
              "raw": m.raw_command(tag, c["off"]).hex(), "detail": detail, "summary": summary}
        if op not in (0x00, 0x01):
            events.append(ev)
        if op in LINK_OPS and ref and ref in m.routines:
            events += flatten(m, ref, clock, via + (tag,), depth + 1, seen)
        clock += c["delay"]
    return events


def inspect_target(t: Target, root: str = "main") -> dict:
    m = Model.load(t.path)
    if root not in m.routines:
        raise click.ClickException(
            f"{t.rel}: no routine {root!r} (has: {', '.join(m.routines) or 'none'})")
    events = sorted(flatten(m, root), key=lambda e: (e["start"], e["offset"]))
    reached = {e["routine"] for e in events} | {root}
    counts: Dict[str, int] = {}
    for s in m.sections:
        counts[f"0x{s.type_code:02X}"] = counts.get(f"0x{s.type_code:02X}", 0) + 1
    end = max((e["start"] + e["dur"] for e in events), default=0)
    return {
        "spec": t.label, "dat": t.rel, "file_id": t.file_id, "names": t.names,
        "size": len(m.data), "root": root, "total": m.routine_total(root), "last_frame": end,
        "routines": {tag: {"total": m.routine_total(tag), "reached": tag in reached}
                     for tag in m.routines},
        "clips": m.clips, "generators": m.gens, "sounds": m.sounds, "traces": m.traces,
        "section_counts": counts, "tree": _tree(m.sections), "timeline": events,
    }


# ── Rendering ────────────────────────────────────────────────────────────────────

_TYPE_TAGS = {T_CLIP: "clip", T_GEN: "gen", T_ROUTINE: "routine", T_CURVE: "curve",
              T_PMESH: "pmesh", T_TEX: "tex", T_SPRITE: "sprite", T_WMESH: "wmesh",
              T_SOUND: "sound", T_TRACE: "trace"}


def _tree_lines(nodes: List[dict], indent: int = 0) -> List[str]:
    lines = []
    for n in nodes:
        if n["type"] == T_DIR:
            kids = n.get("children", [])
            leaf = [k for k in kids if k["type"] != T_DIR]
            summary: Dict[str, List[str]] = {}
            for k in leaf:
                summary.setdefault(_TYPE_TAGS.get(k["type"], f"0x{k['type']:02X}"), []).append(k["name"])
            parts = [f"{len(v)} {k}: {' '.join(v)}" if len(v) <= 12 else f"{len(v)} {k}"
                     for k, v in summary.items()]
            lines.append("  " * indent + f"{n['name']}/  " + " | ".join(parts))
            lines += _tree_lines([k for k in kids if k["type"] == T_DIR], indent + 1)
    return lines


def render(info: dict, show_all: bool) -> str:
    out = []
    head = f"{info['dat']}  ({info['spec']})  {info['size']:,} B"
    if info["file_id"] is not None:
        head += f"  file_id {info['file_id']}"
    out.append(head)
    names = info["names"]
    if names:
        shown = "; ".join(names[:6]) + (f"; +{len(names) - 6} more" if len(names) > 6 else "")
        out.append("  " + shown)
    out.append("")
    out += _tree_lines(info["tree"]) or ["(flat DAT, no directories)"]
    out.append("")
    unreached = [t for t, r in info["routines"].items() if not r["reached"]]
    other = (f"   (other routines: {', '.join(unreached)})" if len(unreached) <= 8
             else f"   ({len(unreached)} other routines; --routine TAG to flatten one)")
    out.append(f"timeline: {info['root']}  totalDelay {info['total']}  last event ends f{info['last_frame']}"
               + (other if unreached else ""))
    out.append(f"  {'frame':>5} {'dur':>4}  {'kind':<8} {'op':<4} {'command':<22} {'ref':<5} detail")
    for e in info["timeline"]:
        if not show_all and e["kind"] in ("flow", "start", "end"):
            continue
        via = f" [{'>'.join(e['via'][1:] + [e['routine']])}]" if e["via"] else ""
        dur = str(e["dur"]) if e["dur"] else "-"
        out.append(f"  {e['start']:>5} {dur:>4}  {e['kind']:<8} {e['op']:02X}   {e['name']:<22} "
                   f"{(e['ref'] or '-'):<5} {e['summary']}{via}")
    out.append("")
    clips = info["clips"]
    if clips:
        out.append("clips in DAT: " + ", ".join(f"{n} {c['frames']:g}f" for n, c in clips.items()))
    else:
        out.append("clips in DAT: none (motion comes from the actor's own pool)")
    if info["sounds"]:
        out.append("sounds: " + ", ".join(
            f"{n}→{s['file']}" + (f" ({s['title']})" if s["title"] else "") for n, s in info["sounds"].items()))
    gens = info["generators"]
    if gens:
        by_attach: Dict[str, List[str]] = {}
        for n, g in gens.items():
            by_attach.setdefault(g["attach"], []).append(n + ("♪" if g["sound"] else ""))
        out.append("generators: " + "; ".join(f"{a}: {' '.join(v)}" for a, v in by_attach.items()))
    return "\n".join(out)


@click.command("inspect")
@click.argument("spec")
@click.option("--routine", "root", default="main", show_default=True,
              help="Routine tag to flatten (the client fires 'main').")
@click.option("--all", "show_all", is_flag=True, help="Show flow/branch commands too.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON (every command, raw bytes included).")
def cmd(spec: str, root: str, show_all: bool, as_json: bool):
    """Flatten an ability DAT's routine graph into one absolute-frame timeline.

    SPEC: ja:N | spell:N | ws:N[:RACE] | fid:N | ROM/x/y[.DAT] | path
    """
    targets = resolve_targets(spec)
    infos = [inspect_target(t, root) for t in targets]
    if as_json:
        click.echo(json.dumps(infos if len(infos) > 1 else infos[0], indent=2, ensure_ascii=False))
        return
    for i, info in enumerate(infos):
        if i:
            click.echo("\n" + "=" * 100 + "\n")
        click.echo(render(info, show_all))


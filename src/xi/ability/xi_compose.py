"""``xi ability compose`` — build a new ability DAT from a recipe that takes commands and
sections from several retail sources (motion from A, VFX from B, sound from C).

A recipe is JSON::

    {
      "name": "tiger_fury",
      "target": {"kind": "ja", "animation": 339},
      "sources": {
        "motion": {"spec": "ws:1",      "routine": "main"},
        "vfx":    {"spec": "spell:144", "routine": "main"},
        "sound":  {"spec": "ja:33",     "routine": "main"}
      },
      "events": [
        {"from": "motion", "op": 5,  "ref": "b00?", "start": 10, "dur": 35, "blend": [20, 0], "loops": 1},
        {"from": "vfx",    "op": 2,  "ref": "g004", "start": 60, "dur": 100},
        {"from": "sound",  "op": 10, "ref": "5112", "start": 60},
        {"from": "motion", "op": 3,  "ref": "mdam", "start": 100}
      ]
    }

Every event becomes one command in the new ``main`` routine. The command bytes are the
source routine's own (``raw`` hex, or looked up by ``routine`` + ``offset``, or matched by
``op`` + ``ref``), with ``delay`` restamped from the absolute ``start`` frames and the
exposed fields (``dur``, ``blend``, ``loops``) patched. Every section a command names is
transplanted from its lane's source DAT with its dependencies (the ``xi fx copy`` walk),
and locally linked routines are carried recursively. Name collisions across sources are
resolved by renaming the later section and patching its references.

A lane whose spec is a weapon-skill number without a race (``ws:1``) is race-bound, so the
recipe composes once per race and the output is one DAT per race.

A lane read from a PC race's own motion file is race-bound the same way: the client's
per-race motion tables (xi_motion_tables) map an emote, battle pack, dance or weapon-skill
DAT to every race's copy, and each race's DAT carries that race's clips (an emote's waist
part from its +6 sibling included). A race without a copy, or without the clip, is built
without that motion and the composed result says so in ``warnings``. The race base (the
``movement`` table: idle, walk, cast and job-ability motions) is always loaded on a
character, so its clips are named, not carried, and a job ability or spell can use them. A
motion file the tables do not index but the character list gives to some races (a race's
Variations) is kept for those races only.
"""

from __future__ import annotations

import fnmatch
import json
import re
import struct
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import click

from xi.ability.xi_inspect import (
    LINK_OPS, REF_OPS, SOUND_OPS, T_CLIP, T_DIR, T_GEN, T_ROUTINE, T_SOUND, T_TRACE,
    Model, Target, _clean, flatten, resolve_targets)
from xi.common.xi_section import encode_section_meta
from xi.entity.anim.xi_motion_tables import RACE_NAMES, _waist_sibling_spec, motion_dat_for_race, motion_slot_for
from xi.fx.xi_copy import _DEP_TYPES, _effect_deps

VFX_OPS = frozenset({0x02, 0x3F, 0x1E, 0x2D})
_GEN_TYPES = (T_GEN,) + tuple(_DEP_TYPES)

# Retail byte layouts (docs/fx/effect_system.md §3, verified on ROM/76/30 and ROM/15/89).
_DIR_DATA = bytes.fromhex("00000000000000200000000000000000")   # an effect directory's payload
_END_SECTION = bytes.fromhex("656e6400800000000000000000000000")
_ROUTINE_SEC1 = bytes.fromhex("00010000") + b"\0" * 12           # end marker, padded to 16
_ROUTINE_START = bytes.fromhex("0102000000000000")               # op 0x01, 2 dwords
_ROUTINE_END = bytes.fromhex("00010000")

# Command templates for events that carry no raw bytes (delay/dur/ref get stamped in).
_TEMPLATES = {
    0x02: bytes.fromhex("02040000000000006730303000000000"),
    0x03: bytes.fromhex("03040000000000006d64616d00000000"),
    0x05: bytes.fromhex("050a0000000000006230303f000000000000803f0000803f0a0000000a000100"
                        "0000000000000000"),
    0x0A: bytes.fromhex("0a08000000000000383035300000000000000000feffef410000000000000000"),
    0x0B: bytes.fromhex("0b08000000000000383035300000000000000000feffef410000000000000000"),
    0x2C: None,
}


@dataclass
class Lane:
    name: str
    spec: str
    routine: str = "main"
    race_bound: bool = False
    model: Optional[Model] = None
    target: Optional[Target] = None
    events: List[dict] = field(default_factory=list)
    # Read from a PC race's own motion file (see the module docstring): `slot` maps it
    # to every race's copy; `owners` are the races a file outside the tables belongs
    # to; `by_reference` (the race base) names clips without carrying them.
    slot: Optional[object] = None
    owners: frozenset = frozenset()
    by_reference: bool = False
    source: Optional[Model] = None     # the spec's own DAT: commands looked up by offset
    waist: Optional[Model] = None      # an emote's part-2 clips (file-NUMBER +6)
    missing: Optional[str] = None      # why this race has no copy; its events are left out


@dataclass
class Composed:
    race: Optional[str]
    data: bytes
    sections: List[str]
    renames: Dict[str, str]
    timeline: List[dict]
    total: int
    warnings: List[str] = field(default_factory=list)


# ── Recipe loading ───────────────────────────────────────────────────────────────

RECIPE_SCHEMA = "xi.ability.v1"      # schema/ability_recipe.json
_SPEC_RX = re.compile(r"^(ja:\d+|ability:\d+|spell:\d+|ws:\d+(:[A-Za-z]+)?|fid:\d+"
                      r"|ROM[0-9]*/\d+/\d+(\.DAT)?|.+\.DAT)$", re.I)
_RECIPE_KEYS = {"schema", "name", "description", "dir", "target", "total", "sources", "events"}
_EVENT_KEYS = {"from", "op", "ref", "start", "dur", "order", "blend", "loops", "raw", "routine", "offset", "name"}


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_recipe(r) -> List[str]:
    """Problems with a recipe against ``schema/ability_recipe.json`` (empty when it
    conforms). Hand-rolled — jsonschema is not a dependency — and kept in step with
    the schema file, which is the specification."""
    errs: List[str] = []
    if not isinstance(r, dict):
        return ["recipe must be a JSON object"]
    for k in r:
        if k not in _RECIPE_KEYS:
            errs.append(f"unknown key {k!r}")
    if r.get("schema") not in (None, RECIPE_SCHEMA):
        errs.append(f"schema must be {RECIPE_SCHEMA!r}")
    name = r.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
        errs.append("name must be letters, digits, _ or -")
    if "dir" in r and not (isinstance(r["dir"], str) and 1 <= len(r["dir"]) <= 4):
        errs.append("dir must be 1–4 characters")
    if "total" in r and not (_is_int(r["total"]) and r["total"] >= 0):
        errs.append("total must be a non-negative integer")
    tgt = r.get("target")
    if tgt is not None:
        if not isinstance(tgt, dict):
            errs.append("target must be an object")
        else:
            if tgt.get("kind") not in (None, "ja", "spell", "ws"):
                errs.append("target.kind must be ja, spell or ws")
            if tgt.get("animation") is not None and not (_is_int(tgt["animation"]) and tgt["animation"] >= 0):
                errs.append("target.animation must be a non-negative integer or null")
            for k in tgt:
                if k not in ("kind", "animation"):
                    errs.append(f"unknown target key {k!r}")
    sources = r.get("sources")
    if not isinstance(sources, dict) or not sources:
        errs.append("sources must be an object with at least one lane")
        sources = {}
    for lane, src in sources.items():
        spec = src if isinstance(src, str) else (src.get("spec") if isinstance(src, dict) else None)
        if not isinstance(spec, str) or not _SPEC_RX.match(spec):
            errs.append(f"sources.{lane}: spec must be ja:N, spell:N, ws:N[:Race], fid:N, ROM/x/y or a .DAT path")
        if isinstance(src, dict):
            for k in src:
                if k not in ("spec", "routine", "name"):
                    errs.append(f"sources.{lane}: unknown key {k!r}")
            if "routine" in src and src["routine"] is not None and not isinstance(src["routine"], str):
                errs.append(f"sources.{lane}: routine must be a routine tag or null (a bare clip pack)")
    events = r.get("events")
    if not isinstance(events, list):
        errs.append("events must be a list")
        events = []
    for i, ev in enumerate(events):
        where = f"events[{i}]"
        if not isinstance(ev, dict):
            errs.append(f"{where}: must be an object")
            continue
        for k in ev:
            if k not in _EVENT_KEYS:
                errs.append(f"{where}: unknown key {k!r}")
        if "name" in ev and not isinstance(ev["name"], str):
            errs.append(f"{where}: 'name' must be a string")
        if not isinstance(ev.get("from"), str):
            errs.append(f"{where}: 'from' (lane) is required")
        elif sources and ev["from"] not in sources:
            errs.append(f"{where}: lane {ev['from']!r} is not in sources")
        op = ev.get("op")
        if _is_int(op):
            ok = 0 <= op <= 255
        else:
            ok = isinstance(op, str) and re.fullmatch(r"(0x)?[0-9A-Fa-f]{1,2}", op) is not None
        if not ok:
            errs.append(f"{where}: op must be an opcode 0–255 (or hex string)")
        if not (_is_int(ev.get("start")) and ev["start"] >= 0):
            errs.append(f"{where}: start must be a non-negative integer frame")
        if ev.get("ref") is not None and not (isinstance(ev["ref"], str) and 1 <= len(ev["ref"]) <= 4):
            errs.append(f"{where}: ref must be a 4-char section id")
        if ev.get("dur") is not None and not (_is_int(ev["dur"]) and ev["dur"] >= 0):
            errs.append(f"{where}: dur must be a non-negative integer")
        if "blend" in ev and not (isinstance(ev["blend"], list) and len(ev["blend"]) == 2
                                  and all(_is_int(b) and b >= 0 for b in ev["blend"])):
            errs.append(f"{where}: blend must be [transIn, transOut]")
        if "loops" in ev and not (_is_int(ev["loops"]) and ev["loops"] >= 0):
            errs.append(f"{where}: loops must be a non-negative integer")
        if "raw" in ev and not (isinstance(ev["raw"], str) and re.fullmatch(r"([0-9A-Fa-f]{2})+", ev["raw"])):
            errs.append(f"{where}: raw must be hex bytes")
        if "offset" in ev and not (_is_int(ev["offset"]) and ev["offset"] >= 0):
            errs.append(f"{where}: offset must be a non-negative integer")
    return errs


def load_recipe(path: Path) -> dict:
    """Read and validate a recipe file (schema/ability_recipe.json)."""
    try:
        r = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise click.ClickException(f"{path}: not valid JSON ({e})")
    errs = validate_recipe(r)
    if errs:
        shown = "\n  ".join(errs[:12]) + (f"\n  … {len(errs) - 12} more" if len(errs) > 12 else "")
        raise click.ClickException(f"{path} does not match schema/ability_recipe.json:\n  {shown}")
    return r


def _is_race_bound(spec: str) -> bool:
    kind, _, rest = spec.partition(":")
    return kind.lower() == "ws" and ":" not in rest


_ROM_SPEC_RX = re.compile(r"^ROM\d*/\d+/\d+(\.DAT)?$", re.I)
_TARU = frozenset({"TaruMale", "TaruFemale"})
_OWNER_CACHE: Dict[str, frozenset] = {}


def _race_owners(spec: str) -> frozenset:
    """The races whose motion set lists a DAT the motion tables do not index, from
    the character list (HumeF Variations is Hume Female's alone). Empty when no race
    lists it, or when every race does (a shared file, nothing to map)."""
    key = spec.replace("\\", "/").upper().removesuffix(".DAT")
    if key in _OWNER_CACHE:
        return _OWNER_CACHE[key]
    owners: Set[str] = set()
    try:
        from xi.entity.anim.xi_categories import CHARACTERS_LIST, _LIST_RACE_TO_XI, rom_key
        data = json.loads(Path(CHARACTERS_LIST).read_text(encoding="utf-8"))
        for race in data.get("races") or []:
            xi_race = _LIST_RACE_TO_XI.get(str(race.get("id") or ""))
            if not xi_race:
                continue
            for action in race.get("actions") or []:
                if any(rom_key(p) == key for p in (action.get("paths") or []) + (action.get("motionPaths") or [])):
                    owners.update(_TARU if xi_race in _TARU else {xi_race})
                    break
    except (OSError, ValueError):
        owners = set()
    out = frozenset() if len(owners) in (0, len(RACE_NAMES)) else frozenset(owners)
    _OWNER_CACHE[key] = out
    return out


_BAKE_KINDS = frozenset({"ja", "spell"})
_DEFAULT_BAKE_RACE = "HumeMale"


def _bake_spec(spec: str, ws: bool, slot, race: str) -> str:
    """One race's copy of a race-bound motion, to bake into a single DAT."""
    if ws:
        return f"{spec}:{race}"                       # ws:N -> ws:N:Race
    if slot is not None:
        mapped = motion_dat_for_race(slot, race)
        if mapped:
            return mapped
        raise click.ClickException(f"{spec}: {race} has no copy of this motion to bake")
    return spec                                        # a race's own file: bake it as it is


def _lanes(recipe: dict, bake_race: Optional[str] = None, kind: Optional[str] = None) -> Dict[str, Lane]:
    """The recipe's lanes. ``kind`` (the action's, else ``target.kind``) decides how a
    race-bound motion is handled: a job ability or spell is one DAT for every race, so it
    BAKES the motion from one race's copy (``bake_race``, HumeMale by default) into that
    DAT — the way retail Blue Magic carries its own ``wz*`` clips — instead of composing per
    race. The clips play on every race, since PC skeletons share their joints."""
    kind = kind or (recipe.get("target") or {}).get("kind")
    bake = kind in _BAKE_KINDS
    race = bake_race or _DEFAULT_BAKE_RACE
    lanes = {}
    for name, src in recipe["sources"].items():
        if isinstance(src, str):
            src = {"spec": src}
        spec = src["spec"]
        ws = _is_race_bound(spec)
        slot, owners = None, frozenset()
        if not ws and _ROM_SPEC_RX.match(spec.replace("\\", "/")):
            slot = motion_slot_for(spec)
            # The movement table's real files are the race base, +0..4 (idle, walk,
            # cast and job-ability motions); the rest of its window is unrelated.
            if slot is not None and slot.category == "movement" and slot.index > 4:
                slot = None
            if slot is None:
                owners = _race_owners(spec)
        by_reference = slot is not None and slot.category == "movement"
        race_bound = ws or (slot is not None and not by_reference) or bool(owners)
        if bake and race_bound:
            # Resolve to the bake race's DAT now; `slot` stays so an emote's waist
            # sibling (+6) still comes along in _load_lane.
            spec = _bake_spec(spec, ws, slot, race)
            race_bound, owners = False, frozenset()
        lanes[name] = Lane(name, spec, src.get("routine", "main"), race_bound,
                           slot=slot, owners=owners, by_reference=by_reference)
    return lanes


def _load_model(spec: str) -> Tuple[Target, Model]:
    t = resolve_targets(spec)[0]
    return t, Model.load(t.path)


def _load_lane(lane: Lane, race: Optional[str]) -> Lane:
    spec = lane.spec
    if lane.slot is not None and not lane.by_reference and race:
        mapped = motion_dat_for_race(lane.slot, race)
        from xi.xi_config import FFXI_DIR
        if not mapped or not (Path(FFXI_DIR) / mapped).is_file():
            return replace(lane, missing=f"{race}: {lane.name} ({lane.spec}) has no {race} copy")
        spec = mapped
    elif lane.owners and race and race not in lane.owners:
        return replace(lane, missing=f"{race}: {lane.name} ({lane.spec}) is "
                                     f"{', '.join(sorted(lane.owners))} only")
    elif lane.race_bound and race and lane.slot is None and not lane.owners:
        spec = f"{lane.spec}:{race}"      # ws:N
    t, m = _load_model(spec)
    # Commands a recipe names by routine + offset point into the spec's own DAT, not a
    # mapped race's copy (whose routines may sit elsewhere).
    source = m if spec == lane.spec or lane.slot is None else _load_model(lane.spec)[1]
    waist = None
    if lane.slot is not None and lane.slot.category == "emote" and not lane.slot.waist and not lane.by_reference:
        wspec = _waist_sibling_spec(t.rel)
        from xi.xi_config import FFXI_DIR
        if wspec and (Path(FFXI_DIR) / wspec).is_file():
            waist = _load_model(wspec)[1]
    # A lane with no routine (`"routine": null`) is a bare clip pack — Basic, the
    # emotes — whose events are plain PlayClip commands built from the template.
    if lane.routine is None:
        return replace(lane, spec=spec, model=m, target=t, events=[], source=source, waist=waist)
    if lane.routine not in source.routines:
        raise click.ClickException(f"lane {lane.name}: {lane.spec} has no routine {lane.routine!r}")
    return replace(lane, spec=spec, model=m, target=t, events=flatten(source, lane.routine),
                   source=source, waist=waist)


# ── Command bytes ────────────────────────────────────────────────────────────────

def _op(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def _name4(s: str) -> bytes:
    return s.encode("ascii")[:4].ljust(4, b"\0")


def _source_command(lane: Lane, ev: dict) -> bytes:
    """The command bytes an event stands for: explicit ``raw``, else the source routine
    command at ``routine``/``offset``, else the first flattened event with the same op and
    ref, else a template."""
    op = _op(ev["op"])
    if ev.get("raw"):
        return bytes.fromhex(ev["raw"])
    if ev.get("routine") and ev.get("offset") is not None and (lane.source or lane.model):
        return (lane.source or lane.model).raw_command(ev["routine"], int(ev["offset"]))
    ref = ev.get("ref")
    for e in lane.events:
        if e["op"] == op and (e["ref"] == ref or (ref is None and e["ref"] is None)):
            return bytes.fromhex(e["raw"])
    tpl = _TEMPLATES.get(op)
    if tpl is None:
        raise click.ClickException(
            f"event op 0x{op:02X} ref {ref!r} is not in lane {lane.name!r}'s routine and has "
            f"no template — give it 'raw' bytes")
    return tpl


def _stamp(cmd: bytes, *, delay: int, ev: dict, ref: Optional[str]) -> bytes:
    b = bytearray(cmd)
    op = b[0]
    struct.pack_into("<H", b, 4, max(0, min(0xFFFF, delay)))
    if ev.get("dur") is not None:
        struct.pack_into("<H", b, 6, max(0, min(0xFFFF, int(ev["dur"]))))
    if ref is not None and op in REF_OPS and len(b) >= 12:
        b[8:12] = _name4(ref)
    if op == 0x05 and len(b) >= 32:
        if ev.get("blend") is not None:
            ti, to = ev["blend"]
            struct.pack_into("<H", b, 24, int(ti))
            struct.pack_into("<H", b, 28, int(to))
        if ev.get("loops") is not None:
            struct.pack_into("<H", b, 30, int(ev["loops"]))
    return bytes(b)


def build_routine(tag: str, commands: List[bytes], total: int, lead: int = 0) -> bytes:
    """A complete ``0x07`` section: 16-byte header, 16 zero bytes, the sec1/sec2/sec3
    offsets + totalDelay, sec1 (end marker), sec2 (start, commands, end), sec3 (end).
    ``lead`` is the wait before the first command; it rides on the start marker's own
    delay field, since a command's delay is the wait *after* it."""
    start = bytearray(_ROUTINE_START)
    struct.pack_into("<H", start, 4, max(0, min(0xFFFF, lead)))
    sec2 = bytes(start) + b"".join(commands) + _ROUTINE_END
    sec2 += b"\0" * (-len(sec2) % 16)
    s1, s2 = 0x40, 0x50
    s3 = s2 + len(sec2)
    body = (b"\0" * 16 + struct.pack("<4I", s1, s2, s3, total) + b"\0" * 16
            + _ROUTINE_SEC1 + sec2 + _ROUTINE_SEC1)
    size = 16 + len(body)
    return _name4(tag) + struct.pack("<I", encode_section_meta(size, T_ROUTINE)) + b"\0" * 8 + body


# ── Section transplanting ────────────────────────────────────────────────────────

@dataclass
class Bag:
    """Sections gathered for the output, keyed by (name, type). ``origin`` remembers which
    lane each came from so collisions can be renamed per source."""
    items: Dict[Tuple[str, int], bytes] = field(default_factory=dict)
    origin: Dict[Tuple[str, int], str] = field(default_factory=dict)
    order: List[Tuple[str, int]] = field(default_factory=list)
    renames: Dict[Tuple[str, str], str] = field(default_factory=dict)   # (lane, old) -> new

    def names_in_use(self) -> Set[str]:
        return {n for n, _ in self.items}

    def add(self, lane: str, name: str, tc: int, data: bytes) -> str:
        key = (name, tc)
        if key in self.items:
            if self.items[key] == data or self.origin[key] == lane:
                return name
            new = self.renames.get((lane, name)) or _fresh_name(name, self.names_in_use())
            self.renames[(lane, name)] = new
            data = data[:0] + _name4(new) + data[4:]
            key = (new, tc)
            name = new
        self.items[key] = data
        self.origin[key] = lane
        self.order.append(key)
        return name


def _fresh_name(name: str, used: Set[str]) -> str:
    stem = name[:3]
    for c in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ":
        cand = stem + c
        if cand not in used and cand != name:
            return cand
    for c in "xyzwvu":
        for d in "0123456789":
            cand = c + name[1:3] + d
            if cand not in used:
                return cand
    raise click.ClickException(f"cannot find a free name for {name!r}")


def _sections_named(m: Model, name: str, types) -> List:
    return [s for s in m.sections if _clean(s.name) == name and s.type_code in types]


def _match(m: Model, pattern: str, tc: int) -> List[str]:
    names = {_clean(s.name) for s in m.sections if s.type_code == tc}
    if "?" in pattern:
        return sorted(n for n in names if fnmatch.fnmatchcase(n, pattern.replace("[", "[[]")))
    return [pattern] if pattern in names else []


def _gather_generator(bag: Bag, lane: Lane, gen: str) -> Optional[str]:
    m = lane.model
    secs = _sections_named(m, gen, (T_GEN,))
    if not secs:
        return None
    s = secs[0]
    body = m.data[s.start:s.start + s.size]
    new = bag.add(lane.name, gen, T_GEN, body)
    for cc in _effect_deps(m.data, m.sections, s):
        dep = cc.rstrip(b"\0 ").decode("latin1")
        for ds in m.sections:
            if _clean(ds.name) == dep and ds.type_code in _DEP_TYPES:
                bag.add(lane.name, dep, ds.type_code, m.data[ds.start:ds.start + ds.size])
    return new


def _gather_for_command(bag: Bag, lane: Lane, op: int, ref: Optional[str],
                        carried_routines: Set[str]) -> Optional[str]:
    """Bring the sections command ``op``/``ref`` needs from the lane's DAT. Returns the
    (possibly renamed) ref the command should carry."""
    m = lane.model
    if ref is None or m is None:
        return ref
    if op in VFX_OPS:
        new = _gather_generator(bag, lane, ref)
        if new is None:
            raise click.ClickException(
                f"lane {lane.name!r} ({lane.target.rel}) has no generator {ref!r}; "
                f"it has: {', '.join(sorted(m.gens)) or 'none'}")
        return new
    if op in SOUND_OPS:
        for s in _sections_named(m, ref, (T_SOUND,)):
            return bag.add(lane.name, ref, T_SOUND, m.data[s.start:s.start + s.size])
        raise click.ClickException(
            f"lane {lane.name!r} ({lane.target.rel}) has no sound pointer {ref!r}; "
            f"it has: {', '.join(sorted(m.sounds)) or 'none'}")
    if op == 0x05:
        if lane.by_reference:
            return ref            # the race base is always loaded: name the clip, carry nothing
        for model in (m, lane.waist):
            if model is None:
                continue
            for clip in _match(model, ref, T_CLIP):
                s = _sections_named(model, clip, (T_CLIP,))[0]
                bag.add(lane.name, clip, T_CLIP, model.data[s.start:s.start + s.size])
        return ref
    if op == 0x2C:
        for tr in _match(m, ref, T_TRACE):
            s = _sections_named(m, tr, (T_TRACE,))[0]
            bag.add(lane.name, tr, T_TRACE, m.data[s.start:s.start + s.size])
        return ref
    if op in LINK_OPS and ref in m.routines and ref not in carried_routines:
        carried_routines.add(ref)
        s = m.routines[ref]
        from xi.event.xi_event import _routine_sec2_commands
        for c in _routine_sec2_commands(m.data, ref):
            if c["op"] in REF_OPS and c["ref"]:
                _gather_for_command(bag, lane, c["op"], c["ref"], carried_routines)
        bag.add(lane.name, ref, T_ROUTINE, m.data[s.start:s.start + s.size])
        return ref
    return ref


def _apply_renames(bag: Bag) -> None:
    """Patch every renamed name inside the bodies of sections that came from the same
    lane (generators name their textures/meshes/curves/sounds by 4-char id; routines name
    their generators and clips)."""
    if not bag.renames:
        return
    by_lane: Dict[str, List[Tuple[bytes, bytes]]] = {}
    for (lane, old), new in bag.renames.items():
        by_lane.setdefault(lane, []).append((_name4(old), _name4(new)))
    for key in list(bag.items):
        lane = bag.origin[key]
        pairs = by_lane.get(lane)
        if not pairs or key[1] not in (T_GEN, T_ROUTINE):
            continue
        body = bytearray(bag.items[key])
        for old, new in pairs:
            i = body.find(old, 16)
            while i >= 0:
                body[i:i + 4] = new
                i = body.find(old, i + 4)
        bag.items[key] = bytes(body)


# ── Composition ──────────────────────────────────────────────────────────────────

_TYPE_ORDER = {T_CLIP: 0, T_TRACE: 1, T_GEN: 2, T_ROUTINE: 3, 0x19: 4, 0x1F: 5, 0x25: 6,
               0x20: 7, 0x21: 8, 0x2E: 9, T_SOUND: 10}


def _base_clip_gaps(lane: Lane, ref: str, race: Optional[str], cache: Dict[str, Optional[Model]]) -> List[str]:
    """Races (``race`` alone, or all of them) whose race base has no clip matching
    ``ref`` — a by-reference lane names the clip, and those races will not play it."""
    from xi.xi_config import FFXI_DIR
    gaps = []
    for r in ([race] if race else RACE_NAMES):
        mapped = motion_dat_for_race(lane.slot, r)
        if mapped not in cache:
            cache[mapped] = _load_model(mapped)[1] if mapped and (Path(FFXI_DIR) / mapped).is_file() else None
        if cache[mapped] is None or not _match(cache[mapped], ref, T_CLIP):
            gaps.append(r)
    return gaps


def compose_once(recipe: dict, lanes: Dict[str, Lane], race: Optional[str]) -> Composed:
    loaded = {n: _load_lane(l, race) for n, l in lanes.items()}
    bag = Bag()
    carried: Set[str] = set()
    warnings: List[str] = []
    left_out: Dict[str, int] = {}
    bases: Dict[str, Optional[Model]] = {}
    events = sorted(recipe["events"], key=lambda e: (int(e["start"]), e.get("order", 0)))
    stamped: List[Tuple[dict, bytes, Optional[str]]] = []
    for ev in events:
        lane = loaded.get(ev.get("from"))
        if lane is None:
            raise click.ClickException(f"event refers to unknown lane {ev.get('from')!r}")
        op = _op(ev["op"])
        ref = ev.get("ref")
        if lane.missing:
            left_out[lane.name] = left_out.get(lane.name, 0) + 1
            continue
        if op == 0x05 and ref:
            if lane.by_reference:
                gaps = _base_clip_gaps(lane, ref, race, bases)
                if gaps:
                    warnings.append(f"{', '.join(gaps)}: no clip {ref} in the race base; "
                                    f"{'that race' if len(gaps) == 1 else 'those races'} will not play it")
            elif (lane.slot is not None or lane.owners) and not any(
                    _match(model, ref, T_CLIP) for model in (lane.model, lane.waist) if model is not None):
                warnings.append(f"{race}: no clip {ref} in {lane.target.rel}; built without it")
                continue
        cmd = _source_command(lane, ev)
        new_ref = _gather_for_command(bag, lane, op, ref, carried) if ref else None
        if ref and new_ref == ref and (lane.name, ref) in bag.renames:
            new_ref = bag.renames[(lane.name, ref)]
        stamped.append((ev, cmd, new_ref))
    _apply_renames(bag)

    # A command's delay is the wait AFTER it (see xi_inspect.flatten): each gets the
    # gap to the next start, the first start rides on the start marker, and the last
    # command waits out to totalDelay — the routine's end, which retail sets to the
    # last command's window (its start + dur). A recipe from `xi ability recipe`
    # carries the source's own total so it round-trips exactly.
    starts = [int(ev["start"]) for ev, _, _ in stamped]
    end = max([s + int(ev.get("dur") or 0) for s, (ev, _, _) in zip(starts, stamped)] + starts + [0])
    total = int(recipe.get("total") or 0)
    # No total in the recipe, or one that ends before the last command starts: the
    # routine ends where its last window does. (A lone clip at frame 0 used to keep
    # total 0 — the routine ended before the clip had played a frame.)
    if not total or total < (starts[-1] if starts else 0):
        total = end
    commands, timeline = [], []
    for i, (ev, cmd, ref) in enumerate(stamped):
        start = starts[i]
        nxt = starts[i + 1] if i + 1 < len(starts) else total
        ref_out = ref if ref is not None else ev.get("ref")
        if ref_out and (ev.get("from"), ref_out) in bag.renames:
            ref_out = bag.renames[(ev.get("from"), ref_out)]
        commands.append(_stamp(cmd, delay=nxt - start, ev=ev, ref=ref_out))
        timeline.append({"start": start, "op": _op(ev["op"]), "ref": ref_out,
                         "dur": ev.get("dur"), "from": ev.get("from")})
    main = build_routine("main", commands, total, lead=starts[0] if starts else 0)

    dir_name = recipe.get("dir") or re.sub(r"[^A-Za-z0-9]", "", recipe["name"])[:4].ljust(4, "_")
    ordered = sorted(bag.order, key=lambda k: (_TYPE_ORDER.get(k[1], 99), bag.order.index(k)))
    out = bytearray()
    out += _name4(dir_name) + struct.pack("<I", encode_section_meta(32, T_DIR)) + b"\0" * 8 + _DIR_DATA
    names = []
    for key in ordered:
        out += bag.items[key]
        names.append(f"{key[0]}(0x{key[1]:02X})")
    out += main
    names.append("main(0x07)")
    out += _END_SECTION
    renames = {f"{lane}:{old}": new for (lane, old), new in bag.renames.items()}
    for name, n in left_out.items():
        warnings.append(f"{loaded[name].missing}; built without its {n} event{'s' if n != 1 else ''}")
    return Composed(race, bytes(out), names, renames, timeline, total, list(dict.fromkeys(warnings)))


def compose(recipe: dict, race: Optional[str] = None, kind: Optional[str] = None) -> List[Composed]:
    """Compose the recipe: once per race when a lane is race-bound, else once. ``kind``
    (ja / spell) bakes a race-bound motion from one race — ``race``, else HumeMale —
    into that single DAT instead (see _lanes)."""
    lanes = _lanes(recipe, bake_race=race, kind=kind)
    race_bound = any(l.race_bound for l in lanes.values())
    if race_bound:
        races = [race] if race else list(RACE_NAMES)
        return [compose_once(recipe, lanes, r) for r in races]
    return [compose_once(recipe, lanes, None)]


def output_name(recipe: dict, c: Composed) -> str:
    return f"{recipe['name']}.{c.race}.DAT" if c.race else f"{recipe['name']}.DAT"


# ── Starter recipes ──────────────────────────────────────────────────────────────

def recipe_from(spec: str, lane: str = "motion", routine: str = "main") -> dict:
    """A recipe that reproduces one source's routine event for event — the starting
    point the mixer edits, and the round-trip proof for the writer."""
    t = resolve_targets(spec)[0]
    m = Model.load(t.path)
    events = []
    for e in flatten(m, routine):
        if e["via"]:
            continue                      # linked routines are carried whole by their link
        ev = {"from": lane, "op": e["op"], "ref": e["ref"], "start": e["start"],
              "routine": e["routine"], "offset": e["offset"], "raw": e["raw"]}
        if e["dur"]:
            ev["dur"] = e["dur"]
        events.append(ev)
    kind = "ws" if spec.startswith("ws:") else ("spell" if spec.startswith("spell:") else "ja")
    return {"schema": RECIPE_SCHEMA,
            "name": re.sub(r"[^A-Za-z0-9_]+", "_", spec).strip("_"),
            "target": {"kind": kind, "animation": None},
            "sources": {lane: {"spec": spec, "routine": routine}},
            # The source's own totalDelay (the routine's end, past the last command's
            # start), so a compose of this recipe round-trips the header exactly.
            "total": m.routine_total(routine),
            "events": events}


# ── CLI ──────────────────────────────────────────────────────────────────────────

@click.command("compose")
@click.argument("recipe_path", type=click.Path(exists=True, path_type=Path))
@click.option("--out", "out_dir", type=click.Path(path_type=Path), default=None,
              help="Output folder (default: exports/ability/<name>/).")
@click.option("--race", default=None, help="Compose one race only (race-bound recipes); the bake race for a ja/spell.")
@click.option("--kind", type=click.Choice(["ja", "spell", "ws"]), default=None,
              help="Publish kind: a job ability or spell bakes a race-bound motion from one race into its single DAT.")
@click.option("--json", "as_json", is_flag=True, help="Print the compose report as JSON.")
def compose_cmd(recipe_path: Path, out_dir: Optional[Path], race: Optional[str], kind: Optional[str], as_json: bool):
    """Build ability DAT(s) from RECIPE_PATH. Writes <name>[.<Race>].DAT plus a report."""
    recipe = load_recipe(recipe_path)
    out_dir = out_dir or Path("exports") / "ability" / recipe["name"]
    out_dir.mkdir(parents=True, exist_ok=True)
    results = compose(recipe, race, kind=kind)
    report = []
    for c in results:
        p = out_dir / output_name(recipe, c)
        p.write_bytes(c.data)
        report.append({"race": c.race, "dat": str(p), "size": len(c.data), "total": c.total,
                       "sections": c.sections, "renames": c.renames, "timeline": c.timeline,
                       "warnings": c.warnings})
    (out_dir / f"{recipe['name']}.report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if as_json:
        click.echo(json.dumps(report, indent=2))
        return
    for r in report:
        click.echo(f"{r['dat']}  {r['size']:,} B  totalDelay {r['total']}  {len(r['sections'])} sections"
                   + (f"  renames {r['renames']}" if r["renames"] else ""))
        for w in r["warnings"]:
            click.echo(click.style(f"  ⚠ {w}", fg="yellow"))


@click.command("recipe")
@click.argument("spec")
@click.option("--lane", default="motion", show_default=True)
@click.option("--routine", default="main", show_default=True)
@click.option("--out", "out_path", type=click.Path(path_type=Path), default=None,
              help="Write the recipe here (default: stdout).")
def recipe_cmd(spec: str, lane: str, routine: str, out_path: Optional[Path]):
    """Write a starter recipe that reproduces SPEC's routine event for event."""
    r = recipe_from(spec, lane, routine)
    text = json.dumps(r, indent=2)
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        click.echo(f"wrote {out_path} ({len(r['events'])} events)")
    else:
        click.echo(text)

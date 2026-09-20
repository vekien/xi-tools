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
resolved by renaming the later section and patching its references. A lane's rename
reaches a routine a command names only when that routine was carried from the lane: a
link to a shared or actor routine (``mdam``, ``proc``, ``sh??``) keeps its name, even where
the lane renamed a section of that name.

An event with no ``from`` is a command of no lane — a lock, a hit, a link to a shared or
actor routine added by hand — and carries its own ``raw`` bytes (validate_recipe checks
them)::

    {"op": 32, "start": 0,  "dur": 100, "raw": "200300000000640000000000"}
    {"op": 3,  "ref": "mdam", "start": 80, "raw": "03040000000000006d64616d00000000"}

Compose writes it as given, with the delay, ``dur`` and a named routine's ``ref`` stamped
in: nothing is gathered for it, no lane's rename touches it, and every race's DAT has it.
A command that names a routine the game will not find where that command looks (this DAT,
ROM/0/0.DAT, or for 0x09 / 0x3C / 0x57 / 0x85 the actor) is written anyway and the result
says so in ``warnings``.

A recipe can also change what it carries. ``generators`` lists in-place edits to a lane's
particle generators and ``curves`` replaces the keys of a lane's keyframe curves, each
addressed by lane and the section's name in the source DAT::

    "generators": [{"lane": "vfx", "ref": "g000", "edits": [
        {"sec": 2, "op": "0x16", "at": 4, "type": "u8", "value": [51, 128, 198, 38]}]}],
    "curves": [{"lane": "vfx", "ref": "k001", "keys": [[0, 0], [0.5, 0.8], [1, 0]]}]

The edits (xi_genedit) are written to the lane's copy of the section before it joins the
output, so a second lane on the same spec keeps its own, and on every race's copy for a
race-bound lane. The source DAT is never touched.

``textures`` replaces a lane's ``0x20`` texture with a PNG, addressed the same way::

    "textures": [{"lane": "vfx", "ref": "ho", "png": "data:image/png;base64,iVBORw0KGgo..."}]

An entry may also give the texture's 16-character ``name``: a DAT can give several
textures one id (ROM/11/21's five ``faid``, "faida01 faida01" to "faida01 faida05") but
never one name, and an entry without it is the first texture of that id.

Compose encodes each distinct PNG once (DXT3, each side a power of two up to 256, alpha
halved: the PNG's 255 is FFXI's 0x80 opaque) and keeps the section's id, 16-character
name and flags, so every mesh and sprite sheet of that lane that names the texture draws
it. ``load_recipe`` reads a ``png`` given as a path relative to the recipe file and puts
its data URI in its place, so compose itself only sees data URIs.

A mesh or sprite sheet binds its texture by that 16-character name, the first match in
the file winning, so compose carries the texture a carried mesh names (not only one whose
id the mesh spells), a second texture of one id under a new id of its own (an id names
the first), and when two lanes would put different textures of one name into the output
the later lane's is renamed and that lane's meshes patched to match.

A lane whose spec is a weapon-skill number without a race (``ws:1``) is race-bound, so the
recipe composes once per race and the output is one DAT per race, laid out like a retail
weapon-skill body: a race root ``<tag>_<c>`` (``hf_1``) holding the effect folder (the
recipe's ``dir``: the sections and ``main``) and a clip folder of the root's name (clips,
then weapon traces). A single DAT (a job ability or spell) is one effect folder holding
everything, as retail's are.

A lane read from a PC race's own motion file is race-bound the same way: the client's
per-race motion tables (xi_motion_tables) map an emote, battle pack, dance or weapon-skill
DAT to every race's copy, and each race's DAT carries that race's clips. A motion's waist
(part 2, from an emote's +6 sibling) goes to the weapon-skill body's companion DAT, not the
body — retail keeps parts 0/1 in the body and part 2 in the +256/+512 companions, and the
client reads a weapon skill's waist from the companion (``Composed.companion``). A race without a copy, or without the clip, is built
without that motion and the composed result says so in ``warnings``. The race base (the
``movement`` table: idle, walk, cast and job-ability motions) is always loaded on a
character, so its clips are named, not carried, and a job ability or spell can use them. A
motion file the tables do not index but the character list gives to some races (a race's
Variations) is kept for those races only.
"""

from __future__ import annotations

import base64
import binascii
import fnmatch
import hashlib
import io
import itertools
import json
import re
import struct
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path, PureWindowsPath
from typing import Dict, List, Optional, Set, Tuple

import click

from xi.ability.xi_genedit import EditError, apply_curve, apply_edits, check_edit, check_keys, is_section_id, walk_stream
from xi.ability.xi_inspect import (
    LINK_OPS, REF_OPS, SOUND_OPS, T_CLIP, T_CURVE, T_DIR, T_GEN, T_PMESH, T_ROUTINE, T_SOUND, T_SPRITE,
    T_TEX, T_TRACE, Model, Target, _clean, flatten, resolve_targets)
from xi.common.xi_section import encode_section_meta
from xi.entity.anim.xi_motion_tables import (
    RACE_NAMES, _waist_sibling_spec, motion_dat_for_race, motion_slot_for, race_index)
from xi.fx.xi_copy import _DEP_TYPES, TEXTURE_NAME_AT, _effect_deps, texture_key, texture_name_fields

VFX_OPS = frozenset({0x02, 0x3F, 0x1E, 0x2D})
# Ops whose +8 names a routine: the links, 0x30 (run it once per target), 0x5F (stop every
# running copy), 0x73 / 0x85 (start it as a loop, stop it).
_ROUTINE_REF_OPS = LINK_OPS | frozenset({0x30, 0x5F, 0x73, 0x85})
# Ops whose +8 names a routine, sound or generator the client looks up by that name.
# Retail writes 0 at +0xC on every one: the client keeps its pointer to what it found there
# (PS2 ExecuteTag: 0x03, 0x30, 0x3B, 0x5F, 0x73, the sounds and the generators) and takes
# anything else for one.
_NAMED_OPS = _ROUTINE_REF_OPS | SOUND_OPS | VFX_OPS
# What a command of no lane cannot be: a generator, sound, clip or weapon trace names a
# section compose carries from its lane's DAT.
_LANE_OPS = {**dict.fromkeys(VFX_OPS, "generator"), **dict.fromkeys(SOUND_OPS, "sound"),
             0x05: "clip", 0x2C: "weapon trace"}
_GEN_TYPES = (T_GEN,) + tuple(_DEP_TYPES)
_BINDING_TYPES = (T_PMESH, T_SPRITE, 0x2E)     # what names a texture by its 16-char name (0x2E ZoneMesh)

# Retail byte layouts (docs/fx/effect_system.md §1 and §3, verified on ROM/76/30 and
# ROM/15/89). A directory's payload is the 16 bytes after its header, and the client reads
# the high nibble of its byte 3. Compose writes retail's commonest values: 2 on an effect folder (a job
# ability's or spell's root, a weapon skill's `0011` / `1111`; about a third of retail WS effect folders
# carry 0xA) and 0 on a weapon skill's race root and clip folder (retail roots are sometimes 0xA).
_DIR_DATA = bytes.fromhex("00000020") + bytes(12)                # an effect directory's payload
_DIR_PLAIN = bytes(16)                                           # a race root's or clip folder's payload
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

# 0x75 ShowHideWeapon, verbatim from ROM/0/0.DAT `hwmg` (hide main+sub if engaged) — the
# tag every spell cast runs to stow the weapon while it plays. An emote or dance never
# moves the weapon hand (skeleton reference 127; HumeMale joint 68), so the client hangs a
# drawn weapon at rest when it plays one as a weapon skill. Emitting these hides main (slot
# 0) and sub (slot 1) for the action; the idle motion that resumes when the skill ends
# shows them again (xisklactor ShowWeapon), so nothing has to turn them back on.
_HIDE_WEP_MAIN = bytes.fromhex("75040000000000000100000000000100")
_HIDE_WEP_SUB = bytes.fromhex("75040000000000000100000001000100")


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
    # The recipe's edits to this lane's sections, by the section's name in the source
    # DAT: (the recipe entry an error names, its edits / its keys).
    gen_edits: Dict[str, Tuple[str, list]] = field(default_factory=dict)
    curve_edits: Dict[str, Tuple[str, list]] = field(default_factory=dict)
    # Textures by (id, name as texture_key), the name None for an entry without one (the
    # first texture of that id): (the entry, the PNG's bytes).
    tex_edits: Dict[Tuple[str, Optional[bytes]], Tuple[str, bytes]] = field(default_factory=dict)


@dataclass
class Composed:
    race: Optional[str]
    data: bytes
    sections: List[str]
    renames: Dict[str, str]
    timeline: List[dict]
    total: int
    warnings: List[str] = field(default_factory=list)
    textures: List[dict] = field(default_factory=list)   # replaced and renamed textures (_texture_report)
    # A weapon-skill body's waist (part-2) clips as a companion DAT: retail keeps parts 0/1
    # in the body and part 2 in the +256/+512 companions, and the client reads a WS's waist
    # from the companion. Set when a non-weapon motion (an emote) carried a +6 waist sibling.
    companion: Optional[bytes] = None


# ── Recipe loading ───────────────────────────────────────────────────────────────

RECIPE_SCHEMA = "xi.ability.v1"      # schema/ability_recipe.json
_SPEC_RX = re.compile(r"^(ja:\d+|ability:\d+|spell:\d+|ws:\d+(:[A-Za-z]+)?|fid:\d+"
                      r"|ROM[0-9]*/\d+/\d+(\.DAT)?|.+\.DAT)$", re.I)
_RECIPE_KEYS = {"schema", "name", "description", "dir", "target", "total", "sources", "events",
                "generators", "curves", "textures"}
_EVENT_KEYS = {"from", "op", "ref", "start", "dur", "order", "blend", "loops", "raw", "routine", "offset", "name",
               "row"}


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_recipe(r) -> List[str]:
    """Problems with a recipe against ``schema/ability_recipe.json`` (empty when it
    conforms). Hand-rolled — jsonschema is not a dependency — and kept in step with
    the schema file, which is the specification. On a Python dict an integer field
    must be an int; load_recipe reads a JSON 4.0 (a schema integer) as 4."""
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
        if "row" in ev and not (_is_int(ev["row"]) and ev["row"] >= 0):
            errs.append(f"{where}: row must be a non-negative integer")
        # No `from`: a command of no lane (a lock, hit or link added by hand), which is
        # nothing but its own bytes.
        if "from" in ev:
            if not isinstance(ev["from"], str):
                errs.append(f"{where}: 'from' must be a lane (a key of sources)")
            elif sources and ev["from"] not in sources:
                errs.append(f"{where}: lane {ev['from']!r} is not in sources")
        elif "raw" not in ev:
            errs.append(f"{where}: an event with no 'from' (lane) is a command of its own and must carry its 'raw' bytes")
        op = ev.get("op")
        if _is_int(op):
            ok = 0 <= op <= 255
        else:
            ok = isinstance(op, str) and re.fullmatch(r"(0x)?[0-9A-Fa-f]{1,2}", op) is not None
        if not ok:
            errs.append(f"{where}: op must be an opcode 0–255 (or hex string)")
        opn = _op(op) if ok else None
        if "from" not in ev and opn in _LANE_OPS:
            errs.append(f"{where}: op 0x{opn:02X} names a {_LANE_OPS[opn]} of its lane's DAT, so it needs 'from'")
        if not (_is_int(ev.get("start")) and ev["start"] >= 0):
            errs.append(f"{where}: start must be a non-negative integer frame")
        ref = ev.get("ref")
        if ref is not None and not is_section_id(ref):
            errs.append(f"{where}: ref must be a 1–4 character section id")
            ref = None
        if ev.get("dur") is not None and not (_is_int(ev["dur"]) and ev["dur"] >= 0):
            errs.append(f"{where}: dur must be a non-negative integer")
        if "blend" in ev and not (isinstance(ev["blend"], list) and len(ev["blend"]) == 2
                                  and all(_is_int(b) and b >= 0 for b in ev["blend"])):
            errs.append(f"{where}: blend must be [transIn, transOut]")
        if "loops" in ev and not (_is_int(ev["loops"]) and ev["loops"] >= 0):
            errs.append(f"{where}: loops must be a non-negative integer")
        if "raw" in ev:
            if not (isinstance(ev["raw"], str) and re.fullmatch(r"([0-9A-Fa-f]{2})+", ev["raw"])):
                errs.append(f"{where}: raw must be hex bytes")
            else:
                errs.extend(f"{where}: {why}" for why in raw_problems(bytes.fromhex(ev["raw"]), opn, ref))
        if "offset" in ev and not (_is_int(ev["offset"]) and ev["offset"] >= 0):
            errs.append(f"{where}: offset must be a non-negative integer")
    _validate_section_edits(r, sources, errs)
    return errs


def _is_name(b: bytes) -> bool:
    """A routine, sound or generator name as a command stores it at +8: 1–4 printable
    ASCII characters, not only spaces, padded with NULs."""
    s = b.rstrip(b"\0")
    return 1 <= len(s) <= 4 and all(0x20 <= c < 0x7F for c in s) and bool(s.strip(b" "))


def raw_problems(raw: bytes, op: Optional[int] = None, ref: Optional[str] = None) -> List[str]:
    """Why ``raw`` cannot go into a routine as one command of ``op``, as phrases (empty when
    it can). The client walks a routine by each command's size (the low 5 bits of byte +1,
    in dwords), so bytes that say another size than they are run every later command
    together; +4 and +6 are the delay and duration compose stamps. A command that names a
    routine, sound or generator (``_NAMED_OPS``) has that name at +8 — ``ref`` when the
    event gives one, which compose writes there — and 0 at +0xC."""
    if len(raw) < 8 or len(raw) % 4:
        return [f"raw is {len(raw)} bytes; a command is whole dwords, 8 bytes at least (op, size, delay, duration)"]
    out = []
    size = max(1, raw[1] & 0x1F) * 4
    if size != len(raw):
        out.append(f"raw is {len(raw)} bytes but its size byte (+1) says {size}")
    if op is not None and raw[0] != op:
        out.append(f"raw is op 0x{raw[0]:02X}, not the event's op 0x{op:02X}")
    if raw[0] in _NAMED_OPS or op in _NAMED_OPS:
        if len(raw) < 16:
            out.append(f"raw is {len(raw)} bytes; a command that names a routine, sound or generator is 16 at "
                       f"least (the name at +8, 0 at +0xC)")
            return out
        if raw[12:16] != bytes(4):
            out.append(f"raw has {raw[12:16].hex()} at +0xC, where it must have 0: the client keeps a pointer "
                       f"there once it has looked the name up, and takes anything else for one")
        if (ref is None or raw[0] not in REF_OPS) and not _is_name(raw[8:12]):
            out.append(f"raw names {raw[8:12].hex()} at +8; a routine, sound or generator name is 1–4 printable "
                       f"ASCII characters, padded with NULs")
    return out


def _validate_section_edits(r: dict, sources: dict, errs: List[str]) -> None:
    """``generators``, ``curves`` and ``textures``: which lane's section, and what to
    write in it. Whether the section exists and the edit fits it is known only with the
    lane's DAT open, so compose reports that."""
    for key, body in (("generators", "edits"), ("curves", "keys"), ("textures", "png")):
        if key not in r:
            continue
        if not isinstance(r[key], list):
            errs.append(f"{key} must be a list")
            continue
        allowed = ("lane", "ref", body, "name") if key == "textures" else ("lane", "ref", body)
        seen: Dict[Tuple[str, str, Optional[bytes]], int] = {}
        for i, row in enumerate(r[key]):
            where = f"{key}[{i}]"
            if not isinstance(row, dict):
                errs.append(f"{where}: must be an object")
                continue
            for k in row:
                if k not in allowed:
                    errs.append(f"{where}: unknown key {k!r}")
            lane, ref = row.get("lane"), row.get("ref")
            if not isinstance(lane, str):
                errs.append(f"{where}: 'lane' is required")
            elif sources and lane not in sources:
                errs.append(f"{where}: lane {lane!r} is not in sources")
            # A texture entry may name the section too: one DAT can give several 0x20
            # sections one id (ROM/11/21's five `faid`), never one 16-character name.
            name = row.get("name") if key == "textures" else None
            bad_name = key == "textures" and "name" in row and not is_texture_name(name)
            if bad_name:
                errs.append(f"{where}: name must be a texture's 16-character name: 1–16 printable ASCII "
                            f"characters, not only spaces")
            if not is_section_id(ref):
                errs.append(f"{where}: ref must be a 1–4 character section id")
            elif isinstance(lane, str) and not bad_name:
                first = seen.setdefault((lane, _clean(ref), _name_key(name)), i)
                if first != i:
                    named = f" name {name!r}" if name is not None else ""
                    errs.append(f"{where}: lane {lane!r} ref {ref!r}{named} is already in {key}[{first}]")
            if key == "curves":
                errs.extend(f"{where}: {e}" for e in check_keys(row.get("keys")))
                continue
            if key == "textures":
                why = _png_value_problem(row.get("png"))
                if why:
                    errs.append(f"{where}: {why}")
                continue
            edits = row.get("edits")
            if not isinstance(edits, list) or not edits:
                errs.append(f"{where}: edits must be a non-empty list")
                continue
            for j, e in enumerate(edits):
                errs.extend(f"{where}.edits[{j}]: {x}" for x in check_edit(e))


_TEXTURE_NAME_RX = re.compile(r"[ -~]*[!-~][ -~]*")


def is_texture_name(v) -> bool:
    """A texture entry's ``name``: the 16-character name at a 0x20 section's +0x11, as
    1–16 printable ASCII characters and not only spaces. Trailing spaces are dropped when
    it is compared, as they are when a mesh binds the texture."""
    return isinstance(v, str) and len(v) <= 16 and _TEXTURE_NAME_RX.fullmatch(v) is not None


def _name_key(name: Optional[str]) -> Optional[bytes]:
    """A texture entry's ``name`` as texture_key compares it; None (no name) stays None."""
    return None if name is None else texture_key(name.encode("ascii"))


# A replacement texture's PNG: what validate_recipe can tell without the DAT.
PNG_URI = "data:image/png;base64,"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_MAX_BYTES = 2 * 1024 * 1024
PNG_MAX_SIDE = 4096
_PNG_MAX_BASE64 = -(-PNG_MAX_BYTES // 3) * 4
_PNG_ONE_OF = f"png must be a {PNG_URI} URI or a relative path ending .png"


def png_size(png: bytes) -> Tuple[int, int]:
    """Width and height from a PNG's IHDR (png_problem has passed)."""
    return struct.unpack(">II", png[16:24])


def png_problem(png: bytes) -> Optional[str]:
    """Why these bytes cannot be a replacement texture, else None: at most 2 MiB, a PNG
    signature, and an IHDR whose sides are 1–4096 (compose resizes to what the game
    takes)."""
    if len(png) > PNG_MAX_BYTES:
        return f"is {len(png):,} bytes; a texture PNG may be 2 MiB at most"
    if not png.startswith(_PNG_SIGNATURE):
        return "is not a PNG (no PNG signature)"
    if len(png) < 24 or png[12:16] != b"IHDR":
        return "is not a PNG (no IHDR header)"
    w, h = png_size(png)
    if not (1 <= w <= PNG_MAX_SIDE and 1 <= h <= PNG_MAX_SIDE):
        return f"is {w}×{h}; each side must be 1–{PNG_MAX_SIDE}"
    return None


def _png_from_uri(value: str) -> Tuple[Optional[bytes], Optional[str]]:
    """(the PNG, None) from a ``data:image/png;base64,`` URI, else (None, why)."""
    if not value.startswith(PNG_URI):
        return None, f"png: a data URI must start {PNG_URI}"
    b64 = value[len(PNG_URI):]
    if len(b64) > _PNG_MAX_BASE64:
        return None, "png is larger than 2 MiB"
    try:
        png = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        return None, "png: the data URI is not valid base64 (standard alphabet, padded)"
    why = png_problem(png)
    return (None, f"png {why}") if why else (png, None)


def _png_value_problem(value) -> Optional[str]:
    if isinstance(value, str) and value.startswith("data:"):
        return _png_from_uri(value)[1]
    if not (isinstance(value, str) and value.lower().endswith(".png")):
        return _PNG_ONE_OF
    path = PureWindowsPath(value)          # its drive and root also catch a POSIX /path
    if path.drive or path.root:
        return f"png {value!r}: a path must be relative to the recipe's folder"
    return None


def _png_data(value, where: str) -> bytes:
    """The PNG a validated entry carries. Compose takes only the data URI: a path is
    read by load_recipe, which knows the recipe's folder."""
    if not (isinstance(value, str) and value.startswith("data:")):
        raise click.ClickException(f"{where}: png must be a {PNG_URI} URI here (a path is read by "
                                   f"load_recipe, relative to the recipe file)")
    png, why = _png_from_uri(value)
    if why:
        raise click.ClickException(f"{where}: {why}")
    return png


def _inline_textures(r: dict, path: Path) -> List[str]:
    """Replace each texture ``png`` given as a path with the file's data URI, in place,
    and return the paths. A path is relative to the recipe's folder and may not leave it,
    so a recipe cannot read a file its folder does not hold."""
    base = Path(path).resolve().parent
    done = []
    for i, row in enumerate(r.get("textures") or []):
        value = row["png"]
        if value.startswith("data:"):
            continue
        where = f"{path}: textures[{i}]"
        f = (base / value).resolve()
        if not f.is_relative_to(base):
            raise click.ClickException(f"{where}: png {value!r} is outside the recipe's folder")
        try:
            png = f.read_bytes()
        except OSError as e:
            raise click.ClickException(f"{where}: cannot read png {value!r} ({e.strerror or e})")
        why = png_problem(png)
        if why:
            raise click.ClickException(f"{where}: {value} {why}")
        row["png"] = PNG_URI + base64.b64encode(png).decode("ascii")
        done.append(value)
    return done


def _json_num(s: str):
    f = float(s)
    # 4.0 is a JSON Schema integer. -0.0 stays a float so an f32 keeps its sign, and so
    # does anything past 2**53, where a float is no exact integer and no field goes.
    return int(f) if f.is_integer() and abs(f) <= 2 ** 53 and not (f == 0 and s.lstrip().startswith("-")) else f


def load_recipe(path: Path, inlined: Optional[List[str]] = None) -> dict:
    """Read and validate a recipe file (schema/ability_recipe.json). A texture ``png``
    given as a path is read here, relative to the recipe's folder, and becomes its data
    URI in the returned recipe, so compose, plan and build only ever see data URIs; the
    paths it read are added to ``inlined`` (dats prepare then stores that self-contained
    recipe rather than a copy of the file)."""
    try:
        r = json.loads(Path(path).read_text(encoding="utf-8"), parse_float=_json_num)
    except json.JSONDecodeError as e:
        raise click.ClickException(f"{path}: not valid JSON ({e})")
    errs = validate_recipe(r)
    if errs:
        shown = "\n  ".join(errs[:12]) + (f"\n  … {len(errs) - 12} more" if len(errs) > 12 else "")
        raise click.ClickException(f"{path} does not match schema/ability_recipe.json:\n  {shown}")
    done = _inline_textures(r, path)
    if inlined is not None:
        inlined.extend(done)
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
    race-bound motion is handled: a job ability or spell is one DAT for every race, so
    asked for one the motion is BAKED from one race's copy (``bake_race``, HumeMale by
    default) into that DAT instead of composed per race.

    EXPERIMENTAL — not verified in game. No retail spell or job ability carries caster
    clips (Blue Magic's ``wz*`` clips animate the monster it shows; the caster plays
    ``ma2?`` from its pool), and the PS2 client's PlayClip (tag 0x05) resolves a wildcard
    ref only from the actor's loaded motions — only an exact ref searches the routine's
    own DAT. The proven route for a unique motion is a weapon skill (per-race DATs)."""
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
    for key, body, attr in (("generators", "edits", "gen_edits"), ("curves", "keys", "curve_edits"),
                            ("textures", "png", "tex_edits")):
        for i, row in enumerate(recipe.get(key) or []):
            if row.get("lane") not in lanes:
                raise click.ClickException(f"{key}[{i}] refers to unknown lane {row.get('lane')!r}")
            where = f"{key}[{i}].edits" if body == "edits" else f"{key}[{i}]"
            value = _png_data(row.get(body), where) if body == "png" else row[body]
            ref = _clean(row["ref"])
            section = (ref, _name_key(row.get("name"))) if body == "png" else ref
            getattr(lanes[row["lane"]], attr)[section] = (where, value)
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


def _dir(name: str, payload: bytes) -> bytes:
    """A 32-byte directory (0x01) section: the 16-byte header naming it, then ``payload``."""
    return _name4(name) + struct.pack("<I", encode_section_meta(32, T_DIR)) + b"\0" * 8 + payload


# Retail's race tag on a weapon-skill body's root. Retail has no Tarutaru female tag: both
# Tarutaru load one file.
_WS_RACE_TAG = {"HumeMale": "hm", "HumeFemale": "hf", "ElvaanMale": "em", "ElvaanFemale": "ef",
                "TaruMale": "tr", "TaruFemale": "tr", "Mithra": "mt", "Galka": "gl"}


def _ws_root(race: str, effect_dir: str) -> str:
    """A weapon-skill body's race root and clip-folder name, retail's ``<tag>_<c>``: ``c``
    is the effect folder's first character, lowercased (``hf_1`` around Tachi: Enpi's
    ``1111``, ``hf_e`` around Tartarus Torpor's ``enst``), ``0`` when that is not a
    letter or digit."""
    c = effect_dir[:1].lower()
    return f"{_WS_RACE_TAG[RACE_NAMES[race_index(race)]]}_{c if c.isascii() and c.isalnum() else '0'}"


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


def _window(ev: dict, cmd: bytes) -> int:
    """How long an event keeps the routine open: its ``dur``, and for a clip played N
    times N of them. ``dur`` is one cycle's window and maxLoops (the event's ``loops``,
    else the command's own at +30) repeats it — the race base's ``ssbk`` waits 122
    ticks on its x2 ``mb2?`` of dur 61, ``sswh`` and ``ssb1`` likewise. A clip that
    loops forever (0) counts one cycle: there is no end to wait for. The client reads
    the count as the low 7 bits of byte +30 (+31's high nibble is the start phase:
    @tr0's 0x2001 plays once), so an explicit ``loops`` is masked the same way."""
    dur = int(ev.get("dur") or 0)
    if _op(ev["op"]) != 0x05:
        return dur
    loops = ev.get("loops")
    if loops is None and len(cmd) >= 31:
        loops = cmd[30]
    return dur * max(1, int(loops or 0) & 0x7F)


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
class _Textures:
    """What compose tracks about the 0x20 textures it carries. A mesh or sprite sheet
    binds its texture by the texture's 16-character name and the first match in the file
    wins, so one name must stand for one texture (see _settle_texture_names). Names are
    compared as texture_key: trailing spaces and NULs dropped."""
    encoded: Dict[str, bytes] = field(default_factory=dict)                    # a PNG's sha256 -> its DXT3 section
    namespaces: Set[bytes] = field(default_factory=set)                        # in the lanes' DATs, or given out
    owner: Dict[bytes, Tuple[str, bytes]] = field(default_factory=dict)        # name -> (lane, section past its id)
    names: Dict[Tuple[str, bytes], bytes] = field(default_factory=dict)        # (lane, source name) -> its 16 bytes
    tokens: Dict[Tuple[str, bytes], bytes] = field(default_factory=dict)       # (lane, source namespace) -> renamed
    # A texture is (lane, its id in the lane's DAT, its source name as texture_key): one
    # DAT can give several textures one id, never one name.
    carried: Dict[Tuple[str, str, bytes], bytes] = field(default_factory=dict)     # -> the section it carries
    users: Dict[Tuple[str, str, bytes], List[str]] = field(default_factory=dict)   # -> generators drawing it
    extra: Dict[bytes, str] = field(default_factory=dict)      # a texture past its id (_bound_textures) -> its new id


@dataclass
class Bag:
    """Sections gathered for the output, keyed by (name, type). ``origin`` remembers which
    lane each came from so collisions can be renamed per source."""
    items: Dict[Tuple[str, int], bytes] = field(default_factory=dict)
    origin: Dict[Tuple[str, int], str] = field(default_factory=dict)
    order: List[Tuple[str, int]] = field(default_factory=list)
    renames: Dict[Tuple[str, str], str] = field(default_factory=dict)   # (lane, old) -> new
    reserved: Dict[str, Set[str]] = field(default_factory=dict)         # lane -> names in its DAT
    private: Set[Tuple[str, int]] = field(default_factory=set)          # added with own: patched later, never shared
    borrowed: Dict[str, Set[Tuple[str, int]]] = field(default_factory=dict)   # lane -> another lane's it shares
    tex: _Textures = field(default_factory=_Textures)

    def names_in_use(self) -> Set[str]:
        return {n for n, _ in self.items}

    def clashes(self, lane: str, name: str, tc: int, data: bytes) -> bool:
        """Whether this lane's section cannot go by ``name``: the lane has renamed it
        already, or another lane holds different bytes under it."""
        key = (name, tc)
        return (lane, name) in self.renames or (
            key in self.items and self.origin[key] != lane and self.items[key] != data)

    def add(self, lane: str, name: str, tc: int, data: bytes, own: bool = False) -> str:
        """Put a section in and return the name it goes by. Identical bytes another
        lane brought are shared unless ``own``: a section whose references get
        renamed for this lane (see _apply_renames) has to be this lane's copy. Nor is
        one added with ``own`` shared later: its bytes match now, but the renames are
        patched into it at the end, so another lane would play them."""
        key = (name, tc)
        new = self.renames.get((lane, name))
        if new is None and key in self.items:
            if self.origin[key] == lane:
                return name
            if self.items[key] == data and not own and key not in self.private:
                self.borrowed.setdefault(lane, set()).add(key)
                return name
            new = self._rename(lane, name)
        if new is not None:
            # A rename is the lane's for every type under that name: an audio generator
            # and its 0x3D sound pointer share one, as do a texture and its sprite
            # sheet, and the lane's references are patched to the new name as a whole.
            data = _name4(new) + data[4:]
            key, name = (new, tc), new
            if key in self.items:
                return name
        self.items[key] = data
        self.origin[key] = lane
        self.order.append(key)
        if own:
            self.private.add(key)
        return name

    def _rename(self, lane: str, name: str) -> str:
        # Not a name the lane's own DAT uses either: the lane may bring that section
        # later, and would take the renamed one for it. Nor one already given to a rename
        # or to a texture past the first of its id whose sections are not in yet
        # (_gather_generator settles names and ids for all its deps, then adds them).
        taken = (self.names_in_use() | self.reserved.get(lane, set()) | set(self.renames.values())
                 | set(self.tex.extra.values()))
        new = self.renames[(lane, name)] = _fresh_name(name, taken)
        for key in [k for k in self.order if k[0] == name and self.origin[k] == lane]:
            moved = (new, key[1])
            self.items[moved] = _name4(new) + self.items.pop(key)[4:]
            self.origin[moved] = self.origin.pop(key)
            self.order[self.order.index(key)] = moved
            if key in self.private:
                self.private.discard(key)
                self.private.add(moved)
        # A section of that name the lane took as another lane's identical copy is renamed
        # with the rest: the lane's generators are patched to the new name, so without its
        # own copy there the reference dangles (lane b shares a's `kir1` mesh, then b's
        # `kir1` texture differs and the name becomes `kir0` for b).
        for key in [k for k in self.order if k[0] == name and k in self.borrowed.get(lane, ())]:
            self.borrowed[lane].discard(key)
            moved = (new, key[1])
            if moved not in self.items:
                self.items[moved] = _name4(new) + self.items[key][4:]
                self.origin[moved] = lane
                self.order.append(moved)
        return new


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
    # The recipe's edits go on before the section joins the bag: the bag shares
    # identical bytes between lanes, so a second lane on this spec keeps its own copy
    # exactly when its edits differ. The walk below reads the edited body, so an id
    # re-pointed at another texture or mesh brings that resource and what it names.
    if gen in lane.gen_edits:
        where, edits = lane.gen_edits[gen]
        ids = {_clean(ds.name): bytes(m.data[ds.start:ds.start + 4])
               for ds in m.sections if ds.type_code in _DEP_TYPES}
        body = _edited(lane, gen, apply_edits, body, edits, where, ids)
    deps = []
    seen = set()
    for cc in _effect_deps(m.data, m.sections, s, body):
        dep = cc.rstrip(b"\0 ").decode("latin1")
        for ds in m.sections:
            if _clean(ds.name) != dep or ds.type_code not in _DEP_TYPES or (dep, ds.type_code) in seen:
                continue
            # The first section of a name and type is the one carried (and the one the
            # viewer edits). A later same-named copy (ROM/10/19 has two p1ca curves, of
            # 10 and 9 keys) is never carried, so it takes no edit and no part in clashes.
            # A texture past the first of its id comes along only by name, below.
            seen.add((dep, ds.type_code))
            data = m.data[ds.start:ds.start + ds.size]
            if ds.type_code == T_CURVE and dep in lane.curve_edits:
                where, keys = lane.curve_edits[dep]
                data = _edited(lane, dep, apply_curve, data, keys, where)
            if ds.type_code == T_TEX:
                data = _texture_edited(bag, lane, dep, data, first=True)
            deps.append((dep, ds.type_code, data))
    by_id = len(deps)
    deps += _bound_textures(bag, lane, deps)
    # Which texture of the lane's DAT each is, by its source id and name: a rename
    # below changes the name, and a texture past its id goes out under a new id.
    sources = [(dep, texture_key(_texture_name(data))) if tc == T_TEX else None for dep, tc, data in deps]
    deps = _settle_texture_names(bag, lane, deps, by_id)
    for i in range(by_id, len(deps)):
        dep, tc, data, mine = deps[i]
        new_id = _extra_id(bag, dep, data)
        deps[i] = (new_id, tc, _name4(new_id) + data[4:], mine)
    drawn = _drawn_textures(body, deps)
    # A generator names its resources by id. When one of them has to be renamed for
    # this lane, the generator is patched to the new name, so it cannot be the copy
    # another lane shares. (It names no texture past the first of its id.)
    own = any(bag.clashes(lane.name, dep, tc, data) for dep, tc, data, _ in deps[:by_id])
    new = bag.add(lane.name, gen, T_GEN, body, own=own)
    for (dep, tc, data, mine), src in zip(deps, sources):
        bag.add(lane.name, dep, tc, data, own=mine)
        if tc == T_TEX:
            bag.tex.carried.setdefault((lane.name, *src), data)
            users = bag.tex.users.setdefault((lane.name, *src), [])
            if dep in drawn and gen not in users:
                users.append(gen)
    return new


# ── Textures ─────────────────────────────────────────────────────────────────────

_TEX_MAX = 256          # every retail effect texture is a power of two, 256 at most


def texture_size(w: int, h: int) -> Tuple[int, int]:
    """The size a replacement is encoded at: each side the nearest power of two from 4
    (a DXT block) to 256, the smaller on a tie. Sides move independently — particle UVs
    are normalised, so a mesh draws any size the same way — but a sprite-sheet atlas has
    to keep its cells in proportion."""
    sides = [1 << n for n in range(2, _TEX_MAX.bit_length())]
    return tuple(min(sides, key=lambda s: (abs(s - v), s)) for v in (w, h))


def _encode_texture(png: bytes, where: str) -> bytes:
    """A 0x20 DXT3 section of the PNG (id and name blank; _replaced_texture fills them)
    through the importers' encoder: texconv, alpha halved so the PNG's 255 stores as
    FFXI's 0x80 opaque. Temporary files go to a temp folder, never the repo."""
    from PIL import Image
    from xi import xi_config
    from xi.entity.mesh.xi_import import encode_png_to_texture_section
    from xi.utils.xi_core import find_texconv
    try:
        find_texconv(xi_config.TEXCONV_PATH)
    except FileNotFoundError:
        raise click.ClickException(
            f"{where}: replacing a texture needs texconv (DirectXTex) to encode the PNG as DXT3; "
            f"put texconv.exe at {xi_config.TEXCONV_PATH} or set TEXCONV_PATH to it")
    try:
        img = Image.open(io.BytesIO(png))
        img.load()
    except (OSError, ValueError) as e:
        raise click.ClickException(f"{where}: png cannot be read ({e})")
    img = img.convert("RGBA")
    size = texture_size(*img.size)
    if size != img.size:
        img = img.resize(size, Image.LANCZOS)
    with tempfile.TemporaryDirectory(prefix="xi-texture-") as tmp:
        src = Path(tmp) / "texture.png"
        img.save(src, "PNG")
        try:
            return encode_png_to_texture_section("", "", src, Path(tmp), force_format="DXT3",
                                                 max_size=_TEX_MAX, alpha_scale=0.5, raise_errors=True)
        except (FileNotFoundError, RuntimeError, ValueError) as e:
            # texconv's own reason: its stderr, or why the exe at TEXCONV_PATH would not start
            raise click.ClickException(f"{where}: texconv could not encode the PNG as DXT3: {e}")


def _texture_name(sec: bytes, start: int = 0) -> bytes:
    """The 16-character name of the 0x20 section at ``start``, as stored."""
    return bytes(sec[start + TEXTURE_NAME_AT:start + TEXTURE_NAME_AT + 16])


def _texture_edit(lane: Lane, ref: str, key: bytes, first: bool) -> Optional[Tuple[str, bytes]]:
    """The recipe's replacement for the lane's 0x20 section ``ref`` named ``key``: the
    entry giving that id and name, else, for the first section of that id, the entry
    giving the id alone (a recipe from before names, or a DAT that never repeats an id)."""
    edit = lane.tex_edits.get((ref, key))
    if edit is None and first:
        edit = lane.tex_edits.get((ref, None))
    return edit


def _texture_edited(bag: Bag, lane: Lane, ref: str, data: bytes, first: bool) -> bytes:
    edit = _texture_edit(lane, ref, texture_key(_texture_name(data)), first)
    return data if edit is None else _replaced_texture(bag.tex, edit, data)


def _bound_textures(bag: Bag, lane: Lane, deps: List[Tuple[str, int, bytes]]) -> List[Tuple[str, int, bytes]]:
    """The textures a carried mesh, sprite sheet or zone mesh binds by name that are not
    the first 0x20 of their id in the lane's DAT, each once, with the recipe's replacement
    in. Retail gives one id to several textures and names them apart (ROM/11/21's five
    `faid`, "faida01 faida01" to "faida01 faida05"); the id walk carries the first, which
    is what a generator, ring or specular op naming the id means. The output needs one id
    per section, so each of these goes out under a new one (_extra_id). Nothing names them
    by id, so no reference is patched and they take no rename (_settle_texture_names)."""
    m = lane.model
    first: Dict[str, int] = {}
    by_key: Dict[bytes, object] = {}
    for s in m.sections:
        if s.type_code == T_TEX:
            first.setdefault(_clean(s.name), s.start)
            by_key.setdefault(texture_key(_texture_name(m.data, s.start)), s)    # first match wins
    by_key.pop(b"", None)
    out, seen = [], set()
    for dep, tc, data in deps:
        if tc not in _BINDING_TYPES:
            continue
        for off in texture_name_fields(data, by_key):
            s = by_key.get(texture_key(data[off:off + 16]))
            if s is None or first[_clean(s.name)] == s.start or s.start in seen:
                continue
            seen.add(s.start)
            ref = _clean(s.name)
            out.append((ref, T_TEX, _texture_edited(bag, lane, ref, m.data[s.start:s.start + s.size], first=False)))
    return out


def _extra_id(bag: Bag, ref: str, data: bytes) -> str:
    """The id a texture past the first of its id goes out under: a new one no lane's DAT,
    the output or a rename uses, kept for every generator that brings the same bytes, so
    two lanes that carry it unchanged share one copy as they share any other section."""
    tex = bag.tex
    new = tex.extra.get(data[4:])
    if new is None:
        taken = (bag.names_in_use() | set().union(*bag.reserved.values()) | set(bag.renames.values())
                 | set(tex.extra.values()))
        new = tex.extra[data[4:]] = _fresh_name(ref, taken)
    return new


def _replaced_texture(tex: _Textures, edit: Tuple[str, bytes], src: bytes) -> bytes:
    """The lane's texture section with the recipe's PNG in it: the source section's id
    bytes, 16-character name and flag bits around the encoded pixels. Each distinct PNG
    is encoded once per compose, whatever the race or lane."""
    where, png = edit
    digest = hashlib.sha256(png).hexdigest()
    if digest not in tex.encoded:
        tex.encoded[digest] = _encode_texture(png, where)
    out = bytearray(tex.encoded[digest])
    out[0:4] = src[0:4]
    info = struct.unpack_from("<I", src, 4)[0]
    struct.pack_into("<I", out, 4, encode_section_meta(len(out), T_TEX, info))
    out[TEXTURE_NAME_AT:TEXTURE_NAME_AT + 16] = src[TEXTURE_NAME_AT:TEXTURE_NAME_AT + 16]
    return bytes(out)


def _fresh_namespace(ns: bytes, taken: Set[bytes]) -> bytes:
    """An 8-byte namespace no texture uses: the old one's start, ``_`` and a suffix
    (``carel3  `` -> ``carel3_1``), padded the way the old one is."""
    pad = b"\0" if ns.endswith(b"\0") else b" "
    stem = texture_key(ns)
    for width in (1, 2):
        for suffix in itertools.product(b"123456789abcdefghijklmnopqrstuvwxyz", repeat=width):
            cand = (stem[:7 - width] + b"_" + bytes(suffix)).ljust(8, pad)
            if texture_key(cand) not in taken:
                return cand
    raise click.ClickException(f"cannot find a free texture namespace for {ns!r}")


def _id_used_elsewhere(bag: Bag, lane: str, name: str) -> bool:
    return (any(name in names for other, names in bag.reserved.items() if other != lane)
            or any(k[0] == name and bag.origin[k] != lane for k in bag.items))


def _settle_texture_names(bag: Bag, lane: Lane, deps: List[Tuple[str, int, bytes]], by_id: Optional[int] = None
                          ) -> List[Tuple[str, int, bytes, bool]]:
    """One name, one texture. The client binds a mesh's texture by the 16-character
    name, first match wins, so two different textures under one name draw as one — a
    replaced texture and another lane's original on the same spec, or two sources that
    happen to share a name. The first to reach the output keeps the name; a later lane's
    different texture gets a fresh namespace (localName and padding kept) and that lane's
    meshes and sprite sheets naming it are patched to match.

    Returns the deps with an ``own`` flag: a renamed texture or patched mesh is this
    lane's alone, never shared, and takes a fresh 4-byte id (registered with the bag
    before anything is added, so this lane's generators are re-pointed to it) wherever
    another lane has a section by its id. The deps from ``by_id`` on are textures past
    the first of their id (_bound_textures), which get a new id of their own anyway."""
    by_id = len(deps) if by_id is None else by_id
    tex = bag.tex
    for dep, tc, data in deps:
        if tc != T_TEX:
            continue
        raw = data[TEXTURE_NAME_AT:TEXTURE_NAME_AT + 16]
        key = texture_key(raw)
        if not key or (lane.name, key) in tex.names:
            continue
        held = tex.owner.get(key)
        new = raw
        if held is not None and held[0] != lane.name and held[1] != data[4:]:
            ns = raw[:8]
            if (lane.name, ns) not in tex.tokens:
                tex.tokens[(lane.name, ns)] = _fresh_namespace(ns, tex.namespaces)
                tex.namespaces.add(texture_key(tex.tokens[(lane.name, ns)]))
            new = tex.tokens[(lane.name, ns)] + raw[8:]
        tex.names[(lane.name, key)] = new
        tex.owner.setdefault(texture_key(new), (lane.name, data[4:TEXTURE_NAME_AT] + new + data[TEXTURE_NAME_AT + 16:]))
    moved = {key: new for (ln, key), new in tex.names.items() if ln == lane.name and texture_key(new) != key}
    out = []
    for i, (dep, tc, data) in enumerate(deps):
        mine = False
        if moved and tc == T_TEX and texture_key(data[TEXTURE_NAME_AT:TEXTURE_NAME_AT + 16]) in moved:
            new = moved[texture_key(data[TEXTURE_NAME_AT:TEXTURE_NAME_AT + 16])]
            data, mine = data[:TEXTURE_NAME_AT] + new + data[TEXTURE_NAME_AT + 16:], True
        elif moved and tc in _BINDING_TYPES:
            patched = bytearray(data)
            for off in texture_name_fields(data, moved):
                new = moved.get(texture_key(data[off:off + 16]))
                if new is not None:
                    patched[off:off + 8] = new[:8]     # the namespace; the mesh's own localName bytes stay
            mine = patched != data
            data = bytes(patched)
        if mine and i < by_id and (lane.name, dep) not in bag.renames and _id_used_elsewhere(bag, lane.name, dep):
            bag._rename(lane.name, dep)
        out.append((dep, tc, data, mine))
    return out


# sec 2 op 0x01 (StandardParticleSetup) names what a particle is: its id at +0x0C, its
# linkedType at +0x21. These types name a mesh or sheet, which binds the texture by its
# 16-char name. A static-mesh link names a 0x1F, or a 0x2E zone mesh (ROM/308/122 bol0).
_LINKED_BINDS = {0x0B: (T_PMESH, 0x2E), 0x0E: (T_SPRITE,), 0x39: (T_SPRITE,)}
_RING = 0x24                  # a ring names its 0x20 by id, as the specular op 0x55 does at +0x10


def _drawn_textures(body: bytes, deps) -> Set[str]:
    """The carried textures the generator draws: the one its linked mesh or sprite sheet
    binds by name, or the 0x20 a ring link or a specular op names by id. The rest of what
    it carries (the id scan's extras, such as Fire's 0x20 `fai0` beside the `fai0` sheet
    that draws `fai2`) draws nothing for it."""
    tex = {dep: data for dep, tc, data, _ in deps if tc == T_TEX}
    by_key: Dict[bytes, List[str]] = {}
    for dep, data in tex.items():
        by_key.setdefault(texture_key(data[TEXTURE_NAME_AT:TEXTURE_NAME_AT + 16]), []).append(dep)
    try:
        streams = [walk_stream(body, n) for n in (1, 2, 3, 4)]
    except EditError:
        return set(tex)
    drawn: Set[str] = set()
    setup = next((pos for op, pos, size in streams[1] if op == 0x01 and size > 0x21), None)
    if setup is not None:
        lid, kind = _clean(body[setup + 0xC:setup + 0x10].decode("latin1")), body[setup + 0x21]
        if kind == _RING and lid in tex:
            drawn.add(lid)
        for dep, tc, data, _ in deps:
            if dep == lid and tc in _LINKED_BINDS.get(kind, ()):
                for off in texture_name_fields(data, by_key):
                    drawn.update(by_key.get(texture_key(data[off:off + 16]), ()))
    for ops in streams:
        for op, pos, size in ops:
            if op == 0x55 and size >= 0x14:
                ref = _clean(body[pos + 0x10:pos + 0x14].decode("latin1"))
                if ref in tex:
                    drawn.add(ref)
    return drawn


def _edited(lane: Lane, name: str, write, *args) -> bytes:
    try:
        return write(*args)
    except EditError as e:
        raise click.ClickException(f"{e} (lane {lane.name!r}: {name} in {lane.target.rel})")


def _gather_for_command(bag: Bag, lane: Lane, op: int, ref: Optional[str],
                        carried_routines: Set[Tuple[str, str]]) -> Optional[str]:
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
    if op in LINK_OPS and ref in m.routines and (lane.name, ref) not in carried_routines:
        # Carried per lane: a second lane that links a routine of the same name gathers
        # its own generators (with its own edits) rather than riding on the first's.
        carried_routines.add((lane.name, ref))
        s = m.routines[ref]
        from xi.event.xi_event import _routine_sec2_commands
        own = False
        for c in _routine_sec2_commands(m.data, ref):
            if c["op"] in REF_OPS and c["ref"]:
                _gather_for_command(bag, lane, c["op"], c["ref"], carried_routines)
                own = own or (lane.name, c["ref"]) in bag.renames
        bag.add(lane.name, ref, T_ROUTINE, m.data[s.start:s.start + s.size], own=own)
        return ref
    return ref


def _lane_ref(bag: Bag, carried: Set[Tuple[str, str]], lane: Optional[str], op: int,
              ref: Optional[str]) -> Optional[str]:
    """The name a lane's command carries for ``ref`` once collisions are settled: the lane's
    rename of that name. A command naming a routine takes it only for a routine carried
    from the lane; any other (a shared ``mdam``, ``proc`` or ``eis1``, an actor's ``sh??``)
    is found by the client in ROM/0/0 or on the actor and keeps its name, even where the
    lane renamed a section of that name. A command of no lane is never renamed."""
    if lane is None or not ref:
        return ref
    new = bag.renames.get((lane, ref))
    if new is None or (op in _ROUTINE_REF_OPS and (lane, ref) not in carried):
        return ref
    return new


def _routine_commands(sec: bytes):
    """``(offset, op, size)`` of each command in a routine section's sec2, the offsets from
    the section's start (its sec2 offset is the second of the four at +0x20)."""
    if len(sec) < 0x30:
        return
    p = struct.unpack_from("<I", sec, 0x24)[0]
    for _ in range(1024):
        if p < 0 or p + 8 > len(sec):
            return
        op, size = sec[p], max(1, sec[p + 1] & 0x1F) * 4
        yield p, op, size
        if op == 0x00:
            return
        p += size


def _foreign_refs(sec: bytes, lane: str, carried: Set[Tuple[str, str]]) -> Set[int]:
    """Where a carried routine names a routine that did not come from its lane (a shared
    or actor one): the offsets of those +8 names, which _apply_renames leaves alone."""
    out = set()
    for p, op, size in _routine_commands(sec):
        if op in _ROUTINE_REF_OPS and size >= 12:
            name = sec[p + 8:p + 12].decode("latin1")
            if (lane, name) not in carried and (lane, _clean(name)) not in carried:
                out.add(p + 8)
    return out


def _apply_renames(bag: Bag, carried: Set[Tuple[str, str]] = frozenset()) -> None:
    """Patch every renamed name inside the bodies of sections that came from the same
    lane (generators name their textures/meshes/curves/sounds by 4-char id; routines name
    their generators, clips and linked routines). A routine's link to a routine not carried
    from its lane keeps its name, as _lane_ref keeps a recipe event's."""
    if not bag.renames:
        return
    by_lane: Dict[str, List[Tuple[bytes, bytes]]] = {}
    for (lane, old), new in bag.renames.items():
        # A body names an id the way its DAT spells it, and a DAT pads a short one with
        # spaces or NULs (Cure III's texture and mesh `ho␣␣`): look for both. The new
        # name is written as its section header has it.
        for spelled in dict.fromkeys(old.encode("ascii")[:4].ljust(4, pad) for pad in (b"\0", b" ")):
            by_lane.setdefault(lane, []).append((spelled, _name4(new)))
    for key in list(bag.items):
        lane = bag.origin[key]
        pairs = by_lane.get(lane)
        if not pairs or key[1] not in (T_GEN, T_ROUTINE):
            continue
        body = bytearray(bag.items[key])
        keep = _foreign_refs(bag.items[key], lane, carried) if key[1] == T_ROUTINE else set()
        for old, new in pairs:
            i = body.find(old, 16)
            while i >= 0:
                if i not in keep:
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


def _check_edit_targets(loaded: Dict[str, Lane]) -> None:
    """Every edited generator, curve and texture has to be in its lane's DAT (each race's
    copy, for a race-bound lane). One that nothing the mix fires uses is left alone (it
    is simply not carried), but a name the DAT does not have is a recipe written against
    another source."""
    for lane in loaded.values():
        if lane.missing:
            continue
        for edits, tc, what in ((lane.gen_edits, T_GEN, "generator"), (lane.curve_edits, T_CURVE, "curve")):
            for ref, (where, _) in edits.items():
                if not _sections_named(lane.model, ref, (tc,)):
                    raise click.ClickException(f"{where.removesuffix('.edits')}: lane {lane.name!r} "
                                               f"({lane.target.rel}) has no {what} {ref!r}")
        # A texture entry is one 0x20 section: with a name, the one of that id and name;
        # without, the first of that id. Two entries on one section leave no telling
        # which PNG it takes.
        replaced: Dict[int, str] = {}
        for (ref, key), (where, _) in lane.tex_edits.items():
            secs = _sections_named(lane.model, ref, (T_TEX,))
            if not secs:
                raise click.ClickException(f"{where}: lane {lane.name!r} ({lane.target.rel}) has no texture {ref!r}")
            s = _texture_section(lane, ref, key)
            if s is None:
                names = ", ".join(repr(texture_key(_texture_name(lane.model.data, x.start)).decode("latin1"))
                                  for x in secs)
                raise click.ClickException(f"{where}: lane {lane.name!r} ({lane.target.rel}) has no texture "
                                           f"{ref!r} named {key.decode('latin1')!r}; its {ref!r} textures are {names}")
            other = replaced.setdefault(s.start, where)
            if other != where:
                named = texture_key(_texture_name(lane.model.data, s.start)).decode("latin1")
                raise click.ClickException(f"{where}: lane {lane.name!r} texture {ref!r} named {named!r} is "
                                           f"already replaced by {other} (an entry without a name replaces "
                                           f"the first {ref!r})")


def _texture_format(sec: bytes) -> dict:
    """``{w, h, format}`` of a 0x20 section, labelled as the viewer's section list does."""
    w, h = struct.unpack_from("<II", sec, 0x25)
    if sec[0x10] == 0xA1:
        fmt = {b"1TXD": "DXT1", b"3TXD": "DXT3", b"5TXD": "DXT5"}.get(bytes(sec[0x49:0x4D]), "DXT?")
    else:
        bits = struct.unpack_from("<H", sec, 0x2F)[0]
        fmt = "RGBA32" if bits == 32 else f"palette {bits}bpp"
    return {"w": w, "h": h, "format": fmt}


def _texture_report(bag: Bag, loaded: Dict[str, Lane]) -> List[dict]:
    """The report's ``textures``: every replacement the recipe asks for, then every
    texture renamed to keep one name to one texture. A row is one 0x20 section of the
    lane's DAT: ``ref`` its id there, and its name there ``renamed_from`` when the texture
    was renamed, else ``name``. ``name`` is the 16-character name in the output (what a
    mesh binds, and what the viewer keys a texture by), ``users``
    the generators whose linked mesh, sprite sheet or ring, or specular op, draws it (see
    _drawn_textures), as the output names them. A texture none of them draws is ``unused``
    even when it is carried; a replacement nothing carries has as ``to`` the size it would
    be encoded at."""
    rows = []
    for lane in loaded.values():
        if lane.missing:
            continue
        # (id, source name) of each texture the recipe replaces, and the PNG
        replaced = {}
        for (ref, key), (_, png) in lane.tex_edits.items():
            replaced[(ref, texture_key(_texture_name(_texture_source(lane, ref, key))))] = png
        renamed = [(ref, key) for (ln, ref, key), sec in bag.tex.carried.items()
                   if ln == lane.name and (ref, key) not in replaced
                   and _texture_name(sec) != _texture_name(_texture_source(lane, ref, key))]
        for ref, key in list(replaced) + renamed:
            src = _texture_source(lane, ref, key)
            out = bag.tex.carried.get((lane.name, ref, key))
            name = _texture_name(out or src)
            row = {"lane": lane.name, "ref": ref, "name": name.decode("latin1"), "from": _texture_format(src)}
            if out is not None:
                row["to"] = _texture_format(out)
            else:
                w, h = texture_size(*png_size(replaced[(ref, key)]))
                row["to"] = {"w": w, "h": h, "format": "DXT3"}
            if name != _texture_name(src):
                row["renamed_from"] = _texture_name(src).decode("latin1")
            row["users"] = [bag.renames.get((lane.name, g), g) for g in bag.tex.users.get((lane.name, ref, key), [])]
            if not row["users"]:
                row["unused"] = True
            rows.append(row)
    return rows


def _texture_section(lane: Lane, ref: str, key: Optional[bytes] = None):
    """The lane's 0x20 section ``ref`` named ``key`` (texture_key), the first of that id
    for None; None when the DAT has no such section."""
    secs = _sections_named(lane.model, ref, (T_TEX,))
    if key is None:
        return secs[0] if secs else None
    return next((s for s in secs if texture_key(_texture_name(lane.model.data, s.start)) == key), None)


def _texture_source(lane: Lane, ref: str, key: Optional[bytes] = None) -> bytes:
    s = _texture_section(lane, ref, key)
    return lane.model.data[s.start:s.start + s.size]


# ── Links ────────────────────────────────────────────────────────────────────────

# A race schedule: a routine every PC race's own files carry (the cast start ca??, the
# release sh?? and its motion ss??, the ranged and song lc?? / ls??) or that the client
# runs by name at a cast's end or interrupt (st?? / sp??).
_ACTOR_SCHEDULE_RX = re.compile(r"(sh|ca|ss|st|sp|lc|ls)[0-9a-z]{2}")
# Where the client looks for the routine a command names (PS2 ExecuteTag): 0x03, 0x30,
# 0x3B, 0x5F and 0x73 in the ability's own DAT, then ROM/0/0.DAT, never on an actor; 0x09
# among the target's own routines, then ROM/0/0; 0x57 and 0x85 among the caster's, the
# files attached to it (a race-bound weapon-skill body), then ROM/0/0; 0x3C among the
# caster's own only (never its attached files).
_ON_ACTOR = {0x09: "target", 0x57: "caster", 0x85: "caster", 0x3C: "caster"}
_SYS_ROUTINES: Dict[str, frozenset] = {}


def _sys_routines() -> Optional[frozenset]:
    """The routines of the shared ROM/0/0.DAT, where a link's name is looked for after the
    ability's own DAT (read once); None when there is no install to read it from."""
    try:
        path = resolve_targets("ROM/0/0")[0].path
    except click.ClickException:
        return None
    key = str(path)
    if key not in _SYS_ROUTINES:
        try:
            _SYS_ROUTINES[key] = frozenset(Model.load(path).routines)
        except Exception:  # noqa: BLE001 — an unreadable ROM/0/0 only means nothing is known
            return None
    return _SYS_ROUTINES[key]


def _link_warnings(commands: List[Tuple[int, bytes]], in_dat: Set[str],
                   on_caster: bool = False) -> List[str]:
    """A warning for each command of ``main`` (``(start, bytes)``) that names a routine the
    game will not find where that command looks: not a routine of this DAT (``in_dat``) unless
    the op looks in it, not one of ROM/0/0 unless the op falls back to it, and not a race
    schedule unless the op looks on the actor. ``on_caster``: this DAT is attached to the
    caster, as a race-bound weapon-skill body is, where 0x57 and 0x85 find its routines. The
    command is written all the same; in game the client logs it and goes on. Without an
    install to read ROM/0/0 from, only a routine of this DAT the op never reaches is said."""
    shared, read = None, False
    out = []
    for start, cmd in commands:
        op = cmd[0]
        if op not in _ROUTINE_REF_OPS or len(cmd) < 12:
            continue
        name = _clean(cmd[8:12].decode("latin1"))
        who = _ON_ACTOR.get(op)
        at = f"link {name} (0x{op:02X}) at frame {start}"
        if name in in_dat:
            # 0x09 and 0x3C never reach this DAT; 0x57 / 0x85 reach it only when it is
            # attached to the caster. A race schedule is the actor's own either way.
            if who is None or (on_caster and op in (0x57, 0x85)) or _ACTOR_SCHEDULE_RX.fullmatch(name):
                continue
            then = "" if op == 0x3C else " and then ROM/0/0"
            out.append(f"{at}: 0x{op:02X} looks among the {who}'s own routines{then}, never in this DAT, so "
                       f"this DAT's {name} does not play; link it with 0x03, or 0x3B to wait for it")
            continue
        if not read:
            shared, read = _sys_routines(), True
        if shared is None:
            continue
        if name in shared:
            if op == 0x3C:
                out.append(f"{at}: 0x3C looks only among the caster's own routines, and {name} is ROM/0/0's; "
                           f"link it with 0x03, or 0x3B to wait for it")
        elif _ACTOR_SCHEDULE_RX.fullmatch(name):
            if who is None:
                how = "; retail links it with 0x3C" if op in (0x03, 0x3B) else ""
                out.append(f"{at}: 0x{op:02X} looks in this DAT and ROM/0/0, never on the caster, so the race "
                           f"schedule {name} is not found{how}")
        else:
            unless = f" unless the {who} has a routine of that name" if who else ""
            out.append(f"{at} resolves nowhere: {name} is not a routine of this DAT or ROM/0/0, nor a race "
                       f"schedule; the game skips it{unless}")
    return out


def compose_once(recipe: dict, lanes: Dict[str, Lane], race: Optional[str],
                 encoded: Optional[Dict[str, bytes]] = None) -> Composed:
    """One output DAT. ``encoded`` holds the replacement textures already encoded by an
    earlier race's compose (a PNG's sha256 -> its section), so each is encoded once."""
    loaded = {n: _load_lane(l, race) for n, l in lanes.items()}
    _check_edit_targets(loaded)
    namespaces = {texture_key(m.data[s.start + TEXTURE_NAME_AT:s.start + TEXTURE_NAME_AT + 8])
                  for l in loaded.values() for m in (l.model, l.waist) if m is not None
                  for s in m.sections if s.type_code == T_TEX}
    bag = Bag(reserved={n: {_clean(s.name) for m in (l.model, l.waist) if m is not None for s in m.sections}
                        for n, l in loaded.items()},
              tex=_Textures(encoded={} if encoded is None else encoded, namespaces=namespaces))
    carried: Set[Tuple[str, str]] = set()
    warnings: List[str] = []
    left_out: Dict[str, int] = {}
    bases: Dict[str, Optional[Model]] = {}
    events = sorted(recipe["events"], key=lambda e: (int(e["start"]), e.get("order", 0)))
    stamped: List[Tuple[dict, bytes]] = []
    for ev in events:
        op = _op(ev["op"])
        ref = ev.get("ref")
        if ev.get("from") is None:
            # A command of no lane: its own bytes, in every race's DAT. Nothing is gathered
            # for it and no lane's rename reaches it.
            if not ev.get("raw"):
                raise click.ClickException(f"event 0x{op:02X} at frame {ev['start']} has no lane ('from') "
                                           f"and no 'raw' bytes")
            stamped.append((ev, bytes.fromhex(ev["raw"])))
            continue
        lane = loaded.get(ev["from"])
        if lane is None:
            raise click.ClickException(f"event refers to unknown lane {ev.get('from')!r}")
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
        if ref:
            _gather_for_command(bag, lane, op, ref, carried)
        stamped.append((ev, cmd))
    _apply_renames(bag, carried)

    # A command's delay is the wait AFTER it (see xi_inspect.flatten): each gets the
    # gap to the next start, the first start rides on the start marker, and the last
    # command waits out to totalDelay — the routine's end, which retail sets to the
    # last command's window (its start + dur, every cycle of a clip played N times:
    # see _window). A recipe from `xi ability recipe` carries the source's own total
    # so it round-trips exactly.
    starts = [int(ev["start"]) for ev, _ in stamped]
    end = max([s + _window(ev, cmd) for s, (ev, cmd) in zip(starts, stamped)] + starts + [0])
    total = int(recipe.get("total") or 0)
    # No total in the recipe, or one that ends before the last command starts: the
    # routine ends where its last window does. (A lone clip at frame 0 used to keep
    # total 0 — the routine ended before the clip had played a frame.)
    if not total or total < (starts[-1] if starts else 0):
        total = end
    commands, timeline = [], []
    for i, (ev, cmd) in enumerate(stamped):
        start = starts[i]
        nxt = starts[i + 1] if i + 1 < len(starts) else total
        op = _op(ev["op"])
        ref_out = _lane_ref(bag, carried, ev.get("from"), op, ev.get("ref"))
        commands.append(_stamp(cmd, delay=nxt - start, ev=ev, ref=ref_out))
        timeline.append({"start": start, "op": op, "ref": ref_out, "dur": ev.get("dur"), "from": ev.get("from")})
    # A weapon skill built from a motion that never moves the weapon hand (an emote or
    # dance — see _HIDE_WEP_*) leaves a drawn weapon hanging at rest in game. Stow main+sub
    # for the action the way a spell cast does, so the pose reads as it does in the mixer
    # (which hides them too). WS only (race is not None); the two tags fire at frame 0.
    routine_commands = commands
    if race is not None and any(
            l.slot is not None and l.slot.category in ("emote", "dance") for l in loaded.values()):
        routine_commands = [_HIDE_WEP_MAIN, _HIDE_WEP_SUB, *commands]
    main = build_routine("main", routine_commands, total, lead=starts[0] if starts else 0)
    in_dat = {name for name, tc in bag.items if tc == T_ROUTINE} | {"main"}
    warnings += _link_warnings([(start, c) for start, c in zip(starts, commands)], in_dat,
                               on_caster=race is not None)

    dir_name = recipe.get("dir") or re.sub(r"[^A-Za-z0-9]", "", recipe["name"])[:4].ljust(4, "_")
    ordered = sorted(bag.order, key=lambda k: (_TYPE_ORDER.get(k[1], 99), bag.order.index(k)))
    # The part-2 (waist) clips a lane's +6 sibling brought (an emote's), by their final name
    # after any rename. Retail keeps these in the companion DATs, not the body; the client
    # reads a WS's waist from the companion, so they go there or the mid-body tears.
    waist_names: Set[str] = set()
    for lane in loaded.values():
        if lane.waist is None:
            continue
        for s in lane.waist.sections:
            if s.type_code == T_CLIP:
                orig = _clean(s.name)
                waist_names.add(bag.renames.get((lane.name, orig), orig))
    out = bytearray()
    names: List[str] = []
    companion: Optional[bytes] = None

    def put(keys) -> None:
        for key in keys:
            out.extend(bag.items[key])
            names.append(f"{key[0]}(0x{key[1]:02X})")

    if race is None:
        # A job ability or spell: one effect folder holding everything, as retail's are.
        out += _dir(dir_name, _DIR_DATA)
        put(ordered)
        out += main + _END_SECTION
        names.append("main(0x07)")
    else:
        # One race's weapon-skill body, laid out like retail's (Tachi: Enpi, ROM/101/76): the
        # race root holds the effect folder (everything else, then `main`) and a clip folder
        # of the root's name (clips, then weapon traces), left out when there are none. The
        # client finds `main`, clips and traces by type and name across a loaded file's
        # whole tree, and a routine's own lookups widen from its folder to the root, so the
        # nesting changes no lookup.
        root = _ws_root(race, dir_name)
        # Parts 0/1 and weapon traces stay in the body's clip folder; the waist (part 2)
        # goes to the companion DAT, as retail lays it out (Fast Blade: b*0/b*1 in the body,
        # b*2 in the +256/+512 companions). `main` names clips by wildcard, so the client
        # resolves each part across the body and the companion it loads alongside it.
        body_motion = [k for k in ordered if k[1] in (T_CLIP, T_TRACE) and k[0] not in waist_names]
        waist_motion = [k for k in ordered if k[1] == T_CLIP and k[0] in waist_names]
        out += _dir(root, _DIR_PLAIN) + _dir(dir_name, _DIR_DATA)
        put(k for k in ordered if k[1] not in (T_CLIP, T_TRACE))
        out += main + _END_SECTION
        names.append("main(0x07)")
        if body_motion:
            out += _dir(root, _DIR_PLAIN)
            put(body_motion)
            out += _END_SECTION
        out += _END_SECTION
        if waist_motion:
            comp = bytearray(_dir(root, _DIR_PLAIN))
            for key in waist_motion:
                comp.extend(bag.items[key])
            comp += _END_SECTION
            companion = bytes(comp)
    renames = {f"{lane}:{old}": new for (lane, old), new in bag.renames.items()}
    for name, n in left_out.items():
        warnings.append(f"{loaded[name].missing}; built without its {n} event{'s' if n != 1 else ''}")
    return Composed(race, bytes(out), names, renames, timeline, total, list(dict.fromkeys(warnings)),
                    _texture_report(bag, loaded), companion=companion)


def compose(recipe: dict, race: Optional[str] = None, kind: Optional[str] = None) -> List[Composed]:
    """Compose the recipe: once per race when a lane is race-bound, else once. ``kind``
    (ja / spell) bakes a race-bound motion from one race — ``race``, else HumeMale —
    into that single DAT instead (see _lanes)."""
    lanes = _lanes(recipe, bake_race=race, kind=kind)
    race_bound = any(l.race_bound for l in lanes.values())
    encoded: Dict[str, bytes] = {}
    if race_bound:
        races = [race] if race else list(RACE_NAMES)
        return [compose_once(recipe, lanes, r, encoded) for r in races]
    return [compose_once(recipe, lanes, None, encoded)]


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
                       "warnings": c.warnings, "textures": c.textures})
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

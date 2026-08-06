"""Bake a self-contained "costume" NPC DAT from a race + face + gear + weapons.

The FFXI client renders a player-looking character (a **Type-1 PC**) by assembling,
at runtime, a shared race skeleton + per-slot equipment mesh DATs + weapon-typed
battle-animation DATs (docs/ue5/animation-actors.md §Multi-part models). Many retail
NPCs are the SAME appearance frozen into ONE self-contained **Type-0 entity DAT** —
skeleton (0x29) + every gear mesh (0x2A) + textures (0x20) + locomotion/face
animations (0x2B) + Info (0x45) in a single file. Verified examples:

    ROM/261/56.DAT  Hume-F, unarmed  (skeleton + 8 meshes + wlk/idl/run + mou4/eye3)
    ROM/8/75.DAT    Taru,  armed     (+ a wep block + battle motions mb**/mw**)

This module reproduces that flattening natively — no GLB round-trip, no external
tools. Because every DAT section is a self-contained, 16-byte-aligned chunk whose
internal offsets are section-RELATIVE (see xi.common.xi_section), and directories
are a simple push (0x01) / pop (0x00-End) stack that the client walks sequentially
(xim DatParser.parse), assembly is just concatenating verbatim section bytes inside
synthesized directory framing — the same trick :func:`xi.entity.mesh.xi_import.rebuild_dat`
uses. Everything comes from the game's own data:

  * skeleton + part-0 locomotion + face anims + turn routines → the race-config DAT
    (``race_skeleton_dat(race)``);
  * part-1/2 locomotion → the race's other movement DATs, located via the
    ``FFXiMain.dll`` motion tables (``xi_motion_tables``);
  * face + each worn slot mesh → ``resolve_gear_dat(race, slot, id)`` (model id 0 is
    the race's naked base part, so an empty slot still shows skin);
  * weapon mesh → the user's weapon DAT (its 0x2A rigs to the shared weapon joint).

Scope (v1 — "core"): appearance + locomotion + face anims + the weapon MESH. Battle /
weapon-skill / dual-wield motion blocks (selected by ``weaponAnimationType``) are a
planned second pass; the weapon's anim type is read here and returned so that pass can
use it. See [[custom-npc-feature]] and docs/entity/npc-look.md.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from xi.common.xi_section import encode_section_meta
from xi.xi_config import FFXI_DIR, read_path_for
from xi.entity.mesh.xi_export import parse_sections, Section
from xi.gear.xi_export import resolve_gear_dat, race_skeleton_dat
from xi.gear.xi_core import LOOK_RACE_NAMES

# Section type codes (mirrors xi.entity.mesh.xi_export.SECTION_TYPE_NAMES).
_T_END = 0x00
_T_DIR = 0x01
_T_ROUTINE = 0x07
_T_TEXTURE = 0x20
_T_SKELETON = 0x29
_T_MESH = 0x2A
_T_ANIM = 0x2B
_T_INFO = 0x45

# Race name -> LOOK race id (reverse of LOOK_RACE_NAMES), for callers that hand us a
# (race, gender) pair. Both directions use the same xi race names ('HumeFemale', …).
RACE_ID_BY_NAME = {name: rid for rid, name in LOOK_RACE_NAMES.items()}

# The (race, gender) the wizard collects -> xi race name. Mithra/Galka/Tarutaru are single
# models (no gender prompt): Tarutaru shares one body across its "fake gender" — the male vs
# female look is chosen purely by the face — so it resolves to one canonical race name and its
# face picker (see :func:`pc_face_list`) offers both the male- and female-looking faces.
RACE_GENDERS = {
    "Hume": {"Male": "HumeMale", "Female": "HumeFemale"},
    "Elvaan": {"Male": "ElvaanMale", "Female": "ElvaanFemale"},
    "Tarutaru": {None: "TaruMale"},
    "Mithra": {None: "Mithra"},
    "Galka": {None: "Galka"},
}

# Locomotion clips a costume NPC carries, in the retail 'mot_' order (ROM/261/56).
# Three "parts" (0=lower/base, 1=upper, 2=extra) that split across the race's movement
# DATs; we gather whichever DAT holds each by name, so the exact per-race file layout
# doesn't matter.
LOCOMOTION_CLIPS = ("wlk0", "wlk1", "wlk2", "idl0", "idl1", "idl2", "run0", "run1", "run2")
# Face + turn animation/routine tags, all in the race-config DAT's 'base' block.
FACE_ANIM_CLIPS = ("mou4", "eye3")
TURN_ROUTINES = ("@tr0", "@tl0")

# The five armour slots a costume NPC can wear (face is handled separately; weapons too).
ARMOUR_SLOTS = ("head", "body", "hands", "legs", "feet")

# Weapon families the wizard offers. Stored for record-keeping and as a hint for the
# planned battle-anim pass; the authoritative ``weaponAnimationType`` is read from the
# weapon DAT's Info section at bake time (see :func:`_read_weapon_anim_type`).
WEAPON_TYPES = (
    "Hand-to-Hand", "Dagger", "Sword", "Great Sword", "Axe", "Great Axe", "Scythe",
    "Polearm", "Katana", "Great Katana", "Club", "Staff", "Archery", "Marksmanship",
)

def _computed_faces(race: str, prefix: str = "F") -> List[Tuple[str, str]]:
    """Fallback face list for a race with no curated :data:`PC_FACES` entry: the 16 base gear
    faces resolved to their DATs, labelled sequentially ``<prefix>1A``..``<prefix>8B`` (the same
    scheme the character creator uses). Skips ids that don't resolve on this install."""
    from xi.gear.xi_core import RACE_TABLES, parse_race_table, slot_file_ids
    ids = sorted(m for m, _ in slot_file_ids(parse_race_table(RACE_TABLES[race])["face"]))
    out: List[Tuple[str, str]] = []
    seen: set = set()
    for mid in ids:
        if mid >= 16:
            break
        try:
            dat = resolve_gear_dat(race, "face", mid)
        except (ValueError, FileNotFoundError):
            continue
        rom = _rom_label(dat)
        if rom in seen:
            continue
        seen.add(rom)
        out.append((rom, f"{prefix}{mid // 2 + 1}{'A' if mid % 2 == 0 else 'B'}"))
    return out


def pc_face_list(race: str) -> List[Tuple[str, str]]:
    """``[(rom_rel, label)]`` of selectable faces for a race, for the wizard's face picker.

    Curated races come straight from :data:`PC_FACES` (face codes + NPC-face names transcribed
    from AltanaViewer). Tarutaru has no curated list and one shared body, so its "gender" is the
    face: the list combines the male-looking (``TaruMale`` gear) and female-looking (``TaruFemale``
    gear) faces, labelled ``Male 1A``.. / ``Female 1A``.."""
    from xi.entity.xi_pc_faces import PC_FACES
    if race in PC_FACES:
        return list(PC_FACES[race])
    if race in ("TaruMale", "TaruFemale", "Tarutaru"):
        return (_computed_faces("TaruMale", prefix="Male ")
                + _computed_faces("TaruFemale", prefix="Female "))
    return _computed_faces(race)


@dataclass
class BakeReport:
    """What the bake pulled together — surfaced to the wizard for a confirmation echo."""
    race: str
    root_name: str
    meshes: List[str]
    textures: List[str]
    locomotion: List[str]
    missing_clips: List[str]
    slots: Dict[str, str]           # slot -> source ("ROM/…", "naked base", "—")
    weapon_main: Optional[str]
    weapon_sub: Optional[str]
    weapon_anim_type: Optional[int]
    size: int


# ---------------------------------------------------------------------------
# Section helpers — every section is `[4-byte id][u32 meta][…]`, 16-aligned.
# ---------------------------------------------------------------------------

def _load(path: Path) -> Tuple[bytes, List[Section]]:
    data = read_path_for(Path(path)).read_bytes()
    return data, parse_sections(data)


def _raw(data: bytes, s: Section) -> bytes:
    return data[s.start:s.start + s.size]


def _rename_section(raw: bytes, new_id: str) -> bytes:
    """Return a copy of a verbatim section with its 4-char id (bytes 0:4) replaced.
    Only the id changes — meta, type, size and every section-relative internal offset
    stay put, so the section still parses identically. Ids aren't cross-referenced
    (meshes point at textures by 16-char NAME, anims/routines resolve by tag), so this
    is purely to keep every child id unique within one directory."""
    return new_id.encode("ascii", "replace")[:4].ljust(4, b"\x00") + raw[4:]


def _dir_section(name: str) -> bytes:
    """A 32-byte 0x01 Directory chunk (push). The client reads only its 4-char id;
    the trailing 24 bytes are zero (matches retail 'base'/'mot_'/'mode' headers)."""
    meta = encode_section_meta(0x20, _T_DIR, what="directory")
    return name.encode("ascii", "replace")[:4].ljust(4, b"\x00") + struct.pack("<I", meta) + b"\x00" * 0x18


def _end_section() -> bytes:
    """A 16-byte 0x00 End chunk (pop the current directory)."""
    meta = encode_section_meta(0x10, _T_END, what="end")
    return b"end\x00" + struct.pack("<I", meta) + b"\x00" * 8


def _texture_name(data: bytes, s: Section) -> Optional[str]:
    """The 16-char internal texture name a 0x20 section advertises (what a mesh's
    0x8000 reference binds to), or None if it isn't a recognised texture layout."""
    pos = s.data_start
    if pos >= len(data) or data[pos] not in (0x81, 0x91, 0xA1, 0xB1):
        return None
    return data[pos + 1:pos + 1 + 0x10].decode("latin1", "replace").rstrip(" \x00")


def _read_weapon_anim_type(data: bytes, sections: Sequence[Section]) -> Optional[int]:
    """A weapon DAT's ``weaponAnimationType`` (Info 0x45, byte 3) — the index the
    client adds to a race's battle-anim base to pick the engaged/attack motions.
    0xFF (none) → None. Used by the planned battle-anim pass; read now for provenance."""
    for s in sections:
        if s.type_code == _T_INFO and s.data_start + 4 <= len(data):
            v = data[s.data_start + 3]
            return None if v == 0xFF else v
    return None


# ---------------------------------------------------------------------------
# Animation sourcing (race-config + movement window)
# ---------------------------------------------------------------------------

def _collect_named(data: bytes, sections: Sequence[Section], type_code: int,
                   wanted: Sequence[str]) -> Dict[str, bytes]:
    """{tag: raw section bytes} for the requested tags of one type, first match wins."""
    want = list(wanted)
    out: Dict[str, bytes] = {}
    for s in sections:
        if s.type_code == type_code:
            tag = s.name.rstrip("\x00")
            if tag in want and tag not in out:
                out[tag] = _raw(data, s)
    return out


def _collect_locomotion(race: str) -> Tuple[Dict[str, bytes], List[str]]:
    """Every locomotion clip (wlk/idl/run parts 0/1/2) for a race, gathered from its
    movement DATs (the race-config DAT holds part 0; parts 1/2 live in sibling DATs the
    ``FFXiMain.dll`` movement table points at). Returns ``(tag -> raw bytes, missing)``.
    Falls back to the race-config DAT alone if the DLL tables can't be read."""
    found: Dict[str, bytes] = {}
    try:
        from xi.entity.anim.xi_motion_tables import (
            load_maindll, category_bases, _window_sizes, _FileIdResolver, RACE_NAMES)
        dll = load_maindll()
        bases = category_bases(dll)
        windows = _window_sizes(bases)
        resolver = _FileIdResolver()
        ri = RACE_NAMES.index(race)
        base, window = bases["movement"][ri], windows["movement"]
        for off in range(window):
            if all(c in found for c in LOCOMOTION_CLIPS):
                break
            spec = resolver.rom_spec(base + off)
            if not spec:
                continue
            full = Path(FFXI_DIR) / spec
            if not full.exists():
                continue
            data, sections = _load(full)
            for tag, raw in _collect_named(data, sections, _T_ANIM, LOCOMOTION_CLIPS).items():
                found.setdefault(tag, raw)
    except Exception:
        pass  # fall through to race-config-only below
    if not all(c in found for c in LOCOMOTION_CLIPS):
        data, sections = _load(race_skeleton_dat(race))
        for tag, raw in _collect_named(data, sections, _T_ANIM, LOCOMOTION_CLIPS).items():
            found.setdefault(tag, raw)
    missing = [c for c in LOCOMOTION_CLIPS if c not in found]
    return found, missing


# ---------------------------------------------------------------------------
# Appearance sourcing (skeleton + face/gear/weapon meshes + textures)
# ---------------------------------------------------------------------------

def _resolve_slot_dat(race: str, slot: str, spec) -> Tuple[Optional[Path], str]:
    """A worn slot's mesh DAT + a human label for the report. ``spec`` may be a DAT
    path/str (the user's own gear), an int gear model id, or None (empty slot → the
    race's naked base part, gear model id 0). Returns (path or None, label)."""
    if spec not in (None, ""):
        p = Path(str(spec)).expanduser()
        if not p.is_absolute() and not p.exists():
            alt = Path(FFXI_DIR) / Path(*str(spec).replace("\\", "/").split("/"))
            if alt.exists():
                p = alt
        if p.exists():
            return p.resolve(), _rom_label(p)
        # A bare gear model id rather than a path.
        try:
            gd = resolve_gear_dat(race, slot, int(str(spec)))
            return gd, _rom_label(gd)
        except (ValueError, FileNotFoundError):
            return None, f"not found: {spec}"
    try:
        gd = resolve_gear_dat(race, slot, 0)
        return gd, "naked base"
    except (ValueError, FileNotFoundError):
        return None, "—"


def _rom_label(p: Path) -> str:
    try:
        return "ROM/" + str(p.resolve().relative_to(Path(FFXI_DIR).resolve())).replace("\\", "/").split("ROM/", 1)[-1]
    except ValueError:
        return p.name


def _grab_meshes_and_textures(path: Path) -> Tuple[List[bytes], List[Tuple[str, bytes]]]:
    """All 0x2A mesh sections and 0x20 texture sections of a DAT, as verbatim bytes.
    Textures come back as ``(16-char name, raw)`` so the caller can dedupe by name."""
    data, sections = _load(path)
    meshes = [_raw(data, s) for s in sections if s.type_code == _T_MESH]
    textures: List[Tuple[str, bytes]] = []
    for s in sections:
        if s.type_code == _T_TEXTURE:
            name = _texture_name(data, s)
            if name is not None:
                textures.append((name, _raw(data, s)))
    return meshes, textures


# ---------------------------------------------------------------------------
# Bake
# ---------------------------------------------------------------------------

def race_from_gender(race: str, gender: Optional[str]) -> str:
    """('Hume', 'Female') → 'HumeFemale'; ('Mithra', None) → 'Mithra'. Accepts an
    already-resolved xi race name too."""
    if race in RACE_ID_BY_NAME:
        return race
    genders = RACE_GENDERS.get(race)
    if not genders:
        raise ValueError(f"Unknown race '{race}'. Valid: {', '.join(RACE_GENDERS)}")
    if None in genders:
        return genders[None]
    if gender not in genders:
        raise ValueError(f"{race} needs a gender ({' / '.join(g for g in genders if g)})")
    return genders[gender]


def bake_costume_npc(
    race: str,
    face_id: object = 0,
    slot_dats: Optional[Dict[str, object]] = None,
    main: Optional[Dict[str, object]] = None,
    sub: Optional[Dict[str, object]] = None,
    dual_wield: bool = False,
    root_name: str = "npc0",
) -> Tuple[bytes, BakeReport]:
    """Assemble a self-contained costume-NPC DAT (bytes) + a :class:`BakeReport`.

    ``race`` is a xi race name ('HumeFemale', …). ``face_id`` and each ``slot_dats`` entry
    (keys :data:`ARMOUR_SLOTS`) may be a **ROM/DAT path**, a **gear model id**, or **None**
    (None → the race's naked base part). ``main``/``sub`` are ``{"dat": path}`` for the weapon
    MESH (battle animations are a later pass; the weapon's ``weaponAnimationType`` is still
    read for the report).
    """
    slot_dats = slot_dats or {}
    race_data, race_secs = _load(race_skeleton_dat(race))

    # --- base block: turn routines + face anims (all from the race-config DAT) ---
    routines = _collect_named(race_data, race_secs, _T_ROUTINE, TURN_ROUTINES)
    face_anims = _collect_named(race_data, race_secs, _T_ANIM, FACE_ANIM_CLIPS)

    # --- mot_ block: locomotion clips ---
    locomotion, missing = _collect_locomotion(race)

    # --- mode block: skeleton + textures + meshes ---
    skel_raw = next((_raw(race_data, s) for s in race_secs if s.type_code == _T_SKELETON), None)
    if skel_raw is None:
        raise ValueError(f"race-config DAT for {race} has no skeleton (0x29) section")

    meshes: List[bytes] = []                 # verbatim mesh sections (ids re-stamped unique)
    tex_by_name: Dict[str, bytes] = {}       # 16-char name -> section (dedupe: first wins)
    slot_report: Dict[str, str] = {}
    mesh_names: List[str] = []

    def _add_source(path: Path) -> None:
        ms, ts = _grab_meshes_and_textures(path)
        for name, raw in ts:
            tex_by_name.setdefault(name, raw)
        meshes.extend(ms)

    # Face first (head/hair/face), then each armour slot, then weapon mesh(es).
    face_path, face_label = _resolve_slot_dat(race, "face", face_id)
    slot_report["face"] = face_label
    if face_path is not None:
        _add_source(face_path)
    for slot in ARMOUR_SLOTS:
        path, label = _resolve_slot_dat(race, slot, slot_dats.get(slot))
        slot_report[slot] = label
        if path is not None:
            _add_source(path)

    weapon_main = weapon_sub = None
    weapon_anim_type = None
    for spec, side in ((main, "main"), (sub, "sub")):
        if not spec or not spec.get("dat"):
            continue
        wp = Path(str(spec["dat"])).expanduser()
        if not wp.exists():
            continue
        _add_source(wp)
        wdata, wsecs = _load(wp)
        atype = _read_weapon_anim_type(wdata, wsecs)
        if side == "main":
            weapon_main, weapon_anim_type = _rom_label(wp), atype
        else:
            weapon_sub = _rom_label(wp)

    if not meshes:
        raise ValueError(
            "no meshes resolved — provide at least a face or one gear slot, and check "
            "the race's naked base parts exist in FFXI_DIR.")

    # --- serialise: unique 4-char ids per child (avoid directory-map collisions) ---
    out = bytearray()
    out += _dir_section(root_name)

    out += _dir_section("base")
    for tag in TURN_ROUTINES:
        if tag in routines:
            out += routines[tag]
    for tag in FACE_ANIM_CLIPS:
        if tag in face_anims:
            out += face_anims[tag]
    out += _end_section()

    out += _dir_section("mot_")
    for tag in LOCOMOTION_CLIPS:
        if tag in locomotion:
            out += locomotion[tag]
    out += _end_section()

    out += _dir_section("mode")
    used_ids: set = set()

    def _unique(stem: str) -> str:
        cand, n = stem[:4].ljust(4, "0"), 0
        while cand in used_ids:
            n += 1
            cand = (stem[:3] + str(n))[:4].ljust(4, "0")
        used_ids.add(cand)
        return cand

    for i, (name, raw) in enumerate(tex_by_name.items()):
        sid = _unique(name.strip()[:4] or ("tex%d" % i))
        out += _rename_section(raw, sid)
    out += skel_raw                                   # 0x29 keeps its own id (uniquely typed)
    for i, raw in enumerate(meshes):
        sid = _unique("m%03d" % i)
        renamed = _rename_section(raw, sid)
        meshes[i] = renamed
        mesh_names.append(sid)
        out += renamed
    out += _end_section()

    # --- Info (0x45): copy the race-config's own metadata verbatim ---
    info_raw = next((_raw(race_data, s) for s in race_secs if s.type_code == _T_INFO), None)
    if info_raw is not None:
        out += info_raw
    out += _end_section()                             # close root

    report = BakeReport(
        race=race, root_name=root_name,
        meshes=mesh_names, textures=list(tex_by_name.keys()),
        locomotion=[c for c in LOCOMOTION_CLIPS if c in locomotion],
        missing_clips=missing, slots=slot_report,
        weapon_main=weapon_main, weapon_sub=weapon_sub,
        weapon_anim_type=weapon_anim_type, size=len(out),
    )
    return bytes(out), report

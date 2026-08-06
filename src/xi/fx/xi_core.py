#!/usr/bin/env python3
"""Shared helpers for `xi fx` (visual-effect inspect/edit).

Effects are `0x05` particle/light generators embedded in a DAT (see
docs/misc/effects.md). They're unencrypted; params are located by opcode tag or
fixed header field. The list/dump/delete/set/copy command modules build on these.
"""

import json
import struct
from pathlib import Path
from typing import Optional, Tuple

# Re-exported so command modules import everything from one place.
# NOTE: these are re-exports consumed by fx.xi_list / zone.xi_apply_changes;
# keep the noqa so an "unused import" cleanup doesn't strip them and break the CLI.
from xi.entity.anim.xi_export import parse_sections  # noqa: F401
from xi.entity.mesh.xi_export import resolve_dat_path  # noqa: F401

EFFECT_TYPE = 0x05
MESH_TYPE = 0x2E
_LIBRARY_PATH = Path(__file__).with_name("fx_library.json")

# Parameter locators (opcode tags; payload is +4 from the tag) and header offsets.
# Opcode locators (opcode = low byte of the sub-section's u32 config; payload +4).
# Validated against xim ParticleGeneratorParser: 0x16 ColorSetup (sec2),
# 0x0F ScaleInitializer (sec2), 0x0A GeneratorCull (sec1; first float = maxEmitDistance).
_TAG_COLOR = b"\x16\x02"   # +4: B,G,R,A bytes (sec2 0x16 ColorSetup)
_TAG_SCALE = b"\x0f\x04"   # +4: 3x f32 (x,y,z) (sec2 0x0F ScaleInitializer)
_TAG_CULL = b"\x0a\x04"    # +4: f32 maxEmitDistance, f32 unk, u32 (sec1 0x0A GeneratorCull = draw distance)
_TAG_FLOW = (b"\x02\xe4", b"\x07\x08")  # +4: 3x f32; .Y animates texture/position flow (observed; opcode TBD)
# Header fields — ALL offsets below are SECTION-start relative (callers do
# `s.start + off` / `body[off]` with body sliced from s.start). data_start is
# section+0x10, so attachFlags sits at data-start+0x00 (xim's frame) and the
# emission group at data-start+0x64.. — the same bytes, one frame. The spawn
# interval at section+0x76 is in-game A/B verified (240 -> ~1.3s gaps).
_OFF_EMIT_VARIANCE = 0x74  # u16 emissionVariance
_OFF_INTERVAL = 0x76       # u16 framesPerEmission (spawn interval; engine adds +1)
_OFF_COUNT = 0x78          # u8  particlesPerEmission (particles per spawn)
_OFF_GENFLAGS = 0x79       # u8  genFlags: bit 0x04 continuousSingleton, bit 0x10 autoRun
_AUTORUN_BIT = 0x10        # genFlags bit: effect auto-spawns (vs scheduler-triggered)

_OFF_ATTACH = 0x10         # attachFlags u16 @ section+0x10 (= data-start+0x00, xim offsetFromDataStart 0). Low 4 bits = type.
# attachType values (low 4 bits of attachFlags) — how the effect binds (xim AttachType enum)
ATTACH_TYPES = {0x0: "None", 0x1: "SourceActor", 0x2: "TargetActor",
                0x3: "SourceToTargetBasis", 0x4: "TargetActorSourceFacing",
                0x5: "SourceActorTargetFacing", 0x6: "TargetToSourceBasis",
                0x9: "SourceActorWeapon", 0xA: "ZoneActor0xA", 0xB: "ZoneActor0xB",
                0xC: "ZoneActor0xC", 0xE: "Sun", 0xF: "Moon"}


def _load_library() -> dict:
    try:
        return json.loads(_LIBRARY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"names": {}, "prefixes": {}}


def classify(name: str, mesh: Optional[str] = None, texture: Optional[str] = None,
             lib: Optional[dict] = None) -> Optional[dict]:
    """Classify an effect FourCC. Signals: exact `names`, the placed `mesh`, the
    referenced `texture` (for mesh-less sprite effects like fire), and `prefixes`.
    A *verified* classification beats a tentative one. Returns the entry or None."""
    lib = lib if lib is not None else _load_library()
    name_entry = lib.get("names", {}).get(name)
    if name_entry is None:
        best = None
        for pre, entry in lib.get("prefixes", {}).items():
            if name.startswith(pre) and (best is None or len(pre) > len(best[0])):
                best = (pre, entry)
        name_entry = best[1] if best else None
    mesh_entry = lib.get("meshes", {}).get(mesh) if mesh else None
    tex_entry = lib.get("textures", {}).get(texture) if texture else None
    for cand in (mesh_entry, tex_entry, name_entry):   # a verified content hit wins
        if cand and cand.get("verified"):
            return cand
    return name_entry or mesh_entry or tex_entry       # else prefer name, then content


def _fourcc(data: bytes, start: int) -> str:
    return bytes(data[start:start + 4]).decode("latin1")


def _mesh_fourccs(data: bytes, sections) -> set:
    return {bytes(data[s.start:s.start + 4]) for s in sections if s.type_code == MESH_TYPE}


def _texture_fourccs(data: bytes, sections) -> set:
    return {bytes(data[s.start:s.start + 4]) for s in sections if s.type_code == 0x20}


def _effect_texture(body: bytes, tex_ccs: set) -> Optional[str]:
    """First texture FourCC an effect references (for mesh-less sprite effects)."""
    for off in range(0x10, len(body) - 3):
        cc = body[off:off + 4]
        if cc in tex_ccs:
            return cc.decode("latin1")
    return None


def _effect_target(body: bytes, mesh_ccs: set) -> Tuple[Optional[str], Optional[Tuple[float, float, float]]]:
    """Best-effort: the mesh an effect places + its local position. Effects name a
    mesh by its 4-byte FourCC, followed by a u32 then an xyz float-triple."""
    for off in range(0x10, len(body) - 19):
        cc = body[off:off + 4]
        if cc not in mesh_ccs:
            continue
        try:
            p = struct.unpack("<3f", body[off + 8:off + 20])
        except struct.error:
            continue
        if all(v == v and abs(v) < 1e5 for v in p) and any(abs(v) > 0.01 for v in p):
            return cc.decode("latin1"), (round(p[0], 2), round(p[1], 2), round(p[2], 2))
    return None, None


def _tag_payload(body: bytes, tag: bytes) -> Optional[int]:
    i = body.find(tag, 0x10)
    return i + 4 if i >= 0 else None


def _pos_offset(body: bytes, ccs: set) -> Optional[int]:
    """Offset of the position float-triple — the xyz after the first referenced
    resource (placed mesh, or texture for mesh-less sprite effects like fire).
    Falls back to scanning for an inline mesh name (4 printable ASCII bytes +
    4 null bytes + XYZ) for runtime effects whose mesh is not a DAT section."""
    for off in range(0x10, len(body) - 19):
        if body[off:off + 4] in ccs:
            return off + 8
    # Runtime effects (e.g. fr/fs torch rims/sparks) embed their mesh name
    # inline in the StandardSetup opcode rather than referencing a DAT section.
    # Pattern: 4 printable-ASCII bytes + 4 null bytes + 3 plausible floats.
    for off in range(0x10, len(body) - 19):
        cc = body[off:off + 4]
        if not all(0x20 <= b <= 0x7E for b in cc):
            continue
        if body[off + 4:off + 8] != b'\x00\x00\x00\x00':
            continue
        try:
            x, y, z = struct.unpack("<3f", body[off + 8:off + 20])
            if all(v == v and abs(v) < 1e5 for v in (x, y, z)):
                return off + 8
        except struct.error:
            continue
    return None


def _read_pos_at(body: bytes, ccs: set) -> Optional[Tuple[float, float, float]]:
    """Position xyz after the first referenced resource in ccs (mesh or texture)."""
    o = _pos_offset(body, ccs)
    if o is None:
        return None
    try:
        p = struct.unpack("<3f", body[o:o + 12])
    except struct.error:
        return None
    if all(v == v and abs(v) < 1e5 for v in p) and any(abs(v) > 0.01 for v in p):
        return (round(p[0], 2), round(p[1], 2), round(p[2], 2))
    return None


def _matches(name: str, patterns) -> bool:
    return any(name == p or name.startswith(p) for p in patterns)


def _rom_rel(path: Path) -> str:
    """`.../ROM/1/41.DAT` -> `rom/1/41`, `.../ROM9/2/105.DAT` -> `rom9/2/105`; otherwise the file stem."""
    parts = [p for p in path.parts]
    for i, p in enumerate(parts):
        if p.upper().startswith("ROM") and i + 1 < len(parts):
            return "/".join([p.lower(), *parts[i + 1:]]).rsplit(".", 1)[0].lower()
    return path.stem

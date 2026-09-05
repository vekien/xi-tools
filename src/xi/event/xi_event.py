"""Core parser for FFXI event DATs (evte format)."""

import math
import struct
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Opcode size table — ported verbatim from the client's EventDisassembler.cpp
# (via docs/external_source/dump_event.py, "a faithful standalone port"). This is
# the authoritative sizing (more complete + correct than the old xeno EventDat.cs
# table: it covers 0x00–0xD9 and fixes several variable-opcode widths).
#
# _FIXED_SIZES[opcode] = total instruction length INCLUDING the opcode byte, or 0
# for a variable-length opcode (use _variable_opcode_size) or an undecodable one
# (0xCA/0xCB N/A, anything ≥ 0xDA) — a 0 result means "stop, can't size this".
# ---------------------------------------------------------------------------

_FIXED_SIZES = [
    1, 3, 8, 5, 3, 3, 3, 5, 5, 5, 5, 3, 3, 5, 5, 5,
    5, 5, 3, 5, 5, 5, 7, 7, 7, 5, 3, 1, 3, 3, 5, 0,
    2, 1, 2, 1, 7, 1, 1, 7, 7, 7, 6, 7, 13, 13, 1, 6,
    1, 0, 3, 2, 3, 3, 7, 9, 3, 3, 7, 11, 7, 7, 7, 7,
    9, 9, 1, 2, 5, 17, 0, 0, 3, 7, 9, 7, 1, 1, 6, 3,
    13, 13, 15, 13, 13, 15, 5, 3, 1, 0, 0, 0, 0, 5, 5, 0,
    0, 2, 17, 3, 11, 11, 0, 5, 1, 4, 7, 9, 9, 7, 7, 1,
    1, 0, 0, 11, 2, 0, 5, 5, 1, 0, 0, 5, 6, 3, 0, 1,
    5, 6, 7, 3, 1, 1, 6, 2, 2, 3, 1, 25, 0, 5, 1, 1,
    1, 3, 6, 3, 6, 3, 1, 5, 1, 5, 1, 1, 3, 0, 2, 17,
    15, 15, 15, 15, 2, 2, 0, 0, 6, 3, 17, 0, 0, 12, 0, 8,
    12, 4, 0, 0, 0, 4, 0, 0, 27, 8, 13, 17, 15, 15, 3, 0,
    3, 5, 0, 7, 12, 17, 15, 15, 7, 1, 0, 0, 0, 17, 15, 15,
    17, 15, 15, 6, 0, 17, 15, 15, 0, 2,
]

# {opcode: (fallback_size, {subcode: size})} for the table-driven variable opcodes.
_SUB_TABLES = {
    0x59: (0, {0: 4, 1: 8, 2: 4, 3: 8, 4: 8, 5: 7, 6: 6, 7: 4, 8: 8}),
    0x8C: (0, {0: 8, 1: 2, 2: 12, 3: 10, 4: 10, 5: 14}),
    0x9D: (8, {0: 8, 1: 8, 2: 6, 3: 8, 4: 8, 5: 8, 6: 8, 7: 6, 8: 23,
               9: 9, 0xA: 10, 0xB: 10, 0xC: 8, 0xD: 10, 0xE: 10, 0xF: 10, 0x10: 10}),
    0xAC: (0, {0: 4, 1: 4, 2: 6, 3: 6, 4: 8}),
    0xAE: (6, {0: 6, 1: 8, 2: 8, 3: 8, 4: 8, 5: 10, 6: 6, 7: 10, 8: 10}),
    0x71: (0, {0: 2, 1: 2, 2: 2, 3: 4, 0x10: 4, 0x11: 4, 0x13: 4, 0x12: 6,
               0x20: 16, 0x21: 2, 0x30: 4, 0x31: 4, 0x32: 6, 0x40: 4, 0x41: 8}),
    0x5F: (0, {0: 2, 1: 2, 2: 6, 3: 16, 4: 16, 5: 18, 6: 18, 7: 14}),
    0x7A: (0, {0: 6, 1: 7, 2: 6, 3: 2, 4: 8, 5: 6}),
    0x7E: (6, {0: 6, 1: 6, 2: 6, 3: 16, 4: 6, 5: 6, 6: 18, 7: 8, 8: 6}),
    0xB3: (2, {0: 4, 1: 14, 2: 2, 3: 4, 4: 4, 5: 18, 6: 4, 7: 4, 8: 2, 9: 4}),
    0xB4: (0, {0: 20, 1: 6, 2: 6, 3: 2, 4: 6, 5: 3, 6: 3, 7: 4, 8: 2, 9: 4,
               0xA: 4, 0xB: 2, 0xC: 4, 0xD: 2, 0xE: 2, 0xF: 6, 0x10: 6, 0x11: 6,
               0x12: 6, 0x13: 20, 0x14: 12, 0x15: 2}),
    0xB6: (4, {0: 4, 1: 4, 2: 4, 3: 4, 4: 4, 5: 4, 6: 4, 7: 4, 8: 4, 9: 4,
               0xA: 4, 0xB: 20, 0xC: 4, 0xD: 14, 0xE: 16, 0xF: 4, 0x10: 2, 0x11: 4,
               0x12: 2, 0x13: 2, 0x14: 6, 0x15: 6}),
    0xB7: (0, {0: 10, 1: 8, 2: 8, 3: 8, 4: 8}),
    0xCC: (4, {0: 10, 1: 10, 2: 14, 3: 10, 0x10: 6, 0x11: 4, 0x20: 4}),
    0xD4: (2, {0: 8, 1: 8, 2: 8, 3: 6, 4: 12, 5: 12}),   # sub 0/2 run the 0x24 helper: op+sub+3 selectors (Isakoth page window @0x1bb6)
    0xD8: (6, {0: 6, 1: 8, 2: 8, 3: 8, 4: 12}),
}

# Opcode names (human-readable labels for the most important ones)
_OPCODE_NAMES = {
    0x00: "noop",       0x01: "set_exec",    0x02: "if",
    0x03: "get_store",  0x05: "set_one",     0x06: "set_zero",
    0x07: "add",        0x08: "sub",         0x09: "set_bit",
    0x0A: "clr_bit",    0x0B: "inc",         0x0C: "dec",
    0x1A: "jump",       0x1B: "break_jump",
    0x1C: "wait_time",  0x1D: "print_msg",   0x1E: "look_talk",
    0x1F: "set_pos",    0x20: "lock_player", 0x21: "end",
    0x23: "wait_dismiss",
    0x24: "dialog_menu",0x25: "wait_select",
    0x2B: "print_msg2", 0x2C: "load_task",   0x2D: "zone_task",
    0x2E: "cancel_clr", 0x2F: "render_flags",
    0x31: "set_pos2",   0x34: "load_zone",   0x35: "load_zone2",
    0x36: "set_pos3",   0x37: "set_pos4",
    0x38: "event_mode", 0x39: "set_dir",
    0x3A: "yaw_float",  0x3B: "get_pos",
    0x3E: "bit_branch",
    0x40: "menu_flag",  0x41: "menu_flag2",
    0x42: "cancel_set", 0x43: "notify_server",
    0x44: "entity_valid_branch",
    0x45: "start_task", 0x46: "camera",      0x47: "update_pos_sv",
    0x48: "print_msg3", 0x49: "print_msg4",
    0x4A: "look_at",    0x4B: "set_yaw",
    0x4C: "open_door",  0x4D: "close_door",
    0x50: "end_task",   0x51: "end_zone_task", 0x52: "end_task2",
    0x53: "wait_task",  0x54: "wait_zone_task", 0x55: "wait_main_task",
    0x57: "frame_delay",0x58: "yield",
    0x5B: "sched_ext",  0x5C: "music",        0x5D: "music_vol",
    0x5E: "stop_action",0x63: "anim_wait",
    0x66: "sched_ext2", 0x67: "hide_hud",     0x68: "show_hud",
    0x69: "sound_vol",  0x6A: "sound_vol2",   0x6E: "emote_anim",
    0x73: "cast_magic",
    0x77: "set_time",   0x78: "reset_time",
    0x7B: "stop_talking",
    0xAF: "get_camera",
    0xB0: "print_msg5",
}

# Opcodes that reference a dialog index (arg_index = offset of the refs[] index byte from opcode start)
_DIALOG_OPCODES = {
    0x1D: 1,  # arg at byte 1
    0x24: 1,  # arg at byte 1 (menu text)
    0x2B: 5,  # arg at byte 5
    0x48: 1,
    0x49: 1,
    0xB0: 10,
}

# Opcodes whose operand is a 2-byte work-selector that resolves (via refs[]) to a ZONE id —
# "load an additional zone for the event" so a scene can play against different geometry
# (e.g. Lower Delkfutt's Tower cutscenes load Qufim Island for the establishing shot, then
# reload the tower). 0x34 loads with XiZone::Close (full swap); 0x35 loads without closing.
# (arg_offset = byte offset of the 2-byte selector from the opcode byte.)
_ZONE_OPCODES = {
    0x34: 1,  # load_zone   — selector at byte 1
    0x35: 1,  # load_zone2  — selector at byte 1
}

# Cutscene marker opcodes (presence of any of these = this event is a cutscene)
_CUTSCENE_OPCODES = {0x38, 0x46, 0x67}

# Opcode families used to CATEGORISE an event (see categorize_event).
_PRINT_OPCODES = {0x1D, 0x2B, 0x48, 0x49, 0xB0}   # print event message (with/without speaker)
_MENU_OPCODES  = {0x24, 0x25, 0x40, 0x41, 0x7F}   # selection menu + option flags + wait-select
_DOOR_OPCODES  = {0x4C, 0x4D}                      # open / close door
_CAST_OPCODES  = {0x73, 0xC4}                      # schedule cast-magic tasks


# ---------------------------------------------------------------------------
# Actor / NPC resolution — which entity an opcode acts on.
#
# Many opcodes embed one or two 4-byte little-endian *actor ids*. These are either
# GetActorIndex "magic" ids (the player / event entity / a party-or-alliance slot) or a
# real NPC server id (high byte set; target index = id & 0x3FF), which the zone's Entity
# (NPC) DAT maps to a name. Ported from dump_event.py (_ACTOR_MAGIC / label_actor),
# which mirrors the client's GetActorIndex table (research/XiEvents Event VM Functions).
# ---------------------------------------------------------------------------

_ACTOR_MAGIC = {
    0x7FFFFFC0: "local player", 0x7FFFFFF0: "local player", 0x7FFFFFF9: "local player",
    0x7FFFFFF8: "event entity",
}
for _i, _v in enumerate((0x7FFFFFC1, 0x7FFFFFC2, 0x7FFFFFC3, 0x7FFFFFC4, 0x7FFFFFC5), start=1):
    _ACTOR_MAGIC[_v] = f"party member {_i}"
for _i, _v in enumerate((0x7FFFFFC6, 0x7FFFFFC7, 0x7FFFFFC8, 0x7FFFFFC9, 0x7FFFFFCA, 0x7FFFFFCB), start=10):
    _ACTOR_MAGIC[_v] = f"alliance member {_i}"
for _i, _v in enumerate((0x7FFFFFCC, 0x7FFFFFCD, 0x7FFFFFCE, 0x7FFFFFCF, 0x7FFFFFD0, 0x7FFFFFD1), start=20):
    _ACTOR_MAGIC[_v] = f"alliance member {_i}"

# Opcode → byte offsets (within raw_args, i.e. AFTER the opcode byte) of its 4-byte actor
# id operand(s). Layouts confirmed from dump_event.py decode_focus (scheduler/look/cast)
# and real bytes. Scheduler tasks: res@0, actorA@2, actorB@6, action FourCC@10. The
# two-entity create/wait variants pack actorA@0, actorB@4. Single-entity ops carry one id.
_ACTOR_ARG_OPCODES = {
    0x45: (2, 6), 0x62: (2, 6), 0x52: (2, 6), 0x55: (2, 6),   # load/wait/end scheduler task
    0x2C: (0, 4), 0x2D: (0, 4),                               # create scheduler task (actorA, actorB)
    0x50: (0, 4), 0x51: (0, 4), 0x53: (0, 4), 0x54: (0, 4),   # end / wait task (two entities)
    0x1E: (0,),                                               # look at + "talk"
    0x4A: (0, 4),                                             # look-at (source, target)
    0x4B: (0,),                                               # set yaw
    0x4E: (1,),                                               # set entity visibility (flag@0, entity@1)
    0x6C: (0,),                                               # fade entity colour
    0x6E: (0,),                                               # play emote animation
    0x2B: (0,),                                               # print message with speaker (speaker@0)
    0x73: (0, 4), 0xC4: (1, 5),                               # cast magic (caster, target); C4 = 73 with a sub byte first (12 B)
}

# Opcodes that play an animation/emote directly on an entity (for the timeline).
_EMOTE_OPCODES = {0x6E}                # play emote animation
_ANIM_OPCODES  = {0x63, 0x1E}          # 0x63 play-anim-on-event-entity+wait; 0x1E look+talk
_VIS_OPCODES   = {0x4E}                # set entity visibility (event-hide flag): flag@0, entity@1
# Facing: 0x4A look-at (source@0 faces target@4); 0x4B set-yaw (entity@0 + yaw selector@4).
# 0x4B yaw is signed/1000 radians. 0xBA's *dir* component is different: 4096 units/turn
# (ref → radians via ×2π/4096); only BA's X/Z/Y use the signed/1000 scale.
_FACE_LOOK_OPCODE = 0x4A
_FACE_YAW_OPCODE  = 0x4B


def _actor_label(aid: int, names: dict | None = None) -> Optional[str]:
    """Human label for a 4-byte actor id: a magic-id role, an Entity-DAT name, or an
    ``NPC #idx`` fallback. Returns None when the value isn't a plausible actor id."""
    if aid in _ACTOR_MAGIC:
        return _ACTOR_MAGIC[aid]
    if names and names.get(aid):
        return names[aid]
    if aid & 0xFF000000:                     # real NPC server id → target index in low bits
        return f"NPC #{aid & 0x3FF}"
    return None


def _opcode_actors(op: int, raw_args: bytes, names: dict | None = None) -> list[dict]:
    """Resolve the actor id operand(s) an opcode carries → ``[{off, id, label}]`` (only the
    entries that look like real actors). Empty for opcodes that don't reference an entity."""
    out = []
    for off in _ACTOR_ARG_OPCODES.get(op, ()):
        if off + 4 > len(raw_args):
            continue
        aid = int.from_bytes(raw_args[off:off + 4], "little")
        label = _actor_label(aid, names)
        if label:
            out.append({"off": off, "id": aid, "label": label})
    return out


# Opcode 0xBA "calibrate an entity's position": operand = entity u32 + four 2-byte
# work-selectors that resolve (via refs[]) to a SIGNED int / 1000 = world coordinate, in
# **X, Z, Y, dir** order (Z/Y swapped vs xyz — verified against this event's camera routes).
# This is how a cutscene stages its NPCs (the data I previously, wrongly, thought was
# server-side): the event places each actor before animating it.
_POS_OPCODE = 0xBA
_POS_SCALE = 1000.0


def _signed32(v: int) -> int:
    return v - 0x100000000 if v >= 0x80000000 else v


def event_entity_positions(event, refs: list[int]) -> dict:
    """``{actorId: {"pos": [x, y, z], "dir": float}}`` from the event's 0xBA position opcodes.

    Uses the **first** 0xBA per entity (its initial staging spot). A selector that's a runtime
    work slot (high bit clear) makes that component unknown → the entity is skipped."""
    out = {}
    for o in event.opcodes:
        if o.op != _POS_OPCODE:
            continue
        a = o.raw_args
        if len(a) < 12:
            continue
        ent = int.from_bytes(a[0:4], "little")
        if ent in out:
            continue
        comps = []
        for i in (4, 6, 8, 10):              # X, Z, Y, dir selectors
            sel = a[i] | (a[i + 1] << 8)
            if sel & 0x8000 and (sel & 0x7FFF) < len(refs):
                comps.append(_signed32(refs[sel & 0x7FFF]) / _POS_SCALE)
            else:
                comps.append(None)
        x, z, y, d = comps
        if None in (x, y, z):
            continue
        # ★ Heading decode: the dir component is NOT ×1000-scaled like positions —
        # it's 4096 units per full turn (proven by retail ref statistics; client
        # decodes ref×2π/4096, xievents 0x00BA). `comps` divided it by _POS_SCALE,
        # so undo that, then convert units → radians, normalized to (−π, π].
        if d is not None:
            units = d * _POS_SCALE                      # raw signed ref value
            d = (units / 4096.0) * (2.0 * math.pi)
            d = (d + math.pi) % (2.0 * math.pi) - math.pi
        out[ent] = {"pos": [x, y, z], "dir": d}
    return out


# ---------------------------------------------------------------------------
# Variable-step dispatcher + size lookup (ported from the client's
# EventDisassembler.cpp variableOpcodeSize() / opcodeSize(), via dump_event.py).
# ---------------------------------------------------------------------------

def _variable_opcode_size(opcode: int, sub: int) -> int:
    # Closed-form cases first (mirror the switch in variableOpcodeSize()).
    if opcode == 0x1F:
        return 8 if sub == 0 else 2
    if opcode == 0x31:
        return 10 if sub == 0 else 2
    if opcode == 0x46:
        return 4 if sub == 2 else 2
    if opcode == 0x47:
        return 10 if sub == 0 else 2
    if opcode == 0x5A:
        return 8 if sub == 0 else 2
    if opcode in (0x5B, 0x66):
        return 15
    if opcode == 0x5C:
        return 4 if sub <= 7 else 6
    if opcode == 0x60:
        return 4 if sub <= 1 else (6 if sub == 2 else 2)
    if opcode == 0x72:
        return 4 if sub == 0 else 6
    if opcode == 0x75:
        return 4 if sub == 0 else 2
    if opcode == 0x79:
        return 12 if sub == 1 else 10
    if opcode == 0xA6:
        return 4 if sub == 2 else 2
    if opcode == 0xA7:
        return 4 if sub == 1 else 2
    if opcode == 0xAB:
        return 4 if sub == 0x11 else 2
    if opcode == 0xB2:
        return 2 if sub == 0 else 4
    if opcode == 0xBF:
        return 8 if (sub == 0 or sub == 0x60) else 10
    if opcode == 0xC2:
        return 4 if sub == 1 else (6 if sub == 2 else 2)
    if opcode in _SUB_TABLES:
        fallback, table = _SUB_TABLES[opcode]
        return table.get(sub, fallback)
    return 0  # 0xCA / 0xCB are N/A; unknown opcodes have no size.


def _opcode_size(opcode: int, sub: int) -> int:
    """Instruction length for ``opcode`` (sub-selector ``sub``), or 0 when it can't be
    sized (unknown / N-A) — callers treat 0 as 'stop decoding here'."""
    if opcode >= 0xDA:
        return 0
    fixed = _FIXED_SIZES[opcode]
    if fixed != 0:
        return fixed
    return _variable_opcode_size(opcode, sub)


def _step(data: bytes, pos: int) -> int:
    op = data[pos]
    sub = data[pos + 1] if pos + 1 < len(data) else 0
    return _opcode_size(op, sub)


def _opcode_name(op: int) -> str:
    return _OPCODE_NAMES.get(op, f"unk_{op:02x}")


# ---------------------------------------------------------------------------
# FourCC detection
# ---------------------------------------------------------------------------

def _is_printable_fourcc(val: int) -> bool:
    b = val.to_bytes(4, 'little')
    return all(0x20 <= c < 0x7F for c in b)


def _fourcc_str(val: int) -> str:
    return val.to_bytes(4, 'little').decode('ascii', errors='replace')


# ---------------------------------------------------------------------------
# getworkofs — mirrors xeno FUNC_XiEvent_getworkofs
# Returns refs[data[pos+index]] if valid, else -1
# ---------------------------------------------------------------------------

def _getworkofs(data: bytes, pos: int, index: int, refs: list[int]) -> int:
    arg_pos = pos + index
    if arg_pos >= len(data):
        return -1
    val = struct.unpack_from('b', data, arg_pos)[0]  # signed byte
    if 0 <= val < len(refs):
        return refs[val]
    return -1


def _resolve_work_selector(data: bytes, pos: int, refs: list[int]) -> int:
    """Resolve a 2-byte little-endian *work-selector* at ``data[pos:pos+2]`` against refs[].

    The event VM encodes "this operand is a reference, not a literal" with the high bit
    ``0x8000``: when set, the low 15 bits index the actor block's refs[]/ImidData table and
    the *value stored there* is the real argument (a zone id, frame count, msg id, …). When
    the high bit is clear the operand is a runtime work-area slot, which we can't statically
    resolve. Returns the refs value, or ``-1`` for a runtime slot / out-of-range index.
    (This is the same model as :func:`_sel_value`; kept separate so it can read straight
    from the bytecode stream during disassembly.)"""
    if pos + 1 >= len(data):
        return -1
    v = data[pos] | (data[pos + 1] << 8)
    if v & 0x8000 and (v & 0x7FFF) < len(refs):
        return refs[v & 0x7FFF]
    return -1


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class OpcodeRecord:
    offset: int
    op: int
    name: str
    step: int
    dialog_ref: int = -1        # resolved dialog index (-1 if not a dialog op)
    zone_ref: int = -1          # resolved zone id for load_zone (0x34/0x35); -1 otherwise
    raw_args: bytes = b''


@dataclass
class EventRecord:
    event_id: int
    offset: int
    opcodes: list = field(default_factory=list)
    is_cutscene: bool = False
    dialog_ids: list = field(default_factory=list)
    animation_tags: list = field(default_factory=list)


@dataclass
class ActorRecord:
    actor_id: int
    events: list = field(default_factory=list)
    refs: list = field(default_factory=list)
    ref_fourccs: list = field(default_factory=list)  # printable FourCC strings from refs[]


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class EventDatError(Exception):
    pass


def parse_event_dat(data: bytes) -> list[ActorRecord]:
    if len(data) < 8:
        raise EventDatError("file too small")

    pos = 0
    block_count = struct.unpack_from('<I', data, pos)[0]
    pos += 4

    # Sanity: blockCount should be plausible (< 65536) and the header should fit
    if block_count > 65535 or pos + 4 * block_count > len(data):
        raise EventDatError(
            f"implausible blockCount={block_count} — not an event DAT")

    block_sizes = list(struct.unpack_from(f'<{block_count}I', data, pos))
    pos += 4 * block_count

    actors = []
    for bi in range(block_count):
        bsize = block_sizes[bi]
        block = data[pos:pos + bsize]
        pos += bsize
        actor = _parse_actor_block(block, bi)
        if actor is not None:
            actors.append(actor)

    return actors


def _parse_actor_block(block: bytes, block_index: int) -> Optional['ActorRecord']:
    try:
        return _do_parse_actor_block(block, block_index)
    except (struct.error, IndexError):
        return None


# ---------------------------------------------------------------------------
# Raw (mutable, byte-exact) representation — the basis for AUTHORING events.
#
# parse_event_dat() disassembles into opcodes and throws the raw bytes away, which
# is lossy. For editing we need a structure that round-trips byte-for-byte: keep
# each actor block's fields AND its original bytes, write untouched actors verbatim,
# and only rebuild the one we edit. build_event_dat() reassembles the file.
# ---------------------------------------------------------------------------

@dataclass
class RawActor:
    actor_id: int
    event_offsets: list          # u16 entry-point offsets into scene_data
    event_ids: list              # u16 event ids. 0xFFFF = placeholder id on a real entry
                                 # offset (~130k in retail corpus); 0xFFFE = wildcard the
                                 # engine DISPATCHES for any requested id (zone blocks)
    references: list             # u32 references[] (ImidData) — msg ids, zone ids, FourCCs…
    scene_data: bytes            # raw bytecode (length = the UNPADDED sceneSize field exactly)
    block_pad: bytes             # trailing bytes that pad the block to a 4-byte boundary (engine-ignored)
    raw_block: bytes             # the original full block bytes (verbatim write when untouched)
    dirty: bool = False          # set True when a field is edited → rebuild instead of verbatim


def parse_raw_actors(data: bytes) -> list:
    """Parse an event DAT into :class:`RawActor` blocks, preserving each block's raw bytes.
    ``build_event_dat(parse_raw_actors(d)) == d`` byte-for-byte for a well-formed DAT."""
    if len(data) < 8:
        raise EventDatError("file too small")
    pos = 0
    block_count = struct.unpack_from('<I', data, pos)[0]; pos += 4
    if block_count > 65535 or pos + 4 * block_count > len(data):
        raise EventDatError(f"implausible blockCount={block_count} — not an event DAT")
    block_sizes = list(struct.unpack_from(f'<{block_count}I', data, pos)); pos += 4 * block_count
    out = []
    for bi in range(block_count):
        bsize = block_sizes[bi]
        block = data[pos:pos + bsize]; pos += bsize
        bp = 0
        actor_id = struct.unpack_from('<I', block, bp)[0]; bp += 4
        ec = struct.unpack_from('<I', block, bp)[0]; bp += 4
        offsets = list(struct.unpack_from(f'<{ec}H', block, bp)); bp += 2 * ec
        ids     = list(struct.unpack_from(f'<{ec}H', block, bp)); bp += 2 * ec
        rc = struct.unpack_from('<I', block, bp)[0]; bp += 4
        refs = list(struct.unpack_from(f'<{rc}I', block, bp)); bp += 4 * rc
        scene_size = struct.unpack_from('<i', block, bp)[0]; bp += 4
        scene = block[bp:bp + scene_size]
        block_pad = block[bp + scene_size:]   # trailing bytes that align the block to 4
        out.append(RawActor(actor_id, offsets, ids, refs, scene, block_pad, block))
    return out


def serialize_actor(a: RawActor) -> bytes:
    """Serialize one actor block. ``sceneSize`` is the UNPADDED scene length; the block is then
    aligned to a 4-byte boundary by a trailing pad (the original bytes when untouched, else
    zeros). Byte-identical to the source block for an unedited actor."""
    scene = bytes(a.scene_data)
    if a.dirty:
        pad = b"\x00" * ((-len(scene)) % 4)
    else:
        pad = bytes(a.block_pad)
    buf = bytearray()
    buf += struct.pack('<I', a.actor_id & 0xFFFFFFFF)
    buf += struct.pack('<I', len(a.event_ids))
    for o in a.event_offsets: buf += struct.pack('<H', o & 0xFFFF)
    for e in a.event_ids:     buf += struct.pack('<H', e & 0xFFFF)
    buf += struct.pack('<I', len(a.references))
    for r in a.references:    buf += struct.pack('<I', r & 0xFFFFFFFF)
    buf += struct.pack('<i', len(scene))
    buf += scene
    buf += pad
    return bytes(buf)


def build_event_dat(actors: list) -> bytes:
    """Reassemble an event DAT from :class:`RawActor` blocks. Untouched actors (``dirty``
    False) are written verbatim from ``raw_block``; edited actors are re-serialized."""
    blocks = [serialize_actor(a) if a.dirty else a.raw_block for a in actors]
    out = bytearray()
    out += struct.pack('<I', len(blocks))
    for b in blocks: out += struct.pack('<I', len(b))
    for b in blocks: out += b
    return bytes(out)


def _do_parse_actor_block(block: bytes, block_index: int) -> 'ActorRecord':
    bp = 0

    actor_id = struct.unpack_from('<I', block, bp)[0]; bp += 4
    event_count = struct.unpack_from('<I', block, bp)[0]; bp += 4

    event_offsets = list(struct.unpack_from(f'<{event_count}H', block, bp)); bp += 2 * event_count
    event_ids     = list(struct.unpack_from(f'<{event_count}H', block, bp)); bp += 2 * event_count

    ref_count = struct.unpack_from('<I', block, bp)[0]; bp += 4
    refs = list(struct.unpack_from(f'<{ref_count}I', block, bp)); bp += 4 * ref_count

    scene_size = struct.unpack_from('<i', block, bp)[0]; bp += 4
    scene = block[bp:bp + scene_size]

    # Decode FourCC refs
    ref_fourccs = [_fourcc_str(r) for r in refs if _is_printable_fourcc(r)]

    events = []
    for i in range(event_count):
        eid = event_ids[i]
        # Listing-only skip. NB the engine does NOT skip 0xFFFE — it's a wildcard
        # that matches ANY requested event id (see external_source/dump_event.py);
        # 0xFFFF is a placeholder id on an otherwise-real entry offset. Neither is
        # addressable by id, so neither belongs in the per-id event list.
        if eid in (0xFFFF, 0xFFFE):
            continue
        eoff = event_offsets[i]
        ev = _disassemble_event(scene, eoff, refs, eid)
        events.append(ev)

    return ActorRecord(
        actor_id=actor_id,
        events=events,
        refs=refs,
        ref_fourccs=ref_fourccs,
    )


def _disassemble_event(scene: bytes, start: int, refs: list[int], event_id: int) -> EventRecord:
    """Disassemble one event by walking its bytecode from ``start`` until the first
    ``end`` (0x21) — following fall-through across tag boundaries, the way the engine
    runs an event from its entry point. (Events are NOT self-contained regions: a tag
    offset is just an entry into a shared instruction stream, and many events fall
    through past the next tag offset until they hit a real ``end``.)

    It does NOT follow jump/branch targets — so an event whose dialogue/cutscene is
    reached only via a forward jump can be under-categorised. Following branches needs
    correct target decoding (measured ambiguous — see docs/events B-decode roadmap), so
    this stays a linear, fall-through heuristic. The unknown-opcode / overrun checks are
    a desync guard: stop cleanly at garbage rather than decoding through it."""
    opcodes = []
    is_cutscene = False
    dialog_ids = []
    anim_tags = set()

    pos = start
    seen_offsets = set()
    limit = 8192  # guard against loops / corrupted data

    while pos < len(scene) and limit > 0:
        if pos in seen_offsets:
            break
        seen_offsets.add(pos)
        limit -= 1

        op = scene[pos]
        step = _step(scene, pos)
        if step < 1 or pos + step > len(scene):   # unknown/unsized opcode or operand overrun → stop
            break

        dialog_ref = -1
        if op in _DIALOG_OPCODES:
            # The message operand is a 2-byte work-selector (0x8000 | refIndex), NOT a signed
            # byte — verified across 13.6k retail print_msg ops, 17% of which index references[]
            # beyond 127. (The old single-byte read silently dropped those as -1.)
            dialog_ref = _resolve_work_selector(scene, pos + _DIALOG_OPCODES[op], refs)
            if dialog_ref >= 0 and dialog_ref not in dialog_ids:
                dialog_ids.append(dialog_ref)

        zone_ref = -1
        if op in _ZONE_OPCODES:
            zone_ref = _resolve_work_selector(scene, pos + _ZONE_OPCODES[op], refs)

        opcodes.append(OpcodeRecord(
            offset=pos - start,
            op=op,
            name=_opcode_name(op),
            step=step,
            dialog_ref=dialog_ref,
            zone_ref=zone_ref,
            raw_args=scene[pos + 1:pos + step],
        ))
        if op in _CUTSCENE_OPCODES:
            is_cutscene = True
        if op == 0x21:  # END EVENT
            break
        pos += step

    # Animation FourCCs the event can reference (scan all of the actor's refs[]).
    for r in refs:
        if _is_printable_fourcc(r):
            anim_tags.add(_fourcc_str(r))

    return EventRecord(
        event_id=event_id,
        offset=start,
        opcodes=opcodes,
        is_cutscene=is_cutscene,
        dialog_ids=sorted(dialog_ids),
        animation_tags=sorted(anim_tags),
    )


# ---------------------------------------------------------------------------
# JSON serialization helpers
# ---------------------------------------------------------------------------

def actor_to_dict(actor: ActorRecord, cutscene_only: bool = False,
                  include_opcodes: bool = True, names: dict | None = None) -> dict:
    events = actor.events
    if cutscene_only:
        events = [e for e in events if e.is_cutscene]
    if not events:
        return None

    return {
        "actor_id": f"0x{actor.actor_id:08X}",
        "actor_id_int": actor.actor_id,
        "actor_name": (names or {}).get(actor.actor_id, ""),
        "ref_count": len(actor.refs),
        "refs_hex": [f"0x{r:08X}" for r in actor.refs],
        "refs_fourcc": actor.ref_fourccs,
        "events": [event_to_dict(e, include_opcodes, names) for e in events],
    }


def event_to_dict(event: EventRecord, include_opcodes: bool = True, names: dict | None = None) -> dict:
    d = {
        "event_id": event.event_id,
        "offset": event.offset,
        "is_cutscene": event.is_cutscene,
        "dialog_ids": event.dialog_ids,
        "animation_tags": event.animation_tags,
        "opcode_count": len(event.opcodes),
    }
    if include_opcodes:
        d["opcodes"] = [opcode_to_dict(o, names) for o in event.opcodes]
    return d


# Lazy, cached {zone_id: name} so opcode dumps can label a load_zone target. Resolved
# from the zone table on first use; falls back to {} if it can't load (keeps the parser
# usable standalone, without the xi.zone package).
_ZONE_NAME_CACHE: dict | None = None


def zone_name_for(zone_id: int) -> Optional[str]:
    """Best-effort display name for a zone id (e.g. 126 → "Qufim Island"), or None."""
    global _ZONE_NAME_CACHE
    if _ZONE_NAME_CACHE is None:
        try:
            from xi.zone.xi_list import get_zone_entries
            _ZONE_NAME_CACHE = {z["id"]: z["name"] for z in get_zone_entries(path_prefix="")}
        except Exception:
            _ZONE_NAME_CACHE = {}
    return _ZONE_NAME_CACHE.get(zone_id)


def opcode_to_dict(op: OpcodeRecord, names: dict | None = None) -> dict:
    d = {
        "offset": op.offset,
        "op": f"0x{op.op:02X}",
        "name": op.name,
        "step": op.step,
        "args": op.raw_args.hex() if op.raw_args else "",
    }
    if op.dialog_ref >= 0:
        d["dialog_ref"] = op.dialog_ref
    if op.zone_ref >= 0:
        d["zone_ref"] = op.zone_ref
        zn = zone_name_for(op.zone_ref)
        if zn:
            d["zone_name"] = zn
    actors = _opcode_actors(op.op, op.raw_args, names)
    if actors:
        d["actors"] = actors                       # [{off, id, label}] — who this opcode acts on
    return d


# ---------------------------------------------------------------------------
# Categorisation — bucket an event by what its bytecode does.
# Priority order: a cutscene that also prints dialogue is still a "Cutscene".
# ---------------------------------------------------------------------------

# Ordered most-specific → least; also the display order for the editor's filter chips.
CATEGORY_ORDER = ["Cutscene", "Menu", "Dialogue", "Door", "Magic", "Script", "Empty"]


def categorize_event(event: EventRecord) -> str:
    """Single primary category for an event, from the opcodes it runs.

    Cutscene (camera/HUD takeover) wins over everything; then a selection Menu;
    then plain Dialogue; then Door / Magic single-purpose scripts; then any other
    non-trivial Script; finally Empty (just an ``end`` / no real opcodes)."""
    ops = {o.op for o in event.opcodes}
    if ops & _CUTSCENE_OPCODES:
        return "Cutscene"
    if ops & _MENU_OPCODES:
        return "Menu"
    if ops & _PRINT_OPCODES:
        return "Dialogue"
    if ops & _DOOR_OPCODES:
        return "Door"
    if ops & _CAST_OPCODES:
        return "Magic"
    # Anything left that does more than terminate is a generic script.
    if any(o.op != 0x21 for o in event.opcodes):
        return "Script"
    return "Empty"


# ---------------------------------------------------------------------------
# Entity (NPC) DAT — actor_id → name.
# Layout (xeno EntityDat.cs): packed 32-byte records, char[28] name + u32 serverID.
# ---------------------------------------------------------------------------

def parse_entity_names(data: bytes) -> dict[int, str]:
    """Map ``serverID → display name`` from a zone's NPC (entity) DAT.

    Names are ~Shift-JIS (cp932); blanks are skipped. Returns ``{}`` for a too-small
    or unreadable DAT so the caller can fall back to showing the hex actor id."""
    names: dict[int, str] = {}
    if not data or len(data) < 32:
        return names
    for off in range(0, (len(data) // 32) * 32, 32):
        try:
            sid = struct.unpack_from("<I", data, off + 28)[0]
            raw = data[off:off + 28].split(b"\x00", 1)[0]
            name = raw.decode("cp932", errors="replace").strip()
        except (struct.error, IndexError):
            continue
        if name and sid:
            names[sid] = name
    return names


# ---------------------------------------------------------------------------
# Editor payload — actor → event tree with categories + stats, for the web editor.
# ---------------------------------------------------------------------------

def build_events_payload(event_data: bytes, npc_data: bytes | None = None) -> dict:
    """Parse a zone's event DAT (+ optional NPC DAT for names) into the shape the
    level editor's Events panel renders: a list of actors, each with its events and
    a per-event category, plus summary stats (counts + per-category breakdown).

    Raises :class:`EventDatError` if ``event_data`` is not a valid event DAT."""
    actors = parse_event_dat(event_data)
    names = parse_entity_names(npc_data or b"")

    out_actors = []
    cat_counts = {c: 0 for c in CATEGORY_ORDER}
    total_events = 0
    total_cutscenes = 0
    total_dialog_lines = 0

    for a in actors:
        events = []
        for e in a.events:
            cat = categorize_event(e)
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
            total_events += 1
            if e.is_cutscene:
                total_cutscenes += 1
            total_dialog_lines += len(e.dialog_ids)
            ops = {o.op for o in e.opcodes}
            events.append({
                "eventId": e.event_id,
                "eventIdHex": f"{e.event_id:04X}",
                "category": cat,
                "isCutscene": e.is_cutscene,
                "opcodeCount": len(e.opcodes),
                "dialogCount": len(e.dialog_ids),
                "dialogIds": e.dialog_ids,
                "hasMenu": bool(ops & _MENU_OPCODES),
                "hasDoor": bool(ops & _DOOR_OPCODES),
                "offset": e.offset,
            })
        if not events:
            continue
        sid = a.actor_id
        out_actors.append({
            "actorId": sid,
            "actorIdHex": f"0x{sid:08X}",
            "name": names.get(sid, ""),
            "targetIndex": sid & 0x3FF,
            "eventCount": len(events),
            "events": events,
        })

    out_actors.sort(key=lambda a: a["actorId"])
    return {
        "stats": {
            "actorCount": len(out_actors),
            "eventCount": total_events,
            "cutsceneCount": total_cutscenes,
            "dialogLineCount": total_dialog_lines,
            "byCategory": cat_counts,
        },
        "actors": out_actors,
    }


# ---------------------------------------------------------------------------
# Cutscene timeline — a "what plays when" view of an event's bytecode.
# Ordered beats (dialogue / shot / wait / fade / music / camera / end) with frame
# timing where it's statically known. Camera + entity motion is performed by named
# scheduler "shots" (0x45 start_task action='sNNN'); the shot tag is surfaced here,
# the actual camera path lives inside the referenced 'evte' scene resource.
# ---------------------------------------------------------------------------

_TASK_START_OPCODES = {0x45, 0x62, 0x7D, 0x2C, 0x2D, 0x9F}
# Animation-task opcodes → (actorA byte offset, 4-char action-tag byte offset) in raw_args.
# These play a named motion on an entity: 0x2C entity-local (action@8); 0x5B/0x45/0x62 via a
# loaded scheduler resource (action@10). Used to build the per-actor animation track.
_ANIM_TAG_OPCODES = {0x2C: (0, 8), 0x5B: (2, 10), 0x45: (2, 10), 0x62: (2, 10)}
_TASK_END_OPCODES = {0x50, 0x51, 0x52, 0x53, 0x54, 0x55, 0xA0, 0xA1, 0xA2, 0xA3}
_CAM_BEAT_OPCODES = {0x20, 0x38, 0x46, 0x67, 0x68}
_MUSIC_OPCODES = {0x5C, 0x5D}


def _sel_value(raw_args: bytes, i: int, refs: list[int]):
    """Resolve a 16-bit work selector at ``raw_args[i:i+2]`` against the refs/ImidData
    table: high bit 0x8000 set → ``refs[low 15 bits]``; else ``None`` (a runtime slot)."""
    if i + 1 >= len(raw_args):
        return None
    v = raw_args[i] | (raw_args[i + 1] << 8)
    if v & 0x8000 and (v & 0x7FFF) < len(refs):
        return refs[v & 0x7FFF]
    return None


def _task_action_tag(raw_args: bytes):
    """The 4-char action FourCC a scheduler task opcode carries (res, A, B, *action*)."""
    if len(raw_args) >= 14:
        tag = raw_args[10:14]
        if all(0x20 <= c < 0x7F for c in tag):
            return tag.decode("ascii")
    return None


def _shot_kind(tag) -> str:
    if not tag:
        return "task"
    if tag[:2] == "fd":
        return "fade"
    if tag[0] == "s" and tag[1:].isdigit():
        return "shot"
    return "task"


def build_cutscene_timeline(event, refs, dialog_index=None, names=None, dismiss_frames=90, fps=30) -> dict:
    """Decode an event's bytecode into a time-ordered list of cutscene beats.

    ``names`` (optional ``{serverId: name}`` from the zone's Entity DAT) labels the NPC each
    beat acts on. Beats gain ``actors`` (scheduler tasks), ``speaker`` (dialogue), or
    ``actor``/``caster``/``target`` (emote / anim / vfx) where the opcode names an entity.

    Walks the (linear, fall-through) opcode stream accumulating a frame clock: explicit
    ``wait_time`` (0x1C) advances it by the exact frame count from the refs table;
    ``wait_dismiss`` (0x23, user-paced) advances it by ``dismiss_frames`` (a default, so the
    timeline is playable). Shot/animation lengths are runtime, so a shot's duration is
    estimated from the frames until its matching end-task (or the next wait).

    NOTE: the linear disassembly threads through every branch's fall-through, so the beats
    can include shots from more than one quest-state path — not a single resolved
    playthrough. Resolving one path needs branch evaluation (future)."""
    beats = []
    anim_tracks: dict = {}     # {actorId: [{frame, tag, op}]} — per-NPC "what motion plays when"
    frame = 0
    dismiss_count = 0          # # of Enter-prompt (0x23) waits — subtracted for the ANIMATION length

    for o in event.opcodes:
        op = o.op
        a = o.raw_args
        # Per-actor animation track: an anim-task opcode naming a motion on a REAL npc (not a
        # 0x7F magic id). Recorded for every such op, independent of the beat handling below.
        _ai = _ANIM_TAG_OPCODES.get(op)
        if _ai:
            _ao, _to = _ai
            if _to + 4 <= len(a):
                _ent = int.from_bytes(a[_ao:_ao + 4], "little")
                _tg = a[_to:_to + 4]
                if (_ent & 0xFF000000) and (_ent >> 24) != 0x7F and all(0x20 <= c < 0x7F for c in _tg):
                    anim_tracks.setdefault(_ent, []).append(
                        {"frame": frame, "tag": _tg.decode("ascii"), "op": f"0x{op:02X}"})
        if op in _PRINT_OPCODES:
            mid = o.dialog_ref if o.dialog_ref >= 0 else None
            text = dialog_index.get(mid) if (dialog_index and mid is not None) else None
            beat = {"frame": frame, "type": "dialogue", "msgId": mid, "text": text}
            spk = _opcode_actors(op, a, names)        # 0x2B carries a speaker entity
            if spk:
                beat["speaker"] = spk[0]["label"]
            beats.append(beat)
        elif op == 0x23:  # wait for the player to dismiss the message box (user-paced)
            frame += dismiss_frames
            dismiss_count += 1
        elif op == 0x1C:  # set WaitTime → an explicit frame delay
            n = _sel_value(a, 0, refs)
            if n:
                beats.append({"frame": frame, "type": "wait", "frames": int(n)})
                frame += int(n)
        elif op in _TASK_START_OPCODES:
            tag = _task_action_tag(a)
            beat = {"frame": frame, "type": _shot_kind(tag), "tag": tag, "op": f"0x{op:02X}"}
            kind, val = resolve_task_resource(op, a, refs)   # per-opcode: only 0x45 → scene file id
            if kind == "scene_file":
                beat["res"] = val
            acts = _opcode_actors(op, a, names)
            if acts:
                beat["actors"] = [x["label"] for x in acts]
                beat["actorIds"] = [x["id"] for x in acts]
            beats.append(beat)
        elif op in _TASK_END_OPCODES:
            beats.append({"frame": frame, "type": "taskEnd", "tag": _task_action_tag(a), "op": f"0x{op:02X}"})
        elif op in _VIS_OPCODES:                      # show / hide an NPC (event-hide flag)
            acts = _opcode_actors(op, a, names)
            if acts:
                shown = len(a) >= 1 and a[0] == 0    # flag byte 0 = visible, 1 = hidden
                beats.append({"frame": frame, "type": "npc", "op": f"0x{op:02X}",
                              "actor": acts[0]["label"], "actorId": acts[0]["id"],
                              "action": "show" if shown else "hide"})
        elif op in _EMOTE_OPCODES:                    # play emote animation on an entity
            acts = _opcode_actors(op, a, names)
            beats.append({"frame": frame, "type": "emote", "op": f"0x{op:02X}",
                          "actor": acts[0]["label"] if acts else None,
                          "actorId": acts[0]["id"] if acts else None})
        elif op in _ANIM_OPCODES:                     # play animation / look+talk on an entity
            acts = _opcode_actors(op, a, names)
            who = acts[0]["label"] if acts else ("event entity" if op == 0x63 else None)
            beats.append({"frame": frame, "type": "anim", "op": f"0x{op:02X}",
                          "name": o.name, "actor": who,
                          "actorId": acts[0]["id"] if acts else None})
        elif op == _FACE_LOOK_OPCODE:                 # look-at: source faces target
            acts = _opcode_actors(op, a, names)
            if acts:
                beats.append({"frame": frame, "type": "face", "op": f"0x{op:02X}",
                              "actor": acts[0]["label"], "actorId": acts[0]["id"],
                              "target": acts[1]["label"] if len(acts) > 1 else None,
                              "targetId": acts[1]["id"] if len(acts) > 1 else None})
        elif op == _FACE_YAW_OPCODE:                  # set-yaw: explicit heading (radians)
            acts = _opcode_actors(op, a, names)
            yaw_raw = _sel_value(a, 4, refs)
            if acts and yaw_raw is not None:
                beats.append({"frame": frame, "type": "face", "op": f"0x{op:02X}",
                              "actor": acts[0]["label"], "actorId": acts[0]["id"],
                              "yaw": _signed32(yaw_raw) / 1000.0})
        elif op in _CAST_OPCODES:                     # cast-magic VFX on caster→target
            acts = _opcode_actors(op, a, names)
            beats.append({"frame": frame, "type": "vfx", "op": f"0x{op:02X}",
                          "caster": acts[0]["label"] if len(acts) > 0 else None,
                          "target": acts[1]["label"] if len(acts) > 1 else None})
        elif op in _CAM_BEAT_OPCODES:
            beats.append({"frame": frame, "type": "camera", "op": f"0x{op:02X}", "name": o.name})
        elif op in _MUSIC_OPCODES:
            beats.append({"frame": frame, "type": "music", "op": f"0x{op:02X}", "name": o.name})
        elif op == 0x21:  # END EVENT
            beats.append({"frame": frame, "type": "end"})

    total = max((b["frame"] for b in beats), default=0)
    # Estimate shot/fade/task durations: until the matching end-task (by tag) or the next
    # later-frame beat; fall back to a default so the bar is visible on the track.
    for i, b in enumerate(beats):
        if b["type"] not in ("shot", "fade", "task"):
            continue
        end_frame = None
        for nb in beats[i + 1:]:
            if nb["frame"] <= b["frame"]:
                continue
            if nb["type"] == "taskEnd" and nb.get("tag") == b.get("tag"):
                end_frame = nb["frame"]; break
            end_frame = nb["frame"]; break
        b["dur"] = max(1, (end_frame - b["frame"]) if end_frame is not None else 30)

    counts = {}
    for b in beats:
        counts[b["type"]] = counts.get(b["type"], 0) + 1
    # Animation length = the frame clock MINUS the player-paced Enter-prompt padding
    # (each 0x23 was estimated at `dismiss_frames`). This is the "real" duration of the
    # non-interactive content (fades + camera + explicit waits) — `totalFrames` is the
    # padded estimate used to lay dialogue beats out on the timeline.
    content_frames = max(0, total - dismiss_count * dismiss_frames)
    return {
        "beats": beats,
        "totalFrames": total,
        "contentFrames": content_frames,
        "dialoguePrompts": dismiss_count,
        "fps": fps,
        "dismissFrames": dismiss_frames,
        "shotTags": sorted({b["tag"] for b in beats if b["type"] == "shot" and b.get("tag")}),
        "counts": counts,
        "animTracks": {f"{aid}": trk for aid, trk in anim_tracks.items()},   # per-NPC motion timeline
    }


# ---------------------------------------------------------------------------
# Camera paths — the scheduler "scene resource" (section-headered 'evte') that a
# cutscene's shots reference. Each shot 'sNNN' (0x07 EffectRoutine) is paired with a
# camera Route 'cNNN' (0x06) holding the eye/look-at/FOV spline. Layout VERIFIED
# 2026-06-18 across 22.5k Routes / 42.6k keyframes (14 zones) — see the
# ffxi-event-format / web-level-editor memory.
#
# Route body (little-endian):
#   +0x00  16  zero
#   +0x10  u32 keyframeCount
#   +0x14  u32 interpMode   # smoothing/path enum 0..4: 0 mixed (snap/linear), 1 (most
#                           #   common) = multi-point spline, 4 a 2nd multi variant, 2/3
#                           #   rare. count==1 ⇒ always 0. Exact easing curve per value
#                           #   still maps to xiclient's CameraSmoothType names.
#   +0x18  8   zero
#   then keyframeCount × 48-byte keyframes:
#   +0x00  3f  eye xyz      (raw FFXI world space, same frame as zone placements)
#   +0x0C  f   FOCAL LENGTH (SplineControlPoint.FovCalculationParameter; NOT an angle). The
#                           #   client's vertical FOV = 2·atan2(192, focal) (xiclient
#                           #   GameManager::UpdateProjectionMatrix). Default 350→57°; larger =
#                           #   zoomed IN (350→57°, 250→75°, 480→43°). Authored round vals 300/350.
#   +0x10  3f  lookAt xyz
#   +0x1C  f   roll         radians, almost always 0 (rare tilts ~0.012-0.017)
#   +0x20  f   time         normalised 0..1 (SplineControlPoint.Param.x)
#   +0x24  12  zero pad     (never non-zero in 42.6k keyframes -> 16-byte alignment)
# ---------------------------------------------------------------------------

def _datid_helper(p: int) -> int:
    """Scheduler resource-id remap (client): file_id = 30704 + this(p).

    Tiers at 300/600. ★ Custom cutscene cameras MUST use p in 300..599 only —
    p≥600 (file ids 71k+) crash the client even with valid scene DATs. See
    docs/events/camera_scene_ids.md."""
    if p >= 600:
        return p + 39643
    if p >= 300:
        return p + 25937
    return p


def _scene_sections(data: bytes):
    """Walk a section-headered DAT → list of (offset, fourcc, typeCode, size). 16-byte
    headers; ``size = (meta>>7 & 0xFFFFF) * 16``, ``typeCode = meta & 0x7F`` (xim's model)."""
    pos, out, n = 0, [], len(data)
    while pos + 16 <= n:
        meta = struct.unpack_from("<I", data, pos + 4)[0]
        size = ((meta >> 7) & 0xFFFFF) * 0x10
        if size <= 0:
            break
        tag = data[pos:pos + 4].split(b"\x00", 1)[0].decode("ascii", "replace")
        out.append((pos, tag, meta & 0x7F, size))
        pos = (pos + size + 15) & ~15
    return out


def parse_camera_routes(data: bytes) -> dict:
    """Parse a scene resource → ``{routeTag: [keyframe, …]}`` for every 0x06 Route ('cNNN').

    Each keyframe = ``{eye, look, time, fov, roll, mode}`` (see the layout comment above,
    verified across 14 zones). ``fov`` is raw — a **focal length** (default 350; client FOV =
    2·atan2(192, focal), larger = zoomed in), NOT an angle; ``roll`` is radians (≈0 normally);
    ``mode`` is the Route's ``SmoothingType`` EASING enum (0 Linear, 1 Decelerate, 2 Accelerate,
    3 Decel→mid→Accel, 4 S-curve), repeated on each kf. Coords are raw FFXI world space (same
    frame as zone placements)."""
    routes = {}
    for pos, tag, tc, size in _scene_sections(data):
        if tc != 0x06:  # Route
            continue
        body = data[pos + 16:pos + size]
        if len(body) < 32:
            continue
        count = struct.unpack_from("<I", body, 16)[0]
        if count <= 0 or count > 4096:
            continue
        mode = struct.unpack_from("<I", body, 20)[0]
        kfs = []
        off = 32
        for _ in range(count):
            if off + 48 > len(body):
                break
            ex, ey, ez, fov = struct.unpack_from("<4f", body, off)
            lx, ly, lz, roll = struct.unpack_from("<4f", body, off + 16)
            t = struct.unpack_from("<f", body, off + 32)[0]
            kfs.append({
                "eye": [ex, ey, ez],
                "look": [lx, ly, lz],
                "time": t,
                "fov": fov,
                "roll": roll,
                "mode": mode,
            })
            off += 48
        if kfs:
            routes[tag] = kfs
    return routes


# The scheduler/effect "shot" a cutscene task fires is a 0x07 EffectRoutine in the scene
# resource (named like the task's action tag, e.g. 'z00b'). Its command stream references
# the resources it plays by 4-char DatId; we bucket each ref by the LOCAL section type it
# points at — robust across the spell-effect vs scene-scheduler command-opcode dialects.
_ROUTINE_REF_KIND = {0x06: "camera", 0x05: "vfx", 0x2B: "anim", 0x3D: "sound", 0x07: "linked"}


def parse_effect_routines(data: bytes) -> dict:
    """Parse a scene/effect resource's 0x07 EffectRoutine sections → ``{tag: {...}}``.

    For each routine, walk its sec2 timed-command stream and bucket the resources it fires by
    the referenced section's type code: ``camera`` (0x06 Route / camera path), ``vfx`` (0x05
    ParticleGenerator), ``anim`` (0x2B SkeletonAnimation), ``sound`` (0x3D), ``linked`` (0x07
    sub-routine). ``total`` = the routine's totalDelay (frames). Refs that live in another DAT
    (e.g. an actor's skeleton anim) aren't typed locally, so only in-resource refs are bucketed.
    Layout per docs/fx/effect_system.md (EffectRoutine §3); best-effort + bounded."""
    secs = _scene_sections(data)
    by_name = {tag: tc for _, tag, tc, _ in secs}
    out = {}
    for off, tag, tc, size in secs:
        if tc != 0x07:
            continue
        body = data[off + 16:off + size]
        if len(body) < 0x20:
            continue
        try:
            _sec1, sec2, _sec3, total = struct.unpack_from("<4I", body, 0x10)
        except struct.error:
            continue
        kinds = {"camera": [], "vfx": [], "anim": [], "sound": [], "linked": []}
        p = sec2 - 16                       # sec offsets are section-relative; body starts at +16
        guard = 0
        cam_dur = 0
        while 0 <= p + 8 <= len(body) and guard < 128:
            guard += 1
            opc = body[p]
            n = struct.unpack_from("<H", body, p + 1)[0] & 0x1F   # numInputs (dwords) in this entry
            entry_len = max(1, n) * 4
            found_cam = False
            for q in range(p + 8, min(p + entry_len, len(body)) - 3, 4):
                cand = body[q:q + 4]
                if all(0x20 <= c < 0x7F for c in cand):
                    s = cand.decode("ascii")
                    kind = _ROUTINE_REF_KIND.get(by_name.get(s))
                    if kind and s != tag and s not in kinds[kind]:
                        kinds[kind].append(s)
                        if kind == "camera":
                            found_cam = True
            # The camera-load command carries the shot's play DURATION as a u16 @+6 (the client's
            # GetMaybeDuration = short × scheduler scale; xiclient CMoSchedulerTask). Capture the raw
            # value so the editor can pace each shot's move by its real authored length rather than a
            # flat nominal. delay @+4 == dur @+6 in retail camera commands.
            if found_cam and p + 8 <= len(body):
                cam_dur = struct.unpack_from("<H", body, p + 6)[0]
            if opc == 0x00:                 # end-of-section marker
                break
            p += entry_len
        kinds["total"] = int(total)
        kinds["camDur"] = int(cam_dur)
        out[tag] = kinds
    return out


# A 0x07 EffectRoutine's sec2 is a command stream; the ones we decode for cutscene playback
# (verified against real DATs via the xim EffectRoutineParser): 0x05 plays a skeleton clip,
# 0x27 follows a PointList path (entity motion). Command framing: op u8, combo u16
# (entry = (combo&0x1F) dwords), unk u8, then sec2 delay u16 + duration u16, then args; a
# 4-char DatId ref sits at byte +8 from the command start.
def _routine_sec2_commands(data: bytes, tag: str):
    """Walk the named 0x07 routine's sec2 → list of ``{op, delay, dur, ref, off}`` commands.
    ``ref`` is the 4-char DatId at +8 (clip for 0x05, PointList for 0x27), or None."""
    for off, t, tc, size in _scene_sections(data):
        if t != tag or tc != 0x07:
            continue
        body = data[off + 16:off + size]
        if len(body) < 0x20:
            return []
        try:
            _s1, sec2, _s3, _tot = struct.unpack_from("<4I", body, 0x10)
        except struct.error:
            return []
        cmds, p, guard = [], sec2 - 16, 0
        while 0 <= p + 8 <= len(body) and guard < 128:
            guard += 1
            op = body[p]
            n = struct.unpack_from("<H", body, p + 1)[0] & 0x1F
            entry_len = max(1, n) * 4
            delay = struct.unpack_from("<H", body, p + 4)[0] if p + 6 <= len(body) else 0
            dur = struct.unpack_from("<H", body, p + 6)[0] if p + 8 <= len(body) else 0
            ref = body[p + 8:p + 12] if p + 12 <= len(body) else b""
            ref_s = ref.decode("ascii") if len(ref) == 4 and all(0x20 <= c < 0x7F for c in ref) else None
            flags0 = struct.unpack_from("<I", body, p + 16)[0] if p + 20 <= len(body) else 0
            # SkeletonAnimation (0x05) loop/transition fields (xim EffectRoutineParser 0x05):
            # transIn u16 @+24, transOut u16 @+28, maxLoops u16 @+30 (0 = loop forever; N = play
            # N times then HOLD the last frame — the 0x2B itself clamps past its end, no loop flag).
            trans_in = struct.unpack_from("<H", body, p + 24)[0] if p + 26 <= len(body) else 0
            trans_out = struct.unpack_from("<H", body, p + 28)[0] if p + 30 <= len(body) else 0
            max_loops = struct.unpack_from("<H", body, p + 30)[0] if p + 32 <= len(body) else 0
            cmds.append({"op": op, "delay": delay, "dur": dur, "ref": ref_s, "off": p, "flags0": flags0,
                         "maxLoops": max_loops, "transIn": trans_in, "transOut": trans_out})
            if op == 0x00:
                break
            p += entry_len
        return cmds
    return []


def parse_pointlist(data: bytes, tag: str):
    """Parse a 0x3E PointList named ``tag`` → ``[[x, y, z], …]`` waypoints (raw FFXI world
    space). Body: ``u32 numPoints, 3×u32 zero, numPoints × (vec3 + f32=1.0)``."""
    for off, t, tc, size in _scene_sections(data):
        if t != tag or tc != 0x3E:
            continue
        body = data[off + 16:off + size]
        if len(body) < 16:
            return []
        count = struct.unpack_from("<I", body, 0)[0]
        if count <= 0 or count > 4096:
            return []
        pts, q = [], 16
        for _ in range(count):
            if q + 16 > len(body):
                break
            x, y, z, _w = struct.unpack_from("<4f", body, q)
            pts.append([x, y, z])
            q += 16
        return pts
    return []


def parse_routine_motion(data: bytes, tag: str):
    """If routine ``tag`` follows a path (sec2 op 0x27), return ``{"duration", "reversed",
    "points": [[x,y,z]…]}`` (the 0x3E PointList it references). ``None`` if it has no motion."""
    for c in _routine_sec2_commands(data, tag):
        if c["op"] == 0x27 and c["ref"]:
            pts = parse_pointlist(data, c["ref"])
            if not pts:
                continue
            reversed_ = not (c.get("flags0", 0) & 0x08)   # 0x08 clear ⇒ traverse path reversed
            return {"duration": c["dur"], "points": pts, "pointList": c["ref"], "reversed": reversed_}
    return None


def parse_routine_clip(data: bytes, tag: str):
    """The skeleton-clip DatId routine ``tag`` plays (sec2 op 0x05, ref at +8; may be a
    parameterised ``xxx?``), or ``None``. The clip is resolved against the actor's anim pool."""
    for c in _routine_sec2_commands(data, tag):
        if c["op"] == 0x05 and c["ref"]:
            return c["ref"]
    return None


def resolve_task_resource(opcode: int, raw_args: bytes, refs: list[int]):
    """Per-opcode scheduler-task resource resolution (verified by agent B against 26k tasks).

    Returns ``(kind, value)``: ``("scene_file", file_id)`` for 0x45 (tag = a section in that
    scene DAT), ``("player_main", file_id)`` for 0x62/0x7D (tag = 'main'), ``("actor_clip",
    tag)`` for 0x5B/0x66 + 0x2C/0x2D (tag = a clip/routine in the actor's OWN anim dirs — NOT
    a file id; the 30704 formula is WRONG for these), or ``("runtime", None)``."""
    if opcode in (0x2C, 0x2D):
        tag = raw_args[8:12].decode("ascii", "replace") if len(raw_args) >= 12 else None
        return ("actor_clip", tag)
    if len(raw_args) < 2:
        return ("runtime", None)
    sel = raw_args[0] | (raw_args[1] << 8)
    tag = raw_args[10:14].decode("ascii", "replace") if len(raw_args) >= 14 else None
    if not (sel & 0x8000) or (sel & 0x7FFF) >= len(refs):
        return ("runtime", None)
    p = int(refs[sel & 0x7FFF])
    if opcode == 0x45:
        return ("scene_file", 30704 + _datid_helper(p))
    if opcode in (0x62, 0x7D):
        return ("player_main", 5112 + p)
    if opcode in (0x5B, 0x66):
        return ("actor_clip", tag)
    return ("runtime", None)


# ---------------------------------------------------------------------------
# Cutscene actor MOTION resolution — event 0x5B "play named motion" → 0x2B skeleton clip.
#
# A 0x5B opcode carries a target entity, a 4-char motion tag (e.g. 'fg00'), and a
# work-selector that resolves (via the EVENT ACTOR RECORD's refs[]) to a per-record index.
# The motion PACKAGE DAT holding that named 0x07 routine sits at
#       file_id = anim_base + refs[sel]
# where ``anim_base`` is per-record (an NPC group's motion-resource origin: Iroha's Lower
# Jeuno cutscene = 66339, generic NPC groups = 59739, …). It is NOT a function of the model
# id — the SAME model id appears with different bases — so we recover it by CONSENSUS: build a
# ``{routine_name → [file_id]}`` index over the NPC motion band, then for the record's
# ``(tag, refs)`` pairs pick the base (``file - refs``) the most tags agree on. Rare/unique
# tags weigh more — a tag present in a single file pins the base exactly (verified: Iroha's
# 'hizc'/'fuk1' each live in one file, both ⇒ 66339). The matched routine's sec2 0x05 command
# then names the 0x2B clip to play (parameterised 'xxx?' → first prefix match in that file).
#
# Verified on Iroha's cutscene (event 0x3F): 9/10 tags resolve; the lone miss ('kam0', a
# standard kamae with a tiny refs) comes from the model's own inline clips, not the package.
# ---------------------------------------------------------------------------

# NPC motion-package file-id band: event-referenced 'mot_' DATs (0x07 routines + their 0x2B
# clips) live here. Wide enough for observed cutscenes; resolve auto-widens once on a miss.
MOTION_BAND = (50000, 76000)

_MOTION_INDEX: dict = {}   # (lo, hi) -> {routine_name: [file_id, …]}


def _clean_tag(b: bytes) -> str:
    """A 4-char motion tag stripped of NUL/non-printable padding."""
    return "".join(chr(c) for c in b if 0x20 <= c < 0x7F)


def _motion_index_path(lo: int, hi: int):
    from pathlib import Path
    return Path("exports") / ".cache" / f"motion_idx_{lo}_{hi}.json"


def build_motion_index(lo: int = None, hi: int = None, *, rebuild: bool = False) -> dict:
    """``{routine_name → [file_id]}`` over the NPC motion band (every 0x07 section), memo- and
    disk-cached. One-time build (~1 min); reused thereafter.

    NOTE: ``scan_file_ids`` COMPACTS its result (drops ids that don't resolve), so each entry's
    own ``file_id`` field must be used — never ``zip`` the result with the input range, which
    misaligns every entry after the first gap (that bug silently corrupts the whole index)."""
    import json
    from pathlib import Path
    lo = MOTION_BAND[0] if lo is None else lo
    hi = MOTION_BAND[1] if hi is None else hi
    key = (lo, hi)
    if not rebuild and key in _MOTION_INDEX:
        return _MOTION_INDEX[key]
    cp = _motion_index_path(lo, hi)
    if not rebuild and cp.is_file():
        try:
            idx = json.loads(cp.read_text())
            _MOTION_INDEX[key] = idx
            return idx
        except Exception:
            pass
    from xi.ftable.xi_core import scan_file_ids
    from xi.xi_config import FFXI_DIR, read_path_for
    idx: dict = {}
    for h in scan_file_ids(list(range(lo, hi))):
        fid = h["file_id"]
        try:
            d = read_path_for(Path(FFXI_DIR) / h["dat"]).read_bytes()
        except Exception:
            continue
        for _off, t, tc, _sz in _scene_sections(d):
            if tc == 0x07:
                idx.setdefault(t, []).append(fid)
    try:
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(idx))
    except Exception:
        pass
    _MOTION_INDEX[key] = idx
    return idx


def actor_motion_ops(actor) -> list:
    """``[(entity_id, tag, refs_value)]`` for every resolvable 0x5B motion across the actor
    record's events (work-selector → the record's own refs[])."""
    out = []
    for ev in actor.events:
        for o in ev.opcodes:
            if o.op != 0x5B or len(o.raw_args) < 14:
                continue
            sel = o.raw_args[0] | (o.raw_args[1] << 8)
            if not (sel & 0x8000):
                continue
            i = sel & 0x7FFF
            if i >= len(actor.refs):
                continue
            ent = int.from_bytes(o.raw_args[2:6], "little")
            tag = _clean_tag(o.raw_args[10:14])
            if tag:
                out.append((ent, tag, int(actor.refs[i])))
    return out


def derive_anim_base(tagrefs, index) -> Optional[int]:
    """Per-record motion base by rarity-weighted consensus: ``base = file_id - refs`` that the
    most tags agree on. A tag in ≤2 files weighs 5× (a one-file tag pins the base); ≤6 files
    2×; otherwise 1×. ``tagrefs`` = iterable of ``(tag, refs_value)``. ``None`` if no signal."""
    from collections import Counter
    votes = Counter()
    for tag, rv in tagrefs:
        files = index.get(tag)
        if not files:
            continue
        w = 5 if len(files) <= 2 else (2 if len(files) <= 6 else 1)
        for f in files:
            votes[f - rv] += w
    return votes.most_common(1)[0][0] if votes else None


def _match_clip(sections, ref: str) -> Optional[str]:
    """Resolve a (possibly parameterised ``'xxx?'``) clip ref to a concrete 0x2B section name
    present in ``sections`` (a :func:`_scene_sections` result). Exact match first, else the
    first 0x2B clip sharing the literal prefix before the ``'?'``."""
    clips = [t for _o, t, tc, _s in sections if tc == 0x2B]
    if ref in clips:
        return ref
    pref = ref.split("?", 1)[0]
    if pref:
        for c in clips:
            if c.startswith(pref):
                return c
    return None


def resolve_event_clips(actor, event=None, index=None) -> dict:
    """Resolve cutscene motions to concrete clips: ``{entity_id: {tag: {"file_id", "clip"}}}``.

    Uses the actor RECORD's consensus base (derived from all its 0x5B tags), then for each
    0x5B in ``event`` (or every event when ``None``) computes ``file = base + refs`` and reads
    the routine → 0x2B clip. Tags whose package lacks the routine (standard motions like
    'kam0') are skipped — those play from the model's own inline clips. ``index`` defaults to
    the cached motion index and auto-widens once if the base can't be found."""
    from pathlib import Path
    from xi.ftable.xi_core import scan_file_ids
    from xi.xi_config import FFXI_DIR, read_path_for
    if index is None:
        index = build_motion_index()
    all_tr = [(t, r) for _e, t, r in actor_motion_ops(actor)]
    base = derive_anim_base(all_tr, index)
    if base is None:  # one widen attempt for zones whose packages sit outside the default band
        lo, hi = MOTION_BAND[0] - 25000, MOTION_BAND[1] + 25000
        index = build_motion_index(lo, hi)
        base = derive_anim_base(all_tr, index)
        if base is None:
            return {}
    events = actor.events if event is None else [event]
    fcache: dict = {}
    out: dict = {}
    for ev in events:
        for o in ev.opcodes:
            if o.op != 0x5B or len(o.raw_args) < 14:
                continue
            sel = o.raw_args[0] | (o.raw_args[1] << 8)
            if not (sel & 0x8000):
                continue
            i = sel & 0x7FFF
            if i >= len(actor.refs):
                continue
            tag = _clean_tag(o.raw_args[10:14])
            ent = int.from_bytes(o.raw_args[2:6], "little")
            if not tag or tag in out.get(ent, {}):
                continue
            fid = base + int(actor.refs[i])
            if fid not in fcache:
                try:
                    hits = scan_file_ids([fid])
                    fcache[fid] = read_path_for(Path(FFXI_DIR) / hits[0]["dat"]).read_bytes() if hits else None
                except Exception:
                    fcache[fid] = None
            d = fcache[fid]
            if not d:
                continue
            anim_cmd = next((c for c in _routine_sec2_commands(d, tag) if c["op"] == 0x05 and c["ref"]), None)
            if not anim_cmd:
                continue
            clip = _match_clip(_scene_sections(d), anim_cmd["ref"])
            if clip:
                # loops: 0 = loop forever (held expressions like 'fg00'); N≥1 = play N times then
                # hold the last frame (one-shot gestures like 'hiz0'/'fuk1'). Drives the viewer's
                # AnimationAction loop mode so gestures stop instead of looping endlessly.
                # layer: 'face' if the clip only drives high/face joints (≥20, no root/spine) — the
                # client plays a facial expression ON TOP of the body motion; the viewer layers it
                # additively so e.g. 'fg00' (face) + 'hiz0' (body) play together, not one-or-other.
                out.setdefault(ent, {})[tag] = {"file_id": fid, "clip": clip,
                                                "loops": anim_cmd["maxLoops"],
                                                "transIn": anim_cmd["transIn"],
                                                "layer": _clip_layer(d, clip)}
    return out


def _clip_layer(data: bytes, clip_name: str) -> str:
    """``'face'`` if the 0x2B clip animates only high/face joints (min joint index ≥ 20, i.e. it
    leaves the root/spine alone — a facial expression to layer on top of a body motion); else
    ``'body'``. Best-effort; defaults to ``'body'`` if the clip can't be parsed."""
    try:
        from xi.entity.anim.xi_export import parse_sections, parse_animation, SECTION_TYPE_SKELETON_ANIMATION
        sec = next((s for s in parse_sections(data)
                    if s.type_code == SECTION_TYPE_SKELETON_ANIMATION and s.name == clip_name), None)
        if sec is not None:
            joints = parse_animation(data, sec).tracks.keys()
            if joints and min(joints) >= 20:
                return "face"
    except Exception:
        pass
    return "body"

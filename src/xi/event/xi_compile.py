"""Cutscene compiler — JSON (schema: ``xi.cutscene.v1``) → FFXI event bytecode.

Lowers a high-level cutscene definition (see :mod:`schema/event_cutscene.json`) into
the byte-exact opcode stream + growing dialog/scene DAT bodies. Reuses the byte-exact
event-DAT writer in :mod:`xi.event.xi_event` (``parse_raw_actors`` / ``build_event_dat``)
and the dialog-DAT appender in :mod:`xi.event.xi_author` (``append_dialog_lines``).

Design in one paragraph
-----------------------

Every scene beat lowers to a small fixed opcode template. Steps that need a
runtime value (a message id, a zone id, a position component, a FourCC) grow the
actor's ``references[]`` and emit a 2-byte work-selector ``0x8000 | refIndex`` in
place of the value, matching the retail work-selector→references[] indirection
(see :doc:`docs/events/format.md#operand-references`). Compilation runs in two
passes: pass 1 walks the steps sizing each opcode + allocating ref slots +
recording label offsets; pass 2 emits real bytes, resolving forward jumps.
The output actor block is spliced onto the existing zone Event DAT — the rest of
the file is left byte-identical (proven by the ``build_event_dat`` round-trip).

Reference profile: Maat event 93 (see :doc:`docs/events/maat_93_study.md`) — every
opcode template below appears in that retail cutscene, so if the compiler emits
them the client will run them. The ``0x38 CliEventModeLocal`` default is 0x2003
(Ailevia; high byte → CliEventModeLocal 0x20 same as 0x0013 — see
:doc:`docs/events/event_mode_bits.md`).

Menus, branches, jumps and the server round-trip (2026-09-03)
---------------------------------------------------------------

Byte template: Ru'Lude Gardens Nomad Moogle event 10196 (job-points intro). A prompt is
one dialog string ``question\noption1\noption2`` shown by ``0x24 dialog_menu`` and
answered by ``0x25 wait_select``, which stores ``selection - 1`` (254 when escaped) in
``Work_Zone[0]``. ``0x02 if`` compares that register with a ``references[]`` constant and
jumps to an ABSOLUTE scene offset; ``0x01 set_exec`` is the unconditional jump. What the
server receives as ``option`` (packet 0x05B ``EndPara``) is ``Work_Zone[1]``, so a branch
stores its outcome there with ``0x03 get_store`` before ``0x21 end`` or before
``0x43 notify_server`` (``43 00`` sends the update, ``43 01`` waits for the server's
reply). Labels are recorded during emission (``_Ctx.mark_label``) and forward jumps are
backpatched once the whole event is emitted (``_resolve_fixups``); offsets are absolute
within the actor's scene, so ``ctx.base_offset`` is the splice point of the new event.

Not yet implemented
-------------------

Nothing in the step table is stubbed any more (``load_zone`` is emitted but untested).

Standalone anims (a dialog-less motion) ARE supported — see :func:`_step_anim` /
:func:`_emit_gesture`: curated humanoid gestures ride ``0x5B sched_ext`` (bank 60),
and an actor's OWN motions fire via ``0x2C SetAction`` with one of the model's 0x07
scheduler-routine tags (retail's own pattern; see ``normalize_cutscene_anim_tags``).
Per-actor 0x66 Tpc motion PACKAGES (Cornelia's kka0 = package 12) remain future work.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from xi.common.xi_section import encode_section_meta
from xi.dialog import xi_dialog
from xi.event import xi_author, xi_event as core, xi_shop, xi_typed


# ---------------------------------------------------------------------------
# Opcodes we emit — kept as named constants so the compiler doc reads clearly.
# ---------------------------------------------------------------------------

OP_LOCK_PLAYER    = 0x20    # arg: u8 flag (1 = lock, 0 = release)
OP_END            = 0x21
OP_WAIT_DISMISS   = 0x23
OP_MENU           = 0x24    # 7B — dialog menu prompt (msgSel + 2 flag sels)
OP_WAIT_SELECT    = 0x25    # 1B — wait for menu selection → Work_Zone[0] = index-1 (254 = escaped)
OP_SET_EXEC       = 0x01    # 3B — op + u16 ABSOLUTE scene offset (goto; retail uses it to skip to `end`)
OP_IF             = 0x02    # 8B — op + val1 sel + val2 sel + u8 kind + u16 absolute target
OP_STORE          = 0x03    # 5B — op + dst sel + src sel   (dst = src; retail `get_store`)
OP_NOTIFY_SERVER  = 0x43    # 2B — op + u8: 0 = send packet 0x05B (mode 1, EndPara = Work_Zone[1]); 1 = wait for the reply
WORK_ZONE_BASE    = 0x1000  # work-selector for Work_Zone[i] is 0x1000 + i (XiEvent::getworkofs)
WORK_MENU_RESULT  = WORK_ZONE_BASE + 0   # where 0x25 leaves the choice
WORK_EVENT_RESULT = WORK_ZONE_BASE + 1   # what 0x05B carries as EndPara → Lua `option`
MENU_CANCELLED    = 254                  # Work_Zone[0] after Escape / forced close
IF_KIND_JUMP_IF_EQUAL = 0x01             # 0x02 kind 1: val1 == val2 → jump, else fall through (+8)
OP_ITEM_INFO      = 0x93    # 3B — op + sel: item id → open the item description window; 0 → close it
OP_INPUT          = 0x71    # var — sub 0x10/0x12 open the number window, 0x11/0x13 wait + store; 0x00/0x01 text
OP_BITS_SET       = 0x40    # 9B — op + lo + hi + dst + src selectors: dst bits[lo..hi] = src << lo  (SETBITWORK)
OP_BITS_GET       = 0x41    # 9B — op + lo + hi + src + dst selectors: dst = bits[lo..hi] of src >> lo (GETBITWORK)
OP_CALL           = 0x1A    # 3B — op + absolute offset: call a subroutine (returns on 0x1B)
OP_STORE_ZERO     = 0x06    # 3B — op + dst selector
OP_STORE_ONE      = 0x05    # 3B — op + dst selector
WORK_PARAM_BASE   = WORK_ZONE_BASE + 2   # server parameter n (startEvent / updateEvent) is Work_Zone[2 + n]
OP_CANCEL_SET     = 0x42
OP_CAMERA_CONTROL = 0x46    # arg: u8 mode  (1 = client cinematic, 0 = restore)
OP_EVENT_MODE     = 0x38    # arg: 2B selector → refs[] → CliEventModeLocal u16
OP_LOOK_AT        = 0x4A    # 9B — 4B entityA + 4B entityB
OP_LOOK_TALK      = 0x1E    # 5B — 4B target (event entity looks + mouth-move)
OP_RENDER_FLAG    = 0x4E    # 6B — op + flag(u8) + entity(u32); flag 0=show 1=hide
OP_EVENT_HIDE     = 0x22    # 2B — op + u8 flag; SetEventHideFlag on event entity
OP_LOAD_WAIT      = 0x80    # 5B — op + entity(u32); yield until entity action/model is ready
OP_START_TASK     = 0x45    # 17B — see start_task layout
OP_WAIT_SCHED     = 0x55    # 15B — mirror of start_task minus the tag+durSel
OP_SCHED_EXT      = 0x5B    # 15B — extended schedule: selA + entA + entB + 4B tag (like 0x45 but no selD)
OP_SCHED_EXT2     = 0x66    # 15B — same layout as 0x5B but LOADS a per-actor motion package
                            #        first (ReadTpcEventMotionRes(actor, sel)); the selector is a
                            #        NON-ZERO per-actor package id in retail (Cornelia 12,
                            #        Ru'Lude 303E 63) — sel 0 = the DEFAULT humanoid talk package
                            #        (tlk0/thk1…), NOT "whatever is loaded". We no longer emit it.
OP_SET_ACTION     = 0x2C    # 13B — op + entA(u32) + entB(u32) + 4B action tag. XiEvent
                            #        CodeSCHEDULOR → SetAction: fires one of the actor's OWN
                            #        RESIDENT 0x07 scheduler routines (no resource load, no wait).
                            #        THE retail opcode for an NPC/mob's own motions (Qufim ev63
                            #        'ids0'; dead/corp/damg/clp0/sit2 across all surveyed zones).
OP_ACTOR_UIFLAG   = 0x94    # 6B — op + flag(u8) + entity(u32): Render.Flags3 bit 0x20000.
                            #      Retail stamps `94 01` on every staged cutscene NPC right
                            #      after its 4E-show (SSandy ev0, Qufim ev63) — believed to
                            #      suppress the actor's overhead name/UI for the event.

# FALLBACK inventory of the shared humanoid gesture bank (anim_bank 60 → file 32164;
# mirrors the bridge's _CUTSCENE_GESTURES). Bank tags are emitted via 0x5B (which loads
# the bank onto the entity); any other tag is treated as one of the actor's OWN scheduler
# routines and fired via 0x2C SetAction.
# ★ Used ONLY when the caller supplies no ``bank_tags`` (the DAT-less CLI path): the
# bridge parses the real bank DAT (`_gesture_bank_tags`) and passes its actual 0x07
# routine inventory, so dispatch is ground truth, not this hand-curated mirror (which
# drifts — e.g. the bank ships 'han0' but this set omits it).
# ★ Per-actor precedence: when the actor's model OWNS a routine with one of these names
# (a custom `anim schedule add` clip named 'tlk0' on a monster rig), the own routine wins
# and fires via 0x2C — the 0x5B bank would force humanoid bones onto the wrong skeleton.
# Verified 2026-07-21 by parsing bank 60's actual 0x07 sections — the previous
# retail-frequency-harvested list was wrong on 13 of 20 tags (aww0/joy0/shk0/… live in
# OTHER bank files retail selects per event, not in 32164; emitting them against bank 60
# no-ops) and missed 8 real routines (han0/han1/ika0/ika1/ski0/tlb0/tlb1/yor0).
# Which 0x5B gesture bank fits which skeleton (survey of every retail 0x5B across the zone
# DATs joined with LandSandBoat's npcs.yaml looks, 2026-09-03). Bank 60 (file 32164) is only
# ever loaded onto FIXED-MODEL humanoid NPCs (the models below); player-skeleton NPCs
# (`npcLook.type: equipped`) get a bank per race. Each bank listed here was checked to carry
# tlk0/tlk1/thk1/thk2/pas0 (its 0x07 routines), so the default talk/think gestures play.
RACE_GESTURE_BANKS = {
    1: 80,    # Hume male    (aww0/1, e000/1, pas0, thk0-2, tlk0/1)
    2: 10,    # Hume female  (cer0, far0/1, pas0, thk1/2, tlk0/1)
    3: 297,   # Elvaan male  (pas0, poi1/2, thk1/2, tlk0/1)
    4: 75,    # Elvaan female(pas0, thk0-2, tlk0/1)
    5: 337,   # Tarutaru male   (29 routines incl. pas0, thk1/2, tlk0/1)
    6: 337,   # Tarutaru female (same skeleton family)
    7: 357,   # Mithra       (pas0, thk0-2, tlk0-2)
    8: 377,   # Galka        (ang0, dis0, kud0, pas0, poi0, talk, thk1/2, tlk0/1)
}
# Fixed models retail animates with bank 60 (every 0x5B bank-60 use in the zone DATs; counts
# led by 855, 90, 153 = Maat). Other fixed models get no shared gesture (see _gesture_fits).
BANK60_MODELS = frozenset({90, 92, 93, 94, 95, 96, 97, 100, 126, 153, 848, 849, 854, 855, 873, 1423, 1454, 1998})

_GESTURE_TAGS = frozenset({
    "ann0", "ann1", "han0", "han1", "ika0", "ika1", "pas0", "ski0",
    "thk1", "thk2", "tlb0", "tlb1", "tlk0", "tlk1", "yor0",
})
OP_STOP_ACTION    = 0x5E    # 5B — 4B tag; stop current entity action + set to <tag>
# Sentinel an Anim keyframe stores (matches IDLE_STOP in cutscene-author.js) meaning
# "stop the current action and drop back to idle" → compiled to 0x5E (owner) / 0x6B (cast).
IDLE_STOP_TAG     = "@idle"
OP_MUSIC          = 0x5C    # var — slot,song selector
OP_MUSIC_VOLUME   = 0x5D    # 5B — vol + ease selectors
OP_PRINT_MSG      = 0x1D    # 3B — 2B message selector, speaker = event entity
OP_PRINT_MSG2     = 0x2B    # 7B — 4B speaker + 2B message selector
OP_NARRATE        = 0x48    # 3B — 2B message selector, no speaker
OP_WAIT_TIME      = 0x1C    # 3B — 2B selector → frames to wait
OP_HIDE_HUD       = 0x67    # 5B — hide the entire HUD (compass/target/menus) for the cutscene
OP_SHOW_HUD       = 0x68    # 1B — unhide the HUD
OP_CALIBRATE_POS  = 0xBA    # 13B — op + entity(u32) + 4×(2B selector)

REF_FLAG = xi_author.REF_FLAG          # 0x8000 — high bit of a work-selector
MAX_REF_IDX = xi_author.MAX_REF_INDEX  # 0x7FFF
MAX_SCENE_OFFSET = xi_author.MAX_SCENE_OFFSET

# Local-player / event-entity magic ids used all over the cutscene bytecode.
# Party/alliance ids match GetActorIndex / dump_event.py / xi_event._ACTOR_MAGIC
# (NOT the contiguous 0x7FFFFFF1..F range — that collides with local-player aliases).
ACTOR_MAGIC = {
    "player":       0x7FFFFFF0,  # also 0x7FFFFFC0 / 0x7FFFFFF9
    "event_entity": 0x7FFFFFF8,
    "party_1":      0x7FFFFFC1, "party_2": 0x7FFFFFC2, "party_3": 0x7FFFFFC3,
    "party_4":      0x7FFFFFC4, "party_5": 0x7FFFFFC5,
    "alliance_10":  0x7FFFFFC6, "alliance_11": 0x7FFFFFC7, "alliance_12": 0x7FFFFFC8,
    "alliance_13":  0x7FFFFFC9, "alliance_14": 0x7FFFFFCA, "alliance_15": 0x7FFFFFCB,
    "alliance_20":  0x7FFFFFCC, "alliance_21": 0x7FFFFFCD, "alliance_22": 0x7FFFFFCE,
    "alliance_23":  0x7FFFFFCF, "alliance_24": 0x7FFFFFD0, "alliance_25": 0x7FFFFFD1,
}

# 0x38 CliEventModeLocal — see docs/events/event_mode_bits.md.
#
# ★ The retail 0x38 handler is `CliEventModeLocal = HIBYTE(val) | 0x20` (XiEvents
#   0x0038.md + UE5 EventSession.cpp:914 + confirmed by the XI server author).
#   It reads ONLY the HIGH byte of the operand; the low byte is discarded. So
#   0x2003 (Ailevia's cinematic tour) and 0x0003 (Balasiel) BOTH resolve to
#   CliEventModeLocal = 0x20 — they are identical at the client. Almost all retail
#   values collapse to 0x20.
#
# We emit 0x2003 to exactly match Ailevia's proven cinematic value. NOTE: the
# 0x38 value is NOT what locks the player — that comes from the server starting
# the event as a CUTSCENE (player:startCutscene, which sets FreezePlayerMovement
# / CUTSCENE_ONLY status server-side). See the Lua stub.
EVENT_MODE_DEFAULT = 0x2003
EVENT_MODE_RESET   = 0x0000     # emitted implicitly at scene end

# Shared retail scene resource holding fdi1 / fdo1 fade routines — same value every
# retail cutscene uses. Resolves to scene DAT 30904 (ROM/62/110.DAT).
FADE_SCENE_RESOURCE = 200

# Standard retail wait between dialog lines / after epilogue (verified Balasiel 627 +
# 625 + 626 + 630 all use 60 frames between lines, 30 before end).
DEFAULT_LINE_GAP    = 60
DEFAULT_OUTRO_GAP   = 30

# The animation tags used by the retail dialog-line pattern.
TAG_TALK = b"tlk0"
TAG_IDLE = b"idl0"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class CutsceneCompileError(Exception):
    """Raised when a JSON cutscene definition is invalid or can't be lowered."""


# ---------------------------------------------------------------------------
# Compile context — one instance per compile() call.
# ---------------------------------------------------------------------------

@dataclass
class CompileResult:
    event_id: int
    event_dat: bytes                        # rebuilt event DAT bytes (write to disk)
    dialog_dat: bytes                       # rebuilt dialog DAT bytes
    scene_dat: Optional[bytes] = None       # rebuilt scene DAT (None until camera writer ships)
    refs_used: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    lua_stub: str = ""


@dataclass
class _Ctx:
    """Mutable compile state carried through both passes.

    * ``refs`` — the actor's ``references[]`` we grow; every runtime value passes
      through ``add_ref`` which returns a 2-byte selector.
    * ``code`` — the bytecode buffer we emit into.
    * ``labels`` — pass-1 records label→scene-offset for pass-2 jump resolution.
    * ``cast`` — cast-id → resolved entity id (u32), computed at compile start.
    * ``dialog_ids`` — line-id → message id assigned by :func:`append_dialog_lines`.
    * ``shots`` — shot-id → FourCC tag (for the ``camera`` step to look up).
    * ``event_mode`` — the 0x38 value we'll emit in the prologue (schema override or default).
    * ``owner_actor`` — cast id that owns this event (its actor block gets the new event id).
    """
    refs: list[int]
    code: bytearray = field(default_factory=bytearray)
    labels: dict[str, int] = field(default_factory=dict)
    cast: dict[str, int] = field(default_factory=dict)
    cast_meta: dict[str, dict] = field(default_factory=dict)   # per-cast pos/dir/etc
    dialog_ids: dict[str, int] = field(default_factory=dict)
    shots: dict[str, str] = field(default_factory=dict)         # shot id → FourCC
    scene_res_selector: int = 0                                 # selector for the FADE scene DAT ref
    camera_res_selector: int = 0                                # selector for the CAMERA scene DAT ref
    event_mode: int = EVENT_MODE_DEFAULT
    owner_actor: str = ""
    talk_anim: str = "tlk0"                                     # gesture while a line is spoken
    idle_anim: str = "idl0"                                     # gesture between lines / at rest
    anim_bank: int = 60                                         # 0x5B selector → shared gesture bank (60→file 32164)
    face_player: bool = True                                    # owner auto-faces the player at cutscene start
    cinematic: bool = True
    pending_fade_in: str = ""                                   # deferred prologue fade-in tag (fires AFTER the opening camera shot)
    warnings: list[str] = field(default_factory=list)
    base_offset: int = 0                                        # scene offset where this event's code starts (jumps are absolute)
    dialog_out: bytes = b""                                     # dialog DAT being grown; steps may append (shop)
    shop_stubs: list = field(default_factory=list)              # (shop step, event id) for the Lua stub
    trailers: list = field(default_factory=list)                # callables emitting code/data AFTER the final `end`
    ffxi_dir: Optional[Path] = None                             # game folder, for templates cloned from retail DATs
    fixups: list[tuple[int, str]] = field(default_factory=list) # (code position of a u16 target, label) to backpatch

    # --- labels / jumps --------------------------------------------------------

    def here(self) -> int:
        """Absolute scene offset of the next byte to be emitted."""
        return self.base_offset + len(self.code)

    def mark_label(self, name: str) -> None:
        if name in self.labels:
            raise CutsceneCompileError(f"duplicate step label: {name!r}")
        self.labels[name] = self.here()

    def emit_target(self, label: str) -> None:
        """Emit a u16 jump target for ``label`` — resolved now if already seen, else
        recorded for :func:`_resolve_fixups` after the whole event is emitted."""
        off = self.labels.get(label)
        if off is None:
            self.fixups.append((len(self.code), label))
            self.code += b"\x00\x00"
        else:
            self.code += struct.pack("<H", off & 0xFFFF)

    # --- ref-slot allocation --------------------------------------------------

    def add_ref(self, value: int) -> int:
        """Append ``value`` (as u32) to ``references[]`` if it's not already there
        and return the 2-byte work-selector (``0x8000 | idx``). Existing values
        are reused so we don't waste slots — matches retail behavior."""
        v = value & 0xFFFFFFFF
        try:
            idx = self.refs.index(v)
        except ValueError:
            idx = len(self.refs)
            if idx > MAX_REF_IDX:
                raise CutsceneCompileError(
                    f"actor references[] full — {idx} > {MAX_REF_IDX}. Split the cutscene.")
            self.refs.append(v)
        return REF_FLAG | idx

    def add_signed_scaled(self, value: float, scale: int = 1000) -> int:
        """Encode a float position component as ``int(value * scale)`` into refs[].
        Used for 0xBA X/Z/Y (scale 1000). Heading uses a separate 4096-units/turn
        path in ``_emit_place`` — do not use this helper for dir."""
        n = int(round(value * scale))
        if n < 0:
            n = (1 << 32) + n
        return self.add_ref(n)

    # --- cast resolution ------------------------------------------------------

    def entity_id(self, cast_id: str) -> int:
        if cast_id not in self.cast:
            raise CutsceneCompileError(f"unknown cast id: {cast_id!r}")
        return self.cast[cast_id]


# ---------------------------------------------------------------------------
# Entity / cast resolution
# ---------------------------------------------------------------------------

def _resolve_entity(spec) -> int:
    """Turn a schema ``entity`` (int, hex string, or symbolic alias) into a u32."""
    if isinstance(spec, int):
        return spec & 0xFFFFFFFF
    if isinstance(spec, str):
        s = spec.strip()
        if s in ACTOR_MAGIC:
            return ACTOR_MAGIC[s]
        if s.lower().startswith("0x"):
            return int(s, 16) & 0xFFFFFFFF
        if s.isdigit():
            return int(s) & 0xFFFFFFFF
    raise CutsceneCompileError(f"can't resolve entity spec: {spec!r}")


def _build_cast(cutscene: dict, ctx: _Ctx) -> None:
    cast_source = cutscene.get("cast")
    if isinstance(cast_source, str):
        raise CutsceneCompileError(
            "cast: path references not yet supported by compile() — inline the cast dict "
            "or use dats-build's resource resolution before calling the compiler.")
    if not isinstance(cast_source, dict) or "cast" not in cast_source:
        raise CutsceneCompileError("cutscene.cast must be an inline cutscene_npc object")
    for entry in cast_source["cast"]:
        cid = entry["id"]
        ctx.cast[cid] = _resolve_entity(entry["entity"])
        ctx.cast_meta[cid] = entry


# ---------------------------------------------------------------------------
# Emit primitives — each writes an opcode into ctx.code.
# ---------------------------------------------------------------------------

def _emit_u8(ctx: _Ctx, b: int) -> None:
    ctx.code.append(b & 0xFF)


def _emit_u16(ctx: _Ctx, v: int) -> None:
    ctx.code += struct.pack("<H", v & 0xFFFF)


def _emit_u32(ctx: _Ctx, v: int) -> None:
    ctx.code += struct.pack("<I", v & 0xFFFFFFFF)


def _emit_start_task(ctx: _Ctx, entity_a: int, entity_b: int, tag: str, dur_selector: int,
                     scene_sel: int | None = None) -> None:
    """0x45 start_task — 17 bytes total. See maat_93_study.md for the layout table.

    ``scene_sel`` selects WHICH scene resource holds the ``tag`` routine — fades use
    the shared fade scene (``ctx.scene_res_selector``, the default), camera shots use
    the cutscene's own camera scene (``ctx.camera_res_selector``)."""
    if len(tag) != 4 or not all(0x20 <= ord(c) < 0x7F for c in tag):
        raise CutsceneCompileError(f"start_task tag must be 4 printable ASCII chars: {tag!r}")
    _emit_u8(ctx, OP_START_TASK)
    _emit_u16(ctx, ctx.scene_res_selector if scene_sel is None else scene_sel)
    _emit_u32(ctx, entity_a)
    _emit_u32(ctx, entity_b)
    ctx.code += tag.encode("ascii")           # FourCC — little-endian byte order == ascii order
    _emit_u16(ctx, dur_selector)


def _emit_wait_sched(ctx: _Ctx, entity_a: int, entity_b: int, tag: str,
                     scene_sel: int | None = None) -> None:
    """0x55 wait_for_scheduler — 15 bytes, mirrors start_task without dur selector."""
    _emit_u8(ctx, OP_WAIT_SCHED)
    _emit_u16(ctx, ctx.scene_res_selector if scene_sel is None else scene_sel)
    _emit_u32(ctx, entity_a)
    _emit_u32(ctx, entity_b)
    ctx.code += tag.encode("ascii")


def _emit_sched_ext(ctx: _Ctx, entity_a: int, entity_b: int, tag_bytes: bytes) -> None:
    """0x5B sched_ext — 15 bytes — start a scheduled action on an entity by tag.

    Retail pattern (Balasiel 627): ``5B <selA=refs[7]=30> <entA> <entB> <4B tag>``.
    Verified: fires talk / gesture / etc. animations on the target entity.
    """
    assert len(tag_bytes) == 4
    _emit_u8(ctx, OP_SCHED_EXT)
    _emit_u16(ctx, ctx.add_ref(30))       # duration selector (retail uses 30 uniformly)
    _emit_u32(ctx, entity_a)
    _emit_u32(ctx, entity_b)
    ctx.code += tag_bytes


def _emit_stop_action(ctx: _Ctx, tag_bytes: bytes) -> None:
    """0x5E stop_action — 5 bytes — stop the EVENT ENTITY's action, set to <tag>."""
    assert len(tag_bytes) == 4
    _emit_u8(ctx, OP_STOP_ACTION)
    ctx.code += tag_bytes


# Gesture pairs in the shared bank (survey of every retail bank-60 sequence, 2026-09-03: the
# next gesture retail loads on the same entity). thk1 -> thk2 2593x, ann0 -> ann1 69x,
# han0 -> han1 94x, ika0 -> ika1 42x, tlb0 -> tlb1 127x, yor0 -> ski0 25x: the first of each
# pair ENTERS a held pose, the second leaves it. Loading another gesture over a held pose
# snaps the model (seen in game 2026-09-03), so the closer is played first.
GESTURE_CLOSERS = {"thk1": "thk2", "ann0": "ann1", "han0": "han1", "ika0": "ika1", "tlb0": "tlb1", "yor0": "ski0"}
# Consecutive talk lines alternate tlk0 -> tlk1 (2728x) -> tlk0 (172x) in retail.
GESTURE_ALTERNATES = {"tlk0": "tlk1", "tlk1": "tlk0"}


def _close_open_gesture(ctx: _Ctx, next_tag: Optional[str] = None) -> None:
    """If the owner is holding a paired pose, play its closer and wait for it (unless the next
    gesture IS that closer)."""
    open_tag = getattr(ctx, "open_gesture", None)
    if not open_tag:
        return
    closer = GESTURE_CLOSERS.get(open_tag)
    ctx.open_gesture = None
    if not closer or closer == next_tag:
        return
    ent = getattr(ctx, "open_gesture_ent", ACTOR_MAGIC["event_entity"])
    bank = getattr(ctx, "bank_tags", None) or _GESTURE_TAGS
    if closer in bank:
        _emit_u8(ctx, OP_SCHED_EXT)
        _emit_u16(ctx, ctx.add_ref(ctx.anim_bank)); _emit_u32(ctx, ent); _emit_u32(ctx, ent)
        ctx.code += closer.encode("ascii")
        _emit_wait_task(ctx, ent, closer.encode("ascii"))


OP_WAIT_TASK = 0x53            # 13B — op + entA(4) + entB(4) + 4B tag: wait for that scheduled routine to finish


def _emit_wait_task(ctx: _Ctx, ent: int, tag_bytes: bytes) -> None:
    """``53 wait_task <ent> <ent> <tag>`` (CodeWAITSCHEDULOR): block until the entity's
    scheduled routine ``tag`` has finished — retail's line ending after a talk gesture."""
    assert len(tag_bytes) == 4
    _emit_u8(ctx, OP_WAIT_TASK); _emit_u32(ctx, ent); _emit_u32(ctx, ent)
    ctx.code += tag_bytes


OP_STOP_ACTION_ACTOR = 0x6B    # 9B — op + action(4) + target(4). Stop a TARGET actor → idle.


def _emit_stop_action_actor(ctx: _Ctx, tag_bytes: bytes, entity: int) -> None:
    """0x6B stop_action on a SPECIFIC actor — 9 bytes — op + action(4) + target(4).
    0x5E's sibling for a non-owner cast NPC (0x5E only ever stops the event entity;
    docs/ue5/event-system.md 0x6B = STOPACT with an explicit ``target=resolve(u32@+5)``)."""
    assert len(tag_bytes) == 4
    _emit_u8(ctx, OP_STOP_ACTION_ACTOR)
    ctx.code += tag_bytes            # action FourCC — the idle to reset to — @ +1
    _emit_u32(ctx, entity)           # target actor @ +5


def _emit_wait_time(ctx: _Ctx, frames: int) -> None:
    """0x1C wait_time — 3 bytes — pause execution for `frames` frames (30fps)."""
    if frames <= 0:
        return
    sel = ctx.add_ref(frames)
    _emit_u8(ctx, OP_WAIT_TIME)
    _emit_u16(ctx, sel)


# ---------------------------------------------------------------------------
# Prologue / epilogue emitters
# ---------------------------------------------------------------------------

FADE_HOLD_FRAMES = 60      # fixed wait after a bookend fade (retail Ambrotien uses 60 = 2s)


def _emit_fade(ctx: _Ctx, tag: str, hold: int = FADE_HOLD_FRAMES) -> None:
    """Fire a fade task on the EVENT ENTITY, then hold with a fixed wait_time.

    ★ Reverse-engineered from retail Ambrotien event 2000 (the reference that
    keeps the HUD + prompts): fades fire on ``event_entity`` (0x7FFFFFF8), NOT
    the player, and are followed by a FIXED ``0x1C wait_time`` — not ``0x55``
    (0x55 in retail waits on the CAMERA scene task, which we don't emit). Using
    the player entity + 0x55 was making the fade behave wrong."""
    ent = ACTOR_MAGIC["event_entity"]
    dur = ctx.add_ref(0)                       # retail passes 0 in the duration slot
    _emit_start_task(ctx, ent, ent, tag, dur)  # 0x45
    if hold:
        _emit_wait_time(ctx, hold)             # 0x1C fixed hold


def _gesture_fits(ctx: _Ctx, is_owner: bool, tag: str, cast_id: Optional[str]) -> bool:
    """The shared gesture bank (0x5B) force-loads HUMANOID skeleton motion onto the
    entity. On a fixed-model NPC (``npcLook.type == "standard"``: moogles, beasts,
    furniture) that freezes the model mid-pose, so the bank is only used for looks that
    carry a race (``equipped``) or for the actor's own motions. Warns once per event."""
    own = bool(cast_id) and tag in getattr(ctx, "own_motions", {}).get(cast_id, ())
    bank = getattr(ctx, "bank_tags", None) or _GESTURE_TAGS
    if own or tag not in bank or not is_owner or getattr(ctx, "owner_look_type", None) != "standard":
        return True                       # own routines (0x2C) never touch the skeleton
    if getattr(ctx, "owner_model", None) in BANK60_MODELS:
        return True                       # retail loads bank 60 onto this model family
    note = (f"owner look is fixed model {getattr(ctx, 'owner_model', '?')} which retail never animates "
            "with the shared gesture bank (it fits the humanoid NPC skeletons only: models "
            "90..100, 126, 153, 848..855, 873, 1423, 1454, 1998); gestures skipped. Give the NPC "
            "an `equipped` look (per-race bank chosen automatically) or its own motion tags")
    if note not in ctx.warnings:
        ctx.warnings.append(note)
    return False


def _emit_gesture(ctx: _Ctx, ent: int, tag: str, cast_id: Optional[str] = None) -> bool:
    """Schedule a gesture (talk/think/etc.) on ``ent`` via ``0x5B sched_ext``.

    ★ We use 0x5B — NOT 0x66 — because they load motion DIFFERENTLY (from the
    decompiled handler): 0x5B calls ``ReadEventMotionRes(entity, sel + 32104)``,
    which EXPLICITLY LOADS a shared gesture bank onto the entity before playing;
    0x66 calls ``ReadTpcEventMotionRes(entity, sel)``, which uses the entity's
    OWN already-loaded motion table. NPCs like Maat only have idl0/wlk0/run0
    loaded — so 0x66 (Ambrotien's opcode) finds nothing to play and the gesture
    silently no-ops. 0x5B with ``ctx.anim_bank`` (default 60 → file 32164, the
    standard humanoid bank holding tlk0/tlk1/thk1/thk2/ann0/han0/pas0/…) is what
    Maat's OWN retail cutscene (event 74) uses, and works on any humanoid NPC.

    Non-blocking: the handler self-waits on the resource LOAD (RetFlag=0 until
    read-complete) but not on playback, so the gesture animates while the
    following ``0x1D`` dialogue shows — no ``0x53 wait_task`` needed.

    ★ Tag dispatch: curated humanoid gestures (``_GESTURE_TAGS``) ride 0x5B +
    bank 60 as above. Any OTHER tag is fired via ``0x2C SetAction`` — the retail
    opcode for an actor's OWN motions. 0x2C fires an action FourCC against the
    entity's RESIDENT resources, i.e. one of its model's 0x07 scheduler routines
    (``ati0``/``atk0``/``cast``/``dead``/``ids0`` …): retail Qufim ev63 plays
    'ids0' this way, and a 9-zone survey found ONLY routine tags ever scheduled
    (clp0/dead/corp/sit2…), never raw 0x2B clip ids. The old emission here —
    0x66 with package 0 — was the untested guess and silently no-oped in game:
    ReadTpcEventMotionRes's selector is a NON-ZERO per-actor package in retail
    (Cornelia 12, 303E 63); package 0 is the default humanoid TALK set, which
    holds no mob motions. Callers must pass ROUTINE tags (the bridge normalises
    legacy raw-clip tags via ``normalize_cutscene_anim_tags``).

    ★ Own-routine precedence: pass ``cast_id`` so a tag the actor's model OWNS
    (``ctx.own_motions``, from the bridge's per-cast motion maps) fires via 0x2C
    even when it collides with a curated gesture name. A custom
    ``anim schedule add`` routine named 'tlk0' on a monster rig MUST NOT ride the
    humanoid bank — 0x5B force-loads humanoid skeleton motion onto the entity and
    the mismatched bones spaz out (works in AltanaViewer, breaks in game).

    Returns True when the tag was fired as an own routine (0x2C) — the caller may
    need an explicit 0x6B stop to end a looping routine."""
    tb = tag.encode("ascii")
    if len(tb) != 4:
        raise CutsceneCompileError(f"animation tag must be 4 chars: {tag!r}")
    # Bank inventory: the REAL parsed bank contents when the bridge supplied them,
    # else the hardcoded fallback mirror.
    bank = getattr(ctx, "bank_tags", None) or _GESTURE_TAGS
    own = bool(cast_id) and tag in getattr(ctx, "own_motions", {}).get(cast_id, ())
    if tag in bank and not own:
        _emit_u8(ctx, OP_SCHED_EXT)                 # 0x5B (loads the bank, then SetAction)
        _emit_u16(ctx, ctx.add_ref(ctx.anim_bank))  # selector → shared gesture bank file
    else:
        _emit_u8(ctx, OP_SET_ACTION)                # 0x2C — fire the actor's OWN routine
    _emit_u32(ctx, ent)
    _emit_u32(ctx, ent)
    ctx.code += tb
    return own or tag not in bank


def normalize_cutscene_anim_tags(cutscene: dict, motions_by_cast: dict,
                                 bank_tags: Optional[frozenset] = None) -> list:
    """Rewrite own-model animation tags into SCHEDULABLE action tags, in place.

    Non-gesture tags compile to ``0x2C SetAction``, which only fires one of the actor's
    OWN 0x07 scheduler routines. Older editor defs (and the anim dropdowns before
    2026-07-21) stored raw 0x2B clip ids (``at00``/``btl0``/``mou4``) — never schedulable
    in retail. Per anim/dialog keyframe tag: a curated gesture or valid routine passes
    through; a clip id owned by a routine is rewritten to that routine (``at00``→``ati0``);
    anything else keeps the tag and gains a warning (it will play in the editor preview
    only). ``motions_by_cast`` = ``{castId: {"valid": set, "clip2routine": {}, "name"}}``
    (built by the bridge from each cast NPC's model DAT). ``bank_tags`` = the real parsed
    inventory of the shared gesture bank (falls back to the hardcoded ``_GESTURE_TAGS``
    mirror). Returns the warning list."""
    warns: list = []
    bank = bank_tags or _GESTURE_TAGS

    def fix(cast_id, tag, where):
        # '@'-prefixed tags are sentinels / system routines (@idle stop-action, auto-turn
        # @tl0/@tr0), not model clips to rewrite — pass them through untouched.
        if not tag or tag.startswith("@"):
            return tag
        m = motions_by_cast.get(cast_id)
        # The actor's OWN routines outrank the gesture bank: a custom routine named like
        # a retail gesture (`anim schedule add tlk` → 'tlk0') must fire via 0x2C on the
        # actor's rig — the 0x5B humanoid bank would load wrong-skeleton motion onto a
        # non-humanoid model. _emit_gesture makes the same per-actor call.
        if m and tag in m.get("valid", ()):
            return tag
        if tag in bank:
            # A motion map exists ⇒ this is a FIXED-MODEL rig (equipped casts are omitted
            # from the maps) that does NOT own the tag: the humanoid bank binds by joint
            # index and distorts on unique rigs (Maat 79, Byakko 67 joints) — warn.
            if m:
                warns.append(f"anim '{tag}' ({where}) rides the shared humanoid gesture "
                             f"bank; {m.get('name') or cast_id} is a fixed-model rig — the "
                             f"bank may distort its skeleton. Prefer one of the model's "
                             f"own motions.")
            return tag                              # gesture — rides the 0x5B bank
        if not m:
            return tag                              # equipped/unknown rig — leave as authored
        name = m.get("name") or cast_id
        r = (m.get("clip2routine") or {}).get(tag)
        if r:
            warns.append(f"anim '{tag}' ({where}) is a raw clip id — rewrote to its motion "
                         f"routine '{r}' so it plays in game ({name})")
            return r
        warns.append(f"anim '{tag}' ({where}) is not a schedulable action on {name}'s rig — "
                     f"it will no-op in game (editor preview only)")
        return tag

    for t in (cutscene.get("timeline") or {}).get("tracks") or []:
        kind, cid = t.get("kind"), t.get("castId")
        if kind not in ("anim", "dialog"):
            continue
        for kf in t.get("keyframes") or []:
            tag = kf.get("anim")
            if tag:
                kf["anim"] = fix(cid or kf.get("actor") or kf.get("speaker"),
                                 tag, f"{kind} @ frame {kf.get('frame', '?')}")
    return warns


def _emit_prologue(ctx: _Ctx, flags: dict) -> None:
    """Cinematic prologue (retail order: lock → camera → cancel → fade → mode)::

        0x20 01   lock_player      -- CliEventUcFlag (cinematic UI state)
        0x46 01   camera           -- cinematic camera
        0x42      cancel_set       -- uninterruptible
        0x45 fdo1 · 0x1C wait 60   -- FADE TO BLACK, hold
        0x38      event_mode       -- CliEventModeLocal (default 0x2003)
        0x45 fdi1 · 0x1C wait 60   -- FADE IN from black, hold

    Server startCutscene owns the movement lock. NO 0x67 hide_hud — retail
    keeps the HUD so dialogue stays visible/dismissible.
    """
    cinematic = bool(flags.get("cinematic", True))
    ctx.cinematic = cinematic
    if not cinematic:
        # Plain NPC dialog (no lock, no fades). Retail opens these with
        # `0x4A look_at event_entity → player` (Ru'Lude Nomad Moogle 10196 and every
        # sibling): the NPC turns to the player before the first box. flags.facePlayer
        # = false skips it.
        if ctx.face_player:
            _emit_face(ctx, ctx.entity_id(ctx.owner_actor), ACTOR_MAGIC["player"])
            # Retail follows the turn with `6F` (yieldable ~16-frame sleep) and `70` (yield while
            # the entity's turn flag is set, then TurnCancel) before its first line (Curio Vendor
            # Moogle 9600/9601, Port Bastok). Without them our first line printed twice: the box
            # opened while the turn was still running (Ru'Lude Test hub, 2026-09-03).
            _emit_u8(ctx, 0x6F); _emit_u8(ctx, 0x70)
        return
    # 0x20 01 — CliEventUcFlag on. Opens VIRTUALLY EVERY retail cutscene (SSandy 210×,
    # Ru'Lude 167×, Qufim 92×, always the first opcode). The server's startCutscene
    # handles the MOVEMENT lock, but this client global drives the cinematic UI state —
    # prime suspect for the overhead-name suppression retail cutscenes show (control
    # test 2026-07-20: retail CS hides names on this client; ours didn't, and this was
    # the biggest remaining stream delta). Event teardown resets it.
    _emit_u8(ctx, OP_LOCK_PLAYER); _emit_u8(ctx, 0x01)
    _emit_u8(ctx, OP_CAMERA_CONTROL); _emit_u8(ctx, 1)  # 0x46 01 (retail order: lock, camera, cancel)
    _emit_u8(ctx, OP_CANCEL_SET)                         # 0x42
    _emit_fade(ctx, "fdo1")                              # fade to BLACK (event entity) + hold
    sel = ctx.add_ref(ctx.event_mode)
    _emit_u8(ctx, OP_EVENT_MODE); _emit_u16(ctx, sel)   # 0x38
    # 0x22 01 — SetEventHideFlag on the event entity (same Flags0 hide bit as 0x4E,
    # implicit target; Ru'Lude 10009 fires it in its prologue). Emitted when:
    #   · the owner has a place step — it re-shows via `4E 00 owner` at the marker; or
    #   · the owner is completely UNREFERENCED — the author doesn't want the trigger
    #     NPC in the scene, and the hide-others event mode can't touch it (its actor
    #     block hosts the event → always event-involved → exempt), so this explicit
    #     hide is the only way to remove it. Teardown restores it after the scene.
    # Skipped only for a referenced-but-unplaced owner (speaks from its world spot).
    if getattr(ctx, "owner_has_place", False) or not getattr(ctx, "owner_referenced", True):
        _emit_u8(ctx, OP_EVENT_HIDE); _emit_u8(ctx, 0x01)
    # Referenced-but-unplaced owner (talks from its world spot, no 4E-show follows):
    # stamp the name-hide directly when the option is on.
    if (getattr(ctx, "hide_names", False) and getattr(ctx, "owner_referenced", False)
            and not getattr(ctx, "owner_has_place", False)):
        _emit_u8(ctx, OP_ACTOR_UIFLAG); _emit_u8(ctx, 0x01)
        _emit_u32(ctx, ctx.entity_id(ctx.owner_actor))
    if ctx.face_player:
        # Turn the owner NPC to face the player while the screen is still black,
        # so cutscenes "just work" without needing an explicit Face keyframe.
        _emit_face(ctx, ctx.entity_id(ctx.owner_actor), ACTOR_MAGIC["player"])
    # DEFER the fade-in: retail fires the opening camera shot FIRST, then fades in, so
    # the scene reveals already framed (no "camera jumps a second later"). The step loop
    # flushes this after any leading camera shots (or immediately if there are none).
    ctx.pending_fade_in = "fdi1"


def _emit_epilogue(ctx: _Ctx, flags: dict) -> None:
    """Cinematic epilogue — matches Ambrotien 2000's ending::

        0x45 fdo1 (event entity) · 0x1C wait 60   -- FADE TO BLACK, hold
        0x46 00   camera           -- release cinematic camera
        0x45 fdi1 (event entity)                  -- FADE IN to gameplay (no hold)
        0x21      end

    Non-cinematic: just 0x21 end.
    """
    if getattr(ctx, "cinematic", True):
        _emit_fade(ctx, "fdo1")                              # fade to BLACK + hold
        if flags.get("hideActorsOnEnd"):
            # Hide every non-player cast NPC under the final black, so nothing is
            # standing there when the scene fades back to gameplay. The OWNER is
            # skipped — it's the world NPC the player triggered and must stay.
            # (Status-6 actors re-hide at teardown anyway; this covers the rest.)
            for cid, ent in ctx.cast.items():
                if cid == ctx.owner_actor or not (ent & 0xFF000000) or (ent >> 24) == 0x7F:
                    continue
                _emit_u8(ctx, OP_RENDER_FLAG); _emit_u8(ctx, 0x01); _emit_u32(ctx, ent)
        if getattr(ctx, "zoom_reset_tag", None) and ctx.camera_res_selector:
            # Restore the default camera zoom under the black: camera routes set the
            # GLOBAL projection focal and nothing resets it at event end — without this
            # the player keeps the last shot's zoom after the cutscene. The 'zrs0'
            # dur-0 still (focal 350) applies once; the follow-cam then resumes with
            # the client-default zoom when 0x46 00 releases control.
            player = ACTOR_MAGIC["player"]
            _emit_start_task(ctx, player, player, ctx.zoom_reset_tag,
                             ctx.add_ref(0), scene_sel=ctx.camera_res_selector)
        _emit_u8(ctx, OP_CAMERA_CONTROL); _emit_u8(ctx, 0)   # 0x46 00 release
        _emit_fade(ctx, "fdi1", hold=0)                      # fade IN, no hold (event ends)
    _emit_u8(ctx, OP_END)


# ---------------------------------------------------------------------------
# Step dispatch — one function per step op.
# ---------------------------------------------------------------------------

def _step_wait(step, ctx: _Ctx):
    # 0x1C wait_time takes a selector → refs[] frames. 3 bytes.
    sel = _value_selector(ctx, step.get("frames", 0))      # constant or a register spec
    _emit_u8(ctx, 0x1C); _emit_u16(ctx, sel)


def _step_camera(step, ctx: _Ctx):
    """Fire a camera shot: ``0x45 start_task <cameraScene> player player <routineTag> <dur>``.

    FIRE-AND-FORGET — no ``0x55 wait_sched``. The scheduler plays the shot's route
    over the routine's own duration WHILE the bytecode continues to dialogue/waits,
    so the camera glides in parallel (retail Balasiel/Ambrotien fire shots this way).
    The next shot's own ``0x45`` at its frame replaces the active camera task."""
    tag = ctx.shots.get(step["shot"])
    if tag is None:
        raise CutsceneCompileError(f"camera step references unknown shot id: {step['shot']!r}")
    player = ACTOR_MAGIC["player"]
    dur_sel = ctx.add_ref(step.get("duration", 0))
    _emit_start_task(ctx, player, player, tag, dur_sel, scene_sel=ctx.camera_res_selector)
    if step.get("wait", False):                 # opt-in blocking (rarely wanted)
        _emit_wait_sched(ctx, player, player, tag, scene_sel=ctx.camera_res_selector)


def _step_fade(step, ctx: _Ctx):
    """Mid-scene fade from a fade TRACK keyframe. Fires on the EVENT ENTITY (matches
    the retail Ambrotien pattern used by the prologue/epilogue fades) + a fixed
    ``0x1C wait_time`` hold (the keyframe's Length). Using the PLAYER entity here
    left the screen stuck black."""
    kind = step["kind"]
    tag = {"in": "fdi1", "out": "fdo1", "in_black": "fdi1", "out_black": "fdo1"}[kind]
    _emit_fade(ctx, tag, hold=int(step.get("frames", 30)))


def _step_say(step, ctx: _Ctx):
    """Print a dialog line. Cinematic mode wraps it in retail's talk-animation
    pattern (Maat's own event 74 uses this exact 0x5B loader)::

        0x5B <sel→bank> <speaker> <speaker> <talkAnim>  -- load bank + talk gesture
        0x1D <msg_sel>   (owner)   OR   0x2B <speaker> <msg_sel>   (other NPC)
        0x23 wait_dismiss                              -- ← Enter to advance (▼ in text)
        0x5E <idleAnim>                                -- stop, back to idle

    The talk/idle gestures are configurable: ``ctx.talk_anim`` / ``ctx.idle_anim``
    (from ``flags.talkAnim``/``flags.idleAnim``, default tlk0/idl0), overridable
    per line via ``step['anim']``.
    """
    if "textFrom" in step:
        # message id held in a register: `48 L9` (retail prints the trial details this way)
        _emit_u8(ctx, 0x48 if step.get("system", True) else OP_PRINT_MSG); _emit_u16(ctx, _work_selector(step["textFrom"]))
        if step.get("wait", True):
            _emit_u8(ctx, OP_WAIT_DISMISS)
        return
    line_id = step.get("text")
    mid = ctx.dialog_ids.get(line_id) if line_id else None
    if mid is None:
        # A dialog keyframe with no line picked yet (or a deleted line) — skip it
        # with a warning rather than crashing the whole publish.
        ctx.warnings.append(
            f"a Dialog keyframe has no valid line ({line_id!r}) — skipped. "
            f"Pick a line for it in the Dialog tab / keyframe.")
        return
    sel = ctx.add_ref(mid)

    if getattr(ctx, "cinematic", True):
        speaker_id = step.get("speaker")
        is_owner = (speaker_id is None) or (speaker_id == ctx.owner_actor)
        # Talk/idle gestures are PER-SPEAKER now (each cast NPC has its own default in the
        # NPCs tab): use the speaker's cast idleAnim/talkAnim, falling back to the global.
        smeta = ctx.cast_meta.get(speaker_id or ctx.owner_actor, {})
        # The talk gesture plays on the event entity (owner) or the speaker's NPC.
        anim_ent = ACTOR_MAGIC["event_entity"] if is_owner else ctx.entity_id(speaker_id)
        talk = step.get("anim") or smeta.get("talkAnim") or ctx.talk_anim
        idle = smeta.get("idleAnim") or ctx.idle_anim
        # retail alternates tlk0/tlk1 on consecutive talk lines of the same speaker
        if not step.get("anim") and talk in GESTURE_ALTERNATES and getattr(ctx, "last_gesture", None) == (anim_ent, talk):
            talk = GESTURE_ALTERNATES[talk]
        own_talk = False
        gesture_played = False
        if is_owner:
            _close_open_gesture(ctx, talk)          # leave a held pose (thk1 -> thk2) first
        if _gesture_fits(ctx, is_owner, talk, speaker_id or ctx.owner_actor):
            own_talk = _emit_gesture(ctx, anim_ent, talk,       # 0x5B bank / 0x2C own routine
                                     cast_id=(speaker_id or ctx.owner_actor))
            gesture_played = True
            ctx.last_gesture = (anim_ent, talk)
            if is_owner and not own_talk:
                ctx.open_gesture = talk if talk in GESTURE_CLOSERS else None
                ctx.open_gesture_ent = anim_ent
        if is_owner:
            _emit_u8(ctx, OP_PRINT_MSG); _emit_u16(ctx, sel)    # 0x1D (event entity speaks)
        else:
            _emit_u8(ctx, OP_PRINT_MSG2)                        # 0x2B (named speaker)
            _emit_u32(ctx, ctx.entity_id(speaker_id)); _emit_u16(ctx, sel)
        if step.get("wait", True):
            _emit_u8(ctx, OP_WAIT_DISMISS)                      # 0x23
        idle_b = idle.encode("ascii", "replace").ljust(4, b" ")[:4]
        talk_b = talk.encode("ascii", "replace").ljust(4, b" ")[:4]
        if own_talk and not is_owner:
            # An own routine fired on a CAST speaker: 0x5E only ever resets the event
            # entity, so a looping routine (custom talk clips loop) would run forever.
            # 0x6B stops THIS actor and drops it back to its idle.
            _emit_stop_action_actor(ctx, idle_b, anim_ent)     # 0x6B idle (this speaker)
        elif gesture_played:
            # Retail (Akta, Ru'Lude 116 / 10068; Maat 74): after the box closes it WAITS for the
            # gesture routine to finish — `53 wait_task <ent> <ent> <tag>` — and the routine
            # itself blends back to the stance. `5E stop_action idl0` here snapped the model
            # into the idle pose between every line (seen in game 2026-09-03).
            _emit_wait_task(ctx, anim_ent, talk_b)             # 0x53 wait for the gesture to end
        else:
            _emit_stop_action(ctx, idle_b)                     # 0x5E idle (event entity)
    else:
        # Non-cinematic: the owner speaks with 0x1D (retail NPC dialogs: Nomad Moogle 10196),
        # another cast member with 0x2B <entity>.
        speaker_id = step.get("speaker")
        if step.get("system"):
            _emit_u8(ctx, 0x48); _emit_u16(ctx, sel)                 # speaker-less line (print_msg3)
        elif speaker_id in (None, ctx.owner_actor):
            _emit_u8(ctx, OP_PRINT_MSG); _emit_u16(ctx, sel)
        else:
            _emit_u8(ctx, OP_PRINT_MSG2); _emit_u32(ctx, ctx.entity_id(speaker_id)); _emit_u16(ctx, sel)
        if step.get("wait", True):
            _emit_u8(ctx, OP_WAIT_DISMISS)


def _step_narrate(step, ctx: _Ctx):
    line_id = step["text"]
    mid = ctx.dialog_ids.get(line_id)
    if mid is None:
        raise CutsceneCompileError(f"narrate step references unknown line id: {line_id!r}")
    sel = ctx.add_ref(mid)
    _emit_u8(ctx, OP_NARRATE); _emit_u16(ctx, sel)
    if step.get("wait", True):
        _emit_u8(ctx, OP_WAIT_DISMISS)


def _emit_face(ctx: _Ctx, actor_id: int, target_id: int, talk: bool = False) -> None:
    """Turn ``actor_id`` to face ``target_id``. When the actor is the event
    OWNER, emit the EVENT magic id (0x7FFFFFF8) rather than its real NPC id —
    the client resolves the magic id straight to the event actor, which turns
    reliably; a raw NPC id can fail the 0x4A actor-index lookup mid-event."""
    actor = ACTOR_MAGIC["event_entity"] if actor_id == ctx.entity_id(ctx.owner_actor) else actor_id
    if talk:
        # 0x1E: 5B — event entity looks at target + mouth-moves
        _emit_u8(ctx, OP_LOOK_TALK); _emit_u32(ctx, target_id)
    else:
        # 0x4A: 9B — actor looks at target (clean turn, no mouth-move)
        _emit_u8(ctx, OP_LOOK_AT); _emit_u32(ctx, actor); _emit_u32(ctx, target_id)


def _step_face(step, ctx: _Ctx):
    _emit_face(ctx, ctx.entity_id(step["actor"]), ctx.entity_id(step["target"]),
               talk=bool(step.get("talk")))


def _step_anim(step, ctx: _Ctx):
    """Play a standalone gesture/emote on an entity — the SAME ``0x5B sched_ext`` the
    dialog talk-gesture uses (see :func:`_emit_gesture`), but on its own with no line.

    ``step`` = ``{op:'anim', actor, anim}`` where ``anim`` is a 4-char motion tag from
    the shared gesture bank (tlk0/thk1/ann0/bow0/…). The gesture plays on the event
    entity when the actor is the owner, else on that cast NPC — mirroring how a spoken
    line fires its gesture. Non-blocking: it animates while later steps run."""
    tag = (step.get("anim") or "").strip()
    actor_id = step.get("actor")
    is_owner = (actor_id is None) or (actor_id == ctx.owner_actor)
    ent = ACTOR_MAGIC["event_entity"] if is_owner else ctx.entity_id(actor_id)

    if tag == IDLE_STOP_TAG:                            # "@idle" — stop current action → idle
        smeta = ctx.cast_meta.get(actor_id or ctx.owner_actor, {})
        idle = (smeta.get("idleAnim") or ctx.idle_anim or "idl0")
        idle_b = idle.encode("ascii", "replace").ljust(4, b" ")[:4]
        if is_owner:
            _emit_stop_action(ctx, idle_b)             # 0x5E — event entity → idle
        else:
            _emit_stop_action_actor(ctx, idle_b, ent)  # 0x6B — this cast NPC → idle
        return

    if len(tag) != 4:
        raise CutsceneCompileError(
            f"anim step needs a 4-char animation tag (got {tag!r}); pick one in the Anim keyframe.")
    _emit_gesture(ctx, ent, tag,                       # 0x5B bank / 0x2C own routine
                  cast_id=(actor_id or ctx.owner_actor))


def _emit_show(ctx: _Ctx, ent: int) -> None:
    """``0x4E 00 <entity>`` — clear the entity's event-hide bit (Flags0 0x20000).

    Ru'Lude 10009 shows Cornelia + 2 others with only ``4E 00`` + ``0x80``; Qufim 63
    adds the 2F/92/94 render-flag ops (also retail, different bits). Either way the op
    only works on an entity the client PREPARED for this event — which requires the
    entity's own actor block to list the event id (see step 5b in compile_cutscene).

    When ``flags.hideNpcNames`` is on, each show is followed by ``94 01`` — the
    retail per-actor Flags3 stamp (SSandy ev0 / Qufim ev63 emit it on every staged
    NPC) believed to suppress the actor's overhead name for the event. Event-scoped:
    teardown resets it, no world side effects.
    """
    _emit_u8(ctx, OP_RENDER_FLAG)
    _emit_u8(ctx, 0x00)
    _emit_u32(ctx, ent)
    if getattr(ctx, "hide_names", False):
        _emit_u8(ctx, OP_ACTOR_UIFLAG)
        _emit_u8(ctx, 0x01)
        _emit_u32(ctx, ent)


def _emit_load_wait(ctx: _Ctx, ent: int) -> None:
    """``0x80`` — yield until the entity is ready (model/action load). 5 bytes.

    Retail (Ru'Lude 10009, Qufim 63) fires this after showing cast NPCs so the VM
    doesn't race ahead while a just-CHARREQ'd model is still loading.
    """
    _emit_u8(ctx, OP_LOAD_WAIT)
    _emit_u32(ctx, ent)


def _step_show_hide(step, ctx: _Ctx, show: bool):
    """Reveal / hide an entity. Show = ``0x4E 00`` (+ name-hide stamp when enabled);
    hide = ``0x4E 01``."""
    ent = ctx.entity_id(step["actor"])
    if show:
        _emit_show(ctx, ent)
    else:
        _emit_u8(ctx, OP_RENDER_FLAG)
        _emit_u8(ctx, 0x01)
        _emit_u32(ctx, ent)


def _step_place(step, ctx: _Ctx):
    """Stage an entity at a world position.

    Non-player cast members get the retail multi-NPC staging sequence
    (Ru'Lude 10009 show + Qufim 63 place/wait)::

        0x4E 00 entity     — reveal (clear the event-hide flag)
        0xBA  entity xyzθ  — calibrate to marker (X, Z, Y, dir)
        0x80  entity       — load-wait until ready

    ★ ``0xBA`` hard-requires ``entity->EventPointer`` (retail pseudocode skips the op
    otherwise). EventPointer is created ONLY by event-init for entities whose actor
    block lists this event id — the involvement blocks from step 5b. Neither server
    staging nor CHARREQ makes ``0xBA`` work without them. Player only needs
    ``0xBA`` + ``0x80``.

    Always uses the cast's raw server id (never ``event_entity``) — ``0xBA`` on
    ``0x7FFFFFF8`` is not a retail pattern for staging.
    """
    cast_id = step["actor"]
    ent = ctx.entity_id(cast_id)
    meta = ctx.cast_meta.get(cast_id, {})
    pos = step.get("pos") or meta.get("pos")
    dir_ = step.get("dir", meta.get("dir", 0.0))
    if pos is None:
        raise CutsceneCompileError(f"place step for {cast_id!r} needs pos (inline or cast)")
    x, y, z = pos
    is_player = ent == ACTOR_MAGIC["player"]

    # When the timeline has an EXPLICIT Show keyframe for this actor, the author wants
    # to control the reveal moment — don't pre-show at placement (status-6 NPCs stay
    # hidden until their Show fires; 0xBA/0x80 work fine on a hidden entity). The
    # owner always pre-shows: the prologue's 0x22 01 hid it.
    skip = bool(step.get("skip_show")) and cast_id != ctx.owner_actor
    if not is_player and not skip:
        _emit_show(ctx, ent)

    _emit_u8(ctx, OP_CALIBRATE_POS)
    _emit_u32(ctx, ent)
    # NOTE: 0xBA argument order is X, Z, Y, dir — Y/Z swap is retail (see docs).
    _emit_u16(ctx, ctx.add_signed_scaled(x))
    _emit_u16(ctx, ctx.add_signed_scaled(z))
    _emit_u16(ctx, ctx.add_signed_scaled(y))
    # ★ Heading encoding: 4096 units per full turn — PROVEN by retail statistics
    # (728 retail 0xBA dir refs sampled: 178 exceed 3142, which radians×1000 cannot
    # produce, and the max is exactly 4095), matching the client decode ref×2π/4096
    # (xievents 0x00BA). The old radians×1000 emit was mis-scaled; the +90° shift
    # layered on top was a curve-fit to observation noise. No convention offset:
    # editor heading and client yaw share the DB-rot angle space.
    units = int(round((dir_ % (2.0 * math.pi)) / (2.0 * math.pi) * 4096.0)) % 4096
    _emit_u16(ctx, ctx.add_ref(units))
    _emit_load_wait(ctx, ent)


def _step_music(step, ctx: _Ctx):
    slot = step.get("track", 0)
    song_sel = ctx.add_ref(step["song"])
    # 0x5C var: observed 3-byte form `5C <slot> <song_sel_hi>` in Maat 93 (`003680`, `013680`).
    _emit_u8(ctx, OP_MUSIC); _emit_u8(ctx, slot); _emit_u16(ctx, song_sel)


def _step_music_volume(step, ctx: _Ctx):
    vol_sel = ctx.add_ref(step["volume"])
    ease_sel = ctx.add_ref(step.get("frames", 0))
    _emit_u8(ctx, OP_MUSIC_VOLUME); _emit_u16(ctx, vol_sel); _emit_u16(ctx, ease_sel)


def _work_selector(spec) -> int:
    """Register spec → work-selector. ``menu_result`` → Work_Zone[0]; ``{"param": n}`` →
    Work_Zone[2 + n] (server parameter n); ``{"work": n}`` / int n → Work_Zone[n];
    ``{"work1700": n}`` → the extended bank; ``{"local": n}`` → WorkLocal[n]."""
    if spec in (None, "menu_result", "menu"):
        return WORK_MENU_RESULT
    if spec == "result":
        return WORK_EVENT_RESULT
    if isinstance(spec, dict):
        if "param" in spec and 0 <= int(spec["param"]) < 36:
            # 0..7 are the server's startEvent/updateEvent values; 8..35 are the same Work_Zone
            # run that retail's query rows read ("{17} times remaining" = Z[19], Oseem 12582)
            return WORK_PARAM_BASE + int(spec["param"])
        if "work" in spec and 0 <= int(spec["work"]) < 0x700:
            return WORK_ZONE_BASE + int(spec["work"])
        if "work1700" in spec and 0 <= int(spec["work1700"]) < 0x100:
            return 0x1700 + int(spec["work1700"])
        if "local" in spec and 0 <= int(spec["local"]) < 0x1000:
            return int(spec["local"])
        if "sel" in spec and 0 <= int(spec["sel"]) <= 0xFFFF:
            return int(spec["sel"])            # a raw selector the decompiler could not name (retail
                                               # occasionally writes an immediate ref where a register goes)
    if isinstance(spec, int) and 0 <= spec < 0x100:
        return WORK_ZONE_BASE + spec
    raise CutsceneCompileError(
        f"register spec must be 'menu_result', {{'param': n}}, {{'work': n}}, {{'work1700': n}} or {{'local': n}}, got {spec!r}")


def _value_selector(ctx: _Ctx, spec) -> int:
    """A value operand: an int constant (→ references[] slot) or a register spec."""
    if isinstance(spec, bool):
        spec = int(spec)
    if isinstance(spec, int):
        return ctx.add_ref(spec)
    return _work_selector(spec)


def _emit_store(ctx: _Ctx, dst_sel: int, value: int) -> None:
    """``0x03 get_store``: Work_Zone[dst] = constant (retail: ``03 0110 xx81`` before end)."""
    val_sel = ctx.add_ref(int(value))
    _emit_u8(ctx, OP_STORE); _emit_u16(ctx, dst_sel); _emit_u16(ctx, val_sel)


def _step_menu(step, ctx: _Ctx):
    """``0x24 dialog_menu <msgSel> <cursorSel> <flagsSel>`` + ``0x25 wait_select``.

    The prompt string is synthesized by :func:`_inject_menu_lines` (question + one line per
    option, retail layout) and reaches us as ``step['_menu_line']``. ``cursor`` = default
    highlighted option (0-based), ``flags`` = the second operand (retail passes 0; bit 0
    suppresses option rows, so leave it). After ``0x25`` the choice is in Work_Zone[0]."""
    if "textFrom" in step:
        # message id in a register (the Magian details menu picks one of four strings at run time)
        msg_sel = _work_selector(step["textFrom"])
        cursor_sel = _value_selector(ctx, step.get("cursor", 0))
        flags_sel = _value_selector(ctx, step["hide"]) if "hide" in step else ctx.add_ref(int(step.get("flags", 0)))
        if step.get("query"):
            _emit_u8(ctx, 0xD4); _emit_u8(ctx, 0x02)       # the query-window flavour keeps its opcode
        else:
            _emit_u8(ctx, OP_MENU)
        _emit_u16(ctx, msg_sel); _emit_u16(ctx, cursor_sel); _emit_u16(ctx, flags_sel)
        _emit_menu_wait(step, ctx)
        return
    line_id = step.get("_menu_line") or step.get("text")
    mid = ctx.dialog_ids.get(line_id)
    if mid is None:
        raise CutsceneCompileError(f"menu step references unknown line id: {line_id!r}")
    msg_sel = ctx.add_ref(mid)
    cursor_sel = _value_selector(ctx, step.get("cursor", 0))
    # Third operand = HIDE mask: the client walks the option rows and skips row i when
    # bit i is set (PS2 CodeQUERY: `if ((val2 & 1) == 0) AddItem(...)`, then `val2 >>= 1`).
    # ``hidden``: list of option indexes to drop; ``hide``: a constant mask or a register
    # spec such as {"param": 6} so the server decides which rows exist.
    if "hide" in step:
        flags_sel = _value_selector(ctx, step["hide"])
    else:
        mask = 0
        for i in step.get("hidden", []) or []:
            mask |= 1 << int(i)
        flags_sel = ctx.add_ref(mask | int(step.get("flags", 0)))
    if step.get("query"):
        # D4 02 <msg> <cursor> <hide>: the query-window flavour (CodeQUERY through PTR_TalkpQW);
        # rows given an item by `row_item` show that item's description while highlighted
        # (Oseem's stone rows, the Splintery Chest list). Same operands, same 0x25 wait.
        _emit_u8(ctx, 0xD4); _emit_u8(ctx, 0x02); _emit_u16(ctx, msg_sel); _emit_u16(ctx, cursor_sel); _emit_u16(ctx, flags_sel)
    else:
        _emit_u8(ctx, OP_MENU); _emit_u16(ctx, msg_sel); _emit_u16(ctx, cursor_sel); _emit_u16(ctx, flags_sel)
    _emit_menu_wait(step, ctx)


def _emit_menu_wait(step, ctx: _Ctx) -> None:
    """``25`` wait_select after a menu; ``"waitOp": 127`` emits retail's ``7F`` variant instead,
    ``"wait": false`` emits nothing (the decompiler keeps whatever retail did)."""
    if step.get("wait", True) is False:
        return
    _emit_u8(ctx, int(step.get("waitOp", OP_WAIT_SELECT)) & 0xFF)


def _step_row_item(step, ctx: _Ctx):
    """``D4 03 <row> <item>``: attach an item id to query-window row ``row`` (1-based, as the
    option rows count) so the window shows its description while that row is highlighted
    (Oseem sets one per stone row before the "Use which item?" query)."""
    _emit_u8(ctx, 0xD4); _emit_u8(ctx, 0x03)
    _emit_u16(ctx, _value_selector(ctx, step.get("row", 1))); _emit_u16(ctx, _value_selector(ctx, step.get("item", 0)))


def _step_augment_preview(step, ctx: _Ctx):
    """``D4 05 <window> <item> <a> <b> <c>``: the augment preview window number ``window``
    (0 = left/previous, 1 = right/new) for ``item`` with the item's exdata words ``a b c``
    (bytes 0..11: kind 02, subkind 03, then four u16 augments). Oseem opens two, one per set,
    before "Keep the previous effects / Keep the new glyptic's effects". ``"kind": 4`` emits
    the D4 04 variant (same operands, flag byte 0 instead of 2)."""
    sub = 0x04 if int(step.get("kind", 5)) == 4 else 0x05
    _emit_u8(ctx, 0xD4); _emit_u8(ctx, sub)
    _emit_u16(ctx, _value_selector(ctx, step.get("window", 0)))
    _emit_u16(ctx, _value_selector(ctx, step.get("item", 0)))
    for n, k in enumerate(("a", "b", "c")):
        _emit_u16(ctx, _value_selector(ctx, step.get(k, {"param": 2 + n})))


def _step_branch(step, ctx: _Ctx):
    """Case table on a work register (default: the menu choice).

    ``cases`` maps ``"0"``, ``"1"``, ... (option index) or ``"cancel"`` (Escape) to a step
    label; ``default`` (optional) is jumped to when nothing matched. Each case is one
    ``0x02 if <reg> <const> kind=1 <target>`` — equal → jump, else fall through — exactly
    how retail chains its menu tests."""
    reg = _work_selector(step.get("on", "menu_result"))
    cases = step.get("cases") or {}
    if not cases and not step.get("default"):
        raise CutsceneCompileError("branch step has no cases")
    for key, label in cases.items():
        k = str(key).strip().lower()
        value = MENU_CANCELLED if k in ("cancel", "escape") else int(k)
        val_sel = ctx.add_ref(value)
        _emit_u8(ctx, OP_IF); _emit_u16(ctx, reg); _emit_u16(ctx, val_sel)
        _emit_u8(ctx, IF_KIND_JUMP_IF_EQUAL); ctx.emit_target(str(label))
    if step.get("default"):
        _emit_u8(ctx, OP_SET_EXEC); ctx.emit_target(str(step["default"]))


def _step_goto(step, ctx: _Ctx):
    """``0x01 set_exec <absolute offset>`` — unconditional jump to a step label."""
    _emit_u8(ctx, OP_SET_EXEC); ctx.emit_target(str(step["to"]))


def _step_set_result(step, ctx: _Ctx):
    """Work_Zone[1] = value: what the server will read as ``option`` on the next
    ``0x05B`` (event end, or a ``server_update``). ``value`` is a constant; ``from`` copies
    a register (e.g. ``{"work": 4}`` after a number_input)."""
    if "from" in step:
        _emit_u8(ctx, OP_STORE); _emit_u16(ctx, WORK_EVENT_RESULT); _emit_u16(ctx, _work_selector(step["from"]))
        return
    _emit_store(ctx, WORK_EVENT_RESULT, int(step.get("value", 0)))


def _step_say_indexed(step, ctx: _Ctx):
    """Print one of N strings chosen at run time: message id = base + register (the Field
    Manual prints ``7872 + regimeId`` this way — one static string per regime, the server
    replies with the index). ``texts`` is the ordered list of strings (each may use ``{n}``
    placeholders); they are appended as a contiguous block and the event computes
    ``L[scratch] = first; L[scratch] += index; print L[scratch]``. ``index`` is a register
    spec (default ``{"param": 7}``); ``speaker`` (cast id) uses 0x2B, otherwise the
    speaker-less 0x48. ``wait`` (default true) adds 0x23."""
    texts = step.get("texts") or []
    if not texts:
        raise CutsceneCompileError("say_indexed needs a non-empty texts list")
    blobs = [xi_dialog.encode_event_string(str(t if (str(t).rstrip().endswith("\\v") or str(t).rstrip().endswith("{noprompt}")) else str(t) + "\\v")) + b"\x00"
             for t in texts]
    ctx.dialog_out, first = xi_author.append_dialog_block(ctx.dialog_out, blobs)
    scratch = int(step.get("scratch", 79))
    if not 0 <= scratch < 80:
        raise CutsceneCompileError("say_indexed scratch must be a WorkLocal index 0..79")
    _emit_u8(ctx, OP_STORE); _emit_u16(ctx, scratch); _emit_u16(ctx, ctx.add_ref(first))
    _emit_u8(ctx, 0x07); _emit_u16(ctx, scratch); _emit_u16(ctx, _work_selector(step.get("index", {"param": 7})))
    speaker = step.get("speaker")
    if speaker:
        _emit_u8(ctx, OP_PRINT_MSG2); _emit_u32(ctx, ctx.entity_id(speaker)); _emit_u16(ctx, scratch)
    else:
        _emit_u8(ctx, OP_NARRATE); _emit_u16(ctx, scratch)
    if step.get("wait", True):
        _emit_u8(ctx, OP_WAIT_DISMISS)


OP_EFFECT    = 0x73    # 11B — op + effect selector + caster entity u32 + target entity u32 (CodeMAGICSCHEDULOR)
OP_LOAD_ZONE = 0x34    # 3B — op + selector → zone id (full swap); 0x35 = overlay/restore


def _entity_spec(ctx: _Ctx, spec) -> int:
    if spec in (None, "self", "owner"):
        return ACTOR_MAGIC["event_entity"]
    if spec == "player":
        return ACTOR_MAGIC["player"]
    if isinstance(spec, int):
        return spec & 0xFFFFFFFF
    if isinstance(spec, str) and spec.lower().startswith("0x"):
        return int(spec, 16) & 0xFFFFFFFF
    return ctx.entity_id(str(spec))


# Cast choreography around a 0x73 effect (Shihu-Danhu, Al Zahbi 103; survey of every retail
# cabk/cawh/shbk/shwh/spef load joined with npcs.yaml looks; AltanaView's PC "Basic" action list,
# 2026-09-03). A player-skeleton NPC already OWNS the cast schedules in its race's basic action
# set (Elvaan male ROM/37/31: cabk = mb00 chant, shbk = mb10 release, cawh/shwh the white pair,
# ssbk/sswh the chained versions), so they are fired with 0x2C SetAction like any own routine.
# Fixed humanoid models (bank-60 family) get the same tags from gesture bank 342 via 0x5B, whose
# release is "spef". Retail waits ~200 frames after the chant and ~100 after the release.
# Spell families of the PC basic action set (Elvaan male ROM/37/31, `xi anim schedule list`):
# chant `ca<xx>` and release `sh<xx>` share a suffix; `ss<xx>` is the chained two-stage release.
#   bk black (mb00/mb10)   wh white (mw00/mw10)   bl blue (ma01/ma10)   nj ninjutsu (mn01/mn10)
#   sm summon (ms00/ms10)  it item use (mi00/mi20)  fa (ms00/ms10, unnamed)  st generic chant (mb00)
CAST_FAMILIES = {"black": "bk", "white": "wh", "blue": "bl", "ninjutsu": "nj", "summon": "sm", "item": "it"}
CAST_FIXED_BANK = 342                       # fixed humanoids: only cabk/cawh + spef exist there
CAST_FIXED_CODES = {"bk", "wh"}


def _cast_code(cast: str) -> str:
    code = CAST_FAMILIES.get(cast, cast)
    if len(code) != 2 or not code.isalpha():
        raise CutsceneCompileError(f"effect cast must be one of {sorted(CAST_FAMILIES)} or a two-letter "
                                   f"schedule code (ca<xx>/sh<xx>), got {cast!r}")
    return code


def _emit_motion(ctx: _Ctx, op: int, ent: int, bank: int, tag: str) -> None:
    """``5B`` (gesture bank file) or ``66`` (per-actor Tpc package) — same 15-byte layout."""
    _emit_u8(ctx, op); _emit_u16(ctx, ctx.add_ref(bank)); _emit_u32(ctx, ent); _emit_u32(ctx, ent)
    ctx.code += tag.encode("ascii", "replace").ljust(4, b" ")[:4]


def _emit_own_action(ctx: _Ctx, ent: int, tag: str) -> None:
    """``2C SetAction ent ent tag``: fire a routine the actor already carries."""
    _emit_u8(ctx, OP_SET_ACTION); _emit_u32(ctx, ent); _emit_u32(ctx, ent)
    ctx.code += tag.encode("ascii", "replace").ljust(4, b" ")[:4]


def _emit_cast_motion(ctx: _Ctx, which: str, cast: str) -> bool:
    """``which`` = "windup" | "release" for the owner's skeleton; False when none is known."""
    ent = ACTOR_MAGIC["event_entity"]
    code = _cast_code(cast)
    if getattr(ctx, "owner_look_type", "") == "equipped":
        _emit_own_action(ctx, ent, ("ca" if which == "windup" else "sh") + code)
        return True
    if getattr(ctx, "owner_model", None) in BANK60_MODELS:
        if code not in CAST_FIXED_CODES:
            note = f"effect cast={cast!r}: fixed humanoid models only carry black/white chants in bank 342; using black"
            if note not in ctx.warnings:
                ctx.warnings.append(note)
            code = "bk"
        _emit_motion(ctx, OP_SCHED_EXT, ent, CAST_FIXED_BANK, ("ca" + code) if which == "windup" else "spef")
        return True
    return False


def _step_effect(step, ctx: _Ctx):
    """Play a spell/ability visual between two entities: ``73 <effectSel> <caster> <target>``
    (the Nomad Moogle's "strange spell" before granting job points: effect 244 from the event
    entity onto the player; the Field Manual plays Reraise/Regen/Protect this way after the
    server confirms a purchase). ``id`` = effect/animation id (constant or register),
    ``from``/``to`` = "self" (event entity), "player" or a cast id. ``wait`` frames (default
    60) adds a 0x1C pause so the animation is seen; 0 skips it."""
    if "id" not in step:
        raise CutsceneCompileError("effect step needs an id")
    if str(step.get("variant", "")).lower() == "ad":
        # Magian Moogle: `AD 02 <effect> self player` (203 / 206, the crystal over the player),
        # bracketed by its own hap0 / hap1 routines and a 300-frame wait.
        _emit_u8(ctx, 0xAD); _emit_u8(ctx, 0x02); _emit_u16(ctx, _value_selector(ctx, step["id"]))
        _emit_u32(ctx, _entity_spec(ctx, step.get("from", "self"))); _emit_u32(ctx, _entity_spec(ctx, step.get("to", "player")))
        frames = int(step.get("wait", 0))
        if frames > 0:
            _emit_u8(ctx, 0x1C); _emit_u16(ctx, ctx.add_ref(frames))
        return
    if getattr(ctx, "cinematic", True):
        _close_open_gesture(ctx)
    cast = str(step.get("cast") or "none").lower()          # "black" | "white" | "none"
    caster = _entity_spec(ctx, step.get("from", "self"))
    choreo = False
    if cast != "none" and step.get("from", "self") == "self":
        choreo = _emit_cast_motion(ctx, "windup", cast)                    # chant (cabk / cawh)
        if not choreo:
            ctx.warnings.append(f"effect cast={cast!r}: no cast motions known for this owner look (only "
                                "equipped looks and the bank-60 model family); playing the effect without a wind-up")
    pre = int(step.get("delay", 200 if choreo else 30))    # retail: ~200 frames of chant; 30 before a bare 0x73
    if pre > 0:
        _emit_u8(ctx, 0x1C); _emit_u16(ctx, ctx.add_ref(pre))
    _emit_u8(ctx, OP_EFFECT); _emit_u16(ctx, _value_selector(ctx, step["id"]))
    _emit_u32(ctx, caster)
    _emit_u32(ctx, _entity_spec(ctx, step.get("to", "player")))
    if choreo:
        _emit_cast_motion(ctx, "release", cast)                            # release (shbk / shwh / spef)
    frames = int(step.get("wait", 100 if choreo else 60))
    if frames > 0:
        _emit_u8(ctx, 0x1C); _emit_u16(ctx, ctx.add_ref(frames))


OP_ITEM_WINDOW2 = 0xCC   # CC 01 <itemSel> <a> <b> <c>: item window with augment words (Tenshodo coffer)
OP_SET_BIT      = 0x3C   # 3C <dst> <bitSel> <limitSel>: dst |= 1 << (bit & 31) when (bit >> 5) < limit
OP_CLEAR_BIT    = 0x3D   # 3D <dst> <bitSel> <limitSel>: dst &= ~(1 << (bit & 31)) under the same test


def _step_augment_window(step, ctx: _Ctx):
    """``CC 01 <item> <a> <b> <c>``: the item description window for ``item`` with three
    augment words (the Tenshodo coffer previews "Your <item> has been augmented..." this
    way after the server's round trip: ``updateEvent(a, b, c)`` where each word packs two
    augments as 5-bit power + 11-bit id). ``item``/``a``/``b``/``c`` are constants or
    register specs ({"param": 0} ...). ``"close"`` closes it (item 0)."""
    item = step.get("item", "close")
    if item in ("close", None, 0) and not any(k in step for k in ("a", "b", "c")):
        sel = ctx.add_ref(0)
        words = [ctx.add_ref(0)] * 3
    elif item in ("close", None, 0):
        sel = ctx.add_ref(0)                    # item 0 with explicit words (retail Port Jeuno 381)
        words = [_value_selector(ctx, step.get(k, 0)) for k in ("a", "b", "c")]
    else:
        sel = _value_selector(ctx, item)
        words = [_value_selector(ctx, step.get(k, {"param": n})) for n, k in enumerate(("a", "b", "c"))]
    _emit_u8(ctx, OP_ITEM_WINDOW2); _emit_u8(ctx, 0x01); _emit_u16(ctx, sel)
    for w in words:
        _emit_u16(ctx, w)


def _step_set_bit(step, ctx: _Ctx, setting: bool):
    """``set_bit`` / ``clear_bit``: ``{"local": n, "bit": b}`` sets or clears bit ``b`` (0..31)
    of WorkLocal[n] (0x3C / 0x3D with limit 1, the coffer's flag word: `set_bit L[1] 24`);
    ``target`` may name any register spec instead. ``bit`` may be a register spec."""
    dst = _work_selector({"local": int(step["local"])}) if "local" in step else _work_selector(step["target"])
    bit = _value_selector(ctx, step.get("bit", 0))
    _emit_u8(ctx, OP_SET_BIT if setting else OP_CLEAR_BIT)
    _emit_u16(ctx, dst); _emit_u16(ctx, bit); _emit_u16(ctx, _value_selector(ctx, step.get("limit", 1)))


def _step_store(step, ctx: _Ctx):
    """``0x03 get_store <dst> <src>``: any register = constant or another register.
    ``{"op":"store","into":{"param":0},"from":13206}`` is how the coffer loads item ids
    into the parameters its ``{rowitem:n}`` rows display; ``{"into":{"local":4},"from":
    {"work":0}}`` copies the menu choice aside."""
    dst = _work_selector(step["into"])
    if "text" in step:
        mid = ctx.dialog_ids.get(step["text"])
        if mid is None:
            raise CutsceneCompileError(f"store: unknown line id {step['text']!r}")
        _emit_u8(ctx, OP_STORE); _emit_u16(ctx, dst); _emit_u16(ctx, ctx.add_ref(mid))
        return
    _emit_u8(ctx, OP_STORE); _emit_u16(ctx, dst); _emit_u16(ctx, _value_selector(ctx, step.get("from", 0)))


def _step_if_equal(step, ctx: _Ctx):
    """``0x02 if <a> <b> kind=1 <target>``: jump to ``to`` when the two values are equal
    (registers or constants), else fall through. The coffer's duplicate-augment guard:
    ``if_equal a=menu_result b={"local":0} to=dup``."""
    _emit_u8(ctx, OP_IF)
    _emit_u16(ctx, _value_selector(ctx, step.get("a", "menu_result")))
    _emit_u16(ctx, _value_selector(ctx, step.get("b", 0)))
    _emit_u8(ctx, IF_KIND_JUMP_IF_EQUAL); ctx.emit_target(str(step["to"]))


# ---------------------------------------------------------------------------------------
# item_list: the Splintery Chest picker (Ru'Lude Gardens 10133, decoded 2026-09-03).
# Retail layout, kept byte-for-byte in shape:
#   L3 state (10 list / 30 preview / 40 confirm / -1 leave), L7 page, L8 last page, L9 cursor,
#   L10 hide mask, L5 index = page*16 + row, L6 low option bits, L4 saved state, Z7[23] = item.
#   A subroutine copies table[page*16 + i] into Z[2..9] / Z7[0..7] (the 16 `{rowitem:i}` rows)
#   and hides empty rows with 3C; rows 16/17/18 are previous / next / cancel with bits 16/17 of
#   the mask hiding them on the first / last page. Pick -> 93 item window on Z7[23] + a line ->
#   "Obtain X? Take it / Leave it" -> option = lowBits | index << 2 -> exit (chest close motion).
# ---------------------------------------------------------------------------------------
_ITEM_LIST_ROWS = 16
_ROW_PREFIX = "{raw:7f800101010120}"     # retail's row opener before the item-name code


def _emit_if(ctx: _Ctx, a: int, b: int, kind: int, label: str) -> None:
    _emit_u8(ctx, OP_IF); _emit_u16(ctx, a); _emit_u16(ctx, b); _emit_u8(ctx, kind); ctx.emit_target(label)


def _emit_store_sel(ctx: _Ctx, dst: int, src: int) -> None:
    _emit_u8(ctx, OP_STORE); _emit_u16(ctx, dst); _emit_u16(ctx, src)


_item_list_counter = [0]


def _step_item_list(step, ctx: _Ctx):
    items = [int(x) for x in (step.get("items") or [])]
    if not items:
        raise CutsceneCompileError("item_list needs items")
    rows = _ITEM_LIST_ROWS
    pages = -(-len(items) // rows)
    low = int(step.get("lowBits", 0)) & 3
    n = _item_list_counter[0]; _item_list_counter[0] += 1
    L = lambda name_: f"__ilist{n}_{name_}"
    ent = ACTOR_MAGIC["event_entity"]

    # strings: list menu (question + 16 name rows + prev/next/cancel), preview line, confirm menu
    q = str(step.get("question", "Retrieve which item?"))
    row_texts = [f"{_ROW_PREFIX}{{rowitem:{i}}}." for i in range(rows)]
    row_texts += [str(step.get("prevText", "Return one page.")), str(step.get("nextText", "Advance one page.")),
                  str(step.get("cancelText", "Cancel."))]
    list_blob = xi_dialog.encode_event_string(q + "\\n" + "\x0b" + "\\n".join(row_texts)) + b"\x7f\x31\x00"
    preview_blob = xi_dialog.encode_event_string(str(step.get("previewText", "You take the {name:0x23:31} in hand..."))) + b"\x7f\x31\x00"
    confirm_blob = xi_dialog.encode_event_string(str(step.get("confirmText", "Obtain {raw:010101} {name:0x24:31}?")) + "\\n" + "\x0b" +
                                                 str(step.get("takeText", "Take it.")) + "\\n" + str(step.get("leaveText", "Leave it."))) + b"\x7f\x31\x00"
    ctx.dialog_out, ids = xi_author.append_dialog_blobs(ctx.dialog_out, [list_blob, preview_blob, confirm_blob])
    msg_list, msg_preview, msg_confirm = (ctx.add_ref(i) for i in ids)

    L0, L1, L2, L3, L4, L5, L6, L7, L8, L9, L10 = range(11)
    Z0, Z1 = WORK_MENU_RESULT, WORK_EVENT_RESULT
    Z7_23 = 0x1700 + 23
    c = ctx.add_ref
    KIND_NE_JUMP, KIND_NE_JUMP_L, KIND_LE, KIND_GE, KIND_LT = 0x00, 0x80, 0x02, 0x03, 0x04

    open_tag = step.get("openAnim"); close_tag = step.get("closeAnim")
    if open_tag:
        _emit_own_action(ctx, ent, str(open_tag)); _emit_u8(ctx, 0x1C); _emit_u16(ctx, c(int(step.get("animWait", 120))))
    _emit_store_sel(ctx, L3, c(10))
    ctx.mark_label(L("loop"))
    _emit_if(ctx, L3, c(0), KIND_LT, L("exit"))
    _emit_if(ctx, L3, c(10), KIND_NE_JUMP_L, L("preview_check"))
    _emit_store_sel(ctx, L8, c(pages - 1)); _emit_store_sel(ctx, L10, c(0))
    _emit_u8(ctx, OP_CALL); ctx.emit_target(L("fill"))
    _emit_if(ctx, L7, c(0), KIND_NE_JUMP, L("s1"))
    _emit_u8(ctx, OP_SET_BIT); _emit_u16(ctx, L10); _emit_u16(ctx, c(16)); _emit_u16(ctx, c(1))
    ctx.mark_label(L("s1"))
    _emit_if(ctx, L7, L8, KIND_NE_JUMP, L("s2"))
    _emit_u8(ctx, OP_SET_BIT); _emit_u16(ctx, L10); _emit_u16(ctx, c(17)); _emit_u16(ctx, c(1))
    ctx.mark_label(L("s2"))
    _emit_u8(ctx, OP_MENU); _emit_u16(ctx, msg_list); _emit_u16(ctx, L9); _emit_u16(ctx, L10)
    _emit_u8(ctx, OP_WAIT_SELECT)
    _emit_if(ctx, Z0, c(rows), KIND_NE_JUMP, L("c17"))
    _emit_if(ctx, L7, c(0), KIND_LE, L("iter"))
    _emit_u8(ctx, 0x0C); _emit_u16(ctx, L7); _emit_store_sel(ctx, L9, c(0))
    _emit_u8(ctx, OP_SET_EXEC); ctx.emit_target(L("iter"))
    ctx.mark_label(L("c17"))
    _emit_if(ctx, Z0, c(rows + 1), KIND_NE_JUMP, L("c18"))
    _emit_if(ctx, L7, c(pages - 1), KIND_GE, L("iter"))
    _emit_u8(ctx, 0x0B); _emit_u16(ctx, L7); _emit_store_sel(ctx, L9, c(0))
    _emit_u8(ctx, OP_SET_EXEC); ctx.emit_target(L("iter"))
    ctx.mark_label(L("c18"))
    _emit_if(ctx, Z0, c(rows + 2), KIND_NE_JUMP, L("pick"))
    _emit_store_sel(ctx, Z1, c(MENU_CANCELLED)); _emit_store_sel(ctx, L3, c(0xFFFFFFFF))
    _emit_u8(ctx, OP_SET_EXEC); ctx.emit_target(L("iter"))
    ctx.mark_label(L("pick"))
    _emit_if(ctx, Z0, c(rows), KIND_GE, L("cancel2"))
    _emit_store_sel(ctx, L6, c(low)); _emit_store_sel(ctx, L5, L7)
    _emit_u8(ctx, 0x14); _emit_u16(ctx, L5); _emit_u16(ctx, c(rows))          # mul
    _emit_u8(ctx, 0x07); _emit_u16(ctx, L5); _emit_u16(ctx, Z0)               # add
    table_ref = len(ctx.code) + 2          # code-relative patch position; the operand gets the ABSOLUTE table offset
    _emit_u8(ctx, 0x9D); _emit_u8(ctx, 0x00); _emit_u16(ctx, 0); _emit_u16(ctx, Z7_23); _emit_u16(ctx, L5)
    _emit_store_sel(ctx, L4, L3); _emit_store_sel(ctx, L3, c(30)); _emit_store_sel(ctx, L9, Z0)
    _emit_u8(ctx, OP_SET_EXEC); ctx.emit_target(L("iter"))
    ctx.mark_label(L("cancel2"))
    _emit_store_sel(ctx, Z1, c(MENU_CANCELLED)); _emit_store_sel(ctx, L3, c(0xFFFFFFFF))
    ctx.mark_label(L("iter"))
    _emit_u8(ctx, OP_SET_EXEC); ctx.emit_target(L("loop_end"))
    ctx.mark_label(L("preview_check"))
    _emit_if(ctx, L3, c(30), KIND_NE_JUMP_L, L("confirm_check"))
    _emit_u8(ctx, OP_ITEM_INFO); _emit_u16(ctx, Z7_23)
    _emit_u8(ctx, 0x48); _emit_u16(ctx, msg_preview); _emit_u8(ctx, OP_WAIT_DISMISS)
    _emit_u8(ctx, OP_ITEM_INFO); _emit_u16(ctx, c(0))
    _emit_store_sel(ctx, L3, c(40))
    _emit_u8(ctx, OP_SET_EXEC); ctx.emit_target(L("loop_end"))
    ctx.mark_label(L("confirm_check"))
    _emit_if(ctx, L3, c(40), KIND_NE_JUMP_L, L("loop_end"))
    _emit_u8(ctx, OP_MENU); _emit_u16(ctx, msg_confirm); _emit_u16(ctx, c(1)); _emit_u16(ctx, c(0))
    _emit_u8(ctx, OP_WAIT_SELECT)
    _emit_if(ctx, Z0, c(0), KIND_NE_JUMP, L("leave"))
    _emit_u8(ctx, 0x06); _emit_u16(ctx, Z1)                                   # set_zero option
    _emit_u8(ctx, OP_BITS_SET); _emit_u16(ctx, c(0)); _emit_u16(ctx, c(1)); _emit_u16(ctx, Z1); _emit_u16(ctx, L6)
    _emit_u8(ctx, OP_BITS_SET); _emit_u16(ctx, c(2)); _emit_u16(ctx, c(9)); _emit_u16(ctx, Z1); _emit_u16(ctx, L5)
    _emit_store_sel(ctx, L3, c(0xFFFFFFFF))
    _emit_u8(ctx, OP_SET_EXEC); ctx.emit_target(L("loop_end"))
    ctx.mark_label(L("leave"))
    _emit_if(ctx, Z0, c(1), KIND_NE_JUMP, L("loop_end"))
    _emit_store_sel(ctx, L3, L4)
    ctx.mark_label(L("loop_end"))
    _emit_u8(ctx, OP_SET_EXEC); ctx.emit_target(L("loop"))
    ctx.mark_label(L("exit"))
    if close_tag:
        _emit_own_action(ctx, ent, str(close_tag)); _emit_u8(ctx, 0x1C); _emit_u16(ctx, c(int(step.get("animWait", 120))))
    _emit_u8(ctx, OP_SET_EXEC); ctx.emit_target(END_LABEL)                    # the event ends with the option

    # trailer: fill subroutine + page-padded table (after `end`, reached only by call / 9D)
    def emit_trailer(rows=rows, pages=pages, items=items, table_ref=table_ref, L=L):
        ctx.mark_label(L("fill"))
        _emit_store_sel(ctx, L1, c(0)); _emit_store_sel(ctx, L2, L7)
        _emit_u8(ctx, 0x14); _emit_u16(ctx, L2); _emit_u16(ctx, c(rows))
        ctx.mark_label(L("fl"))
        _emit_if(ctx, L1, c(rows), KIND_GE, L("fret"))
        read_ref = len(ctx.code) + 2
        _emit_u8(ctx, 0x9D); _emit_u8(ctx, 0x00); _emit_u16(ctx, 0); _emit_u16(ctx, L0); _emit_u16(ctx, L2)
        _emit_if(ctx, L0, c(0), KIND_NE_JUMP, L("f2"))
        _emit_u8(ctx, OP_SET_BIT); _emit_u16(ctx, L10); _emit_u16(ctx, L1); _emit_u16(ctx, c(1))
        ctx.mark_label(L("f2"))
        for i in range(rows):
            dst = (0x1002 + i) if i < 8 else (0x1700 + i - 8)
            _emit_if(ctx, L1, c(i), KIND_NE_JUMP_L, L(f"fi{i}"))
            _emit_store_sel(ctx, dst, L0)
            _emit_u8(ctx, OP_SET_EXEC); ctx.emit_target(L("fnext"))
            ctx.mark_label(L(f"fi{i}"))
        ctx.mark_label(L("fnext"))
        _emit_u8(ctx, 0x0B); _emit_u16(ctx, L1); _emit_u8(ctx, 0x0B); _emit_u16(ctx, L2)
        _emit_u8(ctx, OP_SET_EXEC); ctx.emit_target(L("fl"))
        ctx.mark_label(L("fret"))
        _emit_u8(ctx, 0x1B)
        table_off = ctx.here()
        padded = items + [0] * (pages * rows - len(items)) + [0]
        for v in padded:
            _emit_u16(ctx, c(v))
        struct.pack_into("<H", ctx.code, table_ref, table_off)
        struct.pack_into("<H", ctx.code, read_ref, table_off)
    ctx.trailers.append(emit_trailer)


# --- generic primitives (Magian Moogle 10124 needed all of these) ------------------------
IF_KINDS_BY_NAME = {"ne": 0x00, "eq": 0x01, "le": 0x02, "ge": 0x03, "lt": 0x04, "gt": 0x05}


def _step_task(step, ctx: _Ctx):
    """``45 start_task <scene> <entA> <entB> <tag> <dur>`` with a scene id you name (the Magian
    intro plays 201 / "qstc", the key-item jingle, player -> player). The `camera` step is the
    editor's version that allocates a scene file; this one takes a retail scene id verbatim."""
    _emit_u8(ctx, 0x45); _emit_u16(ctx, _value_selector(ctx, step.get("scene", 0)))      # constant or register
    _emit_u32(ctx, _entity_spec(ctx, step.get("a", "player"))); _emit_u32(ctx, _entity_spec(ctx, step.get("b", "player")))
    ctx.code += _tag4(step) if (step.get("tag") or step.get("tagHex")) else b"qstc"
    _emit_u16(ctx, _value_selector(ctx, step.get("dur", 0)))


def _step_raw(step, ctx: _Ctx):
    """Verbatim opcode bytes. Plain hex is copied as is (entity ids, tags, sub-opcodes).
    A ``{sel}`` token stands for a 2-byte selector taken from ``sels`` (constants or
    register specs) and re-resolved against OUR references[], so the decompiler can carry
    any fixed-layout opcode losslessly: ``{"op":"raw","hex":"9d 00 9e 0a {sel} {sel}",
    "sels":[{"local":15},{"local":14}]}``. Retail openers stay literal: ``2e``, ``1e
    f0ffff7f``, ``79 00 f8ffff7f f0ffff7f``."""
    sels = list(step.get("sels") or [])
    for tok in str(step["hex"]).split():
        if tok == "{sel}":
            if not sels:
                raise CutsceneCompileError("raw: more {sel} tokens than sels")
            _emit_u16(ctx, _value_selector(ctx, sels.pop(0)))
        elif tok.startswith("{tbl:"):
            ctx.emit_target(tok[5:-1])          # absolute offset of a `table` trailer by label
        else:
            ctx.code += bytes.fromhex(tok)
    if sels:
        raise CutsceneCompileError("raw: unused sels")


def _step_table(step, ctx: _Ctx):
    """``table``: a 0x9D data table emitted as a trailer (past the final `end`, like subs).
    ``entries`` are constants or register specs, one u16 selector each, followed by a zero
    terminator (retail pads with zeros; the explainer stops at the first zero). Other steps
    reference it by ``label`` (``table_read`` / ``table_write``); the absolute offset is
    resolved like a jump target."""
    label = str(step["label"])
    entries = list(step.get("entries") or [])

    def emit_trailer(label=label, entries=entries):
        ctx.mark_label(label)
        for e in entries:
            _emit_u16(ctx, _value_selector(ctx, e))
        _emit_u16(ctx, 0)
    ctx.trailers.append(emit_trailer)


def _step_table_read(step, ctx: _Ctx):
    """``9D 00 <table> <dst> <index>``: dst = table[index] (Oseem's eligible-item and stone lists)."""
    _emit_u8(ctx, 0x9D); _emit_u8(ctx, 0x00); ctx.emit_target(str(step["table"]))
    _emit_u16(ctx, _work_selector(step["into"])); _emit_u16(ctx, _value_selector(ctx, step.get("index", 0)))


def _step_table_write(step, ctx: _Ctx):
    """``9D 05 <table> <value> <index>``: table[index] = value, for tables of REGISTER selectors
    (Oseem fills the 36-entry parameter table that its query rows read)."""
    _emit_u8(ctx, 0x9D); _emit_u8(ctx, 0x05); ctx.emit_target(str(step["table"]))
    _emit_u16(ctx, _value_selector(ctx, step.get("value", 0))); _emit_u16(ctx, _value_selector(ctx, step.get("index", 0)))


def _tag4(step, key="tag") -> bytes:
    """Four tag bytes: ``tag`` (ASCII) or ``tagHex`` when retail's tag is not printable."""
    if step.get(key + "Hex"):
        return bytes.fromhex(str(step[key + "Hex"])).ljust(4, bytes([0]))[:4]
    return str(step.get(key, "")).encode("ascii", "replace").ljust(4, b" ")[:4]


def _step_cancel(step, ctx: _Ctx):
    """``42`` cancel_set (``set: true``, the event cannot be escaped) / ``2E`` cancel_clr."""
    _emit_u8(ctx, OP_CANCEL_SET if step.get("set", True) else 0x2E)


def _step_look_talk(step, ctx: _Ctx):
    """``1E <entity>``: the event entity turns to ``target`` with the talking mouth (retail opener)."""
    _emit_u8(ctx, OP_LOOK_TALK); _emit_u32(ctx, _entity_spec(ctx, step.get("target", "player")))


def _step_turn_wait(step, ctx: _Ctx):
    """``6F`` (``phase: "sleep"``, a yieldable ~16-frame sleep) / ``70`` (``phase: "turn"``, yield
    while the entity's turn flag is set, then TurnCancel). Retail follows look_talk with both."""
    _emit_u8(ctx, 0x6F if str(step.get("phase", "sleep")) == "sleep" else 0x70)


def _step_look(step, ctx: _Ctx):
    """``79 <sub> <a> <b>``: entity ``a`` looks at ``b`` (sub 0 = self looks at the player in the
    retail opener)."""
    sub = int(step.get("sub", 0)) & 0xFF
    _emit_u8(ctx, 0x79); _emit_u8(ctx, sub)
    _emit_u32(ctx, _entity_spec(ctx, step.get("a", "self"))); _emit_u32(ctx, _entity_spec(ctx, step.get("b", "player")))
    if sub == 1:
        _emit_u16(ctx, _value_selector(ctx, step.get("value", 0)))   # sub 1 is 12 bytes


def _step_look_at(step, ctx: _Ctx):
    """``4A <a> <b>``: entity ``a`` faces ``b`` (what ``flags.facePlayer`` synthesises)."""
    _emit_u8(ctx, OP_LOOK_AT); _emit_u32(ctx, _entity_spec(ctx, step.get("a", "self"))); _emit_u32(ctx, _entity_spec(ctx, step.get("b", "player")))


def _step_companion(step, ctx: _Ctx):
    """``29 <a> <entity> <b>`` (REQEW, XiEvents "ReqSetWait"): ask ``entity`` to run the event in
    SLOT ``b`` of its own event offset table (a table index, not an event id) at priority
    ``a``, and wait for it to finish. Laityn's 10003 drives her walk, lines and camera cues
    this way (slots 10-26); the Magian Moogle's floating book is (0x010F30EB, 1, 9)."""
    _emit_u8(ctx, 0x29); _emit_u8(ctx, int(step.get("a", 0)) & 0xFF)
    _emit_u32(ctx, _entity_spec(ctx, step.get("entity", "self"))); _emit_u8(ctx, int(step.get("b", 0)) & 0xFF)


def _step_action(step, ctx: _Ctx):
    """``2C <a> <b> <tag>``: play own routine ``tag`` on entity ``a`` toward ``b`` (``anim`` is the
    self form; this one names the entities)."""
    _emit_u8(ctx, OP_SET_ACTION); _emit_u32(ctx, _entity_spec(ctx, step.get("a", "self")))
    _emit_u32(ctx, _entity_spec(ctx, step.get("b", "self"))); ctx.code += _tag4(step)


def _step_wait_task(step, ctx: _Ctx):
    """``53 <a> <b> <tag>``: wait for that scheduled routine to finish."""
    _emit_u8(ctx, OP_WAIT_TASK); _emit_u32(ctx, _entity_spec(ctx, step.get("a", "self")))
    _emit_u32(ctx, _entity_spec(ctx, step.get("b", "self"))); ctx.code += _tag4(step)


def _step_schedule(step, ctx: _Ctx):
    """``5B`` (``kind: "ext"``) / ``66`` (``kind: "ext2"``): ``<sel> <a> <b> <tag>`` schedule a
    gesture-bank routine (5B) or a per-actor motion package (66) by bank/file selector."""
    kind = str(step.get("kind", "ext"))
    _emit_u8(ctx, {"ext": OP_SCHED_EXT, "ext2": OP_SCHED_EXT2, "end": 0x52, "wait_main": OP_WAIT_SCHED}.get(kind, OP_SCHED_EXT))
    _emit_u16(ctx, _value_selector(ctx, step.get("sel", 0)))
    _emit_u32(ctx, _entity_spec(ctx, step.get("a", "self"))); _emit_u32(ctx, _entity_spec(ctx, step.get("b", "self"))); ctx.code += _tag4(step)


def _step_effect_bare(step, ctx: _Ctx):
    """``73 <id> <from> <to>`` with no injected waits (the decompiler's form of `effect`)."""
    _emit_u8(ctx, OP_EFFECT); _emit_u16(ctx, _value_selector(ctx, step.get("id", 0)))
    _emit_u32(ctx, _entity_spec(ctx, step.get("from", "self"))); _emit_u32(ctx, _entity_spec(ctx, step.get("to", "player")))


def _step_input_open(step, ctx: _Ctx):
    """``71 <sub> <sel>...``: open an input window (sub 0x10 number, 0x12 number with a second
    selector, 0x00 text); ``sels`` are the selectors in order."""
    _emit_u8(ctx, OP_INPUT); _emit_u8(ctx, int(step.get("sub", 0x10)) & 0xFF)
    for v in step.get("sels") or []:
        _emit_u16(ctx, _value_selector(ctx, v))


def _step_input_wait(step, ctx: _Ctx):
    """``71 <sub> <reg>``: wait for the input window and store the value (sub 0x11 / 0x13 / 0x01)."""
    sub = int(step.get("sub", 0x11)) & 0xFF
    _emit_u8(ctx, OP_INPUT); _emit_u8(ctx, sub)
    if "into" in step or sub in (0x11, 0x13):     # the number waits store a result; the text wait (01) is 2 bytes
        _emit_u16(ctx, _work_selector(step.get("into", {"work": 4})))


def _step_bits_mask(step, ctx: _Ctx):
    """``3F <dst> <a> <b>``: dst = a % b, 0 when either is 0 (XiEvents 0x3F; Oseem pages its lists
    with `mod L18 39 16`). Step name ``mod``; ``bits_mask`` is the older alias."""
    _emit_u8(ctx, 0x3F); _emit_u16(ctx, _work_selector(step["into"]))
    _emit_u16(ctx, _value_selector(ctx, step.get("a", 0))); _emit_u16(ctx, _value_selector(ctx, step.get("b", 0)))


def _step_nop(step, ctx: _Ctx):
    _emit_u8(ctx, 0x00)


def _step_wait_dismiss(step, ctx: _Ctx):
    _emit_u8(ctx, OP_WAIT_DISMISS)


def _step_wait_select(step, ctx: _Ctx):
    _emit_u8(ctx, OP_WAIT_SELECT)


def _step_server_wait(step, ctx: _Ctx):
    """``43 01`` alone: wait for the server's reply (the pair is `server_update`)."""
    _emit_u8(ctx, OP_NOTIFY_SERVER); _emit_u8(ctx, 0x01)


def _step_server_send(step, ctx: _Ctx):
    """``43 00`` alone: send the option without waiting."""
    _emit_u8(ctx, OP_NOTIFY_SERVER); _emit_u8(ctx, 0x00)


def _step_print2(step, ctx: _Ctx):
    """``2B <speaker> <msg>`` (+ ``23`` unless ``wait: false``): a line spoken by a named entity,
    the decompiler's exact form of a speaker line (``say`` with ``speaker`` adds the talk gesture)."""
    mid = ctx.dialog_ids.get(step.get("text"))
    if mid is None:
        raise CutsceneCompileError(f"print2: unknown line id {step.get('text')!r}")
    _emit_u8(ctx, OP_PRINT_MSG2); _emit_u32(ctx, _entity_spec(ctx, step.get("speaker", "self"))); _emit_u16(ctx, ctx.add_ref(mid))
    if step.get("wait", True):
        _emit_u8(ctx, OP_WAIT_DISMISS)


def _step_set_pos(step, ctx: _Ctx):
    """``1F <sub> [x z y]``: sub 0 moves the event entity to x/z/y (selectors, millimetres);
    other subs are the 2-byte forms."""
    sub = int(step.get("sub", 0)) & 0xFF
    _emit_u8(ctx, 0x1F); _emit_u8(ctx, sub)
    if sub == 0:
        for k in ("x", "z", "y"):
            _emit_u16(ctx, _value_selector(ctx, step.get(k, 0)))


def _step_set_pos4(step, ctx: _Ctx):
    """``37 <x> <z> <y> <dir>``: position + facing for the event's ExtData[1] entity (millimetres, dir/65536 turns)."""
    _emit_u8(ctx, 0x37)
    for k in ("x", "z", "y", "dir"):
        _emit_u16(ctx, _value_selector(ctx, step.get(k, 0)))


def _step_render_flag(step, ctx: _Ctx):
    """``4E <flag> <entity>``: flag 1 hides the entity, 0 shows it."""
    flag = int(step["flag"]) & 0xFF if "flag" in step else (1 if step.get("hide", False) else 0)
    _emit_u8(ctx, OP_RENDER_FLAG); _emit_u8(ctx, flag); _emit_u32(ctx, _entity_spec(ctx, step.get("entity", "self")))


def _step_load_wait(step, ctx: _Ctx):
    """``80 <entity>``: yield until the entity's model / action is loaded."""
    _emit_u8(ctx, OP_LOAD_WAIT); _emit_u32(ctx, _entity_spec(ctx, step.get("entity", "self")))


def _step_ui_flag(step, ctx: _Ctx):
    """``92 <flag> <entity>``: Render.Flags3 bit 0x10000 (name/UI visibility) on the entity."""
    _emit_u8(ctx, 0x92); _emit_u8(ctx, int(step.get("flag", 0)) & 0xFF); _emit_u32(ctx, _entity_spec(ctx, step.get("entity", "self")))


def _step_calibrate(step, ctx: _Ctx):
    """``BA <entity> <a> <b> <c> <d>``: calibrate the entity's position (four selectors)."""
    _emit_u8(ctx, OP_CALIBRATE_POS); _emit_u32(ctx, _entity_spec(ctx, step.get("entity", "self")))
    for v in (step.get("sels") or [0, 0, 0, 0])[:4]:
        _emit_u16(ctx, _value_selector(ctx, v))


def _step_mul(step, ctx: _Ctx):
    """``14 dst src``: dst *= value (retail pages: ``mul L9 16``)."""
    _emit_u8(ctx, 0x14); _emit_u16(ctx, _work_selector(step["into"])); _emit_u16(ctx, _value_selector(ctx, step.get("value", 1)))


def _step_shl(step, ctx: _Ctx):
    """``10 dst src``: dst <<= value (Oseem shifts a hide mask by one so row 0 stays visible)."""
    _emit_u8(ctx, 0x10); _emit_u16(ctx, _work_selector(step["into"])); _emit_u16(ctx, _value_selector(ctx, step.get("value", 1)))


def _step_or(step, ctx: _Ctx):
    """``0E dst src``: dst |= value (register or constant)."""
    _emit_u8(ctx, 0x0E); _emit_u16(ctx, _work_selector(step["into"])); _emit_u16(ctx, _value_selector(ctx, step.get("value", 0)))


def _step_add(step, ctx: _Ctx):
    """``07 dst src``: dst += value (register or constant)."""
    _emit_u8(ctx, 0x07); _emit_u16(ctx, _work_selector(step["into"])); _emit_u16(ctx, _value_selector(ctx, step.get("value", 0)))


def _step_if(step, ctx: _Ctx):
    """``02 a b kind target``: jump to ``to`` when the comparison holds. ``cmp``: ne (retail's
    "== else jump": jump when a != b), eq, le, ge, lt, gt. ``signed: true`` sets the 0x80
    kind flag retail uses on some local-register compares."""
    kind = IF_KINDS_BY_NAME[str(step.get("cmp", "ne")).lower()] | (0x80 if step.get("signed") else 0)
    if "kindRaw" in step:
        kind = int(step["kindRaw"]) & 0xFF          # verbatim kind byte (decompiled odd values)
    _emit_u8(ctx, OP_IF)
    _emit_u16(ctx, _value_selector(ctx, step.get("a", "menu_result")))
    _emit_u16(ctx, _value_selector(ctx, step.get("b", 0)))
    _emit_u8(ctx, kind); ctx.emit_target(str(step["to"]))


def _step_if_bit(step, ctx: _Ctx):
    """``3E reg bit target``: continue when bit ``bit`` of ``reg`` is set, else jump to ``else``."""
    _emit_u8(ctx, 0x3E); _emit_u16(ctx, _work_selector(step["reg"])); _emit_u16(ctx, _value_selector(ctx, step.get("bit", 0)))
    ctx.emit_target(str(step["else"]))


def _step_call(step, ctx: _Ctx):
    """``1A target``: call a ``sub``."""
    _emit_u8(ctx, OP_CALL); ctx.emit_target(str(step["to"]))


def _step_return(step, ctx: _Ctx):
    _emit_u8(ctx, 0x1B)


def _step_sub(step, ctx: _Ctx):
    """A subroutine: its ``steps`` are emitted as a trailer after the event's ``end`` (reached
    only through ``call``) and close with ``1B``. Labels inside are global (``goto`` works
    across), so a sub can re-enter itself the way the Magian info routine does."""
    label = str(step.get("label") or step.get("name"))
    body = list(step.get("steps") or [])
    if not label or not body:
        raise CutsceneCompileError("sub needs a label and steps")

    def emit_trailer(label=label, body=body):
        ctx.mark_label(label)
        for st in body:
            emit = STEP_DISPATCH.get(st["op"])
            if emit is None:
                raise CutsceneCompileError(f"unknown step op in sub {label!r}: {st['op']!r}")
            if st.get("label"):
                ctx.mark_label(str(st["label"]))
            emit(st, ctx)
        if body[-1].get("op") != "return":
            _emit_u8(ctx, 0x1B)
    ctx.trailers.append(emit_trailer)


def _step_load_zone(step, ctx: _Ctx):
    """``0x34 <zoneSel>`` loads another zone's graphics for the scene (full swap, with
    XiZone::Close); ``restore: true`` emits ``0x35`` (overlay / restore the previous zone).
    Documented in XiEvents; untested here."""
    _emit_u8(ctx, 0x35 if step.get("restore") else OP_LOAD_ZONE)
    _emit_u16(ctx, _value_selector(ctx, int(step["zoneId"])))


def _step_item_info(step, ctx: _Ctx):
    """``0x93 <sel>``: open the item description window for an item id (constant or a
    register such as {"param": 2}); ``"close"`` / 0 closes it. Isakoth shows the item this
    way before "Are you sure?"."""
    item = step.get("item", "close")
    sel = ctx.add_ref(0) if item in ("close", None, 0) else _value_selector(ctx, item)
    _emit_u8(ctx, OP_ITEM_INFO); _emit_u16(ctx, sel)


def _step_number_input(step, ctx: _Ctx):
    """Numeric input window (0x71). Surveyed across all 294 zones (1,954 sized uses):
    ``71 12 <style> <digits>`` — ``digits`` is the maximum input length (2 for "two-digit
    combination" / "one to twelve", 3 for "up to 999", 4 for "quality 1-1000", 9 for the
    Mog Garden gil deposit), ``style`` is 1 in every retail use (the client passes
    style + 1 to the window). ``mode: "plain"`` is the older ``71 10 <style>`` form (705
    retail uses, all "enter the quantity to trade", window fixed at 8 digits).
    Then ``71 13`` / ``71 11 <dst>`` waits and stores ``atoi(input)`` in ``result``
    (default Work_Zone[4]); forward it with ``set_result {"from": {"work": 4}}``."""
    mode = step.get("mode", "sized")
    style = _value_selector(ctx, step.get("style", step.get("p1", 1)))
    if mode == "plain":
        _emit_u8(ctx, OP_INPUT); _emit_u8(ctx, 0x10); _emit_u16(ctx, style)
        wait_sub = 0x11
    else:
        digits = _value_selector(ctx, step.get("digits", step.get("p2", 2)))
        _emit_u8(ctx, OP_INPUT); _emit_u8(ctx, 0x12); _emit_u16(ctx, style); _emit_u16(ctx, digits)
        wait_sub = 0x13
    dst = _work_selector(step.get("result", {"work": 4}))
    _emit_u8(ctx, OP_INPUT); _emit_u8(ctx, wait_sub); _emit_u16(ctx, dst)


def _step_text_input(step, ctx: _Ctx):
    """Password/text window: ``71 00`` opens it, ``71 01`` waits; the client sends the
    text to the server in packet 0x060 (LSB ``0x060_passwards``). Untested in game."""
    _emit_u8(ctx, OP_INPUT); _emit_u8(ctx, 0x00)
    _emit_u8(ctx, OP_INPUT); _emit_u8(ctx, 0x01)


def _step_bits(step, ctx: _Ctx, setting: bool):
    """``bits_set``: dst bits[lo..hi] = src << lo (0x40, keeps the other bits) — how retail
    packs ``category | qty << 10 | selection << 16`` into the result.
    ``bits_get``: dst = (src bits[lo..hi]) >> lo (0x41) — how Home Points unpack a server
    bitmask into per-menu registers."""
    lo = _value_selector(ctx, step.get("lo", 0))
    hi = _value_selector(ctx, step.get("hi", 31))
    if setting:
        dst = _work_selector(step.get("into", "result"))
        src = _value_selector(ctx, step["from"])
        _emit_u8(ctx, OP_BITS_SET); _emit_u16(ctx, lo); _emit_u16(ctx, hi); _emit_u16(ctx, dst); _emit_u16(ctx, src)
    else:
        src = _work_selector(step["from"])
        dst = _work_selector(step["into"])
        _emit_u8(ctx, OP_BITS_GET); _emit_u16(ctx, lo); _emit_u16(ctx, hi); _emit_u16(ctx, src); _emit_u16(ctx, dst)


_shop_counter = [0]


def _step_shop(step, ctx: _Ctx):
    """Retail-shaped currency shop — see :mod:`xi.event.xi_shop`. Emits::

        dispatcher (category menu, cursor memory, loop)   ← this event's code
        cloned Isakoth page routine (relocated)           ← called with 0x1A
        row-register table + per-category item/price tables

    and continues after the tables (a following ``end`` closes the event). Server side:
    ``startEvent(csid, 0, balance, 0, 0, 0, limit)`` and ``updateEvent(balance, 0, 0, 0, 0, limit)``
    after each purchase; the Lua stub in the compile result carries the price table."""
    cats = step.get("categories") or []
    if not (1 <= len(cats) <= xi_shop.MAX_CATEGORIES):
        raise CutsceneCompileError(f"shop needs 1..{xi_shop.MAX_CATEGORIES} categories")
    for c in cats:
        if not c.get("items"):
            raise CutsceneCompileError(f"shop category {c.get('name')!r} has no items")
    if ctx.ffxi_dir is None:
        raise CutsceneCompileError("shop step needs the game folder (FFXI_DIR) to clone the retail page routine")
    tpl = xi_shop.load_template(ctx.ffxi_dir)

    cur = step.get("currency") or {}
    name = str(cur.get("name", "gil"))
    singular = str(cur.get("singular", name[:-1] if name.endswith("s") else name))
    short = str(cur.get("short", (name[:3] + ".") if len(name) > 4 else name))
    plural_s = bool(cur.get("pluralS", name.endswith("s")))
    label = str(cur.get("label", name[:1].upper() + name[1:]))

    # 1. strings: category menu (ours) + the four cloned retail strings
    blobs = xi_shop.shop_strings(tpl, name, singular, short, plural_s, step.get("texts"), label=label)
    cat_blob = xi_shop.category_menu_blob(str(step.get("question", "Exchange for what?")), label, cats,
                                          str(step.get("noneText", "None.")))
    ctx.dialog_out, ids = xi_author.append_dialog_blobs(
        ctx.dialog_out, [cat_blob, blobs["page"], blobs["qty"], blobs["confirm"], blobs["yesno"]])
    dialog_map = {xi_shop.TEMPLATE_STRINGS["page"]: ids[1], xi_shop.TEMPLATE_STRINGS["qty"]: ids[2],
                  xi_shop.TEMPLATE_STRINGS["confirm"]: ids[3], xi_shop.TEMPLATE_STRINGS["yesno"]: ids[4]}

    n = _shop_counter[0]; _shop_counter[0] += 1
    L = lambda name_: f"__shop{n}_{name_}"

    # 2. dispatcher
    bal = _work_selector({"param": int(step.get("balanceParam", 1))})
    _emit_u8(ctx, OP_STORE); _emit_u16(ctx, xi_shop.L_BALANCE); _emit_u16(ctx, bal)
    if "limitParam" in step:
        lim = _work_selector({"param": int(step["limitParam"])})
    else:
        lim = ctx.add_ref(int(step.get("limit", 999999)))
    _emit_u8(ctx, OP_STORE); _emit_u16(ctx, xi_shop.L_LIMIT); _emit_u16(ctx, lim)
    _emit_u8(ctx, OP_STORE_ONE); _emit_u16(ctx, xi_shop.L_LOOP)
    _emit_u8(ctx, OP_STORE_ZERO); _emit_u16(ctx, xi_shop.L_CURSOR)
    _emit_u8(ctx, OP_STORE_ZERO); _emit_u16(ctx, xi_shop.L_MENU_FLAGS)
    ctx.mark_label(L("loop"))
    _emit_u8(ctx, OP_STORE); _emit_u16(ctx, xi_shop.WZ_BALANCE_DISPLAY); _emit_u16(ctx, xi_shop.L_BALANCE)
    _emit_u8(ctx, OP_STORE); _emit_u16(ctx, xi_shop.WZ1700_LIMIT_DISPLAY); _emit_u16(ctx, xi_shop.L_LIMIT)
    _emit_u8(ctx, OP_MENU); _emit_u16(ctx, ctx.add_ref(ids[0])); _emit_u16(ctx, xi_shop.L_CURSOR); _emit_u16(ctx, xi_shop.L_MENU_FLAGS)
    _emit_u8(ctx, OP_WAIT_SELECT)
    for k in range(len(cats)):
        _emit_u8(ctx, OP_IF); _emit_u16(ctx, WORK_MENU_RESULT); _emit_u16(ctx, ctx.add_ref(k))
        _emit_u8(ctx, IF_KIND_JUMP_IF_EQUAL); ctx.emit_target(L(f"cat{k}"))
    _emit_u8(ctx, OP_SET_EXEC); ctx.emit_target(L("done"))
    for k in range(len(cats)):
        ctx.mark_label(L(f"cat{k}"))
        _emit_u8(ctx, OP_STORE); _emit_u16(ctx, xi_shop.L_CURSOR); _emit_u16(ctx, WORK_MENU_RESULT)
        _emit_u8(ctx, OP_STORE); _emit_u16(ctx, xi_shop.L_CATEGORY); _emit_u16(ctx, ctx.add_ref(k))
        _emit_u8(ctx, OP_CALL); ctx.emit_target(L("routine"))
        _emit_u8(ctx, OP_IF); _emit_u16(ctx, xi_shop.L_LOOP); _emit_u16(ctx, ctx.add_ref(0))
        _emit_u8(ctx, IF_KIND_JUMP_IF_EQUAL); ctx.emit_target(L("done"))
        _emit_u8(ctx, OP_SET_EXEC); ctx.emit_target(L("loop"))

    ctx.mark_label(L("done"))
    ctx.shop_stubs.append((step, None))

    # 3. routine, row table and per-category item + price tables are emitted as a
    #    TRAILER after the event's final `end`: they are only reached through the 0x1A
    #    call, and keeping them past `end` means the message-id extractor (re-publish
    #    slot reuse) and the disassembler never read table data as opcodes.
    def emit_trailer(cats=cats, tpl=tpl, dialog_map=dialog_map, L=L):
        _emit_shop_trailer(ctx, cats, tpl, dialog_map, L)
    ctx.trailers.append(emit_trailer)


def _emit_shop_trailer(ctx: _Ctx, cats, tpl, dialog_map, L):
    routine_base = ctx.here()
    ctx.mark_label(L("routine"))
    pos = routine_base + len(tpl.routine)
    table_map = {xi_shop.ROWTABLE_START: pos}
    pos += len(tpl.rowtable)
    cat_layout = []
    for k in range(xi_shop.MAX_CATEGORIES):
        items = [int(it["id"]) for it in cats[k]["items"]] if k < len(cats) else []
        prices = [int(it["price"]) for it in cats[k]["items"]] if k < len(cats) else []
        items_off = pos; pos += 2 * xi_shop.table_len(len(items))
        prices_off = pos; pos += 2 * xi_shop.table_len(len(prices))
        old_items, old_prices = tpl.category_tables[k]
        table_map[old_items] = items_off
        table_map[old_prices] = prices_off
        cat_layout.append((items, prices))
    if pos > 0xFFFF:
        raise CutsceneCompileError("shop tables would exceed the u16 scene offset range")

    def sel_map(old_sel: int, is_message: bool = False) -> int:
        idx = old_sel & 0x7FFF
        if idx >= len(tpl.refs):
            raise CutsceneCompileError(f"template selector 0x{old_sel:04x} outside Isakoth's references[]")
        value = tpl.refs[idx]
        if is_message:
            if value not in dialog_map:
                raise CutsceneCompileError(f"template prints message {value} which is not one of the four cloned strings")
            value = dialog_map[value]
        return ctx.add_ref(value)

    ctx.code += xi_shop.relocate_routine(tpl, routine_base, sel_map, table_map)
    assert ctx.here() == table_map[xi_shop.ROWTABLE_START]
    ctx.code += tpl.rowtable
    for items, prices in cat_layout:
        ctx.code += xi_shop.table_bytes(items, ctx.add_ref)
        ctx.code += xi_shop.table_bytes(prices, ctx.add_ref)
    assert ctx.here() == pos


def _step_server_update(step, ctx: _Ctx):
    """Mid-event round-trip: optional ``result`` → Work_Zone[1], then ``43 00`` sends
    packet 0x05B mode 1 (LSB: ``onEventUpdate(player, csid, option)``) and, unless
    ``wait`` is false, ``43 01`` blocks until the server answers (``player:updateEvent``
    / ``0x05C``). Retail Ru'Lude: ``get_store · 43 00 · 43 01 · if Work_Zone[2] ...``."""
    if "result" in step:
        _emit_store(ctx, WORK_EVENT_RESULT, int(step["result"]))
    _emit_u8(ctx, OP_NOTIFY_SERVER); _emit_u8(ctx, 0x00)
    if step.get("wait", True):
        _emit_u8(ctx, OP_NOTIFY_SERVER); _emit_u8(ctx, 0x01)


def _step_end(step, ctx: _Ctx):
    """``0x21 end``; with ``result`` the value is stored in Work_Zone[1] first so
    ``onEventFinish(player, csid, option)`` sees it. A held gesture pose is closed first."""
    if getattr(ctx, "cinematic", True):
        _close_open_gesture(ctx)
    if "result" in step:
        _emit_store(ctx, WORK_EVENT_RESULT, int(step["result"]))
    if END_LABEL not in ctx.labels:
        ctx.mark_label(END_LABEL)      # early `end`s jump HERE, past this step's own store
    _emit_u8(ctx, OP_END)


END_LABEL = "__end"


def _normalize_ends(steps: list[dict]) -> list[dict]:
    """Retail events have ONE ``0x21 end``; a branch that finishes early stores its
    result and ``set_exec``s to it (Ru'Lude 10196: ``get_store · set_exec f06d``). Rewrite
    every ``end`` that is not the last step into ``set_result`` (when it carries one) +
    ``goto __end``; the final ``end`` (or the auto epilogue) owns the ``__end`` label.
    Keeps the decoder happy too — it stops disassembling at the first ``end``."""
    out: list[dict] = []
    last = max((i for i, st in enumerate(steps) if st.get("op") != "sub"), default=len(steps) - 1)
    for i, step in enumerate(steps):
        if step.get("op") == "end" and i != last:
            if "result" in step:
                out.append({"op": "set_result", "value": int(step["result"]),
                            "label": step.get("label")} if step.get("label")
                           else {"op": "set_result", "value": int(step["result"])})
                out.append({"op": "goto", "to": END_LABEL})
            else:
                out.append({"op": "goto", "to": END_LABEL, **({"label": step["label"]} if step.get("label") else {})})
            continue
        out.append(step)
    return out


def _resolve_fixups(ctx: _Ctx) -> None:
    """Backpatch every forward jump recorded by :meth:`_Ctx.emit_target`."""
    for pos, label in ctx.fixups:
        off = ctx.labels.get(label)
        if off is None:
            raise CutsceneCompileError(f"jump to unknown step label: {label!r}")
        if off > 0xFFFF:
            raise CutsceneCompileError(f"label {label!r} offset {off} exceeds the u16 jump range")
        struct.pack_into("<H", ctx.code, pos, off)
    ctx.fixups.clear()


MENU_LINE_SUFFIX = "__menu"
OPTION_MARK = "\x0b"        # control byte before the first option row; cp932-encodes to 0x0B unchanged


def _iter_steps(steps):
    """Steps in document order, descending into ``sub`` bodies."""
    for st in steps:
        yield st
        if st.get("op") == "sub":
            yield from _iter_steps(st.get("steps") or [])


def _inject_menu_lines(cutscene: dict, steps: list[dict]) -> dict:
    """Give every ``menu`` step its prompt string. Retail keeps the question and the
    options in ONE dialog entry separated by newlines (Ru'Lude 12550:
    ``"Do you believe you have what it takes?\\nWithout a doubt!\\nNot at the moment..."``).
    The step's ``text`` names the question line; ``options`` are appended to it as a new
    line ``<text>__menu[N]`` and the step is pointed at it via ``_menu_line``. Returns a
    shallow copy of ``cutscene`` whose ``dialog.lines`` carries the extra entries."""
    menus = [s for s in _iter_steps(steps) if s.get("op") == "menu" and "textFrom" not in s]
    if not menus:
        return cutscene
    dialog = cutscene.get("dialog")
    if not isinstance(dialog, dict) or "lines" not in dialog:
        raise CutsceneCompileError("menu steps need an inline cutscene.dialog with lines")
    lines = [dict(e) for e in dialog["lines"]]
    by_id = {e["id"]: e for e in lines}
    used = set(by_id)
    # Line ids other steps print. A question line used ONLY by its menu is rewritten in
    # place (same id, combined text) so the dialog table keeps one entry per line and
    # re-publish slot reuse stays positional; a shared line gets a separate `<id>__menu`.
    printed = {st.get("text") for st in steps if st.get("op") in ("say", "narrate")}
    taken: set[str] = set()
    # The question text of every line BEFORE any in-place rewrite: two menus that share a
    # question ("Select your {index:8}[first/second] augment:" is used twice by the coffer
    # flow) must each get the question + their own rows, not the question + the first
    # menu's rows + their own (that made the second menu 26 rows and "Accuracy" row 23).
    original_text = {e["id"]: e.get("text") for e in lines}
    for step in menus:
        step.pop("_menu_line", None)              # recompute: step dicts may be compiled more than once
        options = step.get("options") or []
        # retail has one-row menus ("Push!", Ru'Lude 12432) and even row-less stubs whose string
        # is only the prompt (Abyssea storyteller 201): both compile to exactly their bytes
        src = by_id.get(step.get("text"))
        if src is None:
            raise CutsceneCompileError(f"menu step references unknown line id: {step.get('text')!r}")
        question = original_text.get(step.get("text"), src["text"])
        if isinstance(question, list):
            question = question[-1]
        question = str(question)                    # trailing spaces are retail content (Adoulin 562)
        if question.rstrip().endswith("\\v"):
            question = question.rstrip()[:-2]
        # Retail layout (every menu string checked: Ru'Lude 12550/12558/12560, South Gustaberg
        # 7869/7870/10952): question, newline, then 0x0B = "option rows start" (PS2 CodeQUERY
        # case 11 sets the flag; rows before it are comment lines, rows after it are items).
        # an empty question (retail 8500 travel menu) starts straight at the rows
        if options:
            sep = "" if question.rstrip(" ").endswith("\\n") else "\\n"   # retail may end the question with a newline + spaces (Port Jeuno 396)
            text = (question + sep if question else "") + OPTION_MARK + "\\n".join(str(o) for o in options)   # an empty question (retail 8500) starts at the rows
        else:
            text = question                        # no rows: the string is the question (or nothing) plus the prompt
        if step["text"] not in printed and step["text"] not in taken:
            src["text"] = text                      # rewrite the question line in place (copied dict)
            taken.add(step["text"])
            step["_menu_line"] = step["text"]
            continue
        new_id = f"{step['text']}{MENU_LINE_SUFFIX}"
        k = 1
        while new_id in used:
            k += 1
            new_id = f"{step['text']}{MENU_LINE_SUFFIX}{k}"
        used.add(new_id)
        lines.append({"id": new_id, "text": text})
        step["_menu_line"] = new_id
    out = dict(cutscene)
    out["dialog"] = dict(dialog, lines=lines)
    return out


def _step_not_implemented(name: str) -> Callable:
    def _emit(step, ctx: _Ctx):
        raise NotImplementedError(
            f"step op {name!r} is not implemented yet — see xi_compile.py TODOs.")
    return _emit


def _step_typed(step, ctx: _Ctx):
    """Fixed-layout opcodes from xi_typed.TYPED: the fields are written in table order."""
    found = xi_typed.opcode_for(str(step["op"]), step)
    if found is None:
        raise CutsceneCompileError(f"no typed form for step {step.get('op')!r}")
    op, fields = found
    _emit_u8(ctx, op)
    for fld in fields:
        fname, kind = fld[0], fld[1]
        if kind == "bytes":
            n = int(fld[2])
            ctx.code += bytes.fromhex(str(step.get(fname, "")).replace(" ", "")).ljust(n, bytes([0]))[:n]
        elif kind == "u8":
            _emit_u8(ctx, int(step.get(fname, 0)) & 0xFF)
        elif kind == "u16":
            _emit_u16(ctx, int(step.get(fname, 0)) & 0xFFFF)
        elif kind == "sel":
            _emit_u16(ctx, _value_selector(ctx, step.get(fname, 0)))
        elif kind == "reg":
            _emit_u16(ctx, _work_selector(step.get(fname, {"work": 0})))
        elif kind == "ent":
            _emit_u32(ctx, _entity_spec(ctx, step.get(fname, "self")))
        elif kind == "tbl":
            ctx.emit_target(str(step[fname]))            # absolute offset of the table trailer
        elif kind == "msg":
            v = step.get(fname)
            mid = ctx.dialog_ids.get(v) if isinstance(v, str) else None
            _emit_u16(ctx, ctx.add_ref(mid) if mid is not None else _value_selector(ctx, v))
        elif kind == "tag":
            ctx.code += _tag4(step, fname)
        elif kind == "name16":
            if step.get(fname + "Hex"):
                ctx.code += bytes.fromhex(str(step[fname + "Hex"])).ljust(16, bytes([0]))[:16]
            else:
                ctx.code += str(step.get(fname, "")).encode("latin1", "replace").ljust(16, bytes([0]))[:16]


STEP_DISPATCH: dict[str, Callable] = {
    "wait":          _step_wait,
    "camera":        _step_camera,
    "fade":          _step_fade,
    "say":           _step_say,
    "narrate":       _step_narrate,
    "face":          _step_face,
    "show":          lambda s, c: _step_show_hide(s, c, show=True),
    "hide":          lambda s, c: _step_show_hide(s, c, show=False),
    "place":         _step_place,
    "music":         _step_music,
    "music_volume":  _step_music_volume,
    "end":           _step_end,
    "anim":          _step_anim,              # 0x5B gesture — standalone (dialog-less) emote
    "menu":          _step_menu,              # 0x24 + 0x25 — prompt with options
    "branch":        _step_branch,            # 0x02 case table on Work_Zone[n]
    "goto":          _step_goto,              # 0x01 set_exec to a label
    "set_result":    _step_set_result,        # Work_Zone[1] = value (the server's `option`)
    "server_update": _step_server_update,     # 0x43 00 / 0x43 01 round-trip (onEventUpdate)
    "item_info":     _step_item_info,         # 0x93 item description window
    "say_indexed":   _step_say_indexed,       # print (base + register): one of N strings picked by the server
    "number_input":  _step_number_input,      # 0x71 numeric window → register
    "text_input":    _step_text_input,        # 0x71 text window (packet 0x060)
    "bits_set":      lambda st, c: _step_bits(st, c, True),    # 0x40 pack a bit range
    "bits_get":      lambda st, c: _step_bits(st, c, False),   # 0x41 unpack a bit range
    "shop":          _step_shop,              # retail-shaped currency shop (cloned Isakoth page)
    "effect":        _step_effect,            # 0x73 spell/ability visual between two entities
    "load_zone":     _step_load_zone,         # 0x34 / 0x35 zone graphics swap for a scene
    "augment_window": _step_augment_window,  # 0xCC 01 item window with augment words (coffer preview)
    "store":         _step_store,             # 0x03 any register = value / register
    "item_list":     _step_item_list,         # paged item picker with preview + take/leave (Splintery Chest)
    "raw":           _step_raw,               # verbatim bytes (no selectors)
    "row_item":      _step_row_item,          # D4 03 item on a query row
    "mul":           _step_mul,               # 0x14 dst *= value
    "print2": _step_print2,
    "set_pos": _step_set_pos,
    "set_pos4": _step_set_pos4,
    "render_flag": _step_render_flag,
    "load_wait": _step_load_wait,
    "ui_flag": _step_ui_flag,
    "calibrate": _step_calibrate,
    "cancel": _step_cancel,
    "look_talk": _step_look_talk,
    "turn_wait": _step_turn_wait,
    "look": _step_look,
    "look_at": _step_look_at,
    "companion": _step_companion,
    "action": _step_action,
    "wait_task": _step_wait_task,
    "schedule": _step_schedule,
    "effect_bare": _step_effect_bare,
    "input_open": _step_input_open,
    "input_wait": _step_input_wait,
    "bits_mask": _step_bits_mask,
    "mod": _step_bits_mask,
    "nop": _step_nop,
    "wait_dismiss": _step_wait_dismiss,
    "wait_select": _step_wait_select,
    "server_wait": _step_server_wait,
    "server_send": _step_server_send,
    "table":         _step_table,             # 0x9D data table trailer
    "table_read":    _step_table_read,        # 9D 00 dst = table[index]
    "table_write":   _step_table_write,       # 9D 05 table[index] = value
    "shl":           _step_shl,               # 0x10 dst <<= value
    "augment_preview": _step_augment_preview, # D4 05 exdata preview window
    "task":          _step_task,              # 0x45 scheduler task by scene id
    "or":            _step_or,                # 0x0E dst |= value
    "add":           _step_add,               # 0x07 dst += value
    "if":            _step_if,                # 0x02 with any comparison kind
    "if_bit":        _step_if_bit,            # 0x3E jump unless a bit is set
    "call":          _step_call,              # 0x1A
    "return":        _step_return,            # 0x1B
    "sub":           _step_sub,               # subroutine body in the trailer
    "if_equal":      _step_if_equal,          # 0x02 kind 1 jump when two values match
    "set_bit":       lambda st, c: _step_set_bit(st, c, True),    # 0x3C flag bit in a local
    "clear_bit":     lambda st, c: _step_set_bit(st, c, False),   # 0x3D
}
for _typed_name in xi_typed.NAMES:
    STEP_DISPATCH[_typed_name] = _step_typed          # retail-shaped fixed-layout opcodes


# ---------------------------------------------------------------------------
# Camera scene-DAT writer
# ---------------------------------------------------------------------------
#
# A scene resource DAT is concatenated 16-byte-header sections (16-aligned, NO
# ToC — the reader walks by the meta size): evte(0x01) + Route(0x06) sections +
# EffectRoutine(0x07) sections + end(0x00). Reverse-engineered byte-for-byte from
# Balasiel's camera scene (event 627 → file 30834 = ROM/62/82.DAT); see
# docs/events/scene_dat_writer.md and parse_camera_routes / parse_effect_routines.
#   • Route     = the camera path: 1 keyframe = a STILL (fixed cam), 2+ = a SPLINE
#                 (glide eye/look/FOV over the shot duration at normalized times).
#   • Routine   = the "shot" that fires a route; a FIXED 144-byte template whose
#                 only variables are name / total-frames / delay+dur / route ref.

# 0x45 scene ref p → file 30704+_datid_helper(p). Custom cameras: p MUST be 300..599
# (file 56941..57240). p≥600 / 71k file ids crash the client — docs/events/camera_scene_ids.md
CAMERA_SCENE_DATID_BASE = 30704


def _scene_header(name: str, typecode: int, total_size: int) -> bytes:
    """A 16-byte scene-section header. ``meta = (size/16 << 7) | typeCode``; body
    follows and starts at +0x10. ``total_size`` includes this header + must be
    16-aligned (every section shape we emit is)."""
    if total_size % 16:
        raise CutsceneCompileError(f"scene section {name!r} size {total_size} not 16-aligned")
    meta = encode_section_meta(total_size, typecode, what=f"scene section {name!r}")
    return name.encode("ascii")[:4].ljust(4, b"\x00") + struct.pack("<I", meta) + b"\x00" * 8


def _sanitize_look(eye: list[float], look: list[float], dist: float = 2.5) -> list[float]:
    """Pull look-at to ~``dist`` units from eye when the authored point is far away.

    Retail routes keep look ≈ 2 units from eye. The editor used to store
    eye+forward×100 (~100m targets); those custom scenes crash the client on load.
    Direction is preserved; only distance is clamped."""
    dx = float(look[0]) - float(eye[0])
    dy = float(look[1]) - float(eye[1])
    dz = float(look[2]) - float(eye[2])
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if length < 1e-4:
        return [float(eye[0]), float(eye[1]), float(eye[2]) - dist]
    if 0.5 <= length <= 8.0:
        return [float(look[0]), float(look[1]), float(look[2])]
    s = dist / length
    return [float(eye[0]) + dx * s, float(eye[1]) + dy * s, float(eye[2]) + dz * s]


def _build_route(name: str, keyframes: list[dict], interp_mode: int) -> bytes:
    """0x06 Route — 32-byte body header (count @+0x10, interpMode @+0x14) + N×48B
    keyframes (eye vec3 + focal length, look vec3 + roll rad, time 0..1, 12B pad)."""
    if not keyframes:
        raise CutsceneCompileError(f"route {name!r} has no keyframes")
    body = bytearray(32)
    struct.pack_into("<I", body, 0x10, len(keyframes))
    struct.pack_into("<I", body, 0x14, int(interp_mode) & 0xFFFFFFFF)
    for kf in keyframes:
        ex, ey, ez = kf["eye"]
        lx, ly, lz = _sanitize_look(kf["eye"], kf["look"])
        chunk = struct.pack("<4f4f f", float(ex), float(ey), float(ez), float(kf["fov"]),
                            lx, ly, lz, float(kf.get("roll", 0.0)),
                            float(kf.get("time", 0.0)))
        body += chunk + b"\x00" * 12          # 32 + 4 + 12 = 48
    total = 16 + len(body)
    return _scene_header(name, 0x06, total) + bytes(body)


def _build_routine(name: str, route_tag: str, total_frames: int,
                   hold_route: Optional[str] = None) -> bytes:
    """0x07 EffectRoutine — the "play this route" template. Byte-identical to retail
    ca06 (still, total=0) / s075 (move, total=180) except name/total/delay/dur/route-ref.
    sec2 = ``01 02``(begin) · ``04 06 <delay><dur> <route>``(play route) · ``00 02``(end).

    ★ ``hold_route`` chains a second ``04`` command: a 1-keyframe STILL of the move's end
    pose, firing AT the move's end with dur 0. The client's CameraTask DELETES ITSELF when
    its duration runs out (CameraTask::OnMove `delete this`) — a bare move therefore drops
    the camera back to the client's default framing the moment the pan completes, which is
    exactly when a dialogue hold parks the bytecode ("tiger low-left + zoomed out" bug: the
    in-game view was the DEFAULT camera at focal 350, not our route). A dur-0 still rides
    ``CameraResource::Play``'s Locked/duration==0 path — ApplyCameraSettings ONCE, **no
    task created, nothing to expire** — retail's own hold idiom (Balasiel ca06, and the
    chained two-command pattern in cm05/ca00/cm04: ``04 (1,0) still · 04 (200,200) move``,
    total padded ONE frame past the last command → we mirror move-then-hold, total+1)."""
    dur = int(total_frames) & 0xFFFF
    if hold_route is None:
        sec = bytearray(0x90)                                   # 144 bytes
        sec[0:16] = _scene_header(name, 0x07, 0x90)
        struct.pack_into("<4I", sec, 0x20, 0x40, 0x50, 0x80, int(total_frames) & 0xFFFFFFFF)
        sec[0x40] = 0x00; sec[0x41] = 0x01                     # sec1
        sec[0x50] = 0x01; sec[0x51] = 0x02                     # sec2 cmd1: begin
        sec[0x58] = 0x04; sec[0x59] = 0x06                     # sec2 cmd2: PLAY ROUTE (6 dwords)
        struct.pack_into("<H", sec, 0x5C, dur)                # delay
        struct.pack_into("<H", sec, 0x5E, dur)                # dur
        sec[0x60:0x64] = route_tag.encode("ascii")[:4].ljust(4, b"\x00")
        sec[0x70] = 0x00; sec[0x71] = 0x02                     # sec2 cmd3: end
        sec[0x80] = 0x00; sec[0x81] = 0x01                     # sec3
        return bytes(sec)
    sec = bytearray(0xA0)                                       # 160 bytes (one extra command)
    sec[0:16] = _scene_header(name, 0x07, 0xA0)
    struct.pack_into("<4I", sec, 0x20, 0x40, 0x50, 0x90, (dur + 1) & 0xFFFFFFFF)  # retail +1 pad
    sec[0x40] = 0x00; sec[0x41] = 0x01                         # sec1
    sec[0x50] = 0x01; sec[0x51] = 0x02                         # sec2 cmd1: begin
    sec[0x58] = 0x04; sec[0x59] = 0x06                         # sec2 cmd2: PLAY ROUTE (move)
    struct.pack_into("<H", sec, 0x5C, dur)                    # delay
    struct.pack_into("<H", sec, 0x5E, dur)                    # dur
    sec[0x60:0x64] = route_tag.encode("ascii")[:4].ljust(4, b"\x00")
    sec[0x70] = 0x04; sec[0x71] = 0x06                         # sec2 cmd3: PLAY ROUTE (hold still)
    struct.pack_into("<H", sec, 0x74, dur)                    # fires as the move completes
    struct.pack_into("<H", sec, 0x76, 0)                      # dur 0 → apply-once, task-less hold
    sec[0x78:0x7C] = hold_route.encode("ascii")[:4].ljust(4, b"\x00")
    sec[0x88] = 0x00; sec[0x89] = 0x02                         # sec2 cmd4: end
    sec[0x90] = 0x00; sec[0x91] = 0x01                         # sec3
    return bytes(sec)


def build_scene_resource(shots: list[dict],
                         reset_zoom: bool = False) -> tuple[bytes, list[str], Optional[str]]:
    """Assemble a camera scene resource DAT from lowered shots.

    ``shots[i]`` = ``{keyframes: [{eye:[x,y,z], look:[x,y,z], fov:focal, roll,
    time:0..1}], interpMode:int, duration:frames}``. 1 keyframe → a still cut;
    2+ → a glide. Returns ``(scene_dat_bytes, routine_tags, reset_tag)`` where
    ``routine_tags[i]`` is the FourCC the event's ``0x45 start_task`` fires for
    shot ``i`` (route ``cNNN`` / routine ``sNNN``, matching retail convention).

    ``reset_zoom`` appends a ``zres``/``zrs0`` dur-0 still at the LAST shot's end pose
    with focal 350 (the client default — GameManager::InitializeProjection). Camera
    routes poke the GLOBAL projection focal and nothing restores it at event end, so
    without this the player keeps the cutscene zoom after the scene. The epilogue
    fires ``reset_tag`` under the final black, just before ``0x46 camera 00``."""
    if len(shots) > 1000:
        raise CutsceneCompileError("too many camera shots (max 1000 per cutscene)")
    routes, routines, tags = [], [], []
    for i, shot in enumerate(shots):
        route_tag = f"c{i:03d}"
        routine_tag = f"s{i:03d}"
        kfs = shot["keyframes"]
        dur = int(shot.get("duration", 0))
        routes.append(_build_route(route_tag, kfs, shot.get("interpMode", 0)))
        # ★ Any timed shot gets a chained HOLD still (h###, retail tag convention) at its
        # end pose: the client deletes the camera task when its duration expires and falls
        # back to the default camera — visible whenever a dialogue's wait_dismiss parks the
        # bytecode past the move. The dur-0 still applies once and holds task-lessly (see
        # _build_routine). Duration-0 shots ARE that still already — no hold needed.
        hold_tag = None
        if dur > 0:
            hold_tag = f"h{i:03d}"
            hold_kf = {**kfs[-1], "time": 0.0}
            routes.append(_build_route(hold_tag, [hold_kf], 0))
        routines.append(_build_routine(routine_tag, route_tag, dur, hold_route=hold_tag))
        tags.append(routine_tag)
    reset_tag = None
    if reset_zoom and shots:
        last_kf = {**shots[-1]["keyframes"][-1], "fov": 350.0, "roll": 0.0, "time": 0.0}
        routes.append(_build_route("zres", [last_kf], 0))
        routines.append(_build_routine("zrs0", "zres", 0))
        reset_tag = "zrs0"
    dat = _build_evte() + b"".join(routes) + b"".join(routines) + _build_end()
    return dat, tags, reset_tag


def _build_evte() -> bytes:
    """The 32-byte ``evte`` scene header every camera scene DAT opens with."""
    b = bytearray(32)
    b[0:4] = b"evte"
    struct.pack_into("<I", b, 4, 0x0101)                  # size 32 (2*16), typeCode 1
    return bytes(b)


def _build_end() -> bytes:
    """The 16-byte ``end`` section terminator every scene DAT closes with."""
    b = bytearray(16)
    b[0:4] = b"end\x00"
    struct.pack_into("<I", b, 4, 0x80)                    # size 16 (1*16), typeCode 0
    return bytes(b)


# ---------------------------------------------------------------------------
# Dialog compile — reuse xi_author.append_dialog_lines, tracking id→message id.
# ---------------------------------------------------------------------------

def _ensure_prompt(text):
    """Append the ▼ wait-for-key continue prompt (``\\v`` → 0x7F31) to a line so the
    client's dialog box PAUSES for Enter instead of auto-advancing.

    ★ Verified against retail (Balasiel 8028, Maat 74): every cutscene message ends
    with ``7F31`` before the NUL. Without it, the box auto-closes — which is exactly
    the "dialogue plays with no Enter prompt" bug. ``wait_dismiss`` (0x23) alone does
    NOT force the pause; the ▼ code in the message text does."""
    if isinstance(text, list):
        if not text:
            return text
        out = list(text)
        if not (str(out[-1]).rstrip().endswith("\\v") or str(out[-1]).rstrip().endswith("{noprompt}")):
            out[-1] = str(out[-1]) + "\\v"
        return out
    s = str(text)
    return s if (s.rstrip().endswith("\\v") or s.rstrip().endswith("{noprompt}")) else s + "\\v"


def _compile_dialog(cutscene: dict, dialog_dat: bytes,
                    reuse_block=None, prompt=True) -> tuple[bytes, dict[str, int]]:
    """Grow/rewrite the dialog DAT with the cutscene's lines → ``{line_id: msg_id}``.

    ``reuse_block`` (from a prior publish of this event) is a list of message ids to
    OVERWRITE in place, indexed by dialog-line position. The Nth ``cutscene.dialog.lines``
    entry reuses ``reuse_block[N]`` when present, so re-publishing doesn't grow the DAT.

    ``prompt`` (default True for cinematic cutscenes): append the ▼ continue-prompt so
    each line waits for Enter."""
    src = cutscene.get("dialog")
    if isinstance(src, str):
        raise CutsceneCompileError("dialog: path references not resolved here (see cast note).")
    if not isinstance(src, dict) or "lines" not in src:
        raise CutsceneCompileError("cutscene.dialog must be an inline cutscene_dialog object")
    reuse_block = list(reuse_block or [])
    line_ids: dict[str, int] = {}
    if not src["lines"]:
        return dialog_dat, line_ids
    # One parse and one rebuild of the container for all lines (parsing a zone's 1.5 MB
    # dialog DAT per line made a 36-line event cost 14 s). Placement rules are those of
    # xi_author.append_dialog_lines: overwrite the reuse slot, else share a byte-identical
    # entry, else append.
    blobs, obf = xi_dialog.raw_entry_blobs(dialog_dat)
    existing: dict[bytes, int] = {}
    for idx, b in enumerate(blobs):
        existing.setdefault(bytes(b), idx)

    def place(blob: bytes, slot) -> int:
        key = bytes(blob)
        if slot is not None and 0 <= slot < len(blobs):
            blobs[slot] = blob
            existing.setdefault(key, slot)
            return slot
        if key in existing:
            return existing[key]
        idx = len(blobs)
        blobs.append(blob)
        existing[key] = idx
        return idx

    for i, entry in enumerate(src["lines"]):
        text = entry["text"]
        if prompt:
            text = _ensure_prompt(text)
        slot = reuse_block[i] if i < len(reuse_block) else None
        lines = text if isinstance(text, list) else [text]
        if entry.get("paged"):
            joined = "\\v".join(str(t) for t in lines)
            line_ids[entry["id"]] = place(xi_dialog.encode_event_string(joined) + bytes([0]), slot)
        else:
            first = None
            for k, ln in enumerate(lines):
                mid = place(xi_dialog.encode_event_string(str(ln)) + bytes([0]), slot if k == 0 else None)
                first = mid if first is None else first
            line_ids[entry["id"]] = first
    return xi_dialog.build_container(blobs, obf), line_ids


# ---------------------------------------------------------------------------
# Timeline → steps lowering
# ---------------------------------------------------------------------------

FPS = 30                     # client tick rate; all frame math assumes 30fps
_MIN_WAIT_FRAMES = 1         # skip 0-frame waits

# Same-frame ordering. Lower runs first. SETUP ops (priority < 10) all fire during the
# prologue black before fade-in; REVEAL ops (priority >= 10) flush the deferred fade-in.
#
# ★ ``show`` before ``place`` — retail Qufim 63 (Lion/Iroha): reveal block, THEN 0xBA,
#   THEN 0x80. place itself also emits the show block (so a Position keyframe alone is
#   enough); an explicit Show keyframe ahead of it is harmless (idempotent flags).
_STEP_PRIORITY = {
    "show": 0, "hide": 0,
    "place": 1,
    "camera": 2, "face": 2, "music": 2, "fade": 2, "anim": 2,
    "say": 10, "narrate": 10, "wait": 10,
}
_REVEAL_PRIORITY = 10  # fade-in flushes on the first step at/above this


def _extract_message_ids(scene: bytes, offset: int, refs: list[int],
                         limit: Optional[int] = None) -> list[int]:
    """Walk an existing event's bytecode from ``offset`` and return the dialog message
    ids it prints, in order. Used on re-publish to REUSE the same dialog-table slots
    (rewrite in place) instead of appending fresh copies every time.

    Print opcodes + where their 2-byte work-selector sits:
      * ``0x1D`` print_msg   → selector @+1
      * ``0x2B`` print_msg2  → selector @+5 (after the 4-byte speaker)
      * ``0x48`` narrate     → selector @+1
      * ``0x24`` dialog_menu → selector @+1 (the combined question+options string)

    ``limit`` (the next event's offset on this actor) bounds the walk. Our events keep a
    single ``end`` (early ends are rewritten to jumps) and may carry data trailers
    (shop tables) after it, so the walk stops at the first ``end``.
    """
    ids: list[int] = []
    i = offset
    n = len(scene) if limit is None else min(limit, len(scene))
    while i < n:
        op = scene[i]
        sub = scene[i + 1] if i + 1 < n else 0
        sz = core._opcode_size(op, sub)
        if not sz:
            break
        sel_off = None
        if op in (OP_PRINT_MSG, OP_NARRATE, OP_MENU):
            sel_off = i + 1
        elif op == OP_PRINT_MSG2:
            sel_off = i + 5
        if sel_off is not None and sel_off + 2 <= n:
            sel = struct.unpack_from("<H", scene, sel_off)[0]
            if sel & REF_FLAG:
                ridx = sel & MAX_REF_IDX
                if ridx < len(refs):
                    ids.append(refs[ridx])
        if op == OP_END:
            break                          # our events keep a single `end`; data trailers follow it
        i += sz
    return ids


def _timeline_to_steps(cutscene: dict, timeline: dict) -> list[dict]:
    """Flatten every track's keyframes into a linear, frame-ordered step list.

    Emits ``wait`` steps for gaps between keyframes so timing is preserved.
    Handles auto-fade-in/out via ``cutscene.autoFadeIn`` / ``autoFadeOut``.
    Track-kind semantics (see :mod:`schema/event_cutscene.json` timeline block):

    * ``dialog`` / ``say``  → step ``{op:'say', speaker: track.castId, text: kf.line}``
    * ``face``              → step ``{op:'face', actor: track.castId, target: kf.target}``
    * ``npc``               → step ``{op: kf.action, actor: track.castId}`` (show/hide/place)
    * ``music``             → step ``{op:'music', song: kf.song, track: kf.slot}``
    * ``camera``            → ``_lower_camera_track`` → scene-DAT route specs (shipped)
    * ``fade``              → step ``{op:'fade', kind: kf.kind}``
    * ``lock``              → step ``{op:'wait', frames: kf.frames}`` (no dedicated op)
    * ``sfx`` / ``vfx``     → skip today (opcodes not shipped yet)
    """
    total_frames = int(cutscene.get("totalFrames") or 300)
    cinematic = bool(cutscene.get("flags", {}).get("cinematic", True))

    events: list[tuple[int, dict]] = []

    # The cinematic prologue/epilogue now own the bookend fades (fade-to-black →
    # fade-in at start, fade-to-black → fade-in at end), matching retail Maat 74.
    # Fade-track keyframes here are OPTIONAL mid-scene fades on top of those.
    cam_subs = {t["kind"]: t for t in timeline["tracks"] if t["kind"] in ("campos", "camrot", "camzoom")}
    has_subs = "campos" in cam_subs
    for track in timeline["tracks"]:
        kind = track["kind"]
        cast_id = track.get("castId")
        if kind == "camera":
            # Legacy single-camera track (old defs). When the Position/Rotation/Zoom sub-tracks are
            # present, they are authoritative and a flattened 'camera' alongside them is ignored.
            if not has_subs:
                events.extend(_lower_camera_track(track))
            continue
        if kind in ("campos", "camrot", "camzoom"):
            continue                                    # recomposed together below
        for kf in track["keyframes"]:
            frame = int(kf.get("frame", 0))
            step = _kf_to_step(kind, cast_id, kf)
            if step is not None:
                events.append((frame, step))
    if has_subs:
        events.extend(_lower_camera_tracks(cam_subs.get("campos"), cam_subs.get("camrot"), cam_subs.get("camzoom")))

    # Stable sort by frame, then by priority so that at the SAME frame the
    # non-blocking SETUP ops (camera shot, facing, show/place, music, fade) all
    # fire BEFORE a blocking ``say`` — otherwise a dialogue line's wait-for-Enter
    # would stall before the opening camera shot is even established.
    events.sort(key=lambda x: (x[0], _STEP_PRIORITY.get(x[1].get("op"), 1)))

    steps: list[dict] = []
    prev_frame = 0
    for frame, step in events:
        f = max(0, frame)                 # clamp the safety fade's -1
        gap = f - prev_frame
        if gap >= _MIN_WAIT_FRAMES:
            steps.append({"op": "wait", "frames": gap})
        steps.append(step)
        prev_frame = f
    return steps


def _camera_pose(kf: dict) -> dict:
    """A camera keyframe's pose → route-keyframe fields (FFXI world space). The editor stores
    ``eye``/``look`` in FFXI coords + ``fov`` as a vertical FOV in **degrees**. FFXI's route
    stores a FOCAL LENGTH (``SplineControlPoint.FovCalculationParameter``, default 350) whose
    projection is vertical FOV = 2·atan2(192, focal) — so convert degrees → focal =
    192 / tan(fov/2) (xiclient ``GameManager::UpdateProjectionMatrix``)."""
    eye = kf.get("eye") or kf.get("pos") or [0.0, 0.0, 0.0]
    look = kf.get("look") or [eye[0], eye[1], eye[2] - 1.0]
    fov = float(kf.get("fov", 57.0))
    if fov >= 200.0:                    # already a focal length (legacy def / raw route passthrough)
        focal = fov
    else:                               # vertical degrees → focal length
        deg = min(120.0, max(5.0, fov))
        focal = 192.0 / math.tan(math.radians(deg) / 2.0)
    return {"eye": [float(v) for v in eye[:3]],
            "look": [float(v) for v in look[:3]],
            "fov": focal, "roll": float(kf.get("roll", 0.0))}


def _lower_camera_track(track: dict) -> list[tuple[int, dict]]:
    """Camera track → frame-tagged ``{op:'camera', shotSpec:{…}}`` events, each carrying
    the route/routine spec ``build_scene_resource`` needs.

    Three keyframe kinds:
    * ``still``  — cut to its pose at its frame (1-kf route, dur 0).
    * ``spline`` — LINEAR glide from the previous keyframe's pose to its own, arriving at
      its frame (2-kf route fired at the previous frame over the gap).
    * ``curved`` — a run of consecutive curved keyframes (plus the preceding anchor) is
      chained into ONE ``interpMode=4`` multi-point route so the client arcs the camera
      THROUGH all of them (3+ points ⇒ a real curve; 2 ⇒ still a straight line).

    ``compile_cutscene`` collects the specs, builds the scene DAT, and back-fills each
    step's shot id."""
    kfs = sorted(track.get("keyframes", []), key=lambda k: int(k.get("frame", 0)))
    out: list[tuple[int, dict]] = []
    n = len(kfs)
    i = 0
    while i < n:
        kf = kfs[i]
        frame = int(kf.get("frame", 0))
        camkind = kf.get("camKind") or kf.get("kind") or ("spline" if i > 0 else "still")
        if camkind == "curved" and i > 0:
            # Chain the anchor (previous kf) + a run of consecutive Curved kfs into ONE
            # interpMode=4 route so the client arcs the camera THROUGH all of them.
            anchor = kfs[i - 1]
            run = [anchor, kf]
            j = i + 1
            while j < n and (kfs[j].get("camKind") == "curved"):
                run.append(kfs[j]); j += 1
            f0 = int(anchor.get("frame", 0))
            f1 = int(run[-1].get("frame", 0))
            dur = max(1, f1 - f0)
            keyframes = [{**_camera_pose(k), "time": (int(k.get("frame", 0)) - f0) / dur}
                         for k in run]
            # interpMode = SmoothingType 0..4. Multi-point retail uses 4; never allow 0 on a
            # curve (a still-cut's smooth:0 must not poison the chained move).
            sm = int(kf.get("smooth", 4))
            if sm == 0:
                sm = 4
            spec = {"keyframes": keyframes, "interpMode": sm, "duration": dur}
            # Drop a zero-duration still that only exists as this curve's anchor —
            # firing s000(dur0) then s001 at the same frame loads the scene twice
            # and has crashed clients. The multi-point route already starts at the anchor pose.
            if out and out[-1][0] == f0:
                prev_spec = out[-1][1].get("shotSpec") or {}
                if prev_spec.get("duration", 0) == 0 and len(prev_spec.get("keyframes") or []) <= 1:
                    out.pop()
            out.append((f0, {"op": "camera", "shotSpec": spec}))
            i = j
        elif camkind == "spline" and i > 0:
            prev_frame = int(kfs[i - 1].get("frame", 0))
            dur = max(1, frame - prev_frame)
            spec = {"keyframes": [{**_camera_pose(kfs[i - 1]), "time": 0.0},
                                  {**_camera_pose(kf), "time": 1.0}],
                    "interpMode": int(kf.get("smooth", kf.get("interpMode", 4))), "duration": dur}
            if out and out[-1][0] == prev_frame:
                prev_spec = out[-1][1].get("shotSpec") or {}
                if prev_spec.get("duration", 0) == 0 and len(prev_spec.get("keyframes") or []) <= 1:
                    out.pop()
            out.append((prev_frame, {"op": "camera", "shotSpec": spec}))
            i += 1
        else:
            spec = {"keyframes": [{**_camera_pose(kf), "time": 0.0}], "interpMode": 0, "duration": 0}
            out.append((frame, {"op": "camera", "shotSpec": spec}))
            i += 1
    return out


def _cam_channel_sample(kfs: list[dict], frame: int, key: str):
    """Linear-sample a frame-sorted channel ``[{frame, <key>}]`` at ``frame``, holding past the
    ends. The value may be a 3-vector (list) or a scalar. Returns ``None`` for an empty channel."""
    if not kfs:
        return None
    first, last = kfs[0], kfs[-1]
    if frame <= int(first.get("frame", 0)):
        return first.get(key)
    if frame >= int(last.get("frame", 0)):
        return last.get(key)
    for i in range(len(kfs) - 1):
        a, b = kfs[i], kfs[i + 1]
        fa, fb = int(a.get("frame", 0)), int(b.get("frame", 0))
        if fa <= frame <= fb:
            u = (frame - fa) / ((fb - fa) or 1)
            va, vb = a.get(key), b.get(key)
            if isinstance(va, (list, tuple)):
                return [va[j] + (vb[j] - va[j]) * u for j in range(3)]
            return (va or 0.0) + ((vb or 0.0) - (va or 0.0)) * u
    return last.get(key)


def _lower_camera_tracks(pos_track: dict, rot_track: Optional[dict],
                         zoom_track: Optional[dict]) -> list[tuple[int, dict]]:
    """Recompose the Position / Rotation / Zoom sub-tracks → per-shot ``{op:'camera', shotSpec}``
    events — the compile-side twin of the editor's ``_authorCameraShots``.

    Cuts (shot starts) are Position keyframes with ``camKind == 'still'``. Within each shot the
    three channels are sampled at the UNION of their keyframe frames → route control points
    ``{eye, look, fov(focal), roll, time}``. This is the SAME union-recompose the editor previews,
    so published == previewed, and it round-trips a decomposed retail route byte-faithfully
    (verified against Balasiel + zone-in cutscenes)."""
    def _kfs(t):
        return sorted((t or {}).get("keyframes", []), key=lambda k: int(k.get("frame", 0)))
    pos = _kfs(pos_track)
    if not pos:
        return []
    rot, zoom = _kfs(rot_track), _kfs(zoom_track)

    cuts = [i for i, k in enumerate(pos) if i == 0 or (k.get("camKind") or "still") == "still"]
    legacy: list[dict] = []       # recompose → the legacy single-track keyframe shape
    for c, i_start in enumerate(cuts):
        i_end = cuts[c + 1] if c + 1 < len(cuts) else len(pos)
        shot_pos = pos[i_start:i_end]
        start = int(shot_pos[0].get("frame", 0))
        next_cut = int(pos[cuts[c + 1]].get("frame", 0)) if c + 1 < len(cuts) else None
        in_shot = lambda k: int(k.get("frame", 0)) >= start and (next_cut is None or int(k.get("frame", 0)) < next_cut)
        shot_rot = [k for k in rot if in_shot(k)] or [
            {"frame": start, "look": _cam_channel_sample(rot, start, "look") or [0.0, 0.0, 0.0],
             "roll": _cam_channel_sample(rot, start, "roll") or 0.0}]
        shot_zoom = [k for k in zoom if in_shot(k)] or [
            {"frame": start, "fov": _cam_channel_sample(zoom, start, "fov") or 57.0}]
        frames = sorted({int(k.get("frame", 0)) for k in (shot_pos + shot_rot + shot_zoom)})
        npts = len(frames)
        # ★ Do NOT take smooth only from the opening still (often smooth:0 = snap).
        # Multi-point retail routes use mode 4; stamping 0 onto a 3-point curve has
        # crashed the client. Prefer the max authored smooth in the shot, default 4
        # when the shot is a real multi-point move.
        smooths = [int(k.get("smooth", 4)) for k in shot_pos if k.get("smooth") is not None]
        if npts >= 2:
            smooth = max(smooths) if smooths else 4
            if smooth == 0:
                smooth = 4          # never emit multi-point with mode 0
        else:
            smooth = smooths[0] if smooths else 0
        for j, f in enumerate(frames):
            eye = _cam_channel_sample(shot_pos, f, "eye") or [0.0, 0.0, 0.0]
            fov = _cam_channel_sample(shot_zoom, f, "fov")
            legacy.append({
                "frame": f,
                "camKind": "still" if j == 0 else ("curved" if npts >= 3 else "spline"),
                "eye": eye,
                "look": _cam_channel_sample(shot_rot, f, "look") or eye,
                "fov": fov if fov is not None else 57.0,   # degrees → focal in _camera_pose
                "roll": _cam_channel_sample(shot_rot, f, "roll") or 0.0,
                "smooth": smooth,
            })
    # Reuse the PROVEN single-track lowering (identical to the pre-Phase-3 flatten path that shipped
    # working in-game): it re-derives shots from camKind — a 'still' is a duration-0 CUT that also
    # anchors the following curved run, so splines start from the previous shot's end. Building
    # shotSpecs directly here diverged (cuts got duration 1, curves lost their anchor) → crashes.
    return _lower_camera_track({"kind": "camera", "keyframes": legacy})


def _kf_to_step(kind: str, cast_id: str | None, kf: dict) -> dict | None:
    """Map one keyframe (kind + fields) to the matching step dict."""
    if kf.get("op"):
        return {**kf, "op": kf["op"]}
    if kind == "dialog":
        # Consolidated Dialog track: the SPEAKER is per-keyframe (falls back to the
        # track's castId for pre-consolidation defs).
        step = {"op": "say", "speaker": kf.get("speaker") or cast_id,
                "text": kf.get("line") or kf.get("text")}
        if kf.get("anim"):
            step["anim"] = kf["anim"]      # per-line gesture override
        return step
    if kind == "face":
        # Consolidated Face track: the ACTOR (who turns) is per-keyframe — actor=player
        # covers "player faces Maat". Falls back to the track's castId for old defs.
        return {"op": "face", "actor": kf.get("actor") or cast_id,
                "target": kf.get("target", "player"), "talk": bool(kf.get("talk", False))}
    if kind == "position":
        # Consolidated Position track: place an entity (player or NPC) at a spot via 0xBA.
        # `pos` (FFXI world) is resolved from the picked marker by the editor before publish;
        # skip a keyframe with no position rather than crash. The client restores everyone's
        # real position when the event ends, so no explicit reset is needed.
        if not kf.get("pos"):
            return None
        # The Position sub-track's castId is authoritative (one track per actor); a stale
        # per-keyframe `actor` (copy/paste, migration) must not misroute the placement.
        return {"op": "place", "actor": cast_id or kf.get("actor"),
                "pos": kf["pos"], "dir": float(kf.get("dir", 0.0))}
    if kind == "npc":
        action = kf.get("action", "show")
        if action in ("show", "hide"):
            return {"op": action, "actor": cast_id}
        if action == "place":
            step = {"op": "place", "actor": cast_id}
            if "pos" in kf: step["pos"] = kf["pos"]
            if "dir" in kf: step["dir"] = kf["dir"]
            return step
        return None
    if kind == "music":
        return {"op": "music", "song": int(kf.get("song", 0)), "track": int(kf.get("slot", 0))}
    if kind == "fade":
        return {"op": "fade", "kind": kf.get("kind", "in"),
                "frames": int(kf.get("frames", kf.get("dur", 30)))}
    if kind == "anim":
        # Standalone Anim track: play a gesture on the actor with no dialogue (0x5B).
        tag = kf.get("anim")
        if not tag:
            return None
        return {"op": "anim", "actor": kf.get("actor") or cast_id, "anim": tag}
    if kind in ("wait", "lock"):
        # Explicit extra pause (0x1C wait_time) at this frame, on top of the gap
        # the timeline already inserts. Length = kf.frames.
        return {"op": "wait", "frames": int(kf.get("frames", 30))}
    # sfx / vfx / camera: opcode support not shipped yet — silently skip.
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compile_cutscene(cutscene: dict, event_dat: bytes, dialog_dat: bytes,
                     scene_dat: Optional[bytes] = None,
                     camera_scene_ref: Optional[int] = None,
                     cast_motions: Optional[dict] = None,
                     bank_tags: Optional[frozenset] = None,
                     ffxi_dir: Optional[Path] = None) -> CompileResult:
    """Compile a ``xi.cutscene.v1`` dict → byte-exact updated DATs.

    Parameters
    ----------
    cutscene : dict
        A validated cutscene definition (see :mod:`schema/event_cutscene.json`).
        ``cast`` and ``dialog`` must be inlined (path refs are the caller's job).
    event_dat : bytes
        Current bytes of the zone Event DAT (file_id 5820 + zone_id).
    dialog_dat : bytes
        Current bytes of the zone Dialog DAT (file_id 6420 + zone_id).
    scene_dat : bytes, optional
        Current bytes of the target scene resource DAT. Required when the cutscene
        has ``camera.shots`` OR any ``fade`` step (fades need the fdo1/fdi1
        routines to exist somewhere reachable via a scene resource ref).
    cast_motions : dict, optional
        Per-cast schedulable-motion maps from the bridge's ``_cast_motion_maps``
        (``{castId: {"valid": set(routineTag), ...}}``). Lets anim dispatch fire a
        tag the actor's model OWNS via 0x2C even when it collides with a curated
        gesture name — without it a custom 'tlk0' on a monster rig would ride the
        0x5B humanoid bank and play wrong-skeleton motion.
    bank_tags : frozenset, optional
        The REAL routine inventory of the shared gesture bank DAT (bridge's
        ``_gesture_bank_tags`` parses file 32104+bank). When supplied it replaces
        the hardcoded ``_GESTURE_TAGS`` fallback for the 0x5B-vs-0x2C dispatch.

    Returns
    -------
    CompileResult
        Rebuilt DAT bytes + assigned event id + Lua stub.
    """
    if cutscene.get("schema") != "xi.cutscene.v1":
        raise CutsceneCompileError(
            f"unsupported schema: {cutscene.get('schema')!r} (want 'xi.cutscene.v1')")

    flags = dict(cutscene.get("flags", {}))

    # Timeline-mode wins if present. Lower to the classic step list then reuse
    # the shipped emit path — that way we have ONE emit path, not two divergent ones.
    timeline = cutscene.get("timeline")
    if timeline and timeline.get("tracks"):
        steps = _timeline_to_steps(cutscene, timeline)
        # A timeline means "author wanted a cutscene". Default cinematic on
        # so the retail prologue fires (0x42 → 0x46 01 → 0x38 → fdi1) — player
        # gets locked, screen fades in properly, Enter-popup between lines.
        flags.setdefault("cinematic", True)
    else:
        steps = cutscene.get("steps", [])
        flags.setdefault("cinematic", False)
    if not steps:
        raise CutsceneCompileError("cutscene has no steps or timeline tracks")

    # 1. Parse the event DAT + resolve the owning actor. (Cast resolution doesn't
    #    need dialog ids, so we do it up front to determine the event id + whether
    #    we're replacing — which lets us rewrite the SAME dialog slots in place.)
    actors = core.parse_raw_actors(event_dat)
    owner_id = cutscene["actor"]
    ctx = _Ctx(refs=[], dialog_ids={})
    ctx.owner_actor = owner_id
    ctx.event_mode = int(flags.get("eventMode", EVENT_MODE_DEFAULT))
    ctx.talk_anim = str(flags.get("talkAnim") or "tlk0")
    ctx.idle_anim = str(flags.get("idleAnim") or "idl0")
    ctx.anim_bank = int(flags.get("animBank") or 60)
    # {castId: frozenset(own routine tags)} — own routines outrank the gesture bank
    # in _emit_gesture (see cast_motions in the docstring above).
    ctx.own_motions = {cid: frozenset((m or {}).get("valid") or ())
                       for cid, m in (cast_motions or {}).items()}
    # Real bank inventory (None → _emit_gesture falls back to _GESTURE_TAGS).
    ctx.bank_tags = frozenset(bank_tags) if bank_tags else None
    ctx.face_player = flags.get("facePlayer", True) is not False
    look = cutscene.get("npcLook") or {}
    ctx.owner_look_type = str(look.get("type") or "")          # gesture guard
    ctx.owner_model = int(look.get("model") or 0) if str(look.get("type")) == "standard" else None
    if ctx.owner_look_type == "equipped" and not flags.get("animBank"):
        # player-skeleton NPC: the shared bank 60 breaks its legs; use the race's own bank
        ctx.anim_bank = RACE_GESTURE_BANKS.get(int(look.get("race") or 0), ctx.anim_bank)
        if bank_tags is None:
            try:
                from xi.zone.xi_bridge import _gesture_bank_tags
                real = _gesture_bank_tags(ctx.anim_bank)
                if real:
                    ctx.bank_tags = frozenset(real)
            except Exception:              # noqa: BLE001 - fall back to the curated set
                pass
    ctx.hide_names = bool(flags.get("hideNpcNames"))    # 94 01 stamp after each show
    _build_cast(cutscene, ctx)
    owner_entity = ctx.entity_id(owner_id)

    actor = next((a for a in actors if a.actor_id == owner_entity), None)
    created_actor = actor is None
    if created_actor:
        actor = core.RawActor(owner_entity, [], [], [], b"", b"", b"", dirty=True)
        actors.append(actor)

    # Determine the event id now. If replacing an existing event, recover the dialog
    # message ids it owned so the new dialogue OVERWRITES those slots (no DAT growth).
    event_ids_real = [e for e in actor.event_ids if e not in (0xFFFE, 0xFFFF)]
    ev_field = cutscene.get("eventId", "auto")
    reuse_block: list[int] = []
    # The event table's ORDER is load-bearing: a request opcode (0x27/0x28/0x29, REQEW)
    # names another event by its SLOT in the entity's offset table (the client does
    # ``StackExecPointer = TagOffset[tagnum]``), so a replaced event must keep its slot
    # and every existing slot must survive unchanged. Verified at the end of the compile.
    orig_ids = list(actor.event_ids)
    replace_idx: Optional[int] = None
    if ev_field == "auto":
        zone_ids = {e for a in actors for e in a.event_ids if e not in (0xFFFE, 0xFFFF)}
        event_id = (max(event_ids_real) + 1) if event_ids_real else 0
        while event_id in zone_ids or event_id in (0xFFFE, 0xFFFF):
            event_id += 1
    else:
        event_id = int(ev_field)
        if event_id in event_ids_real:
            idx = actor.event_ids.index(event_id)
            replace_idx = idx
            old_off = actor.event_offsets[idx]
            # The old event's print ops give the msg ids in step order; sorting the
            # unique set recovers the contiguous block in dialog-definition order
            # (dialogue is appended in cutscene.dialog.lines order → ascending ids).
            later = [o for o in actor.event_offsets if o > old_off]
            reuse_block = sorted(set(_extract_message_ids(
                actor.scene_data, old_off, actor.references,
                limit=min(later) if later else None)))
            del actor.event_ids[idx]
            del actor.event_offsets[idx]
            # RECLAIM the old bytecode if it was the LAST event in scene_data
            # (the common case: re-publishing the same event repeatedly). This
            # stops the event DAT growing ~1 event/publish from orphaned dead
            # code. Safe only when nothing else lives past old_off — our events
            # have no absolute internal jumps, and truncating the tail can't
            # shift any other (earlier) event's offset. Interleaved re-publishes
            # of DIFFERENT events still leave a little dead code (rare). Our own
            # jumps are absolute but only ever point INSIDE the event being
            # replaced, so discarding it whole is still safe.
            if not actor.event_offsets or old_off >= max(actor.event_offsets):
                actor.scene_data = bytes(actor.scene_data)[:old_off]

    # 2. Compile dialogue — rewrite the reused block in place, append any extras.
    #    Menu steps first get their combined question+options string as an extra line.
    #    Cinematic cutscenes get the ▼ continue-prompt appended so lines wait for Enter
    #    (retail menu strings carry it too: Ru'Lude 12550 ends 7F31).
    steps = _normalize_ends(steps)
    cutscene = _inject_menu_lines(cutscene, steps)
    # ▼ prompt on every line by default — retail plain NPC dialogs carry it too
    # (Ru'Lude 12547..12550 all end 7F31); flags.prompt = false opts out.
    dialog_dat_out, dialog_ids = _compile_dialog(
        cutscene, dialog_dat, reuse_block, prompt=flags.get("prompt", True) is not False)
    ctx.dialog_ids = dialog_ids
    ctx.dialog_out = dialog_dat_out
    ctx.ffxi_dir = Path(ffxi_dir) if ffxi_dir else None

    # Seed ctx.refs from the actor's existing refs so add_ref dedups against them.
    ctx.refs = list(actor.references)

    # 3. Reserve a slot for the scene-resource ref used by every 0x45/0x55.
    # Fade 0x45/0x55 opcodes reference the shared retail fade scene (id 200, file
    # 30904 — fdo1/fdi1/ovl1) via ctx.scene_res_selector, whenever cinematic mode is
    # on OR the scene has an explicit fade step. Camera shots build their own
    # scene DAT below (build_scene_resource) and use ctx.camera_res_selector.
    has_fade = any(s.get("op") == "fade" for s in steps)
    cinematic = flags.get("cinematic", False)
    if cinematic or has_fade:
        ctx.scene_res_selector = ctx.add_ref(FADE_SCENE_RESOURCE)

    # Camera shots (from the timeline camera track, lowered to {op:'camera', shotSpec})
    # → build THIS cutscene's own camera scene DAT and point every camera 0x45 at it
    # via ctx.camera_res_selector. camera_scene_ref (the p → file 30704+helper(p)) is
    # allocated + the returned scene_dat registered by the caller (bridge).
    camera_steps = [s for s in steps if s.get("op") == "camera"]
    scene_dat_out = None
    if camera_steps:
        if camera_scene_ref is None:
            ctx.warnings.append(
                "camera track present but no scene file was allocated — camera shots "
                "skipped. Publish via the editor (the bridge allocates the scene DAT).")
            steps = [s for s in steps if s.get("op") != "camera"]
        else:
            shots = [s["shotSpec"] for s in camera_steps]
            scene_dat_out, tags, zres_tag = build_scene_resource(
                shots, reset_zoom=bool(flags.get("resetZoomOnEnd", True)))
            ctx.camera_res_selector = ctx.add_ref(int(camera_scene_ref))
            ctx.zoom_reset_tag = zres_tag
            for i, s in enumerate(camera_steps):
                s["shot"] = i
                ctx.shots[i] = tags[i]

    # 4. Emit prologue → steps → epilogue.
    owner_meta = ctx.cast_meta.get(owner_id) or {}
    ctx.owner_has_place = ("pos" in owner_meta) or any(
        s.get("op") == "place" and s.get("actor") == owner_id for s in steps)
    # Does the author USE the trigger NPC at all (speak/anim/face/place/show)?
    # ☠ The trigger NPC can't be hidden by the hide-other-NPCs event mode: its actor
    # block hosts the event, so the client marks it event-involved and exempts it.
    # An UNREFERENCED trigger gets an explicit 0x22 hide instead (see prologue) —
    # the author stages a separate cast copy (e.g. retail's Maat 3032) if the
    # character should appear in the scene.
    ctx.owner_referenced = ctx.owner_has_place or any(
        (s.get("op") == "say" and s.get("speaker") in (None, owner_id))
        or (s.get("op") in ("face", "anim", "show", "hide") and s.get("actor") in (None, owner_id))
        for s in steps)
    # Jumps (0x01 / 0x02) carry ABSOLUTE scene offsets: the new event starts where the
    # actor's scene currently ends (the old copy of this event was already reclaimed).
    ctx.base_offset = len(actor.scene_data)
    _emit_prologue(ctx, flags)

    # Actors with an EXPLICIT Show keyframe control their own reveal — placement
    # must not pre-show them (they'd pop in at frame 0 regardless of the keyframe).
    explicit_show = {s.get("actor") for s in steps if s.get("op") == "show"}
    for s in steps:
        if s.get("op") == "place" and s.get("actor") in explicit_show:
            s["skip_show"] = True

    # Implicit place-at-start for every cast entry that carries a pos.
    for cid, meta in ctx.cast_meta.items():
        if "pos" in meta:
            _step_place({"actor": cid, "skip_show": cid in explicit_show}, ctx)

    for step in steps:
        op = step["op"]
        # Flush the deferred prologue fade-in once we reach the first REVEAL step (dialogue
        # / wait): all the during-black SETUP ops (camera shots, entity placement, facing,
        # show/hide, music) fire first, so the fade-in reveals an already-framed, already-
        # positioned scene. (Camera-less/positionless cutscenes just fade in immediately.)
        if ctx.pending_fade_in and _STEP_PRIORITY.get(op, _REVEAL_PRIORITY) >= _REVEAL_PRIORITY:
            _emit_fade(ctx, ctx.pending_fade_in, hold=0)  # retail (Qufim 63) fires fade-in
            # with NO hold — a hold here silently shifts the whole timeline right
            ctx.pending_fade_in = ""
        emit = STEP_DISPATCH.get(op)
        if emit is None:
            raise CutsceneCompileError(f"unknown step op: {op!r}")
        if step.get("label") and op not in ("sub", "table"):
            ctx.mark_label(str(step["label"]))     # branch/goto targets (absolute offsets); subs and tables mark theirs in the trailer
        emit(step, ctx)

    # A camera-only cutscene (no dialogue/other steps): still reveal it.
    if ctx.pending_fade_in:
        _emit_fade(ctx, ctx.pending_fade_in, hold=0)   # no hold — see above
        ctx.pending_fade_in = ""

    # Auto-append `end` if the user didn't (subroutines after the final `end` do not count).
    last_real = next((st for st in reversed(steps) if st.get("op") != "sub"), None)
    if last_real is None or last_real.get("op") != "end":
        if END_LABEL not in ctx.labels:
            ctx.mark_label(END_LABEL)              # early `end`s jump into the epilogue
        _emit_epilogue(ctx, flags)
    for emit_trailer in ctx.trailers:      # subroutines / data tables live past the final `end`
        emit_trailer()
    _resolve_fixups(ctx)
    dialog_dat_out = ctx.dialog_out        # steps (shop) may have appended strings

    # 5. Splice the new event onto the actor block. (Event id + old-event removal
    #    were resolved in step 1, before dialogue compile.)
    new_offset = len(actor.scene_data)
    if new_offset > MAX_SCENE_OFFSET:
        raise CutsceneCompileError(
            f"actor 0x{owner_entity:08X} scene overflow — u16 offset table")

    actor.references = ctx.refs
    actor.scene_data = bytes(actor.scene_data) + bytes(ctx.code)
    if replace_idx is not None:
        actor.event_offsets.insert(replace_idx, new_offset)
        actor.event_ids.insert(replace_idx, event_id)
    else:
        actor.event_offsets.append(new_offset)
        actor.event_ids.append(event_id)
    actor.dirty = True
    if actor.event_ids[:len(orig_ids)] != orig_ids:
        raise CutsceneCompileError(
            f"actor 0x{owner_entity:08X}: event table order changed (request tags index it); "
            f"was {orig_ids[:12]}..., now {actor.event_ids[:12]}...")

    # 5b. Cast involvement blocks — THE reason non-owner NPCs appear at all.
    #     At event start the client walks the DAT's actor blocks and "prepares" only
    #     entities whose block lists this event id (XiEvent init → NowEventChar): it
    #     sets their event render flags and, crucially, sends a 0x016 CHARREQ for any
    #     entity the server hasn't spawned (status-6 CUTSCENE_ONLY NPCs, custom NPCs).
    #     An entity with no block for the event is never requested, and every
    #     0x2F/0x4E/0x92/0x94/0xBA aimed at it is a silent no-op (GetActorIndex fails)
    #     — the NPC simply never shows. Retail gives every cast NPC its own block:
    #     Qufim 63 (Iroha/Lion) and Ru'Lude 10009 (Cornelia) each carry per-NPC
    #     mini-events; the minimal retail form is a single `end` opcode.
    cast_entities = sorted({
        ent for ent in ctx.cast.values()
        if (ent & 0xFF000000) and (ent >> 24) != 0x7F and ent != owner_entity
    })
    for ent in cast_entities:
        blk = next((a for a in actors if a.actor_id == ent), None)
        if blk is None:
            # New block mirrors retail's shape: scene byte 0 is a pad, events @0x0001.
            blk = core.RawActor(ent, [], [], [], b"\x00", b"", b"", dirty=True)
            actors.append(blk)
        if event_id in blk.event_ids:
            continue                      # re-publish: marker already present
        off = len(blk.scene_data)
        if off > MAX_SCENE_OFFSET:
            raise CutsceneCompileError(
                f"actor 0x{ent:08X} scene overflow — u16 offset table")
        blk.scene_data = bytes(blk.scene_data) + b"\x00"   # single `end` (0x00)
        blk.event_offsets.append(off)
        blk.event_ids.append(event_id)
        blk.dirty = True

    # Cast members REMOVED on a re-publish: unlist the stale involvement marker so
    # the client stops preparing them. Only markers whose bytecode starts with `end`
    # (i.e. ours) are touched — real retail events are never unlisted.
    keep = set(cast_entities) | {owner_entity}
    for a in actors:
        if a.actor_id in keep or event_id not in a.event_ids:
            continue
        i = a.event_ids.index(event_id)
        off = a.event_offsets[i]
        later = [o for o in a.event_offsets if o > off]
        size = (min(later) if later else len(a.scene_data)) - off
        if size != 1 or bytes(a.scene_data)[off:off + 1] != b"\x00":
            continue                      # a real event (retail ones open with 0x00 noops)
        del a.event_ids[i]
        del a.event_offsets[i]
        if not a.event_offsets or off >= max(a.event_offsets):
            a.scene_data = bytes(a.scene_data)[:off]
        a.dirty = True

    event_dat_out = core.build_event_dat(actors)

    # 6. Build the Lua stub — include every non-player cast member that this cutscene
    #    places, so the server can setPos+setStatus(NORMAL) them before startCutscene.
    cast_stage = []
    seen_stage = set()
    for step in steps:
        if step.get("op") != "place":
            continue
        cid = step.get("actor")
        if not cid or cid in seen_stage:
            continue
        ent = ctx.cast.get(cid)
        if ent is None or ent == ACTOR_MAGIC["player"]:
            continue
        pos = step.get("pos") or (ctx.cast_meta.get(cid) or {}).get("pos")
        if not pos:
            continue
        seen_stage.add(cid)
        cast_stage.append({
            "id": ent,
            "x": float(pos[0]), "y": float(pos[1]), "z": float(pos[2]),
            "dir": float(step.get("dir", (ctx.cast_meta.get(cid) or {}).get("dir", 0.0))),
        })
    owner_name = None
    for c in (cutscene.get("cast") or {}).get("cast") or []:
        if c.get("id") == owner_id:
            owner_name = c.get("name")
            break
    # Hide-others bits ride the SERVER event-start packet (Mode → CliEventMode), not
    # the 0x38 opcode — retail event DATs carry no hide bits in the 0x38 operand, and
    # in-game testing confirmed the opcode-side high byte alone does nothing. Map the
    # editor's eventMode high-byte hide bits to startCutscene{flags} in the stub.
    server_flags = (ctx.event_mode >> 8) & 0x12          # 0x10 hide NPCs · 0x02 hide PCs
    stub = xi_author.lua_stub(owner_entity, event_id, owner_name,
                              cast_stage=cast_stage, server_flags=server_flags)

    return CompileResult(
        event_id=event_id,
        event_dat=event_dat_out,
        dialog_dat=dialog_dat_out,
        scene_dat=scene_dat_out,     # camera scene DAT bytes (None if no camera track)
        refs_used=ctx.refs,
        warnings=ctx.warnings,
        lua_stub=stub + "".join(chr(10) + xi_shop.lua_stub(st, event_id) for st, _ in ctx.shop_stubs),
    )

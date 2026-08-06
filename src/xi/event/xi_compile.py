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

Not yet implemented
-------------------

Menu / branch / goto (need label backpatching for 0x02/0x3E jumps), load_zone
(0x34/0x35), and the scene-DAT camera-Route/EffectRoutine emitter (Route layout is
decoded, EffectRoutine sec2 command stream still needs a writer). Stubs below raise
:class:`NotImplementedError` with a clear message so callers fail loudly instead of
silently emitting a broken opcode.

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
from typing import Any, Callable, Optional

from xi.common.xi_section import encode_section_meta
from xi.dialog import xi_dialog
from xi.event import xi_author, xi_event as core


# ---------------------------------------------------------------------------
# Opcodes we emit — kept as named constants so the compiler doc reads clearly.
# ---------------------------------------------------------------------------

OP_LOCK_PLAYER    = 0x20    # arg: u8 flag (1 = lock, 0 = release)
OP_END            = 0x21
OP_WAIT_DISMISS   = 0x23
OP_MENU           = 0x24    # 7B — dialog menu prompt (msgSel + 2 flag sels)
OP_WAIT_SELECT    = 0x25    # 1B — wait for menu selection
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
    sel = ctx.add_ref(int(step.get("frames", 30)))
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
        own_talk = _emit_gesture(ctx, anim_ent, talk,           # 0x5B bank / 0x2C own routine
                                 cast_id=(speaker_id or ctx.owner_actor))
        if is_owner:
            _emit_u8(ctx, OP_PRINT_MSG); _emit_u16(ctx, sel)    # 0x1D (event entity speaks)
        else:
            _emit_u8(ctx, OP_PRINT_MSG2)                        # 0x2B (named speaker)
            _emit_u32(ctx, ctx.entity_id(speaker_id)); _emit_u16(ctx, sel)
        if step.get("wait", True):
            _emit_u8(ctx, OP_WAIT_DISMISS)                      # 0x23
        idle_b = idle.encode("ascii", "replace").ljust(4, b" ")[:4]
        if own_talk and not is_owner:
            # An own routine fired on a CAST speaker: 0x5E only ever resets the event
            # entity, so a looping routine (custom talk clips loop) would run forever.
            # 0x6B stops THIS actor and drops it back to its idle.
            _emit_stop_action_actor(ctx, idle_b, anim_ent)     # 0x6B idle (this speaker)
        else:
            _emit_stop_action(ctx, idle_b)                     # 0x5E idle (event entity)
    else:
        # Non-cinematic: plain 0x2B print_msg2 with explicit speaker.
        speaker = ctx.entity_id(step["speaker"])
        _emit_u8(ctx, OP_PRINT_MSG2); _emit_u32(ctx, speaker); _emit_u16(ctx, sel)
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


def _step_end(step, ctx: _Ctx):
    _emit_u8(ctx, OP_END)


def _step_not_implemented(name: str) -> Callable:
    def _emit(step, ctx: _Ctx):
        raise NotImplementedError(
            f"step op {name!r} is not implemented yet — see xi_compile.py TODOs.")
    return _emit


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
    # deferred — see module docstring
    "menu":          _step_not_implemented("menu"),
    "branch":        _step_not_implemented("branch"),
    "goto":          _step_not_implemented("goto"),
    "load_zone":     _step_not_implemented("load_zone"),
}


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
        if not str(out[-1]).rstrip().endswith("\\v"):
            out[-1] = str(out[-1]) + "\\v"
        return out
    s = str(text)
    return s if s.rstrip().endswith("\\v") else s + "\\v"


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
    current = dialog_dat
    for i, entry in enumerate(src["lines"]):
        text = entry["text"]
        if prompt:
            text = _ensure_prompt(text)
        paged = bool(entry.get("paged"))
        reuse = [reuse_block[i]] if i < len(reuse_block) else None
        lines = text if isinstance(text, list) else [text]
        current, mids = xi_author.append_dialog_lines(
            current, lines, paged=paged, reuse_ids=reuse)
        line_ids[entry["id"]] = mids[0]
    return current, line_ids


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


def _extract_message_ids(scene: bytes, offset: int, refs: list[int]) -> list[int]:
    """Walk an existing event's bytecode from ``offset`` and return the dialog message
    ids it prints, in order. Used on re-publish to REUSE the same dialog-table slots
    (rewrite in place) instead of appending fresh copies every time.

    Print opcodes + where their 2-byte work-selector sits:
      * ``0x1D`` print_msg   → selector @+1
      * ``0x2B`` print_msg2  → selector @+5 (after the 4-byte speaker)
      * ``0x48`` narrate     → selector @+1
    """
    ids: list[int] = []
    i = offset
    n = len(scene)
    while i < n:
        op = scene[i]
        sub = scene[i + 1] if i + 1 < n else 0
        sz = core._opcode_size(op, sub)
        if not sz:
            break
        sel_off = None
        if op in (OP_PRINT_MSG, OP_NARRATE):
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
            break
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
                     bank_tags: Optional[frozenset] = None) -> CompileResult:
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
    if ev_field == "auto":
        event_id = (max(event_ids_real) + 1) if event_ids_real else 0
        while event_id in (0xFFFE, 0xFFFF):
            event_id += 1
    else:
        event_id = int(ev_field)
        if event_id in event_ids_real:
            idx = actor.event_ids.index(event_id)
            old_off = actor.event_offsets[idx]
            # The old event's print ops give the msg ids in step order; sorting the
            # unique set recovers the contiguous block in dialog-definition order
            # (dialogue is appended in cutscene.dialog.lines order → ascending ids).
            reuse_block = sorted(set(_extract_message_ids(
                actor.scene_data, old_off, actor.references)))
            del actor.event_ids[idx]
            del actor.event_offsets[idx]
            # RECLAIM the old bytecode if it was the LAST event in scene_data
            # (the common case: re-publishing the same event repeatedly). This
            # stops the event DAT growing ~1 event/publish from orphaned dead
            # code. Safe only when nothing else lives past old_off — our events
            # have no absolute internal jumps, and truncating the tail can't
            # shift any other (earlier) event's offset. Interleaved re-publishes
            # of DIFFERENT events still leave a little dead code (rare).
            if not actor.event_offsets or old_off >= max(actor.event_offsets):
                actor.scene_data = bytes(actor.scene_data)[:old_off]

    # 2. Compile dialogue — rewrite the reused block in place, append any extras.
    #    Cinematic cutscenes get the ▼ continue-prompt appended so lines wait for Enter.
    dialog_dat_out, dialog_ids = _compile_dialog(
        cutscene, dialog_dat, reuse_block, prompt=bool(flags.get("cinematic", True)))
    ctx.dialog_ids = dialog_ids

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
        # step.get('label') — reserved for future backpatching (menu/branch/goto).
        emit(step, ctx)

    # A camera-only cutscene (no dialogue/other steps): still reveal it.
    if ctx.pending_fade_in:
        _emit_fade(ctx, ctx.pending_fade_in, hold=0)   # no hold — see above
        ctx.pending_fade_in = ""

    # Auto-append `end` if the user didn't.
    if not steps or steps[-1].get("op") != "end":
        _emit_epilogue(ctx, flags)

    # 5. Splice the new event onto the actor block. (Event id + old-event removal
    #    were resolved in step 1, before dialogue compile.)
    new_offset = len(actor.scene_data)
    if new_offset > MAX_SCENE_OFFSET:
        raise CutsceneCompileError(
            f"actor 0x{owner_entity:08X} scene overflow — u16 offset table")

    actor.references = ctx.refs
    actor.scene_data = bytes(actor.scene_data) + bytes(ctx.code)
    actor.event_offsets.append(new_offset)
    actor.event_ids.append(event_id)
    actor.dirty = True

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
        if bytes(a.scene_data)[off:off + 1] != b"\x00":
            continue
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
        lua_stub=stub,
    )

"""Authoring helpers — synthesize a new NPC dialogue event + grow the dialog table.

Pairs the two halves of an FFXI event:
  * the **dialog** (event-message) DAT — the strings (:mod:`xi.dialog.xi_dialog`);
  * the **event** DAT — the per-actor bytecode that prints them (:mod:`xi.event.xi_event`).

The bytecode for "an NPC says these lines" is tiny and was verified against retail events
(13.6k real ``print_msg`` opcodes across 4 zones): each line is ``print_msg(0x1D)`` + a 2-byte
**work-selector** + ``wait_dismiss(0x23)``, then ``end(0x21)``. The message id is *not* inlined —
it's stored in the actor block's ``references[]`` and the opcode carries a selector
``0x8000 | refIndex`` (LE): the ``0x8000`` high bit flags "this is a reference", the low 15 bits
index ``references[]`` (so up to 0x7FFF entries — retail genuinely uses indices > 127). The
speaker is the current event entity (VM state), not encoded here.
"""

import struct

from xi.dialog import xi_dialog
from xi.event import xi_event as core

PRINT_MSG = 0x1D
WAIT_DISMISS = 0x23
END = 0x21
REF_FLAG = 0x8000            # work-selector "this operand is a references[] index" bit
MAX_REF_INDEX = 0x7FFF       # the selector's low 15 bits index references[]
MAX_SCENE_OFFSET = 0xFFFF    # eventOffsets are u16
SENTINELS = (0xFFFF, 0xFFFE)


def append_dialog_lines(dialog_data: bytes, lines, paged: bool = False, reuse_ids=None):
    """Add dialogue to an event-message (dialog) DAT, editing in place when possible.

    Returns ``(new_dat_bytes, msg_ids)``. ``separate`` (default): one table entry per line →
    one message id each. ``paged``: all lines joined with ▼ page-prompts into a single entry →
    one message id (one box that pages). Lines may use the same escapes as ``dialog edit``
    (``\\n`` newline, ``\\v`` prompt, ``{player}``/``{npc}``/``{auto:N}``).

    **In-place rewrite (``reuse_ids``):** when re-publishing an event, pass the message ids
    the event previously owned (recovered from its bytecode). The Nth output line then
    OVERWRITES ``reuse_ids[N]`` in place — same id, new text — so the dialog DAT does not
    grow no matter how the text changes. Message ids are positional indices into the entry
    table; ``build_container`` recomputes byte offsets, so rewriting an entry's text keeps
    every id stable.

    **Dedup fallback:** lines beyond ``reuse_ids`` (or when none is given) reuse a
    byte-identical existing entry if one exists, else append. So an unchanged cutscene is a
    no-op on the dialog DAT either way; a changed line just overwrites its own slot."""
    if not lines:
        raise ValueError("no dialogue lines given")
    reuse_ids = list(reuse_ids or [])
    blobs, obf = xi_dialog.raw_entry_blobs(dialog_data)
    existing = {}
    for idx, b in enumerate(blobs):
        existing.setdefault(bytes(b), idx)

    def _place(blob: bytes, slot) -> int:
        """Overwrite ``slot`` in place if given+valid; else reuse an identical entry; else append."""
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

    msg_ids = []
    if paged:
        # Join pages with the ▼ "press enter" prompt; encode_event_string turns \v → 7F31.
        text = "\\v".join(str(s) for s in lines)
        slot = reuse_ids[0] if reuse_ids else None
        msg_ids.append(_place(xi_dialog.encode_event_string(text) + b"\x00", slot))
    else:
        for i, line in enumerate(lines):
            slot = reuse_ids[i] if i < len(reuse_ids) else None
            msg_ids.append(_place(xi_dialog.encode_event_string(str(line)) + b"\x00", slot))
    return xi_dialog.build_container(blobs, obf), msg_ids


def append_dialog_block(dialog_data: bytes, blobs_in):
    """Append a CONTIGUOUS run of entries and return ``(new_dat, first_id)``. If the same
    run already exists contiguously (a re-publish), its first id is returned and the DAT
    is left alone; otherwise every entry is appended fresh (no per-entry dedup, so the
    ids stay consecutive — needed by ``say_indexed``, which prints ``base + n``)."""
    blobs_in = [bytes(b) for b in blobs_in]
    if not blobs_in:
        raise ValueError("no dialog blobs given")
    blobs, obf = xi_dialog.raw_entry_blobs(dialog_data)
    n = len(blobs_in)
    for start in range(0, len(blobs) - n + 1):
        if all(bytes(blobs[start + i]) == blobs_in[i] for i in range(n)):
            return dialog_data, start
    first = len(blobs)
    blobs.extend(blobs_in)
    return xi_dialog.build_container(blobs, obf), first


def append_dialog_blobs(dialog_data: bytes, blobs_in, reuse_ids=None):
    """Like :func:`append_dialog_lines` but with pre-encoded entry blobs (raw bytes incl.
    the NUL terminator), for strings cloned from retail entries whose binary placeholder
    codes must survive untouched. Same in-place / dedup / append rules; returns
    ``(new_dat_bytes, msg_ids)``."""
    if not blobs_in:
        raise ValueError("no dialog blobs given")
    reuse_ids = list(reuse_ids or [])
    blobs, obf = xi_dialog.raw_entry_blobs(dialog_data)
    existing = {}
    for idx, b in enumerate(blobs):
        existing.setdefault(bytes(b), idx)
    msg_ids = []
    for i, blob in enumerate(blobs_in):
        blob = bytes(blob)
        slot = reuse_ids[i] if i < len(reuse_ids) else None
        if slot is not None and 0 <= slot < len(blobs):
            blobs[slot] = blob
            existing.setdefault(blob, slot)
            msg_ids.append(slot)
        elif blob in existing:
            msg_ids.append(existing[blob])
        else:
            idx = len(blobs)
            blobs.append(blob)
            existing[blob] = idx
            msg_ids.append(idx)
    return xi_dialog.build_container(blobs, obf), msg_ids


def build_dialogue_bytecode(refs, msg_ids):
    """Build the scene bytecode for a show-messages event.

    Returns ``(bytecode, new_refs)``. Each message id gets a ``references[]`` slot (reused if the
    id is already present); the opcode stream is ``print_msg <selector> · wait_dismiss`` per id
    (selector = ``0x8000 | refIndex``), terminated by ``end``."""
    refs = list(refs)
    code = bytearray()
    for mid in msg_ids:
        if mid in refs:
            idx = refs.index(mid)
        else:
            idx = len(refs)
            refs.append(mid)
        if idx > MAX_REF_INDEX:
            raise ValueError(
                f"actor reference table is full (index {idx} > {MAX_REF_INDEX})")
        code += bytes([PRINT_MSG]) + struct.pack('<H', REF_FLAG | idx)
        code += bytes([WAIT_DISMISS])
    code += bytes([END])
    return bytes(code), refs


def add_dialogue_event(actors, actor_id: int, msg_ids, event_id=None):
    """Splice a new show-messages event onto ``actor_id`` (created if the actor has no block yet).
    Mutates ``actors`` (a :func:`xi.event.xi_event.parse_raw_actors` list) and returns the
    assigned event id. Raises ``ValueError`` if the actor's scene/refs can't grow to fit it."""
    actor = next((a for a in actors if a.actor_id == actor_id), None)
    created = actor is None
    if created:
        actor = core.RawActor(actor_id & 0xFFFFFFFF, [], [], [], b"", b"", b"", dirty=True)
        actors.append(actor)

    bytecode, new_refs = build_dialogue_bytecode(actor.references, msg_ids)
    new_offset = len(actor.scene_data)
    if new_offset > MAX_SCENE_OFFSET:
        raise ValueError(
            f"actor 0x{actor_id:08X} scene is too large ({new_offset} bytes) to add another "
            f"event — its entry offset would overflow the u16 offset table")

    real_ids = [e for e in actor.event_ids if e not in SENTINELS]
    if event_id is None:
        # Free on EVERY actor: an id shared with another block makes the client run that
        # actor's same-numbered event as a participant of ours.
        zone_ids = {e for a in actors for e in a.event_ids if e not in SENTINELS}
        event_id = (max(real_ids) + 1) if real_ids else 0
        while event_id in SENTINELS or event_id in zone_ids:
            event_id += 1
    else:
        event_id = int(event_id)
        if event_id in real_ids:
            raise ValueError(f"event id {event_id} already exists on actor 0x{actor_id:08X}")
    if not (0 <= event_id < 0xFFFE):
        raise ValueError(f"event id {event_id} out of range (0..65533)")

    actor.references = new_refs
    actor.scene_data = bytes(actor.scene_data) + bytecode
    actor.event_offsets.append(new_offset)
    actor.event_ids.append(event_id)
    actor.dirty = True
    return event_id, created


def lua_stub(actor_id: int, event_id: int, actor_name: str = None,
             cast_stage: list | None = None, server_flags: int = 0) -> str:
    """Server-side trigger stubs (LandSandBoat / CatsEyeXI style).

    ``cast_stage`` — accepted for API compatibility but intentionally NOT emitted as
    staging code. Server-side staging (setPos + setStatus(NORMAL) before
    startCutscene) is not just unnecessary — it BREAKS end-of-scene hiding: event
    teardown resets every actor to its default state, and staging makes that default
    "visible, standing in the world", so hidden cast NPCs pop back when the cutscene
    ends. The compiler's involvement blocks make the client CHARREQ each cast NPC
    itself; status-6 NPCs default to hidden and re-hide automatically at teardown.

    ``server_flags`` — event-packet Mode bits, sent via the lua table form
    ``startCutscene(id, { flags = N })`` → packet 0x032 ``Mode`` → client
    ``CliEventMode``. Low-byte bit 0x10 hides every NPC not involved in the event,
    0x02 hides other players (xiclient CheckHide OR-gate; retail drives these
    server-side — retail event DATs carry no hide bits in the 0x38 operand).
    """
    who = actor_name or f"actor 0x{actor_id:08X}"
    start_call = (f"player:startCutscene({event_id}, {{ flags = 0x{server_flags:02X} }})"
                  if server_flags else f"player:startCutscene({event_id})")
    stage_block = ""
    restore_block = ""
    if cast_stage:
        stage_block = (
            "    -- ★ Cast NPCs need NO server-side staging: the event DAT makes the client\n"
            "    -- fetch each one itself (CHARREQ), and their status-6 default keeps them\n"
            "    -- hidden outside the scene. Do NOT setPos/setStatus(NORMAL) them here —\n"
            "    -- that makes their default state visible and they will POP BACK into the\n"
            "    -- world when the cutscene ends (teardown resets actors to defaults).\n"
        )

    return (
        f"-- ─── Cutscene trigger stubs — event id {event_id}, actor {who} ───\n"
        f"-- Pick ONE of the three patterns below (or combine).\n"
        f"-- ★ Use startCutscene (not startEvent) so the player locks into CUTSCENE mode.\n"
        f"\n"
        f"-- (A) Target-and-interact: paste into scripts/zones/<Zone>/npcs/{actor_name or '<NPC>'}.lua\n"
        f"function onTrigger(player, npc)\n"
        f"{stage_block}"
        f"    {start_call}\n"
        f"    return 1\n"
        f"end\n"
        f"\n"
        f"-- (B) Once-per-character zone-in: paste into scripts/zones/<Zone>/Zone.lua\n"
        f"function onZoneIn(player, prevZone)\n"
        f"    if player:getCharVar('cutscene_{event_id}') == 0 then\n"
        f"        player:setCharVar('cutscene_{event_id}', 1)\n"
        f"{stage_block}"
        f"        return {event_id}\n"
        f"    end\n"
        f"    return -1\n"
        f"end\n"
        f"\n"
        f"-- (C) Trigger region (walk into a bounding box): paste into the zone Lua\n"
        f"function onInitialize(zone)\n"
        f"    zone:registerRegion(1, -10, 0, -10, 10, 5, 10)  -- edit coords\n"
        f"end\n"
        f"function onRegionEnter(player, region)\n"
        f"    if region:getID() == 1 and player:getCharVar('cutscene_{event_id}') == 0 then\n"
        f"        player:setCharVar('cutscene_{event_id}', 1)\n"
        f"{stage_block}"
        f"        {start_call}\n"
        f"    end\n"
        f"end\n"
        f"\n"
        f"function onEventFinish(player, csid, option)\n"
        f"    if csid == {event_id} then\n"
        f"{restore_block}"
        f"        -- reward / state change here\n"
        f"    end\n"
        f"end\n")

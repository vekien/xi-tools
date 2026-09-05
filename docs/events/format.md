# Event DAT format

The binary layout of a zone's **Event DAT** — the per-actor blocks of event
**bytecode** ("scenes") that drive cutscenes and NPC interactions — plus the
**scene-VM opcode reference**.

> Source: reverse-engineered from `thirdparty/xeno projects/FFXI Dat Parser/FFXI DAT
> PARSER/TOOLS/DAT/TYPES/EventDat.cs` and Atom0s's **XiEvents**
> (<https://github.com/atom0s/XiEvents>). Verified at the structural level (it parses
> real DATs); confirm individual opcode argument widths against bytes before relying
> on them.

---

## Where the Event DAT sits

Each zone has four related DATs (entities, event bytecode, JP strings, NA strings).
Resolve the Event DAT's file_id from the zone ID with the formula in
[README.md](README.md#per-zone-file-ids) (base ROM: `5820 + zone_id`), then
[`xi ftable lookup`](../ftable/lookup.md) for its path. Event DATs carry the `evte`
magic (see [../dats.md](../dats.md)).

---

## File layout

The Event DAT is a list of **blocks**, one block per **actor** (an NPC / object that
owns events). Little-endian throughout.

```
u32   blockCount
u32   blockSizes[blockCount]      # byte length of each actor block
... blockCount actor blocks, concatenated (each blockSizes[i] bytes) ...
```

### Actor block

```
u32   actorId                     # server entity id (maps to a name via the Entity DAT)
u32   eventCount
u16   eventOffsets[eventCount]    # byte offset of each event's entry-point INTO sceneData
u16   eventIds[eventCount]        # the event id at each offset (0xFFFF = placeholder, 0xFFFE = wildcard — see below)
u32   refCount
u32   references[refCount]        # ids the events reference (other actors / external data)
i32   sceneSize                   # unpadded bytecode length; trailing pad may follow the scene blob
u8    sceneData[sceneSize]        # the event BYTECODE for all of this actor's events
```

- **`eventIds[i]` → `eventOffsets[i]`** is the lookup the engine uses: "run event N on
  actor A" finds `N` in `eventIds`, then begins executing at `sceneData[eventOffsets[i]]`.
- `eventId` `0xFFFF` is a **placeholder id** on an otherwise-real entry offset — very common
  (~130k instances across the retail zone corpus, most with distinct offsets); it can't be
  requested by id, so per-id tooling skips it. `0xFFFE` is different: a **wildcard the engine
  DISPATCHES** — it matches *any* requested event id (kept as a real match; see
  `external_source/dump_event.py`). It clusters on the zone/master block (`0x7FFFFFF0`).
  Byte census 2026-08: 129,845 × `0xFFFF` vs 337 × `0xFFFE`. Don't treat the two as one
  "sentinel/skip" bucket — xi's listing parser skips both, which is fine for listing but
  not a model of engine dispatch.
- `sceneData` is one shared bytecode buffer per actor; the per-event offsets carve it
  into individual scenes. Execution can fall through / jump across the buffer.
- **`sceneSize` is the *unpadded* bytecode length** (it can be non-multiple-of-4). The block is
  then padded up to a 4-byte boundary by a few trailing bytes *after* the scene (observed
  `0xff`) that the engine ignores — it reads `scene = block[sceneStart : sceneStart + sceneSize]`.
  Every header field is 4-aligned, so padding the scene keeps the whole block a multiple of 4.
  xi's writer (`xi_event.build_event_dat`, [authoring.md](authoring.md)) preserves this for a
  byte-exact round-trip — **verified across 11 zones**.

---

## Operand references — the `references[]` / work-selector model

Most opcode operands are **not literals**. The actor block carries a `references[]` table
(Atom0s calls it *ImidData*); an opcode argument is usually a **2-byte work-selector** that
*points into* that table, and the value stored there is the real argument — a zone id, a
frame count, a message id, an entity-magic id, a scheduler resource id, …

The selector's **high bit `0x8000`** is the "this is a reference" flag:

```
selector u16 (little-endian)
  bit 0x8000 set   → references[ selector & 0x7FFF ]   # the stored value is the argument
  bit 0x8000 clear → a runtime work-area slot          # value computed at run time; not static
```

So a `0x34 load_zone` whose two operand bytes are `18 80` decodes as selector `0x8018`:
high bit set → `references[0x18]` (= `references[24]`). If that table entry holds `0x7E`
(126), the instruction loads **zone 126**. The raw `1880` is therefore neither a zone id
nor a file id — it's an *index indirection* through `references[]`.

> The same indirection drives dialogue (`0x48 print_msg` → `references[i]` = a message id —
> see [dialogue.md](dialogue.md)) and the scheduler/camera focus opcodes (`0x45` etc. — see
> [cutscenes.md](cutscenes.md#how-the-camera-works)). xi resolves it in
> `src/xi/event/xi_event.py` via `_resolve_work_selector` (2-byte work-selector only —
> including print opcodes). Selectors with the high bit clear can't be resolved statically
> — they depend on VM run-time state.
>
> **Work-slot map (from the PS2 decompile, `XiEvent::getworkofs`):** `< 0x0800` =
> `Work_Local[]` (per-event scratch, 64 ints), `0x1000–0x17FF` = `Work_Zone[]` (shared by
> every event in the zone), `0x7F00–0x7F0B` = live fields of the event entity (pos X/Y/Z
> ÷1000, heading ×4096/2π, is-local-player, server id…), `0x7F80–0x7F8B` = the same for the
> local player. Special actor ids: `0x7FFFFFF0` player, `0x7FFFFFF1…F5` party members 1–5,
> `0x7FFFFFF8` the event's own entity. Details: [reference/ps2_decomp_crosscheck.md](../reference/ps2_decomp_crosscheck.md) §4.2–4.3.

---

## Entity (NPC) definitions

The Event DAT only stores **`actorId`s**. The human name / model / spawn data for each
actor lives in the zone's **Entity (NPC) DAT** (file_id `6720 + zone_id` for the base
ROM; see [README.md](README.md#per-zone-file-ids)). The xeno parser resolves
`actorId → name` by joining against that Entity DAT (`shared.entityDat.entities`,
matching on `serverID`). So to fully decode "who says what", you read **both**:

- **Entity DAT** → `actorId → name / model`
- **Event DAT** → `actorId → eventId → scene bytecode → printed dialogue id`

The already-extracted [event-data.md](event-data.md) dataset has done this join for you
(actor name + event id + dialogue strings, per zone).

---

## The scene bytecode (event VM)

`sceneData` is a little instruction stream for a tiny **virtual machine** (Atom0s calls
the per-actor state the *ReqStack*). The VM keeps an **exec pointer**, a set of
**registers / variables**, **bit flags**, **jump** and **wait** state, and the current
**event entity / target entities**. Each instruction is **`opcode:u8`** followed by a
fixed number of argument bytes (the width is per-opcode; the parser advances the exec
pointer by the right amount each time).

Opcodes fall into a few families:

| Family | Opcodes (examples) | Purpose |
|---|---|---|
| **Arithmetic / logic** | `0x07` add, `0x08` sub, `0x14` mul, `0x15` div, `0x0D`–`0x11` and/or/xor/shift, `0x12`/`0x13` rand, `0x16`–`0x18` sin/cos/atan2 | compute values into registers |
| **Variables / flags** | `0x03` get→store, `0x05`/`0x06` set 1/0, `0x09`/`0x0A` set/clear bit, `0x0B`/`0x0C` inc/dec | scratch state & bit flags |
| **Control flow** | `0x01` set exec-pointer, `0x02` `if` conditionals, `0x1A` jump, `0x1B` break jump, `0x3E` test-bit branch, `0x44` entity-valid branch, `0x21` **end event** | branching & termination |
| **Dialogue / menus** | `0x1D`/`0x2B` print message (with speaker), `0x48`/`0x49` print (no speaker), `0x23` wait for player to dismiss, `0x24` open select menu, `0x25` wait for select, `0x40`/`0x41` set/test menu-option flags | see [dialogue.md](dialogue.md) |
| **Entity / scheduler tasks** | `0x2C` create a `CMoSchedularTask` (entity action, 13 bytes), `0x2D` zone task, `0x45` start task, `0x50`–`0x55` end / wait-for task | drive NPC animation & scripted action |
| **Camera / player lock** | `0x20` lock player control, `0x38` `CliEventModeLocal` (hide entity, control camera, hide UI…), `0x46` enable/disable camera control + hide menus | cutscene presentation |
| **Position / facing** | `0x1F`/`0x31`/`0x36`/`0x37` update event position, `0x39`/`0x3A`/`0x3B` directions/yaw, `0x47` send player position to server, `0x4A`/`0x4B` look-at / set yaw, `0x1E` look-at + "talking" mouth animation | move & orient actors |
| **Doors / world** | `0x4C` open door, `0x4D` close door, `0x4E` hide flag, `0x4F` set StatusEvent, `0x34`/`0x35` load extra zone | scene world changes |
| **Server sync** | `0x43` tell server the event updated / completed, `0x27`–`0x2A` `ReqSet`/`GetReqStatus` helpers | client↔server handshake |
| **Timing / yield** | `0x57` frame delay, `0x1C` wait_time (3 bytes), `0x26`/`0x58` yield the VM | pacing |

> The **complete per-opcode table (0x00–0xD9)** is in **[opcodes.md](opcodes.md)**.
> Atom0s's XiEvents documents the full opcode set (descriptions, argument layouts, and
> the engine functions each calls). The xeno `EventDat.cs` `ParseScene` switch is a
> compact local copy of those handlers (with the exec-pointer step per opcode). Treat
> XiEvents as the authoritative reference.

### How an event runs (sketch)

```
find eventId in actor.eventIds → start at sceneData[eventOffsets[i]]
loop:
  read opcode at execPointer
  do its action (compute / move camera / animate NPC / print dialogue / …)
  advance execPointer by the opcode's arg width  (or jump / branch)
until 0x21 "end event" (or the buffer ends)
```

Dialogue printing (`0x1D`/`0x2B`/`0x48`) takes a **message id** and looks the string up
in the zone's dialogue table — see [dialogue.md](dialogue.md). A worked end-to-end walk
through a cutscene is in [cutscenes.md](cutscenes.md).

---

## Related

- [dialogue.md](dialogue.md) — the string tables the print opcodes resolve into.
- [cutscenes.md](cutscenes.md) — the VM in motion: camera, entities, doors, menus.
- [event-data.md](event-data.md) — the already-extracted actor→event→dialogue dataset.
- [../zone/zones.md](../zone/zones.md) — per-zone Model/Dialog/NPC/Event DAT paths.

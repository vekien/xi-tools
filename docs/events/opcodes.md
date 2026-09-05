# Event VM opcode reference

The complete instruction set for the [event bytecode VM](format.md#the-scene-bytecode-event-vm)
that drives cutscenes and NPC interactions. Each scene instruction is **`opcode:u8`**
followed by a fixed number of argument bytes (the VM advances its exec-pointer by the
opcode's width).

> **Source & trust.** Descriptions are from Atom0s's **XiEvents**
> (<https://github.com/atom0s/XiEvents>) as mirrored in the xeno `EventDat.cs`
> `ParseScene` handler (`thirdparty/xeno projects/FFXI Dat Parser/…/TYPES/EventDat.cs`).
> XiEvents is the **authoritative** reference for opcode **semantics** (the descriptions).
> The **`len` column is code-derived** from xi's validated disassembler (`_FIXED_SIZES` /
> `_SUB_TABLES` in `src/xi/event/xi_event.py`) and is byte-identical to the UE FFXIEngine
> client's `kFixedSizes`/sub-tables; `var` marks a multi-case opcode whose length depends on
> a sub-selector byte — confirm those against the bytes before emitting.
> Anything marked *deprecated* has no live handler.

---

## Opcodes vs. DAT section block types (two namespaces)

⚠️ These are **event-VM opcodes**: bytecode instructions inside the Event DAT's per-actor
`sceneData` stream (`opcode:u8` + args — see [format.md](format.md#the-scene-bytecode-event-vm)).
They are **not** the same thing as FFXI **DAT section block-types** (`sectionMeta & 0x7F`) —
the resource-container type codes catalogued in
[../reference/dat_sections.md](../reference/dat_sections.md). Both systems use small hex
numbers, so the same byte means **completely different things** depending on which one
you're reading:

| code | as an **event-VM opcode** (this doc) | as a **DAT section block-type** ([dat_sections.md](../reference/dat_sections.md)) |
|------|--------------------------------------|------------------------------------------------------------------------------------|
| `0x05` | sets a value to 1 (math) | **ParticleGenerator** — a visual effect/emitter |
| `0x06` | sets a value to 0 (math) | **Route** — path/route data, incl. the **camera control / camera path** |
| `0x07` | add two values, store (math) | **EffectRoutine** — the **scheduler/timeline** that sequences effects **and the camera timeline** |

So when an engine source says *"the camera timeline VM is block type `0x07`, camera
controls are block type `0x06`, and the scheduler is the same one effects use"*, that's
the **section block-type** namespace, **not** these opcodes — and it lines up with
[dat_sections.md](../reference/dat_sections.md): `0x07` EffectRoutine is already the shared
effect **scheduler/sequencer**, and `0x06` Route is the camera **path**. The event bytecode
documented here only **triggers** that data (`0x38`/`0x46` set the camera mode, `0x45`
start-task fires a scheduler resource); the camera move itself lives in those `0x06`/`0x07`
**sections**, not inline in this opcode stream. See
[cutscenes.md](cutscenes.md#how-the-camera-works).

---

## Reading the table

- **op** — the opcode byte.
- **len** — total instruction length in bytes (`opcode + args`), **code-derived** from the
  disassembler's size tables (`_FIXED_SIZES` / `_SUB_TABLES`). **var** = length depends on a
  sub-selector byte (confirm against the bytes). A `—` len marks a multi-opcode *group* row
  whose members differ in length — size each opcode from the code.
- Opcodes that **read/store values** operate on the VM's registers/variables; "value"
  args are usually an immediate or a register reference resolved at runtime.

---

## 0x00–0x1F — flow, math, logic

| op | len | description |
|----|-----|-------------|
| `0x00` | 1 | Ends the current ReqStack execution; resets it to defaults. |
| `0x01` | 3 | Directly sets the exec-pointer position. |
| `0x02` | 8 | `if` — handles multiple kinds of conditional. Branches the exec-pointer. |
| `0x03` | 5 | Gets a value, then stores it. |
| `0x04` | 3 | *Deprecated* (no-op). |
| `0x05` | 3 | Sets a value to 1. |
| `0x06` | 3 | Sets a value to 0. |
| `0x07` | 5 | Add two values, store the result. |
| `0x08` | 5 | Subtract two values, store. |
| `0x09` | 5 | Set a bit flag, store. |
| `0x0A` | 5 | Clear a bit flag, store. |
| `0x0B` | 3 | Increment a value, store. |
| `0x0C` | 3 | Decrement a value, store. |
| `0x0D` | 5 | Bitwise AND of two values, store. |
| `0x0E` | 5 | Bitwise OR, store. |
| `0x0F` | 5 | Bitwise XOR, store. |
| `0x10` | 5 | Left-shift, store. |
| `0x11` | 5 | Right-shift, store. |
| `0x12` | 3 | `rand()` → store. |
| `0x13` | 5 | `rand()` with a modulus → store. |
| `0x14` | 5 | Multiply two values, store. |
| `0x15` | 5 | Divide two values, store. |
| `0x16` | 7 | `sin` of two values, store. |
| `0x17` | 7 | `cos`, store. |
| `0x18` | 7 | `atan2`, store. |
| `0x19` | 5 | Read two values, store byte-flipped (endian swap). |
| `0x1A` | 3 | Jump to a new position in the event data (pushes onto the JumpStack). |
| `0x1B` | 1 | Break from the most recent jump on the JumpStack. |
| `0x1C` | 3 | Set/update (decrease) the current `ReqStack[RunPos].WaitTime`. *(Note: a separate handler, surfaced as `0x2C` in the parser, creates a `CMoSchedularTask` on an entity — an entity action/animation.)* |
| `0x1D` | 3 | **Print event message**, speaker = `EntityTargetIndex[1]`. Operand = a 2-byte [work-selector → `references[]`](format.md#operand-references--the-references--work-selector-model) **message id** (`0x8000 \| refIndex`; verified across 13.6k retail print ops, 17% with index > 127). See [authoring.md](authoring.md). |
| `0x1E` | 5 | Tell an entity to look at another and start "talking" (mouth-move animation). |
| `0x1F` | var | Update the event position information. |

---

## 0x20–0x3F — presentation, dialogue, entities, branching

| op | len | description |
|----|-----|-------------|
| `0x20` | 2 | Set `CliEventUcFlag` — **lock the player out of controlling their character**. |
| `0x21` | 1 | **End event** — sets `EventExecEnd = 1`. |
| `0x22` | 2 | `XiAtelBuff::SetEventHideFlag` on the current event entity. |
| `0x23` | 1 | **Wait** for the player to dismiss a dialog message. |
| `0x24` | 7 | **Open a selection menu** for the player to choose from. |
| `0x25` | 1 | **Wait** for a menu select (from `0x24`). |
| `0x26` | 1 | Yield the VM. *(May be deprecated.)* |
| `0x27` | 7 | `FUNC_REQSet` → `XiEvent::ReqSet` (server-request helper). |
| `0x28`–`0x29` | 7 | Like `0x27` with extra checks; end by calling `XiEvent::GetReqStatus`. **The tag byte is a SLOT INDEX into the target entity's event offset table, not an event id** (client: `StackExecPointer = TagOffset[tagnum]`). Laityn 10003 drives her walk, lines and camera cues with `29 08 <Laityn> 0A..1A`, slots 10-26 of her 30-entry table. So an actor's table order is load-bearing: the compiler keeps a replaced event in its original slot and refuses a compile that reorders existing slots (found in game 2026-09-05: the moved entry shifted every later slot by one). |
| `0x2A` | 6 | Like `0x27`/`0x28` with extra checks; ends by calling `XiEvent::GetReqStatus`. *(6 bytes, not 7 — matches `_FIXED_SIZES`; an earlier revision folded it into the 0x28–0x2A row.)* |
| `0x2B` | 7 | **Print event message** with a given entity as speaker (like `0x1D`). Message operand = the same 2-byte `references[]` work-selector, at byte offset 5. |
| `0x2C` | 13 | Create/load a `CMoSchedularTask` on an entity (an **entity action/animation**). |
| `0x2D` | 13 | Create/load a **zone-based** `CMoSchedularTask` (a scheduled zone action). |
| `0x2E` | 1 | Set `CliEventCancelSetData` (and `CliEventCancelFlag` if armed). |
| `0x2F` | 6 | Adjust the given entity's `Render.Flag0`. |
| `0x30` | 1 | Set `ucoff_continue = 0`. |
| `0x31` | var | Update the event position information. |
| `0x32` | 3 | Set `ExtData[1]->MainSpeed`. |
| `0x33` | 2 | Adjust the event entity's `Render.Flags0`. |
| `0x34` | 3 | **Load an additional zone** for the event (with `XiZone::Close` — a full swap). Operand = a 2-byte [work-selector → `references[]`](format.md#operand-references--the-references--work-selector-model) **zone id**. |
| `0x35` | 3 | Like `0x34`, but **without** `XiZone::Close` (overlay / restore the previous zone). Same selector → zone-id operand. |
| `0x36` | 7 | Update `ExtData[1]->EventPos`, recalibrate the event entity. |
| `0x37` | 9 | Update `EventPos` + `EventDir[1]`, recalibrate, `CopyAllPosEvent` + `ReqExecHitCheck`. |
| `0x38` | 3 | Set the low word of **`CliEventModeLocal`** (hide entities, control camera, hide UI, move camera, alter movement…). |
| `0x39` | 3 | Set `ExtData[1]->EventDir[1]`. |
| `0x3A` | 7 | Convert a float yaw → single-byte rep, store. |
| `0x3B` | 11 | Get an entity's position (or `EventPos`), store. |
| `0x3C` | 7 | Compare two values (shift); if met, set a bit flag, store. |
| `0x3D` | 7 | Compare two values (shift); if met, clear a bit flag, store. |
| `0x3E` | 7 | **Test a bit**, branch the exec-pointer on its state. |
| `0x3F` | 7 | Remainder of two values, store. |

---

## 0x40–0x5F — menus, camera, tasks, doors, server

| op | len | description |
|----|-----|-------------|
| `0x40` | 9 | Set a bit flag, store — e.g. **which dialog menu options are enabled**. |
| `0x41` | 9 | Like `0x40`; paired with it to **test/read available menu-option flags**. |
| `0x42` | 1 | Clear `CliEventCancelSetData` (and `CliEventCancelFlag` if armed). |
| `0x43` | 2 | **Tell the server** the client updated / completed the event. |
| `0x44` | 5 | Test if an entity is valid; branch the exec-pointer. |
| `0x45` | 17 | **Start a scheduled task** with two given entities. |
| `0x46` | var | **Enable/disable player camera control** + hide menus for cutscene playback. |
| `0x47` | var | Update the player's location during the event (sends packet `0x05C`). |
| `0x48` | 3 | **Print event message, no speaker** (narration). Message operand = the 2-byte `references[]` work-selector at byte offset 1 (same as `0x1D`). |
| `0x49` | 7 | Print event message, no speaker. Message operand = the 2-byte selector at byte offset 1. |
| `0x4A` | 9 | Tell an entity to look at another entity. |
| `0x4B` | 7 | Update an entity's yaw direction. |
| `0x4C` | 1 | **Open door** — set `StatusEvent = 8` if a `Render.Flags0` bit isn't set. |
| `0x4D` | 1 | **Close door** — set `StatusEvent = 9`. |
| `0x4E` | 6 | Set an entity's event-hide flag in `Render.Flags0`. |
| `0x4F` | 3 | Set an entity's `StatusEvent` to a given value. |
| `0x50` | 13 | End a `CMoSchedularTask`. |
| `0x51` | 13 | End a zone-based `CMoSchedularTask`. |
| `0x52` | 15 | End a `CMoSchedularTask` (Load/Main). |
| `0x53` | 13 | **Wait** for an entity's scheduler to finish its current action. |
| `0x54` | 13 | Wait for the zone scheduler to finish. |
| `0x55` | 15 | Wait for the Main/Load scheduler to finish. |
| `0x56` | 5 | *Deprecated* (reads values, does nothing). |
| `0x57` | 3 | Create a frame delay from the current delay value, store. |
| `0x58` | 1 | Yield the VM. |
| `0x59` | var | Multiple cases — update an entity's event data. |
| `0x5A` | var | Update the event position information (multi-field). |
| `0x5B` | 15 | Load an extended scheduler task. *(Fixed 15 — corpus-verified 2026-08: 67k clean decodes across all retail zone events; an external claim of a 17-byte variant has no instance in this corpus.)* |
| `0x5C` | var | Multiple cases — **the music player**. |
| `0x5D` | 5 | Set/ease the current music to a new volume. |
| `0x5E` | 5 | Stop the event entity's action, reset to idle motion. |
| `0x5F` | var | Multi-case dispatcher (calls `0x53`/`0x5B`/`0xC1`). |

---

## 0x60–0x7F — animation, audio, HUD, weather/time, input

| op | len | description |
|----|-----|-------------|
| `0x60` | var | *Deprecated* (old 2-byte default case, now skipped). |
| `0x61` | 2 | Adjust the event entity's `Render.Flags2`. |
| `0x62` | 17 | Like `0x45` with a different second argument. |
| `0x63` | 3 | **Play an animation on the event entity and wait** for it to complete. |
| `0x64` | 11 | Distance between two points, store. |
| `0x65` | 11 | 3D distance between two entities, store. |
| `0x66` | 15 | Like `0x5B` with different args. *(Fixed 15 — corpus-verified 2026-08, 82k clean decodes; same note as `0x5B`.)* |
| `0x67` | 5 | **Hide the entire HUD** for the cutscene (compass, status, chat, menus…). |
| `0x68` | 1 | **Unhide the HUD**. |
| `0x69` | 4 | Set the volume of a sound type. |
| `0x6A` | 7 | Change the volume of a sound type. |
| `0x6B` | 9 | Stop an entity's action, reset to idle motion. |
| `0x6C` | 9 | **Fade an entity's color** in/out. |
| `0x6D` | 7 | *Deprecated* (no-op). |
| `0x6E` | 7 | Play an **emote animation** on an entity. |
| `0x6F` | 1 | Sleep the VM until `WaitTime` reaches 0 (yieldable sleep). |
| `0x70` | 1 | Check the event entity render flag; yield if set, else cancel movement & advance. |
| `0x71` | var | Handle **string input** from the player (passwords/prompts). *(Sub `0x20` = 16 bytes — corpus-verified 2026-08: every retail site is `71 20` + seven u16 param refs `0x1002…0x1008`, then a companion `71 21`; an external claim of 10 bytes splits that operand run.)* |
| `0x72` | var | Load event weather info and apply it ([weather ids](weather.md)). *(Sizes {4, 6}; an external catalog adds a 10-byte case — no instance found in the zone-event corpus; unadjudicated. Beware `0x72` = ASCII `'r'` when scanning: naive sweeps hit it constantly inside embedded text.)* |
| `0x73` | 11 | Schedule **cast-magic** tasks on two entities. |
| `0x74` | 2 | Adjust the event entity's `Render.Flags1`. |
| `0x75` | var | Load a room, update the player's sub-region with the server. |
| `0x76` | 5 | Check entity `Render.Flags0`/`Flags3`, yield if successful. |
| `0x77` | 5 | **Disable the game clock, set a specific time** (and optionally [weather](weather.md)) for the event. |
| `0x78` | 1 | Enable the game timer, reset zone weather ([ids](weather.md)). |
| `0x79` | var | Look at / rotate toward another entity. |
| `0x7A` | var | Multi-case VM control (reset VM / ReqStack, copy/share ExtData…). |
| `0x7B` | 5 | Unset an entity's talking status (`NpcSpeechFrame = -1`). |
| `0x7C` | 6 | Adjust an entity's `Render.Flags2`. |
| `0x7D` | 3 | Start a scheduled task using the local player (e.g. rank-up animations). |
| `0x7E` | var | Multi-purpose — chocobos & mounts. |
| `0x7F` | 1 | Wait for a player selection. |

---

## 0x80–0xAF — map, world-pass, crafting, time, **camera position**

| op | len | description |
|----|-----|-------------|
| `0x80` | 5 | Test an entity for several conditions; yield or advance (e.g. loading an action). |
| `0x81` | 6 | Set an unknown value in an entity's warp data. *(Semantics unverified prose — no code corroboration; an external catalog reads it as "blinking". Size 6 agrees on both sides.)* |
| `0x82` | 7 | Hit-test a rect from the current event entity's position. |
| `0x83` | 3 | Get & store the current game time. |
| `0x84` | 1 | Adjust the event entity's `Render.Flags3`. |
| `0x85` | 1 | Open a Mog House sub-menu by parameter. |
| `0x86` | 6 | Adjust an entity's `Render.Flags3`. |
| `0x87`/`0x88` | — | World-pass generation (sends `0x01B` packets). |
| `0x89` | 3 | **Open the map** (`/map`) for use in the event (NPC marks/tours). |
| `0x8A` | 1 | Close the map window. |
| `0x8B` | 25 | Set/update a **map marker** point. |
| `0x8C` | var | Crafting helper (recipes, synth support…). |
| `0x8D` | 5 | Open the map window with given properties. |
| `0x8E`/`0x8F` | — | Set event entity's event status to 45 / 46 if valid. |
| `0x90` | 1 | Adjust event entity's `Render.Flags0` + `Flags1`. |
| `0x91` | 3 | Set `ExtData[1].MainSpeedBase`. |
| `0x92` | 6 | Adjust an entity's `Render.Flags3`. |
| `0x93` | 3 | Display item information. |
| `0x94` | 6 | Adjust an entity's `Render.Flags3`. |
| `0x95`/`0x96` | — | Set / unset an entity as an event-based NPC. |
| `0x97` | 5 | Save & set zone `WindBase`/`WindWidth`. |
| `0x98` | 1 | Yield while the zone is loading data. |
| `0x99` | 5 | Yield while a given entity is playing an animation. |
| `0x9A` | 1 | Yield while the music server is reading data. |
| `0x9B` | 1 | Yield while the **event entity** is playing an animation. |
| `0x9C` | 3 | Store the client language id. |
| `0x9D` | var | Multi-purpose string handler. |
| `0x9E` | 2 | Set `PTR_RectEventSendFlag`. |
| `0x9F`–`0xA3` | — | Variants of `0x45` / `0x52` / `0x55` with different args. |
| `0xA4`/`0xA5` | — | Adjust event entity `Render.Flags3`. |
| `0xA6` | var | Request the event map number from the server (`0x0EB` packet), yield until answered. |
| `0xA7` | var | Uses `ExtData[1]->Unknown0003/0004`. |
| `0xA8` | 6 | Open the map (if requested), unlock & rename markers. |
| `0xA9` | 3 | Disable game time, set a specific time. |
| `0xAA` | 17 | Build a Vana'diel timestamp, split into parts, store. |
| `0xAB` | var | Multi-case — alter entity render flags. *(Sizes {2, 4}; an external catalog adds a 6-byte case — no instance found in the zone-event corpus; unadjudicated.)* |
| `0xAC` | var | Multi-case — set entity `StatusServer`/`StatusEvent`/`Render.Flags6/7`. |
| `0xAD` | 12 | Multi-case — scheduler actions on two entities. |
| `0xAE` | var | Multi-case — weather, entity name color, EnvironmentAreaId… |
| **`0xAF`** | 8 | **Get & store the camera position values.** |

---

## 0xB0–0xD9 — dialogue, delivery/rankings, names/gear, chocobo, maps

| op | len | description |
|----|-----|-------------|
| `0xB0` | 12 | **Print event message** using given entities as speaker **and listener**. |
| `0xB1` | 4 | Get & store a flag value (`PTR_UnknownValue`, init 128). |
| `0xB2` | var | Delivery box — open / wait for it to open. |
| `0xB3` | var | Rankings boards (e.g. Chenon's fishing ranks in Selbina). |
| `0xB4` | var | Multi-use handler. |
| `0xB5` | 4 | **Set the current event entity's name.** |
| `0xB6` | var | Multi-use — entity looks / gear visuals. |
| `0xB7` | var | Multi-use handler. |
| `0xB8` | 27 | Open the map (if requested), add & set markers. |
| `0xB9` | 8 | Open the map (if requested), edit & rename a marker (name from the event read buffer). |
| `0xBA` | 13 | Calibrate an entity's position, `CopyAllPosEvent` + `ReqExecHitCheck`. |
| `0xBB`–`0xBD` | — | Variants of `0x45` / `0x52` / `0x55`. |
| `0xBE` | 3 | Store `ReqStack[RunPos].WhoServerId`. |
| `0xBF` | var | Chocobo racing (debug strings present). |
| `0xC0` | 3 | Adjust event entity `Render.Flags3`. |
| `0xC1` | 5 | Test an entity; if ok, kill its last action and delete its resp data. |
| `0xC2` | var | Purpose currently unknown. |
| `0xC3` | 7 | Copy a string value into an unknown buffer. |
| `0xC4` | 11 | Like `0x73` (cast magic) with different args. |
| `0xC5`–`0xD2` | — | Variants of `0x45` / `0x52` / `0x55` with different args. |
| `0xD3` | 6 | Clear an entity's motion-queue lists. |
| `0xD4` | var | Multi-sub-opcode — open the map & query the user for input. |
| `0xD5`–`0xD7` | — | Variants of `0x45` / `0x52` / `0x55`. |
| `0xD8` | var | Set an unknown flag (sound-effect related) **and/or** set `ExtData[1]->EventDir` for an entity (two handlers share this byte). *(Dual-handler claim is unverified prose — no code corroboration here and no other source has it; flagged by the 2026-08 external crosscheck.)* |
| `0xD9` | 2 | (end of the known table) |

---

## Opcode families at a glance

- **Flow / math / logic** — `0x00`–`0x19`, `0x3C`–`0x3F`, `0x64`/`0x65` (distance).
- **Control flow** — `0x01` set-PC, `0x02` if, `0x1A`/`0x1B` jump, `0x3E` test-bit, `0x44` entity-valid, `0x21` end.
- **Dialogue / menus / input** — `0x1D`/`0x2B`/`0x48`/`0x49`/`0xB0` print, `0x23` wait, `0x24`/`0x25`/`0x7F` menus, `0x40`/`0x41` option flags, `0x71` text input. See [dialogue.md](dialogue.md).
- **Entities / scheduler tasks** — `0x2C`/`0x2D`/`0x45`/`0x62` create, `0x50`–`0x55` end/wait, `0x63` play-anim+wait, `0x6E` emote, `0x73` cast-magic, `0x5E`/`0x6B` reset-to-idle, `0xD3` clear motion.
- **Camera & presentation** — `0x20` lock player, `0x38` `CliEventModeLocal`, `0x46` camera control, `0x67`/`0x68` HUD hide/show, `0xAF` read camera pos. See [cutscenes.md](cutscenes.md#how-the-camera-works).
- **Position / facing** — `0x1F`/`0x31`/`0x36`/`0x37`/`0x5A`/`0xBA` event pos, `0x39`/`0x3A`/`0x3B` dir/yaw, `0x4A`/`0x4B`/`0x79` look-at, `0x47` push player pos.
- **World** — `0x4C`/`0x4D` doors, `0x34`/`0x35`/`0x75` load zone/room, `0x72`/`0x77`/`0x78`/`0xA9` weather/time, `0x97` wind.
- **Audio** — `0x5C`/`0x5D` music, `0x69`/`0x6A` SFX volume, `0x9A` music-server yield.
- **UI / map / services** — `0x85` mog house, `0x89`–`0x8D`/`0xB8`/`0xB9`/`0xC8`/`0xD4` map+markers, `0x8C` crafting, `0xB2` delivery, `0xB3` rankings, `0x87`/`0x88` world pass, `0x93`/`0xCC` info windows.
- **Server sync** — `0x27`–`0x2A` ReqSet/GetReqStatus, `0x43` event done, `0xA6` map-number request, `0xBE` server id.

---

## Related

- [format.md](format.md) — how these instructions are packed into the Event DAT.
- [cutscenes.md](cutscenes.md) — the opcodes in motion (camera, entities, doors, menus).
- [dialogue.md](dialogue.md) — the message tables the print opcodes resolve.
- Atom0s **XiEvents** — <https://github.com/atom0s/XiEvents> — authoritative opcode docs.

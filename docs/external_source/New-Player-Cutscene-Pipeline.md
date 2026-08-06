# The New-Player Cutscene Pipeline — Server → Client, End to End

**Worked example:** the opening cutscene a brand-new character sees on first
login in **Port Bastok (zone 236), event id 1**.

This document traces every step of what happens when a player enters a
cutscene, using the new-player CS as a concrete spine. It is assembled from
four local sources:

| Source | What it told us |
|---|---|
| `research/server/src` (+ `scripts/`) | LandSandBoat server: how the event is triggered and the packets sent/received |
| `research/XiPackets` | Exact byte layout of every event packet on the wire |
| `research/XiEvents` | The Event `.DAT` resource format and the 218-opcode VM |
| `research/XiClient` | How the client receives the packet, loads the DAT, and runs the event VM |

The actual bytecode for zone 236 / event 1 was decoded with
`server-tools/dump_event.py --zone 236 --event 1 --block 0`. The full
instruction-by-instruction disassembly is in the sibling file
[`research_zone236_event1_disasm.md`](research_zone236_event1_disasm.md).

---

## 0. The one-paragraph version

A new character logs in and the map server drops them at the Port Bastok
spawn. A first-login hook fires; the `newCharacterCS` hidden quest sees the
player has never watched the intro and, on zone-in, returns event **1** with
cutscene flags. The server packages that into a **`0x0032` GP_SERV_COMMAND_EVENT**
packet and locks the player. The client's `RecvEventCalc` handler resolves
zone 236 to event resource **`ROM/21/45.DAT`** (via the FTABLE/VTABLE file
tables), loads it, finds event id 1 inside **block 0** (the zone/player actor
block), and starts the **event virtual machine**. The VM interprets ~1,781
bytes of bytecode one opcode at a time across many frames: it hides the HUD,
kills the clock, takes camera control, loads animation "schedulers", moves and
poses NPCs, prints dialogue lines (`ShowMessage` + `MESWAIT`), and presents
menu choices (`QUERY`/`QUERYWAIT`). Each menu selection and each "I'm done
with this line" is reported back to the server as a **`0x005B`
GP_CLI_COMMAND_EVENTEND** packet (mode = *UpdatePending*), to which the server
replies with a **`0x0052` GP_SERV_COMMAND_EVENTUCOFF** acknowledgement so the
two stay in lockstep. When the bytecode hits its terminating `0x21`/`0x00`, the
client sends a final `0x005B` (mode = *End*); the server runs the
`onEventFinish[1]` Lua, hands over the Adventurer's Coupon, sets the home
point, repositions the player to the cutscene exit, marks the quest seen, and
releases the player.

---

## 1. Big-picture sequence

```
NEW CHARACTER LOGIN
        │
   (map server: first-login hook, char dropped at Port Bastok spawn)
        │
        ▼
┌──────────────────────── SERVER ────────────────────────┐
│ HiddenQuest 'newCharacterCS'                            │
│   check: notSeen==1 && NEW_CHARACTER_CUTSCENE==1        │
│   PORT_BASTOK.onZoneIn -> return { 1, -1, flags }       │
│        │                                                │
│   CLuaBaseEntity::startEvent(1, ...)                    │
│   -> StartEventHelper -> ParseEvent -> EventInfo        │
│   -> CCharEntity::queueEvent -> tryStartNextEvent       │
│        │  (player -> SUBSTATE_IN_CS, setLocked(true))   │
│        ▼                                                │
│   build & send  0x0032 GP_SERV_COMMAND_EVENT  ──────────┼──► wire
└─────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────── CLIENT ────────────────────────┐
│ RecvEventCalc (0x0032 handler)                          │
│   EventNum=236 -> resolve event DAT via FTABLE/VTABLE   │
│   load ROM/21/45.DAT  (+ event message DAT)             │
│   InitEvent2 -> find block for actor, find event id 1   │
│   XiEvent::XiEventInit -> ExecPointer = event offset    │
│        │                                                │
│   EventIdle (per frame) -> ExecProg (opcode switch)     │
│     hide HUD / lock clock / take camera                 │
│     LoadScheduler (animations) · move/pose NPCs         │
│     ShowMessage + MESWAIT (dialogue)                    │
│     QUERY + QUERYWAIT (menu choices) ───────────────────┼──► 0x005B (mode 1, UpdatePending)
│                                          ◄──────────────┼─── 0x0052 EVENTUCOFF (ack)
│     ... loops until 0x21 SetEventExecEnd / 0x00 End     │
│        │                                                │
│   send  0x005B GP_CLI_COMMAND_EVENTEND (mode 0, End) ───┼──► wire
└─────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────── SERVER ────────────────────────┐
│ GP_CLI_COMMAND_EVENTEND::process (mode End)             │
│   luautils::OnEventFinish(PChar, 1, result)             │
│     -> onEventFinish[1]:                                │
│         giveItem(ADVENTURER_COUPON)                     │
│         messageText(MAP_MARKER_TUTORIAL)                │
│         setPos(134, 8.5, -11, 96); setHomePoint()       │
│         quest:setVar('notSeen', 0)                      │
│   PChar->endCurrentEvent()  (unlock, clear currentEvent)│
│   send 0x0052 GP_SERV_COMMAND_EVENTUCOFF (release)      │
└─────────────────────────────────────────────────────────┘
        │
        ▼
   Player regains control at the CS-exit position.
```

---

## 2. Stage-by-stage timeline

### Stage 0 — New character exists and is flagged

A freshly created character is placed in their starting nation's first city.
On first entry into the map server, the first-login path runs character setup
(`xi.player.charCreate` in `scripts/globals/player.lua`) which stamps the
new-player state. The intro cutscene itself is **not** in the per-zone scripts;
it lives in a cross-zone hidden quest:
`research/server/scripts/quests/hiddenQuests/New_Character_Cutscenes.lua`.

The quest gate (the `check` function) is:

```lua
quest:getVar(player, 'notSeen') == 1 and xi.settings.main.NEW_CHARACTER_CUTSCENE == 1
```

So the CS only ever fires for a character who has never seen it, and only when
the server is configured to play it.

### Stage 1 — Zone-in triggers the event

The hidden-quest framework routes the zone-in through
`InteractionGlobal.onZoneIn`. For Port Bastok the handler is simply:

```lua
[xi.zone.PORT_BASTOK] = {
    onZoneIn = {
        function(player, prevZone)
            local cutsceneFlags = bit.bor(
                xi.cutsceneFlag.UNKNOWN_1,
                xi.cutsceneFlag.NO_PCS,
                xi.cutsceneFlag.UNKNOWN_2)
            return { 1, -1, cutsceneFlags }   -- event id 1, textTable -1, flags
        end
    },
    ...
}
```

The returned tuple is `{ eventId = 1, textId = -1, flags = … }`. The
`NO_PCS` flag hides other players for the duration (a clean stage for the
intro). This return value is what the framework feeds into `startEvent`.

> Each starting city returns a *different* event id from the same script —
> Bastok Mines = 1, Bastok Markets = 0 (then chains to 7), Northern San d'Oria
> = 535, Windurst Waters = 531, etc. Port Bastok = **1**.

### Stage 2 — Server builds and sends the event-start packet

The Lua `startEvent` lands in C++:

- `CLuaBaseEntity::startEvent(EventID, …)` → `StartEventHelper(…, EVENT_TYPE::NORMAL)`
  (`research/server/src/map/lua/lua_baseentity.cpp`)
- `StartEventHelper` parses the params/strings/flags into an **`EventInfo`**
  (`research/server/src/map/event_info.h`) and calls
  `CCharEntity::queueEvent(eventInfo)`
  (`research/server/src/map/entities/charentity.cpp`).
- `queueEvent` → `tryStartNextEvent()` pops the event, sets player substate
  `SUBSTATE_IN_CS`, calls `setLocked(true)` for a cutscene, and pushes the
  appropriate packet:
  - **no numeric params →** `GP_SERV_COMMAND_EVENT` (**0x0032**) ← *our case*
  - with numeric params → `GP_SERV_COMMAND_EVENTNUM` (0x0034)
  - with string params → `GP_SERV_COMMAND_EVENTSTR` (0x0033)

The packet is filled in
`research/server/src/map/packets/s2c/0x032_event.cpp`:

```cpp
packet.UniqueNo   = PChar->id;          // (no target NPC -> the player)
packet.ActIndex   = PChar->targid;
packet.EventNum   = PChar->getZone();   // 236  ← used to pick the event DAT
packet.EventPara  = eventInfo->eventId; // 1    ← which event inside the DAT
packet.Mode       = eventInfo->eventFlags & 0xFFFF;
packet.EventNum2  = PChar->getZone();   // 236  (sub-zone id, same here)
packet.EventPara2 = eventInfo->eventFlags >> 16;
```

**Key insight:** the server never sends cutscene *content*. It sends only
*which* event to play (zone + event id + flags). All choreography lives in the
client's local DAT files. The server's job is to start it, stay synchronized,
and apply the gameplay consequences when it ends.

#### `0x0032 GP_SERV_COMMAND_EVENT` on the wire (20 bytes)

| Off | Type | Field | Value here | Meaning |
|----|------|-------|-----------|---------|
| 0x00 | u16 | id/size | — | bit-packed header (9-bit id, 7-bit size) |
| 0x02 | u16 | sync | — | sync counter |
| 0x04 | u32 | UniqueNo | player id | event "owner" entity server id |
| 0x08 | u16 | ActIndex | player targid | event owner target index |
| 0x0A | u16 | EventNum | **236** | event number → selects the zone event DAT |
| 0x0C | u16 | EventPara | **1** | event id within the DAT |
| 0x0E | u16 | Mode | flags low | event mode/flags |
| 0x10 | u16 | EventNum2 | 236 | secondary/sub event number |
| 0x12 | u16 | EventPara2 | flags high | secondary parameter |

Client handler: **`RecvEventCalc`**.

### Stage 3 — Client receives the packet and loads the event resource

On `RecvEventCalc` the client:

1. Errors out if it is already in an event (one event at a time).
2. Uses `EventNum`/`EventNum2` to determine the **event data DAT** and the
   **event message DAT** for the zone.
3. Begins an async load (`EventStartWait`): load the event-data DAT, load the
   message DAT, then call `InitEvent2` once both are resident.

#### How zone 236 maps to a file: FTABLE / VTABLE

Event DATs are not addressed by name; the client converts a *file table index*
to a `ROM*/folder/file.DAT` path through two combined tables (`VTABLE.DAT` for
the ROM version, `FTABLE.DAT` for the packed folder/file). The mapping used by
both the retail client and our porter (`dump_event.py`, mirroring
`GameDataRepository`) is:

```
file_index = (zone < 0x100 ? 5820 : 84735) + zone
           = 5820 + 236 = 6056
6056 -> VTABLE/FTABLE -> ROM/21/45.DAT
```

Confirmed by the tool:

```
zone 236 (0xEC) -> file index 6056 -> ROM/21/45.DAT
file: .../game-data/ROM/21/45.DAT  (487300 bytes, 244 blocks)
```

### Stage 4 — Inside the event resource

The DAT is the flat per-zone event format (`eventheader_t` + `eventblock_t[]`),
documented in `research/XiEvents/Event DAT Structures.md`:

```
eventheader_t { u32 BlockCount; u32 BlockSizes[BlockCount]; }

eventblock_t {
    u32 Actornumber       // entity server id this block's events belong to,
                          //   or 0x7FFFFFFF / 0x7FFFFFF0 for the zone/player
    u32 TagCount          // number of events in this block
    u16 TagOffset[]       // byte offset of each event's bytecode
    u16 EvectExecNum[]    // the event id of each event (the "tag")
    u32 ImedCount
    u32 ImidData[]        // immediate/reference table (ids, coords, msg ids…)
    u32 EventDataSize
    u8  EventData[]       // 4-byte-aligned bytecode
}
```

For zone 236 the file holds **244 blocks**. The new-player CS is **event id 1
in block 0**, whose actor is `0x7FFFFFF0` — the *local-player / zone* actor.
That is why this cutscene is "owned" by the player rather than an NPC: it is a
zone-level scene that drives the player's camera directly. (Most NPC-anchored
events sit in later blocks keyed to that NPC's server id, e.g. block 3 =
`0x010EC006`, the gate guard "npc idx 6" this scene also animates.)

Locating the event inside the block:

1. Scan `EvectExecNum[]` for `1`; if not found, fall back to wildcard `0xFFFE`.
2. Take the matching `TagOffset[]` → start byte in `EventData`.
3. Set `ExecPointer` there and begin interpreting.

For zone 236/event 1: **offset = 1, length = 1781 bytes.**

### Stage 5 — The event virtual machine runs the bytecode

`research/XiEvents/Event VM Functions.md` and `Event VM Structures.md` describe
the interpreter; the client side is `XiEvent::EventIdle` → `XiEvent::ExecProg`:

- The event state object (`xievent_t`, at `entity->EventPointer`) holds the
  pointers into the block (offsets, ids, immediate table, bytecode), a 16-entry
  **`ReqStack`** for concurrently-running sub-routines (one per actor/priority),
  an 8-deep **JumpTable** (GOSUB/RETURN), the **`ExecPointer`**, and the
  **`RetFlag`** that yields back to the frame loop.
- Each frame `EventIdle` picks the highest-priority `ReqStack` entry, restores
  its saved `ExecPointer`, and calls `ExecProg` in a loop until `RetFlag` is
  set (a wait/yield), then saves the pointer back for next frame.
- `ExecProg` is one giant opcode switch. Opcode = 1 byte; many opcodes carry a
  1-byte *subcode* and 0–24 bytes of operands. Operand sizing is a fixed table
  with per-subcode overrides (faithfully ported in
  `dump_event.py:FIXED_SIZES` / `_SUB_TABLES`).
- Operands that reference data use the `getworkofs` model: a 2-byte selector
  with the high bit `0x8000` set indexes the block's `ImidData[]` table;
  other ranges hit zone work areas or live entity getters.

**Operand example:** at the very first message,
`0x48 ShowMessage sub=0x12  ⇒ refs +1:imid[18]=0x00001C45`. The instruction's
selector resolves to `ImidData[18] = 0x1C45 = 7237`, the **event message id**
the client loads from the zone message DAT and prints. Every dialogue line in
this CS is one of these `ShowMessage`→`imid[n]=0x1Cxx` pairs.

### Stage 6 — Mid-event client↔server handshake

Most of the runtime is the VM driving visuals locally, but several points
require a round-trip so the *server* knows where the player is in the scene
(important for branching and for not double-firing):

- **`0x23 MESWAIT`** / **`0x25 QUERYWAIT`** yield until the player advances the
  dialog or picks a menu option. When a choice is involved, the client sends
  **`0x005B GP_CLI_COMMAND_EVENTEND` with `Mode = UpdatePending (1)`**, carrying
  the selected option in `EndPara`.
- Server handles it in `0x05b_eventend.cpp` (mode `UpdatePending`): it may lock
  the player if the option starts an optional sub-cutscene, then calls
  `luautils::OnEventUpdate(PChar, eventId, result)` → the zone's
  `onEventUpdate[id]` Lua (Port Bastok has none; Windurst Waters' 531 does).
- Server replies with **`0x0052 GP_SERV_COMMAND_EVENTUCOFF`** to clear the
  client's "waiting for server" flag (`RecPendingFlag`), so the VM resumes.
- Other server→client packets that can arrive *during* an event:
  - **`0x003B EVENTMES`** — push a formatted message (entity-name aware).
  - **`0x005C PENDINGNUM`** — write 8 ints into the client's event work area
    (used to feed runtime values the bytecode reads).
  - **`0x005B WPOS`** (server→client; *different* packet from the client's
    0x005B) — reposition an entity/player mid-scene; modes also drive
    zone-in/zone-out fades.

In zone 236/event 1 the bytecode shows **three `QUERY`/`QUERYWAIT` menus**
(offsets +981, +1178, +1322) feeding chains of `0x02 IF` branches — these are
the intro's "choose your response" prompts that steer which dialogue lines play.

### Stage 7 — Event end and gameplay consequences

The bytecode terminates with:

```
+1779  0x21 SetEventExecEnd   // flag the event finished
+1780  0x00 EndReqStack       // pop/clear the running ReqStack entry
```

(Just before that it restores everything it took away: `0x46 CameraControl
enable`, `0x78 RestoreClock`, `0x68 SetHud` unhide, and `fdi2`/`fdo2` fade
schedulers.)

The client then sends **`0x005B GP_CLI_COMMAND_EVENTEND` with `Mode = End (0)`**.
Server handling (`0x05b_eventend.cpp`):

```cpp
case End:
    luautils::OnEventFinish(PChar, eventId, result);
    if (PChar->currentEvent->eventId == eventId)  // no follow-on event queued
        PChar->endCurrentEvent();                 // unlock + clear currentEvent
...
PChar->pushPacket<GP_SERV_COMMAND_EVENTUCOFF>(PChar, EventRecvPending); // release
```

`OnEventFinish` runs the Port Bastok `onEventFinish[1]` Lua, which is where all
the *gameplay* finally happens (none of it was in the cutscene data):

```lua
[1] = function(player, csid, option, npc)
    local ID = zones[player:getZoneID()]
    player:messageText(player, ID.text.MAP_MARKER_TUTORIAL)
    npcUtil.giveItem(player, xi.item.ADVENTURER_COUPON)
    player:setPos(134, 8.5, -11, 96)   -- CS-exit position
    player:setHomePoint()
    quest:setVar(player, 'notSeen', 0) -- never play again
end
```

`endCurrentEvent` clears `currentEvent`, unlocks the player, resets animation,
and runs `tryStartNextEvent()` (a no-op here since nothing else is queued). The
final `0x0052 EVENTUCOFF` returns control. The player is now standing at the
cutscene-exit coordinates, coupon in inventory, home point set, with the intro
permanently marked seen.

---

## 3. The decoded event, narrated

Annotated highlights from
[`research_zone236_event1_disasm.md`](research_zone236_event1_disasm.md) (block
0, actor `0x7FFFFFF0`, event `0x0001`, offsets shown as `+n`):

**Setup / take over the screen**

| Off | Op | Name | What it does |
|----|----|------|--------------|
| +0 | 0x22 | SetEntityVisible | hide the player's normal render flag for the scene |
| +2 | 0x77 | LockClock | freeze the Vana'diel game clock |
| +7 | 0x46/01 | CameraControl disable | take the camera away from the player |
| +9 | 0x42 | (cancel-flag clear) | reset event-cancel state |
| +10 | 0x69/01 | PlaySound | set SFX volume |
| +14,+18 | 0x5C | PlayMusic | start/track the cutscene music |
| +22 | 0x67/04 | SetHud | hide the whole HUD |
| +27 | 0x45/06 | LoadScheduler | load animation scheduler `s00s` for the player actor (res 30840) |

**Stage the actors** — `0x4E SetEntityVisible`, `0x36 SetEventPos`
(`imid[8..10]` = a fixed world position), `0x39 SetEventDir`, `0x32 MainSpeed`,
`0x1F MOVE` walk the player and the gate guard (`0x010EC006`, "npc idx 6") into
place, waiting on schedulers `s001`…`s005` with `0x55 WAITLOADSCHEDULER_Main`.

**Dialogue** — repeated `0x48/0x2B ShowMessage` (each resolving to an
`imid[n] = 0x1Cxx` message id) followed by `0x23 MESWAIT` (block until the
player presses confirm). The first few message ids: `0x1C45, 0x1C46, 0x1C47,
0x1C48 …` — the guard's opening lines.

**Player choices** — three menus:

| Off | Op | Name |
|----|----|------|
| +981 | 0x24/3A | QUERY (open a choice menu) |
| +988 | 0x25 | QUERYWAIT (block for selection) |
| +989… | 0x02 | IF branches on the result |
| +1178 | 0x24/41 | QUERY |
| +1322 | 0x24/48 | QUERY |

Each `QUERY` result is the value reported to the server in the `0x005B`
(UpdatePending) `EndPara`, and locally steers the `0x02 IF` jumps that select
which follow-up lines play. Extended animation/look behaviors appear here:
`0x66/0x5B LoadExtScheduler` actions `tlk0`,`tlk1`,`itl0`,`itl1`,`dis0`,`pas0`,
`ten0`,`kud0` — talk/idle/dismiss/turn gestures keyed to the chosen branch.

**Map tutorial** — near the end the scene actually opens the world map and
drops a marker, matching the server's `MAP_MARKER_TUTORIAL` follow-up:

| Off | Op | Name | Operands |
|----|----|------|----------|
| +1567 | 0xC8/55 | OpenMap | `imid[85]=0xEC` (=236, the zone) |
| +1574 | 0x8B/55 | SetEventMark | marker at packed coords; embedded text `"Dulsie"` |
| +1617 | 0x8A | CloseMap | |

**Teardown / release**

| Off | Op | Name |
|----|----|------|
| +1736 | 0x45/11 | LoadScheduler `fdo2` (fade out) |
| +1756 | 0x46/00 | CameraControl enable (give camera back) |
| +1758 | 0x78 | RestoreClock |
| +1762 | 0x45/11 | LoadScheduler `fdi2` (fade in) |
| +1779 | 0x21 | SetEventExecEnd |
| +1780 | 0x00 | EndReqStack → triggers the client's final `0x005B` (End) |

---

## 4. Packet reference (event lifecycle)

### Server → Client

| ID | Name | Size | Client handler | Role |
|----|------|------|----------------|------|
| 0x0032 | GP_SERV_COMMAND_EVENT | 20 | `RecvEventCalc` | **start event** (zone + event id + flags) — our case |
| 0x0033 | GP_SERV_COMMAND_EVENTSTR | 112 | `RecvEventCalcStr` | start event with 4×16 string params |
| 0x0034 | GP_SERV_COMMAND_EVENTNUM | 52 | `RecvEventCalcNum` | start event with 8 int params |
| 0x003B | GP_SERV_COMMAND_EVENTMES | 12 | `RecvEventMes` | push a message during the event |
| 0x005C | GP_SERV_COMMAND_PENDINGNUM | 36 | `RecvPendingNum` | write 8 ints into event work area |
| 0x005B | GP_SERV_COMMAND_WPOS | 28 | `RecvWpos` | reposition entity / zone-fade during event |
| 0x0052 | GP_SERV_COMMAND_EVENTUCOFF | 8 | `RecvUcOff` | **ack / release** event user-control state |

### Client → Server

| ID | Name | Size | Server handler | Role |
|----|------|------|----------------|------|
| 0x005B | GP_CLI_COMMAND_EVENTEND | 20 | `GP_CLI_COMMAND_EVENTEND::process` | **report progress / finish** — `Mode 1 = UpdatePending`, `Mode 0 = End`; `EndPara` = chosen option |
| 0x005C | GP_CLI_COMMAND_EVENTENDXZY | 32 | `…eventendxzy` | same, but carries the player's new x/y/z/dir (warp-style events) |

> Note the `0x005B` collision: client→server it is **EVENTEND**, server→client
> it is **WPOS**. Direction disambiguates them.

`0x0052 EVENTUCOFF` `Mode` values: `0` adjust standard control, `1` clear
receive-pending, `2` cancel current event, `3` cancel text/number input, `4`
release fishing lock.

---

## 5. Source index (where to look)

**Server (LandSandBoat)**

| Concern | File · symbol |
|---|---|
| New-player CS trigger + finish | `scripts/quests/hiddenQuests/New_Character_Cutscenes.lua` (`PORT_BASTOK`) |
| New-character setup | `scripts/globals/player.lua` · `xi.player.charCreate` |
| Start event (Lua API) | `src/map/lua/lua_baseentity.cpp` · `startEvent` / `StartEventHelper` |
| Event data struct | `src/map/event_info.h` · `EventInfo` |
| Queue / dispatch | `src/map/entities/charentity.cpp` · `queueEvent` / `tryStartNextEvent` / `endCurrentEvent` |
| Build 0x0032 | `src/map/packets/s2c/0x032_event.cpp` · `GP_SERV_COMMAND_EVENT` |
| Handle 0x005B | `src/map/packets/c2s/0x05b_eventend.cpp` · `GP_CLI_COMMAND_EVENTEND::process` |
| Lua callbacks | `src/map/lua/luautils.cpp` · `OnEventUpdate` / `OnEventFinish` |

**Packets:** `research/XiPackets/world/{server,client}/0x00XX/README.md`

**Event format & VM:** `research/XiEvents/Event DAT Structures.md`,
`Event VM Structures.md`, `Event VM Functions.md`, `OpCodes/0x00XX.md` (218 files)

**Client:** `research/XiClient/src/...` — `RecvEventCalc`, `EventStartWait`,
`InitEvent2`, `XiEvent::XiEventInit`, `XiEvent::EventIdle`, `XiEvent::ExecProg`

---

## 6. Reproduce / extend

```bash
# Index every block + event in the Port Bastok event DAT:
python3 server-tools/dump_event.py --zone 236

# Full disassembly of the new-player CS (event 1, block 0):
python3 server-tools/dump_event.py --zone 236 --event 1 --block 0

# Same, as a markdown table with raw bytes:
python3 server-tools/dump_event.py --zone 236 --event 1 --block 0 --bytes --md out.md
```

The resolver `(5820 + zone) → FTABLE/VTABLE → ROM*/folder/file.DAT` works for
any zone, so swap `--zone` to trace San d'Oria (535), Windurst (531), etc. The
sibling file [`research_zone236_event1_disasm.md`](research_zone236_event1_disasm.md)
is the complete instruction table for this cutscene.

---

## 7. Open threads (not yet resolved here)

- **Message text.** We resolved dialogue to message *ids* (`0x1C45…`). Turning
  those into the actual English/Japanese strings means parsing the zone's event
  **message DAT** (the second file `RecvEventCalc` loads). `dump_event.py` does
  not yet decode it.
- **Scheduler resources.** `LoadScheduler` actions (`s00s`, `ovl1`, `fdi2`, …)
  reference animation/camera scheduler resources (res ids like 30840/30904).
  Mapping those to their own DATs is the next layer down (our UE port already
  resolves scheduler resources → FTABLE → camera keyframes; see the engine's
  event camera work).
- **`PENDINGNUM`/`WPOS` in this specific CS.** The bytecode reads work-area
  slots in places; confirming exactly which runtime values the server feeds for
  the Port Bastok intro would need a live capture.
</content>
</invoke>

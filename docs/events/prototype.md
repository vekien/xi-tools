# Prototype — authoring a custom cutscene

A design sketch for building a **custom event/cutscene** from scratch: author it as
**JSON**, **compile** it to the binary the game understands, splice it into a zone's
DATs, and **trigger** it in-game. This is forward-looking — none of it is built yet —
but it's grounded in the [format](format.md), the [opcode set](opcodes.md), the
[dialogue tables](dialogue.md), and the [camera system](cutscenes.md#how-the-camera-works)
documented in this folder.

> **Status: mostly shipped (2026-09-03: menus, branches, goto, server round-trip added — see [authoring.md](authoring.md#menus-branches-and-the-server-round-trip-xi-event-cutscene-compile-2026-09-03)).** The **dialogue** slice of this design is built —
> [`xi event dialogue new`](authoring.md) appends lines and synthesizes a plain
> multi-line dialogue event (byte-exact), returning an event id + a server Lua stub. The
> rest below (camera, menus, branching, the full step compiler) is still a proposal: it
> defines the data model, compile pipeline, and trigger path so we can build it. Treat the
> unbuilt byte-level details as "confirm against XiEvents / real DATs first".

---

## What a cutscene actually is (recap)

A cutscene is an **event**: a per-actor entry in a zone's [Event DAT](format.md) whose
**bytecode** sequences the scene. The bytecode doesn't inline anything — it **references**
dialogue strings, a camera spline, skeleton animations, effects, and sounds by id/FourCC
(see [the resource graph](cutscenes.md#the-resource-graph--what-a-cutscene-references)).
So "make a cutscene" = produce four things and wire them together:

1. **Event bytecode** — the action sequence (→ Event DAT).
2. **Dialogue strings** — the lines (→ the JP/NA EventMessage tables, see [dialogue.md](dialogue.md)).
3. *(optional)* **A camera resource** — the spline move(s).
4. **A server trigger** — events are **server-driven**; something must tell the client
   "run event N on actor A".

---

## The authoring model (JSON)

Author at a **high level** — named steps, not raw opcodes — and let the compiler lower
each step to the right [opcode](opcodes.md) sequence. A strawman schema:

```jsonc
{
  "zone": 241,                      // Southern San d'Oria (target zone)
  "actor": 17780737,               // the NPC that owns this event (server entity id)
  "eventId": 200,                  // a free event id on that actor
  "camera": {                      // optional camera move(s) -> a camera resource
    "mode": "spline",              // locked | straight | spline
    "smoothing": "AccelerateAndDecelerate",
    "points": [
      { "eye": [12.0, -2.0, 5.0],  "look": [10.0, -1.0, 4.0], "fov": 60 },
      { "eye": [8.0,  -3.0, 6.0],  "look": [10.0, -1.0, 4.0], "fov": 45 }
    ]
  },
  "steps": [
    { "op": "enter_cutscene",      "lockPlayer": true, "hideHud": true },   // 0x20/0x46/0x67
    { "op": "camera",              "duration": 90, "attach": "actor" },     // start CameraTask
    { "op": "face",                "actor": "self", "target": "player" },   // 0x1E (+talk)
    { "op": "say",                 "speaker": "self", "text": 0 },          // 0x1D -> string[0]
    { "op": "wait_dismiss" },                                              // 0x23
    { "op": "menu",                "options": ["Yes", "No"], "text": 1 },   // 0x40/0x24/0x25
    { "op": "branch", "on": "select", "cases": { "0": "yes", "1": "no" } },// 0x02/0x3E
    { "label": "yes", "op": "say", "speaker": "self", "text": 2 },
    { "op": "goto", "to": "end" },
    { "label": "no",  "op": "say", "speaker": "self", "text": 3 },
    { "label": "end", "op": "exit_cutscene" }                              // 0x68/0x46/0x21
  ],
  "dialogue": [                     // -> appended to the zone's NA/JP message tables
    "Welcome, traveler.",
    "Shall I tell you of San d'Oria?",
    "Long ago, the Kingdom...",
    "Another time, then."
  ]
}
```

Design notes:

- **High-level steps → opcodes.** `say` → `0x1D`/`0x2B`; `say` with no speaker → `0x48`;
  `wait_dismiss` → `0x23`; `menu` → `0x40` (enable options) + `0x24` + `0x25`; `branch`
  → `0x02`/`0x3E`; `goto`/`label` → `0x1A` jumps; `enter_cutscene`/`exit_cutscene` expand
  to the camera/HUD/lock mode opcodes (`0x20`/`0x38`/`0x46`/`0x67`/`0x68`) + `0x21`.
- **`text` is an index** into this event's `dialogue[]`; the compiler assigns real
  message ids when it appends them to the table.
- **Actors** are referenced symbolically (`"self"`, `"player"`, or another entity id);
  the compiler resolves them to the entity-target slots the opcodes use.
- **Camera** points are eye/look/FOV control points → a [camera resource](cutscenes.md#how-the-camera-works);
  `camera` steps start a `CameraTask` for `duration` frames, optionally attached to an actor.

---

## The compile pipeline (JSON → DAT)

```
cutscene.json
   │  1. validate + resolve symbols (actors, labels, text indices)
   ▼
lower steps → opcode stream  (assign jump targets after a first sizing pass)
   │  2. emit bytecode for each event id; build the actor block:
   │     actorId, eventCount, eventOffsets[], eventIds[], refCount, references[],
   │     sceneSize, sceneData   (see format.md#actor-block)
   ▼
build/extend resources
   ├─ dialogue[]  → encode with the event-string codec → append to the zone JP & NA
   │               EventMessage tables (new indices)            (dialogue.md)
   └─ camera{}    → encode CameraHeader + SplineControlPoint[]  → a camera resource
   ▼
   3. splice into the zone DATs:
   ├─ Event DAT  (file_id 5820+zone, or model+1100):  append/replace the actor block,
   │               fix blockCount + blockSizes table   (format.md#file-layout)
   ├─ Dialog DATs (JP/NA):  rewrite the offset table + string blob
   └─ keep a <dat>.base backup (same convention as xi zone / fx)
   ▼
   4. (custom content) register any NEW DAT in the FileTable so the client can load it
                       (see ftable/ docs); editing an existing zone's DATs in place
                       needs no new registration.
```

Two integration modes, mirroring the rest of xi:

- **In-place edit** — add the event + strings to the **existing** zone DATs (no FileTable
  change). Simplest; good for adding an event to a retail zone.
- **Custom zone / DAT** — for a fresh zone, package the event set as that zone's DATs and
  register their file_ids (`xi dats`, `xi ftable`, `xi zone new`).

---

## Triggering the event in-game

The client never self-starts a story event — the **server** drives it (see
[cutscenes.md](cutscenes.md#trigger--run--release)). On a LandSandBoat-style server
(CatsEyeXI), an NPC's script starts the event and the client runs the matching bytecode:

```lua
-- server: zones/Southern_San_dOria/npcs/<NPC>.lua
function onTrigger(player, npc)
    player:startCutscene(200)     -- the eventId we compiled into the Event DAT
end

function onEventFinish(player, csid, option)
    -- react to the player's menu choice (the `option` value), give rewards, etc.
end
```

So the **end-to-end** for a custom cutscene is:

1. **Client side** (this pipeline): compile the event bytecode + dialogue (+ camera) into
   the zone's Event/Dialog DATs at `eventId = N`.
2. **Server side**: add/extend the NPC's Lua so something (`onTrigger`, a quest step, a
   zone-in) calls `player:startCutscene(N)`, and handle `onEventFinish` for branches.
   (Use `startCutscene`, not `startEvent` — cutscenes need CUTSCENE mode / movement lock.
   Plain dialogue can still use `startEvent` when appropriate.)
3. Both must agree on **`N`** and the **actor** — that's the contract between client DAT
   and server script (the same split as spell visuals in
   [../fx/effect_system.md](../fx/effect_system.md#2-how-spells-are-named--resolved)).

---

## A minimal worked example

"NPC turns to you, says one line, camera pushes in, ends":

```jsonc
{
  "zone": 241, "actor": 17780737, "eventId": 200,
  "camera": { "mode": "straight", "smoothing": "Decelerate",
              "points": [ { "eye": [12,-2,5], "look": [10,-1,4], "fov": 60 },
                          { "eye": [9,-2.5,4.5], "look": [10,-1,4], "fov": 45 } ] },
  "steps": [
    { "op": "enter_cutscene", "lockPlayer": true, "hideHud": true },
    { "op": "camera", "duration": 120, "attach": "actor" },
    { "op": "face",   "actor": "self", "target": "player" },
    { "op": "say",    "speaker": "self", "text": 0 },
    { "op": "wait_dismiss" },
    { "op": "exit_cutscene" }
  ],
  "dialogue": [ "May Altana watch over you." ]
}
```

…lowers to roughly:

```
0x20 (lock player) · 0x46 (camera control) · 0x67 (hide HUD)
<start CameraTask: camera resource, duration=120, attach=self>
0x1E (self look-at player + talk)
0x1D (print string[0], speaker=self)
0x23 (wait for dismiss)
0x68 (show HUD) · 0x46 (restore) · 0x21 (end event)
```

…and the server NPC does `player:startCutscene(200)`.

---

## Tooling (`xi event`)

| Command | Status | Does |
|---|---|---|
| `xi event cutscene export <zone>` | **shipped** | parse a zone's Event DAT → `.txt` disasm / `--json` (actors, eventIds, decoded bytecode, resolved `→ msg`/`→ zone`) — the inverse of compile, for learning from retail events |
| `xi event dialogue actors <zone>` | **shipped** | list a zone's actor ids + names → pick the NPC for `dialogue new` |
| `xi event dialogue new <zone> --json … --actor …` | **shipped** | append lines + synthesize a multi-line dialogue event → event id + Lua stub ([authoring.md](authoring.md)) |
| `xi event cutscene compile <cutscene.json> --event-dat <path> [--dialog-dat <path>] [--dry-run]` | **shipped** | the full step compiler — splice a new/edited event + strings (+ camera/menus/branches) into the zone DATs, keep `<dat>.base`. `--event-dat` required; `--dialog-dat` auto-derived from `/21/`→`/25/` when possible |

**Build order that de-risked it (followed):** `export` **first** (read-only; validated the
format against retail), then a *byte-exact event-DAT round-trip*
([authoring.md](authoring.md#the-byte-exact-event-dat-writer-foundation)), then authoring the
simplest new event (dialogue). The remaining cutscene compiler extends the same foundation —
same crawl-walk-run the `fx` tooling followed (list → dump → edit → copy).

---

## Open questions / risks

- **Exact opcode argument layouts** — [opcodes.md](opcodes.md) has lengths from the xeno
  parser, but multi-case opcodes need XiEvents-level detail before we emit them.
- **Camera model — container now confirmed (2026-06-18), maths still open.** xi has
  byte-decoded *where* camera data lives and its container: a referenced **scene resource**
  holding `0x07` **EffectRoutine** shots (`sNNN`), each paired with a `0x06` **Route**
  (`cNNN`) = eye/look-at/FOV keyframe spline (`parse_camera_routes`; see
  [cutscenes.md](cutscenes.md#how-the-camera-works)). The **full keyframe layout is now
  decoded** (eye, **focal length** — `FOV = 2·atan2(192, focal)`, not decidegrees as older
  notes said — look-at, **roll** in radians, time + pad; a
  per-Route smoothing **mode** enum `0..4` at header `+0x14`), so the compiler can emit
  real camera Routes. The **only** piece still open is which easing curve each `mode` value
  applies (xiclient's 5 `CameraSmoothType` names are the candidates), so keep the
  `camera{}` **smoothing** field experimental until that's pinned; a cutscene can still
  ship without a camera block.
- **String-table growth** — appending dialogue means rewriting the EventMessage offset
  table; verify the engine tolerates a resized Dialog DAT (zone/fx resizes do load, so
  likely yes).
- **Event-id allocation** — find a free `eventId` per actor without colliding with
  retail events; `event dump` makes this auditable.
- **Server coupling** — a client-only event does nothing without a server `startCutscene`
  (or `startEvent` for non-cutscene dialogue); the doc/tooling should emit the matching Lua
  stub through the `xi dats` package resources.

---

## Related

- [format.md](format.md) — the Event DAT block layout we write into.
- [opcodes.md](opcodes.md) — the instruction set the compiler targets.
- [dialogue.md](dialogue.md) — the string tables to encode lines into.
- [cutscenes.md](cutscenes.md) — the camera spline system + the resource graph.
- [../fx/effect_system.md](../fx/effect_system.md) — the sibling client-DAT-+-server
  authoring split (spell visuals) this mirrors.

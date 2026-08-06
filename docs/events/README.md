# events

How FFXI **events** work — the system behind **cutscenes**, **NPC dialogue**, menu
prompts, and scripted scene action (camera moves, doors, NPC animation). This folder
collects what we currently know about the event data format, the dialogue/message
formats, and the cutscene bytecode VM, drawn from the existing DAT research, our zone
code, and the reference implementations in `thirdparty/`.

> **Status.** `xi event cutscene export`, `xi event dialogue new`, and `xi event dialogue`
> ship today. The event export disassembles a zone's events and **resolves operand
> references**: dialogue ids show as `→ msg N`, and `load_zone` (`0x34`/`0x35`) targets as
> `→ zone N Name` (see [the work-selector model](format.md#operand-references--the-references--work-selector-model)).
> `xi event dialogue new` goes the other way — it **authors** a new NPC dialogue event
> from a JSON list of lines (byte-exact); see [authoring.md](authoring.md). The web level
> editor's Events panel shows the same decode, with clickable zone loads. These docs
> describe the underlying formats — a foundation for that tooling and for hand-editing.
> Where a detail is reverse-engineered but not yet verified against bytes by us, it's marked.

---

## The big picture

Every zone ships a small set of **per-zone DATs** that, together, drive everything an
NPC says or does:

```
  player talks to / triggers an NPC  (server sends "run event N on actor A")
        │
        ▼
  Event DAT      ── per-actor blocks of event BYTECODE (the "scene" VM) ──
        │   each actor has a list of (eventId → scene entry-point); the VM runs
        │   opcodes that move the camera, animate entities, open doors, and…
        ├─ print dialogue ──► Dialogue / message tables  (the strings)
        ├─ pop a menu     ──► dialogue + menu-option flags
        └─ end / hand back control to the player
        ▼
  Entity (NPC) DAT  — the actors the bytecode references (id → name / model / spawn)
```

The data splits across **four per-zone files** (Atom0s / XiEvents convention), spread
across ROM3–ROM9 for the expansion zones:

| Slot | Contents | Doc |
|---|---|---|
| Entities | NPC / actor definitions the events reference | [format.md](format.md#entity-npc-definitions) |
| Event bytecode | the per-actor event scripts (the scene VM) | [format.md](format.md) · [cutscenes.md](cutscenes.md) |
| JP strings | Japanese dialogue/message table | [dialogue.md](dialogue.md) |
| NA strings | English dialogue/message table | [dialogue.md](dialogue.md) |

---

## Per-zone file IDs

Each zone's four DAT types resolve from the zone ID by fixed formulas (implemented in
`src/xi/zone/xi_inject.py`). For the base ROM
(`zone_id < 0x100`):

| DAT type | file_id formula (base ROM) | expansion ROM (`zone_id ≥ 0x100`) |
|---|---|---|
| Model (geometry) | `0x64 + zone_id` | `0x147B3 + (zone_id − 0x100)` |
| **Event** (bytecode) | `5820 + zone_id` | `model_file_id + 1100` |
| **Dialog** (strings) | `6420 + zone_id` | `model_file_id + 1700` |
| **NPC** (entities) | `6720 + zone_id` | `model_file_id + 2600` |

Resolve a file_id → DAT path with [`xi ftable lookup`](../ftable/lookup.md). The
full per-zone Model/Dialog/NPC/Event table (294 zones) is in
[../zone/zones.md](../zone/zones.md).

> The Model/Dialog/NPC/Event file-id mapping is **verified** against the live client
> file tables (it's how `xi zone` resolves zones). The internal *byte* layout of the
> Event and Dialog DATs below is from third-party parsers — trustworthy, but confirm
> against bytes before relying on a fine detail.

---

## Docs in this folder

- **[format.md](format.md)** — the Event DAT binary format (per-actor blocks, the
  event-id table, the scene bytecode).
- **[authoring.md](authoring.md)** — **`xi event dialogue new`**: inject dialogue lines +
  synthesize a new event that prints them (the byte-exact writer + verified `print_msg`
  encoding). The shipped slice of [prototype.md](prototype.md).
- **[opcodes.md](opcodes.md)** — the **complete event-VM opcode reference** (0x00–0xD9).
- **[dialogue.md](dialogue.md)** — how NPC dialogue is stored and shown: the
  `EventMessage` and `d_msg` string formats, the (Shift-JIS-ish) event-string codec,
  control codes, and how the bytecode prints/branches text.
- **[cutscenes.md](cutscenes.md)** — how a cutscene actually plays end-to-end:
  triggering, the scene VM loop, camera/player/entity control, doors, menus.
- **[camera_scene_ids.md](camera_scene_ids.md)** — ★ **`p` → file id → DAT** for custom
  cameras (safe mid band only; high-tier 71k ids crash). Read before changing publish.
- **[scene_dat_writer.md](scene_dat_writer.md)** — Route / EffectRoutine scene DAT layout.
- **[cutscene-dev-guide.md](cutscene-dev-guide.md)** — browser editor + compile pipeline.
- **[../common_crashes.md](../common_crashes.md)** — client crash field guide (incl. camera).
- **[weather.md](weather.md)** — the weather **id → in-game name** table (+ element),
  for the weather-setting opcodes.
- **[event-data.md](event-data.md)** — the **extracted dataset** we already have
  (`thirdparty/knowone134 event data/`, 277 zones of actor → event → dialogue JSON)
  and how to look things up in it.

---

## What we have to work with (sources)

| Source | What it gives us |
|---|---|
| `thirdparty/knowone134 event data/` | 277 zones × actor → event → dialogue, as JSON (already extracted) |
| `thirdparty/xeno projects/FFXI Dat Parser/.../EventDat.cs` | a full Event DAT parser + the scene-bytecode opcode handlers |
| `thirdparty/shining fantasia/src/common/resources/event-message.ts`, `dmsg.ts` | message/string table parsers (+ `dmsg2json` / `json2dmsg`) |
| `thirdparty/xiclient/.../UI/Windows/InGame/CTkEventMsg*` | the in-game event-message window (how dialogue is displayed) |
| `src/xi/zone/xi_inject.py`, `xi_list.py` (`parse_dmsg`) | the per-zone file-id formulas + a working `d_msg` string parser |
| [../dats.md](../dats.md), [../dat_index.md](../dat_index.md) | the per-zone 4-file model + DAT magics (`evte`, `d_ms`, `XISTRING`) |
| Atom0s **XiEvents** — <https://github.com/atom0s/XiEvents> | the authoritative event-bytecode opcode reference |
| `thirdparty/xim/.../resource/StringTableParser.kt`, `DatParser.kt` | the `d_msg` string-table parser + the section/DatId resource model |
| `thirdparty/xiclient/.../World/Camera/` | a *fan client's* camera implementation — a useful model for cutscene cameras, but its design (not just structs) may be bespoke (see trust note) |

> **What xim knows about events.** xim (the renderer) parses the **`d_msg` string
> tables** (`StringTableParser`, matching our `parse_dmsg`) and the section/DatId
> resource tree (`DatParser`), and it renders the **effect** side (`0x05`/`0x07`, see
> [../fx/effect_system.md](../fx/effect_system.md)). It does **not** implement the event
> **bytecode VM** — xim's `poc/game/event/` folder is gameplay events (combat, zoning),
> a different thing. For the scene VM, the **XiEvents** opcode docs + the **xiclient**
> reimplementation are the sources.

### Source trust — three tiers

These sources are **not** equally authoritative; weight them accordingly:

1. **Atom0s / XiEvents** — reverse-engineered from the **real** `FFXiMain.dll`. The
   **most trustworthy** for event behaviour: the opcode set, what each does, and the
   engine symbol names it cites (`CliEventModeLocal`, `XiEvent::ReqSet`, `CMoSchedularTask`)
   are real-client internals.
2. **On-disk DAT structs** (from any parser — xeno `EventDat.cs`, shining fantasia, xim,
   xiclient) — trustworthy *to the extent they round-trip real bytes*. A parser has to
   match the file format to work, so the **layouts** are reliable; confirm against bytes
   for fine detail.
3. **xiclient runtime behaviour** — ⚠️ **xiclient is a fully fan-made reimplementation,
   not the official client.** Its **class names are invented** (e.g. `CameraResource`,
   `SplinePath`, `CameraTask`, `CMoSchedulerTask` — note the spelling differs from
   Atom0s's `CMoSchedularTask`), and anything it does *after parsing a DAT* — camera
   spline maths, smoothing curves, the task framework, the "resource graph" wiring — may
   be **bespoke to xiclient's own engine**, not how retail FFXI works. Treat xiclient as
   a **plausible model / hypothesis**, not ground truth, and verify against the real
   client (Atom0s) or in-game behaviour before relying on it.

---

## Related

- [../zone/zones.md](../zone/zones.md) — the zone → Model/Dialog/NPC/Event DAT table.
- [../dat_ror1.md](../dat_ror1.md) — ROR-1 text encoding used by some `menu` string DATs.
- [../fx/effect_system.md](../fx/effect_system.md) — the *visual-effect* sequencer
  (`0x07` EffectRoutine). Different system from events, but the sibling for spell/ability
  visuals; cutscenes can trigger effects.

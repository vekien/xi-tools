# FFXI Format Documentation

Community-researched documentation of Final Fantasy XI's internal data formats —
DAT files, animation, zone mesh, event bytecode, audio, visual effects, and more.

Produced alongside reverse-engineering work on private server modding. Covers the
**retail FFXI client** (PC, PlayOnline era). Format notes are cross-referenced
against the [xim](https://www.ffxiah.com/forum/topic/58758/xim-browser-based-client-simulator) Kotlin client reimplementation
and a UE5 engine port where they confirm or clarify format details.

> **Note:** AI was used extensively in the research and generation of these docs.

> **Note:** Includes documentation on `xi-tools`, which is not yet publicly released — but the format information is accurate and useful for research.

---

## Table of Contents

- [DAT File System](#dat-file-system)
- [Animation](#animation)
- [Zone & Mesh](#zone--mesh)
- [Events & Cutscenes](#events--cutscenes)
- [Dialog](#dialog)
- [Audio](#audio)
- [Visual Effects](#visual-effects)
- [Gear & Equipment](#gear--equipment)
- [Mounts](#mounts)
- [UI Textures](#ui-textures)
- [Entity & NPC](#entity--npc)
- [Object Placement](#object-placement)
- [Key Items](#key-items)
- [Reference](#reference)
- [Research](#research)
- [External Source](#external-source)

---

## DAT File System

How the FFXI ROM file system is organised — FTABLE/VTABLE lookup tables, what
loads at boot, and deep dives into specific well-known DATs.

| Doc | Summary |
|-----|---------|
| [dat_index.md](dat_index.md) | Grouped index: what each DAT range does |
| [dats.md](dats.md) | Known DAT locations by category (living document) |
| [dats_boot.md](dats_boot.md) | Which DATs load at game boot |
| [dat_ror1.md](dat_ror1.md) | ROR-1 text encoding used by `menu` DATs (mission/quest text) |

### Per-file deep dives

| DAT | Doc | Summary |
|-----|-----|---------|
| `ROM/0/4.DAT` | [dats/ROM_0_4.md](dats/ROM_0_4.md) | Fixed-page graphics/system data |
| `ROM/0/14–21.DAT` | [dats/ROM_0_14-21.md](dats/ROM_0_14-21.md) | 8 window background skins (`win0`, DXT tiles) |
| `ROM/0/22.DAT` | [dats/ROM_0_22.md](dats/ROM_0_22.md) | |
| `ROM/0/23.DAT` | [dats/ROM_0_23.md](dats/ROM_0_23.md) | Title screen scene — camera splines, weather, zone refs |
| `ROM/0/24–26.DAT` | [dats/ROM_0_24.md](dats/ROM_0_24.md) | Character-select scene controllers (`sel_`) |
| `ROM/0/27.DAT` | [dats/ROM_0_27.md](dats/ROM_0_27.md) | UI animation / value curves (`damv`) |
| `ROM/1/41.DAT` | [dats/ROM_1_41.md](dats/ROM_1_41.md) | |
| `ROM/97/*.DAT` | [dats/ROM_97_menu_strings.md](dats/ROM_97_menu_strings.md) | Menu label string tables (`XISTRING`, plain ASCII) |
| `ROM/118/114.DAT` | [dats/ROM_118_114.md](dats/ROM_118_114.md) | |
| `ROM/118/115.DAT` | [dats/ROM_118_115.md](dats/ROM_118_115.md) | Live mission/quest text DB (`menu`, ROR-1 encoded) |
| `ROM/165/84.DAT` | [dats/ROM_165_84.md](dats/ROM_165_84.md) | |
| `ROM/168/25.DAT` | [dats/ROM_168_25.md](dats/ROM_168_25.md) | Auto-translate phrase dictionary |
| FX DATs | [dats/fx.md](dats/fx.md) | Visual effect DAT locations |
| Texture format | [dats/tex.md](dats/tex.md) | Texture block format within DATs |

---

## Animation

Skeletal animation DAT format, export/import round-trip, emote routines, and
the `0x07` scheduler that cutscenes use to drive character motion.

| Doc | Summary |
|-----|---------|
| [anim/format.md](anim/format.md) | Binary format: skeleton sections, animation tracks, `0x07` emote-routine layout |
| [anim/export.md](anim/export.md) | Exporting to glTF 2.0 — race auto-detection, texture embedding, bulk export |
| [anim/import.md](anim/import.md) | Importing / replacing / layering tracks; `--static-base`, `--bones`, `--frames` |
| [anim/schedule.md](anim/schedule.md) | `0x07` scheduler routines — what they are and how to author them |
| [anim/emotes.md](anim/emotes.md) | Emote & skeleton-less DATs: base skeleton, overlay slots, routine duration |
| [anim/quickref.md](anim/quickref.md) | One-page cheatsheet of common export/import/layer commands |

---

## Zone & Mesh

Zone mesh decryption, geometry format, player-collision mesh (MZB), navmesh,
and per-object mesh (0x2A) import/export.

| Doc | Summary |
|-----|---------|
| [zone/format.md](zone/format.md) | Zone DAT structure: section types, two decryption schemes, coordinate system |
| [zone/export.md](zone/export.md) | Exporting zone geometry to glTF/FBX |
| [zone/import.md](zone/import.md) | Re-importing edited geometry (placement edits) |
| [zone/import-json.md](zone/import-json.md) | JSON change-set format used by the web level editor |
| [zone/collision.md](zone/collision.md) | Player-collision mesh (MZB): format, export, adding blockers |
| [zone/navmesh.md](zone/navmesh.md) | Server navmesh (`.nav`): bake from collision via Recast/Detour |
| [zone/subareas.md](zone/subareas.md) | Client-side sub-areas (shop interiors): how they work |
| [zone/inject-legacy.md](zone/inject-legacy.md) | Legacy injection path |
| [zone/lod-draw-distance.md](zone/lod-draw-distance.md) | LOD and draw-distance mechanics |
| [zone/reset.md](zone/reset.md) | Restoring a zone DAT to pristine state |
| [zone/templates.md](zone/templates.md) | Zone templates: snapshotting a curated zone for reuse |
| [zone/zones.md](zone/zones.md) | Zone ID reference and custom zone creation |
| [zone/prototype-zones.md](zone/prototype-zones.md) | Unreleased dev/prototype maps in `ROM/0/`: the older layout they use (chained mesh groups, `0x54` placements) and why they load half-empty |
| [zone/prototype-collision.md](zone/prototype-collision.md) | Worked example (ROM/0/41 as zone 501): stride conversion incl. `.base`, collision size ceiling, rebuilding collision from scratch |
| [mesh/format.md](mesh/format.md) | 0x2A mesh + 0x20 texture binary format, opcodes, engine constraints |
| [mesh/export.md](mesh/export.md) | Exporting a mesh DAT to glTF/FBX |
| [mesh/import.md](mesh/import.md) | Importing an edited mesh back into a DAT |

---

## Events & Cutscenes

The FFXI event bytecode VM — the cooperative ReqStack scheduler behind every
cutscene, NPC dialogue trigger, and scripted scene. Includes the full opcode
reference, camera handling, and a cutscene authoring guide.

| Doc | Summary |
|-----|---------|
| [events/README.md](events/README.md) | Big picture: per-zone file-id formulas, sources |
| [events/format.md](events/format.md) | Event DAT binary format: actor blocks, event-id table, scene bytecode |
| [events/opcodes.md](events/opcodes.md) | Complete event-VM opcode reference (0x00–0xD9) |
| [events/cutscenes.md](events/cutscenes.md) | How a scripted scene plays: trigger → run → release, camera, resource graph |
| [events/authoring.md](events/authoring.md) | Authoring custom cutscenes and NPC dialogue events |
| [events/dialogue.md](events/dialogue.md) | NPC dialogue: `EventMessage` + `d_msg` formats, codec, control codes |
| [events/event-data.md](events/event-data.md) | Extracted dataset: 277 zones, ~326k dialogue lines |
| [events/weather.md](events/weather.md) | Weather id → in-game name table for weather opcodes |
| [events/camera_scene_ids.md](events/camera_scene_ids.md) | Camera scene ID reference |
| [events/event_mode_bits.md](events/event_mode_bits.md) | Event mode flag bits |
| [events/maat_93_study.md](events/maat_93_study.md) | Zone 93 (Maat) event study |
| [events/prototype.md](events/prototype.md) | How a custom cutscene could be authored as JSON and compiled |
| [events/scene_dat_writer.md](events/scene_dat_writer.md) | Scene DAT writer internals |
| [cutscene_authoring.md](cutscene_authoring.md) | Top-level cutscene authoring overview |

---

## Dialog

The per-zone dialog DAT — NPC speech and cutscene message text. Separate from
the event DAT that triggers it.

| Doc | Summary |
|-----|---------|
| [dialog/format.md](dialog/format.md) | Container format: 24-bit length, XOR-`0x80`, offset table, `0x7F` prompt codes |
| [dialog/export.md](dialog/export.md) | Decoding to JSON: text, opcodes, hex |
| [dialog/edit.md](dialog/edit.md) | Authoring custom dialog: `\n`, `\v`, `{player}`, `{npc}`, faithful rebuild |

---

## Audio

`.bgw` (music) and `.spw` (sound effect) binary formats, ADPCM/PCM/ATRAC3
decode, and how the game links sounds to effects, zones, and mobs.

| Doc | Summary |
|-----|---------|
| [audio/README.md](audio/README.md) | Command overview, music/sfx search, batch decode |
| [audio/format.md](audio/format.md) | `.bgw`/`.spw` binary format, ADPCM codec, byte-exact gotchas |
| [audio/refs.md](audio/refs.md) | `0x3D` SoundEffectPointer: how the game links sounds to effects/zones |
| [sounds/footsteps.md](sounds/footsteps.md) | Terrain type → footstep sound + decal |

---

## Visual Effects

`0x05` particle/light generator format — the effect blocks baked into DATs for
fountains, lamps, fire, smoke, clouds, and point lights.

| Doc | Summary |
|-----|---------|
| [fx/README.md](fx/README.md) | Command overview, how effects are labelled, typical workflows |
| [fx/effects.md](fx/effects.md) | `0x05` byte format, opcode sub-sections, xim-validated param map |
| [fx/effect_system.md](fx/effect_system.md) | System-level: spell→effect resolution, `0x07` EffectRoutine, authoring |
| [fx/spells.md](fx/spells.md) | Spell visual effect system |
| [fx/export.md](fx/export.md) | Exporting an effect's mesh + material + params |
| [fx/copy.md](fx/copy.md) | Duplicating an effect within or across DATs |
| [fx/delete.md](fx/delete.md) | Removing effects |
| [fx/set.md](fx/set.md) | Editing effect params in place |
| [fx/json.md](fx/json.md) | JSON dump format |
| [fx/editor.md](fx/editor.md) | Browser-based particle/weapon-effect editor |

---

## Gear & Equipment

Gear model ID → DAT file resolution, using lookup tables reverse-engineered
from `FFXiMain.dll`.

| Doc | Summary |
|-----|---------|
| [gear/json.md](gear/json.md) | All gear model entries across races and slots |
| [gear/export.md](gear/export.md) | Gear mesh export |
| [gear/import.md](gear/import.md) | Gear mesh import |
| [gear/inject-legacy.md](gear/inject-legacy.md) | Legacy injection path |
| [gear/edit.md](gear/edit.md) | Editing gear records |
| [gear/particle-editor.md](gear/particle-editor.md) | Weapon particle editor |
| [gear/json.md](gear/json.md) | JSON schema |
| [reference/model-file-ids.md](reference/model-file-ids.md) | FTABLE/VTABLE structure, modelid formula, custom ID ranges |

---

## Mounts

Mount model resolution, the 64-menu / 255-model client limits, EN/JP string
DAT format, and the owned-only display gate.

| Doc | Summary |
|-----|---------|
| [mounts/README.md](mounts/README.md) | Overview and command table |
| [mounts/mechanism.md](mounts/mechanism.md) | Model resolution (`0x019131 + mountId`), limits, string-table formats |
| [mounts/data.md](mounts/data.md) | Per-id reference (0–255): name, key_item, file-id, ROM DAT |

---

## UI Textures

DXT1/3/5 textures packed into FFXI UI container DATs (`lobb` / `menu` format).
Commands live under `xi ui tex …` and `xi ui layout …`.

| Doc | Summary |
|-----|---------|
| [ui/export.md](ui/export.md) | `xi ui tex export` / `sx` — extract textures from a UI DAT |
| [ui/import.md](ui/import.md) | `xi ui tex import` / `si` — re-import edited textures |
| [ui/list.md](ui/list.md) | `xi ui tex list` — list UI DATs by magic |
| [ui/extract.md](ui/extract.md) | Redirect — there is no `extract` command |
| [utils/texture.md](utils/texture.md) | DDS ↔ PNG conversion helpers |

---

## Entity & NPC

NPC appearance records and recolouring.

| Doc | Summary |
|-----|---------|
| [entity/npc-look.md](entity/npc-look.md) | NPC look record format |
| [entity/recolor.md](entity/recolor.md) | Recolouring NPC models |

---

## Object Placement

Per-object placement records within zone DATs — add, move, clone, delete.

| Doc | Summary |
|-----|---------|
| [object/README.md](object/README.md) | Overview and command table |
| [object/import.md](object/import.md) | Adding a new placed object |
| [object/export.md](object/export.md) | Exporting placement data |
| [object/clone.md](object/clone.md) | Cloning a placed object |
| [object/delete.md](object/delete.md) | Removing a placed object |
| [object/replace.md](object/replace.md) | Swapping an object's model |
| [object/set-placement.md](object/set-placement.md) | Editing position/rotation/scale |
| [object/swap-placement.md](object/swap-placement.md) | Swapping two placements |
| [object/json.md](object/json.md) | JSON schema |

---

## Key Items

| Doc | Summary |
|-----|---------|
| [keyitems/categories.md](keyitems/categories.md) | Key item category / menu display mechanics |

---

## Reference

Core lookup tables and named-DAT catalog.

| Doc | Summary |
|-----|---------|
| [reference/model-file-ids.md](reference/model-file-ids.md) | FTABLE/VTABLE structure, modelid formula, custom ID ranges |
| [reference/named-dats.md](reference/named-dats.md) | fileId catalog: string tables, system messages, per-zone event/entity ranges |
| [reference/dat_sections.md](reference/dat_sections.md) | DAT section type reference |
| [ffximain/dll.md](ffximain/dll.md) | `xi dll` category: shared POL1 unpack/pack for FFXiMain / polcore / app |
| [ffximain/ffximain.md](ffximain/ffximain.md) | FFXiMain.dll: POL1 algorithm, gear groups, model formulas, Ghidra |
| [ffximain/polcore.md](ffximain/polcore.md) | polcore.dll: PlayOnline COM host, IPOLCoreCom, base collision with FFXiMain |
| [ffximain/app.md](ffximain/app.md) | app.dll: PlayOnline Viewer UI / apps module |
| [ffximain/inventory.md](ffximain/inventory.md) | FFXiMain.dll 80→120 inventory expansion: relocation root cause, object-relative patching, what's left |
| [dll/README.md](dll/README.md) | `xi dll` command reference: list / unpack / pack / patch / text-dump / gear-groups / gear-patch / crashdump |
| [common_crashes.md](common_crashes.md) | Client crash diagnosis: overlay table shadowing, scene-file churn, FFXI-2003 |

---

## Research

Exploratory scripts from the `FFXiMain.dll` reverse-engineering session.
Documents the process of recovering the LZSS decompression algorithm and
searching the decompressed binary for gear/model formula constants.

| File | Summary |
|------|---------|
| [dats/research/pol_decompress.md](dats/research/pol_decompress.md) | How to use the decompressor + background on the LZSS algorithm |
| [dats/research/pol_decompress.py](dats/research/pol_decompress.py) | Standalone decompressor: POL1 → `FFXiMain_unpacked.dll` (Ghidra/IDA) |
| [dats/research/pol1_inspect.py](dats/research/pol1_inspect.py) | PE section inspector: dump POL1 layout and stub region |
| [dats/research/pol1_unpack.py](dats/research/pol1_unpack.py) | Exploratory OEP finder + brute-force rotation/XOR attempts |
| [dats/research/pol_gear_formula.md](dats/research/pol_gear_formula.md) | Gear race-offset constants found in the decompressed binary |
| [dats/research/search_gear_formula.py](dats/research/search_gear_formula.py) | Search decompressed binary for gear race-offset constants |
| [dats/research/search_model_formula.py](dats/research/search_model_formula.py) | Search decompressed binary for monster modelid formula constants |
| [dats/research/texture_convert.py](dats/research/texture_convert.py) | Texture conversion helper |
| [dats/research/header_summary.json](dats/research/header_summary.json) | Aggregated DAT header scan results |

---

## External Source

Reference material from other researchers.

| File | Summary |
|------|---------|
| [external_source/zone184_event22.md](external_source/zone184_event22.md) | Annotated event bytecode dump — zone 184, event 22 |
| [external_source/dump_event.py](external_source/dump_event.py) | Event DAT dumper script |
| [external_source/New-Player-Cutscene-Pipeline.md](external_source/New-Player-Cutscene-Pipeline.md) | New-player cutscene pipeline notes |

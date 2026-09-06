# xi-tools Docs

## Quick start

```
uv sync
uv run xi --help
```

**Adding prebuilt DATs at new model ids?** Use the interactive wizard:
`uv run xi dats new` — place gear/mount/entity DATs into the live install
(`FFXI_DIR`) from a project manifest ([docs](dats/README.md)).

**Repeating a list of commands?** `uv run xi run FILE` runs every `xi …` line in a
text/markdown file (blank lines and `#` comments skipped, `set NAME=value` → `%NAME%`),
stopping at the first failure and printing the `--start N` to resume from. Example script:
[title/custom_title_screen.md](title/custom_title_screen.md).

**Client crashing after a publish?** See [common_crashes.md](common_crashes.md) —
diagnosis guide for the recurring crashes (overlay tables shadowing a registration,
scene-file churn, FFXI-2003 on zone-in) and what to check in the Ashita log.

**Building or restyling the web level editor UI?** Follow the
[Level-Editor UI Design System](design/editor-ui.md) — the accent-tinted hero / metric-tile
/ card / gradient-CTA language (reference: the Event Info dashboard and Cutscene Publish
tab). New panels and sections should be built from these patterns.

## FTABLE

Manage the shared FTABLE/VTABLE model lookup tables — reserving space for custom
**entity** and **gear** models, inspecting the ranges, resolving IDs, and undoing it all.

**Start here:** custom entities go at modelid **15,000+**, custom gear at modelid
**3,000+** (per race + slot). One command sets up both — see [ftable/expand.md](ftable/expand.md).

- `uv run xi ftable expand` ([docs](ftable/expand.md)) — One command: reserve space for BOTH custom entity + gear models (defaults: **entity 30000 + gear 4095**). `--no-gear` / `expand entity [N]` / `expand gear [N]` to do just one. `--no-pivot` only on bare expand / expand entity; gear always syncs pivot.
- `uv run xi ftable json` — Public JSON listing / table dump (`--tables`, `--models`, …)
- `uv run xi ftable json --tables` — Raw per-ROM table sizes and registration counts
- `uv run xi model json --free` ([docs](model/free.md)) — Show the custom model range and next free model id
- `uv run xi ftable lookup --file-id N` ([docs](ftable/lookup.md)) — Resolve a file_id or modelid to a DAT path (`--table N`, prints header bytes + file size)
- `uv run xi ftable range-scan` ([docs](ftable/range-scan.md)) — Scan FTABLE for occupied file_id blocks
- `uv run xi ftable compare` — Diff registered file_ids between two FTABLE roots
- `uv run xi ftable set` ([docs](ftable/set.md)) — Dual-write an arbitrary file_id → `ROM{N}/<subdir>/<file>.DAT` (base + overlay); the low-level inverse of `delete`
- `uv run xi ftable delete` ([docs](ftable/delete.md)) — Zero a custom entry (base + ROM10) by file_id or modelid
- `uv run xi ftable reset` — Restore tables from `.base` backups + remove injected gear (⚠️ reverts ALL custom registrations)

#### References

- [reference/model-file-ids.md](reference/model-file-ids.md) — FTABLE/VTABLE structure, modelid formula, custom ranges

## GEAR

Resolve gear model IDs to DAT files for all races and equipment slots, using
lookup tables reverse-engineered from `FFXiMain.dll` by Atom0s.

- `uv run xi gear json` ([docs](gear/json.md)) — List all gear model entries across all races and slots
- `uv run xi gear search <query>` — Search race/slot/model/file/DAT entries
- `uv run xi gear export` / `uv run xi gear import` — Existing gear edit round-trip

#### References

- [reference/model-file-ids.md](reference/model-file-ids.md) — FTABLE structure and file_id resolution

## MODEL / MESH / ANIM

Inspect registered model IDs, and export/import meshes, textures, and skeletal animations via glTF/FBX.

- `uv run xi model json` ([docs](model/json.md)) — Dump registered model IDs, file IDs, and DAT paths
- `uv run xi model json --free` ([docs](model/free.md)) — Show the next free custom modelid slot and mob_pools blob
- `uv run xi anim export` ([docs](anim/export.md)) — Export skeleton + animation + **textures** to glTF 2.0 (`--no-tex` to skip); race auto-detected (monster/NPC DATs use their own skeleton — no HumeFemale fallback), digit-less `--anim` merges an emote's parts into one full-body clip, `--mesh` dresses it in a gear look, no-`<dat>` bulk-exports every race
- `uv run xi anim import` ([docs](anim/import.md)) — Replace / **create** / **layer** a track. New name = new track (`--add tlk yap.gltf`); `--layer`/`--bones`/`--frames` overlay part of one clip onto another; `--static-base` holds the body still for partial clips; rotation-based, `--fps` resample, auto-grows emote routine duration, `--no-base` to keep an imported mesh
- `uv run xi anim schedule` ([docs](anim/schedule.md)) — Create/list the **`0x07` scheduler routines** a cutscene needs (`schedule add --clip tlk0`); a cutscene can only `SetAction` a routine, never a raw clip, so a newly imported clip needs one to show in the cutscene author. `schedule create` chains several clips (wizard or `--clip A --clip B`) — transition once, then loop (sit-down → sitting idle). Also `anim import --add-schedule` in one step
- **Quick reference:** [anim/quickref.md](anim/quickref.md) — copy-paste commands for every common export/import/layer task
- `uv run xi mesh export` ([docs](mesh/export.md)) — Export skeleton + mesh + textures to a self-contained `.glb` / texture-embedded `.fbx`
- `uv run xi mesh import` ([docs](mesh/import.md)) — Import an edited/replaced model back into a DAT (re-skin, auto-scale, multi-section, texture injection)

#### References

- [reference/model-file-ids.md](reference/model-file-ids.md) — modelid formula derivation, safe custom range
- [anim/quickref.md](anim/quickref.md) — one-page cheatsheet of anim export/import/layer commands
- [anim/emotes.md](anim/emotes.md) — editing emotes & skeleton-less DATs: base skeleton, overlay slots (poi0/poi1), routine duration, full round-trip
- [anim/format.md](anim/format.md) — internal DAT animation binary format (incl. the `0x07` emote-routine `dur`)
- [mesh/export.md](mesh/export.md) / [mesh/import.md](mesh/import.md) — mesh export/import commands
- [mesh/format.md](mesh/format.md) — 0x2A mesh + 0x20 texture binary format, opcodes, and engine constraints
- **Mounts** now have their own section — see [MOUNTS](#mounts) / [mounts/README.md](mounts/README.md). (Mount models are entity-style DATs, so the model side reuses the entity FTABLE + mesh tooling.)

## MOUNTS

How FFXI mounts work and how to author custom ones — model resolution (`0x019131 + mountId`),
the 64-menu / 255-model limits, the owned-only menu, and the EN/JP string DATs.

- `uv run xi mount json` — enumerate mounts: id, EN/JP name, key item, file-id, ROM DAT, free/occupied
- `uv run xi mount export <id>` — dump one mount's full record (names, help, key-item text) → JSON
- `uv run xi mount import <id> --dat <file>` — override an existing mount's model
- `uv run xi mount delete <id>` — remove a custom mount + emit server cleanup
- `uv run xi dats prepare` / `xi dats build` ([docs](dats/README.md)) — author brand-new mounts as reproducible package actions

#### References

- [mounts/README.md](mounts/README.md) — overview, `xi mount` command table, EN/JP DAT-path reference
- [mounts/mechanism.md](mounts/mechanism.md) — model resolution, the 64/255 limits, display-vs-ride gates, string-table formats, the >64 client patch
- [mounts/data.md](mounts/data.md) — per-id reference table (0–255): name, is_mount, key_item, file-id, ROM DAT

## ZONE

Export, edit, and re-import FFXI zone geometry. Zone meshes and their placement data
are encrypted; both are decrypted automatically using key tables read from `FFXiMain.dll`.
See [zone/README.md](zone/README.md) for the full command table.

- `uv run xi zone export` ([docs](zone/export.md)) — Decrypt + export a zone (meshes + placements + textures) to `.glb`/`.fbx`; `--collision` also exports the player-collision mesh
- `uv run xi zone import` ([docs](zone/import.md)) — Import an edited GLB back (move/rotate/scale/delete + mesh-merge + add collision)
- `uv run xi zone import-json` ([docs](zone/import-json.md)) — Apply a JSON change-set from the web level editor to a local output mirror
- `uv run xi dats prepare zone-changes.json ...` ([docs](dats/README.md)) — Add a zone change-set to a distributable DAT package
- `uv run xi bridge` — WebSocket backend for the [xi-zone-editor](https://github.com/vekien/xi-zone-editor) desktop app (`ws://127.0.0.1:8777/ws`)
- `uv run xi object …` ([docs](object/README.md)) — Add, move, replace, clone, or delete individual placements
- `uv run xi zone import-collision <dat> hull.obj` ([docs](zone/collision.md#replace-the-whole-collision--xi-zone-import-collision)) — Replace (or `--append`) the whole collision from an authored OBJ; `--compact-buckets` fits dense meshes
- `uv run xi zone navmesh` ([docs](zone/navmesh.md)) — Bake a server navmesh (`.nav`) from the zone's collision
- `uv run xi zone new` — Create a blank zone from the pre-baked template; `--sky <DAT>` to splice atmosphere
- `uv run xi zone patch-proto <dat>` ([docs](zone/prototype-zones.md)) — Convert a pre-production zone's `0x54` placements to the retail `0x64` stride
- `uv run xi zone package` ([docs](zone/package.md)) — Bundle custom zones (DATs, FTABLE10/VTABLE10 incl. Ashita override copies, server Lua) into one zip
- `uv run xi zone delete <id>` — Remove a custom zone (400+), zero FTABLE10, print server SQL
- `uv run xi zone build-from-manifest` — Assemble a zone from a Godot designer `build_manifest.json`
- `uv run xi zone reset` — Restore a zone DAT to pristine

#### References

- [zone/README.md](zone/README.md) — full command table + typical workflows
- [zone/format.md](zone/format.md) — zone mesh + ZoneDef binary format and the two decryption schemes
- [zone/collision.md](zone/collision.md) — player-collision mesh (MZB): export, edit, append blockers
- [zone/navmesh.md](zone/navmesh.md) — server navmesh (`.nav`): bake + validate via native Recast/Detour

## FX

Inspect and edit the **visual effects** baked into a DAT — `0x05` particle/light
generators (fountain spray, lamp glow, fire, smoke, clouds, point lights). Effects
live inside the DAT and are unencrypted, so listing and editing are direct.
See [fx/README.md](fx/README.md) for the full command table + workflows.

- `uv run xi fx json` ([docs](fx/json.md)) — Dump every effect (+ decoded params, optionally raw opcodes) to JSON
- `uv run xi fx set` ([docs](fx/set.md)) — Edit params in place: position / scale / color / draw-distance / spawn / count / autorun
- `uv run xi fx copy` ([docs](fx/copy.md)) — Duplicate an effect — same-DAT, or cross-DAT (`--from`) bringing its deps
- `uv run xi fx delete` ([docs](fx/delete.md)) — Remove effect(s) by exact name or name-prefix
- `uv run xi fx export` ([docs](fx/export.md)) — Export an effect's 3D mesh + material/texture + decoded params bundle
- `uv run xi gui weapon` ([docs](fx/editor.md)) — Serve the browser-based particle/weapon-effect editor at `http://localhost:8776/`
- `uv run xi gui spells` ([docs](fx/spells.md)) — Serve the browser-based spell-effect editor at `http://localhost:8774/`

#### References

- [fx/README.md](fx/README.md) — full command table, how effects work + are labelled
- [fx/effects.md](fx/effects.md) — the `0x05` byte format, opcode sub-sections, xim-validated param map, fountain case study
- [fx/effect_system.md](fx/effect_system.md) — system-level view: spell→effect resolution, `0x07` EffectRoutine, authoring new visuals

## EVENTS

How FFXI **events** work — the system behind **cutscenes**, **NPC dialogue**, menu
prompts, and scripted scene action. The `xi event` commands act on the per-zone
Event DAT — `xi event cutscene export` (bytecode disassembly) and `xi event
dialogue` (author new dialogue events **and** edit the underlying dialog string
table, see the DIALOGUE section below). The docs below cover the bytecode VM, the
dialogue/message formats, and the already-extracted dialogue dataset.
See [events/README.md](events/README.md) for the big picture + per-zone file IDs.

- `uv run xi event explain <zone> <actor|name> [--event N]` ([docs](events/retail-events.md)) — annotated disassembly: every operand resolved, dialog text inline; `--list` shows a zone's actors
- `uv run xi event decompile <zone> <actor> --event N -o out.json --check` ([docs](events/retail-events.md)) — retail event → `xi.cutscene.v1` JSON, then recompile and compare
- `uv run xi event sweep <zone…> --check --jobs 8` ([docs](events/retail-events.md)) — decompile and round-trip every event of one or more zones
- `uv run xi event cutscene compile my_event.json` ([docs](events/authoring.md)) — JSON → event/dialog DATs; resolves the zone's DATs from the JSON
- `uv run xi event lint <zone> <actor>` / `survey --op 0x71` / `npc list|add <zone>` ([docs](events/authoring.md)) — pre-flight checks, opcode surveys, the entity-name table
- [events/format.md](events/format.md) — Event DAT binary format (per-actor blocks, event-id table, scene bytecode)
- [events/opcodes.md](events/opcodes.md) — the complete event-VM opcode reference (0x00–0xD9)
- [events/dialogue.md](events/dialogue.md) — NPC dialogue: `EventMessage` + `d_msg` formats, the event-string codec, control codes, in-game display
- [events/cutscenes.md](events/cutscenes.md) — how a scripted scene plays: trigger → run → release, camera handling, the resource graph
- [events/weather.md](events/weather.md) — weather **id → in-game name** table (+ element) for the weather opcodes
- [events/retail-events.md](events/retail-events.md) — **decompile any retail event to JSON, edit it, recompile it, prove the round trip** (`explain`, `decompile --check`, `sweep`, `cutscene compile`)
- [events/typed_opcodes.md](events/typed_opcodes.md) — the typed opcode table the decompiler and compiler share
- [events/prototype.md](events/prototype.md) — how a **custom cutscene** could be authored as JSON, compiled to a DAT, and triggered
- [events/event-data.md](events/event-data.md) — the extracted dataset (277 zones, ~326k dialogue lines) and how to search it

#### References

- [events/README.md](events/README.md) — big picture, per-zone file-id formulas, sources
- [dat_ror1.md](dat_ror1.md) — ROR-1 text encoding for some `menu` string DATs


## AUDIO

Decode FFXI music (`.bgw`) and sound effects (`.spw`) to WAV, browse them with real
names/categories, and find which sounds any effect/zone/mob DAT uses. ADPCM + PCM
decode in pure Python, **byte-for-byte identical to vgmstream**; ATRAC3 routes to
`vgmstream-cli` when present.

- `uv run xi audio json/search --type music` ([docs](audio/README.md)) — List/search music, with titles from MusicInfo
- `uv run xi audio json/search --type sfx` ([docs](audio/README.md)) — List/search sound effects, with game categories
- `uv run xi audio export --type music|sfx` ([docs](audio/README.md)) — Decode music or sound effects
- `uv run xi audio decode FILE…` ([docs](audio/README.md)) — Decode explicit `.bgw`/`.spw` paths to `.wav`
- `uv run xi audio info FILE` ([docs](audio/README.md)) — Dump a file's parsed header
- `uv run xi audio refs <dat>` ([docs](audio/refs.md)) — List the sounds a DAT references (spell VFX, zone ambient, mob) → JSON
- `uv run xi audio scan` ([docs](audio/scan.md)) — Walk **every** DAT: where each sound is used, and whether that DAT is a zone / NPC / spell / gear … → JSON
- `uv run xi batch audio_music` / `audio_sfx` ([docs](audio/README.md)) — Decode **all** music / sfx + write a categorised `catalog.json`

#### References

- [audio/format.md](audio/format.md) — `.bgw`/`.spw` binary format, ADPCM codec, and the byte-exact gotchas
- [audio/refs.md](audio/refs.md) — the `0x3D` SoundEffectPointer system: how the game links sounds to effects/zones
- [audio/scan.md](audio/scan.md) — the whole-install sound usage map and how each DAT is identified
- [sounds/footsteps.md](sounds/footsteps.md) — terrain type → footstep sound + decal

## EVENT DIALOGUE

`xi event dialogue` is the single home for NPC-text work. Authoring (`actors`,
`new` — append lines + splice a new event) is covered in [events/authoring.md](events/authoring.md);
the commands below decode/edit the per-zone **dialog** DAT — NPC speech / cutscene
text (the event message box, not the chat log) — to readable text + a faithful list
of the embedded control codes (the ▼ continue-prompt, newlines, name/value
substitutions, …). Every command takes `<DAT>` as a dialog DAT path (`.DAT`
optional), a **zone id**, a **zone name**, or a zone's **model DAT** — the latter
three route to the zone's dialog DAT and tell you which one.

- `uv run xi event dialogue actors <DAT>` ([docs](events/authoring.md)) — list a zone's NPC actor ids (pick one for `new --actor`)
- `uv run xi event dialogue new <DAT> --json lines.json --actor <id>` ([docs](events/authoring.md)) — append lines + splice a new event that prints them
- `uv run xi event dialogue info <DAT>` ([docs](dialog/export.md)) — entry count + opcode histogram
- `uv run xi event dialogue search <DAT> "text"` ([docs](dialog/export.md)) — find the index + entry for some text (substring/`--regex`, spans line breaks) — pairs with `edit --index`
- `uv run xi event dialogue export <DAT>` ([docs](dialog/export.md)) — decode to 3 sibling files under `exports/event/dialogue/<rom>/`: `<stem>.json` (text), `.opcodes.json`, `.hex.json` (`-o` base, `--no-opcodes`/`--no-raw`, `--json` stdout, `--preview`, `--grep`, `--prompts-only`)
- `uv run xi event dialogue edit <DAT> --index <N> --text "<text>"` ([docs](dialog/edit.md)) — author custom dialog: `\n` newline, `\v` press-enter ▼, `{player}`/`{npc}`/`{auto:N}`; rebuilds the DAT to the output mirror (`--dry-run`)
- `uv run xi event dialogue reset <DAT>` ([docs](dialog/edit.md)) — undo all edits: delete the output mirror, or restore from `<dat>.base` in-place (`--dry-run`); `--full` also resets the zone's **event** DAT, fully undoing a `dialogue new`

#### References

- [dialog/format.md](dialog/format.md) — event-message container (24-bit length, XOR-`0x80`, offset table), the `0x7F` continue-prompt codes, and the opcode reference
- [dialog/export.md](dialog/export.md) — `export`/`info` usage + JSON schema
- [dialog/edit.md](dialog/edit.md) — `edit` authoring syntax + how the faithful rebuild works

## UI

Extract DXT1 textures from FFXI UI container DAT files (`lobb` / `menu` format).
Commands are under `xi ui tex …` / `xi ui layout …`.

- `uv run xi ui tex export` ([docs](ui/export.md)) — Extract all textures from a UI DAT as `.dds` files
- `uv run xi ui tex sx` — Extract a UI DAT to `exports/ui/...` and convert all DDS files to PNG
- `uv run xi ui tex si` — Rebuild DDS files from edited PNGs in `exports/ui/...` and import them back into the DAT (`--all-themes` applies one edited window skin to all of `ROM/0/14..21`)

Example Flow:
- uv run xi ui tex export "<FFXI_DIR>\ROM\0\1.DAT" --output-dir "exports/ui/1"
- uv run xi utils dds2png "exports/ui/1" "exports/ui/1"
- uv run xi utils png2dds "exports/ui/1" "exports/ui/1"
- uv run xi ui tex import "<FFXI_DIR>\ROM\0\1.DAT" "exports/ui/1" --output-dat "<FFXI_DIR>\ROM\0\1.DAT"

Simplified flow:
- `uv run xi ui tex sx "ROM\0\1.DAT"`
- edit the PNG files in `exports/ui/0/1`
- `uv run xi ui tex si "ROM\0\1.DAT"`


#### Known DATs

| file_id | DAT | Contents |
|---|---|---|
| 39541 | `ROM/119/50.DAT` | Title / splash + font sheet (2 textures) |
| 39542 | `ROM/119/51.DAT` | Main UI sheet — buttons, gauges, icons, keyboard (28 textures) |

## TITLE SCREEN

The login screen flies real zones as a live background (`ROM/0/23.DAT`, `titl`); the
logos, wardrobe badges and menus are in `ROM/119/50.DAT` (`lobb`). Every command takes
`--ffxi DIR` to target a pivot/override tree for one call.

- `uv run xi title list` / `timeline` / `weather` ([docs](title/README.md)) — zone segments, shot list per segment, fog per segment
- `uv run xi title set-zone 12 115` then `xi title aim 12` ([docs](title/README.md)) — point a segment at another zone and re-aim its cameras
- `uv run xi title export` / `import` / `camera export` / `camera import` ([docs](title/README.md)) — byte-exact camera round trip (`--timing` for the `0x0210` durations)
- `uv run xi title menu` / `sprite` / `wardrobe` ([docs](title/ui_chrome.md), [main_menu.md](title/main_menu.md), [wardrobe_numbers.md](title/wardrobe_numbers.md)) — move UiMenus, patch sprite quads, hide the wardrobe 3–8 badges
- Worked example as a runnable script: [title/custom_title_screen.md](title/custom_title_screen.md) (`xi run docs/title/custom_title_screen.md`)

#### References

- [title/README.md](title/README.md) — command reference, camera JSON fields, timing, play order
- [dats/ROM_0_23.md](dats/ROM_0_23.md) — the `titl` byte format
- [HANDOVER.md](HANDOVER.md) — findings, dead ends and the unresolved opening-segment problem

## MODEL VIEWER

Bake the JSON [xi-model-viewer](https://github.com/vekien/xi-model-viewer) loads.

- `uv run xi mv update [--only gear,npcs,…] [--dry-run]` ([docs](mv/README.md)) — append missing rows to `mv/lists/*.json`; curated names are never rewritten. Targets: `gear`, `gear-sets`, `gear-labels`, `music`, `sfx`, `zone-music`, `effects`, `images`, `npcs`, `npc-anims`, `zone-names`, `file-ids`
- `uv run xi mv database [--only armor,weapons] [--lang en,jp]` ([docs](mv/README.md)) — decode the item and `d_msg` tables once to `mv/db/<table>.<lang>.json`

## FFXIMAIN

Decompress the POL1-packed `.text` section of FFXiMain.dll for static analysis. Research only — the game loads the original packed DLL.

- `uv run xi dll list` ([docs](ffximain/dll.md)) — Resolve packed paths for FFXiMain / polcore / app
- `uv run xi dll ffximain unpack` ([docs](ffximain/ffximain.md)) — Write `FFXiMain_unpacked.dll` as a valid PE (load in Ghidra/IDA, image base `0x10000000`)
- `uv run xi dll polcore unpack` / `xi dll app unpack` ([docs](ffximain/polcore.md), [app.md](ffximain/app.md)) — Same POL1 unpack for Viewer modules
- `uv run xi dll ffximain pack` / `polcore pack` / `app pack` — Re-compress `.text` into game-loadable POL1
- `uv run xi dll ffximain patch` ([docs](dll/patch.md)) — Apply a replayable `va/expect/replace` `.patch` to an unpacked DLL (e.g. the [80→120 inventory](ffximain/inventory.md) expansion)
- `uv run xi dll ffximain text-dump` ([docs](ffximain/ffximain.md)) — Write `pol_decompressed.bin` + full disassembly `.txt` (~45 MB, ~2–3 min)
- `uv run xi dll ffximain crashdump` — Parse Windows minidumps next to `%LOCALAPPDATA%\CrashDumps`

## Research

Exploratory scripts from the FFXiMain.dll reverse engineering session. Not for regular use — outputs are large and tools are one-off.

- [pol_gear_formula.md](../research/pol_gear_formula.md) — Partial notes from the gear race-offset investigation (work in progress)
- [search_model_formula.py](../research/search_model_formula.py) — Search `pol_decompressed.bin` for monster modelid formula constants
- [search_gear_formula.py](../research/search_gear_formula.py) — Search `pol_decompressed.bin` for gear race-offset constants
- [disasm_lookup.py](../research/disasm_lookup.py) — Disassemble a window around a hardcoded raw file offset in the packed DLL
- [pol1_inspect.py](../research/pol1_inspect.py) — Dump PE section layout and hex-inspect the POL1 stub region
- [pol1_unpack.py](../research/pol1_unpack.py) — Exploratory OEP finder and brute-force rotation attempts
- `pol_decompressed.bin` / `pol_decompressed.txt` — outputs of `uv run xi dll ffximain text-dump`

## DAT Map

Community research into what lives where across the FFXI ROM file system —
UI textures, system files, cutscenes, zone maps, music, and more.

- [dat_index.md](dat_index.md) — **Start here.** Simple grouped index of what each DAT group does
- [named-dats.md](reference/named-dats.md) — **fileId catalog** of identified DATs (item/spell/quest/zone string tables, system messages, weather, per-zone event/entity ranges)
- [dats.md](dats.md) — Known DAT locations by category (living document, with evidence)
- [dats_boot.md](dats_boot.md) — Which DATs actually load at game boot
- [dat_ror1.md](dat_ror1.md) — ROR-1 text encoding used by `menu` DATs (mission/quest text), with codec

### Per-file deep dives

| DAT | Doc | Summary |
|---|---|---|
| `ROM/0/4.DAT` | [dats/ROM_0_4.md](dats/ROM_0_4.md) | Fixed-page graphics/system data (4096×0xC00 records, no magic) |
| `ROM/0/14–21.DAT` | [dats/ROM_0_14-21.md](dats/ROM_0_14-21.md) | 8 window background skins (`win0`, DXT tiles) |
| `ROM/0/23.DAT` | [dats/ROM_0_23.md](dats/ROM_0_23.md) | Title screen scene — camera splines, weather sequence, zone references |
| `ROM/0/24–26.DAT` | [dats/ROM_0_24.md](dats/ROM_0_24.md) | Character-select scene controllers (`sel_`) |
| `ROM/0/27.DAT` | [dats/ROM_0_27.md](dats/ROM_0_27.md) | UI animation / value curves (`damv`) |
| `ROM/97/*.DAT` | [dats/ROM_97_menu_strings.md](dats/ROM_97_menu_strings.md) | Menu label string tables (`XISTRING`, plain ASCII) |
| `ROM/118/115.DAT` | [dats/ROM_118_115.md](dats/ROM_118_115.md) | **Live** mission/quest text DB (`menu`, ROR-1 encoded) |
| `ROM/168/25.DAT` | [dats/ROM_168_25.md](dats/ROM_168_25.md) | Auto-translate phrase dictionary |

## UI

Extract and re-import DXT-compressed textures from UI container DATs.

- `uv run xi ui tex export` ([docs](ui/export.md)) — Extract all textures from a UI DAT as `.dds` files
- `uv run xi ui tex import` ([docs](ui/import.md)) — Import edited `.dds` files from an export folder back into a UI DAT
- `uv run xi ui tex sx` / `uv run xi ui tex si` — Shortcut export/edit/import flow using a DAT-derived `exports/ui/...` folder

## Utils

Standalone texture conversion helpers.

- `uv run xi utils dds2png` ([docs](utils/texture.md)) — Convert `DXT1` / `DXT3` / `DXT5` DDS files to PNG
- `uv run xi utils png2dds` ([docs](utils/texture.md)) — Convert PNG files to DDS using `texconv`

## Notes

- Run `uv sync` to install all dependencies (`click`, `pefile`, `capstone`).
- Generated outputs (`exports/`, `*.gltf`, `*.bin`, `*.DAT.base`) are gitignored.
- `uv run xi dll ffximain text-dump` takes ~2–3 minutes. Outputs land in `research/` and are gitignored by default.
- Custom ROM namespace defaults to `ROM10` — override with `CUSTOM_FTABLE=ROMn`.

# xi quick command list

Paths can be ROM-relative, for example `ROM/1/41`, and resolve against `FFXI_DIR`.

This lists the current public CLI command surface. Hidden compatibility aliases are not included.

## Top Level

```text
xi anim
xi audio
xi batch
xi bridge
xi dats
xi entity
xi event
xi dll
xi ftable
xi fx
xi gear
xi launcher
xi mesh
xi misc
xi model
xi mount
xi object
xi run
xi server
xi tex
xi ui
xi utils
xi zone
```

## Bridge (zone editor backend)

```text
xi bridge
xi bridge --host 127.0.0.1 --port 8777 --idle-secs 90
```

WebSocket at `ws://HOST:PORT/ws`. Used by xi-zone-editor; exits after idle-secs with no clients.

## Dats Packages

```text
xi dats json
xi dats prepare
xi dats build
xi dats new
xi dats package
xi dats release
xi dats changelog
xi dats undo
```

### Example: custom mesh end-to-end

```text
# 1. Export source DAT to editable GLB (+ split textures + FBX)
xi mesh export ROM/351/102 --split-tex --fbx

# 2. Prepare a project manifest — writes dats/battle_worn_byakko.json
xi dats prepare exports/mesh/rom/351/102/102_schema.json --project battle_worn_byakko --replace

# 3. Build (auto-resolves dats/battle_worn_byakko.json) — writes DAT + patches FTABLE
xi dats build battle_worn_byakko
```

## Model / Mesh / Anim

```text
xi model search
xi model json

xi mesh export
xi mesh import
xi mesh json

xi anim export
xi anim import
xi anim list
xi anim json
xi anim schedule
xi anim schedule list
xi anim schedule add
xi anim schedule create
xi anim schedule copy
xi anim schedule edit
```

## Entity

```text
xi entity list
xi entity inject
xi entity recommend
xi entity recolor
xi entity look
xi entity mesh export
xi entity mesh import
```

## Gear

```text
xi gear search
xi gear list
xi gear json
xi gear export
xi gear import
xi gear edit
xi gear character
xi gear inject
xi gear import-json
xi gear recolor
```

## Mounts

```text
xi mount search
xi mount list
xi mount json
xi mount export
xi mount import
xi mount inject
xi mount delete
```

## Zones / Objects / FX

```text
xi zone search
xi zone list
xi zone json
xi zone export
xi zone tree
xi zone import
xi zone import-json
xi zone inject
xi zone reset
xi zone build-from-manifest
xi zone navmesh
xi zone navmesh-info
xi zone new
xi zone make-template
xi zone scaffold-server
xi zone delete
xi zone footsteps
xi zone package             # bundle custom zones (DATs, tables, override-tree tables, server Lua) into one zip
xi zone patch-proto         # convert a prototype zone's 0x54 placement records to retail 0x64
xi zone import-collision    # replace/append the whole collision from an OBJ (--compact-buckets, --reset)
xi zone fx
xi zone fx list

# zone export defaults to what the client draws; add the filtered classes back:
xi zone export ROM/23/95 --with-collision-proxies --with-far-lod
xi zone export ROM/1/41 --no-subareas

xi object json
xi object export
xi object import
xi object replace
xi object clone
xi object delete
xi object set-placement
xi object swap-placement

# also available as xi zone object …
xi zone object list
xi zone object export
xi zone object import
xi zone object replace
xi zone object clone
xi zone object delete
xi zone object set-placement
xi zone object swap-placement

xi fx json
xi fx list
xi fx dump
xi fx set
xi fx copy
xi fx copy-group
xi fx delete
xi fx delete-group
xi fx export
```

## Audio

```text
xi audio search
xi audio json
xi audio export
xi audio decode
xi audio info
xi audio refs
xi audio scan
xi audio import
xi audio install

xi audio music list
xi audio music export
xi audio sfx list
xi audio sfx export
```

## Textures / UI

```text
xi tex json
xi tex list
xi tex export
xi tex import

xi ui tex
xi ui tex export
xi ui tex import
xi ui tex list
xi ui tex sx
xi ui tex si

# Title/login screen (titlwin 1024×1024, expansion logos ex1us/ex2us/ex5us)
xi ui tex sx ROM/119/50.DAT
xi ui tex si ROM/119/50.DAT

# Lobby (xilogo — PlayOnline/FFXI logo, JP expansion logos)
xi ui tex sx ROM/0/2.DAT
xi ui tex si ROM/0/2.DAT

# --ffxi overrides FFXI_DIR for one command (e.g. edit the pivot/override copy)
xi ui tex sx ROM/119/50.DAT --ffxi "<FFXI_PIVOT_DIR>"
xi ui tex si ROM/119/50.DAT --ffxi "<FFXI_PIVOT_DIR>"

# Edited PNGs are resampled to the size the game expects (titlwin -> 1024, ex1us -> 256)
xi ui tex si ROM/119/50.DAT
xi ui tex si ROM/119/50.DAT --no-resize        # import textures, leave rects alone
xi ui tex si ROM/119/50.DAT --repair-rects     # rebuild rects from the reference sheet

# Go past vanilla resolution: keep a bigger PNG and scale its sprite rects to match
xi ui tex si ROM/119/50.DAT --hd
xi ui tex si ROM/119/50.DAT --hd-only ex1us

# Rebuild the sprite-geometry sheet from PRISTINE (retail) DATs
xi ui gen-sheet
xi ui gen-sheet ROM/119/50.DAT ROM/119/51.DAT ROM/280/15.DAT

xi title
xi title export                               # everything -> exports/title/data.json
xi title list                                 # title screen zones, cameras, weather
xi title weather --section 12
xi title timeline                             # shot list per segment (--json to save)
xi title set-zone 12 115                      # swap a segment's zone
xi title aim 12                               # re-aim a segment's cameras into its new zone
xi title swap-sections 4 12                   # exchange two whole segments (see docs/HANDOVER.md §1.6)
xi title camera export                        # -> exports/title/camera.json (exact round trip)
xi title camera import                        # <- exports/title/camera.json
xi title import exports/title/data.json       # cameras (+ --timing) back from the full export

# Title UI DAT (ROM/119/50): menus, sprites, wardrobe badges
xi title menu ROM/119/50.DAT                  # list / move / size / nav the loby UiMenus
xi title sprite ROM/119/50.DAT --list-owners  # 0x31 sprites: dest quads + src rects (--dx/--dy/--hide …)
xi title wardrobe ROM/119/50.DAT              # list the wardrobe 3-8 icons + digits
xi title wardrobe ROM/119/50.DAT --hide       # …and hide them (--no-icons / --no-digits)

xi ui layout
xi ui layout damv-pos
xi ui layout menu-pos
xi ui layout mnc2-pos
xi ui layout sel-pos

xi ui strings
xi ui strings list
xi ui strings search
xi ui strings import
xi ui strings export

xi ui spells
xi ui spells search
xi ui spells export
xi ui spells import
```

## UI Items

```text
xi ui items
xi ui items search
xi ui items export

xi ui items icon
xi ui items icon export
xi ui items icon import

xi ui items general
xi ui items general search
xi ui items general export
xi ui items general json
xi ui items general import
xi ui items general inject
xi ui items general new

xi ui items consumable
xi ui items consumable search
xi ui items consumable export
xi ui items consumable json
xi ui items consumable import
xi ui items consumable inject
xi ui items consumable new

xi ui items puppet
xi ui items puppet search
xi ui items puppet export
xi ui items puppet json
xi ui items puppet import
xi ui items puppet inject
xi ui items puppet new

xi ui items armor
xi ui items armor search
xi ui items armor export
xi ui items armor json
xi ui items armor import
xi ui items armor inject
xi ui items armor new

xi ui items weapon
xi ui items weapon search
xi ui items weapon export
xi ui items weapon json
xi ui items weapon import
xi ui items weapon inject
xi ui items weapon new

xi ui items custom
xi ui items custom search
xi ui items custom export
xi ui items custom json
xi ui items custom import
xi ui items custom inject
xi ui items custom new

xi ui items mount
xi ui items mount search
xi ui items mount list
xi ui items mount export
xi ui items mount export-all
xi ui items mount inject
xi ui items mount import
```

## FTABLE

```text
xi ftable expand
xi ftable expand entity
xi ftable expand gear
xi ftable reset
xi ftable json
xi ftable list
xi ftable tables
xi ftable info
xi ftable lookup
xi ftable range-scan
xi ftable compare
xi ftable delete
xi ftable set
```

## Model Viewer Lists

Refreshes the JSON catalogues in `mv/lists` that the model viewer loads. Every
target is append-only — curated names, labels and groupings are never rewritten.

```text
xi mv update
xi mv update --only gear,music
xi mv update --only images,npcs --dry-run
xi mv update --only file-ids
xi mv update --only zone-names          # curated mog-house names onto zones.json

xi mv database                          # item + d_msg tables -> mv/db/<table>.<lang>.json
xi mv database --only armor,weapons --lang en
```

Full reference: [docs/mv/README.md](docs/mv/README.md).

| Target | Source | What it adds |
|---|---|---|
| `gear` | FFXiMain race tables → FTABLE | missing gear model ids per race/slot (+ fishing rods as `rod: true` Ranged rows) |
| `gear-sets` | `gear_sets.json` + existing labels | `set` on each gear row (content set) |
| `gear-labels` | `(JOB Set)` label suffix | rewrites to `JOB - Name` |
| `music` | `sound*/win/music/data` | unnamed `music*.bgw` |
| `sfx` | `sound*/win/se` | unnamed `se*` folders and `.spw` |
| `zone-music` | `zone_settings.sql` | full rebuild of the zone → BGM map |
| `effects` | spell / ability / weapon-skill animation → file_id | missing VFX DATs |
| `images` | DAT section scan (textures only) | missing map, UI and cutscene art |
| `npcs` | modelid → file_id → DAT, named from `mob_pools` / `npc_list` | missing entity models |
| `npc-anims` | `Directory (0x01)` sections in the model DAT | `anims` packs on trusts / multi-form monsters that borrow clips from other DATs |
| `zone-names` | `MOG_HOUSE_NAMES` in `xi.zone.xi_list` | hand-verified mog-house names on `zones.json` rows |
| `file-ids` | reverse FTABLE/VTABLE | `fileId` on every row in every list |

VFX file_id bands (`offset + animation`): spells `2800`, job abilities `4412`,
weapon skills `4912`. `mob_skills` and `item_usable` animations are *not* file
ids — they index the caster's own motion set.

`gear-sets` writes a single `set` field holding every bucket: Artifact, Relic
and Empyrean (read off the existing `(JOB Set)` label suffix), `Ebur / Furia /
Ebon` (one merged bucket, matched by name prefix), and the content sets Prime
Weapons, Aeonic Weapons, Mythic Weapons, Abjuration and Limbus (matched by name
against `src/xi/mv/gear_sets.json`). Omen has no bucket on purpose — see the
`_comment` in that file.

`gear-labels` then rewrites `Wizard's Coat (BLM Artifact)` to
`BLM - Wizard's Coat`; the set is preserved in `set` first, so nothing is lost.
Only brackets starting with a real job code are touched — `(119 AG)`,
`(Stage 5)`, `(SU5)` and friends are left alone — and a job code paired with an
unknown set keyword is reported rather than renamed, since that is a typo in the
label rather than a set we lack.

## Batch

```text
xi batch zone_object_list_dump
xi batch zone_fbx
xi batch zone_asset_icons
xi batch pack_sprites
xi batch audio_music
xi batch audio_sfx
xi batch dat_header_dump
```

## Run (script of commands)

```text
xi run FILE                 # run each `xi …` line in FILE; skips blanks, `#` comments, ``` fences
xi run FILE --dry-run       # print the expanded commands only
xi run FILE -k              # keep going past a failing line
xi run FILE --start 12      # resume from line 12 (printed when a run stops)
```

`set NAME=value` lines define `%NAME%` (or `${NAME}`) for later lines, so a pasted
batch-style script such as `docs/title/custom_title_screen.md` runs as-is. Lines may start
with `xi`, `uv run xi` or the bare group; a trailing `\` or `^` continues onto the next line.

## Events

```text
xi event explain 243 Laityn                   # annotated disassembly of an NPC's events (--event N, --list)
xi event decompile 252 0x010FC08F --event 9506 -o oseem.json --check   # retail -> xi.cutscene.v1 JSON, prove the round trip
xi event sweep 243 --check --jobs 8           # decompile (+ recompile/compare) every event of a zone (--summary sweep.tsv)
xi event survey --op 0x71 --sub 0x12          # every use of an opcode across all zones
xi event lint 243 0x010F3075 --event 10196    # pre-flight checks before compiling
xi event npc list 243                         # zone entity-name table: ids, gaps, next free id
xi event npc add 243 "Name" --gap 10          # register a name for a new NPC id

xi event cutscene
xi event cutscene export
xi event cutscene import
xi event cutscene compile my_event.json       # JSON -> event/dialog DATs (finds the zone's DATs itself)

xi event dialogue
xi event dialogue actors
xi event dialogue new
xi event dialogue export
xi event dialogue search
xi event dialogue info
xi event dialogue edit
xi event dialogue reset
```

## Client DLLs (POL1) / Launcher / Utils

POL1-packed modules. Docs: `docs/reference/dll.md`, `ffximain.md`, `polcore.md`, `app.md`.

```text
xi dll list
xi dll ffximain unpack
xi dll ffximain pack
xi dll ffximain text-dump
xi dll ffximain gear-groups
xi dll ffximain gear-patch
xi dll ffximain crashdump
xi dll ffximain crashdump --list
xi dll polcore unpack
xi dll polcore pack
xi dll app unpack
xi dll app pack

# examples
xi dll polcore unpack --dll PATH --output misc/polcore_unpacked.dll
xi dll ffximain pack --unpacked misc/FFXiMain_unpacked.dll --template PATH
xi dll ffximain crashdump
xi dll ffximain crashdump path/to/pol.exe.1234.dmp

xi launcher ui-themes

xi utils dds2png
xi utils png2dds
```

## Misc / Server

```text
xi misc scan
xi misc trace
xi misc preview
xi misc stage
xi misc orphans
xi misc navmesh-prep

xi server db
xi server status
xi server npc-snapshot      # bake the offline npc_list fallback the editor uses without a DB
```

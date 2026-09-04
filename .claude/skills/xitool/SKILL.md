---
name: xitool
description: How to use the `xi` CLI in this repo (uv run xi …) for FFXI DAT modding — exporting/importing meshes, animations, gear, zones, objects, FX, audio, UI textures, events/dialogue, FTABLE/model-id work and DAT packaging. Use whenever a task mentions xi-tools, a `xi <group> <cmd>` command, a ROM/x/y DAT, FTABLE/VTABLE/file_id/modelid, or a coordinate/axis/scale/facing problem with an exported or imported model. Also explains where in docs/ and src/xi/ to look before answering.
---

# xi-tools CLI skill

`xi` is a Click CLI (`src/xi/xi_cli.py`) that reads and writes FFXI DAT files **in place**
under `FFXI_DIR`. This skill tells you (1) the quirks of FFXI data that trip up every
newcomer, (2) the common commands, and (3) where the authoritative information lives so
you look it up instead of guessing. Binary layouts are documented; never invent one.

## 1. Running it

```bash
uv run xi --help                 # works with NO game install (help is exempt)
uv run xi <group> --help
uv run xi <group> <cmd> --help   # always check flags here first — docs can lag the code
xi …                             # same, after `uv tool install -e .`
```

- **Every non-help command aborts unless `FFXI_DIR` points at a real install**
  (`_require_ffxi_dir` in `xi_cli.py`; `xi bridge` is the only other exemption). In a
  sandbox without the game you can only run `--help`, read code, and read docs.
- Config comes from `.env` (`XI_ENV_FILE`, then repo root, then cwd; real env vars win).
  Keys: `FFXI_DIR` (required), `FFXI_PIVOT_DIR`, `FFXI_HD_DIR`, `BLENDER_PATH`,
  `CUSTOM_FTABLE` (default `ROM10`), `XI_SERVER_DIR`, `XI_NAVMESH_DIR`, `XI_DB_*`,
  `TEXCONV_PATH`, `XI_VGMSTREAM`. Full list with comments: `.env.sample`.
- **DAT arguments are ROM-relative**: `ROM/1/41` (`.DAT` optional) resolves against
  `FFXI_DIR`; `ROM10/2/0.DAT` addresses the custom ROM. Absolute paths also work.
- **Outputs go to `exports/<area>/<rom path>/<stem>/`**, e.g. `exports/mesh/rom/351/102/`,
  `exports/zone/rom/1/41/`, `exports/ui/119/50/`, `exports/anim/rom/…/<stem>_idl/`.
  Most `import` commands find the matching export automatically when you omit the model.
  `exports/` is the user's master art — never overwrite it with test output.
- Some UI/title commands take `--ffxi DIR` to target a pivot/override tree for one call.
- Blender (`BLENDER_PATH`) is needed only for `--fbx` and FBX→GLB conversion.

## 2. FFXI quirks you must know

### 2.1 Coordinate system (axis, handedness, units)

| Fact | Detail |
|---|---|
| **Y is DOWN** | FFXI world space has `up = (0, -1, 0)`. A *smaller* Y is *higher*. To raise an FX or camera, make `y` **more negative**. Fountain jets in Lower Jeuno sit at `y = -7.2`. |
| Handedness | Left-handed with Y stored flipped (equivalently: right-handed with inverted Y). X = east, Z = north. |
| Units | 1 unit = 1 yalm ≈ 1 m. No global scale. A city zone spans **hundreds** of units; an entity skeleton is **~5 units** tall. |
| Rotations | Placement records: 3 floats, **radians**, applied `rotateZYX`. FX `u8` rotations: `2π·n/255`. Event headings (`0xBA`): `n × 2π/4096`. An upright prop is often `--rot 3.142 1.044 3.142`. |
| Ashita → DAT | `DAT(x, y, z) = Ashita(X, Z, Y)`. Ashita's Z is vertical. The bundled addon `/xi pos` prints a DAT-ready `--pos`. |
| Event `0xBA` positions | Operand order is **X, Z, Y** (signed int ÷ 1000). |
| Winding | DAT triangles are **clockwise-front** (Direct3D). glTF is CCW; the exporters flip it. Symptom of getting it wrong: dark / inside-out surfaces. |
| Normals | The retail client does **not** renormalize. Non-unit normals render **fullbright** regardless of shading. Importers force unit normals; a hand-built mesh may not. |

### 2.2 The `ffxi_root_correction` node (how exports become upright)

Every GLB export parents geometry under a node named **`ffxi_root_correction`** so DCC
tools show it upright and un-mirrored. The transform differs by area:

| Export | Correction | Net mapping FFXI → glTF |
|---|---|---|
| `xi mesh export`, `xi gear export`, `xi anim export` | 180° about X | `(x, y, z) → (x, -y, -z)` |
| `xi zone export`, `xi object export`, editor | 180° about X **and** scale `(-1, 1, -1)` | `(x, y, z) → (-x, -y, z)` |

Rules that follow:
- **Model relative to that node and leave it alone.** Importers recover transforms
  *relative to the actual correction node* (`inv(C)·W`), so the round trip is exact even
  if Blender re-expresses the node. Placements whose node is moved **out of** the group
  are skipped with a warning.
- **Never hand-write an axis flip.** Writing `(-x, y, -z)` instead of `(-x, -y, z)` has
  produced three different-looking bugs (mirrored east/west, flying backwards). Measure,
  don't infer — see `docs/HANDOVER.md` "Things that will bite you".
- `--raw` on `zone export` omits the node (view-only; not re-importable).

### 2.3 Scale and DCC tool differences

- **Blender GLB is the verified path.** Cinema 4D's direct glTF export bakes a 90° axis
  frame and a 100× vertex / 0.01 node scale. Route **C4D → FBX → Blender → GLB**.
  `xi zone import` is **GLB-only** (FBX rejected); `xi gear import` is GLB-only too.
- **Entity/gear mesh import auto-aligns scale** to the skeleton via the bones' inverse-bind
  matrices, so unit mismatches self-correct. `--scale N` is a manual multiplier on top;
  `--rotate-y DEG` fixes facing (e.g. `-90`). Facing comes from how you built the model,
  not from the tool's transform.
- **Zone collision blockers must be in yalms** (same range as `<stem>.collision.obj`,
  hundreds for Jeuno). A ×100 or ÷100 export triggers a warning that suggests the
  `--scale` factor: `xi zone import ROM/1/41 --add-collision blocker.obj --scale 0.1`.
- Zone import auto-calibrates a C4D axis frame and recovers baked uniform scale for
  *placements*; for *mesh-merge* only the Blender path is verified.
- Mirror (negative-scale) placements: DCCs drop the reflection into geometry, so xi only
  rewrites records you actually changed and keeps base scale **signs**. Edit mirrored
  objects via GLB, not FBX (Blender's FBX exporter kicks them to the scene root).

### 2.4 Colour, alpha, textures

- **Alpha is half-scale**: `0x80` = fully opaque. Shaders compute `4·vColor.a·tex.a`.
  Exporters bake the ×2 into PNGs by default (`--alpha-scale 2.0`; pass `1.0` for raw).
- **Vertex colour modulates the live light**, it is not the final colour. Neutral is
  `0x80` per channel. `--shade S` scales it (`1.0` neutral, lower = darker); `--ao` bakes
  ambient occlusion (off by default).
- Foliage alpha-test is keyed on the **mesh name's first byte** (`_` or `#`), not a flag.
  Renamers must preserve it or leaves render opaque/black. `0x2000` = double-sided.
- Textures are DXT1/DXT3 (entity textures DXT3). **DXT5 renders flat grey in UI DATs.**
  UI PNGs are resampled to a canonical size sheet unless you pass `--hd`/`--no-resize`.
- Section offsets in headers are stored **÷2**; every block starts on an even byte.

### 2.5 File addressing: FTABLE / VTABLE / file_id / modelid

- The client never opens a path directly. `file_id` → `FTABLE.DAT` (u16: `subdir<<7 | index`)
  + `VTABLE.DAT` (u8: ROM number) → `ROM<n>/<subdir>/<index>.DAT`. Overlays (pivot, HD)
  shadow files at the same ROM path; a pack that ships its own tables **hides** base-table
  registrations (a top crash cause).
- **Entity modelid → file_id** is a 4-range formula; custom content always uses
  `file_id = modelid + 98239` (the 3500+ range). Retail tops out at modelid ~11,241.
- **Gear** is per `(race, slot)` group tables from `FFXiMain.dll` (`RACE_TABLES` in
  `src/xi/gear/xi_core.py`); never apply a flat offset. Custom gear gets windowed
  file_ids from `128,240` upward.
- **Recommended custom ranges**: entities **modelid 15,000+** (ceiling 30,000), gear
  **modelid 3,000+** per race/slot (ceiling 4,095), mounts **id 39–62** (menu shows 64
  max, `file_id = 0x019131 + id`), custom zones **id ≥ 400**.
- Run **`xi ftable expand` once** per install before placing custom content. It only
  appends empty slots; nothing is renumbered. `xi ftable info` shows ranges and usage.
- Custom DATs live in the **`ROM10`** namespace (`CUSTOM_FTABLE`), except camera-scene
  DATs for cutscenes, which must be under base `ROM/490/…` with VTABLE=1 (see
  `docs/common_crashes.md`).
- Per-zone DATs resolve from the zone id: model `0x64 + zone`, event `5820 + zone`,
  dialog `6420 + zone`, plus NPC list; expansion zones (id ≥ 0x100) use other bases.
  `xi zone json` prints the live table; `docs/events/README.md` has the formulas.

### 2.6 Edits are in place; `.base` is the undo

- The first edit of any DAT or table writes a pristine **`<file>.base`** beside it.
  Reset commands restore from it: `xi zone reset`, `xi ftable reset` (reverts **all**
  custom registrations), `xi event dialogue reset`; `xi dats undo` reverts a whole project build.
  Never break this contract when changing code.
- **Stacking differs per command** — check the doc before assuming:
  - Rebuild from `.base` every run (idempotent): `mesh import`, `zone import` (GLB path),
    `anim import` unless `--no-base`.
  - Layer on the current file (re-running stacks): `zone import --add-collision` alone,
    `event dialogue edit`, `fx set/copy/delete`, `object import/clone`.
- Prefer `--dry-run` where offered (`ftable expand`, `dats build`, `zone import-json`,
  `zone reset`, `mount import`, dialogue edits) before writing.
- `xi dats build` writes into `FFXI_DIR` and then syncs the custom region of the pivot
  tables (`FFXI_PIVOT_DIR`) so sizes match — a table size mismatch crashes the client.

### 2.7 Engine limits that decide "loads in a viewer but crashes in game"

- `0x2A` mesh header must be **64 bytes**, `flags1 = 1`, `numJoints ≥ 1`.
- **128 triangles per draw call**; vertex data per section capped by a u16; importers
  chunk and split automatically. Budget: ≤ ~2k verts, ≤ ~20 draws, texture ≤ 1024².
- Two-joint vertices store **pre-weighted positions** (`p_i = w_i·v_i`) with unweighted
  normals; the assembly is a sum, not a blend. Details: `docs/mesh/format.md`.
- Adding a zone object requires registering its index in **four** `0x1C` structures
  (record + space tree, leaf AABB, collision transform, culling tables) — `object import`
  / `clone` do all four. Missing the culling table = visible only from some spots.
- Cutscene camera scenes: scene ref `p` must be **300–599** (file_id 56,941–57,240);
  ≥ 600 crashes. Look-at ~2 m from eye; multi-point routes use interp mode 4.
- A cutscene can only `SetAction` a **`0x07` schedule routine**, never a raw clip — new
  animation clips need `xi anim schedule add`.

## 3. Where to look (teach yourself before answering)

Resolution order for any question about a command:

1. **`QUICKY.md`** — the public command surface. If a command isn't listed there it is
   hidden, renamed or gone. Known renames you'll meet in older docs: `xi gui zone` → the
   editor now talks to **`xi bridge`**; `xi zone object …` → **`xi object …`**;
   `xi entity anim` → **`xi anim`**; `xi audio music/sfx list` → `xi audio json`.
2. **`uv run xi <group> <cmd> --help`** — the real flags.
3. **`docs/<area>/<cmd>.md`** — behaviour, examples, verified-in-game notes.
4. **`src/xi/<area>/xi_<cmd>.py`** — the code. Groups map to packages:

| Command group | Package | Docs |
|---|---|---|
| `xi mesh` | `src/xi/entity/mesh/` | `docs/mesh/{export,import,format}.md` |
| `xi anim` | `src/xi/entity/anim/` | `docs/anim/quickref.md` first, then `export/import/schedule/emotes/format.md` |
| `xi entity`, `xi model` | `src/xi/entity/` | `docs/entity/`, `docs/model/{json,free}.md` |
| `xi gear` | `src/xi/gear/` | `docs/gear/*.md`, `docs/reference/model-file-ids.md` |
| `xi mount` | `src/xi/mount/` | `docs/mounts/{README,mechanism,data}.md` |
| `xi zone` | `src/xi/zone/` | `docs/zone/README.md` → `format/export/import/import-json/collision/navmesh/templates/zones.md` |
| `xi object` | `src/xi/zone/xi_object.py`, `xi_zonedef.py` | `docs/object/*.md` |
| `xi fx` | `src/xi/fx/` | `docs/fx/README.md`, `effects.md`, `effect_system.md` |
| `xi audio` | `src/xi/audio/` | `docs/audio/{README,format,refs}.md` |
| `xi tex`, `xi utils` | `src/xi/tex/`, `src/xi/utils/` | `docs/dats/tex.md`, `docs/utils/texture.md` |
| `xi ui` | `src/xi/ui/` (+ `items/`, `strings/`, `spells/`) | `docs/ui/*.md`, `docs/dats/ROM_*.md`, `docs/dat_index.md` |
| `xi title` | `src/xi/title/` | `docs/title/README.md`, `docs/dats/ROM_0_23.md`, `docs/HANDOVER.md` |
| `xi event` | `src/xi/event/`, `src/xi/dialog/` | `docs/events/README.md` → `authoring/dialogue/format/opcodes/cutscenes.md`, `docs/dialog/*.md`, `docs/cutscene_authoring.md` |
| `xi ftable` | `src/xi/ftable/` | `docs/ftable/*.md`, `docs/reference/model-file-ids.md` |
| `xi dats` | `src/xi/dats/` | `docs/dats/README.md`, `schema/*.json` |
| `xi dll` | `src/xi/dll/`, `src/xi/ffximain/` | `docs/dll/README.md`, `docs/ffximain/*.md` |
| `xi batch`, `xi misc`, `xi server`, `xi bridge`, `xi mv` | `src/xi/{batch,misc,server,zone/xi_bridge.py,mv}/` | `QUICKY.md`, `docs/zone/navmesh.md` |

Cross-cutting references:
- `docs/README.md` — index of every format doc with one-line summaries.
- `docs/reference/dat_sections.md` — what each section type (`0x05`, `0x1C`, `0x20`,
  `0x2A`, `0x2E`, `0x36`, `0x3D`, …) is.
- `docs/dat_index.md`, `docs/dats.md`, `docs/reference/named-dats.md` — which DAT holds what.
- `docs/common_crashes.md` — **read first when "the client closes after a publish"**.
- `docs/HANDOVER.md` — hard-won lessons (Y-down, correction node, UI alpha, title screen).
- `schema/*.json` — JSON shapes for exports/manifests (`mesh.json`, `zone.json`, `package.json`, …).
- `src/xi/xi_config.py` — env loading, path resolution, the `.base`/redirect rules.
- `src/xi/common/xi_section.py` — DAT section container parsing shared by every area.

Layout conventions inside the code: one Click command per `xi_<verb>.py`, shared parsing in
`xi_core.py` per area, minimal comments, no drive-by refactors. Hidden aliases are marked
`.hidden = True` in `xi_cli.py`.

## 4. Common actions (copy-paste)

All examples assume `uv run` prefix or an installed `xi`.

### Inspect / find things
```bash
xi zone json                          # every zone id → name → DAT path
xi zone search jeuno                  # fuzzy search
xi model search <name>                # registered model ids, DAT paths
xi model json --free                  # next free custom entity modelid + file_id
xi ftable info                        # custom ranges, usage, recommended starts
xi ftable lookup --file-id 1608       # file_id → DAT path (+ --table 10 for ROM10)
xi ftable lookup --modelid 15000
xi gear search <query>                # race/slot/model/file
xi gear json                          # all gear entries
xi mount json                         # mount ids 0–63 (--all for 0–255)
xi audio search <name> / xi audio json --type music|sfx
xi fx json ROM/1/41                   # every 0x05 effect + decoded params
xi object json ROM/1/41               # every placement
xi anim list ROM/5/3                  # tracks: name, frames, joints
xi tex json ROM/1/41 / xi ui tex list
xi audio refs ROM/1/41                # which sounds a DAT references
xi audio scan [--sound 5048]          # every DAT: where each sound is used + what that DAT is
```

### Entity mesh round trip
```bash
xi mesh export ROM/351/102 --split-tex --fbx      # → exports/mesh/rom/351/102/102.glb (+fbx, pngs)
xi mesh export ROM/5/3 --anim idl --frame 0       # posed export
xi mesh import ROM/351/102                        # auto-finds the export; rebuilds from .base
xi mesh import ROM/351/102 model.glb --rotate-y -90 --scale 1.0
```

### Animation
```bash
xi anim export ROM/5/3 --anim idl                 # glTF + textures
xi anim import ROM/5/3 idl                        # replace
xi anim import ROM/5/3 tlk yap.gltf --static-base # new track, hold undriven bones
xi anim import ROM/5/3 --anim idl --add tlk --layer yap.gltf --bones bone0007,bone0009
xi anim schedule add ROM/5/3 --clip tlk0          # make it usable from cutscenes
```
Animate with bone **rotations** only; translation/scale are ignored on import.

### Gear
```bash
xi gear export HumeMale body 0                    # races: HumeMale HumeFemale ElvaanMale ElvaanFemale TaruMale TaruFemale Mithra Galka
xi gear import HumeMale body 0 edited.glb         # slots: face head body hands legs feet main sub ranged
xi gear character …                               # assemble a character from a look blob
```

### Zone / objects / FX
```bash
xi zone export ROM/1/41 [--fbx] [--no-sky --no-vfx] [--objects] [--collision] [--base]
xi zone import ROM/1/41 [edited.glb] [--prune] [--add-collision blocker.obj [--scale 0.1]]
xi zone import-json zone-changes.json [--dry-run] [--pivot]   # editor change-set
xi zone reset ROM/1/41
xi object export ROM/1/41 gaitou01
xi object import ROM/1/41 lamp.glb --name lamp --pos -8.8 0 -7.7 --rot 3.142 1.044 3.142
xi object clone / replace / delete / set-placement / swap-placement …
xi fx set ROM/1/41 tki --color 00FF00 --scale-mul 4 --range 100 500
xi fx copy ROM/1/41 lb09 --from ROM/0/60 --at-pos -20.7 -2 5.2 --name fir0
xi zone navmesh ROM/1/41 && xi zone navmesh-info exports/zone/rom/1/41/41.nav
xi zone new --template <6-hex id> --name "My Zone"   # custom zones need a template bundle
```

### Audio
```bash
xi audio export --type music music10              # → exports/audio/music/*.wav
xi audio decode path/to/se002060.spw --out out/
xi audio import file.wav                          # → custom .spw (sfx only)
xi audio install file.wav                         # place under sound/win/se/…
```

### UI textures, strings, items
```bash
xi ui tex sx ROM/119/50.DAT                       # export → exports/ui/119/50/*.png
xi ui tex si ROM/119/50.DAT [--hd] [--no-resize] [--repair-rects] [--ffxi DIR]
xi ui strings search "text" / xi ui strings export|import
xi ui items armor search <name> / export / json / import / inject / new
xi utils dds2png in.dds out.png / xi utils png2dds in.png out.dds
```

### Events / dialogue
```bash
xi event dialogue actors 245                      # NPC ids in a zone
xi event dialogue search ROM/25/39.DAT "words"    # find an entry index
xi event dialogue edit ROM/25/39.DAT --index 4 --text "Hello {player}!\nLine two.\v"
xi event dialogue new 245 --json lines.json --actor 0x010F5022   # prints the event id + server Lua
xi event cutscene export|import|compile …
```

### Custom content packaging (the reproducible path)
```bash
xi ftable expand                                  # once per install
xi dats new                                       # wizard: place prebuilt DATs at new ids
xi dats prepare exports/mesh/rom/351/102/102_schema.json --project my_mob --replace
xi dats build my_mob [--dry-run]                  # writes DATs + patches tables, syncs pivot
xi dats changelog --project my_mob / xi dats package / xi dats release / xi dats undo
```

### Client DLLs
```bash
xi dll list
xi dll ffximain unpack --output FFXiMain_unpacked.dll
xi dll ffximain pack --unpacked … --template PATH
xi dll ffximain crashdump [dump.dmp]              # decode a minidump after a crash
```

## 5. Working rules for this repo

- Prefer reading `docs/` and the existing module over inventing a DAT layout. Formats are
  cross-checked against xim (Kotlin client port); when a doc and the code disagree,
  trust bytes verified in-game and say which one you followed.
- Keep the `.base` backup contract and the in-place-write model intact.
- Don't commit `.env`, anything under `exports/`, `*.DAT.base`, or game trees.
- Match style: Click commands, minimal comments, no refactors outside the task.
- When a user reports "it loads in AltanaView but crashes in game", go to the engine
  limits in §2.7 and `docs/common_crashes.md` before touching the writer.
- Don't tell users to back up or restore DATs unprompted — the reset commands exist and
  the maintainers find restore chatter obstructive (`docs/HANDOVER.md`).

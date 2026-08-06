# xi dats

`xi dats` builds reproducible DAT package trees from a JSON manifest. It is the
new home for **new content** and distributable patches.

Use domain commands for existing DAT edits:

```bash
uv run xi gear export ROM/33/17
uv run xi gear import ROM/33/17
uv run xi zone import ROM/1/41 edited.glb
uv run xi anim import ROM/37/13 poi
```

Use `xi dats` when the result should be rebuilt from Git and published:

```bash
uv run xi dats prepare workspaces/foo/zone-changes.json projects/update.json --target ROM10/2/0.DAT
uv run xi dats build projects/update.json --dry-run
uv run xi dats build projects/update.json
```

---

## Quick start: `xi dats new` (interactive wizard)

When you **already have the built `.DAT` files** and just want to place them at new
model ids — no `mesh export` / GLB rebuild — run the wizard:

```bash
uv run xi dats new
```

It walks you through, in order:

1. **Target check.** Reports each live target (`FFXI_DIR`, and `FFXI_PIVOT_DIR` if
   distinct) and whether its `FTABLE` is **expanded** for each content type (with sizes),
   warning about any that aren't:

   ```text
   Pivot overlay (FFXI_PIVOT_DIR): …\DATs\<your-overlay>
     FTABLE: 423,152 entries
       ✓ Mounts: file_ids up to 102,768
       ✓ Entity models: custom band from file_id 113,239
       ✓ Gear: window 4095 → needs 423,152 file_ids
   ```

   Mounts fit inside the retail range; **entity** and **gear** need the tables grown
   first with `xi ftable expand entity` / `xi ftable expand gear` on that install.

2. **Project name** (type-ahead autocomplete against existing `projects/*.json` names) → writes/updates
   `projects/<slug>.json`. Picking an **existing** project **preloads defaults** from its action of the
   same type — slot, model id, source folder (saved as `source_dir`), destination — so re-running just
   tweaks it, and gear re-uses each slot's destination block (overwrite in place).

3. **Content type** — `Gear`, `Mounts`, `Entity (NPC / Monster / Object)`, or
   `NPC (costume: race + gear + weapons)`.

4. **Type-specific questions** (see below), then it writes the manifest action and offers
   to build (with a dry-run preview first).

### Gear

Point it at a **folder** of gear DATs. It auto-detects each file's **race** and **slot**
and confirms each is a valid gear mesh — filename first (an explicit rename always wins),
then the codes embedded in the DAT's own section names:

- **Race**: filename prefix (`hm`, `hf`, `em`, `ef`, `tm`, `tf`, `m`, `g`;
  case-insensitive), else the race code inside the DAT (`1em_…` model / `em_…` texture
  headers).
- **Slot**: a slot word anywhere in the filename (`Loxley Hands.DAT`, `helm`, `boots`,
  …), else the slot digit FFXI puts ahead of the race code in the 0x01 model header
  (`1em_` = head). Weapons don't carry the digit, so main/sub/ranged fall back to the
  slot prompt.

```text
Found 5 .DAT Files:
- Elvaan Male - hands - 100 - Loxley Hands.DAT
- Elvaan Male - feet - 110 - Loxley Feet.DAT
  ...
>> Are these correct? [Y/n]
```

A folder can therefore hold a **full set** (one race, many slots), a per-race pack
(one slot, many races), or any mix — the wizard writes **one manifest action per slot**
(`gear.<project>.<slot>`) and the build places them all in one pass. If no slot is
detectable anywhere (e.g. weapons), it asks once, like before.

- A bare **`t`/`taru`** DAT is bound to **both** Taru genders automatically (they share
  one skeleton), each getting its own destination file + file_id.
- You then pick a destination ROM folder (e.g. `rom10/20` — filenames are auto-numbered
  into the first free consecutive block, one block per slot) and a single model id
  (recommend `3000+`) shared by the whole set — each slot has its own file_id window,
  so a set can sit at the same id in every slot.
- **Requires** `xi ftable expand gear` first (per-race windowed file_ids).

### Mounts

Source DAT + destination DAT + mount id (recommend `39–62`, menu-visible; id 63 is
occupied) + optional key item (EN/JP name & description). Reuses the mount record
machinery (model + name/help + key-item d_msg + server snippet).

### Entity

Source DAT + destination DAT + model id (recommend `15000–30000`; dats default often
starts mid-band around `20000` as a buffer — floor is `MODEL_SAFE_START` 15000;
`file_id = model_id + 98239`).

### NPC (costume — bake from race + gear + weapons)

Unlike the other types, this one has no prebuilt DAT to place — it **bakes one**. You pick
a **race + gender**, a **face**, and (optionally) a **DAT per armour slot** (head / body /
hands / legs / feet — skip a slot to use the race's naked base part) and **main / sub weapon
DATs**. The wizard flattens all of that into a single self-contained entity DAT at
`projects/custom/<project>.dat` — the same shape as retail "costume" NPCs such as `ROM/261/56`
(race skeleton `0x29` + every gear mesh `0x2A` + textures `0x20` + `wlk/idl/run` locomotion +
`mou4/eye3` face anims + `Info`). It then falls into the **Entity** flow (destination DAT +
model id) with that baked DAT as the source, so `dats build` places it like any other entity.

Pieces are pulled from the game's own data — race skeleton + part-0 locomotion + face anims
from the race-config DAT, part-1/2 locomotion via the `FFXiMain.dll` motion tables, face/gear
meshes via the gear tables (model id `0` = naked base). The **weapon mesh** is included;
weapon-typed **battle / weapon-skill** motions are a planned follow-up (the weapon's
`weaponAnimationType` is already read and recorded). Registering the baked model as a live
zone NPC is separate — use the editor's **Custom NPCs** browser or the `custom-npc` registry.
See [../entity/npc-look.md](../entity/npc-look.md).

---

## Building (`xi dats build`)

A build writes DATs and patches their file_ids **directly into the base install
(`FFXI_DIR`)**. There is no multi-target `--target pivot,hd` switch — the base install is
the only place custom gear/entity file_ids can register (XIPivot cannot overlay the root
`FTABLE`). After a successful pack build, if `FFXI_PIVOT_DIR` is set, the custom region of
the pivot's tables is updated via `sync_pivot_from_base()` so sizes stay uniform and the
new file_ids resolve through the overlay.

```bash
uv run xi dats build --project gyokko_mask            # into FFXI_DIR, then sync pivot tables
uv run xi dats build --project gyokko_mask --dry-run  # preview only, writes nothing
uv run xi dats changelog --project gyokko_mask        # table of recorded results
```

- The base install's `FTABLE`/`VTABLE` **must already exist and be expanded** for the custom
  models you're placing — run `xi ftable expand entity` / `xi ftable expand gear` on
  that install first. The wizard's opening step reports expansion status.
- Each table is backed up once to `<name>.base` before the first patch (recoverable via
  `xi ftable reset`).
- **`--dry-run`** prints the full per-DAT placement plan (every race for gear) and any
  file_id collisions, without touching disk. There is **no** separate `xi dats plan`
  command — use `--dry-run` or `xi dats changelog`.

The free-block finder for gear scans both `FFXI_DIR` and `FFXI_PIVOT_DIR`, so deleting a
project's DATs from the live install frees those slots again.

---

## Packaging for distribution (`xi dats package`)

```bash
uv run xi dats package gyokko_mask               # read from FFXI_DIR (default --from dir)
uv run xi dats package gyokko_mask --from pivot  # or pivot | hd
```

With no project argument, lists `projects/*.json` to pick from. Reads from **one** source
(`--from dir|pivot|hd`, default `dir` — where builds land). Zips everything needed to run
one project as an overlay:

- every DAT the project's actions placed (from each action's inline `result` — all
  per-race DATs for gear), plus the mount name/help/key-item string DATs for mount actions,
- the full `FTABLE`/`VTABLE` set (so the new file_ids resolve).

Files are laid out ROM-relative inside the zip (XIPivot-ready). Build the project first.

## Package layout vs live target build

Two different trees — don't confuse them:

| Path | Role |
|---|---|
| **Live target** (`FFXI_DIR`) | Where `dats build` places mesh/entity/gear/mount DATs + table patches. Then `sync_pivot_from_base()` updates pivot overlay tables when configured. |
| `projects/resources/` | Source tree you commit: GLBs, PNGs, `zone-changes.json`, imported JSON, mount DATs, etc. |
| `projects/ffxi/` + `projects/ffxi-hd/` | **Zone actions only** — standard/HD package output trees (not the live gear/entity target). |
| `projects/packages/` | Zip output from `xi dats package`. |
| `projects/<project>.json` | Per-project manifests (`dats new` / `prepare`). |

```text
dats/
  update.json                 # default manifest (or projects/<project>.json)
  resources/                  # committed sources
  ffxi/                       # zone standard package tree
  ffxi-hd/                    # zone HD package tree
  packages/                   # distributable zips
  server/                     # emitted server snippets (e.g. mounts)
```

- **Results are recorded inline** on each action (`action["result"]` = model_id → file_id → DAT,
  per-race `placements` for gear), so the manifest is self-describing. `xi dats new` and
  `xi dats build` write it; re-running just overwrites the same key (no duplicates, no side
  `*_changelog.json` file).

## Manifest

`projects/update.json` follows [`schema/package.json`](../../schema/package.json).
Individual action schemas live in [`schema/`](../../schema/).

Minimal zone action:

```json
{
  "schema": "xi.dats.v1",
  "name": "ffxi-update",
  "version": 1,
  "roots": {
    "standard": "projects/ffxi",
    "hd": "projects/ffxi-hd",
    "resources": "projects/resources",
    "locks": "projects/locks.json"
  },
  "actions": [
    {
      "id": "zone.byakko_hideout",
      "type": "zone",
      "op": "update",
      "target": {"dat": "ROM10/2/0.DAT", "zone_id": 450},
      "resources": {"changes": "zone/byakko_hideout/zone-changes.json"},
      "options": {"apply_standard": true, "apply_hd": true}
    }
  ]
}
```

Minimal mount action source for `xi dats prepare workspaces/cyakko/mount.json`:

```json
{
  "id": "mount.cyakko",
  "type": "mount",
  "op": "inject",
  "target": {"mount_id": 50, "model_dat": "auto"},
  "resources": {"model_dat": "model.DAT"},
  "text": {"name_en": "Cyakko", "key_item_name_en": "Cyakko Companion"},
  "server": {"emit": true}
}
```

Minimal mesh action source for `xi dats prepare workspaces/crab/mesh.json`:

```json
{
  "id": "mesh.crab_custom",
  "type": "mesh",
  "op": "update",
  "target": {"dat": "ROM/128/79.DAT"},
  "resources": {"mesh": "crab.glb"}
}
```

For full action JSON, `prepare` preserves the action fields and copies referenced
resource files that live next to the source JSON into `projects/resources/<type>/<id>/`.

## Commands

| Command | What it does |
|---|---|
| `xi dats new` | **Interactive wizard** — place prebuilt DATs (gear/mount/entity) at new model ids and write a manifest action |
| `xi dats build [manifest]` | Build into the **base install** (`FFXI_DIR`), then `sync_pivot_from_base()` when a pivot is configured; `--dry-run` previews (no separate `plan` command) |
| `xi dats package <project>` | Zip the project's built DATs + F/V tables (`--from dir`/`pivot`/`hd`, default `dir`) into `projects/packages/<project>.zip` (ROM-relative, XIPivot-ready) |
| `xi dats release <project>` | Stage the project's DATs + full FTABLE/VTABLE set + patched `FFXiMain.dll` into `<release>\Game\FINAL FANTASY XI\…` (a launcher build folder). Prompts for the folder; `--to <path>`, `--no-dll` |
| `xi dats undo <project>` | Reverse a build: delete the placed DATs + clear their file_id entries, then remove the manifest (`--keep-json` keeps it) |
| `xi dats json [manifest]` | Print the normalized manifest JSON |
| `xi dats prepare <source> [manifest]` | Copy an exported JSON/change-set into `projects/resources` and add an action |
| `xi dats changelog [manifest]` | Table of each action's recorded inline `result` (model_id → file_id → DAT) |

> Note: `new`/`build` write mesh/entity/gear/mount DATs + table patches into **`FFXI_DIR`**
> (then sync pivot tables), while `zone` actions still build the `projects/ffxi` +
> `projects/ffxi-hd` package trees.

## Current builders

Verbatim-placement types (written by `xi dats new`, built into the live target):

- `gear`: places one prebuilt gear DAT per race at a windowed custom gear file_id
  (`gear.xi_inject.custom_fid`). Needs `xi ftable expand gear`.
- `entity`: places a prebuilt entity DAT at `file_id = model_id + 98239`. The **NPC costume**
  wizard also emits an `entity` action — it first *bakes* the DAT it places
  (`entity.xi_bake_npc`, source `projects/custom/<project>.dat`).
- `mount`: places the model DAT at the chosen path, writes EN/JP name/help + optional
  key-item d_msg overrides, registers the file_id, and emits a server snippet.

GLB-rebuild / package types:

- `mesh`: rebuilds an existing model DAT's geometry from an edited GLB into the target.
- `zone`: applies one `zone-changes.json` to the `projects/ffxi` standard tree and
  optionally the `projects/ffxi-hd` tree.

Other action schemas are present so package shape is stable, but their builders are
added incrementally.

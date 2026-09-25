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
uv run xi dats new            # builds into the base install (FFXI_DIR)
uv run xi dats new --pivot    # builds into FFXI_PIVOT_DIR instead
```

It walks you through, in order:

1. **Target check.** Reports the build target — the base install (`FFXI_DIR`), or
   `FFXI_PIVOT_DIR` with `--pivot` — and whether its `FTABLE` is **expanded** for each
   content type (with sizes), warning about any that aren't:

   ```text
   Pivot folder (FFXI_PIVOT_DIR): …\DATs\<your-overlay>
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

3. **Content type** — `Gear`, `Mounts`, `Entity (NPC / Monster / Object)`,
   `NPC (costume: race + gear + weapons)`, `Ability` (a recipe from the model
   viewer's Ability Mixer or `xi ability recipe`), or a `Spell` / `Command` menu
   record (a new spell or job-ability id with its name, help and stats).

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

### Ability (a composed job ability / spell / weapon skill)

Point it at a **mix file** (`*.mix.json`, formerly `*.recipe.json` — both are read;
[schema](../../schema/ability_recipe.json)) — the wizard lists the ones under
`exports/ability/` — then choose what to publish it
as (auto reads it off the recipe: a `ws:` motion lane is a weapon skill, a `spell:` one
a spell, else a job ability), the animation number (auto = the next free one) and the
ROM10 folder. The mix is copied to `projects/resources/ability/<slug>.mix.json` (a texture
it gives as a PNG file is inlined, so the copy stands alone; an older manifest's
`<slug>.recipe.json` still builds) and the build composes it, places the DAT(s) and
registers the file id(s); the number it took is recorded on the action. The action id
comes from the recipe's `name`, not the file name. Each publish also fills its own
folder, `projects/abilities/<slug>/` (`<slug>` is the action id's last part): the server
SQL, a copy of every DAT it placed at its ROM path, and `placements.json`
([schema](../../schema/ability_publish.json)) naming the file id of each, so the ability
can be handed on or packaged as one folder. The folder holds only the latest publish;
files you add to it — the model viewer's mix files among them — are kept. The
same action from arguments, with every parameter defaulted:

```bash
uv run xi dats prepare exports/ability/mixer/tiger_fury.mix.json --project tiger_fury --replace
uv run xi dats build tiger_fury --dry-run
```

`dats build` can also update the local server's row for it, place the client menu record
at the same id and write its Lua script — see
[ability server options](#ability-server-options---apply-db---menu-record---lua-stub).

Full detail: [../ability/mixer.md](../ability/mixer.md#publish).

### Spell / command menu record (a new spell or job-ability id)

Point it at a **definition** (`*.spell.json` / `*.command.json`,
[schema](../../schema/spell_definition.json), [schema](../../schema/command_definition.json)):
the retail record to clone (`like`), the names and help, and the fields that differ
(MP, cast/recast, element, learnable jobs and levels; level, TP, targets for commands).
Then choose the id — auto takes the highest free one above the retail band (spells
1024–4095, commands 2816–4095), top-down so retail can never reach it. The build grows
the `mgc_` / `comm` section of `ROM/118/114.DAT`, writes the record and the EN/JP
name/help blocks, records the id (and a spell's menu index) on the action and emits a
server row template to `projects/server/spells/` or `commands/`. A client without a
ceiling plugin such as cexislots ignores the new band, and `prepare`, the wizard and the
build say so with a ⚠ — see [../menu/records.md](../menu/records.md). Names hold 39
bytes (commands) or 99 (spells) and help 215; a longer text is refused before anything
is written. From arguments, with every parameter defaulted (the type is inferred from
the definition's `schema`):

```bash
uv run xi dats prepare exports/menu/testspell.spell.json --project testspell --replace
uv run xi dats build testspell --dry-run
uv run xi dats build testspell --pivot     # into FFXI_PIVOT_DIR's 114.DAT + name tables
```

Full detail: [../menu/records.md](../menu/records.md).

### Database records (edit or add items, key items, titles, …)

Pick a table (Armor, Weapons, Key items, …), give a record id, see what it holds now, then
type the changes one per line — `level=50`, `jobs=WAR,PLD`, `description~HP+15=>HP+30`,
`mod.HP=30` for the proposed SQL. An empty item slot asks which record to copy into it
(`like`). The edits go into `database.<project>`, merged by table and id with what an
earlier run wrote. Non-interactive: `xi dats prepare edits.json --project P [--merge]` with
a list of edits. See [../database/README.md](../database/README.md).

### Table edits (`ui` action)

A `ui` action carries a retail table's edits at the level of the table's own bytes: the
build reads the table as the target sees it, applies them, and writes the whole table back
at its own ROM path. Untouched entries are carried over byte for byte, so a build is
reproducible from the edits plus the install and a repository need only hold the edits.

It sits beside the [`database`](#database-records-edit-or-add-items-key-items-titles-)
action, which is the way to *author* a record edit — by field name, in both languages,
with the server rows — and reaches for what that action does not: **growing** a table past
its retail count (a text table to 4,096 rows, an item table past its band, the menu
records), an entry's **exact bytes** where a rebuild must not normalise them (an icon, a
field no layout names, a table migrated from another tool), a zone's **dialog** table, and
the spell and command **records** of `ROM/118/114.DAT`.

Four kinds of table, each edited with the JSON its export command writes:

| `target.category` | The table | Edits (`resources.json`) |
|---|---|---|
| `strings` | any d_msg table — `xi ui strings list` names the common ones | `[{"id": 12, "text": "…"}]` from `xi ui strings export`; an entry's `"sub"` (else `target.entry`) picks the sub-string, default the block's first text |
| `items` | an item table `xi ui items info` knows | `[{"id": 30720, "name": "…", "level": 5, "jobs_list": ["WAR"]}]` from `xi ui items <group> json`; `id` is the item id, only the fields given are written, and an id past the end is built the way `xi ui items <group> inject` builds one |
| `dialog` | a zone's dialog table (what `xi event dialogue` edits) | `[{"id": 3, "text": "…"}]` from `xi event dialogue export`, with `edit`'s escapes (`\n`, `\v`, `{player}`); `"raw_hex"` instead of `text` writes those exact bytes, `"gap_hex"` the entry's whole byte-gap, terminator included |
| `menu` | the spell and command records of `ROM/118/114.DAT` | `[{"kind": "spell", "id": 84, "mp": 40, "levels": {"BLM": 75}}]` — the fields `xi ui spells export` shows, written over the record; `"record_hex"` instead writes the whole decoded record. `options.records` (`{"spell": 4096, "command": 4096}`) grows each section with empty records; the file's other sections are carried over |

One language's DAT per action: a JP table is a second action. An id past the end of the
table grows it (empty entries up to the id, then the entry) unless `options.grow` is
false; `options.fill` gives the filler entries a text (strings) or a name or whole record (items)
where a table's convention is a placeholder rather than an empty entry. Edits layer — two actions
on one DAT both land, the second reading the first's copy from the target — so a project
can split a big table's edits by theme. An entry can always carry its exact bytes instead
of fields (`block_hex`, `record_hex`, `gap_hex`), for a table a rebuild must not normalise.

```bash
uv run xi ui strings export Spell_Names -o edits/spell_names_en.json     # then keep only the changed entries
uv run xi dats prepare edits/spell_names_en.json --project names --type ui --target ROM/181/73.DAT --category strings
uv run xi dats build --project names --pivot
```

Or a self-describing file, which `prepare` reads without flags — the table and the edits
in one place, the shape a repository would keep:

```json
{
  "schema": "xi.ui.v1",
  "type": "ui",
  "target": {"dat": "ROM/25/52.DAT", "category": "dialog"},
  "edits": [{"id": 3, "text": "Welcome back.\\v"}]
}
```

`undo` deletes a table the build created in the target; one it edited in place (the
install's own file, or a copy an earlier build put there) holds other edits too, so it is
left and named — restore it from its `.base`, or rebuild without the action.

## Building (`xi dats build`)

A build writes DATs and patches their file_ids **directly into the base install
(`FFXI_DIR`)** — or, with **`--pivot`**, into `FFXI_PIVOT_DIR` instead. A configured
`FFXI_PIVOT_DIR` is never written to without the flag (apart from the table sync below).
After a successful pack build into the base install, if `FFXI_PIVOT_DIR` is set, the
custom region of the pivot's tables is updated via `sync_pivot_from_base()` so sizes stay
uniform and new gear/entity file_ids resolve through it; a `--pivot` build registers in
the pivot's own tables and skips that sync. Each action records the target it was built
into (`result.targets`), and `undo`, `package` and `release` follow it.

```bash
uv run xi dats build --project gyokko_mask            # into FFXI_DIR, then sync pivot tables
uv run xi dats build --project gyokko_mask --pivot    # into FFXI_PIVOT_DIR (no sync)
uv run xi dats build --project gyokko_mask --dry-run  # preview only, writes nothing
uv run xi dats changelog --project gyokko_mask        # table of recorded results
```

### Ability server options (`--apply-db`, `--menu-record`, `--lua-stub`)

For `ability` actions a build can also do the server side, after every DAT is placed and
recorded (the model viewer's Manage switches *Database Update*, *Client Menu Record* and
*Lua Stub*; [../ability/mixer.md](../ability/mixer.md#database-update-client-menu-record-lua-stub)
has the rules):

```bash
uv run xi dats build love --apply-db --clone-from fire --menu-record --lua-stub [--dry-run]
```

| Option | What it does |
|---|---|
| `--apply-db` | Update the `spell_list` / `abilities` / `weapon_skills` row named after the mix (animation only), or, when there is none, insert one cloned from the kind's default donor (`cure` / `berserk` / `fast_blade`). With `--dry-run`: SELECTs only. |
| `--db-row ID` | Confirm, once, a row this mix did not create (the build says `needs-confirm` and names it). |
| `--clone-from X` | Override the donor a new row, menu record and stub clone (an id or a server name). Default: the kind's donor above. |
| `--server-id ID` | The id for a new row / menu record (default: the highest one blank in the client and free on the server). |
| `--menu-record` | The client `mgc_` / `comm` record (and its names) at the row's id, over a blank retail placeholder only — never a named row, whatever `--force` says. |
| `--menu-name TEXT` | What the menu shows (default: the mix name, `_` as a space). |
| `--lua-stub` | `<XI_SERVER_DIR>/scripts/actions/…/<name>.lua` for a row this mix created. |

Credentials come from xi-tools' `.env` (`XI_DB_*`), never from the command line. Each
step prints one line (`db: …`, `menu: …`, `lua: …`, then `⚠` warnings) and never fails the
build: the exit code is 0 whenever the DATs were placed. What each step did is recorded
on the action (`result.db`, `result.menu`, `result.lua`) and carried over by every later
build. A real build that wrote a row ends with `database: 1 row written (spell_list
#1023) — restart the map server (xi_map) to load it`, and the publish folder gains
`<slug>_<animation>.applied.sql`, the statements that ran.

### Which table registers a file_id

The client resolves a file_id through the `ROM{n}` pair first (`ROM10/FTABLE10.DAT` +
`VTABLE10.DAT`) and falls back to the main `FTABLE.DAT`/`VTABLE.DAT`. XIPivot swaps in
the pivot folder's copy of a `ROM{n}` pair, but the main pair always comes from the base
install: the pivot folder's copy of it is never read. Tested in game with `!injectaction`
(a job ability, a spell and a weapon skill each played its custom DAT when registered only
in the pivot folder's `ROM10` pair, even over a main entry still pointing at a retail
placeholder), and XIPivot's debug log shows the same — it serves `ROM10/FTABLE10.DAT` from
the pivot folder and never sees `FTABLE.DAT` opened.

So a build registers a `ROM{n}` placement in the target's `ROM{n}` pair. In the base
install the main pair gets the same entry when it is big enough to hold the id (for the
tools that read only that pair); a retail-sized main `FTABLE` — a launcher may put one
back — is left alone instead of failing the build, and a pivot folder's main pair is never
written. A `ROM/…` placement registers in the main pair, so it needs the base install: with
`--pivot` the build refuses one that would need a new entry.

What this means with a pivot folder that carries its own `ROM10` tables (CatsEyeXI's does):

- A job ability, spell or weapon skill built into the base install lands in the install's
  `ROM10` tables, which that client never reads, and `sync_pivot_from_base()` copies only
  the custom region above retail (109,701 on), so it stays invisible there. Build those
  with `--pivot` (the model viewer's *Use Pivot Folder*).
- The same resolution order decides whether a slot is free: builds and the ability
  publisher read the `ROM{n}` pair first, so a slot that is live through a `ROM{n}` entry
  is never handed out again.
- Gear and entity ids sit in the custom region, so a base-install build reaches the pivot
  folder through the sync — and the same sync overwrites that region of the pivot folder's
  tables, including entries a `--pivot` build registered there. Don't mix plain and
  `--pivot` builds for gear and entities.

- The target's `FTABLE`/`VTABLE` **must already exist and be expanded** for the custom
  models you're placing — run `xi ftable expand entity` / `xi ftable expand gear` on
  that install first. The wizard's opening step reports expansion status.
- A `ROM{n}` pair long enough to hold the id is **not** on its own enough: the client
  takes its file_id ceiling from the tables it loads at startup, and those come from the
  **base install**. A `--pivot` build registers, it does not expand.

  Measured against an install still at the retail 109,701 entries, with the pivot
  folder's `ROM10` pair grown to 431,344: a `ROM10` registration at file id 100,500
  loaded in game, the same registration at 109,750 did not, and lengthening only the
  pivot pair changed neither. After `xi ftable expand` grew the install's tables, both
  loaded. So expansion is a one-time write to the install (or a table set a launcher
  ships) before any file_id past the retail range exists at all — and it is the first
  thing to check when a registration that reads back correctly resolves to nothing.
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
uv run xi dats package gyokko_mask               # read from where it was built
uv run xi dats package gyokko_mask --from pivot  # or dir | pivot | hd
```

With no project argument, lists `projects/*.json` to pick from. Reads from **one** source
(`--from dir|pivot|hd`; the default is where the project was built — `pivot` when every
build used `--pivot`, else `dir`). Zips everything needed to run one project as an overlay:

- every DAT the actions built into that source placed (from each action's inline `result`
  — all per-race DATs for gear), plus the mount name/help/key-item string DATs for mount actions,
- for an ability whose client menu record was placed in that source (`--menu-record`):
  `ROM/118/114.DAT` and the name/help tables it edited, so players get its menu entry,
- the full `FTABLE`/`VTABLE` set (so the new file_ids resolve).

Files are laid out ROM-relative inside the zip (XIPivot-ready). Build the project first.

## Package layout vs live target build

Two different trees — don't confuse them:

| Path | Role |
|---|---|
| **Live target** (`FFXI_DIR`, or `FFXI_PIVOT_DIR` with `--pivot`) | Where `dats build` places mesh/entity/gear/mount DATs + table patches and edits menu records. After a base-install build, `sync_pivot_from_base()` updates pivot overlay tables when configured. |
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
  abilities/<slug>/           # one per ability: its SQL, a copy of each DAT placed, placements.json,
                              #   <slug>_<n>.applied.sql (--apply-db) and the viewer's <Name>.mix.json
  server/                     # emitted server snippets (e.g. mounts, spell / command records)
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

### Includes

An `actions` entry can be a string: the path, relative to the file it appears in, of an
include file ([`schema/include.json`](../../schema/include.json)) whose actions are spliced
in at that spot. One feature can then keep one project file and split its actions across
files — gear, record edits, events — without splitting the build:

```json
{
  "schema": "xi.dats.v1",
  "name": "abyssea",
  "roots": {"standard": "projects/ffxi", "hd": "projects/ffxi-hd", "resources": "projects/resources"},
  "actions": [
    "abyssea/gear.json",
    "abyssea/records.json",
    {"id": "ability.tomahawk", "type": "ability", "kind": "auto", "…": "…"}
  ]
}
```

```json
{
  "schema": "xi.dats.include.v1",
  "description": "Abyssea: the record edits",
  "actions": [{"id": "db.abyssea", "type": "database", "edits": ["…"]}]
}
```

- Every command reads the project flattened: one action list, in order, includes nested
  as deep as they go. `xi dats json` prints it that way.
- A write puts each action back in the file it came from: a build's `result` lands in the
  include file beside its action, and the include strings stay where they were written.
  An include file whose actions didn't change is left alone. An action added by `new` or
  `prepare` goes in the project file.
- Included actions need an `id` (it is how they are matched back), and an id may appear
  once across the project and its includes. A missing file, a file without
  `"schema": "xi.dats.include.v1"` and an include cycle are refused, naming the file.
- Keep include files in a folder under `projects/` (`projects/abyssea/records.json`):
  project lists and cross-project scans read only `projects/*.json`, and skip an include
  file found there.

## Commands

| Command | What it does |
|---|---|
| `xi dats new` | **Interactive wizard** — place prebuilt DATs (gear/mount/entity/NPC) at new model ids, publish an ability recipe, or add a spell / command menu record, and write a manifest action; `--pivot` checks and builds into `FFXI_PIVOT_DIR` |
| `xi dats build [manifest]` | Build into the **base install** (`FFXI_DIR`), then `sync_pivot_from_base()` when a pivot is configured; `--pivot` builds into `FFXI_PIVOT_DIR` instead (no sync); `--dry-run` previews (no separate `plan` command); for abilities `--apply-db` / `--menu-record` / `--lua-stub` ([above](#ability-server-options---apply-db---menu-record---lua-stub)) |
| `xi dats package <project>` | Zip the project's built DATs + F/V tables (`--from dir`/`pivot`/`hd`, default where it was built) into `projects/packages/<project>.zip` (ROM-relative, XIPivot-ready) |
| `xi dats release <project>` | Stage the project's DATs + full FTABLE/VTABLE set + patched `FFXiMain.dll` into `<release>\Game\FINAL FANTASY XI\…` (a launcher build folder), and the DATs of `--pivot` builds into the release's pivot folder. Prompts for the folder; `--to <path>`, `--no-dll` |
| `xi dats undo <project>` | Reverse a build in each target an action was built into: delete the placed DATs + clear their file_id entries, put menu records back (an ability's only while the row still holds what the build wrote), then remove the manifest (`--keep-json` keeps it). `--apply-db` also reverts the database row an ability inserted or changed and deletes its unedited Lua stub; without it they are listed (with the revert SQL) and left, and the manifest is **kept** — its cleared actions marked `undone` — until a later `undo --apply-db` removes them (a row already gone or already back at its old animation counts as done). An ability menu record that can't be written back (the game has `114.DAT` open) also keeps the manifest, and the next undo retries it |
| `xi dats json [manifest]` | Print the normalized manifest JSON |
| `xi dats prepare <source> [manifest]` | Copy an exported JSON/change-set/ability recipe/spell or command definition into `projects/resources` and add an action (`--type`; for abilities `--kind` / `--animation` / `--subdir`; for spells and commands `--record-id` / `--menu-index`; for a table's edits `--type ui --target <ROM path> --category strings|items|dialog`) |
| `xi dats changelog [manifest]` | Table of each action's recorded inline `result` (model_id → file_id → DAT) |

> Note: `new`/`build` write mesh/entity/gear/mount DATs + table patches into **`FFXI_DIR`**
> (then sync pivot tables), or into `FFXI_PIVOT_DIR` with `--pivot`, while `zone` actions
> still build the `projects/ffxi` + `projects/ffxi-hd` package trees.

## Current builders

Verbatim-placement types (written by `xi dats new`, built into the live target):

- `gear`: places one prebuilt gear DAT per race at a windowed custom gear file_id
  (`gear.xi_inject.custom_fid`). Needs `xi ftable expand gear`.
- `entity`: places a prebuilt entity DAT at `file_id = model_id + 98239`. The **NPC costume**
  wizard also emits an `entity` action — it first *bakes* the DAT it places
  (`entity.xi_bake_npc`, source `projects/custom/<project>.dat`).
- `mount`: places the model DAT at the chosen path, writes EN/JP name/help + optional
  key-item d_msg overrides, registers the file_id, and emits a server snippet.
- `ability`: composes a recipe (`xi.ability.xi_compose`) into one DAT (job ability /
  spell) or body + two companion DATs per race (weapon skill), takes the animation
  number against the live tables, places them under `ROM10/<subdir>/` and registers the
  file ids; records kind / animation / placements on the action and writes the publish
  folder `projects/abilities/<slug>/`: the server SQL, a copy of each placed DAT
  at its ROM path and `placements.json` (`schema/ability_publish.json`), replacing what
  the previous publish of the action put there. Needs no table expansion. With
  `--apply-db` / `--menu-record` / `--lua-stub` it then updates the server row, places
  the client menu record and writes the Lua stub, recording `result.db` / `menu` / `lua`.
- `spell` / `command`: writes a definition (`xi.menu.xi_menu_table`) as a new record of
  `ROM/118/114.DAT` (the `mgc_` / `comm` section grown to hold it) plus its EN/JP
  name/help blocks in `ROM/181`, at an id above the retail band decided against the
  live table; records `record_id` (+ `menu_index`) and the edited string tables on the
  action, emits a server row template to `projects/server/spells|commands/`. No file
  ids, no table expansion; the client needs a ceiling plugin such as cexislots to show
  the band. A `--force` overwrite keeps the old row on `result.replaced` for `undo`.
- `ui`: a client table with some entries changed (`xi.dats.xi_tables`) — a d_msg
  string table (`strings`), an item table (`items`) or a zone's dialog table (`dialog`).
  The build reads the table as the target sees it, applies the edits in `resources.json`
  and writes the result at the same ROM path; a retail file, so no file id and no table
  expansion. Everything the edits do not name is carried over byte for byte. An id past
  the end grows the table unless `options.grow` is false. Records `entries`, `grown_to`
  and `created` (the target held no copy before, so `undo` deletes it; an in-place edit
  is left). See [Table edits](#table-edits-ui-action).

Record edits:

- `database` ([`schema/database.json`](../../schema/database.json),
  [../database/README.md](../database/README.md)): edits to the client's record tables —
  the tables the model viewer's Database shows (items by category, key items, titles, quest
  and mission logs, spell and ability text) — by table key and id, with the viewer's field
  and sub-string names. Only the named fields change, in the English and Japanese records,
  in legacy or retail DATs; `like` copies another record into an empty slot first. Records
  each changed field's value before and after per target, so a rebuild converges and
  `undo` puts the records back. An item edit may carry its server rows (`item_basic`,
  `item_equipment` with `MId` or a gear action's model, `item_mods`, …), written as proposed
  SQL to `<project>.sql`, never run. No file ids, no table expansion.

GLB-rebuild / package types:

- `mesh`: rebuilds an existing model DAT's geometry from an edited GLB into the target.
- `zone`: applies one `zone-changes.json` to the `projects/ffxi` standard tree and
  optionally the `projects/ffxi-hd` tree.

Other action schemas are present so package shape is stable, but their builders are
added incrementally.

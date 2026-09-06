# `xi mv` — model-viewer lists and database

Helpers that bake the JSON files [xi-model-viewer](https://github.com/vekien/xi-model-viewer)
loads at runtime, so the viewer never has to scan the game install or decode the
large item DATs in the browser.

| Command | Output | What it does |
|---|---|---|
| `xi mv update` | `mv/lists/*.json` | Append missing entries to the viewer's asset lists (gear, music, sfx, effects, images, NPCs, …). Never rewrites curated names. |
| `xi mv database` | `mv/db/<table>.<lang>.json` + `manifest.json` | Decode the item tables and `d_msg` text tables once and write them as JSON rows. |

`mv/lists` is tracked in git (the viewer ships from it). `mv/db` is regenerable and
gitignored.

---

## `xi mv update`

```bash
uv run xi mv update                                 # every target
uv run xi mv update --only gear,music
uv run xi mv update --only images,npcs --dry-run    # report, write nothing
uv run xi mv update --only zone-names               # push curated mog-house names
uv run xi mv update --lists D:\viewer\lists --base mv\lists   # write elsewhere, seed from repo
```

| Option | Default | Meaning |
|---|---|---|
| `--only TARGETS` | all | Comma-separated subset of the targets below (`zone_music`, `file_ids`, `npc_anims`, `zone_names` … spellings accepted) |
| `--lists DIR` | `mv/lists` | Output directory |
| `--base DIR` | — | Seed a list from this directory when it is missing under `--lists`; writes still go to `--lists` |
| `--sql PATH` | `<XI_SERVER_DIR>/sql/zone_settings.sql` | Source for `zone-music` |
| `--mid-cap N` | `1500` | Gear: only consider model ids up to `N` |
| `--dry-run` | off | Report what would change without writing |

Every target is **append-only**: it adds rows the list lacks and leaves curated names,
labels and groupings alone. A full run reads ~53k DAT headers; progress streams per
target so the command never goes quiet.

### Targets

| Target | Source | What it adds |
|---|---|---|
| `gear` | FFXiMain race tables → FTABLE | Missing gear model ids per race/slot, plus one `rod: true` Ranged row per fishing rod (see [../anim/fishing.md](../anim/fishing.md)) |
| `gear-sets` | `src/xi/mv/gear_sets.json` + existing labels | `set` on each gear row (Artifact / Relic / Empyrean / Prime / Aeonic / Mythic / Abjuration / Ebur-Furia-Ebon), `groupLabels` renames on weapon-type groups, the `rangedDisplay` rule, and clears any `retiredSets` |
| `gear-labels` | `(JOB Set)` label suffix | Rewrites `Wizard's Coat (BLM Artifact)` → `BLM - Wizard's Coat` (the set is preserved in `set` first) |
| `music` | `sound*/win/music/data` | Unnamed `music*.bgw` |
| `sfx` | `sound*/win/se` | Unnamed `se*` folders and `.spw` files |
| `zone-music` | `zone_settings.sql` | Full rebuild of the zone → BGM map |
| `effects` | spell / ability / weapon-skill animation → file_id | Missing VFX DATs |
| `images` | DAT section scan (textures only) | Missing map, UI and cutscene art |
| `npcs` | modelid → file_id → DAT, named from `mob_pools` / `npc_list` | Missing entity models |
| `npc-anims` | `Directory (0x01)` sections in each model DAT | `anims` on NPC rows whose model borrows animation packs from other DATs (trusts, multi-form monsters) |
| `zone-names` | `MOG_HOUSE_NAMES` in `xi.zone.xi_list` | Hand-verified names on mog-house rows in `zones.json`, matched by path; ids, fileIds and custom zones are left alone |
| `file-ids` | reverse FTABLE/VTABLE | `fileId` on every row in every list |

Not covered: `floors.json`, and a full `zones.json` regeneration (new or renamed named
zones). Those are manual — see `xi zone json --rooms --dev`.

### VFX file-id bands

`effects` resolves an animation id to a file id by band (`offset + animation`): spells
`2800` (`0xAF0`), job abilities `4412`, weapon skills `4912`. `mob_skills` and
`item_usable` animations are **not** file ids — they index the caster's own motion set.

### Gear sets and labels

`gear-sets` writes a single `set` field holding every bucket. Artifact, Relic and
Empyrean come off the existing `(JOB Set)` label suffix; `Ebur / Furia / Ebon` is one
merged bucket matched by name prefix; Prime, Aeonic, Mythic and Abjuration are matched by
normalised item name against `gear_sets.json`. Every name in that file was checked
against the server's `item_basic` table **and** an existing label in `characters.json`,
which is how a fabricated wiki list of "Limbus" items was caught. Limbus itself was
tried and withdrawn (`retiredSets`); Omen is deliberately absent because its rewards
are upgraded Artifact/Relic/Empyrean models or accessories with no model.

`gear-labels` only touches brackets that start with a real job code — `(119 AG)`,
`(Stage 5)`, `(SU5)` are left alone — and reports (rather than renames) a job code paired
with an unknown set keyword, since that is a typo in the label.

### Trust animation packs (`npc-anims`)

A trust does not carry its combat clips in its own DAT. The model declares the content
families it pulls in as `Directory (0x01)` sections, and the packs are the DATs whose
*root* FourCC is one of those names. Both Iroha models (`ROM/310/3`, `ROM/310/4`) name
`iro_`; of the 17 DATs with that root, the six carrying a `SkeletonAnimation`
(`ROM/338/15`–`20`) are the packs.

Each entry is recorded as `{path, clips}` rather than a flat clip list, because packs
reuse clip ids (Iroha's six packs hold only four distinct ids) and the viewer loads one
at a time. `src/xi/mv/npc_anims.json` holds the two tunables: `maxFamilySize` (a
Directory id naming more DATs than this is a shared vocabulary such as `mot_`, not one
entity's pack) and `ignoreDirIds`.

### Ranged weapons and fishing rods

Ranged weapons bind straight to the back-mount bone (no grip joint; `standardJointIndex`
255) and nothing in the motion DATs animates those joints, so the **client** decides
visibility: a bow is scaled to zero until it is in use. The `rangedDisplay` block in
`gear_sets.json` ships that rule to the viewer — which action groups count as "in use"
and which hand joint (126 left, 127 right) the mount bone is re-parented onto.

Fishing rods are not ranged meshes at all (the ranged table only has a 3-vertex stub for
model ids 1–15). The `gear` target appends one Ranged row per rod and race from the
client's per-race rod table, named from `fishingRods` in `gear_sets.json`, flagged
`rod: true`. [../anim/fishing.md](../anim/fishing.md) has the full mechanism.

---

## `xi mv database`

```bash
uv run xi mv database                              # every table, en + jp
uv run xi mv database --only armor,weapons --lang en
uv run xi mv database --out D:\viewer\db --game "D:\FFXI\FINAL FANTASY XI"
```

| Option | Default | Meaning |
|---|---|---|
| `--only KEYS` | all | Comma-separated subset of table keys |
| `--lang` | `en,jp` | Client languages to bake |
| `--out DIR` | `mv/db` | Output directory |
| `--game DIR` | `FFXI_DIR` | Game install to read |

Writes one `<table>.<lang>.json` per table plus `manifest.json` (row counts, source
DATs, timestamp). The viewer's *Assets → Database* page prefers these files and falls
back to decoding the DATs itself when they are missing.

**Item tables** (`general`, `usable`, `puppet`, `armor`, `weapons`, `maze`, `monst1`,
`roeObj`, `items3`–`items6`, `monst2`, `roeCat`, `gil`): rows are `{idx, part, raw}`
where `raw` is the typed header plus `strings` / `stringOffset` for real item records,
or `{id, block}` with the decoded block base64-encoded for the DATs the viewer decodes
itself. Blocks are the client's rotate-left-3 "encryption", undone here.

**`d_msg` tables** (quests and missions per nation and expansion, `keyitems`, `titles`,
`jobs`, `spells`, `abilities`, `status`, `mounts`, `augments`, `merits`, `trust`, …):
rows are `{idx, offset, length, subs}`; `subs` is a list of decoded text, or the integer
marker for a non-text sub-string.

The table registry, block layouts and row shapes mirror `ui/js/database.js` in
xi-model-viewer and must stay in step with it.

---

## Files

| File | Role |
|---|---|
| `src/xi/mv/xi_update.py` | `xi mv update` command |
| `src/xi/mv/update_lists.py` | One updater per target, `ALL_TARGETS`, `run_updates` |
| `src/xi/mv/xi_database.py` | `xi mv database` — item and `d_msg` decoders, table registry |
| `src/xi/mv/dat_index.py` | Header scan of every DAT (section types, Directory FourCCs), `extra_anim_packs()` |
| `src/xi/mv/server_names.py` | NPC names from `mob_pools` / `npc_list` |
| `src/xi/mv/gear_sets.json` | Set membership, label fixes, section order, `rangedDisplay`, `fishingRods`, `groupLabels`, `retiredSets` |
| `src/xi/mv/npc_anims.json` | `npc-anims` tunables |
| `mv/lists/*.json` | The shipped lists: `characters`, `npcs`, `effects`, `images`, `music`, `sfx`, `zone_music`, `zones`, `floors` |
| `mv/db/` | `xi mv database` output (gitignored) |

Related: [../anim/fishing.md](../anim/fishing.md), [../zone/prototype-zones.md](../zone/prototype-zones.md)
(the dev/prototype rows in `zones.json`), [../zone/viewer-prototype-town.md](../zone/viewer-prototype-town.md).

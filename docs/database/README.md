# Database records — the `database` action

Edits to the client's record tables — the tables the model viewer's Database shows — made
reproducible: a `database` action in a `xi dats` project names each record by table and id
and only the fields that change, `xi dats build` patches the records, and the same project
rebuilds them on another install or after a client update. Item edits can also carry the
item's server rows, written as proposed SQL beside the project (xi-tools never runs it).

Schema: [`schema/database.json`](../../schema/database.json). Library: `xi.database`
(`xi_core` — names, validator, encoders; `xi_build` — build, undo, SQL).

---

## An example

`projects/gear_tweaks.json` (or an include file — see
[../dats/README.md](../dats/README.md#includes)):

```json
{
  "id": "database.gear_tweaks",
  "type": "database",
  "edits": [
    {"table": "armor", "id": 10253, "set": {"level": 50}},
    {"table": "armor", "id": 12579,
     "strings": {"en": {"description": {"replace": {"HP+15": "HP+30"}}}},
     "server": {"item_mods": {"HP": 30}}}
  ]
}
```

```
xi dats build gear_tweaks --dry-run --pivot
xi dats build gear_tweaks --pivot
```

Decennial Coat +1 (10253) goes to level 50 in the English and Japanese armor DATs;
Scorpion Harness's (12579) description reads HP+30, its Ice/Water/Dark icons untouched;
`projects/gear_tweaks.sql` gets `UPDATE item_equipment SET level = 50 …` and
`REPLACE INTO item_mods … (12579, 2, 30)`.

Three ways in:

- write the action (or a list of edits) and `xi dats prepare edits.json --project P`;
  `--merge` adds the edits to the project's action of the same id, replacing an edit of the
  same table and id;
- `xi dats new` → *Database records*: pick a table, give an id, see what the record holds,
  then type changes — `level=50`, `jobs=WAR,PLD`, `flags=RARE,EX`,
  `description=Line one\nLine two`, `description~HP+15=>HP+30`, `jp.name=…`, `mod.HP=30`
  (`mod.HP=` removes it), `note=why`;
- the model viewer, which can drive `prepare` with a file of edits.

---

## Edits

| Key | What |
|---|---|
| `table` | The viewer's table key: `armor`, `weapons`, `general`, `usable`, `puppet`, `maze`, `monst1`, `items7`, `roeObj`, `items6`, `gil`; `keyitems`, `titles`, `q_*` / `m_*`, `spells`, `spellHelp`, `abilities`, `abilityHelp`, …; `spellData`, `abilityData` (below). Or a d_msg table's ROM path (below). |
| `id` | Item tables: the item id — it picks the DAT (armor 10240–16383 → `ROM/118/109`, 23040–28671 → `ROM/286/73`). Key items and quest / mission logs: the id stored in the row. Other text tables, and spell / ability data: the row index. |
| `set` | Header fields by the viewer's names: `level`, `itemLevel`, `superiorLevel`, `jobs`, `races`, `slots`, `flags`, `stack`, `type`, `targets`, `resourceId`, `shieldSize`, `maxCharges`, `castTime`, `useDelay`, `reuseDelay`; weapons `damage`, `delay`, `dps`, `skill`, `jugSize`, `baseItemId`. Written to both languages' records. |
| `strings` | `en` / `jp` sub-strings: items `name`, `article`, `logName`, `logPlural`, `description` (JP: `name`, `description`); text tables their own (`name`, `plural`, `description`, …; the help tables have one, `help`; the Japanese key items keep only `name` and `description`, the Japanese status names only `name`). A value is the text, or `{"replace": {old: new}}` on the record's original text. |
| `like` | Create the record: copy record `like` of the same table into the empty slot `id`, then apply the edit. The Japanese record takes the English name/description unless `strings.jp` says otherwise. |
| `icon` | `{"from": <item id>}` — copy another item's icon. |
| `hex` | The whole record, exactly (below). |
| `server` | The item's server rows (see below), or `false` to leave the server out. |
| `note` | Why — shown in the SQL. |

**Names, not bits.** Jobs (`WAR` … `RUN`, or `"all"`), races (`HUME_M` … `GALKA`, or `"all"`),
slots (`MAIN` … `BACK`), flags (the server's `ITEM_FLAG` names: `RARE`, `EX`, `CANEQUIP`,
`NOAUCTION`, …) and weapon skills (`HAND_TO_HAND` … `FISHING`, or a number) are the names
CatsEyeXI's item JSON uses, checked against the DATs built from it. The build encodes each
side: the DAT keeps WAR at job bit 1, the server at bit 0.

**Text** is written as the client shows it, `\n` for a line break. `{Fire}` `{Ice}` `{Wind}`
`{Earth}` `{Lightning}` `{Water}` `{Light}` `{Dark}` are the element icons, `{at:xxxxxxxx}` an
auto-translate phrase, `{x:hh}` a byte cp932 can't show. All 73,728 armor and weapon strings
of the CatsEyeXI install read and write back byte for byte this way, so a record's old text
is exact in the changelog and in undo. A description must end before the icon (0x280 bytes into the record).

**`dps`** follows `damage` / `delay` (`damage × 6000 / delay`) unless you set it.

---

## The server side

`server` names the server's own tables and columns: `item_basic`, `item_equipment` (`MId`
may be `{"gear": "<gear action id>"}` for the model a gear action placed), `item_weapon`,
`item_usable`, `item_furnishing`, `item_info` (CatsEyeXI's), `item_mods`, `item_mods_pet`,
`item_latents`.

- `item_mods` changes only the mods it names (`null` removes one): `REPLACE INTO item_mods`.
  Mod names are `xi.mod`'s, read from `XI_SERVER_DIR/scripts/enum/mod.lua`.
- `item_mods_pet` and `item_latents` list the item's complete set: `DELETE` then `INSERT`.
- The other tables are `UPDATE`s of the columns given.
- With `server.mirror` (default on), the fields both sides hold follow the `set`: `flags`,
  `stack` (`item_basic`), `level`, `itemLevel` → `ilevel`, `superiorLevel` → `su_level`,
  `jobs`, `slots` → `slot`, `shieldSize` (`item_equipment`), `damage` → `dmg`, `delay`,
  `skill` (`item_weapon`). A column the edit names itself wins.
- A `like` edit copies the donor's rows (`INSERT … SELECT`) for its kind of item, then applies
  the changes; with an English name it also sets `name` / `sortname` (`abyssal_earring`).

The SQL goes to `<project>.sql` beside the project file (or `server.sql`, relative to the file
holding the action), each action between `-- >>> <id>` / `-- <<< <id>` markers so a rebuild
replaces its own part. It is a proposal: review it and copy it into the server's modules
(`sql/custom`), where `dbtool` re-applies it on every update. `server.emit: false` writes none.

---

## Builds, rebuilds and undo

- **Which file.** The build reads a record's DAT from the target — the base install, or the
  pivot folder with `--pivot` (its own copy, else the install's, copied in on the first
  write) — detects its format (legacy 0xC00 / retail 0x1400) and patches that. A base-install
  build warns when the pivot folder has its own copy: that is the one the client loads.
  CatsEyeXI's item DATs live in its pivot folder, so its builds use `--pivot`.
- **What's recorded.** `result.roots.<target>` lists every record written — table, id,
  language, DAT, format, block — with each changed field's value before and after. A record
  a `like` created keeps the slot's old bytes.
- **Rebuilds converge.** A build first puts back what the previous build of the action
  changed, then applies the edits: the same edits give the same bytes, an edit you remove goes
  back to what it was, and `replace` always applies to the original text.
- **Undo** (`xi dats undo P`) puts each record back field by field, removes a created record
  (the slot's old bytes), and drops the action's SQL section. A field something else changed
  since the build is left as it is, and said.
- `.base`: the first write of a DAT in the base install keeps `<DAT>.base`, as every build does.

Refused, naming the edit: an id outside the table, an empty slot without `like`, a `like`
into a slot that holds a record (unless `--force`), a `replace` whose text isn't there, text
that doesn't fit, a mod name the server doesn't have.

## Spell and ability data

`spellData` and `abilityData` are the spell and command records of `ROM/118/114.DAT` — what the
model viewer shows under *Spells & Abilities*: a spell's kind, element, skill, MP, cast and
recast time and the jobs that learn it; an ability's type, TP, level, range, radius and AoE.
One file serves every client language (the names and help live in `spells` / `spellHelp`,
`abilities` / `abilityHelp`).

```json
{"table": "spellData", "id": 1, "set": {"mp": 5, "cast": 4, "levels": {"RDM": 1, "PLD": null}}}
{"table": "abilityData", "id": 32, "set": {"range": 4}}
{"table": "spellData", "id": 1030, "like": 1, "set": {"mp": 10}}
```

| Field | What |
|---|---|
| spells | `kind` (`white`, `black`, `summon`, `ninjutsu`, `song`, `blue`, `geomancy`, `trust`, or a number), `element` (`fire` … `dark`, `none`, or a number), `skill` (33 healing, 36 elemental …), `mp`, `cast` and `recast` (quarter seconds: 8 = 2 s), `targets`, `menu_index`, `icon`, `icon2`, `requirements`, `levels` |
| abilities | `type` (1 ability, 3 weapon skill, …), `tp`, `level`, `range`, `radius`, `aoe`, `targets`, `valid_targets`, `tp_modifier`, `charges`, `icon`, `icon2` |

`levels` merges into the spell's: `{"RDM": 1}` adds Red Mage at 1, `null` takes a job off (MON is
the Monstrosity column). `like` copies a record into an empty slot; an id past the end grows the
section (the client reads 1,024 spells and 2,816 commands without a ceiling plugin such as
cexislots, and the build says so). New spells with their names, help and a menu slot are the
[`spell` type](../menu/records.md); this edits the records that are there.

**The server.** The client shows these numbers; the server decides them. A spell edit writes its
`spell_list` row to the proposed SQL — `mpCost`, `castTime` and `recastTime` (quarter seconds × 250
= milliseconds), `element` (the server counts from 1: fire 1 … dark 8, none 0), `skill`, and `jobs`
(one byte per job, WAR first, 0 = can't learn) for the fields the edit sets; `like` copies the
donor's row first. Ability records carry no SQL yet (how a command id maps to the server's
ability and weapon-skill ids isn't pinned down), and `server: false` leaves a spell's out.

## Exact bytes (`hex`)

`hex` writes a whole record exactly — for what a field edit can't say, or a record copied byte
for byte from another install. Item tables: the decrypted record (0xC00 bytes legacy, 0x1400
retail), per language: `{"hex": {"en": "…", "jp": "…"}}` (a language not given is left alone).
d_msg tables: the block, per language the same way. Spell / ability data and tables by path: one
text, the decoded record. It goes alone in its edit — no `set`, `strings`, `icon` or `like` — and
undo puts the record back as it was, while it still holds what the build wrote. The model viewer
shows a record's bytes in its detail card.

## Tables by path

A d_msg table the viewer doesn't name can be given by its ROM path: `{"table": "ROM/181/72.DAT",
"id": 5, "strings": {"sub0": "Fast Blade II"}}`. Its sub-strings are then `sub0`, `sub1`, … by
position (text, `{"replace": …}`, or a number where the sub-string is one); a row is its index,
`like` adds one past the end as for the named tables, and `hex` writes a block. It is one file,
whatever the language — a Japanese table is its own path.

## New text rows

A text-table row past the end needs `like`, the row to copy: `{"table": "titles", "id": 1300,
"like": 1, "strings": {"en": {"name": "Abyssea Delver"}}}` grows the titles table to 1,301
rows — the rows in between hold `.`, as retail's unnamed rows do — in both languages (the
Japanese row takes the English text unless `strings.jp` gives its own). Key items and the
quest / mission logs are keyed by the id each record carries, so a new id is appended; `like`
there picks the record to copy. Undo trims the rows back off, only while nothing grew the
table since. A zone's dialog lines are not here — they are [`zone_dialog`](../dialog/zone_dialog.md).

## Not yet

- CatsEyeXI's custom item band (`ROM/288/79–80` grown past 30,720) is recognised for the
  `general`, `usable`, `armor` and `weapons` tables, but not yet tried on their install.
- An icon from a PNG (the client's palettized icon format needs an encoder).
- Proposed SQL for ability records (`abilities`, `weapon_skills`); the `mnc2`, `mon_` and `levc`
  sections of `114.DAT`, which aren't decoded yet.

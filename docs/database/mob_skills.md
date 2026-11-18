# Mob skill names

Rename a mob's TP move, or name a new one, so the log shows it: "The Goblin readies Stomp.",
"The Goblin uses Stomp." This page covers where the name comes from, how to change it with
xi-tools, how high an id can go, and how to look the names up.

## Where the name comes from

The name is **client-side**. The server sends only the skill's id (`mob_skills.mob_skill_id`)
and the client looks the name up in its own table:

| Ids | Name table | Row | `database` table key |
|---|---|---|---|
| 256 and up (every normal TP move) | `ROM/27/80.DAT` English, `ROM/27/79.DAT` Japanese (file ids 7035 / 7034) | `mob_skill_id − 256` | `mobSkills` |
| Below 256 (humanoid mobs using weapon skills) | `ROM/181/72.DAT` / `ROM/181/68.DAT`, the weapon-skill names | `mob_skill_id` | `abilities` |

Measured on CatsEyeXI: 3,353 of the 3,593 rows from id 256 in `sql/mob_skills.sql` have their
name on that row. The rest are rows the client leaves unnamed (`.`) or server-only rows. Renaming
`mob_skill_name` in the SQL alone changes nothing in the log.

So a mob skill is two halves:

1. **Server:** the `mob_skills` row (animation, range, AoE, times, flags) and its Lua script in
   `scripts/actions/mobskills/`, plus `mob_skill_lists` to give it to a mob. xi-tools doesn't
   write these.
2. **Client:** its name, which the `database` action edits. Players need the patched DAT
   (build with `--pivot` to put it in CatsEyeXI's DATs folder, `FFXI_PIVOT_DIR`).

## Rename an existing skill

`mob_names.json`:

```json
{"id": "database.mob_names", "type": "database", "edits": [
  {"table": "mobSkills", "id": 257, "strings": {"en": {"name": "Stomp"}}},
  {"table": "mobSkills", "id": 1000, "strings": {"en": {"name": {"replace": {"Lambda": "Omega"}}}}}
]}
```

```
uv run xi dats prepare mob_names.json --project mob_names
uv run xi dats build mob_names --pivot --dry-run
uv run xi dats build mob_names --pivot
```

Foot Kick (257) reads Stomp; Cronos Sling: Lambda (1000) reads Cronos Sling: Omega.
`{"replace": {old: new}}` changes part of the name.

## Add a new skill

Pick an id, give the server a `mob_skills` row with it, and name it here.

- **An unused row inside the table** (it holds `.`): nothing grows.
  `uv run xi database json mobSkills --all` lists every row; the ones with `"name": "."` and no
  `server` row are free.
- **An id past the end of the table:** the build grows the table to that id, in both languages,
  the rows in between holding `.`.

```json
{"table": "mobSkills", "id": 5200, "strings": {"en": {"name": "Abyssal Roar"}, "jp": {"name": "アビサルロア"}}}
```

- The Japanese table takes the English name unless `strings.jp` gives one, when its row has no
  name of its own (`.`, `#n`, or a row the build adds). A Japanese name already there is kept.
- `xi dats undo mob_names` puts every name back and trims the rows a build added (while nothing
  grew the table since). `xi dats build mob_names --reset` resets the tables first.
- The `xi dats new` wizard has the table as *Mob skill names*; the id it asks for is the
  `mob_skill_id`.
- An edit takes `strings` (`name`) and `note`. `copy_from`, `hex` and `set` are refused: a row
  is just a name.

## How high an id can go

| Where | Limit | Measured in |
|---|---|---|
| The server, today | Mob skills live in a `std::array` of `MAX_MOBSKILL_ID` slots: **4,262 in CatsEyeXI's tree**, so ids 0–4261 | `src/map/mobskill.h`, `utils/battleutils.cpp` |
| The server, raised | **65,535**. The id is 16-bit unsigned everywhere it is kept: `mob_skills`, `mob_skill_lists` and `pet_skills.mob_skill_id` (`smallint unsigned`), `CMobSkill::m_ID`, `GetMobSkill(uint16)`, `m_UsedSkillIds` (`std::map<uint16, …>`). `MAX_MOBSKILL_ID` 65536 covers every id: 65,536 pointers, 512 KB | the same, `entities/battleentity.cpp` |
| The packet | Not the limit: "readies" carries it in a 17-bit field (131,071), "uses" in a 32-bit one | `packets/s2c/0x028_battle2.cpp` |
| The client's table | 4,864 English rows (ids 256–5119), 5,120 Japanese (256–5375) in a current install; it grows by adding rows | `ROM/27/80`, `ROM/27/79` |
| The file | A 24-bit length (16 MB): hundreds of thousands of names. 65,280 rows (every id to 65,535) is about 1 MB | the file's header |

**Ids already free under the server's limit.** CatsEyeXI's `mob_skills.sql` uses 3,593 of the ids
256–4261, which leaves 414 ids without a server row: 83 of them unnamed in the client too (the
cleanest place for a new skill), and 331 with a retail name the server doesn't implement (free to
rename). So new skills don't need the limit raised until those run out; past 4261 they do.

**A server bug to know about:** CatsEyeXI's tree has one row at id 4262 (`hell_trap`), one past
the end of a 4,262-slot array, so loading it writes outside the array. Raising `MAX_MOBSKILL_ID`
fixes that too.

**Growing the table once.** `uv run xi database grow ROM/27/80.DAT 65280` (and `ROM/27/79.DAT`)
fills it to id 65,535 with `.` rows as one-time set-up; the install's `.base` grows with it, so a
`--reset` build keeps the rows.

**Not tried in game yet:** the two language tables already differ in length, so the client reads
each file's own row count, but nobody has checked that it shows a name from a row past retail's
end (an id above 5119 in English). Try one id in a grown table before relying on it.

## Look the names up

```
uv run xi database search mobSkills "foot kick"     # 257  Foot Kick  [foot_kick anim 1]
uv run xi database search mobSkills 4262            # a number is an id
uv run xi database search mobSkills hell_trap       # the server's name matches too
uv run xi database json mobSkills --search breath   # the matching rows as JSON
uv run xi database json mobSkills --all --output mob_skills.json
```

| Option | What |
|---|---|
| `--search Q` (`json`) | Only rows whose id is `Q` (a number, or `#257`) or whose text contains it |
| `--lang en\|jp` | The language's table (default `en`) |
| `--all` (`json`) | Include the rows the client leaves unnamed (`.`, `#n`) |
| `--output PATH` (`json`) | Write the JSON there instead of stdout |
| `--json` (`search`) | Print the matching rows as JSON |

A row is `{id, name, row, server}`. With `XI_SERVER_DIR` set, `server` is the skill's
`mob_skills` row read from `sql/mob_skills.sql` (`mob_anim_id`, `mob_skill_name`, AoE, radius,
distance, animation and prepare time, valid targets, flag, param, knockback, skillchains), and a
server row the client has no name for is listed with `name: null`.

## In the model viewer

The viewer's Database panel lists them under **Names & Text › Mob Skill Names**, with an ID
column (the `mob_skill_id`) and the name; `id:257` or `#257` finds one. It reads the DATs, or the
JSON `uv run xi mv database --only mobSkills` writes to `mv/db/`.

## The format

`ROM/27/80` and `79` are event-message tables (the zone dialog format, `xi.dialog.xi_dialog`),
not d_msg: a 24-bit length and a `0x10` flag (XOR 0x80 from byte 4), a u32 offset table, then one
cp932 name and a NUL per row. Row 0 is `#0`; unused rows are `.`. See
[`docs/dat_index.md`](../dat_index.md) and the `database` action's reference,
[README.md](README.md).

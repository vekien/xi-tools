# Spell and command menu records — adding new spell / job-ability ids

The client keeps one row per spell and per command (job ability, pet command, weapon
skill, trait, mount…) in `ROM/118/114.DAT`, and names them from the `d_msg` tables in
`ROM/181`. A spell or job ability the server knows but the client has no row for is
invisible: no menu entry, no name in the log, `/ma "Name"` cannot resolve it. The
`spell` and `command` action types of `xi dats` add those rows.

This is the **menu half** of a new ability. The **presentation half** (motion + VFX +
sound) is the `ability` action ([../ability/mixer.md](../ability/mixer.md)); the two
are independent id spaces that only the server joins (`spell_list.animation`,
`abilities.animation`).

## Where the client's ceilings are

| kind | section | record | retail rows | the client iterates | custom ids here |
|---|---|---|---|---|---|
| spell | `mgc_` (resource type 0x49) | 0x64 bytes | 1024 (ids 0–1023) | 0x400 | 1024–4095 |
| command | `comm` (resource type 0x53) | 0x30 bytes | 2816 (ids 0–2815) | 0xB00 | 2816–4095 |

The section may be any length — FFXiMain reads records straight from the loaded file
and its loops decide how many it looks at. So a **retail client shows nothing for the
custom band**; it needs its loop bounds, known-list buffers and recast tables raised
to 0x1000 by a runtime patch (an Ashita plugin that patches the client's tables
in memory). The DAT side built here is the same either way, and a client without
the patch is simply unaffected: the extra rows sit past everything it reads.

Ids are allocated **top-down from 4095** so they can never meet anything retail adds
from the bottom. Ids inside the retail band are refused unless `--force` (a retail
update could take them).

## Definition files

A spell: [schema/spell_definition.json](../../schema/spell_definition.json)

```json
{
  "schema": "xi.spell.v1",
  "name": "testspell",
  "like": 144,
  "id": "auto",
  "text": {"name_en": "Testspell", "help_en": "A test spell: deals fire damage to the target."},
  "fields": {"mp": 12, "cast": 8, "recast": 40, "element": "fire", "levels": {"BLM": 20, "RDM": 25}}
}
```

A command: [schema/command_definition.json](../../schema/command_definition.json)

```json
{
  "schema": "xi.command.v1",
  "name": "war_cry",
  "like": 547,
  "text": {"name_en": "War Cry", "help_en": "Goads all enemies in range into attacking you."},
  "fields": {"level": 30}
}
```

`like` is the retail record to clone: everything you do not name (icon, target bits,
flags, the donor's job table) is kept. `fields` are the decoded record fields:

| spell field | record | meaning |
|---|---|---|
| `kind` | `+0x02` u16 | 1 white, 2 black, 3 summon, 4 ninjutsu, 5 song, 6 blue, 7 geomancy, 8 trust (names accepted) |
| `element` | `+0x04` u16 | 0 fire … 7 dark, 15 none (names accepted) |
| `targets` | `+0x06` u16 | target bits |
| `skill` | `+0x08` u16 | 33 healing, 36 elemental, 39 ninjutsu, 43 blue … |
| `mp` | `+0x0A` u16 | MP cost |
| `cast`, `recast` | `+0x0C`, `+0x0D` u8 | quarter seconds (8 = 2 s) |
| `levels` | `+0x0E` 24 × u16 | `{JOB: level}`; jobs not named cannot learn it (replaces the donor's table) |
| `menu_index` | `+0x3E` u16 | the slot the known-spell list sorts into — allocated, not set here |
| `icon`, `icon2` | `+0x40`, `+0x42` u16 | |
| `requirements` | `+0x44` u8 | requirement bits; the client refuses the spell ("required ability not activated") until the state is on: 0x10 job point gift (only Meteor), 0x20 / 0x40 addendum and unbridled spells. Clones of such spells want 0 |

| command field | record | meaning |
|---|---|---|
| `type` | `+0x02` u8 | 1 ability, 2 pet ability, 3 weapon skill, 4 trait … (`COMM_TYPE_NAMES`) |
| `icon`, `icon2` | `+0x03` u8, `+0x04` u16 | |
| `charges` | `+0x06` u16 | |
| `targets`, `valid_targets` | `+0x0A`, `+0x13` u16 | |
| `tp` | `+0x0C` u16 | TP cost (0xFFFF = none) |
| `level`, `range`, `radius`, `aoe`, `tp_modifier` | `+0x0F`–`+0x15` u8 | |

Texts: `name_en` is required; `name_jp` falls back to it, help to `.` (what retail
puts in unnamed rows), so a grown table never shows garbage in either language.

## Commands

```bash
uv run xi dats prepare exports/menu/testspell.spell.json --project testspell --replace   # --type spell inferred
uv run xi dats build testspell --dry-run       # shows the id it would take + the server SQL
uv run xi dats build testspell                 # writes 114.DAT + names/help, records the id
uv run xi dats changelog --project testspell
uv run xi dats undo testspell                  # empties the record, puts '.' back in the names
uv run xi dats new                             # wizard: "Spell menu record" / "Command menu record"
```

`prepare` flags: `--record-id N` (instead of auto), `--menu-index N` (spells). The
build records `result.record_id` (and `menu_index`) on the action; a rebuild lands on
the same id, and `--replace` on a re-prepare keeps it unless a flag changed the
target. `--force` allows an id inside the retail band or one that already holds a
record.

What a build writes, in place with `.base` backups on the first edit (later builds
layer on the same files). Each file goes to the copy the client will actually load:
when `FFXI_PIVOT_DIR` is configured and the XIPivot overlay carries that ROM path,
the overlay's copy is read and written (a server that ships edited `114.DAT` and
spell-name tables ships them there); tables the overlay lacks stay under
`FFXI_DIR`. Close the client first — it holds these files open.

- `ROM/118/114.DAT` — the section grown to hold the id, the record encoded with the
  per-record rotation, every other byte of the file untouched (`xi.menu.xi_menu_table`).
- `ROM/181/73` + `/69` (spell names EN/JP), `/75` + `/71` (help); commands `/72` + `/68`,
  `/74` + `/70`. Tables grow by cloning a real block's shape.
- `projects/server/spells/<name>_<id>.sql` (or `commands/`): an `INSERT … SELECT` that
  clones the donor's stock row under the new id with the overrides that map (MP, cast,
  recast, level). A template — put the row where your server keeps custom content and
  set its `animation` to the number the `ability` action took.

`xi dats package` / `release` ship `114.DAT` and the edited name tables with the
project's other DATs.

## Reading and editing what is there

```bash
uv run xi ui spells search "Cure"               # id, MP, cast/recast, learnable jobs
uv run xi ui spells search "Provoke" --abilities
uv run xi ui spells export -o spells.json       # decoded fields per named record
uv run xi ui spells import edits.json           # write edited fields back (mp, levels, …)
uv run xi ui layout mnc2-pos "ROM/118/114.DAT" --block mgc_ --records --limit 20
```

## Format notes

`114.DAT` is a `menu` container: a 0x20-byte file header, then sections. Each section
is a 16-byte header — 4-char tag, u32 `(length_in_16_byte_units << 7) | resource_type`
(the length includes the header), 8 zero bytes — and its payload; the file ends with
an `end\0` section of length 1. The client asks its resource list for type 0x49 / 0x53
and reads fixed-size records from the payload; on disk record 0 starts 0x10 into the
section (verified: the decoded id at `+0` equals the index for every retail row of both
tables). Full block map: [../dats/ROM_118_114.md](../dats/ROM_118_114.md).

Tests: `tests/test_menu_table.py` (library, synthetic bytes),
`tests/test_dats_records.py` (prepare / build / undo on a synthetic install).

# Zone NPCs — the `zone_npcs` action

A zone's NPC list in a `xi dats` project: the names the client shows for the zone's NPCs
renamed, or new NPCs added, with each new or changed NPC's `npc_list` row written as proposed
SQL beside the project. xi-tools never runs the SQL.

Schema: [`schema/zone_npcs.json`](../../schema/zone_npcs.json). Library:
`xi.entity.xi_zone_npcs` (on `xi.entity.xi_custom_npc`, the zone editor's custom-NPC code).

---

## An example

```json
{
  "id": "zone_npcs.lower_jeuno",
  "type": "zone_npcs",
  "zone": 245,
  "npcs": [
    {"id": "auto", "new": true, "name": "Vekien",
     "server": {"model": 25005, "status": 0, "pos": [-33.5, 0.0, -18.25], "rot": 64}},
    {"id": 1, "name": "Wolfgang the Watchful"}
  ]
}
```

```
xi dats build jeuno --dry-run --pivot
xi dats build jeuno --pivot
```

Vekien takes the first free id in the custom band (`0x380` in Lower Jeuno, entity
`0x010F5380`) and goes into the zone's name table; the SQL gets his full `npc_list` row. The
gate guard's name becomes "Wolfgang the Watchful", and the SQL updates his `polutils_name`.

Three ways in: write the action (or `{"zone", "npcs"}`) and `xi dats prepare npcs.json
--project P` — a bare list needs `--type zone_npcs --zone N`, `--merge` adds to the action
already there; or `xi dats new` → *Zone NPCs*: the zone, then an id to rename or `new`
(name, model, status, position).

---

## NPCs

| Key | What |
|---|---|
| `id` | The zone-local id, `0x001`–`0x3FF` (entity id `0x01000000 \| zone << 12 \| id`), or `"auto"` for a new NPC. |
| `name` | The name the client shows (up to 27 cp932 bytes). |
| `new` | Add the NPC: a name record, and its full `npc_list` row in the SQL. |
| `server` | `npc_list` columns (below), or `false` to leave the server out. |
| `note` | Why — shown in the SQL. |

**Ids.** Static NPCs live at `0x001`–`0x3FF`: `0x400`–`0x6FF` is the player range and the `0x800`
bit marks dynamic entities. New NPCs belong in the custom band `0x380`–`0x3FF`, where no retail mob
sits and retail NPCs are rare (128 per zone). `"auto"` takes the first id there that neither the
zone's name table nor its event table (whose blocks begin with their actor's id) uses; the id a
build took is recorded and kept by the NPC's name on rebuilds. An explicit id below `0x380` builds
with a warning: that is where retail updates add NPCs.

**`server` for a new NPC** needs `model` (a fixed model id — the look becomes size 0 + the id) or
`look` (the 20-byte look as hex, for an equipped character). The rest default to a cutscene-only
NPC: `status` 6 (an event shows it; the zone never spawns it), `flag` 1, `speed` 50,
`entityFlags` 27, position 0,0,0. Give `status: 0` and `pos` / `rot` for an NPC that stands in the
zone. `npc_list.name` defaults to the name with spaces as `_` — it is what the server loads the
NPC's Lua by (`scripts/zones/<zone>/npcs/<name>.lua`); override with `server.name`.

**Renames** change the client's name only. The SQL sets `polutils_name` and never `name`, which
binds the NPC's script. Other columns given on an existing NPC become an `UPDATE`.

New `npc_list` rows spawn when the zone boots: restart the zone (or the server) after applying the
SQL.

---

## Which files

The name table is file id `6720 + zone` (the zone model's `+ 2600` from zone 256): 32-byte
records, `char[28]` name + `u32` entity id, sorted by id after a `none` / 0 record. New records go
in at their sorted place. The build reads the table from the target — the base install, or the
pivot folder with `--pivot` — and warns when a base-install build edits one the pivot folder has
its own copy of. The SQL goes to `<project>.sql` (or `server.sql`) between the action's markers,
as the `database` action's does.

## Builds, rebuilds and undo

Each name written is recorded per target (entity id, name before and after). A build first takes
out the names a previous build added and puts renamed ones back, then applies the NPCs — so a
rebuild converges and an NPC taken out of the action is removed. `xi dats undo` does the same and
drops the action's SQL section; a name something else changed since is left, and said.

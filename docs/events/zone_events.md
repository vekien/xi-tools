# Zone events — the `zone_events` action

Events put on a zone's NPCs, in a `xi dats` project: cutscenes (`xi.cutscene.v1`, what the zone
editor authors and `xi event decompile` writes) and plain dialogues (lines an NPC says). Each is
compiled into the zone's event table, with its lines in the zone's dialog tables. The SQL that
hides the cast's names and the Lua that starts each event are written beside the project; xi-tools
never runs them.

Schema: [`schema/zone_events.json`](../../schema/zone_events.json). Library:
`xi.event.xi_zone_events`, on the cutscene compiler (`xi.event.xi_compile`) and
`xi.event.xi_cutscene_publish` (camera scene ids, animation tags and the lint gate, shared with the
zone editor).

---

## An example

```json
{
  "id": "zone_events.ru_lude_gardens",
  "type": "zone_events",
  "zone": 243,
  "events": [
    {"name": "maat_hello", "dialogue": {"actor": "0x010F3031", "lines": ["Hmph. Another one."]}},
    {"cutscene": "cutscenes/maat_greeting.json", "camera": "ROM10/490/55.DAT"},
    {"cutscene": "maat_93.json"}
  ]
}
```

```
xi dats build ru_lude --dry-run
xi dats build ru_lude
xi dats undo ru_lude
```

The first event is a dialogue: two lines on Maat, one box each, at the next free event id. The
second is a cutscene from the zone editor with a camera: its scene DAT goes to `ROM10/490/55.DAT`
and is registered at a camera file id. The third is `xi event decompile 243 Maat --event 93`, edited:
its `eventId` is pinned to 93, so it replaces retail's event 93, and undo puts retail's back.

Four ways in:
- write the action, or `{"zone", "events"}`, and run `xi dats prepare events.json --project P`;
- prepare a single cutscene file: `xi dats prepare maat_greeting.json --project P [--zone N]
  [--camera ROM10/…]`. It becomes an event of the zone's action (`zone_events.<zone>`),
  replacing one of the same name;
- use `xi dats new`, then *Zone events*: the zone, then a cutscene file or a dialogue;
- use the old commands, which are now aliases of `prepare` + `build`:
  `xi event cutscene compile CUTSCENE [--zone N] [--project P] [--camera …] [--dry-run]` and
  `xi event dialogue new ZONE --json lines.json --actor 0x… [--paged] [--project P]`. The
  project defaults to the zone's name (`ru_lude_gardens`).

---

## Events

| Key | What |
|---|---|
| `name` | What the build records the event by. Its event id and line ids are kept under it. Default: the cutscene file's name; an inline event needs one. |
| `cutscene` | A cutscene file (`xi.cutscene.v1`), or the cutscene itself. Its `cast` and `dialog` may be files beside it. |
| `dialogue` | `{actor, lines, paged?}`: the NPC's entity id and its lines, with the escapes of `xi dialog edit` (`\n`, `\v` for the ▼ prompt, `{player}`). |
| `eventId` | `"auto"` (the default: a free id, kept on rebuilds) or an id. Default for a cutscene: its own `eventId`. An id the NPC already has is replaced. |
| `camera` | A cutscene with a camera: where its scene DAT goes (`ROM10/490/55.DAT`). Default: the cutscene's `cameraDat` (the zone editor's Camera DAT fields). |
| `cameraFileId` | The camera scene's file id, in the safe band 56941–57240 only. Default: the one a build took, else the cutscene's `cameraSceneFileId`, else the lowest free. |
| `note` | Why. |

A path is looked up beside the file holding the action (an include file's folder), then under the
project's resources, then beside the project, then in the current folder. `prepare` stores a
cutscene under the resources relative to them, and anywhere else as an absolute path.

The cutscene files CatsEyeXI's editor saved (`cexi.cutscene.v1`) are the same format and are read
as they are. A cutscene with a `zone` field (a decompiled one) must be for the action's zone.

**Server.** `flags.hideNpcNames` is not an event opcode. Nameless actors carry `npc_list.namevis`
bit `0x20` (not `0x08`, which leaves cutscene names showing), so the SQL sets it on every cast NPC
except the owner and the player, with the statement that takes it back beside it. Each event's
start script (`player:startCutscene(<id>)`, from `xi.event.xi_author.lua_stub`) goes to
`<project>.lua`, or to `server.lua` relative to the action's file; `server.emit: false` writes
neither.

---

## What a build does

For each event, in order:

1. The NPC looks and animation tags are resolved. A cast NPC's look comes from the cast entry, then
   the project's `zone_npcs` actions (a new NPC's `model` / `look`), then what the zone editor
   resolves: its custom-NPC registry, the live `npc_list`, the bundled snapshot. Tags are normalised
   against each model's own routines and the gesture bank's real inventory.
2. `compile_cutscene` compiles it against the zone's tables as the events before it left them.
   An `auto` event takes the id its last build recorded; a new one takes the NPC's highest id + 1
   that no block in the zone uses. The owner's block gets the event, and each cast NPC gets a
   one-byte `end` block under the same id. The client prepares only NPCs whose block lists the
   event, so a cast NPC with no block never appears.
3. The lint gate: sizes, jumps, selectors, message ids and menu markers are checked, as
   `xi event cutscene compile` always has. An error stops the build before anything is written.
4. The English lines go into the English table, and the same lines at the same ids go into the
   Japanese one, so either client prints them.
5. A camera scene DAT is placed at `camera` and registered at its file id in the target's tables
   (the ROM's pair, and the install's main pair when it holds the id). A path that already holds
   something other than a camera scene (`evte`) is refused: that is how a mesh went invisible
   under a camera once. A placement whose ROM has no tables in the target stops the build, the
   dry run included.

The file ids: the event table is `5820 + zone`, the English dialog `6420 + zone`, the Japanese
`6120 + zone`; from zone 256, the zone model's `+ 1100`, `+ 1700` and `+ 1400` (`xi.zone.xi_inject`).
They are resolved through the target's ROM10 pair first, then every table pair of the install (the
expansion zones live in ROM2–9).

## Builds, rebuilds and undo

Each event records, per target, its id and every change it made:

- **blocks**: each event-table block it changed, by position and entity, the block's sha1 after,
  and the splices that put back its offset, id and reference lists and its bytecode (`[p, s, m]`:
  the before value is `after[:p] + m + after[len - s:]`). A block the event created is recorded as
  `created`.
- **lines**: each dialog line it changed (bytes before and after, as `zone_dialog` records them),
  and the lines it added as one run per table.
- **camera**: the scene DAT's file id and placement.

`xi dats build` first puts back what the project's zone and database actions changed last time,
newest first, then applies them in order. Where several actions change one table (events on one
NPC, lines added to one zone by `zone_dialog` and `zone_events`), each gets back exactly the table
it changed. A rebuild writes the same bytes, and `xi dats undo` restores the tables as they were.
A dry run holds each step's writes in memory, so it plans against what the steps before it would
leave.

When something outside the project changed a table since (another project built after this one):

- **Lines added after this event's** stay. Its own lines are blanked in place rather than cut off,
  so no later line id moves, and its next build writes its lines back into those ids.
- **A block changed since**: only this event is taken out of it (its entry, and its bytecode when
  nothing follows it), with a warning. The rest of the block stays.

A camera scene an event no longer has is deleted and unregistered by the next build; `undo`
removes them all. A replaced retail event's old bytecode stays in the block as dead space until
undo. Replacing keeps the event's slot in the NPC's table, which request opcodes index.

## From the zone editor

The editor's publishes are `xi dats` actions in `dats.json` in the active editor project's folder
(`xi.zone.xi_bridge_dats`), committed with the workspace, so a collaborator can build them into
their own install with `xi dats build <workspace>/<project>/dats.json`:

- **Publish** (`zone.compileCutscene` without `dryRun`) saves the definition as
  `cutscene-defs/{zone}_{actor}_{event}.json` (its event id fixed first, so the file and the event
  share one name), makes it an event of the zone's `zone_events` action, and builds that into the
  base install. With *Publish Cutscenes to Pivot* on (the default), it also builds into the pivot
  folder. The editor's live `namevis` update still runs; the action carries the same change as SQL.
- **Delete** (`zone.deleteEvent`) takes an event the editor published out of the action and rebuilds.
  The last one going undoes and drops the action. An event from anywhere else is deleted in place,
  as before.
- **Custom NPCs** (`customNpc.create` / `update` / `delete`) keep the editor's registry and its
  live `npc_list` row. The name goes through the zone's `zone_npcs` action (id, name, model,
  status, position, as proposed SQL too), built into the base install and the pivot folder. A name
  an older editor wrote in place is taken over by the first build.

The build's output comes back to the editor as `dats.log`.

## Decompile, edit, recompile

`xi event decompile` lists in the cast every NPC whose block has the event, so a recompile keeps
their involvement. Where the compiler rewrites a retail line, it keeps what follows the line's NUL.
A decompiled event recompiled unchanged therefore changes only the owner's block (its new copy
of the bytecode), no line. See [retail-events.md](retail-events.md).

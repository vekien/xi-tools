# Ability Mixer — recipe, compose, publish

Build a new ability presentation from pieces of retail ones: the motion of one, the
effects of another, the sound of a third. The xi-model-viewer's **Ability Mixer** mode
is the UI; these are the commands it drives, usable on their own.

```bash
uv run xi ability recipe ws:1:HumeMale --out fb.json     # starter recipe from one source
uv run xi ability compose recipe.json [--out DIR] [--race Mithra] [--json]
uv run xi ability publish recipe.json [--project NAME] [--kind ja|spell|ws] [--animation N] [--subdir 20] [--dry-run]
uv run xi mv update --only abilities                      # the viewer's pick list (mv/lists/abilities.json)
```

A recipe is validated against [`schema/ability_recipe.json`](../../schema/ability_recipe.json)
(`"schema": "xi.ability.v1"`); `compose`, `publish` and `dats prepare` refuse one that
does not match, naming the field.

## Recipe

```jsonc
{
  "name": "tiger_fury",
  "sources": {
    "motion": { "spec": "ws:1",      "routine": "main" },   // race-bound: one DAT per race
    "vfx":    { "spec": "spell:144", "routine": "main" },
    "sound":  { "spec": "ja:33",     "routine": "main" }
  },
  "events": [
    { "from": "motion", "op": 5,  "ref": "b00?", "start": 10, "dur": 35, "blend": [20, 0], "loops": 1 },
    { "from": "motion", "op": 44, "ref": "b0a?", "start": 55, "dur": 70 },
    { "from": "vfx",    "op": 2,  "ref": "g004", "start": 60, "dur": 100 },
    { "from": "sound",  "op": 2,  "ref": "g0s0", "start": 60 },
    { "from": "motion", "op": 3,  "ref": "mdam", "start": 100 }
  ]
}
```

- **`sources`** — one lane per name; `spec` is anything `xi ability inspect` accepts.
  A `ws:N` spec without a race is *race-bound*: the recipe composes once per race.
- **`events`** — one command each in the new `main` routine. `start` is the absolute
  frame (the writer turns it back into the format's relative delays). `dur`, `blend`
  (`[in, out]`) and `loops` patch the command's fields; anything else in the command is
  the source's own bytes. Give `raw` (hex from `inspect --json`) or `routine` +
  `offset` to pin an exact source command; otherwise the first command in the lane's
  routine with the same `op` and `ref` is used, and a handful of ops have templates.
- Commands with no ref (locks, flinch) are copied verbatim.

## Compose

`compose` gathers, for every event, the sections its command names from that lane's
DAT: generators plus their textures, sprite sheets, meshes, curves and sound pointers
(the `xi fx copy --from` walk); sound pointers; clips and weapon traces matching the
ref (wildcards included); locally linked routines, recursively. Two sources naming the
same section differently get the later one **renamed** (`g003` → `g004`…) and every
reference in that source's copied generators and routines patched; the report lists
the renames. Then it writes one directory holding everything and a fresh `main` routine
laid out like retail (sec1 end marker, sec2 start + commands + end, sec3 end marker,
`totalDelay` = the routine's end: the source's own total when the recipe came from
`xi ability recipe`, else the last event's start + duration; each command's delay is
the gap to the next, and the first start rides on the start marker).

Output: `exports/ability/<name>/<name>[.<Race>].DAT` and a `<name>.report.json` with
sections, renames and the absolute-frame timeline. Round trip is proven in
`tests/test_ability_compose.py`: a recipe made by `xi ability recipe` from Fast Blade
composes back to the same timeline the inspector reads from the retail DAT.

A referenced generator or sound pointer that the lane's DAT does not have is an
error, not a silent drop.

## Publish

> **Running out of numbers?** The counts above are what the *client* can reach:
> the animation number becomes a DAT file id through fixed arithmetic per kind, and
> the ids that arithmetic lands on are mostly taken by other content. A client-side
> plugin can patch it so numbers at or above a threshold resolve into a reserved
> region instead; set `FX_*_BAND_*` in `.env` (see `xi_config.py`) and the publisher
> will allocate there once the retail band is full. Unset, nothing changes.

Publishing is an **`xi dats` action** (`type: "ability"`, schema
[`schema/ability.json`](../../schema/ability.json)), so a published ability sits in a
project manifest beside any gear, mount or entity actions, is rebuilt from Git by
`dats build`, listed by `dats changelog` and reverted by `dats undo`. Three ways in:

```bash
# 1. the wizard — pick "Ability" as the content type
uv run xi dats new

# 2. straight arguments — every parameter has a default the build fills in
uv run xi dats prepare exports/ability/mixer/tiger_fury.recipe.json --project tiger_fury --replace \
    [--kind ja|spell|ws] [--animation N] [--subdir 20]
uv run xi dats build tiger_fury --dry-run          # the plan: slot, file ids, server SQL
uv run xi dats build tiger_fury                    # add --pivot to build into FFXI_PIVOT_DIR

# 3. the shortcut — exactly 2., in one command
uv run xi ability publish recipe.json [--project NAME] [--dry-run] [--pivot]
```

`prepare` copies the recipe to `projects/resources/ability/<name>.recipe.json` and writes:

```jsonc
{
  "id": "ability.tiger_fury", "type": "ability",
  "kind": "auto",                              // auto = from the recipe (table below)
  "target": {"animation": "auto", "subdir": 20},
  "resources": {"recipe": "ability/tiger_fury.recipe.json"},
  "server": {"emit": true}                     // projects/server/abilities/<name>_<animation>.sql
}
```

`build` composes the recipe (one DAT, or body + two companion DATs per race for a
weapon skill), takes the animation number, places the DAT(s) under `ROM10/<subdir>/`
in the base install and registers the file id(s) — the same verbatim placement and
table patching every other action uses, `.base` backups included, then syncs the
custom table region into the pivot overlay. With `--pivot` it places and registers in
`FFXI_PIVOT_DIR` instead and skips that sync. A pivot folder that carries its own `ROM10`
tables (CatsEyeXI's does) hides a base-install publish from its client: the client reads
that folder's `ROM10` tables, and the sync copies only ids above retail, which ability
file ids never are — so publish with `--pivot` for such a client (see
[which table registers a file_id](../dats/README.md#which-table-registers-a-file_id)).
The allocation is recorded on the action (`result`: kind, animation, placements), so a
rebuild lands on the same slot and `dats undo` knows what to clear. A running client
holds the file tables in memory: publish with it closed, or restart it afterwards.

| Kind | When | Client lookup | Custom numbers | Server |
|---|---|---|---|---|
| `ja` | no lane is race-bound, or the recipe / action says `ja` (a race-bound motion is then baked from one race, below) | `file_id = 4412 + animation` | first free from **339** (retail band 4412–4750 is full) | `abilities.animation` |
| `spell` | the motion lane is a `spell:N` (or `target.kind` says so); one DAT for every race | `file_id = 0xAF0 + animation` — the client has no spell table, `spell_list.animation` rides in the magic-finish action packet | first **unregistered** id from **1012** (retail reaches 1011; the ids above are shared with other content, 92 free up to 1611) | `spell_list.animation` |
| `ws` | a lane is race-bound: `ws:N`, or a motion from a race's own animation files (below) | per-race extended bank, slots 256–271 | first slot whose body DAT is a retail dummy on every race (**264–271**) | `weapon_skills.animation`, or a `mob_skills` row with id < 256 |

### Motion from a race's own animation files

A lane whose source is a PC motion DAT — an emote, a battle pack, a dance, a weapon-skill
file picked from the character list — is mapped to every race through the client's own
per-race motion tables in FFXiMain.dll (`base[race] + index`, the lookup the client
uses; `xi.entity.anim.xi_motion_tables.motion_slot_for`). Hume Female's emote file
`ROM/37/13` is Galka's `ROM/61/8`, and so on. Such a recipe composes once per race, and
each race's DAT carries that race's own clips, an emote's waist part from its `+6`
sibling included. A race with no copy of the file, or no clip by that name, is built
without that motion, and compose, `dats build` and its dry run say so:

```text
⚠ Galka: motion2 (ROM/173/48.DAT) is HumeFemale only; built without its 3 events
```

The proven route for such motion is `ws`: one DAT per race, and with a client-side plugin's
custom band (cexislots: numbers 272–527, see *Running out of numbers?* above) there is room
for hundreds.

**Experimental — a job ability or spell with a baked motion.** Asked for `ja` or `spell`,
compose bakes the motion from one race's copy into the single DAT (`_lanes` resolves the lane
to that race — the `--race` given to `compose`, HumeMale by default — and carries its clips,
an emote's waist sibling included). This is **not verified in game** and there is no retail
precedent: no spell or job ability carries caster clips (Blue Magic's `wz*` clips animate the
monster it shows; Maelstrom's caster plays `ma2?` from its pool), the per-race copies of one
clip do not even share a joint count (`bow0` is 14 joints on Tarutaru, 16 on Hume and Elvaan,
24 on Mithra and Galka), and the PS2 client's PlayClip (`ymschdecript.cpp`, tag `0x05`)
resolves a wildcard ref such as `bow?` only from the actor's loaded motions — only an exact
ref (`bow0`) searches the routine's own DAT. Two cases differ:

- **The race base** (the first five files of the `movement` table: idle, walk, cast and
  job-ability motions such as `cm0?`, `mb0?`) is always loaded on a character, so its
  clips are named, not carried, and a job ability or spell can use them. A race whose
  base lacks the clip gets a warning.
- **A file the tables do not index** that the character list gives to some races only
  (a race's Variations files) is built for those races, with the warning above for the
  rest.

A weapon-skill slot built from motion that is not itself a weapon skill has no source
companions to copy: its waist clips ride in the body DAT, and the slot's own companion
ids keep pointing at retail's placeholders.

For `ws`, the three DATs per race (body + companion A/B waist packs) are placed and
registered; the companions are copied from the motion source's own slot for that race.
Tarutaru male and female share one bank row and are registered once.

`dats build --dry-run` prints the plan: kind, animation number, every file id with its
current occupant, and the SQL to add. The mixer shows this plan before it asks to
confirm. **Restart the client** after publishing — it caches the file tables at
startup.

The client accepts custom numbers of all three kinds from the ROM10 overlay: job abilities
past retail's 338, weapon skills in the extended slots, and spell animations past 1011.
The quickest in-game check needs no database change — on a LandSandBoat server the
`!injectaction <category> <animation>` GM command sends the action packet directly
(category 6 job ability, 3 weapon skill, 4 spell). Target a mob or NPC first: the server
takes the client's *face target*, which is not sent for yourself, and ignore the message
line the command prints (it is hardcoded). Publish with the client closed, or restart it
afterwards: a running client holds the overlay's file tables in memory and can write them
back over a registration made while it runs.

## Catalog (the viewer's pick list)

`xi mv update --only abilities` rebuilds `mv/lists/abilities.json`: every job ability
(band 0–338, named from `abilities.sql`), spell (`xi.spell`) and weapon skill (both
banks, per-race paths, named from `weapon_skills.sql` and humanoid `mob_skills.sql`),
each with its generators (audio ones separately), sound pointers, clips and total
frames. Dummies and DATs without `main` are skipped. ~1,500 entries, about a minute.
It ships with the viewer like every other list and reaches installs through the lists
manifest (see [../mv/README.md](../mv/README.md)); the mixer's *Build catalog* button
runs the same target into the viewer's own lists folder when the list is missing.

The file also carries a `base_motions` list (`xi.ability.xi_catalog.build_base_motions`):
the curated cast and job-ability motions the always-loaded race base offers — black/white/
blue magic, ninjutsu, summoning, item use, generic job ability, plus Bard songs, ranged
(bow/marksmanship) and Geomancy — each a clip group (`mb0?`, `mw0?`, `cm0?`…) read from the
real race base, with the base DAT per race for preview. The game does not name these clips,
so the song/ranged/geomancy labels are best-effort; the clip prefix, not the name, resolves
the motion (edit `BASE_MOTIONS` freely). On the `ja`/`spell` motion lane the mixer shows this short list in place of every
spell whose motion is really one of these few; a pick composes by-reference (one
race-agnostic DAT that names the clip). WS still shows every motion, baked per race. The
names are cosmetic — edit `BASE_MOTIONS` in `xi_catalog.py`; the clip prefix resolves the
motion. Motions that live in the weapon-skill bank (Tomahawk, Jump, Mug…) are **not** here:
they are race-bound and would force a `ws` publish, so they stay on the WS list.

## In the viewer

*Assets → Ability Mixer.* Left: the picker, one lane at a time — hover or arrow through
rows and the stage plays them on the current character (open Actors to pick race or an
NPC). Click or Enter takes a row for the lane. Right: the timeline (drag a block to
move it, drag a lane label to shift the lane, *snap to strike* aligns a lane's first
generator with the motion's hit frame), the selected block's numbers, and the **Parts**
of the previewed source with *solo* (play one generator alone) and *take*. **Play mix**
composes for the actor's race under `exports/ability/mixer/` and plays it; **Publish**
prepares the `xi dats` action for the recipe, shows `dats build --dry-run`'s plan, then
builds it. The Manage panel's *Use Pivot Folder* switch adds `--pivot` to that build and
to its Check, so the mix goes into `FFXI_PIVOT_DIR` instead of the game folder. Recipes
save next to the composed DATs.

Weapon-skill motion carries its own clips; the viewer resolves a routine's clip refs
against the loaded character, so picking a `ws:` motion source also sets the
character's Action to that skill (a short reload). Job-ability and spell motion
(`cm0?`, `ma2?`) plays from the character's own pool.

**Stance.** The Category / Action combos above the Actors panel are the Characters
view's own: *Battle → Battle: Sword* (etc.) or equipping a main weapon in Actors loads
that stance's `btl` clip, and in the mixer the character rests in it between the
routine's clips, so the mix is seen from the pose it plays from in game. *Basic* puts
the plain idle back.

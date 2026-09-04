# `xi audio scan` — where every sound is used

Walk **every DAT** in the install, collect every sound-effect reference, and write one
JSON that says, per sound id, which DATs play it — and what each of those DATs *is*
(a zone, an NPC model, a spell effect, a piece of gear, …).

```bash
uv run xi audio scan                          # -> exports/audio/scan.json
uv run xi audio scan --sound 5048 --stdout    # who plays se005048?
uv run xi audio scan --rom ROM3 --unused      # one ROM, plus never-referenced .spw
uv run xi audio scan --no-lookup --limit 500  # quick structural pass
```

`xi audio refs` answers the question for **one** DAT ("what does ROM/1/41 play?");
`scan` inverts it across the whole tree ("who plays se005048?").

## What it does

1. **Walks `ROM*/<sub>/<idx>.DAT`** under `FFXI_DIR` (every ROM, ~52k files). Only the
   16-byte section headers are read, plus the 12-byte head of each `0x3D`
   SoundEffectPointer (`SeSep␠␠` + `u32` sound id — see [refs.md](refs.md)), so a
   full pass takes tens of seconds. `.DAT.base` backups and raw-PNG "DATs" are skipped;
   a `0x3D` section without the `SeSep` magic is ignored.
2. **Identifies each DAT** that references a sound (`--lookup`, the default). The
   client addresses DATs by `file_id`, and each content family has its own id formula,
   so inverting FTABLE/VTABLE tells us what a path is:

   | `kind` | How it is recognised | Extra fields | Name from |
   |---|---|---|---|
   | `zone` | `0x64 + zone_id` (model), `5820/6420/6720 + zone_id` (event / dialog / NPC list); expansion bases for id ≥ 0x100 | `zone_id`, `part` | zone-name table `ROM/165/84` |
   | `zone` (curated) | path in `DEV_ZONES` / `MOG_HOUSE_NAMES` (`xi.zone.xi_list`) | `group` | curated list |
   | `entity` | the four monster modelid bands (`xi.entity.xi_core.RANGES`) | `model_id`, `category`, `source` | server `mob_pools` / `npc_list` when `XI_SERVER_DIR` is set |
   | `spell` | `0xAF0 + animation` via the spell animation table | `spell_id`, `anim_id` | spell-name table `ROM/181/73` |
   | `ability` | `4412 + animation` (0–338) | `anim_id`, `ability_id` | server `abilities.sql` when set |
   | `weapon_skill` | `4912 + animation` (0–245) | `anim_id`, `weapon_skill_id` | server `weapon_skills.sql` when set |
   | `gear` | per-`(race, slot)` group tables from `FFXiMain.dll` | `race`, `slot`, `model_id` | — |
   | `mount` | `0x19131 + mount_id` | `mount_id` | mount-name table (EN) |
   | `fishing_rod` | per-race rod table | `race`, `model_id` | — |

   A DAT the tables cannot explain falls back to what its **own sections** say:
   `zone` (has a `0x1C` ZoneDef — unnamed rooms), `model` (`0x2A` SkeletonMesh),
   `effect` (`0x05`/`0x07` only), `image` (textures only) or `unknown`.
   `kind_source` records which of the three (`curated` / `file_table` / `sections`)
   produced the answer, and `content` always carries the section-derived kind so
   you can see, for instance, that a zone's *event* DAT holds no geometry.

   Where two formulas claim one `file_id`, the explicit tables and the narrow verified
   bands win over the broad entity bands (zones → spells → abilities → weapon skills →
   gear → mounts → rods → entities). A DAT registered under several ids reports the
   identifying one as `file_id` and all of them as `file_ids`.

3. **Resolves every sound id** to its `.spw` (name + category from the bundled
   pol-utils metadata, `exists` across the seven sound roots), exactly like `refs`.

Every lookup source is best-effort. A missing name table or no server checkout is
reported in the JSON's `lookup` block (and on the console) and that family simply
comes through unnamed — the scan never fails because a name source is absent.

## Options

| Option | Effect |
|---|---|
| `--out FILE` | Write here instead of `exports/audio/scan.json` |
| `--stdout` | Print the JSON instead of writing a file (progress goes to stderr) |
| `--rom ROMn` | Only walk these ROM directories (repeatable: `--rom ROM --rom ROM3`) |
| `--sound ID` | Only report these sound ids (repeatable); every DAT is still walked, and `dats` is trimmed to the DATs that use them |
| `--no-lookup` | Skip the file-table identification: `kind` comes from the sections only, `file_id` is `null` |
| `--unused` | Append every `.spw` on disk that no DAT references |
| `--limit N` | Stop after N DATs — for a quick test |

## Output JSON

```jsonc
{
  "ffxi_dir": "…/FINAL FANTASY XI",
  "generated": "2026-09-04 12:00",
  "roms": ["ROM", "ROM2", "ROM3", …],
  "dat_count": 52113,            // DATs walked
  "dats_with_sounds": 1842,      // DATs holding at least one 0x3D reference
  "ref_count": 40120,            // 0x3D sections found
  "unique_sound_count": 6012,
  "missing_count": 15,           // referenced ids with no .spw on disk
  "lookup": {                    // null with --no-lookup
    "file_table": "10 table(s), 109,481 registrations",
    "zones": "294 named zones",
    "spells": "928 spells",
    "abilities": "339 animation slots, 260 named",
    "weapon_skills": "246 animation slots, 206 named",
    "gear": "48320 race/slot entries",
    "mounts": "256 ids",
    "fishing_rods": "8 races",
    "entities": "30,001 modelid slots, 4,120 named"
  },
  "kinds": { "zone": 380, "effect": 1120, "entity": 300, … },   // DATs with sounds, by kind

  "sounds": [                    // sorted by sound id
    {
      "sound_id": 5048,
      "folder": "se005",
      "file": "se005048",
      "spw": "se/se005/se005048.spw",
      "title": null,             // from SFXInfo, where known
      "category": "Combat Sounds",
      "exists": true,
      "located_root": "sound",
      "use_count": 3,            // 0x3D sections across all DATs
      "dat_count": 2,
      "used_in": [
        { "dat": "ROM/1/41.DAT", "file_id": 141, "kind": "zone", "name": "Lower Jeuno",
          "count": 2, "sections": ["cmb0", "cmb1"] },
        { "dat": "ROM/0/0.DAT",  "file_id": 0,   "kind": "effect", "name": null,
          "count": 1, "sections": ["5048"] }
      ]
    }
  ],

  "dats": [                      // every DAT with sounds, in ROM/subdir/index order
    {
      "dat": "ROM/1/41.DAT",
      "file_id": 141,
      "kind": "zone",
      "name": "Lower Jeuno",
      "zone_id": 41,
      "part": "model",
      "kind_source": "file_table",
      "content": "zone",         // what the sections hold
      "ref_count": 512,
      "sound_ids": [1001, 2003, 100010, …]
    }
  ],

  "unreferenced_count": 812,     // only with --unused
  "unreferenced": [ { "sound_id": 5099, "file": "se005099", "root": "sound",
                      "title": null, "category": "Combat Sounds" } ]
}
```

`used_in` carries just enough to read a sound's usage on its own (`kind` + `name`);
the full identification of each DAT (zone id and part, model id, race/slot, spell id,
…) lives once, in `dats`.

## Reading the result

- **Ambient / footstep sounds** show up under `zone` DATs; a room or private zone with
  no entry in the zone-name table appears as `kind: "zone"` with `name: null` and
  `kind_source: "sections"` (mog houses get their curated name).
- **Monster cries and attack sounds** sit in `entity` DATs. Names need the server SQL
  (`XI_SERVER_DIR`): without it you still get `model_id`, which
  `xi ftable lookup --modelid N` and `xi model search` resolve.
- **Spell / ability / weapon-skill sounds** land in their effect DATs; the many shared
  combat sounds (`se005xxx`) appear across dozens of them, which is what `dat_count`
  is for.
- A sound with `use_count` 0 never appears — it is not referenced by any DAT. Those
  are the `--unused` list: system/menu sounds and anything the client or server
  triggers by id from code rather than through a `0x3D` pointer.

## See also

- [refs.md](refs.md) — the `0x3D` SoundEffectPointer format and the single-DAT command
- [README.md](README.md) — the full `xi audio` command set
- [../reference/model-file-ids.md](../reference/model-file-ids.md) — the file_id formulas
- [../reference/named-dats.md](../reference/named-dats.md) — per-zone file-id bases
- [../fx/spells.md](../fx/spells.md) — spell → animation → effect DAT resolution

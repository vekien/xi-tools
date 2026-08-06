# `xi audio refs` — what sounds a DAT uses

Find every sound effect a DAT references and resolve each to its `.spw` file, with
name + category. Works on **effect DATs** (a spell/ability's VFX sounds), **zone
DATs** (ambient + footstep sounds), and **mob DATs**.

```bash
uv run xi audio refs <dat> [--unique] [--out FILE | --stdout]
uv run xi audio refs ROM/0/0            # an effect DAT → combat sounds
uv run xi audio refs ROM3/5/7 --unique  # a zone → ambient + footsteps
```

`<dat>` may be a ROM-relative spec (resolved against `FFXI_DIR`) or a direct path.
Output defaults to `exports/audio/refs/<stem>.json`.

## How sounds are linked in FFXI

The client never refers to sounds by filename. It uses a DAT **section type
`0x3D`** ("SoundEffectPointer"): the payload is the magic `SeSep␠␠` (8 bytes)
followed by a `u32` sound id. These sections live inside the DATs that *use*
sounds, so scanning a DAT for them tells you exactly what it plays.

```
section (type 0x3D)
  data_start + 0x00 : "SeSep  "   (8-byte magic)
  data_start + 0x08 : u32 sound_id
```

The sound id resolves to a file the same way the header `id` does
(see [format.md](format.md#id--file)):

```
folder = id // 1000  -> se{folder:03d}      file = id -> se{id:06d}.spw
```

xi reuses the generic DAT section walker
([`parse_sections`](../../src/xi/entity/anim/xi_export.py): 16-byte header,
`type = meta & 0x7F`, `size = ((meta >> 7) & 0x7FFFF) * 0x10` on write —
**19-bit** size field, mask `0x7FFFF`; some readers still use a wider mask under
the 8 MiB ceiling),
`data_start = start + 0x10`). See [`xi_section.py`](../../src/xi/common/xi_section.py).

## Where the sounds are

| DAT kind | What `refs` surfaces |
|----------|----------------------|
| **Effect** (spell/ability/weaponskill VFX) | the sounds the effect routine triggers at its keyframes |
| **Zone** | **ambient** loops (`se001` Wind, `se002` Environment), the zone's footstep + movement sounds |
| **Mob / model** | cries, attack/idle sounds attached to the model |

Verified examples:

- `ROM/0/0.DAT` → 98 references, almost all **Combat Sounds** (`se005xxx`).
- `ROM3/5/7.DAT` (a zone) → Ambient Wind + Ambient Environment + 432 Footstep
  Effects + Movement sounds.

## Output JSON

```jsonc
{
  "dat": "…/ROM/0/0.DAT",
  "ref_count": 98,
  "unique_sound_count": 94,
  "missing_count": 0,
  "sound_refs": [
    {
      "section": "5048",          // the section's 4-char DatId
      "sound_id": 5048,
      "folder": "se005",
      "file": "se005048",
      "spw": "se/se005/se005048.spw",
      "title": null,              // from SFXInfo, where known
      "category": "Combat Sounds",// from the seNNN folder (whole-tree coverage)
      "exists": true,
      "located_root": "sound"     // which sound root holds the file
    }
  ]
}
```

`--unique` collapses repeated ids; `--stdout` prints the JSON instead of writing a
file.

## Names & categories

Resolved from the bundled Windower pol-utils metadata
([`src/xi/audio/data/`](../../src/xi/audio/data/)):

- **`category`** — from the `seNNN` folder, which is the game's own grouping and
  covers the *whole* tree: Spell Sounds (`se003`), Combat Sounds (`se005`),
  Skillchain Sounds (`se019`), Weapon Skill Effects (`se032`), Footstep Effects
  (`se100`–`se128`), Monster SFX (`se201`+), and so on.
- **`title`** — a per-sound name where pol-utils provided one (partial; system /
  menu sounds and some categories).

## From a spell *name* to its sounds

A spell's effect lives in its own DAT. Resolution (see [../fx/spells.md](../fx/spells.md)):

```
animationIndex = SpellAnimationTable[spellIndex]   # server / LSB spell_list → bundled table
fileId         = 0xAF0 + animationIndex            # NOT spellIndex directly
datPath        = FileTable[fileId]                 # e.g. ROM/x/y.DAT
```

xi maps spell name/index → effect DAT via `spell_catalog()` /
`resolve_spell_dat_rel()` in [`xi.spell`](../../src/xi/spell/xi_spell.py)
(`FILE_TABLE_OFFSET = 0xAF0`). The editor also exposes this as the zone spell list
(`zone.spellList` / Spells browser). Point `audio refs` at the resolved DAT to list
every sound that effect plays.

## See also

- [format.md](format.md) — the `.bgw`/`.spw` binary format and codec
- [README.md](README.md) — the full `xi audio` command set
- [../fx/spells.md](../fx/spells.md) — spell → animation DAT resolution
- [../sounds/footsteps.md](../sounds/footsteps.md) — the footstep half of the sound system

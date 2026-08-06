# xi fx

Inspect and edit the **visual effects** baked into a DAT — fountain spray, lamp
glow, fire, smoke, bubbles, sky clouds, point lights. Mirrors the `xi fx`
command group (`src/xi/fx/`).

Effects are sections of **type `0x05`** — FFXI calls them *generators* (the
engine's Generator/Scheduler subsystem owns them). They live **inside the DAT**,
not in separate files; a zone like Lower Jeuno (`ROM/1/41`) carries ~300 of them.
Each is a small **FourCC-named** record (`tki5`, `lt37`, `grid`, …) that holds its
own local position and references a mesh, textures, and sub-effects **by name**.
`0x05` sections are **unencrypted**, so listing and editing are direct byte
operations — no cipher, no reimport step.

`<dat>` is a path or a ROM-relative spec like `ROM/1/41`.

## Commands

| Command | Doc | What it does |
|---|---|---|
| `fx json` | [json.md](json.md) | Dump every effect (+ decoded params, optionally raw opcodes) to JSON |
| `fx set` | [set.md](set.md) | Edit params in place — position / scale / color / draw-distance / spawn / count / autorun |
| `fx copy` | [copy.md](copy.md) | Duplicate an effect — same-DAT, or cross-DAT (brings its deps) |
| `fx delete` | [delete.md](delete.md) | Remove effect(s) by exact name or name-prefix |
| `fx export` | [export.md](export.md) | Export an effect's 3D mesh + material/texture + decoded params as a bundle |
| `gui weapon` | [editor.md](editor.md) | Serve the browser-based particle/weapon-effect editor |

`xi zone json --fx` provides the same effect dump from the zone command group.

## Typical workflows

```bash
# See what's in a zone, then dump the full param set for offline review
uv run xi fx json ROM/1/41                     # JSON dump of effects + decoded params

# Recolour + enlarge + extend the draw distance of the fountain spray
uv run xi fx set ROM/1/41 tki --color 00FF00 --scale-mul 4 --range 100 500

# Transplant a Castle Zvahl wall torch onto the fountain (brings its deps)
uv run xi fx copy ROM/1/41 lb09 --from ROM/0/60 --at-pos -20.7 -2 5.2 --name fir0

# Strip the whole fountain effect set
uv run xi fx delete ROM/1/41 tki awa grid

# Export one effect's mesh + texture + params for inspection
uv run xi fx export ROM/1/41 tki5
```

## How effects work (the knowledge behind these commands)

- **Effects live INSIDE the DAT** as `0x05` sections, alongside the zone's geometry
  (`0x2E`), placements (`0x1C`), and textures (`0x20`). They are *generators*, not
  "Events" (NPC dialogue) and not "Sequences" (scheduler chains) — those are
  separate systems. A sequence can *trigger* a generator, but the spray itself is a
  generator.
- An effect references its mesh / textures / sub-effects **by 4-char FourCC**, never
  by byte offset. The meshes effects place are often **orphan `0x2E` meshes** with no
  `0x1C` placement record, so they appear *only* via their effect — and are
  **invisible in `xi zone export`** (this is why the fountain puddle never showed
  up in the GLB).
- **Coordinates: `−Y` is UP.** FFXI world space is Y-down. The fountain jets sit at
  `y=−7.2`; a positive `y` buries an effect underground. To raise something, make
  `y` *more negative*. (Zone export/import handles this via the correction node; it
  only bites when editing raw effect positions directly.)
- **Editing keeps a `<dat>.base` backup.** Restore the whole DAT from it to undo, or
  run `xi zone reset`.

## How effects are labelled

`fx json` annotates each effect with a human label from a curated,
editable registry, [`src/xi/fx/fx_library.json`](../../src/xi/fx/fx_library.json).
Classification signals, best-first:

1. **exact name** (`names`) — e.g. `grid` = "Water surface".
2. **the mesh it places** (`meshes`) — the strongest *content* signal: anything
   placing `ligh` is a Light, `rnp#` a Lamp post, `clod` a Cloud, `sibj`/`awan`/`suim`
   the fountain. Works regardless of the effect's own name.
3. **the texture it references** (`textures`) — for **mesh-less sprite effects**
   (e.g. fire billboards a texture directly): anything referencing the `fire`
   texture is fire.
4. **name prefix** (`prefixes`) — `lt`* = Light, `pl`* = Point light, `cld`* = Cloud,
   `sun`/`moon`/`star` = sky.

A *verified* classification beats a tentative one (a trailing `?` in the listing).
`(unidentified)` = no library match yet. Extend the library freely — add to
`names`/`meshes`/`prefixes`/`textures`; no code change needed.

## Reference

- **[effects.md](effects.md)** — the `0x05` byte format deep-dive,
  the four opcode sub-sections, the xim-validated param map, the fountain case study,
  and cross-DAT transplant notes.
- **[effect_system.md](effect_system.md)** — system-level view: how a
  spell cast resolves to an effect, the `0x07` EffectRoutine sequencer, geometry/
  texture/keyframe binding, `autoRun`, and authoring a brand-new spell visual.
- **[spells.md](spells.md)** — the **spell VFX** implementation reference: the verified
  spell→DAT resolution chain (`0xAF0 + animIndex`, the no-XOR name-table gotcha), the
  `xi.spell` module + `zone.spellList`/`zone.spellVfx` RPCs, the editor's Spells
  asset-browser spawn feature + `SpellRoutinePlayer`, and the particle-engine gap
  analysis (editor JS vs the UE5 C++ engine).
- **[../reference/dat_sections.md](../reference/dat_sections.md)** — section type-code table
  (`0x05` ParticleGenerator, `0x07` EffectRoutine, `0x19`/`0x1F`/`0x21` geometry, …).
- **[../dats/ROM_1_41.md](../dats/ROM_1_41.md)** — Lower Jeuno section histogram +
  full `0x05` effect catalog (the test bed used throughout these docs).
- **[../pipelines/rom_1_41_fountain_removal.md](../pipelines/rom_1_41_fountain_removal.md)**
  — end-to-end "remove the fountain" recipe.
- **[../dats/fx.md](../dats/fx.md)** — the original single-page `xi fx` overview
  (superseded by this folder; kept for the narrative walkthrough).

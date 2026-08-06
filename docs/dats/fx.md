# `xi fx` — visual effects in a DAT

Inspect and edit the **visual effects** baked into a DAT: fountain spray, lamp
glow, fire, bubbles, clouds, etc. Core commands:

```
xi fx json   <dat>                 # dump all effects + decoded params to JSON
xi fx delete <dat> <name>...       # remove effect(s) by name or name-prefix
xi fx set    <dat> <name>... ...   # edit params (pos/scale/color/draw-distance)
xi fx copy   <dat> <src> [--from/--at/--at-pos/--offset/--name]  # duplicate (cross-DAT too)
xi fx export <dat> <effect>        # export its 3D mesh + material/texture + JSON bundle
```

`<dat>` is a path or a ROM-relative spec like `ROM/1/41`. Implemented in
the `src/xi/fx/` package (`xi_core.py` shared; `xi_list.py`/`xi_dump.py`/`xi_delete.py`/`xi_set.py`/`xi_copy.py`/`xi_export.py` per command). For the byte-level effect format see
[../fx/effects.md](../fx/effects.md); for the Lower Jeuno catalog see
[ROM_1_41.md](ROM_1_41.md).

---

## `xi fx json <dat>`

Emits every effect in the DAT as JSON:

```json
{
  "effects": [
    {"name": "lt37", "size": 416, "label": "Light", "mesh": "ligh", "position": [6.29, -4.37, 42.46]},
    {"name": "grid", "size": 384, "label": "Water surface (puddle)", "mesh": "suim", "position": [-16.15, -0.39, 5.41]}
  ]
}
```

Each entry includes the **FourCC name**, **section size**, **label**, decoded params,
and, when the effect places a mesh or texture, the local position that follows it.

- A trailing **`?`** = the label is a tentative guess (not yet confirmed).
- **`(unidentified)`** = no entry in the effect library yet.

### How effects work

- **Effects live INSIDE the DAT**, not in separate files. They are sections of
  **type `0x05`** — FFXI calls them *generators* (the engine's Generator/Scheduler
  subsystem owns them). A zone like Lower Jeuno has ~300 of them.
- Each effect is a small, **FourCC-named** record (e.g. `lt37`, `grid`, `tki1`)
  that carries its own **local position** and **references a mesh** (the particle
  quad / glow billboard / cloud), textures, and sub-effects **by name**. Example:
  the fountain's `tki1`–`tki5` each place the `sibj` splash mesh at a spout.
- The meshes an effect places are often **orphan `0x2E` meshes** with no `0x1C`
  placement record — so they appear *only* via their effect and are **invisible in
  `xi zone export`** (this is why effect geometry like the fountain puddle never
  showed up in the GLB).
- Effects are **unencrypted**, so listing/editing is direct (no cipher).
- They are **not** "Events" (NPC dialogue/cutscenes) and not "Sequences" (scheduler
  command chains) — those are separate systems. An effect is a generator.

### How `json` labels effects

Labels come from a curated, editable registry, `src/xi/fx/fx_library.json`
(`classify()` in `xi_core.py`):

1. **Name candidate** — exact `names` hit, else longest matching `prefixes`
   (prefixes feed name, not a separate final step).
2. **Mesh / texture candidates** — `meshes` / `textures` maps from what the
   effect places or references.
3. **Verified wins** — first verified among mesh → tex → name.
4. **Else** name → mesh → tex.

Effects with **no mesh/texture reference** (pure particle/weather generators)
can't be content-classified yet. Extend the library freely — add to
`names`/`meshes`/`textures`/`prefixes`; no code change needed.

---

## JSON output

Prints JSON to **stdout** by default — useful for diffing, bulk review, or piping.
Use `--output PATH` to write it to disk (e.g. `exports/fx/…`).

```sh
uv run xi fx json ROM/1/41
#  -> stdout  (317 effects: light:99 prop:74 sky:18 water:16 …)
uv run xi fx json ROM/1/41 --output exports/fx/rom_1_41.json
```

Each effect entry:

```json
{
  "name": "tki5", "offset": "0x115970", "size": 464,
  "label": "Water splash", "category": "water", "verified": true,
  "mesh": "sibj", "position": [-17.86, -7.2, 5.41],
  "params": {
    "attach": "None", "color_rgb": "505050", "scale": [0.3, 1.5, 0.3],
    "draw_distance": 15.0, "emission_variance": 5, "spawn_interval": 39,
    "count": 0, "autorun": true
  }
}
```

`params` are the xim-validated fields the decoder can locate:

| field | source | meaning |
|---|---|---|
| `attach` | attachFlags low 4 bits (section-start `+0x10` = data-start `+0x00`) | how it binds — `None` = world-positioned, else actor/sun/moon |
| `color_rgb` | sec2 `0x16` ColorSetup | particle tint (BGR in bytes) |
| `scale` | sec2 `0x0F` ScaleInitializer | x,y,z particle size |
| `draw_distance` | sec1 `0x0A` GeneratorCull | max emit distance (cull range) |
| `emission_variance` | `@0x74` | random jitter on the spawn interval |
| `spawn_interval` | `@0x76` framesPerEmission | frames between spawns (engine adds +1) |
| `count` | `@0x78` particlesPerEmission | particles per spawn (`0` = continuous singleton) |
| `autorun` | `@0x79` genFlags bit `0x10` | auto-spawns on load (vs scheduler-triggered) |

Effects missing a tag simply omit that key. The top of the file has the DAT path,
total `count`, and a `categories` histogram.

**`--opcodes`** — add the raw 4 opcode sub-sections per effect (large output). Each
entry is `{op, name, size, alloc, hex[, floats]}`; opcode names come from xim's
sec1–sec4 handlers (unknown codes show as `op_XX`). This is the full instruction
stream — generator updaters (sec1), particle initializers (sec2), particle updaters
(sec3/4) — and is always included in `xi fx export`'s per-effect JSON.

---

## `xi fx delete <dat> <name>...`

Removes one or more effects and rebuilds the DAT.

```sh
uv run xi fx delete ROM/1/41 grid              # one effect (the puddle)
uv run xi fx delete ROM/1/41 tki awa grid      # prefixes: tki1-5 + awa1-6 + grid
uv run xi fx delete ROM/1/41 tki --dry-run     # preview, write nothing
```

- Each `<name>` matches by **exact FourCC or prefix** (so `tki` removes `tki1`–
  `tki5`). Multiple names allowed.
- `--dry-run` prints what *would* be removed without writing.
- Sections are spliced out from the end (offsets stay valid); the engine tolerates
  the resize. **Verified**: removing the fountain set loads clean in-game.
- A pristine **`<dat>.base`** backup is created (shared with `xi zone import`).
  Restore the whole DAT from it to undo.

---

## `xi fx set <dat> <name>... [params]`

Edit an effect's parameters in place (by exact name or prefix). Params are located
by their **opcode tag**, so the same edit works on any effect using the shared
format — not just the fountain.

```sh
# recolour + enlarge + extend the draw distance of the fountain spray
uv run xi fx set ROM/1/41 tki --color 00FF00 --scale-mul 4 --range 100 500

# move a single effect
uv run xi fx set ROM/1/41 grid --pos -16 -0.4 5
```

| flag | effect | how it's found |
|------|--------|----------------|
| `--pos X Y Z` (alias `--at-pos`) | local position (`--at-pos` matches `/xi pos`) | 3×f32 after the placed-mesh/texture ref |
| `--scale X Y Z` | scale (width height depth) | tag `0f 04` |
| `--scale-mul F` | multiply current scale by F | tag `0f 04` |
| `--color RRGGBB` | tint colour (or `r,g,b`) | opcode `0x16` ColorSetup (written B,G,R) |
| `--range NEAR FAR` | draw distance (sets maxEmitDistance = FAR; NEAR unused) | opcode `0x0A` GeneratorCull |
| `--spawn-interval N` | framesPerEmission — frames between spawns | header u16 `@0x76` |
| `--flow F` | multiply texture/position flow speed by F (observed; opcode TBD) | `02e4`/`0708` (.Y) |
| `--count N` | particlesPerEmission (0–255) | header u8 `@0x78` |
| `--autorun / --no-autorun` | set/clear the autoRun flag (auto-spawn vs scheduler-triggered) | header u8 `@0x79` bit `0x10` |

Notes:
- `--pos` on a **prefix** sets *every* matched effect to the same point — use a
  single name for position; `--scale`/`--color`/`--range` apply uniformly and are
  fine across a group.
- In-place, unencrypted, keeps `<dat>.base`. Confirmed in-game on the fountain:
  green spray, 4× taller/wider, visible from far.
- Colour byte order is written **B,G,R** (FFXI convention) — the green channel is
  confirmed; pure red vs blue ordering is assumed, not yet verified.

---

## `xi fx copy <dat> <src> [params]`

Duplicate an effect — within a DAT, or **cross-DAT** with `--from`. The copy is
inserted as a new section (or `--replace`s an existing slot). With `--from`, any
**dependencies the effect references are copied too** — its texture (`0x20`), the
texture companion (`0x21`), sub-resources (`0x19`), and meshes (`0x2E`) — that the
destination lacks.

```sh
# same-DAT
uv run xi fx copy ROM/1/41 tki5 --offset 6 0 0          # 6th fountain jet
# cross-DAT: a Castle Zvahl wall torch onto the fountain (brings fire texture + light sub-resources)
uv run xi fx copy ROM/1/41 lb09 --from ROM/0/60 --at-pos -20.7 -2 5.2 --name fir0
# place at an existing object's spot, nudged up 2
uv run xi fx copy ROM/1/41 lb09 --from ROM/0/60 --at funsui --offset 0 -2 0 --name fir0
```

| flag | meaning |
|------|---------|
| `--from SRC_DAT` | copy `src` from another DAT (path or ROM spec); brings its deps |
| `--name NEW` | FourCC for the copy (default: auto-derived, e.g. `tki5`→`tki6`) |
| `--replace E` | overwrite effect `E`'s slot (copy inherits its spawn behaviour) |
| `--pos X Y Z` / `--at-pos X Y Z` | absolute position (`--at-pos` matches the `/xi pos` output) |
| `--at REF` | place at an existing **effect** (FourCC) or **placed object** (mesh id) |
| `--offset DX DY DZ` | nudge from the source / `--at` / `--pos` |

- **`/xi pos`** in-game (the `xitools` Ashita addon, `addon/xitools/`) prints a
  ready `--at-pos X Y Z` line and a full `xi fx copy … --at-pos …` command — stand
  where you want the effect, paste, done.
- **Coordinates: `−Y` is UP** (FFXI world is Y-down). The fountain jets sit at
  `y=−7.2`; a positive `y` buries an effect underground. Move things up by going
  *more negative*.
- Effects are enumerated **sequentially**, so same-DAT copies/native effects
  spawn. **Cross-DAT transplant works when the full dependency set comes along**
  (texture `0x20`, SpriteSheetMesh `0x21`, keyframe `0x19`, meshes `0x2E`) —
  including boss/ability effects. Verified: `a133` from ROM/0/73 (dungeon fire,
  `autoRun=true`) renders at the Lower Jeuno fountain once `fire(0x21)` is
  included. Only generators that are genuinely **`autoRun=false`** need
  `--replace` onto a spawning slot or `fx set --autorun` afterward. Verified:
  a Castle Zvahl torch also renders as a standalone fire at the fountain.
- Keeps a `<dat>.base` backup; the DAT grows (rebuilt, all sections preserved).

---

## `xi fx export <dat> <effect>`

Export an effect's referenced **3D mesh** (with materials + texture) and its
decoded params as a bundle, into `exports/fx/<rom>/<effect>/` (or `--out DIR`):

```sh
uv run xi fx export ROM/1/41 tki5
#  -> tki5.glb   (mesh 'sibjun3', material 'funsui  sib1_alpha', texture embedded)
#     funsui_sib1.png
#     tki5.json   (the effect's decoded params)
```

- The mesh is whatever the effect places (fountain `tki` → `sibj` splash quad,
  lamp `lt` → its glow mesh). Geometry + UVs + material(s) go into the `.glb`
  (texture embedded), with the texture also written alongside as PNG.
- **Mesh-less sprite effects** (e.g. fire, which billboards a texture directly)
  have no 3D mesh — they export the referenced texture as PNG + the JSON.
- Reuses the zone GLB builder, so the `.glb` opens in Blender/any glTF viewer.

### Caveat — ordering with `zone import`

`fx delete` edits the DAT **in place** (it does not reset to `.base`), so it stacks
on top of mesh-merge/placement edits. But `xi zone import` **rebuilds from
`.base`**, which still contains the effects — so re-running `zone import` **re-adds**
deleted effects. Run `fx delete` *after* import. (Effect editing isn't wired into
the import pipeline yet.) See the worked recipe in
[../pipelines/rom_1_41_fountain_removal.md](../pipelines/rom_1_41_fountain_removal.md).

---

## Related

- [../fx/effects.md](../fx/effects.md) — `0x05` byte format, the
  generator/scheduler subsystem, the fountain case study.
- [ROM_1_41.md](ROM_1_41.md) — Lower Jeuno effect catalog.
- [../pipelines/rom_1_41_fountain_removal.md](../pipelines/rom_1_41_fountain_removal.md)
  — end-to-end "remove the fountain" recipe.

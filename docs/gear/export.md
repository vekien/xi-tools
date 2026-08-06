# xi gear export

Export a gear model (skeleton + mesh + textures) to GLB and optionally FBX.

Resolves `race / slot / model_id` to the correct DAT via the embedded gear tables,
then uses the same pipeline as `xi mesh export` — the binary format is
identical between gear and entity models.

---

## Usage

```
uv run xi gear export <race> <slot> <model_id> [--fbx] [--output DIR]
```

| Argument / Option | Description |
|---|---|
| `<race>` | One of the 8 playable races (see below) |
| `<slot>` | Equipment slot: `face head body hands legs feet main sub ranged` |
| `<model_id>` | Integer model index within that race/slot |
| `--fbx` / `--no-fbx` | Also export a texture-embedded FBX via Blender (default: on) |
| `--output DIR` | Output directory (default: `exports/gear/rom/<sub>/<file>/`, mirroring the DAT path like every other xi export) |

---

## Races

`HumeMale` · `HumeFemale` · `ElvaanMale` · `ElvaanFemale` · `TaruMale` · `TaruFemale` · `Mithra` · `Galka`

---

## Examples

```bash
# export Hume Male body model 0 (base body mesh)
uv run xi gear export HumeMale body 0

# export Mithra hands slot model 5, no FBX
uv run xi gear export Mithra hands 5 --no-fbx

# export Galka main-hand weapon model 1 to a custom location
uv run xi gear export Galka main 1 --output /tmp/galka_sword
```

---

## Output files

```
exports/gear/rom/<sub>/<file>/     # e.g. mirrors the resolved DAT path
  *.glb              ← glTF — Blender, three.js, etc.
  *.fbx              ← texture-embedded FBX for C4D / Maya  (--fbx only)
  *.png              ← texture sheets (also embedded in the GLB)
  *.json             ← sidecar metadata (race/slot/model_id, sections, textures)
  *_schema.json      ← `xi dats prepare`-ready mesh action that re-injects the
                       (edited) GLB back into this gear DAT
```

The `*_schema.json` is emitted by default (like `xi mesh export`); set the
`SCHEMA_GENERATION=0` env var to suppress it. It is a `xi.mesh.v1` action with
`source`/`target` pointing at the gear DAT and no `model_id`, so a `dats build`
rebuilds the geometry in place — the same round-trip as `xi gear import`.

---

## Finding model IDs

Use `xi gear json` to see all model IDs for a race/slot:

```bash
uv run xi gear json
# exports/gear/gear_HumeMale.json  →  [{slot,model_id,file_id,dat}, ...]
```

The `model_id` is the value stored in the item database
(`item.head`, `item.body`, etc. in the LSB item table).

---

## Notes

- Weapon slots (`main`, `sub`, `ranged`) have **race-specific DATs** — the same
  `model_id` resolves to a different DAT for each race (each race's skeleton differs).
- The `ffxi_root_correction` node is included in the GLB so the model arrives
  correctly oriented in the DCC tool.
- To re-import an edited mesh, use `xi gear import`.

---

## Related commands

- **`xi gear import`** — re-import an edited GLB back into the gear DAT
- **`xi gear json`** — list all gear model IDs and their DAT paths
- **`xi mesh export`** — same pipeline, for monster/NPC models

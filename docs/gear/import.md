# xi gear import

Import an edited GLB back into a gear model DAT.

Resolves `race / slot / model_id` to the correct DAT via the gear tables, then uses
the same mesh-import pipeline as `xi mesh import`. GLB only — convert FBX
to GLB in Blender first if needed.

---

## Usage

```
uv run xi gear import <race> <slot> <model_id> <glb_path> [OPTIONS]
```

| Argument / Option | Description |
|---|---|
| `<race>` | One of the 8 playable races |
| `<slot>` | Equipment slot: `face head body hands legs feet main sub ranged` |
| `<model_id>` | Integer model index within that race/slot |
| `<glb_path>` | Path to the edited `.glb` file (GLB only) |
| `--mesh-name NAME` | Override the target mesh section name |
| `--double-sided` / `--single-sided` | Face culling (default: double-sided) |
| `--scale FLOAT` | Uniform scale factor applied to the imported geometry |
| `--rotate-y DEG` | Rotate mesh around Y axis before import (degrees) |

---

## Typical workflow

```bash
# 1. Export the model
uv run xi gear export HumeMale body 0

# 2. Edit in Blender
#    exports/gear/HumeMale/body/0/*.glb

# 3. Export from Blender as GLB, then import
uv run xi gear import HumeMale body 0 hume_body_edited.glb
```

---

## Examples

```bash
# basic import
uv run xi gear import HumeMale body 0 edited.glb

# import with no double-sided faces
uv run xi gear import Mithra hands 5 hands.glb --single-sided

# import with a scale fix
uv run xi gear import Galka main 1 sword.glb --scale 0.5
```

---

## Notes

- The import replaces **all mesh sections** in the target DAT with the GLB geometry.
- Texture sections referenced by name are preserved; new textures added in the GLB
  are encoded and inserted as new `0x20` sections automatically.
- Writes the DAT back in place under `FFXI_DIR` — the pristine bytes are kept in a
  `<dat>.base` backup.
- The `ffxi_root_correction` node exported by `xi gear export` is automatically
  handled — the importer recovers the FFXI coordinate frame without manual adjustment.

---

## Related commands

- **`xi gear export`** — export the model to GLB/FBX first
- **`xi gear json`** — list all model IDs and DAT paths
- **`xi mesh import`** — same pipeline, for monster/NPC models

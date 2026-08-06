# xi object export

Export a **single named zone mesh** to GLB (+ optional FBX), without the full zone
context. Smaller file, faster to open in a DCC tool.

---

## Usage

```
uv run xi object export <dat> <mesh_name> [--fbx] [--output DIR]
```

| Argument / Option | Description |
|---|---|
| `<dat>` | Zone DAT or ROM-relative spec (e.g. `ROM/1/41`) |
| `<mesh_name>` | Exact mesh name to export |
| `--fbx` | Also produce a texture-embedded `.fbx` via Blender |
| `--output DIR` | Output directory (default: `exports/object/<rom>/<mesh_name>/`) |

---

## Examples

```bash
# export a single building section
uv run xi object export ROM/1/41 block03

# export with FBX for C4D
uv run xi object export ROM/1/41 hasi --fbx

# export to a custom location
uv run xi object export ROM/1/41 block03 --output /tmp/block03
```

---

## What it does

1. Parses the full zone (decrypt, mesh, textures) — only takes the requested mesh.
2. Writes a GLB with the same `ffxi_root_correction` node as a full `xi zone export`.
3. Optionally converts to FBX via Blender (`--fbx`).

The `ffxi_root_correction` node is important: it means the mesh arrives in the DCC
tool in the correct orientation and can be re-imported with `xi object replace`
without any manual axis adjustment.

---

## Finding mesh names

List the meshes/placements in a zone with `object list`, or browse them visually:

```bash
# every placement name + index in the zone
uv run xi object json ROM/1/41

# or the web level editor — the Objects panel shows every mesh name
uv run xi gui zone
```

(`xi zone json` lists the *zones* and their DAT paths.)

LOD suffixes (`_l`, `_m`, `_h`) are resolved automatically: if you pass `block03`
and only `block03_h` exists, the high-detail variant is exported.

---

## Output files

```
exports/object/1/41/block03/
  block03.glb      ← three.js / Blender / everything that can open glTF
  block03.fbx      ← (--fbx only) texture-embedded for C4D / Maya
  *.png            ← texture sheets embedded in the GLB (also written as PNGs)
```

---

## Related commands

- **`xi object replace`** — import an edited GLB back as the mesh geometry
- **`xi zone export`** — export the whole zone (all meshes + placements)
- **`xi object delete`** — blank a placement so it stops rendering

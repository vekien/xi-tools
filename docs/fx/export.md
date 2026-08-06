# xi fx export

Export one effect's referenced **3D mesh** (with materials + texture) and its decoded
params as a bundle: `<effect>.glb` + `<texture>.png` + `<effect>.json`. Reuses the
zone GLB builder, so the `.glb` opens in Blender or any glTF viewer.

---

## Usage

```
uv run xi fx export <dat> <effect> [--out DIR]
```

| Argument / Option | Description |
|---|---|
| `<dat>` | DAT path or ROM spec (e.g. `ROM/1/41`) |
| `<effect>` | FourCC of the effect to export |
| `--out DIR` | Output directory (default: `exports/fx/<rom>/<effect>/`) |

---

## Examples

```bash
# export the fountain splash effect's mesh + texture + params
uv run xi fx export ROM/1/41 tki5

# custom output directory
uv run xi fx export ROM/1/41 lt37 --out /tmp/lamp_light
```

---

## Example output

```sh
uv run xi fx export ROM/1/41 tki5
#  Exported tki5: mesh 'sibjun3' -> exports/fx/rom/1/41/tki5/tki5.glb
#    3 file(s) -> exports/fx/rom/1/41/tki5
```

```
exports/fx/rom/1/41/tki5/
  tki5.glb        ← the placed mesh (geometry + UVs + material(s), texture embedded)
  funsui_sib1.png ← the referenced texture, also written alongside
  tki5.json       ← the effect's decoded params (incl. raw opcodes)
```

---

## Notes

- The mesh exported is **whatever the effect places** — the fountain `tki` → `sibj`
  splash quad, a lamp `lt` → its glow billboard. Geometry, UVs, and material(s) go
  into the `.glb` (texture embedded), with the texture also written alongside as PNG.
- **Mesh-less sprite effects** (e.g. fire, which billboards a texture directly) have
  no 3D mesh — they export the referenced texture as PNG + the JSON only.
- If an effect references neither a mesh nor a texture, only the JSON is written.
- The `<effect>.json` is the same per-effect entry as [`xi fx json`](json.md), and
  it **always includes the raw opcode sub-sections** (`--opcodes`) — the full
  instruction stream for that effect.
- This command is read-only — it never modifies the source DAT.

---

## Related commands

- **[`xi fx json`](json.md)** — inspect all effects' params as JSON
- **[`xi fx copy`](copy.md)** — duplicate / transplant the effect instead of exporting
- **`xi zone export`** — same GLB builder, for a whole zone (placed `0x1C` objects)

> Note: effect-placed meshes are often **orphan `0x2E` meshes** with no `0x1C`
> placement, so they never appear in `xi zone export`'s GLB — `fx export` is how you
> pull that geometry out. See [effects.md](effects.md#orphan-meshes-invisible-in-zone-export).

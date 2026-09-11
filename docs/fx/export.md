# xi fx export

Export one effect's referenced **3D mesh** (with materials + texture) and its decoded
params as a bundle: `<effect>.glb` + `<texture>.png` + `<effect>.json`. Reuses the
zone GLB builder, so the `.glb` opens in Blender or any glTF viewer.

---

## Usage

```
uv run xi fx export <dat> [<effect>] [--out DIR]
uv run xi fx export <dat> --assemble [--all-layers] [--out DIR]
```

| Argument / Option | Description |
|---|---|
| `<dat>` | DAT path or ROM spec (e.g. `ROM/1/41`) |
| `<effect>` | FourCC of the effect to export; omit to export every effect in the DAT |
| `--assemble` | Every particle mesh the DAT's generators draw, in **one** GLB (see below) |
| `--all-layers` | With `--assemble`: include triggered generators and `0x21` sprite cards |
| `--out DIR` | Output directory (default: `exports/fx/<rom>/<effect>/`) |

---

## Examples

```bash
# export the fountain splash effect's mesh + texture + params
uv run xi fx export ROM/1/41 tki5

# custom output directory
uv run xi fx export ROM/1/41 lt37 --out /tmp/lamp_light

# the whole Home Point crystal — every idle layer, one GLB
uv run xi fx export ROM/3/25 --assemble
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

## `--assemble` — the whole object in one file

An effect-only entity's appearance is spread across a dozen generators, and no
single one of them looks like anything. `--assemble` builds them into one GLB,
each mesh at its generator's own position and scale:

```sh
uv run xi fx export ROM/3/25 --assemble
#  Assembled 6 layer(s) over 4 mesh(es) (bind, naka, sil, wa) [autorun]
#    -> exports/fx/rom/3/25/rom_3_25.glb
```

Two filters make the default *the object at rest*, and `--all-layers` turns both
off:

- **autorun.** An ambient entity splits its generators between an idle routine and
  a triggered one, and the `genFlags` autorun bit (`0x10`) separates them exactly —
  every generator in the Home Point's `aper` idle routine sets it, none in its
  `bind` activation routine does. A spell DAT has no autorun generator at all (a
  routine fires it), so there everything is drawn.
- **`0x21` sprite cards** are the billboard for one *emitted particle*, so a static
  copy at the origin is a flat square through the middle of the model.

The GLB is a **snapshot at rest**: each layer's static `0x09` rotation is baked
into its placement, but the spin (`0x0B`) and the UV scroll (sec3 `0x27`/`0x28`)
are not animated into the file. `xi fx json` reports both per generator, and the
model viewer plays them live — see
[effects.md → What MOVES an effect](effects.md#what-moves-an-effect).

## Notes

- The mesh exported is **whatever the effect places** — a `0x1F` ParticleMesh for
  spell/ability/NPC effects, or a `0x2E` ZoneMesh for zone effects (the fountain
  `tki` → `sibj` splash quad). Geometry, UVs, and material(s) go into the `.glb`
  (texture embedded), with the texture also written alongside as PNG. The `0x1F`
  format is in [particle_mesh.md](particle_mesh.md).
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

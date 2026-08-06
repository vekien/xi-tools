# xi object replace

Replace a zone mesh section with geometry from an edited GLB.

Unlike `xi zone import --rebuild` (which patches vertex positions in-place and
requires the same topology), `object replace` fully re-encodes the `0x2E` section —
any vertex count, any face count, any topology.

---

## Usage

```
uv run xi object replace <dat> <mesh_name> <glb_path>
```

| Argument | Description |
|---|---|
| `<dat>` | Zone DAT or ROM-relative spec (e.g. `ROM/1/41`) |
| `<mesh_name>` | Name of the mesh section to replace |
| `<glb_path>` | Path to the edited `.glb` file |

GLB only — convert FBX to GLB in Blender before importing.

---

## Typical workflow

```bash
# 1. Export the mesh you want to edit
uv run xi object export ROM/1/41 block03

# 2. Open in Blender / C4D, edit geometry
#    exports/object/1/41/block03/block03.glb

# 3. Export as GLB from Blender, then:
uv run xi object replace ROM/1/41 block03 block03_edited.glb
```

---

## What it does

1. Decrypts each `0x2E` section in the DAT to find the one matching `<mesh_name>`.
2. Parses the GLB — extracts all mesh primitives (positions, normals, UVs).
3. Applies the display→FFXI coordinate transform (undoes the Y-flip applied at export).
4. Re-encodes the section using the same name, author, and encryption key as the
   original (via `encode_zone_mesh_section`).
5. Splices the new section in place of the old one.
6. Writes the DAT back in place.

---

## Coordinate system

`object export` includes an `ffxi_root_correction` node in the exported GLB.
When you model relative to that node in your DCC tool, the round-trip is transparent —
`object replace` undoes the same transform automatically.

If you model in a different orientation, apply a manual rotation in Blender before
exporting (e.g. 180° X then flip Z).

---

## Texture names

Texture names are preserved from the original mesh where possible. The importer:

1. Reuses the original prim's `texture_name` for each submesh (matched by mesh index).
2. Falls back to the GLB **material name** if no original is available.

The textures themselves (the `0x20` sections) are not modified — only the mesh geometry
changes. Use `xi tex import` to swap texture images separately.

---

## Notes

- The `0x05` particle generators (water, fountain, lights) that reference this mesh
  by FourCC will automatically pick up the new geometry — no FX changes needed.
- Does not rebuild collision or sound data. For fully modifying the zone structure
  consider exporting the whole zone and re-importing with `xi zone import`.

---

## Related commands

- **`xi object export`** — export the mesh you want to edit
- **`xi zone export`** — full zone export (all meshes + placements)
- **`xi zone import`** — import placements + optional vertex patch from a full zone GLB
- **`xi tex import`** — re-import edited textures separately

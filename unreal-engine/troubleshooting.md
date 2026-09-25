# Troubleshooting, and how we got here

Getting South Gustaberg to look like the game in UE 5.6 took a long chain of fixes. Each one
uncovered the next problem. This is that chain as a lookup table, followed by what was tried
and ruled out, and what's still open. Background for every row is in
[how-ffxi-draws-zones.md](how-ffxi-draws-zones.md).

**Before theorising, compare.** Put the XI Model Viewer and UE side by side at the same spot.
Most of these were found in minutes that way, after hours of guessing.

## Symptom → cause → fix

| What you see | Cause | Fix |
|---|---|---|
| **Hard pale squares** along paths and terrain edges (Noesis exports look the same) | FBX import *Vertex Color Import Option* = **Ignore** (the default). The overlay fades live in vertex alpha, so every overlay tile draws at full strength. | Import with **Replace**. Check with Show › Vertex Colors: the ground should be grey, not white. |
| Whole zone **flat / washed out**, no baked shading | The material ignores vertex colour | MasterMaterial base colour = `Texture × Power(VertexColor × 2, 2.2)` |
| Adding the **×2 blows everything out** | Vertex colours weren't imported, so every vertex is white (1.0 × 2) | Fix the import (Replace) first; then the ×2 is right |
| Shading there but **low contrast**: shadows too light | Multiply done in linear space | `Power(VertexColor × 2, 2.2)`, not `VertexColor × 2`. FFXI multiplies in gamma space. |
| Tiles slightly **too bright** even with the right material | Blender wrote FBX vertex colours as sRGB (0.5 → 0.735) | `--unreal` (raw colours) exports them linear. Re-export. |
| Solid ground textures slightly **dark / shifted** against the viewer | The `_opaque.png` twins went through Blender's view transform (AgX), up to −42/255 | Fixed in xi-tools: twins were written byte-identical, and `--unreal` no longer writes them at all. Re-export and import into a **fresh** folder, because a mesh reimport doesn't reload textures. |
| **Holes / checkerboard** in floors and walls | Opaque materials carried texture alpha | `--unreal` (includes `--opaque`): solid surfaces are `OPAQUE` and their instances keep `Enable - Alpha` at 0 |
| **Trunk sways** with the leaves, or grass **slides at the roots** | An export from before the foliage split puts a tree's trunk and leaves on one `_cutout` instance; a constant `Wind Weight` moves every vertex the same | Re-export with `--unreal` (trunk goes to the solid instance) and weight the wind by `TexCoord[1]` ([materials.md › Wind](materials.md#wind)) |
| **Signs or grates sway** | `Enable - Wind` on every `_cutout` instance; signs are cutouts too | Turn wind on only for the foliage instances |
| Ground overlays **mismatch in cloudy weather** | Lit-translucent overlays use UE's forward translucency lighting, not the deferred ground's | Ground overlays as DBuffer decals (`M_FFXIZone_Overlay`, `OVERLAY_MODE = "decal"`) |
| **Flag / sign missing** in front of a wall | A wall overlay made into a decal paints over whatever is in front of it | Only up-facing overlays painted onto a surface become decals; walls go to `M_FFXIZone_OverlayWall` |
| **Sharp shading lines** across terrain | `--alpha-split-mesh`'s 45° auto-smooth re-creased the normals | Single FBX: `--unreal --no-sky --no-vfx`, no split |
| One **stray wrong-textured triangle** (repeated wherever that piece is placed) | Two opaque triangles at the same position with different textures; FFXI shows the first-drawn, UE shows either | `--unreal` drops the hidden duplicates. Re-export and reimport the mesh. |
| Walls / banners **flicker** as the camera moves | Wall overlays exactly coplanar with the wall, no depth bias | `M_FFXIZone_OverlayWall`'s 0.5 cm Depth Pull. Raise `Depth Pull` if needed. |
| Ground overlays **flicker far away** | Depth Pull too small for the distance | Raise `Depth Pull` on `M_FFXIZone_Overlay` (5–10) |
| Overlays **spill past** hill edges | Depth Pull too large | Lower it |
| Faint **lines along tile edges** | Mipmaps blend neighbouring atlas tiles | `NO_MIPMAPS = True` (the default) |
| Distant ground **shimmers** | The flip side of no mips | `NO_MIPMAPS = False` if you prefer the lines; TSR/TAA hides most of it |
| On a new zone, **`_alpha` instances untouched** | The script read another zone's `.glb` | It now finds the `.glb` through the selected mesh and stops on a mismatch. Select the whole folder, **including the static mesh**. |
| Script log says `nothing selected` | Nothing selected in the Content Browser | Open the zone folder, Ctrl+A, run again |
| Script log lists instances `not in the .glb` | Those instances came from a different export than the `.glb` | Export and import together; set `GLB_PATH` if the `.glb` moved |
| `Failed to compile` for an `M_FFXIZone_*` material in the Output Log | Usually DBuffer decals are off | Project Settings › Rendering › **DBuffer Decals** on |

## Ruled out along the way

These looked plausible and weren't the cause, so they don't need retrying:

- **Texture sRGB off.** It was already on for every zone texture. The script now forces it on
  anyway.
- **Overlay stacking or draw order.** Almost every ground triangle carries a single overlay
  (2,445 of 2,449 in South Gustaberg), so there's nothing to sort.
- **Z-fighting from LOD copies.** The export writes one copy per piece.
- **Ambient occlusion.** The sharp lines disappeared in the Unlit view mode, which points at
  normals. They were `--alpha-split-mesh`'s auto-smooth creases.
- **The base PNGs.** They match the DAT. The pale patches were the import dropping vertex
  colours. (The `_opaque` twins were a separate, real problem; see the table.)
- **UE Python's `set_material_instance_scalar_parameter_value` returning `False`.** It returns
  `False` even when the value lands. The script reads the value back instead of trusting it.

## Still open

- **Lighting model.** FFXI's look is baked vertex shade plus a flat ambient-and-sun term; UE adds
  a physically based sun and sky light on top. The materials keep specular off, but some double
  lighting remains. Matching the game exactly would move most of the look into Emissive, as
  FFXIEngine does.
- **Wall overlay fades are hard cuts.** Masked surfaces have no partial transparency. A wall
  overlay that fades softly in the game (snow on a cliff face) ends in a clean edge. If one
  matters, parent that single instance to `M_FFXIZone_Overlay` by hand. It's safe wherever nothing
  hangs in front of the surface. A rerun of the script moves it back, so redo it after one.
- **Mixed ground/wall materials.** A texture used on both floors and walls gets one treatment, and
  the 40% up-facing cut-off was tuned on South Gustaberg.
- **Partial opaque overlaps.** Coplanar opaque triangles that only partly overlap can't be
  dropped; South Gustaberg has two small spots, at the gate and on the lighthouse.
- **Effect-placed meshes.** `--no-vfx` leaves out meshes the game places with effect generators;
  the export can't position them yet.
- **Doors and lifts** are exported in their rest pose; the viewer animates them, UE doesn't.
- **`<stem>.ue5_mat.py`,** written by every export, still describes the older
  `Texture × VertexColor × 2` contract. Use `ffxi_zone_setup.py` instead.

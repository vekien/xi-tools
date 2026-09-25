# FFXI zones in Unreal Engine

Everything needed to take an FFXI zone from the game into Unreal Engine 5.6 and have it look
like the game (and the XI Model Viewer): the export options, the FBX import settings, the
materials, and a setup script you run once per zone.

| File | What it is |
|---|---|
| [how-ffxi-draws-zones.md](how-ffxi-draws-zones.md) | How the FFXI client draws a zone, and what each part becomes in Unreal |
| [materials.md](materials.md) | The four materials, their parameters, and how to rebuild MasterMaterial by hand |
| [troubleshooting.md](troubleshooting.md) | Symptom → cause → fix, what was tried and ruled out, and what's still open |
| `Content/CORE/*.uasset` | The materials (UE 5.6): `MasterMaterial` plus the three `M_FFXIZone_*` overlay materials |
| `ffxi_zone_setup.py` | The per-zone setup script (Tools › Execute Python Script) |

Built and tuned on South Gustaberg (`ROM/0/124`) in UE 5.6.

## Quick start

1. **Once per project:** copy `Content/CORE/` into your project's `Content/` folder, so the
   materials land at `/Game/CORE/…`.
2. **Export** the zone with `--unreal --no-sky --no-vfx` into an empty folder.
3. **Import** the `.fbx` into a new Content Browser folder with **Vertex Color Import Option =
   Replace** and instanced materials based on `/Game/CORE/MasterMaterial`.
4. **Run the script:** open that folder, **Ctrl+A**, then **Tools › Execute Python Script…** and
   pick `ffxi_zone_setup.py`.

The details of each step are below.

## 1. Materials (once per project)

Copy `unreal-engine/Content/CORE/` into `<YourProject>/Content/`, so that you end up with
`<YourProject>/Content/CORE/MasterMaterial.uasset` and friends. Do it with the editor closed,
or use the Content Browser's *Show in Explorer* on `Content` and let the editor pick the files up.

- `MasterMaterial` is the one you actually need. Every zone material instance derives from it.
  It references only engine content (wind, dither), so it opens in any 5.6+ project.
- The three `M_FFXIZone_*` materials are built by `ffxi_zone_setup.py` anyway. It rebuilds
  them on every run, so copying them is optional.
- The script expects all four under `/Game/CORE`. To keep them somewhere else, change `MAT_DIR`
  and `MASTER` at the top of the script.

On an older engine the `.uasset` files won't open. Rebuild MasterMaterial by hand from
[materials.md](materials.md); it's a handful of nodes.

## 2. Export

### From the XI Model Viewer

Open the zone and use **Export**:

- **Format:** FBX.
- **Destination:** an **empty** folder per zone (e.g. `Desktop\gustaberg`), so old PNGs and
  scripts from another export don't get mixed in.
- **Arguments:** `--unreal --no-sky --no-vfx`

Leave **Opaque materials** and **Right-handed** unticked. `--unreal` already turns both on.

### From the xi-tools CLI

```bash
uv run xi zone export ROM/0/124 --unreal --no-sky --no-vfx --output D:/exports/gustaberg
```

### What the options do

| Option | Why |
|---|---|
| `--unreal` | The Unreal preset. It gives you `--fbx` plus the following. **Right-handed:** the layout comes out right way up and un-mirrored, and mirrored placements are baked into their own `~mir` meshes. **Opaque:** solid surfaces are `OPAQUE`, each paired with a 24-bit `_opaque.png` twin, so UE doesn't wire texture alpha into them and punch holes in floors. **Raw vertex colours:** the untouched DAT colours and vertex alpha, written to the FBX as linear, not sRGB. **Duplicate removal:** hidden duplicate opaque triangles are dropped. |
| `--no-sky` | Leaves the skybox dome out. Use Unreal's own sky (UDS, SkyAtmosphere, …). |
| `--no-vfx` | Leaves out effect meshes and meshes with no placement. The export can't position effect-placed meshes, so without this they pile up at the world origin. |
| `--zero-coords` (optional) | For placing pieces yourself, with `--sub-areas` and/or `--objects`: every FBX comes in at 0,0,0 with no rotation, and `<stem>.zone.json` says where each goes back (per object, every placement's transform). The setup script still works: it reads the `.glb`, which isn't moved. See [zone export › Zero coords](../docs/zone/export.md#zero-coords---zero-coords). |

What to leave **off**:

| Option | Why not |
|---|---|
| `--alpha-split-mesh`, `--decal-offset` | The two-FBX decal split. Its 45° auto-smooth draws sharp shading creases across the terrain, and the overlay materials' Depth Pull does the same job better. |
| `--weld-seams` | Merges vertices in Blender. That's cosmetic, and it can blend away vertex-colour fades. |
| `--no-subareas` | Sub-areas are real geometry the game draws. Without them, doorways and parts of buildings go missing. |
| `--vertex-color baked` | For shaderless viewers only. It bakes and clamps FFXI's `×2` into the colours, which flattens bright tiles to white once UE lights them. |

### What you get

| File | Used for |
|---|---|
| `<stem>.fbx` | The import. One combined mesh. |
| `<stem>.glb` | **Keep it next to the `.fbx`.** `ffxi_zone_setup.py` reads it to sort ground overlays from wall overlays. |
| `model_*.png`, `model_*_opaque.png` | Textures. The `_opaque` twin is the alpha-free copy that solid materials use. |
| `<stem>.ue5_mat.py` | An older, minimal setup script (sRGB plus Enable - Alpha). `ffxi_zone_setup.py` replaces it, so don't run it. |

## 3. Import

Drag the `.fbx` into a **new, empty** Content Browser folder (e.g. `/Game/ZONES/gustaberg`). A
fresh folder means UE creates fresh material instances instead of reusing a previous import's.

| Setting | Value | Why |
|---|---|---|
| **Vertex Color Import Option** | **Replace** | **The important one.** The default, *Ignore*, throws away FFXI's baked lighting and every overlay fade. You get hard pale squares wherever a path or terrain transition should fade out. Noesis exports look the same for the same reason. |
| Build Nanite | Off | A Nanite mesh can't draw the decal or translucent overlay materials. The zones are small anyway. |
| Combine Meshes | On | One static mesh per zone. |
| Normal Import Method | Import Normals | Keeps FFXI's normals. |
| Generate Lightmap UVs | Off | Lightmaps don't matter under a dynamic sky, and generating them only slows the import. |
| Import Uniform Scale | 1.0 | |
| Material Import Method | Create New Instanced Materials | |
| Base Material Name | `/Game/CORE/MasterMaterial` | |
| Base Color Texture Property | `Texture` | The texture parameter on MasterMaterial. *Base Opacity Texture Property* can also be `Texture`; that's harmless. |
| Texture Import: Search Location | Local | Fresh textures from the export folder. |

**Check the colours came in:** open the static mesh and turn on **Show › Vertex Colors**. The ground
should be **mid-grey** with darker fades. If it's **white**, the colours were ignored; reimport
with *Replace*.

UE resets these settings per import. When you **Reimport Mesh With Dialog**, check *Vertex Color
Import Option* again.

## 4. Run the setup script

1. In the Content Browser, open the zone's import folder.
2. **Ctrl+A.** Everything must be selected: the static mesh, the material instances and the textures.
3. **Tools › Execute Python Script…** and pick `xi-tools/unreal-engine/ffxi_zone_setup.py`.
   (If the menu entry is missing, enable **Python Editor Script Plugin** under Edit › Plugins.)

What it does:

| Instances / assets | Becomes |
|---|---|
| `*_alpha`, **ground** (mostly facing up, painted onto the surface under it) | Parent → `M_FFXIZone_Overlay`, a DBuffer mesh decal, lit exactly like the ground it sits on |
| `*_alpha`, **everything else** (walls, banners, windows, free-standing pieces) | Parent → `M_FFXIZone_OverlayWall`, MasterMaterial's masked two-sided look plus a small depth pull |
| `*_cutout` | `Enable - Alpha` = 1 on their MasterMaterial instance (foliage, fences, grates) |
| Textures | Mipmaps off, sRGB on |

It finds the zone's `.glb` through the static mesh: UE remembers which `.fbx` the mesh came from,
and the `.glb` sits beside it. So the script can live anywhere, and it's safe to run as often as
you like. If the `.glb` it finds doesn't know the selected overlays (another zone's), it stops before
changing anything. You can also set `GLB_PATH` at the top of the script.

A good run ends with a summary in the **Output Log**:

```
[FFXI overlay] read D:\exports\gustaberg\124.glb: 13 overlay materials
[FFXI overlay] 8 ground overlays -> decal:
    model___gus_09_alpha (100% up, 100% painted on a surface)
    ...
[FFXI overlay] 5 wall overlays -> /Game/CORE/M_FFXIZone_OverlayWall (Depth Pull 0.5 cm):
    model___kabe_alpha (12% up, 100% painted on a surface)
    ...
[FFXI overlay] 'Enable - Alpha' = 1 on N *_cutout instances
```

**When you need to rerun it:** after a fresh import. When you only reimport the mesh, the
instances keep their parents, so there's no need to rerun unless the `_alpha` instances snapped back
to MasterMaterial.

**Settings at the top of the script** (edit, then rerun):

| Setting | Default | |
|---|---|---|
| `OVERLAY_MODE` | `"decal"` | `"translucent"` puts ground overlays on the lit-translucent material instead. It's closer to FFXI's own blend, but it's lit differently from the ground and mismatches in cloudy weather. |
| `DEPTH_PULL_CM` | `2.0` | How far ground overlays are pulled toward the camera. Raise it if they flicker far away; lower it if they spill past the edges of hills. |
| `WALL_DEPTH_PULL_CM` | `0.5` | The same for wall overlays. It's kept small so they can't draw over a flag or sign hanging just in front. Raise it if a wall still flickers. |
| `GROUND_SHARE` / `PAINTED_SHARE` | `0.40` / `0.90` | The ground test: at least 40% facing up **and** at least 90% painted onto a surface. |
| `NO_MIPMAPS` | `True` | Zone textures are atlases, and mips blur neighbouring tiles into lines along tile edges. `False` puts mips back, which trades the lines for less shimmer in the distance. |
| `DEBUG_VERTEX_ALPHA` | `False` | Shows the decal's vertex alpha as greyscale, to check the fades arrived. |

Both Depth Pull values are also parameters on the materials, so you can tune them live in the
material editor.

## See also

- [docs/zone/export.md](../docs/zone/export.md) — every `xi zone export` option.
- [how-ffxi-draws-zones.md](how-ffxi-draws-zones.md) — why each of the above is needed.

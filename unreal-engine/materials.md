# Materials

Four materials, all under `/Game/CORE` (`Content/CORE/` in this folder, UE 5.6):

| Material | Used by | Made by |
|---|---|---|
| `MasterMaterial` | Every zone material instance: opaque, `_cutout`, and the import's default for `_alpha` | Hand-built; ships as `.uasset` |
| `M_FFXIZone_Overlay` | Ground `_alpha` overlays (DBuffer mesh decal) | `ffxi_zone_setup.py` |
| `M_FFXIZone_OverlayWall` | All other `_alpha` overlays (masked surface) | `ffxi_zone_setup.py` |
| `M_FFXIZone_OverlayTranslucent` | Ground overlays when `OVERLAY_MODE = "translucent"` | `ffxi_zone_setup.py` |

The script deletes and rebuilds the three `M_FFXIZone_*` graphs on every run, so edits made
inside them in the editor don't survive a rerun. Change the constants at the top of the script
instead, or override parameters on the instances. All four share the parameter name `Texture`,
so an instance keeps its texture when the script moves it to a different parent.

Every formula below is explained in [how-ffxi-draws-zones.md](how-ffxi-draws-zones.md).

## MasterMaterial

Surface, **Masked**, **Two Sided**, Default Lit. The tables below cover what the kit relies on.
The shipped asset also has a few extras (dither and pixel-depth helpers), so open it for the
full graph.

| Output | Graph |
|---|---|
| Base Color | `Texture.rgb × Power(VertexColor.rgb × 2, 2.2) × Texture Strength` |
| Opacity Mask | `Texture.a` when `Enable - Alpha` is 1, otherwise 1 |
| Specular / Metallic / Roughness | their parameters, all 0 |
| World Position Offset | Engine `SimpleGrassWind` when `Enable - Wind` is 1 (see [Wind](#wind)) |
| Emissive | the emissive texture × colour × strength when `Enable Emissive Texture` is 1 |

| Parameter | Default | |
|---|---|---|
| `Texture` | — | The zone texture. The FBX import fills it: *Base Color Texture Property* = `Texture`. |
| `Texture Strength` | 1 | Overall brightness multiplier |
| `Enable - Alpha` | 0 | 1 = cut out by texture alpha. The setup script sets it on `_cutout` instances. |
| `Specular`, `Metallic`, `Roughness` | 0 | 0 keeps FFXI's matte look |
| `Enable - Wind`, `Wind Intensity`, `Wind Weight`, `Wind Speed` | 0, 0.2, 0.5, … | Sway for foliage instances |
| `Enable Emissive Texture`, `Emissive Texture`, `Emissive Texture Colour`, `Emissive Texture Strength` | 0, —, black, … | Glow maps, set per instance |
| `Enable - Pixel Depth Offset` | 0 | |

Two things in it are load-bearing for the rest of the kit. The base colour must be
`Texture × Power(VertexColor × 2, 2.2)`, because the overlay materials copy that formula to
match the ground under them. And the cutout switch must be named exactly `Enable - Alpha`,
because the setup script sets it by name (`CUTOUT_PARAM`).

### Wind

`--unreal` splits every tree into two instances of MasterMaterial on the same texture: the
see-through triangles (leaves, grass blades) on `<texture>_cutout`, and the solid ones (trunk,
bark-textured branch cards) on `<texture>`. So `Enable - Wind` = 1 on the `_cutout` instance
sways the leaves and the trunk stays still.

It also writes wind weights to the mesh's second UV channel, **`TexCoord[1]`**: `x` is height
within the mesh (0 at its base, 1 at its top) and `y` is reach (0 by the trunk, 1 at the widest
point). Both are 0 on everything that isn't a see-through triangle. With a constant
`Wind Weight` a grass clump slides at the roots; to pin them, feed `SimpleGrassWind`'s
**WindWeight** with

```
Wind Weight × TexCoord[1].x                   height only: roots and the base of the canopy still
Wind Weight × TexCoord[1].x × TexCoord[1].y   leaves near the trunk move less than branch tips
```

The why, with measurements, is in [foliage.md](foliage.md); the export side in
[zone export › Foliage](../docs/zone/export.md#foliage-cutout-vs-solid-triangles-and-wind-weights).

- **Turn wind on per instance.** Signs, fences and grates are cutouts too and carry weights,
  so set `Enable - Wind` on the foliage instances (West Ronfaure: `model___ron_w01c_cutout`,
  `model___ron_w03c_cutout`, `model___ron_k01c_cutout`), not on every `_cutout`. Grass can share
  a tree's texture (`_ron_k03` uses `ron_w03c`), so it shares that instance's wind settings.
- **Shadows.** A mesh whose material moves its vertices invalidates its Virtual Shadow Map
  cache every frame, and with *Combine Meshes* on that's the whole zone. If that costs too much,
  set the zone actor's *Shadow Cache Invalidation Behavior* to *Rigid*: shadows stop following
  the sway, which is hard to see on small foliage.
- **Lightmaps.** Channel 1 is where UE would put generated lightmap UVs. The kit imports with
  *Generate Lightmap UVs* off, so nothing overwrites it; if Map Check complains about lightmap
  UVs, that's UE reading the wind channel, and it doesn't matter under a dynamic sky.

### Rebuilding it by hand (UE versions that can't open the .uasset)

Only the part the kit relies on is needed; wind and emissive are optional.

1. New Material, `/Game/CORE/MasterMaterial`. Details: **Blend Mode = Masked**, **Two Sided = on**.
2. **Texture Sample Parameter 2D** named `Texture` (sampler type Color).
3. **Vertex Color** → **Multiply** (B = 2) → **Power** (Exp = 2.2).
4. **Multiply** the Texture's RGB by that, then **Multiply** by a **Scalar Parameter** `Texture Strength`
   (default 1) → **Base Color**.
5. **Scalar Parameter** `Enable - Alpha` (default 0) → **If** (A = `Enable - Alpha`, B = 0.5,
   A > B = Texture's A, A == B and A < B = 1) → **Opacity Mask**.
6. **Scalar Parameters** `Specular`, `Metallic` and `Roughness` (all default 0) → their pins.

## M_FFXIZone_Overlay (ground overlays)

Domain **Deferred Decal**, Blend Mode **Translucent**: a DBuffer mesh decal. It needs
**Project Settings › Rendering › DBuffer Decals** on, which is the default, and a mesh with
Nanite off.

| Output | Graph |
|---|---|
| Base Color | `Texture.rgb × Power(VertexColor.rgb × Vertex Colour Scale, Vertex Colour Power) × Texture Strength` |
| Opacity | `min(Texture.a × VertexColor.a × 2, 1)` |
| World Position Offset | `normalize(CameraPosition − WorldPosition) × Depth Pull` |

| Parameter | Default | |
|---|---|---|
| `Texture` | — | Kept from the instance |
| `Texture Strength` | 1 | Same as MasterMaterial's |
| `Vertex Colour Scale` | 2 | FFXI's modulate2x (`VERTEX_COLOUR_SCALE`) |
| `Vertex Colour Power` | 2.2 | The gamma-space multiply (`VERTEX_COLOUR_POWER`) |
| `Depth Pull` | 2 cm | Toward the camera, so the decal never ties with the ground (`DEPTH_PULL_CM`) |

## M_FFXIZone_OverlayWall (wall and free-standing overlays)

Surface, **Masked**, **Two Sided**, with the **Opacity Mask Clip Value copied from MasterMaterial**
on every run, so walls clip exactly as they did on MasterMaterial.

| Output | Graph |
|---|---|
| Base Color | as the decal |
| Opacity Mask | `min(Texture.a × VertexColor.a × 2, 1)`. Unlike MasterMaterial it includes vertex alpha, so FFXI's fades decide where the cut falls. |
| Specular / Metallic / Roughness | parameters, 0 (same names as MasterMaterial, so instance overrides carry over) |
| World Position Offset | `normalize(CameraPosition − WorldPosition) × Depth Pull` |

`Depth Pull` defaults to **0.5 cm** (`WALL_DEPTH_PULL_CM`). That's enough to stop a wall overlay
flickering against the wall it's painted on. It's small enough that it can't come through a flag
or sign hanging in front of the wall.

## M_FFXIZone_OverlayTranslucent (optional)

Surface, **Translucent**, Lighting Mode **Surface Per Pixel Lighting**, one-sided. Same colour,
opacity and Depth Pull as the decal, plus Specular 0, Metallic 0 and Roughness 1. It's FFXI's
own alpha blend, so fades are soft on any surface. But UE lights translucency differently from
the deferred ground, and in cloudy weather it visibly mismatches. It's kept for comparison: set
`OVERLAY_MODE = "translucent"` and rerun.

## Debugging an overlay

- Set `DEBUG_VERTEX_ALPHA = True` and rerun. The overlays then draw their vertex alpha × 2 as
  greyscale, fully opaque: white is full strength, black is faded out. A uniformly white
  overlay means the vertex colours didn't import; check *Vertex Color Import Option = Replace*.
  Set it back to `False` and rerun.
- To see just one material, open the static mesh, pick the material slot and use **Isolate**.

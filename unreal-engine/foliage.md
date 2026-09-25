# Foliage: trees, grass and wind

What `--unreal` does with trees and grass, how to make them sway in Unreal, and the answers to
the questions a first look in UE raises: why leaf normals face down, why the trunk and leaves
should be separate materials, whether to use a wind-mask texture or vertex colour, and why
there used to be two copies of every texture. Everything here was measured on West Ronfaure
(`ROM/0/120`) unless it says otherwise.

## How FFXI builds a tree

- **One mesh, one atlas.** A tree is a single mesh whose name starts with `_` (`_ron_w01_m`),
  on one texture that packs leaf branches, grass blades and bark together (`ron_w01c`). The `_`
  makes the client alpha-test the whole mesh.
- **Two submeshes.** The trunk is drawn one-sided (back faces culled). The leaves carry the
  `0x2000` flag, which turns culling off, so they're drawn from both sides.
- **Grass can borrow a tree's atlas.** `_ron_k03` is a grass clump on `ron_w03c`, the same
  texture as the `_ron_w03`/`_ron_w06` trees. Whatever material those trees' leaves get, that
  grass gets too.

## Leaf normals face the ground

That's how the DAT is authored, not an export bug. On every big Ronfaure tree, both the leaf
triangles' winding and their stored normals point down:

| Mesh | Trunk faces (up / down / sideways) | Leaf faces (up / down) | Leaf normals match winding |
|---|---|---|---|
| `_ron_w01_m` | 104 / 93 / 118 | 0 / 130 | 100% |
| `_ron_w10_m` | 87 / 94 / 134 | 0 / 112 | 100% |
| `_ron_w05_m` | 71 / 66 / 163 | 0 / 123 | 100% |

The export isn't flipping anything: in the same zone the terrain comes out 100% facing up, and
stored normals match the winding on 99% of all triangles. It isn't universal either: West
Sarutabaruta's two-sided parts are 16% up, 33% down and the rest sideways.

It stays hidden because the client draws the leaves two-sided. **Keep them two-sided in UE**
(MasterMaterial already is): leaf cards are flat and seen from both above and below, and UE
lights the back of a two-sided face with its normal flipped. The down-facing normals only
matter if you make the leaves one-sided, or build something on the normal's direction.

## Trunk and leaves on separate materials

`--unreal` splits every `_` mesh by triangle, by the part of the texture each one covers:

- **See-through** (any texel whose alpha, after the export's `×2`, is below `0.5`): leaves and
  grass blades. They keep `<texture>_cutout`, which the setup script turns `Enable - Alpha` on for.
- **Solid:** trunks, posts and bark-textured branch cards. They move to the plain `<texture>`
  instance, shared with any other solid surface on that texture. Cheaper than a masked material,
  and what the client shows anyway: its alpha test discards nothing there.

| Mesh | Solid | See-through |
|---|---|---|
| `_ron_w01_m` (big tree) | 315 (the whole trunk) | 130 (every leaf) |
| `_ron_w06_m` (small tree) | 101 (trunk + 77 branch cards) | 74 |
| `_ron_k03` (grass) | 0 | 28 |

Masked triangles in the zone drop from 2,250 to 767. Splitting on the two-sided flag instead
would get the small trees wrong: their two-sided part holds bark-textured branch cards as well
as leaves.

The test takes no safety margin around see-through texels. FFXI's atlas blocks meet on exact
texel lines and the trunks' UVs sit right on them (`u = 128.00`, beside the leaf block), so a
one-texel margin pulled 250 of `_ron_w01_m`'s 315 trunk triangles in with the leaves. What
bilinear filtering bleeds across such an edge is a sub-texel sliver.

The instance names show which is which in the Content Browser: `model___ron_w01c` is the
trunks, `model___ron_w01c_cutout` the leaves.

## Wind

### The weights

Every mesh carries a second UV channel, **`TexCoord[1]`**, with a wind weight per vertex. It's 0
on solid triangles and on every mesh without a cutout, so only leaves and grass can move:

| Channel | Meaning |
|---|---|
| `x`, **height** | 0 at the mesh's lowest point, 1 at its top. Grass roots and the bottom of a canopy stay put. |
| `y`, **reach** | 0 on the vertical line through the middle of the mesh, 1 at its widest point. Leaves at a branch tip move more than those by the trunk. |

Both run 0 to 1 within each mesh, so a grass clump and a tree both use the full range, and the
material decides how far that is. A flat cutout has no height, so its height weight is 0.

### Hooking it up

1. **Once, in MasterMaterial:** feed `SimpleGrassWind`'s **WindWeight** with the weight instead
   of the constant `Wind Weight` parameter alone:

   ```
   Wind Weight × TexCoord[1].x                   height only
   Wind Weight × TexCoord[1].x × TexCoord[1].y   and less sway near the trunk
   ```

   Without this step, wind still works, because the trunk is on its own instance, but every
   leaf and blade moves the same amount and grass slides at the roots.
2. **Per zone:** set `Enable - Wind` = 1 on the **foliage** `_cutout` instances only (Ronfaure:
   `model___ron_w01c_cutout`, `model___ron_w03c_cutout`, `model___ron_k01c_cutout`). Signs, fences
   and grates are cutouts too and carry weights (Bastok Mines' `_kanban03`, `_inmark2`), so don't
   turn it on for every `_cutout`.

More on the parameters in [materials.md › Wind](materials.md#wind), including the shadow-cache
cost of moving vertices on a combined zone mesh.

### Why not a mask texture?

A black-and-white **wind mask** over the atlas, white where the leaves and grass are and black
on the bark, sampled at each vertex's UV, does the same job with one material. It's the same
test the export makes (which part of the texture a triangle uses), and it gets the small trees'
branch cards right too. The per-vertex weights are the better vehicle for it:

- **No painting.** Every atlas needs its own mask, in every zone. The weights come out of the
  export for every zone.
- **Gradients.** A mask marks whole blocks of the atlas, so a grass clump moves as one piece and
  slides at the roots unless someone paints a gradient by hand. Height and reach are gradients.
- **No texture read in the vertex shader,** and no extra texture per atlas.

### Why not vertex colour?

FFXI's vertex colour is baked lighting (neutral `0x80`), not a wind mask. It happens to be a
rough one for leaves: on Ronfaure's trees the leaves range 40–170 and get brighter away from the
trunk (correlation about +0.35) and higher up (up to +0.58). But the trunk sits at 120–128, the
same as mid-canopy leaves, so it won't hold the trunk still, and grass (`_ron_k01`) is a flat
128, so it gives grass nothing. It's also lighting, so a tree's shaded side would sway less.

## One texture per image

Exports before this change wrote every texture twice, `model_*.png` and an alpha-free
`model_*_opaque.png` for solid materials. The copy guarded against Blender's FBX importer, which
wires any alpha channel into the material. In UE, the material decides: MasterMaterial ignores
texture alpha unless `Enable - Alpha` is on, and only `_cutout` instances have it on. So
`--unreal` writes one PNG per texture, shared by its solid and cutout instances.

Sharing costs nothing where a texture has both kinds, but a texture used only by solid materials
would keep a compressed alpha channel it never uses. The setup script compresses those without
alpha (BC1, half the size of BC3). It works out which ones from the zone's `.glb`, not the
selection, so a leaf texture can't lose its alpha because its cutout instance wasn't selected.

Plain `--fbx` exports still write the `_opaque` copies, for Blender.

## Checklist

1. Re-export the zone with `--unreal --no-sky --no-vfx`. Older exports have no wind channel and
   still put trunks on the leaves' instance.
2. Import into a **fresh** folder (see [README › Import](README.md#3-import)); *Generate Lightmap
   UVs* stays off so nothing overwrites channel 1.
3. Run `ffxi_zone_setup.py`.
4. Once per project: weight MasterMaterial's wind by `TexCoord[1]` (above).
5. Turn on `Enable - Wind` for the foliage `_cutout` instances.
6. Leave the leaves two-sided.

## Not yet checked in UE

The export side is tested on synthetic data and on the `.glb` of real zones (West Ronfaure,
Bastok Mines, South Gustaberg). Still to confirm in UE 5.6: that the wind channel arrives
through Blender and the FBX import as UV channel 1, that the solid instances look right on the
shared PNG, and the setup script's *Compress Without Alpha* pass. If something's off, the
[troubleshooting](troubleshooting.md) table is the place to add it.

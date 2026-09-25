# How FFXI draws a zone, and what that becomes in Unreal

The FFXI client is a fixed-function, gamma-space renderer from 2002. Unreal is a physically
based, linear-space deferred renderer. Nearly everything that looked wrong on the way here came
from one of the differences below. Each section says what the game does, what the export writes,
and what Unreal has to do to match.

## Three kinds of surface

Every zone submesh carries a texture and a set of flags. `xi zone export` names each material
after its texture, with a suffix saying how it's drawn:

| Material name | FFXI | What it is | In Unreal |
|---|---|---|---|
| `model   gus_01` | opaque | Terrain, walls, buildings | MasterMaterial instance |
| `model   gu_w05c_cutout` | alpha-tested | Foliage, fences, grates: a pixel is either there or not | MasterMaterial instance, `Enable - Alpha` = 1 |
| `model   gus_01_alpha` | alpha-blended (the `0x8000` blend bit) | **Overlays**: path edges, terrain transitions, wall grime, snow on cliffs, banners, windows | `M_FFXIZone_Overlay` (ground) or `M_FFXIZone_OverlayWall` (the rest) |

UE's import turns the spaces into underscores (`model___gus_01_alpha`); the setup script
compares names with those differences ignored.

## Vertex colour is the lighting

FFXI zones have no lightmaps. Each vertex carries a colour, and that colour **is** the baked
lighting: shade, ambient occlusion, the warm tint by a torch. The pixel is:

```
pixel = texture × vertexColour × 2        (per channel, clamped to 1)
```

This is the fixed-function *modulate2x*. The neutral vertex colour is `0x80` (0.5), so an unlit
vertex leaves the texture as it is, darker vertices shade it, and values above `0x80` brighten
it.

Two details matter in Unreal:

**1. The ×2 belongs in the material, not the mesh.** If the export folds the ×2 into the vertex
colour (`--vertex-color baked`, meant for shaderless viewers), anything brighter than neutral
clamps to 1.0 and loses its texture detail. UE then lights that already brightened colour with
its own sun, which blows it out. `--unreal` writes the raw colours (`--vertex-color raw`), and
the material does the ×2.

**2. FFXI multiplies in gamma space.** The game's texture and vertex colour values are sRGB
bytes, and the multiply happens on those bytes. UE samples an sRGB texture into linear light,
roughly `tex^2.2`. The same multiply, done in UE's linear space, is therefore:

```
(tex × vc × 2)^2.2  =  tex^2.2 × (vc × 2)^2.2
                    =  Texture sample × Power(VertexColor × 2, 2.2)
```

A plain `Texture × VertexColor × 2` in UE flattens the contrast of the baked shading. The shadowed
side of a rock comes out too light, because `0.25` in gamma is about `0.05` in linear light. So
MasterMaterial's base colour is `Texture × Power(VertexColor × 2, 2.2)`.

**How the colours reach UE unchanged.** The `.glb` stores vertex colours as linear floats (the
glTF rule). Blender carries them into the FBX, but by default it writes FBX colours as sRGB:
neutral `0.5` becomes `0.735`, and every tile comes out brighter. Under `--unreal` (raw colours)
xi-tools asks Blender for **linear** FBX colours, so UE's `VertexColor` node reads the DAT's own
`0.5`.

**And UE must actually import them.** The FBX import's *Vertex Color Import Option* defaults to
*Ignore*, which replaces every colour with white. With the lighting gone, the ×2 over-brightens the
whole zone, and every overlay fade disappears (see below). Set it to **Replace**.

## Alpha: `4 × vertexAlpha × textureAlpha`

For blended and tested surfaces the game's alpha is:

```
alpha = textureAlpha × vertexAlpha × 4     (both 0x80 = 1.0)
```

Both halves carry the same "0x80 is full" convention as the colour. The export writes the PNGs with
one of those ×2s already applied (`--alpha-scale 2.0`, the default), so opaque texels are `0xFF`
in the file and a PNG viewer shows the right cut-outs. That leaves one ×2, and the vertex alpha,
to the material:

```
opacity = min(Texture.a × VertexColor.a × 2, 1)
```

The **vertex alpha is how overlays fade**. A path-edge overlay is a set of whole terrain tiles
laid over the grass, with the vertex alpha going from full along the path to zero at the tile
edge. Without vertex colours (the *Ignore* import), every overlay tile draws at full opacity, and
you get hard pale squares along every path. That was the single biggest problem.

## Overlays sit exactly on the surface under them

An overlay triangle uses **the same vertex positions** as the opaque triangle it's painted on.
In South Gustaberg, 2,445 of 2,449 ground overlay triangles sit exactly on their base
triangle. Two surfaces at the same depth z-fight in any engine, so the game settles the tie. It
draws the blended pass after the opaque one with a depth bias toward the camera. The XI Model
Viewer does the same: blend submeshes get `polygonOffset(-5, 1)` (the client's *ZBiasLevel
High*) and a `LEQUAL` depth test.

Unreal has no depth bias for surface materials. The equivalent is **World Position Offset toward
the camera**:

```
WPO = normalize(CameraPosition − WorldPosition) × Depth Pull
```

Moving a vertex along the line to the camera doesn't change where it lands on screen. It only
changes its depth, so the overlay wins the tie without visibly moving. Ground overlays pull
2 cm. Wall overlays pull 0.5 cm, so they can't come through a flag or sign hanging just in front
of the wall.

### Why ground overlays are decals

A lit **translucent** surface is FFXI's own approach, an alpha blend on top. But UE lights
translucency on a separate, forward path. It matches the deferred ground in direct sun and
drifts badly when the sky light dominates, as in cloudy weather. A **DBuffer mesh decal** writes
its colour into the ground's G-buffer *before* lighting, so the overlay and the ground under it
are lit by exactly the same maths in any weather. `M_FFXIZone_Overlay` is that decal, and
`M_FFXIZone_OverlayTranslucent` is kept as the alternative (`OVERLAY_MODE`).

### Why wall overlays aren't

A mesh decal paints into whatever G-buffer pixels it covers. On a wall, that includes a flag
hanging in front of the wall. That's how the gate flags in South Gustaberg disappeared. So an
overlay is treated as ground only if it mostly **faces up** (40%+) and is **painted onto a
surface** (90%+ of it shares the exact corners of an opaque triangle). Everything else, such as
walls, banners, windows, water and loose pieces, goes to `M_FFXIZone_OverlayWall`. That's
MasterMaterial's masked, two-sided surface plus the 0.5 cm pull.
Masked means no partial transparency: a wall overlay's fade becomes a clean cut at the clip
value. That's the trade-off for being lit identically to the wall.

The setup script measures both numbers from the zone's `.glb`, per material.

## Opaque ties: first drawn wins

A few tiles carry **two opaque triangles at the same position with different textures**. South
Gustaberg's `mitid00_m` has a `gus_02` and a `gus_07` triangle stacked, repeated at all 11 of its
placements. The client and the viewer draw with a `LESS` depth test, so the first-drawn triangle
always wins and the second is never seen. Unreal has no such rule and shows either one, which
gives a single wrong-textured triangle. `--unreal` drops the hidden duplicates, keeping the first
in submesh order, and leaves blend overlays and cutouts alone.

Triangles that only *partly* overlap in the same plane can't be dropped. They're rare: in South
Gustaberg that's one strip of `gus_14` over `kabe` at the gate and a patch on the lighthouse.

## Textures

- **Colour, so sRGB.** Every zone texture is base colour; the setup script forces sRGB on.
- **Atlases, so no mipmaps.** Zone textures pack several tiles into one image. UE's mipmaps
  average neighbouring tiles together, which draws faint lines along tile edges. The game and
  the viewer don't use mips here, and the setup script turns them off (`NO_MIPMAPS`). The cost
  is some shimmer on distant ground when the camera moves.
- **One PNG per texture.** Many FFXI textures carry junk in the alpha channel of their solid
  areas. The material decides whether alpha counts: MasterMaterial ignores it unless
  `Enable - Alpha` is on, which the setup script sets only on `_cutout` instances. So a solid
  and a cutout material share one texture. Earlier exports wrote an alpha-free
  `<texture>_opaque.png` copy for every solid material; that guarded against *Blender's* FBX
  importer, which wires any alpha channel into the material, and isn't needed in UE.
- **No alpha where nothing reads it.** A texture only solid materials use is compressed without
  alpha (BC1, half the size of BC3). The setup script works that out from the `.glb`.

## Foliage

A tree is one mesh on one texture atlas (leaves, grass blades and bark packed together), alpha
tested as a whole because its name starts with `_`. Only the triangles that sample the
see-through background around the leaves are really cut out; the trunk and bark-textured branch
cards sample solid bark. `--unreal` splits them by the texels each triangle covers: see-through
ones on `<texture>_cutout`, solid ones on `<texture>`. That's cheaper, it's what the client
shows, and it lets wind move the leaves without the trunk. The leaf cards themselves face the
ground in the DAT (winding and stored normals both), and the client draws them two-sided, so
keep them two-sided in UE. Wind weights ride in the second UV channel. The full story, with
measurements, is in [foliage.md](foliage.md).

## Coordinates

FFXI's world is **Y-down** and left-handed. `--unreal` (via `--right-handed`) converts the layout
so it comes in upright and un-mirrored, with winding that faces outward. Placements that FFXI
mirrors with a negative scale are baked into their own `~mir` mesh copies, so they arrive the
right way round without relying on UE to handle a negative-scale placement.

## Lighting: what's still different

FFXI's look is almost all in the vertex colours: baked shade plus the game's own flat
ambient-and-sun term. Unreal adds a physically based sun and sky light on top of that baked
shade, so there's always *some* double lighting. The materials keep Specular and Metallic at 0,
so surfaces don't pick up highlights FFXI never had. Matching the game exactly
would mean moving most of the look into Emissive, as FFXIEngine does, and that isn't done here.
Tune the level's sun, sky light and exposure to taste.

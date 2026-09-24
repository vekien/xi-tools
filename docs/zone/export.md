# xi zone export

Export an FFXI **zone** (static area geometry) to a self-contained `.glb` and a
texture-embedded `.fbx`, with every object instanced and placed in world space.

```bash
uv run xi zone export <dat> [--fbx] [--no-sky] [--no-vfx] [--objects] [--collision] [--json] [--base] [--raw] [--right-handed] [--unreal] [--vertex-color raw|baked] [--alpha-scale N] [--opaque]
                            [--with-collision-proxies] [--with-far-lod] [--no-subareas] [--no-weld] [--weld-seams] [--mesh-merge-dp N]
                            [--alpha-split-mesh] [--decal-offset N] [--decal-smooth-angle DEG]
uv run xi zone export ROM/1/41            # Lower Jeuno
uv run xi zone export ROM/1/41 --alpha-split-mesh --right-handed   # Unreal: base + decal FBX pair
```

`<dat>` may be a ROM-relative spec like `ROM/1/41` (resolved against `FFXI_DIR`).
Output goes to `exports/zone/<rom path>/<stem>/`.

Zones are a different format from entity models (no skeleton); see
[../mesh/export.md](../mesh/export.md) for character/object models.

## What it does

1. **Decrypts** the encrypted mesh chunks (`0x2E` ZoneMesh) and the placement
   table (`0x1C` ZoneDef) — two different encryption schemes, keyed off tables
   read automatically from `FFXiMain.dll`.
2. Parses each chunk's static geometry (positions, normals, UVs, per-chunk
   texture) and the `0x20` zone textures.
3. Reads the **placement table**: every placed object is a record of
   `mesh id + position + rotation + scale`, so one mesh can be instanced many
   times (e.g. a wall segment repeated along a street).
4. Emits **one glTF mesh per unique chunk** and **one node per placement** (with
   the object's transform), so in the DCC you get the zone laid out correctly,
   each instance a named object whose transform you can read.

## Options

| Flag | Effect |
|------|--------|
| `--fbx` | Also export a texture-embedded `.fbx` via Blender (for editors like C4D that can't open `.glb`). Import always expects `.glb`. |
| `--no-sky` | Omit the skybox/celestial chunks (sun, moon, stars, clouds). |
| `--no-vfx` | Omit every **unplaced** (non-world) mesh — any `0x2E` mesh with no `0x1C` placement that isn't sky. Covers effect-placed VFX (water jets, light glows, `lcut`/`lightstp`, prop generators) **and** dead/unreferenced geometry the client never renders (`cyst`, `sh-u`). Combine with `--no-sky` to leave only placed world geometry (empty `unplaced_skybox` node). |
| `--objects` | Export **each mesh as its own file** — `<meshname>.glb` (and `<meshname>.fbx` with `--fbx`) into a `<stem>_objects/` subfolder, instead of one combined zone file. Each object is emitted in local space at the origin (its raw geometry) with textures embedded, oriented by the same `ffxi_root_correction` node. Honors `--no-sky`/`--no-vfx` for which meshes are written. **Note:** with `--fbx` this spawns Blender once per object, so a full zone (100s of meshes) takes a while. |
| `--collision` | Also dump the **player-collision mesh** (the `0x1C` MZB triangle soup) to `<stem>.collision.obj` + `.mtl` + `.collision.json`. Same frame as the `.glb` so it overlays. See [collision.md](collision.md). |
| `--base` | Export from the pristine original instead of your edited DAT — handy to regenerate a clean model after edits. |
| `--raw` | Omit the orientation-correction node (raw FFXI coords). View-only — a raw export is not meant to be re-imported. |
| `--right-handed` | Export for a game engine (Godot/Unreal/Unity): bake the handedness flip into geometry (engines drop the negative node scale, mirroring the zone and breaking collision) **and** flip winding to CCW-front so single-sided engines light the terrain instead of culling it black. See below. |
| `--unreal` | Unreal preset: `--right-handed` + `--opaque` + `--fbx` + `--vertex-color raw`. One flag for a UE-facing FBX with correct winding/orientation, no clipped floors, and FFXI's `×2` left to the zone material. The generated `*.ue5_mat.py` states the material contract. An explicit `--vertex-color` still wins. See below. |
| `--vertex-color raw\|baked` | Where FFXI's baked-lighting `×2` (modulate2x) lives in `COLOR_0`. **Default `baked`** folds it in and clamps it, so a shaderless glTF/DCC viewer shows in-game brightness. `raw` emits the untouched DAT colour (and real vertex alpha) and leaves the `×2` to the engine material — matches `entity mesh export`. `--unreal` implies `raw`. See below. |
| `--json` | Also write `<stem>.zone.json`: every placement (full TRS, LOD, links), mesh list, textures, per-weather ambient sounds, companion event/dialog/NPC DAT paths, sub-area interior DATs. |
| `--with-collision-proxies` | Include **collision-only placements** — draw distance exactly `1.0`, the sentinel the client treats as never-render (`hitwall_*`, `kabe-atariyou`, `hit_*`, `id_board*` / `id_box*`). Retail has 15,835 of them; Ru'Aun Gardens is 45% proxies. Off by default: they stack invisible geometry on the zone. |
| `--with-far-lod` | Include **far copies** — `m_` / `lnd_` meshes that stand in for richer geometry the zone also places (Ru'Aun's `m_osid_*` islands, `m_bri_*` bridge). The client shows one or the other by region, so exporting both puts the cheap copy inside the detailed one. Only fires where a richer same-stem twin is actually placed, so ordinary `m_` props (`m_bed_02`, `m_pot`) are never affected. |
| `--no-subareas` | Omit placements tagged with a sub-area id: shop and inn interiors in the towns, and in Ru'Aun Gardens a whole second low-detail copy of the sky. Included by default — they are real geometry, drawn inside their own volume. See [subareas.md](subareas.md). |
| `--alpha-scale N` | Multiply texture alpha by `N` (clamped to 255) before writing the PNGs. **Default `2.0`** — see below. Pass `1.0` for the raw FFXI alpha, or higher to force more opacity. |
| `--opaque` | Write non-blend materials as `alphaMode: OPAQUE` instead of `MASK`. Fixes the checkerboard/eaten floors and walls some zones show in Blender — see below. |
| `--no-weld` | Keep the original per-triangle vertices. **Welding is on by default** — see below. |
| `--weld-seams` | Also fuse the UV-seam splits the default weld leaves (tiled terrain). **Needs `--fbx`** — the merge runs in Blender and keeps the texture. See below. |
| `--mesh-merge-dp N` | Decimal places for the weld position threshold. **Default `4`** (`0.0001` units). Lower it if adjacent polys stay unjoined; raise it to weld only exact matches. |
| `--alpha-split-mesh` | **(Test) Alpha Split Mesh** — export **two** FBX files for Unreal, splitting FFXI's coplanar ground decals off the terrain so they stop z-fighting. Implies `--fbx`. See below. |
| `--decal-offset N` | How far `--alpha-split-mesh` lifts each decal off the surface, in mesh/FFXI units. **Default `0.00001`** (≈`0.001` cm in Unreal after the usual 100× FBX import). See below. |
| `--decal-smooth-angle DEG` | Auto-smooth angle for `--alpha-split-mesh`. **Default `45`** (45–60 is typical). |

## Welding (`--no-weld`, `--mesh-merge-dp`)

A zone mesh chunk stores geometry as an index list over a shared vertex pool, but a
plain export writes every triangle as three standalone corners — a "triangle soup"
where a shared edge is two coincident-but-separate vertices. In a DCC that means you
can't select connected geometry, smoothing looks faceted, and vertex counts are
roughly double what they need to be.

**Welding is on by default** (same as `gear export`). It deduplicates coincident
corners into a shared, **indexed** vertex buffer per mesh — a joined, editable mesh
like Noesis produces. Dedup is by rounded **position + UV + baked vertex colour**;
the first corner to land at a spot wins the normal, so a shared edge welds even where
the two faces stored slightly different normals. Baked lighting colour is part of the
key on purpose: zones carry meaningful per-vertex lighting, so verts that differ only
in colour stay split rather than smearing the bake across a discontinuity.

Welding only re-indexes — the triangles it emits are identical to `--no-weld`, so the
model looks the same; it just joins the vertices. On the standard zones it drops the
vertex count ~50% (Lower Jeuno 175k → 76k, West Sarutabaruta 179k → 87k). The
round-trip is unaffected: `zone import` reads indexed and non-indexed GLBs alike.

`--mesh-merge-dp N` sets how many decimal places the position is rounded to before
comparison (default `4` = `0.0001` units, ample at zone scale). Pass `--no-weld` to
keep the original per-triangle vertices.

### Tiled terrain still looks split (`--weld-seams`)

The default weld keeps the two sides of a **UV seam** apart, because a **glTF** stores one
UV per vertex — a position that samples the texture at two different spots is genuinely
two vertices there. Tiled ground is the worst case: the same patch of world reuses one
position with several UVs, so a terrain tile with ~121 distinct positions still exports as
~400 vertices and reads as triangle soup in a DCC. `--mesh-merge-dp` does **not** help —
the positions already match; it's the UVs that differ.

`--weld-seams` fixes this, but it needs `--fbx`. **FBX** stores UVs per polygon-vertex, so
the fix is to weld the shared *vertices* in Blender during the GLB→FBX step while every
face keeps its own UV — connected geometry **with the texture intact**. (A GLB can't
represent that, so `--weld-seams` without `--fbx` only prints a note and leaves the `.glb`
alone.) The merge radius follows `--mesh-merge-dp`.

The weld is grouped by material, which matters: FFXI terrain layers a blended overlay
(`sar_kk2_alpha`) on top of the opaque base (`sar_kk2`) as a second triangle at the same
position. Blender can't hold two faces with identical vertices, so a blind merge-by-distance
would delete the overlay — a visible ~10% of the surface. So the base (**all opaque
materials together**, connecting the ground across texture boundaries too) welds as one
group, while each **alpha overlay welds on its own** and is never folded into the base. On
West Sarutabaruta's `daich_2_m` this takes the tile from 418 vertices to 152 with **every
face, all surface area, and the full UV layout unchanged**.

The DAT's authored **normals are preserved** through the weld (carried per-corner so bmesh
can't recompute them from the winding), so the base and its overlay keep the identical
normals the DAT gave them and shade as one — no seam between the ground and its blended
patches.

## Black terrain in Unreal / Godot (`--right-handed`)

If the ground imports **black from above but lit from below** (or see-through from above),
that's a winding problem, not the normals. FFXI DAT triangles are **clockwise-front**
(Direct3D) and the retail client draws the world **two-sided**, so the exporter leaves the
winding as-is and marks every material `doubleSided`. A glTF/DCC viewer honours that and
looks fine. But **Unreal/Unity/Godot make imported materials single-sided** and cull the
back face — and the terrain's front face (by their counter-clockwise convention) is its
*underside*, so from above you see the culled back and the ground goes black.

There is a second half to it: FFXI **reuses terrain tiles mirrored** — placements with an odd
number of negative scale components (West Sarutabaruta: 246 of 4153 placements, ~40% of the
ground tiles). A reflection reverses the winding *for that one instance*, so no winding in
the shared mesh suits both, and the Blender → FBX → engine path doesn't compensate: the
mirrored tiles go black in a **checkerboard** while their neighbours are fine.

**`--right-handed` fixes both** (it's the game-engine export mode anyway): it flips the winding
to CCW-front, matching the stored up-normals, so a single-sided engine lights the top; and it
**bakes every mirrored placement into a mirrored mesh variant** (`<mesh>~mir`, ignored by
`zone import`) with the matching un-mirrored node scale, so no negative-determinant transform
ever reaches the engine. It also bakes the handedness with a pure rotation instead of the
negative node scale those engines silently drop. Verified in world space through the final
FBX: 0 negative-determinant objects, winding-vs-normal agreement 100%, geometry identical to
the original layout. So for a game
engine, always export with `--right-handed` (add `--weld-seams` too for connected terrain).
The normals themselves export correctly either way — the DAT stores them all pointing up;
if you still want them two-sided in-engine, set the material two-sided there.

## Exporting for Unreal (`--unreal`, `--vertex-color`)

> **The full Unreal workflow** — recommended args, FBX import settings, the materials and the
> per-zone setup script — lives in [`unreal-engine/`](../../unreal-engine/README.md). Use
> `--unreal --no-sky --no-vfx`. This section covers what the flag itself does.

`--unreal` is a one-flag preset: `--right-handed --opaque --fbx --vertex-color raw`. It also
drops **hidden duplicate triangles**: a few tiles carry two opaque triangles at the exact same
position with different textures (South Gustaberg's `mitid00_m`: a `gus_02` and a `gus_07`
triangle). The client and the XI viewer always show the first-drawn one; an engine has no rule
for two surfaces at one depth and shows either, so a single wrong-textured triangle appears.
The export keeps the first, in submesh order (blend overlays and cutouts are left alone). It gives
a UE-facing FBX that's right-side-up, un-mirrored, collidable, has no junk-alpha holes in
floors/walls, and leaves FFXI's baked-lighting `×2` to the zone material instead of baking it
into the mesh. The `.fbx` ships with a `*.ffxi_root_correction`-oriented layout and a generated
`<stem>.ue5_mat.py` (paste into UE5's Python console after import) that enables alpha on the
`_alpha` blend-overlay materials and forces base-colour textures to sRGB; its header states the
vertex-colour contract for the mode you exported.

### Washed-out / flat-white terrain patches — the vertex-colour bake

FFXI draws terrain as `texture × vertexColour × 2` (modulate2x): the neutral vertex colour is
`0x80` (0.5), so the `×2` brings it back to full brightness, and baked lighting darkens from
there. A **glTF/DCC viewer has no shader**, so `--vertex-color baked` (the default) folds the
`×2` into `COLOR_0` and clamps it, and a viewer's `baseColorTexture × COLOR_0` reproduces the
in-game look.

In a **shaded engine that multiplies by vertex colour** (a real UE zone material), that pre-bake
is wrong two ways:

- Every tile lit **at or above neutral** clamps to pure white after the `×2`, so it loses all
  texture detail — the flat, washed-out beige patches. (Measured on Abdhaljs Isle: the neutral
  floor `rz_be_fl1_m` goes to `1.0` on every channel under `baked`, versus its true `0.5` under
  `raw`.)
- The engine then lights that already-brightened albedo with its own sun (UDS, etc.), doubling
  the brightness again → blown highlights.

`--vertex-color raw` (what `--unreal` selects) emits the **untouched** DAT colour — the true
`0x80` neutral and the real per-vertex alpha — and you do the `×2` once, in the material:

```
BaseColor = TextureColor × VertexColor × 2          (FFXI, in gamma space)
BaseColor = Texture × Power(VertexColor × 2, 2.2)   (the same thing in UE's linear space)
```

FFXI does the multiply on sRGB values; UE's texture sample is already linear, so a plain `× 2`
flattens the baked shading. The `Power(…, 2.2)` form is what `unreal-engine/`'s MasterMaterial
uses. With `raw` colours the FBX also carries them **linear** (Blender's default sRGB would turn
the neutral `0.5` into `0.735`). This is the same split the FFXI engine and `entity mesh export` use. **A raw export looks
half-bright under a stock material** — the material *must* apply the `×2`, or the whole zone reads
dark. The generated `ue5_mat.py` header spells this out. If you'd rather keep the plug-and-play
baked look for a quick look-see, pass `--vertex-color baked` (or just don't use `--unreal`).

What `--unreal` can't fix (engine-side, not export): auto-exposure/tonemapping in the level,
whether blend overlays (`_alpha` road paint / terrain transitions) render as translucent quads
or DBuffer decals, and lightmap UVs (moot under a fully dynamic sky). The overlays are the other
source of "faceted white patches": they fade by vertex alpha, so UE's FBX import must use
**Vertex Color Import Option = Replace** (the default *Ignore* draws every overlay tile at full
strength). Routing them to a decal / masked material with a depth pull is what
`unreal-engine/ffxi_zone_setup.py` does.

## (Test) Alpha Split Mesh (`--alpha-split-mesh`)

> **Not recommended for Unreal any more.** Its auto-smooth draws sharp shading creases across
> the terrain; a single `--unreal` FBX plus the overlay materials' camera-facing Depth Pull in
> [`unreal-engine/`](../../unreal-engine/README.md) solves the z-fighting without it.

FFXI paints ground decals — paths, cracks, puddles, blood stains — as an **alpha-blend
overlay** (the `0x8000` blend bit, exported as an `_alpha` material) drawn **coplanar** with
the opaque terrain underneath it. The retail client sorts them fine, but in Unreal two
surfaces at the exact same depth **z-fight**: the decal and the ground flicker against each
other as the camera moves.

`--alpha-split-mesh` runs the recipe that fixes it and writes **two** FBX files:

- `<stem>.fbx` — the opaque **base** terrain/props.
- `<stem>_A.fbx` — just the **decals**, separated out, lifted a hair off the surface.

Import them into Unreal as **two separate static meshes**. Per mesh it:

1. **Welds** the opaque polys together, and the alpha polys together — separately, so a
   decal is never fused into the base (Blender can't hold two faces on the same vertices and
   would delete the overlay).
2. **Auto-smooths** both by polygon angle (`--decal-smooth-angle`, default 45°).
3. **Separates** the alpha polys into their own mesh, named `<object>_A`.
4. **Pushes** the decal polys along the surface normal by `--decal-offset` so they sit just
   in front of the ground instead of on it.
5. **Transfers** the base surface's normals onto the decal, so the lifted decal still shades
   as one with the ground (no visible seam where it lifts).

```bash
uv run xi zone export ROM/1/41 --alpha-split-mesh --right-handed
#   -> exports/zone/rom/1/41/41.fbx      (opaque base)
#   -> exports/zone/rom/1/41/41_A.fbx    (decals)
#   -> exports/zone/rom/1/41/41.ue5_mat.py  (sets 'Enable - Alpha' on the _alpha materials)
```

Needs Blender (it runs the split there), so `--fbx` is implied. This targets Unreal, so
**pair it with `--right-handed`** — otherwise the terrain is still mirrored and black-from-
above in-engine (see above); the command prints a reminder if you leave it off. `--no-sky
--no-vfx` are worth adding too, or the sky/water blend meshes end up in the decal file.
It combines with `--unreal` too: both FBX files then carry raw vertex colours, and the
`ue5_mat.py` header states that contract.

**Tuning the offset.** `--decal-offset` is in mesh/FFXI units (~metres); the default
`0.00001` lands at ≈`0.001` cm in Unreal after the standard 100× FBX import. That is
deliberately tiny, but FBX vertex precision gets coarser the further geometry sits from the
origin, so on a large zone decals far from `0,0,0` may still z-fight — **raise `--decal-offset`**
(e.g. `0.0005`) until they hold. Too large and the decal visibly floats.

`--alpha-split-mesh` can't be combined with `--objects` (it builds one combined base/decal
pair, not per-mesh files).

## Texture opacity (`--alpha-scale`)

FFXI stores texture alpha at **half scale**: `0x80` (128), not `0xFF`, means fully
opaque. The game (and xim) double it at draw time — the shaders compute
`4·vColor.a·tex.a`, and the neutral vertex alpha `vColor.a` is `0x80`, a net **×2**
on the texture alpha. A standalone exported PNG has no shader, so opaque texels
would read as **~50% transparent** in Blender/C4D/etc.

The exporter bakes the same ×2 into the PNG by default, so opaque texels come out
opaque while real cutouts (alpha 0) and gradients scale proportionally — matching
the in-game look. Pass `--alpha-scale 1.0` to keep the raw (faint) FFXI alpha.

## Clipped floors and walls (`--opaque`)

By default every non-blend material is written as `alphaMode: MASK` (0.5 cutoff), so real
cutouts (foliage, fences, signs) stay cut out in a DCC tool. The client, however, **ignores
texture alpha on non-blend submeshes** — they are solid whatever the alpha says — and many
zone textures store junk in that channel. In ROM/1/34 `model_doors2` is alpha 0 everywhere,
`model_sidestep` is 97% below the cutoff, `model_f2yuka` 71%, `model_yuka_h` and
`model_kabe2_h` 67%. Under MASK Blender clips those texels, and whole floors, stairs and
walls come out as a checkerboard of holes.

`--opaque` writes those materials as `OPAQUE` instead, which is what the client draws, and
uses the client's own rules for the rest: only the `0x8000` blend bit gives `BLEND` (water,
glows), only a mesh name starting with `_` gives `MASK` (foliage cutout, see
[format.md](format.md)), and the `0x2000` double-sided bit no longer implies transparency.
Without the flag `0x2000` submeshes are still written as `BLEND` and everything else as
`MASK`, because `xi object import` reads those modes back into the flags word. The icon
batch (`xi batch icons`) always exports the `--opaque` way.

With `--fbx`, every OPAQUE material in the `.fbx` points at a 24-bit `<texture>_opaque.png`
twin written next to the normal PNGs. The export writes each twin itself, byte-identical to the
texture's RGB (Blender's `save_render`, which used to make them, applied the scene's view
transform and tone-mapped every texel, so base ground came out up to 42/255 darker). Blender's FBX importer wires a diffuse texture's alpha
into the material whenever the PNG has an alpha channel, which would undo `--opaque` on the
way back in; the alpha-free twin is the only thing it leaves alone. Blend and cutout
materials keep using the original PNG.

## Skybox vs placed geometry

Skybox/celestial meshes are **not** in the placement table — the engine wraps
them around the camera at runtime — so they're stored at the origin. The exporter
groups them under an `unplaced_skybox` node (hide it, or use `--no-sky`). All
placed world geometry sits under its real transforms.

The same `unplaced_skybox` node also catches every other **unplaced** mesh — any
`0x2E` mesh with no `0x1C` placement. These are never positioned world geometry;
they're one of: effect-placed VFX (water jets, light glows, prop generators like
`rnp*`, `lcut`, `lightstp`, the `lowsea` ocean — positioned solely by a `0x05`
generator) or dead/unreferenced geometry the client never renders (`cyst`, `sh-u`).
Use `--no-vfx` to drop all of them; add `--no-sky` to also drop the sky, leaving an
empty `unplaced_skybox` node and only your placed world geometry.

## Per-object export (`--objects`)

Instead of one combined zone file, `--objects` writes **one file per mesh** into a
`<stem>_objects/` subfolder — e.g. `t_obj05.glb`, `tower_a1.glb` (and `.fbx` too
with `--fbx`). Each object is emitted in **local space at the origin** (its raw
geometry, not its world placements), textures embedded, oriented by the same
`ffxi_root_correction` node as the full export. This is the way to pull a zone's
props out as a reusable asset library.

```bash
# Every placed world object as its own fbx (sky + vfx pruned)
uv run xi zone export ROM/1/41 --objects --fbx --no-sky --no-vfx
#   -> exports/zone/.../41_objects/t_obj05.fbx, tower_a1.fbx, ...
```

`--no-sky` / `--no-vfx` decide which meshes are written (same filtering as the
combined export). With `--fbx`, Blender is spawned once per object, so a full zone
(100s of meshes) takes a while — progress prints per object.

## Requirements & limits

- Needs `FFXiMain.dll` at `FFXI_DIR` (the decryption key tables are read from it).
- Needs Blender (`BLENDER_PATH`) for the `--fbx` step; omit `--fbx` to skip it.
- LOD: objects with `_l`/`_m`/`_h` ids resolve to the highest-detail mesh.
- Vertex colours (FFXI baked lighting) are written as `COLOR_0`. By default the
  FFXI ×2 modulate is folded in (for shaderless DCC viewers); `--vertex-color raw`
  (and `--unreal`) leave it to the engine material instead. See above.

Re-importing an edited GLB back into the DAT is done via `xi zone import` (placements, mesh-merge) and `xi object import` (individual objects). See [import.md](import.md).

## Format reference

Mesh + texture binary, the two decryption schemes, and the ZoneDef placement
record layout: [format.md](format.md).

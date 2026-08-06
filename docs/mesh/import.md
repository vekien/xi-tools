# xi mesh import

Import an edited — or entirely new — model back into an FFXI entity DAT,
re-skinned onto the DAT's existing skeleton.

```bash
uv run xi mesh import <dat> [model]
uv run xi mesh import ROM/7/97             # uses the exported model automatically
uv run xi mesh import ROM/7/97 model.fbx   # or an explicit model
```

`<dat>` may be a filesystem path or a ROM-relative spec like `ROM/7/97`. If
`[model]` is omitted, the model exported for that DAT
(`exports/mesh/<rom>/<stem>.fbx`, then `.glb`/`.gltf`) is used automatically.
Older exports under `exports/entity` are still accepted as a fallback.

Verified in-game on `ROM/7/97` with both round-tripped retail geometry and a
fully custom replacement model (custom geometry + custom texture).

See also: [export.md](export.md) for producing the editable model.

## What it does

1. Keeps a `<dat>.base` backup and **always rebuilds from it** — safe to re-run,
   and the restore point if anything goes wrong.
2. Converts FBX → glTF via Blender if needed, then reads geometry, UVs, and skin weights.
3. Re-bakes every vertex into FFXI joint-local space using the DAT's existing
   skeleton, auto-aligned to it via the bones' inverse-bind matrices (so DCC unit
   differences self-correct). Two-joint positions are stored **pre-multiplied by
   their weight** (the DAT format — see
   [format.md](format.md#vertex--world-space-skinning-the-assembly-math)); normals
   stay unweighted.
4. Serializes the geometry into one or more `0x2A` mesh sections and rebuilds the DAT.
5. Encodes any **new** textures (added in the DCC, not already in the DAT) into
   `0x20` DXT sections and injects them; the mesh references them by material name.

It prints a before/after comparison (file size, mesh sections, vertices, draws,
triangles) so you can watch the geometry budget.

## Options

- `--scale N` — uniform scale on top of the auto-alignment (default `1.0`)
- `--rotate-y DEG` — rotate about the vertical axis to fix facing (e.g. `-90`)
- `--single-sided` — do not emit reversed back-faces (default is double-sided so
  thin surfaces like flags are not back-face culled)
- `--mesh-name` — 4-char section name (default reuses the DAT's first mesh section)

## What is and isn't changed

- **Changed:** the skeleton-mesh section(s) are fully rewritten; new texture
  sections may be added.
- **Untouched:** the skeleton (`0x29`) and animations (`0x2B`). New geometry is
  re-skinned onto the existing bones — keep the armature in your edited file.

## DCC workflow (Cinema 4D / Blender)

1. Export with [export.md](export.md) and open the `.fbx` (C4D) or `.glb` (Blender).
2. Keep the armature/bones. Build or edit geometry and **bind it to a bone**
   (binding everything to `bone0000` makes a rigid static object).
3. Model at the skeleton's scale (it is small, ~5 units), or scale the whole rig
   up uniformly and let the importer's auto-alignment correct it.
4. Assign materials. To use an **existing** DAT texture, name the material to
   match its 16-char name. To add a **new** texture, just texture/UV your mesh
   normally — the importer bakes the image into the DAT.
5. Export FBX and run the import (add `--rotate-y` if the facing is off).

## Engine constraints handled automatically

These were reverse-engineered against retail entity DATs (full byte layout:
[format.md](format.md)). The importer respects them so output loads in the real client. A viewer like AltanaView is more lenient
(it seeks by offsets and loops the instruction stream), so "loads in AltanaView
but crashes in-game" almost always means one of these:

- **64-byte mesh-section header.** Retail `0x2A` headers are 64 bytes (instructions
  start at `0x40`), with `flags1 = 1` and a `u32` at `0x2E` equal to the end
  offset. A short header makes the engine read instruction bytes as header offsets
  and crash. *(This was the bug that caused the in-game crashes during development.)*
- **128 triangles per draw call.** A single draw instruction is capped at 128
  triangles (384 corners); geometry is chunked to respect it.
- **Joint array + `numJoints ≥ 1`.** The section carries an identity joint array;
  vertices index it. No retail mesh uses `numJoints = 0`.
- **Single- vs double-jointed split.** Vertices are partitioned into 1-influence
  then 2-influence groups, each with its own data layout. No retail mesh is
  all-double-jointed.
- **Per-section vertex cap.** `maybeVertexDataSize` is a `u16` (131070 vertex-data
  bytes, ≈ a few thousand verts) and vertex indices are `u16` (max 65535). Large
  meshes are split across multiple `0x2A` sections automatically.

## Limits & recommendations

The importer respects the hard engine limits automatically (chunking draws,
splitting sections), but **budget matters for performance** — especially the
draw-call count, which is per-batch overhead multiplied across every visible
entity in the scene. The engine targets PS2-era hardware; retail entity models
are small.

Hard limits (handled for you):

| Limit | Value | How it's handled |
|---|---|---|
| Triangles per draw call | **128** (384 corners) | geometry is auto-chunked |
| Vertex-data per `0x2A` section | `u16` words → ~5,400 single-jointed verts | auto-split into multiple sections |
| Vertex indices per section | `u16` (65,535) | auto-split |
| Texture format | DXT1 / DXT3 | encoder forces DXT3 (alpha) / DXT1 (opaque); DXT5/`5TXD` exists in the format but is unobserved on entities and not emitted |

Recommended budget (retail-like, safe for mass-placed or on-screen-with-others):

- **≤ ~2,000 vertices** total
- **≤ ~1,500 triangles** total
- **≤ ~20 draw calls** total (this is the big one for scene perf)
- **texture ≤ 1024×1024** (512² is plenty for most props), DXT3
- **DAT ≤ ~1 MB**

Retail reference: entity meshes are typically 1–12 draws, a few hundred to
~1,200 triangles, and ≤ ~1,000 verts (a few outliers reach ~3,000). Textures are
DXT3, up to ~1024×2048.

Works-but-extreme: a one-off placed object can go much higher — e.g. ~10k verts /
16k tris / 130 draws / ~3 MB loaded with no lag as a single furnishing. Fine for
a showpiece you place once; avoid that scale for anything mass-placed, equippable,
or commonly seen in groups.

## Coordinate handling

- glTF/FFXI axis: Blender bakes the export's root-correction node into vertex
  positions, so round-tripped coordinates map back to FFXI space by negating Y
  and Z (an involution).
- Scale is auto-aligned to the skeleton via the bones' inverse-bind matrices, so
  unit differences between DCC tools self-correct. `--scale` is a manual
  multiplier on top.
- Facing/orientation comes from how the model is built in the DCC, not from our
  transform (verified: X→X, Z→Z through calibration). Use `--rotate-y` to correct.

## Textures

Retail entity textures are standard DXT3 stored in standard block order, so
`texconv` output (`TEXCONV_PATH`) is byte-usable. New textures are DXT-encoded
and wrapped in a `0x20` section inserted alongside the model's existing textures.
The mesh's `0x8000` texture reference and the section's 16-char name are both
emitted space-padded so they compare equal. Keep texture dimensions within retail
norms (≤ ~1024×2048); very large textures may exceed game limits.

For the `0x20` header layout and DXT details see
[../../utils/texture.md](../../utils/texture.md).

## Current limitations

- No cloth-effect simulation: a re-imported mesh is a standard skinned mesh, so
  original cloth (e.g. flapping banners) becomes static.
- Animation is not part of this workflow (use `xi anim`); the skeleton and
  animations are preserved unchanged.
- Vertex colours from the DCC are not preserved; untextured primitives import with
  a neutral colour.

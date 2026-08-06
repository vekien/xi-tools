# xi mesh export

Export an FFXI entity's skeleton + mesh + textures to a standard 3D format for
editing in a DCC tool (Cinema 4D, Blender, …).

```bash
uv run xi mesh export <dat>
```

`<dat>` may be a filesystem path or a ROM-relative spec like `ROM/7/97`
(resolved against `FFXI_DIR`; the trailing `.DAT` is optional).

See also: [import.md](import.md) for the return trip.

## What it does

Reads the DAT's skeleton (`0x29`), **one** skinned-mesh section (`0x2A`, LOD 0 by
default), and embedded textures (`0x20`), then writes to
`exports/mesh/<rom>/<stem>/`:

- `<stem>.glb` — self-contained glTF 2.0 (geometry + skeleton + textures embedded)
- `<stem>.fbx` — texture-embedded FBX produced via Blender (only with `--fbx`)
- `<name>.png` — each texture, decoded for editing
- `<stem>.json` — sidecar metadata (sections, `mesh.lod` / `lod_count`, `pose`, …)

Entity DATs often carry several `0x2A` sections — progressively lower **LODs**.
Merging them superimposes geometry, so the exporter emits a single LOD (0 =
highest detail); use `--lod N` to pick another. `lod_count` in the `.json` says how
many exist.

No animation **tracks** are written — this is the static mesh/skeleton path (use
`anim export` for editable animation). By default the mesh is in its neutral
**bind pose**; `--anim`/`--frame` can bake it into an animation pose (below).

## Why `.glb` always, `.fbx` opt-in (`--fbx`)

`.glb` is always written (Blender / portable single-file). Cinema 4D's native
glTF importer silently drops materials/textures but imports textures from FBX
reliably — pass **`--fbx`** to also emit a texture-embedded `.fbx` via Blender.
FBX is **not** produced by default.

## Options

- `--fbx` — also emit a texture-embedded `.fbx` via Blender (for Cinema 4D)
- `--lod N` — export mesh LOD section `N` (default 0 = highest detail)
- `--anim NAME` — pose the skeleton by animation `NAME` (e.g. `idl`) before baking,
  instead of the neutral bind pose
- `--frame N` — keyframe index within `--anim` to pose at (default 0)
- `--alpha-scale N` — multiply texture alpha by `N` (clamped to 255) before writing
  the PNGs. **Default `2.0`** — see [Texture decode](#texture-decode). Pass `1.0` for
  the raw FFXI alpha, or higher to force more opacity.
- `--output <dir>` — override the output directory

The FBX conversion shells out to Blender (`BLENDER_PATH` in `config.py`).

### Posing by animation (`--anim` / `--frame`)

The bind pose is the neutral rest pose. In-game (and tools like Noesis/AltanaView)
you usually see the model deformed by its idle animation, so the bind pose can look
different from "how it looks in the game." To export the mesh already posed —
e.g. to match an idle crouch — pass the animation and frame:

```bash
xi mesh export ROM/5/3 --anim idl --frame 0
```

This samples that keyframe, poses the skeleton, and bakes the mesh + bones + skin
in that pose (skin stays identity at the posed bind, so it still rigs cleanly). The
chosen `anim`/`frame` are recorded in the `.json` sidecar's `pose` field. Animation
bone **scale** is not applied (rigid bake; FFXI idles use unit scale). Animations
are read from the same DAT as the skeleton.

## glTF contents

- Skeleton nodes named `bone0000`, `bone0001`, … under an `ffxi_root_correction`
  node (a 180° X rotation so the model is right-side-up in DCC tools).
- One skinned mesh from the selected `0x2A` LOD section.
- Per-vertex attributes: `POSITION`, `NORMAL`, `TEXCOORD_0`, `JOINTS_0`,
  `WEIGHTS_0` (+ `COLOR_0` for vertex-coloured primitives).
- One material per texture, named by its 16-char DAT name. Both halves of a
  symmetric mesh share the same material (the mirror is geometry, not a separate
  skin) — no `_mirrored` duplicate.

Positions/normals are assembled to world space using FFXI's two-joint skinning
(pre-weighted positions, weight on the bone translation, contributions summed) and
triangle winding is flipped from FFXI's clockwise-front to glTF's CCW-front. The
inverse-bind matrices make the skin identity at bind, so the baked pose renders
correctly *and* the rig still animates. Full conventions + the formula:
[format.md → Vertex → world-space skinning](format.md#vertex--world-space-skinning-the-assembly-math).

## Texture decode

Embedded DAT textures are decoded to PNG. Entity textures are almost always DXT3
(`0x20` section, inner type `0xA1`, `3TXD`). The decoder also handles DXT1,
palettized, and uncompressed forms. Decode details and the DXT block ordering are
in [../../utils/texture.md](../../utils/texture.md).

### Texture opacity (`--alpha-scale`)

FFXI stores texture alpha at **half scale**: `0x80` (128), not `0xFF`, means fully
opaque. The game (and xim) double it at draw time — the shaders compute
`4·vColor.a·tex.a`, and the neutral vertex alpha `vColor.a` is `0x80`, a net **×2**
on the texture alpha. A standalone exported PNG has no shader, so opaque texels
would read as **~50% transparent** in Blender/C4D/etc. — and worse, the materials use
`alphaMode: MASK` whose default `0.5` cutoff sits right at the half-scale opaque value
(`~127/255`), so nominally-opaque texels can be discarded entirely.

The exporter bakes the same ×2 into the PNG by default, so opaque texels come out
opaque while real cutouts (alpha 0) and gradients scale proportionally — matching
the in-game look. Pass `--alpha-scale 1.0` to keep the raw (faint) FFXI alpha. (The
zone exporter shares the same `--alpha-scale` knob and default.)

## Section model

The DAT is a sequence of 16-byte-aligned sections with packed metadata (low 7
bits = type, bits 7–25 = 19-bit size in 16-byte units / mask `0x7FFFF`, bit 26 =
`is_shadow`). Full mesh/texture binary layout: [format.md](format.md). Section
types used here:

- `0x20` texture
- `0x29` skeleton
- `0x2A` skinned mesh

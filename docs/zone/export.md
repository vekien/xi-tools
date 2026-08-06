# xi zone export

Export an FFXI **zone** (static area geometry) to a self-contained `.glb` and a
texture-embedded `.fbx`, with every object instanced and placed in world space.

```bash
uv run xi zone export <dat> [--fbx] [--no-sky] [--no-vfx] [--objects] [--collision] [--base] [--raw] [--alpha-scale N]
uv run xi zone export ROM/1/41            # Lower Jeuno
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
| `--alpha-scale N` | Multiply texture alpha by `N` (clamped to 255) before writing the PNGs. **Default `2.0`** — see below. Pass `1.0` for the raw FFXI alpha, or higher to force more opacity. |

## Texture opacity (`--alpha-scale`)

FFXI stores texture alpha at **half scale**: `0x80` (128), not `0xFF`, means fully
opaque. The game (and xim) double it at draw time — the shaders compute
`4·vColor.a·tex.a`, and the neutral vertex alpha `vColor.a` is `0x80`, a net **×2**
on the texture alpha. A standalone exported PNG has no shader, so opaque texels
would read as **~50% transparent** in Blender/C4D/etc.

The exporter bakes the same ×2 into the PNG by default, so opaque texels come out
opaque while real cutouts (alpha 0) and gradients scale proportionally — matching
the in-game look. Pass `--alpha-scale 1.0` to keep the raw (faint) FFXI alpha.

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
- Vertex colours (FFXI baked lighting) are written as `COLOR_0`, with the
  FFXI ×2 modulate folded in.

Re-importing an edited GLB back into the DAT is done via `xi zone import` (placements, mesh-merge) and `xi object import` (individual objects). See [import.md](import.md).

## Format reference

Mesh + texture binary, the two decryption schemes, and the ZoneDef placement
record layout: [format.md](format.md).

# Zone import — manipulating zone objects

Reverse of [zone export](export.md). Writes edits from an edited **GLB** back into
a zone DAT: move/rotate/scale/delete placements, merge new geometry into an
existing object, and (manually, for now) edit effects. Implemented in
`src/xi/zone/xi_import.py`; cipher inverse in `src/xi/zone/xi_decrypt.py`; mesh
serializer in `src/xi/zone/xi_mesh.py`. Section format: [format.md](format.md).
Effect (`0x05`) details: [../fx/effects.md](../fx/effects.md).

> **Import is GLB-only.** FBX is rejected. Export your edits as GLB.

## Recommended workflow

```
C4D  ──(FBX)──►  Blender  ──(GLB)──►  xi zone import
```

Editing in Cinema 4D is fine, but **export FBX → import to Blender → export GLB**.
Blender's GLB gives a clean **identity axis frame** and upright geometry. C4D's
*direct* glTF/glb export bakes a 90° axis frame and an odd scale (verts at 100×,
node scale 0.01, geometry flattened/upside-down) that caused extensive
upside-down/wrong-orientation debugging. The importer auto-calibrates the C4D
frame for *placements*, but for *mesh-merge* the Blender path is what's verified.

## CLI

```
xi zone import ROM/1/41 [model.glb] [--prune] [--rebuild] [--placement MESH] [--add-collision OBJ]
```

- `model` optional — defaults to the newest `.glb` in `exports/zone/<rom>/`.
- A `<dat>.base` pristine backup is created on first edit.
- **Stacking rules (not uniform):**
  - **GLB import** (placements + mesh-merge + `--prune`/`--rebuild`/`--placement`)
    always starts from `<dat>.base` — edits are recomputed from pristine, not stacked.
  - **`--add-collision` alone** layers on the *current* DAT (re-running appends again).
  - **Combined** (`zone import model.glb --add-collision blocker.obj`): collision runs
    **after** the GLB path, so one-shot works (GLB would otherwise wipe earlier
    collision by resetting from `.base`).
- Output line reports: `Placements: N of M updated`, plus `deleted`/`added`/
  `Rebuilt`/`Mesh-merged` counts when those apply.

What runs by default (no flags): placement transforms **and** automatic mesh-merge
(below). `--prune` deletes removed objects; `--rebuild` patches vertices in place;
`--placement` appends new instances of an existing mesh; `--add-collision OBJ`
appends new collision blockers from an `.obj` (can run standalone without a GLB).
See [collision.md](collision.md) for the full collision workflow.

---

## 1. Re-encryption (the safety gate)

`xi_decrypt.py` factors both zone ciphers into self-inverse passes and adds
`reencrypt_zone_mesh` (pass2→pass1) and `reencrypt_zone_objects` (mask-names→
xor-stream) — the reverse of the decrypt order. **Verified byte-exact**: every
`0x2E` mesh + the `0x1C` zonedef in ROM/1/41 decrypt→reencrypt to identical bytes.
This is the gate everything else rides on; it passes.

- `0x2E` mesh: pass1 keyed-XOR stream (if `mode=(meta>>24)&0xFF >= 5`) + pass2
  8-byte block swaps (if `u16@ds+6 == 0xFFFF`). Header bytes 0–7 not encrypted.
- `0x1C` zonedef: XOR-`0xFF` body stream + each 16-char object name masked `0x55`.
- `0x05` effects: **unencrypted** (mode byte 0) — edit directly, no round-trip.

A no-edit round-trip is **not** byte-identical for negative-scale records (see
mirror note); compare world matrices, not bytes.

---

## 2. Placement edits (`0x1C` ZoneObject records)

Each placed object = a 0x64-byte record: mesh id@0x00 (16ch), position@0x10,
rotation@0x1C (radians), scale@0x28, then a tail (effect-link@0x34=usually 0, LOD
thresholds, flags). Editing rewrites pos/rot/scale in place — object count and
geometry untouched, then `0x1C` is re-encrypted.

### Coordinate recovery (NOT a hardcoded flip)

Export parents every placement (raw TRS as its local matrix) under an
`ffxi_root_correction` node (180°-X rotation + scale `[-1,1,-1]`, net
`(x,y,z) -> (-x,-y,z)` = a det +1 rotation, to display upright + un-mirrored and
matching the in-game / level-editor orientation: FFXI is left-handed with Y stored
flipped, glTF is right-handed). A DCC round-trip may *re-express* the correction node itself (e.g.
Blender: 180°-Y + scale `(-1,-1,-1)`). So recovery is **relative to the actual
correction node**: `inv(C) · W` (exact for any node still under the correction
group, regardless of how the DCC mangled `C`). `world_matrices_by_name` walks
world matrices, captures `C`, applies `inv(C)·W` to correction-descendants.

### Axis-frame auto-calibration

Some exporters (C4D) bake an extra uniform axis frame on top. `_calibrate_frame`
detects it by aligning *unchanged* objects to the original zone (signed-permutation
+ similarity `R·M·Rᵀ`) and corrects every recovered transform. **Identity (no-op)
for the Blender/xi path.**

### Baked-scale recovery

C4D bakes an object's scale into its mesh geometry (node scale stays 1).
`_recover_uniform_scales` recovers per-instance uniform scale = (glb mesh RMS
radius / base `0x2E` RMS radius) / global-median, multiplied into the placement
scale. No-op for Blender/xi. (funsui 2× recovered exactly.)

### Changed-only writes + mirror preservation (critical)

**Only overwrite records the user actually changed** (moved > 0.5 / rescaled /
non-mirror-rotated); leave everything else byte-identical to `.base`. This is
essential for **mirror (negative-scale)** objects: a DCC drops the reflection from
the node and bakes it into geometry, so the recovered transform loses the mirror —
overwriting would flip the object to the wrong side. For a *changed* mirror object,
the base scale signs are kept and only magnitudes applied.

### `--prune` (delete objects)

Blanks the mesh id of placements whose node is absent from the model, so the engine
skips them (object count unchanged). Excludes mirror-skipped nodes. In-place.

### Operational rule (DCC)

A placement only imports if its node is a **descendant of the
`ffxi_root_correction` group**. Nodes at the scene root are skipped (the importer
prints a yellow "Skipped (outside … group)" warning) — drag them back into the
group to edit them. The FBX exporter sometimes kicks mirrored objects (e.g.
`funsui`) out to the root; the GLB path avoids this.

**Verified in-game:** move, rotate, scale, delete all work.

---

## 3. Mesh-merge (grow an existing object's `0x2E` mesh)

To add a *brand-new* mesh + placement, use [`xi object import`](../object/import.md).
To grow geometry on an object that already renders: **merge new/edited geometry
into its existing `0x2E`.** Automatic in `xi zone import` (no flag).

### Detection

A mesh is "grown" when its glb triangle count differs from the pristine `0x2E` by
> `max(32, 10%)`. The count is the **MAX over single nodes, not the sum**: a mesh
placed N times yields N nodes each at the base count — summing falsely flags all of
them (was 77 false positives; max-rule = 1 = the real merge). Helpers:
`_dat_tri_counts`, `_glb_tri_counts`, `_node_mesh_name` (exact name, then the
instance-aware `_maps_to_mesh`).

### Coordinate conversion

Per vertex: `mesh_local = inv(P_base) · frame · inv(C) · W · v`
- `W` = node's full glb scene-graph world; `C` = correction node world;
  `frame` = calibrated axis fix (identity for Blender); `P_base` = the object's
  **base** placement matrix (`trs_matrix(pos,rot,scale)`).
- `R·inv(C)·W·v` recovers the correct raw-FFXI **world** position (verified against
  unchanged objects); `inv(P_base)` re-expresses it in mesh-local so drawing at the
  base placement reproduces it.
- **The merged object's placement is KEPT AT BASE** (skipped in the placement loop)
  — geometry is baked relative to it, and the scale-recovery would otherwise
  rescale it (giant fountain). The user may freely *move* the object's group in the
  DCC; the move is captured in the baked world positions.

### Winding + normals

Reverse triangle winding (glTF RH → FFXI LH); **recompute flat normals from the
winding** (cross product), negated for FFXI orientation — so the DCC's normal state
is ignored. Nuance: winding still drives both backface visibility *and* the
recomputed normal; if faces read inside-out or mis-lit, flip them in the DCC.

### Serialization (`xi_mesh.py` `encode_zone_mesh_section`)

- Writes **tri-mesh** format (config bit0=0; indices = plain groups of 3), not
  degenerate strips (strips exploded the tri count). Round-trips exact.
- **Splits any sub-mesh exceeding the u16 limit** — > 65535 verts *or* indices —
  into multiple sub-meshes (both `numVerts` and `numIndices` are u16).
- Copies the 4-byte section name + 8-byte cipher header (mode/key/pass2) +
  author(8)/meshname(16) from the **decrypted** original (the encrypted one writes
  garbage). Forces config = tri-mesh + non-blend. Re-encrypts on output.
- def header (0x40): meshCount0@0, bbox0@0x04, **section1Off@0x1C** = 0x40,
  meshCount1@0x20, bbox1@0x24.

The grown section is spliced back and the DAT rebuilt (sequential; file just grows;
all sections preserved, parse to EOF).

**Verified in-game:** 6 fountains merged into `funsui` (3785 → 22710 tris,
`0x15600` → `0xFA3F0`), upright, solid, textured.

### Limitations

Assumes the merged object is **single-placement** (gather collects all glb nodes
matching the mesh name). Mesh-merge is **visual only** — it does not update the
client collision MZB inside `0x1C`. Author walk/block geometry separately with
`--add-collision` ([collision.md](collision.md)); bake the server navmesh from
that soup ([navmesh.md](navmesh.md)).

---

## 4. Effects (`0x05` generators)

Effects (fountain spray, fire, lamp glow, sky) live **in the zone DAT** as
unencrypted `0x05` sections. They are managed by the separate **`xi fx`**
command group — not via `zone import`. Full docs: [../dats/fx.md](../dats/fx.md).

Available commands:

```sh
xi fx json   ROM/1/41              # full JSON dump of all effects + decoded params
xi fx delete ROM/1/41 tki awa grid # remove by name or prefix
xi fx set    ROM/1/41 tki --color 00FF00 --scale-mul 4 --range 100 500
xi fx copy   ROM/1/41 lb09 --from ROM/0/60 --at-pos -20.7 -2 5.2 --name fir0
xi fx export ROM/1/41 tki5         # export its 3D mesh + decoded params as bundle
```

> **Ordering note.** `fx delete` edits the DAT in place (keeps a `.base` backup).
> `xi zone import` **rebuilds from `.base`**, so running import after `fx delete`
> will re-add the deleted effects. Always run `fx delete` *after* `zone import`.
> See [../pipelines/rom_1_41_fountain_removal.md](../pipelines/rom_1_41_fountain_removal.md)
> for the end-to-end recipe.

Effect-placed meshes (e.g. fountain splash `sibj`, bubbles `awan`) are **orphans**
— they have no `0x1C` placement record and never appear in zone export. They are
only positioned by their effect. (~64 orphan meshes in ROM/1/41.)

---

## Gotchas

- **`python -m xi.cli` does nothing** — `cli.py` has no `__main__`; it imports and
  exits silently (looks like success). Use the installed `xi` entrypoint.
- **TRS decomposition is ambiguous for negative-scale records** (mirrors with e.g.
  scale `(1,1,-1)`, rot `(π,θ,π)`). `decompose_trs` returns an *equivalent*
  factorization (world matrix matches to 1e-16), not byte-identical. Compare world
  matrices on a no-edit round-trip.
- **GLB is loss-less; FBX is not.** GLB skips the Blender round-trip that mangles
  mirror objects and mesh names. `--rebuild` is GLB-only (FBX loses glTF mesh names
  → "Mesh.001").
- **xiclient is a fan reverse-engineering** — a strong hint, not authoritative
  truth. Verify against real DAT bytes and in-game behaviour.

## Not done / future

- **Brand-new placements work** via [`xi object import`](../object/import.md)
  (registers the index across space-tree, culling tables, and collision
  transforms). Prefer that over hand-growing `0x1C`. Mesh-merge (§3) remains the
  path for *editing geometry* of an object that already exists.
- **Vertex edits with original topology** (`--rebuild`) work but coord-correctness
  not deeply verified — check in a viewer.
- **New effect types**, effect params decode, effect duplication into the CLI.
- **Collision** is client-side MZB in `0x1C` — see [collision.md](collision.md);
  server navmesh is baked from it ([navmesh.md](navmesh.md)).

# xi object import

Add a **brand-new mesh + placement** to a zone from a GLB (or raw `.zone2e` section).
Making the engine actually *render* a new object requires registering its index across
**four** separate `0x1C` structures (see *Placement registration* below) — `import` does
all four. Verified in-game in Lower Jeuno (`ROM/1/41`).

> **Sibling commands** that share the same registration:
> [`object clone`](clone.md) duplicates a mesh already in the zone;
> [`object swap-placement`](swap-placement.md) repoints an existing slot;
> [`object set-placement`](set-placement.md) just moves an existing placement.

---

## Usage

```
uv run xi object import <dat> <source> [--name N] [--pos X Y Z]
                               [--rot RX RY RZ] [--scale SX SY SZ]
                               [--draw-distance D] [--raw]
                               [--shade S] [--ao] [--no-alpha] [--two-sided]
```

| Argument / Option | Description |
|---|---|
| `<dat>` | Zone DAT or ROM-relative spec (e.g. `ROM/1/41`) |
| `<source>` | `.glb`, `.zone2e` raw section, or extensionless export path |
| `--name N` | Mesh name in the DAT (default: source filename stem). Auto-prefixed `xi_` and auto-numbered if taken (`xi_gaitou0101`, `0102`, …) |
| `--pos X Y Z` | FFXI-space position. **Omit to import the mesh only** (no placement) |
| `--rot RX RY RZ` | Rotation in radians (default `0 0 0`). FFXI lamps/props sit upright at `3.142 1.044 3.142` |
| `--scale SX SY SZ` | Scale (default `1 1 1`) |
| `--draw-distance D` | Distance-cull threshold, record `+0x40` (default **1000** = visible far). See revert guide |
| `--raw` | Use a `.zone2e` raw section instead of GLB geometry |
| `--shade S` | Brightness multiplier on the vertex colours (`1.0` = neutral `0x80`). Lower to darken. See *Shading & brightness* |
| `--ao` / `--no-ao` | Bake ambient-occlusion self-shadow into vColor. **Default off** |
| `--ao-floor F` | Darkest AO multiplier for fully-occluded verts (with `--ao`) |
| `--no-alpha` | Force the injected texture fully opaque (ignore source alpha) |
| `--two-sided` | Set the `0x2000` back-face-cull-disable flag (planes, foliage cards) |

```bash
# add the mesh only (place it later with object swap-placement, or in the editor)
uv run xi object import ROM/1/41 exports/object/rom/1/40/obj01/obj01.glb

# add + place an upright street lamp
uv run xi object import ROM/1/41 exports/object/rom/1/41/gaitou01/gaitou01.glb \
    --name gaitou01 --pos -8.804 0 -7.735 --rot 3.142 1.044 3.142
```

Get a `<source>` GLB with [`object export`](export.md) (same zone) or `zone export` /
`object export` from another zone (cross-zone import).

---

## Shading & brightness

How the imported mesh is lit is governed by its **vertex colours** and **normals** — see
[format.md → Lighting & shading](../format.md#lighting--shading--how-a-0x2e-mesh-is-lit) for
the full model. Practical notes:

- **Normals are auto-normalized on import.** A GLB authored at a large scale (e.g. 100×,
  placed at `0.01`) would otherwise bake that scale into its normals, and the retail client
  — which does **not** renormalize — then renders the mesh **fullbright, ignoring every
  brightness control**. The importer and encoder now force unit normals, so this can't
  recur. *Symptom on a legacy/hand-built mesh: bright no matter what `--shade` you set, yet
  it looks fine in the editor (the xim port renormalizes). Fix: re-import, or renormalize
  the stored `n0` in the `0x2E` section.*
- **`--shade S`** — linear brightness multiplier on the vertex colours (`1.0` = neutral
  `0x80` = the retail-default look; `0.5` = half; `2.0` washes toward white). With correct
  normals the engine lights the mesh, so a plain import at `--shade 1.0` comes in neutral
  and reads correctly.
- **`--ao` / `--no-ao`** — bake ambient-occlusion self-shadow into the vertex colours
  (creases/undersides darken). **Off by default** — neutral vColor + the engine's own
  lighting is the faithful look; turn `--ao` on for extra baked form. `--ao-floor F` sets
  how dark fully-occluded verts go.

The same flags exist on [`object replace`](replace.md) and the web editor's GLB-import
panel (which feeds [`import-json`](../zone/import-json.md) via `shade` / `ao` change-set fields).

---

## Placement registration — the four structures

A zone object only renders if its index is correctly registered in **all** of these.
They were reverse-engineered against xim's renderer (`thirdparty/xim/`, files
`ZoneDefParser.kt`, `Culler.kt`, `Scene.kt`). Missing any one produces a different,
confusing failure mode — we hit every one of them in order before it worked:

| # | Structure | Code | Symptom if missing |
|---|---|---|---|
| 1 | **Object record + space-tree leaf** | `add_placements` | Object doesn't exist / not reached by the frustum walk |
| 2 | **Leaf AABB widened to mesh bbox** | `expand_placement_bounds_points` | Large meshes clipped when the leaf box leaves view |
| 3 | **Per-object collision transform** (0xC0 array, indexed 1:1 by object) | `add_collision_transforms` | New index reads past the array → garbage transform / cull volume |
| 4 | **Culling tables (PVS)** | `add_to_culling_tables` | **The real bug.** Object visible only from certain camera positions |

**#4 is the one that gated visibility.** The client picks a culling table from the
collision floor *under the camera* (`Scene.getCullingTableIndex`), then draws a leaf's
objects only if they're in that table (`Culler.check`) — unless the floor's table is
null, where everything draws. A new object in no table therefore appears only where the
floor maps to a null table (near fountains/walls/arches) and vanishes everywhere else.
We add the new index to **all** tables, so it's visible from every camera floor.

`swap-placement` reuses an existing index but still re-runs #2-#4 (its old slot was only
in its old region's tables), so a swapped object is also visible everywhere.

---

## Change / revert guide (what we touched chasing this bug)

We went down three wrong-but-plausible theories before #4. Everything below is **kept**
because each is independently correct, but here's the honest record + how to back any of
it out if it ever causes trouble. All live in `src/xi/zone/xi_zonedef.py` (helpers) and
`xi_object.py` (the import flow).

| Change | Theory at the time | Verdict | Revert |
|---|---|---|---|
| **`--draw-distance` / record +0x40 = 1000** | "lamps cull by short draw distance" — **wrong** (real lamps use 80 and render fine) | **Keep, optional.** +0x40 is a genuine distance-cull threshold; defaulting high keeps placed objects visible at range. Not the bug fix. | Drop the `OBJ_DRAW_DISTANCE` write in `import_object` + the `--draw-distance` option to inherit the template's value. |
| **`add_collision_transforms`** (grow 0xC0 transform array) | "garbage per-object cull box" — **not the visible-culling cause** | **Keep, required for consistency.** `add_placements` bumps collision `indexCount@0x18`; the array must match or the engine reads past it. | Only safe to drop if `add_placements` also stops bumping `indexCount` (it must not — that bump prevents a visible-object buffer overrun). |
| **`expand_placement_bounds_points` to mesh bbox** | "leaf box too small, frustum-culled at angles" — partial | **Keep, defensive.** Ensures the broad-phase leaf contains the whole mesh; mirrors `import-json`. | Harmless to remove; the nearest existing leaf usually already covers small props. |
| **`add_to_culling_tables`** | PVS membership | **Keep — THIS is the fix.** | Don't. |

`OBJ_DRAW_DISTANCE = 0x40` and the three `add_*` / `_invert_affine` helpers were all added
to `xi_zonedef.py`; none touch existing read paths, so they're additive.

> The 0x40 field is also documented (with the full record layout) in [format.md](../format.md).

---

## Known caveats / future-issue watch-list

If a placed object misbehaves later, check these first — they're consequences of the
choices above, not bugs:

- **Added to ALL culling tables → no PVS occlusion.** The object renders from every
  camera floor, including *through walls* from adjacent rooms. Correct for an
  always-visible decoration; if you ever want true room-by-room occlusion, add the
  index only to the specific table(s) the target area's floor selects, not all of them.
- **No collision.** `add_collision_transforms` adds the per-object *transform* entry
  (for index/cull consistency) but **not** a collision mesh — placed objects are
  walk-through. Real collision is a separate, larger job (collision mesh + grid map).
- **`--draw-distance` defaults to 1000.** Tiny props placed this way stay visible from
  very far, which can look odd. Lower it per-object if needed.
- **Cross-zone imports get approximate cull bounds.** When the source mesh has no
  existing placement in the target zone, the `+0x80` collision-transform cull segment is
  copied from the spatially-nearest object instead of a same-mesh instance — slightly
  wrong bounds (usually harmless; the bbox is padded).
- **The grow functions assume Lower-Jeuno-style layout.** `add_to_culling_tables` /
  `add_collision_transforms` rely on the 0x1C sub-section ordering (objects → culling →
  space-tree → … → collision) and a uniform `0xC0` transform stride. They **bail safely**
  (return unchanged) on ships / zones with no collision or no culling section, but have
  only been verified on `ROM/1/41`. Re-verify counts (`node == indexCount == transforms`,
  index present in tables, DAT parses to EOF) on a new zone before trusting in-game.
- **`swap-placement` consumes the overwritten object.** The original object at that slot
  is gone. Pick a throwaway index (`xi object json`), and remember `reset`
  restores everything.

---

## Related commands

- **`xi object export`** — pull a mesh out to GLB to edit, then re-import it
- **`xi zone import-json`** — batch placement edits (move/rotate/scale/add/delete) from the web editor
- **`xi object json`** — list every placement + index
- **`xi zone reset`** — restore the DAT to pristine

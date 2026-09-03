# Zone collision mesh (MZB)

The player-collision system for FFXI zones — the invisible triangle soup the
client uses to stop you walking through walls and to stand you on floors.

> For the footstep sound/VFX system that rides on top of collision terrain
> types, see [../sounds/footsteps.md](../sounds/footsteps.md).

---

## Where collision lives

Collision is **not** the visible `0x2E` ZoneMesh, and it is **not** a separate
file. It is a triangle soup embedded inside the encrypted **`0x1C` ZoneDef**
section of the zone model DAT — the same block that holds placements and the
space tree. The client does swept-sphere-vs-triangle collision against these
tris; the server navmesh (`.nav` — see [navmesh.md](navmesh.md)) and LoS mesh
(`.obj`) are both derived from this same soup. All three systems ultimately come
from one source.

---

## Export the collision mesh

```bash
uv run xi zone export ROM/1/41 --collision
```

Writes alongside the regular zone export output:

| File | Contents |
|------|----------|
| `<stem>.collision.obj` | All collision triangles in `(-x, -y, z)` world space — overlays the `.glb` in Blender 1:1 |
| `<stem>.collision.mtl` | Materials colour-coded by type: **red** = walls, coloured by terrain for floors |
| `<stem>.collision.json` | Header offsets, grid dimensions, terrain/material histograms, world bbox — useful reference |

### Frame

Vertices are stored as `(-x, -y, z)` (FFXI world with X and Y negated). This is the
same net transform as the zone `.glb`'s `ffxi_root_correction` node
(`diag(-1,-1,1)`), so the `.collision.obj` **overlays the zone `.glb` at 1:1**
in Blender — no adjustment needed.

The transform is its own inverse, so re-import just negates X and Y again.

### Material naming scheme

Each collision triangle carries two properties — **block vs walk** and a
**terrain type** — encoded entirely in the face's Wavefront material name:

```
col_wall_<terrain>    → blocker   (stops the player; not a standable floor)
col_floor_<terrain>   → walkable  (actor stands on it; <terrain> = footstep sound)
```

`<terrain>` is one of:

| Name | Index | Surface | Footmark |
|------|------:|---------|:--------:|
| `object`       | 0  | generic / props, default | — |
| `path`         | 1  | dirt path / road | — |
| `grass`        | 2  | grass | — |
| `sand`         | 3  | sand | ✓ |
| `snow`         | 4  | snow | ✓ |
| `stone`        | 5  | stone / pavement | — |
| `metal`        | 6  | metal | — |
| `wood`         | 7  | wood / planks | — |
| `shallowwater` | 8  | shallow water | — |
| `deepwater`    | 9  | deep water | — |
| `unk0xa`       | 10 | unknown (rare) | — |

So `col_wall_stone` is a stone wall you bump into; `col_floor_grass` is grass
you walk on with grass footsteps; `col_floor_deepwater` is the bottom of a
canal. The terrain type drives the footstep sound/VFX (see
[../sounds/footsteps.md](../sounds/footsteps.md)) — `sand`/`snow` also leave
footmark decals.

**Defaults:** faces whose material is missing or not in the `col_wall_*` /
`col_floor_*` scheme are treated as `col_wall_object` — a plain solid blocker.
So for a quick zone-line wall you don't need to assign any material at all.

> In a DCC tool it's the **material** name that matters, *not* the object/mesh
> name — assign a material called e.g. `col_wall_stone` to the faces (C4D/Blender
> export it as `usemtl col_wall_stone`). The object name is ignored on import.

---

## Append new collision blockers

```bash
uv run xi zone import ROM/1/41 --add-collision blocker.obj
```

Appends the triangles in `blocker.obj` as **new collision blockers**, keeping
all existing collision untouched. No GLB model is needed — this flag can be
used standalone (layers on the current DAT). Combined with a GLB on the same
invocation (`zone import model.glb --add-collision blocker.obj`), collision runs
**after** the GLB path so blockers are not wiped by the `.base` reset — see
[import.md](import.md#cli).

### Authoring workflow

1. Export the collision mesh (see above) — opens in Blender alongside the zone
   `.glb` in the exact same world frame.
   2. Model new geometry over the exported collision in the same `(-x, -y, z)` frame.
   A **single plane is fine** — every face (walls **and** floors) is emitted
   **double-sided** automatically, so walls block from any approach and you don't
   need to worry about which way the normal faces or build a closed box. Make
   walls **tall enough to cover the player's height** (real walls are ~3-11 yalms
   tall) so the collision sphere can't step or hop over.
3. Export **only the new geometry** as a Wavefront `.obj`.
4. Run `xi zone import ROM/1/41 --add-collision blocker.obj`.

Use `usemtl col_wall_<terrain>` on faces that should block movement; `usemtl
col_floor_<terrain>` for walkable floors. Unrecognised materials default to
`col_wall_object` (blocker). Face indexing supports quads (fan-triangulated).

The obj path is **resolved against the zone's export dir** if not found as
given, so you can pass just a filename — `--add-collision blocker.obj` finds
`exports/zone/<rom>/blocker.obj`.

### Camera transparency (default) vs `--camera-block`

By default added **wall** blockers are **camera-transparent**: they stop the
player but the camera passes through (no pull-in), like FFXI's visible walls —
so an invisible barrier doesn't make the camera lurch for no on-screen reason.
Pass `--camera-block` to also block the camera on walls (like FFXI's invisible
event/zone-line barriers). **Floor** faces always get `flags=0` (camera-blocking,
like the ground) regardless. Either way the **player is always blocked**; this
only changes the camera/line-of-sight ray on walls.

Edits the zone DAT in place — layered on any existing edits, with a `.base`
backup taken on first edit. **Re-running appends again** — run
`xi zone reset ROM/1/41` to start clean.

### Safety checks

The import sanity-checks the obj before writing:

- **Full-mesh guard (hard stop):** if the obj has ≥ 50% as many triangles as the
  zone's existing collision, it's almost certainly the full exported
  `.collision.obj` (not a blocker) — appending it would duplicate the whole
  zone's collision, so the import refuses. Author only the *new* geometry.
- **Scale/position warning:** if the blocker's bounding box falls outside the
  zone's collision bounds (or is wildly larger/smaller), you get a warning — the
  usual cause is a DCC export-unit mismatch. Coordinates should be FFXI yalms
  (~hundreds for a zone like Jeuno), not tens-of-thousands (×100) or single
  digits (÷100). The warning **suggests the `--scale` factor** to fix it.

### Fixing an export-unit mismatch — `--scale`

If your DCC tool exports at the wrong unit scale, pass `--scale` to correct the
coordinates on import (multiplies every coord about the origin):

```bash
xi zone import ROM/1/41 --add-collision blocker.obj --scale 0.1   # coords came out 10x too large
```

The scale/position warning tells you the factor to use. A clean export at FFXI
scale (1 unit = 1 yalm ≈ 1 m) needs no `--scale`. The reliable check: your
blocker's coordinates should be in the same range as `41.collision.obj`
(hundreds for Jeuno).

> **Needs in-game test** to confirm client collision is live. The codec is
> byte-exact verified (Lower Jeuno round-trip; flag encoding reproduces all
> 7 material words exactly), but blocking in-game hasn't been reported yet.

---

## Server navmesh

The mob/NPC pathfinding navmesh (`.nav`) is baked from this same collision soup —
see **[navmesh.md](navmesh.md)** (`xi zone navmesh <dat>`). It's a separate
server-side artifact; rebuild it when your collision edits affect where mobs roam.

## What's next

**Full-replace** (`--collision <obj>`) — rebuild the entire collision block from
scratch (grid-bucketed, identity transforms), for major edits or fully custom
zones. Higher risk (fall-through on errors). Planned after the append path is
confirmed in-game.

---

## Technical notes

- The collision block lives at `0x1C ds+0x08 = collisionMeshOffset` (0 on
  ship-interior zones = no collision).
- Triangle records are 8 bytes: four packed `u16` (`rawP0/1/2, rawD`). Low
  bits = vertex/normal index; top nibble of each → material word:
  `hitWall = material & 0x40`; terrain = sum of bit `0x8` per nibble.
- Transforms are `0xC0`-byte per placed collision object: `toWorld` (16 f32
  column-major) + `toCollision` (inverse, 16 f32) + cull/env tail.
- **Per-object Y-cull (must be set, or the blocker never blocks).** Each `0xC0`
  collision-object record stores a world-space Y AABB at `+0xB4`/`+0xB8`; the
  retail client skips the object entirely unless the player's Y overlaps it
  (`CollisionManager.cpp` `if (obj->minY <= playerMaxY && obj->maxY >= playerMinY)`).
  A zeroed record (`minY=maxY=0`) is culled everywhere except world-Y≈0, so an
  appended blocker silently fails to block. `--add-collision` writes a **finite**
  Y AABB = the authored geometry's own Y extent ± `_CULL_MARGIN` (50 yalms), plus
  an identity 3×3 matrix at `+0x80` (the tail's leading 36-byte field — writing
  identity yields correct hit normals in-game, so "normal matrix" is the working
  interpretation; the field itself is unconfirmed — same record tail as
  [format.md](format.md)'s collision-transform entry). **Not ±1e6**: the
  camera-collision / vertical-clamp query does arithmetic with this AABB, and an
  extreme bound crashes the client the moment the camera tests a (camera-blocking)
  floor — player-walk cull is only a comparison so it would be fine, but the
  camera path chokes. A finite bracket still always survives the cull where the
  geometry is. (xim has no such cull, which is why a structurally-valid blocker
  can pass tooling tests yet not block in retail.) Collision *query* is driven by
  grid-cell → group → (object, mesh) and is not tied to placement objects — the
  `+0x18` `indexCount` is unused for those queries. It **is** used as the length
  of the per-placement collision-transform / cull array (mirrors `nodeCount`; see
  [format.md](format.md) and `xi_zonedef`), so growing placements must grow that
  array too.
- **Camera/LoS transparency is a separate flag.** The camera radius pull-in (and
  client line-of-sight) ray skips a triangle only when its mesh's
  `CollisionMeshHeader.Flags != 0` **and** the triangle's third index word has bit
  `0x4000` set (`CollisionQuery.hpp` `DoubleSidedSkipPolicy`). Player movement is
  independent — it keys off the *second* index word's `0x4000` bit, so camera
  transparency never affects blocking. In xi's encoding the third-word `0x4000`
  bit coincides with `hitWall`, so the lever is the per-mesh flag.
  `--add-collision` segregates walls and floors into separate meshes: **wall**
  meshes get `flags=1` (camera-transparent) by default or `flags=0` with
  `--camera-block`; **floor** meshes are always `flags=0` (camera-blocking, like
  the ground).
- **Collision is one-sided, and the solid side is decided by the WINDING.** Every
  triangle in every retail zone sampled (Lower Jeuno, rom/0/28, the mog-house
  template — thousands of walls and floors) obeys one rule: the stored normal is
  **anti-parallel** to the winding's cross product `(v1-v0)x(v2-v1)` (Direct3D
  clockwise-front), one facing per face. The client blocks on the side opposite
  the winding cross; the stored normal must agree with it (it drives the hit
  response). Real game walls therefore wind so their normal faces the *walkable*
  side, and solid walls are backed by a second, oppositely-wound face behind.
  `--add-collision` emits **every** face (walls **and** floors) as a pair of
  **mirror twins** — `(a,b,c)` with `-w` and `(a,c,b)` with `+w` — so each twin is
  retail-consistent and the face blocks from either approach. Flipping only the
  normal (what earlier builds did) leaves the pair single-sided on the winding's
  side: three.js boxes wind outward, so their only real facing pointed *into* the
  box and the player walked through. A single-sided collision tri crashes this
  client (verified in-game on floors), so the pair is mandatory either way. Floors
  put the up facing first (negative FFXI-Y normal) so the standable side is
  unambiguous.
- Codec: `src/xi/zone/xi_collision.py` — `parse_collision_raw` /
  `serialize_collision_raw` / `roundtrip_check` (byte-exact verified on 95
  real zone models).
- Format cross-reference: [format.md](format.md) (ZoneDef header / visibility
  structures).

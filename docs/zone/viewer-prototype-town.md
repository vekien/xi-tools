# Rendering the prototype town (`ROM10/2/12.DAT`)

Worked notes from getting **Dev Town — windmill + bridge** (and similar
prototype / town DATs) to look right in [xi-model-viewer](https://github.com/joshjka/xi-model-viewer).
The same lessons apply to other pre-production maps that park autorun effects
under the area root and ship dual mesh sections.

This is a **viewer** guide, not a retail-client conversion checklist. For
playable collision / stride work see [prototype-collision.md](prototype-collision.md)
and [prototype-zones.md](prototype-zones.md).

---

## What this zone is

| | |
|---|---|
| Path | `ROM10/2/12.DAT` (also listed under pinned “Dev Town” in the viewer) |
| Layout | Directory tree rooted at `town/` — not the classic `data/effe` + `data/mode` split |
| Geometry | Many `0x2E` meshes + `0x1C` placements (`w_mill`, buildings, grates, …) |
| Motion | Autorun `0x05` generators (`mil1`…`mil6`, `mi01`…, water, clouds) |

The interesting bits for a renderer are **not** all in the placement table.
Windmills, roof vanes, and water are driven by particle generators that *link*
to zone meshes by DatId / short name.

---

## 1. Duplicate `0x2E` sections, same name

### Symptom

The gate mesh `wgtc` loaded as a thin shell / missing body. The zone editor
showed a full gate.

### Cause

The DAT contains **two** `0x2E` sections both named `wgtc`:

1. A small “hit” / stub (~3 KB)
2. The full gate body (~44 KB)

A first-wins name map keeps the stub and drops the real geometry.

### Fix (viewer)

When inserting into the name → prims map, **keep the richer mesh** (more vertex
count), not the first:

```text
if name already mapped:
  replace only if newPrims.vertexCount > old.vertexCount
```

Also keep a **separate** list of every mesh section `{ path, id, name, prims }`
for the particle system — generators must resolve the section in *their*
directory, not a collapsed global name (same pattern as multi-weather `clod`).

### Export tools

`xi zone export` / Python `parse_zone` historically first-wins the same way.
If a GLB is missing props that the viewer shows after this fix, check for
duplicate section names before blaming placements.

---

## 2. Two-sided doors and grates (`dum_d2`, …)

### Symptom

Door/grate cards invisible from one side. Zone editor looked correct.

### Cause

The editor uses `THREE.DoubleSide` for **all** zone materials. Retail’s `0x2000`
submesh flag (no backface cull) is often **unset** on thin door geometry. The
viewer only disabled cull when `0x2000` was set (or blank texture name).

### Fix

Auto-set `noCull` when the submesh AABB is a thin slab:

```text
shortest_axis / longest_axis < 0.08  →  noCull
```

Plus the existing blank-texture and `0x2000` paths. That covers doors/grates
without forcing DoubleSide on every solid building wall.

---

## 3. Windmills: three systems, not one

ROM10/2/12 has **both** static bases and spinning wheels. Mixing them up is what
made mills look half-broken or frozen.

### 3a. Placed bases — `w_mill`

Two `0x1C` placements of mesh `w_mill`. Small building / axle housing only
(~1×2×1 local bounds). These stay **static** world geometry.

### 3b. Particle-driven wheels — `mill` via `mil1`…`mil6`

Six autorun generators under the area root:

| Gen | Mesh link | Role |
|-----|-----------|------|
| `mil1`…`mil6` | StaticMesh → `mill` | Full water-wheel / mill body |
| | RotationVelocity ≈ `(0, 0.01745, 0)` rad/frame @ 30 fps | Spin around local Y |
| | `basePosition` | Absolute FFXI world pos (not parented to `w_mill`) |

These positions are **not** the same as the two `w_mill` placements. There are
six free-standing spinning mills elsewhere in the map.

**Do not** bake `mill` as a static unplaced orphan at the origin, and **do not**
only companion-attach it without also running the particle system — you will
either get a ghost at 0,0,0 or a frozen wheel.

### 3c. Companions on `w_mill` — spin the full wheel, not the half wing

Unplaced meshes with no placement:

| Mesh | Notes |
|------|--------|
| `mill` | Full wheel (large AABB). **Companion + live Y-spin** on each `w_mill`. |
| `mil_pol` | Axle pole — static companion on `w_mill`. |
| `mil_wing` | **Half** wheel only (local X ≤ 0). Never attach — looks like a one-sided half mill. |
| `fu_in` | Roof vanes — particle-only (`mi*` gens → `fu_i` / `fu_in`). |

Viewer rules:

```text
isMillCompanion(host, mesh):
  host is w_mill AND mesh in { mill, mil_pol }

isParticleOnly:
  fu_in / fu_i / mil_wing   → never static / unplaced draw

mill on w_mill:
  zoneSpinners[] with spinY = 0.01745 * 30 rad/s
  re-bake world verts each frame (two-sided)
```

Particle system still draws `mill` at the six `mil*` world positions. Same mesh
asset, two instance paths — that matches the DAT.

### 3d. Registering zone autorun effects

Classic retail: generators under `data/effe` and `data/mode`.

This town DAT parks `mil*`, `mi*`, water, etc. **on the area root** (`town/`).
`registerZoneEffects` must:

1. Walk classic `data/effe` + `data/mode` if present
2. **Also** walk the area root (and non-`weat/` subtrees) for `autoRun` effects

Skipping (2) = silent static town (no spinning mills, no vanes).

Weather generators stay under `weat/<id>/` and are activated by weather switch,
not by zone registration.

### 3e. Resolving generator → mesh links

Links are often a **4-char stem** (`mill`, `fu_i`) while the `0x2E` name is the
full id (`mill`, `fu_in`). Resolution order that works:

1. DatId → section list (path-scoped)
2. DatId → name map
3. Exact name / id key on the mesh map
4. Prefix: mesh name starts with link (not the reverse — short keys must not
   steal unrelated meshes)

Encrypted fourccs in the raw opcode stream need the same DatId decrypt path as
the rest of the tree.

---

## 4. Unplaced meshes

Anything in `0x2E` with no `0x1C` reference is an orphan. After companions and
particle-owned meshes are claimed:

- Remaining orphans go on layer `unplaced`, drawn at identity, **off by default**
- Objects panel lists them; a visibility toggle shows origin ghosts for inspection

Never leave particle-owned meshes (`mill`, `fu_in`) on this layer or they fight
the live generators.

---

## 5. Shadows and sky shells

### Camera-following “arch” shadow

Symptom: huge soft arch on the ground that **moves with the camera** and **grows
with shadow range**.

Cause: an enclosing sky / env shell (or huge particle zone-mesh) fills the
**camera-centered** shadow cascade. Its silhouette is an arc that tracks the view.

Fixes in the viewer:

1. Do not cast from `layer === 'sky'`, blend water, unplaced, celestial batches
2. Particle cast: skip `isSkyName` / `isWaterName`, sun/moon, `followCamera`, and
   meshes with local radius ≫ prop scale (~80+)
3. Static cast: if a batch’s bounding sphere **fully contains** the cascade disc,
   skip it (enclosing shell heuristic)

### Sky domes vs zone batches

The procedural `0x2F` gradient dome is a separate draw — it should never enter
the zone batch shadow list. Textured cloud shells are particle / weather meshes;
name them with the usual sky prefixes (`clod`, `sora`, `fog`, …) so
`isSkyName` catches them.

---

## 6. Time of day (sequencer + panel clock)

Smooth TOD is a **lighting** problem more than a keyframe problem.

| Bad | Good |
|-----|------|
| 10 Hz timer + full `setSkyDome` every tick | rAF (or every frame) time apply |
| `switchWeather` on every minute change | `switchWeather` only when weather id changes |
| React `setState` @ 60 Hz | Throttle UI readout (~20 Hz); keep env clock live |

Time-only path:

1. `env.setTimeMinutes(m)`
2. `renderer.setTerrainLighting(env.getTerrainLighting())` every frame (sun eases)
3. Rebuild sky dome only when game-time moved ~30s or weather is cross-fading

Sequencer **Time** track: keyframes store `timeMinutes`; playback **lerps**
between keys. **Scene** track is weather cuts only (step + engine cross-fade).

---

## 7. Checklist for a new prototype / town DAT

When a map looks “half dead” compared to the zone editor:

1. **Mesh parse** — chained groups, blank textures, strip flag (see [prototype-zones.md](prototype-zones.md))
2. **Duplicate names** — richer-wins on the placement map; full section list for VFX
3. **Autorun effects** — register under area root, not only `data/effe`
4. **StaticMesh links** — stem / prefix resolve; don’t first-wins particle meshes away
5. **Companions** — only known kits (`mill`/`mil_pol` → `w_mill`); never half-meshes
6. **Cull** — planar auto two-sided for doors/cards
7. **Shadows** — skip enclosing shells and sky/water particle meshes
8. **TOD** — light time path + lerp keys; don’t rebuild the sky every tick

---

## Code map (xi-model-viewer)

| Concern | Where |
|---------|--------|
| Zone mesh parse, richer-wins, planar `noCull` | `ui/js/zone.js` |
| Placements, companions, spinners, unplaced | `ui/js/zoneModel.js` |
| Autorun registration, mesh link resolve | `ui/js/particle/system.js` |
| Particle draw + shadow cast filters | `ui/js/particleDrawer.js` |
| Cascades, enclosing-shell skip, spinner bake | `ui/js/renderer.js` |
| TOD apply (smooth time / weather split) | `ui/src/App.jsx` (`applyWeatherTime`) |
| Sequencer Time track + `sampleTod` | `ui/src/CameraSequencer.jsx`, `ui/js/camseq.js` |

---

## Related docs

- [prototype-zones.md](prototype-zones.md) — on-disk format differences (`0x54` placements, chained groups)
- [prototype-collision.md](prototype-collision.md) — making a prototype zone playable
- [format.md](format.md) — `0x2E` / `0x1C` / decryption
- [../fx/README.md](../fx/README.md) — `0x05` generator inspection (`xi zone json --fx`)

# FFXI Zone Rendering, LoD & Draw Distance

Research into how the client handles zone rendering, level-of-detail, frustum culling,
and draw distance — and how an Ashita v4 plugin can extend range to reduce pop-in.

Sources: xiclient (`ZoneRenderer.cpp`, `XiZone.cpp`, `XiArea.cpp`, `ZoneBlockFormat.h`,
`PositionedMeshBlock.h`, `MeshBlockInstance.h`, `ZoneLayoutData.h`, `CameraManager.h`,
`UnderscoreAtStruct.h`) — fan-made RE, treat field names and offsets as approximate until
verified against a live process.

---

## Zone data pipeline (how a zone gets into memory)

A zone is represented as one or more MZB files (Map Zone Block). The renderer holds up to
`MAX_ZONE_LOAD_COUNT` zones simultaneously — the main zone plus any sub-zones (e.g. a
connected dungeon cell). Each loaded zone maps to a `ZoneLayoutData` slot.

### ZoneLayoutData

The central per-zone struct. Relevant fields:

```cpp
struct ZoneLayoutData {
    int ZoneType;               // 0 = empty slot; non-zero = loaded
    unsigned int positionedBlockCount;
    PositionedMeshBlock* positionedBlocks; // flat array of chunk instances
    QuadTreeNode* QuadTreeRoot;            // null on older MZB format versions
    UnderscoreAtStruct* UnderscoreAtStructs;
    unsigned int UnderscoreAtCount;
    MeshBlockManager BlockManager;         // owns the actual MMB mesh data
    LightBindingEntry* LightBindings;
    int TerrainScaleX, TerrainScaleZ;
    int TerrainUnitsX, TerrainUnitsZ;
};
```

### Load sequence

1. `OpenMzb()` reads the `ZoneBlockHeader`, allocates a flat `PositionedMeshBlock[]`,
   and processes each `PositionedMeshBlockData` entry from the MZB.
2. For each chunk: `ResolveMeshReference()` → `InitializeMeshLOD()` resolves the mesh
   name to actual `MeshBlockInstance` pointers (high/mid/low detail).
3. World matrix is built: scale → rotateX → rotateY → rotateZ → translate. Inverted
   world matrix is also pre-computed and stored.
4. Bounding box corners are transformed into world space for frustum culling.
5. If MZB format version ≥ 21, a quad tree is initialized from the `QuadTreeOffset`.
   Older zones use the flat list fallback.
6. `InitWeather()` creates `XiArea` instances and links each chunk to its area via
   `AreaResourceID` (FourCC). Area controls per-chunk fog and lighting.

### MZB header flags that matter

```cpp
struct ZoneBlockHeader {
    unsigned int SizeAndVersion;            // bits 24-31 = format version
    unsigned int ChunkCountAndDecryptIndex; // bits 0-23 = chunk count
    unsigned int CollisionDataOffset;
    unsigned char TerrainScaleX/Z, TerrainUnitsX/Z;
    unsigned int GroupListOffset / QuadTreeOffset; // union, depends on version
    unsigned int LightingOffset;
    unsigned char SubstructureType;         // +1 = ZoneType in ZoneLayoutData
    unsigned char CollisionFlags;
};
```

---

## PositionedMeshBlock — the core per-chunk runtime object

Each chunk instance in memory:

```cpp
struct PositionedMeshBlock {
    char RenderType;        // 0=special, 1=submap-only, 2=normal (rendered)
    char field_B;           // 1 if SpecialEffects & 0x01 — "LoD-enabled" cull mode
    char field_F;           // 1 if UsesSpecialAnimation — deferred render pass

    MeshBlockInstance* LowDetailMesh;
    MeshBlockInstance* MediumDetailMesh;
    MeshBlockInstance* HighDetailMesh;

    Vector3 Translation, Rotation, Scaling;
    Matrix4 WorldMatrix, InvertedWorldMatrix;

    float NearThresholdSquared;  // squared LodNearDistance
    float MidThresholdSquared;   // squared LodMidDistance
    float FarThresholdSquared;   // squared LodFarDistance

    short ClockwiseCulling;      // 1 if scale.x * scale.z < 0 (mirrored)
    TextureAndAlphaMode TextureMode;

    int AreaResourceID;
    XiArea* Area;                // linked after weather init
    int LightReferences[4];      // indices into LightBindingEntry array
    Vector3 BoundingBoxCorners[8]; // pre-transformed world-space AABB corners
};
```

**RenderType** gates whether a chunk is drawn at all:
- `0` — has a non-zero `BlockID`, skipped by normal render
- `1` — belongs to a sub-map that isn't the active collision map, skipped
- `2` — normal, rendered every frame

---

## MeshBlockInstance — the shared mesh data

Multiple `PositionedMeshBlock` entries can point to the same `MeshBlockInstance`.
The instance holds the actual D3D vertex buffer data, bounding box, and animation info:

```cpp
struct MeshBlockInstance {
    char Name[16];
    int MeshType;   // 1=Static, 2=Animated, 3=StaticBump
    int MeshPartCount;
    MeshBlockPart* MeshParts;
    int AnimationFrameCount;  // 1, 16, or 32
    bool IsAnimated;
    BoundingBox BoundingBox;
    Vector3 BoundingBoxVertices[8];
    float BoundingBoxRadius;
};
```

---

## LoD — how mesh detail levels work

### Naming convention (from `InitializeMeshLOD`)

LoD variants are identified by the **last character of the mesh name**:
- `h` suffix → high detail
- `m` suffix → medium detail
- `l` suffix → low detail

The function tries all three variants from the `BlockManager`. Fallback rules:
- If only `m` exists: all three slots point to it
- If only `h` exists: all three slots point to it
- If only `l` exists: all three slots point to it
- If `h` + `m` but no `l`: low → medium, high → high
- If `h` + `l` but no `m`: medium → high

**Key implication:** most chunks only have one mesh variant. All three
(`High/Medium/LowDetailMesh`) point to the same `MeshBlockInstance`. These chunks
never visually "change" quality with distance — they just disappear at the global cull
boundary. Pop-in from these chunks is purely a draw-distance issue, not a LoD issue.

True multi-LoD objects (with `h`/`m`/`l` variants in the DAT) are relatively rare.

### Mesh name prefix — texture mode

The **first character** of the mesh name sets the texture/alpha mode:
- `_` prefix → `AlphaWrap` (alpha-tested, wrap addressing)
- `#` prefix → `OpaqueClamp` (no alpha test, clamp addressing)
- anything else → `OpaqueWrap` (default)

This is also what drives the `ClockwiseCulling` flag — negative x×z scale means the
mesh is mirrored, so winding order flips.

### LoD selection per frame

```cpp
// ZoneRenderer.cpp RenderChunk2, called per chunk per frame
if (magsq <= MidThresholdSquared) {
    if (magsq < NearThresholdSquared)
        render = HighDetailMesh;   // within near radius
    else
        render = MediumDetailMesh; // between near and mid
} else {
    render = LowDetailMesh;        // beyond mid radius
}
if (render == nullptr) return;     // chunk vanishes — this is pop-out
```

Distance is measured as squared magnitude from `CameraEyePosition` to
`chunk->Translation` (chunk origin, not bounding box center).

---

## Culling pipeline — full order per frame

Each frame, `ZoneRenderer::Draw()` calls `RenderSubStruct(i)` for each loaded zone slot.

### 1. Quad tree frustum cull (format version ≥ 21)

`DrawHelper()` walks the quad tree top-down, calling
`IsBoxOutsideFrustum(node->BoundingBoxCorners)` against the current
`ViewReversedZProjectionMatrix`. Entire subtrees are skipped if the node's AABB is
outside the frustum. Leaf nodes contain `PositionedMeshBlock*` references.

Older zones (no quad tree) iterate the flat `positionedBlocks[]` array linearly.

### 2. Distance cull (per chunk)

Two modes depending on `chunk->field_B` (`SpecialEffects & 0x01`):

```cpp
// LoD-enabled objects (field_B = 1)
float val = renderer->field_39428 * chunk->FarThresholdSquared;
if (magsq > val) return;

// Normal objects (field_B = 0)
if (magsq > DrawDistanceSq) return;
```

`field_39428` is the same multiplier from `GetAnotherSomething()` that feeds draw
distance — so LoD-enabled objects have their own per-chunk cull distance independent
of the global value.

### 3. Per-chunk bounding box frustum cull

After distance check, `IsBoxOutsideFrustum(chunk->BoundingBoxCorners)` is tested
again using the pre-transformed world-space corners stored on the chunk.

### 4. LoD selection → mesh selection → render

If a mesh is selected and non-null, the chunk is rendered. Transparent/alpha chunks
(`TextureMode == AlphaWrap`) go into a deferred list and render after all opaque chunks.

Animated chunks (`field_F = 1`) also defer — they are appended to
`DeferredRenderBlocks[]` and drawn after the main pass in a second loop.

### UnderscoreAtStructs

Some zones have `UnderscoreAtStruct` groups — up to 4 `PositionedMeshBlock` subchunks
grouped into a single animated unit (e.g. rotating or oscillating objects). These are
drawn by `DrawUnderscoreAtStructs()` after the main chunk pass. Each subchunk goes
through its own distance + frustum + LoD check via `DrawUASSubchunk()`.

---

## Draw distance — where the numbers come from

### Call chain

```
ZoneRenderer::Draw()
  → XiZone::GetDrawDistance(false)
      → XiArea::GetWorldC()           // returns WorldEnvironment->field_C
      → XiArea::GetAnotherSomething() // registry multiplier
  → DrawDistanceSq = DrawDistance²
  → far clip plane set to DrawDistance
```

### GetWorldC()

Returns `WorldEnvironment->field_C`. This is set by the weather/environment data loaded
for the zone. The `XiZone` constructor sets the default weather:

```cpp
default_weather.world.field_C = 100.0f;  // base draw distance
```

So without a real weather environment, draw distance base = 100 game units.

### GetAnotherSomething() — the multiplier

Reads registry config floats:
- `Config::MainRegistryConfig::flt104458E0` — geometry multiplier (a2=false)
- `Config::MainRegistryConfig::flt104458D0` — NPC/actor multiplier (a2=true)

If `GetWorldC() > 500.0`:
- Checks `flt10445884` (geometry) or `flt10445894` (actor); if not 1.0, clamps result to 0.5×

Zone-specific clamp via `XiZone::zone->field_1D4`:
- Positive value: result floored to `field_1D4` (minimum multiplier)
- Negative value: result capped to `|field_1D4|` (maximum multiplier)
- Whitegate specifically sets `field_1D4 = 0.7f` → caps draw distance multiplier at 0.7

### Zone-specific overrides (set at zone load in `XiZone::Open`)

| Zone | Effect |
|---|---|
| Ordelles Caves | `MinimumDrawDistance = 0.0f` (no minimum) |
| Aht Urhgan Whitegate | `field_1D4 = 0.7f` (caps multiplier to 0.7) |
| Dynamis zones | `califloat1 = 3–7` (very short draw), open world = 50 |
| Uleguerand Range | `califloat3 = 1.0f` (vs 0.4 elsewhere) |

`califloat1/2/3` are static globals on `XiZone`. Their exact role in `GetWorldC()` vs
the registry floats is not fully traced in xiclient — they appear related but the
direct chain from califloat → DrawDistance is not confirmed.

### MinimumDrawDistance

Default `43.0f`. Acts as a floor: if `base * mult < MinimumDrawDistance`, the result
clamps to `MinimumDrawDistance`. Raising this is only useful if you also raise the
base (`WorldEnvironment->field_C`) — otherwise the ceiling is unchanged.

---

## Camera system

`CameraManager` is a singleton (`CameraManager::g_pCameraManager`, also accessible via
`GameManager::instance->CameraManager`). Key fields:

```cpp
struct CameraManager {
    Matrix4 ViewMatrix;
    Vector3 CurrentEyePosition;   // camera world-space origin — used for all distance checks
    Vector3 CurrentLookAtTarget;
    float CurrentRollAngle;
    // cached/next versions of the above for interpolation
    bool IsViewStale;
};
```

`CurrentEyePosition` is what all per-chunk distance checks measure from. The view
matrix is uploaded to D3D and combined with the projection matrix to produce
`ViewReversedZProjectionMatrix` (frustum culling) and `ViewProjectionMatrix` (rendering).

The projection matrix is built with a reversed-Z approach for better depth precision:
```cpp
ReversedZProjectionMatrix.SetToReversedZPerspective(
    fovW, fovH,
    GameManager::instance->ProjectionFocalLength,
    0.2f,          // near clip
    DrawDistance   // far clip — moves with draw distance
);
```

So the far clip plane is directly tied to `DrawDistance`. Extending it extends the
Z-buffer range, which can cause z-fighting artefacts at large distances because
floating-point precision thins out.

The `CameraSplineController` is initialized from the zone resource on zone load —
this is the cinematic camera path data embedded in the zone file, used for cutscenes.

---

## Lighting

Each `PositionedMeshBlock` holds four `LightReferences[]` indices into the zone's
`LightBindingEntry` array. Up to four dynamic lights affect any one chunk.

The renderer uses D3D light slots 2–5 for chunk lights (slots 0–1 are weather/global
lights). Before rendering each chunk, `UpdateBlockLightSettings()` enables/disables
each light slot based on the chunk's references.

Lights with `LightID & 0xFF == 99` are ignored — likely placeholder/null entries.

---

## Fog and draw distance coupling

`D3DRS_FOGSTART` and `D3DRS_FOGEND` are set per-area from `XiArea::GetFog()` which
reads from the area's weather environment. Because `WorldEnvironment->field_C` feeds
both `GetWorldC()` (draw distance) and is part of the same weather struct that feeds
fog end, patching `field_C` moves both together. This is the desired behavior —
fog end should cover the draw distance horizon. If they drift apart:
- Fog end < draw distance: objects render beyond fog → look flat and unfogged
- Fog end > draw distance: objects pop out before fog reaches — hard edge

---

## What causes pop-in

| Cause | Description |
|---|---|
| Chunk has only one LOD variant | All three mesh slots → same pointer. Chunk is invisible beyond `MidThresholdSquared`, regardless of draw distance |
| `LodMidDistance` is small | Mid-threshold is close; chunk vanishes too early |
| Global `DrawDistance` is small | Chunks hit global cull before they're visually out of range |
| Fog end < draw distance | Objects render but appear flat (no fog); then vanish at draw distance |
| Dynamis/Whitegate | Zone-specific multipliers reduce draw distance regardless of settings |

---

## Ashita v4 plugin fixes

### Fix 1: Patch `WorldEnvironment->field_C` (recommended starting point)

`GetWorldC()` returns `WorldEnvironment->field_C`. Default = `100.0f`. Patching this
scales draw distance and fog end together.

```cpp
// After zone load (packet 0x0A):
// Follow XiZone::zone->WorldEnvironment, write field_C
float* worldC = (float*)((uintptr_t)worldEnv + FIELD_C_OFFSET);
*worldC = 400.0f;
```

Re-apply on weather change — the environment pointer or value may refresh.

### Fix 2: Raise `MinimumDrawDistance` on zone singleton

Raises the floor, but only effective when `base * mult` would be the binding constraint.
Combine with Fix 1.

```cpp
float* minDraw = (float*)((uintptr_t)xizonePtr + MINIMUM_DRAW_DISTANCE_OFFSET);
*minDraw = 400.0f;
```

### Fix 3: Per-frame write to `ZoneRenderer::DrawDistanceSq`

The game rewrites `DrawDistance` and `DrawDistanceSq` every frame from
`GetDrawDistance()`. Override them after the game sets them (e.g. in `EndScene`):

```cpp
renderer->DrawDistance   = 400.0f;
renderer->DrawDistanceSq = 400.0f * 400.0f;
```

Also update the projection matrix far clip if z-fighting becomes an issue — the game
sets this from `DrawDistance` too. Overriding just `DrawDistanceSq` without touching
the projection matrix means culling extends but the depth buffer doesn't.

### Fix 4: Walk chunk list on zone load, patch per-object thresholds (surgical)

Fixes the most common pop-in: chunks with a single LOD variant disappearing at their
mid-threshold. Run once after zone load, on the flat `positionedBlocks[]` array:

```cpp
for (int i = 0; i < layout->positionedBlockCount; ++i) {
    PositionedMeshBlock* chunk = layout->positionedBlocks + i;

    // If all three LOD slots point to the same mesh (single variant),
    // the object will vanish beyond MidThreshold regardless of draw distance.
    // Push mid/far out to FLT_MAX so global draw distance takes over.
    if (chunk->LowDetailMesh == chunk->HighDetailMesh) {
        chunk->MidThresholdSquared = FLT_MAX;
        chunk->FarThresholdSquared = FLT_MAX;
    }
}
```

This is one-time and cheap. It means single-LOD objects render at full quality to the
global draw distance limit rather than blinking out at their (often tight) mid-threshold.

---

## Recommended combination

1. **Fix 1** — patch `WorldEnvironment->field_C` to 400 on zone load
2. **Fix 4** — walk chunks on zone load, push thresholds on single-LOD chunks
3. **Fix 3** (optional) — per-frame override `DrawDistanceSq` as a belt-and-suspenders
4. Watch for z-fighting if the far clip plane extends — may need to tune near clip or
   accept the tradeoff

---

## Finding the pointers

Neither `XiZone::zone` nor the `ZoneRenderer` instance are trivially accessible. Both
need pattern scanning against `FFXiMain.dll`. Recommended workflow:

1. **Cheat Engine** on a running client — search for the float `43.0` (4-byte) near
   other known values from the constructor to find `MinimumDrawDistance` in the
   `XiZone` instance. Trace back to the static `XiZone::zone` pointer.
2. Similarly find `DrawDistanceSq` (changes each frame as you move) to locate the
   `ZoneRenderer` instance.
3. Encode offsets as byte-pattern scans in the plugin's `initialize()`.

Good scan anchors:
- `MinimumDrawDistance = 43.0f` in `XiZone` constructor — distinctive float adjacent
  to `field_1D4 = 0` in memory
- `default_weather.world.field_C = 100.0f` — in the same constructor, nearby
- `DrawDistance = 0` in `ZoneRenderer` constructor — then find runtime value via
  float scan for current draw distance value

---

## Notes and caveats

- xiclient is fan-made RE — field names/offsets are educated guesses. Verify everything
  against a live process before shipping.
- Fog end ties to draw distance through the weather environment. Patch both or objects
  render without fog cover at the new far distance.
- Extending the far clip plane thins out Z-buffer precision — z-fighting risk on
  overlapping surfaces at distance.
- Dynamis zones hardcode very short draw distances (califloat values 3–7 vs 40–50 for
  open world) — zone-aware handling may be needed.
- Whitegate's `field_1D4 = 0.7f` caps the draw distance multiplier — even with a large
  `field_C`, distance is limited. Override `field_1D4` to 0 to remove the cap.
- `UnderscoreAtStruct` animated chunks have their own per-subchunk distance + frustum
  check — they'll also benefit from Fix 4 if their subchunk thresholds are tight.
- Multiple zone slots (`MAX_ZONE_LOAD_COUNT`) may be loaded simultaneously. Fix 4 should
  walk all non-zero slots, not just slot 0.

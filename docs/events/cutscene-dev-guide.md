# Cutscene Developer Guide

A self-contained reference for picking up cutscene work in xi. Assumes you know
FFXI broadly but haven't touched this codebase.

> **State as of 2026-06-19.** Everything marked ✅ is verified end-to-end in the browser
> editor with a real FFXI zone (Lower Jeuno zone 126 / RoV mission 1-8). Items marked
> ⚠️ are partially done. Items marked ❌ are known-broken or not started.

---

## What Is a Cutscene in FFXI

A cutscene is the **event bytecode VM** running a scripted scene. The client receives
a server packet saying "run event N on actor A in zone Z", looks up that event in the
**Event DAT** (zone-specific, file ID 5820 + zone_id), and executes the bytecode. The
VM controls camera, NPC visibility/animation, dialogue, and world state.

No single "cutscene file" exists — a cutscene is the bytecode sequence plus a
constellation of referenced resources (camera splines, animation clips, particle
generators, dialogue strings) spread across multiple DATs.

### ★ Camera scene ids (`p` → file id) — DO NOT FORGET

Custom camera shots load a **global** scene DAT via a small ref **`p`** in the
actor's `references[]`. **Wrong band = instant client crash** even with perfect
bytes. Full rules: **[camera_scene_ids.md](camera_scene_ids.md)**.

| `p` | file-id band | Custom cameras |
|-----|----------------|----------------|
| 0..299 | 30704..31003 | Retail — leave alone |
| **300..599** | **56941..57240** | **Only safe band** |
| ≥ 600 | 70947+ | **Crashes** (verified) |

Also: DAT under **`ROM/`** (vt=1), not ROM10; look-at ~2.5m from eye; multi-point
`interpMode=4`.

---

## Architecture Overview

```
Server packet → event N, actor A, zone Z
                ↓
Event DAT (5820 + zone_id)
  ├─ Actor block for actor A
  │    └─ Event N → bytecode entry point
  └─ Scene bytecode
        ├─ 0xBA — sets NPC initial positions / headings
        ├─ 0x4E — show/hide entities
        ├─ 0x45 start_task 'z00b' → scene resource (evte) → 0x07 EffectRoutine
        │         ├─ 0x06 Route → camera spline (eye/look-at/FOV/roll/time keyframes)
        │         ├─ 0x05 ParticleGenerator → VFX
        │         └─ 0x2B SkeletonAnimation → camera-attached anim
        ├─ 0x5B — schedule gesture on entity (tag = routine name in motion package)
        ├─ 0xD0 — schedule locomotion controller ('main' player_main routine)
        └─ 0x1D/0x2B/0x48 — print dialogue from message table
```

**xi decode chain** (all backend, Python):
- `xi_event.py`: `build_cutscene_timeline()`, `parse_effect_routines()`, `parse_routine_clip()`,
  `parse_pointlist()`, `parse_routine_motion()`
- `xi_character.py`: `build_character_glb()`, `_facing_axis()`, `resolve_event_clips()`,
  `build_motion_index()`, `derive_anim_base()`
- Bridge (`xi_bridge.py`): `zone.cutscene`, `zone.cutsceneActors`, `zone.cutsceneActorGlb`,
  `zone.sceneResource`, `zone.serverEventInfo`

---

## NPC Positions — opcode 0xBA ✅

**`0xBA` `calibrate_entity_position`** sets an NPC's stage position before the scene
starts. It is NOT set_pos (0x36/0x37) — those move NPCs _during_ the scene.

Operand layout (4 selectors → `references[]`):

```
entity u32 | X selector | Z selector | Y selector | dir selector
```

- **X / Z / Y** — signed int / 1000 → world units
- **dir** — raw signed units → radians via `× 2π/4096` (4096 units per full turn). **Not** ×1000.

> ⚠️ Axis order is **X, Z, Y** — Z and Y are swapped relative to what you'd expect.

**Code**: `event_entity_positions()` in `xi_event.py`. The first `0xBA` per entity is
the initial stage position. `_cutscene_actors()` in the bridge prefers this over the
`npc_list` DB position.

**Editor yaw formula** (hardcoded, derived and verified):
```javascript
node.rotation.y = dirRad - Math.PI / 2;
```
Derivation: FFXI heading φ → forward `(cos φ, 0, -sin φ)` in FFXI space →
`(-cos φ, 0, sin φ)` after zoneRoot `scale(-1,1,-1)` → equals -Z-forward GLB world
forward `(sin θ, 0, cos θ)` when `θ = φ - π/2`.

If an NPC faces the wrong direction after this, the cause is **dynamic** — a look-at
opcode (0x79/0x4A) rotated them during the scene. The initial heading is correct.

---

## NPC Models — character assembler ✅

### Look blob

Every NPC in `npc_list` has a 20-byte `look` blob (CatsEyeXI `look_t`):

```
u16 size  — 0=standard (u16 model_id follows), 1=equipped (u8 face + u8 race)
u16 slots[8] — head,body,hands,legs,feet,main,sub,ranged
             — slot word = (slotIdx<<12)|(model_id & 0xFFF)
```

**Parser**: `parse_look()` in `src/xi/gear/xi_core.py`. Documented in
`docs/entity/npc-look.md`.

### Equipped NPCs (type 1 — most named NPCs)

Race (1-8 = HumeMale..Galka) + gear slot model IDs → real DAT paths via
`resolve_gear_dat(race, slot, model_id)` + `race_skeleton_dat(race)`.

Up to 8 gear meshes merged onto the race skeleton into ONE rigged GLB by
`build_character_glb(..., extra_clips=[])`. Multi-slot merge was "free" because
`build_gltf` already accepts a list of meshes.

### Standard NPCs (type 0 — monsters, unique named NPCs like Iroha)

Model ID → DAT via `modelid_to_file_id(model_id)` (`entity/xi_core.py` `RANGES`; see
[model-file-ids.md](../reference/model-file-ids.md)):
```python
(0,    1499, +1300)    # 0x514
(1500, 2999, +50295)   # 0xC477
(3000, 3499, +96907)   # 0x17A8B
(3500+,      +98239)   # 0x17FBF  (MODEL_FILE_OFFSET; not +98546)
```

All named `0x2A` PART sections in the model DAT are merged (deduped by name):
`hh_l`/`hh_b`/`hf_h`/`hh_h`/`wep*`. Do NOT use LOD0 only (`export_dat`) — that
gives half the model. `wep*` sections are skipped (weapon drops to bind-pose floor
when unconditionally merged).

**Facing axis bug**: unique models face different axes.
- Iroha (model 2449): faces **Z** → backend adds +90° via `facingAxis='z'`
- Lion (model 60): faces **X** → no correction (same as default)
- `_facing_axis(globals_by_joint)` in `xi_character.py` classifies from bind-pose
  joint spread; returns `'x'` or `'z'`; frontend applies `facingExtraDeg` (+90° for z).

---

## Animations ✅

### Idle (embedded in character GLB)

`_collect_clips()` embeds every `0x2B` clip from the model DAT. Idle clip is first
(`idl0`/`idl`/`idle`/`stnd`/`ids0` matched in that order). Editor loops idle via
`THREE.AnimationMixer`.

**Do not** also call `_pose_idle()` when clips are embedded — the animation drives
the joints; posing them to bind again gives a double-pose artifact.

### Event-driven gestures — opcode 0x5B

`0x5B` schedules a gesture on an entity using a **tag** (4-char FourCC like `fg00`,
`hiz0`, `fuk1`). The tag is a **0x07 EffectRoutine name** in a **motion package**,
NOT a 0x2B clip name.

**Resolution chain** (everything in `xi_event.py`):
1. `0x5B` operand has a work-selector → resolves against the event-record's `refs[]`
   → gives an offset into the **motion package** file_id = `anim_base + refs[sel]`.
2. In the motion package DAT: find the `0x07` routine named `tag`.
3. Walk routine's `sec2` command stream; opcode `0x05` SkeletonAnimationRoutine at
   `+8` gives a clip DatId (may be parameterized with `?` → prefix-match).
4. The resolved `0x2B` clip lives **in the same motion package file**.

### Finding anim_base — the consensus formula

`anim_base` is **per-event-record** and is NOT derivable from model_id or a global
table. It is recovered empirically by consensus over the on-disk motion index:

```python
# One-time scan (cached in exports/.cache/motion_idx_*.json, ~7s; 0.04s cached):
index = build_motion_index(50000, 76000)  # {routine_name: [file_id]}

# Derive base from the event's tag+ref pairs:
anim_base = derive_anim_base(tagrefs, index)
# weights rarity: a tag that appears in ≤2 files gets 5× weight
```

Verified: Iroha zone-126 event 0x3F → `anim_base=66339`. Same base used for ALL
entities animated by that event record.

### ☠ Critical gotcha: scan_file_ids() COMPACTS

`scan_file_ids(list_of_ids)` **silently drops** IDs that don't resolve to a real
DAT. The returned list is **shorter** than the input. NEVER:

```python
for fid, result in zip(range(50000, 76000), scan_file_ids(range(50000, 76000))):
    ...  # WRONG — misaligned after any gap
```

ALWAYS use each returned dict's own `h['file_id']`:
```python
for h in scan_file_ids(range(50000, 76000)):
    fid = h['file_id']  # correct
```

### Locomotion — opcode 0xD0

`0xD0` schedules the **player_main 'main' routine** (DAT 5112 = ROM/16/101) — an
idle/walk/run state machine that plays the appropriate animation based on entity
velocity. It is NOT a gesture clip; there is nothing to extract. An NPC using `0xD0`
will animate automatically once it moves.

### Layering — body + facial clips simultaneously ✅

Many gestures have a **body clip** (full rig) and a **facial clip** (joints ≥20 only).
`_clip_layer()` classifies by minimum joint index:
- `'face'` → min joint ≥ 20 (face bones only)
- `'body'` → has lower joints (full rig)

Frontend plays both simultaneously. The facial action is made **additive**
(`makeClipAdditive` + `AdditiveAnimationBlendMode`) because the body clip also
drives face joints to neutral — plain blend would fight it.

`maxLoop` for looping is in the `0x05` SkeletonAnimationRoutine command at `+30`
(NOT from the 0x2B clip header): `0` = loop forever (facial expressions), `N≥1` =
play N times then hold on final frame (gestures).

---

## Motion Splines — opcode 0x27 ✅

Entity path movement uses `0x27 FollowPoints` in a `0x07` routine → references a
`0x3E PointList` (waypoints) → Catmull-Rom interpolation over `duration` frames.

**Layout:**
- `0x27`: op@0, duration u16@6, pointListRef@8, flags0@16 (`0x08` CLEAR = reversed)
- `0x3E PointList`: u32 count, 3×u32 zeros, then `count × (vec3 + f32)` waypoints

**Decoders**: `parse_routine_motion()`, `parse_pointlist()` in `xi_event.py`. Editor
moves the NPC along `THREE.CatmullRomCurve3` in FFXI coords (= node-local space under
zoneRoot) via `csUpdateActorMotion()`.

---

## Camera ✅

Camera lives in the **scene resource** (`evte`) referenced by `0x45 start_task`.

★ **Id / path rules (crash if wrong):** [camera_scene_ids.md](camera_scene_ids.md).
Custom cameras: **`p` 300..599 only**, DAT under **`ROM/`** (not ROM10), look-at
~2.5m from eye, multi-point **`interpMode=4`**.

Scene resource structure:
- Each **shot** = a `0x07 EffectRoutine` (`sNNN`) paired with a `0x06 Route` (`cNNN`)
- Route header (+0x10 = count, +0x14 = smoothing mode enum 0..4)
- `count × 48-byte keyframes`: eye `vec3`, **focal length** (not degrees), look-at
  `vec3` (~2m from eye), roll (radians), normalized time, 12B pad

**Parser**: `parse_camera_routes()` in `xi_event.py`. Bridge caches routes+routines
per scene file via `_scene_data()`. Editor drives viewport camera along the spline.
**Writer**: `build_scene_resource` + `_write_camera_scene` (user-chosen `ROM{vt}/{path}/{file}`
from Settings ▸ Camera DAT; mid-band file id; registers root + ROM{vt} tables, base + pivot).

**Open item**: the exact easing curve each `mode` value (0..4) applies is not yet
pinned. Best guess is xiclient's 5 `CameraSmoothType` names (Linear, Decelerate,
Accelerate, DecelerateToMidpoint, AccelerateDecelerate) in that order.

---

## VFX (Particle Generators) ✅ (approximate timing)

### Where generators live

Zone cutscenes reference `0x05 ParticleGenerator` sections via nested routines.
The generators are stored in **scene resource files** alongside the camera data.

**Key pattern:** generators are `autoRun=False` — they are NOT fired automatically
by the zone's ambient VFX system. They are fired by `kmse`/`zet0`/`s062` style
routines, which are **nested deeper** than the shot routines the timeline beats
directly use. Simple per-beat extraction misses them.

**Current approach** (approximate): `csLoadVfx()` loads EVERY scene resource the
cutscene references and fires **all** their `0x05` generators across each resource's
window, pinned to the nearest cast actor's world position. Timing is approximate
(not exact to the nested routine schedule) but generators actually render.

**Proper fix (future)**: follow the shot → linked-routine → generator nesting to get
exact trigger timing and correct attach-point.

### Zone weather VFX (rain, snow, etc.) ❌

Zone DAT generators (`autoRun=False`, weather-conditional) are NOT loaded by the
editor's ambient VFX system. Only `autoRun=True` generators load. Weather-specific
VFX requires identifying the active weather, matching generators to that condition,
and loading them separately. Not implemented.

### VFX coordinate space

Particle emitters are attached to `zoneRoot` (FFXI world space group) not `scene`
directly. `ParticleEmitter(genData, effectsData, scene, camera, parent=zoneRoot)`:
- If `parent` is provided: `parent.add(meshGroup)` — no extra rotation (zoneRoot
  owns the FFXI→display transform)
- If `parent` is null (standalone particle editor mode): `meshGroup.rotation.x = -PI/2;
  scene.add(meshGroup)`

Position emitters using actor's **zoneRoot-local coords** (= FFXI world coords),
NOT `getWorldPosition()` (which gives three.js world coords).

---

## Look-At / Dynamic Facing ⚠️

Opcodes `0x79` / `0x4A` rotate an NPC toward a target during the scene.

**Done:** `xi_event.py` parses `0x4A` into typed face beats (`actor`, `target`,
`yaw`). `xi_compile.py` compiles face tracks to bytecode (`OP_LOOK_AT`). The
cutscene-author UI exposes a Face sub-track with target and talk-gesture options.

**Missing:** 3D preview playback. `csUpdateActorAnims()` does not process face-kind
tracks — NPCs still hold their initial `0xBA` heading for the whole playthrough.
Fix: read face beats from `animTracks` and tween `node.rotation.y` on the playhead.

---

## Lion's Walk — identified but scene-path uncracked ⚠️

**What's confirmed:**
- Lion uses `0xD0 'main'` = locomotion controller → will animate once she moves.
- The mission script (`scripts/missions/rov/1_08_At_Heavens_Door.lua`) has NO
  `setPos`/`pathThrough`/movement calls — Lion's walk path is NOT server-scripted.
- The 1_08 event record has NO `0x27 FollowPoints` motion spline.
- Lion's entity in this event is `0x0107E203` (zone 126, server idx 515).

**Working hypothesis:** Lion's walk is triggered by an NPC AI state or a server
quest flag that sends a move packet after the event starts. May require reading
server-side NPC behavior beyond what's in the Lua scripts. Alternatively, there is a
`0x45`/`0xD0`-style task chained to a hidden motion schedule in a nested routine not
yet traced.

**What to check next:**
1. Full `0x45`/`0x62`/`0xD0` task dump for ALL entities in event 0x3F — verify
   no `0x27` motion ref is missed.
2. Trace `0xD0` selectors for Lion entity — there are two: `refs[0x01]=0` → the
   locomotion state-machine, and `refs[0x1f]=123` → scene 30827 (camera scene for
   Lion). See if 30827 contains a `0x27`.
3. Inspect live server traffic during the cutscene (LandSandBoat + wireshark) —
   a move packet (0x00D?) would confirm the server is driving it.

---

## Prop Meshes (non-particle VFX)

Some cutscene "FX" are **prop meshes** (`0x2A` sections) — e.g. a sword that appears
in a character's hand mid-scene. These are a completely different render path from
`0x05` particle generators and are NOT currently handled. When a `0x2B` clip ref
in a routine points at a `0x2A` section instead of a skeleton animation, it's a prop.

---

## Open Problems Summary

> These are open problems specifically in the **web level editor** (`web/leveleditor/`).
> Some — particularly weather VFX — may already be solved in the separate `xi-gl2`
> renderer (`D:\xi-gl2`). Check there before starting work on any of these.

| Problem | Status | Priority |
|---|---|---|
| Look-at (0x79/0x4A) dynamic facing | ⚠️ authored+compiled, preview missing | Medium |
| Lion's walk path | ⚠️ cause identified, not cracked | Low |
| Zone weather VFX (rain/snow) | ❌ not started | Low |
| Prop mesh VFX (0x2A) | ❌ not started | Low |
| Camera easing curve per mode 0..4 | ✅ resolved (`_csEase`, all 5 modes) | — |
| VFX exact timing (nested routines) | ⚠️ approximate | Low |
| Zone ambient VFX engine (replace crude emitters) | ⚠️ partially done — sky/env-mesh path still legacy | Low |

---

## File & Function Reference

### Backend (Python)

| File | Key functions |
|---|---|
| `src/xi/event/xi_event.py` | `build_cutscene_timeline`, `parse_effect_routines`, `parse_routine_clip`, `parse_routine_motion`, `parse_pointlist`, `event_entity_positions`, `build_motion_index`, `derive_anim_base`, `parse_camera_routes` |
| `src/xi/gear/xi_character.py` | `build_character_glb`, `_facing_axis`, `resolve_event_clips`, `_load_external_clips`, `_collect_clips` |
| `src/xi/gear/xi_core.py` | `parse_look`, `resolve_gear_dat`, `race_skeleton_dat`, `model_file_id` |
| `src/xi/zone/xi_bridge.py` | `_cutscene`, `_camera_scene_fileid`, `_write_camera_scene`, `_publish_pivot_tables`, `_scene_data` |
| `docs/events/camera_scene_ids.md` | ★ `p` / file-id bands — custom camera crash rules |

### Frontend (JavaScript)

| File/function | Does |
|---|---|
| `main.js: csLoadActors()` | Resolves NPC cast, places GLBs in zoneRoot |
| `main.js: csLoadActorGlb()` | Fetches character GLB + extra motion clips, starts AnimationMixer |
| `main.js: csUpdatePlayhead()` | Per-frame: actor visibility, anim, motion, VFX |
| `main.js: csUpdateActorAnims()` | Crossfades body+facial clips, velocity→walk |
| `main.js: csUpdateActorMotion()` | Catmull-Rom motion spline |
| `main.js: csUpdateVfx()` | Edge-triggers ParticleEmitters on VFX beats |
| `web/leveleditor/ffxi/particle_runtime.js` | `ParticleEmitter` — faithful FFXI particle engine |
| `web/leveleditor/ffxi/particle_effects.js` | `parseAllEffects` — decodes generator + effect data |
| `web/leveleditor/ffxi/sections.js` | Shared DAT section parser |

### Docs

| Doc | Covers |
|---|---|
| `docs/events/format.md` | Event DAT layout, bytecode VM, work-selector / references[] model |
| `docs/events/opcodes.md` | Full opcode family reference |
| `docs/events/cutscenes.md` | High-level cutscene flow, camera, zone-swap, resource graph |
| `docs/entity/npc-look.md` | look_t blob layout, slot formula |

---

## Testing the Cutscene Player

1. `xi gui zone` (port 8777) — kills existing processes first or you get
   phantom WebSocket connections pinned to stale processes.
2. Load zone 126 (Qufim Island).
3. Events tab → Timeline → Load Cutscene → event 0x3F (63).
4. You should see: Iroha + 5 other NPCs placed in Qufim geometry, playing idle
   animations. Press Play — camera follows spline shots, NPCs gesture/face each
   other. Lion stands still (walk uncracked).
5. The "Server Path" input (default `<XI_SERVER_DIR>`) auto-resolves to the
   mission script and shows its `:event()` call.

**Kill/restart loop** (Windows):
```powershell
Stop-Process -Name "python" -Force  # or target the specific xi gui zone PID
xi gui zone
```
Then hard-refresh the browser (Ctrl+Shift+R) — the WebSocket is pinned to the old
process otherwise.

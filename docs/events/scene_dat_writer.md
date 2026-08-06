# Scene-DAT writer — SHIPPED (2026-07-07)

The compiler at [xi_compile.py](../../src/xi/event/xi_compile.py) now emits the
paired **scene resource DAT** for camera cutscenes, so the level-editor camera
track compiles to a real in-game camera. The two sections:

- **`0x06 Route`** — the camera keyframe spline (eye/look/FOV/roll/time).
- **`0x07 EffectRoutine`** — the shot timeline that fires the Route.

Reading: [xi_event.parse_camera_routes](../../src/xi/event/xi_event.py#L1043) /
[parse_effect_routines](../../src/xi/event/xi_event.py#L1091). Writing:
[build_scene_resource](../../src/xi/event/xi_compile.py) (`_build_evte` /
`_build_route` / `_build_routine` / `_build_end`). The EffectRoutine turned out to
be a fixed 144-byte template (only name / total / delay+dur / route-ref vary) — no
opcode-table guessing needed, so nothing below "What blocks writing" applies any
more; it's kept for the reverse-engineering record.

## ★ Placement + file id (do not regress)

Full rules: **[camera_scene_ids.md](camera_scene_ids.md)**.

- Register under **base `ROM/<subdir>/`**, VTABLE **1** — not `ROM10`.
- Scene ref **`p` only in 300..599** (file ids 56941..57240). **`p ≥ 600` /
  file ids 71k+ crash the client** even with perfect retail scene bytes.
- Look-at must stay **~2–3 m from eye** (not eye+forward×100).
- Multi-point routes: **`interpMode = 4`**, never 0.

## What's understood

### `0x06 Route` — fully decoded

32-byte header + N × 48-byte keyframes. Verified across 22.5k routes / 42.6k
keyframes over 14 zones:

| Offset | Type       | Field                                  |
|--------|------------|----------------------------------------|
| +0x00  | 16B zero   | padding                                |
| +0x10  | u32        | keyframe count                         |
| +0x14  | u32        | interpMode (0..4)                      |
| +0x18  | 8B zero    | padding                                |
| +0x20  | keyframe[] | 48 bytes each                          |

Per keyframe:

| Offset | Type   | Field                                        |
|--------|--------|----------------------------------------------|
| +0x00  | 3f     | eye vec3 (FFXI world)                        |
| +0x0C  | f      | **Focal length** (not degrees; default ~350). Client FOV = 2·atan2(192, focal). Editor stores degrees and converts on compile. |
| +0x10  | 3f     | look-at vec3                                 |
| +0x1C  | f      | roll (radians; retail ≈ 0)                   |
| +0x20  | f      | time normalized 0..1                         |
| +0x24  | 12B zero | pad                                        |

**We can write Route sections safely today.**

### Camera task lifetime — why every move gets a chained `h###` hold still (2026-07-21)

The client's `CameraTask` **deletes itself the moment its duration runs out**
(`CameraTask::OnMove` → `delete this; return 1` — xiclient `CameraTask.cpp`). There is no
"hold the last frame": once a bare move finishes, the camera reverts to the client's
default framing (focal 350 → vfov 57.5°). Since dialogue `0x23 wait_dismiss` parks the
event bytecode for however long the player reads, a shot's task almost always dies
mid-dialogue — the visible symptom was "in-game framing sits low-left and zoomed out vs
the editor" (the in-game view was the default camera, not the authored route).

Retail's hold idiom, confirmed in Balasiel's scene (ROM/62/82.DAT):

- A **1-keyframe Route played with dur 0** takes `CameraResource::Play`'s
  `Locked && duration == 0` early-return — `ApplyCameraSettings` once, **no task created,
  nothing to expire**. That's how retail stills (`ca06`, total=0) hold forever.
- Retail chains **two `04` commands in one routine** (`cm05`/`ca00`/`cm04`:
  `04 (1,0) still · 04 (200,200) move`, section grows 144→160 bytes, and `total` is padded
  **one frame past the last command** — 201/221).

`_build_routine(hold_route=…)` mirrors that: move `04 (D,D) cNNN` · hold
`04 (D,0) hNNN` (1-kf still of the move's end pose) · total `D+1`. `build_scene_resource`
chains it onto every shot with `duration > 0`; duration-0 shots already ARE the task-less
still and are left alone.

### `0x07 EffectRoutine` — partially decoded

Section body layout is known:

| Offset | Type | Field                                    |
|--------|------|------------------------------------------|
| +0x00  | 16B  | zero                                     |
| +0x10  | u32  | sec1 offset (section-relative)           |
| +0x14  | u32  | sec2 offset (command stream)             |
| +0x18  | u32  | sec3 offset                              |
| +0x1C  | u32  | totalDelay (frames)                      |
| ...    | ...  | sec1/sec2/sec3 bodies                    |

Reader walks the sec2 stream and buckets its `0x06 Route` refs, `0x05
ParticleGenerator` refs, etc. by the section type of what each ref points at:

```python
_ROUTINE_REF_KIND = {0x06: "camera", 0x05: "vfx", 0x2B: "anim", 0x3D: "sound"}
```

## What blocks writing

The sec2 stream's opcodes (the ones that emit a "play this Route" instruction)
aren't in our tables. The reader treats them opaquely — it scans for 4-byte
aligned FourCC refs and classifies the referenced sections. Retail routines use
opcodes we've observed but not defined.

**Emitting a malformed sec2 crashes the client on scene load.** We refuse to
guess.

## Recommended path forward

Instead of forwards-engineering the opcode table, **template-copy from retail**:

1. Pick a minimum retail EffectRoutine that only fires one Route (no anim/vfx
   siblings). Camera-shot routines like `w005` are typically pure-Route.
2. Byte-diff `w005` between two zones that share the shot shape but differ in
   Route ref (find them via `parse_effect_routines`). This isolates exactly
   which bytes encode the Route FourCC.
3. Write a `retag_effect_routine(template, new_route_tag, new_total_delay)` that
   copies the template and patches ONLY those bytes.
4. Serialize + append to the target scene DAT (section table is order-based, no
   ToC — appending works as long as headers are correct).

## What this unblocks in the compiler

Once shipped, the timeline editor's `camera` track kind becomes real. The compile
path is already scaffolded — `_step_camera` in xi_compile.py emits
`0x45 start_task <sceneRes> player player <FourCC> <duration>`. The scene_res
ref stays at 0 today (with a compile-time warning). The writer just needs to
fill that in.

## Related

- [maat_93_study.md](maat_93_study.md) — retail cutscene template we're targeting
- [event_mode_bits.md](event_mode_bits.md) — 0x38 flags for cinematic mode
- [cutscene-dev-guide.md](cutscene-dev-guide.md) — cross-references

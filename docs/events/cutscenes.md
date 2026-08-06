# Cutscenes — how a scripted scene plays

A cutscene (or any NPC interaction) is the [event bytecode VM](format.md#the-scene-bytecode-event-vm)
running an actor's scene: it locks the player, takes over the camera, animates entities,
prints [dialogue](dialogue.md), maybe pops a menu, then hands control back. This doc
walks that flow end-to-end and points at the opcodes that do each job.

> The event system is **server-driven**: the server tells the client *"run event N on
> actor A"*; the client looks up that scene in the zone's [Event DAT](format.md) and
> executes it. Many opcodes also report state back to the server (`0x43`, `0x47`).

---

## Trigger → run → release

```
1. TRIGGER   server: "run event N on actor A"  (player clicked the NPC, stepped on a
             trigger, completed a quest step, …)
                │
2. LOOK UP     Event DAT → actor A's block → find eventId N → entry-point in sceneData
                │
3. ENTER       0x20 lock player control · 0x46 take camera + hide UI ·
   CUTSCENE    0x38 CliEventModeLocal (hide entities / move camera / change movement)
                │
4. PLAY        the VM loop: move & face entities (0x1E/0x4A/0x4B/0x36/0x37),
   THE SCENE   schedule actions/animations (0x2C/0x2D/0x45 + 0x50–0x55 waits),
               open/close doors (0x4C/0x4D), load extra zones (0x34/0x35),
               print dialogue (0x1D/0x2B/0x48) and wait for dismissal (0x23),
               branch on flags / player choices (0x02/0x3E/0x24/0x25/0x40/0x41),
               pace with wait_time / frame delays / yields (0x1C/0x57/0x26/0x58)
                │
5. RELEASE     0x42/0x2E cancel-flags · restore camera/control · 0x43 tell server done ·
               0x21 END EVENT
```

---

## What the VM controls

### Camera & player presentation
- **`0x20`** — set `CliEventUcFlag`: lock the player out of controlling their character.
- **`0x46`** — enable/disable player camera control and hide menus, so the scene plays
  without HUD clutter.
- **`0x38`** — `CliEventModeLocal`: the master presentation mask — hide entities, take
  camera control, hide UI elements, move the camera, alter how movement works.

### Entities (the actors on stage)
- **`0x2C`** — create a `CMoSchedularTask` on an entity (an *action*/animation, 13 bytes);
  **`0x2D`** schedules a **zone**-level task; **`0x45`** starts a task with two entities.
  (`0x1C` is **not** a scheduler op — it is wait_time, 3 bytes.)
- **`0x50`–`0x52`** end those tasks; **`0x53`–`0x55`** **wait** for an entity / zone /
  main task to finish its current action — this is how the script stays in step with an
  animation before moving on.
- **`0x1E`** — face another entity and play the **"talking"** (mouth-moving) animation;
  **`0x4A`/`0x4B`** plain look-at / set-yaw.
- **`0x2F`/`0x33`/`0x4E`** adjust an entity's `Render.Flags0` (e.g. show/hide).

### Position & facing
- **`0x1F`/`0x31`/`0x36`/`0x37`** update the event position (and recalibrate the event
  entity); **`0x39`/`0x3A`/`0x3B`** handle directions / yaw / reading an entity's
  position; **`0x47`** pushes the player's new position to the server (packet `0x05C`).

### World
- **`0x4C`** open door / **`0x4D`** close door (set the entity's `StatusEvent` to 8/9);
  **`0x4F`** set an arbitrary `StatusEvent`.
- **`0x34`/`0x35`** load an additional zone for the scene, so it can play against
  *different geometry* (airship interiors, establishing shots). See
  [Loading another zone for a scene](#loading-another-zone-for-a-scene).

### Dialogue & menus
See [dialogue.md](dialogue.md#how-the-bytecode-reaches-a-line) — `0x1D`/`0x2B`/`0x48`
print, `0x23` waits for dismissal, `0x24`/`0x25` run a selection menu, `0x40`/`0x41`
gate which menu options are available.

### Logic, timing & control flow
- **Registers / flags**: `0x03`–`0x11` (get/set/inc/dec, bit ops), `0x12`/`0x13` rand.
- **Branching**: `0x02` `if`, `0x3E` test-bit branch, `0x44` entity-valid branch,
  `0x1A` jump, `0x1B` break-jump, `0x01` set exec-pointer directly.
- **Pacing**: `0x1C` wait_time (3 bytes), `0x57` frame delay, `0x26`/`0x58` yield the VM
  (resume next frame). This is what makes a scene play out *over time* rather than
  instantly.
- **End**: `0x21` ends the event and sets `EventExecEnd`.

### Server handshake
- **`0x43`** tells the server the client has updated / completed the event.
- **`0x27`–`0x2A`** are `ReqSet` / `GetReqStatus` helpers — conditional gates that check
  request state (often used to wait on a server response mid-scene).

---

## How the camera works

The event bytecode does **not** hand-key the camera frame-by-frame — it touches the
camera through a few mode/trigger opcodes and the path itself lives elsewhere.

> ⚠️ **Trust note.** What's solid (from Atom0s/XiEvents — real-client RE): the bytecode
> touches the camera via opcodes `0x38` (`CliEventModeLocal`), `0x46` (camera control),
> and `0xAF` (read camera position). Everything below describes how **xiclient** — a
> **fan reimplementation, not the official client** — *models* cutscene cameras. The
> class names (`CameraResource`, `SplinePath`, `CameraTask`, `CameraSmoothType`, …) are
> **xiclient's own invention**, and the spline maths / smoothing / task design **may be
> bespoke to that engine** rather than retail. **Update (2026-06-18):** xi has since
> **byte-decoded** the camera resource itself (see the confirmation note below), so
> *whether* retail stores a camera move as a dedicated resource is **no longer open** — it
> does (`0x06` Route spline + `0x07` EffectRoutine). What stays **xiclient's
> interpretation** is the spline *maths* / smoothing-curve model and class names below —
> how that keyframe data is *sampled*, not confirmed retail behaviour. See
> [README.md](README.md#source-trust--three-tiers).

> **Confirmed (2026-06-18) — camera = `0x06` Route + `0x07` EffectRoutine.** xi's
> `parse_camera_routes` (`src/xi/event/xi_event.py`) byte-decoded a real cutscene's
> camera data from the referenced scene (`evte`) resource: each shot is a `0x07`
> **EffectRoutine** (`sNNN`) paired with a `0x06` **Route** (`cNNN`) holding the
> eye/look-at/FOV **keyframe spline** — layout now **fully decoded**: a 32-byte header
> (`count` at `+0x10`, a smoothing/interp **mode** enum `0..4` at `+0x14`) then `count` ×
> 48-byte keyframes: **eye** `vec3`, **focal length** (not degrees; `FOV° = 2·atan2(192, focal)`,
> default ~350), **look-at** `vec3`, **roll** (radians), normalized **time**, + 12 zero pad
> bytes. An engine-knowledgeable
> source from the FFXI **GDTV** community independently described the same — *"the camera
> timeline VM is block type `0x07`, camera controls block type `0x06`, and the scheduler is
> the same one effects use"* — matching both our decode and the xim-derived
> [dat_sections.md](../reference/dat_sections.md) (`0x07` EffectRoutine = the shared effect
> scheduler). So *where* the camera lives, *which section types* carry it, **and the
> keyframe layout** are all **settled** (verified across 22.5k Routes / 42.6k keyframes in
> 14 zones: the two former unknown floats are **focal length** — `FOV° = 2·atan2(192, focal)`,
> default ~350 — and **roll** in radians; the trailing 12 bytes are always-zero padding). The only
> thing still unmapped is the exact **easing curve** each per-Route `mode` value selects —
> most likely xiclient's 5 `CameraSmoothType` variants (5 names ↔ the 5 observed mode
> values `0..4`). (These `0x06`/`0x07` **section types** are a different namespace from the
> [event-VM opcodes](opcodes.md#opcodes-vs-dat-section-block-types-two-namespaces) of the
> same number.)

In **xiclient's model**:

- **Camera resource** (`CameraResource`) — a small data block describing a camera move:
  a `CameraHeader` (`ControlPointCount`, `InterpFactor`, `Flags`, `SmoothingType`,
  optional `KeyframeResource`) followed by **`SplineControlPoint`s**, each holding an
  **eye position**, a **look-at target**, and an **FOV** parameter.
- **Path mode** (`CameraPathMode`): `Locked` (snap to a fixed view), `Straight` (lerp
  between two endpoints), or `Spline` (smooth curve through all control points).
- **Spline path** (`SplinePath`) builds **three** splines from the control points —
  `EyeTrack`, `LookAtTrack`, and `RollFOVTrack` — and `EvaluatePath(t)` samples eye
  position + look-at + `[FOV, Roll, PointID]` at normalized time `t ∈ [0,1]`. *(Byte-check:
  the `[FOV, Roll]` pair is exactly the two per-keyframe floats we decoded — the first is a
  **focal length**, `FOV = 2·atan2(192, focal)`, not an angle; roll in radians. See
  [camera_scene_ids.md](camera_scene_ids.md) — older "decidegrees" readings were wrong.)*
- **Smoothing** (`CameraSmoothType`): `Linear`, `Decelerate`, `Accelerate`,
  `DecelerateToMidpointThenAccelerate`, `AccelerateAndDecelerate` (S-curve) — or a custom
  progression curve from a `CMoKeyframe` resource. This is the ease-in/ease-out of the move.
  *(Byte-check: these 5 names line up with the per-Route `mode` enum `0..4` we read at the
  Route header `+0x14`; mapping each name to its value is the one piece still unconfirmed.)*
- **Camera task** (`CameraResource::CreateCameraTask(duration, caster, target)`) plays
  the resource over `duration`; each frame the `CameraTask` evaluates the progression
  curve, samples the spline, and applies eye/target/FOV/roll to the camera manager.
  Passing a **caster/target actor** attaches the move to an entity (so the camera can
  follow or frame an NPC), via `START_AT_CURRENT_POS` / `END_AT_CURRENT_POS` flags.
- During an event the renderer switches to the **`CameraSplineController`**
  (`ZoneRenderer::EventFlag`); the `CliEventMode` / `CliEventModeLocal` masks (set by
  opcodes `0x38`/`0x46`) gate camera control, HUD hiding, and movement.

The **format-agnostic** takeaway (the part that doesn't depend on xiclient's design):
the event bytecode's role for the camera is to **trigger** a camera move (for N frames,
optionally attached to an actor) and to set the camera/UI **mode flags** (`0x38`/`0x46`)
— it doesn't compute the path inline. The path data itself is stored as a `0x06` **Route**
spline keyed by a `0x07` **EffectRoutine** in the referenced scene resource (decoded by
xi `parse_camera_routes` — see above): each keyframe's **eye**, **focal length**
(`FOV = 2·atan2(192, focal)`; not decidegrees — that older reading was wrong),
**look-at**, **roll**, **time** and the per-Route smoothing **mode** are all decoded; the
only thing *not* yet pinned is the exact easing curve each `mode` value applies. The event
can also just read the live camera position (`0xAF`).

---

## Loading another zone for a scene

A cutscene can play against **different geometry than where the player is standing** — an
establishing shot of a landmark, an airship deck, a flashback. It does this by *swapping
the loaded zone* mid-scene with **`0x34`** / **`0x35`**:

- **`0x34` load_zone** — load a zone *with* `XiZone::Close`: tear down the current map and
  bring up the target's geometry/lighting.
- **`0x35` load_zone2** — load *without* `XiZone::Close`: bring a zone back without
  tearing down, used to **restore** the player's original zone at the end of the scene.

Both take a single **2-byte [work-selector → `references[]`](format.md#operand-references--the-references--work-selector-model)
operand that resolves to a zone id** — not a raw id, and not a file id.

### Worked example — Lower Delkfutt's Tower (zone 184), event `0x16`

The tower physically rises out of **Qufim Island**, so its intro cutscene shows the
exterior, then drops the player back inside:

```
+0000  0x20 lock_player          lock player input
+0002  0x46 camera               take camera control
+0005  0x45 start_task 'fdo1'    fade out  (scheduler scene resource — see below)
…      dialogue / menu (msgs 11868/11869) …
+00aa  0x34 load_zone   1880     selector 0x8018 → references[24] = 126  ► LOAD QUFIM ISLAND
…      camera shots z000–z00c, music, msg 7372 …            (play the scene on Qufim)
+0383  0x45 start_task 'fdo2'    final fade out
+03ef  0x35 load_zone2  1080     selector 0x8010 → references[16] = 184  ► RESTORE THE TOWER
+03f2  0x78 reset_time           release the clock locked earlier
+0401  0x21 end
```

The proof these operands are **zone ids** (not files): the restore at `+03ef` resolves to
**184 — the zone you're already in**, which only makes sense as "reload my own zone."

`xi event cutscene export 184` surfaces this directly — the `load_zone` lines read
`→ zone 126 Qufim Island` / `→ zone 184 Lower Delkfutt's Tower` (unnamed ids, e.g. the
540/542 Delkfutt's battlefield instances, show the number alone). In the web level
editor's **Opcodes** view the same chip is **clickable** — it opens the loaded zone
side-by-side so you can inspect both at once.

---

## The resource graph — what a cutscene references

The Event DAT holds the **full sequence** of a cutscene as bytecode, but the bytecode
**references other resources by id/FourCC** — there is no single monolithic "cutscene
file", and animations/camera/FX/audio are *not* inlined. The pieces:

| "What controls…" | Lives in | Triggered by |
|---|---|---|
| **the action sequence** (the script itself) | the **Event DAT** bytecode | the server: "run event N on actor A" |
| **camera move / spline** | a **camera resource** (spline control points; see above) | a `CameraTask` started during the event (`0x38`/`0x46` set the mode) |
| **NPC / actor animations** | separate **skeleton-animation** data (`0x2B` SkeletonAnimation; the actor's model DAT) — see [../anim/format.md](../anim/format.md) | scheduler-task opcodes `0x2C`/`0x45`/`0x63`/`0x6E` |
| **visual FX** (spells, glows, particles) | the **effect** system — `0x05` generators sequenced by `0x07` EffectRoutine | the scheduler firing a generator/routine (`0x73` cast-magic, scheduler tags) — see [../fx/effect_system.md](../fx/effect_system.md) |
| **sound / music** | `0x3D` SoundEffectPointer / the music player | `0x5C`/`0x5D` (music), `0x69`/`0x6A` (SFX) |
| **dialogue text** | the JP/NA **message tables** | `0x1D`/`0x2B`/`0x48`/`0xB0` print opcodes — see [dialogue.md](dialogue.md) |
| **which NPCs are present** | the zone's **Entity (NPC) DAT** (actors already exist in the zone) | the event toggles their visibility/status (`Render.Flags`, `StatusEvent`) and animates them — it does not spawn new geometry |

The connective tissue is a **Generator / Scheduler subsystem** that carries a *caster*
and *target* actor and runs *tags*: the event VM creates scheduler tasks, those tasks
drive entity actions, and the same scheduler fires effect generators and camera/sound
tasks — a shared runtime that events, effects, and the camera all plug into. (Atom0s's
opcode notes call the task `CMoSchedularTask`; xiclient names it `CMoSchedulerTask` /
`CYyScheduler`. The *concept* is well-supported across sources; the exact class layout is
xiclient's interpretation — see [README.md](README.md#source-trust--three-tiers).)

> **"Adding an NPC" caveat.** A cutscene doesn't add new models to a zone — it works
> with the actors the zone/server already provides (the Entity DAT + server-spawned
> event NPCs) and changes their **visibility/animation**. Genuinely new geometry would
> be a zone edit ([../object/README.md](../object/README.md)), not an event.

---

## Inspecting a cutscene in xi

`xi event cutscene export <zone>` and the web level editor's **Timeline / Opcodes** views
decode the resource graph above into a "what plays when" view:

- **NPCs (cast).** Every actor-id operand is resolved to a label — a magic role (*local
  player*, *event entity*, *party member N*) or, via the zone's Entity DAT, the **NPC name**.
  The `0x4E` visibility toggles become **NPC show/hide beats**, so the timeline's *NPCs* lane
  shows exactly who appears and when (e.g. Lower Delkfutt's event `0x16` reveals Wolfgang,
  Pherimociel, Neraf-Najiruf, … at the moment it swaps to the Qufim map). The Opcodes view
  tags each line with a `→ <name>` chip; the export prints the same.
- **Animations / emotes.** `0x6E` emote, `0x63` play-anim, `0x1E` look+talk become *Anim* /
  *Emote* beats on their entity. The scheduler **shots** (`0x45` tasks, action FourCC `z00b`…)
  are matched to the `0x07` EffectRoutine of the same name in the scene resource, and what
  that routine fires is surfaced per-shot.
- **VFX.** A shot's routine references are bucketed by the **section type** they point at:
  `0x06` Route → camera, `0x05` ParticleGenerator → **vfx**, `0x2B` → skeleton anim, `0x3D`
  → sound. The timeline's *VFX* lane + the info panel's VFX/SFX summary list what the
  cutscene plays (event-level `0x73`/`0xC4` cast-magic also become *VFX* beats).
- **Camera.** Each shot resolves to its actual `0x06` Route (via the routine, not a name
  guess), so the editor can drive the viewport camera along the real spline.
- **3D cast.** Each revealed NPC's appearance is resolved from the server `npc_list`
  [`look`](../entity/npc-look.md) and **assembled into a rigged model** (race skeleton +
  every equipped gear slot, or a fixed model) by `build_character_glb`; the editor drops
  the cast into the viewport at their positions and shows/hides them on the timeline. NPCs
  the event positions at runtime (`set_pos`, `npc_list` pos `0,0,0`) are placed
  approximately; zone-swap cutscenes stage their cast in the *loaded* zone's coords.

> Decoders: `build_cutscene_timeline` + `parse_effect_routines` (`src/xi/event/xi_event.py`),
> `build_character_glb` (`src/xi/gear/xi_character.py`); served to the editor by
> `zone.cutscene` / `zone.eventOpcodes` / `zone.cutsceneActors` / `zone.cutsceneActorGlb`.

---

## Coordinates & timing notes

- **Frame-based timing.** Waits and delays are counted in **frames** (the VM yields and
  resumes), not seconds — the same model as the effect sequencer in
  [../fx/effect_system.md](../fx/effect_system.md#timing).
- **World space is Y-down** (the same convention as zones/effects). Entity positions the
  script reads/writes follow the zone's coordinate frame — see
  [../fx/effects.md](../fx/effects.md) for the `−Y` is up caveat when hand-editing.
- **Doors & objects** moved by a cutscene are the zone's placed entities — see
  [../object/README.md](../object/README.md) for inspecting placements.

---

## Events vs. effects (don't confuse them)

- **Events** (this folder) = the **scripted scene** layer: camera, NPC motion, dialogue,
  menus, quest flow. Server-triggered. Bytecode in the **Event DAT**.
- **Effects** ([../fx/](../fx/README.md)) = the **visual** layer: particle/light
  **generators** (`0x05`) sequenced by `0x07` EffectRoutine. A cutscene can *trigger*
  effects, but the spray/glow itself is an effect, not an event.

---

## Related

- [format.md](format.md) — the Event DAT layout + full opcode families.
- [dialogue.md](dialogue.md) — the message tables the print opcodes resolve.
- [event-data.md](event-data.md) — browse real scenes' dialogue (277 zones extracted).
- Atom0s **XiEvents** — <https://github.com/atom0s/XiEvents> — authoritative opcode docs.

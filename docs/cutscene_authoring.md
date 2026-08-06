# Cutscene Authoring — Editor & Compiler Reference

How the browser cutscene editor and the native event compiler fit together, plus the
reverse-engineered format facts and the non-obvious pitfalls. Companion to
[`docs/events/scene_dat_writer.md`](events/scene_dat_writer.md),
[`docs/events/event_mode_bits.md`](events/event_mode_bits.md) and
[`docs/events/maat_93_study.md`](events/maat_93_study.md).

## Overview

A cutscene is authored on a **timeline** (tracks of keyframes) in the level editor, saved as a
`xi.cutscene.v1` JSON "def", and compiled natively to FFXI event bytecode + a camera scene
resource DAT. There is **one** view — the editable author sequencer. (The old read-only "Load
Cutscene" decode view was removed; see [Removed: Load view](#removed-load-view).)

```
Editor timeline (state.tracks)
  → buildCutscene() → xi.cutscene.v1 def  (saved to cutscene-defs/<zone>_<event>.json)
  → compile_cutscene()  [src/xi/event/xi_compile.py]
      → event bytecode spliced into the zone Event DAT (byte-exact rest of file)
      → camera scene DAT (evte + Route 0x06 + EffectRoutine 0x07)
```

## Backend — the compiler (`src/xi/event/xi_compile.py`)

`compile_cutscene(cutscene, event_dat, dialog_dat, …)` lowers the def:

1. `_timeline_to_steps` walks all tracks' keyframes in frame order, mapping each via
   `_kf_to_step(kind, cast_id, kf)` and inserting **wait steps for the frame gaps between
   keyframes** (`_MIN_WAIT_FRAMES`). Steps at the same frame are ordered by `_STEP_PRIORITY`
   (show/hide = 0, place = 1, camera/face/music/fade/anim = 2, blocking `say`/`narrate`/`wait`
   = 10; the ≥10 threshold also flushes the deferred fade-in — that's why `say` sits at 10).
2. `STEP_DISPATCH[op]` emits the opcodes for each step.

### Dialogue + gestures — `0x5B sched_ext`

Cinematic dialogue emits (per line): `0x5B` talk gesture → `0x1D`/`0x2B` print → `0x23`
wait-for-Enter → `0x5E` idle. The gesture emitter `_emit_gesture(ctx, ent, tag)` is the crux:

- **`0x5B` plays a 4-char gesture** (tlk0 / thk1 / ann0 / bow0 / …) from the **shared humanoid
  gesture bank** (`ctx.anim_bank = 60` → file **32164**) onto any entity. Byte-exact from Maat's
  retail event 74.
- ★ **Use `0x5B`, not `0x66`.** `0x5B ReadEventMotionRes` loads the bank onto the entity first;
  `0x66 ReadTpcEventMotionRes` uses the entity's *own already-loaded* motion table — NPCs like
  Maat only have idl0/wlk0/run0 loaded, so `0x66` silently no-ops.
- ★ **An actor's OWN routine outranks a same-named curated gesture** (2026-07-21). A custom
  `anim schedule add` routine named `tlk0` on a monster rig collides with the curated humanoid
  gesture tag; routing it through the `0x5B` bank force-loads *humanoid* skeleton motion onto
  the model — the bones spaz out in game (while AltanaViewer, playing the model's own routine,
  looks fine). `_emit_gesture(…, cast_id=…)` consults `ctx.own_motions` (the bridge's
  `_cast_motion_maps`, passed as `compile_cutscene(cast_motions=…)`): tag owned by the actor's
  model → `0x2C SetAction`; bank gesture the actor doesn't own → `0x5B` bank. When an own
  routine fires on a **cast** speaker, the per-line reset emits `0x6B` (stop *that* actor)
  instead of `0x5E` (event-entity only), so a looping custom talk clip actually stops.
- ★ **The bank inventory is parsed, not hardcoded** (2026-07-21). The bridge's
  `_gesture_bank_tags` reads the real bank DAT (file `32104 + bank`, default 60 → 32164) and
  passes its actual 0x07 routine tags as `compile_cutscene(bank_tags=…)` — ground truth for
  the `0x5B`-vs-`0x2C` call. **Bank 60 holds exactly 15 routines:** `ann0 ann1 han0 han1
  ika0 ika1 pas0 ski0 thk1 thk2 tlb0 tlb1 tlk0 tlk1 yor0`. The old hand-curated
  `_GESTURE_TAGS` mirror (retail-frequency harvest) was wrong on 13 of 20 tags —
  `aww0/joy0/shk0/bop0/…` live in *other* bank files retail selects per event and no-op
  against bank 60 — and missed 8 real ones. The corrected mirror survives only as the
  fallback for the DAT-less CLI path. The editor's gesture dropdown (`_gesture_dropdown`)
  offers the parsed inventory too. Normalisation also warns when a **fixed-model** rig
  (Maat 79 / Byakko 67 joints) is given a bank gesture it doesn't own — the bank binds by
  joint index and distorts on unique rigs.
- ★ **`@`-prefixed system routines never appear in anim dropdowns.** `@tl0`/`@tr0` are the
  client's auto-turn (turn-in-place) routines — schedulable, but not author anims, and
  `@tl0` sorts right beside `tlk0` so it kept getting mis-picked as a talk anim. The editor
  filters them from the keyframe/idle lists; the only deliberate `@` entry is the separate
  "↩ return to idle" (`@idle`) sentinel.

### Standalone anims (dialogue-less emotes) — `0x2C SetAction`

`_step_anim` reuses `_emit_gesture` to fire a motion with no accompanying line. The keyframe
stores `kf.anim` (a 4-char tag picked from the actor's own motion list — see
[Animation lists](#animation-lists--preview-playback)). Owner → `event_entity` (0x7FFFFFF8),
else `ctx.entity_id(actor)`. A named NPC must be **shown** in the scene first, or it no-ops.

★ **Non-gesture tags emit as `0x2C SetAction` (13 B: op + ent + ent + tag) and MUST be one
of the actor's own 0x07 scheduler-routine tags** (`ati0`/`atk0`/`cast`/`dead`/`ids0` …).
Ground truth (2026-07-21, root cause of "plays in editor, not in game"):

- The client's `0x2C` (`CodeSCHEDULOR`) calls `SetAction(ent, tag)` directly against the
  actor's **resident** resources — a mob's motions are its model DAT's 0x07 routines.
  Retail Qufim ev63 plays `ids0` this way; a 9-zone survey found **only** routine tags
  ever scheduled (`clp0`/`tlk0`/`dead`/`corp`/`sit2`…), never a raw `0x2B` clip id.
- `0x66 sched_ext2` is NOT "the entity's own loaded table with package 0": its selector is
  a **non-zero per-actor Tpc motion package** in retail (Cornelia `kka0` = package 12,
  Ru'Lude 303E = 63); selector **0 is the default humanoid TALK package** (`tlk0`/`thk1`,
  694 retail uses). Emitting `0x66` + package 0 with a mob clip id (the old compiler
  behaviour) silently no-ops in game. Per-actor packages are future work.
- The bridge runs `normalize_cutscene_anim_tags` before every compile: a raw clip id from
  an older def is rewritten to its owning routine (`at00`→`ati0`) with a warning; tags with
  no routine (`btl0`, `idl0`/`wlk0`/`run0` on humanoid rigs, `mou4`) compile but warn
  **"preview only"** — battle stance is an engine state, locomotion is movement-driven
  (give the NPC Position keyframes and the client walks it), mouth-flap rides the dialogue.

### Camera routes + interpolation — `interpMode`

A `0x06` Route body holds the eye/look/FOV keyframes; **`+0x14` is a smoothing enum 0..4**:
`0` = mixed/linear, **`1` = multi-point spline** (most common in retail), `4` = variant, `2/3`
rare. `_lower_camera_track` turns the camera track into route specs:

- **Snap** (`camKind: still`) → 1-point route (a cut).
- **Linear** (`spline`) → 2-point route (a straight glide from the previous pose). *Two points
  are a straight line regardless of interpMode.*
- **Curved** (`curved`) → a run of consecutive curved keyframes (plus the preceding anchor) is
  **chained into ONE `interpMode=4` route** so the client arcs *through* them. 3+ points give a
  real curve; 2 stay a line. (The compiler never emits mode 1: `xi_compile.py` promotes
  `smooth=0` to 4 and takes `max(smooths) or 4` on multi-point routes — the "1 = most common
  retail" note above is a decode-side observation; see `events/camera_scene_ids.md` for the
  dispute.)

☠ **The format stores only the interpMode + point list — there are NO per-point tangent
handles.** Draggable bezier handles that persist to the game are impossible. Reduce overshoot
by adding control points, or by mixing Linear on segments you want dead-straight.

### Waits & timing

FFXI events are a linear opcode list with **no clock** — they run back-to-back. `0x1C wait_time`
is the only pause. Two ways it happens:

- **Auto:** the frame gap between keyframes compiles to a `0x1C` (this is how the timeline's
  visual spacing becomes real pacing — you rarely add waits by hand).
- **Explicit Wait keyframe:** an *additional* `0x1C` on top of the auto-gap. ★ They **stack** —
  don't both space keyframes apart *and* drop a Wait for the same span, or you double the delay.

Dialogue blocks on `0x23 wait_dismiss` (player Enter), so everything after a line is
player-paced, not clock-timed. `0x23` is *not* the same as a Wait.

### Bridge endpoints (`src/xi/zone/xi_bridge.py`)

`zone.cutscene` (decode → beats), `zone.compileCutscene` (compile + write), `zone.loadCutsceneDef`
(the saved def), `zone.deleteEvent`. `_mark_custom_events` sets a per-event **`isCustom`** flag
by diffing the live Event DAT against the `.base` pristine backup — the editor gates the Delete
button to user-added events only.

## Editor — the sequencer (`web/leveleditor/viewport/cutscene.js`)

Author-only. Tracks: Camera (mandatory), Fade (auto, read-only), Actor groups (+ Dialog / Face /
Position / Anim sub-tracks carrying a castId), and flat Wait / Music / SFX / VFX.

- **Camera curve graph** (X/Y/Z eye + FOV) — toggle by clicking the CAMERA lane. `csSampleShot`
  drives BOTH the 3D viewport camera and the SVG graph; it uses **centripetal Catmull-Rom**
  (`_csCatmull`, α=0.5) for `interpMode 4` multi-point shots (the compiler's chained-route
  mode) — no overshoot/cusps at sharp turns.
- **Keyframe interactions:** drag to move; right-click for Edit / Delete (+ camera Snap/Linear/
  Curved interpolation switch); **shift-drag a marquee box** to multi-select across tracks, then
  right-click → "Delete N" or drag the group; **Delete/Backspace** removes the selection.
- **Undo/redo:** every edit snapshots `state.tracks` and pushes a `{undo,redo}` command into the
  **global** editor history — Ctrl+Z/Ctrl+Y walk keyframe edits alongside placement edits.
- **Zoom:** `csZoomBy` resizes the body and re-renders the ruler with a **zoom-aware tick
  density** (`dur / (8·csZoom)` → 30s→15s→5s…). Scroll position is preserved across rebuilds.
- **Scroll layout:** the tracks column (`.cs-seq-scrollx`) owns **both** scrollbars
  (`overflow:auto`) at the visible edges; the labels column has no scrollbar and syncs its
  `scrollTop` in JS. This is what keeps the horizontal bar in view and the columns gap-free.

### Animation lists & preview playback

**One options builder, everywhere.** Every animation dropdown in the editor — the NPCs-tab
*Default idle*, the sequencer keyframe *Anim* on Dialog tracks, and on Anim tracks — is
rendered by the ONE function `animOptionsHtml(ref, selected, {emptyLabel, emptyDisabled})`
in `panels/cutscene-author.js`. The sequencer keyframe panes (in `viewport/cutscene.js`,
which cannot import the author module back) call it via `state.animOptionsFor` on the shared
author state. There is one per-NPC cache (`_npcAnimCache`, keyed by entity hex, filled by
`fetchNpcAnimsFor` with an in-flight guard; `fetchNpcAnims` is a thin owner wrapper). When a
list arrives, `_animListArrived()` re-renders the author modal and the open keyframe pane.

- **Two kinds from one cache** (2026-07-21): the **keyframe (action) list** holds the model's
  schedulable 0x07 **routines** (`ati0`/`atk0`/`cast`/`dead`… — the "motions" AltanaViewer
  shows), each carrying the `0x2B` clip it drives so the preview can play it; clips no routine
  covers are appended as explicit "`· preview only`" entries. The **Default-idle list** holds
  the raw clips deduped to 3-char motions (`idl`/`wlk`/`btl`) — the rest pose is a
  preview/staging concept, not a scheduled action. `animOptionsHtml(ref, sel, {kind})` picks.
- **Preview mapping**: routine names don't prefix-match their clips (`ati0` ≠ `at00`), so
  `state.animTagClips` (`{castId: {routineTag: clip}}`, rebuilt in `_animListArrived` /
  `prefetchCastAnims`) feeds `csResolveClip`'s `tagMap` — the sequencer plays `ati0` as the
  GLB's embedded `at00`.
- ☠ **Do not reintroduce** `st.npcAnimList`, `st.npcAnimsByCast`, hardcoded `idl0-idl3`, or
  `<datalist>` anim inputs — each of those was a second list representation and every one of
  them drifted from the real per-actor list at some point. Deleted 2026-07-20.

**Author preview actually plays what you author.** `csRebuildAuthorAnimTracks()` derives each
staged NPC's gesture timeline (`rec.animTrack`) from the author tracks — Anim-keyframe
`kf.anim` plus per-line Dialog `kf.anim` — on every `csAuthorRefresh()` (all edit paths
funnel there) and after actor spawn. Retail cutscenes migrate their decoded gestures into
those same tracks on load (`_seedFromCutscene`), so the tracks are the single timeline
source; `sourceAnim` only supplies `motionClips` (resolved external gesture clips).

- The NPCs-tab **Default idle** flows into the author path as `rec.preferredIdle`, resolved
  exact → 3-char prefix → alias (`csResolveClip`); changing it calls `csSetActorIdle` — a
  live base-layer crossfade, no respawn.
- Picking a keyframe anim **auditions immediately** (`csAuditionAnim`) — no need to scrub to
  the frame. The next playhead move reconciles back to the timeline.
- Playback is **layered**: the idle always runs as a weight-1 base; the body motion overlays
  at weight 8. FFXI mob clips are partial-body (Byakko `at00` drives 34 of 67 joints) — fading
  the idle out freezes the rest of the rig, which reads as "nothing happens".

### Cinematic framing

Sequencer-bar toggles: **Hide UI**, **Fixed Ratio** (renders the active camera into a scissored
16:9 sub-rect = WYSIWYG with the game), and **Add Visual Crosshair** (a centre crosshair over the
camera's render rect for lining up shots). Persisted via settings.

### Event Info tab

Info + Timeline are one tab (horizontal stat table). When a saved def exists the **stats & chips
come from the def** (`_defStats` / `DEF_KIND_META` in `events-panel.js`) — so "Cam shots" is your
camera-keyframe count, not the decoded route count. Dialogue **Lines** render SMS-style with
speaker attribution (from the decoded `0x2B` speaker), colour-coded, player right-aligned.

## Removed: Load view

The read-only "Load Cutscene" playback view **decoded the published Event DAT**, so its
controls/labels differed and it showed the *chained* shot count instead of the source keyframes —
confusing. It was removed (`csOpenSequencer` deleted; the `!csAuthorMode` branch in
`csBuildSequencer` is dead). The event Info button is now **"Open Timeline Sequencer"** →
`openCutsceneAuthorFrom`, which loads the saved def first (falls back to decoded beats only for
retail cutscenes you never authored).

## Reusable UI gotchas

- **`position:fixed` inside a `backdrop-filter` ancestor.** `.cs-seq` has `backdrop-filter`,
  which makes it the *containing block* for fixed descendants — a fixed child positioned with
  viewport (`getBoundingClientRect`) coords lands off-screen. Put floating tips (scrub pill,
  drag tip) on `document.body`.
- **16:9 raycasting.** Fixed Ratio scissors the render into a sub-rect, so pointer→NDC using the
  full canvas is wrong (gizmo un-grabbable, clicks miss). Use the shared `clientToNdc` (cine-aware)
  + `getActiveViewportCamera()` for every raycast, and wrap `TransformControls._getPointer` (three
  r169 binds it per-instance) to remap into the cine rect.
- ☠ **Never `transform.camera = activeCamera`.** During playback the active camera is the cutscene
  camera, which sits *on* the camera rig; TransformControls scales the gizmo by distance-to-camera
  ≈ 0 → the axis lines blow up into a full-screen crosshair. Keep the gizmo on the viewport camera.

## File map

| Area | File |
|---|---|
| Compiler | `src/xi/event/xi_compile.py` |
| Event decode / dialogue author | `src/xi/event/xi_event.py`, `xi_author.py`, `xi_commands.py` |
| Bridge endpoints | `src/xi/zone/xi_bridge.py` |
| Schemas | `schema/event_cutscene.json`, `cutscene_dialog.json`, `cutscene_npc.json` |
| Sequencer | `web/leveleditor/viewport/cutscene.js` |
| Author modal | `web/leveleditor/panels/cutscene-author.js` |
| Event Info / Lines / stats | `web/leveleditor/panels/events-panel.js` |
| Raycast / gizmo / markers | `web/leveleditor/main.js`, `core/selection.js`, `objects/markers.js` |
| Sequencer CSS | `web/leveleditor/css/events.css` |

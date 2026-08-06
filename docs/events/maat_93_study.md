# Maat event 93 — retail cutscene reference profile

Source: `ROM/21/52.DAT` (Ru'Lude Gardens events), actor `0x010F3031` (Maat), event id `93`.
Dumped via `xi event cutscene export ROM/21/52.DAT --actor 0x010F3031 --all-events`.

This document is the **retail reference profile** the [event_cutscene](../../schema/event_cutscene.json)
schema and the future JSON→bytecode compiler target. Every opcode and FourCC below appears
in a working retail cutscene — if the compiler emits the same bytes, the client will run them.

## Scale

- 145 opcodes, 29 dialog messages (ids `10492..10520`), 17 scheduled tasks.
- No `0x67`/`0x68` HUD-hide pair — HUD hiding is packed into `0x46` + `0x38 CliEventModeLocal`.
- No inline animation opcodes (`0x63`/`0x5B`) — animations are driven from the scheduled
  camera/scene routines, not the event bytecode.

## Opcode profile

| op | name | count | role in this cutscene |
|----|------|-------|----------------------|
| `0x20` | lock_player | 1 | Prologue — arg=`01` enables `CliEventUcFlag` |
| `0x42` | cancel_set | 1 | Prologue — clears `CliEventCancelSetData` (uninterruptible) |
| `0x46` | camera | 1 | Prologue — arg=`01` client takes camera + hides HUD menus |
| `0x38` | event_mode | 1 | Prologue — selector `0x800c` → `references[12]` sets `CliEventModeLocal` bitfield |
| `0x22` | (SetEventHideFlag) | 1 | Prologue — arg=`01` on current event entity |
| `0x97` | (WindBase/Width) | 1 | Prologue — freezes zone wind (selectors `0x800b 0x800d`) |
| `0x45` | start_task | 17 | Fires FourCC scheduled tasks (camera shots, fades, overlays, quest-completion cue) |
| `0x55` | wait_for_scheduler | 13 | Blocks until the previous `0x45` main/load task completes |
| `0x27` | ReqSet | 11 | Server-request per beat (motion / quest state) |
| `0x29` | (schedule task) | 3 | `0x2C`-family entity schedule |
| `0x2A` | (schedule task) | 2 | Zone-based schedule variant |
| `0x4A` | look_at | 6 | Face target (Maat ↔ player pairs) |
| `0x4E` | render_flag | 2 | Hide/show entities |
| `0x76` | render_flag_yield | 3 | Yield until entity render flag set |
| `0x6F` | wait_time | 3 | Sleep until `WaitTime` reaches 0 |
| `0x1C` | wait_time | 14 | Set/decrement `WaitTime` |
| `0x5C` | music | 4 | Play BGM (2 pairs of `<slot 0>` + `<slot 1>`) |
| `0x5D` | music_vol | 1 | Ease music volume |
| `0x2B` | print_msg2 | 29 | Speaker-tagged dialog line |
| `0x23` | wait_dismiss | 29 | Wait for player to page/dismiss each line |
| `0x1A` | jump | 1 | Internal branch |
| `0x21` | end | 1 | Epilogue |

## FourCC scheduled tasks (`0x45 start_task`)

Every `0x45` fires a scheduler task by 4-character tag. The tag names an
`0x07 EffectRoutine` section inside a linked scene resource DAT (see
[cutscenes.md](cutscenes.md)); each shot routine is paired with a `0x06 Route`
holding eye/look/FOV keyframes. Retail-observed naming:

| tag | count | role |
|-----|-------|------|
| `fdo1` | 1 | **Fade out** — pre-scene curtain-in |
| `fdi1` | 1 | **Fade in** — post-scene curtain-out |
| `w005` .. `w014` | 11 (one repeat) | **Camera shots** — numbered timeline segments |
| `ovl1` | 3 | **Overlay** — 2D text/graphic layer |
| `qstc` | 1 | **Quest completion cue** — final `start_task` before `end` |

Sequence in this event: `fdo1 · w005 · fdi1 · w006 · ovl1 · w007 · ovl1 · w008 · w009 · w010 · w011 · w012 · w010 · w013 · ovl1 · w014 · qstc`.

The `w010` repeat is telling — the same shot can be re-fired mid-scene (loop / callback / second cast angle). The compiler must permit steps referencing the same shot id more than once.

## `0x45 start_task` operand layout — reverse-engineered

Observed args (16 bytes after the opcode byte): `0a80 f0ffff7f f0ffff7f 66646f31 0b80`

| offset | bytes | meaning |
|--------|-------|---------|
| 0..1 | `0a 80` | selector A → `refs[10]` (typically the **scene resource** file id ref) |
| 2..5 | `f0 ff ff 7f` | entity A = `0x7FFFFFF0` (local player, ACTOR_MAGIC) |
| 6..9 | `f0 ff ff 7f` | entity B = `0x7FFFFFF0` |
| 10..13 | `66 64 6f 31` | **FourCC tag** = `"fdo1"` (little-endian bytes read left-to-right) |
| 14..15 | `0b 80` | selector D → `refs[11]` (usually a duration / callback ref) |

The compiler emits these six fields per `camera` / `fade` step; `refs[]` grows to
absorb the two selector-referenced values.

## Prologue template

```
0x20 01                              ; lock_player
0x42                                 ; cancel_set (uninterruptible)
0x46 01                              ; camera mode 1
0x45 <sceneRes> player player fdo1 <dur>  ; start_task fade-out (pre-curtain)
0x55 <sceneRes> player player fdo1        ; wait for fade-out
0x38 <selector>                      ; CliEventModeLocal bitfield
0x29 <selector> player <slot>        ; entity schedule (music? state?)
0x97 <selA> <selB>                   ; freeze zone wind
0x22 01                              ; SetEventHideFlag on event entity
```

## Body pattern (per shot)

Each of the 12+ shots follows:

```
0x4E <flags> <entity>                ; (optional) hide/show
0x4A <a> <b>                         ; (optional) look-at pair
0x45 <sceneRes> pA pB <wNNN> <dur>   ; start camera shot
0x27 03 <entity> <state>             ; server ReqSet — advance quest state
0x55 <sceneRes> pA pB <wNNN>         ; wait for shot
0x2B <entity> <msg-selector>         ; print message
0x23                                 ; wait for dismiss
(repeat print_msg2/wait_dismiss for each line in the shot)
0x1C <selector>                      ; wait_time (inter-shot pause)
```

## Epilogue template

```
0x45 <sceneRes> player player fdi1 <dur>  ; start_task fade-in
0x45 <sceneRes> player player qstc <dur>  ; start_task quest cue
0x21                                       ; end
```

## Message-id density

29 messages sequential from `10492` → `10520`. This confirms Maat 93 authors its
strings by **appending a contiguous block** to the dialog DAT — matching the
`append_dialog_lines` strategy `xi event dialogue new` already ships.

## What we cannot resolve from this dump alone

- **`0x38 CliEventModeLocal` bit-by-bit semantics** — we see the operand
  (`selector 0x800c` → `refs[12]`) but the runtime interprets that value as a
  bitfield (fade curtain / HUD hide / camera hide / movement lock). Compare a
  fade-heavy event vs a fade-less event to isolate the fade bit.
- **`sceneRes` file id** — the actual scene DAT that owns `fdo1`/`w005`/...
  Read `refs[10]` and resolve via `30704 + datid_helper(v)` (retail formula).
  Do this in a second pass with `_scene_data` (bridge helper).
- **`qstc` semantics** — plausibly a server-completion signal, but its FourCC
  isn't in xi's decode tables. Ship the compiler treating it as a plain
  scheduled task; verify against other quest events.

## Next actions

1. **Compiler skeleton** (`src/xi/event/xi_compile.py`) — lower each step
   in [event_cutscene.json](../../schema/event_cutscene.json) to the templates
   above; emit through the existing `serialize_actor` / `build_event_dat` writer.
2. **Scene-DAT writer** — extend the `parse_camera_routes` inverse: emit a
   `0x06 Route` + `0x07 EffectRoutine` pair for each `camera.shots[]` entry.
3. **`0x38` bit inventory** — dump 6-8 retail events with known behavior
   (fade / no-fade / HUD-only / movement-lock) and diff their `0x38` operands.
4. **`xi dats build` wiring** — teach the event action handler to accept
   `resources.cutscene`, parse it as `xi.cutscene.v1`, and dispatch to
   `xi_compile.emit_cutscene`.

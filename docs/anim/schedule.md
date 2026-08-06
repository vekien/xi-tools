# xi anim schedule

Create the **`0x07` scheduler routine** a cutscene needs to play a clip.

```bash
uv run xi anim schedule add    <dat> --clip tlk0 [--tag NAME] [--loop/--no-loop] [--loops N]
                                       [--dur N] [--blend N] [--trans-in N] [--trans-out N]
uv run xi anim schedule create <dat> [--tag NAME] [--clip A --clip B …]
                                       [--loop-last/--no-loop-last] [--blend N]
uv run xi anim schedule edit   <dat> <tag> [--blend N] [--trans-in N] [--trans-out N]
                                       [--loop/--no-loop] [--loops N] [--dur N]
uv run xi anim schedule copy   <dat> <src_tag> <dst_tag>
uv run xi anim schedule list   <dat>
```

## Why this exists

A cutscene dispatches animation with event opcode **`0x2C SetAction`**, which fires a
4-char **action tag** against the actor's *resident* resources. The client only ever
schedules **`0x07` routine** tags — a 9-zone survey of retail event DATs found *only*
routine tags scheduled (`clp0`/`tlk0`/`dead`/`sit2`…), **never** a raw `0x2B` clip id
like `at00`/`idl0`. See [../cutscene_authoring.md](../cutscene_authoring.md).

So a freshly imported clip (`anim import`) is **invisible to the cutscene author** — the
"Anim" dropdown lists the model's routines (via `list_look_animations` → `_model_motions`),
not raw clips. Wrapping the clip in a routine makes it selectable and playable.

```
anim import  → adds the tlk0 CLIP   (0x2B)   ← plays nothing on its own
anim schedule → adds the tlk0 ROUTINE (0x07)  ← what a cutscene can SetAction
```

## `schedule add`

Clones the model's cleanest single-clip routine (e.g. `corp` — sec2 is just *start →
play one clip → end*) and retargets it: renames the section to `--tag`, points its
`0x05` command at `--clip`, sizes its playback window, and sets its loop mode. The new
routine is spliced in beside the donor so its clip reference resolves the same way.

- `--clip NAME` *(required)* — the `0x2B` clip to play (must already be in the DAT).
- `--tag NAME` — the routine/action tag the cutscene fires (≤4 chars). **Default = the
  clip name** (`tlk0`). This is what shows in the dropdown.
- `--loop / --no-loop` *(default `--loop`)* — **loop forever** (`maxLoops=0`; plays
  until the cutscene issues the next action) vs **play once** and hold the last frame
  (`maxLoops=1`). Overridden by `--loops N`. Loop is *baked into the routine* — that's
  why the editor has no loop toggle; set it here.
- `--loops N` — `maxLoops` as a full u16: `0` = forever, `N` = play N times then hold
  (retail cast uses e.g. `28`). Overrides `--loop`/`--no-loop`.
- `--dur N` — playback window (u16). Default = `2 × clip length` (natural speed).
- `--blend N` *(default `15`)* — **animation blending**: crossfade in AND out, in
  frames (30/s) — the `0x05` command's `transIn`/`transOut` fields (`+24`/`+28`).
  This is the client's own blend mechanism — retail routines use 8-20 (a tiger's
  `ati0`/`atf0` are `10/10`/`8/8`, `dead` fades in over 20). `0` = hard snap.
- `--trans-in N` / `--trans-out N` — override one side of `--blend` (e.g.
  `--blend 8 --trans-out 20` = snappy entry, slow release). ★ The clone must SET
  these: snap-style donors (`corp`/`gurd` pose instantly by design) carry `0/0`,
  and inheriting that made every scheduled custom clip POP instead of blending
  from the idle.

**Additive:** operates on the DAT as-is (the clip must already be imported) — it does
**not** restore `.base` first, so your imported clip is kept. Re-running with the same
`--tag` replaces that routine.

If the model has no clean single-clip routine to clone (only complex ones with
sub-routines/sounds/vfx), it refuses rather than drag their extra commands along.

## `schedule create` — chain several clips

One routine can play **several clips in sequence** — sec2 is a timed command list, and
each `0x05` play command carries its own delay/window/loops/blend. That's how retail
does *transition, then loop*: the sit routine `ssit` plays the sit-down clip (`mi2`)
once, then the sitting idle (`mi3`); a tiger's `dead` is `ded0 → cor0` (die, then hold
the corpse pose).

```bash
uv run xi anim schedule create rom9/25/40                          # interactive wizard
uv run xi anim schedule create rom9/25/40 --tag sit0 --clip sitd --clip siti
```

The wizard asks for the routine tag, then clips in play order (Enter to finish),
whether the last clip loops, and offers an optional per-clip tuning pass. With `--clip`
flags it runs non-interactively on the same defaults:

- **delay auto-chains**: each clip starts when the previous clip's play window ends
  (`delay` = previous `dur`; sec2 delays are relative to the previous command, and the
  header's `totalDelay` is restamped to their sum — the retail invariant).
- **`dur`** = `2 × clip length` (one natural-speed play-through), per clip.
- **loops**: earlier clips play once and hold; the last loops forever
  (`--no-loop-last` = play once and hold instead).
- **blend**: crossfade in on the first clip and out on the last (`--blend`, default 15);
  the joins between chained clips hard-cut — their poses align by construction
  (retail `ssit` is `10/0 → 0/20`).

Like `add` it clones the donor routine, but replaces its single play command with one
per clip, shifting the sec1/sec2/sec3 header offsets and re-padding the section. The
same donor requirement applies. Re-using an existing `--tag` replaces that routine;
`schedule edit` can still tune blend afterwards (loops/dur are per-command, so `edit`
refuses them on chains — re-run `create` instead).

★ The editor's 3D preview resolves a routine to its **first** clip only — the full
chain plays in-game.

## `schedule list`

The model's schedulable routines and the clip each plays — exactly what the cutscene
author's Anim dropdown shows. A chained routine lists every clip; blend shows the
first clip's fade-in / the last's fade-out, loops the last clip's:

```
 routine  plays clip  blend in/out  loops
 ----------------------------------------
     ati0  at00        10/10f        x1
     dead  ded0→cor0   20/0f         ∞
     tlk0  tlk         10/10f        x1
```

## `schedule edit`

Patch an **existing** routine in place — no re-cloning, no clip needed:

```bash
uv run xi anim schedule edit rom9/25/40 tlk0 --blend 5          # snappier crossfade
uv run xi anim schedule edit rom9/25/40 tlk0 --trans-out 20     # keep in, slow release
uv run xi anim schedule edit rom9/25/40 tlk0 --no-loop --dur 60
uv run xi anim schedule edit rom9/25/40 tlk0 --loops 28         # play 28× then hold
```

Byte-surgical (u16 pokes at the `0x05` command's fixed offsets, section size
unchanged), so it works on **any** routine — your scheduled clips *and* the model's
retail ones (`ati0`, `dead`, …). Only the fields you pass change. A routine with
several play commands accepts blend edits (applied to all) but refuses
`--dur`/`--loop`/`--loops` (ambiguous per-command); a routine that only chains a
sub-routine has no play command to edit — edit the linked routine instead.

## `schedule copy`

Duplicate an existing routine under a new tag (byte-identical clone; only the section
name changes). Fork, then edit the copy without losing the original:

```bash
uv run xi anim schedule copy rom9/25/40 tlk0 tlk1
uv run xi anim schedule edit rom9/25/40 tlk1 --loops 1 --blend 8
```

Re-running with a destination tag that already exists replaces that routine.

## One-shot with import

`anim import --add-schedule` imports the clip **and** creates the routine in one step
(routine tag defaults to the clip name; `--schedule-tag` / `--loop` / `--no-loop` /
`--loops N` / `--blend N` to control it):

```bash
uv run xi anim import <dat> tlk yap.gltf --static-base --add-schedule --blend 12
uv run xi anim import <dat> --anim idl --add tlk --layer yap.gltf \
     --bones bone0007,bone0009 --add-schedule --blend 12 --loops 1
```

Then **hard-refresh the editor** (Ctrl+Shift+R) so it re-fetches the model's routine
list — `tlk0` will appear in the keyframe "Anim" dropdown.

## Format reference

The `0x07` EffectRoutine layout (sec1/sec2/sec3, the `0x05` command's clip DatId @+8,
`dur` @+6, `maxLoops` @+30): [../fx/effect_system.md](../fx/effect_system.md) §3.

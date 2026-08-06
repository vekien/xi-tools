# xi anim import

Import an edited (or newly authored) glTF animation back into an FFXI entity DAT.
It can **replace** an existing `0x2B` track, **create a new** one, or **layer** part
of one clip onto another.

```bash
# Replace an existing track from its exported clip
uv run xi anim import ROM/217/32 idl
uv run xi anim import ROM/218/91 idl --no-base      # after a mesh import
uv run xi anim import rom/37/13 poi                 # emote, all slots (see emotes.md)

# Create a NEW track from a full-body glTF (both forms equivalent)
uv run xi anim import ROM9/25/40 tlk yap.gltf
uv run xi anim import ROM9/25/40 --add tlk yap.gltf

# Import a partial clip cleanly (only the bones the glTF drives; rest held still)
uv run xi anim import ROM9/25/40 tlk yap.gltf --static-base

# LAYER a clip onto another: talk (bones 7 & 9) over the live idle, as a new track
uv run xi anim import ROM9/25/40 --anim idl --add tlk \
     --layer yap.gltf --frames 0-35 --bones bone0007,bone0009
```

`<dat>` may be a ROM-relative spec like `ROM/217/32`. See [export.md](export.md)
for producing the editable clip, [../mesh/import.md](../mesh/import.md) for the
geometry half, and [emotes.md](emotes.md) for emotes / skeleton-less DATs.

The three modes:

| Mode | When | How |
|---|---|---|
| **Replace** | edit an existing track | `import <dat> <existing> [gltf]` |
| **Create** | add a brand-new track | `import <dat> <newname> <gltf>` or `--add <newname> <gltf>` |
| **Layer** | overlay some bones/frames of one clip onto another | `--anim <base> --add <new> --layer <gltf> [--bones …] [--frames A-B]` |

## Arguments

- `<dat>` — the DAT to modify (a `<dat>.base` backup / output-dir mirror is kept).
- `<anim>` — target track name (a plain **positional**, or `--anim NAME`). A name with
  **no trailing digit** writes **every slot** of that emote (`poi` → `poi0` + `poi1`,
  …); a numbered name (`poi1`) writes just that slot. A single track with no digit
  still defaults to slot 0 (`idl` → `idl0`). If the name **doesn't exist yet**, it is
  **created** (a new `0x2B` section appended after the last one).
- `[gltf]` — optional. A full/relative path is used directly; a **bare name**
  (`yap.gltf`) is looked up under the DAT's export folder
  `exports/anim/<rom>/<stem>/…`. If omitted entirely, the exported clip(s) for
  `<anim>` are found automatically under `exports/anim/<rom>/<stem>_<anim>/` (older
  exports under `exports/entity/anim` are still accepted as a fallback).
- `--add NAME` — the target track name, and a signal to **create** it. Handy because
  a lone positional after it is then taken as the glTF: `--add tlk yap.gltf`. With
  `--layer` it names the layered result instead. A digit-less new name is
  slot-normalised (`tlk` → `tlk0`, still found by `--anim tlk`).

## What it does

1. Restores the DAT from pristine / `<dat>.base` (unless `--no-base`).
2. Reads the glTF animation (bone channels on `bone0000…` nodes) and, for
   skeleton-less DATs, the bind pose from a base race skeleton (`--race`).
3. **Resamples** the clip uniformly at **30 fps** over its real time span, so the
   DAT frame count tracks the authored duration regardless of how the DCC tool
   sampled (longer clips are fully preserved — see below).
4. Converts the posed local bone transforms into the DAT's per-joint delta channels,
   using each target track as its structural template.
5. Grows the emote's `0x07` routine duration to fit the clip (unless
   `--keep-routine-duration`).
6. Writes/replaces the target `0x2B` section(s) and rebuilds the DAT in one pass.

## Options

- `--no-base` — **write onto the current DAT without restoring** first. Use after
  `entity mesh import` so the imported mesh is kept (mesh = `0x2A`, animation =
  `0x2B`, skeleton untouched — they compose). Also use it to layer a second separate
  import; without it the import reverts to the vanilla DAT and earlier edits are lost.
- `--template-anim NAME` — existing track to use as the structural template when the
  target doesn't already exist (default `idl`). Lets you author a brand-new slot.
- `--race NAME` / `--skeleton-dat PATH` — base skeleton for **animation-only** DATs
  (no skeleton of their own). `--race` is **auto-detected from the DAT id**
  (`rom/37/13` → HumeFemale); a DAT with its **own** skeleton (monster/NPC/object)
  needs neither, and an undetectable race is **not** forced to HumeFemale — an
  animation-only DAT errors and asks for one of these. See [emotes.md](emotes.md).
- `--keep-routine-duration` — don't grow the emote's `0x07` routine window. By default
  a longer clip lengthens the routine so the whole thing plays in-game.
- `--fps N` — keyframe sampling rate. Default is the clip's **native** rate (emotes
  15 fps). Higher packs more keyframes for **smoother in-game interpolation** —
  `--fps 30` (a keyframe per game tick) or `--fps 60` (denser-than-tick, for 60 fps
  clients). Playback **duration is preserved** (kdur is set to `fps/30`). Stock clips
  are all `<30` fps, so non-native fps is non-stock — test in-game.
- `--static-base` — hold the **template clip's translation & scale static** (frame 0)
  so **only the glTF's rotation animates**. Use when your glTF drives just a few bones
  and you don't want the base clip's idle bob/motion leaking onto the others. See
  *[Only some bones move? `--static-base`](#only-some-bones-move---static-base)*.
- **Layer mode** (all imply `--layer`):
  - `--layer GLTF` — overlay this clip's rotation onto the base (`--anim` / positional)
    and write the result as `--add NAME`. Bare names resolve under the export folder.
  - `--bones LIST` — comma/space list to overlay, `bone0007,bone0009` or `7,9`
    (default: every bone the layer animates).
  - `--frames A-B` — frame window to overlay, e.g. `0-35` (default: the whole base
    clip; the base's frame count wins). Indices are 30 fps frames, as in the glTF.

## Frame count & longer clips

The DAT plays a clip for `(numFrames - 1) / keyFrameDuration` frames. The importer
resamples over the glTF's actual time span — it does **not** just copy however many
keyframes the exporter wrote (DCC tools keyframe-reduce, which used to truncate long
edits). Make your clip's **timeline length** in the DCC tool reflect the full
intended duration.

It samples at the clip's **native keyframe rate** and keeps the template's
`keyFrameDuration` (emotes = `0.5` → 15 fps; some idles `0.4` → 12 fps), so the
rebuilt clip is structurally identical to a stock one. A 4 s emote edit lands as
~63 frames at `kdur 0.5`, not 126 at `kdur 1.0` — the real duration is the same, but
the layout matches what the retail client expects.

For emotes there's a second gate: the `0x07` routine `dur`. The importer grows it
automatically. Full explanation in [emotes.md](emotes.md).

## Edit rotations, not translations

The importer takes **rotations** from your glTF (your pose) but **preserves each
bone's translation and scale from the original clip** — those are rig structure
(weapon/holster attachment offsets, pelvis height, finger spread), not animation.
FFXI animation is rotation-based: a bone follows its parent's rotation, so keeping
the original local offset keeps the weapon attached and the body at standing height
while your new pose drives everything. This also makes the round-trip immune to DCC
tools (Blender) that mangle bone translations. Practical upshot: **animate with bone
rotations**; translating bones in your DCC tool has no effect on import.

## Create a new track

Give a name that doesn't exist yet and the importer **appends a new `0x2B` section**
(the client just walks sections, so a fresh track can be added):

```bash
uv run xi anim import ROM9/25/40 tlk yap.gltf          # positional new name + glTF
uv run xi anim import ROM9/25/40 --add tlk yap.gltf    # same, explicit
uv run xi anim export ROM9/25/40 --anim tlk            # confirm it round-trips
```

- The **structural template** for the new track (its translation/scale rig + frame
  layout) is `--template-anim` (default `idl`); rotation comes from your glTF.
- A **full import** takes *every* bone's rotation from the glTF. Bones the glTF does
  **not** animate fall to **bind/rest pose** — so this wants a **full-body** clip. For
  a clip that drives only a few bones (a talk that moves the jaw), either add
  [`--static-base`](#only-some-bones-move---static-base) (hold the body still) or use
  [layer mode](#layered--partial-bone-overlay) (keep the body's live idle).
- A digit-less new name is slot-normalised → `tlk0` (still matched by `--anim tlk`).

> **Want it in a cutscene?** A new clip won't appear in the cutscene author's "Anim"
> dropdown until it's wrapped in a `0x07` scheduler routine — a cutscene can only
> `SetAction` a routine, never a raw clip. Add `--add-schedule` here (creates the
> routine in the same step), or run [`anim schedule add`](schedule.md) after. 

## Only some bones move? `--static-base`

By default translation & scale come from the base/template clip **per frame** — which
means the base clip's own motion (e.g. an idle **bob**, stored as translation on the
pelvis/hip bones) rides along on bones your glTF doesn't touch. If your glTF only
animates a couple of bones, you'll see a subtle **bounce** in-game that isn't in your
DCC preview.

`--static-base` holds the template's translation & scale **at frame 0** (constant), so
**only the glTF's rotation animates**. Rig offsets are kept; only the per-frame base
motion is dropped:

```bash
uv run xi anim import ROM9/25/40 tlk yap.gltf --static-base
```

It's **opt-in** — a normal edit/round-trip (re-importing `idl` → `idl0`) still wants
the base clip's own motion preserved, so the default doesn't touch it.

## Layered / partial-bone overlay

Build a **new** track by overlaying part of one clip onto another: take the whole base
clip and overlay the layer glTF's **rotation** onto just `--bones` over just
`--frames`; everything else (other bones, all translations/scales, out-of-window
frames) is copied from the base. Perfect for a "talk" built from "idle" with a yap
clip driving only the jaw:

```bash
uv run xi anim import ROM9/25/40 --anim idl --add tlk \
     --layer yap.gltf --frames 0-35 --bones bone0007,bone0009
```

- **`--anim`** is the base (copied), **`--add`** is the new track, **`--layer`** the
  overlay clip. Omit `--bones` to overlay every bone the layer animates; omit
  `--frames` to span the whole base clip.
- Output is baked to **30 fps** so the frame indices match the exported glTF (a DAT
  clip is stored sparsely — an idle can be 17 keyframes at ~9 fps but 55 frames at 30).
- The layer path **deliberately keeps the base's live motion** for un-selected bones
  (that's the point — talk *over* a running idle). Want the body dead-still instead?
  Do a full import with [`--static-base`](#only-some-bones-move---static-base).
- Prints a concise grouped summary (DAT / overlay / encoded / updated / verify); add
  `--verbose` for per-bone rotations, timing, and byte sizes.

## Emotes & multi-slot import

A digit-less name writes every slot of the emote in one session. Source resolution
when no path is given (in priority order):

1. **Full-body file** `<stem>_poi.gltf` — author the whole skeleton in one clip and
   the import **splits it across slots by joint** (poi0 takes 1–17, poi1 takes
   26–70). This is the easiest workflow — animate everything in one Blender file.
2. **Per-slot files** `13_poi0.gltf` → `poi0`, `13_poi1.gltf` → `poi1` — each slot
   from its own file.

You can also pass a path explicitly: `import rom/37/13 poi myfullbody.gltf` splits the
one file across all slots, exactly like (1).

Why both slots matter, and the bone-routing caveats, are in [emotes.md](emotes.md).

## The Mesh → Anim composition

Both importers share the pristine/`.base` baseline and normally restore it first, so
order matters:

```bash
uv run xi mesh import ROM/x/y            # restores baseline, writes the mesh
uv run xi anim import ROM/x/y idl --no-base   # writes the animation, keeps the mesh
```

In-game they compose: the mesh is skinned to the bones, the `0x2B` track drives the
bones. **Weight your mesh to the bones the animation actually moves**, or it will
look static.

## Authoring a new animation

To add a brand-new named track, see [Create a new track](#create-a-new-track) above.
If instead you're editing an existing track that was **empty** (0 keyframes — some
idles ship with none), there's nothing to edit: keyframe the bones yourself in the DCC
and export, then import. The importer errors clearly ("no bone animation keyframes")
if the glTF has no bone channels.

## Format reference

Per-joint channel encoding, offset semantics, NLERP, and root-bone handling:
[format.md](format.md).

# Editing emotes & animation-only DATs

Conceptual guide for the cases that aren't a single self-contained model: **emote
DATs** (e.g. `rom/37/13` = `/point`, `/bow`, `/laugh`, …) and any DAT that holds
animation tracks but **no skeleton of its own**. Command details:
[export.md](export.md), [import.md](import.md). Binary layout: [format.md](format.md).

## 1. Animation-only DATs need a base skeleton

A normal entity DAT carries its skeleton (`0x29`), mesh (`0x2A`) and animations
(`0x2B`) together. Emote/gesture DATs carry **only `0x2B` tracks** — the clip is
keyed by joint index and meant to play on a shared **race skeleton**.

So both export and import take a base skeleton when the DAT has none:

- `--race NAME` — **auto-detected from the DAT id** and rarely needed: motion files
  are race-specific, so `rom/37/13` IS HumeFemale's emote file and `rom/61/8` is
  Galka's (detection uses the model-viewer file ranges). Pass it only to override.
  One of `HumeMale`, `HumeFemale`, `ElvaanMale`, `ElvaanFemale`, `TaruMale`,
  `TaruFemale`, `Mithra`, `Galka`.
- `--skeleton-dat PATH` — an explicit skeleton DAT (overrides `--race`).

> **Use the SAME base skeleton on import that you used on export**, or the
> bind-pose deltas drift and the motion is wrong.

The race body skeleton has no mesh, so an emote export is **skeleton-only** by
default (the animated bone rig). Add `--mesh` to attach a body for visual
reference — see [export.md](export.md).

## 2. An emote is THREE clips played at once — across TWO files

This is the part that surprises people. A single emote is **split into three parts
by body region** (the Upper/Lower/Waist split, like Unreal's), played
simultaneously and overlaid into one pose. The digit is the part — and the **waist
part lives in a different DAT**:

| part  | clip   | file (HumeFemale) | joints |
| ----- | ------ | ----------------- | ------ |
| lower | `poi0` | ROM/37/13         | 0–17   |
| upper | `poi1` | ROM/37/13         | 26–96  |
| waist | `poi2` | **ROM/37/19**     | 4–25   |

They animate **disjoint** joint sets; the client resolves the wildcard `poi?` to
every loaded part and overlays them. The waist file is `MotionE + 6` (so
`ROM/37/13 → ROM/37/19`); a robe/skirt body uses `MotionE + 12` instead. One `0x07`
routine in 37/13 drives all three. The race body skeleton has no emote clips.

**Consequence:** editing only 37/13 (lower + upper) leaves the **waist (poi2) stock**
— so a custom full-body pose breaks at the mid-body (it sinks/clips). All three parts
must come from the same clip. The importer does this for you: a digit-less import of
a full-body glTF writes poi0/poi1 to 37/13 **and** poi2 to 37/19, split by joint.

> File map (from the FFXI model viewer's `LoadMotion`): per race, base skeleton =
> `BaseMotionFileNo` (HumeFemale `32058`), emotes = `MotionEFileNo` (HumeFemale
> `37013`) loaded as `+0..5` (the 0/1 parts of each emote) **and** `+6..11` (the 2 /
> waist parts).

## 3. Playback length is gated by an `0x07` routine, not the frame count

The emote DAT also holds **`0x07` EffectRoutines** (`em00`, `em01`, …). The one
that plays a clip carries a `dur` (a `u16`) that sets the in-game playback window.
The client scales the clip into `dur / (2·rate)` frames, so a clip plays at natural
speed only when:

```
dur = 2 × clip_length_in_gameframes        # clip_length = (numFrames - 1) / keyFrameDuration
```

A longer edited clip left with the original `dur` is **cut off mid-play**. The
importer fixes this automatically: it **grows** the matching routine's `dur` to fit
the longest slot (grow-only — one routine's wildcard serves both slots, so the
window must fit the longer one). Opt out with `--keep-routine-duration`.

## 4. The full round-trip (full-body, recommended)

```bash
# 1. Export the WHOLE emote merged onto one skeleton (all 3 parts incl. waist)
uv run xi anim export rom/37/13 --anim poi
#    → exports/anim/rom/37/13/13_poi/13_poi.gltf   (animates all 97 joints)

# 2. In Blender, animate the WHOLE body and re-save 13_poi.gltf (keep bone names)

# 3. Import — auto-finds 13_poi.gltf and splits it across all parts
uv run xi anim import rom/37/13 poi
#    → poi0/poi1 to 37/13, poi2 (waist) to 37/19, routine grown
```

A digit-less export merges every part (lower + upper + waist) into one clip, and a
digit-less import splits it back by joint and writes each part to its file —
including the waist in the +6 sibling. So you edit the entire body in one place and
nothing is dropped. See *Import source modes* below for the per-slot alternative
(which does **not** cover the waist).

Key points:

- **Digit-less name** (`poi`) merges all parts into one full-body clip on export and
  writes **all slots** (across both files, incl. the waist) on import. A **numbered
  name** (`poi1`) handles just that one part.
- Import writes all slots in **one session**, so it never re-seeds `.base` between
  slots (which would wipe the slot you just wrote).
- The routine duration is grown automatically to fit your longer clip.

### Import source modes (easiest first)

- **One full-body file (recommended):** animate the **whole skeleton** in a single
  Blender clip — legs *and* arms — and save it as `<stem>_poi.gltf` (e.g.
  `13_poi/13_poi.gltf`). `import rom/37/13 poi` auto-finds it and **splits it across
  slots by joint** (poi0 takes 1–17, poi1 takes 26–70). One coherent animation →
  legs and arms stay consistent, no broken lower body. You can also pass it
  explicitly: `import rom/37/13 poi myfullbody.gltf`.
- **Per-slot files:** if `13_poi.gltf` isn't present, auto-find falls back to
  `13_poi0.gltf` → `poi0` and `13_poi1.gltf` → `poi1`, each carrying its own
  body-half motion. ⚠ Keep the two halves consistent — a `poi0` whose legs aren't
  posed for standing will break the lower body in-game.

To get a full-skeleton clip to edit, export the 71-joint variant
(`anim export rom/37/13 --anim poi1`) — it includes every bone — then animate the
whole body and save as `13_poi.gltf`.

### Caveats

- The bones a slot captures are fixed by what the **original** clip animated
  (poi0: 1–17, poi1: 26–70). A full-body edit that moves a joint **neither** slot
  originally used (e.g. 18–25, 71+) is **dropped** — no slot claims it.
- A glTF that only animates the upper body, imported to both slots, writes `poi0`
  at full length but **static** (held pose) — no reset, but no leg motion. Animate
  the lower-body bones too if you want them to move.

# xi gear pose

Export a **fully dressed character** — every gear slot plus the weapons in hand — as one
rigged GLB (and optionally FBX), with the geometry the game hides actually removed.

This is what `xi gear export` is not: that command exports *one* gear DAT. A character is
eight or nine of them merged onto a shared race skeleton, and merging them naively gives
you a model that is wrong in three specific ways this command fixes.

---

## Why a plain merge is wrong

### 1. Hidden pieces

FFXI gear is authored to overlap. The race body keeps its bare wrists, its shins and a
full head of hair; the client *drops* whichever of those the worn set covers. Two bytes
decide it:

| Byte | Where | Meaning |
|---|---|---|
| `occludeType` | 0x2A mesh header, byte 3 (flags4) | what this mesh **hides on other pieces** |
| `displayType` | render-properties block (0x8010), byte 13 | what a piece **is** |

| `displayType` | Piece | Hidden when some equipped mesh declares |
|---|---|---|
| 1 | hair | `0x02` `0x03` `0x04` `0x05` `0x06` |
| 2, 3 | hair | `0x04` `0x05` `0x06` |
| 4 | face | `0x05` |
| 5 | wrist | `0x12` |
| 6 | pants | `0x32` |
| 7 | shins | `0x22` |

(`0x11` / `0x21` / `0x31` are body / legs / feet self-markers and hide nothing.)

So a full helm declares `0x05` and the whole head section of the face DAT goes; a sleeve
declares `0x12` and the wrist skin goes. Merge without this and that geometry is sealed
*inside* the armour — invisible in a static viewport, but it inflates the mesh, drags
along textures nothing samples, and pokes straight through the moment anything is posed
or the outer mesh is edited.

`xi gear pose` computes the union of `occludeType` across the whole worn set first, then
keeps or drops each piece against it — the same test the client runs
(xim `ActorModel.isOccluded`). A robed, full-helmed Hume Male loses ~25% of its triangles
and one whole texture this way.

### 2. The weapon on the floor

A weapon mesh is skinned to its own grip joint, and in the bind pose that joint sits at
the skeleton root — merge the DAT as-is and the sword lies on the ground. The client
re-parents the grip onto the hand when the weapon is drawn
(xim `jointParentOverrides`), and the grip adopts the hand's transform wholesale.

The grip is named by the weapon's `info.standardJointIndex`, which indexes the skeleton's
128-entry joint-reference table; reference **127** is the right hand, **126** the left.
`--main` and `--sub` do that re-parenting.

Ranged weapons report no `standardJointIndex` at all — their mesh binds straight to a
back-mount bone that no clip animates, and the client simply **scales them to zero**
until they are drawn. So a stowed ranged weapon is left out of the export entirely,
exactly as the game leaves it out; `--draw-ranged` re-parents its mount onto the bow
hand and brings it in. (Merging a stowed bow at its bind pose drops it on the floor at
the character's feet — the back mount is not where the bind pose puts it.)

### 3. Half a pose

FFXI splits a clip **by body region** across numbered siblings that animate disjoint
joint sets and play together — and for a player character those siblings live in
*different DATs*: `idl0` (lower body) in the race body DAT, `idl1` (upper body) and
`idl2` (waist) in its companion motion packs. Pose from `idl0` alone and every
upper-body joint stays at bind: arms in the wrong place, and a stowed shield down at
the character's feet instead of on the back, because the joint it hangs from is only
ever driven by `idl1`.

Pass the motion packs with `--anim-dat` and every layer of the clip is merged into one
full-body pose. The summary names the layers it found.

---

## Usage

```
uv run xi gear pose <DAT…> [--main DAT] [--sub DAT] [--ranged DAT] [options]
uv run xi gear pose --race RACE --slots slot=model_id,…            [options]
uv run xi gear pose --look <20-byte hex | npc id>                   [options]
```

Three ways to say who the character is — pick whichever you already have:

| Form | Use when |
|---|---|
| bare DAT paths | you already know the DATs (this is how the model viewer calls it — it passes exactly what is on screen) |
| `--race` + `--slots` | you are working from gear model ids |
| `--look` | you have an NPC's 20-byte `look` blob, or an npc id and the server DB is up |

| Option | Description |
|---|---|
| `--main DAT` / `--sub DAT` | weapon attached to the right / left hand |
| `--ranged DAT` | ranged weapon — not drawn while stowed, as the game leaves it |
| `--draw-ranged` | draw the ranged weapon into the bow hand |
| `--anim-dat DAT` | a DAT to take animation from but no geometry — the companion motion packs that carry a clip's upper-body and waist layers. Repeatable |
| `--skeleton DAT` | skeleton to rig onto (default: the first source that has one) |
| `--anim TAG` | pose to this clip; `--anim ''` for the neutral bind pose (default `idl`) |
| `--frame N` | frame of `--anim` to freeze, on the **30 fps playback timeline** — the same frame number a viewer's own counter shows (default 0) |
| `--all-frames` | embed the whole clip as an animation instead of freezing one frame, so the export plays in a DCC. With `--fbx` the motion is baked into the FBX too |
| `--pose-file F` | bake joint world transforms a viewer has already evaluated, instead of resolving `--anim`. See **Baked poses** below |
| `--fbx` | also write a texture-embedded `.fbx` via Blender |
| `--keep-hidden` | **skip** occlusion culling — merge every piece, covered skin and all |
| `-o DIR` / `--name` | output directory (default `exports/gear/pose/<name>/`) and file stem |
| `--alpha-scale` `--mesh-merge-dp` `--weld/--no-weld` `--split-tex` | same meanings as `xi mesh export` |
| `--json` | emit the summary as JSON instead of text |

Gear DATs carry **no skeleton of their own** — they are all rigged against the shared race
body skeleton. Pass the race body DAT among the sources, or name it with `--skeleton`;
`--race` and `--look` resolve it for you.

---

## Examples

```bash
# from the DATs a viewer has open — race body first, then each slot, plus the race's
# motion packs so the pose covers the upper body too
uv run xi gear pose ROM/27/82 ROM/27/88 ROM/27/111 ROM/28/19 ROM/28/64 ROM/28/96 ROM/29/0 \
    --main ROM/29/29 --anim-dat ROM/27/83 --anim-dat ROM/27/85 --fbx

# from a race + gear model ids
uv run xi gear pose --race HumeMale --slots face=1,head=22,body=3,hands=12,legs=3,feet=1

# from an NPC's look blob
uv run xi gear pose --look 0x0100020114101920193019401950056000700000

# a specific frame, or the whole clip as a playable animation
uv run xi gear pose --race HumeMale --slots body=12 --anim wlk --frame 7
uv run xi gear pose --race HumeMale --slots body=12 --anim wlk --all-frames --fbx

# see what the culling removed
uv run xi gear pose --race HumeMale --slots face=1,head=22,body=3 --keep-hidden
```

Output names every part it kept, every weapon it attached, and every piece it hid:

```
Assembled pose → exports/gear/pose/HumeMale/HumeMale.glb
  skeleton ROM/27/82.DAT · 94 joints
  posed to idl frame 0/56  (3 layer(s): idl0, idl1, idl2)
    face    hh_h     12 piece(s)  ROM/27/88.DAT
    body    hh_b      2 piece(s)  ROM/28/19.DAT
    …
    ranged  wep2    stowed — not drawn (the client scales it to zero; pass --draw-ranged to include it)
    main    attached joint 12 → hand joint 68
  occlusion: 18 piece(s) hidden by the worn set (occludeTypes 0x12, 0x22, 0x32, 0x4, 0x5)
    hid  14 piece(s) of face/hh_h (displayTypes [1, 3, 4])
    hid   2 piece(s) of hands/hh_g (displayTypes [5])
    hid   2 piece(s) of feet/hh_f (displayTypes [7])
```

---

## Frames

`--frame` counts **played** frames at 30 fps, not the clip's stored keyframes. The two are
not the same number: a 15-keyframe idle with a keyframe duration of ~0.25 plays for 56
frames, so keyframe 5 and played frame 5 are different poses. Counting played frames is
what makes `--frame N` the frame a viewer shows at N — and the same pose frame N of an
`--all-frames` export lands on.

(`xi mesh export --frame` is the older, keyframe-indexed one; it is left as it was.)

`--all-frames` swaps the single frozen frame for the whole clip embedded as a glTF
animation. The mesh then sits at bind pose and the clip drives it, which is what a DCC
wants to scrub. Tracks on a re-parented weapon grip are dropped, so a drawn weapon stays
welded to the hand for the whole clip instead of animating back off it.

---

## Baked poses

`--anim`/`--frame` can only reach a pose that a clip name and a frame number describe.
Plenty of what a viewer shows is not that. A weapon-skill schedule lays several clips on
a timeline, blends each back out to an underlaid base idle, merges the waist pack and
re-parents the weapon grips — the result is a composition, and the viewer's own "current
animation" is empty for the whole of it.

`--pose-file` takes the answer instead of the question: the world transform of every
joint, as the viewer already evaluated it.

```json
{
  "name": "main",
  "fps": 30,
  "space": "world",
  "frames": [[qx, qy, qz, qw, tx, ty, tz,  … 7 more per joint …]]
}
```

One flat array per frame, seven floats per joint in skeleton order — rotation as a
quaternion `(x, y, z, w)`, then translation. Give it one frame to freeze that pose, or
many with `--all-frames` to embed the lot; `--frame N` selects within a multi-frame file.
Scale is ignored, as everywhere else in the exporter.

The locals that reproduce those worlds on the DAT's own hierarchy are solved for, so the
node tree the file carries agrees with the baked vertices. Anything the viewer did to the
pose — including a weapon grip it re-parented onto a hand — comes through in the solve,
which is why `--pose-file` skips the weapon re-parenting rather than redoing it.

This is how the model viewer's Full Pose export works: it writes `<name>.pose.json` beside
the model and passes it, so what lands in the DCC is the frame that was on screen. The
file stays behind as a record of the exact pose.

---

## Limits

- Schedule-driven stances (the battle idle, a weapon skill, the waist packs) are the
  model viewer's composition and are not reimplemented here. `--anim` reaches raw clips
  only; for anything else, hand over the evaluated joints with `--pose-file`.
- Textures merge by name across DATs, last one wins. Distinct gear rarely collides
  (FFXI texture names are per-model), but a clash would silently pick one.
- The `info` render scale (`scale/100`, which the client applies to a character actor)
  is **not** baked in, matching `xi mesh export` — the GLB is at DAT scale so it can be
  re-imported.

## See also

- [gear/export.md](export.md) — one gear DAT at a time
- [mesh/export.md](../mesh/export.md) — the shared GLB/FBX pipeline
- `xi gear character` — assembles an NPC `look` (including fixed-model monsters) without
  occlusion culling, for the level editor's preview

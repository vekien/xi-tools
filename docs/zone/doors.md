# Zone doors

Zone doors (shop doors, gates, workshop doors, etc.) open and close by playing a small
**`0x07` scheduler routine** that rotates or slides the door's model parts. Like
[elevators](elevators.md), it is **client-side** — a door's whole definition (trigger
volume, model, open/close animation) lives in the zone DAT.

Ground truth: PS2 decompile `XiDoorActor` — `main/actor/xidooractor.cpp` (`SetDoor`,
`OpenDoor`/`CloseDoor`, `InitOpenDoor`/`InitCloseDoor`, `SetDoorAngle`/`SetDoorSlide`,
`MakeDoorMatrix`, `OnMove`). Verified against Metalworks (`ROM/1/37`, zone 237).

## The pieces

A door ties three things together, all keyed by a **`'_'`-kind RID id** (e.g. `_6l0`):

1. **Trigger volume** — a `0x36` *ZoneInteraction* entry whose `sourceId[0]` is `'_'`
   (see [subareas.md §1](subareas.md#1-discovery--the-0x36-zoneinteraction-section)).
   `rect->flag` is the collision toggle: **1 = closed (solid), 0 = open (passable)**.
2. **Model** — a `DoorData` (`parts_list` of `parts_num` movable parts, each a matrix at
   `+0x20`), fetched with `KO_MapManager::GetDoor(rectId)`. The visible geometry is an
   ordinary `0x1C` placement whose **file-id link at record offset `0x34`** holds the door's
   RID id (same binding as a lift car — [elevators.md](elevators.md#binding-the-car-to-the-record)).
   `l`/`r` name suffixes (e.g. `kouboudoorl`/`kouboudoorr`) are the two leaves.
3. **Animation** — two `0x07` routines under **`wt_b/door/<rid>/`**: **`open`** and **`clos`**.

## Open / close

`OpenDoor` / `CloseDoor` look up the routine under the door's directory and run it via
`YmScheduler::Execute`; `SchTotalTime` is set to the routine's `total_frame` and counted
down each tick in `OnMove` (a completion callback fires at 0). `isOpen` tracks state and
`rect->flag` flips the collision. `InitOpenDoor`/`InitCloseDoor` are the load-time variants
(a door authored already-open uses the `into`/`intc` routines).

The routine drives each part by **rotation** (`SetDoorAngle` → `MakeDoorMatrix`) and/or
**slide** (`SetDoorSlide`): `MakeDoorMatrix` composes `rot · trans` and multiplies it into
the part's base matrix, so the part swings about (or slides from) its authored origin — for a
hinged door that origin is the hinge, so a rotation swings the leaf correctly.

### What the routine contains (Metalworks `_6l0`)

```
wt_b/door/_6l0/open :  0x0b Sound(9021)
                       0x0d ModelRotation  dur 70   angle −1.396 rad (≈ −80°)   ← leaf A
                       0x0d ModelRotation  dur 70   angle +1.396 rad (≈ +80°)   ← leaf B
wt_b/door/_6l0/clos :  0x0b Sound(9022)
                       0x0d ModelRotation  dur 70   angle 0   (both leaves back to shut)
```

So this double door swings its two leaves ±80° over **70 frames** with an open/close sound
(`0x0b`, sound ids 9021/9022). Op `0x0d` is `ModelRotation`, `0x0c` is `ModelTranslation`
(sliding doors); both are the ordinary `0x07` effect-routine ops (see the op table in the
model-viewer's `dat/inspect.js` / xim `EffectRoutineParser`). The angle sign per op selects
which leaf and which way it swings.

## Animating doors in a viewer

Enough to drive an **"open / close all doors"** toggle for any zone:

1. Collect every `'_'` RID id, and the `0x1C` placements whose `0x34` link points at one
   (the door leaves). `l`/`r` share a rid.
2. For each rid, find `wt_b/door/<rid>/open` (or `clos`) and read its `0x0d`/`0x0c` ops:
   per op an **angle (rad) or offset** and a **`dur` in frames**.
3. Animate each linked leaf placement from its shut pose to the open pose over `dur`
   frames — rotate about the placement origin (the hinge) for `0x0d`, translate for `0x0c` —
   re-baking it live like a lift car (the static pass skips animated placements).
4. Optionally play the `0x0b` sound and flip a collision flag.

> **Open question for a first cut:** the `0x0d` operand layout (which axis the angle is on,
> and how the two ops map to the `l`/`r` leaves) should be read back from a couple of doors
> before trusting it globally — swing doors are about Y (vertical hinge), but confirm against
> a sliding door (`0x0c`) too. Positions/durations above are exact from the DAT.

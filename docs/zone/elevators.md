# Zone elevators (lifts)

The moving platforms in Metalworks, the Bastok Mines / Metalworks freight lifts, the
Ru'Lude Gardens lift, etc. are **client-side**: they run on the client's own clock with
**no server packet and no player trigger**. Everything needed is baked into the zone DAT —
one `0x36` *ZoneInteraction* record per car plus its moving model — so a car goes up,
pauses, comes down, pauses, forever, and every client sees it at the same height.

> **Not an effect.** A zone's `0x05` VFX generators / `0x07` routines run the smoke and
> spinning gears, but the lift *platform* itself is not particle-driven. It is a
> `'@'`-kind RID volume moving a bound model. Contrast the `'m'`/`'z'`/`'_'` RID kinds in
> [subareas.md](subareas.md), which are **player-triggered** (enter the volume).

Ground truth: PS2 decompile `XiLiftActor` — `main/actor/xiliftactor.cpp` (`SetLift`,
`SetLiftHeight`, `LiftMove`, `SetLiftSchedule`). RID layout: [../reference/ps2_decomp_crosscheck.md](../reference/ps2_decomp_crosscheck.md) §8.
Verified against Metalworks (`ROM/1/37`, zone 237).

## The `'@'` RID record

A lift is a `0x36` *ZoneInteraction* entry (the plaintext `"RID"` section — same 64-byte
OBB entry as every other kind, see [subareas.md §1](subareas.md#1-discovery--the-0x36-zoneinteraction-section))
whose **`sourceId[0]` is `'@'`**. Two fields in the tail carry the motion; the OBB itself
(`pos`, `size`) is only the **detection box** and is deliberately larger than the travel —
do **not** use it as the travel range.

| Off | Field (`KO_RectData`) | Meaning for a `'@'` lift |
|-----|-----------------------|--------------------------|
| 0x04 | `y` (f32) | the record's Y — the **base** both floor heights are added to |
| 0x24 | `id` (4-byte) | `'@' + …`; the id used to bind the car (below) |
| 0x34 | `lift_height` (s16) | **floor 0** offset |
| 0x36 | *(second s16)* | **floor 1** offset |
| 0x38 | `lift_current_height` (f32) | runtime only — 0 on disk |

### Floor heights — the exact formula

The two s16 are **the two floor heights the car stops at**, each `param / 256 + record.Y`.
Straight from `XiLiftActor::SetLift`:

```c
this->HeightTbl   = (short)rect->lift_height /256.0 + rect->y;   // floor 0  (+0x34)
this->field_0x19c = (short)rect->field_0x36  /256.0 + rect->y;   // floor 1  (+0x36)
```

`HeightTbl[0]` / `HeightTbl[1]` are then the two stops; the car moves between them.
(FFXI world Y is **down**, so the more-negative floor is the higher one.)

## Pairing — why both cars line up

A paired shaft has **two `'@'` records with identical params**, so both cars compute the
**same two floors**. Each car is parked (its `0x1C` placement Y) on one of those floors; a
pair is authored parked on **opposite** floors, so it runs exactly **antiphase** — one car
at the top while the other is at the bottom, crossing in the middle. Because the range is
identical, they stay aligned in *both* phases (giving them different ranges makes them match
in one phase and drift in the other).

## Binding the car to the record

The moving geometry is a `0x1C` placement, not part of the `'@'` record. Two links tie them:

- The placement's **file-id link at record offset `0x34`** (the same field that ties a door
  to its `'_'` volume — see [subareas.md §2](subareas.md#2-the-placeholder-link-0x1c-object-0x50);
  xi decodes it as `file_id_link` in [`xi_objects.py`](../../src/xi/zone/xi_objects.py))
  holds the `'@'` record's 4-char id. In the client `SetLift` pairs them via
  `FindRectData(id)` + `KO_MapManager::GetDoor(id)` — the lift car is a `DoorData`
  (`parts_list` of transforms), the **same structure doors use**.
- A car with a non-zero link is **generator/rect-bound**, so the normal draw pass should
  skip its static copy and draw it only at its live height.

## Movement timing

The record has **no timing** — both tail s16 are floor heights. The move duration lives in
**`0x07` scheduler routines** under the zone's `wt_b/even/<id>/` directory, named
**`mv<from><to>`** (`mv00`, `mv01`, `mv10`, `mv11` — from-floor → to-floor). `LiftMove(floor)`
/ `SetLiftSchedule` build that id from the current and target floor index and run it with
`YmScheduler::Execute`. Each `mv` routine is a `0x02 SpawnGenerator` plus op **`0x1D`
`LiftMoveDriveTask`** (see [../reference/ps2_decomp_crosscheck.md](../reference/ps2_decomp_crosscheck.md) §7) with
`dur = 480` frames — i.e. **8 s at 60 fps** to travel between floors, verified in `ROM/1/37`.
The wait at each floor is scheduled separately by the actor. So the *positions* are exact from
the record and the *move time* is 480 frames; a viewer can read `mv01`/`mv10` for the exact
cadence or approximate it and still land on the correct floors.

## Worked example — Metalworks (`ROM/1/37`, zone 237)

The `e237` `0x36` section holds two `'@'` records for the paired freight lift, `record.Y = −13.1`:

| id | shaft (Z) | params (s16 @ 0x34 / 0x36) | floor 0 | floor 1 | parked car | parked on |
|----|-----------|---------------------------|---------|---------|-----------|-----------|
| `@6l1` | +12 | 3856 / 798 | `3856/256 − 13.1 = +1.96` | `798/256 − 13.1 = −9.98` | `liftall` @ +1.97 | floor 0 (bottom) |
| `@6l0` | −12 | 3856 / 798 | +1.96 | −9.98 | `liftallb` @ −9.98 | floor 1 (top) |

Both cars run **+1.96 ↔ −9.98**; `liftall` rises from the bottom while `liftallb` descends
from the top. The `0x1C` placements `liftall` / `liftallb` link to `@6l1` / `@6l0` via
record offset `0x34`.

## Animating lifts in a viewer

The [xi-model-viewer](https://github.com/vekien/xi-model-viewer) plays these live (any zone,
any `'@'` volume with a bound car):

| Step | Where |
|---|---|
| Read each placement's `0x34` interaction link | `ui/js/zone.js` (`parseZoneDef`) |
| Read each `0x36` entry's tail s16 params | `ui/js/zone.js` (`parseZoneInteractions`) |
| Bind a placement whose link → a `'@'` record; compute floor0/floor1 = `param/256 + record.Y` | `ui/js/zoneModel.js` (`liftMotionFromInteraction`) |
| Start the car on the floor it's parked on, oscillate to the other | `ui/js/zoneModel.js` (`liftHeightAt`, `bakeLiftDraws`) |
| Re-bake the car each frame; the static pass skips it | `ui/js/renderer.js` (shared dynamic-batch path) |
| Show it in the Data Struct inspector (kind **Elevator**, Motion = the two floors) | `ui/js/dat/inspect.js` |

Timing there is an approximation (the `m<from><to>` routines are not yet decoded); the floor
positions are exact.

# FFXI Footstep Sounds & Collision Terrain Types

How FFXI picks the sound (and footprint decal) you hear when an actor's foot
lands, and how that is driven by the **terrain type** baked into each
player-collision triangle. Reverse-engineered from the `xim` reference
reimplementation (`thirdparty/xim/`) and cross-checked against xi's collision
decoder (`src/xi/zone/xi_collision.py`).

> Companion docs: collision geometry itself lives in the encrypted `0x1C`
> ZoneDef section — see [zone/format.md](../zone/format.md) and the collision
> decode in [`xi_collision.py`](../../src/xi/zone/xi_collision.py). This doc is
> only about the *footstep audio/VFX* layer that sits on top of the terrain flags.
> For decoding the sound files themselves and finding which sounds a DAT uses, see
> [audio/format.md](../audio/format.md) and [audio/refs.md](../audio/refs.md).

## TL;DR

- Every player-collision triangle carries a **terrain type 0..10** (Object, Path,
  Grass, Sand, Snow, Stone, Metal, Wood, ShallowWater, DeepWater, Unk0xA). It is
  decoded from one bit of each of the triangle's four packed `u16` index words —
  it is *not* a separate per-triangle field.
- When a foot lands, the engine reads the terrain type off the floor triangle the
  actor is standing on and builds a 4-character `DatId` `0<terrain><move><shake>`,
  which it looks up in the zone DAT's **`fses`** sub-directory to get a sound
  pointer; that pointer resolves to a shared sound file under `sound/win/se/`
  (retail ships `.spw` — the `.ogg` paths quoted from xim below are that browser
  port's pre-converted asset convention).
- The `<move>`/`<shake>` digits come from the actor **model's** `info` section
  (for a PC, from the equipped **feet** item) — not from the zone. So footwear,
  not the zone, varies the timbre/pitch of the step.
- **Sand and Snow** additionally drop a footprint decal; the decal effect is
  **global** (loaded from `ROM/0/0.DAT`), the per-terrain footstep VFX is
  **zone-local** (`fses/fefs`).
- For a fully custom zone: the audio files and the footprint decal are global and
  need nothing from you, **but the `fses` pointer table is zone-local** — a zone
  DAT with no `fses` directory gets no footstep sound (and, in the xim port,
  throws). Clone the `fses` (and optionally `fefs`) directory from a stock zone.

---

## 1. Terrain types (index 0..10)

Defined as `enum class TerrainType` in
[`ZoneDefParser.kt:22`](../../thirdparty/xim/src/jsMain/kotlin/xim/resource/ZoneDefParser.kt):

| Idx | Name           | Surface                | Footmark? |
|-----|----------------|------------------------|-----------|
| 0   | `Object`       | generic object/default | no        |
| 1   | `Path`         | dirt/road path         | no        |
| 2   | `Grass`        | grass                  | no        |
| 3   | `Sand`         | sand                   | **yes**   |
| 4   | `Snow`         | snow                   | **yes**   |
| 5   | `Stone`        | stone/rock             | no        |
| 6   | `Metal`        | metal grating/plate    | no        |
| 7   | `Wood`         | wood planking          | no        |
| 8   | `ShallowWater` | shallow water          | no        |
| 9   | `DeepWater`    | deep water             | no        |
| 10  | `Unk0xA`       | unknown                | no        |

Only **Sand** and **Snow** carry `hasFootMark = true`
([`ZoneDefParser.kt:26-27`](../../thirdparty/xim/src/jsMain/kotlin/xim/resource/ZoneDefParser.kt)) —
i.e. they leave a footprint decal. Everything else is `false`.

### How the terrain type is encoded in a collision triangle

Each collision triangle is 8 bytes: four `u16` words (`rawP0`, `rawP1`, `rawP2`,
`rawD`). The low bits of each are a vertex/normal index; the **top nibble**
(`>> 12`) of each word is material/flag data. The terrain index is the **sum of
bit `0x8`** taken from each of the four nibbles, weighted by position
([`ZoneDefParser.kt:37-41`](../../thirdparty/xim/src/jsMain/kotlin/xim/resource/ZoneDefParser.kt)):

```kotlin
fun fromFlags(f0, f1, f2, f3): TerrainType {
    val index = (f0 and 0x8 ushr 3) + (f1 and 0x8 ushr 2) + (f2 and 0x8 ushr 1) + (f3 and 0x8)
    ...
}
```

So `f0` contributes bit 0, `f1` bit 1, `f2` bit 2, `f3` bit 3 → a 0..15 value,
of which 0..10 are named (11..15 throw `Unknown terrain-type` in xim).

xi decodes the identical expression per-triangle in
[`xi_collision.py:238`](../../src/xi/zone/xi_collision.py) and emits the result
as Wavefront `usemtl col_floor_<terrain>` / `col_wall_<terrain>` material groups
(`TERRAIN_NAMES`, [`xi_collision.py:95`](../../src/xi/zone/xi_collision.py);
`material_name()`, [`xi_collision.py:125`](../../src/xi/zone/xi_collision.py)),
so the terrain type is editable as a material assignment in a DCC tool. The
`hitWall` bit (`material & 0x40`) is orthogonal — it marks a blocker rather than a
standable floor and does not affect footstep selection.

---

## 2. Terrain type → footstep sound id

The sound is chosen in
[`AudioManager.playFootEffect()`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/audio/AudioManager.kt)
([`AudioManager.kt:116`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/audio/AudioManager.kt)):

```kotlin
val soundEffectId = DatId("0${terrainType.index}${movementInfo.movementChar}${movementInfo.shakeFactor+1}")
val sePointer = soundDir.getNullableChildAs(soundEffectId, SoundPointerResource::class)
```
([`AudioManager.kt:119-120`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/audio/AudioManager.kt))

The `DatId` is a 4-character FourCC built from four fields:

| Char | Source                          | Meaning                                  |
|------|---------------------------------|------------------------------------------|
| 0    | literal `'0'`                   | constant prefix                          |
| 1    | `terrainType.index`             | terrain type 0..9                        |
| 2    | `movementInfo.movementChar`     | movement char (footwear-derived, see §3) |
| 3    | `movementInfo.shakeFactor + 1`  | shake factor (footwear-derived) + 1      |

> **Quirk / caveat.** The terrain digit is interpolated as a *decimal* int, so
> index `10` (`Unk0xA`) would expand to the two characters `"10"` and produce a
> malformed 5-character id — the scheme only cleanly addresses terrain indices
> 0..9. (Contrast the *visual*-effect id below, which uses a hex nibble.)
> xi's `xi_footsteps.py` formats the index as a **hex** nibble (`f"0{idx:x}"`) —
> identical for 0-9, diverging only at index 10 (`0a` vs `010`, the malformed
> case above); which of the two the real client does is unresolved.

`soundDir` is the zone DAT's `fses` directory (passed in from `ZoneDrawer`, §4).
The lookup returns a `SoundPointerResource`, **not** the audio itself — the
pointer's `folderId`/`fileId` resolve to a shared OGG:

```kotlin
"sound/win/se/se${folderId}/se${fileId}.ogg"
```
([`AudioManager.kt:216-218`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/audio/AudioManager.kt),
via `toSoundFileName` at [`AudioManager.kt:147`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/audio/AudioManager.kt))

> **Note.** That `.ogg` extension (and the unpadded `se${folderId}` path form) is
> the **xim browser port's** pre-converted asset convention, not the on-disk
> client format — **retail ships `.spw`**, which xi resolves as
> `se{folder:03d}/se{file:06d}.spw` (see [audio/format.md](../audio/format.md)).

So the `fses` table maps **terrain+footwear → a row in the global `sound/win/se`
pool**; the actual waveform lives in that global pool, not in the zone.

### The matching per-terrain *visual* effect id

Separately from the sound, the terrain type also names a footstep **VFX**
resource via `TerrainType.toFootEffectId()`
([`ZoneDefParser.kt:44-47`](../../thirdparty/xim/src/jsMain/kotlin/xim/resource/ZoneDefParser.kt)):

```kotlin
fun toFootEffectId(): DatId {
    val char = index.toString(0x10)   // hex nibble: 0..9,a
    return DatId("0${char}00")        // e.g. "0300" for Sand, "0a00" for Unk0xA
}
```

This id is looked up in the zone's `fses/fefs` directory (§4). Note it uses a
**hex** nibble and a fixed `00` suffix — a different scheme from the sound id
above.

`DatId.fses` and `DatId.fefs` are the FourCC constants
`"fses"` / `"fefs"`
([`DatResource.kt:68-69`](../../thirdparty/xim/src/jsMain/kotlin/xim/resource/DatResource.kt)).

---

## 3. The `movementChar` / `shakeFactor` inputs

Both come from an `InfoDefinition`
([`InfoSection.kt:45`](../../thirdparty/xim/src/jsMain/kotlin/xim/resource/InfoSection.kt))
obtained from the actor model via
`ActorModel.getFootInfoDefinition()` →
`model.getMovementInfo()`
([`ActorModel.kt:213`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/ActorModel.kt)).

The fields are parsed from an `info` (`DatId.info`) section of a model DAT
([`InfoSection.kt:84-119`](../../thirdparty/xim/src/jsMain/kotlin/xim/resource/InfoSection.kt)):

- **byte 0 → `movementType`** — `MovementType` enum: `Walking(0)`, `Sliding(1)`,
  `Large(2)`, `Flying(3)`, `Unset(0xFF)`
  ([`InfoSection.kt:30-43`](../../thirdparty/xim/src/jsMain/kotlin/xim/resource/InfoSection.kt)).
- **byte 1 → `movementChar`** — a base-36 character (`0xFF` is normalized to
  `'0'`), via `toMovementChar`
  ([`InfoSection.kt:88,146-148`](../../thirdparty/xim/src/jsMain/kotlin/xim/resource/InfoSection.kt)).
- **byte 2 → `shakeFactor`** — a raw `u8` (the parser comments "Not sure"); the
  sound id uses `shakeFactor + 1`
  ([`InfoSection.kt:89`](../../thirdparty/xim/src/jsMain/kotlin/xim/resource/InfoSection.kt)).

### What feeds them per actor kind

- **PC / playable race** — `getMovementInfo()` composes the definition from *two*
  sources: `movementType` from the **race** config's `info` section, but
  `movementChar` **and** `shakeFactor` from the equipped **feet** item's `info`
  section
  ([`Model.kt:293-299`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/Model.kt)):

  ```kotlin
  val raceInfo = raceResources.raceConfig.getOnlyChildByType(InfoResource::class).infoDefinition
  val feetInfo = getInfo(ItemModelSlot.Feet) ?: InfoDefinition()
  return InfoDefinition(movementType = raceInfo.movementType,
                        movementChar = feetInfo.movementChar,
                        shakeFactor  = feetInfo.shakeFactor)
  ```

  Practically: **the equipped footwear selects the footstep sound variant**
  (`movementChar`/`shakeFactor` digits), while the race selects the gait class.
  There is no separate "walk vs run" digit in the sound id — gait shows up only
  as `movementType`, which is a *gate* (see below), not a digit.
- **Mounts / NPCs / other models** — read their own model's `info` section
  directly ([`Model.kt:510-514`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/Model.kt)).
- **Default / not-yet-loaded** — a default `InfoDefinition()` yields
  `movementType = Unset`, `movementChar = '0'`, `shakeFactor = 0`
  ([`InfoSection.kt:45-48`](../../thirdparty/xim/src/jsMain/kotlin/xim/resource/InfoSection.kt)).

### Gating in `emitFootSteps`

The terrain type used is read from the collision floor the actor most recently
landed on, and several conditions gate emission
([`ZoneDrawer.kt:182`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/ZoneDrawer.kt)):

- skip if invisible or in free-fall, or if there are no collision results
  ([`ZoneDrawer.kt:183-186`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/ZoneDrawer.kt));
- skip while an animation transition is in progress
  ([`ZoneDrawer.kt:191`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/ZoneDrawer.kt));
- only fires on the frame a foot **lands** — `getCollidingFoot()` returns the
  left/right foot only in `FootState.Landing`
  ([`ActorModel.kt:217-225`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/ActorModel.kt),
  [`ZoneDrawer.kt:193`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/ZoneDrawer.kt));
- the terrain type is taken from the first collision property under the actor
  ([`ZoneDrawer.kt:196`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/ZoneDrawer.kt));
- **the sound always plays**, but the **footmark decal and per-terrain VFX only
  emit when `movementType == Walking`**
  ([`ZoneDrawer.kt:200-211`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/ZoneDrawer.kt)) —
  so sliding/large/flying creatures get audio but no footprints/foot VFX.

---

## 4. Footsteps for a fully custom zone

The orchestration call passes **two different directories**
([`MainTool.kt:156`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/MainTool.kt)):

```kotlin
ZoneDrawer.emitFootSteps(actor, GlobalDirectory.directoryResource, currentScene.getMainAreaRootDirectory())
//                              ^^ systemEffects (GLOBAL)          ^^ zoneDat (ZONE-LOCAL)
```

- `GlobalDirectory.directoryResource` is loaded once from **`ROM/0/0.DAT`** — the
  global system-effects DAT
  ([`MainTool.kt:250,281`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/MainTool.kt)).
- `getMainAreaRootDirectory()` returns `mainArea.root` — the **current zone's**
  DAT directory
  ([`Scene.kt:282-284`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/Scene.kt)).

Tracing where each footstep resource is fetched:

| Resource                | DatId           | Directory                                   | Scope          |
|-------------------------|-----------------|---------------------------------------------|----------------|
| Footstep **sound** ptr  | `0<t><m><s>`    | `zoneDat.getSubDirectory(fses)`             | **zone-local** |
| Footstep **VFX**        | `0<hex t>00`    | `zoneDat.fses → fefs`                        | **zone-local** |
| Footprint **decal**     | `fmrk`          | `systemEffects` (global, recursive)         | **global**     |
| Actual **audio file**   | —               | `sound/win/se/seXXX/seYYY.ogg` (xim port — retail ships `.spw`) | **global**     |

Citations: sound dir `zoneDat.getSubDirectory(DatId.fses)`
([`ZoneDrawer.kt:200`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/ZoneDrawer.kt));
footmark decal `fmrk` from `systemEffects`
([`ZoneDrawer.kt:205`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/ZoneDrawer.kt));
per-terrain VFX `zoneDat.getSubDirectory(fses).getNullableSubDirectory(fefs)`
([`ZoneDrawer.kt:209-211`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/ZoneDrawer.kt));
OGG path ([`AudioManager.kt:216-218`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/audio/AudioManager.kt)).

### What a custom-zone author must provide

- **The OGG audio: nothing.** The `fses` entry is only a `SoundPointerResource`
  into the shared `sound/win/se` pool, which every install already has. You reuse
  existing footstep samples by pointing at them.
- **The footprint decal (`fmrk`): nothing.** It is read from the global
  `ROM/0/0.DAT`, never from the zone.
- **The `fses` pointer directory: REQUIRED, and it is zone-local.** This is the
  one piece that lives in your zone DAT. It must contain a `SoundPointerResource`
  named `0<terrain><movementChar><shakeFactor+1>` for each (terrain, footwear)
  combination you want audible. Miss it and you get silence — and note that the
  xim port calls the non-nullable `getSubDirectory(DatId.fses)`, which **throws**
  `IllegalStateException` if the `fses` directory is absent entirely
  ([`DatResource.kt:413-414`](../../thirdparty/xim/src/jsMain/kotlin/xim/resource/DatResource.kt)).
  (Real retail zone DATs all ship an `fses` directory; the retail client is
  presumably more tolerant of a *missing pointer* — `playFootEffect` itself only
  warns and returns when an individual pointer is absent,
  [`AudioManager.kt:122-125`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/audio/AudioManager.kt) —
  but a zone built from scratch with no `fses` at all is the failure case.)
- **The `fefs` sub-directory (per-terrain foot VFX): optional.** Looked up with
  the *nullable* `getNullableSubDirectory`, so a missing `fefs` just means no
  per-terrain footstep particle — sound and (sand/snow) footprint decal still work
  ([`ZoneDrawer.kt:209`](../../thirdparty/xim/src/jsMain/kotlin/xim/poc/ZoneDrawer.kt)).

**Recommended approach:** clone the `fses` directory (and `fefs` if you want the
foot VFX) from a stock zone whose surfaces match yours, then ensure your
collision triangles carry the matching terrain indices (§1) so the right pointer
is addressed. Because the terrain index is the second `DatId` character, a zone
that only ever uses, say, Stone (`5`) needs only the `05**` family of pointers
present.

xi has a targeted helper for the sound-pointer half:

```bash
uv run xi zone footsteps ROM10/2/4 --from ROM/0/57 --terrain snow
```

This copies matching `fses` `0x3D` `SoundEffectPointer` sections from the donor
zone. It does not touch collision, meshes, weather, `fefs` VFX, or the shared
audio files.

---

## Cross-reference: xi tooling

xi already round-trips the terrain types that drive all of the above:

- Decode/export per-triangle terrain as obj material groups —
  [`xi_collision.py`](../../src/xi/zone/xi_collision.py) (`TERRAIN_NAMES`,
  `_parse_mesh`, `material_name`).
- The exported `.collision.json` sidecar records a one-line footstep note
  ([`xi_collision.py:484`](../../src/xi/zone/xi_collision.py)).

Editing a triangle's terrain index (its `usemtl col_floor_<terrain>`) is therefore
sufficient to change which footstep `fses` pointer that surface addresses — no
audio asset work required, as long as the target `0<t><m><s>` pointer exists in
the zone's `fses` directory.

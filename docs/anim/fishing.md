# Fishing rods and hand-held props

How the client puts a fishing rod in a character's hands, what the data
actually is, and how `xi mv update` and the model viewer reproduce it. The
logging hatchet is covered at the end as the *other* way the game does a
hand prop, because the two look alike on screen and are nothing alike in the
data.

Everything here was worked out from the retail DATs, `FFXiMain.dll`, the
LandSandBoat item tables, and cross-checked against xim
(`research/external/xim`, `FishingStartEvent.kt`, `TickActorEvent.kt`,
`Actor.kt`, `MainDll.kt`).

## TL;DR

- A fishing rod is **not a ranged-weapon mesh**. Every race's ranged gear
  table maps rod model ids 1–15 to a 3-vertex stub. That is why the Ranged
  list never had rods and why an equipped rod is invisible on the back.
- The rod is a **rigged entity of its own**: a 9-joint skeleton (so the tip
  bends), one 36-vertex mesh, fifteen `fh??` clips plus `idl0`, and the same
  ten `fsh0`–`fsh9` schedules the character plays.
- `FFXiMain.dll` holds a **per-race base file id** for the rod set; the rod's
  **item model id** (`item_equipment.MId`) is added to it.
- While fishing the client **spawns the rod as a second actor at the
  character's position and rotation** and enqueues the *same* `fsh` schedule
  on both. The rod's own clips carry it from the actor origin into the grip.
  Nothing is attached to a hand joint.
- The viewer grafts the rod rig onto the character with its root under the
  actor origin and its clips merged by id, so the existing pose/skinning path
  plays both in lockstep.

## 1. Where the rod model comes from

### The ranged table only has a stub

`gear.xi_core.RACE_TABLES` gives Hume Male's ranged slot as one group,
`(9416, 256)`: file ids 9416–9671, model id = file id − 9416. Resolving the
low model ids through the file table:

| ranged mid | file id | DAT | contents |
|---|---|---|---|
| 0–21 | 9416–9437 | `ROM/31/80.DAT` | 3 vertices, no pieces (stub) |
| 22 | 9438 | `ROM/31/81.DAT` | 3 vertices (stub) |
| 23 | 9439 | `ROM/31/82.DAT` | the "None" row |
| 24+ | 9440+ | `ROM/31/83.DAT`… | real throwing / archery / gun models |

Every ROM's `VTABLE`/`FTABLE` agrees, so no later patch redirects them. The
same holds for every race. The rod model ids in the item DB are exactly the
ones in that stub range:

| MId | items (LandSandBoat `item_weapon.skill = 48` joined to `item_equipment`) |
|---|---|
| 0 | bait (`slice_of_bluetail`, `sardine_ball`, …) |
| 1 | Halcyon Rod, Composite Fishing Rod, MMM Fishing Rod |
| 2 | Single-Hook Fishing Rod |
| 3 | Clothespole |
| 4 | Carbon Fishing Rod |
| 5 | Glass Fiber Fishing Rod |
| 6 | Tarutaru Fishing Rod |
| 7 | Bamboo Fishing Rod |
| 8 | Hume Fishing Rod, Fastwater Fishing Rod |
| 9 | Yew Fishing Rod |
| 10 | Willow Fishing Rod |
| 11 | Lu Shang's Fishing Rod, Judge's Rod |
| 12 | Goldfish Basket |
| 13 | Ebisu Fishing Rod |
| 32782 (0x800E → 14) | Lu Shang's Fishing Rod +1 |
| 32783 (0x800F → 15) | Ebisu Fishing Rod +1 |

The `0x8000` flag on the +1 rods is masked off by the client; what is left is
the index.

### The client's own rod table

`FFXiMain.dll` carries a small u16 table, one entry per **look-race byte**
(the same 0–8 index the look block uses), giving the base file id of that
race's rod set. In the retail DLL it sits at `0x369a8`; xim finds it by the
byte-pattern hint `0x8B998B99` (two consecutive `39307` entries) — see
`MainDll.getBaseFishingRodIndex`.

```
rod file id = table[lookRace] + MId
```

| look race | base file id | mid 0 | mid 11 | mid 12 | mid 13 | mid 14 | mid 15 |
|---|---|---|---|---|---|---|---|
| 0 (unused) | 39307 | `ROM/90/58` | `ROM/90/69` | `ROM/143/68` | `ROM/267/61` | `ROM/334/72` | `ROM/334/73` |
| 1 Hume M | 39307 | `ROM/90/58` | `ROM/90/69` | `ROM/143/68` | `ROM/267/61` | `ROM/334/72` | `ROM/334/73` |
| 2 Hume F | 39339 | `ROM/90/70` | `ROM/90/81` | `ROM/143/69` | `ROM/267/62` | `ROM/334/74` | `ROM/334/75` |
| 3 Elvaan M | 39371 | `ROM/90/82` | `ROM/90/93` | `ROM/143/70` | `ROM/267/63` | `ROM/334/76` | `ROM/334/77` |
| 4 Elvaan F | 39403 | `ROM/90/94` | `ROM/90/105` | `ROM/143/71` | `ROM/267/64` | `ROM/334/78` | `ROM/334/79` |
| 5 Tarutaru M | 39435 | `ROM/90/106` | `ROM/90/117` | `ROM/143/72` | `ROM/267/65` | `ROM/334/80` | `ROM/334/81` |
| 6 Tarutaru F | 39435 | (shares 5) | | | | | |
| 7 Mithra | 39467 | `ROM/90/118` | `ROM/91/1` | `ROM/143/73` | `ROM/267/66` | `ROM/334/82` | `ROM/334/83` |
| 8 Galka | 39499 | `ROM/91/2` | `ROM/91/13` | `ROM/143/74` | `ROM/267/67` | `ROM/334/84` | `ROM/334/85` |

Each race owns 32 file ids. Mids 0–11 are the launch rods in `ROM/90`–`91`;
12 (the basket), 13 (Ebisu) and 14–15 (the +1 rods) were added later and
live in `ROM/143`, `ROM/267` and `ROM/334`, which is why a naive "scan the
folder" misses them. Mid 0 — the bait entry — still points at a complete rod
model, the one the client shows when the range slot holds bait.

The table is hard-coded in `gear.xi_core.FISHING_ROD_BASES` with
`fishing_rod_file_id(look_race, mid)` beside it.

### How it was found

Nothing in the DATs is labelled. The trail was: rod mids all resolve to the
stub → scan every section header in ~53,000 DATs for texture names → the rod
textures are `hf_sao1_` (竿, *sao*, is Japanese for rod) → those DATs form a
7-race × 12 block at `ROM/90/58`–`ROM/91/13` → search `FFXiMain.dll` for the
block's first file id as u16 → a 9-entry table, matching xim's hint.

## 2. What a rod DAT contains

`ROM/90/58.DAT` (Hume Male, mid 0) is typical:

```
skeleton   9 joints, 128 joint references
mesh       1 group, 36 vertices, texture "tim     hf_sao1_"
clips      fh00 fh10 fh20 fh30 fh40 fh50 fh60 fh70 fh80 fh90 fha0 fhb0 fhc0 fhd0 idl0
schedules  fsh0 = fh00,fh10   fsh1 = fh80,fh20   fsh2 = fh30,fh40   fsh3 = fh50,fha0
           fsh4 = fh60,fh40   fsh5 = fh70,fh40   fsh6 = fh90,fh40   fsh7 = fhb0
           fsh8 = fhc0        fsh9 = fhd0
```

Points that matter:

- **It is an entity, not gear.** Skeleton + mesh + clips + routines, exactly
  like an NPC. There is no `0x45` info section, no `standardJointIndex`, no
  grip joint. The joints form a single chain root → tip; the mesh is skinned
  to joints 2–8, so the pole bends.
- **Every clip animates the root joint's translation and rotation.** The rod
  starts at the actor origin and its own track moves it into the character's
  hands and swings it. This is why nothing is attached to a hand.
- **The clips carry scale tracks.** At frame 0 of `fsh0` joints 2–8 are
  scaled to `1e-5` and joint 8 to `1e-10`: the rod is invisible until the
  cast, then "unfolds" as the scale returns to 1. A viewer that ignores scale
  tracks will show the rod at full size at the wrong moment.
- **Clip and schedule ids mirror the character's.** The character's fishing
  DAT has `fh01`/`fh00` body-region pairs; the rod has the single `fh00`. A
  schedule ref of `fh0?` matches both.
- The Tarutaru block (`ROM/90/106`–`117`) has 14 clips instead of 15; the
  later rods (`ROM/334/76` etc.) carry 18 schedules.

## 3. How the client plays it (from xim)

`FishingStartEvent`:

```kotlin
val rangedItem = source.getEquipment(EquipSlot.Range)
val rangedItemModelId = ItemModelTable[rangedItem.info()]
val rangedItemModelIndex = MainDll.getBaseFishingRodIndex(sourceRace) + rangedItemModelId

InitialActorState(
    name = "(Rod)", type = ActorType.Effect,
    position = source.position, rotation = source.rotation,
    modelLook = ModelLook.fileTableIndex(rangedItemModelIndex),
    dependentSettings = DependentSettings(actorId, ActorFishingRod),
)
```

Then every tick (`TickActorEvent.syncDummyDependents`) the rod actor's
position, rotation, velocity and collision result are copied from the
character. And on each fishing state change (`Actor.updateFishingState`) the
character enqueues one of these routines on itself **and the same id on the
rod actor**:

| state | routine | what you see |
|---|---|---|
| Waiting | `fsh0` | cast, then hold |
| Hooked | `fsh1` | bite — rod dips, `ase` sweat particles |
| SuccessFish | `fsh2` | reel in a fish |
| BreakRod | `fsh3` | rod snaps |
| BreakLine | `fsh4` | line snaps |
| SuccessMonster | `fsh5` | reel in a monster |
| Cancel | `fsh6` | give up |
| ActiveCenter / Right / Left | `fsh7` / `fsh8` / `fsh9` | the minigame nudges |

So the whole mechanism is: two actors, same origin, same routine id, and the
rod DAT's own animation does the rest. There is no joint parenting and no
"weapon attach" at any point.

## 4. The character side

Each race has a fishing motion set. Hume Male's:

| DAT | contents |
|---|---|
| `ROM/90/15.DAT` | 28 clips (`fh?0` lower body, `fh?1` upper body), the ten `fsh` schedules, plus a `fish` directory of effects |
| `ROM/90/16.DAT` – `19.DAT` | companion motion packs (`fh?2` waist tracks, `chh2`/`run2`/`wlk2` locomotion) |

The `fish` directory in `90/15` is effects only: generators `vib0`/`vib1`
(rod-tip vibration) and `ase0`/`ase1` (the sweat drops on a bite), two
`0x19` key-frame curves, and a `0x1F` particle mesh `po` — a 2-triangle quad
with a 32×32 texture `fishing po`, the float. It is **not** the rod, despite
the name.

The routines use two ops the scheduler does not decode yet: `0x1f` (present
in `fsh2`/`fsh3`/`fsh4` with the routine's duration) and `0x5f` (with a
sibling routine id as its ref, e.g. `fsh1 → fsh0`). Neither is needed to
play the rod.

The character's fishing clips animate joint 87 on every upper-body clip. That
joint is the **forearm shield mount** (the per-race shield meshes at
`ROM/31/30`–`52` bind to it), not a rod bone — a red herring while hunting
for the attach point.

## 5. Tooling

### `xi mv update` (gear target)

`update_lists._append_fishing_rods` adds, for every mapped race, one Ranged
row per rod:

```json
{ "id": "135:90/59", "label": "Halcyon Rod (Composite / MMM Fishing Rod)",
  "group": "Fishing", "paths": ["ROM\\90\\59.DAT"], "mid": 1,
  "fileId": 39308, "rod": true }
```

- Names come from `gear_sets.json → fishingRods`, keyed by model id, so the
  list stays data-driven. Mid 0 (bait) is left out.
- The DAT is resolved through `FISHING_ROD_BASES`, never the ranged table.
- Rows are deduplicated by path, so a rerun is a no-op.
- `mid` is the real item model id, so the viewer's look string encodes what
  the game would.
- `rangedDisplay.showForActions: ["Fishing"]` (copied from `gear_sets.json`
  by the gear-sets target) tells the viewer which *action*, as opposed to
  which skill group, puts the ranged slot on show.

### The model viewer

`dat.js graftRig(model, rig, hostJoint, sourcePath)`:

1. Appends the rod's joints to the character's skeleton. The rod root is
   parented onto `hostJoint`; the loader passes **−1, the actor origin**,
   matching the client. (Hanging it off the right-hand reference instead
   applied the hand transform on top of the rod's own root track and left
   the rod floating a couple of units in front of the character.)
2. Re-indexes the rod mesh's `joint0`/`joint1` onto the appended joints and
   tags the group `rig: true, isWeapon: true` (so framing ignores its length).
3. Merges each rod clip's tracks into the character's clip **of the same
   id** (`fh00` → `fh00`, `idl0` → `idl0`), re-indexed. Clips the character
   lacks are added whole. Because the schedules already resolve `fh0?` by
   prefix, `fsh1` drives character and rod on one timeline with no second
   actor, no second pose, and no attachment code.

`CharacterList` sends a `rod: true` Ranged row to the loader as `rodPaths`
rather than into `weaponSlots.range`, so the bow-style mount re-parenting
never touches it, and counts Fishing as "in use" via `showForActions`. The
existing hidden-sources rule then hides the rod under every other action, the
way the client keeps it off the back.

## 6. Contrast: the logging hatchet

Logging (`ROM/37/18.DAT` for Hume Female) is the *other* prop mechanism:

```
em00: 0x07 lock 180 · 0x2f · 0x2e · 0x1f · 0x05 def? · 0x02 spawn ono0 @19 for 143 · 0x0a sfx 6108 @109
```

- The hatchet is a **`0x1F` particle mesh** (`hf_o`, 8688 bytes) drawn by
  generator `ono0`, which the routine spawns 19 ticks in.
- `ono0` has `attachType 0x9 = SourceActorWeapon` and joint reference 10
  (→ joint 62, the right-hand region). The particle rides that reference's
  position **and orientation every frame**, which is what swings the axe.
  In the viewer that means `getActorAttachTransform` and a per-frame resample
  for that attach type only; every other actor attach is still sampled once
  at spawn, because those are world points, not bone follows.
- `0x2f` / `0x2e` at the top of the routine appear to hide the main and sub
  weapons for its duration, and `0x1f` is the same undecoded op the fishing
  routines use.

So: **rod = second skeletal actor + shared routine id; hatchet = particle
mesh + weapon-attach generator.** Same look, different pipelines.

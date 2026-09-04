# FFXI Zone Effects (`0x05` particle / light generators)

How ambient world effects — fountain water spray, fire, smoke, lamp glow, sky
clouds — are stored and rendered in FFXI. Investigated against `ROM/1/41`
(Lower Jeuno) using the central fountain spray as a test bed.

> For the **system-level picture** — how spells resolve to effects, the `0x07`
> EffectRoutine sequencer, geometry/texture/keyframe binding, and authoring new
> spell visuals — see [effect_system.md](effect_system.md). This doc is the
> hands-on `0x05` deep-dive + the `xi fx` toolchain.

## TL;DR

- **Effects live INSIDE the zone DAT itself**, not in separate files. They are
  sections of **type `0x05`** (Lower Jeuno has 317 of them). The zone's geometry
  (`0x2E`), placements (`0x1C`), textures (`0x20`) and effects (`0x05`) all ship
  in the same `.DAT`.
- An effect is a **particle/light generator**: a FourCC-named resource that
  carries its own **local position**, a set of **parameters**, and **references
  by name** to a mesh (the particle quad/geometry), textures, and sub-effects.
- They are owned by the engine's **Generator / Scheduler** subsystem — NOT by
  "Events" (NPC dialogue/cutscenes) and NOT by "Sequences" (scheduler command
  chains). Sequences can *trigger* generators, but the spray itself is a generator.
- `0x05` sections are **unencrypted** (section meta mode byte = 0), unlike the
  `0x2E`/`0x1C` ciphers — so parameters can be read and edited directly.

## Where effects sit in the file

Same flat sequential section list as the rest of the zone (see
`docs/zone/format.md`). Each `0x05` section:

```
+0x00  char[4]  fourcc        e.g. "tki5", "lt01", "efc1", "pl00"
+0x04  uint32   meta          (paddedSize/16 << 7) | 0x05   (mode byte = 0 = unencrypted)
+0x08  8 bytes  zeros
+0x10  ...      effect body (params + reference stream)
```

`data_start = section.start + 0x10`, like every zone section.

## Body structure (mapped from `tki5`, the fountain spray)

Effects are a header region of fixed fields + an **offset table** pointing into a
**tagged opcode/parameter stream**. Annotated layout of a `tki` (size 0x1D0):

```
+0x10  attachFlags u16 / additionalAttachFlags u16 / scale amounts
       (looks like zeros on many ambient effects; NOT a free zero pad — see header table)
+0x30  uint32   runtime pointer (absolute addr, fixed up at load; ~0x01a2e310)
+0x40  float[8] all 1.0          — base color/scale params (RGBA / size)
+0x60  uint32   runtime pointer  (~0x01b8af90)
+0x74  uint16   emissionVariance — (early notes called this "count"; the real count u8 is @+0x78)
+0x80  uint32[] offset table     0x90, 0xC0, 0x180, 0x1C0  (offsets into this section; = data +0x70)
+0x90  ...      tagged param stream begins
```

The **tagged stream** is a sequence of entries shaped roughly
`tag:uint16  sub:uint16  <data...>` where data is float params, counts, or a
4-char reference. Tags seen in `tki5` (low,high bytes): `0a04`, `1507`, `010c`,
`02e4`, `0708`, `0904`, `0f04`, `1264`, `1364`, `1602`, `2e04`, `0e01`, `08 61`,
`2802`, `1b01`. (Meanings not yet fully decoded — see xim Notes opcodes below.)

Embedded references and the **position** appear inline in the stream:

```
... 01 0c 00 00 00 40 00 00 00 00 00 00  "sibj"     <- mesh reference (the splash quad, 0x2E)
    00 00 00 00                                       (u32, 0)
    <float x> <float y> <float z>                     <- LOCAL POSITION of this jet
...
    16 02 00 00  50 50 50 00                          <- "PPP" = RGB color bytes (0x50,0x50,0x50)
...
    "tkus"                                             <- sub-effect reference
```

For `tki5` the position decodes to `(-17.86, -7.20, 5.41)` — exactly at the funsui
fountain (placement `-15.73, 0, 5.23`). All five `tki*` cluster there at Y=-7.20
(the spout height), one per jet — see `docs/dats/ROM_1_41.md`.

### Parameter map (fountain `tki` effect)

Derived by **diffing the 5 jets** (`tki1`–`tki5`): whatever differs between them is
a per-jet parameter; the rest is the shared effect template. Offsets are from the
section start; payloads follow a 2-byte tag at the listed offset. **May be specific
to this effect type** — re-derive per effect by diffing its instances.

| offset | tag | type | meaning | status |
|--------|-----|------|---------|--------|
| +0xD4 / +0xD8 / +0xDC | (after `sibj` ref) | 3×f32 | **position** x / y / z | ✅ confirmed |
| +0x134 / +0x138 / +0x13C | `0f04` @+0x130 | 3×f32 | **scale** (width, height, depth); jets ~(0.3–0.5, 1.5, 0.3–0.5) | ✅ confirmed (×4 → big tall columns in-game) |
| +0x164..+0x167 | `0216` @+0x160 | 4×u8 | **color** B,G,R,A tint (jets ~`50 50 50 00` grey) | ✅ confirmed (set to `00 FF 00` → green spray in-game) |
| +0x184 / +0x188 | `2e04` @+0x180 | 2×f32 | ~~draw distance~~ **mislabeled** — xim says `2e04` is a texcoord keyframe; the real draw-distance knob is `0a04` GeneratorCull (see +0x94 row and the correction table below). xi's `--range` writes only `0a04`. | ❌ retracted |
| **+0x76** | — (header field) | u16 | **spawn interval** (frames between spawns); jets 39/44 | ✅ confirmed (240 → ~1.3s gaps; 2 → continuous gush) |
| +0xF0 (.Y), +0x100 (.Y) | `02e4`, `0708` | f32 | **flow / texture-scroll speed** (Y component; how fast the texture streams up the mesh). NOT particle launch velocity | ✅ confirmed (×10 → texture whips past; 0.05× → slow stream) |
| +0x74 | — (header field) | u16 | ~~particle count~~ **emissionVariance** (see correction table below; the count u8 is @+0x78) | ❌ superseded |
| +0x94 | `0a04` @+0x90 | f32 | **draw distance** = GeneratorCull `maxEmitDistance` (default 15). This is the real range knob (`fx set --range`); `2e04` is unrelated (texcoord keyframe) | ✅ confirmed |
| +0x11C, +0x128 | `0904` | f32 | **direction / tilt** (per-jet angle) | candidate |
| +0x078 | — | ~~f32~~ | per-jet toggle reading is stale — +0x78 is the **u8 particlesPerEmission** and +0x79 the **u8 genFlags** (a 4-byte float here would span both plus moreFlags) | ❌ superseded |

`@0x74`/`@0x76` are fixed header fields (u16); the float params (color/scale/range/
flow/direction) are located by their opcode tag, so those edits work on any effect
that shares the format — header-field offsets may be more effect-specific.

These params are located by their **opcode tag** (not fixed offset), so the same
edit works on any effect sharing the format: color = tag `16 02` (+4 → BGRA),
scale = tag `0f 04` (+4 → 3×f32), range = tag `0a 04` GeneratorCull (+4 →
`maxEmitDistance` f32; NEAR unused), position = the 3×f32 after the placed-mesh
FourCC reference.

Editing is a direct in-place byte write (`0x05` is unencrypted; no reimport). E.g.
setting the 3 color bytes at each `tki`'s +0x164 to `00 FF 00` tinted the whole
fountain spray green — confirmed in-game.

**Key fields for editing (per jet):**
- **Position**: the float-triple immediately after the `sibj` reference (`sibj` +
  4 bytes of u32 + 12 bytes xyz). Move/duplicate jets by changing this.
- **Base color/scale**: the `float[8]` at +0x40 and the `"PPP"`-style RGB bytes.
- **Per-stage params**: the floats in the tagged stream (life, velocity, size,
  fade — exact mapping TBD via opcode decode).

The two **runtime pointers** (+0x30, +0x60) are absolute addresses the loader
fixes up — leave them alone when editing params; they aren't file offsets.

## Validated against xim (authoritative)

xim's JS renderer draws these effects correctly, so its parser is ground truth:
`thirdparty/xim/src/jsMain/kotlin/xim/resource/ParticleGeneratorParser.kt` (+
`ParticleGeneratorSettings.kt`, `ParticleGeneratorUpdaters.kt`). Comparing it to my
byte-pattern reverse-engineering:

### Real header layout — MIND THE TWO FRAMES

⚠️ The rows below use **two different offset bases** (this table used to be labeled
"data-start relative" as a whole, which is how the frames got conflated across docs).
`data_start = section_start + 0x10`.

**Data-start relative** (xim's parse frame — `ParticleGeneratorParser` reads these
from the start of the section *data*):

| off (data) | = off (section) | field | notes |
|-----|-----|-------|-------|
| +0x00 | +0x10 | `attachFlags` u16 | attachType (low 4 bits) + attachedJoint0 (bits 4-9) + attachedJoint1 (bits 10-15) — how it binds to a caster/joint |
| +0x02 | +0x12 | `additionalAttachFlags` u16 | source-oriented; actor position/size scale targets |
| +0x10 | +0x20 | `actorPositionScaleAmount` f32, +0x14 `actorSizeScaleAmount` f32 | |
| +0x50 | +0x60 | `unkId` u32, +0x54 `environmentId` DatId | |

**Section-start relative** (the emission group — this is the frame `xi fx` reads and
writes, and the `+0x76` interval is in-game A/B verified in this frame):

| off (section) | = off (data) | field | notes |
|-----|-----|-------|-------|
| +0x74 | +0x64 | **`emissionVariance` u16** | (I mislabeled this as "count") |
| +0x76 | +0x66 | **`framesPerEmission` u16 (+1)** | = spawn interval ✓ (note the +1: stored 39 → 40 frames) |
| +0x78 | +0x68 | **`particlesPerEmission` u8** | the real "count" (I had the wrong offset) |
| +0x79 | +0x69 | **`genFlags` u8** | bit `0x04` = continuousSingleton, **bit `0x10` = `autoRun`** |
| +0x7B | +0x6B | `moreFlags` u8 | bit `0x20` = batched (weather) |
| +0x80 | +0x70 | `section1..4Offset` 4× u32 | offsets to the four opcode sub-streams |

### The body is FOUR opcode sub-sections, not loose tags

Each sub-section is a stream of entries: a `u32 config` then payload. `opCode =
config & 0xFF`, `size = (config >> 8) & 0x1F` in **4-byte words**, `allocOffset =
config >> 0xD`. Sub-sections: **1** generator updaters, **2** particle
initializers, **3** particle updaters, **4** more updaters. So my "tags" were
really `opCode` (the low byte of the config word) inside a specific sub-section.

### My findings vs xim

| my finding | xim truth | verdict |
|------------|-----------|---------|
| spawn interval `@0x76` | `framesPerEmission` (+1) | ✅ right |
| color `0216` → `+0x164` | sec2 opcode **0x16 ColorSetup** (BGRA) | ✅ right (it's an opcode, not a free tag) |
| scale `0f04` | sec2 opcode **0x0F ScaleInitializer** | ✅ right |
| draw distance = `0a04` (LOD gate) | sec1 opcode **0x0A GeneratorCull** → first float = `maxEmitDistance` | ✅ right — this is THE draw distance |
| draw distance = `2e04` near/far (10,15) | 0x2E in sec2 = a **TexCoord.u keyframe**, not distance | ❌ mislabeled — `0a04` did the work in that test; `2e04` was incidental |
| flow/scroll = `02e4`/`0708` .Y | byte-pattern false matches (0x02/0x07 are other opcodes) | ❌ likely wrong |
| count = `@0x74` (=5) | `@0x74` is `emissionVariance`; count is `@0x78` (u8) | ❌ wrong offset |
| (spawn gate — unknown) | **`autoRun` flag, `@0x79` bit `0x10`** (auto-spawn vs scheduler-triggered) | 🆕 real flag, now exposed. NB it's *not* why the a133 transplant failed (a133 is `autoRun=true`) — that was a missing `0x21` SpriteSheetMesh dep (see "How effects are triggered" below) |

Other useful opcodes (sec2 initializers unless noted): `0x10/0x11` scale variance,
`0x17/0x18` color variance, `0x1D` sprite-sheet, `0x1E` blend func, `0x44/0x53/0x6A`
child generators, `0x58` point-light params, `0x4C` audio range; sec3 `0x48`
`DoubleRangeDrawDistanceUpdater` (a second, per-particle draw-distance fade).

### Actionable corrections for our tooling — ALL IMPLEMENTED (kept for history)

- ✅ `fx set --count` writes **`@0x78` (u8)** (`_OFF_COUNT = 0x78`, `xi_core.py`).
- ✅ `fx set --autorun` exists (toggles `@0x79` bit `0x10`, `xi_set.py`).
- ✅ `--range` writes only **`0a04` GeneratorCull `maxEmitDistance`**; no `2e04`
  write exists anywhere in the code.
- ✅ Color/scale offsets are opcode-located (0x16/0x0F).

## How effects are triggered — `0x07` EffectRoutine

From xim (`EffectRoutineParser.kt`, `Scene.kt`). On zone load, two things spawn
effects:

1. **Direct** — a `0x05` ParticleGenerator with **`autoRun`** (genFlags `@0x79`
   bit `0x10`) is registered straight away. Ambient zone effects (fountain jets,
   torches) work this way.
2. **Via routine** — a `0x07` **EffectRoutine** with `autoRunHeuristic` runs and
   fires the generators it references. Boss/ability/cutscene effects work this way
   (the routine is normally kicked off by the server on cast).

**EffectRoutine layout**: header `0x10` zeros, then `sec1/sec2/sec3` offsets +
`totalDelay`; three opcode streams (`opcode:u8`, `unkCombo:u16` whose low 5 bits =
arg-dword count, `unk:u8`). **Section 2** holds the timed commands — each starts
with `delay:u16` + `duration:u16`, then opcode-specific args, referencing other
resources **by FourCC (DatId)**:

| sec2 opcode | command | references |
|-------------|---------|------------|
| `0x02` | **ParticleGeneratorRoutine** — fire a `0x05` effect | generator FourCC |
| `0x03`/`0x09`/`0x3B`/`0x3C`/`0x57` | LinkedEffectRoutine — run another `0x07` | routine FourCC |
| `0x05`/`0x06` | SkeletonAnimationRoutine | anim FourCC |
| `0x0A`/`0x0B`/`0x4A` | sound-effect emitter | sound |
| `0x19` | SpellEffect | spell index |
| `0x1E` | ParticleDampen | generator FourCC |

`autoRunHeuristic` is set when: the directory holds a **single** routine under the
`effe` dir (DatParser), or sec2 opcode `0x52` (timed/interval, e.g. weather), or
sec3 opcode `0x01` (on-complete **loop**). xim itself notes this is a heuristic —
the true server-vs-auto distinction isn't fully known.

### Resolved: why the dungeon fire (`a133`) didn't transplant

It was **not** the trigger layer or `autoRun` (a133 is `autoRun=true`,
`attachType=None` — same as the ambient torches). The cause was a **missing
dependency**: my first manual transplant copied `fire(0x20)` + `hiaa(0x19)` but
**missed `fire(0x21)` SpriteSheetMesh** — the particle's actual geometry — so there
was nothing to draw. `a133`'s full dep set is just `fire(0x20+0x21)` + `hiaa(0x19)`,
all of which `xi fx copy --from` now brings (`_DEP_TYPES` includes `0x21` and
`0x1F`). **Verified in-game**: `fx copy a133 --from ROM/0/73` renders the dungeon
boss flame at the Lower Jeuno fountain. So cross-DAT transplant works for *any*
effect — boss/ability included — as long as the full dependency set comes along.

## The fountain effect stack (case study)

The Lower Jeuno fountain is **three independent `0x05` effect layers**, each naming
a mesh + a position, all clustered at the basin:

| Effect(s) | places mesh | role | Y |
|-----------|-------------|------|---|
| `tki1`–`tki5` | `sibj` (sibjun3) | splash **jets** | -7.20 |
| `awa1`–`awa6` | `awan` | **bubbles** | -0.42 |
| `grid` | `suim` (suimen, 水面 "water surface") | **puddle** | -0.39 |

This is the general shape of a zone effect: **a `0x05` generator that names a mesh
and positions it.**

### Orphan meshes (invisible in zone export)

`sibj`, `awan`, `suim` are `0x2E` meshes with **no `0x1C` placement record** — they
exist only to be summoned by their effect. Since `xi zone export` assembles only
`0x1C`-placed objects, **effect-placed meshes never appear in the exported GLB**
(this is why the fountain "puddle" was missing from the export). To find them:
list `0x2E` mesh names, subtract the set of placed mesh ids — the remainder are
orphans (ROM/1/41: 64, incl. sky spheres, clouds, gulls, `lowsea` ocean).

## Linked resources (the texture/mesh chain)

The fountain effect resolves a name chain entirely within `ROM/1/41`:

```
tki1..tki5 (0x05 generator)
   ├─ "sibj"            -> 0x2E mesh  (the splash quad geometry)        @0x1248e0
   ├─ "tkus"            -> sub-effect (0x05)                            (referenced)
   └─ textures (0x20), shared funsui set:
        funsui_abuk     foam   (abuku)   @0x1120e0
        funsui_sib1     splash (shibuki) @0x11bdd0
        funsui_umi02    water  (umi)     @0x117980
        funsui_tare     drip   (tare)    @0x116530
```

(The mesh's own texture-name field is how the splash quad picks up `funsui_sib1`,
etc. — same texname matching the rest of the zone uses.)

## How effects render (engine subsystem)

From the fan decompile `thirdparty/xiclient` (a reverse-engineering effort — a
**strong hint, not authoritative truth**) and `thirdparty/xim/src/Notes.txt`:

- **Generator** = `CYyGenerator` (+ `CYyGeneratorClone`); element types under
  `World/Generator/Effects/`: `CMoElem` base, `CMoD3mElem`/`CMoD3aElem`/`CMoD3bElem`
  (model-based particles), `CMoPointLightProgElem`, `CMoDistElem`, `CYySoundElem`.
- **Scheduler** (`CMoSchedulerTask`) drives generators: tag `0x02` = "execute
  generator" (clones it and anchors it to a caster/target position); tag
  `0x03`/`0x73` = chain to another scheduler resource (a "sequence").
- **Resource types** (`Resource/ResourceType.h`): `Generater=5`, `D3m=31`,
  `D3a=33`, `D3b=37`, `Sep=61`, `Lfd=57`, `Pointlightprog=71`.
- **Particle script opcodes** (`xim/src/Notes.txt`): `0x48` distance fade/cull,
  `0x30` scale, `0x2E` distance fade, `0x45/0x3C/0x3F/0x44/0x01` parent/child
  attach, `0x53/0x6A` billboard. These overlap the tags we see in the `tki` stream
  and are the next reference for full param decode.

### Events vs Sequences (clarification)

- **Events** = NPC interaction / cutscene dialogue (largely server-driven, separate
  event data). Not particle effects.
- **Sequences** = scheduler command chains. They can trigger a generator but aren't
  the effect itself.
- The fountain spray is a **generator** (`0x05`), distinct from both. It is *not*
  linked from the `funsui` `0x1C` placement record — that record's effect-link
  field (`+0x34`) is `0` (the floats there are LOD distances 10/100/80).

## Zone object animation (generator-bound placements)

Moving zone props — windmill blades, scrolling lava sheets, pulsing lights — are ordinary
`0x2E` meshes with a `0x1C` placement whose **BlockID (`+0x34`) names a `0x05` generator**.
The client skips such records in its normal pass and lets the generator draw the mesh:
`StandardSetup` (sec2 `0x01`) links the mesh (`StaticMesh` `0x0B`) and holds the base
position, sec2 `0x09` the rotation, and the motion opcodes do the rest. The minimal
spinning-object generator (Rabao `f001`, 320 bytes) is:

```
header    autoRun (genFlags 0x10), framesPerEmission 1, particlesPerEmission 0
sec2      0x01 StandardSetup  link "de_7", basePos (−38.0, −4.45, 51.8), lifespan 0 (= forever)
          0x09 Rotation       (0, 0.244, 0)         ← the placement's yaw
          0x0B RotationVelocity (0, 0, 0.0122)      ← radians per 60 Hz frame, local Z
          0x0F Scale (1,1,1)  0x1E BlendFunc 0x44   0x16 Color 80 80 80 80
sec3      0x05 RotationUpdater                      ← rotation += velocity every frame
```

So "make our own animation" = place the mesh, clone a generator like `f001` re-targeted at
that mesh / position / rotation (only `0x0B` needs a new value for a different spin), and
write the generator's FourCC into the record's BlockID. `xi zone import-json` does exactly
this for a placement `add` carrying `anim` (see
[../zone/import-json.md](../zone/import-json.md) and
[../zone/format.md → Generator-bound objects](../zone/format.md#generator-bound-objects-animated-placements)).

## Tooling: `xi fx`

```
xi fx json   <dat>                 # every 0x05: name, size, params, placed mesh + position
xi fx delete <dat> <name>...       # remove effect(s) by exact name or prefix
```

`<dat>` accepts a path or ROM spec (`ROM/1/41`). `delete` matches each arg by exact
FourCC **or prefix**, splices the sections out (from the end, so offsets stay
valid), and keeps a `<dat>.base` backup. Section-resize is tolerated by the engine
(same as mesh-merge growth). `--dry-run` previews. Implemented in `the src/xi/fx/ package`.

Examples:
```
xi fx json   ROM/1/41
xi fx delete ROM/1/41 tki awa grid    # strip the whole fountain effect set
```

### Effect type library (`src/xi/fx/fx_library.json`)

`fx json` annotates each effect with a human label from a curated registry,
`src/xi/fx/fx_library.json` (`classify()` in `xi_core.py`). Entries carry
`label`, `category`, `verified`, `notes`. A trailing `?` in the listing =
`verified: false`; `(unidentified)` = no library match.

**Classification** (verified mesh/tex/name wins; else name → mesh → tex):

1. Build a **name** candidate: exact `names` hit, else longest matching `prefixes`
   entry (prefixes feed the name signal, not a separate final step).
2. Look up **mesh** (`meshes`) and **texture** (`textures`) candidates from what
   the effect places/references.
3. Among candidates with `verified: true`, pick the first of mesh → tex → name.
4. Otherwise fall back: name → mesh → tex.

So a verified mesh/texture label beats a tentative name/prefix guess; without any
verified hit, the name (incl. prefix) wins over content signals.

**Mesh-less sprite effects & their position.** Some effects place no mesh — they
billboard a texture directly (e.g. ROM/0/73's dungeon torches: ~132 `a0NN` effects
referencing the `fire` texture, sitting on `syokudai` candle-stand pillars). Their
**position is the xyz after the first referenced resource** (the texture FourCC),
not after a mesh — for the torches that's `+0xc4` (`fire` ref `+0xbc`, then +8).
`_pos_offset` anchors on the first mesh *or* texture ref, so `fx copy`/`--pos`
handle these too.

**Live position → DAT coords (Ashita).** The `xitools` Ashita addon
(`addon/xitools/`, `/xi pos`) reports the player's position for
point-and-place. The axis order differs from the DAT: **`DAT(x, y, z) =
Ashita(X, Z, Y)`** — Ashita's `Z` is the vertical axis, its `Y` is depth. The addon
prints a DAT-ready `--pos` (already swapped). Verified at the Lower Jeuno fountain.

**Coordinate convention: `−Y` is UP.** FFXI world space is Y-down (gravity = +Y).
Effect/jet positions are negative-up — the fountain water jets sit at `y=−7.2`, a
Castle Zvahl wall torch at `y=−26`. Setting a positive `y` buries an effect
underground (we lost a transplanted flame at `y=+12`, found it at `y=−10`). To
raise something, make `y` *more negative*. (Zone export/import already handles this
via the correction node's 180°-X flip; it only bites when editing raw effect
positions directly.)

**Section types** an effect touches are named in [dat_sections.md](../reference/dat_sections.md)
(from xim): `0x05` ParticleGenerator (the effect), `0x19` **ParticleKeyFrameData**
(animation curves), `0x1F` **ParticleMesh** / `0x21` **SpriteSheetMesh** (geometry),
`0x20` Texture, `0x3D` SoundEffectPointer, and `0x07` **EffectRoutine** (the
trigger/sequencer).

**Cross-DAT transplant.** `xi fx copy --from <src_dat>` copies an effect between
DATs, bringing every dependency it references that the destination lacks — the
`0x20` texture, the `0x21` SpriteSheetMesh, `0x19` ParticleKeyFrameData, and `0x2E`
meshes (a name may map to several sections; all dep-typed ones are copied). Note it
does **not** bring the `0x07` EffectRoutine that may trigger the effect.
- **Full deps are enough for autoRun effects** — including boss/ability sources.
  Verified in-game: `a133` from ROM/0/73 (`autoRun=true`, `attachType=None`)
  renders at the Lower Jeuno fountain once `fire(0x20+0x21)` + `hiaa(0x19)` come
  along (an earlier miss of `0x21` SpriteSheetMesh was the real failure, not the
  trigger layer). See [Resolved](#resolved-why-the-dungeon-fire-a133-didnt-transplant).
- Only generators that are genuinely **`autoRun=false`** need **`--replace`** onto
  a spawning slot or `fx set --autorun` afterward.
- The `+0x30`/`+0x60` body fields are runtime pointers; `+0x30` being zero does
  *not* gate spawning (ambient torches have it zero and still render).
- Also verified: a Castle Zvahl torch (`lb09` + `fire` 0x20/0x21 + `lirr/lirg/lirb/hiaa`
  0x19) injected at the Lower Jeuno fountain renders a standalone flame.

This labels every effect that places a mesh or texture (in Lower Jeuno: 78 lights,
68 lamp posts, clouds, windows, sea, sky…; mesh-less sprites classify via the
texture signal). **What it can't yet classify** are effects with **no mesh and no
texture** reference — pure particle/weather generators (`a###`, `b###`, `w###`,
`0N00`, `stk`, `gNNN`, point lights `pl`) — those need the element class decoded
from the tagged opcode stream (future). The `0x05` body has no clean standalone
"type byte": a light and a mesh-particle share the same header fields.

The library is **hand-maintained** — extend `names`/`meshes`/`textures`/`prefixes`
as effects are identified (no code change). Verified so far: `tki`=water jet,
`awa`=bubbles, `grid`/`suim`=water surface, `ligh`=light.

> Caveat: `fx delete` edits the DAT **in place** (does not reset to `.base`), so it
> stacks on mesh-merge/placement edits. But `xi zone import` rebuilds from `.base`
> (which still has the effects), so re-running import **re-adds** deleted effects.
> Effect editing is not yet wired into the import pipeline.

## Project goals (staged) — fountain as test bed

1. **Delete** — ✅ DONE. `xi fx delete`. Verified in-game: `tki`/`awa`/`grid`
   removed, fountains present, no water, no crash (1934 → 1922 sections).
2. **Modify** — change params on an existing effect (position is already located:
   the float-triple after the mesh reference; then color/scale; then speed/life via
   the opcode decode). Next milestone.
3. **Inspect** — emit an `effects.json` of every `0x05`'s fields/params for offline
   authoring (`xi fx json`).
4. **Duplicate** — copy an effect to a new position (e.g. spray on each merged
   fountain). Needs section-grow + position edit; mechanically the same as the
   verified mesh-merge growth.

Authoring brand-new effect *types* is a later phase.

## Files / references in this project

- `docs/dats/ROM_1_41.md` — Lower Jeuno section histogram + full `0x05` catalog +
  fountain effect identification.
- `docs/zone/format.md` — zone DAT section format (lists `0x05` as "particles").
- `src/xi/zone/` — zone export/import (mesh `0x2E`, placement `0x1C`); no `0x05`
  handling yet (to be added).
- `thirdparty/xiclient/src/XIClient/include/World/Generator/` — `CYyGenerator`,
  `Effects/CMo*Elem`.
- `thirdparty/xiclient/src/XIClient/include/Resource/ResourceType.h` — resource codes.
- `thirdparty/xim/src/Notes.txt` — particle script opcodes.

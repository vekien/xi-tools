# FFXI Effect System — how spells, effects, geometry & textures connect

A system-level reference for **authoring new visual content** (spells, abilities,
ambient effects) on CatsEyeXI. It maps the whole chain from "a spell is cast" down
to "triangles on screen", with the binary formats and the resource graph that wires
them together.

> **Source & trust.** Everything here is reverse-engineered from the **xim** renderer
> (`thirdparty/xim`, fan-made but it renders FFXI effects correctly) and verified
> against real DAT bytes where noted. xim is a *guide*, not ground truth — confirm
> against bytes before relying on a detail. Companion docs:
> [effects.md](effects.md) (the `0x05` deep-dive + the `xi fx` toolchain),
> [dat_sections.md](../reference/dat_sections.md) (section type codes),
> [../zone/zones.md](../zone/zones.md) (zone→DAT map + FileTable method).

---

## The big picture

```
  spell cast (spellId)
        │   server tells client "actor casts spell N at target"
        ▼
  SpellAnimationTable[spellId] = index        ← from the SERVER (LSB spell_list.sql),
        │                                        not a client DAT
  fileId = 0xAF0 + index
        ▼
  FileTableManager.getFilePath(fileId) ─────► ROM/x/y.DAT     (the spell's animation DAT)
        │
        ▼
  DAT  ──parse──►  section tree (Directory 0x01 nodes + leaves)
        │              find child  DatId "main"
        ▼
  0x07 EffectRoutine  "main"   ── the sequencer / trigger layer ──
        │   timed commands (delay, duration) reference resources by 4-char DatId:
        ├─ 0x05 ParticleGenerator   "fire", "tki5", …   (the emitters)
        ├─ 0x2B SkeletonAnimation                        (caster/target motion)
        ├─ 0x3D sound (routine ops 0x0A/0x0B/…), 0x54 WeaponTrace, …
        └─ 0x03/0x09 LinkedEffectRoutine                 (chain another 0x07)
        ▼
  0x05 ParticleGenerator  emits particles; each particle is drawn with:
        ├─ geometry:  0x1F ParticleMesh | 0x21 SpriteSheetMesh | 0x25 WeightedMesh
        ├─ texture:   0x20 Texture  (+ sprite-sheet frames, blend mode)
        └─ animation: 0x19 ParticleKeyFrameData  (curves sampled over particle life)
```

Two ways an effect runs (see [Triggering](#5-triggering--autorun)):
1. **Server/spell-triggered** — the normal path above (a routine fired on cast).
2. **autoRun** — ambient zone effects (fountains, torches) that self-start on zone load.

---

## 1. The DAT resource model

A DAT is a **flat, ordered list of sections**. Some sections (`Directory`, `End`)
turn that flat list into a **tree**; the rest are leaves (a mesh, a texture, an
effect, …). Sections reference each other by a **4-char `DatId` (FourCC)** — never by
byte offset.

### Section header (16 bytes)
```
0x00  4   DatId        4-char id / FourCC (e.g. "main", "fire", "tki5", "data")
0x04  4   meta         type = meta & 0x7F ; size = ((meta >> 7) & size_mask) * 0x10
0x08  8   padding      (zeros) — data starts at section + 0x10
```
**Size field is 19 bits** (`size_mask = 0x7FFFF` on write — bits 7–25). Writers must
use that mask (`xi.common.xi_section`); overflowing into bit 26 corrupts `is_shadow`
and the client reads the section 8 MiB short. Some readers still accept a wider mask
under the 8 MiB ceiling. `dataStart = sectionStart + 0x10`; next section at
`sectionStart + size` (no gaps). xim: `DatParser.kt:128-160`.

### Section types
See [dat_sections.md](../reference/dat_sections.md) for the full code→name table. The ones that
matter for effects: `0x01` Directory, `0x05` ParticleGenerator, `0x06` Route,
`0x07` EffectRoutine, `0x19` ParticleKeyFrameData, `0x1F` ParticleMesh, `0x20`
Texture, `0x21` SpriteSheetMesh, `0x25` WeightedMesh, `0x2B` SkeletonAnimation,
`0x3D` SoundEffectPointer, `0x3E` PointList, `0x45` Info, `0x49` SpellList,
`0x53` AbilityList, `0x54` WeaponTrace. (`DatResource.kt:14-46`.)

### Directory tree (`0x01` + `0x00`)
- A `Directory` (`0x01`) **pushes** a new scope; an `End` (`0x00`) **pops** back to
  the parent. Sections in between are that directory's children.
- Effect DATs commonly nest `root → "data" → "effe"` (effects) and `"mode"` (models).
- Resolution of a `DatId` reference: **local directory first, then up the parent
  chain, then the global registry** (`DatResource.kt:433-459`, `TextureLink.kt`).

### Cross-DAT references
A `DatId` only resolves to a section that is **reachable in the tree or in a global
cache** — references do *not* carry a DAT path. So to transplant an effect into
another DAT you must **bring every section it names** (texture, sprite-sheet mesh,
keyframe data, sub-meshes) into the destination. This is exactly what
`xi fx copy --from` automates (`_DEP_TYPES = 0x20, 0x21, 0x1F, 0x19, 0x2E`).

### Coordinates & units
FFXI is **Y-down**: the up vector is `(0, −1, 0)` (`Vector3f.kt`). Right-handed with
inverted Y; X = east, Z = north. World units are meters-ish (no global scale).
Ashita player position → DAT position: `DAT(x,y,z) = Ashita(X, Z, Y)` (Ashita's
Z is vertical). Rotations stored as `u8` are `2π * (n/255)`.

---

## 2. How spells are named & resolved

A spell has a numeric **`spellId`**. Three independent tables turn that into visuals:

| Table | Source | Maps |
|-------|--------|------|
| **SpellInfoTable** | client `0x49` SpellList (`ROM/118/114.DAT`) | spellId → `SpellInfo` (type, element, AoE, cast time, MP, target flags) |
| **SpellNameTable** | client string table `ROM/181/73.DAT` | spellId → display name |
| **SpellAnimationTable** | **server** (`landsandboat/SpellAnimationTable.DAT`, from LSB `spell_list.sql`) | spellId → animation index |

**Resolution** (`SpellTables.kt`, `EffectDisplayer.kt`):
```
index   = SpellAnimationTable[spellId]          # Trust spells hardcode 0xe9b
fileId  = 0xAF0 + index
datPath = FileTableManager.getFilePath(fileId)  # the merged FileTable, see zones.md
routine = load(datPath).child("main") as 0x07 EffectRoutine
```
> ⚠️ **Authoring nuance:** the `spellId → animation index` link lives in the **server
> database**, not in a client DAT. To give a *new* spell a visual you (a) author the
> animation DAT and register it in the FileTable, and (b) point the server's
> spell_list row at its index. The client tables only name/describe the spell.

### `SpellInfo` fields (`SpellListSection.kt:84-199`, block = `0x64` bytes)
`index`, `spellId`, `magicType`, `element`, `aoeType`, `targetFlags`, `castTime`,
`recastDelay`, `mpCost`, `aoeSize`.

```
MagicType   : None, WhiteMagic, BlackMagic, Summoning, Ninjutsu, Songs, BlueMagic, Geomancy, Trust
SpellElement: Fire0 Ice1 Wind2 Earth3 Lightning4 Water5 Light6 Dark7 None15
AoeType     : None0 Target1 Cone2 Source3 Line20
TargetFlag  : Self0x01 Player0x02 Party0x04 Ally0x08 Npc0x10 Enemy0x20 Corpse0x80
```

### Cast-motion FourCCs
The "charging" animation is picked by MagicType — id = `"ca" + suffix`
(`DatResource.kt:130-152`): `wh bk sm nj so bl ge fa` → e.g. White Magic = **`cawh`**,
Ninjutsu = `canj`, Trust = `cafa`. (Spell-stop uses the `sp…` family.)

---

## 3. `0x07` EffectRoutine — the sequencer / trigger layer

The routine is a **timeline of commands** that fire generators, play animations,
sounds, etc. It's what a spell/ability points at.

### Layout (`EffectRoutineParser.kt:33-93`)
SE's names (PS2 decompile, `YmScheduler`): sec1 = `init_tag`, sec2 = `idle_tag`,
sec3 = `die_tag`, totalDelay = `total_frame`. The per-tag task classes (camera `0x58`,
time `0x7C/0x7D`, weather `0x7E`, depth-of-field `0x82/0x83` …) are tabulated in
[reference/ps2_decomp_crosscheck.md](../reference/ps2_decomp_crosscheck.md) §5.
```
0x00  16  zeros
0x10  u32 sec1Offset   (+ sectionStart)   sec1 = setup/conditional state
0x14  u32 sec2Offset   (+ sectionStart)   sec2 = the timed command list  ← the meat
0x18  u32 sec3Offset   (+ sectionStart)   sec3 = on-complete (looping)
0x1C  u32 totalDelay
```

### Command stream (each entry)
```
u8   opCode
u16  unkCombo        size_dwords = max(1, unkCombo & 0x1F)   # entry length in dwords
u8   unk0
# sec2 only: next two fields are always
u16  delay           # frames to wait before this command (accumulated)
u16  duration        # frames the effect persists
... opcode-specific args (DatId refs are 4 bytes) ...
# advance by size_dwords * 4 bytes ; opCode 0x00 ends the section
```
**Entry length = `(combo & 0x1F)` dwords (minimum 1), i.e. that × 4 bytes** — not
`(combo & 0x1F) - 1`. xi walks it this way
(`xi_event.py` / `xi_schedule.py`: `n = combo & 0x1F; entry_len = max(1, n) * 4`).
(xim's Kotlin parser subtracts 1 then advances by the remainder after already
consuming the opcode dword — same on-disk stride, different framing.)

Resource references are 4-char `DatId`s, resolved through the routine's local
directory then parents (so a routine fires the `0x05`/`0x2B`/sound sections that sit
beside it in the DAT). (`EffectRoutineParser.kt:62-135`, verified against bytes.)

### Command reference (the useful subset)
Names are xim's interpretation (`EffectRoutineParser.kt` / `EffectRoutineEffects.kt`).
The full ~85-opcode `when` is in `EffectRoutineParser.kt:96-540`.

| op | command | args | does |
|----|---------|------|------|
| 0x00 | EndRoutineMarker | — | end of section |
| 0x01 | StartRoutineMarker | — | start marker |
| **0x02** | **ParticleGeneratorRoutine** | DatId | **fire a `0x05` generator** for `duration` frames |
| 0x03 | LinkedEffectRoutine | DatId | run another `0x07` on the **source** actor |
| 0x05 | SkeletonAnimationRoutine | DatId, 2×f32, transIn/out u16, maxLoop u16 | play a `0x2B` skeleton anim |
| 0x07 / 0x59 | AnimationLock | — | lock animation for `duration` |
| 0x09 | LinkedEffectRoutine (target) | DatId | run another `0x07` on the **target** |
| 0x0A / 0x0B / 0x4A / 0x53 / 0x60 | SoundEffect | DatId, i32, far/near/unk f32 | play a `0x3D` sound (`0x0A` source-pos, `0x0B` target-pos, plus player-only / nearest / global variants) |
| 0x0C / 0x0D | Model Translation / Rotation | Vec3, idx | interpolate model pos/rot over `duration` |
| 0x19 | SpellEffect | spellIndex u32 | trigger a nested spell animation |
| 0x1E | ParticleDampen | DatId | stop+fade a generator |
| 0x2D | StopParticleGenerator | DatId | stop emitting |
| 0x3B / 0x3C | LinkedEffectRoutine (blocking) | DatId | child routine that blocks the parent until done |
| 0x3D / 0x3E | RandomChild open/close | — | pick exactly one child routine between them |
| 0x3F | TransitionParticle | DatId stop, DatId start | swap one effect for another |
| 0x40-0x47 | ActorWrap texture/UV/color | DatId/float/BGRA | overlay texture on source/target actor |
| 0x52 | **TimeBasedReplay** | start, end, interval (in-game min) | weather/time-gated auto-run (sets autoRun) |
| 0x73 / 0x85 | Start / End loop | DatId | loop another routine |
| 0x64 / 0x67 / 0x6B | conditional branch / condition | regs | control flow (if/else) |

(Also: flinch 0x21/0x25, knockback 0x5E, actor fade 0x29/0x2A, weapon trace 0x2C,
jump 0x7A, ranged start/finish 0x76/0x77, visibility 0x75/0xA3, anim-mode 0x79/0x8C/0xA4/0xA5.)

### Timing
`delay` is **accumulated**: the engine subtracts each command's `delay` from a frame
counter and runs commands as the counter allows, so commands are sequenced relative
to each other. `duration` is how long that one effect lives (emit time, lock length,
interpolation length). (`EffectRoutineInstance.kt:283-300`.)

---

## 4. `0x05` ParticleGenerator — the emitter

Full deep-dive (header fields, the 4 opcode sub-sections, the `xi fx` param map) is
in [effects.md](effects.md); the complete opcode name tables live in
`src/xi/fx/xi_opcodes.py` and are inspectable via `xi fx json --opcodes`. Summary:

- **Header** — two offset frames, stated per field (`data_start = section_start + 0x10`;
  see effects.md for the full table): `attachFlags` @ **section+0x10 = data+0x00** (xim's
  `offsetFromDataStart 0`), scale amounts and environment id in the data-start frame, and
  the emission group in the **section-start** frame: `emissionVariance@sec+0x74 /
  framesPerEmission@sec+0x76 / particlesPerEmission@sec+0x78 / genFlags@sec+0x79`
  (`autoRun` = bit `0x10`; the `sec+0x76` interval is in-game A/B verified).
- **Body = four opcode sub-streams** (offset table at section-start `+0x80`):
  **sec1** generator updaters · **sec2** particle initializers (run once per particle)
  · **sec3** particle updaters (run every tick) · **sec4** expiration handlers.
  Entry = `config:u32` → `opcode = config & 0xFF`, `size = (config>>8)&0x1F` ×4 bytes,
  `alloc = config>>0xD`.

### The per-particle allocation model
`alloc` (= `config>>0xD`) is a **slot in per-particle memory**. A sec2 **initializer**
writes a value/struct into that slot (e.g. `TranslationVelocity` → a `PositionTransform`,
`KeyFrameValueSetup` → a `KeyFrameReference`); a sec3 **updater** with the same `alloc`
reads it back each frame to evolve the particle. This is how "set initial velocity" and
"integrate position each tick" cooperate. (`Particle.kt:83`, `ParticleInitializers.kt`,
`ParticleUpdaters.kt`.)

### Particle lifecycle (`Particle.kt`)
`age` → `maxAge`; `progress = age/maxAge` (0→1). Fields: `position, velocity, rotation,
scale, color (RGBA), texCoordTranslate, spriteSheetIndex, weightedMeshWeights[5]`,
`blendFunc`, and the `dynamicallyAllocated` slot map. Generators emit on the
`framesPerEmission` cadence; particles update until `age ≥ maxAge`, then sec4 expiration
handlers may fire (e.g. `EmitChild`).

### Attachment (`ParticleGeneratorAttachment.kt`, `AttachType`)
`attachFlags & 0x0F` picks how the generator is positioned; bits 4-9/10-15 pick
source/target **joints**.
```
None0x0  SourceActor0x1  TargetActor0x2  SourceToTargetBasis0x3
TargetActorSourceFacing0x4  SourceActorTargetFacing0x5  TargetToSourceBasis0x6
SourceActorWeapon0x9  ZoneActor0xA/0xB/0xC  Sun0xE  Moon0xF
```
`None` = world-positioned (ambient zone effects, and why a `None` effect transplants
cleanly). `SourceActor`/`TargetActor` = follows caster/target — the basis of spell
visuals. `Sun`/`Moon` track the sky.

### Child generators
A generator can spawn another (sec2 `0x44/0x53/0x6A`, driven by sec3 `0x33/0x46/0x25`):
the child inherits the parent's attachment/position; lifespan = parent's `maxAge`
(or infinite for `continuousSingleton`). This builds compound effects (a fire that
emits sparks that emit smoke).

---

## 5. Geometry, textures & keyframes (what a particle is made of)

A `0x05` generator's `StandardParticleSetup` (sec2 `0x01`) names a geometry resource by
`DatId` + a `linkedDataType` byte (`ParticleGeneratorSettings.kt:187-205`):

| linkedDataType | section | use |
|---|---|---|
| `0x0B` StaticMesh | **0x1F ParticleMesh** | a fixed little mesh per particle |
| `0x0E` SpriteSheet | **0x21 SpriteSheetMesh** | billboard quads with animated frames (fire, smoke) |
| `0x1D` WeightedMesh | **0x25 WeightedMesh** | morphable mesh (blend between N shape "chunks") |
| `0x24` RingMesh / `0x22` Distortion | — | procedural ring / heat-haze |

### `0x1F` ParticleMesh (`ParticleMeshSection.kt`)
```
0x10 u32 version (3/5/6)   0x14 u8 #meshes-with-tex   0x15 u8 #meshes-without
0x16 u16 numTriangles      then per-mesh tri counts, then 0x10-byte texture names
vertices: pos(3f) normal(3f) colorBGRA(u32) u(f) v(f)   = 36 bytes, 3 per triangle
```

### `0x21` SpriteSheetMesh  (xim calls the parsed type "SpriteSheet")
```
u16 unkFlag   u16 numFrames   u8 lensFlareFlag   …   0x10-byte texture name
per frame: u16=1, u8 numQuads, …, 6 verts/quad: pos(3f) colorRGBA(u32) u(f) v(f)
```
The particle's `spriteSheetIndex` selects the current frame; **sec3 `0x0D`
SpriteSheetFrameUpdater** advances it over the particle's life (frame animation).
UVs may be 0-256 and need ÷256 (a normalization flag).

### `0x25` WeightedMesh (`WeightedMeshSection.kt`)
N position/normal "chunks"; the final vertex = Σ `chunk[i] * weight[i]` (weights
normalized). sec3 `0x1E-0x22` drive `weightedMeshWeights[0..4]` → morph over life.
Packed normals: 10 bits/axis, `/512`.

### `0x20` Texture & blend modes
Textures are bound by name (resolved local→global). **BlendFunc** (sec2 `0x1E`,
deferred `0x43`) selects the GL blend: `Src_One_Add` (additive — glows/fire),
`Src_InvSrc_Add` (alpha), `Src_One_RevSub` (subtractive), `Zero_InvSrc_Add`, etc.
(`ParticleInitializers.kt:981-1057`). xi reads/writes `0x20` via
[`xi tex`](../dats/tex.md).

### `0x19` ParticleKeyFrameData (`ParticleKeyFrameSection.kt`)
A **curve**: a list of `(time:f32, value:f32)` pairs, `time` normalized 0→1,
terminated when `time == 1.0`. Sampled by `lerp` at the particle's progress.
The `KeyFrameValueSetup` opcodes (sec2 `0x39`, `0x68`, `0x6C-0x70`, `0x74-0x97`)
store a `KeyFrameReference {keyFrameId, numCycles}` in a particle slot; the matching
sec3 `ProgressValueUpdater` samples `curve(progress * numCycles mod 1)` each frame and
writes it to the target field (scale, color, UV, rotation, point-light, …). `Clock*`
variants sample by **time-of-day** instead of particle progress (day/night effects).

---

## 6. Triggering & autoRun

On zone load the engine walks the `effe`/`mode` directories and registers
(`Scene.kt:109-131`):
- every `0x05` whose **`autoRun`** bit (`genFlags 0x10`) is set, and
- every `0x07` whose **`autoRunHeuristic`** is set.

`autoRunHeuristic` is set when (`DatParser.kt:175-192`, `EffectRoutineParser.kt`):
- a directory closes containing **exactly one** `0x07` routine that sits in `effe`
  (or one level under it) — ambient single-routine effects; or
- the routine has a **`0x52` TimeBasedReplay** command (weather/time gating); or
- its **sec3 has opcode `0x01`** (loop-on-complete).

Everything else is **server/spell-triggered**: nothing auto-fires; the routine waits
to be invoked by a cast/ability via the spell-resolution chain in §2.

---

## 7. Authoring a new spell-effect (the practical chain)

To add a brand-new spell visual, you touch both the **client DATs** and the **server**:

1. **Geometry** — author a `0x21` SpriteSheetMesh (billboard, for fire/glows) or `0x1F`
   ParticleMesh, with its `0x20` texture(s). (xi: `xi tex` round-trips textures;
   mesh import is partial — see [effects.md](effects.md).)
2. **Curves** — `0x19` keyframe curves for scale/color/alpha fade over life.
3. **Generator** — a `0x05` ParticleGenerator: set attach type (`SourceActor`/
   `TargetActor` for a spell), emission rate/count, the sec2 initializers (color, scale,
   velocity, sprite-sheet, blend mode) and sec3 updaters (the keyframe-driven evolution).
   Reuse an existing one as a template with `xi fx copy`/`fx set`/`fx json --opcodes`.
4. **Routine** — a `0x07` EffectRoutine `main` that fires the generator(s) with
   `0x02 ParticleGeneratorRoutine`, plus any `0x2B` caster motion, `0x3D` sound, etc.,
   sequenced by delay/duration.
5. **Package** — put all the above in one DAT under `data → effe`, register the DAT in
   the **FileTable** at a `fileId` (`0xAF0 + index`), so the client can load it.
6. **Server** — point the spell's `spell_list` row (LSB) at that animation `index`, so
   `SpellAnimationTable[spellId]` resolves to your DAT.

**What xi can do today:** inspect/dump/edit/copy/export `0x05` effects (`xi fx`),
round-trip `0x20` textures (`xi tex`), transplant effects with all deps across
DATs, and resolve **spell → animation DAT** (`xi.spell` / `spell_catalog` /
`resolve_spell_dat_rel` — see [spells.md](spells.md)). **Not yet:** authoring `0x07`
routines from scratch, FileTable registration, or mesh re-encode — those are the next
tooling targets.

---

## References (xim source)

| Topic | File |
|-------|------|
| Section format / DatId / directory tree | `resource/DatParser.kt`, `resource/DatResource.kt` |
| Spell tables & resolution | `resource/table/SpellTables.kt`, `resource/SpellListSection.kt`, `poc/game/EffectDisplayer.kt` |
| EffectRoutine | `resource/EffectRoutineParser.kt`, `EffectRoutineEffects.kt`, `EffectRoutineInstance.kt` |
| ParticleGenerator | `resource/ParticleGeneratorParser.kt`, `ParticleGenerator.kt`, `Particle.kt`, `ParticleGeneratorSettings.kt`, `ParticleGeneratorAttachment.kt` |
| Initializers / updaters | `resource/ParticleInitializers.kt`, `ParticleUpdaters.kt`, `ParticleGeneratorUpdaters.kt` |
| Geometry / textures / keyframes | `resource/ParticleMeshSection.kt`, `SpriteSheetSection.kt`, `WeightedMeshSection.kt`, `ParticleKeyFrameSection.kt` |
| Zone load / autoRun | `poc/Scene.kt`, `resource/DatParser.kt` |
| FileTable / names | `resource/table/FTable.kt`, `ZoneTables.kt` — see [../zone/zones.md](../zone/zones.md) |

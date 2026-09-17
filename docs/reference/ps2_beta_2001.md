# PS2 beta disc (SLPM 621.35, November 2001) — what it adds

The FINAL FANTASY XI β PlayStation 2 disc (build `20011120_0`) boots `SLPM_621.35`, an
**unstripped** Metrowerks ELF: 18,308 symbols and DWARF 1 debug info (1,245 struct/class
layouts, **107 enums**, 5,763 prototypes). It is the same engine as the 2003 client in
[ps2_decomp_crosscheck.md](ps2_decomp_crosscheck.md), 18 months earlier. It closes two gaps
listed in that doc's §11:

- **Enums.** `_types.h` is missing from the 2003 checkout. Here the enums come straight
  from DWARF, including `RES_TYPE` and the effect generator / scheduler opcode enums.
- **String literals.** This is the binary itself, not a decompile, so every string is
  still there.

Tools and raw material live in the separate `xi-beta-viewer` repo (`D:\xi-beta-viewer`):

- the disc/DAT viewer and `scripts/xibeta.mjs`;
- `research/dwarf/dwarf1.py`, which regenerates `types.h` / `functions.txt` /
  `symbols.tsv` from the ELF (the outputs stay local);
- `docs/FORMATS.md`, covering the disc, install packs, textures, characters (§11) and
  zones (§12).

Names below are Square Enix's, copied from those enums. The **2001 numbers agree with the
retail PC client** wherever both sides name a code (evidence with each table). Retail
added codes after the 2001 enums end, so absence past a table's end means nothing.

---

## 1. Section types — `YmResourceHeader::RES_TYPE`

`main/miyagawa/ymres.cpp`. Every code [dat_sections.md](dat_sections.md) names lines up:

- `0x05` Generater, `0x07` Scheduler, `0x19` KeyFrame
- `0x1C` **Mzb**, `0x2E` **Mmb** (the zone placement / zone mesh formats)
- `0x1F`/`0x20`/`0x21`/`0x25` **D3m / D3s / D3a / D3b** (the Direct3D particle mesh,
  surface = texture, sprite-sheet mesh and weighted mesh)
- `0x29`/`0x2A`/`0x2B` **Sk2 / Os2 / Mo2** (the "version 2" skeleton / mesh / motion;
  the PS2 build uses `Skd`/`Osm`/`Mod` at `0x12`/`0x11`/`0x17`)
- `0x2F` Weather, `0x36` Rid, `0x3D` Sep, `0x3E` Vtx, `0x4A` Sph
- `0x49` **Mgb**, `0x53` **Acb** (magic and ability tables; `0x48` Mgd and `0x52` Acd
  sit beside them)

The enum stops at `0x53` (`Max` = 84). `0x54` WeaponTrace, `0x5D` and `0x5E` are later
additions: the 2003 client has `YmAfbRes` at `0x54`.

The enum puts `Terminate` at `0` and `Rmp` (resource map, the directory) at `1`. The
2003 map's `0x01 YmTerminateRes` row is probably the handler for type 0.


### Codes and names

| code | SE `RES_TYPE` | dat_sections.md | code | SE `RES_TYPE` | dat_sections.md |
|---|---|---|---|---|---|
| `0x00` | Terminate | End | `0x2A` | Os2 | SkeletonMesh |
| `0x01` | Rmp | Directory | `0x2B` | Mo2 | SkeletonAnimation |
| `0x02` | Rmw |  | `0x2C` | Psw |  |
| `0x03` | Directory |  | `0x2D` | Wsd |  |
| `0x04` | Bin | Table | `0x2E` | Mmb | ZoneMesh |
| `0x05` | Generater | ParticleGenerator | `0x2F` | Weather | Environment |
| `0x06` | Camera | Route | `0x30` | Meb | UiMenu |
| `0x07` | Scheduler | EffectRoutine | `0x31` | Msb | UiElementGroup |
| `0x08` | Mtx |  | `0x32` | Med |  |
| `0x09` | Tim |  | `0x33` | Msh |  |
| `0x0A` | TexInfo |  | `0x34` | Ysh |  |
| `0x0B` | Vum |  | `0x35` | Mbp |  |
| `0x0C` | Om1 |  | `0x36` | Rid | ZoneInteractions |
| `0x0D` | FileInfo |  | `0x37` | Wd |  |
| `0x0E` | Anm |  | `0x38` | Bgm |  |
| `0x0F` | Rsd |  | `0x39` | Lfd |  |
| `0x10` | UnKnown |  | `0x3A` | Lfe |  |
| `0x11` | Osm |  | `0x3B` | Esh |  |
| `0x12` | Skd |  | `0x3C` | Sch |  |
| `0x13` | Mtd |  | `0x3D` | Sep | SoundEffectPointer |
| `0x14` | Mld |  | `0x3E` | Vtx | PointList |
| `0x15` | Mlt |  | `0x3F` | Lwo |  |
| `0x16` | Mws |  | `0x40` | Rme |  |
| `0x17` | Mod |  | `0x41` | Elt |  |
| `0x18` | Tim2 |  | `0x42` | Rab |  |
| `0x19` | KeyFrame | ParticleKeyFrameData | `0x43` | Mtt |  |
| `0x1A` | Bmp |  | `0x44` | Mtb |  |
| `0x1B` | Bmp2 |  | `0x45` | Cib | Info |
| `0x1C` | Mzb | ZoneDef | `0x46` | Tlt | (unknown) |
| `0x1D` | Mmd |  | `0x47` | PointLightProg |  |
| `0x1E` | Mep |  | `0x48` | Mgd |  |
| `0x1F` | D3m | ParticleMesh | `0x49` | Mgb | SpellList |
| `0x20` | D3s | Texture | `0x4A` | Sph | Path |
| `0x21` | D3a | SpriteSheetMesh | `0x4B` | Bmd |  |
| `0x22` | DistProg |  | `0x4C` | Qif |  |
| `0x23` | VuLineProg |  | `0x4D` | Qdt |  |
| `0x24` | RingProg |  | `0x4E` | Mif |  |
| `0x25` | D3b | WeightedMesh | `0x4F` | Mdt |  |
| `0x26` | Asn |  | `0x50` | Sif |  |
| `0x27` | Mot |  | `0x51` | Sdt |  |
| `0x28` | Skl |  | `0x52` | Acd |  |
| `0x29` | Sk2 | Skeleton | `0x53` | Acb | AbilityList |

---

## 2. `0x05` generator opcodes — SE names

`main/miyagawa/effect/ymgenerater.cpp` declares one enum per sub-section.
[src/xi/fx/xi_opcodes.py](../../src/xi/fx/xi_opcodes.py) walks the same four streams:
`GenerateCode` = sec1, `InitCode` = sec2, `IdleCode` = sec3, `DieCode` = sec4.

The numbering is shared. Wherever xim and SE both name a code, they describe the same
thing at the same number:

| Section | Code | xim / xi_opcodes.py | SE enum |
|---|---|---|---|
| sec1 | `0x0A` | GeneratorCull | `GenerateRangeGClip` |
| sec1 | `0x11` | Association | `GenerateTracking` |
| sec2 | `0x1D` | SpriteSheet | `InitShapeAnimate` |
| sec2 | `0x1E` | BlendFunc | `InitAlphaMode` |
| sec2 | `0x30` | DepthBias | `InitZOffset` |
| sec2 | `0x32` | HazeOffset | `InitDistElemParam` |
| sec2 | `0x3A` | RingMesh | `InitRingElemParam` |
| sec2 | `0x45`–`0x4A` | Parent* | `InitInheritance*` |
| sec2 | `0x58` | PointLightParams | `InitPointLightElemParam` |
| sec2 | `0x72` | ProjectionBias | `InitZOffsetFit` |
| sec3 | `0x0D` | SpriteSheetFrame | `IdleShapeAnimate` |
| sec3 | `0x2C` | VelocityDampener | `IdleAccele` |
| sec3 | `0x45` | MoonPhaseSprite | `IdleMoonShape` |
| sec4 | `0x05` | RepeatExpiration | `DieNeverLife` |

Things to take from the tables:

- **"fills a gap"** marks 70 codes xim could not name, among them sec1 `0x15`
  (`GenerateViewGClip`) and sec2 `0x2D` / `0x63`, the two the `xi_opcodes.py` comment
  singles out.
- **Families.** `CorrectKey*` codes are what xim calls `KF.*` / `Prog.*`, and
  `CorrectKeyTime*` codes are what it calls `Clock.*`. Going by xim's names, those are
  keyframe values over the particle's life and over the time of day.
- **Suffix pattern.** The names form regular families: `Pos` / `Rot` / `Scale` / `Rgb`
  each with `V_W`, `RV_W`, `F_W`, `RF_W` variants. Many of the gaps (`InitPosF_W`,
  `InitRotF_W`, …) are family members whose neighbours xim already names.
- **Disagreements, left for byte checks.**
  - sec2 `0x31`: xim `RandomVelocity`, SE `InitScaleRVA`.
  - sec3 `0x4E`: xim `DayOfWeekColor`, SE (2001) `IdleMoonColor`.
  - Where one name is specific and the other generic (sec3 `0x03`/`0x06`/`0x09` all
    `VelocityAccel` in xim, SE `IdlePosG_W` / `IdleRotG_W` / `IdleScaleG_W`), SE's
    distinguishes the three.
- **Tool-side list.** `YmCode` in `ymgeneratercode.cpp` is a flat 164-entry list of the
  same operations. It looks like the effect tool's unified list: the DAT sub-sections
  number by the per-phase enums above, not by `YmCode`. It is in `types.h` if a single
  name set is ever wanted.

#### sec1: generator updaters = `GenerateCode`

| code | SE name (2001) | xi_opcodes.py | |
|---|---|---|---|
| `0x01` | GenerateRandomInterval |  | fills a gap |
| `0x02` | GenerateRandomGenerateNum |  | fills a gap |
| `0x03` | GenerateCorrectKeyLife |  | fills a gap |
| `0x04` | GenerateCorrectKeyGenerateNum | EmissionFrequency |  |
| `0x05` | GenerateCorrectKeyExplodePosV_W | RelativeVelocity |  |
| `0x06` | GenerateCorrectKeyBallRangeDX2_data | SphericalRadius |  |
| `0x07` | GenerateCorrectKeyBallRangeDX2_min | SphericalRadiusVariance |  |
| `0x08` | GenerateCorrectKeyBallRangeDX2_RotZ | Gen.RotationZ |  |
| `0x09` | GenerateCorrectKeyBallRangeDX2_RotY | Gen.RotationY |  |
| `0x0A` | GenerateRangeGClip | GeneratorCull |  |
| `0x0B` | GenerateCorrectKeyGeneratePosX | Gen.BasePosX |  |
| `0x0C` | GenerateCorrectKeyGeneratePosY | Gen.BasePosY |  |
| `0x0D` | GenerateCorrectKeyGeneratePosZ | Gen.BasePosZ |  |
| `0x0E` | GenerateCorrectKeyRollingX | Gen.RotationX |  |
| `0x0F` | GenerateCorrectKeyRollingY | Gen.RotationY |  |
| `0x10` | GenerateCorrectKeyRollingZ | Gen.RotationZ |  |
| `0x11` | GenerateTracking | Association |  |
| `0x12` | GenerateCorrectKeyGeneratePosVX | Gen.VelocityX |  |
| `0x13` | GenerateCorrectKeyGeneratePosVY | Gen.VelocityY |  |
| `0x14` | GenerateCorrectKeyGeneratePosVZ | Gen.VelocityZ |  |
| `0x15` | GenerateViewGClip |  | fills a gap |
| `0x16` | GenerateJunction |  | fills a gap |

#### sec2: particle initializers = `InitCode`

| code | SE name (2001) | xi_opcodes.py | |
|---|---|---|---|
| `0x01` | InitLife | StandardSetup |  |
| `0x02` | InitPosV_W | TranslationVelocity |  |
| `0x03` | InitPosRV_W | PosVelVariance |  |
| `0x04` | InitPosF_W |  | fills a gap |
| `0x05` | InitPosRF_W |  | fills a gap |
| `0x06` | InitBallRange | SphPosVarSimple |  |
| `0x07` | InitBallRangeDX | SphPosVarMedium |  |
| `0x08` | InitExplodePosV_W | RelativeVelocity |  |
| `0x09` | InitRot | Rotation |  |
| `0x0A` | InitRotR | RotationVariance |  |
| `0x0B` | InitRotV_W | RotationVelocity |  |
| `0x0C` | InitRotRV_W | RotVelVariance |  |
| `0x0D` | InitRotF_W |  | fills a gap |
| `0x0E` | InitRotRF_W |  | fills a gap |
| `0x0F` | InitScale | Scale |  |
| `0x10` | InitScaleR | ScaleVariance |  |
| `0x11` | InitScaleRA | SingleScaleVariance |  |
| `0x12` | InitScaleV_W | ScaleVelocity |  |
| `0x13` | InitScaleRV_W | ScaleVelVariance |  |
| `0x14` | InitScaleF_W |  | fills a gap |
| `0x15` | InitScaleRF_W |  | fills a gap |
| `0x16` | InitRgb | Color |  |
| `0x17` | InitRgbR | ColorVariance |  |
| `0x18` | InitRgbRA | UniformColorVariance |  |
| `0x19` | InitRgbV_W | ColorTransform |  |
| `0x1A` | InitRgbRV_W | ColorTransformVariance |  |
| `0x1B` | InitRgbF_W |  | fills a gap |
| `0x1C` | InitRgbRF_W |  | fills a gap |
| `0x1D` | InitShapeAnimate | SpriteSheet |  |
| `0x1E` | InitAlphaMode | BlendFunc |  |
| `0x1F` | InitBallRangeDX2 | SphPosVarFull |  |
| `0x20` | InitDougnutRange |  | fills a gap |
| `0x21` | InitCorrectKeyPosX |  | fills a gap |
| `0x22` | InitCorrectKeyPosY |  | fills a gap |
| `0x23` | InitCorrectKeyPosZ |  | fills a gap |
| `0x24` | InitCorrectKeyRotX |  | fills a gap |
| `0x25` | InitCorrectKeyRotY |  | fills a gap |
| `0x26` | InitCorrectKeyRotZ |  | fills a gap |
| `0x27` | InitCorrectKeyScaleX |  | fills a gap |
| `0x28` | InitCorrectKeyScaleY |  | fills a gap |
| `0x29` | InitCorrectKeyScaleZ |  | fills a gap |
| `0x2A` | InitCorrectKeyRgbR |  | fills a gap |
| `0x2B` | InitCorrectKeyRgbG |  | fills a gap |
| `0x2C` | InitCorrectKeyRgbB |  | fills a gap |
| `0x2D` | InitCorrectKeyRgbA |  | fills a gap |
| `0x2E` | InitCorrectKeyUScroll |  | fills a gap |
| `0x2F` | InitCorrectKeyVScroll |  | fills a gap |
| `0x30` | InitZOffset | DepthBias |  |
| `0x31` | InitScaleRVA | RandomVelocity |  |
| `0x32` | InitDistElemParam | HazeOffset |  |
| `0x33` | InitCorrectKeyMorphBlend0 |  | fills a gap |
| `0x34` | InitCorrectKeyMorphBlend1 |  | fills a gap |
| `0x35` | InitCorrectKeyMorphBlend2 |  | fills a gap |
| `0x36` | InitCorrectKeyMorphBlend3 |  | fills a gap |
| `0x37` | InitCorrectKeyMorphBlend4 |  | fills a gap |
| `0x38` | InitCorrectKeyDistSize |  | fills a gap |
| `0x39` | InitCorrectKeyDistAmount | KeyFrameValue |  |
| `0x3A` | InitRingElemParam | RingMesh |  |
| `0x3B` | InitCycleRot | IncrementalRotation |  |
| `0x3C` | InitBornSync | OnceChildGenerator |  |
| `0x3D` | InitTurbulence | Oscillation |  |
| `0x3E` | InitTurbulenceX | OscAccelX |  |
| `0x3F` | InitTurbulenceY | OscAccelY |  |
| `0x40` | InitTurbulenceZ | OscAccelZ |  |
| `0x41` | InitExplodeR | RelVelVariance |  |
| `0x42` | InitFitGround | GroundProjection |  |
| `0x43` | InitLightMapElemParam | DeferredBlendFunc |  |
| `0x44` | InitMoveSync | ChildGenerator |  |
| `0x45` | InitInheritancePos | ParentPositionCopy |  |
| `0x46` | InitInheritancePosV | ParentVelocity |  |
| `0x47` | InitInheritanceRot | ParentRotate |  |
| `0x48` | InitInheritanceRgb | ParentColor |  |
| `0x49` | InitInheritanceScale | ParentScale |  |
| `0x4A` | InitInheritanceUV | ParentTexCoord |  |
| `0x4B` | InitLfdElemParam |  | fills a gap |
| `0x4C` | InitSoundElemParam | AudioRange |  |
| `0x4D` | InitBornSound |  | fills a gap |
| `0x4E` | InitVertexPos | FixedPointPosVar |  |
| `0x4F` | InitOrderVertexPos | FixedPointPosVar |  |
| `0x50` | InitCorrectKeyPosVX |  | fills a gap |
| `0x51` | InitCorrectKeyPosVY |  | fills a gap |
| `0x52` | InitCorrectKeyPosVZ |  | fills a gap |
| `0x53` | InitShaftMoveSync | ChildGenerator |  |
| `0x54` | InitPathLineMove | PointListPosition |  |
| `0x55` | InitSpecularElemParam | SpecularParams |  |
| `0x56` | InitVuParticleElemCorrect | Batching |  |
| `0x57` | InitVuLineElemParam |  | fills a gap |
| `0x58` | InitPointLightElemParam | PointLightParams |  |
| `0x59` | InitCorrectKeySpeLightRotX |  | fills a gap |
| `0x5A` | InitCorrectKeySpeLightRotY |  | fills a gap |
| `0x5B` | InitCorrectKeySpeLightRotZ |  | fills a gap |
| `0x5C` | InitCorrectKeySpeLightRgbR |  | fills a gap |
| `0x5D` | InitCorrectKeySpeLightRgbG |  | fills a gap |
| `0x5E` | InitCorrectKeySpeLightRgbB |  | fills a gap |
| `0x5F` | InitCorrectKeySpeLightRgbA |  | fills a gap |
| `0x60` | InitCorrectKeyTimeRgbR |  | fills a gap |
| `0x61` | InitCorrectKeyTimeRgbG |  | fills a gap |
| `0x62` | InitCorrectKeyTimeRgbB |  | fills a gap |
| `0x63` | InitCorrectKeyTimeRgbA |  | fills a gap |
| `0x64` | InitCorrectKeyTimeScaleX |  | fills a gap |
| `0x65` | InitCorrectKeyTimeScaleY |  | fills a gap |
| `0x66` | InitCorrectKeyTimeScaleZ |  | fills a gap |
| `0x67` | InitReverseMove | ReverseDisplacement |  |
| `0x68` | InitCorrectKeyTimeVolume | KF.ToDVolume |  |
| `0x69` | InitCorrectKeyAccele | KF.VelocityDampener |  |
| `0x6A` | InitBillboardShaftMoveSync | ChildGenerator |  |
| `0x6B` | InitPathSound | PathReference |  |
| `0x6C` | InitCorrectKeyTimeLightPower | KF.PointLightParams |  |
| `0x6D` | InitCorrectKeyTimeSpeLightRgbR | KF.ToDSpecColor |  |
| `0x6E` | InitCorrectKeyTimeSpeLightRgbG | KF.ToDSpecColor |  |
| `0x6F` | InitCorrectKeyTimeSpeLightRgbB | KF.ToDSpecColor |  |
| `0x70` | InitCorrectKeyTimeSpeLightRgbA | KF.ToDSpecColor |  |
| `0x71` | InitJunction |  | fills a gap |
| `0x72` | InitZOffsetFit | ProjectionBias |  |
| `0x74` |  | KF.UVVelX | after 2001 |
| `0x75` |  | KF.UVVelY | after 2001 |
| `0x76` |  | KF.RotVelX | after 2001 |
| `0x77` |  | KF.RotVelY | after 2001 |
| `0x78` |  | KF.RotVelZ | after 2001 |
| `0x79` |  | ParentRotate | after 2001 |
| `0x7B` |  | ProgressPositionOffset | after 2001 |
| `0x7C` |  | KF.PLTheta | after 2001 |
| `0x7D` |  | KF.PLRange | after 2001 |
| `0x7E` |  | ParentTheta | after 2001 |
| `0x7F` |  | ParentRange | after 2001 |
| `0x80` |  | KF.PLThetaMul | after 2001 |
| `0x81` |  | KF.PLRangeMul | after 2001 |
| `0x83` |  | KF.ToDRotVelX | after 2001 |
| `0x84` |  | KF.ToDRotVelY | after 2001 |
| `0x85` |  | KF.ToDRotVelZ | after 2001 |
| `0x88` |  | PointLightAttachment | after 2001 |
| `0x8B` |  | KF.ToDRotX | after 2001 |
| `0x8C` |  | KF.ToDRotY | after 2001 |
| `0x8D` |  | KF.ToDRotZ | after 2001 |
| `0x8E` |  | FootMark | after 2001 |
| `0x90` |  | DaylightColorAdjuster | after 2001 |
| `0x91` |  | DaylightColorSetup | after 2001 |
| `0x95` |  | KF.ToDPosX | after 2001 |
| `0x96` |  | KF.ToDPosY | after 2001 |
| `0x97` |  | KF.ToDPosZ | after 2001 |
| `0x9B` |  | ParentPositionSnapshot | after 2001 |

#### sec3: particle updaters = `IdleCode` (generater)

| code | SE name (2001) | xi_opcodes.py | |
|---|---|---|---|
| `0x01` | IdlePosV |  | fills a gap |
| `0x02` | IdlePosV_W | Position |  |
| `0x03` | IdlePosG_W | VelocityAccel |  |
| `0x04` | IdleRotV |  | fills a gap |
| `0x05` | IdleRotV_W | Rotation |  |
| `0x06` | IdleRotG_W | VelocityAccel |  |
| `0x07` | IdleScaleV |  | fills a gap |
| `0x08` | IdleScaleV_W | Scale |  |
| `0x09` | IdleScaleG_W | VelocityAccel |  |
| `0x0A` | IdleRgbV |  | fills a gap |
| `0x0B` | IdleRgbV_W | ColorTransformApplier |  |
| `0x0C` | IdleRgbG_W | ColorTransformModifier |  |
| `0x0D` | IdleShapeAnimate | SpriteSheetFrame |  |
| `0x0E` | IdleKeyFrameCorrect | AgeAdvance |  |
| `0x0F` | IdleCorrectKeyPosX | Prog.PosX |  |
| `0x10` | IdleCorrectKeyPosY | Prog.PosY |  |
| `0x11` | IdleCorrectKeyPosZ | Prog.PosZ |  |
| `0x12` | IdleCorrectKeyRotX | Prog.RotX |  |
| `0x13` | IdleCorrectKeyRotY | Prog.RotY |  |
| `0x14` | IdleCorrectKeyRotZ | Prog.RotZ |  |
| `0x15` | IdleCorrectKeyScaleX | Prog.ScaleX |  |
| `0x16` | IdleCorrectKeyScaleY | Prog.ScaleY |  |
| `0x17` | IdleCorrectKeyScaleZ | Prog.ScaleZ |  |
| `0x18` | IdleCorrectKeyRgbR | Prog.ColorR |  |
| `0x19` | IdleCorrectKeyRgbG | Prog.ColorG |  |
| `0x1A` | IdleCorrectKeyRgbB | Prog.ColorB |  |
| `0x1B` | IdleCorrectKeyRgbA | Prog.ColorA |  |
| `0x1C` | IdleCorrectKeyUScroll | Prog.TexU |  |
| `0x1D` | IdleCorrectKeyVScroll | Prog.TexV |  |
| `0x1E` | IdleCorrectKeyMorphBlend0 | Prog.WeightMesh0 |  |
| `0x1F` | IdleCorrectKeyMorphBlend1 | Prog.WeightMesh1 |  |
| `0x20` | IdleCorrectKeyMorphBlend2 | Prog.WeightMesh2 |  |
| `0x21` | IdleCorrectKeyMorphBlend3 | Prog.WeightMesh3 |  |
| `0x22` | IdleCorrectKeyMorphBlend4 | Prog.WeightMesh4 |  |
| `0x23` | IdleCorrectKeyDistSize |  | fills a gap |
| `0x24` | IdleCorrectKeyDistAmount | Prog.HazeOffsetX |  |
| `0x25` | IdleMoveSync | ChildGeneratorBasic |  |
| `0x26` | IdleTurn | VelocityRotator |  |
| `0x27` | IdleUScroll | TexCoordU |  |
| `0x28` | IdleVScroll | TexCoordV |  |
| `0x29` | IdleTurbulenceX | OscillationX |  |
| `0x2A` | IdleTurbulenceY | OscillationY |  |
| `0x2B` | IdleTurbulenceZ | OscillationZ |  |
| `0x2C` | IdleAccele | VelocityDampener |  |
| `0x2D` | IdleViewClip |  | fills a gap |
| `0x2E` | IdleRangeAlphaClip | DrawDistance |  |
| `0x2F` | IdleMoveAhead | VelocityRotation |  |
| `0x30` | IdleCorrectKeyPosVX | Prog.VelX |  |
| `0x31` | IdleCorrectKeyPosVY | Prog.VelY |  |
| `0x32` | IdleCorrectKeyPosVZ | Prog.VelZ |  |
| `0x33` | IdleShaftMoveSync | ChildGenerator |  |
| `0x34` | IdlePathLineMove | PointListPosition |  |
| `0x35` | IdleCorrectKeySpeLightRotX | Prog.SpecRotX |  |
| `0x36` | IdleCorrectKeySpeLightRotY | Prog.SpecRotY |  |
| `0x37` | IdleCorrectKeySpeLightRotZ | Prog.SpecRotZ |  |
| `0x38` | IdleCorrectKeySpeLightRgbR | Prog.SpecColorR |  |
| `0x39` | IdleCorrectKeySpeLightRgbG | Prog.SpecColorG |  |
| `0x3A` | IdleCorrectKeySpeLightRgbB | Prog.SpecColorB |  |
| `0x3B` | IdleCorrectKeySpeLightRgbA | Prog.SpecColorA |  |
| `0x3C` | IdleCorrectKeyTimeRgbR | Clock.ColorR |  |
| `0x3D` | IdleCorrectKeyTimeRgbG | Clock.ColorG |  |
| `0x3E` | IdleCorrectKeyTimeRgbB | Clock.ColorB |  |
| `0x3F` | IdleCorrectKeyTimeRgbA | Clock.AlphaMul |  |
| `0x40` | IdleCorrectKeyTimeScaleX | Clock.ScaleX |  |
| `0x41` | IdleCorrectKeyTimeScaleY | Clock.ScaleY |  |
| `0x42` | IdleCorrectKeyTimeScaleZ | Clock.ScaleZ |  |
| `0x43` | IdleCorrectKeyTimeVolume | Clock.Volume |  |
| `0x44` | IdleCorrectKeyAccele | Prog.Dampening |  |
| `0x45` | IdleMoonShape | MoonPhaseSprite |  |
| `0x46` | IdleBillboardShaftMoveSync | ChildGenerator |  |
| `0x47` | IdleTextureAnimate |  | fills a gap |
| `0x48` | IdleRangeAlphaClip2 | DoubleRangeDrawDistance |  |
| `0x49` | IdleCorrectKeyTimeLightPower | Clock.PLTheta |  |
| `0x4A` | IdleCorrectKeyTimeSpeLightRgbR | Clock.SpecColorR |  |
| `0x4B` | IdleCorrectKeyTimeSpeLightRgbG | Clock.SpecColorG |  |
| `0x4C` | IdleCorrectKeyTimeSpeLightRgbB | Clock.SpecColorB |  |
| `0x4D` | IdleCorrectKeyTimeSpeLightRgbA | Clock.SpecColorA |  |
| `0x4E` | IdleMoonColor | DayOfWeekColor |  |
| `0x4F` | IdleMoonShapeColor | MoonPhaseColor |  |
| `0x50` | IdleWeatherAlpha |  | fills a gap |
| `0x51` | IdleJunction |  | fills a gap |
| `0x52` | IdleGenerateNop |  | fills a gap |
| `0x53` |  | Occlusion | after 2001 |
| `0x54` |  | TexU.Scroll | after 2001 |
| `0x55` |  | TexV.Scroll | after 2001 |
| `0x56` |  | Rot.X.Add | after 2001 |
| `0x57` |  | Rot.Y.Add | after 2001 |
| `0x58` |  | Rot.Z.Add | after 2001 |
| `0x59` |  | AngularDistanceRot | after 2001 |
| `0x5B` |  | Prog.PLTheta | after 2001 |
| `0x5C` |  | Prog.PLRange | after 2001 |
| `0x5D` |  | Prog.PLThetaMult | after 2001 |
| `0x5E` |  | Prog.PLRangeMult | after 2001 |
| `0x5F` |  | CameraShake | after 2001 |
| `0x60` |  | ScreenFlash | after 2001 |
| `0x61` |  | ClockRot.X | after 2001 |
| `0x62` |  | ClockRot.Y | after 2001 |
| `0x63` |  | ClockRot.Z | after 2001 |
| `0x66` |  | Clock.RotX | after 2001 |
| `0x67` |  | Clock.RotY | after 2001 |
| `0x68` |  | Clock.RotZ | after 2001 |
| `0x69` |  | DaylightColorApplier | after 2001 |
| `0x6B` |  | Clock.PosX | after 2001 |
| `0x6C` |  | Clock.PosY | after 2001 |
| `0x6D` |  | Clock.PosZ | after 2001 |
| `0x6E` |  | DoubleRangeWeightedMesh | after 2001 |

#### sec4: expiration = `DieCode` (generater)

| code | SE name (2001) | xi_opcodes.py | |
|---|---|---|---|
| `0x01` | DieReborn | EmitChild |  |
| `0x02` | DieKickScheduler |  | fills a gap |
| `0x03` | DieSound |  | fills a gap |
| `0x04` | DiePathLineMove |  | fills a gap |
| `0x05` | DieNeverLife | RepeatExpiration |  |
| `0x06` | DieJunction |  | fills a gap |

---

## 3. `0x07` scheduler tags — SE names

The scheduler's codes are a second enum, merged into the same DWARF names:

- `IdleCode` 1–107 for the idle tags;
- `DieCode` `DieLoopScheduler = 1`;
- `InitCode`, which has no scheduler codes (`SchedulerInitCodeMax = 1`).

Where [ps2_decomp_crosscheck.md §5](ps2_decomp_crosscheck.md#5-scheduler-0x07--ymscheduler--ymschedulertaskexecutetag)
has a task for a tag, the SE name fits it. The loosest fit is `0x17`/`0x18`.

| Tag | 2003 task | SE enum |
|---|---|---|
| `0x02` | fire generator | `IdleKickGenerater` |
| `0x07`/`0x08` | BondageActor | `IdleBondageCaster`/`Target` |
| `0x0C`/`0x0D` | door slide / door angle | `IdleDoorSlide` / `IdleDoorOpen` |
| `0x16`, `0x23` | spawn `XiDollActor` | `IdleTargetFreeze` / `IdleTrackingTargetFreeze` |
| `0x17`/`0x18` | set caster/target | `IdleCasterUnfreeze` / `IdleTargetUnfreeze` |
| `0x1A`–`0x1C` | FindActor | `IdleDirectLoadChara` / `IdleDirectCaster` / `IdleDirectTarget` |
| `0x24` | Suspend on result | `IdleAtkSchSelect` |
| `0x2C` | KzAfterImage | `IdleAfterImage` |
| `0x3B`/`0x3C` | blocking child | `IdleGosub` / `IdleCasterGosub` |
| `0x3F` | transition generator | `IdleKillRebornGenerater` |
| `0x42`–`0x45` | EffectScrollActor | `Idle{Caster,Target}Effect{U,V}Scroll` |
| `0x53` | target-relative sound | `IdleNearTargetSound` |
| `0x55` | EffectTextureActor | `IdleTargetEffectTexture2` |
| `0x59` | LockCasterMagic | `IdleLockCasterMagic` |

Against the xim labels in [../fx/effect_system.md](../fx/effect_system.md):

- xim calls both `0x07` and `0x59` "AnimationLock". SE separates them: `0x07`
  `IdleBondageCaster` (the 2003 table also has BondageActor there) and `0x59`
  `IdleLockCasterMagic`.
- xim `0x19` "SpellEffect" is `IdleDirectLoadMagic`, which fits.

Apart from the conditionals below, the 2001 list stops at `0x5C`. Camera `0x58`, time
`0x7C`/`0x7D`, weather `0x7E` and depth of field `0x82`/`0x83` are later.

The structured-control tags already exist in 2001, at `0x64`–`0x6B`: `IdleIf`, `IdleOr`,
`IdleThen`, `IdleElse`, `IdleEndif`, `IdleNestIn`, `IdleNestOut`, `IdleInstruction`. If
retail kept those numbers, its conditional routines decode there. That is worth
checking against retail `0x07` data.

| tag | SE name (2001) | tag | SE name (2001) |
|---|---|---|---|
| `0x01` | IdleInit | `0x30` | IdleKickSchedulerAllTarget |
| `0x02` | IdleKickGenerater | `0x31` | IdleLoopTarget |
| `0x03` | IdleKickScheduler | `0x32` | IdleNextTarget |
| `0x04` | IdleKickCamera | `0x39` | IdleDummy |
| `0x05` | IdleCasterMotion | `0x3A` | IdleJump |
| `0x06` | IdleTargetMotion | `0x3B` | IdleGosub |
| `0x07` | IdleBondageCaster | `0x3C` | IdleCasterGosub |
| `0x08` | IdleBondageTarget | `0x3D` | IdleRandomStart |
| `0x09` | IdleTargetScheduler | `0x3E` | IdleRandomEnd |
| `0x0A` | IdleCasterSound | `0x3F` | IdleKillRebornGenerater |
| `0x0B` | IdleTargetSound | `0x40` | IdleCasterEffectTexture |
| `0x0C` | IdleDoorSlide | `0x41` | IdleTargetEffectTexture |
| `0x0D` | IdleDoorOpen | `0x42` | IdleCasterEffectUScroll |
| `0x0E` | IdleScreenBlur | `0x43` | IdleCasterEffectVScroll |
| `0x0F` | IdleScreenColor | `0x44` | IdleTargetEffectUScroll |
| `0x10` | IdleScreenOverRap | `0x45` | IdleTargetEffectVScroll |
| `0x11` | IdleCasterApprouch | `0x46` | IdleCasterEffectColor |
| `0x12` | IdleCasterBackJump | `0x47` | IdleTargetEffectColor |
| `0x13` | IdleTargetApprouch | `0x48` | IdleCasterDistortionAmount |
| `0x14` | IdleTargetBackJump | `0x49` | IdleTargetDistortionAmount |
| `0x15` | IdleCasterFreeze | `0x4A` | IdlePlayerTargetSound |
| `0x16` | IdleTargetFreeze | `0x4B` | IdleDisableTargetEffectTexture |
| `0x17` | IdleCasterUnfreeze | `0x4C` | IdleCasterDistortion |
| `0x18` | IdleTargetUnfreeze | `0x4D` | IdleDisableCasterDistortion |
| `0x19` | IdleDirectLoadMagic | `0x4E` | IdleTargetDistortion |
| `0x1A` | IdleDirectLoadChara | `0x4F` | IdleDisableTargetDistortion |
| `0x1B` | IdleDirectCaster | `0x50` | IdleNop |
| `0x1C` | IdleDirectTarget | `0x51` | IdleDisplayColor |
| `0x1D` | IdleLiftMove | `0x52` | IdleActivateTime |
| `0x1E` | IdleKillGenerater | `0x53` | IdleNearTargetSound |
| `0x1F` | IdleLockCasterStatus | `0x54` | IdleCasterEffectTexture2 |
| `0x20` | IdleLockTargetStatus | `0x55` | IdleTargetEffectTexture2 |
| `0x21` | IdleCasterDamage | `0x56` | IdleLockAllTargetStatus |
| `0x22` | IdleTrackingCasterFreeze | `0x57` | IdleCasterScheduler |
| `0x23` | IdleTrackingTargetFreeze | `0x59` | IdleLockCasterMagic |
| `0x24` | IdleAtkSchSelect | `0x5A` | IdleCasterGuard |
| `0x25` | IdleTargetDamage | `0x5B` | IdleTargetGuard |
| `0x26` | IdlePathDriveCaster | `0x5C` | IdleCasterCounter |
| `0x27` | IdlePathDriveTarget | `0x64` | IdleIf |
| `0x28` | IdleSysMotion | `0x65` | IdleOr |
| `0x29` | IdleCasterColor | `0x66` | IdleThen |
| `0x2A` | IdleTargetColor | `0x67` | IdleElse |
| `0x2B` | IdlePutStatusMessage | `0x68` | IdleEndif |
| `0x2C` | IdleAfterImage | `0x69` | IdleNestIn |
| `0x2D` | IdleStopGenerater | `0x6A` | IdleNestOut |
| `0x2E` | IdleLockCasterControl | `0x6B` | IdleInstruction |
| `0x2F` | IdleLockCasterRotation |  | |

---

## 4. Attach points — `EID_INDEX` (`main/miyagawa/ymattach.cpp`)

This is the 128-entry joint-reference table on every skeleton. xi-tools already knows
127 as the right hand and 126 as the left ([../gear/pose.md](../gear/pose.md)); these
are the SE names for all of it:

- `EID_DAM_*`: the eight hit directions.
- `EID_AF_START*` / `EID_AF_END*`: after-image, i.e. weapon-trace, endpoints.
- `112`–`125`: the grip joint for each weapon type.
- `48`–`51`: the virtual targets a generator can attach to.

| id | SE name | id | SE name | id | SE name |
|---|---|---|---|---|---|
| `0` | EID_CURRENT | `27` | EID_REACH_C | `70` | EID_AF_START3 |
| `1` | EID_WAIST | `28` | EID_REACH_D | `71` | EID_AF_END3 |
| `2` | EID_NAME | `29` | EID_REACH_E | `72` | EID_AF_START4 |
| `3` | EID_NECK | `30` | EID_REACH_F | `73` | EID_AF_END4 |
| `4` | EID_LOOK_AT | `31` | EID_REACH_G | `74` | EID_AF_START5 |
| `5` | EID_HEAD_TOP | `32` | EID_REACH_H | `75` | EID_AF_END5 |
| `6` | EID_EYE_CENTER | `33` | EID_R_EYE0 | `76` | EID_AF_START6 |
| `7` | EID_CHEST | `34` | EID_L_EYE0 | `77` | EID_AF_END6 |
| `8` | EID_R_FOOT | `35` | EID_R_EYE1 | `78` | EID_AF_START7 |
| `9` | EID_L_FOOT | `36` | EID_L_EYE1 | `79` | EID_AF_END7 |
| `10` | EID_R_HAND | `37` | EID_R_EYE2 | `112` | EID_CLAW_R |
| `11` | EID_L_HAND | `38` | EID_L_EYE2 | `113` | EID_SWORD_R |
| `12` | EID_HEIGHT | `39` | EID_CAMERA0 | `114` | EID_AXE_R |
| `13` | EID_DAM_N | `40` | EID_CAMERA1 | `115` | EID_KATANA |
| `14` | EID_DAM_NE | `41` | EID_CAMERA2 | `116` | EID_KNIFE_R |
| `15` | EID_DAM_E | `42` | EID_CAMERA3 | `117` | EID_NINJA_R |
| `16` | EID_DAM_SE | `43` | EID_EMPTY | `118` | EID_SCYTHE |
| `17` | EID_DAM_S | `48` | EID_GROUND | `119` | EID_D_AXE |
| `18` | EID_DAM_SW | `49` | EID_NEAREST_CASTER | `120` | EID_TWOHAND |
| `19` | EID_DAM_W | `50` | EID_NEAREST_TARGET | `121` | EID_SWORD_L |
| `20` | EID_DAM_NW | `51` | EID_NEAREST_CAMERA | `122` | EID_AXE_L |
| `21` | EID_BODY_CENTER | `64` | EID_AF_START0 | `123` | EID_KNIFE_L |
| `22` | EID_HEAD_CENTER | `65` | EID_AF_END0 | `124` | EID_NINJA_L |
| `23` | EID_MAGIC0 | `66` | EID_AF_START1 | `125` | EID_CLAW_L |
| `24` | EID_MAGIC1 | `67` | EID_AF_END1 | `126` | EID_L_WEPON_JOINT |
| `25` | EID_REACH_A | `68` | EID_AF_START2 | `127` | EID_R_WEPON_JOINT |
| `26` | EID_REACH_B | `69` | EID_AF_END2 |  |  |

---

## 5. Other game enums

All from DWARF. The full text is in `xi-beta-viewer/research/dwarf/types.h`.

### Gear hide groups — `HIDE_GROUP` (`kazumi/KzOsm.cpp`)

`HIDE_GRP_NON=0`, `KAMI=1` (hair), `MOMI=2` (sideburns), `USHIRO=3` (back hair),
`TEKUBI=4` (wrist), `HIZA=5` (knee), `SUNE=6` (shin), `METOTHER=7`.

Compare the `displayType` values in [../gear/pose.md](../gear/pose.md): 1 hair, 2/3 hair,
4 face, 5 wrist, 6 "pants", 7 shins. That is the same list with *face* inserted at 4, and
the hair values 1–3 match exactly. If retail inserted face and shifted the rest, **display
type 6 is the knee region (`HIZA`)** rather than trousers as a whole. This is unverified.

### Actor state — `GAME_STATUS` (`xiactorwin.cpp`)

| Values | Names |
|---|---|
| 0–3 | `G_NOR_IDLE`, `G_BTL_IDLE`, `G_NOR_DEAD`, `G_BTL_DEAD` |
| 4–7 | `G_NOR_EVT`, `G_NOR_CHOCOBO`, `G_NOR_FISHING`, `G_NOR_POL` |
| 8–9 | `G_DOOROPEN`, `G_DOORCLOSE` |
| 10–17 | `G_LIFT0`–`G_LIFT7` |
| 18–25 | `G_MODEL0`–`G_MODEL7` |
| 26–31 | `G_EVSP0`–`G_EVSP5` |
| 32–33 | `G_REQLOGOUT`, `G_CAMP` |
| 255 | `G_NONE` |

Related enums:

- `SUBACTOR_STATUS`: `SUB_CHOCOBO0`–`3`.
- `MOVEDIR_ID`: `MVD_IDLE/FRONT/RIGHT/BACK/LEFT`.
- Motion-list categories `CATEGORY` (`KzResfList`): `RFL_STANDARD`, `RFL_BATTLE`,
  `RFL_EVENT`, `RFL_EMOTION0`, `RFL_ETC`, `RFL_COMMON`.

### Equipment slots — `SAVE_EQUIP_KIND`

| Values | Slots |
|---|---|
| 0–3 | `RIGHTHAND`, `LEFTHAND`, `BOW`, `ARROW` |
| 4–8 | `HEAD`, `BODY`, `ARM`, `LEG`, `FOOT` |
| 9–10 | `NECK`, `BELT` |
| 11–14 | `RIGHTEAR`, `LEFTEAR`, `RIGHTFINGER`, `LEFTFINGER` |
| 15 | `BACKPACK` |

This is the retail slot order.

### Commands and emotes — `COMNUM` (`main/actor/commandcalc.cpp`)

The client's command numbers, 0–230. The emotes run from `COM_E_POINT = 58` to
`COM_E_SULK = 93`:

- `POINT`, `BOW`, `SALUTE`, `KNEEL`, `LAUGH`, `CRY`
- `DENIAL`, `NOD`, `WAVE`, `GOOBYE`, `WELCOME`
- `GLAD`, `CHEERING`, `CLAPPING`, `APPLAUSE`, `SMILE`
- `NUDGE`, `BEAT`, `FALLDOWN`, `DEPRESSED`, `COMFORT`
- `SURPRISING`, `WHY`, `GAZE`, `SHY`, `ANGRY`, `DISGUST`
- `SILENCE`, `DOZE`, `PANIC`, `GRIN`, `DANCE`, `THINK`
- `GRR`, `DOUBT`, `SULK`

The enum also lists the text commands and debug commands (`COM_SETZONE`, `COM_POSSET`,
`COM_DBMODE`, `COM_BATTLEDEBUG`, …).

### Packets — `GP_SERV_COMMAND` / `GP_CLI_COMMAND` (`main/net/game_cli/gczone.c`)

These are the 2001 ids with SE constant names, e.g.:

- server: `GP_SERV_COMMAND_LOGIN = 10`, `CHAR_PC = 13`, `CHAR_NPC = 14`, `EVENT = 50`,
  `SCHEDULOR = 56`, `WEATHER = 87`
- client: `GP_CLI_COMMAND_POS = 21`, `ACTION = 26`, `EVENTEND = 91`

The ids that exist in the 2003 table in [ps2_decomp_crosscheck.md §9](ps2_decomp_crosscheck.md#9-network--2003-opcode-tables-new)
are the same. This adds the constant names and the handful of 2001-only commands
(`GP_SERV_COMMAND_QMSG_*`, `GP_CLI_COMMAND_FAQ_GMCALL`, …).

---

## 6. PlayOnline library (the code inside polcore.dll / app.dll)

The beta links Square's PlayOnline "sq" library with names. The PC `polcore.dll` carries
the same library, so these names can label it:

- The path helper [../ffximain/polcore.md](../ffximain/polcore.md) calls
  `POL_BuildDataFilePath` is **`sqPlayOnlineGetFileName`**. It is at 0x29ABF8 in
  SLPM_621.35 and switches over `polerr.bin`, `sqsound.irx`, `sqpolcts.bin`,
  `sqpolexe.bin`, `sqpolkey.bin`, `sqpoliop.bin`, picking one by id.
- Named subsystems, with their debug strings intact for cross-architecture matching:

  | Prefix | Covers |
  |---|---|
  | `sqPolcon*` | connection and login state machine (`sqPolconOpenCheck: …` messages) |
  | `sqIrc*` | POL messaging |
  | `sqProf*` | profile |
  | `sqPatch*` | patcher, including `sqUDirectPatchDecompressor` / `sqUIndirectPatchDecompressor` |
  | `sqCrm*` / `sqCram_MD5` | CRAM-MD5 authentication |
  | `sqMime*`, `R3MA_MIME_*` | MIME handling |
  | `sqRsa*`, `sqBlowFish*`, `sqCrypt64`, `sqMd5*`, `sqBase64*`, `sqBase32*` | crypto and encodings |

### Ciphers

**`sqEncryptFile` / `sqDecryptFile`** (0x2732E8 / 0x273340)

- An 8-byte key feeds a 256-byte schedule: rotate the key halves, a byte mix, then a ×5
  chain of 32 u64 words.
- Data is processed in 8-byte blocks. `firstencrypt` swaps the halves, XORs a schedule
  word and a counter mix `(c | c<<10 | c<<20 | c<<30) + 0xA1652347`, then adds the word.
  `secondencrypt` adds the word's bytes through `enctable` (which is just `+0x88`) and
  chain-XORs the result.
- The last block is `[u32 original length][u32 checksum]`.
- The cipher barely diffuses: near-identical plaintext gives near-identical ciphertext.
- **`PolCtsAny` = `cb 83 24 b7 ba 3e 9e 0a`** is the fixed key for the 48-byte content
  authentication tokens (`__sqPolGenerateContentsAuthenticationPassword`).

**`IrxDecrypt(buf, len, seed)`** (0x2985A8)

- Undo a byte chain-XOR walking backwards, then XOR with an LCG:
  `next = next*0x41C64E6B + 0x3021`, byte `(next>>16) & 0xFF`.
- With `seed = len` it decrypts every PS2 `.ERX` module archive:
  `[u32 size][char name[12]][ELF]`, 4-byte aligned.

Reference implementations with an exact encrypt/decrypt round trip are in
`xi-beta-viewer/research/pol/polcrypt.py`.

**Open lead.** On PC, `PlayOnlineViewer/data/doc/polerr.bin` and `sqpolcts.bin` have
near-identical 8-byte blocks at `+0x18` (`8a cb 88 f2 26 f7 fc 70` and
`8b ca 89 f3 26 f7 fc 70`). That is the fingerprint of this cipher. Neither decrypts with
`PolCtsAny`. Known plaintext constrains the schedule word bit by bit, and blocks in the
same 256-byte window share it up to a factor of 5, so a few known blocks should recover
the key. That makes this a practical next step for reading them.

---

## 7. Sound: the beta is sequenced

`MISC.DAT` on the disc installs the HDD sound tree:

- `sound/ps2/music/data/musicNNN.bgm`: `BGM ` magic, then the file's number as a u16,
  twice.
- `sound/ps2/wave/waveNNNN.wd`: `WD` magic, bank id.

Beta music is MIDI-style sequences over sample banks. This is Square's PS2 SQS engine
(`Sqs*` symbols), the same `.bgm` + `.wd` file family as Square's other PS2 titles
(e.g. Kingdom Hearts). The PC client streams `.bgw` instead. Existing BGM/WD tools
could render the beta soundtrack; nothing in `xi audio` needs it.

---

## 8. Characters and zones in the beta formats

The PS2 build stores geometry as ready-made VU1 packets, so the byte layouts do not carry
over to the PC formats (`Os2`, `Mmb`). The engine rules around them may. They are decoded
and rendered in xi-beta-viewer (FORMATS.md §11-12). The ones to compare against retail are
below; none has been checked against the PC formats yet.

- **Pose** (`KzObject::GetElemWorldMatrix`):
  `local = T(skd.trans + mot.trans) · R(mot.qrot ⊗ skd.qrot)`, then parent · local. Motion
  values are offsets from the skeleton, not absolute poses.
- **Motion timing** (`KzMotionQue::UpdateFrame`): `0.5 × speed` frames per 60 Hz field
  (30 fps at speed 1). Looping motions repeat their first key as the last, so a cycle is
  `frames − 1`. NPC motions come in layers with a digit suffix (`idl0` + `idl1` + `idl2`)
  that play together.
- **Rotation keys** are second-order deltas (value, velocity, byte or short steps), 2048 = 1.0.
  `KzFQuat::slerp` compiled to a shortest-path linear blend.
- **Half meshes.** One side is stored; the other is mirrored in x onto each joint's `flip`
  partner from the skeleton.
- **Mesh vertices** are in bind-pose model space relative to joint 0, blended across two
  joints with one weight.
- **Weapons** bind to a joint whose bind position is the origin and follow it when a motion
  keys that joint. Unkeyed weapons go through an EID-table constraint that is not decoded.
- **Zone layout** (`KO_MapLocation`): a 20 × 20 grid of 40-unit cells centred on the origin.
  Each placement is a model-list index, a flag byte (0x01 mirrored, 0x20 shadow-only,
  0x80 hidden; doors toggle 0x80) and a column-major 4×4 matrix with a ×16 unit factor.
  Model names end in `h`/`m`/`l` for the level of detail; a leading `_` means alpha test,
  a leading `#` shadow-only.
- **Coordinates.** Right-handed with −Y up, for both characters and zones.

---

## 9. What the beta cannot help with

- Gear model tables, model-id ranges 2–4, DXT textures and expansion zones all post-date
  2001, as they do for the 2003 corpus.
- Event VM opcodes: the 2003 corpus is newer and richer (`XiEvent` has 36 methods here).
- `FFXI_POL.ENC`, the HDD game program. It is not the sq cipher (it fails the
  known-plaintext ELF test) and not `IrxDecrypt`. It is decrypted by the separate PS2
  PlayOnline Viewer program, which is not on this disc. That Viewer is the companion disc **SLPM 621.34** (typically `F:\`); see [ps2_pol_viewer_2001.md](ps2_pol_viewer_2001.md).

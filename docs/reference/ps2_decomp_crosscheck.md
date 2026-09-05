# PS2 client decompile (SCUS_972.66, 2003) — cross-check against this knowledgebase

Source: `research/external/FFXI-PS2` (git-ignored; upstream
<https://github.com/sruon/FFXI-PS2>). A Ghidra decompile of the **PlayStation 2 FFXI
client** with the original MetroWerks DWARF symbols, so class, field, method and
parameter names are Square Enix's own. 11,042 functions, 689 class headers.

What it is good for, and what it is not:

- **Authoritative names** for engine structures (`XiEvent`, `XiWeather`, `KO_RectData`,
  `YmScheduler` …) and for every event-VM / scheduler / packet handler. Field names tagged
  `[llm]`/`[synth]` in `include/*.h` are guesses; untagged ones are DWARF.
- **No string literals survive** (only addresses), so opcode *names* come from handler
  function names, not from text tables.
- **2003 snapshot.** Anything added later (event opcodes ≥ `0xA7`, expansion zone ids,
  model-id ranges 2–4, DXT textures) is absent. Absence is not evidence of non-existence.
- **PS2 resource types differ from PC for GS-specific data** (meshes, textures, zone
  geometry). Shared logic types (effects, scheduler, camera, keyframes, weather, RID,
  sound pointers) use the **same type codes** as the PC client.

Everything below was read from the decompiled bodies, not from the README.

---

## 1. Engine setup (boot → frame loop)

`src/main/boot/kernel.cpp` `main()`:

1. `XiHardInitialize` — SIF/CD init, IOP reboot, memory pools (`YmMcb`, `YmDataMcb`,
   `YmElemMcb`), `YmInit`, GS/DMA/VU0 reset, IOP modules, `YmInitProtocol`.
2. `XiBootInitialize` — pad/mouse, `KO_Init` (renderer), `KzInit` (skeleton/anim),
   `fsInitFileSystem` (creates `XiFileManager`), then loads the **system resource files**
   with `YmResourceFile::Load(file_id)` + `DisableGC`: string pages via
   `StringPageLoad(0x18, 4|5)`, file ids **1**, **0x4E (78)** and one more, then menu-shape
   (`YmYshRes::SetFile`), `TkInit`, `CTkMenuMng::FrameLoad`, `YkInit`, `KaInit`, macros.
3. `MainProg` — frame buffer, default weather, vsync, then `InstallerMain` / `PatchMain` /
   `PolRebootMain` before the game `MainFlow`.

`MainFlow` (`mainloop.cpp`) is the lobby → login → in-game → logout state machine.
`MainIdle` is the per-frame loop, in this order:

```
YmDB::Swap → StDancer::Update → XiDateTime::SysMove → KO_CalcClipMatrix
→ XiZone::SysMove / SendTexture → YmIdle → pad/keyboard/mouse
→ KO_SetBgEnv(area) → KO_Idle → XiActor::SysMove → KO_SetCharaEnv → XiActor::SysDraw
→ AtelIdle (net/actor buffers) → input dispatch → FsGameProc
→ YmTask::SysIdle → YmGenerater::SysIdle (particles) → YmSortElem::SysIdle/SysDraw
→ menu OnCalc/OnDraw → YmSoundElem::SysMove → YmLoadResourceTask::SysIdle → YmMcb::SysIdle
```

Subsystem prefixes (original source tree, by author): `ym*` = Miyagawa core (memory,
resources, effects, camera, sound), `ko_*` / `KO_*` = Ohno renderer (VU1, collision, RID,
map), `Kz*` = Kazumi skeleton/animation, `Xi*` = game actors/zones/events, `gc*`/`gp*`/`nt*` =
network, `Tk*`/`Yk*`/`Ka*`/`Fs*` = UI.

---

## 2. File addressing (FTABLE / VTABLE) — **verified**

`XiFileImpl::_readNumFile` / `_getFileName` (`src/main/fshira/XiFileImpl.cpp`):

- The "num file" is an array of `u16`; `_getFileName` formats
  `(num >> 7, num & 0x7F)` → `ROM/<subdir>/<index>`. Identical to PC `FTABLE.DAT`.
- The "version file" is a parallel `u8` array; value `1` → base ROM path, any other value
  `n` → `ROM<n>/…`. Identical to PC `VTABLE.DAT`. Missing version file ⇒ all 1.
- Extra ROMs are probed by number **2..9** at startup (`for (v = 2; v < 10; ++v)`), each
  with its own num/version pair, exactly the `ROM2…ROM9` overlay scheme.
- Alternate modes exist for name-file lookup (mode 0) and a DVD-sector mode
  (`num / 30, num % 30`).

This confirms `docs/ftable/*` and the `subdir<<7 | index` packing in
[model-file-ids.md](model-file-ids.md#how-the-ftable-works).

### Per-zone DAT ids — **verified**

| DAT | PS2 code | our formula |
|---|---|---|
| zone model | `XiZone::OpenIndoor/OpenExtendTexture`: `map_num + 100` | `0x64 + zone` ✓ |
| event bytecode | `xievent.cpp`: `eventnum + 0x16BC` | `5820 + zone` ✓ |
| dialog | `xievent.cpp`/`xiatelnet.cpp`: `+ 0x1914` | `6420 + zone` ✓ |
| NPC names | `xiatelnet.cpp`: `+ 0x1A40` → `_MonName` | `6720 + zone` ✓ |

Indoor sub-areas are opened with the **same** `map_num + 100` formula through
`XiZone::OpenIndoor(map_num)` (gated by `GetIndoorPos`), confirming
[../zone/subareas.md](../zone/subareas.md).

### Model ids — **verified for the 2003 range**

`XiSkeletonActor` loads monsters with `ReferenceRead(equip0 + 0x514)` — i.e.
`file_id = modelid + 1300`, the range-1 formula in
[model-file-ids.md](model-file-ids.md#monster-model-lookup). Only one range existed in 2003.

Player gear: `equipNum = slot << 12 | model` (9 slots, `0..8`), file id =
`model + model_group_tab[race][slot]` — a per-race, per-slot base table, which is the
`RACE_TABLES` mechanism (`src/xi/gear/xi_core.py`), never a flat offset. The table values
live in the binary's data, not in the decomp.

---

## 3. DAT resource container — **verified, PS2 type map recovered**

`YmResourceFile::Init` (`ymresfile.cpp`) reads the 16-byte section header exactly as we do:
`type = hdr[4] & 0x7F`, `size = ((hdr32 >> 7) & 0x7FFFF) * 16`. Bit 31 of that word marks
an **extended header** whose extra fields at `+0x14`/`+0x1C` are copied. Directory nesting
uses type `1` (push) and type `0` (pop) — see `FindResourceMine`.

`ymresfileprocess.cpp` dispatches each section to a class. Types that match the PC
[dat_sections.md](dat_sections.md) numbering are marked ✓; the rest are PS2-only.

| type | PS2 class | base / meaning | PC |
|---|---|---|---|
| `0x01` | `YmTerminateRes` | directory marker | ✓ |
| `0x05` | `YmGeneraterRes` | particle generator | ✓ ParticleGenerator |
| `0x06` | `YmCameraRes` | camera keys | ✓ Route (camera) |
| `0x07` | `YmSchedulerRes` | scheduler (init/idle/die tags) | ✓ EffectRoutine |
| `0x08` | `YmMtxRes` | GS texture (`YmTex`) | PC `0x20` |
| `0x0B` | `YmVumRes` | VU mesh (`KO_Vum`: triangle/transparent/collision groups) | PC `0x2E`-like |
| `0x0E` | `YmAnmRes` | shape animation (`ShapeAnm`) | — |
| `0x11` | `YmOsmRes` | skinned model (`KzOSM`) | PC `0x2A` |
| `0x12` | `YmSkdRes` | skeleton (`KzSKD`) | PC `0x29` |
| `0x14` | `YmMldRes` | map location data (`KO_MapLocation`: chips, parts, doors, lights, env) | PC `0x1C`-like |
| `0x17` | `YmModRes` | motion (`KzMOD`) | PC `0x2B` |
| `0x19` | `YmKeyFrameRes` | keyframe curves | ✓ |
| `0x1D` | `YmMmdRes` | map model (`KO_Mmd`, opaque/transparent VU objects) | — |
| `0x2C` | `YmPswRes` | water (`KO_Water`) | — |
| `0x2F` | `YmWeatherRes` | environment (`XiWeather`) | ✓ Environment |
| `0x34` | `YmYshRes` | menu shape (UI) | — |
| `0x35` | `YmMbpRes` | (unnamed) | — |
| `0x36` | `YmRidRes` | rect data (`KO_Rid`) | ✓ ZoneInteractions |
| `0x37` | `YmWdRes` | `YmWdHeader` | — |
| `0x39` | `YmLfdRes` | lens flare (`KO_LensFlare`) | — |
| `0x3D` | `YmSepRes` | sound effect pointer (`SeSepHEAD`) | ✓ |
| `0x3E` | `YmVtxRes` | vertex list | ✓ PointList |
| `0x42` | `YmRabRes` | path (`KO_Path`) | — |
| `0x44` | `YmMtbRes` | (unnamed) | — |
| `0x4A` | `YmSphRes` | path (`KO_Path`) | ✓ Path |
| `0x4B` | `YmBmdRes` | `KO_Bmd` model | — |
| `0x54` | `YmAfbRes` | after-image buffer (`KzAfb`) | ✓ WeaponTrace |
| `0x5E` | `YmSefRes` | shadow/blur effect data (`CEfData`) | ✓ Blur |

Implication: **PS2 DATs are not byte-compatible for meshes, textures, zone geometry or
animation** (different type codes and different headers — e.g. `KzMOD` starts
`nbFrame:u16, nbChannel:u16, scale:f32[3], speed:f32, version:u8`, unlike the PC `0x2B`
10-byte header in [../anim/format.md](../anim/format.md)). Effect, scheduler, camera,
keyframe, weather, RID and sound-pointer sections **are** the same design.

---

## 4. Event VM (`XiEvent`, `src/main/actor/xievent.cpp`)

### 4.1 Actor block parse — **verified**

`XiEvent::XiEvent(actor, idx, data)`: `TagCount = data[4]`, `TagOffset = data+8`
(`u16[TagCount]`), `EvectExecNum` follows (`u16[TagCount]`, the event ids; `0xFFFE` is
matched as a wildcard in `XiEventInit`), then `ImedCount:u16`, `ImidData = +4`
(the `references[]`), then `EventData = ImidData + (ImedCount+1)*4` (skips the size word).
This is byte-for-byte the layout in [../events/format.md](../events/format.md#actor-block).

### 4.2 Operand / work-selector model — **verified and completed**

`getworkofs` / `setworkofs`: an operand `u16` with bit `0x8000` set reads
`ImidData[sel & 0x7FFF]` (our `references[]`). With the bit clear it is a **work slot**:

| selector | meaning |
|---|---|
| `0x0000–0x07FF` | `Work_Local[]` — per-event scratch (64 ints, zeroed on start) |
| `0x1000–0x17FF` | `Work_Zone[]` — zone-global work area shared by all events |
| `0x7F00` / `01` / `02` | event entity position X / Y / Z (int ÷ 1000) |
| `0x7F03` | event entity heading, `n × 2π / 4096` |
| `0x7F06` | 1 if the event entity is the local player |
| `0x7F07`, `0x7F0A`, `0x7F0B` | actor buffer fields (`+0xC3`, `+0x74` = server id, `+0xF7` bit) |
| `0x7F80–0x7F8B` | the same fields for the **local player** |

So slots below `0x8000` are resolvable at author time by category even though their values
are runtime state. The heading scale (`÷4096`) matches the `0xBA` note in
[../events/opcodes.md](../events/opcodes.md).

### 4.3 Special actor ids (`GetActorIndex`)

| id | resolves to |
|---|---|
| `0x7FFFFFF0` | local player (`gcZoneCharID`) |
| `0x7FFFFFF1 … F5` | party members 1–5 (walks `gcGroupLook*`) |
| `0x7FFFFFF8` | the event's own entity |
| other with top byte `0` | the event's own actor; otherwise `index = num & 0x3FF` |

### 4.4 Opcode table — original handler names, 2003 extent

`XiEvent::ExecProg` is a `switch` over the opcode byte that ends at **`0xA6`**; everything
from `0xA7` to `0xD9` in [opcodes.md](../events/opcodes.md) was added after 2003. Where the
PS2 body calls a named method, that is SE's name for the opcode:

| op | SE handler | op | SE handler |
|---|---|---|---|
| `0x02` | `CodeIF` | `0x50`/`0x51`/`0x52` | `CodeENDSCHEDULOR` / `CodeENDMAPSCHEDULOR` / `CodeENDLOADSCHEDULER_Main` |
| `0x1F` | `CodeMOVE` | `0x53`/`0x54`/`0x55` | `CodeWAITSCHEDULOR` / `CodeWAITMAPSCHEDULOR` / `CodeWAITLOADSCHEDULER_Main` |
| `0x23` | `CodeMESWAIT` | `0x5A` | `CodeMOVE2` |
| `0x24` | `CodeQUERY` | `0x5B`, `0x66` | `CodeLOADEXTSCHEDULERMain` |
| `0x25` | `CodeQUERYWAIT` | `0x62` | `CodeLOADEVENTSCHEDULER` |
| `0x28` / `0x29` | `CodeREQSW` / `CodeREQEW` | `0x65` | `CodeGETDISTANCEAA` |
| `0x2C` | `CodeSCHEDULOR` | `0x6C` | `CodeTRANSPAR` |
| `0x2D` | `CodeMAPSCHEDULOR` | `0x6E` | `CodeEMOT` |
| `0x31` | `CodeSMOVE` | `0x71` | `CodeOPENPASSWIN` |
| `0x34` / `0x35` | `XiZone::Open` (with / without close) | `0x72` | `CodeGETWEATER` |
| `0x40` / `0x41` | `CodeSETBITWORK` / `CodeGETBITWORK` | `0x73` | `CodeMAGICSCHEDULOR` |
| `0x45` | `CodeLOADSCHEDULER` | `0x75` | `CodeLOADROOM` |
| `0x46` | `CodeDEFCAMERA` | `0x7E` | `CodeCHOCOBO` |
| `0x4A` | `CodeDTURA` (turn toward) | `0x7F` | `CodeQUERYWAIT2` |
| `0x80` | `CodeLOADWAIT` | `0x8B` | `CodeSETEVENTMARK` |
| `0x98` | `XiZone::IsReadingExtData` / `ReleaseIndoor` | `0x9F`–`0xA3` | `CodeLOADEVENTSCHEDULER2` + wait/end main variants |

Packets sent from inside the VM: `0x87`/`0x88` → client `0x01B`, **`0x8C` → client
`0x058`** (crafting), `0xA6` → client `0x0EB`. The `0x47` → `0x05C` send in our table does
not exist in 2003.

---

## 5. Scheduler (`0x07`) — `YmScheduler` / `YmSchedulerTask::ExecuteTag`

Header fields (after the 16-byte section header): `init_tag`, `idle_tag`, `die_tag`
offsets and `total_frame` — SE's names for what
[../fx/effect_system.md](../fx/effect_system.md#3-0x07-effectroutine--the-sequencer--trigger-layer)
calls sec1 / sec2 / sec3 / totalDelay. Each tag is `opcode:u8, len:u8 (& 0x1F, in dwords)`.

Tag opcodes and the task class they spawn (SE names; xim names in the doc above agree
where both exist):

| tag | task / action | tag | task / action |
|---|---|---|---|
| `0x02` | fire generator (`YmGenerater::Activate`) | `0x53` | sound (target-relative, `YmLength`) |
| `0x05` | `XiSkeletonActor::FindModResList` (skeleton anim) | `0x55` | `EffectTextureActor` |
| `0x07`/`0x08` | `BondageActor` (bind caster↔target) | `0x56` | `LockActorStatus` on target |
| `0x0C` / `0x0D` | `DoorSlideDriveTask` / `DoorAngleDriveTask` | **`0x58`** | **camera** `SetEye` / `SetAt` / `SetProjection` |
| `0x0E` / `0x0F` | `ScreenBlurDriveTask` / `ScreenColorDriveTask` | `0x59` | `LockCasterMagic` |
| `0x10` | `ScreenOverRapTask` (screen overlap/fade) | `0x5E` | knockback (`GetDamageDirId`) |
| `0x12` / `0x14` | `XiControlActor::BackJump` | `0x60`, `0x8A` / `0x8B` | `YmSepRes::Play` / `Stop` |
| `0x16`, `0x23` | spawn `XiDollActor` | `0x62` | `IdleAdjustDirTask` |
| `0x17` / `0x18` | set caster / target | `0x70` | `LightDriveTask` |
| `0x1A`–`0x1C` | `XiActor::FindActor` / debug create | `0x71` | status message (`PutMessageSP`) |
| `0x1D` | `LiftMoveDriveTask` | `0x74` | `DisintegrateActor` |
| `0x1E`, `0x2D` | generator deactivate / kill | `0x75` | show / hide weapon (`HideWepControl`) |
| `0x1F` / `0x20` | `LockActorStatus` | `0x7A` | `EidLinkControl` |
| `0x21`, `0x25` | flinch (`GetDamageDirId`) | **`0x7C` / `0x7D`** | **set Vana'diel tick / current time** |
| `0x24` | `YmParentTask::Suspend` on result | **`0x7E`** | **`XiWeather::SetDefault`** |
| `0x27` | `PathDriveActorTask` | `0x7F` | `SpecularPowerDriveActor` |
| `0x29` / `0x2A`, `0x46` / `0x47` | `ActorColorDriveTask` (fade) | `0x80` | `UseProcMagic` (clone status) |
| `0x2B` | status message (`PutMessage`) | **`0x82` / `0x83`** | **`ChangeFocusDriveTask` (depth of field)** |
| `0x2C` | `KzAfterImage` / `IdleAfterImageTask` (weapon trace) | `0x85` | `YmScheduler::Kill` |
| `0x2E` / `0x2F` | `LockCasterControl` / `LockCasterRotation` | `0x88` | `LockColorDriveTask` |
| `0x3B` / `0x3C` | suspend parent (blocking child) | `0x89` | `LockConstrainDriveTask` |
| `0x3F` | transition generator (`AttachCalc`/`Idle`/`KillAll`) | | |
| `0x42`–`0x45` | `EffectScrollActor` (actor texture wrap/UV) | `0x49` | `EffectDistortionActor` |

Bold rows are new relative to the xim-derived table.

Scheduler task state (`YmSchedulerTask`): `caster`, `target`, `document` (the scheduler),
`tag`, `stat` (`CXiSchStatus`, battle result), `speed_ratio`, `time_cnt`, `wait`,
`early_end`, `is_loop`, `is_tech_res`, `is_ship_scheduler`.

---

## 6. Camera (`0x06`) — `YmCameraKey` is 48 bytes

```
+0x00 f32[4] eye        (w = the focal-length slot we documented at +0x0C)
+0x10 f32[4] at         (w = roll)
+0x20 f32    keyframe   (normalized time)
+0x24 s16    focus_near
+0x26 s16    focus_far
+0x28 u8[8]  padding
```

`YmCamera` carries `key_spline_mode` (our interp `mode 0..4`). `YmCameraPath` drives three
splines: eye, at, fov. New detail vs [../events/cutscenes.md](../events/cutscenes.md): the
"12 zero pad" bytes start with two `s16` depth-of-field values.

---

## 7. Environment (`0x2F`) — `XiWeather` record

```
+0x00 u32 is_shadow            (our "indoors")
+0x04 u32 point_light_num
+0x08 ptr point_light_tbl      (XiPointLightEnv[])
+0x0C XiColorEnv chara         (32 bytes)
+0x2C XiColorEnv bg            (32 bytes)
+0x4C XiWorldEnv world         (96 bytes)

XiColorEnv: +0 room_light_col rgba, +4 room_light_vec (see below), +8 ambient_col,
            +0xC fog_col, +0x10 fog_far, +0x14 fog_near, +0x18 light_power, +0x1C pad
XiWorldEnv: +0 r, +4 focus_far, +8 focus_near, +0xC clip_range, +0x10 focus_far_num u8,
            +0x11 focus_near_num u8, +0x12 sphere_div_v_num u8, +0x14 effect_ambient_col,
            +0x18 zone_sound_res, +0x1C sphere (68 bytes: radius, ring colours, elevations)
```

`XiArea::ColorCorrect`: when `is_shadow & 1` is **clear**, `+4` is the sub-light *colour*;
when **set**, `+4` holds a signed-byte **light direction vector** (normalised at load) and
the sub-light colour comes from the parent zone. Our layout in
[../zone/inject-legacy.md](../zone/inject-legacy.md#environment-section-layout-0x2f) matches;
this adds the point-light table pointer, the depth-of-field pair at `+0x50/+0x54`, the
effect ambient colour at `+0x60` and the zone sound reference at `+0x64`.

---

## 8. RID (`0x36`) — `KO_Rid` / `KO_RectData`

Header: `file_id:u32, version:u32, dummy:u32[2], offset_tbl:u32[8]` — the first table
offset is the `dataOffset` we read at `+0x10`; there are up to eight sub-tables.

Entry (64 bytes, DWARF names):

```
+0x00 f32 x, y, z        +0x0C u32 tex_map_no     +0x10 f32 ry (Y rotation only)
+0x14 u32 pad            +0x18 f32 sx, sy, sz     +0x24 i32 id
+0x28 i32 target_id      +0x2C i32 zone_no        +0x30 u32 arrow_flag
+0x34 s16 lift_height    +0x38 f32 lift_current_height (runtime)  +0x3C u32 flag
```

`KO_RectData::HitCheck` transforms the test segment by `translate(-pos) · rotY(-ry) ·
scale(1/s)` and accepts `|x|,|y|,|z| < 0.5` — so **`sx/sy/sz` are full extents**, and the
only rotation is about Y. The low byte of `id` (the first character of the 4-char id) is the
**kind**: `'z'` zone line, `'m'` map / sub-area, `'@'` lift, `'s'` sound region.
Corrections to [../zone/subareas.md](../zone/subareas.md) are noted there.

---

## 9. Network — 2003 opcode tables (new)

Packet framing (`gczone.c` `RecvProc`, `enacv.c`): UDP datagram → `enAcvGet` (Blowfish
decrypt of the body, MD5 trailer check, Huffman decode) → a run of `GP_GAME_PACKET_HEAD`
packets, each `id = word0 & 0x1FF`, `length = byte1 >> 1` (in 4-byte units), `seq = word1`.
Two handler tables, indexed by id (`< 0x110`): the **net layer** table
(`gcZoneRecvCallBack2`, runs first) and the **UI/actor** table (`gcZoneRecvCallBack`, runs
after, gated by zone-ready flags). Handler names below are SE's, with the
`GP_SERV_*` struct each takes.

### Server → client

| id | handler (struct) | id | handler (struct) |
|---|---|---|---|
| `0x005` | RecvPacketControl | `0x058` | RecvAssist |
| `0x006` | RecvNaraku | `0x059` | RecvFriendPass |
| `0x008` | RecvEnterZone | `0x05A` | RecvEmotionMes (MOTIONMES) |
| `0x009` | RecvMessage | `0x05B` | RecvWpos |
| `0x00A` | RecvLogIn | `0x05C` | RecvPendingNum |
| `0x00B` | RecvLogOut / TkZoneOutCallBack | `0x05D` | RecvAuctionHouse |
| `0x00D` | RecvCharPc (CHAR_PC) | `0x05E` | RecvConquest |
| `0x00E` | RecvCharNpc (CHAR_NPC) | `0x05F` | RecvMusic |
| `0x011` | RecvCharDel | `0x060` | RecvMusicVolume |
| `0x012` / `0x013` | RecvGm / RecvGmCommand | `0x061` / `0x062` | RecvCliStatus / RecvCliStatus2 |
| `0x014` | RecvMessageTell | `0x064` | receivePreferenceData |
| `0x016` | RecvMessageTalk | `0x065` | RecvWpos2 |
| `0x017` | RecvStdChat | `0x06F` / `0x070` | gcRecvCombine / gcRecvCombineInfo |
| `0x01C` / `0x01D` | RecvItemMax / RecvItemSame | `0x078` / `0x079` | switch (vote) Start / Proc |
| `0x01E` / `0x01F` / `0x020` | RecvItemNum / RecvItemList / RecvItemAttr | `0x082`–`0x086` | guild buy / buy-list / sell / sell-list / open |
| `0x021`–`0x025` | item trade req / res / list / present / my-list | `0x096`–`0x09E` | myroom enter / exit / is / exist / plant / raise / harvest / diary / job |
| `0x027` / `0x02A` / `0x036` / `0x043` | TalkNumWork2 / TalkNumWork / TalkNum / TalkNumName | `0x0A0` | gcRecvMapGroup |
| `0x028` | RecvBattleCalc2 (BATTLE2) | `0x0AA` / `0x0AB` / `0x0AC` | RecvMagicData / RecvFeatData / RecvCommandData |
| `0x029` / `0x02D` | RecvBattleMessage / 2 | `0x0B4` | RecvConf (CONFIG) |
| `0x02B` / `0x02C` | RecvChannelItem / RecvChannelState | `0x0B5` / `0x0B6` / `0x0B7` | GM param / GM notice / GmScitem |
| `0x02E` | RecvOpenMogMenu | `0x0C8` | RecvGroupTbl |
| `0x02F` | RecvDig | `0x0C9` / `0x0CA` | RecvEquipInspect / RecvInspectMessage |
| `0x030` | RecvEffect | `0x0CC` | RecvComlinkMessage (LINKSHELL_MESSAGE) |
| `0x031` | RecvRecipe | `0x0D2` / `0x0D3` | RecvTrophyList / RecvTrophySolution |
| `0x032` / `0x033` / `0x034` | RecvEventCalc / Str / Num | `0x0DC`–`0x0E2` | group solicit-req / list / solicit-no / attr / comlink / checkid / list2 |
| `0x037` | RecvServerStatus | `0x0F4` / `0x0F5` / `0x0F6` | tracking list / pos / state |
| `0x038` / `0x039` / `0x03A` | RecvSchedulor / RecvMapSchedulor / RecvMagicSchedulor | `0x0F9` | RecvServRes |
| `0x03B` | RecvEventMes | `0x0FA` | RecvOperation (MYROOM_OPERATION) |
| `0x03C`–`0x03F` | shop list / sell / open / buy | `0x105`–`0x10A` | bazaar list / buy / close / shopping / sell / sale |
| `0x041` / `0x042` | gcRecvBlackList / gcRecvBlackEdit | `0x10E` | RecvSubMapNum |
| `0x04B` | RecvReqPostReplyCommon (PBX_RESULT) | `0x10F` | RecvLogoutInfo |
| `0x04C` | RecvAucCommon | | |
| `0x04D` | RecvFragments (MOTD) | | |
| `0x04F` / `0x050` | RecvEquipClear / RecvEquipList | | |
| `0x051` | RecvGrapList | | |
| `0x052` | RecvUcOff (EVENTUCOFF) | | |
| `0x053` / `0x054` | RecvSystemMessage / RecvDebufPrint | | |
| `0x055` / `0x056` | RecvScenarioItem / RecvMissionItem | | |
| `0x057` | RecvWeather | | |

### Client → server (from `gcZoneSendQueSearch(id)` call sites)

| id | sender | id | sender |
|---|---|---|---|
| `0x00A` | StepCalc / BackUpPos | `0x05F` | COM_SETUSER / GETUSER / SPEED (debug) |
| `0x00B` | zone change / logout | `0x060` | CodeOPENPASSWIN (password) |
| `0x00C` | ZoneStartOk | `0x061` | ReqCliStatus |
| `0x00F` | send client game status | `0x062` / `0x063` | FishingIdle / CheckDig |
| `0x015` | `XiAtelBuff::SendCharPos` | `0x064` | SendScenarioItem |
| `0x016` | ReqChrData / InitEvent2 | `0x06E`–`0x078` | group solicit / leave / breakup / strike / change / res / list / change2 / checkid |
| `0x01A` | `cmdf_COM_*` action commands (attack, ability, assist…) | `0x082`–`0x085` | shop req / buy / sell-req / sell |
| `0x01B` | ExecProg (world pass) | `0x08C` / `0x08D` | preference read / save |
| `0x01E` / `0x01F` | GM command / end | `0x096` | gcSendCombine |
| `0x028` / `0x029` | item dump | `0x0A0` / `0x0A1` / `0x0A2` | switch proposal / vote / dice |
| `0x02A` | item attr req | `0x0AA`–`0x0AD` | guild trade / list |
| `0x032`–`0x034` | trade req / cancel-make-start / my-list | `0x0B5` / `0x0B6` | chat std / name |
| `0x036` / `0x037` | hand-over, use item / item use | `0x0C3` / `0x0C4` | comlink make / list |
| `0x038`–`0x03A` | item debug-make / list-req / stack | `0x0C9` / `0x0CA` | myroom enter / exit |
| `0x03C` / `0x03D` | blacklist check / edit | `0x0D2` | map group |
| `0x041` / `0x042` | trophy entry / absence | `0x0D3`–`0x0D5` | bug-report / GM param / notice |
| `0x04B` | MOTD fragments | `0x0DC` | config |
| `0x04D` | post box (SendReqPost_*) | `0x0DD` / `0x0DE` | equip inspect / inspect message |
| `0x04E` | auction (SendAuction_*) | `0x0E0`–`0x0E4` | linkshell messages |
| `0x050` | equip change | `0x0E6`–`0x0EA` | COM_POL / LOGOUT / CAMP / SIT |
| `0x058` | ExecProg (crafting, opcode `0x8C`) | `0x0EB` | ExecProg (map number, opcode `0xA6`) |
| `0x059` | ItemEffectTask | `0x0F0` | rescue |
| `0x05A` | ReqConquest | `0x0F1` | buff cancel |
| `0x05B` | SendEventEnd / SendPendingTag | `0x0F2` | sub-map change |
| `0x05C` | SendBuffCancel | `0x0F4`–`0x0F6` | tracking |
| `0x05D` | COM_GARDEN | `0x0FA`–`0x101` | myroom layout / bank / plants / job / dancer |
| `0x05E` | CliLocalTask | `0x104`–`0x10B` | bazaar |

Headings on the wire are `u8 = angle × 256 / 2π` (`enDirCliToNet`), unlike the event VM's
`÷4096`.

---

## 10. Miscellany worth keeping

- `XiActor::GetType`: `0`/`1` = player-style actors (race + equipment slots), `2` = monster
  (single model id + 1300).
- Event dialog text goes through `XiAtelMess::MesDecode`; item names resolve via
  `CTkItemData::GetItemDataMenber` with singular / plural / long forms.
- Vana'diel time lives in `XiDateTime` (`src/common/xidatetime.cpp`); scheduler tags
  `0x7C`/`0x7D` can override it during cutscenes.
- The `disable_handle_resource` flag is set while a zone loads, so texture and skeleton
  (handle) resources are not garbage-collected mid-load.

- Inventory containers (`gcitem.c`, `KmItem`): 81-row bags with slot 0 reserved, raw
  per-container max byte from packet `0x01C`, unbounded inbound item writes, and the
  `0xDEC` / `0x288` array shapes — written up against the 120-slot patch in
  [../ffximain/inventory.md](../ffximain/inventory.md#11-ps2-2003-decompile-cross-check).

## 11. Gaps this corpus cannot close

- `_globals.h` / `_types.h` (the `RES_TYPE` enum with names) are referenced by the README
  but **not present** in the checkout.
- No string literals ⇒ generator (`0x05`) parameter opcode names are not recoverable
  here; `YmGenerater::GetCode` confirms the `opcode:u8, len:u8 & 0x1F (dwords)` walk only.
- Gear base tables, model-id ranges 2–4, expansion-zone bases and DXT texture handling all
  post-date this binary.

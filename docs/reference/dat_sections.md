# FFXI DAT section types

Canonical section type-code → name registry, from xim's parser
(`thirdparty/xim/src/jsMain/kotlin/xim/resource/DatResource.kt` `SectionType` enum,
dispatched in `DatParser.kt`). The type code is `sectionMeta & 0x7F`. xim renders
these correctly, so these names are authoritative.

The 2003 **PS2 client** uses the same codes for `0x05/0x06/0x07/0x19/0x2F/0x36/0x3D/0x3E/
0x4A/0x54/0x5E` but different codes and headers for meshes, textures, skeletons and
animation — see the PS2 class map in [ps2_decomp_crosscheck.md](ps2_decomp_crosscheck.md#3-dat-resource-container--verified-ps2-type-map-recovered).

See [effect_system.md](../fx/effect_system.md) for how the effect-related sections
(`0x05`/`0x07`/`0x19`/`0x1F`/`0x20`/`0x21`/`0x25`) connect, and how spells resolve to them.

| code | name | what it is |
|------|------|------------|
| `0x00` | End | directory terminator |
| `0x01` | Directory | groups child sections |
| `0x04` | Table | generic table |
| **`0x05`** | **ParticleGenerator** | a visual **effect** (see [effects.md](../fx/effects.md)) |
| `0x06` | Route | path/route data — also the **camera control / camera path** (see [camera note](#camera-resources-use-the-same-section-types)) |
| **`0x07`** | **EffectRoutine** | the **routine/scheduler that triggers & sequences effects** (e.g. WarCry's `main`/`ssub`) — and the **camera timeline** (same scheduler) |
| **`0x19`** | **ParticleKeyFrameData** | keyframe curves an effect references (e.g. fountain `tkus`, fire `hiaa`, lamp `lirr/lirg/lirb`) |
| `0x1C` | ZoneDef | object placement table (positions/TRS) |
| `0x1F` | ParticleMesh | a particle's mesh (e.g. WarCry `wor0`–`wor3`) |
| `0x20` | Texture | DXT / palettized texture |
| **`0x21`** | **SpriteSheetMesh** | sprite-sheet geometry companion to a texture (e.g. `fire`, `tare`, `wor4`) |
| `0x25` | WeightedMesh | skinned/weighted mesh |
| `0x29` | Skeleton | |
| `0x2A` | SkeletonMesh | skinned entity mesh |
| `0x2B` | SkeletonAnimation | |
| `0x2E` | ZoneMesh | static zone geometry (local space) |
| `0x2F` | Environment | fog/lighting/atmosphere |
| `0x30` | UiMenu | |
| `0x31` | UiElementGroup | |
| `0x36` | ZoneInteractions | |
| `0x3D` | SoundEffectPointer | sound trigger (e.g. WarCry `7008`/`7009` → `se007008.spw`) |
| `0x3E` | PointList | |
| `0x45` | Info | |
| `0x46` | (unknown) | |
| `0x49` | SpellList | spell id list |
| `0x4A` | Path | |
| `0x53` | AbilityList | ability id list |
| `0x54` | WeaponTrace | weapon swing trail |
| `0x5D` | BumpMap | |
| `0x5E` | Blur | |
| `0x5F` | (unknown) | |

## What this clarified for effects

- An **effect** is a `0x05` ParticleGenerator. Its referenced helpers, which we'd
  been calling generic "sub-resources":
  - `0x19` **ParticleKeyFrameData** — the animation curves (color/scale/etc. over
    the particle's life).
  - `0x1F` **ParticleMesh** / `0x21` **SpriteSheetMesh** — the geometry/sprite the
    particle draws.
  - `0x20` Texture, `0x3D` SoundEffectPointer — appearance + sound.
- **`0x07` EffectRoutine** is the **trigger/sequencer**. Boss/ability effects are
  driven by an EffectRoutine; zone-ambient effects (fountain, torches) are
  instantiated by the zone. This is the layer that decides *when* an effect fires —
  relevant to why a transplanted boss effect may not appear without its routine.

## Camera resources use the same section types

A **cutscene camera** is built from these same block-types, **byte-verified in xi**
(`parse_camera_routes` in `src/xi/event/xi_event.py`, decoded 2026-06-18 from a real
cutscene's referenced scene resource): each shot pairs a `0x07` **EffectRoutine** (`sNNN`,
the camera timeline) with a `0x06` **Route** (`cNNN`, the eye/look-at/FOV keyframe spline).
Each Route = a 32-byte header (`count`, plus a smoothing/interp **mode** enum `0..4` at
`+0x14`) then `count` × 48-byte keyframes: eye `vec3`, FOV as **focal length**
(`FOV° = 2·atan2(192, focal)`, default ~350 — not tenths-of-a-degree), look-at
`vec3`, roll (radians), normalized time, + 12 zero pad bytes.
An engine-knowledgeable source (FFXI **GDTV** community) independently described the same —
*"the camera timeline is block type `0x07`, camera controls block type `0x06`, and the
scheduler is the same one effects use"* — which also matches the xim-derived `0x07`
EffectRoutine = shared effect scheduler above. The event-VM bytecode only *triggers* these
sections (`0x38`/`0x46` mode flags, `0x45` start-task) — don't confuse these section
block-types with the [event-VM opcodes](../events/opcodes.md#opcodes-vs-dat-section-block-types-two-namespaces)
of the same number. Full layout: [../events/cutscenes.md](../events/cutscenes.md#how-the-camera-works).

> Note: there is **no per-effect-name catalog** — effects are identified only by
> their 4-char FourCC, which varies per DAT (e.g. `tki1`, `lb09`, `g000`). xim
> gives us the section-type vocabulary and the parameter opcodes
> ([effects.md](../fx/effects.md)), not human names for individual effects. The
> `0x49 SpellList` / `0x53 AbilityList` sections map spell/ability **ids** (not
> effect FourCCs) and live in specific DATs.

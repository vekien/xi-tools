# Entity Recolor

Clone an existing entity model (mob, NPC, object) with recolored and/or
scaled textures and inject it as a new model ID in FTABLE10.

## Quick start

```bash
# Red tiger (multiply tint preserves stripes)
xi entity recolor "Crimson Tiger" --clone 308 --tint "#ff3300cc" --blend multiply

# Giant beetle — 4x scale, desaturated marble look
xi entity recolor "Marble Beetle" --clone 408 --scale 4.0 --saturation -100 --lightness 30

# Tiny cyan wyrm
xi entity recolor "Tiny Wyrm" --clone 783 --scale 0.5 --hue 180

# Shadow goblin (dark overlay)
xi entity recolor "Shadow Goblin" --clone 291 --lightness -40 --tint "#330066aa" --blend overlay

# Holy tiger with white blur aura
xi entity recolor "Holy Tiger" --clone 308 --blur "#ffffff,30,120"

# Purple aura wyrm
xi entity recolor "Aura Wyrm" --clone 783 --hue 280 --blur "#8040cc,25,150"

# Orc with Ridill (gear weapon swap + joint remap)
xi entity recolor "Orc Knight" --clone 617 --weapon "gear:HumeMale:main:259"

# Dual-wielding orc with recolored weapons
xi entity recolor "Orc Dual" --clone 617 \
  --weapon "gear:HumeMale:main:319" --weapon-tint "#8800cccc" --weapon-blend overlay \
  --offhand "gear:HumeMale:main:319" --offhand-tint "#8800cccc" --offhand-blend overlay

# Bighead skeleton (scale head joints 4x)
xi entity recolor "Bighead Skel" --clone 564 --joint-scales "5-7:4.0"

# Dry run
xi entity recolor "Test" --clone 308 --hue 120 --dry-run
```

## How it works

1. Resolves the source model ID to its DAT via FTABLE
2. Swaps weapon/offhand blocks if `--weapon`/`--offhand` set
3. Recolors all 0x20 texture sections (DXT endpoints + palettes)
4. Scales skeleton (0x29), mesh (0x2A), and animations (0x2B) if `--scale` set
5. Scales specific body part vertices if `--joint-scales` set
6. Injects 0x5E blur section if `--blur` set
7. Saves the modified DAT to ROM10
8. Registers a new model ID in FTABLE10/VTABLE10 via `entity inject`
9. Outputs SQL for `mob_pools` and `pet_list`

Entity DATs use the same 0x20 DXT/paletted texture format as zone DATs,
so the same recoloring engine applies: DXT endpoint modification (lossless,
format-agnostic) and palette HSV rotation.

## Options

| Option | Type | Description |
|--------|------|-------------|
| `--clone N` | int | Source model ID to clone (required) |
| `--hue N` | 0–360 | Rotate hue |
| `--saturation N` | -100 to 100 | Adjust saturation |
| `--lightness N` | -100 to 100 | Adjust brightness |
| `--tint #RRGGBB[AA]` | hex | Blend a colour onto all textures |
| `--blend MODE` | string | `normal`, `multiply`, `screen`, `overlay`, `add` |
| `--scale N` | float | Uniform scale (2.0 = double, 0.5 = half) |
| `--blur #RRGGBB[,alpha,radius]` | string | Add blur/distortion aura |
| `--weapon SPEC` | string | Swap main weapon (model ID, `gear:RACE:SLOT:ID`, or ROM path) |
| `--weapon-hue N` | 0–360 | Hue shift for swapped weapon |
| `--weapon-tint #RRGGBB[AA]` | hex | Tint colour for swapped weapon |
| `--weapon-blend MODE` | string | Blend mode for weapon tint |
| `--weapon-scale N` | float | Scale factor for swapped weapon |
| `--offhand SPEC` | string | Swap offhand weapon (same format as `--weapon`) |
| `--offhand-hue N` | 0–360 | Hue shift for offhand weapon |
| `--offhand-tint #RRGGBB[AA]` | hex | Tint colour for offhand weapon |
| `--offhand-blend MODE` | string | Blend mode for offhand tint |
| `--offhand-scale N` | float | Scale factor for offhand weapon |
| `--joint-scales SPEC` | string | Per-joint scaling: `"5-7:4.0"` or `"5-7:4.0,20-30:2.0"` |
| `--modelid N` | int | Target model ID (default: auto from 15000+) |
| `--dry-run` | flag | Show plan without writing |

## Model scaling

The `--scale` option uniformly scales skeleton bone translations, mesh
vertex positions, and animation translation keyframes. This produces
correctly proportioned models at any size.

Works best with grounded mobs (tigers, beetles, wyrms, genbu). Floating
mobs (bombs, snolls) will float proportionally higher since their hover
height is encoded in the skeleton/animations.

The scale is baked into the DAT — no runtime changes needed. Combine
with recoloring for variants like "Giant Crimson Tiger" or "Tiny Ghost
Beetle".

## Blur aura (distortion effect)

The `--blur` option adds a 0x5E blur section — the same distortion/shimmer
effect seen on Prime Avatars (Ifrit, Shiva, etc.). The effect creates a
radial distortion around the model with configurable color and intensity.

### CLI usage

```bash
# White aura, default settings
xi entity recolor "Holy Tiger" --clone 308 --blur "#ffffff"

# Purple aura, stronger distortion, larger radius
xi entity recolor "Aura Wyrm" --clone 783 --blur "#8040cc,25,150"

# Subtle blue shimmer
xi entity recolor "Frost Tiger" --clone 308 --blur "#c0d0ff,6,60"
```

Format: `--blur "#RRGGBB[,alpha,radius]"`
- Color only: `--blur "#ffffff"` (alpha=20, radius=120 defaults)
- With alpha: `--blur "#ffffff,30"` (radius=120 default)
- Full: `--blur "#ffffff,30,150"`

### Parameters

| Parameter | Range | Default | Effect |
|-----------|-------|---------|--------|
| `r`, `g`, `b` | 0–255 | 200,200,255 | Tint color on distorted edges |
| `alpha` | 1–50 | 20 | Distortion strength (higher = more blur) |
| `radius` | 30–200 | 120 | Blur extent (larger = softer transitions) |
| `layers` | 1–20 | 11 | Distortion layer count (more = blurrier) |
| `frequency` | 1–15 | 8 | Shimmer oscillation speed |
| `falloff` | 50–800 | 400 | Edge blur falloff (higher = subtler edges) |

### Tips

- **Light/white colors** look best — the color tints the distorted edges,
  not the model itself.
- **Larger radius** (120+) produces softer, more natural-looking blur.
  Small radius creates sharper, more pixelated edges.
- **Alpha 20–40** is the sweet spot. Below 10 is barely visible, above 50
  is extremely blurry.
- **Scale the radius** with the model — a 3x model needs ~150-180 radius
  for the same visual effect as radius 60 on a 1x model.
- Combine with recoloring for effects like a dark desaturated orc with
  purple blur aura (see Dark Warlord example below).

### How it works

The 0x5E section (called "Blur" in xim) is inserted before the final END
section in the entity DAT. The FFXI client reads it on model load and
applies a radial distortion effect. This is the same mechanism Prime
Avatars use for their characteristic shimmer — it's not a particle effect
or status effect, it's baked into the model data.

## Afterglow (weapon particle aura)

The afterglow effect is the particle glow seen on completed relic/mythic
weapons. Unlike the blur (which is a distortion), afterglow is a full
particle system with glowing geometry that wraps around the weapon.

### How it works

The afterglow aura block is extracted from a retail AG weapon DAT
(Ragnarok AG, gear model 546) and wrapped around any gear weapon DAT.
The color is controlled by tinting the particle textures and modifying
float multipliers in the particle generators.

The aura block contains:
- 2x ParticleGenerator (0x05) — the glow particles
- 2x KeyFrame (0x19) — animation curves
- 2x ParticleMesh (0x1F) — particle geometry
- 2x Texture (0x20) — paletted particle textures
- EffectRoutine (0x07) sections — lifecycle management

### CLI usage

```bash
# Blue afterglow on Ridill
xi gear inject HumeMale main 259 --afterglow "#3380ff"

# Red afterglow
xi gear inject HumeMale main 259 --afterglow "#ff3300"

# Green afterglow with hue-shifted blade
xi gear inject HumeMale main 259 --afterglow "#33ff66" --hue 120

# Purple afterglow on Ragnarok
xi gear inject HumeMale main 319 --afterglow "#bb33ff"
```

Format: `--afterglow "#RRGGBB"` — the hex color is converted to float
multipliers internally. Approximate mappings:

| Hex | Floats | Look |
|-----|--------|------|
| `#ffffff` | 1.0, 1.0, 1.0 | White/gold (retail default) |
| `#ff3333` | 1.0, 0.2, 0.2 | Red |
| `#3380ff` | 0.2, 0.5, 1.0 | Blue |
| `#33ff4d` | 0.2, 1.0, 0.3 | Green |
| `#b333ff` | 0.7, 0.2, 1.0 | Purple |

### Afterglow vs blur

| Feature | Blur (0x5E) | Afterglow |
|---------|-------------|-----------|
| Effect type | Radial distortion | Particle glow |
| Applies to | Entity mobs/NPCs | Gear weapons |
| Mechanism | Single section insert | DAT wrapper (container + aura block) |
| Color control | RGB per ring | RGB float multipliers + palette tint |
| Visual | Shimmer/heat haze | Glowing particles around weapon |

Use **blur** for mobs/NPCs (avatar shimmer). Use **afterglow** for weapons
(relic glow). They can be combined — a mob with blur holding an afterglow weapon.

## Per-joint scaling (body part scaling)

Scale specific body parts by targeting joints in the skeleton hierarchy.
Vertices attached to the specified joints (and their descendants) are
scaled in-place — bone translations are NOT modified, so the body part
stays connected to the body but its geometry grows.

### CLI usage

```bash
# Giant head skeleton (joints 5-7 = head)
xi entity recolor "Bighead Skel" --clone 564 --joint-scales "5-7:4.0"

# Big head + small arms
xi entity recolor "Bobblehead" --clone 564 --joint-scales "5-7:3.0,8-33:0.5"

# Giant wyrm head
xi entity recolor "Bighead Wyrm" --clone 783 --joint-scales "70-79:2.0"
```

Format: `--joint-scales "RANGE:SCALE[,RANGE:SCALE,...]"`
- Ranges: `"5-7:4.0"` scales joints 5, 6, and 7 by 4x
- Singles: `"5:3.0"` scales only joint 5
- Multiple: `"5-7:3.0,20-30:0.5"` combines ranges

All descendants of specified joints are automatically included.

### How it works

1. Entity DATs contain a skeleton (0x29) with a joint hierarchy and
   mesh sections (0x2A) with per-vertex joint assignments
2. The tool identifies which vertices are attached to the target joints
   via the vertex-to-joint mapping in the mesh
3. Only those vertices' positions are scaled — the rest of the model
   is untouched
4. Humanoid mobs (skeletons, beastmen, orcs) have multiple mesh sections
   (body, armor, weapon) — all meshes are processed

### Identifying joints

Use the skeleton hierarchy to find body parts. Each model's joint tree
can be explored programmatically:

**Skeleton mob (model 564, 48 joints):**
| Body Part | Joints | Notes |
|-----------|--------|-------|
| Head | 5–7 | J5=neck, J6=skull, J7=jaw |
| Right arm | 8–20 | Shoulder through fingers |
| Left arm | 21–33 | Mirror of right |
| Left leg | 34–40 | Hip through foot |
| Right leg | 41–47 | Mirror of left |

**Wyrm (model 783, 106 joints):**
| Body Part | Joints | Notes |
|-----------|--------|-------|
| Tail | 5–19 | Long chain from mid-body |
| Rear legs | 20–30, 31–43 | Left/right symmetric |
| Wings | 44–55, 56–67 | Left/right symmetric |
| Head/neck | 70–79 | Forward chain |
| Front legs | 80–92, 93–105 | Left/right symmetric |

> **Note**: Some models use mirrored rendering — one side of the mesh
> is flipped at render time. Vertex counts may appear asymmetric because
> only one side has explicit vertex data.

## Weapon swapping

Humanoid mob DATs are organized into sub-model blocks delimited by HDR
(0x01) headers. The weapon block (typically named "wep_", "c_cl", etc.)
contains the weapon's texture + mesh as a self-contained unit:

```
HDR "wep_" → TEX "scyt" → MSH "hf_s" → END
```

### Weapon source formats

The `--weapon` and `--offhand` options accept three formats:

```bash
# Mob model ID (same-family weapon swap)
xi entity recolor "Club Skel" --clone 564 --weapon 569

# Gear model (auto-resolves DAT + remaps joint)
xi entity recolor "Orc Knight" --clone 617 --weapon "gear:HumeMale:main:259"

# Explicit DAT path
xi entity recolor "Custom Orc" --clone 617 --weapon "ROM/30/104.DAT"
```

Weapon recoloring and scaling are applied independently of the mob body:

```bash
# Green Ridill on an orc
xi entity recolor "Orc Knight" --clone 617 \
  --weapon "gear:HumeMale:main:259" --weapon-hue 120

# Oversized purple Ragnarok
xi entity recolor "Orc Warlord" --clone 617 \
  --weapon "gear:HumeMale:main:319" --weapon-scale 2.0 \
  --weapon-tint "#8800cccc" --weapon-blend overlay
```

### Dual wielding

Use `--offhand` for the off-hand weapon. Humanoid mobs with an offhand
block ("rng_") support dual wielding. Each hand is swapped independently
with automatic joint remapping:

```bash
# Dual-wielding Ridills with different colors
xi entity recolor "Orc Dualwield" --clone 617 \
  --weapon "gear:HumeMale:main:259" --weapon-hue 120 \
  --offhand "gear:HumeMale:main:259" --offhand-tint "#ff0000" --offhand-blend multiply
```

Main hand maps to the mob's weapon joint (e.g. joint 29 for orcs),
offhand maps to the mob's offhand joint (e.g. joint 46). Orc models
617, 619, 623 have offhand blocks.

Mobs without an existing offhand block get one inserted after the
main weapon block.

### Common gear weapon model IDs

| Weapon | gear_model | DAT Path |
|--------|-----------|----------|
| Ridill | 259 | ROM/30/104.DAT |
| Ragnarok | 319 | ROM/120/5.DAT |
| Excalibur | 320 | ROM/120/6.DAT |
| Mandau | 334 | ROM/136/102.DAT |
| Rune Chopper | 335 | ROM/136/103.DAT |
| Claustrum | 342 | ROM/136/110.DAT |
| Idris | 834 | ROM/357/126.DAT |

Find more with: `xi gear json HumeMale main`

### Full example: Dark Warlord

Combining all features — scaling, recoloring, dual-wield, weapon
scaling, weapon recoloring, and blur aura:

```bash
xi entity recolor "Dark Warlord" --clone 617 \
  --scale 3.0 --saturation -60 --lightness -30 \
  --weapon "gear:HumeMale:main:319" --weapon-scale 2.0 \
    --weapon-tint "#8800cccc" --weapon-blend overlay \
  --offhand "gear:HumeMale:main:319" --offhand-scale 2.0 \
    --offhand-tint "#8800cccc" --offhand-blend overlay \
  --blur "#6428b4,25,180"
```

Result: a 3x dark desaturated orc dual-wielding oversized purple
Ragnaroks with a dark purple distortion aura. The mob body is scaled
and darkened, each weapon is independently scaled 2x and tinted purple,
and the blur effect adds an avatar-like shimmer.

### Limitations

- **Cross-family mob swaps** don't work well — weapon meshes are
  skinned to specific joints that differ between mob families. Gear
  weapons work on any humanoid mob because the joint is auto-remapped.
- **Block structure varies**: some mob families (gigas, some orcs) pack
  weapon meshes into the main body block rather than a separate weapon
  block. These can't be swapped with the current approach.
- Gear weapon blocks that lack an END section get one appended
  automatically during the swap.

### Skeleton weapon variants

| Model | Weapon | Block |
|-------|--------|-------|
| 564 | Scythe | "wep_" |
| 569 | Club | "c_cl" |
| 571 | Staff | "wep_" |

## Finding model IDs

Model IDs are in the `mob_pools.modelid` column as a 20-byte blob.
The actual model ID is bytes 2–3 as uint16 LE:

```sql
-- Find Tiger model IDs
SELECT poolid, name, CONV(HEX(SUBSTRING(modelid, 3, 2)), 16, 10) as mid
FROM mob_pools WHERE name LIKE '%Tiger%';
```

Or use `xi model json` to browse registered models.

### Model ID → File ID formula

| Range | Offset |
|-------|--------|
| 0–1499 | `modelid + 1300` |
| 1500–2999 | `modelid + 50295` |
| 3000–3499 | `modelid + 96907` |
| 3500+ | `modelid + 98239` |

Custom models use IDs 15000–30000 (default `MAX_ENTITY_MODELID`; override with
`XI_MAX_ENTITY_MODELID`). Requires `xi ftable expand` first — this expands
**all** base FTABLE/VTABLE files (not just ROM10) to 128,240 entries at the
default ceiling. Tables must stay the same size across roots (volume-direct
lookup + overlay shadow); if the base FTABLE is shorter than the overlay,
high file IDs are invisible even when FTABLE10 has them registered.

**Important:** Never restore base FTABLEs to their original size after
expanding. Zone injection works without expansion (zone file IDs are below
109,701), but entity models require it.

## Colour tips

| Effect | Options |
|--------|---------|
| Red variant | `--tint "#ff3300cc" --blend multiply` |
| Blue frost | `--hue 180 --saturation 30` |
| Dark shadow | `--lightness -40 --tint "#330066aa" --blend overlay` |
| Desaturated | `--saturation -60` |
| Golden | `--hue 40 --saturation 50 --lightness 10` |

**Multiply blend** is best for recoloring — it preserves the original
texture's shading, detail, and contrast while shifting the overall colour.

## Server-side usage

After injection, use the new model ID in spawn commands:

```lua
mob:setModelId(15000)  -- the injected model ID
```

Or reference via mob_pools for persistent spawns.

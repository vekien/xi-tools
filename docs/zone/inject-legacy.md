# Zone Injection Legacy

`xi zone inject` is a hidden compatibility command. New distributable zones
should use `xi zone new` plus `xi dats prepare` / `xi dats build`.


Create new zones by cloning existing ones with colour/lighting modifications.
Registers into FTABLE10 without touching original game files.

## Quick start

```bash
# Teal grasslands
xi zone inject "Verdant Sarutabaruta" --clone 115 --hue 120

# Red forests
xi zone inject "Crimson Ronfaure" --clone "West Ronfaure" --hue 240

# Dark city with purple fog
xi zone inject "Shadow Jeuno" --clone 244 --hue 180 --lightness -30 \
    --env-lightness -60 --fog-tint "#1a0a2a" --fog-end 80

# Colour overlay
xi zone inject "Dark Bastok" --clone "Bastok Mines" --tint "#330066aa" --blend overlay

# Dry run
xi zone inject "Test Zone" --clone 115 --hue 120 --dry-run
```

## Texture options

All texture adjustments are applied in-place to DXT colour endpoints
(no decode/encode, no quality loss) and paletted texture palettes.
Processing time: ~1.5s for a 17 MB HD zone DAT.

| Option | Type | Description |
|--------|------|-------------|
| `--hue N` | 0–360 | Rotate hue (120 = green shift, 240 = red shift) |
| `--saturation N` | -100 to 100 | Adjust colour saturation |
| `--lightness N` | -100 to 100 | Adjust brightness |
| `--tint #RRGGBB[AA]` | hex | Blend a colour onto all textures |
| `--blend MODE` | string | Blend mode: `normal`, `multiply`, `screen`, `overlay`, `add` |

## Environment options

Modify the 0x2F environment sections (ambient lighting, fog colour/distance).
These affect the overall scene lighting independent of textures.

| Option | Type | Description |
|--------|------|-------------|
| `--env-lightness N` | -100 to 100 | Darken/brighten ambient, sun, moon, fog colours |
| `--fog-tint #RRGGBB` | hex | Override fog colour (what distant objects fade to) |
| `--fog-end N` | float | Fog far distance in world units (lower = denser). Retail: 100–500 |
| `--fog-start N` | float | Fog near distance (default 0) |

### Environment section layout (0x2F)

Each 0x2F section holds indoor/outdoor lighting, fog, clear colour, draw distance,
and (outdoors) the procedural sky-dome rings. Offsets below are relative to the
section **data start** (after the section header). Two independent LightConfigs
(model + terrain), then sky fields:

```
+0x00  u32   indoors (1=indoor, 0=outdoor)
+0x0C  RGBA  sun colour (model)
+0x10  RGBA  moon colour (model)
+0x14  RGBA  ambient colour (model)
+0x18  RGBA  fog colour (model)
+0x1C  f32   fog far distance (model)   ← far BEFORE near
+0x20  f32   fog near distance (model)
+0x24  f32   diffuse multiplier (model)
+0x2C  RGBA  sun colour (terrain)
+0x30  RGBA  moon colour (terrain)
+0x34  RGBA  ambient colour (terrain)
+0x38  RGBA  fog colour (terrain)
+0x3C  f32   fog far distance (terrain)
+0x40  f32   fog near distance (terrain)
+0x44  f32   diffuse multiplier (terrain)
+0x4C  RGBA  clear colour
+0x58  f32   draw distance
+0x5E  u16   sky-dome spoke count
+0x68  f32   sky-dome radius (~2029.5)
+0x6C  8×RGBA  sky-dome ring colours (horizon → zenith)
+0x8C  8×f32   sky-dome ring elevations (0→1)
```

> **PS2 decompile names (`XiWeather` / `XiColorEnv` / `XiWorldEnv`, see
> [reference/ps2_decomp_crosscheck.md](../reference/ps2_decomp_crosscheck.md) §7):** `+0x04` point-light count, `+0x08` point-light table;
> per block `room_light_col`, `room_light_vec`, `ambient_col`, `fog_col`, `fog_far`,
> `fog_near`, `light_power`. When bit 0 of `+0x00` is set, the `+0x10`/`+0x30` slot is a
> signed-byte **light direction**, not a colour. World block: `+0x50` focus_far, `+0x54`
> focus_near (depth of field), `+0x58` clip_range, `+0x5C/+0x5D` focus step counts,
> `+0x5E` sphere V divisions, `+0x60` effect ambient colour, `+0x64` zone sound resource.

**Packed colour = RGBA byte order** (R at lowest address, then G, B, A). Top byte is
often `0x80`. This is **not** the same as per-vertex mesh colour, which is **BGRA**.
Sanity check: outdoor noon zenith rings should read blue-dominant. Fog far `== 0`
means fog disabled (discrete; do not treat as a tiny far plane). Not encrypted.

### Sky and weather

Outdoor sky is mostly **DAT-authored**, not a single engine-only gradient:

1. **Type-47 sky dome** — procedural gradient rings inside each 0x2F environment
   record (colours + elevations above). Drawn camera-attached, depth-write off.
   This is what sets the horizon→zenith colour; edit via 0x2F / `--env-*` / fog flags.

2. **Skybox / celestial meshes** — unplaced 0x2E sections (clouds, sun, moon, stars)
   with 0x20 textures, usually under `weat/<weather>/`. Float around the camera; **not**
   in the ZoneDef placement table. Driven as ambient effects / env shells.

3. **`zonetype` (server `zone_settings`)** — still matters for client outdoor/indoor
   behaviour and some clear/fog modes, but it is **not** “the only sky colour knob”.
   Prefer editing 0x2F + sky meshes for look; use zonetype when matching server flags
   (e.g. Dynamis-style dark).

| zonetype | Typical use |
|----------|-------------|
| 1–2 | Normal outdoor / town |
| 3 | Fog-oriented |
| 128 | Dynamis-style perpetual dark |

#### Skybox section names

| Prefix | Content | Notes |
|--------|---------|-------|
| `suny` | Sun/daytime sky | Outdoor zones |
| `fine` | Fine weather sky | DAT `weat/` tag (not the same as weather id 0) |
| `lf01`–`lf04` | Sky gradient layers | Per-weather |
| `clod`, `cld_` | Clouds | |
| `mist` | Mist/haze | |
| `moon` | Moon phases | `moonshap` + `kasa` |
| `star`, `sta2` | Star field | |
| `dark` | Dark sky (Abyssea) | Replaces `suny`/`fine` in Abyssea zones |

The prefix list used by xi-tools for sky identification:
```python
_SKY_PREFIXES = ("sun", "moon", "star", "clod", "cld", "cloud", "kamo", "suny", "sora", "dust")
```

**Note:** `suna` (sunakabe) is a wall texture, not sky — be careful with prefix matching.
Check internal texture names to distinguish.

#### Darkening the sky

- Crush sky **texture** brightness (`--lightness -85`) for cloud/sun/moon meshes
- Tint / shorten fog via `--fog-tint` / `--fog-end` (0x2F fog planes)
- Edit 0x2F dome ring colours for the procedural backdrop
- Set `zonetype = 128` when you need that server/client mode, not as a substitute
  for missing 0x2F data
- Permanent clouds weather (type 2) keeps cloud geometry up; fog weather (type 3)
  tends to haze it out

#### Abyssea-style sky swap

Abyssea zones use `dark`-prefixed skybox sections instead of `suny`/`fine`.
To swap skyboxes between zones:

1. Identify sky 0x2E (mesh) and 0x20 (texture) sections by name prefix
2. Remove the target zone's sky sections
3. Insert the donor zone's sky sections at the same position
4. The mesh references textures by internal name — both must be copied together
5. Copy the donor `weat/` 0x2F environments if you want matching dome/fog/lighting

### Weather system

Weather is stored in `zone_weather` as a blob of 2160 × uint16 values
(one per Vanadiel day, cycling). Each uint16 packs three 5-bit weather IDs:

```
Bit layout: 0 NNNNN CCCCC RRRRR
              normal common rare
```

**LandSandBoat runtime** rolls: 50% normal, 35% common, 15% rare (and may reschedule
on a short real-time timer). That roll is **server policy**, not a proven retail client
algorithm. Retail is often modelled as deterministic **normal**-slot weather with a
fixed multi-year cycle; a packed value of `0` is treated by some tools as “no entry /
carry forward prior day”, **not** as weather id 0. If you need retail-matching offline
prediction, do not assume LSB’s RNG matches live retail day-for-day.

**Weather IDs (LSB / server enum):** 0=None (no DAT tag), 1=Sunshine/`suny`, 2=Clouds/`clod`,
3=Fog/`mist`, 4=HotSpell/`dryw`, 5=HeatWave/`heat`, 6=Rain/`rain`, 7=Squall/`squl`,
8=DustStorm/`dust`, 9=SandStorm/`sand`, 10=Wind/`wind`, 11=Gales/`stom`, 12=Snow/`snow`,
13=Blizzard/`bliz`, 14=Thunder/`thdr`, 15=Thunderstorms/`bolt`, 16=Auroras/`aura`,
17=StellarGlare/`ligt`, 18=Gloom/`fogd`, 19=Darkness/`dark`.

Zone DATs also use a **`fine`** folder tag under `weat/` — that is a resource name, not
LSB id 0. See [events/weather.md](../events/weather.md).

**Permanent weather:** Set all 2160 entries to the same packed value.

```sql
-- Permanent clouds (weather 2): packed = (2<<10)|(2<<5)|2 = 0x0842
-- Permanent fog (weather 3): packed = (3<<10)|(3<<5)|3 = 0x0C63
-- Permanent darkness (weather 19): packed = (19<<10)|(19<<5)|19 = 0x4E73
```

Can also force weather in Zone.lua:
```lua
zoneObject.onInitialize = function(zone)
    zone:SetWeather(xi.weather.CLOUDS)
end
```

**Visual interaction:** Cloud weather (type 2) keeps skybox cloud geometry visible.
Fog weather (type 3) fades skybox geometry to the fog colour. For dark atmospheric
zones, clouds weather with dark-tinted skybox textures often looks better than fog.

## Zone registration

| Option | Description |
|--------|-------------|
| `--clone` | Source zone — name or ID (required) |
| `--zone-id N` | Target zone ID (default: auto-assign from 400+) |
| `--model-dat` | Override model DAT path |
| `--subdir N` | ROM10 subdirectory (default: 1) |

## How it works

1. Resolves the source zone's model, dialog, NPC, and event DATs via FTABLE
2. Copies or recolours the model DAT into `ROM10/<subdir>/<n>.DAT`
3. Clones dialog, NPC, and event DATs
4. Registers all four file IDs in `FTABLE10.DAT` / `VTABLE10.DAT`
5. Auto-assigns a zone ID from 400+ if not specified

### Zone file ID formulas

| DAT type | Zones < 256 | Zones ≥ 256 |
|----------|-------------|-------------|
| Model | `100 + zone_id` | `83891 + (zone_id - 256)` |
| Event | `5820 + zone_id` | Model + 1100 |
| Dialog | `6420 + zone_id` | Model + 1700 |
| NPC | `6720 + zone_id` | Model + 2600 |

## Server-side setup

### 1. Bump MAX_ZONEID

In `src/map/zone.h` and `scripts/enum/zone.lua`:

```diff
-    MAX_ZONEID = 300,
+    MAX_ZONEID = 512,
```

See `patches/zone_max_512.patch`. Rebuild the C++ server.

### 2. Add zone_settings

```sql
INSERT INTO zone_settings
  (zoneid, zonetype, zoneip, zoneport, name, music_day, music_night,
   battlesolo, battlemulti, restriction, tax, misc)
VALUES
  (400, 2, '127.0.0.1', 54230, 'Verdant_Sarutabaruta',
   113, 113, 101, 103, 0, 0.00, 3230);
```

Set `zonetype` to control sky rendering (128 for dark, 3 for fog).

### 3. Create zone scripts

```bash
mkdir scripts/zones/Verdant_Sarutabaruta
```

Create a minimal `IDs.lua` with the new zone ID:

```lua
zones = zones or {}
zones[400] = {
    text = { NOTHING_OUT_OF_THE_ORDINARY = 6404 },
    mob = {},
    npc = {},
}
return zones[400]
```

Copy `Zone.lua` and `DefaultActions.lua` from the source zone.

### 4. Zone name (in-game display)

The zone name string table `ROM/165/84.DAT` (d_msg, XOR 0xFF) must be
extended to include the new zone ID. Without patching it, the client
shows "Area: N".

### 5. Restart

Restart both server and client. FTABLE is loaded at client boot.

## Zone scaling

Scale all zone geometry by a uniform factor. Creates giant or miniature
versions of existing zones.

```python
from xi.zone.xi_inject import scale_zone_dat

# 5x Valley of Sorrows
scale_zone_dat(source_dat, output_dat, scale=5.0)
```

### What gets scaled

| Data | Section | Scaled |
|------|---------|--------|
| Mesh vertices | 0x2E | Yes — decrypt, scale XYZ, update bbox, re-encrypt |
| Object placements | 0x1C | Yes — position XYZ + draw distance |
| Space-tree bounds | 0x1C | Yes — all 8 bounding box corners per node |
| Collision grid | 0x1C | Partial — grid origin + cell size |
| Collision meshes | 0x1C | Yes — vertices + transform translations |
| Textures | 0x20 | No (unchanged) |
| Environment/fog | 0x2F | No (unchanged) |

### Usage

1. Clone the zone with `xi zone inject` or register manually
2. Scale the model DAT from the **original** source (not a recolored copy — recoloring changes bytes that encryption depends on)
3. If combining with recoloring, scale first, then recolor the scaled output

```python
# Scale from original, then recolor
scale_zone_dat(original_dat, output_dat, scale=5.0)
recolour_zone_dat(output_dat, output_dat, hue=160, tint='#aaddff66', ...)
```

### Important notes

- Zone mesh sections (0x2E) are encrypted. The scaling pipeline decrypts, modifies, and re-encrypts using `reencrypt_zone_mesh` (NOT `decrypt_zone_mesh` again — the encryption is NOT an involution, the pass order must be reversed)
- **Collision grid limitation**: collision mesh vertices and transforms are scaled correctly, but the collision grid map (which maps world positions to collision meshes) has fixed dimensions baked into the zone header's block sizes. After scaling, the grid only covers the original zone area — geometry outside this central region has no collision. Players will rubberband if they walk beyond the original zone bounds. Rebuilding the grid is a non-trivial RE task.
- Works best with outdoor zones. Indoor zones with tight corridors may have culling issues at large scales.
- Zone scripts and `zone_settings` must exist for the target zone ID, including the Lua enum entry in `scripts/enum/zone.lua`

## Limitations

- Zone IDs above 301 require MAX_ZONEID bump and server rebuild
- FTABLE10/VTABLE10 must be in the game directory (not XIPivot overlay)
- Zone file IDs (100–6900) fit within the retail FTABLE size (109,701
  entries), so zone injection does NOT require `xi ftable expand`.
  Entity model injection DOES — all base FTABLEs must be expanded
  (`xi ftable expand`; default ceiling modelid 30000 → 128,240 entries)
  for model IDs 15000+ to resolve. Never restore base FTABLEs to original
  size after expanding.
- **ROM subdir file limit**: FTABLE entries encode the file index as
  7 bits (0–127). Each ROM10 subdirectory can hold a maximum of 128
  DAT files. Exceeding this causes file index wrap-around — file 169
  in subdir 1 silently encodes as file 41 in subdir 2, pointing to
  the wrong DAT and causing invisible geometry or crashes. Tools must
  auto-bump to the next subdir when a directory reaches 128 files.
- Outdoor sky colour/lighting is primarily DAT-driven (0x2F dome + `weat/`
  sky meshes); `zonetype` is a server/client mode flag, not the only sky knob.
  Abyssea-style looks still need the matching skybox geometry + environments
- Character select zone name reads from a separate string table
  (`ROM/97/57.DAT` XISTRING format)

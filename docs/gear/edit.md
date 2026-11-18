# Gear Edit

Edit a gear model **in place** — currently recolours weapon and armour model
textures with targeted colour adjustments. Overwrites the model DAT under
`FFXI_DIR`, so every item that shares the model ID gets the new look and **no addon
is required**. Supports colour-range filtering to target specific parts of a
model (e.g. blade vs hilt).

> `gear edit` overwrites the existing model (original and edited cannot
> coexist). Minting a **new** model ID is a package/legacy workflow; see
> [`xi dats`](../dats/README.md) and the hidden compatibility notes in
> [`gear inject`](inject-legacy.md).
>
> `gear recolor` is kept as a hidden back-compat alias for `gear edit`.

## Quick start

```bash
# Green Ridill blade (only shift blue parts, preserve gold hilt)
xi gear edit HumeMale main 259 --hue 240 --hue-min 150 --hue-max 340 --saturation 30

# Red tint on gray metallic parts
xi gear edit HumeMale main 259 --tint "#bb2222aa" --blend overlay --sat-max 0.15

# Full weapon hue shift
xi gear edit HumeMale main 259 --hue 180

# Auto-detect race/slot/model from the DAT itself
xi gear edit ROM/33/17 --hue 200
```

## Identifying the model

Pass the model explicitly (`RACE SLOT MODEL_ID`) **or** a DAT path / file_id on
its own and the race/slot/model are auto-detected from the gear tables:

```bash
xi gear edit HumeFemale body 34 --hue 200   # explicit
xi gear edit ROM/33/17 --hue 200            # DAT path  (same model)
xi gear edit 10578 --hue 200                # raw file_id (same model)
```

## How it works

Gear models use the same 0x20 DXT/paletted texture format as entity and
zone DATs. The same DXT endpoint modification engine applies.

Gear models are per-race — each race has its own DAT for the same weapon.
Editing one race's model does not affect others.

## Finding model IDs

```sql
-- Find weapon model ID from item name
SELECT e.itemId, b.name, e.MId, e.slot
FROM item_equipment e
JOIN item_basic b ON e.itemId = b.itemid
WHERE b.name LIKE '%Ridill%';
```

Or use `xi gear json` to browse all registered gear models.

## Colour range targeting

Use `--hue-min`, `--hue-max`, `--val-min`, `--val-max`, `--sat-min`,
`--sat-max` to restrict the edit to a band of colours, so you can recolour
parts of a model independently. Pixels outside the band are left untouched.

| Filter | Type | Description |
|--------|------|-------------|
| `--hue-min/max` | 0–360 | Only affect pixels with hue in range (min > max wraps past 360) |
| `--val-min/max` | 0.0–1.0 | Only affect pixels with brightness in range |
| `--sat-min/max` | 0.0–1.0 | Only affect pixels with saturation in range |

### Example: Ridill blade vs hilt

Ridill's texture has two distinct colour regions:
- **Blade**: blue/gray (hue 150–340, plus low-saturation metallics)
- **Hilt**: gold/brown (hue 15–60)

To recolour only the blade:
```bash
xi gear edit HumeMale main 259 --hue 240 --hue-min 150 --hue-max 340
```

## Per-texture adjustments, skips and several ranges

Three repeatable options pick different adjustments for different textures. A texture's
name is the 16 characters after its type byte (`suny    sky01`: category, then name), as
`xi tex list ROM/0/124` prints it; a PATTERN matches it case-insensitively,
with `*` and `?` wildcards.

| Option | Example | What it does |
|---|---|---|
| `--texture PATTERN:KEY=VALUE,…` | `--texture "suny*:lightness=-70"` | Textures matching PATTERN get these keys instead of the top-level ones (the others still apply). The first matching `--texture` wins. Keys: `hue`, `saturation`, `lightness`, `tint`, `blend`, `hue_min` … `val_max` |
| `--skip PATTERN` | `--skip "lf*"` | Textures matching PATTERN are left byte for byte |
| `--range KEY=VALUE,…` | `--range hue_min=150,hue_max=340,hue=240` | A pass limited to a colour range. Several `--range`s run in order, each on the colours the one before left, and replace the top-level `--hue` / `--saturation` / `--lightness` / `--tint` |

A texture none of the adjustments change keeps its bytes exactly. (Before, a call with
only fog or environment options still re-packed every DXT colour through 8 bits, which
loses the low bits of dark colours.)

`--texture "environment:KEY=VALUE,…"` adjusts the 0x2F environment sections instead
(`hue`, `saturation`, `lightness`, `tint`, `blend`, `fog_tint`, `fog_end`, `fog_start`).

```bash
xi zone inject "Shadow Jeuno" --clone 244 --hue 180 --saturation 20 --lightness -30 \
    --texture "suny*:lightness=-70" --texture "clod*:hue=180,lightness=-60" \
    --texture "environment:lightness=-60,fog_tint=#1a0a2a,fog_end=80,fog_start=0"
xi gear edit HumeMale main 259 --range hue_min=150,hue_max=340,hue=240 --range val_max=0.2,lightness=40
```

These follow dats' zone builder and Loxley's LPS (`modules/equipment/textures.py`,
LPS-modules `mobs`), ported with his permission. Verified on CatsEyeXI's six recoloured
zones (`content/zones/*.json` in its dats repo, the same settings as `texture_overrides`):
the models come out within one 5/6-bit step per DXT colour endpoint, and ±2 per palette
and environment byte, of the files that builder made.

## Races

| Race | Name |
|------|------|
| HumeMale | Hume Male |
| HumeFemale | Hume Female |
| ElvaanMale | Elvaan Male |
| ElvaanFemale | Elvaan Female |
| TaruMale | Tarutaru Male |
| TaruFemale | Tarutaru Female |
| Mithra | Mithra |
| Galka | Galka |

## Slots

`main`, `sub`, `ranged`, `head`, `body`, `hands`, `legs`, `feet`

## Limitations

- Gear models are per-race — edit each race separately
- No FTABLE injection needed — the overlay replaces the existing model DAT
- The edit replaces the original model for ALL items that share the same
  model ID. To create a truly new model (original preserved), use a package
  action through [`xi dats`](../dats/README.md); the old inject flow is hidden.

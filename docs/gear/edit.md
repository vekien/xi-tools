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

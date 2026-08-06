# xi gear json

Builds a complete list of every gear model entry for all 8 playable races
across all 9 equipment slots (face, head, body, hands, legs, feet, main, sub,
ranged). Each entry maps a `model_id` to a `file_id` and resolved DAT path via
the FTABLE.

Source data is ported from Atom0s's reverse-engineering of `FFXiMain.dll` —
the same lookup tables the game uses to resolve item model IDs to DAT files.

---

## Usage

```
uv run xi gear json [RACE] [SLOT] [--search TEXT] [--output PATH]
uv run xi gear search <query> [--race RACE] [--slot SLOT]
```

| Option | Description |
|---|---|
| `RACE` | Optional race filter, e.g. `HumeFemale` |
| `SLOT` | Optional slot filter, e.g. `body`, `main`, `ranged` |
| `--search TEXT` | Filter the JSON rows by text |
| `--output PATH` | Write JSON to a file instead of stdout |

The old `xi gear list` command remains as a hidden compatibility alias.

---

## Races

| Name | Notes |
|---|---|
| `HumeMale` | |
| `HumeFemale` | |
| `ElvaanMale` | |
| `ElvaanFemale` | |
| `TaruMale` | |
| `TaruFemale` | |
| `Mithra` | Female only race |
| `Galka` | Male only race |

---

## Example output

```
uv run xi gear json
```

```
Loaded ROM1 tables  (109,701 entries)
...
Loaded ROM10 tables  (118,240 entries)

Found 47,760 gear model entries across 8 races

  HumeMale          5,970 entries
  HumeFemale        5,970 entries
  ElvaanMale        5,970 entries
  ElvaanFemale      5,970 entries
  TaruMale          5,970 entries
  TaruFemale        5,970 entries
  Mithra            5,970 entries
  Galka             5,970 entries
```

Weapons only:

```
uv run xi gear json --search main
```

```
Found 20,952 gear model entries across 8 races  (slots: main, ranged, sub)

  HumeMale          2,619 entries
  ...
```

---

## JSON output

One file per race. Each entry:

```json
{
  "slot":     "head",
  "model_id": 0,
  "file_id":  7112,
  "rom":      1,
  "dat":      "ROM/27/119.DAT"
}
```

`model_id` is the value stored in the item database (e.g. `item.head` in the
LSB item table). Use `race` + `slot` + `model_id` to look up the DAT for any
equipped item.

Use `--output` when you want a file; otherwise JSON is printed to stdout.

---

## Slot breakdown (HumeMale example)

| slot | entries | notes |
|---|---|---|
| face | 32 | face style variants |
| head | 665 | |
| body | 663 | |
| hands | 663 | |
| legs | 663 | |
| feet | 665 | |
| main | 1,179 | weapon — **race-specific DATs** |
| sub | 1,184 | weapon — **race-specific DATs** |
| ranged | 256 | |

Weapon slots (main/sub/ranged) have different file_ids per race. The same
`model_id` on a Hume and a Galka resolves to a different DAT — each race's
skeleton requires its own mesh.

---

## How it works

Each race has a 432-byte lookup table in `FFXiMain.dll` (9 slots × 6 groups ×
8 bytes). Each group is a `(base_file_id, count)` pair. The game maps a gear
`model_id` cumulatively across groups to get the final `file_id`:

```
model_id 0..count1-1    → base_fid1 + model_id
model_id count1..count1+count2-1 → base_fid2 + (model_id - count1)
...
```

`xi gear json` expands all groups for all races, collects every unique
`file_id`, resolves them in a single FTABLE scan, then assembles the results.

---

## Related commands

- **`xi gear export`** — export a gear model to GLB/FBX for editing
- **`xi gear import`** — re-import an edited GLB back into a gear DAT
- **`xi ftable lookup`** — look up a single file_id to get its DAT path and header bytes
- **`xi mesh export`** — equivalent for monster/NPC/entity models

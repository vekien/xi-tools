# xi object set-placement

Move / rotate / scale a **single existing** placement by mesh name. The per-object
equivalent of one [`zone import-json`](../zone/import-json.md) *modify* op — it patches the
full transform of the matched record and re-homes it in the nearest space-tree leaf.

This is what the web editor's **Export Commands** emits for each moved object.

---

## Usage

```
uv run xi object set-placement <dat> <name> --pos X Y Z [--rot RX RY RZ] [--scale SX SY SZ]
```

| Argument / Option | Description |
|---|---|
| `<dat>` | Zone DAT or ROM-relative spec (e.g. `ROM/1/41`) |
| `<name>` | Mesh name of the placement to move (the **first** record with this name) |
| `--pos X Y Z` | New FFXI-space position (required) |
| `--rot RX RY RZ` | Rotation in radians (default `0 0 0`) |
| `--scale SX SY SZ` | Scale (default `1 1 1`) |

```bash
uv run xi object set-placement ROM/1/41 block03 --pos -8.8 0 -7.7 --rot 0 1.04 0
```

---

## Notes

- **Matches by name, first occurrence.** If several placements share `<name>` (e.g.
  duplicated copies), this always targets the first record in the `0x1C` array — it can't
  single out a specific copy. For many same-named objects, edit in the web editor and use
  [`zone import-json`](../zone/import-json.md) (which still matches by name, but lets you see
  what you're moving).
- It does **not** add or remove objects — the object count is unchanged. To add, use
  [`object import`](import.md) / [`object clone`](clone.md).
- Layers onto prior edits and writes the DAT in place; `xi zone reset` restores
  the original.

---

## Related commands

- **[`zone import-json`](../zone/import-json.md)** — batch-apply many moves/adds/deletes from the editor
- **[`object swap-placement`](swap-placement.md)** — also change the *mesh* of a slot, not just its transform
- **[`object json`](json.md)** — list placements and their names

# xi object clone

Duplicate an **existing** placement of a mesh that's already in the zone, at a new world
position. The cheap, safe form of "add an object" — the mesh (`0x2E`) and a template
record already exist, so it just appends a new placement and registers it.

The new index is registered across all **four** `0x1C` visibility structures (space-tree,
leaf bounds, collision transform, culling tables), so the clone renders from every camera
position — same as [`object import`](import.md), where those structures are documented.

---

## Usage

```
uv run xi object clone <dat> <mesh_id> --pos X Y Z [--count N]
```

| Argument / Option | Description |
|---|---|
| `<dat>` | Zone DAT or ROM-relative spec (e.g. `ROM/1/41`) |
| `<mesh_id>` | Mesh to duplicate — **must already have at least one placement** in the zone (that record is the template; its rotation/scale/flags are copied) |
| `--pos X Y Z` | World position (raw FFXI coords) for the new copy (required) |
| `--count N` | Add this many copies, all at `--pos` (default `1`); nudge them apart afterwards |

```bash
# drop another gaitou01 lamp into the plaza
uv run xi object clone ROM/1/41 gaitou01 --pos -5 0 -5
```

---

## Notes

- The mesh must already exist in the zone. To bring in a **brand-new** mesh, use
  [`object import`](import.md).
- Rotation/scale are copied from the nearest/template placement — to re-orient a copy
  afterwards, use [`object set-placement`](set-placement.md) (or the web editor).
- **No collision mesh** is added — clones are visual-only (walk-through).
- Layers onto prior edits; **re-running stacks another copy**. Use `xi zone reset` to
  start from pristine.

---

## Related commands

- **[`object import`](import.md)** — add a brand-new mesh (not just a copy)
- **[`object swap-placement`](swap-placement.md)** — repoint an existing slot instead of appending
- **[`object set-placement`](set-placement.md)** — move/rotate/scale an existing placement
- **[`zone import-json`](../zone/import-json.md)** — the web editor's copy/paste exports an `add` op that does the same thing

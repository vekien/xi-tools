# xi object swap-placement

Repoint an **existing** placement slot at a different mesh + transform, **in place** —
reusing that object's index. Unlike [`object import`](import.md) / [`object clone`](clone.md)
(which append a new object), `swap-placement` overwrites a slot you already have, so no
section growth is needed for the record itself.

It still re-runs the visibility registration (leaf bounds, collision transform, and
culling-table membership for the slot's new region), so the swapped object renders from
every camera position. See *Placement registration* in [import.md](import.md).

---

## Usage

```
uv run xi object swap-placement <dat> <index> <mesh_name> --pos X Y Z [--rot RX RY RZ] [--scale SX SY SZ]
```

| Argument / Option | Description |
|---|---|
| `<dat>` | Zone DAT or ROM-relative spec (e.g. `ROM/1/41`) |
| `<index>` | Object slot index to overwrite — find it with [`object json`](json.md) |
| `<mesh_name>` | Mesh to render at that slot; **must already exist** as a `0x2E` section in the DAT |
| `--pos X Y Z` | New FFXI-space position (required) |
| `--rot RX RY RZ` | Rotation in radians (default `0 0 0`) |
| `--scale SX SY SZ` | Scale (default `1 1 1`) |

```bash
# turn slot 600 into a gaitou01 lamp, 1u from the real one at -19.96
uv run xi object swap-placement ROM/1/41 600 gaitou01 --pos -18.96 0 -3.74 --rot 3.142 1.044 3.142
```

---

## When to use it

- **Repurpose a throwaway object** — e.g. an unwanted prop slot becomes the mesh you want,
  without growing the zone.
- **Test placement** of a mesh that's already in the zone without appending a new object.

## Notes

- **The overwritten object is gone** — the slot now renders `<mesh_name>` instead. Pick a
  slot you don't mind losing (`object list` shows what each index currently is).
- Layers onto prior edits and writes the DAT in place; `xi zone reset` restores
  the original.

---

## Related commands

- **[`object import`](import.md)** — add a brand-new mesh + placement (full detail on the four registration structures)
- **[`object clone`](clone.md)** — duplicate an existing mesh's placement at a new spot
- **[`object set-placement`](set-placement.md)** — just move/rotate/scale a placement (keeps its mesh)
- **[`object json`](json.md)** — find slot indices

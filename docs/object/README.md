# xi object

Add, edit, inspect, and remove **individual zone objects** (placements) in a zone DAT.
Mirrors the top-level `xi object` command group. The old `xi zone object`
group remains as a hidden compatibility alias.

| Command | Doc | What it does |
|---|---|---|
| `object json` | [json.md](json.md) | List every placement as structured JSON |
| `object export` | [export.md](export.md) | Export one named mesh to GLB (+ `.zone2e` / FBX) for editing |
| `object import` | [import.md](import.md) | Add a **brand-new** mesh + placement from a GLB |
| `object clone` | [clone.md](clone.md) | Duplicate an **existing** mesh's placement at a new spot |
| `object replace` | [replace.md](replace.md) | Replace a mesh's geometry from an edited GLB (any topology) |
| `object set-placement` | [set-placement.md](set-placement.md) | Move/rotate/scale one existing placement |
| `object swap-placement` | [swap-placement.md](swap-placement.md) | Repoint an existing slot at a different mesh, in place |
| `object delete` | [delete.md](delete.md) | Blank a placement so the engine skips it |

## Adding vs editing

- **Editing** existing placements (`set-placement`, `swap-placement`, `delete`,
  `replace`) is cheap — counts/offsets stay valid.
- **Adding** placements (`import`, `clone`) appends a new object index, which must be
  registered across **four** `0x1C` visibility structures or the object renders
  incorrectly / vanishes by camera angle. That mechanism — and the reverse-engineering
  story behind it — is documented in **[import.md → Placement registration](import.md#placement-registration--the-four-structures)**.

## Related

- [`../zone/import-json.md`](../zone/import-json.md) — apply a whole change-set from the web editor (the GUI way to do all of the above)
- [`../zone/format.md`](../zone/format.md) — the `0x1C` binary format these commands manipulate

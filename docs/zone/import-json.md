# xi zone import-json

Apply a JSON change-set exported from the **web level editor** back to a zone DAT.
Handles placement moves/deletes and VFX add/remove/modify in a single command.

---

## Usage

```
uv run xi zone import-json <changes_json> [dat_path] [--dry-run]
```

| Argument / Option | Description |
|---|---|
| `<changes_json>` | Path to the `zone-changes.json` exported from the web editor |
| `[dat_path]` | Zone DAT (optional — falls back to the `zone` field in the JSON) |
| `--dry-run` | Print what would be applied without writing anything |
| `--pivot` | After applying, copy the modified DAT to `FFXI_PIVOT_DIR` |
| `--debug` | Print per-stage diagnostics (section insert position vs the `end\0` terminator, mesh/texture import, placement records, and the four visibility structures for each added object) |

---

## Typical workflow

```
1. Open the web editor:  uv run xi gui zone
2. Load a zone, make edits (move/rotate/scale/delete objects, move VFX)
3. Changes > Export JSON  →  zone-changes.json
4. uv run xi zone import-json zone-changes.json
```

---

## JSON format

```json
{
  "zone": "game/ROM/1/41.DAT",
  "placements": [
    { "op": "modify", "name": "block03", "pos": [x,y,z], "rot": [rx,ry,rz], "scale": [sx,sy,sz] },
    { "op": "add",    "name": "block03", "pos": [x,y,z], "rot": [rx,ry,rz], "scale": [sx,sy,sz] },
    { "op": "add",    "name": "ka_s000", "pos": [x,y,z], "rot": [rx,ry,rz], "scale": [sx,sy,sz],
      "sourceZone": "ROM2/0/25.DAT", "sourceName": "ka_s000" },
    { "op": "add",    "name": "my_statue", "pos": [x,y,z], "rot": [rx,ry,rz], "scale": [sx,sy,sz],
      "glb": "workspaces/ROM_1_41/statue.glb", "shade": 0.5, "ao": false, "opaque": false, "doubleSided": false },
    { "op": "delete", "name": "hasi" }
  ],
  "vfx": [
    { "op": "modify", "id": "seap", "pos": [x,y,z] },
    { "op": "remove", "id": "tki1" },
    { "op": "add", "source_id": "tki1", "new_id": "tki9", "pos": [x,y,z] }
  ]
}
```

`id` for VFX = the 4-char section FourCC stored in the generator header (shown in the
web editor's VFX panel and in `xi fx json`).

---

## Operations

### Placements

| Op | Behaviour |
|---|---|
| `modify` | Decrypt → patch the full TRS at fixed offsets → re-home in the nearest space-tree leaf → re-encrypt. The single-object CLI equivalent is **`xi object set-placement <dat> <name> --pos … --rot … --scale …`** |
| `add` | Duplicate an existing placement of `name` (same mesh) at a new TRS (editor copy/paste). Registers the new index across **all four** visibility structures (space-tree leaf, mesh-bbox bounds, collision transform, culling tables) — same as `object import`, so it renders from every camera position |
| `add` *(cross-zone)* | With `sourceZone` (+ optional `sourceName`), copy the `0x2E` ZoneMesh **and its `0x20` textures** from another zone DAT into this one, renamed via `_xi_prefixed` (`foo` → `xi_foo`; a name starting with `_`/`#` keeps that byte → `_jag_w02_m` → `_xi_jag_w02_m`), then place it. Same four-structure registration. See the gotcha below. |
| `add` *(GLB import)* | With `glb` (a `.glb` path), inject a **brand-new** mesh from disk + place it — same as [`object import`](../object/import.md). Material fields: **`shade`** (brightness ×, `1.0` = neutral), **`ao`** (bake AO self-shadow, default **false**), `aoFloor`, `opaque` (force texture opaque), `doubleSided` (`0x2000`). Normals are auto-normalized on import — see [object import → Shading & brightness](../object/import.md#shading--brightness). Copies sharing one import are grouped by `xiId`. |
| `delete` | Decrypt → zero the 16-byte mesh_id → re-encrypt (record stays, engine renders nothing). CLI: `xi object delete <dat> <name>` |

The editor exports the **full** `pos`/`rot`/`scale` (not just the component that
changed) at float32-lossless precision for every moved/rotated/scaled object, so
re-import reproduces the exact transform.

> A plain `add` duplicates an *existing* mesh's placement. A cross-zone `add` (with
> `sourceZone`) brings in a **brand-new** mesh from another zone — equivalent to
> [`xi object import`](../object/import.md) but driven from the editor's JSON.

### Cross-zone add — section placement gotcha

Imported `0x2E`/`0x20` sections **must be spliced in before the zone's trailing `end\0`
terminator run** (the contiguous `type 0x00`, 16-byte sections at EOF). The game's zone
loader stops enumerating sections at the first `end\0`, so anything appended *past* it is
dead space: the object renders fine in the web editor (whose loader walks to EOF) but is
**invisible in-game**. `import-json` locates the terminator run and inserts ahead of it;
run with `--debug` to confirm `all imported sections precede terminator: PASS`.

### VFX

| Op | Behaviour |
|---|---|
| `modify` | Patch position and/or scale in the `0x05` section body |
| `remove` | Splice the `0x05` section out entirely |
| `add` | Duplicate an existing `0x05` section with a new FourCC and position. With `source_dat` (+ `source_offset`) the donor comes from another zone DAT together with its dependency sections (textures, meshes, SeSep). `new_id` is optional: when absent the copy is auto-named (`l_01` → `l_00`) and the bridge **pins that id back into the change-set's op** (`_export`), so every later reset-from-pristine publish re-creates the copy under the same id and the editor can adopt the baked copy by id on reload (a pinned id that already exists in a non-reset target falls back to a fresh one). |
| `add` *(point light)* | A donor whose `StandardSetup` linked type is `0x47` / carries `PointLightParams 0x58` is a point light. The generator alone never lights anything in the client: the copy's FourCC is also written into the `0x1C` **light table** (header `+0x18`, 256 × 0x4C) and referenced from every placement whose transformed mesh bbox lies within the light's range (record `+0x54`, max four lights per object — objects already using all four are reported). See [format.md → Light table](format.md#light-table-pointlightoff0x18). |

---

## Also see: Export Commands

The web editor's **Changes → Export Commands** button produces
`zone-changes.commands.txt` — every change as an **individual** CLI command you can run
or read one by one, instead of the single batch `import-json`:

| Change | Emitted command |
|---|---|
| placement move/rotate/scale | `xi object set-placement <dat> <name> --pos … --rot … --scale …` |
| placement duplicate (copy/paste) | `xi object clone <dat> <name> --pos …` (rot/scale via a follow-up `set-placement`) |
| placement delete | `xi object delete <dat> <name>` |
| VFX | `xi fx set` / `xi fx delete` / `xi fx copy` |

---

## Related commands

- **`xi object delete`** — quick single-object deletion by name
- **`xi fx delete`** — remove individual VFX by FourCC
- **`xi fx set`** — modify VFX position / scale / color individually
- **`xi fx copy`** — duplicate / transplant a VFX
- **`xi gui zone`** — start the web editor that generates the JSON

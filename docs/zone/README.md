# xi zone

Tools for FFXI **zones** — the static area geometry, placements, and effects baked into
a zone DAT (`ROM/<dir>/<dat>.DAT`). Mirrors the `xi zone` command group.

## Whole-zone

| Command | Doc | What it does |
|---|---|---|
| `zone json` | [zones.md](zones.md) | List every FFXI zone and its DAT path (via FTABLE) |
| `zone json --dev` | [prototype-zones.md](prototype-zones.md) | Also list the unreleased dev/prototype maps in `ROM/0/` — read this before parsing or publishing to one |
| — | [prototype-collision.md](prototype-collision.md) | Worked example: getting a `0x54` prototype zone playable — stride conversion, collision limits, what not to try |
| `zone export` | [export.md](export.md) | Export a whole zone (meshes + placements + textures) to GLB/FBX |
| `zone import` | [import.md](import.md) | Import an edited whole-zone GLB (move/rotate/scale/delete + mesh-merge) |
| `zone import-json` | [import-json.md](import-json.md) | Apply a JSON change-set from the web level editor |
| `gui zone` | — | Serve the browser-based [web level editor](../../web/leveleditor/README.md) |
| `zone reset` | [reset.md](reset.md) | Restore a zone DAT to pristine (undo all edits) |
| `zone new` | [templates.md](templates.md) | Create a custom zone (**requires** `--template <id>`; see [templates.md](templates.md)); optional `--sky <DAT>` to splice atmosphere |
| `zone delete` | — | Remove a custom zone (ID ≥ 400) — deletes DAT, zeros FTABLE10, prints server SQL |
| `zone build-from-manifest` | — | Assemble a custom zone from a Godot designer `build_manifest.json`; reads biome + size from the manifest |
| `zone json --fx` | [../fx/README.md](../fx/README.md) | Inspect the zone's `0x05` VFX generators as JSON |
| `zone export --collision` | [collision.md](collision.md) | Export the player-collision mesh (MZB) to `.collision.obj` + `.mtl` + `.collision.json` |
| `zone import --add-collision` | [collision.md](collision.md) | Append new collision blockers from an authored `.obj` |
| `zone navmesh` | [navmesh.md](navmesh.md) | Bake a server navmesh (`.nav`) from the zone's collision (native Recast/Detour) |
| `zone navmesh-info` | [navmesh.md](navmesh.md) | Inspect/validate a Detour `.nav` (ours or stock) |

## Individual objects — `object …`

The top-level [`object`](../object/README.md) group adds, edits, inspects, and removes single
placements: `json`, `export`, `import`, `clone`, `replace`, `set-placement`,
`swap-placement`, `delete`. The old `xi zone object` group remains as a hidden
compatibility alias.

## Reference

- **[format.md](format.md)** — the FFXI zone binary format (`0x2E` mesh, `0x1C` ZoneDef,
  decryption, the visibility structures).
- **[subareas.md](subareas.md)** — sub-areas (shop / building interiors): the `0x36` trigger
  volumes, the placeholder file-id link, interior-DAT resolution, and the editor feature.
- **[collision.md](collision.md)** — player-collision mesh (MZB): export, edit, and append new blockers.
- **[navmesh.md](navmesh.md)** — server navmesh (`.nav`): bake from collision via the bundled
  native Recast/Detour lib, install, and validate. Built lib lives in
  [../../misc/tools/xi-navmesh/](../../misc/tools/xi-navmesh/README.md).

## Typical workflows

```bash
# Browse + edit visually, then write back
uv run xi gui zone                       # move/rotate/scale, copy/paste, delete
#   → Changes ▸ Export JSON ▸ zone-changes.json
uv run xi zone import-json zone-changes.json

# Add a new prop from a model
uv run xi object export ROM/1/41 gaitou01     # get a GLB to base it on
uv run xi object import ROM/1/41 gaitou01.glb --name lamp --pos -8.8 0 -7.7 --rot 3.142 1.044 3.142

# Export collision mesh + edit + append blockers
uv run xi zone export ROM/1/41 --collision  # <stem>.collision.obj overlays the zone .glb
# author new blocker geometry in DCC (same (-x,-y,z) frame), export as blocker.obj
uv run xi zone import ROM/1/41 --add-collision blocker.obj

# Bake a server navmesh from the (edited) collision, then validate it
uv run xi zone navmesh ROM/1/41             # -> exports/zone/rom/1/41/41.nav
uv run xi zone navmesh-info exports/zone/rom/1/41/41.nav
#   copy to the server as navmeshes/<ZoneName>.nav (e.g. Lower_Jeuno.nav)

# Start over
uv run xi zone reset ROM/1/41
```

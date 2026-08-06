# Zone navmesh (server pathfinding)

Bake a **server-side navmesh** (`.nav`) for a zone from its collision mesh —
entirely within xi-tools, via our own native Recast/Detour library (no external
NavMesh Builder / FFXINAV).

> The navmesh is what the **LandSandBoat / CatsEyeXI map server** pathfinds mobs,
> NPCs, and trusts on (`navmeshes/<ZoneName>.nav`). It is a *separate* artifact
> from the **client** collision mesh (which blocks the player — see
> [collision.md](collision.md)). Both are derived from the same `0x1C` collision
> triangle soup, so a navmesh built here automatically reflects your custom
> collision edits.

---

## What it is

The `.nav` is a **Detour `NAVMESHSET`** file (the standard RecastNavigation save
format — the same one the stock server meshes use). It contains a tiled navigation
mesh: walkable polygons derived from the zone geometry that Detour runs A* over.

xi produces it with this stack:

```
xi (Python)  ──ctypes──▶  xi_navmesh.dll  ──links──▶  Recast + Detour (vendored)
  xi_navmesh.py               our extern "C" wrapper        misc/tools/xi-navmesh/
```

- **Recast** voxelizes the collision triangle soup and extracts walkable surface.
- **Detour** turns that into navmesh tiles and is the on-disk format.
- Our wrapper (`misc/tools/xi-navmesh/xi_navmesh.cpp`) runs the standard
  RecastDemo "Tile Mesh" build and serializes the `NAVMESHSET`. It's our own code
  over a vendored, zlib-licensed copy of RecastNavigation; it does not link
  FFXINAV. See [../../misc/tools/xi-navmesh/README.md](../../misc/tools/xi-navmesh/README.md).

---

## One-time: build the native library

Needs a C++ toolchain (MSVC / clang / gcc) + CMake ≥ 3.15.

```sh
cd misc/tools/xi-navmesh
cmake -B build
cmake --build build --config Release
```

This produces `xi_navmesh.dll` (Windows) / `.so` (Linux) under `build/`.
`xi zone navmesh` finds it automatically (`build/Release/`, `build/Debug/`, or
`build/`). `build/` is gitignored — rebuild whenever you pull changes to the
native sources.

---

## Build a navmesh

```bash
xi zone navmesh ROM/1/41
```

Decodes the zone's `0x1C` collision, converts it to Detour space, and writes
`exports/zone/<rom>/<stem>.nav`. It reads your **edited** DAT, so custom collision
(blockers, etc.) is included.

### Options

| Flag | Default | Effect |
|------|--------:|--------|
| `--output PATH` | `exports/zone/<rom>/<stem>.nav` | Where to write the `.nav` |
| `--agent-radius` | `0.3` | Recast agent radius in yalms. `0.3` = stock mob profile; ~`0.7` for player-movement-style meshes |
| `--agent-max-climb` | `0.5` | Max step/climb height the agent can traverse |
| `--cell-size` | `0.40` | Recast voxel cell size |
| `--tile-size` | `256` | Tile size in cells (tile world size = `cell-size × tile-size` = 102.4) |

The defaults match the stock server navmeshes (cell 0.4, agent radius 0.3, tile
256 → `tileWidth` 102.4, `maxTiles` 64, `maxPolys` 65536).

### Coordinate frame

Vertices are converted FFXI world → **Detour space `(x, -y, -z)`** (the server's
`CNavMesh::ToDetourPos`: negate Y and Z). This is handled automatically; it's why
a navmesh built here lines up with the server's coordinate convention.

---

## Install on the server

Copy the result to the server's `navmeshes/<ZoneName>.nav`. The server keys the
file by `zone_settings.name` (spaces → underscores), e.g. Lower Jeuno →
`Lower_Jeuno.nav`:

```sh
cp exports/zone/rom/1/41/41.nav  /path/to/server/navmeshes/Lower_Jeuno.nav
```

(On CatsEyeXI, `<LSB_DIR>/navmeshes` is symlinked to `xiNavmeshes/`.) **Back up
the stock `.nav` first** if you're overwriting one.

---

## Inspect / validate a `.nav`

```bash
xi zone navmesh-info exports/zone/rom/1/41/41.nav        # ours
xi zone navmesh-info /path/to/navmeshes/Lower_Jeuno.nav  # stock
xi zone navmesh-info <nav> --tiles                       # per-tile breakdown
```

Validates the `NAVMESHSET` magic/version, walks every tile (each must be a `DNAV`
v7 Detour tile), checks the file parses exactly, and reports tile / polygon /
vertex counts, origin, and tile/poly limits. Works on any `.nav`.

Example — built vs stock for Lower Jeuno (same origin/params confirms correctness):

```
41.nav:          VALID  version 1  tiles 9   polys 161  verts 434
                 origin (-336.51,-13.11,-792.67)  tileWidth 102.4  maxTiles 64  maxPolys 65536
Lower_Jeuno.nav: VALID  version 1  tiles 21  polys 826  verts 1919
                 origin (-336.51,-13.11,-792.67)  tileWidth 102.4  maxTiles 64  maxPolys 65536
```

---

## Known limitation: sub-region collision

The collision decode currently reads **only the main zone's `0x1C` collision**, not
**sub-region model collision** (the `RID 0x36` referenced model DATs — building
interiors, bridges, etc. — that the stock server meshes also include).

- **Custom / new zones** (no sub-regions) → complete.
- **Existing complex zones** → under-covered vs stock (e.g. Lower Jeuno: 161 polys /
  9 tiles here vs 826 / 21 stock — same bounds, but the stock includes sub-region
  interiors where mobs walk). Mobs path the main areas but not sub-region interiors.

Planned: extend the collision gather to follow `RID 0x36`, load the referenced
model DATs, and merge their `0x1C` collision (with transforms) so existing zones
match stock coverage.

---

## Format reference

`NAVMESHSET` (little-endian; **32-bit poly/tile refs** — `DT_POLYREF64` OFF):

```
NavMeshSetHeader (40 bytes):
  int   magic        = 'MSET'  (on-disk bytes 'TESM')
  int   version      = 1
  int   numTiles
  dtNavMeshParams:   float orig[3]; float tileWidth; float tileHeight;
                     int maxTiles; int maxPolys
per tile (× numTiles):
  NavMeshTileHeader (8 bytes):  unsigned int tileRef;  int dataSize
  <dataSize bytes>  raw dtMeshTile, starts with dtMeshHeader magic 'DNAV'
                    (on-disk bytes 'VAND'), version 7
```

The server loader is `<LSB_DIR>/src/map/navmesh.cpp` (`CNavMesh::load`). See
[format.md](format.md) for the zone DAT / collision side, and
[collision.md](collision.md) for the client collision mesh these are built from.

# xi-navmesh

A small native library that bakes a **server-compatible FFXI navmesh** (a Detour
`NAVMESHSET` `*.nav`) from a zone's collision triangle soup. Driven from Python by
`xi zone navmesh <dat>`.

It's our own `extern "C"` wrapper (`xi_navmesh.cpp`) around a bundled copy of
**RecastNavigation** (Recast + Detour). xi calls one function:

```c
int xi_build_navmesh(const float* verts, int nverts,   // nverts*3 floats, Detour space (x,-y,-z)
                       const int*   tris,  int ntris,     // ntris*3  vertex indices
                       const XiNavSettings* settings,
                       const char*  out_path);            // writes the .nav here
// returns tiles written (>=0), or negative on error
```

The build pipeline is the standard RecastDemo "Tile Mesh" sample (rasterize →
filter → regions → contours → poly mesh → detail → `dtCreateNavMeshData` per tile),
serialized as the `NAVMESHSET` the server's `CNavMesh::load` reads. It was
cross-referenced against xenonsmurf's FFXINAV `NavMeshBuilder.cpp`, but links no
external project.

## Build

Needs a C++ toolchain (MSVC / clang / gcc) + CMake ≥ 3.15.

```sh
cd misc/tools/xi-navmesh
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
```

Produces `xi_navmesh.dll` (Windows) / `xi_navmesh.so` (Linux) under `build/`
(`build/Release/` for MSVC). `xi zone navmesh` looks for it there automatically.

## Compatibility

- **Do not** define `DT_POLYREF64` — Detour stays 32-bit (8-byte tile headers),
  matching the LandSandBoat/CatsEyeXI server's `.nav` format.
- Output is the standard RecastDemo `NAVMESHSET` (`MSET` magic, version 1) — the
  same format as the 296 stock `.nav` files; rename/drop into `navmeshes/<ZoneName>.nav`.

## Licensing

`recast/` and `detour/` are **RecastNavigation** by Mikko Mononen, **zlib license**
(see each source file's header). `xi_navmesh.cpp`, `ChunkyTriMesh.*`, and this
build are part of xi-tools. The pipeline references xenonsmurf's FFXINAV as a
design reference only.

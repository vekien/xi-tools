#!/usr/bin/env python3
"""Bake a server-compatible FFXI navmesh (Detour ``NAVMESHSET`` ``*.nav``) from a
zone's collision mesh, via our native ``xi_navmesh`` library (Recast + Detour).

The navmesh is a *server-side* artifact: the LandSandBoat/CatsEyeXI map server
pathfinds mobs/NPCs over it (``navmeshes/<ZoneName>.nav``). It is baked from the
same collision triangle soup the client uses to block the player — so it picks up
custom collision edits automatically.

Pipeline: decode the 0x1C collision (see ``xi_collision.decode_collision``) →
convert each vertex to Detour space ``(x, -y, -z)`` (the server's ToDetourPos) →
hand the triangle soup to ``xi_build_navmesh`` (native Recast tile build →
Detour NAVMESHSET serialize). A prebuilt lib ships in ``xi/libs/``; to rebuild,
run ``cmake -B build && cmake --build build --config Release`` in
``misc/tools/xi-navmesh`` (or point ``XI_NAVMESH_LIB`` at a prebuilt .dll).
"""

import array
import ctypes
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from xi.zone.xi_collision import decode_collision, decrypted_zonedef


# ---------------------------------------------------------------------------
# Native library
# ---------------------------------------------------------------------------

class _XiNavSettings(ctypes.Structure):
    # Must match struct XiNavSettings in native/xi-navmesh/xi_navmesh.cpp.
    _fields_ = [
        ("cellSize", ctypes.c_float), ("cellHeight", ctypes.c_float),
        ("agentHeight", ctypes.c_float), ("agentRadius", ctypes.c_float),
        ("agentMaxClimb", ctypes.c_float), ("agentMaxSlope", ctypes.c_float),
        ("regionMinSize", ctypes.c_float), ("regionMergeSize", ctypes.c_float),
        ("edgeMaxLen", ctypes.c_float), ("edgeMaxError", ctypes.c_float),
        ("vertsPerPoly", ctypes.c_float), ("detailSampleDist", ctypes.c_float),
        ("detailSampleMaxError", ctypes.c_float), ("tileSize", ctypes.c_float),
        ("partitionType", ctypes.c_int),
    ]


@dataclass
class NavSettings:
    """Recast build settings. Defaults = the LandSandBoat/Topaz mob-navmesh profile
    (matches the stock ``.nav`` files: cell 0.4, agent radius 0.3, tile 256)."""
    cell_size: float = 0.40
    cell_height: float = 0.20
    agent_height: float = 1.8
    agent_radius: float = 0.3
    agent_max_climb: float = 0.5
    agent_max_slope: float = 46.0
    region_min_size: float = 8.0
    region_merge_size: float = 20.0
    edge_max_len: float = 12.0
    edge_max_error: float = 1.3
    verts_per_poly: float = 6.0
    detail_sample_dist: float = 6.0
    detail_sample_max_error: float = 1.0
    tile_size: float = 256.0
    partition_type: int = 0   # 0=watershed, 1=monotone, 2=layers

    def to_c(self) -> _XiNavSettings:
        return _XiNavSettings(
            self.cell_size, self.cell_height, self.agent_height, self.agent_radius,
            self.agent_max_climb, self.agent_max_slope, self.region_min_size,
            self.region_merge_size, self.edge_max_len, self.edge_max_error,
            self.verts_per_poly, self.detail_sample_dist, self.detail_sample_max_error,
            self.tile_size, self.partition_type)


_LIB_NAMES = ("xi_navmesh.dll", "xi_navmesh.so", "xi_navmesh.dylib", "libxi_navmesh.so")


def _native_roots() -> list:
    """Source-tree roots where the xi-navmesh build output lives (current + legacy)."""
    repo = Path(__file__).resolve().parents[3]   # src/xi/zone/xi_navmesh.py -> xi-tools/
    return [repo / "misc" / "tools" / "xi-navmesh",   # current location
            repo / "native" / "xi-navmesh"]           # legacy (pre-move)


def _find_library() -> Optional[Path]:
    # 1) Explicit override.
    env = os.environ.get("XI_NAVMESH_LIB", "").strip()
    if env and Path(env).is_file():
        return Path(env)
    candidates = []
    # 2) Built from source (current + legacy roots; Release/Debug/flat).
    for root in _native_roots():
        for cfg in ("Release", "Debug", ""):
            candidates += [root / "build" / cfg / n for n in _LIB_NAMES]
        candidates += [root / "build" / n for n in _LIB_NAMES]
    # 3) Bundled with the package (ships in the editor distribution) or next to the exe.
    pkg = Path(__file__).resolve().parents[1]    # .../xi
    bundled = [pkg / "libs", pkg]
    try:
        bundled.append(Path(sys.executable).resolve().parent)
    except Exception:
        pass
    for d in bundled:
        candidates += [d / n for n in _LIB_NAMES]
    for c in candidates:
        if c.is_file():
            return c
    return None


_lib = None


def _load_library() -> ctypes.CDLL:
    global _lib
    if _lib is not None:
        return _lib
    path = _find_library()
    if path is None:
        raise FileNotFoundError(
            "xi_navmesh native library not found. Build it once with:\n"
            f"  cd {_native_roots()[0]}\n"
            "  cmake -B build && cmake --build build --config Release\n"
            "  (or set XI_NAVMESH_LIB to a prebuilt xi_navmesh.dll)")
    lib = ctypes.CDLL(str(path))
    lib.xi_build_navmesh.restype = ctypes.c_int
    lib.xi_build_navmesh.argtypes = [
        ctypes.POINTER(ctypes.c_float), ctypes.c_int,        # verts, nverts
        ctypes.POINTER(ctypes.c_int), ctypes.c_int,          # tris, ntris
        ctypes.POINTER(_XiNavSettings), ctypes.c_char_p,   # settings, out_path
    ]
    _lib = lib
    return lib


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_navmesh(verts: array.array, tris: array.array, out_path: Path,
                  settings: Optional[NavSettings] = None) -> int:
    """Low-level: build a .nav from a flat float vertex array (x,y,z,... already in
    Detour space) + a flat int index array. Returns tiles written (>=0)."""
    lib = _load_library()
    s = (settings or NavSettings()).to_c()
    nverts = len(verts) // 3
    ntris = len(tris) // 3
    if nverts == 0 or ntris == 0:
        raise ValueError("no geometry to build a navmesh from")
    vp = (ctypes.c_float * len(verts)).from_buffer(verts)
    tp = (ctypes.c_int * len(tris)).from_buffer(tris)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    rc = lib.xi_build_navmesh(vp, nverts, tp, ntris, ctypes.byref(s), str(out_path).encode("utf-8"))
    if rc < 0:
        raise RuntimeError(f"xi_build_navmesh failed (code {rc})")
    return rc


def build_navmesh_from_collision(source: Path, out_path: Path,
                                 settings: Optional[NavSettings] = None) -> tuple:
    """Decode a zone DAT's collision and bake a .nav. Returns (out_path, n_tris, n_tiles).

    Vertices are converted FFXI world -> Detour space (x, -y, -z) so the navmesh
    matches the server's coordinate convention (CNavMesh::ToDetourPos)."""
    parsed = decrypted_zonedef(source)
    if parsed is None:
        raise ValueError("DAT has no 0x1C ZoneDef section")
    buf, ds, _ss, _sz = parsed
    data = decode_collision(buf, ds)
    if not data.tris:
        raise ValueError("zone has no collision geometry to build a navmesh from")

    # Non-indexed soup: 3 verts per tri, sequential indices. FFXI (x,y,z) -> Detour (x,-y,-z).
    # Vertex order is (v0, v2, v1): the (x,-y,-z) Z-negation flips triangle winding, so we
    # reverse the order here to keep face normals pointing up in Detour space (walkable).
    verts = array.array("f")
    for t in data.tris:
        for (x, y, z) in (t.v0, t.v2, t.v1):
            verts.append(x); verts.append(-y); verts.append(-z)
    tris = array.array("i", range(3 * len(data.tris)))

    n_tiles = build_navmesh(verts, tris, out_path, settings)
    return Path(out_path), len(data.tris), n_tiles


# ---------------------------------------------------------------------------
# Inspect / validate a .nav
# ---------------------------------------------------------------------------

import struct as _struct  # noqa: E402

_NAVMESHSET_MAGIC = b"TESM"   # 'MSET' little-endian on disk
_DT_TILE_MAGIC = b"VAND"      # 'DNAV' little-endian on disk


def navmesh_info(path: Path) -> dict:
    """Parse a Detour NAVMESHSET .nav and return a structural summary. Validates
    the container magic/version, walks every tile (each must be a DNAV v7 Detour
    tile), and checks the file is consumed exactly. Works on any .nav (ours or
    the stock server meshes)."""
    b = Path(path).read_bytes()
    if len(b) < 40 or b[:4] != _NAVMESHSET_MAGIC:
        raise ValueError(f"{Path(path).name}: not a NAVMESHSET .nav (bad magic {b[:4]!r})")
    _magic, ver, num_tiles = _struct.unpack_from("<3i", b, 0)
    orig = _struct.unpack_from("<3f", b, 12)
    tile_w, tile_h = _struct.unpack_from("<2f", b, 24)
    max_tiles, max_polys = _struct.unpack_from("<2i", b, 32)

    off = 40
    polys = verts = bad = 0
    per_tile = []
    for _ in range(num_tiles):
        if off + 8 > len(b):
            bad += 1
            break
        _tile_ref, data_size = _struct.unpack_from("<Ii", b, off)
        off += 8
        if off + 100 > len(b) or data_size <= 0:
            bad += 1
            break
        dmagic = b[off:off + 4]
        dver = _struct.unpack_from("<i", b, off + 4)[0]
        tx, ty = _struct.unpack_from("<2i", b, off + 8)
        pc, vc = _struct.unpack_from("<2i", b, off + 24)
        if dmagic != _DT_TILE_MAGIC or dver != 7:
            bad += 1
        polys += pc
        verts += vc
        per_tile.append((tx, ty, pc, vc))
        off += data_size

    return {
        "version": ver,
        "tiles": num_tiles,
        "bad_tiles": bad,
        "consumed_exactly": off == len(b),
        "valid": ver == 1 and bad == 0 and off == len(b),
        "polys": polys,
        "verts": verts,
        "origin": tuple(round(o, 2) for o in orig),
        "tile_width": round(tile_w, 2),
        "max_tiles": max_tiles,
        "max_polys": max_polys,
        "size": len(b),
        "per_tile": per_tile,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

import click as _click  # noqa: E402


def navmesh_triangles(path: Path) -> list:
    """Parse a Detour NAVMESHSET .nav and return walkable polygon triangles as a
    flat float list [x,y,z,...] in FFXI world space (inverts Detour's x,-y,-z).
    Off-mesh connection polys are skipped.  Returns [] if the file is missing or
    the magic doesn't match."""
    b = Path(path).read_bytes()
    if len(b) < 40 or b[:4] != _NAVMESHSET_MAGIC:
        return []

    _magic, _ver, num_tiles = _struct.unpack_from("<3i", b, 0)
    off = 40  # byte offset of first per-tile record

    DT_HEADER_SIZE = 100   # sizeof(dtMeshHeader)
    DT_POLY_SIZE   = 32    # sizeof(dtPoly) with DT_VERTS_PER_POLY=6
    DT_MAX_VERTS   = 6     # DT_VERTS_PER_POLY

    positions = []

    for _ in range(num_tiles):
        if off + 8 > len(b):
            break
        _tile_ref, data_size = _struct.unpack_from("<Ii", b, off)
        off += 8
        ts = off  # start of DNAV tile data
        if data_size <= 0 or ts + data_size > len(b):
            break
        if b[ts:ts + 4] != _DT_TILE_MAGIC:
            off += data_size
            continue

        poly_count    = _struct.unpack_from("<i", b, ts + 24)[0]
        vert_count    = _struct.unpack_from("<i", b, ts + 28)[0]
        off_mesh_base = _struct.unpack_from("<i", b, ts + 56)[0]

        # Vertices: float32[3] × vert_count immediately after the 100-byte header.
        # Detour stores world coords as (x, -y, -z) relative to FFXI world, so
        # invert y and z to recover FFXI world coords.
        vert_off = ts + DT_HEADER_SIZE
        verts = []
        for i in range(vert_count):
            dx, dy, dz = _struct.unpack_from("<3f", b, vert_off + i * 12)
            verts.append((dx, -dy, -dz))  # Detour → FFXI world

        # Polygons: dtPoly × poly_count after the vertex block.
        # dtPoly layout (32 bytes): firstLink(4) + verts[6]×uint16(12) +
        #   neis[6]×uint16(12) + flags(2) + vertCount(1) + areaAndtype(1)
        poly_off   = vert_off + vert_count * 12
        walkable_n = min(poly_count, off_mesh_base) if off_mesh_base > 0 else poly_count
        for pi in range(walkable_n):
            p = poly_off + pi * DT_POLY_SIZE
            nv = b[p + 30]  # vertCount byte
            if nv < 3:
                continue
            vi = _struct.unpack_from(f"<{DT_MAX_VERTS}H", b, p + 4)
            # Fan-triangulate the convex polygon
            for i in range(1, nv - 1):
                for k in (0, i, i + 1):
                    positions.extend(verts[vi[k]])

        off += data_size

    return positions


@_click.command("navmesh")
@_click.argument("dat_path")
@_click.option("--output", type=_click.Path(path_type=Path), default=None,
               help="Output .nav path (default: exports/zone/<rom>/<stem>.nav)")
@_click.option("--agent-radius", type=float, default=0.3, show_default=True,
               help="Recast agent radius in yalms (0.3 = stock mob profile; ~0.7 for player movement)")
@_click.option("--agent-max-climb", type=float, default=0.5, show_default=True,
               help="Max step/climb height the agent can traverse")
@_click.option("--cell-size", type=float, default=0.40, show_default=True, help="Recast voxel cell size")
@_click.option("--tile-size", type=float, default=256.0, show_default=True, help="Tile size in cells")
def cmd(dat_path: str, output, agent_radius: float, agent_max_climb: float,
        cell_size: float, tile_size: float):
    """Bake a server navmesh (.nav) for a zone from its collision mesh.

    Decodes the zone's 0x1C collision and builds a Detour NAVMESHSET via the native
    xi_navmesh (Recast/Detour) library — the same format the LandSandBoat/
    CatsEyeXI map server pathfinds mobs on. Reads your edited DAT, so custom
    collision is included. Install by copying the result to the server's
    navmeshes/<ZoneName>.nav (keyed by zone_settings.name, e.g. Lower_Jeuno.nav).

    Build the native lib once: `cmake -B build && cmake --build build --config
    Release` in native/xi-navmesh.
    """
    from xi.entity.mesh.xi_export import resolve_dat_path
    from xi.xi_config import read_path_for
    from xi.zone.xi_export import default_output_dir
    try:
        resolved = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise _click.ClickException(str(e))
    source = read_path_for(resolved)
    out = Path(output) if output else (default_output_dir(resolved) / f"{resolved.stem}.nav")
    settings = NavSettings(cell_size=cell_size, agent_radius=agent_radius,
                           agent_max_climb=agent_max_climb, tile_size=tile_size)
    try:
        outp, n_tris, n_tiles = build_navmesh_from_collision(source, out, settings)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        raise _click.ClickException(str(e))
    _click.echo(f"Wrote navmesh:  {outp}")
    _click.echo(f"Built:          {n_tris} collision triangles -> {n_tiles} navmesh tiles")
    _click.echo("Install:        copy to the server's navmeshes/<ZoneName>.nav "
                "(e.g. Lower_Jeuno.nav).")


@_click.command("navmesh-info")
@_click.argument("nav_path", type=_click.Path(exists=True, path_type=Path))
@_click.option("--tiles", "show_tiles", is_flag=True, help="List per-tile (x, y, polys, verts)")
def info_cmd(nav_path: Path, show_tiles: bool):
    """Inspect/validate a Detour navmesh (.nav) — works on ours or the stock server
    meshes. Checks the NAVMESHSET magic/version, that every tile is a valid DNAV v7
    Detour tile, and that the file parses exactly; reports tile/poly/vert counts."""
    try:
        info = navmesh_info(Path(nav_path))
    except ValueError as e:
        raise _click.ClickException(str(e))
    ok = info["valid"]
    _click.echo(f"{Path(nav_path).name}: " + (_click.style("VALID", fg="green") if ok
                else _click.style("INVALID", fg="red")))
    _click.echo(f"  version {info['version']}  tiles {info['tiles']}  "
                f"polys {info['polys']}  verts {info['verts']}  ({info['size']} bytes)")
    _click.echo(f"  origin {info['origin']}  tileWidth {info['tile_width']}  "
                f"maxTiles {info['max_tiles']}  maxPolys {info['max_polys']}")
    if info["bad_tiles"]:
        _click.echo(_click.style(f"  {info['bad_tiles']} malformed tile(s)!", fg="red"))
    if not info["consumed_exactly"]:
        _click.echo(_click.style("  file did not parse exactly (truncated/extra bytes)!", fg="red"))
    if show_tiles:
        for (tx, ty, pc, vc) in info["per_tile"]:
            _click.echo(f"    tile ({tx},{ty}): {pc} polys, {vc} verts")

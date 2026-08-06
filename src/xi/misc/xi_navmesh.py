"""Navmesh preparation for recovered zones.

Pipeline:
    1. xi zone export <DAT>      -> .glb (visual zone mesh, world-transformed)
    2. xi misc navmesh-prep      -> .obj suitable for Recast/Detour + recipe.txt
    3. (manual) bake the .obj into a .nav using:
       - xenonsmurf's FFXI Navmesh Builder (load OBJ in NavMesh tab), OR
       - a Recast/Detour CLI baker (need to build one separately)
    4. Drop <Zone>.nav into LSB's navmeshes/ folder

Notes on accuracy:
    The .glb xi exports is the *visual* zone mesh, not the original
    collision/hit mesh. Retail FFXI zones ship a separate collision model
    which is what xenonsmurf's tool dumps. For recovered orphan zones we
    typically don't have collision data — visual geometry is what's left.

    Recast handles this by classifying triangles as walkable based on slope.
    Decorative geometry (walls, ceilings, trees) is mostly auto-rejected by
    the slope filter. Expect some overzealous walkable surfaces on roofs
    and ledges — tune `m_agentMaxSlope` if the navmesh is too generous.
"""

from __future__ import annotations

import json
import math
import os
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

import click

from xi.xi_config import FFXI_DIR


# ── FFXI standard Recast parameters (from xenonsmurf's FFXI Navmesh Builder) ──
# Two tested profiles. Mob-pathing is the default — that's what the existing
# 299 .nav files in <LSB_DIR>/navmeshes were built with.
FFXI_RECAST_PARAMS_MOB = {
    "m_tileSize":            256,
    "m_cellSize":            0.40,
    "m_cellHeight":          0.20,
    "m_agentHeight":         1.8,
    "m_agentRadius":         0.3,
    "m_agentMaxClimb":       0.5,
    "m_agentMaxSlope":       46.0,
    "m_regionMinSize":       8,
    "m_regionMergeSize":     20,
    "m_edgeMaxLen":          12.0,
    "m_edgeMaxError":        1.3,
    "m_vertsPerPoly":        6.0,
    "m_detailSampleDist":    6.0,
    "m_detailSampleMaxError": 1.0,
}

FFXI_RECAST_PARAMS_PLAYER = {
    **FFXI_RECAST_PARAMS_MOB,
    "m_tileSize":      64,
    "m_agentRadius":   0.7,
    "m_agentMaxClimb": 0.5,
}


# ── glTF .glb parser (binary glTF, in-house, no third-party deps) ─────────────

_GLB_MAGIC      = 0x46546C67  # 'glTF'
_CHUNK_JSON     = 0x4E4F534A  # 'JSON'
_CHUNK_BIN      = 0x004E4942  # 'BIN\0'

_COMPONENT_BYTE   = 5120
_COMPONENT_UBYTE  = 5121
_COMPONENT_SHORT  = 5122
_COMPONENT_USHORT = 5123
_COMPONENT_UINT   = 5125
_COMPONENT_FLOAT  = 5126

_PACK_CODE = {
    _COMPONENT_BYTE: "b", _COMPONENT_UBYTE: "B",
    _COMPONENT_SHORT: "h", _COMPONENT_USHORT: "H",
    _COMPONENT_UINT: "I",  _COMPONENT_FLOAT: "f",
}

_PRIM_TRIANGLES = 4


@dataclass(slots=True)
class _Mat4:
    m: list[float]  # row-major 16 floats

    @staticmethod
    def identity() -> "_Mat4":
        return _Mat4([1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1])

    @staticmethod
    def from_trs(t, r, s) -> "_Mat4":
        # t: (x,y,z) | r: quaternion (x,y,z,w) | s: (x,y,z)
        tx, ty, tz = t
        qx, qy, qz, qw = r
        sx, sy, sz = s
        xx, yy, zz = qx*qx, qy*qy, qz*qz
        xy, xz, yz = qx*qy, qx*qz, qy*qz
        wx, wy, wz = qw*qx, qw*qy, qw*qz
        return _Mat4([
            (1 - 2*(yy + zz)) * sx,  2*(xy - wz) * sy,        2*(xz + wy) * sz,        tx,
            2*(xy + wz) * sx,        (1 - 2*(xx + zz)) * sy,  2*(yz - wx) * sz,        ty,
            2*(xz - wy) * sx,        2*(yz + wx) * sy,        (1 - 2*(xx + yy)) * sz,  tz,
            0, 0, 0, 1,
        ])

    def multiply(self, other: "_Mat4") -> "_Mat4":
        a, b = self.m, other.m
        out = [0.0] * 16
        for r in range(4):
            for c in range(4):
                out[r*4 + c] = sum(a[r*4 + k] * b[k*4 + c] for k in range(4))
        return _Mat4(out)

    def transform_point(self, x, y, z):
        m = self.m
        nx = m[0]*x + m[1]*y + m[2]*z + m[3]
        ny = m[4]*x + m[5]*y + m[6]*z + m[7]
        nz = m[8]*x + m[9]*y + m[10]*z + m[11]
        return nx, ny, nz


def _node_matrix(node: dict) -> _Mat4:
    if "matrix" in node:
        # glTF stores matrix as column-major; convert to row-major for our impl.
        col = node["matrix"]
        rows = [col[i + 4*j] for j in range(4) for i in range(4)]
        return _Mat4(rows)
    t = node.get("translation", [0, 0, 0])
    r = node.get("rotation", [0, 0, 0, 1])
    s = node.get("scale", [1, 1, 1])
    return _Mat4.from_trs(t, r, s)


def glb_to_obj(glb_path: Path, obj_path: Path) -> tuple[int, int]:
    """Convert .glb to a minimal Recast-ready .obj.

    Walks the scene graph to apply world transforms (zones have many object
    instances with per-node TRS). Emits only positions + triangle faces —
    materials/normals/UVs are stripped since Recast doesn't need them.

    Returns (vertex_count, triangle_count).
    """
    raw = glb_path.read_bytes()
    magic, version, _length = struct.unpack_from("<III", raw, 0)
    if magic != _GLB_MAGIC:
        raise ValueError(f"Not a binary glTF: {glb_path}")
    if version != 2:
        raise ValueError(f"Only glTF 2.0 supported (got version {version})")

    off = 12
    gltf_json: dict | None = None
    bin_buf: bytes = b""
    while off < len(raw):
        chunk_len, chunk_type = struct.unpack_from("<II", raw, off)
        off += 8
        chunk_data = raw[off : off + chunk_len]
        off += chunk_len
        if chunk_type == _CHUNK_JSON:
            gltf_json = json.loads(chunk_data.decode("utf-8"))
        elif chunk_type == _CHUNK_BIN:
            bin_buf = chunk_data

    if gltf_json is None or not bin_buf:
        raise ValueError("Malformed .glb: missing JSON or BIN chunk")

    buffer_views = gltf_json.get("bufferViews", [])
    accessors    = gltf_json.get("accessors", [])
    meshes       = gltf_json.get("meshes", [])
    nodes        = gltf_json.get("nodes", [])
    scenes       = gltf_json.get("scenes", [])
    scene_idx    = gltf_json.get("scene", 0)

    def _read_positions(accessor_idx: int) -> list[tuple[float, float, float]]:
        acc = accessors[accessor_idx]
        view = buffer_views[acc["bufferView"]]
        start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        count = acc["count"]
        fmt = "<" + _PACK_CODE[_COMPONENT_FLOAT] * 3 * count
        flat = struct.unpack_from(fmt, bin_buf, start)
        return [(flat[i*3], flat[i*3+1], flat[i*3+2]) for i in range(count)]

    def _read_indices(accessor_idx: int) -> list[int]:
        acc = accessors[accessor_idx]
        view = buffer_views[acc["bufferView"]]
        start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        count = acc["count"]
        ctype = acc["componentType"]
        code = _PACK_CODE[ctype]
        return list(struct.unpack_from(f"<{count}{code}", bin_buf, start))

    out_verts: list[tuple[float, float, float]] = []
    out_tris:  list[tuple[int, int, int]] = []

    def _emit_mesh(mesh_idx: int, world: _Mat4) -> None:
        mesh = meshes[mesh_idx]
        for prim in mesh.get("primitives", []):
            if prim.get("mode", _PRIM_TRIANGLES) != _PRIM_TRIANGLES:
                continue
            attrs = prim.get("attributes", {})
            pos_acc = attrs.get("POSITION")
            if pos_acc is None:
                continue
            positions = _read_positions(pos_acc)
            if "indices" in prim:
                indices = _read_indices(prim["indices"])
            else:
                indices = list(range(len(positions)))
            base = len(out_verts)
            for x, y, z in positions:
                out_verts.append(world.transform_point(x, y, z))
            for i in range(0, len(indices) - 2, 3):
                out_tris.append((base + indices[i] + 1,
                                 base + indices[i+1] + 1,
                                 base + indices[i+2] + 1))

    def _walk(node_idx: int, parent_world: _Mat4) -> None:
        node = nodes[node_idx]
        local = _node_matrix(node)
        world = parent_world.multiply(local)
        if "mesh" in node:
            _emit_mesh(node["mesh"], world)
        for child in node.get("children", []):
            _walk(child, world)

    root_nodes = scenes[scene_idx].get("nodes", []) if scenes else range(len(nodes))
    for root in root_nodes:
        _walk(root, _Mat4.identity())

    with obj_path.open("w", encoding="utf-8") as f:
        f.write(f"# Recast-ready OBJ - generated by xi misc navmesh-prep\n")
        f.write(f"# source: {glb_path.name}\n")
        for x, y, z in out_verts:
            f.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")
        for a, b, c in out_tris:
            f.write(f"f {a} {b} {c}\n")

    return len(out_verts), len(out_tris)


def _write_recipe(recipe_path: Path, params: dict, kind: str, obj_name: str) -> None:
    """Write a human-readable + machine-parseable Recast param recipe."""
    bbox_hint = (
        "# Tile bounds are derived from the .obj's axis-aligned bounds at bake time.\n"
        "# Don't set them manually — RecastDemo / FFXI Navmesh Builder compute them.\n"
    )
    lines = [
        f"# Recast/Detour navmesh recipe",
        f"# Profile: {kind}",
        f"# Use this with xenonsmurf's FFXI Navmesh Builder NavMesh tab,",
        f"# or with a Recast CLI baker that accepts these field names.",
        f"# Input OBJ: {obj_name}",
        f"# Output: <ZoneName>.nav (NAVMESHSET_MAGIC='MSET', version=1) — drop in LSB's navmeshes/",
        "",
        bbox_hint,
    ]
    for k, v in params.items():
        lines.append(f"{k} = {v}")
    recipe_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ensure_glb(dat_path: str, xi_root: Path) -> Path:
    """Ensure a .glb for this DAT exists (running zone export if needed)."""
    rom_rel = dat_path.replace("\\", "/").lstrip("/")
    if rom_rel.lower().endswith(".dat"):
        rom_rel = rom_rel[:-4]
    export_dir = xi_root / "exports" / "zone" / rom_rel.lower()
    if export_dir.exists():
        existing = sorted(export_dir.glob("*.glb"), key=lambda p: p.stat().st_mtime, reverse=True)
        if existing:
            return existing[0]

    click.echo(f"[zone export] {dat_path} (no cached .glb)")
    env = os.environ.copy()
    env["XI_TOOLS_DIR"] = str(xi_root)
    result = subprocess.run(
        ["uv", "run", "xi", "zone", "export", dat_path],
        cwd=xi_root, env=env,
    )
    if result.returncode != 0:
        raise click.ClickException(f"xi zone export failed (exit {result.returncode})")

    candidates = sorted(export_dir.glob("*.glb"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise click.ClickException(f"No .glb produced under {export_dir}")
    return candidates[0]


@click.command("navmesh-prep")
@click.argument("dat_path")
@click.option("--profile", type=click.Choice(["mob", "player"]), default="mob", show_default=True,
              help="FFXI Recast parameter preset.")
@click.option("--output", "-o", type=click.Path(),
              help="Output dir (default: exports/navmesh/<rom-path>/).")
def cmd(dat_path, profile, output):
    """Prepare a Recast-ready .obj + recipe.txt for a zone.

    Step 1 of the navmesh pipeline. Calls `xi zone export` if no .glb is
    cached, then converts to .obj with world-space transforms applied.

    Step 2 (manual today): bake the .obj -> .nav using either
    `thirdparty/navmesh builder/FFXI Navmesh Builder.exe` (load OBJ on the
    NavMesh tab) or a RecastDemo CLI build.
    """
    xi_root = Path(os.environ.get("XI_TOOLS_DIR", "F:/Repos/xi-tools"))
    if not (xi_root / "pyproject.toml").exists():
        xi_root = Path(__file__).resolve().parents[3]

    glb = _ensure_glb(dat_path, xi_root)
    click.echo(f"[glb]   {glb}")

    if output:
        out_dir = Path(output)
    else:
        rom_norm = dat_path.replace("\\", "/").lstrip("/")
        if rom_norm.lower().endswith(".dat"):
            rom_norm = rom_norm[:-4]
        out_dir = xi_root / "exports" / "navmesh" / rom_norm.lower()
    out_dir.mkdir(parents=True, exist_ok=True)

    obj_path = out_dir / (glb.stem + ".obj")
    recipe   = out_dir / "recipe.txt"

    n_verts, n_tris = glb_to_obj(glb, obj_path)
    params = FFXI_RECAST_PARAMS_PLAYER if profile == "player" else FFXI_RECAST_PARAMS_MOB
    _write_recipe(recipe, params, profile, obj_path.name)

    click.echo(click.style(f"[obj]   {obj_path}  ({n_verts:,} verts, {n_tris:,} tris)", fg="green"))
    click.echo(click.style(f"[recipe] {recipe}  (profile={profile})", fg="green"))
    click.echo()
    click.echo("Next step (manual today):")
    click.echo(f"  Option A — GUI bake:")
    click.echo(f"    1. Run thirdparty/navmesh builder/FFXI Navmesh Builder.exe as admin")
    click.echo(f"    2. NavMesh tab -> 'Select obj file to build a NavMesh for.' -> pick {obj_path.name}")
    click.echo(f"    3. Apply NavMesh Settings using the values in {recipe.name}")
    click.echo(f"    4. Build -> rename output to <ZoneName>.nav -> copy to {os.environ.get('LSB_DIR','<LSB>')}/navmeshes/")
    click.echo()
    click.echo(f"  Option B — CLI bake (requires building a Recast baker):")
    click.echo(f"    not yet shipped; planned as `xi misc navmesh-bake`")

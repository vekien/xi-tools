"""Assemble a custom zone DAT from a Godot build manifest.

Reads the `build_manifest.json` the Godot zone-designer exports (schema v1) and,
for every placed object, pulls its mesh out of the source biome zone and injects
it (geometry + textures + a fully-registered placement) into a TEMPLATE zone DAT.

We don't synthesize the 0x1C collision/spatial grid from scratch — instead we
start from a real, small, flat zone (Celennia Memorial Library) whose grid is
already valid, and GROW it via the proven `import_object` path. The template's
own contents are left for a later strip pass (M3).

    xi zone build-from-manifest build_manifest.json
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import click

import struct

from xi.zone.xi_export import (parse_zone, resolve_dat_path, trs_matrix,
                                 SECTION_TYPE_TEXTURE, SECTION_TYPE_ZONE_MESH, SECTION_TYPE_ZONE_DEF)
from xi.zone.xi_object import export_object, import_object
from xi.zone.xi_decrypt import load_key_tables, decrypt_zone_objects, reencrypt_zone_objects
from xi.zone.xi_zonedef import (add_placements, add_collision_transforms, add_to_culling_tables,
                                  expand_placement_bounds_points, parse_zonedef,
                                  OBJ_ARRAY_START, OBJ_RECORD_SIZE, OBJ_DRAW_DISTANCE)
from xi.entity.anim.xi_export import parse_sections
from xi.entity.mesh.xi_export import parse_texture
from xi.xi_config import FFXI_DIR, editable_dat, output_path_for

# Celennia Memorial Library — small (37 meshes), flat, enclosed. Its floor (yuka1) sits at
# (-105, ~0, -95), so we drop the origin-centred Godot build onto that point. Y must be the
# FLOOR (~0), not the mid-range — FFXI is Y-down, so a too-negative Y floats objects upward.
DEFAULT_TEMPLATE = "ROM/303/26"
DEFAULT_CENTER = (-105.0, 0.0, -95.0)

# theme -> source zone ROM path (fallback when the manifest omits its own "sources" map).
DEFAULT_SOURCES = {"City": "ROM/1/41", "Desert": "ROM2/0/1", "Snow": "ROM/0/72", "Lush": "ROM2/0/4"}

# Base template per biome — gives that biome's lighting + skybox + a valid 0x1C skeleton.
# Flatness barely matters (we add our own flat walkable grid); we only avoid the template's
# terrain poking through via floor_y. center = (x, floor_y, z): a starting build spot on the
# zone's main ground level — tune in-game (collision-survey medians, 2026-06-09). Picked for
# flattest-of-biome: lush=Yuhtunga(69), desert=E.Altepa(78), snow=Xarcabard(153, flattest real
# outdoor snow — Fei'Yin/Pso'Xja are dungeons, not snow). city=Celennia (indoor, dim).
# Template = the biome's own asset-source zone where possible, so injected assets render in
# their native lighting (desert/lush/snow all match their sources -> consistent look).
BIOME_TEMPLATES = {
    "desert": ("ROM2/0/1",  (95.0,   8.0,  99.0)),   # Eastern Altepa Desert (= Desert source)
    "lush":   ("ROM2/0/4",  (-205.0, 0.0,  13.0)),   # Yuhtunga Jungle (= Lush source; flat)
    # Snow zones are ALL hilly/cramped (Beaucedine/Xarcabard etc.) -> builds land in void pockets.
    # So snow uses the flat, open, lit Eastern Altepa base; the biome comes from the snow floor
    # tiles + snow structures cross-injected onto it (same reliable result as the desert build).
    "snow":   ("ROM2/0/1",  (95.0,   8.0,  99.0)),   # Eastern Altepa (flat base) + snow tiles/props
    "city":   ("ROM/303/26", (-105.0, 0.0, -95.0)),  # Celennia Memorial Library (indoor)
}
DEFAULT_BIOME = "desert"

# Plot edge in units, mirroring the Godot designer's New-Map sizes. Sets the walkable-collision
# GRID footprint; we don't pre-render a floor — the grid IS the walkable plot.
SIZE_GRID = {"small": 64.0, "medium": 128.0, "large": 256.0}
DEFAULT_SIZE = "medium"

# The Godot designer's floor "tiles" are synthesised quads, so they have no source mesh of
# their own. Instead we reuse a real, flat ~40u biome ground mesh and scale it to the tile's
# footprint — genuine FFXI sand/grass/snow geometry rather than a hand-built quad.
# biome -> (source ROM, flat ground mesh id, native X span in units).
TILE_FLOORS = {
    "Desert": ("ROM2/0/1", "de_fl_2_m", 40.0),   # Eastern Altepa sand
    "Snow":   ("ROM/0/72", "ga_fl_1_m", 40.0),   # Beaucedine snow/frost floor
    "Lush":   ("ROM2/0/4", "flat_01",   40.0),   # Yuhtunga grass floor
}
DEFAULT_TILE_BIOME = "Desert"


def _tile_biome(mesh_label: str, theme) -> str:
    """Guess a tile's biome from its label/theme (sand->Desert, snow->Snow, grass->Lush)."""
    if theme in TILE_FLOORS:
        return theme
    n = (mesh_label or "").lower()
    if "snow" in n or "yuki" in n or "ice" in n:
        return "Snow"
    if "grass" in n or "lush" in n or "siba" in n or "kusa" in n:
        return "Lush"
    return DEFAULT_TILE_BIOME


def _tile_units(o: dict) -> float:
    """Tile edge length in world units. Prefer an explicit tile_size; else parse the label
    ('… 8x8' -> 8 cells * 4u = 32u). Falls back to a 32u block."""
    if o.get("tile_size"):
        return float(o["tile_size"])
    mt = re.search(r"(\d+)\s*x\s*\d+", str(o.get("mesh", "")))
    return float(int(mt.group(1)) * 4) if mt else 32.0


def _find_mesh(meshes: dict, name: str):
    """Resolve a (possibly collapsed) manifest mesh name to a real source mesh id."""
    if name in meshes:
        return name
    for suf in ("_h", "_m", "_l"):
        if name + suf in meshes:
            return name + suf
    cands = sorted(m for m in meshes if m.startswith(name))
    return cands[0] if cands else None


def build_from_manifest(manifest_path: Path,
                        template_rom: str = DEFAULT_TEMPLATE,
                        center: tuple = DEFAULT_CENTER,
                        strip_template: bool = False,
                        walkable: bool = False,
                        floor_y: float = 0.0,
                        biome: str | None = None,
                        size: str | None = None,
                        render_tiles: bool = True) -> tuple[Path, int, list[str]]:
    """Assemble the zone. Returns (output_dat_path, placed_count, warnings).

    biome (desert/lush/snow/city) picks the base template + its lighting/center; size
    (small/medium/large) sets the walkable-collision GRID to the matching Godot plot
    (64/128/256). Either present implies walkable and overrides template_rom/center/floor_y.
    The manifest's own "biome"/"size" fields are used when these args are None.
    render_tiles: render the manifest's floor tiles as ground meshes. Default OFF — the
    walkable grid is the floor ("just the grid"), so tiles only mark walkable area.
    strip_template: blank every ORIGINAL template placement so only the manifest's objects
    render — a clean canvas. The template's collision stays as a boundary."""
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    sources = data.get("sources") or DEFAULT_SOURCES
    objs = data.get("objects", [])
    warnings: list[str] = []

    # Biome+size preset (args win over the manifest's own "biome"/"size" fields).
    plot_dim = None
    biome = (biome or data.get("biome") or "").strip().lower() or None
    size = (size or data.get("size") or "").strip().lower() or None
    if biome or size:
        b = biome if biome in BIOME_TEMPLATES else DEFAULT_BIOME
        s = size if size in SIZE_GRID else DEFAULT_SIZE
        tpl, ctr = BIOME_TEMPLATES[b]
        plot_dim = SIZE_GRID[s]
        template_rom = tpl
        if tuple(center) == DEFAULT_CENTER:   # don't clobber an explicit --center
            center = ctr
        floor_y = ctr[1]                       # grid sits at the template's ground level
        walkable = True
        warnings.append(f"(preset {b}/{s}: base {tpl} @ {ctr}, {plot_dim:.0f}u walkable grid)")

    template = resolve_dat_path(template_rom)
    editable_dat(template, fresh=True)   # reset output to a fresh template copy

    tmp = Path(tempfile.mkdtemp(prefix="xi_zonebuild_"))
    rom_cache: dict = {}                 # ROM path -> meshes_by_name (lazy)
    def _rom_meshes(rom: str) -> dict:
        if rom not in rom_cache:
            try:
                rom_cache[rom] = parse_zone(resolve_dat_path(rom))[0]
            except Exception:
                rom_cache[rom] = {}
        return rom_cache[rom]

    # Resolve each object to a concrete (source ROM, mesh) and collect the UNIQUE meshes, so we
    # inject each one only ONCE. Injecting a separate mesh-section per placement (the old path)
    # is what crashed the client's renderer on builds with repeated meshes.
    resolved: list = []                  # (real, pos, rot, scale)
    unique: dict = {}                    # real -> (src_rom, glb, zraw, bbox6)
    used_source_roms: set = set()
    tiles = 0

    for o in objs:
        mesh = str(o.get("mesh", ""))
        is_tile = bool(o.get("is_tile")) or mesh.startswith("Tile ")
        if is_tile:
            tiles += 1
            if not render_tiles:
                continue  # "just the grid" — the walkable collision is the floor, not a mesh
            # (opt-in) render the tile as a real biome ground mesh, scaled to its footprint.
            biome = _tile_biome(mesh, o.get("theme"))
            src_rom, real, native_span = TILE_FLOORS.get(biome, TILE_FLOORS[DEFAULT_TILE_BIOME])
            s = _tile_units(o) / (native_span or 40.0)
            scale = (s, s, s)
        else:
            hint = o.get("theme")
            # Declared theme first; else infer by searching every source zone for the mesh.
            order = ([hint] if hint in sources else []) + [t for t in sources if t != hint]
            src_rom = real = None
            for th in order:
                r = _find_mesh(_rom_meshes(sources[th]), mesh)
                if r is not None:
                    src_rom, real = sources[th], r
                    break
            if real is None:
                warnings.append(f"mesh '{mesh}' not found in any source zone")
                continue
            sc = o.get("scale")
            if isinstance(sc, (int, float)):          # Godot exports a scalar
                scale = (float(sc), float(sc), float(sc))
            elif sc:
                scale = (float(sc[0]), float(sc[1]), float(sc[2]))
            else:
                scale = (1.0, 1.0, 1.0)

        if real not in unique:
            prims = _rom_meshes(src_rom).get(real)
            if not prims:
                warnings.append(f"mesh '{real}' missing in {src_rom}")
                continue
            paths = export_object(resolve_dat_path(src_rom), real, tmp / f"{real}", fbx=False)
            glb = next(p for p in paths if p.suffix == ".glb")
            zraw = next((p for p in paths if p.suffix == ".zone2e"), None)
            xs = [v[0] for pr in prims for v in pr.positions]
            ys = [v[1] for pr in prims for v in pr.positions]
            zs = [v[2] for pr in prims for v in pr.positions]
            bbox6 = (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)) if xs else (0, 0, 0, 0, 0, 0)
            unique[real] = (src_rom, glb, zraw, bbox6)
            used_source_roms.add(src_rom)

        px, py, pz = o["position"]
        pos = (px + center[0], py + center[1], pz + center[2])
        rot = (0.0, float(o.get("rotation_y", 0.0)), 0.0)
        resolved.append((real, pos, rot, scale))

    if tiles:
        warnings.append(f"({tiles} floor tile(s) rendered)" if render_tiles
                        else f"({tiles} floor tile(s) skipped — the walkable grid is the floor)")

    out = output_path_for(template)

    # Phase 0 — walkable floor FIRST (onto the pristine template); placements then grow on top.
    # This order loads cleanly (collision appended AFTER placement growth crashed on zone-in).
    # The grid is plot-sized when --size is set, else the build's footprint.
    if walkable:
        if plot_dim:
            half = plot_dim / 2.0
            bounds = (center[0] - half, center[0] + half, center[2] - half, center[2] + half)
        elif resolved:
            bounds = _build_footprint(resolved, unique)
        else:
            bounds = (center[0] - 32, center[0] + 32, center[2] - 32, center[2] + 32)
        fobj = tmp / "_walkable_floor.obj"
        fobj.write_text(_floor_obj(bounds, floor_y), encoding="ascii")
        from xi.zone.xi_collision import add_collision_from_obj
        _o, n_tris, _n_meshes, w2 = add_collision_from_obj(template, fobj, camera_transparent=True)
        mnx, mxx, mnz, mxz = bounds
        warnings.append(f"(walkable grid: +{n_tris} collision tri(s) over "
                        f"X[{mnx:.0f},{mxx:.0f}] Z[{mnz:.0f},{mxz:.0f}] @ FFXI Y={floor_y})")
        warnings.extend(f"  collision: {w}" for w in w2)

    # Phase 1 — inject each unique mesh ONCE (no placement); capture its final (renamed) id.
    final_name: dict = {}
    for real, (src_rom, glb, zraw, _bb) in unique.items():
        _, fname = import_object(template, glb, real, pos=None, raw_section=zraw)
        final_name[real] = fname

    # Phase 2 — register ALL placements in a SINGLE 0x1C decrypt/encrypt pass (one grow). Runs
    # even with no objects when stripping, so a blank --size plot still comes out clean.
    items = [(final_name[real], unique[real][3], pos, rot, scale) for real, pos, rot, scale in resolved]
    if items or strip_template:
        _batch_placements(out, items, strip_original=strip_template)
    if strip_template:
        warnings.append("(stripped template render — only your objects draw; collision kept as boundary)")

    # Phase 3 — splice the source zones' textures so the meshes render.
    tex_added = _inject_textures(out, used_source_roms)
    if tex_added:
        warnings.append(f"(injected {tex_added} textures from source zones)")

    # Phase 4 — when stripping, also remove the template's leftover VFX (0x05 generators:
    # candle flames, light shafts, etc.) so only your scene remains. Effects splice out
    # independently of meshes/placements/collision.
    if strip_template:
        from xi.fx.xi_list import list_effects
        from xi.fx.xi_delete import delete_effects
        fx_names = [e["name"] for e in list_effects(out)]
        if fx_names:
            removed = delete_effects(template, fx_names)
            warnings.append(f"(stripped {len(removed)} leftover template effect(s))")

    return out, len(resolved), warnings


def _build_footprint(resolved: list, unique: dict, pad: float = 2.0):
    """XZ bounds (minx,maxx,minz,maxz) covering every placed object's world footprint."""
    mnx = mnz = 1e9
    mxx = mxz = -1e9
    for real, pos, _rot, scale in resolved:
        _x0, _x1, _y0, _y1, _z0, _z1 = unique[real][3]
        ext_x = max(abs(_x0), abs(_x1)) * scale[0]
        ext_z = max(abs(_z0), abs(_z1)) * scale[2]
        mnx = min(mnx, pos[0] - ext_x); mxx = max(mxx, pos[0] + ext_x)
        mnz = min(mnz, pos[2] - ext_z); mxz = max(mxz, pos[2] + ext_z)
    return (mnx - pad, mxx + pad, mnz - pad, mxz + pad)


def _floor_obj(bounds, floor_y: float) -> str:
    """A single col_floor_sand quad over the given XZ bounds, in the collision (x,-y,z) frame
    with the normal up (parse_collision_obj un-negates Y, so FFXI Y=floor_y -> obj y=-floor_y)."""
    mnx, mxx, mnz, mxz = bounds
    oy = -floor_y
    return ("o xi_walkable_floor\n"
            "usemtl col_floor_sand\n"
            f"v {mnx:.3f} {oy:.3f} {mnz:.3f}\n"
            f"v {mxx:.3f} {oy:.3f} {mnz:.3f}\n"
            f"v {mxx:.3f} {oy:.3f} {mxz:.3f}\n"
            f"v {mnx:.3f} {oy:.3f} {mxz:.3f}\n"
            "f 1 4 3 2\n")


def _bbox_points(bbox6, pos, rot, scale):
    """8 corners of an axis-aligned mesh bbox, transformed into world space."""
    xmin, xmax, ymin, ymax, zmin, zmax = bbox6
    m = trs_matrix(pos, rot, scale)
    pts = []
    for x in (xmin, xmax):
        for y in (ymin, ymax):
            for z in (zmin, zmax):
                pts.append((m[0] * x + m[4] * y + m[8] * z + m[12],
                            m[1] * x + m[5] * y + m[9] * z + m[13],
                            m[2] * x + m[6] * y + m[10] * z + m[14]))
    return pts


def _batch_placements(dat_path: Path, items: list, strip_original: bool = False) -> None:
    """Register every placement in ONE decrypt -> add -> re-encrypt pass on the 0x1C
    section. items = [(mesh_name, bbox6, pos, rot, scale), ...]. Each new record copies flags
    from the nearest native placement, then gets full visibility registration (space-tree leaf +
    collision transform + culling/PVS membership). Growing the section once avoids the
    renderer crash seen when import_object grew it separately for every object."""
    table1, _t2 = load_key_tables(Path(FFXI_DIR) / "FFXiMain.dll")
    data = bytearray(Path(dat_path).read_bytes())
    zdsec = next(s for s in parse_sections(data) if s.type_code == SECTION_TYPE_ZONE_DEF)
    node_count = decrypt_zone_objects(data, zdsec.data_start, zdsec.start, zdsec.size, table1)

    def _rec_pos(i):
        off = zdsec.data_start + OBJ_ARRAY_START + i * OBJ_RECORD_SIZE + 0x10
        return struct.unpack_from("<3f", data, off)

    # Template per new object = nearest existing placement (copies its flags/links).
    adds = []
    for (name, _bb, pos, rot, scale) in items:
        tmpl = min(range(node_count),
                   key=lambda i: sum((a - b) ** 2 for a, b in zip(_rec_pos(i), pos)))
        adds.append((tmpl, pos, rot, scale, name))

    sec = bytearray(data[zdsec.start:zdsec.start + zdsec.size])
    base = node_count
    sec = add_placements(sec, adds, register_in_tree=True)
    zd = parse_zonedef(sec, 0x10, 0, len(sec))
    xform_entries = []
    for k, (name, bbox6, pos, rot, scale) in enumerate(items):
        expand_placement_bounds_points(sec, zd, base + k, _bbox_points(bbox6, pos, rot, scale))
        rec = 0x10 + OBJ_ARRAY_START + (base + k) * OBJ_RECORD_SIZE
        struct.pack_into("<f", sec, rec + OBJ_DRAW_DISTANCE, 2000.0)
        xform_entries.append((adds[k][0], trs_matrix(pos, rot, scale)))
    sec = add_collision_transforms(sec, xform_entries)
    for k in range(len(items)):
        sec = add_to_culling_tables(sec, base + k)
    if strip_original:
        # Blank every original template placement (mesh id -> 0) so only the new
        # objects render. Records/collision stay; the engine just draws nothing.
        for i in range(base):
            off = 0x10 + OBJ_ARRAY_START + i * OBJ_RECORD_SIZE
            sec[off:off + 0x10] = b"\x00" * 0x10
    reencrypt_zone_objects(sec, 0x10, 0, len(sec), table1)
    data[zdsec.start:zdsec.start + zdsec.size] = sec
    Path(dat_path).write_bytes(bytes(data))


def _inject_textures(target_path: Path, source_roms: set) -> int:
    """Copy raw 0x20 texture sections from each used source zone into the assembled DAT
    (deduped by name). Textures aren't encrypted, so we splice the exact bytes — the source
    mesh sections reference them by the same name, so the link is preserved verbatim."""
    if not source_roms:
        return 0
    data = bytearray(Path(target_path).read_bytes())
    secs = list(parse_sections(data))
    existing = set()
    for s in secs:
        if s.type_code == SECTION_TYPE_TEXTURE:
            img = parse_texture(bytes(data), s)
            if img:
                existing.add((img.name or "").strip())

    blobs = bytearray()
    added = 0
    for rom in source_roms:
        src = bytearray(resolve_dat_path(rom).read_bytes())
        for s in parse_sections(src):
            if s.type_code != SECTION_TYPE_TEXTURE:
                continue
            img = parse_texture(bytes(src), s)
            nm = (img.name or "").strip() if img else ""
            if nm and nm not in existing:
                blobs += src[s.start:s.start + s.size]
                existing.add(nm)
                added += 1
    if blobs:
        last_mesh = max((s for s in secs if s.type_code == SECTION_TYPE_ZONE_MESH),
                        key=lambda s: s.start)
        ins = last_mesh.start + last_mesh.size
        data[ins:ins] = blobs
        Path(target_path).write_bytes(bytes(data))
    return added


@click.command("build-from-manifest")
@click.argument("manifest", type=click.Path(exists=True, path_type=Path))
@click.option("--template", default=DEFAULT_TEMPLATE, show_default=True,
              help="ROM path of the template zone to grow from.")
@click.option("--center", nargs=3, type=float, default=DEFAULT_CENTER, show_default=True,
              metavar="X Y Z", help="Where the build's origin lands inside the template.")
@click.option("--strip-template", is_flag=True,
              help="Blank every original template placement so only your objects render "
                   "(clean canvas). Collision stays as a boundary; add --walkable for a floor.")
@click.option("--walkable", is_flag=True,
              help="Auto-add a col_floor_sand collision plane over the build footprint so the "
                   "player can stand on it (the one-command walkable zone).")
@click.option("--floor-y", type=float, default=0.0, show_default=True,
              help="FFXI Y of the walkable floor surface (lower it if you stand too high).")
@click.option("--biome", type=click.Choice(["desert", "lush", "snow", "city"]), default=None,
              help="Base template biome (lighting + skybox). Overrides --template/--center/--floor-y "
                   "and implies --walkable. Falls back to the manifest's 'biome' field.")
@click.option("--size", type=click.Choice(["small", "medium", "large"]), default=None,
              help="Plot size matching the Godot designer (small=64, medium=128, large=256). Sets "
                   "the walkable grid footprint. Falls back to the manifest's 'size' field.")
@click.option("--render-tiles/--no-render-tiles", default=True, show_default=True,
              help="Render floor tiles as visible biome ground meshes (snow/sand/grass). "
                   "--no-render-tiles leaves just the invisible walkable grid.")
def cmd(manifest, template, center, strip_template, walkable, floor_y, biome, size, render_tiles):
    """Assemble a custom zone DAT from a Godot build_manifest.json.

    Pulls each placed object's mesh from its source biome zone and injects it (mesh +
    textures + registered placement) into the base template, written in place.
    Use --biome/--size (matching the Godot designer) for a clean, walkable, only-your-stuff
    plot; the walkable grid is the floor, so floor tiles aren't rendered unless --render-tiles.
    """
    out, placed, warnings = build_from_manifest(manifest, template, tuple(center),
                                                strip_template=strip_template,
                                                walkable=walkable, floor_y=floor_y,
                                                biome=biome, size=size, render_tiles=render_tiles)
    click.echo(f"Placed {placed} objects into template {template}")
    for w in warnings[:40]:
        click.echo(click.style(f"  ! {w}", fg="yellow"))
    if len(warnings) > 40:
        click.echo(f"  … and {len(warnings) - 40} more warnings")
    click.echo(f"Wrote: {out}")

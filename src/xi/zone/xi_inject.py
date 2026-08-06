"""``xi zone inject`` — create new zones by cloning existing ones with
colour/lighting modifications.

Supports hue shift, saturation, lightness, tint (with blend modes), and
environment lighting controls (ambient darkening, fog colour/distance).

Examples::

    xi zone inject "Verdant Sarutabaruta" --clone 115 --hue 120
    xi zone inject "Crimson Ronfaure" --clone "West Ronfaure" --hue 240
    xi zone inject "Shadow Jeuno" --clone 244 --hue 180 --saturation 20 --lightness -30 --fog-tint "#1a0a2a" --fog-end 80
    xi zone inject "Dark Bastok" --clone "Bastok Mines" --tint "#330066aa" --blend overlay
"""

import shutil
import struct
from pathlib import Path

import click

from xi.ftable.xi_core import (
    patch_table, ftable_path, vtable_path,
    load_all_tables, scan_file_ids,
)
from xi.tex.xi_recolor import recolour_zone_dat
from xi.xi_config import FFXI_DIR
from xi.zone.xi_list import zone_file_id


# ---------------------------------------------------------------------------
# File-ID formulas for all four zone DAT types
# ---------------------------------------------------------------------------

def zone_model_file_id(zone_id: int) -> int:
    return zone_file_id(zone_id)

def zone_event_file_id(zone_id: int) -> int:
    if zone_id < 0x100:
        return 5820 + zone_id
    return zone_model_file_id(zone_id) + 1100

def zone_dialog_file_id(zone_id: int) -> int:
    if zone_id < 0x100:
        return 6420 + zone_id
    return zone_model_file_id(zone_id) + 1700

def zone_npc_file_id(zone_id: int) -> int:
    if zone_id < 0x100:
        return 6720 + zone_id
    return zone_model_file_id(zone_id) + 2600

ZONE_DAT_TYPES = {
    'model':  zone_model_file_id,
    'event':  zone_event_file_id,
    'dialog': zone_dialog_file_id,
    'npc':    zone_npc_file_id,
}


# ---------------------------------------------------------------------------
# Zone geometry scaling
# ---------------------------------------------------------------------------

def scale_zone_dat(source: Path, output: Path, scale: float) -> dict:
    """Scale all zone geometry by a uniform factor.

    Handles:
      - 0x2E mesh vertices + bounding boxes (decrypt/re-encrypt)
      - 0x1C placement positions + space-tree bounding boxes + collision
        transforms (decrypt/re-encrypt)

    Returns ``{'meshes': N, 'vertices': N, 'placements': N, 'tree_nodes': N}``.
    """
    from xi.zone.xi_export import parse_sections, SECTION_TYPE_ZONE_MESH
    from xi.zone.xi_decrypt import (
        load_key_tables, decrypt_zone_mesh, reencrypt_zone_mesh,
        decrypt_zone_objects, reencrypt_zone_objects,
    )
    from xi.zone.xi_import import _mesh_vertex_offsets
    from xi.zone.xi_zonedef import (
        SECTION_TYPE_ZONE_DEF, OBJ_RECORD_SIZE, OBJ_ARRAY_START,
    )

    dll_path = Path(FFXI_DIR) / 'FFXiMain.dll'
    if not dll_path.is_file():
        raise click.ClickException('FFXiMain.dll not found (needed for zone decryption)')
    t1, t2 = load_key_tables(dll_path)

    data = bytearray(source.read_bytes())
    sections = parse_sections(data)
    stats = {'meshes': 0, 'vertices': 0, 'placements': 0, 'tree_nodes': 0}

    # ── Scale 0x2E mesh vertices ────────────────────────────────────
    for s in sections:
        if s.type_code != SECTION_TYPE_ZONE_MESH:
            continue
        decrypt_zone_mesh(data, s.data_start, t1, t2)

        try:
            offsets = _mesh_vertex_offsets(bytes(data), s)
        except (struct.error, IndexError, OverflowError):
            reencrypt_zone_mesh(data, s.data_start, t1, t2)
            continue

        if not offsets:
            reencrypt_zone_mesh(data, s.data_start, t1, t2)
            continue

        scaled_ok = True
        for off, _stride in offsets:
            for j in range(3):
                foff = off + j * 4
                val = struct.unpack_from('<f', data, foff)[0]
                if abs(val) > 1e30 or val != val:
                    scaled_ok = False
                    break
                struct.pack_into('<f', data, foff, val * scale)
            if not scaled_ok:
                break

        if not scaled_ok:
            chunk = bytearray(source.read_bytes()[s.start:s.start + s.size])
            data[s.start:s.start + s.size] = chunk
            continue

        stats['vertices'] += len(offsets)

        # Update mesh bounding boxes
        ds = s.data_start
        def_start = ds + 0x20
        xs, ys, zs = [], [], []
        for off, _ in offsets:
            x, y, z = struct.unpack_from('<3f', data, off)
            xs.append(x); ys.append(y); zs.append(z)
        bbox = (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))
        struct.pack_into('<6f', data, def_start + 0x04, *bbox)
        struct.pack_into('<6f', data, def_start + 0x24, *bbox)

        reencrypt_zone_mesh(data, s.data_start, t1, t2)
        stats['meshes'] += 1

    # ── Scale 0x1C zonedef ──────────────────────────────────────────
    for s in sections:
        if s.type_code != SECTION_TYPE_ZONE_DEF:
            continue
        node_count = decrypt_zone_objects(data, s.data_start, s.start, s.size, t1)
        ds = s.data_start

        for i in range(node_count):
            b = ds + OBJ_ARRAY_START + i * OBJ_RECORD_SIZE
            for j in range(3):
                off = b + 0x10 + j * 4
                val = struct.unpack_from('<f', data, off)[0]
                struct.pack_into('<f', data, off, val * scale)
            dd_off = b + 0x40
            val = struct.unpack_from('<f', data, dd_off)[0]
            struct.pack_into('<f', data, dd_off, val * scale)
            stats['placements'] += 1

        spacetree_rel = struct.unpack_from('<I', data, ds + 0x10)[0]
        if spacetree_rel != 0:
            _scale_tree_nodes(data, ds, spacetree_rel, scale, stats)

        collision_rel = struct.unpack_from('<I', data, ds + 0x08)[0]
        if collision_rel != 0:
            _scale_collision(data, ds, collision_rel, scale)

        reencrypt_zone_objects(data, s.data_start, s.start, s.size, t1)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(bytes(data))
    return stats


def _scale_tree_nodes(data: bytearray, ds: int, root_rel: int,
                      scale: float, stats: dict):
    """Recursively scale space-tree node bounding boxes (8×vec3 each)."""
    seen = set()

    def walk(rel):
        if rel == 0 or rel in seen:
            return
        seen.add(rel)
        pos = ds + rel
        for i in range(24):
            off = pos + i * 4
            val = struct.unpack_from('<f', data, off)[0]
            struct.pack_into('<f', data, off, val * scale)
        stats['tree_nodes'] += 1
        for k in range(4):
            child = struct.unpack_from('<I', data, pos + 0x68 + k * 4)[0]
            walk(child)

    walk(root_rel)


def _scale_collision(data: bytearray, ds: int, collision_rel: int, scale: float):
    """Scale collision mesh vertices, transforms, and grid map."""
    cb = ds + collision_rel

    num_meshes       = struct.unpack_from('<I', data, cb)[0]
    first_mesh_rel   = struct.unpack_from('<I', data, cb + 0x04)[0]
    pairs_rel        = struct.unpack_from('<I', data, cb + 0x0C)[0]
    map_rel          = struct.unpack_from('<I', data, cb + 0x10)[0]
    transforms_rel   = struct.unpack_from('<I', data, cb + 0x14)[0]

    if first_mesh_rel != 0:
        mp = ds + first_mesh_rel
        for _ in range(num_meshes):
            if mp + 16 > len(data):
                break
            pos_rel  = struct.unpack_from('<I', data, mp)[0]
            norm_rel = struct.unpack_from('<I', data, mp + 4)[0]
            idx_rel  = struct.unpack_from('<I', data, mp + 8)[0]
            tri_count = struct.unpack_from('<H', data, mp + 0x0C)[0]

            if pos_rel != 0 and tri_count > 0:
                vert_count = (norm_rel - pos_rel) // 12 if norm_rel > pos_rel else tri_count * 3
                vp = ds + pos_rel
                for v in range(vert_count):
                    for j in range(3):
                        off = vp + v * 12 + j * 4
                        if off + 4 > len(data):
                            break
                        val = struct.unpack_from('<f', data, off)[0]
                        struct.pack_into('<f', data, off, val * scale)

            if idx_rel != 0:
                mp = ds + idx_rel + tri_count * 8
            else:
                break

    if transforms_rel != 0 and pairs_rel > transforms_rel:
        span = pairs_rel - transforms_rel
        t_count = span // 0xC0
        for i in range(t_count):
            tp = ds + transforms_rel + i * 0xC0
            for row in range(3):
                off = tp + row * 16 + 12
                val = struct.unpack_from('<f', data, off)[0]
                struct.pack_into('<f', data, off, val * scale)

    if map_rel != 0:
        map_base = ds + map_rel
        for j in range(2):
            off = map_base + j * 4
            val = struct.unpack_from('<f', data, off)[0]
            struct.pack_into('<f', data, off, val * scale)
        off = map_base + 8
        val = struct.unpack_from('<f', data, off)[0]
        struct.pack_into('<f', data, off, val * scale)


# ---------------------------------------------------------------------------
# FTABLE registration helpers
# ---------------------------------------------------------------------------

def _ensure_rom10():
    rom10 = Path(FFXI_DIR) / 'ROM10'
    ft10  = rom10 / 'FTABLE10.DAT'
    vt10  = rom10 / 'VTABLE10.DAT'
    ft_size = (Path(FFXI_DIR) / 'FTABLE.DAT').stat().st_size
    vt_size = (Path(FFXI_DIR) / 'VTABLE.DAT').stat().st_size
    rom10.mkdir(parents=True, exist_ok=True)
    if not ft10.exists():
        ft10.write_bytes(b'\x00' * ft_size)
    if not vt10.exists():
        vt10.write_bytes(b'\x00' * vt_size)
    return ft10, vt10

def _next_free_slot(rom10_dir: Path, subdir: int = 1) -> int:
    d = rom10_dir / str(subdir)
    d.mkdir(parents=True, exist_ok=True)
    used = {int(f.stem) for f in d.glob('*.DAT') if f.stem.isdigit()}
    slot = 0
    while slot in used:
        slot += 1
    return slot


# Blank `zone new` zones clone their event/dialog/npc companions from here so the
# client's per-zone file lookups (model+1100/+1700/+2600) all resolve. Altar Room
# (152) is the template's origin and has a tiny 1.4 KB entity list.
TEMPLATE_COMPANION_ZONE = 152


def register_zone_file(fid: int, subdir: int, slot: int, *,
                       base: bool = True, dry_run: bool = False) -> None:
    """Register one file ID → ``ROM10/<subdir>/<slot>.DAT`` with version byte 10.

    Writes the per-ROM tables (FTABLE10/VTABLE10) AND, by default, the base
    FTABLE.DAT/VTABLE.DAT — the client needs both to route a custom file ID into
    ROM10. (base table = rom_idx 1, NOT 0.)"""
    ftval = (subdir << 7) | slot
    patch_table(ftable_path(10), vtable_path(10), fid, ftval, 10, dry_run=dry_run)
    if base:
        patch_table(ftable_path(1), vtable_path(1), fid, ftval, 10, dry_run=dry_run)


def unregister_zone_file(fid: int, *, base: bool = True) -> None:
    """Zero a file ID's entry in the ROM10 tables and, by default, the base tables."""
    patch_table(ftable_path(10), vtable_path(10), fid, 0, 0)
    if base:
        patch_table(ftable_path(1), vtable_path(1), fid, 0, 0)


def clone_zone_companions(zone_id: int, companion_zone: int, subdir: int,
                          rom10_dir: Path, tables: dict, *,
                          reserved: set | None = None, base: bool = True) -> list:
    """Copy event/dialog/npc DATs from *companion_zone* into ``ROM10/<subdir>`` and
    register them for *zone_id*.  Returns ``[(dtype, fid, slot, dst), ...]``.

    Without these three the client throws **FFXI-2003 "Failed to read data"** on
    zone-in: it computes their file IDs (model+1100/+1700/+2600) and requires all
    of them — especially the npc/entity-list file (model+2600)."""
    reserved = reserved if reserved is not None else set()
    out = []
    for dtype, fn in (('event', zone_event_file_id),
                      ('dialog', zone_dialog_file_id),
                      ('npc', zone_npc_file_id)):
        src_fid, tgt_fid = fn(companion_zone), fn(zone_id)
        hits = scan_file_ids([src_fid], tables)
        if not hits:
            continue
        src = Path(FFXI_DIR) / hits[0]['dat']
        if not src.exists():
            continue
        slot = _next_free_slot(rom10_dir, subdir)
        while slot in reserved:
            slot += 1
        reserved.add(slot)
        dst = rom10_dir / str(subdir) / f'{slot}.DAT'
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        register_zone_file(tgt_fid, subdir, slot, base=base)
        out.append((dtype, tgt_fid, slot, dst))
    return out


# ---------------------------------------------------------------------------
# Zone name resolution
# ---------------------------------------------------------------------------

def _load_zone_names() -> list[str]:
    from xi.zone.xi_list import parse_dmsg, ZONE_NAME_DAT
    return parse_dmsg((Path(FFXI_DIR) / ZONE_NAME_DAT).read_bytes())

def _find_zone_by_name(name: str, zone_names: list[str]) -> int | None:
    lower = name.lower().replace('_', ' ')
    for zid, zname in enumerate(zone_names):
        if zname.lower().replace('_', ' ') == lower:
            return zid
    matches = [(zid, zname) for zid, zname in enumerate(zone_names)
               if lower in zname.lower().replace('_', ' ')]
    return matches[0][0] if len(matches) == 1 else None

def _next_free_zone_id(tables: dict) -> int:
    for zid in range(400, 512):
        if not scan_file_ids([zone_model_file_id(zid)], tables):
            return zid
    raise click.ClickException('No free zone IDs in range 400–511.')


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command('inject')
@click.argument('name')
@click.option('--clone', type=str, required=True,
              help='Source zone — name or ID.')
@click.option('--hue', type=float, default=None,
              help='Hue shift in degrees (0–360).')
@click.option('--saturation', type=float, default=None,
              help='Saturation adjust (-100 to 100).')
@click.option('--lightness', type=float, default=None,
              help='Brightness adjust (-100 to 100).')
@click.option('--tint', type=str, default=None,
              help='Tint colour (#RRGGBB or #RRGGBBAA).')
@click.option('--blend', type=click.Choice(['normal', 'multiply', 'screen', 'overlay', 'add']),
              default='normal', help='Blend mode for --tint.')
@click.option('--env-lightness', type=float, default=None,
              help='Environment lighting adjustment (-100 to 100).')
@click.option('--fog-tint', type=str, default=None,
              help='Override fog colour (#RRGGBB).')
@click.option('--fog-end', type=float, default=None,
              help='Fog far distance (lower = denser). Original zones use 100–500.')
@click.option('--fog-start', type=float, default=None,
              help='Fog near distance (default 0).')
@click.option('--zone-id', type=int, default=None,
              help='Target zone ID (default: auto-assign from 400+).')
@click.option('--model-dat', type=str, default=None,
              help='Override model DAT path.')
@click.option('--subdir', type=int, default=1,
              help='ROM10 subdirectory (default: 1).')
@click.option('--dry-run', is_flag=True, help='Show plan without writing.')
def cmd(name: str, clone: str, hue: float | None, saturation: float | None,
        lightness: float | None, tint: str | None, blend: str,
        env_lightness: float | None, fog_tint: str | None,
        fog_end: float | None, fog_start: float | None,
        zone_id: int | None, model_dat: str | None, subdir: int, dry_run: bool):
    """Create a new zone by cloning an existing one into FTABLE10.

    NAME is the new zone's name (e.g. "Verdant Sarutabaruta").

    \b
    Examples:
      xi zone inject "Verdant Sarutabaruta" --clone 115 --hue 120
      xi zone inject "Crimson Ronfaure" --clone "West Ronfaure" --hue 240
      xi zone inject "Shadow Jeuno" --clone 244 --hue 180 --lightness -30 \\
          --env-lightness -60 --fog-tint "#1a0a2a" --fog-end 80
      xi zone inject "Dark Bastok" --clone "Bastok Mines" --tint "#330066aa" --blend overlay
    """
    tables = load_all_tables()
    zone_names = _load_zone_names()

    try:
        clone_zid = int(clone)
    except ValueError:
        clone_zid = _find_zone_by_name(clone, zone_names)
        if clone_zid is None:
            raise click.ClickException(f'Could not find zone "{clone}".')

    clone_name = zone_names[clone_zid] if clone_zid < len(zone_names) else f'zone {clone_zid}'
    has_adjustments = any([hue, saturation, lightness, tint, env_lightness, fog_tint,
                           fog_end is not None, fog_start is not None])

    desc = []
    if hue:        desc.append(f'hue {hue}°')
    if saturation: desc.append(f'sat {saturation:+.0f}%')
    if lightness:  desc.append(f'lit {lightness:+.0f}%')
    if tint:       desc.append(f'tint {tint} ({blend})')
    if env_lightness: desc.append(f'env {env_lightness:+.0f}%')
    if fog_tint:   desc.append(f'fog {fog_tint}')
    if fog_end is not None: desc.append(f'fog_end={fog_end}')
    click.echo(f'Cloning zone {clone_zid} ({clone_name}) → "{name}"')
    if desc:
        click.echo(f'  Adjustments: {", ".join(desc)}')

    if model_dat:
        source = Path(FFXI_DIR) / model_dat if not Path(model_dat).is_absolute() else Path(model_dat)
    else:
        hits = scan_file_ids([zone_model_file_id(clone_zid)], tables)
        if not hits:
            raise click.ClickException(f'Model DAT for zone {clone_zid} not found.')
        source = Path(FFXI_DIR) / hits[0]['dat']
    if not source.exists():
        raise click.ClickException(f'Source DAT not found: {source}')
    click.echo(f'  Source: {source.name} ({source.stat().st_size:,} bytes)')

    if zone_id is None:
        zone_id = _next_free_zone_id(tables)
    else:
        if scan_file_ids([zone_model_file_id(zone_id)], tables):
            raise click.ClickException(f'Zone {zone_id} already registered.')
    click.echo(f'  Target: zone {zone_id}')

    ft10, vt10 = _ensure_rom10() if not dry_run else (None, None)
    rom10_dir = Path(FFXI_DIR) / 'ROM10'
    plan = []

    # _next_free_slot reads disk state, but the planner allocates multiple
    # slots before any write happens — so we must track in-flight allocations
    # locally or every entry collides at slot 0.
    reserved: set[int] = set()
    def _alloc(subdir_: int) -> int:
        if dry_run:
            slot = 0
        else:
            slot = _next_free_slot(rom10_dir, subdir_)
            while slot in reserved:
                slot += 1
        reserved.add(slot)
        return slot

    slot = _alloc(subdir)
    plan.append(('model', zone_model_file_id(zone_id), subdir, slot, source))

    for dtype, fn in [('event', zone_event_file_id),
                      ('dialog', zone_dialog_file_id),
                      ('npc', zone_npc_file_id)]:
        src_hits = scan_file_ids([fn(clone_zid)], tables)
        if not src_hits:
            click.echo(click.style(f'  warning: {dtype} DAT not found, skipping', fg='yellow'))
            continue
        src_dat = Path(FFXI_DIR) / src_hits[0]['dat']
        if not src_dat.exists():
            continue
        slot = _alloc(subdir)
        plan.append((dtype, ZONE_DAT_TYPES[dtype](zone_id), subdir, slot, src_dat))

    click.echo(f'\n  Injection plan:')
    for dtype, fid, sd, sl, src in plan:
        click.echo(f'    {dtype:8s}  file_id={fid:6d}  → ROM10/{sd}/{sl}.DAT')

    if dry_run:
        click.echo(click.style('\n  Dry run — nothing written.', fg='cyan'))
        return

    for dtype, fid, sd, sl, src in plan:
        dst = rom10_dir / str(sd) / f'{sl}.DAT'
        dst.parent.mkdir(parents=True, exist_ok=True)

        if dtype == 'model' and has_adjustments:
            click.echo(f'  Recolouring {src.name} → {dst.name}')
            stats = recolour_zone_dat(
                src, dst,
                hue=hue or 0, saturation=saturation or 0, lightness=lightness or 0,
                tint=tint, blend_mode=blend,
                env_lightness=env_lightness, fog_tint=fog_tint,
                fog_end=fog_end, fog_start=fog_start,
            )
            click.echo(f'    {stats["dxt"]} DXT + {stats["paletted"]} paletted + '
                       f'{stats["environment"]} environment sections')
        else:
            click.echo(f'  Copying {src.name} → {dst.name}')
            shutil.copy2(src, dst)

        register_zone_file(fid, sd, sl)   # ROM10 + base tables

    click.echo(click.style(f'\n✓ Zone {zone_id} "{name}" registered.', fg='green'))
    click.echo(f'\nServer-side TODO:')
    click.echo(f'  1. Apply patches/zone_max_512.patch (MAX_ZONEID >= {zone_id + 1})')
    click.echo(f'  2. INSERT INTO zone_settings VALUES ({zone_id}, ...)')
    click.echo(f'  3. Create scripts/zones/{name.replace(" ", "_")}/')
    click.echo(f'  4. Restart server + client')

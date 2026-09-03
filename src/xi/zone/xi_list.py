import json
import struct
from pathlib import Path

import click

from xi.ftable.xi_core import load_all_tables, scan_file_ids
from xi.xi_config import FFXI_DIR


ZONE_NAME_DAT = 'ROM/165/84.DAT'  # d_msg, XOR 0xFF (xim ZoneNameTable)

def probe_zone_mesh_name(path: Path) -> str | None:
    """Return the first mesh name if the file is a zone DAT, else None.

    Zone DATs are identified by having BOTH a 0x2E (ZoneMesh) section AND a 0x1C
    (ZoneDef/placement table) section.  Entity/prop DATs have 0x2E but not 0x1C, so
    this filter excludes standalone prop geometry files.

    Uses seek-based scanning so only section headers (8 bytes each) are read — the
    section bodies are skipped, which is important since the first 0x2E can be hundreds
    of KB into a zone DAT.

    Returns '' when the mesh name is encrypted (mode >= 5); the caller should fall back
    to a path-derived display name.  Returns None when the file is not a zone DAT.
    """
    mesh_name: str | None = None  # set when first 0x2E is seen
    has_1c = False
    try:
        with path.open('rb') as f:
            pos = 0
            section_count = 0
            while section_count < 4096:
                f.seek(pos)
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                meta = struct.unpack_from('<I', hdr, 4)[0]
                type_code = meta & 0x7F
                size = ((meta >> 7) & 0xFFFFF) * 0x10
                if size <= 0:
                    break
                section_count += 1
                if type_code == 0x2E and mesh_name is None:
                    f.seek(pos + 0x10)
                    sec_data = f.read(0x20)
                    if len(sec_data) >= 0x20:
                        mode = (struct.unpack_from('<I', sec_data, 0)[0] >> 24) & 0xFF
                        if mode >= 5:
                            mesh_name = ''      # encrypted; fall back to path name
                        else:
                            raw = sec_data[0x10:0x20]
                            mesh_name = raw.split(b'\x00')[0].decode('ascii', errors='replace').strip()
                    else:
                        mesh_name = ''
                elif type_code == 0x1C:
                    has_1c = True
                if mesh_name is not None and has_1c:
                    return mesh_name            # confirmed zone DAT
                pos = (pos + size + 0xF) & ~0xF
    except OSError:
        pass
    return None


def parse_dmsg(data: bytes, bitmask: int = 0xFF) -> list[str]:
    """Return the first string from each d_msg block."""
    if data[:5] != b'd_msg':
        raise ValueError('not a d_msg file')

    (_unk0, file_size, table_offset, table_size, string_block_size,
     _string_section_size, num_strings, _unk1) = struct.unpack_from('<8I', data, 0x10)

    buf = bytearray(data)
    if bitmask:
        for i in range(table_offset, file_size):
            buf[i] ^= bitmask

    def parse_block(start: int) -> str:
        n = struct.unpack_from('<I', buf, start)[0]
        offsets = [struct.unpack_from('<I', buf, start + 4 + i * 8)[0] for i in range(n)]
        for off in offsets:
            bp = start + off
            if struct.unpack_from('<I', buf, bp)[0] == 1:
                sp = bp + 4 + 0x18
                end = buf.index(0, sp)
                return buf[sp:end].decode('cp932', 'replace').strip()
        return ''

    names = []
    if table_size == 0:
        for i in range(num_strings):
            names.append(parse_block(table_offset + string_block_size * i))
    else:
        offs = [struct.unpack_from('<I', buf, table_offset + i * 8)[0] for i in range(num_strings)]
        string_start = table_offset + num_strings * 8
        for o in offs:
            names.append(parse_block(string_start + o))
    return names


def zone_file_id(zone_id: int) -> int:
    return 0x64 + zone_id if zone_id < 0x100 else 0x147B3 + (zone_id - 0x100)


DEV_GROUP = 'Dev / Prototype'

# Hand-curated leftover development maps. These never shipped as playable zones:
# they have no entry in the zone-name table and no zone id, so neither the named
# scan above nor the FTABLE room scan can surface them — they are listed by path.
#
# Names marked "?" are inferred from the mesh/texture names inside each DAT
# (e.g. ROM/0/39 carries `ghe_jk0`/`uge_ie*` textures → Ghelsba), not from any
# official source. Rename freely.
#
# Most of these use the pre-production layout — 0x54 placement records and/or
# multi-group meshes. They LOAD, but publishing edits back is not supported:
# the writer hardcodes the 0x64 record size. See xi.zone.xi_zonedef.
DEV_ZONES: list[tuple[str, str]] = [
    ('ROM/1/5.DAT',  'Character Creation'),
    ('ROM/0/28.DAT', 'Dev Town — windmill + bridge'),
    ('ROM/0/29.DAT', 'Dev Snowfield'),                      # snowfiel* textures
    ('ROM/0/30.DAT', 'Dev Snowfield 2'),                    # setugen = 雪原
    ('ROM/0/31.DAT', 'Dev Cave + Waterfall'),               # gratest_cave / _taki
    ('ROM/0/32.DAT', 'Dev Boss Test'),                      # ren_testboss_a
    ('ROM/0/33.DAT', 'Dev Castle Town'),                    # gratest_* walls/towers
    ('ROM/0/34.DAT', 'Dev Desert'),                         # sabaku = 砂漠
    ('ROM/0/35.DAT', 'Dev Forest — moss test'),             # m_koke = 苔 (moss)
    ('ROM/0/36.DAT', 'Dev Cliffs + Forest'),                # cliff_f1 / forest_g
    ('ROM/0/37.DAT', 'Dev World Map / diorama?'),           # anai syama* + ship
    ('ROM/0/38.DAT', 'Dev Mountain Terrain'),               # yama, gake = cliff
    ('ROM/0/39.DAT', 'Fort Ghelsba prototype?'),            # ghe_jk0, uge_ie*
    ('ROM/0/40.DAT', 'Dev Test Plane'),                     # 100 flat tiles, 400x400
    ('ROM/0/41.DAT', 'Castle prototype — very early?'),     # mix12_28castle*
    ('ROM/0/42.DAT', 'Tower prototype?'),                   # twr2bai tower_*
    ('ROM/0/43.DAT', "Northern San d'Oria prototype?"),     # origin san_d/san_g/ron_w
    ('ROM/0/44.DAT', 'Bastok interior prototype?'),         # r_1ba02_room
    ('ROM/0/46.DAT', "Ru'Lude Gardens prototype (untextured)"),  # 3 nation crests, ekken
    ('ROM/0/47.DAT', "Chateau d'Oraguille prototype?"),     # uwa_*, monsyou, e_ekken
    ('ROM/0/48.DAT', 'Selbina prototype (untextured)'),     # ami = nets, cen_shop
    ('ROM/0/49.DAT', 'Ship / airship room prototype?'),     # sroom_*, ship_roo
]

# Hand-identified mog houses. These are private "Rooms" (see get_room_entries) with
# no entry in the zone-name table, so they'd otherwise surface unnamed — their raw
# path or an internal shell mesh name shared across several rooms (e.g. "dn00").
#
# ROM/1/20-23 (rental) and 45-47 (home nation): DATura lore/DAT crossref for Jeuno
# (research/external/DATura, visually verified) plus community DAT lists (the
# "Ultimate Moghouse Expansion" thread), user-confirmed in-editor 2026-09.
MOG_HOUSE_NAMES: dict[str, str] = {
    'ROM/1/20.DAT': 'Jeuno Mog House',
    'ROM/1/21.DAT': "San d'Oria Mog House (rental)",
    'ROM/1/22.DAT': 'Bastok Mog House (rental)',
    'ROM/1/23.DAT': 'Windurst Mog House (rental)',
    'ROM/1/45.DAT': "San d'Oria Mog House",
    'ROM/1/46.DAT': 'Bastok Mog House',
    'ROM/1/47.DAT': 'Windurst Mog House',
}


def get_dev_entries(path_prefix: str = 'game/') -> list[dict]:
    """Curated dev/prototype DATs (see DEV_ZONES), skipping any not on disk.

    No 'id' key — these have no zone id, same as room entries.
    """
    out = []
    for dat, name in DEV_ZONES:
        if (Path(FFXI_DIR) / dat).is_file():
            out.append({'name': name, 'path': path_prefix + dat, 'group': DEV_GROUP})
    return out


def get_zone_entries(path_prefix: str = 'game/', include_rooms: bool = False,
                     include_dev: bool = False) -> list[dict]:
    name_path = Path(FFXI_DIR) / ZONE_NAME_DAT
    names = parse_dmsg(name_path.read_bytes())
    tables = load_all_tables()

    zones = []
    for zone_id, name in enumerate(names):
        name = name.strip()
        if not name or name in ('none', '?'):
            continue

        hits = scan_file_ids([zone_file_id(zone_id)], tables)
        if not hits:
            continue

        dat = hits[0]['dat']
        if not (Path(FFXI_DIR) / dat).is_file():
            continue

        zones.append({'id': zone_id, 'name': name, 'path': path_prefix + dat})

    zones.sort(key=lambda z: z['name'].lower())

    if include_dev:
        zones += get_dev_entries(path_prefix=path_prefix)

    if include_rooms:
        zones += get_room_entries(path_prefix=path_prefix, _known_tables=tables)

    return zones


def get_room_entries(path_prefix: str = 'game/', _known_tables=None) -> list[dict]:
    """Scan ftable file IDs for zone-format DATs not covered by the named zone list.

    Room zones (mog houses, mission rooms, etc.) live at file IDs outside the range
    used by zone_file_id(), so they never appear in the d_msg name table scan.  We
    build the known-DAT exclusion set first, then walk each ROM's ftable looking for
    new zone-format files.

    Scanning is capped at _ROOM_SCAN_LIMIT file IDs per ROM table.  All known room
    zones found in practice fall well within this window.
    """
    name_path = Path(FFXI_DIR) / ZONE_NAME_DAT
    names = parse_dmsg(name_path.read_bytes())
    tables = _known_tables or load_all_tables()

    # Build exclusion set from all named zones
    known_dats: set[str] = set()
    for zone_id, name in enumerate(names):
        hits = scan_file_ids([zone_file_id(zone_id)], tables)
        if hits:
            known_dats.add(hits[0]['dat'])

    _ROOM_SCAN_LIMIT = 2000

    rooms = []
    seen_dats: set[str] = set(known_dats)

    for rom_idx, (fdata, vdata) in tables.items():
        limit = min(len(fdata) // 2, _ROOM_SCAN_LIMIT)
        for file_id in range(1, limit):
            if file_id >= len(vdata):
                break
            vt = vdata[file_id]
            if vt == 0:
                continue
            ft = struct.unpack_from('<H', fdata, file_id * 2)[0]
            subdir = ft >> 7
            file_num = ft & 0x7F
            dat = f'ROM/{subdir}/{file_num}.DAT' if vt == 1 else f'ROM{vt}/{subdir}/{file_num}.DAT'

            if dat in seen_dats:
                continue
            seen_dats.add(dat)

            full_path = Path(FFXI_DIR) / dat
            mesh_name = probe_zone_mesh_name(full_path)
            if mesh_name is None:
                continue  # not a zone DAT

            # Fall back to "ROM/x/y" when the name is encrypted; MOG_HOUSE_NAMES wins either way.
            display = MOG_HOUSE_NAMES.get(dat) or (mesh_name if mesh_name else dat.removesuffix('.DAT'))
            rooms.append({'name': display, 'path': path_prefix + dat, 'group': 'Rooms'})

    # Curated mog houses the strict probe above doesn't recognize as zone DATs.
    for dat, name in MOG_HOUSE_NAMES.items():
        if dat in seen_dats:
            continue
        if (Path(FFXI_DIR) / dat).is_file():
            rooms.append({'name': name, 'path': path_prefix + dat, 'group': 'Rooms'})
            seen_dats.add(dat)

    rooms.sort(key=lambda r: r['name'].lower())
    return rooms


@click.command('list')
@click.option('--json', 'as_json', is_flag=True, help='Print level-editor zones JSON.')
@click.option('--output', '-o', type=click.Path(path_type=Path), default=None,
              help='Write output to a file instead of stdout.')
@click.option('--path-prefix', default='game/', show_default=True,
              help='Prefix used for DAT paths in JSON output.')
@click.option('--rooms', is_flag=True, default=False,
              help='Include unnamed zone DATs (rooms, private zones) as a Rooms group.')
@click.option('--dev', is_flag=True, default=False,
              help='Include curated dev/prototype DATs as a "Dev / Prototype" group.')
@click.option('--search', '-s', default=None, metavar='TEXT',
              help='Filter to zones whose name contains TEXT (case-insensitive).')
def cmd(as_json: bool, output: Path | None, path_prefix: str, rooms: bool, dev: bool,
        search: str | None):
    """List FFXI zones resolved through FTABLE."""
    try:
        zones = get_zone_entries(path_prefix=path_prefix, include_rooms=rooms,
                                 include_dev=dev)
    except Exception as e:
        raise click.ClickException(str(e)) from e

    if search:
        q = search.lower()
        zones = [z for z in zones if q in z['name'].lower()]

    if as_json:
        text = json.dumps(zones, ensure_ascii=False, indent=0)
    else:
        header = f'Found {len(zones):,} zone(s)' + (f' matching {search!r}' if search else '')
        lines = [header, '', f'  {"id":>4}  {"name":<32} path', '-' * 80]
        lines.extend(f'  {z["id"]:>4}  {z["name"]:<32} {z["path"]}' for z in zones)
        text = '\n'.join(lines)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding='utf-8')
        click.echo(f'Wrote {len(zones)} zone(s) -> {output}')
    else:
        click.echo(text)

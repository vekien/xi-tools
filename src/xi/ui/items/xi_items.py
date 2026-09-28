"""``xi ui items`` — search and export FFXI item DATs.

Reads binary item DATs directly from FFXI_DIR. Cipher: rotate-left-3 per byte.
Record stride is detected per file — 0xC00 on a legacy install, 0x1400 on
retail since the 10 September 2026 update — so every command here works
against either (``xi ui items info`` shows what was detected).

Item types and DAT ranges:
  general     0       - 4095    ROM/118/106.DAT
  consumable  4096    - 8191    ROM/118/107.DAT
  puppet      8192    - 8703    ROM/118/110.DAT
  armor       10240   - 28671   ROM/118/109.DAT + ROM/286/73.DAT
  weapon      16384   - 23039   ROM/118/108.DAT
  items 7     30720   - 31743   ROM/387/14.DAT   (retail Sept 2026+, placeholders so far)
  monstrosity 29696   - 30719   ROM/288/80.DAT   (the Monstrosity instinct table)
  mount       model + key item + name strings (separate system)
"""

import io
import json
import struct
from dataclasses import asdict
from pathlib import Path

import click

from xi.ui.items.xi_parser import (
    ITEM_DATS, STRIDE, TEXT_OFFSETS, TYPE_NAME, ItemDat,
    parse_dat, record_count, _decrypt, _encrypt, _read_strings, _resolve_text_offset,
    decode_flags, decode_jobs, encode_flags, encode_jobs,
    _patch_record, build_record,
)
from xi.ui.items.xi_layout import ICON_OFFSET, ICON_DATA, describe, detect_stride_path
from xi.xi_config import FFXI_DIR, output_path_for

_STUB = 'Not yet implemented.'
_EXPORT_ROOT = Path('exports') / 'ui' / 'items'


def _export_path(name: str) -> Path:
    return _EXPORT_ROOT / f'{name}.json'


def _write_export(data: list, path: Path, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    click.echo(f'Exported {len(data)} {label} -> {path}')


# ── Shared helpers ─────────────────────────────────────────────────────────────

_SPLIT_GROUPS = {
    'items':      ['Items_1', 'Items_2', 'Items_3', 'Items_4', 'Items_5', 'Items_6', 'Items_7'],
    'consumable': ['Consumable'],
    'puppet':     ['Puppet'],
    'armor':      ['Armor_1', 'Armor_2'],
    'weapon':     ['Weapons'],
    'monstrosity': ['Monstrosity_1', 'Monstrosity_2'],
    'misc':       ['Moblin', 'RoE_Objectives', 'RoE_Categories', 'Gil'],
}


def _dat_path(en_rom: str) -> Path:
    return Path(FFXI_DIR) / Path(en_rom.replace('/', '\\'))


def _item_to_dict(item):
    d = asdict(item)
    d.pop('icon_data', None)
    d.pop('dat', None)  # absolute local path — dat_ui (ROM-relative) is the portable form
    d['flags_decoded'] = decode_flags(d['flags'])
    if d.get('jobs'):
        d['jobs_list'] = decode_jobs(d['jobs'])
    return d


def _iter_all_dats(type_filter=None):
    """Yield items from every matching DAT, printing which DAT to stderr."""
    for cat_name, base_id, item_type, en_rom, jp_rom in ITEM_DATS:
        if type_filter is not None and item_type != type_filter:
            continue
        en_path = _dat_path(en_rom)
        if not en_path.exists():
            continue
        click.echo(f'Processing {cat_name}: {en_path}', err=True)
        yield from parse_dat(FFXI_DIR, cat_name, base_id, item_type, en_rom, jp_rom)


# ── Top-level group ────────────────────────────────────────────────────────────

@click.group('items')
def group():
    """Item DAT operations — search across all types, or drill into a specific type."""
    pass


@group.command('info')
@click.option('--as-json', is_flag=True, help='Output as JSON.')
def info_cmd(as_json):
    """Show every item DAT under FFXI_DIR with its detected record format.

    Legacy installs use 0xC00-byte records; retail since 10 September 2026
    uses 0x1400-byte records with a wider header. Detection is per file, so a
    mixed install (a retail DAT dropped into a legacy client) shows as mixed.

    \b
    Examples:
      xi ui items info
      xi ui items info --as-json
    """
    rows = []
    for cat_name, base_id, item_type, en_rom, jp_rom in ITEM_DATS:
        for lang, rom in (('en', en_rom), ('jp', jp_rom)):
            if lang == 'jp' and jp_rom == en_rom:
                continue
            p = _dat_path(rom)
            row = {'category': cat_name, 'lang': lang, 'dat': rom, 'base_id': base_id,
                   'type': TYPE_NAME.get(item_type, str(item_type))}
            if not p.exists():
                row.update(exists=False, format=None, stride=None, records=0)
            else:
                try:
                    stride = detect_stride_path(p)
                    row.update(exists=True, stride=stride, format=describe(stride).split(' ')[0],
                               records=p.stat().st_size // stride)
                except ValueError as e:
                    row.update(exists=True, stride=None, format=f'unknown ({e})', records=0)
            rows.append(row)

    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return
    formats = {r['format'] for r in rows if r['exists'] and r['format']}
    click.echo(f'FFXI_DIR: {FFXI_DIR}')
    click.echo('Install: ' + (', '.join(sorted(formats)) if formats else 'no item DATs found')
               + ('  (MIXED — DATs from two client versions)' if len(formats) > 1 else ''))
    click.echo(f'{"category":<15} {"lang":<4} {"dat":<18} {"format":<8} {"stride":>7} {"records":>8}')
    for r in rows:
        if not r['exists']:
            click.echo(f'{r["category"]:<15} {r["lang"]:<4} {r["dat"]:<18} {"missing":<8}')
            continue
        click.echo(f'{r["category"]:<15} {r["lang"]:<4} {r["dat"]:<18} {r["format"]:<8} '
                   f'{(r["stride"] and hex(r["stride"])) or "-":>7} {r["records"]:>8}')


@group.command('search')
@click.argument('query')
@click.option('--exact', is_flag=True, help='Exact match instead of substring.')
@click.option('--as-json', is_flag=True, help='Output as JSON.')
def search_all_cmd(query, exact, as_json):
    """Search for an item by name across all item DATs.

    \b
    Examples:
      xi ui items search "Excalibur"
      xi ui items search "sword" --as-json
    """
    results = []
    for item in _iter_all_dats():
        match = (item.name.lower() == query.lower()) if exact else (query.lower() in item.name.lower())
        if match:
            results.append(item)

    if as_json:
        click.echo(json.dumps([_item_to_dict(i) for i in results], ensure_ascii=False, indent=2))
        return
    if not results:
        click.echo('No matches found.')
        return
    for item in results:
        click.echo(f'[{item.type_name:<10}] #{item.id:>6}  {item.name}')


@group.command('export')
@click.option('--output', '-o', default=None,
              help=f'Output path (default: {_EXPORT_ROOT}/all.json). Ignored with --split.')
@click.option('--no-icons', is_flag=True, help='Omit icon bitmap data.')
@click.option('--split', 'do_split', is_flag=True,
              help='Write one JSON per group (items/consumable/armor/weapon/etc) '
                   f'into {_EXPORT_ROOT}/.')
def export_all_cmd(output, no_icons, do_split):
    """Export all item definitions across all DATs to JSON.

    Prints which DAT is being processed to stderr as it goes.

    \b
    Default output: exports/ui/items/all.json
    Split output:   exports/ui/items/items.json
                    exports/ui/items/consumable.json
                    exports/ui/items/armor.json
                    ... (one file per group)
    """
    if do_split:
        cat_map = {cat: grp for grp, cats in _SPLIT_GROUPS.items() for cat in cats}
        buckets = {grp: [] for grp in _SPLIT_GROUPS}
        buckets['_other'] = []

        for item in _iter_all_dats():
            d = _item_to_dict(item)
            if no_icons:
                d.pop('icon_data', None)
            dat_cat = next(
                (cn for cn, _, _, er, _ in ITEM_DATS if er == d.get('dat_ui')), None)
            grp = cat_map.get(dat_cat, '_other')
            buckets[grp].append(d)

        for grp, rows in buckets.items():
            if not rows:
                continue
            name = grp if grp != '_other' else 'misc_other'
            _write_export(rows, _export_path(name), f'{name} items')
        return

    results = []
    for item in _iter_all_dats():
        d = _item_to_dict(item)
        if no_icons:
            d.pop('icon_data', None)
        results.append(d)

    out_path = Path(output) if output else _export_path('all')
    _write_export(results, out_path, 'items')


# ── Icon subgroup ──────────────────────────────────────────────────────────────

@group.group('icon')
def icon_grp():
    """Export or replace an item's icon bitmap."""
    pass


def _find_item_dat(item_id: int):
    """Return (cat_name, base_id, item_type, en_path, idx) or raise ClickException.

    Record counts come from the detected stride, so the id → DAT resolution is
    the same on a legacy and a retail install."""
    for cat_name, base_id, item_type, en_rom, jp_rom in ITEM_DATS:
        en_path = _dat_path(en_rom)
        if not en_path.exists():
            continue
        try:
            n_records = record_count(en_path)
        except ValueError:
            continue
        if base_id <= item_id < base_id + n_records:
            return cat_name, base_id, item_type, en_path, item_id - base_id
    raise click.ClickException(f'Item ID {item_id} not found in any known DAT.')


@icon_grp.command('export')
@click.argument('item_id', type=int)
@click.option('--output', '-o', default=None,
              help='Output file path. Default: item_<id>.png next to the DAT.')
@click.option('--bmp', 'as_bmp', is_flag=True,
              help='Save as BMP instead of PNG.')
def icon_export_cmd(item_id, output, as_bmp):
    """Extract an item's icon and save it as PNG (or BMP).

    \b
    Examples:
      xi ui items icon export 12345
      xi ui items icon export 12345 -o sword.png
      xi ui items icon export 12345 --bmp -o sword.bmp
    """
    try:
        from PIL import Image
    except ImportError:
        raise click.ClickException('Pillow is required: pip install pillow')

    cat_name, base_id, item_type, en_path, idx = _find_item_dat(item_id)
    dat = ItemDat.load(en_path)
    click.echo(f'Processing {cat_name}: {en_path}  (record {idx}, {describe(dat.stride)})', err=True)

    rec = dat.record(idx)
    icon_size = struct.unpack_from('<I', rec, ICON_OFFSET)[0]
    if icon_size == 0:
        raise click.ClickException(f'Item {item_id} has no icon (size=0).')
    if ICON_DATA + icon_size > dat.stride:
        raise click.ClickException(f'Icon size {icon_size} exceeds record bounds.')

    bmp_bytes = rec[ICON_DATA:ICON_DATA + icon_size]

    ext = 'bmp' if as_bmp else 'png'
    out = Path(output) if output else Path(f'item_{item_id}.{ext}')

    if as_bmp:
        out.write_bytes(bmp_bytes)
    else:
        img = Image.open(io.BytesIO(bmp_bytes))
        img.save(out, format='PNG')

    click.echo(f'Icon exported ({icon_size} bytes BMP -> {out})')


@icon_grp.command('import')
@click.argument('item_id', type=int)
@click.argument('png_file', type=click.Path(exists=True))
@click.option('--dry-run', is_flag=True, help='Show plan without writing.')
def icon_import_cmd(item_id, png_file, dry_run):
    """Replace an item's icon with a PNG image (resized to 32x32).

    Finds the correct DAT, decrypts the record, replaces the BMP icon
    at offset 0x284, re-encrypts and writes to the output directory.

    \b
    Examples:
      xi ui items icon import 12345 my_icon.png
      xi ui items icon import 12345 my_icon.png --dry-run
    """
    try:
        from PIL import Image
    except ImportError:
        raise click.ClickException('Pillow is required: pip install pillow')

    cat_name, base_id, item_type, en_path, idx = _find_item_dat(item_id)
    dat = ItemDat.load(en_path)
    click.echo(f'Found item {item_id} in {cat_name}: {en_path}  (record {idx}, {describe(dat.stride)})', err=True)

    img = Image.open(png_file).resize((32, 32)).convert('RGBA')
    buf = io.BytesIO()
    img.save(buf, format='BMP')
    bmp_bytes = buf.getvalue()

    max_icon_size = dat.icon_capacity
    if len(bmp_bytes) > max_icon_size:
        raise click.ClickException(
            f'BMP too large: {len(bmp_bytes)} bytes > max {max_icon_size}. '
            'Try converting to RGB.')

    if dry_run:
        click.echo(f'Dry run: would write {len(bmp_bytes)}-byte BMP at record+0x284.')
        return

    rec = bytearray(dat.record(idx))
    old_icon_size = struct.unpack_from('<I', rec, ICON_OFFSET)[0]
    struct.pack_into('<I', rec, ICON_OFFSET, len(bmp_bytes))
    rec[ICON_DATA:ICON_DATA + len(bmp_bytes)] = bmp_bytes
    if old_icon_size > len(bmp_bytes):
        tail = ICON_DATA + len(bmp_bytes)
        rec[tail:ICON_DATA + old_icon_size] = b'\x00' * (old_icon_size - len(bmp_bytes))
    dat.set_record(idx, bytes(rec))

    out_path = output_path_for(en_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(dat.encrypted())
    click.echo(f'Icon updated ({len(bmp_bytes)} bytes) -> {out_path}')


# ── Per-type subgroups ─────────────────────────────────────────────────────────

def _make_type_group(name, description, type_id=None, dats=None):
    @click.group(name)
    def grp():
        pass
    grp.__doc__ = description

    def _dats_for_group():
        for cat_name, base_id, item_type, en_rom, jp_rom in ITEM_DATS:
            if dats and cat_name not in dats:
                continue
            if type_id is not None and item_type != type_id:
                continue
            en_path = _dat_path(en_rom)
            if not en_path.exists():
                continue
            yield cat_name, base_id, item_type, en_rom, jp_rom, en_path

    @grp.command('search')
    @click.argument('query')
    @click.option('--exact', is_flag=True)
    @click.option('--as-json', is_flag=True)
    def search_cmd(query, exact, as_json):
        f"""Search for a {name} item by name."""
        results = []
        for cat_name, base_id, item_type, en_rom, jp_rom, en_path in _dats_for_group():
            click.echo(f'Processing {cat_name}: {en_path}', err=True)
            for item in parse_dat(FFXI_DIR, cat_name, base_id, item_type, en_rom, jp_rom):
                match = (item.name.lower() == query.lower()) if exact else (query.lower() in item.name.lower())
                if match:
                    results.append(item)

        if as_json:
            click.echo(json.dumps([_item_to_dict(i) for i in results], ensure_ascii=False, indent=2))
            return
        if not results:
            click.echo('No matches found.')
            return
        for item in results:
            click.echo(f'#{item.id:>6}  {item.name}')

    @grp.command('export')
    @click.option('--output', '-o', default=None,
                  help=f'Output path (default: exports/ui/items/{name}.json).')
    @click.option('--no-icons', is_flag=True)
    def export_cmd(output, no_icons):
        f"""Export all {name} item definitions to JSON.

        Default output: exports/ui/items/{name}.json
        """
        results = []
        for cat_name, base_id, item_type, en_rom, jp_rom, en_path in _dats_for_group():
            click.echo(f'Processing {cat_name}: {en_path}', err=True)
            for item in parse_dat(FFXI_DIR, cat_name, base_id, item_type, en_rom, jp_rom):
                d = _item_to_dict(item)
                if no_icons:
                    d.pop('icon_data', None)
                results.append(d)
        out_path = Path(output) if output else _export_path(name)
        _write_export(results, out_path, f'{name} items')

    @grp.command('json')
    @click.option('--output', '-o', default=None,
                  help=f'Output path (default: {_EXPORT_ROOT}/{name}s/all.json).')
    @click.option('--icons', is_flag=True,
                  help='Include the icon bitmap as base64 (omitted by default).')
    @click.option('--header-bytes', default=0x40, show_default=True,
                  help='How many raw decrypted record bytes to include as header_hex '
                       '(0 to omit). Surfaces fields not yet decoded.')
    def json_cmd(output, icons, header_bytes):
        f"""Dump every {name} record to a JSON file (always written to disk).

        Includes all parsed fields (name, flags, jobs, dmg/delay/dps/skill, …)
        plus ``dat`` / ``dat_ui`` (source DAT), ``record_index`` (slot in the DAT),
        ``format`` (legacy / retail record layout) and ``header_hex`` (the raw
        decrypted record header — note the client item DAT does not store a 3D
        model/file id; gear model ids live in the server's item_equipment table).

        \b
        Default output: {_EXPORT_ROOT}/{name}s/all.json

        \b
        Examples:
          xi ui items {name} json
          xi ui items {name} json -o custom/path.json
          xi ui items {name} json --icons
        """
        import base64
        results = []
        for cat_name, base_id, item_type, en_rom, jp_rom, en_path in _dats_for_group():
            click.echo(f'Processing {cat_name}: {en_path}', err=True)
            dat = ItemDat.load(en_path) if header_bytes > 0 else None
            for item in parse_dat(FFXI_DIR, cat_name, base_id, item_type, en_rom, jp_rom):
                d = _item_to_dict(item)  # pops icon_data; adds flags_decoded / jobs_list
                idx = item.id - base_id
                d['record_index'] = idx
                if dat is not None:
                    d['header_hex'] = dat.record(idx)[:header_bytes].hex()
                if icons and item.icon_data:
                    d['icon_data'] = base64.b64encode(item.icon_data).decode('ascii')
                results.append(d)

        out_path = Path(output) if output else (_EXPORT_ROOT / f'{name}s' / 'all.json')
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        click.echo(f'Wrote {len(results)} {name} records -> {out_path}')

    @grp.command('import')
    @click.argument('json_file', type=click.Path(exists=True))
    @click.option('--dry-run', is_flag=True)
    def import_cmd(json_file, dry_run):
        f"""Patch existing {name} items from a JSON array.

        Accepts the format produced by ``export``. Only fields present in each
        entry are written; unspecified fields are left untouched.
        ``jobs_list`` rebuilds ``jobs``; ``flags_decoded`` rebuilds ``flags``.
        Field offsets follow the DAT's own record format (legacy or retail).

        \b
        Examples:
          xi ui items {name} import edits.json
          xi ui items {name} import edits.json --dry-run
        """
        entries = json.loads(Path(json_file).read_text(encoding='utf-8'))
        if not isinstance(entries, list):
            raise click.ClickException('JSON must be an array of item objects.')

        # Group entries by the DAT they belong to
        by_dat = {}
        for entry in entries:
            item_id = entry.get('id')
            if not isinstance(item_id, int):
                click.echo(f'  skip entry (no id): {entry}', err=True)
                continue
            for cat_name_dat, base_id, item_type_dat, en_rom, jp_rom, en_path in _dats_for_group():
                n_records = record_count(en_path)
                if base_id <= item_id < base_id + n_records:
                    by_dat.setdefault((cat_name_dat, en_rom, item_type_dat), []).append(
                        (item_id - base_id, entry))
                    break

        total = 0
        for (cat_name_dat, en_rom, item_type_dat), patches in by_dat.items():
            en_path = _dat_path(en_rom)
            dat = ItemDat.load(en_path)
            click.echo(f'Processing {cat_name_dat}: {en_path}  ({describe(dat.stride)})', err=True)
            for idx, entry in patches:
                rec_view = bytearray(dat.record(idx))
                _patch_record(rec_view, dict(entry), item_type_dat, dat.format)
                dat.set_record(idx, bytes(rec_view))
                if dry_run:
                    click.echo(f'  dry-run: would patch id={entry["id"]}')
                total += 1
            if not dry_run:
                out_path = output_path_for(en_path)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(dat.encrypted())
                click.echo(f'  wrote {out_path}')

        msg = f'{"Would patch" if dry_run else "Patched"} {total} item(s).'
        click.echo(msg)

    @grp.command('inject')
    @click.argument('json_file', type=click.Path(exists=True))
    @click.option('--dry-run', is_flag=True)
    def inject_cmd(json_file, dry_run):
        f"""Inject brand-new {name} items from a JSON array into free DAT slots.

        Each entry must include at minimum a ``name``. Numeric fields default to
        zero when absent. ``jobs_list`` / ``flags_decoded`` are accepted and
        converted to their decimal forms before writing.

        The first DAT in the {name} group is used. Free slots (empty name) are
        filled in order; an error is raised if there are not enough free slots.
        New records are built in the DAT's own format (legacy or retail).

        \b
        Example entry:
          {{"name": "My Sword", "level": 75, "jobs_list": ["WAR","PLD"],
            "flags_decoded": ["rare","ex"], "dmg": 50, "delay": 240}}

        \b
        Examples:
          xi ui items {name} inject new_items.json
          xi ui items {name} inject new_items.json --dry-run
        """
        entries = json.loads(Path(json_file).read_text(encoding='utf-8'))
        if not isinstance(entries, list):
            raise click.ClickException('JSON must be an array of item objects.')

        # Resolve decoded helpers into decimals before building records
        resolved = []
        for entry in entries:
            e = dict(entry)
            if 'jobs_list' in e and 'jobs' not in e:
                e['jobs'] = encode_jobs(e['jobs_list'])
            if 'flags_decoded' in e and 'flags' not in e:
                e['flags'] = encode_flags(e['flags_decoded'])
            resolved.append(e)

        # Find the first usable DAT in this group
        target = next(_dats_for_group(), None)
        if target is None:
            raise click.ClickException(f'No accessible DAT found for group {name!r}.')

        cat_name_dat, base_id, item_type_dat, en_rom, jp_rom, en_path = target
        dat = ItemDat.load(en_path)
        click.echo(f'Processing {cat_name_dat}: {en_path}  ({describe(dat.stride)})', err=True)

        # Collect free slot indices (empty name)
        free_slots = []
        for idx in range(dat.count):
            rec = dat.record(idx)
            text_off = _resolve_text_offset(rec, item_type_dat, dat.format)
            strings = _read_strings(rec, text_off) if text_off is not None else []
            if not strings or not strings[0] or strings[0] == '.':
                free_slots.append(idx)
            if len(free_slots) >= len(resolved):
                break

        if len(free_slots) < len(resolved):
            raise click.ClickException(
                f'Not enough free slots: need {len(resolved)}, found {len(free_slots)}.')

        injected = []
        for slot_idx, entry in zip(free_slots, resolved):
            item_id = base_id + slot_idx
            new_rec = build_record(entry, item_type_dat, dat.format)
            if dry_run:
                click.echo(f'  dry-run: would inject "{entry.get("name","")}" at id={item_id} (slot {slot_idx})')
            else:
                dat.set_record(slot_idx, new_rec)
            injected.append(item_id)

        if not dry_run:
            out_path = output_path_for(en_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(dat.encrypted())
            click.echo(f'Injected {len(injected)} item(s) at IDs {injected} -> {out_path}')
        else:
            click.echo(f'Dry-run: would inject {len(injected)} item(s) at IDs {injected}.')

    @grp.command('new')
    def new_cmd():
        f"""Interactive wizard to create a new {name} item."""
        raise click.ClickException(_STUB)

    return grp


group.add_command(_make_type_group(
    'general',    'General items (IDs 0-4095, 8704-10239, 30720-31743 and the small tables).', type_id=0,
    dats=['Items_1', 'Items_2', 'Items_3', 'Items_4', 'Items_5', 'Items_6', 'Items_7']))
group.add_command(_make_type_group(
    'consumable', 'Consumable items (IDs 4096-8191).', type_id=1,
    dats=['Consumable']))
group.add_command(_make_type_group(
    'puppet',     'Puppet items (IDs 8192-8703).', type_id=5,
    dats=['Puppet']))
group.add_command(_make_type_group(
    'armor',      'Armor (IDs 10240-28671, covers Armor_1 and Armor_2).', type_id=3,
    dats=['Armor_1', 'Armor_2']))
group.add_command(_make_type_group(
    'weapon',     'Weapons (IDs 16384-23039).', type_id=4,
    dats=['Weapons']))
group.add_command(_make_type_group(
    'monstrosity', 'The Monstrosity instinct table (IDs 29696-30719).',
    dats=['Monstrosity_1']))
_custom = _make_type_group('custom', 'Old name of monstrosity.', dats=['Monstrosity_1'])
_custom.hidden = True
group.add_command(_custom)


# ── Mount subgroup (delegates to xi.mount module) ───────────────────────────

@group.group('mount')
def mount_grp():
    """Custom mounts — model, name strings, key item text.

    Delegates to the same logic as ``xi mount``. Use this group as the
    canonical entry point under ``xi ui items``.
    """
    pass


@mount_grp.command('search')
@click.argument('query')
@click.option('--exact', is_flag=True, help='Exact name match.')
@click.option('--as-json', is_flag=True)
def mount_search(query, exact, as_json):
    """Search mounts by EN name."""
    from xi.mount import xi_core as M
    results = []
    for mid in range(M.MODEL_CAP + 1):
        rec = M.read_record(mid)
        name = rec.get('name_en') or ''
        match = (name.lower() == query.lower()) if exact else (query.lower() in name.lower())
        if match and name:
            results.append(rec)
    if as_json:
        click.echo(json.dumps(results, ensure_ascii=False, indent=2))
        return
    if not results:
        click.echo('No matches found.')
        return
    for r in results:
        click.echo(f"#{r['id']:>3}  {r['name_en']:<20}  ki={r['key_item']}  {r['model_dat'] or '(no model)'}")


@mount_grp.command('list')
@click.option('--all', 'show_all', is_flag=True, help='List ids 0-255 (default 0-63).')
@click.option('--occupied', is_flag=True)
@click.option('--free', is_flag=True)
@click.option('--as-json', is_flag=True)
def mount_list(show_all, occupied, free, as_json):
    """List all mounts."""
    from xi.mount import xi_list
    ctx = click.get_current_context()
    ctx.invoke(xi_list.cmd, show_all=show_all, occupied=occupied, free=free, as_json=as_json)


@mount_grp.command('export')
@click.argument('mount_id', type=int)
@click.option('--as-json', is_flag=True, default=True, is_eager=True)
def mount_export(mount_id, as_json):
    """Export a single mount's full record."""
    from xi.mount import xi_export
    ctx = click.get_current_context()
    ctx.invoke(xi_export.cmd, mount_id=mount_id, as_json=as_json)


@mount_grp.command('export-all')
@click.option('--output', '-o', default=None)
def mount_export_all(output):
    """Export all mount records to JSON."""
    from xi.mount import xi_core as M
    results = []
    for mid in range(M.MODEL_CAP + 1):
        rec = M.read_record(mid)
        if M.is_real_mount(rec):
            results.append(rec)
    out = json.dumps(results, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(out, encoding='utf-8')
        click.echo(f'Exported {len(results)} mounts -> {output}')
    else:
        click.echo(out)


@mount_grp.command('inject')
@click.pass_context
def mount_inject(ctx):
    """Inject a new mount (alias for ``xi mount inject``)."""
    from xi.mount import xi_inject
    click.echo('Use: xi mount inject --id N --dat ROM/... --name "Name"')
    click.echo('Or run: xi ui items mount inject --help after wiring full options.')
    raise click.ClickException('Run `xi mount inject` directly for the full option set.')


@mount_grp.command('import')
@click.pass_context
def mount_import(ctx):
    """Import/reskin a mount model (alias for ``xi mount import``)."""
    click.echo('Run `xi mount import` directly for the full option set.')
    raise click.ClickException('Run `xi mount import` directly.')

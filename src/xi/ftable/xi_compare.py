#!/usr/bin/env python3
"""`xi ftable compare <path_a> <path_b>` — diff the registered file_ids between
two ROM-structured DAT folders (e.g. `dats/builds` vs a deployed Ashita
`polplugins/DATs/<pack>` override folder).

For each folder it loads every FTABLE/VTABLE pair it can find (base FTABLE.DAT
plus ROMn/FTABLEn.DAT), enumerates every registered file_id (VTABLE byte != 0),
and reports:

  * file_ids registered in A but missing from B
  * file_ids registered in B but missing from A
  * file_ids in both but pointing at a DIFFERENT ROM/subdir/file DAT path

Entries are keyed by (rom table, file_id) so each ROM table is compared against
its counterpart.
"""

import os
import struct

import click

from xi.entity.xi_core import MODEL_FILE_OFFSET, MODEL_SAFE_START, MODEL_SAFE_END

# How many ROM indices to probe. Base ROM is 1; expansions/custom go above.
_MAX_ROM = 20


def _table_pairs(base_dir: str):
    """Yield (rom_idx, ftable_path, vtable_path) for every FTABLE/VTABLE pair
    present under base_dir. rom_idx 1 is the base FTABLE.DAT; n>=2 is
    ROMn/FTABLEn.DAT."""
    pairs = []
    ft = os.path.join(base_dir, 'FTABLE.DAT')
    vt = os.path.join(base_dir, 'VTABLE.DAT')
    if os.path.exists(ft) and os.path.exists(vt):
        pairs.append((1, ft, vt))
    for idx in range(2, _MAX_ROM + 1):
        ft = os.path.join(base_dir, f'ROM{idx}', f'FTABLE{idx}.DAT')
        vt = os.path.join(base_dir, f'ROM{idx}', f'VTABLE{idx}.DAT')
        if os.path.exists(ft) and os.path.exists(vt):
            pairs.append((idx, ft, vt))
    return pairs


def _registered(ft_path: str, vt_path: str) -> dict[int, str]:
    """Map file_id -> resolved 'ROM/subdir/file.DAT' path for every registered
    (VTABLE byte != 0) entry in one FTABLE/VTABLE pair."""
    with open(ft_path, 'rb') as f:
        fdata = f.read()
    with open(vt_path, 'rb') as f:
        vdata = f.read()
    out: dict[int, str] = {}
    n = min(len(fdata) // 2, len(vdata))
    for fid in range(n):
        vt_val = vdata[fid]
        if vt_val == 0:
            continue
        ft_val = struct.unpack_from('<H', fdata, fid * 2)[0]
        subdir = ft_val >> 7
        file_idx = ft_val & 0x7F
        rom = 'ROM' if vt_val == 1 else f'ROM{vt_val}'
        out[fid] = f'{rom}/{subdir}/{file_idx}.DAT'
    return out


def _scan(base_dir: str) -> dict[tuple[int, int], str]:
    """Return {(rom_idx, file_id): dat_path} for every registered entry across
    all FTABLE/VTABLE pairs under base_dir."""
    entries: dict[tuple[int, int], str] = {}
    for rom_idx, ft, vt in _table_pairs(base_dir):
        for fid, dat in _registered(ft, vt).items():
            entries[(rom_idx, fid)] = dat
    return entries


def _annotate(fid: int) -> str:
    """Append a '(modelid N)' hint for file_ids that fall in the custom entity
    band, so the numbers are recognisable."""
    lo = MODEL_FILE_OFFSET + MODEL_SAFE_START
    hi = MODEL_FILE_OFFSET + MODEL_SAFE_END
    if lo <= fid <= hi:
        return f'  (modelid {fid - MODEL_FILE_OFFSET:,})'
    return ''


@click.command('compare')
@click.argument('path_a', type=click.Path(exists=True, file_okay=False))
@click.argument('path_b', type=click.Path(exists=True, file_okay=False))
def cmd(path_a, path_b):
    """Diff the registered file_ids between two ROM-structured DAT folders.

    Loads every FTABLE/VTABLE pair under each folder, enumerates the registered
    file_ids, and reports what one side has that the other doesn't (plus entries
    that resolve to a different DAT path on each side).
    """
    a = _scan(path_a)
    b = _scan(path_b)

    if not a:
        raise click.ClickException(f'No FTABLE/VTABLE pairs found under: {path_a}')
    if not b:
        raise click.ClickException(f'No FTABLE/VTABLE pairs found under: {path_b}')

    a_keys = set(a)
    b_keys = set(b)
    only_a = sorted(a_keys - b_keys)
    only_b = sorted(b_keys - a_keys)
    differ = sorted(k for k in (a_keys & b_keys) if a[k] != b[k])

    def _tbl(rom_idx: int) -> str:
        return 'FTABLE' if rom_idx == 1 else f'FTABLE{rom_idx}'

    click.echo('=' * 68)
    click.echo('  ftable compare')
    click.echo('=' * 68)
    click.echo(f'  A: {os.path.abspath(path_a)}')
    click.echo(f'     {len(a):,} registered file_id(s) across '
               f'{len({k[0] for k in a})} table(s)')
    click.echo(f'  B: {os.path.abspath(path_b)}')
    click.echo(f'     {len(b):,} registered file_id(s) across '
               f'{len({k[0] for k in b})} table(s)')
    click.echo('=' * 68)

    # Per-table overview so a whole missing/unexpanded table stands out from
    # scattered per-file_id gaps.
    all_roms = sorted({k[0] for k in (a_keys | b_keys)})
    click.echo()
    click.echo('Per table:')
    click.echo(f'  {"table":<9}{"A":>10}{"B":>10}{"onlyA":>10}{"onlyB":>10}{"differ":>10}')
    for rom_idx in all_roms:
        a_n = sum(1 for k in a_keys if k[0] == rom_idx)
        b_n = sum(1 for k in b_keys if k[0] == rom_idx)
        oa = sum(1 for k in only_a if k[0] == rom_idx)
        ob = sum(1 for k in only_b if k[0] == rom_idx)
        df = sum(1 for k in differ if k[0] == rom_idx)
        flag = '  <- absent on B' if b_n == 0 else ('  <- absent on A' if a_n == 0 else '')
        click.echo(f'  {_tbl(rom_idx):<9}{a_n:>10,}{b_n:>10,}'
                   f'{oa:>10,}{ob:>10,}{df:>10,}{flag}')

    click.echo()
    click.echo(f'Missing from B (in A, not B): {len(only_a)}')
    for rom_idx, fid in only_a:
        click.echo(f'  * {_tbl(rom_idx):<9} file_id {fid:<8} -> {a[(rom_idx, fid)]}'
                   f'{_annotate(fid)}')

    click.echo()
    click.echo(f'Missing from A (in B, not A): {len(only_b)}')
    for rom_idx, fid in only_b:
        click.echo(f'  * {_tbl(rom_idx):<9} file_id {fid:<8} -> {b[(rom_idx, fid)]}'
                   f'{_annotate(fid)}')

    if differ:
        click.echo()
        click.echo(f'Different DAT path (registered in both): {len(differ)}')
        for rom_idx, fid in differ:
            click.echo(f'  * {_tbl(rom_idx):<9} file_id {fid:<8}'
                       f'{_annotate(fid)}')
            click.echo(f'      A -> {a[(rom_idx, fid)]}')
            click.echo(f'      B -> {b[(rom_idx, fid)]}')

    click.echo()
    if not only_a and not only_b and not differ:
        click.echo('Identical: both folders register the same file_ids -> same DATs.')
    else:
        click.echo(f'Summary: {len(only_a)} only in A, {len(only_b)} only in B, '
                   f'{len(differ)} differing.')

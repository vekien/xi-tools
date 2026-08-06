import struct
import click
from xi.ftable.xi_core import ftable_path, vtable_path, load_tables, patch_table
from xi.entity.xi_core import MODEL_FILE_OFFSET
from xi.xi_config import CUSTOM_ROM, CUSTOM_ROM_IDX


def _read_entry(rom_idx: int, file_id: int):
    """(ft_val, vt_val) for file_id in ROM{rom_idx}'s tables, or None if the
    table is missing or file_id is out of range."""
    result = load_tables(rom_idx)
    if result is None:
        return None
    fdata, vdata = result
    if file_id * 2 + 2 > len(fdata) or file_id >= len(vdata):
        return None
    ft_val = struct.unpack_from('<H', fdata, file_id * 2)[0]
    vt_val = vdata[file_id]
    return ft_val, vt_val


def _fmt_dat(ft_val: int, vt_val: int) -> str:
    subdir   = ft_val >> 7
    file_idx = ft_val & 0x7F
    return (f'ROM/{subdir}/{file_idx}.DAT' if vt_val == 1
            else f'ROM{vt_val}/{subdir}/{file_idx}.DAT')


def delete_entry(file_id: int, dry_run: bool = False):
    click.echo(f'\n  file_id : {file_id}')
    if file_id >= MODEL_FILE_OFFSET + 3500:
        modelid = file_id - MODEL_FILE_OFFSET
        click.echo(f'  modelid : {modelid}  (3500+ range)')
    else:
        click.echo(f'  modelid : (not in 3500+ range - raw file_id only)')

    # A custom registration can live in the base table (version byte -> ROM10)
    # and/or the ROM10 overlay. `xi ftable set` writes both; clear both.
    base_ft, base_vt = _read_entry(1, file_id) or (0, 0)
    over_ft, over_vt = _read_entry(CUSTOM_ROM_IDX, file_id) or (0, 0)

    # Protect retail: the version byte is the *target* ROM. If the base routes
    # this file_id anywhere other than the custom ROM, it's a stock game file.
    if base_vt not in (0, CUSTOM_ROM_IDX):
        click.echo(f'  Base     : 0x{base_ft:04X} v{base_vt}  -> {_fmt_dat(base_ft, base_vt)}')
        raise click.ClickException(
            f'file_id {file_id} routes to ROM{base_vt} (retail), not {CUSTOM_ROM}. '
            f'Refusing to clear a non-custom entry.')

    clear_base    = base_vt == CUSTOM_ROM_IDX
    clear_overlay = over_vt != 0

    if not clear_base and not clear_overlay:
        click.echo('  -> Not found / already zero. Nothing to do.')
        return

    if clear_base:
        click.echo(f'  Base     : 0x{base_ft:04X} v{base_vt}  -> {_fmt_dat(base_ft, base_vt)}')
    if clear_overlay:
        click.echo(f'  {CUSTOM_ROM}    : 0x{over_ft:04X} v{over_vt}  -> {_fmt_dat(over_ft, over_vt)}')

    if dry_run:
        tbls = []
        if clear_base:    tbls.append('FTABLE.DAT/VTABLE.DAT')
        if clear_overlay: tbls.append(f'FTABLE{CUSTOM_ROM_IDX}.DAT/VTABLE{CUSTOM_ROM_IDX}.DAT')
        click.echo(f'\n  [dry-run] Would zero file_id {file_id} in: {", ".join(tbls)}')
        return

    if clear_base:
        patch_table(ftable_path(1), vtable_path(1), file_id, 0, 0)
        click.echo(f'\n  Zeroed  : FTABLE.DAT + VTABLE.DAT  [{file_id}]')
    if clear_overlay:
        patch_table(ftable_path(CUSTOM_ROM_IDX), vtable_path(CUSTOM_ROM_IDX), file_id, 0, 0)
        click.echo(f'  Zeroed  : FTABLE{CUSTOM_ROM_IDX}.DAT + VTABLE{CUSTOM_ROM_IDX}.DAT  [{file_id}]')
    click.echo('  Done.')


@click.command('delete')
@click.option('--file-id', 'file_ids', type=int, multiple=True,
              help='One or more file_ids to delete')
@click.option('--modelid', 'modelids', type=int, multiple=True,
              help='One or more modelids (converted via +98239)')
@click.option('--dry-run', is_flag=True, help='Show what would be deleted without writing')
def cmd(file_ids, modelids, dry_run):
    """Zero one or more FTABLE entries from the custom ROM."""
    if not file_ids and not modelids:
        raise click.UsageError('Provide --file-id and/or --modelid')
    all_file_ids = list(file_ids) + [m + MODEL_FILE_OFFSET for m in modelids]
    click.echo()
    click.echo('=' * 58)
    click.echo(f'  FFXI FTABLE Entry Delete (base + {CUSTOM_ROM})')
    if dry_run:
        click.echo('  [DRY RUN]')
    click.echo('=' * 58)
    for fid in all_file_ids:
        delete_entry(fid, dry_run)
        click.echo()
    click.echo('=' * 58)

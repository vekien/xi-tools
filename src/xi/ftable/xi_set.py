import os
import click
from xi.ftable.xi_core import ftable_path, vtable_path, patch_table
from xi.entity.xi_core import MODEL_FILE_OFFSET
from xi.xi_config import CUSTOM_ROM_IDX, read_path_for, FFXI_DIR


def _table_label(rom_idx: int) -> str:
    return 'FTABLE.DAT' if rom_idx == 1 else f'ROM{rom_idx}/FTABLE{rom_idx}.DAT'


def _capacity(ft_path: str, file_id: int):
    """(fits, entry_count) for the on-disk FTABLE that backs ``file_id``."""
    p = read_path_for(ft_path)
    if not os.path.exists(p):
        return False, 0
    entries = os.path.getsize(p) // 2
    return file_id < entries, entries


@click.command('set')
@click.option('--file-id', type=int, default=None, help='Raw file_id to register')
@click.option('--modelid', type=int, default=None,
              help=f'Entity modelid (file_id = modelid + {MODEL_FILE_OFFSET})')
@click.option('--rom', type=int, default=CUSTOM_ROM_IDX, show_default=True,
              help='ROM version byte: 1 = base ROM/, N = ROM{N}/ . This is the VTABLE value.')
@click.option('--subdir', type=int, required=True, help='Folder number under the ROM (0-511)')
@click.option('--file', 'file_idx', type=int, required=True,
              help='File number within the folder (0-127)')
@click.option('--no-base', is_flag=True,
              help="Write only the ROM{N} overlay; leave the base FTABLE/VTABLE untouched")
@click.option('--dry-run', is_flag=True, help='Show the plan without writing')
def cmd(file_id, modelid, rom, subdir, file_idx, no_base, dry_run):
    """Register a file_id -> ROM{N}/<subdir>/<file>.DAT in the FTABLE/VTABLE.

    \b
    By default writes BOTH the base FTABLE.DAT/VTABLE.DAT (the in-memory master
    the client actually reads at file-open) AND the ROM{N}/FTABLE{N}/VTABLE{N}
    overlay, both with version byte N. Use --no-base for the overlay only.

    \b
    The tables only store a pointer - the target DAT must physically exist at
    ROM{N}/<subdir>/<file>.DAT or the client will fail to load this file_id.

    \b
    Example (custom mount slot 50):
      xi ftable set --file-id 102755 --rom 10 --subdir 10 --file 1
    """
    # ---- resolve file_id ---------------------------------------------------
    if file_id is None and modelid is None:
        raise click.UsageError('Provide --file-id or --modelid')
    if file_id is not None and modelid is not None:
        raise click.UsageError('Provide --file-id or --modelid, not both')
    if modelid is not None:
        file_id = modelid + MODEL_FILE_OFFSET

    # ---- validate field ranges --------------------------------------------
    if not (1 <= rom <= 255):
        raise click.UsageError(f'--rom must be 1-255 (got {rom})')
    if not (0 <= file_idx <= 127):
        raise click.UsageError(f'--file must be 0-127 (7-bit field), got {file_idx}')
    if not (0 <= subdir <= 511):
        raise click.UsageError(f'--subdir must be 0-511 (9-bit field), got {subdir}')

    ftval   = (subdir << 7) | file_idx
    rom_dir = 'ROM' if rom == 1 else f'ROM{rom}'
    dat_rel = f'{rom_dir}/{subdir}/{file_idx}.DAT'

    # rom==1 means the overlay IS the base table - never write it twice.
    write_base = (not no_base) and rom != 1

    # ---- capacity guard on every FTABLE we will touch ---------------------
    for idx in ({rom} | ({1} if write_base else set())):
        fits, entries = _capacity(ftable_path(idx), file_id)
        if not fits:
            raise click.ClickException(
                f'{_table_label(idx)} holds {entries:,} entries; file_id {file_id} is out of range. '
                f'Run "xi ftable expand" to grow the tables first.')

    # ---- warn on a dangling pointer ---------------------------------------
    dat_disk   = read_path_for(os.path.join(FFXI_DIR, rom_dir, str(subdir), f'{file_idx}.DAT'))
    dat_exists = os.path.exists(dat_disk)

    # ---- plan --------------------------------------------------------------
    click.echo()
    click.echo('=' * 60)
    click.echo('  FFXI FTABLE Set' + ('   [DRY RUN]' if dry_run else ''))
    click.echo('=' * 60)
    click.echo(f'  file_id      : {file_id}  (0x{file_id:05X})')
    if modelid is not None:
        click.echo(f'  modelid      : {modelid}  (+{MODEL_FILE_OFFSET})')
    click.echo(f'  -> DAT path  : {dat_rel}')
    click.echo(f'  FTABLE value : 0x{ftval:04X}  (subdir={subdir} << 7 | file={file_idx})')
    click.echo(f'  VTABLE value : {rom}')
    click.echo(f'  DAT on disk  : {"YES" if dat_exists else "NO  (!! pointer will dangle)"}')
    click.echo()
    click.echo(f'  Write overlay: {_table_label(rom)}')
    if write_base:
        click.echo(f'  Write base   : FTABLE.DAT + VTABLE.DAT  (master, version byte {rom})')
    elif no_base and rom != 1:
        click.echo(f'  Write base   : skipped (--no-base)')
    click.echo('=' * 60)

    if dry_run:
        click.echo('\n[DRY RUN] Nothing written.')
        return

    # ---- write -------------------------------------------------------------
    patch_table(ftable_path(rom), vtable_path(rom), file_id, ftval, rom)
    click.echo(f'\n[+] {_table_label(rom)} patched '
               f'(FTABLE @ 0x{file_id*2:05X}, VTABLE @ 0x{file_id:05X})')

    if write_base:
        patch_table(ftable_path(1), vtable_path(1), file_id, ftval, rom)
        click.echo(f'[+] FTABLE.DAT + VTABLE.DAT patched (master, version byte {rom})')

    if not dat_exists:
        click.echo()
        click.echo(f'[!] WARNING: {dat_rel} is missing on disk - the table now points at nothing.')
        click.echo(f'    Place your DAT there before launching the client.')

    click.echo('\n[*] Done.')

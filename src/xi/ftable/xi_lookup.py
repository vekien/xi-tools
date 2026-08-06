import os
import re
import struct
import click
from xi.xi_config import FFXI_DIR, read_path_for
from xi.ftable.xi_core import ftable_path
from xi.entity.xi_core import MODEL_FILE_OFFSET


def _find_vtable(ftable_path: str) -> str:
    directory   = os.path.dirname(ftable_path)
    filename    = os.path.basename(ftable_path)
    vtable_name = re.sub(r'(?i)^FTABLE', 'VTABLE', filename)
    return os.path.join(directory, vtable_name)


def lookup(ftable_file: str, file_id: int) -> dict:
    ftable_file = os.path.normpath(os.path.abspath(ftable_file))
    vtable_file = _find_vtable(ftable_file)
    if not os.path.exists(ftable_file):
        raise FileNotFoundError(f'FTABLE not found: {ftable_file}')
    if not os.path.exists(vtable_file):
        raise FileNotFoundError(f'VTABLE not found: {vtable_file}')
    ft_entries = os.path.getsize(ftable_file) // 2
    vt_entries = os.path.getsize(vtable_file)
    if file_id >= ft_entries:
        raise ValueError(f'file_id {file_id} out of range (FTABLE has {ft_entries} entries)')
    if file_id >= vt_entries:
        raise ValueError(f'file_id {file_id} out of range (VTABLE has {vt_entries} entries)')
    with open(ftable_file, 'rb') as f:
        f.seek(file_id * 2)
        (ftable_val,) = struct.unpack('<H', f.read(2))
    with open(vtable_file, 'rb') as f:
        f.seek(file_id)
        vtable_val = f.read(1)[0]
    subdir   = ftable_val >> 7
    file_idx = ftable_val & 0x7F
    rom_dir  = 'ROM' if vtable_val == 1 else f'ROM{vtable_val}'
    rel_path  = f'{rom_dir}/{subdir}/{file_idx}.DAT'
    full_path = os.path.join(FFXI_DIR, rom_dir, str(subdir), f'{file_idx}.DAT')
    return {
        'file_id':    file_id,
        'ftable_val': ftable_val,
        'vtable_val': vtable_val,
        'rom_dir':    rom_dir,
        'subdir':     subdir,
        'file_idx':   file_idx,
        'rel_path':   rel_path,
        'full_path':  full_path,
        'exists':     os.path.exists(full_path),
        'ft_entries': ft_entries,
        'vt_entries': vt_entries,
    }


@click.command('lookup')
@click.argument('ftable_file', required=False)
@click.option('--table', '-t', type=int, default=1, show_default=True,
              help='ROM table index: 1 = base FTABLE.DAT, N = ROM{N}/FTABLE{N}.DAT. '
                   'Ignored when an explicit FTABLE_FILE path is given.')
@click.option('--file-id', type=int, default=None, help='Raw file_id to look up')
@click.option('--modelid', type=int, default=None,
              help=f'Monster modelid (converted via file_id = modelid + {MODEL_FILE_OFFSET})')
def cmd(ftable_file, table, file_id, modelid):
    """Resolve a file_id or modelid against an FTABLE/VTABLE pair.

    \b
    With no path, looks up the base FTABLE.DAT; pass --table N for ROM{N}/FTABLE{N}.DAT.
    An explicit FTABLE_FILE path overrides --table.
    """
    if file_id is None and modelid is None:
        raise click.UsageError('Provide --file-id or --modelid')
    if file_id is not None and modelid is not None:
        raise click.UsageError('Provide --file-id or --modelid, not both')
    if modelid is not None:
        file_id = MODEL_FILE_OFFSET + modelid
    if ftable_file is None:
        ftable_file = read_path_for(ftable_path(table))
    try:
        r = lookup(ftable_file, file_id)
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e))
    click.echo()
    click.echo('=' * 56)
    click.echo('  FFXI FTABLE Lookup')
    click.echo('=' * 56)
    if modelid is not None:
        click.echo(f'  modelid      : {modelid}  (0x{modelid:04X})')
        click.echo(f'  file_id      : {file_id}  (= {MODEL_FILE_OFFSET} + {modelid})')
    else:
        click.echo(f'  file_id      : {file_id}')
    click.echo()
    click.echo(f'  FTABLE value : 0x{r["ftable_val"]:04X}')
    click.echo(f'  VTABLE value : {r["vtable_val"]}')
    click.echo()
    if r['vtable_val'] == 0:
        click.echo('  Result       : EMPTY SLOT (vtable=0, not registered)')
    else:
        click.echo(f'  ROM dir      : {r["rom_dir"]}')
        click.echo(f'  Subdir       : {r["subdir"]}')
        click.echo(f'  File index   : {r["file_idx"]}')
        click.echo()
        click.echo(f'  DAT path     : {r["rel_path"]}')
        click.echo(f'  Full path    : {r["full_path"]}')
        click.echo(f'  File exists  : {"YES" if r["exists"] else "NO"}')
        if r['exists']:
            with open(r['full_path'], 'rb') as f:
                hdr = f.read(4)
            size = os.path.getsize(r['full_path'])
            ascii_repr = ''.join(chr(b) if 32 <= b < 127 else '.' for b in hdr)
            click.echo(f'  Header bytes : {hdr.hex()}  "{ascii_repr}"')
            click.echo(f'  File size    : {size:,} bytes')
    click.echo()
    click.echo(f'  FTABLE size  : {r["ft_entries"]:,} entries')
    click.echo(f'  VTABLE size  : {r["vt_entries"]:,} entries')
    click.echo('=' * 56)

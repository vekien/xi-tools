import os
import struct
import click
from xi.xi_config import FFXI_DIR

RETAIL_MAX = 109701


def run_scan(ffxi_dir: str = FFXI_DIR, max_entries: int = RETAIL_MAX):
    ft_path = os.path.join(ffxi_dir, 'FTABLE.DAT')
    vt_path = os.path.join(ffxi_dir, 'VTABLE.DAT')
    with open(ft_path, 'rb') as f:
        ft = f.read()
    with open(vt_path, 'rb') as f:
        vt = f.read()

    click.echo(f'Scanning first {max_entries:,} entries of FTABLE\n')

    runs = []
    in_run = False
    run_start = None
    for fid in range(max_entries):
        occupied = (vt[fid] != 0) and (struct.unpack_from('<H', ft, fid * 2)[0] != 0)
        if occupied and not in_run:
            in_run = True
            run_start = fid
        elif not occupied and in_run:
            in_run = False
            runs.append((run_start, fid - 1))
    if in_run:
        runs.append((run_start, max_entries - 1))

    click.echo(f'Found {len(runs)} occupied runs\n')
    click.echo(f'{"file_id start":>14}  {"file_id end":>11}  {"count":>6}  {"sample DAT path"}')
    click.echo('-' * 70)
    for a, b in runs:
        count    = b - a + 1
        mid      = (a + b) // 2
        ft_val   = struct.unpack_from('<H', ft, mid * 2)[0]
        vt_val   = vt[mid]
        subdir   = ft_val >> 7
        file_idx = ft_val & 0x7F
        rom      = 'ROM' if vt_val == 1 else f'ROM{vt_val}'
        dat      = f'{rom}/{subdir}/{file_idx}.DAT'
        click.echo(f'  {a:>12,}  {b:>11,}  {count:>6,}  {dat}')


@click.command('range-scan')
@click.option('--max-entries', type=int, default=RETAIL_MAX, show_default=True,
              help='Number of FTABLE entries to scan')
def cmd(max_entries):
    """Scan FTABLE for occupied file_id blocks (retail layout analysis)."""
    run_scan(FFXI_DIR, max_entries)

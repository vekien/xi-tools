import click
from xi.ftable.xi_core import load_tables, resolve_dat
from xi.entity.xi_core import MODEL_FILE_OFFSET, MODEL_SAFE_START, MODEL_SAFE_END, modelid_blob


def next_free_and_occupied():
    tables = {}
    for idx in range(1, 11):
        result = load_tables(idx)
        if result:
            tables[idx] = result
    if not tables:
        raise click.ClickException('No FTABLE/VTABLE files found.')

    occupied  = []
    next_free = None
    for modelid in range(MODEL_SAFE_START, MODEL_SAFE_END + 1):
        file_id = modelid + MODEL_FILE_OFFSET
        found = False
        for rom_idx, (fdata, vdata) in sorted(tables.items()):
            dat, vt_val = resolve_dat(fdata, vdata, file_id)
            if dat:
                occupied.append((modelid, file_id, vt_val, dat))
                found = True
                break
        if not found and next_free is None:
            next_free = modelid
    return next_free, occupied


@click.command('recommend')
def cmd():
    """Show the next free custom entity model ID slot in ROM10."""
    next_free, occupied = next_free_and_occupied()

    click.echo()
    click.echo('=' * 62)
    click.echo('  FFXI Custom Entity Model - Next Available Slot')
    click.echo('=' * 62)

    if next_free is None:
        raise click.ClickException('Custom range is full! Expand FTABLE further.')

    next_file_id = next_free + MODEL_FILE_OFFSET
    click.echo(f'\n  Next free model ID : {next_free}')
    click.echo(f'  File ID            : {next_file_id}  (modelid + {MODEL_FILE_OFFSET})')
    click.echo(f'  mob_pools blob     : {modelid_blob(next_free)}')
    click.echo(f'\n  Custom range       : modelid {MODEL_SAFE_START} - {MODEL_SAFE_END}')
    click.echo(f'  Slots used         : {len(occupied)}')
    click.echo(f'  Slots remaining    : {MODEL_SAFE_END - MODEL_SAFE_START + 1 - len(occupied)}')

    if occupied:
        click.echo(f'\n  Registered custom slots:')
        click.echo(f'  {"-"*8}  {"-"*8}  {"-"*5}  {"-"*30}')
        click.echo(f'  {"modelid":>8}  {"file_id":>8}  {"rom":>5}  dat')
        click.echo(f'  {"-"*8}  {"-"*8}  {"-"*5}  {"-"*30}')
        for m, fid, vt_val, dat in occupied:
            click.echo(f'  {m:>8}  {fid:>8}  ROM{vt_val:>2}  {dat}')
    else:
        click.echo('\n  No custom slots registered yet.')

    click.echo()
    click.echo(f'  To inject:  xi entity inject <your.DAT> --modelid {next_free}')
    click.echo()
    click.echo('=' * 62)

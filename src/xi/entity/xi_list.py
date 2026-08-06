import json
import os
import click
from xi.xi_config import XI_TOOLS_DIR
from xi.ftable.xi_core import load_all_tables, scan_file_ids
from xi.entity.xi_core import RANGES, MAX_3500_MODELID, modelid_blob


def get_all_entries(tables: dict | None = None, quiet: bool = False):
    if tables is None:
        tables = load_all_tables()
        if not quiet:
            for rom_idx, (fdata, _) in sorted(tables.items()):
                click.echo(f'Loaded ROM{rom_idx} tables  ({len(fdata)//2:,} entries)')
            click.echo()

    # Build file_id -> modelid map from the 4 monster ranges
    fid_to_mid = {}
    for (mid_start, mid_end, offset) in RANGES:
        end = mid_end if mid_end is not None else MAX_3500_MODELID
        for modelid in range(mid_start, end + 1):
            fid_to_mid[modelid + offset] = modelid

    entries = []
    for e in scan_file_ids(fid_to_mid.keys(), tables):
        modelid = fid_to_mid[e['file_id']]
        entries.append({
            'file_id':       e['file_id'],
            'model_id':      modelid,
            'model_id_text': modelid_blob(modelid),
            'rom':           e['rom'],
            'dat':           e['dat'],
        })
    return entries


@click.command('list')
@click.option('--json', 'as_json', is_flag=True, help='Write exports/entity_models.json')
@click.option('--csv',  'as_csv',  is_flag=True, help='Write exports/entity_models.csv')
def cmd(as_json, as_csv):
    """List all registered entity model entries (monsters, NPCs, objects) across all ROM tables."""
    entries = get_all_entries()

    click.echo(f'Found {len(entries):,} entity model entries (monsters, NPCs, objects)\n')
    click.echo(f'{"file_id":>8}  {"model_id":>8}  {"rom":>5}  dat')
    click.echo('-' * 70)
    for e in entries:
        click.echo(f'  {e["file_id"]:>8}  {e["model_id"]:>8}  ROM{e["rom"]:>2}  {e["dat"]}')

    if as_json or as_csv:
        out_dir = os.path.join(XI_TOOLS_DIR, 'exports')
        os.makedirs(out_dir, exist_ok=True)
        if as_json:
            path = os.path.join(out_dir, 'entity_models.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(entries, f, indent=2)
            click.echo(f'\nJSON written: {path}')
        if as_csv:
            path = os.path.join(out_dir, 'entity_models.csv')
            with open(path, 'w', encoding='utf-8') as f:
                f.write('file_id,model_id,model_id_text,rom,dat\n')
                for e in entries:
                    f.write(f'{e["file_id"]},{e["model_id"]},{e["model_id_text"]},{e["rom"]},{e["dat"]}\n')
            click.echo(f'CSV written:  {path}')

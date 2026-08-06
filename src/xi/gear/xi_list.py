import json
import os
import click
from xi.xi_config import XI_TOOLS_DIR
from xi.ftable.xi_core import load_all_tables, scan_file_ids
from xi.gear.xi_core import RACE_TABLES, SLOTS, parse_race_table, slot_file_ids, match_race


def get_all_entries(tables: dict | None = None, quiet: bool = False) -> dict:
    """
    Returns {race: [{'slot', 'model_id', 'file_id', 'rom', 'dat'}, ...]}
    Loads FTABLE once and resolves all file_ids in a single scan pass.
    """
    if tables is None:
        tables = load_all_tables()
        if not quiet:
            for rom_idx, (fdata, _) in sorted(tables.items()):
                click.echo(f'Loaded ROM{rom_idx} tables  ({len(fdata)//2:,} entries)')
            click.echo()

    # Expand all races into (race, slot, model_id, file_id) tuples
    all_tuples = []
    all_fids   = set()
    for race, raw in RACE_TABLES.items():
        race_table = parse_race_table(raw)
        for slot in SLOTS:
            for model_id, file_id in slot_file_ids(race_table[slot]):
                all_tuples.append((race, slot, model_id, file_id))
                all_fids.add(file_id)

    # Resolve all file_ids in one pass
    fid_map = {}
    for e in scan_file_ids(sorted(all_fids), tables):
        fid_map[e['file_id']] = {'rom': e['rom'], 'dat': e['dat']}

    # Assemble per-race lists preserving original order
    entries: dict[str, list] = {}
    for race, slot, model_id, file_id in all_tuples:
        resolved = fid_map.get(file_id)
        if not resolved:
            continue
        entries.setdefault(race, []).append({
            'slot':     slot,
            'model_id': model_id,
            'file_id':  file_id,
            'rom':      resolved['rom'],
            'dat':      resolved['dat'],
        })

    return entries


WEAPON_SLOTS = ('main', 'sub', 'ranged')


def _list_detail(race: str, slot: str | None, as_json: bool, as_csv: bool):
    """Detailed file_id / model_id / dat table for one race (and optional slot)."""
    race_key = match_race(race)
    if race_key is None:
        raise click.ClickException(f"Unknown race '{race}'. Valid: {', '.join(RACE_TABLES)}")
    if slot is not None:
        slot = slot.lower()
        if slot not in SLOTS:
            raise click.ClickException(f"Unknown slot '{slot}'. Valid: {', '.join(SLOTS)}")
    slots = [slot] if slot else SLOTS

    race_table = parse_race_table(RACE_TABLES[race_key])
    rows = []
    for s in slots:
        for model_id, file_id in slot_file_ids(race_table[s]):
            rows.append({'slot': s, 'model_id': model_id, 'file_id': file_id})

    tables = load_all_tables()
    datmap = {e['file_id']: e['dat']
              for e in scan_file_ids(sorted({r['file_id'] for r in rows}), tables)}
    for r in rows:
        r['dat'] = datmap.get(r['file_id'])

    if as_json:
        click.echo(json.dumps([{'race': race_key, **r} for r in rows], indent=2))
        return
    if as_csv:
        click.echo('race,slot,file_id,model_id,dat')
        for r in rows:
            click.echo(f"{race_key},{r['slot']},{r['file_id']},{r['model_id']},{r['dat'] or ''}")
        return

    title = f"{race_key} / {slot}" if slot else race_key
    click.echo(f"{title}  -  {len(rows)} models\n")
    show_slot = slot is None
    if show_slot:
        header = f"{'slot':<7}{'file_id':>9}{'model_id':>10}  dat"
    else:
        header = f"{'file_id':>9}{'model_id':>10}  dat"
    click.echo(header)
    click.echo('-' * max(len(header), 48))
    for r in rows:
        dat = r['dat'] or '(unregistered)'
        if show_slot:
            click.echo(f"{r['slot']:<7}{r['file_id']:>9,}{r['model_id']:>10,}  {dat}")
        else:
            click.echo(f"{r['file_id']:>9,}{r['model_id']:>10,}  {dat}")


@click.command('list')
@click.argument('race', required=False, metavar='[RACE]')
@click.argument('slot', required=False, metavar='[SLOT]')
@click.option('--json',    'as_json',   is_flag=True,
              help='Output JSON. (summary mode: writes files; RACE mode: prints JSON)')
@click.option('--csv',     'as_csv',    is_flag=True,
              help='Output CSV. (summary mode: writes files; RACE mode: prints CSV)')
@click.option('--slots',   'slot_filter', multiple=True,
              type=click.Choice(SLOTS + list(WEAPON_SLOTS), case_sensitive=False),
              help='(summary mode) Filter to specific slots (repeatable).')
@click.option('--weapons', 'weapons_only', is_flag=True,
              help='(summary mode) Shorthand for --slots main --slots sub --slots ranged')
def cmd(race, slot, as_json, as_csv, slot_filter, weapons_only):
    """List gear model entries.

    \b
    No args        per-race summary across all slots.
    RACE [SLOT]    detailed file_id / model_id / dat table for that race
                   (optionally narrowed to one slot).

    \b
    Examples:
      xi gear list
      xi gear list HumeFemale
      xi gear list HumeFemale body
      xi gear list HumeFemale body --csv
    """
    if race:
        _list_detail(race, slot, as_json, as_csv)
        return

    entries = get_all_entries()

    # Apply slot filter
    active_slots = set(slot_filter) or (set(WEAPON_SLOTS) if weapons_only else None)
    if active_slots:
        entries = {
            race: [e for e in items if e['slot'] in active_slots]
            for race, items in entries.items()
        }

    total = sum(len(v) for v in entries.values())
    slot_desc = f'  (slots: {", ".join(sorted(active_slots))})' if active_slots else ''
    click.echo(f'Found {total:,} gear model entries across {len(entries)} races{slot_desc}\n')

    for race, items in entries.items():
        click.echo(f'  {race:<16}  {len(items):>5,} entries')
    click.echo()

    if as_json or as_csv:
        suffix = '_weapons' if weapons_only else ('_' + '_'.join(sorted(active_slots)) if active_slots else '')
        out_dir = os.path.join(XI_TOOLS_DIR, 'exports', 'gear')
        os.makedirs(out_dir, exist_ok=True)

        for race, items in entries.items():
            if as_json:
                path = os.path.join(out_dir, f'gear_{race}{suffix}.json')
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(items, f, indent=2)
                click.echo(f'JSON written: {path}')

            if as_csv:
                path = os.path.join(out_dir, f'gear_{race}{suffix}.csv')
                with open(path, 'w', encoding='utf-8') as f:
                    f.write('slot,model_id,file_id,rom,dat\n')
                    for e in items:
                        f.write(f'{e["slot"]},{e["model_id"]},{e["file_id"]},{e["rom"]},{e["dat"]}\n')
                click.echo(f'CSV written:  {path}')

        # combined dump across all races (mirrors entity_models.json)
        flat = [dict(race=race, **e) for race, items in entries.items() for e in items]
        if as_json:
            path = os.path.join(out_dir, f'gear_models{suffix}.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(flat, f, indent=2)
            click.echo(f'JSON written: {path}')
        if as_csv:
            path = os.path.join(out_dir, f'gear_models{suffix}.csv')
            with open(path, 'w', encoding='utf-8') as f:
                f.write('race,slot,model_id,file_id,rom,dat\n')
                for e in flat:
                    f.write(f'{e["race"]},{e["slot"]},{e["model_id"]},{e["file_id"]},{e["rom"]},{e["dat"]}\n')
            click.echo(f'CSV written:  {path}')

"""
xi ui spells — spell and command (job ability / weapon skill) menu data.

Names come from the d_msg tables (ROM/181/73 spells, /72 commands; JP /69, /68)
and the numeric metadata from the ``mgc_`` / ``comm`` records of ROM/118/114.DAT,
decoded with ``xi.menu.xi_menu_table`` (record layout documented there and in
docs/dats/ROM_118_114.md). ``import`` writes edited fields back into the records.

New spells / commands are added through ``xi dats`` (``--type spell`` /
``--type command``; docs/menu/records.md) — this group only reads and edits what
is there.
"""

import json
from pathlib import Path

import click

from xi.menu import xi_menu_table as MT
from xi.xi_config import FFXI_DIR


def _root() -> Path:
    return Path(FFXI_DIR)


def _names(kind: str, lang: str) -> list:
    click.echo(f'Processing {MT.KINDS[kind].names[lang]}', err=True)
    return MT.read_names(kind, _root(), lang)


def _iter_records(kind: str, lang: str = 'en', named_only: bool = True):
    """One dict per record: id, name (from the string table) and the decoded fields."""
    names = _names(kind, lang)
    click.echo(f'Processing {MT.MENU_DAT}', err=True)
    try:
        menu = MT.load_menu(_root())
    except (OSError, MT.MenuError) as e:
        raise click.ClickException(f'{MT.MENU_DAT}: {e}')
    for idx, rec in enumerate(menu.records(kind)):
        name = names[idx] if idx < len(names) else ''
        if named_only and name in ('', '.'):
            continue
        if MT.is_empty(rec):
            continue
        row = {'id': idx, 'name': name}
        row.update(MT.read_fields(kind, rec))
        yield row


# ── commands ──────────────────────────────────────────────────────────────────

@click.group('spells')
def group():
    """Spell and command (job ability / weapon skill) menu data.

    Names come from the d_msg string DATs; MP, cast/recast, element, skill and
    per-job levels from the mgc_ / comm records of ROM/118/114.DAT. To ADD a
    spell or command use `xi dats prepare <definition> --type spell|command`.
    """
    pass


@group.command('search')
@click.argument('query')
@click.option('--exact', is_flag=True, help='Exact name match.')
@click.option('--abilities', is_flag=True, help='Search commands (job abilities / weapon skills) instead of spells.')
@click.option('--lang', default='en', show_default=True, type=click.Choice(['en', 'jp']))
@click.option('--as-json', is_flag=True)
def search_cmd(query, exact, abilities, lang, as_json):
    """Search for a spell or command by name.

    \b
    Examples:
      xi ui spells search "Cure"
      xi ui spells search "Mighty Strikes" --abilities
      xi ui spells search "Fire" --exact
    """
    kind = 'command' if abilities else 'spell'
    results = []
    for entry in _iter_records(kind, lang):
        name = entry['name']
        match = (name.lower() == query.lower()) if exact else (query.lower() in name.lower())
        if match:
            results.append(entry)
    if as_json:
        click.echo(json.dumps(results, ensure_ascii=False, indent=2))
        return
    if not results:
        click.echo('No matches found.')
        return
    for e in results:
        extra = ''
        if kind == 'spell':
            lv = ', '.join(f'{j} {l}' for j, l in e['levels'].items())
            extra = f"  MP {e['mp']:>4}  cast {e['cast'] / 4:g}s  recast {e['recast'] / 4:g}s  {lv}"
        else:
            extra = f"  type {e['type']}  lvl {e['level']}"
        click.echo(f"#{e['id']:>4}  {e['name']:<28}{extra}")


@group.command('export')
@click.option('--output', '-o', default=None, help='Output JSON file path (default: stdout).')
@click.option('--abilities', is_flag=True, help='Export commands instead of spells.')
@click.option('--lang', default='en', show_default=True, type=click.Choice(['en', 'jp']))
@click.option('--all', 'everything', is_flag=True, help='Include unnamed records too.')
def export_cmd(output, abilities, lang, everything):
    """Export spell (or command) names and decoded record fields to JSON.

    \b
    Examples:
      xi ui spells export -o spells.json
      xi ui spells export --abilities -o abilities.json
      xi ui spells export --lang jp
    """
    kind = 'command' if abilities else 'spell'
    results = list(_iter_records(kind, lang, named_only=not everything))
    out = json.dumps(results, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(out, encoding='utf-8')
        click.echo(f'Exported {len(results)} entries -> {output}')
    else:
        click.echo(out)


@group.command('import')
@click.argument('json_file', type=click.Path(exists=True))
@click.option('--abilities', is_flag=True, help='The file holds command records instead of spells.')
@click.option('--dry-run', is_flag=True)
def import_cmd(json_file, abilities, dry_run):
    """Write edited record fields from an `export` JSON back into ROM/118/114.DAT.

    Each entry needs an `id`; every other key that names a record field
    (spells: mp, cast, recast, element, skill, targets, icon, icon2, requirements, levels;
    commands: type, icon, charges, targets, tp, level, range, radius …) is
    written, the rest of the record is kept. The first write backs the DAT up
    to 114.DAT.base.

    \b
    Examples:
      xi ui spells import edits.json
      xi ui spells import edits.json --dry-run
    """
    kind = 'command' if abilities else 'spell'
    fields = set(MT.KINDS[kind].fields) - {'id', 'menu_index'}
    if kind == 'spell':
        fields.add('levels')
    try:
        menu = MT.load_menu(_root())
    except (OSError, MT.MenuError) as e:
        raise click.ClickException(f'{MT.MENU_DAT}: {e}')
    recs = menu.records(kind)
    entries = json.loads(Path(json_file).read_text(encoding='utf-8'))
    changed = 0
    for entry in entries:
        idx = entry.get('id')
        if not isinstance(idx, int) or idx >= len(recs):
            continue
        edits = {k: v for k, v in entry.items() if k in fields}
        if not edits:
            continue
        try:
            menu.set_record(kind, idx, MT.write_fields(kind, recs[idx], edits))
        except MT.MenuError as e:
            raise click.ClickException(f'record {idx}: {e}')
        changed += 1
    out = MT.save_menu(_root(), menu, dry_run=dry_run)
    if dry_run:
        click.echo(f'Dry run: would update {changed} {kind} records in {out}')
        return
    click.echo(f'Updated {changed} {kind} records -> {out}')

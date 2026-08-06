"""``xi ui strings`` — search and export FFXI d_msg string-table DATs.

Covers all DMSG categories: spell names, quest/mission names, titles, key items,
job names, mount names, status effects, and more (~40 categories total).

The d_msg format is a fixed-stride table of indexed string blocks. Each block
holds one or more sub-strings encoded in cp932 (Shift-JIS). Some tables are
XOR-encrypted with a one-byte bitmask (e.g. key items use 0xFF).
"""

import json
import sys
from pathlib import Path

import click

from xi.common import xi_dmsg as D
from xi.xi_config import read_path_for, FFXI_DIR, output_path_for

# ── Category registry ─────────────────────────────────────────────────────────
# Maps category name → (rom_path_en, rom_path_jp, bitmask, text_sub_index)
# text_sub_index: which sub-string within each block holds the display name.
# Most categories use sub[0]; key items use sub[4] (name), sub[5] (plural), sub[6] (desc).
# bitmask: 0 for plain, 0xFF for XOR-encrypted.

_CATEGORIES = {
    'Job_Names':                     ('ROM/165/86.DAT', 'ROM/165/86.DAT', 0x00, 0),
    'Help_Desk':                     ('ROM/165/72.DAT', 'ROM/165/71.DAT', 0x00, 0),
    'Search_Description':            ('ROM/165/75.DAT', 'ROM/165/74.DAT', 0x00, 0),
    'POL_Messages':                  ('ROM/165/70.DAT', 'ROM/165/70.DAT', 0x00, 0),
    'Server_Names':                  ('ROM/333/34.DAT', 'ROM/333/33.DAT', 0x00, 0),
    'Heading_Names':                 ('ROM/165/81.DAT', 'ROM/165/67.DAT', 0x00, 0),
    'Equipment_Slot_Names':          ('ROM/175/33.DAT', 'ROM/175/32.DAT', 0x00, 0),
    'Blue_Mage_Spell_Help_Text':     ('ROM/166/116.DAT','ROM/166/115.DAT',0x00, 0),
    'Augment_Attributes':            ('ROM/220/58.DAT', 'ROM/220/57.DAT', 0x00, 0),
    'Menu_Merit_Points':             ('ROM/169/75.DAT', 'ROM/169/74.DAT', 0x00, 0),
    'Menu_Job_Points':               ('ROM/314/62.DAT', 'ROM/314/61.DAT', 0x00, 0),
    'Menu_Job_Point_Gifts':          ('ROM/324/59.DAT', 'ROM/324/58.DAT', 0x00, 0),
    'Soulplate_Attributes':          ('ROM/187/70.DAT', 'ROM/187/67.DAT', 0x00, 0),
    'Trust_Messages':                ('ROM/311/74.DAT', 'ROM/311/73.DAT', 0x00, 0),
    'Emote_Help_Text':               ('ROM/327/124.DAT','ROM/327/123.DAT',0x00, 0),
    'Chat_Window_Command_Help_Text': ('ROM/173/89.DAT', 'ROM/173/88.DAT', 0x00, 0),
    'Monster_Family_Names':          ('ROM/188/38.DAT', 'ROM/188/37.DAT', 0x00, 0),
    'Moblin_Maze_Mongers_Rune_Help': ('ROM/219/86.DAT', 'ROM/219/85.DAT', 0x00, 0),
    'Titles':                        ('ROM/180/78.DAT', 'ROM/180/77.DAT', 0x00, 0),
    'Key_Items':                     ('ROM/175/35.DAT', 'ROM/175/34.DAT', 0xFF, 4),
    'Status_Names_with_Adjectives':  ('ROM/180/102.DAT','ROM/180/101.DAT',0x00, 0),
    'Spell_Names':                   ('ROM/181/73.DAT', 'ROM/181/69.DAT', 0x00, 0),
    'Spell_Help_Text':               ('ROM/181/75.DAT', 'ROM/181/71.DAT', 0x00, 0),
    'Ability_Names':                 ('ROM/181/72.DAT', 'ROM/181/68.DAT', 0x00, 0),
    'Ability_Help_Text':             ('ROM/181/74.DAT', 'ROM/181/70.DAT', 0x00, 0),
    'Mount_Names':                   ('ROM/351/84.DAT', 'ROM/351/82.DAT', 0x00, 0),
    'Mount_Help_Text':               ('ROM/351/85.DAT', 'ROM/351/83.DAT', 0x00, 0),
    # Quests
    'Quests_SandOria':               ('ROM/176/60.DAT', 'ROM/176/46.DAT', 0x00, 0),
    'Quests_Bastok':                 ('ROM/176/61.DAT', 'ROM/176/47.DAT', 0x00, 0),
    'Quests_Windurst':               ('ROM/176/62.DAT', 'ROM/176/48.DAT', 0x00, 0),
    'Quests_Jeuno':                  ('ROM/176/63.DAT', 'ROM/176/49.DAT', 0x00, 0),
    'Quests_Other_Areas':            ('ROM/176/64.DAT', 'ROM/176/50.DAT', 0x00, 0),
    'Quests_Treasures_of_Aht_Urhgan':('ROM/176/66.DAT','ROM/176/52.DAT', 0x00, 0),
    'Quests_Wings_of_the_Goddess':   ('ROM/196/6.DAT',  'ROM/196/3.DAT',  0x00, 0),
    'Quests_Abyssea':                ('ROM/242/64.DAT', 'ROM/242/63.DAT', 0x00, 0),
    'Quests_Assault':                ('ROM/176/72.DAT', 'ROM/176/58.DAT', 0x00, 0),
    'Quests_Campaign_Ops':           ('ROM/196/8.DAT',  'ROM/196/5.DAT',  0x00, 0),
    'Quests_Seekers_of_Adoulin':     ('ROM/293/70.DAT', 'ROM/293/67.DAT', 0x00, 0),
    'Quests_Coalition_Assignments':  ('ROM/293/71.DAT', 'ROM/293/68.DAT', 0x00, 0),
    # Missions
    'Missions_SandOria':             ('ROM/176/67.DAT', 'ROM/176/53.DAT', 0x00, 0),
    'Missions_Bastok':               ('ROM/176/68.DAT', 'ROM/176/54.DAT', 0x00, 0),
    'Missions_Windurst':             ('ROM/176/69.DAT', 'ROM/176/55.DAT', 0x00, 0),
    'Missions_Rise_of_the_Zilart':   ('ROM/176/70.DAT', 'ROM/176/56.DAT', 0x00, 0),
    'Missions_Chains_of_Promathia':  ('ROM/176/71.DAT', 'ROM/176/57.DAT', 0x00, 0),
    'Missions_Treasures_of_Aht_Urhgan':('ROM/176/73.DAT','ROM/176/59.DAT',0x00, 0),
    'Missions_Wings_of_the_Goddess': ('ROM/196/7.DAT',  'ROM/196/4.DAT',  0x00, 0),
    'Missions_A_Crystalline_Prophecy':('ROM/222/18.DAT','ROM/222/17.DAT', 0x00, 0),
    'Missions_A_Moogle_Kupo_dEtat':  ('ROM/223/12.DAT', 'ROM/223/10.DAT', 0x00, 0),
    'Missions_A_Shantotto_Ascension':('ROM/223/13.DAT', 'ROM/223/11.DAT', 0x00, 0),
    'Missions_Seekers_of_Adoulin':   ('ROM/293/69.DAT', 'ROM/293/66.DAT', 0x00, 0),
    'Missions_Rhapsodies_of_Vanadiel':('ROM/333/4.DAT', 'ROM/333/3.DAT',  0x00, 0),
}

CATEGORY_NAMES = sorted(_CATEGORIES.keys())


def _rom_to_disk(rom_path: str) -> Path:
    """Resolve a ROM-relative path (e.g. 'ROM/181/73.DAT') to a full disk path,
    preferring the output-dir mirror when it exists."""
    full = Path(FFXI_DIR) / Path(rom_path.replace('/', '\\'))
    out = output_path_for(full)
    return out if out.exists() else full


def _load(category: str, lang: str) -> tuple:
    """Returns (DmsgTable, bitmask, text_sub_index) for the given category+lang."""
    if category not in _CATEGORIES:
        raise click.ClickException(
            f'Unknown category {category!r}. Run `xi ui strings list` to see all.')
    en_path, jp_path, bitmask, sub_idx = _CATEGORIES[category]
    rom_path = en_path if lang == 'en' else jp_path
    disk_path = _rom_to_disk(rom_path)
    if not disk_path.exists():
        raise click.ClickException(f'DAT not found: {disk_path}')
    data = disk_path.read_bytes()
    try:
        table = D.parse(data, bitmask)
    except D.DmsgError as e:
        raise click.ClickException(f'Failed to parse {rom_path}: {e}')
    return table, bitmask, sub_idx


def _iter_strings(table: D.DmsgTable, sub_idx: int):
    """Yield (index, text) for every non-empty block in the table."""
    for i, block in enumerate(table.blocks):
        try:
            text = D.get_text(block, sub_idx)
        except Exception:
            continue
        if text:
            yield i, text


# ── Commands ──────────────────────────────────────────────────────────────────

@click.group('strings')
def group():
    """Search and export FFXI d_msg string tables (spell names, quest names, titles, etc.)."""
    pass


@group.command('list')
def list_cmd():
    """List all known d_msg string-table categories."""
    click.echo(f'{"Category":<45}  {"EN DAT":<22}  JP DAT')
    click.echo('-' * 95)
    for cat in CATEGORY_NAMES:
        en, jp, mask, _ = _CATEGORIES[cat]
        flag = ' [XOR]' if mask else ''
        click.echo(f'{cat:<45}  {en:<22}  {jp}{flag}')


@group.command('search')
@click.argument('query')
@click.option('--category', '-c', default=None,
              help='Limit search to one category (default: all).')
@click.option('--lang', default='en', show_default=True,
              type=click.Choice(['en', 'jp']), help='Language variant to search.')
@click.option('--exact', is_flag=True, help='Exact match instead of substring.')
@click.option('--json', 'as_json', is_flag=True, help='Output as JSON.')
def search_cmd(query, category, lang, exact, as_json):
    """Search for QUERY across all (or one) string-table categories.

    \b
    Examples:
      xi ui strings search "Cure"
      xi ui strings search "Sandy" --category Quests_SandOria
      xi ui strings search "Fire" --category Spell_Names --exact
    """
    cats = [category] if category else CATEGORY_NAMES
    results = []
    for cat in cats:
        try:
            table, _, sub_idx = _load(cat, lang)
        except (click.ClickException, Exception):
            continue
        for idx, text in _iter_strings(table, sub_idx):
            match = (text.lower() == query.lower()) if exact else (query.lower() in text.lower())
            if match:
                results.append({'category': cat, 'id': idx, 'text': text})

    if as_json:
        click.echo(json.dumps(results, ensure_ascii=False, indent=2))
        return

    if not results:
        click.echo('No matches found.')
        return

    for r in results:
        click.echo(f'[{r["category"]}] #{r["id"]:>5}  {r["text"]}')


@group.command('import')
@click.argument('category', metavar='CATEGORY',
                type=click.Choice(CATEGORY_NAMES, case_sensitive=False))
@click.argument('json_file', type=click.Path(exists=True))
@click.option('--lang', default='en', show_default=True,
              type=click.Choice(['en', 'jp']), help='Language variant to edit.')
@click.option('--dry-run', is_flag=True, help='Show changes without writing.')
def import_cmd(category, json_file, lang, dry_run):
    """Import edited string entries from JSON back into the d_msg DAT.

    The JSON must be an array of objects with ``id`` and ``text`` fields,
    as produced by ``xi ui strings export``. Only entries present in the
    JSON are updated; all other blocks are left untouched.

    \b
    Examples:
      xi ui strings import Spell_Names edits.json
      xi ui strings import Key_Items ki_edits.json --lang en --dry-run
    """
    import json as _json
    from xi.xi_config import output_path_for

    table, bitmask, sub_idx = _load(category, lang)

    entries = _json.loads(Path(json_file).read_text(encoding='utf-8'))
    if not isinstance(entries, list):
        raise click.ClickException('JSON must be a list of {id, text} objects.')

    changed = 0
    for entry in entries:
        idx = entry.get('id')
        text = entry.get('text', '')
        if idx is None or not isinstance(idx, int):
            continue
        if idx >= len(table.blocks):
            click.echo(f'  skip #{idx}: out of range (table has {len(table.blocks)} entries)', err=True)
            continue
        try:
            new_block = D.set_text(table.blocks[idx], sub_idx, text)
            if dry_run:
                old = D.get_text(table.blocks[idx], sub_idx)
                if old != text:
                    click.echo(f'  #{idx}: {old!r} -> {text!r}')
            else:
                table.blocks[idx] = bytearray(new_block)
            changed += 1
        except D.DmsgError as e:
            click.echo(f'  skip #{idx}: {e}', err=True)

    if dry_run:
        click.echo(f'Dry run: would update {changed} entries in {category} ({lang}).')
        return

    en_path, jp_path, _, _ = _CATEGORIES[category]
    rom_path = en_path if lang == 'en' else jp_path
    disk_path = _rom_to_disk(rom_path)
    out_path = output_path_for(disk_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(D.serialize(table))
    click.echo(f'Updated {changed} entries in {category} ({lang}) -> {out_path}')


@group.command('export')
@click.argument('category', metavar='CATEGORY',
                type=click.Choice(CATEGORY_NAMES, case_sensitive=False))
@click.option('--lang', default='en', show_default=True,
              type=click.Choice(['en', 'jp']), help='Language variant to export.')
@click.option('--output', '-o', default=None,
              help='Output JSON file path (default: stdout).')
@click.option('--no-empty', is_flag=True,
              help='Skip empty/null entries.')
def export_cmd(category, lang, output, no_empty):
    """Export a d_msg string table to JSON.

    \b
    Examples:
      xi ui strings export Spell_Names
      xi ui strings export Key_Items --lang jp -o key_items_jp.json
      xi ui strings export Titles --no-empty
    """
    table, _, sub_idx = _load(category, lang)
    entries = []
    for i, block in enumerate(table.blocks):
        text = D.get_text(block, sub_idx)
        if no_empty and not text:
            continue
        entries.append({'id': i, 'text': text})

    out = json.dumps(entries, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(out, encoding='utf-8')
        click.echo(f'Exported {len(entries)} entries → {output}')
    else:
        click.echo(out)

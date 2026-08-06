"""``xi ui spells`` — search and manage FFXI spell/ability metadata.

Spell and ability NAMES come from d_msg DATs:
  Spell_Names    ROM/181/73.DAT  (EN)   ROM/181/69.DAT  (JP)
  Ability_Names  ROM/181/72.DAT  (EN)   ROM/181/68.DAT  (JP)

Spell METADATA (MP cost, cast time, recast, job restrictions, etc.) lives in
the mgc_ block of ROM/118/114.DAT.  The mgc_ block uses a per-record cipher
that has not yet been fully reverse-engineered from raw binary analysis.

Currently confirmed raw fields (no cipher — plaintext at these offsets):
  record + 0x44  u16  mpCost        (verified: 0=id=0, 8=Cure, 0=Dia[id=4]...)
  record + 0x46  u16  unknown_46    (constant 0x0001 for curative spells)
  record + 0x48  u16  unknown_48
  record + 0x4A  u16  unknown_4A

The search/export commands provide spell names (from DAT) plus whatever raw
metadata can be extracted with confidence.  The ``import`` command updates the
mgc_ bytes directly once the cipher is known.
"""

import json
import struct
from pathlib import Path

import click

from xi.common import xi_dmsg as D
from xi.xi_config import FFXI_DIR, output_path_for

# ── constants ─────────────────────────────────────────────────────────────────

MGC_DAT          = 'ROM/118/114.DAT'
SPELL_NAMES_EN   = 'ROM/181/73.DAT'
SPELL_NAMES_JP   = 'ROM/181/69.DAT'
SPELL_HELP_EN    = 'ROM/181/75.DAT'
ABILITY_NAMES_EN = 'ROM/181/72.DAT'
ABILITY_NAMES_JP = 'ROM/181/68.DAT'
ABILITY_HELP_EN  = 'ROM/181/74.DAT'

MGC_RECORD_SIZE  = 0x64
MGC_RECORD_COUNT = 0x400   # 1024


# ── helpers ───────────────────────────────────────────────────────────────────

def _resolve(rom_path: str) -> Path:
    full = Path(FFXI_DIR) / Path(rom_path.replace('/', '\\'))
    out = output_path_for(full)
    return out if out.exists() else full


def _load_dmsg(rom_path: str, bitmask: int = 0x00) -> dict:
    """Return {index: text} from a d_msg DAT, or {} if unavailable."""
    p = _resolve(rom_path)
    if not p.exists():
        return {}
    try:
        table = D.parse(p.read_bytes(), bitmask)
        return {i: D.get_text(b, 0) for i, b in enumerate(table.blocks)}
    except Exception:
        return {}


def _find_mgc_block(data: bytes) -> tuple:
    """Return (data_start, data_end) of the mgc_ record area.

    Uses the block-finding scan from ``xi ui layout mnc2-pos``:
    scans all known block tag positions so later occurrences of 'mgc_' inside
    the mnc2 data region are not mistaken for the block header.
    """
    from xi.ui.xi_mnc2_pos import find_blocks
    blocks = find_blocks(data)
    mgc_block = next((b for b in blocks if b.tag == 'mgc_'), None)
    if mgc_block is None:
        raise click.ClickException(f'mgc_ block not found in {MGC_DAT}')
    data_start = mgc_block.offset + 0x30
    data_end   = min(mgc_block.next_offset, data_start + MGC_RECORD_SIZE * MGC_RECORD_COUNT)
    return data_start, data_end


def _raw_metadata(rec: bytes) -> dict:
    """Extract the subset of mgc_ record fields that are confirmed plaintext."""
    if len(rec) < MGC_RECORD_SIZE:
        return {}
    mp_cost      = struct.unpack_from('<H', rec, 0x44)[0]
    unknown_46   = struct.unpack_from('<H', rec, 0x46)[0]
    unknown_48   = struct.unpack_from('<H', rec, 0x48)[0]
    unknown_4a   = struct.unpack_from('<H', rec, 0x4A)[0]
    return {
        'mp_cost':    mp_cost,
        'unknown_46': unknown_46,
        'unknown_48': unknown_48,
        'unknown_4a': unknown_4a,
    }


def _iter_spells(lang: str = 'en'):
    """Yield one dict per spell slot, combining name (d_msg) + raw metadata (mgc_)."""
    names_rom  = SPELL_NAMES_EN  if lang == 'en' else SPELL_NAMES_JP
    help_rom   = SPELL_HELP_EN   if lang == 'en' else 'ROM/181/71.DAT'

    click.echo(f'Processing Spell_Names: {_resolve(names_rom)}', err=True)
    names = _load_dmsg(names_rom)

    click.echo(f'Processing {MGC_DAT}: {_resolve(MGC_DAT)}', err=True)
    p = _resolve(MGC_DAT)
    if not p.exists():
        raise click.ClickException(f'DAT not found: {p}')

    raw = p.read_bytes()
    data_start, data_end = _find_mgc_block(raw)

    for idx in range(MGC_RECORD_COUNT):
        off = data_start + idx * MGC_RECORD_SIZE
        if off + MGC_RECORD_SIZE > data_end:
            break
        name = names.get(idx, '')
        if not name:
            continue
        rec = raw[off:off + MGC_RECORD_SIZE]
        meta = _raw_metadata(rec)
        yield {'id': idx, 'name': name, **meta}


def _iter_abilities(lang: str = 'en'):
    """Yield one dict per ability slot from Ability_Names d_msg."""
    names_rom = ABILITY_NAMES_EN if lang == 'en' else ABILITY_NAMES_JP

    click.echo(f'Processing Ability_Names: {_resolve(names_rom)}', err=True)
    names = _load_dmsg(names_rom)
    for idx, name in names.items():
        if name:
            yield {'id': idx, 'name': name}


# ── commands ──────────────────────────────────────────────────────────────────

@click.group('spells')
def group():
    """Spell and ability name lookup and metadata export.

    Names come from the Spell_Names / Ability_Names d_msg DATs.
    Numeric metadata (MP cost etc.) comes from ROM/118/114.DAT (mgc_ block);
    some fields are still pending full cipher analysis.
    """
    pass


@group.command('search')
@click.argument('query')
@click.option('--exact', is_flag=True, help='Exact name match.')
@click.option('--abilities', is_flag=True, help='Search abilities instead of spells.')
@click.option('--lang', default='en', show_default=True,
              type=click.Choice(['en', 'jp']))
@click.option('--as-json', is_flag=True)
def search_cmd(query, exact, abilities, lang, as_json):
    """Search for a spell or ability by name.

    \b
    Examples:
      xi ui spells search "Cure"
      xi ui spells search "Mighty Strikes" --abilities
      xi ui spells search "Fire" --exact
    """
    source = _iter_abilities(lang) if abilities else _iter_spells(lang)
    results = []
    for entry in source:
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
        mp = f"  MP:{e['mp_cost']}" if 'mp_cost' in e else ''
        click.echo(f"#{e['id']:>4}  {e['name']:<28}{mp}")


@group.command('export')
@click.option('--output', '-o', default=None, help='Output JSON file path (default: stdout).')
@click.option('--abilities', is_flag=True, help='Export abilities instead of spells.')
@click.option('--lang', default='en', show_default=True,
              type=click.Choice(['en', 'jp']))
def export_cmd(output, abilities, lang):
    """Export all spell (or ability) names and available metadata to JSON.

    Prints which DATs are being processed to stderr.

    \b
    Examples:
      xi ui spells export -o spells.json
      xi ui spells export --abilities -o abilities.json
      xi ui spells export --lang jp
    """
    source = _iter_abilities(lang) if abilities else _iter_spells(lang)
    results = list(source)
    out = json.dumps(results, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(out, encoding='utf-8')
        click.echo(f'Exported {len(results)} entries -> {output}')
    else:
        click.echo(out)


@group.command('import')
@click.argument('json_file', type=click.Path(exists=True))
@click.option('--dry-run', is_flag=True)
def import_cmd(json_file, dry_run):
    """Import edited spell metadata from JSON back into the mgc_ DAT.

    NOTE: only the confirmed plaintext fields (mp_cost, unknown_46,
    unknown_48, unknown_4a) can be written back without cipher knowledge.
    Fields that require cipher decryption (element, magic_type, job levels)
    are not yet supported.

    \b
    Examples:
      xi ui spells import edits.json
      xi ui spells import edits.json --dry-run
    """
    p = _resolve(MGC_DAT)
    if not p.exists():
        raise click.ClickException(f'DAT not found: {p}')

    data = bytearray(p.read_bytes())
    data_start, data_end = _find_mgc_block(bytes(data))

    entries = json.loads(Path(json_file).read_text(encoding='utf-8'))
    changed = 0
    for entry in entries:
        idx = entry.get('id')
        if not isinstance(idx, int):
            continue
        off = data_start + idx * MGC_RECORD_SIZE
        if off + MGC_RECORD_SIZE > data_end:
            continue
        if 'mp_cost' in entry:
            struct.pack_into('<H', data, off + 0x44, int(entry['mp_cost']))
        if 'unknown_46' in entry:
            struct.pack_into('<H', data, off + 0x46, int(entry['unknown_46']))
        if 'unknown_48' in entry:
            struct.pack_into('<H', data, off + 0x48, int(entry['unknown_48']))
        if 'unknown_4a' in entry:
            struct.pack_into('<H', data, off + 0x4A, int(entry['unknown_4a']))
        changed += 1

    if dry_run:
        click.echo(f'Dry run: would update {changed} spell records in {p}')
        return

    out_path = output_path_for(p)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(data))
    click.echo(f'Updated {changed} spell records -> {out_path}')

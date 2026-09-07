"""``xi anim ws`` — resolve a weapon-skill animation number to the motion DAT each
PC race plays, through the two weapon-skill banks in ``FFXiMain.dll``.

The number is the 12-bit ``animation`` field of an action-packet result
(category 3 / ``SkillFinish``), i.e. what ``weapon_skills.animation`` or a
``!injectaction 3 <n>`` carries. It is NOT a skill id and NOT a name id: the
same number picks a different asset per race (259 is ``ROM/204/17`` for Hume
male, ``ROM/226/17`` for Hume female and a ``dumm`` placeholder for Elvaan
female). See docs/anim/weapon-skills.md.

    animation <  256 -> primary_body[race]  + animation
    animation >= 256 -> extended_body[race] + (animation - 256)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

import click

from xi.entity.anim.xi_motion_tables import (
    RACE_NAMES, WsBank, WsSlot, _FileIdResolver, load_maindll,
    weapon_skill_banks, weapon_skill_slot, race_index)

_T_DIR, _T_ROUTINE, _T_MOTION = 0x01, 0x07, 0x2B


def _inspect(spec: Optional[str]) -> Dict[str, object]:
    """What a resolved DAT holds: directories, routines, motion tracks, size,
    and the two flags that matter for a weapon skill — a ``main`` routine (the
    presentation entry point) and the ``dumm`` placeholder directory. A dummy
    DAT still has ``main``, so check both."""
    from xi.xi_config import FFXI_DIR, read_path_for
    from xi.entity.mesh.xi_export import parse_sections
    out: Dict[str, object] = {'exists': False}
    if not spec:
        return out
    p = Path(FFXI_DIR) / spec
    if not p.exists():
        return out
    data = read_path_for(p).read_bytes()
    try:
        secs = parse_sections(data)
    except Exception as e:  # noqa: BLE001
        return {'exists': True, 'size': len(data), 'error': str(e)}
    dirs = [s.name for s in secs if s.type_code == _T_DIR]
    routines = [s.name for s in secs if s.type_code == _T_ROUTINE]
    motions = [s.name for s in secs if s.type_code == _T_MOTION]
    return {
        'exists': True, 'size': len(data), 'dirs': dirs, 'routines': routines,
        'motions': motions, 'main': 'main' in routines, 'dummy': 'dumm' in dirs,
    }


def _bank_rows(banks: Dict[str, WsBank]) -> List[Dict[str, object]]:
    rows = []
    for b in banks.values():
        for i, race in enumerate(RACE_NAMES):
            rows.append({'bank': b.name, 'animations': f'{b.first_animation}..{b.last_animation}',
                         'race': race, 'body': b.body[i], 'companion_a': b.companion_a[i],
                         'companion_b': b.companion_b[i], 'table_offset': b.table_offset})
    return rows


@click.command('ws')
@click.argument('animation', required=False, type=int)
@click.option('--race', default=None,
              help='One race only (HumeMale, HumeFemale, ElvaanMale, ElvaanFemale, '
                   'TaruMale, TaruFemale, Mithra, Galka). Default: all eight.')
@click.option('--json', 'as_json', is_flag=True, help='Emit JSON instead of a table.')
@click.option('--no-inspect', is_flag=True,
              help='Skip opening the resolved DATs (just ids and paths).')
@click.option('--dll', 'dll_path', type=click.Path(exists=True, path_type=Path), default=None,
              help='Read the bank tables from this FFXiMain.dll instead of the install\'s.')
def cmd(animation: Optional[int], race: Optional[str], as_json: bool,
        no_inspect: bool, dll_path: Optional[Path]):
    """Resolve a weapon-skill ANIMATION number to each race's motion DAT.

    ANIMATION is the 12-bit action-packet animation (weapon_skills.animation,
    the second argument of !injectaction 3 N). 0-255 go through the primary
    bank, 256-271 through the extended bank; the client picks by comparing
    with 256, it never adds 256+ to the primary base. Omit ANIMATION to print
    the bank tables (body + two companion bases per race).

    \b
      uv run xi anim ws 259                 # every race
      uv run xi anim ws 259 --race HumeMale
      uv run xi anim ws                     # the tables themselves
    """
    try:
        dll = dll_path.read_bytes() if dll_path else load_maindll()
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    banks = weapon_skill_banks(dll)
    if not banks:
        raise click.ClickException(
            'No weapon-skill bank tables found in FFXiMain.dll (unexpected build).')

    if animation is None:
        rows = _bank_rows(banks)
        if as_json:
            click.echo(json.dumps(rows, indent=2))
            return
        click.echo('Weapon-skill banks in FFXiMain.dll '
                   '(file_id = body[race] + slot; companions A/B = part-2 waist blocks):\n')
        click.echo(f'  {"bank":<9} {"anims":<9} {"race":<13} {"body":>6} {"comp A":>7} {"comp B":>7}  table')
        click.echo('  ' + '-' * 62)
        for r in rows:
            click.echo(f'  {r["bank"]:<9} {r["animations"]:<9} {r["race"]:<13} {r["body"]:>6} '
                       f'{r["companion_a"]:>7} {r["companion_b"]:>7}  0x{r["table_offset"]:x}')
        return

    try:
        races = [race_index(race)] if race else list(range(len(RACE_NAMES)))
        slots: List[WsSlot] = [weapon_skill_slot(banks, ri, animation) for ri in races]
    except ValueError as e:
        raise click.ClickException(str(e))

    resolver = _FileIdResolver()
    results = []
    for s in slots:
        spec = resolver.rom_spec(s.file_id)
        row: Dict[str, object] = {
            'race': s.race, 'animation': s.animation, 'bank': s.bank, 'slot': s.index,
            'file_id': s.file_id, 'dat': spec,
            'companion_a': {'file_id': s.companion_a, 'dat': resolver.rom_spec(s.companion_a)},
            'companion_b': {'file_id': s.companion_b, 'dat': resolver.rom_spec(s.companion_b)},
        }
        if not no_inspect:
            row.update(_inspect(spec))
        results.append(row)

    if as_json:
        click.echo(json.dumps(results, indent=2))
        return

    bank = slots[0].bank
    click.echo(f'animation {animation} -> {bank} bank, slot {slots[0].index}\n')
    hdr = f'  {"race":<13} {"file_id":>7}  {"dat":<16}'
    if not no_inspect:
        hdr += f' {"size":>8}  {"main":<4} {"dumm":<4}  dirs / motions'
    click.echo(hdr)
    click.echo('  ' + '-' * (len(hdr) + 12))
    for r in results:
        line = f'  {r["race"]:<13} {r["file_id"]:>7}  {str(r["dat"] or "-"):<16}'
        if not no_inspect:
            if r.get('exists'):
                dirs = ','.join(r.get('dirs', [])[:4]) or '-'
                mots = ','.join(r.get('motions', [])[:6]) or '-'
                line += (f' {r["size"]:>8}  {"yes" if r.get("main") else "no":<4} '
                         f'{"yes" if r.get("dummy") else "no":<4}  {dirs} / {mots}')
            else:
                line += f' {"missing":>8}'
        click.echo(line)
    click.echo('')
    ca = results[0]['companion_a']
    cb = results[0]['companion_b']
    click.echo(f'  {results[0]["race"]} companions: A {ca["file_id"]} {ca["dat"]}   '
               f'B {cb["file_id"]} {cb["dat"]}')

"""
xi dll ffximain geargroups
========================
Dump FFXiMain.dll's per-race per-slot gear group table (6 groups × 8 bytes each)
so you can see the current model_id ranges each group covers, both retail and
patched. Each `(race, slot)` has 6 groups of `(base_file_id, count)`; the client
walks them cumulatively to map a model_id to a file_id.

Use for auditing what `xi ftable expand gear` / manual DLL patches did.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import click

from xi.xi_config import FFXI_DIR
from xi.gear.xi_inject import RACES, DLL_TABLES_FILE_OFFSET
from xi.gear.xi_core import SLOTS, GROUPS_PER_SLOT, BYTES_PER_TABLE


def read_all_groups(dll_path: Path) -> dict:
    """Return {race: {slot: [{'base_fid', 'count', 'cum_start', 'cum_end'}, ...]}}
    for every (race, slot) pair, with cumulative model_id ranges precomputed."""
    result: dict = {}
    with open(dll_path, 'rb') as f:
        for ri, race in enumerate(RACES):
            result[race] = {}
            for si, slot in enumerate(SLOTS):
                cum = 0
                groups = []
                for g in range(GROUPS_PER_SLOT):
                    off = (DLL_TABLES_FILE_OFFSET
                           + ri * BYTES_PER_TABLE
                           + si * GROUPS_PER_SLOT * 8
                           + g * 8)
                    f.seek(off)
                    base, count = struct.unpack('<II', f.read(8))
                    groups.append({
                        'group': g,
                        'base_fid': base,
                        'count': count,
                        'cum_start': cum,
                        'cum_end': cum + count - 1 if count else cum,
                    })
                    cum += count
                result[race][slot] = groups
    return result


@click.command('geargroups')
@click.option('--dll', type=click.Path(path_type=Path), default=None,
              help='FFXiMain.dll to inspect  [default: FFXI_DIR/FFXiMain.dll]')
@click.option('--race', default=None,
              help=f'Only show one race ({"/".join(RACES)}).')
@click.option('--slot', default=None,
              help=f'Only show one slot ({"/".join(SLOTS)}).')
@click.option('--json', 'as_json', is_flag=True, help='Emit JSON instead of table.')
def cmd(dll: Path | None, race: str | None, slot: str | None, as_json: bool) -> None:
    """List per-race per-slot gear model groups from FFXiMain.dll.

    Each (race, slot) has 6 groups. Client walks them cumulatively — model_id N
    lands in the group where cum_start <= N <= cum_end, at file_id
    base_fid + (N - cum_start).

    \b
    Examples:
      xi dll ffximain geargroups
      xi dll ffximain geargroups --race HumeFemale --slot body
      xi dll ffximain geargroups --json > groups.json
    """
    dll = dll or Path(FFXI_DIR) / 'FFXiMain.dll'
    if not dll.exists():
        raise click.ClickException(f'DLL not found: {dll}')

    if race and race not in RACES:
        raise click.ClickException(f'Unknown race {race!r}. Valid: {", ".join(RACES)}')
    if slot and slot not in SLOTS:
        raise click.ClickException(f'Unknown slot {slot!r}. Valid: {", ".join(SLOTS)}')

    groups = read_all_groups(dll)
    if race:
        groups = {race: groups[race]}
    if slot:
        groups = {r: {slot: s[slot]} for r, s in groups.items()}

    if as_json:
        click.echo(json.dumps(groups, indent=2))
        return

    for r, slots in groups.items():
        for s, gs in slots.items():
            active_count = sum(1 for g in gs if g['count'])
            total = sum(g['count'] for g in gs)
            click.echo(f"\n{r} / {s}  ({active_count}/6 groups, model_id 0-{total-1 if total else 0})")
            click.echo(f"  {'grp':>3}  {'base_fid':>8}  {'count':>5}  {'model_id range':>16}")
            click.echo('  ' + '-' * 42)
            for g in gs:
                if g['count']:
                    rng = f"{g['cum_start']}-{g['cum_end']}"
                else:
                    rng = '(empty)'
                click.echo(f"  G{g['group']}   {g['base_fid']:>8,}  {g['count']:>5,}  {rng:>16}")

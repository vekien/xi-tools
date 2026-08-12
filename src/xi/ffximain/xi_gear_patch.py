"""
xi dll ffximain gear-patch
========================
Patch FFXiMain.dll's per-race per-slot gear group table so custom gear model_ids
work in-game. Extends group G5 for head/body/hands/legs/feet/main/sub to cover
the full 4095 model_id range (12-bit ceiling — anything higher can't be
transmitted in the entity Look struct anyway).

Works with `xi ftable expand gear` + `xi gear inject`: expand pre-allocates
file_id windows in the FTABLE, inject drops your DAT at a `custom_fid()` slot,
this patch tells the client to look there. Without the patch the client's
group walker stops at retail counts and never resolves your custom model_id.

The G5 base_fid must be computed from the CUMULATIVE model_id at G5's start
(sum of G0..G4 counts), NOT from CUSTOM_MODEL_START — the client walks groups
cumulatively. Getting this wrong shifts every lookup by (CUSTOM_MODEL_START -
cumulative_start), which for armor is 64 = the retail G5's original count.

Face and ranged are skipped: their retail G5 is empty (count=0), so the same
G5-extension trick doesn't apply — they'd need a G1-slot patch instead.
"""

from __future__ import annotations

import shutil
import struct
from pathlib import Path

import click

from xi.xi_config import FFXI_DIR, MAX_GEAR_MODELID
from xi.gear.xi_inject import RACES, DLL_TABLES_FILE_OFFSET, custom_fid
from xi.gear.xi_core import SLOTS, GROUPS_PER_SLOT, BYTES_PER_TABLE


# G5-extensible slots. Face/ranged have retail G5=0 and need a different patch.
PATCH_SLOTS = ['head', 'body', 'hands', 'legs', 'feet', 'main', 'sub']


def _read_group(f, race_idx: int, slot: str, group: int) -> tuple[int, int]:
    si = SLOTS.index(slot)
    off = DLL_TABLES_FILE_OFFSET + race_idx * BYTES_PER_TABLE + si * GROUPS_PER_SLOT * 8 + group * 8
    f.seek(off)
    return struct.unpack('<II', f.read(8))


def _write_group(f, race_idx: int, slot: str, group: int, base: int, count: int) -> None:
    si = SLOTS.index(slot)
    off = DLL_TABLES_FILE_OFFSET + race_idx * BYTES_PER_TABLE + si * GROUPS_PER_SLOT * 8 + group * 8
    f.seek(off)
    f.write(struct.pack('<II', base, count))


def patch_gear_groups(dll: Path, max_model: int = MAX_GEAR_MODELID,
                      dry_run: bool = False) -> dict:
    """Extend G5 to cover model_ids up to `max_model` for every extensible
    (race, slot). Returns per-slot before/after summary.

    Uses the pristine `.base` backup (created on first run) to read the
    cumulative G0..G4 counts — running against an already-patched G5 would
    otherwise pollute the cumulative math."""
    backup = dll.with_suffix('.dll.base')
    if not backup.exists() and not dry_run:
        shutil.copy2(dll, backup)

    src = backup if backup.exists() else dll
    changes: list[dict] = []

    # Read cumulative G0..G4 model_id count from pristine backup — never from
    # `dll`, since that may already have G5 modified from a prior run.
    with open(src, 'rb') as fb:
        cumulatives = {}
        for ri, race in enumerate(RACES):
            for slot in PATCH_SLOTS:
                cumulatives[(race, slot)] = sum(_read_group(fb, ri, slot, g)[1] for g in range(5))

    mode = 'r+b' if not dry_run else 'rb'
    with open(dll, mode) as f:
        for ri, race in enumerate(RACES):
            for slot in PATCH_SLOTS:
                g5_start = cumulatives[(race, slot)]
                new_base = custom_fid(race, slot, g5_start, max_model)
                new_count = max_model - g5_start + 1
                old_base, old_count = _read_group(f, ri, slot, 5)
                if not dry_run:
                    _write_group(f, ri, slot, 5, new_base, new_count)
                changes.append({
                    'race': race, 'slot': slot,
                    'g5_start_model': g5_start,
                    'old_base': old_base, 'old_count': old_count,
                    'new_base': new_base, 'new_count': new_count,
                })
    return {'backup': str(backup), 'changes': changes}


@click.command('gear-patch')
@click.option('--dll', type=click.Path(path_type=Path), default=None,
              help='FFXiMain.dll to patch  [default: FFXI_DIR/FFXiMain.dll]')
@click.option('--max-model', type=int, default=MAX_GEAR_MODELID, show_default=True,
              help=f'Ceiling model_id per (race, slot). 12-bit hardware limit is 4095.')
@click.option('--dry-run', is_flag=True, help='Show plan without writing.')
def cmd(dll: Path | None, max_model: int, dry_run: bool) -> None:
    """Patch FFXiMain.dll gear groups to allow custom model_ids up to 4095/slot.

    Extends group G5 for head/body/hands/legs/feet/main/sub across all 8 races
    to cover the full 12-bit model_id range. Pairs with `xi ftable expand
    gear` + `xi gear inject` — that side allocates the file_ids, this tells
    the client's group walker to look there.

    First run auto-creates FFXiMain.dll.base backup (revert = restore it).

    \b
    Examples:
      xi dll ffximain gear-patch                # patch to model_id 4095 (default)
      xi dll ffximain gear-patch --dry-run      # preview without writing
      xi dll ffximain gear-patch --max-model 2048  # smaller ceiling
    """
    dll = dll or Path(FFXI_DIR) / 'FFXiMain.dll'
    if not dll.exists():
        raise click.ClickException(f'DLL not found: {dll}')
    if max_model < 672 or max_model > 4095:
        raise click.ClickException(
            f'--max-model {max_model} out of range. Must be 672..4095 (12-bit ceiling).')

    result = patch_gear_groups(dll, max_model, dry_run=dry_run)

    # Summary: one line per race×slot showing before/after G5 span
    click.echo(f"{'race':12s} {'slot':6s} {'G5_start':>8s}  {'old base/count':>18s}  ->  {'new base/count':>18s}")
    click.echo('-' * 76)
    for c in result['changes']:
        old_span = f"{c['old_base']:,}/{c['old_count']:,}"
        new_span = f"{c['new_base']:,}/{c['new_count']:,}"
        click.echo(f"{c['race']:12s} {c['slot']:6s} {c['g5_start_model']:>8}  {old_span:>18s}  ->  {new_span:>18s}")
    click.echo()
    click.echo(f"Backup: {result['backup']}")
    if dry_run:
        click.echo(click.style('Dry run - nothing written.', fg='cyan'))
    else:
        click.echo(click.style(
            f"Patched {len(result['changes'])} groups. Restart the client to reload the DLL.",
            fg='green'))

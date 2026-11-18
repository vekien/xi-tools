"""``xi gear edit`` — modify a gear model in place.

Today this recolours the model's textures; it is the home for future in-place
edits (scale, mesh swaps, ...). The edit overwrites the model DAT under
``FFXI_DIR`` (a one-time ``.bak`` backup is kept beside it), so **every item
that shares this model ID** gets the new appearance and no addon is required —
but the original and the edited version cannot coexist.

To instead create a NEW model ID (leaving the original untouched, assigned to
specific items via SQL) use ``xi gear inject``. Both commands share the same
recolor option-set, defined here as ``recolor_options`` / ``recolor_kwargs``.

Examples::

    xi gear edit HumeMale main 259 --hue 240 --hue-min 150 --hue-max 340
    xi gear edit ROM/33/17 --tint "#22bb44aa" --blend overlay --sat-max 0.15
"""

import shutil
from pathlib import Path

import click

from xi.gear.xi_export import resolve_gear_target
from xi.tex.xi_recolor import recolour_zone_dat

BLEND_MODES = ['normal', 'multiply', 'screen', 'overlay', 'add']


def recolor_options(f):
    """Attach the shared texture-recolor options to a click command.

    Reused by ``gear edit`` and ``gear inject`` so the two stay in lockstep.
    The matching keyword names are consumed by :func:`recolor_kwargs`.
    """
    options = [
        click.option('--hue', type=float, default=None, help='Hue shift (0–360).'),
        click.option('--saturation', type=float, default=None, help='Saturation adjust (-100 to 100).'),
        click.option('--lightness', type=float, default=None, help='Brightness adjust (-100 to 100).'),
        click.option('--tint', type=str, default=None, help='Tint colour (#RRGGBB[AA]).'),
        click.option('--blend', type=click.Choice(BLEND_MODES), default='normal',
                     help='Blend mode for --tint.'),
        click.option('--hue-min', type=float, default=None, help='Only affect pixels with hue >= this (0–360).'),
        click.option('--hue-max', type=float, default=None, help='Only affect pixels with hue <= this (0–360).'),
        click.option('--sat-min', type=float, default=None, help='Only affect pixels with saturation >= this (0-1).'),
        click.option('--sat-max', type=float, default=None, help='Only affect pixels with saturation <= this (0-1).'),
        click.option('--val-min', type=float, default=None, help='Only affect pixels with brightness >= this (0-1).'),
        click.option('--val-max', type=float, default=None, help='Only affect pixels with brightness <= this (0-1).'),
        click.option('--texture', 'textures', multiple=True, metavar='PATTERN:KEY=VALUE,…',
                     help='Different adjustments for the textures whose name matches PATTERN '
                          '(suny*:lightness=-70). Repeatable; the first match wins.'),
        click.option('--skip', 'skips', multiple=True, metavar='PATTERN',
                     help='Leave the textures whose name matches PATTERN as they are. Repeatable.'),
        click.option('--range', 'ranges', multiple=True, metavar='KEY=VALUE,…',
                     help='A range-filtered pass (hue_min=150,hue_max=340,hue=240), applied in order '
                          'instead of the adjustments above. Repeatable.'),
    ]
    for option in reversed(options):
        f = option(f)
    return f


def recolor_kwargs(hue=None, saturation=None, lightness=None, tint=None, blend='normal',
                   hue_min=None, hue_max=None, sat_min=None, sat_max=None,
                   val_min=None, val_max=None, textures=(), skips=(), ranges=()) -> dict:
    """Normalise the shared ``recolor_options`` values into the kwargs accepted
    by :func:`xi.tex.xi_recolor.recolour_zone_dat`."""
    out = dict(
        hue=hue or 0, saturation=saturation or 0, lightness=lightness or 0,
        tint=tint, blend_mode=blend,
        hue_min=hue_min, hue_max=hue_max, sat_min=sat_min, sat_max=sat_max,
        val_min=val_min, val_max=val_max,
    )
    out.update(texture_passes_kwargs(textures, skips, ranges))
    return out


def texture_passes_kwargs(textures=(), skips=(), ranges=()) -> dict:
    """``--texture`` / ``--skip`` / ``--range`` values as recolour_zone_dat's
    ``texture_overrides`` / ``skip_textures`` / ``color_ranges``."""
    from xi.tex.xi_recolor import parse_adjustments, parse_texture_option
    out = {}
    try:
        if textures:
            out['texture_overrides'] = dict(parse_texture_option(t) for t in textures)
        if ranges:
            out['color_ranges'] = [parse_adjustments(r) for r in ranges]
    except ValueError as e:
        raise click.BadParameter(str(e))
    if skips:
        out['skip_textures'] = list(skips)
    return out


@click.command('edit')
@click.argument('race', metavar='RACE|DAT|FILE_ID')
@click.argument('slot', required=False, metavar='[SLOT]')
@click.argument('model_id', required=False, type=int, metavar='[MODEL_ID]')
@recolor_options
@click.option('--dry-run', is_flag=True, help='Show plan without writing.')
def cmd(race, slot, model_id,
        hue, saturation, lightness, tint, blend,
        hue_min, hue_max, sat_min, sat_max, val_min, val_max,
        textures, skips, ranges, dry_run):
    """Edit a gear model in place (recolour its textures).

    Overwrites the model DAT under FFXI_DIR (a one-time .bak backup is kept),
    so every item that shares this model ID gets the new look — no addon
    needed, but destructive (original and edited cannot coexist). Use
    `xi gear inject` to mint a NEW model ID instead and leave the original
    intact.

    Identify the model explicitly (RACE SLOT MODEL_ID) or let the race be
    auto-detected from a DAT path / file_id passed on its own.

    \b
    RACE: HumeMale, HumeFemale, ElvaanMale, etc.
    SLOT: main, sub, ranged, head, body, hands, legs, feet
    MODEL_ID: Equipment model ID (from item_equipment.MId)
    DAT/FILE_ID: e.g. ROM/33/17 or 10578 (race/slot/model auto-detected)

    \b
    Examples:
      xi gear edit HumeMale main 259 --hue 240 --hue-min 150 --hue-max 340
      xi gear edit HumeMale main 259 --tint "#bb2222aa" --blend overlay --sat-max 0.15
      xi gear edit ROM/33/17 --hue 200
      xi gear edit HumeMale main 259 --range hue_min=150,hue_max=340,hue=240 --range val_max=0.2,lightness=40
    """
    try:
        source, race, slot, model_id = resolve_gear_target(race, slot, model_id)
    except (ValueError, FileNotFoundError) as e:
        raise click.ClickException(str(e))

    opts = recolor_kwargs(hue, saturation, lightness, tint, blend,
                          hue_min, hue_max, sat_min, sat_max, val_min, val_max,
                          textures, skips, ranges)

    click.echo(f'Source: {source.name} ({source.stat().st_size:,} bytes)')
    click.echo(f'  Race: {race}, Slot: {slot}, Model: {model_id}')

    # Edit the DAT in place under FFXI_DIR.
    output = source

    if dry_run:
        click.echo(f'  Would write to: {output}')
        click.echo(click.style('Dry run — nothing written.', fg='cyan'))
        return

    # Back up the DAT once, before the first edit.
    bak = Path(str(output) + '.bak')
    if output.exists() and not bak.exists():
        shutil.copy2(output, bak)
        click.echo(f'  Backed up: {bak.name}')

    stats = recolour_zone_dat(source, output, **opts)
    click.echo(f'  {stats["dxt"]} DXT + {stats["paletted"]} paletted textures'
               + (f', {stats["skipped"]} left as they were' if stats.get("skipped") else ''))
    click.echo(f'  Output: {output}')
    click.echo(click.style('Done. Restart client to see changes.', fg='green'))

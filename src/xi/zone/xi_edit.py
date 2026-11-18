"""``xi zone edit``: recolour an existing zone's model in place.

The ``edit`` half of the zone pair (DESIGN.md §5): ``xi zone inject --clone`` makes a
brand-new zone with its file ids and server stubs; ``xi zone edit`` changes a zone that
already exists, a retail one or a custom one (400+), and nothing else. It takes the same
recolour options as ``xi gear edit`` (``xi.gear.xi_edit.recolor_options``: ``--hue`` …
``--texture``, ``--skip``, ``--range``) plus the environment and fog options of
``zone inject``, and hands them to the same engine (``xi.tex.xi_recolor.recolour_zone_dat``).

The model DAT is opened through ``editable_dat(fresh=False)``: the first edit keeps
``<dat>.base``, and later edits layer on the current bytes, so other zone edits
(``import-object``, ``import-json``) are kept and two ``--hue 90`` runs add up to 180.
``xi zone reset`` puts the pristine model back.
"""
from __future__ import annotations

from pathlib import Path

import click

from xi.gear.xi_edit import recolor_kwargs, recolor_options


def resolve_zone_model(target: str) -> tuple[str, Path]:
    """A zone id, a zone name or a DAT path → (label, model DAT)."""
    from xi.zone.xi_check import _zone_dats
    if str(target).isdigit() or '/' in target or '\\' in target or target.lower().endswith('.dat'):
        (label, path), = _zone_dats([target], False)
        return label, path
    from xi.zone.xi_inject import _find_zone_by_name, _load_zone_names
    names = _load_zone_names()
    zid = _find_zone_by_name(target, names)
    if zid is None:
        raise click.ClickException(f'No zone called "{target}" (or more than one matches). '
                                   'Give its id, or `xi zone search` to find it.')
    (_, path), = _zone_dats([str(zid)], False)
    return f'zone {zid} ({names[zid]})', path


@click.command('edit')
@click.argument('zone', metavar='ZONE_ID|NAME|DAT')
@recolor_options
@click.option('--env-lightness', type=float, default=None,
              help='Environment lighting adjustment (-100 to 100). Default: --lightness.')
@click.option('--fog-tint', type=str, default=None, help='Fog colour (#RRGGBB).')
@click.option('--fog-end', type=float, default=None,
              help='Fog far distance (lower = denser). Retail zones use 100–500.')
@click.option('--fog-start', type=float, default=None, help='Fog near distance.')
@click.option('--dry-run', is_flag=True, help='Show what would change; write nothing.')
def cmd(zone, hue, saturation, lightness, tint, blend,
        hue_min, hue_max, sat_min, sat_max, val_min, val_max, textures, skips, ranges,
        env_lightness, fog_tint, fog_end, fog_start, dry_run):
    """Recolour an existing zone's model in place.

    Takes the same colour options as `xi gear edit`, plus the zone's environment
    lighting and fog. The first edit keeps the DAT's .base; later edits layer on top;
    `xi zone reset` undoes them all. To make a new recoloured copy of a zone instead,
    use `xi zone inject NAME --clone ZONE`.

    \b
    Examples:
      xi zone edit 244 --hue 180 --lightness -30 --dry-run
      xi zone edit "Lower Jeuno" --texture "suny*:lightness=-70" --skip "clod*"
      xi zone edit 115 --range hue_min=60,hue_max=150,hue=-40 --fog-tint "#203040" --fog-end 120
      xi zone edit ROM10/1/8.DAT --hue 120
    """
    import xi.xi_config as cfg
    from xi.tex.xi_recolor import recolour_zone_dat

    opts = recolor_kwargs(hue, saturation, lightness, tint, blend,
                          hue_min, hue_max, sat_min, sat_max, val_min, val_max,
                          textures, skips, ranges)
    if not any([hue, saturation, lightness, tint, textures, ranges, env_lightness, fog_tint,
                fog_end is not None, fog_start is not None]):
        raise click.UsageError('nothing to change: give a colour option (--hue, --texture, '
                               '--range, …) or an environment one (--fog-tint, --fog-end, …)')

    label, source = resolve_zone_model(zone)
    if not source.is_file():
        raise click.ClickException(f'{label}: model DAT not found at {source}')
    click.echo(f'{label}: {source} ({source.stat().st_size:,} bytes)')

    if dry_run:
        click.echo(click.style('Dry run — nothing written.', fg='cyan'))
        return

    target = cfg.editable_dat(source, fresh=False)
    stats = recolour_zone_dat(target, target, env_lightness=env_lightness, fog_tint=fog_tint,
                              fog_end=fog_end, fog_start=fog_start, **opts)
    click.echo(f'  {stats["dxt"]} DXT + {stats["paletted"]} paletted textures, '
               f'{stats["environment"]} environment sections'
               + (f', {stats["skipped"]} left as they were' if stats.get('skipped') else ''))
    click.echo(click.style(f'✓ Zone model recoloured -> {target}', fg='green'))

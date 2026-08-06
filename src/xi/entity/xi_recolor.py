"""``xi entity recolor`` — clone an existing entity model with recolored
textures and inject it as a new model ID.

Examples::

    xi entity recolor "Crimson Tiger" --clone 308 --tint "#ff3300cc" --blend multiply
    xi entity recolor "Frost Dragon" --clone 420 --hue 180 --saturation 30
    xi entity recolor "Shadow Goblin" --clone 291 --lightness -40 --tint "#330066aa" --blend overlay
    xi entity recolor "Dark Warlord" --clone 617 --scale 3.0 --saturation -60 --lightness -30 --blur "#6428b4,25,180"
    xi entity recolor "Orc Knight" --clone 617 --weapon "gear:HumeMale:main:259"
"""

import os
import tempfile

import click

from xi.xi_config import FFXI_DIR
from xi.entity.xi_core import MODEL_SAFE_START
from xi.tex.xi_recolor import recolour_zone_dat
from xi.entity.xi_inject import cmd as inject_cmd


@click.command('recolor')
@click.argument('name')
@click.option('--clone', type=int, required=True,
              help='Source model ID to clone.')
@click.option('--hue', type=float, default=None,
              help='Hue shift in degrees (0–360).')
@click.option('--saturation', type=float, default=None,
              help='Saturation adjust (-100 to 100).')
@click.option('--lightness', type=float, default=None,
              help='Brightness adjust (-100 to 100).')
@click.option('--tint', type=str, default=None,
              help='Tint colour (#RRGGBB or #RRGGBBAA).')
@click.option('--blend', type=click.Choice(['normal', 'multiply', 'screen', 'overlay', 'add']),
              default='normal', help='Blend mode for --tint.')
@click.option('--scale', type=float, default=None,
              help='Uniform scale factor (e.g. 2.0 = double, 0.5 = half).')
@click.option('--blur', type=str, default=None,
              help='Add blur aura: "#RRGGBB" or "#RRGGBB,alpha,radius" (e.g. "#8040cc,25,150").')
@click.option('--weapon', type=str, default=None,
              help='Swap main weapon. Model ID, gear:RACE:SLOT:ID, or ROM path.')
@click.option('--weapon-hue', type=float, default=None,
              help='Hue shift for swapped weapon.')
@click.option('--weapon-tint', type=str, default=None,
              help='Tint colour for swapped weapon.')
@click.option('--weapon-blend', type=click.Choice(['normal', 'multiply', 'screen', 'overlay', 'add']),
              default='normal', help='Blend mode for weapon tint.')
@click.option('--weapon-scale', type=float, default=None,
              help='Scale factor for swapped weapon.')
@click.option('--offhand', type=str, default=None,
              help='Swap offhand weapon (same format as --weapon).')
@click.option('--offhand-hue', type=float, default=None,
              help='Hue shift for offhand weapon.')
@click.option('--offhand-tint', type=str, default=None,
              help='Tint colour for offhand weapon.')
@click.option('--offhand-blend', type=click.Choice(['normal', 'multiply', 'screen', 'overlay', 'add']),
              default='normal', help='Blend mode for offhand tint.')
@click.option('--offhand-scale', type=float, default=None,
              help='Scale factor for offhand weapon.')
@click.option('--joint-scales', type=str, default=None,
              help='Per-joint scaling: "5-7:4.0" or "5-7:4.0,20-30:2.0".')
@click.option('--modelid', type=int, default=None,
              help=f'Target model ID (default: auto-assign from {MODEL_SAFE_START}+).')
@click.option('--dry-run', is_flag=True, help='Show plan without writing.')
def cmd(name: str, clone: int, hue: float | None, saturation: float | None,
        lightness: float | None, tint: str | None, blend: str,
        scale: float | None, blur: str | None,
        weapon: str | None, weapon_hue: float | None,
        weapon_tint: str | None, weapon_blend: str,
        weapon_scale: float | None,
        offhand: str | None, offhand_hue: float | None,
        offhand_tint: str | None, offhand_blend: str,
        offhand_scale: float | None,
        joint_scales: str | None,
        modelid: int | None, dry_run: bool):
    """Clone an entity model with recolored/scaled textures.

    NAME is the new model's name (e.g. "Crimson Tiger").

    \b
    Examples:
      xi entity recolor "Crimson Tiger" --clone 308 --tint "#ff3300cc" --blend multiply
      xi entity recolor "Giant Beetle" --clone 408 --scale 4.0 --saturation -100
      xi entity recolor "Holy Tiger" --clone 308 --blur "#ffffff,30,120"
      xi entity recolor "Orc Knight" --clone 617 --weapon "gear:HumeMale:main:259"
      xi entity recolor "Bighead Skel" --clone 564 --joint-scales "5-7:4.0"
    """
    from pathlib import Path
    from xi.ftable.xi_core import load_all_tables, scan_file_ids
    from xi.entity.xi_core import modelid_to_file_id

    # Resolve source model DAT
    tables = load_all_tables()
    src_fid = modelid_to_file_id(clone)
    hits = scan_file_ids([src_fid], tables)
    if not hits:
        raise click.ClickException(f'Model {clone} not found in FTABLE (file_id {src_fid}).')

    source = Path(FFXI_DIR) / hits[0]['dat']
    if not source.exists():
        raise click.ClickException(f'Source DAT not found: {source}')

    # Parse --blur option: "#RRGGBB" or "#RRGGBB,alpha,radius"
    blur_opts = None
    if blur:
        parts = blur.split(',')
        hex_color = parts[0].lstrip('#')
        if len(hex_color) != 6:
            raise click.ClickException('--blur color must be #RRGGBB')
        blur_opts = {
            'r': int(hex_color[0:2], 16),
            'g': int(hex_color[2:4], 16),
            'b': int(hex_color[4:6], 16),
            'alpha': int(parts[1]) if len(parts) > 1 else 20,
            'radius': float(parts[2]) if len(parts) > 2 else 120.0,
        }

    # Parse --joint-scales
    js_parsed = None
    if joint_scales:
        from xi.entity.xi_joints import parse_joint_scales
        js_parsed = parse_joint_scales(joint_scales)

    # Resolve weapon sources
    weapon_dat_path = None
    offhand_dat_path = None
    if weapon:
        from xi.entity.xi_weapon import resolve_weapon_source
        weapon_dat_path = resolve_weapon_source(weapon)
    if offhand:
        from xi.entity.xi_weapon import resolve_weapon_source
        offhand_dat_path = resolve_weapon_source(offhand)

    has_adjustments = any([hue, saturation, lightness, tint, scale, blur_opts,
                           weapon_dat_path, offhand_dat_path, js_parsed])
    desc = []
    if hue: desc.append(f'hue {hue}°')
    if saturation: desc.append(f'sat {saturation:+.0f}%')
    if lightness: desc.append(f'lit {lightness:+.0f}%')
    if tint: desc.append(f'tint {tint} ({blend})')
    if scale: desc.append(f'scale {scale}x')
    if blur_opts: desc.append(f'blur RGB({blur_opts["r"]},{blur_opts["g"]},{blur_opts["b"]})')
    if weapon_dat_path: desc.append(f'weapon swap ({weapon})')
    if offhand_dat_path: desc.append(f'offhand swap ({offhand})')
    if js_parsed: desc.append(f'{len(js_parsed)} joints scaled')

    click.echo(f'Cloning model {clone} → "{name}"')
    click.echo(f'  Source: {source.name} ({source.stat().st_size:,} bytes)')
    if desc:
        click.echo(f'  Adjustments: {", ".join(desc)}')

    if dry_run:
        click.echo(click.style('\nDry run — nothing written.', fg='cyan'))
        return

    # Recolour/scale to temp file
    with tempfile.NamedTemporaryFile(suffix='.DAT', delete=False) as tmp:
        tmp_path = tmp.name

    try:
        raw = source.read_bytes()

        # Weapon swapping (before recoloring — operates on raw DAT)
        if weapon_dat_path or offhand_dat_path:
            from xi.entity.xi_weapon import swap_weapon, recolor_weapon_dat
            if weapon_dat_path:
                wep_dat = weapon_dat_path.read_bytes()
                wep_recolor = _build_weapon_opts(weapon_hue, weapon_tint,
                                                  weapon_blend, weapon_scale)
                if wep_recolor:
                    wep_dat = recolor_weapon_dat(weapon_dat_path, wep_recolor)
                raw = swap_weapon(raw, wep_dat)
                click.echo(f'    Weapon swapped from {weapon_dat_path.name}')

            if offhand_dat_path:
                off_dat = offhand_dat_path.read_bytes()
                off_recolor = _build_weapon_opts(offhand_hue, offhand_tint,
                                                  offhand_blend, offhand_scale)
                if off_recolor:
                    off_dat = recolor_weapon_dat(offhand_dat_path, off_recolor)
                raw = swap_weapon(raw, off_dat, block_name='rng_')
                click.echo(f'    Offhand swapped from {offhand_dat_path.name}')

        # Write intermediate data for recoloring
        Path(tmp_path).write_bytes(raw)

        if any([hue, saturation, lightness, tint, scale]):
            stats = recolour_zone_dat(
                Path(tmp_path), Path(tmp_path),
                hue=hue or 0, saturation=saturation or 0, lightness=lightness or 0,
                tint=tint, blend_mode=blend,
                scale=scale or 0,
            )
            click.echo(f'    {stats["dxt"]} DXT + {stats["paletted"]} paletted textures')
            if stats.get('joints_scaled'):
                click.echo(f'    Scaled: {stats["joints_scaled"]} joints + '
                           f'{stats["vertices_scaled"]} vertices + '
                           f'{stats["anims_scaled"]} anim channels')

        # Per-joint scaling
        if js_parsed:
            from xi.entity.xi_joints import scale_joints_inplace
            from xi.entity.anim.xi_export import parse_sections
            dat = bytearray(Path(tmp_path).read_bytes())
            sections = parse_sections(bytes(dat))
            js_stats = scale_joints_inplace(dat, sections, js_parsed)
            Path(tmp_path).write_bytes(bytes(dat))
            click.echo(f'    Parts: {js_stats["joints"]} joints + '
                       f'{js_stats["vertices"]} vertices scaled')

        # Inject blur section if requested
        if blur_opts:
            from xi.entity.xi_blur import build_blur_section, inject_blur_section
            blur_data = build_blur_section(**blur_opts)
            dat = bytearray(Path(tmp_path).read_bytes())
            dat = inject_blur_section(dat, blur_data)
            Path(tmp_path).write_bytes(bytes(dat))
            click.echo(f'    Blur: 0x5E section injected')

        # Invoke entity inject with the processed DAT
        pet_name = name.replace(' ', '_')[:15]
        ctx = click.Context(inject_cmd)
        ctx.invoke(inject_cmd,
                   dat_file=tmp_path,
                   modelid=modelid,
                   pool_id=None,
                   pet_id=None,
                   pet_name=pet_name,
                   species=114,
                   subdir=1,
                   register_existing=False,
                   dry_run=False)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _build_weapon_opts(hue, tint, blend, scale):
    """Build weapon recolor options dict from CLI args, or None if no opts."""
    opts = {}
    if hue: opts['hue'] = hue
    if tint: opts['tint'] = tint
    if blend and blend != 'normal': opts['blend_mode'] = blend
    if scale: opts['scale'] = scale
    return opts or None

import shutil
from pathlib import Path

import click

import xi.xi_config as _cfg
from xi.xi_config import ensure_base, output_path_for, read_path_for
from xi.ui.xi_core import (compression_name, output_file_names, parse_dds, parse_textures,
                           replace_texture, resolve_layout_reference, scale_layout_rects,
                           sync_layout_rects, write_dds)
from xi.utils.xi_core import convert_dds_to_png, convert_png_to_dds


def _apply_ffxi_dir(ffxi: str | None) -> str:
    """Override FFXI_DIR for this invocation if --ffxi was supplied."""
    if ffxi:
        _cfg.FFXI_DIR = ffxi
    return _cfg.FFXI_DIR

# Window-skin theme set: ROM/0/14..21 share the same 4 texture names
# (newtex/hfr1/corner/vfr1), so edited PNGs propagate across them by name.
WIN0_THEME_IDS = list(range(14, 22))


def _resolve_dat_path(dat_path: str) -> Path:
    p = Path(dat_path)
    if not p.is_absolute():
        p = Path(_cfg.FFXI_DIR) / p
    if not p.exists():
        raise click.ClickException(f'DAT not found: {p}')
    return p


def _default_export_dir(dat_file: Path) -> Path:
    try:
        rel = dat_file.resolve().relative_to(Path(_cfg.FFXI_DIR).resolve())
    except ValueError:
        rel = dat_file

    parts = list(rel.parts)
    if parts and parts[0].upper().startswith('ROM'):
        parts = parts[1:]

    if not parts:
        return Path('exports/ui') / dat_file.stem

    if len(parts) == 1:
        return Path('exports/ui') / Path(parts[0]).stem

    return Path('exports/ui').joinpath(*parts[:-1], Path(parts[-1]).stem)


ALPHA_SIDECAR = 'alpha-scale.json'


def _alpha_boost_png(png_path: Path) -> float:
    """Brighten a PNG's alpha to full range, returning the factor applied.

    FFXI stores UI alpha at half scale -- `0x80` means fully opaque -- so an exported
    PNG opens at roughly 50% opacity and is miserable to edit. DXT3's 4-bit alpha
    cannot hold 128 exactly, so the encoder dithers 119/136 (7/17 and 8/17) around it;
    that dither is the fingerprint of a half-scale texture.

    The factor is per-texture `255 / max_alpha` rather than a flat 2.0 because these
    DATs mix conventions: `ex1us` peaks at 136 but `20logo` is already 255. A flat 2.0
    would clamp the latter and the inverse would come back as 128, wrecking it. Deriving
    the factor from the actual peak never clamps, so the round-trip is exact.
    """
    from PIL import Image
    im = Image.open(png_path)
    if im.mode != 'RGBA':
        return 1.0
    peak = im.getchannel('A').getextrema()[1]
    if peak == 0 or peak >= 255:
        return 1.0
    factor = 255.0 / peak
    r, g, b, a = im.split()
    a = a.point(lambda v: min(255, round(v * factor)))
    Image.merge('RGBA', (r, g, b, a)).save(png_path)
    return factor


def _apply_alpha_scale(png_path: Path, factor: float, out_path: Path) -> Path:
    """Write `png_path` to `out_path` with its alpha multiplied by `factor`."""
    from PIL import Image
    im = Image.open(png_path)
    if im.mode != 'RGBA' or factor == 1.0:
        return png_path
    r, g, b, a = im.split()
    a = a.point(lambda v: min(255, round(v * factor)))
    Image.merge('RGBA', (r, g, b, a)).save(out_path)
    return out_path


def _read_alpha_sidecar(work_dir: Path) -> dict:
    import json
    f = work_dir / ALPHA_SIDECAR
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return {}


@click.command('simple-extract')
@click.argument('dat_path', metavar='DAT_FILE')
@click.option('--raw-alpha', is_flag=True,
              help="Write the PNGs with FFXI's raw half-scale alpha (they will look "
                   "roughly 50% transparent) instead of brightening them for editing.")
@click.option('--ffxi', default=None, metavar='DIR',
              help='Override FFXI_DIR for this command (e.g. a pivot/override root).')
def simple_extract_cmd(dat_path: str, raw_alpha: bool, ffxi: str | None):
    """Extract a UI DAT to DDS and immediately convert all DDS files to PNG.

    The export folder is derived automatically from the DAT path:
    `ROM/0/1.DAT -> exports/ui/0/1`.
    """
    _apply_ffxi_dir(ffxi)
    dat_file = _resolve_dat_path(dat_path)
    out_dir = _default_export_dir(dat_file)

    data = read_path_for(dat_file).read_bytes()
    entries = parse_textures(data)
    if not entries:
        raise click.ClickException('No texture entries found in this DAT.')

    out_dir.mkdir(parents=True, exist_ok=True)

    click.echo(f'File: {dat_file}')
    click.echo(f'Export dir: {out_dir}')
    click.echo()

    written = 0
    alpha_scales: dict[str, float] = {}
    for entry, dds_name in zip(entries, output_file_names(entries)):
        dds_path = out_dir / dds_name
        png_path = out_dir / f'{dds_path.stem}.png'
        write_dds(entry, dds_path)
        convert_dds_to_png(dds_path, png_path)
        boost = 1.0 if raw_alpha else _alpha_boost_png(png_path)
        if boost != 1.0:
            alpha_scales[png_path.stem] = boost
        note = '' if boost == 1.0 else f' alpha x{boost:.3g}'
        click.echo(f'  extracted {dds_name} [{compression_name(entry)}]{note} -> {png_path.name}')
        written += 1

    import json
    sidecar = out_dir / ALPHA_SIDECAR
    if alpha_scales:
        sidecar.write_text(json.dumps(alpha_scales, indent=2), encoding='utf-8')
    elif sidecar.exists():
        sidecar.unlink()      # stale scales would be re-applied on the next import

    click.echo()
    click.echo(f'Extracted and converted {written} texture(s) to {out_dir}')
    if alpha_scales:
        click.echo(f'Brightened alpha on {len(alpha_scales)} texture(s); '
                   f'{ALPHA_SIDECAR} records the factors so import restores them.')


def _import_one(dat_file: Path, out_file: Path, work_dir: Path, requested_format: str,
                fix_layout: bool = True, reference: str | None = None,
                repair: bool = False) -> None:
    """Rebuild DDS from the PNGs in work_dir and patch them into dat_file -> out_file."""
    if not work_dir.exists() or not work_dir.is_dir():
        raise click.ClickException(f'Working directory not found: {work_dir}')

    data = bytearray(dat_file.read_bytes())
    entries = parse_textures(data)
    if not entries:
        raise click.ClickException('No texture entries found in this DAT.')

    click.echo(f'File: {dat_file}')
    click.echo(f'Working dir: {work_dir}')
    click.echo(f'Output DAT: {out_file}')
    click.echo(f'Format: {requested_format.lower()}')

    converted = 0
    filenames = output_file_names(entries)
    entry_by_filename = dict(zip(filenames, entries))
    alpha_scales = _read_alpha_sidecar(work_dir)
    tmp_dir = work_dir / '.alpha'
    for filename in filenames:
        png_path = work_dir / f'{Path(filename).stem}.png'
        dds_path = work_dir / filename
        if not png_path.exists():
            continue

        # Undo the brightening sx applied, so the DAT gets FFXI's own alpha back.
        boost = float(alpha_scales.get(png_path.stem, 1.0))
        source_png = png_path
        if boost != 1.0:
            tmp_dir.mkdir(parents=True, exist_ok=True)
            source_png = _apply_alpha_scale(png_path, 1.0 / boost, tmp_dir / png_path.name)

        _analysis, dds_format, format_reason = convert_png_to_dds(
            source_png,
            dds_path,
            requested_format=requested_format.lower(),
            match_source=str(dds_path) if requested_format.lower() == 'auto' and dds_path.exists() else None,
        )
        entry = entry_by_filename[filename]
        note = '' if boost == 1.0 else f'; alpha /{boost:.3g}'
        click.echo(
            f'  rebuilt {png_path.name} -> {dds_path.name} '
            f'[{dds_format}; source {compression_name(entry)}; {format_reason}{note}]'
        )
        converted += 1

    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if converted == 0:
        raise click.ClickException(f'No matching .png files found in {work_dir}')

    replaced = 0
    missing = 0
    resized: dict = {}
    for entry, filename in reversed(list(zip(entries, filenames))):
        dds_path = work_dir / filename
        if not dds_path.exists():
            missing += 1
            continue
        was = compression_name(entry)   # replace_texture updates the entry in place
        was_size = f'{entry.width}x{entry.height}'
        try:
            dds = parse_dds(dds_path)
            replace_texture(data, entry, dds)
        except ValueError as e:
            raise click.ClickException(str(e))
        now_size = f'{entry.width}x{entry.height}'
        grew = '' if was_size == now_size else f' {was_size} -> {now_size}'
        if grew:
            resized[entry.name] = (tuple(int(v) for v in was_size.split('x')),
                                   (entry.width, entry.height))
        click.echo(
            f'  imported {filename} [{was} -> {compression_name(entry)}]{grew} '
            f'-> {entry.name or "unnamed"}'
        )
        replaced += 1

    if fix_layout:
        for name, off, was, now in scale_layout_rects(data, resized):
            click.echo(f'  layout: {name} @0x{off:06x} src {was[0]}x{was[1]}@({was[2]},{was[3]}) '
                       f'-> {now[0]}x{now[1]}@({now[2]},{now[3]})')
    if repair:
        _fix_layout(data, dat_file, reference)

    out_file.parent.mkdir(parents=True, exist_ok=True)
    if out_file.exists():
        ensure_base(out_file)   # keep the pristine bytes for undo
    out_file.write_bytes(data)
    click.echo(f'Rebuilt {converted} DDS file(s) and patched {replaced} texture(s) into {out_file}')
    if missing:
        click.echo(f'Left {missing} texture(s) unchanged (no matching .dds present)')


def _fix_layout(data: bytearray, dat_file: Path, reference: str | None) -> None:
    """Repoint whole-texture sprite rects at their texture's current pixel size.

    Runs on every import, not only when this run resized something: a DAT can arrive
    already mismatched (texture enlarged by another tool, rects left at the old
    extent), and an export/re-import round-trip of that DAT changes no dimensions, so
    a resize-triggered fixup would never fire and the sprite stays cropped.
    """
    ref, how = resolve_layout_reference(dat_file, reference)
    click.echo(f'  layout: using {how}')
    for name, off, old, new in sync_layout_rects(data, ref):
        click.echo(f'  layout: {name} @0x{off:06x} src {old[0]}x{old[1]}@({old[2]},{old[3]}) '
                   f'-> {new[0]}x{new[1]}@({new[2]},{new[3]})')


def _seed_theme_from_source(source_dir: Path, theme_dat: Path) -> Path:
    """Copy the source theme's edited PNGs into theme_dat's work dir (by name, so
    they map onto matching texture names regardless of per-DAT order), and extract
    the theme's current DDS first so 'auto' format matching has a reference."""
    theme_work = _default_export_dir(theme_dat)
    theme_work.mkdir(parents=True, exist_ok=True)
    # extract the theme's current DDS (reference for auto format matching)
    tdata = theme_dat.read_bytes()
    tentries = parse_textures(tdata)
    for entry, fn in zip(tentries, output_file_names(tentries)):
        write_dds(entry, theme_work / fn)
    # copy the source PNGs in by filename
    for png in sorted(source_dir.glob('*.png')):
        shutil.copy2(png, theme_work / png.name)
    return theme_work


@click.command('simple-import')
@click.argument('dat_path', metavar='DAT_FILE')
@click.option('--output-dat', default=None,
              help='Write to a different DAT path instead of overwriting DAT_FILE.')
@click.option(
    '--format',
    'requested_format',
    type=click.Choice(['auto', 'dxt1', 'dxt3', 'dxt5'], case_sensitive=False),
    default='auto',
    show_default=True,
    help='DDS compression format. auto preserves the existing extracted DDS format.',
)
@click.option('--reference', default=None, metavar='DAT',
              help='Unmodified DAT to read original sprite rects from. Defaults to the '
                   'built-in reference sheet, then <dat>.base.')
@click.option('--no-resize', 'fix_layout', is_flag=True, default=True, flag_value=False,
              help='Import the textures but leave sprite source rects alone, so a texture '
                   'whose size changed keeps pointing at the old sub-region.')
@click.option('--repair-rects', 'repair', is_flag=True,
              help='Also rebuild every sprite rect from the reference sheet. For a DAT left '
                   'inconsistent by an earlier edit; not needed for a normal import.')
@click.option('--all-themes', is_flag=True,
              help='Window skins only (ROM/0/14..21): apply this theme\'s edited PNGs to ALL skins and import each.')
@click.option('--ffxi', default=None, metavar='DIR',
              help='Override FFXI_DIR for this command (e.g. a pivot/override root).')
def simple_import_cmd(dat_path: str, output_dat: str | None, requested_format: str,
                      reference: str | None, fix_layout: bool, repair: bool,
                      all_themes: bool, ffxi: str | None):
    """Convert edited PNG files back to DDS and import them into a UI DAT.

    The working folder is derived automatically from the DAT path:
    `ROM/0/1.DAT -> exports/ui/0/1`.

    With --all-themes, the edited PNGs from this DAT's folder are copied onto every
    window-skin DAT (ROM/0/14..21) and imported into each in one go.
    """
    _apply_ffxi_dir(ffxi)
    dat_file = _resolve_dat_path(dat_path)

    if requested_format.lower() == 'dxt5':
        # Tested on ROM/119/50: a correctly-built all-DXT5 DAT renders flat grey.
        # See docs/ui/export.md. Left selectable so the failure can be re-tested.
        click.echo('WARNING: the client does not render DXT5 (5TXD) — textures come '
                   'out flat grey. Use dxt3 for alpha, dxt1 for opaque.')

    if all_themes:
        try:
            stem_id = int(dat_file.stem)
        except ValueError:
            stem_id = None
        if dat_file.parent.name != '0' or stem_id not in WIN0_THEME_IDS:
            raise click.ClickException('--all-themes only supports window skins ROM/0/14..21.')

        source_dir = _default_export_dir(dat_file)
        if not source_dir.is_dir() or not list(source_dir.glob('*.png')):
            raise click.ClickException(f'No edited PNGs found in {source_dir}. Extract + edit this DAT first.')

        click.echo(f'Applying {dat_file.stem} theme PNGs to all window skins {WIN0_THEME_IDS[0]}..{WIN0_THEME_IDS[-1]}')
        click.echo()
        for theme_id in WIN0_THEME_IDS:
            theme_dat = dat_file.with_name(f'{theme_id}.DAT')
            if not theme_dat.exists():
                click.echo(f'-- skip {theme_dat.name}: not found')
                continue
            work = source_dir if theme_id == stem_id else _seed_theme_from_source(source_dir, theme_dat)
            _import_one(theme_dat, output_path_for(theme_dat), work, requested_format,
                        fix_layout, reference, repair)
            click.echo()
        click.echo('All themes updated.')
        return

    if output_dat:
        out_file = Path(output_dat)
        if not out_file.is_absolute():
            out_file = Path(_cfg.FFXI_DIR) / out_file
    else:
        # Default: write the DAT back in place.
        out_file = output_path_for(dat_file)
    _import_one(dat_file, out_file, _default_export_dir(dat_file), requested_format,
                fix_layout, reference, repair)

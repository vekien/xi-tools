import shutil
from pathlib import Path

import click

import xi.xi_config as _cfg
from xi.xi_config import ensure_base, output_path_for, read_path_for
from xi.ui.xi_core import compression_name, output_file_names, parse_dds, parse_textures, replace_texture, write_dds
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


@click.command('simple-extract')
@click.argument('dat_path', metavar='DAT_FILE')
@click.option('--ffxi', default=None, metavar='DIR',
              help='Override FFXI_DIR for this command (e.g. a pivot/override root).')
def simple_extract_cmd(dat_path: str, ffxi: str | None):
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
    for entry, dds_name in zip(entries, output_file_names(entries)):
        dds_path = out_dir / dds_name
        png_path = out_dir / f'{dds_path.stem}.png'
        write_dds(entry, dds_path)
        convert_dds_to_png(dds_path, png_path)
        click.echo(f'  extracted {dds_name} [{compression_name(entry)}] -> {png_path.name}')
        written += 1

    click.echo()
    click.echo(f'Extracted and converted {written} texture(s) to {out_dir}')


def _import_one(dat_file: Path, out_file: Path, work_dir: Path, requested_format: str) -> None:
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
    for filename in filenames:
        png_path = work_dir / f'{Path(filename).stem}.png'
        dds_path = work_dir / filename
        if not png_path.exists():
            continue
        _analysis, dds_format, format_reason = convert_png_to_dds(
            png_path,
            dds_path,
            requested_format=requested_format.lower(),
            match_source=str(dds_path) if requested_format.lower() == 'auto' and dds_path.exists() else None,
        )
        entry = entry_by_filename[filename]
        click.echo(
            f'  rebuilt {png_path.name} -> {dds_path.name} '
            f'[{dds_format}; source {compression_name(entry)}; {format_reason}]'
        )
        converted += 1

    if converted == 0:
        raise click.ClickException(f'No matching .png files found in {work_dir}')

    replaced = 0
    missing = 0
    for entry, filename in reversed(list(zip(entries, filenames))):
        dds_path = work_dir / filename
        if not dds_path.exists():
            missing += 1
            continue
        try:
            dds = parse_dds(dds_path)
            replace_texture(data, entry, dds)
        except ValueError as e:
            raise click.ClickException(str(e))
        click.echo(
            f'  imported {filename} [{dds.fourcc.decode()} -> {compression_name(entry)}] '
            f'-> {entry.name or "unnamed"}'
        )
        replaced += 1

    out_file.parent.mkdir(parents=True, exist_ok=True)
    if out_file.exists():
        ensure_base(out_file)   # keep the pristine bytes for undo
    out_file.write_bytes(data)
    click.echo(f'Rebuilt {converted} DDS file(s) and patched {replaced} texture(s) into {out_file}')
    if missing:
        click.echo(f'Left {missing} texture(s) unchanged (no matching .dds present)')


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
@click.option('--all-themes', is_flag=True,
              help='Window skins only (ROM/0/14..21): apply this theme\'s edited PNGs to ALL skins and import each.')
@click.option('--ffxi', default=None, metavar='DIR',
              help='Override FFXI_DIR for this command (e.g. a pivot/override root).')
def simple_import_cmd(dat_path: str, output_dat: str | None, requested_format: str, all_themes: bool, ffxi: str | None):
    """Convert edited PNG files back to DDS and import them into a UI DAT.

    The working folder is derived automatically from the DAT path:
    `ROM/0/1.DAT -> exports/ui/0/1`.

    With --all-themes, the edited PNGs from this DAT's folder are copied onto every
    window-skin DAT (ROM/0/14..21) and imported into each in one go.
    """
    _apply_ffxi_dir(ffxi)
    dat_file = _resolve_dat_path(dat_path)

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
            _import_one(theme_dat, output_path_for(theme_dat), work, requested_format)
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
    _import_one(dat_file, out_file, _default_export_dir(dat_file), requested_format)

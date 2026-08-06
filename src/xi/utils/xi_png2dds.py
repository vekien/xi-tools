from pathlib import Path

import click

from xi.xi_config import TEXCONV_PATH
from xi.utils.xi_core import convert_png_to_dds


def _resolve_match_source(
    png_path: Path,
    output_dds_path: Path,
    requested_format: str,
    match_source: str | None,
) -> str | None:
    explicit = Path(match_source) if match_source else None
    if explicit:
        if not explicit.exists():
            raise click.ClickException(f'--match-source not found: {explicit}')
        if explicit.is_dir():
            candidate = explicit / f'{png_path.stem}.dds'
            if not candidate.exists():
                raise click.ClickException(
                    f'No matching source DDS for {png_path.name} in {explicit}'
                )
            return str(candidate)
        return str(explicit)

    # In the common extract/edit/rebuild flow, reusing the DDS we are about to
    # overwrite is the safest way to preserve the original compression format.
    if output_dds_path.exists():
        return str(output_dds_path)

    if requested_format == 'auto':
        raise click.ClickException(
            f'No existing DDS to overwrite for {png_path.name}. '
            'Pass --format or --match-source.'
        )

    return None


@click.command('png2dds')
@click.argument('input_file', metavar='INPUT_PNG')
@click.argument('output_file', metavar='OUTPUT_DDS')
@click.option(
    '--format',
    'requested_format',
    type=click.Choice(['auto', 'dxt1', 'dxt3', 'dxt5'], case_sensitive=False),
    default='auto',
    show_default=True,
    help='DDS compression format. auto picks from PNG alpha usage.',
)
@click.option(
    '--alpha-mode',
    type=click.Choice(['auto', 'opaque', 'cutout', 'sharp', 'smooth'], case_sensitive=False),
    default='auto',
    show_default=True,
    help='Override auto format choice: cutout->DXT1, sharp->DXT3, smooth->DXT5.',
)
@click.option(
    '--mipmaps',
    type=int,
    default=1,
    show_default=True,
    help='Mip level count passed to texconv. Use 1 for a single top-level image.',
)
@click.option('--gamma-convert', is_flag=True,
              help="Let texconv gamma-convert sRGB-tagged input to the linear DXT format. "
                   "Off by default so texel bytes pass through unchanged — on, an "
                   "editor-saved PNG re-imports visibly darker.")
@click.option('--texconv', default=TEXCONV_PATH, show_default=True,
              help='Path to texconv.exe or command name in PATH.')
@click.option('--match-source', default=None,
              help='Original DDS file to read and reuse its DXT1/DXT3/DXT5 format.')
def cmd(
    input_file: str,
    output_file: str,
    requested_format: str,
    alpha_mode: str,
    mipmaps: int,
    gamma_convert: bool,
    texconv: str,
    match_source: str | None,
):
    """Convert a PNG file to DDS using texconv.

    INPUT_PNG and OUTPUT_DDS can each be either a file path or a directory path.
    When both are directories, all `*.png` files in the input directory are converted
    to `.dds` files with matching basenames in the output directory.

    In directory mode, `--match-source` may be either a single DDS file path or a
    directory containing source DDS files with matching basenames.

    If `--match-source` is omitted and the output DDS already exists, the command
    automatically reuses that DDS as the format source. If no output DDS exists,
    pass either `--format` or `--match-source`.
    """
    input_path = Path(input_file)
    output_path = Path(output_file)

    if input_path.is_dir() or output_path.is_dir():
        if not input_path.is_dir() or not output_path.is_dir():
            raise click.ClickException('Directory mode requires both INPUT_PNG and OUTPUT_DDS to be directories.')

        png_files = sorted(p for p in input_path.iterdir() if p.is_file() and p.suffix.lower() == '.png')
        if not png_files:
            raise click.ClickException(f'No .png files found in {input_path}')

        converted = 0
        for png_path in png_files:
            dds_path = output_path / f'{png_path.stem}.dds'
            source_match = _resolve_match_source(
                png_path,
                dds_path,
                requested_format=requested_format.lower(),
                match_source=match_source,
            )

            try:
                analysis, dds_format, format_reason = convert_png_to_dds(
                    png_path,
                    dds_path,
                    requested_format=requested_format.lower(),
                    alpha_mode=alpha_mode.lower(),
                    mipmaps=mipmaps,
                    gamma_convert=gamma_convert,
                    texconv=texconv,
                    match_source=source_match,
                )
            except (FileNotFoundError, ValueError, RuntimeError, OSError) as e:
                raise click.ClickException(str(e))

            click.echo(
                f'{png_path.name} [{analysis.mode} -> {dds_format}] -> {dds_path.name}'
                f' ({format_reason})'
            )
            converted += 1

        click.echo(f'Converted {converted} PNG file(s) to {output_path}')
        return

    try:
        source_match = _resolve_match_source(
            input_path,
            output_path,
            requested_format=requested_format.lower(),
            match_source=match_source,
        )
        analysis, dds_format, format_reason = convert_png_to_dds(
            input_path,
            output_path,
            requested_format=requested_format.lower(),
            alpha_mode=alpha_mode.lower(),
            mipmaps=mipmaps,
            gamma_convert=gamma_convert,
            texconv=texconv,
            match_source=source_match,
        )
    except (FileNotFoundError, ValueError, RuntimeError, OSError) as e:
        raise click.ClickException(str(e))

    click.echo(f'Alpha analysis: has_alpha={analysis.has_alpha} mode={analysis.mode} source={analysis.source}')
    click.echo(f'Alpha detail: {analysis.detail}')
    click.echo(f'Format source: {format_reason}')
    click.echo(f'Chosen DDS format: {dds_format}')
    click.echo(f'Wrote DDS: {output_file}')

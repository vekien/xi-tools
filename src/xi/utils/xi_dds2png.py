from pathlib import Path

import click

from xi.utils.xi_core import convert_dds_to_png


@click.command('dds2png')
@click.argument('input_file', metavar='INPUT_DDS')
@click.argument('output_file', metavar='OUTPUT_PNG')
def cmd(input_file: str, output_file: str):
    """Convert a DXT1/DXT3/DXT5 DDS file to PNG.

    INPUT_DDS and OUTPUT_PNG can each be either a file path or a directory path.
    When both are directories, all `*.dds` files in the input directory are converted
    to `.png` files with matching basenames in the output directory.
    """
    input_path = Path(input_file)
    output_path = Path(output_file)

    if input_path.is_dir() or output_path.is_dir():
        if not input_path.is_dir() or not output_path.is_dir():
            raise click.ClickException('Directory mode requires both INPUT_DDS and OUTPUT_PNG to be directories.')

        dds_files = sorted(p for p in input_path.iterdir() if p.is_file() and p.suffix.lower() == '.dds')
        if not dds_files:
            raise click.ClickException(f'No .dds files found in {input_path}')

        converted = 0
        for dds_path in dds_files:
            png_path = output_path / f'{dds_path.stem}.png'
            try:
                info = convert_dds_to_png(dds_path, png_path)
            except (FileNotFoundError, ValueError, OSError) as e:
                raise click.ClickException(str(e))

            click.echo(f'{dds_path.name} [{info.fourcc} {info.width}x{info.height}] -> {png_path.name}')
            converted += 1

        click.echo(f'Converted {converted} DDS file(s) to {output_path}')
        return

    try:
        info = convert_dds_to_png(input_path, output_path)
    except (FileNotFoundError, ValueError, OSError) as e:
        raise click.ClickException(str(e))

    format_notes = {
        'DXT1': 'opaque or cutout alpha',
        'DXT3': 'explicit sharp alpha',
        'DXT5': 'interpolated smooth alpha',
    }
    click.echo(f'Detected DDS format: {info.fourcc}')
    if info.fourcc in format_notes:
        click.echo(f'Format note: {format_notes[info.fourcc]}')
    click.echo(f'Dimensions: {info.width}x{info.height}')
    click.echo(f'Wrote PNG: {output_file}')

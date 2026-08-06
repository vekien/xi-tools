"""xi ui import - patch extracted DDS textures back into a UI DAT file."""

from pathlib import Path

import click

from xi.xi_config import FFXI_DIR, ensure_base, output_path_for
from xi.ui.xi_core import compression_name, output_file_names, parse_dds, parse_textures, replace_texture


@click.command('import')
@click.argument('dat_path', metavar='DAT_FILE')
@click.argument('texture_dir', metavar='TEXTURE_DIR')
@click.option('--output-dat', default=None,
              help='Write to a different DAT path instead of overwriting DAT_FILE.')
def cmd(dat_path: str, texture_dir: str, output_dat: str | None):
    """Import edited .dds files from a folder back into a UI container DAT.

    DAT_FILE can be an absolute path or a path relative to the FFXI directory.
    TEXTURE_DIR should be a folder created by `xi ui extract`.

    Examples:

    \b
      uv run xi ui import ROM/119/50.DAT exports/ui/50
      uv run xi ui import ROM/119/51.DAT exports/ui/51 --output-dat ROM/119/51_mod.DAT
    """
    dat_file = Path(dat_path)
    if not dat_file.is_absolute():
        dat_file = Path(FFXI_DIR) / dat_file
    if not dat_file.exists():
        raise click.ClickException(f'DAT not found: {dat_file}')

    folder = Path(texture_dir)
    if not folder.exists() or not folder.is_dir():
        raise click.ClickException(f'Texture directory not found: {folder}')

    if output_dat:
        out_file = Path(output_dat)
        if not out_file.is_absolute():
            out_file = Path(FFXI_DIR) / out_file
    else:
        # Default: write the DAT back in place.
        out_file = output_path_for(dat_file)

    data = bytearray(dat_file.read_bytes())
    entries = parse_textures(data)
    if not entries:
        raise click.ClickException('No texture entries found in this DAT.')

    filenames = output_file_names(entries)
    replaced = 0
    missing = 0

    for entry, filename in reversed(list(zip(entries, filenames))):
        dds_path = folder / filename
        if not dds_path.exists():
            missing += 1
            continue

        try:
            dds = parse_dds(dds_path)
            replace_texture(data, entry, dds)
        except ValueError as e:
            raise click.ClickException(str(e))

        replaced += 1
        click.echo(
            f'  imported {filename} [{dds.fourcc.decode()} -> {compression_name(entry)}] '
            f'-> {entry.name or "unnamed"}'
        )

    if replaced == 0:
        raise click.ClickException(f'No matching .dds files found in {folder}')

    out_file.parent.mkdir(parents=True, exist_ok=True)
    if out_file.exists():
        ensure_base(out_file)   # keep the pristine bytes for undo
    out_file.write_bytes(data)

    click.echo()
    click.echo(f'Patched {replaced} texture(s) into {out_file}')
    if missing:
        click.echo(f'Left {missing} texture(s) unchanged because no matching .dds file was present')

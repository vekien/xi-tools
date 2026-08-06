"""xi ui extract — dump all textures from a UI DAT file as .dds files."""

import json
from pathlib import Path

import click

from xi.xi_config import FFXI_DIR, read_path_for
from xi.ui.xi_core import compression_name, output_file_names, parse_textures, write_dds


@click.command('export')
@click.argument('dat_path', metavar='DAT_FILE')
@click.option('--output-dir', '-o', default=None,
              help='Directory to write .dds files (default: exports/ui/<stem>/).')
@click.option('--json', 'as_json', is_flag=True,
              help='Also write a manifest JSON listing all extracted textures.')
@click.option('--list', 'list_only', is_flag=True,
              help='Print texture list without extracting files.')
def cmd(dat_path: str, output_dir: str | None, as_json: bool, list_only: bool):
    """Extract DXT1/DXT3/DXT5 textures from a UI container DAT (lobb / menu format).

    DAT_FILE can be an absolute path or a path relative to the FFXI directory.

    Examples:

    \b
      uv run xi ui extract ROM/119/50.DAT
      uv run xi ui extract ROM/119/51.DAT --output-dir exports/ui/51
      uv run xi ui extract ROM/119/51.DAT --list
    """
    # resolve path
    p = Path(dat_path)
    if not p.is_absolute():
        p = Path(FFXI_DIR) / p
    if not p.exists():
        raise click.ClickException(f'DAT not found: {p}')

    data = read_path_for(p).read_bytes()
    entries = parse_textures(data)

    if not entries:
        raise click.ClickException('No texture entries found in this DAT.')

    click.echo(f'Found {len(entries)} texture(s) in {p.name}')
    click.echo(f'File: {p}')
    click.echo()

    if list_only:
        _print_table(entries)
        return

    # output directory
    out_dir = Path(output_dir) if output_dir else Path('exports/ui') / p.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    _print_table(entries)
    click.echo()

    manifest = []
    for entry, dds_name in zip(entries, output_file_names(entries)):
        out_path = out_dir / dds_name

        write_dds(entry, out_path)
        click.echo(f'  wrote {out_path}')

        manifest.append({
            'name':         entry.name,
            'parent':       entry.parent,
            'file':         dds_name,
            'width':        entry.width,
            'height':       entry.height,
            'compression':  compression_name(entry),
            'data_size':    entry.data_size,
            'txd_offset':   hex(entry.txd_offset),
        })

    if as_json:
        json_path = out_dir / 'manifest.json'
        json_path.write_text(json.dumps(manifest, indent=2))
        click.echo()
        click.echo(f'  manifest -> {json_path}')

    click.echo()
    click.echo(f'Extracted {len(entries)} textures to {out_dir}/')


def _print_table(entries):
    click.echo(f'  {"#":>3}  {"name":<12} {"size":>9}  {"fmt":<14}  txd_offset')
    click.echo(f'  {"-"*3}  {"-"*12} {"-"*9}  {"-"*14}  {"-"*10}')
    for i, e in enumerate(entries):
        click.echo(
            f'  {i:>3}  {e.name:<12} {e.width:>4}x{e.height:<4}  '
            f'{compression_name(e):<14}  0x{e.txd_offset:06x}'
        )

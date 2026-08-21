"""xi ui gen-sheet - record a DAT's original sprite geometry into the layout reference sheet."""

import json
from pathlib import Path

import click

import xi.xi_config as _cfg
from xi.ui.xi_core import (LAYOUT_SHEET_PATH, _rects_by_owner, layout_sheet_key,
                           load_layout_sheet, parse_textures)


@click.command('gen-sheet')
@click.argument('dat_paths', metavar='DAT_FILE...', nargs=-1, required=True)
@click.option('--output', default=None, metavar='JSON',
              help=f'Sheet to update (default: {LAYOUT_SHEET_PATH}).')
@click.option('--key', default=None, metavar='ROM/N/M',
              help='Sheet key to store under, if the DAT is not inside a ROM/<n>/<m> tree.')
@click.option('--replace', is_flag=True,
              help='Drop the existing entry first instead of merging into it. Discards '
                   'hand-entered geometry for sprites the DAT does not contain.')
@click.option('--ffxi', default=None, metavar='DIR',
              help='Override FFXI_DIR for this command.')
def cmd(dat_paths: tuple, output: str | None, key: str | None, replace: bool, ffxi: str | None):
    """Record original texture sizes and sprite rects from pristine DATs.

    The sheet is what `ui tex import` scales sprite rects from when a texture is
    resized, so it must be built from **unmodified** DATs — a retail install, not an
    override that has already been edited.

    \b
      uv run xi ui gen-sheet ROM/119/50.DAT
      uv run xi ui gen-sheet ROM/119/50.DAT ROM/119/51.DAT ROM/280/15.DAT

    Entries merge by texture name, so geometry hand-added for a sprite no retail DAT
    ships (a private server's own logo) survives a regeneration from retail.
    """
    if ffxi:
        _cfg.FFXI_DIR = ffxi

    sheet_path = Path(output) if output else LAYOUT_SHEET_PATH
    sheet = dict(load_layout_sheet()) if sheet_path == LAYOUT_SHEET_PATH else {}
    if sheet_path != LAYOUT_SHEET_PATH and sheet_path.exists():
        try:
            sheet = json.loads(sheet_path.read_text(encoding='utf-8'))
        except ValueError as e:
            raise click.ClickException(f'{sheet_path} is not valid JSON: {e}')

    if key and len(dat_paths) > 1:
        raise click.ClickException('--key applies to a single DAT.')

    for raw in dat_paths:
        dat_file = Path(raw)
        if not dat_file.is_absolute():
            dat_file = Path(_cfg.FFXI_DIR) / dat_file
        if not dat_file.exists():
            raise click.ClickException(f'DAT not found: {dat_file}')

        sheet_key = key or layout_sheet_key(dat_file)
        if not sheet_key:
            raise click.ClickException(
                f'{dat_file} is not inside a ROM/<n>/<m> tree; pass --key to set one.')

        data = dat_file.read_bytes()
        textures = {e.name: [e.width, e.height] for e in parse_textures(data)}
        if not textures:
            raise click.ClickException(f'No texture entries found in {dat_file}.')
        rects = {name: [list(rect) for _off, _pre, rect in sprites]
                 for name, sprites in _rects_by_owner(data).items()}

        entry = {'textures': {}, 'rects': {}} if replace else sheet.get(sheet_key) or {}
        merged_tex = dict(entry.get('textures', {}))
        merged_rects = dict(entry.get('rects', {}))
        kept = len(set(merged_tex) - set(textures))
        merged_tex.update(textures)
        merged_rects.update(rects)
        sheet[sheet_key] = {'textures': merged_tex, 'rects': merged_rects}

        total = sum(len(v) for v in rects.values())
        note = f', kept {kept} pre-existing texture(s)' if kept else ''
        click.echo(f'  {sheet_key}: {len(textures)} texture(s), {total} sprite rect(s){note}')

    sheet_path.parent.mkdir(parents=True, exist_ok=True)
    sheet_path.write_text(json.dumps(sheet, indent=1, sort_keys=True), encoding='utf-8')
    click.echo(f'Wrote {sheet_path} ({sheet_path.stat().st_size:,} bytes, {len(sheet)} DAT(s))')

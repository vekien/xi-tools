#!/usr/bin/env python3
"""`xi tex export` — decode `0x20` textures to PNG (exports/tex/<rom>/)."""

from pathlib import Path
from typing import List

import click

from xi.tex.xi_core import (parse_sections, parse_texture, resolve_dat_path,
                           SECTION_TYPE_TEXTURE, fourcc, sanitize, matches, rom_rel)
from xi.utils.xi_core import write_png_rgba
from xi.xi_config import read_path_for


def export_textures(dat_path: Path, out_dir: Path, patterns=()) -> List[str]:
    data = bytes(read_path_for(dat_path).read_bytes())
    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    for s in parse_sections(bytearray(data)):
        if s.type_code != SECTION_TYPE_TEXTURE:
            continue
        img = parse_texture(data, s)
        if img is None:
            continue
        if patterns and not matches(img.name, fourcc(data, s.start), patterns):
            continue
        fn = sanitize(img.name) + ".png"
        write_png_rgba(out_dir / fn, img.width, img.height, img.rgba)
        written.append(fn)
    return written


@click.command()
@click.argument("dat_path")
@click.argument("names", nargs=-1)
@click.option("--out", "out_dir", type=click.Path(), default=None, help="Output dir (default: exports/tex/<rom>/).")
def export_cmd(dat_path, names, out_dir):
    """Decode textures to PNG (all, or only those matching NAME/prefix)."""
    try:
        resolved = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    out = Path(out_dir) if out_dir else Path("exports") / "tex" / rom_rel(Path(resolved))
    written = export_textures(resolved, out, names)
    if not written:
        raise click.ClickException("No matching textures found.")
    click.echo(f"Wrote {len(written)} PNG(s) -> {out}")
    for fn in written[:40]:
        click.echo(f"  {fn}")
    if len(written) > 40:
        click.echo(f"  … and {len(written) - 40} more")

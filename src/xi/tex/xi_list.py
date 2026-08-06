#!/usr/bin/env python3
"""`xi tex list` — list the `0x20` textures in a DAT (name, dimensions, size)."""

from pathlib import Path
from typing import Dict, List

import click

from xi.tex.xi_core import (parse_sections, parse_texture, resolve_dat_path,
                           SECTION_TYPE_TEXTURE, fourcc)
from xi.xi_config import read_path_for


def list_textures(dat_path: Path) -> List[Dict]:
    data = bytes(read_path_for(dat_path).read_bytes())
    out: List[Dict] = []
    for s in parse_sections(bytearray(data)):
        if s.type_code != SECTION_TYPE_TEXTURE:
            continue
        img = parse_texture(data, s)
        cc = fourcc(data, s.start)
        if img:
            out.append({"fourcc": cc, "name": img.name, "width": img.width,
                        "height": img.height, "size": s.size, "offset": s.start})
        else:
            out.append({"fourcc": cc, "name": None, "width": None,
                        "height": None, "size": s.size, "offset": s.start})
    return out


@click.command()
@click.argument("dat_path")
def list_cmd(dat_path):
    """List textures (0x20 sections) in a DAT: name, dimensions, size."""
    try:
        resolved = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    tx = list_textures(resolved)
    if not tx:
        click.echo("No textures (0x20) in this DAT.")
        return
    click.echo(f"{len(tx)} texture(s) in {resolved}:")
    for t in tx:
        dim = f"{t['width']}x{t['height']}" if t["width"] else "?"
        click.echo(f"  {t['fourcc']:6s} {(t['name'] or '').strip():18s} {dim:>10s}  size=0x{t['size']:x}")

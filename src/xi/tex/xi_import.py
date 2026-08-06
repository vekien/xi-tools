#!/usr/bin/env python3
"""`xi tex import` — re-encode edited PNGs back into a DAT, matched to their
`0x20` section by texture name. PNG -> DXT via texconv (DXT3 if alpha, else DXT1).
Keeps a `<dat>.base` backup."""

import tempfile
from pathlib import Path
from typing import Dict, List

import click

from xi.xi_config import editable_dat, output_path_for
from xi.tex.xi_core import (parse_sections, parse_texture, resolve_dat_path,
                           SECTION_TYPE_TEXTURE, fourcc, sanitize, rom_rel)
from xi.entity.mesh.xi_import import encode_png_to_texture_section


def import_textures(dat_path: Path, png_paths: List[Path]) -> Dict[str, List[str]]:
    """Re-encode PNGs back into their matching `0x20` sections (matched by name).
    Returns {'updated': [...], 'skipped': [...]}. Edits are written to the DAT
    in place, with a `<dat>.base` backup of the pristine bytes."""
    dat = editable_dat(dat_path, fresh=False)
    data = bytearray(dat.read_bytes())
    sections = parse_sections(data)
    # map sanitized texture name -> (section, fourcc, stored_name)
    by_key: Dict[str, tuple] = {}
    for s in sections:
        if s.type_code != SECTION_TYPE_TEXTURE:
            continue
        img = parse_texture(bytes(data), s)
        if img:
            by_key[sanitize(img.name)] = (s, fourcc(data, s.start), img.name)
    updated: List[str] = []
    skipped: List[str] = []
    replacements = []  # (start, old_size, new_bytes)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for png in png_paths:
            key = png.stem
            entry = by_key.get(key)
            if entry is None:
                skipped.append(f"{png.name} (no texture named '{key}')")
                continue
            s, cc, stored = entry
            new_sec = encode_png_to_texture_section(cc, stored, png, tmp)
            if new_sec is None:
                skipped.append(f"{png.name} (encode failed — is texconv available?)")
                continue
            replacements.append((s.start, s.size, new_sec))
            updated.append(key)
        for start, old_size, new_sec in sorted(replacements, key=lambda x: -x[0]):
            data = data[:start] + bytearray(new_sec) + data[start + old_size:]
        if updated:
            dat.write_bytes(data)
    return {"updated": updated, "skipped": skipped}


@click.command()
@click.argument("dat_path")
@click.argument("pngs", nargs=-1, type=click.Path(exists=True))
@click.option("--dir", "png_dir", type=click.Path(exists=True), default=None, help="Import every *.png in this dir (default: exports/tex/<rom>/).")
def import_cmd(dat_path, pngs, png_dir):
    """Re-encode edited PNG(s) back into the DAT, matched to their texture by name.

    With no PNG args, imports every PNG in exports/tex/<rom>/ (or --dir).
    """
    try:
        resolved = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    paths = [Path(p) for p in pngs]
    if not paths:
        d = Path(png_dir) if png_dir else Path("exports") / "tex" / rom_rel(Path(resolved))
        if not d.is_dir():
            raise click.ClickException(f"No PNGs given and {d} not found. Run `xi tex export` first.")
        paths = sorted(d.glob("*.png"))
    if not paths:
        raise click.ClickException("No PNGs to import.")
    res = import_textures(resolved, paths)
    if res["updated"]:
        click.echo(f"Updated {len(res['updated'])} texture(s) in {resolved}: {', '.join(res['updated'])}")
        click.echo(f"Wrote: {output_path_for(resolved)}")
    for s in res["skipped"]:
        click.echo(click.style(f"  skipped {s}", fg="yellow"))
    if not res["updated"]:
        raise click.ClickException("Nothing updated.")

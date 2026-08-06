#!/usr/bin/env python3
"""`xi fx list` — list every `0x05` effect with a label, placed mesh/texture, position."""

from pathlib import Path
from typing import Dict, List

import click

from xi.fx.xi_core import (parse_sections, resolve_dat_path, classify, _load_library,
                          EFFECT_TYPE, _fourcc, _mesh_fourccs, _texture_fourccs,
                          _effect_target, _effect_texture, _read_pos_at)
from xi.xi_config import read_path_for


def list_effects(dat_path: Path) -> List[Dict]:
    """Every `0x05` effect in the DAT: name, offset, size, placed-mesh, position."""
    data = bytearray(read_path_for(dat_path).read_bytes())
    sections = parse_sections(data)
    mesh_ccs = _mesh_fourccs(data, sections)
    tex_ccs = _texture_fourccs(data, sections)
    out: List[Dict] = []
    for s in sections:
        if s.type_code != EFFECT_TYPE:
            continue
        body = bytes(data[s.start:s.start + s.size])
        mesh, pos = _effect_target(body, mesh_ccs)
        texture = _effect_texture(body, tex_ccs)
        if pos is None and texture:                  # mesh-less sprite: pos after texture ref
            pos = _read_pos_at(body, tex_ccs)
        out.append({"name": _fourcc(data, s.start), "offset": s.start, "size": s.size,
                    "mesh": mesh, "pos": pos, "texture": texture})
    return out


@click.command()
@click.argument("dat_path")
def list_cmd(dat_path):
    """List effect (0x05) generators in a DAT: name, size, placed mesh + position."""
    try:
        resolved = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    fx = list_effects(resolved)
    if not fx:
        click.echo("No effects (0x05) in this DAT.")
        return
    lib = _load_library()
    click.echo(f"{len(fx)} effect(s) in {resolved}:")
    for e in fx:
        entry = classify(e["name"], e["mesh"], e.get("texture"), lib)
        if entry:
            tag = entry["label"] + ("" if entry.get("verified") else " ?")
        else:
            tag = "(unidentified)"
        ref = e["mesh"] or (f"tex:{e['texture']}" if e.get("texture") else None)
        loc = f"  -> {ref} @ {e['pos']}" if (ref and e["pos"]) else (f"  -> {ref}" if ref else "")
        click.echo(f"  {e['name']:8s} size=0x{e['size']:04x}  {tag:24s}{loc}")

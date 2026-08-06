#!/usr/bin/env python3
"""`xi fx export` — export an effect's referenced 3D mesh (+ materials/texture)
and its decoded params as a bundle: `<effect>.glb` + `<texture>.png` + `<effect>.json`.

The mesh is the geometry the effect places (e.g. the fountain `tki` -> `sibj`
splash quad, a lamp `lt` -> `ligh` glow billboard). Mesh-less sprite effects
(e.g. fire, which billboards a texture directly) export the texture + JSON only.
"""

import json
from pathlib import Path
from typing import Dict, Optional

import click

from xi.fx.xi_core import (parse_sections, resolve_dat_path, EFFECT_TYPE, _fourcc,
                             _mesh_fourccs, _texture_fourccs, _effect_target,
                             _effect_texture, _rom_rel)
from xi.fx.xi_dump import dump_effects
from xi.zone.xi_export import parse_zone, build_glb, parse_zone_mesh_section
from xi.zone.xi_decrypt import load_key_tables, decrypt_zone_mesh
from xi.entity.mesh.xi_export import parse_texture
from xi.utils.xi_core import write_png_rgba
from xi.xi_config import FFXI_DIR, read_path_for


def _mesh_name_for_fourcc(data: bytearray, sections, cc: str) -> Optional[str]:
    """Resolve a mesh's section FourCC (as referenced by an effect) to its mesh
    NAME (the str@0x10 field that `parse_zone`/`build_glb` key on)."""
    t1, t2 = load_key_tables(Path(FFXI_DIR) / "FFXiMain.dll")
    for s in sections:
        if s.type_code == 0x2E and _fourcc(data, s.start) == cc:
            buf = bytearray(data)
            decrypt_zone_mesh(buf, s.data_start, t1, t2)
            name, _prims = parse_zone_mesh_section(buf, s)
            return name
    return None


def export_effect(dat_path: Path, effect_name: str, out_dir: Path) -> Dict:
    """Export one effect's mesh + textures + params into ``out_dir``. Returns a
    summary dict (mesh, texture, files)."""
    dat = read_path_for(dat_path)
    data = bytearray(dat.read_bytes())
    sections = parse_sections(data)
    eff = next((s for s in sections if s.type_code == EFFECT_TYPE and _fourcc(data, s.start) == effect_name), None)
    if eff is None:
        raise ValueError(f"No effect named '{effect_name}' in this DAT.")
    body = bytes(data[eff.start:eff.start + eff.size])
    mesh_ccs = _mesh_fourccs(data, sections)
    tex_ccs = _texture_fourccs(data, sections)
    mesh_cc, _pos = _effect_target(body, mesh_ccs)
    texture_cc = _effect_texture(body, tex_ccs)

    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    mesh_name = None
    glb = None

    if mesh_cc:
        mesh_name = _mesh_name_for_fourcc(data, sections, mesh_cc)
        meshes_by_name, _placements, textures = parse_zone(dat)
        if mesh_name and mesh_name in meshes_by_name:
            # single mesh, no placement (lands at origin), with its texture(s) embedded
            paths = build_glb(Path(effect_name), out_dir, {mesh_name: meshes_by_name[mesh_name]},
                              [], textures)
            files.extend(paths)
            glb = paths[0]
    elif texture_cc:
        # mesh-less sprite effect: export the referenced texture as PNG
        for s in sections:
            if s.type_code == 0x20 and _fourcc(data, s.start) == texture_cc:
                img = parse_texture(bytes(data), s)
                if img:
                    png = out_dir / (texture_cc + ".png")
                    write_png_rgba(png, img.width, img.height, img.rgba)
                    files.append(png)
                break

    # params JSON for this effect
    entry = next((e for e in dump_effects(dat, include_opcodes=True)["effects"] if e["name"] == effect_name), None)
    jpath = out_dir / f"{effect_name}.json"
    jpath.write_text(json.dumps(entry, indent=2), encoding="utf-8")
    files.append(jpath)

    return {"effect": effect_name, "mesh": mesh_name, "mesh_fourcc": mesh_cc,
            "texture": texture_cc, "glb": str(glb) if glb else None, "files": files}


@click.command()
@click.argument("dat_path")
@click.argument("effect_name", required=False, default=None)
@click.option("--out", "out_dir", type=click.Path(), default=None, help="Output dir (default: exports/fx/<rom>/<effect>/).")
def export_cmd(dat_path, effect_name, out_dir):
    """Export an effect's 3D mesh (+ materials/texture) and params as a bundle.

    Omit EFFECT_NAME to export all effects in the DAT.

    Example:  xi fx export ROM/1/41 tki5   ->  exports/fx/rom/1/41/tki5/{tki5.glb, *.png, tki5.json}
    """
    try:
        resolved = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e))

    rom_rel = _rom_rel(Path(resolved))

    if effect_name is None:
        dump = dump_effects(resolved)
        all_effects = [e["name"] for e in dump["effects"]]
        if not all_effects and not dump.get("schedules"):
            raise click.ClickException("No effects or schedules found in this DAT.")
        base_out = Path(out_dir) if out_dir else Path("exports") / "fx" / rom_rel
        base_out.mkdir(parents=True, exist_ok=True)
        click.echo(f"Exporting {len(all_effects)} effect(s) from {rom_rel} -> {base_out}")
        total_files = 0
        for name in all_effects:
            try:
                res = export_effect(resolved, name, base_out)
            except ValueError as e:
                click.echo(f"  {name}: skipped ({e})", err=True)
                continue
            total_files += len(res["files"])
            if res["glb"]:
                click.echo(f"  {name}: mesh '{res['mesh']}' -> {res['glb']}")
            elif res["texture"]:
                click.echo(f"  {name}: mesh-less (texture '{res['texture']}')")
            else:
                click.echo(f"  {name}: no mesh/texture — JSON only")
        if dump.get("schedules"):
            sched_path = base_out / "schedules.json"
            sched_path.write_text(json.dumps(dump["schedules"], indent=2), encoding="utf-8")
            total_files += 1
            click.echo(f"  schedules.json: {len(dump['schedules'])} routine(s)")
        click.echo(f"Done. {total_files} file(s) written.")
        return

    out = Path(out_dir) if out_dir else Path("exports") / "fx" / rom_rel / effect_name
    try:
        res = export_effect(resolved, effect_name, out)
    except ValueError as e:
        raise click.ClickException(str(e))
    if res["glb"]:
        click.echo(f"Exported {effect_name}: mesh '{res['mesh']}' -> {res['glb']}")
    elif res["texture"]:
        click.echo(f"Exported {effect_name}: mesh-less (texture '{res['texture']}') — wrote texture + JSON")
    else:
        click.echo(f"Exported {effect_name}: no mesh/texture reference — wrote JSON only")
    click.echo(f"  {len(res['files'])} file(s) -> {out}")

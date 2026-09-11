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
from xi.fx.xi_particle_mesh import particle_meshes
from xi.zone.xi_export import (parse_zone, build_glb, parse_zone_mesh_section,
                               Placement)
from xi.zone.xi_decrypt import load_key_tables, decrypt_zone_mesh
from xi.entity.mesh.xi_export import parse_texture, parse_textures
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

    # Particle geometry (`0x1F`/`0x21`) first: it needs no zone decryption and it's
    # what every non-zone effect actually draws. Only fall through to the `0x2E`
    # zone-mesh path when the reference isn't one of those.
    pmesh = particle_meshes(data, sections).get(mesh_cc) if mesh_cc else None
    if pmesh is not None:
        mesh_name = pmesh.fourcc.strip() or pmesh.fourcc
        textures = parse_textures(data, sections)
        paths = build_glb(Path(effect_name), out_dir, {mesh_name: pmesh.prims},
                          [], textures)
        files.extend(paths)
        glb = paths[0]
    elif mesh_cc:
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


def _sane_scale(values) -> tuple:
    """A generator's `0x0F` Scale, or (1,1,1). The tag is located by byte search,
    so a false positive can land anywhere — reject anything that isn't a plausible
    scale rather than exploding the assembled model."""
    if not values or len(values) != 3:
        return (1.0, 1.0, 1.0)
    if not all(v == v and 0.0 < v <= 100.0 for v in values):
        return (1.0, 1.0, 1.0)
    return tuple(float(v) for v in values)


def assemble_effect(dat_path: Path, out_dir: Path, all_layers: bool = False) -> Dict:
    """Every particle mesh this DAT's generators draw, in ONE GLB, each placed at
    its generator's local position and scale.

    This is the whole visible object for an effect-only entity — a Home Point is
    seven generator draws over four meshes, and no single one of them looks like
    anything on its own.

    Two filters make the default the object *at rest*:

    * **autorun.** An ambient entity splits its generators between an idle routine
      and a triggered one, and the `genFlags` autorun bit (0x10) separates them
      exactly — every generator in the Home Point's `aper` idle routine has it,
      every one in its `bind` activation routine does not. When a DAT has autorun
      generators, only those are drawn; a spell DAT has none (it is fired by a
      routine) so everything is drawn instead.
    * **`0x21` sprite cards** are the quad a sprite-sheet particle billboards, one
      per emitted particle — a static copy at the origin is a flat square through
      the middle of the model, not a layer of it.

    ``all_layers`` disables both.
    """
    dat = read_path_for(dat_path)
    data = bytearray(dat.read_bytes())
    sections = parse_sections(data)
    pmeshes = particle_meshes(data, sections)
    if not pmeshes:
        raise ValueError("No 0x1F/0x21 particle meshes in this DAT.")
    textures = parse_textures(data, sections)

    effects = dump_effects(dat)["effects"]
    autorun = [e for e in effects if (e.get("params") or {}).get("autorun")]
    chosen = effects if all_layers or not autorun else autorun

    meshes_by_name: Dict[str, list] = {}
    placements: list = []
    seen = set()
    for eff in chosen:
        pmesh = pmeshes.get(eff["mesh"]) if eff.get("mesh") else None
        if pmesh is None:
            continue
        if pmesh.section_type == 0x21 and not all_layers:
            continue
        name = pmesh.fourcc.strip() or pmesh.fourcc
        meshes_by_name.setdefault(name, pmesh.prims)
        params = eff.get("params") or {}
        pos = tuple(eff["position"]) if eff.get("position") else (0.0, 0.0, 0.0)
        scale = _sane_scale(params.get("scale"))
        # sec2 0x09 Rotation. Two generators often differ ONLY here — a Home
        # Point's nak0/nak1 are one mesh at 30 deg and 150 deg, the crossed
        # planes inside the crystal — so it has to be in the dedupe key as well
        # as the placement, or half the object silently disappears.
        rot = tuple(params.get("rotation") or (0.0, 0.0, 0.0))
        key = (name, pos, rot, scale)
        if key in seen:            # two generators, same mesh at the same transform
            continue
        seen.add(key)
        placements.append(Placement(mesh_id=name, position=pos, rotation=rot, scale=scale))

    if not placements:
        raise ValueError("No generator in this DAT references a particle mesh.")

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _rom_rel(Path(dat)).replace("/", "_")
    files = build_glb(Path(stem), out_dir, meshes_by_name, placements, textures)
    return {"glb": str(files[0]), "meshes": sorted(meshes_by_name),
            "layers": len(placements), "files": files,
            "filter": "all" if (all_layers or not autorun) else "autorun"}


@click.command()
@click.argument("dat_path")
@click.argument("effect_name", required=False, default=None)
@click.option("--assemble", is_flag=True,
              help="Combine every particle mesh the DAT's generators draw into one GLB "
                   "(the whole effect-only entity, e.g. a Home Point crystal).")
@click.option("--all-layers", "all_layers", is_flag=True,
              help="With --assemble: include routine-triggered generators and 0x21 sprite "
                   "cards too, not just the autorun idle layers.")
@click.option("--out", "out_dir", type=click.Path(), default=None, help="Output dir (default: exports/fx/<rom>/<effect>/).")
def export_cmd(dat_path, effect_name, assemble, all_layers, out_dir):
    """Export an effect's 3D mesh (+ materials/texture) and params as a bundle.

    Omit EFFECT_NAME to export all effects in the DAT.

    Example:  xi fx export ROM/1/41 tki5   ->  exports/fx/rom/1/41/tki5/{tki5.glb, *.png, tki5.json}
    Example:  xi fx export ROM/3/25 --assemble  ->  the whole Home Point crystal in one GLB
    """
    try:
        resolved = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e))

    rom_rel = _rom_rel(Path(resolved))

    if assemble:
        out = Path(out_dir) if out_dir else Path("exports") / "fx" / rom_rel
        try:
            res = assemble_effect(resolved, out, all_layers=all_layers)
        except ValueError as e:
            raise click.ClickException(str(e))
        click.echo(f"Assembled {res['layers']} layer(s) over {len(res['meshes'])} mesh(es) "
                   f"({', '.join(res['meshes'])}) [{res['filter']}] -> {res['glb']}")
        click.echo(f"  {len(res['files'])} file(s) -> {out}")
        return

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

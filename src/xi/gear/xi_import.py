#!/usr/bin/env python3
"""`xi gear import` — import an edited GLB back into a gear model DAT.

Resolves the race/slot/model_id to its DAT file via the gear tables, then
delegates to the entity mesh import pipeline — the binary format is identical.
GLB only (no FBX); convert to GLB in Blender first if needed.
"""

from pathlib import Path

import click

from xi.gear.xi_export import (resolve_gear_target, race_skeleton_dat,
                                 default_gear_output_dir, legacy_gear_output_dir)
from xi.entity.mesh.xi_import import import_mesh
from xi.xi_config import output_path_for


def default_gear_model_path(race: str, slot: str, dat_path: Path) -> Path | None:
    # New flat layout first, then the legacy <race>/<slot> path so models
    # exported before the flatten are still auto-found.
    for out_dir in (default_gear_output_dir(dat_path),
                    legacy_gear_output_dir(race, slot, dat_path)):
        for ext in (".glb", ".gltf", ".fbx"):
            candidate = out_dir / f"{dat_path.stem}{ext}"
            if candidate.is_file():
                return candidate
    return None


@click.command("import")
@click.argument("spec1", metavar="RACE|DAT|FILE_ID")
@click.argument("spec2", required=False, metavar="SLOT|GLB_PATH")
@click.argument("spec3", required=False, metavar="[MODEL_ID]")
@click.argument("spec4", required=False, metavar="[GLB_PATH]")
@click.option("--mesh-name", default=None,
              help="Override the target mesh section name (default: first mesh in the DAT).")
@click.option("--double-sided/--single-sided", default=True, show_default=True,
              help="Render faces from both sides (default: double-sided, like the original).")
@click.option("--scale", "manual_scale", type=float, default=1.0, show_default=True,
              help="Uniform scale factor applied to the imported geometry.")
@click.option("--rotate-y", "rotate_y_deg", type=float, default=0.0, show_default=True,
              help="Rotate the mesh around the Y axis before import (degrees).")
@click.option("--flip-yz/--no-flip-yz", "flip_yz", default=None,
              help="Flip Y/Z axes. Auto-detected from asset.generator (Blender=flip, C4D=no flip).")
@click.option("--tex", "tex_only", is_flag=True, default=False,
              help="Only import textures: re-encode the GLB's images over the DAT's matching "
                   "texture sections. Geometry is untouched (a prior mesh import survives).")
@click.option("--tex-local", "tex_local", is_flag=True, default=False,
              help="Texture-only import sourcing images from files next to the GLB "
                   "(matched by name; the GLB is only a naming reference). Implies --tex.")
def cmd(spec1, spec2, spec3, spec4, mesh_name, double_sided, manual_scale, rotate_y_deg, flip_yz,
        tex_only, tex_local):
    """Import an edited GLB into a gear model DAT (GLB only — convert FBX in Blender first).

    Resolves the gear DAT, replaces all mesh sections with the geometry from the
    GLB, and writes the DAT back in place under FFXI_DIR (pristine bytes kept
    in a `<dat>.base` backup). Identify the model explicitly
    (RACE SLOT MODEL_ID) or by a DAT path / file_id (race auto-detected); the GLB
    path always comes last.

    With --tex, only the DAT's texture sections are replaced (geometry left
    alone); --tex-local additionally reads the images from files on disk next to
    the GLB instead of the copies embedded in it.

    \b
    RACE / SLOT / MODEL_ID   — same as `xi gear export`
    DAT / FILE_ID            — e.g. ROM/33/17 or 10578 (race/slot/model auto-detected)
    GLB_PATH                 — edited .glb file (final argument)

    Examples:

    \b
      xi gear import HumeMale body 0 edited_body.glb
      xi gear import Mithra hands 5 hands_edit.glb --no-double-sided
      xi gear import ROM/33/17 edited_body.glb
      xi gear import ROM/33/17 edited_body.glb --tex-local
    """
    # The GLB path is always the final positional; the 1 or 3 tokens before it
    # identify the gear model (a single DAT/file_id, or a RACE SLOT MODEL_ID triple).
    parts = [p for p in (spec1, spec2, spec3, spec4) if p is not None]
    if not parts:
        raise click.ClickException(
            "Usage: gear import (RACE SLOT MODEL_ID | DAT | FILE_ID) [GLB_PATH]")

    # A model-file suffix (.glb/.gltf/.fbx) on the last token is the reliable
    # signal that it's the GLB argument — a DAT path (ROM/33/17) or file_id
    # (10578) never carries one. Key off the suffix, not existence: if we keyed
    # off existence, a mistyped/relative GLB path would silently fall through and
    # be miscounted as a model token, producing a misleading usage error instead
    # of "GLB not found".
    glb_path: Path | None = None
    if parts and Path(parts[-1]).suffix.lower() in (".glb", ".gltf", ".fbx"):
        glb_path = Path(parts[-1])
        parts = parts[:-1]
        if not glb_path.exists():
            raise click.ClickException(f"GLB not found: {glb_path}")

    if len(parts) == 1:
        race, slot, model_id = parts[0], None, None
    elif len(parts) == 3:
        try:
            model_id = int(parts[2])
        except ValueError:
            raise click.ClickException(f"MODEL_ID must be an integer, got '{parts[2]}'")
        race, slot = parts[0], parts[1]
    elif len(parts) == 0:
        raise click.ClickException(
            "Usage: gear import (RACE SLOT MODEL_ID | DAT | FILE_ID) [GLB_PATH]")
    else:
        raise click.ClickException(
            "Identify the gear model as 'RACE SLOT MODEL_ID' or a single DAT path / file_id.")

    try:
        dat_path, race, slot, model_id = resolve_gear_target(race, slot, model_id)
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e))

    if glb_path is None:
        glb_path = default_gear_model_path(race, slot, dat_path)
        if glb_path is None:
            raise click.ClickException(
                f"No GLB found in {default_gear_output_dir(dat_path)}. "
                "Export first or pass a GLB path.")
        click.echo(f"Using model:    {glb_path}")

    try:
        _out, _v, _t, _ntex, tex_map = import_mesh(dat_path, glb_path, mesh_name=mesh_name,
                    double_sided=double_sided, manual_scale=manual_scale,
                    rotate_y_deg=rotate_y_deg, skeleton_dat=race_skeleton_dat(race),
                    flip_yz=flip_yz, tex_only=tex_only, tex_local=tex_local)
    except (ValueError, FileNotFoundError, RuntimeError, OSError) as e:
        raise click.ClickException(str(e))

    click.echo(f"Resolved: {race} / {slot} / model_id {model_id}  ({dat_path.name})")
    if tex_only or tex_local:
        click.echo(f"Wrote: {output_path_for(dat_path)}")
        click.echo(f"Textures replaced: {_ntex}")
        for tex_name, source in tex_map:
            click.echo(f"  {tex_name:16s} <- {source}")
        return
    click.echo(f"Imported: {glb_path.name} → {dat_path.name}  ({_v} verts, {_t} tris)")
    click.echo(f"Wrote: {output_path_for(dat_path)}")
    click.echo("Texture map:")
    for tex_name, tri_count in tex_map:
        click.echo(f"  {tex_name or '(untextured)':30s}  {tri_count} tris")

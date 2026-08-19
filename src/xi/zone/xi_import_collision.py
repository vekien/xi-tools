"""``xi zone import-collision`` — bake an authored OBJ as a zone's collision.

Collision-only counterpart to ``zone import --add-collision``: that one always
appends and is bundled with the GLB/texture import path. This replaces the zone's
collision outright by default, which is what you want after authoring a hull in a
DCC — otherwise you stack a new hull on top of the old one.

The OBJ is expected in the same frame as ``zone export --collision`` writes it:
FFXI world with X and Y negated, ``(-x, -y, z)``.

Material names drive the per-triangle fields: ``col_floor_<terrain>`` and
``col_wall_<terrain>`` (e.g. ``col_floor_stone``). Faces with any other material
fall back to ``--wall/--floor`` and ``--terrain``. Many DCCs flatten materials on
export, so check the summary this prints.
"""

from pathlib import Path

import click

from xi.zone.xi_collision import (parse_collision_obj, replace_zone_collision,
                                  add_collision_from_obj, resolve_collision_obj)
from xi.zone.xi_zonedef import SECTION_TYPE_ZONE_DEF



def _report_budget(dat: Path, n_tris: int) -> None:
    """Print how close the written 0x1C section is to the client's 19-bit size limit.

    Exceeding MAX_SECTION_BYTES is a hard failure (SectionTooLargeError), so knowing
    the headroom before the next iteration is worth more than finding out on the
    attempt that fails.
    """
    from xi.common.xi_section import MAX_SECTION_BYTES
    from xi.zone.xi_export import parse_sections
    data = bytearray(Path(dat).read_bytes())
    sec = next((s for s in parse_sections(data)
                if s.type_code == SECTION_TYPE_ZONE_DEF), None)
    if sec is None:
        return
    pct = 100.0 * sec.size / MAX_SECTION_BYTES
    colour = "red" if pct >= 90 else "yellow" if pct >= 75 else "green"
    click.echo(f"  0x1C section: {sec.size:,} of {MAX_SECTION_BYTES:,} bytes  "
               + click.style(f"({pct:.1f}%)", fg=colour))
    if n_tris > 0:
        per = sec.size / n_tris
        room = int((MAX_SECTION_BYTES - sec.size) / per) if per > 0 else 0
        click.echo(f"  headroom: ~{room:,} more triangles at {per:.0f} bytes/triangle")

@click.command("import-collision")
@click.argument("dat_path")
@click.argument("obj_path")
@click.option("--replace/--append", default=True, show_default=True,
              help="Replace the zone's collision entirely, or add to what is there. "
                   "Replace is the default: appending an authored hull to the original "
                   "leaves both, which is rarely what you want.")
@click.option("--wall/--floor", "default_wall", default=True, show_default=True,
              help="Flag for faces whose material is not col_wall_*/col_floor_*.")
@click.option("--terrain", type=int, default=0, show_default=True,
              help="Terrain id (0-10) for faces without a col_*_<terrain> material. "
                   "Picks footstep sound/effects.")
@click.option("--scale", type=float, default=1.0, show_default=True,
              help="Scale the OBJ coords (fix a DCC unit mismatch, e.g. 0.01 if it "
                   "exported in centimetres).")
@click.option("--camera-block", is_flag=True,
              help="Make the collision block the camera too. Default: the camera passes "
                   "through, like FFXI's invisible blockers.")
@click.option("--reset", "do_reset", is_flag=True,
              help="Restore the DAT from its .base first, discarding ALL prior edits "
                   "(placements, VFX, everything), then bake this collision. Without it "
                   "only the collision is replaced and other edits are kept.")
@click.option("--dry-run", is_flag=True, help="Parse and report without writing.")
def cmd(dat_path: str, obj_path: str, replace: bool, default_wall: bool,
        terrain: int, scale: float, camera_block: bool, do_reset: bool, dry_run: bool):
    """Bake an authored OBJ as a zone's collision, replacing what is there.

    \b
    Examples:
      xi zone import-collision ROM10/1/4 4.collision.obj
      xi zone import-collision ROM10/1/4 hull.obj --floor --terrain 1
      xi zone import-collision ROM10/1/4 extra.obj --append --camera-block
      xi zone import-collision ROM10/1/4 hull.obj --reset --floor --camera-block
    """
    from xi.entity.mesh.xi_export import resolve_dat_path
    try:
        dat = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e)) from e
    obj = resolve_collision_obj(dat, obj_path)
    if not Path(obj).is_file():
        raise click.ClickException(f"collision OBJ not found: {obj}")

    tris = parse_collision_obj(Path(obj), default_wall=default_wall,
                               default_terrain=terrain, scale=scale)
    if not tris:
        raise click.ClickException(f"{Path(obj).name}: no triangles parsed — wrong file, "
                                   f"or faces reference vertices it does not define?")

    walls = sum(1 for t in tris if t.hit_wall)
    xs = [v[0] for t in tris for v in (t.v0, t.v1, t.v2)]
    ys = [v[1] for t in tris for v in (t.v0, t.v1, t.v2)]
    zs = [v[2] for t in tris for v in (t.v0, t.v1, t.v2)]
    click.echo(f"  {Path(obj).name}: {len(tris):,} triangles  "
               f"({walls:,} wall / {len(tris) - walls:,} floor)")
    click.echo(f"  extent  X [{min(xs):.0f}, {max(xs):.0f}]  "
               f"Y [{min(ys):.0f}, {max(ys):.0f}]  Z [{min(zs):.0f}, {max(zs):.0f}]  (FFXI world)")
    if walls == len(tris) and default_wall:
        click.echo(click.style(
            "  note: every face used the fallback flag — the OBJ has no col_wall_*/"
            "col_floor_* materials (many DCCs drop them on export).", fg="cyan"))

    if dry_run:
        if do_reset:
            click.echo("  would reset the DAT from .base first")
        click.echo(click.style('\n  Dry run — nothing written.', fg="cyan"))
        return

    if do_reset:
        from xi.zone.xi_reset import reset_dat
        click.echo("  " + reset_dat(dat))

    if replace:
        out, removed, added = replace_zone_collision(dat, tris,
                                                     camera_transparent=not camera_block)
        click.echo(f"\n  replaced collision in {Path(out).name}: "
                   f"{removed:,} source meshes removed, {added:,} triangles baked")
        _report_budget(out, added)
    else:
        out, _before, added, _names = add_collision_from_obj(
            dat, Path(obj), default_wall=default_wall, default_terrain=terrain,
            scale=scale, camera_transparent=not camera_block)
        click.echo(f"\n  appended {added:,} triangles to {Path(out).name}")
        _report_budget(out, added)
    click.echo(click.style("✓ Restart the game client to see it.", fg="green"))

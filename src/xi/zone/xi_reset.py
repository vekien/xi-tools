#!/usr/bin/env python3
"""`xi zone reset` — reset a zone DAT back to the pristine original.

Restores the DAT from the <dat>.base backup created on first edit.
"""

from pathlib import Path

import click

from xi.entity.mesh.xi_export import resolve_dat_path


def reset_dat(dat_path: Path) -> str:
    """Reset a DAT to pristine (restore from its .base backup). Returns a short
    status message."""
    base = dat_path.with_name(dat_path.name + ".base")
    if not base.exists():
        return f"No .base backup found for {dat_path.name} — nothing to reset."
    import shutil
    shutil.copy2(base, dat_path)
    return f"Restored {dat_path.name} from {base.name}"


@click.command("reset")
@click.argument("dat_path")
@click.option("--dry-run", is_flag=True, help="Show what would be reset without doing it.")
@click.option("--clear-collision", is_flag=True,
              help="After resetting, also wipe the zone's existing collision geometry "
                   "(keeps the grid + per-object cull transforms) so only collision you "
                   "add afterward remains. Use to REPLACE a zone's collision, not append.")
def cmd(dat_path, dry_run, clear_collision):
    """Reset a zone DAT back to the pristine original.

    Removes any accumulated edits (apply-changes, import-object, etc.) by
    restoring the DAT from its <dat>.base backup.

    With --clear-collision, the reset zone is also stripped of all its
    existing collision meshes (grid + cull transforms preserved), so a
    subsequent --add-collision / editor bake becomes the ONLY collision.

    Examples:

    \b
      xi zone reset ROM/1/41
      xi zone reset ROM/1/41 --dry-run
      xi zone reset ROM/1/41 --clear-collision
    """
    try:
        resolved = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e))

    if dry_run:
        base = resolved.with_name(resolved.name + ".base")
        click.echo(f"Would restore {resolved.name} from {base}")
        if clear_collision:
            click.echo("  → then wipe all existing collision geometry (grid kept)")
        return

    msg = reset_dat(resolved)
    click.echo(msg)
    if clear_collision:
        from xi.zone.xi_collision import clear_zone_collision
        _out, n = clear_zone_collision(resolved)
        click.echo(f"Cleared {n} collision mesh(es) — only collision added after this remains.")

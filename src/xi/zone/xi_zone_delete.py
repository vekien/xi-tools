"""``xi zone delete <zone_id>`` — remove a custom zone (ID ≥ 400).

Deletes all four per-zone client DATs (model + event/dialog/npc) from
FFXI_DIR/ROM10, zeros their FTABLE10/VTABLE10 + base-table entries, and
cleans up the server database (zone_settings + zone_weather) — auto-applied via
the XI_DB_* / .env creds, or printed for a manual run if the DB is unreachable.
"""

from __future__ import annotations

import struct
from pathlib import Path

import click

from xi.ftable.xi_core import load_tables
from xi.xi_config import (FFXI_DIR, DB_AUTOAPPLY, DB_HOST, DB_NAME,
                            DB_USER, db_creds)
from xi.zone.xi_inject import (
    zone_model_file_id, zone_event_file_id, zone_dialog_file_id, zone_npc_file_id,
    unregister_zone_file,
)

_MIN_ZONE_ID   = 400
_CUSTOM_ROM_IDX = 10

# A zone is four client files; the client requires every one or it throws
# FFXI-2003 on zone-in (see zone-creation-tools memory). Delete must remove all.
_ZONE_PARTS = (
    ("model",  zone_model_file_id),
    ("event",  zone_event_file_id),
    ("dialog", zone_dialog_file_id),
    ("npc",    zone_npc_file_id),
)


@click.command("delete")
@click.argument("zone_id", type=int)
@click.option("--dry-run", is_flag=True, help="Show what would happen without writing.")
@click.option("--apply-db/--no-apply-db", default=None,
              help="Run the zone_settings/zone_weather DELETE on the dev DB now "
                   "(uses XI_DB_* / .env creds). Default: follow XI_DB_AUTOAPPLY.")
def cmd(zone_id: int, dry_run: bool, apply_db: bool | None):
    """Delete a custom zone by ID (must be 400+).

    \b
    Removes all four client DATs (model + event/dialog/npc) from
    FFXI_DIR/ROM10, zeros their FTABLE10/VTABLE10 + base-table entries,
    and deletes the server zone_settings + zone_weather rows (auto-applied with
    the XI_DB_* / .env creds; printed for a manual run if the DB is unreachable).

    \b
    Example:
      xi zone delete 403
      xi zone delete 403 --no-apply-db   # skip the DB delete
      xi zone delete 403 --dry-run
    """
    if zone_id < _MIN_ZONE_ID:
        raise click.ClickException(
            f"Zone ID {zone_id} is below {_MIN_ZONE_ID}. "
            "Only custom zones (400+) can be deleted.")

    result = load_tables(_CUSTOM_ROM_IDX)
    if result is None:
        raise click.ClickException("Could not load FTABLE10/VTABLE10.")
    fdata, vdata = result

    model_fid = zone_model_file_id(zone_id)
    if model_fid >= len(vdata) or (
        vdata[model_fid] == 0
        and struct.unpack_from("<H", fdata, model_fid * 2)[0] == 0
    ):
        raise click.ClickException(
            f"Zone {zone_id} (file_id={model_fid}) has no FTABLE10 entry — already deleted?")

    # Resolve each part's DAT path from the current FTABLE10 snapshot.
    targets = []  # (dtype, fid, dat_path, registered)
    for dtype, fn in _ZONE_PARTS:
        fid = fn(zone_id)
        if fid * 2 + 2 > len(fdata) or fid >= len(vdata):
            continue
        ft_val = struct.unpack_from("<H", fdata, fid * 2)[0]
        registered = not (vdata[fid] == 0 and ft_val == 0)
        subdir, file_idx = ft_val >> 7, ft_val & 0x7F
        dat_path = Path(FFXI_DIR) / f"ROM{_CUSTOM_ROM_IDX}" / str(subdir) / f"{file_idx}.DAT"
        targets.append((dtype, fid, dat_path, registered))

    click.echo(f"Zone ID : {zone_id}\n")
    for dtype, fid, dat_path, registered in targets:
        loc = f"ROM{_CUSTOM_ROM_IDX}/{dat_path.parent.name}/{dat_path.name}" if registered else "(not registered)"
        click.echo(f"  {dtype:6} file_id={fid:6}  {loc}")
    click.echo()

    if dry_run:
        click.echo(click.style(
            "[dry-run] Would delete the above DATs, zero their FTABLE10/VTABLE10 + "
            "base FTABLE.DAT/VTABLE.DAT entries, and run the SQL below on the dev DB.", fg="cyan"))
        click.echo()
        _print_sql(zone_id)
        return

    for dtype, fid, dat_path, registered in targets:
        if not registered:
            continue
        if dat_path.exists():
            dat_path.unlink()
            click.echo(click.style(f"Deleted  : {dat_path}", fg="green"))
        else:
            click.echo(click.style(f"DAT gone : {dat_path} (already removed)", fg="yellow"))
        unregister_zone_file(fid)  # zeros ROM10 + base tables

    click.echo(click.style(
        f"Zeroed   : FTABLE10/VTABLE10 + base tables for all {len(targets)} files", fg="green"))
    click.echo()

    # DB cleanup — remove the server zone_settings + zone_weather rows that `zone new`
    # cloned in, so re-creating a zone of the same ID starts clean (no orphan weather row).
    do_apply = DB_AUTOAPPLY if apply_db is None else apply_db
    if do_apply:
        try:
            rows = _apply_delete(zone_id)
            click.echo(click.style(
                f"DB       : removed {rows} row(s) from zone_settings/zone_weather "
                f"in {DB_NAME}@{DB_HOST}", fg="green"))
        except Exception as exc:  # connection refused, pymysql missing, etc.
            click.echo(click.style(f"DB       : auto-delete skipped ({exc})", fg="yellow"))
            _print_sql(zone_id)
            click.echo(f"             or: mysql -u{DB_USER} {DB_NAME} -e \"{'; '.join(_delete_statements(zone_id))}\"")
    else:
        _print_sql(zone_id)


def _delete_statements(zone_id: int) -> list[str]:
    """The DB rows a custom zone owns (mirrors what `zone new` inserts)."""
    return [
        f"DELETE FROM zone_settings WHERE zoneid = {zone_id}",
        f"DELETE FROM zone_weather WHERE zone = {zone_id}",
    ]


def _apply_delete(zone_id: int) -> int:
    """Run the zone_settings/zone_weather DELETEs against the dev DB. Returns the total
    rows removed. Raises on connection/SQL error (caller falls back to printing the SQL)."""
    import pymysql
    conn = pymysql.connect(**db_creds(), charset="utf8mb4",
                           autocommit=True, connect_timeout=4)
    rows = 0
    try:
        with conn.cursor() as cur:
            for stmt in _delete_statements(zone_id):
                cur.execute(stmt)
                rows += cur.rowcount
    finally:
        conn.close()
    return rows


def _print_sql(zone_id: int) -> None:
    click.echo(click.style("Server SQL to run:", fg="cyan"))
    for stmt in _delete_statements(zone_id):
        click.echo(f"  {stmt};")

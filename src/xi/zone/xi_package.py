"""``xi zone package`` — bundle custom zones into a distributable archive.

A working custom zone is spread across more places than the ROM10 DAT people
expect, and every one of them is load-bearing:

* ``ROM10/<sub>/<slot>.DAT``           the zone model, plus its event/dialog/npc
                                       companions in the next three slots
* ``ROM10/FTABLE10.DAT`` / ``VTABLE10`` the registration that maps file id -> DAT
* ``Ashita/polplugins/DATs/<pack>/ROM10/FTABLE10.DAT`` / ``VTABLE10``
                                       Ashita's DAT-override tree shadows the game
                                       folder; if the tables there are stale the
                                       client resolves the OLD layout and the zone
                                       simply is not there
* ``scripts/commands/zone.lua``        the ``!zone`` row (a zone with no row drops
                                       you at 0,0,0 -- underground, since +Y is down)
* ``scripts/zones/<name>/``            IDs.lua + Zone.lua; the map server errors on
                                       a custom zone without IDs.lua
* ``zone_settings`` / ``zone_weather``  DB rows

Ship any subset of those and the zone breaks in a way that looks like a client
bug. This packages all of them plus a manifest.
"""
from __future__ import annotations

import json
import shutil
import struct
import zipfile
from pathlib import Path

import click

from xi.ftable.xi_core import load_tables
from xi.xi_config import FFXI_DIR
from xi.zone.xi_list import zone_file_id

# A zone's four consecutive ROM10 slots: model, then event/dialog/npc.
COMPANION_COUNT = 4


def _registered_zones(min_id: int = 400, max_id: int = 999) -> dict[int, tuple[int, int]]:
    """``{zone_id: (subdir, slot)}`` for every zone registered in FTABLE10."""
    tables = load_tables(10)
    if not tables:
        return {}
    fdata, vdata = tables
    out: dict[int, tuple[int, int]] = {}
    for zid in range(min_id, max_id + 1):
        fid = zone_file_id(zid)
        if fid >= len(vdata) or vdata[fid] != 10 or fid * 2 + 2 > len(fdata):
            continue
        val = struct.unpack_from("<H", fdata, fid * 2)[0]
        out[zid] = (val >> 7, val & 0x7F)
    return out


def _override_roots(client_root: Path) -> list[Path]:
    """Ashita DAT-override trees (``Ashita/polplugins/DATs/*``) that carry ROM10."""
    base = client_root / "Ashita" / "polplugins" / "DATs"
    if not base.is_dir():
        return []
    return [p for p in sorted(base.iterdir()) if (p / "ROM10").is_dir()]


@click.command("package")
@click.option("--zone", "zone_ids", multiple=True, type=int,
              help="Zone id to include (repeatable). Default: every zone registered in "
                   "FTABLE10 from --min-id up.")
@click.option("--min-id", type=int, default=400, show_default=True,
              help="Lowest zone id to auto-include when --zone is not given.")
@click.option("--out", "out_path", type=click.Path(dir_okay=False), default=None,
              help="Output .zip (default: exports/packages/custom-zones.zip).")
@click.option("--server-dir", type=click.Path(file_okay=False), default=None,
              help="Dev server checkout, for zone.lua and scripts/zones/. "
                   "Default: XI_SERVER_DIR.")
@click.option("--dry-run", is_flag=True, help="List what would be packaged.")
def cmd(zone_ids, min_id, out_path, server_dir, dry_run):
    """Bundle custom zones (DATs, FTABLE/VTABLE, server Lua, SQL) into one archive.

    \b
    Examples:
      xi zone package                          # every custom zone (400+)
      xi zone package --zone 403 --zone 404
      xi zone package --dry-run
    """
    from xi.xi_config import XI_SERVER_DIR

    ffxi = Path(FFXI_DIR)
    client_root = ffxi.parent.parent          # ...\catseyexi-client
    registered = _registered_zones(min_id)
    if zone_ids:
        missing = [z for z in zone_ids if z not in registered]
        if missing:
            raise click.ClickException(
                f"not registered in FTABLE10: {', '.join(map(str, missing))}")
        wanted = {z: registered[z] for z in zone_ids}
    else:
        wanted = registered
    if not wanted:
        raise click.ClickException("no custom zones registered in FTABLE10")

    files: list[tuple[Path, str]] = []       # (source, archive path)
    missing_files: list[str] = []

    # 1. zone DATs + companions
    for zid, (sub, slot) in sorted(wanted.items()):
        for i in range(COMPANION_COUNT):
            rel = f"ROM10/{sub}/{slot + i}.DAT"
            src = ffxi / rel
            if src.exists():
                files.append((src, f"client/Game/FINAL FANTASY XI/{rel}"))
            elif i == 0:
                missing_files.append(rel)

    # 2. the tables, in the game folder AND every override tree
    for rel in ("ROM10/FTABLE10.DAT", "ROM10/VTABLE10.DAT"):
        src = ffxi / rel
        if src.exists():
            files.append((src, f"client/Game/FINAL FANTASY XI/{rel}"))
        else:
            missing_files.append(rel)
    for root in _override_roots(client_root):
        for name in ("FTABLE10.DAT", "VTABLE10.DAT"):
            src = root / "ROM10" / name
            if src.exists():
                arc = f"client/Ashita/polplugins/DATs/{root.name}/ROM10/{name}"
                files.append((src, arc))

    # 3. server side
    sdir = Path(server_dir) if server_dir else (Path(XI_SERVER_DIR) if XI_SERVER_DIR else None)
    zone_names: dict[int, str] = {}
    if sdir and sdir.is_dir():
        zlua = sdir / "scripts" / "commands" / "zone.lua"
        if zlua.exists():
            files.append((zlua, "server/scripts/commands/zone.lua"))
        try:
            import pymysql
            from xi.xi_config import db_creds
            conn = pymysql.connect(**db_creds(), charset="utf8mb4", connect_timeout=4)
            with conn.cursor() as cur:
                cur.execute("SELECT zoneid, name FROM zone_settings WHERE zoneid IN %s",
                            (tuple(wanted),))
                zone_names = {r[0]: r[1] for r in cur.fetchall()}
            conn.close()
        except Exception as exc:                 # DB optional
            click.echo(click.style(f"  DB: name lookup skipped ({exc})", fg="yellow"))
        for zid, nm in zone_names.items():
            d = sdir / "scripts" / "zones" / nm
            if d.is_dir():
                for f in sorted(d.rglob("*")):
                    if f.is_file():
                        files.append((f, f"server/scripts/zones/{nm}/{f.relative_to(d).as_posix()}"))

    click.echo(f"Zones:  {', '.join(str(z) for z in sorted(wanted))}")
    click.echo(f"Files:  {len(files)}")
    if missing_files:
        click.echo(click.style(f"  warning: {len(missing_files)} expected file(s) missing: "
                               + ", ".join(missing_files[:5]), fg="yellow"))
    if dry_run:
        for _src, arc in files:
            click.echo(f"    {arc}")
        click.echo(click.style("Dry run — nothing written.", fg="cyan"))
        return

    out = Path(out_path) if out_path else Path("exports/packages/custom-zones.zip")
    out.parent.mkdir(parents=True, exist_ok=True)

    manifest = {
        "zones": [
            {"id": z, "name": zone_names.get(z), "rom10": f"ROM10/{s}/{sl}.DAT"}
            for z, (s, sl) in sorted(wanted.items())
        ],
        "note": "Copy client/ over the client root and server/ over the server checkout. "
                "The Ashita DAT-override tables shadow the game folder — ship both.",
    }
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for src, arc in files:
            z.write(src, arc)
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        z.writestr("README.txt", _README)

    size = out.stat().st_size
    click.echo(click.style(f"\n✓ {out}  ({size:,} bytes, {len(files)} files)", fg="green"))
    click.echo("  Contents: client/Game/... , client/Ashita/... , server/... , manifest.json")


_README = """Custom zone package
===================

client/Game/FINAL FANTASY XI/ROM10/...
    Zone DATs (model + event/dialog/npc) and FTABLE10/VTABLE10.

client/Ashita/polplugins/DATs/<pack>/ROM10/FTABLE10.DAT, VTABLE10.DAT
    Ashita's DAT-override tree SHADOWS the game folder. If these are stale the
    client resolves the old layout and the custom zones are simply absent. Ship
    them together with the game-folder copies.

server/scripts/commands/zone.lua
    Contains the `!zone <id>` rows. A zone with no row spawns you at 0,0,0 —
    underground for most zones, since FFXI's +Y axis points DOWN.

server/scripts/zones/<name>/
    IDs.lua and Zone.lua. The map server errors on a custom zone without IDs.lua.

Not included: zone_settings / zone_weather rows. Generate those with
`xi zone new` on the target server, or copy the workspace zone-migration.sql.
"""

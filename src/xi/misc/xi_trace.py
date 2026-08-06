"""DAT path → zone_id reverse lookup.

You found a 41.DAT and suspect it's a zone but don't know which zone_id. This
walks every zone_id in the client and asks: does its FTABLE lookup land on
this DAT? Returns every hit (usually 0 or 1, occasionally more if a file_id
range was re-used).
"""

from __future__ import annotations

import re
from pathlib import Path

import click

from xi.ftable.xi_core import load_all_tables, scan_file_ids
from xi.xi_config import FFXI_DIR
from xi.zone.xi_list import ZONE_NAME_DAT, parse_dmsg, zone_file_id

from xi.misc.xi_lsb import load_lsb_zones, load_lsb_zone_enum


def _normalize_dat_path(raw: str) -> str:
    """Accept 'ROM/x/41.DAT', 'rom\\x\\41.dat', or a full absolute path."""
    s = raw.replace("\\", "/").strip()
    if Path(s).is_absolute():
        try:
            s = str(Path(s).relative_to(Path(FFXI_DIR))).replace("\\", "/")
        except ValueError:
            pass
    s = s.lstrip("/")
    # Normalize 'rom' -> 'ROM' casing for the prefix only.
    s = re.sub(r"^(rom[0-9]*)", lambda m: m.group(1).upper(), s)
    # Normalize the extension casing.
    if s.lower().endswith(".dat"):
        s = s[:-4] + ".DAT"
    return s


def trace(dat_path: str) -> list[dict]:
    """Return every zone_id whose FTABLE lookup resolves to `dat_path`."""
    target = _normalize_dat_path(dat_path)

    name_path = Path(FFXI_DIR) / ZONE_NAME_DAT
    names = parse_dmsg(name_path.read_bytes()) if name_path.exists() else []
    tables = load_all_tables()
    try:
        lsb_zones = load_lsb_zones()
    except FileNotFoundError:
        lsb_zones = {}
    try:
        lsb_enum = load_lsb_zone_enum()
    except FileNotFoundError:
        lsb_enum = {}

    hits: list[dict] = []
    # Probe every plausible zone_id. Use both name-table coverage and a
    # safety upper bound past it.
    max_id = max(len(names), 1024)
    for zone_id in range(max_id):
        result = scan_file_ids([zone_file_id(zone_id)], tables)
        if not result:
            continue
        if result[0]["dat"] == target:
            client_name = names[zone_id].strip() if zone_id < len(names) else ""
            lsb = lsb_zones.get(zone_id)
            hits.append({
                "zone_id":     zone_id,
                "file_id":     zone_file_id(zone_id),
                "client_name": client_name,
                "lsb_name":    lsb.name if lsb else None,
                "lsb_enum":    lsb_enum.get(zone_id),
            })
    return hits


@click.command("trace")
@click.argument("dat_path")
def cmd(dat_path):
    """Find which zone_id(s) a DAT path is registered as.

    Example:

        xi misc trace ROM/108/41.DAT
        xi misc trace "F:\\...\\FINAL FANTASY XI\\ROM/108/41.DAT"
    """
    hits = trace(dat_path)
    if not hits:
        click.echo(click.style(
            f"No zone_id resolves to {dat_path!r} via FTABLE.\n"
            "It may be a non-zone DAT (texture, mesh, dialogue), or in a ROM the FTABLE doesn't cover.",
            fg="yellow"))
        return

    click.echo(click.style(f"{len(hits)} match{'es' if len(hits) > 1 else ''}:", fg="green"))
    for h in hits:
        cn = h["client_name"] or click.style("<no client name>", fg="magenta")
        ln = h["lsb_name"] or click.style("<not in LSB>", fg="yellow")
        en = h["lsb_enum"] or "-"
        click.echo(f"  zone_id={h['zone_id']:>4}  file_id=0x{h['file_id']:05X}  "
                   f"client={cn!s:24s}  lsb={ln!s:20s}  enum={en}")

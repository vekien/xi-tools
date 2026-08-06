#!/usr/bin/env python3
"""`xi zone object delete` — blank placement(s) by name so the engine skips them.

Zeroes the 16-byte mesh_id field of each matched record; the record itself stays
(object count unchanged) so the section size and all internal offsets remain valid.
The change is re-encrypted and written back to the DAT in place."""

from pathlib import Path
from typing import List

import click

from xi.entity.anim.xi_export import parse_sections
from xi.xi_config import FFXI_DIR, editable_dat, output_path_for
from xi.zone.xi_decrypt import decrypt_zone_objects, reencrypt_zone_objects, load_key_tables
from xi.zone.xi_export import SECTION_TYPE_ZONE_DEF
from xi.zone.xi_import import _zero_placement


def _read_name(data: bytes, data_start: int, index: int) -> str:
    base = data_start + 0x20 + index * 0x64
    raw = bytes(data[base: base + 0x10])
    return raw.split(b"\x00")[0].decode("ascii", errors="replace").strip()


def delete_placements(dat_path: Path, names) -> List[str]:
    """Blank each named placement's mesh_id (zeroes it so the engine skips it).
    Returns the list of names that were actually matched and blanked."""
    names_set = set(names)
    dll = Path(FFXI_DIR) / "FFXiMain.dll"
    if not dll.exists():
        raise FileNotFoundError(f"FFXiMain.dll not found at {dll}")
    table1, _ = load_key_tables(dll)

    out = editable_dat(dat_path, fresh=False)
    data = bytearray(out.read_bytes())
    sections = parse_sections(data)
    zonedef = next((s for s in sections if s.type_code == SECTION_TYPE_ZONE_DEF), None)
    if zonedef is None:
        raise ValueError("DAT has no 0x1C ZoneDef (placement) section")

    node_count = decrypt_zone_objects(data, zonedef.data_start, zonedef.start, zonedef.size, table1)

    removed = []
    for i in range(node_count):
        name = _read_name(data, zonedef.data_start, i)
        if name in names_set:
            _zero_placement(data, zonedef.data_start, i)
            removed.append(name)

    reencrypt_zone_objects(data, zonedef.data_start, zonedef.start, zonedef.size, table1)
    out.write_bytes(bytes(data))
    return removed


@click.command("delete")
@click.argument("dat_path")
@click.argument("names", nargs=-1, required=True)
@click.option("--dry-run", is_flag=True, help="Show what would be deleted without writing.")
def cmd(dat_path, names, dry_run):
    """Delete (blank) zone placement(s) by mesh name so the engine skips them.

    The record stays in the 0x1C section — its mesh_id is zeroed so the engine
    renders nothing. Object count and all internal offsets remain valid.

    Examples:

    \b
      xi zone object delete ROM/1/41 hasi
      xi zone object delete ROM/1/41 hasi block03 my_npc
    """
    from xi.entity.mesh.xi_export import resolve_dat_path
    try:
        resolved = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e))

    if dry_run:
        click.echo(f"Would delete: {', '.join(names)}")
        return

    try:
        removed = delete_placements(resolved, names)
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e))

    not_found = [n for n in names if n not in removed]
    if removed:
        click.echo(f"Deleted {len(removed)} placement(s): {', '.join(removed)}")
    if not_found:
        click.echo(f"Not found (skipped): {', '.join(not_found)}", err=True)
    if not removed:
        raise click.ClickException("No matching placements found.")
    click.echo(f"Wrote: {output_path_for(resolved)}")

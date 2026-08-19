"""``xi zone patch-proto`` — make a pre-production zone readable by the retail client.

The client's ZoneDef reader (``FUN_10177ef0`` in FFXiMain) advances placement records
by a hardcoded 0x64 with no branch on the mode byte, so it misreads every 0x54
pre-production zone: most records land on garbage and are silently dropped, which is
why these zones look half-built in game. Widening the records to 0x64 is what makes
the client agree with the file.

Patches ``<dat>.base`` too by default — publish is *reset-from-pristine then apply*, so
leaving the base at 0x54 means every publish reverts the fix and then writes into the
reverted file, shredding records. See docs/zone/prototype-zones.md.
"""

from pathlib import Path

import click

from xi.zone.xi_decrypt import (load_key_tables, decrypt_zone_objects,
                                reencrypt_zone_objects)
from xi.zone.xi_export import parse_sections, resolve_dat_path
from xi.zone.xi_zonedef import (SECTION_TYPE_ZONE_DEF, OBJ_RECORD_SIZE,
                                convert_zonedef_to_retail_stride, zonedef_record_size)
from xi.xi_config import FFXI_DIR


def _zonedef(data: bytearray):
    secs = [s for s in parse_sections(data) if s.type_code == SECTION_TYPE_ZONE_DEF]
    if not secs:
        raise click.ClickException("no 0x1C ZoneDef section — is this a zone DAT?")
    return secs[0]


def patch_file(path: Path, table1: bytes, dry_run: bool = False) -> str:
    """Convert one DAT in place. Returns a one-line status."""
    data = bytearray(path.read_bytes())
    sec = _zonedef(data)
    n = decrypt_zone_objects(data, sec.data_start, sec.start, sec.size, table1)
    stride = zonedef_record_size(data, sec.data_start, n)
    if stride == OBJ_RECORD_SIZE:
        return f"{path.name}: already 0x64 ({n} records) — skipped"
    if dry_run:
        grow = n * (OBJ_RECORD_SIZE - stride)
        return f"{path.name}: would convert {n} records 0x{stride:02x} -> 0x64 (+{grow:,} bytes)"

    secbuf = bytearray(data[sec.start:sec.start + sec.size])
    secbuf, delta = convert_zonedef_to_retail_stride(secbuf)
    reencrypt_zone_objects(secbuf, 0x10, 0, len(secbuf), table1)
    out = bytearray(data[:sec.start]) + secbuf + bytearray(data[sec.start + sec.size:])
    path.write_bytes(bytes(out))

    chk = bytearray(path.read_bytes())
    s2 = _zonedef(chk)
    n2 = decrypt_zone_objects(chk, s2.data_start, s2.start, s2.size, table1)
    got = zonedef_record_size(chk, s2.data_start, n2)
    if got != OBJ_RECORD_SIZE or n2 != n:
        raise click.ClickException(
            f"{path.name}: verification failed after conversion "
            f"(stride 0x{got:02x}, {n2} records, expected 0x64 / {n})")
    return f"{path.name}: {n} records 0x{stride:02x} -> 0x64 (+{delta:,} bytes)"


@click.command("patch-proto")
@click.argument("dat_path")
@click.option("--base/--no-base", "do_base", default=True, show_default=True,
              help="Also patch <dat>.base. Leave this ON unless you know why not: "
                   "publish resets from .base, so an unpatched base undoes the fix "
                   "on every publish and corrupts records on the way.")
@click.option("--dry-run", is_flag=True, help="Show what would change without writing.")
def cmd(dat_path: str, do_base: bool, dry_run: bool):
    """Convert a pre-production zone's placement records from 0x54 to 0x64.

    The retail client always reads placements at 0x64, so a 0x54 zone renders with
    most of its objects missing or misplaced. This rewrites the records to the size
    the client expects; geometry, collision and every other section are untouched.

    Safe to re-run — an already-converted zone is skipped.

    \b
    Examples:
      xi zone patch-proto ROM/0/41
      xi zone patch-proto ROM10/1/4 --dry-run
      xi zone patch-proto ROM/0/42 --no-base
    """
    try:
        dat = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e)) from e

    dll = Path(FFXI_DIR) / "FFXiMain.dll"
    if not dll.is_file():
        raise click.ClickException(f"FFXiMain.dll not found at {dll} (needed for decryption)")
    table1, _ = load_key_tables(dll)

    targets = [dat]
    base = dat.with_name(dat.name + ".base")
    if do_base:
        if base.is_file():
            targets.append(base)
        else:
            click.echo(click.style(
                f"  note: no {base.name} yet — it is created on first edit, "
                f"and will already be 0x64 once this DAT is converted.", fg="cyan"))

    for path in targets:
        click.echo("  " + patch_file(path, table1, dry_run=dry_run))
    if not dry_run:
        click.echo(click.style("\n✓ Client can now read this zone's placements. "
                               "Restart the game client to see it.", fg="green"))

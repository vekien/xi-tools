#!/usr/bin/env python3
"""`xi ftable list` — dump every file_id → DAT path mapping from the FTABLE/VTABLE tables.

Reads all ROM tables (base + ROM2-9) and lists every registered file_id alongside
its resolved DAT path and ROM index. Pass --header-read (or --header to filter)
to also read each DAT's file header — the first 4 bytes of the file, which
identify its type (e.g. "lobb", "menu"). Off by default since reading the header
means opening every matching DAT on disk. Useful for finding where a particular
DAT lives and auditing the table layout.
"""

import json
import sys
import time
from pathlib import Path
from typing import Iterator

import click

from xi.xi_config import FFXI_DIR
from xi.ftable.xi_core import all_tables, resolve_dat


def _iter_entries(rom_filter: int | None = None, header_filter: str | None = None,
                  id_min: int | None = None, id_max: int | None = None,
                  progress: bool = False, read_header: bool = False) -> Iterator[dict]:
    """Yield one dict per registered file_id across all loaded ROM tables."""
    header_filter_b = header_filter.encode("ascii") if header_filter else None
    yielded = 0
    last_log = time.monotonic()
    for rom_idx, fdata, vdata in all_tables():
        if rom_filter is not None and rom_idx != rom_filter:
            continue
        n = min(len(fdata) // 2, len(vdata))
        if progress:
            print(f"[ftable] ROM {rom_idx}: scanning {n:,} file_ids...", file=sys.stderr)
        for file_id in range(n):
            if progress and time.monotonic() - last_log > 2:
                print(f"[ftable] ROM {rom_idx}: file_id {file_id:,}/{n:,}  "
                      f"({yielded:,} entries so far)", file=sys.stderr)
                last_log = time.monotonic()
            if id_min is not None and file_id < id_min:
                continue
            if id_max is not None and file_id > id_max:
                continue
            dat, vt_val = resolve_dat(fdata, vdata, file_id)
            if not dat:
                continue
            entry = {"file_id": file_id, "rom": rom_idx, "dat": dat}
            if header_filter_b or read_header:
                full = Path(FFXI_DIR) / dat
                if full.exists():
                    with open(full, "rb") as f:
                        hdr = f.read(4)
                    ascii_header = "".join(chr(b) if 32 <= b < 127 else "." for b in hdr)
                    entry["header"] = ascii_header
                    entry["header_hex"] = hdr.hex()
                    if header_filter_b and hdr[:4] != header_filter_b:
                        continue
                else:
                    entry["header"] = None
                    entry["header_hex"] = None
                    if header_filter_b:
                        continue
            yielded += 1
            yield entry


@click.command("list")
@click.option("--rom", "rom_filter", type=int, default=None,
              help="Filter to a specific ROM index (1=base, 2-9=expansions).")
@click.option("--header", "header_filter", default=None, metavar="AAAA",
              help="Filter by the DAT's 4-byte file header, e.g. --header lobb or "
                   "--header menu. Forces a header read per file.")
@click.option("--range", "id_range", default=None, metavar="MIN-MAX",
              help="Restrict to a file_id range, e.g. --range 0-200.")
@click.option("--json", "as_json", is_flag=True,
              help="Write exports/ftable/ftable_list.json instead of printing.")
@click.option("--header-read", "header_read", is_flag=True,
              help="Also read each DAT's file header (slower: opens every matching "
                   "DAT on disk). Implied by --header.")
@click.option("--progress", is_flag=True,
              help="Print scan progress to stderr (useful for unfiltered scans).")
def list_cmd(rom_filter, header_filter, id_range, as_json, header_read, progress):
    """List all file_id → DAT path mappings from the FTABLE/VTABLE tables.

    Reads every registered entry across the base ROM and ROM2-9 and resolves
    each file_id to its DAT path. Pass --header-read to also read the first 4
    bytes of each DAT and show its file header.

    Examples:

    \b
      xi ftable list --header lobb
      xi ftable list --header menu
      xi ftable list --range 0-200
      xi ftable list --rom 1 --json
    """
    id_min = id_max = None
    if id_range:
        parts = id_range.split("-")
        if len(parts) == 2:
            id_min, id_max = int(parts[0]), int(parts[1])
        else:
            raise click.ClickException("--range must be MIN-MAX, e.g. 0-200")

    entries = list(_iter_entries(
        rom_filter=rom_filter,
        header_filter=header_filter,
        id_min=id_min,
        id_max=id_max,
        progress=progress,
        read_header=header_read,
    ))

    if not entries:
        click.echo("No entries found.")
        return

    if as_json:
        out_dir = Path("exports") / "ftable"
        out_dir.mkdir(parents=True, exist_ok=True)
        suffix = f"_header_{header_filter}" if header_filter else ""
        suffix += f"_rom{rom_filter}" if rom_filter else ""
        out = out_dir / f"ftable_list{suffix}.json"
        out.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        click.echo(f"Wrote {len(entries):,} entries → {out}")
        return

    # tabular output
    click.echo(f"{'file_id':>8}  {'ROM':>4}  {'DAT path':<30}  {'header'}")
    click.echo("-" * 72)
    for e in entries:
        header = e.get("header") or "?"
        click.echo(f"{e['file_id']:>8}  {e['rom']:>4}  {e['dat']:<30}  {header}")
    click.echo(f"\n{len(entries):,} entries.")

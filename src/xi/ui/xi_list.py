#!/usr/bin/env python3
"""`xi ui list` — list all DAT files that contain UI textures (lobb / menu format).

Uses the pre-scanned header research (`research/ftable_full_scan.json`) to find
DATs whose first 4 bytes match a known UI container magic: `menu`, `lobb`, `win0`,
`sel_`, `titl`, `mgc_`. Falls back to a live FTABLE scan if the research file is
absent.
"""

import json
import os
from pathlib import Path

import click

from xi.xi_config import XI_TOOLS_DIR


# UI container magic bytes (first 4 bytes of the DAT file).
# menu / lobb = main UI containers the `ui extract/import` commands handle.
# win0 = window-skin DATs (ROM/0/14-21). sel_ / titl / mgc_ are UI sub-types.
UI_MAGICS = {"menu", "lobb", "win0", "sel_", "titl", "mgc_"}

_SCAN_JSON = Path(XI_TOOLS_DIR) / "research" / "ftable_full_scan.json"


def _magic_from_scan_field(field: str) -> str | None:
    """Parse the ASCII magic out of a scan field like '6d656e75 \"menu\"'."""
    if '"' in field:
        start = field.index('"') + 1
        end = field.rindex('"')
        return field[start:end]
    return None


def list_ui_dats(magic_filter: set | None = None) -> list[dict]:
    """Return entries from the pre-scanned research file matching UI magics.
    Falls back to a live file-header scan against FFXI_DIR if the JSON is absent."""
    want = magic_filter or UI_MAGICS

    if _SCAN_JSON.exists():
        raw = json.loads(_SCAN_JSON.read_text(encoding="utf-8"))
        entries = []
        # Format: {"FTABLE": {"file_id": ["ROM/path", size, "hex \"magic\""]}}
        table = raw.get("FTABLE", {})
        for fid_str, info in table.items():
            if not isinstance(info, list) or len(info) < 3:
                continue
            dat_path = info[0]
            size = info[1]
            magic = _magic_from_scan_field(str(info[2]))
            if magic and magic in want:
                entries.append({
                    "file_id": int(fid_str),
                    "dat": dat_path,
                    "magic": magic,
                    "size": size,
                })
        return sorted(entries, key=lambda e: e["file_id"])

    # Fallback: live scan via FTABLE
    from xi.xi_config import FFXI_DIR
    from xi.ftable.xi_core import all_tables, resolve_dat
    entries = []
    for rom_idx, fdata, vdata in all_tables():
        n = min(len(fdata) // 2, len(vdata))
        for file_id in range(n):
            dat, _ = resolve_dat(fdata, vdata, file_id)
            if not dat:
                continue
            full = Path(FFXI_DIR) / dat
            if not full.exists():
                continue
            try:
                with open(full, "rb") as f:
                    hdr = f.read(4)
                magic = "".join(chr(b) if 32 <= b < 127 else "." for b in hdr)
            except OSError:
                continue
            if magic in want:
                entries.append({
                    "file_id": file_id,
                    "dat": dat,
                    "magic": magic,
                    "size": os.path.getsize(full),
                })
    return sorted(entries, key=lambda e: e["file_id"])


@click.command("list")
@click.option("--magic", "magic_filter", multiple=True,
              help="Filter to specific magic(s), e.g. --magic menu --magic lobb. "
                   f"Default: all UI types ({', '.join(sorted(UI_MAGICS))}).")
@click.option("--json", "as_json", is_flag=True,
              help="Write exports/ui/ui_dats.json instead of printing.")
def list_cmd(magic_filter, as_json):
    """List all DAT files that contain UI textures (lobb / menu / win0 etc.).

    Uses pre-scanned header data from research/ftable_full_scan.json to identify
    DATs whose first 4 bytes match a known UI container format. These are the files
    that `xi ui tex export` / `xi ui tex import` can operate on.

    Examples:

    \b
      xi ui tex list
      xi ui tex list --magic lobb
      xi ui tex list --magic menu --magic win0
      xi ui tex list --json
    """
    want = set(magic_filter) if magic_filter else None
    entries = list_ui_dats(want)

    if not entries:
        click.echo("No UI DATs found.")
        return

    if as_json:
        out_dir = Path("exports") / "ui"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / "ui_dats.json"
        out.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        click.echo(f"Wrote {len(entries)} UI DATs → {out}")
        return

    click.echo(f"{'file_id':>8}  {'magic':>6}  {'size':>10}  DAT path")
    click.echo("-" * 70)
    for e in entries:
        click.echo(f"{e['file_id']:>8}  {e['magic']:>6}  {e['size']:>10,}  {e['dat']}")
    click.echo(f"\n{len(entries)} UI DAT(s) found.")

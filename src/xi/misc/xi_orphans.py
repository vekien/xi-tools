"""Find ROM DATs that are physically on disk but have no FTABLE entry.

An orphan DAT is one where no file_id in any FTABLE resolves to its path.
These are dev/cut content the client can't reach at runtime — sometimes
zones, sometimes models, sometimes test data.

Zone-like DATs are heuristically large (>1MB) but small ones can be cut
content too — surfaces every orphan with size + first 8 bytes for triage.
"""

from __future__ import annotations

import struct
from pathlib import Path

import click

from xi.ftable.xi_core import load_all_tables, resolve_dat
from xi.xi_config import FFXI_DIR


def _enumerate_registered_paths(tables: dict) -> set[str]:
    """Walk every (rom_idx, file_id) and collect the resolved DAT path."""
    out: set[str] = set()
    for rom_idx, (fdata, vdata) in sorted(tables.items()):
        # FTABLE is a uint16[] of length len/2; ceiling of N file_ids covered.
        n_entries = min(len(fdata) // 2, len(vdata))
        for file_id in range(n_entries):
            dat, _vt = resolve_dat(fdata, vdata, file_id)
            if dat:
                out.add(dat.replace("\\", "/"))
    return out


def _all_dats_on_disk() -> list[Path]:
    root = Path(FFXI_DIR)
    out: list[Path] = []
    for rom_dir in sorted(root.glob("ROM*")):
        if not rom_dir.is_dir():
            continue
        for dat in rom_dir.rglob("*.DAT"):
            out.append(dat)
    return out


def _rel_dat(path: Path) -> str:
    return str(path.relative_to(Path(FFXI_DIR))).replace("\\", "/")


def find_orphans(min_size: int = 0) -> list[dict]:
    tables = load_all_tables()
    registered = _enumerate_registered_paths(tables)

    orphans: list[dict] = []
    for p in _all_dats_on_disk():
        rel = _rel_dat(p)
        if rel in registered:
            continue
        size = p.stat().st_size
        if size < min_size:
            continue
        head = b""
        try:
            with open(p, "rb") as f:
                head = f.read(8)
        except OSError:
            pass
        orphans.append({
            "path": rel,
            "size": size,
            "head_hex": head.hex(),
            "head_ascii": "".join(chr(b) if 32 <= b < 127 else "." for b in head),
        })
    orphans.sort(key=lambda o: -o["size"])
    return orphans


@click.command("orphans")
@click.option("--min-size", default=1_000_000, show_default=True,
              help="Skip DATs smaller than this (bytes). Default ~1MB for zone-candidate filtering.")
@click.option("--name", "filter_name",
              help='Only show DATs whose filename matches this (e.g. "41.DAT").')
@click.option("--limit", default=50, show_default=True, type=int,
              help="Max rows to print.")
def cmd(min_size, filter_name, limit):
    """List ROM DATs on disk that no FTABLE entry resolves to.

    Default surfaces DATs >= 1MB (size cutoff for likely zones). For a
    specific filename hunt, pass --name FILE.DAT.
    """
    orphans = find_orphans(min_size=min_size)
    if filter_name:
        wanted = filter_name.lower().lstrip("/")
        orphans = [o for o in orphans if o["path"].lower().endswith("/" + wanted) or o["path"].lower() == wanted]

    if not orphans:
        click.echo(click.style("No orphan DATs match.", fg="yellow"))
        return

    click.echo(click.style(f"{len(orphans)} orphan DAT(s) (showing up to {limit}):", fg="green"))
    click.echo(f"{'path':40s}  {'size':>10s}  head_hex            ascii")
    click.echo("-" * 100)
    for o in orphans[:limit]:
        size_str = f"{o['size']:,}"
        click.echo(f"{o['path']:40s}  {size_str:>10s}  {o['head_hex']:18s}  {o['head_ascii']}")

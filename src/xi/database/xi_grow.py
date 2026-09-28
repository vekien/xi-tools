"""``xi database grow``: a client table grown to a fixed number of rows, once — the set-up a
table needs when a client plugin reads it to a fixed count, as ``xi ftable expand`` is for the
file tables. An item table (``ROM/288/80.DAT``) or a d_msg table (``ROM/181/72.DAT``), told
apart by what the file holds.

The new rows are a filler:

- a d_msg table: a row blanked to '.' (the last row's shape), as retail's unnamed rows are;
- an item table: a copy of the table's last placeholder record (named '.' or nothing);
- ``--fill-from ROW``: a copy of that row; ``--fill-hex``: exact bytes (a d_msg block, or a
  decrypted item record).

An item record's id continues the table's numbering: row 0's id plus its row.

Growing is set-up, not content: the table's ``.base`` grows too (the install's, or the pivot
folder's with ``--pivot``), so a reset (``xi dats build --all``) keeps the rows and takes back
only what the projects wrote into them. A table already that long is left alone.
"""
from __future__ import annotations

import struct
from pathlib import Path

import click

from xi.common import xi_dmsg as D


class GrowError(ValueError):
    pass


def _is_dmsg(data: bytes) -> bool:
    return data[:5] == b"d_msg"


def rows(data: bytes) -> int:
    if _is_dmsg(data):
        return D.parse(data).num
    from xi.ui.items.xi_layout import detect_stride
    return len(data) // detect_stride(data)


def _placeholder(rec: bytes) -> bool:
    from xi.database.xi_build import parse_subs, sub_value
    from xi.ui.items.xi_layout import ICON_OFFSET, find_text_offset
    off = find_text_offset(rec)
    if off is None:
        return False
    try:
        subs = parse_subs(rec, off, ICON_OFFSET)[0]
    except ValueError:
        return False
    return bool(subs) and subs[0]["flag"] == 0 and sub_value(subs[0]).strip() in ("", ".")


def filler(data: bytes, row: int | None = None, hexed: str | None = None) -> bytes:
    """The bytes each new row of the table ``data`` gets (an item record before its id)."""
    if _is_dmsg(data):
        t = D.parse(data)
        if hexed is not None:
            block = bytes.fromhex(hexed.replace(" ", ""))
            if t.stride and len(block) != t.stride:
                raise GrowError(f"--fill-hex is {len(block)} bytes; the table's rows are {t.stride}")
            return block
        if row is not None:
            if not 0 <= row < t.num:
                raise GrowError(f"--fill-from {row}: the table has {t.num} rows")
            return bytes(t.blocks[row])
        if not t.num:
            raise GrowError("the table has no row to take the shape of a new one from; give --fill-hex")
        from xi.database.xi_build import _blank, _block_subs
        return D._assemble_block(_blank(_block_subs(t.blocks[-1]), "."), t.stride)
    from xi.ui.items.xi_layout import detect_stride
    from xi.ui.items.xi_parser import _decrypt
    stride = detect_stride(data)
    dec = _decrypt(data)
    n = len(dec) // stride
    if hexed is not None:
        rec = bytes.fromhex(hexed.replace(" ", ""))
        if len(rec) != stride:
            raise GrowError(f"--fill-hex is {len(rec)} bytes; the table's records are {stride:#x}")
        return rec
    if row is not None:
        if not 0 <= row < n:
            raise GrowError(f"--fill-from {row}: the table has {n} records")
        return dec[row * stride:(row + 1) * stride]
    for i in reversed(range(n)):
        rec = dec[i * stride:(i + 1) * stride]
        if _placeholder(rec):
            return rec
    raise GrowError("the table has no placeholder record (named '.' or nothing) to copy; "
                    "give --fill-from or --fill-hex")


def grow(data: bytes, count: int, fill: bytes) -> bytes:
    """``data`` with rows added until it holds ``count``, each ``fill``."""
    if _is_dmsg(data):
        t = D.parse(data)
        if t.num >= count:
            return data
        t.blocks += [bytearray(fill) for _ in range(count - t.num)]
        return D.serialize(t)
    from xi.ui.items.xi_layout import detect_stride
    from xi.ui.items.xi_parser import _decrypt, _encrypt
    stride = detect_stride(data)
    dec = bytearray(_decrypt(data))
    n = len(dec) // stride
    if n >= count:
        return data
    first = struct.unpack_from("<I", dec, 0)[0] if n else struct.unpack_from("<I", fill, 0)[0]
    for i in range(n, count):
        rec = bytearray(fill)
        struct.pack_into("<I", rec, 0, (first + i) & 0xFFFFFFFF)
        dec += rec
    return _encrypt(bytes(dec))


def _pristine(path: Path) -> bytes | None:
    """``path``'s bytes before xi-tools wrote them: its ``.base``, else itself."""
    base = path.with_name(path.name + ".base")
    if base.is_file() and base.stat().st_size:
        return base.read_bytes()
    return path.read_bytes() if path.is_file() else None


def grow_table(root: Path, rel: str, count: int, *, row: int | None = None, hexed: str | None = None,
               dry_run: bool = False) -> dict:
    """Grow table ``rel`` in ``root`` and its ``.base`` to ``count`` rows. ``{rel, before,
    after, base_before, written}``."""
    import xi.xi_config as cfg
    from xi.menu.xi_menu_table import target_path
    target = target_path(root, rel)
    install = Path(cfg.FFXI_DIR) / Path(*rel.split("/"))
    base = target.with_name(target.name + ".base")
    if target.is_file():
        current = target.read_bytes()
    elif install.is_file():
        current = install.read_bytes()          # the root has no copy yet: the install's, as a read gets
    else:
        raise GrowError(f"{rel}: no such table in {root} or the install")
    if base.is_file() and base.stat().st_size:
        baseline = base.read_bytes()
    elif base.is_file() or not target.is_file():
        baseline = _pristine(install)           # a pivot copy a build made: what the folder falls back to
    else:
        baseline = current                      # never written through xi-tools
    try:
        fill = filler(baseline, row, hexed)
        new_current, new_base = grow(current, count, fill), grow(baseline, count, fill)
        out = {"rel": rel, "before": rows(current), "after": rows(new_current),
               "base_before": rows(baseline), "written": False}
    except (D.DmsgError, ValueError) as e:
        if isinstance(e, GrowError):
            raise
        raise GrowError(f"{rel}: not an item or d_msg table ({e})") from None
    if dry_run or (new_current == current and new_base == baseline):
        return out
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(new_current)
    base.write_bytes(new_base)
    out["written"] = True
    return out


@click.command("grow")
@click.argument("table")
@click.argument("count", type=click.IntRange(1))
@click.option("--fill-from", "row", type=int, default=None, help="Each new row a copy of this row.")
@click.option("--fill-hex", "hexed", default=None,
              help="Each new row exactly these bytes (a d_msg block, or a decrypted item record).")
@click.option("--pivot", is_flag=True, default=False,
              help="Grow FFXI_PIVOT_DIR's copy of the table (made from the install's when it has none).")
@click.option("--dry-run", is_flag=True, default=False, help="Say what would change; write nothing.")
def cmd(table: str, count: int, row: int | None, hexed: str | None, pivot: bool, dry_run: bool):
    """Grow TABLE (a ROM path: an item DAT or a d_msg table) to COUNT rows, once.

    \b
    New rows are a filler: a d_msg row blanked to '.', or the item table's last placeholder
    record (its id following the table's numbering), unless --fill-from / --fill-hex says
    otherwise. The table's .base grows too, so `xi dats build --all` resets back to the
    grown table. A table already COUNT rows long is left alone.

    \b
    Examples:
      xi database grow ROM/181/72.DAT 4096
      xi database grow ROM/288/80.DAT 8192 --fill-from 1023 --pivot
    """
    if row is not None and hexed is not None:
        raise click.ClickException("give --fill-from or --fill-hex, not both")
    from xi.dats.xi_dats import _target_root
    rel = table.replace("\\", "/")
    rel = "ROM" + rel[3:] if rel[:3].upper() == "ROM" else rel
    if not rel.upper().endswith(".DAT"):
        rel += ".DAT"
    root = _target_root("pivot" if pivot else "dir")
    try:
        r = grow_table(root, rel, count, row=row, hexed=hexed, dry_run=dry_run)
    except GrowError as e:
        raise click.ClickException(str(e))
    if r["before"] >= count and r["base_before"] >= count:
        click.echo(f"{rel}: already {r['before']:,} rows; left as it is")
        return
    verb = "would grow" if dry_run else "grew"
    click.echo(f"{rel} in {root}: {verb} {r['before']:,} -> {r['after']:,} rows "
               f"(its .base {r['base_before']:,} -> {max(count, r['base_before']):,})")

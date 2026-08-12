"""xi dll <target> patch — apply a .patch file (va/expect/replace edits) to an unpacked DLL.

Patch-file format (one edit per line, ``#`` comments, ``;`` inline notes)::

    <va> <expect_hex> <replace_hex>  ; <note>

``va`` is a virtual address (the DLL's PE ImageBase). ``expect_hex`` must be present
before the edit (mismatch aborts the whole run — no partial writes); ``replace_hex``
is written in its place and must be the same length. Re-applying is safe: any edit
whose bytes already equal ``replace_hex`` is skipped.

See ``docs/ffximain/inventory.md`` and ``docs/ffximain/ffximain_inventory.patch``.
"""
from __future__ import annotations

from pathlib import Path

import click


def parse_patch(text: str) -> list[tuple[int, int, bytes, bytes]]:
    entries: list[tuple[int, int, bytes, bytes]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split(";", 1)[0].strip()  # drop inline ; note
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3:
            raise click.ClickException(
                f"patch line {lineno}: expected '<va> <expect_hex> <replace_hex>'"
            )
        try:
            va = int(parts[0], 16)
            expect = bytes.fromhex(parts[1])
            replace = bytes.fromhex(parts[2])
        except ValueError as e:
            raise click.ClickException(f"patch line {lineno}: {e}")
        if len(expect) != len(replace):
            raise click.ClickException(
                f"patch line {lineno}: expect ({len(expect)}) / replace "
                f"({len(replace)}) length mismatch"
            )
        entries.append((lineno, va, expect, replace))
    return entries


def make_patch_cmd():
    @click.command(
        "patch",
        help="Apply a .patch file (va/expect/replace byte edits) to an UNPACKED DLL.",
    )
    @click.option(
        "--unpacked",
        required=True,
        type=click.Path(exists=True, path_type=Path),
        help="Unpacked DLL to patch (output of `unpack`).",
    )
    @click.option(
        "--patch",
        "patch_file",
        required=True,
        type=click.Path(exists=True, path_type=Path),
        help="Patch file (e.g. docs/ffximain/ffximain_inventory.patch).",
    )
    @click.option(
        "--output",
        type=click.Path(path_type=Path),
        default=None,
        help="Output DLL [default: overwrite --unpacked in place].",
    )
    @click.option(
        "--dry-run",
        is_flag=True,
        help="Verify and report only; write nothing.",
    )
    def cmd(unpacked: Path, patch_file: Path, output: Path | None, dry_run: bool) -> None:
        import pefile

        data = bytearray(unpacked.read_bytes())
        pe = pefile.PE(data=bytes(data), fast_load=True)
        img = pe.OPTIONAL_HEADER.ImageBase

        def va_to_off(va: int) -> int:
            return pe.get_offset_from_rva(va - img)

        entries = parse_patch(patch_file.read_text())
        applied = skipped = 0
        fails: list[tuple[int, int, str]] = []

        for lineno, va, expect, replace in entries:
            try:
                off = va_to_off(va)
            except Exception as e:  # noqa: BLE001 - report any RVA-mapping failure
                fails.append((lineno, va, f"va not in image ({e})"))
                continue
            cur = bytes(data[off : off + len(expect)])
            if cur == replace:
                skipped += 1
                continue
            if cur != expect:
                fails.append(
                    (lineno, va, f"expect {expect.hex()} but found {cur.hex()}")
                )
                continue
            data[off : off + len(replace)] = replace
            applied += 1

        click.echo(f"Patch file      : {patch_file}")
        click.echo(f"Target          : {unpacked}  (ImageBase 0x{img:08X})")
        click.echo(f"Edits           : {len(entries)}")
        click.echo(f"  applied       : {applied}")
        click.echo(f"  already-patched: {skipped}")
        click.echo(f"  failed        : {len(fails)}")
        for lineno, va, msg in fails[:25]:
            click.echo(f"    line {lineno}  0x{va:08X}: {msg}")
        if len(fails) > 25:
            click.echo(f"    ... and {len(fails) - 25} more")

        if fails:
            raise click.ClickException(
                f"{len(fails)} edit(s) did not match — nothing written. "
                "Is --unpacked a clean unpack of the matching FFXiMain.dll?"
            )
        if dry_run:
            click.echo("Dry-run: no file written.")
            return

        out = output or unpacked
        out.write_bytes(bytes(data))
        click.echo(f"Wrote           : {out}")
        click.echo("Next: `xi dll ffximain pack --template <packed> --unpacked <this> --output <packed>`")

    return cmd


# Shared across targets (image base comes from the DLL's PE header).
cmd_ffximain = make_patch_cmd()

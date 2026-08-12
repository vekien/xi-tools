"""xi dll <target> unpack — decompress POL1 .text to a Ghidra-loadable PE."""
from __future__ import annotations

from pathlib import Path

import click

from xi.dll.pol1_io import unpack_dll
from xi.dll.targets import DllTarget, get_target


def make_unpack_cmd(target: DllTarget):
    help_text = (
        f"Decompress POL1 -> {Path(target.filename).stem}_unpacked.dll "
        "(load in Ghidra/IDA)."
    )

    @click.command("unpack", help=help_text)
    @click.option(
        "--dll",
        type=click.Path(exists=True, path_type=Path),
        default=None,
        help=f"Path to packed {target.display} "
        f"[default: first existing candidate / misc/{target.filename}]",
    )
    @click.option(
        "--output",
        type=click.Path(path_type=Path),
        default=None,
        help=f"Output unpacked DLL [default: misc/{Path(target.filename).stem}_unpacked.dll]",
    )
    def cmd(dll: Path | None, output: Path | None) -> None:
        packed = dll or target.resolve_packed()
        if packed is None:
            tried = "\n  ".join(str(p) for p in target.candidate_packed_paths())
            raise click.ClickException(
                f"Could not find {target.display}. Pass --dll PATH.\nTried:\n  {tried}"
            )
        out = output or target.misc_unpacked
        info = unpack_dll(packed, out)

        click.echo(f"Packed          : {info['packed']}")
        click.echo(f"POL1 raw offset : 0x{info['pol1_raw']:08X}")
        click.echo(f"Decompressed    : {info['text_len']:,} bytes")
        click.echo(f"First 16 bytes  : {info['text_head'].hex()}")
        click.echo(f"Image base      : 0x{info['image_base']:08X}")
        click.echo(f".text VA        : 0x{info['text_va']:08X}")

        try:
            import capstone
            import pefile

            md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
            md.detail = False
            pe = pefile.PE(str(out), fast_load=True)
            ts = next(s for s in pe.sections if s.Name.rstrip(b"\x00") == b".text")
            blob = out.read_bytes()[
                ts.PointerToRawData : ts.PointerToRawData + min(64, ts.SizeOfRawData)
            ]
            click.echo(f"\nFirst 10 instructions (VA 0x{info['text_va']:08X}):")
            for insn in list(md.disasm(blob, info["text_va"]))[:10]:
                click.echo(
                    f"  {insn.address:08X}: {insn.bytes.hex():<20}  "
                    f"{insn.mnemonic} {insn.op_str}"
                )
        except ImportError:
            click.echo("(capstone not available for verification)")

        click.echo(f"\nWrote unpacked  : {out}")
        click.echo(
            f"Load in Ghidra with image base 0x{info['image_base']:08X} "
            "(research only — not game-loadable)."
        )

    return cmd


# Pre-built commands for registration
cmd_ffximain = make_unpack_cmd(get_target("ffximain"))
cmd_polcore = make_unpack_cmd(get_target("polcore"))
cmd_app = make_unpack_cmd(get_target("app"))

"""
xi dll ffximain text-dump
=======================
Decompress FFXiMain.dll's POL1 section and write two flat output files:

  pol_decompressed.bin  — raw decompressed x86 bytes (3.2 MB)
  pol_decompressed.txt  — full linear disassembly (~45 MB, ~1M instructions)

Takes ~2–3 minutes to disassemble the full binary.

See docs/reference/ffximain.md for full background on the POL1 packer format.
"""

from __future__ import annotations
from pathlib import Path
import os

import click
import capstone

from xi.xi_config import XI_TOOLS_DIR
from xi.ffximain.xi_core import load_and_decompress


@click.command('text-dump')
@click.option(
    '--dll',
    type=click.Path(exists=True, path_type=Path),
    default=None,
    show_default=True,
    help='Path to FFXiMain.dll  [default: $XI_TOOLS_DIR/misc/FFXiMain.dll]',
)
@click.option(
    '--output-dir',
    type=click.Path(path_type=Path),
    default=None,
    show_default=True,
    help='Directory for output files  [default: $XI_TOOLS_DIR/research]',
)
def cmd(dll: Path | None, output_dir: Path | None) -> None:
    """Decompress POL1 -> pol_decompressed.bin + pol_decompressed.txt."""

    dll        = dll        or Path(XI_TOOLS_DIR) / 'misc' / 'FFXiMain.dll'
    output_dir = output_dir or Path(XI_TOOLS_DIR) / 'research'

    output_dir.mkdir(parents=True, exist_ok=True)
    bin_out = output_dir / 'pol_decompressed.bin'
    txt_out = output_dir / 'pol_decompressed.txt'

    _pe, text, _text_sec, image_base, text_va = load_and_decompress(dll)

    # ── Write raw binary ──────────────────────────────────────────────────────
    bin_out.write_bytes(text)
    print(f'Binary written: {bin_out}')

    # ── Write full disassembly ────────────────────────────────────────────────
    print(f'Disassembling to {txt_out} ...')
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = False

    CHUNK   = 4096
    written = 0

    with open(txt_out, 'w', encoding='utf-8') as out:
        out.write(f'; FFXiMain.dll decompressed .text section\n')
        out.write(f'; ImageBase: 0x{image_base:08X}   .text VA: 0x{text_va:08X}\n')
        out.write(f'; Decompressed size: {len(text):,} bytes\n\n')

        offset = 0
        while offset < len(text):
            chunk = text[offset:offset + CHUNK]
            for insn in md.disasm(chunk, text_va + offset):
                line = f'{insn.address:08X}  {insn.bytes.hex():<16}  {insn.mnemonic} {insn.op_str}\n'
                out.write(line)
                written += 1
            offset += CHUNK
            if offset % (CHUNK * 64) == 0:
                pct = offset * 100 // len(text)
                print(f'  {pct}% ({offset:,} / {len(text):,} bytes)  {written:,} instructions')

    sz = os.path.getsize(txt_out)
    print(f'Done. {written:,} instructions written to {txt_out}')
    print(f'File size: {sz / 1024 / 1024:.1f} MB')

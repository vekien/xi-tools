"""
xi ffximain unpack
====================
Decompress FFXiMain.dll's POL1 section and write a fully patched
FFXiMain_unpacked.dll with the .text section restored on disk.

The output is a valid Windows PE/DLL — load it in Ghidra or IDA Pro for full
static analysis with auto-analysis, symbol recovery, and decompiler output
(set image base 0x10000000).

RESEARCH ONLY — the game will not run the unpacked DLL.

See docs/reference/ffximain.md for full background on the POL1 packer format.
"""

from __future__ import annotations
from pathlib import Path
import shutil
import struct

import click
import pefile

from xi.xi_config import XI_TOOLS_DIR
from xi.ffximain.xi_core import load_and_decompress


@click.command('unpack')
@click.option(
    '--dll',
    type=click.Path(exists=True, path_type=Path),
    default=None,
    show_default=True,
    help='Path to FFXiMain.dll  [default: $XI_TOOLS_DIR/misc/FFXiMain.dll]',
)
@click.option(
    '--output',
    type=click.Path(path_type=Path),
    default=None,
    show_default=True,
    help='Output DLL path  [default: $XI_TOOLS_DIR/misc/FFXiMain_unpacked.dll]',
)
def cmd(dll: Path | None, output: Path | None) -> None:
    """Decompress POL1 -> FFXiMain_unpacked.dll (load in Ghidra/IDA)."""

    dll    = dll    or Path(XI_TOOLS_DIR) / 'misc' / 'FFXiMain.dll'
    output = output or Path(XI_TOOLS_DIR) / 'misc' / 'FFXiMain_unpacked.dll'

    output.parent.mkdir(parents=True, exist_ok=True)

    pe, text_data, text_sec, _image_base, _text_va = load_and_decompress(dll)

    print(f'POL1 raw offset : 0x{next(s for s in pe.sections if b"POL1" in s.Name).PointerToRawData:08X}')
    print(f'Decompressed    : {len(text_data):,} bytes')
    print(f'First 16 bytes  : {text_data[:16].hex()}')

    # Quick sanity check — first few instructions should be valid x86
    try:
        import capstone
        md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        md.detail = False
        base_va = pe.OPTIONAL_HEADER.ImageBase + text_sec.VirtualAddress
        print(f'\nFirst 10 instructions (VA 0x{base_va:08X}):')
        for insn in list(md.disasm(text_data[:64], base_va))[:10]:
            print(f'  {insn.address:08X}: {insn.bytes.hex():<20}  {insn.mnemonic} {insn.op_str}')
    except ImportError:
        print('(capstone not available for verification)')

    # ── Patch and write the output DLL ───────────────────────────────────────
    print(f'\nWriting unpacked DLL to: {output}')
    shutil.copy2(dll, output)

    pe2    = pefile.PE(str(output))
    text2  = next(s for s in pe2.sections if s.Name.rstrip(b'\x00') == b'.text')

    with open(output, 'r+b') as f:
        f.seek(text2.PointerToRawData if text2.PointerToRawData else 0x400)
        f.write(text_data)
        for s in pe2.sections:
            if s.Name.rstrip(b'\x00') == b'.text':
                hdr_off = s.get_file_offset()
                f.seek(hdr_off + 16)   # SizeOfRawData field
                f.write(struct.pack('<I', len(text_data)))
                print(f'Patched .text SizeOfRawData -> 0x{len(text_data):08X}')
                break

    print('Done.')
    print()
    print('Load FFXiMain_unpacked.dll in Ghidra (image base 0x10000000) or IDA Pro.')

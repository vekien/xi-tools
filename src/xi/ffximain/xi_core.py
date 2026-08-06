"""
Shared LZSS decompressor and PE loader for FFXiMain.dll POL1 section.

Sizes confirmed from the OEP stub (see docs/reference/ffximain.md):
  SRC_SIZE  0x1E57B0  — compressed data  (1,988,528 bytes)
  DST_SIZE  0x32716E  — decompressed     (3,305,838 bytes)
"""

from __future__ import annotations
from pathlib import Path

import pefile

SRC_SIZE = 0x1E57B0   # compressed data size
DST_SIZE = 0x32716E   # decompressed .text size


def lzss_decompress(src: bytes, dst_size: int) -> bytes:
    """
    LZSS decompressor reverse-engineered from the FFXiMain.dll OEP stub.
    Control byte processed MSB-first; bit=1 → literal, bit=0 → back-reference.
    offset=0 is the end-of-stream sentinel.
    """
    dst = bytearray(dst_size)
    si = di = 0
    while si < len(src) and di < dst_size:
        ctrl = src[si]; si += 1
        for _ in range(8):
            carry = (ctrl >> 7) & 1
            ctrl  = (ctrl << 1) & 0xFF
            if carry:                       # bit=1 → literal byte
                if si >= len(src): break
                dst[di] = src[si]; si += 1; di += 1
            else:                           # bit=0 → back-reference
                if si + 1 >= len(src): break
                b0 = src[si]; si += 1
                b1 = src[si]; si += 1
                offset = ((b0 << 8) | b1) & 0xFFF
                if offset == 0:             # end-of-stream sentinel
                    return bytes(dst[:di])
                length = (b0 >> 4) + 3
                for _ in range(length):
                    if di >= dst_size: break
                    dst[di] = dst[di - offset]; di += 1
            if di >= dst_size: break
    return bytes(dst[:di])


def load_and_decompress(dll_path: Path) -> tuple:
    """
    Load FFXiMain.dll, find the POL1 and .text sections, decompress .text.

    Returns:
        (pe, text_data, text_sec, image_base, text_va)
    """
    pe = pefile.PE(str(dll_path))
    pol1     = next(s for s in pe.sections if b'POL1' in s.Name)
    text_sec = next(s for s in pe.sections if s.Name.rstrip(b'\x00') == b'.text')

    with open(dll_path, 'rb') as f:
        f.seek(pol1.PointerToRawData)
        src = f.read(SRC_SIZE)

    print('Decompressing...')
    text_data = lzss_decompress(src, DST_SIZE)
    print(f'  {len(text_data):,} bytes')

    image_base = pe.OPTIONAL_HEADER.ImageBase
    text_va    = image_base + text_sec.VirtualAddress
    return pe, text_data, text_sec, image_base, text_va

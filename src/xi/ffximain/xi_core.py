"""
Shared LZSS codec and PE helpers for FFXiMain.dll POL1 section.

See docs/reference/ffximain.md for packer background.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pefile

# Defaults from one known build (overridden by detect_pol1_layout when possible)
SRC_SIZE = 0x1E57B0
DST_SIZE = 0x32716E


def lzss_decompress(src: bytes, dst_size: int) -> bytes:
    """
    LZSS decompressor reverse-engineered from the FFXiMain.dll OEP stub.
    Control byte processed MSB-first; bit=1 → literal, bit=0 → back-reference.
    offset=0 is the end-of-stream sentinel.
    """
    dst = bytearray(dst_size)
    si = di = 0
    while si < len(src) and di < dst_size:
        ctrl = src[si]
        si += 1
        for _ in range(8):
            carry = (ctrl >> 7) & 1
            ctrl = (ctrl << 1) & 0xFF
            if carry:  # bit=1 → literal byte
                if si >= len(src):
                    break
                dst[di] = src[si]
                si += 1
                di += 1
            else:  # bit=0 → back-reference
                if si + 1 >= len(src):
                    break
                b0 = src[si]
                si += 1
                b1 = src[si]
                si += 1
                offset = ((b0 << 8) | b1) & 0xFFF
                if offset == 0:  # end-of-stream sentinel
                    return bytes(dst[:di])
                length = (b0 >> 4) + 3
                for _ in range(length):
                    if di >= dst_size:
                        break
                    dst[di] = dst[di - offset]
                    di += 1
            if di >= dst_size:
                break
    return bytes(dst[:di])


def _find_match(raw: bytes, i: int, n: int, head: list[int], node_next: list[int]) -> tuple[int, int]:
    """Longest match at i using 3-byte hash chains. Returns (offset, length)."""
    max_len = min(18, n - i)
    if max_len < 3:
        return 0, 0
    key = raw[i] | (raw[i + 1] << 8) | (raw[i + 2] << 16)
    best_len = 0
    best_off = 0
    p = head[key & 0xFFFFF]
    window_min = i - 4095
    checks = 0
    while p >= 0 and p >= window_min and checks < 64:
        checks += 1
        off = i - p
        if off <= 0 or off > 4095:
            p = node_next[p]
            continue
        # quick reject
        if raw[p] != raw[i] or raw[p + 1] != raw[i + 1] or raw[p + 2] != raw[i + 2]:
            p = node_next[p]
            continue
        length = 3
        while length < max_len and raw[p + length] == raw[i + length]:
            length += 1
        if length > best_len:
            best_len = length
            best_off = off
            if best_len == max_len:
                break
        p = node_next[p]
    return best_off, best_len


def lzss_compress(raw: bytes) -> bytes:
    """
    LZSS compressor matching lzss_decompress() (hash chains + lazy matching).

    Control bytes MSB-first; bit=1 literal, bit=0 backref.
    Backref: b0=(len-3)<<4|(off>>8), b1=off&0xFF; off==0 is EOS.
    """
    n = len(raw)
    if n == 0:
        return bytes([0x00, 0x00, 0x00])

    # Hash chains: 20-bit key from 3 bytes
    head = [-1] * (1 << 20)
    node_next = [-1] * n

    def insert(pos: int) -> None:
        if pos + 2 >= n:
            return
        key = raw[pos] | (raw[pos + 1] << 8) | (raw[pos + 2] << 16)
        key &= 0xFFFFF
        node_next[pos] = head[key]
        head[key] = pos

    out = bytearray()
    i = 0
    # Pre-insert nothing; insert as we pass

    while i < n:
        items: list = []
        while len(items) < 8 and i < n:
            off, length = _find_match(raw, i, n, head, node_next)

            # Lazy matching: if next position has a strictly longer match,
            # emit a literal now and take the better match next item.
            if length >= 3 and i + 1 < n:
                # Insert i so i+1 can match through it
                insert(i)
                _o2, len2 = _find_match(raw, i + 1, n, head, node_next)
                if len2 > length:
                    items.append(("L", raw[i]))
                    i += 1
                    continue
                # Keep current match; i already inserted
                items.append(("R", off, length))
                for k in range(1, length):
                    insert(i + k)
                i += length
                continue

            if length >= 3:
                items.append(("R", off, length))
                for k in range(length):
                    insert(i + k)
                i += length
            else:
                items.append(("L", raw[i]))
                insert(i)
                i += 1

        ctrl = 0
        for idx, it in enumerate(items):
            if it[0] == "L":
                ctrl |= 1 << (7 - idx)
        out.append(ctrl)
        for it in items:
            if it[0] == "L":
                out.append(it[1])
            else:
                _t, off, length = it
                b0 = ((length - 3) << 4) | ((off >> 8) & 0x0F)
                b1 = off & 0xFF
                out.append(b0)
                out.append(b1)

    # EOS group
    out.append(0x00)
    out.append(0x00)
    out.append(0x00)
    return bytes(out)


@dataclass
class Pol1Layout:
    pol1_raw: int
    pol1_va: int
    payload_size: int  # max compressed bytes before stub
    stub_rva: int
    stub_raw: int
    dst_size: int  # expected decompressed .text size (from stub imm)
    text_va: int
    text_raw: int
    text_vsize: int
    image_base: int


def detect_pol1_layout(pe: pefile.PE, data: bytes | None = None) -> Pol1Layout:
    """Derive POL1 payload size and dst size from PE + OEP stub immediates."""
    pol1 = next(s for s in pe.sections if b"POL1" in s.Name)
    text = next(s for s in pe.sections if s.Name.rstrip(b"\x00") == b".text")
    image_base = pe.OPTIONAL_HEADER.ImageBase
    ep_rva = pe.OPTIONAL_HEADER.AddressOfEntryPoint
    pol1_va = image_base + pol1.VirtualAddress
    stub_rva = ep_rva
    stub_raw = pe.get_offset_from_rva(stub_rva)
    payload_size = stub_rva - pol1.VirtualAddress
    if payload_size <= 0:
        payload_size = SRC_SIZE

    # Parse stub for mov eax, imm32 (dst size) and push imm32 (src size)
    dst_size = text.Misc_VirtualSize or DST_SIZE
    if data is None:
        # pe.__data__ may be available
        data = pe.__data__ if hasattr(pe, "__data__") and pe.__data__ else None
    if data is not None and stub_raw and stub_raw > 0:
        stub = data[stub_raw : stub_raw + 0x100]
        # look for B8 xx xx xx xx (mov eax, imm32) near start — dst size
        # and 68 xx xx xx xx (push imm32) — src size
        for i in range(min(len(stub) - 5, 80)):
            if stub[i] == 0xB8:
                imm = int.from_bytes(stub[i + 1 : i + 5], "little")
                if 0x100000 < imm < 0x800000:
                    dst_size = imm
            if stub[i] == 0x68:
                imm = int.from_bytes(stub[i + 1 : i + 5], "little")
                if 0x10000 < imm < 0x400000 and imm == payload_size:
                    payload_size = imm

    return Pol1Layout(
        pol1_raw=pol1.PointerToRawData,
        pol1_va=pol1_va,
        payload_size=payload_size,
        stub_rva=stub_rva,
        stub_raw=stub_raw,
        dst_size=dst_size,
        text_va=image_base + text.VirtualAddress,
        text_raw=text.PointerToRawData,
        text_vsize=text.Misc_VirtualSize,
        image_base=image_base,
    )


def load_and_decompress(dll_path: Path) -> tuple:
    """
    Load FFXiMain.dll, find the POL1 and .text sections, decompress .text.

    Returns:
        (pe, text_data, text_sec, image_base, text_va)
    """
    raw = Path(dll_path).read_bytes()
    pe = pefile.PE(data=raw, fast_load=True)
    layout = detect_pol1_layout(pe, raw)
    pol1 = next(s for s in pe.sections if b"POL1" in s.Name)
    text_sec = next(s for s in pe.sections if s.Name.rstrip(b"\x00") == b".text")

    src = raw[layout.pol1_raw : layout.pol1_raw + layout.payload_size]
    print("Decompressing...")
    text_data = lzss_decompress(src, layout.dst_size)
    print(f"  {len(text_data):,} bytes")

    image_base = pe.OPTIONAL_HEADER.ImageBase
    text_va = image_base + text_sec.VirtualAddress
    return pe, text_data, text_sec, image_base, text_va

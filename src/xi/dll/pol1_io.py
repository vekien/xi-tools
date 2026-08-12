"""POL1 unpack/pack PE I/O shared by all client DLLs."""
from __future__ import annotations

import struct
from pathlib import Path

import pefile

from xi.ffximain.xi_core import (
    detect_pol1_layout,
    load_and_decompress,
    lzss_compress,
    lzss_decompress,
)


def write_unpacked_pe(packed_dll: Path, text_data: bytes, output: Path) -> None:
    """Write a Ghidra-loadable PE with .text restored on disk.

    Handles the common packed layout where ``.text`` has ``PointerToRawData=0``
    / ``SizeOfRawData=0`` by inserting a raw .text blob and shifting later
    sections' file offsets.
    """
    raw = bytearray(packed_dll.read_bytes())
    pe0 = pefile.PE(data=bytes(raw), fast_load=True)
    text = next(s for s in pe0.sections if s.Name.rstrip(b"\x00") == b".text")
    fa = pe0.OPTIONAL_HEADER.FileAlignment or 0x200
    text_raw_size = (len(text_data) + fa - 1) // fa * fa

    if text.PointerToRawData and text.SizeOfRawData >= len(text_data):
        # Simple in-place overwrite (FFXiMain-style after a prior unpack, etc.)
        output.write_bytes(raw)
        with open(output, "r+b") as f:
            f.seek(text.PointerToRawData)
            f.write(text_data)
            hdr = text.get_file_offset()
            f.seek(hdr + 16)
            f.write(struct.pack("<I", len(text_data)))
        return

    # Insert .text raw at the first existing section file offset (usually 0x400)
    secs_with_raw = [s for s in pe0.sections if s.PointerToRawData > 0]
    if not secs_with_raw:
        raise ValueError("PE has no sections with file data; cannot place .text")
    first_raw = min(s.PointerToRawData for s in secs_with_raw)

    new = bytearray(raw[:first_raw])
    new += text_data + b"\x00" * (text_raw_size - len(text_data))
    new += raw[first_raw:]

    pe1 = pefile.PE(data=bytes(new), fast_load=True)
    for s in pe1.sections:
        name = s.Name.rstrip(b"\x00")
        hoff = s.get_file_offset()
        if name == b".text":
            struct.pack_into("<I", new, hoff + 16, text_raw_size)  # SizeOfRawData
            struct.pack_into("<I", new, hoff + 20, first_raw)  # PointerToRawData
            # CODE | EXECUTE | READ
            struct.pack_into("<I", new, hoff + 36, 0x60000020)
        elif s.PointerToRawData >= first_raw and s.PointerToRawData != 0:
            struct.pack_into("<I", new, hoff + 20, s.PointerToRawData + text_raw_size)

    # Clear PE checksum
    struct.pack_into("<I", new, pe1.DOS_HEADER.e_lfanew + 0x18 + 0x40, 0)
    output.write_bytes(new)


def unpack_dll(packed_dll: Path, output: Path) -> dict:
    """Decompress POL1 .text and write unpacked PE. Returns summary dict."""
    output.parent.mkdir(parents=True, exist_ok=True)
    pe, text_data, text_sec, image_base, text_va = load_and_decompress(packed_dll)
    pol1 = next(s for s in pe.sections if b"POL1" in s.Name)
    write_unpacked_pe(packed_dll, text_data, output)
    return {
        "packed": packed_dll,
        "output": output,
        "pol1_raw": pol1.PointerToRawData,
        "text_len": len(text_data),
        "text_head": text_data[:16],
        "image_base": image_base,
        "text_va": text_va,
        "text_vsize": text_sec.Misc_VirtualSize,
    }


def pack_dll(
    template_dll: Path,
    output: Path,
    *,
    unpacked: Path | None = None,
    text_bin: Path | None = None,
    verify: bool = True,
) -> dict:
    """Re-compress .text into POL1 using template PE shell. Returns summary dict."""
    import click

    if not unpacked and not text_bin:
        raise click.UsageError("Provide --unpacked and/or --text-bin")

    output.parent.mkdir(parents=True, exist_ok=True)
    tmpl_raw = template_dll.read_bytes()
    tmpl_pe = pefile.PE(data=tmpl_raw, fast_load=True)
    layout = detect_pol1_layout(tmpl_pe, tmpl_raw)

    if text_bin:
        text_data = text_bin.read_bytes()
    else:
        upe = pefile.PE(str(unpacked), fast_load=True)
        utext = next(s for s in upe.sections if s.Name.rstrip(b"\x00") == b".text")
        uraw = unpacked.read_bytes()
        n = utext.Misc_VirtualSize or utext.SizeOfRawData
        avail = utext.SizeOfRawData
        take = min(n, avail, layout.dst_size) if layout.dst_size else min(n, avail)
        text_data = uraw[utext.PointerToRawData : utext.PointerToRawData + take]

    if layout.dst_size and len(text_data) < layout.dst_size:
        text_data = text_data + bytes(layout.dst_size - len(text_data))
    elif layout.dst_size and len(text_data) > layout.dst_size:
        click.echo(
            f"warn: .text is {len(text_data)} bytes > stub dst {layout.dst_size}; truncating"
        )
        text_data = text_data[: layout.dst_size]

    orig_payload = tmpl_raw[layout.pol1_raw : layout.pol1_raw + layout.payload_size]
    try:
        orig_text = lzss_decompress(orig_payload, layout.dst_size or len(text_data))
    except Exception:
        orig_text = b""

    if orig_text == text_data:
        compressed = orig_payload
        identity = True
    else:
        identity = False
        compressed = lzss_compress(text_data)
        if len(compressed) > layout.payload_size:
            raise click.ClickException(
                f"Compressed .text ({len(compressed)} bytes) exceeds POL1 payload "
                f"budget ({layout.payload_size} bytes)."
            )

    if verify:
        check = lzss_decompress(compressed, layout.dst_size or len(text_data) + 0x1000)
        if check != text_data and check != text_data[: len(check)]:
            mism = next(
                (i for i in range(min(len(check), len(text_data))) if check[i] != text_data[i]),
                None,
            )
            raise click.ClickException(
                f"Round-trip mismatch at offset {mism} "
                f"(got {len(check)} bytes, want {len(text_data)})"
            )
        if len(check) < len(text_data) and any(text_data[len(check) :]):
            raise click.ClickException(
                f"Decompressed {len(check)} < {len(text_data)} with non-zero tail"
            )

    import shutil

    shutil.copy2(template_dll, output)
    with open(output, "r+b") as f:
        f.seek(layout.pol1_raw)
        f.write(compressed)
        pad = layout.payload_size - len(compressed)
        if pad > 0:
            f.write(b"\x00" * pad)
        pe_out = pefile.PE(str(output), fast_load=True)
        for s in pe_out.sections:
            if s.Name.rstrip(b"\x00") == b".text":
                hdr = s.get_file_offset()
                f.seek(hdr + 16)
                f.write(struct.pack("<I", 0))
                break

    return {
        "template": template_dll,
        "output": output,
        "text_len": len(text_data),
        "compressed_len": len(compressed),
        "payload_max": layout.payload_size,
        "stub_rva": layout.stub_rva,
        "dst_size": layout.dst_size,
        "identity": identity,
        "size": output.stat().st_size,
        "ep_rva": pefile.PE(str(output), fast_load=True).OPTIONAL_HEADER.AddressOfEntryPoint,
    }

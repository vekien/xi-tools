"""
xi ffximain pack
================
Re-compress a restored .text blob into POL1 and write a packed FFXiMain.dll
the game can load (OEP stays on the POL1 unpacker stub).

Typical round-trip (no edits)::

    xi ffximain unpack --dll game/FFXiMain.dll --output /tmp/u.dll
    xi ffximain pack   --unpacked /tmp/u.dll --template game/FFXiMain.dll \\
                       --output /tmp/packed.dll

Or pack from a raw .text dump + original packed template.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import struct

import click
import pefile

from xi.xi_config import XI_TOOLS_DIR
from xi.ffximain.xi_core import (
    detect_pol1_layout,
    lzss_compress,
    lzss_decompress,
    load_and_decompress,
)


@click.command("pack")
@click.option(
    "--template",
    "template_dll",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Original packed FFXiMain.dll (PE shell + POL1 stub). "
    "[default: $XI_TOOLS_DIR/misc/FFXiMain.dll]",
)
@click.option(
    "--unpacked",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Unpacked DLL whose .text will be re-compressed. "
    "If omitted, --text-bin is required.",
)
@click.option(
    "--text-bin",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Raw decompressed .text bytes (alternative to --unpacked).",
)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="Output packed DLL path "
    "[default: $XI_TOOLS_DIR/misc/FFXiMain_repacked.dll]",
)
@click.option(
    "--verify/--no-verify",
    default=True,
    show_default=True,
    help="Decompress result and compare to input .text.",
)
def cmd(
    template_dll: Path | None,
    unpacked: Path | None,
    text_bin: Path | None,
    output: Path | None,
    verify: bool,
) -> None:
    """Compress .text into POL1 -> packed FFXiMain.dll (game-loadable)."""

    template_dll = template_dll or Path(XI_TOOLS_DIR) / "misc" / "FFXiMain.dll"
    output = output or Path(XI_TOOLS_DIR) / "misc" / "FFXiMain_repacked.dll"
    output.parent.mkdir(parents=True, exist_ok=True)

    if not unpacked and not text_bin:
        raise click.UsageError("Provide --unpacked and/or --text-bin")

    tmpl_raw = template_dll.read_bytes()
    tmpl_pe = pefile.PE(data=tmpl_raw, fast_load=True)
    layout = detect_pol1_layout(tmpl_pe, tmpl_raw)

    # Source .text bytes
    if text_bin:
        text_data = text_bin.read_bytes()
    else:
        upe = pefile.PE(str(unpacked), fast_load=True)
        utext = next(s for s in upe.sections if s.Name.rstrip(b"\x00") == b".text")
        uraw = unpacked.read_bytes()
        # Prefer virtual size; fall back to raw
        n = utext.Misc_VirtualSize or utext.SizeOfRawData
        # If unpacked has full raw .text, use min(layout.dst_size, available)
        avail = utext.SizeOfRawData
        take = min(n, avail, layout.dst_size) if layout.dst_size else min(n, avail)
        text_data = uraw[utext.PointerToRawData : utext.PointerToRawData + take]

    # Pad/trim to stub's expected dst size when known
    if layout.dst_size and len(text_data) < layout.dst_size:
        text_data = text_data + bytes(layout.dst_size - len(text_data))
    elif layout.dst_size and len(text_data) > layout.dst_size:
        click.echo(
            f"warn: .text is {len(text_data)} bytes > stub dst {layout.dst_size}; truncating"
        )
        text_data = text_data[: layout.dst_size]

    print(f"Template        : {template_dll}")
    print(f".text in        : {len(text_data):,} bytes")
    print(f"POL1 payload max: {layout.payload_size:#x} ({layout.payload_size:,}) bytes")
    print(f"Stub RVA        : {layout.stub_rva:#x}")
    print(f"Stub dst size   : {layout.dst_size:#x}")

    # Identity fast-path: if .text matches template's decompressed blob, reuse
    # the original POL1 payload (guaranteed to fit + bit-identical pack).
    orig_payload = tmpl_raw[layout.pol1_raw : layout.pol1_raw + layout.payload_size]
    try:
        orig_text = lzss_decompress(orig_payload, layout.dst_size or len(text_data))
    except Exception:
        orig_text = b""
    if orig_text == text_data or (
        len(orig_text) == len(text_data) and orig_text == text_data
    ):
        print("Identity match with template .text — reusing original POL1 payload")
        compressed = orig_payload.rstrip(b"\x00")
        # Keep full payload region zeros after; write path pads
        if len(compressed) == 0:
            compressed = orig_payload
        # Prefer exact original payload bytes (including any trailing zeros before stub)
        compressed = orig_payload
    else:
        print("Compressing...")
        compressed = lzss_compress(text_data)
        print(f"  compressed    : {len(compressed):,} bytes")

        if len(compressed) > layout.payload_size:
            raise click.ClickException(
                f"Compressed .text ({len(compressed)} bytes) exceeds POL1 payload "
                f"budget ({layout.payload_size} bytes). Reduce patches or improve compressor."
            )

    if verify:
        print("Verifying round-trip decompress...")
        check = lzss_decompress(compressed, layout.dst_size or len(text_data) + 0x1000)
        if check != text_data[: len(check)] and check != text_data:
            # Allow exact match on original length
            if check != text_data.rstrip(b"\x00") and check != text_data:
                # strict
                mism = next(
                    (i for i in range(min(len(check), len(text_data))) if check[i] != text_data[i]),
                    None,
                )
                raise click.ClickException(
                    f"Round-trip mismatch at offset {mism} "
                    f"(got {len(check)} bytes, want {len(text_data)})"
                )
        if len(check) != len(text_data):
            # decompress may stop at EOS with exact length
            if check != text_data[: len(check)]:
                raise click.ClickException("Round-trip length/content mismatch")
            if len(check) < len(text_data) and any(text_data[len(check) :]):
                raise click.ClickException(
                    f"Decompressed {len(check)} < {len(text_data)} with non-zero tail"
                )
        print("  OK")

    # Write: copy template, overwrite POL1 payload, force .text SizeOfRawData=0
    shutil.copy2(template_dll, output)
    with open(output, "r+b") as f:
        # POL1 payload
        f.seek(layout.pol1_raw)
        f.write(compressed)
        # zero-fill remainder of payload region (up to stub)
        pad = layout.payload_size - len(compressed)
        if pad > 0:
            f.write(b"\x00" * pad)

        # .text on-disk size = 0 (packed form)
        pe_out = pefile.PE(str(output), fast_load=True)
        for s in pe_out.sections:
            if s.Name.rstrip(b"\x00") == b".text":
                hdr = s.get_file_offset()
                f.seek(hdr + 16)  # SizeOfRawData
                f.write(struct.pack("<I", 0))
                # PointerToRawData can stay; loader uses virtual size
                print(f"Patched .text SizeOfRawData -> 0")
                break

    # Final PE sanity
    pe_check = pefile.PE(str(output), fast_load=True)
    print(f"\nWrote packed DLL : {output}")
    print(f"  size           : {output.stat().st_size:,}")
    print(f"  EP RVA         : {pe_check.OPTIONAL_HEADER.AddressOfEntryPoint:#x}")
    print("Done. Drop over game FFXiMain.dll to test (keep a backup).")

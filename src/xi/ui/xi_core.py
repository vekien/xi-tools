"""
FFXI UI texture extraction from lobb/menu container DAT files.

Container format (lobb / menu magic):
  - 32-byte file header  (magic + version + padding)
  - One or more chunks, each containing texture entries

Texture entry structure (57 bytes before each xTXD block):
  Offset  Size  Field
  -57     1     type marker = 0xa1
  -56     8     parent name (space-padded ASCII)
  -48     8     texture name (space-padded ASCII)
  -40     4     header_size = 40  (LE uint32, always 0x28)
  -36     4     width             (LE uint32)
  -32     4     height            (LE uint32)
  -28     2     mip_count         (LE uint16, usually 1)
  -26     2     pixel_format      (LE uint16)
  -24     20    padding (zeros)
  -4      4     extra = 0x10      (LE uint32)

xTXD block (immediately follows entry header):
  Offset  Size  Field
   0      4     magic "1TXD" / "3TXD" / "5TXD"
   4      4     data_size  (LE uint32) — total compressed pixel bytes
   8      4     pitch      (LE uint32) — bytes per row of DXT blocks
  12      N     pixel data (N = data_size)

TXD magic encodes the DXT compression format:
  "1TXD" → DXT1   (4 bits/px, 1-bit alpha optional)
  "3TXD" → DXT3   (8 bits/px, explicit 4-bit alpha)
  "5TXD" → DXT5   (8 bits/px, interpolated alpha)

pixel_format values observed so far:
  4  = DXT1 with 1-bit punch-through alpha
  8  = DXT1 fully opaque (or opaque DXT3/DXT5 variant)
"""

import re
import struct
from dataclasses import dataclass
from pathlib import Path

# ── constants ────────────────────────────────────────────────────────────────

ENTRY_HEADER_SIZE = 57      # bytes before xTXD magic

# All known TXD magic values → DDS FourCC
TXD_MAGICS: dict[bytes, bytes] = {
    b'1TXD': b'DXT1',
    b'3TXD': b'DXT3',
    b'5TXD': b'DXT5',
}
FOURCC_TO_TXD = {v: k for k, v in TXD_MAGICS.items()}

TXD_HEADER_SIZE = 12        # magic(4) + data_size(4) + pitch(4)

# pixel_format values
FMT_DXT1_ALPHA  = 4     # DXT1 with 1-bit punch-through alpha
FMT_DXT1_OPAQUE = 8     # DXT1 fully opaque (also used for DXT3/DXT5 opaque)

# DDS constants
DDS_MAGIC        = b'DDS '
DDSD_CAPS        = 0x00000001
DDSD_HEIGHT      = 0x00000002
DDSD_WIDTH       = 0x00000004
DDSD_PIXELFORMAT = 0x00001000
DDSD_LINEARSIZE  = 0x00080000
DDSCAPS_TEXTURE  = 0x00001000
DDPF_FOURCC      = 0x00000004


# ── data types ───────────────────────────────────────────────────────────────

@dataclass
class TextureEntry:
    parent:       str
    name:         str
    width:        int
    height:       int
    mip_count:    int
    pixel_format: int
    data_size:    int
    pitch:        int
    txd_magic:    bytes        # b'1TXD', b'3TXD', or b'5TXD'
    pixels:       bytes
    txd_offset:   int          # byte offset of xTXD magic in the source DAT


@dataclass
class DdsTexture:
    width: int
    height: int
    fourcc: bytes
    pixels: bytes


# ── parsing ──────────────────────────────────────────────────────────────────

# Combined pattern matching any of the known TXD magic values
_TXD_PATTERN = re.compile(b'[135]TXD')


def parse_textures(data: bytes) -> list[TextureEntry]:
    """Return all texture entries found in a DAT file."""
    entries = []
    for m in _TXD_PATTERN.finditer(data):
        txd_magic = m.group()
        if txd_magic not in TXD_MAGICS:
            continue

        txd_off   = m.start()
        entry_off = txd_off - ENTRY_HEADER_SIZE
        if entry_off < 0:
            continue
        if data[entry_off] != 0xa1:
            continue

        parent = data[entry_off + 1 : entry_off + 9].rstrip(b' ').decode('ascii', 'replace')
        name   = data[entry_off + 9 : entry_off + 17].rstrip(b' ').decode('ascii', 'replace')

        _hdr_size, width, height, mip_count, pixel_fmt = struct.unpack_from(
            '<IIIHH', data, entry_off + 17
        )

        data_size, pitch = struct.unpack_from('<II', data, txd_off + 4)
        pixels = data[txd_off + TXD_HEADER_SIZE : txd_off + TXD_HEADER_SIZE + data_size]

        entries.append(TextureEntry(
            parent       = parent,
            name         = name,
            width        = width,
            height       = height,
            mip_count    = mip_count,
            pixel_format = pixel_fmt,
            data_size    = data_size,
            pitch        = pitch,
            txd_magic    = txd_magic,
            pixels       = pixels,
            txd_offset   = txd_off,
        ))
    return entries


def output_file_names(entries: list[TextureEntry]) -> list[str]:
    """Return the same deduplicated filenames used by ui extract."""
    names = []
    seen: dict[str, int] = {}
    for entry in entries:
        base = entry.name or 'unnamed'
        seen[base] = seen.get(base, 0) + 1
        suffix = f'_{seen[base]}' if seen[base] > 1 else ''
        names.append(f'{base}{suffix}.dds')
    return names


def parse_dds(path: Path) -> DdsTexture:
    data = path.read_bytes()
    if len(data) < 128 or data[:4] != DDS_MAGIC:
        raise ValueError(f'Not a valid DDS file: {path}')

    height = struct.unpack_from('<I', data, 12)[0]
    width = struct.unpack_from('<I', data, 16)[0]
    fourcc = data[84:88]
    if fourcc == b'DX10':
        raise ValueError(f'DX10 DDS files are not supported: {path}')
    if fourcc not in FOURCC_TO_TXD:
        raise ValueError(f'Unsupported DDS format {fourcc.decode("ascii", "replace")}: {path}')

    return DdsTexture(
        width=width,
        height=height,
        fourcc=fourcc,
        pixels=data[128:],
    )


def txd_magic_for_fourcc(fourcc: bytes) -> bytes:
    return FOURCC_TO_TXD[fourcc]


def pitch_for(width: int, fourcc: bytes) -> int:
    bytes_per_block = 8 if fourcc == b'DXT1' else 16
    return ((width + 3) // 4) * bytes_per_block


def replace_texture(data: bytearray, entry: TextureEntry, dds: DdsTexture) -> None:
    if dds.width != entry.width or dds.height != entry.height:
        raise ValueError(
            f'{entry.name or "unnamed"}: DDS is {dds.width}x{dds.height} but DAT entry is '
            f'{entry.width}x{entry.height}'
        )

    txd_magic = txd_magic_for_fourcc(dds.fourcc)
    data_size = len(dds.pixels)
    pitch = pitch_for(dds.width, dds.fourcc)
    expected_size = ((dds.height + 3) // 4) * pitch
    if data_size != expected_size:
        raise ValueError(
            f'{entry.name or "unnamed"}: DDS payload size {data_size} does not match expected '
            f'{expected_size} for {dds.fourcc.decode()} {dds.width}x{dds.height}'
        )

    entry_off = entry.txd_offset - ENTRY_HEADER_SIZE
    struct.pack_into('<IIHH', data, entry_off + 21, dds.width, dds.height, entry.mip_count, entry.pixel_format)
    struct.pack_into('<4sII', data, entry.txd_offset, txd_magic, data_size, pitch)

    pixel_start = entry.txd_offset + TXD_HEADER_SIZE
    pixel_end = pixel_start + entry.data_size
    data[pixel_start:pixel_end] = dds.pixels


# ── DDS output ───────────────────────────────────────────────────────────────

def _dds_header(width: int, height: int, data_size: int, fourcc: bytes = b'DXT1') -> bytes:
    """Build a 128-byte DDS file prefix (magic + 124-byte DDS_HEADER)."""
    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT | DDSD_LINEARSIZE

    # DDS_PIXELFORMAT (32 bytes)
    pf = struct.pack('<II4sIIIII',
        32,             # dwSize
        DDPF_FOURCC,    # dwFlags
        fourcc,         # dwFourCC
        0, 0, 0, 0, 0,  # bit counts / masks (unused for FourCC formats)
    )

    # DDS_HEADER (124 bytes)
    hdr = struct.pack('<IIIII',
        124,            # dwSize
        flags,          # dwFlags
        height,         # dwHeight
        width,          # dwWidth
        data_size,      # dwPitchOrLinearSize
    )
    hdr += struct.pack('<II', 0, 0)     # dwDepth=0, dwMipMapCount=0
    hdr += b'\x00' * 44                 # dwReserved1[11]
    hdr += pf
    hdr += struct.pack('<IIIII',
        DDSCAPS_TEXTURE, 0, 0, 0, 0,   # dwCaps … dwReserved2
    )
    return DDS_MAGIC + hdr


def compression_name(entry: TextureEntry) -> str:
    """Human-readable compression name for display."""
    fourcc = TXD_MAGICS.get(entry.txd_magic, b'????').decode()
    if entry.txd_magic == b'1TXD':
        if entry.pixel_format == FMT_DXT1_ALPHA:
            return 'DXT1+alpha'
        return 'DXT1'
    return fourcc


def fourcc_for(entry: TextureEntry) -> bytes:
    """Return the DDS FourCC bytes for this texture entry."""
    return TXD_MAGICS.get(entry.txd_magic, b'DXT1')


def write_dds(entry: TextureEntry, out_path: Path) -> None:
    """Write a texture entry as a .dds file."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    header = _dds_header(entry.width, entry.height, entry.data_size, fourcc_for(entry))
    out_path.write_bytes(header + entry.pixels)

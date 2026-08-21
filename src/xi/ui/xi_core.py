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

from xi.common.xi_section import (MAX_SECTION_UNITS, SECTION_SIZE_SHIFT, SECTION_UNIT,
                                  encode_section_meta)

# ── constants ────────────────────────────────────────────────────────────────

ENTRY_HEADER_SIZE = 57      # bytes before xTXD magic
CHUNK_HEADER_SIZE = 16      # 0x20 chunk header preceding the entry header
TEXTURE_SECTION_TYPE = 0x20 # section type code of a UI texture chunk

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


def chunk_bytes_for(data_size: int) -> int:
    """Padded byte length of the 0x20 chunk that wraps a texture of `data_size`."""
    raw = CHUNK_HEADER_SIZE + ENTRY_HEADER_SIZE + TXD_HEADER_SIZE + data_size
    return (raw + SECTION_UNIT - 1) // SECTION_UNIT * SECTION_UNIT


def _resize_chunk(data: bytearray, entry: TextureEntry, new_data_size: int) -> None:
    """Grow or shrink the 0x20 chunk holding `entry` and fix its section header.

    A format change (DXT1 <-> DXT3/DXT5) halves or doubles the pixel payload, so
    the chunk's 19-bit size field must move with it — the client walks chunks by
    that field alone, and a stale one desynchronises every chunk after this point.
    Callers must patch entries back-to-front so earlier txd_offsets stay valid.
    """
    name = entry.name or 'unnamed'
    chunk_off = entry.txd_offset - ENTRY_HEADER_SIZE - CHUNK_HEADER_SIZE
    if chunk_off < 0:
        raise ValueError(f'{name}: no room for a chunk header before the entry')

    info = struct.unpack_from('<I', data, chunk_off + 4)[0]
    type_code = info & 0x7F
    old_bytes = ((info >> 7) & MAX_SECTION_UNITS) * SECTION_UNIT
    if type_code != TEXTURE_SECTION_TYPE or old_bytes != chunk_bytes_for(entry.data_size):
        raise ValueError(
            f'{name}: chunk header at 0x{chunk_off:06x} does not describe this texture '
            f'(type 0x{type_code:02x}, {old_bytes} bytes; expected type 0x20, '
            f'{chunk_bytes_for(entry.data_size)} bytes) - refusing to resize it'
        )

    new_bytes = chunk_bytes_for(new_data_size)
    headers_end = CHUNK_HEADER_SIZE + ENTRY_HEADER_SIZE + TXD_HEADER_SIZE
    headers = bytes(data[chunk_off:chunk_off + headers_end])
    # Pixels and padding are rewritten by the caller; zero-fill them for now.
    data[chunk_off:chunk_off + old_bytes] = headers + bytes(new_bytes - headers_end)
    struct.pack_into('<I', data, chunk_off + 4,
                     encode_section_meta(new_bytes, TEXTURE_SECTION_TYPE, info,
                                         what=f'0x20 texture chunk {name!r}'))


# -- layout (sprite mapping) records ------------------------------------------

# Sprite records live in the container's 0x31 chunk (`lobb`). Each is introduced by
# a 4-byte marker, an 8-byte parent tag and an 8-byte texture name; the payload runs
# to the next marker. A 41-byte payload decodes as:
#
#   [dest quad: 4 x,y u16 screen points][src_w][src_h][src_x][src_y]
#
# Corners are inclusive, so a whole 256x256 texture reads as src 255x255 @ (0,0).
# That is why enlarging a texture alone changes nothing on screen: the source rect
# still names the old extent, and the client samples that sub-region.
#
# Confirmed empirically on ROM/119/50: patching 255x255 -> 1023x1023 on the two
# records flanking the `20logo` name made a 1024x1024 texture render in full, at the
# same on-screen size. The name-to-payload binding is NOT reliable in either
# direction (both readings leave records whose src rect falls outside their named
# texture), so only rects that exactly match the old full-texture extent are
# rewritten, and only next to a matching name.

LAYOUT_SECTION_TYPE = 0x31
_LAYOUT_MARKER = re.compile(bytes([1, 0]))   # + type, subtype, parent[8], name[8]
HEADER_MAGIC = bytes([1, 0])
SRC_RECT_OFFSET = 16        # byte offset of src_w within a payload
_MIN_PAYLOAD = SRC_RECT_OFFSET + 8


def _layout_span(data: bytes) -> tuple[int, int] | None:
    """Byte range of the container's layout (0x31) chunk, or None."""
    off = 0x20
    while off + CHUNK_HEADER_SIZE <= len(data):
        info = struct.unpack_from('<I', data, off + 4)[0]
        nbytes = ((info >> SECTION_SIZE_SHIFT) & MAX_SECTION_UNITS) * SECTION_UNIT
        if nbytes < CHUNK_HEADER_SIZE or off + nbytes > len(data):
            return None
        if info & 0x7F == LAYOUT_SECTION_TYPE:
            return off, off + nbytes
        off += nbytes
    return None


def parse_layout_records(data: bytes) -> list[tuple[str, int, int]]:
    """Return (texture_name, payload_offset, payload_length) for each sprite record."""
    span = _layout_span(data)
    if span is None:
        return []
    start, end = span
    printable = range(0x20, 0x7f)

    # A record header is 01 00 <type> <subtype> then an 8-byte parent tag and an
    # 8-byte texture name. Earlier revisions hardcoded the header as 01 00 01 01,
    # which matched only 666 of the 1230 records in ROM/119/50 and silently merged
    # the rest into oversized payloads -- the bug that corrupted the expansion
    # banners. type/subtype are small integers; the two 8-byte tags are ASCII.
    marks = []
    pos = start
    while True:
        pos = data.find(HEADER_MAGIC, pos, end)
        if pos < 0:
            break
        head = pos + len(HEADER_MAGIC)
        if head + 2 + 16 > end:
            break
        if data[head] < 0x10 and data[head + 1] < 0x10:
            tags = data[head + 2:head + 18]
            if all(b in printable for b in tags):
                marks.append((pos, head + 18, tags[8:].rstrip(b' ').decode('ascii', 'replace')))
                pos = head + 18
                continue
        pos += 1

    out = []
    for i, (_hdr, payload, name) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else end
        out.append((name, payload, stop - payload))
    return out


def rescale_layout_rects(data: bytearray, tex_name: str, old_size: tuple[int, int],
                         new_size: tuple[int, int]) -> tuple[list, int]:
    """Point `tex_name`'s whole-texture sprite rects at its new pixel size.

    A rect counts as whole-texture when it is anchored at (0,0) and covers a square
    power-of-two extent -- `255x255`, `511x511`, `1023x1023`. That is deliberately
    looser than "matches `old_size` exactly", because a texture may already have been
    enlarged by another tool without its rects being fixed, which is precisely the
    state this repairs. Anything else is an atlas sub-rect and is left alone.

    Returns (list of (payload_offset, old_wh, new_wh), count_of_sub_rects_skipped).
    """
    new_w, new_h = new_size
    records = parse_layout_records(data)
    changed: list[tuple[int, tuple[int, int], tuple[int, int]]] = []
    partial = 0

    def whole(w: int, h: int) -> bool:
        return w == h and (w + 1) > 1 and (w + 1) & w == 0

    for i, (_name, off, length) in enumerate(records):
        if length < _MIN_PAYLOAD:
            continue
        # The payload/name binding is ambiguous, so accept either neighbour.
        neighbours = {records[i][0]}
        if i + 1 < len(records):
            neighbours.add(records[i + 1][0])
        if tex_name not in neighbours:
            continue

        sw, sh, sx, sy = struct.unpack_from('<4H', data, off + SRC_RECT_OFFSET)
        if (sx, sy) == (0, 0) and whole(sw, sh):
            if (sw, sh) != (new_w - 1, new_h - 1):
                struct.pack_into('<2H', data, off + SRC_RECT_OFFSET, new_w - 1, new_h - 1)
                changed.append((off, (sw + 1, sh + 1), (new_w, new_h)))
        elif sw < 0x8000 and sh < 0x8000:
            partial += 1

    return changed, partial


def replace_texture(data: bytearray, entry: TextureEntry, dds: DdsTexture,
                    allow_resize: bool = False) -> None:
    """Patch `dds` over `entry` in `data`, resizing the chunk if the payload changed.

    `data` is mutated in place and may change length. Patch entries back-to-front
    (see `_resize_chunk`) so a resize never invalidates a txd_offset still to come.

    `allow_resize` permits a different pixel size. Safe for a sprite that maps as a
    whole texture (the `lobb` record for `20logo` drives 256x256 and 512x512 alike),
    but NOT for an atlas: glyph and icon sheets carry source rectangles in texture
    pixels, and those live in a separate layout chunk this function never touches.
    """
    if dds.width != entry.width or dds.height != entry.height:
        if not allow_resize:
            raise ValueError(
                f'{entry.name or "unnamed"}: DDS is {dds.width}x{dds.height} but DAT entry is '
                f'{entry.width}x{entry.height} (pass allow_resize to change it)'
            )
        entry.width = dds.width
        entry.height = dds.height

    txd_magic = txd_magic_for_fourcc(dds.fourcc)
    data_size = len(dds.pixels)
    pitch = pitch_for(dds.width, dds.fourcc)
    expected_size = ((dds.height + 3) // 4) * pitch
    if data_size != expected_size:
        raise ValueError(
            f'{entry.name or "unnamed"}: DDS payload size {data_size} does not match expected '
            f'{expected_size} for {dds.fourcc.decode()} {dds.width}x{dds.height}'
        )

    if data_size != entry.data_size:
        _resize_chunk(data, entry, data_size)

    entry_off = entry.txd_offset - ENTRY_HEADER_SIZE
    struct.pack_into('<IIHH', data, entry_off + 21, dds.width, dds.height, entry.mip_count, entry.pixel_format)
    struct.pack_into('<4sII', data, entry.txd_offset, txd_magic, data_size, pitch)

    pixel_start = entry.txd_offset + TXD_HEADER_SIZE
    data[pixel_start:pixel_start + data_size] = dds.pixels

    # Keep the entry consistent with what is now on disk.
    entry.txd_magic = txd_magic
    entry.data_size = data_size
    entry.pitch = pitch
    entry.pixels = bytes(dds.pixels)


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


def _record_prefix(data: bytes, off: int, length: int, tex_wh: tuple[int, int]) -> int | None:
    """Byte offset of the quad within a payload, or None if no reading fits.

    Payloads carry a variable prefix: 41-byte records start at +0, 42-byte ones at +1.
    Rather than trust the length, each candidate is validated by checking the source
    rect it yields actually fits inside the texture, which is self-correcting for the
    handful of longer variants.
    """
    w, h = tex_wh
    for pre in (0, 1):
        if length < pre + SRC_RECT_OFFSET + 8:
            continue
        sw, sh, sx, sy = struct.unpack_from('<4H', data, off + pre + SRC_RECT_OFFSET)
        if sw and sh and sx + sw <= w and sy + sh <= h:
            return pre
    return None


def _scale_extent(value: int, old_full: int, new_full: int) -> int:
    """Scale one rect component from an old texture size to a new one.

    A component covering the whole texture is stored as `full - 1` (a 256px texture
    reads 255), so it is remapped to `new_full - 1` rather than multiplied -- 255x2
    would give 510, not the 511 the client needs. Everything else is an atlas
    coordinate and scales proportionally.
    """
    if value == old_full - 1:
        return new_full - 1
    return round(value * new_full / old_full)


def _rects_by_owner(data: bytes) -> dict:
    """Map texture name -> ordered list of (payload_offset, prefix, rect) for its sprites."""
    dims = {e.name: (e.width, e.height) for e in parse_textures(data)}
    records = parse_layout_records(data)
    out: dict[str, list] = {}
    for i, (_name, off, length) in enumerate(records):
        if i + 1 >= len(records):
            continue
        # A payload is described by the name that FOLLOWS it (confirmed on ROM/119/50:
        # patching the payload between the `titlwin` and `20logo` names moved the
        # 20logo sprite and nothing else).
        name = records[i + 1][0]
        if name not in dims:
            continue
        pre = _record_prefix(data, off, length, dims[name])
        if pre is None:
            continue
        rect = struct.unpack_from('<4H', data, off + pre + SRC_RECT_OFFSET)
        out.setdefault(name, []).append((off, pre, rect))
    return out


LAYOUT_SHEET_PATH = Path(__file__).with_name('data') / 'layout_reference.json'
_layout_sheet_cache: dict | None = None


def load_layout_sheet() -> dict:
    """Built-in table of original texture sizes and sprite rects, keyed by ROM path.

    Generated from pristine retail DATs, plus hand-entered geometry for sprites no
    retail DAT ships (CatsEyeXI's `20logo`). This is the default reference: it needs no
    second file on disk, it cannot have been mangled by a previous edit, and it still
    works when the only copy of a DAT the user owns is already wrong.
    """
    global _layout_sheet_cache
    if _layout_sheet_cache is None:
        import json
        try:
            _layout_sheet_cache = json.loads(LAYOUT_SHEET_PATH.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            _layout_sheet_cache = {}
    return _layout_sheet_cache


def layout_sheet_key(dat_file: Path) -> str | None:
    """`.../ROM/119/50.DAT` -> `ROM/119/50`, the key used by the built-in sheet."""
    parts = list(Path(dat_file).parts)
    idx = [i for i, part in enumerate(parts) if part.upper().startswith('ROM')]
    if not idx:
        return None
    tail = list(parts[idx[-1]:])
    tail[-1] = Path(tail[-1]).stem
    return '/'.join(tail).replace('\\', '/')


def sheet_reference(dat_file: Path) -> tuple[dict, dict] | None:
    """(texture dims, rects by texture) for `dat_file` from the built-in sheet."""
    key = layout_sheet_key(dat_file)
    entry = load_layout_sheet().get(key) if key else None
    if not entry:
        return None
    dims = {k: tuple(v) for k, v in entry.get('textures', {}).items()}
    rects = {k: [tuple(r) for r in v] for k, v in entry.get('rects', {}).items()}
    return dims, rects


def dat_reference(reference: bytes) -> tuple[dict, dict]:
    """(texture dims, rects by texture) read from a pristine copy of the DAT."""
    dims = {e.name: (e.width, e.height) for e in parse_textures(reference)}
    rects = {name: [rect for _off, _pre, rect in sprites]
             for name, sprites in _rects_by_owner(reference).items()}
    return dims, rects


def sync_layout_rects(data: bytearray,
                      ref: tuple[dict, dict] | None = None) -> list[tuple[str, int, tuple, tuple]]:
    """Bring sprite source rects in line with the textures currently in the DAT.

    `ref` is (original texture dims, original rects per texture) -- from the built-in
    sheet or a pristine DAT. Each rect is recomputed from the original value scaled by
    that texture's current-vs-original size, never from the live bytes, so the pass is
    idempotent: an import cannot scale a rect that is already scaled, and a rect left
    wrong by some other tool is corrected rather than compounded. It also lets an atlas
    like `ex1us` (five 240x48 banners stacked in one texture) survive an upscale, since
    every sub-rect moves proportionally.

    Sprites pair with the reference by **ordinal within their texture**, not by record
    index: a server override adds and removes unrelated records (CatsEyeXI's `119/50`
    has 1198 where retail has 1230), so index pairing misaligns everything after the
    first difference.

    Textures missing from the reference fall back to whole-texture repair, the one case
    recognisable without any reference: a `full - 1` square power-of-two extent anchored
    at (0,0).

    Returns (texture_name, payload_offset, old_rect, new_rect) per rect changed.
    """
    dims = {e.name: (e.width, e.height) for e in parse_textures(data)}
    cur = _rects_by_owner(data)
    ref_dims, ref_rects = ref if ref else ({}, {})
    changed = []

    def whole(w: int, h: int) -> bool:
        # Square, power-of-two-minus-one, and big enough to be a texture rather than a
        # sprite. Without the size floor a 7x7 or 15x15 atlas cell at (0,0) satisfies
        # the shape test and the no-reference fallback rewrites it to the full texture,
        # destroying the sprite. 64 is below every whole-texture rect seen in the UI
        # DATs and above every atlas cell.
        return w == h and w + 1 >= 64 and (w + 1) & w == 0

    for name, sprites in cur.items():
        cur_w, cur_h = dims[name]
        originals = ref_rects.get(name)
        ref_w, ref_h = ref_dims.get(name, (0, 0))
        paired = originals is not None and len(originals) == len(sprites) and ref_w and ref_h

        for idx, (off, pre, old) in enumerate(sprites):
            if paired:
                sw, sh, sx, sy = originals[idx]
                new = (_scale_extent(sw, ref_w, cur_w), _scale_extent(sh, ref_h, cur_h),
                       round(sx * cur_w / ref_w), round(sy * cur_h / ref_h))
            else:
                sw, sh, sx, sy = old
                if not ((sx, sy) == (0, 0) and whole(sw, sh)
                        and cur_w == cur_h and whole(cur_w - 1, cur_h - 1)):
                    continue
                new = (cur_w - 1, cur_h - 1, 0, 0)

            if new != old:
                struct.pack_into('<4H', data, off + pre + SRC_RECT_OFFSET, *new)
                changed.append((name, off, old, new))

    return changed


def resolve_layout_reference(dat_file: Path,
                             explicit: str | None = None) -> tuple[tuple[dict, dict] | None, str]:
    """Pick the original-geometry source for `dat_file`, returning (model, description).

    Order: an explicit --reference DAT, then the built-in sheet, then a `<dat>.base`
    snapshot. The sheet outranks `.base` because a `.base` is only as good as whatever
    state the DAT was in the first time it was written, and may not exist; the sheet is
    generated from pristine retail DATs and ships with the tool.
    """
    if explicit:
        cand = Path(explicit)
        if not cand.exists():
            return None, f'reference not found: {cand}'
        return dat_reference(cand.read_bytes()), f'reference DAT {cand}'

    model = sheet_reference(dat_file)
    if model:
        key = layout_sheet_key(dat_file)
        return model, f'built-in sheet [{key}]'

    base = dat_file.with_name(dat_file.name + '.base')
    if base.exists():
        return dat_reference(base.read_bytes()), f'reference DAT {base}'

    return None, 'no reference (whole-texture rects only)'

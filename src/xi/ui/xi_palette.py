"""Palettized (`0xB1`) UI textures — 8-bit indices into a 256-entry RGBA palette.

FFXI's UI containers hold two kinds of texture. The common one is DXT, marked `0xA1`
with an `xTXD` block. The other is this: a palette of 256 colours plus one byte per
pixel. Around 2100 of them ship across the retail DATs (VFX, zone effects, and `hi52`
in `ROM/0/0`), so it is a mainstream format rather than a curiosity.

It is worth using because DXT3 spends its bits badly on UI art: four colours per 4x4
block, and 4-bit alpha. A palette picks 256 colours across the whole image and stores
8-bit alpha per entry. On a real expansion-logo texture that measured 32.4 dB RGB with
*exact* alpha, against 29.9 dB and quantised alpha for DXT3.

Layout, decoded from retail and confirmed on screen::

    chunk header  16B   (section type 0x20, size in 16-byte units)
    entry header  64B
      +0   u8     0xB1
      +1   8      parent tag (space padded)
      +9   8      texture name (space padded)
      +17  u32    0x28
      +21  u32    width
      +25  u32    height
      +29  u16    mip count (1)
      +31  u16    pixel format (8)
      +33  20     zeros
      +53  u32    0x20
      +57  u32    1
      +61  3      padding
    +64          256 palette entries, 4 bytes each: A, B, G, R
    +1088        width * height 8-bit indices

**Index rows run bottom-up.** Unlike the DXT blocks in an `xTXD` texture, row 0 of the
stored data is the bottom of the image; writing top-down renders the sprite vertically
mirrored, with a stack of sprites appearing in reverse order.

**Alpha is half scale**, matching the rest of FFXI: retail palettes top out around 123,
not 255.
"""

import collections
import struct
from dataclasses import dataclass
from pathlib import Path

from xi.common.xi_section import MAX_SECTION_UNITS, SECTION_UNIT, encode_section_meta

PAL_MARKER = 0xB1
PAL_ENTRY_HEADER = 64
PAL_COLOURS = 256
PAL_BYTES = PAL_COLOURS * 4
TEXTURE_SECTION_TYPE = 0x20
DEFAULT_ALPHA_SCALE = 0.5      # retail palettes store alpha at half scale


@dataclass
class PalTexture:
    parent: str
    name: str
    width: int
    height: int
    chunk_off: int
    chunk_bytes: int
    info: int
    palette: list      # [(r, g, b, a), ...]
    indices: bytes


def parse_palettized(data: bytes) -> list[PalTexture]:
    """Every `0xB1` texture in a UI container, found by walking the chunk chain."""
    out = []
    off = 0x20
    while off + 17 <= len(data):
        info = struct.unpack_from('<I', data, off + 4)[0]
        nbytes = ((info >> 7) & MAX_SECTION_UNITS) * SECTION_UNIT
        if nbytes < 16 or off + nbytes > len(data):
            break
        if (info & 0x7F) == TEXTURE_SECTION_TYPE and data[off + 16] == PAL_MARKER:
            e = off + 16
            _hdr, width, height = struct.unpack_from('<III', data, e + 17)
            pal_off = e + PAL_ENTRY_HEADER
            idx_off = pal_off + PAL_BYTES
            if idx_off + width * height <= off + nbytes:
                palette = []
                for i in range(PAL_COLOURS):
                    a, b, g, r = data[pal_off + i * 4:pal_off + i * 4 + 4]
                    palette.append((r, g, b, a))
                out.append(PalTexture(
                    parent=data[e + 1:e + 9].rstrip(b' ').decode('ascii', 'replace'),
                    name=data[e + 9:e + 17].rstrip(b' ').decode('ascii', 'replace'),
                    width=width, height=height,
                    chunk_off=off, chunk_bytes=nbytes, info=info,
                    palette=palette,
                    indices=bytes(data[idx_off:idx_off + width * height]),
                ))
        off += nbytes
    return out


def to_image(tex: PalTexture, alpha_scale: float = DEFAULT_ALPHA_SCALE):
    """Decode to a top-down RGBA image, undoing the half-scale alpha."""
    from PIL import Image
    inv = (1.0 / alpha_scale) if alpha_scale else 1.0
    lut = [(r, g, b, min(255, round(a * inv))) for r, g, b, a in tex.palette]
    img = Image.new('RGBA', (tex.width, tex.height))
    img.putdata([lut[i] for i in tex.indices])
    return img.transpose(Image.FLIP_TOP_BOTTOM)


def quantize(img, budget: int = PAL_COLOURS - 1):
    """Build a palette and indices for an RGBA image, via median cut in RGBA space.

    Alpha has to be quantised jointly with colour, because a palette entry carries both:
    the same colour at two opacities needs two entries. An earlier version split the
    budget across the distinct alpha levels and quantised colour within each. That works
    for art with a handful of opacities (an expansion banner has 11) but collapses on
    anything with soft edges -- a logo with hundreds of alpha levels got one colour per
    level and rendered as a flat silhouette.

    Median cut over the 4-D RGBA histogram has no such failure mode: it spends entries
    where the pixels actually are, whether they vary in colour, in opacity, or in both.
    Index 0 is reserved for fully transparent.
    """
    px = list(img.getdata())
    hist = collections.Counter(px)
    hist.pop((0, 0, 0, 0), None)
    opaque_keys = [k for k in hist if k[3] > 0]
    if not opaque_keys:
        return [(0, 0, 0, 0)], bytes(len(px))

    # Alpha is weighted up: an error in opacity is more visible than the same numeric
    # error in one colour channel, since it shows as a halo against whatever is behind.
    WEIGHT = (1.0, 1.0, 1.0, 2.0)

    def extent(box):
        lo = [255] * 4
        hi = [0] * 4
        for c in box:
            for i in range(4):
                if c[i] < lo[i]:
                    lo[i] = c[i]
                if c[i] > hi[i]:
                    hi[i] = c[i]
        return [(hi[i] - lo[i]) * WEIGHT[i] for i in range(4)]

    boxes = [[k for k in hist if k[3] > 0]]
    while len(boxes) < budget:
        # Split the box that is worst by spread x population; a wide box holding a few
        # stray pixels is not worth an entry that a tight, heavily-used one could have.
        best, best_score, best_axis = None, 0.0, 0
        for bi, box in enumerate(boxes):
            if len(box) < 2:
                continue
            ext = extent(box)
            axis = max(range(4), key=lambda i: ext[i])
            score = ext[axis] * sum(hist[c] for c in box)
            if score > best_score:
                best, best_score, best_axis = bi, score, axis
        if best is None:
            break
        box = sorted(boxes[best], key=lambda c: c[best_axis])
        half = sum(hist[c] for c in box) / 2
        run = 0
        cut = 1
        for i, c in enumerate(box):
            run += hist[c]
            if run >= half:
                cut = max(1, min(i + 1, len(box) - 1))
                break
        boxes[best:best + 1] = [box[:cut], box[cut:]]

    palette = [(0, 0, 0, 0)]
    for box in boxes:
        n = sum(hist[c] for c in box)
        if not n:
            continue
        palette.append(tuple(
            min(255, round(sum(c[i] * hist[c] for c in box) / n)) for i in range(4)))
    palette = palette[:PAL_COLOURS]

    lookup = {}
    for bi, box in enumerate(boxes):
        if bi + 1 < len(palette):
            for c in box:
                lookup[c] = bi + 1

    indices = bytearray()
    for p in px:
        if p[3] == 0:
            indices.append(0)
            continue
        hit = lookup.get(p)
        if hit is None:
            hit = min(range(1, len(palette)),
                      key=lambda i: sum(WEIGHT[k] * (palette[i][k] - p[k]) ** 2
                                        for k in range(4)))
            lookup[p] = hit
        indices.append(hit)
    return palette, bytes(indices)


def build_chunk(tag: bytes, info: int, parent: str, name: str, img,
                alpha_scale: float = DEFAULT_ALPHA_SCALE) -> bytes:
    """Encode a top-down RGBA image as a complete `0xB1` chunk."""
    from PIL import Image
    flipped = img.transpose(Image.FLIP_TOP_BOTTOM)      # stored bottom-up
    palette, indices = quantize(flipped)

    body = bytearray(PAL_ENTRY_HEADER)
    body[0] = PAL_MARKER
    body[1:9] = parent.ljust(8)[:8].encode('ascii', 'replace')
    body[9:17] = name.ljust(8)[:8].encode('ascii', 'replace')
    struct.pack_into('<IIIHH', body, 17, 0x28, img.width, img.height, 1, 8)
    struct.pack_into('<I', body, 53, 0x20)
    struct.pack_into('<I', body, 57, 1)
    for r, g, b, a in palette:
        body += bytes((min(255, round(a * alpha_scale)), b, g, r))
    body += bytes(4 * (PAL_COLOURS - len(palette)))
    body += indices

    padded = (16 + len(body) + SECTION_UNIT - 1) // SECTION_UNIT * SECTION_UNIT
    chunk = bytearray(padded)
    chunk[0:4] = tag
    struct.pack_into('<I', chunk, 4,
                     encode_section_meta(padded, TEXTURE_SECTION_TYPE, info,
                                         what=f'0x20 palettized chunk {name!r}'))
    chunk[16:16 + len(body)] = body
    return bytes(chunk)


def find_texture_chunk(data: bytes, name: str):
    """Locate a texture chunk by name, DXT (`0xA1`) or palettized (`0xB1`).

    `parse_textures` only matches `xTXD`, so it cannot see a chunk that has been
    converted to a palette -- which would leave the tool blind to its own output.
    """
    off = 0x20
    while off + 17 <= len(data):
        info = struct.unpack_from('<I', data, off + 4)[0]
        nbytes = ((info >> 7) & MAX_SECTION_UNITS) * SECTION_UNIT
        if nbytes < 16 or off + nbytes > len(data):
            break
        if (info & 0x7F) == TEXTURE_SECTION_TYPE and data[off + 16] in (0xA1, PAL_MARKER):
            e = off + 16
            if data[e + 9:e + 17].rstrip(b' ').decode('ascii', 'replace') == name:
                _hdr, width, height = struct.unpack_from('<III', data, e + 17)
                return {
                    'off': off, 'bytes': nbytes, 'info': info,
                    'tag': bytes(data[off:off + 4]),
                    'parent': data[e + 1:e + 9].rstrip(b' ').decode('ascii', 'replace'),
                    'width': width, 'height': height,
                    'palettized': data[off + 16] == PAL_MARKER,
                }
        off += nbytes
    return None


def build_dxt_chunk(tag: bytes, info: int, parent: str, name: str, dds) -> bytes:
    """Encode a parsed DDS as a complete DXT (`0xA1`) chunk.

    The counterpart to `build_chunk`. Without it a texture converted to a palette is
    stuck that way: `parse_textures` matches only `xTXD`, so the normal import loop
    cannot see the chunk to replace it, and the DAT keeps whatever the palette pass
    last wrote.
    """
    from xi.ui.xi_core import (ENTRY_HEADER_SIZE, FMT_DXT1_ALPHA, FMT_DXT1_OPAQUE,
                               pitch_for, txd_magic_for_fourcc)

    pixel_format = FMT_DXT1_ALPHA if dds.fourcc == b'DXT1' else FMT_DXT1_OPAQUE
    body = bytearray(ENTRY_HEADER_SIZE)
    body[0] = 0xA1
    body[1:9] = parent.ljust(8)[:8].encode('ascii', 'replace')
    body[9:17] = name.ljust(8)[:8].encode('ascii', 'replace')
    struct.pack_into('<IIIHH', body, 17, 0x28, dds.width, dds.height, 1, pixel_format)
    struct.pack_into('<I', body, 53, 0x10)

    pitch = pitch_for(dds.width, dds.fourcc)
    body += struct.pack('<4sII', txd_magic_for_fourcc(dds.fourcc), len(dds.pixels), pitch)
    body += dds.pixels

    padded = (16 + len(body) + SECTION_UNIT - 1) // SECTION_UNIT * SECTION_UNIT
    chunk = bytearray(padded)
    chunk[0:4] = tag
    struct.pack_into('<I', chunk, 4,
                     encode_section_meta(padded, TEXTURE_SECTION_TYPE, info,
                                         what=f'0x20 texture chunk {name!r}'))
    chunk[16:16 + len(body)] = body
    return bytes(chunk)

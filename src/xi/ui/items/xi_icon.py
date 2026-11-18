"""Item icons: the 32x32 bitmap an item record holds, to and from PNG and BMP files.

Used by ``xi ui items icon export`` / ``import``. An item record keeps its icon at
``ICON_DATA`` (0x284) after a u32 size (``xi.ui.items.xi_layout``). Measured on all
34,816 icons of a legacy (pre-September-2026) client's item DATs, every icon is 2,105
bytes laid out the same way:

    0x91                      mark byte
    16 ASCII characters       tag: a kind ("item    ", "armor   ", "weapon  ", "icon    ",
                              "recepi  ", "etc     ") then a name, space-padded
    BITMAPINFOHEADER (40)     size 40, 32 x 32, 1 plane, 8 bits, BI_RGB, biSizeImage 0,
                              no resolution, biClrUsed 0, biClrImportant 32
    256 x BGRA                the palette; alpha 0..128, 128 = opaque
    1,024 bytes               palette indices, bottom row first

``biClrImportant`` is 32 on every retail icon, whatever the colour count; CatsEyeXI found
that an icon with another value is drawn wrong the first time it appears in an
inventory list. 29,039 icons have opaque palette entries at alpha 128; the 5,394 tagged
"icon" have alpha 0 on every entry and are drawn opaque by the client [inferred: they
are retail's ordinary key-item-style icons and show in game], so an all-zero palette
alpha is read as "no alpha" rather than "invisible".

A PNG export is an indexed (palette) PNG holding the record's palette, alpha and
indices as they are, plus the tag in an ``xi.icon.tag`` text chunk, so importing an
unedited export writes the record's icon back byte for byte. Any other image (RGBA,
RGB, a different size) is resized to 32x32 and reduced to 256 colours.

The encoding follows LPS (Loxley's Patch System, ``lps/icons.py``), ported with his
permission; the alpha scaling, the all-zero-alpha case, the indexed round trip and the
tag handling are new here.
"""
from __future__ import annotations

import io
import struct

ICON_MARK = 0x91
TAG_LEN = 16
SIZE = 32
HEADER = struct.pack('<IiiHHIIiiII', 40, SIZE, SIZE, 1, 8, 0, 0, 0, 0, 0, 32)
PALETTE_LEN = 256 * 4
PIXELS_LEN = SIZE * SIZE
BLOB_LEN = 1 + TAG_LEN + len(HEADER) + PALETTE_LEN + PIXELS_LEN       # 2,105
DEFAULT_TAG = 'item    custom'
TAG_KEY = 'xi.icon.tag'
NO_ALPHA_KEY = 'xi.icon.alpha'           # "none": the palette's alpha bytes are all 0


class IconError(ValueError):
    pass


def _alpha_to_png(a: int) -> int:
    return min(255, a * 2)


def _alpha_from_png(a: int) -> int:
    # (A + 1) // 2 inverts min(255, 2a) for every a in 0..128 (255 -> 128).
    return (a + 1) // 2


def parse(blob: bytes) -> dict:
    """The parts of an icon blob (what follows the u32 size in the record)."""
    if len(blob) < BLOB_LEN or blob[0] != ICON_MARK:
        raise IconError(f'not an item icon (mark {blob[:1].hex() or "none"}, {len(blob)} bytes)')
    dib = blob[1 + TAG_LEN:]
    head, w, h, _planes, bits = struct.unpack_from('<IiiHH', dib, 0)
    if (head, w, h, bits) != (40, SIZE, SIZE, 8):
        raise IconError(f'unexpected icon header: {head}-byte header, {w}x{h}, {bits} bits')
    pal = dib[head:head + PALETTE_LEN]
    pix = dib[head + PALETTE_LEN:head + PALETTE_LEN + PIXELS_LEN]
    return {'tag': blob[1:1 + TAG_LEN].decode('ascii', errors='replace'),
            'header': bytes(dib[:head]), 'palette': bytes(pal), 'pixels': bytes(pix)}


def build(palette: bytes, pixels: bytes, tag: str = DEFAULT_TAG) -> bytes:
    """An icon blob from a 1,024-byte BGRA palette (alpha 0..128) and 1,024 bottom-up indices."""
    if len(palette) != PALETTE_LEN or len(pixels) != PIXELS_LEN:
        raise IconError('an icon is a 256-colour palette and 32x32 indices')
    t = tag.encode('ascii', errors='strict')
    if len(t) > TAG_LEN:
        raise IconError(f'icon tag {tag!r} is longer than {TAG_LEN} characters')
    return bytes([ICON_MARK]) + t.ljust(TAG_LEN, b' ') + HEADER + palette + pixels


def to_bmp(blob: bytes) -> bytes:
    """The icon as a .bmp file (what a ``database`` edit's ``icon.file`` reads back)."""
    p = parse(blob)
    dib = p['header'] + p['palette'] + p['pixels']
    return b'BM' + struct.pack('<IHHI', 14 + len(dib), 0, 0, 14 + len(p['header']) + PALETTE_LEN) + dib


def to_image(blob: bytes):
    """The icon as an indexed PIL image (top row first) with its alpha as PNG transparency."""
    from PIL import Image
    p = parse(blob)
    pal, pix = p['palette'], p['pixels']
    rows = [pix[(SIZE - 1 - y) * SIZE:(SIZE - y) * SIZE] for y in range(SIZE)]
    img = Image.frombytes('P', (SIZE, SIZE), b''.join(rows))
    rgb = bytearray()
    for i in range(256):
        b, g, r = pal[i * 4:i * 4 + 3]
        rgb += bytes((r, g, b))
    img.putpalette(bytes(rgb), rawmode='RGB')
    alphas = pal[3::4]
    no_alpha = not any(alphas)
    if not no_alpha:
        img.info['transparency'] = bytes(_alpha_to_png(a) for a in alphas)
    img.info[TAG_KEY] = p['tag']
    if no_alpha:
        img.info[NO_ALPHA_KEY] = 'none'
    return img


def to_png(blob: bytes) -> bytes:
    from PIL import PngImagePlugin
    img = to_image(blob)
    meta = PngImagePlugin.PngInfo()
    meta.add_text(TAG_KEY, img.info[TAG_KEY])
    if img.info.get(NO_ALPHA_KEY):
        meta.add_text(NO_ALPHA_KEY, 'none')
    out = io.BytesIO()
    kw = {'pnginfo': meta, 'optimize': False}
    if 'transparency' in img.info:
        kw['transparency'] = img.info['transparency']
    img.save(out, format='PNG', **kw)
    return out.getvalue()


def _palette_entries(img) -> list[tuple[int, int, int, int]]:
    """An indexed image's 256 palette entries as RGBA (PNG alpha 0..255), padded with black."""
    raw = img.getpalette(rawmode='RGB') or []
    trns = img.info.get('transparency')
    out = []
    for i in range(256):
        r, g, b = (raw[i * 3:i * 3 + 3] + [0, 0, 0])[:3] if i * 3 < len(raw) else (0, 0, 0)
        if isinstance(trns, (bytes, bytearray)):
            a = trns[i] if i < len(trns) else 255
        elif isinstance(trns, int):
            a = 0 if i == trns else 255
        else:
            a = 255
        out.append((r, g, b, a))
    return out


def from_image(img, tag: str | None = None, no_alpha: bool | None = None,
               default_tag: str = DEFAULT_TAG) -> bytes:
    """An icon blob from a PIL image.

    An indexed 32x32 image keeps its palette and indices as they are (an unedited export
    comes back byte for byte). Anything else is converted to RGBA, resized to 32x32 and
    reduced to at most 256 colours: exactly when it has 256 or fewer distinct colours
    (palette in first-seen order), else by Pillow's fast octree quantizer.

    ``tag`` defaults to the image's ``xi.icon.tag`` text, then ``default_tag``.
    ``no_alpha`` (default: the image's ``xi.icon.alpha`` text says "none") writes alpha 0
    on every palette entry, as retail's "icon"-tagged icons have.
    """
    from PIL import Image
    if tag is None:
        tag = img.info.get(TAG_KEY) or default_tag
    if no_alpha is None:
        no_alpha = img.info.get(NO_ALPHA_KEY) == 'none'

    if img.mode == 'P' and img.size == (SIZE, SIZE):
        entries = _palette_entries(img)
        idx = img.tobytes()
    else:
        rgba = img.convert('RGBA')
        if rgba.size != (SIZE, SIZE):
            rgba = rgba.resize((SIZE, SIZE), Image.Resampling.LANCZOS)
        raw = rgba.tobytes()
        px = [tuple(raw[i:i + 4]) for i in range(0, len(raw), 4)]
        colours: dict[tuple, int] = {}
        for c in px:
            if c not in colours and len(colours) <= 256:
                colours[c] = len(colours)
        if len(colours) <= 256:
            entries = list(colours) + [(0, 0, 0, 0)] * (256 - len(colours))
            idx = bytes(colours[c] for c in px)
        else:
            q = rgba.quantize(colors=256, method=Image.Quantize.FASTOCTREE)
            pal = (q.getpalette(rawmode='RGBA') or [])
            pal += [0] * (1024 - len(pal))
            entries = [tuple(pal[i * 4:i * 4 + 4]) for i in range(256)]
            idx = q.tobytes()

    palette = bytearray()
    for r, g, b, a in entries:
        palette += bytes((b, g, r, 0 if no_alpha else _alpha_from_png(a)))
    pixels = b''.join(idx[(SIZE - 1 - y) * SIZE:(SIZE - y) * SIZE] for y in range(SIZE))
    return build(bytes(palette), pixels, tag)


def from_file(data: bytes, tag: str | None = None, default_tag: str = DEFAULT_TAG) -> bytes:
    """An icon blob from PNG/BMP/any Pillow-readable bytes, or from a .bmp written by
    ``to_bmp`` / ``icon export --bmp`` (whose palette alpha is already 0..128, kept as is)."""
    if data[:2] == b'BM' and len(data) >= 14 + 40:
        head, w, h, _pl, bits = struct.unpack_from('<IiiHH', data, 14)
        used, important = struct.unpack_from('<II', data, 14 + 32)
        if (head, w, h, bits) == (40, SIZE, SIZE, 8) and important == 32 and not used:
            dib = data[14:14 + 40 + PALETTE_LEN + PIXELS_LEN]
            if len(dib) == 40 + PALETTE_LEN + PIXELS_LEN:
                return build(dib[40:40 + PALETTE_LEN], dib[40 + PALETTE_LEN:], tag or default_tag)
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:  # noqa: BLE001 - Pillow raises several types for a bad file
        raise IconError(f'not an image Pillow can read: {e}') from e
    return from_image(img, tag=tag, default_tag=default_tag)

"""Build and inject 0x5E blur (distortion aura) sections into entity DATs.

The 0x5E section creates the shimmer/distortion effect seen on Prime Avatars
(Ifrit, Shiva, etc.). It's a radial blur with configurable color, intensity,
and radius.

Section format (after 16-byte DAT section header):
    byte  0:    layers    — number of distortion layers (more = blurrier)
    byte  1:    frequency — oscillation speed (higher = faster shimmer)
    bytes 2-3:  falloff   — edge blur falloff, u16 LE (higher = subtler)
    bytes 4-7:  base_color1 — RGBA (usually gray 128,128,128,0)
    bytes 8-11: base_color2 — RGBA (usually gray 128,128,128,0)
    bytes 12-15: inner_radius — float, initial blur radius
    bytes 16+:  rings — N entries of 8 bytes each: RGB(3) + alpha(1) + radius(float)
"""

import struct

from xi.common.xi_section import encode_section_meta


def build_blur_section(r: int = 200, g: int = 200, b: int = 255,
                       alpha: int = 20, radius: float = 120.0,
                       layers: int = 11, frequency: int = 8,
                       falloff: int = 0x0190, ring_count: int = 8) -> bytes:
    """Build a 0x5E blur section.

    Args:
        r, g, b: Ring color (0-255). Light colors look best.
        alpha: Distortion strength (5-50). Higher = more blur across model.
        radius: Outer blur radius (60-180). Larger = softer transitions.
        layers: Distortion layer count (3-11). More = blurrier.
        frequency: Shimmer oscillation speed (4-8). Higher = faster.
        falloff: Edge blur falloff (100-800). Higher = subtler edges.
        ring_count: Gradient ring count (default 8).

    Returns:
        Complete 0x5E section bytes (header + body), 16-byte aligned.
    """
    inner_radius = radius * 0.4

    body = bytearray()
    body += struct.pack('<BBH', layers, frequency, falloff)
    body += struct.pack('BBBB', 128, 128, 128, 0)   # base color 1
    body += struct.pack('BBBB', 128, 128, 128, 0)   # base color 2
    body += struct.pack('<f', inner_radius)

    for i in range(ring_count):
        t = i / max(1, ring_count - 1)
        a = max(4, int(alpha * (1 - t * 0.5)))
        rad = inner_radius + (radius - inner_radius) * t
        body += struct.pack('BBBB', r, g, b, a)
        body += struct.pack('<f', rad)

    # Final ring at radius 0
    body += struct.pack('BBBB', r, g, b, max(1, alpha // 2))
    body += struct.pack('<f', 0.0)
    # Tail marker
    body += struct.pack('BBBB', 0, 0, 128, 63)
    body += struct.pack('<f', 0.0)

    while len(body) % 16 != 0:
        body += b'\x00'

    padded_size = len(body) + 16
    meta = encode_section_meta(padded_size, 0x5E, what="blur section")
    header = b'blur' + struct.pack('<I', meta) + b'\x00' * 8
    return header + bytes(body)


def inject_blur_section(data: bytearray, blur_bytes: bytes) -> bytearray:
    """Insert a 0x5E section into an entity DAT before its final END section."""
    # Find the last END (type 0x00) section
    pos = 0
    last_end_pos = None
    while pos + 16 <= len(data):
        meta = struct.unpack_from('<I', data, pos + 4)[0]
        type_code = meta & 0x7F
        size = ((meta >> 7) & 0xFFFFF) * 0x10
        if size <= 0:
            break
        if type_code == 0x00:
            last_end_pos = pos
        pos += size

    if last_end_pos is None:
        raise ValueError('No END section found in DAT')

    return bytearray(bytes(data[:last_end_pos]) + blur_bytes +
                     bytes(data[last_end_pos:]))

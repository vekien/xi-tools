"""The DAT texture decoders in ``xi.entity.mesh.xi_export`` against the standard D3D
block layout: texel i (row-major in a 4x4 block) takes index bits 2i and, for DXT3,
alpha nibble i of the 64-bit little-endian alpha word. xim's port read that word as
(first dword << 32) | second, swapping alpha rows 0<->1 and 2<->3 — the horizontal
streaks in exported effect textures whose alpha carries the image."""
import struct

from xi.entity.anim.xi_export import Reader
from xi.entity.mesh.xi_export import decode_dxt1, decode_dxt3


def _alpha_word(nibbles):
    return sum(n << (4 * i) for i, n in enumerate(nibbles)).to_bytes(8, "little")


def _index_word(indices):
    return sum(v << (2 * i) for i, v in enumerate(indices)).to_bytes(4, "little")


def test_dxt3_alpha_nibble_i_is_texel_i():
    nibbles = list(range(16))
    block = _alpha_word(nibbles) + struct.pack("<HH", 0xFFFF, 0x0000) + _index_word([0] * 16)
    rgba = decode_dxt3(Reader(block), 4, 4)
    assert [rgba[4 * i + 3] for i in range(16)] == [n * 255 // 16 for n in nibbles]


def test_dxt1_index_i_is_texel_i():
    indices = [i % 4 for i in range(16)]
    # c0 > c1: four opaque colours; red channel 255, 0, 170, 85.
    block = struct.pack("<HH", 0xF800, 0x0000) + _index_word(indices)
    rgba = decode_dxt1(Reader(block), 4, 4)
    red = {0: 255, 1: 0, 2: 170, 3: 85}
    assert [rgba[4 * i] for i in range(16)] == [red[v] for v in indices]

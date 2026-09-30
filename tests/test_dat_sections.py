"""The DAT chunk walkers read a section's size the way the client does: bits 7-25 of the
info word, 19 bits (``xi.common.xi_section``). Bit 26 is ``is_shadow``; a walker that
masks 20 bits (xim's model, which several walkers had copied) reads a shadow section
8 MiB too long and steps past every section after it."""
import struct
from pathlib import Path

from xi.common.xi_section import encode_section_meta
from xi.entity.anim.xi_export import parse_sections
from xi.entity.xi_particle_patch import _parse_sections as particle_sections
from xi.event.xi_event import _scene_sections
from xi.mv.dat_index import _walk_types

IS_SHADOW = 1 << 26
SRC = Path(__file__).resolve().parents[1] / "src" / "xi"


def _section(tag: bytes, type_code: int, body: int, flags: int = 0) -> bytes:
    size = 0x10 + body
    return tag + struct.pack("<I", encode_section_meta(size, type_code, flags)) + bytes(8 + body)


# A shadow texture section, then a directory: the walk has to land on the second.
DAT = _section(b"tex0", 0x20, 0x20, IS_SHADOW) + _section(b"dir0", 0x01, 0x10)


def test_the_flag_bits_are_kept_out_of_the_size():
    meta = struct.unpack_from("<I", DAT, 4)[0]
    assert meta & IS_SHADOW and (meta >> 7) & 0xFFFFF != 3


def test_parse_sections_reads_19_bits():
    secs = parse_sections(DAT)
    assert [(s.name, s.type_code, s.start, s.size) for s in secs] == [
        ("tex0", 0x20, 0x00, 0x30), ("dir0", 0x01, 0x30, 0x20)]


def test_the_event_scene_walker_reads_19_bits():
    assert _scene_sections(DAT) == [(0x00, "tex0", 0x20, 0x30), (0x30, "dir0", 0x01, 0x20)]


def test_the_particle_patch_walker_reads_19_bits():
    assert [(s["id"], s["start"], s["size"]) for s in particle_sections(DAT)] == [
        ("tex0", 0x00, 0x30), ("dir0", 0x30, 0x20)]


def test_the_model_viewer_index_walker_reads_19_bits(tmp_path):
    path = tmp_path / "1.DAT"
    path.write_bytes(DAT)
    first, types, dirs = _walk_types(path)
    assert (first, types, dirs) == ("tex0", frozenset({0x20, 0x01}), frozenset({"dir0"}))


def test_no_walker_masks_the_size_with_20_bits():
    wide = [f"{p.relative_to(SRC)}:{n}" for p in SRC.rglob("*.py")
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if "& 0xFFFFF)" in line and ">> 7" in line]
    assert wide == []

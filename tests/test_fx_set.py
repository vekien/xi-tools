"""``xi fx set --color`` and the colour ``xi fx json`` / ``fx dump`` print, on a synthetic
DAT: sec2 0x16 ColorSetup holds R,G,B,A, and both commands use that order."""
import struct
from pathlib import Path

from xi.common.xi_section import encode_section_meta
from xi.fx.xi_dump import dump_effects
from xi.fx.xi_set import _parse_color, set_effect_params


def _section(name: bytes, type_code: int, body: bytes) -> bytes:
    body = body + b"\0" * (-len(body) % 16)
    return name.ljust(4, b"\0") + struct.pack("<I", encode_section_meta(16 + len(body), type_code)) + b"\0" * 8 + body


def _generator(name: bytes, color: bytes) -> bytes:
    """A 0x05 section with one op in sec2: 0x16 ColorSetup, two dwords."""
    sec2 = struct.pack("<I", 0x16 | (2 << 8)) + color + bytes.fromhex("00010000")
    end = bytes.fromhex("00010000")
    offs = struct.pack("<4I", 0x90, 0x94, 0x94 + len(sec2), 0x98 + len(sec2))
    return _section(name, 0x05, b"\0" * 0x70 + offs + end + sec2 + end + end)


def test_color_is_written_and_read_as_rgb(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("xi.xi_config.FFXI_DIR", str(tmp_path))
    # Fire's g000 tint: orange, which these bytes are only as R,G,B,A.
    fire = bytes.fromhex("c6803326")
    dat = tmp_path / "fx.DAT"
    dat.write_bytes(_section(b"effe", 0x01, b"\0" * 16) + _generator(b"g000", fire)
                    + _generator(b"g001", fire) + _section(b"end", 0x00, b""))
    before = dat.read_bytes()
    assert {e["name"]: e["params"]["color_rgb"] for e in dump_effects(dat)["effects"]} == {
        "g000": "C68033", "g001": "C68033"}

    log = set_effect_params(dat, ["g000"], color=_parse_color("3380C6"))
    assert log == [{"name": "g000", "color": ("c68033", "3380c6")}]
    after = dat.read_bytes()
    at = before.index(fire)
    assert after[at:at + 4] == bytes.fromhex("3380c626")                 # R,G,B written, alpha kept
    assert after[:at] == before[:at] and after[at + 4:] == before[at + 4:]
    assert {e["name"]: e["params"]["color_rgb"] for e in dump_effects(dat)["effects"]} == {
        "g000": "3380C6", "g001": "C68033"}
    assert _parse_color("51,128,198") == (0x33, 0x80, 0xC6)
    assert (tmp_path / "fx.DAT.base").read_bytes() == before              # the pristine backup

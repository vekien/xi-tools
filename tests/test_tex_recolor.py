"""The texture recolour engine (xi.tex.xi_recolor.recolour_zone_dat): per-texture overrides,
skipped textures, several colour-range passes and the environment override, on a synthetic
DAT of 0x20 texture sections (DXT1 and paletted) and a 0x2F environment section."""
import struct
from pathlib import Path

import pytest

from xi.tex import xi_recolor as R


def _section(type_code: int, body: bytes, name: bytes = b"tex ") -> bytes:
    body = body + bytes(-(16 + len(body)) % 16)
    units = (16 + len(body)) // 16
    return name + struct.pack("<I", type_code | (units << 7)) + bytes(8) + body


def _dxt1(name: str, c0: int, c1: int, blocks: int = 4) -> bytes:
    head = bytearray(0x45)
    head[0] = 0xA1
    head[1:0x11] = name.encode("ascii").ljust(16, b" ")
    struct.pack_into("<I", head, 0x15, 8)
    struct.pack_into("<I", head, 0x19, 8)
    struct.pack_into("<H", head, 0x1F, 32)
    head[0x39:0x3D] = b"1TXD"
    struct.pack_into("<I", head, 0x3D, blocks * 8)
    data = b"".join(struct.pack("<HHI", c0, c1, 0x55555555) for _ in range(blocks))
    return _section(0x20, bytes(head) + data)


def _paletted(name: str, colours: list) -> bytes:
    head = bytearray(0x3D)
    head[0] = 0x91
    head[1:0x11] = name.encode("ascii").ljust(16, b" ")
    struct.pack_into("<I", head, 0x15, 4)
    struct.pack_into("<I", head, 0x19, 4)
    struct.pack_into("<H", head, 0x1F, 8)
    pal = bytearray(1024)
    for i, (r, g, b) in enumerate(colours):
        pal[i * 4:i * 4 + 4] = bytes((b, g, r, 0x80))
    return _section(0x20, bytes(head) + bytes(pal) + bytes(16))


def _environment() -> bytes:
    body = bytearray(0x60)
    for off in (0x0C, 0x10, 0x14, 0x18, 0x2C, 0x30, 0x34, 0x38):
        body[off:off + 4] = bytes((0x40, 0x80, 0xC0, 0xFF))
    struct.pack_into("<f", body, 0x1C, 300.0)
    struct.pack_into("<f", body, 0x3C, 300.0)
    return _section(0x2F, bytes(body), b"env ")


RED, BLUE = 0xF800, 0x001F          # RGB565


def _dat(tmp: Path) -> Path:
    p = tmp / "zone.DAT"
    p.write_bytes(_dxt1("suny    sky01", RED, BLUE) + _dxt1("wall    w01", RED, BLUE)
                  + _paletted("tree    t01", [(200, 30, 30), (30, 30, 200), (30, 200, 30)]) + _environment())
    return p


def _sections(path: Path) -> dict:
    data = path.read_bytes()
    out = {}
    for type_code, start, size, ds in R._parse_sections(data):
        key = R.texture_name(data, ds) if type_code == 0x20 else "environment"
        out[key] = data[start:start + size]
    return out


def test_no_new_options_changes_every_texture(tmp_path):
    src = _dat(tmp_path)
    out = tmp_path / "out.DAT"
    stats = R.recolour_zone_dat(src, out, hue=120)
    a, b = _sections(src), _sections(out)
    assert all(a[k] != b[k] for k in ("suny    sky01", "wall    w01", "tree    t01"))
    assert stats["dxt"] == 2 and stats["paletted"] == 1 and stats["skipped"] == 0


def test_override_replaces_only_its_keys_first_match_wins(tmp_path):
    src = _dat(tmp_path)
    plain, over = tmp_path / "plain.DAT", tmp_path / "over.DAT"
    R.recolour_zone_dat(src, plain, hue=120)
    R.recolour_zone_dat(src, over, hue=120, texture_overrides={
        "SUNY*": {"lightness": -50}, "suny    sky*": {"hue": 0}, "tree*": {"hue": 0}})
    p, o, s = _sections(plain), _sections(over), _sections(src)
    assert o["wall    w01"] == p["wall    w01"]                   # no override: the top-level pass
    assert o["suny    sky01"] not in (p["suny    sky01"], s["suny    sky01"])   # hue 120 + lightness -50
    assert o["tree    t01"] == s["tree    t01"]                    # override to no change: left as is


def test_skip_leaves_bytes(tmp_path):
    src = _dat(tmp_path)
    out = tmp_path / "out.DAT"
    stats = R.recolour_zone_dat(src, out, hue=120, skip_textures=["wall*", "tree    t01"])
    a, b = _sections(src), _sections(out)
    assert b["wall    w01"] == a["wall    w01"] and b["tree    t01"] == a["tree    t01"]
    assert b["suny    sky01"] != a["suny    sky01"]
    assert stats["skipped"] == 2


def test_color_ranges_are_passes_in_order(tmp_path):
    src = _dat(tmp_path)
    out = tmp_path / "out.DAT"
    # reds (hue ~0) turn green (+120); then the greens (hue ~120, including the new ones) darken
    R.recolour_zone_dat(src, out, hue=300, color_ranges=[
        {"hue_min": 350, "hue_max": 10, "hue": 120},
        {"hue_min": 100, "hue_max": 140, "lightness": -50}])
    data = out.read_bytes()
    pal = None
    for type_code, start, size, ds in R._parse_sections(data):
        if type_code == 0x20 and R.texture_name(data, ds) == "tree    t01":
            pal = data[ds + 0x3D:ds + 0x3D + 12]
    (b0, g0, r0), (b1, g1, r1), (b2, g2, r2) = pal[0:3], pal[4:7], pal[8:11]
    assert g0 > r0 and g0 <= 110                                  # red -> green -> darkened
    assert (r1, g1, b1) == (30, 30, 200)                          # blue: in no range, top-level hue ignored
    assert g2 <= 110                                              # green: darkened by the second pass


def test_nothing_to_change_keeps_texture_bytes(tmp_path):
    """Before, an all-zero call still re-packed every DXT endpoint through 8-bit and back,
    which drops low bits (565 value 1 -> 8 -> 0)."""
    p = tmp_path / "z.DAT"
    p.write_bytes(_dxt1("wall    w01", 0x0821, 0x0001) + _environment())
    out = tmp_path / "out.DAT"
    stats = R.recolour_zone_dat(p, out, fog_end=80.0)
    a, b = _sections(p), _sections(out)
    assert b["wall    w01"] == a["wall    w01"] and stats["skipped"] == 1
    assert struct.unpack_from("<f", b["environment"], 16 + 0x1C)[0] == 80.0


def test_environment_override(tmp_path):
    src = _dat(tmp_path)
    out = tmp_path / "out.DAT"
    R.recolour_zone_dat(src, out, hue=120, texture_overrides={
        "environment": {"hue": 0, "lightness": -50, "fog_tint": "#102030", "fog_end": 80, "fog_start": 0}})
    env = _sections(out)["environment"][16:]
    assert env[0x0C:0x0F] == bytes((0x20, 0x40, 0x60))           # lightness -50, no hue shift
    assert env[0x18:0x1C] == bytes((0x30, 0x20, 0x10, 0xFF))      # fog colour set (BGRA)
    assert struct.unpack_from("<f", env, 0x1C)[0] == 80.0
    assert struct.unpack_from("<f", env, 0x20)[0] == 0.0


def test_parse_options():
    assert R.parse_adjustments("hue_min=150,hue_max=340, hue=240 blend=multiply tint=#ff000080") == {
        "hue_min": 150, "hue_max": 340, "hue": 240, "blend_mode": "multiply", "tint": "#ff000080"}
    assert R.parse_texture_option("suny*:lightness=-70") == ("suny*", {"lightness": -70})
    assert R.parse_texture_option("environment:fog_end=80.5") == ("environment", {"fog_end": 80.5})
    with pytest.raises(ValueError):
        R.parse_adjustments("colour=red")
    with pytest.raises(ValueError):
        R.parse_texture_option("lightness=-70")


def test_cli_options_reach_the_engine():
    from xi.gear.xi_edit import recolor_kwargs
    kw = recolor_kwargs(hue=10, textures=("suny*:lightness=-70",), skips=("wall*",),
                        ranges=("hue_min=1,hue_max=2,hue=3",))
    assert kw["texture_overrides"] == {"suny*": {"lightness": -70}}
    assert kw["skip_textures"] == ["wall*"]
    assert kw["color_ranges"] == [{"hue_min": 1, "hue_max": 2, "hue": 3}]
    assert "texture_overrides" not in recolor_kwargs(hue=10)


def test_skip_everything_on_a_real_zone(root, tmp_path):
    """On a real zone model, skipping every texture leaves every 0x20 section byte for byte."""
    from xi.ftable.xi_core import load_all_tables, scan_file_ids
    from xi.zone.xi_inject import zone_model_file_id
    hits = scan_file_ids([zone_model_file_id(154)], load_all_tables())     # Dragon's Aery
    if not hits:
        pytest.skip("zone 154 not in this install")
    src = root / hits[0]["dat"]
    out = tmp_path / "out.DAT"
    stats = R.recolour_zone_dat(src, out, hue=160, skip_textures=["*"],
                                texture_overrides={"environment": {"fog_end": 120}})
    a, b = src.read_bytes(), out.read_bytes()
    assert stats["skipped"] == stats["total"] > 0
    for type_code, start, size, ds in R._parse_sections(a):
        if type_code == 0x20:
            assert a[start:start + size] == b[start:start + size]

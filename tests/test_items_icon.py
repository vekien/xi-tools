"""Item icons (xi.ui.items.xi_icon, `xi ui items icon export` / `import`).

Synthetic tests pin the format every retail icon has (0x91, a 16-character tag, an 8-bit
BITMAPINFOHEADER with biClrImportant 32, a BGRA palette with alpha 0..128, bottom-up
indices) and the PNG / BMP round trips. With FFXI_DIR, every icon of the install's main
item DATs must come back byte for byte through an exported PNG and BMP, and the CLI must
do the same on a copy of ROM/118/106.DAT.
"""
import io
import shutil
import struct
from pathlib import Path

import pytest
from PIL import Image

from xi.ui.items import xi_icon as I


def _sample_blob(no_alpha: bool = False) -> bytes:
    pal = bytearray()
    for i in range(256):
        a = 0 if no_alpha else (0 if i == 0 else (64 if i == 1 else 128))
        pal += bytes((i, 255 - i, (i * 7) & 0xFF, a))
    pix = bytes((x * 3 + y * 5) & 0xFF for y in range(32) for x in range(32))
    return I.build(bytes(pal), pix, "armor   12345")


def test_blob_layout_matches_retail():
    blob = _sample_blob()
    assert len(blob) == I.BLOB_LEN == 2105
    assert blob[0] == 0x91
    assert blob[1:17] == b"armor   12345   "
    head = struct.unpack_from("<IiiHHIIiiII", blob, 17)
    assert head == (40, 32, 32, 1, 8, 0, 0, 0, 0, 0, 32)


def test_png_round_trip_is_byte_identical():
    blob = _sample_blob()
    png = I.to_png(blob)
    img = Image.open(io.BytesIO(png))
    assert img.mode == "P" and img.size == (32, 32)
    assert img.info.get(I.TAG_KEY) == "armor   12345   "
    assert I.from_file(png) == blob


def test_png_alpha_is_twice_the_record_alpha():
    img = Image.open(io.BytesIO(I.to_png(_sample_blob()))).convert("RGBA")
    # bottom-up rows: the top-left pixel is the last row's first index
    pix = _sample_blob()[17 + 40 + 1024:]
    idx = pix[31 * 32]
    expected = 0 if idx == 0 else (128 if idx == 1 else 255)
    assert img.getpixel((0, 0))[3] == expected


def test_no_alpha_icon_exports_opaque_and_comes_back():
    blob = _sample_blob(no_alpha=True)
    png = I.to_png(blob)
    img = Image.open(io.BytesIO(png))
    assert img.info.get(I.NO_ALPHA_KEY) == "none"
    assert img.convert("RGBA").getpixel((5, 5))[3] == 255
    assert I.from_file(png) == blob


def test_bmp_round_trip_and_is_a_valid_bitmap():
    blob = _sample_blob()
    bmp = I.to_bmp(blob)
    assert bmp[:2] == b"BM" and len(bmp) == 14 + 40 + 1024 + 1024
    assert struct.unpack_from("<I", bmp, 10)[0] == 14 + 40 + 1024
    assert I.from_file(bmp, tag="armor   12345") == blob
    Image.open(io.BytesIO(bmp)).load()


def test_rgba_image_keeps_its_pixels():
    src = Image.open(io.BytesIO(I.to_png(_sample_blob()))).convert("RGBA")
    out = I.from_image(src, tag="item    custom")
    assert I.to_image(out).convert("RGBA").tobytes() == src.tobytes()
    assert out[1:17] == b"item    custom  "


def test_many_colours_are_quantized_deterministically_and_resized():
    g = Image.new("RGBA", (64, 64))
    g.putdata([(x * 4, y * 4, (x ^ y) * 4, 255 if x > 24 else 0) for y in range(64) for x in range(64)])
    a, b = I.from_image(g), I.from_image(g)
    assert a == b and len(a) == I.BLOB_LEN
    assert I.to_image(a).convert("RGBA").getpixel((0, 10))[3] == 0


def test_tag_precedence():
    png = I.to_png(_sample_blob())
    assert I.parse(I.from_file(png, tag="weapon  1"))["tag"] == "weapon  1       "
    flat = io.BytesIO()
    Image.open(io.BytesIO(png)).convert("RGBA").save(flat, "PNG")      # no tag chunk
    assert I.parse(I.from_file(flat.getvalue(), default_tag="icon    9"))["tag"].rstrip() == "icon    9"


def test_not_an_icon():
    with pytest.raises(I.IconError):
        I.parse(b"BM" + bytes(3000))
    with pytest.raises(I.IconError):
        I.from_file(b"not an image")
    with pytest.raises(I.IconError):
        I.build(bytes(1024), bytes(1024), "x" * 17)


# ── against the install ─────────────────────────────────────────────────────

ICON_DATS = ["ROM/118/106.DAT", "ROM/118/107.DAT", "ROM/118/108.DAT", "ROM/118/109.DAT",
             "ROM/118/110.DAT", "ROM/286/73.DAT"]


@pytest.mark.parametrize("rel", ICON_DATS)
def test_every_install_icon_round_trips(root, rel):
    from xi.ui.items.xi_layout import ICON_DATA, ICON_OFFSET
    from xi.ui.items.xi_parser import ItemDat
    p = root / rel
    if not p.exists():
        pytest.skip(f"{rel} not in this install")
    dat = ItemDat.load(p)
    n = 0
    for i in range(dat.count):
        rec = dat.record(i)
        size = struct.unpack_from("<I", rec, ICON_OFFSET)[0]
        if not size:
            continue
        blob = bytes(rec[ICON_DATA:ICON_DATA + size])
        assert I.from_file(I.to_png(blob)) == blob, f"{rel} record {i}: PNG round trip"
        assert I.from_file(I.to_bmp(blob), tag=I.parse(blob)["tag"]) == blob, f"{rel} record {i}: BMP"
        n += 1
    assert n


def test_cli_export_import_round_trip(root, tmp_path, monkeypatch):
    from click.testing import CliRunner
    import xi.xi_config as cfg
    import xi.ui.items.xi_items as items
    src = root / "ROM/118/106.DAT"
    if not src.exists():
        pytest.skip("ROM/118/106.DAT not in this install")
    game = tmp_path / "game"
    (game / "ROM/118").mkdir(parents=True)
    shutil.copy2(src, game / "ROM/118/106.DAT")
    monkeypatch.setattr(cfg, "FFXI_DIR", str(game))
    monkeypatch.setattr(items, "FFXI_DIR", str(game))
    runner = CliRunner()
    png = tmp_path / "chocobo_bedding.png"
    r = runner.invoke(items.icon_export_cmd, ["1", "-o", str(png)])
    assert r.exit_code == 0, r.output
    before = (game / "ROM/118/106.DAT").read_bytes()
    r = runner.invoke(items.icon_import_cmd, ["1", str(png), "--dry-run"])
    assert r.exit_code == 0, r.output
    assert (game / "ROM/118/106.DAT").read_bytes() == before
    r = runner.invoke(items.icon_import_cmd, ["1", str(png)])
    assert r.exit_code == 0, r.output
    assert (game / "ROM/118/106.DAT.base").exists()
    assert (game / "ROM/118/106.DAT").read_bytes() == before

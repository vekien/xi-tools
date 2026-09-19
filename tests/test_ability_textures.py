"""Texture replacement in an ability recipe (``textures``), the texture carry by name,
textures that share an id, and the renames that keep one name to one texture:
``validate_recipe``, ``load_recipe`` inlining a PNG path, and ``xi ability compose``.

A DAT is packed by hand and the DXT3 encoder (texconv) is stubbed unless a test is about
it; those skip without texconv. The last tests use the ``root`` fixture and skip without
FFXI_DIR."""
import base64
import json
import struct
import zlib
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from xi.ability import xi_compose as ac
from xi.ability import xi_inspect as ai
from xi.ability.xi_genedit import walk_stream
from xi.common.xi_section import encode_section_meta
from xi.entity.anim.xi_export import parse_sections
from xi.entity.mesh.xi_import import build_texture_section
from xi.fx import xi_copy

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = json.loads((REPO / "schema" / "ability_recipe.json").read_text(encoding="utf-8"))["examples"]
FLAG = 1 << 28                   # a ver_num bit of the section info word: compose keeps the source's


# ── Synthetic bytes ──────────────────────────────────────────────────────────────

def _png(w: int, h: int, rgba=(255, 200, 64, 255)) -> bytes:
    raw = b"".join(b"\0" + bytes(rgba) * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _uri(png: bytes) -> str:
    return ac.PNG_URI + base64.b64encode(png).decode("ascii")


def _section(name: bytes, type_code: int, body: bytes, flags: int = 0) -> bytes:
    body = body + b"\0" * (-len(body) % 16)
    return (name.ljust(4, b"\0") + struct.pack("<I", encode_section_meta(16 + len(body), type_code, flags))
            + b"\0" * 8 + body)


def _name16(name: str) -> bytes:
    return name.encode("ascii").ljust(16, b" ")


def _gen(name: bytes, link: bytes, *ids: bytes, kind: int = 0x0B) -> bytes:
    """A generator that draws ``link`` and names ``ids``: a zero head, the stream table at
    +0x80, sec 2 holding the setup op 0x01 (linkedId at +0x0C, linkedType ``kind`` at
    +0x21: 0x0B a mesh, 0x0E a sprite sheet), an end marker, then the ids (compose finds
    every id by a 4-byte scan)."""
    setup = bytearray(0x30)
    struct.pack_into("<I", setup, 0, 0x01 | (0x30 // 4) << 8)
    setup[0xC:0x10] = link
    setup[0x21] = kind
    return _section(name, ai.T_GEN, bytes(0x70) + struct.pack("<4I", 0xC0, 0x90, 0xC0, 0xC0)
                    + bytes(setup) + bytes.fromhex("00010000") + b"".join(ids))


def _sheet(name: bytes, tex: str) -> bytes:
    """A 0x21 SpriteSheetMesh: its one texture name at payload +0x08, one card."""
    return _section(name, ai.T_SPRITE, struct.pack("<HHBBBB", 1, 1, 0, 1, 1, 0) + _name16(tex) + bytes(152))


def _mesh(name: bytes, *texes: str) -> bytes:
    """A 0x1F ParticleMesh (marker 6): a material per texture name after the count table."""
    n = len(texes)
    shorts = n if n % 4 == 0 else n - n % 4 + 3
    head = struct.pack("<HHBBH", 6, 0, n, 0, 1) + struct.pack(f"<{shorts}H", *([1] * n + [0] * (shorts - n)))
    return _section(name, ai.T_PMESH, head + b"".join(_name16(t) for t in texes) + bytes(3 * 36))


def _tex(name: bytes, tex_name: str, fill: int, flags: int = 0) -> bytes:
    """A 4×4 DXT3 0x20 with the DAT's own spelling of its id (a short id space-padded)."""
    sec = bytearray(build_texture_section("", tex_name, 4, 4, bytes([fill]) * 16, dxt1=False))
    sec[0:4] = name
    struct.pack_into("<I", sec, 4, struct.unpack_from("<I", sec, 4)[0] | flags)
    return bytes(sec)


def _dat(tmp_path: Path, name: str, sections) -> Path:
    p = tmp_path / name
    fire = ac._stamp(ac._TEMPLATES[0x02], delay=0, ev={"dur": 30}, ref="g000")
    p.write_bytes(b"test" + struct.pack("<I", encode_section_meta(32, ai.T_DIR)) + b"\0" * 8 + ac._DIR_DATA
                  + b"".join(sections) + ac.build_routine("main", [fire], 30) + ac._END_SECTION)
    return p


@pytest.fixture
def fx(tmp_path: Path, monkeypatch) -> Path:
    """Fire's layout in miniature. g000 draws sheet `fai0`, which names "fxtest  fai01" —
    section `fai2`, not `fai0` (`fai0` is "fxtest  fai03"). g001 and g002 draw meshes that
    name "fxtest  ho", section `ho␣␣`, whose name spells its id. `tx09` is drawn by nothing."""
    monkeypatch.setattr("xi.xi_config.FFXI_DIR", str(tmp_path))
    return _dat(tmp_path, "fx.DAT", [
        _gen(b"g000", b"fai0", kind=0x0E), _gen(b"g001", b"ho  "), _gen(b"g002", b"ms02"),
        _sheet(b"fai0", "fxtest  fai01"), _mesh(b"ho  ", "fxtest  ho"), _mesh(b"ms02", "fxtest  ho"),
        _tex(b"fai0", "fxtest  fai03", 0x10), _tex(b"fai2", "fxtest  fai01", 0x20, FLAG),
        _tex(b"ho  ", "fxtest  ho", 0x30), _tex(b"tx09", "fxtest  tx09", 0x40)])


@pytest.fixture
def dup(tmp_path: Path, monkeypatch) -> Path:
    """Two textures under one id, told apart by name, as ROM/11/21's five `faid`: g000's
    sheet `sh0` binds "fxtest  dup1" (the first `dup`), g001's mesh `ms1` binds
    "fxtest  dup2" (the second)."""
    monkeypatch.setattr("xi.xi_config.FFXI_DIR", str(tmp_path))
    return _dat(tmp_path, "dup.DAT", [
        _gen(b"g000", b"sh0\0", kind=0x0E), _gen(b"g001", b"ms1\0"),
        _sheet(b"sh0", "fxtest  dup1"), _mesh(b"ms1", "fxtest  dup2"),
        _tex(b"dup\0", "fxtest  dup1", 0x11), _tex(b"dup\0", "fxtest  dup2", 0x22, FLAG)])


@pytest.fixture
def encoder(monkeypatch):
    """Stand in for texconv: a DXT3 section of the size compose asks for, every block
    0xEE. Returns the list of PNGs it was asked to encode."""
    calls = []

    def fake(png: bytes, where: str) -> bytes:
        calls.append(png)
        w, h = ac.texture_size(*ac.png_size(png))
        return build_texture_section("", "", w, h, b"\xee" * (w * h), dxt1=False)
    monkeypatch.setattr(ac, "_encode_texture", fake)
    return calls


def _sections(data: bytes) -> dict:
    return {(ai._clean(s.name), s.type_code): data[s.start:s.start + s.size] for s in parse_sections(data)}


def _by_name(data: bytes) -> dict:
    """0x20 sections by their 16-character name."""
    return {s[0x11:0x21]: s for (_, tc), s in _sections(data).items() if tc == ai.T_TEX}


def _recipe(*lanes, events=None, **more) -> dict:
    sources = {name: {"spec": str(spec)} for name, spec in lanes}
    return {"name": "mix", "sources": sources,
            "events": events or [{"from": lanes[0][0], "op": 2, "ref": "g000", "start": 0, "dur": 30}], **more}


def _fire(lane: str, ref: str, start: int = 0) -> dict:
    return {"from": lane, "op": 2, "ref": ref, "start": start, "dur": 30}


# ── validate_recipe ──────────────────────────────────────────────────────────────

def _errs(textures) -> list:
    return ac.validate_recipe(dict(EXAMPLES[2], textures=textures))


GOOD = EXAMPLES[2]["textures"][0]
BARE = {k: v for k, v in GOOD.items() if k != "name"}          # an entry without a name (the first texture of its id)


def test_schema_example_carries_a_texture():
    assert ac.validate_recipe(EXAMPLES[2]) == []
    png = base64.b64decode(GOOD["png"][len(ac.PNG_URI):])
    assert ac.png_problem(png) is None and ac.png_size(png) == (8, 8)


def test_validate_recipe_takes_a_data_uri_or_a_relative_path():
    assert _errs([dict(GOOD, png=_uri(_png(4, 4)))]) == []
    assert _errs([dict(GOOD, png=_uri(_png(4096, 1)))]) == []
    for path in ("ho.png", "tex/ho.PNG", "tex\\ho.png", "../shared/ho.png"):      # load_recipe decides ..
        assert _errs([dict(GOOD, png=path)]) == [], path
    assert _errs([GOOD, dict(GOOD, ref="obi")]) == []


@pytest.mark.parametrize("png, says", [
    (5, "png must be a data:image/png;base64, URI or a relative path ending .png"),
    ("ho.jpg", "png must be a data:image/png;base64, URI or a relative path ending .png"),
    ("/tmp/ho.png", "png '/tmp/ho.png': a path must be relative to the recipe's folder"),
    ("C:\\ho.png", "png 'C:\\\\ho.png': a path must be relative to the recipe's folder"),
    ("\\\\srv\\share\\ho.png", "png '\\\\\\\\srv\\\\share\\\\ho.png': a path must be relative to the recipe's folder"),
    ("data:image/jpeg;base64,AAAA", "png: a data URI must start data:image/png;base64,"),
    (ac.PNG_URI + "iVBOR w0K", "png: the data URI is not valid base64 (standard alphabet, padded)"),
    (ac.PNG_URI + "iVBORw0KGgo", "png: the data URI is not valid base64 (standard alphabet, padded)"),
    (_uri(b"GIF89a" + bytes(40)), "png is not a PNG (no PNG signature)"),
    (_uri(b"\x89PNG\r\n\x1a\n" + bytes(40)), "png is not a PNG (no IHDR header)"),
    (_uri(_png(4097, 4)), "png is 4097×4; each side must be 1–4096"),
    (_uri(_png(0, 4)), "png is 0×4; each side must be 1–4096"),
])
def test_validate_recipe_names_a_bad_png(png, says):
    assert _errs([dict(GOOD, png=png)]) == [f"textures[0]: {says}"]


def test_validate_recipe_caps_the_png_at_2_mib():
    big = _png(4, 4) + b"\0" * ac.PNG_MAX_BYTES                     # trailing bytes: still a PNG header
    assert _errs([dict(GOOD, png=_uri(big))]) == ["textures[0]: png is larger than 2 MiB"]
    just = _png(4, 4)
    just += b"\0" * (ac.PNG_MAX_BYTES - len(just) + 1)             # one byte over, within the base64 bound
    assert _errs([dict(GOOD, png=_uri(just))]) == [
        f"textures[0]: png is {ac.PNG_MAX_BYTES + 1:,} bytes; a texture PNG may be 2 MiB at most"]


def test_validate_recipe_names_the_texture_entry():
    assert _errs({}) == ["textures must be a list"]
    assert _errs([5]) == ["textures[0]: must be an object"]
    assert _errs([dict(GOOD, alpha="raw")]) == ["textures[0]: unknown key 'alpha'"]
    assert _errs([dict(GOOD, lane="nope")]) == ["textures[0]: lane 'nope' is not in sources"]
    assert _errs([dict(GOOD, ref="toolong")]) == ["textures[0]: ref must be a 1–4 character section id"]
    assert _errs([{"lane": "vfx", "ref": "ho"}]) == [
        "textures[0]: png must be a data:image/png;base64, URI or a relative path ending .png"]
    # One entry per lane + ref + name, trailing padding ignored.
    assert _errs([GOOD, dict(GOOD, ref="ho  ")]) == [
        "textures[1]: lane 'vfx' ref 'ho  ' name 'carel3  ho' is already in textures[0]"]
    assert _errs([BARE, dict(BARE, ref="ho  ")]) == ["textures[1]: lane 'vfx' ref 'ho  ' is already in textures[0]"]
    assert ac.validate_recipe(dict(EXAMPLES[0], textures=[])) == []


def test_validate_recipe_takes_a_texture_name():
    assert GOOD["name"] == "carel3  ho" and _errs([BARE]) == []
    # ROM/11/21's five `faid`, each by name, beside an entry without one (the first `faid`).
    faid = [dict(BARE, ref="faid", name=f"faida01 faida0{i}") for i in range(1, 6)]
    assert _errs(faid + [dict(BARE, ref="faid")]) == []
    assert _errs([dict(GOOD, name="0123456789abcdef"), dict(GOOD, ref="x", name="  lead")]) == []
    # Trailing spaces are padding, not part of the name.
    assert _errs([GOOD, dict(GOOD, name="carel3  ho      ")]) == [
        "textures[1]: lane 'vfx' ref 'ho' name 'carel3  ho      ' is already in textures[0]"]
    # Only a texture entry has a name.
    curve = dict(EXAMPLES[1]["curves"][0], name="k001")
    assert ac.validate_recipe(dict(EXAMPLES[1], curves=[curve])) == ["curves[0]: unknown key 'name'"]


@pytest.mark.parametrize("name", ["", "    ", "0123456789abcdefg", "carel3\tho", "café", 5, None])
def test_validate_recipe_names_a_bad_texture_name(name):
    assert _errs([dict(GOOD, name=name)]) == [
        "textures[0]: name must be a texture's 16-character name: 1–16 printable ASCII characters, not only spaces"]


# ── load_recipe ──────────────────────────────────────────────────────────────────

def _write(tmp_path: Path, png, name="r.recipe.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(dict(EXAMPLES[2], textures=[dict(GOOD, png=png)])), encoding="utf-8")
    return p


def test_load_recipe_inlines_a_png_beside_it(tmp_path: Path):
    (tmp_path / "tex").mkdir()
    png = _png(16, 8)
    (tmp_path / "tex" / "gold.png").write_bytes(png)
    inlined = []
    r = ac.load_recipe(_write(tmp_path, "tex/gold.png"), inlined=inlined)
    assert r["textures"][0]["png"] == _uri(png) and inlined == ["tex/gold.png"]
    # A data URI is left as it is, and nothing is reported inlined.
    inlined = []
    assert ac.load_recipe(_write(tmp_path, GOOD["png"]), inlined=inlined)["textures"][0]["png"] == GOOD["png"]
    assert inlined == []


def test_load_recipe_keeps_a_png_path_inside_the_recipes_folder(tmp_path: Path):
    (tmp_path / "mix").mkdir()
    (tmp_path / "outside.png").write_bytes(_png(4, 4))
    with pytest.raises(click.ClickException, match=r"textures\[0\]: png '../outside.png' is outside the recipe's folder"):
        ac.load_recipe(_write(tmp_path / "mix", "../outside.png"))
    # A path that goes up and back in stays inside, and is read.
    (tmp_path / "mix" / "in.png").write_bytes(_png(4, 4))
    assert ac.load_recipe(_write(tmp_path / "mix", "../mix/in.png"))["textures"][0]["png"] == _uri(_png(4, 4))
    with pytest.raises(click.ClickException, match="a path must be relative to the recipe's folder"):
        ac.load_recipe(_write(tmp_path / "mix", str(tmp_path / "outside.png")))
    with pytest.raises(click.ClickException, match=r"textures\[0\]: cannot read png 'gone.png'"):
        ac.load_recipe(_write(tmp_path / "mix", "gone.png"))
    (tmp_path / "mix" / "fake.png").write_bytes(b"not a png")
    with pytest.raises(click.ClickException, match=r"textures\[0\]: fake.png is not a PNG"):
        ac.load_recipe(_write(tmp_path / "mix", "fake.png"))


def test_compose_takes_only_a_data_uri(fx: Path, encoder):
    with pytest.raises(click.ClickException, match=r"textures\[0\]: png must be a data:image/png;base64, URI here"):
        ac.compose(_recipe(("vfx", fx), textures=[{"lane": "vfx", "ref": "fai2", "png": "gold.png"}]))


# ── Carry by name ────────────────────────────────────────────────────────────────

def test_texture_name_fields_read_the_mesh_layouts():
    assert xi_copy.texture_name_fields(_sheet(b"fai0", "fxtest  fai01")) == [0x18]
    mesh = _mesh(b"m2", "a       one", "b       two")
    fields = xi_copy.texture_name_fields(mesh)
    assert [mesh[o:o + 16] for o in fields] == [_name16("a       one"), _name16("b       two")]
    # Five entries: the count table rounds to 7 shorts, not up to 16 bytes.
    five = _mesh(b"m5", *(f"n{i}" for i in range(5)))
    assert xi_copy.texture_name_fields(five)[0] == 0x10 + 8 + 2 * 7
    # A zone mesh is searched for the names asked about, as whole 16-byte fields.
    zone = _section(b"zm  ", 0x2E, bytes(40) + b"zone    top\0\0\0\0\0" + bytes(8) + b"zone    topper  ")
    keys = [xi_copy.texture_key(b"zone    top     ")]
    assert xi_copy.texture_name_fields(zone, keys) == [16 + 40]


def test_a_mesh_brings_the_texture_it_names(fx: Path):
    # Fire's `fai0` sheet names "fai01   fai01", which is section `fai2`; the id scan
    # alone carried `fai0` (the sheet's own id) and missed it.
    data = fx.read_bytes()
    secs = parse_sections(data)
    g000 = next(s for s in secs if s.name == "g000")
    assert xi_copy._effect_deps(data, secs, g000) == [b"fai0", b"fai2"]
    (c,) = ac.compose(_recipe(("vfx", fx)))
    assert {"fai0(0x21)", "fai0(0x20)", "fai2(0x20)"} <= set(c.sections)
    assert _name16("fxtest  fai01") in _by_name(c.data)


def test_without_textures_an_id_bound_source_composes_as_before(fx: Path, monkeypatch):
    # g001 and g002 draw a texture whose name spells its id: the name binding finds what
    # the id scan did, and the output is byte for byte the id scan's.
    recipe = _recipe(("vfx", fx), events=[_fire("vfx", "g001"), _fire("vfx", "g002", 10)])
    (now,) = ac.compose(recipe)
    monkeypatch.setattr(xi_copy, "texture_name_fields", lambda sec, names=(): [])
    (before,) = ac.compose(recipe)
    assert now.data == before.data and now.textures == []


# ── Replacing a texture ──────────────────────────────────────────────────────────

def test_a_replacement_keeps_the_sections_id_name_and_flags(fx: Path, encoder):
    png = _png(40, 20)
    (c,) = ac.compose(_recipe(("vfx", fx), textures=[{"lane": "vfx", "ref": "fai2", "png": _uri(png)}]))
    sec = _sections(c.data)[("fai2", ai.T_TEX)]
    assert sec[:4] == b"fai2" and sec[0x11:0x21] == _name16("fxtest  fai01")
    info = struct.unpack_from("<I", sec, 4)[0]
    assert info & FLAG and info & 0x7F == ai.T_TEX and ((info >> 7) & 0x7FFFF) * 16 == len(sec)
    assert struct.unpack_from("<II", sec, 0x25) == (32, 16)                  # nearest powers of two
    assert sec[0x49:0x4D] == b"3TXD" and sec[0x55:0x55 + 4] == b"\xee" * 4
    assert _sections(c.data)[("fai0", ai.T_TEX)][0x55] == 0x10              # the texture beside it is untouched
    assert c.textures == [{"lane": "vfx", "ref": "fai2", "name": "fxtest  fai01   ",
                           "from": {"w": 4, "h": 4, "format": "DXT3"}, "to": {"w": 32, "h": 16, "format": "DXT3"},
                           "users": ["g000"]}]


def test_one_png_is_encoded_once_per_compose(fx: Path, encoder):
    png = _uri(_png(8, 8))
    recipe = _recipe(("vfx", fx), events=[_fire("vfx", "g000"), _fire("vfx", "g001", 10), _fire("vfx", "g002", 20)],
                     textures=[{"lane": "vfx", "ref": "fai2", "png": png}, {"lane": "vfx", "ref": "ho", "png": png}])
    (c,) = ac.compose(recipe)
    assert len(encoder) == 1
    assert [(t["ref"], t["users"]) for t in c.textures] == [("fai2", ["g000"]), ("ho", ["g001", "g002"])]


def test_an_unused_replacement_is_reported_and_a_missing_one_is_an_error(fx: Path, encoder):
    (c,) = ac.compose(_recipe(("vfx", fx), textures=[{"lane": "vfx", "ref": "tx09", "png": _uri(_png(300, 3))}]))
    assert encoder == [] and _sections(c.data).get(("tx09", ai.T_TEX)) is None
    assert c.textures == [{"lane": "vfx", "ref": "tx09", "name": "fxtest  tx09    ",
                           "from": {"w": 4, "h": 4, "format": "DXT3"}, "to": {"w": 256, "h": 4, "format": "DXT3"},
                           "users": [], "unused": True}]
    with pytest.raises(click.ClickException) as ei:
        ac.compose(_recipe(("vfx", fx), textures=[{"lane": "vfx", "ref": "zz", "png": _uri(_png(4, 4))}]))
    rel = ai.resolve_targets(str(fx))[0].rel
    assert ei.value.message == f"textures[0]: lane 'vfx' ({rel}) has no texture 'zz'"


def test_a_carried_texture_the_generator_does_not_draw_is_unused(fx: Path, encoder):
    # g000 names `fai0`, so the 0x20 `fai0` ("fxtest  fai03") comes along with the sheet of
    # that id, but the sheet draws "fxtest  fai01" (`fai2`): replacing `fai0` shows nothing.
    (c,) = ac.compose(_recipe(("vfx", fx), textures=[{"lane": "vfx", "ref": "fai0", "png": _uri(_png(8, 8))}]))
    assert _sections(c.data)[("fai0", ai.T_TEX)][0x55] == 0xEE                 # still carried, and replaced
    (t,) = c.textures
    assert (t["ref"], t["users"], t.get("unused")) == ("fai0", [], True)
    assert t["to"] == {"w": 8, "h": 8, "format": "DXT3"}


def test_texture_sizes_are_powers_of_two_from_4_to_256():
    assert ac.texture_size(64, 64) == (64, 64)
    assert ac.texture_size(1, 2) == (4, 4)
    assert ac.texture_size(40, 20) == (32, 16)
    assert ac.texture_size(48, 96) == (32, 64)              # a tie takes the smaller
    assert ac.texture_size(200, 4096) == (256, 256)


def test_a_missing_texconv_says_what_to_install(fx: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setattr("xi.xi_config.TEXCONV_PATH", str(tmp_path / "nowhere" / "texconv.exe"))
    with pytest.raises(click.ClickException) as ei:
        ac.compose(_recipe(("vfx", fx), textures=[{"lane": "vfx", "ref": "fai2", "png": _uri(_png(4, 4))}]))
    msg = ei.value.message
    assert msg.startswith("textures[0]: replacing a texture needs texconv") and "TEXCONV_PATH" in msg


def test_a_texconv_failure_says_why(monkeypatch):
    # texconv is there but fails (its stderr, or an exe that will not start): the reason
    # it gave reaches the message.
    def fail(png, dds, requested_format=None):
        raise RuntimeError("texconv exited with code 1: bad png")
    monkeypatch.setattr("xi.utils.xi_core.find_texconv", lambda p: p)
    monkeypatch.setattr("xi.utils.xi_core.convert_png_to_dds", fail)
    with pytest.raises(click.ClickException) as ei:
        ac._encode_texture(_png(16, 16, (255, 0, 0, 255)), "textures[0]")
    assert ei.value.message == "textures[0]: texconv could not encode the PNG as DXT3: texconv exited with code 1: bad png"


def test_compose_json_reports_the_textures(fx: Path, tmp_path: Path, encoder):
    rp = tmp_path / "mix.recipe.json"
    rp.write_text(json.dumps(_recipe(("vfx", fx), textures=[{"lane": "vfx", "ref": "fai2", "png": _uri(_png(4, 4))}])),
                  encoding="utf-8")
    r = CliRunner().invoke(ac.compose_cmd, [str(rp), "--out", str(tmp_path / "out"), "--json"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    (entry,) = json.loads(r.output)
    assert [t["ref"] for t in entry["textures"]] == ["fai2"]
    assert json.loads((tmp_path / "out" / "mix.report.json").read_text(encoding="utf-8")) == [entry]


# ── One name, one texture ────────────────────────────────────────────────────────

def _drawn(c, gen: str) -> bytes:
    """The 16-char name the generator's sheet or mesh binds in the composed DAT."""
    secs = _sections(c.data)
    body = secs[(gen, ai.T_GEN)]
    link = body[0x9C:0xA0]                                 # the setup op's linkedId
    mesh = next(s for (n, tc), s in secs.items() if tc in (ai.T_SPRITE, ai.T_PMESH) and s[:4] == link)
    return mesh[xi_copy.texture_name_fields(mesh)[0]:][:16]


def test_a_replaced_texture_gets_its_own_name_beside_the_original(fx: Path, encoder):
    # Two lanes on one spec; b replaces `fai2`. Both "fxtest  fai01" textures would reach
    # the output, and the client binds the first: b's goes out as "fxtest_1fai01" and b's
    # copy of the sheet names that, under ids of its own.
    recipe = _recipe(("a", fx), ("b", fx), events=[_fire("a", "g000"), _fire("b", "g000", 10)],
                     textures=[{"lane": "b", "ref": "fai2", "png": _uri(_png(8, 8))}])
    (c,) = ac.compose(recipe)
    names = _by_name(c.data)
    b_gen = c.renames["b:g000"]
    assert _drawn(c, "g000") == _name16("fxtest  fai01") and names[_name16("fxtest  fai01")][0x55] == 0x20
    assert _drawn(c, b_gen) == _name16("fxtest_1fai01") and names[_name16("fxtest_1fai01")][0x55] == 0xEE
    # The patched sheet and the renamed texture take new ids; a's keep theirs.
    assert c.renames["b:fai0"] not in ("fai0", "fai2") and c.renames["b:fai2"] not in ("fai0", "fai2", c.renames["b:fai0"])
    assert names[_name16("fxtest_1fai01")][:4] == ac._name4(c.renames["b:fai2"])
    assert [(t["lane"], t["ref"], t["name"], t.get("renamed_from"), t["users"]) for t in c.textures] == [
        ("b", "fai2", "fxtest_1fai01   ", "fxtest  fai01   ", [b_gen])]


def test_two_sources_sharing_a_name_keep_their_own_textures(fx: Path, tmp_path: Path):
    # No replacement: a second DAT simply has a different "fxtest  ho". The later lane's is
    # renamed, the lane's meshes follow, and the report lists the rename.
    other = _dat(tmp_path, "other.DAT", [
        _gen(b"g001", b"ho  "), _mesh(b"ho  ", "fxtest  ho"), _tex(b"ho  ", "fxtest  ho", 0x77)])
    recipe = _recipe(("a", fx), ("b", other), events=[_fire("a", "g001"), _fire("b", "g001", 10)])
    (c,) = ac.compose(recipe)
    b_gen = c.renames["b:g001"]
    assert _drawn(c, "g001") == _name16("fxtest  ho") and _drawn(c, b_gen) == _name16("fxtest_1ho")
    assert _by_name(c.data)[_name16("fxtest_1ho")][0x55] == 0x77
    (t,) = c.textures
    assert (t["lane"], t["ref"], t["name"], t["renamed_from"], t["users"]) == (
        "b", "ho", "fxtest_1ho      ", "fxtest  ho      ", [b_gen])
    assert t["from"] == t["to"] == {"w": 4, "h": 4, "format": "DXT3"} and "unused" not in t


def test_identical_textures_are_still_shared(fx: Path):
    recipe = _recipe(("a", fx), ("b", fx), events=[_fire("a", "g000"), _fire("b", "g000", 10)])
    (c,) = ac.compose(recipe)
    assert c.renames == {} and c.textures == []


# ── Textures that share an id ────────────────────────────────────────────────────

def _tex_ids(data: bytes) -> list:
    return [ai._clean(s.name) for s in parse_sections(data) if s.type_code == ai.T_TEX]


BOTH = [_fire("vfx", "g000"), _fire("vfx", "g001", 10)]


def test_textures_sharing_an_id_each_come_along_under_an_id_of_their_own(dup: Path):
    # Each mesh binds its texture by name, so both come along; the output needs one id per
    # section, and the second gets a new one (an id names the first, which keeps it).
    (c,) = ac.compose(_recipe(("vfx", dup), events=BOTH))
    ids, names = _tex_ids(c.data), _by_name(c.data)
    assert len(ids) == 2 and len(set(ids)) == 2 and names[_name16("fxtest  dup1")][:4] == b"dup\0"
    assert _drawn(c, "g000") == _name16("fxtest  dup1") and names[_name16("fxtest  dup1")][0x55] == 0x11
    assert _drawn(c, "g001") == _name16("fxtest  dup2") and names[_name16("fxtest  dup2")][0x55] == 0x22
    assert struct.unpack_from("<I", names[_name16("fxtest  dup2")], 4)[0] & FLAG
    assert c.renames == {} and c.textures == []
    # g001 alone still brings the texture it draws.
    (c,) = ac.compose(_recipe(("vfx", dup), events=[_fire("vfx", "g001")]))
    assert _drawn(c, "g001") == _name16("fxtest  dup2") and _by_name(c.data)[_name16("fxtest  dup2")][0x55] == 0x22


def test_a_replacement_by_name_lands_on_that_texture_alone(dup: Path, encoder):
    entry = {"lane": "vfx", "ref": "dup", "name": "fxtest  dup2", "png": _uri(_png(8, 8))}
    (c,) = ac.compose(_recipe(("vfx", dup), events=BOTH, textures=[entry]))
    names = _by_name(c.data)
    assert names[_name16("fxtest  dup2")][0x55] == 0xEE and names[_name16("fxtest  dup1")][0x55] == 0x11
    assert struct.unpack_from("<I", names[_name16("fxtest  dup2")], 4)[0] & FLAG
    assert c.textures == [{"lane": "vfx", "ref": "dup", "name": "fxtest  dup2    ",
                           "from": {"w": 4, "h": 4, "format": "DXT3"}, "to": {"w": 8, "h": 8, "format": "DXT3"},
                           "users": ["g001"]}]


def test_an_entry_without_a_name_replaces_the_first_texture_of_its_id(dup: Path, encoder):
    def compose(**name):
        entry = dict(name, lane="vfx", ref="dup", png=_uri(_png(8, 8)))
        return ac.compose(_recipe(("vfx", dup), events=BOTH, textures=[entry]))[0]
    bare = compose()
    names = _by_name(bare.data)
    assert names[_name16("fxtest  dup1")][0x55] == 0xEE and names[_name16("fxtest  dup2")][0x55] == 0x22
    assert [(t["ref"], t["name"], t["users"]) for t in bare.textures] == [("dup", "fxtest  dup1    ", ["g000"])]
    named = compose(name="fxtest  dup1")
    assert named.data == bare.data and named.textures == bare.textures


def test_a_texture_entry_names_one_section_the_lane_has(dup: Path, encoder):
    rel = ai.resolve_targets(str(dup))[0].rel
    png = _uri(_png(4, 4))
    with pytest.raises(click.ClickException) as ei:
        ac.compose(_recipe(("vfx", dup), textures=[{"lane": "vfx", "ref": "dup", "name": "fxtest  dup9", "png": png}]))
    assert ei.value.message == (f"textures[0]: lane 'vfx' ({rel}) has no texture 'dup' named 'fxtest  dup9'; "
                                f"its 'dup' textures are 'fxtest  dup1', 'fxtest  dup2'")
    # An entry without a name and one naming the first `dup` are the same section.
    bare = {"lane": "vfx", "ref": "dup", "png": png}
    with pytest.raises(click.ClickException) as ei:
        ac.compose(_recipe(("vfx", dup), textures=[bare, dict(bare, name="fxtest  dup1")]))
    assert ei.value.message == ("textures[1]: lane 'vfx' texture 'dup' named 'fxtest  dup1' is already replaced "
                                "by textures[0] (an entry without a name replaces the first 'dup')")
    (c,) = ac.compose(_recipe(("vfx", dup), events=BOTH, textures=[bare, dict(bare, name="fxtest  dup2")]))
    assert [t["name"] for t in c.textures] == ["fxtest  dup1    ", "fxtest  dup2    "]


def test_two_lanes_share_a_texture_past_its_id_and_rename_a_replaced_one(dup: Path, encoder):
    events = [_fire("a", "g001"), _fire("b", "g001", 10)]
    # The same bytes from both lanes: one copy, as any identical section.
    (c,) = ac.compose(_recipe(("a", dup), ("b", dup), events=events))
    assert len(_tex_ids(c.data)) == 2 and c.renames == {}
    # b replaces the second `dup`: b's goes out under a new namespace, and b's mesh names it.
    entry = {"lane": "b", "ref": "dup", "name": "fxtest  dup2", "png": _uri(_png(8, 8))}
    (c,) = ac.compose(_recipe(("a", dup), ("b", dup), events=events, textures=[entry]))
    b_gen, names, ids = c.renames["b:g001"], _by_name(c.data), _tex_ids(c.data)
    assert len(ids) == len(set(ids))
    assert _drawn(c, "g001") == _name16("fxtest  dup2") and names[_name16("fxtest  dup2")][0x55] == 0x22
    assert _drawn(c, b_gen) == _name16("fxtest_1dup2") and names[_name16("fxtest_1dup2")][0x55] == 0xEE
    (t,) = c.textures
    assert (t["lane"], t["ref"], t["name"], t["renamed_from"], t["users"]) == (
        "b", "dup", "fxtest_1dup2    ", "fxtest  dup2    ", [b_gen])


# ── Bag renames ──────────────────────────────────────────────────────────────────

def test_a_rename_patches_an_id_the_dat_pads_with_spaces():
    # Cure III spells its texture and mesh `ho␣␣`, and so do its generators. A rename is
    # keyed by the clean name; the patch has to find the DAT's spelling.
    bag = ac.Bag()
    body = _gen(b"g001", b"ho  ", b"k\0\0\0")
    bag.add("b", "g001", ai.T_GEN, body)
    bag.renames[("b", "ho")] = "ho0"
    bag.renames[("b", "k")] = "k1"
    ac._apply_renames(bag)
    out = bag.items[("g001", ai.T_GEN)]
    assert out.count(b"ho0\0") == 1 and b"ho  " not in out and out.count(b"k1\0\0") == 1


def test_a_rename_covers_a_section_the_lane_shared():
    # Lane b's `kir1` mesh is a's to the byte, so b shares it; then b's `kir1` texture
    # differs and the name goes to `kir0` for b. b's generators will name `kir0`, so b
    # needs its own `kir0` mesh too.
    bag = ac.Bag()
    mesh, tex_a, tex_b = _mesh(b"kir1", "x       kir1"), _tex(b"kir1", "x       kir1", 1), _tex(b"kir1", "y       kir1", 2)
    for lane, tex in (("a", tex_a), ("b", tex_b)):
        bag.add(lane, "kir1", ai.T_PMESH, mesh)
        new = bag.add(lane, "kir1", ai.T_TEX, tex)
    assert new == bag.renames[("b", "kir1")] != "kir1"
    assert bag.origin[(new, ai.T_PMESH)] == "b" and bag.items[(new, ai.T_PMESH)] == ac._name4(new) + mesh[4:]
    assert bag.items[("kir1", ai.T_PMESH)] == mesh and bag.origin[("kir1", ai.T_PMESH)] == "a"


def test_colliding_ids_compose_to_resources_that_exist(fx: Path, tmp_path: Path):
    # Both Bag fixes through compose: b's `ho␣␣` texture has another name and other pixels,
    # its mesh is a's to the byte. b's generator names the renamed id, which holds b's own
    # mesh and texture.
    other = _dat(tmp_path, "other.DAT", [
        _gen(b"g001", b"ho  "), _mesh(b"ho  ", "fxtest  ho"), _tex(b"ho  ", "other   ho", 0x77)])
    (c,) = ac.compose(_recipe(("a", fx), ("b", other), events=[_fire("a", "g001"), _fire("b", "g001", 10)]))
    new = c.renames["b:ho"]
    secs = _sections(c.data)
    b_gen = secs[(c.renames["b:g001"], ai.T_GEN)]
    assert b_gen.count(ac._name4(new)) == 1 and b"ho  " not in b_gen[16:]
    assert (new, ai.T_PMESH) in secs and secs[(new, ai.T_TEX)][0x55] == 0x77
    assert secs[("g001", ai.T_GEN)].count(b"ho  ") == 1 and secs[("ho", ai.T_TEX)][0x55] == 0x30


# ── With texconv ─────────────────────────────────────────────────────────────────

@pytest.fixture
def texconv():
    from xi import xi_config
    from xi.utils.xi_core import find_texconv
    try:
        return find_texconv(xi_config.TEXCONV_PATH)
    except FileNotFoundError:
        pytest.skip("texconv not available (TEXCONV_PATH)")


def test_texconv_encodes_dxt3_with_half_scale_alpha(texconv, fx: Path):
    png = _png(64, 32, (255, 255, 255, 255))
    sec = ac._encode_texture(png, "textures[0]")
    assert sec[0x10] == 0xA1 and sec[0x49:0x4D] == b"3TXD"
    assert struct.unpack_from("<II", sec, 0x25) == (64, 32)
    assert struct.unpack_from("<II", sec, 0x4D) == (64 * 32, 16 * 16)      # DXT byte size, bytes per block row
    assert len(sec) == (0x55 + 64 * 32 + 15) & ~15
    assert sec[0x55:0x55 + 8] == b"\x88" * 8                                # opaque stores as 8 of 15
    (c,) = ac.compose(_recipe(("vfx", fx), textures=[{"lane": "vfx", "ref": "fai2", "png": _uri(png)}]))
    out = _sections(c.data)[("fai2", ai.T_TEX)]
    assert out[:4] == b"fai2" and out[0x11:0x21] == _name16("fxtest  fai01") and struct.unpack_from("<I", out, 4)[0] & FLAG
    assert out[0x10] == sec[0x10] and out[0x21:] == sec[0x21:]              # the encoded header and pixels


# ── With the game ────────────────────────────────────────────────────────────────

def test_fire_sheets_and_meshes_bring_the_textures_they_name(root: Path):
    # Fire (spell:144, ROM/11/17): g004's sheet `fai0` binds "fai01   fai01" (`fai2`) and
    # g003's mesh `fa04` binds "fai01   fai04" (`fai3`).
    for gen, tex in (("g004", "fai2"), ("g003", "fai3")):
        (c,) = ac.compose({"name": "fire", "sources": {"vfx": {"spec": "spell:144"}},
                           "events": [{"from": "vfx", "op": 2, "ref": gen, "start": 0, "dur": 30}]})
        assert f"{tex}(0x20)" in c.sections, (gen, c.sections)


def test_fire_reports_the_generators_that_draw_each_texture(root: Path, encoder):
    # g004's sheet `fai0` draws `fai2`; the 0x20 `fai0` it carries beside that sheet is
    # drawn only by g002's sheet `fai2` ("fai01   fai03").
    def users(fired, ref):
        (c,) = ac.compose({"name": "fire", "sources": {"vfx": {"spec": "spell:144"}},
                           "events": [{"from": "vfx", "op": 2, "ref": g, "start": i, "dur": 10}
                                      for i, g in enumerate(fired)],
                           "textures": [{"lane": "vfx", "ref": ref, "png": _uri(_png(8, 8))}]})
        (t,) = c.textures
        return t["users"], t.get("unused", False)
    every = ["g004", "g000", "g001", "g002", "g003"]
    assert users(["g004"], "fai0") == ([], True)
    assert users(every, "fai0") == (["g002"], False)
    assert users(every, "fai2") == (["g004", "g001"], False)


def test_a_race_bound_lane_is_replaced_on_every_race(root: Path, encoder):
    # ws:1 composes once per race from that race's DAT; the PNG is encoded once and lands
    # in each race's copy of `0110` under that copy's own name.
    recipe = ac.recipe_from("ws:1:HumeMale")
    recipe["sources"] = {"motion": {"spec": "ws:1"}}
    recipe["textures"] = [{"lane": "motion", "ref": "0110", "png": _uri(_png(16, 16))}]
    out = ac.compose(recipe)
    assert [c.race for c in out] == list(ac.RACE_NAMES) and len(encoder) == 1
    for c in out:
        sec = _sections(c.data)[("0110", ai.T_TEX)]
        assert sec[0x11:0x21] == b"0011    0110    " and sec[0x55] == 0xEE, c.race
        assert c.textures[0]["users"] and "unused" not in c.textures[0], c.race


def test_cure3_without_textures_composes_as_before(root: Path, monkeypatch):
    # Cure III's texture names all spell their ids, so the name binding carries nothing
    # new: every generator composes byte for byte as the id scan alone did.
    m = ai.Model.load(ai.resolve_targets("spell:3")[0].path)
    gens = [ai._clean(s.name) for s in m.sections if s.type_code == ai.T_GEN]
    recipes = [ac.recipe_from("spell:3"),
               {"name": "c3", "sources": {"vfx": {"spec": "spell:3"}},
                "events": [_fire("vfx", g, i) for i, g in enumerate(gens)]}]
    now = [ac.compose(r)[0].data for r in recipes]
    monkeypatch.setattr(xi_copy, "texture_name_fields", lambda sec, names=(): [])
    assert now == [ac.compose(r)[0].data for r in recipes]


def test_schema_example_replaces_cure3s_texture(root: Path, texconv):
    # The third example: Cure III's "carel3  ho" (section `ho␣␣`, 64×64) replaced by an
    # 8×8 PNG, drawn by ho00's mesh and sh01's.
    (c,) = ac.compose(EXAMPLES[2])
    sec = _by_name(c.data)[b"carel3  ho      "]
    assert sec[:4] == b"ho  " and sec[0x10] == 0xA1 and sec[0x49:0x4D] == b"3TXD"
    assert struct.unpack_from("<II", sec, 0x25) == (8, 8)
    (t,) = c.textures
    assert (t["ref"], t["name"], t["users"]) == ("ho", "carel3  ho      ", ["ho00", "sh01"])
    assert t["from"] == {"w": 64, "h": 64, "format": "DXT3"} and t["to"] == {"w": 8, "h": 8, "format": "DXT3"}


_LINKED = {0x0B: (ai.T_PMESH, 0x2E), 0x0E: (ai.T_SPRITE,), 0x39: (ai.T_SPRITE,)}


def _textures_drawn(data: bytes, names) -> set:
    """The texture names (texture_key) the DAT's generators draw: what the mesh or sprite
    sheet their setup op links (sec 2 op 0x01: id at +0x0C, linkedType at +0x21) binds."""
    first = {}
    for s in parse_sections(data):
        first.setdefault((ai._clean(s.name), s.type_code), data[s.start:s.start + s.size])
    drawn = set()
    for (gen, tc), body in first.items():
        if tc != ai.T_GEN:
            continue
        setup = next((pos for op, pos, size in walk_stream(body, 2) if op == 0x01 and size > 0x21), None)
        if setup is None:
            continue
        link = ai._clean(body[setup + 0xC:setup + 0x10].decode("latin1"))
        for mesh in (first.get((link, t)) for t in _LINKED.get(body[setup + 0x21], ())):
            if mesh is not None:
                drawn.update(xi_copy.texture_key(mesh[o:o + 16]) for o in xi_copy.texture_name_fields(mesh, names))
    return drawn


def _source_textures(spec: str) -> dict:
    m = ai.Model.load(ai.resolve_targets(spec)[0].path)
    out = {}
    for s in m.sections:
        if s.type_code == ai.T_TEX:
            out.setdefault(xi_copy.texture_key(m.data[s.start + 0x11:s.start + 0x21]), m.data[s.start + 4:s.start + s.size])
    return out


def _fire_every_generator(spec: str) -> dict:
    m = ai.Model.load(ai.resolve_targets(spec)[0].path)
    gens = [ai._clean(s.name) for s in m.sections if s.type_code == ai.T_GEN]
    return {"name": "dup", "sources": {"vfx": {"spec": spec}}, "events": [_fire("vfx", g, i) for i, g in enumerate(gens)]}


@pytest.mark.parametrize("spec, shared", [
    ("ROM/11/21", [f"faida01 faida0{i}" for i in range(1, 6)]),               # five `faid`
    ("ROM/11/48", ["fire1   fire11", "fire1   fire13", "fire1   fire12", "fire1   fire16"]),   # two `fire`, two `fir1`
    ("ROM/11/122", ["enm1    enm10", "enm1    enm12", "enm1    enm13"]),       # four `enm1`, three drawn
])
def test_every_texture_a_generator_draws_comes_along_when_ids_repeat(root: Path, spec: str, shared: list):
    # Every generator fired: each texture a linked mesh or sprite sheet binds is in the
    # output with the source's pixels, the ones sharing an id included, under ids of their own.
    source = _source_textures(spec)
    (c,) = ac.compose(_fire_every_generator(spec))
    drawn = _textures_drawn(c.data, list(source)) & set(source)
    assert {n.encode() for n in shared} <= drawn
    out = {xi_copy.texture_key(s[0x11:0x21]): s[4:] for s in (c.data[x.start:x.start + x.size]
                                                             for x in parse_sections(c.data) if x.type_code == ai.T_TEX)}
    assert all(out.get(name) == source[name] for name in drawn), sorted(n for n in drawn if out.get(n) != source[n])
    ids = _tex_ids(c.data)
    assert len(ids) == len(set(ids)), ids


def test_rom_11_21_replaces_the_faid_its_entry_names(root: Path, encoder):
    # A name picks one of the five `faid`; no name is the first ("faida01 faida04").
    recipe = _fire_every_generator("ROM/11/21")
    for name, users in (("faida01 faida03", ["g001"]), (None, ["g010", "g005"])):
        entry = {"lane": "vfx", "ref": "faid", "png": _uri(_png(8, 8)), **({"name": name} if name else {})}
        (c,) = ac.compose(dict(recipe, textures=[entry]))
        hit = name or "faida01 faida04"
        pixels = {xi_copy.texture_key(k): s[0x55] for k, s in _by_name(c.data).items()}
        assert [k.decode() for k, p in pixels.items() if p == 0xEE] == [hit]
        (t,) = c.textures
        assert (t["ref"], t["name"].rstrip(), t["users"]) == ("faid", hit, users)

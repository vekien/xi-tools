"""Generator and curve edits in an ability recipe: the pure patcher
(xi.ability.xi_genedit), the ``generators`` / ``curves`` keys of ``validate_recipe``, and
``xi ability compose`` applying them to a lane's copy of a section.

Everything but the last two tests runs on a synthetic DAT packed by hand; those use the
``root`` fixture and skip without FFXI_DIR."""
import json
import struct
from pathlib import Path

import click
import pytest

from xi.ability import xi_compose as ac
from xi.ability import xi_genedit as ge
from xi.ability import xi_inspect as ai
from xi.common.xi_section import encode_section_meta
from xi.entity.anim.xi_export import parse_sections

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = json.loads((REPO / "schema" / "ability_recipe.json").read_text(encoding="utf-8"))["examples"]

_END = bytes.fromhex("00010000")          # an op stream's terminator, as retail writes it


# ── Synthetic bytes ──────────────────────────────────────────────────────────────

def _section(name: bytes, type_code: int, body: bytes) -> bytes:
    body = body + b"\0" * (-len(body) % 16)
    return name.ljust(4, b"\0") + struct.pack("<I", encode_section_meta(16 + len(body), type_code)) + b"\0" * 8 + body


def _op(code: int, words: int, payload: bytes = b"", alloc: int = 0) -> bytes:
    assert len(payload) <= words * 4 - 4
    return struct.pack("<I", code | (words << 8) | (alloc << 13)) + payload.ljust(words * 4 - 4, b"\0")


def _generator(name: bytes = b"g000", *, link: bytes = b"tex0", link_type: int = 0x0E,
               color: bytes = bytes.fromhex("c6803326"), curve: bytes = b"k000") -> bytes:
    """A 0x05 section laid out the way retail's are: a 0x90-byte head with the stream
    table at +0x80, then sec1 (0x0A cull), sec2 (0x01 setup naming ``link``, 0x0A rotation
    variance, 0x0F scale, 0x16 colour, 0x27 keyframed scale naming ``curve``), sec3 (0x0E)
    and an empty sec4. sec1 and sec2 both hold an op 0x0A, whose config bytes match."""
    setup = (struct.pack("<HHI", 0x0001, 0, 0) + link + struct.pack("<f3f", 0.0, 0.0, -0.1, 0.0)
             + struct.pack("<BBHH", 0x10, link_type, 30, 4))
    streams = [
        _op(0x0A, 4, struct.pack("<ffI", 15.0, -1.0, 0)) + _END,
        (_op(0x01, 12, setup) + _op(0x0A, 4, struct.pack("<3f", 3.0, 3.0, 3.0))
         + _op(0x0F, 4, struct.pack("<3f", 1.0, 1.0, 1.0)) + _op(0x16, 2, color)
         + _op(0x27, 4, struct.pack("<I", 0) + curve + struct.pack("<I", 1 << 5), alloc=1) + _END),
        _op(0x0E, 1) + _END,
        _END,
    ]
    head = bytearray(0x80)
    struct.pack_into("<H", head, 0x00, 0x0002)                       # attachFlags: TargetActor
    struct.pack_into("<HHBB", head, 0x64, 5, 39, 0, 0x10)            # variance, interval, count, autoRun
    offs, at = [], 0x90
    for s in streams:
        offs.append(at)
        at += len(s)
    return _section(name, ai.T_GEN, bytes(head[:0x70]) + struct.pack("<4I", *offs) + b"".join(streams))


def _curve(name: bytes, keys) -> bytes:
    return _section(name, ai.T_CURVE, b"".join(struct.pack("<2f", t, v) for t, v in keys))


_KEYS = [(0.0, 0.0), (0.25, 0.5), (1.0, 0.0)]


def _fire(ref: str, delay: int = 0, dur: int = 30) -> bytes:
    return ac._stamp(ac._TEMPLATES[0x02], delay=delay, ev={"dur": dur}, ref=ref)


def _dat(tmp_path: Path, name: str, sections) -> Path:
    p = tmp_path / name
    p.write_bytes(b"test" + struct.pack("<I", encode_section_meta(32, ai.T_DIR)) + b"\0" * 8
                  + ac._DIR_DATA + b"".join(sections) + ac._END_SECTION)
    return p


@pytest.fixture
def spell(tmp_path: Path, monkeypatch) -> Path:
    """A spell-like DAT: `main` fires g000 and links `hit1`, which fires g001. g000 draws
    `tex0`; `msh1` is a mesh no generator names yet, drawn with `tex2`; `obi ` is a
    texture whose name the DAT pads with a space."""
    monkeypatch.setattr("xi.xi_config.FFXI_DIR", str(tmp_path))
    link = ac._stamp(ac._TEMPLATES[0x03], delay=0, ev={}, ref="hit1")
    return _dat(tmp_path, "spell.DAT", [
        _generator(b"g000"), _generator(b"g001", color=bytes.fromhex("80808080")),
        ac.build_routine("main", [_fire("g000", 10), link], 40),
        ac.build_routine("hit1", [_fire("g001")], 30),
        _curve(b"k000", _KEYS),
        _section(b"msh1", ai.T_PMESH, b"\0" * 16 + b"tex2" + b"\0" * 12),
        _section(b"tex0", ai.T_TEX, b"T0" * 16), _section(b"tex1", ai.T_TEX, b"T1" * 16),
        _section(b"tex2", ai.T_TEX, b"T2" * 16), _section(b"obi ", ai.T_TEX, b"OB" * 16)])


def _sections(data: bytes) -> dict:
    return {(ai._clean(s.name), s.type_code): data[s.start:s.start + s.size] for s in parse_sections(data)}


def _recipe(spec, events=None, **more) -> dict:
    return {"name": "mix", "sources": {"vfx": {"spec": str(spec)}},
            "events": events or [{"from": "vfx", "op": 2, "ref": "g000", "start": 0, "dur": 30}], **more}


def _edits(*edits, lane="vfx", ref="g000") -> list:
    return [{"lane": lane, "ref": ref, "edits": list(edits)}]


_COLOR = {"sec": 2, "op": "0x16", "at": 4, "type": "u8", "value": [51, 128, 198, 38]}


# ── The patcher ──────────────────────────────────────────────────────────────────

def test_ops_are_found_by_walking_their_stream():
    # sec1 0x0A GeneratorCull and sec2 0x0A RotationVariance start with the same two
    # bytes (`0a 04`). A byte search for that tag finds whichever comes first, which on a
    # generator without the sec1 op (Cure III's pk00) is the wrong one. The walk keeps
    # them apart.
    gen = _generator()
    assert [hex(op) for op, _, _ in ge.walk_stream(gen, 1)] == ["0xa"]
    assert [hex(op) for op, _, _ in ge.walk_stream(gen, 2)] == ["0x1", "0xa", "0xf", "0x16", "0x27"]
    assert ge.walk_stream(gen, 4) == []
    cull = ge.resolve(gen, {"sec": 1, "op": 0x0A, "at": 4, "type": "f32", "value": 0})
    spin = ge.resolve(gen, {"sec": 2, "op": "0x0A", "at": 4, "type": "f32", "value": 0})
    assert cull == (0x94, 0x98) and spin == (0xA4 + 48 + 4, 0xA4 + 48 + 8)
    out = ge.apply_edits(gen, [{"sec": 2, "op": "0A", "at": 4, "type": "f32", "value": [1, 2, 3]}])
    assert struct.unpack_from("<f", out, cull[0])[0] == 15.0                    # the cull distance stands
    assert struct.unpack_from("<3f", out, spin[0]) == (1.0, 2.0, 3.0)


def test_each_type_writes_little_endian_and_nothing_else():
    gen = _generator()
    color = ge.resolve(gen, _COLOR)[0]
    out = ge.apply_edits(gen, [
        _COLOR,
        {"sec": 0, "at": 0x76, "type": "u16", "value": 0x0102},
        {"sec": 0, "at": 0x79, "type": "u8", "value": 0, "mask": 0x10},            # clear autoRun only
        {"sec": 0, "at": 0x10, "type": "u16", "value": 0x0009, "mask": 0x000F},    # attach type, joints kept
        {"sec": 2, "op": 1, "at": 0x22, "type": "i16", "value": [-2, 7]},
        {"sec": 2, "op": 1, "at": 0x08, "type": "u32", "value": 0xDEADBEEF},
        {"sec": 2, "op": 1, "at": 0x28, "type": "i32", "value": -1},
        {"sec": 2, "op": 0x0F, "at": 8, "type": "f32", "value": 2.5},
        {"sec": 2, "op": 1, "at": 0x0C, "type": "datid", "value": "tex1"}])
    assert len(out) == len(gen) and out[:16] == gen[:16]                          # same size, same meta
    assert out[color:color + 4] == bytes([51, 128, 198, 38])
    assert out[0x76:0x78] == b"\x02\x01"
    assert out[0x79] == 0x00 and gen[0x79] == 0x10
    assert out[0x10:0x12] == b"\x09\x00"
    setup = ge.resolve(gen, {"sec": 2, "op": 1, "at": 4, "type": "u8", "value": 0})[0] - 4
    assert out[setup + 0x22:setup + 0x26] == struct.pack("<hh", -2, 7)
    assert out[setup + 0x08:setup + 0x0C] == bytes.fromhex("efbeadde")
    assert out[setup + 0x28:setup + 0x2C] == b"\xff" * 4
    assert out[setup + 0x0C:setup + 0x10] == b"tex1"
    scale = ge.resolve(gen, {"sec": 2, "op": 0x0F, "at": 4, "type": "f32", "value": 0})[0]
    assert struct.unpack_from("<3f", out, scale) == (1.0, 2.5, 1.0)
    touched = set()
    for a, n in ((color, 4), (0x76, 2), (0x79, 1), (0x10, 2), (setup + 0x22, 4), (setup + 0x08, 4),
                 (setup + 0x28, 4), (scale + 4, 4), (setup + 0x0C, 4)):
        touched.update(range(a, a + n))
    assert all(out[i] == gen[i] for i in range(len(gen)) if i not in touched)
    assert ge.apply_edits(out, [_COLOR]) == out                                    # absolute: no drift
    # A mask keeps the bits outside it, on a signed type too.
    masked = ge.apply_edits(gen, [{"sec": 0, "at": 0x76, "type": "i16", "value": -1, "mask": 0xFF00}])
    assert masked[0x76:0x78] == bytes([39, 0xFF])


def test_a_datid_is_padded_the_way_the_dat_pads_it():
    gen = _generator(link=b"ab  ")
    link = {"sec": 2, "op": 1, "at": 0x0C, "type": "datid", "value": "obi"}
    at = ge.resolve(gen, link)[0]
    assert ge.apply_edits(gen, [link])[at:at + 4] == b"obi "                       # like the bytes it replaces
    assert ge.apply_edits(_generator(), [link])[at:at + 4] == b"obi\0"
    # Given the lane's section names, the id is the name as the DAT spells it, and one
    # the DAT does not have is refused.
    assert ge.apply_edits(_generator(), [link], ids={"obi": b"obi "})[at:at + 4] == b"obi "
    with pytest.raises(ge.EditError, match=r"edits\[0\]: no texture, mesh, curve or sound section named 'obi'"):
        ge.apply_edits(gen, [link], ids={"tex0": b"tex0"})


@pytest.mark.parametrize("edit, says", [
    ({"sec": 0, "at": 0x0C, "type": "u32", "value": 0}, "leaves the generator header"),          # the section header
    ({"sec": 0, "at": 0x7E, "type": "u32", "value": 0}, "leaves the generator header"),          # into the stream table
    ({"sec": 0, "at": 0x80, "type": "u32", "value": 0}, "leaves the generator header"),
    ({"sec": 2, "op": 0x16, "at": 0, "type": "u8", "value": 0}, "config dword is not writable"),
    ({"sec": 2, "op": 0x16, "at": 3, "type": "u16", "value": 0}, "config dword is not writable"),
    ({"sec": 2, "op": 0x16, "at": 6, "type": "u32", "value": 0}, r"runs past sec 2 op 0x16 \(8 bytes\)"),
    ({"sec": 2, "op": 0x16, "at": 4, "type": "u8", "value": [1, 2, 3, 4, 5]}, "runs past sec 2 op 0x16"),
    ({"sec": 3, "op": 0x16, "at": 4, "type": "u8", "value": 0}, "sec 3 has no op 0x16"),
    ({"sec": 4, "op": 0x01, "at": 4, "type": "u8", "value": 0}, "sec 4 has no op 0x01"),
    ({"sec": 2, "op": 0x16, "nth": 1, "at": 4, "type": "u8", "value": 0}, "sec 2 has 1 op 0x16, so no nth 1"),
])
def test_an_edit_that_does_not_fit_names_itself(edit, says):
    gen = _generator()
    with pytest.raises(ge.EditError, match=r"^generators\[2\]\.edits\[1\]: .*" + says):
        ge.apply_edits(gen, [_COLOR, edit], "generators[2].edits")
    with pytest.raises(ge.EditError, match="too short to be a generator"):
        ge.apply_edits(gen[:0x60], [_COLOR])


def test_curve_keys_are_replaced_in_place():
    sec = _curve(b"k000", _KEYS)
    assert len(sec) == 48 and ge.curve_keys(sec) == _KEYS                # 3 keys, then 8 bytes of padding
    out = ge.apply_curve(sec, [[0, 1], [0.5, 2.5], [1, -1]])
    assert ge.curve_keys(out) == [(0.0, 1.0), (0.5, 2.5), (1.0, -1.0)]
    assert len(out) == len(sec) and out[:16] == sec[:16] and out[40:] == sec[40:]
    # The last key's time is the source's: it ends the curve, so what the recipe says
    # there is not written.
    assert ge.curve_keys(ge.apply_curve(sec, [[0, 0], [0.5, 1], [0.9, 3]]))[-1] == (1.0, 3.0)
    with pytest.raises(ge.EditError, match=r"^curves\[1\]: 2 keys for a curve of 3"):
        ge.apply_curve(sec, [[0, 0], [1, 1]], "curves[1]")
    with pytest.raises(ge.EditError, match=r"^curves\[1\]: keys\[1\] has time 1"):
        ge.apply_curve(sec, [[0, 0], [1, 1], [1, 0]], "curves[1]")
    # A curve with no key at time 1 runs to the end of its section.
    open_ended = _curve(b"k001", [(0.0, 0.0), (0.5, 1.0)])
    assert ge.curve_keys(open_ended) == [(0.0, 0.0), (0.5, 1.0)]
    assert ge.curve_keys(ge.apply_curve(open_ended, [[0.1, 4], [0.7, 5]])) == [(pytest.approx(0.1), 4.0), (0.5, 5.0)]


# ── validate_recipe ──────────────────────────────────────────────────────────────

def test_schema_examples_validate():
    assert len(EXAMPLES) >= 2 and "generators" in EXAMPLES[1] and "curves" in EXAMPLES[1]
    for ex in EXAMPLES:
        assert ac.validate_recipe(ex) == []
    assert ac.validate_recipe(dict(EXAMPLES[0], generators=[], curves=[])) == []


def _errs(**keys):
    return ac.validate_recipe(dict(EXAMPLES[1], **keys))


def test_validate_recipe_names_the_generator_entry():
    good = EXAMPLES[1]["generators"][0]
    assert _errs(generators={}) == ["generators must be a list"]
    assert _errs(generators=[5]) == ["generators[0]: must be an object"]
    assert _errs(generators=[dict(good, **{"as": "g00a"})]) == ["generators[0]: unknown key 'as'"]
    assert _errs(generators=[dict(good, lane="nope")]) == ["generators[0]: lane 'nope' is not in sources"]
    assert _errs(generators=[{"ref": "g000", "edits": good["edits"]}]) == ["generators[0]: 'lane' is required"]
    for ref in ("", "g0000", "gé0", "    ", 7, None):
        assert _errs(generators=[dict(good, ref=ref)]) == ["generators[0]: ref must be a 1–4 character section id"], ref
    assert _errs(generators=[good, dict(good, ref="g004"), good]) == [
        "generators[2]: lane 'vfx' ref 'g000' is already in generators[0]"]
    # A name is compared without its padding, the way the DAT's own are.
    assert _errs(generators=[dict(good, ref="pk0"), dict(good, ref="pk0 ")]) == [
        "generators[1]: lane 'vfx' ref 'pk0 ' is already in generators[0]"]
    for edits in ([], None, {}):
        assert _errs(generators=[dict(good, edits=edits)]) == ["generators[0]: edits must be a non-empty list"]


@pytest.mark.parametrize("edit, says", [
    (5, "must be an object"),
    (dict(_COLOR, offset=308), "unknown key 'offset'"),
    (dict(_COLOR, sec=5), "sec must be 0 (the header) or 1–4 (an op stream)"),
    (dict(_COLOR, sec="2"), "sec must be 0"),
    ({"sec": 2, "at": 4, "type": "u8", "value": 1}, "op must be an opcode 0–255 (or hex string)"),
    (dict(_COLOR, op=256), "op must be an opcode"),
    (dict(_COLOR, op="0x1G"), "op must be an opcode"),
    ({"sec": 0, "op": 1, "at": 0x76, "type": "u16", "value": 1}, "op and nth do not apply to sec 0"),
    ({"sec": 0, "nth": 1, "at": 0x76, "type": "u16", "value": 1}, "op and nth do not apply to sec 0"),
    (dict(_COLOR, nth=-1), "nth must be a non-negative integer"),
    (dict(_COLOR, nth=1.0), "nth must be a non-negative integer"),
    (dict(_COLOR, at=-4), "at must be a non-negative byte offset"),
    (dict(_COLOR, at=True), "at must be a non-negative byte offset"),
    (dict(_COLOR, at=2), "at must be 4 or more: the op's config dword is not writable"),
    (dict(_COLOR, at=121), "at + size runs past the longest op (124 bytes)"),
    ({"sec": 0, "at": 8, "type": "u32", "value": 1}, "sec 0 writes stay inside the generator header"),
    ({"sec": 0, "at": 0x7E, "type": "u32", "value": 1}, "sec 0 writes stay inside the generator header"),
    (dict(_COLOR, type="hex"), "type must be one of u8, u16, u32, i16, i32, f32, datid"),
    ({"sec": 2, "op": 1, "at": 4, "type": "u8"}, "value is required"),
    (dict(_COLOR, value=[]), "value is required"),
    (dict(_COLOR, value=256), "value 256 is not a u8 (0..255)"),
    (dict(_COLOR, value=[1, -1]), "value -1 is not a u8"),
    (dict(_COLOR, value=1.0), "value 1.0 is not a u8"),
    (dict(_COLOR, value=True), "value True is not a u8"),
    (dict(_COLOR, type="i16", value=40000), "value 40000 is not a i16 (-32768..32767)"),
    (dict(_COLOR, type="f32", value="1"), "value '1' is not a finite f32"),
    (dict(_COLOR, type="f32", value=float("nan")), "is not a finite f32"),
    (dict(_COLOR, type="f32", value=1e39), "is not a finite f32"),
    (dict(_COLOR, type="f32", value=[0.75, 10 ** 400]), "is not a finite f32"),     # no float for it at all
    (dict(_COLOR, type="datid", value="toolong"), "value 'toolong' is not a 1–4 character section id"),
    (dict(_COLOR, type="datid", value=5), "value 5 is not a 1–4 character section id"),
    (dict(_COLOR, mask=0x100), "mask must fit a u8"),
    (dict(_COLOR, mask=-1), "mask must fit a u8"),
    (dict(_COLOR, type="f32", value=1, mask=1), "mask applies to integer types only"),
    (dict(_COLOR, type="datid", value="tex1", mask=1), "mask applies to integer types only"),
])
def test_validate_recipe_names_the_edit(edit, says):
    good = EXAMPLES[1]["generators"][0]
    errs = _errs(generators=[good, dict(good, ref="g004", edits=[_COLOR, edit])])
    assert len(errs) == 1 and errs[0].startswith("generators[1].edits[1]: ") and says in errs[0], errs


def test_validate_recipe_names_the_curve_entry():
    good = EXAMPLES[1]["curves"][0]
    assert _errs(curves=[good, dict(good, ref="k002")]) == []
    assert _errs(curves="k001") == ["curves must be a list"]
    assert _errs(curves=[dict(good, edits=[])]) == ["curves[0]: unknown key 'edits'"]
    assert _errs(curves=[dict(good, lane="nope")]) == ["curves[0]: lane 'nope' is not in sources"]
    assert _errs(curves=[dict(good, ref="toolong")]) == ["curves[0]: ref must be a 1–4 character section id"]
    assert _errs(curves=[good, good]) == ["curves[1]: lane 'vfx' ref 'k001' is already in curves[0]"]
    for keys in ([], None, "x"):
        assert _errs(curves=[dict(good, keys=keys)]) == ["curves[0]: keys must be a non-empty list of [time, value] pairs"]
    for key in ([0], [0, 1, 2], [0, "1"], [None, 1], [0, float("inf")], [0, 10 ** 400], [10 ** 400, 0], 5):
        assert _errs(curves=[dict(good, keys=[[0, 0], key])]) == [
            "curves[0]: keys[1] must be [time, value], two finite numbers"], key
    assert _errs(curves=[dict(good, keys=[[-0.1, 0], [1.5, 0]])]) == [
        "curves[0]: keys[0]: time must be 0..1 (a fraction of the particle's life)",
        "curves[0]: keys[1]: time must be 0..1 (a fraction of the particle's life)"]
    # An earlier key at time 1 as an f32 ends the curve, which compose refuses whatever
    # the DAT. 0.99999997 rounds below 1 and is fine; the last key's time is the source's.
    for t in (1, 0.99999998):
        assert _errs(curves=[dict(good, keys=[[0, 0], [t, 1], [1, 0]])]) == [
            "curves[0]: keys[1] has time 1, which ends the curve; only the last key may"], t
    assert ge.check_keys([[0, 0], [0.99999997, 1], [1, 0]]) == []
    assert ge.check_keys([[0, 0], [0.5, 1], [0.5, 0]]) == []


def test_load_recipe_reads_an_integral_float_as_an_integer(tmp_path: Path):
    # JSON Schema's integer is any number with no fraction, so 4.0 conforms. A dict has
    # to hold the int; load_recipe reads it so. An f32 keeps its float, -0.0 its sign.
    recipe = json.loads(json.dumps(EXAMPLES[1]))
    edits = recipe["generators"][0]["edits"]
    edits[0]["at"], edits[1]["value"], recipe["events"][0]["start"] = 4.0, [-0.0, 2.0], 1.0
    assert ac.validate_recipe(recipe) != []
    path = tmp_path / "blue_fire.json"
    path.write_text(json.dumps(recipe), encoding="utf-8")
    assert '"at": 4.0' in path.read_text(encoding="utf-8")
    r = ac.load_recipe(path)
    got = r["generators"][0]["edits"]
    assert type(got[0]["at"]) is int and type(r["events"][0]["start"]) is int and got[0]["at"] == 4
    assert str(got[1]["value"][0]) == "-0.0" and got[1]["value"][1] == 2


# ── Compose ──────────────────────────────────────────────────────────────────────

def test_compose_writes_the_edits_into_its_copy(spell: Path):
    source = spell.read_bytes()
    plain = ac.compose(_recipe(spell))[0]
    assert _sections(plain.data)[("g000", ai.T_GEN)] == _sections(source)[("g000", ai.T_GEN)]
    # A recipe without the keys, or with empty lists, composes to the same bytes.
    assert ac.compose(_recipe(spell, generators=[], curves=[]))[0].data == plain.data

    recipe = _recipe(spell, generators=_edits(_COLOR, {"sec": 0, "at": 0x76, "type": "u16", "value": 1}),
                     curves=[{"lane": "vfx", "ref": "k000", "keys": [[0, 0], [0.5, 0.8], [1, 0.25]]}])
    assert ac.validate_recipe(recipe) == []
    (c,) = ac.compose(recipe)
    got, was = _sections(c.data), _sections(plain.data)
    assert set(got) == set(was) and c.sections == plain.sections and c.renames == {}
    gen = got[("g000", ai.T_GEN)]
    assert gen == ge.apply_edits(was[("g000", ai.T_GEN)], recipe["generators"][0]["edits"])
    assert gen[ge.resolve(gen, _COLOR)[0]:][:4] == bytes([51, 128, 198, 38]) and gen[0x76] == 1
    assert ge.curve_keys(got[("k000", ai.T_CURVE)]) == [(0.0, 0.0), (0.5, pytest.approx(0.8)), (1.0, 0.25)]
    assert {k for k in got if got[k] != was[k]} == {("g000", ai.T_GEN), ("k000", ai.T_CURVE)}
    assert c.timeline == plain.timeline and c.total == plain.total
    assert spell.read_bytes() == source                                   # the source DAT is only read


def test_an_unused_edit_is_fine_and_a_missing_section_is_not(spell: Path):
    plain = ac.compose(_recipe(spell))[0]
    rel = ai.resolve_targets(str(spell))[0].rel
    # g001 is in the DAT but this recipe never fires it: the edit is simply not used.
    unused = _recipe(spell, generators=_edits(_COLOR, ref="g001"))
    assert ac.compose(unused)[0].data == plain.data
    with pytest.raises(click.ClickException) as ei:
        ac.compose(_recipe(spell, generators=_edits(_COLOR) + _edits(_COLOR, ref="g009")))
    assert ei.value.message == f"generators[1]: lane 'vfx' ({rel}) has no generator 'g009'"
    with pytest.raises(click.ClickException) as ei:
        ac.compose(_recipe(spell, curves=[{"lane": "vfx", "ref": "k009", "keys": [[0, 0], [1, 1]]}]))
    assert ei.value.message == f"curves[0]: lane 'vfx' ({rel}) has no curve 'k009'"
    # What does not fit the section is reported against the recipe entry, with the section.
    with pytest.raises(click.ClickException) as ei:
        ac.compose(_recipe(spell, generators=_edits(_COLOR, dict(_COLOR, nth=3))))
    assert ei.value.message == ("generators[0].edits[1]: sec 2 has 1 op 0x16, so no nth 3 "
                                f"(lane 'vfx': g000 in {rel})")
    with pytest.raises(click.ClickException) as ei:
        ac.compose(_recipe(spell, curves=[{"lane": "vfx", "ref": "k000", "keys": [[0, 0], [1, 1]]}]))
    assert ei.value.message.startswith("curves[0]: 2 keys for a curve of 3")
    with pytest.raises(click.ClickException, match=r"generators\[0\] refers to unknown lane 'nope'"):
        ac.compose(_recipe(spell, generators=_edits(_COLOR, lane="nope")))


def test_two_lanes_on_one_spec_keep_their_own_edits(spell: Path):
    # The way to play one generator twice with different edits: a second lane on the same
    # spec. Its copy is renamed, the report says so, and what both lanes leave alone (the
    # texture, the curve) is carried once.
    red = dict(_COLOR, value=[255, 0, 0, 128])
    recipe = {"name": "mix", "sources": {"a": {"spec": str(spell)}, "b": {"spec": str(spell)}},
              "events": [{"from": "a", "op": 2, "ref": "g000", "start": 0, "dur": 30},
                         {"from": "b", "op": 2, "ref": "g000", "start": 5, "dur": 30}],
              "generators": _edits(_COLOR, lane="a") + _edits(red, lane="b")}
    (c,) = ac.compose(recipe)
    assert c.renames == {"b:g000": "g002"}                                # g001 is taken in that DAT
    assert [t["ref"] for t in c.timeline] == ["g000", "g002"]
    got = _sections(c.data)
    at = ge.resolve(got[("g000", ai.T_GEN)], _COLOR)[0]
    assert got[("g000", ai.T_GEN)][at:at + 4] == bytes([51, 128, 198, 38])
    assert got[("g002", ai.T_GEN)][at:at + 4] == bytes([255, 0, 0, 128])
    assert got[("g002", ai.T_GEN)][:4] == b"g002"
    assert sorted(c.sections) == sorted(set(c.sections))
    assert {s for s in c.sections if not s.endswith("(0x05)")} == {"tex0(0x20)", "k000(0x19)", "main(0x07)"}
    # Only the edited lane differs: the other still shares nothing with it, and an
    # unedited second lane shares everything.
    shared = ac.compose(dict(recipe, generators=[]))[0]
    assert shared.renames == {} and shared.sections.count("g000(0x05)") == 1


def test_a_curve_edited_on_one_lane_is_not_the_other_lanes(spell: Path):
    # Curves are shared by name. Lane b edits k000; lane a does not, and its generator
    # is byte for byte lane b's. Each lane still has to play its own curve, so the later
    # lane gets its own generator, patched to name its own copy of the curve.
    for first, second in (("a", "b"), ("b", "a")):
        recipe = {"name": "mix", "sources": {"a": {"spec": str(spell)}, "b": {"spec": str(spell)}},
                  "events": [{"from": first, "op": 2, "ref": "g000", "start": 0, "dur": 30},
                             {"from": second, "op": 2, "ref": "g000", "start": 5, "dur": 30}],
                  "curves": [{"lane": "b", "ref": "k000", "keys": [[0, 9], [0.5, 9], [1, 9]]}]}
        (c,) = ac.compose(recipe)
        assert c.renames == {f"{second}:g000": "g002", f"{second}:k000": "k001"}
        got = _sections(c.data)
        names = {"a": ("g000", "k000"), "b": ("g000", "k000"), second: ("g002", "k001")}
        for lane, (gen, curve) in names.items():
            at = ge.resolve(got[(gen, ai.T_GEN)], {"sec": 2, "op": 0x27, "at": 8, "type": "datid", "value": "x"})[0]
            assert got[(gen, ai.T_GEN)][at:at + 4] == curve.encode()
            values = [v for _, v in ge.curve_keys(got[(curve, ai.T_CURVE)])]
            assert values == ([9.0, 9.0, 9.0] if lane == "b" else [0.0, 0.5, 0.0]), (first, lane)
        assert sorted(c.sections) == sorted(set(c.sections))              # nothing carried twice
        assert c.sections.count("tex0(0x20)") == 1


def test_an_edited_lane_does_not_lend_the_generator_it_patches(spell: Path):
    # g000 and g001 both name k000. a fires g001 (carrying k000), b fires g000 with its
    # k000 edited, then a fires g000. b's g000 matches a's byte for byte when a gets to
    # it, but is patched to b's renamed curve at the end, so a cannot share it.
    recipe = {"name": "mix", "sources": {"a": {"spec": str(spell)}, "b": {"spec": str(spell)}},
              "events": [{"from": "a", "op": 2, "ref": "g001", "start": 0, "dur": 30},
                         {"from": "b", "op": 2, "ref": "g000", "start": 5, "dur": 30},
                         {"from": "a", "op": 2, "ref": "g000", "start": 10, "dur": 30}],
              "curves": [{"lane": "b", "ref": "k000", "keys": [[0, 9], [0.5, 9], [1, 9]]}]}
    (c,) = ac.compose(recipe)
    got = _sections(c.data)
    curve_id = {"sec": 2, "op": 0x27, "at": 8, "type": "datid", "value": "x"}
    for t, values in ((c.timeline[1], [9.0, 9.0, 9.0]), (c.timeline[2], [0.0, 0.5, 0.0])):
        gen = got[(t["ref"], ai.T_GEN)]
        at = ge.resolve(gen, curve_id)[0]
        curve = gen[at:at + 4].decode()
        assert [v for _, v in ge.curve_keys(got[(curve, ai.T_CURVE)])] == values, (c.renames, c.timeline)
    assert c.timeline[1]["ref"] != c.timeline[2]["ref"]


def test_a_second_curve_of_the_same_name_is_left_alone(tmp_path: Path, monkeypatch):
    # Retail has DATs with two curves of one name and different key counts (ROM/10/19's
    # p1ca, 10 and 9 keys). The first is the one carried and the one the viewer edits;
    # the second takes no edit, and no part in whether a second lane shares.
    monkeypatch.setattr("xi.xi_config.FFXI_DIR", str(tmp_path))
    spec = str(_dat(tmp_path, "twin.DAT", [
        _generator(b"g000"), ac.build_routine("main", [_fire("g000")], 30),
        _curve(b"k000", _KEYS), _curve(b"k000", [(0.0, 5.0), (1.0, 5.0)]),
        _section(b"tex0", ai.T_TEX, b"T0" * 16)]))
    keys = [[0, 1], [0.5, 2], [1, 3]]
    (c,) = ac.compose(_recipe(spec, curves=[{"lane": "vfx", "ref": "k000", "keys": keys}]))
    assert c.sections.count("k000(0x19)") == 1
    assert ge.curve_keys(_sections(c.data)[("k000", ai.T_CURVE)]) == [(0.0, 1.0), (0.5, 2.0), (1.0, 3.0)]
    two = {"name": "mix", "sources": {"a": {"spec": spec}, "b": {"spec": spec}},
           "events": [{"from": "a", "op": 2, "ref": "g000", "start": 0}, {"from": "b", "op": 2, "ref": "g000", "start": 5}]}
    (c,) = ac.compose(two)
    assert c.renames == {} and [t["ref"] for t in c.timeline] == ["g000", "g000"]
    assert ge.curve_keys(_sections(c.data)[("k000", ai.T_CURVE)]) == _KEYS


def test_an_audio_generator_keeps_its_sound_pointer_when_renamed(tmp_path: Path, monkeypatch):
    # Retail names an audio generator after its 0x3D sound pointer. When a second lane's
    # copy of the generator is renamed, its references are patched to the new name, so
    # its sound pointer has to go by that name too (identical bytes or not).
    monkeypatch.setattr("xi.xi_config.FFXI_DIR", str(tmp_path))
    spec = str(_dat(tmp_path, "snd.DAT", [
        _generator(b"8210", link=b"8210", link_type=0x3D),
        ac.build_routine("main", [_fire("8210")], 30),
        _section(b"8210", ai.T_SOUND, b"SeSep  \0" + struct.pack("<I", 18210)), _curve(b"k000", _KEYS)]))
    recipe = {"name": "mix", "sources": {"a": {"spec": spec}, "b": {"spec": spec}},
              "events": [{"from": "a", "op": 2, "ref": "8210", "start": 0}, {"from": "b", "op": 2, "ref": "8210", "start": 5}],
              "generators": _edits({"sec": 0, "at": 0x78, "type": "u8", "value": 3}, lane="b", ref="8210")}
    (c,) = ac.compose(recipe)
    assert c.renames == {"b:8210": "8211"}
    got = _sections(c.data)
    link = {"sec": 2, "op": 1, "at": 0x0C, "type": "datid", "value": "x"}
    for name in ("8210", "8211"):
        gen = got[(name, ai.T_GEN)]
        at = ge.resolve(gen, link)[0]
        assert gen[at:at + 4] == name.encode() and (name, ai.T_SOUND) in got
    assert got[("8211", ai.T_SOUND)][4:] == got[("8210", ai.T_SOUND)][4:]
    assert got[("8211", ai.T_GEN)][0x78] == 3 and got[("8210", ai.T_GEN)][0x78] == 0


def test_a_renamed_generator_gets_the_edit_by_its_source_name(spell: Path, tmp_path: Path):
    # Lane b's g000 collides with lane a's (another DAT, other bytes) and is renamed; the
    # recipe still addresses it as lane b's g000.
    other = _dat(tmp_path, "other.DAT", [_generator(b"g000", color=bytes.fromhex("10203040"), link=b"tex9"),
                                         ac.build_routine("main", [_fire("g000")], 30),
                                         _section(b"tex9", ai.T_TEX, b"T9" * 16), _curve(b"k000", _KEYS)])
    recipe = {"name": "mix", "sources": {"a": {"spec": str(spell)}, "b": {"spec": str(other)}},
              "events": [{"from": "a", "op": 2, "ref": "g000", "start": 0}, {"from": "b", "op": 2, "ref": "g000", "start": 5}],
              "generators": _edits({"sec": 2, "op": 0x0F, "at": 4, "type": "f32", "value": [2, 3, 4]}, lane="b")}
    (c,) = ac.compose(recipe)
    assert c.renames == {"b:g000": "g001"}
    got = _sections(c.data)
    scale = ge.resolve(got[("g001", ai.T_GEN)], recipe["generators"][0]["edits"][0])[0]
    assert struct.unpack_from("<3f", got[("g001", ai.T_GEN)], scale) == (2.0, 3.0, 4.0)
    assert struct.unpack_from("<3f", got[("g000", ai.T_GEN)], scale) == (1.0, 1.0, 1.0)
    assert ("tex9", ai.T_TEX) in got and ("tex0", ai.T_TEX) in got


def test_a_generator_inside_a_linked_routine_is_editable(spell: Path):
    # g001 is fired only by `hit1`, which the recipe reaches through a link.
    link = [{"from": "vfx", "op": 3, "ref": "hit1", "start": 0}]
    recipe = _recipe(spell, link, generators=_edits(_COLOR, ref="g001"))
    (c,) = ac.compose(recipe)
    got = _sections(c.data)
    at = ge.resolve(got[("g001", ai.T_GEN)], _COLOR)[0]
    assert got[("g001", ai.T_GEN)][at:at + 4] == bytes([51, 128, 198, 38])
    assert ("hit1", ai.T_ROUTINE) in got and c.renames == {}
    # With an unedited lane linking the same routine first, the edited lane carries its
    # own copy of the routine, firing its own copy of the generator.
    two = {"name": "mix", "sources": {"a": {"spec": str(spell)}, "vfx": {"spec": str(spell)}},
           "events": [{"from": "a", "op": 3, "ref": "hit1", "start": 0}, {"from": "vfx", "op": 3, "ref": "hit1", "start": 9}],
           "generators": recipe["generators"]}
    (c,) = ac.compose(two)
    assert c.renames == {"vfx:g001": "g002", "vfx:hit1": "hit0"}
    assert [t["ref"] for t in c.timeline] == ["hit1", "hit0"]
    out = spell.parent / "two.DAT"
    out.write_bytes(c.data)
    m = ai.Model.load(out)
    assert [e["ref"] for e in ai.flatten(m, "main") if e["op"] == 2] == ["g001", "g002"]
    got = _sections(c.data)
    assert got[("g002", ai.T_GEN)][at:at + 4] == bytes([51, 128, 198, 38])
    assert got[("g001", ai.T_GEN)][at:at + 4] == bytes.fromhex("80808080")


def test_a_repointed_id_brings_its_resource_along(spell: Path):
    # g000 draws tex0. Pointed at the mesh msh1 instead, the composed DAT carries msh1
    # and the texture msh1 names, and no longer tex0.
    mesh = [{"sec": 2, "op": 1, "at": 0x0C, "type": "datid", "value": "msh1"},
            {"sec": 2, "op": 1, "at": 0x21, "type": "u8", "value": 0x0B}]
    (c,) = ac.compose(_recipe(spell, generators=_edits(*mesh)))
    assert {s for s in c.sections if s[-6:] in ("(0x1F)", "(0x20)")} == {"msh1(0x1F)", "tex2(0x20)"}
    # An id is written as the DAT spells the name, so a space-padded one still resolves.
    (c,) = ac.compose(_recipe(spell, generators=_edits(dict(mesh[0], value="obi"))))
    gen = _sections(c.data)[("g000", ai.T_GEN)]
    assert gen[ge.resolve(gen, mesh[0])[0]:][:4] == b"obi " and "obi(0x20)" in c.sections
    # The id has to name something compose can carry from this lane's DAT.
    rel = ai.resolve_targets(str(spell))[0].rel
    for missing in ("tex7", "g001", "hit1"):
        with pytest.raises(click.ClickException) as ei:
            ac.compose(_recipe(spell, generators=_edits(dict(mesh[0], value=missing))))
        assert ei.value.message == (f"generators[0].edits[0]: no texture, mesh, curve or sound section named "
                                    f"{missing!r} in the source DAT (lane 'vfx': g000 in {rel})")


# ── With the game ────────────────────────────────────────────────────────────────

def test_schema_example_composes_against_retail(root: Path, tmp_path: Path):
    # The schema's second example edits Fire's flame (spell:144 g000) and the curve its
    # height follows. Composed, the DAT reads back with the new values through the tools'
    # own readers, and nothing but those two sections differs from the unedited compose.
    from xi.fx.xi_dump import dump_effects
    recipe = EXAMPLES[1]
    plain = ac.compose({k: v for k, v in recipe.items() if k not in ("generators", "curves")})[0]
    (c,) = ac.compose(recipe)
    assert c.timeline == plain.timeline and c.total == plain.total and c.sections == plain.sections
    got, was = _sections(c.data), _sections(plain.data)
    assert {k for k in got if got[k] != was[k]} == {("g000", ai.T_GEN), ("k001", ai.T_CURVE)}
    assert was[("g000", ai.T_GEN)][0x79] & 0x10 == 0 and got[("g000", ai.T_GEN)][0x76] == 1
    assert [round(v, 4) for _, v in ge.curve_keys(got[("k001", ai.T_CURVE)])] == [0.0, 0.8, 0.0]
    out = tmp_path / "blue_fire.DAT"
    out.write_bytes(c.data)
    fx = {e["name"]: e["params"] for e in dump_effects(out)["effects"]}
    assert fx["g000"]["color_rgb"] == "3380C6" and fx["g000"]["scale"] == [0.0, 0.75, 1.5]
    assert fx["g000"]["spawn_interval"] == 1 and fx["g004"]["color_rgb"] == "808080"
    info = ai.inspect_target(ai.Target("blue_fire", out, "blue_fire.DAT"))
    assert [(e["start"], e["ref"]) for e in info["timeline"] if e["op"] == 2] == [(1, "g000"), (50, "g004")]


def test_a_race_bound_lane_is_edited_on_every_race(root: Path):
    # ws:1 composes once per race from that race's DAT; the edit is addressed by stream
    # and op, so it lands in each.
    recipe = ac.recipe_from("ws:1:HumeMale")
    recipe["sources"] = {"motion": {"spec": "ws:1"}}
    target = ai.resolve_targets("ws:1:HumeMale")[0]
    gen = next(ai._clean(s.name) for s in ai.Model.load(target.path).sections if s.type_code == ai.T_GEN)
    edit = {"sec": 0, "at": 0x76, "type": "u16", "value": 77}
    recipe["generators"] = _edits(edit, lane="motion", ref=gen)
    out = ac.compose(recipe)
    assert [c.race for c in out] == list(ac.RACE_NAMES)
    for c in out:
        assert _sections(c.data)[(gen, ai.T_GEN)][0x76:0x78] == b"\x4d\x00", c.race

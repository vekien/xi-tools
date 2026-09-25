"""The ``zone_dialog`` action of ``xi dats`` (xi.dialog.xi_zone_dialog): a zone's dialog lines
edited and added in its English and Japanese tables, on a synthetic game folder whose FTABLE
registers the zone's two dialog DATs — build, rebuild, undo, --pivot and prepare."""
import json
import struct
from pathlib import Path

import pytest
from click.testing import CliRunner

from _minischema import errors, load
from xi.dialog import xi_dialog as XD
from xi.dialog import xi_zone_dialog as ZD

ZONE = 245
EN, JP = "ROM/25/54.DAT", "ROM/23/54.DAT"          # file ids 6665 / 6365
LINES = ["Line zero.\\v", "Hello {player}, welcome to Jeuno.\\v", "Do you want to move?\nRight away.\nNot now."]


def container(lines, tail: bytes = b"") -> bytes:
    blobs = [XD.encode_event_string(s) + b"\x00" for s in lines]
    blobs[1] += tail                                   # a variant after the displayed text
    return XD.build_container(blobs, True)


def tables(n: int, entries: dict) -> tuple[bytes, bytes]:
    ft, vt = bytearray(2 * n), bytearray(n)
    for fid, (sub, idx) in entries.items():
        struct.pack_into("<H", ft, fid * 2, (sub << 7) | idx)
        vt[fid] = 1
    return bytes(ft), bytes(vt)


def write(root: Path, rel: str, data: bytes) -> None:
    p = root / Path(*rel.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


@pytest.fixture
def game(tmp_path: Path, monkeypatch):
    import xi.ftable.xi_core as fc
    import xi.ftable.xi_expand as xe
    import xi.xi_config as cfg
    root = tmp_path / "game"
    ft, vt = tables(7000, {6665: (25, 54), 6365: (23, 54)})
    write(root, "FTABLE.DAT", ft)
    write(root, "VTABLE.DAT", vt)
    write(root, EN, container(LINES, b"variant\x00"))
    write(root, JP, container(["零。\\v", "ようこそ{player}。\\v", "移動しますか？\nはい\nいいえ"]))
    for mod in (cfg, fc, xe):
        monkeypatch.setattr(mod, "FFXI_DIR", str(root), raising=False)
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", None, raising=False)
    monkeypatch.setattr(xe, "FFXI_PIVOT_DIR", "")
    fc.forget_tables()
    monkeypatch.chdir(tmp_path)
    return root


def blobs(root: Path, rel: str) -> list[bytes]:
    return XD.raw_entry_blobs((root / Path(*rel.split("/"))).read_bytes())[0]


def prepare(data, project="jeuno", *extra):
    from xi.dats.xi_dats import group
    src = Path("lines.json")
    src.write_text(json.dumps(data), encoding="utf-8")
    r = CliRunner().invoke(group, ["prepare", str(src), "--project", project, *extra], catch_exceptions=False)
    assert r.exit_code == 0, r.output


def build(project="jeuno", *extra):
    from xi.dats.xi_dats import group
    return CliRunner().invoke(group, ["build", project, *extra], catch_exceptions=False)


def action(project="jeuno") -> dict:
    from xi.dats.xi_dats import _read_manifest
    return _read_manifest(Path(f"projects/{project}.json"))["actions"][0]


def test_jp_file_ids():
    from xi.zone.xi_inject import zone_dialog_file_id, zone_dialog_jp_file_id
    assert zone_dialog_jp_file_id(245) == 6365
    assert zone_dialog_jp_file_id(280) == zone_dialog_file_id(280) - 300     # model + 1400 vs + 1700


def test_schema_example_and_package_entry():
    schema = load("zone_dialog.json")
    for example in schema["examples"]:
        assert ZD.validate_action(example) == [] and errors(schema, example) == []
    assert "zone_dialog.json" in [e.get("$ref") for e in load("package.json")["$defs"]["entry"]["oneOf"]]
    assert load("ui.json")["properties"]["target"]["properties"]["category"]["enum"] == ["texture", "layout"]


def test_edit_replace_and_jp_then_rebuild_and_undo(game):
    en0, jp0 = (game / EN).read_bytes(), (game / JP).read_bytes()
    prepare({"zone": ZONE, "lines": [
        {"id": 0, "en": "Line zero, edited.\\v", "jp": "零、改。\\v"},
        {"id": 1, "en": {"replace": {"welcome to Jeuno": "welcome to Lower Jeuno"}}}]})
    r = build()
    assert r.exit_code == 0, r.output
    assert "zone 245, 3 lines" in r.output and "line 1 [en]" in r.output
    en = blobs(game, EN)
    assert XD.decode_event_string(en[0].split(b"\0")[0])[0].startswith("Line zero, edited.")
    assert en[1] == XD.encode_event_string("Hello {player}, welcome to Lower Jeuno.\\v") + b"\x00variant\x00"
    assert b"\x08" in en[1]                                           # {player} kept as its code
    assert ZD.line_text(blobs(game, JP)[0]).startswith("零、改。")
    assert (game / (EN + ".base")).is_file()
    recs = action()["result"]["roots"]["dir"]
    assert {(e["id"], e["lang"]) for e in recs} == {(0, "en"), (0, "jp"), (1, "en")}

    after = (game / EN).read_bytes()
    assert build().exit_code == 0                                     # a rebuild converges
    assert (game / EN).read_bytes() == after
    one = next(e for e in action()["result"]["roots"]["dir"] if e["id"] == 1)
    assert one["from"].startswith("Hello") and "welcome to Jeuno" in one["from"]

    from xi.dats.xi_dats import group
    r = CliRunner().invoke(group, ["undo", "jeuno", "--yes"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert (game / EN).read_bytes() == en0 and (game / JP).read_bytes() == jp0


def test_new_lines_grow_both_tables_and_undo_trims_them(game):
    en0, jp0 = (game / EN).read_bytes(), (game / JP).read_bytes()
    prepare({"zone": ZONE, "lines": [{"id": 5, "new": True, "en": "A new line.\\v"}]})
    assert build().exit_code == 0
    en, jp = blobs(game, EN), blobs(game, JP)
    assert len(en) == len(jp) == 6
    assert en[3] == en[4] == b"\x00"                                  # unused lines in between
    assert ZD.line_text(en[5]) == ZD.line_text(jp[5]) == ZD.line_text(XD.encode_event_string("A new line.\\v"))
    from xi.dats.xi_dats import group
    assert CliRunner().invoke(group, ["undo", "jeuno", "--yes"], catch_exceptions=False).exit_code == 0
    assert (game / EN).read_bytes() == en0 and (game / JP).read_bytes() == jp0


@pytest.mark.parametrize("line, says", [
    ({"id": 9, "en": "past"}, "line 9 is past the end — mark it \"new\""),
    ({"id": 1, "new": True, "en": "x"}, "already has line 1"),
    ({"id": 1, "en": {"replace": {"Bastok": "Windurst"}}}, "'Bastok' isn't in"),
])
def test_errors_name_the_line(game, line, says):
    prepare({"zone": ZONE, "lines": [line]})
    r = build()
    assert r.exit_code != 0 and says in r.output


def test_a_line_taken_out_goes_back(game):
    prepare({"zone": ZONE, "lines": [{"id": 0, "en": "A.\\v"}, {"id": 2, "en": "B.\\v"}]})
    before2 = blobs(game, EN)[2]
    assert build().exit_code == 0
    prepare({"zone": ZONE, "lines": [{"id": 0, "en": "A.\\v"}]}, "jeuno", "--replace")
    assert build().exit_code == 0
    assert blobs(game, EN)[2] == before2
    assert [e["id"] for e in action()["result"]["roots"]["dir"]] == [0]


def test_pivot_build_and_prepare_forms(game, tmp_path: Path, monkeypatch):
    import xi.xi_config as cfg
    pivot = tmp_path / "pivot"
    pivot.mkdir()
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", str(pivot))
    en0 = (game / EN).read_bytes()
    prepare([{"id": 0, "en": "Pivot only.\\v"}], "jeuno", "--type", "zone_dialog", "--zone", "245")
    assert build("jeuno", "--pivot").exit_code == 0
    assert (game / EN).read_bytes() == en0
    assert ZD.line_text(blobs(pivot, EN)[0]).startswith("Pivot only.")
    prepare({"zone": ZONE, "lines": [{"id": 1, "en": "Merged.\\v"}]}, "jeuno", "--id", action()["id"], "--merge")
    assert [l["id"] for l in action()["lines"]] == [0, 1] and action()["result"]["roots"]["pivot"]

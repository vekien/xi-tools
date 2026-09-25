"""Building the ``database`` action of ``xi dats`` (xi.database.xi_build): record edits on a
synthetic game folder — item DATs in the legacy (0xC00) and retail (0x1400) formats and a
key-item d_msg table — through ``prepare`` / ``build`` / ``undo``, plus the proposed SQL.
No install needed."""
import json
import struct
from pathlib import Path

import pytest
from click.testing import CliRunner

from xi.common import xi_dmsg as D
from xi.database import xi_build as DB
from xi.ui.items.xi_layout import read_field, text_offset, write_field
from xi.ui.items.xi_parser import ItemDat, _encrypt

ARMOR_EN, ARMOR_JP = "ROM/118/109.DAT", "ROM/0/7.DAT"
WEAPON_EN, WEAPON_JP = "ROM/118/108.DAT", "ROM/0/6.DAT"
KI_EN, KI_JP = "ROM/175/35.DAT", "ROM/175/34.DAT"
FIRE_ICE = b"DEF:40 HP+15 \xef\x20-20\nAccuracy+10"     # \xef\x20 = the Ice icon


def text_sub(b: bytes) -> dict:
    body = b + b"\0"
    return {"flag": 0, "raw": struct.pack("<I", 1) + bytes(D.META_LEN) + body + bytes(-len(body) % 4)}


def num_sub(v: int) -> dict:
    return {"flag": 1, "raw": struct.pack("<I", v)}


def item_record(layout: str, fmt: str, item_id: int, name: bytes, desc: bytes, lang: str, **fields) -> bytes:
    rec = bytearray(0xC00 if fmt == "legacy" else 0x1400)
    rec[-1] = 0xFF
    struct.pack_into("<I", rec, 0, item_id)
    base = {"flags": 0x0800, "stack": 1, "type": 5 if layout == "armor" else 4, "resource_id": item_id & 0xFFFF}
    if layout in ("armor", "weapon"):
        base.update(level=1, slots=0x20, races=0x1FE, jobs=0x7FFFFE)
    for k, v in {**base, **fields}.items():
        write_field(rec, layout, fmt, k, v)
    subs = ([text_sub(name), num_sub(0), text_sub(name.lower()), text_sub(name.lower() + b"s"), text_sub(desc)]
            if lang == "en" else [text_sub(name), text_sub(desc)])
    block = D._assemble_block(subs, 0)
    off = text_offset(layout, fmt)
    rec[off:off + len(block)] = block
    struct.pack_into("<I", rec, 0x280, 8)
    rec[0x284:0x28C] = bytes([item_id & 0xFF]) * 8
    return bytes(rec)


def item_dat(layout: str, fmt: str, first: int, rows: dict, count: int, lang: str) -> bytes:
    """``count`` records from id ``first``; ``rows`` = {id: (name, desc, fields)}, the rest '.'."""
    out = bytearray()
    for i in range(first, first + count):
        name, desc, fields = rows.get(i, (b".", b".", {}))
        out += item_record(layout, fmt, i, name, desc, lang, **fields)
    return _encrypt(bytes(out))


def key_items(rows: dict, jp: bool = False) -> bytes:
    """A key-item table: sub[0] id and sub[1] category are numbers, then text. The Japanese
    table keeps only id, name and description (as both installs' ROM/175/34.DAT do)."""
    blocks = [bytearray(D._assemble_block(
        [num_sub(kid), text_sub(name), text_sub(desc)] if jp else
        [num_sub(kid), num_sub(1), text_sub(b""), text_sub(b""), text_sub(name), text_sub(name + b"s"),
         text_sub(desc)], 700)) for kid, (name, desc) in rows.items()]
    prefix = bytearray(D.HEADER_SIZE)
    prefix[:5] = b"d_msg"
    struct.pack_into("<8I", prefix, 0x10, 0, 0, D.HEADER_SIZE, 0, 700, 0, len(blocks), 0)
    return D.serialize(D.DmsgTable(prefix, blocks, 0, 700, D.HEADER_SIZE))


def write(root: Path, rel: str, data: bytes) -> None:
    p = root / Path(*rel.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def install(root: Path, fmt: str = "legacy") -> Path:
    armor = {10240: (b"Scorpion Harness", FIRE_ICE, {"level": 57}),
             10241: (b"Decennial Coat +1", b"DEF:2", {}),
             10242: (b"Moonshade Earring", b"Accuracy+4", {"level": 90, "slots": 0x1800})}
    write(root, ARMOR_EN, item_dat("armor", fmt, 10240, armor, 8, "en"))
    write(root, ARMOR_JP, item_dat("armor", fmt, 10240, armor, 8, "jp"))
    weapons = {16384: (b"Cesti", b"DMG:4 Delay:288", {"dmg": 4, "delay": 288, "dps": 83, "skill": 1})}
    write(root, WEAPON_EN, item_dat("weapon", fmt, 16384, weapons, 4, "en"))
    write(root, WEAPON_JP, item_dat("weapon", fmt, 16384, weapons, 4, "jp"))
    ki = {1: (b"Zeruhn report", b"The Galka seem to suffer."), 2: (b"Palborough map", b"A map.")}
    write(root, KI_EN, key_items(ki))
    write(root, KI_JP, key_items(ki, jp=True))
    from test_menu_table import dmsg_table
    write(root, TITLES_EN, dmsg_table(["Fodderchief Flayer", "Worm Wrangler", "Kupo Keeper"], 256))
    write(root, TITLES_JP, dmsg_table(["ﾌｫﾀﾞｰ", "ワーム", "クポ"], 256))
    return root


TITLES_EN, TITLES_JP = "ROM/180/78.DAT", "ROM/180/77.DAT"


@pytest.fixture
def game(tmp_path: Path, monkeypatch):
    import xi.ftable.xi_expand as xe
    import xi.xi_config as cfg
    root = install(tmp_path / "game")
    server = tmp_path / "server" / "scripts" / "enum"
    server.mkdir(parents=True)
    (server / "mod.lua").write_text("xi.mod =\n{\n    NONE = 0,\n    DEF = 1,\n    HP = 2,\n    ACC = 25,\n}\n",
                                    encoding="utf-8")
    monkeypatch.setattr(cfg, "FFXI_DIR", str(root))
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", None, raising=False)
    monkeypatch.setattr(cfg, "XI_SERVER_DIR", str(tmp_path / "server"), raising=False)
    monkeypatch.setattr(xe, "FFXI_DIR", str(root))
    monkeypatch.setattr(xe, "FFXI_PIVOT_DIR", "")
    monkeypatch.chdir(tmp_path)
    return root


def record(root: Path, rel: str, idx: int) -> bytes:
    return ItemDat.load(root / Path(*rel.split("/"))).record(idx)


def prepare(edits, project="tweaks", *extra) -> None:
    from xi.dats.xi_dats import group
    src = Path("edits.json")
    src.write_text(json.dumps(edits), encoding="utf-8")
    r = CliRunner().invoke(group, ["prepare", str(src), "--project", project, *extra], catch_exceptions=False)
    assert r.exit_code == 0, r.output


def build(project="tweaks", *extra):
    from xi.dats.xi_dats import group
    return CliRunner().invoke(group, ["build", project, *extra], catch_exceptions=False)


def action(project="tweaks") -> dict:
    from xi.dats.xi_dats import _read_manifest
    return _read_manifest(Path(f"projects/{project}.json"))["actions"][0]


# ── text ─────────────────────────────────────────────────────────────────────

def test_text_codec_is_lossless():
    raw = FIRE_ICE + b" \xfd\x02\x02\x10\x05\xfd \x81\x20 {x}"          # 0x81 0x20: not cp932
    s = DB.decode_text(raw)
    assert s.startswith("DEF:40 HP+15 {Ice}-20\nAccuracy+10 {at:02021005}") and "{x:8120}" in s and "{x:7b}" in s
    assert DB.encode_text(s) == raw
    assert DB.encode_text("{Fire}+5 {Dark}") == b"\xef\x1f+5 \xef\x26"


# ── edits ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fmt", ["legacy", "retail"])
def test_level_and_a_text_replace_build_and_rebuild(game, fmt):
    if fmt == "retail":
        install(game, "retail")
    prepare([{"table": "armor", "id": 10241, "set": {"level": 50}},
             {"table": "armor", "id": 10240, "strings": {"en": {"description": {"replace": {"HP+15": "HP+30"}}}},
              "server": {"item_mods": {"HP": 30}}}])
    r = build()
    assert r.exit_code == 0, r.output
    assert 'armor 10241 "Decennial Coat +1" [en]' in r.output and "level: 1 -> 50" in r.output
    for rel in (ARMOR_EN, ARMOR_JP):              # the header goes to both languages
        assert read_field(record(game, rel, 1), "armor", fmt, "level") == 50
        assert len(record(game, rel, 1)) == (0xC00 if fmt == "legacy" else 0x1400)
    en = record(game, ARMOR_EN, 0)
    assert b"HP+30 \xef\x20-20\nAccuracy+10" in en    # the Ice icon kept byte for byte
    assert (game / "ROM/118/109.DAT.base").is_file()
    res = action()["result"]
    assert res["targets"] == ["dir"]
    recs = res["roots"]["dir"]
    assert {(e["table"], e["id"], e["lang"]) for e in recs} == {("armor", 10241, "en"), ("armor", 10241, "jp"),
                                                               ("armor", 10240, "en")}
    assert all(e["format"] == fmt for e in recs)
    lvl = next(e for e in recs if e["id"] == 10241 and e["lang"] == "en")["changed"]["level"]
    assert lvl == {"from": 1, "to": 50}
    sql = Path("projects/tweaks.sql").read_text(encoding="utf-8")
    assert "UPDATE `item_equipment` SET `level` = 50 WHERE `itemId` = 10241;" in sql
    assert "REPLACE INTO `item_mods` (`itemId`, `modId`, `value`) VALUES (10240, 2, 30);" in sql
    assert res["sql"].replace("\\", "/") == "projects/tweaks.sql"

    # A rebuild converges: same bytes, the original values still recorded for undo.
    dat_after = (game / "ROM/118/109.DAT").read_bytes()
    assert build().exit_code == 0
    assert (game / "ROM/118/109.DAT").read_bytes() == dat_after
    again = next(e for e in action()["result"]["roots"]["dir"] if e["id"] == 10241 and e["lang"] == "en")
    assert again["changed"]["level"] == {"from": 1, "to": 50}
    assert Path("projects/tweaks.sql").read_text(encoding="utf-8").count(">>> database.edits") == 1


def test_dry_run_writes_nothing(game):
    prepare([{"table": "armor", "id": 10241, "set": {"level": 50}}])
    before = (game / "ROM/118/109.DAT").read_bytes()
    r = build("tweaks", "--dry-run")
    assert r.exit_code == 0 and "level: 1 -> 50" in r.output and "UPDATE `item_equipment`" in r.output
    assert (game / "ROM/118/109.DAT").read_bytes() == before
    assert not (game / "ROM/118/109.DAT.base").exists() and not Path("projects/tweaks.sql").exists()
    assert "result" not in action()


def test_an_edit_taken_out_goes_back_on_the_next_build(game):
    prepare([{"table": "armor", "id": 10241, "set": {"level": 50}},
             {"table": "armor", "id": 10242, "set": {"level": 75, "jobs": ["WAR", "PLD"]}}])
    pristine = record(game, ARMOR_EN, 2)
    assert build().exit_code == 0
    assert read_field(record(game, ARMOR_EN, 2), "armor", "legacy", "jobs") == (1 << 1) | (1 << 7)
    prepare([{"table": "armor", "id": 10241, "set": {"level": 50}}], "tweaks", "--replace")
    assert build().exit_code == 0
    assert record(game, ARMOR_EN, 2) == pristine
    assert {e["id"] for e in action()["result"]["roots"]["dir"]} == {10241}


def test_like_creates_a_record_and_undo_puts_the_slot_back(game):
    en0, jp0 = (game / "ROM/118/109.DAT").read_bytes(), (game / "ROM/0/7.DAT").read_bytes()
    prepare([{"table": "armor", "id": 10245, "like": 10242,
              "set": {"level": 75, "flags": ["RARE", "EX", "CANEQUIP"]},
              "strings": {"en": {"name": "Abyssal Earring", "logName": "abyssal earring",
                                 "logPlural": "abyssal earrings", "description": "Accuracy+5"}},
              "server": {"item_mods": {"ACC": 5}}}])
    r = build()
    assert r.exit_code == 0, r.output
    rec = record(game, ARMOR_EN, 5)
    assert struct.unpack_from("<I", rec)[0] == 10245
    assert DB.item_name(rec, "armor", "legacy") == "Abyssal Earring"
    assert read_field(rec, "armor", "legacy", "flags") == 0xC800
    assert read_field(rec, "armor", "legacy", "slots") == 0x1800          # the donor's
    assert DB.item_name(record(game, ARMOR_JP, 5), "armor", "legacy") == "Abyssal Earring"   # EN carried over
    sql = Path("projects/tweaks.sql").read_text(encoding="utf-8")
    assert "INSERT INTO `item_equipment` (`itemId`, `name`" in sql and "WHERE `itemId` = 10242;" in sql
    assert "`name` = 'abyssal_earring'" in sql and "(10245, 25, 5)" in sql
    assert "item_weapon" not in sql                                       # armor copies armor's rows

    from xi.dats.xi_dats import group
    r = CliRunner().invoke(group, ["undo", "tweaks", "--yes"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert "put back 2 records" in r.output
    assert (game / "ROM/118/109.DAT").read_bytes() == en0 and (game / "ROM/0/7.DAT").read_bytes() == jp0
    assert not Path("projects/tweaks.sql").exists() and not Path("projects/tweaks.json").exists()


def test_weapon_dps_follows_damage_and_skill_names(game):
    prepare([{"table": "weapons", "id": 16384, "set": {"damage": 6, "skill": "HAND_TO_HAND"}}])
    assert build().exit_code == 0
    rec = record(game, WEAPON_EN, 0)
    assert read_field(rec, "weapon", "legacy", "dmg") == 6
    assert read_field(rec, "weapon", "legacy", "dps") == 6 * 6000 // 288
    sql = Path("projects/tweaks.sql").read_text(encoding="utf-8")
    assert "UPDATE `item_weapon` SET `dmg` = 6, `skill` = 1 WHERE `itemId` = 16384;" in sql


def test_icon_from_another_item_and_back(game):
    prepare([{"table": "armor", "id": 10241, "icon": {"from": 10242}}])
    pristine = record(game, ARMOR_EN, 1)
    assert build().exit_code == 0
    assert record(game, ARMOR_EN, 1)[0x284:0x28C] == bytes([10242 & 0xFF]) * 8
    from xi.dats.xi_dats import group
    assert CliRunner().invoke(group, ["undo", "tweaks", "--yes"], catch_exceptions=False).exit_code == 0
    assert record(game, ARMOR_EN, 1) == pristine


def test_key_items_edit_and_add(game):
    before = (game / "ROM/175/35.DAT").read_bytes()
    prepare([{"table": "keyitems", "id": 1, "strings": {"en": {"description": "Better morale."}}},
             {"table": "keyitems", "id": 99, "strings": {"en": {"name": "Abyssite", "description": "Glows."}}}])
    r = build()
    assert r.exit_code == 0, r.output
    t = D.parse((game / "ROM/175/35.DAT").read_bytes())
    assert t.num == 3
    subs = DB._block_subs(t.blocks[DB.locate_block(t, "keyitems", 1)])
    assert DB.sub_value(subs[0]) == 1 and DB.sub_value(subs[1]) == 1       # numbers stay numbers
    assert DB.sub_value(subs[6]) == "Better morale."
    new = DB._block_subs(t.blocks[DB.locate_block(t, "keyitems", 99)])
    assert DB.sub_value(new[4]) == "Abyssite" and DB.sub_value(new[0]) == 99
    assert not Path("projects/tweaks.sql").exists()                         # no server side
    from xi.dats.xi_dats import group
    assert CliRunner().invoke(group, ["undo", "tweaks", "--yes"], catch_exceptions=False).exit_code == 0
    assert (game / "ROM/175/35.DAT").read_bytes() == before


def test_pivot_build_leaves_the_install_alone(game, tmp_path: Path, monkeypatch):
    import xi.xi_config as cfg
    pivot = tmp_path / "pivot"
    pivot.mkdir()
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", str(pivot))
    install_bytes = (game / "ROM/118/109.DAT").read_bytes()
    prepare([{"table": "armor", "id": 10241, "set": {"level": 50}}])
    assert build("tweaks", "--pivot").exit_code == 0
    assert (game / "ROM/118/109.DAT").read_bytes() == install_bytes
    assert read_field(record(pivot, ARMOR_EN, 1), "armor", "legacy", "level") == 50
    assert action()["result"]["targets"] == ["pivot"]
    # a base-install build now warns that the pivot copy is the one the client loads
    r = build()
    assert r.exit_code == 0 and "the pivot folder has its own ROM/118/109.DAT" in r.output


@pytest.mark.parametrize("edit, says", [
    ({"table": "armor", "id": 10245, "set": {"level": 5}}, "is an empty slot; give like"),
    ({"table": "armor", "id": 10241, "like": 10242}, "already holds 'Decennial Coat +1'"),
    ({"table": "armor", "id": 16000, "set": {"level": 5}}, "holds 8 records; armor 16000 is past its end"),
    ({"table": "armor", "id": 30000, "set": {"level": 5}}, "armor has no id 30000"),
    ({"table": "armor", "id": 10241, "server": {"item_mods": {"HPP": 3}}}, "'HPP' isn't an xi.mod name"),
    ({"table": "armor", "id": 10240, "strings": {"en": {"description": {"replace": {"MP+9": "MP+1"}}}}},
     "'MP+9' isn't in"),
    ({"table": "keyitems", "id": 7, "strings": {"en": {"name": "x" * 900}}}, "doesn't fit"),
])
def test_build_errors_name_the_edit(game, edit, says):
    prepare([edit])
    r = build()
    assert r.exit_code != 0 and says in r.output and "edits[0]" in r.output


def test_prepare_merges_edits_and_keeps_the_result(game):
    prepare([{"table": "armor", "id": 10241, "set": {"level": 50}}], "tw", "--id", "database.tw")
    assert build("tw").exit_code == 0
    prepare([{"table": "armor", "id": 10242, "set": {"level": 60}},
             {"table": "armor", "id": 10241, "set": {"level": 55}}], "tw", "--id", "database.tw", "--merge")
    a = action("tw")
    assert [(e["id"], e["set"]["level"]) for e in a["edits"]] == [(10241, 55), (10242, 60)]
    assert a["result"]["roots"]["dir"]
    assert build("tw").exit_code == 0
    lvl = next(e for e in action("tw")["result"]["roots"]["dir"] if e["id"] == 10241 and e["lang"] == "en")
    assert lvl["changed"]["level"] == {"from": 1, "to": 55}                # from the original, not 50


def test_changelog_and_package_see_the_records(game):
    from xi.dats.xi_dats import _project_dat_rels, _read_manifest, group
    prepare([{"table": "armor", "id": 10241, "set": {"level": 50}},
             {"table": "keyitems", "id": 2, "strings": {"en": {"name": "Map"}}}])
    assert build().exit_code == 0
    r = CliRunner().invoke(group, ["changelog", "--project", "tweaks"], catch_exceptions=False)
    assert "armor 10241 jp" in r.output and "keyitems 2 en" in r.output
    rels, _ = _project_dat_rels(_read_manifest(Path("projects/tweaks.json")))
    assert rels == sorted({"ROM/118/109.DAT", "ROM/0/7.DAT", "ROM/175/35.DAT"})


def test_a_text_row_past_the_end_needs_like_and_grows_both_languages(game):
    en0, jp0 = (game / TITLES_EN).read_bytes(), (game / TITLES_JP).read_bytes()
    prepare([{"table": "titles", "id": 6, "strings": {"en": {"name": "Abyssea Delver"}}}])
    r = build()
    assert r.exit_code != 0 and "give like: <row> to add it" in r.output
    prepare([{"table": "titles", "id": 6, "like": 1, "strings": {"en": {"name": "Abyssea Delver"}}}],
            "tweaks", "--replace")
    r = build()
    assert r.exit_code == 0, r.output
    for rel, name in ((TITLES_EN, "Abyssea Delver"), (TITLES_JP, "Abyssea Delver")):   # JP takes the EN text
        t = D.parse((game / Path(*rel.split("/"))).read_bytes())
        assert t.num == 7
        assert [DB.sub_value(DB._block_subs(t.blocks[i])[0]) for i in (3, 4, 5)] == [".", ".", "."]
        assert DB.sub_value(DB._block_subs(t.blocks[6])[0]) == name
    from xi.dats.xi_dats import group
    assert CliRunner().invoke(group, ["undo", "tweaks", "--yes"], catch_exceptions=False).exit_code == 0
    assert (game / TITLES_EN).read_bytes() == en0 and (game / TITLES_JP).read_bytes() == jp0


def test_like_on_an_existing_text_row_is_refused(game):
    prepare([{"table": "titles", "id": 1, "like": 0, "strings": {"en": {"name": "x"}}}])
    r = build()
    assert r.exit_code != 0 and "already has row 1; like only adds a new one" in r.output

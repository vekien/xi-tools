"""The ``database`` action's spell and command records (ROM/118/114.DAT), its exact-bytes edits
(``hex``) and d_msg tables named by ROM path — on the synthetic game folder of
test_dats_database_build, with test_menu_table's 114.DAT and name / help tables added."""
import base64
from pathlib import Path

import pytest
from click.testing import CliRunner

import test_dats_database_build as T
import test_menu_table as M
from test_dats_database_build import game as base_game  # noqa: F401 — the fixture below builds on it
from xi.common import xi_dmsg as D
from xi.database import xi_build as DB
from xi.database import xi_core as C
from xi.menu import xi_menu_table as MT


@pytest.fixture
def game(base_game):  # noqa: F811
    M.install(base_game)
    return base_game


def menu(root: Path) -> MT.MenuDat:
    return MT.parse((root / Path(*MT.MENU_DAT.split("/"))).read_bytes())


def spell(root: Path, idx: int) -> dict:
    return MT.read_fields("spell", menu(root).records("spell")[idx])


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*.DAT"))}


def undo(project="tweaks"):
    from xi.dats.xi_dats import group
    r = CliRunner().invoke(group, ["undo", project, "--yes"], catch_exceptions=False)
    assert r.exit_code == 0, r.output


def test_validator_names_the_field():
    errs = "\n".join(C.validate_action({"id": "database.x", "type": "database", "edits": [
        {"table": "spellData", "id": 1, "set": {"bogus": 1, "levels": {"XXX": 1}, "element": "plasma"}},
        {"table": "spellData", "id": 2, "strings": {"en": {"name": "x"}}},
        {"table": "abilityData", "id": 3, "hex": "0011"},
        {"table": "ROM/181/72.DAT", "id": 4, "strings": {"name": "x"}},
        {"table": "armor", "id": 10240, "hex": {"en": "00"}, "set": {"level": 1}},
        {"table": "spellData", "id": 5, "server": {"spell_list": {}}},
    ]}))
    assert "edits[0].set.bogus: table 'spellData' has no field 'bogus'" in errs
    assert "edits[0].set.levels: 'XXX' is not a job" in errs and "'plasma' is not an element" in errs
    assert "edits[1].strings: 'spellData' takes set, like, hex or server: false" in errs
    assert "edits[2].hex is 2 bytes; a record of this table is 48" in errs
    assert "edits[3].strings.name: a table by path names its sub-strings sub0, sub1" in errs
    assert "edits[4]: hex is the whole record; give it alone" in errs
    assert "edits[5].server: only false" in errs


def test_spell_fields_levels_and_the_proposed_sql(game):
    before = snapshot(game)
    T.prepare([{"table": "spellData", "id": 3, "note": "cheaper",
                "set": {"mp": 5, "cast": 4, "element": "ice", "levels": {"RDM": 1, "BLM": None}}}])
    r = T.build()
    assert r.exit_code == 0, r.output
    f = spell(game, 3)
    assert (f["mp"], f["cast"], f["element"], f["levels"]) == (5, 4, 1, {"RDM": 1})
    assert spell(game, 2)["mp"] == 7                                   # the neighbours untouched
    changed = T.action()["result"]["roots"]["dir"][0]["changed"]
    assert changed["levels.BLM"] == {"from": 13, "to": None} and changed["levels.RDM"] == {"from": None, "to": 1}
    sql = Path("projects/tweaks.sql").read_text(encoding="utf-8")
    jobs = bytes(1 if j == "RDM" else 0 for j in C.JOBS).hex()
    assert ("UPDATE `spell_list` SET `mpCost` = 5, `castTime` = 1000, `element` = 2, "
            f"`jobs` = 0x{jobs} WHERE `spellid` = 3;") in sql
    after = snapshot(game)
    assert T.build().exit_code == 0 and snapshot(game) == after       # a rebuild converges
    undo()
    assert snapshot(game) == before


def test_ability_field_and_no_sql(game):
    before = snapshot(game)
    T.prepare([{"table": "abilityData", "id": 2, "set": {"level": 30, "range": 4}}])
    assert T.build().exit_code == 0
    f = MT.read_fields("command", menu(game).records("command")[2])
    assert (f["level"], f["range"]) == (30, 4)
    assert not Path("projects/tweaks.sql").exists()
    undo()
    assert snapshot(game) == before


def test_like_past_the_end_grows_and_undo_shrinks(game):
    before = snapshot(game)
    T.prepare([{"table": "spellData", "id": 10, "like": 1, "set": {"mp": 99}}])
    r = T.build()
    assert r.exit_code == 0, r.output
    m = menu(game)
    assert m.count("spell") == 11 and spell(game, 10)["id"] == 10 and spell(game, 10)["mp"] == 99
    assert not any(m.records("spell")[9])                               # the slot between stays empty
    assert "INSERT INTO `spell_list` SELECT * FROM `_xi_spell`" in Path("projects/tweaks.sql").read_text("utf-8")
    undo()
    assert snapshot(game) == before


def test_an_empty_slot_needs_like(game):
    T.prepare([{"table": "spellData", "id": 20, "set": {"mp": 1}}])
    r = T.build()
    assert r.exit_code != 0 and "is an empty slot; give like" in r.output


def test_exact_bytes_for_a_spell_an_item_and_a_text_row(game):
    before = snapshot(game)
    rec = bytearray(menu(game).records("spell")[4])
    rec[0x0A] = 42                                                     # MP 42, every other byte as is
    item = bytearray(T.record(game, T.ARMOR_EN, 1))
    item[0x10] ^= 0xFF
    ki = D.parse((game / T.KI_EN).read_bytes())
    block = bytearray(ki.blocks[0])
    block[-1] = 0x5A
    T.prepare([{"table": "spellData", "id": 4, "hex": rec.hex()},
               {"table": "armor", "id": 10241, "hex": {"en": item.hex()}},
               {"table": "keyitems", "id": 1, "hex": {"en": block.hex()}}])
    r = T.build()
    assert r.exit_code == 0, r.output
    assert menu(game).records("spell")[4] == bytes(rec)
    assert T.record(game, T.ARMOR_EN, 1) == bytes(item)
    assert D.parse((game / T.KI_EN).read_bytes()).blocks[0] == block
    assert (game / T.ARMOR_JP).read_bytes() == before[T.ARMOR_JP]    # only the language given
    undo()
    assert snapshot(game) == before


def test_a_d_msg_table_by_path(game):
    before = snapshot(game)
    rel = MT.KINDS["spell"].help["en"]
    T.prepare([{"table": rel, "id": 2, "strings": {"sub0": "Freezes the target."}},
               {"table": rel, "id": 9, "like": 1, "strings": {"sub0": "A new help text."}}])
    r = T.build()
    assert r.exit_code == 0, r.output
    t = D.parse((game / rel).read_bytes())
    texts = [DB.sub_value(DB._block_subs(b)[0]) for b in t.blocks]
    assert texts[2] == "Freezes the target." and texts[9] == "A new help text." and texts[8] == "."
    assert (game / MT.KINDS["spell"].help["jp"]).read_bytes() == before[MT.KINDS["spell"].help["jp"]]
    undo()
    assert snapshot(game) == before


def test_help_tables_edit_their_one_sub_string(game):
    before = snapshot(game)
    T.prepare([{"table": "spellHelp", "id": 1, "strings": {"en": {"help": {"replace": {"Help": "Aid"}}}}}])
    r = T.build()
    assert r.exit_code == 0, r.output
    t = D.parse((game / MT.KINDS["spell"].help["en"]).read_bytes())
    assert DB.sub_value(DB._block_subs(t.blocks[1])[0]) == "Aid 1"
    undo()
    assert snapshot(game) == before


def test_the_viewer_rows_and_describe(game):
    from xi.mv.xi_database import menu_rows
    rows = menu_rows((game / Path(*MT.MENU_DAT.split("/"))).read_bytes(), "spell", ["Name 0", "Name 1"])
    assert rows[1]["name"] == "Name 1" and rows[1]["fields"]["mp"] == 7 and rows[1]["levels"] == {"BLM": 13}
    assert base64.b16decode(rows[1]["hex"].upper()) == menu(game).records("spell")[1]
    d = DB.describe(game, "spellData", 1)
    assert d["name"] == "Name 1" and d["fields"]["mp"] == 7 and d["fields"]["levels.BLM"] == 13
    assert DB.describe(game, "spellData", 50)["empty"]

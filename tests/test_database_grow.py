"""``xi database grow`` (xi.database.xi_grow): a d_msg or item table grown to a fixed number of
rows once, its ``.base`` with it — on the synthetic game folder of test_dats_database_build."""
import json
import struct
from pathlib import Path

from click.testing import CliRunner

import test_dats_database_build as T
from test_dats_database_build import ARMOR_EN, TITLES_EN, TITLES_JP, game  # noqa: F401 — the fixture
from xi.common import xi_dmsg as D
from xi.database import xi_build as DB
from xi.dats import xi_build_list as BL


def grow(*args):
    from xi.database.xi_grow import cmd
    return CliRunner().invoke(cmd, list(args), catch_exceptions=False)


def titles(path: Path) -> list[str]:
    t = D.parse(path.read_bytes())
    return [DB.sub_value(DB._block_subs(b)[0]) for b in t.blocks]


def test_a_text_table_grows_with_dot_rows_and_its_base_too(game):
    path = game / Path(*TITLES_EN.split("/"))
    r = grow(TITLES_EN, "10", "--dry-run")
    assert r.exit_code == 0 and "would grow 3 -> 10 rows" in r.output and len(titles(path)) == 3
    r = grow(TITLES_EN, "10")
    assert r.exit_code == 0, r.output
    assert titles(path) == ["Fodderchief Flayer", "Worm Wrangler", "Kupo Keeper"] + ["."] * 7
    assert titles(path.with_name(path.name + ".base")) == titles(path)
    r = grow(TITLES_EN, "10")
    assert r.exit_code == 0 and "already 10 rows" in r.output


def test_rows_it_added_are_edited_like_any_and_a_reset_keeps_them(game):
    path = game / Path(*TITLES_EN.split("/"))
    assert grow(TITLES_EN, "10").exit_code == 0 and grow(TITLES_JP, "10").exit_code == 0    # both languages
    T.prepare([{"table": "titles", "id": 8, "strings": {"en": {"name": "Abyssea Delver"}}},
               {"table": TITLES_EN, "id": 9, "hex": bytes(D.parse(path.read_bytes()).blocks[0]).hex()}], "a")
    Path("projects/build_list.json").write_text(json.dumps({"schema": BL.LIST_SCHEMA, "projects": ["a"]}))
    from xi.dats.xi_dats import group
    for _ in range(2):
        r = CliRunner().invoke(group, ["build", "--list", "--reset"], catch_exceptions=False)
        assert r.exit_code == 0, r.output
        assert titles(path)[7:] == [".", "Abyssea Delver", "Fodderchief Flayer"]


def test_an_item_table_grows_with_its_placeholder_its_ids_counting_on(game):
    r = grow(ARMOR_EN, "12")
    assert r.exit_code == 0, r.output
    for i in range(8, 12):
        rec = T.record(game, ARMOR_EN, i)
        assert struct.unpack_from("<I", rec)[0] == 10240 + i and DB.item_name(rec, "armor", "legacy") == "."
    assert grow(ARMOR_EN, "14", "--fill-from", "2").exit_code == 0
    rec = T.record(game, ARMOR_EN, 13)
    assert struct.unpack_from("<I", rec)[0] == 10253 and DB.item_name(rec, "armor", "legacy") == "Moonshade Earring"
    r = grow(ARMOR_EN, "20", "--fill-hex", "00ff")
    assert r.exit_code != 0 and "--fill-hex is 2 bytes; the table's records are 0xc00" in r.output


def test_pivot_gets_a_grown_copy_and_the_install_is_left_alone(game, tmp_path: Path, monkeypatch):
    import xi.xi_config as cfg
    pivot = tmp_path / "pivot"
    pivot.mkdir()
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", str(pivot))
    install = (game / Path(*TITLES_EN.split("/"))).read_bytes()
    assert grow(TITLES_EN, "6", "--pivot").exit_code == 0
    copy = pivot / Path(*TITLES_EN.split("/"))
    assert len(titles(copy)) == 6 and titles(copy.with_name(copy.name + ".base")) == titles(copy)
    assert (game / Path(*TITLES_EN.split("/"))).read_bytes() == install

"""The server pass of an ability build (design2 §2.2-§2.4, §2.8): ``dats build
--apply-db / --menu-record / --lua-stub``, their console lines, what they record on the
action, and ``dats undo`` / ``package`` for them — driven through the CLI on a synthetic
game folder (a full-size 114.DAT: 1024 spells and 1024 commands, retail placeholders at a
few ids) and an in-memory fake server. No test connects to a database or touches a real
server checkout."""
import json
import re
import struct
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

import _srvtree as T
from _fakedb import FakeServer
from test_dats_ability import ENTRIES, EXAMPLE, _stub_compose
from test_menu_table import command_record, install, spell_record
from test_server_db_apply import ability, spell, wskill
from xi.common import xi_dmsg as DM
from xi.dats import xi_dats as xd
from xi.menu import xi_menu_table as MT
from xi.server import xi_db_apply as D
from xi.server import xi_server_pass as SP

SRV = "xidb@127.0.0.1:3306"
PH_SPELLS = (1023, 1022, 1001)      # blank retail placeholders in the synthetic client
PH_JA = (511, 510, 509)             # abilities 511..509 = comm 1023..1021
PH_WS = (237, 236)
MENU = Path("ROM/118/114.DAT")


def placeholder_spell(i):
    return spell_record(i, kind=8, mp=0, skill=0, icon2=0xFFFF, levels={}, menu_index=0)


def placeholder_command(idx):
    if idx >= 512:
        return command_record(idx, type=1, icon=46, icon2=46, range=0, tp=0xFFFF, level=0)
    return command_record(idx, type=3, icon=46, icon2=46, aoe=0, radius=0, range=0)


def set_names(root: Path, kind: str, names: dict) -> None:
    k = MT.KINDS[kind]
    for lang in MT.LANGS:
        p = root / k.names[lang]
        t = DM.parse(p.read_bytes())
        for i, text in names.items():
            t.blocks[i] = bytearray(DM.set_text(t.blocks[i], 0, text))
        p.write_bytes(DM.serialize(t))


def set_rows(root: Path, kind: str, rows: dict) -> None:
    p = root / MENU
    m = MT.parse(p.read_bytes())
    for i, rec in rows.items():
        m.set_record(kind, i, rec)
    p.write_bytes(m.serialize())


def row(root: Path, kind: str, i: int) -> bytes:
    return MT.parse((root / MENU).read_bytes()).records(kind)[i]


def names(root: Path, kind: str, i: int) -> tuple:
    return MT.read_names(kind, root, "en")[i], MT.read_names(kind, root, "jp")[i]


def client_files(root: Path) -> dict:
    rels = [MENU.as_posix()] + MT.string_tables("spell") + MT.string_tables("command")
    return {r: (root / r).read_bytes() for r in rels}


@pytest.fixture
def world(tmp_path: Path, monkeypatch):
    """A synthetic FFXI_DIR (file tables + a full-size menu table), the cwd where
    projects/ lands, a mix named LOVE, small server id ranges inside the retail bands,
    and a hand-made server tree (not configured unless a test sets it)."""
    import xi.ftable.xi_expand as xe
    import xi.xi_config as cfg
    root = install(tmp_path / "game", n_spells=1024, n_commands=1024)
    m = MT.parse((root / MENU).read_bytes())
    for i in range(900, 1024):          # keep the spell menu indexes below 0x400 (retail's reach 973)
        m.set_record("spell", i, spell_record(i, menu_index=i - 800))
    for i in PH_SPELLS:
        m.set_record("spell", i, placeholder_spell(i))
    for idx in [a + 512 for a in PH_JA] + list(PH_WS):
        m.set_record("command", idx, placeholder_command(idx))
    provoke = bytearray(m.records("command")[547])
    struct.pack_into("<H", provoke, 0x08, 5)                  # its recast id
    m.set_record("command", 547, bytes(provoke))
    (root / MENU).write_bytes(m.serialize())
    set_names(root, "spell", {i: "." for i in PH_SPELLS})
    set_names(root, "command", {i: "." for i in [a + 512 for a in PH_JA] + list(PH_WS)})
    (root / "ROM10").mkdir(parents=True)
    (root / "FTABLE.DAT").write_bytes(b"\0" * (ENTRIES * 2))
    (root / "VTABLE.DAT").write_bytes(b"\0" * ENTRIES)
    (root / "ROM10" / "FTABLE10.DAT").write_bytes(b"\0" * (ENTRIES * 2))
    (root / "ROM10" / "VTABLE10.DAT").write_bytes(b"\0" * ENTRIES)
    monkeypatch.setattr(cfg, "FFXI_DIR", str(root))
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", None, raising=False)
    monkeypatch.setattr(xe, "FFXI_DIR", str(root))
    monkeypatch.setattr(xe, "FFXI_PIVOT_DIR", "")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(D.KIND_TABLES, "spell", D.Table("spell_list", "spellid", 1000, 1023))
    monkeypatch.setitem(D.KIND_TABLES, "ja", D.Table("abilities", "abilityId", 500, 511, (505,)))
    monkeypatch.setitem(D.KIND_TABLES, "ws", D.Table("weapon_skills", "weaponskillid", 230, 237))
    _stub_compose(monkeypatch)
    rp = tmp_path / "exports" / "ability" / "mixer" / "LOVE.mix.json"
    rp.parent.mkdir(parents=True)
    rp.write_text(json.dumps(dict(EXAMPLE, name="LOVE")), encoding="utf-8")
    placeholder = {i: row(root, "spell", i) for i in PH_SPELLS}
    return SimpleNamespace(root=root, rp=rp, tmp=tmp_path, srv=tmp_path / "srv", placeholder=placeholder,
                           cfg=cfg, monkeypatch=monkeypatch)


def fake_server(extra=()):
    rows = {"spell_list": [spell(144, "fire"), spell(1, "cure", group=6), *extra],
            "abilities": [ability(35, "provoke")], "weapon_skills": [wskill(32, "fast_blade")]}
    return FakeServer(rows)


def use_db(monkeypatch, srv):
    monkeypatch.setenv("XI_DB_HOST", "127.0.0.1")
    monkeypatch.setattr(D, "connect", lambda: srv.conn)


def use_server_dir(w):
    T.scripts_tree(w.srv)
    w.monkeypatch.setattr(w.cfg, "XI_SERVER_DIR", str(w.srv), raising=False)


def prepare(w, kind="spell", *extra):
    r = CliRunner().invoke(xd.group, ["prepare", str(w.rp), "--project", "LOVE", "--replace", "--kind", kind,
                                      *extra], catch_exceptions=False)
    assert r.exit_code == 0, r.output


def build(*args):
    r = CliRunner().invoke(xd.group, ["build", "LOVE", *args], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    return r


def step(r, key):
    """The one ``<key>: …`` line, trimmed (None when absent)."""
    hits = [ln.strip() for ln in r.output.splitlines() if ln.strip().startswith(f"{key}: ")]
    assert len(hits) <= 1, hits
    return hits[0] if hits else None


def warns(r, key):
    return [ln.strip()[len(f"⚠ {key}: "):] for ln in r.output.splitlines() if ln.strip().startswith(f"⚠ {key}: ")]


def result():
    return xd._read_manifest(Path("projects/LOVE.json"))["actions"][0]["result"]


def non_select(conn):
    return [s for s, _ in conn.log if not re.match(r"\s*SELECT\b", s, re.I)]


# ── no flags ──────────────────────────────────────────────────────────────────

def test_no_flags_prints_no_server_lines_and_carries_the_results_over(world):
    prepare(world)
    r = build()
    assert not any(step(r, k) for k in ("db", "menu", "lua"))
    m = xd._read_manifest(Path("projects/LOVE.json"))
    saved = {"db": {"table": "spell_list", "id": 1023, "name": "love", "created": True, "confirmed": False,
                    "like": {"id": 144, "name": "fire"}, "group": 2, "animation": 339, "before": None,
                    "op": "insert", "server": SRV, "at": "2026-09-19T00:00:00Z"},
             "menu": {"kind": "spell", "record_kind": "spell", "server_id": 1023, "name": "LOVE",
                      "like": {"server_id": 144, "record_id": 144}, "provisional": False, "roots": {}},
             "lua": {"path": "scripts/actions/spells/black/love.lua", "sha256": "0" * 64, "donor": "black/fire#144",
                     "kind": "spell", "at": "2026-09-19T00:00:00Z"}}
    m["actions"][0]["result"].update(saved, undone="2026-09-19T00:00:00Z")
    xd._write_manifest(Path("projects/LOVE.json"), m)
    r = build()
    assert not any(step(r, k) for k in ("db", "menu", "lua"))
    res = result()
    assert {k: res[k] for k in saved} == saved and "undone" not in res        # a build drops `undone`
    prepare(world)                                                                # a re-prepare keeps them too
    assert {k: result()[k] for k in saved} == saved


# ── Client Menu Record ────────────────────────────────────────────────────────

def test_menu_only_offline_places_a_provisional_record_then_is_unchanged(world):
    root = world.root
    prepare(world)
    donor = row(root, "spell", 144)
    r = build("--menu-record", "--clone-from", "144")
    assert step(r, "menu") == ("menu: place spell 1023 'LOVE' like spell 144, menu index 900, in dir "
                               "(replaces the blank placeholder) · provisional — database not checked")
    assert step(r, "db") is None and step(r, "lua") is None
    w = warns(r, "menu")
    assert SP.RESTART_CLIENT in w and SP.PROVISIONAL in w
    assert row(root, "spell", 1023) == MT.write_fields("spell", donor, {"id": 1023, "menu_index": 900})
    assert names(root, "spell", 1023) == ("LOVE", "LOVE")
    menu = result()["menu"]
    assert menu["server_id"] == 1023 and menu["provisional"] is True and menu["kind"] == "spell"
    assert menu["like"] == {"server_id": 144, "record_id": 144}
    rs = menu["roots"]["dir"]
    assert rs["record_id"] == 1023 and rs["menu_index"] == 900 and rs["written"] == row(root, "spell", 1023).hex()
    assert rs["replaced"]["record"] == world.placeholder[1023].hex()
    before = client_files(root)
    r = build("--menu-record", "--clone-from", "144")
    assert step(r, "menu") == "menu: unchanged spell 1023 'LOVE' in dir"
    assert client_files(root) == before
    assert result()["menu"] == menu                               # nothing to re-record
    r = build("--menu-record")                                    # its own record keeps its donor
    assert step(r, "menu") == "menu: unchanged spell 1023 'LOVE' in dir"
    r = build("--menu-record", "--menu-name", "Big Love")
    assert step(r, "menu").startswith("menu: place spell 1023 'Big Love' like spell 144, menu index 900, in dir "
                                      "(rewrites its own record)")
    assert names(root, "spell", 1023) == ("Big Love", "Big Love")
    assert result()["menu"]["roots"]["dir"]["replaced"]["record"] == world.placeholder[1023].hex()


def test_a_named_row_at_the_server_id_is_refused_and_nothing_is_written(world):
    prepare(world)
    before = client_files(world.root)
    r = build("--menu-record", "--clone-from", "144", "--server-id", "1000")
    assert step(r, "menu") == "menu: refused — Server id 1000 (--server-id): the client's spell 1000 holds 'Name 1000'"
    assert client_files(world.root) == before
    assert "menu" not in result()


def test_a_taken_row_is_repicked_and_the_old_one_left(world):
    root = world.root
    prepare(world)
    build("--menu-record", "--clone-from", "144")
    set_rows(root, "spell", {1023: spell_record(1023, mp=50, menu_index=5)})     # "a retail update"
    set_names(root, "spell", {1023: "Retail"})
    r = build("--menu-record", "--clone-from", "144")
    assert step(r, "menu").startswith("menu: place spell 1022 'LOVE' like spell 144")
    assert "spell 1023 in dir now holds 'Retail'; left as it is" in warns(r, "menu")
    assert names(root, "spell", 1023) == ("Retail", "Retail")
    assert result()["menu"]["server_id"] == 1022


def test_a_moved_record_puts_its_old_row_back(world):
    root = world.root
    prepare(world)
    build("--menu-record", "--clone-from", "144")
    r = build("--menu-record", "--clone-from", "144", "--server-id", "1022")
    assert step(r, "menu").startswith("menu: place spell 1022 'LOVE'")
    assert row(root, "spell", 1023) == world.placeholder[1023] and names(root, "spell", 1023) == (".", ".")
    assert names(root, "spell", 1022) == ("LOVE", "LOVE")


def test_menu_name_with_an_apostrophe(world):
    prepare(world, "ja")
    r = build("--menu-record", "--clone-from", "35", "--menu-name", "Tiger's Fury")
    assert step(r, "menu") == ("menu: place job ability 511 'Tiger\\'s Fury' at command 1023 like job ability 35 "
                               "(command 547), in dir (replaces the blank placeholder) · provisional — database "
                               "not checked")
    assert names(world.root, "command", 1023) == ("Tiger's Fury", "Tiger's Fury")
    assert ("shares job ability 35's recast timer on the client (recast id 5, comm +0x08 can't be changed yet)"
            in warns(r, "menu"))


@pytest.mark.parametrize("bad", ["Love\nLove", "Lo\x07ve"])
def test_a_menu_name_with_a_control_character_is_refused(world, bad):
    prepare(world)
    before = client_files(world.root)
    r = build("--menu-record", "--clone-from", "144", "--menu-name", bad)
    assert step(r, "menu") == "menu: refused — the menu name has a control character or a line break"
    assert client_files(world.root) == before


def test_a_menu_index_past_0x400_is_refused(world):
    root = world.root
    set_rows(root, "spell", {5: spell_record(5, menu_index=1023)})
    prepare(world)
    before = client_files(root)
    r = build("--menu-record", "--clone-from", "144")
    assert step(r, "menu") == "menu: refused — menu index 1024 is past the 1024 the game lists without cexislots"
    assert client_files(root) == before


def test_a_past_end_index_is_refused_and_the_table_does_not_grow(world, monkeypatch):
    root = world.root
    m = MT.parse((root / MENU).read_bytes())
    s = m.section("comm")
    del s.body[600 * 0x30:]
    (root / MENU).write_bytes(m.serialize())
    size = (root / MENU).stat().st_size
    monkeypatch.setitem(D.KIND_TABLES, "ja", D.Table("abilities", "abilityId", 16, 511, (55, 353, 354, 355)))
    prepare(world, "ja")
    r = build("--menu-record", "--clone-from", "35", "--server-id", "200")
    assert step(r, "menu") == ("menu: refused — Server id 200 (--server-id): dir's 114.DAT has only 600 command "
                               "records; 712 is past its end")
    assert (root / MENU).stat().st_size == size


def test_ws_menu_record_needs_database_update(world):
    prepare(world, "ws")
    r = build("--menu-record", "--clone-from", "32")
    assert step(r, "menu") == f"menu: skip — {SP.WS_MENU_SKIP}"


def test_another_projects_id_is_never_taken(world):
    Path("projects").mkdir(exist_ok=True)
    Path("projects/other.json").write_text(json.dumps({"name": "other", "actions": [
        {"id": "ability.other", "type": "ability", "result": {"db": {"table": "spell_list", "id": 1023}}}]}),
        encoding="utf-8")
    prepare(world)
    r = build("--menu-record", "--clone-from", "144")
    assert step(r, "menu").startswith("menu: place spell 1022 'LOVE'")


def test_a_non_pivot_build_warns_when_the_pivot_has_its_own_menu_table(world, tmp_path):
    pivot = tmp_path / "pivot"
    (pivot / "ROM" / "118").mkdir(parents=True)
    (pivot / "ROM" / "118" / "114.DAT").write_bytes((world.root / MENU).read_bytes())
    world.monkeypatch.setattr(world.cfg, "FFXI_PIVOT_DIR", str(pivot), raising=False)
    prepare(world)
    r = build("--menu-record", "--clone-from", "144")
    assert SP.PIVOT_SHADOW in warns(r, "menu")
    assert step(r, "menu").startswith("menu: place spell 1023")


def test_the_game_fixture_pivot_none_gives_no_menu_error(world):
    assert world.cfg.FFXI_PIVOT_DIR is None
    prepare(world)
    r = build("--menu-record", "--clone-from", "144")
    assert not step(r, "menu").startswith("menu: error")
    assert SP.PIVOT_SHADOW not in warns(r, "menu")


def test_a_step_that_raises_is_an_error_line_and_exit_0(world, monkeypatch):
    prepare(world)

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(MT, "place_record", boom)
    r = build("--menu-record", "--clone-from", "144")
    assert step(r, "menu") == "menu: error — boom"
    assert result()["placements"] and "menu" not in result()


# ── menu-only with a database (read-only lookup) ───────────────────────────────

def test_menu_only_finds_the_row_named_after_the_mix_with_selects_only(world, monkeypatch):
    srv = fake_server([spell(1001, "love", anim=5)])
    use_db(monkeypatch, srv)
    prepare(world)
    r = build("--menu-record", "--clone-from", "fire")
    assert step(r, "db") is None
    assert step(r, "menu") == ("menu: place spell 1001 'LOVE' like spell 144, menu index 900, in dir "
                               "(replaces the blank placeholder)")
    assert ("#1001 'love' was not made by this mix; its menu record goes at its id (nothing on the server changes)"
            in warns(r, "menu"))
    assert non_select(srv.conn) == []
    assert result()["menu"]["provisional"] is False


def test_menu_only_with_no_row_takes_a_database_checked_id(world, monkeypatch):
    srv = fake_server([spell(1023, "other_thing")])
    use_db(monkeypatch, srv)
    prepare(world)
    r = build("--menu-record", "--clone-from", "fire")
    assert step(r, "menu") == ("menu: place spell 1022 'LOVE' like spell 144, menu index 900, in dir "
                               "(replaces the blank placeholder)")
    assert non_select(srv.conn) == [] and result()["menu"]["provisional"] is False


def test_menu_only_when_the_database_cannot_be_reached(world, monkeypatch):
    monkeypatch.setenv("XI_DB_HOST", "127.0.0.1")

    def refuse():
        raise D.DbUnavailable("Could not reach the server — is it running, and are the host and port right?")
    monkeypatch.setattr(D, "connect", refuse)
    prepare(world)
    r = build("--menu-record", "--clone-from", "144")
    assert step(r, "menu").endswith("· provisional — database not checked")
    assert ("couldn't check the database (Could not reach the server — is it running, and are the host and port "
            "right?); the id is provisional") in warns(r, "menu")


# ── Database Update ───────────────────────────────────────────────────────────

def test_insert_and_menu_record_land_on_the_same_id(world, monkeypatch):
    srv = fake_server()
    use_db(monkeypatch, srv)
    prepare(world)
    r = build("--apply-db", "--menu-record", "--clone-from", "fire")
    assert step(r, "db") == f"db: insert spell_list #1023 'love' like #144 'fire' animation 339 ({SRV})"
    assert step(r, "menu") == ("menu: place spell 1023 'LOVE' like spell 144, menu index 900, in dir "
                               "(replaces the blank placeholder)")
    assert "learn it in game with !addspell 1023" in warns(r, "db")
    assert "database: 1 row written (spell_list #1023) — restart the map server (xi_map) to load it" in r.output
    res = result()
    assert res["db"]["id"] == res["menu"]["server_id"] == 1023
    assert res["db"]["created"] is True and res["db"]["like"] == {"id": 144, "name": "fire"} and res["db"]["group"] == 2
    assert res["menu"]["provisional"] is False
    assert json.loads(json.dumps(res)) == res
    new = srv.row("spell_list", 1023)
    assert new["name"] == "love" and new["animation"] == 339 and new["group"] == 2 and new["content_tag"] is None
    applied = Path("projects/abilities/love/love_339.applied.sql").read_text(encoding="utf-8")
    assert applied.startswith("-- xi dats build --apply-db · ") and "DELETE FROM `spell_list` WHERE `spellid` = 1023" in applied
    assert "password" not in applied.lower()
    # a republish: the row is ours now, and nothing changes
    r = build("--apply-db", "--menu-record", "--clone-from", "fire")
    assert step(r, "db") == f"db: unchanged spell_list #1023 'love' animation 339 ({SRV})"
    assert step(r, "menu") == "menu: unchanged spell 1023 'LOVE' in dir"
    assert "database:" not in r.output


def test_needs_confirm_writes_nothing_then_db_row_updates(world, monkeypatch):
    srv = fake_server([spell(1001, "love", anim=5)])
    use_db(monkeypatch, srv)
    prepare(world)
    before = client_files(world.root)
    r = build("--apply-db", "--menu-record", "--clone-from", "fire")
    assert step(r, "db") == ("db: needs-confirm spell_list #1001 'love' animation 5 -> 339 — this mix did not create "
                             "that row; confirm it once (Confirm in Manage, or --db-row 1001)")
    assert step(r, "menu") == "menu: skip — waiting for the database row to be confirmed"
    assert non_select(srv.conn) == [] and client_files(world.root) == before
    assert "db" not in result()
    r = build("--apply-db", "--menu-record", "--clone-from", "fire", "--db-row", "1001")
    assert step(r, "db") == f"db: update spell_list #1001 'love' animation 5 -> 339 ({SRV})"
    assert step(r, "menu").startswith("menu: place spell 1001 'LOVE' like spell 144")
    db = result()["db"]
    assert db["confirmed"] is True and db["created"] is False and db["before"] == {"animation": 5, "animationTime": 2000}
    assert srv.row("spell_list", 1001)["animation"] == 339


def test_undo_apply_db_puts_a_confirmed_rows_animation_back(world, monkeypatch):
    srv = fake_server([spell(1001, "love", anim=5)])
    use_db(monkeypatch, srv)
    prepare(world)
    build("--apply-db", "--menu-record", "--clone-from", "fire", "--db-row", "1001")
    assert srv.row("spell_list", 1001)["animation"] == 339
    u = CliRunner().invoke(xd.group, ["undo", "LOVE", "--yes", "--apply-db"], catch_exceptions=False)
    assert u.exit_code == 0, u.output
    assert "database:   spell_list #1001 'love' (animation 5 -> 339 — put back with --apply-db)" in u.output
    assert "db: reverted spell_list #1001 'love' animation 339 -> 5" in u.output
    assert srv.row("spell_list", 1001)["animation"] == 5 and srv.row("spell_list", 1001)["name"] == "love"
    assert row(world.root, "spell", 1001) == world.placeholder[1001]
    assert not Path("projects/LOVE.json").exists()


def test_a_database_that_cannot_be_reached_still_places_the_dats(world, monkeypatch):
    monkeypatch.setenv("XI_DB_HOST", "127.0.0.1")

    def refuse():
        raise D.DbUnavailable("Wrong username or password.")
    monkeypatch.setattr(D, "connect", refuse)
    prepare(world)
    r = build("--apply-db", "--menu-record", "--clone-from", "144")
    assert step(r, "db") == "db: error — Wrong username or password."
    assert step(r, "menu").endswith("· provisional — database not checked")
    res = result()
    assert res["placements"] and "db" not in res and res["menu"]["provisional"] is True


def test_an_insert_needs_a_readable_client_menu_table(world, monkeypatch):
    srv = fake_server()
    use_db(monkeypatch, srv)
    (world.root / MENU).unlink()
    prepare(world)
    r = build("--apply-db", "--clone-from", "fire")
    assert step(r, "db").startswith("db: refused — can't read the client's menu table in ")
    assert non_select(srv.conn) == []


def test_not_configured_skips_the_database_without_connecting(world, monkeypatch):
    monkeypatch.setattr(D, "connect", lambda: pytest.fail("connect must not be called"))
    prepare(world)
    r = build("--apply-db")
    assert step(r, "db") == f"db: skip — {D.NOT_CONFIGURED}"


def test_a_dry_run_reads_only_and_says_would(world, monkeypatch):
    srv = fake_server()
    use_db(monkeypatch, srv)
    use_server_dir(world)
    prepare(world)
    before = client_files(world.root)
    stubs = T.tree_bytes(world.srv)
    r = build("--dry-run", "--apply-db", "--menu-record", "--lua-stub", "--clone-from", "fire")
    assert step(r, "db") == f"db: would insert spell_list #1023 'love' like #144 'fire' animation 339 ({SRV})"
    assert step(r, "menu").startswith("menu: would place spell 1023 'LOVE'")
    assert step(r, "lua") == ("lua: would write scripts/actions/spells/black/love.lua "
                              "(calls black/fire #144 at run time)")
    assert non_select(srv.conn) == [] and client_files(world.root) == before and T.tree_bytes(world.srv) == stubs
    assert "database:" not in r.output and "result" not in xd._read_manifest(Path("projects/LOVE.json"))["actions"][0]


def test_a_type_change_is_refused_and_keeps_what_the_mix_owns(world, monkeypatch):
    srv = fake_server()
    use_db(monkeypatch, srv)
    prepare(world)
    build("--apply-db", "--menu-record", "--clone-from", "fire")
    owned = {k: result()[k] for k in ("db", "menu")}
    prepare(world, "ja")
    r = build("--apply-db", "--menu-record", "--clone-from", "provoke")
    why = ("this mix created spell_list #1023 when its Type was spell; set the Type back, or undo it first "
           "(xi dats undo LOVE --apply-db)")
    assert step(r, "db") == f"db: refused — {why}" and step(r, "menu") == f"menu: refused — {why}"
    assert {k: result()[k] for k in ("db", "menu")} == owned
    writes = non_select(srv.conn)
    assert len(writes) == 1 and writes[0].startswith("INSERT")          # the first build's, and nothing since


def test_a_manifest_that_cannot_be_written_after_an_insert_warns_and_exits_0(world, monkeypatch):
    srv = fake_server()
    use_db(monkeypatch, srv)
    prepare(world)
    real = xd._write_manifest
    calls = []

    def flaky(path, data):
        calls.append(path)
        if len(calls) == 2:
            raise OSError(28, "No space left on device")
        real(path, data)
    monkeypatch.setattr(xd, "_write_manifest", flaky)
    r = build("--apply-db", "--clone-from", "fire")
    assert len(calls) == 2
    w = warns(r, "db")
    assert any(x.startswith("inserted spell_list #1023 but couldn't record it in ")
               and x.endswith("the next publish will treat #1023 as a row this mix didn't make") for x in w), w
    res = result()
    assert res["placements"] and "db" not in res


def test_an_applied_sql_that_cannot_be_written_warns(world, monkeypatch):
    srv = fake_server()
    use_db(monkeypatch, srv)
    prepare(world)

    def refuse(*a, **k):
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr(D, "applied_sql", refuse)
    r = build("--apply-db", "--clone-from", "fire")
    assert any(x.startswith("couldn't write ") and "love_339.applied.sql" in x for x in warns(r, "db"))
    assert result()["db"]["created"] is True


# ── Lua Stub, undo ────────────────────────────────────────────────────────────

def test_insert_writes_the_stub_and_undo_keeps_the_manifest_until_apply_db(world, monkeypatch):
    root = world.root
    srv = fake_server()
    use_db(monkeypatch, srv)
    use_server_dir(world)
    prepare(world)
    r = build("--apply-db", "--menu-record", "--lua-stub", "--clone-from", "fire")
    assert step(r, "lua") == "lua: write scripts/actions/spells/black/love.lua (calls black/fire #144 at run time)"
    stub = world.srv / "scripts/actions/spells/black/love.lua"
    assert stub.is_file() and result()["lua"]["path"] == "scripts/actions/spells/black/love.lua"
    assert "\\" not in step(r, "lua")
    import _minischema as MS
    schema = MS.load("ability.json")                  # result.db / menu / lua as schema/ability.json has them
    assert MS.errors(schema["properties"]["result"], result(), root=schema) == []

    runner = CliRunner()
    u = runner.invoke(xd.group, ["undo", "LOVE", "--yes"], catch_exceptions=False)
    assert u.exit_code == 0, u.output
    assert "database:   spell_list #1023 'love' (created — deleted with --apply-db)" in u.output
    assert "menu:       spell 1023 in dir (the placeholder is put back)" in u.output
    assert "lua stub:   scripts/actions/spells/black/love.lua (removed with --apply-db)" in u.output
    assert "(deleted once nothing is left)" in u.output
    assert "DELETE FROM `spell_list` WHERE `spellid` = 1023 AND `name` = 'love' AND `animation` = 339;" in u.output
    assert "database and server scripts left as they are; run again with --apply-db to revert them" in u.output
    assert ("manifest kept: spell_list #1023 and scripts/actions/spells/black/love.lua are still there; "
            "run xi dats undo LOVE --apply-db to remove them") in u.output
    assert row(root, "spell", 1023) == world.placeholder[1023] and names(root, "spell", 1023) == (".", ".")
    res = result()
    assert res["undone"] and "menu" not in res and res["db"]["id"] == 1023 and res["lua"]["path"]
    assert srv.row("spell_list", 1023) is not None and stub.is_file()

    u = runner.invoke(xd.group, ["undo", "LOVE", "--yes", "--apply-db"], catch_exceptions=False)
    assert u.exit_code == 0, u.output
    assert "db: deleted spell_list #1023 'love'" in u.output
    assert "lua: removed scripts/actions/spells/black/love.lua" in u.output
    assert srv.row("spell_list", 1023) is None and not stub.exists()
    assert not Path("projects/LOVE.json").exists()


@pytest.mark.parametrize("db", ["not configured", "unreachable"])
def test_undo_apply_db_removes_the_stub_without_a_database(world, monkeypatch, db):
    srv = fake_server()
    use_db(monkeypatch, srv)
    use_server_dir(world)
    prepare(world)
    build("--apply-db", "--lua-stub", "--clone-from", "fire")
    if db == "not configured":
        monkeypatch.delenv("XI_DB_HOST")
        why = D.NOT_CONFIGURED
    else:
        why = "Could not reach the server — is it running, and are the host and port right?"

        def refuse():
            raise D.DbUnavailable(why)
        monkeypatch.setattr(D, "connect", refuse)
    u = CliRunner().invoke(xd.group, ["undo", "LOVE", "--yes", "--apply-db"], catch_exceptions=False)
    assert u.exit_code == 0, u.output
    assert f"db: left spell_list #1023 — {why}" in u.output
    assert "lua: removed scripts/actions/spells/black/love.lua" in u.output
    res = result()
    assert "lua" not in res and res["db"]["id"] == 1023 and res["undone"]


def test_undo_leaves_a_menu_row_that_is_no_longer_ours(world):
    root = world.root
    prepare(world)
    build("--menu-record", "--clone-from", "144")
    set_rows(root, "spell", {1023: spell_record(1023, mp=50, menu_index=5)})
    set_names(root, "spell", {1023: "Retail"})
    u = CliRunner().invoke(xd.group, ["undo", "LOVE", "--yes"], catch_exceptions=False)
    assert u.exit_code == 0, u.output
    assert "⚠ menu: spell 1023 in dir now holds 'Retail'; left as it is" in u.output
    assert names(root, "spell", 1023) == ("Retail", "Retail")
    assert not Path("projects/LOVE.json").exists()          # a menu record someone else took never keeps it


def undo(*args):
    u = CliRunner().invoke(xd.group, ["undo", "LOVE", "--yes", *args], catch_exceptions=False)
    assert u.exit_code == 0, u.output
    return u


def test_undo_apply_db_finishes_when_the_created_row_is_already_gone(world, monkeypatch):
    srv = fake_server()
    use_db(monkeypatch, srv)
    prepare(world)
    build("--apply-db", "--clone-from", "fire")
    srv.rows["spell_list"] = [r for r in srv.rows["spell_list"] if r["spellid"] != 1023]    # a dbtool re-import
    u = undo("--apply-db")
    assert "db: spell_list #1023 is already gone" in u.output
    assert "db: left" not in u.output and "manifest kept" not in u.output and "restart the map server" not in u.output
    assert not Path("projects/LOVE.json").exists()
    assert [s for s in non_select(srv.conn) if s.startswith("DELETE")] == [
        "DELETE FROM `spell_list` WHERE `spellid` = %s AND `name` = %s AND `animation` = %s"]


def test_undo_apply_db_finishes_when_the_row_already_has_its_animation_back(world, monkeypatch):
    srv = fake_server([spell(1001, "love", anim=5)])
    use_db(monkeypatch, srv)
    prepare(world)
    build("--apply-db", "--db-row", "1001")
    srv.row("spell_list", 1001)["animation"] = 5                     # put back by hand
    u = undo("--apply-db")
    assert "db: spell_list #1001 already has animation 5" in u.output
    assert "manifest kept" not in u.output and "restart the map server" not in u.output
    assert not Path("projects/LOVE.json").exists()


def test_undo_apply_db_leaves_a_row_that_changed_since_and_keeps_the_manifest(world, monkeypatch):
    srv = fake_server()
    use_db(monkeypatch, srv)
    prepare(world)
    build("--apply-db", "--clone-from", "fire")
    srv.row("spell_list", 1023)["animation"] = 7
    u = undo("--apply-db")
    assert "db: left spell_list #1023 (it changed since; it holds animation 7 now)" in u.output
    assert ("manifest kept: spell_list #1023 is still there; run xi dats undo LOVE --apply-db to remove them"
            in u.output)
    assert srv.row("spell_list", 1023) is not None and result()["db"]["id"] == 1023 and result()["undone"]


def _locked(*a, **k):
    raise PermissionError(13, "Permission denied", "ROM/118/114.DAT")


def test_a_menu_restore_that_fails_keeps_the_manifest_and_the_next_undo_retries(world, monkeypatch):
    root = world.root
    prepare(world)
    build("--menu-record", "--clone-from", "144")
    written = row(root, "spell", 1023)
    menu = result()["menu"]
    real = MT.restore_record
    monkeypatch.setattr(MT, "restore_record", _locked)               # the game has 114.DAT open
    u = undo()
    assert ("⚠ menu: couldn't put back spell 1023 in dir: [Errno 13] Permission denied: 'ROM/118/114.DAT' "
            "(is the game running? close it and undo again)") in u.output
    assert ("manifest kept: the spell 1023 menu record in dir is still there; close the game and run "
            "xi dats undo LOVE again to put it back") in u.output
    assert row(root, "spell", 1023) == written
    res = result()
    assert res["undone"] and res["menu"] == menu                        # the whole binding, for the retry
    assert xd._project_built_targets(xd._read_manifest(Path("projects/LOVE.json"))) == []

    monkeypatch.setattr(MT, "restore_record", real)                  # the game is closed
    u = undo()
    assert "menu:       spell 1023 in dir (the placeholder is put back; it couldn't be last time)" in u.output
    assert "✓ dir: 1 menu record put back" in u.output
    assert row(root, "spell", 1023) == world.placeholder[1023] and names(root, "spell", 1023) == (".", ".")
    assert not Path("projects/LOVE.json").exists()


def test_a_failed_menu_restore_is_kept_beside_a_database_row_and_retried_with_it(world, monkeypatch):
    root = world.root
    srv = fake_server()
    use_db(monkeypatch, srv)
    prepare(world)
    build("--apply-db", "--menu-record", "--clone-from", "fire")
    real = MT.restore_record
    monkeypatch.setattr(MT, "restore_record", _locked)
    u = undo()
    assert ("manifest kept: spell_list #1023 and the spell 1023 menu record in dir are still there; run xi dats "
            "undo LOVE --apply-db to remove them") in u.output
    res = result()
    assert res["undone"] and res["db"]["id"] == 1023 and list(res["menu"]["roots"]) == ["dir"]
    monkeypatch.setattr(MT, "restore_record", real)
    u = undo("--apply-db")
    assert "db: deleted spell_list #1023 'love'" in u.output and "✓ dir: 1 menu record put back" in u.output
    assert row(root, "spell", 1023) == world.placeholder[1023] and srv.row("spell_list", 1023) is None
    assert not Path("projects/LOVE.json").exists()


def test_a_kept_manifest_that_cannot_be_rewritten_warns_and_exits_0(world, monkeypatch):
    srv = fake_server()
    use_db(monkeypatch, srv)
    prepare(world)
    build("--apply-db", "--clone-from", "fire")
    before = Path("projects/LOVE.json").read_bytes()

    def full(path, data):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(xd, "_write_manifest", full)
    u = undo()
    assert any(ln.strip().startswith("⚠ couldn't update ") and "the next undo repeats this one" in ln
               for ln in u.output.splitlines()), u.output
    assert "manifest kept: spell_list #1023 is still there" in u.output
    assert Path("projects/LOVE.json").read_bytes() == before


def test_undo_menu_reports_an_error_instead_of_raising(world, monkeypatch):
    prepare(world)
    build("--menu-record", "--clone-from", "144")
    menu = result()["menu"]
    real = MT.load_menu
    monkeypatch.setattr(MT, "load_menu", lambda root: (_ for _ in ()).throw(ValueError("bad 114.DAT")))
    assert SP.undo_menu(menu, "dir", world.root) == ("error", "couldn't put back spell 1023 in dir: bad 114.DAT")
    monkeypatch.setattr(MT, "load_menu", real)
    assert SP.undo_menu(menu, "dir", world.tmp / "no-such-root") == ("done", None)      # its 114.DAT went with it
    assert SP.menu_record_label(menu, "dir") == "the spell 1023 menu record in dir"


# ── options, package ──────────────────────────────────────────────────────────

def test_ability_publish_forwards_every_server_option(world, monkeypatch):
    from xi.ability.cli import group as ability
    seen = []
    monkeypatch.setattr(SP, "run", lambda pairs, opts, **kw: seen.append((opts, kw["dry_run"])) or False)
    r = CliRunner().invoke(ability, ["publish", str(world.rp), "--project", "LOVE", "--kind", "spell", "--dry-run",
                                     "--apply-db", "--db-row", "7", "--clone-from", "fire", "--server-id", "1001",
                                     "--menu-record", "--menu-name", "Big Love", "--lua-stub"],
                           catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert seen == [(SP.ServerOpts(True, 7, "fire", 1001, True, "Big Love", True), True)]


def test_the_dats_new_wizard_still_builds_with_every_option_at_its_default(world, monkeypatch):
    import inspect
    params = inspect.signature(xd.build_cmd.callback).parameters
    assert {k: params[k].default for k in ("apply_db", "db_row", "clone_from", "server_id", "menu_record",
                                           "menu_name", "lua_stub")} == {
        "apply_db": False, "db_row": None, "clone_from": None, "server_id": None, "menu_record": False,
        "menu_name": None, "lua_stub": False}
    prepare(world)
    monkeypatch.setattr(SP, "run", lambda *a, **k: pytest.fail("no server option was given"))
    with click.Context(xd.group) as ctx:
        ctx.invoke(xd.build_cmd, project="LOVE", pivot=False)       # what `dats new` runs
    assert result()["placements"]


def test_package_ships_an_abilitys_menu_record_only_where_it_was_placed():
    data = {"actions": [{"id": "ability.love", "type": "ability", "result": {
        "targets": ["pivot", "dir"], "placements": [{"dat": "ROM10/20/0.DAT"}],
        "menu": {"kind": "spell", "record_kind": "spell", "server_id": 1023, "roots": {"pivot": {"record_id": 1023}}}}},
        {"id": "ability.gone", "type": "ability", "result": {
            "targets": ["pivot"], "undone": "2026-09-19T00:00:00Z", "placements": [{"dat": "ROM10/20/1.DAT"}],
            "menu": {"kind": "ja", "record_kind": "command", "server_id": 511, "roots": {"pivot": {"record_id": 1023}}}}}]}
    pivot, _ = xd._project_dat_rels(data, target="pivot")
    assert pivot == sorted(["ROM10/20/0.DAT", "ROM/118/114.DAT", *MT.string_tables("spell")])
    assert xd._project_dat_rels(data, target="dir")[0] == ["ROM10/20/0.DAT"]
    assert xd._project_built_targets(data) == ["pivot", "dir"]

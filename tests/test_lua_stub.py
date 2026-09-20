"""Lua Stub (design2 §2.5): plan / write / remove against a tmp server tree."""
import hashlib
import os
import re
from pathlib import Path

import pytest

import _srvtree as T
from xi.server import xi_lua_stub as L

SPELL_DB = {"table": "spell_list", "id": 1023, "name": "love", "created": True, "confirmed": False,
            "like": {"id": 144, "name": "fire"}, "group": 2, "animation": 1100, "before": None}


@pytest.fixture
def srv(tmp_path):
    return T.scripts_tree(tmp_path / "srv")


def ctx(srv, kind="spell", name="LOVE", db=None, **kw):
    kw.setdefault("project", "LOVE")
    kw.setdefault("action", "ability.love")
    return L.StubCtx(kind, name, SPELL_DB if db is None else db, server_dir=str(srv), **kw)


def _entry_points(text: str) -> set:
    return set(re.findall(r"^\w+Object\.(\w+) = function", text, re.M))


@pytest.fixture
def no_rename(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("stubs are written in place, never renamed")
    monkeypatch.setattr(os, "replace", boom)
    monkeypatch.setattr(Path, "rename", boom)
    monkeypatch.setattr(Path, "replace", boom)


def test_spell_write_then_unchanged(srv, no_rename):
    p = L.plan_stub(ctx(srv, created_now=True))
    assert p.op == "write" and p.path == "scripts/actions/spells/black/love.lua"
    assert p.step(would=True).line("lua") == \
        "lua: would write scripts/actions/spells/black/love.lua (calls black/fire #144 at run time)"
    assert not (srv / "scripts/actions/spells/black/love.lua").exists()          # plan writes nothing
    L.write_stub(p)
    f = srv / "scripts" / "actions" / "spells" / "black" / "love.lua"
    data = f.read_bytes()
    assert b"\r" not in data and not data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8")
    assert text.splitlines()[:3] == [L.RULE, "-- Spell: love", L.MARKER]
    assert re.search(r"^-- xi-stub: v=1 project=LOVE action=ability\.love kind=spell donor=black/fire#144 "
                     r"body=sha256:[0-9a-f]{64}$", text, re.M)
    assert "local DONOR_GROUP = 'black'" in text and "local DONOR_ID    = 144" in text
    assert _entry_points(text) == {"onMagicCastingCheck", "onSpellCast"}
    info = L.read_stub(f)
    assert info.marker and info.body_ok and (info.project, info.action) == ("LOVE", "ability.love")
    rec = p.result_lua("2026-09-19T00:00:00Z")
    assert rec == {"path": "scripts/actions/spells/black/love.lua", "sha256": hashlib.sha256(data).hexdigest(),
                   "donor": "black/fire#144", "kind": "spell", "at": "2026-09-19T00:00:00Z"}
    assert "\\" not in rec["path"] and "\\" not in p.step().line("lua")
    w = p.step().warnings
    assert any("hasn't been tried in game yet — cast love once" in x for x in w)
    assert "restart the map server (xi_map) to load it with the new row" in w
    again = L.plan_stub(ctx(srv, prev_lua=rec))
    assert again.op == "unchanged" and again.step().line("lua") == "lua: unchanged scripts/actions/spells/black/love.lua"
    assert again.result_lua()["sha256"] == rec["sha256"]


def test_rewrite_when_the_template_or_donor_changes(srv):
    L.write_stub(L.plan_stub(ctx(srv)))
    db = dict(SPELL_DB, like={"id": 145, "name": "fire"})           # same file, different donor id
    p = L.plan_stub(ctx(srv, db=db))
    assert p.op == "rewrite" and p.step().line("lua").endswith("(donor or template changed)")
    assert "the server reloads changed scripts on its own (restart it if the change doesn't show)" in p.warnings
    L.write_stub(p)
    assert "local DONOR_ID    = 145" in (srv / p.path).read_text(encoding="utf-8")


def test_an_edited_stub_is_kept(srv):
    p = L.write_stub(L.plan_stub(ctx(srv)))
    rec = p.result_lua()
    f = srv / p.path
    f.write_text(f.read_text(encoding="utf-8").replace("return 0\n", "return 1\n"), encoding="utf-8", newline="\n")
    before = f.read_bytes()
    k = L.plan_stub(ctx(srv, prev_lua=rec))
    assert k.op == "kept" and k.step().line("lua") == (
        "lua: kept scripts/actions/spells/black/love.lua — edited by hand; delete its 'xi:' line to own it, "
        "or delete the file to regenerate")
    assert L.write_stub(k).executed is False and f.read_bytes() == before
    assert k.result_lua() == rec


def test_a_file_without_the_marker_is_skipped(srv):
    f = srv / "scripts/actions/spells/black/love.lua"
    f.write_text("-- mine\nreturn {}\n", encoding="utf-8")
    p = L.plan_stub(ctx(srv))
    assert p.op == "skip" and p.reason == "scripts/actions/spells/black/love.lua exists (not written by xi)"
    assert f.read_text(encoding="utf-8") == "-- mine\nreturn {}\n"


def test_another_projects_stub_is_refused(srv):
    L.write_stub(L.plan_stub(ctx(srv, project="OTHER", action="ability.other")))
    p = L.plan_stub(ctx(srv))
    assert p.op == "refused" and p.reason == "scripts/actions/spells/black/love.lua is OTHER/ability.other's stub"


def test_a_script_of_that_name_in_another_folder_is_refused(srv):
    (srv / "scripts/actions/spells/white/love.lua").write_text("return {}\n", encoding="utf-8")
    p = L.plan_stub(ctx(srv))
    assert p.op == "refused" and p.reason == "love already has a script: scripts/actions/spells/white/love.lua"
    (srv / "scripts/actions/abilities/pets/love.lua").write_text("return {}\n", encoding="utf-8")
    ja = dict(SPELL_DB, table="abilities", like={"id": 35, "name": "provoke"})
    p = L.plan_stub(ctx(srv, kind="ja", db=ja))
    assert p.op == "refused" and p.reason == "love already has a script: scripts/actions/abilities/pets/love.lua"


def test_a_group_change_moves_our_own_stub(srv):
    old = L.write_stub(L.plan_stub(ctx(srv)))
    white = dict(SPELL_DB, group=6, like={"id": 1, "name": "cure"})
    p = L.plan_stub(ctx(srv, db=white, prev_lua=old.result_lua()))
    assert p.op == "write" and p.path == "scripts/actions/spells/white/love.lua"
    L.write_stub(p)
    assert (srv / p.path).is_file() and not (srv / old.path).exists()
    assert ("removed the old stub scripts/actions/spells/black/love.lua; it stays loaded until the map "
            "server restarts") in p.warnings


def test_a_group_change_keeps_an_edited_old_stub(srv):
    old = L.write_stub(L.plan_stub(ctx(srv)))
    f = srv / old.path
    f.write_text(f.read_text(encoding="utf-8") + "-- mine\n", encoding="utf-8", newline="\n")
    white = dict(SPELL_DB, group=6, like={"id": 1, "name": "cure"})
    p = L.write_stub(L.plan_stub(ctx(srv, db=white, prev_lua=old.result_lua())))
    assert p.op == "write" and p.executed and f.is_file()
    assert any(w.startswith("the old stub scripts/actions/spells/black/love.lua was edited by hand") for w in p.warnings)


def test_names_a_module_uses_are_refused(srv):
    p = L.plan_stub(ctx(srv, name="hexed"))
    assert p.op == "refused" and "a server module uses the name 'hexed' (modules/x/lua/y.lua)" in p.reason


@pytest.mark.parametrize("name", ["black", "pets", "spells", "weaponskills", "trust"])
def test_path_segment_names_are_refused(srv, name):
    p = L.plan_stub(ctx(srv, name=name))
    assert p.op == "refused" and "folder name of the server's script loader" in p.reason


def test_donor_guards(srv):
    p = L.plan_stub(ctx(srv, db=dict(SPELL_DB, like={"id": 150, "name": "flare"})))
    assert p.op == "refused" and p.reason == "flare has no script in scripts/actions (a module-defined donor isn't supported)"
    for donor in ("corsairs_roll", "box_step"):
        ja = dict(SPELL_DB, table="abilities", like={"id": 98, "name": donor})
        p = L.plan_stub(ctx(srv, kind="ja", db=ja))
        assert p.op == "refused" and "looks its tuning up by ability id" in p.reason, donor
    for g in (0, 3, 8, None):
        p = L.plan_stub(ctx(srv, db=dict(SPELL_DB, group=g)))
        assert p.op == "refused" and "has no script folder" in p.reason


def test_a_missing_group_folder_is_never_created(srv):
    import shutil
    shutil.rmtree(srv / "scripts/actions/spells/white")
    p = L.plan_stub(ctx(srv, db=dict(SPELL_DB, group=6)))
    assert p.op == "refused" and p.reason.startswith("scripts/actions/spells/white not found")
    assert L.write_stub(p).executed is False and not (srv / "scripts/actions/spells/white").exists()


def test_skip_and_refusal_basics(srv, tmp_path):
    p = L.plan_stub(ctx(srv, db=dict(SPELL_DB, created=False)))
    assert p.op == "skip" and p.reason.startswith("Lua Stub writes a script only for a row this mix created")
    assert L.plan_stub(ctx(srv, db={})).op == "skip"
    p = L.plan_stub(L.StubCtx("spell", "LOVE", SPELL_DB, server_dir=""))
    assert p.op == "skip" and p.reason == "no server folder (Settings › Local Server)"
    (tmp_path / "empty").mkdir()
    p = L.plan_stub(ctx(tmp_path / "empty"))
    assert p.op == "refused" and "is XI_SERVER_DIR a LandSandBoat checkout?" in p.reason
    assert p.step().line("lua").startswith("lua: refused — scripts/actions/spells not found")


def test_the_server_dir_defaults_to_xi_server_dir(srv, monkeypatch):
    import xi.xi_config as cfg
    monkeypatch.setattr(cfg, "XI_SERVER_DIR", str(srv))
    p = L.plan_stub(L.StubCtx("spell", "LOVE", SPELL_DB, project="LOVE", action="ability.love"))
    assert p.op == "write"


def test_ja_and_ws_stubs(srv):
    ja = dict(SPELL_DB, table="abilities", id=511, like={"id": 35, "name": "provoke"})
    ja.pop("group")
    p = L.write_stub(L.plan_stub(ctx(srv, kind="ja", db=ja)))
    text = (srv / "scripts/actions/abilities/love.lua").read_text(encoding="utf-8")
    assert p.path == "scripts/actions/abilities/love.lua" and "-- Ability: love" in text
    assert _entry_points(text) == {"onAbilityCheck", "onUseAbility"} and "local DONOR_NAME = 'provoke'" in text
    assert p.step().line("lua") == "lua: write scripts/actions/abilities/love.lua (calls provoke #35 at run time)"
    assert not any("tried in game" in w for w in p.warnings)
    ws = dict(SPELL_DB, table="weapon_skills", id=237, like={"id": 32, "name": "fast_blade"})
    p = L.write_stub(L.plan_stub(ctx(srv, kind="ws", db=ws)))
    text = (srv / "scripts/actions/weaponskills/love.lua").read_text(encoding="utf-8")
    assert "-- Weapon Skill: love" in text and _entry_points(text) == {"onUseWeaponSkill"}
    assert "return 0, 0, false, 0" in text and not any("tried in game" in w for w in p.warnings)
    assert p.result_lua()["donor"] == "fast_blade#32"


def test_the_donor_is_always_the_rows_like(srv):
    p = L.plan_stub(ctx(srv, db=dict(SPELL_DB, like={"id": 1, "name": "cure"}, group=6)))
    assert p.donor == "white/cure#1" and "local DONOR_NAME  = 'cure'" in p.content.decode()


def test_the_helper_table_check_warns(srv):
    (srv / "scripts/globals/spells/damage_spell.lua").write_text("-- no tables\n", encoding="utf-8")
    p = L.plan_stub(ctx(srv))
    assert any(w.startswith("the spell helpers no longer keep the per-spell tables") for w in p.warnings)


def test_remove_stub(srv):
    p = L.write_stub(L.plan_stub(ctx(srv)))
    rec = p.result_lua()
    f = srv / p.path
    assert L.remove_stub(str(srv), rec, project="LOVE", action="ability.love", dry_run=True) == ("removed", None)
    assert f.is_file()
    assert L.remove_stub(str(srv), rec, project="OTHER", action="ability.other")[0] == "left"
    assert L.remove_stub(None, rec) == ("left", "no server folder")
    assert L.remove_stub(str(srv), rec) == ("removed", None) and not f.exists()
    assert L.remove_stub(str(srv), rec) == ("missing", None)
    L.write_stub(L.plan_stub(ctx(srv)))
    f.write_text(f.read_text(encoding="utf-8") + "-- edit\n", encoding="utf-8", newline="\n")
    assert L.remove_stub(str(srv), rec) == ("left", "edited by hand") and f.is_file()
    f.write_text("return {}\n", encoding="utf-8")
    assert L.remove_stub(str(srv), rec) == ("left", "it lost its 'xi:' line (yours now)")
    assert L.remove_stub(str(srv), {"path": "../../etc/x.lua"})[0] == "left"


def test_crlf_normalised_before_hashing(srv):
    p = L.write_stub(L.plan_stub(ctx(srv)))
    f = srv / p.path
    f.write_bytes(f.read_bytes().replace(b"\n", b"\r\n"))
    assert L.read_stub(f).body_ok
    assert L.plan_stub(ctx(srv)).op == "unchanged"

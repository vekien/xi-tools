"""Database Update (design2 §2.3): plan (SELECT only) / execute / revert against an
in-memory fake server. No test connects to a database."""
import re

import pytest

import _srvtree as T
from _fakedb import FakeConn, FakeServer
from xi.server import xi_db_apply as D
from xi.server import xi_ws_widen as W

SRV = "xidb@127.0.0.1:3306"


def spell(i, name, group=2, anim=None, **kw):
    return {"spellid": i, "name": name, "jobs": b"\x00" * 22, "group": group,
            "animation": i if anim is None else anim, "animationTime": 2000, "radius": 0, "content_tag": "COP", **kw}


def ability(i, name, anim=None, job=1, level=15, recast=5):
    return {"abilityId": i, "name": name, "job": job, "level": level, "recastId": recast,
            "animation": i if anim is None else anim, "animationTime": 2000, "range": 0.0, "content_tag": None}


def wskill(i, name, anim=None, skill=5):
    return {"weaponskillid": i, "name": name, "jobs": b"\x00" * 22, "skilllevel": skill,
            "animation": i if anim is None else anim, "animationTime": 2000, "range": 5}


def base_rows():
    return {
        "spell_list": [spell(144, "fire"), spell(1, "cure", group=6), spell(10, "nogroup", group=0),
                       spell(513, "foot_kick", group=3), spell(896, "shantotto", group=8),
                       spell(211, "double-up")],
        "abilities": [ability(35, "provoke"), ability(354, "pet_thing"), ability(16, "mighty_strikes")],
        "weapon_skills": [wskill(32, "fast_blade", anim=32)],
    }


@pytest.fixture
def srv():
    return FakeServer(base_rows())


def ctx(kind="spell", name="LOVE", anim=1100, prev=None, **kw):
    return D.DbCtx(kind, name, anim, prev=prev, **kw)


def created_db(i=1023, like=(144, "fire"), anim=1100, table="spell_list", **kw):
    d = {"table": table, "id": i, "name": "love", "created": True, "confirmed": False,
         "like": {"id": like[0], "name": like[1]}, "group": 2, "animation": anim, "before": None,
         "op": "insert", "server": SRV, "at": "2026-09-19T00:00:00Z"}
    d.update(kw)
    return d


def non_select(conn):
    return [s for s, _ in conn.log if not re.match(r"\s*SELECT\b", s, re.I)]


# ── connection / configuration ────────────────────────────────────────────────

def test_not_configured_means_no_connection(monkeypatch):
    assert D.configured() is None
    assert D.not_configured_step().line("db") == (
        "db: skip — no database configured (Settings › Local Server in the model viewer, or XI_DB_* in "
        "xi-tools' .env)")
    monkeypatch.setenv("XI_DB_HOST", "127.0.0.1")
    c = D.configured()
    assert c and c["source"] == "env" and "password" not in c


def test_connect_failure_is_friendly(monkeypatch):
    import pymysql

    def refuse(**kw):
        assert "password" in kw and kw["connect_timeout"] == 5
        raise pymysql.err.OperationalError(2003, "Can't connect to MySQL server on '127.0.0.1' (10061)")
    monkeypatch.setattr(pymysql, "connect", refuse)
    with pytest.raises(D.DbUnavailable) as e:
        D.connect()
    assert str(e.value) == "Could not reach the server — is it running, and are the host and port right?"


def test_q_refuses_a_write_without_write_true():
    conn = FakeConn([(r".", [])])
    with pytest.raises(AssertionError):
        D._q(conn, "UPDATE spell_list SET animation = 1")
    with pytest.raises(AssertionError):
        D._q(conn, "  DELETE FROM x")
    assert D._q(conn, " select 1") == []


# ── update / confirm ──────────────────────────────────────────────────────────

def test_our_created_row_updates_with_a_guarded_statement(srv):
    srv.rows["spell_list"].append(spell(1023, "love", anim=1100))
    prev = {"db": created_db()}
    p = D.plan(srv.conn, ctx(anim=1200, prev=prev))
    assert p.op == "update" and non_select(srv.conn) == []
    assert p.statement == ("UPDATE `spell_list` SET `animation` = %s WHERE `spellid` = %s AND `animation` = %s",
                           (1200, 1023, 1100))
    assert p.step().line("db") == "db: update spell_list #1023 'love' animation 1100 -> 1200 (xidb@127.0.0.1:3306)"
    assert "restart the map server (xi_map): it reads spell_list only at startup" in p.warnings
    D.execute(srv.conn, p)
    assert p.executed and srv.row("spell_list", 1023)["animation"] == 1200
    rec = p.result_db("T")
    assert rec["created"] is True and rec["before"] is None and rec["like"] == {"id": 144, "name": "fire"}
    assert rec["op"] == "update" and rec["server"] == SRV and "password" not in str(rec)


def test_a_foreign_row_needs_confirming_and_writes_nothing(srv):
    p = D.plan(srv.conn, ctx(name="fire", anim=1100))
    assert p.op == "needs-confirm" and non_select(srv.conn) == []
    assert p.step().line("db") == (
        "db: needs-confirm spell_list #144 'fire' animation 144 -> 1100 — this mix did not create that row; "
        "confirm it once (Confirm in Manage, or --db-row 144)")
    assert D.execute(srv.conn, p) is p and not p.executed and non_select(srv.conn) == []
    assert p.result_db() is None                                       # nothing recorded
    wrong = D.plan(srv.conn, ctx(name="fire", anim=1100, db_row=145))
    assert wrong.op == "needs-confirm" and "(--db-row 145 does not match #144)" in wrong.reason


def test_db_row_confirms_once_and_before_is_kept(srv):
    p = D.plan(srv.conn, ctx(name="fire", anim=1100, db_row=144))
    assert p.op == "update" and p.confirmed
    D.execute(srv.conn, p)
    rec = p.result_db()
    assert rec["confirmed"] is True and rec["created"] is False
    assert rec["before"] == {"animation": 144, "animationTime": 2000}
    # the next publish: ours now, no --db-row needed, and `before` stays the first one
    p2 = D.plan(srv.conn, ctx(name="fire", anim=1200, prev={"db": rec}))
    assert p2.op == "update" and D.execute(srv.conn, p2).executed
    assert p2.result_db()["before"] == {"animation": 144, "animationTime": 2000}
    assert srv.row("spell_list", 144)["animation"] == 1200


def test_db_row_on_an_unchanged_foreign_row_records_confirmed(srv):
    srv.row("spell_list", 144)["animation"] = 1100
    p = D.plan(srv.conn, ctx(name="fire", anim=1100, db_row=144))
    assert p.op == "unchanged" and p.confirmed and non_select(srv.conn) == []
    assert p.step().line("db") == "db: unchanged spell_list #144 'fire' animation 1100 (xidb@127.0.0.1:3306)"
    rec = p.result_db()
    assert rec["confirmed"] is True and rec["op"] == "unchanged" and rec["before"] is None
    plain = D.plan(srv.conn, ctx(name="fire", anim=1100))
    assert plain.op == "unchanged" and plain.result_db() is None


def test_two_rows_with_the_name_are_refused(srv):
    srv.rows["spell_list"].append(spell(700, "FIRE"))
    p = D.plan(srv.conn, ctx(name="fire"))
    assert p.op == "refused" and p.reason == "2 spell_list rows are named 'fire' (#144, #700)"


def test_rowcount_zero_is_an_error(srv):
    srv.rows["spell_list"].append(spell(1023, "love", anim=1100))
    p = D.plan(srv.conn, ctx(anim=1200, prev={"db": created_db()}))
    srv.write_rowcount = 0
    D.execute(srv.conn, p)
    assert p.op == "error" and p.reason == "the row changed while publishing; publish again"
    assert p.result_db() == created_db()                              # binding carried over


def test_a_renamed_bound_row_is_refused(srv):
    srv.rows["spell_list"].append(spell(1023, "someone_else"))
    p = D.plan(srv.conn, ctx(prev={"db": created_db()}))
    assert p.op == "refused" and p.reason == "spell_list #1023 is now named 'someone_else', not 'love'"


# ── insert ────────────────────────────────────────────────────────────────────

def test_no_row_clones_the_default_donor(srv):
    # No row named after the mix and no --clone-from: insert one cloned from the kind's
    # default donor (spell -> cure) so it plays and works; a dev fixes its stats later.
    p = D.plan(srv.conn, ctx(name="double_up", anim=300))
    assert p.op == "insert" and p.like["id"] == 1 and p.like["name"] == "cure"
    assert p.step().line("db") == "db: insert spell_list #1023 'double_up' like #1 'cure' animation 300 (xidb@127.0.0.1:3306)"


def test_default_donor_missing_is_refused(srv):
    srv.rows["spell_list"] = [r for r in srv.rows["spell_list"] if r["name"] != "cure"]
    p = D.plan(srv.conn, ctx(name="double_up", anim=300))
    assert p.op == "refused" and "default donor 'cure'" in p.reason and non_select(srv.conn) == []


def test_insert_clones_the_probed_columns(srv):
    del srv.cols["spell_list"][6]                                  # schema drift: no `radius`
    p = D.plan(srv.conn, ctx(clone_from="Fire", menu_record=False, lua_stub=False))
    assert p.op == "insert" and p.id == 1023 and non_select(srv.conn) == []
    sql, params = p.statement
    assert sql == ("INSERT INTO `spell_list` (`spellid`, `name`, `jobs`, `group`, `animation`, `animationTime`, "
                   "`content_tag`) SELECT %s, %s, `jobs`, `group`, %s, `animationTime`, NULL FROM `spell_list` "
                   "WHERE `spellid` = %s")
    assert params == (1023, "love", 1100, 144)
    assert "love" not in sql                                       # the name is always a parameter
    assert p.step().line("db") == \
        "db: insert spell_list #1023 'love' like #144 'fire' animation 1100 (xidb@127.0.0.1:3306)"
    assert p.warnings == [
        "learn it in game with !addspell 1023",
        "restart the map server (xi_map): it reads spell_list only at startup",
        "no script yet — turn on Lua Stub, or write scripts/actions/spells/black/love.lua (without one it cannot be cast)",
        "no client menu record at spell 1023 — turn on Client Menu Record so players can use it from the menu"]
    D.execute(srv.conn, p)
    row = srv.row("spell_list", 1023)
    assert row["name"] == "love" and row["group"] == 2 and row["animation"] == 1100 and row["content_tag"] is None
    rec = p.result_db("T")
    assert rec == {"table": "spell_list", "id": 1023, "name": "love", "created": True, "confirmed": False,
                   "like": {"id": 144, "name": "fire"}, "group": 2, "animation": 1100, "before": None,
                   "op": "insert", "server": SRV, "at": "T"}


def test_content_tag_is_null_only_when_present(srv):
    p = D.plan(srv.conn, ctx(kind="ws", name="love", anim=200, clone_from="fast blade"))
    assert p.op == "insert" and "NULL" not in p.statement[0] and "content_tag" not in p.statement[0]


def test_id_pick_top_down_with_the_trust_check(srv):
    srv.rows["spell_list"].append(spell(1023, "taken"))
    srv.pools = {6022}                                             # spell 1022 is a trust's
    p = D.plan(srv.conn, ctx(clone_from="fire"))
    assert p.op == "insert" and p.id == 1021
    assert D.pick_server_id(srv.conn, "spell").server_id == 1021
    assert D.pick_server_id(srv.conn, "spell", accept=lambda i: "client row taken" if i == 1021 else None).server_id == 1020


def test_server_id_guards(srv):
    p = D.plan(srv.conn, ctx(clone_from="fire", server_id=1024))
    assert p.op == "refused" and p.reason.startswith("Server id 1024 (--server-id): 1024 is past the server's spell limit")
    p = D.plan(srv.conn, ctx(clone_from="fire", server_id=144))
    assert p.op == "refused" and p.reason == "Server id 144 (--server-id): spell_list #144 already exists"
    p = D.plan(srv.conn, ctx(clone_from="fire", server_id=1000))
    assert p.op == "insert" and p.id == 1000


def test_a_custom_id_picker_is_still_rechecked(srv):
    p = D.plan(srv.conn, ctx(clone_from="fire"), choose_id=lambda plan: D.IdChoice(144, source="pick"))
    assert p.op == "refused" and p.reason == "#144: spell_list #144 already exists ('fire')"
    p = D.plan(srv.conn, ctx(clone_from="fire"), choose_id=lambda plan: D.IdChoice(None, reason="nope"))
    assert p.op == "refused" and p.reason == "nope"


def test_a_name_over_the_probed_maximum_is_refused(srv):
    p = D.plan(srv.conn, ctx(name="x" * 25, clone_from="fire"))
    assert p.op == "refused" and "longer than spell_list.name holds (24)" in p.reason
    p = D.plan(srv.conn, ctx(name="-bad", clone_from="fire"))
    assert p.op == "refused" and p.reason.startswith("the name must start with a letter or digit")


@pytest.mark.parametrize("donor, says", [
    ("foot_kick", "is blue magic spell (group 3)"),
    ("shantotto", "is a trust spell (group 8)"),
    ("nogroup", "has spell group 0, which has no script folder"),
    ("flare", "matches no spell_list row"),
])
def test_spell_donor_guards(srv, donor, says):
    p = D.plan(srv.conn, ctx(clone_from=donor))
    assert p.op == "refused" and says in p.reason and non_select(srv.conn) == []


def test_ja_ids_and_donors(srv, monkeypatch):
    assert 55 not in set(D.candidate_ids("ja")) and not {353, 354, 355} & set(D.candidate_ids("ja"))
    assert next(D.candidate_ids("ja")) == 511
    p = D.plan(srv.conn, ctx(kind="ja", clone_from="pet_thing"))
    assert p.op == "refused" and "not a player job ability" in p.reason
    monkeypatch.setitem(D.KIND_TABLES, "ja", D.Table("abilities", "abilityId", 50, 357, (55, 353, 354, 355)))
    srv.rows["abilities"] += [ability(356, "a"), ability(357, "b")]
    p = D.plan(srv.conn, ctx(kind="ja", clone_from="provoke", menu_record=True))
    assert p.op == "insert" and p.id == 352
    assert p.warnings == [
        "every WAR of level ≥ 15 gets it after the restart (cloned from provoke)",
        "shares provoke's recast timer (recastId 5) on the server and the client",
        "restart the map server (xi_map): it reads abilities only at startup"]
    p = D.plan(srv.conn, ctx(kind="ja", clone_from="provoke", server_id=55))
    assert p.op == "refused" and "55 is reserved on the server" in p.reason


def test_ja_update_warns_when_the_script_sets_its_own_animation(srv, tmp_path):
    srv.rows["abilities"].append(ability(400, "love", anim=10))
    lua = tmp_path / "scripts" / "actions" / "abilities"
    lua.mkdir(parents=True)
    (lua / "love.lua").write_text("action:setAnimation(target:getID(), 99)\n", encoding="utf-8")
    prev = {"db": created_db(400, like=(35, "provoke"), anim=10, table="abilities")}
    p = D.plan(srv.conn, ctx(kind="ja", anim=20, prev=prev, server_dir=str(tmp_path)))
    assert p.op == "update" and "love's script sets its own animation; the new number will not show" in p.warnings


def test_clone_from_differs_from_the_created_rows_donor(srv):
    srv.rows["spell_list"].append(spell(1023, "love", anim=1100))
    p = D.plan(srv.conn, ctx(anim=1200, prev={"db": created_db()}, clone_from="cure"))
    assert p.op == "update"
    assert ("#1023 was cloned from #144 'fire'; Clone from 'cure' is ignored (undo, then publish again, "
            "to clone it from 'cure')") in p.warnings
    D.execute(srv.conn, p)
    assert p.result_db()["like"] == {"id": 144, "name": "fire"}


def test_a_created_row_that_is_gone_is_reinserted_from_its_donor(srv):
    p = D.plan(srv.conn, ctx(prev={"db": created_db()}))            # no --clone-from
    assert p.op == "insert" and p.id == 1023 and p.like["id"] == 144
    assert ("#1023 'love', which this mix created, was missing (a dbtool re-import?); re-inserted it from "
            "#144 'fire' as before") in p.warnings
    D.execute(srv.conn, p)
    assert srv.row("spell_list", 1023)["name"] == "love"
    p = D.plan(srv.conn, ctx(prev={"db": dict(created_db(), id=1022)}, server_id=1000))
    assert p.op == "refused" and p.reason.startswith("this mix already created #1022; undo it first")


# ── the weapon-skill gate ─────────────────────────────────────────────────────

def _widen_column(srv):
    srv.cols["weapon_skills"] = [c if c[0] != "animation" else ("animation", "smallint", "smallint(5) unsigned")
                                 for c in srv.cols["weapon_skills"]]


@pytest.fixture
def ws_srv(srv):
    srv.rows["weapon_skills"].append(wskill(237, "love", anim=100))
    return srv


def ws_prev():
    return {"db": created_db(237, like=(32, "fast_blade"), anim=100, table="weapon_skills")}


def test_ws_at_or_below_255_needs_no_gate(ws_srv, tmp_path):
    stock = T.ws_tree(tmp_path / "stock")
    p = D.plan(ws_srv.conn, ctx(kind="ws", anim=200, prev=ws_prev(), server_dir=str(stock)))
    assert p.op == "update" and not any("16-bit" in w for w in p.warnings)


def test_ws_gate_tinyint_column(ws_srv, tmp_path):
    p = D.plan(ws_srv.conn, ctx(kind="ws", anim=300, prev=ws_prev(), server_dir=str(T.ws_tree(tmp_path / "s"))))
    assert p.op == "refused" and p.reason.startswith("weapon_skills.animation holds 0–255; widen it first")


def test_ws_gate_stock_source_and_foreign_rows_are_refused_not_confirmed(ws_srv, tmp_path):
    _widen_column(ws_srv)
    stock = T.ws_tree(tmp_path / "stock")
    p = D.plan(ws_srv.conn, ctx(kind="ws", anim=300, prev=ws_prev(), server_dir=str(stock)))
    assert p.op == "refused" and p.reason.startswith(
        "xi_map reads weapon-skill animations as 8 bits (src/map/utils/battleutils.cpp:10 …)")
    foreign = D.plan(ws_srv.conn, ctx(kind="ws", name="fast_blade", anim=300, server_dir=str(stock)))
    assert foreign.op == "refused"                                   # never needs-confirm first
    p = D.plan(ws_srv.conn, ctx(kind="ws", anim=300, prev=ws_prev(), server_dir=""))
    assert p.op == "refused" and p.reason == "set the Server folder so the build can check xi_map reads 16 bits"


def test_ws_gate_binary(ws_srv, tmp_path):
    _widen_column(ws_srv)
    wide = T.ws_tree(tmp_path / "wide", files={r: W.widen(r, t) for r, t in T.STOCK.items()})
    T.xi_map(wide, member="uint8")
    p = D.plan(ws_srv.conn, ctx(kind="ws", anim=300, prev=ws_prev(), server_dir=str(wide)))
    assert p.op == "refused" and p.reason.startswith("xi_map has not been rebuilt since the widen (it would play 44)")
    # No PDB, but xi_map.exe is newer than the widened source: built after the widen, so it
    # reads 16 bits — trusted from the file dates, no warning.
    T.xi_map(wide, member=None, pdb=False)
    T.set_times(wide, 1_000_000)
    T.xi_map(wide, member=None, pdb=False, exe_time=2_000_000)
    p = D.plan(ws_srv.conn, ctx(kind="ws", anim=300, prev=ws_prev(), server_dir=str(wide)))
    assert p.op == "update" and not any("can't" in w for w in p.warnings)
    # No xi_map.exe to check at all: it warns (but still updates).
    (wide / "xi_map.exe").unlink()
    p = D.plan(ws_srv.conn, ctx(kind="ws", anim=300, prev=ws_prev(), server_dir=str(wide)))
    assert p.op == "update" and any("no xi_map.exe" in w for w in p.warnings)
    # A PDB that shows a 16-bit m_AnimationId (even beside a stale 8-bit one): confirmed.
    T.xi_map(wide, member="uint16")
    p = D.plan(ws_srv.conn, ctx(kind="ws", anim=300, prev=ws_prev(), server_dir=str(wide)))
    assert p.op == "update" and not any("can't" in w for w in p.warnings)
    D.execute(ws_srv.conn, p)
    assert ws_srv.row("weapon_skills", 237)["animation"] == 300
    ins = D.plan(ws_srv.conn, ctx(kind="ws", name="other", anim=300, clone_from="32", server_dir=str(wide)))
    assert ins.op == "insert" and ins.id == 255
    assert "every job that can use fast_blade gets it (skill ≥ 5) after the restart" in ins.warnings


# ── the Type-change guard ─────────────────────────────────────────────────────

def test_a_type_change_is_refused_while_the_old_kind_is_owned(srv):
    prev = {"db": created_db()}
    p = D.plan(srv.conn, ctx(kind="ja", prev=prev, project="LOVE"))
    assert p.op == "refused" and p.reason == (
        "this mix created spell_list #1023 when its Type was spell; set the Type back, or undo it first "
        "(xi dats undo LOVE --apply-db)")
    assert p.result_db() == prev["db"]
    changed = {"db": {"table": "spell_list", "id": 144, "before": {"animation": 144}, "created": False}}
    assert "this mix changed spell_list #144" in D.type_change_refusal(changed, "ws")
    menu = {"menu": {"kind": "spell", "server_id": 1023, "roots": {"pivot": {}}}}
    assert D.type_change_refusal(menu, "ja").startswith("this mix placed the spell 1023 menu record")
    lua = {"lua": {"kind": "spell", "path": "scripts/actions/spells/black/love.lua"}}
    assert D.type_change_refusal(lua, "ws").startswith("this mix wrote scripts/actions/spells/black/love.lua")
    harmless = {"db": {"table": "spell_list", "id": 144, "created": False, "confirmed": False, "before": None}}
    assert D.type_change_refusal(harmless, "ja") is None


# ── dry run, revert, the applied SQL ──────────────────────────────────────────

def test_a_plan_runs_select_only(srv):
    srv.rows["spell_list"].append(spell(1023, "love", anim=1100))
    for c in (ctx(anim=1200, prev={"db": created_db()}), ctx(name="fire"), ctx(name="new", clone_from="fire"),
              ctx(name="nothing")):
        D.plan(srv.conn, c)
    assert non_select(srv.conn) == [] and srv.conn.log


def test_revert_update_and_delete(srv):
    p = D.plan(srv.conn, ctx(name="fire", anim=1100, db_row=144))
    D.execute(srv.conn, p)
    rec = p.result_db()
    assert D.revert_sql(rec) == "UPDATE `spell_list` SET `animation` = 144 WHERE `spellid` = 144 AND `animation` = 1100;"
    assert D.revert(srv.conn, rec) == ("reverted", None) and srv.row("spell_list", 144)["animation"] == 144
    # already put back (by the run above, or by hand): done, not "changed since"
    assert D.revert(srv.conn, rec) == ("already", {"id": 144, "name": "fire", "animation": 144})
    srv.row("spell_list", 144)["animation"] = 7                         # someone changed it: left
    assert D.revert(srv.conn, rec) == ("changed", {"id": 144, "name": "fire", "animation": 7})
    assert srv.row("spell_list", 144)["animation"] == 7
    srv.rows["spell_list"] = [r for r in srv.rows["spell_list"] if r["spellid"] != 144]
    assert D.revert(srv.conn, rec) == ("gone", None)                  # a changed row that's gone: done
    ins = D.execute(srv.conn, D.plan(srv.conn, ctx(clone_from="cure")))
    rec = ins.result_db()
    srv.row("spell_list", 1023)["animation"] = 5                      # someone changed it
    assert D.revert(srv.conn, rec) == ("changed", {"id": 1023, "name": "love", "animation": 5})
    assert srv.row("spell_list", 1023)
    srv.row("spell_list", 1023)["animation"] = 1100
    assert D.revert(srv.conn, rec) == ("deleted", None) and srv.row("spell_list", 1023) is None
    assert D.revert(srv.conn, rec) == ("gone", None)                  # a dbtool re-import dropped it: done
    srv.rows["spell_list"].append(spell(1023, "other_thing", anim=1100))
    assert D.revert(srv.conn, rec) == ("other", {"id": 1023, "name": "other_thing", "animation": 1100})
    assert srv.row("spell_list", 1023)["name"] == "other_thing"       # not ours: never deleted
    assert {"deleted", "reverted", "gone", "already", "other", "none"} == D.REVERT_DONE
    assert D.revert(srv.conn, {"table": "spell_list", "id": 5, "created": False, "before": None}) == ("none", None)
    sql, params = srv.conn.log[-1]                                    # the look-up is a SELECT by primary key
    assert sql == "SELECT `spellid`, `name`, `animation` FROM `spell_list` WHERE `spellid` = %s" and params == (1023,)
    with pytest.raises(ValueError):
        D.revert(srv.conn, {"table": "users; DROP", "id": 1, "created": True})


def test_applied_sql(srv):
    up = D.execute(srv.conn, D.plan(srv.conn, ctx(name="fire", anim=1100, db_row=144)))
    ins = D.execute(srv.conn, D.plan(srv.conn, ctx(name="it's", clone_from="fire")))
    assert ins.op == "refused"                                        # ' isn't a server name
    ins = D.execute(srv.conn, D.plan(srv.conn, ctx(clone_from="fire")))
    text = D.applied_sql(srv.conn, [up, ins], at="2026-09-19T14:02:11Z")
    assert text == (
        "-- xi dats build --apply-db · 2026-09-19T14:02:11Z · xidb@127.0.0.1:3306\n"
        "-- what ran against the database; safe to run again (keyed by primary key)\n"
        "UPDATE `spell_list` SET `animation` = 1100 WHERE `spellid` = 144;\n"
        "DELETE FROM `spell_list` WHERE `spellid` = 1023 AND `name` = 'love';\n"
        "INSERT INTO `spell_list` (`spellid`, `name`, `jobs`, `group`, `animation`, `animationTime`, `radius`, "
        "`content_tag`) SELECT 1023, 'love', `jobs`, `group`, 1100, `animationTime`, `radius`, NULL FROM "
        "`spell_list` WHERE `spellid` = 144;\n")
    assert D.applied_sql(srv.conn, [D.plan(srv.conn, ctx(name="fire"))]) is None


def test_a_database_error_during_plan_is_an_error_plan():
    s = FakeServer(base_rows(), fail=(r"information_schema", Exception("Lost connection to MySQL server")))
    p = D.plan(s.conn, ctx(name="fire"))
    assert p.op == "error" and p.step().line("db") == "db: error — Lost connection to MySQL server"
    p = D.plan(FakeServer(columns={"abilities": [], "weapon_skills": [], "spell_list": []}).conn, ctx())
    assert p.op == "refused" and p.reason == "no spell_list table in xidb"


def test_offline_id_scan(tmp_path):
    sql = tmp_path / "sql"
    sql.mkdir()
    (sql / "spell_list.sql").write_text("INSERT INTO `spell_list` VALUES (1023,'x',0x00);\n"
                                        "INSERT INTO `spell_list` VALUES (1,'cure',0x00);\n", encoding="utf-8")
    (sql / "mob_pools.sql").write_text("INSERT INTO `mob_pools` VALUES (6022,'t');\n", encoding="utf-8")
    mod = tmp_path / "modules" / "m" / "sql"
    mod.mkdir(parents=True)
    (mod / "a.sql").write_text("INSERT INTO spell_list VALUES (1020,'y'),(1019,'z');\n"
                               "UPDATE spell_list SET animation = 1 WHERE spellid = 1018;\n", encoding="utf-8")
    assert D.server_ids_offline(tmp_path, "spell") == {1023, 1, 1020, 1019, 1018}
    c = D.pick_server_id(None, "spell", server_dir=str(tmp_path))
    assert (c.server_id, c.provisional) == (1021, True)                 # 1022 is a trust pool
    assert D.server_id_problem(None, "spell", 1020, server_dir=str(tmp_path)) == \
        "the server's SQL has a spell_list row #1020"
    assert D.server_ids_offline(None, "spell") is None
    assert D.resolve_donor_offline(str(tmp_path), "spell", "Cure") == ({"id": 1, "name": "cure"}, None)
    assert D.resolve_donor_offline(str(tmp_path), "spell", "144") == ({"id": 144, "name": None}, None)
    assert D.resolve_donor_offline(None, "spell", "fire") == (
        None, "can't resolve Clone from 'fire' without the database; give its id")

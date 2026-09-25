"""The zone editor's publishes through ``xi dats`` (xi.zone.xi_bridge_dats): a published cutscene
is an event of the zone's ``zone_events`` action in the project's ``dats.json``, a custom NPC's
name an entry of its ``zone_npcs`` action; deleting either takes the tables back — on the
synthetic game folder of test_dats_zone_events."""
import json
from pathlib import Path

import pytest

import test_dats_zone_events as T
from test_dats_zone_events import game  # noqa: F401 — the fixture
from xi.zone import xi_bridge as B
from xi.zone import xi_bridge_dats as BD


@pytest.fixture
def ws(game, tmp_path: Path, monkeypatch):  # noqa: F811
    folder = tmp_path / "workspaces" / "0123456789abcdef"
    folder.mkdir(parents=True)
    (folder.parent / "projects.json").write_text(json.dumps(
        {"projects": [{"id": folder.name, "name": "Gate Guard"}]}), encoding="utf-8")
    monkeypatch.setattr(B, "_ACTIVE_WS_ROOT", folder)
    return folder


def publish(cs: dict, **more) -> dict:
    return B._compile_cutscene({"zoneId": T.ZONE, "cutscene": cs, "dryRun": False, "publishPivot": False, **more})


def test_publish_republish_and_delete(game, ws):  # noqa: F811
    before = T.snapshot(game)
    r = publish(T.cutscene(("Halt!",)))
    assert r["ok"], r
    owner = T.sid(1)
    stem = f"{T.ZONE}_{owner}_{r['eventId']}"
    assert r["eventId"] == 6 and (ws / "cutscene-defs" / f"{stem}.json").is_file()
    assert r["written"] and "startCutscene(6" in r["luaStub"]
    manifest = json.loads((ws / "dats.json").read_text(encoding="utf-8"))
    (action,) = manifest["actions"]
    assert manifest["name"] == "gate_guard" and action["type"] == "zone_events"
    assert action["events"] == [{"name": stem, "cutscene": f"cutscene-defs/{stem}.json"}]
    assert (ws / "dats.lua").is_file()

    # The editor sends the id back pinned; an edit republishes the same event, same line id.
    r2 = publish(T.cutscene(("Halt! Again.",), eventId=6))
    assert r2["ok"] and r2["eventId"] == 6, r2
    en = T.lines(game)
    assert len(en) == 3 and T.text(en[2]) == "Halt! Again."
    assert len(json.loads((ws / "dats.json").read_text(encoding="utf-8"))["actions"][0]["events"]) == 1

    d = B._delete_event({"zoneId": T.ZONE, "actorId": owner, "eventId": 6})
    assert d["ok"] and d["dats"]["removed"], d
    assert T.snapshot(game) == before
    assert json.loads((ws / "dats.json").read_text(encoding="utf-8"))["actions"] == []


def test_two_events_delete_one(game, ws):  # noqa: F811
    assert publish(T.cutscene(("One.",)))["ok"]
    r = publish(T.cutscene(("Two.",), actor=2))
    assert r["ok"], r
    d = B._delete_event({"zoneId": T.ZONE, "actorId": T.sid(1), "eventId": 6})
    assert d["ok"] and not d["dats"]["removed"], d
    guard, other = T.actor(game, T.sid(1)), T.actor(game, T.sid(2))
    assert guard.event_ids == [5] and other.event_ids == [r["eventId"]]


def test_an_event_the_editor_did_not_publish_is_not_its_to_delete(game, ws):  # noqa: F811
    assert BD.delete_event(T.ZONE, T.sid(1), 5, pivot=False) is None


def test_custom_npc_name_goes_through_zone_npcs(game, ws):  # noqa: F811
    before = T.read(game, T.NAMES)
    rec = {"npcid": T.sid(0x380), "name": "Vekien", "modelid": 25005, "zoneId": T.ZONE, "status": 6,
           "pos": [0, 0, 0], "rot": 0}
    r = BD.put_npc(rec)
    assert r["ok"], r
    from xi.entity import xi_zone_npcs as ZN
    assert ("Vekien", T.sid(0x380)) in ZN.parse_names(T.read(game, T.NAMES))
    r = BD.put_npc({**rec, "name": "Vekien the Bold"})
    assert r["ok"] and ("Vekien the Bold", T.sid(0x380)) in ZN.parse_names(T.read(game, T.NAMES))
    sql = (ws / "dats.sql").read_text(encoding="utf-8")
    assert "Vekien the Bold" in sql
    assert BD.drop_npc(T.sid(0x380))["ok"]
    assert T.read(game, T.NAMES) == before
    assert BD.drop_npc(T.sid(0x380)) is None

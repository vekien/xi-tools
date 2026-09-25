"""The ``zone_events`` action of ``xi dats`` (xi.event.xi_zone_events): cutscenes and dialogues
compiled into a zone's event table with their lines in its dialog tables, rebuilt to the same
bytes and undone exactly — on a synthetic game folder whose FTABLE registers the zone's event,
dialog (English + Japanese) and name tables, and the ROM10 pair a camera scene registers in."""
import json
import struct
from pathlib import Path

import pytest
from click.testing import CliRunner

from _minischema import errors, load
from xi.dialog import xi_dialog as XD
from xi.event import xi_event as core
from xi.event import xi_zone_events as ZE

ZONE = 245
EVENTS, DIALOG, DIALOG_JP, NAMES = "ROM/21/54.DAT", "ROM/25/54.DAT", "ROM/23/54.DAT", "ROM/27/54.DAT"
CAMERA = "ROM10/490/55.DAT"


def sid(local: int) -> int:
    return 0x01000000 | (ZONE << 12) | local


def block(actor: int, events=(), scene: bytes = b"") -> core.RawActor:
    a = core.RawActor(actor, [off for _e, off in events], [e for e, _off in events], [], scene, b"", b"", dirty=True)
    a.raw_block = core.serialize_actor(a)
    a.block_pad = a.raw_block[len(a.raw_block) - ((-len(scene)) % 4):] if (-len(scene)) % 4 else b""
    return a


def name_table(rows) -> bytes:
    out = bytearray()
    for name, s in rows:
        rec = bytearray(32)
        rec[:len(name)] = name.encode("cp932")
        struct.pack_into("<I", rec, 28, s)
        out += rec
    return bytes(out)


def write(root: Path, rel: str, data: bytes) -> None:
    p = root / Path(*rel.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def read(root: Path, rel: str) -> bytes:
    return (root / Path(*rel.split("/"))).read_bytes()


@pytest.fixture
def game(tmp_path: Path, monkeypatch):
    import xi.ftable.xi_core as fc
    import xi.ftable.xi_expand as xe
    import xi.xi_config as cfg
    from xi.event import xi_cutscene_publish as CP
    root = tmp_path / "game"
    n = 57300
    ft, vt = bytearray(2 * n), bytearray(n)
    for fid, (sub, idx) in {6065: (21, 54), 6665: (25, 54), 6365: (23, 54), 6965: (27, 54)}.items():
        struct.pack_into("<H", ft, fid * 2, (sub << 7) | idx)
        vt[fid] = 1
    write(root, "FTABLE.DAT", bytes(ft))
    write(root, "VTABLE.DAT", bytes(vt))
    write(root, "ROM10/FTABLE10.DAT", bytes(2 * n))
    write(root, "ROM10/VTABLE10.DAT", bytes(n))
    # The zone's own block, the gate guard with retail event 5 (noop, end), an NPC with none.
    write(root, EVENTS, core.build_event_dat([block(0x7FFFFFF0), block(sid(1), [(5, 0)], b"\x00\x21"),
                                              block(sid(2))]))
    retail = [XD.encode_event_string(t) + b"\x00" for t in ("Welcome to Jeuno.", "Move along.")]
    write(root, DIALOG, XD.build_container(retail))
    write(root, DIALOG_JP, XD.build_container(retail))
    write(root, NAMES, name_table([("none", 0), ("Wolfgang", sid(1)), ("Pherimociel", sid(2))]))
    server = tmp_path / "server" / "scripts" / "enum"
    server.mkdir(parents=True)
    (server / "mod.lua").write_text("xi.mod =\n{\n    HP = 2,\n}\n", encoding="utf-8")
    # Modules that copy FFXI_DIR when first imported are imported first and patched with the
    # rest, so none keeps this folder once the test is over; the caches keyed by less than
    # the install start empty and go back after.
    import sys
    import xi.event.xi_commands  # noqa: F401
    import xi.zone.xi_list  # noqa: F401
    for name, mod in list(sys.modules.items()):
        if (name == "xi" or name.startswith("xi.")) and isinstance(getattr(mod, "FFXI_DIR", None), str):
            monkeypatch.setattr(mod, "FFXI_DIR", str(root))
    monkeypatch.setattr(core, "_ZONE_NAME_CACHE", None)
    monkeypatch.setattr(CP, "_GESTURE_BANK_TAGS_CACHE", {})
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", None, raising=False)
    monkeypatch.setattr(xe, "FFXI_PIVOT_DIR", "")
    monkeypatch.setattr(CP, "default_look_rows", lambda ids: {})     # no server, no editor registry
    fc.forget_tables()
    monkeypatch.chdir(tmp_path)
    yield root
    fc.forget_tables()


def cutscene(lines=("Halt! Who goes there?",), actor=1, cast=(), **more) -> dict:
    cs = {"schema": "xi.cutscene.v1", "actor": "guard",
          "cast": {"schema": "xi.cutscene.npc.v1", "cast": [
              {"id": "player", "entity": "player"},
              {"id": "guard", "entity": f"0x{sid(actor):08X}", "name": "Wolfgang"},
              *[{"id": f"extra{i}", "entity": f"0x{sid(c):08X}"} for i, c in enumerate(cast)]]},
          "dialog": {"schema": "xi.cutscene.dialog.v1",
                     "lines": [{"id": f"l{i}", "text": t, "speaker": "guard"} for i, t in enumerate(lines)]},
          "steps": [*[{"op": "say", "speaker": "guard", "text": f"l{i}"} for i in range(len(lines))], {"op": "end"}]}
    cs.update(more)
    return cs


def prepare(data, project="jeuno", *extra, name="events.json"):
    from xi.dats.xi_dats import group
    src = Path(name)
    src.write_text(json.dumps(data), encoding="utf-8")
    r = CliRunner().invoke(group, ["prepare", str(src), "--project", project, *extra], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    return r


def build(project="jeuno", *extra):
    from xi.dats.xi_dats import group
    return CliRunner().invoke(group, ["build", project, *extra], catch_exceptions=False)


def undo(project="jeuno"):
    from xi.dats.xi_dats import group
    r = CliRunner().invoke(group, ["undo", project, "--yes"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    return r


def actions(project="jeuno") -> list:
    from xi.dats.xi_dats import _read_manifest
    return _read_manifest(Path(f"projects/{project}.json"))["actions"]


def actor(root: Path, entity: int):
    return next((a for a in core.parse_raw_actors(read(root, EVENTS)) if a.actor_id == entity), None)


def lines(root: Path, rel: str = DIALOG) -> list:
    return XD.raw_entry_blobs(read(root, rel))[0]


def text(blob: bytes) -> str:
    """A line's displayed text, without the ▼ prompt a cutscene line ends in."""
    shown = XD.decode_event_string(blob.split(b"\x00")[0])[0]
    return shown.split("{")[0] if "{" in shown else shown


def snapshot(root: Path) -> dict:
    return {rel: read(root, rel) for rel in (EVENTS, DIALOG, DIALOG_JP, NAMES)}


def test_schema_example_and_package_entry():
    schema = load("zone_events.json")
    for example in schema["examples"]:
        assert ZE.validate_action(example) == [] and errors(schema, example) == []
    refs = [e.get("$ref") for e in load("package.json")["$defs"]["entry"]["oneOf"]]
    assert "zone_events.json" in refs and "event.json" not in refs


def test_validator_names_the_field():
    errs = "\n".join(ZE.validate_action({"id": "zone_events.x", "type": "zone_events", "zone": ZONE, "events": [
        {"cutscene": {"schema": "nope"}},
        {"name": "a", "dialogue": {"actor": "guard", "lines": []}},
        {"name": "b", "cutscene": "b.json", "dialogue": {"actor": 1, "lines": ["x"]}},
        {"cutscene": "c.json", "camera": "490/55.DAT", "cameraFileId": 71367, "eventId": -1},
        {"cutscene": "dir/c.json"},
    ]}))
    assert "events[0].cutscene.schema must be" in errs and "events[0]: an inline event needs a name" in errs
    assert "events[1].dialogue.actor must be" in errs and "events[1].dialogue.lines must be" in errs
    assert "events[2]: give cutscene or dialogue (one of them)" in errs
    assert "events[3].camera must be" in errs and "events[3].cameraFileId must be in the safe" in errs
    assert "events[3].eventId must be" in errs
    assert "events[4]: the name 'c' is taken by events[3]" in errs


def test_cutscene_builds_rebuilds_the_same_and_undoes_exactly(game):
    before = snapshot(game)
    prepare({"zone": ZONE, "events": [{"name": "halt", "cutscene": cutscene(("Halt!", "Who goes there?"),
                                                                            cast=[2])}]})
    r = build()
    assert r.exit_code == 0, r.output
    guard, extra = actor(game, sid(1)), actor(game, sid(2))
    rec = actions()[0]["result"]["roots"]["dir"][0]
    assert rec["event"] in guard.event_ids and guard.event_ids[0] == 5          # retail's slot 0 is kept
    assert rec["event"] in extra.event_ids                                    # the cast NPC's marker block
    en, jp = lines(game), lines(game, DIALOG_JP)
    assert len(en) == 4 and XD.decode_event_string(en[2].split(b"\x00")[0])[0].startswith("Halt!")
    assert jp[2:] == en[2:]                                                    # the same lines at the same ids
    assert [ln["grew"] for ln in rec["lines"]] == [[2, 4], [2, 4]]
    lua = Path("projects/jeuno.lua").read_text(encoding="utf-8")
    assert f"startCutscene({rec['event']}" in lua and "-- >>> zone_events.events" in lua
    assert "Wolfgang" in lua

    after = snapshot(game)
    assert build().exit_code == 0                                              # a rebuild converges
    assert snapshot(game) == after and actions()[0]["result"]["roots"]["dir"][0]["event"] == rec["event"]

    undo()
    assert snapshot(game) == before and not Path("projects/jeuno.lua").exists()


def test_edited_cutscene_keeps_its_event_and_line_ids(game):
    prepare({"zone": ZONE, "events": [{"name": "halt", "cutscene": cutscene(("One.", "Two."))}]})
    assert build().exit_code == 0
    first = actions()[0]["result"]["roots"]["dir"][0]
    prepare({"zone": ZONE, "events": [{"name": "halt", "cutscene": cutscene(("One!", "Two!", "Three!"))}]},
            "jeuno", "--id", "zone_events.events", "--replace")
    assert build().exit_code == 0
    again = actions()[0]["result"]["roots"]["dir"][0]
    assert again["event"] == first["event"]
    en = lines(game)                          # lines 2 and 3 are rewritten; the third goes on the end
    assert len(en) == 5
    assert [text(b) for b in en[2:]] == ["One!", "Two!", "Three!"]


def test_pinned_event_replaces_retail_and_undo_puts_it_back(game):
    before = snapshot(game)
    prepare({"zone": ZONE, "events": [{"name": "gate", "eventId": 5, "cutscene": cutscene(("New words.",))}]})
    assert build().exit_code == 0
    guard = actor(game, sid(1))
    assert guard.event_ids == [5] and bytes(guard.scene_data) != b"\x00\x21"
    undo()
    assert snapshot(game) == before


def test_dialogue_event(game):
    before = snapshot(game)
    prepare({"zone": ZONE, "events": [{"name": "chat", "dialogue": {"actor": f"0x{sid(2):08X}",
                                                                    "lines": ["Hi.", "Bye."]}}]})
    r = build()
    assert r.exit_code == 0, r.output
    rec = actions()[0]["result"]["roots"]["dir"][0]
    assert actor(game, sid(2)).event_ids == [rec["event"]] and len(lines(game)) == 4
    undo()
    assert snapshot(game) == before


def test_project_of_zone_actions_rebuilds_in_order(game):
    """zone_npcs adds an NPC, zone_dialog a line, zone_events a cutscene casting the new NPC with
    more lines after: the tables are shared, and each rebuild takes the stack down first."""
    before = snapshot(game)
    prepare({"zone": ZONE, "npcs": [{"id": 0x380, "new": True, "name": "Vekien",
                                     "server": {"model": 25005}}]}, name="npcs.json")
    prepare({"zone": ZONE, "lines": [{"id": 2, "new": True, "en": "A new line."}]}, name="dialog.json")
    prepare({"zone": ZONE, "events": [{"name": "meet", "cutscene": cutscene(("Meet Vekien.",), cast=[0x380],
                                                                            flags={"hideNpcNames": True})}]})
    assert build().exit_code == 0
    after = snapshot(game)
    assert len(lines(game)) == 4 and actor(game, sid(0x380)) is not None
    sql = Path("projects/jeuno.sql").read_text(encoding="utf-8")
    assert f"`namevis` = `namevis` | 32 WHERE `npcid` IN ({sid(0x380)})" in sql
    r = build()
    assert r.exit_code == 0 and "⚠" not in r.output, r.output
    assert snapshot(game) == after
    dry = build("jeuno", "--dry-run")
    assert dry.exit_code == 0 and "⚠" not in dry.output, dry.output
    assert snapshot(game) == after
    undo()
    assert snapshot(game) == before


def test_lines_added_after_are_kept_and_ids_do_not_move(game):
    """Another project added lines after this one's: a rebuild blanks and refills its own ids."""
    prepare({"zone": ZONE, "events": [{"name": "halt", "cutscene": cutscene(("First.",))}]}, "a")
    assert build("a").exit_code == 0
    prepare({"zone": ZONE, "lines": [{"id": 3, "new": True, "en": "B's line."}]}, "b", name="b.json")
    assert build("b").exit_code == 0
    r = build("a")
    assert r.exit_code == 0, r.output
    en = lines(game)
    assert len(en) == 4 and en[3].startswith(XD.encode_event_string("B's line."))
    assert XD.decode_event_string(en[2].split(b"\x00")[0])[0].startswith("First.")
    undo("a")
    en = lines(game)
    assert len(en) == 4 and en[2] == b"\x00"                                  # a blank id; B's line stays at 3
    undo("b")
    assert len(lines(game)) == 3


def test_camera_scene_is_placed_registered_and_removed(game):
    from xi.ftable.xi_core import resolve_dat_in_root
    shot = {"keyframes": [{"eye": [0, -2, 5], "look": [0, -1, 0], "fov": 350, "roll": 0, "time": 0}],
            "duration": 0}
    cs = cutscene(("Look.",), steps=[{"op": "camera", "shotSpec": shot},
                                     {"op": "say", "speaker": "guard", "text": "l0"}, {"op": "end"}])
    prepare({"zone": ZONE, "events": [{"name": "cam", "cutscene": cs, "camera": CAMERA}]})
    r = build()
    assert r.exit_code == 0, r.output
    rec = actions()[0]["result"]["roots"]["dir"][0]
    assert rec["camera"] == {"file_id": 56941, "dat": CAMERA}
    assert read(game, CAMERA)[:4] == b"evte"
    import xi.ftable.xi_core as fc
    fc.forget_tables()
    assert (resolve_dat_in_root(game, 56941)[0] or "").upper() == CAMERA
    assert build().exit_code == 0 and actions()[0]["result"]["roots"]["dir"][0]["camera"]["file_id"] == 56941
    undo()
    assert not (game / CAMERA).exists()
    fc.forget_tables()
    assert not resolve_dat_in_root(game, 56941)[0]


def test_camera_needs_a_placement_and_refuses_a_model(game):
    shot = {"keyframes": [{"eye": [0, 0, 5], "look": [0, 0, 0], "fov": 350, "roll": 0, "time": 0}], "duration": 0}
    cs = cutscene(("Look.",), steps=[{"op": "camera", "shotSpec": shot}, {"op": "end"}])
    prepare({"zone": ZONE, "events": [{"name": "cam", "cutscene": cs}]})
    r = build()
    assert r.exit_code != 0 and "give camera" in r.output
    write(game, CAMERA, b"XISM" + bytes(60))
    prepare({"zone": ZONE, "events": [{"name": "cam", "cutscene": cs, "camera": CAMERA}]},
            "jeuno", "--id", "zone_events.events", "--replace")
    r = build()
    assert r.exit_code != 0 and "already holds a non-camera file" in r.output


def test_prepare_a_cutscene_file_merges_into_the_zone_action(game):
    Path("cs").mkdir()
    Path("cs/cast.json").write_text(json.dumps(cutscene()["cast"]), encoding="utf-8")
    one = {**cutscene(("One.",)), "cast": "cast.json", "zone": ZONE}
    Path("cs/one.json").write_text(json.dumps(one), encoding="utf-8")
    Path("cs/two.json").write_text(json.dumps(cutscene(("Two.",), actor=2)), encoding="utf-8")
    from xi.dats.xi_dats import group
    for args in (["cs/one.json"], ["cs/two.json", "--zone", str(ZONE)]):
        r = CliRunner().invoke(group, ["prepare", *args, "--project", "jeuno"], catch_exceptions=False)
        assert r.exit_code == 0, r.output
    (a,) = actions()
    from xi.dats.xi_dats import _zone_slug
    assert a["id"] == f"zone_events.{_zone_slug(ZONE)}" and [e["name"] for e in a["events"]] == ["one", "two"]
    r = build()
    assert r.exit_code == 0, r.output
    assert len(actions()[0]["result"]["roots"]["dir"]) == 2


def test_a_cutscene_for_another_zone_is_refused(game):
    prepare({"zone": ZONE, "events": [{"name": "x", "cutscene": cutscene(zone=243)}]})
    r = build()
    assert r.exit_code != 0 and "the cutscene is for zone 243, not 245" in r.output


def test_old_commands_are_aliases_of_the_action(game, monkeypatch):
    """`xi event cutscene compile` and `xi event dialogue new` prepare + build a zone_events
    action; `xi dats undo` takes both back out."""
    from xi.event import xi_commands as EC
    before = snapshot(game)
    Path("halt.json").write_text(json.dumps(cutscene(("Halt!",), zone=ZONE)), encoding="utf-8")
    r = CliRunner().invoke(EC.cutscene_group, ["compile", "halt.json", "--project", "gate"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    Path("chat.json").write_text(json.dumps(["Hello.", "Bye."]), encoding="utf-8")
    r = CliRunner().invoke(EC.dialogue_group, ["new", str(ZONE), "--json", "chat.json", "--actor", hex(sid(2)),
                                               "--project", "gate"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    (a,) = actions("gate")
    assert [e["name"] for e in a["events"]] == ["halt", "chat"] and len(a["result"]["roots"]["dir"]) == 2
    assert actor(game, sid(2)).event_ids and len(lines(game)) == 5
    undo("gate")
    assert snapshot(game) == before

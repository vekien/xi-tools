"""The ``zone_npcs`` action of ``xi dats`` (xi.entity.xi_zone_npcs): a zone's NPC names renamed
and added in its entity-name table, with the npc_list rows as proposed SQL — on a synthetic game
folder whose FTABLE registers the zone's name and event tables."""
import json
import struct
from pathlib import Path

import pytest
from click.testing import CliRunner

from _minischema import errors, load
from xi.entity import xi_zone_npcs as ZN

ZONE = 245
NAMES, EVENTS = "ROM/27/54.DAT", "ROM/21/54.DAT"        # file ids 6965 / 6065


def sid(local: int) -> int:
    return 0x01000000 | (ZONE << 12) | local


def name_table(rows) -> bytes:
    out = bytearray()
    for name, s in rows:
        rec = bytearray(32)
        enc = name.encode("cp932")
        rec[:len(enc)] = enc
        struct.pack_into("<I", rec, 28, s)
        out += rec
    return bytes(out)


def event_table(actors) -> bytes:
    blocks = [struct.pack("<I", a) + b"\x00" * 12 for a in actors]
    return struct.pack("<I", len(blocks)) + b"".join(struct.pack("<I", len(b)) for b in blocks) + b"".join(blocks)


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
    ft, vt = bytearray(2 * 7000), bytearray(7000)
    for fid, (sub, idx) in {6965: (27, 54), 6065: (21, 54)}.items():
        struct.pack_into("<H", ft, fid * 2, (sub << 7) | idx)
        vt[fid] = 1
    write(root, "FTABLE.DAT", bytes(ft))
    write(root, "VTABLE.DAT", bytes(vt))
    write(root, NAMES, name_table([("none", 0), ("Wolfgang", sid(1)), ("Pherimociel", sid(2)),
                                   ("Retail High", sid(0x380))]))
    write(root, EVENTS, event_table([0x7FFFFFF0, sid(1), sid(0x381)]))
    server = tmp_path / "server" / "scripts" / "enum"
    server.mkdir(parents=True)
    (server / "mod.lua").write_text("xi.mod =\n{\n    HP = 2,\n}\n", encoding="utf-8")
    for mod in (cfg, fc, xe):
        monkeypatch.setattr(mod, "FFXI_DIR", str(root), raising=False)
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", None, raising=False)
    monkeypatch.setattr(xe, "FFXI_PIVOT_DIR", "")
    fc.forget_tables()
    monkeypatch.chdir(tmp_path)
    return root


def names(root: Path) -> list:
    return ZN.parse_names((root / Path(*NAMES.split("/"))).read_bytes())


def prepare(data, project="jeuno", *extra):
    from xi.dats.xi_dats import group
    src = Path("npcs.json")
    src.write_text(json.dumps(data), encoding="utf-8")
    r = CliRunner().invoke(group, ["prepare", str(src), "--project", project, *extra], catch_exceptions=False)
    assert r.exit_code == 0, r.output


def build(project="jeuno", *extra):
    from xi.dats.xi_dats import group
    return CliRunner().invoke(group, ["build", project, *extra], catch_exceptions=False)


def action(project="jeuno") -> dict:
    from xi.dats.xi_dats import _read_manifest
    return _read_manifest(Path(f"projects/{project}.json"))["actions"][0]


def test_schema_example_and_package_entry():
    schema = load("zone_npcs.json")
    for example in schema["examples"]:
        assert ZN.validate_action(example) == [] and errors(schema, example) == []
    assert "zone_npcs.json" in [e.get("$ref") for e in load("package.json")["$defs"]["entry"]["oneOf"]]


def test_validator_names_the_field():
    errs = "\n".join(ZN.validate_action({"id": "zone_npcs.x", "type": "zone_npcs", "zone": ZONE, "npcs": [
        {"id": "auto", "name": "A"},
        {"id": 0x500, "name": "B"},
        {"id": 3, "new": True, "name": "C"},
        {"id": 4, "new": True, "name": "D", "server": {"status": 7, "look": "zz"}},
        {"id": 5},
        {"id": 6, "new": True, "name": "E", "server": {"status": 0}},
    ]}))
    assert "npcs[0]: id auto is for a new NPC" in errs
    assert "npcs[1].id must be the NPC's zone-local id" in errs
    assert "npcs[2]: a new NPC needs its server row" in errs
    assert "npcs[3].server.status must be one of" in errs and "npcs[3].server.look must be" in errs
    assert "npcs[4]: the entry changes nothing" in errs
    assert "npcs[5].server: a new NPC's row needs model" in errs


def test_add_auto_and_rename_then_rebuild_and_undo(game):
    before = (game / NAMES).read_bytes()
    prepare({"zone": ZONE, "npcs": [
        {"id": "auto", "new": True, "name": "Vekien", "server": {"model": 25005, "status": 0, "pos": [1, 2, 3]}},
        {"id": 1, "name": "Wolfgang the Watchful"}]})
    r = build()
    assert r.exit_code == 0, r.output
    got = names(game)
    assert ("Vekien", sid(0x382)) in got                          # 0x380 named, 0x381 an event actor
    assert ("Wolfgang the Watchful", sid(1)) in got
    assert [s for _n, s in got[1:]] == sorted(s for _n, s in got[1:])      # still sorted by id
    sql = Path("projects/jeuno.sql").read_text(encoding="utf-8")
    assert f"REPLACE INTO `npc_list`" in sql and f"({sid(0x382)}, 'Vekien', 'Vekien', 0, 1.000, 2.000, 3.000" in sql
    assert "0x0000AD61" + "0" * 32 in sql                          # look: size 0 + model 25005
    assert f"UPDATE `npc_list` SET `polutils_name` = 'Wolfgang the Watchful' WHERE `npcid` = {sid(1)};" in sql
    assert "`name` = 'Wolfgang" not in sql                         # npc_list.name binds the Lua
    recs = action()["result"]["roots"]["dir"]
    assert {(e["local"], bool(e.get("created"))) for e in recs} == {(0x382, True), (1, False)}

    after = (game / NAMES).read_bytes()
    assert build().exit_code == 0                                  # a rebuild keeps 0x382
    assert (game / NAMES).read_bytes() == after

    from xi.dats.xi_dats import group
    r = CliRunner().invoke(group, ["undo", "jeuno", "--yes"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert (game / NAMES).read_bytes() == before and not Path("projects/jeuno.sql").exists()


@pytest.mark.parametrize("npc, says", [
    ({"id": 0x3A0, "name": "Nobody"}, "has no NPC 0x3a0; mark it \"new\""),
    ({"id": 2, "new": True, "name": "Clash", "server": {"model": 1}}, "already has NPC 0x2 ('Pherimociel')"),
])
def test_build_errors_name_the_npc(game, npc, says):
    prepare({"zone": ZONE, "npcs": [npc]})
    r = build()
    assert r.exit_code != 0 and says in r.output and "npcs[0]" in r.output


def test_prepare_list_merge_and_pivot(game, tmp_path: Path, monkeypatch):
    import xi.xi_config as cfg
    pivot = tmp_path / "pivot"
    pivot.mkdir()
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", str(pivot))
    before = (game / NAMES).read_bytes()
    prepare([{"id": "auto", "new": True, "name": "Vekien", "server": {"model": 25005}}],
            "jeuno", "--type", "zone_npcs", "--zone", "245")
    prepare({"zone": ZONE, "npcs": [{"id": "auto", "new": True, "name": "Vekien", "server": {"model": 30000}},
                                    {"id": 2, "name": "Pheri"}]}, "jeuno", "--id", action()["id"], "--merge")
    a = action()
    assert [n.get("name") for n in a["npcs"]] == ["Vekien", "Pheri"] and a["npcs"][0]["server"]["model"] == 30000
    assert build("jeuno", "--pivot").exit_code == 0
    assert (game / NAMES).read_bytes() == before
    assert ("Pheri", sid(2)) in names(pivot) and ("Vekien", sid(0x382)) in names(pivot)

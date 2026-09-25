"""The ``database`` action of ``xi dats`` (schema/database.json, xi.database.xi_core): the
schema's examples against the hand validator and the schema checker, the name tables
against the schema's enums, the per-side bit encoders, and the validator naming the field.
No install needed; the build side lands with its builder."""
import json
from pathlib import Path

from _minischema import errors, load
from xi.database import xi_core as db

REPO = Path(__file__).resolve().parents[1]
SCHEMA = load("database.json")


def test_schema_examples_validate():
    for example in SCHEMA["examples"]:
        assert db.validate_action(example) == []
        assert errors(SCHEMA, example) == []


def test_include_example_validates():
    inc = load("include.json")
    for example in inc["examples"]:
        assert errors(inc, example) == []
        for action in example["actions"]:
            assert db.validate_action(action) == []


def test_package_entry_takes_includes_and_database_actions():
    entry = load("package.json")["$defs"]["entry"]["oneOf"]
    refs = [e["$ref"] for e in entry]
    assert "common.json#/$defs/includePath" in refs and "database.json" in refs


def test_tables_match_the_registry_and_the_schema():
    assert SCHEMA["$defs"]["edit"]["properties"]["table"]["anyOf"][0]["enum"] == db.tables()
    assert all(subs for subs in db.DMSG_SUBS.values()), "every d_msg table needs its sub-string names"
    assert db.ID_KEYED == {k for k in db.DMSG_SUBS if k[:2] in ("q_", "m_")} | {"keyitems"}


def test_name_tables_match_the_schema_enums():
    d = SCHEMA["$defs"]
    assert d["jobName"]["enum"] == db.JOBS
    assert d["races"]["oneOf"][1]["items"]["enum"] == db.RACES
    assert d["slots"]["items"]["enum"] == db.SLOTS
    assert d["flags"]["items"]["enum"] == db.FLAGS
    assert d["skill"]["oneOf"][0]["enum"] == list(db.SKILLS)
    assert sorted(d["set"]["properties"]) == sorted({f for t in db.ITEM_LAYOUT for f in db.set_fields(t)})


def test_masks_encode_each_side():
    # Butznar Shield (28671), read from the DAT: jobs 0x182, all races, flags 0x0824, Sub.
    assert db.job_mask(["WAR", "PLD", "DRK"]) == 0x182
    assert db.job_mask(["WAR", "PLD", "DRK"], "server") == 0xC1
    assert db.job_mask("all") == 0x7FFFFE and db.job_mask("all", "server") == 4194303   # the SQL's ALL_JOBS
    assert db.race_mask("all") == 0x1FE and db.race_mask(["HUME_M"]) == 0x2
    assert db.flag_mask(["MYSTERY_BOX", "INSCRIBABLE", "CANEQUIP"]) == 0x0824
    assert db.flag_mask(["EX", "RARE"]) == 0xC000
    assert db.slot_mask(["SUB"]) == 0x2 and db.slot_mask(["EAR1", "EAR2"]) == 0x1800
    assert db.skill_id("FISHING") == 48 and db.skill_id(255) == 255


def test_set_fields_follow_the_layout():
    assert {"itemLevel", "shieldSize", "jobs", "races"} <= set(db.set_fields("armor"))
    assert "damage" not in db.set_fields("armor")
    assert {"damage", "delay", "dps", "skill", "baseItemId"} <= set(db.set_fields("weapons"))
    assert db.set_fields("monst1") == ["flags", "stack", "type", "resourceId", "targets", "level", "races",
                                       "instinctCost"]
    assert db.string_names("keyitems", "en") == ["category", "name", "plural", "description"]
    assert db.string_names("armor", "jp") == ["name", "description"]


def test_validator_names_the_field():
    bad = {
        "id": "db.bad", "type": "database", "extra": 1,
        "server": {"emit": "yes", "sql": "out.txt"},
        "edits": [
            {"table": "armour", "id": 1, "set": {"level": 1}},
            {"table": "armor", "id": 28671, "set": {"damage": 5, "jobs": ["WARR"], "level": -1}},
            {"table": "armor", "id": 28671, "strings": {"jp": {"logName": "x"}, "fr": {}}},
            {"table": "keyitems", "id": 1, "set": {"level": 1}, "strings": {"en": {"name": 5}}},
            {"table": "weapons", "id": 16385, "icon": {"from": 1, "png": "a.png"},
             "server": {"item_stuff": {}, "item_equipment": {"MId": 5000, "colour": 1},
                        "item_mods": {"def": 3}, "item_latents": [{"mod": "HP", "value": 1}]}},
            {"table": "titles", "id": 3},
        ],
    }
    errs = db.validate_action(bad)
    text = "\n".join(errs)
    assert "action: unknown key 'extra'" in text
    assert "server.emit must be true or false" in text and "server.sql must be a path ending in .sql" in text
    assert "edits[0].table: 'armour' is not a table" in text
    assert "edits[1].set.damage: table 'armor' (armor layout) has no field 'damage'" in text
    assert "edits[1].set.jobs: 'WARR' is not a job" in text
    assert "edits[1].set.level must be an integer 0-65535" in text
    assert "edits[2]: armor 28671 is already edited by edits[1]" in text
    assert "edits[2].strings.jp: table 'armor' has no jp string 'logName'" in text
    assert "edits[2].strings: unknown language 'fr'" in text
    assert "edits[3].set: only item tables take 'set'" in text
    assert "edits[3].strings.en.name must be a string" in text
    assert "edits[4].icon: unknown key 'png'" in text
    assert "edits[4].server: unknown server table 'item_stuff'" in text
    assert "edits[4].server.item_equipment.MId must be a model id 0-4095" in text
    assert "edits[4].server.item_equipment: unknown column 'colour'" in text
    assert "edits[4].server.item_mods: 'def' is not a mod name" in text
    assert "edits[4].server.item_latents[0]: missing 'latentId'" in text
    assert "edits[5]: the edit changes nothing" in text


def test_validator_takes_every_table_example_shape():
    ok = {"id": "db.ok", "type": "database", "edits": [
        {"table": "keyitems", "id": 1, "strings": {"en": {"category": 2, "plural": "reports"}}},
        {"table": "q_bastok", "id": 5, "strings": {"en": {"description": "…"}, "jp": {"name": "…"}}},
        {"table": "monst1", "id": 29700, "set": {"instinctCost": 3, "races": "all"}},
        {"table": "usable", "id": 4096, "set": {"castTime": 2, "flags": ["CANUSE"]}, "server": False,
         "strings": {"en": {"article": 1}}},
        {"table": "weapons", "id": 16385, "set": {"skill": 255, "jugSize": 3},
         "server": {"item_equipment": {"MId": {"gear": "gear.cesti_plus"}, "jobs": "all", "slot": ["MAIN"]},
                    "item_mods_pet": [{"mod": "ATT", "value": 5, "petType": 1}],
                    "item_info": {"icon": 16385, "desc": None}}},
    ]}
    assert db.validate_action(ok) == []
    assert errors(SCHEMA, ok) == []
    assert json.loads(json.dumps(ok)) == ok

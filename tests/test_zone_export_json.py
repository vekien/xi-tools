"""The ``<stem>.zone.json`` of ``xi zone export --json`` (xi.zone.xi_export.export_zone_json):
schema/zone_export.json's examples against the hand validator (xi.zone.xi_zone_json) and
the schema checker, the validator's shapes against the schema, the database-backed fields
and --zero-coords' fbx_zero_coords, and the validator naming the field. The export of
Lower Jeuno (ROM/1/41) itself is checked with the ``root`` fixture (skipped without it)."""
import copy
import json

from _minischema import errors, load
from xi.zone import xi_zone_json as zj

SCHEMA = load("zone_export.json")
EXAMPLE = SCHEMA["examples"][0]


def _valid(doc) -> None:
    assert zj.validate_zone_json(doc) == []
    assert errors(SCHEMA, doc) == []


def test_schema_examples_validate():
    for example in SCHEMA["examples"]:
        _valid(example)


def test_validator_shapes_match_the_schema():
    d = SCHEMA["$defs"]
    nodes = {"": SCHEMA, "collision": SCHEMA["properties"]["collision"],
             "lod": d["placement"]["properties"]["lod"], **{k: v for k, v in d.items() if "properties" in v}}
    assert set(nodes) == set(zj.SHAPES)
    for name, (required, optional) in zj.SHAPES.items():
        node = nodes[name]
        assert set(node.get("required", [])) == set(required), name
        assert set(node["properties"]) == set(required) | set(optional), name
        assert node["additionalProperties"] is False, name
        assert ("dependentRequired" in node) == (name in zj._ALL_OR_NONE), name
    assert SCHEMA["properties"]["schema"]["const"] == zj.ZONE_JSON_SCHEMA
    assert d["zeroFile"]["properties"]["kind"]["enum"] == list(zj.ZERO_KINDS)


def test_database_fields_validate():
    # music and weather.weights as an export with the server database up writes them
    # (Davoi's, its weights trimmed); the example has {} for both, as without one.
    doc = copy.deepcopy(EXAMPLE)
    doc["music"] = {"day": None, "night": None,
                    "battle_solo": {"id": 115, "title": "Dungeon Battle Theme (Solo)"},
                    "battle_party": {"id": 102, "title": "Dungeon Battle Theme (Party)"}}
    doc["weather"]["weights"] = {"total_days": 2160, "weights": [
        {"id": 0, "name": "None", "tag": None, "in_dat": False,
         "normal_days": 863, "common_days": 863, "rare_days": 863},
        {"id": 1, "name": "Sunshine", "tag": "suny", "in_dat": True,
         "normal_days": 42, "common_days": 270, "rare_days": 270}]}
    _valid(doc)
    doc["companion_dats"] = {}           # no zone_id
    doc["sub_areas"][1]["dat"] = None    # a sub-area id the file tables don't register
    del doc["sub_areas"][1]["placements"]
    _valid(doc)


def test_zero_coords_validate():
    # fbx_zero_coords as export_zone builds it for --zero-coords: a zone file, a sub-area
    # file, and object files whose instances come from _placement_to_fbx — on the zone's
    # records, a sub-area's, a mirrored one and none (a mesh the zone never places).
    from xi.zone.xi_export import Placement, _placement_to_fbx

    def plc(rec, scale=None):
        return Placement(rec["name"], tuple(rec["pos"]), tuple(rec["rot"]), tuple(scale or rec["scale"]),
                         index=rec["index"])

    zone, sub = EXAMPLE["placements"], EXAMPLE["sub_areas"][0]["placements"]
    off = (1.5, -2.0, 0.25)
    files = [
        {"file": "41.fbx", "kind": "zone", "offset": [12.5, -40.0, -3.25]},
        {"file": "41_454.fbx", "kind": "sub_area", "sub_area": 454, "offset": [-81.0, 191.5, 0.0]},
        {"file": "block03.fbx", "kind": "object", "mesh": "block03", "offset": list(off),
         "instances": [_placement_to_fbx(plc(zone[0]), off, False),
                       _placement_to_fbx(plc(zone[1], (-1.0, 1.0, 1.0)), off, False)]},
        {"file": "41_454/ren_barrel02.fbx", "kind": "object", "mesh": "ren_barrel02", "sub_area": 454,
         "offset": list(off), "instances": [_placement_to_fbx(plc(r), off, True) for r in sub]},
        {"file": "lowsea.fbx", "kind": "object", "mesh": "lowsea", "offset": [0.0, 0.0, 0.0],
         "instances": [_placement_to_fbx(None, (0.0, 0.0, 0.0), False)]},
    ]
    doc = dict(EXAMPLE, fbx_zero_coords={"frame": "The FBX as Blender imports it.", "files": files})
    _valid(doc)
    instances = [i for f in files for i in f.get("instances", [])]
    assert [i.get("mirrored") for i in instances] == [None, True, None, None, None]
    assert "placement" not in instances[-1]


def test_validate_names_the_field():
    assert zj.validate_zone_json([]) == ["the zone JSON must be a JSON object"]
    bad = copy.deepcopy(EXAMPLE)
    del bad["zone_name"]
    bad.update(extra=1, schema="xi.zone.v1", mesh_count=99)
    bad["placements"][0]["effect_link"] = "_6t3"
    bad["placements"][1]["flags"] = [0, 0, 256]
    bad["placements"][2]["point_lights"] = [0, 1, 2, 3, 4]
    bad["placements"][3]["file_id_link"] = 0
    bad["music"] = {"day": {"id": 0, "title": None}}
    bad["weather"]["ambient_sounds"][0]["file"] = "se1090.wav"
    bad["weather"]["weights"] = {"total_days": 2160}
    bad["companion_dats"]["npc"] = "27/54.DAT"
    bad["sub_areas"][0]["placements"][0]["pos"] = [0, 0]
    bad["collision"] = {"present": 1}
    bad["fbx_zero_coords"] = {"frame": "", "files": [
        {"file": "41.fbx", "kind": "zone", "offset": [0, 0, 0], "mesh": "x"},
        {"file": "a.fbx", "kind": "object", "offset": [0, 0, 0]},
        {"file": "b.glb", "kind": "sub_area", "offset": [0, 0]},
        {"file": "c.fbx", "kind": "object", "mesh": "c", "offset": [0, 0, 0], "instances": [
            {"matrix": [[1, 0, 0, 0]], "location": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1],
             "mirrored": False}]},
    ]}
    errs = zj.validate_zone_json(bad)
    for says in ("unknown key 'extra'", "missing 'zone_name'", "schema must be 'xi.zone-export.v1'",
                 "mesh_count: is 99, but meshes has 2",
                 "placements[0].effect_link: must be 8 lowercase hex digits or null",
                 "placements[1].flags: must be 4 bytes",
                 "placements[2].point_lights: must have at most 4 entries",
                 "placements[3].file_id_link: must be an integer from 1",
                 "music: must have all of day, night, battle_solo, battle_party",
                 "music.day.id: must be an integer at least 1",
                 "weather.ambient_sounds[0].file: must be se<id>.spw",
                 "weather.weights: must have all of total_days, weights",
                 "companion_dats.npc: must be a ROM path",
                 "sub_areas[0].placements[0].pos: must be a list of 3 numbers",
                 "collision.present: must be true or false",
                 "fbx_zero_coords.files[0]: kind 'zone' has no 'mesh'",
                 "fbx_zero_coords.files[1]: kind 'object' needs 'mesh'",
                 "fbx_zero_coords.files[1]: kind 'object' needs 'instances'",
                 "fbx_zero_coords.files[2].file: must be the .fbx's path",
                 "fbx_zero_coords.files[2].offset: must be a list of 3 numbers",
                 "fbx_zero_coords.files[2]: kind 'sub_area' needs 'sub_area'",
                 "fbx_zero_coords.files[3].instances[0].matrix: must be 4 rows of 4 numbers",
                 "fbx_zero_coords.files[3].instances[0].mirrored: must be true"):
        assert any(says in e for e in errs), (says, errs)
    # A missing key is reported once, not again as a wrong type.
    assert [e for e in errs if "zone_name" in e] == ["missing 'zone_name'"]
    assert errors(SCHEMA, bad)


def test_lower_jeuno_export_validates(root, tmp_path):
    # The real document: ROM/1/41 with its 13 sub-areas, plus fbx_zero_coords as
    # --zero-coords hands it over (the entries themselves are Blender's; any will do).
    from xi.entity.mesh.xi_export import resolve_dat_path
    from xi.zone.xi_export import Placement, _placement_to_fbx, export_zone_json

    block = Placement("block03", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), index=0)
    zero = [{"file": "41.fbx", "kind": "zone", "offset": [0.0, 0.0, 0.0]},
            {"file": "block03.fbx", "kind": "object", "mesh": "block03", "offset": [0.0, 0.0, 0.0],
             "instances": [_placement_to_fbx(block, (0.0, 0.0, 0.0), False)]}]
    out = export_zone_json(resolve_dat_path("ROM/1/41"), tmp_path, zero_files=zero)
    doc = json.loads(out.read_text(encoding="utf-8"))
    _valid(doc)
    assert doc["schema"] == zj.ZONE_JSON_SCHEMA and doc["zone_name"] == "Lower Jeuno"
    assert [sa["id"] for sa in doc["sub_areas"]] == list(range(454, 467))
    assert all(sa.get("placements") for sa in doc["sub_areas"])
    assert doc["fbx_zero_coords"]["files"] == zero

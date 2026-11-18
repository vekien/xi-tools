"""The ``zone_clone`` action (xi.zone.xi_clone, schema/zone_clone.json): a retail zone copied
to a new zone id, recoloured, its four files placed in ROM10 and registered, its name in the
zone-name tables — built into a pivot folder, rebuilt, reset, undone and re-prepared, on a
synthetic game folder (the database tests' install plus zone 107's files)."""
import json
import struct
from pathlib import Path

import pytest
from click.testing import CliRunner

from _minischema import errors, load
from test_dats_database_build import game  # noqa: F401 — the fixture
from test_menu_table import dmsg_table
from test_tex_recolor import _dxt1, _environment
from xi.zone import xi_clone as ZC

ENTRIES = 90_000                     # past every file id a zone up to 430 uses
SOURCE = {"model": "ROM2/0/9.DAT", "event": "ROM/1/1.DAT", "dialog": "ROM/1/2.DAT", "npc": "ROM/1/3.DAT"}
RECIPE = {"name": "Azure Gustaberg", "clone": 107, "zone_id": 422, "hue": 200, "saturation": 30,
          "tint": "#4488ccaa", "blend_mode": "overlay", "textures": {}}


def run(*args):
    from xi.dats.xi_dats import group
    return CliRunner().invoke(group, list(args), catch_exceptions=False)


def action(project="zones", i=0) -> dict:
    from xi.dats.xi_dats import _read_manifest
    return _read_manifest(Path(f"projects/{project}.json"))["actions"][i]


def _tables(root: Path, rom: int, entries: int = ENTRIES) -> None:
    if (root / ("FTABLE.DAT" if rom == 1 else f"ROM{rom}/FTABLE{rom}.DAT")).exists():
        return
    pair = ("FTABLE.DAT", "VTABLE.DAT") if rom == 1 else (f"ROM{rom}/FTABLE{rom}.DAT", f"ROM{rom}/VTABLE{rom}.DAT")
    for rel, n in zip(pair, (2, 1)):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\0" * entries * n)


def _register(root: Path, file_id: int, rel: str) -> None:
    parts = rel.split("/")
    rom = 1 if parts[0] == "ROM" else int(parts[0][3:])
    ft, vt = ((root / "FTABLE.DAT", root / "VTABLE.DAT") if rom == 1 else
              (root / f"ROM{rom}/FTABLE{rom}.DAT", root / f"ROM{rom}/VTABLE{rom}.DAT"))
    f, v = bytearray(ft.read_bytes()), bytearray(vt.read_bytes())
    struct.pack_into("<H", f, file_id * 2, (int(parts[1]) << 7) | int(parts[2].split(".")[0]))
    v[file_id] = rom
    ft.write_bytes(f)
    vt.write_bytes(v)


def _resolve(root: Path, file_id: int, rom: int = 10):
    from xi.ftable.xi_core import resolve_dat, root_table_pair
    ft, vt = (Path(p) for p in root_table_pair(root, rom))
    return resolve_dat(ft.read_bytes(), vt.read_bytes(), file_id)[0]


def _name(root: Path, rel: str, row: int) -> str:
    from xi.common import xi_dmsg as D
    return D.get_text(D.parse((root / rel).read_bytes()).blocks[row], 0)


@pytest.fixture
def zones(game: Path, tmp_path: Path, monkeypatch) -> Path:
    """Zone 107 in the install (registered in its main tables), and a pivot folder with
    ROM10 tables and the zone-name tables grown to 512 rows."""
    import xi.xi_config as cfg
    from xi.ftable import xi_core as FC
    _tables(game, 1)
    _tables(game, 2)                       # the model in ROM2/, as Dragon's Aery's is
    for kind, rel in SOURCE.items():
        p = game / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(_dxt1("suny    sky01", 0xF800, 0x001F) + _dxt1("wall    w01", 0x07E0, 0x001F) + _environment()
                      if kind == "model" else f"retail zone 107 {kind}".encode())
        _register(game, ZC.file_ids(107)[kind], rel)
    pivot = tmp_path / "pivot"
    _tables(pivot, 10)
    for rel in ZC.NAME_TABLES.values():
        p = pivot / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(dmsg_table(["."] * 512, 64))
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", str(pivot))
    FC.forget_tables() if hasattr(FC, "forget_tables") else None
    return pivot


def test_schema_examples_validate():
    schema = load("zone_clone.json")
    for example in schema["examples"]:
        assert ZC.validate_action(example) == [] and errors(schema, example) == []
    assert any(e.get("$ref") == "zone_clone.json" for e in load("package.json")["$defs"]["entry"]["oneOf"])
    assert errors({**schema["$defs"]["recipe"], "$defs": schema["$defs"]}, RECIPE) == []


def test_validator_names_the_field():
    errs = ZC.validate_action({"id": "zone_clone.x", "type": "zone_clone", "source": {"zone": "107"},
                               "target": {"zone_id": -1, "slot": 200}, "names": {"en": ""},
                               "recolour": {"hue": "lots", "blend": "burn", "colour": 1,
                                            "texture_overrides": {"suny*": {"fog_end": 3}}}})
    assert "source must be {\"zone\": <the retail zone id to copy>}" in errs
    assert "target.zone_id must be a zone id or \"auto\"" in errs
    assert "target.slot must be the first of four free slots, 0-124" in errs
    assert "names must be {\"en\": <the zone's name>} (and optionally \"jp\")" in errs
    assert "recolour.hue must be a number" in errs and "recolour: unknown key 'colour'" in errs
    assert "recolour.blend must be one of normal, multiply, screen, overlay, add" in errs
    assert "recolour.texture_overrides['suny*']: unknown key 'fog_end'" in errs


def test_a_recipe_becomes_an_action():
    a = ZC.action_from_recipe(dict(RECIPE, textures={"environment": {"fog_end": 80}}))
    assert a == {"id": "zone_clone.azure_gustaberg", "type": "zone_clone", "source": {"zone": 107},
                 "target": {"zone_id": 422}, "names": {"en": "Azure Gustaberg"},
                 "recolour": {"hue": 200, "saturation": 30, "tint": "#4488ccaa", "blend": "overlay",
                              "texture_overrides": {"environment": {"fog_end": 80}}}}
    assert ZC.validate_action(a) == []
    with pytest.raises(ZC.CloneError):
        ZC.action_from_recipe({"name": "x", "clone": 107, "colour": "red"})


def test_build_rebuild_dry_run_and_undo(zones, game, tmp_path):
    recipe = tmp_path / "Azure_Gustaberg.json"
    recipe.write_text(json.dumps(RECIPE), encoding="utf-8")
    r = run("prepare", str(recipe), "--project", "zones")
    assert r.exit_code == 0, r.output
    assert action()["type"] == "zone_clone"
    r = run("build", "zones", "--pivot", "--dry-run")
    assert r.exit_code == 0, r.output
    assert not (zones / "ROM10" / "1").exists() and _name(zones, "ROM/165/84.DAT", 422) == "."
    r = run("build", "zones", "--pivot")
    assert r.exit_code == 0, r.output
    fids = ZC.file_ids(422)
    for i, kind in enumerate(ZC.KINDS):
        assert _resolve(zones, fids[kind]) == f"ROM10/1/{i}.DAT"
        placed = (zones / "ROM10" / "1" / f"{i}.DAT").read_bytes()
        if kind == "model":
            assert placed != (game / SOURCE["model"]).read_bytes()          # recoloured
            assert len(placed) == len((game / SOURCE["model"]).read_bytes())
        else:
            assert placed == (game / SOURCE[kind]).read_bytes()            # as they are
    assert _name(zones, "ROM/165/84.DAT", 422) == "Azure Gustaberg" == _name(zones, "ROM/165/63.DAT", 422)
    res = action()["result"]
    assert res["zone_id"] == 422 and res["targets"] == ["pivot"]
    assert [p["dat"] for p in res["placements"]] == [f"ROM10/1/{i}.DAT" for i in range(4)]
    assert res["names"]["en"] == {"dat": "ROM/165/84.DAT", "row": 422, "from": ".", "to": "Azure Gustaberg"}
    snapshot = {p: p.read_bytes() for p in zones.rglob("*") if p.is_file()}
    assert run("build", "zones", "--pivot").exit_code == 0
    assert {p: p.read_bytes() for p in zones.rglob("*") if p.is_file()} == snapshot      # a rebuild: the same
    log = run("changelog", "zones").output
    assert "zone 422 model" in log and "zone 422 name 'Azure Gustaberg'" in log
    r = run("undo", "zones", "--yes")
    assert r.exit_code == 0, r.output
    assert not any((zones / "ROM10" / "1").glob("*.DAT"))
    assert all(_resolve(zones, f) is None for f in fids.values())
    assert _name(zones, "ROM/165/84.DAT", 422) == "." == _name(zones, "ROM/165/63.DAT", 422)


def test_auto_ids_slots_and_a_re_prepare_keep_the_allocation(zones, tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps({"name": "First Copy", "clone": 107}), encoding="utf-8")
    b.write_text(json.dumps({"name": "Second Copy", "clone": 107, "hue": 90}), encoding="utf-8")
    assert run("prepare", str(a), "--project", "zones").exit_code == 0
    assert run("prepare", str(b), "--project", "zones", "--subdir", "2").exit_code == 0
    r = run("build", "zones", "--pivot")
    assert r.exit_code == 0, r.output
    first, second = action(i=0)["result"], action(i=1)["result"]
    assert (first["zone_id"], second["zone_id"]) == (400, 401)
    assert [p["dat"] for p in second["placements"]] == [f"ROM10/2/{i}.DAT" for i in range(4)]
    # re-prepare (a changed hue): the zone id and slots the build took survive
    b.write_text(json.dumps({"name": "Second Copy", "clone": 107, "hue": 45}), encoding="utf-8")
    assert run("prepare", str(b), "--project", "zones", "--replace").exit_code == 0
    assert action(i=1)["result"]["zone_id"] == 401 and action(i=1)["recolour"] == {"hue": 45}
    assert run("build", "zones", "--pivot").exit_code == 0
    assert [p["dat"] for p in action(i=1)["result"]["placements"]] == [f"ROM10/2/{i}.DAT" for i in range(4)]


def test_slots_taken_in_rom10_are_skipped(zones, tmp_path):
    _register(zones, 500, "ROM10/1/0.DAT")
    _register(zones, 501, "ROM10/1/2.DAT")
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"name": "Copy", "clone": 107, "zone_id": 410}), encoding="utf-8")
    assert run("prepare", str(a), "--project", "zones").exit_code == 0
    assert run("build", "zones", "--pivot").exit_code == 0
    assert [p["dat"] for p in action()["result"]["placements"]] == [
        "ROM10/1/1.DAT", "ROM10/1/3.DAT", "ROM10/1/4.DAT", "ROM10/1/5.DAT"]


def test_an_ungrown_name_table_is_refused(zones, tmp_path):
    (zones / "ROM/165/84.DAT").write_bytes(dmsg_table(["."] * 300, 64))
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"name": "Copy", "clone": 107, "zone_id": 410}), encoding="utf-8")
    assert run("prepare", str(a), "--project", "zones").exit_code == 0
    r = run("build", "zones", "--pivot")
    assert r.exit_code != 0 and "xi database grow ROM/165/84.DAT 512" in r.output


def test_reset_takes_the_names_back_to_the_install_then_rebuilds(zones, game, tmp_path):
    """As CatsEyeXI's CI has it: the zone-name tables grown in the install, the build into the
    pivot folder with --reset."""
    for rel in ZC.NAME_TABLES.values():
        p = game / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(dmsg_table(["."] * 512, 64))
    recipe = tmp_path / "a.json"
    recipe.write_text(json.dumps(RECIPE), encoding="utf-8")
    assert run("prepare", str(recipe), "--project", "zones").exit_code == 0
    assert run("build", "zones", "--pivot").exit_code == 0
    built = (zones / "ROM/165/84.DAT").read_bytes()
    (zones / "ROM/165/84.DAT").write_bytes(dmsg_table(["stale"] * 512, 64))
    r = run("build", "zones", "--pivot", "--reset")
    assert r.exit_code == 0, r.output
    assert (zones / "ROM/165/84.DAT").read_bytes() == built
    assert _name(zones, "ROM/165/84.DAT", 0) == "."                       # the install's row, not "stale"


def test_zone_inject_is_prepare_and_build(zones):
    from xi.zone.xi_inject import cmd
    r = CliRunner().invoke(cmd, ["Crimson Copy", "--clone", "107", "--hue", "240", "--zone-id", "420",
                                 "--texture", "suny*:lightness=-70", "--pivot"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    a = action()
    assert a["id"] == "zone_clone.crimson_copy" and a["source"] == {"zone": 107}
    assert a["recolour"] == {"hue": 240.0, "texture_overrides": {"suny*": {"lightness": -70}}}
    assert a["result"]["zone_id"] == 420 and _resolve(zones, ZC.file_ids(420)["model"]) == "ROM10/1/0.DAT"
    assert "--model-dat is no longer taken" in CliRunner().invoke(
        cmd, ["X", "--clone", "107", "--model-dat", "ROM/0/1.DAT"]).output

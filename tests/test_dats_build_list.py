"""``xi dats build --list`` and ``--reset`` (xi.dats.xi_build_list, schema/build_list.json):
the projects of a build list built first to last, and the tables they edit reset from their
``.base`` first when asked — on the synthetic game folder of test_dats_database_build."""
import json
from pathlib import Path

from click.testing import CliRunner

import test_dats_database_build as T
from _minischema import errors, load
from test_dats_database_build import ARMOR_EN, KI_EN, game  # noqa: F401 — the fixture
from xi.database import xi_build as DB
from xi.dats import xi_build_list as BL
from xi.ui.items.xi_layout import read_field


def build_list(*projects, path=BL.DEFAULT_PATH) -> Path:
    path = Path(path)
    doc = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"schema": BL.LIST_SCHEMA}
    doc["projects"] = list(projects)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def build(*args):
    from xi.dats.xi_dats import group
    return CliRunner().invoke(group, ["build", *args], catch_exceptions=False)


def level(root: Path, idx: int = 1) -> int:
    return read_field(T.record(root, ARMOR_EN, idx), "armor", "legacy", "level")


def key_item(root: Path, kid: int) -> str:
    from xi.common import xi_dmsg as D
    t = D.parse((root / Path(*KI_EN.split("/"))).read_bytes())
    return DB.sub_value(DB._block_subs(t.blocks[DB.locate_block(t, "keyitems", kid)])[4])


def recorded() -> list[str]:
    return BL.recorded(json.loads(BL.DEFAULT_PATH.read_text()), "dir")


def test_schema_example_validates_and_the_list_is_not_a_project():
    schema = load("build_list.json")
    for example in schema["examples"]:
        assert BL.validate(example) == [] and errors(schema, example) == []
    assert BL.validate({"schema": BL.LIST_SCHEMA, "projects": ["a", "a"], "x": 1}) == [
        "unknown key 'x'", "projects[1]: 'a' is listed twice"]
    assert BL.validate({"schema": BL.LIST_SCHEMA, "projects": []}) == [
        "projects must be a non-empty list of project names or paths"]
    assert BL.list_path(None) == Path("projects/build_list.json")
    assert BL.list_path("release") == Path("projects/release.json")


def test_projects_layer_in_the_list_order_and_reset_makes_it_repeatable(game):
    from xi.dats.xi_dats import _existing_project_names
    T.prepare([{"table": "armor", "id": 10241, "set": {"level": 50}}], "a")
    T.prepare([{"table": "armor", "id": 10241, "set": {"level": 60}}], "b")
    build_list("a", "b")
    assert _existing_project_names() == ["a", "b"]                      # the list file isn't one
    r = build("--list")
    assert r.exit_code == 0, r.output
    assert level(game) == 60 and "from .base" not in r.output           # nothing reset without --reset
    assert recorded() == ["ROM/0/7.DAT", "ROM/118/109.DAT"]
    build_list("b", "a")
    r = build("--list", "--reset")
    assert r.exit_code == 0, r.output
    assert level(game) == 50 and "Reset 2 tables from .base" in r.output
    assert build("--list", "--reset").exit_code == 0 and level(game) == 50   # the same every time


def test_a_named_list_and_a_list_given_as_a_project(game):
    T.prepare([{"table": "armor", "id": 10241, "set": {"level": 50}}], "a")
    build_list("a", path="projects/release.json")
    r = build("release", "--list")
    assert r.exit_code == 0, r.output
    assert level(game) == 50
    T.prepare([{"table": "armor", "id": 10241, "set": {"level": 55}}], "a", "--replace")
    r = build("projects/release.json")                      # a list file says what it is: no --list
    assert r.exit_code == 0 and "Building the build list" in r.output, r.output
    assert level(game) == 55
    r = build("release", "--list", "--only", "database.edits")
    assert r.exit_code != 0 and "--only doesn't go with it" in r.output
    r = build("projects/release.json", "--only", "database.edits")
    assert r.exit_code != 0 and "--only doesn't go with it" in r.output


def test_a_project_taken_off_the_list_goes_back_on_a_reset(game):
    T.prepare([{"table": "armor", "id": 10241, "set": {"level": 50}}], "a")
    T.prepare([{"table": "keyitems", "id": 2, "strings": {"en": {"name": "New map"}}}], "c")
    build_list("a", "c")
    assert build("--list").exit_code == 0 and key_item(game, 2) == "New map"
    build_list("a")
    assert build("--list").exit_code == 0 and key_item(game, 2) == "New map"   # no reset: it stays
    assert "ROM/175/35.DAT" in recorded()                                    # and is still remembered
    r = build("--list", "--reset")
    assert r.exit_code == 0, r.output
    assert key_item(game, 2) == "Palborough map" and level(game) == 50
    assert recorded() == ["ROM/0/7.DAT", "ROM/118/109.DAT"]


def test_nothing_is_touched_when_a_project_is_wrong(game):
    T.prepare([{"table": "armor", "id": 10241, "set": {"level": 50}}], "a")
    assert build("--list").exit_code != 0                               # no build list yet
    build_list("a")
    assert build("--list").exit_code == 0
    data = (game / ARMOR_EN).read_bytes()
    Path("projects/bad.json").write_text(json.dumps({"actions": [
        {"id": "database.bad", "type": "database", "edits": [{"table": "armour", "id": 1, "set": {"level": 1}}]}]}))
    build_list("a", "bad", "missing")
    r = build("--list", "--reset")
    assert r.exit_code != 0 and "nothing was built" in r.output
    assert "bad: database.bad: edits[0].table: 'armour' is not a table" in r.output
    assert "missing: no project file at projects/missing.json" in r.output
    assert (game / ARMOR_EN).read_bytes() == data


def test_dry_run_writes_nothing(game):
    T.prepare([{"table": "armor", "id": 10241, "set": {"level": 50}}], "a")
    build_list("a")
    assert build("--list").exit_code == 0
    T.prepare([{"table": "armor", "id": 10241, "set": {"level": 70}}], "a", "--replace")
    data = (game / ARMOR_EN).read_bytes()
    r = build("--list", "--reset", "--dry-run")
    assert r.exit_code == 0 and "Would reset 2 tables" in r.output and "Dry run" in r.output
    assert (game / ARMOR_EN).read_bytes() == data
    r = build("a", "--reset", "--dry-run")
    assert r.exit_code == 0 and "Would reset 2 tables" in r.output
    assert (game / ARMOR_EN).read_bytes() == data


def test_reset_of_one_action_rebuilds_the_actions_that_share_its_tables(game):
    from xi.dats.xi_dats import _read_manifest
    T.prepare([{"table": "armor", "id": 10241, "set": {"level": 50}}], "p", "--id", "database.one")
    T.prepare([{"table": "armor", "id": 10242, "set": {"level": 75}}], "p", "--id", "database.two")
    assert build("p").exit_code == 0 and (level(game, 1), level(game, 2)) == (50, 75)
    T.prepare([{"table": "armor", "id": 10241, "set": {"level": 51}}], "p", "--id", "database.one", "--replace")
    r = build("p", "--only", "database.one", "--reset")
    assert r.exit_code == 0, r.output
    assert "database.two" in r.output and "(shares a reset table)" in r.output
    assert (level(game, 1), level(game, 2)) == (51, 75)
    one = next(a for a in _read_manifest(Path("projects/p.json"))["actions"] if a["id"] == "database.one")
    assert [e["changed"]["level"] for e in one["result"]["roots"]["dir"] if e["lang"] == "en"] == [
        {"from": 1, "to": 51}]                                          # reset: this build's records only


def test_a_pivot_reset_starts_from_the_install(game, tmp_path: Path, monkeypatch):
    import xi.xi_config as cfg
    pivot = tmp_path / "pivot"
    pivot.mkdir()
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", str(pivot))
    T.prepare([{"table": "keyitems", "id": 2, "strings": {"en": {"name": "Pivot map"}}}], "c")
    build_list("c")
    assert build("--list", "--pivot").exit_code == 0
    copy = pivot / Path(*KI_EN.split("/"))
    assert key_item(pivot, 2) == "Pivot map" and not copy.with_name(copy.name + ".base").exists()
    assert key_item(game, 2) == "Palborough map"                        # the install is left alone
    assert not (pivot / Path(*T.KI_JP.split("/"))).exists()              # nor is the JP table copied
    # The install edited in place: a pivot reset takes its .base, not the edited file.
    T.prepare([{"table": "keyitems", "id": 2, "strings": {"en": {"name": "Install map"}}}], "d")
    assert build("d").exit_code == 0 and key_item(game, 2) == "Install map"
    r = build("--list", "--reset", "--pivot")
    assert r.exit_code == 0, r.output
    assert "Reset 1 table from the install (FFXI_DIR)" in r.output
    assert key_item(pivot, 2) == "Pivot map" and key_item(game, 2) == "Install map"
    # A project taken off the list: its table goes back to the install's untouched copy.
    Path("projects/empty.json").write_text(json.dumps({"actions": []}))
    build_list("empty")
    assert build("--list", "--reset", "--pivot").exit_code == 0
    assert key_item(pivot, 2) == "Palborough map"


def test_a_reset_resets_what_a_definition_names_before_its_first_build(game, tmp_path: Path, monkeypatch):
    import xi.xi_config as cfg
    pivot = tmp_path / "pivot"
    pivot.mkdir()
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", str(pivot))
    # The pivot folder holds an older build of the table, and neither the project nor the
    # list has a recorded result (a fresh clone): --reset still starts it from the install.
    T.prepare([{"table": "armor", "id": 10242, "set": {"level": 77}}], "old")
    assert build("old", "--pivot").exit_code == 0 and level(pivot, 2) == 77
    T.prepare([{"table": "armor", "id": 10241, "set": {"level": 50}}], "a")
    build_list("a")
    r = build("--list", "--reset", "--pivot")
    assert r.exit_code == 0, r.output
    assert "Reset 2 tables from the install (FFXI_DIR)" in r.output       # armor, EN and JP
    assert level(pivot) == 50 and level(pivot, 2) == level(game, 2) == 90

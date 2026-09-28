"""``xi dats build --all`` and the build order (xi.dats.xi_order, schema/build_order.json):
projects built first to last after every table they edit is reset from its ``.base`` — on
the synthetic game folder of test_dats_database_build."""
import json
from pathlib import Path

from click.testing import CliRunner

import test_dats_database_build as T
from _minischema import errors, load
from test_dats_database_build import ARMOR_EN, KI_EN, game  # noqa: F401 — the fixture
from xi.database import xi_build as DB
from xi.dats import xi_order as O
from xi.ui.items.xi_layout import read_field


def order(*projects, path=O.DEFAULT_PATH) -> Path:
    path = Path(path)
    doc = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"schema": O.ORDER_SCHEMA}
    doc["projects"] = list(projects)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def build_all(*extra):
    from xi.dats.xi_dats import group
    return CliRunner().invoke(group, ["build", "--all", *extra], catch_exceptions=False)


def level(root: Path) -> int:
    return read_field(T.record(root, ARMOR_EN, 1), "armor", "legacy", "level")


def key_item(root: Path, kid: int) -> str:
    from xi.common import xi_dmsg as D
    t = D.parse((root / Path(*KI_EN.split("/"))).read_bytes())
    return DB.sub_value(DB._block_subs(t.blocks[DB.locate_block(t, "keyitems", kid)])[4])


def test_schema_example_validates_and_the_order_is_not_a_project():
    schema = load("build_order.json")
    for example in schema["examples"]:
        assert O.validate(example) == [] and errors(schema, example) == []
    assert O.validate({"schema": O.ORDER_SCHEMA, "projects": ["a", "a"], "x": 1}) == [
        "unknown key 'x'", "projects[1]: 'a' is listed twice"]
    assert O.validate({"schema": O.ORDER_SCHEMA, "projects": []}) == [
        "projects must be a non-empty list of project names or paths"]


def test_projects_on_one_table_layer_in_the_order_given(game):
    from xi.dats.xi_dats import _existing_project_names
    T.prepare([{"table": "armor", "id": 10241, "set": {"level": 50}}], "a")
    T.prepare([{"table": "armor", "id": 10241, "set": {"level": 60}}], "b")
    order("a", "b")
    assert _existing_project_names() == ["a", "b"]                      # the order file isn't one
    r = build_all()
    assert r.exit_code == 0, r.output
    assert level(game) == 60 and "Reset 0 table(s)" in r.output
    assert O.recorded(json.loads(O.DEFAULT_PATH.read_text()), "dir") == ["ROM/0/7.DAT", "ROM/118/109.DAT"]
    order("b", "a")
    r = build_all()
    assert r.exit_code == 0, r.output
    assert level(game) == 50 and "Reset 2 table(s)" in r.output
    assert build_all().exit_code == 0 and level(game) == 50           # the same every time


def test_a_project_taken_off_the_order_goes_back(game):
    T.prepare([{"table": "armor", "id": 10241, "set": {"level": 50}}], "a")
    T.prepare([{"table": "keyitems", "id": 2, "strings": {"en": {"name": "New map"}}}], "c")
    order("a", "c")
    assert build_all().exit_code == 0 and key_item(game, 2) == "New map"
    order("a")
    r = build_all()
    assert r.exit_code == 0, r.output
    assert key_item(game, 2) == "Palborough map" and level(game) == 50
    assert O.recorded(json.loads(O.DEFAULT_PATH.read_text()), "dir") == ["ROM/0/7.DAT", "ROM/118/109.DAT"]


def test_nothing_is_touched_when_a_project_is_wrong(game):
    T.prepare([{"table": "armor", "id": 10241, "set": {"level": 50}}], "a")
    assert build_all().exit_code != 0                                   # no build order yet
    order("a")
    assert build_all().exit_code == 0
    data = (game / ARMOR_EN).read_bytes()
    Path("projects/bad.json").write_text(json.dumps({"actions": [
        {"id": "database.bad", "type": "database", "edits": [{"table": "armour", "id": 1, "set": {"level": 1}}]}]}))
    order("a", "bad", "missing")
    r = build_all()
    assert r.exit_code != 0 and "nothing was built" in r.output
    assert "bad: database.bad: edits[0].table: 'armour' is not a table" in r.output
    assert "missing: no project file at projects/missing.json" in r.output
    assert (game / ARMOR_EN).read_bytes() == data


def test_dry_run_and_the_flags_it_refuses(game):
    T.prepare([{"table": "armor", "id": 10241, "set": {"level": 50}}], "a")
    order("a")
    assert build_all().exit_code == 0
    T.prepare([{"table": "armor", "id": 10241, "set": {"level": 70}}], "a", "--replace")
    data = (game / ARMOR_EN).read_bytes()
    r = build_all("--dry-run")
    assert r.exit_code == 0 and "Would reset 2 table(s)" in r.output and "Dry run" in r.output
    assert (game / ARMOR_EN).read_bytes() == data
    from xi.dats.xi_dats import group
    r = CliRunner().invoke(group, ["build", "a", "--all"])
    assert r.exit_code != 0 and "give no project" in r.output


def test_pivot_copies_a_build_made_are_taken_out(game, tmp_path: Path, monkeypatch):
    import xi.xi_config as cfg
    pivot = tmp_path / "pivot"
    pivot.mkdir()
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", str(pivot))
    T.prepare([{"table": "keyitems", "id": 2, "strings": {"en": {"name": "Pivot map"}}}], "c")
    order("c")
    assert build_all("--pivot").exit_code == 0
    copy = pivot / Path(*KI_EN.split("/"))
    assert key_item(pivot, 2) == "Pivot map" and copy.with_name(copy.name + ".base").read_bytes() == b""
    assert key_item(game, 2) == "Palborough map"                        # the install is left alone
    Path("projects/empty.json").write_text(json.dumps({"actions": []}))
    order("empty")
    r = build_all("--pivot")
    assert r.exit_code == 0, r.output
    assert "1 pivot copies taken out" in r.output and not copy.exists()

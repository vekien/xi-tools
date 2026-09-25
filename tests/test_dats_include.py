"""Includes in a ``xi dats`` project (xi.dats.xi_include): a string in ``actions`` names a
``xi.dats.include.v1`` file whose actions are spliced in at that spot. Reading flattens the
project, a write puts every action back in the file it came from, and a real build (the
spell record action on the synthetic game folder) records its result in the include file."""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from xi.dats import xi_include as inc

from test_dats_records import game  # noqa: F401  (fixture)


def _write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _include(*actions) -> dict:
    return {"schema": inc.INCLUDE_SCHEMA, "actions": list(actions)}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """projects/p.json: [include a.json, inline x] ; a.json: [a1, include b.json, a2] ; b.json: [b1]."""
    root = tmp_path / "projects"
    _write(root / "p" / "b.json", _include({"id": "b1", "type": "gear"}))
    _write(root / "p" / "a.json", _include({"id": "a1", "type": "gear"}, "b.json", {"id": "a2", "type": "mesh"}))
    return _write(root / "p.json", {"schema": "xi.dats.v1", "name": "p",
                                     "actions": ["p/a.json", {"id": "x", "type": "mount"}]})


def test_flatten_splices_in_order(project: Path):
    data = json.loads(project.read_text(encoding="utf-8"))
    flat, layout = inc.flatten(project, data["actions"])
    assert [a["id"] for a in flat] == ["a1", "b1", "a2", "x"]
    assert set(layout["owners"]) == {"a1", "a2", "b1"}
    assert Path(layout["owners"]["b1"]).name == "b.json"
    assert json.loads(json.dumps(layout)) == layout          # plain JSON


def test_write_puts_each_action_back(project: Path):
    from xi.dats.xi_dats import _read_manifest, _write_manifest
    b_before = (project.parent / "p" / "b.json").read_text(encoding="utf-8")
    m = _read_manifest(project)
    by_id = {a["id"]: a for a in m["actions"]}
    by_id["a2"]["result"] = {"placed": True}
    by_id["x"]["result"] = {"placed": True}
    m["actions"].append({"id": "new", "type": "gear"})
    _write_manifest(project, m)

    top = json.loads(project.read_text(encoding="utf-8"))
    assert top["actions"][0] == "p/a.json"                      # the include string stays put
    assert [a["id"] for a in top["actions"][1:]] == ["x", "new"]  # a new action goes in the project file
    assert top["actions"][1]["result"] == {"placed": True}
    assert "_include_layout" not in top
    a = json.loads((project.parent / "p" / "a.json").read_text(encoding="utf-8"))
    assert a["schema"] == inc.INCLUDE_SCHEMA
    assert a["actions"][0]["id"] == "a1" and a["actions"][1] == "b.json"
    assert a["actions"][2] == {"id": "a2", "type": "mesh", "result": {"placed": True}}
    # an include whose actions didn't change is not rewritten
    assert (project.parent / "p" / "b.json").read_text(encoding="utf-8") == b_before
    # and the whole thing reads back the same
    assert [x["id"] for x in _read_manifest(project)["actions"]] == ["a1", "b1", "a2", "x", "new"]


def test_a_dropped_included_action_leaves_its_file(project: Path):
    from xi.dats.xi_dats import _read_manifest, _write_manifest
    m = _read_manifest(project)
    m["actions"] = [a for a in m["actions"] if a["id"] != "a1"]
    _write_manifest(project, m)
    a = json.loads((project.parent / "p" / "a.json").read_text(encoding="utf-8"))
    assert a["actions"][0] == "b.json" and a["actions"][1]["id"] == "a2"


def test_new_actions_go_after_a_trailing_include(tmp_path: Path):
    from xi.dats.xi_dats import _read_manifest, _write_manifest
    _write(tmp_path / "q" / "tail.json", _include({"id": "t1", "type": "gear"}))
    p = _write(tmp_path / "q.json", {"schema": "xi.dats.v1", "name": "q",
                                     "actions": [{"type": "zone"}, {"id": "x", "type": "gear"}, "q/tail.json"]})
    m = _read_manifest(p)
    assert [a.get("id") for a in m["actions"]] == [None, "x", "t1"]
    m["actions"].append({"id": "new", "type": "gear"})
    _write_manifest(p, m)
    top = json.loads(p.read_text(encoding="utf-8"))["actions"]
    assert top[0] == {"type": "zone"} and top[1]["id"] == "x" and top[2] == "q/tail.json" and top[3]["id"] == "new"


@pytest.mark.parametrize("setup, says", [
    (lambda r: _write(r / "p" / "a.json", _include("a.json")), "include cycle: p.json -> a.json -> a.json"),
    (lambda r: _write(r / "p" / "a.json", _include({"id": "x", "type": "gear"})), "action id 'x' is in both"),
    (lambda r: (r / "p" / "a.json").unlink(), "include not found"),
    (lambda r: _write(r / "p" / "a.json", {"actions": []}), '"schema": "xi.dats.include.v1"'),
    (lambda r: _write(r / "p" / "a.json", _include({"type": "gear"})), "an included action needs an id"),
    (lambda r: _write(r / "p.json", {"schema": "xi.dats.v1", "name": "p", "actions": ["p/a.txt"]}),
     "is not a .json path"),
])
def test_bad_includes_are_named(project: Path, setup, says):
    import click
    from xi.dats.xi_dats import _read_manifest
    setup(project.parent)
    with pytest.raises(click.ClickException) as exc:
        _read_manifest(project)
    assert says in str(exc.value.message)


def test_scanners_see_included_actions(project: Path, monkeypatch):
    from xi.dats.xi_dats import _existing_project_names
    monkeypatch.chdir(project.parent.parent)
    assert [a["id"] for a in inc.project_actions(project)] == ["a1", "b1", "a2", "x"]
    _write(project.parent / "stray.json", _include({"id": "s", "type": "gear"}))   # misplaced include
    assert inc.project_actions(project.parent / "stray.json") == []
    assert _existing_project_names() == ["p"]


def test_build_records_the_result_in_the_include_file(game):
    from xi.dats.xi_dats import _read_manifest, group
    root, sp, cp = game
    runner = CliRunner()
    for src in (sp, cp):
        assert runner.invoke(group, ["prepare", str(src), "--project", "fs", "--replace"],
                             catch_exceptions=False).exit_code == 0
    # Move the spell into projects/fs/records.json, included from the project.
    top = json.loads(Path("projects/fs.json").read_text(encoding="utf-8"))
    spell, command = top["actions"]
    _write(Path("projects/fs/records.json"), _include(spell))
    top["actions"] = ["fs/records.json", command]
    _write(Path("projects/fs.json"), top)

    r = runner.invoke(group, ["build", "fs"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    included = json.loads(Path("projects/fs/records.json").read_text(encoding="utf-8"))["actions"][0]
    assert included["id"] == "spell.testspell" and included["result"]["record_id"] == 4095
    top = json.loads(Path("projects/fs.json").read_text(encoding="utf-8"))
    assert top["actions"][0] == "fs/records.json"
    assert top["actions"][1]["id"] == "command.war_cry" and top["actions"][1]["result"]["record_id"] == 4095
    assert [a["id"] for a in _read_manifest(Path("projects/fs.json"))["actions"]] == \
        ["spell.testspell", "command.war_cry"]

    r = runner.invoke(group, ["json", "projects/fs.json"], catch_exceptions=False)
    printed = json.loads(r.output)
    assert "_include_layout" not in printed and printed["actions"][0]["id"] == "spell.testspell"

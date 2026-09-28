"""The ``copy`` action (xi.dats.xi_copy, schema/copy.json): a file put into the install or
the pivot folder as it is, and taken back out by ``undo`` — on the synthetic game folder of
test_dats_database_build."""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from _minischema import errors, load
from test_dats_database_build import game  # noqa: F401 — the fixture
from xi.dats import xi_copy as CP

SHEET = "ROM/119/51.DAT"


def run(*args):
    from xi.dats.xi_dats import group
    return CliRunner().invoke(group, list(args), catch_exceptions=False)


def action(project="ui") -> dict:
    from xi.dats.xi_dats import _read_manifest
    return _read_manifest(Path(f"projects/{project}.json"))["actions"][0]


@pytest.fixture
def sheet(game: Path, tmp_path: Path) -> Path:
    """A redrawn UI sheet kept in a repo laid out like the game (rom/ROM/119/51.DAT), over
    the install's own."""
    (game / "ROM" / "119").mkdir(parents=True, exist_ok=True)
    (game / "ROM" / "119" / "51.DAT").write_bytes(b"retail sheet")
    src = tmp_path / "repo" / "rom" / "ROM" / "119" / "51.DAT"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"redrawn sheet")
    return src


def test_schema_example_validates():
    schema = load("copy.json")
    for example in schema["examples"]:
        assert CP.validate_action(example) == [] and errors(schema, example) == []
    assert any(e.get("$ref") == "copy.json" for e in load("package.json")["$defs"]["entry"]["oneOf"])


def test_paths_stay_inside_the_game_folder():
    assert CP.rel_path("ROM/119/51") == "ROM/119/51.DAT"
    assert CP.rel_path("sound9\\win\\music\\data\\music067.bgw") == "sound9/win/music/data/music067.bgw"
    for bad in ("", "/ROM/1/2.DAT", "C:/ROM/1/2.DAT", "ROM/../FTABLE.DAT", None):
        assert CP.rel_path(bad) is None
    assert CP.validate_action({"id": "copy.x", "type": "copy", "target": {"path": "../x.DAT"},
                               "resources": {"file": "x.DAT"}, "extra": 1}) == [
        "unknown key 'extra'",
        "target.path '../x.DAT' must be a path inside the game folder "
        "(like ROM/119/51.DAT or sound9/win/music/data/music067.bgw)"]
    assert CP.guess_target(Path("repo/rom/ROM/119/51.dat")) == "ROM/119/51.DAT"
    assert CP.guess_target(Path("rom/sound9/win/music/data/music067.bgw")) == "sound9/win/music/data/music067.bgw"
    assert CP.guess_target(Path("art/title.png")) is None


def test_a_build_into_the_install_keeps_what_it_replaced_and_undo_puts_it_back(game, sheet):
    r = run("prepare", str(sheet), "--project", "ui", "--type", "copy")
    assert r.exit_code == 0, r.output
    a = action()
    assert a["target"] == {"path": SHEET} and a["resources"]["file"] == "../repo/rom/ROM/119/51.DAT"
    r = run("build", "ui")
    assert r.exit_code == 0, r.output
    placed = game / "ROM" / "119" / "51.DAT"
    assert placed.read_bytes() == b"redrawn sheet"
    assert placed.with_name("51.DAT.base").read_bytes() == b"retail sheet"
    assert action()["result"] == {"path": SHEET, "targets": ["dir"]}
    assert "(already the same)" in run("build", "ui").output
    r = run("undo", "ui", "--yes")
    assert r.exit_code == 0, r.output
    assert placed.read_bytes() == b"retail sheet"


def test_a_pivot_build_writes_the_file_it_finds_and_undo_deletes_it(game, sheet, tmp_path, monkeypatch):
    import xi.xi_config as cfg
    pivot = tmp_path / "pivot"
    (pivot / "ROM" / "119").mkdir(parents=True)
    (pivot / "ROM" / "119" / "51.dat").write_bytes(b"old pivot sheet")     # the pivot's own case
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", str(pivot))
    assert run("prepare", str(sheet), "--project", "ui", "--type", "copy").exit_code == 0
    r = run("build", "ui", "--pivot", "--dry-run")
    assert r.exit_code == 0 and (pivot / "ROM" / "119" / "51.dat").read_bytes() == b"old pivot sheet"
    r = run("build", "ui", "--pivot")
    assert r.exit_code == 0, r.output
    assert [p.name for p in (pivot / "ROM" / "119").iterdir()] == ["51.dat"]   # no .base, no second file
    assert (pivot / "ROM" / "119" / "51.dat").read_bytes() == b"redrawn sheet"
    assert (game / "ROM" / "119" / "51.DAT").read_bytes() == b"retail sheet"
    assert run("undo", "ui", "--yes").exit_code == 0
    assert not (pivot / "ROM" / "119" / "51.dat").exists()


def test_building_into_the_folder_the_source_lives_in_leaves_it(game, sheet, monkeypatch):
    import xi.xi_config as cfg
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", str(sheet.parents[2]))       # repo/rom is the pivot
    assert run("prepare", str(sheet), "--project", "ui", "--type", "copy").exit_code == 0
    r = run("build", "ui", "--pivot")
    assert r.exit_code == 0 and "(the source itself" in r.output
    r = run("undo", "ui", "--yes")
    assert r.exit_code == 0 and "own source file: left alone" in r.output
    assert sheet.read_bytes() == b"redrawn sheet"


def test_a_music_track_and_a_re_prepare_that_keeps_the_result(game, tmp_path):
    track = tmp_path / "music067.bgw"
    track.write_bytes(b"BGMStream new")
    target = "sound9/win/music/data/music067.bgw"
    assert "pass --target" in run("prepare", str(track), "--project", "ui", "--type", "copy").output
    assert run("prepare", str(track), "--project", "ui", "--type", "copy", "--target", target,
               "--id", "copy.mog_garden").exit_code == 0
    assert run("build", "ui").exit_code == 0
    assert (game / Path(*target.split("/"))).read_bytes() == b"BGMStream new"
    assert run("prepare", str(track), "--project", "ui", "--type", "copy", "--id", "copy.mog_garden",
               "--replace").exit_code == 0
    assert action() == {"id": "copy.mog_garden", "type": "copy", "target": {"path": target},
                        "resources": {"file": "../music067.bgw"},
                        "result": {"path": target, "targets": ["dir"]}}
    assert json.loads(Path("projects/ui.json").read_text())["actions"][0]["result"]["targets"] == ["dir"]
    assert run("undo", "ui", "--yes").exit_code == 0
    assert not (game / Path(*target.split("/"))).exists()                  # it was new: deleted

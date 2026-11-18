"""``xi zone edit`` (xi.zone.xi_edit): recolouring an existing zone model in place, on the
synthetic DAT of test_tex_recolor (two DXT1 textures, a paletted one, an environment
section) copied into a temp game folder."""
import shutil

import pytest
from click.testing import CliRunner

from test_tex_recolor import _dat, _sections
from xi.zone import xi_edit as ZE


@pytest.fixture
def game(tmp_path, monkeypatch):
    import xi.xi_config as cfg
    root = tmp_path / "game"
    (root / "ROM" / "1").mkdir(parents=True)
    dat = root / "ROM" / "1" / "41.DAT"
    shutil.copy2(_dat(tmp_path), dat)
    monkeypatch.setattr(cfg, "FFXI_DIR", str(root))
    monkeypatch.setattr(cfg, "_REDIRECT_DIR", None)
    return dat


def _run(*args):
    return CliRunner().invoke(ZE.cmd, [str(a) for a in args])


def test_edit_in_place_keeps_base(game):
    before = game.read_bytes()
    r = _run(game, "--hue", 120, "--skip", "wall*")
    assert r.exit_code == 0, r.output
    base = game.with_name(game.name + ".base")
    assert base.read_bytes() == before
    a, b = _sections(base), _sections(game)
    assert b["suny    sky01"] != a["suny    sky01"]
    assert b["wall    w01"] == a["wall    w01"]
    assert "1 left as they were" in r.output


def test_dry_run_writes_nothing(game):
    before = game.read_bytes()
    r = _run(game, "--hue", 120, "--dry-run")
    assert r.exit_code == 0, r.output
    assert game.read_bytes() == before
    assert not game.with_name(game.name + ".base").exists()


def test_edits_layer_and_reset_restores(game):
    from xi.zone.xi_reset import reset_dat
    pristine = game.read_bytes()
    assert _run(game, "--fog-end", 80).exit_code == 0
    once = _sections(game)
    assert _run(game, "--texture", "tree*:lightness=-40").exit_code == 0
    twice = _sections(game)
    assert twice["environment"] == once["environment"] != _sections(game.with_name(game.name + ".base"))["environment"]
    assert twice["tree    t01"] != once["tree    t01"]                  # layered on top of the fog edit
    reset_dat(game)
    assert game.read_bytes() == pristine


def test_nothing_to_change_is_a_usage_error(game):
    r = _run(game)
    assert r.exit_code == 2 and "nothing to change" in r.output


def test_bad_texture_option(game):
    r = _run(game, "--texture", "lightness=-70")
    assert r.exit_code == 2


def test_unknown_zone_name(monkeypatch):
    import xi.zone.xi_inject as inj
    monkeypatch.setattr(inj, "_load_zone_names", lambda: ["", "Lower Jeuno", "Upper Jeuno"])
    with pytest.raises(Exception, match="No zone called"):
        ZE.resolve_zone_model("Jeuno")                 # two match: refused, not guessed


def test_retail_zone_by_name_resolves(root):
    label, path = ZE.resolve_zone_model("Lower Jeuno")
    assert label == "zone 245 (Lower Jeuno)" and path.is_file()

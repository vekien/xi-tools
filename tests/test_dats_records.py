"""The ``spell`` / ``command`` actions of ``xi dats`` (xi.dats.xi_dats): a definition
becomes an action with ``prepare``, ``build`` writes the record into an enlarged
ROM/118/114.DAT plus its names/help and records the id, ``undo`` clears it — on a
synthetic game folder (small menu table + string tables), no install needed.

The table library itself is covered by tests/test_menu_table.py."""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from xi.menu import xi_menu_table as MT

from test_menu_table import COMMAND_EXAMPLE, SPELL_EXAMPLE, install


@pytest.fixture
def game(tmp_path: Path, monkeypatch):
    """A synthetic FFXI_DIR with 8 spells / 6 commands, and the cwd where projects/ lands."""
    import xi.ftable.xi_expand as xe
    import xi.xi_config as cfg
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", None, raising=False)   # stay inside tmp_path
    root = install(tmp_path / "game")
    monkeypatch.setattr(cfg, "FFXI_DIR", str(root))
    # xi_expand keeps its own copies of the paths: never let a build's table sync reach a real folder
    monkeypatch.setattr(xe, "FFXI_DIR", str(root))
    monkeypatch.setattr(xe, "FFXI_PIVOT_DIR", "")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "exports" / "menu").mkdir(parents=True)
    sp = tmp_path / "exports" / "menu" / "testspell.spell.json"
    sp.write_text(json.dumps(dict(SPELL_EXAMPLE, like=3)), encoding="utf-8")
    cp = tmp_path / "exports" / "menu" / "war_cry.command.json"
    cp.write_text(json.dumps(dict(COMMAND_EXAMPLE, like=2)), encoding="utf-8")
    return root, sp, cp


def test_prepare_writes_record_actions(game):
    from xi.dats.xi_dats import _detect_source_type, _read_manifest, group
    root, sp, cp = game
    assert _detect_source_type(sp, None)[0] == "spell"
    assert _detect_source_type(cp, None)[0] == "command"
    runner = CliRunner()
    r = runner.invoke(group, ["prepare", str(sp), "--project", "fs", "--replace"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert "auto takes an id past what the game reads" in r.output and "cexislots" in r.output
    r = runner.invoke(group, ["prepare", str(cp), "--project", "fs", "--replace"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    acts = _read_manifest(Path("projects/fs.json"))["actions"]
    a, c = acts
    assert a["type"] == "spell" and a["id"] == "spell.testspell"
    assert a["target"] == {"id": "auto", "menu_index": "auto"}
    assert (Path("projects/resources") / a["resources"]["definition"]).is_file()
    assert c["type"] == "command" and c["id"] == "command.war_cry" and c["target"] == {"id": "auto"}

    # A re-prepare keeps the recorded id; an explicit flag replaces the target.
    m = _read_manifest(Path("projects/fs.json"))
    m["actions"][0]["result"] = {"record_id": 4090, "like": 3}
    Path("projects/fs.json").write_text(json.dumps(m), encoding="utf-8")
    r = runner.invoke(group, ["prepare", str(sp), "--project", "fs", "--replace", "--record-id", "4000"],
                      catch_exceptions=False)
    assert r.exit_code == 0, r.output
    a = _read_manifest(Path("projects/fs.json"))["actions"][0]
    assert a["target"]["id"] == 4000 and a["result"]["record_id"] == 4090

    # A definition of the other kind is refused for the wrong --type.
    r = runner.invoke(group, ["prepare", str(cp), "--project", "fs", "--replace", "--type", "spell"])
    assert r.exit_code != 0 and "command definition" in r.output


def test_build_writes_record_and_names_then_undo(game):
    from xi.dats.xi_dats import _read_manifest, group
    root, sp, cp = game
    runner = CliRunner()
    for src in (sp, cp):
        assert runner.invoke(group, ["prepare", str(src), "--project", "fs", "--replace"],
                             catch_exceptions=False).exit_code == 0

    # dry run: the plan is shown, nothing changes on disk
    r = runner.invoke(group, ["build", "fs", "--dry-run"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert 'spell 4095 "Testspell" cloned from 3' in r.output and "menu index 8" in r.output
    assert 'command 4095 "War Cry" cloned from 2' in r.output
    assert "spell id 4095 is past what the game reads (spells 0-1023)" in r.output and "cexislots" in r.output
    assert "INSERT INTO `spell_list`" in r.output
    assert MT.load_menu(root).count("spell") == 8
    assert not MT.menu_path(root).with_name("114.DAT.base").exists()

    r = runner.invoke(group, ["build", "fs"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    menu = MT.load_menu(root)
    assert menu.count("spell") == 4096 and menu.count("command") == 4096
    f = MT.read_fields("spell", menu.records("spell")[4095])
    assert f["id"] == 4095 and f["mp"] == 12 and f["levels"] == {"BLM": 20, "RDM": 25} and f["menu_index"] == 8
    assert MT.read_fields("command", menu.records("command")[4095])["level"] == 30
    assert MT.read_fields("spell", menu.records("spell")[3])["mp"] == 7      # retail row untouched
    assert MT.read_names("spell", root)[4095] == "Testspell"
    assert MT.read_names("command", root, "jp")[4095] == "War Cry"
    assert MT.menu_path(root).with_name("114.DAT.base").exists()
    acts = _read_manifest(Path("projects/fs.json"))["actions"]
    assert acts[0]["result"]["record_id"] == 4095 and acts[0]["result"]["menu_index"] == 8
    assert acts[0]["result"]["dat"] == "ROM/118/114.DAT"
    assert set(acts[0]["result"]["strings"]) == {"ROM/181/73.DAT", "ROM/181/69.DAT", "ROM/181/75.DAT", "ROM/181/71.DAT"}
    assert "dir" in acts[0]["result"]["targets"]
    assert acts[1]["result"]["record_id"] == 4095 and "menu_index" not in acts[1]["result"]
    assert Path("projects/server/spells/testspell_4095.sql").read_text(encoding="utf-8").startswith("--")
    assert Path("projects/server/commands/war_cry_4095.sql").exists()

    # a rebuild keeps the same ids (the result wins over "auto")
    r = runner.invoke(group, ["build", "fs"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert _read_manifest(Path("projects/fs.json"))["actions"][0]["result"]["record_id"] == 4095
    assert MT.is_empty(MT.load_menu(root).records("spell")[4094])

    r = runner.invoke(group, ["changelog", "--project", "fs"], catch_exceptions=False)
    assert r.exit_code == 0 and "4095" in r.output and "ROM/118/114.DAT" in r.output

    r = runner.invoke(group, ["undo", "fs"], input="y\n", catch_exceptions=False)
    assert r.exit_code == 0, r.output
    menu = MT.load_menu(root)
    assert MT.is_empty(menu.records("spell")[4095]) and MT.is_empty(menu.records("command")[4095])
    assert MT.read_names("spell", root)[4095] == "." and MT.read_names("command", root)[4095] == "."
    assert not Path("projects/fs.json").exists()


def test_build_refuses_retail_band_and_occupied_ids(game):
    from xi.dats.xi_dats import group
    root, sp, cp = game
    runner = CliRunner()
    r = runner.invoke(group, ["prepare", str(sp), "--project", "fs", "--replace", "--record-id", "5"],
                      catch_exceptions=False)
    assert r.exit_code == 0, r.output
    r = runner.invoke(group, ["build", "fs"])
    assert r.exit_code != 0 and "retail band" in r.output
    r = runner.invoke(group, ["build", "fs", "--force"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert MT.read_fields("spell", MT.load_menu(root).records("spell")[5])["mp"] == 12

    # a second action asking for an id another one took is refused
    r = runner.invoke(group, ["prepare", str(cp), "--project", "fs", "--replace"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    r = runner.invoke(group, ["build", "fs", "--only", "command.war_cry"], catch_exceptions=False)
    assert r.exit_code == 0, r.output                       # took command 4095
    other = cp.with_name("other.command.json")
    other.write_text(json.dumps(dict(COMMAND_EXAMPLE, name="other", like=2, id=4095)), encoding="utf-8")
    r = runner.invoke(group, ["prepare", str(other), "--project", "fs", "--replace"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    r = runner.invoke(group, ["build", "fs", "--only", "command.other"])
    assert r.exit_code != 0 and "already holds a record" in r.output
    # …and auto steps past it
    r = runner.invoke(group, ["prepare", str(other), "--project", "fs", "--replace", "--record-id", "4094"],
                      catch_exceptions=False)
    assert r.exit_code == 0, r.output
    r = runner.invoke(group, ["build", "fs", "--only", "command.other"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert MT.read_names("command", root)[4094] == "War Cry"


def test_forced_overwrite_is_put_back_by_undo(game):
    from xi.dats.xi_dats import _read_manifest, group
    root, sp, cp = game
    original = MT.load_menu(root).records("spell")[5]
    runner = CliRunner()
    r = runner.invoke(group, ["prepare", str(sp), "--project", "fs", "--replace", "--record-id", "5"],
                      catch_exceptions=False)
    assert r.exit_code == 0, r.output
    r = runner.invoke(group, ["build", "fs", "--force"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert "writes over the record that was at 5" in r.output
    assert MT.read_names("spell", root)[5] == "Testspell"
    res = _read_manifest(Path("projects/fs.json"))["actions"][0]["result"]
    assert res["replaced"]["record"] == original.hex()
    # a rebuild keeps what the first build replaced, not its own record
    assert runner.invoke(group, ["build", "fs", "--force"], catch_exceptions=False).exit_code == 0
    assert _read_manifest(Path("projects/fs.json"))["actions"][0]["result"]["replaced"]["record"] == original.hex()

    r = runner.invoke(group, ["undo", "fs", "--yes"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert MT.load_menu(root).records("spell")[5] == original
    assert MT.read_names("spell", root)[5] == "Name 5" and MT.read_names("spell", root, "jp")[5] == "Name 5"


def test_changed_id_puts_the_old_row_back(game):
    from xi.dats.xi_dats import _read_manifest, group
    root, sp, cp = game
    runner = CliRunner()
    assert runner.invoke(group, ["prepare", str(sp), "--project", "fs", "--replace"],
                         catch_exceptions=False).exit_code == 0
    assert runner.invoke(group, ["build", "fs"], catch_exceptions=False).exit_code == 0     # took 4095
    r = runner.invoke(group, ["prepare", str(sp), "--project", "fs", "--replace", "--record-id", "4000"],
                      catch_exceptions=False)
    assert r.exit_code == 0, r.output

    r = runner.invoke(group, ["build", "fs", "--dry-run"], catch_exceptions=False)
    assert r.exit_code == 0 and "moved from spell 4095" in r.output
    assert MT.read_names("spell", root)[4095] == "Testspell"                           # dry run: untouched

    r = runner.invoke(group, ["build", "fs"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    menu = MT.load_menu(root)
    assert MT.is_empty(menu.records("spell")[4095]) and MT.read_names("spell", root)[4095] == "."
    f = MT.read_fields("spell", menu.records("spell")[4000])
    assert f["mp"] == 12 and f["menu_index"] == 8 and MT.read_names("spell", root)[4000] == "Testspell"
    assert _read_manifest(Path("projects/fs.json"))["actions"][0]["result"]["record_id"] == 4000


def test_text_that_does_not_fit_fails_before_writing(game):
    from xi.dats.xi_dats import group
    root, sp, cp = game
    runner = CliRunner()
    # 41 bytes is over a command name's 39: refused as soon as the definition is read
    long_cmd = cp.with_name("long.command.json")
    long_cmd.write_text(json.dumps(dict(COMMAND_EXAMPLE, name="long", like=2, text={"name_en": "x" * 41})),
                        encoding="utf-8")
    r = runner.invoke(group, ["prepare", str(long_cmd), "--project", "fs", "--replace"])
    assert r.exit_code != 0 and "a command name holds 39" in r.output

    # 60 bytes fits a retail spell name (99) but not this install's 80-byte blocks: the
    # build stops before 114.DAT or any name table is written
    long_spell = sp.with_name("long.spell.json")
    long_spell.write_text(json.dumps(dict(SPELL_EXAMPLE, name="long", like=3, text={"name_en": "x" * 60})),
                          encoding="utf-8")
    assert runner.invoke(group, ["prepare", str(long_spell), "--project", "fs", "--replace"],
                         catch_exceptions=False).exit_code == 0
    before = {p: p.read_bytes() for p in (root / "ROM").rglob("*.DAT")}
    r = runner.invoke(group, ["build", "fs"])
    assert r.exit_code != 0 and "holds 39" in r.output
    assert {p: p.read_bytes() for p in (root / "ROM").rglob("*.DAT")} == before
    assert not list(root.rglob("*.base"))


def test_pivot_flag_builds_into_the_pivot_folder_only(game, tmp_path: Path, monkeypatch):
    """A configured FFXI_PIVOT_DIR is used only with --pivot: a plain build edits the base
    install; --pivot edits the pivot folder's own 114.DAT (copying in the name tables it
    lacks, without .base), records the target, and undo clears the record there."""
    import xi.xi_config as cfg
    from xi.dats.xi_dats import _read_manifest, group
    from test_menu_table import menu_dat
    root, sp, cp = game
    pivot = tmp_path / "pivot"
    (pivot / "ROM" / "118").mkdir(parents=True)
    (pivot / "ROM" / "118" / "114.DAT").write_bytes(menu_dat(9, 6))
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", str(pivot), raising=False)
    runner = CliRunner()

    assert runner.invoke(group, ["prepare", str(sp), "--project", "base", "--replace"],
                         catch_exceptions=False).exit_code == 0
    r = runner.invoke(group, ["build", "base"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert MT.load_menu(root).count("spell") == 4096
    assert MT.load_menu(pivot).count("spell") == 9 and not (pivot / "ROM" / "181").exists()

    assert runner.invoke(group, ["prepare", str(sp), "--project", "piv", "--replace"],
                         catch_exceptions=False).exit_code == 0
    r = runner.invoke(group, ["build", "piv", "--pivot", "--dry-run"], catch_exceptions=False)
    assert r.exit_code == 0 and "Target: Pivot folder" in r.output
    assert MT.load_menu(pivot).count("spell") == 9
    r = runner.invoke(group, ["build", "piv", "--pivot"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    menu = MT.load_menu(pivot)
    assert menu.count("spell") == 4096 and MT.read_fields("spell", menu.records("spell")[4095])["mp"] == 12
    assert MT.read_names("spell", pivot)[4095] == "Testspell"
    assert sorted(p.name for p in (pivot / "ROM" / "181").iterdir()) == ["69.DAT", "71.DAT", "73.DAT", "75.DAT"]
    assert not list(pivot.rglob("*.base"))
    a = _read_manifest(Path("projects/piv.json"))["actions"][0]
    assert a["result"]["targets"] == ["pivot"] and a["result"]["record_id"] == 4095

    r = runner.invoke(group, ["undo", "piv", "--yes"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert MT.is_empty(MT.load_menu(pivot).records("spell")[4095])
    assert MT.read_names("spell", pivot)[4095] == "."
    assert MT.read_fields("spell", MT.load_menu(root).records("spell")[4095])["mp"] == 12   # base project's row stays

    # --pivot without a configured folder is refused
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", "", raising=False)
    r = runner.invoke(group, ["build", "base", "--pivot"])
    assert r.exit_code != 0 and "FFXI_PIVOT_DIR is not configured" in r.output

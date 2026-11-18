"""The monster ability names (``mobSkills``, ROM/27/80 EN / ROM/27/79 JP) in the ``database``
action, ``xi database grow``, ``xi database json`` / ``search`` and ``xi mv database``, on a
synthetic game folder. No install needed."""
from pathlib import Path

from click.testing import CliRunner

from test_dats_database_build import action, build, game, prepare  # noqa: F401  (game: the fixture)
from xi.database import xi_build as DB
from xi.database import xi_core as C
from xi.dialog.xi_dialog import build_container, raw_entry_blobs

EN, JP = "ROM/27/80.DAT", "ROM/27/79.DAT"


def names_dat(names: list[bytes]) -> bytes:
    return build_container([n + b"\0" for n in names], obfuscated=True)


def install_names(root: Path) -> None:
    for rel, names in ((EN, [b"#0", b"Foot Kick", b"Dust Cloud", b"."]),
                       (JP, [b"#0", "フットキック".encode("cp932"), b".", b".", b"."])):
        p = root / Path(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(names_dat(names))


def names(root: Path, rel: str) -> list[str]:
    return [DB._event_text(b)[0] for b in raw_entry_blobs((root / Path(*rel.split("/"))).read_bytes())[0]]


def undo() -> None:
    from xi.dats.xi_dats import group
    r = CliRunner().invoke(group, ["undo", "tweaks", "--yes"], catch_exceptions=False)
    assert r.exit_code == 0, r.output


def test_a_name_edit_builds_rebuilds_and_undoes(game):
    install_names(game)
    en0, jp0 = (game / EN).read_bytes(), (game / JP).read_bytes()
    prepare([{"table": "mobSkills", "id": 257, "strings": {"en": {"name": "Stomp"}}},
             {"table": "mobSkills", "id": 258, "strings": {"en": {"name": {"replace": {"Dust": "Sand"}}}}}])
    r = build()
    assert r.exit_code == 0, r.output
    assert names(game, EN) == ["#0", "Stomp", "Sand Cloud", "."]
    # JP 257 has a name of its own and keeps it; JP 258 is a placeholder and takes the English.
    assert names(game, JP) == ["#0", "フットキック", "Sand Cloud", ".", "."]
    recs = action()["result"]["roots"]["dir"]
    assert {(e["id"], e["lang"]) for e in recs} == {(257, "en"), (258, "en"), (258, "jp")}
    assert next(e for e in recs if e["id"] == 257)["changed"]["strings.name"] == {"from": "Foot Kick", "to": "Stomp"}
    after = (game / EN).read_bytes(), (game / JP).read_bytes()
    assert build().exit_code == 0                                        # a rebuild converges
    assert ((game / EN).read_bytes(), (game / JP).read_bytes()) == after
    undo()
    assert (game / EN).read_bytes() == en0 and (game / JP).read_bytes() == jp0


def test_dry_run_writes_nothing(game):
    install_names(game)
    en0 = (game / EN).read_bytes()
    prepare([{"table": "mobSkills", "id": 257, "strings": {"en": {"name": "Stomp"}}}])
    r = build("tweaks", "--dry-run")
    assert r.exit_code == 0, r.output
    assert (game / EN).read_bytes() == en0 and "result" not in action()


def test_an_id_past_the_end_grows_both_tables_and_undo_trims_them(game):
    install_names(game)
    en0, jp0 = (game / EN).read_bytes(), (game / JP).read_bytes()
    prepare([{"table": "mobSkills", "id": 262, "strings": {"en": {"name": "Abyssal Roar"},
                                                           "jp": {"name": "アビサルロア"}}}])
    r = build()
    assert r.exit_code == 0, r.output
    assert names(game, EN) == ["#0", "Foot Kick", "Dust Cloud", ".", ".", ".", "Abyssal Roar"]
    assert names(game, JP)[-1] == "アビサルロア" and len(names(game, JP)) == 7
    grew = {e["lang"]: e["grew"] for e in action()["result"]["roots"]["dir"]}
    assert grew == {"en": [4, 7], "jp": [5, 7]}
    assert build().exit_code == 0                                        # on top: the same bytes
    assert names(game, EN)[-1] == "Abyssal Roar" and len(names(game, EN)) == 7
    undo()
    assert (game / EN).read_bytes() == en0 and (game / JP).read_bytes() == jp0


def test_reset_takes_an_edit_back_and_replace_keeps_the_result(game):
    install_names(game)
    prepare([{"table": "mobSkills", "id": 257, "strings": {"en": {"name": "Stomp"}}},
             {"table": "mobSkills", "id": 258, "strings": {"en": {"name": "Sand Cloud"}}}])
    assert build().exit_code == 0
    prepare([{"table": "mobSkills", "id": 257, "strings": {"en": {"name": "Stomp"}}}], "tweaks", "--replace")
    assert action()["result"]["roots"]["dir"]                            # --replace keeps the recorded result
    assert build("tweaks", "--reset").exit_code == 0
    assert names(game, EN) == ["#0", "Stomp", "Dust Cloud", "."]
    assert names(game, JP)[2] == "."


def test_the_validator_names_what_a_mob_skill_edit_cannot_do():
    def errs(edit):
        return C.validate_action({"id": "database.t", "type": "database", "edits": [edit]})
    assert errs({"table": "mobSkills", "id": 257, "strings": {"en": {"name": "Stomp"}}}) == []
    assert any("ids start at 256" in e for e in errs({"table": "mobSkills", "id": 32,
                                                       "strings": {"en": {"name": "x"}}}))
    assert any("takes strings and note" in e for e in errs({"table": "mobSkills", "id": 257, "copy_from": 258,
                                                            "strings": {"en": {"name": "x"}}}))
    assert any("has no en string 'help'" in e for e in errs({"table": "mobSkills", "id": 257,
                                                             "strings": {"en": {"help": "x"}}}))
    assert any("changes nothing" in e for e in errs({"table": "mobSkills", "id": 257}))


def test_grow_fills_an_event_message_table_with_placeholders(game):
    from xi.database.xi_grow import grow_table
    install_names(game)
    r = grow_table(game, EN, 10)
    assert (r["before"], r["after"], r["base_before"]) == (4, 10, 4)
    assert names(game, EN)[4:] == ["."] * 6
    base =raw_entry_blobs((game / "ROM/27/80.DAT.base").read_bytes())[0]
    assert len(base) == 10                                               # the .base grows too
    assert grow_table(game, EN, 10)["written"] is False                 # already that long


def test_json_and_search_join_the_server_rows(game, tmp_path):
    install_names(game)
    sql = tmp_path / "server" / "sql"
    sql.mkdir(parents=True)
    (sql / "mob_skills.sql").write_text(
        "CREATE TABLE IF NOT EXISTS `mob_skills` (\n  `mob_skill_id` smallint(4) unsigned NOT NULL,\n"
        "  `mob_anim_id` smallint(4) unsigned NOT NULL,\n  `mob_skill_name` varchar(40) NOT NULL,\n"
        "  `mob_skill_distance` float(3,1) NOT NULL DEFAULT '6.0',\n  PRIMARY KEY (`mob_skill_id`)\n"
        ") ENGINE=Aria;\n"
        "INSERT INTO `mob_skills` VALUES (257,1,'foot_kick',7.0);\n"
        "INSERT INTO `mob_skills` VALUES (4300,2170,'hell''s_trap',10.0);\n", encoding="utf-8")
    from xi.database.xi_json import mob_skill_rows
    rows = mob_skill_rows(game)
    assert [r["id"] for r in rows] == [257, 258, 4300]                  # '#0' and '.' left out
    by_id = {r["id"]: r for r in rows}
    assert by_id[257]["server"] == {"mob_anim_id": 1, "mob_skill_name": "foot_kick", "mob_skill_distance": 7.0}
    assert 259 not in by_id                                              # '.' rows only with --all
    assert by_id[4300]["name"] is None and by_id[4300]["server"]["mob_skill_name"] == "hell's_trap"
    assert 259 in {r["id"] for r in mob_skill_rows(game, show_all=True)}

    from xi.xi_cli import cli
    r = CliRunner().invoke(cli, ["database", "search", "mobSkills", "foot"], catch_exceptions=False)
    assert r.exit_code == 0 and "257  Foot Kick  [foot_kick anim 1]" in r.output
    r = CliRunner().invoke(cli, ["database", "search", "mobSkills", "1"], catch_exceptions=False)
    assert "No mobSkills row" in r.output                                # a number is an id, not text
    r = CliRunner().invoke(cli, ["database", "json", "mobSkills", "--search", "#4300"], catch_exceptions=False)
    assert '"hell\'s_trap"' in r.output


def test_the_model_viewer_bake_lists_every_row(game, tmp_path):
    install_names(game)
    from xi.mv.xi_database import cmd
    out = tmp_path / "db"
    r = CliRunner().invoke(cmd, ["--only", "mobSkills", "--out", str(out), "--game", str(game)],
                           catch_exceptions=False)
    assert r.exit_code == 0, r.output
    import json
    doc = json.loads((out / "mobSkills.en.json").read_text("utf-8"))
    assert (doc["kind"], doc["firstId"], doc["num"], doc["files"]) == ("names", 256, 4, [EN])
    assert doc["rows"][1] == {"idx": 1, "id": 257, "offset": 4 + 4 * 4 + 3, "length": 9, "name": "Foot Kick"}
    assert json.loads((out / "mobSkills.jp.json").read_text("utf-8"))["rows"][1]["name"] == "フットキック"

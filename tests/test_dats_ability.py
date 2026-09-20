"""The ``ability`` action of ``xi dats`` (xi.dats.xi_dats): recipe validation against
schema/ability_recipe.json, ``prepare`` turning a recipe into an action, and ``build``
placing the planned DATs and registering their file ids — driven with a synthetic
game folder (empty tables) and a stubbed compose, so no install is needed. The plan of a
weapon skill's waist companions reads the install's own placeholders (the ``root``
fixture, skipped without it).

The composition itself is covered by tests/test_ability_compose.py."""
import base64
import json
import struct
from pathlib import Path

import pytest
from click.testing import CliRunner

from xi.ability import xi_compose as ac
from xi.ability import xi_publish as ap

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = json.loads((REPO / "schema" / "ability_recipe.json").read_text(encoding="utf-8"))["examples"]
EXAMPLE = EXAMPLES[0]


# ── recipe schema ────────────────────────────────────────────────────────────────

def test_schema_example_validates():
    # Every example, the one with generator and curve edits included (those keys have
    # their own tests in tests/test_ability_genedit.py).
    for example in EXAMPLES:
        assert ac.validate_recipe(example) == []


def test_validate_recipe_names_the_field():
    bad = dict(EXAMPLE, events=[{"from": "nope", "op": 999, "start": -1, "blend": [1]}], extra=1)
    errs = ac.validate_recipe(bad)
    assert any("unknown key 'extra'" in e for e in errs)
    assert any("lane 'nope'" in e for e in errs)
    assert any("op must be" in e for e in errs)
    assert any("start must be" in e for e in errs)
    assert any("blend must be" in e for e in errs)


def test_validate_recipe_accepts_event_names():
    # The mixer writes the inspector's command name on every event (PlayClip,
    # LockCasterMagic…) — a label, not an instruction, and compose ignores it.
    r = {"name": "x", "sources": {"motion": "ja:0"},
         "events": [{"from": "motion", "op": 5, "ref": "cm0?", "start": 0, "name": "PlayClip"}]}
    assert ac.validate_recipe(r) == []
    r["events"][0]["name"] = 5
    assert ac.validate_recipe(r) == ["events[0]: 'name' must be a string"]


def test_validate_recipe_accepts_event_rows():
    # The mixer saves which row of its track a pill sits in. Layout only: compose
    # ignores it, so the schema and the validator just hold it to a row number.
    r = {"name": "x", "sources": {"motion": "ja:0"},
         "events": [{"from": "motion", "op": 5, "ref": "cm0?", "start": 0, "row": 0},
                    {"from": "motion", "op": 5, "ref": "cm1?", "start": 0, "row": 2}]}
    assert ac.validate_recipe(r) == []
    for bad in (-1, 1.5, "1", True, None):
        r["events"][1]["row"] = bad
        assert ac.validate_recipe(r) == ["events[1]: row must be a non-negative integer"], bad
    schema = json.loads((REPO / "schema" / "ability_recipe.json").read_text(encoding="utf-8"))
    assert schema["$defs"]["event"]["properties"]["row"] == {
        "type": "integer", "minimum": 0,
        "description": schema["$defs"]["event"]["properties"]["row"]["description"]}


def test_validate_recipe_accepts_string_sources_and_hex_ops():
    r = {"name": "x", "sources": {"motion": "ws:1:HumeMale"},
         "events": [{"from": "motion", "op": "0x05", "ref": "b00?", "start": 0, "dur": 10}]}
    assert ac.validate_recipe(r) == []


_LOCK = "200300000000000000000000"                     # 0x20 LockActorStatus, 3 dwords
_LINK = "03040000000000006d64616d00000000"             # 0x03 LinkRoutine `mdam`


def test_validate_recipe_takes_a_command_of_no_lane():
    # A lock, hit or link added by hand has no `from`: the event is its own `raw` bytes.
    # The schema says the same: `op` and `start` always, and `from` or `raw`.
    r = {"name": "x", "sources": {"motion": "ws:1"},
         "events": [{"op": 0x20, "start": 0, "dur": 100, "raw": "200300000000640000000000"},
                    {"op": "0x2E", "start": 0, "dur": 90, "raw": "2e02000000005a00"},
                    {"op": 3, "ref": "mdam", "start": 80, "raw": _LINK, "name": "LinkRoutine(source)"},
                    {"op": 0x3C, "start": 0, "raw": "3c040000000000007368626b00000000"},
                    {"from": "motion", "op": 0x20, "start": 0, "raw": _LOCK}]}
    assert ac.validate_recipe(r) == []
    event = json.loads((REPO / "schema" / "ability_recipe.json").read_text(encoding="utf-8"))["$defs"]["event"]
    assert event["required"] == ["op", "start"]
    assert event["anyOf"] == [{"required": ["from"]}, {"required": ["raw"]}]


@pytest.mark.parametrize("event, says", [
    ({"op": 0x20, "start": 0}, "an event with no 'from' (lane) is a command of its own and must carry its 'raw' bytes"),
    ({"from": None, "op": 0x20, "start": 0, "raw": _LOCK}, "'from' must be a lane (a key of sources)"),
    # The client walks a routine by each command's size byte (+1): the bytes must say
    # what they are, and byte 0 is the op.
    ({"op": 0x20, "start": 0, "raw": "200200000000000000000000"}, "raw is 12 bytes but its size byte (+1) says 8"),
    ({"from": "motion", "op": 0x20, "start": 0, "raw": "200200000000000000000000"},
     "raw is 12 bytes but its size byte (+1) says 8"),
    ({"op": 0x20, "start": 0, "raw": "2003000000000000000000"},
     "raw is 11 bytes; a command is whole dwords, 8 bytes at least (op, size, delay, duration)"),
    ({"op": 0x20, "start": 0, "raw": "20010000"},
     "raw is 4 bytes; a command is whole dwords, 8 bytes at least (op, size, delay, duration)"),
    ({"op": 0x20, "start": 0, "raw": "1f0300000000000000000000"}, "raw is op 0x1F, not the event's op 0x20"),
    # A command naming a routine, sound or generator: the client keeps its pointer at +0xC.
    ({"op": 3, "ref": "mdam", "start": 0, "raw": _LINK[:24] + "01000000"},
     "raw has 01000000 at +0xC, where it must have 0: the client keeps a pointer there once it has looked "
     "the name up, and takes anything else for one"),
    ({"op": 3, "start": 0, "raw": "0304000000000000ff00000000000000"},
     "raw names ff000000 at +8; a routine, sound or generator name is 1–4 printable ASCII characters, padded with NULs"),
    ({"op": 3, "start": 0, "raw": "030300000000000000000000"},
     "raw is 12 bytes; a command that names a routine, sound or generator is 16 at least (the name at +8, 0 at +0xC)"),
    ({"op": 3, "ref": "mdä", "start": 0, "raw": _LINK}, "ref must be a 1–4 character section id"),
    # What a lane's DAT holds cannot come from no lane.
    ({"op": 5, "ref": "cm0?", "start": 0, "raw": ac._TEMPLATES[0x05].hex()},
     "op 0x05 names a clip of its lane's DAT, so it needs 'from'"),
    ({"op": 2, "ref": "g000", "start": 0, "raw": ac._TEMPLATES[0x02].hex()},
     "op 0x02 names a generator of its lane's DAT, so it needs 'from'"),
    ({"op": 0x2D, "ref": "g000", "start": 0, "raw": "2d0400000000000067303030" + "00000000"},
     "op 0x2D names a generator of its lane's DAT, so it needs 'from'"),
    ({"op": 0x0A, "ref": "8050", "start": 0, "raw": ac._TEMPLATES[0x0A].hex()},
     "op 0x0A names a sound of its lane's DAT, so it needs 'from'"),
    ({"op": 0x2C, "ref": "b0a?", "start": 0, "raw": "2c02000000000000"},
     "op 0x2C names a weapon trace of its lane's DAT, so it needs 'from'"),
])
def test_validate_recipe_refuses_a_bad_command(event, says):
    r = {"name": "x", "sources": {"motion": "ws:1"}, "events": [event]}
    assert ac.validate_recipe(r) == [f"events[0]: {says}"]


def test_load_recipe_rejects_bad_file(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"name": "bad name!", "sources": {}, "events": []}), encoding="utf-8")
    with pytest.raises(Exception) as ei:
        ac.load_recipe(p)
    assert "ability_recipe.json" in str(ei.value)


def test_infer_kind():
    assert ap.infer_kind(EXAMPLE) == "ja"
    assert ap.infer_kind({"name": "s", "sources": {"motion": {"spec": "spell:144"}}, "events": []}) == "spell"
    assert ap.infer_kind({"name": "w", "sources": {"motion": {"spec": "ws:1"}}, "events": []}) == "ws"
    # A race-bound motion asked to publish as a job ability is baked from one race into
    # the single DAT (experimental), not refused.
    assert ap.infer_kind({"name": "w", "sources": {"motion": {"spec": "ws:1"}}, "events": []}, kind="ja") == "ja"


# ── dats prepare / build / undo ──────────────────────────────────────────────────

ENTRIES = 6000     # enough for file_id 4412 + 339 (a custom job ability)


@pytest.fixture
def game(tmp_path: Path, monkeypatch):
    """A synthetic FFXI_DIR with empty tables, and the cwd where projects/ lands."""
    root = tmp_path / "game"
    for d in (root, root / "ROM10"):
        d.mkdir(parents=True)
    (root / "FTABLE.DAT").write_bytes(b"\0" * (ENTRIES * 2))
    (root / "VTABLE.DAT").write_bytes(b"\0" * ENTRIES)
    (root / "ROM10" / "FTABLE10.DAT").write_bytes(b"\0" * (ENTRIES * 2))
    (root / "ROM10" / "VTABLE10.DAT").write_bytes(b"\0" * ENTRIES)
    import xi.ftable.xi_expand as xe
    import xi.xi_config as cfg
    monkeypatch.setattr(cfg, "FFXI_DIR", str(root))
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", None, raising=False)
    # xi_expand keeps its own copies of the paths: without these the build's pivot table
    # sync reads (and could write) the real install and FFXI_PIVOT_DIR from .env
    monkeypatch.setattr(xe, "FFXI_DIR", str(root))
    monkeypatch.setattr(xe, "FFXI_PIVOT_DIR", "")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "exports" / "ability" / "mixer").mkdir(parents=True)
    rp = tmp_path / "exports" / "ability" / "mixer" / "tiger_fury.recipe.json"
    rp.write_text(json.dumps(EXAMPLE), encoding="utf-8")
    return root, rp


def _stub_compose(monkeypatch, animation=339):
    """Stand in for compose: one job-ability DAT of 64 bytes at file_id 4412 + animation."""
    def fake_plan(recipe, root, *, kind=None, animation=None, subdir=20, force=False, previous=None,
                  animation_from=None):
        anim = animation if animation is not None else ((previous or {}).get("animation") or 339)
        prev = {(p.get("race"), p.get("role")): p for p in (previous or {}).get("placements") or []}
        place = (prev.get((None, "body")) or {}).get("dat") or f"ROM10/{subdir}/0.DAT"
        return {"root": root, "kind": ap.infer_kind(recipe, kind), "animation": anim, "subdir": subdir,
                "files": [{"race": None, "role": "body", "file_id": ap.ABILITY_FILE_OFFSET + anim,
                           "place": place, "composed": object(), "current": None}]}

    def fake_write(recipe, plan):
        out = Path("exports") / "ability" / recipe["name"]
        out.mkdir(parents=True, exist_ok=True)
        p = out / f"{recipe['name']}.DAT"
        p.write_bytes(b"tigr" + b"\0" * 60)
        return [p]

    monkeypatch.setattr(ap, "plan", fake_plan)
    monkeypatch.setattr(ap, "write_sources", fake_write)


def _resolve(root: Path, file_id: int, rom: int = 1):
    """What one table pair of ``root`` says for file_id: the main pair, or ROM{rom}'s."""
    from xi.ftable.xi_core import resolve_dat, root_table_pair
    ft, vt = (Path(p) for p in root_table_pair(root, rom))
    return resolve_dat(ft.read_bytes(), vt.read_bytes(), file_id)


def test_prepare_writes_ability_action(game):
    from xi.dats.xi_dats import _detect_source_type, _read_manifest, group
    root, rp = game
    assert _detect_source_type(rp, None)[0] == "ability"
    r = CliRunner().invoke(group, ["prepare", str(rp), "--project", "tf", "--replace"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    a = _read_manifest(Path("projects/tf.json"))["actions"][0]
    assert a["type"] == "ability" and a["id"] == "ability.tiger_fury" and a["kind"] == "ja"
    assert a["target"] == {"animation": "auto", "subdir": 20}
    assert (Path("projects/resources") / a["resources"]["recipe"]).is_file()

    # A re-prepare keeps the recorded allocation; explicit flags replace the target.
    m = _read_manifest(Path("projects/tf.json"))
    m["actions"][0]["result"] = {"kind": "ja", "animation": 340, "placements": []}
    Path("projects/tf.json").write_text(json.dumps(m), encoding="utf-8")
    r = CliRunner().invoke(group, ["prepare", str(rp), "--project", "tf", "--replace", "--animation", "345"],
                           catch_exceptions=False)
    assert r.exit_code == 0, r.output
    a = _read_manifest(Path("projects/tf.json"))["actions"][0]
    assert a["target"]["animation"] == 345 and a["result"]["animation"] == 340


def test_prepare_keeps_generator_and_curve_edits(game):
    # The recipe is copied whole into the project, so the edits it carries are what
    # `dats build` composes and publishes.
    from xi.dats.xi_dats import _read_manifest, group
    root, rp = game
    rp.write_text(json.dumps(EXAMPLES[1]), encoding="utf-8")
    r = CliRunner().invoke(group, ["prepare", str(rp), "--project", "bf", "--replace"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    a = _read_manifest(Path("projects/bf.json"))["actions"][0]
    assert a["id"] == "ability.blue_fire" and a["kind"] == "spell"
    copied = json.loads((Path("projects/resources") / a["resources"]["recipe"]).read_text(encoding="utf-8"))
    assert copied["generators"] == EXAMPLES[1]["generators"] and copied["curves"] == EXAMPLES[1]["curves"]
    # A recipe whose edits do not validate is refused before anything is written.
    bad = dict(EXAMPLES[1], name="bad_fire", generators=[{"lane": "vfx", "ref": "g000", "edits": []}])
    rp.write_text(json.dumps(bad), encoding="utf-8")
    r = CliRunner().invoke(group, ["prepare", str(rp), "--project", "bad", "--replace"])
    assert r.exit_code != 0 and "generators[0]: edits must be a non-empty list" in r.output
    assert not Path("projects/bad.json").exists()


def test_prepare_stores_a_recipe_with_texture_files_self_contained(game):
    # A texture given as a PNG beside the recipe is inlined as its data URI in the copy
    # under projects/, so the action rebuilds without the file; a recipe that already
    # carries data URIs is copied byte for byte.
    from xi.dats.xi_dats import _read_manifest, group
    root, rp = game
    png = base64.b64decode(EXAMPLES[2]["textures"][0]["png"].split(",", 1)[1])
    (rp.parent / "gold.png").write_bytes(png)
    with_path = dict(EXAMPLES[2], textures=[dict(EXAMPLES[2]["textures"][0], png="gold.png")])
    rp.write_text(json.dumps(with_path), encoding="utf-8")
    r = CliRunner().invoke(group, ["prepare", str(rp), "--project", "gc", "--replace"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    a = _read_manifest(Path("projects/gc.json"))["actions"][0]
    stored = Path("projects/resources") / a["resources"]["recipe"]
    assert json.loads(stored.read_text(encoding="utf-8")) == EXAMPLES[2]
    rp.write_text(json.dumps(EXAMPLES[2]), encoding="utf-8")
    r = CliRunner().invoke(group, ["prepare", str(rp), "--project", "gc", "--replace"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert stored.read_bytes() == rp.read_bytes()


def test_build_places_and_registers_then_undo(game, monkeypatch):
    from xi.dats.xi_dats import _read_manifest, group
    root, rp = game
    _stub_compose(monkeypatch)
    runner = CliRunner()
    assert runner.invoke(group, ["prepare", str(rp), "--project", "tf", "--replace"], catch_exceptions=False).exit_code == 0

    r = runner.invoke(group, ["build", "tf", "--dry-run"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert "ja animation 339" in r.output and "ROM10/20/0.DAT" in r.output and "!injectaction 6 339" in r.output
    assert not (root / "ROM10" / "20" / "0.DAT").exists()
    assert _resolve(root, 4412 + 339) == (None, None)

    r = runner.invoke(group, ["build", "tf"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert (root / "ROM10" / "20" / "0.DAT").read_bytes()[:4] == b"tigr"
    assert _resolve(root, 4412 + 339)[0] == "ROM10/20/0.DAT"
    assert struct.unpack_from("<H", (root / "ROM10" / "FTABLE10.DAT").read_bytes(), (4412 + 339) * 2)[0] == (20 << 7)
    assert (root / "FTABLE.DAT.base").exists()
    a = _read_manifest(Path("projects/tf.json"))["actions"][0]
    assert a["result"]["animation"] == 339 and a["result"]["kind"] == "ja"
    assert a["result"]["placements"] == [{"race": None, "role": "body", "file_id": 4751, "dat": "ROM10/20/0.DAT"}]
    assert "dir" in a["result"]["targets"]
    assert Path("projects/abilities/tiger_fury/tiger_fury_339.sql").read_text(encoding="utf-8").startswith("--")
    assert a["result"]["server"] == str(Path("projects/abilities/tiger_fury/tiger_fury_339.sql"))

    # A rebuild keeps the slot (the stub reads it from the recorded result).
    r = runner.invoke(group, ["build", "tf"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert _read_manifest(Path("projects/tf.json"))["actions"][0]["result"]["animation"] == 339

    r = runner.invoke(group, ["changelog", "tf"], catch_exceptions=False)
    assert "ability.tiger_fury" in r.output and "4751" in r.output

    r = runner.invoke(group, ["undo", "tf", "--yes"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert _resolve(root, 4412 + 339) == (None, None)


def test_build_pivot_places_in_the_pivot_folder(game, tmp_path: Path, monkeypatch):
    """--pivot places the DAT and registers its file id in FFXI_PIVOT_DIR's ROM10 tables
    (never its main pair, which the client does not read), leaves the base install alone,
    skips the base-to-pivot table sync, and undo clears it there."""
    import xi.ftable.xi_expand as xe
    import xi.xi_config as cfg
    from xi.dats.xi_dats import _read_manifest, group
    root, rp = game
    pivot = tmp_path / "pivot"
    for d in (pivot, pivot / "ROM10"):
        d.mkdir(parents=True)
    (pivot / "FTABLE.DAT").write_bytes(b"\0" * (ENTRIES * 2))
    (pivot / "VTABLE.DAT").write_bytes(b"\0" * ENTRIES)
    (pivot / "ROM10" / "FTABLE10.DAT").write_bytes(b"\0" * (ENTRIES * 2))
    (pivot / "ROM10" / "VTABLE10.DAT").write_bytes(b"\0" * ENTRIES)
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", str(pivot), raising=False)
    monkeypatch.setattr(xe, "pivot_root", lambda: str(pivot))
    monkeypatch.setattr(xe, "sync_pivot_from_base",
                        lambda *a, **k: pytest.fail("a --pivot build must not copy the base tables over the pivot's"))
    _stub_compose(monkeypatch)
    runner = CliRunner()
    assert runner.invoke(group, ["prepare", str(rp), "--project", "tf", "--replace"], catch_exceptions=False).exit_code == 0

    r = runner.invoke(group, ["build", "tf", "--pivot"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert (pivot / "ROM10" / "20" / "0.DAT").read_bytes()[:4] == b"tigr"
    assert _resolve(pivot, 4412 + 339, rom=10)[0] == "ROM10/20/0.DAT"
    assert _resolve(pivot, 4412 + 339) == (None, None)                          # main pair untouched
    assert not (root / "ROM10" / "20" / "0.DAT").exists()
    assert _resolve(root, 4412 + 339) == (None, None) and _resolve(root, 4412 + 339, rom=10) == (None, None)
    assert _read_manifest(Path("projects/tf.json"))["actions"][0]["result"]["targets"] == ["pivot"]

    r = runner.invoke(group, ["undo", "tf", "--yes"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert not (pivot / "ROM10" / "20" / "0.DAT").exists()
    assert _resolve(pivot, 4412 + 339, rom=10) == (None, None)


def test_a_retail_sized_main_table_does_not_stop_a_rom10_build(game, monkeypatch):
    """A main FTABLE too small for the id (retail size, or reset by a launcher) is left
    alone: the ROM10 entry is the one the client resolves, and undo clears just that."""
    from xi.dats.xi_dats import group
    root, rp = game
    small = 4412 + 339                     # one short of the job ability's file id
    (root / "FTABLE.DAT").write_bytes(b"\0" * (small * 2))
    (root / "VTABLE.DAT").write_bytes(b"\0" * small)
    _stub_compose(monkeypatch)
    runner = CliRunner()
    assert runner.invoke(group, ["prepare", str(rp), "--project", "tf", "--replace"], catch_exceptions=False).exit_code == 0
    r = runner.invoke(group, ["build", "tf"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert _resolve(root, 4412 + 339, rom=10)[0] == "ROM10/20/0.DAT"
    assert (root / "FTABLE.DAT").read_bytes() == b"\0" * (small * 2)
    r = runner.invoke(group, ["undo", "tf", "--yes"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert _resolve(root, 4412 + 339, rom=10) == (None, None)


def test_custom_band_ws_from_other_motion_plans_placeholder_companions(root: Path, monkeypatch):
    """A weapon skill in the custom band whose composed body carries no waist companion (a
    motion with no +6 waist sibling): the slot has no retail companions, so each race's two
    waist ids get retail's placeholder, slot 0's companions (a 160-byte `dumm` DAT on every
    race), and are registered as every retail slot's are. On a retail slot (264) the slot's
    own placeholders stay. Reads the install's tables and DLL (`root`); compose is stubbed."""
    import xi.xi_config as cfg
    for key, value in (("FX_WS_BAND_FIRST", 272), ("FX_WS_BAND_BASE", 431344), ("FX_WS_BAND_SLOTS", 256)):
        monkeypatch.setattr(cfg, key, value)
    monkeypatch.setattr(ap, "compose", lambda recipe, race=None, kind=None: [
        ac.Composed(r, b"body" + bytes(60), [], {}, [], 0) for r in ac.RACE_NAMES])
    emote = {"name": "bow", "sources": {"motion": {"spec": "ROM/37/13.DAT", "routine": None}},
             "events": [{"from": "motion", "op": 5, "ref": "bow?", "start": 0, "dur": 60}]}
    assert ap._source_ws_animation(emote) is None
    # ROM10/127: a folder nothing publishes to, so the plan always finds 24 free numbers.
    p = ap.plan(emote, root, kind="ws", animation=300, force=True, subdir=127)
    by_role: dict = {}
    for f in p["files"]:
        by_role.setdefault(f["role"], []).append(f)
    assert [f["race"] for f in by_role["body"]] == list(ac.RACE_NAMES)
    for role, step in (("companion_a", 8), ("companion_b", 16)):
        assert [f["race"] for f in by_role[role]] == list(ac.RACE_NAMES)
        for body, f in zip(by_role["body"], by_role[role]):
            assert f["file_id"] == body["file_id"] + step and "composed" not in f
            data = Path(f["copy_from"]).read_bytes()
            assert len(data) == 160 and data[:4] == b"dumm", (f["race"], role, f["copy_from"])
    assert len({f["place"] for f in p["files"]}) == 24              # a DAT path of its own each
    p = ap.plan(emote, root, kind="ws", animation=264, force=True, subdir=127)
    assert {f["role"] for f in p["files"]} == {"body"}


def test_custom_band_ws_writes_the_motions_own_waist_companion(root: Path, monkeypatch):
    """When compose splits a waist companion (an emote's part-2 clips), Publish writes that
    companion to both waist ids — the motion's own waist, not a `dumm` placeholder — so the
    weapon skill's mid-body plays. Reads the install's tables/DLL (`root`); compose stubbed
    to return a body plus a companion payload per race."""
    import xi.xi_config as cfg
    for key, value in (("FX_WS_BAND_FIRST", 272), ("FX_WS_BAND_BASE", 431344), ("FX_WS_BAND_SLOTS", 256)):
        monkeypatch.setattr(cfg, key, value)
    waist = b"WST." + bytes(200)
    monkeypatch.setattr(ap, "compose", lambda recipe, race=None, kind=None: [
        ac.Composed(r, b"body" + bytes(60), [], {}, [], 0, companion=waist) for r in ac.RACE_NAMES])
    emote = {"name": "bow", "sources": {"motion": {"spec": "ROM/37/13.DAT", "routine": None}},
             "events": [{"from": "motion", "op": 5, "ref": "bow?", "start": 0, "dur": 60}]}
    p = ap.plan(emote, root, kind="ws", animation=300, force=True, subdir=127)
    by_role: dict = {}
    for f in p["files"]:
        by_role.setdefault(f["role"], []).append(f)
    for role, step in (("companion_a", 8), ("companion_b", 16)):
        assert [f["race"] for f in by_role[role]] == list(ac.RACE_NAMES)
        for body, f in zip(by_role["body"], by_role[role]):
            assert f["file_id"] == body["file_id"] + step
            assert "copy_from" not in f and f["composed_companion"] == waist, (f["race"], role)
    # The bytes land at the companion's placement when materialised.
    paths = ap.write_sources(emote, p)
    comp = [Path(pp) for pp, f in zip(paths, p["files"]) if "composed_companion" in f]
    assert comp and all(pp.read_bytes() == waist for pp in comp)


def test_pivot_refuses_a_rom_placement_that_needs_a_main_table_entry(game, tmp_path: Path, monkeypatch):
    """A ROM/ placement registers in the main FTABLE, which the client reads only from the
    base install: with --pivot the build refuses it before writing anything."""
    import xi.xi_config as cfg
    from xi.dats.xi_dats import group
    root, rp = game
    pivot = tmp_path / "pivot"
    (pivot / "ROM10").mkdir(parents=True)
    (pivot / "ROM10" / "FTABLE10.DAT").write_bytes(b"\0" * (ENTRIES * 2))
    (pivot / "ROM10" / "VTABLE10.DAT").write_bytes(b"\0" * ENTRIES)
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", str(pivot), raising=False)
    raw = tmp_path / "npc.dat"
    raw.write_bytes(b"npc0" + b"\0" * 60)
    Path("projects").mkdir(exist_ok=True)
    Path("projects/ent.json").write_text(json.dumps({"name": "ent", "actions": [{
        "id": "entity.npc", "type": "entity", "target": {"dat": "ROM/300/1.DAT"},
        "resources": {"raw_dat": str(raw)}, "model": {"kind": "entity", "model_id": 4000}}]}), encoding="utf-8")
    runner = CliRunner()
    for args in (["build", "ent", "--pivot", "--dry-run"], ["build", "ent", "--pivot"]):
        r = runner.invoke(group, args)
        assert r.exit_code != 0 and "never from the pivot folder" in r.output, r.output
    assert not (pivot / "ROM" / "300" / "1.DAT").exists()


# ── the publish folder (projects/abilities/<slug>/) ──────────────────────────────

PUBLISH = json.loads((REPO / "schema" / "ability_publish.json").read_text(encoding="utf-8"))
FOLDER = Path("projects/abilities/tiger_fury")             # under the game fixture's cwd


def test_publish_schema_examples_validate():
    for example in PUBLISH["examples"]:
        assert ap.validate_publish_record(example) == []
    # The validator knows the keys the schema lists, no more and no fewer.
    assert set(PUBLISH["required"]) == set(PUBLISH["properties"]) == set(ap._PUBLISH_KEYS)
    item = PUBLISH["properties"]["placements"]["items"]
    assert set(item["required"]) == set(item["properties"]) == set(ap._PUBLISH_PLACEMENT_KEYS)
    assert item["properties"]["role"]["enum"] == list(ap._PUBLISH_ROLES)
    assert PUBLISH["properties"]["schema"]["const"] == ap.PUBLISH_SCHEMA


def test_validate_publish_record_names_the_field():
    bad = dict(PUBLISH["examples"][1], extra=1, kind="mount", animation=5000, server="../x.sql",
               placements=[{"race": 1, "role": "hat", "file_id": -1, "dat": "../ROM10/20/0.DAT", "x": 0}])
    del bad["name"]
    errs = ap.validate_publish_record(bad)
    for says in ("unknown key 'extra'", "missing 'name'", "kind must be", "animation must be", "server must be",
                 "placements[0]: unknown key 'x'", "placements[0]: race must be", "placements[0]: role must be",
                 "placements[0]: file_id must be", "placements[0]: dat must be"):
        assert any(says in e for e in errs), (says, errs)
    assert ap.validate_publish_record(dict(PUBLISH["examples"][1], placements=[])) == [
        "placements must be a non-empty list"]
    assert ap.validate_publish_record(dict(PUBLISH["examples"][1], server=None)) == []


def _stub_compose_ws(monkeypatch):
    """Stand in for compose: one race's weapon skill — a body and two waist companions, each
    of its own bytes (with the animation in them), placed in the action's subdir from file 0.
    The file ids stay inside the synthetic tables."""
    def fake_plan(recipe, root, *, kind=None, animation=None, subdir=20, force=False, previous=None,
                  animation_from=None):
        anim = animation if animation is not None else ((previous or {}).get("animation") or 339)
        roles = (("body", 0), ("companion_a", 8), ("companion_b", 16))
        return {"root": root, "kind": "ws", "animation": anim, "subdir": subdir,
                "files": [{"race": "HumeMale", "role": role, "file_id": ap.ABILITY_FILE_OFFSET + anim + step,
                           "place": f"ROM10/{subdir}/{i}.DAT", "composed": object(), "current": None}
                          for i, (role, step) in enumerate(roles)]}

    def fake_write(recipe, plan):
        out = Path("exports") / "ability" / recipe["name"]
        out.mkdir(parents=True, exist_ok=True)
        paths = []
        for f in plan["files"]:
            p = out / f"{recipe['name']}.{f['role']}.DAT"
            p.write_bytes(f"{f['role']}:{plan['animation']}".encode() + b"\0" * 48)
            paths.append(p)
        return paths

    monkeypatch.setattr(ap, "plan", fake_plan)
    monkeypatch.setattr(ap, "write_sources", fake_write)


def _tree(folder: Path) -> dict:
    """``{folder-relative path: bytes}`` of every file under ``folder``."""
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def test_build_writes_the_publish_folder(game, monkeypatch):
    """A build leaves projects/abilities/<slug>/ holding the SQL, a byte-identical copy
    of every DAT it placed at the DAT's ROM path, and a placements.json that validates."""
    from xi.dats.xi_dats import _read_manifest, group
    root, rp = game
    _stub_compose_ws(monkeypatch)
    runner = CliRunner()
    assert runner.invoke(group, ["prepare", str(rp), "--project", "tf", "--replace"], catch_exceptions=False).exit_code == 0
    r = runner.invoke(group, ["build", "tf"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert f"folder: {FOLDER}" in r.output
    res = _read_manifest(Path("projects/tf.json"))["actions"][0]["result"]
    assert res["server"] == str(FOLDER / "tiger_fury_339.sql")
    assert set(_tree(FOLDER)) == {"tiger_fury_339.sql", "placements.json",
                                  "ROM10/20/0.DAT", "ROM10/20/1.DAT", "ROM10/20/2.DAT"}
    assert (FOLDER / "tiger_fury_339.sql").read_text(encoding="utf-8") == ap.server_snippet(EXAMPLE, "ws", 339) + "\n"
    record = json.loads((FOLDER / "placements.json").read_text(encoding="utf-8"))
    assert ap.validate_publish_record(record) == []
    assert record == {"schema": "xi.ability-publish.v1", "id": "ability.tiger_fury", "name": "tiger_fury",
                      "kind": "ws", "animation": 339, "placements": res["placements"],
                      "server": "tiger_fury_339.sql"}
    assert len(record["placements"]) == 3
    for p in record["placements"]:
        assert (FOLDER / p["dat"]).read_bytes() == (root / p["dat"]).read_bytes()


def test_republish_mirrors_the_new_publish_and_keeps_the_users_files(game, monkeypatch):
    """`xi ability publish` again at another animation and folder (the alias builds through
    the same path): the folder ends up with only the new publish's SQL and DATs — the old
    ones are found from the previous placements.json — while files the user put there stay,
    and the flat SQL an older build wrote beside the folders is left where it is."""
    from xi.ability.cli import group as ability
    root, rp = game
    _stub_compose_ws(monkeypatch)
    runner = CliRunner()
    r = runner.invoke(ability, ["publish", str(rp), "--project", "tf"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert set(_tree(FOLDER)) == {"tiger_fury_339.sql", "placements.json",
                                  "ROM10/20/0.DAT", "ROM10/20/1.DAT", "ROM10/20/2.DAT"}
    flat = Path("projects/server/abilities/tiger_fury_338.sql")     # where xi-tools <= 1.9 wrote it
    flat.parent.mkdir(parents=True, exist_ok=True)
    flat.write_text("-- an older publish\n", encoding="utf-8")
    (FOLDER / "notes.txt").write_text("mine\n", encoding="utf-8")
    (FOLDER / "tiger_fury_notes.sql").write_text("-- mine too\n", encoding="utf-8")

    r = runner.invoke(ability, ["publish", str(rp), "--project", "tf", "--animation", "340", "--subdir", "21"],
                      catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert set(_tree(FOLDER)) == {"tiger_fury_340.sql", "placements.json", "notes.txt", "tiger_fury_notes.sql",
                                  "ROM10/21/0.DAT", "ROM10/21/1.DAT", "ROM10/21/2.DAT"}
    assert not (FOLDER / "ROM10" / "20").exists()           # emptied by the move, so removed
    record = json.loads((FOLDER / "placements.json").read_text(encoding="utf-8"))
    assert ap.validate_publish_record(record) == []
    assert record["animation"] == 340 and record["server"] == "tiger_fury_340.sql"
    for p in record["placements"]:
        assert p["dat"].startswith("ROM10/21/")
        assert (FOLDER / p["dat"]).read_bytes() == (root / p["dat"]).read_bytes()
        assert (FOLDER / p["dat"]).read_bytes().startswith(f"{p['role']}:340".encode())
    assert (FOLDER / "notes.txt").read_text(encoding="utf-8") == "mine\n"
    assert flat.read_text(encoding="utf-8") == "-- an older publish\n"


def test_a_build_into_two_targets_copies_each_dat_once(game, tmp_path: Path, monkeypatch):
    """build_cmd places an action into each target in turn (the target root set, then the
    builder). Each pass writes the publish folder; the second finds every copy already
    holding its bytes, so no DAT is copied twice."""
    import xi.xi_config as cfg
    from xi.dats import xi_dats as xd
    root, rp = game
    pivot = tmp_path / "pivot"
    for d in (pivot, pivot / "ROM10"):
        d.mkdir(parents=True)
    (pivot / "FTABLE.DAT").write_bytes(b"\0" * (ENTRIES * 2))
    (pivot / "VTABLE.DAT").write_bytes(b"\0" * ENTRIES)
    (pivot / "ROM10" / "FTABLE10.DAT").write_bytes(b"\0" * (ENTRIES * 2))
    (pivot / "ROM10" / "VTABLE10.DAT").write_bytes(b"\0" * ENTRIES)
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", str(pivot), raising=False)
    _stub_compose_ws(monkeypatch)
    copies: list = []
    real = ap._write_bytes_if_changed

    def spy(dest, data):
        wrote = real(dest, data)
        if wrote:
            copies.append(Path(dest).relative_to(FOLDER).as_posix())
        return wrote

    monkeypatch.setattr(ap, "_write_bytes_if_changed", spy)
    r = CliRunner().invoke(xd.group, ["prepare", str(rp), "--project", "tf", "--replace"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    manifest = Path("projects/tf.json")
    data = xd._read_manifest(manifest)
    try:
        for target in (pivot, root):
            xd._set_target_root(target)
            res = xd._build_ability(data["actions"][0], manifest, data)
    finally:
        xd._set_target_root(None)
    assert sorted(copies) == ["ROM10/20/0.DAT", "ROM10/20/1.DAT", "ROM10/20/2.DAT"]
    for p in res["placements"]:
        assert (pivot / p["dat"]).read_bytes() == (root / p["dat"]).read_bytes() == (FOLDER / p["dat"]).read_bytes()


def test_dry_run_writes_no_publish_folder(game, monkeypatch):
    """--dry-run names the folder it would write and writes nothing to it — not on a first
    publish, and not over an earlier publish's folder at another animation."""
    from xi.dats.xi_dats import group
    root, rp = game
    _stub_compose_ws(monkeypatch)
    runner = CliRunner()
    assert runner.invoke(group, ["prepare", str(rp), "--project", "tf", "--replace"], catch_exceptions=False).exit_code == 0
    r = runner.invoke(group, ["build", "tf", "--dry-run"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert f"folder: {FOLDER}" in r.output and str(FOLDER / "tiger_fury_339.sql") in r.output
    assert not FOLDER.parent.exists()

    assert runner.invoke(group, ["build", "tf"], catch_exceptions=False).exit_code == 0
    before = _tree(FOLDER)
    r = runner.invoke(group, ["prepare", str(rp), "--project", "tf", "--replace", "--animation", "345",
                              "--subdir", "22"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    r = runner.invoke(group, ["build", "tf", "--dry-run"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert str(FOLDER / "tiger_fury_345.sql") in r.output
    assert _tree(FOLDER) == before


@pytest.mark.parametrize("bad", ["ability.C:", "ability./tmp", "ability.D:foo", "ability.//srv/share",
                                 "ability.love.", "ability.a/b", "ability.Love", "ability._x", "ability.x\n"])
def test_publish_folder_takes_only_a_plain_slug(bad):
    """The folder is named by the action id's last part. One that is empty, rooted,
    drive-qualified or nested would move the folder (and what a publish writes and removes
    in it) out of projects/abilities, so it is refused."""
    import click
    with pytest.raises(click.ClickException, match="name its publish folder"):
        ap.publish_folder(bad)
    assert ap.PUBLISH_ROOT == Path("projects") / "abilities"
    assert ap.publish_folder("ability.tiger_fury") == ap.PUBLISH_ROOT / "tiger_fury"
    assert ap.publish_folder("ability.a-b2") == ap.PUBLISH_ROOT / "a-b2"


def test_an_id_that_cannot_name_the_folder_fails_before_placing(game, monkeypatch):
    from xi.dats.xi_dats import group
    root, rp = game
    _stub_compose_ws(monkeypatch)
    runner = CliRunner()
    r = runner.invoke(group, ["prepare", str(rp), "--project", "tf", "--replace", "--id", "ability.love."],
                      catch_exceptions=False)
    assert r.exit_code == 0, r.output
    for extra in (["--dry-run"], []):
        r = runner.invoke(group, ["build", "tf", *extra])
        assert r.exit_code != 0 and "name its publish folder" in r.output, r.output
    assert _resolve(root, 4412 + 339, rom=10) == (None, None)
    assert not (root / "ROM10" / "20").exists() and not ap.PUBLISH_ROOT.exists()


def test_a_sql_resaved_in_another_encoding_is_rewritten(game, monkeypatch):
    """The user edits the SQL in the folder and saves it as ANSI: the next build rewrites it
    instead of failing to read it, and records its result."""
    from xi.dats.xi_dats import _read_manifest, _write_manifest, group
    root, rp = game
    _stub_compose_ws(monkeypatch)
    runner = CliRunner()
    assert runner.invoke(group, ["prepare", str(rp), "--project", "tf", "--replace"], catch_exceptions=False).exit_code == 0
    assert runner.invoke(group, ["build", "tf"], catch_exceptions=False).exit_code == 0
    sql = FOLDER / "tiger_fury_339.sql"
    sql.write_bytes(sql.read_bytes() + "-- café\n".encode("cp1252"))
    manifest = Path("projects/tf.json")
    data = _read_manifest(manifest)
    data["actions"][0].pop("result")
    _write_manifest(manifest, data)
    r = runner.invoke(group, ["build", "tf"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert _read_manifest(manifest)["actions"][0]["result"]["animation"] == 339
    assert sql.read_bytes() == (ap.server_snippet(EXAMPLE, "ws", 339) + "\n").encode("utf-8")


def test_a_folder_that_cannot_be_written_warns_and_the_build_is_recorded(game, monkeypatch):
    """The DATs are placed and registered before the folder is written, so a copy that fails
    (read-only, in use) is a warning: the build finishes and its result is what undo and the
    next build go by."""
    from xi.ability.cli import group as ability
    from xi.dats.xi_dats import _read_manifest
    root, rp = game
    _stub_compose_ws(monkeypatch)
    runner = CliRunner()
    assert runner.invoke(ability, ["publish", str(rp), "--project", "tf"], catch_exceptions=False).exit_code == 0

    def refuse(dest, data):
        raise PermissionError(13, "Access is denied", str(dest))

    monkeypatch.setattr(ap, "_write_bytes_if_changed", refuse)
    r = runner.invoke(ability, ["publish", str(rp), "--project", "tf", "--animation", "340", "--subdir", "21"],
                      catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert "publish folder not updated" in r.output
    res = _read_manifest(Path("projects/tf.json"))["actions"][0]["result"]
    assert res["animation"] == 340 and all(p["dat"].startswith("ROM10/21/") for p in res["placements"])
    assert _resolve(root, 4412 + 340, rom=10)[0] == "ROM10/21/0.DAT"


# ── the viewer list ──────────────────────────────────────────────────────────────

def test_update_abilities_keeps_real_names(tmp_path: Path, monkeypatch):
    from xi.ability import xi_catalog
    from xi.mv import update_lists as ul
    lists = tmp_path / "lists"
    lists.mkdir()
    (lists / "abilities.json").write_text(json.dumps({"races": [], "entries": [
        {"spec": "ja:5", "kind": "ja", "name": "Provoke", "names": ["Provoke"], "path": "ROM/1/1.DAT"},
        {"spec": "ja:6", "kind": "ja", "name": "Gone", "names": ["Gone"], "path": "ROM/1/2.DAT"},
    ]}), encoding="utf-8")
    fresh = [{"spec": "ja:5", "kind": "ja", "name": "ability anim 5", "names": [], "path": "ROM/1/1.DAT", "gens": []},
             {"spec": "ja:7", "kind": "ja", "name": "Berserk", "names": ["Berserk"], "path": "ROM/1/3.DAT", "gens": []}]
    monkeypatch.setattr(xi_catalog, "build_catalog", lambda kinds=("ja", "spell", "ws"), echo=None: [dict(e) for e in fresh])
    rep = ul.update_abilities(lists)
    assert rep["wrote"] and rep["added"] == 1 and rep["removed"] == 1 and rep["names_kept"] == 1
    data = json.loads((lists / "abilities.json").read_text(encoding="utf-8"))
    by = {e["spec"]: e for e in data["entries"]}
    assert by["ja:5"]["name"] == "Provoke" and by["ja:7"]["name"] == "Berserk" and "ja:6" not in by
    assert "\n" not in (lists / "abilities.json").read_text(encoding="utf-8").strip()


# ── mix files (*.mix.json, formerly *.recipe.json) ───────────────────────────────

def test_a_legacy_recipe_resource_still_builds_and_a_re_prepare_writes_a_mix_file(game, monkeypatch):
    from xi.dats.xi_dats import _read_manifest, group
    root, rp = game                                  # the fixture's recipe has the old name, *.recipe.json
    _stub_compose(monkeypatch)
    res_dir = Path("projects/resources/ability")
    res_dir.mkdir(parents=True)
    legacy = res_dir / "tiger_fury.recipe.json"
    legacy.write_bytes(rp.read_bytes())
    Path("projects/tf.json").write_text(json.dumps({"name": "tf", "actions": [{
        "id": "ability.tiger_fury", "type": "ability", "kind": "auto", "target": {"animation": "auto", "subdir": 20},
        "resources": {"recipe": "ability/tiger_fury.recipe.json"}, "server": {"emit": True}}]}), encoding="utf-8")
    runner = CliRunner()
    r = runner.invoke(group, ["build", "tf"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert _read_manifest(Path("projects/tf.json"))["actions"][0]["result"]["animation"] == 339
    mix = rp.with_name("tiger_fury.mix.json")
    mix.write_bytes(rp.read_bytes())
    r = runner.invoke(group, ["prepare", str(mix), "--project", "tf", "--replace"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    a = _read_manifest(Path("projects/tf.json"))["actions"][0]
    assert a["resources"]["recipe"] == "ability/tiger_fury.mix.json" and a["result"]["animation"] == 339
    assert (res_dir / "tiger_fury.mix.json").read_bytes() == mix.read_bytes()
    assert legacy.is_file()                          # left where it is; nothing points at it now


def test_a_check_mix_file_makes_the_same_action_id(game):
    """The action id comes from the recipe's name, not the file name: the viewer's Check
    writes check.<Name>.mix.json into the publish folder and prepares that."""
    from xi.dats.xi_dats import _read_manifest, group
    root, rp = game
    folder = ap.PUBLISH_ROOT / "love"
    folder.mkdir(parents=True)
    check = folder / "check.LOVE.mix.json"
    check.write_text(json.dumps(dict(EXAMPLE, name="LOVE")), encoding="utf-8")
    r = CliRunner().invoke(group, ["prepare", str(check), "--project", "LOVE", "--replace"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    a = _read_manifest(Path("projects/LOVE.json"))["actions"][0]
    assert a["id"] == "ability.love" and a["resources"]["recipe"] == "ability/love.mix.json"


def test_the_mix_file_in_the_publish_folder_is_prepared_and_kept(game, monkeypatch):
    """The viewer's Publish writes <Name>.mix.json into the publish folder and prepares it
    from there (its Check writes check.<Name>.mix.json beside it). No publish removes a mix
    file — not even one a hand-edited placements.json names — and the folder keeps the
    .applied.sql of the animation it holds, and only that one."""
    from xi.dats.xi_dats import _read_manifest, group
    root, rp = game
    _stub_compose_ws(monkeypatch)
    runner = CliRunner()
    assert runner.invoke(group, ["prepare", str(rp), "--project", "tf", "--replace"], catch_exceptions=False).exit_code == 0
    assert runner.invoke(group, ["build", "tf"], catch_exceptions=False).exit_code == 0
    mix = FOLDER / "tiger_fury.mix.json"
    mix.write_bytes(rp.read_bytes())
    extras = {"check.tiger_fury.mix.json": b"{}\n", "tiger_fury.recipe.json": b"{}\n",
              "tiger_fury_339.applied.sql": b"-- ran\n"}
    for name, data in extras.items():
        (FOLDER / name).write_bytes(data)
    r = runner.invoke(group, ["prepare", str(mix), "--project", "tf", "--replace"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    a = _read_manifest(Path("projects/tf.json"))["actions"][0]
    assert a["resources"]["recipe"] == "ability/tiger_fury.mix.json" and a["result"]["animation"] == 339
    assert (Path("projects/resources") / a["resources"]["recipe"]).read_bytes() == mix.read_bytes()
    record = json.loads((FOLDER / "placements.json").read_text(encoding="utf-8"))
    record["server"] = "tiger_fury.mix.json"                          # hand-edited: not a .sql, never taken
    record["placements"][0]["dat"] = "check.tiger_fury.mix.json"      # not a ROM path, never taken
    (FOLDER / "placements.json").write_text(json.dumps(record), encoding="utf-8")
    assert runner.invoke(group, ["build", "tf"], catch_exceptions=False).exit_code == 0
    assert set(_tree(FOLDER)) == {"tiger_fury.mix.json", *extras, "tiger_fury_339.sql", "placements.json",
                                  "ROM10/20/0.DAT", "ROM10/20/1.DAT", "ROM10/20/2.DAT"}
    r = runner.invoke(group, ["prepare", str(mix), "--project", "tf", "--replace", "--animation", "340",
                              "--subdir", "21"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert runner.invoke(group, ["build", "tf"], catch_exceptions=False).exit_code == 0
    assert set(_tree(FOLDER)) == {"tiger_fury.mix.json", "check.tiger_fury.mix.json", "tiger_fury.recipe.json",
                                  "tiger_fury_340.sql", "placements.json",
                                  "ROM10/21/0.DAT", "ROM10/21/1.DAT", "ROM10/21/2.DAT"}
    assert mix.read_bytes() == rp.read_bytes()
    assert ap.validate_publish_record(json.loads((FOLDER / "placements.json").read_text(encoding="utf-8"))) == []


def test_the_wizard_lists_mix_files_and_legacy_recipes(tmp_path: Path):
    from xi.dats.xi_dats import _mix_files
    d = tmp_path / "ability"
    (d / "mixer").mkdir(parents=True)
    for n in ("a.mix.json", "a.recipe.json", "b.recipe.json", "mixer/c.mix.json", "mixer/notes.json"):
        (d / n).write_text("{}", encoding="utf-8")
    assert _mix_files(d) == sorted([d / "a.mix.json", d / "b.recipe.json", d / "mixer" / "c.mix.json"])
    assert _mix_files(tmp_path / "missing") == []


def test_server_snippet_keeps_animation_time_and_names_ws_widen():
    for kind in ("spell", "ja"):
        sql = ap.server_snippet(dict(EXAMPLE, name="LOVE"), kind, 339)
        assert "animationTime = " not in sql and "WHERE name = 'love'" in sql and "--clone-from" in sql
        assert "INSERT INTO" not in sql
    ws = ap.server_snippet(EXAMPLE, "ws", 300)
    assert "xi server ws-widen" in ws and "UPDATE weapon_skills SET animation = 300" in ws
    assert "one-line cpp-patch" not in ws

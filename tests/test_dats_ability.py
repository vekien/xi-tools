"""The ``ability`` action of ``xi dats`` (xi.dats.xi_dats): recipe validation against
schema/ability_recipe.json, ``prepare`` turning a recipe into an action, and ``build``
placing the planned DATs and registering their file ids — driven with a synthetic
game folder (empty tables) and a stubbed compose, so no install is needed.

The composition itself is covered by tests/test_ability_compose.py."""
import json
import struct
from pathlib import Path

import pytest
from click.testing import CliRunner

from xi.ability import xi_compose as ac
from xi.ability import xi_publish as ap

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = json.loads((REPO / "schema" / "ability_recipe.json").read_text(encoding="utf-8"))["examples"][0]


# ── recipe schema ────────────────────────────────────────────────────────────────

def test_schema_example_validates():
    assert ac.validate_recipe(EXAMPLE) == []


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


def test_validate_recipe_accepts_string_sources_and_hex_ops():
    r = {"name": "x", "sources": {"motion": "ws:1:HumeMale"},
         "events": [{"from": "motion", "op": "0x05", "ref": "b00?", "start": 0, "dur": 10}]}
    assert ac.validate_recipe(r) == []


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
    with pytest.raises(Exception):
        ap.infer_kind({"name": "w", "sources": {"motion": {"spec": "ws:1"}}, "events": []}, kind="ja")


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
    def fake_plan(recipe, root, *, kind=None, animation=None, subdir=20, force=False, previous=None):
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
    assert Path("projects/server/abilities/tiger_fury_339.sql").read_text(encoding="utf-8").startswith("--")

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

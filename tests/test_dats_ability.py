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
    import xi.xi_config as cfg
    monkeypatch.setattr(cfg, "FFXI_DIR", str(root))
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", None, raising=False)
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


def _resolve(root: Path, file_id: int):
    from xi.ftable.xi_core import resolve_dat
    return resolve_dat((root / "FTABLE.DAT").read_bytes(), (root / "VTABLE.DAT").read_bytes(), file_id)


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

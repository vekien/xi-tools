"""The ``ui`` action of ``xi dats`` (xi.dats.xi_tables): a table with some entries
changed, written at its own ROM path into the build target — a d_msg string
table, an item table, a zone's dialog table. On a synthetic game folder and a
synthetic pivot folder; the retail passthrough test alone needs an install."""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from xi.common import xi_dmsg as D
from xi.dats import xi_tables as T
from xi.dialog import xi_dialog as XD
from xi.menu import xi_menu_table as MT
from xi.ui.items.xi_parser import ItemDat, _encrypt, _read_strings, _resolve_text_offset, build_record

from test_menu_table import install

NAMES = MT.KINDS["spell"].names["en"]        # a fixed-stride d_msg table install() writes
ITEMS = "ROM/118/106.DAT"                    # Items_1: ids from 0, general layout
DIALOG = "ROM/25/52.DAT"


def _items_dat(n: int) -> bytes:
    return _encrypt(b"".join(build_record({"name": f"Item {i}", "level": i}, 0, "legacy") for i in range(n)))


def _dialog_dat(lines) -> bytes:
    return XD.build_container([XD.encode_event_string(s) + b"\x00" for s in lines], True)


def _write(root: Path, rom: str, data: bytes) -> Path:
    p = root / Path(*rom.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


@pytest.fixture
def game(tmp_path: Path, monkeypatch):
    """A synthetic FFXI_DIR (string tables, an item table, a dialog table), an empty
    FFXI_PIVOT_DIR, and the cwd where projects/ lands."""
    import xi.ftable.xi_expand as xe
    import xi.xi_config as cfg
    root = install(tmp_path / "game")
    _write(root, ITEMS, _items_dat(4))
    _write(root, DIALOG, _dialog_dat(["Line 0", "Line 1", "Line 2"]))
    pivot = tmp_path / "pivot"
    pivot.mkdir()
    monkeypatch.setattr(cfg, "FFXI_DIR", str(root))
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", str(pivot))
    monkeypatch.setattr(xe, "FFXI_DIR", str(root))
    monkeypatch.setattr(xe, "FFXI_PIVOT_DIR", str(pivot))
    monkeypatch.chdir(tmp_path)
    return root, pivot


def _project(actions: list, edits: dict) -> Path:
    """Write projects/t.json and each edit list under projects/resources/ui/."""
    for name, rows in edits.items():
        p = Path("projects/resources/ui") / f"{name}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rows), encoding="utf-8")
    m = Path("projects/t.json")
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(json.dumps({"schema": "xi.dats.v1", "name": "t", "version": 1, "actions": actions}),
                 encoding="utf-8")
    return m


def _ui(aid: str, dat: str, category: str, edits: str, **more) -> dict:
    a = {"id": aid, "type": "ui", "target": {"dat": dat, "category": category},
         "resources": {"json": f"ui/{edits}.json"}}
    a.update(more)
    return a


def _build(*args):
    from xi.dats.xi_dats import group
    r = CliRunner().invoke(group, ["build", "--project", "t", *args], catch_exceptions=False)
    return r


def _result(aid: str) -> dict:
    m = json.loads(Path("projects/t.json").read_text(encoding="utf-8"))
    return next(a for a in m["actions"] if a["id"] == aid).get("result") or {}


# ── strings ───────────────────────────────────────────────────────────────────

def test_strings_edit_and_grow_into_the_pivot(game):
    root, pivot = game
    _project([_ui("ui.names", NAMES, "strings", "names")],
             {"names": [{"id": 2, "text": "Fire II"}, {"id": 11, "text": "New Spell"}]})
    r = _build("--pivot")
    assert r.exit_code == 0, r.output
    assert "strings " + NAMES + ": 2 entries, grown to 12" in r.output

    src = D.parse((root / Path(*NAMES.split("/"))).read_bytes())
    out = D.parse((pivot / Path(*NAMES.split("/"))).read_bytes())
    assert len(src.blocks) == 8 and len(out.blocks) == 12
    assert D.get_text(out.blocks[2], 0) == "Fire II"
    assert D.get_text(out.blocks[11], 0) == "New Spell"
    # untouched entries are carried over byte for byte; the gap is empty fillers
    for i in (0, 1, 3, 4, 5, 6, 7):
        assert out.blocks[i] == src.blocks[i]
    assert all(D._parse_block(out.blocks[i]) == [] for i in (8, 9, 10))
    # a grown block keeps the shape of the table's own blocks
    assert len(out.blocks[11]) == src.stride

    res = _result("ui.names")
    assert res["entries"] == 2 and res["grown_to"] == 12 and res["created"] is True
    assert res["targets"] == ["pivot"] and res["dat"] == NAMES


def test_no_edits_reproduce_the_source_exactly(game):
    root, pivot = game
    _project([_ui("ui.names", NAMES, "strings", "none")], {"none": []})
    assert _build("--pivot").exit_code == 0
    assert (pivot / Path(*NAMES.split("/"))).read_bytes() == (root / Path(*NAMES.split("/"))).read_bytes()


def test_actions_layer_on_one_table(game):
    root, pivot = game
    _project([_ui("ui.a", NAMES, "strings", "a"), _ui("ui.b", NAMES, "strings", "b")],
             {"a": [{"id": 1, "text": "From A"}], "b": [{"id": 3, "text": "From B"}]})
    assert _build("--pivot").exit_code == 0
    out = D.parse((pivot / Path(*NAMES.split("/"))).read_bytes())
    assert D.get_text(out.blocks[1], 0) == "From A" and D.get_text(out.blocks[3], 0) == "From B"
    # the second action found the first's copy in the target, so it did not create it
    assert _result("ui.a")["created"] is True and _result("ui.b")["created"] is False


def test_grow_can_be_switched_off(game):
    _project([_ui("ui.names", NAMES, "strings", "far", options={"grow": False})],
             {"far": [{"id": 40, "text": "Too far"}]})
    r = _build("--pivot")
    assert r.exit_code != 0 and "options.grow" in r.output


def test_dry_run_writes_nothing(game):
    root, pivot = game
    _project([_ui("ui.names", NAMES, "strings", "names")], {"names": [{"id": 2, "text": "Fire II"}]})
    r = _build("--pivot", "--dry-run")
    assert r.exit_code == 0, r.output
    assert not (pivot / Path(*NAMES.split("/"))).exists()
    assert "result" not in json.loads(Path("projects/t.json").read_text(encoding="utf-8"))["actions"][0]


# ── items ─────────────────────────────────────────────────────────────────────

def _name(dat: ItemDat, idx: int) -> str:
    rec = dat.record(idx)
    off = _resolve_text_offset(rec, 0, dat.format)
    strings = _read_strings(rec, off) if off is not None else []
    return strings[0] if strings else ""


def test_items_patch_existing_and_add_past_the_end(game):
    root, pivot = game
    _project([_ui("ui.items", ITEMS, "items", "items")],
             {"items": [{"id": 1, "name": "Renamed"},
                        {"id": 6, "name": "Brand New", "stack": 12, "flags_decoded": ["rare", "ex"]}]})
    r = _build("--pivot")
    assert r.exit_code == 0, r.output
    dat = ItemDat.load(pivot / Path(*ITEMS.split("/")))
    src = ItemDat.load(root / Path(*ITEMS.split("/")))
    assert dat.count == 7 and src.count == 4
    assert _name(dat, 1) == "Renamed"
    assert dat.record(2) == src.record(2) and dat.record(3) == src.record(3)
    assert _name(dat, 4) == "." and _name(dat, 5) == "."      # fillers read as free slots
    assert _name(dat, 6) == "Brand New"
    from xi.ui.items.xi_parser import _parse_record, decode_flags
    new = _parse_record(6, dat.record(6), None, 0, "", fmt=dat.format)
    assert new.stack == 12 and sorted(decode_flags(new.flags)) == ["ex", "rare"]
    assert _result("ui.items")["grown_to"] == 7


def test_items_id_below_the_table_is_refused(game):
    root, _pivot = game
    _write(root, "ROM/118/107.DAT", _items_dat(2))                # Consumable: ids from 4096
    _project([_ui("ui.items", "ROM/118/107.DAT", "items", "low")], {"low": [{"id": 1, "name": "x"}]})
    r = _build("--pivot")
    assert r.exit_code != 0 and "below" in r.output


def test_items_unknown_table_is_refused(game):
    _project([_ui("ui.items", "ROM/1/2.DAT", "items", "x")], {"x": [{"id": 0, "name": "x"}]})
    _write(game[0], "ROM/1/2.DAT", _items_dat(1))
    r = _build("--pivot")
    assert r.exit_code != 0 and "not an item table" in r.output


# ── dialog ────────────────────────────────────────────────────────────────────

def test_dialog_text_raw_and_append(game):
    root, pivot = game
    _project([_ui("ui.dialog", DIALOG, "dialog", "dialog")],
             {"dialog": [{"id": 1, "text": "Hello there.\\v"},
                         {"id": 4, "raw_hex": "41 42"}]})
    r = _build("--pivot")
    assert r.exit_code == 0, r.output
    data = (pivot / Path(*DIALOG.split("/"))).read_bytes()
    entries, obf = XD.parse_event_message(data)
    assert obf is True and len(entries) == 5
    assert entries[0].text == "Line 0" and entries[2].text == "Line 2"
    assert entries[1].text.startswith("Hello there.")
    assert entries[3].raw_hex == "" and entries[4].raw_hex == "41 42"
    assert _result("ui.dialog")["grown_to"] == 5


# ── byte-exact forms, fillers, and the menu table ─────────────────────────────

def test_strings_fill_and_block_hex(game):
    root, pivot = game
    src = D.parse((root / Path(*NAMES.split("/"))).read_bytes())
    exact = bytes(src.blocks[5])
    _project([_ui("ui.names", NAMES, "strings", "names", options={"fill": "."})],
             {"names": [{"id": 10, "text": "Tenth"}, {"id": 1, "block_hex": exact.hex()}]})
    assert _build("--pivot").exit_code == 0
    out = D.parse((pivot / Path(*NAMES.split("/"))).read_bytes())
    assert len(out.blocks) == 11 and D.get_text(out.blocks[10], 0) == "Tenth"
    assert all(D.get_text(out.blocks[i], 0) == "." for i in (8, 9))     # fillers carry the fill text
    assert bytes(out.blocks[1]) == exact                                # block_hex lands verbatim


def test_items_record_hex_lands_verbatim(game):
    root, pivot = game
    src = ItemDat.load(root / Path(*ITEMS.split("/")))
    rec = bytearray(src.record(2))
    rec[0x40:0x44] = b"\xde\xad\xbe\xef"                                # a field no layout names
    _project([_ui("ui.items", ITEMS, "items", "items")],
             {"items": [{"id": 2, "record_hex": bytes(rec).hex()}, {"id": 9, "record_hex": bytes(rec).hex()}]})
    assert _build("--pivot").exit_code == 0
    out = ItemDat.load(pivot / Path(*ITEMS.split("/")))
    assert out.record(2) == bytes(rec) and out.record(9) == bytes(rec) and out.count == 10
    _project([_ui("ui.items", ITEMS, "items", "short")], {"short": [{"id": 2, "record_hex": "0000"}]})
    r = _build("--pivot")
    assert r.exit_code != 0 and "record_hex" in r.output


def test_items_fill_record_gets_each_slots_id(game):
    root, pivot = game
    src = ItemDat.load(root / Path(*ITEMS.split("/")))
    placeholder = bytearray(src.record(3))
    placeholder[0x40:0x44] = b"\xca\xfe\xf0\x0d"                        # e.g. a shared "no image" icon
    _project([_ui("ui.items", ITEMS, "items", "items", options={"fill": {"record_hex": bytes(placeholder).hex()}})],
             {"items": [{"id": 8, "name": "Eighth"}]})
    assert _build("--pivot").exit_code == 0
    out = ItemDat.load(pivot / Path(*ITEMS.split("/")))
    assert out.count == 9 and _name(out, 8) == "Eighth"
    for i in (4, 5, 6, 7):
        rec = out.record(i)
        assert int.from_bytes(rec[:4], "little") == i and rec[4:] == bytes(placeholder[4:])


def test_dialog_gap_hex_lands_verbatim(game):
    root, pivot = game
    blobs, _ = XD.raw_entry_blobs((root / Path(*DIALOG.split("/"))).read_bytes())
    gap = b"Odd end\x00\x07"                                             # a gap that does not end in NUL
    _project([_ui("ui.dialog", DIALOG, "dialog", "dialog")],
             {"dialog": [{"id": 1, "gap_hex": gap.hex()}, {"id": 4, "gap_hex": blobs[0].hex()}]})
    assert _build("--pivot").exit_code == 0
    out, _ = XD.raw_entry_blobs((pivot / Path(*DIALOG.split("/"))).read_bytes())
    assert out[1] == gap and out[4] == blobs[0] and out[2] == blobs[2] and len(out) == 5


def test_menu_records_fields_hex_and_growth(game):
    root, pivot = game
    menu_rom = MT.MENU_DAT
    src = MT.parse((root / Path(*menu_rom.split("/"))).read_bytes())
    exact = src.records("command")[1]
    _project([_ui("ui.menu", menu_rom, "menu", "menu", options={"records": {"spell": 16}})],
             {"menu": [{"kind": "spell", "id": 3, "mp": 99, "levels": {"BLM": 40}},
                       {"kind": "command", "id": 9, "record_hex": exact.hex()},
                       {"kind": "spell", "id": 20, "record_hex": src.records("spell")[2].hex()}]})
    r = _build("--pivot")
    assert r.exit_code == 0, r.output
    out = MT.parse((pivot / Path(*menu_rom.split("/"))).read_bytes())
    assert out.count("spell") == 21 and out.count("command") == 10      # 16 asked, 21 needed; 10 by the edit
    f = MT.read_fields("spell", out.records("spell")[3])
    assert f["mp"] == 99 and f["levels"].get("BLM") == 40
    assert out.records("command")[9] == exact and out.records("spell")[20] == src.records("spell")[2]
    assert all(MT.is_empty(r_) for r_ in out.records("spell")[8:20])      # growth is empty records
    assert [s.tag for s in out.sections] == [s.tag for s in src.sections]  # other sections carried over
    assert _result("ui.menu")["grown_to"] == {"spell": 21, "command": 10}


# ── undo / prepare ────────────────────────────────────────────────────────────

def test_undo_deletes_what_the_build_created(game):
    from xi.dats.xi_dats import group
    root, pivot = game
    _project([_ui("ui.names", NAMES, "strings", "names")], {"names": [{"id": 2, "text": "Fire II"}]})
    assert _build("--pivot").exit_code == 0
    out = pivot / Path(*NAMES.split("/"))
    assert out.exists()
    r = CliRunner().invoke(group, ["undo", "t", "--yes"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert not out.exists() and not Path("projects/t.json").exists()


def test_prepare_from_a_bare_list_and_a_self_describing_file(game):
    from xi.dats.xi_dats import _read_manifest, group
    runner = CliRunner()
    bare = Path("exports/spell_names.json")
    bare.parent.mkdir(parents=True)
    bare.write_text(json.dumps([{"id": 2, "text": "Fire II"}]), encoding="utf-8")

    # a bare export needs to be told which table it is
    r = runner.invoke(group, ["prepare", str(bare), "--project", "p"], catch_exceptions=False)
    assert r.exit_code != 0 and "--type ui" in r.output
    r = runner.invoke(group, ["prepare", str(bare), "--project", "p", "--type", "ui",
                              "--target", NAMES, "--category", "strings"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    a = _read_manifest(Path("projects/p.json"))["actions"][0]
    assert a["type"] == "ui" and a["id"] == "ui.spell_names"
    assert a["target"] == {"dat": NAMES, "category": "strings"}
    assert (Path("projects/resources") / a["resources"]["json"]).is_file()

    # a self-describing file carries the table, and its edits inline
    described = Path("exports/rulude.json")
    described.write_text(json.dumps({"schema": "xi.ui.v1", "type": "ui",
                                     "target": {"dat": DIALOG, "category": "dialog"},
                                     "options": {"grow": False},
                                     "edits": [{"id": 0, "text": "Welcome."}]}), encoding="utf-8")
    r = runner.invoke(group, ["prepare", str(described), "--project", "p"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    b = _read_manifest(Path("projects/p.json"))["actions"][1]
    assert b["target"] == {"dat": DIALOG, "category": "dialog"} and b["options"] == {"grow": False}
    assert T.load_edits(Path("projects/resources") / b["resources"]["json"]) == [{"id": 0, "text": "Welcome."}]


# ── the real thing ────────────────────────────────────────────────────────────

def test_retail_tables_pass_through_unchanged(root):
    """With no edits, each builder reproduces the install's own bytes — the
    guarantee that lets a repository hold only the edits."""
    def pristine(rel: str) -> bytes:
        p = root / rel
        base = Path(str(p) + ".base")
        return (base if base.exists() else p).read_bytes()
    for rel, fn in ((NAMES, lambda d: T.apply_strings(d, [])),
                    (ITEMS, lambda d: T.apply_items(d, [], ITEMS)),
                    (DIALOG, lambda d: T.apply_dialog(d, [])),
                    (MT.MENU_DAT, lambda d: T.apply_menu(d, []))):
        if not (root / rel).exists():
            pytest.skip(f"{rel} not in this install")
        data = pristine(rel)
        out, n, grew = fn(data)
        assert out == data and n == 0 and grew is None, rel

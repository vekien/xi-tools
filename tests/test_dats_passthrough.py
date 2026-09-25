"""Passthrough: the tables ``xi dats`` edits in place come back byte for byte when nothing is asked
of them — on the real installs (FFXI_DIR, and FFXI_RETAIL_DIR when set; skipped without them).

Two checks, the second PR #15's: every table a database or zone action writes reads and rewrites
to the same bytes (item DATs, d_msg tables, the spell and command menu table, zone dialog and
event tables), and a build whose edits set values to what they already are changes no file. So a
build touches only what its edits name, and a project needs only its edits plus the install."""
import os
from pathlib import Path

import pytest

from xi.common import xi_dmsg as D
from xi.dialog import xi_dialog as XD
from xi.event import xi_event as core
from xi.menu import xi_menu_table as MT
from xi.mv.xi_database import DMSG_TABLES, ITEM_TABLES
from xi.ui.items.xi_parser import ItemDat

# Zones whose event and dialog tables are checked: the Jeuno cities, Qufim, Mog Garden (a zone
# past 255, whose tables sit in ROM2-9) and Western Adoulin.
ZONES = (243, 244, 245, 246, 126, 280, 256)


def _installs() -> list:
    out = []
    for key in ("FFXI_DIR", "FFXI_RETAIL_DIR"):
        d = os.environ.get(key)
        if d and Path(d).is_dir():
            out.append(pytest.param(Path(d), id=key))
    return out or [pytest.param(None, marks=pytest.mark.skip(reason="no FFXI install configured"))]


INSTALLS = _installs()


def _files(root: Path, rels) -> list[Path]:
    return [root / Path(*r.split("/")) for r in dict.fromkeys(rels) if (root / Path(*r.split("/"))).is_file()]


@pytest.mark.parametrize("root", INSTALLS)
def test_record_tables_rewrite_to_the_same_bytes(root):
    items = [r for _k, _l, parts in ITEM_TABLES for pair in parts for r in pair]
    for p in _files(root, items):
        data = p.read_bytes()
        assert ItemDat.load(p).encrypted() == data, p
    for p in _files(root, [r for _k, en, jp in DMSG_TABLES for r in (en, jp)]):
        data = p.read_bytes()
        assert D.serialize(D.parse(data)) == data, p
    menu = root / Path(*MT.MENU_DAT.split("/"))
    assert MT.verify_roundtrip(menu.read_bytes())


@pytest.mark.parametrize("root", INSTALLS)
def test_zone_tables_rewrite_to_the_same_bytes(root, monkeypatch):
    import xi.ftable.xi_core as fc
    import xi.xi_config as cfg
    from xi.dialog.xi_zone_dialog import dialog_rel
    from xi.event.xi_zone_events import event_rel
    monkeypatch.setattr(cfg, "FFXI_DIR", str(root))
    monkeypatch.setattr(fc, "FFXI_DIR", str(root), raising=False)
    fc.forget_tables()
    try:
        for zone in ZONES:
            for rel in (dialog_rel(root, zone, "en"), dialog_rel(root, zone, "jp")):
                if not rel or not (root / rel).is_file():
                    continue
                data = (root / rel).read_bytes()
                blobs, obf = XD.raw_entry_blobs(data)
                assert XD.build_container(blobs, obf) == data, (zone, rel)
            rel = event_rel(root, zone)
            if rel and (root / rel).is_file():
                data = (root / rel).read_bytes()
                assert core.build_event_dat(core.parse_raw_actors(data)) == data, (zone, rel)
    finally:
        fc.forget_tables()


@pytest.mark.parametrize("root", INSTALLS)
def test_an_edit_to_what_is_there_changes_nothing(root, monkeypatch):
    """A dry-run build of edits that restate the install's own values plans no file."""
    import xi.xi_config as cfg
    from xi.database import xi_build as DB
    from xi.dats import xi_stage
    monkeypatch.setattr(cfg, "FFXI_DIR", str(root))
    monkeypatch.setattr(cfg, "FFXI_PIVOT_DIR", None, raising=False)
    armor = DB.describe(root, "armor", 12579)
    cure = DB.describe(root, "spellData", 1)
    ki = DB.describe(root, "keyitems", 1)
    edits = [
        {"table": "armor", "id": 12579, "set": {"level": armor["fields"]["level"]},
         "strings": {"en": {"description": armor["strings"]["description"]}}, "server": False},
        {"table": "spellData", "id": 1, "set": {"mp": cure["fields"]["mp"], "levels": {
            k.split(".", 1)[1]: v for k, v in cure["fields"].items() if k.startswith("levels.")}}, "server": False},
        {"table": "keyitems", "id": 1, "strings": {"en": {"name": ki["strings"]["name"]}}},
    ]
    action = {"id": "database.passthrough", "type": "database", "edits": edits}
    with xi_stage.session(True):
        built = DB.build(action, root=root, target="dir", manifest={}, sql_path=None, project="passthrough",
                         dry_run=True)
    assert built["files"] == [] and built["records"] == []

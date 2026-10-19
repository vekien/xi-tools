"""The gear window `xi dats build` checks (xi.dats.xi_dats._gear_window): FFXiMain.dll's patch,
or, with a retail DLL, the FTABLE windows `xi ftable expand --no-dll` grew and linked (a client
whose gear groups cexidats patches at load). On synthetic tables, with no DLL in the folder."""
import xi.ftable.xi_core as fc
from xi.dats import xi_dats as xd
from xi.gear import xi_inject as gi

from test_ftable_expand_no_dll import RETAIL, install, run  # noqa: F401 — the fixture


def grow(root, entries):
    for rel in ("FTABLE.DAT", "VTABLE.DAT", "ROM2/FTABLE2.DAT", "ROM2/VTABLE2.DAT"):
        p = root / rel
        p.write_bytes(p.read_bytes() + b"\0" * (entries - RETAIL) * (2 if "FTABLE" in rel else 1))
    fc.forget_tables()


def expand_no_dll():
    r = run("--no-dll")
    assert r.exit_code == 0, r.output
    fc.forget_tables()


def gear_row(entries):
    return next(row for row in xd._expansion_report(entries) if row[0] == "gear")


def test_retail_has_no_gear_window(install):
    assert xd._gear_window() == (None, None) and xd._gear_expand_max() is None
    _, label, ok, detail = gear_row(RETAIL)
    assert not ok and "aren't linked" in detail and "--no-dll" in detail
    assert "Gear window: none" in xd._gear_tables_status()


def test_no_dll_expand_is_a_gear_window(install):
    expand_no_dll()
    assert xd._gear_window() == (gi.MAX_GEAR_MODELID, "links")
    _, label, ok, detail = gear_row(gi.gear_ftable_target(gi.MAX_GEAR_MODELID))
    assert ok and label == "Gear windows linked" and "cexidats" in detail
    assert "Gear window (FTABLE links" in xd._gear_tables_status()


def test_tables_larger_than_the_gear_target_keep_the_setting_window(install):
    """437,488 entries reads as a 4,294 window by size, past the 12-bit ceiling; the links sit
    in the MAX_GEAR_MODELID window `expand` wrote, so that is the one checked."""
    grow(install, 437_488)
    expand_no_dll()
    assert gi.current_expand_max() == 4294
    assert gi.linked_expand_max() == gi.MAX_GEAR_MODELID == xd._gear_expand_max()


def test_big_enough_without_the_links_is_not_a_window(install):
    grow(install, gi.gear_ftable_target(gi.MAX_GEAR_MODELID))
    assert gi.linked_expand_max() is None and xd._gear_window() == (None, None)


def test_the_dll_patch_comes_first(install, monkeypatch):
    expand_no_dll()
    monkeypatch.setattr(gi, "dll_expand_max", lambda: 2000)
    assert xd._gear_window() == (2000, "dll")
    assert gear_row(gi.gear_ftable_target(2000))[1] == "Gear DLL patched"

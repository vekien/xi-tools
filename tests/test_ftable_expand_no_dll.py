"""`xi ftable expand gear --no-dll` (xi.gear.xi_inject.expand_gear_tables): the gear windows
are grown and the retail armour originals linked into them, but FFXiMain.dll is never
written — for a client whose gear groups a plugin (cexidats) patches at load. On synthetic
tables; with no DLL in the folder the ported retail group tables stand in."""
import struct
from pathlib import Path

import pytest
from click.testing import CliRunner

from xi.gear import xi_inject as gi
from xi.gear.xi_core import BYTES_PER_TABLE, GROUPS_PER_SLOT, RACE_TABLES, SLOTS

RETAIL = 109_701


def _g5(race: str, slot: str) -> tuple[int, int]:
    off = SLOTS.index(slot) * GROUPS_PER_SLOT * 8 + 5 * 8
    return struct.unpack_from("<II", RACE_TABLES[race], off)


@pytest.fixture
def install(tmp_path: Path, monkeypatch):
    """A retail-sized main pair and ROM2 pair, with every armour G5 original registered."""
    import xi.ftable.xi_core as fc
    import xi.ftable.xi_expand as xe
    import xi.xi_config as cfg
    root = tmp_path / "game"
    (root / "ROM2").mkdir(parents=True)
    ft, vt = bytearray(RETAIL * 2), bytearray(RETAIL)
    for race in gi.RACES:
        for slot in gi.ARMOR_SLOTS:
            base, count = _g5(race, slot)
            for i in range(count):
                struct.pack_into("<H", ft, (base + i) * 2, 0x1234)
                vt[base + i] = 1
    for d, n in ((root, ""), (root / "ROM2", "2")):
        (d / f"FTABLE{n}.DAT").write_bytes(bytes(ft))
        (d / f"VTABLE{n}.DAT").write_bytes(bytes(vt))
    for mod in (cfg, gi, xe, fc):
        monkeypatch.setattr(mod, "FFXI_DIR", str(root), raising=False)
    for mod in (cfg, xe):
        monkeypatch.setattr(mod, "FFXI_PIVOT_DIR", "", raising=False)
    fc.forget_tables()
    monkeypatch.chdir(tmp_path)
    return root


def run(*args):
    return CliRunner().invoke(gi.gear_expand_cmd, list(args), catch_exceptions=False)


def links_in(root: Path) -> int:
    ft, vt = (root / "FTABLE.DAT").read_bytes(), (root / "VTABLE.DAT").read_bytes()
    base, count = _g5("HumeMale", "head")
    fid = gi.custom_fid("HumeMale", "head", gi.CUSTOM_MODEL_START["head"] - count, gi.MAX_MODELID_CEILING)
    return vt[fid] if fid < len(vt) and struct.unpack_from("<H", ft, fid * 2)[0] == 0x1234 else 0


def test_no_dll_grows_and_links_but_never_writes_the_dll(install):
    r = run("--no-dll")
    assert r.exit_code == 0, r.output
    target = gi.gear_ftable_target(gi.MAX_MODELID_CEILING)
    for rel in ("FTABLE.DAT", "ROM2/FTABLE2.DAT"):
        assert (install / rel).stat().st_size == target * 2
    assert links_in(install) == 1
    assert not (install / "FFXiMain.dll").exists() and "skipped (--no-dll)" in r.output
    r = run("--no-dll")                                                   # a second run: nothing to do
    assert r.exit_code == 0 and "Already set up" in r.output and "left alone (--no-dll)" in r.output


def test_tables_big_enough_without_the_links_still_get_them(install):
    big = 437_488                                                          # grown by something else
    for rel in ("FTABLE.DAT", "VTABLE.DAT", "ROM2/FTABLE2.DAT", "ROM2/VTABLE2.DAT"):
        p = install / rel
        p.write_bytes(p.read_bytes() + b"\0" * (big - RETAIL) * (2 if "FTABLE" in rel else 1))
    import xi.ftable.xi_core as fc
    fc.forget_tables()
    r = run("--no-dll")
    assert r.exit_code == 0, r.output
    assert "armour links aren't in the windows yet" in r.output and links_in(install) == 1
    assert (install / "FTABLE.DAT").stat().st_size == big * 2           # never shrunk


def test_a_pivot_band_tail_does_not_set_the_size(install, tmp_path, monkeypatch):
    """A pivot ROM10 pair grown to the animation-band ceiling (437,488) runs past its peers
    on purpose (the client plugin grows that table in memory): it counts as the band floor,
    so every table ends at the gear target, 423,152, like the rest of the client's."""
    import xi.ftable.xi_expand as xe
    import xi.xi_config as cfg
    pivot = tmp_path / "pivot"
    (pivot / "ROM10").mkdir(parents=True)
    (pivot / "ROM10" / "FTABLE10.DAT").write_bytes(b"\0" * 437_488 * 2)
    (pivot / "ROM10" / "VTABLE10.DAT").write_bytes(b"\0" * 437_488)
    for mod in (cfg, xe):
        monkeypatch.setattr(mod, "FFXI_PIVOT_DIR", str(pivot), raising=False)
    r = run("--no-dll")
    assert r.exit_code == 0, r.output
    target = gi.gear_ftable_target(gi.MAX_MODELID_CEILING)
    assert target == 423_152 == cfg.fx_band_floor()
    assert (install / "FTABLE.DAT").stat().st_size == target * 2
    assert (install / "ROM10" / "FTABLE10.DAT").stat().st_size == target * 2
    assert (pivot / "ROM10" / "FTABLE10.DAT").stat().st_size == 437_488 * 2    # the tail is kept

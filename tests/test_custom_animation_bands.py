"""Custom animation bands: allocating past the retail ceilings.

The client turns an animation number into a DAT file id with fixed arithmetic per
kind, and the ids that arithmetic reaches run out long before the 12-bit number
space does. A client-side plugin can patch the arithmetic so numbers at or above a
threshold resolve into a reserved region; these pin what the publisher does when
such a band is configured, and that it does nothing different when one is not."""
import pytest

import xi.xi_config as cfg
from xi.ability import xi_publish as ap
from xi.entity.anim import xi_motion_tables as mt

SPELL_RETAIL_BASE = 0xAF0
JA_RETAIL_BASE = 4412


@pytest.fixture
def no_bands(monkeypatch):
    for name in ("FX_SPELL_BAND_FIRST", "FX_SPELL_BAND_BASE", "FX_JA_BAND_FIRST",
                 "FX_JA_BAND_BASE", "FX_WS_BAND_FIRST", "FX_WS_BAND_BASE", "FX_WS_BAND_SLOTS"):
        monkeypatch.setattr(cfg, name, 0, raising=False)


@pytest.fixture
def bands(monkeypatch, no_bands):
    monkeypatch.setattr(cfg, "FX_SPELL_BAND_FIRST", 1612)
    monkeypatch.setattr(cfg, "FX_SPELL_BAND_BASE", 423_152)
    monkeypatch.setattr(cfg, "FX_JA_BAND_FIRST", 500)
    monkeypatch.setattr(cfg, "FX_JA_BAND_BASE", 427_248)
    monkeypatch.setattr(cfg, "FX_WS_BAND_FIRST", 272)
    monkeypatch.setattr(cfg, "FX_WS_BAND_BASE", 431_344)
    monkeypatch.setattr(cfg, "FX_WS_BAND_SLOTS", 256)


def test_without_a_band_nothing_changes(no_bands):
    assert ap.custom_band("spell") == (0, 0)
    assert ap.file_id_for("spell", 1011) == SPELL_RETAIL_BASE + 1011
    assert ap.file_id_for("spell", 1611) == SPELL_RETAIL_BASE + 1611
    assert ap.file_id_for("ja", 499) == JA_RETAIL_BASE + 499
    assert ap._candidates("spell") == range(ap.SPELL_CUSTOM_FIRST, ap.SPELL_CUSTOM_LAST + 1)


def test_below_the_threshold_keeps_the_retail_arithmetic(bands):
    assert ap.file_id_for("spell", 1611) == SPELL_RETAIL_BASE + 1611
    assert ap.file_id_for("ja", 499) == JA_RETAIL_BASE + 499


def test_at_and_above_the_threshold_lands_in_the_band(bands):
    # the number is added whole; the threshold picks the arithmetic, it is not part of it
    assert ap.file_id_for("spell", 1612) == 423_152 + 1612
    assert ap.file_id_for("spell", 4095) == 423_152 + 4095
    assert ap.file_id_for("ja", 500) == 427_248 + 500
    assert ap.file_id_for("ja", 4095) == 427_248 + 4095


def test_each_band_reserves_the_whole_number_space(bands):
    # what makes the addition safe: the next base starts past this band's last id,
    # so no number in one kind can collide with any number in another
    assert ap.file_id_for("spell", 4095) < 427_248        # ja base
    assert ap.file_id_for("ja", 4095) < 431_344           # ws base


def test_the_band_is_where_the_client_looks(bands):
    # the one number this was all built for, and the id the plugin resolves it to
    assert ap.file_id_for("spell", 1612) == 424_764


def test_the_picker_reaches_into_the_band(bands, no_bands_needed=None):
    cands = ap._candidates("ja")
    assert cands.start == ap.JA_CUSTOM_FIRST
    assert 500 in cands and 4095 in cands


def test_the_picker_stops_at_the_end_of_the_number_space(bands):
    # 12 bits in the action packet: offering 4096 and up would allocate a DAT for
    # a number that cannot reach the client
    assert 4096 not in ap._candidates("ja")
    assert 4096 not in ap._candidates("spell")


def test_a_band_never_overlaps_the_retail_ids_it_replaces(bands):
    retail_max = max(ap.file_id_for("spell", n) for n in range(0, 1612))
    band_min = ap.file_id_for("spell", 1612)
    assert band_min > retail_max


def test_weapon_skill_band_groups_ids_by_number(bands):
    # The race term is 1-based: the client indexes the band by RaceGenderConfig 1..8 (Hume
    # male is 1) and cexislots feeds that in, so race 0 (Hume male) sits at base + 1.
    a = mt.weapon_skill_slot({}, 0, 272)
    assert (a.bank, a.file_id, a.companion_a, a.companion_b) == ("custom", 431_345, 431_353, 431_361)
    # every race of one number sits together, so a number costs 24 ids
    last_race = mt.weapon_skill_slot({}, 7, 272)
    assert last_race.file_id == 431_344 + 8            # race 7 -> base + (7 + 1)
    assert mt.weapon_skill_slot({}, 0, 273).file_id == 431_344 + 24 + 1


def test_weapon_skill_band_size_can_grow_without_moving_anything(bands, monkeypatch):
    before = mt.weapon_skill_slot({}, 3, 300).file_id
    monkeypatch.setattr(cfg, "FX_WS_BAND_SLOTS", 1024)
    assert mt.weapon_skill_slot({}, 3, 300).file_id == before


def test_weapon_skill_past_the_reserved_count_is_refused(bands):
    with pytest.raises(ValueError, match="past the custom weapon-skill band"):
        mt.weapon_skill_slot({}, 0, 272 + 256)


def test_an_incomplete_band_is_ignored(monkeypatch, no_bands):
    monkeypatch.setattr(cfg, "FX_SPELL_BAND_FIRST", 1612)   # no base set
    assert ap.custom_band("spell") == (0, 0)
    assert ap.file_id_for("spell", 2000) == SPELL_RETAIL_BASE + 2000


# ── the bands are on unless switched off; weapon-skill numbers and their errors ──────

def test_an_unset_band_takes_the_plugin_default_and_zero_switches_it_off(monkeypatch):
    monkeypatch.delenv("FX_WS_BAND_FIRST", raising=False)
    assert cfg._band("FX_WS_BAND_FIRST", 272) == 272
    monkeypatch.setenv("FX_WS_BAND_FIRST", "0")       # what the model viewer sends when off
    assert cfg._band("FX_WS_BAND_FIRST", 272) == 0
    monkeypatch.setenv("FX_WS_BAND_FIRST", "300")
    assert cfg._band("FX_WS_BAND_FIRST", 272) == 300


def test_weapon_skill_candidates_are_the_stock_numbers_then_the_band(bands):
    nums = ap.ws_candidates()
    assert nums[:9] == [264, 265, 266, 267, 268, 269, 270, 271, 272]
    assert nums[-1] == 527 and len(nums) == 8 + 256
    assert ap.ws_candidates(300)[0] == 300
    assert ap.needs_plugin("ws", 272) and not ap.needs_plugin("ws", 271)


def test_weapon_skill_candidates_without_a_band(no_bands):
    assert ap.ws_candidates() == list(range(264, 272))
    assert ap.ws_candidates(272) == []
    assert not ap.needs_plugin("ws", 300)


@pytest.fixture
def every_number_taken(monkeypatch, tmp_path):
    monkeypatch.setattr(mt, "load_maindll", lambda: b"")
    monkeypatch.setattr(ap, "resolve_weapon_skill", lambda n, race=None, dll=None: [])
    monkeypatch.setattr(ap, "_ws_taken", lambda root, slots, ours=None: [("HumeMale", "ROM10/20/1.DAT")])
    return tmp_path


def test_a_full_weapon_skill_range_with_the_band_off_says_how_to_switch_it_on(no_bands, every_number_taken):
    with pytest.raises(Exception) as e:
        ap._pick_animation(every_number_taken, "ws", None, False)
    msg = str(e.value.message)
    assert "264–271" in msg and "FX_WS_BAND_FIRST=0" in msg and "Custom animation bands" in msg


def test_a_full_weapon_skill_range_with_the_band_on_names_the_whole_range(bands, every_number_taken):
    with pytest.raises(Exception) as e:
        ap._pick_animation(every_number_taken, "ws", None, False)
    msg = str(e.value.message)
    assert "264–527" in msg and "switched off" not in msg and "FX_WS_BAND_SLOTS" in msg


def test_animation_from_starts_the_search_there(bands, monkeypatch, tmp_path):
    monkeypatch.setattr(mt, "load_maindll", lambda: b"")
    monkeypatch.setattr(ap, "resolve_weapon_skill", lambda n, race=None, dll=None: [n])
    monkeypatch.setattr(ap, "_ws_taken", lambda root, slots, ours=None: [] if slots[0] >= 269 else [("x", "y")])
    assert ap._pick_animation(tmp_path, "ws", None, False) == 269
    assert ap._pick_animation(tmp_path, "ws", None, False, start=300) == 300
    with pytest.raises(Exception, match="past the last weapon-skill number"):
        ap._pick_animation(tmp_path, "ws", None, False, start=600)


# ── the band's file ids sit past the expanded tables: the pivot overlay's pair grows ──

def _tables(root, entries, rom=10):
    d = root / f"ROM{rom}"
    d.mkdir(parents=True)
    (d / f"FTABLE{rom}.DAT").write_bytes(b"\0" * entries * 2)
    (d / f"VTABLE{rom}.DAT").write_bytes(b"\0" * entries)
    return d / f"FTABLE{rom}.DAT", d / f"VTABLE{rom}.DAT"


def test_the_band_bounds_are_the_plugins_ceiling(bands):
    assert cfg.fx_band_floor() == 423_152
    assert cfg.fx_band_ceiling() == 437_488            # cexislots FX_CEILING


def test_no_band_no_bounds(no_bands):
    assert cfg.fx_band_floor() == 0 and cfg.fx_band_ceiling() == 0


def test_a_band_build_grows_the_pivot_rom_pair_only(bands, monkeypatch, tmp_path):
    from xi.dats import xi_dats as xd
    ft, vt = _tables(tmp_path, 423_152)
    monkeypatch.setattr(xd, "_root_target_name", lambda root: "pivot")
    xd._make_room_for_band(tmp_path, [432_016, 432_023], dry_run=True)
    assert vt.stat().st_size == 423_152                # a dry run writes nothing
    xd._make_room_for_band(tmp_path, [432_016, 432_023], dry_run=False)
    assert vt.stat().st_size == 437_488 and ft.stat().st_size == 437_488 * 2
    assert (tmp_path / "ROM10" / "VTABLE10.DAT.base").stat().st_size == 423_152
    xd._make_room_for_band(tmp_path, [61_464], dry_run=False)      # a stock number: nothing to do


def test_a_band_build_into_the_install_says_to_use_the_pivot_folder(bands, monkeypatch, tmp_path):
    from xi.dats import xi_dats as xd
    _ft, vt = _tables(tmp_path, 423_152)
    monkeypatch.setattr(xd, "_root_target_name", lambda root: "dir")
    with pytest.raises(Exception, match="--pivot"):
        xd._make_room_for_band(tmp_path, [432_016], dry_run=True)
    assert vt.stat().st_size == 423_152


def test_a_pivot_sync_keeps_the_band_tail(bands, monkeypatch, tmp_path):
    from xi.ftable import xi_expand as xe
    game, piv = tmp_path / "game", tmp_path / "pivot"
    for root in (game, piv):
        root.mkdir()
        (root / "FTABLE.DAT").write_bytes(b"\0" * 423_152 * 2)
        (root / "VTABLE.DAT").write_bytes(b"\0" * 423_152)
    _tables(game, 423_152)
    pft, pvt = _tables(piv, 437_488)
    v = bytearray(pvt.read_bytes()); v[432_016] = 10; pvt.write_bytes(bytes(v))        # a band registration
    gv = bytearray((game / "ROM10" / "VTABLE10.DAT").read_bytes()); gv[200_000] = 10   # gear, install side
    (game / "ROM10" / "VTABLE10.DAT").write_bytes(bytes(gv))
    monkeypatch.setattr(xe, "FFXI_DIR", str(game))
    monkeypatch.setattr(xe, "pivot_root", lambda: str(piv))
    monkeypatch.setattr(xe, "read_path_for", lambda p: p)
    xe.sync_pivot_from_base()
    out = pvt.read_bytes()
    assert len(out) == 437_488 and out[432_016] == 10 and out[200_000] == 10
    # and the longer overlay pair is not a size mismatch
    assert len(set(xe.table_entry_sizes(include_pivot=True).values())) == 1

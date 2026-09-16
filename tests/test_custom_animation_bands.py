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
    a = mt.weapon_skill_slot({}, 0, 272)
    assert (a.bank, a.file_id, a.companion_a, a.companion_b) == ("custom", 431_344, 431_352, 431_360)
    # every race of one number sits together, so a number costs 24 ids
    last_race = mt.weapon_skill_slot({}, 7, 272)
    assert last_race.file_id == 431_344 + 7
    assert mt.weapon_skill_slot({}, 0, 273).file_id == 431_344 + 24


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

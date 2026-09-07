"""Weapon-skill bank tables (xi.entity.anim.xi_motion_tables): the two banks,
their companion blocks, and the < 256 / >= 256 resolution rule.

The synthetic tests need no game install. The install-backed tests use the
``root`` fixture and skip without FFXI_DIR."""
import struct
from pathlib import Path

import pytest

from xi.entity.anim import xi_motion_tables as mt


def _region(bases, slots):
    """One bank region as it sits in the DLL: 9 u16 body rows (row 0 = HumeMale
    again), a zero word, then 9 (compA, compB) pairs."""
    rows = [bases[0]] + list(bases)
    out = struct.pack('<9H', *rows) + struct.pack('<H', 0)
    for b in rows:
        out += struct.pack('<HH', b + slots, b + 2 * slots)
    return out


PRIMARY = [33227, 33995, 34763, 35531, 36299, 36299, 37067, 37835]
EXTENDED = [61451, 61499, 61547, 61595, 61643, 61643, 61691, 61739]


def _fake_dll(primary=PRIMARY, extended=EXTENDED, break_companion=False):
    ext = bytearray(_region(extended, mt.WS_EXTENDED_SLOTS))
    if break_companion:
        struct.pack_into('<H', ext, mt.WS_COMPANION_TABLE_OFFSET + 4, 1)
    # The scanner starts past the PE headers; mirror the real layout (extended
    # region sits just below the primary one).
    return (b'\0' * mt._DLL_SCAN_START + b'\xaa' * 64 + bytes(ext) + b'\0' * 8
            + _region(primary, mt.WS_PRIMARY_SLOTS) + b'\0' * 64)


def test_hints_are_the_duplicated_humemale_rows():
    assert mt.MOTION_CATEGORY_HINTS['weaponSkill'] == 0xCB81CB81
    assert struct.unpack('<HH', struct.pack('>I', 0xCB81CB81)) == (33227, 33227)
    assert struct.unpack('<HH', struct.pack('>I', mt.MOTION_CATEGORY_HINTS['weaponSkillExt'])) == (61451, 61451)


def test_read_both_banks_from_synthetic_dll():
    banks = mt.weapon_skill_banks(_fake_dll())
    assert set(banks) == {'primary', 'extended'}
    p, e = banks['primary'], banks['extended']
    assert p.body == tuple(PRIMARY) and e.body == tuple(EXTENDED)
    assert p.companion_a == tuple(b + 256 for b in PRIMARY)
    assert p.companion_b == tuple(b + 512 for b in PRIMARY)
    assert e.companion_a == tuple(b + 16 for b in EXTENDED)
    assert e.companion_b == tuple(b + 32 for b in EXTENDED)
    assert (p.first_animation, p.last_animation) == (0, 255)
    assert (e.first_animation, e.last_animation) == (256, 271)


def test_bank_shape_is_validated_not_just_the_hint():
    banks = mt.weapon_skill_banks(_fake_dll(break_companion=True))
    assert 'primary' in banks and 'extended' not in banks


def test_resolution_rule_primary_vs_extended():
    banks = mt.weapon_skill_banks(_fake_dll())
    hm190 = mt.weapon_skill_slot(banks, 'HumeMale', 190)
    assert (hm190.bank, hm190.file_id) == ('primary', 33227 + 190)
    hm259 = mt.weapon_skill_slot(banks, 'HumeMale', 259)
    assert (hm259.bank, hm259.index, hm259.file_id) == ('extended', 3, 61454)
    # the naive add lands in the primary bank's companion A block
    assert 33227 + 259 == banks['primary'].companion_a[0] + 3
    assert hm259.companion_a == 61451 + 16 + 3
    ef259 = mt.weapon_skill_slot(banks, 'ElvaanFemale', 259)
    assert ef259.file_id == 61595 + 3
    # Taru male/female share a table by design
    assert mt.weapon_skill_slot(banks, 'TaruMale', 5).file_id == \
        mt.weapon_skill_slot(banks, 'TaruFemale', 5).file_id


def test_out_of_bank_numbers_are_errors_not_masked():
    banks = mt.weapon_skill_banks(_fake_dll())
    for bad in (272, 300, 4095):
        with pytest.raises(ValueError):
            mt.weapon_skill_slot(banks, 'HumeMale', bad)
    with pytest.raises(ValueError):
        mt.weapon_skill_slot(banks, 'HumeMale', -1)
    with pytest.raises(ValueError):
        mt.weapon_skill_slot(banks, 'HumeMale', 4096)
    # no extended bank at all -> 259 must fail loudly rather than fall back
    only_primary = {'primary': banks['primary']}
    with pytest.raises(ValueError):
        mt.weapon_skill_slot(only_primary, 'HumeMale', 259)


def test_race_index_accepts_names_and_rejects_past_galka():
    assert mt.race_index('HumeMale') == 0
    assert mt.race_index('hume female') == 1
    assert mt.race_index('Tarutaru') == 4
    assert mt.race_index('Galka') == 7
    assert mt.race_index(3) == 3
    with pytest.raises(ValueError):
        mt.race_index(8)
    with pytest.raises(ValueError):
        mt.race_index('Chocobo')


# ── install-backed ────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def dll(root: Path) -> bytes:
    p = root / 'FFXiMain.dll'
    if not p.exists():
        pytest.skip('FFXiMain.dll not in the game folder')
    return p.read_bytes()


def test_installed_dll_has_both_banks(dll):
    banks = mt.weapon_skill_banks(dll)
    assert set(banks) == {'primary', 'extended'}
    assert banks['primary'].body == tuple(PRIMARY)
    assert banks['extended'].body == tuple(EXTENDED)
    # the extended region sits just below the primary one in every build seen
    assert 0 < banks['primary'].table_offset - banks['extended'].table_offset < 0x100


def test_installed_resolution_matches_retail_assets(dll, root):
    banks = mt.weapon_skill_banks(dll)
    resolver = mt._FileIdResolver()
    hm259 = mt.weapon_skill_slot(banks, 'HumeMale', 259)
    hm190 = mt.weapon_skill_slot(banks, 'HumeMale', 190)
    assert resolver.rom_spec(hm259.file_id) == 'ROM/204/17.DAT'
    assert resolver.rom_spec(hm190.file_id) == 'ROM/147/8.DAT'
    assert resolver.rom_spec(banks['primary'].body[0] + 259) == 'ROM/76/126.DAT'
    assert resolver.rom_spec(mt.weapon_skill_slot(banks, 'HumeFemale', 259).file_id) == 'ROM/226/17.DAT'
    assert resolver.rom_spec(mt.weapon_skill_slot(banks, 'ElvaanMale', 259).file_id) == 'ROM/204/27.DAT'

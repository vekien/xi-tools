"""Rotated ``comm`` / ``mgc_`` records of ROM/118/114.DAT (xi.common.xi_menu_records)."""
import os
import random
import struct
from pathlib import Path

import pytest

from xi.common import xi_menu_records as mr


def test_round_trip_is_exact_and_leaves_plain_bytes_alone():
    rng = random.Random(1234)
    for stride in (mr.COMM_STRIDE, mr.MGC_STRIDE):
        for _ in range(200):
            rec = bytes(rng.randrange(256) for _ in range(stride))
            enc = mr.encode_record(rec)
            assert mr.decode_record(enc) == rec
            assert mr.encode_record(mr.decode_record(rec)) == rec
            for off in mr.PLAIN_OFFSETS:
                assert enc[off] == rec[off]


def test_rotate_amount_table():
    # all-zero plain bytes -> |0+0-0| % 5 = 0 -> rotate 1
    rec = bytearray(mr.COMM_STRIDE)
    assert mr.rotate_amount(rec) == 1
    rec[0x02] = 0xFF            # pop 8 -> index 3 -> rotate 6
    assert mr.rotate_amount(rec) == 6
    rec[0x0B] = 0x0F            # |8+0-4| = 4 -> rotate 3
    assert mr.rotate_amount(rec) == 3


def test_decode_records_splits_whole_records_only():
    payload = bytes(range(48)) * 3 + b'\x01\x02'
    recs = mr.decode_records(payload, 48)
    assert len(recs) == 3 and all(len(r) == 48 for r in recs)
    assert mr.encode_records(recs) == payload[:144]


# ── install-backed ────────────────────────────────────────────────────────────

def test_comm_ids_are_sequential_and_255_is_a_weapon_skill(root: Path):
    p = root / 'ROM/118/114.DAT'
    if not p.exists():
        pytest.skip('ROM/118/114.DAT missing')
    data = p.read_bytes()
    off = data.find(b'comm')
    assert off > 0
    recs = mr.decode_records(data[off + 0x10: off + 0x10 + 2816 * mr.COMM_STRIDE], mr.COMM_STRIDE)
    assert len(recs) == 2816
    ids = [struct.unpack_from('<H', r, 0)[0] for r in recs]
    assert ids == list(range(2816))
    assert recs[255][2] == mr.COMM_TYPE_WEAPON_SKILL
    assert recs[259][2] == mr.COMM_TYPE_WEAPON_SKILL
    assert sum(1 for r in recs if r[2] == mr.COMM_TYPE_WEAPON_SKILL) == 512


def test_ability_names_line_up_with_comm_ids(root: Path):
    if not (root / mr.ABILITY_NAME_DAT).exists():
        pytest.skip('ROM/181/72.DAT missing')
    names = mr.load_ability_names(root)
    assert len(names) == 5888
    assert names[1] == 'Combo'
    assert names[32] == 'Fast Blade'
    assert names[255] == 'Dimensional Death'
    assert names[259] == '.'          # unnamed extended-bank slot

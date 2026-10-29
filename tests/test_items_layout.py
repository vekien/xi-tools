"""Item record layouts: legacy (0xC00) and retail September 2026 (0x1400).

These tests need no game files. They build records in both formats, check
that the stride detector tells them apart, and that a record written in one
format reads back with the same decoded values as the other.
"""
import struct

import pytest

from xi.ui.items import xi_layout as L
from xi.ui.items.xi_parser import (
    ItemDat, _decrypt, _encrypt, _parse_record, _patch_record, build_record,
    text_offset_for,
)


def _dat_bytes(records: list[bytes]) -> bytes:
    return _encrypt(b"".join(records))


# ── detection ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fmt,stride", [("legacy", L.STRIDE_LEGACY), ("retail", L.STRIDE_RETAIL)])
def test_detect_stride_unambiguous_sizes(fmt, stride):
    recs = [build_record({"name": f"Thing {i}"}, 0, fmt) for i in range(7)]  # 7 is not a multiple of 5
    data = _dat_bytes(recs)
    assert L.detect_stride(data) == stride
    assert L.detect_format(data) == fmt


@pytest.mark.parametrize("fmt,stride", [("legacy", L.STRIDE_LEGACY), ("retail", L.STRIDE_RETAIL)])
def test_detect_stride_ambiguous_size_uses_terminator(fmt, stride):
    # 5 legacy records = 3 retail records in bytes (15360); the 0xFF record
    # terminator has to settle it.
    n = 5 if fmt == "legacy" else 3
    recs = [build_record({"name": f"Thing {i}"}, 0, fmt) for i in range(n)]
    data = _dat_bytes(recs)
    assert len(data) % L.STRIDE_LEGACY == 0 and len(data) % L.STRIDE_RETAIL == 0
    assert L.detect_stride(data) == stride


def test_detect_stride_ambiguous_size_without_terminators_uses_ids():
    # No 0xFF terminators at all: fall back to "record ids count up by one".
    recs = []
    for i in range(3):
        rec = bytearray(build_record({"name": f"Thing {i}"}, 0, "retail"))
        struct.pack_into("<I", rec, 0, 100 + i)
        rec[-1] = 0
        recs.append(bytes(rec))
    data = _dat_bytes(recs)
    assert L.detect_stride(data) == L.STRIDE_RETAIL


def test_detect_stride_rejects_non_item_sizes():
    with pytest.raises(ValueError):
        L.detect_stride(b"\0" * 1000)


def test_detect_stride_path_reads_sparsely(tmp_path):
    recs = [build_record({"name": f"Thing {i}"}, 0, "retail") for i in range(3)]
    p = tmp_path / "x.DAT"
    p.write_bytes(_dat_bytes(recs))
    assert L.detect_stride_path(p) == L.STRIDE_RETAIL
    assert L.detect_format_path(p) == "retail"


# ── field tables ────────────────────────────────────────────────────────────

def test_every_layout_has_both_formats():
    for layout in L.LAYOUTS:
        for fmt in (L.FORMAT_LEGACY, L.FORMAT_RETAIL):
            fields = L.fields_for(layout, fmt)
            assert "id" in fields and "flags" in fields
            toff = L.text_offset(layout, fmt)
            # no header field may overlap the string block
            for name, (off, sfmt) in fields.items():
                assert off + struct.calcsize(sfmt) <= toff, (layout, fmt, name)


def test_retail_common_fields_shift_by_two():
    for layout in ("general", "usable", "puppet", "armor", "weapon", "maze", "instinct"):
        leg = L.fields_for(layout, "legacy")
        ret = L.fields_for(layout, "retail")
        for name in ("stack", "type", "resource_id", "targets"):
            assert ret[name][0] == leg[name][0] + 2, (layout, name)
        assert ret["flags"] == leg["flags"] == (0x04, "<H")


def test_retail_equipment_fields_shift_by_four_after_races():
    for layout in ("armor", "weapon"):
        leg = L.fields_for(layout, "legacy")
        ret = L.fields_for(layout, "retail")
        assert ret["races"][0] == leg["races"][0] + 2
        for name in ("jobs", "superior_level", "item_level"):
            assert ret[name][0] == leg[name][0] + 4, (layout, name)
    assert L.text_offset("weapon", "retail") == 0x3C
    assert L.text_offset("armor", "retail") == 0x30
    assert L.text_offset("usable", "retail") == L.text_offset("usable", "legacy") == 0x1C
    assert L.text_offset("roe", "retail") == L.text_offset("roe", "legacy") == 0x20


# Charges, cast time, use and reuse delay of usable gear, as the retail legacy DATs hold them
# (ROM/118/109 Signal Pearl, ROM/118/108 Charm Wand): one byte each for charges and cast time,
# the reuse delay a u32 (Charm Wand's 86400 s doesn't fit a u16)
SIGNAL_PEARL_1C = bytes.fromhex("010c0a0060540000")   # armor 0x1C..0x23
CHARM_WAND_28 = bytes.fromhex("0a201e0080510100")     # weapon 0x28..0x2F


@pytest.mark.parametrize("layout,offset,raw,expected", [
    ("armor", 0x1C, SIGNAL_PEARL_1C, (1, 12, 10, 21600)),
    ("weapon", 0x28, CHARM_WAND_28, (10, 32, 30, 86400)),
])
def test_legacy_usable_gear_fields(layout, offset, raw, expected):
    rec = bytearray(L.STRIDE_LEGACY)
    rec[offset:offset + len(raw)] = raw
    names = ("max_charges", "cast_time", "use_delay", "reuse_delay")
    assert tuple(L.read_field(rec, layout, "legacy", n) for n in names) == expected


def test_legacy_reuse_delay_write_replaces_the_whole_u32():
    rec = bytearray(L.STRIDE_LEGACY)
    rec[0x1C:0x24] = SIGNAL_PEARL_1C
    assert L.write_field(rec, "armor", "legacy", "reuse_delay", 3600)
    assert rec[0x1C:0x24] == bytes.fromhex("010c0a00100e0000")
    assert L.read_field(rec, "armor", "legacy", "reuse_delay") == 3600


# ── build / parse round trips ───────────────────────────────────────────────

WEAPON = {
    "name": "Kraken Club", "singular": "kraken club", "plural": "kraken clubs",
    "description": "DMG:11 Delay:264 \nOccasionally attacks 2 to 8 times",
    "flags": 0x8800, "stack": 1, "resource_id": 0x2B1D, "targets": 0,
    "level": 63, "slots": 3, "races": 0x1FE, "jobs": 0xFFFE, "superior_level": 0,
    "kind": 4, "dmg": 11, "delay": 264, "dps": 250, "skill": 11,
}
ARMOR = {
    "name": "Scorpion Harness", "singular": "scorpion harness", "plural": "scorpion harnesses",
    "description": "DEF:40 HP+15\nAccuracy+10 Evasion+10",
    "flags": 0x0824, "stack": 1, "resource_id": 0xC443, "targets": 0,
    "level": 57, "slots": 0x20, "races": 0x1FE, "jobs": 0x004B77E6, "superior_level": 0,
}


@pytest.mark.parametrize("fmt", ["legacy", "retail"])
@pytest.mark.parametrize("entry,item_type", [(WEAPON, 4), (ARMOR, 3)])
def test_build_then_parse_round_trip(fmt, entry, item_type):
    rec = build_record(entry, item_type, fmt)
    assert len(rec) == L.STRIDE_BY_FORMAT[fmt]
    assert rec[-1] == L.TERMINATOR
    item = _parse_record(entry.get("id", 12345), rec, None, item_type, "x", fmt=fmt)
    assert item is not None
    assert item.format == fmt
    for k in ("name", "singular", "plural", "description", "flags", "stack", "resource_id",
              "level", "slots", "races", "jobs"):
        assert getattr(item, k) == entry[k], k
    if item_type == 4:
        for k in ("dmg", "delay", "dps", "skill", "kind"):
            assert getattr(item, k) == entry[k], k


@pytest.mark.parametrize("entry,item_type", [(WEAPON, 4), (ARMOR, 3)])
def test_same_entry_decodes_identically_in_both_formats(entry, item_type):
    a = _parse_record(1, build_record(entry, item_type, "legacy"), None, item_type, "x", fmt="legacy")
    b = _parse_record(1, build_record(entry, item_type, "retail"), None, item_type, "x", fmt="retail")
    skip = {"format", "dat", "record_index"}
    for k, v in vars(a).items():
        if k not in skip:
            assert getattr(b, k) == v, k


def test_retail_flags_keep_high_word_clear():
    rec = bytearray(build_record({"name": "x", "flags": 0xFFFF}, 0, "retail"))
    assert struct.unpack_from("<I", rec, 4)[0] == 0xFFFF
    _patch_record(rec, {"flags": 0x0824}, 0, "retail")
    assert struct.unpack_from("<I", rec, 4)[0] == 0x0824


def test_text_offset_for_matches_tables():
    assert text_offset_for(4, "legacy") == 0x38 and text_offset_for(4, "retail") == 0x3C
    assert text_offset_for(0, "legacy") == 0x18 and text_offset_for(0, "retail") == 0x1C


def test_itemdat_handle_detects_and_round_trips(tmp_path):
    recs = [build_record({"name": f"Thing {i}", "flags": i}, 0, "retail") for i in range(4)]
    p = tmp_path / "items.DAT"
    p.write_bytes(_dat_bytes(recs))
    dat = ItemDat.load(p)
    assert dat.format == "retail" and dat.stride == L.STRIDE_RETAIL and dat.count == 4
    rec = bytearray(dat.record(2))
    _patch_record(rec, {"flags": 0x1234}, 0, dat.format)
    dat.set_record(2, bytes(rec))
    p.write_bytes(dat.encrypted())
    again = ItemDat.load(p)
    assert _parse_record(2, again.record(2), None, 0, "x", fmt=again.format).flags == 0x1234
    with pytest.raises(ValueError):
        dat.set_record(0, b"\0" * L.STRIDE_LEGACY)

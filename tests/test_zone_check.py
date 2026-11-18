"""``xi zone check`` (xi.zone.xi_check): the section-level checks on synthetic bytes, then,
with FFXI_DIR, retail zones checking clean (a normal zone, a ship with two ZoneDefs, the
test zone 0 with its older transforms) and copies of a retail zone broken on purpose."""
import json
import struct

import pytest
from click.testing import CliRunner

from _minischema import errors, load
from xi.zone import xi_check as ZC


def _sec(type_code: int, body: bytes = b"") -> bytes:
    body = body + bytes(-(16 + len(body)) % 16)
    return b"sec " + struct.pack("<I", type_code | (((16 + len(body)) // 16) << 7)) + bytes(8) + body


def test_no_zonedef_and_trailing_bytes():
    r = ZC.check_zone(_sec(0x01) + _sec(0x20, bytes(32)) + b"tail", b"", b"")
    assert "no ZoneDef (0x1C) section: not a zone model" in r["problems"]
    assert any("the file is" in p for p in r["problems"])
    assert any("never ended" in n for n in r["notes"])


def test_schema_examples():
    schema = load("zone_check.json")
    for ex in schema["examples"]:
        assert errors(schema, ex) == []


# ── against the install ─────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def keys(root):
    from xi.zone.xi_decrypt import load_key_tables
    return load_key_tables(root / "FFXiMain.dll")


def _zone(root, zid: int) -> bytes:
    from xi.ftable.xi_core import load_all_tables, scan_file_ids
    from xi.zone.xi_list import zone_file_id
    hits = scan_file_ids([zone_file_id(zid)], load_all_tables())
    if not hits:
        pytest.skip(f"zone {zid} not in this install")
    return (root / hits[0]["dat"]).read_bytes()


@pytest.mark.parametrize("zid", [107, 154, 224, 0])
def test_retail_zones_check_clean(root, keys, zid):
    r = ZC.check_zone(_zone(root, zid), *keys)
    assert r["problems"] == [], r["problems"]
    if zid == 224:
        assert any("2 ZoneDefs" in n for n in r["notes"])
    if zid == 0:
        assert any("0x70-byte collision transforms" in n for n in r["notes"])


def _broken(root, keys, edit) -> list:
    """Zone 107's model with ``edit(buf, ds)`` applied to its decrypted ZoneDef."""
    from xi.entity.anim.xi_export import parse_sections
    from xi.zone.xi_decrypt import decrypt_zone_objects, reencrypt_zone_objects
    buf = bytearray(_zone(root, 107))
    zs = next(s for s in parse_sections(buf) if s.type_code == 0x1C)
    decrypt_zone_objects(buf, zs.data_start, zs.start, zs.size, keys[0])
    edit(buf, zs.data_start, zs.size - 0x10)
    reencrypt_zone_objects(buf, zs.data_start, zs.start, zs.size, keys[0])
    return ZC.check_zone(bytes(buf), *keys)["problems"]


def test_broken_tree_child(root, keys):
    def edit(buf, ds, end):
        root_rel = struct.unpack_from("<I", buf, ds + 0x10)[0]
        struct.pack_into("<I", buf, ds + root_rel + 0x68, end + 0x100)
    assert any("has a child at" in p for p in _broken(root, keys, edit))


def test_broken_leaf_member(root, keys):
    def edit(buf, ds, end):
        from xi.zone.xi_zonedef import parse_zonedef
        leaf = next(n for n in parse_zonedef(buf, ds, ds - 0x10, end + 0x10).nodes if n.idx_ref)
        struct.pack_into("<I", buf, ds + leaf.idx_ref, 999_999)
    assert any("lists object 999999" in p for p in _broken(root, keys, edit))


def test_broken_grid_cell(root, keys):
    def edit(buf, ds, end):
        cb = ds + struct.unpack_from("<I", buf, ds + 8)[0]
        map_off = struct.unpack_from("<I", buf, cb + 0x10)[0]
        n = (end - map_off) // 4
        cells = struct.unpack_from(f"<{n}I", buf, ds + map_off)
        i = next(k for k, c in enumerate(cells) if 0 < c < end)
        struct.pack_into("<I", buf, ds + map_off + 4 * i, cells[i] + 4)       # inside, but not a group
    assert any("point inside the ZoneDef but at no collision group" in p for p in _broken(root, keys, edit))


def test_base_grid_bigger(root, keys):
    r = ZC.check_zone(_zone(root, 107), *keys, base_blocks=(60, 60, 40, 40))
    assert any("smaller than the base zone's" in p for p in r["problems"])


def test_cli_json_matches_schema(root):
    from xi.zone.xi_check import cmd
    r = CliRunner().invoke(cmd, ["107", "224", "--json"])
    assert r.exit_code == 0, r.output
    doc = json.loads(r.output)
    assert errors(load("zone_check.json"), doc) == []
    assert [z["target"] for z in doc["zones"]] == ["zone 107", "zone 224"]

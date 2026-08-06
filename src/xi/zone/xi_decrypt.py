"""Zone mesh (0x2E) decryption.

Ported from xim's ZoneDecrypt.kt. Zone mesh sections are encrypted with a
two-pass scheme keyed off two 256-byte tables that live inside FFXiMain.dll:

  pass 1: a keyed XOR stream over the section body
  pass 2: conditional swapping of 8-byte blocks between the two halves

The key tables are located in the DLL by a 4-byte signature (the table's own
first bytes), scanned from offset 0x30000 — the same hint mechanism xim uses, so
it works across DLL versions without hard-coded offsets.
"""

import struct
from functools import lru_cache
from pathlib import Path
from typing import Tuple

# First 4 bytes (big-endian) of each key table — used as a find signature.
_TABLE1_SIG = bytes.fromhex("E2E506A9")
_TABLE2_SIG = bytes.fromhex("B8C5F784")
_SCAN_START = 0x30000
_TABLE_SIZE = 0x100


@lru_cache(maxsize=4)
def load_key_tables(dll_path: Path) -> Tuple[bytes, bytes]:
    """Extract the two 256-byte zone-decrypt tables from FFXiMain.dll.

    Cached: the tables are a pure function of the DLL, which never changes during a
    run, so a batch over many zones reads/scans FFXiMain.dll once instead of per-zone.
    """
    data = dll_path.read_bytes()
    i1 = data.find(_TABLE1_SIG, _SCAN_START)
    i2 = data.find(_TABLE2_SIG, _SCAN_START)
    if i1 < 0 or i2 < 0:
        raise ValueError(f"Could not locate zone-decrypt key tables in {dll_path}")
    return data[i1 : i1 + _TABLE_SIZE], data[i2 : i2 + _TABLE_SIZE]


# ---------------------------------------------------------------------------
# ZoneDef (0x1C) — XOR-0xFF body stream + 0x55 name masking
#
# Every primitive op here is self-inverse (XOR), so re-encryption is the same
# two passes in the *reverse* order. The XOR key stream is position-driven (keys
# come from table1 + counters, never from the data), so it inverts cleanly no
# matter how the body was edited in between.
# ---------------------------------------------------------------------------


def _zone_objects_xor_pass(buf: bytearray, data_start: int, section_start: int, section_size: int, table1: bytes) -> int:
    """Apply the variable-length XOR-0xFF body stream in place. Returns node count.

    Self-inverse: running it on encrypted bytes decrypts, running it again
    re-encrypts. Header bytes (metadata @ds, node count/key @ds+4) are never
    touched, so the keys stay stable across a decrypt/re-encrypt pair."""
    ds = data_start
    metadata = struct.unpack_from("<I", buf, ds)[0]
    decode_length = metadata & 0x00FFFFFF
    node_count = struct.unpack_from("<I", buf, ds + 4)[0] & 0x00FFFFFF

    key_index = (struct.unpack_from("<I", buf, ds + 4)[0] >> 24) & 0xFF
    key = table1[key_index ^ 0xFF]
    counter = 0
    consumed = 0
    p = ds + 8
    section_end = section_start + section_size
    if p + decode_length > section_end:
        decode_length -= (p + decode_length) - section_end

    while consumed < decode_length:
        xor_length = ((key >> 4) & 7) + 16
        apply_mask = (key & 1) != 0
        has_remaining = consumed + xor_length < decode_length
        if apply_mask and has_remaining:
            for _ in range(xor_length):
                buf[p] ^= 0xFF
                p += 1
        else:
            p += xor_length
        counter += 1
        key += counter
        consumed += xor_length
    return node_count


def _zone_objects_mask_names(buf: bytearray, data_start: int, node_count: int) -> None:
    """XOR each object's 16-char name with 0x55 (self-inverse: mask == unmask)."""
    name_pos = data_start + 0x20  # offsetFrom(section, 0x30) == data_start + 0x20
    for i in range(node_count):
        base = name_pos + i * 0x64
        for j in range(0x10):
            buf[base + j] ^= 0x55


def decrypt_zone_objects(buf: bytearray, data_start: int, section_start: int, section_size: int, table1: bytes) -> int:
    """Decrypt a 0x1C ZoneDef section in place. Returns the object (node) count.

    Different scheme from the mesh: a variable-length XOR-0xFF stream over the
    body, then each object's 16-char name is unmasked with 0x55.
    """
    mode = (struct.unpack_from("<I", buf, data_start)[0] >> 24) & 0xFF
    node_count = struct.unpack_from("<I", buf, data_start + 4)[0] & 0x00FFFFFF
    if mode <= 0x1A:
        return node_count  # not encrypted
    _zone_objects_xor_pass(buf, data_start, section_start, section_size, table1)
    _zone_objects_mask_names(buf, data_start, node_count)
    return node_count


def reencrypt_zone_objects(buf: bytearray, data_start: int, section_start: int, section_size: int, table1: bytes) -> int:
    """Re-encrypt a 0x1C ZoneDef section in place (inverse of decrypt_zone_objects).

    Reverse pass order: mask names first, then run the XOR stream over the body."""
    mode = (struct.unpack_from("<I", buf, data_start)[0] >> 24) & 0xFF
    node_count = struct.unpack_from("<I", buf, data_start + 4)[0] & 0x00FFFFFF
    if mode <= 0x1A:
        return node_count  # not encrypted
    _zone_objects_mask_names(buf, data_start, node_count)
    _zone_objects_xor_pass(buf, data_start, section_start, section_size, table1)
    return node_count


# ---------------------------------------------------------------------------
# ZoneMesh (0x2E) — keyed XOR stream (pass 1) + 8-byte block swaps (pass 2)
#
# Both passes are self-inverse, so re-encrypt = pass2 then pass1 (reverse of the
# decrypt order pass1 -> pass2). The header (8 bytes from ds) is never decoded.
# ---------------------------------------------------------------------------


def _zone_mesh_pass1(buf: bytearray, data_start: int, table1: bytes) -> None:
    """Keyed XOR stream over the body (only when mode >= 5). Self-inverse."""
    ds = data_start
    metadata = struct.unpack_from("<I", buf, ds)[0]
    total_size = metadata & 0x00FFFFFF
    decode_length = total_size - 8
    mode = (metadata >> 24) & 0xFF
    if mode < 5:
        return
    key_index = buf[ds + 5]
    key = table1[key_index ^ 0xF0]
    counter = 0
    p = ds + 8
    for i in range(decode_length):
        key_mod = ((key & 0xFF) << 8) | (key & 0xFF)
        counter += 1
        key += counter
        buf[p + i] ^= (key_mod >> (key & 7)) & 0xFF
        counter += 1
        key += counter


def _zone_mesh_pass2(buf: bytearray, data_start: int, table2: bytes) -> None:
    """Conditional 8-byte block swaps between the two body halves. Self-inverse."""
    ds = data_start
    metadata = struct.unpack_from("<I", buf, ds)[0]
    total_size = metadata & 0x00FFFFFF
    decode_length = total_size - 8
    if struct.unpack_from("<H", buf, ds + 6)[0] != 0xFFFF:
        return
    key1 = buf[ds + 5] ^ 0xF0
    key2 = table2[key1]
    decode_count = (decode_length & ~0xF) // 2
    p = ds + 8
    i = 0
    while i < decode_count:
        if key2 & 1:
            for j in range(8):
                buf[p + j], buf[p + decode_count + j] = buf[p + decode_count + j], buf[p + j]
        p += 8
        i += 8
        key1 += 9
        key2 += key1


def decrypt_zone_mesh(buf: bytearray, data_start: int, table1: bytes, table2: bytes) -> None:
    """Decrypt a 0x2E zone-mesh section in place. data_start = section.start + 0x10."""
    _zone_mesh_pass1(buf, data_start, table1)
    _zone_mesh_pass2(buf, data_start, table2)


def reencrypt_zone_mesh(buf: bytearray, data_start: int, table1: bytes, table2: bytes) -> None:
    """Re-encrypt a 0x2E zone-mesh section in place (inverse of decrypt_zone_mesh).

    Reverse pass order: pass 2 (block swaps) then pass 1 (keyed XOR)."""
    _zone_mesh_pass2(buf, data_start, table2)
    _zone_mesh_pass1(buf, data_start, table1)

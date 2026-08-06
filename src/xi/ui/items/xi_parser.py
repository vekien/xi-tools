"""Binary item DAT parser for FFXI item DATs.

Cipher: every byte is rotated left 3 bits — (b >> 5) | (b << 3).
Record stride: 0xC00 bytes per item.
Text section offset varies by item type (derived from ShiningFantasia item.ts).
Text section is a d_msg-style entry: n (u32), n×8 offset table, strings at offset+0x1C.
Icon bitmap: u32 size at 0x280, BMP2 blob at 0x284.
"""

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

STRIDE = 0xC00

# Job restriction bitmask — bit N corresponds to JOBS[N]
JOBS = [
    'WAR', 'MNK', 'WHM', 'BLM', 'RDM', 'THF',
    'PLD', 'DRK', 'BST', 'BRD', 'RNG', 'SAM',
    'NIN', 'DRG', 'SMN', 'BLU', 'COR', 'PUP',
    'DNC', 'SCH', 'GEO', 'RUN',
]

# Item flags at record+0x04 (u16)
ITEM_FLAGS = {
    0x0001: 'rare',
    0x0002: 'ex',
    0x0004: 'usable',
    0x0008: 'npc_only',
    0x0020: 'deliverable',
    0x0040: 'bazaar',
    0x0080: 'storage',
    0x0200: 'scroll',
    0x0800: 'temporary',
    0x4000: 'trial',
    0x8000: 'enchanted',
}


def decode_flags(flags: int) -> list:
    return [name for bit, name in ITEM_FLAGS.items() if flags & bit]


def decode_jobs(jobs: int) -> list:
    return [JOBS[i] for i in range(len(JOBS)) if jobs & (1 << i)]


# Per-type text section offset within the decrypted record.
# type id matches xidats/ShiningFantasia ItemType: 0=General, 1=Usable, 3=Armor, 4=Weapon, 6=Furnishing
TEXT_OFFSETS = {
    0: 0x18,   # General
    1: 0x1C,   # Usable/Consumable
    3: 0x2C,   # Armor
    4: 0x38,   # Weapon
    5: 0x18,   # Puppet (same layout as general)
    6: 0x54,   # Furnishing
}

# DAT categories: (name, base_id, item_type, en_rom_path, jp_rom_path)
ITEM_DATS = [
    ('Items_1',       0,     0, 'ROM/118/106.DAT', 'ROM/0/4.DAT'),
    ('Consumable',    4096,  1, 'ROM/118/107.DAT', 'ROM/0/5.DAT'),
    ('Puppet',        8192,  5, 'ROM/118/110.DAT', 'ROM/0/8.DAT'),
    ('Items_2',       8704,  0, 'ROM/301/115.DAT', 'ROM/301/114.DAT'),
    ('Armor_1',       10240, 3, 'ROM/118/109.DAT', 'ROM/0/7.DAT'),
    ('Weapons',       16384, 4, 'ROM/118/108.DAT', 'ROM/0/6.DAT'),
    ('Armor_2',       23040, 3, 'ROM/286/73.DAT',  'ROM/286/72.DAT'),
    ('Moblin',        28672, 0, 'ROM/217/21.DAT',  'ROM/217/20.DAT'),
    ('Monstrosity_1', 29696, 0, 'ROM/288/80.DAT',  'ROM/288/79.DAT'),
    # Custom_Items shares the same DAT as Monstrosity_1
    ('RoE_Objectives',57344, 0, 'ROM/307/16.DAT',  'ROM/307/15.DAT'),
    ('Items_3',       61432, 0, 'ROM/314/89.DAT',  'ROM/314/89.DAT'),
    ('Monstrosity_2', 61440, 0, 'ROM/288/67.DAT',  'ROM/288/66.DAT'),
    ('RoE_Categories',61952, 0, 'ROM/307/24.DAT',  'ROM/307/23.DAT'),
    ('Items_4',       62976, 0, 'ROM/320/26.DAT',  'ROM/320/26.DAT'),
    ('Items_5',       63008, 0, 'ROM/332/49.DAT',  'ROM/332/47.DAT'),
    ('Items_6',       63024, 0, 'ROM/332/48.DAT',  'ROM/332/46.DAT'),
    ('Gil',           65535, 0, 'ROM/174/48.DAT',  'ROM/0/9.DAT'),
]

TYPE_NAME = {0: 'general', 1: 'consumable', 3: 'armor', 4: 'weapon', 5: 'puppet', 6: 'furnishing'}


@dataclass
class ItemRecord:
    id: int
    type: int
    type_name: str
    flags: int
    stack: int = 1
    resource_id: int = 0  # offset 0x0A — resource/icon identifier (NOT a file_id; gear model_id is separate in item_equipment DB)
    targets: int = 0
    # text fields
    name: str = ''
    singular: str = ''
    plural: str = ''
    description: str = ''
    name_jp: str = ''
    description_jp: str = ''
    # equipment fields (weapons/armor)
    level: int = 0
    slots: int = 0
    races: int = 0
    jobs: int = 0         # u32 at 0x14 — 22-bit bitmask (bits 0-21 = WAR through RUN)
    superior_level: int = 0  # 0x18 — item level (superior/ilvl)
    # weapon-specific
    kind: int = 0         # 0x08 — item type from record (4=weapon)
    dmg: int = 0          # 0x1C
    delay: int = 0        # 0x1E
    dps: int = 0          # 0x20
    skill: int = 0        # 0x22 (u8)
    # icon
    icon_size: int = 0
    icon_data: bytes = field(default_factory=bytes, repr=False)
    # source
    dat: str = ''
    dat_ui: str = ''   # ROM-relative path e.g. ROM/118/106.DAT


def encode_flags(decoded: list) -> int:
    name_to_bit = {name: bit for bit, name in ITEM_FLAGS.items()}
    result = 0
    for name in decoded:
        if name in name_to_bit:
            result |= name_to_bit[name]
    return result


def encode_jobs(jobs_list: list) -> int:
    job_to_bit = {job: (1 << i) for i, job in enumerate(JOBS)}
    result = 0
    for job in jobs_list:
        if job.upper() in job_to_bit:
            result |= job_to_bit[job.upper()]
    return result


def _decrypt(data: bytes) -> bytes:
    return bytes(((b >> 5) | (b << 3)) & 0xFF for b in data)


def _encrypt(data: bytes) -> bytes:
    return bytes(((b >> 3) | (b << 5)) & 0xFF for b in data)


def _read_strings(rec: bytes, text_off: int) -> list:
    """Parse the d_msg text section at text_off. Returns list of strings (empty for numeric slots)."""
    base = rec[text_off:]
    if len(base) < 4:
        return []
    n = struct.unpack_from('<I', base, 0)[0]
    if n == 0 or n > 16:
        return []
    if 4 + n * 8 > len(base):
        return []
    results = []
    for j in range(n):
        str_off  = struct.unpack_from('<I', base, 4 + j * 8)[0]
        str_type = struct.unpack_from('<I', base, 4 + j * 8 + 4)[0]
        if str_type == 0:  # string
            p = str_off + 0x1C
            if p >= len(base):
                results.append('')
                continue
            end = base.find(b'\x00', p)
            raw = base[p:end] if end != -1 else base[p:]
            try:
                results.append(raw.decode('cp932'))
            except Exception:
                results.append(raw.decode('latin-1', 'replace'))
        else:
            results.append('')  # numeric slot — keep index alignment
    return results


def _parse_record(item_id: int, rec_en: bytes, rec_jp: Optional[bytes], item_type: int, dat_path: str, dat_ui: str = '') -> Optional[ItemRecord]:
    text_off = TEXT_OFFSETS.get(item_type, 0x18)

    en = _read_strings(rec_en, text_off)
    if not en or not en[0] or en[0] == '.':
        return None  # placeholder slot

    jp = _read_strings(rec_jp, text_off) if rec_jp else []

    flags       = struct.unpack_from('<H', rec_en, 0x04)[0]
    stack       = struct.unpack_from('<H', rec_en, 0x06)[0]
    resource_id = struct.unpack_from('<H', rec_en, 0x0A)[0]
    targets     = struct.unpack_from('<H', rec_en, 0x0C)[0]

    item = ItemRecord(
        id=item_id,
        type=item_type,
        type_name=TYPE_NAME.get(item_type, str(item_type)),
        flags=flags,
        stack=stack,
        resource_id=resource_id,
        targets=targets,
        name=en[0] if len(en) > 0 else '',
        singular=en[2] if len(en) > 2 else '',
        plural=en[3] if len(en) > 3 else '',
        description=en[4] if len(en) > 4 else '',
        name_jp=jp[0] if len(jp) > 0 else '',
        description_jp=jp[1] if len(jp) > 1 else '',
        dat=dat_path,
        dat_ui=dat_ui,
    )

    if item_type in (3, 4):  # armor or weapon
        item.level   = struct.unpack_from('<H', rec_en, 0x0E)[0]
        item.slots   = struct.unpack_from('<H', rec_en, 0x10)[0]
        item.races   = struct.unpack_from('<H', rec_en, 0x12)[0]
        item.jobs    = struct.unpack_from('<I', rec_en, 0x14)[0]  # u32 — full 22-bit job mask
        item.superior_level = struct.unpack_from('<H', rec_en, 0x18)[0]

    if item_type == 4:  # weapon
        item.kind  = struct.unpack_from('<H', rec_en, 0x08)[0]
        item.dmg   = struct.unpack_from('<H', rec_en, 0x1C)[0]
        item.delay = struct.unpack_from('<H', rec_en, 0x1E)[0]
        item.dps   = struct.unpack_from('<H', rec_en, 0x20)[0]
        item.skill = rec_en[0x22]  # u8

    icon_size = struct.unpack_from('<I', rec_en, 0x280)[0]
    if icon_size > 0 and 0x284 + icon_size <= STRIDE:
        item.icon_size = icon_size
        item.icon_data = rec_en[0x284:0x284 + icon_size]

    return item


def _write_strings(strings: list, text_off: int, rec: bytearray) -> None:
    """Write a list of strings into the text section of a decrypted record buffer (in-place).

    Format mirrors _read_strings: base = rec[text_off:]
      base[0..3]          u32  n
      base[4 + j*8]       u32  str_off  (index into base such that base[str_off+0x1C] = string)
      base[4 + j*8 + 4]   u32  str_type (0=string, 1=numeric/empty)
      base[str_off + 0x1C] ... string bytes (cp932, NUL-terminated)
    """
    n = len(strings)
    struct.pack_into('<I', rec, text_off, n)

    # String data area starts at max(end_of_table, 0x1C) relative to text_off
    table_end = 4 + n * 8
    data_start = max(table_end, 0x1C)  # from text_off

    cursor = 0
    for i, s in enumerate(strings):
        if s and isinstance(s, str):
            str_type = 0
            str_off  = (data_start - 0x1C) + cursor
            encoded  = s.encode('cp932') + b'\x00'
        else:
            str_type = 1
            str_off  = 0
            encoded  = b''
        struct.pack_into('<I', rec, text_off + 4 + i * 8,     str_off)
        struct.pack_into('<I', rec, text_off + 4 + i * 8 + 4, str_type)
        if encoded:
            pos = text_off + data_start + cursor
            rec[pos:pos + len(encoded)] = encoded
            cursor += len(encoded)


def _patch_record(rec: bytearray, entry: dict, item_type: int) -> None:
    """Patch numeric fields (and optionally text) in a decrypted record buffer from a dict.

    Supports decoded helpers: jobs_list → jobs, flags_decoded → flags.
    Does NOT re-encrypt — caller must encrypt after all patches.
    """
    # Resolve decoded helpers first
    if 'jobs_list' in entry and 'jobs' not in entry:
        entry = {**entry, 'jobs': encode_jobs(entry['jobs_list'])}
    if 'flags_decoded' in entry and 'flags' not in entry:
        entry = {**entry, 'flags': encode_flags(entry['flags_decoded'])}
    # If both explicit and decoded provided, decoded wins only for consistency —
    # prefer the explicit decimal if present.
    if 'jobs_list' in entry and 'jobs' in entry:
        # explicit 'jobs' takes priority; decoded is display-only
        pass
    if 'flags_decoded' in entry and 'flags' in entry:
        pass

    def _u16(key, off):
        if key in entry:
            struct.pack_into('<H', rec, off, int(entry[key]) & 0xFFFF)

    def _u32(key, off):
        if key in entry:
            struct.pack_into('<I', rec, off, int(entry[key]) & 0xFFFFFFFF)

    _u16('flags',       0x04)
    _u16('stack',       0x06)
    _u16('resource_id', 0x0A)
    _u16('targets',     0x0C)

    if item_type in (3, 4):
        _u16('level',          0x0E)
        _u16('slots',          0x10)
        _u16('races',          0x12)
        _u32('jobs',           0x14)  # u32 — full 22-bit job mask
        _u16('superior_level', 0x18)

    if item_type == 4:
        _u16('kind',  0x08)
        _u16('dmg',   0x1C)
        _u16('delay', 0x1E)
        _u16('dps',   0x20)
        if 'skill' in entry:
            struct.pack_into('B', rec, 0x22, int(entry['skill']) & 0xFF)

    # Text fields — only written when explicitly present in entry
    text_keys = {'name', 'singular', 'plural', 'description'}
    if text_keys & entry.keys():
        text_off = TEXT_OFFSETS.get(item_type, 0x18)
        strings = [
            entry.get('name', ''),
            '',   # numeric slot (index 1 is always numeric)
            entry.get('singular', entry.get('name', '')),
            entry.get('plural',   entry.get('name', '') + 's'),
            entry.get('description', ''),
        ]
        _write_strings(strings, text_off, rec)


def build_record(entry: dict, item_type: int) -> bytes:
    """Build a full 0xC00-byte DECRYPTED item record from a dict.  Caller must encrypt."""
    rec = bytearray(STRIDE)
    struct.pack_into('<H', rec, 0x00, item_type)
    _patch_record(rec, entry, item_type)
    return bytes(rec)


def parse_dat(ffxi_dir: str, cat_name: str, base_id: int, item_type: int,
              en_rom: str, jp_rom: str):
    """Generator: yields ItemRecord for every real item in the DAT.
    Prints which DAT it is processing to stdout before iterating."""
    from xi.xi_config import read_path_for, FFXI_DIR

    en_path = Path(FFXI_DIR) / Path(en_rom.replace('/', '\\'))
    jp_path = Path(FFXI_DIR) / Path(jp_rom.replace('/', '\\'))

    if not en_path.exists():
        return

    en_data = _decrypt(en_path.read_bytes())
    jp_data = _decrypt(jp_path.read_bytes()) if jp_path.exists() else None

    n_records = len(en_data) // STRIDE

    for idx in range(n_records):
        item_id = base_id + idx
        rec_en = en_data[idx * STRIDE:(idx + 1) * STRIDE]
        rec_jp = jp_data[idx * STRIDE:(idx + 1) * STRIDE] if jp_data and idx * STRIDE < len(jp_data) else None
        item = _parse_record(item_id, rec_en, rec_jp, item_type, str(en_path), dat_ui=en_rom)
        if item is not None:
            yield item

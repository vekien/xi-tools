"""Binary item DAT parser for FFXI item DATs.

Cipher: every byte is rotated left 3 bits — (b >> 5) | (b << 3).
Record stride: 0xC00 bytes per item on a legacy install, 0x1400 on retail
since the 10 September 2026 update (see ``xi_layout`` — the stride and the
header offsets are detected per file, never assumed).
Text section offset varies by item type (derived from ShiningFantasia item.ts).
Text section is a d_msg-style entry: n (u32), n×8 offset table, strings at offset+0x1C.
Icon bitmap: u32 size at 0x280, BMP2 blob at 0x284 (unchanged in both formats).
"""

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from xi.ui.items.xi_layout import (
    FORMAT_LEGACY, FORMAT_RETAIL, FORMAT_BY_STRIDE, STRIDE_BY_FORMAT,
    STRIDE_LEGACY, STRIDE_RETAIL, ICON_OFFSET, ICON_DATA, TERMINATOR,
    detect_stride, detect_stride_path, format_for_stride, layout_for_type,
    read_field, write_field, text_offset, find_text_offset,
)

# Legacy record stride, kept for callers that import the old name. Nothing in
# this package assumes it any more — use ``detect_stride`` / ``ItemDat``.
STRIDE = STRIDE_LEGACY

# Job restriction bitmask — bit N corresponds to JOBS[N]
JOBS = [
    'WAR', 'MNK', 'WHM', 'BLM', 'RDM', 'THF',
    'PLD', 'DRK', 'BST', 'BRD', 'RNG', 'SAM',
    'NIN', 'DRG', 'SMN', 'BLU', 'COR', 'PUP',
    'DNC', 'SCH', 'GEO', 'RUN',
]

# Item flags at record+0x04 (u16 low word in both formats)
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


# Per-type text section offset within the decrypted record — LEGACY layout.
# Kept as the historical name; ``text_offset_for(item_type, fmt)`` is what the
# parser uses (it knows both formats).
# type id matches xidats/ShiningFantasia ItemType: 0=General, 1=Usable, 3=Armor, 4=Weapon, 6=Furnishing
TEXT_OFFSETS = {
    0: 0x18,   # General
    1: 0x1C,   # Usable/Consumable
    3: 0x2C,   # Armor
    4: 0x38,   # Weapon
    5: 0x18,   # Puppet (same layout as general)
    6: 0x54,   # Furnishing / Maze Mongers tabulae
}


def text_offset_for(item_type: int, fmt: str = FORMAT_LEGACY) -> int:
    """String-block offset for an item type in the given record format."""
    return text_offset(layout_for_type(item_type), fmt)


# DAT categories: (name, base_id, item_type, en_rom_path, jp_rom_path)
ITEM_DATS = [
    ('Items_1',       0,     0, 'ROM/118/106.DAT', 'ROM/0/4.DAT'),
    ('Consumable',    4096,  1, 'ROM/118/107.DAT', 'ROM/0/5.DAT'),
    ('Puppet',        8192,  5, 'ROM/118/110.DAT', 'ROM/0/8.DAT'),
    ('Items_2',       8704,  0, 'ROM/301/115.DAT', 'ROM/301/114.DAT'),
    ('Armor_1',       10240, 3, 'ROM/118/109.DAT', 'ROM/0/7.DAT'),
    ('Weapons',       16384, 4, 'ROM/118/108.DAT', 'ROM/0/6.DAT'),
    ('Armor_2',       23040, 3, 'ROM/286/73.DAT',  'ROM/286/72.DAT'),
    ('Moblin',        28672, 6, 'ROM/217/21.DAT',  'ROM/217/20.DAT'),
    ('Monstrosity_1', 29696, 0, 'ROM/288/80.DAT',  'ROM/288/79.DAT'),
    # Added by the 10 September 2026 retail update: a fresh 1024-slot general
    # item table (ids 30720-31743), all placeholders at launch. Absent on a
    # legacy install — the loaders skip DATs that do not exist.
    ('Items_7',       30720, 0, 'ROM/387/14.DAT',  'ROM/387/13.DAT'),
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
    resource_id: int = 0  # resource/icon identifier (NOT a file_id; gear model_id is separate in item_equipment DB)
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
    jobs: int = 0         # u32 — 22-bit bitmask (bits 0-21 = WAR through RUN)
    superior_level: int = 0  # item level (superior/ilvl)
    # weapon-specific
    kind: int = 0         # item type from record (4=weapon)
    dmg: int = 0
    delay: int = 0
    dps: int = 0
    skill: int = 0        # u8
    # icon
    icon_size: int = 0
    icon_data: bytes = field(default_factory=bytes, repr=False)
    # source
    dat: str = ''
    dat_ui: str = ''   # ROM-relative path e.g. ROM/118/106.DAT
    # record format the DAT was read in: 'legacy' (0xC00) or 'retail' (0x1400)
    format: str = FORMAT_LEGACY
    record_index: int = 0


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


_DECRYPT = bytes(((b >> 5) | (b << 3)) & 0xFF for b in range(256))
_ENCRYPT = bytes(((b >> 3) | (b << 5)) & 0xFF for b in range(256))


def _decrypt(data: bytes) -> bytes:
    return bytes(data).translate(_DECRYPT)


def _encrypt(data: bytes) -> bytes:
    return bytes(data).translate(_ENCRYPT)


# ── format-aware DAT handle ──────────────────────────────────────────────────

@dataclass
class ItemDat:
    """One item DAT loaded and decrypted, with its record format detected.

    ``ItemDat.load(path)`` is the single way the item tools open a DAT: the
    stride comes from the bytes (legacy 0xC00 / retail 0x1400), so every
    command works unchanged against a legacy install and a current retail one.
    """
    path: Path
    data: bytearray          # decrypted
    stride: int

    @classmethod
    def load(cls, path) -> 'ItemDat':
        p = Path(path)
        raw = p.read_bytes()
        stride = detect_stride(raw)
        return cls(path=p, data=bytearray(_decrypt(raw)), stride=stride)

    @property
    def format(self) -> str:
        return format_for_stride(self.stride)

    @property
    def count(self) -> int:
        return len(self.data) // self.stride

    def record(self, idx: int) -> bytes:
        return bytes(self.data[idx * self.stride:(idx + 1) * self.stride])

    def set_record(self, idx: int, rec: bytes) -> None:
        if len(rec) != self.stride:
            raise ValueError(f'record is {len(rec)} bytes; this DAT uses {self.stride:#x}-byte records')
        self.data[idx * self.stride:(idx + 1) * self.stride] = rec

    def encrypted(self) -> bytes:
        return _encrypt(bytes(self.data))

    @property
    def icon_capacity(self) -> int:
        """Largest icon blob a record can hold (the tail past the icon is reserved)."""
        return self.stride - ICON_DATA - 1


def record_count(path) -> int:
    """Number of records in an item DAT without reading it whole."""
    p = Path(path)
    return p.stat().st_size // detect_stride_path(p)


# ── strings ─────────────────────────────────────────────────────────────────

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


def _resolve_text_offset(rec: bytes, item_type: int, fmt: str) -> Optional[int]:
    """The string block for this record: the layout's offset for ``fmt`` when it
    checks out, otherwise a scan (tables typed ``general`` that really use the
    maze/instinct/RoE layouts land here)."""
    return find_text_offset(rec, text_offset_for(item_type, fmt))


def _parse_record(item_id: int, rec_en: bytes, rec_jp: Optional[bytes], item_type: int,
                  dat_path: str, dat_ui: str = '', fmt: Optional[str] = None,
                  record_index: int = 0) -> Optional[ItemRecord]:
    if fmt is None:
        fmt = format_for_stride(len(rec_en))
    layout = layout_for_type(item_type)

    text_off = _resolve_text_offset(rec_en, item_type, fmt)
    if text_off is None:
        return None
    en = _read_strings(rec_en, text_off)
    if not en or not en[0] or en[0] == '.':
        return None  # placeholder slot

    jp = []
    if rec_jp:
        jp_off = _resolve_text_offset(rec_jp, item_type, format_for_stride(len(rec_jp)))
        if jp_off is not None:
            jp = _read_strings(rec_jp, jp_off)

    rf = lambda name: read_field(rec_en, layout, fmt, name)

    item = ItemRecord(
        id=item_id,
        type=item_type,
        type_name=TYPE_NAME.get(item_type, str(item_type)),
        flags=rf('flags'),
        stack=rf('stack'),
        resource_id=rf('resource_id'),
        targets=rf('targets'),
        name=en[0] if len(en) > 0 else '',
        singular=en[2] if len(en) > 2 else '',
        plural=en[3] if len(en) > 3 else '',
        description=en[4] if len(en) > 4 else '',
        name_jp=jp[0] if len(jp) > 0 else '',
        description_jp=jp[1] if len(jp) > 1 else '',
        dat=dat_path,
        dat_ui=dat_ui,
        format=fmt,
        record_index=record_index,
    )

    if item_type in (3, 4):  # armor or weapon
        item.level   = rf('level')
        item.slots   = rf('slots')
        item.races   = rf('races')
        item.jobs    = rf('jobs')
        item.superior_level = rf('superior_level')

    if item_type == 4:  # weapon
        item.kind  = rf('type')
        item.dmg   = rf('dmg')
        item.delay = rf('delay')
        item.dps   = rf('dps')
        item.skill = rf('skill')

    icon_size = struct.unpack_from('<I', rec_en, ICON_OFFSET)[0]
    if icon_size > 0 and ICON_DATA + icon_size <= len(rec_en):
        item.icon_size = icon_size
        item.icon_data = rec_en[ICON_DATA:ICON_DATA + icon_size]

    return item


def _write_strings(strings: list, text_off: int, rec: bytearray) -> None:
    """Write a list of strings into the text section of a decrypted record buffer (in-place).

    Lays the block out exactly as the client's own records do (and as
    ``_read_strings`` / the model viewer read it): base = rec[text_off:]
      base[0..3]            u32  n
      base[4 + j*8]         u32  offset   (from base) of entry j's object
      base[4 + j*8 + 4]     u32  flag     (0 = text object, 1 = bare number)
      objects, packed after the table starting at 4 + n*8:
        text object   u32 1, 24 zero bytes, cp932 bytes, NUL — padded to 4 bytes
        number object u32 value
    A list element that is a str is a text object (an empty str still writes
    an empty text object); an int (or None) is a number object.
    """
    n = len(strings)
    struct.pack_into('<I', rec, text_off, n)
    cursor = 4 + n * 8
    for i, s in enumerate(strings):
        pos = text_off + cursor
        if isinstance(s, str):
            encoded = s.encode('cp932') + b'\x00'
            obj_len = (0x1C + len(encoded) + 3) & ~3
            if pos + obj_len > ICON_OFFSET:
                raise ValueError('item text does not fit before the icon at 0x280')
            rec[pos:pos + obj_len] = b'\x00' * obj_len
            struct.pack_into('<I', rec, pos, 1)
            rec[pos + 0x1C:pos + 0x1C + len(encoded)] = encoded
            flag = 0
        else:
            obj_len = 4
            if pos + obj_len > ICON_OFFSET:
                raise ValueError('item text does not fit before the icon at 0x280')
            struct.pack_into('<I', rec, pos, int(s or 0) & 0xFFFFFFFF)
            flag = 1
        struct.pack_into('<I', rec, text_off + 4 + i * 8, cursor)
        struct.pack_into('<I', rec, text_off + 4 + i * 8 + 4, flag)
        cursor += obj_len


def _patch_record(rec: bytearray, entry: dict, item_type: int, fmt: Optional[str] = None) -> None:
    """Patch numeric fields (and optionally text) in a decrypted record buffer from a dict.

    ``fmt`` is the record format the buffer is in ('legacy' / 'retail'); when
    omitted it is taken from the buffer length. Field names are the same in
    both formats — only their offsets differ.

    Supports decoded helpers: jobs_list → jobs, flags_decoded → flags.
    Does NOT re-encrypt — caller must encrypt after all patches.
    """
    if fmt is None:
        fmt = format_for_stride(len(rec))
    layout = layout_for_type(item_type)

    # Resolve decoded helpers first
    if 'jobs_list' in entry and 'jobs' not in entry:
        entry = {**entry, 'jobs': encode_jobs(entry['jobs_list'])}
    if 'flags_decoded' in entry and 'flags' not in entry:
        entry = {**entry, 'flags': encode_flags(entry['flags_decoded'])}

    def _set(key, name=None):
        if key in entry:
            write_field(rec, layout, fmt, name or key, int(entry[key]))

    _set('flags')
    _set('stack')
    _set('resource_id')
    _set('targets')

    if item_type in (3, 4):
        _set('level')
        _set('slots')
        _set('races')
        _set('jobs')
        _set('superior_level')

    if item_type == 4:
        _set('kind', 'type')
        _set('dmg')
        _set('delay')
        _set('dps')
        _set('skill')

    # Text fields — only written when explicitly present in entry
    text_keys = {'name', 'singular', 'plural', 'description'}
    if text_keys & entry.keys():
        text_off = text_offset_for(item_type, fmt)
        strings = [
            entry.get('name', ''),
            int(entry.get('article', 0) or 0),   # numeric slot (index 1 is always a number)
            entry.get('singular', entry.get('name', '')),
            entry.get('plural',   entry.get('name', '') + 's'),
            entry.get('description', ''),
        ]
        _write_strings(strings, text_off, rec)


def build_record(entry: dict, item_type: int, fmt: str = FORMAT_LEGACY) -> bytes:
    """Build a full DECRYPTED item record from a dict in the given format
    (``'legacy'`` = 0xC00 bytes, ``'retail'`` = 0x1400). Caller must encrypt."""
    stride = STRIDE_BY_FORMAT[fmt]
    rec = bytearray(stride)
    struct.pack_into('<H', rec, 0x00, item_type)
    rec[stride - 1] = TERMINATOR
    _patch_record(rec, entry, item_type, fmt)
    return bytes(rec)


def parse_dat(ffxi_dir: str, cat_name: str, base_id: int, item_type: int,
              en_rom: str, jp_rom: str):
    """Generator: yields ItemRecord for every real item in the DAT.

    The EN and JP DATs are each opened through ``ItemDat.load`` so a legacy
    (0xC00) and a retail (0x1400) file decode the same way — even mixed."""
    from xi.xi_config import FFXI_DIR

    en_path = Path(FFXI_DIR) / Path(en_rom.replace('/', '\\'))
    jp_path = Path(FFXI_DIR) / Path(jp_rom.replace('/', '\\'))

    if not en_path.exists():
        return

    en = ItemDat.load(en_path)
    jp = ItemDat.load(jp_path) if jp_path.exists() and jp_path != en_path else None

    for idx in range(en.count):
        item_id = base_id + idx
        rec_en = en.record(idx)
        rec_jp = jp.record(idx) if jp is not None and idx < jp.count else None
        item = _parse_record(item_id, rec_en, rec_jp, item_type, str(en_path),
                             dat_ui=en_rom, fmt=en.format, record_index=idx)
        if item is not None:
            yield item

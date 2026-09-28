"""Item DAT record layouts — legacy (``0xC00``) and retail September 2026 (``0x1400``).

The 10 September 2026 retail update grew every item record from 3072 to 5120
bytes and widened the header (``docs/retail/update_september_10_2026.md``).
A *legacy install* — any client from before that update, which is what most
private servers still ship — keeps the old records. Both formats are live, so
every reader and writer goes through this module:

* :func:`detect_stride` / :func:`detect_format` tell the two apart from the
  bytes alone (file size, then the ``0xFF`` byte that ends every record).
* :data:`FIELDS` holds the header offset of each decoded field, per layout and
  per format. The retail change is mechanical — ``flags`` became a u32 at
  ``0x04`` (shifting what follows by 2) and one more 2-byte pad appears after
  ``races`` in the equipment layouts (shifting the rest by 4) — but the exact
  spots differ per layout, so they are tabulated rather than computed.
* :data:`TEXT_OFFSETS` is where the string block starts, per layout and format.

Verified field by field against 30,000+ item records present in both formats.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

STRIDE_LEGACY = 0xC00    # 3072 — every client before 10 September 2026
STRIDE_RETAIL = 0x1400   # 5120 — retail from 10 September 2026
STRIDES = (STRIDE_RETAIL, STRIDE_LEGACY)

FORMAT_LEGACY = 'legacy'
FORMAT_RETAIL = 'retail'
FORMAT_BY_STRIDE = {STRIDE_LEGACY: FORMAT_LEGACY, STRIDE_RETAIL: FORMAT_RETAIL}
STRIDE_BY_FORMAT = {v: k for k, v in FORMAT_BY_STRIDE.items()}

# The icon (u32 size + BMP blob) did not move; the record grew below it.
ICON_OFFSET = 0x280
ICON_DATA = ICON_OFFSET + 4

# Every record ends in a 0xFF byte (the byte is 0xFF before and after the
# rotate-3 cipher, so it can be tested on raw file bytes).
TERMINATOR = 0xFF

# ── layouts ──────────────────────────────────────────────────────────────────
# Layout names are the ones the model viewer's database registry uses; the
# ``ItemType`` ints of the item CLI map onto them through :data:`TYPE_LAYOUT`.

LAYOUTS = ('general', 'usable', 'puppet', 'armor', 'weapon', 'maze', 'instinct', 'roe')

# item_type (xidats / ShiningFantasia ItemType) -> layout name
TYPE_LAYOUT = {0: 'general', 1: 'usable', 3: 'armor', 4: 'weapon', 5: 'puppet', 6: 'maze'}

# Header fields shared by every layout. ``flags`` is the only field that
# changed width (u16 -> u32); it is read as the u16 low word in both formats so
# the decoded value is identical across installs.
_COMMON_LEGACY = {
    'id':          (0x00, '<I'),
    'flags':       (0x04, '<H'),
    'stack':       (0x06, '<H'),
    'type':        (0x08, '<H'),
    'resource_id': (0x0A, '<H'),
    'targets':     (0x0C, '<H'),
}
_COMMON_RETAIL = {
    'id':          (0x00, '<I'),
    'flags':       (0x04, '<H'),
    'stack':       (0x08, '<H'),
    'type':        (0x0A, '<H'),
    'resource_id': (0x0C, '<H'),
    'targets':     (0x0E, '<H'),
}

_EQUIP_LEGACY = {
    'level':          (0x0E, '<H'),
    'slots':          (0x10, '<H'),
    'races':          (0x12, '<H'),
    'jobs':           (0x14, '<I'),
    'superior_level': (0x18, '<H'),
}
_EQUIP_RETAIL = {
    'level':          (0x10, '<H'),
    'slots':          (0x12, '<H'),
    'races':          (0x14, '<H'),
    'jobs':           (0x18, '<I'),
    'superior_level': (0x1C, '<H'),
}

FIELDS: dict[str, dict[str, dict[str, tuple[int, str]]]] = {
    'general': {
        FORMAT_LEGACY: {**_COMMON_LEGACY},
        FORMAT_RETAIL: {**_COMMON_RETAIL},
    },
    'usable': {
        FORMAT_LEGACY: {**_COMMON_LEGACY, 'cast_time': (0x0E, '<H')},
        FORMAT_RETAIL: {**_COMMON_RETAIL, 'cast_time': (0x10, '<H')},
    },
    'puppet': {
        FORMAT_LEGACY: {**_COMMON_LEGACY, 'puppet_slot': (0x0E, '<H'), 'element_charge': (0x10, '<I')},
        FORMAT_RETAIL: {**_COMMON_RETAIL, 'puppet_slot': (0x10, '<H'), 'element_charge': (0x14, '<I')},
    },
    'armor': {
        FORMAT_LEGACY: {
            **_COMMON_LEGACY, **_EQUIP_LEGACY,
            'shield_size': (0x1A, '<H'), 'max_charges': (0x1C, '<H'), 'cast_time': (0x1E, '<H'),
            'use_delay': (0x20, '<H'), 'reuse_delay': (0x22, '<H'), 'item_level': (0x26, '<H'),
        },
        FORMAT_RETAIL: {
            **_COMMON_RETAIL, **_EQUIP_RETAIL,
            'shield_size': (0x1E, '<H'), 'max_charges': (0x20, '<H'), 'cast_time': (0x22, '<H'),
            'use_delay': (0x24, '<H'), 'reuse_delay': (0x26, '<H'), 'item_level': (0x2A, '<H'),
        },
    },
    'weapon': {
        FORMAT_LEGACY: {
            **_COMMON_LEGACY, **_EQUIP_LEGACY,
            'dmg': (0x1C, '<H'), 'delay': (0x1E, '<H'), 'dps': (0x20, '<H'),
            'skill': (0x22, '<B'), 'jug_size': (0x23, '<B'),
            'max_charges': (0x28, '<H'), 'cast_time': (0x2A, '<H'), 'use_delay': (0x2C, '<H'),
            'reuse_delay': (0x2E, '<H'), 'base_item_id': (0x30, '<H'), 'item_level': (0x32, '<H'),
        },
        FORMAT_RETAIL: {
            **_COMMON_RETAIL, **_EQUIP_RETAIL,
            'dmg': (0x20, '<H'), 'delay': (0x22, '<H'), 'dps': (0x24, '<H'),
            'skill': (0x26, '<B'), 'jug_size': (0x27, '<B'),
            'max_charges': (0x2C, '<H'), 'cast_time': (0x2E, '<H'), 'use_delay': (0x30, '<H'),
            'reuse_delay': (0x32, '<H'), 'base_item_id': (0x34, '<H'), 'item_level': (0x36, '<H'),
        },
    },
    # Maze Mongers tabulae / runes (ROM/217): the common fields shift by 2 and
    # the old 0x10 pad absorbs it, so everything from 0x14 stays put.
    'maze': {
        FORMAT_LEGACY: {**_COMMON_LEGACY},
        FORMAT_RETAIL: {**_COMMON_RETAIL},
    },
    # Monstrosity instincts (ROM/288/80): equipment-like shift (+2, then +4 after races).
    'instinct': {
        FORMAT_LEGACY: {**_COMMON_LEGACY, 'level': (0x0E, '<H'), 'races': (0x12, '<H'), 'instinct_cost': (0x18, '<H')},
        FORMAT_RETAIL: {**_COMMON_RETAIL, 'level': (0x10, '<H'), 'races': (0x14, '<H'), 'instinct_cost': (0x1C, '<H')},
    },
    # Records of Eminence objectives (ROM/307/16): the header did not change at
    # all — only the record stride did.
    'roe': {
        FORMAT_LEGACY: {**_COMMON_LEGACY},
        FORMAT_RETAIL: {**_COMMON_LEGACY},
    },
}

# Where the string block (u32 count, count x {u32 offset, u32 flag}) starts.
TEXT_OFFSETS: dict[str, dict[str, int]] = {
    'general':  {FORMAT_LEGACY: 0x18, FORMAT_RETAIL: 0x1C},
    'usable':   {FORMAT_LEGACY: 0x1C, FORMAT_RETAIL: 0x1C},
    'puppet':   {FORMAT_LEGACY: 0x18, FORMAT_RETAIL: 0x1C},
    'armor':    {FORMAT_LEGACY: 0x2C, FORMAT_RETAIL: 0x30},
    'weapon':   {FORMAT_LEGACY: 0x38, FORMAT_RETAIL: 0x3C},
    'maze':     {FORMAT_LEGACY: 0x54, FORMAT_RETAIL: 0x54},
    'instinct': {FORMAT_LEGACY: 0x28, FORMAT_RETAIL: 0x2C},
    'roe':      {FORMAT_LEGACY: 0x20, FORMAT_RETAIL: 0x20},
}


def _unknown_fields(layout: str) -> dict[str, tuple[int, str]]:
    """The header bytes of a legacy record the named fields don't cover, as u16 fields
    ``unknown_<offset hex>`` (a u8 for an odd byte): real values the client has (general
    items hold 0xFFFF at 0x0E, most armor 0x0100 at 0x2A) but nobody has named yet. They make
    a whole record expressible as fields; they stay out of ``FIELDS`` (and so out of the
    viewer's exports) until they have names. Legacy only: their retail offsets are unmapped."""
    named = FIELDS[layout][FORMAT_LEGACY]
    covered = set()
    for off, sfmt in named.values():
        covered.update(range(off, off + struct.calcsize(sfmt)))
    out: dict[str, tuple[int, str]] = {}
    off = 0
    end = TEXT_OFFSETS[layout][FORMAT_LEGACY]
    while off < end:
        if off in covered:
            off += 1
            continue
        if off + 1 < end and off + 1 not in covered:
            out[f'unknown_{off:02x}'] = (off, '<H')
            off += 2
        else:
            out[f'unknown_{off:02x}'] = (off, '<B')
            off += 1
    return out


UNKNOWN_FIELDS: dict[str, dict[str, dict[str, tuple[int, str]]]] = {
    layout: {FORMAT_LEGACY: _unknown_fields(layout)}
    for layout in ('general', 'usable', 'armor', 'weapon', 'maze')
}


def _spec(layout: str, fmt: str, name: str) -> tuple[int, str] | None:
    spec = fields_for(layout, fmt).get(name)
    if spec is None:
        spec = UNKNOWN_FIELDS.get(layout, {}).get(fmt, {}).get(name)
    return spec


def layout_for_type(item_type: int) -> str:
    return TYPE_LAYOUT.get(item_type, 'general')


def fields_for(layout: str, fmt: str) -> dict[str, tuple[int, str]]:
    """Field table for ``layout`` in ``fmt``; unknown layouts fall back to ``general``."""
    return FIELDS.get(layout, FIELDS['general'])[fmt]


def text_offset(layout: str, fmt: str) -> int:
    return TEXT_OFFSETS.get(layout, TEXT_OFFSETS['general'])[fmt]


def read_field(rec: bytes, layout: str, fmt: str, name: str, default: int = 0) -> int:
    spec = _spec(layout, fmt, name)
    if spec is None:
        return default
    off, sfmt = spec
    if off + struct.calcsize(sfmt) > len(rec):
        return default
    return struct.unpack_from(sfmt, rec, off)[0]


def write_field(rec: bytearray, layout: str, fmt: str, name: str, value: int) -> bool:
    """Store ``value`` into ``name``; False when the layout has no such field."""
    spec = _spec(layout, fmt, name)
    if spec is None:
        return False
    off, sfmt = spec
    size = struct.calcsize(sfmt)
    mask = (1 << (8 * size)) - 1
    struct.pack_into(sfmt, rec, off, int(value) & mask)
    if name == 'flags' and fmt == FORMAT_RETAIL:
        struct.pack_into('<H', rec, off + 2, 0)   # keep the widened high word clear
    return True


# ── string block ────────────────────────────────────────────────────────────

def string_block_valid(rec: bytes, off: int) -> bool:
    """True when a d_msg-style string block plausibly starts at ``off``."""
    if off < 0 or off + 8 > len(rec):
        return False
    n = struct.unpack_from('<I', rec, off)[0]
    if not 1 <= n <= 16:
        return False
    first = struct.unpack_from('<I', rec, off + 4)[0]
    return first == 4 + n * 8 and off + first < min(len(rec), ICON_OFFSET)


def find_text_offset(rec: bytes, expected: int | None = None) -> int | None:
    """Locate the string block: the expected offset when it checks out, else a
    scan of the header (the layouts are small; the block is never past 0x90)."""
    if expected is not None and string_block_valid(rec, expected):
        return expected
    for off in range(0x08, 0x92, 2):
        if string_block_valid(rec, off):
            return off
    return None


# ── format detection ────────────────────────────────────────────────────────

def _terminator_ratio(data: bytes, stride: int) -> float:
    count = len(data) // stride
    if not count:
        return 0.0
    hits = sum(1 for i in range(stride - 1, len(data), stride) if data[i] == TERMINATOR)
    return hits / count


def _ids_sequential(data: bytes, stride: int, probe: int = 8) -> bool:
    """Decoded record ids count up by one from record to record."""
    count = min(probe, len(data) // stride)
    if count < 2:
        return False
    ids = []
    for k in range(count):
        raw = data[k * stride:k * stride + 4]
        dec = bytes(((b >> 5) | (b << 3)) & 0xFF for b in raw)
        ids.append(struct.unpack('<I', dec)[0])
    return all(ids[k + 1] == ids[k] + 1 for k in range(count - 1))


def detect_stride(data: bytes) -> int:
    """Record stride of an item DAT from its bytes: ``0x1400`` (retail) or ``0xC00`` (legacy).

    File size decides when only one stride divides it (every real table but
    two). When both do — sizes that are multiples of 15360 — the record
    terminator census decides, and failing that the record id sequence. A
    file neither stride divides raises ``ValueError``.
    """
    forced = forced_format()
    if forced:
        return STRIDE_BY_FORMAT[forced]
    n = len(data)
    cands = [s for s in STRIDES if n >= s and n % s == 0]
    if len(cands) == 1:
        return cands[0]
    if not cands:
        raise ValueError(f'not an item DAT: {n} bytes is not a multiple of 0xC00 or 0x1400')
    scored = sorted(((_terminator_ratio(data, s), s) for s in cands), reverse=True)
    if scored[0][0] > 0 and scored[0][0] > scored[1][0]:
        return scored[0][1]
    for s in cands:
        if _ids_sequential(data, s):
            return s
    return STRIDE_LEGACY


def detect_stride_path(path) -> int:
    """:func:`detect_stride` without reading a 30 MB file: size first, then the
    few whole records the terminator census and id probe need."""
    forced = forced_format()
    if forced:
        return STRIDE_BY_FORMAT[forced]
    p = Path(path)
    n = p.stat().st_size
    cands = [s for s in STRIDES if n >= s and n % s == 0]
    if len(cands) == 1:
        return cands[0]
    if not cands:
        raise ValueError(f'not an item DAT: {p} ({n} bytes)')
    lcm = STRIDE_RETAIL * STRIDE_LEGACY // _gcd(STRIDE_RETAIL, STRIDE_LEGACY)
    want = min(n, lcm * 8)
    with open(p, 'rb') as fh:
        head = fh.read(want)
    return detect_stride(head)


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a


def detect_format(data: bytes) -> str:
    return FORMAT_BY_STRIDE[detect_stride(data)]


def detect_format_path(path) -> str:
    return FORMAT_BY_STRIDE[detect_stride_path(path)]


def format_for_stride(stride: int) -> str:
    try:
        return FORMAT_BY_STRIDE[stride]
    except KeyError:
        raise ValueError(f'unknown item record stride {stride:#x}') from None


def forced_format() -> str | None:
    """``XI_ITEM_FORMAT=legacy|retail`` overrides detection (debugging aid only)."""
    v = os.environ.get('XI_ITEM_FORMAT', '').strip().lower()
    return v if v in (FORMAT_LEGACY, FORMAT_RETAIL) else None


def describe(stride: int) -> str:
    fmt = FORMAT_BY_STRIDE.get(stride)
    if fmt == FORMAT_RETAIL:
        return 'retail (0x1400-byte records, Sept 2026 layout)'
    if fmt == FORMAT_LEGACY:
        return 'legacy (0xC00-byte records)'
    return f'unknown ({stride:#x}-byte records)'

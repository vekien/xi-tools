"""Per-record byte rotation used by the ``comm`` (ability) and ``mgc_`` (magic)
tables of ``ROM/118/114.DAT`` (the boot-loaded ``menu`` numeric-table DAT).

Every fixed-size record — ``0x30`` bytes for ``comm``, ``0x64`` for ``mgc_`` — is
obfuscated **independently**. Three bytes are stored plain (``+0x02``, ``+0x0B``,
``+0x0C``); the population counts of those three pick a rotate amount:

    amount = ROTATE_AMOUNTS[ abs(pop(b[2]) + pop(b[0xC]) - pop(b[0xB])) % 5 ]

and every *other* byte of the record is rotated LEFT by that amount to decode
(RIGHT to encode). Because the amount depends only on the plain bytes, a record
can be re-encoded after editing without touching its neighbours.

Decoded ``comm`` record layout (what the client reads):

    +0x00 u16  id      — sequential; == record index for all 2816 rows
    +0x02 u8   type    — see COMM_TYPE_NAMES (3 = weapon skill)
    +0x03 i8   icon id, +0x04 i16 icon2, +0x06 i16 charges, +0x0A u16 target bits,
    +0x0C i16  tp cost, +0x0F i8 level, +0x10 range, +0x11 radius, +0x12 aoe type,
    +0x13 u16  valid targets, +0x15 i8 tp modifier ... (Shining Fantasia comm2json)

None of the 48 bytes carries the weapon-skill *animation* number (checked as
u8/u16/u32 at every offset against the 211 known animations — best hit 2/211),
so the name table, this table and the animation banks are three separate ID
spaces joined only by the server. See docs/anim/weapon-skills.md and
docs/dats/ROM_118_114.md.

Names: ``ROM/181/72.DAT`` is the matching ``d_msg`` table (fixed 80-byte
blocks, no XOR) — block *i* names ``comm`` record *i* (block 1 "Combo",
32 "Fast Blade", 255 "Dimensional Death"; unnamed rows hold ".").

Ported from Shining Fantasia's ``mgc-decode.ts`` (research/external).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

COMM_STRIDE = 0x30
MGC_STRIDE = 0x64

ROTATE_AMOUNTS = (1, 7, 2, 6, 3)
PLAIN_OFFSETS = (0x02, 0x0B, 0x0C)

# comm record +0x02. Same vocabulary Shining Fantasia and the server use.
COMM_TYPE_NAMES = {
    1: 'Ability', 2: 'Pet ability', 3: 'Weapon skill', 4: 'Job trait',
    6: 'Blood Pact: Rage', 8: "Corsair's Roll", 9: 'Quick Draw',
    10: 'Blood Pact: Ward', 11: 'Samba', 12: 'Waltz', 13: 'Step', 14: 'Flourish',
    15: 'Stratagem', 16: 'Jig', 17: 'Flourish II', 18: 'Ready', 19: 'Flourish III',
    20: 'Monstrosity', 21: 'Rune Enchantment', 22: 'Ward', 23: 'Effusion',
}
COMM_TYPE_WEAPON_SKILL = 3

ABILITY_NAME_DAT = 'ROM/181/72.DAT'   # EN; JP is ROM/181/68.DAT (xi.mv.xi_database)


def _popcount(v: int) -> int:
    return bin(v & 0xFF).count('1')


def rotate_amount(rec: bytes) -> int:
    """Rotate amount for one record, from its three plain bytes."""
    x, y, z = rec[0x02], rec[0x0B], rec[0x0C]
    return ROTATE_AMOUNTS[abs(_popcount(x) + _popcount(z) - _popcount(y)) % 5]


def _rol(b: int, r: int) -> int:
    return ((b << r) | (b >> (8 - r))) & 0xFF


def _ror(b: int, r: int) -> int:
    return ((b >> r) | (b << (8 - r))) & 0xFF


def decode_record(rec: bytes) -> bytes:
    """On-disk record -> the bytes the client reads."""
    if len(rec) <= max(PLAIN_OFFSETS):
        raise ValueError(f'record too short to decode ({len(rec)} bytes)')
    r = rotate_amount(rec)
    return bytes(b if i in PLAIN_OFFSETS else _rol(b, r) for i, b in enumerate(rec))


def encode_record(rec: bytes) -> bytes:
    """Inverse of :func:`decode_record` (the plain bytes are untouched, so the
    amount is recoverable from the encoded form too)."""
    if len(rec) <= max(PLAIN_OFFSETS):
        raise ValueError(f'record too short to encode ({len(rec)} bytes)')
    r = rotate_amount(rec)
    return bytes(b if i in PLAIN_OFFSETS else _ror(b, r) for i, b in enumerate(rec))


def decode_records(payload: bytes, stride: int) -> List[bytes]:
    """Split ``payload`` into ``stride``-byte records and decode each. A trailing
    partial record is dropped (the client iterates whole records only)."""
    if stride <= max(PLAIN_OFFSETS):
        raise ValueError(f'stride {stride} too small')
    n = len(payload) // stride
    return [decode_record(payload[i * stride:(i + 1) * stride]) for i in range(n)]


def encode_records(records: List[bytes]) -> bytes:
    return b''.join(encode_record(r) for r in records)


def load_ability_names(ffxi_dir: Optional[Path] = None) -> List[str]:
    """``[name]`` indexed by ``comm`` record id, from ROM/181/72.DAT. Empty list
    when the install (or the table) is missing, so callers can degrade to ids."""
    from xi.xi_config import FFXI_DIR, read_path_for
    from xi.common import xi_dmsg
    root = Path(ffxi_dir) if ffxi_dir else Path(FFXI_DIR)
    p = root / ABILITY_NAME_DAT
    if not p.exists():
        return []
    try:
        table = xi_dmsg.parse(read_path_for(p).read_bytes())
        return [xi_dmsg.get_text(b, 0) for b in table.blocks]
    except Exception:  # noqa: BLE001 — names are a convenience
        return []

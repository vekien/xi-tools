"""In-place edits to the particle sections an ability recipe carries: a ``0x05``
generator's header and op streams (the recipe's ``generators``) and a ``0x19`` curve's
keys (its ``curves``). Pure bytes in, bytes out: ``xi ability compose`` applies them to a
lane's copy of a section before the section goes into the output.

A generator edit names its bytes by where they sit in the format, never by file offset,
so it holds on every race's copy of a weapon skill::

    {"sec": 2, "op": "0x16", "nth": 0, "at": 4, "type": "u8", "value": [198, 128, 51, 38]}

``sec`` 0 is the generator header and ``at`` counts from the section start (the spawn
interval is at 0x76). ``sec`` 1–4 are the four op streams (generator updaters,
initializers, updaters, expiration handlers) whose offsets sit at section +0x80: the op
is found by walking the stream, ``nth`` picks among repeats of one opcode, and ``at``
counts from the op's config dword. Every write is little-endian and the size of what it
replaces; values are absolute, so applying an edit twice changes nothing.

Never writable: the 16-byte section header, the stream-offset table (+0x80..0x8F) and an
op's config dword. They are the structure the client walks, and a same-size edit has no
business in them. Layouts: docs/fx/effects.md, docs/fx/effect_system.md §4–5.
"""

from __future__ import annotations

import math
import re
import struct
from typing import Dict, List, Optional, Sequence, Tuple

from xi.fx.xi_opcodes import _OFF_SECTION_TABLE

EDIT_KEYS = {"sec", "op", "nth", "at", "type", "value", "mask"}
# type -> (bytes, lowest, highest); f32 and datid are four bytes with no integer range.
_INTS = {"u8": (1, 0, 0xFF), "u16": (2, 0, 0xFFFF), "u32": (4, 0, 0xFFFFFFFF),
         "i16": (2, -0x8000, 0x7FFF), "i32": (4, -0x80000000, 0x7FFFFFFF)}
TYPES = tuple(_INTS) + ("f32", "datid")
_F32_MAX = 3.4028234663852886e38

_HEADER_FROM = 0x10                  # the section header ends here
_HEADER_TO = _OFF_SECTION_TABLE      # the stream-offset table starts here
_MAX_OP_BYTES = 0x1F * 4             # an op's size field is five bits of dwords
_CURVE_KEYS_FROM = 0x10
_CURVE_END = 1.0                     # a key at time 1 is the curve's last


class EditError(ValueError):
    """An edit that does not fit the section it is applied to."""


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v) -> bool:
    """A JSON number that is not NaN or infinite. An int is always finite (isfinite
    would overflow on a huge one); the callers' f32 range check rejects it."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    return isinstance(v, int) or math.isfinite(v)


def is_section_id(v) -> bool:
    """1–4 printable ASCII characters, not all padding: a section name as a recipe
    writes it."""
    return isinstance(v, str) and re.fullmatch(r"[\x20-\x7E]{1,4}", v) is not None and bool(v.strip())


def _opcode(v) -> Optional[int]:
    """An opcode given as an integer or a hex string (the rule of an event's ``op``)."""
    if _is_int(v):
        return v if 0 <= v <= 255 else None
    if isinstance(v, str) and re.fullmatch(r"(0x)?[0-9A-Fa-f]{1,2}", v):
        return int(v, 16)
    return None


def _size(kind: str) -> int:
    return _INTS[kind][0] if kind in _INTS else 4


def _values(edit: dict) -> list:
    v = edit["value"]
    return list(v) if isinstance(v, list) else [v]


# ── Static checks (validate_recipe) ──────────────────────────────────────────────

def check_edit(e) -> List[str]:
    """What is wrong with one edit on its own, before any DAT is read (empty when it
    conforms to schema/ability_recipe.json ``$defs.edit``)."""
    if not isinstance(e, dict):
        return ["must be an object"]
    errs = [f"unknown key {k!r}" for k in e if k not in EDIT_KEYS]
    sec = e.get("sec")
    sec_ok = _is_int(sec) and 0 <= sec <= 4
    if not sec_ok:
        errs.append("sec must be 0 (the header) or 1–4 (an op stream)")
    elif sec == 0:
        if "op" in e or e.get("nth", 0) != 0:
            errs.append("op and nth do not apply to sec 0 (the header)")
    elif _opcode(e.get("op")) is None:
        errs.append("op must be an opcode 0–255 (or hex string)")
    if "nth" in e and not (_is_int(e["nth"]) and e["nth"] >= 0):
        errs.append("nth must be a non-negative integer")
    at = e.get("at")
    at_ok = _is_int(at) and at >= 0
    if not at_ok:
        errs.append("at must be a non-negative byte offset")
    kind = e.get("type")
    kind_ok = isinstance(kind, str) and kind in TYPES
    if not kind_ok:
        errs.append(f"type must be one of {', '.join(TYPES)}")
    value_ok = "value" in e and not (isinstance(e["value"], list) and not e["value"])
    if not value_ok:
        errs.append("value is required (one value, or a non-empty list of consecutive ones)")
    elif kind_ok:
        for v in _values(e):
            if kind in _INTS:
                _, lo, hi = _INTS[kind]
                if not (_is_int(v) and lo <= v <= hi):
                    errs.append(f"value {v!r} is not a {kind} ({lo}..{hi})")
                    value_ok = False
            elif kind == "f32":
                if not (_is_number(v) and abs(v) <= _F32_MAX):
                    errs.append(f"value {v!r} is not a finite f32")
                    value_ok = False
            elif not is_section_id(v):
                errs.append(f"value {v!r} is not a 1–4 character section id")
                value_ok = False
    if "mask" in e:
        if not kind_ok or kind not in _INTS:
            errs.append("mask applies to integer types only")
        elif not (_is_int(e["mask"]) and 0 <= e["mask"] <= (1 << (8 * _size(kind))) - 1):
            errs.append(f"mask must fit a {kind}")
    if sec_ok and at_ok and kind_ok and value_ok:
        end = at + _size(kind) * len(_values(e))
        if sec == 0 and not (_HEADER_FROM <= at and end <= _HEADER_TO):
            errs.append(f"sec 0 writes stay inside the generator header, "
                        f"0x{_HEADER_FROM:02X} <= at and at + size <= 0x{_HEADER_TO:02X}")
        elif sec and at < 4:
            errs.append("at must be 4 or more: the op's config dword is not writable")
        elif sec and end > _MAX_OP_BYTES:
            errs.append(f"at + size runs past the longest op ({_MAX_OP_BYTES} bytes)")
    return errs


def check_keys(keys) -> List[str]:
    """What is wrong with a curve's ``keys`` on their own (empty when they conform)."""
    if not isinstance(keys, list) or not keys:
        return ["keys must be a non-empty list of [time, value] pairs"]
    errs = []
    last = len(keys) - 1
    for k, key in enumerate(keys):
        if not (isinstance(key, list) and len(key) == 2 and all(_is_number(v) and abs(v) <= _F32_MAX for v in key)):
            errs.append(f"keys[{k}] must be [time, value], two finite numbers")
        elif not 0 <= key[0] <= 1:
            errs.append(f"keys[{k}]: time must be 0..1 (a fraction of the particle's life)")
        # apply_curve refuses this whatever the DAT: an earlier key at time 1 (as an
        # f32, so 0.99999998 too) would end the curve. The last key takes the source's time.
        elif k != last and struct.unpack("<f", struct.pack("<f", float(key[0])))[0] == _CURVE_END:
            errs.append(f"keys[{k}] has time 1, which ends the curve; only the last key may")
    return errs


# ── Generators (0x05) ────────────────────────────────────────────────────────────

def walk_stream(body: bytes, sec: int) -> List[Tuple[int, int, int]]:
    """``(opcode, offset, bytes)`` of every op in stream ``sec`` (1–4) of a generator
    section; the offset is the config dword's, from the section start. An absent
    stream (offset 0) has none."""
    if len(body) < _OFF_SECTION_TABLE + 16:
        raise EditError("the section is too short to be a generator (no stream table at +0x80)")
    pos = struct.unpack_from("<I", body, _OFF_SECTION_TABLE + (sec - 1) * 4)[0]
    ops = []
    while 0 < pos and pos + 4 <= len(body):
        config = struct.unpack_from("<I", body, pos)[0]
        op, size = config & 0xFF, ((config >> 8) & 0x1F) * 4
        if op == 0 or size == 0:
            break
        if pos + size > len(body):
            raise EditError(f"sec {sec} op 0x{op:02X} at +0x{pos:X} runs past the section")
        ops.append((op, pos, size))
        pos += size
    return ops


def resolve(body: bytes, edit: dict) -> Tuple[int, int]:
    """The byte range ``[start, end)`` of the section an edit writes, with every bound
    checked against this section's own layout."""
    sec, at = edit["sec"], edit["at"]
    nbytes = _size(edit["type"]) * len(_values(edit))
    if sec == 0:
        if not (_HEADER_FROM <= at and at + nbytes <= _HEADER_TO):
            raise EditError(f"at 0x{at:X} + {nbytes} bytes leaves the generator header "
                            f"(0x{_HEADER_FROM:02X}..0x{_HEADER_TO - 1:02X})")
        if at + nbytes > len(body):
            raise EditError(f"at 0x{at:X} + {nbytes} bytes runs past the section ({len(body)} bytes)")
        return at, at + nbytes
    op, nth = _opcode(edit["op"]), edit.get("nth", 0)
    found = [(pos, size) for code, pos, size in walk_stream(body, sec) if code == op]
    if nth >= len(found):
        raise EditError(f"sec {sec} has {len(found) or 'no'} op 0x{op:02X}"
                        + (f", so no nth {nth}" if found else ""))
    pos, size = found[nth]
    if at < 4:
        raise EditError("at must be 4 or more: the op's config dword is not writable")
    if at + nbytes > size:
        raise EditError(f"at {at} + {nbytes} bytes runs past sec {sec} op 0x{op:02X} ({size} bytes)")
    return pos + at, pos + at + nbytes


def _id_bytes(value: str, old: bytes, ids: Optional[Dict[str, bytes]]) -> bytes:
    """The four bytes a ``datid`` writes. ``ids`` (clean name -> the name as the DAT
    spells it) is what the id may point at; without it the id is padded the way the
    bytes it replaces are, since a DAT pads short names with spaces or with zeros."""
    name = value.rstrip(" ")
    if ids is not None:
        if name not in ids:
            raise EditError(f"no texture, mesh, curve or sound section named {name!r} in the source DAT")
        return ids[name]
    pad = b" " if old.endswith(b" ") else b"\0"
    return name.encode("ascii").ljust(4, pad)


def apply_edits(body: bytes, edits: Sequence[dict], where: str = "edits",
                ids: Optional[Dict[str, bytes]] = None) -> bytes:
    """``body`` (a whole 0x05 section) with ``edits`` written in order. The result is the
    same length; an edit that does not fit raises EditError naming ``where[j]``."""
    out = bytearray(body)
    for j, e in enumerate(edits):
        try:
            start, _ = resolve(body, e)
            kind = e["type"]
            for n, v in enumerate(_values(e)):
                a = start + n * _size(kind)
                if kind == "datid":
                    out[a:a + 4] = _id_bytes(v, bytes(out[a:a + 4]), ids)
                elif kind == "f32":
                    struct.pack_into("<f", out, a, float(v))
                else:
                    size = _size(kind)
                    full = (1 << (8 * size)) - 1
                    mask = e.get("mask", full)
                    old = int.from_bytes(out[a:a + size], "little")
                    out[a:a + size] = ((old & ~mask) | (v & full & mask)).to_bytes(size, "little")
        except EditError as err:
            raise EditError(f"{where}[{j}]: {err}") from None
    return bytes(out)


# ── Curves (0x19) ────────────────────────────────────────────────────────────────

def curve_keys(body: bytes) -> List[Tuple[float, float]]:
    """The ``(time, value)`` keys of a 0x19 section: f32 pairs from +0x10 up to and
    including the key at time 1 (or the section's end). Bytes after that are padding."""
    keys = []
    pos = _CURVE_KEYS_FROM
    while pos + 8 <= len(body):
        t, v = struct.unpack_from("<2f", body, pos)
        keys.append((t, v))
        pos += 8
        if t == _CURVE_END:
            break
    return keys


def apply_curve(body: bytes, keys: Sequence[Sequence[float]], where: str = "curve") -> bytes:
    """``body`` (a whole 0x19 section) with its keys replaced. The key count is the
    source's, since the section cannot grow, and the last key keeps the source's time:
    that key ends the curve, and a reader stops at it."""
    have = curve_keys(body)
    if len(keys) != len(have):
        raise EditError(f"{where}: {len(keys)} keys for a curve of {len(have)} (the key count cannot change)")
    out = bytearray(body)
    last = len(have) - 1
    for k, (t, v) in enumerate(keys):
        t = have[last][0] if k == last else struct.unpack("<f", struct.pack("<f", float(t)))[0]
        if k != last and t == _CURVE_END:
            raise EditError(f"{where}: keys[{k}] has time 1, which ends the curve; only the last key may")
        struct.pack_into("<2f", out, _CURVE_KEYS_FROM + k * 8, t, float(v))
    return bytes(out)

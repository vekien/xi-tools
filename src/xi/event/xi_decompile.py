"""Retail event bytecode -> xi.cutscene.v1 JSON (the common, DAT-independent form).

    xi event decompile <zone> <actor> --event N [-o out.json] [--check]

Walks every block reachable from the event (jumps, branches, calls, bit-branches), turns
each opcode into the authoring step the compiler understands, renders the dialog strings
back into the token syntax, and emits subroutines as ``sub`` steps. Opcodes without a
step model become ``raw`` steps whose 2-byte selectors are carried as ``sels`` so the
compiler re-resolves them against its own references[] (lossless for fixed-layout
opcodes). ``--check`` compiles the result in bare mode against the pristine DATs and
compares the two events opcode by opcode with operands resolved (constants, registers,
message TEXT, jump targets by block order) and reports the coverage.

Known gaps (2026-09-04): strings that end WITHOUT the 7F 31
prompt get one from the compiler; 0x73 effects and 0x2C actions on other entities stay
raw; the retail else-jump chains are kept as ``if``/``goto`` pairs rather than folded
into ``branch``.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import xi_event as core
from . import xi_explain as ex
from . import xi_typed

PLAYER, SELF = 0x7FFFFFF0, 0x7FFFFFF8
IF_NAMES = {0: "ne", 1: "eq", 2: "le", 3: "ge", 4: "lt", 5: "gt"}
NAME_KINDS = {0x25: "item", 0x36: "keyitem", 0x24: "rowitem"}


# ---------------------------------------------------------------------------
# strings -> authoring tokens
# ---------------------------------------------------------------------------
def _unpad(b: bytes) -> bytes:
    """Content bytes of a dialog blob: everything up to the LAST terminator, when what follows
    it is only padding (`00 07`, `00 00`). Interior NULs are content (menu blobs carry them),
    and a `07` newline right before the terminator is content too."""
    last = b.rfind(bytes([0]))
    if last >= 0 and all(x in (0x00, 0x07) for x in b[last + 1:]):
        return b[:last]
    return b


def _v7(b: bytes, i: int) -> int:
    """The value behind an `82` marker: three bytes, seven bits each, low first (zone 236 is
    `ec 81 80`; parameter indexes fit in the first byte)."""
    return (b[i] & 0x7F) | ((b[i + 1] & 0x7F) << 7) | ((b[i + 2] & 0x7F) << 14)


def render_authoring(raw: bytes) -> str:
    """Inverse of xi_dialog.encode_event_string for the tokens the compiler knows.
    Strips the trailing prompt (7F 31) and the NUL/07 terminator; the compiler adds
    the prompt back. Unknown control bytes become {raw:xx}."""
    b = raw
    b = _unpad(b)
    noprompt = False
    if b.endswith(b"\x7f\x31"):
        b = b[:-2]
    else:
        noprompt = True             # retail line without the press-enter prompt: keep it that way
    out = []
    i = 0
    n = len(b)
    while i < n:
        c = b[i]
        if c == 0x07:
            out.append("\\n"); i += 1; continue
        if c == 0x0A and i + 1 < n:
            out.append(f"{{{b[i + 1]}}}"); i += 2; continue
        if c == 0x0B:
            out.append("{options}"); i += 1; continue
        if c == 0x0C and i + 1 < n:
            out.append(f"{{index:{b[i + 1]}}}"); i += 2; continue
        if c == 0x08:
            out.append("{player}"); i += 1; continue
        if c == 0x19 and i + 1 < n:
            out.append(f"{{member:{b[i + 1]}}}"); i += 2; continue
        if c == 0x09:
            out.append("{npc}"); i += 1; continue
        if c == 0x7F and i + 1 < n:
            s = b[i + 1]
            if s == 0x31:
                out.append("\\v"); i += 2; continue
            if s == 0x34 and i + 2 < n:
                out.append(f"{{auto:{b[i + 2]}}}"); i += 3; continue
            if s == 0x92 and i + 2 < n:
                out.append(f"{{plural:{b[i + 2]}}}"); i += 3; continue
            if s == 0x85:
                out.append("{gender}"); i += 2; continue
            if b[i:i + 6] == b"\x7f\x80\x01\x01\x05\x23" and i + 9 < n and b[i + 6] == 0x82:
                out.append(f"{{rowname:{_v7(b, i + 7)}}}"); i += 10; continue
            out.append(f"{{raw:{b[i:i + 2].hex()}}}"); i += 2; continue
        if c == 0x01 and i + 6 < n and b[i + 1] == 0x05 and b[i + 3] == 0x82:
            kind, idx = b[i + 2], _v7(b, i + 4)
            name = NAME_KINDS.get(kind)
            out.append(f"{{{name}:{idx}}}" if name else f"{{name:0x{kind:02x}:{idx}}}")
            i += 7; continue
        if c == 0x01 and i + 10 < n and b[i + 1] == 0x09 and b[i + 2] == 0x29 and b[i + 3] == 0x82 and b[i + 7] == 0x82:
            out.append(f"{{qtyitem:{_v7(b, i + 4)}:{_v7(b, i + 8)}}}"); i += 11; continue
        if c < 0x20:
            out.append(f"{{raw:{c:02x}}}"); i += 1; continue
        if 0x81 <= c <= 0x9F or 0xE0 <= c <= 0xFC:
            if i + 1 < n:
                try:
                    out.append(b[i:i + 2].decode("cp932")); i += 2; continue
                except UnicodeDecodeError:
                    pass
            out.append(f"{{raw:{c:02x}}}"); i += 1; continue
        if c >= 0x80:
            try:
                out.append(bytes([c]).decode("cp932")); i += 1; continue
            except UnicodeDecodeError:
                out.append(f"{{raw:{c:02x}}}"); i += 1; continue
        if c == 0x7B:
            out.append("{raw:7b}"); i += 1; continue
        if c == 0x7D:
            out.append("{raw:7d}"); i += 1; continue
        if c == 0x5C:
            out.append("\\\\"); i += 1; continue
        out.append(chr(c)); i += 1
    if noprompt:
        out.append("{noprompt}")               # an empty retail string stays empty too (Qufim-side 267/26)
    return "".join(out)


import re
import re as _re

_TOKEN_RE = _re.compile(r"\{([^{}]*)\}(\[[^\]]*\])?")


def segments_from_text(text: str) -> list[dict]:
    """The machine form of a line: a list of segments an executor renders without knowing the
    token grammar. Kinds: text; newline; prompt (press enter); number {param}; select
    {param, choices} (alternative by value); plural {param, choices} (choices[0] when the
    parameter is 1, else choices[1]); item / rowitem / keyitem {param} (name by id in the
    parameter); qtyitem {countParam, itemParam}; player; npc; auto {seconds}; options (menu rows
    start); raw {hex}. Parameters are Z[2 + n]: the server's eight values or `store` steps."""
    out: list[dict] = []
    pos = 0
    buf = ""

    def flush():
        nonlocal buf
        if buf:
            out.append({"kind": "text", "text": buf}); buf = ""

    i = 0
    n = len(text)
    while i < n:
        if text.startswith("\\n", i):
            flush(); out.append({"kind": "newline"}); i += 2; continue
        if text.startswith("\\v", i):
            flush(); out.append({"kind": "prompt"}); i += 2; continue
        if text.startswith("\\\\", i):
            buf += "\\"; i += 2; continue
        if text[i] == "{":
            m = _TOKEN_RE.match(text, i)
            if m:
                tok, bracket = m.group(1), m.group(2)
                choices = bracket[1:-1].split("/") if bracket else None
                seg = None
                if tok.isdigit():
                    seg = {"kind": "number", "param": int(tok)}
                elif tok.startswith("index:"):
                    seg = {"kind": "select", "param": int(tok[6:]), "choices": choices or []}
                elif tok.startswith("plural:"):
                    seg = {"kind": "plural", "param": int(tok[7:]), "choices": choices or []}
                elif tok.startswith("item:") or tok.startswith("rowitem:") or tok.startswith("rowname:") or tok.startswith("keyitem:"):
                    kind = tok.split(":")[0]
                    seg = {"kind": "keyitem" if kind == "keyitem" else "item", "param": int(tok.split(":")[1]), "form": kind}
                elif tok.startswith("name:"):
                    parts = tok.split(":")
                    seg = {"kind": "item", "param": int(parts[2]), "form": "name", "nameKind": int(parts[1], 0)}
                elif tok.startswith("qtyitem:"):
                    parts = tok.split(":")
                    seg = {"kind": "qtyitem", "countParam": int(parts[1]), "itemParam": int(parts[2])}
                elif tok == "gender":
                    seg = {"kind": "gender", "choices": choices or []}
                elif tok.startswith("member:"):
                    seg = {"kind": "member", "index": int(tok[7:])}
                elif tok == "player":
                    seg = {"kind": "player"}
                elif tok == "npc":
                    seg = {"kind": "npc"}
                elif tok == "options":
                    seg = {"kind": "options"}
                elif tok.startswith("auto:"):
                    seg = {"kind": "auto", "seconds": int(tok[5:])}
                elif tok.startswith("raw:"):
                    seg = {"kind": "raw", "hex": tok[4:]}
                elif tok == "noprompt":
                    seg = {"kind": "noprompt"}
                if seg is not None:
                    flush(); out.append(seg); i = m.end(); continue
        buf += text[i]; i += 1
    flush()
    return out


def split_menu(text: str) -> tuple[str, list[str]]:
    """Question and option rows of a rendered menu string. Rendered text carries line breaks as
    the two characters backslash + n; '{options}' separates the question from the rows, and a
    string without the marker uses its first line as the question."""
    nl = "\\n"
    if "{options}" not in text:
        parts = text.split(nl)
        if len(parts) >= 3:
            return parts[0], parts[1:]
        return text, []
    q, rows = text.split("{options}", 1)
    if q.endswith(nl):
        q = q[:-2]
    opts = rows.split(nl) if rows != "" else []    # empty rows are rows (a debug menu ends with one)
    return q, opts


# ---------------------------------------------------------------------------
# selector layouts of the opcodes that stay `raw` (offsets of their 2-byte selectors)
# ---------------------------------------------------------------------------
# From F:\XiEvents\OpCodes (getworkofs_ offsets) and the PS2 source. An entry means "this
# opcode's layout is known": the selectors listed are re-resolved on recompile and every
# other byte (entity ids, tags, sub-opcodes, u8 flags) is literal, so the raw step is
# lossless. Keys: opcode, or (opcode, size) when the size selects the form.
SEL_LAYOUTS: dict = {
    0x27: [], 0x94: [], 0x99: [], 0x2F: [], 0x2A: [], 0x76: [], 0x30: [], 0x9A: [], 0x78: [], 0x8A: [],
    0x22: [], 0x20: [], 0x7B: [], 0xAB: [], 0x75: [], 0x33: [],
    (0x5C, 4): [2], (0x5C, 6): [2, 4],
    0x6E: [5], 0x32: [1], (0x46, 2): [], (0x46, 4): [],
    (0xB6, 2): [], (0xB6, 4): [2], (0xB6, 6): [2, 4],
    0x6C: [5, 7], 0x08: [1, 3], 0x0D: [1, 3], 0x11: [1, 3], 0x13: [1, 3], 0x15: [1, 3],
    (0x59, 4): [2], (0x59, 6): [], (0x59, 7): [], (0x59, 8): [6],          # size 8: sub, entity at +2, selector at +6
    0x62: [1, 15], 0x9F: [1, 15], 0xBB: [1, 15], 0xCD: [1, 15],
    0x3B: [5, 7, 9], 0x3A: [5], 0x38: [1], 0x5D: [1, 3], 0x34: [1], 0x35: [1],
    0x4B: [5], 0x6B: [], 0x77: [1, 3],
    (0xB4, 6): [4], (0xB4, 2): [], (0xB4, 3): [], (0xB4, 4): [],
    (0x9D, 8): [4, 6], (0x9D, 6): [2, 4],
    0x7C: [], 0x81: [], 0x7F: [], 0x4C: [], 0x4D: [], 0x97: [1, 3], 0x8D: [1, 3], 0x36: [1, 3, 5],
    (0xAD, 12): [2], (0xD8, 6): [], (0xD8, 8): [6], (0xD8, 12): [6, 8, 10],
    (0x7A, 2): [], (0x7A, 6): [], (0x7A, 7): [], (0x7A, 8): [], 0xC4: [2],
    (0x9D, 10): [6, 8], (0x9D, 9): [], (0x9D, 23): [],
    0x5E: [], (0x5A, 2): [], (0x5A, 8): [2, 4, 6], 0x39: [1], 0xB5: [], (0x47, 2): [], (0x47, 10): [2, 4, 6, 8],
    0xA8: [2, 4], (0xD4, 8): [2, 4, 6], (0xD4, 6): [2, 4], (0xD4, 12): [2, 4, 6, 8, 10], (0xD4, 2): [],
    0x95: [1], (0x5F, 2): [], (0x5F, 6): [], (0x5F, 16): [], 0x8B: [1, 3, 5, 7], 0x0F: [1, 3], 0x7D: [1], 0xA5: [], 0xC0: [1],
    (0xB4, 20): [],
    0xD0: [1, 15], 0x83: [1], 0xAA: [1], 0x89: [1], 0xB1: [], 0xD3: [], (0x5F, 14): [], (0x5F, 18): [],
    (0xB6, 14): [2], (0xB6, 16): [2], (0xB6, 20): [2],
    0x86: [], 0x61: [], 0xA4: [], 0x96: [], 0xC9: [], 0x84: [], 0x26: [], 0x50: [], 0xCE: [],
    (0x72, 4): [2], (0x72, 6): [2, 4], (0x72, 10): [2],
    (0xAE, "sub", 1): [2, 4], (0xAE, "sub", 3): [2, 4], (0xAE, 6): [], (0xAE, 8): [6], (0xAE, 10): [],
    0xB8: [1, 3, 5, 7, 9], (0xB4, 12): [],
    0xC5: [1, 15],           # same layout as 45 start_task: sel, entA, entB, tag, sel (Ru'Lude 0x010F3129 'hit0')
    0xBC: [], (0x31, 2): [], (0x31, 10): [2, 4, 6, 8], (0xAC, 4): [2], (0xAC, 6): [], (0xAC, 8): [6],
    0x17: [3, 5], 0x16: [3, 5], 0x69: [2], 0x28: [], 0x82: [], 0x2D: [], 0x6A: [1, 3, 5], 0xD9: [], 0xA7: [], 0xA0: [], 0x90: [],
    (0x7E, 6): [], (0x7E, 8): [6], (0x7E, 16): [6, 8, 10, 12, 14], (0x7E, 18): [6, 8, 10, 12, 14, 16], (0xCC, 4): [2],
    0x9C: [], 0xB0: [10], 0x63: [1], 0xA2: [], 0x98: [], 0x43: [],
    # ten-zone city sweep (2026-09-05): 04 / 0A carry no references, 19 is two registers,
    # 87 / 88 / 8E / 9E are sub-only, A6 sub 2 carries one register, B7 (8-byte subs) one
    # selector before its entity, B9 three immediate references
    0x04: [], 0x0A: [], 0x19: [1, 3], 0x87: [], 0x88: [], 0x8E: [], 0x9E: [],
    (0xA6, 2): [], (0xA6, 4): [2], (0xB7, 8): [2], (0xB9, 8): [2, 4, 6],
    0xA9: [1],                                     # fix the game time to a value (XiEvents 0x00A9)
    0xC7: [1],                                     # 15 bytes: selector, entity, entity, tag (a 0x52-style scheduler call)
    0x60: [],                                      # sub byte + 4-char tag ("sta1"), no selectors
    0x12: [1],                                     # 3 bytes: one selector
    0x09: [1, 3], 0x8F: [], 0x91: [1],             # night sweep: two selectors / bare / one selector
    0xA3: [1], 0xCF: [1], 0xBD: [1],               # 15 bytes: selector, entity, entity, tag
    (0xB3, 2): [], (0xB3, 4): [2], (0xB3, 14): [2, 4, 6, 8, 10, 12], (0xB3, 18): [2, 4, 6, 8, 10, 12, 14, 16],
    (0xBF, 8): [2, 4, 6], (0xBF, 10): [2, 4, 6, 8], 0x18: [1, 3, 5], 0x2B: [5], 0xC1: [], 0xD6: [1],   # chocobo racing (zone 70)
    (0xC2, 2): [], (0xC2, 4): [], (0xC2, 6): [2], (0x8C, 2): [], (0x8C, 8): [2, 4, 6], (0x8C, 10): [2, 4, 6], (0x8C, 12): [2, 4, 6], (0x8C, 14): [2, 4, 6],
    0xC6: [], 0x68: [], 0xC8: [1, 3, 5], 0x67: [1, 3], 0xD5: [], 0x65: [], 0xC3: [1, 5], 0x49: [5], 0x56: [], 0x44: [1], 0x4F: [1],
    0x74: [], 0x64: [3, 5, 7, 9], 0x54: [], 0x57: [1], 0x51: [], 0x58: [], 0x6D: [], (0xCC, 10): [2, 4, 6, 8], (0xCC, 6): [2, 4],
}


def sel_offsets(op: int, sz: int, sub: int = -1):
    """Known selector offsets for a raw opcode, or None when the layout is unknown. Keys:
    (op, "sub", n) when the sub-opcode decides the layout, then (op, size), then op."""
    if (op, "sub", sub) in SEL_LAYOUTS:
        return SEL_LAYOUTS[(op, "sub", sub)]
    if (op, sz) in SEL_LAYOUTS:
        return SEL_LAYOUTS[(op, sz)]
    return SEL_LAYOUTS.get(op)


# ---------------------------------------------------------------------------
# operands
# ---------------------------------------------------------------------------
@dataclass
class Ctx:
    scene: bytes
    refs: list[int]
    blobs: Optional[list[bytes]]
    lines: dict = field(default_factory=dict)      # msg id -> {"id":..., "text":...}
    full_text: dict = field(default_factory=dict)  # msg id -> rendered text before any menu split
    raw_ops: int = 0
    total_ops: int = 0
    notes: list[str] = field(default_factory=list)
    tables: dict = field(default_factory=dict)     # table offset -> {"label":..., "entries": [...]}

    def table(self, off: int) -> str:
        """Register the 0x9D table at ``off`` (selectors until the first zero) and return its label."""
        if off not in self.tables:
            entries = []
            pos = off
            while pos + 2 <= len(self.scene):
                sel = struct.unpack_from("<H", self.scene, pos)[0]
                if sel == 0:
                    break
                plausible = ((sel & 0x8000) and (sel & 0x7FFF) < len(self.refs)) or sel < 80                     or 0x1000 <= sel < 0x1100 or 0x1700 <= sel < 0x1800
                if not plausible:
                    break                     # ran into code: retail tables are not always zero-terminated
                v = self.val(sel)
                if v == 0 and self.is_const(sel):
                    break
                entries.append(v)
                pos += 2
            self.tables[off] = {"label": f"tbl_{off:04x}", "entries": entries}
        return self.tables[off]["label"]

    def u16(self, pos: int) -> int:
        return struct.unpack_from("<H", self.scene, pos)[0]

    def u32(self, pos: int) -> int:
        return struct.unpack_from("<I", self.scene, pos)[0]

    def val(self, sel: int):
        """selector -> constant int or register spec"""
        if sel & 0x8000:
            if (sel & 0x7FFF) < len(self.refs):
                return int(self.refs[sel & 0x7FFF])
            return {"sel": sel}                  # reference past the table (retail garbage): keep the selector
        return self.reg(sel)

    @staticmethod
    def reg(sel: int):
        if sel == 0x1000:
            return "menu_result"
        if sel == 0x1001:
            return "result"
        if 0x1002 <= sel < 0x1002 + 36:
            return {"param": sel - 0x1002}
        if 0x1000 <= sel < 0x1100:
            return {"work": sel - 0x1000}
        if 0x1700 <= sel < 0x1800:
            return {"work1700": sel - 0x1700}
        if 0x1000 <= sel < 0x1700:
            return {"work": sel - 0x1000}
        if sel < 0x1000:
            return {"local": sel}
        if sel in xi_typed.STATE_NAMES:
            return {"state": xi_typed.STATE_NAMES[sel]}   # entity / local-player runtime state
        return {"sel": sel}           # unnamed selector bank: carried verbatim

    def is_const(self, sel: int) -> bool:
        return bool(sel & 0x8000)

    def ent(self, v: int):
        if v == PLAYER:
            return "player"
        if v == SELF:
            return "self"
        return f"0x{v:08X}"

    def line(self, msg_id: int, prefix: str = "m") -> str:
        key = f"{prefix}{msg_id}"
        if msg_id not in self.lines:
            blob = self.blobs[msg_id] if self.blobs and msg_id < len(self.blobs) else b""
            self.full_text[msg_id] = render_authoring(bytes(blob))
            self.lines[msg_id] = {"id": key, "text": self.full_text[msg_id], "segments": segments_from_text(self.full_text[msg_id])}
        return self.lines[msg_id]["id"]


# ---------------------------------------------------------------------------
# control-flow walk
# ---------------------------------------------------------------------------
def walk(scene: bytes, start: int) -> tuple[dict[int, list[int]], set[int], set[int]]:
    """blocks {first_offset: [op offsets]}, jump targets, call targets"""
    seen: set[int] = set()
    work = [start]
    blocks: dict[int, list[int]] = {}
    targets: set[int] = set()
    calls: set[int] = set()
    while work:
        pos = work.pop()
        if pos in seen or pos >= len(scene):
            continue
        first, ops = pos, []
        while pos < len(scene) and pos not in seen:
            seen.add(pos)
            op = scene[pos]
            sub = scene[pos + 1] if pos + 1 < len(scene) else 0
            sz = core._opcode_size(op, sub)
            if not sz:
                break
            ops.append(pos)
            if op == 0x02:
                t = struct.unpack_from("<H", scene, pos + 6)[0]; targets.add(t); work.append(t)
            elif op == 0x01:
                t = struct.unpack_from("<H", scene, pos + 1)[0]; targets.add(t); work.append(t)
            elif op == 0x1A:
                t = struct.unpack_from("<H", scene, pos + 1)[0]; calls.add(t); work.append(t)
            elif op == 0x3E:
                t = struct.unpack_from("<H", scene, pos + 5)[0]; targets.add(t); work.append(t)
            pos += sz
            if op in (0x21, 0x1B, 0x01):
                break
        blocks[first] = ops
    # a jump or call may land INSIDE a block walked earlier (retail calls into the middle of
    # straight-line code, Ru'Lude Wolfgang 58): split that block so the target starts one.
    for t in sorted(targets | calls):
        if t in blocks:
            continue
        owner = next((b for b, ops in blocks.items() if ops and ops[0] < t <= ops[-1]), None)
        if owner is None:
            continue
        ops = blocks[owner]
        k = next((i for i, o in enumerate(ops) if o == t), None)
        if k is None:
            continue
        blocks[owner], blocks[t] = ops[:k], ops[k:]
    # a block walked later may FALL THROUGH into the middle of a block walked earlier (the walk
    # stops at the first byte already seen): that entry point starts a block too, so the edge
    # is visible to successors() and the listing, and our explicit goto there compares equal
    changed = True
    while changed:
        changed = False
        for bo, ops in list(blocks.items()):
            if not ops:
                continue
            last = ops[-1]
            if scene[last] in (0x21, 0x1B, 0x01):
                continue
            nxt = last + core._opcode_size(scene[last], scene[last + 1] if last + 1 < len(scene) else 0)
            if nxt in blocks or nxt >= len(scene):
                continue
            owner = next((b for b, o in blocks.items() if o and o[0] < nxt <= o[-1]), None)
            if owner is None:
                continue
            k = next((i for i, o in enumerate(blocks[owner]) if o == nxt), None)
            if k is None:
                continue
            blocks[owner], blocks[nxt] = blocks[owner][:k], blocks[owner][k:]
            changed = True
    return blocks, targets, calls


# ---------------------------------------------------------------------------
# opcode -> step
# ---------------------------------------------------------------------------
def _raw(ctx: Ctx, pos: int, sz: int, sel_offsets: list[int] = (), note: str = "") -> dict:
    """raw bytes; 2-byte selectors at sel_offsets are replaced by {sel} and carried in sels."""
    b = bytearray(ctx.scene[pos:pos + sz])
    parts, sels = [], []
    i = 0
    while i < sz:
        if i in sel_offsets:
            sels.append(ctx.val(struct.unpack_from("<H", b, i)[0])); parts.append("{sel}"); i += 2
        else:
            parts.append(f"{b[i]:02x}"); i += 1
    step = {"op": "raw", "hex": " ".join(parts)}
    if sels:
        step["sels"] = sels
    if note:
        step["note"] = note
    if note == "unmodelled":
        ctx.raw_ops += 1              # layout unknown: selectors (if any) would be lost
    return step


def _tag(sc: bytes, pos: int) -> dict:
    """{"tag": "abcd"} for a printable 4-byte tag, else {"tagHex": "00fefefe"}."""
    b = sc[pos:pos + 4]
    if all(0x20 <= x < 0x7F for x in b):
        return {"tag": b.decode("ascii")}
    return {"tagHex": b.hex()}


def decode_op(ctx: Ctx, pos: int, nxt_op: Optional[int], labels: dict[int, str], sub_names: dict[int, str]) -> tuple[list[dict], int]:
    """Return (steps, bytes consumed). A say may consume the following 0x23."""
    sc = ctx.scene
    op = sc[pos]
    sub = sc[pos + 1] if pos + 1 < len(sc) else 0
    sz = core._opcode_size(op, sub)
    ctx.total_ops += 1
    u16, val, reg = ctx.u16, ctx.val, ctx.reg

    def lbl(t: int) -> str:
        return labels.get(t) or sub_names.get(t) or f"L{t:04x}"

    if op == 0x21:
        return [{"op": "end"}], sz
    if op == 0x1B:
        return [{"op": "return"}], sz
    if op == 0x01:
        return [{"op": "goto", "to": lbl(u16(pos + 1))}], sz
    if op == 0x1A:
        return [{"op": "call", "to": lbl(u16(pos + 1))}], sz
    if op == 0x02:
        kind = sc[pos + 5]
        st = {"op": "if", "a": val(u16(pos + 1)), "b": val(u16(pos + 3)), "cmp": IF_NAMES.get(kind & 0x7F, "ne"), "to": lbl(u16(pos + 6))}
        if kind & 0x80:
            st["signed"] = True
        if (kind & 0x7F) not in IF_NAMES:
            st["kindRaw"] = kind          # bytes retail jumps into mid-opcode decode as odd kinds: keep them verbatim
        return [st], sz
    if op == 0x3E:
        return [{"op": "if_bit", "reg": reg(u16(pos + 1)), "bit": val(u16(pos + 3)), "else": lbl(u16(pos + 5))}], sz
    if op == 0x03:
        return [{"op": "store", "into": reg(u16(pos + 1)), "from": val(u16(pos + 3))}], sz
    if op == 0x05:
        return [{"op": "store", "into": reg(u16(pos + 1)), "from": 1}], sz
    if op == 0x06:
        return [{"op": "store", "into": reg(u16(pos + 1)), "from": 0}], sz
    if op == 0x07:
        return [{"op": "add", "into": reg(u16(pos + 1)), "value": val(u16(pos + 3))}], sz
    if op == 0x0B:
        return [{"op": "add", "into": reg(u16(pos + 1)), "value": 1}], sz
    if op == 0x0C:
        return [{"op": "add", "into": reg(u16(pos + 1)), "value": 4294967295}], sz
    if op == 0x0E:
        return [{"op": "or", "into": reg(u16(pos + 1)), "value": val(u16(pos + 3))}], sz
    if op == 0x14:
        return [{"op": "mul", "into": reg(u16(pos + 1)), "value": val(u16(pos + 3))}], sz
    if op == 0x10:
        return [{"op": "shl", "into": reg(u16(pos + 1)), "value": val(u16(pos + 3))}], sz
    if op == 0x1C:
        return [{"op": "wait", "frames": val(u16(pos + 1))}], sz
    if op in (0x1D, 0x48):
        sel = u16(pos + 1)
        wait = nxt_op == 0x23
        if ctx.is_const(sel):
            st = {"op": "say", "text": ctx.line(val(sel))}
        else:
            st = {"op": "say", "textFrom": reg(sel)}
        st["system"] = (op == 0x48)
        if not wait:
            st["wait"] = False
        return [st], sz + (1 if wait else 0)
    if op == 0x24 or (op == 0xD4 and sub == 0x02):
        base = pos + (1 if op == 0x24 else 2)
        msg, cur, hide = u16(base), u16(base + 2), u16(base + 4)
        st = {"op": "menu"}
        if ctx.is_const(msg):
            mid = val(msg)
            lid = ctx.line(mid)
            q, opts = split_menu(ctx.full_text[mid])      # the same string may feed several menus
            ctx.lines[mid]["text"] = q
            ctx.lines[mid]["segments"] = segments_from_text(q)
            st["optionSegments"] = [segments_from_text(o) for o in opts]
            st["text"] = lid
            st["options"] = opts
        else:
            st["textFrom"] = reg(msg)
        if op == 0xD4:
            st["query"] = True
        c = val(cur)
        if c != 0:
            st["cursor"] = c
        h = val(hide)
        if h != 0:
            st["hide"] = h
        if nxt_op == 0x7F:
            st["waitOp"] = 0x7F
        elif nxt_op != 0x25:
            st["wait"] = False
        consumed = sz + (1 if nxt_op in (0x25, 0x7F) else 0)
        return [st], consumed
    if op == 0xD4 and sub == 0x03:
        return [{"op": "row_item", "row": val(u16(pos + 2)), "item": val(u16(pos + 4))}], sz
    if op == 0xD4 and sub in (0x04, 0x05):
        st = {"op": "augment_preview", "window": val(u16(pos + 2)), "item": val(u16(pos + 4)),
              "a": val(u16(pos + 6)), "b": val(u16(pos + 8)), "c": val(u16(pos + 10))}
        if sub == 0x04:
            st["kind"] = 4
        return [st], sz
    if op == 0x93:
        v = val(u16(pos + 1))
        return [{"op": "item_info", "item": "close" if v == 0 else v}], sz
    if op == 0xCC and sub == 0x01:
        return [{"op": "augment_window", "item": val(u16(pos + 2)), "a": val(u16(pos + 4)), "b": val(u16(pos + 6)), "c": val(u16(pos + 8))}], sz
    if op in (0x3C, 0x3D):
        st = {"op": "set_bit" if op == 0x3C else "clear_bit", "target": reg(u16(pos + 1)), "bit": val(u16(pos + 3))}
        lim = val(u16(pos + 5))
        if lim != 1:
            st["limit"] = lim
        return [st], sz
    if op in (0x40, 0x41):
        lo, hi = u16(pos + 1), u16(pos + 3)
        if ctx.is_const(lo) and ctx.is_const(hi):
            if op == 0x40:
                st = {"op": "bits_set", "lo": val(lo), "hi": val(hi), "into": reg(u16(pos + 5)), "from": val(u16(pos + 7))}
            else:
                st = {"op": "bits_get", "lo": val(lo), "hi": val(hi), "from": reg(u16(pos + 5)), "into": reg(u16(pos + 7))}
            return [st], sz
    if op == 0x43:
        if sub == 0x00 and nxt_op == 0x43:
            return [{"op": "server_update"}], sz + 2
    if op == 0x45:
        return [{"op": "task", "scene": val(u16(pos + 1)), "a": ctx.ent(ctx.u32(pos + 3)), "b": ctx.ent(ctx.u32(pos + 7)),
                 **_tag(sc, pos + 11), "dur": val(u16(pos + 15))}], sz
    if op == 0xAD and sub == 0x02:
        return [{"op": "effect", "variant": "ad", "id": val(u16(pos + 2)), "from": ctx.ent(ctx.u32(pos + 4)), "to": ctx.ent(ctx.u32(pos + 8)), "wait": 0}], sz
    if op == 0x9D and sub == 0x00:
        return [{"op": "table_read", "table": ctx.table(u16(pos + 2)), "into": reg(u16(pos + 4)), "index": val(u16(pos + 6))}], sz
    if op == 0x9D and sub == 0x05:
        return [{"op": "table_write", "table": ctx.table(u16(pos + 2)), "value": val(u16(pos + 4)), "index": val(u16(pos + 6))}], sz
    if op == 0x2B:
        sel = u16(pos + 5)
        wait = nxt_op == 0x23
        if ctx.is_const(sel):
            st = {"op": "print2", "speaker": ctx.ent(ctx.u32(pos + 1)), "text": ctx.line(val(sel))}
            if not wait:
                st["wait"] = False
            return [st], sz + (1 if wait else 0)
    if op in (0x52, 0x55):
        return [{"op": "schedule", "kind": "end" if op == 0x52 else "wait_main", "sel": val(u16(pos + 1)), "a": ctx.ent(ctx.u32(pos + 3)),
                 "b": ctx.ent(ctx.u32(pos + 7)), **_tag(sc, pos + 11)}], sz
    if op == 0x1F:
        if sub == 0:
            return [{"op": "set_pos", "sub": 0, "x": val(u16(pos + 2)), "z": val(u16(pos + 4)), "y": val(u16(pos + 6))}], sz
        return [{"op": "set_pos", "sub": sub}], sz
    if op == 0x37:
        return [{"op": "set_pos4", "x": val(u16(pos + 1)), "z": val(u16(pos + 3)), "y": val(u16(pos + 5)), "dir": val(u16(pos + 7))}], sz
    if op == 0x4E:
        st = {"op": "render_flag", "hide": sc[pos + 1] != 0, "entity": ctx.ent(ctx.u32(pos + 2))}
        if sc[pos + 1] not in (0, 1):
            st["flag"] = sc[pos + 1]
        return [st], sz
    if op == 0x80:
        return [{"op": "load_wait", "entity": ctx.ent(ctx.u32(pos + 1))}], sz
    if op == 0x92:
        return [{"op": "ui_flag", "flag": sc[pos + 1], "entity": ctx.ent(ctx.u32(pos + 2))}], sz
    if op == 0xBA:
        return [{"op": "calibrate", "entity": ctx.ent(ctx.u32(pos + 1)), "sels": [val(u16(pos + 5 + 2 * k)) for k in range(4)]}], sz
    if op == 0xB4 and sub in (1, 2, 4) and sz == 6:
        return [_raw(ctx, pos, sz, [4], note="B4 sub with one selector (layout only)")], sz
    if op == 0x2C:
        a, b = ctx.u32(pos + 1), ctx.u32(pos + 5)
        return [{"op": "action", "a": ctx.ent(a), "b": ctx.ent(b), **_tag(sc, pos + 9)}], sz
    if op == 0x2E:
        return [{"op": "cancel", "set": False}], sz
    if op == 0x42:
        return [{"op": "cancel", "set": True}], sz
    if op == 0x1E:
        return [{"op": "look_talk", "target": ctx.ent(ctx.u32(pos + 1))}], sz
    if op == 0x6F:
        return [{"op": "turn_wait", "phase": "sleep"}], sz
    if op == 0x70:
        return [{"op": "turn_wait", "phase": "turn"}], sz
    if op == 0x79:
        st = {"op": "look", "sub": sub, "a": ctx.ent(ctx.u32(pos + 2)), "b": ctx.ent(ctx.u32(pos + 6))}
        if sz >= 12:
            st["value"] = val(u16(pos + 10))         # sub 1 carries one more selector (12 bytes)
        return [st], sz
    if op == 0x4A:
        return [{"op": "look_at", "a": ctx.ent(ctx.u32(pos + 1)), "b": ctx.ent(ctx.u32(pos + 5))}], sz
    if op == 0x29:
        return [{"op": "request_wait", "a": sc[pos + 1], "entity": ctx.ent(ctx.u32(pos + 2)), "b": sc[pos + 6]}], sz   # CodeREQEW: b = slot of the entity's event table
    if op == 0x53:
        return [{"op": "wait_task", "a": ctx.ent(ctx.u32(pos + 1)), "b": ctx.ent(ctx.u32(pos + 5)), **_tag(sc, pos + 9)}], sz
    if op in (0x5B, 0x66):
        return [{"op": "schedule", "kind": "ext" if op == 0x5B else "ext2", "sel": val(u16(pos + 1)), "a": ctx.ent(ctx.u32(pos + 3)),
                 "b": ctx.ent(ctx.u32(pos + 7)), **_tag(sc, pos + 11)}], sz
    if op == 0x73:
        return [{"op": "effect_bare", "id": val(u16(pos + 1)), "from": ctx.ent(ctx.u32(pos + 3)), "to": ctx.ent(ctx.u32(pos + 7))}], sz
    if op == 0x71:
        if sub in (0x11, 0x13, 0x01):
            if sz >= 4:
                return [{"op": "input_wait", "sub": sub, "into": reg(u16(pos + 2))}], sz
            return [{"op": "input_wait", "sub": sub}], sz          # text-input wait: no operand (2 bytes)
        return [{"op": "input_open", "sub": sub, "sels": [val(u16(pos + 2 + 2 * k)) for k in range((sz - 2) // 2)]}], sz
    if op == 0x3F:
        return [{"op": "mod", "into": reg(u16(pos + 1)), "a": val(u16(pos + 3)), "b": val(u16(pos + 5))}], sz
    if op == 0x40:
        return [{"op": "bits_set", "lo": val(u16(pos + 1)), "hi": val(u16(pos + 3)), "into": reg(u16(pos + 5)), "from": val(u16(pos + 7))}], sz
    if op == 0x41:
        return [{"op": "bits_get", "lo": val(u16(pos + 1)), "hi": val(u16(pos + 3)), "from": reg(u16(pos + 5)), "into": reg(u16(pos + 7))}], sz
    if op == 0x00:
        return [{"op": "nop"}], sz
    if op == 0x23:
        return [{"op": "wait_dismiss"}], sz
    if op == 0x25:
        return [{"op": "wait_select"}], sz
    if op == 0x43 and sub in (0x00, 0x01):
        return [{"op": "server_wait" if sub == 0x01 else "server_send"}], sz
    if op == 0x9D and sz >= 8 and sub not in (0x02,) and xi_typed.spec_for(op, sz, sub) is None:
        label = ctx.table(u16(pos + 2))
        offs = sel_offsets(op, sz) or []
        st = _raw(ctx, pos, sz, offs, note=f"9D sub {sub:02x} (layout)")
        parts = st["hex"].split()
        parts[2:4] = ["{tbl:" + label + "}"]
        st["hex"] = " ".join(parts)
        return [st], sz
    # known layouts: literal bytes plus re-resolved selectors (lossless); unknown: literal bytes
    typed = xi_typed.spec_for(op, sz, sub)
    if typed is not None:
        name, fields = typed
        st = {"op": name}
        at = pos + 1
        for fld in fields:
            fname, kind = fld[0], fld[1]
            if kind == "bytes":
                st[fname] = sc[at:at + int(fld[2])].hex(" ")
            elif kind == "u8":
                st[fname] = sc[at]
            elif kind == "u16":
                st[fname] = u16(at)
            elif kind == "sel":
                st[fname] = val(u16(at))
            elif kind == "reg":
                st[fname] = reg(u16(at))
            elif kind == "ent":
                st[fname] = ctx.ent(ctx.u32(at))
            elif kind == "tbl":
                st[fname] = ctx.table(u16(at))
            elif kind == "msg":
                m = u16(at)
                st[fname] = ctx.line(val(m)) if ctx.is_const(m) else reg(m)
            elif kind == "tag":
                t = _tag(sc, at)
                st.update(t if fname == "tag" else {fname + ("Hex" if "tagHex" in t else ""): t.get("tag", t.get("tagHex"))})
            elif kind == "name16":
                raw = sc[at:at + 16]
                st[fname] = raw.split(bytes([0]))[0].decode("latin1")
                if raw != st[fname].encode("latin1").ljust(16, bytes([0])):
                    st[fname + "Hex"] = raw.hex()
            at += xi_typed.field_size(fld)
        return [st], sz
    offs = sel_offsets(op, sz, sub)
    if offs is not None:
        return [_raw(ctx, pos, sz, offs, note=f"{core._opcode_name(op)} (layout)")], sz
    return [_raw(ctx, pos, sz, note="unmodelled")], sz


# ---------------------------------------------------------------------------
# event -> cutscene dict
# ---------------------------------------------------------------------------
def successors(scene: bytes, blocks: dict[int, list[int]], bo: int) -> list[int]:
    """Blocks control can flow to from block ``bo`` without a call: jump / branch targets and
    the fallthrough into the next block when the block does not end in end / return / goto."""
    ops = blocks[bo]
    out: list[int] = []
    if not ops:
        return out
    last = ops[-1]
    op = scene[last]
    for pos in ops:
        o = scene[pos]
        if o == 0x02:
            out.append(struct.unpack_from("<H", scene, pos + 6)[0])
        elif o == 0x3E:
            out.append(struct.unpack_from("<H", scene, pos + 5)[0])
        elif o == 0x01:
            out.append(struct.unpack_from("<H", scene, pos + 1)[0])
    if op not in (0x21, 0x1B, 0x01):
        sz = core._opcode_size(op, scene[last + 1] if last + 1 < len(scene) else 0)
        nxt = last + sz
        if nxt in blocks:
            out.append(nxt)
    return [t for t in out if t in blocks]


def reach(scene: bytes, blocks: dict[int, list[int]], start: int) -> set[int]:
    seen, work = set(), [start]
    while work:
        b = work.pop()
        if b in seen or b not in blocks:
            continue
        seen.add(b)
        work.extend(successors(scene, blocks, b))
    return seen


def decompile_event(ffxi_dir: Path, zone: int, actor_id: int, event_id: int, installed: bool = False) -> tuple[dict, Ctx]:
    """Decompile from the PRISTINE DATs (.base) by default: retail stub events fall through into
    whatever bytes follow them, and in the installed DAT that is our own appended code
    (Ru'Lude Nomad Moogle 10163-10174). ``installed=True`` reads the live DATs (our events)."""
    zf = ex.zone_files(ffxi_dir, [zone])[0]
    if installed:
        actors, blobs, names = ex.load_zone(zf)
    else:
        import dataclasses
        zb = dataclasses.replace(zf, event=Path(str(zf.event) + ".base") if Path(str(zf.event) + ".base").exists() else zf.event,
                                 dialog=Path(str(zf.dialog) + ".base") if zf.dialog and Path(str(zf.dialog) + ".base").exists() else zf.dialog)
        actors, blobs, names = ex.load_zone(zb)
    return decompile_loaded(actors, blobs, names, zf, actor_id, event_id)


def decompile_loaded(actors, blobs, names, zf, actor_id: int, event_id: int, which: int = 0) -> tuple[dict, Ctx]:
    """Decompile from already-loaded zone tables. ``which`` picks among several entries with the
    same event id on the actor (0 = first, -1 = last: the compiler's appended copy)."""
    a = next(x for x in actors if x.actor_id == actor_id)
    zone_id = getattr(zf, "zone_id", None) or getattr(zf, "id", None) or getattr(zf, "zone", None)
    scene = bytes(a.scene_data)
    ctx = Ctx(scene, list(a.references), blobs)
    start = a.event_offsets[[i for i, e in enumerate(a.event_ids) if e == event_id][which]]
    bounds = sorted(o for o in a.event_offsets if o > start) + [len(scene)]
    limit = bounds[0]

    blocks, targets, calls = walk(scene, start)
    orphans = sorted(t for t in (targets | calls) if t not in blocks)   # inside an opcode / past the scene
    labels = {t: f"L{t:04x}" for t in targets}
    sub_names = {c: f"sub_{c:04x}" for c in calls}

    # main = every block reachable from the start WITHOUT taking a call (jumps, branches and
    # fallthrough), wherever retail placed it (events share tail code: Nomad Moogle 10196 jumps
    # past its own end; Magian 10124 falls through into a subroutine body). A sub body = what
    # its call target reaches minus main; a call target that is itself in main becomes a label.
    main_set = reach(scene, blocks, start)
    sub_bodies: dict[int, list[int]] = {}
    claimed: set[int] = set()
    for c in sorted(calls):
        if c in main_set or c in claimed:
            continue                           # inside a body emitted elsewhere: a label there (below)
        body = sorted(b for b in reach(scene, blocks, c) if b not in main_set and b not in claimed)
        sub_bodies[c] = body
        claimed.update(body)
    main_blocks = sorted(b for b in blocks if b in main_set or (b not in claimed and b not in sub_bodies))
    for c in calls:
        if c in main_set or c not in sub_bodies:
            labels[c] = sub_names[c]           # called into the main body or into another sub's
                                               # body (a shared tail): label, not a sub of its own

    def fallthrough(bo: int):
        ops = blocks[bo]
        if not ops:
            return None
        last = ops[-1]
        if scene[last] in (0x21, 0x1B, 0x01):
            return None
        nxt = last + core._opcode_size(scene[last], scene[last + 1] if last + 1 < len(scene) else 0)
        return nxt if nxt in blocks else None

    inames = ex.item_names()
    try:
        knames = ex.key_item_names()
    except Exception:
        knames = {}

    # which parameters the strings read as items / key items (annotate() runs before the
    # template pass, so compute it from the lines here)
    item_reads: set[int] = set(); key_reads: set[int] = set()
    for ln in ctx.lines.values():
        for sg in ln.get("segments") or []:
            if sg.get("kind") == "item":
                item_reads.add(int(sg["param"]))
            elif sg.get("kind") == "qtyitem":
                item_reads.add(int(sg["itemParam"]))
            elif sg.get("kind") == "keyitem":
                key_reads.add(int(sg["param"]))

    def annotate(steps: list[dict]) -> None:
        """Static knowledge for executors: a constant stored into a parameter gets its item /
        key-item name; a say / menu gets `resolved`, the parameters whose value is known at
        that point (set by a constant store earlier in the same block). Anything else is
        dynamic (server values, registers) and must be resolved at run time."""
        known: dict[int, int] = {}
        for st in steps:
            if st.get("label"):
                known = {}                      # a jump target: earlier stores no longer certain
            op = st.get("op")
            if op == "store" and isinstance(st.get("into"), dict) and "param" in st["into"]:
                n = int(st["into"]["param"])
                v = st.get("from")
                if isinstance(v, int):
                    known[n] = v
                    if v in inames and n in item_reads:
                        st["itemName"] = inames[v]
                    if v in knames and n in key_reads:
                        st["keyItemName"] = knames[v]
                else:
                    known.pop(n, None)
            elif op in ("add", "mul", "or", "shl", "bits_set", "bits_get", "table_read", "input_wait"):
                tgt = st.get("into")
                if isinstance(tgt, dict) and "param" in tgt:
                    known.pop(int(tgt["param"]), None)
            elif op == "server_update":
                known = {k: v for k, v in known.items() if k >= 8}
            elif op in ("say", "menu", "print2") and "text" in st:
                res = {}
                for n, v in sorted(known.items()):
                    entry = {"value": v}
                    if v in inames and n in item_reads:
                        entry["itemName"] = inames[v]
                    if v in knames and n in key_reads:
                        entry["keyItemName"] = knames[v]
                    res[str(n)] = entry
                if res:
                    st["resolved"] = res

    def emit_blocks(block_offsets: list[int], entry: Optional[int] = None) -> list[dict]:
        # the entry block goes first (retail may place jump targets below the event start);
        # whenever the emission order breaks a fallthrough, an explicit goto restores it
        # chains of fallthrough stay adjacent (a block has exactly one physical predecessor, so
        # the chains are disjoint): the entry's chain first, then each remaining block in offset
        # order with its own chain. No explicit goto is needed unless a chain is broken by a
        # block that belongs to another body.
        remaining = list(block_offsets)
        order: list[int] = []
        def take_chain(b: int):
            while b is not None and b in remaining:
                remaining.remove(b); order.append(b)
                b = fallthrough(b)
        if entry is not None and entry in remaining:
            take_chain(entry)
        while remaining:
            take_chain(remaining[0])
        for k, bo in enumerate(order):                 # label every broken chain's continuation first
            ft = fallthrough(bo)
            if ft is not None and (k + 1 >= len(order) or order[k + 1] != ft) and ft in emitted_all:
                labels.setdefault(ft, f"L{ft:04x}")
        steps: list[dict] = []
        for k, bo in enumerate(order):
            ops = blocks[bo]
            if not ops and bo in labels:
                steps.append({"op": "nop", "label": labels[bo], "note": "jump target inside undecodable bytes"})
                continue
            i = 0
            while i < len(ops):
                pos = ops[i]
                nxt = scene[ops[i + 1]] if i + 1 < len(ops) else None
                sts, consumed = decode_op(ctx, pos, nxt, labels, sub_names)
                if pos in labels and sts:
                    sts[0]["label"] = labels[pos]
                steps.extend(sts)
                # advance by consumed bytes (a say/menu/server_update may swallow the next op)
                end = pos + consumed
                i += 1
                while i < len(ops) and ops[i] < end:
                    i += 1
            ft = fallthrough(bo)
            if ft is None and ops and scene[ops[-1]] not in (0x21, 0x1B, 0x01):
                steps.append({"op": "end", "note": "retail runs off the scene / into undecodable bytes here"})
            if ft is not None and (k + 1 >= len(order) or order[k + 1] != ft):
                if ft not in emitted_all:
                    # falls into bytes no body emits (a walked-but-unreachable block): end here
                    steps.append({"op": "end", "note": "fallthrough into code outside every body"})
                else:
                    labels.setdefault(ft, f"L{ft:04x}")
                    steps.append({"op": "goto", "to": labels[ft], "note": "fallthrough made explicit"})
        return steps

    emitted_all = set(main_blocks)
    for _c, _body in sub_bodies.items():
        emitted_all.update(_body)

    def fold_end_gotos(steps: list[dict], keep: set = frozenset()) -> None:
        """A `goto` to a step that is only an `end` (directly or through goto chains) becomes an
        inline `end`; an end that then carries an unreferenced label loses it, and is dropped when
        the step before it cannot fall through. The compiler does the inverse (it shares one end
        block), so decompiling our own output gives these steps back unchanged."""
        idx = {st["label"]: i for i, st in enumerate(steps) if "label" in st}

        def ends_at(label: str, seen: tuple = ()) -> bool:
            i = idx.get(label)
            if i is None or label in seen:
                return False
            st = steps[i]
            if st["op"] == "end":
                return True
            return st["op"] == "goto" and ends_at(st["to"], seen + (label,))

        for st in steps:
            if st["op"] == "goto" and ends_at(st["to"]):
                st.pop("to"); st.pop("note", None); st["op"] = "end"
        refs: set[str] = set()

        def collect(x, own=None):
            if isinstance(x, dict):
                for k, v in x.items():
                    if k != "label":
                        collect(v)
            elif isinstance(x, list):
                for v in x:
                    collect(v)
            elif isinstance(x, str) and x in idx:
                refs.add(x)
        collect(steps)
        out: list[dict] = []
        for i, st in enumerate(steps):
            prev = out[-1] if out else None
            if st["op"] == "end" and "label" in st and st["label"] not in refs and st["label"] not in keep:
                if prev is not None and prev["op"] in ("end", "goto", "return"):
                    continue                      # unreachable end-only block
                st = {k: v for k, v in st.items() if k != "label"}
            if st["op"] == "end" and "label" not in st and prev is not None and prev["op"] in ("end", "goto", "return"):
                continue                          # dead end after a terminator (a folded goto left it)
            out.append(st)
        steps[:] = out

    steps = emit_blocks(main_blocks, entry=start)
    annotate(steps)
    if not steps or steps[-1].get("op") != "end":
        steps.append({"op": "end"})        # keep the compiler from adding its own epilogue block
    sub_steps: dict[int, list[dict]] = {}
    for c in sorted(sub_bodies):
        body = emit_blocks(sub_bodies[c], entry=c)
        annotate(body)
        sub_steps[c] = body

    def all_refs(*bodies) -> set:
        found: set = set()

        def rec(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    if k != "label":
                        rec(v)
            elif isinstance(x, list):
                for v in x:
                    rec(v)
            elif isinstance(x, str) and (x.startswith("L") or x.startswith("sub_")):
                found.add(x)
        for b in bodies:
            rec(b)
        return found
    # orphan targets (inside an opcode / past the scene) become a labelled `end` in the body
    # that references them: a second decompile of our bytes finds that end reachable only from
    # there and groups it the same way
    for t in orphans:
        lab = labels.get(t, f"L{t:04x}")
        end_step = {"op": "end", "label": lab, "note": "retail jumps inside an opcode here; treated as end"}
        owners = [c for c, body in sub_steps.items() if lab in all_refs(body)]
        if owners and lab not in all_refs(steps):
            sub_steps[owners[0]].append(end_step)
        else:
            steps.append(end_step)
    # fold the subs first, then main with the labels the folded subs still reference
    for c, body in sub_steps.items():
        fold_end_gotos(body, all_refs(steps, *(b for c2, b in sub_steps.items() if c2 != c)))
    keep = all_refs(*sub_steps.values())
    fold_end_gotos(steps, keep)
    for c in sorted(sub_bodies):
        body = sub_steps[c]
        if body and body[0].get("label") == sub_names.get(c):
            del body[0]["label"]
        if not body or body[-1].get("op") not in ("return", "end"):
            body.append({"op": "return"})
        steps.append({"op": "sub", "label": sub_names[c], "steps": body})

    for off in sorted(ctx.tables):
        steps.append({"op": "table", "label": ctx.tables[off]["label"], "entries": ctx.tables[off]["entries"]})

    # The template dictionary: every distinct placeholder the event's strings expect, and every
    # variable (parameter) they read with where its values come from, so an executor can check
    # up front that it can supply them (server values are dynamic; event constants are listed).
    placeholders: dict[str, dict] = {}
    variables: dict[int, dict] = {}

    def note_segments(segs: list[dict]):
        for sg in segs:
            k = sg.get("kind")
            if k in ("text", "newline", "prompt", "options", "raw"):
                continue
            key = json.dumps(sg, sort_keys=True)
            placeholders.setdefault(key, dict(sg))
            for pk in ("param", "countParam", "itemParam"):
                if pk in sg:
                    v = variables.setdefault(int(sg[pk]), {"param": int(sg[pk]), "readAs": set(), "sources": set(), "constants": set()})
                    v["readAs"].add(k if pk == "param" else ("count" if pk == "countParam" else "item"))

    for ln in ctx.lines.values():
        note_segments(ln.get("segments") or [])
    all_steps: list[dict] = []
    for st in steps:
        all_steps.append(st)
        if st.get("op") == "sub":
            all_steps.extend(st["steps"])
    for st in all_steps:
        for row in st.get("optionSegments") or []:
            note_segments(row)
    item_params = {n for n, v in variables.items() if v["readAs"] & {"item", "qtyitem"}}
    key_params = {n for n, v in variables.items() if "keyitem" in v["readAs"]}
    for st in all_steps:
        if st.get("op") == "store" and isinstance(st.get("into"), dict) and "param" in st["into"]:
            n = int(st["into"]["param"])
            v = variables.setdefault(n, {"param": n, "readAs": set(), "sources": set(), "constants": set()})
            if isinstance(st.get("from"), int):
                v["sources"].add("event constant"); v["constants"].add(st["from"])
            else:
                v["sources"].add("event register")
        elif st.get("op") in ("add", "mul", "or", "shl", "bits_set", "bits_get", "table_read", "input_wait"):
            tgt = st.get("into")
            if isinstance(tgt, dict) and "param" in tgt:
                variables.setdefault(int(tgt["param"]), {"param": int(tgt["param"]), "readAs": set(), "sources": set(), "constants": set()})["sources"].add("event arithmetic")
    for n, v in variables.items():
        if n < 8:
            v["sources"].add("server (startEvent / updateEvent)")
        v["readAs"] = sorted(v["readAs"]); v["sources"] = sorted(v["sources"])
        consts = sorted(v["constants"]); v["constants"] = consts
        named = {c: inames[c] for c in consts if c in inames and n in item_params}
        if named:
            v["itemNames"] = {str(c): nm for c, nm in named.items()}
        knamed = {c: knames[c] for c in consts if c in knames and n in key_params}
        if knamed:
            v["keyItemNames"] = {str(c): nm for c, nm in knamed.items()}
    template = {
        "placeholders": sorted(placeholders.values(), key=lambda x: json.dumps(x, sort_keys=True)),
        "variables": [variables[n] for n in sorted(variables)],
    }

    # The asset dictionary: everything the event refers to by id, resolved INSIDE the file so an
    # executor (and later our own custom content) never needs a DAT lookup. Items and key items
    # are the constants the event stores into token parameters; entities are every actor the
    # steps name; motions, effects and scenes are the tags and ids the steps schedule.
    assets: dict = {"items": {}, "keyItems": {}, "entities": {}, "motions": set(), "effects": set(), "scenes": set()}
    plurals = ex.item_plurals()
    ent_names = names if isinstance(names, dict) else {}

    def ent_entry(spec):
        if isinstance(spec, str) and spec.lower().startswith("0x"):
            eid = int(spec, 16)
            assets["entities"].setdefault(spec, {"id": eid, "name": ent_names.get(eid, ""), "zone": zone_id})
        elif spec in ("self", "player"):
            assets["entities"].setdefault(spec, {"role": spec, "id": actor_id if spec == "self" else None, "name": str(ent_names.get(actor_id, "")) if spec == "self" else "the player"})

    for st in all_steps:
        op = st.get("op")
        for k in ("a", "b", "target", "entity", "from", "to", "speaker"):
            v = st.get(k)
            if op in ("action", "wait_task", "schedule", "look", "look_at", "look_talk", "companion", "request_wait", "task", "effect", "effect_bare",
                      "render_flag", "load_wait", "ui_flag", "calibrate", "print2") and isinstance(v, str):
                ent_entry(v)
        if op in ("action", "wait_task", "schedule", "task") and st.get("tag"):
            assets["motions"].add(st["tag"])
        if op in ("effect", "effect_bare") and isinstance(st.get("id"), int):
            assets["effects"].add(st["id"])
        if op == "task" and isinstance(st.get("scene"), int):
            assets["scenes"].add(st["scene"])
        if op == "store" and isinstance(st.get("from"), int) and isinstance(st.get("into"), dict) and "param" in st["into"]:
            v, n = st["from"], int(st["into"]["param"])
            if v in inames and n in item_params:
                assets["items"][str(v)] = {"id": v, "name": inames[v], "plural": plurals.get(v, "")}
            if v in knames and n in key_params:
                assets["keyItems"][str(v)] = {"id": v, "name": knames[v]}
    for tb in ctx.tables.values():
        for e in tb["entries"]:
            if isinstance(e, int) and e in inames:
                assets["items"][str(e)] = {"id": e, "name": inames[e], "plural": plurals.get(e, "")}
    assets["motions"] = sorted(assets["motions"]); assets["effects"] = sorted(assets["effects"]); assets["scenes"] = sorted(assets["scenes"])
    assets["note"] = "ids are retail's today; custom content replaces these entries and keeps the same keys"

    name = names.get(actor_id, f"0x{actor_id:08X}") if isinstance(names, dict) else f"0x{actor_id:08X}"
    cs = {
        "schema": "xi.cutscene.v1",
        "spec": {
            "parameters": "Z[2 + n]: n = 0..7 are the server's startEvent/updateEvent values, higher n are set by store steps",
            "segments": "each line carries `segments`, the parsed form of `text`; menus carry `optionSegments` per row",
            "tokens": "docs/events/authoring.md, 'Text tokens: what a client must resolve'",
        },
        "description": f"Decompiled from {zf.event} actor 0x{actor_id:08X} event {event_id} ({name}); {ctx.total_ops} opcodes, {ctx.raw_ops} raw.",
        "eventId": event_id,
        "zone": zone_id,
        "actor": "owner",
        "npcName": str(name),
        "cast": {"cast": [{"id": "owner", "entity": f"0x{actor_id:08X}", "name": str(name)}]},
        "dialog": {"lines": [ctx.lines[k] for k in sorted(ctx.lines)]},
        "template": template,
        "assets": assets,
        "flags": {"cinematic": False, "facePlayer": False},
        "steps": steps,
    }
    return cs, ctx


# ---------------------------------------------------------------------------
# round-trip check
# ---------------------------------------------------------------------------
def _msg(blobs, v):
    """Message operand as compared by the checker: the raw dialog bytes (hex). Comparing the
    rendered text would let a token the renderer drops pass on both sides; the bytes cannot."""
    if not (isinstance(v, int) and blobs and v < len(blobs)):
        return v
    return _unpad(bytes(blobs[v])).hex()


def listing(scene: bytes, refs: list[int], blobs, start: int, msg_ids: bool = False) -> list[tuple]:
    """(opname, resolved operands) per opcode in control-flow order; jump targets as block indexes;
    message operands as raw dialog bytes."""
    blocks, targets, calls = walk(scene, start)
    order = canonical_order(scene, blocks, start)   # depth-first in control-flow order: layout-independent
    # end-like blocks: a lone `end`, or a lone goto to an end-like block (the compiler's shared
    # end). A block that FALLS THROUGH into one gets an inline `end` entry, so retail's inline
    # `21` and our `goto shared_end` compare equal; the end-like blocks themselves are dropped
    # by _normalize and jumps to them become END.
    end_like = {bo for bo, ops in blocks.items() if len(ops) == 1 and scene[ops[0]] == 0x21}
    grew = True
    while grew:
        grew = False
        for bo, ops in blocks.items():
            if bo not in end_like and len(ops) == 1 and scene[ops[0]] == 0x01                     and struct.unpack_from("<H", scene, ops[0] + 1)[0] in end_like:
                end_like.add(bo); grew = True
    # block numbers count only the blocks that survive normalisation (end-like, empty and
    # nop-only blocks get out-of-band numbers), so a label-carrier present in one layout and
    # not the other cannot shift every later index
    index_of: dict[int, int] = {}
    n_real = n_aux = 0
    for o in order:
        ops = blocks[o]
        aux = o in end_like or not ops or (all(scene[p] == 0x00 for p in ops[:-1]) and scene[ops[-1]] in (0x00, 0x01, 0x21) and any(scene[p] == 0x00 for p in ops))
        if aux:
            index_of[o] = 100000 + n_aux; n_aux += 1
        else:
            index_of[o] = n_real; n_real += 1
    out = []
    for bo in order:
        ops_here = list(blocks[bo])
        inline_end = False
        if ops_here and bo not in end_like:
            last = ops_here[-1]
            if scene[last] not in (0x21, 0x1B, 0x01):
                nxt = last + core._opcode_size(scene[last], scene[last + 1] if last + 1 < len(scene) else 0)
                # ... or the block runs off the end of the scene / into undecodable bytes (retail
                # stub tails): the decompiled copy ends explicitly, so count an end here too
                off_end = nxt >= len(scene) or core._opcode_size(scene[nxt], scene[nxt + 1] if nxt + 1 < len(scene) else 0) == 0
                inline_end = (nxt in end_like and nxt != bo) or off_end
        ft_target = None
        if ops_here and bo not in end_like and not inline_end:
            last = ops_here[-1]
            if scene[last] not in (0x21, 0x1B, 0x01):
                nxt = last + core._opcode_size(scene[last], scene[last + 1] if last + 1 < len(scene) else 0)
                if nxt in blocks and nxt != bo:
                    ft_target = nxt                    # listed as an explicit jump below
        for pos in ops_here:
            op = scene[pos]; sub = scene[pos + 1] if pos + 1 < len(scene) else 0
            sz = core._opcode_size(op, sub)
            name = core._opcode_name(op)
            ops: list = []
            b = scene[pos:pos + sz]
            if op in (0x01, 0x1A):
                ops = [index_of.get(struct.unpack_from("<H", b, 1)[0], -1)]
            elif op == 0x02:
                a, c = struct.unpack_from("<HH", b, 1); ops = [_res(refs, a), _res(refs, c), b[5], index_of.get(struct.unpack_from("<H", b, 6)[0], -1)]
            elif op == 0x3E:
                a, c = struct.unpack_from("<HH", b, 1); ops = [_res(refs, a), _res(refs, c), index_of.get(struct.unpack_from("<H", b, 5)[0], -1)]
            elif op in (0x1D, 0x48):
                sel = struct.unpack_from("<H", b, 1)[0]
                v = _res(refs, sel)
                ops = [_msg(blobs, v)]
            elif op == 0x2B:
                sel = struct.unpack_from("<H", b, 5)[0]
                v = _res(refs, sel)
                ops = [b[1:5].hex(), _msg(blobs, v)]
            elif op == 0xB0:
                sel = struct.unpack_from("<H", b, 10)[0]
                v = _res(refs, sel)
                ops = [b[1:10].hex(), _msg(blobs, v)]
            elif op == 0x24 or (op == 0xD4 and sub == 0x02):
                base = 1 if op == 0x24 else 2
                sels = struct.unpack_from("<HHH", b, base)
                v = _res(refs, sels[0])
                ops = [_msg(blobs, v), _res(refs, sels[1]), _res(refs, sels[2])]
            elif op == 0x9D and sz >= 8:
                toff = struct.unpack_from("<H", b, 2)[0]
                entries, tp = [], toff
                while tp + 2 <= len(scene):
                    tsel = struct.unpack_from("<H", scene, tp)[0]
                    plausible = ((tsel & 0x8000) and (tsel & 0x7FFF) < len(refs)) or tsel < 80                         or 0x1000 <= tsel < 0x1100 or 0x1700 <= tsel < 0x1800
                    if tsel == 0 or not plausible:
                        break
                    tv = _res(refs, tsel)
                    if tsel & 0x8000 and tv == 0:
                        break
                    entries.append(tv); tp += 2
                ops = [sub, tuple(entries), _res(refs, struct.unpack_from("<H", b, 4)[0]), _res(refs, struct.unpack_from("<H", b, 6)[0])]
            elif op in (0x03, 0x07, 0x0E, 0x14, 0x10, 0x3C, 0x3D, 0x40, 0x41, 0x1C, 0x93, 0x05, 0x06, 0x0B, 0x0C):
                ops = [_res(refs, s[0]) for s in struct.iter_unpack("<H", b[1:1 + ((sz - 1) // 2) * 2])]
            elif op == 0xD4 and sub in (0x03, 0x04, 0x05):
                ops = [sub] + [_res(refs, s[0]) for s in struct.iter_unpack("<H", b[2:2 + ((sz - 2) // 2) * 2])]
            elif op == 0xCC:
                ops = [sub] + [_res(refs, s[0]) for s in struct.iter_unpack("<H", b[2:2 + ((sz - 2) // 2) * 2])]
            else:
                offs = sel_offsets(op, sz, sub)
                if offs:
                    lit = bytearray(b)
                    vals = []
                    for o in offs:
                        vals.append(_res(refs, struct.unpack_from("<H", b, o)[0])); lit[o:o + 2] = b"\x00\x00"
                    ops = [lit.hex()] + vals
                else:
                    ops = [b.hex()]
            out.append((f"{name}" + (f".{sub:02x}" if op in (0xD4, 0xCC, 0x43, 0x71, 0x9D) else ""), tuple(ops), index_of[bo]))
        if inline_end:
            out.append(("end", ("21",), index_of[bo]))
        elif ft_target is not None:
            out.append(("set_exec", (index_of.get(ft_target, -1),), index_of[bo]))   # explicit fallthrough
    return out


def canonical_order(scene: bytes, blocks: dict[int, list[int]], start: int) -> list[int]:
    """Blocks in depth-first preorder from the event start, following each block's jump and
    call targets in opcode order and then its fallthrough. The same program gives the same
    order wherever its blocks sit in the DAT; unreached blocks follow in offset order."""
    order: list[int] = []
    seen: set[int] = set()
    stack = [start]
    while stack:
        b = stack.pop()
        if b in seen or b not in blocks:
            continue
        seen.add(b); order.append(b)
        nxt: list[int] = []
        for pos in blocks[b]:
            o = scene[pos]
            if o == 0x02:
                nxt.append(struct.unpack_from("<H", scene, pos + 6)[0])
            elif o == 0x3E:
                nxt.append(struct.unpack_from("<H", scene, pos + 5)[0])
            elif o in (0x01, 0x1A):
                nxt.append(struct.unpack_from("<H", scene, pos + 1)[0])
        nxt.extend(successors(scene, blocks, b)[len([1 for pos in blocks[b] if scene[pos] in (0x01, 0x02, 0x3E)]):])
        for t in reversed(nxt):
            if t in blocks and t not in seen:
                stack.append(t)
    order.extend(sorted(b for b in blocks if b not in seen))
    return order


def _res(refs: list[int], sel: int):
    if sel & 0x8000:
        i = sel & 0x7FFF
        return int(refs[i]) if i < len(refs) else f"ref?{i}"
    return Ctx.reg(sel) if not isinstance(Ctx.reg(sel), dict) else json.dumps(Ctx.reg(sel), sort_keys=True)


def check_roundtrip(ffxi_dir: Path, zone: int, actor_id: int, event_id: int, cs: dict) -> dict:
    """Compile the decompiled cutscene in bare mode against the pristine DATs and compare."""
    from . import xi_compile
    zf = ex.zone_files(ffxi_dir, [zone])[0]
    ev = _pristine(zf.event); dl = _pristine(zf.dialog)
    actors_a = core.parse_raw_actors(ev)
    a = next(x for x in actors_a if x.actor_id == actor_id)
    # compile onto a one-event copy of the actor: the decompiled JSON is self-contained, so the
    # recompiled bytes never need the actor's other events, and an actor at the 64K edge
    # (San d'Oria 0x010E6112, Bastok Mines 0x010EA090) can still be round-tripped
    import dataclasses
    start = a.event_offsets[a.event_ids.index(event_id)]
    small = dataclasses.replace(a, event_offsets=[0], event_ids=[event_id], scene_data=bytes(a.scene_data[start:start + 1]) or bytes([0x21]),
                                block_pad=bytes(), raw_block=bytes(), dirty=True)
    ev_small = core.build_event_dat([small])
    res = xi_compile.compile_cutscene(json.loads(json.dumps(cs)), ev_small, dl, ffxi_dir=ffxi_dir)
    blobs_a, _ = _blobs(dl)
    la = listing(bytes(a.scene_data), list(a.references), blobs_a, a.event_offsets[a.event_ids.index(event_id)])
    actors_b = core.parse_raw_actors(res.event_dat)
    b = next(x for x in actors_b if x.actor_id == actor_id)
    blobs_b, _ = _blobs(res.dialog_dat)
    # the compiled copy is the LAST event with that id on the actor
    idx = max(i for i, e in enumerate(b.event_ids) if e == res.event_id)
    lb = listing(bytes(b.scene_data), list(b.references), blobs_b, b.event_offsets[idx])
    la_n = _normalize(la); lb_n = _normalize(lb)
    # retail events may end by falling through into the next event's code; ours must end
    # explicitly, so one trailing `end` beyond retail's last entry is not a difference
    if len(lb_n) == len(la_n) + 1 and lb_n[-1][0] == "end" and (not la_n or la_n[-1][0] != "end"):
        lb_n = lb_n[:-1]
    mism = [(i, x, y) for i, (x, y) in enumerate(zip(la_n, lb_n)) if x != y]
    # second decode: decompile OUR bytes again and compare the JSON with the first decompile
    # (labels, sub names and line ids are offset-derived, so both are canonicalised by first
    # appearance before comparing)
    stable, first_diff = True, None
    try:
        import dataclasses, tempfile, os
        with tempfile.TemporaryDirectory() as td:
            pe, pd = Path(td) / "ev.DAT", Path(td) / "dl.DAT"
            pe.write_bytes(res.event_dat); pd.write_bytes(res.dialog_dat)
            zf2 = dataclasses.replace(zf, event=pe, dialog=pd)
            actors2, blobs2, names2 = ex.load_zone(zf2)
            cs2, _ = decompile_loaded(actors2, blobs2, names2, zf2, actor_id, res.event_id, which=-1)
        c1, c2 = canon(cs), canon(cs2)
        stable = c1 == c2
        if not stable:
            for k in ("lines", "steps"):
                if c1[k] != c2[k]:
                    for i, (x, y) in enumerate(zip(c1[k], c2[k])):
                        if x != y:
                            first_diff = (k, i, str(x)[:160], str(y)[:160]); break
                    else:
                        first_diff = (k, "length", len(c1[k]), len(c2[k]))
                    break
    except Exception as e:                                    # a failing second pass is itself a finding
        stable, first_diff = False, ("error", type(e).__name__, str(e)[:160], "")
    return {"retail_ops": len(la), "ours_ops": len(lb), "mismatches": len(mism) + abs(len(la_n) - len(lb_n)),
            "first_mismatches": [(i, str(x)[:120], str(y)[:120]) for i, x, y in mism[:12]],
            "compiled_event": res.event_id, "stable": stable, "stable_diff": first_diff}


_LBL = re.compile(r"^(L[0-9a-f]{4}|sub_[0-9a-f]{4}|tbl_[0-9a-f]{4}|m\d+)$")
_TBL_IN = re.compile(r"\{tbl:(tbl_[0-9a-f]{4})\}")


def canon(cs: dict) -> dict:
    """The decompiled JSON with every offset-derived name (labels, subs, line ids) replaced by
    its order of first appearance, so two decompiles of the same program compare equal
    wherever they were placed in the DAT."""
    names: dict[str, str] = {}                  # line ids -> #tN (set below), labels -> #N

    def nm(v):
        if isinstance(v, str) and _LBL.match(v):
            if v not in names:
                names[v] = f"#{sum(1 for x in names.values() if not x.startswith('#t'))}"
            return names[v]
        if isinstance(v, str) and "{tbl:" in v:          # table placeholders inside raw hex
            return _TBL_IN.sub(lambda m: "{tbl:" + nm(m.group(1)) + "}", v)
        return v

    def walk(x):
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items() if k not in ("description", "note")}
        if isinstance(x, list):
            return [walk(v) for v in x]
        return nm(x)

    # lines in text order (their emission order follows block placement, which moves)
    # lines: one per distinct text (retail repeats strings under several ids; the compiler
    # shares one blob), named in text order
    by_text: dict[str, str] = {}
    for l in cs.get("dialog", {}).get("lines", []):
        by_text.setdefault(l["text"], None)
    for i, t in enumerate(sorted(by_text)):
        by_text[t] = f"#t{i}"
    for l in cs.get("dialog", {}).get("lines", []):
        names[l["id"]] = by_text[l["text"]]
    lines = [{"id": by_text[t], "text": t} for t in sorted(by_text)]
    all_steps = cs.get("steps", [])
    steps = walk([st for st in all_steps if st.get("op") not in ("table", "sub")])
    # subs: whichever sub is already named (referenced) first comes next, so definition order
    # (retail offset order) does not matter
    pending = [st for st in all_steps if st.get("op") == "sub"]
    subs: list = []
    while pending:
        ranked = sorted(pending, key=lambda st: (names.get(st["label"]) is None, names.get(st["label"], "")))
        st = ranked[0]; pending.remove(st); subs.append(walk(st))
    tables = sorted((walk(st) for st in all_steps if st.get("op") == "table"), key=lambda st: st["label"])
    return {"lines": lines, "steps": steps + subs + tables}


ALIASES = {"set_zero": ("get_store", 0), "set_one": ("get_store", 1), "inc": ("add", 1), "dec": ("add", 4294967295)}


def _normalize(entries: list[tuple]) -> list[tuple]:
    """Semantic equivalents compare equal: retail's set_zero/set_one/inc/dec are our store/add
    with a constant, and a jump to a block that is only an `end` counts as an inline `end`
    (the compiler folds intermediate ends into one shared end; retail does both). Entries
    carry the walker's block index as their third element."""
    members: dict[int, list[int]] = {}
    for i, (_, _, bi) in enumerate(entries):
        members.setdefault(bi, []).append(i)
    def _end_entry(e) -> bool:                  # `end`, or a goto to nowhere (target outside the scene)
        if e[0] == "end":
            return True
        return e[0] == "set_exec" and bool(e[1]) and isinstance(e[1][0], int) and (e[1][0] < 0 or e[1][0] not in members)
    end_only = {bi for bi, idx in members.items() if len(idx) == 1 and _end_entry(entries[idx[0]])}
    def _nopish(idx: list[int]) -> bool:      # label-carrier nops, optionally followed by one goto
        names = [entries[i][0] for i in idx]
        return "noop" in names and all(n == "noop" for n in names[:-1]) and names[-1] in ("noop", "set_exec", "end")
    nop_only = {bi for bi, idx in members.items() if _nopish(idx)}
    # ... and, transitively, blocks that are only a jump to such a block (the compiler folds every
    # intermediate `end` into `set_exec -> shared end`, so an `if -> end` becomes `if -> [goto end]`)
    grew = True
    while grew:
        grew = False
        for bi, idx in members.items():
            if bi not in end_only and len(idx) == 1:
                nm, ops, _ = entries[idx[0]]
                if nm == "set_exec" and ops and ops[0] in end_only:
                    end_only.add(bi); grew = True
    # trampolines: a block that is only `goto X` (X a real block) is transparent: jumps to it
    # go to X, and it is dropped (our layout adds them where retail placed X right after)
    tramp: dict[int, int] = {}
    for bi, idx in members.items():
        if bi in end_only or bi in nop_only or len(idx) != 1:
            continue
        nm, ops, _ = entries[idx[0]]
        if nm == "set_exec" and ops and isinstance(ops[0], int) and ops[0] in members and ops[0] not in end_only and ops[0] != bi:
            tramp[bi] = ops[0]

    def final(t, seen=()):
        while t in tramp and t not in seen:
            seen = seen + (t,); t = tramp[t]
        return t
    # renumber the surviving blocks so jump targets stay comparable after the drop
    kept = [bi for bi in sorted(members) if bi not in end_only and bi not in nop_only and bi not in tramp]
    renum = {bi: i for i, bi in enumerate(kept)}
    TARGET_POS = {"jump": 0, "set_exec": 0, "if": 3, "bit_branch": 2}
    out = []
    for name, ops, bi in entries:
        if name in ALIASES:
            alias, const = ALIASES[name]
            out.append((alias, (ops[0], const))); continue
        if bi in end_only or bi in nop_only or bi in tramp:
            continue                      # a lone end (or jump-only block to one), a label-carrier nop, a trampoline
        if name == "set_exec" and ops and (ops[0] in end_only or ops[0] in nop_only or ops[0] == -1
                                           or (isinstance(ops[0], int) and ops[0] >= 0 and ops[0] not in members)):
            out.append(("end", ("21",))); continue     # a goto to an end, to nowhere, or to a label-carrier nop
        if name in TARGET_POS and len(ops) > TARGET_POS[name]:
            t = ops[TARGET_POS[name]]
            if isinstance(t, int):
                t = final(t)
            is_end = t in end_only or (isinstance(t, int) and t >= 0 and t not in members)   # merged lone end
            if t in nop_only or t == -1:
                t_out = "END"             # no block there (target inside an opcode): compared as an end
            else:
                t_out = "END" if is_end else renum.get(t, t)
            ops = tuple(t_out if i == TARGET_POS[name] else v for i, v in enumerate(ops))
        out.append((name, ops))
    return out


def _pristine(p: Path) -> bytes:
    base = Path(str(p) + ".base")
    return (base if base.exists() else p).read_bytes()


def _blobs(dialog_dat: bytes):
    from ..dialog import xi_dialog
    return xi_dialog.raw_entry_blobs(dialog_dat)

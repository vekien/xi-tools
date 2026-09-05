#!/usr/bin/env python3
"""Decode FFXI *event-message* DATs — the per-zone "dialog" string tables
(e.g. ``ROM/25/39.DAT`` for Southern San d'Oria) that hold NPC speech and
cutscene text.

Container format (matches Shining Fantasia's ``EventMessage`` resource):

  * bytes 0-2  : 24-bit resource length; ``len == lsb24(0) + 4``.
  * byte  3    : ``0x10`` flags the body as lightly obfuscated — every byte
                 from offset 4 onward is XOR-0x80. (This is why a raw grep for
                 dialog text finds nothing; it is obfuscation, not encryption.)
  * offset 4.. : a u32 offset table. Each entry is ``offset + 4`` into the file;
                 the first entry doubles as the end-of-table marker. Strings run
                 from one offset to the next, minus the trailing NUL.

Each string is a near-Shift_JIS byte stream with FFXI-specific control codes
("opcodes"). The decode tables live in :mod:`xi.dialog._sjis_data`, generated
from Shining Fantasia. The headline opcode is the **continue prompt**:

  * ``7F 31/32/33/37`` → manual prompt: show ▼ and wait for a keypress.
  * ``7F 34/35/36 NN`` → auto prompt: ``NN`` = seconds before it self-advances.

Other common opcodes: ``07`` newline, ``02 xx xx`` set-X, ``03 xx xx`` set-Y,
``08`` player name, ``09`` NPC name, ``0A nn`` value substitution.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

from xi.dialog._sjis_data import BYTE_LEN, SPECIAL, CODE_NAMES, CHAR_OVERRIDE


class DialogError(Exception):
    """Raised when a DAT is not a valid event-message string table."""


def looks_like_event_message(data: bytes) -> bool:
    """Cheap signature check: an event-message file's 24-bit length header equals
    its size minus the 4-byte header. Distinguishes a dialog DAT from a zone
    model / NPC / event-bytecode DAT without fully parsing it."""
    return len(data) >= 8 and (data[0] | data[1] << 8 | data[2] << 16) + 4 == len(data)


# ── friendly opcode naming ──────────────────────────────────────────────────
# The raw SpecialCode names from Shining Fantasia are faithful but terse (the
# prompt codes are literally "UNKNOWN"). We layer readable names + a compact
# inline token on top, and decide which opcodes belong inline in the readable
# `text` (content substitutions) vs. only in the structured `opcodes` list
# (layout + flow control).

# SpecialCode names whose token is woven into the readable text (they ARE the
# sentence — a name/number the engine substitutes at display time).
_CONTENT = {
    "PLAYER_NAME", "NPC_NAME", "VALUE", "INDEX", "NUMBER", "TIME",
    "SKILL_TEXT", "SPELL_NAME", "EVENT_SPELL_NAME", "ABILITY_NAME",
    "ABILITY_NAME2", "EVENT_ABILITY_NAME", "PARTY_MEMBER_NAME",
    "PARTY_MEMBER_NAME_BY_ID", "EVENT_STRING", "SPECIAL_NAME",
    "TWO_DIGIT_VALUE", "FOUR_DIGIT_VALUE", "HEX_VALUE", "BINARY_VALUE",
    "HEADING", "PLAYER_GENDER",
}

# Compact inline tokens for the content substitutions above.
_CONTENT_TOKEN = {
    "PLAYER_NAME": "{player}", "NPC_NAME": "{npc}", "PLAYER_GENDER": "{gender}",
    "HEADING": "{heading}", "SPECIAL_NAME": "{name}",
}


@dataclass
class Opcode:
    """One control code inside a string."""
    pos: int            # byte offset within the string
    raw: str            # hex of the opcode + its param bytes, e.g. "7f 34 09"
    code: str           # SpecialCode name (UNKNOWN / PLAYER_NAME / VALUE / …)
    name: str           # friendly name (prompt / prompt_auto / newline / …)
    params: list[int] = field(default_factory=list)
    note: str = ""      # human description


@dataclass
class Entry:
    index: int
    offset: int         # byte offset of the string within the file
    length: int         # raw string length in bytes (excludes trailing NUL)
    text: str           # readable rendering (newlines real, content substituted)
    opcodes: list[Opcode]
    raw_hex: str        # full de-obfuscated string bytes, for ground truth

    def to_dict(self, with_raw=True, with_opcodes=True):
        d = {"index": self.index, "offset": self.offset, "length": self.length,
             "text": self.text}
        if with_opcodes:
            d["opcodes"] = [asdict(o) for o in self.opcodes]
        if with_raw:
            d["raw_hex"] = self.raw_hex
        return d

    # Focused views, so a dump can be split into easy-to-share companion files.
    def text_dict(self):
        return {"index": self.index, "offset": self.offset,
                "length": self.length, "text": self.text}

    def opcodes_dict(self):
        return {"index": self.index, "text": self.text,
                "opcodes": [asdict(o) for o in self.opcodes]}

    def hex_dict(self):
        return {"index": self.index, "offset": self.offset,
                "length": self.length, "raw_hex": self.raw_hex}


def _decode_char(b: bytes) -> str:
    """Decode 1-2 raw bytes as a character, falling back to an escape."""
    try:
        return b.decode("cp932")
    except UnicodeDecodeError:
        return "".join(f"\\x{x:02x}" for x in b)


def _friendly(cval: int, code: int, params: list[int]) -> tuple[str, str, str, bool]:
    """Return (name, inline_token, note, inline_in_text) for one opcode."""
    # Continue-prompt family lives in the 0x7F00-0x7FFF control region.
    if 0x7F00 <= cval <= 0x7FFF:
        sel = cval & 0xFF
        if sel in (0x31, 0x32, 0x33, 0x37):
            return "prompt", "[▼]", "wait-for-key continue prompt", False
        if sel in (0x34, 0x35, 0x36):
            t = params[0] if params else 0
            return "prompt_auto", f"[auto {t}s]", \
                f"auto-advance after {t}s (else wait-for-key)", False
        return f"ctrl_7f{sel:02x}", "", "", False

    name = CODE_NAMES.get(code, f"code_{code}")

    if cval == 0x07:          # newline in event/dialog text
        return "newline", "\n", "line break", True
    if name == "NUL":
        return "end", "", "string terminator", False
    if name == "SET_X":
        v = (params[1] << 8 | params[0]) if len(params) >= 2 else 0
        return "set_x", "", f"text X position = {v}", False
    if name == "SET_Y":
        v = (params[1] << 8 | params[0]) if len(params) >= 2 else 0
        return "set_y", "", f"text Y position = {v}", False
    if name in ("VALUE", "NUMBER", "TWO_DIGIT_VALUE", "FOUR_DIGIT_VALUE",
                "HEX_VALUE", "BINARY_VALUE", "INDEX"):
        p = params[0] if params else "?"
        return name.lower(), f"{{{p}}}", f"{name.lower()} parameter", True
    if name in _CONTENT:
        token = _CONTENT_TOKEN.get(name, f"{{{name.lower()}}}")
        return name.lower(), token, name.lower().replace("_", " "), True

    # Unmapped / structural control code (gauge glyphs, menu codes, …): keep it
    # out of the readable text, but record it faithfully under a stable id.
    tag = f"ctrl_{cval:02x}" if cval < 0x100 else f"ctrl_{cval:04x}"
    return tag, "", CODE_NAMES.get(code, "").lower().replace("_", " "), False


def decode_event_string(raw: bytes) -> tuple[str, list[Opcode]]:
    """Decode one raw (de-obfuscated, NUL-stripped) string into readable text +
    a list of the control codes it contains."""
    out: list[str] = []
    ops: list[Opcode] = []
    i = 0
    n_raw = len(raw)
    while i < n_raw:
        lead = raw[i]
        n = BYTE_LEN[lead] or 1
        if n == 2 and i + 1 < n_raw:
            cval = (lead << 8) | raw[i + 1]
        else:
            n = 1
            cval = lead
        sp = SPECIAL.get(cval)
        if sp is not None:
            if cval == 0:          # NUL terminator — stop; don't record it
                break
            code, extra = sp
            params = list(raw[i + n:i + n + extra])
            name, token, note, inline = _friendly(cval, code, params)
            ops.append(Opcode(pos=i,
                              raw=" ".join(f"{x:02x}" for x in raw[i:i + n + extra]),
                              code=CODE_NAMES.get(code, str(code)),
                              name=name, params=params, note=note))
            if inline and token:
                out.append(token)
            i += n + extra
        else:
            ch = chr(CHAR_OVERRIDE[cval]) if cval in CHAR_OVERRIDE else _decode_char(raw[i:i + n])
            out.append(ch)
            i += n
    return "".join(out), ops


def parse_event_message(data: bytes) -> tuple[list[Entry], bool]:
    """Parse an event-message DAT into entries. Returns (entries, obfuscated)."""
    if len(data) < 8:
        raise DialogError("file too small to be an event-message table")
    b = bytearray(data)
    res_len = (b[0] | b[1] << 8 | b[2] << 16) + 4
    if res_len != len(b):
        raise DialogError(
            f"not an event-message table: header length {res_len} != file size {len(b)}")
    obfuscated = (b[3] == 0x10)
    if obfuscated:
        for i in range(4, len(b)):
            b[i] ^= 0x80

    start = int.from_bytes(b[4:8], "little") + 4
    if not (8 <= start <= len(b)):
        raise DialogError("event-message offset table is out of range")

    entries: list[Entry] = []
    o = 4
    prev = 0
    idx = 0
    while o < start:
        so = int.from_bytes(b[o:o + 4], "little") + 4
        no = int.from_bytes(b[o + 4:o + 8], "little") + 4 if o + 4 < start else len(b)
        o += 4
        if so >= len(b) or so <= prev or no < so:
            break
        prev = so
        raw = bytes(b[so:max(so, no - 1)])   # drop trailing NUL
        text, ops = decode_event_string(raw)
        entries.append(Entry(index=idx, offset=so, length=len(raw), text=text,
                             opcodes=ops,
                             raw_hex=" ".join(f"{x:02x}" for x in raw)))
        idx += 1
    return entries, obfuscated


def load(path) -> tuple[list[Entry], bool]:
    """Read + parse an event-message DAT from disk."""
    with open(path, "rb") as f:
        return parse_event_message(f.read())


# ── encoding / rebuild (authoring custom dialog) ────────────────────────────
# An entry's byte-gap can hold several NUL-separated sub-strings (gender/plural
# /menu variants); only the first is the displayed line. So rebuilds preserve
# every entry's full gap verbatim, and an edit swaps only that first part.

def raw_entry_blobs(data: bytes) -> tuple[list[bytes], bool]:
    """Split a container into the full (de-obfuscated) byte-gap of each entry —
    everything between consecutive offsets, verbatim. Returns (blobs, obfuscated).
    Re-building these unchanged reproduces the file byte-for-byte."""
    if len(data) < 8:
        raise DialogError("file too small to be an event-message table")
    b = bytearray(data)
    res_len = (b[0] | b[1] << 8 | b[2] << 16) + 4
    if res_len != len(b):
        raise DialogError(
            f"not an event-message table: header length {res_len} != file size {len(b)}")
    obfuscated = (b[3] == 0x10)
    if obfuscated:
        for i in range(4, len(b)):
            b[i] ^= 0x80
    start = int.from_bytes(b[4:8], "little") + 4
    if not (8 <= start <= len(b)):
        raise DialogError("event-message offset table is out of range")
    n = (start - 4) // 4
    offs = [int.from_bytes(b[4 + 4 * i:8 + 4 * i], "little") + 4 for i in range(n)]
    bounds = offs + [len(b)]
    return [bytes(b[bounds[k]:bounds[k + 1]]) for k in range(n)], obfuscated


def build_container(blobs: list[bytes], obfuscated: bool = True) -> bytes:
    """Assemble entry byte-gaps back into a container: 4-byte header (24-bit
    length + `0x10` flag), the u32 offset table, then the concatenated gaps,
    XOR-`0x80` from offset 4. Inverse of :func:`raw_entry_blobs`."""
    n = len(blobs)
    table = bytearray()
    pos = 4 + 4 * n                      # file offset of the first string
    for blob in blobs:
        table += (pos - 4).to_bytes(4, "little")
        pos += len(blob)
    out = bytearray(4)                   # header placeholder (bytes 0-3)
    out += table
    out += b"".join(blobs)
    ln = len(out) - 4
    out[0], out[1], out[2] = ln & 0xFF, (ln >> 8) & 0xFF, (ln >> 16) & 0xFF
    out[3] = 0x10 if obfuscated else 0x00
    if obfuscated:
        for i in range(4, len(out)):
            out[i] ^= 0x80
    return bytes(out)


# Friendly authoring escapes -> raw bytes.
#   \n -> 0x07 newline   \v -> 7F 31 prompt (press enter)   \\ -> literal '\'
#   {player} -> 08   {npc} -> 09   {auto:N} -> 7F 34 NN (auto-advance N sec)
#   {N} (digits) -> 0A NN  numeric event parameter N (server startEvent/updateEvent p_N)
#   {item:N} -> 01 05 25 82 (80|N) 80 80  item name for the id in parameter N; {name:0xKK:N} other kinds
#   {keyitem:N} -> kind 0x36 (key item name)   {rowitem:N} -> kind 0x24 (item name, coffer rows)
#   {index:N}[a/b/c] -> 0C NN + literal bracket list: the client shows alternative number N
#   {plural:N}[a/b] -> 7F 92 NN (alternative by the count in parameter N)   {rowname:N} -> in-row item name
#   {qtyitem:C:I} -> 01 09 29 ... ("<count> <items>", count in parameter C, item id in parameter I)
def _v7b(v: int) -> list[int]:
    """The `82` value form: three bytes, seven bits each, low first, high bit set (zone 236 is
    `ec 81 80`; parameter indexes fit in the first byte)."""
    return [0x80 | (v & 0x7F), 0x80 | ((v >> 7) & 0x7F), 0x80 | ((v >> 14) & 0x7F)]


def encode_event_string(s: str) -> bytes:
    """Encode authoring text (with escapes/tokens) into raw event-string bytes.
    Inverse-ish of the `text` rendering; see module docstring for the codes."""
    out = bytearray()
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\n":          # a real newline (e.g. multiline input) -> 0x07
            out.append(0x07); i += 1; continue
        if c == "\r":
            i += 1; continue
        if c == "\\" and i + 1 < n:
            nx = s[i + 1]
            if nx == "n":
                out.append(0x07); i += 2; continue
            if nx in ("v", "p"):
                out += b"\x7f\x31"; i += 2; continue
            if nx == "\\":
                out.append(0x5C); i += 2; continue
            # unknown escape: emit the backslash literally, reparse next char
            out.append(0x5C); i += 1; continue
        if c == "{":
            end = s.find("}", i)
            if end > i:
                tok = s[i + 1:end]
                if tok == "player":
                    out.append(0x08); i = end + 1; continue
                if tok == "npc":
                    out.append(0x09); i = end + 1; continue
                if tok.startswith("auto:"):
                    try:
                        sec = int(tok[5:])
                    except ValueError:
                        raise DialogError(f"bad auto-prompt token: {{{tok}}}")
                    out += bytes([0x7F, 0x34, sec & 0xFF]); i = end + 1; continue
                if tok.isdigit():
                    # {n} -> 0A nn: numeric substitution of event parameter n
                    # (Work_Zone[2 + n]; n >= 8 reads the extended Work_Zone_1700 bank).
                    out += bytes([0x0A, int(tok) & 0xFF]); i = end + 1; continue
                if tok == "options":
                    out.append(0x0B); i = end + 1; continue     # menu rows start here (CodeQUERY case 11)
                if tok.startswith("raw:"):
                    # {raw:7f800101010120}: verbatim control bytes (retail menu rows open with
                    # 7F 80 01 + 01 01 01 + space before the item-name code; meaning unknown).
                    try:
                        out += bytes.fromhex(tok[4:])
                    except ValueError:
                        raise DialogError(f"bad raw token: {{{tok}}}")
                    i = end + 1; continue
                if tok == "noprompt":
                    # {noprompt}: marker only; the compiler does not append the 7F 31 prompt to
                    # a line ending with it (retail lines such as Oseem 12588 have none)
                    i = end + 1; continue
                if tok.startswith("member:"):
                    # {member:n} -> 19 nn: the name of party member n (Mog House menus, Bastok Markets 487)
                    try:
                        idx = int(tok[7:])
                    except ValueError:
                        raise DialogError(f"bad member token: {{{tok}}}")
                    out += bytes([0x19, idx & 0xFF]); i = end + 1; continue
                if tok == "gender":
                    # {gender}[a/b] -> 7F 85 + literal brackets: alternative by the player's gender
                    # (Magian Moogle 14471 "found {gender}[his/her] way")
                    out += bytes([0x7F, 0x85]); i = end + 1; continue
                if tok.startswith("plural:"):
                    # {plural:n}[a/b] -> 7F 92 nn + literal brackets: the client picks the
                    # alternative by the COUNT in parameter n ("{3} time{plural:3}[/s]",
                    # "[that/those]" in Oseem 12580/12582); {index:n} picks by value instead.
                    try:
                        idx = int(tok[7:])
                    except ValueError:
                        raise DialogError(f"bad plural token: {{{tok}}} (use {{plural:n}}[a/b])")
                    out += bytes([0x7F, 0x92, idx & 0xFF]); i = end + 1; continue
                if tok.startswith("rowname:"):
                    # {rowname:n} -> 7F 80 01 01 05 23 82 (80|n) 80 80: an item name (id in
                    # parameter n) at the start of a query row, exactly as Oseem's stone rows
                    # and eligible-equipment rows carry it (12575, 12582).
                    try:
                        idx = int(tok[8:])
                    except ValueError:
                        raise DialogError(f"bad rowname token: {{{tok}}}")
                    if not 0 <= idx < 128:
                        raise DialogError(f"name token index out of range: {{{tok}}}")
                    out += bytes([0x7F, 0x80, 0x01, 0x01, 0x05, 0x23, 0x82, *_v7b(idx)]); i = end + 1; continue
                if tok.startswith("qtyitem:"):
                    # {qtyitem:c:i} -> 01 09 29 82 (80|c) 80 80 82 (80|i) 80 80: "<count> <item>"
                    # with the plural item name, count in parameter c and item id in parameter i
                    # ("I've got 4 pellucid stones in storage", Oseem 12579/12580).
                    parts = tok.split(":")
                    try:
                        cidx, iidx = int(parts[1]), int(parts[2])
                    except (IndexError, ValueError):
                        raise DialogError(f"bad qtyitem token: {{{tok}}} (use {{qtyitem:c:i}})")
                    out += bytes([0x01, 0x09, 0x29, 0x82, *_v7b(cidx), 0x82, *_v7b(iidx)]); i = end + 1; continue
                if tok.startswith("index:"):
                    # {index:n}[a/b/c] -> 0C nn followed by the literal bracket list: the client
                    # picks alternative number (parameter n) ("Select your {index:8}[first/second]
                    # augment", Tenshodo coffer 9940). The brackets stay literal text.
                    try:
                        idx = int(tok[6:])
                    except ValueError:
                        raise DialogError(f"bad index token: {{{tok}}} (use {{index:n}}[a/b])")
                    out += bytes([0x0C, idx & 0xFF]); i = end + 1; continue
                if tok.startswith("keyitem:") or tok.startswith("rowitem:"):
                    # Name-by-kind shorthands: {keyitem:n} = kind 0x36 (key item whose id is in
                    # parameter n; coffer menu 9931 rows), {rowitem:n} = kind 0x24 (item id in
                    # parameter n as the coffer's "Obtain which item?" rows use it).
                    kind = 0x36 if tok.startswith("keyitem:") else 0x24
                    try:
                        idx = int(tok.split(":")[1])
                    except (IndexError, ValueError):
                        raise DialogError(f"bad name token: {{{tok}}}")
                    if not 0 <= idx < (1 << 21):
                        raise DialogError(f"name token index out of range: {{{tok}}}")
                    out += bytes([0x01, 0x05, kind, 0x82, *_v7b(idx)]); i = end + 1; continue
                if tok.startswith("item:") or tok.startswith("name:"):
                    # Special-name substitution 01 05 <kind> 82 <0x80|n> 80 80: the name of the
                    # thing whose id sits in event parameter n. {item:n} = kind 0x25 (item name,
                    # Bonanza Moogle menu rows / synergy text); {name:KIND:n} for the other
                    # kinds seen in retail (0x23/0x24 item via row register, 0x36 key item,
                    # 0x38 zone, 0x84 entity name, 0x40 RoE objective) — verify in game.
                    parts = tok.split(":")
                    try:
                        if parts[0] == "item":
                            kind, idx = 0x25, int(parts[1])
                        else:
                            kind, idx = int(parts[1], 0), int(parts[2])
                    except (IndexError, ValueError):
                        raise DialogError(f"bad name token: {{{tok}}} (use {{item:n}} or {{name:0xKK:n}})")
                    if not 0 <= idx < (1 << 21):
                        raise DialogError(f"name token index out of range: {{{tok}}}")
                    out += bytes([0x01, 0x05, kind & 0xFF, 0x82, *_v7b(idx)]); i = end + 1; continue
                # unrecognised token: fall through and emit '{' literally
        try:
            out += c.encode("cp932")
        except UnicodeEncodeError:
            raise DialogError(f"character {c!r} cannot be encoded (not in Shift-JIS)")
        i += 1
    return bytes(out)


def replace_entry_text(blob: bytes, new_text: str) -> bytes:
    """Swap an entry's displayed (first) sub-string for ``new_text``, preserving
    the original terminator and any trailing variant sub-strings / padding."""
    nul = blob.find(0)
    tail = blob[nul:] if nul >= 0 else b"\x00"
    return encode_event_string(new_text) + tail


def opcode_histogram(entries: list[Entry]) -> dict[str, int]:
    """Count opcodes by friendly name across all entries (most useful first)."""
    counts: dict[str, int] = {}
    for e in entries:
        for op in e.opcodes:
            counts[op.name] = counts.get(op.name, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

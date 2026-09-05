"""Retail-shaped currency shop for custom NPCs.

The client already ships a complete "exchange N currency for item" page routine: the
Records of Eminence exchange (Isakoth, Bastok Markets, actor 0x010EB0B1). Its bytecode is
self-contained — category tables, paging (15 rows), cursor memory, a quantity menu with
the unit price multiplied in, the ``0x93`` item-description popup, the Yes/No confirm,
and the ``0x40``-packed ``category | qty << 10 | selection << 16`` result the server
receives on ``43 00 / 43 01``. The currency is only a number to the client: the balance
comes from a server parameter and the word "sparks" is text in four strings.

So instead of re-deriving that logic we CLONE it: read the routine from the player's own
Bastok Markets DATs (``ROM/21/44.DAT`` scene 0x1866..0x1e7b, the row-register table at
0x0d46..0x0d84 and strings 14376..14379 from ``ROM/25/44.DAT``), relocate every absolute
jump and table offset, remap every ``references[]`` selector into the target actor's table
(substituting our item ids, prices and string ids), and emit a small dispatcher in front of
it. Decoded from the retail bytes and the xi-tools
events docs.

Register contract (WorkLocal selectors) between the dispatcher and the routine:
  L[4]   balance            L[7]  loop flag (routine decrements on "None")
  L[8]   category-menu hide mask   L[24] category index 0..9
  L[27]  category-menu cursor      L[33] remaining limit ({34} in the page header)
The routine reads the new balance from Work_Zone[2] and the new limit from Work_Zone[7]
after the server's ``updateEvent`` (LSB: ``player:updateEvent(balance, 0, 0, 0, 0, limit)``).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from xi.dialog import xi_dialog
from xi.event import xi_event as core

TEMPLATE_ZONE = 235                       # Bastok Markets
TEMPLATE_EVENT_DAT = "ROM/21/44.DAT"
TEMPLATE_DIALOG_DAT = "ROM/25/44.DAT"
TEMPLATE_ACTOR = 0x010EB0B1               # Isakoth
ROUTINE_START, ROUTINE_END = 0x1866, 0x1E7B   # end exclusive; last opcode is 0x1B break_jump @0x1e7a
ROWTABLE_START, ROWTABLE_END = 0x0D46, 0x0D84 # 31 register selectors the page writes item ids into
TEMPLATE_STRINGS = {"page": 14376, "qty": 14377, "confirm": 14378, "yesno": 14379}
MAX_CATEGORIES = 10
ROWS_PER_PAGE = 15

# WorkLocal selectors used by the dispatcher/routine contract.
L_BALANCE, L_LOOP, L_MENU_FLAGS, L_CATEGORY, L_CURSOR, L_LIMIT = 0x0004, 0x0007, 0x0008, 0x0018, 0x001B, 0x0021
WZ_BALANCE_DISPLAY = 0x1002               # Work_Zone[2]  -> "{0}" in strings
WZ1700_LIMIT_DISPLAY = 0x171A             # Work_Zone_1700[26] -> "{34}"

# 0xD4 (query window) sizes by sub-opcode — XiEvents pseudo code: case 0/2 call the 0x24
# helper (op + sub + 3 selectors = 8), case 1 = 8, case 3 = 6, case 4/5 = 12.
D4_SIZES = {0x00: 8, 0x01: 8, 0x02: 8, 0x03: 6, 0x04: 12, 0x05: 12}

# Operand byte offsets (from the opcode byte) that hold u16 work-selectors, per opcode.
SELECTOR_FIELDS = {
    0x02: (1, 3), 0x03: (1, 3), 0x05: (1,), 0x06: (1,), 0x07: (1, 3), 0x08: (1, 3),
    0x09: (1, 3), 0x0B: (1,), 0x0C: (1,), 0x10: (1, 3), 0x14: (1, 3), 0x15: (1, 3),
    0x1C: (1,), 0x1D: (1,), 0x24: (1, 3, 5), 0x3C: (1, 3, 5), 0x3F: (1, 3, 5),
    0x40: (1, 3, 5, 7), 0x41: (1, 3, 5, 7), 0x48: (1,), 0x83: (1,), 0x93: (1,),
    0x9D: (4, 6),
    0x00: (), 0x01: (), 0x1A: (), 0x23: (), 0x25: (), 0x1B: (), 0x43: (),   # jumps carry offsets, not selectors
}
D4_SELECTOR_FIELDS = {0x02: (2, 4, 6), 0x03: (2, 4), 0x00: (2, 4, 6)}
# Which selector field of an opcode is a MESSAGE id (only those are substituted with our
# cloned strings; every other selector is a plain constant or register).
MESSAGE_FIELD = {0x1D: 1, 0x48: 1, 0x24: 1, 0x2B: 5, (0xD4, 0x00): 2, (0xD4, 0x02): 2}


class ShopTemplateError(Exception):
    pass


def opcode_size(op: int, sub: int) -> int:
    if op == 0xD4:
        return D4_SIZES.get(sub, 0)
    return core._opcode_size(op, sub)


@dataclass
class ShopTemplate:
    routine: bytes                               # scene[ROUTINE_START:ROUTINE_END]
    rowtable: bytes                              # scene[ROWTABLE_START:ROWTABLE_END]
    refs: list[int]                              # Isakoth's references[]
    strings: dict[str, bytes]                    # raw dialog blobs by role
    category_tables: list[tuple[int, int]]       # (items table offset, prices table offset) per category index
    ops: list[tuple[int, int, int]]              # (offset within routine, opcode, size)


def _u16(b: bytes, i: int) -> int:
    return struct.unpack_from("<H", b, i)[0]


def _parse_ops(routine: bytes) -> list[tuple[int, int, int]]:
    ops = []
    pos = 0
    while pos < len(routine):
        op = routine[pos]
        sub = routine[pos + 1] if pos + 1 < len(routine) else 0
        sz = opcode_size(op, sub)
        if not sz:
            raise ShopTemplateError(f"unknown opcode 0x{op:02x} at routine+0x{pos:04x}")
        if op == 0xD4 and sub not in D4_SELECTOR_FIELDS:
            raise ShopTemplateError(f"0xD4 sub 0x{sub:02x} at routine+0x{pos:04x} has no selector map")
        if op != 0xD4 and op not in SELECTOR_FIELDS:
            raise ShopTemplateError(f"opcode 0x{op:02x} at routine+0x{pos:04x} has no selector map")
        ops.append((pos, op, sz))
        pos += sz
    if not ops or ops[-1][1] != 0x1B:
        raise ShopTemplateError("template routine does not end with 0x1B break_jump")
    return ops


def _read_template_file(ffxi_dir: Path, rel: str) -> bytes:
    """Prefer the pristine ``.base`` copy when an edited DAT sits in the game folder."""
    p = ffxi_dir / rel
    base = Path(str(p) + ".base")
    return (base if base.exists() else p).read_bytes()


_CACHE: dict[str, ShopTemplate] = {}


def load_template(ffxi_dir: Path) -> ShopTemplate:
    key = str(ffxi_dir)
    if key in _CACHE:
        return _CACHE[key]
    event_dat = _read_template_file(ffxi_dir, TEMPLATE_EVENT_DAT)
    dialog_dat = _read_template_file(ffxi_dir, TEMPLATE_DIALOG_DAT)
    actor = next((a for a in core.parse_raw_actors(event_dat) if a.actor_id == TEMPLATE_ACTOR), None)
    if actor is None:
        raise ShopTemplateError(f"actor 0x{TEMPLATE_ACTOR:08X} (Isakoth) not found in {TEMPLATE_EVENT_DAT}")
    scene = bytes(actor.scene_data)
    refs = list(actor.references)
    routine = scene[ROUTINE_START:ROUTINE_END]
    ops = _parse_ops(routine)

    # Category → (items, prices) tables: the routine tests L[24] against a references[]
    # constant, then reads its tables with 0x9D sub 0 (items first, then prices).
    tables_by_cat: dict[int, list[int]] = {}
    cat: Optional[int] = None
    for off, op, sz in ops:
        if op == 0x02 and _u16(routine, off + 1) == L_CATEGORY:
            v = _u16(routine, off + 3)
            cat = refs[v & 0x7FFF] if v & 0x8000 else None
        elif op == 0x9D and routine[off + 1] == 0x00 and cat is not None:
            t = _u16(routine, off + 2)
            lst = tables_by_cat.setdefault(cat, [])
            if t not in lst:
                lst.append(t)
    category_tables = []
    for k in range(MAX_CATEGORIES):
        lst = tables_by_cat.get(k, [])
        if len(lst) != 2:
            raise ShopTemplateError(f"category {k}: expected 2 tables, found {lst}")
        category_tables.append((lst[0], lst[1]))

    blobs, _ = xi_dialog.raw_entry_blobs(dialog_dat)
    strings = {role: bytes(blobs[i]) for role, i in TEMPLATE_STRINGS.items()}
    if b"Sparks" not in strings["page"] or b"spark" not in strings["qty"]:
        raise ShopTemplateError("template strings do not look like the sparks exchange (edited dialog DAT?)")

    tpl = ShopTemplate(routine, scene[ROWTABLE_START:ROWTABLE_END], refs, strings, category_tables, ops)
    _CACHE[key] = tpl
    return tpl


def relocate_routine(tpl: ShopTemplate, new_base: int, sel_map: Callable[[int, bool], int],
                     table_map: dict[int, int]) -> bytes:
    """Copy the routine to ``new_base``: absolute jumps shifted, 0x9D table offsets mapped
    through ``table_map``, every references[] selector rewritten by ``sel_map``."""
    out = bytearray(tpl.routine)
    delta = new_base - ROUTINE_START

    def patch_offset(pos: int) -> None:
        t = _u16(out, pos)
        if not (ROUTINE_START <= t < ROUTINE_END):
            raise ShopTemplateError(f"jump target 0x{t:04x} at routine+0x{pos:04x} leaves the routine")
        struct.pack_into("<H", out, pos, t + delta)

    for off, op, sz in tpl.ops:
        sub = out[off + 1] if sz > 1 else 0
        if op in (0x01, 0x1A):
            patch_offset(off + 1)
        elif op == 0x02:
            patch_offset(off + 6)
        if op == 0x9D:
            t = _u16(out, off + 2)
            if t not in table_map:
                raise ShopTemplateError(f"0x9D at routine+0x{off:04x} reads unmapped table 0x{t:04x}")
            struct.pack_into("<H", out, off + 2, table_map[t])
        fields = D4_SELECTOR_FIELDS[sub] if op == 0xD4 else SELECTOR_FIELDS[op]
        msg_field = MESSAGE_FIELD.get((op, sub) if op == 0xD4 else op)
        for f in fields:
            v = _u16(out, off + f)
            if v & 0x8000:
                struct.pack_into("<H", out, off + f, sel_map(v, f == msg_field))
    return bytes(out)


def table_len(count: int) -> int:
    """Entries in a category table holding ``count`` items: the page routine reads row
    ``page * ROWS_PER_PAGE + row`` for every row of a page and hides rows whose item is
    0, so the table is padded with zero selectors to whole pages plus one trailing zero
    (the count loop stops on it). Isakoth's own tables are zero-padded the same way."""
    pages = max(1, -(-count // ROWS_PER_PAGE))
    return pages * ROWS_PER_PAGE + 1


def table_bytes(values: list[int], sel_of: Callable[[int], int]) -> bytes:
    """A 0x9D table: one u16 work-selector per value, zero-padded to :func:`table_len`."""
    padded = list(values) + [0] * (table_len(len(values)) - len(values))
    return b"".join(struct.pack("<H", sel_of(v)) for v in padded)


def _replace_all(blob: bytes, pairs: list[tuple[bytes, bytes]]) -> bytes:
    for old, new in pairs:
        blob = blob.replace(old, new)
    return blob


def shop_strings(tpl: ShopTemplate, name: str, singular: str, short: str, plural_s: bool = True,
                 overrides: Optional[dict] = None, label: Optional[str] = None) -> dict[str, bytes]:
    """The four retail strings with the currency words swapped. The page/quantity strings
    keep their binary placeholders (item-name codes ``01 05 …``, number codes ``0A nn``, the
    plural code ``7F 92 nn`` + ``[/s]``) — only ASCII words are replaced.

    ``overrides`` may give plain authoring text for ``confirm`` / ``yesno`` (they only use
    ``{0}``); the page and quantity strings are always cloned."""
    enc = lambda s: s.encode("cp932")
    out = {}
    out["page"] = _replace_all(tpl.strings["page"], [(b"Sparks", enc(label or name)), (b"Spa.", enc(short))])
    qty = tpl.strings["qty"]
    confirm = _replace_all(tpl.strings["confirm"], [(b" of eminence", b"")])
    for idx in range(0, 16):
        pat = b"spark\x7f\x92" + bytes([idx]) + b"[/s]"
        rep = (enc(singular) + b"\x7f\x92" + bytes([idx]) + b"[/s]") if plural_s else enc(name)
        qty = qty.replace(pat, rep)
        confirm = confirm.replace(pat, rep)
    out["qty"] = qty
    out["confirm"] = confirm
    out["yesno"] = tpl.strings["yesno"]
    overrides = overrides or {}
    for role in ("confirm", "yesno"):
        if overrides.get(role):
            out[role] = xi_dialog.encode_event_string(str(overrides[role])) + b"\x7f\x31\x00"
    return out


def category_menu_blob(question: str, currency_label: str, categories: list[dict], none_text: str) -> bytes:
    rows = [str(c["name"]) for c in categories] + [none_text]
    # question, newline, 0x0B "options start" marker, then the option rows (retail layout)
    text = f"{question} ({currency_label}: {{0}})" + "\\n" + "\x0b" + "\\n".join(rows)
    return xi_dialog.encode_event_string(text) + b"\x7f\x31\x00"


def lua_stub(step: dict, event_id: int) -> str:
    """Server side for the shop (LandSandBoat): mirrors sparkshop.lua's option decoding."""
    cur = step.get("currency") or {}
    cname = cur.get("server", cur.get("name", "gil"))
    lines = [f"-- ─── Shop event {event_id}: server side ───",
             "-- balance param = " + str(step.get("balanceParam", 1)) +
             ", limit param = " + str(step.get("limitParam", "none")) +
             f"; currency '{cname}' (player:getCurrency name, or 'gil')",
             "local SHOP = {"]
    for k, c in enumerate(step["categories"]):
        lines.append(f"    [{k + 1}] = {{ -- {c['name']}")
        for i, it in enumerate(c["items"]):
            lines.append(f"        [{i}] = {{ id = {int(it['id'])}, cost = {int(it['price'])} }},")
        lines.append("    },")
    lines.append("}")
    lines += [
        "",
        "local function balance(player)",
        f"    return {'player:getGil()' if cname == 'gil' else f'player:getCurrency({cname!r})'}",
        "end",
        "",
        "local function charge(player, amount)",
        f"    {'player:delGil(amount)' if cname == 'gil' else f'player:delCurrency({cname!r}, amount)'}",
        "end",
        "",
        "function onTrigger(player, npc)",
        "    -- p0 unused (greeting selector), p1 balance, p5 remaining limit (large number = no limit)",
        f"    player:startEvent({event_id}, 0, balance(player), 0, 0, 0, 999999)",
        "end",
        "",
        "function onEventUpdate(player, csid, option)",
        f"    if csid ~= {event_id} then return end",
        "    local category  = bit.band(option, 0xFF)",
        "    local qty       = bit.band(bit.rshift(option, 10), 0x3F)",
        "    local selection = bit.rshift(option, 16)",
        "    local entry     = SHOP[category] and SHOP[category][selection]",
        "    if entry and qty > 0 then",
        "        local cost = entry.cost * qty",
        "        if balance(player) >= cost and npcUtil.giveItem(player, { { entry.id, qty } }) then",
        "            charge(player, cost)",
        "        end",
        "    end",
        "    player:updateEvent(balance(player), 0, 0, 0, 0, 999999)",
        "end",
    ]
    return "\n".join(lines) + "\n"

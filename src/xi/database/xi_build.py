"""Building a ``database`` action (schema/database.json): the records it names are patched
in the DATs of the build's root, and the proposed SQL for their server rows is written
beside the project.

Every build starts from the records as they were before this action touched them: what a
previous build of the action changed in this root is put back first (from the values its
``result`` recorded), then the edits are applied. So a rebuild converges, an edit removed
from the action goes back to what it was, and a ``replace`` text edit always applies to the
original text. A file is written only when its bytes changed.

Item records: an item DAT holds fixed-size records (0xC00 legacy / 0x1400 retail, detected
per file), each a typed header, a string block and the icon (xi.ui.items.xi_layout). d_msg
records: a block of a string table (xi.common.xi_dmsg). Both string blocks share one form —
``n``, n x (offset, flag), then the sub-strings; flag 0 is text (u32 1, 0x18 meta bytes,
NUL-terminated cp932), flag 1 a number (u32). The flag says which, not the value: key item 1
stores the number 1.
"""
from __future__ import annotations

import base64
import difflib
import hashlib
import re
import struct
from pathlib import Path

from xi.common import xi_dmsg as D
from xi.database import xi_core as C
from xi.mv.xi_database import DMSG_TABLES, _FIELD_KEYS
from xi.ui.items.xi_layout import ICON_DATA, ICON_OFFSET, find_text_offset, read_field, text_offset, write_field
from xi.ui.items.xi_parser import ItemDat


class DbError(ValueError):
    pass


# ── registry ─────────────────────────────────────────────────────────────────

# Item tables: (EN DAT, JP DAT, id of the DAT's first record, lowest id, highest id) —
# the viewer's registry (ui/js/database.js ITEM_TABLES).
ITEM_PARTS = {
    "general": [("ROM/118/106.DAT", "ROM/0/4.DAT", 0, 0, 4095),
                ("ROM/301/115.DAT", "ROM/301/114.DAT", 8704, 8704, 10239)],
    "usable": [("ROM/118/107.DAT", "ROM/0/5.DAT", 4096, 4096, 8191)],
    "puppet": [("ROM/118/110.DAT", "ROM/0/8.DAT", 8192, 8192, 8703)],
    "armor": [("ROM/118/109.DAT", "ROM/0/7.DAT", 10240, 10240, 16383),
              ("ROM/286/73.DAT", "ROM/286/72.DAT", 23040, 23040, 28671)],
    "weapons": [("ROM/118/108.DAT", "ROM/0/6.DAT", 16384, 16384, 23039)],
    "maze": [("ROM/217/21.DAT", "ROM/217/20.DAT", 28672, 28672, 29695)],
    "monst1": [("ROM/288/80.DAT", "ROM/288/79.DAT", 29696, 29696, 30719)],
    "items7": [("ROM/387/14.DAT", "ROM/387/13.DAT", 30720, 30720, 31743)],
    "roeObj": [("ROM/307/16.DAT", "ROM/307/15.DAT", 57344, 57344, 61431)],
    "items6": [("ROM/332/48.DAT", "ROM/332/46.DAT", 63024, 63024, 63263)],
    "gil": [("ROM/174/48.DAT", "ROM/0/9.DAT", 65535, 65535, 65535)],
}
# CatsEyeXI's cexidats band: ROM/288/79-80 grown past Monstrosity's 1,024 records, each
# sub-range holding records in its own item layout (dats repo README, "Custom_Items").
BAND_DAT = ("ROM/288/80.DAT", "ROM/288/79.DAT", 29696)
BAND = {"general": [(30720, 34815), (55296, 57343)], "usable": [(34816, 38911)],
        "armor": [(38912, 47103)], "weapons": [(47104, 55295)]}
BAND_FIRST = 30720          # a 288/80 holding records past this carries the band

DMSG_PARTS = {key: (en, jp) for key, en, jp in DMSG_TABLES}
LAYOUT_NAMES = {v: k for k, v in _FIELD_KEYS.items()}     # viewer field name -> xi_layout name
_MASKS = {"flags": C.flag_mask, "races": C.race_mask, "slots": C.slot_mask}


def is_item_table(table: str) -> bool:
    return table in C.ITEM_LAYOUT


# ── text ─────────────────────────────────────────────────────────────────────
# The client's text is cp932 plus a few codes that aren't: 0xEF 0x1F-0x26 draws an
# element icon, 0xFD .. 0xFD an auto-translate phrase. They read and write as {Fire} …
# {Dark}, {at:xxxxxxxx}, and any other byte cp932 can't show as {x:hh}, so text read
# from a record writes back byte for byte.

ELEMENTS = ["Fire", "Ice", "Wind", "Earth", "Lightning", "Water", "Light", "Dark"]
_TOKEN = re.compile(r"\{(" + "|".join(ELEMENTS) + r"|at:[0-9a-f]{8}|x:(?:[0-9a-f]{2})+)\}")


def decode_text(b: bytes) -> str:
    out, i, n = [], 0, len(b)
    while i < n:
        c = b[i]
        if c == 0xEF and i + 1 < n and 0x1F <= b[i + 1] <= 0x26:
            out.append("{" + ELEMENTS[b[i + 1] - 0x1F] + "}")
            i += 2
            continue
        if c == 0xFD and i + 5 < n and b[i + 5] == 0xFD:
            out.append("{at:" + b[i + 1:i + 5].hex() + "}")
            i += 6
            continue
        step = 2 if (0x81 <= c <= 0x9F or 0xE0 <= c <= 0xFC) and i + 1 < n else 1
        chunk = b[i:i + step]
        try:
            s = chunk.decode("cp932")
        except UnicodeDecodeError:
            s = None
        out.append("{x:" + chunk.hex() + "}" if s is None or s == "{" else s)
        i += step
    return "".join(out)


def encode_text(s: str) -> bytes:
    out, pos = bytearray(), 0
    for m in _TOKEN.finditer(s):
        out += _cp932(s[pos:m.start()])
        tok = m.group(1)
        if tok in ELEMENTS:
            out += bytes([0xEF, 0x1F + ELEMENTS.index(tok)])
        elif tok.startswith("at:"):
            out += b"\xfd" + bytes.fromhex(tok[3:]) + b"\xfd"
        else:
            out += bytes.fromhex(tok[2:])
        pos = m.end()
    out += _cp932(s[pos:])
    return bytes(out)


def _cp932(s: str) -> bytes:
    try:
        return s.encode("cp932")
    except UnicodeEncodeError as e:
        raise DbError(f"{s[e.start:e.end]!r} can't be shown by the client (text must be cp932 / Shift-JIS)")


# ── string blocks ────────────────────────────────────────────────────────────

def parse_subs(buf, off: int, limit: int) -> tuple[list[dict], int]:
    """The sub-strings of the string block at ``off`` as ``[{flag, raw}]``, and the
    offset its last sub-string ends at. Lengths come from the flag, never the value."""
    n = struct.unpack_from("<I", buf, off)[0]
    if not 1 <= n <= 32 or off + 4 + 8 * n > limit:
        raise DbError(f"no string block at {off:#x}")
    subs, end = [], off + 4 + 8 * n
    for j in range(n):
        o, flag = struct.unpack_from("<II", buf, off + 4 + 8 * j)
        p = off + o
        if flag == 0:
            s = p + 4 + D.META_LEN
            try:
                e = bytes(buf[s:limit]).index(0) + s
            except ValueError:
                e = limit - 1
            length = (e + 1 - p + 3) & ~3
        else:
            length = 4
        subs.append({"flag": flag, "raw": bytes(buf[p:p + length])})
        end = max(end, p + length)
    return subs, end


def sub_value(sub: dict):
    if sub["flag"] == 0:
        body = sub["raw"][4 + D.META_LEN:]
        return decode_text(body.split(b"\0", 1)[0])
    return struct.unpack_from("<I", sub["raw"])[0]


def set_sub(sub: dict, value) -> None:
    if sub["flag"] == 0:
        body = encode_text(value) + b"\0"
        body += b"\0" * (-len(body) % 4)
        sub["raw"] = struct.pack("<I", 1) + (sub["raw"][4:4 + D.META_LEN] or bytes(D.META_LEN)) + body
    else:
        sub["raw"] = struct.pack("<I", int(value) & 0xFFFFFFFF)


def _new_text(spec, base: str) -> str:
    """The value a string edit asks for: the text, or ``{"replace": {old: new}}`` on ``base``."""
    if isinstance(spec, dict):
        out = base
        for old, new in spec["replace"].items():
            if old not in out:
                raise DbError(f"{old!r} isn't in {base!r}")
            out = out.replace(old, new)
        return out
    return spec


# ── files in the build root ──────────────────────────────────────────────────

class _Files:
    """The DATs one build reads and writes, each loaded once from the copy ``root``
    reads (its own, else the install's — xi.menu.xi_menu_table's rules)."""

    def __init__(self, root: Path):
        self.root = root
        self.items: dict[str, ItemDat | None] = {}
        self.dmsg: dict[str, D.DmsgTable | None] = {}
        self.orig: dict[str, bytes] = {}

    def item(self, rel: str) -> ItemDat | None:
        if rel not in self.items:
            from xi.menu.xi_menu_table import dat_path
            p = dat_path(self.root, rel)
            self.items[rel] = ItemDat.load(p) if p.is_file() else None
            if self.items[rel] is not None:
                self.orig[rel] = bytes(self.items[rel].data)
        return self.items[rel]

    def table(self, rel: str) -> D.DmsgTable | None:
        if rel not in self.dmsg:
            from xi.menu.xi_menu_table import dat_path
            p = dat_path(self.root, rel)
            if not p.is_file():
                self.dmsg[rel] = None
            else:
                data = p.read_bytes()
                try:
                    self.dmsg[rel] = D.parse(data)
                except D.DmsgError as e:
                    raise DbError(f"{rel}: {e}")
                self.orig[rel] = data
        return self.dmsg[rel]

    def changed(self) -> list[tuple[str, bytes]]:
        out = []
        for rel, dat in self.items.items():
            if dat is not None and bytes(dat.data) != self.orig[rel]:
                out.append((rel, dat.encrypted()))
        for rel, t in self.dmsg.items():
            if t is not None:
                data = D.serialize(t)
                if data != self.orig[rel]:
                    out.append((rel, data))
        return out


# ── item records ─────────────────────────────────────────────────────────────

def _parts(files: _Files, table: str) -> list[tuple]:
    parts = list(ITEM_PARTS[table])
    en, jp, base = BAND_DAT
    dat = files.item(en) if table in BAND else None
    if dat is not None and base + dat.count > BAND_FIRST:
        parts += [(en, jp, base, lo, hi) for lo, hi in BAND[table]]
    return parts


def locate_item(files: _Files, table: str, item_id: int) -> tuple[str, str, int]:
    """``(EN DAT, JP DAT, record index)`` of item ``item_id`` in ``table``."""
    ranges = []
    for en, jp, base, lo, hi in _parts(files, table):
        ranges.append(f"{lo}-{hi}")
        if not lo <= item_id <= hi:
            continue
        dat = files.item(en)
        if dat is None:
            raise DbError(f"{table} {item_id} lives in {en}, which this install doesn't have")
        idx = item_id - base
        if idx >= dat.count:
            raise DbError(f"{en} holds {dat.count} records; {table} {item_id} is past its end")
        return en, jp, idx
    raise DbError(f"{table} has no id {item_id} (ids {', '.join(ranges)})")


def _layout(table: str) -> str:
    return C.ITEM_LAYOUT[table]


def item_strings(rec: bytes, layout: str, fmt: str) -> tuple[int, list[dict], int] | None:
    off = find_text_offset(rec, text_offset(layout, fmt))
    if off is None:
        return None
    try:
        subs, end = parse_subs(rec, off, ICON_OFFSET)
    except DbError:
        return None
    return off, subs, end


def item_name(rec: bytes, layout: str, fmt: str) -> str:
    s = item_strings(rec, layout, fmt)
    if not s or not s[1] or s[1][0]["flag"] != 0:
        return ""
    return sub_value(s[1][0])


def is_empty_item(rec: bytes, layout: str, fmt: str) -> bool:
    name = item_name(rec, layout, fmt).strip()
    return not name or set(name) == {"."}


def _write_item_strings(rec: bytearray, off: int, subs: list[dict], old_end: int) -> None:
    block = D._assemble_block(subs, 0)
    if off + len(block) > ICON_OFFSET:
        raise DbError(f"the text is {off + len(block) - ICON_OFFSET} bytes too long for the record "
                      f"(it must end before the icon at {ICON_OFFSET:#x})")
    rec[off:off + len(block)] = block
    if old_end > off + len(block):
        rec[off + len(block):old_end] = bytes(old_end - off - len(block))


def _field_value(name: str, value) -> int:
    if name in _MASKS:
        return _MASKS[name](value)
    if name == "jobs":
        return C.job_mask(value, "dat")
    if name == "skill":
        return C.skill_id(value)
    return int(value)


def _sha1(b: bytes) -> str:
    return hashlib.sha1(bytes(b)).hexdigest()


def _icon_region(rec) -> bytes:
    return bytes(rec[ICON_OFFSET:len(rec) - 1])


def _item_icon(files: _Files, item_id: int, fmt: str) -> bytes:
    for table in C.ITEM_LAYOUT:
        try:
            en, _jp, idx = locate_item(files, table, item_id)
        except DbError:
            continue
        dat = files.item(en)
        if dat.format != fmt:
            raise DbError(f"icon from {item_id}: {en} is {dat.format}-format, the record is {fmt}")
        rec = dat.record(idx)
        size = struct.unpack_from("<I", rec, ICON_OFFSET)[0]
        if not size or ICON_DATA + size > len(rec) - 1:
            raise DbError(f"item {item_id} has no icon to copy")
        return rec[ICON_OFFSET:ICON_DATA + size]
    raise DbError(f"icon from {item_id}: no item table has that id")


def _apply_item(files: _Files, edit: dict, lang: str, rec: bytearray, layout: str, fmt: str,
                base_texts: dict) -> dict:
    """Apply ``edit`` to one language's record; ``{field: {from, to}}`` of what changed."""
    changed: dict = {}
    values = dict(edit.get("set") or {})
    if layout == "weapon" and ({"damage", "delay"} & values.keys()) and "dps" not in values:
        dmg = values.get("damage", read_field(rec, layout, fmt, "dmg"))
        delay = values.get("delay", read_field(rec, layout, fmt, "delay"))
        if delay:
            values["dps"] = int(dmg) * 6000 // int(delay)
    for name, value in values.items():
        lname = LAYOUT_NAMES.get(name, name)
        old = read_field(rec, layout, fmt, lname)
        new = _field_value(name, value)
        if old != new:
            write_field(rec, layout, fmt, lname, new)
            changed[name] = {"from": old, "to": read_field(rec, layout, fmt, lname)}
    spec = dict(((edit.get("strings") or {}).get(lang)) or {})
    if lang == "jp" and "like" in edit and not spec:
        # A new record shouldn't show its donor's Japanese name: carry the English text over.
        en = (edit.get("strings") or {}).get("en") or {}
        spec = {k: en[k] for k in ("name", "description") if isinstance(en.get(k), str)}
    if spec:
        found = item_strings(rec, layout, fmt)
        if found is None:
            raise DbError("the record has no string block to edit")
        off, subs, end = found
        names = C.ITEM_STRINGS[lang]
        touched = False
        for key, want in spec.items():
            i = names.index(key)
            if i >= len(subs):
                raise DbError(f"the record has no {lang} {key!r} (it has {len(subs)} strings)")
            old = sub_value(subs[i])
            new = _new_text(want, base_texts.get(key, old)) if subs[i]["flag"] == 0 else int(want)
            if old != new:
                set_sub(subs[i], new)
                changed[f"strings.{key}"] = {"from": old, "to": new}
                touched = True
        if touched:
            _write_item_strings(rec, off, subs, end)
    icon = edit.get("icon")
    if icon and "from" in icon:
        blob = _item_icon(files, icon["from"], fmt)
        before = _icon_region(rec)
        region = blob + bytes(len(before) - len(blob))
        if region != before:
            rec[ICON_OFFSET:len(rec) - 1] = region
            changed["icon"] = {"from": base64.b64encode(before.rstrip(b"\0")).decode("ascii"),
                               "to": f"item {icon['from']}", "sha1": _sha1(region)}
    return changed


def _restore_item(rec: bytearray, layout: str, fmt: str, entry: dict, warnings: list, label: str) -> None:
    """Put back what ``entry`` (a recorded edit) changed, field by field — only where the
    record still holds what the build wrote."""
    strings = None
    for key, ch in (entry.get("changed") or {}).items():
        if key == "icon":
            if _sha1(_icon_region(rec)) != ch.get("sha1"):
                warnings.append(f"{label}: the icon changed since the last build; left as it is")
                continue
            old = base64.b64decode(ch["from"])
            rec[ICON_OFFSET:len(rec) - 1] = old + bytes(len(rec) - 1 - ICON_OFFSET - len(old))
            continue
        if key.startswith("strings."):
            strings = strings or item_strings(rec, layout, fmt)
            if strings is None:
                warnings.append(f"{label}: its string block is gone; {key} left as it is")
                continue
            name = key.split(".", 1)[1]
            names = C.ITEM_STRINGS[entry["lang"]]
            sub = strings[1][names.index(name)]
            if sub_value(sub) != ch["to"]:
                warnings.append(f"{label}: {name} changed since the last build; left as it is")
                continue
            set_sub(sub, ch["from"])
            continue
        lname = LAYOUT_NAMES.get(key, key)
        if read_field(rec, layout, fmt, lname) != ch["to"]:
            warnings.append(f"{label}: {key} changed since the last build; left as it is")
            continue
        write_field(rec, layout, fmt, lname, ch["from"])
    if strings is not None:
        _write_item_strings(rec, strings[0], strings[1], strings[2])


# ── d_msg records ────────────────────────────────────────────────────────────

def _block_subs(block) -> list[dict]:
    return parse_subs(block, 0, len(block))[0]


def locate_block(t: D.DmsgTable, table: str, rid: int) -> int | None:
    """Block index of record ``rid``: the block whose id sub-string is ``rid`` (key items,
    quest and mission logs), else the row index."""
    if table in C.ID_KEYED:
        for i, b in enumerate(t.blocks):
            try:
                subs = _block_subs(b)
            except DbError:
                continue
            if subs and subs[0]["flag"] == 1 and sub_value(subs[0]) == rid:
                return i
        return None
    return rid if rid < t.num else None


def _template_block(t: D.DmsgTable, n: int) -> bytearray:
    for b in t.blocks:
        try:
            if len(_block_subs(b)) == n:
                return b
        except DbError:
            continue
    raise DbError("the table has no record to copy the shape of a new one from")


def _apply_block(t: D.DmsgTable, idx: int, table: str, lang: str, spec: dict, base_texts: dict) -> dict:
    subs = _block_subs(t.blocks[idx])
    names = C.DMSG_SUBS[table]
    changed = {}
    for key, want in spec.items():
        i = names.index(key)
        if i >= len(subs):
            raise DbError(f"record has no {key!r} (it has {len(subs)} strings)")
        old = sub_value(subs[i])
        new = _new_text(want, base_texts.get(key, old)) if subs[i]["flag"] == 0 else int(want)
        if old != new:
            set_sub(subs[i], new)
            changed[f"strings.{key}"] = {"from": old, "to": new}
    if not changed:
        return changed
    try:
        t.blocks[idx] = bytearray(D._assemble_block(subs, t.stride))
    except D.DmsgError as e:
        raise DbError(f"the text doesn't fit the table's {t.stride}-byte records ({e})")
    return changed


def _restore_block(t: D.DmsgTable, idx: int, table: str, entry: dict, warnings: list, label: str) -> None:
    subs = _block_subs(t.blocks[idx])
    names = C.DMSG_SUBS[table]
    for key, ch in (entry.get("changed") or {}).items():
        sub = subs[names.index(key.split(".", 1)[1])]
        if sub_value(sub) != ch["to"]:
            warnings.append(f"{label}: {key.split('.', 1)[1]} changed since the last build; left as it is")
            continue
        set_sub(sub, ch["from"])
    t.blocks[idx] = bytearray(D._assemble_block(subs, t.stride))


# ── build / undo ─────────────────────────────────────────────────────────────

def _label(table: str, rid: int, lang: str) -> str:
    return f"{table} {rid} ({lang})"


def _restore(files: _Files, entry: dict, warnings: list) -> None:
    """Undo one recorded edit in ``files`` (in memory)."""
    table, rid, lang = entry["table"], entry["id"], entry["lang"]
    label = _label(table, rid, lang)
    if is_item_table(table):
        dat = files.item(entry["dat"])
        if dat is None:
            warnings.append(f"{label}: {entry['dat']} is gone; nothing to put back")
            return
        if dat.format != entry.get("format", dat.format):
            warnings.append(f"{label}: {entry['dat']} is {dat.format}-format now; left as it is")
            return
        rec = bytearray(dat.record(entry["block"]))
        if entry.get("created"):
            if entry.get("sha1") and _sha1(rec) != entry["sha1"]:
                warnings.append(f"{label}: the record changed since the last build; left as it is")
                return
            dat.set_record(entry["block"], base64.b64decode(entry["before_block"]))
            return
        _restore_item(rec, _layout(table), dat.format, entry, warnings, label)
        dat.set_record(entry["block"], bytes(rec))
        return
    t = files.table(entry["dat"])
    idx = locate_block(t, table, rid) if t is not None else None
    if idx is None:
        warnings.append(f"{label}: record not found in {entry['dat']}; nothing to put back")
        return
    if entry.get("created"):
        del t.blocks[idx]
        return
    _restore_block(t, idx, table, entry, warnings, label)


def _base_texts(prev: dict, table: str, rid: int, lang: str) -> dict:
    """Original text of each sub-string a previous build changed (for ``replace``)."""
    for e in prev:
        if (e["table"], e["id"], e["lang"]) == (table, rid, lang):
            return {k.split(".", 1)[1]: v["from"] for k, v in (e.get("changed") or {}).items()
                    if k.startswith("strings.")}
    return {}


def _item_edit(files: _Files, edit: dict, prev: list, force: bool) -> list[dict]:
    table, rid = edit["table"], edit["id"]
    layout = _layout(table)
    en, jp, idx = locate_item(files, table, rid)
    donor = locate_item(files, table, edit["like"]) if "like" in edit else None
    out = []
    for lang, rel in (("en", en), ("jp", jp)):
        dat = files.item(rel)
        if dat is None:
            if lang == "en":
                raise DbError(f"{rel} is missing")
            continue
        fmt = dat.format
        rec = bytearray(dat.record(idx))
        entry = {"table": table, "id": rid, "lang": lang, "dat": rel, "format": fmt, "block": idx}
        if donor is not None:
            ddat = files.item(donor[0] if lang == "en" else donor[1])
            if ddat is None or ddat.format != fmt:
                raise DbError(f"{table} {edit['like']} ({lang}) isn't in a {fmt}-format DAT like {rid}")
            if not is_empty_item(rec, layout, fmt) and not force:
                raise DbError(f"{table} {rid} already holds {item_name(rec, layout, fmt)!r}; "
                              "pick an empty id or pass --force")
            entry["created"] = True
            entry["before_block"] = base64.b64encode(bytes(rec)).decode("ascii")
            rec = bytearray(ddat.record(donor[2]))
            struct.pack_into("<I", rec, 0, rid)
        elif is_empty_item(rec, layout, fmt):
            raise DbError(f"{table} {rid} is an empty slot; give like: <id> to create a record there")
        entry["name"] = item_name(rec, layout, fmt)
        entry["changed"] = _apply_item(files, edit, lang, rec, layout, fmt, _base_texts(prev, table, rid, lang))
        entry["name"] = item_name(rec, layout, fmt) or entry["name"]
        dat.set_record(idx, bytes(rec))
        if entry.get("created"):
            entry["sha1"] = _sha1(rec)          # what undo checks is still there
        if entry["changed"] or entry.get("created"):
            out.append(entry)
    return out


def _dmsg_edit(files: _Files, edit: dict, prev: list) -> list[dict]:
    table, rid = edit["table"], edit["id"]
    en, jp = DMSG_PARTS[table]
    out = []
    for lang, rel in (("en", en), ("jp", jp)):
        spec = ((edit.get("strings") or {}).get(lang)) or {}
        if not spec:
            continue
        t = files.table(rel)
        if t is None:
            raise DbError(f"{rel} is missing")
        idx = locate_block(t, table, rid)
        created = False
        if idx is None:
            if table not in C.ID_KEYED:
                raise DbError(f"{table} has no row {rid} ({t.num} rows)")
            template = _block_subs(_template_block(t, len(C.DMSG_SUBS[table])))
            set_sub(template[0], rid)
            for s in template[1:]:
                set_sub(s, "" if s["flag"] == 0 else 0)
            t.blocks.append(bytearray(D._assemble_block(template, t.stride)))
            idx, created = t.num - 1, True
        entry = {"table": table, "id": rid, "lang": lang, "dat": rel, "block": idx}
        entry["changed"] = _apply_block(t, idx, table, lang, spec, _base_texts(prev, table, rid, lang))
        subs = _block_subs(t.blocks[idx])
        names = C.DMSG_SUBS[table]
        name_i = names.index("name") if "name" in names else (1 if len(names) > 1 else 0)
        entry["name"] = sub_value(subs[name_i]) if name_i < len(subs) and subs[name_i]["flag"] == 0 else ""
        if created:
            entry["created"] = True
        if entry["changed"] or created:
            out.append(entry)
    return out


def _pivot_shadow(root: Path, target: str | None, rels: list[str]) -> list[str]:
    """DATs a base-install build edits that the pivot folder has its own copy of (the one
    the client loads)."""
    from xi.xi_config import FFXI_PIVOT_DIR
    if target != "dir" or not FFXI_PIVOT_DIR or Path(FFXI_PIVOT_DIR).resolve() == Path(root).resolve():
        return []
    return [r for r in rels if (Path(FFXI_PIVOT_DIR) / Path(*r.split("/"))).is_file()]


def build(action: dict, *, root: Path, target: str | None, manifest: dict, sql_path: Path | None,
          project: str, force: bool = False, dry_run: bool = False) -> dict:
    """Apply ``action`` to the DATs of ``root``; the build result (``records`` is what
    gets recorded for this root)."""
    errs = C.validate_action(action)
    if errs:
        raise DbError("; ".join(errs))
    prev = (((action.get("result") or {}).get("roots") or {}).get(target)) or []
    files = _Files(Path(root))
    warnings: list[str] = []
    for entry in reversed(prev):
        _restore(files, entry, warnings)
    records: list[dict] = []
    for i, edit in enumerate(action["edits"]):
        try:
            if is_item_table(edit["table"]):
                records += _item_edit(files, edit, prev, force)
            else:
                records += _dmsg_edit(files, edit, prev)
        except DbError as e:
            raise DbError(f"edits[{i}] ({edit['table']} {edit['id']}): {e}") from None
    # The SQL before anything is written: a mod name it can't resolve stops the build clean.
    sql = proposed_sql(action, manifest, records) if (action.get("server") or {}).get("emit", True) else ""
    changed = files.changed()
    written = []
    if not dry_run:
        from xi.menu.xi_menu_table import _write
        for rel, data in changed:
            written.append(str(_write(root, rel, data)))
    warnings += [f"the pivot folder has its own {rel}, which the client loads instead of this one — "
                 "build with --pivot to change what players see" for rel in _pivot_shadow(root, target,
                                                                                       [r for r, _ in changed])]
    if sql and sql_path is not None and not dry_run:
        write_sql_section(sql_path, action["id"], sql, project)
    return {"id": action["id"], "type": "database", "target": target, "records": records,
            "files": [r for r, _ in changed], "written": written, "warnings": warnings,
            "sql": sql or None, "server": str(sql_path) if sql and sql_path is not None else None}


def undo(action: dict, root: Path, target: str, dry_run: bool = False) -> tuple[int, list[str]]:
    """Put back every record ``action`` changed in ``root``; ``(records restored, warnings)``."""
    entries = (((action.get("result") or {}).get("roots") or {}).get(target)) or []
    files = _Files(Path(root))
    warnings: list[str] = []
    for entry in reversed(entries):
        _restore(files, entry, warnings)
    if not dry_run:
        from xi.menu.xi_menu_table import _write
        for rel, data in files.changed():
            _write(root, rel, data)
    return len(entries), warnings


# ── the proposed SQL ─────────────────────────────────────────────────────────

_ID_COL = {"item_basic": "itemid", "item_equipment": "itemId", "item_weapon": "itemId", "item_usable": "itemid",
           "item_furnishing": "itemid", "item_mods": "itemId", "item_mods_pet": "itemId",
           "item_latents": "itemId", "item_info": "itemid"}
_COLUMNS = {
    "item_basic": ["itemid", "subid", "name", "sortname", "type", "stackSize", "flags", "aH", "BaseSell"],
    "item_equipment": ["itemId", "name", "level", "ilevel", "jobs", "MId", "shieldSize", "scriptType", "slot",
                       "rslot", "rslotlook", "su_level"],
    "item_weapon": ["itemId", "name", "skill", "subskill", "ilvl_skill", "ilvl_parry", "ilvl_macc", "dmgType",
                    "hit", "delay", "dmg", "unlock_points"],
    "item_usable": ["itemid", "name", "validTargets", "activation", "animation", "animationTime", "maxCharges",
                    "useDelay", "reuseDelay", "aoe"],
    "item_furnishing": ["itemid", "name", "storage", "moghancement", "element", "aura"],
    "item_mods": ["itemId", "modId", "value"],
    "item_mods_pet": ["itemId", "modId", "value", "petType"],
    "item_latents": ["itemId", "modId", "value", "latentId", "latentParam"],
}
_ROW_TABLES = ["item_basic", "item_equipment", "item_weapon", "item_usable", "item_furnishing"]
# The server tables that hold rows for an item of each client table (what a `like` copies).
_SERVER_TABLES = {
    "armor": ["item_basic", "item_equipment", "item_mods", "item_mods_pet", "item_latents"],
    "weapons": ["item_basic", "item_equipment", "item_weapon", "item_mods", "item_mods_pet", "item_latents"],
    "usable": ["item_basic", "item_usable"],
    "general": ["item_basic", "item_furnishing"],
}
_LIST_KEYS = {"modId": "mod", "value": "value", "latentId": "latentId", "latentParam": "latentParam",
              "petType": "petType"}
# viewer field -> (server table, column): the fields both sides hold.
MIRROR = {"flags": ("item_basic", "flags"), "stack": ("item_basic", "stackSize"),
          "level": ("item_equipment", "level"), "itemLevel": ("item_equipment", "ilevel"),
          "superiorLevel": ("item_equipment", "su_level"), "jobs": ("item_equipment", "jobs"),
          "slots": ("item_equipment", "slot"), "shieldSize": ("item_equipment", "shieldSize"),
          "damage": ("item_weapon", "dmg"), "delay": ("item_weapon", "delay"), "skill": ("item_weapon", "skill")}

_mods_cache: dict = {}


def mod_ids(server_dir: str | None = None) -> dict[str, int]:
    """``xi.mod`` names -> ids from the server's scripts/enum/mod.lua (XI_SERVER_DIR)."""
    if server_dir is None:
        from xi.server.xi_step import current_server_dir
        server_dir = current_server_dir()
    if not server_dir:
        raise DbError("item_mods needs the server's mod names: set XI_SERVER_DIR in .env")
    path = Path(server_dir) / "scripts" / "enum" / "mod.lua"
    if path not in _mods_cache:
        if not path.is_file():
            raise DbError(f"item_mods needs {path}, which isn't there")
        text = path.read_text(encoding="utf-8", errors="replace")
        body = text[text.find("xi.mod"):]
        _mods_cache[path] = {m.group(1): int(m.group(2))
                             for m in re.finditer(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(\d+)", body, re.M)}
    return _mods_cache[path]


def _mod(name: str, mods: dict) -> int:
    if name not in mods:
        near = difflib.get_close_matches(name, list(mods), n=3)
        raise DbError(f"{name!r} isn't an xi.mod name" + (f" (did you mean {', '.join(near)}?)" if near else ""))
    return mods[name]


def _sql_value(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, int):
        return str(v)
    return "'" + str(v).replace("\\", "\\\\").replace("'", "''") + "'"


def _server_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_+.-]", "", name.lower().replace("'", "").replace(" ", "_"))


def _server_columns(edit: dict, mirror: bool, manifest: dict) -> dict[str, dict]:
    """``{table: {column: value}}`` for an edit: its mirrored fields, then its own columns."""
    cols: dict[str, dict] = {}
    if mirror:
        for field, value in (edit.get("set") or {}).items():
            if field in MIRROR:
                t, c = MIRROR[field]
                cols.setdefault(t, {})[c] = value
        name = ((edit.get("strings") or {}).get("en") or {}).get("name")
        if "like" in edit and isinstance(name, str):
            for t in _SERVER_TABLES.get(edit["table"], ["item_basic"]):
                if t in _ROW_TABLES:
                    cols.setdefault(t, {})["name"] = _server_name(name)
            cols.setdefault("item_basic", {})["sortname"] = _server_name(name)
    for t, row in (edit.get("server") or {}).items():
        if t in _ROW_TABLES or t == "item_info":
            cols.setdefault(t, {}).update(row)
    for t, row in cols.items():
        for c, v in list(row.items()):
            if c == "flags":
                row[c] = C.flag_mask(v) if isinstance(v, list) else v
            elif c == "jobs":
                row[c] = C.job_mask(v, "server")
            elif c in ("slot", "rslot"):
                row[c] = C.slot_mask(v) if isinstance(v, list) else v
            elif c == "skill":
                row[c] = C.skill_id(v)
            elif c == "MId" and isinstance(v, dict):
                row[c] = _gear_model(v["gear"], manifest)
    return cols


def _gear_model(action_id: str, manifest: dict) -> int:
    for a in manifest.get("actions") or []:
        if a.get("id") == action_id:
            if a.get("type") != "gear":
                raise DbError(f"MId: {action_id} is a {a.get('type')} action, not gear")
            mid = (a.get("result") or {}).get("model_id", (a.get("model") or {}).get("model_id"))
            if isinstance(mid, int):
                return mid
            raise DbError(f"MId: gear action {action_id} has no model id yet")
    raise DbError(f"MId: no action {action_id} in this project")


def proposed_sql(action: dict, manifest: dict, records: list[dict]) -> str:
    """The SQL for the action's item edits (``""`` when none has a server side)."""
    mirror = (action.get("server") or {}).get("mirror", True)
    names = {(r["table"], r["id"]): r.get("name") for r in records if r.get("lang") == "en"}
    lines: list[str] = []
    for i, edit in enumerate(action["edits"]):
        if not is_item_table(edit["table"]) or edit.get("server") is False:
            continue
        try:
            lines += _edit_sql(edit, mirror, manifest, names)
        except DbError as e:
            raise DbError(f"edits[{i}] ({edit['table']} {edit['id']}): {e}") from None
    return "\n".join(lines).rstrip() + "\n" if lines else ""


def _edit_sql(edit: dict, mirror: bool, manifest: dict, names: dict) -> list[str]:
    """The statements for one item edit (none when it has no server side)."""
    lines: list[str] = []
    rid = edit["id"]
    cols = _server_columns(edit, mirror, manifest)
    server = edit.get("server") or {}
    mods = server.get("item_mods") or {}
    lists = {t: server[t] for t in ("item_mods_pet", "item_latents") if t in server}
    if not (cols or mods or lists or "like" in edit):
        return []
    head = f"-- {edit['table']} {rid} {names.get((edit['table'], rid)) or ''}".rstrip()
    lines.append(head + (f": {edit['note']}" if edit.get("note") else ""))
    if "like" in edit:
        like = edit["like"]
        lines.append(f"-- a new item, its rows copied from {like}")
        for t in _SERVER_TABLES.get(edit["table"], ["item_basic"]):
            columns, idc = _COLUMNS[t], _ID_COL[t]
            rest = ", ".join(f"`{c}`" for c in columns[1:])
            lines.append(f"DELETE FROM `{t}` WHERE `{idc}` = {rid};")
            lines.append(f"INSERT INTO `{t}` (`{idc}`, {rest}) SELECT {rid}, {rest} FROM `{t}` "
                         f"WHERE `{idc}` = {like};")
    for t in _ROW_TABLES:
        row = cols.get(t)
        if row:
            sets = ", ".join(f"`{c}` = {_sql_value(v)}" for c, v in row.items())
            lines.append(f"UPDATE `{t}` SET {sets} WHERE `{_ID_COL[t]}` = {rid};")
    if cols.get("item_info"):
        row = {"icon": rid, **cols["item_info"]}
        names_ = ", ".join(f"`{c}`" for c in row)
        vals = ", ".join(_sql_value(v) for v in row.values())
        upd = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in cols["item_info"])
        lines.append(f"INSERT INTO `item_info` (`itemid`, {names_}) VALUES ({rid}, {vals}) "
                     f"ON DUPLICATE KEY UPDATE {upd};")
    if mods:
        ids = mod_ids()
        gone = [f"{_mod(m, ids)}" for m, v in mods.items() if v is None]
        keep = [(m, v) for m, v in mods.items() if v is not None]
        if gone:
            lines.append(f"DELETE FROM `item_mods` WHERE `itemId` = {rid} AND `modId` IN ({', '.join(gone)});")
        if keep:
            vals = ", ".join(f"({rid}, {_mod(m, ids)}, {v})" for m, v in keep)
            lines.append(f"REPLACE INTO `item_mods` (`itemId`, `modId`, `value`) VALUES {vals}; "
                         f"-- {', '.join(f'{m} {v}' for m, v in keep)}")
    for t, rows in lists.items():
        lines.append(f"DELETE FROM `{t}` WHERE `itemId` = {rid};")
        if rows:
            ids = mod_ids()
            cols_t = _COLUMNS[t][1:]
            vals = []
            for r in rows:
                cells = [str(rid)]
                for c in cols_t:
                    v = r.get(_LIST_KEYS[c], 0)
                    cells.append(str(_mod(v, ids)) if c == "modId" else _sql_value(v))
                vals.append("(" + ", ".join(cells) + ")")
            lines.append(f"INSERT INTO `{t}` (`itemId`, {', '.join(f'`{c}`' for c in cols_t)}) "
                         f"VALUES {', '.join(vals)};")
    lines.append("")
    return lines


def sql_path(action: dict, action_file: Path, project_file: Path) -> Path | None:
    """Where the action's SQL goes: ``server.sql`` relative to the file holding the action,
    else ``<project>.sql`` beside the project file. None when ``server.emit`` is false."""
    server = action.get("server") or {}
    if not server.get("emit", True):
        return None
    if server.get("sql"):
        return Path(action_file).parent / server["sql"]
    return Path(project_file).with_suffix(".sql")


_HEAD = ("-- Proposed by `xi dats build {project}`. xi-tools never runs this file: review it, then\n"
         "-- copy it into the server's modules (sql/custom) or run it yourself.\n")


def _section(action_id: str) -> tuple[str, str]:
    return f"-- >>> {action_id}\n", f"-- <<< {action_id}\n"


def write_sql_section(path: Path, action_id: str, sql: str, project: str) -> None:
    """Put the action's SQL in ``path`` between its markers, replacing its earlier section."""
    begin, end = _section(action_id)
    text = path.read_text(encoding="utf-8") if path.is_file() else _HEAD.format(project=project)
    block = begin + sql + end
    if begin in text and end in text:
        a = text.index(begin)
        b = text.index(end, a) + len(end)
        text = text[:a] + block + text[b:]
    else:
        text = text.rstrip("\n") + "\n\n" + block
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def remove_sql_section(path: Path, action_id: str) -> bool:
    """Drop the action's section from ``path`` (the file too once nothing else is left)."""
    if not path.is_file():
        return False
    begin, end = _section(action_id)
    text = path.read_text(encoding="utf-8")
    if begin not in text:
        return False
    a = text.index(begin)
    b = text.index(end, a) + len(end) if end in text[a:] else len(text)
    text = (text[:a].rstrip("\n") + "\n" + text[b:].lstrip("\n")).rstrip("\n") + "\n"
    if "-- >>> " not in text:
        path.unlink()
    else:
        path.write_text(text, encoding="utf-8")
    return True


# ── reading records (the wizard, previews) ───────────────────────────────────

def describe(root: Path, table: str, rid: int) -> dict:
    """What record ``rid`` of ``table`` holds in ``root``: ``{name, empty, fields}``."""
    files = _Files(Path(root))
    if is_item_table(table):
        en, _jp, idx = locate_item(files, table, rid)
        dat = files.item(en)
        rec = dat.record(idx)
        layout = _layout(table)
        fields = {f: read_field(rec, layout, dat.format, LAYOUT_NAMES.get(f, f)) for f in C.set_fields(table)}
        strings = item_strings(rec, layout, dat.format)
        texts = ({n: sub_value(s) for n, s in zip(C.ITEM_STRINGS["en"], strings[1])} if strings else {})
        return {"name": item_name(rec, layout, dat.format), "empty": is_empty_item(rec, layout, dat.format),
                "fields": fields, "strings": texts, "dat": en, "format": dat.format}
    en, _jp = DMSG_PARTS[table]
    t = files.table(en)
    idx = locate_block(t, table, rid) if t is not None else None
    if idx is None:
        return {"name": "", "empty": True, "fields": {}, "strings": {}, "dat": en}
    subs = _block_subs(t.blocks[idx])
    texts = {n: sub_value(s) for n, s in zip(C.DMSG_SUBS[table], subs)}
    return {"name": texts.get("name", ""), "empty": False, "fields": {}, "strings": texts, "dat": en}

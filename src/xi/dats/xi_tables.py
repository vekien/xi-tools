"""``ui`` actions of ``xi dats``: a client table with some entries changed.

Three kinds of retail table take a list of edits and come out as the same DAT
with those entries replaced, at the same ROM path, in the build target:

  strings   a ``d_msg`` text table — names, help text, titles, key items, …
            (any table ``xi ui strings list`` shows, or any other d_msg DAT)
  items     an item table (``xi ui items``): records patched, or added past the end
  dialog    a zone's dialog table: the lines its NPCs and menus print

None of these register a file id. The DAT already has one — it is a retail file —
and the build writes the edited copy at that path, which an overlay (XIPivot) or
a launcher then serves in place of the original. Everything the edits do not
name is carried over from the table the build reads, byte for byte: an empty
edit list reproduces the source exactly, so a build is reproducible from the
edits plus the install, and a repository need only hold the edits.

Edits layer. The table read is the one the target already holds — written by an
earlier action of the same build, or an earlier build — else the install's, the
way the client resolves it. Two actions may edit one DAT and both land.

The edits are the JSON the matching command exports:

  strings   ``[{"id": 12, "text": "…"}]``            (``xi ui strings export``)
            an optional ``"sub"`` names the sub-string; else ``target.entry``,
            else the block's first text sub-string
  items     ``[{"id": 30720, "name": "…", …}]``       (``xi ui items <group> json``)
            ``id`` is the item id; only the fields given are written to an
            existing record, and a record past the end is built from the entry
            the way ``xi ui items <group> inject`` builds one
  dialog    ``[{"id": 3, "text": "…"}]``              (``xi event dialogue export``)
            ``text`` takes the same escapes as ``xi event dialogue edit``;
            ``"raw_hex"`` instead of ``text`` writes those exact bytes

An id past the end of the table grows it, unless ``options.grow`` is false.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import click

from xi.common import xi_dmsg as D
from xi.menu import xi_menu_table as MT

CATEGORIES = ("strings", "items", "dialog")

_ROM_RE = re.compile(r"^ROM[0-9]*/[0-9]+/[0-9]+\.DAT$", re.I)


class TableError(click.ClickException):
    pass


# ── the action ─────────────────────────────────────────────────────────────────

def build(action: dict, root, edits_path: Path, *, dry_run: bool = False) -> dict:
    """Apply the action's edits to its table as ``root`` sees it and write the
    result into ``root``. Returns the build result (what ``dats build`` records
    on the action); with ``dry_run`` nothing is written."""
    action_id = action.get("id", "?")
    target = action.get("target") or {}
    rom = _rom_path(target.get("dat"), action_id)
    category = target.get("category")
    if category not in CATEGORIES:
        raise TableError(f"{action_id}: target.category must be one of {', '.join(CATEGORIES)} "
                         f"(got {category!r}).")
    grow = bool((action.get("options") or {}).get("grow", True))
    edits = load_edits(edits_path)

    src = MT.dat_path(root, rom)
    if not src.exists():
        raise TableError(f"{action_id}: {rom} is neither in the target nor in the install.")
    data = src.read_bytes()
    out_path = MT.target_path(root, rom)
    created = not out_path.exists()

    try:
        if category == "strings":
            new, n, grew = apply_strings(data, edits, default_sub=target.get("entry"), grow=grow)
        elif category == "items":
            new, n, grew = apply_items(data, edits, rom, grow=grow)
        else:
            new, n, grew = apply_dialog(data, edits, grow=grow)
    except TableError as e:
        raise TableError(f"{action_id}: {e.message}")

    if not dry_run:
        out_path = MT.write_dat(root, rom, new)

    label = f"{n} entr{'y' if n == 1 else 'ies'}"
    if grew:
        label += f", grown to {grew}"
    return {
        "dat": rom, "category": category, "entries": n, "grown_to": grew,
        "output": str(out_path), "source": str(src), "bytes": len(new),
        "created": created,
        "registered": f"{category} {rom}: {label}",
    }


def load_edits(path: Path) -> list:
    """The edit list from a JSON file: a bare list, or an object holding one
    under ``edits`` (the self-describing form ``dats prepare`` accepts)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise TableError(f"cannot read the edits {path}: {e}")
    if isinstance(data, dict):
        data = data.get("edits")
    if not isinstance(data, list) or not all(isinstance(e, dict) for e in data):
        raise TableError(f"{path}: the edits must be a JSON list of objects (or an object with an "
                         "\"edits\" list).")
    return data


def _rom_path(value, action_id: str) -> str:
    rom = str(value or "").replace("\\", "/")
    if rom and not rom.upper().endswith(".DAT"):
        rom += ".DAT"
    if not _ROM_RE.match(rom):
        raise TableError(f"{action_id}: target.dat must be a ROM path such as ROM/181/73.DAT (got {value!r}).")
    return rom


def _entry_id(entry: dict, what: str) -> int:
    idx = entry.get("id")
    if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0:
        raise TableError(f"{what}: every edit needs an integer \"id\" (got {idx!r}).")
    return idx


# ── strings: a d_msg table ─────────────────────────────────────────────────────

def _text_slot(block: bytearray, preferred) -> int:
    """The sub-string index holding a block's text: ``preferred`` when it is a
    text slot, else the first text slot, else -1."""
    try:
        subs = D._parse_block(block)
    except D.DmsgError:
        return -1
    if isinstance(preferred, int) and 0 <= preferred < len(subs) and subs[preferred]["marker"] == 1:
        return preferred
    if preferred is None:
        for i, s in enumerate(subs):
            if s["marker"] == 1:
                return i
    return -1


def _template_block(table: D.DmsgTable) -> bytearray | None:
    """A block to clone for a new entry: the last one that carries text, so a
    grown table keeps the shape the client expects for that table."""
    for block in reversed(table.blocks):
        if _text_slot(block, None) >= 0:
            return bytearray(block)
    return None


def apply_strings(data: bytes, edits: list, *, default_sub=None, grow: bool = True):
    """(new bytes, entries written, grown-to or None) for a d_msg table."""
    try:
        table = D.parse(data)
    except D.DmsgError as e:
        raise TableError(f"not a d_msg table: {e}")
    template = None
    n = 0
    grew = None
    for entry in edits:
        idx = _entry_id(entry, "strings")
        text = entry.get("text")
        if not isinstance(text, str):
            raise TableError(f"strings: edit {idx} needs a \"text\" string.")
        sub = entry.get("sub", default_sub)
        if idx >= len(table.blocks):
            if not grow:
                raise TableError(f"strings: id {idx} is past the table ({len(table.blocks)} entries) "
                                 "and options.grow is off.")
            if table.variable:
                raise TableError("strings: growing a variable-size d_msg table is not supported.")
            template = template or _template_block(table)
            if template is None:
                raise TableError("strings: no block with text to model a new entry on.")
            D.ensure_len(table, idx)             # fillers up to the id, empty like retail's
            table.blocks.append(bytearray(template))
            grew = len(table.blocks)
        block = table.blocks[idx]
        slot = _text_slot(block, sub)
        if slot < 0 and _is_filler(block):
            # An earlier growth left an empty filler here: give it a text slot.
            template = template or _template_block(table)
            if template is None:
                raise TableError("strings: no block with text to model a new entry on.")
            block = bytearray(template)
            slot = _text_slot(block, sub)
        if slot < 0:
            raise TableError(f"strings: entry {idx} has no text sub-string"
                             + (f" at {sub}" if sub is not None else "") + ".")
        try:
            table.blocks[idx] = bytearray(D.set_text(block, slot, text, stride=table.stride))
        except D.DmsgError as e:
            raise TableError(f"strings: entry {idx}: {e}")
        n += 1
    return D.serialize(table), n, grew


def _is_filler(block: bytearray) -> bool:
    try:
        return not D._parse_block(block)
    except D.DmsgError:
        return False


# ── items: an item table ───────────────────────────────────────────────────────

def item_table(rom: str):
    """``(base_id, item_type)`` of the item table at ``rom`` (either language's
    DAT), or None when it is not one ``xi ui items`` knows."""
    from xi.ui.items.xi_parser import ITEM_DATS
    want = rom.upper()
    for _name, base_id, item_type, en_rom, jp_rom in ITEM_DATS:
        if want in (en_rom.upper(), jp_rom.upper()):
            return base_id, item_type
    return None


def apply_items(data: bytes, edits: list, rom: str, *, grow: bool = True):
    """(new bytes, entries written, grown-to or None) for an item table."""
    from xi.ui.items.xi_parser import _decrypt, _encrypt, _patch_record, build_record
    from xi.ui.items.xi_layout import detect_stride, format_for_stride

    info = item_table(rom)
    if info is None:
        raise TableError(f"items: {rom} is not an item table this build knows "
                         "(`xi ui items info` lists them).")
    base_id, item_type = info
    stride = detect_stride(data)
    fmt = format_for_stride(stride)
    buf = bytearray(_decrypt(data))
    count = len(buf) // stride
    filler = None
    n = 0
    grew = None
    for entry in edits:
        item_id = _entry_id(entry, "items")
        idx = item_id - base_id
        if idx < 0:
            raise TableError(f"items: id {item_id} is below {rom}'s first id {base_id}.")
        if idx >= count:
            if not grow:
                raise TableError(f"items: id {item_id} is past the table ({count} records, ids "
                                 f"{base_id}..{base_id + count - 1}) and options.grow is off.")
            # Retail's unused slots hold ".", which is what the item tools treat as free.
            filler = filler or build_record({"name": "."}, item_type, fmt)
            while count <= idx:
                buf += filler
                count += 1
            grew = count
            rec = build_record(dict(entry), item_type, fmt)
        else:
            rec = bytearray(buf[idx * stride:(idx + 1) * stride])
            _patch_record(rec, dict(entry), item_type, fmt)
            rec = bytes(rec)
        buf[idx * stride:(idx + 1) * stride] = rec
        n += 1
    return _encrypt(bytes(buf)), n, grew


# ── dialog: a zone's dialog table ──────────────────────────────────────────────

def apply_dialog(data: bytes, edits: list, *, grow: bool = True):
    """(new bytes, entries written, grown-to or None) for a zone dialog table."""
    from xi.dialog import xi_dialog as XD
    try:
        blobs, obfuscated = XD.raw_entry_blobs(data)
    except XD.DialogError as e:
        raise TableError(f"not a dialog table: {e}")
    n = 0
    grew = None
    for entry in edits:
        idx = _entry_id(entry, "dialog")
        raw_hex = entry.get("raw_hex")
        text = entry.get("text")
        try:
            if isinstance(raw_hex, str):
                blob = bytes.fromhex(raw_hex.replace(" ", "")) + b"\x00"
            elif isinstance(text, str):
                blob = (XD.replace_entry_text(blobs[idx], text) if idx < len(blobs)
                        else XD.encode_event_string(text) + b"\x00")
            else:
                raise TableError(f"dialog: edit {idx} needs \"text\" or \"raw_hex\".")
        except (ValueError, XD.DialogError) as e:
            raise TableError(f"dialog: entry {idx}: {e}")
        if idx >= len(blobs):
            if not grow:
                raise TableError(f"dialog: id {idx} is past the table ({len(blobs)} entries) "
                                 "and options.grow is off.")
            while len(blobs) < idx:
                blobs.append(b"\x00")            # an empty line, like an unused retail entry
            blobs.append(blob)
            grew = len(blobs)
        else:
            blobs[idx] = blob
        n += 1
    return XD.build_container(blobs, obfuscated), n, grew

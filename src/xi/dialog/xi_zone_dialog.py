"""The ``zone_dialog`` action of ``xi dats`` (schema/zone_dialog.json): a zone's dialog lines
— what its NPCs, menus and cutscenes print — edited or added by line id, in the English and
Japanese dialog tables of the zone.

A line is an entry of the zone's event-message table (xi.dialog.xi_dialog): its displayed
text first, then any NUL-separated variants the client picks between. An edit changes the
displayed part and keeps the rest; ``replace`` swaps text inside it, so control codes the
text form can't show survive. New lines go past the end in both languages, so event
scripts, which name lines by index, see the same line in either client.

Like the ``database`` action, every build starts from the lines as they were before this
action: what a previous build of it changed in this root is put back first (each line's
bytes before and after are recorded), then the edits are applied — a rebuild converges, a
line taken out of the action goes back, and undo is exact.
"""
from __future__ import annotations

import re
from pathlib import Path

from xi.dialog import xi_dialog as XD

LANGS = ("en", "jp")
BLANK = b"\x00"            # an unused line id
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_ACTION_KEYS = {"id", "type", "enabled", "description", "depends_on", "zone", "lines", "result", "outputs"}
_LINE_KEYS = {"id", "en", "jp", "new", "note"}


class ZoneDialogError(ValueError):
    pass


# ── validation ───────────────────────────────────────────────────────────────

def _is_int(v, lo: int = 0, hi: int | None = None) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= lo and (hi is None or v <= hi)


def _check_text(v, at: str, errs: list) -> None:
    if isinstance(v, str):
        return
    if isinstance(v, dict) and set(v) == {"replace"} and isinstance(v["replace"], dict) and v["replace"] \
            and all(isinstance(a, str) and a and isinstance(b, str) for a, b in v["replace"].items()):
        return
    for key in ("hex", "entry_hex"):
        if isinstance(v, dict) and set(v) == {key} and isinstance(v[key], str):
            try:
                raw = bytes.fromhex(v[key].replace(" ", ""))
            except ValueError:
                break
            if key == "entry_hex" and not raw.endswith(b"\x00"):
                errs.append(f"{at}.entry_hex is the whole entry: it ends in the 00 that closes the line")
            return
    errs.append(f"{at} must be text, {{\"replace\": {{old: new, …}}}}, {{\"hex\": \"…\"}} (the shown "
                "text's bytes) or {\"entry_hex\": \"…\"} (the whole entry)")


def validate_action(action) -> list[str]:
    """Problems with a ``zone_dialog`` action, each naming the field; ``[]`` when valid."""
    if not isinstance(action, dict):
        return ["the action must be an object"]
    errs: list[str] = []
    for k in action:
        if k not in _ACTION_KEYS:
            errs.append(f"action: unknown key {k!r}")
    if action.get("type") != "zone_dialog":
        errs.append("type must be 'zone_dialog'")
    if not (isinstance(action.get("id"), str) and _ID_RE.match(action["id"])):
        errs.append("id must be an action id (^[a-z0-9][a-z0-9_.-]*$)")
    if not _is_int(action.get("zone"), 0, 0xFFFF):
        errs.append("zone must be a zone id")
    if "enabled" in action and not isinstance(action["enabled"], bool):
        errs.append("enabled must be true or false")
    lines = action.get("lines")
    if not isinstance(lines, list) or not lines:
        errs.append("lines must be a non-empty list")
        return errs
    seen: dict = {}
    for i, line in enumerate(lines):
        at = f"lines[{i}]"
        if not isinstance(line, dict):
            errs.append(f"{at} must be an object")
            continue
        for k in line:
            if k not in _LINE_KEYS:
                errs.append(f"{at}: unknown key {k!r}")
        if not _is_int(line.get("id")):
            errs.append(f"{at}.id must be a line id (an integer >= 0)")
        elif line["id"] in seen:
            errs.append(f"{at}: line {line['id']} is already edited by lines[{seen[line['id']]}] — merge them")
        else:
            seen[line["id"]] = i
        if "new" in line and not isinstance(line["new"], bool):
            errs.append(f"{at}.new must be true or false")
        if not ({"en", "jp"} & line.keys()):
            errs.append(f"{at}: give en and/or jp")
        en = line.get("en")
        if line.get("new") and not (isinstance(en, str) or (isinstance(en, dict) and {"hex", "entry_hex"} & en.keys())):
            errs.append(f"{at}: a new line needs its English text as en (text, hex or entry_hex)")
        for lang in LANGS:
            if lang in line:
                _check_text(line[lang], f"{at}.{lang}", errs)
                if line.get("new") and isinstance(line[lang], dict) and "replace" in line[lang]:
                    errs.append(f"{at}.{lang}: a new line has nothing to replace in; give its text")
        if "note" in line and not isinstance(line["note"], str):
            errs.append(f"{at}.note must be a string")
    return errs


# ── the zone's tables ────────────────────────────────────────────────────────

def dialog_file_id(zone: int, lang: str) -> int:
    from xi.zone.xi_inject import zone_dialog_file_id, zone_dialog_jp_file_id
    return zone_dialog_file_id(zone) if lang == "en" else zone_dialog_jp_file_id(zone)


def dialog_rel(root, zone: int, lang: str) -> str | None:
    """The ROM path of the zone's dialog table as ``root`` resolves it: the target's ROM10
    pair (custom zones), then every table pair of the install. None when unregistered."""
    from xi.ftable.xi_core import resolve_dat_in_root, scan_file_ids
    fid = dialog_file_id(zone, lang)
    dat, _rom = resolve_dat_in_root(root, fid)
    if dat:
        return dat
    hits = scan_file_ids([fid])
    return hits[0]["dat"] if hits else None


def displayed(blob: bytes) -> bytes:
    nul = blob.find(0)
    return blob[:nul] if nul >= 0 else blob


def line_text(blob: bytes) -> str:
    return XD.decode_event_string(displayed(blob))[0]


def _set_line(blob: bytes, spec) -> bytes:
    tail = blob[len(displayed(blob)):] or b"\x00"
    if isinstance(spec, str):
        return XD.encode_event_string(spec) + tail
    if "entry_hex" in spec:
        return bytes.fromhex(spec["entry_hex"].replace(" ", ""))
    if "hex" in spec:
        return bytes.fromhex(spec["hex"].replace(" ", "")) + tail
    shown = displayed(blob)
    for old, new in spec["replace"].items():
        ob = XD.encode_event_string(old)
        if ob not in shown:
            raise ZoneDialogError(f"{old!r} isn't in {line_text(blob)!r}")
        shown = shown.replace(ob, XD.encode_event_string(new))
    return shown + tail


class _Tables:
    """The zone's dialog tables one build reads and writes, each loaded once."""

    def __init__(self, root: Path, zone: int):
        self.root, self.zone = Path(root), zone
        self.rel: dict[str, str | None] = {}
        self.blobs: dict[str, list[bytes]] = {}
        self.obf: dict[str, bool] = {}
        self.orig: dict[str, bytes] = {}

    def load(self, lang: str) -> list[bytes] | None:
        if lang not in self.rel:
            from xi.dats import xi_stage
            rel = dialog_rel(self.root, self.zone, lang)
            self.rel[lang] = rel
            if rel is not None:
                data = xi_stage.read(self.root, rel)
                if data is None:
                    self.rel[lang] = None
                else:
                    try:
                        self.blobs[lang], self.obf[lang] = XD.raw_entry_blobs(data)
                    except XD.DialogError as e:
                        raise ZoneDialogError(f"{rel}: {e}")
                    self.orig[lang] = data
        return self.blobs.get(lang) if self.rel[lang] else None

    def changed(self) -> list[tuple[str, bytes]]:
        out = []
        for lang, blobs in self.blobs.items():
            data = XD.build_container(blobs, self.obf[lang])
            if data != self.orig[lang]:
                out.append((self.rel[lang], data))
        return out


# ── build / undo ─────────────────────────────────────────────────────────────

def _restore(tables: _Tables, entry: dict, warnings: list) -> None:
    lang, lid = entry["lang"], entry["id"]
    blobs = tables.load(lang)
    label = f"zone {tables.zone} line {lid} ({lang})"
    if blobs is None:
        warnings.append(f"{label}: {entry.get('dat')} is gone; nothing to put back")
        return
    if entry.get("grew"):
        # Lines a build added: off the end again, or, when lines were added after them since
        # (another action or project), blanked in place so no later line id moves. A blank
        # line (b"\x00") is what retail's unused ids hold; the next build writes into it again.
        before, after = entry["grew"]
        mine = entry.get("to_hex")
        held = ([blobs[lid].hex()] if lid < len(blobs) else []) if isinstance(mine, str) else             [b.hex() for b in blobs[before:after]]
        if len(blobs) < after or (mine is not None and held != ([mine] if isinstance(mine, str) else mine)):
            warnings.append(f"{label}: changed since the last build; left as it is")
            return
        if len(blobs) == after:
            del blobs[before:]
        else:
            for i in range(before, after):
                blobs[i] = BLANK
        return
    if lid >= len(blobs) or blobs[lid].hex() != entry["to_hex"]:
        warnings.append(f"{label}: changed since the last build; left as it is")
        return
    blobs[lid] = bytes.fromhex(entry["from_hex"])


def _entry(zone, lang, rel, lid, before: bytes, after: bytes, **more) -> dict:
    return {"zone": zone, "lang": lang, "dat": rel, "id": lid,
            "from": line_text(before) if before else None, "to": line_text(after),
            "from_hex": before.hex(), "to_hex": after.hex(), **more}


def build(action: dict, *, root: Path, target: str | None, dry_run: bool = False, unwound: bool = False) -> dict:
    """Apply ``action`` to the zone's dialog tables in ``root``; the build result
    (``records`` is what gets recorded for this root). ``unwound``: the previous build's
    changes were already put back (``xi dats build`` does that for the whole project first)."""
    errs = validate_action(action)
    if errs:
        raise ZoneDialogError("; ".join(errs))
    zone = action["zone"]
    prev = (((action.get("result") or {}).get("roots") or {}).get(target)) or []
    tables = _Tables(Path(root), zone)
    warnings: list[str] = []
    for entry in ([] if unwound else reversed(prev)):
        _restore(tables, entry, warnings)
    if tables.load("en") is None:
        raise ZoneDialogError(f"zone {zone} has no English dialog table (file id {dialog_file_id(zone, 'en')} "
                              "is unregistered)")
    records: list[dict] = []
    lines = action["lines"]
    # Edits of existing lines first, then new lines in id order, so the tables grow once each.
    for line in [l for l in lines if not l.get("new")] + sorted((l for l in lines if l.get("new")),
                                                                  key=lambda l: l["id"]):
        lid = line["id"]
        for lang in LANGS:
            blobs = tables.load(lang)
            spec = line.get(lang)
            if line.get("new"):
                spec = spec if spec is not None else line["en"]     # the JP table takes the EN text
                if blobs is None:
                    if lang == "jp":
                        warnings.append(f"zone {zone} has no Japanese dialog table; line {lid} is English only")
                        continue
                if lid < len(blobs):
                    if blobs[lid] != BLANK:
                        raise ZoneDialogError(f"lines: zone {zone} already has line {lid} ({lang}); drop new to edit it")
                    blob = _set_line(b"\x00", spec)          # a blank id (see _restore)
                    records.append(_entry(zone, lang, tables.rel[lang], lid, blobs[lid], blob, created=True))
                    blobs[lid] = blob
                    continue
                before_n = len(blobs)
                while len(blobs) < lid:
                    blobs.append(b"\x00")                  # an unused line, as retail's are
                blob = _set_line(b"\x00", spec)
                blobs.append(blob)
                records.append(_entry(zone, lang, tables.rel[lang], lid, b"", blob, created=True,
                                      grew=[before_n, len(blobs)]))
                continue
            if spec is None:
                continue
            if blobs is None:
                raise ZoneDialogError(f"zone {zone} has no {'Japanese' if lang == 'jp' else 'English'} dialog table")
            if lid >= len(blobs):
                raise ZoneDialogError(f"lines: zone {zone} has {len(blobs)} lines ({lang}); line {lid} is past "
                                      "the end — mark it \"new\": true to add it")
            before = blobs[lid]
            try:
                after = _set_line(before, spec)
            except XD.DialogError as e:
                raise ZoneDialogError(f"line {lid} ({lang}): {e}")
            except ZoneDialogError as e:
                raise ZoneDialogError(f"line {lid} ({lang}): {e}")
            if after != before:
                blobs[lid] = after
                records.append(_entry(zone, lang, tables.rel[lang], lid, before, after))
    changed = tables.changed()
    written = []
    from xi.dats import xi_stage
    for rel, data in changed:
        out = xi_stage.write(root, rel, data, dry_run)
        if out is not None:
            written.append(str(out))
    from xi.database.xi_build import _pivot_shadow
    warnings += [f"the pivot folder has its own {rel}, which the client loads instead of this one — "
                 "build with --pivot to change what players see"
                 for rel in _pivot_shadow(Path(root), target, [r for r, _ in changed])]
    return {"id": action["id"], "type": "zone_dialog", "zone": zone, "target": target, "records": records,
            "files": [r for r, _ in changed], "written": written, "warnings": warnings}


def undo(action: dict, root: Path, target: str, dry_run: bool = False) -> tuple[int, list[str]]:
    """Put back every line ``action`` changed or added in ``root``; ``(lines, warnings)``."""
    entries = (((action.get("result") or {}).get("roots") or {}).get(target)) or []
    tables = _Tables(Path(root), action.get("zone"))
    warnings: list[str] = []
    for entry in reversed(entries):
        _restore(tables, entry, warnings)
    from xi.dats import xi_stage
    for rel, data in tables.changed():
        xi_stage.write(root, rel, data, dry_run)
    return len(entries), warnings


def describe(root: Path, zone: int, lid: int) -> dict:
    """Line ``lid`` of the zone as ``root`` holds it: ``{en, jp, count, dat}``."""
    tables = _Tables(Path(root), zone)
    out = {"dat": None, "count": 0}
    for lang in LANGS:
        blobs = tables.load(lang)
        if blobs is None:
            continue
        if lang == "en":
            out["dat"], out["count"] = tables.rel[lang], len(blobs)
        out[lang] = line_text(blobs[lid]) if lid < len(blobs) else None
    if out["dat"] is None:
        raise ZoneDialogError(f"zone {zone} has no dialog table in this install")
    return out

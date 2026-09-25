"""The ``database`` action of ``xi dats``: edits to the client's record tables (schema/database.json).

A table is one the model viewer's Database shows, by the same key (``xi.mv.xi_database``,
kept in step with the viewer's ``ui/js/database.js``); a field is the viewer's name for it.
This module holds the names the action is written in — tables, fields, sub-strings, jobs,
races, slots, flags, weapon skills — the hand validator for the schema, and the encoders
that turn names into each side's bits: the DAT keeps WAR at job bit 1, the server at bit 0.

Checked against CatsEyeXI's item JSON and the DATs built from it (5,090 weapons): every
job, race, slot, flag and skill name lands on the bit / number below.
"""
from __future__ import annotations

import re

from xi.mv.xi_database import DMSG_TABLES, ITEM_TABLES, _FIELD_KEYS
from xi.ui.items.xi_layout import FIELDS, FORMAT_LEGACY, LAYOUTS

JOBS = ["WAR", "MNK", "WHM", "BLM", "RDM", "THF", "PLD", "DRK", "BST", "BRD", "RNG", "SAM",
        "NIN", "DRG", "SMN", "BLU", "COR", "PUP", "DNC", "SCH", "GEO", "RUN"]
RACES = ["HUME_M", "HUME_F", "ELVAAN_M", "ELVAAN_F", "TARU_M", "TARU_F", "MITHRA", "GALKA"]
SLOTS = ["MAIN", "SUB", "RANGED", "AMMO", "HEAD", "BODY", "HANDS", "LEGS", "FEET",
         "NECK", "WAIST", "EAR1", "EAR2", "RING1", "RING2", "BACK"]
# The server's ITEM_FLAG enum (src/map/items/item.h), bit n = FLAGS[n]; the DAT uses the same bits.
FLAGS = ["WALLHANGING", "UNKNOWN_01", "MYSTERY_BOX", "MOG_GARDEN", "MAIL2ACCOUNT", "INSCRIBABLE",
         "NOAUCTION", "SCROLL", "LINKSHELL", "CANUSE", "CANTRADENPC", "CANEQUIP", "NOSALE",
         "NODELIVERY", "EX", "RARE"]
SKILLS = {"NONE": 0, "HAND_TO_HAND": 1, "DAGGER": 2, "SWORD": 3, "GREAT_SWORD": 4, "AXE": 5,
          "GREAT_AXE": 6, "SCYTHE": 7, "POLEARM": 8, "KATANA": 9, "GREAT_KATANA": 10, "CLUB": 11,
          "STAFF": 12, "ARCHERY": 25, "MARKSMANSHIP": 26, "THROWING": 27, "STRING_INSTRUMENT": 41,
          "WIND_INSTRUMENT": 42, "HANDBELL": 45, "FISHING": 48}

# ── tables ───────────────────────────────────────────────────────────────────

# Item tables whose records have a known header (the rest are special blocks with no fields).
ITEM_LAYOUT = {key: layout for key, layout, _parts in ITEM_TABLES if layout in LAYOUTS}

_QUEST_SUBS = ["id", "name", "description"]
_HELP_SUBS = ["name", "help"]
# d_msg sub-string names per table — the viewer's DMSG_GROUPS. `id`/`unk*` are not editable:
# `id` is how a key-item / quest / mission row is addressed.
DMSG_SUBS = {key: (_QUEST_SUBS if key[:2] in ("q_", "m_") else None) for key, _en, _jp in DMSG_TABLES}
DMSG_SUBS.update({
    "keyitems": ["id", "category", "unk2", "unk3", "name", "plural", "description"],
    "titles": ["name"], "jobs": ["name"], "spells": ["name"], "spellHelp": _HELP_SUBS,
    "abilities": ["name"], "abilityHelp": _HELP_SUBS, "bluHelp": _HELP_SUBS,
    "status": ["name", "adjective"], "mounts": ["name", "keyItem"], "mountHelp": _HELP_SUBS,
    "monsterFamilies": ["name", "plural"], "slots": ["name"], "augments": ["format"],
    "merits": ["text"], "jobPoints": ["text"], "jobGifts": ["text"], "soulplates": ["text"],
    "trust": ["text"], "emoteHelp": ["text"], "chatHelp": ["text"], "mazeRunes": _HELP_SUBS,
    "headings": ["name"], "servers": ["name"],
})
# d_msg tables whose rows carry their own id: an edit's `id` is that id, not the row index.
ID_KEYED = {key for key, subs in DMSG_SUBS.items() if subs and subs[0] == "id"}

ITEM_STRINGS = {"en": ["name", "article", "logName", "logPlural", "description"], "jp": ["name", "description"]}
_NUMERIC_SUBS = {"article": 3, "category": None, "keyItem": None}   # name -> maximum (None: any u32)


def tables() -> list[str]:
    """Every table key an edit may name: item tables with a known layout, then d_msg tables."""
    return list(ITEM_LAYOUT) + list(DMSG_SUBS)


def set_fields(table: str) -> list[str]:
    """The header fields ``set`` may name for an item table (the viewer's names)."""
    layout = ITEM_LAYOUT[table]
    return [_FIELD_KEYS.get(n, n) for n in FIELDS[layout][FORMAT_LEGACY] if n != "id"]


def string_names(table: str, lang: str) -> list[str]:
    if table in ITEM_LAYOUT:
        return ITEM_STRINGS[lang]
    return [s for s in DMSG_SUBS[table] if s != "id" and not s.startswith("unk")]


# ── encoders ─────────────────────────────────────────────────────────────────

def _mask(names, table: list[str], shift: int = 0) -> int:
    return sum(1 << (table.index(n) + shift) for n in names)


def job_mask(jobs, side: str = "dat") -> int:
    """Jobs as bits: ``side`` 'dat' (WAR = bit 1, all = 0x7FFFFE) or 'server' (WAR = bit 0)."""
    names = JOBS if jobs == "all" else jobs
    return _mask(names, JOBS, 1 if side == "dat" else 0)


def race_mask(races) -> int:
    return _mask(RACES if races == "all" else races, RACES, 1)


def slot_mask(slots) -> int:
    return _mask(slots, SLOTS)


def flag_mask(flags) -> int:
    return _mask(flags, FLAGS)


def skill_id(skill) -> int:
    return skill if isinstance(skill, int) else SKILLS[skill]


# ── validation ───────────────────────────────────────────────────────────────

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_MOD_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_ACTION_KEYS = {"id", "type", "enabled", "description", "depends_on", "edits", "server", "result", "outputs"}
_EDIT_KEYS = {"table", "id", "like", "note", "set", "strings", "icon", "server"}
_SERVER_KEYS = {"emit", "mirror", "sql"}

# Server columns (catseyexi sql/item_*.sql; item_info is CatsEyeXI's, modules sql/custom).
_ROW_COLS = {
    "item_basic": {"subid", "name", "sortname", "type", "stackSize", "flags", "aH", "BaseSell"},
    "item_equipment": {"name", "level", "ilevel", "jobs", "MId", "shieldSize", "scriptType", "slot", "rslot",
                       "rslotlook", "su_level"},
    "item_weapon": {"name", "skill", "subskill", "ilvl_skill", "ilvl_parry", "ilvl_macc", "dmgType", "hit",
                    "delay", "dmg", "unlock_points"},
    "item_usable": {"name", "validTargets", "activation", "animation", "animationTime", "maxCharges",
                    "useDelay", "reuseDelay", "aoe"},
    "item_furnishing": {"name", "storage", "moghancement", "element", "aura"},
    "item_info": {"icon", "desc"},
}
_LIST_ROWS = {"item_mods_pet": ({"mod", "value", "petType"}, {"mod", "value"}),
              "item_latents": ({"mod", "value", "latentId", "latentParam"}, {"mod", "value", "latentId"})}
SERVER_TABLES = [*_ROW_COLS, "item_mods", *_LIST_ROWS]


def _is_int(v, lo: int = 0, hi: int | None = None) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= lo and (hi is None or v <= hi)


def _unknown(obj: dict, allowed, at: str, errs: list) -> None:
    for k in obj:
        if k not in allowed:
            errs.append(f"{at}: unknown key {k!r}")


def _names(value, allowed: list[str], what: str, at: str, errs: list, allow_all: bool = False) -> None:
    if allow_all and value == "all":
        return
    if not isinstance(value, list):
        errs.append(f"{at} must be a list of {what} names" + (' or "all"' if allow_all else ""))
        return
    for v in value:
        if v not in allowed:
            errs.append(f"{at}: {v!r} is not a {what} ({', '.join(allowed)})")
    if len(set(map(str, value))) != len(value):
        errs.append(f"{at}: a {what} is listed twice")


def _check_set(table: str, values, at: str, errs: list) -> None:
    if not isinstance(values, dict):
        errs.append(f"{at} must be an object")
        return
    fields = set_fields(table)
    for name, v in values.items():
        where = f"{at}.{name}"
        if name not in fields:
            errs.append(f"{where}: table {table!r} ({ITEM_LAYOUT[table]} layout) has no field {name!r}"
                        f" — it has {', '.join(fields)}")
        elif name == "flags":
            _names(v, FLAGS, "flag", where, errs)
        elif name == "jobs":
            _names(v, JOBS, "job", where, errs, allow_all=True)
        elif name == "races":
            _names(v, RACES, "race", where, errs, allow_all=True)
        elif name == "slots":
            _names(v, SLOTS, "slot", where, errs)
        elif name == "skill":
            if not (v in SKILLS or _is_int(v, 0, 255)):
                errs.append(f"{where}: {v!r} is not a skill name or number 0-255")
        elif name == "jugSize":
            if not _is_int(v, 0, 255):
                errs.append(f"{where} must be an integer 0-255")
        elif name == "elementCharge":
            if not _is_int(v, 0, 0xFFFFFFFF):
                errs.append(f"{where} must be an integer 0-4294967295")
        elif not _is_int(v, 0, 0xFFFF):
            errs.append(f"{where} must be an integer 0-65535")


def _check_strings(table: str, strings, at: str, errs: list) -> None:
    if not isinstance(strings, dict):
        errs.append(f"{at} must be an object")
        return
    for lang, subs in strings.items():
        where = f"{at}.{lang}"
        if lang not in ("en", "jp"):
            errs.append(f"{at}: unknown language {lang!r} (en, jp)")
            continue
        if not isinstance(subs, dict):
            errs.append(f"{where} must be an object")
            continue
        names = string_names(table, lang)
        for name, v in subs.items():
            if name not in names:
                errs.append(f"{where}: table {table!r} has no {lang} string {name!r} — it has {', '.join(names)}")
            elif name in _NUMERIC_SUBS:
                if not _is_int(v, 0, _NUMERIC_SUBS[name]):
                    hi = _NUMERIC_SUBS[name]
                    errs.append(f"{where}.{name} must be an integer" + (f" 0-{hi}" if hi is not None else ""))
            elif isinstance(v, dict):
                rep = v.get("replace")
                if set(v) != {"replace"} or not isinstance(rep, dict) or not rep \
                        or not all(isinstance(a, str) and a and isinstance(b, str) for a, b in rep.items()):
                    errs.append(f"{where}.{name} must be text or {{\"replace\": {{old: new, …}}}}")
            elif not isinstance(v, str):
                errs.append(f"{where}.{name} must be a string")


def _check_icon(icon, at: str, errs: list) -> None:
    if not isinstance(icon, dict):
        errs.append(f"{at} must be an object")
        return
    _unknown(icon, {"from"}, at, errs)
    if not _is_int(icon.get("from")):
        errs.append(f"{at}.from must be the item id whose icon to copy")


def _check_server(rows, at: str, errs: list) -> None:
    if rows is False:
        return
    if not isinstance(rows, dict):
        errs.append(f"{at} must be an object of server tables, or false")
        return
    for table, row in rows.items():
        where = f"{at}.{table}"
        if table not in SERVER_TABLES:
            errs.append(f"{at}: unknown server table {table!r} ({', '.join(SERVER_TABLES)})")
        elif table == "item_mods":
            if not isinstance(row, dict):
                errs.append(f"{where} must map mod names to values")
                continue
            for mod, v in row.items():
                if not _MOD_RE.match(str(mod)):
                    errs.append(f"{where}: {mod!r} is not a mod name (xi.mod, e.g. DEF, HP, ACC)")
                if v is not None and not _is_int(v, -(1 << 31)):
                    errs.append(f"{where}.{mod} must be an integer, or null to remove the mod")
        elif table in _LIST_ROWS:
            allowed, required = _LIST_ROWS[table]
            if not isinstance(row, list):
                errs.append(f"{where} must be a list")
                continue
            for i, r in enumerate(row):
                rat = f"{where}[{i}]"
                if not isinstance(r, dict):
                    errs.append(f"{rat} must be an object")
                    continue
                _unknown(r, allowed, rat, errs)
                for k in sorted(required - r.keys()):
                    errs.append(f"{rat}: missing {k!r}")
                if "mod" in r and not _MOD_RE.match(str(r["mod"])):
                    errs.append(f"{rat}.mod: {r['mod']!r} is not a mod name")
                for k in allowed - {"mod"}:
                    if k in r and not _is_int(r[k], -(1 << 31)):
                        errs.append(f"{rat}.{k} must be an integer")
        else:
            if not isinstance(row, dict):
                errs.append(f"{where} must be an object of columns")
                continue
            for col, v in row.items():
                cat = f"{where}.{col}"
                if col not in _ROW_COLS[table]:
                    errs.append(f"{where}: unknown column {col!r} ({', '.join(sorted(_ROW_COLS[table]))})")
                elif col in ("name", "sortname"):
                    if not isinstance(v, str):
                        errs.append(f"{cat} must be a string")
                elif col == "desc":
                    if not (v is None or isinstance(v, str)):
                        errs.append(f"{cat} must be a string or null")
                elif col == "flags":
                    _names(v, FLAGS, "flag", cat, errs)
                elif col == "jobs":
                    _names(v, JOBS, "job", cat, errs, allow_all=True)
                elif col in ("slot", "rslot"):
                    _names(v, SLOTS, "slot", cat, errs)
                elif col == "skill":
                    if not (v in SKILLS or _is_int(v, 0, 255)):
                        errs.append(f"{cat}: {v!r} is not a skill name or number 0-255")
                elif col == "MId":
                    if isinstance(v, dict):
                        _unknown(v, {"gear"}, cat, errs)
                        if not (isinstance(v.get("gear"), str) and _ID_RE.match(v["gear"])):
                            errs.append(f"{cat}.gear must be a gear action id")
                    elif not _is_int(v, 0, 4095):
                        errs.append(f"{cat} must be a model id 0-4095 or {{\"gear\": <action id>}}")
                elif not _is_int(v, -(1 << 31)):
                    errs.append(f"{cat} must be an integer")


def _check_edit(edit, at: str, errs: list) -> tuple | None:
    if not isinstance(edit, dict):
        errs.append(f"{at} must be an object")
        return None
    _unknown(edit, _EDIT_KEYS, at, errs)
    table = edit.get("table")
    if table not in ITEM_LAYOUT and table not in DMSG_SUBS:
        errs.append(f"{at}.table: {table!r} is not a table ({', '.join(tables())})")
        return None
    item = table in ITEM_LAYOUT
    if not _is_int(edit.get("id")):
        errs.append(f"{at}.id must be a record id (an integer >= 0)")
    for key in ("like", "set", "icon", "server"):
        if key in edit and not item:
            errs.append(f"{at}.{key}: only item tables take {key!r} ({table!r} is a text table)")
    if "like" in edit and not _is_int(edit["like"]):
        errs.append(f"{at}.like must be an item id")
    if "note" in edit and not isinstance(edit["note"], str):
        errs.append(f"{at}.note must be a string")
    if item and "set" in edit:
        _check_set(table, edit["set"], f"{at}.set", errs)
    if "strings" in edit:
        _check_strings(table, edit["strings"], f"{at}.strings", errs)
    if item and "icon" in edit:
        _check_icon(edit["icon"], f"{at}.icon", errs)
    if item and "server" in edit:
        _check_server(edit["server"], f"{at}.server", errs)
    if not ({"like", "set", "strings", "icon"} & edit.keys() or edit.get("server")):
        errs.append(f"{at}: the edit changes nothing (give set, strings, icon, like or server)")
    return table, edit.get("id")


def validate_action(action) -> list[str]:
    """Problems with a ``database`` action, each naming the field; ``[]`` when it is valid.
    Mirrors schema/database.json, plus what the schema can't say: which fields and
    sub-strings each table has. Whether an id exists in the install is the build's check."""
    if not isinstance(action, dict):
        return ["the action must be an object"]
    errs: list[str] = []
    _unknown(action, _ACTION_KEYS, "action", errs)
    if action.get("type") != "database":
        errs.append("type must be 'database'")
    if not (isinstance(action.get("id"), str) and _ID_RE.match(action["id"])):
        errs.append("id must be an action id (^[a-z0-9][a-z0-9_.-]*$)")
    if "enabled" in action and not isinstance(action["enabled"], bool):
        errs.append("enabled must be true or false")
    if "description" in action and not isinstance(action["description"], str):
        errs.append("description must be a string")
    deps = action.get("depends_on", [])
    if not (isinstance(deps, list) and all(isinstance(d, str) and _ID_RE.match(d) for d in deps)):
        errs.append("depends_on must be a list of action ids")
    server = action.get("server", {})
    if not isinstance(server, dict):
        errs.append("server must be an object")
    else:
        _unknown(server, _SERVER_KEYS, "server", errs)
        for key in ("emit", "mirror"):
            if key in server and not isinstance(server[key], bool):
                errs.append(f"server.{key} must be true or false")
        if "sql" in server and not (isinstance(server["sql"], str) and server["sql"].lower().endswith(".sql")):
            errs.append("server.sql must be a path ending in .sql")
    edits = action.get("edits")
    if not isinstance(edits, list) or not edits:
        errs.append("edits must be a non-empty list")
        return errs
    seen: dict = {}
    for i, edit in enumerate(edits):
        key = _check_edit(edit, f"edits[{i}]", errs)
        if key and key in seen:
            errs.append(f"edits[{i}]: {key[0]} {key[1]} is already edited by edits[{seen[key]}] — merge them")
        elif key:
            seen[key] = i
    return errs

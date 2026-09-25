"""The ``zone_npcs`` action of ``xi dats`` (schema/zone_npcs.json): a zone's NPC list — the names
the client shows for its NPCs, in the zone's entity-name table — renamed or added, with each new
or changed NPC's ``npc_list`` row written as proposed SQL beside the project (never run).

The entity-name table (file id 6720 + zone, the zone model's + 2600 from zone 256) is a list of
32-byte records, ``char[28]`` name + ``u32`` entity id, sorted by id after a ``none`` / 0 record;
the client finds an NPC's name there by its id (without one it shows "NPC"). An entity id is
``0x01000000 | zone << 12 | local``; a static NPC's ``local`` is 0x001–0x3FF, and custom NPCs
take the band from 0x380 (xi.entity.xi_custom_npc), where no retail mob and almost no retail NPC
sits. ``"id": "auto"`` takes the first local there that neither the name table nor the zone's
event table (whose blocks begin with their actor's id) uses; a rebuild keeps the one it took.

Like the ``database`` action, a build starts from the table as it was before this action (the
records a previous build added come off, renamed ones get their names back), then applies the
NPCs — a rebuild converges and undo is exact.
"""
from __future__ import annotations

import re
import struct
from pathlib import Path

from xi.entity import xi_custom_npc as CN

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_ACTION_KEYS = {"id", "type", "enabled", "description", "depends_on", "zone", "npcs", "server", "result",
                "outputs"}
_NPC_KEYS = {"id", "name", "new", "server", "note"}
_SERVER_ACTION_KEYS = {"emit", "sql"}
NAME_BYTES = 27                    # the client's char[28], NUL-terminated
SERVER_NAME_BYTES = 24             # npc_list.name varbinary(24)
# npc_list columns an NPC's `server` block may set, with their type.
SERVER_FIELDS = {
    "name": str, "polutils_name": str, "model": int, "look": str, "pos": list, "rot": int,
    "flag": int, "speed": int, "speedsub": int, "animation": int, "animationsub": int, "namevis": int,
    "status": int, "entityFlags": int, "name_prefix": int, "content_tag": (str, type(None)), "widescan": int,
}
_UPDATE_COLUMNS = {"pos": ("pos_x", "pos_y", "pos_z"), "rot": ("pos_rot",)}


class ZoneNpcsError(ValueError):
    pass


# ── validation ───────────────────────────────────────────────────────────────

def _is_int(v, lo: int = 0, hi: int | None = None) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= lo and (hi is None or v <= hi)


def _check_server(server, at: str, new: bool, errs: list) -> None:
    if server is False:
        return
    if not isinstance(server, dict):
        errs.append(f"{at} must be an object of npc_list columns, or false")
        return
    for k, v in server.items():
        if k not in SERVER_FIELDS:
            errs.append(f"{at}: unknown column {k!r} ({', '.join(SERVER_FIELDS)})")
        elif k == "pos":
            if not (isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                                                 for x in v)):
                errs.append(f"{at}.pos must be [x, y, z]")
        elif k == "look":
            try:
                ok = len(bytes.fromhex(v)) == 20
            except (TypeError, ValueError):
                ok = False
            if not ok:
                errs.append(f"{at}.look must be the 20-byte look as 40 hex digits")
        elif k == "status" and v not in CN.NPC_STATUS_VALUES:
            errs.append(f"{at}.status must be one of {sorted(CN.NPC_STATUS_VALUES)} "
                        f"({', '.join(f'{s} {n}' for s, n in CN.NPC_STATUS_CHOICES)})")
        elif SERVER_FIELDS[k] is int and not _is_int(v, 0, 0xFFFFFFFF):
            errs.append(f"{at}.{k} must be an integer >= 0")
        elif SERVER_FIELDS[k] is str and not isinstance(v, str):
            errs.append(f"{at}.{k} must be a string")
        elif k == "content_tag" and not (v is None or isinstance(v, str)):
            errs.append(f"{at}.content_tag must be a string or null")
    if new and not ({"model", "look"} & server.keys()):
        errs.append(f"{at}: a new NPC's row needs model (a fixed model id) or look")
    if "model" in server and "look" in server:
        errs.append(f"{at}: give model or look, not both")


def validate_action(action) -> list[str]:
    """Problems with a ``zone_npcs`` action, each naming the field; ``[]`` when valid."""
    if not isinstance(action, dict):
        return ["the action must be an object"]
    errs: list[str] = []
    for k in action:
        if k not in _ACTION_KEYS:
            errs.append(f"action: unknown key {k!r}")
    if action.get("type") != "zone_npcs":
        errs.append("type must be 'zone_npcs'")
    if not (isinstance(action.get("id"), str) and _ID_RE.match(action["id"])):
        errs.append("id must be an action id (^[a-z0-9][a-z0-9_.-]*$)")
    if not _is_int(action.get("zone"), 0, 0xFFF):
        errs.append("zone must be a zone id")
    server = action.get("server", {})
    if not isinstance(server, dict):
        errs.append("server must be an object")
    else:
        for k in server:
            if k not in _SERVER_ACTION_KEYS:
                errs.append(f"server: unknown key {k!r}")
        if "emit" in server and not isinstance(server["emit"], bool):
            errs.append("server.emit must be true or false")
        if "sql" in server and not (isinstance(server["sql"], str) and server["sql"].lower().endswith(".sql")):
            errs.append("server.sql must be a path ending in .sql")
    npcs = action.get("npcs")
    if not isinstance(npcs, list) or not npcs:
        errs.append("npcs must be a non-empty list")
        return errs
    ids, autos = {}, {}
    for i, npc in enumerate(npcs):
        at = f"npcs[{i}]"
        if not isinstance(npc, dict):
            errs.append(f"{at} must be an object")
            continue
        for k in npc:
            if k not in _NPC_KEYS:
                errs.append(f"{at}: unknown key {k!r}")
        new = npc.get("new", False)
        if not isinstance(new, bool):
            errs.append(f"{at}.new must be true or false")
        nid = npc.get("id")
        name = npc.get("name")
        if nid == "auto":
            if not new:
                errs.append(f"{at}: id auto is for a new NPC (\"new\": true)")
            if isinstance(name, str):
                if name in autos:
                    errs.append(f"{at}: another auto NPC is named {name!r} (auto ids are kept by name)")
                autos[name] = i
        elif not _is_int(nid, CN.NPC_STATIC_TARGID_MIN, CN.NPC_STATIC_TARGID_MAX):
            errs.append(f"{at}.id must be the NPC's zone-local id "
                        f"(0x{CN.NPC_STATIC_TARGID_MIN:X}-0x{CN.NPC_STATIC_TARGID_MAX:X}) or \"auto\"")
        elif nid in ids:
            errs.append(f"{at}: NPC {nid:#x} is already in npcs[{ids[nid]}] — merge them")
        else:
            ids[nid] = i
        if name is not None or new:
            if not isinstance(name, str) or not name:
                errs.append(f"{at}.name must be the NPC's name")
            else:
                try:
                    if len(name.encode("cp932")) > NAME_BYTES:
                        errs.append(f"{at}.name is longer than the client's {NAME_BYTES} bytes")
                except UnicodeEncodeError:
                    errs.append(f"{at}.name has characters the client can't show (cp932)")
        if "server" in npc:
            _check_server(npc["server"], f"{at}.server", bool(new), errs)
        elif new:
            errs.append(f"{at}: a new NPC needs its server row (server: {{model or look, …}}), or server: false")
        if not new and name is None and not npc.get("server"):
            errs.append(f"{at}: the entry changes nothing (give name or server)")
        if "note" in npc and not isinstance(npc["note"], str):
            errs.append(f"{at}.note must be a string")
    return errs


# ── the zone's tables ────────────────────────────────────────────────────────

def _resolve(root, fid: int) -> str | None:
    from xi.ftable.xi_core import resolve_dat_in_root, scan_file_ids
    dat, _rom = resolve_dat_in_root(root, fid)
    if dat:
        return dat
    hits = scan_file_ids([fid])
    return hits[0]["dat"] if hits else None


def names_rel(root, zone: int) -> str | None:
    from xi.zone.xi_inject import zone_npc_file_id
    return _resolve(root, zone_npc_file_id(zone))


def parse_names(data: bytes) -> list[tuple[str, int]]:
    """``[(name, entity id)]`` of an entity-name table."""
    out = []
    for i in range(len(data) // 32):
        raw = data[i * 32:i * 32 + 28].split(b"\0", 1)[0]
        out.append((raw.decode("cp932", "replace"), struct.unpack_from("<I", data, i * 32 + 28)[0]))
    return out


def _name_of(data: bytes, sid: int) -> str | None:
    for name, rsid in parse_names(data):
        if rsid == sid:
            return name
    return None


def event_actors(root, zone: int) -> set[int]:
    """Entity ids the zone's event table has a block for (the retail NPCs a DB may lack)."""
    from xi.dats import xi_stage
    from xi.zone.xi_inject import zone_event_file_id
    rel = _resolve(root, zone_event_file_id(zone))
    data = xi_stage.read(root, rel) if rel else None
    if data is None:
        return set()
    try:
        cnt = struct.unpack_from("<I", data, 0)[0]
        sizes = struct.unpack_from(f"<{cnt}I", data, 4)
    except struct.error:
        return set()
    off, out = 4 + 4 * cnt, set()
    for s in sizes:
        if off + 4 <= len(data):
            out.add(struct.unpack_from("<I", data, off)[0])
        off += s
    return out


def free_local(taken: set[int]) -> int:
    for local in range(CN.CUSTOM_NPC_LOCAL_START, CN.NPC_STATIC_TARGID_MAX + 1):
        if local not in taken:
            return local
    raise ZoneNpcsError(f"the custom NPC band (0x{CN.CUSTOM_NPC_LOCAL_START:X}-"
                        f"0x{CN.NPC_STATIC_TARGID_MAX:X}) is full in this zone")


# ── build / undo ─────────────────────────────────────────────────────────────

def _restore(data: bytes, entry: dict, warnings: list) -> bytes:
    sid = entry["sid"]
    cur = _name_of(data, sid)
    label = f"NPC {sid:#010x}"
    if cur != entry["to"]:
        warnings.append(f"{label}: its name changed since the last build ({cur!r}); left as it is")
        return data
    if entry.get("created"):
        return CN.remove_name_record(data, sid)
    return CN.inject_name_record(data, sid, entry["from"])


def _row(npc: dict, sid: int) -> dict:
    """The full npc_list row of a new NPC: the custom-NPC defaults, then its own columns."""
    s = dict(npc.get("server") or {})
    d = CN.NPC_DEFAULTS
    name = npc["name"]
    look = CN.look_bytes(s["model"]) if "model" in s else bytes.fromhex(s["look"])
    pos = s.get("pos", [0, 0, 0])
    return {
        "npcid": sid,
        "name": s.get("name", name.replace(" ", "_").replace("'", "")),
        "polutils_name": s.get("polutils_name", name),
        "pos_rot": s.get("rot", 0), "pos_x": float(pos[0]), "pos_y": float(pos[1]), "pos_z": float(pos[2]),
        "flag": s.get("flag", d["flag"]), "speed": s.get("speed", d["speed"]),
        "speedsub": s.get("speedsub", d["speedsub"]), "animation": s.get("animation", d["animation"]),
        "animationsub": s.get("animationsub", d["animationsub"]), "namevis": s.get("namevis", d["namevis"]),
        "status": s.get("status", d["status"]), "entityFlags": s.get("entityFlags", d["entityFlags"]),
        "look": look, "name_prefix": s.get("name_prefix", d["name_prefix"]),
        "content_tag": s.get("content_tag"), "widescan": s.get("widescan", d["widescan"]),
    }


def _sql_value(v) -> str:
    from xi.database.xi_build import _sql_value as sv
    if isinstance(v, (bytes, bytearray)):
        return "0x" + bytes(v).hex().upper()
    if isinstance(v, float):
        return f"{v:.3f}"
    return sv(v)


def _npc_sql(npc: dict, sid: int, zone: int, warnings: list) -> list[str]:
    head = f"-- {npc.get('name') or ''} {sid:#010x} (zone {zone})".rstrip() + (f": {npc['note']}" if npc.get("note") else "")
    if npc.get("new"):
        row = _row(npc, sid)
        if len(row["name"].encode("utf-8")) > SERVER_NAME_BYTES:
            warnings.append(f"NPC {sid:#010x}: npc_list.name {row['name']!r} is longer than {SERVER_NAME_BYTES} "
                            "bytes; give server.name")
        cols = ", ".join(f"`{c}`" for c in CN.NPC_COLUMNS)
        vals = ", ".join(_sql_value(row[c]) for c in CN.NPC_COLUMNS)
        tail = "" if row["status"] != CN.NPC_STATUS_CUTSCENE_ONLY else "  -- status 6: shown only by an event"
        return [head, f"REPLACE INTO `npc_list` ({cols}) VALUES ({vals});{tail}", ""]
    s = dict(npc.get("server") or {})
    if npc.get("name") is not None:
        # The display name follows the rename; npc_list.name stays, it binds the NPC's Lua script.
        s.setdefault("polutils_name", npc["name"])
    sets = []
    for k, v in s.items():
        if k == "model":
            sets.append(f"`look` = {_sql_value(CN.look_bytes(v))}")
        elif k == "look":
            sets.append(f"`look` = {_sql_value(bytes.fromhex(v))}")
        elif k in _UPDATE_COLUMNS:
            vals = v if isinstance(v, list) else [v]
            sets += [f"`{c}` = {_sql_value(float(x) if k == 'pos' else x)}" for c, x in zip(_UPDATE_COLUMNS[k], vals)]
        else:
            sets.append(f"`{k}` = {_sql_value(v)}")
    return [head, f"UPDATE `npc_list` SET {', '.join(sets)} WHERE `npcid` = {sid};", ""] if sets else []


def build(action: dict, *, root: Path, target: str | None, manifest: dict | None = None,
          sql_path: Path | None = None, project: str = "", force: bool = False, dry_run: bool = False,
          unwound: bool = False) -> dict:
    """Apply ``action`` to the zone's entity-name table in ``root`` and write the proposed SQL;
    the build result (``records`` is what gets recorded for this root). ``unwound``: the
    previous build's names were already put back (``xi dats build`` does that first)."""
    from xi.database import xi_build as DB
    from xi.dats import xi_stage
    errs = validate_action(action)
    if errs:
        raise ZoneNpcsError("; ".join(errs))
    zone = action["zone"]
    rel = names_rel(root, zone)
    orig = xi_stage.read(root, rel) if rel else None
    if orig is None:
        raise ZoneNpcsError(f"zone {zone} has no entity-name table in this install")
    prev_result = action.get("result") or {}
    prev = ((prev_result.get("roots") or {}).get(target)) or []
    warnings: list[str] = []
    data = orig
    for entry in ([] if unwound else reversed(prev)):
        data = _restore(data, entry, warnings)
    kept = {e["name_key"]: e["local"] for e in prev if e.get("name_key")}     # auto ids a build took
    taken = {sid & 0xFFF for _n, sid in parse_names(data) if (sid >> 12) & 0xFFF == zone}
    taken |= {a & 0xFFF for a in event_actors(root, zone) if (a >> 12) & 0xFFF == zone}
    taken |= {n["id"] for n in action["npcs"] if isinstance(n.get("id"), int)}
    records, sql_lines = [], []
    for i, npc in enumerate(action["npcs"]):
        auto = npc.get("id") == "auto"
        if auto:
            local = kept.get(npc["name"])
            if local is None or local in taken:
                local = free_local(taken)
            taken.add(local)
        else:
            local = npc["id"]
        sid = CN.make_npcid(zone, local)
        cur = _name_of(data, sid)
        try:
            if npc.get("new"):
                if cur is not None and not force:
                    raise ZoneNpcsError(f"zone {zone} already has NPC {local:#x} ({cur!r}); pick another id, "
                                        "\"auto\", or --force")
                if local < CN.CUSTOM_NPC_LOCAL_START:
                    warnings.append(f"NPC {local:#x} is below the custom band (0x{CN.CUSTOM_NPC_LOCAL_START:X}+), "
                                    "where retail updates add NPCs")
                data = CN.inject_name_record(data, sid, npc["name"])
                entry = {"zone": zone, "dat": rel, "sid": sid, "local": local, "from": cur,
                         "to": npc["name"], "created": cur is None}
            else:
                if cur is None:
                    raise ZoneNpcsError(f"zone {zone} has no NPC {local:#x}; mark it \"new\": true to add it")
                entry = None
                if npc.get("name") is not None and npc["name"] != cur:
                    data = CN.inject_name_record(data, sid, npc["name"])
                    entry = {"zone": zone, "dat": rel, "sid": sid, "local": local, "from": cur, "to": npc["name"]}
            if auto and entry is not None:
                entry["name_key"] = npc["name"]
            if entry is not None and (entry.get("created") or entry["from"] != entry["to"]):
                records.append(entry)
            if npc.get("server") is not False and (npc.get("new") or npc.get("server") or npc.get("name")):
                sql_lines += _npc_sql(npc, sid, zone, warnings)
        except ZoneNpcsError as e:
            raise ZoneNpcsError(f"npcs[{i}]: {e}") from None
    sql = ("\n".join(sql_lines).rstrip() + "\n") if sql_lines and (action.get("server") or {}).get("emit", True) else ""
    written = []
    if data != orig:
        out = xi_stage.write(root, rel, data, dry_run)
        if out is not None:
            written.append(str(out))
    warnings += [f"the pivot folder has its own {r}, which the client loads instead of this one — "
                 "build with --pivot to change what players see"
                 for r in DB._pivot_shadow(Path(root), target, [rel] if data != orig else [])]
    if sql and sql_path is not None and not dry_run:
        DB.write_sql_section(sql_path, action["id"], sql, project)
    return {"id": action["id"], "type": "zone_npcs", "zone": zone, "target": target, "records": records,
            "files": [rel] if data != orig else [], "written": written, "warnings": warnings,
            "sql": sql or None, "server": str(sql_path) if sql and sql_path is not None else None}


def undo(action: dict, root: Path, target: str, dry_run: bool = False) -> tuple[int, list[str]]:
    """Put the zone's name table back as it was before ``action``; ``(records, warnings)``."""
    from xi.dats import xi_stage
    entries = (((action.get("result") or {}).get("roots") or {}).get(target)) or []
    warnings: list[str] = []
    if not entries:
        return 0, warnings
    rel = entries[0]["dat"]
    orig = xi_stage.read(root, rel)
    if orig is None:
        return 0, [f"{rel} is gone; nothing to put back"]
    data = orig
    for entry in reversed(entries):
        data = _restore(data, entry, warnings)
    if data != orig:
        xi_stage.write(root, rel, data, dry_run)
    return len(entries), warnings


def describe(root: Path, zone: int) -> dict:
    """The zone's NPC names and the next free custom id: ``{dat, names: [(name, local)], free}``."""
    from xi.menu.xi_menu_table import dat_path
    rel = names_rel(root, zone)
    if not rel or not dat_path(root, rel).is_file():
        raise ZoneNpcsError(f"zone {zone} has no entity-name table in this install")
    names = [(n, sid & 0xFFF) for n, sid in parse_names(dat_path(root, rel).read_bytes())
             if (sid >> 12) & 0xFFF == zone]
    taken = {local for _n, local in names} | {a & 0xFFF for a in event_actors(root, zone)
                                                if (a >> 12) & 0xFFF == zone}
    return {"dat": rel, "names": names, "free": free_local(taken)}

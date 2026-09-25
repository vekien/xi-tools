"""The ``zone_events`` action of ``xi dats`` (schema/zone_events.json): events put on a zone's
NPCs. Each event is a cutscene (``xi.cutscene.v1``, what the zone editor authors and
``xi event decompile`` writes) or a plain dialogue (lines an NPC says, one box each or one
paged box). It is compiled into the zone's event table, with its lines in the zone's
dialog tables.

The compile changes the event table's blocks. The owner's block gets the event, and each
cast NPC gets a one-byte block so the client requests it. The dialog tables change by the
lines the event prints. A build records, per event, each block it changed as the splice
that puts it back, and each line as zone_dialog does. Every build starts from the tables
as they were before this action, so a rebuild converges and undo is exact. An ``auto``
event keeps the id its first build took (the server script names it). A camera track
gets its own scene DAT, placed and registered like any DAT a ``xi dats`` action places.

What the server needs is written beside the project, never run: the SQL that hides
the cast's names (``flags.hideNpcNames``), and the Lua that starts each event.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from xi.dialog import xi_dialog as XD
from xi.dialog import xi_zone_dialog as ZD
from xi.event import xi_cutscene_publish as CP
from xi.event import xi_event as core

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_PLACE_RE = re.compile(r"^ROM\d*/\d+/\d+\.DAT$", re.I)
_ACTION_KEYS = {"id", "type", "enabled", "description", "depends_on", "zone", "events", "server", "result",
                "outputs"}
_EVENT_KEYS = {"name", "cutscene", "dialogue", "eventId", "camera", "cameraFileId", "note"}
_DIALOGUE_KEYS = {"actor", "lines", "paged"}
_SERVER_KEYS = {"emit", "sql", "lua"}
SCHEMAS = ("xi.cutscene.v1", "cexi.cutscene.v1")      # CatsEyeXI's editor fork writes the same format
NAMEVIS_HIDE = CP.NAMEVIS_HIDE          # npc_list.namevis: 0x20 hides the name (not 0x08)


class ZoneEventsError(ValueError):
    pass


# ── validation ───────────────────────────────────────────────────────────────

def _is_int(v, lo: int = 0, hi: int | None = None) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= lo and (hi is None or v <= hi)


def _entity(v) -> int | None:
    if _is_int(v, 0, 0xFFFFFFFF):
        return v
    if isinstance(v, str) and re.match(r"^0x[0-9a-fA-F]{1,8}$", v):
        return int(v, 16)
    return None


def event_key(ev: dict) -> str | None:
    """What an event is known by in the action's result: its name, else its cutscene file's stem."""
    if isinstance(ev.get("name"), str) and ev["name"]:
        return ev["name"]
    if isinstance(ev.get("cutscene"), str) and ev["cutscene"]:
        return Path(ev["cutscene"]).stem
    return None


def validate_action(action) -> list[str]:
    """Problems with a ``zone_events`` action, each naming the field; ``[]`` when valid."""
    if not isinstance(action, dict):
        return ["the action must be an object"]
    errs: list[str] = []
    for k in action:
        if k not in _ACTION_KEYS:
            errs.append(f"action: unknown key {k!r}")
    if action.get("type") != "zone_events":
        errs.append("type must be 'zone_events'")
    if not (isinstance(action.get("id"), str) and _ID_RE.match(action["id"])):
        errs.append("id must be an action id (^[a-z0-9][a-z0-9_.-]*$)")
    if not _is_int(action.get("zone"), 0, 0xFFFF):
        errs.append("zone must be a zone id")
    if "enabled" in action and not isinstance(action["enabled"], bool):
        errs.append("enabled must be true or false")
    server = action.get("server")
    if server is not None:
        if not isinstance(server, dict):
            errs.append("server must be an object")
        else:
            for k in server:
                if k not in _SERVER_KEYS:
                    errs.append(f"server: unknown key {k!r}")
            if "emit" in server and not isinstance(server["emit"], bool):
                errs.append("server.emit must be true or false")
            for k in ("sql", "lua"):
                if k in server and not (isinstance(server[k], str) and server[k]):
                    errs.append(f"server.{k} must be a file path")
    events = action.get("events")
    if not isinstance(events, list) or not events:
        errs.append("events must be a non-empty list")
        return errs
    keys: dict = {}
    for i, ev in enumerate(events):
        at = f"events[{i}]"
        if not isinstance(ev, dict):
            errs.append(f"{at} must be an object")
            continue
        for k in ev:
            if k not in _EVENT_KEYS:
                errs.append(f"{at}: unknown key {k!r}")
        if ("cutscene" in ev) == ("dialogue" in ev):
            errs.append(f"{at}: give cutscene or dialogue (one of them)")
        if "name" in ev and not (isinstance(ev["name"], str) and ev["name"].strip()):
            errs.append(f"{at}.name must be a non-empty string")
        cs = ev.get("cutscene")
        if "cutscene" in ev:
            if isinstance(cs, dict):
                if cs.get("schema") not in SCHEMAS:
                    errs.append(f"{at}.cutscene.schema must be \"xi.cutscene.v1\"")
            elif not (isinstance(cs, str) and cs):
                errs.append(f"{at}.cutscene must be a cutscene file (a path) or the cutscene itself")
        dlg = ev.get("dialogue")
        if "dialogue" in ev:
            if not isinstance(dlg, dict):
                errs.append(f"{at}.dialogue must be {{actor, lines, paged?}}")
            else:
                for k in dlg:
                    if k not in _DIALOGUE_KEYS:
                        errs.append(f"{at}.dialogue: unknown key {k!r}")
                if _entity(dlg.get("actor")) is None:
                    errs.append(f"{at}.dialogue.actor must be the NPC's entity id (0x01…)")
                lines = dlg.get("lines")
                if not (isinstance(lines, list) and lines and all(isinstance(s, str) and s for s in lines)):
                    errs.append(f"{at}.dialogue.lines must be a non-empty list of text")
                if "paged" in dlg and not isinstance(dlg["paged"], bool):
                    errs.append(f"{at}.dialogue.paged must be true or false")
        if "eventId" in ev and not (ev["eventId"] == "auto" or _is_int(ev["eventId"], 0, 0xFFFD)):
            errs.append(f"{at}.eventId must be \"auto\" or an event id (0–65533)")
        if "camera" in ev:
            if "dialogue" in ev:
                errs.append(f"{at}.camera: a dialogue has no camera")
            elif not (isinstance(ev["camera"], str) and _PLACE_RE.match(ev["camera"])):
                errs.append(f"{at}.camera must be where the camera scene DAT goes, like ROM10/490/55.DAT")
        if "cameraFileId" in ev and not CP.camera_scene_id_safe(ev["cameraFileId"]):
            errs.append(f"{at}.cameraFileId must be in the safe camera band ({CP.MID_BAND[0]}–{CP.MID_BAND[1]})")
        if "note" in ev and not isinstance(ev["note"], str):
            errs.append(f"{at}.note must be a string")
        key = event_key(ev)
        if key is None:
            if "cutscene" in ev or "dialogue" in ev:
                errs.append(f"{at}: an inline event needs a name")
        elif key in keys:
            errs.append(f"{at}: the name {key!r} is taken by events[{keys[key]}] — give them names")
        else:
            keys[key] = i
    return errs


# ── the cutscene ─────────────────────────────────────────────────────────────

def load_cutscene(spec, resolve) -> tuple[dict, Path | None]:
    """The cutscene an event names, with its cast and dialog inlined (a path in either is
    relative to the cutscene file) → ``(cutscene, file)``. ``resolve(path)`` finds a path the
    action names."""
    src = None
    if isinstance(spec, str):
        src = Path(resolve(spec))
        try:
            cs = json.loads(src.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ZoneEventsError(f"{spec}: {e}")
    else:
        cs = copy.deepcopy(spec)
    if not isinstance(cs, dict) or cs.get("schema") not in SCHEMAS:
        raise ZoneEventsError(f"{spec if src else 'cutscene'}: not a xi.cutscene.v1 cutscene")
    cs["schema"] = "xi.cutscene.v1"
    for part in ("cast", "dialog"):
        if isinstance(cs.get(part), str):
            p = Path(cs[part])
            p = p if p.is_absolute() else ((src.parent if src else Path.cwd()) / p)
            try:
                cs[part] = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                raise ZoneEventsError(f"{part} {cs[part]}: {e}")
    return cs, src


# ── the zone's tables ────────────────────────────────────────────────────────

def _resolve(root, fid: int) -> str | None:
    from xi.ftable.xi_core import resolve_dat_in_root, scan_file_ids
    dat, _rom = resolve_dat_in_root(root, fid)
    if dat:
        return dat
    hits = scan_file_ids([fid])
    return hits[0]["dat"] if hits else None


def event_rel(root, zone: int) -> str | None:
    from xi.zone.xi_inject import zone_event_file_id
    return _resolve(root, zone_event_file_id(zone))


def _sha1(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


def _splice(before, after) -> list:
    """``before`` as ``[p, s, middle]``: ``after[:p] + middle + after[len(after)-s:]``."""
    n = min(len(before), len(after))
    p = 0
    while p < n and before[p] == after[p]:
        p += 1
    s = 0
    while s < n - p and before[len(before) - 1 - s] == after[len(after) - 1 - s]:
        s += 1
    mid = before[p:len(before) - s]
    return [p, s, bytes(mid).hex() if isinstance(before, (bytes, bytearray)) else list(mid)]


def _unsplice(after, sp: list, as_bytes: bool):
    p, s, mid = sp
    mid = bytes.fromhex(mid) if as_bytes else list(mid)
    return after[:p] + mid + after[len(after) - s:]


def _block_records(before: bytes, after: bytes) -> list[dict]:
    """Each actor block the compile changed or added, as what puts it back. A compile never
    reorders blocks (a new one goes on the end), so blocks pair up by position."""
    old, new = core.parse_raw_actors(before), core.parse_raw_actors(after)
    if len(new) < len(old) or any(a.actor_id != b.actor_id for a, b in zip(old, new)):
        raise ZoneEventsError("the compile reordered the event table's blocks; nothing can put that back")
    out = []
    for i, a in enumerate(new):
        rec = {"index": i, "actor": f"0x{a.actor_id:08X}", "sha1": _sha1(a.raw_block)}
        if i >= len(old):
            out.append({**rec, "created": True})
            continue
        b = old[i]
        if b.raw_block == a.raw_block:
            continue
        for name, field in (("offsets", "event_offsets"), ("ids", "event_ids"), ("refs", "references")):
            x, y = list(getattr(b, field)), list(getattr(a, field))
            if x != y:
                rec[name] = _splice(x, y)
        if bytes(b.scene_data) != bytes(a.scene_data):
            rec["scene"] = _splice(bytes(b.scene_data), bytes(a.scene_data))
        rec["pad"] = bytes(b.block_pad).hex()
        out.append(rec)
    return out


def _take_out(a, event_id: int) -> bool:
    """Take ``event_id`` out of block ``a`` on its own: its table entry, and its bytecode when
    nothing follows it (as the compiler reclaims a replaced event). True when it was there."""
    if event_id not in a.event_ids:
        return False
    k = a.event_ids.index(event_id)
    off = a.event_offsets[k]
    del a.event_ids[k]
    del a.event_offsets[k]
    if not a.event_offsets or off >= max(a.event_offsets):
        a.scene_data = bytes(a.scene_data)[:off]
    a.dirty = True
    return True


def _restore_blocks(data: bytes, blocks: list[dict], event_id, label: str, warnings: list) -> bytes:
    actors = core.parse_raw_actors(data)
    for rec in reversed(blocks):
        i, aid = rec["index"], int(rec["actor"], 16)
        if i >= len(actors) or actors[i].actor_id != aid:
            i = next((k for k, a in enumerate(actors) if a.actor_id == aid), None)
        if i is None:
            warnings.append(f"{label}: NPC {rec['actor']} has no events now; nothing to put back")
            continue
        if _sha1(actors[i].raw_block) != rec["sha1"]:
            # Something else (another project) changed the block since: take just this event out.
            if isinstance(event_id, int) and _take_out(actors[i], event_id):
                warnings.append(f"{label}: NPC {rec['actor']}'s events changed since the last build; "
                                f"took event {event_id} out on its own")
            else:
                warnings.append(f"{label}: NPC {rec['actor']}'s events changed since the last build; "
                                "left as they are")
            continue
        a = actors[i]
        if rec.get("created"):
            del actors[i]
            continue
        restored = core.RawActor(
            aid,
            _unsplice(list(a.event_offsets), rec["offsets"], False) if "offsets" in rec else list(a.event_offsets),
            _unsplice(list(a.event_ids), rec["ids"], False) if "ids" in rec else list(a.event_ids),
            _unsplice(list(a.references), rec["refs"], False) if "refs" in rec else list(a.references),
            _unsplice(bytes(a.scene_data), rec["scene"], True) if "scene" in rec else bytes(a.scene_data),
            bytes.fromhex(rec.get("pad", "")), b"")
        restored.raw_block = core.serialize_actor(restored)
        actors[i] = restored
    return core.build_event_dat(actors)


def _line_records(zone: int, lang: str, rel: str, before: list, after: list) -> list[dict]:
    out = [ZD._entry(zone, lang, rel, i, before[i], after[i])
           for i in range(min(len(before), len(after))) if before[i] != after[i]]
    if len(after) > len(before):
        grown = after[len(before):]
        out.append({"zone": zone, "lang": lang, "dat": rel, "id": len(before), "created": True,
                    "grew": [len(before), len(after)], "to": [ZD.line_text(b) for b in grown],
                    "to_hex": [b.hex() for b in grown]})
    return out


def _slots(rec: dict | None, blobs: list) -> list[int]:
    """The English line ids a previous build of this event took that are free for it again
    (blank, or just past the end): its lines go back into them, so no id moves."""
    ids = []
    for e in (rec or {}).get("lines") or []:
        if e.get("lang") != "en":
            continue
        if e.get("grew"):
            ids += range(*e["grew"])
        elif e.get("from_hex") == ZD.BLANK.hex():
            ids.append(e["id"])
    return [i for i in sorted(set(ids)) if i >= len(blobs) or blobs[i] == ZD.BLANK]


def _restore(event_data: bytes | None, tables: ZD._Tables, rec: dict, warnings: list) -> bytes | None:
    label = f"zone {rec.get('zone')} event {rec.get('event')} ({rec.get('name')})"
    for entry in reversed(rec.get("lines") or []):
        ZD._restore(tables, entry, warnings)
    if rec.get("blocks"):
        if event_data is None:
            warnings.append(f"{label}: {rec.get('dat')} is gone; nothing to put back")
        else:
            event_data = _restore_blocks(event_data, rec["blocks"], rec.get("event"), label, warnings)
    return event_data


# ── build / undo ─────────────────────────────────────────────────────────────

def _recorded(action: dict, target: str | None, key: str) -> dict | None:
    """This event's record from the last build: this target's, else another's."""
    roots = ((action.get("result") or {}).get("roots")) or {}
    for name in [target, *sorted(k for k in roots if k != target)]:
        for rec in roots.get(name) or []:
            if rec.get("name") == key:
                return rec
    return None


def _recorded_cameras(manifest: dict | None, skip_action: str) -> set[int]:
    out = set()
    for a in (manifest or {}).get("actions") or []:
        if a.get("type") != "zone_events" or a.get("id") == skip_action:
            continue
        for recs in (((a.get("result") or {}).get("roots")) or {}).values():
            out |= {r["camera"]["file_id"] for r in recs if isinstance(r.get("camera"), dict)}
    return out


def project_looks(manifest: dict | None, zone: int) -> dict[int, dict]:
    """The looks the project's ``zone_npcs`` actions give their new NPCs in ``zone``
    (``{entity: {name, look}}``), so a cast NPC the project adds resolves without a server."""
    from xi.entity import xi_custom_npc as CN
    out: dict[int, dict] = {}
    for a in (manifest or {}).get("actions") or []:
        if a.get("type") != "zone_npcs" or a.get("zone") != zone:
            continue
        roots = ((a.get("result") or {}).get("roots")) or {}
        taken = {r.get("name_key") or r.get("to"): r.get("sid") for recs in roots.values() for r in recs}
        for npc in a.get("npcs") or []:
            server = npc.get("server") if isinstance(npc.get("server"), dict) else {}
            sid = CN.make_npcid(zone, npc["id"]) if isinstance(npc.get("id"), int) else taken.get(npc.get("name"))
            if not sid or not ({"model", "look"} & server.keys()):
                continue
            look = CN.look_bytes(server["model"]) if "model" in server else bytes.fromhex(server["look"])
            out[int(sid)] = {"name": npc.get("name") or "", "look": look}
    return out


def _cast_entities(cs: dict, owner: int | None) -> list[int]:
    from xi.event import xi_compile
    out = []
    for c in (cs.get("cast") or {}).get("cast") or []:
        try:
            ent = xi_compile._resolve_entity(c.get("entity"))
        except xi_compile.CutsceneCompileError:
            continue
        if ent != owner and (ent & 0xFF000000) and (ent >> 24) != 0x7F:
            out.append(ent)
    return sorted(set(out))


def _names(root, zone: int) -> dict[int, str]:
    from xi.dats import xi_stage
    from xi.entity.xi_zone_npcs import names_rel, parse_names
    rel = names_rel(root, zone)
    data = xi_stage.read(root, rel) if rel else None
    return {sid: n for n, sid in parse_names(data)} if data else {}


def build(action: dict, *, root: Path, target: str | None, resolve, manifest: dict | None = None,
          project: str = "", force: bool = False, dry_run: bool = False, unwound: bool = False) -> dict:
    """Compile ``action``'s events into the zone's tables in ``root``. The build result:
    ``records`` (what gets recorded for this root), ``cameras`` (scene DATs to place:
    ``{file_id, dat, data}``), ``sql`` / ``lua`` (the server's part), warnings.
    ``unwound``: the previous build's changes were already put back (``xi dats build``
    does that for the whole project first)."""
    from xi import xi_config
    from xi.dats import xi_stage
    from xi.event import xi_author, xi_compile

    errs = validate_action(action)
    if errs:
        raise ZoneEventsError("; ".join(errs))
    zone = action["zone"]
    rel = event_rel(root, zone)
    event_data = xi_stage.read(root, rel) if rel else None
    if event_data is None:
        raise ZoneEventsError(f"zone {zone} has no event table in this install")
    orig_event = event_data
    tables = ZD._Tables(Path(root), zone)
    warnings: list[str] = []
    prev = (((action.get("result") or {}).get("roots") or {}).get(target)) or []
    for rec in ([] if unwound else reversed(prev)):
        event_data = _restore(event_data, tables, rec, warnings)
    if tables.load("en") is None:
        raise ZoneEventsError(f"zone {zone} has no English dialog table")
    tables.load("jp")

    looks = project_looks(manifest, zone)

    def look_rows(ids):
        rows = {i: looks[i] for i in ids if i in looks}
        rest = [i for i in ids if i not in rows]
        if rest:
            rows.update(CP.default_look_rows(rest))
        return rows

    names = _names(root, zone)
    prev_by_key = {r.get("name"): r for r in prev}
    skip_cams = _recorded_cameras(manifest, action["id"]) | xi_stage.claimed("camera_file_id")
    records, cameras, sql, lua = [], [], [], []
    for i, ev in enumerate(action["events"]):
        key = event_key(ev)
        at = f"events[{i}] ({key})"
        before_event = event_data
        before = {lang: list(tables.blobs[lang]) for lang in tables.blobs}
        en_dat = XD.build_container(tables.blobs["en"], tables.obf["en"])
        old = _recorded(action, target, key)
        mine = prev_by_key.get(key)           # this root's last build: its line ids go back in
        slots = _slots(mine, tables.blobs["en"])
        cam = None
        try:
            if "dialogue" in ev:
                dlg = ev["dialogue"]
                owner = _entity(dlg["actor"])
                pinned = ev.get("eventId", "auto")
                kept = old.get("event") if old and old.get("actor") == f"0x{owner:08X}" else None
                event_id = None if pinned == "auto" and kept is None else (kept if pinned == "auto" else pinned)
                new_dialog, msg_ids = xi_author.append_dialog_lines(en_dat, dlg["lines"], paged=bool(dlg.get("paged")),
                                                                     reuse_ids=slots)
                actors = core.parse_raw_actors(event_data)
                try:
                    event_id, _created = xi_author.add_dialogue_event(actors, owner, msg_ids, event_id)
                except ValueError as e:
                    if kept is not None and pinned == "auto":
                        raise ZoneEventsError(f"event {kept}, which its last build took, is taken on NPC "
                                              f"0x{owner:08X} now (a client update?) — give eventId") from None
                    raise ZoneEventsError(str(e)) from None
                event_data, dialog_out = core.build_event_dat(actors), new_dialog
                errors, lint_warn = CP.lint(event_data, dialog_out, owner, event_id)
                if errors:
                    raise ZoneEventsError("lint: " + "; ".join(errors))
                warnings += [f"{at}: {w}" for w in lint_warn]
                stub = xi_author.lua_stub(owner, event_id, names.get(owner))
                hide = []
            else:
                cs, _src = load_cutscene(ev["cutscene"], resolve)
                if cs.get("zone") is not None and cs.get("zone") != zone:
                    raise ZoneEventsError(f"the cutscene is for zone {cs.get('zone')}, not {zone}")
                owner = CP.owner_entity(cs)
                if owner is None:
                    raise ZoneEventsError(f"the cutscene's actor {cs.get('actor')!r} isn't in its cast")
                pinned = ev.get("eventId", cs.get("eventId", "auto"))
                if pinned == "auto" and old and old.get("actor") == f"0x{owner:08X}":
                    kept = old["event"]
                    actor = next((a for a in core.parse_raw_actors(event_data) if a.actor_id == owner), None)
                    if actor is not None and kept in actor.event_ids:
                        raise ZoneEventsError(f"event {kept}, which its last build took, is taken on NPC "
                                              f"0x{owner:08X} now (a client update?) — give eventId")
                    pinned = kept
                cs["eventId"] = pinned
                cam_p = None
                if CP.has_camera_track(cs):
                    place = ev.get("camera") or CP.camera_dat_rel(cs)
                    if not place:
                        raise ZoneEventsError("the cutscene has a camera; give camera: where its scene DAT goes "
                                              "(like ROM10/490/55.DAT)")
                    place = place.replace("\\", "/")
                    fid = ev.get("cameraFileId") or ((old or {}).get("camera") or {}).get("file_id")
                    if fid is None and CP.camera_scene_id_safe(cs.get("cameraSceneFileId")) \
                            and int(cs["cameraSceneFileId"]) not in skip_cams:
                        fid = int(cs["cameraSceneFileId"])
                    if fid is None:
                        fid = CP.free_camera_file_id(skip_cams)
                    if not CP.camera_scene_id_safe(fid):
                        raise ZoneEventsError(f"camera file id {fid} is outside the safe band "
                                              f"({CP.MID_BAND[0]}–{CP.MID_BAND[1]})")
                    hit = CP.camera_path_collision([root], place, fid)
                    if hit:
                        raise ZoneEventsError(hit)
                    skip_cams.add(fid)
                    xi_stage.claim("camera_file_id", fid)
                    cam = {"file_id": fid, "dat": place}
                    cam_p = CP.scene_p_for(fid)
                motions, bank, norm_warn = CP.prepare_anim(cs, look_rows)
                res = xi_compile.compile_cutscene(cs, event_data, en_dat, camera_scene_ref=cam_p,
                                                  dialog_slots=slots,
                                                  cast_motions=motions, bank_tags=bank,
                                                  ffxi_dir=Path(xi_config.FFXI_DIR) if xi_config.FFXI_DIR else None)
                errors, lint_warn = CP.lint(res.event_dat, res.dialog_dat, owner, res.event_id)
                if errors:
                    raise ZoneEventsError("lint: " + "; ".join(errors))
                warnings += [f"{at}: {w}" for w in [*norm_warn, *res.warnings, *lint_warn]]
                event_id, event_data, dialog_out, stub = res.event_id, res.event_dat, res.dialog_dat, res.lua_stub
                if cam is not None:
                    if res.scene_dat:
                        cameras.append({**cam, "data": res.scene_dat, "event": key})
                    else:
                        cam = None
                hide = _cast_entities(cs, owner) if (cs.get("flags") or {}).get("hideNpcNames") else []
        except (xi_compile.CutsceneCompileError, NotImplementedError, XD.DialogError, ValueError) as e:
            raise ZoneEventsError(f"{at}: {e}") from None

        tables.blobs["en"], _obf = XD.raw_entry_blobs(dialog_out)
        # The Japanese table gets the same lines at the same ids, so either client prints them.
        jp = tables.blobs.get("jp")
        if jp is not None:
            en = tables.blobs["en"]
            for lid in range(len(en)):
                changed = lid >= len(before["en"]) or en[lid] != before["en"][lid]
                if changed:
                    while len(jp) < lid:
                        jp.append(b"\x00")
                    if lid < len(jp):
                        jp[lid] = en[lid]
                    else:
                        jp.append(en[lid])
        elif len(tables.blobs["en"]) > len(before["en"]):
            warnings.append(f"{at}: zone {zone} has no Japanese dialog table; its lines are English only")
        lines = []
        for lang in ("en", "jp"):
            if lang in tables.blobs:
                lines += _line_records(zone, lang, tables.rel[lang], before[lang], tables.blobs[lang])
        rec = {"name": key, "zone": zone, "event": event_id, "actor": f"0x{owner:08X}", "dat": rel,
               "blocks": _block_records(before_event, event_data), "lines": lines}
        if names.get(owner):
            rec["npc"] = names[owner]
        if cam is not None:
            rec["camera"] = cam
        records.append(rec)
        who = names.get(owner) or f"0x{owner:08X}"
        lua += [f"-- {key}: event {event_id} on {who} (0x{owner:08X})", stub.rstrip(), ""]
        if hide:
            ids = ", ".join(str(e) for e in hide)
            sql += [f"-- {key}: event {event_id} hides its cast NPCs' names (namevis bit 0x20)",
                    f"UPDATE `npc_list` SET `namevis` = `namevis` | {NAMEVIS_HIDE} WHERE `npcid` IN ({ids});",
                    f"-- to take it back: UPDATE `npc_list` SET `namevis` = `namevis` & ~{NAMEVIS_HIDE} "
                    f"WHERE `npcid` IN ({ids});", ""]

    changed = [(rel, event_data)] if event_data != orig_event else []
    changed += tables.changed()
    written = []
    for r, data in changed:
        out = xi_stage.write(root, r, data, dry_run)
        if out is not None:
            written.append(str(out))
    from xi.database.xi_build import _pivot_shadow
    warnings += [f"the pivot folder has its own {r}, which the client loads instead of this one — "
                 "build with --pivot to change what players see"
                 for r in _pivot_shadow(Path(root), target, [r for r, _ in changed])]
    return {"id": action["id"], "type": "zone_events", "zone": zone, "target": target, "records": records,
            "cameras": cameras, "files": [r for r, _ in changed], "written": written, "warnings": warnings,
            "sql": ("\n".join(sql).rstrip() + "\n") if sql else "",
            "lua": ("\n".join(lua).rstrip() + "\n") if lua else ""}


def undo(action: dict, root: Path, target: str, dry_run: bool = False) -> tuple[int, list[str]]:
    """Take ``action``'s events back out of the zone's tables in ``root``; ``(events, warnings)``.
    The camera scene DATs are placements: ``xi dats undo`` deletes and unregisters those."""
    from xi.dats import xi_stage
    recs = (((action.get("result") or {}).get("roots") or {}).get(target)) or []
    warnings: list[str] = []
    if not recs:
        return 0, warnings
    zone = action.get("zone")
    rel = recs[0].get("dat")
    event_data = xi_stage.read(root, rel) if rel else None
    orig = event_data
    tables = ZD._Tables(Path(root), zone)
    for rec in reversed(recs):
        event_data = _restore(event_data, tables, rec, warnings)
    if event_data is not None and event_data != orig:
        xi_stage.write(root, rel, event_data, dry_run)
    for r, data in tables.changed():
        xi_stage.write(root, r, data, dry_run)
    return len(recs), warnings


def next_event_id(root: Path, zone: int, owner: int) -> int:
    """The id an ``auto`` event on ``owner`` takes now (the compiler's rule): its highest real
    id + 1, past any id another block of the zone uses."""
    from xi.dats import xi_stage
    rel = event_rel(root, zone)
    data = xi_stage.read(root, rel) if rel else None
    if data is None:
        raise ZoneEventsError(f"zone {zone} has no event table in this install")
    actors = core.parse_raw_actors(data)
    real = [e for a in actors if a.actor_id == owner for e in a.event_ids if e not in (0xFFFE, 0xFFFF)]
    used = {e for a in actors for e in a.event_ids}
    eid = max(real) + 1 if real else 0
    while eid in used or eid in (0xFFFE, 0xFFFF):
        eid += 1
    return eid


def describe(root: Path, zone: int) -> dict:
    """The zone's NPCs that have events: ``{dat, actors: [(entity, name, [event ids])]}``."""
    from xi.dats import xi_stage
    rel = event_rel(root, zone)
    data = xi_stage.read(root, rel) if rel else None
    if data is None:
        raise ZoneEventsError(f"zone {zone} has no event table in this install")
    names = _names(root, zone)
    actors = [(a.actor_id, names.get(a.actor_id, ""), [e for e in a.event_ids if e not in (0xFFFE, 0xFFFF)])
              for a in core.parse_raw_actors(data)]
    return {"dat": rel, "actors": actors}

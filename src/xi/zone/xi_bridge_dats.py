"""The zone editor's publishes, as ``xi dats`` actions.

What the editor puts into the game goes through ``xi dats``, so it is recorded, rebuilt
the same on another install and taken back out by ``xi dats undo``: a published cutscene
is an event of the zone's ``zone_events`` action, a custom NPC's name is an entry of the
zone's ``zone_npcs`` action. The actions live in ``dats.json`` in the active editor
project's folder, beside its ``cutscene-defs``, and are committed with the workspace so
a collaborator can build them into their own install.

Each change rewrites the one action and builds it: into the base install (FFXI_DIR),
then into CatsEyeXI's DATs folder (FFXI_PIVOT_DIR) when asked and configured. The build's
own output comes back as ``log`` for the editor to show. The editor's live-database
conveniences (a custom NPC's ``npc_list`` row, the cast's ``namevis``) stay in the bridge;
the actions carry the same rows as proposed SQL, never run.
"""
from __future__ import annotations

import contextlib
import io
from pathlib import Path

MANIFEST = "dats.json"


def manifest_path() -> Path:
    from xi.zone.xi_bridge import workspace_root
    return workspace_root() / MANIFEST


def _project_name(folder: Path) -> str:
    """The editor project's name as a dats project name, else the folder's."""
    try:
        from xi.zone.xi_bridge import _read_projects
        for p in (_read_projects(folder.parent) or {}).get("projects", []):
            if p.get("id") == folder.name and p.get("name"):
                from xi.dats.xi_dats import _project_slug
                return _project_slug(p["name"])
    except Exception:  # noqa: BLE001 — a name is a nicety
        pass
    return folder.name


def load() -> tuple[Path, dict]:
    from xi.dats.xi_dats import _read_manifest
    path = manifest_path()
    manifest = _read_manifest(path)
    if not path.exists():
        manifest["name"] = _project_name(path.parent)
    return path, manifest


def save(path: Path, manifest: dict) -> None:
    from xi.dats.xi_dats import _write_manifest
    _write_manifest(path, manifest)


def zone_action(manifest: dict, kind: str, zone: int, key: str) -> dict:
    """The manifest's ``kind`` action for ``zone`` (``zone_events.<zone name>``), added when
    there is none yet."""
    from xi.dats.xi_dats import _zone_slug
    for a in manifest.get("actions", []):
        if isinstance(a, dict) and a.get("type") == kind and a.get("zone") == zone:
            return a
    action = {"id": f"{kind}.{_zone_slug(zone)}", "type": kind, "zone": zone, key: []}
    manifest.setdefault("actions", []).append(action)
    return action


def build(path: Path, action_id: str, *, pivot: bool = True, force: bool = False) -> dict:
    """Build one action of the manifest into the base install, then (``pivot``, when
    FFXI_PIVOT_DIR is set) into the DATs folder. ``{ok, log, targets, error?}``; the
    manifest on disk carries the result."""
    import click
    from xi import xi_config
    from xi.dats.xi_dats import build_cmd
    targets = [False] + ([True] if pivot and str(xi_config.FFXI_PIVOT_DIR or "").strip() else [])
    out = io.StringIO()
    done = []
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            for to_pivot in targets:
                with click.Context(build_cmd) as ctx:
                    ctx.invoke(build_cmd, manifest=path, only=(action_id,), force=force, pivot=to_pivot)
                done.append("pivot" if to_pivot else "dir")
    except click.ClickException as e:
        return {"ok": False, "error": e.format_message(), "log": out.getvalue(), "targets": done}
    return {"ok": True, "log": out.getvalue(), "targets": done}


def undo_action(path: Path, manifest: dict, action: dict) -> list[str]:
    """Take one action's last build back out of every target it was built into and drop
    it from the manifest (an action with nothing left in it can't be built)."""
    from xi.database import xi_build as DB
    from xi.dats import xi_dats as D
    warnings: list[str] = []
    for name in D._action_targets(action):
        try:
            root = D._target_root(name)
        except Exception as e:  # noqa: BLE001 — e.g. FFXI_PIVOT_DIR unset since
            warnings.append(f"{name}: {e}")
            continue
        for file_id, dat in D._action_placements(action):
            p = root / Path(*dat.split("/"))
            if p.is_file():
                p.unlink()
            D._unregister_file_id(root, file_id, D._parse_rom_placement(dat)[0])
        _n, warns = D._restorable_module(action["type"]).undo(action, root, name)
        warnings += warns
    for key in ("sql", "lua"):
        f = (action.get("result") or {}).get(key)
        if isinstance(f, str) and f:
            DB.remove_sql_section(Path(f), action["id"])
    manifest["actions"] = [a for a in manifest.get("actions", []) if a is not action]
    save(path, manifest)
    return warnings


def _record(action: dict, name: str) -> dict | None:
    for target in ("dir", "pivot"):
        for rec in (((action.get("result") or {}).get("roots") or {}).get(target)) or []:
            if rec.get("name") == name:
                return rec
    return None


# ── cutscenes ────────────────────────────────────────────────────────────────

def publish_cutscene(zone: int, def_rel: str, name: str, *, pivot: bool) -> dict:
    """Put the cutscene saved at ``def_rel`` (relative to the project folder) into the zone's
    ``zone_events`` action as event ``name`` (replacing one of that name) and build it.
    ``{ok, action, record, log, targets, manifest, error?}``."""
    path, manifest = load()
    action = zone_action(manifest, "zone_events", zone, "events")
    event = {"name": name, "cutscene": def_rel}
    events = [e for e in action.get("events") or [] if e.get("name") != name]
    at = next((i for i, e in enumerate(action.get("events") or []) if e.get("name") == name), len(events))
    events.insert(at, event)
    action["events"] = events
    save(path, manifest)
    built = build(path, action["id"], pivot=pivot)
    _path, manifest = load()
    action = next(a for a in manifest["actions"] if a.get("id") == action["id"])
    return {**built, "action": action["id"], "manifest": str(path), "record": _record(action, name),
            "sql": (action.get("result") or {}).get("sql"), "lua": (action.get("result") or {}).get("lua")}


def delete_event(zone: int, actor: int, event_id: int, *, pivot: bool) -> dict | None:
    """Take an event the editor published out of the zone's action and rebuild; None when
    no event of the action is that one (it came from somewhere else)."""
    path, manifest = load()
    action = next((a for a in manifest.get("actions", []) if isinstance(a, dict)
                   and a.get("type") == "zone_events" and a.get("zone") == zone), None)
    if action is None:
        return None
    actor_hex = f"0x{actor:08X}"
    names = {r.get("name") for recs in (((action.get("result") or {}).get("roots")) or {}).values()
             for r in recs if r.get("actor") == actor_hex and r.get("event") == event_id}
    names.add(f"{zone}_{actor}_{event_id}")
    keep = [e for e in action.get("events") or [] if e.get("name") not in names]
    if len(keep) == len(action.get("events") or []):
        return None
    if not keep:
        warnings = undo_action(path, manifest, action)
        return {"ok": True, "action": action["id"], "removed": True, "warnings": warnings, "log": ""}
    action["events"] = keep
    save(path, manifest)
    return {**build(path, action["id"], pivot=pivot), "action": action["id"], "removed": False}


# ── custom NPCs ──────────────────────────────────────────────────────────────

def npc_entry(rec: dict) -> dict:
    """A registry record as an entry of a ``zone_npcs`` action: its id, name and row."""
    server = {"model": int(rec["modelid"]), "status": int(rec.get("status", 6)),
              "pos": [float(v) for v in rec.get("pos") or [0, 0, 0]], "rot": int(rec.get("rot") or 0)}
    return {"id": int(rec["npcid"]) & 0xFFF, "new": True, "name": rec["name"], "server": server}


def put_npc(rec: dict) -> dict:
    """Add or update a custom NPC in its zone's ``zone_npcs`` action and build it (names that
    an earlier in-place write left in the table are taken over, hence ``force``)."""
    zone = int(rec["zoneId"])
    path, manifest = load()
    action = zone_action(manifest, "zone_npcs", zone, "npcs")
    entry = npc_entry(rec)
    npcs = [n for n in action.get("npcs") or [] if n.get("id") != entry["id"]]
    npcs.append(entry)
    action["npcs"] = sorted(npcs, key=lambda n: n["id"] if isinstance(n.get("id"), int) else 0xFFF)
    save(path, manifest)
    return {**build(path, action["id"], pivot=True, force=True), "action": action["id"]}


def drop_npc(npcid: int) -> dict | None:
    """Take a custom NPC out of its zone's ``zone_npcs`` action and rebuild; None when the
    action doesn't have it."""
    zone, local = (npcid >> 12) & 0xFFF, npcid & 0xFFF
    path, manifest = load()
    action = next((a for a in manifest.get("actions", []) if isinstance(a, dict)
                   and a.get("type") == "zone_npcs" and a.get("zone") == zone), None)
    if action is None or not any(n.get("id") == local for n in action.get("npcs") or []):
        return None
    keep = [n for n in action["npcs"] if n.get("id") != local]
    if not keep:
        return {"ok": True, "action": action["id"], "removed": True,
                "warnings": undo_action(path, manifest, action), "log": ""}
    action["npcs"] = keep
    save(path, manifest)
    return {**build(path, action["id"], pivot=True, force=True), "action": action["id"], "removed": False}

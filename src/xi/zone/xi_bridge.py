"""Backend command handler for the web zone editor's WebSocket bridge.

The editor frontend (web/leveleditor) calls these over ``ws://<host>/ws`` so saves
and exports happen without a CLI round-trip. ``handle_command(method, params)`` is
wired into :func:`xi.common.xi_editor.serve` as its ``command_handler``.

Workspace model (see docs): each zone gets a folder
``web/leveleditor/workspaces/<zone-key>/`` holding the editor's
``zone-changes.json`` (the diff vs. pristine), any uploaded GLB assets, a
``versions/`` subfolder of per-publish snapshots (``vNNNN.json``), and — after
an Export — a ``<dat>.edited`` copy of the applied DAT for convenience. A single
``workspaces/settings.json`` holds workspace-wide settings (e.g. the per-zone
version counters).

Methods (v1):
  * ``zone.state {zone}``       → ``{hasChanges, hasEdited, changes, workspace}``
  * ``zone.saveChanges {zone, changes}`` → write ``zone-changes.json`` (no DAT bake)
  * ``zone.putAsset {zone, name, bytesBase64}`` → store a GLB in the workspace
  * ``zone.export {zone, changes, reset}`` → apply to the game DAT (in place),
    copy the result to the workspace as ``<dat>.edited``, then snapshot the
    change-set into ``versions/`` (bumping the per-zone counter in settings.json)
  * ``zone.versions {zone}``    → ``{versions: [{version, ts, counts, file, hasLog}], current}``
  * ``zone.versionGet {zone, version}`` → one snapshot's ``{version, ts, counts, changes, log}``
  * ``zone.versionSaveLog {zone, version, log}`` → attach a publish log to a snapshot
  * ``zone.versionsClear {zone}`` → delete all snapshots + reset the counter → ``{removed}``
  * ``zone.package {zone}`` → zip one zone's edited game + HD DATs (client-relative
    layout) into ``workspaces/packages/<ZoneName>_<version>.zip``, make a one-time
    pristine ``<ZoneName>_backup.zip``, and reveal the zip in the OS file browser
  * ``zone.packageProject {zones, projectName}`` → zip several zones' edited game + HD
    DATs into one ``workspaces/packages/<project>.zip`` (the editor's Package wizard),
    plus a one-time pristine ``<ZoneName>_backup.zip`` per zone
  * ``audio.decodeSfx {soundId}`` → decode that sound's ``.spw`` to a WAV →
    ``{wavBase64, format, sampleRate, channels, duration}`` for in-browser playback
  * ``ping`` → ``{pong: True}`` (connectivity check)
"""

import base64
import json
import os
import re
import shutil
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from xi.xi_config import XI_TOOLS_DIR, output_path_for

# Serialize backend mutations — a single editor client issues commands one at a
# time, but a DAT apply must never overlap another (shared output files).
_LOCK = threading.Lock()


def _zone_rel(zone: str) -> str:
    """Normalise the frontend ``zone`` field to a game-relative DAT path.

    The editor sends the zone dropdown value, e.g. ``game/ROM/1/41.DAT``,
    ``game-hd/ROM/1/41.DAT``, or ``ROM/1/41.DAT``; strip the junction prefix
    and normalise slashes."""
    z = (zone or "").replace("\\", "/").strip()
    try:
        from xi.xi_config import FFXI_DIR
        game = Path(FFXI_DIR).resolve().as_posix().rstrip("/") + "/"
        p = Path(z)
        if p.is_absolute():
            zp = p.resolve().as_posix()
            if zp.startswith(game):
                z = zp[len(game):]
    except Exception:
        pass
    z = z.removeprefix("game-hd/")
    return z.removeprefix("game/")


def _is_hd(zone_url: str) -> bool:
    """True when the zone URL uses the ``game-hd/`` prefix (HD mode)."""
    z = (zone_url or "").replace("\\", "/").strip()
    return z.startswith("game-hd/")


def _zone_key(zone_rel: str) -> str:
    """Filesystem-safe per-zone workspace key, e.g. ``ROM/1/41.DAT`` → ``ROM_1_41``."""
    stem = zone_rel.rsplit(".", 1)[0] if "." in zone_rel.rsplit("/", 1)[-1] else zone_rel
    return stem.replace("/", "_").replace("\\", "_")


def _editor_dir() -> Path:
    """Locate the bundled web/leveleditor dir (mirrors xi_editor._default_editor_dir)."""
    env = os.environ.get("XI_EDITOR_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for base in here.parents:
        cand = base / "web" / "leveleditor"
        if cand.is_dir():
            return cand
    return Path(XI_TOOLS_DIR) / "web" / "leveleditor"


# Optional workspaces repo for the first-run setup's clone path. No default —
# workspaces is just a folder you point at; cloning is only offered when you
# explicitly set XI_WORKSPACES_REPO_URL. Whether that folder is backed by git,
# Dropbox or nothing at all is up to you.
WORKSPACES_REPO_URL = os.environ.get("XI_WORKSPACES_REPO_URL", "")


# Set per-session when a project is opened (workspace.setActiveProject); None → the
# legacy editor-local default. When set, EVERY workspace read/write — state, save,
# publish, versions, packages, assets — resolves under the project folder, because
# they all go through workspace_root().
_ACTIVE_WS_ROOT = None


def workspace_root() -> Path:
    """The active workspace root: the open project's folder when one is active, else
    the legacy editor-local <web/leveleditor>/workspaces."""
    if _ACTIVE_WS_ROOT is not None:
        return _ACTIVE_WS_ROOT
    return _editor_dir() / "workspaces"


def workspaces_repo_root() -> Path:
    """The shared workspaces repo root — the folder holding projects.json, one level
    above the active project folder. User templates live here (committed + shared across
    projects). Resolution: the active project's repo (editor) → ``XI_WORKSPACES_DIR``
    (lets a CLI process target the same clone) → the legacy editor-local workspaces dir."""
    if _ACTIVE_WS_ROOT is not None:
        return _ACTIVE_WS_ROOT.parent
    env = os.environ.get("XI_WORKSPACES_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    return _editor_dir() / "workspaces"


def _workspace_dir(zone_rel: str, create: bool = True) -> Path:
    d = workspace_root() / _zone_key(zone_rel)
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


# ── Version history ─────────────────────────────────────────────────────────
# Every Publish snapshots the applied change-set into <zone>/versions/vNNNN.json.
# The monotonic per-zone version number is stored centrally in the main workspace
# settings file (workspaces/settings.json) under ``versionCounters``.

def _settings_path() -> Path:
    """The main workspace settings file: ``workspaces/settings.json``."""
    return workspace_root() / "settings.json"


def _read_settings() -> dict:
    p = _settings_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8")) or {}
        except (ValueError, OSError):
            return {}
    return {}


def _write_settings(data: dict) -> None:
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _next_version(zone_key: str) -> int:
    """Bump and persist the per-zone version counter; return the new number."""
    s = _read_settings()
    counters = s.setdefault("versionCounters", {})
    n = int(counters.get(zone_key, 0)) + 1
    counters[zone_key] = n
    _write_settings(s)
    return n


def _drop_version_counter(zone_key: str) -> None:
    """Remove a zone's entry from ``versionCounters`` (next Publish restarts at v1)."""
    s = _read_settings()
    counters = s.get("versionCounters") or {}
    if counters.pop(zone_key, None) is not None:
        s["versionCounters"] = counters
        _write_settings(s)


def _prune_orphan_counters() -> int:
    """Drop ``versionCounters`` entries whose zone no longer has a change-set in the
    active workspace. Counters are keyed by workspace folder name (== ``_zone_key``);
    a zone with no ``zone-changes.json`` carries no versionable changes, so its counter
    is stale (e.g. left behind by ``project.removeZone`` + a later folder re-create).
    Self-healing: runs on the Project Zones refresh. Returns the number pruned."""
    root = workspace_root()
    s = _read_settings()
    counters = s.get("versionCounters") or {}
    stale = [k for k in counters if not (root / k / "zone-changes.json").exists()]
    if stale:
        for k in stale:
            counters.pop(k, None)
        s["versionCounters"] = counters
        _write_settings(s)
    return len(stale)


def _versions_dir(zone_rel: str, create: bool = False) -> Path:
    d = _workspace_dir(zone_rel, create=create) / "versions"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def _change_counts(changes: dict) -> dict:
    def ops(lst):
        counts = {"add": 0, "delete": 0, "modify": 0}
        for x in (lst or []):
            op = x.get("op")
            if op in counts:
                counts[op] += 1
        return counts
    return {
        "placements": ops(changes.get("placements")),
        "vfx":        ops(changes.get("vfx")),
        "markers":    ops(changes.get("markers")),
        "collisions": ops(changes.get("collisions")),
    }


def _save_version(zone_rel: str, changes: dict) -> dict:
    """Snapshot a published change-set into the zone's ``versions/`` folder.

    Returns ``{version, ts, counts}`` for the just-written snapshot."""
    n = _next_version(_zone_key(zone_rel))
    counts = _change_counts(changes)
    payload = {
        "version": n,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "zone": zone_rel,
        "counts": counts,
        "changes": changes,
    }
    vdir = _versions_dir(zone_rel, create=True)
    (vdir / f"v{n:04d}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"version": n, "ts": payload["ts"], "counts": counts}


def _resolve_dat(zone_rel: str) -> Path:
    from xi.entity.mesh.xi_export import resolve_dat_path
    return resolve_dat_path(zone_rel)


def handle_command(method: str, params: dict):
    if method == "ping":
        return {"pong": True}
    if method == "config.info":
        from xi.xi_config import FFXI_DIR, FFXI_HD_DIR
        return {
            "ffxiDir": str(Path(FFXI_DIR)),                          # pristine root — the /game/ junction target
            "hdDir": bool(FFXI_HD_DIR),
            "hdDirPath": str(Path(FFXI_HD_DIR)) if FFXI_HD_DIR else "",  # /game-hd/ root
        }
    if method == "app.version":
        return {"version": _app_version()}
    if method == "workspace.pickFolder":
        return _pick_folder(params)            # native folder dialog → {ok, path}
    if method == "workspace.skip":
        return _workspace_skip()               # create local workspaces/ at xi root, no git
    if method == "workspace.setup":
        return _workspace_setup(params)        # git clone / adopt the workspaces repo (streams progress)
    if method == "workspace.status":
        return _workspace_status(params)       # does the configured workspace still exist?
    if method == "workspace.setActiveProject":
        return _set_active_project(params)     # point reads/writes at a project folder
    if method == "env.status":
        return _env_status(params)             # .env paths for first-run setup form
    if method == "env.save":
        return _env_save(params)               # write .env + hot-reload xi_config
    if method == "env.detectBlender":
        return _env_detect_blender(params)     # best-effort Blender path guess
    if method == "env.pickPath":
        return _env_pick_path(params)          # folder or file picker for env fields
    if method == "editor.loadSettings":
        return _editor_load_settings(params)   # local per-user view-state (editor.json)
    if method == "editor.saveSettings":
        return _editor_save_settings(params)
    if method == "project.loadSettings":
        return _project_load_settings(params)  # per-project settings (project_settings.json in the workspace)
    if method == "project.saveSettings":
        return _project_save_settings(params)
    if method == "project.list":
        return _project_list(params)           # projects.json in the workspaces repo
    if method == "project.create":
        return _project_create(params)
    if method == "project.update":
        return _project_update(params)         # edit name/description/authors/tags
    if method == "project.zones":
        return _project_zones(params)          # zones with edits in the active project
    if method == "project.removeZone":
        return _project_remove_zone(params)    # delete one zone's workspace from the project
    if method == "project.openFolder":
        return _project_open_folder(params)    # reveal a project's folder in the OS file browser
    if method == "zone.companionDats":
        return _zone_companion_dats(params)    # dialog/npc/event DAT paths for a zone
    if method == "project.delete":
        return _project_delete(params)         # remove a project (git history preserved)
    if method == "zone.state":
        return _state(params)
    if method == "zone.versions":
        return _versions(params)            # list snapshots — read-only, no lock
    if method == "zone.versionGet":
        return _version_get(params)         # fetch one snapshot — read-only, no lock
    if method == "zone.navmesh":
        return _navmesh(params)
    if method == "zone.navmesh.generate":
        return _navmesh_generate(params)
    if method == "zone.events":
        return _events(params)              # parse event DAT → actor/event tree — read-only, no lock
    if method == "zone.dialog":
        return _dialog(params)              # decode dialogue lines by message id — read-only, no lock
    if method == "zone.eventOpcodes":
        return _event_opcodes(params)       # disassemble one event → opcode list — read-only, no lock
    if method == "zone.cutscene":
        return _cutscene(params)            # decode one event → cutscene timeline — read-only, no lock
    if method == "zone.compileCutscene":
        return _compile_cutscene(params)    # JSON → bytecode; dryRun keeps mirror untouched
    if method == "zone.loadCutsceneDef":
        return _load_cutscene_def(params)   # saved cutscene definition for Edit — read-only
    if method == "zone.npcAnimations":
        return _npc_animations(params)      # animation tags for an NPC (dropdown) — read-only
    if method == "zone.deleteEvent":
        return _delete_event(params)        # remove an event from an actor's block — writes mirror
    if method == "zone.cutsceneActors":
        return _cutscene_actors(params)     # cutscene NPC list (names/positions/looks) — read-only
    if method == "zone.npcDefaults":
        return _npc_defaults(params)        # default npc_list pos+look for a list of entity ids — read-only
    if method == "zone.cutsceneActorGlb":
        return _cutscene_actor_glb(params)  # assemble one actor's character GLB — read-only
    if method == "zone.characterData":
        return _cutscene_actor_data(params) # raw geometry/skeleton/anim data (no GLB) — read-only
    if method == "zone.sceneResource":
        return _scene_resource(params)      # raw scene-resource DAT bytes (for VFX) — read-only
    if method == "zone.subareas":
        return _subareas(params)            # sub-area ids → interior DAT paths (FTABLE) — read-only
    if method == "zone.subareaParent":
        return _subarea_parent(params)      # interior DAT → owning zone (reverse 0x36 index) — read-only
    if method == "zone.serverEventInfo":
        return _server_event_info(params)   # find the server (LSB/CatsEye) script for this event — read-only
    if method == "zone.hdVariant":
        return _hd_variant(params)         # does an HD asset-pack DAT exist for this zone? — read-only
    if method == "zone.list-custom":
        return _zone_list_custom()
    if method == "zone.listEffects":
        return _zone_list_effects(params)  # read-only, no lock
    if method == "zone.mobList":
        return _mob_list(params)           # mob catalog (mob_pools) for the asset browser — read-only
    if method == "zone.mobGlb":
        return _mob_glb(params)            # assemble one mob's model GLB from its look — read-only
    if method == "zone.pickGlb":
        return _pick_glb(params)            # native file dialog — no mutation, no lock
    if method == "zone.getAsset":
        return _get_asset(params)           # read a GLB's bytes for re-display — no lock
    if method == "audio.decodeSfx":
        return _audio_decode_sfx(params)    # decode a sound's .spw → WAV base64 — read-only, no lock
    if method == "audio.decodeBgm":
        return _audio_decode_bgm(params)    # decode a zone BGM .bgw → WAV base64 — read-only, no lock
    if method == "audio.musicCatalog":
        return _audio_music_catalog(params) # list every music .bgw with header info — read-only, no lock
    if method == "audio.sfxCatalog":
        return _audio_sfx_catalog(params)   # list every sfx .spw grouped by folder — read-only, no lock
    if method == "audio.importSound":
        return _audio_import_sound(params)  # convert+install an uploaded audio file → soundId
    if method == "audio.importMusic":
        return _audio_import_music(params)  # convert+install an uploaded audio file → music id
    if method == "zone.bgm":
        return _zone_bgm(params)            # zone music ids from zone_settings (DB) — read-only, no lock
    if method == "zone.setBgm":
        return _zone_set_bgm(params)        # write zone_settings music_* columns (DB)
    if method == "db.tables":
        return _db_tables(params)
    if method == "db.query":
        return _db_query(params)
    if method == "db.update":
        return _db_update(params)
    if method == "db.exec":
        return _db_exec(params)
    if method == "zone.writeMobSpawns":
        return _write_mob_spawns(params)   # placed mobs → mob_groups + mob_spawn_points (DB)
    if method == "customNpc.list":
        return _custom_npc_list(params)    # registry custom NPCs for the Asset Browser — read-only
    if method == "customNpc.create":
        return _custom_npc_create(params)  # register a placed model as a zone NPC (+SQL +live DB)
    if method == "customNpc.update":
        return _custom_npc_update(params)  # patch registry fields (status, …) + SQL + live DB
    if method == "customNpc.delete":
        return _custom_npc_delete(params)  # remove a custom NPC from registry/SQL/DB
    if method == "zone.templates":
        return _zone_templates(params)
    with _LOCK:
        if method == "zone.new":
            return _zone_new(params)
        if method == "zone.makeTemplate":
            return _zone_make_template(params)
        if method == "zone.duplicate":
            return _zone_duplicate(params)
        if method == "zone.getSettings":
            return _zone_get_settings(params)
        if method == "zone.setZonetype":
            return _zone_set_field(params, "zonetype")
        if method == "zone.setMisc":
            return _zone_set_field(params, "misc")
        if method == "zone.delete":
            return _zone_delete(params)
        if method == "zone.saveChanges":
            return _save_changes(params)
        if method == "zone.putAsset":
            return _put_asset(params)
        if method == "zone.export":
            return _export(params)
        if method == "zone.cloneToHd":
            return _clone_to_hd(params)      # copy the published standard DAT byte-for-byte over the HD DAT
        if method == "zone.reset":
            return _reset(params)
        if method == "zone.clearCollision":
            return _clear_collision(params)
        if method == "zone.package":
            return _package(params)          # zip edited game+HD DATs → workspaces/packages — locked
        if method == "zone.packageProject":
            return _package_project(params)  # zip multiple zones into one project zip — locked
        if method == "zone.versionsClear":
            return _versions_clear(params)   # delete snapshots + reset counter — mutates, locked
        if method == "zone.versionSaveLog":
            return _version_save_log(params)  # attach a publish log to a snapshot — mutates, locked
        if method == "zone.replaceCollision":
            return _replace_collision(params)
    raise ValueError(f"unknown method: {method}")


def _navmesh(params: dict) -> dict:
    """Return navmesh triangle positions for a zone's .nav file.

    Search order:
      1. ``<editor>/assets/<stem>.nav``        (zone DAT companion nav)
      2. ``exports/zone/.../<stem>.nav``        (xi zone navmesh output)
      3. ``XI_NAVMESH_DIR/<ZoneName>.nav``   (server pre-baked navmeshes)
    Returns ``{positions: [x,y,z,...], navFile: str}`` or ``{positions: [], error: str}``."""
    from xi.zone.xi_navmesh import navmesh_triangles
    from xi.xi_config import XI_NAVMESH_DIR
    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel:
        raise ValueError("missing 'zone'")
    dat = _resolve_dat(zone_rel)
    stem = dat.stem

    editor_dir = _editor_dir()
    nav_path = editor_dir / "assets" / f"{stem}.nav"

    if not nav_path.exists() and XI_NAVMESH_DIR:
        zone_name = _zone_name_for_dat(dat)
        if zone_name:
            candidate = Path(XI_NAVMESH_DIR) / f"{zone_name.replace(' ', '_')}.nav"
            if candidate.exists():
                nav_path = candidate

    if not nav_path.exists():
        from xi.zone.xi_export import default_output_dir
        nav_path = default_output_dir(dat) / f"{stem}.nav"

    if not nav_path.exists():
        return {"positions": [], "error": f"no .nav found for {stem}"}

    positions = navmesh_triangles(nav_path)
    return {"positions": positions, "navFile": str(nav_path)}


def _navmesh_generate(params: dict) -> dict:
    """Bake a fresh .nav for the zone from its collision mesh.

    Calls ``build_navmesh_from_collision`` directly (same as ``xi zone navmesh``).
    Returns ``{ok, navFile, nTris, nTiles}`` or raises on error."""
    from xi.zone.xi_export import default_output_dir
    from xi.zone.xi_navmesh import NavSettings, build_navmesh_from_collision
    from xi.xi_config import read_path_for

    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel:
        raise ValueError("missing 'zone'")
    dat = _resolve_dat(zone_rel)
    source = read_path_for(dat)
    out = default_output_dir(dat) / f"{dat.stem}.nav"

    agent_radius = float(params.get("agentRadius", 0.3))
    agent_max_climb = float(params.get("agentMaxClimb", 0.5))
    cell_size = float(params.get("cellSize", 0.4))
    tile_size = float(params.get("tileSize", 256.0))
    settings = NavSettings(
        cell_size=cell_size, agent_radius=agent_radius,
        agent_max_climb=agent_max_climb, tile_size=tile_size,
    )
    out_path, n_tris, n_tiles = build_navmesh_from_collision(source, out, settings)
    return {"ok": True, "navFile": str(out_path), "nTris": n_tris, "nTiles": n_tiles}


def _resolve_zone_id(zone_rel: str):
    """Best-effort ``zone_rel`` (model DAT path) → ``(zone_id, zone_name)``.

    Tries the workspace ``zone-meta.json`` first (custom ROM10 zones carry their id
    there), then matches the path against the static zone table. Returns
    ``(None, None)`` when neither resolves."""
    try:
        mp = _workspace_dir(zone_rel, create=False) / "zone-meta.json"
        if mp.exists():
            m = json.loads(mp.read_text(encoding="utf-8"))
            if m.get("zoneId") is not None:
                return int(m["zoneId"]), m.get("name")
    except (OSError, ValueError):
        pass
    try:
        from xi.zone.xi_list import get_zone_entries
        zl = zone_rel.lower()
        for e in get_zone_entries(path_prefix=""):
            if zl.endswith(e["path"].lower()):
                return e["id"], e["name"]
    except Exception:
        pass
    return None, None


def _mark_custom_events(payload: dict, ev_rel: str) -> None:
    """Set ``ev['isCustom']=True`` on events that exist in the live Event DAT but not in
    the pristine baseline, so the UI can offer Delete only on user-added events.

    The baseline is the ``.base`` backup written next to the output mirror on the first
    publish/delete (see :func:`_publish`/:func:`_delete_event`). No ``.base`` ⇒ the DAT
    was never edited ⇒ nothing is custom."""
    from xi.xi_config import FFXI_DIR, output_path_for
    from xi.event.xi_event import parse_raw_actors

    base = Path(str(output_path_for(Path(FFXI_DIR) / ev_rel)) + ".base")
    if not base.exists():
        return
    known = {(a.actor_id, eid) for a in parse_raw_actors(base.read_bytes())
             for eid in a.event_ids}
    for actor in payload.get("actors", []):
        aid = actor.get("actorId")
        for ev in actor.get("events", []):
            if (aid, ev.get("eventId")) not in known:
                ev["isCustom"] = True


def _events(params: dict) -> dict:
    """Parse a zone's event DAT into an actor → event tree for the editor's Events panel.

    Resolves the zone's Event + NPC DATs from its zone id (passed by the frontend as a
    hint, else recovered from the workspace meta / zone table), reads them
    (edits live in place under FFXI_DIR), and categorises each
    event (Cutscene / Menu / Dialogue / …). Returns ``{ok: False, error}`` rather than
    raising so the panel can show a friendly message."""
    from xi.xi_config import FFXI_DIR, read_path_for
    from xi.zone.xi_inject import zone_event_file_id, zone_npc_file_id
    from xi.ftable.xi_core import scan_file_ids
    from xi.event.xi_event import build_events_payload, EventDatError

    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel:
        raise ValueError("missing 'zone'")

    zone_id = params.get("zoneId")
    zone_name = params.get("zoneName") or None
    if zone_id is None:
        zone_id, zone_name = _resolve_zone_id(zone_rel)
    if zone_id is None:
        return {"ok": False, "error": "Could not determine the zone id for this DAT."}
    zone_id = int(zone_id)

    ev_hits = scan_file_ids([zone_event_file_id(zone_id)])
    if not ev_hits:
        return {"ok": False, "error": f"No event DAT registered for zone {zone_id}."}
    ev_rel = ev_hits[0]["dat"]
    ev_path = read_path_for(Path(FFXI_DIR) / ev_rel)
    if not ev_path.exists():
        return {"ok": False, "error": f"Event DAT not found on disk: {ev_rel}"}

    npc_rel = None
    npc_data = b""
    npc_hits = scan_file_ids([zone_npc_file_id(zone_id)])
    if npc_hits:
        npc_rel = npc_hits[0]["dat"]
        npc_path = read_path_for(Path(FFXI_DIR) / npc_rel)
        if npc_path.exists():
            npc_data = npc_path.read_bytes()

    try:
        payload = build_events_payload(ev_path.read_bytes(), npc_data)
    except EventDatError as exc:
        return {"ok": False, "error": f"{ev_rel}: {exc}"}

    # Flag user-added events (present now but not in the pristine baseline) so the panel
    # can gate Delete to custom events only. Never let this break the tree.
    try:
        _mark_custom_events(payload, ev_rel)
    except Exception:
        pass

    payload.update({
        "ok": True,
        "zoneId": zone_id,
        "zoneName": zone_name,
        "eventDat": ev_rel,
        "npcDat": npc_rel,
    })
    return payload


# Parsed dialogue tables are reused across event clicks: {dat path → (mtime_ns, {id: text})}.
_DIALOG_CACHE: dict = {}


def _dialog_index(path: Path) -> dict:
    """Parse a dialogue (event-message) DAT → ``{message_id: text}``, cached by path+mtime."""
    from xi.dialog.xi_dialog import parse_event_message
    key = str(path)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = 0
    cached = _DIALOG_CACHE.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    entries, _ = parse_event_message(path.read_bytes())
    index = {e.index: e.text for e in entries}
    _DIALOG_CACHE[key] = (mtime, index)
    return index


def _dialog(params: dict) -> dict:
    """Decode dialogue lines for a zone by message id, for the editor's Events panel.

    ``{zone, zoneId?, ids: [int]}`` → resolve the zone's Dialog DAT, decode the requested
    message ids to text (edit-aware), and return ``{ok, lines: [{id, text, missing?}]}``.
    Returns ``{ok: False, error}`` rather than raising so the panel can show a message."""
    from xi.xi_config import FFXI_DIR, read_path_for
    from xi.zone.xi_inject import zone_dialog_file_id
    from xi.ftable.xi_core import scan_file_ids
    from xi.dialog.xi_dialog import DialogError

    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel:
        raise ValueError("missing 'zone'")
    zone_id = params.get("zoneId")
    if zone_id is None:
        zone_id, _ = _resolve_zone_id(zone_rel)
    if zone_id is None:
        return {"ok": False, "error": "Could not determine the zone id for this DAT."}
    zone_id = int(zone_id)

    try:
        ids = [int(i) for i in (params.get("ids") or [])]
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid 'ids'"}

    hits = scan_file_ids([zone_dialog_file_id(zone_id)])
    if not hits:
        return {"ok": False, "error": f"No dialogue DAT registered for zone {zone_id}."}
    dg_rel = hits[0]["dat"]
    dg_path = read_path_for(Path(FFXI_DIR) / dg_rel)
    if not dg_path.exists():
        return {"ok": False, "error": f"Dialogue DAT not found on disk: {dg_rel}"}

    try:
        index = _dialog_index(dg_path)
    except DialogError as exc:
        return {"ok": False, "error": f"{dg_rel}: {exc}"}

    lines = [
        {"id": i, "text": index[i]} if i in index else {"id": i, "text": "", "missing": True}
        for i in ids
    ]
    return {"ok": True, "zoneId": zone_id, "dialogDat": dg_rel, "lines": lines, "total": len(index)}


# Parsed event actors are reused across opcode-view clicks: {dat path → (mtime_ns, actors)}.
_EVENT_CACHE: dict = {}


def _event_actors(ev_path: Path):
    """Parse an event DAT → list[ActorRecord], cached by path+mtime."""
    from xi.event.xi_event import parse_event_dat
    key = str(ev_path)
    try:
        mtime = ev_path.stat().st_mtime_ns
    except OSError:
        mtime = 0
    cached = _EVENT_CACHE.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    actors = parse_event_dat(ev_path.read_bytes())
    _EVENT_CACHE[key] = (mtime, actors)
    return actors


# Parsed NPC (entity) name tables, reused across event/opcode/cutscene calls: {path → (mtime, names)}.
_NPC_NAMES_CACHE: dict = {}


def _zone_npc_names(zone_id: int) -> dict:
    """``{serverId: name}`` for a zone's NPCs (Entity DAT), cached by path+mtime. ``{}`` if
    the zone has no NPC DAT — so callers can resolve actor ids to names where possible."""
    from xi.xi_config import FFXI_DIR, read_path_for
    from xi.zone.xi_inject import zone_npc_file_id
    from xi.ftable.xi_core import scan_file_ids
    from xi.event.xi_event import parse_entity_names
    hits = scan_file_ids([zone_npc_file_id(zone_id)])
    if not hits:
        return {}
    npc_path = read_path_for(Path(FFXI_DIR) / hits[0]["dat"])
    if not npc_path.exists():
        return {}
    key = str(npc_path)
    try:
        mtime = npc_path.stat().st_mtime_ns
    except OSError:
        mtime = 0
    cached = _NPC_NAMES_CACHE.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    names = parse_entity_names(npc_path.read_bytes())
    _NPC_NAMES_CACHE[key] = (mtime, names)
    return names


def _event_opcodes(params: dict) -> dict:
    """Disassemble one event → its opcode list, for the editor's dialogue modal "Opcodes" tab.

    ``{zone, zoneId?, actorId, eventId}`` → resolve the zone's Event DAT, find the actor +
    event, and return ``{ok, opcodes:[{offset,op,name,step,args,dialog_ref?}], …}``."""
    from xi.xi_config import FFXI_DIR, read_path_for
    from xi.zone.xi_inject import zone_event_file_id
    from xi.ftable.xi_core import scan_file_ids
    from xi.event.xi_event import opcode_to_dict, categorize_event, EventDatError

    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel:
        raise ValueError("missing 'zone'")
    zone_id = params.get("zoneId")
    if zone_id is None:
        zone_id, _ = _resolve_zone_id(zone_rel)
    if zone_id is None:
        return {"ok": False, "error": "Could not determine the zone id for this DAT."}
    zone_id = int(zone_id)
    try:
        actor_id = int(params.get("actorId"))
        event_id = int(params.get("eventId"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid 'actorId'/'eventId'"}

    hits = scan_file_ids([zone_event_file_id(zone_id)])
    if not hits:
        return {"ok": False, "error": f"No event DAT registered for zone {zone_id}."}
    ev_path = read_path_for(Path(FFXI_DIR) / hits[0]["dat"])
    if not ev_path.exists():
        return {"ok": False, "error": f"Event DAT not found on disk: {hits[0]['dat']}"}

    try:
        actors = _event_actors(ev_path)
    except EventDatError as exc:
        return {"ok": False, "error": f"{hits[0]['dat']}: {exc}"}

    actor = next((a for a in actors if a.actor_id == actor_id), None)
    if actor is None:
        return {"ok": False, "error": f"Actor 0x{actor_id:08X} not found."}
    ev = next((e for e in actor.events if e.event_id == event_id), None)
    if ev is None:
        return {"ok": False, "error": f"Event {event_id} not found on that actor."}

    names = _zone_npc_names(zone_id)
    return {"ok": True, "actorId": actor_id, "eventId": event_id,
            "opcodes": [opcode_to_dict(o, names) for o in ev.opcodes],
            "opcodeCount": len(ev.opcodes), "isCutscene": ev.is_cutscene,
            "category": categorize_event(ev), "dialogIds": ev.dialog_ids}


def _cutscene(params: dict) -> dict:
    """Decode one event into a cutscene timeline for the editor's playback view.

    ``{zone, zoneId?, actorId, eventId, dismissFrames?}`` → resolve the Event DAT (find the
    actor + event), resolve the zone's Dialog DAT to fill in line text, and build a
    time-ordered beat list via :func:`build_cutscene_timeline`. Returns
    ``{ok, beats, totalFrames, fps, …}`` or ``{ok: False, error}``."""
    from xi.xi_config import FFXI_DIR, read_path_for
    from xi.zone.xi_inject import zone_event_file_id, zone_dialog_file_id
    from xi.ftable.xi_core import scan_file_ids
    from xi.event.xi_event import build_cutscene_timeline, EventDatError

    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel:
        raise ValueError("missing 'zone'")
    zone_id = params.get("zoneId")
    if zone_id is None:
        zone_id, _ = _resolve_zone_id(zone_rel)
    if zone_id is None:
        return {"ok": False, "error": "Could not determine the zone id for this DAT."}
    zone_id = int(zone_id)
    try:
        actor_id = int(params.get("actorId"))
        event_id = int(params.get("eventId"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid 'actorId'/'eventId'"}

    hits = scan_file_ids([zone_event_file_id(zone_id)])
    if not hits:
        return {"ok": False, "error": f"No event DAT registered for zone {zone_id}."}
    ev_path = read_path_for(Path(FFXI_DIR) / hits[0]["dat"])
    if not ev_path.exists():
        return {"ok": False, "error": f"Event DAT not found on disk: {hits[0]['dat']}"}
    try:
        actors = _event_actors(ev_path)
    except EventDatError as exc:
        return {"ok": False, "error": f"{hits[0]['dat']}: {exc}"}

    actor = next((a for a in actors if a.actor_id == actor_id), None)
    if actor is None:
        return {"ok": False, "error": f"Actor 0x{actor_id:08X} not found."}
    ev = next((e for e in actor.events if e.event_id == event_id), None)
    if ev is None:
        return {"ok": False, "error": f"Event {event_id} not found on that actor."}

    # Dialogue text (best-effort — a timeline still works without it).
    dialog_index = None
    dg_hits = scan_file_ids([zone_dialog_file_id(zone_id)])
    if dg_hits:
        dg_path = read_path_for(Path(FFXI_DIR) / dg_hits[0]["dat"])
        if dg_path.exists():
            try:
                dialog_index = _dialog_index(dg_path)
            except Exception:
                dialog_index = None

    try:
        dismiss = int(params.get("dismissFrames", 90))
    except (TypeError, ValueError):
        dismiss = 90
    dismiss = max(1, min(dismiss, 1800))

    names = _zone_npc_names(zone_id)
    tl = build_cutscene_timeline(ev, actor.refs, dialog_index=dialog_index,
                                 names=names, dismiss_frames=dismiss)

    # Stage positions: the event places each NPC with a 0xBA "calibrate position" opcode
    # (entity → world X/Y/Z + dir, from refs[]). Hang them on the matching npc beats so the
    # 3D actors stand where the cutscene puts them (not at the origin).
    from xi.event.xi_event import event_entity_positions
    positions = event_entity_positions(ev, actor.refs)
    if positions:
        for b in tl["beats"]:
            p = positions.get(b.get("actorId")) if b.get("type") == "npc" else None
            if p:
                b["pos"] = p["pos"]
                b["dir"] = p["dir"]

    # Resolve each shot/task against its scene resource: the 0x07 EffectRoutine named like the
    # action tag (e.g. 'z00b') tells us the camera Route it drives + the VFX / skeleton anims /
    # sounds it fires. Load each scene resource once; hang the resolved data on the beat and
    # accumulate a cutscene-level VFX/anim summary.
    cam_shots = 0
    scene_cache: dict = {}
    vfx_set, anim_set, sound_set = set(), set(), set()
    for b in tl["beats"]:
        rid, tag = b.get("res"), b.get("tag")
        if not rid or not tag or b.get("type") not in ("shot", "task", "fade"):
            continue
        if rid not in scene_cache:
            scene_cache[rid] = _scene_data(rid)
        sd = scene_cache[rid]
        routine = sd["routines"].get(tag)
        # Camera: prefer the Route the routine actually references; else the legacy 'c'+tag guess.
        cand = (routine["camera"] if routine and routine.get("camera") else [])
        for ct in [*cand, "c" + tag[1:]]:
            kfs = sd["routes"].get(ct)
            if kfs:
                b["camera"] = kfs
                # Real per-shot move length (raw u16 from the routine's camera command) so the
                # editor paces each shot by its authored duration instead of a flat nominal.
                if routine and routine.get("camDur"):
                    b["camDur"] = routine["camDur"]
                cam_shots += 1
                break
        if routine:
            if routine.get("vfx"):
                b["vfx"] = routine["vfx"]; vfx_set.update(routine["vfx"])
            if routine.get("anim"):
                b["playAnim"] = routine["anim"]; anim_set.update(routine["anim"])
            if routine.get("sound"):
                b["sound"] = routine["sound"]; sound_set.update(routine["sound"])
        m = sd["motions"].get(tag)        # 0x27 FollowPoints → world-space waypoint path
        if m:
            b["motion"] = m

    # Resolve each animated entity's 0x5B motion tags → concrete skeleton clips (file_id +
    # 0x2B clip name), keyed by entity id, so the editor can embed the right gesture per actor.
    from xi.event.xi_event import resolve_event_clips
    try:
        motion_clips = {str(ent): clips for ent, clips in resolve_event_clips(actor, ev).items()}
    except Exception:
        motion_clips = {}

    tl.update({"ok": True, "actorId": actor_id, "eventId": event_id,
               "actorName": "", "category": "Cutscene" if ev.is_cutscene else "",
               "opcodeCount": len(ev.opcodes), "cameraShots": cam_shots,
               "motionClips": motion_clips,
               "vfxResources": {"vfx": sorted(vfx_set), "anim": sorted(anim_set),
                                "sound": sorted(sound_set)}})
    return tl


def _npc_look_rows(ids: list[int]) -> dict:
    """Fetch ``{npcid: {name, look(bytes), pos, rot}}`` for the given npc ids.

    Registered custom NPCs win over the live ``npc_list`` row (so a custom id never
    silently renders as the retail NPC it collided with). Everything else comes from
    ``npc_list``; still-missing ids fall back to the registry so a freshly-created
    custom NPC renders on-stage before the SQL is ever applied."""
    ids = [int(i) for i in ids if i]
    if not ids:
        return {}
    # Custom registry first — authoritative for anything we've registered.
    out: dict = dict(_custom_npc_rows(ids))
    need = [i for i in ids if i not in out]
    if not need:
        return out
    try:
        conn = _db_connect({})
    except Exception:
        conn = None
    if conn is not None:
        try:
            with conn.cursor() as cur:
                placeholders = ",".join(["%s"] * len(need))
                cur.execute(
                    f"SELECT npcid, name, look, pos_x, pos_y, pos_z, pos_rot "
                    f"FROM npc_list WHERE npcid IN ({placeholders})", need)
                for npcid, name, look, px, py, pz, rot in cur.fetchall():
                    nm = name.decode("utf-8", "replace") if isinstance(name, (bytes, bytearray)) else name
                    out[int(npcid)] = {"name": nm, "look": bytes(look or b""),
                                       "pos": [float(px or 0), float(py or 0), float(pz or 0)],
                                       "rot": int(rot or 0)}
        except Exception:
            pass
        finally:
            conn.close()
    return out


# Built character GLBs, reused across actor requests: {look hex → (glb bytes, meta dict)}.
_ACTOR_GLB_CACHE: dict = {}


def _character_glb(look: bytes, extra_clips=None) -> tuple:
    """Assemble a character/model GLB for a 20-byte ``look`` → ``(glb_bytes|None, meta)``,
    cached by (look + embedded-clip set). ``extra_clips`` = ``{tag: {"file_id", "clip"}}`` of
    cutscene motion clips to embed (named by tag). ``meta`` carries type/race/parts/missing
    (or an error)."""
    clip_sig = ""
    if extra_clips:
        clip_sig = ";".join(f"{t}:{i.get('file_id')}:{i.get('clip')}"
                            for t, i in sorted(extra_clips.items()))
    key = look.hex() + ("|" + clip_sig if clip_sig else "")
    if key in _ACTOR_GLB_CACHE:
        return _ACTOR_GLB_CACHE[key]
    from xi.gear.xi_character import build_character_glb
    out_dir = Path("exports") / "cutscene_actors"
    try:
        r = build_character_glb(look, out_dir, name=f"actor_{key[:12]}", extra_clips=extra_clips)
    except Exception as exc:
        result = (None, {"ok": False, "error": str(exc)})
        _ACTOR_GLB_CACHE[key] = result
        return result
    glb_path = r.get("glb")
    data = None
    if r.get("ok") and glb_path and Path(glb_path).is_file():
        data = Path(glb_path).read_bytes()
    meta = {k: v for k, v in r.items() if k != "glb"}
    result = (data, meta)
    _ACTOR_GLB_CACHE[key] = result
    return result


# The client resolves a 0x45 scene ref `p` to a file id as `30704 + _datid_helper(p)`.
# _datid_helper has three tiers:
#   p < 300            → file 30704 + p           (band 30704..31003, retail-full — don't touch)
#   300 <= p < 600     → file 56641 + p           (band 56941..57240)  ★ SAFE for custom cameras
#   p >= 600           → file 70347 + p           (band 70947.. ~76k)  ✗ UNSAFE — crashes
#
# ★ 2026-07-15: A/B proved the high tier crashes the client even when the DAT bytes are a
#   pure retail camera pack. Same bytes at p=53 (fid 30757) and p=599 (fid 57240) work;
#   p=1019 (fid 71366) instant-crashes. Custom cameras MUST stay in the mid band only.
#   (Refs store p as a work value; high-tier ids also sit awkwardly near u16 limits.)


def _scene_p_for(file_id: int) -> int | None:
    """Inverse of the 0x45 scene datid map: a scene DAT at ``file_id`` → the ref value
    ``p`` that reaches it, or ``None`` if the id isn't in a reachable band."""
    if 30704 <= file_id < 31004:           # tier p<300      : file = 30704 + p
        return file_id - 30704
    if 56941 <= file_id <= 57240:          # tier 300<=p<600 : file = 56641 + p
        return file_id - 56641
    if 70947 <= file_id <= 76000:          # tier p>=600     : file = 70347 + p
        return file_id - 70347
    return None


def _camera_scene_id_safe(file_id: int | None) -> bool:
    """True if ``file_id`` is in the mid band we may use for custom cameras."""
    if file_id is None:
        return False
    p = _scene_p_for(int(file_id))
    return p is not None and 300 <= p < 600


def _has_camera_track(cutscene: dict) -> bool:
    # A camera exists if the legacy single 'camera' track OR the split Position sub-track
    # ('campos', which defines the shots) carries keyframes. Post-Phase-3 the editor sends the
    # three sub-tracks (campos/camrot/camzoom); older defs still send 'camera'.
    tl = cutscene.get("timeline") or {}
    return any(t.get("kind") in ("camera", "campos") and t.get("keyframes")
               for t in (tl.get("tracks") or []))


def _camera_scene_fileid(zone_id: int, cutscene: dict) -> int | None:
    """The camera scene DAT file-id for this cutscene, or ``None`` if it has no camera
    track. Reuses a stored id only when it is in the **safe mid band** (p 300..599 /
    file 56941..57240). High-tier ids (71k+, p≥600) are rejected — they crash the client
    even with valid retail scene bytes. Allocates the lowest free mid-band hole."""
    if not _has_camera_track(cutscene):
        return None
    # 1) Prefer an id the editor round-tripped (must still be mid-band safe).
    dict_fid = cutscene.get("cameraSceneFileId")
    if _camera_scene_id_safe(dict_fid):
        return int(dict_fid)
    # 2) Else reuse the id stored in the saved def for this event id (when safe).
    ev_field = cutscene.get("eventId")
    if ev_field not in (None, "auto"):
        for name in (
            f"{zone_id}_{int(ev_field)}.json",
            # actor-keyed def (preferred when present)
        ):
            f = _cutscene_defs_dir() / name
            if f.exists():
                try:
                    fid = json.loads(f.read_text(encoding="utf-8")).get("cameraSceneFileId")
                    if _camera_scene_id_safe(fid):
                        return int(fid)
                except Exception:
                    pass
        # actor-keyed: zone_actor_event.json
        for f in _cutscene_defs_dir().glob(f"{zone_id}_*_{int(ev_field)}.json"):
            try:
                fid = json.loads(f.read_text(encoding="utf-8")).get("cameraSceneFileId")
                if _camera_scene_id_safe(fid):
                    return int(fid)
            except Exception:
                pass
    from xi.ftable.xi_core import load_all_tables, resolve_dat
    tables = load_all_tables()

    def _free(fid):
        return not any(resolve_dat(fd, vd, fid)[0] for _, (fd, vd) in tables.items())
    # ★ Mid band ONLY (p 300..599 → file 56941..57240). Lowest free hole first.
    for p in range(300, 600):
        if _free(p + 56641):
            return p + 56641
    raise RuntimeError(
        "no free camera-scene file id in the safe mid band (p 300..599 / file 56941..57240). "
        "Free a mid-band slot or expand — high-tier 71k ids crash the client.")


# ── Camera-scene placement (user-controlled) ─────────────────────────────────
# The editor's Settings ▸ Camera DAT fields choose where a cutscene's camera scene DAT
# lands: volume `vt`, subdir, slot → ROM{vt}/{subdir}/{slot}.DAT. This is SEPARATE from the
# scene's file-id (the 0x45 `p` ref target, a safe mid-band id allocated above) — the
# placement only decides where the bytes go + the FTABLE value at that file-id.
#
# Registration mirrors `xi dats build` / `dats new`: patch BOTH the root FTABLE/VTABLE and
# the ROM{vt} FTABLE{vt}/VTABLE{vt}, in the base-game output mirror AND the pivot overlay pack.
# Real client is volume-direct (VTABLE version picks the ROM volume; FTABLE entry
# is the path on that volume). Overlay packs still shadow base by load order, so
# a FREE file-id slot must be set to the SAME (ftval, vt) across base + pivot
# tables. A stale/differing entry left in a shadowing pivot ROM10 table is what
# corrupts lookup → the 0x45 camera crash. (xim's OR-merge combine is a different
# model — don't treat it as the real client.)


def _rom_folder(vt: int) -> str:
    """Disk folder for a VTABLE version byte: 1 → 'ROM', else 'ROM{vt}'."""
    return "ROM" if vt == 1 else f"ROM{vt}"


def _parse_camera_dat(cutscene: dict) -> tuple[int, int, int] | None:
    """Parse the editor's Camera DAT fields → ``(vt, subdir, slot)``, or ``None`` when the
    cutscene has no camera track (nothing to place). Raises ``ValueError`` with a user-facing
    message when a camera track exists but the fields are missing/invalid — Publish is gated
    on this."""
    if not _has_camera_track(cutscene):
        return None
    cd = cutscene.get("cameraDat") or {}
    rom_raw = str(cd.get("rom", "")).strip().upper()
    if rom_raw.startswith("ROM"):
        rom_raw = rom_raw[3:].strip()
    path_raw = str(cd.get("path", "")).strip().strip("/\\")
    file_raw = str(cd.get("file", "")).strip()
    if not rom_raw or not path_raw or not file_raw:
        raise ValueError(
            "Set the Camera DAT (ROM, Path, Dat Filename) in Settings before publishing a "
            "cutscene with a camera.")
    try:
        vt = int(rom_raw)
    except ValueError:
        raise ValueError(f"Camera DAT ROM must be a number like 10, got {cd.get('rom')!r}.")
    # Path is the numeric subdir (tolerate a user pasting e.g. 'ROM10/490' — take the last part).
    try:
        subdir = int(path_raw.replace("\\", "/").split("/")[-1])
    except ValueError:
        raise ValueError(f"Camera DAT Path must be a folder number like 490, got {path_raw!r}.")
    try:
        slot = int(Path(file_raw).stem)
    except ValueError:
        raise ValueError(f"Camera DAT filename must be like 50.dat, got {file_raw!r}.")
    if not (1 <= vt <= 10):
        raise ValueError(f"Camera DAT ROM {vt} out of range (1..10).")
    if not (0 <= subdir <= 511):
        raise ValueError(f"Camera DAT Path {subdir} out of range (0..511).")
    if not (0 <= slot <= 127):
        raise ValueError(f"Camera DAT filename slot {slot} out of range (0..127).")
    return vt, subdir, slot


def _ensure_output_rom_table(vt: int) -> None:
    """Make sure ``FFXI_DIR/ROM{vt}/(F|V)TABLE{vt}.DAT`` exist before patching,
    else zero-filled to the root table size. No-op for vt==1 (patch_table
    seeds the root tables itself)."""
    if vt == 1:
        return
    import shutil
    from xi.xi_config import FFXI_DIR
    out_dir = Path(FFXI_DIR) / f"ROM{vt}"
    out_ft, out_vt = out_dir / f"FTABLE{vt}.DAT", out_dir / f"VTABLE{vt}.DAT"
    if out_ft.exists() and out_vt.exists():
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    base_ft = Path(FFXI_DIR) / f"ROM{vt}" / f"FTABLE{vt}.DAT"
    base_vt = Path(FFXI_DIR) / f"ROM{vt}" / f"VTABLE{vt}.DAT"
    root_ft, root_vt = Path(FFXI_DIR) / "FTABLE.DAT", Path(FFXI_DIR) / "VTABLE.DAT"
    if not out_ft.exists():
        shutil.copy2(base_ft, out_ft) if base_ft.is_file() else out_ft.write_bytes(b"\x00" * root_ft.stat().st_size)
    if not out_vt.exists():
        shutil.copy2(base_vt, out_vt) if base_vt.is_file() else out_vt.write_bytes(b"\x00" * root_vt.stat().st_size)


def _camera_path_collision(vt: int, subdir: int, slot: int, file_id: int) -> str | None:
    """Refuse Camera DAT placements that would clobber a non-camera file.

    The editor's Camera DAT fields are free-form (ROM/path/file). If they land on a path
    already holding an entity mesh (or anything that is not an ``evte`` camera scene), the
    pivot overlay shadows the real DAT and the model vanishes in-game — that is exactly how
    Battle Worn Byakko (ROM10/25/40.DAT) went invisible after a cutscene publish pointed its
    camera there. Returns a user-facing error string, or ``None`` when the path is free/ours.
    """
    from xi.xi_config import FFXI_DIR, FFXI_PIVOT_DIR

    rel = Path(_rom_folder(vt)) / str(subdir) / f"{slot}.DAT"
    candidates = [
        Path(FFXI_DIR) / rel,
    ]
    pivot = Path(FFXI_PIVOT_DIR) if str(FFXI_PIVOT_DIR or "").strip() else None
    if pivot is not None:
        candidates.append(pivot / rel)

    for p in candidates:
        if not p.is_file():
            continue
        try:
            head = p.read_bytes()[:4]
        except OSError:
            continue
        # Already a camera scene (ours or a previous publish of the same slot) — safe to overwrite.
        if head == b"evte":
            continue
        kind = head.decode("ascii", "replace") if head else "?"
        return (
            f"Camera DAT {rel.as_posix()} already holds a non-camera file "
            f"({kind!r}, {p.stat().st_size} bytes at {p}). "
            f"Pick a free ROM/Path/Filename in Settings — overwriting it would hide the "
            f"model that lives there (file id {file_id} would shadow it via the Ashita overlay)."
        )
    return None


def _write_camera_scene(file_id: int, scene_bytes: bytes,
                        vt: int, subdir: int, slot: int) -> tuple[str, int, list[str]]:
    """Write ``scene_bytes`` to the base-game output mirror at ``ROM{vt}/{subdir}/{slot}.DAT``
    and register ``file_id`` in BOTH the root and ROM{vt} FTABLE/VTABLE (same as
    ``xi dats build``). Returns ``(scene_path, ftval, [game table paths written])``."""
    from xi.ftable.xi_core import ftable_path, vtable_path, patch_table
    from xi.xi_config import FFXI_DIR, editable_dat

    hit = _camera_path_collision(vt, subdir, slot, file_id)
    if hit:
        raise ValueError(hit)

    ftval = (subdir << 7) | slot
    dst = Path(FFXI_DIR) / _rom_folder(vt) / str(subdir) / f"{slot}.DAT"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(scene_bytes)

    # Root table (always full-size) + ROM{vt} table, both set to the same (ftval, vt)
    # so volume-direct lookup (and any overlay shadow) resolves this placement.
    patch_table(ftable_path(1), vtable_path(1), file_id, ftval, vt)
    table_paths = [
        str(editable_dat(ftable_path(1), fresh=False)),
        str(editable_dat(vtable_path(1), fresh=False)),
    ]
    if vt != 1:
        _ensure_output_rom_table(vt)
        patch_table(ftable_path(vt), vtable_path(vt), file_id, ftval, vt)
        table_paths += [
            str(editable_dat(ftable_path(vt), fresh=False)),
            str(editable_dat(vtable_path(vt), fresh=False)),
        ]
    return str(dst), ftval, table_paths


def _publish_pivot_tables(file_id: int, ftval: int, vt: int,
                          scene_path: Path | str | None = None) -> tuple[list[str], list[str]]:
    """Mirror one camera-scene registration into the pivot overlay pack the same way
    ``xi dats build --target pivot`` does: patch the pack's root FTABLE/VTABLE AND its
    ROM{vt} FTABLE{vt}/VTABLE{vt} to ``(ftval, vt)`` at ``file_id`` (seeding the ROM{vt} tables
    from the base install if the pack doesn't ship them), then copy the scene DAT to the
    matching ``ROM{vt}/{subdir}/{slot}.DAT`` under the pack. Returns ``(written paths,
    warnings)``."""
    import struct
    import shutil
    from xi.xi_config import FFXI_PIVOT_DIR, FFXI_DIR
    written: list[str] = []
    warnings: list[str] = []
    root = Path(FFXI_PIVOT_DIR) if str(FFXI_PIVOT_DIR or "").strip() else None
    if root is None or not root.is_dir():
        return written, warnings

    def _patch(ft: Path, vtp: Path) -> None:
        try:
            fdata = bytearray(ft.read_bytes())
            vdata = bytearray(vtp.read_bytes())
            if file_id * 2 + 2 > len(fdata) or file_id >= len(vdata):
                warnings.append(f"pivot table too small for file id {file_id}: {ft}")
                return
            struct.pack_into("<H", fdata, file_id * 2, ftval)
            vdata[file_id] = vt & 0xFF
            ft.write_bytes(fdata)
            vtp.write_bytes(vdata)
            written.extend([str(ft), str(vtp)])
        except OSError as exc:
            warnings.append(f"pivot table patch failed ({ft.name}): {exc}")

    # Root tables — authoritative for the client's vt lookup.
    ft, vtp = root / "FTABLE.DAT", root / "VTABLE.DAT"
    if ft.is_file() and vtp.is_file():
        _patch(ft, vtp)
    else:
        warnings.append("pivot has no root FTABLE/VTABLE — camera scene not registered in pivot.")

    # ROM{vt} tables — dats build patches these too. Seed from the base install (or the pack
    # root) when the pack doesn't already ship them, so the entry is present without dropping
    # any other customs the pack registered in ROM{vt}.
    if vt != 1:
        rdir = root / f"ROM{vt}"
        rft, rvt = rdir / f"FTABLE{vt}.DAT", rdir / f"VTABLE{vt}.DAT"
        if not (rft.is_file() and rvt.is_file()):
            rdir.mkdir(parents=True, exist_ok=True)
            base_rft = Path(FFXI_DIR) / f"ROM{vt}" / f"FTABLE{vt}.DAT"
            base_rvt = Path(FFXI_DIR) / f"ROM{vt}" / f"VTABLE{vt}.DAT"
            try:
                if not rft.is_file():
                    shutil.copy2(base_rft if base_rft.is_file() else ft, rft)
                if not rvt.is_file():
                    shutil.copy2(base_rvt if base_rvt.is_file() else vtp, rvt)
            except OSError as exc:
                warnings.append(f"pivot ROM{vt} table seed failed: {exc}")
        if rft.is_file() and rvt.is_file():
            _patch(rft, rvt)

    # Copy scene DAT to ROM{vt}/<subdir>/<slot>.DAT under the pack (mirrors the Game layout).
    if scene_path is not None:
        sp = Path(scene_path)
        if sp.is_file():
            try:
                rel = None
                parts = sp.parts
                for i, part in enumerate(parts):
                    if part.upper() == _rom_folder(vt).upper() and i + 2 < len(parts):
                        rel = Path(*parts[i:i + 3])
                        break
                dst = root / (rel if rel is not None else Path(_rom_folder(vt)) / sp.parent.name / sp.name)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sp, dst)
                written.append(str(dst))
            except OSError as exc:
                warnings.append(f"pivot scene DAT copy failed: {exc}")
        else:
            warnings.append(f"pivot scene DAT missing on disk: {sp}")
    return written, warnings


def _cast_motion_maps(cutscene: dict) -> dict:
    """Per-cast schedulable-motion maps for tag normalisation →
    ``{castId: {"valid": set(routineTag), "clip2routine": {clipTag: routineTag}, "name"}}``.

    Built from each fixed-model cast NPC's model DAT (its 0x07 routines + resolved clips,
    via :func:`list_look_animations`). Equipped-look cast members are omitted — their
    gestures ride the shared bank and their tags pass through untouched. '@'-prefixed
    system routines (auto-turn @tl0/@tr0) stay VALID but never capture a clip mapping —
    @tl0 references wlk?/idl? clips internally, and mapping 'wlk0'→'@tl0' would turn a
    walk keyframe into a turn-in-place."""
    out = {}
    cast = ((cutscene.get("cast") or {}).get("cast")) or []
    wanted = []
    for c in cast:
        ent = c.get("entity")
        if not ent or ent == "player":
            continue
        try:
            wanted.append((c.get("id"), int(str(ent).replace("0x", "").replace("0X", ""), 16),
                           c.get("name")))
        except ValueError:
            continue
    if not wanted:
        return out
    try:
        rows = _npc_look_rows([aid for _cid, aid, _n in wanted])
    except Exception:
        return out
    from xi.gear.xi_character import list_look_animations
    for cid, aid, name in wanted:
        row = rows.get(aid)
        if not row:
            continue
        try:
            r = list_look_animations(row["look"])
        except Exception:
            continue
        if not r.get("ok") or r.get("type") == "equipped":
            continue
        motions = r.get("motions") or []
        c2r = {}
        for m in motions:
            if not str(m["tag"]).startswith("@"):
                c2r.setdefault(m["clip"], m["tag"])
        out[cid] = {"valid": {m["tag"] for m in motions},
                    "clip2routine": c2r, "name": name or cid}
    return out


_GESTURE_BANK_TAGS_CACHE: dict = {}


def _gesture_bank_tags(bank: int = 60) -> frozenset:
    """Routine tags ACTUALLY present in the shared gesture bank DAT — ground truth for
    the compiler's 0x5B-vs-0x2C dispatch.

    0x5B ReadEventMotionRes loads file ``32104 + bank`` (default bank 60 → 32164, the
    humanoid talk/think/bow set) onto the entity and plays the tag from it. The compiler
    used to trust its hardcoded ``_GESTURE_TAGS`` mirror of this file, which drifts (the
    docstrings mention 'han0' yet the mirror omits it → a han0 keyframe no-oped). Parsing
    the bank's 0x07 sections gives the real inventory. Cached per bank; empty frozenset on
    any failure — the compiler then falls back to ``_GESTURE_TAGS``."""
    if bank in _GESTURE_BANK_TAGS_CACHE:
        return _GESTURE_BANK_TAGS_CACHE[bank]
    tags = frozenset()
    try:
        from xi.xi_config import FFXI_DIR, read_path_for
        from xi.ftable.xi_core import scan_file_ids
        from xi.event.xi_event import _scene_sections
        fid = 32104 + bank
        # ☠ scan_file_ids compacts its result — match on file_id, never positionally.
        hit = next((h for h in scan_file_ids([fid]) if h.get("file_id") == fid), None)
        if hit:
            data = read_path_for(Path(FFXI_DIR) / hit["dat"]).read_bytes()
            tags = frozenset(t for _o, t, tc, _s in _scene_sections(data) if tc == 0x07)
    except Exception:
        tags = frozenset()
    _GESTURE_BANK_TAGS_CACHE[bank] = tags
    return tags


def _compile_cutscene(params: dict) -> dict:
    """Compile a ``xi.cutscene.v1`` JSON to bytecode and (optionally) write to the mirror.

    ``{zone, zoneId?, cutscene, dryRun?}`` → resolve the zone Event + Dialog DATs, hand
    them to :func:`xi.event.xi_compile.compile_cutscene`, return
    ``{ok, eventId, luaStub, warnings, sizes, disasm}`` (dry-run) or the same plus
    ``written: [<paths>]`` when actually persisted. Emits ``.base`` backups on first
    write (same convention as ``event dialogue new``).
    """
    from xi.xi_config import FFXI_DIR, read_path_for, output_path_for
    from xi.zone.xi_inject import zone_event_file_id, zone_dialog_file_id
    from xi.ftable.xi_core import scan_file_ids
    from xi.event import xi_compile, xi_event as _ev

    cutscene = params.get("cutscene")
    if not isinstance(cutscene, dict):
        return {"ok": False, "error": "params.cutscene must be a xi.cutscene.v1 object"}
    zone_rel = _zone_rel(params.get("zone", ""))
    zone_id = params.get("zoneId")
    if zone_id is None and zone_rel:
        zone_id, _ = _resolve_zone_id(zone_rel)
    if zone_id is None:
        return {"ok": False, "error": "could not determine zone id"}
    zone_id = int(zone_id)

    ev_hits = scan_file_ids([zone_event_file_id(zone_id)])
    dg_hits = scan_file_ids([zone_dialog_file_id(zone_id)])
    if not ev_hits or not dg_hits:
        return {"ok": False, "error": f"no event/dialog DAT for zone {zone_id}"}
    ev_src = read_path_for(Path(FFXI_DIR) / ev_hits[0]["dat"])
    dg_src = read_path_for(Path(FFXI_DIR) / dg_hits[0]["dat"])
    if not ev_src.exists() or not dg_src.exists():
        return {"ok": False, "error": "event/dialog DAT missing on disk"}

    # Camera track → allocate (or reuse) this cutscene's own camera scene DAT file-id,
    # and pass its ref value (p) so every camera 0x45 points at it.
    try:
        cam_fid = _camera_scene_fileid(zone_id, cutscene)
    except Exception as e:
        return {"ok": False, "error": f"camera scene alloc failed: {e}"}
    cam_p = _scene_p_for(cam_fid) if cam_fid else None    # ref value that reaches this file (per-band)

    # The Camera DAT placement (ROM / Path / Filename from Settings) is REQUIRED to publish a
    # cutscene with a camera. Dry-run preview is allowed without it (nothing is written); a real
    # publish with a camera but no/invalid placement fails fast with a clear message.
    cam_place = None
    if cam_fid:
        try:
            cam_place = _parse_camera_dat(cutscene)
        except ValueError as e:
            if not params.get("dryRun"):
                return {"ok": False, "error": str(e)}
        # Fail BEFORE compiling/writing when the Camera DAT path would clobber a mesh
        # (or any non-evte file). Dry-run still compiles so Preview can show bytecode.
        if cam_place and not params.get("dryRun"):
            vt, subdir, slot = cam_place
            hit = _camera_path_collision(vt, subdir, slot, cam_fid)
            if hit:
                return {"ok": False, "error": hit}

    # ★ Normalise animation tags BEFORE lowering: non-gesture tags emit as 0x2C SetAction,
    # which only fires the actor's OWN 0x07 routines — legacy defs stored raw 0x2B clip ids
    # (at00/btl0) that silently no-op in game. Rewrites clip→routine where the model allows.
    # The same maps feed compile_cutscene so an OWN routine outranks a same-named curated
    # gesture (custom 'tlk0' on a monster rig must not ride the 0x5B humanoid bank).
    # bank_tags = the REAL routine inventory of the shared gesture bank DAT (32104+bank),
    # replacing the compiler's hardcoded _GESTURE_TAGS mirror for the 0x5B-vs-0x2C call.
    cast_motions = _cast_motion_maps(cutscene)
    bank_tags = _gesture_bank_tags(int((cutscene.get("flags") or {}).get("animBank") or 60))
    norm_warnings = xi_compile.normalize_cutscene_anim_tags(cutscene, cast_motions,
                                                            bank_tags=bank_tags)

    try:
        res = xi_compile.compile_cutscene(cutscene, ev_src.read_bytes(), dg_src.read_bytes(),
                                          camera_scene_ref=cam_p, cast_motions=cast_motions,
                                          bank_tags=bank_tags)
    except (xi_compile.CutsceneCompileError, NotImplementedError) as e:
        return {"ok": False, "error": str(e)}
    res.warnings.extend(norm_warnings)

    # Disassemble the freshly-emitted event so the editor can preview the opcodes.
    disasm = []
    try:
        actors = _ev.parse_raw_actors(res.event_dat)
        owner = next(a for a in actors if a.actor_id ==
                     xi_compile._resolve_entity(cutscene["cast"]["cast"][
                         next(i for i, c in enumerate(cutscene["cast"]["cast"])
                              if c["id"] == cutscene["actor"])
                     ]["entity"]))
        idx = owner.event_ids.index(res.event_id)
        off = owner.event_offsets[idx]
        scene = owner.scene_data
        i = off
        while i < len(scene) and len(disasm) < 200:
            op = scene[i]
            sub = scene[i + 1] if i + 1 < len(scene) else 0
            sz = _ev._opcode_size(op, sub)
            if not sz:
                break
            disasm.append({
                "offset": i - off,
                "op": f"0x{op:02X}",
                "name": _ev._opcode_name(op),
                "args": scene[i + 1:i + sz].hex(),
            })
            if op == 0x21:
                break
            i += sz
    except Exception:
        pass

    payload = {
        "ok": True,
        "eventId": res.event_id,
        "luaStub": res.lua_stub,
        "warnings": list(res.warnings),
        "sizes": {
            "eventDatBefore": ev_src.stat().st_size,
            "eventDatAfter": len(res.event_dat),
            "dialogDatBefore": dg_src.stat().st_size,
            "dialogDatAfter": len(res.dialog_dat),
        },
        "disasm": disasm,
        "eventDat": ev_hits[0]["dat"],
        "dialogDat": dg_hits[0]["dat"],
    }

    if params.get("dryRun"):
        payload["dryRun"] = True
        return payload

    ev_out = output_path_for(ev_src)
    dg_out = output_path_for(dg_src)
    written = []
    for src, out, blob in [(ev_src, ev_out, res.event_dat),
                            (dg_src, dg_out, res.dialog_dat)]:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        base = Path(str(out) + ".base")
        if not base.exists():
            base.write_bytes(Path(src).read_bytes())
        out.write_bytes(blob)
        written.append(str(out))

    # ☠ XIPivot shadowing: when the overlay pack carries this DAT (shipped there by
    # `dats build --target pivot` / packaging), the client reads the PACK copy and a
    # stale one silently undoes the publish — cast NPCs "don't appear" even though the
    # Game-dir DAT is perfect. Mirror the fresh bytes into the pack whenever the file
    # already exists there (absent file = no shadowing = nothing to do).
    if params.get("publishPivot", True):
        from xi.xi_config import FFXI_PIVOT_DIR
        piv_root = Path(FFXI_PIVOT_DIR) if str(FFXI_PIVOT_DIR or "").strip() else None
        if piv_root is not None:
            for rel, blob in [(ev_hits[0]["dat"], res.event_dat),
                              (dg_hits[0]["dat"], res.dialog_dat)]:
                piv = piv_root / rel
                if piv.is_file():
                    try:
                        piv.write_bytes(blob)
                        written.append(str(piv))
                    except Exception as exc:
                        payload.setdefault("warnings", []).append(
                            f"stale pivot copy NOT updated ({rel}): {exc} — the client "
                            f"will keep loading the old cutscene from the overlay")

    # Camera scene DAT: write the freshly-built evte+Route+EffectRoutine resource to the
    # user-chosen ROM{vt}/<subdir>/<slot>.DAT and register its file-id (root + ROM{vt}, base
    # + pivot) so the client can load it when the cutscene's 0x45 fires.
    if res.scene_dat and cam_fid and cam_place:
        vt, subdir, slot = cam_place
        try:
            scene_path, ftval, game_tables = _write_camera_scene(
                cam_fid, res.scene_dat, vt, subdir, slot)
            written.append(scene_path)
            written += game_tables            # base root FTABLE/VTABLE + ROM{vt} FTABLE{vt}/VTABLE{vt}
            # "Publish Cutscenes to Pivot" (editor setting, default ON): mirror the same
            # registration into the pivot overlay pack's root + ROM{vt} F/VTABLEs AND copy the
            # scene DAT into the pack, so the pack's tables shadow-resolve the file too.
            if params.get("publishPivot", True):
                pv_written, pv_warnings = _publish_pivot_tables(
                    cam_fid, ftval, vt, scene_path=scene_path)
                written += pv_written
                for w in pv_warnings:
                    payload.setdefault("warnings", []).append(w)
        except Exception as exc:
            payload.setdefault("warnings", []).append(f"camera scene DAT not written: {exc}")

    # "Hide cast NPC names" — server-side, NOT an event opcode: retail cutscenes DO
    # render floating names; nameless actors (the "???" qm markers) carry npc_list
    # namevis bit 0x08 in their spawn packet. Flag ON → set the bit on every cast NPC
    # except the trigger NPC; flag turned OFF after being on → clear it again (the
    # previous saved def tells us whether we set it). Needs a server restart to load.
    try:
        _publish_cast_namevis(cutscene, zone_id, params, payload)
    except Exception as exc:
        payload.setdefault("warnings", []).append(f"cast namevis update failed: {exc}")

    # Save the FULL cutscene definition keyed by zone+actor+eventId so Edit
    # Cutscene can reload it losslessly. Actor is required: retail reuses the
    # same event id on multiple NPCs (Maat 93 on 0x010F3031 and 0x010F3032).
    try:
        defs_dir = _cutscene_defs_dir()
        defs_dir.mkdir(parents=True, exist_ok=True)
        cs_out = dict(cutscene)
        cs_out["eventId"] = res.event_id
        owner_ent = None
        try:
            owner_ent = xi_compile._resolve_entity(
                next(c for c in (cutscene.get("cast") or {}).get("cast") or []
                     if c.get("id") == cutscene.get("actor")).get("entity"))
        except Exception:
            owner_ent = None
        if owner_ent is not None:
            cs_out["actorId"] = int(owner_ent) & 0xFFFFFFFF
        if cam_fid:
            cs_out["cameraSceneFileId"] = cam_fid    # reuse the same scene file on republish
        if owner_ent is not None:
            def_path = defs_dir / f"{zone_id}_{int(owner_ent) & 0xFFFFFFFF}_{res.event_id}.json"
        else:
            def_path = defs_dir / f"{zone_id}_{res.event_id}.json"
        def_path.write_text(json.dumps(cs_out, indent=2), encoding="utf-8")
        # Keep legacy zone_event.json in sync when actor is known so older
        # loaders still find something (prefer actor-keyed on read).
        if owner_ent is not None:
            legacy = defs_dir / f"{zone_id}_{res.event_id}.json"
            try:
                legacy.write_text(json.dumps(cs_out, indent=2), encoding="utf-8")
            except OSError:
                pass
    except Exception as exc:
        payload.setdefault("warnings", []).append(f"could not save cutscene def: {exc}")

    payload["written"] = written
    if cam_fid:
        payload["cameraSceneFileId"] = cam_fid   # editor stores this + sends it back → stable file, no churn
    return payload


# npc_list.namevis = the HIGH byte of packet 0x000E Flags3 (XiPackets): bit 0x20
# (packet bit 29) = "health bar hidden and the name above their head not rendered".
# ☠ NOT 0x08 (packet bit 27) — that's "untargetable + name hidden under specific
# conditions" and verified in-game to NOT hide cutscene cast names. Retail zone-243
# rows agree: Survival_Guide / Proto-Waypoint (no floating name) carry 0x20.
_NAMEVIS_HIDE_BIT = 0x20


def _publish_cast_namevis(cutscene: dict, zone_id: int, params: dict, payload: dict) -> None:
    """Apply the ``flags.hideNpcNames`` presentation option at publish time.

    True  → ``namevis |= 0x08`` on every non-player, non-owner cast NPC (live DB +
    the custom-NPC registry so packaged SQL matches).
    False → clear the bit, but ONLY when the previously saved def for this event had
    the flag on — so we never strip an intentional retail hidden-name value.
    """
    from xi.event import xi_compile as _xc
    from xi.entity import xi_custom_npc as cn

    want = cutscene.get("flags", {}).get("hideNpcNames")
    owner_id = cutscene.get("actor")
    cast = (cutscene.get("cast") or {}).get("cast") or []
    # Every cast NPC EXCEPT the trigger NPC. namevis is a WORLD property — hiding the
    # trigger NPC's name here left it nameless standing in the zone (regression,
    # reverted 2026-07-20). For a nameless owner in-scene, register a custom-NPC
    # clone of it and cast the clone instead of the world NPC.
    ents = []
    for c in cast:
        if c.get("id") == owner_id:
            continue
        try:
            ent = _xc._resolve_entity(c.get("entity"))
        except Exception:
            continue
        if (ent & 0xFF000000) and (ent >> 24) != 0x7F:
            ents.append(ent)
    if not ents:
        return

    if not want:
        # Only undo our own work: check the previous saved def for this event.
        prev = _load_cutscene_def({"zoneId": zone_id,
                                   "eventId": cutscene.get("eventId"),
                                   "actorId": None}).get("cutscene") or {}
        if not (prev.get("flags") or {}).get("hideNpcNames"):
            return

    ids = ",".join(str(e) for e in ents)
    op = f"namevis | {_NAMEVIS_HIDE_BIT}" if want else f"namevis & ~{_NAMEVIS_HIDE_BIT}"
    conn = _db_connect(params)
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE npc_list SET namevis = {op} WHERE npcid IN ({ids})")
            changed = cur.rowcount
    finally:
        conn.close()

    # Mirror into the custom-NPC registry (+ regenerate its SQL) so a packaged
    # project ships the same namevis.
    reg = _load_custom_npcs()
    reg_changed = False
    for n in reg.get("npcs", []):
        if int(n.get("npcid", 0)) not in ents:
            continue
        nv = int(n.get("namevis", 0))
        new_nv = (nv | _NAMEVIS_HIDE_BIT) if want else (nv & ~_NAMEVIS_HIDE_BIT)
        if new_nv != nv:
            n["namevis"] = new_nv
            reg_changed = True
    if reg_changed:
        cn.save_registry(_custom_npcs_path(), reg)
        _custom_npc_write_sql()

    payload["castNamevis"] = {"hidden": bool(want), "rows": changed}
    payload.setdefault("warnings", []).append(
        f"cast NPC names {'hidden' if want else 'restored'} (namevis, {changed} row(s)) — "
        f"restart the map server to apply")


def _cutscene_defs_dir() -> Path:
    """Where authored cutscene definitions are stored (source of truth for editing)."""
    return workspace_root() / "cutscene-defs"


def _load_cutscene_def(params: dict) -> dict:
    """Return the saved cutscene definition for ``{zoneId, eventId, actorId?}``.

    Prefers ``{zone}_{actor}_{event}.json`` when ``actorId`` is given (required for
    retail events that reuse an id across NPCs). Falls back to legacy
    ``{zone}_{event}.json``. ``{ok: True, cutscene: None}`` when nothing saved."""
    zone_id = params.get("zoneId")
    if zone_id is None:
        zone_rel = _zone_rel(params.get("zone", ""))
        if zone_rel:
            zone_id, _ = _resolve_zone_id(zone_rel)
    try:
        event_id = int(params.get("eventId"))
        zone_id = int(zone_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid zoneId/eventId"}
    actor_id = params.get("actorId")
    try:
        actor_id = int(actor_id) & 0xFFFFFFFF if actor_id is not None else None
    except (TypeError, ValueError):
        actor_id = None
    defs_dir = _cutscene_defs_dir()
    candidates = []
    if actor_id is not None:
        candidates.append(defs_dir / f"{zone_id}_{actor_id}_{event_id}.json")
    candidates.append(defs_dir / f"{zone_id}_{event_id}.json")
    for f in candidates:
        if not f.is_file():
            continue
        try:
            cs = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"ok": False, "error": f"could not read cutscene def: {exc}"}
        # Legacy file for a different actor — skip when we know who we want.
        if actor_id is not None and cs.get("actorId") is not None:
            try:
                if (int(cs["actorId"]) & 0xFFFFFFFF) != actor_id:
                    continue
            except (TypeError, ValueError):
                pass
        return {"ok": True, "cutscene": cs}
    return {"ok": True, "cutscene": None}


def _delete_event(params: dict) -> dict:
    """Remove one event from an actor's block on the zone Event DAT.

    ``{zone, zoneId?, actorId, eventId}`` → parse actors, drop the matching
    (event_id, event_offset) pair from the actor's parallel arrays, rebuild the
    DAT, write it in place (+ .base backup on first write).

    The scene bytes for the deleted event stay in ``scene_data`` — no other event
    offset moves, so no chance of corrupting siblings. The bytes are just dead
    code the VM never enters. If we ever want to reclaim them, a compact-scene
    pass can slice + shift, but that's a follow-up.
    """
    from xi.xi_config import FFXI_DIR, read_path_for, output_path_for
    from xi.zone.xi_inject import zone_event_file_id
    from xi.ftable.xi_core import scan_file_ids
    from xi.event import xi_event as _ev

    zone_rel = _zone_rel(params.get("zone", ""))
    zone_id = params.get("zoneId")
    if zone_id is None and zone_rel:
        zone_id, _ = _resolve_zone_id(zone_rel)
    if zone_id is None:
        return {"ok": False, "error": "could not determine zone id"}
    zone_id = int(zone_id)
    try:
        actor_id = int(params.get("actorId"))
        event_id = int(params.get("eventId"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid actorId/eventId"}

    ev_hits = scan_file_ids([zone_event_file_id(zone_id)])
    if not ev_hits:
        return {"ok": False, "error": f"no event DAT for zone {zone_id}"}
    ev_src = read_path_for(Path(FFXI_DIR) / ev_hits[0]["dat"])
    if not ev_src.exists():
        return {"ok": False, "error": "event DAT missing on disk"}

    actors = _ev.parse_raw_actors(ev_src.read_bytes())
    actor = next((a for a in actors if a.actor_id == actor_id), None)
    if actor is None:
        return {"ok": False, "error": f"actor 0x{actor_id:08X} not found"}
    try:
        idx = actor.event_ids.index(event_id)
    except ValueError:
        return {"ok": False, "error": f"event {event_id} not on actor 0x{actor_id:08X}"}

    del actor.event_offsets[idx]
    del actor.event_ids[idx]
    actor.dirty = True

    # Strip the cast involvement markers the compiler added for this event on other
    # actors (single-`end` mini-events; see xi_compile step 5b). Only 0x00-first
    # bytecode is unlisted, so retail events are never touched.
    for a in actors:
        if a is actor or event_id not in a.event_ids:
            continue
        i = a.event_ids.index(event_id)
        off = a.event_offsets[i]
        if bytes(a.scene_data)[off:off + 1] != b"\x00":
            continue
        del a.event_ids[i]
        del a.event_offsets[i]
        if not a.event_offsets or off >= max(a.event_offsets):
            a.scene_data = bytes(a.scene_data)[:off]
        a.dirty = True

    new_bytes = _ev.build_event_dat(actors)

    ev_out = Path(output_path_for(ev_src))
    ev_out.parent.mkdir(parents=True, exist_ok=True)
    base = Path(str(ev_out) + ".base")
    if not base.exists():
        base.write_bytes(ev_src.read_bytes())
    ev_out.write_bytes(new_bytes)
    return {
        "ok": True,
        "written": str(ev_out),
        "actorId": actor_id,
        "eventId": event_id,
        "remainingEvents": len(actor.event_ids),
    }


_ACTOR_DATA_CACHE: dict = {}


def _cutscene_actor_data(params: dict) -> dict:
    """Build (cached) raw character data for one cutscene actor → geometry/skeleton/anim JSON.
    ``{actorId, motionClips?}`` → npc_list look → assembled data dict so Three.js can build a
    SkinnedMesh directly without parsing a GLB."""
    try:
        actor_id = int(params.get("actorId"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid 'actorId'"}
    rows = _npc_look_rows([actor_id])
    row = rows.get(actor_id)
    if not row:
        return {"ok": False, "error": f"npc {actor_id} not in npc_list (server DB reachable?)"}
    extra_clips = params.get("motionClips") or None
    clip_sig = ""
    if extra_clips:
        clip_sig = ";".join(f"{t}:{i.get('file_id')}:{i.get('clip')}"
                            for t, i in sorted(extra_clips.items()))
    # Cache-key on the source DAT's mtime too, so re-importing an animation into the model
    # DAT invalidates the cache and the preview picks up the new clip without a server
    # restart (the look hex alone doesn't change when you edit the DAT's contents).
    from xi.gear.xi_character import build_character_data, look_clip_dat
    src = look_clip_dat(row["look"])
    dat_sig = ""
    try:
        if src and src.exists():
            dat_sig = f"@{int(src.stat().st_mtime_ns)}"
    except OSError:
        pass
    key = row["look"].hex() + ("|" + clip_sig if clip_sig else "") + dat_sig
    if key in _ACTOR_DATA_CACHE:
        cached = _ACTOR_DATA_CACHE[key]
        return {"ok": True, "actorId": actor_id, "name": row["name"], **cached}
    try:
        r = build_character_data(row["look"], extra_clips)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error", "could not assemble model")}
    # Cache the geometry/anim payload (everything except actorId/name which vary per call)
    payload = {k: v for k, v in r.items() if k != "ok"}
    _ACTOR_DATA_CACHE[key] = payload
    return {"ok": True, "actorId": actor_id, "name": row["name"], **payload}


def _npc_defaults(params: dict) -> dict:
    """Default ``npc_list`` position + look for a list of entity ids — author-mode
    preview so the cutscene's cast NPCs show where they stand in the world (helps
    frame the camera). ``{ids:[int]}`` → ``{ok, actors:[{actorId, name, pos, rot,
    hasModel, look, runtimePos}]}``. Unlike :func:`_cutscene_actors` this is driven
    by the cast (not by npc show/hide beats), so it works for a brand-new cutscene."""
    ids = params.get("ids") or []
    # Preserve order but drop duplicate ids (cast can list the trigger twice).
    seen: set[int] = set()
    uniq: list[int] = []
    for i in ids:
        try:
            aid = int(i)
        except (TypeError, ValueError):
            continue
        if not aid or aid in seen:
            continue
        seen.add(aid)
        uniq.append(aid)
    ids = uniq
    rows = _npc_look_rows(ids)
    out = []
    for aid in ids:
        row = rows.get(aid)
        if not row:
            out.append({"actorId": aid, "hasModel": False, "dbMissing": True})
            continue
        rec = {"actorId": aid, "name": row["name"], "pos": row["pos"], "rot": row["rot"],
               "runtimePos": row["pos"] == [0.0, 0.0, 0.0]}
        try:
            from xi.gear.xi_core import parse_look
            lk = parse_look(row["look"])
            rec["look"] = {"type": lk["type"], "race": lk.get("raceName")}
            rec["hasModel"] = lk["type"] in ("standard", "equipped")
        except Exception:
            rec["hasModel"] = False
        out.append(rec)
    return {"ok": True, "actors": out, "dbReachable": bool(rows) or not ids}


def _cutscene_actors(params: dict) -> dict:
    """List the NPCs a cutscene reveals, for 3D preview: each actor's name, show/hide frames,
    npc_list position, and resolved appearance (look type/race). The GLB itself is fetched
    per-actor via :func:`_cutscene_actor_glb` (lazy, cached). ``{ok, actors:[…]}``."""
    tl = _cutscene(params)
    if not tl.get("ok"):
        return tl
    # Collect each NPC actor from the timeline's show/hide beats.
    actors: dict = {}
    for b in tl.get("beats", []):
        if b.get("type") != "npc" or not b.get("actorId"):
            continue
        aid = int(b["actorId"])
        a = actors.setdefault(aid, {"actorId": aid, "name": b.get("actor") or "",
                                    "showFrame": None, "hideFrame": None, "eventPos": None, "eventDir": None})
        if b.get("action") == "show" and a["showFrame"] is None:
            a["showFrame"] = b["frame"]
        if b.get("action") == "hide":
            a["hideFrame"] = b["frame"]
        if b.get("pos") and a["eventPos"] is None:        # 0xBA staging position from the event
            a["eventPos"] = b["pos"]
            a["eventDir"] = b.get("dir")
    anim_tracks = tl.get("animTracks", {})
    motion_clips = tl.get("motionClips", {})       # {entityId(str): {tag: {file_id, clip}}}
    # Movement paths (0x27 FollowPoints): {actorId: [{frame, duration, points, reversed}]}. The
    # event entity (magic 0x7FFFFFF8) maps to the cutscene's own actor id.
    try:
        ev_actor_id = int(params.get("actorId"))
    except (TypeError, ValueError):
        ev_actor_id = None
    motion_by_actor: dict = {}
    for b in tl.get("beats", []):
        m = b.get("motion")
        if not m:
            continue
        for aid in (b.get("actorIds") or []):
            tgt = ev_actor_id if (aid == 0x7FFFFFF8 and ev_actor_id) else aid
            motion_by_actor.setdefault(tgt, []).append({"frame": b["frame"], **m})
    rows = _npc_look_rows(list(actors))
    for aid, a in actors.items():
        a["animTrack"] = anim_tracks.get(str(aid), [])   # [{frame, tag, op}] motion timeline
        a["motionClips"] = motion_clips.get(str(aid), {})  # {tag: {file_id, clip}} resolved clips
        a["motion"] = motion_by_actor.get(aid, [])       # [{frame, duration, points, reversed}] paths
        row = rows.get(aid)
        # Prefer the event's 0xBA stage position; fall back to npc_list (placed zone NPCs).
        if a["eventPos"]:
            a["pos"] = a["eventPos"]
            a["dir"] = a["eventDir"]
            a["runtimePos"] = False
            a["posSource"] = "event"
        if row:
            if "pos" not in a:
                a["pos"] = row["pos"]
                a["rot"] = row["rot"]
                a["runtimePos"] = row["pos"] == [0.0, 0.0, 0.0]
                a["posSource"] = "npc_list"
            try:
                from xi.gear.xi_core import parse_look
                lk = parse_look(row["look"])
                a["look"] = {"type": lk["type"], "race": lk.get("raceName")}
                a["hasModel"] = lk["type"] in ("standard", "equipped")
            except Exception:
                a["hasModel"] = False
        else:
            a["hasModel"] = False
            a["dbMissing"] = True
    ordered = sorted(actors.values(), key=lambda a: (a["showFrame"] is None, a["showFrame"] or 0))
    return {"ok": True, "actors": ordered, "dbReachable": bool(rows) or not actors,
            "totalFrames": tl.get("totalFrames"), "fps": tl.get("fps")}


_ZONE_ENUM_CACHE: dict = {}


def _server_zone_names(server_path: Path) -> dict:
    """Parse ``scripts/enum/zone.lua`` → ``{zone_id: NAME}`` (e.g. 126 → 'QUFIM_ISLAND'),
    cached per server path. Empty dict if the file isn't found."""
    key = str(server_path)
    if key in _ZONE_ENUM_CACHE:
        return _ZONE_ENUM_CACHE[key]
    out: dict = {}
    enum = server_path / "scripts" / "enum" / "zone.lua"
    try:
        for line in enum.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\s*([A-Z][A-Z0-9_]+)\s*=\s*(\d+)\s*,", line)
            if m:
                out[int(m.group(2))] = m.group(1)
    except Exception:
        pass
    _ZONE_ENUM_CACHE[key] = out
    return out


def _server_event_info(params: dict) -> dict:
    """Find the server-side (LandSandBoat / CatsEyeXI) script that drives a cutscene event, so
    the editor can surface its zone, mission/quest, event params and any NPC movement.

    ``{serverPath, zoneId, eventId}`` → scan ``scripts/{missions,quests,zones,battlefields}``
    for files that START this event (``:event(<id>`` / ``:startEvent(<id>``) or HANDLE it
    (``[<id>] = function`` in onTrigger/onEventFinish/onEventUpdate), preferring files that also
    name the zone. Returns ``{ok, zoneName, matches:[{file, lines:[{n,text}], movement:[…]}]}``;
    ``movement`` pulls any ``setPos``/``pathThrough``/``:path``/``walk`` lines (the NPC-motion
    calls a cutscene might issue). Read-only — never writes."""
    server_path = Path(params.get("serverPath") or "")
    if not server_path or not server_path.is_dir():
        return {"ok": False, "error": f"Server path not found: {server_path}"}
    try:
        event_id = int(params.get("eventId"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid 'eventId'"}
    try:
        zone_id = int(params.get("zoneId"))
    except (TypeError, ValueError):
        zone_id = None

    zone_name = _server_zone_names(server_path).get(zone_id) if zone_id is not None else None
    start_re = re.compile(rf"(?<![0-9]):(?:start)?[Ee]vent\(\s*{event_id}\b")
    handler_re = re.compile(rf"\[\s*{event_id}\s*\]\s*=\s*function")
    move_re = re.compile(r"(setPos|pathThrough|:path\(|:walk|PathThrough|injectActionPacket|:setAnimation)")

    roots = [server_path / "scripts" / d for d in ("missions", "quests", "zones", "battlefields")]
    matches = []
    for root in roots:
        if not root.is_dir():
            continue
        for lua in root.rglob("*.lua"):
            try:
                text = lua.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            lines = text.splitlines()
            hit_lines = [(i + 1, ln) for i, ln in enumerate(lines)
                         if start_re.search(ln) or handler_re.search(ln)]
            if not hit_lines:
                continue
            names_zone = bool(zone_name and zone_name in text)
            movement = [{"n": i + 1, "text": ln.strip()[:200]}
                        for i, ln in enumerate(lines) if move_re.search(ln)]
            rel = str(lua.relative_to(server_path)).replace("\\", "/")
            matches.append({
                "file": rel,
                "namesZone": names_zone,
                "lines": [{"n": n, "text": ln.strip()[:200]} for n, ln in hit_lines[:8]],
                "movement": movement[:20],
            })
    matches.sort(key=lambda m: (not m["namesZone"], m["file"]))  # zone-named matches first
    # Friendly cutscene NAME (not in the DATs — only the server has it): derive from the best
    # zone-matched mission/quest filename, e.g. "1_08_At_Heavens_Door" → "At Heavens Door".
    name = None
    best_kind = None
    best = next((m for m in matches if m["namesZone"]), matches[0] if matches else None)
    if best:
        stem = best["file"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
        stem = re.sub(r"^[\d_]+", "", stem)            # strip the "1_08_" mission/chapter prefix
        cleaned = stem.replace("_", " ").strip()
        if cleaned:
            kind = "Mission" if "/missions/" in best["file"] else ("Quest" if "/quests/" in best["file"] else "Event")
            name = cleaned
            best_kind = kind
    return {"ok": True, "zoneName": zone_name, "zoneId": zone_id, "eventId": event_id,
            "name": name, "kind": (best_kind if name else None),
            "serverPath": str(server_path), "matches": matches[:12]}


# Friendly labels for gesture tags. What the dropdown OFFERS comes from the parsed
# bank inventory (`_gesture_bank_tags` → the real 0x07 routines in file 32164) — the
# old retail-frequency-harvested list offered 13 tags bank 60 doesn't hold (they live
# in OTHER bank files retail selects per event; against bank 60 they no-op) and missed
# 8 it does. This list is now only the label lookup + a no-DAT fallback.
_CUTSCENE_GESTURES = [
    ("idl0", "Idle / stand"), ("tlk0", "Talk"), ("tlk1", "Talk (alt)"),
    ("tlb0", "Talk (bow)"), ("tlb1", "Talk (bow, alt)"),
    ("thk1", "Think"), ("thk2", "Think (alt)"),
    ("ann0", "Announce / gesture"), ("ann1", "Announce (alt)"),
    ("pas0", "Impassioned"), ("han0", "Hand gesture"), ("han1", "Hand gesture (alt)"),
    ("ika0", "Angry"), ("ika1", "Angry (alt)"), ("ski0", "Pleased"),
    ("yor0", "Stagger / lean"),
]


def _gesture_dropdown() -> list:
    """Gesture entries for the editor's anim dropdowns: the REAL bank-60 inventory with
    friendly labels where known (tag-as-label otherwise). Falls back to the curated list
    when the bank DAT can't be read."""
    bank = _gesture_bank_tags()
    labels = dict(_CUTSCENE_GESTURES)
    if bank:
        return [{"tag": t, "label": labels.get(t, t)} for t in sorted(bank)]
    return [{"tag": t, "label": l} for t, l in _CUTSCENE_GESTURES]


def _npc_animations(params: dict) -> dict:
    """List animation tags for an NPC → ``{ok, gestures:[{tag,label}], modelClips:[tag], idle}``.

    ``{actorId}`` → npc_list look → the model/skeleton's own 0x2B clips (base locomotion),
    PLUS the curated cutscene-gesture set (talk/think/etc. — these live in the shared motion
    library, not the model DAT). The editor merges both into the animation dropdown; the user
    can still type any 4-char tag. Works without the DB (falls back to gestures only)."""
    out = {"ok": True,
           "gestures": _gesture_dropdown(),
           "modelClips": [], "idle": "idl0"}
    try:
        actor_id = int(params.get("actorId"))
    except (TypeError, ValueError):
        return out                         # no actor → curated gestures only
    try:
        row = _npc_look_rows([actor_id]).get(actor_id)
        if row:
            from xi.gear.xi_character import list_look_animations
            r = list_look_animations(row["look"])
            if r.get("ok"):
                clips = r.get("clips", [])
                out["modelClips"] = clips
                # ★ The SCHEDULABLE motions: the model's own 0x07 routines (ati0/atk0/
                # cast/dead…), each with the 0x2B clip it plays (for the editor preview).
                # These are the only tags the game can fire on the actor (0x2C SetAction);
                # the raw 0x2B clip ids above are preview/idle material only.
                out["motions"] = r.get("motions") or []
                if r.get("idle"):
                    out["idle"] = r["idle"]
                # The curated gesture set lives in the shared HUMANOID bank (file
                # 32164) authored for the standard PC race rigs (94-108 joints).
                # Fixed-model NPCs are all unique rigs (Maat 79, Cornelia 84,
                # Byakko 67 joints) — the bank binds by joint index and distorts on
                # them, so offer ONLY the model's own clips (matches AltanaViewer).
                # The anim inputs stay free-text: a known-good gesture tag can
                # still be typed manually.
                if r.get("type") != "equipped":
                    out["gestures"] = []
                    out["fixedModel"] = True
                    mob_prefixes = ("at0", "at1", "at2", "atm", "ma0", "ma1",
                                    "ma2", "dbi", "dbm", "dfi", "dfm")
                    if any(c.lower().startswith(mob_prefixes) for c in clips):
                        out["mobSkeleton"] = True
    except Exception:
        pass                               # DB offline / look unresolved → gestures only
    return out


def _cutscene_actor_glb(params: dict) -> dict:
    """Build (cached) the character GLB for one cutscene actor → ``{ok, bytesBase64, meta}``.
    ``{actorId, motionClips?}`` → npc_list look → assembled rigged GLB (equipped char or fixed
    model), with any resolved cutscene motion clips (``{tag: {file_id, clip}}``) embedded so the
    editor plays each gesture by tag."""
    try:
        actor_id = int(params.get("actorId"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid 'actorId'"}
    rows = _npc_look_rows([actor_id])
    row = rows.get(actor_id)
    if not row:
        return {"ok": False, "error": f"npc {actor_id} not in npc_list (server DB reachable?)"}
    extra_clips = params.get("motionClips") or None
    data, meta = _character_glb(row["look"], extra_clips)
    if not data:
        return {"ok": False, "error": meta.get("error", "could not assemble model"), "meta": meta}
    return {"ok": True, "actorId": actor_id, "name": row["name"],
            "bytesBase64": base64.b64encode(data).decode("ascii"), "meta": meta}


# Decoded scene resources, reused across cutscene opens: {dat path → (mtime, {routes,routines,dat})}.
_SCENE_CACHE: dict = {}


def _scene_data(file_id: int) -> dict:
    """Resolve a scheduler scene-resource file id → its decoded camera routes **and** effect
    routines, cached by path+mtime: ``{"dat": rel, "routes": {tag:kfs}, "routines": {tag:{…}}}``.
    The routines map each shot's action tag to the camera/vfx/anim/sound resources it fires."""
    from xi.xi_config import FFXI_DIR, read_path_for
    from xi.ftable.xi_core import scan_file_ids
    from xi.event.xi_event import (parse_camera_routes, parse_effect_routines,
                                     parse_routine_motion, _scene_sections)
    hits = scan_file_ids([file_id])
    if not hits:
        return {"dat": None, "routes": {}, "routines": {}, "motions": {}}
    rel = hits[0]["dat"]
    p = read_path_for(Path(FFXI_DIR) / rel)
    if not p.exists():
        return {"dat": rel, "routes": {}, "routines": {}, "motions": {}}
    key = str(p)
    try:
        mtime = p.stat().st_mtime_ns
    except OSError:
        mtime = 0
    cached = _SCENE_CACHE.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        raw = p.read_bytes()
        # Entity motion paths: each 0x07 routine that has a 0x27 FollowPoints → its 0x3E PointList.
        motions = {}
        for _o, tag, tc, _s in _scene_sections(raw):
            if tc == 0x07:
                m = parse_routine_motion(raw, tag)
                if m:
                    motions[tag] = m
        data = {"dat": rel, "routes": parse_camera_routes(raw),
                "routines": parse_effect_routines(raw), "motions": motions}
    except Exception:
        data = {"dat": rel, "routes": {}, "routines": {}, "motions": {}}
    _SCENE_CACHE[key] = (mtime, data)
    return data


def _scene_resource(params: dict) -> dict:
    """Return a scheduler scene-resource DAT's raw bytes (base64) by file id, so the editor can
    parse its 0x05 particle generators client-side (with the particle engine) and render the
    cutscene's VFX. ``{res: int}`` → ``{ok, res, datRel, bytesBase64}``. Cached by path+mtime."""
    from xi.xi_config import FFXI_DIR, read_path_for
    from xi.ftable.xi_core import scan_file_ids
    try:
        res = int(params.get("res"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid 'res'"}
    hits = scan_file_ids([res])
    if not hits:
        return {"ok": False, "error": f"no DAT registered for file {res}"}
    rel = hits[0]["dat"]
    p = read_path_for(Path(FFXI_DIR) / rel)
    if not p.exists():
        return {"ok": False, "error": f"scene resource not on disk: {rel}"}
    try:
        data = p.read_bytes()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "res": res, "datRel": rel,
            "bytesBase64": base64.b64encode(data).decode("ascii")}


def _subarea_file_id(sub_area_id: int) -> int:
    """Sub-area id → global file-table id (mirrors xim ``getSubAreaResourcePath``).

    Almost every sub-area fits the first file-table section (``+0x64``); only
    [Escha - Ru'Aun] sits in the high range (``+ (0x14768 - 0x271)``). The resulting
    id resolves through the same FTABLE/VTABLE as any other file."""
    if sub_area_id < 0x271:
        return sub_area_id + 0x64
    return sub_area_id + (0x14768 - 0x271)


def _subareas(params: dict) -> dict:
    """Resolve a zone's sub-area ids (shops / building interiors) to their interior
    DAT paths via the FTABLE.

    The ids come from the zone's ``0x36`` ZoneInteraction section, parsed client-side
    (entries whose ``sourceId`` starts with ``'m'``). ``{ids: [int]}`` →
    ``{ok, subAreas: [{id, fileId, dat}]}`` where ``dat`` is the game-relative interior
    DAT path (e.g. ``ROM/19/30.DAT``), fetchable under the editor's ``game/`` route, or
    ``None`` when the id isn't registered. Pure lookup — never writes."""
    from xi.ftable.xi_core import load_all_tables, scan_file_ids
    try:
        ids = sorted({int(i) for i in (params.get("ids") or []) if int(i) > 0})
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid 'ids'"}
    tables = load_all_tables()
    subareas = []
    for sid in ids:
        fid = _subarea_file_id(sid)
        hits = scan_file_ids([fid], tables)
        subareas.append({"id": sid, "fileId": fid, "dat": hits[0]["dat"] if hits else None})
    return {"ok": True, "subAreas": subareas}


# Reverse index: interior DAT path → owning zone. The parent link only exists inside each
# main zone's 0x36 section, so the reverse direction needs a one-time scan of every zone
# (~4s); cached to workspace/subarea_index.json (base-game DATs are read-only).
_SUBAREA_INDEX = None


def _scan_zone_subarea_params(data: bytes) -> list[int]:
    """Parse a zone DAT's plaintext 0x36 ZoneInteraction section → its ``'m'`` sub-area ids.
    Mirrors the client-side ``parseZoneInteractions`` (web/leveleditor/ffxi/zone.js)."""
    import struct as _s
    out, pos, length = [], 0, len(data)
    while pos + 16 <= length:
        meta = _s.unpack_from("<I", data, pos + 4)[0]
        size = ((meta >> 7) & 0xFFFFF) * 0x10
        if size <= 0:
            break
        if (meta & 0x7F) == 0x36 and data[pos + 0x10:pos + 0x13] == b"RID":
            ds = pos + 0x10
            q = ds + _s.unpack_from("<I", data, ds + 0x10)[0]   # ds + dataOffset
            n = _s.unpack_from("<I", data, q)[0]
            q += 0x10                                            # skip count + three zero u32
            for i in range(n):
                b = q + i * 0x40
                if b + 0x40 > pos + size:
                    break
                if data[b + 0x24:b + 0x25] == b"m":             # sourceId[0] == 'm' → sub-area
                    param = _s.unpack_from("<I", data, b + 0x2C)[0]
                    if param:
                        out.append(param)
        pos = (pos + size + 0xF) & ~0xF
    return out


def _build_subarea_index() -> dict:
    """Scan every zone's 0x36 → ``{interior_dat_lower: {parentId, parentName, parentDat, subAreaId}}``."""
    from xi.xi_config import FFXI_DIR, read_path_for
    from xi.zone.xi_list import get_zone_entries
    from xi.ftable.xi_core import load_all_tables, scan_file_ids
    tables = load_all_tables()
    index: dict = {}
    for e in get_zone_entries(path_prefix=""):
        p = Path(read_path_for(Path(FFXI_DIR) / e["path"]))
        if not p.exists():
            continue
        try:
            params = _scan_zone_subarea_params(p.read_bytes())
        except Exception:
            continue
        for sid in params:
            hits = scan_file_ids([_subarea_file_id(sid)], tables)
            if not hits:
                continue
            index.setdefault(hits[0]["dat"].lower(), {       # first owner wins (a few ids are shared)
                "parentId": e["id"], "parentName": e["name"],
                "parentDat": e["path"], "subAreaId": sid,
            })
    return index


def _subarea_index() -> dict:
    """Lazily load (or build + cache) the interior→parent reverse index. Rebuilt if the cache
    was made for a different FFXI install."""
    global _SUBAREA_INDEX
    if _SUBAREA_INDEX is not None:
        return _SUBAREA_INDEX
    from xi.xi_config import FFXI_DIR
    ffxi = str(Path(FFXI_DIR))
    cache = workspace_root() / "subarea_index.json"
    if cache.exists():
        try:
            blob = json.loads(cache.read_text(encoding="utf-8"))
            if blob.get("ffxiDir") == ffxi and isinstance(blob.get("index"), dict):
                _SUBAREA_INDEX = blob["index"]
                return _SUBAREA_INDEX
        except (ValueError, OSError):
            pass
    _SUBAREA_INDEX = _build_subarea_index()
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"ffxiDir": ffxi, "index": _SUBAREA_INDEX}, indent=1), encoding="utf-8")
    except OSError:
        pass
    return _SUBAREA_INDEX


def _subarea_parent(params: dict) -> dict:
    """Reverse lookup: is the loaded DAT a building interior, and if so which zone owns it?

    ``{zone}`` → ``{ok, parent: {zoneId, zoneName, dat, subAreaId} | None}``. ``dat`` is the
    parent's game-relative path (prepend ``game/`` to load it). The first call builds the
    reverse index (~4s, cached); later calls are instant."""
    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel:
        return {"ok": True, "parent": None}
    hit = _subarea_index().get(zone_rel.lower())
    if not hit:
        return {"ok": True, "parent": None}
    return {"ok": True, "parent": {
        "zoneId": hit["parentId"], "zoneName": hit["parentName"],
        "dat": hit["parentDat"], "subAreaId": hit["subAreaId"],
    }}



def _mob_list(params: dict) -> dict:
    """Mob catalog for the asset browser. ``{}`` → ``{ok, count, mobs:[{poolid,name,packetName,
    family,modelid}]}``. Pulls every ``mob_pools`` row from the server DB; ``modelid`` is the
    20-byte look blob (hex) that :func:`_mob_glb` turns into a renderable model."""
    try:
        conn = _db_connect(params)
    except Exception as exc:  # noqa: BLE001 — surface as data, never crash the bridge
        return {"ok": False, "error": f"Database unavailable: {exc}"}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT poolid, name, packet_name, familyid, HEX(modelid) "
                        "FROM mob_pools ORDER BY name")
            mobs = []
            for poolid, name, packet, family, modelhex in cur.fetchall():
                nm = name.decode("utf-8", "replace") if isinstance(name, (bytes, bytearray)) else (name or "")
                pk = packet.decode("utf-8", "replace") if isinstance(packet, (bytes, bytearray)) else (packet or "")
                mobs.append({"poolid": int(poolid), "name": nm or pk or f"pool {poolid}",
                             "packetName": pk, "family": int(family or 0),
                             "modelid": (modelhex or "").lower()})
            return {"ok": True, "count": len(mobs), "mobs": mobs}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Query failed: {exc}"}
    finally:
        conn.close()


def _mob_glb(params: dict) -> dict:
    """Assemble a mob's character GLB from its 20-byte model look. ``{modelid: hex}`` (or
    ``{poolid}`` to look the model up) → ``{ok, bytesBase64, meta}``. Reuses the cached cutscene
    character builder — the mob look format is identical to NPC / fixed-model looks."""
    modelhex = (params.get("modelid") or "").strip()
    if not modelhex and params.get("poolid") is not None:
        try:
            conn = _db_connect(params)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Database unavailable: {exc}"}
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT HEX(modelid) FROM mob_pools WHERE poolid=%s", (int(params["poolid"]),))
                row = cur.fetchone()
                modelhex = ((row[0] if row else "") or "")
        finally:
            conn.close()
    try:
        look = bytes.fromhex(modelhex)
    except ValueError:
        return {"ok": False, "error": f"invalid modelid hex: {modelhex!r}"}
    if not look:
        return {"ok": False, "error": "empty modelid"}
    data, meta = _character_glb(look)
    if not data:
        return {"ok": False, "error": meta.get("error", "could not assemble mob model"), "meta": meta}
    return {"ok": True, "modelid": modelhex.lower(),
            "bytesBase64": base64.b64encode(data).decode("ascii"), "meta": meta}


def _zone_name_for_dat(dat: Path) -> str | None:
    """Map a resolved DAT path to its zone name (e.g. 'Beaucedine Glacier')."""
    try:
        from xi.zone.xi_list import get_zone_entries
        dat_lower = dat.as_posix().lower()
        for e in get_zone_entries(path_prefix=""):
            if dat_lower.endswith(e["path"].lower()):
                return e["name"]
    except Exception:
        pass
    return None


def _hd_variant(params: dict) -> dict:
    """Does the HD asset pack ship a DAT for this zone? ``{exists, path}``.

    Drives the editor's HD-Zone read-only mode and the "Publish to HD Zone"
    button — both only make sense when FFXI_HD_DIR holds a real DAT for the
    zone. We deliberately do NOT count a zone the HD pack lacks (publishing
    there would just seed a vanilla copy with no HD textures)."""
    from xi.xi_config import FFXI_HD_DIR, hd_path_for
    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel or not FFXI_HD_DIR:
        return {"exists": False, "path": ""}
    try:
        hp = hd_path_for(_resolve_dat(zone_rel))
        return {"exists": hp.exists(), "path": str(hp)}
    except Exception:
        return {"exists": False, "path": ""}


def _clone_to_hd(params: dict) -> dict:
    """Clone-to-HD: copy the just-published STANDARD zone DAT byte-for-byte over the
    HD asset-pack DAT. Unlike a HD publish (which re-applies the change-set to the HD
    DAT and so keeps its high-res textures), this is a straight file copy — the
    standard map fully replaces the HD one. Used when a custom/new map has no real HD
    assets so the HD pack should just mirror the standard zone. Returns ``{ok, src, dst, bytes}``."""
    from xi.xi_config import FFXI_HD_DIR, hd_path_for, output_path_for
    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel:
        return {"ok": False, "error": "missing 'zone'"}
    if not FFXI_HD_DIR:
        return {"ok": False, "error": "FFXI_HD_DIR is not configured"}
    dat = _resolve_dat(zone_rel)
    src = output_path_for(dat)          # the standard DAT the publish just wrote
    if not src.exists():
        return {"ok": False, "error": f"standard DAT not found: {src}"}
    dst = hd_path_for(dat)
    if not dst.exists():
        return {"ok": False, "error": "no HD asset-pack DAT exists for this zone"}
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Snapshot the HD asset-pack's PRISTINE bytes as <dat>.base BEFORE overwriting it with
    # the standard DAT — exactly like hd_editable_dat() does on a real HD publish. Without
    # this, clone-to-HD leaves the HD zone with no pristine baseline, so a later HD reset /
    # reset-from-pristine publish has nothing clean to restore: baked content (collision,
    # meshes) accumulates and can never be removed. This is the missing ".base on every HD
    # write" that the normal/HD-publish paths already guarantee.
    base = dst.with_name(dst.name + ".base")
    if not base.exists():
        shutil.copy2(dst, base)
    shutil.copy2(src, dst)
    return {"ok": True, "src": str(src), "dst": str(dst), "bytes": dst.stat().st_size}


def _state(params: dict) -> dict:
    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel:
        return {"hasChanges": False, "hasEdited": False, "changes": None, "workspace": None}
    d = _workspace_dir(zone_rel)
    changes_path = d / "zone-changes.json"
    edited = next(iter(sorted(d.glob("*.edited"))), None)
    changes = None
    if changes_path.exists():
        try:
            changes = json.loads(changes_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            changes = None
    return {
        "hasChanges": changes_path.exists(),
        "hasEdited": edited is not None,
        "changes": changes,
        "workspace": str(d),
    }


def _versions(params: dict) -> dict:
    """List a zone's published version snapshots, newest first.

    Returns ``{versions: [{version, ts, counts, file}], current}`` where ``current``
    is the latest version number recorded in settings.json (0 if none)."""
    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel:
        return {"versions": [], "current": 0}
    vdir = _versions_dir(zone_rel)
    out = []
    if vdir.is_dir():
        for f in vdir.glob("v*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            out.append({
                "version": d.get("version"),
                "ts": d.get("ts"),
                "counts": d.get("counts") or {},
                "file": f.name,
                "hasLog": bool(d.get("log")),
            })
    out.sort(key=lambda v: (v.get("version") or 0), reverse=True)
    current = _read_settings().get("versionCounters", {}).get(_zone_key(zone_rel), 0)
    return {"versions": out, "current": current}


def _version_get(params: dict) -> dict:
    """Return one version snapshot's full change-set (for Restore) and its publish log."""
    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel:
        raise ValueError("missing 'zone'")
    n = int(params.get("version"))
    f = _versions_dir(zone_rel) / f"v{n:04d}.json"
    if not f.is_file():
        raise ValueError(f"version {n} not found")
    d = json.loads(f.read_text(encoding="utf-8"))
    return {"version": d.get("version"), "ts": d.get("ts"),
            "counts": d.get("counts") or {}, "changes": d.get("changes") or {},
            "log": d.get("log") or ""}


def _version_save_log(params: dict) -> dict:
    """Attach a publish console log to an existing version snapshot (``vNNNN.json``).

    Called by the editor right after a Publish (which created the snapshot) so the exact
    log the user saw can be re-viewed later from Version History. Returns ``{version}``."""
    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel:
        raise ValueError("missing 'zone'")
    n = int(params.get("version"))
    f = _versions_dir(zone_rel) / f"v{n:04d}.json"
    if not f.is_file():
        raise ValueError(f"version {n} not found")
    d = json.loads(f.read_text(encoding="utf-8"))
    d["log"] = params.get("log") or ""
    f.write_text(json.dumps(d, indent=2), encoding="utf-8")
    return {"version": n}


def _versions_clear(params: dict) -> dict:
    """Delete all of a zone's published version snapshots and reset its counter.

    Removes the ``versions/vNNNN.json`` files and drops the zone's entry from
    ``versionCounters`` so the next Publish starts again at v1. The live scene and
    the saved change-set (``zone-changes.json``) are untouched — only the Publish
    history is removed. Returns ``{removed}`` (number of snapshot files deleted)."""
    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel:
        raise ValueError("missing 'zone'")
    vdir = _versions_dir(zone_rel)
    removed = 0
    if vdir.is_dir():
        for f in vdir.glob("v*.json"):
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    # Reset the per-zone counter so numbering restarts at v1 on the next Publish.
    _drop_version_counter(_zone_key(zone_rel))
    return {"removed": removed}


def _audio_decode_sfx(params: dict) -> dict:
    """Decode a placed sound's ``.spw`` to a WAV (base64) for in-browser playback.

    Zone sound emitters carry a sound id (xim SoundEffectSection): the client maps
    it to ``<root>/win/se/se{id//1000:03d}/se{id:06d}.spw``. We locate that file
    under the pristine FFXI_DIR sound roots and decode it — native ADPCM/PCM
    (byte-exact) or vgmstream for ATRAC3 — returning the WAV bytes base64-encoded
    plus a little header info for the UI. Read-only: ``.spw`` files are game assets
    the editor never edits, so no lock needed."""
    from xi.xi_config import FFXI_DIR
    from xi.audio.xi_core import locate_sound, decode_file, find_vgmstream, AudioError
    sid = params.get("soundId")
    if sid is None:
        raise ValueError("missing 'soundId'")
    sound_id = int(sid)
    found = locate_sound(Path(FFXI_DIR), sound_id)
    if not found:
        raise ValueError(f"sound se{sound_id:06d}.spw not found under the game's sound roots")
    src, root = found
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / f"se{sound_id:06d}.wav"
        try:
            header = decode_file(src, dest, loops=False, vgmstream=find_vgmstream())
        except AudioError as e:
            raise ValueError(str(e))   # surface "needs vgmstream" / "encrypted" to the editor
        wav = dest.read_bytes()
    return {
        "ok": True,
        "soundId": sound_id,
        "root": root,
        "format": header.format_name,
        "sampleRate": header.sample_rate,
        "channels": header.channels,
        "duration": round(header.duration_sec, 3),
        "wavBase64": base64.b64encode(wav).decode("ascii"),
    }


def _audio_decode_bgm(params: dict) -> dict:
    """Decode a zone BGM track (``music{id:03d}.bgw``) to WAV (base64) for playback.

    The music tree twin of :func:`_audio_decode_sfx`: ``musicId`` is a
    ``zone_settings`` music value; the client plays it from
    ``<root>/win/music/data/music{id:03d}.bgw``. Native ADPCM/PCM decode byte-exact;
    ATRAC3 (≈a third of the soundtrack) routes through vgmstream when one is found.
    Read-only game asset — no lock needed."""
    from xi.xi_config import FFXI_DIR
    from xi.audio.xi_core import locate_music, decode_file, find_vgmstream, AudioError
    from xi.audio.xi_names import music_name
    mid = params.get("musicId")
    if mid is None:
        raise ValueError("missing 'musicId'")
    music_id = int(mid)
    if music_id <= 0:
        raise ValueError("this slot has no music (id 0)")
    found = locate_music(Path(FFXI_DIR), music_id)
    if not found:
        raise ValueError(f"music{music_id:03d}.bgw not found under the game's sound roots")
    src, root = found
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / f"music{music_id:03d}.wav"
        try:
            header = decode_file(src, dest, loops=False, vgmstream=find_vgmstream())
        except AudioError as e:
            raise ValueError(str(e))   # surface "needs vgmstream" / "encrypted" to the editor
        wav = dest.read_bytes()
    return {
        "ok": True,
        "musicId": music_id,
        "title": music_name(music_id) or f"Music #{music_id}",
        "root": root,
        "format": header.format_name,
        "sampleRate": header.sample_rate,
        "channels": header.channels,
        "duration": round(header.duration_sec, 3),
        "wavBase64": base64.b64encode(wav).decode("ascii"),
    }


def _audio_music_catalog(params: dict) -> dict:
    """List every music ``.bgw`` under the game's sound roots, with header info.

    Header-only reads (cheap) yield format / channels / rate / duration / loop. The
    ``musicXXX`` *file number* (zero-padded on disk) is the playable id that
    :func:`_audio_decode_bgm` expects and is what ``zone_settings`` stores, so rows
    are keyed by it (not the in-file header id, which occasionally differs) and the
    title comes from pol-utils ``MusicInfo``. ATRAC3 tracks are listed but only
    ``playable`` when a vgmstream binary is present, and carry no reliable duration
    without a full decode. Read-only — no lock."""
    import re as _re
    from xi.xi_config import FFXI_DIR
    from xi.audio import xi_core as core
    from xi.audio.xi_names import music_name
    from xi.audio.xi_encode import load_custom_music
    custom = load_custom_music(Path(FFXI_DIR))   # {str(id): {title, file}} for imported music
    have_vgm = core.find_vgmstream() is not None
    rows = []
    seen = set()
    for e in core.list_entries(core.MUSIC, Path(FFXI_DIR)):
        m = _re.match(r"music0*(\d+)$", e.stem, _re.IGNORECASE)
        num = int(m.group(1)) if m else None
        key = num if num is not None else e.stem
        if key in seen:            # same number across sound roots → first (priority) root wins
            continue
        seen.add(key)
        try:
            h = core.parse_header_file(e.path)
        except (core.AudioError, OSError):
            h = None
        decodable = h is not None and h.sample_format in (core.FMT_ADPCM, core.FMT_PCM)
        is_atrac = h is not None and h.sample_format == core.FMT_ATRAC3
        title = (music_name(num) or (custom.get(str(num)) or {}).get("title")
                 or f"Music #{num}") if num is not None else e.stem
        rows.append({
            "id": num,
            "file": e.stem,
            "root": e.root,
            "title": title,
            "format": h.format_name if h else None,
            "channels": h.channels if h else None,
            "sampleRate": h.sample_rate if h else None,
            "duration": round(h.duration_sec, 3) if decodable else None,
            "looped": bool(h.looped) if h else None,
            "playable": bool(num is not None and (decodable or (is_atrac and have_vgm))),
        })
    rows.sort(key=lambda r: (r["id"] is None, r["id"] or 0, r["file"]))
    return {"ok": True, "count": len(rows), "vgmstream": have_vgm, "rows": rows}


def _audio_sfx_catalog(params: dict) -> dict:
    """List every sound-effect ``.spw`` grouped by its ``seNNN`` folder.

    The folder is the game's own category system (Spell Sounds, Combat Sounds,
    Skillchain Sounds, Monster SFX, Footstep Effects, …, from pol-utils SFXInfo).
    No header reads — the SFX tree is huge, so this stays fast — rows carry only the
    soundId (the file number ``audio.decodeSfx`` expects: ``se{id//1000:03d}/
    se{id:06d}.spw``) and a title where pol-utils named one. Read-only — no lock."""
    import re as _re
    from xi.xi_config import FFXI_DIR
    from xi.audio import xi_core as core
    from xi.audio.xi_names import sfx_name, folder_category
    from xi.audio.xi_encode import load_custom_sounds, _CUSTOM_ID_BASE
    custom = load_custom_sounds(Path(FFXI_DIR))   # {str(id): {title, file}} for imported sounds
    custom_folder = _CUSTOM_ID_BASE // 1000       # e.g. 990 — imports live here and up
    groups: dict = {}
    seen = set()
    for e in core.list_entries(core.SFX, Path(FFXI_DIR)):
        m = _re.match(r"se0*(\d+)$", e.stem, _re.IGNORECASE)
        if not m:
            continue
        sid = int(m.group(1))
        if sid in seen:                      # same id across sound roots → first (priority) wins
            continue
        seen.add(sid)
        folder = f"{sid // 1000:03d}"
        g = groups.get(folder)
        if g is None:
            label = "Imported (custom)" if (sid // 1000) >= custom_folder else (folder_category(folder) or f"se{folder}")
            g = groups[folder] = {"key": f"se{folder}", "label": label, "sounds": []}
        title = sfx_name(sid) or (custom.get(str(sid)) or {}).get("title")
        g["sounds"].append({"id": sid, "file": e.stem, "title": title})
    group_list = sorted(groups.values(), key=lambda g: g["key"])
    for g in group_list:
        g["sounds"].sort(key=lambda s: s["id"])
        g["count"] = len(g["sounds"])
    return {"ok": True, "count": sum(g["count"] for g in group_list),
            "groupCount": len(group_list), "groups": group_list}


def _audio_import_sound(params: dict) -> dict:
    """Convert+install an uploaded audio file into the game's sound tree → return its
    soundId so the editor can add it to the SFX catalog and place it in a zone.

    The frontend sends the file's bytes (base64) + name; we write a temp file, run it
    through the encoder/installer (next free custom id, friendly title from the name),
    and the new ``se{id}.spw`` lands where the client loads it. Returns ``{ok:False,
    error}`` rather than raising so the import button can show a friendly message."""
    import base64
    from xi.xi_config import FFXI_DIR
    from xi.audio.xi_encode import install_sound
    b64 = params.get("dataBase64")
    if not b64:
        return {"ok": False, "error": "no audio data received"}
    name = params.get("filename") or "sound.wav"
    fmt = params.get("format") or "adpcm"
    loop = bool(params.get("loop"))
    title = (params.get("title") or Path(name).stem).strip() or Path(name).stem
    sid_in = params.get("soundId")
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return {"ok": False, "error": "audio data was not valid base64"}
    ext = Path(name).suffix or ".wav"
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / ("input" + ext)
        tmp.write_bytes(raw)
        try:
            info = install_sound(tmp, Path(FFXI_DIR),
                                 sound_id=int(sid_in) if sid_in else None,
                                 fmt=fmt, loop=loop, title=title)
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {
        "ok": True, "soundId": info["sound_id"], "title": info["title"],
        "file": f"se{info['sound_id'] // 1000:03d}/se{info['sound_id']:06d}.spw",
        "installed": info["installed"], "format": info["format"],
        "rate": info["rate"], "duration": info["duration_sec"], "bytes": info["bytes"],
    }


def _audio_import_music(params: dict) -> dict:
    """Convert+install an uploaded audio file into the game's music tree → return its
    music id so the editor can add it to the Music catalog. Music twin of
    :func:`_audio_import_sound`: writes a ``.bgw`` (BGMStream, stereo, looped) under the
    next free custom id. Returns ``{ok:False, error}`` on failure for a friendly UI."""
    import base64
    from xi.xi_config import FFXI_DIR
    from xi.audio.xi_encode import install_music_file
    b64 = params.get("dataBase64")
    if not b64:
        return {"ok": False, "error": "no audio data received"}
    name = params.get("filename") or "music.wav"
    fmt = params.get("format") or "adpcm"
    loop = params.get("loop")
    loop = True if loop is None else bool(loop)   # music loops by default
    title = (params.get("title") or Path(name).stem).strip() or Path(name).stem
    mid_in = params.get("musicId")
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return {"ok": False, "error": "audio data was not valid base64"}
    ext = Path(name).suffix or ".wav"
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / ("input" + ext)
        tmp.write_bytes(raw)
        try:
            info = install_music_file(tmp, Path(FFXI_DIR),
                                      music_id=int(mid_in) if mid_in else None,
                                      fmt=fmt, loop=loop, title=title)
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {
        "ok": True, "musicId": info["music_id"], "title": info["title"],
        "file": f"music{info['music_id']:03d}.bgw", "installed": info["installed"],
        "format": info["format"], "rate": info["rate"], "channels": info["channels"],
        "duration": info["duration_sec"], "bytes": info["bytes"],
    }


def _zone_bgm(params: dict) -> dict:
    """Report a zone's background music (from the server DB) for the ZONE panel.

    BGM is server-side: ``zone_settings`` carries four music ids per zone —
    ``music_day``, ``music_night``, ``battlesolo``, ``battlemulti`` (0 = silent).
    We resolve the zone id (frontend hint → workspace meta → static table), read the
    row, map each id to a title (pol-utils ``MusicInfo``) and check the ``.bgw``
    exists on disk so the UI can grey out unplayable slots.

    Returns ``{ok: True, zoneId, zoneName, slots: [{key,label,id,title,playable}]}``
    or ``{ok: False, error}`` — never raises (DB offline / no row is the normal case
    when no server is running), so the panel degrades to a friendly line and the rest
    of the editor keeps working."""
    from xi.xi_config import FFXI_DIR
    from xi.audio.xi_core import locate_music
    from xi.audio.xi_names import music_name
    zone_rel = _zone_rel(params.get("zone", ""))
    zid = params.get("zoneId")
    zname = None
    try:
        zid = int(zid) if zid is not None else None
    except (TypeError, ValueError):
        zid = None
    if not zid or zid <= 0:
        zid, zname = _resolve_zone_id(zone_rel)
    if not zid:
        return {"ok": False, "error": "Could not resolve this zone's id."}
    zid = int(zid)
    try:
        conn = _db_connect(params)
    except Exception as e:
        return {"ok": False, "zoneId": zid, "error": f"Database unavailable: {e}"}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, music_day, music_night, battlesolo, battlemulti"
                " FROM zone_settings WHERE zoneid = %s", (zid,))
            row = cur.fetchone()
    except Exception as e:
        return {"ok": False, "zoneId": zid, "error": f"Query failed: {e}"}
    finally:
        conn.close()
    if not row:
        return {"ok": False, "zoneId": zid,
                "error": f"No zone_settings row for zone {zid} (custom/unseeded zone?)."}
    name, day, night, bsolo, bmulti = row
    base = Path(FFXI_DIR)
    specs = [("day", "Day", day), ("night", "Night", night),
             ("battlesolo", "Battle (solo)", bsolo),
             ("battlemulti", "Battle (party)", bmulti)]
    slots = []
    for key, label, raw in specs:
        try:
            mido = int(raw or 0)
        except (TypeError, ValueError):
            mido = 0
        if mido <= 0:
            slots.append({"key": key, "label": label, "id": 0,
                          "title": "None", "playable": False})
            continue
        slots.append({"key": key, "label": label, "id": mido,
                      "title": music_name(mido) or f"Music #{mido}",
                      "playable": locate_music(base, mido) is not None})
    return {"ok": True, "zoneId": zid, "zoneName": zname or name, "slots": slots}


_ZONE_MUSIC_COLS = {"day": "music_day", "night": "music_night",
                    "battlesolo": "battlesolo", "battlemulti": "battlemulti"}


def _zone_set_bgm(params: dict) -> dict:
    """Write a zone's music assignment to ``zone_settings`` (the live server DB).

    ``updates`` maps slot keys (``day``/``night``/``battlesolo``/``battlemulti``) to
    music ids (0 = silent). Columns come from a fixed whitelist, so the values are the
    only thing parameterised — no injection surface. Returns ``{ok:False, error}`` on
    any failure so the UI can surface it (DB offline, no row, etc.)."""
    zone_rel = _zone_rel(params.get("zone", ""))
    zid = params.get("zoneId")
    try:
        zid = int(zid) if zid is not None else None
    except (TypeError, ValueError):
        zid = None
    if not zid or zid <= 0:
        zid = _resolve_zone_id(zone_rel)[0]
    if not zid:
        return {"ok": False, "error": "Could not resolve this zone's id."}
    zid = int(zid)
    updates = params.get("updates") or {}
    sets = {}
    for k, v in updates.items():
        col = _ZONE_MUSIC_COLS.get(k)
        if not col:
            continue
        try:
            sets[col] = max(0, int(v))
        except (TypeError, ValueError):
            continue
    if not sets:
        return {"ok": False, "error": "no valid music slots to update"}
    try:
        conn = _db_connect(params)
    except Exception as e:
        return {"ok": False, "zoneId": zid, "error": f"Database unavailable: {e}"}
    try:
        set_clause = ", ".join(f"`{c}` = %s" for c in sets)
        vals = list(sets.values()) + [zid]
        with conn.cursor() as cur:
            cur.execute(f"UPDATE `zone_settings` SET {set_clause} WHERE `zoneid` = %s", vals)
            affected = cur.rowcount
    except Exception as e:
        return {"ok": False, "zoneId": zid, "error": f"Update failed: {e}"}
    finally:
        conn.close()
    if not affected:
        return {"ok": False, "zoneId": zid,
                "error": f"No zone_settings row for zone {zid} (nothing updated)."}
    return {"ok": True, "zoneId": zid, "updated": list(updates.keys()), "affected": affected}


def _save_changes(params: dict) -> dict:
    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel:
        raise ValueError("missing 'zone'")
    d = _workspace_dir(zone_rel)
    changes = params.get("changes") or {}
    path = d / "zone-changes.json"
    path.write_text(json.dumps(changes, indent=2), encoding="utf-8")
    _touch_active_project()
    plc = changes.get("placements", [])
    vfx = changes.get("vfx", [])
    markers = changes.get("markers", [])
    return {"ok": True, "path": str(path),
            "counts": {"placements": len(plc), "vfx": len(vfx), "markers": len(markers)}}


def _pick_glb(params: dict) -> dict:
    """Open a NATIVE OS file dialog on the editor host and return the chosen GLB's
    absolute path plus its bytes.

    Browsers can't expose a real filesystem path (``<input type=file>`` gives only a
    spoofed ``C:\\fakepath\\name``; the File System Access API gives an opaque handle),
    so the frontend calls this when the bridge is online to obtain the true source path.
    The frontend saves that path to editor.json (local machine only) for "Refresh GLB
    from disk" — the shared zone-changes.json only carries the bare filename, pointing
    at the workspace copy that ``putAsset`` already stored.

    Returns ``{ok, path, name, bytesBase64}`` on success, ``{ok: False, cancelled}``
    if the user dismisses the dialog, or ``{ok: False, error}`` if no GUI/dialog is
    available (headless/remote) — the frontend then falls back to the browser picker."""
    import subprocess

    path = ""
    try:
        if os.name == "nt":
            # PowerShell WinForms OpenFileDialog in STA mode (own process, so it sidesteps
            # Tk's main-thread requirement — the bridge handler is on a worker thread).
            # FOREGROUNDING is the hard part: a process spawned in the background hits
            # Windows' foreground LOCK (focus-stealing prevention), so a plain
            # SetForegroundWindow is ignored — especially when several terminals are open and
            # foreground ownership is ambiguous (the dialog then opens behind). The reliable
            # fix is AttachThreadInput: temporarily join the current foreground thread's input
            # queue so our SetForegroundWindow is honoured, then detach. A shown 1x1 off-screen
            # topmost owner form is the modal's parent. If the P/Invoke compile fails (no C#
            # compiler), we fall back to show/activate, which works in a clean single terminal.
            ps = """
Add-Type -AssemblyName System.Windows.Forms | Out-Null
$fgOk = $false
try {
  Add-Type -Namespace Win -Name Fg -ErrorAction Stop -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow(); [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, IntPtr pid); [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool c); [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();'
  $fgOk = $true
} catch { }
$o = New-Object System.Windows.Forms.Form
$o.TopMost = $true; $o.ShowInTaskbar = $false; $o.StartPosition = 'Manual'
$o.Left = -3000; $o.Top = -3000; $o.Width = 1; $o.Height = 1
$o.Show(); $o.Activate(); $o.BringToFront()
if ($fgOk) {
  try {
    $fg = [Win.Fg]::GetWindowThreadProcessId([Win.Fg]::GetForegroundWindow(), [IntPtr]::Zero)
    $cur = [Win.Fg]::GetCurrentThreadId()
    [Win.Fg]::AttachThreadInput($fg, $cur, $true) | Out-Null
    [Win.Fg]::SetForegroundWindow($o.Handle) | Out-Null
    [Win.Fg]::AttachThreadInput($fg, $cur, $false) | Out-Null
  } catch { }
}
$f = New-Object System.Windows.Forms.OpenFileDialog
$f.Title = 'Select GLB to import'
$f.Filter = 'glTF (*.glb;*.gltf)|*.glb;*.gltf|All files (*.*)|*.*'
$f.Multiselect = $false
$r = $f.ShowDialog($o)
$o.Close()
if ($r -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($f.FileName) }
"""
            cp = subprocess.run(
                ["powershell", "-STA", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True, text=True, timeout=600)
            path = (cp.stdout or "").strip()
        else:
            import tkinter
            import tkinter.filedialog as _fd
            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            root.lift()
            root.focus_force()
            path = _fd.askopenfilename(
                title="Select GLB to import",
                filetypes=[("glTF binary", "*.glb"), ("glTF", "*.gltf"), ("All files", "*.*")]) or ""
            root.destroy()
    except Exception as exc:  # no display / no Tk / dialog failure -> frontend falls back
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    if not path:
        return {"ok": False, "cancelled": True}
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"file not found: {path}"}
    return {"ok": True, "path": str(p.resolve()), "name": p.name,
            "bytesBase64": base64.b64encode(p.read_bytes()).decode("ascii")}


def _app_version() -> str:
    """The xi version for the Projects launcher.

    Prefer pyproject.toml — it's the live source of truth in a dev/editable
    checkout. importlib.metadata reflects the *installed* version, which goes
    stale between release bumps (the editable install isn't refreshed each bump).
    Fall back to installed metadata for packaged builds without pyproject.toml."""
    try:
        import tomllib
        root = Path(__file__).resolve().parents[3]      # src/xi/zone/xi_bridge.py -> repo root
        pp = root / "pyproject.toml"
        if pp.exists():
            with pp.open("rb") as fh:
                v = tomllib.load(fh).get("project", {}).get("version")
            if v:
                return str(v)
    except Exception:
        pass
    try:
        from importlib.metadata import version, PackageNotFoundError
        try:
            return version("xi")
        except PackageNotFoundError:
            pass
    except Exception:
        pass
    return "0.0.0"


def _pick_folder(params: dict) -> dict:
    """Open a NATIVE OS folder dialog on the editor host → ``{ok, path}``.

    Mirrors ``_pick_glb``'s foreground-attach trickery but with a folder picker.
    ``{ok: False, cancelled}`` if dismissed, ``{ok: False, error}`` if no GUI is
    available — the frontend then leaves the typed path alone."""
    import subprocess

    path = ""
    try:
        if os.name == "nt":
            ps = """
Add-Type -AssemblyName System.Windows.Forms | Out-Null
$fgOk = $false
try {
  Add-Type -Namespace Win -Name Fg -ErrorAction Stop -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow(); [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, IntPtr pid); [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool c); [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();'
  $fgOk = $true
} catch { }
$o = New-Object System.Windows.Forms.Form
$o.TopMost = $true; $o.ShowInTaskbar = $false; $o.StartPosition = 'Manual'
$o.Left = -3000; $o.Top = -3000; $o.Width = 1; $o.Height = 1
$o.Show(); $o.Activate(); $o.BringToFront()
if ($fgOk) {
  try {
    $fg = [Win.Fg]::GetWindowThreadProcessId([Win.Fg]::GetForegroundWindow(), [IntPtr]::Zero)
    $cur = [Win.Fg]::GetCurrentThreadId()
    [Win.Fg]::AttachThreadInput($fg, $cur, $true) | Out-Null
    [Win.Fg]::SetForegroundWindow($o.Handle) | Out-Null
    [Win.Fg]::AttachThreadInput($fg, $cur, $false) | Out-Null
  } catch { }
}
$f = New-Object System.Windows.Forms.FolderBrowserDialog
$f.Description = 'Select the folder for your xi-tools workspaces'
$f.ShowNewFolderButton = $true
$r = $f.ShowDialog($o)
$o.Close()
if ($r -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($f.SelectedPath) }
"""
            cp = subprocess.run(
                ["powershell", "-STA", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True, text=True, timeout=600)
            path = (cp.stdout or "").strip()
        else:
            import tkinter
            import tkinter.filedialog as _fd
            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            root.lift()
            root.focus_force()
            path = _fd.askdirectory(title="Select the folder for your xi-tools workspaces") or ""
            root.destroy()
    except Exception as exc:  # no display / no Tk / dialog failure
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    if not path:
        return {"ok": False, "cancelled": True}
    return {"ok": True, "path": str(Path(path))}


def _workspace_skip() -> dict:
    """Create a local ``workspaces/`` folder at the xi-tools root (no git).
    Returns ``{ok: True, path}`` on success."""
    try:
        path = Path(XI_TOOLS_DIR) / "workspaces"
        path.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "path": str(path)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _workspace_setup(params: dict) -> dict:
    """Adopt (or create) a local workspaces folder at ``path``.

    No git requirement — any directory is fine. Creates the folder if missing and
    seeds an empty ``projects.json`` when absent. Returns
    ``{ok: True, path, adopted}`` or ``{ok: False, error}``."""
    raw = (params.get("path") or "").strip()
    if not raw:
        return {"ok": False, "error": "No folder was provided."}
    dest = Path(raw).expanduser()
    try:
        dest.mkdir(parents=True, exist_ok=True)
        if not dest.is_dir():
            return {"ok": False, "error": f"Not a folder: {dest}"}
        # Seed projects index if this is a brand-new folder.
        projects = dest / "projects.json"
        if not projects.exists():
            projects.write_text(
                json.dumps({"projects": []}, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"Created workspaces folder at {dest}")
        else:
            print(f"Using workspaces folder at {dest}")
        return {"ok": True, "path": str(dest.resolve()), "adopted": True}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def _workspace_status(params: dict) -> dict:
    """Is the configured workspace path still a usable folder on disk?

    The editor calls this on boot — if the user deleted the folder, ``exists`` /
    ``isRepo`` are False and the frontend falls back to first-run setup.
    ``isRepo`` is kept for older frontends and now means "folder exists" (git is
    optional)."""
    raw = (params.get("path") or "").strip()
    if not raw:
        return {"exists": False, "isRepo": False, "path": ""}
    p = Path(raw).expanduser()
    exists = p.is_dir()
    return {"exists": exists, "isRepo": exists, "path": str(p)}


# ── First-run .env setup (FFXI_DIR etc.) ──────────────────────────────────────

# Keys the zone-editor setup form cares about (subset of .env.app).
_ENV_SETUP_KEYS = (
    "FFXI_DIR",
    "FFXI_HD_DIR",
    "FFXI_PIVOT_DIR",
    "BLENDER_PATH",
)


def _tools_root() -> Path:
    """xi-tools install root (parent of ``src/``)."""
    return Path(__file__).resolve().parents[3]


def _env_file_path() -> Path:
    explicit = (os.environ.get("XI_ENV_FILE") or os.environ.get("CEXI_ENV_FILE") or "").strip()
    if explicit:
        return Path(explicit)
    return _tools_root() / ".env"


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def _write_env_file(path: Path, updates: dict[str, str]) -> None:
    """Merge ``updates`` into ``path``, preserving unknown keys and comments."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines: list[str] = []
    if path.is_file():
        try:
            existing_lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            existing_lines = []

    seen: set[str] = set()
    out_lines: list[str] = []
    for raw in existing_lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out_lines.append(raw)
            continue
        key = stripped.split("=", 1)[0].strip().removeprefix("export ").strip()
        if key in updates:
            val = updates[key]
            if val:
                out_lines.append(f"{key}={val}")
            # blank → drop the line (unset)
            seen.add(key)
        else:
            out_lines.append(raw)

    for key in _ENV_SETUP_KEYS:
        if key in seen:
            continue
        val = (updates.get(key) or "").strip()
        if val:
            out_lines.append(f"{key}={val}")

    text = "\n".join(out_lines).rstrip() + "\n"
    path.write_text(text, encoding="utf-8")


def _env_status(params: dict) -> dict:
    """Return current path settings and whether FFXI_DIR is ready."""
    from xi import xi_config as cfg

    file_vals = _parse_env_file(_env_file_path())

    def _get(key: str) -> str:
        # Live process wins, then .env file.
        return (os.environ.get(key) or file_vals.get(key) or getattr(cfg, key, "") or "").strip()

    ffxi = _get("FFXI_DIR")
    ffxi_ok = bool(ffxi) and Path(ffxi).is_dir()
    # Zone decrypt needs FFXiMain.dll under the install (or POL tree).
    dll_ok = False
    if ffxi_ok:
        p = Path(ffxi)
        for cand in (
            p / "FFXiMain.dll",
            p.parent / "FFXiMain.dll",
            p / ".." / "FFXiMain.dll",
        ):
            try:
                if cand.resolve().is_file():
                    dll_ok = True
                    break
            except OSError:
                pass

    return {
        "ok": True,
        "envFile": str(_env_file_path()),
        "needsSetup": not ffxi_ok,
        "ffxiOk": ffxi_ok,
        "dllOk": dll_ok,
        "values": {
            "FFXI_DIR": ffxi,
            "FFXI_HD_DIR": _get("FFXI_HD_DIR"),
            "FFXI_PIVOT_DIR": _get("FFXI_PIVOT_DIR"),
            "BLENDER_PATH": _get("BLENDER_PATH"),
        },
    }


def _env_detect_blender(params: dict) -> dict:
    """Best-effort Blender executable discovery (Windows Program Files)."""
    import glob as _glob
    cands: list[str] = []
    if sys.platform == "win32":
        patterns = [
            r"C:\Program Files\Blender Foundation\Blender *\blender.exe",
            r"C:\Program Files (x86)\Blender Foundation\Blender *\blender.exe",
        ]
        for pat in patterns:
            cands.extend(sorted(_glob.glob(pat), reverse=True))
    which = shutil.which("blender") or shutil.which("blender.exe")
    if which:
        cands.insert(0, which)
    # de-dupe preserve order
    seen: set[str] = set()
    uniq = []
    for c in cands:
        k = c.lower()
        if k not in seen and Path(c).is_file():
            seen.add(k)
            uniq.append(c)
    return {"ok": True, "path": uniq[0] if uniq else "", "candidates": uniq}


def _env_pick_path(params: dict) -> dict:
    """Native folder (default) or file picker for env fields."""
    kind = (params.get("kind") or "folder").strip().lower()
    title = (params.get("title") or "Select path").strip()
    initial = (params.get("initial") or "").strip()
    if kind == "file":
        # reuse pick folder machinery with a file dialog
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            kw = {"title": title}
            if initial:
                p = Path(initial)
                if p.is_file():
                    kw["initialdir"] = str(p.parent)
                    kw["initialfile"] = p.name
                elif p.is_dir():
                    kw["initialdir"] = str(p)
            path = filedialog.askopenfilename(**kw) or ""
            root.destroy()
            if not path:
                return {"ok": False, "cancelled": True}
            return {"ok": True, "path": path}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    return _pick_folder({"title": title, "initial": initial})


def _env_save(params: dict) -> dict:
    """Write setup keys to ``.env`` and hot-reload ``xi_config`` in this process."""
    from xi.xi_config import apply_env_overrides

    raw = params.get("values") or {}
    if not isinstance(raw, dict):
        return {"ok": False, "error": "values must be an object"}
    updates = {k: str(raw.get(k) or "").strip() for k in _ENV_SETUP_KEYS}
    ffxi = updates.get("FFXI_DIR") or ""
    if not ffxi:
        return {"ok": False, "error": "FFXI_DIR is required — choose your FINAL FANTASY XI folder."}
    if not Path(ffxi).is_dir():
        return {"ok": False, "error": f"FFXI_DIR is not a folder:\n{ffxi}"}

    env_path = _env_file_path()
    try:
        _write_env_file(env_path, updates)
    except OSError as e:
        return {"ok": False, "error": f"Could not write {env_path}: {e}"}

    # Hot-reload so this bridge process uses the new paths immediately.
    apply_env_overrides(updates)
    os.environ["XI_ENV_FILE"] = str(env_path)

    st = _env_status({})
    st["ok"] = True
    st["saved"] = True
    st["envFile"] = str(env_path)
    return st


def _set_active_project(params: dict) -> dict:
    """Point the workspace at a project folder (``root``), or reset to the legacy
    default when ``root`` is blank. All subsequent workspace ops follow. Returns the
    resolved root so the editor can confirm."""
    global _ACTIVE_WS_ROOT
    raw = (params.get("root") or "").strip()
    _ACTIVE_WS_ROOT = Path(raw).expanduser() if raw else None
    return {"ok": True, "root": str(workspace_root())}


# ── Editor settings (local, per-user view-state) ─────────────────────────────
def _editor_settings_path() -> Path:
    """Per-user editor view-state (locks, categorySets, …) — local, NOT shared.
    Lives at the editor root, separate from the project's content change-sets."""
    return _editor_dir() / "editor.json"


def _editor_load_settings(params: dict) -> dict:
    p = _editor_settings_path()
    if not p.exists():
        return {"ok": True, "settings": {}}
    try:
        return {"ok": True, "settings": json.loads(p.read_text(encoding="utf-8"))}
    except (ValueError, OSError) as exc:
        return {"ok": False, "error": str(exc), "settings": {}}


def _editor_save_settings(params: dict) -> dict:
    data = params.get("data")
    if not isinstance(data, dict):
        return {"ok": False, "error": "data must be an object"}
    try:
        _editor_settings_path().write_text(json.dumps(data, indent=2), encoding="utf-8")
        return {"ok": True}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


# ── Project settings (per-project, saved in the workspace project folder) ─────
def _project_settings_path() -> Path:
    """Per-project settings (HD publish mode, publish-reset flags, a few viewport
    prefs). Lives in the active project's workspace root, so it travels with the
    project — distinct from the per-user editor.json view-state. With no active
    project this resolves under the legacy editor-local workspaces dir (harmless)."""
    return workspace_root() / "project_settings.json"


def _project_load_settings(params: dict) -> dict:
    p = _project_settings_path()
    if not p.exists():
        return {"ok": True, "settings": {}}
    try:
        return {"ok": True, "settings": json.loads(p.read_text(encoding="utf-8"))}
    except (ValueError, OSError) as exc:
        return {"ok": False, "error": str(exc), "settings": {}}


def _project_save_settings(params: dict) -> dict:
    data = params.get("data")
    if not isinstance(data, dict):
        return {"ok": False, "error": "data must be an object"}
    try:
        p = _project_settings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return {"ok": True}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


# ── Projects (shared, committed to the workspaces repo) ───────────────────────
def _read_projects(ws_path: Path) -> dict:
    f = ws_path / "projects.json"
    if f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("projects"), list):
                return d
        except (ValueError, OSError):
            pass
    return {"projects": []}


_projects_json_lock = threading.Lock()   # guards projects.json read-modify-write (concurrent saves)


def _touch_active_project() -> None:
    """Bump the active project's ``lastUpdated`` in projects.json (best-effort, no commit).
    No-op when no project is active (legacy/browse) or projects.json is absent. The active
    workspace root is ``<repo>/<project_id>``, so the repo + id are derived from it."""
    root = _ACTIVE_WS_ROOT
    if root is None:
        return
    f = root.parent / "projects.json"
    if not f.exists():
        return
    pid = root.name
    try:
        from datetime import datetime, timezone
        with _projects_json_lock:
            data = json.loads(f.read_text(encoding="utf-8"))
            projs = data.get("projects")
            if not isinstance(projs, list):
                return
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for p in projs:
                if p.get("id") == pid:
                    p["lastUpdated"] = now
                    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    return
    except (ValueError, OSError):
        pass


def _project_list(params: dict) -> dict:
    raw = (params.get("path") or "").strip()
    if not raw:
        return {"ok": False, "error": "No workspace path", "projects": []}
    projects = _read_projects(Path(raw).expanduser()).get("projects", [])
    projects.sort(key=lambda p: p.get("created") or "", reverse=True)   # newest first
    return {"ok": True, "projects": projects}


def _project_create(params: dict) -> dict:
    """Create a project: a random-id folder under the workspaces repo + a row in
    projects.json, committed so collaborators get it. ``{ok, project}`` / ``{ok:False, error}``."""
    import secrets
    import subprocess
    from datetime import datetime, timezone

    raw = (params.get("path") or "").strip()
    name = (params.get("name") or "").strip()
    if not raw:
        return {"ok": False, "error": "No workspace path configured."}
    if not name:
        return {"ok": False, "error": "Project name is required."}
    ws = Path(raw).expanduser()
    if not ws.is_dir():
        return {"ok": False, "error": f"Workspace folder not found: {ws}"}

    description = (params.get("description") or "").strip()[:260]

    def _csv(v):
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return [x.strip() for x in str(v or "").split(",") if x.strip()]
    authors = _csv(params.get("authors"))
    tags = _csv(params.get("tags"))

    project_id = secrets.token_hex(8)          # 16 hex chars
    (ws / project_id).mkdir(parents=True, exist_ok=True)

    data = _read_projects(ws)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    project = {
        "id": project_id,
        "name": name,
        "description": description,
        "authors": authors,
        "tags": tags,
        "created": now,
        "lastUpdated": now,
    }
    data["projects"].append(project)
    (ws / "projects.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    # Best-effort commit so the team gets the new project (non-fatal if git/identity missing).
    try:
        subprocess.run(["git", "-C", str(ws), "add", "projects.json"], capture_output=True, text=True)
        subprocess.run(["git", "-C", str(ws), "commit", "-m", f"Add project: {name}"],
                       capture_output=True, text=True)
    except Exception:
        pass
    return {"ok": True, "project": project}


def _project_update(params: dict) -> dict:
    """Edit a project's metadata (name/description/authors/tags) in projects.json,
    committed. ``{ok, project}`` / ``{ok:False, error}``."""
    import subprocess
    from datetime import datetime, timezone

    raw = (params.get("path") or "").strip()
    pid = (params.get("id") or "").strip()
    name = (params.get("name") or "").strip()
    if not raw or not pid:
        return {"ok": False, "error": "Missing workspace path or project id."}
    if not name:
        return {"ok": False, "error": "Project name is required."}
    ws = Path(raw).expanduser()
    if not ws.is_dir():
        return {"ok": False, "error": f"Workspace folder not found: {ws}"}

    def _csv(v):
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return [x.strip() for x in str(v or "").split(",") if x.strip()]

    with _projects_json_lock:
        data = _read_projects(ws)
        proj = next((p for p in data["projects"] if p.get("id") == pid), None)
        if proj is None:
            return {"ok": False, "error": "Project not found."}
        proj["name"] = name
        proj["description"] = (params.get("description") or "").strip()[:260]
        proj["authors"] = _csv(params.get("authors"))
        proj["tags"] = _csv(params.get("tags"))
        proj["lastUpdated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        (ws / "projects.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        result = dict(proj)

    try:
        subprocess.run(["git", "-C", str(ws), "add", "projects.json"], capture_output=True, text=True)
        subprocess.run(["git", "-C", str(ws), "commit", "-m", f"Update project: {name}"],
                       capture_output=True, text=True)
    except Exception:
        pass
    return {"ok": True, "project": result}


def _project_delete(params: dict) -> dict:
    """Delete a project: remove its folder + projects.json entry, committed. The
    project stays recoverable from git history. ``{ok, name}`` / ``{ok:False, error}``."""
    import subprocess
    import shutil

    raw = (params.get("path") or "").strip()
    pid = (params.get("id") or "").strip()
    if not raw or not pid:
        return {"ok": False, "error": "Missing workspace path or project id."}
    if not re.fullmatch(r"[A-Za-z0-9_-]+", pid):
        return {"ok": False, "error": "Invalid project id."}
    ws = Path(raw).expanduser()
    if not ws.is_dir():
        return {"ok": False, "error": f"Workspace folder not found: {ws}"}

    data = _read_projects(ws)
    proj = next((p for p in data["projects"] if p.get("id") == pid), None)
    name = (proj or {}).get("name") or pid
    data["projects"] = [p for p in data["projects"] if p.get("id") != pid]
    (ws / "projects.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    # Remove the project folder — git rm stages it (history retained); fs delete as fallback.
    folder = ws / pid
    try:
        cp = subprocess.run(["git", "-C", str(ws), "rm", "-r", "--quiet", pid],
                            capture_output=True, text=True)
        if cp.returncode != 0 and folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
    except Exception:
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)

    # Commit so the team sees the removal (git history keeps the project recoverable).
    try:
        subprocess.run(["git", "-C", str(ws), "add", "projects.json", pid], capture_output=True, text=True)
        subprocess.run(["git", "-C", str(ws), "commit", "-m", f"Remove project: {name}"],
                       capture_output=True, text=True)
    except Exception:
        pass
    return {"ok": True, "name": name}


def _project_zones(params: dict) -> dict:
    """Zones edited in the active project — every subfolder holding a zone-changes.json.
    Returns ``{zones: [{key, zone, counts, total}]}`` (uses the active workspace root)."""
    _prune_orphan_counters()   # self-heal stale counters (removeZone leftovers, etc.)
    root = workspace_root()
    out = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            cf = d / "zone-changes.json"
            if not (d.is_dir() and cf.exists()):
                continue
            try:
                data = json.loads(cf.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            counts = {k: len(data.get(k) or []) for k in
                      ("placements", "vfx", "collisions", "sounds", "mobs", "markers", "textPlanes")}
            out.append({"key": d.name, "zone": data.get("zone") or "",
                        "counts": counts, "total": sum(counts.values())})
    return {"ok": True, "zones": out}


def _project_remove_zone(params: dict) -> dict:
    """Remove a zone's workspace folder from the active project (deletes zone-changes.json
    and any uploaded GLB assets / version snapshots for that zone)."""
    import shutil
    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel:
        raise ValueError("missing 'zone'")
    d = _workspace_dir(zone_rel, create=False)
    if d.is_dir():
        shutil.rmtree(d)
    # Drop the zone's version counter too — otherwise it lingers in settings.json and a
    # later folder re-create (zone reopen) leaves an orphaned counter with no changes.
    _drop_version_counter(_zone_key(zone_rel))
    return {"ok": True, "zone": zone_rel}


def _open_folder(path: Path) -> bool:
    """Open ``path`` itself in the OS file browser (vs ``_reveal_in_explorer`` which
    opens the parent with the target selected). Best-effort, non-fatal."""
    import subprocess
    import sys
    try:
        if sys.platform.startswith("win"):
            import os
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except OSError:
        return False


def _project_open_folder(params: dict) -> dict:
    """Open a project's folder (``<workspace>/<id>``) in the OS file browser."""
    raw = (params.get("path") or "").strip()
    pid = (params.get("id") or "").strip()
    if not raw or not pid:
        return {"ok": False, "error": "Missing workspace path or project id."}
    d = Path(raw).expanduser() / pid
    if not d.is_dir():
        return {"ok": False, "error": f"Project folder not found: {d}"}
    return {"ok": _open_folder(d), "path": str(d)}


def _zone_companion_dats(params: dict) -> dict:
    """Return the ROM-relative paths of the event/dialog/npc companion DATs for a zone."""
    from xi.ftable.xi_core import load_all_tables, scan_file_ids
    from xi.zone.xi_inject import zone_event_file_id, zone_dialog_file_id, zone_npc_file_id
    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel:
        raise ValueError("missing 'zone'")
    zone_entry = params.get("zoneEntry") or {}
    zid = zone_entry.get("id")
    if zid is None:
        return {"ok": True, "event": None, "dialog": None, "npc": None}
    tables = load_all_tables()
    result = {}
    for key, fn in (("event", zone_event_file_id), ("dialog", zone_dialog_file_id), ("npc", zone_npc_file_id)):
        hits = scan_file_ids([fn(zid)], tables)
        result[key] = hits[0]["dat"] if hits else None
    return {"ok": True, **result}


def _get_asset(params: dict) -> dict:
    """Return a GLB's bytes (base64) so the editor can re-display a glb-add on zone
    load / edit-mode switch WITHOUT prompting the user to re-pick the file. Looks in the
    zone workspace first (where import + publish persist GLBs), then at the change-set's
    stored absolute source path. Returns ``{ok: False}`` if neither exists, and the editor
    falls back to its manual picker."""
    zone_rel = _zone_rel(params.get("zone", ""))
    name = Path(params.get("name", "")).name
    candidates = []
    if zone_rel and name:
        candidates.append(_workspace_dir(zone_rel, create=False) / name)
    glb = (params.get("glb") or "").strip()
    if glb:
        candidates.append(Path(glb))
    for p in candidates:
        try:
            if p.is_file():
                return {"ok": True, "name": p.name,
                        "bytesBase64": base64.b64encode(p.read_bytes()).decode("ascii")}
        except OSError:
            pass
    return {"ok": False, "error": "asset not found in workspace or source path"}


def _put_asset(params: dict) -> dict:
    """Store an uploaded asset (e.g. a GLB referenced by a glb-add change) in the
    workspace so :func:`_export` can resolve it on disk."""
    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel:
        raise ValueError("missing 'zone'")
    name = Path(params.get("name", "")).name  # strip any path components
    if not name:
        raise ValueError("missing 'name'")
    raw = base64.b64decode(params.get("bytesBase64", ""))
    d = _workspace_dir(zone_rel)
    path = d / name
    path.write_bytes(raw)
    return {"ok": True, "path": str(path), "bytes": len(raw)}


def _export(params: dict) -> dict:
    """Apply the change-set to the live output DAT, then mirror the result into the
    workspace as ``<dat>.edited``.  Writes to FFXI_HD_DIR when the zone URL uses the
    ``game-hd/`` prefix, otherwise in place to the game DAT."""
    from xi.zone.xi_apply_changes import apply_changes_data, _check_cancel, PublishCancelled

    cancel = params.get("_cancel")   # editor "Stop publish" Event (None from the CLI)
    zone_url = params.get("zone", "")
    zone_rel = _zone_rel(zone_url)
    use_hd = _is_hd(zone_url)
    if not zone_rel:
        raise ValueError("missing 'zone'")
    dat = _resolve_dat(zone_rel)
    d = _workspace_dir(zone_rel)
    changes = json.loads(json.dumps(params.get("changes") or {}))  # deep copy

    # Resolve glb-add references. The persisted JSON keeps a bare relative filename in
    # 'glb' (resolved relative to the workspace dir on load) and records the resolved
    # workspace path in 'glb_source' for reference.  The apply step needs absolute paths,
    # so build a separate deep copy with the resolved paths before writing the tidy JSON.
    apply_changes = json.loads(json.dumps(changes))
    for ch_save, ch_apply in zip(changes.get("placements", []),
                                 apply_changes.get("placements", [])):
        if ch_apply.get("op") == "add" and ch_apply.get("glb"):
            p = Path(ch_apply["glb"])
            if not (p.is_absolute() and p.exists()):
                p = d / p.name
            ch_apply["glb"] = str(p)          # absolute path for apply step
            ch_save["glb_source"] = str(p)    # workspace abs path for reference
            ch_save["glb"] = p.name           # bare filename for persistence

    # Persist the change-set alongside, so the workspace stays the source of truth.
    (d / "zone-changes.json").write_text(json.dumps(changes, indent=2), encoding="utf-8")
    _touch_active_project()

    if params.get("reset"):
        if use_hd:
            # HD reset: re-seed the HD copy from the pristine FFXI_DIR source.
            from xi.xi_config import hd_editable_dat
            hd_editable_dat(dat, fresh=True)
        else:
            from xi.zone.xi_reset import reset_dat
            reset_dat(dat)

    # "Reset Collision on Publish": strip the zone's own collision (after any reset) so the
    # change-set's baked collision becomes the ONLY collision (replace, not append). Independent
    # of the DAT reset, and runs before apply so the new collision lands on the emptied grid.
    if params.get("clearCollision"):
        from xi.zone.xi_collision import clear_zone_collision
        if use_hd:
            from xi.xi_config import hd_path_for
            clear_zone_collision(hd_path_for(dat))
        else:
            clear_zone_collision(dat)

    # Building-interior (sub-area) edits target a SEPARATE DAT. Split them out of the main-zone
    # apply so they never match a same-named object in this zone, and group them by their interior
    # DAT for a per-DAT pass below. (The full change-set — interiors included — was already persisted
    # to zone-changes.json above, so reload still restores them.)
    interior_groups: dict[str, list] = {}
    main_plc = []
    for ch in apply_changes.get("placements", []):
        sub_dat_rel = ch.get("subAreaDat")
        if sub_dat_rel:
            interior_groups.setdefault(sub_dat_rel, []).append(ch)
        else:
            main_plc.append(ch)
    apply_changes["placements"] = main_plc

    # Reset has just (re)written the live DAT from pristine — a cancel from here on leaves it
    # reverted/partial, which is the "bad state" the editor warns about. Check before the
    # (longer) apply so an early Stop is honoured.
    _check_cancel(cancel)
    results = apply_changes_data(dat, apply_changes, debug=bool(params.get("debug")), use_hd=use_hd,
                                 cancel=cancel)

    # Apply each touched interior DAT in its own pass (modify-only this iteration; adds/deletes to
    # interiors are guarded editor-side). Counts roll into the top-line placement totals + a
    # per-interior breakdown for the publish console.
    if interior_groups:
        sub_results = []
        for sub_dat_rel, sub_changes in interior_groups.items():
            entry = {"dat": sub_dat_rel, "modified": 0, "skipped": len(sub_changes)}
            try:
                sub_dat = _resolve_dat(_zone_rel(sub_dat_rel))
                _check_cancel(cancel)
                sub_r = apply_changes_data(
                    sub_dat,
                    {"zone": _zone_rel(sub_dat_rel), "placements": sub_changes},
                    debug=bool(params.get("debug")), use_hd=use_hd, cancel=cancel)
                sp = sub_r.get("placements") or {}
                entry.update(modified=sp.get("modified", 0), skipped=sp.get("skipped", 0),
                             output=sub_r.get("output"))
            except PublishCancelled:
                raise
            except Exception as exc:   # one bad interior must not abort the whole publish
                entry["error"] = str(exc)
            sub_results.append(entry)
        results["subAreas"] = sub_results
        pl = results.setdefault("placements", {"modified": 0, "added": 0, "deleted": 0, "skipped": 0})
        pl["modified"] = pl.get("modified", 0) + sum(e.get("modified", 0) for e in sub_results)

    out = Path(results.get("output") or output_path_for(dat))

    footsteps = changes.get("footsteps") if isinstance(changes.get("footsteps"), dict) else None
    footstep_source = (footsteps or {}).get("sourceZone") or ""
    if footstep_source:
        from xi.zone.xi_footsteps import copy_footstep_sound_pointers
        donor_rel = _zone_rel(footstep_source)
        if not donor_rel:
            raise ValueError("invalid footstep source zone")
        donor = _resolve_dat(donor_rel)
        _fp_out, copied, skipped, removed = copy_footstep_sound_pointers(
            dat, donor, target_path=out, replace=bool((footsteps or {}).get("replace")))
        results["footsteps"] = {
            "sourceZone": donor_rel,
            "copied": copied,
            "skipped": skipped,
            "removed": removed,
        }

    # HD shares the std workspace (same zone_rel); keep its mirror under a distinct name
    # so a combined std+HD publish doesn't have the two legs clobber each other's .edited.
    edited = d / (dat.name + (".hd.edited" if use_hd else ".edited"))
    try:
        if out.exists():
            shutil.copy2(out, edited)
            results["edited"] = str(edited)
    except OSError:
        pass

    # Version history: snapshot this published change-set into versions/ and bump the
    # per-zone counter in settings.json. Non-fatal — a publish must not fail over history.
    # HD publishes (skipVersion) reuse the standard change-set, so the standard leg owns
    # the version trail — recording the HD leg too would just double every version.
    if not params.get("skipVersion"):
        try:
            results["version"] = _save_version(zone_rel, changes)
        except OSError:
            pass
    return results


def _reveal_in_explorer(path: Path) -> bool:
    """Open the OS file browser with ``path`` selected. Best-effort, non-fatal.

    Windows Explorer (and the macOS Finder) return a non-zero exit code even on
    success, so we fire-and-forget with Popen and never inspect the result."""
    import subprocess
    import sys
    p = str(Path(path))
    try:
        if sys.platform.startswith("win"):
            import os
            os.startfile(str(Path(path).parent))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", p])
        else:
            subprocess.Popen(["xdg-open", str(Path(path).parent)])
        return True
    except OSError:
        return False


def _client_relpath(p: Path, client_root: Path, fallback: str) -> str:
    """Path of ``p`` relative to the catseyexi-client root, as a forward-slash
    string. Falls back to ``fallback`` when ``p`` lives outside the client tree
    (e.g. FFXI_HD_DIR pointed somewhere exotic)."""
    try:
        return p.resolve().relative_to(client_root).as_posix()
    except ValueError:
        return fallback


def _package(params: dict) -> dict:
    """``zone.package`` — zip this zone's edited game + HD DATs into a deployable
    package whose internal layout mirrors the catseyexi-client install exactly
    (``Game/FINAL FANTASY XI/ROM/...`` and ``Ashita/polplugins/DATs/ffxi-hd/ROM/...``),
    written to ``workspaces/packages/<ZoneName>_<version>.zip``.

    Also writes a one-time pristine ``<ZoneName>_backup.zip`` (same layout, from the
    ``.base`` backups) the first time the zone is packaged, so the vanilla state is
    always recoverable. Reveals the freshly-built zip in the OS file browser.

    Returns ``{ok, zip, backup, backupCreated, opened, zoneName, version, members}``.
    """
    import zipfile
    from xi.xi_config import FFXI_DIR, FFXI_HD_DIR, output_path_for, hd_path_for

    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel:
        raise ValueError("missing 'zone'")
    dat = _resolve_dat(zone_rel)                       # pristine FFXI_DIR path
    ffxi_dir = Path(FFXI_DIR).resolve()
    rel = dat.resolve().relative_to(ffxi_dir)          # e.g. ROM/1/41.DAT
    # catseyexi-client root = parent of "<root>/Game/FINAL FANTASY XI".
    client_root = ffxi_dir.parent.parent

    # Human zone name for the filename (static table → workspace meta → key).
    name = _zone_name_for_dat(dat)
    if not name:
        _zid, name = _resolve_zone_id(zone_rel)
    safe = re.sub(r"[^A-Za-z0-9]+", "_", name or "").strip("_") or _zone_key(zone_rel)

    # Version = the zone's current published counter (0 if never published).
    version = int(_read_settings().get("versionCounters", {}).get(_zone_key(zone_rel), 0))

    # ── Resolve the live edited files and their client-relative archive paths ──
    # Normal DAT: edited bytes live at output_path_for (== dat in in-place mode),
    # but the archive path is always the Game-relative path the client expects.
    game_arc = _client_relpath(dat, client_root, (Path("Game/FINAL FANTASY XI") / rel).as_posix())
    norm_src = Path(output_path_for(dat))
    if not norm_src.exists():
        norm_src = dat                                 # no edit mirror yet → pristine
    members = [(norm_src, game_arc)]

    # HD variant: only when the HD pack actually ships a DAT for this zone.
    hd_src = None
    hd_arc = None
    if FFXI_HD_DIR:
        try:
            hp = hd_path_for(dat)
            if hp.exists():
                hd_src = hp
                # HD archive path is the HD pack folder name + ROM-relative path,
                # e.g. ``ffxi-hd/ROM/1/41.DAT`` (no Ashita/polplugins/DATs prefix).
                hd_arc = (Path(Path(FFXI_HD_DIR).name) / rel).as_posix()
                members.append((hp, hd_arc))
        except (ValueError, OSError):
            pass

    # NavMesh (server-side mob pathing): bundle the zone's .nav at NavMesh/<ZoneName>.nav.
    # Resolved the same way the editor's overlay finds it; skipped if none exists.
    from xi.xi_config import XI_NAVMESH_DIR
    nav_arc_name = (name or "").replace(" ", "_") or safe
    nav_src = _editor_dir() / "assets" / f"{dat.stem}.nav"
    if not nav_src.exists() and XI_NAVMESH_DIR:
        cand = Path(XI_NAVMESH_DIR) / f"{nav_arc_name}.nav"
        if cand.exists():
            nav_src = cand
    if not nav_src.exists():
        from xi.zone.xi_export import default_output_dir
        nav_src = default_output_dir(dat) / f"{dat.stem}.nav"
    if nav_src.exists():
        members.append((nav_src, f"NavMesh/{nav_arc_name}.nav"))

    pkg_dir = workspace_root() / "packages"
    pkg_dir.mkdir(parents=True, exist_ok=True)

    zip_path = pkg_dir / f"{safe}_{version}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for src, arc in members:
            zf.write(src, arc)

    # ── One-time pristine backup zip (same layout, vanilla bytes from .base) ──
    backup_path = pkg_dir / f"{safe}_backup.zip"
    backup_created = False
    if not backup_path.exists():
        base_norm = dat.with_name(dat.name + ".base")
        backup_members = [(base_norm if base_norm.exists() else dat, game_arc)]
        if hd_src is not None:
            hd_base = hd_src.with_name(hd_src.name + ".base")
            backup_members.append((hd_base if hd_base.exists() else hd_src, hd_arc))
        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for src, arc in backup_members:
                zf.write(src, arc)
        backup_created = True

    opened = _reveal_in_explorer(zip_path)
    return {
        "ok": True,
        "zip": str(zip_path),
        "backup": str(backup_path),
        "backupCreated": backup_created,
        "opened": opened,
        "zoneName": name or safe,
        "version": version,
        "members": [arc for _src, arc in members],
    }


def _package_project(params: dict) -> dict:
    """``zone.packageProject`` — zip the edited game + HD DATs for multiple zones into a
    single deployable package named after the project.

    ``{zones: ['ROM/1/41.DAT', ...], projectName: 'Byakkos Hideout'}``
    → ``workspaces/packages/byakkos hideout.zip``
    """
    import zipfile
    from xi.xi_config import FFXI_DIR, FFXI_HD_DIR, output_path_for, hd_path_for
    from xi.xi_config import XI_NAVMESH_DIR

    zone_urls = params.get("zones") or []
    project_name = (params.get("projectName") or "").strip()
    if not zone_urls:
        raise ValueError("missing 'zones'")
    if not project_name:
        raise ValueError("missing 'projectName'")

    ffxi_dir = Path(FFXI_DIR).resolve()
    client_root = ffxi_dir.parent.parent
    slug = re.sub(r"[^a-z0-9 ]+", "", project_name.lower()).strip() or "package"

    members: list = []       # list[tuple[Path, str]]
    arc_seen: set = set()
    zone_names: list = []
    backup_members: list = []   # vanilla .base bytes, same archive layout as `members`

    for zone_url in zone_urls:
        zone_rel = _zone_rel(zone_url)
        if not zone_rel:
            continue
        try:
            dat = _resolve_dat(zone_rel)
        except Exception:
            continue

        try:
            rel = dat.resolve().relative_to(ffxi_dir)
        except ValueError:
            continue
        game_arc = _client_relpath(dat, client_root, (Path("Game/FINAL FANTASY XI") / rel).as_posix())
        norm_src = Path(output_path_for(dat))
        if not norm_src.exists():
            norm_src = dat

        name = _zone_name_for_dat(dat)
        if not name:
            _zid, name = _resolve_zone_id(zone_rel)
        zone_names.append(name or _zone_key(zone_rel))

        if game_arc not in arc_seen:
            members.append((norm_src, game_arc))
            arc_seen.add(game_arc)
            base_norm = dat.with_name(dat.name + ".base")
            backup_members.append((base_norm if base_norm.exists() else dat, game_arc))

        if FFXI_HD_DIR:
            try:
                hp = hd_path_for(dat)
                if hp.exists():
                    hd_arc = (Path(Path(FFXI_HD_DIR).name) / rel).as_posix()
                    if hd_arc not in arc_seen:
                        members.append((hp, hd_arc))
                        arc_seen.add(hd_arc)
                        hd_base = hp.with_name(hp.name + ".base")
                        backup_members.append((hd_base if hd_base.exists() else hp, hd_arc))
            except (ValueError, OSError):
                pass

        nav_arc_name = (name or "").replace(" ", "_") or re.sub(r"[^A-Za-z0-9]+", "_", zone_rel).strip("_")
        nav_src = _editor_dir() / "assets" / f"{dat.stem}.nav"
        if not nav_src.exists() and XI_NAVMESH_DIR:
            cand = Path(XI_NAVMESH_DIR) / f"{nav_arc_name}.nav"
            if cand.exists():
                nav_src = cand
        if not nav_src.exists():
            from xi.zone.xi_export import default_output_dir
            nav_src = default_output_dir(dat) / f"{dat.stem}.nav"
        if nav_src.exists():
            nav_arc = f"NavMesh/{nav_arc_name}.nav"
            if nav_arc not in arc_seen:
                members.append((nav_src, nav_arc))
                arc_seen.add(nav_arc)

    # Optionally bundle the custom-NPC npc_list SQL (regenerated from the registry) so the
    # deployed package carries the rows that spawn each custom NPC. The model DATs
    # themselves ship via the normal DAT pipeline (they're already placed/injected).
    custom_npc_sql = False
    if params.get("includeCustomNpcs"):
        sql_path = _custom_npc_write_sql()
        if sql_path and sql_path.exists():
            arc = "sql/custom_npcs.sql"
            if arc not in arc_seen:
                members.append((sql_path, arc))
                arc_seen.add(arc)
                custom_npc_sql = True

    if not members:
        raise ValueError("No packagable files found for the selected zones.")

    pkg_dir = workspace_root() / "packages"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    zip_path = pkg_dir / f"{slug}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for src, arc in members:
            zf.write(src, arc)

    # One-time pristine backup for the whole project (vanilla bytes from .base files,
    # same archive layout as the main zip). Skipped if it already exists.
    backup_path = pkg_dir / f"{slug}_backup.zip"
    backup_created = False
    if not backup_path.exists() and backup_members:
        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for src, arc in backup_members:
                zf.write(src, arc)
        backup_created = True

    opened = _reveal_in_explorer(zip_path)
    return {
        "ok": True,
        "zip": str(zip_path),
        "backup": str(backup_path),
        "backupCreated": backup_created,
        "opened": opened,
        "zoneNames": zone_names,
        "memberCount": len(members),
        "customNpcSql": custom_npc_sql,
    }


def _clear_collision(params: dict) -> dict:
    """``zone.clearCollision`` — strip ALL baked collision from the zone's DAT (its OWN 0x1C
    collision mesh, not just user-placed prims). Writes the DAT in place; the
    pristine bytes live in the .base backup (Reset Zone restores). Returns
    ``{ok, removed, output}``. Works for any zone id — the editor gates the < 400 confirm."""
    from xi.zone.xi_collision import clear_zone_collision
    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel:
        raise ValueError("missing 'zone'")
    out, n = clear_zone_collision(_resolve_dat(zone_rel))
    return {"ok": True, "removed": n, "output": str(out)}


def _reset(params: dict) -> dict:
    """``zone.reset`` — restore the DAT to pristine (the ``xi zone reset`` op) AND wipe
    this zone's workspace of all pending edits, so the editor reloads a truly clean scene.

    Deletes the change-set, every uploaded GLB/glTF asset, and the ``<dat>.edited`` mirror.
    Keeps ``zone-meta.json`` (the zone's identity — name/id — not an edit)."""
    zone_url = params.get("zone", "")
    zone_rel = _zone_rel(zone_url)
    use_hd = _is_hd(zone_url)
    if not zone_rel:
        raise ValueError("missing 'zone'")
    dat = _resolve_dat(zone_rel)

    if use_hd:
        from xi.xi_config import hd_editable_dat, hd_path_for
        hd_editable_dat(dat, fresh=True)
        msg = f"Re-seeded HD copy of {dat.name} from pristine FFXI_DIR"
        if params.get("clearCollision"):
            from xi.zone.xi_collision import clear_zone_collision
            try:
                _out, n = clear_zone_collision(hd_path_for(dat))
                msg += f"; cleared {n} collision mesh(es)"
            except Exception as exc:
                msg += f"; clear-collision failed: {exc}"
    else:
        from xi.zone.xi_reset import reset_dat
        msg = reset_dat(dat)
        if params.get("clearCollision"):
            from xi.zone.xi_collision import clear_zone_collision
            try:
                _out, n = clear_zone_collision(dat)
                msg += f"; cleared {n} collision mesh(es)"
            except Exception as exc:
                msg += f"; clear-collision failed: {exc}"
    removed = []
    wd = _workspace_dir(zone_rel, create=False)
    if wd.exists():
        for f in wd.iterdir():
            if not f.is_file() or f.name == "zone-meta.json":
                continue
            if (f.name == "zone-changes.json"
                    or f.suffix.lower() in (".glb", ".gltf")
                    or f.name.endswith(".edited")):
                try:
                    f.unlink()
                    removed.append(f.name)
                except OSError:
                    pass
    return {"ok": True, "message": msg, "removed": removed}


def _replace_collision(params: dict) -> dict:
    """``zone.replaceCollision`` — parse an OBJ payload and bake it as the zone's entire
    collision (clear-then-add). OBJ must use xi's export frame: vertices as (x,-y,z),
    materials col_wall_<terrain> / col_floor_<terrain>."""
    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel:
        raise ValueError("missing 'zone'")
    dat = _resolve_dat(zone_rel)
    obj_text = params.get("objText", "")
    if not obj_text:
        raise ValueError("missing 'objText'")
    scale = float(params.get("scale", 1.0))

    from xi.zone.xi_collision import parse_collision_obj_text, replace_zone_collision
    tris = parse_collision_obj_text(obj_text, scale=scale)
    if not tris:
        raise ValueError("OBJ contained no usable triangles")
    out_path, n_removed, n_added = replace_zone_collision(dat, tris)
    return {"ok": True, "path": str(out_path), "removed": n_removed, "added": n_added}


def _db_connect(params: dict):
    """Resolve credentials and return an open pymysql connection.

    Uses the same resolution order as the CLI (params → network.lua → defaults).
    Raises ``RuntimeError`` instead of calling ``sys.exit`` so the bridge handler's
    ``except Exception`` can surface the error cleanly to the frontend."""
    try:
        import pymysql
    except ImportError:
        raise RuntimeError("pymysql not installed — run: uv pip install pymysql")
    from xi.server.xi_commands import _resolve
    h, p, u, pw, db = _resolve(
        params.get("host"), params.get("port"), params.get("user"),
        params.get("password"), params.get("database"),
    )
    return pymysql.connect(
        host=h, port=p, user=u, password=pw,
        database=db, charset="utf8mb4", autocommit=True,
    )


def _db_tables(params: dict) -> dict:
    """Return ``{tables: [str]}`` for the configured database (``SHOW TABLES``)."""
    conn = _db_connect(params)
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES")
            return {"tables": [row[0] for row in cur.fetchall()]}
    finally:
        conn.close()


def _db_query(params: dict) -> dict:
    """Return ``{columns, rows, total, offset, limit}`` for a paginated SELECT."""
    table = params.get("table", "")
    if not table or not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', table):
        raise ValueError(f"invalid table name: {table!r}")
    limit  = min(int(params.get("limit", 50)), 500)
    offset = max(int(params.get("offset", 0)), 0)
    conn = _db_connect(params)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM `{table}`")
            total = int(cur.fetchone()[0])
            cur.execute(f"SELECT * FROM `{table}` LIMIT %s OFFSET %s", (limit, offset))
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = [[None if v is None else str(v) for v in row] for row in cur.fetchall()]
            cur.execute(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE"
                " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
                " AND CONSTRAINT_NAME = 'PRIMARY' ORDER BY ORDINAL_POSITION",
                (table,),
            )
            primary_keys = [r[0] for r in cur.fetchall()]
            return {"columns": columns, "rows": rows, "total": total, "offset": offset,
                    "limit": limit, "primary_keys": primary_keys}
    finally:
        conn.close()


def _db_update(params: dict) -> dict:
    """Run ``UPDATE table SET … WHERE pk_col=… LIMIT 1``."""
    table = params.get("table", "")
    if not table or not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', table):
        raise ValueError(f"invalid table name: {table!r}")
    pk      = params.get("pk")      or {}
    updates = params.get("updates") or {}
    if not updates:
        raise ValueError("updates required")
    col_re = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
    for col in list(pk.keys()) + list(updates.keys()):
        if not col_re.match(col):
            raise ValueError(f"invalid column name: {col!r}")
    if not pk:
        raise ValueError("pk required")
    set_clause   = ", ".join(f"`{c}` = %s" for c in updates)
    set_vals     = list(updates.values())
    where_parts  = []
    where_vals   = []
    for col, val in pk.items():
        if val is None:
            where_parts.append(f"`{col}` IS NULL")
        else:
            where_parts.append(f"`{col}` = %s")
            where_vals.append(val)
    where_sql = " AND ".join(where_parts)
    sql = f"UPDATE `{table}` SET {set_clause} WHERE {where_sql} LIMIT 1"

    def _lit(v):  # display only — execution stays parameterized
        if v is None:
            return "NULL"
        if isinstance(v, (int, float)):
            return str(v)
        return "'" + str(v).replace("'", "''") + "'"

    disp_set   = ", ".join(f"`{c}` = {_lit(v)}" for c, v in updates.items())
    disp_where = " AND ".join(
        (f"`{c}` IS NULL" if v is None else f"`{c}` = {_lit(v)}") for c, v in pk.items()
    )
    display_sql = f"UPDATE `{table}` SET {disp_set} WHERE {disp_where} LIMIT 1"

    conn = _db_connect(params)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, set_vals + where_vals)
            affected = cur.rowcount  # CHANGED rows — 0 can mean "row unchanged"
            # Re-check matched rows so callers can tell "not found" from "unchanged".
            cur.execute(f"SELECT COUNT(*) FROM `{table}` WHERE {where_sql}", where_vals)
            matched = int(cur.fetchone()[0])
            return {"affected": affected, "matched": matched, "sql": display_sql}
    finally:
        conn.close()


def _db_exec(params: dict) -> dict:
    """Run arbitrary SQL and return ``{columns, rows, total}`` or ``{affected}``."""
    sql = (params.get("sql") or "").strip()
    if not sql:
        raise ValueError("sql required")
    limit = min(int(params.get("limit", 500)), 2000)
    conn = _db_connect(params)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            if cur.description:
                columns = [d[0] for d in cur.description]
                raw = cur.fetchmany(limit)
                rows = [[None if v is None else str(v) for v in row] for row in raw]
                return {"columns": columns, "rows": rows,
                        "total": len(rows), "truncated": len(rows) >= limit}
            else:
                return {"columns": [], "rows": [], "total": 0,
                        "affected": cur.rowcount}
    finally:
        conn.close()


def _mob_pool_template(cur, poolid: int) -> dict:
    """Group/spawn defaults to copy when spawning this pool: pulled from an existing spawn of the
    same pool so a new mob behaves like its peers; falls back to sane generic values if the pool
    has no spawns yet. (mob_spawn_points has no zoneid — it's derived from the mobid, matching the
    server's own ``mob_groups.zoneid=((mobid>>12)&0xFFF)`` join.)"""
    try:
        cur.execute(
            "SELECT g.respawntime, g.spawntype, g.dropid, g.HP, g.MP, g.allegiance, "
            "       s.minLevel, s.maxLevel "
            "FROM mob_groups g JOIN mob_spawn_points s ON s.groupid=g.groupid "
            "  AND ((s.mobid>>12)&0xFFF)=g.zoneid "
            "WHERE g.poolid=%s LIMIT 1", (poolid,))
        row = cur.fetchone()
    except Exception:
        row = None
    if row:
        return {"respawntime": int(row[0] or 0), "spawntype": int(row[1] or 0),
                "dropid": int(row[2] or 0), "HP": int(row[3] or 0), "MP": int(row[4] or 0),
                "allegiance": int(row[5] or 0), "minLevel": int(row[6] or 1), "maxLevel": int(row[7] or 1)}
    return {"respawntime": 300, "spawntype": 0, "dropid": 0, "HP": 0, "MP": 0,
            "allegiance": 0, "minLevel": 1, "maxLevel": 1}


def _write_mob_spawns(params: dict) -> dict:
    """Write placed mobs to the server DB as real spawns (mob_groups + mob_spawn_points).
    ``{zoneId, mobs:[{poolid,modelid,name,pos,rot,mobid?,groupid?}], <creds>}`` →
    ``{ok, written, skipped, spawns:[{name,mobid,groupid,poolid}], errors}``.

    Idempotent across re-publishes: a mob already carrying mobid/groupid is upserted in place; a
    new one is allocated ``mobid = 0x1000000 + (zoneId<<12) + nextLocal`` and a fresh per-zone
    ``groupid``. A New-Mob entry (no pool, model id only) reuses an existing pool with the same
    model; if none exists it's skipped (creating a pool is the monster-importer's job)."""
    import math
    try:
        zoneid = int(params.get("zoneId"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "missing/invalid zoneId"}
    if zoneid <= 0 or zoneid > 0xFFF:
        return {"ok": False, "error": f"zoneId {zoneid} out of range (1..4095)"}
    mobs = params.get("mobs") or []
    if not mobs:
        return {"ok": True, "written": 0, "skipped": 0, "spawns": [], "errors": []}
    try:
        conn = _db_connect(params)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Database unavailable: {exc}"}
    spawns, errors = [], []
    written = skipped = 0
    try:
        with conn.cursor() as cur:
            base = 0x1000000 + (zoneid << 12)
            cur.execute("SELECT COALESCE(MAX(mobid),0) FROM mob_spawn_points WHERE ((mobid>>12)&0xFFF)=%s", (zoneid,))
            max_mobid = int(cur.fetchone()[0] or 0)
            next_local = (max_mobid - base) + 1 if max_mobid >= base else 1
            cur.execute("SELECT COALESCE(MAX(groupid),0) FROM mob_groups WHERE zoneid=%s", (zoneid,))
            next_group = int(cur.fetchone()[0] or 0) + 1
            for mob in mobs:
                name = (mob.get("name") or "mob")
                poolid = int(mob.get("poolid") or 0)
                modelhex = (mob.get("modelid") or "").strip()
                pos = mob.get("pos") or [0, 0, 0]
                rot = mob.get("rot") or [0, 0, 0]
                # New Mob (no pool) → reuse a pool that already uses this exact model, else skip.
                if poolid <= 0:
                    if not modelhex:
                        skipped += 1; errors.append(f"{name}: no pool and no model id"); continue
                    try:
                        cur.execute("SELECT poolid FROM mob_pools WHERE modelid=UNHEX(%s) LIMIT 1", (modelhex,))
                        prow = cur.fetchone()
                    except Exception:
                        prow = None
                    if not prow:
                        skipped += 1
                        errors.append(f"{name}: no mob_pools entry uses model {modelhex} (needs the monster importer)")
                        continue
                    poolid = int(prow[0])
                tmpl = _mob_pool_template(cur, poolid)
                # Re-use stamped ids for an in-place upsert, else allocate fresh.
                gid = mob.get("groupid")
                groupid = int(gid) if gid else next_group
                if not gid:
                    next_group += 1
                mid = mob.get("mobid")
                if mid:
                    mobid = int(mid)
                elif next_local > 0xFFF:
                    skipped += 1; errors.append(f"{name}: zone {zoneid} is full (4095 mobs)"); continue
                else:
                    mobid = base + next_local; next_local += 1
                mobname = re.sub(r"[^A-Za-z0-9_]", "_", name)[:24] or f"mob_{poolid}"
                polutils = name[:50]
                try:
                    roty = float(rot[1]) if len(rot) > 1 else 0.0
                except (TypeError, ValueError):
                    roty = 0.0
                pos_rot = int(round(roty / (2 * math.pi) * 256)) & 0xFF
                px = float(pos[0] or 0); py = float(pos[1] or 0); pz = float(pos[2] or 0)
                try:
                    cur.execute(
                        "INSERT INTO mob_groups (groupid,poolid,zoneid,name,respawntime,spawntype,dropid,HP,MP,allegiance) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON DUPLICATE KEY UPDATE poolid=VALUES(poolid),name=VALUES(name),respawntime=VALUES(respawntime),"
                        "spawntype=VALUES(spawntype),dropid=VALUES(dropid),HP=VALUES(HP),MP=VALUES(MP),allegiance=VALUES(allegiance)",
                        (groupid, poolid, zoneid, mobname, tmpl["respawntime"], tmpl["spawntype"],
                         tmpl["dropid"], tmpl["HP"], tmpl["MP"], tmpl["allegiance"]))
                    cur.execute(
                        "INSERT INTO mob_spawn_points (mobid,spawnslotid,mobname,polutils_name,groupid,minLevel,maxLevel,pos_x,pos_y,pos_z,pos_rot) "
                        "VALUES (%s,0,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON DUPLICATE KEY UPDATE mobname=VALUES(mobname),polutils_name=VALUES(polutils_name),groupid=VALUES(groupid),"
                        "minLevel=VALUES(minLevel),maxLevel=VALUES(maxLevel),pos_x=VALUES(pos_x),pos_y=VALUES(pos_y),pos_z=VALUES(pos_z),pos_rot=VALUES(pos_rot)",
                        (mobid, mobname, polutils, groupid, tmpl["minLevel"], tmpl["maxLevel"], px, py, pz, pos_rot))
                    written += 1
                    spawns.append({"name": mob.get("name"), "mobid": mobid, "groupid": groupid, "poolid": poolid})
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{name}: {exc}")
                    skipped += 1
        return {"ok": True, "written": written, "skipped": skipped, "spawns": spawns,
                "errors": errors, "zoneId": zoneid}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Custom NPCs — register an already-placed entity model as a zone NPC.
# The registry (custom-npcs.json at the active project workspace root) drives the
# Asset Browser list, the packaged npc_list SQL, and the cutscene actor list. See
# :mod:`xi.entity.xi_custom_npc` for the record shape and SQL generator.
# ---------------------------------------------------------------------------

def _custom_npcs_path() -> Path:
    return workspace_root() / "custom-npcs.json"

def _custom_npc_sql_path() -> Path:
    return workspace_root() / "sql" / "custom_npcs.sql"

def _load_custom_npcs() -> dict:
    from xi.entity import xi_custom_npc as cn
    return cn.load_registry(_custom_npcs_path())


def _custom_npc_rows(ids) -> dict:
    """``{npcid: {name, look(bytes), pos, rot}}`` for registered custom NPCs among ``ids``.
    Lets the cutscene preview resolve a custom NPC's model with no live ``npc_list`` row."""
    want = {int(i) for i in ids if i}
    if not want:
        return {}
    reg = _load_custom_npcs()
    out: dict = {}
    for n in reg.get("npcs", []):
        nid = int(n.get("npcid", 0))
        if nid in want:
            out[nid] = {
                "name": n.get("name") or "",
                "look": bytes.fromhex(n.get("look") or ""),
                "pos": [float(x) for x in (n.get("pos") or [0, 0, 0])],
                "rot": int(n.get("rot") or 0),
            }
    return out


def _custom_npc_db_used_locals(zone_id: int, params: dict) -> set:
    """Zone-local targids already claimed in the live DB for ``zone_id`` — every
    ``npc_list`` NPC plus every ``mob_spawn_points`` mob (mobs share this per-zone targid
    space), restricted to the static band. Empty set if the DB is unreachable.

    Returns the full SET rather than a max: retail occupies the top of the band *sparsely*
    in a handful of zones, so :func:`alloc_local` needs to know which individual slots are
    taken in order to skip them."""
    from xi.entity.xi_custom_npc import NPC_STATIC_TARGID_MAX
    used: set = set()
    try:
        conn = _db_connect(params)
    except Exception:
        return used
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT npcid & 0xFFF FROM npc_list "
                "WHERE ((npcid>>12)&0xFFF)=%s AND (npcid & 0xFFF) <= %s",
                (zone_id, NPC_STATIC_TARGID_MAX))
            used.update(int(r[0]) for r in cur.fetchall())
            try:
                cur.execute(
                    "SELECT mobid & 0xFFF FROM mob_spawn_points "
                    "WHERE ((mobid>>12)&0xFFF)=%s AND (mobid & 0xFFF) <= %s",
                    (zone_id, NPC_STATIC_TARGID_MAX))
                used.update(int(r[0]) for r in cur.fetchall())
            except Exception:
                pass
    except Exception:
        return used
    finally:
        conn.close()
    return used


def _custom_npc_event_used_locals(zone_id: int) -> set:
    """Zone-local targids already claimed by Event DAT actors in ``zone_id``.

    Retail NPCs live in the Event DAT even when the live DB is missing a row (or the
    DB max query under-counts). Without this set, ``alloc_local`` can hand out an id
    that already belongs to e.g. Shami — the cutscene picker then has two <option>s
    with the same value and the select snaps to the zone NPC."""
    from xi.entity.xi_custom_npc import (
        NPC_STATIC_TARGID_MAX, zone_of, local_of,
    )
    from xi.xi_config import FFXI_DIR, read_path_for
    from xi.zone.xi_inject import zone_event_file_id
    from xi.ftable.xi_core import scan_file_ids
    from xi.event.xi_event import parse_raw_actors
    used: set = set()
    try:
        hits = scan_file_ids([zone_event_file_id(int(zone_id))])
        if not hits:
            return used
        path = read_path_for(Path(FFXI_DIR) / hits[0]["dat"])
        # Prefer the pristine .base backup: the live DAT contains xi's OWN compiled
        # actor blocks (cutscene cast involvement markers, custom owners). Counting
        # those as "retail-claimed" made repair bump a custom NPC to a new id on every
        # publish while the saved cutscene kept the old id — cast broke silently.
        from xi.xi_config import output_path_for as _opf
        base = Path(str(_opf(path)) + ".base")
        if base.exists():
            path = base
        if not path.exists():
            return used
        for a in parse_raw_actors(path.read_bytes()):
            aid = int(getattr(a, "actor_id", 0) or 0)
            if zone_of(aid) != int(zone_id):
                continue
            loc = local_of(aid)
            if 0 < loc <= NPC_STATIC_TARGID_MAX:
                used.add(loc)
    except Exception:
        return used
    return used


def _custom_npc_repair_collisions(reg: dict, params: dict | None = None) -> list:
    """Re-allocate any custom NPC whose npcid collides with an Event DAT actor.

    Mutates ``reg`` in place and returns the list of rewritten records (empty when
    nothing collided). Caller is responsible for save + SQL regen."""
    from xi.entity import xi_custom_npc as cn
    params = params or {}
    fixed = []
    # Group by zone so we only parse each Event DAT once.
    by_zone: dict = {}
    for n in reg.get("npcs", []):
        try:
            z = cn.zone_of(int(n.get("npcid", 0)))
        except (TypeError, ValueError):
            continue
        by_zone.setdefault(z, []).append(n)
    for zone_id, rows in by_zone.items():
        event_used = _custom_npc_event_used_locals(zone_id)
        if not event_used:
            continue
        # Locals claimed by OTHER custom NPCs in this zone (after any rewrites below).
        for n in rows:
            try:
                nid = int(n.get("npcid", 0))
            except (TypeError, ValueError):
                continue
            loc = cn.local_of(nid)
            if loc not in event_used:
                continue
            # Collision — pick a free local above DB + event + remaining registry.
            # Temporarily drop this row from the reg view so alloc doesn't keep its old id.
            old_id = nid
            shadow = {
                "npcs": [x for x in reg.get("npcs", [])
                         if int(x.get("npcid", -1)) != old_id],
            }
            db_used = _custom_npc_db_used_locals(zone_id, params)
            try:
                new_local = cn.alloc_local(zone_id, shadow, used=db_used | event_used)
            except ValueError:
                continue
            new_id = cn.make_npcid(zone_id, new_local)
            n["npcid"] = new_id
            n["npcidHex"] = f"0x{new_id:08X}"
            fixed.append(n)
            # Keep subsequent allocs in this zone clear of the new id too.
            event_used.add(new_local)
    return fixed


def _custom_npc_write_sql() -> Path | None:
    """(Re)generate ``sql/custom_npcs.sql`` from the whole registry. Returns the path, or
    ``None`` (and removes a stale file) when the registry is empty."""
    from xi.entity import xi_custom_npc as cn
    reg = _load_custom_npcs()
    p = _custom_npc_sql_path()
    if not reg.get("npcs"):
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass
        return None
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(cn.generate_sql(reg), encoding="utf-8")
    return p


def _custom_npc_live_insert(record: dict, params: dict):
    """Best-effort ``REPLACE`` the record into the live ``npc_list``. Returns
    ``(ok, detail)`` — ``ok=False`` (with a reason) when the DB is unreachable."""
    from xi.entity import xi_custom_npc as cn
    try:
        conn = _db_connect(params)
    except Exception as exc:  # noqa: BLE001
        return False, f"DB unavailable: {exc}"
    try:
        cols = ", ".join(f"`{c}`" for c in cn.NPC_COLUMNS)
        ph = ", ".join(["%s"] * len(cn.NPC_COLUMNS))
        with conn.cursor() as cur:
            cur.execute(f"REPLACE INTO `npc_list` ({cols}) VALUES ({ph})", cn.row_values(record))
        return True, "npc_list row inserted"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    finally:
        conn.close()


def _custom_npc_name_dat(zone_id: int):
    """``(read_path, write_path)`` for a zone's client NPC name DAT (``ROM/27/…``), or
    ``(None, None)`` when the FTABLE has no entry for it."""
    from xi.zone.xi_inject import zone_npc_file_id
    from xi.xi_config import FFXI_DIR, read_path_for, output_path_for
    hits = scan_file_ids([zone_npc_file_id(int(zone_id))])
    if not hits:
        return None, None
    rel = Path(FFXI_DIR) / hits[0]["dat"]
    return read_path_for(rel), output_path_for(rel)


def _custom_npc_sync_name_table(zone_id: int, sid: int, name: str, remove: bool = False) -> bool:
    """Inject (or drop) a custom NPC's record in the zone's client name DAT so the game shows
    its name instead of the "NPC" fallback. Keeps a one-time ``.base`` backup of the pristine
    table, writes the game/output DAT, and mirrors into the Ashita pivot pack when configured.
    Best-effort — never raises to the caller. Returns True when at least one DAT was written."""
    from xi.entity import xi_custom_npc as cn
    from xi.xi_config import FFXI_DIR, FFXI_PIVOT_DIR
    try:
        read_path, write_path = _custom_npc_name_dat(zone_id)
        if not read_path or not read_path.exists():
            return False
        data = read_path.read_bytes()
        base = write_path.with_name(write_path.name + ".base")
        if not base.exists():
            base.parent.mkdir(parents=True, exist_ok=True)
            base.write_bytes(data)
        new = (cn.remove_name_record(data, sid) if remove
               else cn.inject_name_record(data, sid, name))
        wrote = False
        if new != data or not write_path.exists() or write_path.read_bytes() != new:
            write_path.parent.mkdir(parents=True, exist_ok=True)
            write_path.write_bytes(new)
            wrote = True
        # Mirror into the pivot overlay so Ashita clients see the name too (name DATs are
        # otherwise only in the base game tree and the "NPC" fallback sticks).
        try:
            rel = None
            src_res = Path(read_path).resolve()
            base_res = Path(FFXI_DIR).resolve()
            try:
                rel = src_res.relative_to(base_res)
            except ValueError:
                rel = None
            piv_root = Path(FFXI_PIVOT_DIR) if str(FFXI_PIVOT_DIR or "").strip() else None
            if piv_root is not None and rel is not None:
                piv = piv_root / rel
                if remove:
                    if piv.is_file():
                        # Re-read base/game without this sid for the pivot copy.
                        piv.parent.mkdir(parents=True, exist_ok=True)
                        piv.write_bytes(new)
                        wrote = True
                else:
                    piv.parent.mkdir(parents=True, exist_ok=True)
                    if (not piv.exists()) or piv.read_bytes() != new:
                        piv.write_bytes(new)
                        wrote = True
        except OSError:
            pass
        return wrote
    except Exception:  # noqa: BLE001
        return False


def _custom_npc_list(params: dict) -> dict:
    """``customNpc.list`` — registry NPCs, optionally filtered to ``zoneId``.

    Auto-repairs any custom NPC whose id collides with a retail Event DAT actor
    (legacy bug: allocation used to ignore Event DAT locals → cutscene picker
    showed the retail NPC for the same option value)."""
    from xi.entity import xi_custom_npc as cn
    reg = _load_custom_npcs()
    repaired = _custom_npc_repair_collisions(reg, params)
    if repaired:
        cn.save_registry(_custom_npcs_path(), reg)
        _custom_npc_write_sql()
        _touch_active_project()
        # Best-effort: write the new free ids into the live DB (old colliding rows
        # are left alone so we never delete a retail NPC we may have overwritten).
        for rec in repaired:
            try:
                _custom_npc_live_insert(rec, params)
            except Exception:
                pass
    try:
        zone_id = int(params.get("zoneId") or 0)
    except (TypeError, ValueError):
        zone_id = 0
    # Copies — for_zone returns the registry's own dicts and we annotate below;
    # a later save_registry must never persist the transient dbStatus key.
    npcs = [dict(n) for n in cn.for_zone(reg, zone_id)]
    # Best-effort: annotate each record with the LIVE npc_list.status so the UI /
    # debug dump can show drift between the registry and what the server actually
    # runs (the registry is what xi wrote; the DB is what the zone loaded at boot).
    if npcs:
        try:
            conn = _db_connect(params)
            try:
                ids = ",".join(str(int(n["npcid"])) for n in npcs)
                with conn.cursor() as cur:
                    cur.execute(f"SELECT npcid, status FROM npc_list WHERE npcid IN ({ids})")
                    db_status = {int(r[0]): int(r[1]) for r in cur.fetchall()}
            finally:
                conn.close()
            for n in npcs:
                n["dbStatus"] = db_status.get(int(n["npcid"]))   # None = row missing
        except Exception:
            pass                                                 # DB down → no dbStatus keys
    return {
        "ok": True,
        "npcs": npcs,
        "count": len(reg.get("npcs", [])),
        "repaired": [
            {"name": r.get("name"), "npcidHex": r.get("npcidHex")} for r in repaired
        ],
    }


def _custom_npc_create(params: dict) -> dict:
    """``customNpc.create`` — register a placed model id as a zone NPC.

    ``{zoneId, zoneName?, name, modelid, pos?, rot?, status?, <db creds>}`` → validate the
    model is in the FTABLE, allocate a zone-local ``npcid``, persist the record +
    regenerate the project SQL, and best-effort insert the live ``npc_list`` row.
    ``status`` is the ``npc_list.status`` STATUS_TYPE (0 normal / 2 disappear / 3 invisible
    / 6 cutscene-only)."""
    from xi.entity import xi_custom_npc as cn
    name = (params.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "name is required"}
    try:
        modelid = int(params.get("modelid"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "a numeric model id is required"}
    try:
        zone_id = int(params.get("zoneId"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "missing/invalid zoneId"}
    if zone_id <= 0 or zone_id > 0xFFF:
        return {"ok": False, "error": f"zoneId {zone_id} out of range (1..4095)"}
    try:
        file_id, dat_rel = cn.resolve_model(modelid)   # confirm the DAT is placed
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    zone_name = (params.get("zoneName") or "").strip()
    # Custom NPCs default to CUTSCENE_ONLY (6): invisible in the zone, revealed + positioned
    # by the cutscene's own opcodes — matching how retail stages Lion/Iroha. The Asset Browser
    # status dropdown overrides for a permanently-standing NPC.
    status = cn.normalize_status(params.get("status"), cn.NPC_STATUS_CUTSCENE_ONLY)
    reg = _load_custom_npcs()
    db_used = _custom_npc_db_used_locals(zone_id, params)
    event_used = _custom_npc_event_used_locals(zone_id)
    try:
        local = cn.alloc_local(zone_id, reg, used=db_used | event_used)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    npcid = cn.make_npcid(zone_id, local)
    rec = cn.make_record(zone_id, zone_name, name, modelid, npcid, file_id, dat_rel,
                         pos=params.get("pos") or [0, 0, 0], rot=int(params.get("rot") or 0),
                         status=status)
    cn.upsert(reg, rec)
    cn.save_registry(_custom_npcs_path(), reg)
    _touch_active_project()
    sql_path = _custom_npc_write_sql()
    db_ok, db_detail = _custom_npc_live_insert(rec, params)
    # Give the NPC a client-side name (the game reads names from ROM/27/… by serverID;
    # without an entry it shows the "NPC" fallback).
    name_written = _custom_npc_sync_name_table(zone_id, npcid, name)
    return {"ok": True, "npc": rec, "sql": str(sql_path) if sql_path else None,
            "dbWritten": db_ok, "dbDetail": db_detail, "datRel": dat_rel, "fileId": file_id,
            "nameTableWritten": name_written}


def _custom_npc_update(params: dict) -> dict:
    """``customNpc.update`` — patch fields on an existing custom NPC.

    ``{npcid, status?, name?}`` → update the registry record, regenerate SQL, and
    best-effort REPLACE the live ``npc_list`` row. Status changes to CUTSCENE_ONLY
    also force pos (0,0,0) like retail Lion/Iroha. Name changes re-sync the client
    name DAT."""
    from xi.entity import xi_custom_npc as cn
    try:
        npcid = int(params.get("npcid"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "missing/invalid npcid"}
    reg = _load_custom_npcs()
    rec = next((n for n in reg.get("npcs", []) if int(n.get("npcid", -1)) == npcid), None)
    if not rec:
        return {"ok": False, "error": f"custom NPC 0x{npcid:08X} not in registry"}
    name_changed = False
    if "name" in params and str(params.get("name") or "").strip():
        new_name = str(params.get("name")).strip()
        if new_name != rec.get("name"):
            rec["name"] = new_name
            name_changed = True
    if "status" in params:
        rec["status"] = cn.normalize_status(
            params.get("status"), rec.get("status", cn.NPC_STATUS_CUTSCENE_ONLY))
        if rec["status"] == cn.NPC_STATUS_CUTSCENE_ONLY:
            rec["pos"] = [0.0, 0.0, 0.0]
    cn.upsert(reg, rec)
    cn.save_registry(_custom_npcs_path(), reg)
    _touch_active_project()
    sql_path = _custom_npc_write_sql()
    db_ok, db_detail = _custom_npc_live_insert(rec, params)
    name_written = False
    if name_changed or params.get("resyncName"):
        name_written = _custom_npc_sync_name_table(
            cn.zone_of(npcid), npcid, rec.get("name") or "")
    return {"ok": True, "npc": rec, "sql": str(sql_path) if sql_path else None,
            "dbWritten": db_ok, "dbDetail": db_detail, "nameTableWritten": name_written}


def _custom_npc_delete(params: dict) -> dict:
    """``customNpc.delete`` — drop a custom NPC from the registry + SQL (+ best-effort DB)."""
    from xi.entity import xi_custom_npc as cn
    try:
        npcid = int(params.get("npcid"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "missing/invalid npcid"}
    reg = _load_custom_npcs()
    rec = next((n for n in reg.get("npcs", []) if int(n.get("npcid", -1)) == npcid), None)
    removed = cn.remove(reg, npcid)
    cn.save_registry(_custom_npcs_path(), reg)
    _custom_npc_write_sql()
    if rec:
        _custom_npc_sync_name_table(cn.zone_of(npcid), npcid, "", remove=True)
    db_ok = False
    try:
        conn = _db_connect(params)
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM npc_list WHERE npcid=%s", (npcid,))
            db_ok = True
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        db_ok = False
    return {"ok": True, "removed": removed, "dbDeleted": db_ok}


def _zone_templates(params: dict) -> dict:
    """List all templates for the editor's New-Zone dropdown.

    Returns ``{templates: [{id, label, description}, ...]}``."""
    from xi.zone.xi_new import all_templates
    return {"templates": [
        {"id": tid, "label": meta.get("label", tid),
         "description": meta.get("description", "")}
        for tid, meta in all_templates().items()
    ]}


def _zone_make_template(params: dict) -> dict:
    """Editor → package the current zone into a reusable template bundle.

    Params: ``{sourceZone, label, description?, fromPristine?, dryRun?}``
    Source zone must be >= 400 (custom zone)."""
    from xi.zone.xi_make_template import make_template
    if params.get("sourceZone") in (None, ""):
        raise ValueError("sourceZone is required.")
    return make_template(
        source_zone=int(params["sourceZone"]),
        label=params.get("label") or "",
        description=params.get("description") or "",
        from_pristine=bool(params.get("fromPristine")),
        dry_run=bool(params.get("dryRun")),
    )


def _zone_new(params: dict) -> dict:
    """Create a new zone from a template bundle.

    Accepts ``{template: "<6-char-id>", name: "..."}`` — copies all four zone DATs
    from the bundle, registers them in FTABLE10, writes + auto-applies the DB migration.
    Returns ``{zoneId, fileId, datUrl, datPath, name, template, db}``."""
    import shutil as _shutil
    from xi.zone.xi_new import (NEW_SUBDIR, resolve_template, all_templates,
                                  _splice_sky, _zone_migration_sql, _apply_migration,
                                  write_server_zone_scripts, _copy_template_companions)
    from xi.xi_config import DB_AUTOAPPLY, DB_NAME, DB_HOST, FFXI_DIR
    from xi.zone.xi_inject import (
        _ensure_rom10, _next_free_slot, _next_free_zone_id, zone_model_file_id,
        register_zone_file,
    )

    template = (params.get("template") or "").strip()
    if not template:
        raise ValueError("template id is required.")
    tmpl = resolve_template(template)
    if tmpl is None:
        available = ", ".join(all_templates()) or "(none — run `xi zone make-template` first)"
        raise FileNotFoundError(
            f"Unknown template '{template}'. Available: {available}")

    sky_raw = (params.get("sky") or "").strip()
    sky_path: Path | None = None
    if sky_raw:
        from xi.zone.xi_export import resolve_dat_path
        sky_path = resolve_dat_path(sky_raw)

    from xi.ftable.xi_core import load_all_tables
    tables = load_all_tables()
    zone_id = _next_free_zone_id(tables)
    fid = zone_model_file_id(zone_id)

    rom10_dir = Path(FFXI_DIR) / "ROM10"
    slot = _next_free_slot(rom10_dir, NEW_SUBDIR)
    dst = rom10_dir / str(NEW_SUBDIR) / f"{slot}.DAT"

    _ensure_rom10()
    dst.parent.mkdir(parents=True, exist_ok=True)
    _shutil.copy2(tmpl["dat"], dst)

    if sky_path:
        data = bytearray(dst.read_bytes())
        _splice_sky(data, sky_path)
        dst.write_bytes(bytes(data))

    reserved = {slot}
    register_zone_file(fid, NEW_SUBDIR, slot)
    _copy_template_companions(zone_id, tmpl, NEW_SUBDIR, rom10_dir, reserved)

    dat_url = f"game/ROM10/{NEW_SUBDIR}/{slot}.DAT"
    zone_name = (params.get("name") or "").strip() or f"Zone_{zone_id}"
    meta = {"name": zone_name, "zoneId": zone_id, "fileId": fid, "datUrl": dat_url}
    ws = _workspace_dir(f"ROM10/{NEW_SUBDIR}/{slot}.DAT")
    (ws / "zone-meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    db_msg = None
    from xi.zone.xi_new import _template_migration_sql
    mig_sql = _template_migration_sql(tmpl.get("data_sql"), zone_id, zone_name)
    if mig_sql is None:
        source = tmpl.get("source_zone")
        if source:
            mig_sql = _zone_migration_sql(zone_id, zone_name, source)
    if mig_sql:
        (ws / "zone-migration.sql").write_text(mig_sql, encoding="utf-8")
        if DB_AUTOAPPLY:
            try:
                n = _apply_migration(mig_sql)
                db_msg = f"applied {n} statement(s) to {DB_NAME}@{DB_HOST}"
            except Exception as exc:
                db_msg = f"auto-apply skipped: {exc}"

    server_msg = write_server_zone_scripts(zone_id, zone_name)

    return {"zoneId": zone_id, "fileId": fid, "datUrl": dat_url, "datPath": str(dst),
            "name": zone_name, "template": template, "db": db_msg, "server": server_msg}


def _zone_get_settings(params: dict) -> dict:
    """Return zone_settings row for a zone.

    Params: ``{zoneId: N}``
    Returns ``{zoneid, zonetype, misc, music_day, music_night, battlesolo, battlemulti,
               restriction, tax}`` or ``{error}`` if not in DB."""
    zone_id = params.get("zoneId")
    if zone_id is None:
        raise ValueError("zoneId required")
    zone_id = int(zone_id)
    try:
        import pymysql
        from xi.xi_config import db_creds
        conn = pymysql.connect(**db_creds(), charset="utf8mb4",
                               autocommit=True, connect_timeout=4)
    except Exception as e:
        return {"error": f"DB unavailable: {e}"}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT zoneid, zonetype, misc, music_day, music_night, "
                "battlesolo, battlemulti, restriction, tax "
                "FROM zone_settings WHERE zoneid=%s", (zone_id,))
            row = cur.fetchone()
        if not row:
            return {"error": f"zone {zone_id} not in zone_settings"}
        cols = ("zoneid", "zonetype", "misc", "music_day", "music_night",
                "battlesolo", "battlemulti", "restriction", "tax")
        return dict(zip(cols, row))
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def _zone_set_field(params: dict, field: str) -> dict:
    """UPDATE zone_settings SET <field>=value WHERE zoneid=N.

    Params: ``{zoneId: N, value: V}``"""
    zone_id = params.get("zoneId")
    value = params.get("value")
    if zone_id is None or value is None:
        raise ValueError("zoneId and value required")
    zone_id = int(zone_id)
    value = int(value)
    try:
        import pymysql
        from xi.xi_config import db_creds
        conn = pymysql.connect(**db_creds(), charset="utf8mb4",
                               autocommit=True, connect_timeout=4)
    except Exception as e:
        raise RuntimeError(f"DB unavailable: {e}")
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE zone_settings SET `{field}`=%s WHERE zoneid=%s",
                (value, zone_id))
        return {"ok": True, "zoneId": zone_id, field: value}
    except Exception as e:
        raise RuntimeError(str(e))
    finally:
        conn.close()


def _zone_duplicate(params: dict) -> dict:
    """Duplicate the current zone to a new zone ID.

    Copies the model DAT (current output state), then finds and copies the
    source zone's event/dialog/npc companions.  Clones zone_settings +
    zone_weather from the source zone in the dev DB.  Copies the workspace
    (zone-changes.json + GLB assets) so the editor reopens with the same edits.

    Accepts ``{ zone: "game/ROM10/2/0.DAT", sourceZoneId: 400, name: "..." }``
    Returns ``{ zoneId, fileId, datUrl, datPath, name, sourceZoneId, db }``."""
    import shutil as _shutil
    from xi.ftable.xi_core import load_all_tables, scan_file_ids
    from xi.zone.xi_inject import (
        _ensure_rom10, _next_free_slot, _next_free_zone_id, zone_model_file_id,
        zone_event_file_id, zone_dialog_file_id, zone_npc_file_id,
        register_zone_file,
    )
    from xi.zone.xi_new import _zone_migration_sql, _apply_migration, NEW_SUBDIR
    from xi.xi_config import FFXI_DIR, DB_AUTOAPPLY, DB_NAME, DB_HOST

    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel:
        raise ValueError("missing 'zone'")
    source_zone_id = params.get("sourceZoneId")
    if source_zone_id is None:
        raise ValueError("missing 'sourceZoneId'")
    source_zone_id = int(source_zone_id)
    zone_name = (params.get("name") or "").strip() or f"Zone_{source_zone_id}_copy"

    def _find_dat(rel: str) -> Path | None:
        p = Path(FFXI_DIR) / rel
        return p if p.exists() else None

    src_model = _find_dat(zone_rel)
    if src_model is None:
        raise ValueError(f"Source model DAT not found: {zone_rel}")

    tables = load_all_tables()
    new_zone_id = _next_free_zone_id(tables)
    new_fid = zone_model_file_id(new_zone_id)

    rom10_dir = Path(FFXI_DIR) / "ROM10"
    _ensure_rom10()

    reserved: set[int] = set()

    def _alloc() -> int:
        slot = _next_free_slot(rom10_dir, NEW_SUBDIR)
        while slot in reserved:
            slot += 1
        reserved.add(slot)
        return slot

    # Copy model DAT.
    slot_model = _alloc()
    dst_model = rom10_dir / str(NEW_SUBDIR) / f"{slot_model}.DAT"
    dst_model.parent.mkdir(parents=True, exist_ok=True)
    _shutil.copy2(src_model, dst_model)
    register_zone_file(new_fid, NEW_SUBDIR, slot_model)

    # Copy event / dialog / npc companion DATs from the source zone.
    companions = []
    for dtype, src_fn, tgt_fn in [
        ("event",  zone_event_file_id,  zone_event_file_id),
        ("dialog", zone_dialog_file_id, zone_dialog_file_id),
        ("npc",    zone_npc_file_id,    zone_npc_file_id),
    ]:
        src_fid = src_fn(source_zone_id)
        tgt_fid = tgt_fn(new_zone_id)
        hits = scan_file_ids([src_fid], tables)
        if not hits:
            continue
        src_path = _find_dat(hits[0]["dat"])
        if src_path is None:
            continue
        slot = _alloc()
        dst = rom10_dir / str(NEW_SUBDIR) / f"{slot}.DAT"
        dst.parent.mkdir(parents=True, exist_ok=True)
        _shutil.copy2(src_path, dst)
        register_zone_file(tgt_fid, NEW_SUBDIR, slot)
        companions.append(f"{dtype}→ROM10/{NEW_SUBDIR}/{slot}")

    dat_url = f"game/ROM10/{NEW_SUBDIR}/{slot_model}.DAT"
    meta = {
        "name": zone_name, "zoneId": new_zone_id, "fileId": new_fid,
        "datUrl": dat_url, "sourceZoneId": source_zone_id,
    }
    ws_new = _workspace_dir(f"ROM10/{NEW_SUBDIR}/{slot_model}.DAT")
    (ws_new / "zone-meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # Copy workspace files (zone-changes.json + GLB assets) from source zone.
    ws_src = _workspace_dir(zone_rel, create=False)
    if ws_src.exists():
        for f in ws_src.iterdir():
            if not f.is_file() or f.name == "zone-meta.json":
                continue
            if f.name == "zone-changes.json" or f.suffix.lower() in (".glb", ".gltf"):
                _shutil.copy2(f, ws_new / f.name)

    # DB migration — clone source zone's zone_settings + zone_weather.
    db_msg = None
    mig_sql = _zone_migration_sql(new_zone_id, zone_name, source_zone_id)
    (ws_new / "zone-migration.sql").write_text(mig_sql, encoding="utf-8")
    if DB_AUTOAPPLY:
        try:
            n = _apply_migration(mig_sql)
            db_msg = (f"applied {n} statement(s) to {DB_NAME}@{DB_HOST} "
                      f"(cloned from zone {source_zone_id})")
        except Exception as exc:
            db_msg = f"auto-apply skipped: {exc}"

    # Scaffold the per-zone server Lua (IDs.lua + Zone.lua) for the duplicate.
    from xi.zone.xi_new import write_server_zone_scripts
    server_msg = write_server_zone_scripts(new_zone_id, zone_name)

    return {
        "zoneId": new_zone_id, "fileId": new_fid, "datUrl": dat_url,
        "datPath": str(dst_model), "name": zone_name,
        "sourceZoneId": source_zone_id, "companions": companions, "db": db_msg,
        "server": server_msg,
    }


def _zone_delete(params: dict) -> dict:
    """Delete a custom zone by ID (must be 400+).

    Removes all four per-zone DATs (model + event/dialog/npc) from
    FFXI_DIR/ROM10 and zeros their FTABLE10/VTABLE10 + base-table entries.
    Accepts ``{ zoneId: <int> }``."""
    import struct as _struct
    from xi.ftable.xi_core import load_tables
    from xi.zone.xi_inject import (
        zone_model_file_id, zone_event_file_id, zone_dialog_file_id, zone_npc_file_id,
        unregister_zone_file,
    )

    _MIN_ZONE_ID = 400
    zone_id = params.get("zoneId")
    if zone_id is None:
        raise ValueError("missing 'zoneId'")
    zone_id = int(zone_id)
    if zone_id < _MIN_ZONE_ID:
        raise ValueError(
            f"Zone ID {zone_id} is below {_MIN_ZONE_ID}. Only custom zones (400+) can be deleted.")

    model_fid = zone_model_file_id(zone_id)
    result = load_tables(10)
    if result is None:
        raise ValueError("Could not load FTABLE10/VTABLE10.")
    fdata, vdata = result

    # Guard: the model must currently be registered, else there's nothing to delete.
    if model_fid >= len(vdata) or (
        vdata[model_fid] == 0
        and _struct.unpack_from("<H", fdata, model_fid * 2)[0] == 0
    ):
        raise ValueError(
            f"Zone {zone_id} (file_id={model_fid}) is not registered — already deleted?")

    removed = []
    for fn in (zone_model_file_id, zone_event_file_id, zone_dialog_file_id, zone_npc_file_id):
        fid = fn(zone_id)
        if fid * 2 + 2 > len(fdata) or fid >= len(vdata):
            continue
        ft_val = _struct.unpack_from("<H", fdata, fid * 2)[0]
        if vdata[fid] == 0 and ft_val == 0:
            continue  # not registered
        subdir, file_idx = ft_val >> 7, ft_val & 0x7F
        dat_path = Path(FFXI_DIR) / "ROM10" / str(subdir) / f"{file_idx}.DAT"
        if dat_path.exists():
            dat_path.unlink()
            removed.append(str(dat_path))
        unregister_zone_file(fid)

    return {"ok": True, "zoneId": zone_id, "fileId": model_fid, "removed": removed}


def _zone_list_custom() -> dict:
    """Scan FTABLE10/VTABLE10 for custom zones (ID 400–511) that have a live DAT on disk.

    Returns ``{ zones: [{ zoneId, fileId, datUrl }] }`` sorted by zone ID."""
    import struct as _struct
    from xi.ftable.xi_core import load_tables
    from xi.xi_config import FFXI_DIR

    result = load_tables(10)
    if result is None:
        return {"zones": []}

    fdata, vdata = result
    zones = []
    for zone_id in range(400, 512):
        fid = 0x147B3 + (zone_id - 0x100)
        if fid * 2 + 2 > len(fdata) or fid >= len(vdata):
            break
        if vdata[fid] != 10:
            continue
        ft_val = _struct.unpack_from("<H", fdata, fid * 2)[0]
        subdir   = ft_val >> 7
        file_idx = ft_val & 0x7F
        dat_path = Path(FFXI_DIR) / "ROM10" / str(subdir) / f"{file_idx}.DAT"
        if not dat_path.exists():
            continue
        zones.append({
            "zoneId": zone_id,
            "fileId": fid,
            "datUrl": f"game/ROM10/{subdir}/{file_idx}.DAT",
        })
    return {"zones": zones}


def _zone_list_effects(params: dict) -> dict:
    """List the VFX generators and sound emitters in a source zone DAT.

    Returns ``{effects: [...], soundGens: [...], zone}`` where *effects* are
    visual-only 0x05 generators and *soundGens* are 0x05 generators that drive
    a linked 0x3D SeSep (sound emitters).  Each entry: {id, label, mesh, pos,
    soundId?, soundFile?}."""
    from xi.fx.xi_list import list_effects
    from xi.fx.xi_core import _load_library, classify, parse_sections as _ps, EFFECT_TYPE
    from xi.audio.xi_refs import scan_sound_refs
    from xi.xi_config import FFXI_DIR, read_path_for

    zone_rel = _zone_rel(params.get("zone", ""))
    if not zone_rel:
        raise ValueError("missing 'zone'")
    dat_path = read_path_for(Path(FFXI_DIR) / zone_rel)
    if not dat_path.is_file():
        raise FileNotFoundError(f"DAT not found: {zone_rel}")
    data = dat_path.read_bytes()
    fx = list_effects(dat_path)
    sounds = scan_sound_refs(data)

    # Map SeSep name → sound info so we can detect sound emitter generators.
    # Each 0x05 generator that references a 0x3D section at body offset 0xAC is
    # a sound emitter (the generator and SeSep are a pair linked by FourCC name).
    sesep_by_name = {s["section"].strip(): s for s in sounds}
    sections = _ps(bytearray(data))
    sound_gen_ids: dict = {}   # generator fourcc → sound info
    for sec in sections:
        if sec.type_code != EFFECT_TYPE:
            continue
        body = data[sec.start:sec.start + sec.size]
        if len(body) <= 0xB0:
            continue
        linked = bytes(body[0xAC:0xAC + 4]).decode("latin1").strip()
        if linked in sesep_by_name:
            gen_id = bytes(data[sec.start:sec.start + 4]).decode("latin1").strip()
            sound_gen_ids[gen_id] = sesep_by_name[linked]

    lib = _load_library()
    effects_out = []
    sound_gens_out = []
    for e in fx:
        eid = e["name"].strip()
        entry = classify(e["name"], e.get("mesh"), e.get("texture"), lib)
        label = entry["label"] if entry else "(unidentified)"
        item = {
            "id": eid,
            "label": label,
            "mesh": e.get("mesh"),
            "pos": list(e["pos"]) if e.get("pos") else None,
        }
        snd = sound_gen_ids.get(eid)
        if snd:
            item["soundId"] = snd["sound_id"]
            item["soundFile"] = snd["spw"]
            sound_gens_out.append(item)
        else:
            effects_out.append(item)

    return {"effects": effects_out, "soundGens": sound_gens_out, "zone": zone_rel}

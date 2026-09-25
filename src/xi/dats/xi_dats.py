from __future__ import annotations

import json
import os
import re
import shutil
import struct
import sys
from contextlib import contextmanager
from pathlib import Path

import click

from xi.dats.xi_include import INCLUDE_SCHEMA, LAYOUT_KEY, IncludeError, documents, flatten


DEFAULT_MANIFEST = Path("projects/update.json")


def _resolve_manifest_path(manifest: Path | None, project: str | None) -> Path:
    """Resolve a manifest to a `dats/*.json` path.

    A bare **project name** — whether passed positionally (`dats build gyokko_mask`)
    or via `--project gyokko_mask` — resolves to `dats/<name>.json`, matching the
    project semantics of `dats new`. An explicit path (has a directory part or a
    `.json` suffix) is used verbatim. Falls back to `dats/update.json`."""
    if manifest is not None:
        p = Path(manifest)
        # Bare name (no directory component, no suffix) → treat as a project name.
        if p.parent == Path(".") and p.suffix == "":
            return Path("projects") / f"{p.name}.json"
        return p
    return Path(f"projects/{project}.json") if project else DEFAULT_MANIFEST

# Recommended starting model-id per action type — the floor of each custom band.
# `prepare` stamps these so you have a sane id to bump by hand in update.json;
# `build` turns (type, model_id) into the real file_id. Mirrors the bands the
# ftable/gear tooling already uses (entity floor == MODEL_SAFE_START = 15000).
# entity default sits mid-band (above MODEL_SAFE_START 15000); ceiling is
# MAX_ENTITY_MODELID (default 30000) — not a second hard limit.
DEFAULT_MODEL_ID = {"mesh": 15000, "gear": 3000, "mount": 50, "entity": 15000}
# Action type -> model "kind" used by the build's file-id math.
_MODEL_KIND = {"mesh": "entity", "gear": "gear", "mount": "mount", "entity": "entity"}

# ── `dats new` wizard vocabulary ────────────────────────────────────────────
# Canonical race order (mirrors xi.gear.xi_inject.RACES) plus the short codes
# a user types for the gear branch, and the filename prefixes we auto-match a
# per-race source DAT against (case-insensitive). A single 't'/'m'/'g' file is
# common in packs that ship one shared model, so those aliases point at the
# usual gender.
GEAR_RACES = [
    "HumeMale", "HumeFemale", "ElvaanMale", "ElvaanFemale",
    "TaruMale", "TaruFemale", "Mithra", "Galka",
]
# Human-readable race labels shown back to the user after auto-detection.
RACE_LABEL = {
    "HumeMale": "Hume Male", "HumeFemale": "Hume Female",
    "ElvaanMale": "Elvaan Male", "ElvaanFemale": "Elvaan Female",
    "TaruMale": "Taru Male", "TaruFemale": "Taru Female",
    "Mithra": "Mithra", "Galka": "Galka",
}
# Filename stem prefixes accepted for each race when scanning a source folder.
RACE_FILE_PREFIXES = {
    "HumeMale": ("hm", "humemale", "hume_male"),
    "HumeFemale": ("hf", "humefemale", "hume_female"),
    "ElvaanMale": ("em", "elvaanmale", "elvaan_male"),
    "ElvaanFemale": ("ef", "elvaanfemale", "elvaan_female"),
    # Bare "t"/"taru"/"tarutaru" is handled specially (shared across both Taru
    # genders — they use one skeleton/model), so it's NOT listed here.
    "TaruMale": ("tm", "tarumale", "taru_male"),
    "TaruFemale": ("tf", "tarufemale", "taru_female"),
    "Mithra": ("mi", "mithra", "m"),
    "Galka": ("ga", "galka", "g"),
}
# Two-letter race codes FFXI embeds in gear-DAT resource names: the 0x01 model
# header ("1hf_" = slot digit + code) and 0x20 texture headers ("hf_m31_2").
# The shared Tarutaru model uses "tr" (body textures occasionally "tl").
RACE_CONTENT_CODES = {
    "hm": ["HumeMale"], "hf": ["HumeFemale"],
    "em": ["ElvaanMale"], "ef": ["ElvaanFemale"],
    "tr": ["TaruMale", "TaruFemale"], "tl": ["TaruMale", "TaruFemale"],
    "mt": ["Mithra"], "gl": ["Galka"],
}
# The nine equip slots (mirrors xi.gear.xi_core.SLOTS).
GEAR_SLOTS = ["face", "head", "body", "hands", "legs", "feet", "main", "sub", "ranged"]
# Slot digit FFXI embeds as the FIRST character of a gear DAT's 0x01 model-header
# name ("1em_…" = head + ElvaanMale; digits verified against retail DATs). Armor
# slots only — weapon names (main/sub/ranged) follow several other schemes, so
# those never resolve from content and fall back to filename keywords.
SLOT_CONTENT_DIGITS = {"0": "body", "1": "head", "2": "hands", "3": "legs",
                       "4": "feet", "5": "face"}
# Whole-word filename keywords accepted per slot when scanning a source folder
# ("Loxley Hands.DAT" -> hands), checked in GEAR_SLOTS order.
SLOT_FILE_KEYWORDS = {
    "face": ("face",),
    "head": ("head", "helm", "helmet", "hat", "cap", "crown", "mask", "circlet"),
    "body": ("body", "chest", "harness", "robe", "tunic", "vest", "doublet"),
    "hands": ("hands", "hand", "gloves", "gauntlets", "cuffs", "mitts", "mittens",
              "bracers"),
    "legs": ("legs", "leg", "pants", "trousers", "slops", "hose", "brais",
             "subligar", "shorts", "skirt"),
    "feet": ("feet", "foot", "boots", "shoes", "greaves", "gaiters", "sandals",
             "clogs", "sabots", "ledelsens"),
    "main": ("main", "mainhand", "weapon"),
    "sub": ("sub", "offhand", "shield", "grip"),
    "ranged": ("ranged", "bow", "gun", "crossbow"),
}


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid JSON in {path}: {exc}") from exc


def _dump_json(data: object, output: Path | None = None) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        click.echo(f"Wrote {output}")
        return
    click.echo(text)


def _default_manifest(path: Path) -> dict:
    name = path.stem
    return {
        "schema": "xi.dats.v1",
        "name": name,
        "version": 1,
        "roots": {
            "standard": "projects/ffxi",
            "hd": "projects/ffxi-hd",
            "resources": "projects/resources",
        },
        "actions": [],
    }


def _read_manifest(path: Path) -> dict:
    if not path.exists():
        return _default_manifest(path)
    manifest = _load_json(path)
    manifest.setdefault("schema", "xi.dats.v1")
    manifest.setdefault("name", path.stem)
    manifest.setdefault("version", 1)
    manifest.setdefault("roots", {})
    roots = manifest["roots"]
    roots.setdefault("standard", "projects/ffxi")
    roots.setdefault("hd", "projects/ffxi-hd")
    roots.setdefault("resources", "projects/resources")
    roots.pop("changelog", None)  # results now live inline on each action (was a side file)
    manifest.setdefault("actions", [])
    if any(isinstance(a, str) for a in manifest["actions"]):
        # Includes: splice each file's actions in; _write_manifest puts them back.
        try:
            manifest["actions"], manifest[LAYOUT_KEY] = flatten(path, manifest["actions"])
        except IncludeError as exc:
            raise click.ClickException(str(exc)) from exc
    for action in manifest["actions"]:
        if isinstance(action, dict):
            action.pop("op", None)  # `op` is unused — dropped on read so it's shed on next write
    return manifest


def _write_manifest(path: Path, manifest: dict) -> None:
    """Write the project file, and each include file it was read with whose content
    changed (a build's `result` goes back beside the action that earned it; an untouched
    include keeps its own formatting)."""
    for file, doc in documents(path, manifest).items():
        if file != path and file.exists():
            try:
                if json.loads(file.read_text(encoding="utf-8")) == doc:
                    continue
            except ValueError:
                pass
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _root_path(manifest_path: Path, manifest: dict, key: str) -> Path:
    root = Path(manifest["roots"][key])
    return root.resolve()


def _resource_root(manifest_path: Path, manifest: dict) -> Path:
    return _root_path(manifest_path, manifest, "resources")


def _plan_result(action: dict) -> dict:
    """Compute an action's ``result`` — the concrete allocation it lands on
    (model_id → file_id → DAT). Deterministic from the action definition, so it's
    the same whether written at `dats new` time or after a build. Recorded INLINE
    on the action (``action["result"]``) so the manifest is self-describing; no
    separate ledger file. Idempotent — re-running just overwrites the same key."""
    kind = action.get("type")
    model = action.get("model") or {}
    target = action.get("target") or {}

    if kind == "gear":
        from xi.gear.xi_inject import custom_fid, RACES
        slot = action.get("slot")
        mid = model.get("model_id")
        max_model = _gear_expand_max()
        placements = []
        for t in action.get("targets") or []:
            race = t.get("race")
            fid = None
            if mid is not None and max_model is not None and race in RACES and slot in GEAR_SLOTS:
                fid = custom_fid(race, slot, int(mid), max_model)
            placements.append({"race": race, "dat": _rom_rel(t.get("dat", "")), "file_id": fid})
        return {"model_id": mid, "slot": slot, "placements": placements}

    if kind in ("entity", "mesh"):
        from xi.entity.xi_core import MODEL_FILE_OFFSET
        mid = model.get("model_id")
        dat = target.get("dat")
        return {"model_id": mid,
                "file_id": (int(mid) + MODEL_FILE_OFFSET) if mid is not None else None,
                "dat": _rom_rel(dat) if dat else None}

    if kind == "mount":
        from xi.mount.xi_core import file_id_for, key_item_for
        mid = target.get("mount_id")
        if mid is None:
            mid = model.get("model_id")
        text = action.get("text") or {}
        has_ki = bool(text.get("add_key_item", True)) and not text.get("no_key_item")
        res: dict = {"mount_id": mid, "dat": _rom_rel(target["dat"]) if target.get("dat") else None}
        if isinstance(mid, int):
            res["file_id"] = file_id_for(mid)
            res["key_item"] = key_item_for(mid) if has_ki else None
        return res

    return {}


def _slug(value: str) -> str:
    value = value.replace("\\", "/").lower()
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return value or "action"


def _project_slug(value: str) -> str:
    """Project filename slug: lowercase, spaces -> '_', and everything that
    isn't a-z/0-9 dropped (not turned into '_'). So "Gyokko's Mask" -> "gyokkos_mask"."""
    value = value.strip().lower()
    value = re.sub(r"\s+", "_", value)          # runs of whitespace -> single underscore
    value = re.sub(r"[^a-z0-9_]+", "", value)   # drop any other punctuation
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "project"


def _rom_rel(value: str) -> str:
    value = str(value).replace("\\", "/").removeprefix("game/")
    if value.upper().endswith(".DAT"):
        return value
    if re.match(r"^ROM[0-9]*/[0-9]+/[0-9]+$", value, re.I):
        return value + ".DAT"
    return value


def _parse_rom_placement(rom_rel: str) -> tuple[int, int, int]:
    """Split a ROM-relative DAT path into (rom_version, subdir, file_idx).

    'ROM10/5/3.DAT' -> (10, 5, 3);  'ROM/5/3.DAT' -> (1, 5, 3). These are the
    three fields the FTABLE/VTABLE encode: rom = VTABLE byte, FTABLE entry =
    (subdir << 7) | file_idx (9-bit subdir, 7-bit file)."""
    m = re.match(r"^ROM([0-9]*)/([0-9]+)/([0-9]+)\.DAT$", rom_rel, re.I)
    if not m:
        raise click.ClickException(
            f"target.dat {rom_rel!r} must look like ROM10/5/3 (ROM<n>/<subdir>/<file>).")
    rom = int(m.group(1)) if m.group(1) else 1
    subdir, file_idx = int(m.group(2)), int(m.group(3))
    if not (0 <= file_idx <= 127):
        raise click.ClickException(f"file index {file_idx} in {rom_rel!r} must be 0-127 (7-bit field).")
    if not (0 <= subdir <= 511):
        raise click.ClickException(f"subdir {subdir} in {rom_rel!r} must be 0-511 (9-bit field).")
    return rom, subdir, file_idx


def _patch_package_tables(file_id: int, ftval: int, rom: int) -> None:
    """Register file_id -> placement in the package FTABLE/VTABLE. Must run inside
    `_package_config` so the editable tables resolve under the standard root.
    Writes the ROM{rom} overlay plus the base tables (the client reads the base
    at file-open), matching `xi ftable set`."""
    from xi.ftable.xi_core import ftable_path, vtable_path, patch_table
    from xi.xi_config import editable_dat

    ftp = editable_dat(ftable_path(rom), fresh=False)
    entries = ftp.stat().st_size // 2
    if file_id >= entries:
        raise click.ClickException(
            f"Package FTABLE for ROM{rom} holds {entries:,} entries; file_id {file_id:,} is out "
            f"of range. Run `xi ftable expand entity` on your install first (the package copies "
            f"your tables).")
    patch_table(ftable_path(rom), vtable_path(rom), file_id, ftval, rom)
    if rom != 1:
        patch_table(ftable_path(1), vtable_path(1), file_id, ftval, rom)


# ── Build target ────────────────────────────────────────────────────────────
# A build writes DATs + FTABLE/VTABLE patches DIRECTLY into a live target root —
# the base install (FFXI_DIR) or the CatsEyeXI pivot overlay (FFXI_PIVOT_DIR).
# There is no intermediate pack: placement + table patches land in the chosen
# root, whose tables must already exist and be expanded. `_BUILD_TARGET_ROOT` is
# set by `build_cmd` for the duration of a build; tables are `.base`-backed-up
# once before the first patch so the edit stays recoverable.

_BUILD_TARGET_ROOT: Path | None = None


def _set_target_root(root: Path | None) -> None:
    global _BUILD_TARGET_ROOT
    _BUILD_TARGET_ROOT = root


# Build/package target names -> (label, config var name).
_TARGET_LABELS = {"pivot": "Pivot folder", "hd": "HD overlay", "dir": "Base install"}


def _target_root(target: str) -> Path:
    """Resolve a target name to a root: 'dir'=FFXI_DIR, 'pivot'=FFXI_PIVOT_DIR,
    'hd'=FFXI_HD_DIR."""
    from xi.xi_config import FFXI_DIR, FFXI_PIVOT_DIR, FFXI_HD_DIR
    if target == "pivot":
        if not FFXI_PIVOT_DIR:
            raise click.ClickException("FFXI_PIVOT_DIR is not configured.")
        return Path(FFXI_PIVOT_DIR)
    if target == "hd":
        if not FFXI_HD_DIR:
            raise click.ClickException("FFXI_HD_DIR is not configured.")
        return Path(FFXI_HD_DIR)
    return Path(FFXI_DIR)


def _active_build_root() -> Path:
    """Root the current build writes DATs + tables into (set by build_cmd; falls
    back to FFXI_DIR for out-of-build reads)."""
    if _BUILD_TARGET_ROOT is not None:
        return _BUILD_TARGET_ROOT
    from xi.xi_config import FFXI_DIR
    return Path(FFXI_DIR)


def _pivot_build_dir() -> Path:
    return _active_build_root()


def _table_rel_paths():
    yield Path("FTABLE.DAT")
    yield Path("VTABLE.DAT")
    for n in range(2, 11):
        yield Path(f"ROM{n}") / f"FTABLE{n}.DAT"
        yield Path(f"ROM{n}") / f"VTABLE{n}.DAT"


def _ftable_entries(ft: Path) -> int:
    """Entry count (uint16 each) of an FTABLE.DAT, or 0 if absent."""
    return (ft.stat().st_size // 2) if ft.exists() else 0


def _backup_once(path: Path) -> None:
    """Copy a table to <name>.base once before its first modification, so the
    change stays recoverable (matches `xi ftable reset`'s .base backups)."""
    base = path.with_name(path.name + ".base")
    if path.exists() and not base.exists():
        shutil.copy2(path, base)


def _patch_raw_table(ft: Path, vt: Path, file_id: int, ftval: int, vt_val: int) -> None:
    if not ft.exists() or not vt.exists():
        raise click.ClickException(
            f"Target table {ft} is missing — build into a folder that already has "
            "FTABLE/VTABLE (FFXI_DIR, or a provisioned FFXI_PIVOT_DIR).")
    if ft.stat().st_size < (file_id + 1) * 2 or vt.stat().st_size < file_id + 1:
        raise click.ClickException(
            f"Target table {ft} is too small for file_id {file_id:,} — run `xi ftable expand` "
            "on it first.")
    _backup_once(ft)
    _backup_once(vt)
    with open(ft, "r+b") as f:
        f.seek(file_id * 2)
        f.write(struct.pack("<H", ftval))
    with open(vt, "r+b") as f:
        f.seek(file_id)
        f.write(bytes([vt_val & 0xFF]))
    from xi.ftable.xi_core import forget_tables
    forget_tables()


def _table_holds(ft: Path, vt: Path, file_id: int) -> bool:
    """Both tables exist and are large enough to carry ``file_id``."""
    return (ft.exists() and vt.exists()
            and ft.stat().st_size >= (file_id + 1) * 2 and vt.stat().st_size >= file_id + 1)


def patch_launcher_tables(file_id: int, ftval: int, rom: int) -> None:
    """Register file_id -> placement in the active target, in the table pair the client
    resolves it through (xi.ftable.xi_core.resolve_dat_in_root).

    A ROM{n} placement registers in the target's ROM{n} pair: the client honours that
    entry over the main FTABLE/VTABLE, and reads a pivot folder's ROM{n} pair in place
    of the install's. The base install's main pair gets the same entry when it is large
    enough, for the tools that read only that pair; a retail-sized main FTABLE is left
    alone instead of failing the build, and a pivot folder's main pair is never touched —
    the client never reads it.

    A ROM/ placement registers in the main pair, which counts only in the base install;
    ``_check_registrable`` refuses one that would need a new entry anywhere else."""
    root = _active_build_root()
    in_install = _root_target_name(root) == "dir"
    main_ft, main_vt = root / "FTABLE.DAT", root / "VTABLE.DAT"
    if rom == 1:
        if in_install:
            _patch_raw_table(main_ft, main_vt, file_id, ftval, rom)
        return
    rom_ft, rom_vt = root / f"ROM{rom}" / f"FTABLE{rom}.DAT", root / f"ROM{rom}" / f"VTABLE{rom}.DAT"
    if not (rom_ft.exists() and rom_vt.exists()):
        raise click.ClickException(
            f"{root} has no ROM{rom} tables, so file_id {file_id:,} cannot be registered there. "
            f"Run `xi ftable expand` on it, or build into a folder that has ROM{rom}/FTABLE{rom}.DAT.")
    _patch_raw_table(rom_ft, rom_vt, file_id, ftval, rom)
    if in_install and _table_holds(main_ft, main_vt, file_id):
        try:
            _patch_raw_table(main_ft, main_vt, file_id, ftval, rom)
        except PermissionError:
            pass   # the copy for main-pair readers only; the ROM{rom} entry is registered


def _patch_active_tables(file_id: int, ftval: int, rom: int) -> None:
    patch_launcher_tables(file_id, ftval, rom)


def _check_registrable(file_id: int, place_rel: str, rom: int, action_id: str) -> None:
    """Refuse, before anything is written, a ROM/ placement outside the base install
    that needs a new main FTABLE entry: the client reads the main pair only from the
    install, so an entry in a pivot folder would never count. When the client already
    resolves file_id to that path, the DAT alone overrides the file and passes."""
    if rom != 1 or _root_target_name(_active_build_root()) == "dir":
        return
    from xi.ftable.xi_core import resolve_dat_in_root
    from xi.xi_config import FFXI_DIR
    current, _ = resolve_dat_in_root(FFXI_DIR, file_id)
    if (current or "").upper() != place_rel.upper():
        raise click.ClickException(
            f"{action_id}: {place_rel} is a ROM/ placement, which registers in the main FTABLE — and "
            "the client reads that only from the base install, never from the pivot folder. Build it "
            "without --pivot, or place it under ROM10.")


def _active_placement(file_id: int) -> str | None:
    """What file_id resolves to for a client reading through the active target (the
    collision check): its ROM{n} pair first, then the install's main pair."""
    from xi.ftable.xi_core import resolve_dat_in_root
    return resolve_dat_in_root(_active_build_root(), file_id)[0]


def _current_launcher_placement(file_id: int) -> str | None:
    return _active_placement(file_id)


def _copy_file(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)
    return dst


def _copy_zone_change_resources(source: Path, dest_dir: Path) -> Path:
    data = _load_json(source)
    for change in data.get("placements", []):
        if change.get("op") != "add" or not change.get("glb"):
            continue
        glb = Path(change["glb"])
        if not glb.is_absolute():
            glb = source.parent / glb
        if glb.exists():
            _copy_file(glb, dest_dir / glb.name)
            change["glb"] = glb.name
    dest = dest_dir / "zone-changes.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dest


def _copy_resource_reference(value: str, source: Path, dest_dir: Path, resource_root: Path) -> str:
    ref = Path(value)
    candidates = [ref] if ref.is_absolute() else [source.parent / ref, resource_root / ref]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            dest = _copy_file(candidate, dest_dir / candidate.name)
            return _relative_to_resources(dest, resource_root)
    return value.replace("\\", "/")


def _link_resource_reference(value: str, source: Path, resource_root: Path) -> str:
    """Resolve a resource reference to an absolute path without copying it —
    the manifest keeps pointing straight at the exports folder, so re-exporting
    (e.g. re-running `mesh export`) updates the build without a re-`prepare`."""
    ref = Path(value)
    candidates = [ref] if ref.is_absolute() else [source.parent / ref, resource_root / ref]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return str(candidate.resolve())
    return value.replace("\\", "/")


def _copy_action_resources(data: dict, source: Path, dest_dir: Path, resource_root: Path, copy: bool = True) -> dict:
    resources = dict(data.get("resources") or {})
    for key, value in list(resources.items()):
        if isinstance(value, str):
            resources[key] = (
                _copy_resource_reference(value, source, dest_dir, resource_root) if copy
                else _link_resource_reference(value, source, resource_root)
            )
    return resources


def _relative_to_resources(path: Path, resource_root: Path) -> str:
    try:
        return path.resolve().relative_to(resource_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _action_id_for(source: Path, action_type: str, target: str | None) -> str:
    if action_type == "zone" and target:
        return f"zone.{_slug(target)}"
    return f"{action_type}.{_slug(source.stem)}"


def _detect_source_type(source: Path, explicit_type: str | None) -> tuple[str, dict]:
    data = _load_json(source)
    if explicit_type:
        return explicit_type, data
    # Database edits: a bare list of them, or {"edits": [...]} (schema/database.json).
    if isinstance(data, list):
        if all(isinstance(e, dict) and "table" in e for e in data):
            return "database", data
        raise click.ClickException(f"Cannot infer action type for {source}. Pass --type.")
    if isinstance(data.get("edits"), list) and not data.get("type"):
        return "database", data
    # A zone's dialog lines (schema/zone_dialog.json): {"zone", "lines"}.
    if isinstance(data.get("lines"), list) and "zone" in data and not data.get("type"):
        return "zone_dialog", data
    # A zone's NPC list (schema/zone_npcs.json): {"zone", "npcs"}.
    if isinstance(data.get("npcs"), list) and "zone" in data and not data.get("type"):
        return "zone_npcs", data
    # A zone's events (schema/zone_events.json): {"zone", "events"}, or one cutscene
    # (xi.cutscene.v1 — the zone editor's, or `xi event decompile`'s).
    from xi.event.xi_zone_events import SCHEMAS as CUTSCENE_SCHEMAS
    if (isinstance(data.get("events"), list) and "zone" in data and not data.get("type")
            and not isinstance(data.get("sources"), dict)) or data.get("schema") in CUTSCENE_SCHEMAS:
        return "zone_events", data
    if source.name == "zone-changes.json" or "placements" in data or "vfx" in data or "zone" in data:
        return "zone", data
    # A spell / command definition (schema/spell_definition.json, command_definition.json).
    from xi.menu.xi_menu_table import DEFINITION_SCHEMAS
    if data.get("schema") in DEFINITION_SCHEMAS:
        return DEFINITION_SCHEMAS[data["schema"]], data
    # An ability recipe (schema/ability_recipe.json): lanes of sources + routine events.
    if isinstance(data.get("sources"), dict) and isinstance(data.get("events"), list):
        return "ability", data
    kind = data.get("type") or data.get("schema", "").split(".")[-1]
    if kind:
        return str(kind), data
    raise click.ClickException(f"Cannot infer action type for {source}. Pass --type.")


def _add_or_replace_action(manifest: dict, action: dict, replace: bool, preserve: tuple[str, ...] = ()) -> None:
    """Add a new action, or on --replace, refresh an existing one from a fresh
    export/schema while keeping ``preserve`` keys (e.g. a hand-edited
    ``target``/``model``) as they already are in the manifest."""
    actions = manifest.setdefault("actions", [])
    for idx, existing in enumerate(actions):
        if existing.get("id") == action["id"]:
            if not replace:
                raise click.ClickException(
                    f"Action {action['id']!r} already exists. Use --replace to update it.")
            merged = dict(action)
            for key in preserve:
                if key in existing:
                    merged[key] = existing[key]
            actions[idx] = merged
            return
    actions.append(action)


def _print_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    def fmt(row: tuple[str, ...]) -> str:
        return " | ".join(cell.ljust(w) for cell, w in zip(row, widths))
    click.echo(fmt(headers))
    click.echo("-+-".join("-" * w for w in widths))
    for row in rows:
        click.echo(fmt(row))


def _resolve_existing_rom_source(rom_rel: str, redirect_root: str | None) -> Path:
    from xi.xi_config import FFXI_DIR

    rel = Path(*_rom_rel(rom_rel).split("/"))
    candidates = []
    if redirect_root:
        candidates.append(Path(redirect_root) / rel)
    candidates.append(Path(FFXI_DIR) / rel)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise click.ClickException(f"Source DAT for {rom_rel} was not found in FFXI_DIR.")


def _load_changes_for_apply(path: Path) -> dict:
    data = _load_json(path)
    for change in data.get("placements", []):
        if change.get("op") == "add" and change.get("glb"):
            p = Path(change["glb"])
            if not p.is_absolute():
                change["glb"] = str(path.parent / p)
    return data


def _copy_tables_to_package(standard_root: Path, redirect_root: str | None) -> list[str]:
    from xi.xi_config import FFXI_DIR

    copied = []
    for rel in ("FTABLE.DAT", "VTABLE.DAT", "ROM10/FTABLE10.DAT", "ROM10/VTABLE10.DAT"):
        src = None
        rel_path = Path(*rel.split("/"))
        for base in (Path(redirect_root) if redirect_root else None, Path(FFXI_DIR)):
            if base is None:
                continue
            candidate = base / rel_path
            if candidate.exists():
                src = candidate
                break
        if src is not None:
            _copy_file(src, standard_root / rel_path)
            copied.append(rel)
    return copied


def _ensure_rom_tables(standard_root: Path, rom_idx: int = 10) -> None:
    from xi.xi_config import FFXI_DIR

    rom_dir = standard_root / f"ROM{rom_idx}"
    rom_dir.mkdir(parents=True, exist_ok=True)
    ft = rom_dir / f"FTABLE{rom_idx}.DAT"
    vt = rom_dir / f"VTABLE{rom_idx}.DAT"
    base_ft = Path(FFXI_DIR) / "FTABLE.DAT"
    base_vt = Path(FFXI_DIR) / "VTABLE.DAT"
    if not ft.exists():
        ft.write_bytes(b"\x00" * base_ft.stat().st_size)
    if not vt.exists():
        vt.write_bytes(b"\x00" * base_vt.stat().st_size)


@contextmanager
def _package_config(standard_root: Path, hd_root: Path):
    """Route DAT writes into a build/package root for the duration of the block
    via xi_config's internal redirect (normally None = write in place)."""
    import xi.xi_config as cfg

    old_redirect = cfg._REDIRECT_DIR
    old_hd = cfg.FFXI_HD_DIR
    cfg._REDIRECT_DIR = str(standard_root.resolve())
    cfg.FFXI_HD_DIR = str(hd_root.resolve())
    try:
        yield
    finally:
        cfg._REDIRECT_DIR = old_redirect
        cfg.FFXI_HD_DIR = old_hd


def _build_zone(action: dict, manifest_path: Path, manifest: dict, redirect_root: str | None) -> dict:
    from xi.zone.xi_apply_changes import apply_changes_data

    resources = action.get("resources", {})
    target = action.get("target", {})
    options = action.get("options", {})
    changes_rel = resources.get("changes")
    dat_rel = target.get("dat")
    if not changes_rel or not dat_rel:
        raise click.ClickException(f"{action['id']}: zone actions require target.dat and resources.changes")

    resource_root = _resource_root(manifest_path, manifest)
    changes_path = resource_root / changes_rel
    if not changes_path.exists():
        raise click.ClickException(f"{action['id']}: changes file not found: {changes_path}")

    standard_root = _root_path(manifest_path, manifest, "standard")
    hd_root = _root_path(manifest_path, manifest, "hd")
    source_dat = _resolve_existing_rom_source(dat_rel, redirect_root)
    rel_path = Path(*_rom_rel(dat_rel).split("/"))

    results = {"id": action["id"], "type": "zone", "outputs": []}
    if options.get("apply_standard", True):
        dest = _copy_file(source_dat, standard_root / rel_path)
        if not options.get("copy_only", False):
            apply_changes_data(dest, _load_changes_for_apply(changes_path), use_hd=False)
        results["outputs"].append(str(dest))

    if options.get("apply_hd", False):
        dest = _copy_file(source_dat, hd_root / rel_path)
        if not options.get("copy_only", False):
            apply_changes_data(dest, _load_changes_for_apply(changes_path), use_hd=False)
        results["outputs"].append(str(dest))

    return results


def _next_mount_id(action: dict) -> int:
    """Reuse the mount id previously recorded on this action's ``result`` (stable
    across rebuilds), else pick the first free menu-visible id."""
    from xi.mount import xi_core as M

    prev = (action.get("result") or {}).get("mount_id")
    if isinstance(prev, int):
        return prev
    cache: dict = {}
    for mount_id in range(M.RETAIL_COUNT, M.MENU_CAP):
        if not M.read_record(mount_id, cache=cache)["occupied"]:
            return mount_id
    raise click.ClickException("No free mount ids in the menu-visible range.")


def _build_mount(action: dict, manifest_path: Path, manifest: dict,
                 force: bool = False, dry_run: bool = False) -> dict:
    """Build a mount action into the active target (live install or a dats/builds
    pack): place the model DAT at the chosen ROM path + register its file_id,
    then write the EN/JP name/help and (optionally) the key-item d_msg overrides."""
    from xi.mount import xi_core as M

    target = action.get("target", {})
    text = action.get("text", {})
    resources = action.get("resources", {})
    dat_src = (_resolve_raw_source(resources["model_dat"], manifest_path, manifest)
               if resources.get("model_dat") else None)

    mount_id = target.get("mount_id")
    if mount_id is None:
        mount_id = (action.get("model") or {}).get("model_id")
    if mount_id == "auto" or mount_id is None:
        mount_id = _next_mount_id(action)
        # Write the resolved id back so the result + a rebuild both see it.
        action.setdefault("target", {})["mount_id"] = mount_id
    else:
        mount_id = int(mount_id)
    file_id = M.file_id_for(mount_id)
    active_root = _active_build_root()

    # Model DAT: place at the wizard-chosen target.dat when given (verbatim into
    # the target), else fall back to register_model's auto placement (ROM10/100+).
    place_rel = _rom_rel(target["dat"]) if target.get("dat") else None
    model_place = None
    if dat_src is not None and place_rel is not None:
        model_place = _place_raw_dat_in_build(dat_src, place_rel, file_id, force=force,
                                              action_id=action["id"], dry_run=dry_run)
    elif dat_src is not None:
        with _package_config(active_root, active_root):
            info = M.register_model(mount_id, str(dat_src), dry_run=dry_run)
        place_rel = info.get("rom_rel")
    else:
        raise click.ClickException(f"{action['id']}: mount action needs resources.model_dat.")

    # Names / help / key item -> the active target's d_msg override DATs.
    name_en = text.get("name_en") or f"Custom Mount {mount_id}"
    name_jp = text.get("name_jp") or name_en
    with _package_config(active_root, active_root):
        M.set_mount_name(mount_id, "en", name_en, dry_run=dry_run)
        M.set_mount_name(mount_id, "jp", name_jp, dry_run=dry_run)
        if text.get("help_en"):
            M.set_mount_name(mount_id, "en", text["help_en"], help_text=True, dry_run=dry_run)
        if text.get("help_jp"):
            M.set_mount_name(mount_id, "jp", text["help_jp"], help_text=True, dry_run=dry_run)

        key_item = None
        if text.get("add_key_item", True) and not text.get("no_key_item"):
            ki_name_en = text.get("key_item_name_en") or f"{name_en} Companion"
            ki_name_jp = text.get("key_item_name_jp") or ki_name_en
            M.set_key_item(mount_id, "en", ki_name_en, desc=text.get("key_item_desc_en") or "", dry_run=dry_run)
            M.set_key_item(mount_id, "jp", ki_name_jp, desc=text.get("key_item_desc_jp") or "", dry_run=dry_run)
            key_item = M.key_item_for(mount_id)

    server = action.get("server", {})
    server_path = None
    if server.get("emit", True):
        server_path = Path("projects") / "server" / "mounts" / f"{action['id'].split('.')[-1]}_{mount_id}.lua"
        if not dry_run:
            server_path.parent.mkdir(parents=True, exist_ok=True)
            server_path.write_text(M.server_bundle(mount_id, name_en, name_en), encoding="utf-8")

    return {
        "id": action["id"], "type": "mount", "mount_id": mount_id,
        "model_id": mount_id, "file_id": file_id, "target_dat": place_rel,
        "key_item": key_item, "name_en": name_en, "server": str(server_path) if server_path else None,
        "source": str(dat_src) if dat_src else None,
        "bytes": dat_src.stat().st_size if dat_src else None,
        "occupied_by": (model_place or {}).get("occupied_by"),
    }


def _build_mesh(action: dict, manifest_path: Path, manifest: dict, force: bool = False,
                dry_run: bool = False) -> dict:
    """Build an entity mesh action: rebuild the donor DAT's geometry from the
    edited GLB, write it into the launcher build mirror at the pivot/override
    pack's ROM path, and (when a model_id is set) register file_id -> placement
    in the build's tables across every mirrored root. ``dry_run`` skips the GLB
    rebuild + all writes and just reports the plan."""
    from xi.entity.mesh.xi_export import resolve_dat_path
    from xi.entity.xi_core import MODEL_FILE_OFFSET

    source = action.get("source") or {}
    target = action.get("target") or {}
    resources = action.get("resources", {})
    options = action.get("options", {})
    model = action.get("model") or {}

    # Donor = the DAT whose structure/skeleton the geometry is rebuilt onto.
    # (Legacy schemas without `source` fall back to `target`.)
    donor_rel = source.get("dat") or target.get("dat")
    if not donor_rel:
        raise click.ClickException(f"{action.get('id')}: mesh action needs a source.dat (donor).")
    donor = resolve_dat_path(donor_rel)
    mesh_path = _resource_root(manifest_path, manifest) / resources["mesh"]

    # Landing location for the built DAT (defaults to the donor's own path). It
    # lands in the pivot/override pack subpath of the build so the client loads it.
    place_rel = _rom_rel(target.get("dat") or donor_rel)
    same_as_source = place_rel.upper() == _rom_rel(donor_rel).upper()
    if same_as_source and not force and not dry_run:
        raise click.ClickException(
            f"{action.get('id')}: target.dat ({place_rel}) is the same as source.dat — this "
            "overwrites the donor DAT in place. Pass --force if that's intentional."
        )
    rom, subdir, file_idx = _parse_rom_placement(place_rel)
    out_dat = _pivot_build_dir() / Path(*place_rel.split("/"))
    if model.get("model_id") is not None:
        _check_registrable(int(model["model_id"]) + MODEL_FILE_OFFSET, place_rel, rom, action["id"])

    result = {
        "id": action["id"], "type": "mesh", "output": str(out_dat),
        "target_dat": place_rel, "source": donor_rel, "mesh": str(mesh_path),
    }
    if same_as_source:
        result["warning"] = "target.dat == source.dat"
    if options.get("unmirror"):
        result["unmirror"] = True

    if not dry_run:
        from xi.entity.mesh.xi_import import build_imported_dat
        from xi.xi_config import read_path_for
        donor_bytes = Path(read_path_for(donor)).read_bytes()
        rebuilt, stats = build_imported_dat(
            donor_bytes, mesh_path,
            mesh_name=source.get("mesh_name") or target.get("mesh_name"),
            double_sided=options.get("double_sided", True),
            manual_scale=options.get("scale", 1.0),
            rotate_y_deg=options.get("rotate_y", 0.0),
            flip_yz=options.get("flip_yz"),
        )
        out_dat.parent.mkdir(parents=True, exist_ok=True)
        out_dat.write_bytes(rebuilt)
        result["vertices"], result["triangles"] = stats["vertices"], stats["triangles"]

    # Custom-model mode: derive the file_id from (kind, model_id) and register it
    # in the build's tables (base + ROM{rom}, every mirrored root). Without a
    # model_id this is a plain DAT drop (no table change).
    if model.get("model_id") is not None:
        kind = model.get("kind", "entity")
        if kind != "entity":
            raise click.ClickException(
                f"{action.get('id')}: mesh build supports model kind 'entity' only (got {kind!r}).")
        model_id = int(model["model_id"])
        file_id = model_id + MODEL_FILE_OFFSET
        ftval = (subdir << 7) | (file_idx & 0x7F)

        # The target tables are shared across every project's manifest — two
        # different projects (or two actions) can silently collide on the same
        # file_id and overwrite each other's placement.
        existing = _active_placement(file_id)
        collision = existing is not None and existing.upper() != place_rel.upper()
        if collision and not force and not dry_run:
            raise click.ClickException(
                f"{action.get('id')}: file_id {file_id} is already registered to {existing} "
                f"in the target — this build would repoint it to {place_rel}. "
                "Pass --force if that's intentional."
            )
        if not dry_run:
            _patch_active_tables(file_id, ftval, rom)
        result.update({"model_id": model_id, "file_id": file_id,
                       "registered": f"file_id {file_id} -> {place_rel} (rom={rom})"})
        if collision:
            result["occupied_by"] = existing

    return result


# ── Verbatim DAT placement (entity / gear / mount model) ────────────────────
# The `dats new` wizard emits actions that place an ALREADY-BUILT DAT at a new
# model_id, with no GLB rebuild. All three share this primitive: drop the file
# byte-for-byte into the flat launcher build pack (dats/builds/) and register
# its file_id -> ROM placement in that pack's seeded FTABLE/VTABLE — exactly the
# table math `_build_mesh` uses for custom entity models.

def _resolve_raw_source(value: str, manifest_path: Path, manifest: dict) -> Path:
    """Resolve a source-DAT reference from an action. Absolute paths win; else
    try the manifest's resource root, then the manifest's own directory, then
    CWD. Raises if nothing is found."""
    ref = Path(value)
    if ref.is_absolute():
        if ref.exists():
            return ref
        raise click.ClickException(f"source DAT not found: {ref}")
    candidates = [
        _resource_root(manifest_path, manifest) / ref,
        manifest_path.parent / ref,
        Path.cwd() / ref,
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    raise click.ClickException(f"source DAT not found: {value} (looked under resources/, "
                               f"{manifest_path.parent}, and CWD).")


def _place_raw_dat_in_build(src: Path | None, place_rel: str, file_id: int, *, force: bool,
                            action_id: str, dry_run: bool = False, data: bytes | None = None) -> dict:
    """Copy ``src`` (or write ``data``, a DAT the build made) verbatim into the active
    target at ``place_rel`` and register ``file_id`` -> that placement in its base +
    ROM{rom} tables. When ``dry_run`` nothing is written: the plan (incl. any occupant
    of file_id) is returned and a collision is reported rather than raised."""
    place_rel = _rom_rel(place_rel)
    rom, subdir, file_idx = _parse_rom_placement(place_rel)
    _check_registrable(file_id, place_rel, rom, action_id)
    out_dat = _pivot_build_dir() / Path(*place_rel.split("/"))
    ftval = (subdir << 7) | (file_idx & 0x7F)
    existing = _active_placement(file_id)
    collision = existing is not None and existing.upper() != place_rel.upper()
    if collision and not force and not dry_run:
        raise click.ClickException(
            f"{action_id}: file_id {file_id} is already registered to {existing} in the "
            f"target — this build would repoint it to {place_rel}. Pass "
            "--force if that's intentional.")

    blob = data if data is not None else src.read_bytes()
    if not dry_run:
        out_dat.parent.mkdir(parents=True, exist_ok=True)
        out_dat.write_bytes(blob)
        _patch_active_tables(file_id, ftval, rom)

    result = {"output": str(out_dat), "target_dat": place_rel, "file_id": file_id,
              "rom": rom, "source": str(src) if src is not None else "(built)", "bytes": len(blob),
              "registered": f"file_id {file_id} -> {place_rel} (rom={rom})"}
    if collision:
        result["occupied_by"] = existing
    return result


def _build_entity(action: dict, manifest_path: Path, manifest: dict, force: bool = False,
                  dry_run: bool = False) -> dict:
    """Place a prebuilt entity (NPC/monster/object) DAT verbatim at a custom
    entity model_id (file_id = model_id + MODEL_FILE_OFFSET)."""
    from xi.entity.xi_core import MODEL_FILE_OFFSET

    resources = action.get("resources", {})
    target = action.get("target", {})
    model = action.get("model", {})
    if not resources.get("raw_dat"):
        raise click.ClickException(f"{action.get('id')}: entity action needs resources.raw_dat.")
    if not target.get("dat"):
        raise click.ClickException(f"{action.get('id')}: entity action needs target.dat.")
    if model.get("model_id") is None:
        raise click.ClickException(f"{action.get('id')}: entity action needs model.model_id.")

    src = _resolve_raw_source(resources["raw_dat"], manifest_path, manifest)
    model_id = int(model["model_id"])
    file_id = model_id + MODEL_FILE_OFFSET
    placed = _place_raw_dat_in_build(src, target["dat"], file_id, force=force,
                                     action_id=action["id"], dry_run=dry_run)
    placed.update({"id": action["id"], "type": "entity", "model_id": model_id})
    return placed


# ── Ability (composed job ability / spell / weapon skill from a recipe) ────────
# The recipe (schema/ability_recipe.json) is the source; `dats build` composes it
# with xi.ability.xi_compose, decides the animation number against the live
# tables, places the DAT(s) in ROM10 and registers the file ids with the same
# verbatim-placement primitive the other types use. Library: xi.ability.xi_publish.

ABILITY_DEFAULT_SUBDIR = 20


def _ability_action_from_recipe(source: Path, resource_root: Path, *, action_id: str | None = None,
                                kind: str | None = None, animation: int | None = None,
                                subdir: int | None = None, animation_from: int | None = None) -> dict:
    """Validate a recipe (a mix file, ``*.mix.json``, or a legacy ``*.recipe.json``), copy
    it to ``projects/resources/ability/<slug>.mix.json`` and return the manifest action for
    it (shared by `dats prepare` and the `dats new` wizard). The action id comes from the
    recipe's ``name``, never the file name, so the viewer's ``check.LOVE.mix.json`` makes
    ``ability.love`` like ``LOVE.mix.json`` does. A recipe whose textures name PNG files is
    stored as load_recipe read it, each PNG inlined as its data URI, so the copy rebuilds on
    its own; any other is copied byte for byte. An older ``<slug>.recipe.json`` copy is
    left where it is (the action just stops pointing at it)."""
    from xi.ability.xi_compose import load_recipe
    inlined: list = []
    recipe = load_recipe(source, inlined=inlined)
    action_id = action_id or f"ability.{_slug(recipe['name'])}"
    dest = resource_root / "ability" / f"{action_id.removeprefix('ability.')}.mix.json"
    if inlined:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(recipe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif source.resolve() != dest.resolve():
        _copy_file(source, dest)
    target: dict = {"animation": animation if animation is not None else "auto",
                    "subdir": subdir if subdir is not None else ABILITY_DEFAULT_SUBDIR}
    if animation_from is not None:
        target["animation_from"] = animation_from
    return {
        "id": action_id, "type": "ability",
        "kind": kind or (recipe.get("target") or {}).get("kind") or "auto",
        "target": target,
        "resources": {"recipe": _relative_to_resources(dest, resource_root)},
        "server": {"emit": True},
    }


#: What the server pass recorded on an ability action (the database row it owns, the
#: client menu record it placed, the Lua stub it wrote). A build keeps them whether or
#: not it runs the pass, so a binding is never lost (schema/ability.json result.db/menu/lua).
ABILITY_SERVER_KEYS = ("db", "menu", "lua")


def _ability_result(built: dict | None, previous: dict | None = None) -> dict:
    """The inline ``result`` recorded for an ability action, from its build result, with
    ``previous``'s ``db`` / ``menu`` / ``lua`` carried over (``undone`` is not: a build
    after a partial undo owns its DATs again)."""
    b = built or {}
    res = {"kind": b.get("kind"), "animation": b.get("animation"),
           "placements": [{"race": p.get("race"), "role": p.get("role"),
                           "file_id": p.get("file_id"), "dat": p.get("dat")}
                          for p in b.get("placements") or []],
           "server": b.get("server")}
    if isinstance(previous, dict):
        for key in ABILITY_SERVER_KEYS:
            if previous.get(key) is not None:
                res[key] = previous[key]
    return res


def _build_ability(action: dict, manifest_path: Path, manifest: dict, force: bool = False,
                   dry_run: bool = False) -> dict:
    """Compose the action's recipe and place the result in the active target: one DAT
    for a job ability or spell, body + two companion DATs per race for a weapon skill.
    A rebuild keeps the animation number and paths recorded on the action."""
    from xi.ability import xi_publish as AP

    resources = action.get("resources", {})
    if not resources.get("recipe"):
        raise click.ClickException(f"{action.get('id')}: ability action needs resources.recipe.")
    # Named first: an id that cannot name its publish folder fails before anything is placed.
    folder = AP.publish_folder(action["id"])
    recipe_path = _resolve_raw_source(resources["recipe"], manifest_path, manifest)
    recipe = AP.load_recipe(recipe_path)
    target = action.get("target") or {}
    anim = target.get("animation", "auto")
    wanted = None if anim in (None, "auto") else int(anim)
    subdir = int(target.get("subdir", ABILITY_DEFAULT_SUBDIR))
    start = target.get("animation_from")
    force = force or bool((action.get("options") or {}).get("force"))
    root = _active_build_root()
    plan = AP.plan(recipe, root, kind=action.get("kind"), animation=wanted, subdir=subdir,
                   force=force, previous=action.get("result"),
                   animation_from=int(start) if start is not None else None)
    sources = AP.write_sources(recipe, plan)

    placements = []
    try:
        _make_room_for_band(root, [f["file_id"] for f in plan["files"]], dry_run)
        for f, src in zip(plan["files"], sources):
            # plan() already applied the slot policy (free job-ability / spell ids;
            # weapon-skill slots only where every race's DAT is a retail dummy; a
            # rebuild's own slots; or --force), so the placement's collision guard is
            # answered here rather than raised again.
            r = _place_raw_dat_in_build(src, f["place"], f["file_id"], force=True,
                                        action_id=action["id"], dry_run=dry_run)
            entry = {"race": f["race"], "role": f["role"], "file_id": f["file_id"],
                     "dat": r["target_dat"], "source": r["source"], "bytes": r["bytes"]}
            cur = f.get("current")
            if cur and cur.upper() != r["target_dat"].upper() and not _is_own_previous(action, cur):
                # A free extended slot still points at retail's placeholder DAT (a
                # `dumm` directory); that is what makes it free, not a collision.
                if AP.is_placeholder(root, cur):
                    entry["placeholder"] = cur
                else:
                    entry["occupied_by"] = cur
            placements.append(entry)
    except PermissionError as e:
        raise click.ClickException(AP.permission_hint(root, e))

    sql = AP.server_snippet(recipe, plan["kind"], plan["animation"])
    # The publish folder (projects/abilities/<slug>/) takes the SQL, a copy of each DAT
    # just placed and placements.json, so the publish can be handed on as one folder.
    # Written after placement, so a failed build leaves the last publish's folder as it was.
    server_name = None
    if (action.get("server") or {}).get("emit", True):
        server_name = f"{folder.name}_{plan['animation']}.sql"
    warnings = list(plan.get("warnings") or [])
    if not dry_run:
        record = AP.publish_record(action["id"], recipe["name"], plan["kind"], plan["animation"],
                                   placements, server_name)
        # The DATs are placed and registered by now, so a folder that cannot be written (a
        # read-only copy, a file in use) is a warning: the build still returns and its result
        # is recorded, which is what undo and the next build go by.
        try:
            AP.write_publish_folder(folder, record, [Path(p["source"]) for p in placements], sql)
        except (OSError, ValueError) as e:
            warnings.append(f"publish folder not updated: {e}")
    return {
        "id": action["id"], "type": "ability", "kind": plan["kind"], "animation": plan["animation"],
        "name": recipe["name"],
        "recipe": str(recipe_path), "placements": placements, "folder": str(folder),
        "server": str(folder / server_name) if server_name else None, "sql": sql,
        "warnings": warnings,
        "registered": f"animation {plan['animation']} -> {len(placements)} DAT(s)",
    }


def _make_room_for_band(root: Path, file_ids: list[int], dry_run: bool) -> None:
    """A custom band's file ids sit past the end of the expanded tables. The client plugin
    (cexislots) grows the client's table in memory and merges each XIPivot overlay's
    ROM pair into it, so the overlay's pair is what has to hold the id: grow the pivot
    folder's to the band ceiling here. The install's tables are never grown for it —
    the plugin does not read band registrations from them."""
    import xi.xi_config as cfg
    from xi.xi_config import CUSTOM_ROM_IDX
    floor, ceiling = cfg.fx_band_floor(), cfg.fx_band_ceiling()
    top = max((f for f in file_ids if floor and floor <= f < ceiling), default=None)
    if top is None:
        return
    ft = root / f"ROM{CUSTOM_ROM_IDX}" / f"FTABLE{CUSTOM_ROM_IDX}.DAT"
    vt = root / f"ROM{CUSTOM_ROM_IDX}" / f"VTABLE{CUSTOM_ROM_IDX}.DAT"
    if not (ft.exists() and vt.exists()) or _table_holds(ft, vt, ceiling - 1):
        return                     # missing tables get their own error; big enough already
    if _root_target_name(root) != "pivot":
        if _table_holds(ft, vt, top):
            return
        raise click.ClickException(
            f"file_id {top:,} is in a custom animation band. The client plugin (cexislots) reads "
            "band registrations from an XIPivot overlay's ROM tables, not the install's, so build "
            "this into the pivot folder: add --pivot (Use Pivot Folder in the model viewer's Manage "
            "panel). Or take a number any client loads (--animation N; `xi ability slots` lists them).")
    have = vt.stat().st_size
    click.echo(f"  {'would grow' if dry_run else 'growing'} {ft.parent.name} tables in the pivot folder "
               f"{have:,} -> {ceiling:,} entries for the custom animation bands "
               "(the install's tables are left alone)")
    if dry_run:
        return
    from xi.ftable.xi_core import forget_tables
    from xi.ftable.xi_expand import _grow_table_file
    _backup_once(ft)
    _backup_once(vt)
    _grow_table_file(str(ft), ceiling, 2, False)
    _grow_table_file(str(vt), ceiling, 1, False)
    forget_tables()


def _is_own_previous(action: dict, dat: str) -> bool:
    """True when ``dat`` is a placement this action recorded on an earlier build."""
    prev = (action.get("result") or {}).get("placements") or []
    return any(str(p.get("dat", "")).upper() == dat.upper() for p in prev)


# ── Spell / command menu records (a new row of ROM/118/114.DAT + its names) ────
# The definition (schema/spell_definition.json, command_definition.json) names a
# retail record to clone and the fields/texts to change; `dats build` decides the
# id against the live table (ids above the retail band only), writes the record
# into the enlarged section, the names/help into the d_msg tables, and emits a
# server row template. Library: xi.menu.xi_menu_table. The client only reads past
# the retail band with a ceiling plugin such as cexislots (docs/menu/records.md).

RECORD_TYPES = ("spell", "command")


def _record_action_from_definition(source: Path, resource_root: Path, kind: str, *,
                                   action_id: str | None = None, record_id: int | None = None,
                                   menu_index: int | None = None) -> dict:
    """Validate a definition, copy it under ``projects/resources/<kind>/`` and return
    the manifest action for it (shared by `dats prepare` and the `dats new` wizard)."""
    from xi.menu.xi_menu_table import MenuError, definition_kind, load_definition
    try:
        d = load_definition(source)
    except MenuError as e:
        raise click.ClickException(str(e))
    if definition_kind(d) != kind:
        raise click.ClickException(f"{source}: is a {definition_kind(d)} definition, not {kind}.")
    action_id = action_id or f"{kind}.{_slug(d['name'])}"
    dest = resource_root / kind / f"{action_id.removeprefix(kind + '.')}.{kind}.json"
    if source.resolve() != dest.resolve():
        _copy_file(source, dest)
    target: dict = {"id": record_id if record_id is not None else d.get("id", "auto")}
    if kind == "spell":
        target["menu_index"] = menu_index if menu_index is not None else d.get("menu_index", "auto")
    return {
        "id": action_id, "type": kind,
        "target": target,
        "resources": {"definition": _relative_to_resources(dest, resource_root)},
        "server": {"emit": True},
    }


def _record_result(built: dict | None) -> dict:
    """The inline ``result`` recorded for a spell / command action."""
    b = built or {}
    res = {"record_id": b.get("record_id"), "like": b.get("like"),
           "dat": b.get("dat"), "strings": b.get("strings") or [], "server": b.get("server")}
    if b.get("menu_index") is not None:
        res["menu_index"] = b["menu_index"]
    if b.get("replaced"):
        res["replaced"] = b["replaced"]   # a forced overwrite's old row, for undo
    return res


def _next_record_id(kind: str, action: dict, menu) -> int:
    """Reuse the id previously recorded on this action's ``result`` (stable across
    rebuilds), else the highest free id above the retail band — top-down, so the
    ids stay clear of anything retail could ever add below."""
    from xi.menu.xi_menu_table import MenuError, pick_id
    prev = (action.get("result") or {}).get("record_id")
    if isinstance(prev, int):
        return prev
    try:
        return pick_id(kind, menu)
    except MenuError as e:
        raise click.ClickException(str(e))


def _root_target_name(root: Path) -> str | None:
    """The build target (`dir` / `pivot` / `hd`) whose folder is ``root``."""
    for name in ("dir", "pivot", "hd"):
        try:
            if _target_root(name).resolve() == Path(root).resolve():
                return name
        except click.ClickException:
            continue
    return None


def _build_record(action: dict, manifest_path: Path, manifest: dict, force: bool = False,
                  dry_run: bool = False) -> dict:
    from xi.menu import xi_menu_table as MT
    kind = action["type"]
    resources = action.get("resources", {})
    if not resources.get("definition"):
        raise click.ClickException(f"{action.get('id')}: {kind} action needs resources.definition.")
    def_path = _resolve_raw_source(resources["definition"], manifest_path, manifest)
    try:
        d = MT.load_definition(def_path)
    except MT.MenuError as e:
        raise click.ClickException(str(e))
    target = action.get("target") or {}
    prev = action.get("result") or {}
    prev_id = prev.get("record_id") if isinstance(prev.get("record_id"), int) else None
    root = _active_build_root()
    # Only a build into this same folder counts as "ours" here: another target may
    # hold something else at that id.
    built_here = _root_target_name(root) in (prev.get("targets") or [])
    try:
        menu = MT.load_menu(root)
    except (OSError, MT.MenuError) as e:
        raise click.ClickException(f"{action['id']}: cannot read {MT.MENU_DAT} under {root}: {e}")
    wanted = target.get("id", "auto")
    record_id = _next_record_id(kind, action, menu) if wanted in (None, "auto") else int(wanted)
    k = MT.KINDS[kind]
    if record_id < k.custom_first and not force:
        raise click.ClickException(
            f"{action['id']}: {kind} id {record_id} is inside the retail band (< {k.custom_first}); "
            "a retail update could take it. Pass --force to use it anyway.")
    if record_id >= k.ceiling:
        raise click.ClickException(f"{action['id']}: {kind} id {record_id} is past the client ceiling {k.ceiling - 1}.")
    # The id changed since the last build into this folder: that row goes back to what
    # it replaced (or empty) instead of being left behind.
    moved_from = prev_id if prev_id is not None and prev_id != record_id and built_here else None
    if moved_from is not None:
        MT.restore_row(kind, menu, moved_from, prev.get("replaced"))
    own = prev_id == record_id and built_here
    recs = menu.records(kind)
    occupied = record_id < len(recs) and not MT.is_empty(recs[record_id]) and not own
    if occupied and not force:
        raise click.ClickException(
            f"{action['id']}: {kind} id {record_id} already holds a record. Pass --force to overwrite it.")
    mi = target.get("menu_index", "auto") if kind == "spell" else None
    menu_index = None if mi in (None, "auto") else int(mi)
    if menu_index is None and kind == "spell" and prev_id == record_id:
        # A rebuild keeps the slot it took (else "next free" would count its own record).
        if isinstance(prev.get("menu_index"), int):
            menu_index = prev["menu_index"]
    try:
        # What a forced overwrite replaces is kept on the result so undo can put it back.
        replaced = prev.get("replaced") if own else MT.capture_record(kind, root, record_id, menu)
        rec = MT.build_record(kind, menu, d, record_id, menu_index)
        fields = MT.read_fields(kind, rec)
        menu.set_record(kind, record_id, rec)
        # Every name / help table is checked before anything is written, so text that
        # does not fit leaves 114.DAT and the string tables as they were.
        MT.set_texts(kind, root, record_id, d["text"], dry_run=True)
        dat_path = MT.save_menu(root, menu, dry_run=dry_run)
        if moved_from is not None:
            MT.restore_texts(kind, root, moved_from, prev.get("replaced"), dry_run=dry_run)
        MT.set_texts(kind, root, record_id, d["text"], dry_run=dry_run)
    except MT.MenuError as e:
        raise click.ClickException(f"{action['id']}: {e}")

    sql = MT.server_snippet(kind, d, record_id, d["like"])
    server_path = None
    if (action.get("server") or {}).get("emit", True):
        server_path = Path("projects") / "server" / f"{kind}s" / f"{action['id'].split('.')[-1]}_{record_id}.sql"
        if not dry_run:
            server_path.parent.mkdir(parents=True, exist_ok=True)
            server_path.write_text(sql + "\n", encoding="utf-8")
    return {
        "id": action["id"], "type": kind, "record_id": record_id, "like": d["like"],
        "name": d["text"]["name_en"], "menu_index": fields.get("menu_index"),
        "dat": MT.MENU_DAT, "output": str(dat_path), "strings": MT.string_tables(kind),
        "replaced": replaced, "moved_from": moved_from,
        "server": str(server_path) if server_path else None, "sql": sql,
        "warning": MT.client_warning(kind, record_id),
        "registered": f"{kind} {record_id} ({d['text']['name_en']}) <- cloned from {d['like']}",
    }


def _wizard_record(kind: str, slug: str, prev: dict | None, manifest_path: Path, manifest: dict) -> dict:
    from xi.menu.xi_menu_table import KINDS, client_warning, load_definition, MenuError
    p = prev or {}
    resource_root = _resource_root(manifest_path, manifest)
    found = sorted(Path("exports").glob(f"**/*.{kind}.json")) if Path("exports").is_dir() else []
    click.echo(f"\n>> Which definition? (a .{kind}.json — see schema/{kind}_definition.json; "
               f"`like` names the retail {kind} to clone)")
    if found:
        click.echo("   Found:")
        for i, f in enumerate(found[:20], 1):
            click.echo(f"     {i}. {f}")
        click.echo("   Enter a number, or type a path.")
    click.echo()
    default = (p.get("resources") or {}).get("definition")
    if default:
        default = str(resource_root / default)
    while True:
        raw = click.prompt("Enter definition", default=default or "", show_default=bool(default)).strip().strip('"')
        if raw.isdigit() and found and 1 <= int(raw) <= len(found[:20]):
            src = found[int(raw) - 1]
        else:
            src = Path(raw)
        if not src.is_file():
            click.echo(f"   ✗ not a file: {src}")
            continue
        try:
            load_definition(src)
            break
        except MenuError as e:
            click.echo(f"   ✗ {e}")
    k = KINDS[kind]
    prev_id = (p.get("target") or {}).get("id", "auto")
    click.echo(f"\n>> Which {kind} id? (auto = the highest free id above the retail band "
               f"{k.custom_first}..{k.ceiling - 1}; ids below need --force)")
    click.echo(click.style(f"   ⚠ {client_warning(kind)}", fg="yellow"))
    click.echo()
    while True:
        raw = click.prompt("Enter id or auto", default=str(prev_id)).strip().lower()
        if raw in ("", "auto"):
            record_id = None
            break
        if raw.isdigit() and int(raw) < k.ceiling:
            record_id = int(raw)
            break
        click.echo(f"   ✗ enter auto or a number 0..{k.ceiling - 1}")
    action = _record_action_from_definition(src, resource_root, kind, action_id=f"{kind}.{slug}",
                                            record_id=record_id)
    if p.get("result"):
        action["result"] = p["result"]   # keep the id a previous build landed on
    return action


_DB_LABELS = {
    "armor": "Armor", "weapons": "Weapons", "general": "General items", "usable": "Consumables",
    "puppet": "Automaton", "maze": "Maze Mongers", "monst1": "Monstrosity instincts",
    "items7": "Items 7 (retail, Sept 2026)", "roeObj": "RoE objectives", "items6": "Commands",
    "gil": "Currency", "keyitems": "Key items", "titles": "Titles", "spells": "Spell names",
    "spellHelp": "Spell help", "abilities": "Ability names", "abilityHelp": "Ability help",
    "spellData": "Spell data (MP, cast, recast, levels)", "abilityData": "Ability data (TP, level, range)",
}
_MASK_FIELDS = ("flags", "jobs", "races", "slots")


def _wizard_change(edit: dict, table: str, raw: str) -> None:
    """Apply one wizard line to ``edit``: ``field=value``, ``jp.field=value``,
    ``field~old=>new`` (replace part of a text), ``mod.NAME=value`` (empty removes it)."""
    from xi.database import xi_core as C
    item = table in C.ITEM_LAYOUT
    if "~" in raw.split("=", 1)[0]:
        key, rest = raw.split("~", 1)
        if "=>" not in rest:
            raise ValueError("write a replace as field~old=>new")
        old, new = rest.split("=>", 1)
        lang, key = ("jp", key[3:]) if key.startswith("jp.") else ("en", key)
        spec = edit.setdefault("strings", {}).setdefault(lang, {})
        cur = spec.get(key.strip())
        rep = cur["replace"] if isinstance(cur, dict) else {}
        spec[key.strip()] = {"replace": {**rep, old.strip(): new.strip().replace("\\n", "\n")}}
        return
    if "=" not in raw:
        raise ValueError("write field=value")
    key, value = (s.strip() for s in raw.split("=", 1))
    if key.startswith("mod."):
        if not item:
            raise ValueError("mods are for item tables")
        mods = edit.setdefault("server", {}).setdefault("item_mods", {})
        mods[key[4:].upper()] = int(value) if value else None
        return
    if key == "note":
        edit["note"] = value
        return
    if table in C.MENU_KINDS:
        # A spell / command record: its fields (mp=5, element=light) and levels.WHM=1
        # (levels.WHM= : the job can't learn it).
        values = edit.setdefault("set", {})
        if key.startswith("levels."):
            values.setdefault("levels", {})[key[7:].upper()] = int(value) if value else None
        elif key in C.menu_fields(table):
            values[key] = int(value) if value.lstrip("-").isdigit() else value.lower()
        else:
            raise ValueError(f"{table} has no {key!r} — it has {', '.join(C.menu_fields(table))} "
                             "(levels as levels.WHM=1)")
        return
    lang, name = ("jp", key[3:]) if key.startswith("jp.") else ("en", key)
    if item and name in C.set_fields(table) and lang == "en":
        if name in _MASK_FIELDS:
            names = [v.strip().upper() for v in re.split(r"[,\s]+", value) if v.strip()]
            edit.setdefault("set", {})[name] = "all" if names == ["ALL"] and name in ("jobs", "races") else names
        elif name == "skill":
            edit.setdefault("set", {})[name] = int(value) if value.isdigit() else value.upper()
        else:
            edit.setdefault("set", {})[name] = int(value)
        return
    if name in C.string_names(table, lang):
        numeric = name in ("article", "category", "keyItem")
        edit.setdefault("strings", {}).setdefault(lang, {})[name] = (
            int(value) if numeric else value.replace("\\n", "\n"))
        return
    fields = (C.set_fields(table) if item else []) + C.string_names(table, "en")
    raise ValueError(f"{table} has no {key!r} — it has {', '.join(fields)}")


def _wizard_database(slug: str, prev: dict | None, pivot: bool) -> dict:
    """`dats new` for record edits: pick a table and an id, see what the record holds,
    then give the changes as field=value lines. Edits go into database.<slug>."""
    from xi.database import xi_build as DB
    from xi.database import xi_core as C
    root = _target_root("pivot" if pivot else "dir")
    action = dict(prev) if prev else {"id": f"database.{slug}", "type": "database", "edits": []}
    edits = list(action.get("edits") or [])
    tables = C.tables()
    labels = [f"{_DB_LABELS.get(t, t)} ({t})" for t in tables]
    while True:
        table = tables[labels.index(_choose("Which table?", labels, default=labels[0]))]
        rid = _ask("Record id? (the item id for items)", type=int)
        at = next((i for i, e in enumerate(edits) if e.get("table") == table and e.get("id") == rid), None)
        edit = dict(edits[at]) if at is not None else {"table": table, "id": rid}
        try:
            cur = DB.describe(root, table, rid)
        except DB.DbError as e:
            click.echo(click.style(f"  {e}", fg="yellow"))
            continue
        if cur["empty"] and (DB.is_item_table(table) or table in C.MENU_KINDS):
            click.echo(f"\n  {table} {rid} is an empty slot ({cur['dat']}).")
            if "like" not in edit:
                edit["like"] = _ask("Copy which record into it? (an id of the same table)", type=int)
        else:
            click.echo(f"\n  {table} {rid}: {cur['name']!r}  ({cur['dat']}, {cur.get('format', 'd_msg')})")
            for k, v in cur["fields"].items():
                click.echo(f"    {k:<14} {v}")
            for k, v in cur["strings"].items():
                click.echo(f"    {k:<14} {_short(v, 90)}")
        click.echo("\n  Changes, one per line (blank when done):  level=50   jobs=WAR,PLD   flags=RARE,EX\n"
                   "    description=New text\\nSecond line   description~HP+15=>HP+30   jp.name=…\n"
                   "    mod.HP=30 (the proposed SQL; mod.HP= removes it)   note=why\n"
                   "    spell / ability data: mp=5   cast=8 (quarter seconds)   element=light   levels.RDM=3")
        while True:
            raw = click.prompt("  change", default="", show_default=False).strip()
            if not raw:
                break
            try:
                _wizard_change(edit, table, raw)
            except ValueError as e:
                click.echo(click.style(f"  {e}", fg="yellow"))
        errs = C.validate_action({"id": action["id"], "type": "database", "edits": [edit]})
        if errs:
            for e in errs:
                click.echo(click.style(f"  ⚠ {e}", fg="yellow"))
            if not click.confirm("  Keep this record's edit anyway?", default=False):
                continue
        if at is not None:
            edits[at] = edit
        else:
            edits.append(edit)
        if not click.confirm("\n>> Edit another record?", default=False):
            break
    action["edits"] = edits
    return action


def _wizard_zone_dialog(slug: str, prev: dict | None, pivot: bool) -> dict:
    """`dats new` for a zone's dialog lines: the zone, then line by line — see the line,
    type its new text (or old=>new to change part of it). `new` adds a line at the end."""
    from xi.dialog import xi_zone_dialog as ZD
    root = _target_root("pivot" if pivot else "dir")
    action = dict(prev) if prev else {"id": f"zone_dialog.{slug}", "type": "zone_dialog", "lines": []}
    zone = _ask("Which zone? (the zone id — xi zone search <name>)", type=int, default=action.get("zone"))
    if zone != action.get("zone"):
        action["lines"] = []
    action["zone"] = zone
    lines = list(action.get("lines") or [])
    try:
        info = ZD.describe(root, zone, 0)
    except ZD.ZoneDialogError as e:
        raise click.ClickException(str(e))
    click.echo(f"\n  zone {zone}: {info['dat']}, {info['count']} lines  (xi event dialogue search to find one)")
    while True:
        raw = click.prompt("\n  Line id (or 'new' to add one at the end)", default="new")
        if raw.strip().lower() == "new":
            taken = [l["id"] for l in lines if l.get("new")]
            lid = max([info["count"] - 1, *taken]) + 1
            line = {"id": lid, "new": True}
            click.echo(f"  new line {lid}")
        else:
            lid = int(raw)
            at = next((i for i, l in enumerate(lines) if l.get("id") == lid), None)
            line = dict(lines[at]) if at is not None else {"id": lid}
            cur = ZD.describe(root, zone, lid)
            if cur["en"] is None:
                click.echo(click.style(f"  zone {zone} has no line {lid} ({cur['count']} lines)", fg="yellow"))
                continue
            click.echo(f"  en: {cur['en']!r}")
            if cur.get("jp") is not None:
                click.echo(f"  jp: {cur['jp']!r}")
        click.echo("  Text: \\n new line in the box, \\v wait for a key, {player} {npc} …; old=>new changes part")
        for lang in ("en", "jp"):
            if lang == "jp" and line.get("new"):
                hint = " (blank: the English text)"
            else:
                hint = " (blank: leave it)"
            text = click.prompt(f"  {lang}{hint}", default="", show_default=False)
            if not text:
                continue
            if "=>" in text and not line.get("new"):
                old, new = text.split("=>", 1)
                line[lang] = {"replace": {old.strip(): new.strip()}}
            else:
                line[lang] = text
        errs = ZD.validate_action({"id": action["id"], "type": "zone_dialog", "zone": zone, "lines": [line]})
        if errs:
            for e in errs:
                click.echo(click.style(f"  ⚠ {e}", fg="yellow"))
        else:
            at = next((i for i, l in enumerate(lines) if l.get("id") == line["id"]), None)
            if at is None:
                lines.append(line)
            else:
                lines[at] = line
        if not click.confirm("\n>> Another line?", default=False):
            break
    action["lines"] = lines
    return action


def _wizard_zone_npcs(slug: str, prev: dict | None, pivot: bool) -> dict:
    """`dats new` for a zone's NPC list: the zone, then rename an NPC by its id or add one
    (`new`) — its name, model and status; the id comes from the custom band."""
    from xi.entity import xi_custom_npc as CN
    from xi.entity import xi_zone_npcs as ZN
    root = _target_root("pivot" if pivot else "dir")
    action = dict(prev) if prev else {"id": f"zone_npcs.{slug}", "type": "zone_npcs", "npcs": []}
    zone = _ask("Which zone? (the zone id — xi zone search <name>)", type=int, default=action.get("zone"))
    if zone != action.get("zone"):
        action["npcs"] = []
    action["zone"] = zone
    npcs = list(action.get("npcs") or [])
    try:
        info = ZN.describe(root, zone)
    except ZN.ZoneNpcsError as e:
        raise click.ClickException(str(e))
    click.echo(f"\n  zone {zone}: {info['dat']}, {len(info['names'])} NPC names; "
               f"next free custom id {info['free']:#x}")
    names = dict((local, n) for n, local in info["names"])
    while True:
        raw = click.prompt("\n  NPC id to rename (hex like 0x2A or decimal), or 'new'", default="new").strip()
        if raw.lower() == "new":
            taken = {n["id"] for n in npcs if isinstance(n.get("id"), int)}
            local = info["free"]
            while local in taken or local in names:
                local += 1
            name = click.prompt("  name")
            model = click.prompt("  model id (a placed entity model — xi model json --free)", type=int)
            status = click.prompt("  status (0 normal, 2 hidden, 3 invisible, 6 cutscene only)", type=int,
                                  default=CN.NPC_STATUS_CUTSCENE_ONLY)
            server = {"model": model, "status": status}
            if status != CN.NPC_STATUS_CUTSCENE_ONLY:
                pos = click.prompt("  position x y z", default="0 0 0")
                server["pos"] = [float(v) for v in pos.split()[:3]]
                server["rot"] = click.prompt("  rotation (0-255)", type=int, default=0)
            npc = {"id": local, "new": True, "name": name, "server": server}
            click.echo(f"  new NPC {local:#x} ({CN.make_npcid(zone, local):#010x})")
        else:
            local = int(raw, 0)
            if local not in names:
                click.echo(click.style(f"  zone {zone} has no NPC {local:#x}", fg="yellow"))
                continue
            click.echo(f"  {local:#x}: {names[local]!r}")
            name = click.prompt("  new name", default=names[local])
            npc = {"id": local, "name": name}
        errs = ZN.validate_action({"id": action["id"], "type": "zone_npcs", "zone": zone, "npcs": [npc]})
        if errs:
            for e in errs:
                click.echo(click.style(f"  ⚠ {e}", fg="yellow"))
        else:
            at = next((i for i, n in enumerate(npcs) if n.get("id") == npc["id"]), None)
            if at is None:
                npcs.append(npc)
            else:
                npcs[at] = npc
        if not click.confirm("\n>> Another NPC?", default=False):
            break
    action["npcs"] = npcs
    return action


def _wizard_zone_events(slug: str, prev: dict | None, pivot: bool) -> dict:
    """`dats new` for a zone's events: the zone, then a cutscene file (xi.cutscene.v1, from
    the zone editor or `xi event decompile`) or a plain dialogue on an NPC."""
    from xi.event import xi_cutscene_publish as CP
    from xi.event import xi_zone_events as ZE
    root = _target_root("pivot" if pivot else "dir")
    action = dict(prev) if prev else {"id": f"zone_events.{slug}", "type": "zone_events", "events": []}
    zone = _ask("Which zone? (the zone id — xi zone search <name>)", type=int, default=action.get("zone"))
    if zone != action.get("zone"):
        action["events"] = []
    action["zone"] = zone
    events = list(action.get("events") or [])
    try:
        info = ZE.describe(root, zone)
    except ZE.ZoneEventsError as e:
        raise click.ClickException(str(e))
    with_events = [a for a in info["actors"] if a[2]]
    click.echo(f"\n  zone {zone}: {info['dat']}, {len(info['actors'])} blocks, {len(with_events)} NPCs with events")
    while True:
        kind = _choose("Add a cutscene or a dialogue?", ["cutscene", "dialogue"], default="cutscene")
        if kind == "cutscene":
            path = _prompt_existing_dat("Cutscene file (xi.cutscene.v1 JSON)", "Enter path")
            ev = {"cutscene": str(path.resolve())}
            try:
                cs, _src = ZE.load_cutscene(str(path.resolve()), lambda r: Path(r))
            except ZE.ZoneEventsError as e:
                click.echo(click.style(f"  ⚠ {e}", fg="yellow"))
                continue
            name = click.prompt("  name (what the build records it by)", default=path.stem)
            ev["name"] = name
            if CP.has_camera_track(cs):
                place = click.prompt("  camera scene DAT (where it goes, like ROM10/490/55.DAT)",
                                     default=CP.camera_dat_rel(cs) or "")
                ev["camera"] = place.replace("\\", "/")
        else:
            actor = click.prompt("  NPC entity id (0x01… — xi event dialogue actors <zone>)")
            lines = []
            click.echo("  lines (an empty one ends):")
            while True:
                ln = click.prompt("   ", default="", show_default=False)
                if not ln:
                    break
                lines.append(ln)
            name = click.prompt("  name (what the build records it by)")
            ev = {"name": name, "dialogue": {"actor": actor, "lines": lines,
                                              "paged": click.confirm("  one paged box?", default=False)}}
        errs = ZE.validate_action({"id": action["id"], "type": "zone_events", "zone": zone, "events": [ev]})
        if errs:
            for e in errs:
                click.echo(click.style(f"  ⚠ {e}", fg="yellow"))
        else:
            at = next((i for i, e in enumerate(events) if ZE.event_key(e) == ZE.event_key(ev)), None)
            if at is None:
                events.append(ev)
            else:
                events[at] = ev
        if not click.confirm("\n>> Another event?", default=False):
            break
    action["events"] = events
    return action


def _mix_files(folder: Path) -> list[Path]:
    """Mix files in ``folder`` and one level down: ``*.mix.json`` and the legacy
    ``*.recipe.json``, the ``.mix.json`` winning when both exist for one name."""
    if not folder.is_dir():
        return []
    found: dict = {}
    for pattern in ("*.recipe.json", "*/*.recipe.json", "*.mix.json", "*/*.mix.json"):
        for p in folder.glob(pattern):
            stem = p.name[:-len(".mix.json")] if p.name.endswith(".mix.json") else p.name[:-len(".recipe.json")]
            found[(p.parent, stem.lower())] = p       # .mix.json globbed last, so it wins
    return sorted(found.values())


def _wizard_ability(slug: str, prev: dict | None, manifest_path: Path, manifest: dict) -> dict:
    """`dats new` → Ability: pick a recipe, how to publish it and (optionally) the
    animation number; the build allocates the rest."""
    from xi.ability.xi_compose import load_recipe
    from xi.ability.xi_publish import KINDS, infer_kind
    p = prev or {}
    resource_root = _resource_root(manifest_path, manifest)
    # Mix files the mixer and `xi ability recipe --out` leave under exports/ability.
    found = _mix_files(Path("exports/ability"))
    click.echo("\n>> Which mix? (a .mix.json — formerly .recipe.json — from the Ability Mixer "
               "or `xi ability recipe --out`)")
    if found:
        click.echo("   Found:")
        for i, f in enumerate(found[:20], 1):
            click.echo(f"     {i}. {f}")
        click.echo("   Enter a number, or type a path.")
    click.echo()
    default = (p.get("resources") or {}).get("recipe")
    if default:
        default = str(resource_root / default)
    while True:
        raw = click.prompt("Enter recipe", default=default or "", show_default=bool(default)).strip().strip('"')
        if raw.isdigit() and found and 1 <= int(raw) <= len(found[:20]):
            src = found[int(raw) - 1]
        else:
            src = Path(os.path.expanduser(raw)) if raw else None
        if src and src.is_file():
            try:
                recipe = load_recipe(src)
                break
            except click.ClickException as e:
                click.echo(f"  {e}")
                continue
        click.echo(f"  Not a file: {raw!r}")
    lanes = ", ".join(f"{k}: {v.get('spec') if isinstance(v, dict) else v}" for k, v in recipe["sources"].items())
    click.echo(f"\n   {recipe['name']}: {len(recipe['events'])} events — {lanes}")
    try:
        inferred = infer_kind(recipe)
    except click.ClickException:
        inferred = "ws"
    labels = {"auto": f"Auto (the recipe says: {inferred})", "ja": "Job ability (file_id 4412 + animation)",
              "spell": "Spell (file_id 0xAF0 + animation)", "ws": "Weapon skill (per-race extended slot)"}
    prev_kind = p.get("kind") or "auto"
    kind = next(k for k, lbl in labels.items()
                if lbl == _choose("Publish as", list(labels.values()), default=labels.get(prev_kind)))
    if kind != "auto" and kind not in KINDS:
        kind = "auto"
    prev_anim = (p.get("target") or {}).get("animation", "auto")
    anim_raw = _ask("Animation number (the server row's `animation`; auto = the next free one)",
                    "Enter number or auto", default=str(prev_anim)).strip().lower()
    animation = None if anim_raw in ("", "auto") else int(anim_raw)
    animation_from = None
    if animation is None:
        prev_from = (p.get("target") or {}).get("animation_from")
        from_raw = _ask("Start auto at (the first free number at or above it; any = the first free of all)",
                        "Enter number or any", default=str(prev_from if prev_from is not None else "any")).strip().lower()
        animation_from = None if from_raw in ("", "any") else int(from_raw)
    subdir = _ask("ROM10 folder to place the DAT(s) in", "Enter folder number", type=int,
                  default=int((p.get("target") or {}).get("subdir", ABILITY_DEFAULT_SUBDIR)))
    action = _ability_action_from_recipe(src, resource_root, action_id=f"ability.{slug}",
                                         kind=kind, animation=animation, subdir=subdir,
                                         animation_from=animation_from)
    if p.get("result"):
        action["result"] = p["result"]   # keep the slot a previous build landed on
    return action


def _gear_expand_max() -> int | None:
    """The per-(race,slot) gear model_id window this install supports, or None if
    gear was never expanded (custom gear file_ids unaddressable). Read from
    FFXiMain.dll's patched gear group tables — the client's own model_id→file_id
    map, so it can't disagree with what the game will do — NOT from
    gear_inject_state.json, a machine-local sidecar that never ships with a
    distributed install (real expanded installs looked unexpanded without it)."""
    from xi.gear.xi_inject import dll_expand_max
    return dll_expand_max()


def _gear_tables_status() -> str:
    """Multi-line sizing report of the base install's gear addressing, for errors
    and warnings — so 'not expanded' always comes with the actual numbers."""
    from xi.gear.xi_inject import gear_ftable_target, DEFAULT_MAX_MODELID
    from xi.ftable.xi_expand import RETAIL_ENTRIES
    root = _target_root("dir")
    ft = root / "FTABLE.DAT"
    entries = _ftable_entries(ft)
    size_note = ("missing" if entries == 0 else
                 "retail size" if entries <= RETAIL_ENTRIES else
                 f"retail +{entries - RETAIL_ENTRIES:,}")
    lines = [f"FTABLE: {entries:,} entries ({size_note}) — {ft}"]
    max_model = _gear_expand_max()
    if max_model is None:
        need = gear_ftable_target(DEFAULT_MAX_MODELID)
        lines.append(f"FFXiMain.dll gear window: none — retail DLL, or a client update "
                     f"reverted the patch ({root / 'FFXiMain.dll'})")
        lines.append(f"`xi ftable expand gear` patches the DLL and grows the FTABLE "
                     f"to {need:,} entries (default window {DEFAULT_MAX_MODELID})")
    else:
        need = gear_ftable_target(max_model)
        ok = "OK" if entries >= need else "FTABLE TOO SMALL"
        lines.append(f"FFXiMain.dll gear window: model ids to {max_model} per slot "
                     f"→ needs {need:,} FTABLE entries ({ok})")
    return "\n  ".join(lines)


def _build_gear(action: dict, manifest_path: Path, manifest: dict, force: bool = False,
                dry_run: bool = False) -> dict:
    """Place one prebuilt gear DAT per race verbatim, registering each at the
    windowed custom gear file_id for (race, slot, model_id)."""
    from xi.gear.xi_inject import custom_fid, RACES

    slot = action.get("slot")
    model = action.get("model", {})
    targets = action.get("targets") or []
    if slot not in GEAR_SLOTS:
        raise click.ClickException(f"{action.get('id')}: gear action needs a valid slot (got {slot!r}).")
    if model.get("model_id") is None:
        raise click.ClickException(f"{action.get('id')}: gear action needs model.model_id.")
    if not targets:
        raise click.ClickException(f"{action.get('id')}: gear action has no per-race targets.")

    max_model = _gear_expand_max()
    if max_model is None:
        raise click.ClickException(
            f"{action.get('id')}: custom gear tables are not expanded — custom gear "
            "file_ids have nowhere to register.\n"
            f"  {_gear_tables_status()}\n"
            "Run `xi ftable expand gear` on this install, then re-run the build.")

    model_id = int(model["model_id"])
    placed_races, file_ids, first_dat = [], [], None
    for t in targets:
        race = t.get("race")
        if race not in RACES:
            raise click.ClickException(f"{action.get('id')}: unknown race {race!r}.")
        if not t.get("raw_dat") or not t.get("dat"):
            raise click.ClickException(f"{action.get('id')}: gear target for {race} needs raw_dat + dat.")
        src = _resolve_raw_source(t["raw_dat"], manifest_path, manifest)
        file_id = custom_fid(race, slot, model_id, max_model)
        res = _place_raw_dat_in_build(src, t["dat"], file_id, force=force,
                                      action_id=f"{action['id']}:{race}", dry_run=dry_run)
        flag = "  ⚠ occupied by " + res["occupied_by"] if res.get("occupied_by") else ""
        placed_races.append(f"{race} -> {res['target_dat']} (file_id {file_id}){flag}")
        file_ids.append(file_id)
        first_dat = first_dat or res["target_dat"]

    return {"id": action["id"], "type": "gear", "slot": slot, "model_id": model_id,
            "file_id": file_ids[0] if file_ids else None, "target_dat": first_dat,
            "races": placed_races}


# ── database: edits to the client's record tables (xi.database) ─────────────────

def _action_file(action: dict, manifest_path: Path, manifest: dict) -> Path:
    """The file an action is written in: its include file, else the project file."""
    owner = ((manifest.get(LAYOUT_KEY) or {}).get("owners") or {}).get(action.get("id"))
    return Path(owner) if owner else manifest_path


def _build_database(action: dict, manifest_path: Path, manifest: dict, force: bool = False,
                    dry_run: bool = False, unwound: bool = False) -> dict:
    from xi.database import xi_build as DB
    root = _active_build_root()
    sql_path = DB.sql_path(action, _action_file(action, manifest_path, manifest), manifest_path)
    try:
        return DB.build(action, root=root, target=_root_target_name(root) or "dir", manifest=manifest,
                        sql_path=sql_path, project=manifest.get("name") or manifest_path.stem,
                        force=force, dry_run=dry_run, unwound=unwound)
    except DB.DbError as e:
        raise click.ClickException(f"{action.get('id')}: {e}")


def _build_zone_dialog(action: dict, manifest_path: Path, manifest: dict, force: bool = False,
                       dry_run: bool = False, unwound: bool = False) -> dict:
    from xi.dialog import xi_zone_dialog as ZD
    root = _active_build_root()
    try:
        return ZD.build(action, root=root, target=_root_target_name(root) or "dir", dry_run=dry_run,
                        unwound=unwound)
    except ZD.ZoneDialogError as e:
        raise click.ClickException(f"{action.get('id')}: {e}")


def _build_zone_npcs(action: dict, manifest_path: Path, manifest: dict, force: bool = False,
                     dry_run: bool = False, unwound: bool = False) -> dict:
    from xi.database import xi_build as DB
    from xi.entity import xi_zone_npcs as ZN
    root = _active_build_root()
    sql_path = DB.sql_path(action, _action_file(action, manifest_path, manifest), manifest_path)
    try:
        return ZN.build(action, root=root, target=_root_target_name(root) or "dir", manifest=manifest,
                        sql_path=sql_path, project=manifest.get("name") or manifest_path.stem,
                        force=force, dry_run=dry_run, unwound=unwound)
    except ZN.ZoneNpcsError as e:
        raise click.ClickException(f"{action.get('id')}: {e}")


def _lua_path(action: dict, action_file: Path, project_file: Path) -> Path | None:
    """Where the action's proposed Lua goes: ``server.lua`` relative to the file holding the
    action, else ``<project>.lua`` beside the project file. None when ``server.emit`` is false."""
    server = action.get("server") or {}
    if not server.get("emit", True):
        return None
    if server.get("lua"):
        return Path(action_file).parent / server["lua"]
    return Path(project_file).with_suffix(".lua")


_LUA_HEAD = ("-- Proposed by `xi dats build {project}`. xi-tools never runs this file: each section is the\n"
             "-- server script that starts an event; paste it into the NPC's script under scripts/zones.\n")


def _build_zone_events(action: dict, manifest_path: Path, manifest: dict, force: bool = False,
                       dry_run: bool = False, unwound: bool = False) -> dict:
    """Compile the action's events into the zone (xi.event.xi_zone_events), place each camera
    scene DAT like any DAT an action places, and write the server's part beside the project."""
    from xi.database import xi_build as DB
    from xi.event import xi_zone_events as ZE
    root = _active_build_root()
    target = _root_target_name(root) or "dir"
    action_file = _action_file(action, manifest_path, manifest)
    project = manifest.get("name") or manifest_path.stem

    def resolve(ref: str) -> Path:
        p = Path(ref)
        if p.is_absolute():
            if p.is_file():
                return p
            raise ZE.ZoneEventsError(f"{ref}: no such file")
        for base in (action_file.parent, _resource_root(manifest_path, manifest), manifest_path.parent, Path.cwd()):
            if (base / p).is_file():
                return base / p
        raise ZE.ZoneEventsError(f"{ref}: not found beside {action_file.name}, under the project's "
                                 "resources or here")

    try:
        built = ZE.build(action, root=root, target=target, resolve=resolve, manifest=manifest, project=project,
                         force=force, dry_run=dry_run, unwound=unwound)
    except ZE.ZoneEventsError as e:
        raise click.ClickException(f"{action.get('id')}: {e}")
    # Each camera scene DAT is placed and registered like a verbatim DAT; one a previous build
    # placed that this one no longer does comes out again.
    placed = []
    for cam in built["cameras"]:
        rom = _parse_rom_placement(_rom_rel(cam["dat"]))[0]
        if rom != 1 and not all((root / f"ROM{rom}" / f"{t}TABLE{rom}.DAT").is_file() for t in "FV"):
            # What the build's registration would stop on, said by a dry run too.
            raise click.ClickException(
                f"{action['id']}: {root} has no ROM{rom} tables, so camera scene {cam['file_id']} "
                f"({cam['dat']}) can't be registered there. Run `xi ftable expand` on it, or place "
                "the camera under a ROM that has them.")
        placed.append(_place_raw_dat_in_build(None, cam["dat"], cam["file_id"], force=force,
                                              action_id=action["id"], dry_run=dry_run, data=cam.pop("data")))
    now = {(c["file_id"], _rom_rel(c["dat"]).upper()) for c in built["cameras"]}
    dropped = []
    for rec in (((action.get("result") or {}).get("roots") or {}).get(target)) or []:
        cam = rec.get("camera")
        if isinstance(cam, dict) and (cam["file_id"], _rom_rel(cam["dat"]).upper()) not in now:
            dropped.append(cam)
            if not dry_run:
                p = root / Path(*_rom_rel(cam["dat"]).split("/"))
                if p.is_file() and p.read_bytes()[:4] == b"evte":
                    p.unlink()
                _unregister_file_id(root, cam["file_id"], _parse_rom_placement(_rom_rel(cam["dat"]))[0])
    built["placements"], built["dropped"] = placed, dropped
    sql_path = DB.sql_path(action, action_file, manifest_path)
    lua_path = _lua_path(action, action_file, manifest_path)
    if not dry_run:
        for path, text, head in ((sql_path, built["sql"], None), (lua_path, built["lua"], _LUA_HEAD)):
            if path is None:
                continue
            if text:
                DB.write_sql_section(path, action["id"], text, project, head=head)
            else:
                DB.remove_sql_section(path, action["id"])
    built["server"] = str(sql_path) if built["sql"] and sql_path is not None else None
    built["lua_file"] = str(lua_path) if built["lua"] and lua_path is not None else None
    return built


def _zone_list_action_from_source(kind: str, key: str, source: Path, data, action_id: str | None,
                                  manifest_path: Path, manifest: dict, merge: bool, zone: int | None) -> dict:
    """A zone_dialog / zone_npcs action from a source: a whole action, ``{"zone", key}``, or a
    bare list with --zone. With ``merge`` its entries go into the project's action of the same
    id, replacing an entry of the same id (an "auto" NPC: of the same name)."""
    if kind == "zone_dialog":
        from xi.dialog.xi_zone_dialog import validate_action
    else:
        from xi.entity.xi_zone_npcs import validate_action
    body = {key: data} if isinstance(data, list) else \
        {k: v for k, v in data.items() if k not in ("schema", "$schema", "result")}
    if zone is not None:
        body["zone"] = zone
    if not isinstance(body.get(key), list) or not isinstance(body.get("zone"), int):
        raise click.ClickException(f"{source}: a {kind} source is an action, {{\"zone\", \"{key}\"}}, "
                                   f"or a list of {key} with --zone.")
    action_id = action_id or body.get("id") or f"{kind}.{_slug(source.stem)}"
    action = {"id": action_id, "type": kind, **{k: v for k, v in body.items() if k not in ("id", "type")}}
    existing = next((a for a in manifest.get("actions", []) if a.get("id") == action_id), None)
    if merge and existing:
        def ident(e):
            return ("name", e.get("name")) if e.get("id") == "auto" else ("id", e.get("id"))
        items = list(existing.get(key) or [])
        at = {ident(e): i for i, e in enumerate(items)}
        for e in action[key]:
            if ident(e) in at:
                items[at[ident(e)]] = e
            else:
                items.append(e)
        action = {**existing, **{k: v for k, v in action.items() if k != key}, key: items}
    errs = validate_action(action)
    if errs:
        raise click.ClickException(f"{source}:\n  " + "\n  ".join(errs))
    return action


def _zone_events_action_from_source(source: Path, data, action_id: str | None, manifest_path: Path,
                                    manifest: dict, merge: bool, zone: int | None, camera: str | None,
                                    event_name: str | None) -> tuple[dict, bool]:
    """A zone_events action from a source: a whole action or ``{"zone", "events"}`` (its
    cutscene paths are taken relative to the source), or one cutscene file, which becomes an
    event of the zone's action (``zone_events.<zone>``), replacing one of the same name.
    Returns ``(action, merge)``."""
    from xi.event import xi_zone_events as ZE

    def ref(path: str) -> str:
        # A cutscene the project names: relative to its resources when it lives there, else absolute.
        p = Path(path)
        p = p if p.is_absolute() else (source.parent / p)
        return _relative_to_resources(p, _resource_root(manifest_path, manifest)) if p.exists() else path

    if data.get("schema") in ZE.SCHEMAS:
        z = zone if zone is not None else data.get("zone")
        if not isinstance(z, int):
            raise click.ClickException(f"{source}: the cutscene has no zone field; pass --zone")
        ev = {"name": event_name or source.stem, "cutscene": ref(str(source.resolve()))}
        if camera:
            ev["camera"] = _rom_rel(camera)
        if data.get("eventId") not in (None, "auto"):
            ev["eventId"] = data["eventId"]
        body, merge = {"zone": z, "events": [ev]}, True
        action_id = action_id or next((a.get("id") for a in manifest.get("actions", [])
                                       if a.get("type") == "zone_events" and a.get("zone") == z), None) \
            or f"zone_events.{_zone_slug(z)}"
    else:
        body = {k: v for k, v in data.items() if k not in ("schema", "$schema", "result")}
        if zone is not None:
            body["zone"] = zone
        body["events"] = [({**e, "cutscene": ref(e["cutscene"])} if isinstance(e, dict)
                           and isinstance(e.get("cutscene"), str) else e) for e in body.get("events") or []]
        action_id = action_id or body.get("id") or f"zone_events.{_slug(source.stem)}"
    action = {"id": action_id, "type": "zone_events", **{k: v for k, v in body.items() if k not in ("id", "type")}}
    existing = next((a for a in manifest.get("actions", []) if a.get("id") == action_id), None)
    if merge and existing:
        events = list(existing.get("events") or [])
        at = {ZE.event_key(e): i for i, e in enumerate(events)}
        for e in action["events"]:
            if ZE.event_key(e) in at:
                events[at[ZE.event_key(e)]] = e
            else:
                events.append(e)
        action = {**existing, **{k: v for k, v in action.items() if k != "events"}, "events": events}
    errs = ZE.validate_action(action)
    if errs:
        raise click.ClickException(f"{source}:\n  " + "\n  ".join(errs))
    return action, merge


def _zone_slug(zone: int) -> str:
    """A zone's name as an id part (``lower_jeuno``), else ``zone_<id>``."""
    try:
        from xi.event.xi_event import zone_name_for
        name = zone_name_for(zone)
    except Exception:
        name = None
    return _slug(name) if name else f"zone_{zone}"


def _database_result(built: dict | None, previous: dict | None) -> dict:
    """The inline ``result`` of a database action: per build target, every record it
    wrote with each field's value before and after (what a rebuild and undo start from)."""
    b = built or {}
    roots = dict((previous or {}).get("roots") or {})
    roots[b.get("target") or "dir"] = b.get("records") or []
    out = {"roots": roots, "sql": b.get("server")}
    if b.get("type") == "zone_events":
        out["lua"] = b.get("lua_file")
    return out


def _database_action_from_source(source: Path, data, action_id: str | None, manifest: dict,
                                 merge: bool) -> dict:
    """A database action from a source file: a whole action, ``{"edits": [...]}`` or a bare
    list of edits. With ``merge`` its edits go into the project's action of the same id,
    replacing an edit of the same table and id."""
    from xi.database.xi_core import validate_action
    if isinstance(data, list):
        body: dict = {"edits": data}
    elif isinstance(data, dict) and isinstance(data.get("edits"), list):
        body = {k: v for k, v in data.items() if k not in ("schema", "$schema", "result")}
    else:
        raise click.ClickException(f"{source}: a database source is a database action, "
                                   '{"edits": [...]} or a list of edits.')
    action_id = action_id or body.get("id") or f"database.{_slug(source.stem)}"
    action = {"id": action_id, "type": "database",
              **{k: v for k, v in body.items() if k not in ("id", "type")}}
    existing = next((a for a in manifest.get("actions", []) if a.get("id") == action_id), None)
    if merge and existing:
        edits = list(existing.get("edits") or [])
        at = {(e.get("table"), e.get("id")): i for i, e in enumerate(edits)}
        for e in action["edits"]:
            key = (e.get("table"), e.get("id"))
            if key in at:
                edits[at[key]] = e
            else:
                edits.append(e)
        action = {**existing, **{k: v for k, v in action.items() if k != "edits"}, "edits": edits}
    errs = validate_action(action)
    if errs:
        raise click.ClickException(f"{source}:\n  " + "\n  ".join(errs))
    return action


def _short(value, width: int = 70) -> str:
    s = repr(value)
    return s if len(s) <= width else s[:width - 1] + "…"


@click.group("dats")
def group():
    """Build reproducible DAT package trees from manifest JSON."""
    pass


@group.command("json")
@click.argument("manifest", type=click.Path(path_type=Path), default=DEFAULT_MANIFEST, required=False)
@click.option("--output", "output", type=click.Path(path_type=Path), default=None)
def json_cmd(manifest: Path, output: Path | None):
    """Print a normalized dats manifest as JSON (includes expanded in place)."""
    data = _read_manifest(manifest)
    data.pop(LAYOUT_KEY, None)
    _dump_json(data, output)


@group.command("prepare")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.argument("manifest", type=click.Path(path_type=Path), default=None, required=False)
@click.option("--project", default=None, help="Manifest name — writes to dats/<project>.json instead of dats/update.json.")
@click.option("--id", "action_id", default=None, help="Stable action id to use in the manifest.")
@click.option("--type", "action_type", default=None, help="Force the action type instead of inferring it.")
@click.option("--target", default=None, help="Target ROM DAT path when the source does not contain one.")
@click.option("--hd/--no-hd", default=True, show_default=True, help="For zone actions, also build dats/ffxi-hd output.")
@click.option("--replace", is_flag=True, help="Replace an existing action with the same id.")
@click.option("--kind", "ability_kind", type=click.Choice(["auto", "ja", "spell", "ws"]), default=None,
              help="Ability recipes: publish as a job ability, spell or weapon skill (default auto = from the recipe).")
@click.option("--animation", type=int, default=None,
              help="Ability recipes: the animation number to take (default auto = next free).")
@click.option("--animation-from", type=int, default=None,
              help="Ability recipes: where auto starts — the first free number at or above this.")
@click.option("--subdir", type=int, default=None,
              help=f"Ability recipes: ROM10 folder to place the DAT(s) in (default {ABILITY_DEFAULT_SUBDIR}).")
@click.option("--record-id", type=int, default=None,
              help="Spell / command definitions: the record id to take (default auto = highest free above the retail band).")
@click.option("--menu-index", type=int, default=None,
              help="Spell definitions: the menu slot to sort into (default auto = next after retail's).")
@click.option("--merge", is_flag=True, default=False,
              help="Database edits / zone dialog lines: add them to the project's action of the same id, "
                   "replacing an edit of the same table and id (or a line of the same id); the rest stay.")
@click.option("--zone", "zone_id", type=int, default=None,
              help="Zone dialog lines / zone NPCs / a cutscene: the zone a bare list (or a cutscene with no "
                   "zone field) belongs to.")
@click.option("--camera", default=None,
              help="A cutscene with a camera: where its camera scene DAT goes (like ROM10/490/55.DAT).")
@click.option("--event-name", default=None,
              help="A cutscene: the name its event is recorded by (default: the file's name).")
def prepare_cmd(source: Path, manifest: Path | None, project: str | None, action_id: str | None, action_type: str | None,
                target: str | None, hd: bool, replace: bool,
                ability_kind: str | None = None, animation: int | None = None, subdir: int | None = None,
                record_id: int | None = None, menu_index: int | None = None,
                animation_from: int | None = None, merge: bool = False, zone_id: int | None = None,
                camera: str | None = None, event_name: str | None = None):
    """Add an exported/import JSON, zone-changes.json or ability recipe to a dats package.

    \b
    An ability recipe — a mix file, *.mix.json (formerly *.recipe.json), from `xi ability
    recipe` or the model viewer's Ability Mixer — becomes an `ability` action: it is copied
    to projects/resources/ability/<slug>.mix.json and `dats build` composes it, places the
    DAT(s) in ROM10 and registers the file ids:
      xi dats prepare exports/ability/mixer/tiger_fury.mix.json --project tiger_fury --replace
      xi dats build tiger_fury --dry-run

    \b
    Database edits (schema/database.json) — a database action, {"edits": [...]} or a bare
    list of edits — become a `database` action; --merge adds them to the one already there:
      xi dats prepare edits.json --project gear_tweaks --merge
    """
    manifest = _resolve_manifest_path(manifest, project)
    manifest_data = _read_manifest(manifest)
    if project:
        manifest_data["name"] = project
    kind, data = _detect_source_type(source, action_type)
    resource_root = _resource_root(manifest, manifest_data)

    if kind == "ability":
        action = _ability_action_from_recipe(source, resource_root, action_id=action_id,
                                             kind=ability_kind, animation=animation, subdir=subdir,
                                             animation_from=animation_from)
        action_id = action["id"]
    elif kind in RECORD_TYPES:
        action = _record_action_from_definition(source, resource_root, kind, action_id=action_id,
                                                record_id=record_id, menu_index=menu_index)
        action_id = action["id"]
    elif kind == "database":
        action = _database_action_from_source(source, data, action_id, manifest_data, merge)
        action_id = action["id"]
        replace = replace or merge
    elif kind in ("zone_dialog", "zone_npcs"):
        action = _zone_list_action_from_source(kind, "lines" if kind == "zone_dialog" else "npcs", source, data,
                                               action_id, manifest, manifest_data, merge, zone_id)
        action_id = action["id"]
        replace = replace or merge
    elif kind == "zone_events":
        action, merge = _zone_events_action_from_source(source, data, action_id, manifest, manifest_data, merge,
                                                        zone_id, camera, event_name)
        action_id = action["id"]
        replace = replace or merge
    elif kind == "zone":
        dat_target = _rom_rel(target or data.get("zone", ""))
        if not dat_target:
            raise click.ClickException("Zone prepare needs --target or a 'zone' field in zone-changes.json.")
        action_id = action_id or _action_id_for(source, "zone", dat_target)
        dest_dir = resource_root / "zone" / action_id.removeprefix("zone.")
        changes_dest = _copy_zone_change_resources(source, dest_dir)
        action = {
            "id": action_id,
            "type": "zone",
            "target": {"dat": dat_target},
            "resources": {"changes": _relative_to_resources(changes_dest, resource_root)},
            "options": {"apply_standard": True, "apply_hd": hd, "reset_before_apply": True},
        }
    else:
        action_id = action_id or data.get("id") or _action_id_for(source, kind, target)
        dest_dir = resource_root / kind / action_id.removeprefix(f"{kind}.")
        if isinstance(data.get("resources"), dict):
            action = {k: v for k, v in data.items() if k not in {"schema", "$schema"}}
            action["id"] = action_id
            action["type"] = kind
            action.pop("op", None)  # `op` is not used — the builder dispatches on `type`
            # Mesh resources (the .glb) stay referenced in-place in exports/ rather
            # than being copied into dats/resources/, so a re-export is picked up
            # by the next build without needing to re-`prepare`.
            action["resources"] = _copy_action_resources(data, source, dest_dir, resource_root, copy=kind != "mesh")
            if target:
                # Explicit --target always wins — the schema's own "target"
                # (defaulted to source.dat) must not shadow it via setdefault.
                target_data = dict(action.get("target") or {})
                target_data["dat"] = _rom_rel(target)
                action["target"] = target_data
        else:
            copied = _copy_file(source, dest_dir / source.name)
            action = {
                "id": action_id,
                "type": kind,
                "target": data.get("target", {}) if isinstance(data.get("target"), dict) else {},
                "resources": {"import_json": _relative_to_resources(copied, resource_root)},
            }

    # Stamp the recommended custom model-id for this type so the user has a
    # concrete value to edit. `build` derives the file_id from (kind, model_id).
    if action.get("type") in DEFAULT_MODEL_ID:
        model = dict(action.get("model") or {})
        model.setdefault("kind", _MODEL_KIND[action["type"]])
        model.setdefault("model_id", DEFAULT_MODEL_ID[action["type"]])
        action["model"] = model

    # Interactive authoring (like `dats new`): for the single-target model types,
    # prompt for the target DAT and model id with kind-specific guidance. The model
    # KIND drives the range check (entity 15000+, gear <=4095, mount <=63). Skipped
    # when stdin isn't a TTY (scripted/piped) — then schema/flags/defaults are used.
    # XI_FORCE_INTERACTIVE=1 (set by the xi-tools launcher, whose pipes are not
    # TTYs but can answer prompts) keeps the prompts on anyway.
    _tty = getattr(sys.stdin, "isatty", lambda: False)
    _forced = os.environ.get("XI_FORCE_INTERACTIVE") == "1"
    interactive = action.get("type") in ("mesh", "entity", "mount") and (bool(_tty()) or _forced)
    if interactive:
        existing = None
        if replace:
            existing = next((a for a in manifest_data.get("actions", [])
                             if a.get("id") == action.get("id")), None)
        mk = (action.get("model") or {}).get("kind") or _MODEL_KIND[action["type"]]
        tgt_default = target or (action.get("target") or {}).get("dat") \
            or ((existing or {}).get("target") or {}).get("dat")
        tgt = _prompt_dest_dat("Target DAT? (e.g. ROM10/25/40.DAT)", default=tgt_default)
        action["target"] = {**(action.get("target") or {}), "dat": tgt}

        mid_default = (action.get("model") or {}).get("model_id")
        if replace and existing:
            mid_default = (existing.get("model") or {}).get("model_id", mid_default)
        mid = _prompt_prepare_model_id(mk, default=mid_default)
        action["model"] = {**(action.get("model") or {}), "kind": mk, "model_id": mid}

    # On --replace, keep a hand-edited target/model as-is (e.g. you relocated
    # the build target or bumped model_id after the first `prepare`) rather
    # than clobbering them with the freshly regenerated defaults — unless
    # --target was passed this run, or we just set them interactively, which are
    # explicit relocation requests.
    if interactive:
        preserve = ()
    elif kind == "ability":
        # The recorded allocation (animation number, placements) always survives a
        # re-prepare so a rebuild lands on the same slot; the target block does too
        # unless this run set part of it explicitly.
        explicit = (ability_kind is not None or animation is not None or subdir is not None
                    or animation_from is not None)
        preserve = ("result",) + (() if explicit else ("target", "kind"))
    elif kind in RECORD_TYPES:
        # Same rule: the recorded id survives a re-prepare; the target too unless set here.
        explicit = record_id is not None or menu_index is not None
        preserve = ("result",) + (() if explicit else ("target",))
    elif kind in RESTORABLE_TYPES:
        # What each build changed (the values undo and the next build put back) survives.
        preserve = ("result",)
    else:
        preserve = ("model",) + (() if target else ("target",))
    _add_or_replace_action(manifest_data, action, replace, preserve=preserve)
    _write_manifest(manifest, manifest_data)

    # Show the prepared action and its resolved options (no indentation).
    saved = next((a for a in manifest_data.get("actions", []) if a.get("id") == action["id"]), action)
    click.echo(f"\nPrepared {action['id']} -> {manifest}\n")
    click.echo(json.dumps(saved, ensure_ascii=False, indent=2))
    model = saved.get("model") or {}
    tgt = (saved.get("target") or {}).get("dat")
    click.echo("")
    if tgt:
        click.echo(f"Target DAT: {tgt}")
    if model.get("model_id") is not None:
        fid = _prepare_file_id(saved)
        fid_note = f" -> file_id {fid}" if fid is not None else ""
        click.echo(f"Model ID: {model['model_id']} ({model.get('kind')}){fid_note}")
    if kind in RECORD_TYPES:
        # The id the build will take: an explicit target, else the one a build recorded, else auto.
        from xi.menu.xi_menu_table import client_warning
        rid = (saved.get("target") or {}).get("id")
        if not isinstance(rid, int):
            rid = (saved.get("result") or {}).get("record_id")
        warning = client_warning(kind, rid if isinstance(rid, int) else None)
        if warning:
            click.echo(click.style(f"⚠ {warning}", fg="yellow"))


def _result_rows(manifest_data: dict) -> list[tuple[str, ...]]:
    """Rows for the results table, read from each action's inline ``result``
    (falling back to its definition where a build hasn't recorded one yet)."""
    rows = []
    for action in manifest_data.get("actions", []):
        aid = action.get("id", "?")
        typ = action.get("type", "?")
        res = action.get("result") or {}
        if typ == "zone_events":
            recorded = [(t, e) for t, recs in (res.get("roots") or {}).items() for e in recs]
            for t, e in recorded:
                cam = e.get("camera") or {}
                rows.append((aid, f"{typ} ({t})", e.get("dat") or "-",
                             f"zone {e.get('zone')} event {e.get('event')} on {e.get('actor')} ({e.get('name')})",
                             str(cam.get("file_id", "-"))))
            if not recorded:
                from xi.event.xi_zone_events import event_key
                rows += [(aid, typ, "-", f"zone {action.get('zone')} {event_key(ev)}", "-")
                         for ev in action.get("events") or []]
            continue
        if typ in ("database", "zone_dialog", "zone_npcs"):
            recorded = [(t, e) for t, recs in (res.get("roots") or {}).items() for e in recs]
            for t, e in recorded:
                tag = " new" if e.get("created") else ""
                what = (f"zone {e.get('zone')} line {e.get('id')}" if typ == "zone_dialog" else
                        f"zone {e.get('zone')} NPC {e.get('local', 0):#x}" if typ == "zone_npcs" else
                        f"{e.get('table')} {e.get('id')}")
                lang = f" {e['lang']}" if e.get("lang") else ""
                rows.append((aid, f"{typ} ({t})", e.get("dat") or "-", f"{what}{lang}{tag}", "-"))
            if not recorded:
                todo = ([f"zone {action.get('zone')} line {l.get('id')}" for l in action.get("lines") or []]
                        if typ == "zone_dialog" else
                        [f"zone {action.get('zone')} NPC {n.get('id')}" for n in action.get("npcs") or []]
                        if typ == "zone_npcs" else
                        [f"{e.get('table')} {e.get('id')}" for e in action.get("edits") or []])
                rows += [(aid, typ, "-", w, "-") for w in todo]
            continue
        if typ in RECORD_TYPES:
            rid = res.get("record_id", (action.get("target") or {}).get("id", "auto"))
            rows.append((aid, typ, res.get("dat") or "ROM/118/114.DAT", str(rid), "-"))
            continue
        if typ in ("gear", "ability"):
            placements = res.get("placements") or []
            model_id = res.get("model_id", (action.get("model") or {}).get("model_id"))
            if typ == "ability":
                model_id = res.get("animation", (action.get("target") or {}).get("animation", "auto"))
            for p in placements:
                rows.append((aid, typ, p.get("dat", "-") or "-", str(model_id if model_id is not None else "-"),
                             str(p.get("file_id") if p.get("file_id") is not None else "-")))
            if not placements:
                rows.append((aid, typ, "-", str(model_id if model_id is not None else "-"), "-"))
        else:
            dat = res.get("dat") or (action.get("target") or {}).get("dat") or "-"
            model_id = res.get("model_id")
            if model_id is None:
                model_id = res.get("mount_id")
            if model_id is None:
                model_id = (action.get("model") or {}).get("model_id")
            file_id = res.get("file_id")
            rows.append((aid, typ, dat, str(model_id if model_id is not None else "-"),
                         str(file_id if file_id is not None else "-")))
    return rows


@group.command("changelog")
@click.argument("manifest", type=click.Path(path_type=Path), default=None, required=False)
@click.option("--project", default=None, help="Manifest name — reads dats/<project>.json instead of dats/update.json.")
def changelog_cmd(manifest: Path | None, project: str | None):
    """Show each action's recorded result (model_id -> file_id -> DAT) as a table."""
    manifest = _resolve_manifest_path(manifest, project)
    manifest_data = _read_manifest(manifest)
    rows = _result_rows(manifest_data)
    if not rows:
        click.echo("No actions in manifest.")
        return
    _print_table(("id", "type", "dat", "model_id", "file_id"), rows)


def _action_summary(action: dict) -> str:
    """One-line, human-readable description of an action for the build listing:
    `id - type - model N: source.dat >> target.dat`."""
    parts = [action.get("id", "?"), action.get("type", "?")]
    model_id = (action.get("model") or {}).get("model_id")
    if model_id is not None:
        parts.append(f"model {model_id}")
    line = " - ".join(parts)
    if action.get("type") == "zone_events":
        evs = action.get("events") or []
        from xi.event.xi_zone_events import event_key
        names = [f"{event_key(e) or '?'}{' (dialogue)' if 'dialogue' in e else ''}" for e in evs[:4]]
        line += (f" - zone {action.get('zone')}: {len(evs)} event{'s' if len(evs) != 1 else ''} "
                 f"({', '.join(names)}{' …' if len(evs) > 4 else ''})")
        return line
    if action.get("type") == "zone_npcs":
        npcs = action.get("npcs") or []
        names = [f"{'+' if n.get('new') else ''}{n.get('name') or n.get('id')}" for n in npcs[:5]]
        line += (f" - zone {action.get('zone')}: {len(npcs)} NPC{'s' if len(npcs) != 1 else ''} "
                 f"({', '.join(names)}{' …' if len(npcs) > 5 else ''})")
        return line
    if action.get("type") == "zone_dialog":
        lines = action.get("lines") or []
        ids = [f"{'+' if l.get('new') else ''}{l.get('id')}" for l in lines[:6]]
        line += (f" - zone {action.get('zone')}: {len(lines)} line{'s' if len(lines) != 1 else ''} "
                 f"({', '.join(ids)}{' …' if len(lines) > 6 else ''})")
        return line
    if action.get("type") == "database":
        edits = action.get("edits") or []
        names = [f"{e.get('table')} {e.get('id')}" for e in edits[:4]]
        line += f" - {len(edits)} edit{'s' if len(edits) != 1 else ''}: {', '.join(names)}"
        return line + (" …" if len(edits) > 4 else "")
    if action.get("type") in RECORD_TYPES:
        rid = (action.get("result") or {}).get("record_id", (action.get("target") or {}).get("id", "auto"))
        line += f" - id {rid}: {(action.get('resources') or {}).get('definition', '?')}"
        return line
    if action.get("type") == "ability":
        anim = (action.get("target") or {}).get("animation", "auto")
        start = (action.get("target") or {}).get("animation_from")
        if anim == "auto" and start is not None:
            anim = f"auto from {start}"
        placements = (action.get("result") or {}).get("placements") or []
        line += f" - {action.get('kind') or 'auto'} animation {anim}"
        line += f": {(action.get('resources') or {}).get('recipe', '?')}"
        if placements:
            line += f" ({len(placements)} DAT{'s' if len(placements) != 1 else ''})"
        return line
    # Gear expands to one DAT per race — make the count explicit so "Actions: 1"
    # doesn't read as "one DAT".
    targets = action.get("targets")
    if action.get("type") == "gear" and isinstance(targets, list):
        line += f": {len(targets)} DATs ({targets[0]['dat']} … {targets[-1]['dat']})" if targets else ""
        return line
    src = (action.get("source") or {}).get("dat")
    tgt = (action.get("target") or {}).get("dat")
    if src and tgt and src != tgt:
        line += f": {src} >> {tgt}"
    elif tgt:
        line += f": {tgt}"
    return line


def _mesh_options_summary(options: dict) -> str:
    sided = "Double Sided" if options.get("double_sided", True) else "Single Sided"
    scale = options.get("scale", 1.0)
    mirror = "Unmirrored" if options.get("unmirror") else "Mirrored"
    return f"{sided}, Scale = {scale}, {mirror}"


def _list_glb_textures(mesh_path: Path) -> list[tuple[str, str, str]]:
    """(texture name, format, "WxH") for each material's baseColorTexture in a
    glTF/glb mesh, sniffed straight from the embedded/sibling image bytes."""
    import tempfile
    from PIL import Image
    from xi.entity.mesh.xi_import import load_gltf_document, _texture_name_for_material, _image_source_path

    doc, buffers = load_gltf_document(mesh_path)
    out: list[tuple[str, str, str]] = []
    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        for material in doc.get("materials", []):
            bct = material.get("pbrMetallicRoughness", {}).get("baseColorTexture")
            if bct is None:
                continue
            image = doc["images"][doc["textures"][bct["index"]]["source"]]
            name = _texture_name_for_material(material, image)
            src = _image_source_path(doc, buffers, mesh_path, image, tmp_dir, name)
            if src is None or not src.is_file():
                continue
            fmt = src.suffix.lstrip(".").lower() or "?"
            try:
                with Image.open(src) as im:
                    size = f"{im.width}x{im.height}"
            except Exception:
                size = "?"
            out.append((name.strip(), fmt, size))
    return out


@group.command("build")
@click.argument("manifest", type=click.Path(path_type=Path), default=None, required=False)
@click.option("--project", default=None, help="Manifest name — builds dats/<project>.json instead of dats/update.json.")
@click.option("--only", "only", multiple=True, help="Build only these action ids (repeatable).")
@click.option("--verbose", is_flag=True, default=False, help="Print options/resource/texture detail under each action.")
@click.option("--force", is_flag=True, default=False,
              help="Allow a file_id collision with another project's placement, or target.dat == source.dat.")
@click.option("--dry-run", is_flag=True, default=False,
              help="Preview only: show what's detected and where each DAT/file_id would land, "
                   "without writing any files or patching tables.")
@click.option("--dry-note/--no-dry-note", default=True, hidden=True,
              help="Print the trailing 'Dry run — nothing written' note (the wizard suppresses it).")
@click.option("--pivot", is_flag=True, default=False,
              help="Build into FFXI_PIVOT_DIR instead of the base install (FFXI_DIR).")
@click.option("--apply-db", is_flag=True, default=False,
              help="Database Update: after the DATs are placed, update the server row named after each ability "
                   "(or insert one cloned from --clone-from). With --dry-run: a read-only preview.")
@click.option("--db-row", type=int, default=None,
              help="Confirm, once, the existing row the build found by name (its id). Needs --apply-db.")
@click.option("--clone-from", default=None,
              help="Override the donor a new row / menu record / Lua stub clones: an id or a server name "
                   "(fire, fast_blade). Default: the kind's donor — cure (spell), berserk (ja), fast_blade (ws).")
@click.option("--server-id", type=int, default=None,
              help="Server id for an insert / a menu record with no row yet (default: the highest blank one).")
@click.option("--menu-record", is_flag=True, default=False,
              help="Client Menu Record: place the client spell/command record at the row's id (ROM/118/114.DAT + "
                   "names). Finds the row with SELECTs only when a server is configured.")
@click.option("--menu-name", default=None,
              help="Name the menu shows (default: the mix name, _ as a space). No control characters or line breaks.")
@click.option("--lua-stub", is_flag=True, default=False,
              help="Lua Stub: write the server script for a row this mix created into XI_SERVER_DIR/scripts/actions.")
def build_cmd(manifest: Path | None, project: str | None, only: tuple[str, ...], verbose: bool,
              force: bool, dry_run: bool, dry_note: bool = True, pivot: bool = False,
              apply_db: bool = False, db_row: int | None = None, clone_from: str | None = None,
              server_id: int | None = None, menu_record: bool = False, menu_name: str | None = None,
              lua_stub: bool = False):
    """Build a manifest into the base install (FFXI_DIR), or FFXI_PIVOT_DIR with --pivot.

    DATs are placed and their file_ids registered straight into the target's tables
    (which must already exist + be expanded); in the base install the tables are backed
    up once to `<name>.base` before the first patch. A base-install build then syncs the
    custom region of FFXI_PIVOT_DIR's tables so the sizes match; a --pivot build
    registers in that folder's own tables and skips the sync. Each action records the
    target it was built into, which `undo`, `package` and `release` follow.

    \b
    For ability actions, once the DATs are placed (docs/ability/mixer.md):
      --apply-db     update / insert the server row (xi-tools' .env XI_DB_*)
      --menu-record  the client menu record at the same id (a blank retail row only)
      --lua-stub     the server script for a row this mix created (XI_SERVER_DIR)
    Their outcome never fails the build: each prints a db: / menu: / lua: line.
    """
    import xi.xi_config as cfg
    from xi.server import xi_server_pass as SP
    opts = SP.ServerOpts(apply_db=apply_db, db_row=db_row,
                         clone_from=(clone_from.strip() or None) if isinstance(clone_from, str) else None,
                         server_id=server_id, menu_record=menu_record, menu_name=menu_name, lua_stub=lua_stub)

    manifest = _resolve_manifest_path(manifest, project)
    if not manifest.exists():
        raise click.ClickException(
            f"No manifest at {manifest}. Pass an existing project "
            f"(e.g. `dats build {manifest.stem}` → dats/{manifest.stem}.json) "
            f"or a path to a manifest .json.")

    manifest_data = _read_manifest(manifest)
    standard_root = _root_path(manifest, manifest_data, "standard")
    hd_root = _root_path(manifest, manifest_data, "hd")
    redirect_root = cfg._REDIRECT_DIR   # normally None (writes go in place)

    selected = set(only)
    active_actions = [a for a in manifest_data.get("actions", [])
                      if a.get("enabled", True) and (not selected or a.get("id") in selected)]

    click.echo()
    verb = "Previewing" if dry_run else "Building"
    click.echo(f"{verb}: {manifest_data.get('name', 'DAT')} build")
    # standard_root (dats/ffxi) and hd_root (dats/ffxi-hd) are only for
    # zone actions; skip both entirely when the manifest has no zone action.
    zone_actions = [a for a in active_actions if a.get("type") == "zone"]
    if zone_actions:
        standard_root.mkdir(parents=True, exist_ok=True)
        copied_tables = _copy_tables_to_package(standard_root, redirect_root)
        _ensure_rom_tables(standard_root, 10)
        click.echo(f"Copied {len(copied_tables)} f/v tables -> {standard_root}")

    # mesh + the verbatim-placement types write DATs + table patches directly into the
    # target (the base install, or FFXI_PIVOT_DIR with --pivot); menu records edit its
    # 114.DAT and string tables.
    pack_actions = [a for a in active_actions
                    if a.get("type") in ("mesh", "entity", "gear", "mount", "ability")]
    target = "pivot" if pivot else "dir"
    target_roots = [(target, _target_root(target))]
    if pivot and not target_roots[0][1].is_dir():
        raise click.ClickException(f"FFXI_PIVOT_DIR does not exist: {target_roots[0][1]}")
    if any(a.get("type") != "zone" for a in active_actions):
        from xi.xi_config import CUSTOM_ROM_IDX
        n_with_tables = 0
        for name, root in target_roots:
            # A ROM{n} pair alone registers file_ids (the client honours it over the main
            # pair, and it is all a pivot folder can register through).
            main_entries = _ftable_entries(root / "FTABLE.DAT")
            rom_entries = _ftable_entries(root / f"ROM{CUSTOM_ROM_IDX}" / f"FTABLE{CUSTOM_ROM_IDX}.DAT")
            has_tables = max(main_entries, rom_entries) > 0
            n_with_tables += has_tables
            suffix = ""
            if pack_actions and not has_tables:
                suffix = "  (DAT-only — no FTABLE here)"
            elif pack_actions and main_entries == 0:
                suffix = f"  (ROM{CUSTOM_ROM_IDX} tables only)"
            click.echo(f"Target: {_TARGET_LABELS[name]} -> {root}{suffix}")
        if pack_actions and n_with_tables == 0:
            raise click.ClickException(
                "None of the chosen targets have an FTABLE to register the file_ids in — "
                "include a target with tables (pivot or dir).")

    click.echo()
    click.echo(f"Actions: {len(active_actions)}")
    click.echo()
    for i, action in enumerate(active_actions, 1):
        click.echo(f"{i}. {_action_summary(action)}")
        if verbose and action.get("type") == "mesh":
            options = action.get("options") or {}
            click.echo(f"   * Options: {_mesh_options_summary(options)}")
            mesh_rel = (action.get("resources") or {}).get("mesh")
            if mesh_rel:
                mesh_path = _resource_root(manifest, manifest_data) / mesh_rel
                click.echo(f"   * Resource Mesh: {mesh_path}")
                try:
                    textures = _list_glb_textures(mesh_path)
                except Exception as e:
                    textures = []
                    click.echo(f"     (could not read textures: {e})")
                for ti, (tname, fmt, size) in enumerate(textures, 1):
                    click.echo(f"     - Applying Texture {ti} - {tname} - {fmt} - {size}")
    click.echo()

    def _dispatch(action, kind):
        if kind == "mount":
            return _build_mount(action, manifest, manifest_data, force=force, dry_run=dry_run)
        if kind == "entity":
            return _build_entity(action, manifest, manifest_data, force=force, dry_run=dry_run)
        if kind == "gear":
            return _build_gear(action, manifest, manifest_data, force=force, dry_run=dry_run)
        if kind == "mesh":
            return _build_mesh(action, manifest, manifest_data, force=force, dry_run=dry_run)
        if kind == "ability":
            return _build_ability(action, manifest, manifest_data, force=force, dry_run=dry_run)
        if kind in RECORD_TYPES:
            return _build_record(action, manifest, manifest_data, force=force, dry_run=dry_run)
        # The restorable types: the project's last build of them was put back first (_unwind).
        if kind == "database":
            return _build_database(action, manifest, manifest_data, force=force, dry_run=dry_run, unwound=True)
        if kind == "zone_dialog":
            return _build_zone_dialog(action, manifest, manifest_data, force=force, dry_run=dry_run, unwound=True)
        if kind == "zone_npcs":
            return _build_zone_npcs(action, manifest, manifest_data, force=force, dry_run=dry_run, unwound=True)
        if kind == "zone_events":
            return _build_zone_events(action, manifest, manifest_data, force=force, dry_run=dry_run, unwound=True)
        raise click.ClickException(
            f"{action.get('id')}: build support for type {kind!r} is not implemented yet.")

    results = []
    server_pairs = []           # (action, build result) of the ability actions, for the server pass
    from xi.dats import xi_stage
    with xi_stage.session(dry_run):
        # A dry run holds what it would write, so each step sees the ones before it.
        unwound = _unwind(active_actions, target_roots[:1] if dry_run else target_roots, dry_run)
        for action in active_actions:
            kind = action.get("type")
            if kind == "zone":
                # Zone actions use the standard/hd package roots, not the live target.
                if dry_run:
                    continue  # zone dry-run preview not modelled; skip
                with _package_config(standard_root, hd_root):
                    result = _build_zone(action, manifest, manifest_data, redirect_root)
            else:
                # Place into every selected target (a dry-run just previews once).
                result = None
                for _name, root in (target_roots[:1] if dry_run else target_roots):
                    _set_target_root(root)
                    result = _dispatch(action, kind)
            if unwound.get(action.get("id")) and isinstance(result, dict):
                result["warnings"] = unwound[action["id"]] + list(result.get("warnings") or [])
            results.append(result)
            if kind == "ability" and isinstance(result, dict):
                server_pairs.append((action, result))
            # Record the allocation INLINE on the action (idempotent — overwrites the
            # same key), so the manifest itself is the single source of truth. Track
            # every target the DATs have been built into (union across builds).
            if not dry_run and kind != "zone":
                prior = set((action.get("result") or {}).get("targets") or [])
                built = prior | {name for name, _ in target_roots}
                # An ability's allocation (animation number, per-race placements) is
                # decided by the build against the live tables, not by the definition
                # alone, so it is taken from the build result rather than re-planned.
                if kind == "ability":
                    res = _ability_result(result, action.get("result"))
                elif kind in RECORD_TYPES:
                    res = _record_result(result)   # the id is decided against the live table too
                elif kind in RESTORABLE_TYPES:
                    res = _database_result(result, action.get("result"))
                else:
                    res = _plan_result(action)
                res["targets"] = [n for n in ("pivot", "dir", "hd") if n in built]
                action["result"] = res

    _set_target_root(None)

    # The server pass (Database Update, Client Menu Record, Lua Stub) runs after every DAT
    # is placed. It never raises: each step's outcome is a db: / menu: / lua: line.
    server_kw = dict(root=target_roots[0][1], target=target, manifest_path=manifest,
                     manifest=manifest_data, project=manifest_data.get("name") or manifest.stem)

    if dry_run:
        if opts.any() and server_pairs:
            SP.run(server_pairs, opts, dry_run=True, **server_kw)     # SELECT-only, reads files
        _print_placements(results, "Planned actions:")
        if any(a.get("type") != "zone" for a in active_actions):
            click.echo("\nWould build into: " + ", ".join(str(r) for _n, r in target_roots))
        if dry_note:
            click.echo(click.style("\nDry run — nothing was written. Re-run without --dry-run to build.",
                                   fg="cyan"))
        return

    # Persist the inline results back into the manifest (self-describing, idempotent).
    _write_manifest(manifest, manifest_data)

    # A client that loads FFXI_PIVOT_DIR through XIPivot reads that folder's ROM{n}
    # tables in place of the install's (never its main FTABLE/VTABLE). Propagate the
    # custom region (the gear/entity file_ids just registered) into each table the
    # pivot folder carries, so those models resolve there too and the sizes stay
    # uniform. Retail-range pivot entries are kept — so a job ability, spell or weapon
    # skill registered only in the install stays invisible to that client; those are
    # built with --pivot. A --pivot build registered in the pivot's own tables, which a
    # copy of the base install's region would overwrite, so it skips this.
    if pack_actions and not pivot:
        from xi.ftable.xi_expand import sync_pivot_from_base, pivot_root
        if pivot_root():
            click.echo(f"\nSyncing pivot overlay tables ({pivot_root()}) ...")
            synced = sync_pivot_from_base()
            click.echo(f"  synced {len(synced)} pivot table(s)" if synced
                       else "  (pivot tables already in sync)")

    if opts.any() and server_pairs:
        changed = SP.run(server_pairs, opts, dry_run=False, **server_kw)
        if changed:
            # The placements were recorded above, so a manifest that cannot be written now
            # only loses what the pass did — which each step that wrote something says.
            try:
                _write_manifest(manifest, manifest_data)
            except Exception as e:                  # noqa: BLE001 — the DATs are placed: exit 0
                SP.warn_unrecorded(results, manifest, e)

    click.echo()
    if results:
        _print_placements(results, "Placed DATs:")
    for _name, root in target_roots:
        click.echo(click.style(f"\n✓ Built into {root}", fg="green"))
    db_line = SP.database_line(results)
    if db_line:
        click.echo(db_line)


RESTORABLE_TYPES = ("database", "zone_dialog", "zone_npcs", "zone_events")


def _restorable_module(kind: str):
    """The library of a restorable type: its ``undo(action, root, target, dry_run)``."""
    if kind == "database":
        from xi.database import xi_build as mod
    elif kind == "zone_dialog":
        from xi.dialog import xi_zone_dialog as mod
    elif kind == "zone_npcs":
        from xi.entity import xi_zone_npcs as mod
    else:
        from xi.event import xi_zone_events as mod
    return mod


def _unwind(actions: list[dict], roots, dry_run: bool) -> dict[str, list[str]]:
    """Put back what the last build of each restorable action changed, newest first, before
    any of them is applied again: a stack taken down in order. Every action then gets back
    the tables it changed, even where several change one table (two that add a zone's
    dialog lines, events on one NPC). Returns each action's warnings by id."""
    out: dict[str, list[str]] = {}
    for name, root in roots:
        for action in reversed([a for a in actions if a.get("type") in RESTORABLE_TYPES]):
            if not (((action.get("result") or {}).get("roots") or {}).get(name)):
                continue
            _n, warns = _restorable_module(action["type"]).undo(action, root, name, dry_run=dry_run)
            out.setdefault(action["id"], []).extend(warns)
    return out


def _project_built_targets(manifest_data: dict) -> list[str]:
    """Targets any of the project's actions have been built into (union), in
    canonical order — read from each action's recorded ``result.targets``."""
    built: set[str] = set()
    for a in manifest_data.get("actions", []):
        if _undone(a):
            continue            # its DATs were cleared by an earlier undo that kept the manifest
        built.update((a.get("result") or {}).get("targets") or [])
    return [n for n in ("pivot", "dir", "hd") if n in built]


def _undone(action: dict) -> bool:
    """An action a `dats undo` already cleared (DATs, table entries, menu records) but kept
    in the manifest because a database row or Lua stub it made is still there."""
    return bool((action.get("result") or {}).get("undone"))


def _action_placements(action: dict) -> list[tuple[int, str]]:
    """(file_id, ROM-relative DAT) for each thing an action placed, from its
    inline result — one per race for gear, one otherwise."""
    res = action.get("result") or {}
    out: list[tuple[int, str]] = []
    if action.get("type") in ("gear", "ability"):
        for p in res.get("placements", []):
            if p.get("file_id") is not None and p.get("dat"):
                out.append((int(p["file_id"]), _rom_rel(p["dat"])))
    elif action.get("type") == "zone_events":
        # Each camera scene DAT an event placed (the event and dialog tables are restored).
        for recs in (res.get("roots") or {}).values():
            for rec in recs:
                cam = rec.get("camera")
                if isinstance(cam, dict) and (int(cam["file_id"]), _rom_rel(cam["dat"])) not in out:
                    out.append((int(cam["file_id"]), _rom_rel(cam["dat"])))
    elif action.get("type") in RESTORABLE_TYPES:
        pass
    elif res.get("file_id") is not None and res.get("dat"):
        out.append((int(res["file_id"]), _rom_rel(res["dat"])))
    return out


def _unregister_file_id(root: Path, file_id: int, rom: int) -> bool:
    """Clear a file_id's entry (ftval + vtable version -> 0, so it resolves to
    nothing) in the tables a build of ``root`` registers it in
    (``patch_launcher_tables``): the ROM{rom} pair, and in the base install its main
    pair when that holds the id. False when ``root`` has none of them."""
    pairs = []
    if rom != 1:
        pairs.append((root / f"ROM{rom}" / f"FTABLE{rom}.DAT", root / f"ROM{rom}" / f"VTABLE{rom}.DAT"))
    if _root_target_name(root) == "dir":
        pairs.append((root / "FTABLE.DAT", root / "VTABLE.DAT"))
    cleared = False
    for ft, vt in pairs:
        if _table_holds(ft, vt, file_id):
            _patch_raw_table(ft, vt, file_id, 0, 0)
            cleared = True
    return cleared


def _pick_project(verb: str) -> str:
    """List the dats/*.json projects and prompt for one (used when a command's
    project argument is omitted)."""
    names = _existing_project_names()
    if not names:
        raise click.ClickException("No projects found (dats/*.json).")
    click.echo(f"\n>> Which project to {verb}?")
    for i, n in enumerate(names, 1):
        click.echo(f"  {i}. {n}")
    click.echo()
    return names[click.prompt("Enter number", type=click.IntRange(1, len(names))) - 1]


def _action_targets(action: dict) -> list[str]:
    """Targets an action was built into (``result.targets``). A result from before
    targets were recorded counts as the base install."""
    res = action.get("result") or {}
    return res.get("targets") or (["dir"] if res else [])


def _project_dat_rels(manifest_data: dict, target: str | None = None) -> tuple[list[str], bool]:
    """ROM-relative DATs a project's actions produced (from inline results), plus
    whether it has any mount action (whose d_msg string DATs must ship too). With
    ``target``, only the actions built into that target count."""
    rels: list[str] = []
    has_mount = False
    for action in manifest_data.get("actions", []):
        typ = action.get("type")
        res = action.get("result") or {}
        if _undone(action):
            continue
        if target is not None and target not in _action_targets(action):
            continue
        if typ in ("gear", "ability"):
            rels += [_rom_rel(p["dat"]) for p in res.get("placements", []) if p.get("dat")]
            menu = res.get("menu") if typ == "ability" else None
            roots = (menu or {}).get("roots") or {}
            if isinstance(menu, dict) and roots and (target is None or target in roots):
                # The Client Menu Record: the shared menu table and the name/help tables it
                # edited ship with the ability, or players get it with no menu entry.
                from xi.menu.xi_menu_table import string_tables
                rk = menu.get("record_kind") or ("spell" if menu.get("kind") == "spell" else "command")
                rels += [_rom_rel("ROM/118/114.DAT")] + [_rom_rel(s) for s in string_tables(rk)]
        elif typ in RECORD_TYPES:
            # The shared menu table plus the name/help tables the build edited.
            rels += [_rom_rel(res.get("dat") or "ROM/118/114.DAT")]
            rels += [_rom_rel(s) for s in res.get("strings") or []]
        elif typ in RESTORABLE_TYPES:
            # The item DATs / string / dialog / event tables whose records it changed, in each
            # target, and a zone_events action's camera scene DATs.
            for tname, recs in (res.get("roots") or {}).items():
                if target is None or tname == target:
                    rels += [_rom_rel(e["dat"]) for e in recs if e.get("dat")]
                    for e in recs:
                        rels += [_rom_rel(ln["dat"]) for ln in e.get("lines") or [] if ln.get("dat")]
                        if isinstance(e.get("camera"), dict):
                            rels.append(_rom_rel(e["camera"]["dat"]))
        elif res.get("dat"):
            rels.append(_rom_rel(res["dat"]))
        if typ == "mount":
            has_mount = True
    return sorted(set(rels)), has_mount


@group.command("package")
@click.argument("project", required=False, default=None)
@click.option("--from", "source", type=click.Choice(["dir", "pivot", "hd"]), default=None,
              help="Read the built DATs + tables from the base install ('dir'), FFXI_PIVOT_DIR "
                   "('pivot'), or FFXI_HD_DIR ('hd'). Default: where the project was built — "
                   "'pivot' when every build used --pivot, else 'dir'.")
@click.option("--output", "output", type=click.Path(path_type=Path), default=None,
              help="Output zip path (default dats/packages/<project>.zip).")
def package_cmd(project: str | None, source: str | None, output: Path | None):
    """Zip a project's built DATs + F/V tables into a distributable overlay pack.

    With no PROJECT, lists the dats/*.json projects to pick from. Reads from where the
    project was built; collects every DAT the actions built there placed (from each
    action's inline result), the mount string DATs for any mount actions, and the full
    FTABLE/VTABLE set — into a single zip laid out ROM-relative.
    """
    import zipfile

    if not project:
        project = _pick_project("package")
    manifest_path = _resolve_manifest_path(None, project)
    if not manifest_path.exists():
        raise click.ClickException(f"No manifest at {manifest_path}.")
    manifest_data = _read_manifest(manifest_path)
    if source is None:
        source = "pivot" if _project_built_targets(manifest_data) == ["pivot"] else "dir"
    root = _target_root(source)
    if not (root / "FTABLE.DAT").exists():
        raise click.ClickException(f"No FTABLE.DAT under {root}.")

    dat_rels, has_mount = _project_dat_rels(manifest_data, target=source)
    table_rels = [rel.as_posix() for rel in _table_rel_paths() if (root / rel).exists()]
    # Mount name/help/key-item string DATs (shared d_msg tables the mount build edits).
    if has_mount:
        for sub in ("ROM/351", "ROM/175"):
            d = root / Path(*sub.split("/"))
            if d.is_dir():
                dat_rels += [f"{sub}/{f.name}" for f in d.iterdir() if f.suffix.lower() == ".dat"]

    missing = [r for r in dat_rels if not (root / Path(*r.split("/"))).exists()]
    for r in missing:
        click.echo(click.style(f"  ⚠ not in {source} target (build there first?): {r}", fg="yellow"))
    files = sorted(set(table_rels) | {r for r in dat_rels if r not in missing})

    out = output or (Path("projects") / "packages" / f"{_project_slug(project)}.zip")
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            z.write(root / Path(*rel.split("/")), rel)

    n_dat = len([r for r in files if r not in table_rels])
    click.echo(click.style(
        f"✓ Packaged {n_dat} DAT(s) + {len(table_rels)} F/V table file(s) -> {out}", fg="green"))


# Standard FFXI install subpath inside a launcher build folder.
_LAUNCHER_GAME_SUBPATH = Path("Game") / "FINAL FANTASY XI"
# Where XIPivot overlays live inside a launcher build folder. The overlay's own
# folder name is NOT hardcoded — it is taken from FFXI_PIVOT_DIR's leaf, so the
# build always uses the same name the client is configured to load.
_LAUNCHER_PIVOT_PARENT = Path("Ashita") / "polplugins" / "DATs"


@group.command("release")
@click.argument("project", required=False, default=None)
@click.option("--to", "release_root", type=click.Path(path_type=Path), default=None,
              help="Launcher build release folder (prompts if omitted).")
@click.option("--no-dll", is_flag=True, default=False,
              help="Skip FFXiMain.dll (the gear-model patch); copy only DATs + tables.")
def release_cmd(project: str | None, release_root: Path | None, no_dll: bool):
    """Stage a project's built files into a launcher build folder.

    Copies, from the base install, everything the launcher needs to serve this
    project — the project's DATs, the full FTABLE/VTABLE set, and (unless --no-dll)
    the patched FFXiMain.dll — into `<release>\\Game\\FINAL FANTASY XI\\...`, mirroring
    the game's folder layout so the launcher deploys them straight to the client.

    Also stages the pivot folder's synced F/V tables (from FFXI_PIVOT_DIR): the
    client reads its ROM{n} tables over the base install's, so the release must carry
    the same copies or they shadow the base with stale/retail ones. DATs of actions
    built with --pivot are staged from FFXI_PIVOT_DIR into that same pivot folder of
    the release.
    """
    if not project:
        project = _pick_project("release")
    manifest_path = _resolve_manifest_path(None, project)
    if not manifest_path.exists():
        raise click.ClickException(f"No manifest at {manifest_path}.")
    manifest_data = _read_manifest(manifest_path)
    src_root = _target_root("dir")  # builds without --pivot land in the base install

    if release_root is None:
        click.echo("\n>> Launcher build release folder")
        release_root = Path(click.prompt("Enter path").strip().strip('"'))
    dest_base = release_root / _LAUNCHER_GAME_SUBPATH

    # What to ship: the project's DATs (+ mount d_msg), the full F/V table set, and
    # the patched DLL (the model->file_id map the client reads at boot).
    dat_rels, has_mount = _project_dat_rels(manifest_data, target="dir")
    if has_mount:
        for sub in ("ROM/351", "ROM/175"):
            d = src_root / Path(*sub.split("/"))
            if d.is_dir():
                dat_rels += [f"{sub}/{f.name}" for f in d.iterdir() if f.suffix.lower() == ".dat"]
    table_rels = [rel.as_posix() for rel in _table_rel_paths() if (src_root / rel).exists()]
    rels = sorted(set(dat_rels) | set(table_rels))
    # Ask whether to include the patched DLL (needed for custom gear model resolution),
    # unless --no-dll forced it off.
    if not no_dll and (src_root / "FFXiMain.dll").exists():
        if click.confirm("\n>> Copy the patched FFXiMain.dll too? (needed for custom gear models)",
                         default=True):
            rels.append("FFXiMain.dll")

    click.echo(f"\nReleasing {manifest_path.stem} -> {dest_base}")
    copied = 0
    missing = []
    for rel in rels:
        src = src_root / Path(*rel.split("/"))
        if not src.exists():
            missing.append(rel)
            continue
        dst = dest_base / Path(*rel.split("/"))
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    for r in missing:
        click.echo(click.style(f"  ⚠ not in base install (build first?): {r}", fg="yellow"))

    # Also stage the pivot folder's F/V tables. The client reads its ROM{n} tables
    # OVER the base install's (XIPivot; the main FTABLE/VTABLE always come from the
    # install), so the build must carry the same synced/expanded copies or they shadow
    # the base with stale ones. Mirrors the pivot folder layout under
    # <release>\Ashita\polplugins\DATs\catseyexi (created here, full path).
    from xi.xi_config import FFXI_PIVOT_DIR
    pivot_copied = pivot_dats = 0
    pivot_rels, pivot_mount = _project_dat_rels(manifest_data, target="pivot")
    if FFXI_PIVOT_DIR and Path(FFXI_PIVOT_DIR).is_dir():
        piv_src = Path(FFXI_PIVOT_DIR)
        piv_dest = release_root / _LAUNCHER_PIVOT_PARENT / piv_src.name
        for rel in _table_rel_paths():
            s = piv_src / rel
            if not s.exists():
                continue
            d = piv_dest / rel
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
            pivot_copied += 1
        # DATs of actions built with --pivot live in that folder, so they ship in it.
        if pivot_mount:
            for sub in ("ROM/351", "ROM/175"):
                d = piv_src / Path(*sub.split("/"))
                if d.is_dir():
                    pivot_rels += [f"{sub}/{f.name}" for f in d.iterdir() if f.suffix.lower() == ".dat"]
        for rel in sorted(set(pivot_rels)):
            s = piv_src / Path(*rel.split("/"))
            if not s.exists():
                click.echo(click.style(f"  ⚠ not in FFXI_PIVOT_DIR (build with --pivot first?): {rel}",
                                       fg="yellow"))
                continue
            d = piv_dest / Path(*rel.split("/"))
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
            pivot_dats += 1
    elif pivot_rels:
        click.echo(click.style("  ⚠ some actions were built with --pivot, but FFXI_PIVOT_DIR is not "
                               "configured — their DATs are not staged", fg="yellow"))

    n_tables = len([r for r in rels if r in table_rels])
    n_dat = len([r for r in rels if r in dat_rels])
    dll = " + FFXiMain.dll" if (not no_dll and "FFXiMain.dll" in rels) else ""
    pivot = f" + {pivot_copied} pivot table(s)" if pivot_copied else ""
    pivot += f" + {pivot_dats} pivot DAT(s)" if pivot_dats else ""
    click.echo(click.style(
        f"✓ Released {n_dat} DAT(s) + {n_tables} F/V table file(s){dll}{pivot} -> {dest_base}",
        fg="green"))


@group.command("undo")
@click.argument("project", required=False, default=None)
@click.option("--yes", is_flag=True, default=False, help="Skip the confirmation prompt.")
@click.option("--keep-json", is_flag=True, default=False,
              help="Undo the built DATs + table entries but keep the manifest JSON.")
@click.option("--apply-db", is_flag=True, default=False,
              help="Also revert the database rows and delete the Lua stubs the project's abilities wrote.")
def undo_cmd(project: str | None, yes: bool, keep_json: bool, apply_db: bool = False):
    """Undo a project's build and remove it.

    With no PROJECT, lists the dats/*.json projects to pick from. For every target
    the project was built into (recorded on each action's `result.targets`): delete
    the DAT files it placed, clear their file_id entries from that target's
    FTABLE/VTABLE, (for mounts) blank the name/help/key-item strings, and put back
    the client menu record an ability placed (only while the row still holds what the
    build wrote). With --apply-db, also revert the database row an ability inserted or
    changed and delete the Lua stub it wrote (only while it is unedited). Then delete
    the `dats/<project>.json` manifest — unless `--keep-json`, or a database row or stub
    is still there, or a menu record couldn't be put back (a locked 114.DAT while the game
    runs): then the manifest is kept (the cleared actions marked `undone`), so a later
    undo (`--apply-db` for the server side) can finish the job.
    """
    from xi.mount import xi_core as M
    from xi.server import xi_db_apply as DBA
    from xi.server import xi_lua_stub as LS
    from xi.server import xi_server_pass as SP
    from xi.server.xi_step import DASH, current_server_dir, now_iso, q

    if not project:
        project = _pick_project("undo")

    manifest_path = _resolve_manifest_path(None, project)
    if not manifest_path.exists():
        raise click.ClickException(f"No manifest at {manifest_path}.")
    manifest_data = _read_manifest(manifest_path)
    actions = manifest_data.get("actions", [])
    live = [a for a in actions if not _undone(a)]      # what this run clears
    live_ids = {id(a) for a in live}
    built = _project_built_targets(manifest_data)
    n_placements = sum(len(_action_placements(a)) for a in live)
    proj_name = manifest_data.get("name") or manifest_path.stem
    abilities = [a for a in actions if a.get("type") == "ability" and isinstance(a.get("result"), dict)]
    db_acts = [(a, a["result"]["db"]) for a in abilities if SP.db_needs_undo(a["result"].get("db"))]
    lua_acts = [(a, a["result"]["lua"]) for a in abilities
                if isinstance(a["result"].get("lua"), dict) and a["result"]["lua"].get("path")]

    # Menu records an earlier undo couldn't put back (a locked 114.DAT): it kept them on
    # the action it marked undone, so this run tries them again (menu only; no --apply-db
    # needed — the DATs and table entries went last time).
    retry = [a for a in abilities if _undone(a) and isinstance(a["result"].get("menu"), dict)
             and a["result"]["menu"].get("roots")]
    retry_ids = {id(a) for a in retry}
    click.echo(f"\nUndo {manifest_path.stem}:")
    none_built = ("(its DATs went with an earlier undo)" if retry else "(none recorded — nothing to remove in-game)")
    click.echo(f"  built into: {', '.join(built) if built else none_built}")
    click.echo(f"  DATs:       {n_placements} file(s) in each target")
    for a in abilities:
        res = a["result"]
        if isinstance(res.get("db"), dict):
            click.echo(f"  database:   {SP.describe_db(res['db'])}")
        menu = res.get("menu")
        if isinstance(menu, dict) and (id(a) in live_ids or id(a) in retry_ids):
            label = DBA.KIND_LABEL.get(menu.get("kind"), menu.get("record_kind") or "record")
            again = "" if id(a) in live_ids else "; it couldn't be last time"
            for rname in (menu.get("roots") or {}):
                click.echo(f"  menu:       {label} {menu.get('server_id')} in {rname} "
                           f"(the placeholder is put back{again})")
        if isinstance(res.get("lua"), dict) and res["lua"].get("path"):
            click.echo(f"  lua stub:   {res['lua']['path']} (removed with --apply-db)")
    has_menu = any(isinstance(a["result"].get("menu"), dict) and a["result"]["menu"].get("roots")
                   for a in abilities if id(a) in live_ids or id(a) in retry_ids)
    manifest_note = ("kept" if keep_json else "deleted once nothing is left" if (db_acts or lua_acts or has_menu)
                     else "deleted")
    click.echo(f"  manifest:   {manifest_path}  ({manifest_note})")
    if not yes and not click.confirm("\nProceed?", default=False):
        click.echo("Aborted — nothing changed.")
        return

    removed = cleared = restored = 0
    db_left: list[str] = []         # database fields left as they are (changed since the build)
    menu_err: dict = {}             # id(action) -> {root name: label}: restores that failed (kept, retried)

    def undo_menu_in(action: dict, rname: str, root, warns: list) -> int:
        """Put back ``action``'s menu record in root ``rname`` (only while the row still
        holds what the build wrote). 1 when it was restored now. A failure (a locked
        114.DAT) is remembered in ``menu_err`` so the manifest keeps it for the next undo."""
        menu = (action.get("result") or {}).get("menu")
        rs = ((menu or {}).get("roots") or {}).get(rname) if isinstance(menu, dict) else None
        if not (isinstance(rs, dict) and isinstance(rs.get("record_id"), int)):
            return 0
        outcome, warn = SP.undo_menu(menu, rname, root)
        if outcome == "error":
            menu_err.setdefault(id(action), {})[rname] = SP.menu_record_label(menu, rname)
        if warn:
            warns.append(warn)
        return 1 if outcome == "restored" else 0

    for name in built:
        root = _target_root(name)
        menu_left = []
        here = 0
        for action in live:
            if name not in _action_targets(action):
                continue   # never built here: whatever this folder holds at those paths is not ours
            for file_id, dat in _action_placements(action):
                rom = _parse_rom_placement(dat)[0]
                p = root / Path(*dat.split("/"))
                if p.exists():
                    p.unlink()
                    removed += 1
                if _unregister_file_id(root, file_id, rom):
                    cleared += 1
            if action.get("type") == "mount":
                mid = (action.get("result") or {}).get("mount_id")
                if isinstance(mid, int):
                    with _package_config(root, root):
                        M.clear_mount_strings(mid)
            if action.get("type") in RECORD_TYPES:
                res = action.get("result") or {}
                if isinstance(res.get("record_id"), int):
                    # Put back what a forced overwrite replaced, else empty the row.
                    from xi.menu.xi_menu_table import restore_record
                    restore_record(action["type"], root, res["record_id"], res.get("replaced"))
                    cleared += 1
            if action.get("type") == "ability":
                # The Client Menu Record: put back the placeholder, only while the row still
                # holds the bytes the build wrote (a retail update may have taken it since).
                cleared += undo_menu_in(action, name, root, menu_left)
        # The records, lines, names and events the restorable types changed go back to what
        # they held before the build, newest action first (a stack: where two changed one
        # table, each gets back what it changed). What something else changed since is left,
        # and said.
        for action in reversed(live):
            if name in _action_targets(action) and action.get("type") in RESTORABLE_TYPES:
                n, warns = _restorable_module(action["type"]).undo(action, root, name)
                here += n
                db_left.extend(warns)
        restored += here
        click.echo(f"  ✓ {name}: DATs deleted + table entries cleared"
                   + (f", {here} record{'s' if here != 1 else ''} put back" if here else ""))
        for w in db_left:
            click.echo(click.style(f"  ⚠ {w}", fg="yellow"))
        db_left.clear()
        for w in menu_left:
            click.echo(click.style(f"  ⚠ menu: {w}", fg="yellow"))

    # The proposed SQL (and a zone_events action's Lua) goes with the action.
    for action in live:
        if action.get("type") not in RESTORABLE_TYPES:
            continue
        res = action.get("result") or {}
        for key, what in (("sql", "SQL"), ("lua", "Lua")):
            path = res.get(key)
            if isinstance(path, str) and path:
                from xi.database import xi_build as DB
                if DB.remove_sql_section(Path(path), action["id"]):
                    click.echo(f"  ✓ removed {action['id']}'s {what} from {path}")

    # The menu records an earlier undo couldn't put back.
    for name in [n for n in ("pivot", "dir", "hd")
                 if any(n in a["result"]["menu"]["roots"] for a in retry)]:
        menu_left = []
        try:
            root = _target_root(name)
        except click.ClickException as e:           # e.g. FFXI_PIVOT_DIR unset since
            for a in retry:
                if name in a["result"]["menu"]["roots"]:
                    lbl = SP.menu_record_label(a["result"]["menu"], name)
                    menu_err.setdefault(id(a), {})[name] = lbl
                    menu_left.append(f"couldn't put back {lbl} ({e.format_message()})")
            root = None
        n = 0
        if root is not None:
            for a in retry:
                n += undo_menu_in(a, name, root, menu_left)
        cleared += n
        if n:
            click.echo(f"  ✓ {name}: {n} menu record{'s' if n != 1 else ''} put back")
        for w in menu_left:
            click.echo(click.style(f"  ⚠ menu: {w}", fg="yellow"))

    # The server side: the database rows and Lua stubs the abilities wrote.
    left_db: dict = {}              # action id -> "spell_list #1023"
    left_lua: dict = {}             # action id -> path
    reverted: set = set()
    stub_gone: set = set()
    if apply_db:
        if db_acts:
            conn, why = None, None
            if DBA.configured() is None:
                why = DBA.NOT_CONFIGURED
            else:
                try:
                    conn = DBA.connect()
                except DBA.DbUnavailable as e:
                    why = str(e)
            n_db = 0
            for a, db in db_acts:
                what = f"{db.get('table')} #{db.get('id')}"
                if conn is None:
                    click.echo(click.style(f"  db: left {what} {DASH} {why}", fg="yellow"))
                    left_db[a.get("id")] = what
                    continue
                try:
                    op, now = DBA.revert(conn, db)
                except Exception as e:              # noqa: BLE001
                    click.echo(click.style(f"  db: left {what} {DASH} {DBA.friendly_db_error(e)}", fg="yellow"))
                    left_db[a.get("id")] = what
                    continue
                if op in ("deleted", "reverted"):
                    tail = ("" if op == "deleted" else
                            f" animation {db.get('animation')} -> {(db.get('before') or {}).get('animation')}")
                    click.echo(f"  db: {op} {what} {q(db.get('name') or '')}{tail}")
                    n_db += 1
                elif op == "gone":                  # a dbtool re-import, a hand delete: nothing to do
                    click.echo(f"  db: {what} is already gone")
                elif op == "already":
                    click.echo(f"  db: {what} already has animation {now['animation']}")
                elif op == "other":
                    click.echo(click.style(f"  db: {what} now holds {q(now['name'])}, not {q(db.get('name') or '')} "
                                           f"{DASH} not this mix's row; left as it is", fg="yellow"))
                elif op not in DBA.REVERT_DONE:     # "changed": still there with other values
                    held = (f"; it holds animation {now['animation']} now"
                            if now and now.get("animation") is not None else "")
                    click.echo(click.style(f"  db: left {what} (it changed since{held})", fg="yellow"))
                    left_db[a.get("id")] = what
                    continue
                reverted.add(a.get("id"))           # nothing of this mix is left in that row
            if conn is not None:
                try:
                    conn.close()
                except Exception:                   # noqa: BLE001
                    pass
            if n_db:
                click.echo("  restart the map server (xi_map) to load the change")
        server_dir = current_server_dir()
        n_lua = 0
        for a, lua in lua_acts:
            path = lua["path"]
            if not server_dir:
                click.echo(click.style(f"  lua: left {path} {DASH} no server folder", fg="yellow"))
                left_lua[a.get("id")] = path
                continue
            outcome, why = LS.remove_stub(server_dir, lua, project=proj_name, action=a.get("id"))
            if outcome == "removed":
                click.echo(f"  lua: removed {path}")
                stub_gone.add(a.get("id"))
                n_lua += 1
            elif outcome == "missing":
                click.echo(f"  lua: {path} is already gone")
                stub_gone.add(a.get("id"))
            else:
                click.echo(click.style(f"  lua: left {path} {DASH} {why}", fg="yellow"))
                left_lua[a.get("id")] = path
        if n_lua:
            click.echo("  restart the map server to unload it")
    elif db_acts or lua_acts:
        for a, db in db_acts:
            try:
                sql = DBA.revert_sql(db)
            except (ValueError, KeyError, TypeError):
                sql = None
            click.echo(click.style(f"  db: {sql or SP.describe_db(db)}", fg="yellow"))
            left_db[a.get("id")] = f"{db.get('table')} #{db.get('id')}"
        server_dir = current_server_dir()
        for a, lua in lua_acts:
            where = f" (in {server_dir})" if server_dir else ""
            click.echo(click.style(f"  lua stub: {lua['path']}{where}", fg="yellow"))
            left_lua[a.get("id")] = lua["path"]
        click.echo(click.style("  database and server scripts left as they are; run again with --apply-db "
                               "to revert them", fg="yellow"))

    left_menu = [lbl for a in actions for lbl in (menu_err.get(id(a)) or {}).values()]
    left = [*left_db.values(), *left_lua.values(), *left_menu]
    kept = keep_json or bool(left)
    if not kept:
        manifest_path.unlink()
    elif not keep_json:
        # Something is still there: keep the manifest so a later undo can remove it, with
        # what this run cleared marked (and no longer counted). A menu record that couldn't
        # be put back stays on its action (only the roots that failed) for the next undo.
        now = now_iso()
        for a in actions:
            res = a.get("result")
            if not isinstance(res, dict):
                continue
            if id(a) in live_ids:
                res["undone"] = now
            if (id(a) in live_ids or id(a) in retry_ids) and isinstance(res.get("menu"), dict):
                failed = menu_err.get(id(a)) or {}
                roots = {k: v for k, v in (res["menu"].get("roots") or {}).items() if k in failed}
                if roots:
                    res["menu"] = {**res["menu"], "roots": roots}
                else:
                    res.pop("menu", None)
            if a.get("id") in reverted:
                res.pop("db", None)
            if a.get("id") in stub_gone:
                res.pop("lua", None)
        try:
            _write_manifest(manifest_path, manifest_data)
        except OSError as e:
            click.echo(click.style(f"  ⚠ couldn't update {manifest_path} ({e}); the next undo repeats this "
                                   "one (what's already gone is skipped)", fg="yellow"))

    tail = "" if kept else f", removed {manifest_path.name}"
    put_back = f", put back {restored} record{'s' if restored != 1 else ''}" if restored else ""
    click.echo(click.style(
        f"\n✓ Undone. Deleted {removed} DAT{'s' if removed != 1 else ''}, "
        f"cleared {cleared} table entr{'ies' if cleared != 1 else 'y'}{put_back}{tail}.", fg="green"))
    if left and not keep_json:
        what = " and ".join(left)
        verb = "is" if len(left) == 1 else "are"
        if left_db or left_lua:
            how = f"run xi dats undo {manifest_path.stem} --apply-db to remove them"
        else:
            how = (f"close the game and run xi dats undo {manifest_path.stem} again to put "
                   f"{'it' if len(left) == 1 else 'them'} back")
        click.echo(click.style(f"manifest kept: {what} {verb} still there; {how}", fg="yellow"))


def _print_placements(results: list[dict], title: str) -> None:
    """Human-readable per-DAT listing of what a build placed (or would place),
    including the full per-race breakdown for gear and any file_id collisions."""
    click.echo(click.style(title, bold=True))
    collisions = 0
    for r in results:
        kind = r.get("type")
        head = f"  {r.get('id')} ({kind})"
        if kind == "gear":
            races = r.get("races", [])
            click.echo(f"{head}: slot {r.get('slot')}, model_id {r.get('model_id')} "
                       f"— {len(races)} DAT{'s' if len(races) != 1 else ''}")
            for line in races:
                mark = "⚠ " if "occupied by" in line else ""
                click.echo(f"     - {mark}{line}")
                if "occupied by" in line:
                    collisions += 1
        elif kind in RECORD_TYPES:
            mi = f", menu index {r['menu_index']}" if r.get("menu_index") is not None else ""
            click.echo(f"{head}: {kind} {r.get('record_id')} \"{r.get('name')}\" "
                       f"cloned from {r.get('like')}{mi} -> {r.get('dat')}")
            if r.get("moved_from") is not None:
                click.echo(f"     - moved from {kind} {r['moved_from']}, which is put back")
            if r.get("replaced"):
                click.echo(click.style(f"     ⚠ writes over the record that was at {r.get('record_id')} "
                                       "(--force); undo puts it back", fg="yellow"))
            for s in r.get("strings") or []:
                click.echo(f"     - names/help: {s}")
            if r.get("warning"):
                click.echo(click.style(f"     ⚠ {r['warning']}", fg="yellow"))
            if r.get("server"):
                click.echo(f"     server: {r['server']}")
            if r.get("sql"):
                for line in r["sql"].splitlines():
                    click.echo(f"       {line}")
        elif kind == "database":
            recs = r.get("records") or []
            where = ", ".join(r.get("files") or []) or "no DAT changes"
            click.echo(f"{head}: {len(recs)} record{'s' if len(recs) != 1 else ''} -> {where}")
            for e in recs:
                name = f" \"{e['name']}\"" if e.get("name") else ""
                tag = "  (new, copied from its like)" if e.get("created") else ""
                click.echo(f"     - {e['table']} {e['id']}{name} [{e['lang']}] {e['dat']} block {e['block']}{tag}")
                for key, ch in (e.get("changed") or {}).items():
                    if key == "icon":
                        click.echo(f"         icon -> {ch['to']}")
                    else:
                        click.echo(f"         {key}: {_short(ch['from'])} -> {_short(ch['to'])}")
            for w in r.get("warnings") or []:
                click.echo(click.style(f"     ⚠ {w}", fg="yellow"))
            if r.get("server"):
                click.echo(f"     server (proposed, not run): {r['server']}")
            if r.get("sql"):
                for line in r["sql"].splitlines():
                    click.echo(f"       {line}")
        elif kind == "zone_npcs":
            recs = r.get("records") or []
            where = ", ".join(r.get("files") or []) or "no DAT changes"
            click.echo(f"{head}: zone {r.get('zone')}, {len(recs)} name{'s' if len(recs) != 1 else ''} -> {where}")
            for e in recs:
                what = "new" if e.get("created") else f"renamed from {e['from']!r}"
                click.echo(f"     - NPC {e['local']:#x} ({e['sid']:#010x}) {e['to']!r}: {what}")
            for w in r.get("warnings") or []:
                click.echo(click.style(f"     ⚠ {w}", fg="yellow"))
            if r.get("server"):
                click.echo(f"     server (proposed, not run): {r['server']}")
            if r.get("sql"):
                for line in r["sql"].splitlines():
                    click.echo(f"       {line}")
        elif kind == "zone_events":
            recs = r.get("records") or []
            where = ", ".join(r.get("files") or []) or "no DAT changes"
            click.echo(f"{head}: zone {r.get('zone')}, {len(recs)} event{'s' if len(recs) != 1 else ''} -> {where}")
            for e in recs:
                blocks = e.get("blocks") or []
                new_blocks = sum(1 for b in blocks if b.get("created"))
                en = [ln for ln in e.get("lines") or [] if ln.get("lang") == "en"]
                n_lines = sum((ln["grew"][1] - ln["grew"][0]) if ln.get("grew") else 1 for ln in en)
                who = f"{e['npc']} ({e['actor']})" if e.get("npc") else e["actor"]
                click.echo(f"     - {e['name']}: event {e['event']} on {who} — {len(blocks)} block"
                           f"{'s' if len(blocks) != 1 else ''} ({new_blocks} new), {n_lines} line"
                           f"{'s' if n_lines != 1 else ''}")
                for ln in en:
                    if ln.get("grew"):
                        for k, text in enumerate(ln.get("to") or []):
                            click.echo(f"         line {ln['grew'][0] + k} (new): {_short(text)}")
                    else:
                        click.echo(f"         line {ln['id']}: {_short(ln['from'])} -> {_short(ln['to'])}")
            for pl in r.get("placements") or []:
                occ = pl.get("occupied_by")
                note = f"   (occupied by {occ})" if occ else ""
                click.echo(f"     - camera scene: file_id {pl['file_id']} -> {pl['target_dat']}{note}")
                if occ:
                    collisions += 1
            for cam in r.get("dropped") or []:
                click.echo(f"     - camera scene {cam['file_id']} ({cam['dat']}) is no longer used: removed")
            for w in r.get("warnings") or []:
                click.echo(click.style(f"     ⚠ {w}", fg="yellow"))
            if r.get("server"):
                click.echo(f"     server (proposed, not run): {r['server']}")
            if r.get("sql"):
                for line in r["sql"].splitlines():
                    click.echo(f"       {line}")
            if r.get("lua_file"):
                click.echo(f"     server scripts (proposed): {r['lua_file']}")
            elif r.get("lua"):
                for line in r["lua"].splitlines():
                    click.echo(f"       {line}")
        elif kind == "zone_dialog":
            recs = r.get("records") or []
            where = ", ".join(r.get("files") or []) or "no DAT changes"
            click.echo(f"{head}: zone {r.get('zone')}, {len(recs)} line{'s' if len(recs) != 1 else ''} -> {where}")
            for e in recs:
                if e.get("created"):
                    click.echo(f"     - line {e['id']} [{e['lang']}] {e['dat']}  (new): {_short(e['to'])}")
                else:
                    click.echo(f"     - line {e['id']} [{e['lang']}] {e['dat']}: {_short(e['from'])} -> "
                               f"{_short(e['to'])}")
            for w in r.get("warnings") or []:
                click.echo(click.style(f"     ⚠ {w}", fg="yellow"))
        elif kind == "ability":
            files = r.get("placements", [])
            click.echo(f"{head}: {r.get('kind')} animation {r.get('animation')} "
                       f"— {len(files)} DAT{'s' if len(files) != 1 else ''}")
            for w in r.get("warnings") or []:
                click.echo(click.style(f"     ⚠ {w}", fg="yellow"))
            for p in files:
                who = f"{p.get('race') or 'all races'} {p['role']}"
                occ = p.get("occupied_by")
                ph = p.get("placeholder")
                mark = "⚠ " if occ else ""
                note = (f"   (occupied by {occ})" if occ
                        else f"   (retail placeholder {ph}, free)" if ph else "")
                click.echo(f"     - {mark}file_id {p['file_id']:>6}  {who:<26} -> {p['dat']}{note}")
                if occ:
                    collisions += 1
            if r.get("folder"):
                holds = ["the server SQL"] if r.get("server") else []
                holds += [f"a copy of {'each' if len(files) != 1 else 'the'} DAT", "placements.json"]
                click.echo(f"     folder: {r['folder']}  ({', '.join(holds)})")
            if r.get("server"):
                click.echo(f"     server: {r['server']}")
            if r.get("sql"):
                for line in r["sql"].splitlines():
                    click.echo(f"       {line}")
            # The server pass (design2 §1.2): one line per step, then its warnings. The
            # model viewer parses these lines; keep their form.
            sp = r.get("server_pass") or {}
            for key in ("db", "menu", "lua"):
                step = sp.get(key)
                if step is not None:
                    click.echo(f"     {step.line(key)}")
            for key in ("db", "menu", "lua"):
                step = sp.get(key)
                for w in (step.warnings if step is not None else []):
                    click.echo(click.style(f"     ⚠ {key}: {w}", fg="yellow"))
        else:
            src = r.get("source")
            size = f", {r['bytes']:,} B" if r.get("bytes") else ""
            click.echo(f"{head}: model_id {r.get('model_id')} -> file_id {r.get('file_id')}")
            if src:
                click.echo(f"     source: {src}{size}")
            click.echo(f"     dest  : {r.get('target_dat')}")
            if kind == "mount":
                click.echo(f"     name  : {r.get('name_en')!r}"
                           + (f"   key item {r['key_item']}" if r.get("key_item") else "   (no key item)"))
            if r.get("occupied_by"):
                click.echo(click.style(f"     ⚠ file_id already registered to {r['occupied_by']} "
                                       "— would repoint (use --force to build).", fg="yellow"))
                collisions += 1
            if r.get("warning"):
                click.echo(click.style(f"     ⚠ {r['warning']}", fg="yellow"))
    if collisions:
        click.echo(click.style(f"\n{collisions} collision(s) detected — the real build needs --force "
                               "to repoint them.", fg="yellow"))


# ── `dats new` — interactive injection wizard ───────────────────────────────
# Places an ALREADY-BUILT DAT (or a folder of per-race DATs) at new model_ids by
# writing a manifest action, then optionally running `dats build`. This is the
# "I already have the DATs, just inject them" path — no `mesh export` / GLB
# rebuild. Mount reuses the old `mount inject` core; gear reuses the windowed
# gear file_id scheme; entity is a plain model_id + MODEL_FILE_OFFSET placement.

_RULE = "-" * 41  # section divider printed through the wizard


def _rule() -> None:
    click.echo(f"\n{_RULE}")


def _ask(header: str, enter_label: str = "Enter value", **prompt_kwargs):
    """A '>> <header>' section prompt: the header on its own line, a blank line,
    then the actual input line ('<enter_label>: ')."""
    click.echo(f"\n>> {header}")
    click.echo()
    return click.prompt(enter_label, **prompt_kwargs)


def _existing_project_names() -> list[str]:
    """Names of existing dats projects — the `dats/*.json` manifest stems."""
    d = Path("projects")
    if not d.is_dir():
        return []
    return sorted({p.stem for p in d.glob("*.json") if not _is_include_file(p)})


def _is_include_file(path: Path) -> bool:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("schema") == INCLUDE_SCHEMA
    except (OSError, ValueError, AttributeError):
        return False


def _prompt_project_name() -> str:
    """Project-name prompt with type-ahead completion against existing projects
    (prompt_toolkit). Falls back to a plain prompt where there's no interactive
    console (piped input, or the lib is missing from a bundled build)."""
    names = _existing_project_names()
    click.echo("\n>> Project name: (Use an existing name to append to that project)")
    if names:
        click.echo(f"   Existing: {', '.join(names)}")
    click.echo()
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import FuzzyWordCompleter
    except ImportError:
        return click.prompt("Enter name").strip()
    try:
        return PromptSession().prompt(
            "Enter name: ",
            completer=FuzzyWordCompleter(names),
            complete_while_typing=True,
        ).strip()
    except (EOFError, KeyboardInterrupt):
        raise click.Abort()
    except Exception:
        # No usable console (redirected/piped stdin) — fall back to a plain prompt.
        return click.prompt("Enter name").strip()


def _choose(prompt: str, options: list[str], default: str | None = None) -> str:
    click.echo(f"\n>> {prompt.rstrip('?: ')}:")
    for i, o in enumerate(options, 1):
        click.echo(f"  {i}. {o}")
    click.echo()
    default_idx = (options.index(default) + 1) if default in options else None
    n = click.prompt("Enter number", type=click.IntRange(1, len(options)), default=default_idx)
    return options[n - 1]


def _live_file_id_dat(file_id: int) -> str | None:
    """The ROM-relative DAT a file_id currently resolves to in the LIVE install
    tables, or None if unregistered — used for pre-inject occupancy checks."""
    from xi.ftable.xi_core import load_all_tables, resolve_dat
    for _idx, (fdata, vdata) in sorted(load_all_tables().items()):
        dat, _ = resolve_dat(fdata, vdata, file_id)
        if dat:
            return dat
    return None


def _ps_quote(s: str) -> str:
    """Wrap a string as a PowerShell single-quoted literal (backslashes and ``$``
    stay literal; embedded single quotes are doubled)."""
    return "'" + s.replace("'", "''") + "'"


# Off-screen TopMost owner form + AttachThreadInput foregrounding, so the dialog
# reliably opens IN FRONT of the console rather than behind it (Windows' focus-stealing
# lock ignores a plain SetForegroundWindow from a freshly-spawned background process).
# Lifted verbatim in spirit from the editor bridge's proven _pick_glb.
_PS_OWNER = r"""
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
"""


def _native_pick_path(title: str, *, directory: bool = False, file_filter: str | None = None,
                      initial_dir: str | None = None) -> str | None:
    """Open a native OS file/folder picker and return the chosen absolute path, or
    ``None`` if the user cancelled or no GUI dialog is available.

    Windows drives a PowerShell WinForms dialog in its own STA process — the bundled
    embeddable Python omits Tk, and PowerShell is always present. Other platforms fall
    back to tkinter. Best-effort: any failure returns ``None`` so the caller keeps its
    typed-path prompt as the fallback."""
    import subprocess
    initial_dir = initial_dir or os.getcwd()
    try:
        if os.name == "nt":
            if directory:
                dlg = (f"$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
                       f"$d.Description = {_ps_quote(title)}; $d.SelectedPath = {_ps_quote(initial_dir)}; ")
                emit = ("if ($d.ShowDialog($o) -eq [System.Windows.Forms.DialogResult]::OK) "
                        "{ [Console]::Out.Write($d.SelectedPath) }")
            else:
                flt = file_filter or "All files (*.*)|*.*"
                dlg = (f"$d = New-Object System.Windows.Forms.OpenFileDialog; "
                       f"$d.Title = {_ps_quote(title)}; $d.Filter = {_ps_quote(flt)}; "
                       f"$d.InitialDirectory = {_ps_quote(initial_dir)}; $d.Multiselect = $false; ")
                emit = ("if ($d.ShowDialog($o) -eq [System.Windows.Forms.DialogResult]::OK) "
                        "{ [Console]::Out.Write($d.FileName) }")
            ps = (f"Add-Type -AssemblyName System.Windows.Forms | Out-Null; {dlg}"
                  f"{_PS_OWNER}\n{emit}; $o.Close()")
            cp = subprocess.run(["powershell", "-STA", "-NoProfile", "-NonInteractive", "-Command", ps],
                                capture_output=True, text=True, timeout=600)
            return (cp.stdout or "").strip() or None
        import tkinter
        import tkinter.filedialog as _fd
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.lift()
        root.focus_force()
        if directory:
            path = _fd.askdirectory(title=title, initialdir=initial_dir)
        else:
            path = _fd.askopenfilename(title=title, initialdir=initial_dir)
        root.destroy()
        return path or None
    except Exception:  # no display / no Tk / PowerShell missing -> caller keeps typed prompt
        return None


_DAT_FILTER = "FFXI DAT files (*.DAT)|*.DAT|All files (*.*)|*.*"


def _picker_initial_dir(default: str | None, fallback: str) -> str:
    """Prefer the previous answer's folder so re-run browse lands somewhere useful."""
    if not default:
        return fallback
    p = Path(os.path.expanduser(default))
    if p.is_file():
        return str(p.parent)
    if p.is_dir():
        return str(p)
    if p.parent.is_dir():
        return str(p.parent)
    return fallback


def _prompt_existing_dat(header: str, enter_label: str = "Enter path", default: str | None = None) -> Path:
    from xi.xi_config import FFXI_DIR
    click.echo(f"\n>> {header}\n")
    # Pop the native picker straight away (fresh runs only — a re-run keeps its prior
    # answer as the default). Cancelling drops through to the typed prompt.
    auto = default is None
    while True:
        initial = _picker_initial_dir(default, str(FFXI_DIR))
        if auto:
            auto = False
            click.echo("   Opening file picker… (cancel it to type a path instead)")
            raw = _native_pick_path("Select your .DAT file", file_filter=_DAT_FILTER, initial_dir=initial) or ""
        else:
            # Enter accepts the shown default (re-run); empty / b always opens the picker.
            if default:
                click.echo("   Press Enter to keep the previous file, type a path, or b to browse.")
            else:
                click.echo("   Press Enter to browse for the file, or type/paste a path (b = browse).")
            raw = click.prompt(enter_label, default=default or "", show_default=bool(default)).strip().strip('"')
            if raw.lower() in ("b", "browse") or (not raw and not default):
                raw = _native_pick_path("Select your .DAT file", file_filter=_DAT_FILTER, initial_dir=initial) or ""
        if not raw:
            click.echo("  No file selected — type b to browse, or type a path.")
            continue
        rel = raw.replace("\\", "/").split("/")
        cands = (Path(os.path.expanduser(raw)), Path(FFXI_DIR, *rel))
        for cand in cands:
            if cand.is_file():
                return cand.resolve()
        if any(cand.is_dir() for cand in cands):
            click.echo(f"  This step needs a single .DAT file — point at one inside the folder "
                       f"(e.g. {raw}\\hm.DAT), not the folder itself.")
        else:
            click.echo(f"  Not found (tried CWD, FFXI_DIR): {raw}")


def _prompt_existing_dir(header: str, enter_label: str = "Enter path", default: str | None = None) -> Path:
    click.echo(f"\n>> {header}\n")
    auto = default is None
    while True:
        initial = _picker_initial_dir(default, os.getcwd())
        if auto:
            auto = False
            click.echo("   Opening folder picker… (cancel it to type a path instead)")
            raw = _native_pick_path("Select your folder", directory=True, initial_dir=initial) or ""
        else:
            if default:
                click.echo("   Press Enter to keep the previous folder, type a path, or b to browse.")
            else:
                click.echo("   Press Enter to browse for the folder, or type/paste a path (b = browse).")
            raw = click.prompt(enter_label, default=default or "", show_default=bool(default)).strip().strip('"')
            if raw.lower() in ("b", "browse") or (not raw and not default):
                raw = _native_pick_path("Select your folder", directory=True, initial_dir=initial) or ""
        if not raw:
            click.echo("  No folder selected — type b to browse, or type a path.")
            continue
        p = Path(os.path.expanduser(raw))
        if p.is_dir():
            return p.resolve()
        click.echo(f"  Not a folder: {raw}")


def _prompt_dest_dat(header: str, enter_label: str = "Enter DAT path", default: str | None = None) -> str:
    click.echo(f"\n>> {header}\n")
    while True:
        raw = click.prompt(enter_label, default=default).strip().strip('"').upper()
        try:
            rel = _rom_rel(raw)
            _parse_rom_placement(rel)
            return rel
        except click.ClickException as e:
            click.echo(f"  {e}")


def _prompt_dest_dir(header: str, enter_label: str = "Enter DAT Path", default: str | None = None) -> str:
    click.echo(f"\n>> {header}\n")
    while True:
        raw = click.prompt(enter_label, default=default).strip().strip('"').replace("\\", "/").strip("/").upper()
        if re.match(r"^ROM[0-9]*/[0-9]+$", raw):
            return raw
        click.echo(f"  Expected a ROM folder like ROM10/20 (got {raw!r}).")


def _existing_indices(dest_dir: str) -> set[int]:
    """File indices already present at dest_dir across the live targets (FFXI_DIR
    and FFXI_PIVOT_DIR), so the free-block finder won't clobber either."""
    from xi.xi_config import FFXI_DIR, FFXI_PIVOT_DIR
    used: set[int] = set()
    parts = dest_dir.split("/")
    roots = [Path(FFXI_DIR)]
    if FFXI_PIVOT_DIR:
        roots.append(Path(FFXI_PIVOT_DIR))
    for base in roots:
        d = base / Path(*parts)
        if d.is_dir():
            for f in d.iterdir():
                if f.suffix.lower() == ".dat":
                    try:
                        used.add(int(f.stem))
                    except ValueError:
                        pass
    return used


def _find_free_block(dest_dir: str, count: int, reserved: set[int] | None = None) -> list[int] | None:
    """First run of ``count`` consecutive free file indices (1..127) at dest_dir.
    ``reserved`` marks indices already claimed earlier in this wizard run (other
    slots' blocks) that aren't on disk yet."""
    used = _existing_indices(dest_dir) | (reserved or set())
    for start in range(1, 128):
        block = list(range(start, start + count))
        if block[-1] > 127:
            return None
        if not any(i in used for i in block):
            return block
    return None


def _prompt_free_mount_id(default: int | None = None) -> int:
    from xi.mount import xi_core as M
    lo, hi = M.RETAIL_COUNT, M.MENU_CAP - 1
    cache: dict = {}
    if default is None:
        default = next((mid for mid in range(lo, hi + 1)
                        if not M.read_record(mid, cache=cache)["occupied"]), lo)
    click.echo(f"\n>> Which Mount ID would you like to use? (Recommend {lo}-{hi}, menu-visible)\n")
    while True:
        mid = click.prompt("Enter Mount ID", type=int, default=default)
        rec = M.read_record(mid)
        if rec["occupied"] and not click.confirm(
                f"  Mount id {mid} is already used ({rec['model_dat']}). Overwrite?", default=False):
            continue
        return mid


def _prompt_free_entity_id(default: int | None = None) -> int:
    from xi.entity.xi_core import MODEL_FILE_OFFSET, MODEL_SAFE_START
    from xi.xi_config import MAX_ENTITY_MODELID
    click.echo(f"\n>> What Model ID would you like to use? (Recommend {DEFAULT_MODEL_ID['entity']}+, "
               f"safe range {MODEL_SAFE_START}-{MAX_ENTITY_MODELID})\n")
    while True:
        mid = click.prompt("Enter Model ID", type=int,
                           default=default if default is not None else DEFAULT_MODEL_ID["entity"])
        dat = _live_file_id_dat(mid + MODEL_FILE_OFFSET)
        if dat and not click.confirm(
                f"  Model id {mid} already resolves to {dat}. Overwrite?", default=False):
            continue
        return mid


def _prompt_free_gear_id(pairs: list[tuple[str, str]], default: int | None = None) -> int:
    """Prompt for ONE model id shared by every (race, slot) target — each slot has
    its own file_id window, so a whole set can sit at the same id in each."""
    from xi.gear.xi_inject import custom_fid
    max_model = _gear_expand_max()
    if max_model is None:
        click.echo(click.style(
            "\n  ⚠ Custom gear tables are not expanded (run `xi ftable expand gear`). The "
            "manifest will still be written, but `dats build` needs the expanded tables.",
            fg="yellow"))
    click.echo(f"\n>> What Model ID would you like to use? (Recommend {DEFAULT_MODEL_ID['gear']}+)\n")
    while True:
        mid = click.prompt("Enter Model ID", type=int,
                           default=default if default is not None else DEFAULT_MODEL_ID["gear"])
        if max_model is None:
            return mid
        if mid > max_model:
            click.echo(f"  Model id must be <= the expand window ({max_model}). "
                       "Re-expand larger or pick a lower id.")
            continue
        occupied = [(race, slot, dat) for race, slot in pairs
                    if (dat := _live_file_id_dat(custom_fid(race, slot, mid, max_model)))]
        if occupied:
            click.echo(click.style(f"\n  ⚠ Model id {mid} is already in use:", fg="yellow"))
            for race, slot, dat in occupied:
                click.echo(f"     - {RACE_LABEL.get(race, race):14} {slot:6} -> {dat}")
            if not click.confirm("\n  Overwrite?", default=False):
                continue
        return mid


def _prepare_file_id(action: dict) -> int | None:
    """Best-effort resolved file_id for a prepared action's model, for display.
    Returns None when it can't be computed here (e.g. mount id math, or gear
    without an expanded window)."""
    model = action.get("model") or {}
    mid = model.get("model_id")
    if mid is None:
        return None
    try:
        if model.get("kind") == "entity":
            from xi.entity.xi_core import MODEL_FILE_OFFSET
            return mid + MODEL_FILE_OFFSET
        if model.get("kind") == "gear":
            from xi.gear.xi_inject import custom_fid, RACES
            mm = _gear_expand_max()
            slot = action.get("slot")
            if mm is not None and slot:
                return custom_fid(RACES[0], slot, mid, mm)
    except Exception:
        return None
    return None


def _prompt_prepare_model_id(kind: str, default: int | None = None) -> int:
    """Prompt for a model id with range guidance keyed on the model KIND
    (entity/gear/mount). Soft ranges warn but continue; hard upper bounds re-ask.

    - entity: recommend 15000+; below that warns (custom entity range) and continues
    - gear:   must be <= 4095 (per-slot window); below 1000 warns (retail range)
    - mount:  must be <= 63 (menu cap); below 40 warns (retail range)"""
    guide = {
        "entity": "Recommend 15000+ (custom entity range)",
        "gear": "0-4095 (per-slot gear window); 1000+ recommended",
        "mount": "0-63 (menu-visible); 40+ recommended",
    }.get(kind, "")
    click.echo(f"\n>> Target Model ID? ({guide})\n")
    while True:
        mid = click.prompt("Enter Model ID", type=int,
                           default=default if default is not None else DEFAULT_MODEL_ID.get(kind))
        if kind == "entity":
            if mid < 15000:
                click.echo(click.style(
                    f"! Model id {mid} is below the recommended 15000+ custom entity range "
                    "— continuing anyway.", fg="yellow"))
        elif kind == "gear":
            if mid > 4095:
                click.echo(click.style(
                    f"! Gear model id must be 4095 or less (per-slot window) — pick a lower id.",
                    fg="yellow"))
                continue
            if mid < 1000:
                click.echo(click.style(
                    f"! Model id {mid} is within the retail range (below 1000) "
                    "— continuing anyway.", fg="yellow"))
        elif kind == "mount":
            if mid > 63:
                click.echo(click.style(
                    f"! Mount id must be 63 or less (menu cap) — pick a lower id.", fg="yellow"))
                continue
            if mid < 40:
                click.echo(click.style(
                    f"! Mount id {mid} is within the retail range (below 40) "
                    "— continuing anyway.", fg="yellow"))
        return mid


def _detect_races(stem: str) -> list[str]:
    """Race(s) a DAT filename stem maps to (case-insensitive). Usually one race;
    a bare 't'/'taru'/'tarutaru' maps to BOTH Taru genders since they share one
    skeleton/model. Exact prefix matches win over startswith."""
    stem = stem.lower()
    if stem in ("t", "taru", "tarutaru"):
        return ["TaruMale", "TaruFemale"]
    for race in GEAR_RACES:
        if stem in RACE_FILE_PREFIXES[race]:
            return [race]
    for race in GEAR_RACES:
        if any(stem.startswith(p) for p in RACE_FILE_PREFIXES[race]):
            return [race]
    return []


def _detect_races_from_content(path: Path) -> list[str]:
    """Race(s) encoded INSIDE a gear DAT, read from its section names — retail
    (and retail-derived) gear embeds a race code in the 0x01 model header
    ("1hf_") and every 0x20 texture header ("hf_m…"). Texture codes outrank the
    0x01 code when they disagree (packs sometimes clone one race's mesh and only
    swap its textures). Returns [] when nothing — or more than one race — matches."""
    try:
        from xi.entity.mesh.xi_export import parse_sections
        sections = parse_sections(path.read_bytes())
    except Exception:
        return []
    tex_codes: set[str] = set()
    model_codes: set[str] = set()
    for s in sections:
        name = s.name.lower()
        if s.type_code == 0x20 and name[2:3] == "_" and name[:2] in RACE_CONTENT_CODES:
            tex_codes.add(name[:2])
        elif s.type_code == 0x01 and name[3:4] == "_" and name[1:3] in RACE_CONTENT_CODES:
            model_codes.add(name[1:3])
    for codes in (tex_codes, tex_codes | model_codes):
        if len(codes) == 1:
            return RACE_CONTENT_CODES[next(iter(codes))]
    return []


def _detect_slot(stem: str) -> str | None:
    """Slot a DAT filename stem maps to, by whole-word keyword match
    ("100 - Loxley Hands" -> hands). Word-split so substrings don't false-match."""
    tokens = set(re.split(r"[^a-z0-9]+", stem.lower()))
    for slot in GEAR_SLOTS:
        if tokens & set(SLOT_FILE_KEYWORDS[slot]):
            return slot
    return None


def _detect_slot_from_content(path: Path) -> str | None:
    """Slot encoded INSIDE a gear DAT: armor 0x01 model-header names start with a
    slot digit ahead of the race code ("1em_…" = head). Weapon names don't follow
    the digit scheme, so main/sub/ranged never resolve here. Returns None when
    nothing — or more than one slot — matches."""
    try:
        from xi.entity.mesh.xi_export import parse_sections
        sections = parse_sections(path.read_bytes())
    except Exception:
        return None
    slots = {SLOT_CONTENT_DIGITS[n[0]] for s in sections
             if s.type_code == 0x01 and (n := s.name.lower())
             and n[:1] in SLOT_CONTENT_DIGITS and n[3:4] == "_"
             and n[1:3] in RACE_CONTENT_CODES}
    return next(iter(slots)) if len(slots) == 1 else None


def _race_group_label(races: list[str]) -> str:
    """Friendly label for the race(s) a file covers ('Tarutaru' for the shared pair)."""
    if set(races) == {"TaruMale", "TaruFemale"}:
        return "Tarutaru"
    return " + ".join(RACE_LABEL[r] for r in races)


def _validate_gear_dat(path: Path) -> tuple[bool, str]:
    """Confirm a file is a real gear piece: parse its DAT sections and require at
    least one SkeletonMesh (0x2A). Gear DATs carry NO skeleton of their own — they
    rig against the shared race skeleton — so this checks the mesh, not a skeleton."""
    try:
        from xi.entity.mesh.xi_export import parse_sections, SECTION_TYPE_SKELETON_MESH
        sections = parse_sections(path.read_bytes())
        parts = [s for s in sections if s.type_code == SECTION_TYPE_SKELETON_MESH]
        if not parts:
            return False, "no SkeletonMesh (0x2A) section — not a gear model?"
        return True, f"valid gear mesh, {len(parts)} part{'s' if len(parts) != 1 else ''}"
    except Exception as e:  # keep the wizard going on an unexpected parse error
        return False, f"could not parse ({e})"


def _detect_gear_files(folder: Path, default_slot: str | None = None) -> dict[str, dict[str, Path]]:
    """Scan a source folder and auto-detect each DAT's SLOT and RACE(s) — filename
    first (an explicit rename always wins), then the codes embedded in the DAT's
    own section names — list everything back for one confirm, and prompt only for
    what couldn't be detected. Returns {slot: {race: Path}} in canonical slot/race
    order, so a full set (one race, many slots), a per-race pack (one slot, many
    races), or any mix all come out naturally. A shared Taru DAT is bound to both
    genders."""
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".dat")
    if not files:
        raise click.ClickException(f"No .DAT files in {folder}.")

    click.echo("\nChecking for .DAT files...")
    click.echo(f"Found {len(files)} .DAT File{'s' if len(files) != 1 else ''}:")
    mapping: dict[str, dict[str, Path]] = {}
    pending: list[tuple[Path, list[str]]] = []   # race known, slot not
    unknown: list[Path] = []                     # race unknown

    def _report(p: Path, races: list[str], slot: str | None, note: str = "") -> None:
        """One line per file; details only when something needs attention."""
        ok, detail = _validate_gear_dat(p)
        problem = "" if ok else f" — {click.style('⚠ ' + detail, fg='yellow')}"
        click.echo(f"- {_race_group_label(races)} - {slot or '?'} - {p.name}{note}{problem}")
        content = _detect_races_from_content(p)
        if content and not set(content) & set(races):
            click.echo(click.style(
                f"  ! {p.name} contents look like {_race_group_label(content)} — "
                "double-check this assignment.", fg="yellow"))

    def _assign(p: Path, races: list[str], slot: str) -> None:
        per_slot = mapping.setdefault(slot, {})
        assigned = [r for r in races if r not in per_slot]
        for r in assigned:
            per_slot[r] = p
        note = "" if assigned == races else "  (already assigned for this slot; skipped)"
        _report(p, assigned or races, slot, note)

    for p in files:
        races = _detect_races(p.stem) or _detect_races_from_content(p)
        slot_name = _detect_slot(p.stem)
        slot_content = _detect_slot_from_content(p)
        if not races:
            unknown.append(p)
            click.echo(f"- ? - ? - {p.name}")
            continue
        slot = slot_name or slot_content
        if slot is None:
            pending.append((p, races))
            _report(p, races, None)
            continue
        _assign(p, races, slot)
        if slot_name and slot_content and slot_name != slot_content:
            click.echo(click.style(
                f"  ! {p.name} contents look like a {slot_content} model — "
                "double-check the slot.", fg="yellow"))

    for p in unknown:
        if click.confirm(f"  Assign {p.name} to a race?", default=False):
            label = _choose(f"Race for {p.name}", [RACE_LABEL[r] for r in GEAR_RACES])
            race = next(r for r in GEAR_RACES if RACE_LABEL[r] == label)
            slot = _detect_slot(p.stem) or _detect_slot_from_content(p)
            if slot:
                _assign(p, [race], slot)
            else:
                pending.append((p, [race]))

    if pending and not mapping:
        # No slot detected anywhere — a classic per-race, single-slot pack.
        slot = _choose("Which slot are these for?", GEAR_SLOTS, default=default_slot)
        for p, races in pending:
            _assign(p, races, slot)
    else:
        for p, races in pending:
            slot = _choose(f"Which slot is {p.name} for?", GEAR_SLOTS, default=default_slot)
            _assign(p, races, slot)

    if not mapping:
        raise click.ClickException(
            "No gear detected — no recognizable filenames (hm/hf/em/ef/tm/tf/m/g, "
            "or slot words like Head/Body/Hands) or codes inside the DATs.")
    if not click.confirm("\n>> Are these correct?", default=True):
        raise click.ClickException("Aborted — adjust the filenames and re-run.")
    return {slot: {r: mapping[slot][r] for r in GEAR_RACES if r in mapping[slot]}
            for slot in GEAR_SLOTS if slot in mapping}


def _wizard_mount(slug: str, project: str, prev: dict | None = None) -> dict:
    p = prev or {}
    ptext = p.get("text") or {}
    src = _prompt_existing_dat("Where is your mount .DAT file? (e.g. ROM10/10/1.DAT or a path)",
                               default=(p.get("resources") or {}).get("model_dat"))
    dest = _prompt_dest_dat("Which ROM folder and DAT path would you like? (e.g. ROM10/10/1.DAT)",
                            default=(p.get("target") or {}).get("dat"))
    mount_id = _prompt_free_mount_id(default=(p.get("target") or {}).get("mount_id"))

    name_en = _ask("What is the mount name? (English)", "Enter name", default=ptext.get("name_en") or project)
    name_jp = _ask("What is the mount name? (Japanese)", "Enter name", default=ptext.get("name_jp") or name_en)
    text: dict = {"name_en": name_en, "name_jp": name_jp}
    ki_default = True if prev is None else (bool(ptext.get("add_key_item")) and not ptext.get("no_key_item"))
    click.echo("\n>> Add a key item? (grants the mount in-game)")
    if click.confirm(">> Add key item", default=ki_default):
        text["add_key_item"] = True
        text["key_item_name_en"] = _ask("Key item name (English)", "Enter name",
                                         default=ptext.get("key_item_name_en") or f"{name_en} Companion")
        text["key_item_desc_en"] = _ask("Key item description (English)", "Enter text",
                                         default=ptext.get("key_item_desc_en") or "")
        text["key_item_name_jp"] = _ask("Key item name (Japanese)", "Enter name",
                                         default=ptext.get("key_item_name_jp") or text["key_item_name_en"])
        text["key_item_desc_jp"] = _ask("Key item description (Japanese)", "Enter text",
                                         default=ptext.get("key_item_desc_jp") or "")
    else:
        text["no_key_item"] = True

    return {
        "id": f"mount.{slug}", "type": "mount",
        "model": {"kind": "mount", "model_id": mount_id},
        "target": {"mount_id": mount_id, "dat": dest},
        "resources": {"model_dat": str(src)},
        "text": text, "server": {"emit": True},
    }


def _wizard_entity(slug: str, prev: dict | None = None) -> dict:
    p = prev or {}
    src = _prompt_existing_dat("Select your .DAT file",
                               default=(p.get("resources") or {}).get("raw_dat"))
    dest = _prompt_dest_dat("Which ROM folder and DAT path would you like? (e.g. ROM10/25/2.DAT)",
                            default=(p.get("target") or {}).get("dat"))
    model_id = _prompt_free_entity_id(default=(p.get("model") or {}).get("model_id"))
    return {
        "id": f"entity.{slug}", "type": "entity",
        "model": {"kind": "entity", "model_id": model_id},
        "target": {"dat": dest},
        "resources": {"raw_dat": str(src)},
    }


def _resolve_dat_input(raw: str) -> Path | None:
    """Resolve a user-typed DAT reference to an existing file, tolerantly. Tries the value as
    given and with ``.DAT`` appended, against CWD then FFXI_DIR — so ``rom/136/87``,
    ``ROM/136/87.DAT``, or a full path all resolve (Windows' case-insensitive FS handles
    ``rom`` vs ``ROM``). Returns the resolved absolute Path, or None."""
    from xi.xi_config import FFXI_DIR
    raw = raw.strip().strip('"')
    if not raw:
        return None
    variants = [raw] + ([] if raw.lower().endswith(".dat") else [raw + ".DAT"])
    for v in variants:
        parts = v.replace("\\", "/").split("/")
        for cand in (Path(os.path.expanduser(v)), Path(FFXI_DIR, *parts)):
            if cand.is_file():
                return cand.resolve()
    return None


def _prompt_optional_dat(header: str, default: str | None = None) -> str | None:
    """A DAT prompt that can be skipped (Enter with no default = skip). Text-first
    (unlike :func:`_prompt_existing_dat`, which auto-pops a picker) so the NPC wizard's
    many slot prompts don't each open a dialog. ``b`` browses; a blank keeps the default
    when there is one, else skips. Resolves against CWD then FFXI_DIR."""
    from xi.xi_config import FFXI_DIR
    click.echo(f"\n>> {header}\n")
    if default:
        click.echo("   Press Enter to keep the previous file, a path, b to browse, or - to clear.")
    else:
        click.echo("   Press Enter to skip (use the naked base part), a path, or b to browse.")
    while True:
        raw = click.prompt("Enter path", default=default or "", show_default=bool(default)).strip().strip('"')
        if raw == "-":
            return None
        if not raw:
            return default or None
        if raw.lower() in ("b", "browse"):
            picked = _native_pick_path("Select a .DAT file", file_filter=_DAT_FILTER,
                                       initial_dir=_picker_initial_dir(default, str(FFXI_DIR)))
            raw = (picked or "").strip()
            if not raw:
                continue
        resolved = _resolve_dat_input(raw)
        if resolved:
            return str(resolved)
        click.echo(f"  Not found (tried CWD, FFXI_DIR, +.DAT): {raw}")


def _prompt_slot_dat(slot_label: str, default: str | None = None) -> str | None:
    """Compact per-slot DAT prompt for the NPC armour loop — no header/guidance (the section
    prints one intro line). Enter skips (or keeps the default), ``b`` browses, ``-`` clears;
    anything else is resolved by :func:`_resolve_dat_input` (infers ``.DAT`` + FFXI_DIR)."""
    from xi.xi_config import FFXI_DIR
    while True:
        raw = click.prompt(f"Enter DAT path for: {slot_label}",
                           default=default or "", show_default=bool(default)).strip().strip('"')
        if raw == "-":
            return None
        if not raw:
            return default or None
        if raw.lower() in ("b", "browse"):
            raw = (_native_pick_path("Select a .DAT file", file_filter=_DAT_FILTER,
                                     initial_dir=_picker_initial_dir(default, str(FFXI_DIR))) or "").strip()
            if not raw:
                continue
        resolved = _resolve_dat_input(raw)
        if resolved:
            return str(resolved)
        click.echo(f"  Not found (tried CWD, FFXI_DIR, +.DAT): {raw}")


def _face_catalog(race: str) -> list[tuple[object, str, str]]:
    """``[(spec, label, ref)]`` for a race's faces, mirroring AltanaViewer's Face dropdown.

    Entries come from :func:`xi.entity.xi_bake_npc.pc_face_list` — the static face table
    (codes like ``F8A`` and NPC-face names, baked into xi from AltanaViewer's lists), with
    Tarutaru computed. ``spec`` is the face DAT's ROM path."""
    from xi.entity.xi_bake_npc import pc_face_list
    return [(rom, label, rom.replace(".DAT", "")) for rom, label in pc_face_list(race)]


def _prompt_face(race: str, default: object | None = None):
    """Pick a face the way AltanaViewer's Face dropdown does — by code (``F8A``) or name
    (``Maximilian``), not a raw byte. Returns the chosen ``spec`` (a ROM path from the CSV, or
    a face id from the computed fallback) — both are accepted downstream by the baker. The
    user can type a list number, or a face code like ``8A``."""
    catalog = _face_catalog(race)
    if not catalog:
        return default if default is not None else 0
    by_code: dict[str, object] = {}
    for spec, label, _ in catalog:
        key = label.upper()
        if re.match(r"^F\d", key):                        # a face code (F8A) — allow '8A' too
            by_code.setdefault(key[1:], spec)

    click.echo("\n>> Which face? (as shown in AltanaViewer's Face list)\n")
    for i, (_, label, ref) in enumerate(catalog, 1):
        click.echo(f"  {i:3}. {label:24} {ref}")
    click.echo()
    default_num = next((i for i, (spec, _, _) in enumerate(catalog, 1) if spec == default), None)
    while True:
        raw = str(click.prompt("Enter face code or DAT",
                               default=str(default_num) if default_num else None)).strip().strip('"')
        if raw.isdigit() and 1 <= int(raw) <= len(catalog):      # a list position
            return catalog[int(raw) - 1][0]
        if raw.upper().lstrip("F") in by_code:                   # a face code (8A / F8A)
            return by_code[raw.upper().lstrip("F")]
        resolved = _resolve_dat_input(raw)                       # a custom face .DAT
        if resolved:
            return str(resolved)
        click.echo(f"  Enter a list number (1-{len(catalog)}), a face code like 8A, or a .DAT path.")


def _prompt_weapon(side: str, race: str, prev: dict | None = None) -> dict | None:
    """Collect one weapon: a type (record-keeping + a hint for the future battle-anim
    pass) and a DAT whose mesh is baked in. Returns ``{"type", "dat"}`` or None."""
    from xi.entity.xi_bake_npc import WEAPON_TYPES
    prev = prev or {}
    label = "main-hand" if side == "main" else "sub-hand"
    if not click.confirm(f"\n>> Add a {label} weapon?", default=bool(prev.get("dat"))):
        return None
    wtype = _choose(f"{label.title()} weapon type", WEAPON_TYPES, default=prev.get("type"))
    dat = _prompt_optional_dat(f"{label.title()} weapon DAT", default=prev.get("dat"))
    if not dat:
        return None
    return {"type": wtype, "dat": dat}


def _look_race_to_wizard(look_race: str) -> tuple[str | None, str | None, str]:
    """(wizard race label, gender, canonical xi race) for a look's raceName. Tarutaru's two
    look genders both fold to the single Tarutaru model (its look is chosen by the face)."""
    from xi.entity.xi_bake_npc import RACE_GENDERS
    if look_race in ("TaruMale", "TaruFemale"):
        return "Tarutaru", None, "TaruMale"
    for label, genders in RACE_GENDERS.items():
        for g, r in genders.items():
            if r == look_race:
                return label, g, r
    return None, None, look_race


def _npc_from_look(raw: str) -> tuple[dict | None, str | None]:
    """Decode a 20-byte FFXI look string into an NPC-wizard recipe, or ``(None, error)``.

    Resolves the face + each worn slot to its DAT using the LOOK's own race (so a Tarutaru
    *female* look picks the female face DAT even though the bake uses the shared Taru model).
    Only equipped-character looks make a costume; a fixed-model look is rejected with a hint."""
    from xi.gear.xi_core import parse_look
    from xi.gear.xi_export import resolve_gear_dat
    from xi.entity.mesh.xi_export import rom_relative
    hexs = re.sub(r"[^0-9a-fA-F]", "", raw)
    if len(hexs) < 40:
        return None, f"a look is 20 bytes (40 hex chars); got {len(hexs)}."
    try:
        look = parse_look(bytes.fromhex(hexs[:40]))
    except ValueError as e:
        return None, str(e)
    if look.get("type") != "equipped":
        return None, (f"that look is a fixed model ({look.get('type')}, model "
                      f"{look.get('modelid')}) — not a wearable costume. Use the normal flow and "
                      f"point the Entity type at that model's DAT.")
    look_race = look.get("raceName")
    if not look_race:
        return None, f"unknown race id {look.get('race')} in the look."
    label, gender, canon = _look_race_to_wizard(look_race)

    def resolve(slot: str, mid: int):
        try:
            return rom_relative(resolve_gear_dat(look_race, slot, mid))
        except Exception:
            return None

    slots = look["slots"]
    slot_dats: dict = {}
    for slot in ("head", "body", "hands", "legs", "feet"):
        if slots.get(slot):
            if (pth := resolve(slot, slots[slot])):
                slot_dats[slot] = pth
    main = sub = None
    if slots.get("main") and (d := resolve("main", slots["main"])):
        main = {"type": None, "dat": d}
    if slots.get("sub") and (d := resolve("sub", slots["sub"])):
        sub = {"type": None, "dat": d}
    recipe = {
        "race_label": label, "gender": gender, "race": canon,
        "face_id": resolve("face", look["face"]) or look["face"],
        "slots": slot_dats, "main": main, "sub": sub, "dual_wield": bool(main and sub),
    }
    return recipe, None


def _prompt_look() -> dict | None:
    """Prompt for a pasted look string and decode it to a recipe (``None`` = fall back to the
    normal prompts — empty input, or the user declines to retry a bad string)."""
    while True:
        raw = click.prompt("Paste the look string (40 hex chars)", default="").strip()
        if not raw:
            return None
        recipe, err = _npc_from_look(raw)
        if err:
            click.echo(f"  {err}")
            if not click.confirm("  Try another look string?", default=True):
                return None
            continue
        return recipe


def _wizard_npc(slug: str, prev: dict | None = None) -> dict:
    """Collect an NPC costume (race + gender + face + gear + weapons), BAKE it into a
    single self-contained entity DAT at ``dats/custom/<slug>.dat``, then fall into the
    entity flow (dest ROM path + model id) with that baked DAT as the source. The build
    step then places it verbatim like any other entity. See [[custom-npc-feature]]."""
    from xi.entity.xi_bake_npc import (
        bake_costume_npc, race_from_gender, RACE_GENDERS, ARMOUR_SLOTS)
    p = prev or {}
    rec = p.get("npc") or {}

    # Fast path: paste a 20-byte look string and auto-fill race/face/gear/weapons.
    looked = None
    if click.confirm("\n>> Do you have a Look String? (paste it to auto-fill race, face and gear)",
                     default=False):
        looked = _prompt_look()

    if looked:
        rec = looked
        race_label, gender, race = looked["race_label"], looked["gender"], looked["race"]
        face_id, slot_dats = looked["face_id"], dict(looked["slots"])
        main, sub, dual_wield = looked["main"], looked["sub"], looked["dual_wield"]
        click.echo(click.style("\n✓ Decoded look:", fg="green"))
        click.echo(f"   {race}")
        click.echo(f"     {'face':5} {face_id}")
        for slot in ARMOUR_SLOTS:
            click.echo(f"     {slot:5} {slot_dats.get(slot, '(naked base)')}")
        if main:
            click.echo(f"     main  {main['dat']}")
        if sub:
            click.echo(f"     sub   {sub['dat']}")
    else:
        race_label = _choose("Which race?", list(RACE_GENDERS.keys()), default=rec.get("race_label"))
        genders = RACE_GENDERS[race_label]
        gender = None
        if None not in genders:
            gender = _choose(f"Which gender?", [g for g in genders if g], default=rec.get("gender"))
        race = race_from_gender(race_label, gender)

        face_id = _prompt_face(race, default=rec.get("face_id"))

        prev_slots = rec.get("slots") or {}
        slot_dats = {}
        click.echo("\n>> Now we'll build the gear — skip any to use the naked base parts, "
                   "or type \"b\" to open a file browser.\n")
        for slot in ARMOUR_SLOTS:
            path = _prompt_slot_dat(slot.upper(), default=prev_slots.get(slot))
            if path:
                slot_dats[slot] = path

        main = _prompt_weapon("main", race, rec.get("main"))
        sub = _prompt_weapon("sub", race, rec.get("sub"))
        dual_wield = bool(main and sub) and click.confirm("\n>> Dual wield?", default=bool(rec.get("dual_wield")))

    click.echo("\n>> Baking the NPC DAT …")
    data, report = bake_costume_npc(
        race=race, face_id=face_id, slot_dats=slot_dats,
        main=main, sub=sub, dual_wield=dual_wield, root_name=(slug[:4] or "npc0"))
    baked = Path("projects/custom") / f"{slug}.dat"
    baked.parent.mkdir(parents=True, exist_ok=True)
    baked.write_bytes(data)

    # Just the result — the costume was already echoed back above (decoded look) or typed
    # in slot by slot. Only a genuine problem earns another line.
    click.echo(click.style(f"\n✓ Baked {report.size:,} bytes -> {baked}", fg="green"))
    if report.missing_clips:
        click.echo(click.style(f"   ! missing locomotion clips: {', '.join(report.missing_clips)}", fg="yellow"))

    # Fall into the entity flow: place the baked DAT at a custom entity model id.
    dest = _prompt_dest_dat("Which ROM folder and DAT path would you like? (e.g. ROM/25/2.DAT)",
                            default=(p.get("target") or {}).get("dat"))
    model_id = _prompt_free_entity_id(default=(p.get("model") or {}).get("model_id"))
    return {
        "id": f"npc.{slug}", "type": "entity",
        "model": {"kind": "entity", "model_id": model_id},
        "target": {"dat": dest},
        "resources": {"raw_dat": str(baked)},
        # Recipe kept so re-running this project preloads the costume defaults.
        "npc": {"race_label": race_label, "gender": gender, "race": race, "face_id": face_id,
                "slots": slot_dats, "main": main, "sub": sub, "dual_wield": dual_wield},
    }


def _wizard_gear(slug: str, prevs: list[dict] | None = None) -> list[dict]:
    """Collect gear placement(s) from one source folder. Multi-slot aware: the
    folder is grouped {slot: {race: file}} and ONE action per slot is returned —
    the build already treats each action independently, so a full set builds in
    one pass. A single model id and destination folder are shared across the set
    (each slot has its own file_id window)."""
    prevs = prevs or []
    p0 = prevs[0] if prevs else {}
    prev_by_slot = {a.get("slot"): a for a in prevs}
    folder = _prompt_existing_dir("Enter the path to the folder where your custom .DAT files are located",
                                  default=p0.get("source_dir"))
    groups = _detect_gear_files(folder, default_slot=p0.get("slot"))

    # Default the destination folder to the project's previous one.
    prev_targets0 = p0.get("targets") or []
    dest_default = None
    if prev_targets0:
        dest_default = "/".join(_rom_rel(prev_targets0[0]["dat"]).split("/")[:-1])
    dest_dir = _prompt_dest_dir(
        "Which ROM folder and DAT path would you like? (File names will be automatic) (e.g. rom10/20)",
        default=dest_default)

    # Allocate destination file indices per slot. If re-running the same project
    # into the same folder with the same races, reuse that slot's existing block
    # (overwrite in place); everything else takes the next free block. Reuse is
    # resolved FIRST so a fresh block can't land on a reused (but not yet built)
    # index.
    blocks: dict[str, list[int]] = {}
    reused: set[str] = set()
    reserved: set[int] = set()
    for slot, race_map in groups.items():
        prev_targets = (prev_by_slot.get(slot) or {}).get("targets") or []
        prev_idx = {t["race"]: int(_rom_rel(t["dat"]).split("/")[-1].split(".")[0])
                    for t in prev_targets
                    if "/".join(_rom_rel(t["dat"]).split("/")[:-1]) == dest_dir}
        if prev_idx and set(prev_idx) == set(race_map):
            blocks[slot] = [prev_idx[r] for r in race_map]
            reused.add(slot)
            reserved.update(blocks[slot])
    for slot, race_map in groups.items():
        if slot in blocks:
            continue
        block = _find_free_block(dest_dir, len(race_map), reserved=reserved)
        if block is None:
            raise click.ClickException(
                f"No free block of {len(race_map)} consecutive files in {dest_dir} (1-127).")
        blocks[slot] = block
        reserved.update(block)

    click.echo()
    for slot, race_map in groups.items():
        block = blocks[slot]
        span = f"{dest_dir}/{block[0]}.DAT" + (f" ... {dest_dir}/{block[-1]}.DAT" if len(block) > 1 else "")
        note = "reusing this project's block" if slot in reused else "free block"
        click.echo(f"  {slot:6} -> {span}  ({note})")

    pairs = [(race, slot) for slot, race_map in groups.items() for race in race_map]
    model_id = _prompt_free_gear_id(pairs, default=(p0.get("model") or {}).get("model_id"))

    multi = len(groups) > 1
    actions = []
    for slot, race_map in groups.items():
        prev_a = prev_by_slot.get(slot)
        # Keep the id an earlier run gave this slot; a single-slot first run keeps
        # the historical bare `gear.<slug>` id, multi-slot runs suffix the slot.
        aid = ((prev_a or {}).get("id")
               or (f"gear.{slug}.{slot}" if multi or prevs else f"gear.{slug}"))
        actions.append({
            "id": aid, "type": "gear", "slot": slot,
            "model": {"kind": "gear", "model_id": model_id},
            "source_dir": str(folder),
            "targets": [{"race": race, "raw_dat": str(race_map[race]), "dat": f"{dest_dir}/{idx}.DAT"}
                        for race, idx in zip(race_map, blocks[slot])],
        })
    return actions


def _expansion_report(entries: int) -> list[tuple[str, str, bool, str]]:
    """(ctype, label, is_ready, detail) for each custom content type, given the
    base install's FTABLE entry count. Mounts fit within the retail range; entity +
    gear need the FTABLE grown past it (`xi ftable expand`). Gear additionally
    needs the FFXiMain.dll gear-patch (a client/launcher update can revert it)."""
    from xi.mount.xi_core import MOUNT_FILE_BASE, MENU_CAP
    from xi.entity.xi_core import MODEL_FILE_OFFSET, MODEL_SAFE_START
    from xi.gear.xi_inject import gear_ftable_target, CUSTOM_GEAR_BASE
    from xi.xi_config import MAX_ENTITY_MODELID

    out: list[tuple[str, str, bool, str]] = []
    mount_top = MOUNT_FILE_BASE + MENU_CAP - 1
    if entries > mount_top:
        out.append(("mount", "Mounts ready", True, f"mount ids up to {MENU_CAP - 1}"))
    else:
        out.append(("mount", "Mounts", False,
                    f"FTABLE too small for mount file_ids (needs over {mount_top:,} entries)"))
    entity_floor = MODEL_FILE_OFFSET + MODEL_SAFE_START
    if entries > entity_floor:
        out.append(("entity", "Entity models ready", True,
                    f"custom model ids {MODEL_SAFE_START:,}-{MAX_ENTITY_MODELID:,}"))
    else:
        out.append(("entity", "Entity models", False,
                    f"FTABLE lacks the custom entity band — run `xi ftable expand entity`"))
    max_model = _gear_expand_max()
    if max_model is None:
        out.append(("gear", "Gear", False,
                    "FFXiMain.dll has no custom gear window — retail DLL, or a client "
                    "update reverted the patch (run `xi ftable expand gear`)"))
    else:
        need = gear_ftable_target(max_model)
        opened = need - CUSTOM_GEAR_BASE
        if entries >= need:
            out.append(("gear", "Gear DLL patched", True,
                        f"model ids up to {max_model:,} per race & slot — "
                        f"{opened:,} file ids opened"))
        else:
            out.append(("gear", "Gear", False,
                        f"DLL window is {max_model:,} but the FTABLE is too small "
                        f"(needs {need:,} entries — run `xi ftable expand gear`)"))
    return out


def _check_targets_ready(target: str = "dir") -> dict[str, bool]:
    """First step of the wizard: verify the build target's FTABLE (the base install,
    or FFXI_PIVOT_DIR for `--pivot`) and whether it's expanded for mounts / entity /
    gear (with sizes). Aborts when there's no FTABLE at all (nothing could register
    file_ids); otherwise returns per-type readiness so the wizard can refuse a type
    whose tables/DLL aren't ready — instead of collecting every answer and only
    failing at build time. Builds patch this table directly (no seeding)."""
    root = _target_root(target)
    setting = "FFXI_PIVOT_DIR" if target == "pivot" else "FFXI_DIR"
    entries = _ftable_entries(root / "FTABLE.DAT")
    click.echo(f"\n{_TARGET_LABELS[target]} ({setting}): {root}")
    if entries == 0:
        raise click.ClickException(
            f"No FTABLE.DAT in {root} — builds register file_ids there, so nothing "
            f"can be built. Check {setting} points at the right folder, then re-run "
            "`xi dats new`.")
    from xi.ftable.xi_expand import RETAIL_ENTRIES
    if entries > RETAIL_ENTRIES:
        click.echo(f"  {click.style('✓', fg='green')} FTABLE expanded "
                   f"({entries:,} entries — retail {RETAIL_ENTRIES:,} "
                   f"+ {entries - RETAIL_ENTRIES:,} custom)")
    else:
        click.echo(f"  {click.style('⚠', fg='yellow')} FTABLE at retail size "
                   f"({entries:,} entries — `xi ftable expand` grows it)")
    ready: dict[str, bool] = {}
    for ctype, label, ok, detail in _expansion_report(entries):
        mark = click.style("✓", fg="green") if ok else click.style("⚠", fg="yellow")
        click.echo(f"  {mark} {label} ({detail})" if ok else f"  {mark} {label}: {detail}")
        ready[ctype] = ok
    if not all(ready.values()):
        click.echo(click.style(
            "\n  Some types are NOT ready — the wizard will refuse those until the "
            "base install is expanded/patched.", fg="yellow"))
    return ready


@group.command("new")
@click.option("--project", default=None,
              help="Skip the name prompt — writes to dats/<project>.json.")
@click.option("--pivot", is_flag=True, default=False,
              help="Check and build into FFXI_PIVOT_DIR instead of the base install (FFXI_DIR).")
def new_cmd(project: str | None, pivot: bool = False):
    """Interactively inject prebuilt DAT(s) at new model ids.

    A wizard for the "I already have the DATs, just place them at new model ids"
    case: no `mesh export` / GLB rebuild. Asks for the content type (gear /
    mount / entity), collects the source DAT(s), destination, and model id(s),
    writes a `dats/<project>.json` manifest action, then offers to build it.
    """
    click.echo("\nWelcome to the Dat Modification wizard")
    _rule()
    ready = _check_targets_ready("pivot" if pivot else "dir")
    _rule()
    project = project or _prompt_project_name()
    slug = _project_slug(project)
    manifest_path = Path(f"projects/{slug}.json")
    manifest = _read_manifest(manifest_path)
    manifest["name"] = slug

    ctype = {
        "Gear": "gear",
        "Mounts": "mount",
        "Entity (NPC / Monster / Object)": "entity",
        "NPC (costume: race + gear + weapons)": "npc",
        "Ability (recipe from the Ability Mixer / xi ability recipe)": "ability",
        "Spell menu record (a new spell id: name, help, MP, levels)": "spell",
        "Command menu record (a new job ability / weapon skill id)": "command",
        "Database records (edit or add items, key items, titles, …)": "database",
        "Zone dialog (edit or add the lines a zone's NPCs say)": "zone_dialog",
        "Zone NPCs (rename or add a zone's NPCs)": "zone_npcs",
        "Zone events (cutscenes and dialogue on a zone's NPCs)": "zone_events",
    }[_choose("What type of content is being added?",
              ["Gear", "Mounts", "Entity (NPC / Monster / Object)",
               "NPC (costume: race + gear + weapons)",
               "Ability (recipe from the Ability Mixer / xi ability recipe)",
               "Spell menu record (a new spell id: name, help, MP, levels)",
               "Command menu record (a new job ability / weapon skill id)",
               "Database records (edit or add items, key items, titles, …)",
               "Zone dialog (edit or add the lines a zone's NPCs say)",
               "Zone NPCs (rename or add a zone's NPCs)",
               "Zone events (cutscenes and dialogue on a zone's NPCs)"])]
    # The baked NPC is placed at a custom entity model id, so it needs the entity tables.
    # Abilities take retail-range ids (job-ability / spell bands, weapon-skill dummies),
    # so the tables need no expansion.
    ready_key = "entity" if ctype == "npc" else ctype
    if ctype not in ("ability", *RESTORABLE_TYPES, *RECORD_TYPES) \
            and not ready.get(ready_key, True):
        hint = {"gear": "Run `xi ftable expand gear` (expands the FTABLE and patches "
                        "FFXiMain.dll)",
                "entity": "Run `xi ftable expand entity`",
                "mount": "The FTABLE is smaller than retail — check the install"}[ready_key]
        raise click.ClickException(
            f"The {'pivot folder' if pivot else 'base install'} isn't ready for {ctype} content "
            f"(see the report above). {hint}, then re-run `xi dats new`.")
    # If this project already has action(s) of the chosen type, use them to
    # default the prompts (slot, model id, paths, …) so re-running just tweaks.
    if ctype == "gear":
        # Gear can hold one action per slot (gear.<slug> / gear.<slug>.<slot>).
        prevs = [a for a in manifest.get("actions", [])
                 if a.get("type") == "gear"
                 and (a.get("id") == f"gear.{slug}"
                      or str(a.get("id", "")).startswith(f"gear.{slug}."))]
        if prevs:
            click.echo(click.style(
                "\n(Found existing gear action(s) in this project — defaults preloaded.)",
                fg="cyan"))
        new_actions = _wizard_gear(slug, prevs)
    else:
        prev = next((a for a in manifest.get("actions", []) if a.get("id") == f"{ctype}.{slug}"), None)
        if prev is not None:
            click.echo(click.style(f"\n(Found existing {ctype} action in this project — defaults preloaded.)",
                                   fg="cyan"))
        if ctype == "mount":
            new_actions = [_wizard_mount(slug, project, prev)]
        elif ctype == "npc":
            new_actions = [_wizard_npc(slug, prev)]
        elif ctype == "ability":
            new_actions = [_wizard_ability(slug, prev, manifest_path, manifest)]
        elif ctype in RECORD_TYPES:
            new_actions = [_wizard_record(ctype, slug, prev, manifest_path, manifest)]
        elif ctype == "database":
            new_actions = [_wizard_database(slug, prev, pivot)]
        elif ctype == "zone_dialog":
            new_actions = [_wizard_zone_dialog(slug, prev, pivot)]
        elif ctype == "zone_npcs":
            new_actions = [_wizard_zone_npcs(slug, prev, pivot)]
        elif ctype == "zone_events":
            new_actions = [_wizard_zone_events(slug, prev, pivot)]
        else:
            new_actions = [_wizard_entity(slug, prev)]

    by_id = {a.get("id"): a for a in manifest.get("actions", [])}
    for action in new_actions:
        if action.get("type") not in ("ability", *RESTORABLE_TYPES, *RECORD_TYPES):
            # Allocated deterministically from the definition; abilities and menu
            # records are decided by the build against the live tables instead.
            action["result"] = _plan_result(action)  # record the allocation inline (same as build)
        prior_targets = ((by_id.get(action["id"]) or {}).get("result") or {}).get("targets")
        if prior_targets:  # keep the record of where it was last built into
            action.setdefault("result", {})["targets"] = prior_targets
        _add_or_replace_action(manifest, action, replace=True)
    _write_manifest(manifest_path, manifest)
    _rule()
    wrote = ", ".join(a["id"] for a in new_actions)
    click.echo(click.style(f"\n✓ Wrote {wrote} -> {manifest_path}", fg="green"))

    if click.confirm("\n>> Would you like to build this project now?", default=True):
        _rule()
        click.get_current_context().invoke(build_cmd, project=slug, pivot=pivot)
    else:
        click.echo(f"Run it later with:  xi dats build --project {slug}" + (" --pivot" if pivot else ""))

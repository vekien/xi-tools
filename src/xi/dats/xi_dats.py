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
    for action in manifest["actions"]:
        if isinstance(action, dict):
            action.pop("op", None)  # `op` is unused — dropped on read so it's shed on next write
    return manifest


def _write_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
_TARGET_LABELS = {"pivot": "Pivot overlay", "hd": "HD overlay", "dir": "Base install"}


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


def patch_launcher_tables(file_id: int, ftval: int, rom: int) -> None:
    """Register file_id -> placement in the active target's base + ROM{rom} tables.
    A target with no FTABLE (e.g. the HD overlay) is a DAT-only drop — the file_id
    registration comes from another target's tables — so skip patching there."""
    root = _active_build_root()
    if not (root / "FTABLE.DAT").exists():
        return
    _patch_raw_table(root / "FTABLE.DAT", root / "VTABLE.DAT", file_id, ftval, rom)
    if rom != 1:
        _patch_raw_table(root / f"ROM{rom}" / f"FTABLE{rom}.DAT",
                         root / f"ROM{rom}" / f"VTABLE{rom}.DAT", file_id, ftval, rom)


def _patch_active_tables(file_id: int, ftval: int, rom: int) -> None:
    patch_launcher_tables(file_id, ftval, rom)


def _active_placement(file_id: int) -> str | None:
    """What file_id is registered to in the active target's tables (collision check)."""
    from xi.ftable.xi_core import resolve_dat
    root = _active_build_root()
    ft, vt = root / "FTABLE.DAT", root / "VTABLE.DAT"
    if not ft.exists() or not vt.exists():
        return None
    dat, _ = resolve_dat(ft.read_bytes(), vt.read_bytes(), file_id)
    return dat


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
    if source.name == "zone-changes.json" or "placements" in data or "vfx" in data or "zone" in data:
        return "zone", data
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


def _place_raw_dat_in_build(src: Path, place_rel: str, file_id: int, *, force: bool,
                            action_id: str, dry_run: bool = False) -> dict:
    """Copy ``src`` verbatim into the active target at ``place_rel`` and register
    ``file_id`` -> that placement in its base + ROM{rom} tables. When ``dry_run``
    nothing is written: the plan (incl. any occupant of file_id) is returned and
    a collision is reported rather than raised."""
    place_rel = _rom_rel(place_rel)
    rom, subdir, file_idx = _parse_rom_placement(place_rel)
    out_dat = _pivot_build_dir() / Path(*place_rel.split("/"))
    ftval = (subdir << 7) | (file_idx & 0x7F)
    existing = _active_placement(file_id)
    collision = existing is not None and existing.upper() != place_rel.upper()
    if collision and not force and not dry_run:
        raise click.ClickException(
            f"{action_id}: file_id {file_id} is already registered to {existing} in the "
            f"target — this build would repoint it to {place_rel}. Pass "
            "--force if that's intentional.")

    if not dry_run:
        out_dat.parent.mkdir(parents=True, exist_ok=True)
        out_dat.write_bytes(src.read_bytes())
        _patch_active_tables(file_id, ftval, rom)

    result = {"output": str(out_dat), "target_dat": place_rel, "file_id": file_id,
              "rom": rom, "source": str(src), "bytes": src.stat().st_size,
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


@click.group("dats")
def group():
    """Build reproducible DAT package trees from manifest JSON."""
    pass


@group.command("json")
@click.argument("manifest", type=click.Path(path_type=Path), default=DEFAULT_MANIFEST, required=False)
@click.option("--output", "output", type=click.Path(path_type=Path), default=None)
def json_cmd(manifest: Path, output: Path | None):
    """Print a normalized dats manifest as JSON."""
    _dump_json(_read_manifest(manifest), output)


@group.command("prepare")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.argument("manifest", type=click.Path(path_type=Path), default=None, required=False)
@click.option("--project", default=None, help="Manifest name — writes to dats/<project>.json instead of dats/update.json.")
@click.option("--id", "action_id", default=None, help="Stable action id to use in the manifest.")
@click.option("--type", "action_type", default=None, help="Force the action type instead of inferring it.")
@click.option("--target", default=None, help="Target ROM DAT path when the source does not contain one.")
@click.option("--hd/--no-hd", default=True, show_default=True, help="For zone actions, also build dats/ffxi-hd output.")
@click.option("--replace", is_flag=True, help="Replace an existing action with the same id.")
def prepare_cmd(source: Path, manifest: Path | None, project: str | None, action_id: str | None, action_type: str | None,
                target: str | None, hd: bool, replace: bool):
    """Add an exported/import JSON or zone-changes.json to a dats package."""
    manifest = _resolve_manifest_path(manifest, project)
    manifest_data = _read_manifest(manifest)
    if project:
        manifest_data["name"] = project
    kind, data = _detect_source_type(source, action_type)
    resource_root = _resource_root(manifest, manifest_data)

    if kind == "zone":
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


def _result_rows(manifest_data: dict) -> list[tuple[str, ...]]:
    """Rows for the results table, read from each action's inline ``result``
    (falling back to its definition where a build hasn't recorded one yet)."""
    rows = []
    for action in manifest_data.get("actions", []):
        aid = action.get("id", "?")
        typ = action.get("type", "?")
        res = action.get("result") or {}
        if typ == "gear":
            placements = res.get("placements") or []
            model_id = res.get("model_id", (action.get("model") or {}).get("model_id"))
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
def build_cmd(manifest: Path | None, project: str | None, only: tuple[str, ...], verbose: bool,
              force: bool, dry_run: bool, dry_note: bool = True):
    """Build a manifest directly into the base install (FFXI_DIR).

    DATs are placed and their file_ids registered straight into the base install's
    tables (which must already exist + be expanded); the tables are backed up once
    to `<name>.base` before the first patch. (XIPivot can't overlay the root FTABLE,
    so the base install is the only target where custom gear/entity file_ids resolve.)
    """
    import xi.xi_config as cfg

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

    # mesh + the verbatim-placement types write DATs + table patches directly into
    # the base install (the only target whose root FTABLE the client actually reads).
    pack_actions = [a for a in active_actions
                    if a.get("type") in ("mesh", "entity", "gear", "mount")]
    target_roots = [("dir", _target_root("dir"))]
    if pack_actions:
        n_with_tables = 0
        for name, root in target_roots:
            has_tables = _ftable_entries(root / "FTABLE.DAT") > 0
            n_with_tables += has_tables
            suffix = "" if has_tables else "  (DAT-only — no FTABLE here)"
            click.echo(f"Target: {_TARGET_LABELS[name]} -> {root}{suffix}")
        if n_with_tables == 0:
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
        raise click.ClickException(
            f"{action.get('id')}: build support for type {kind!r} is not implemented yet.")

    results = []
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
        results.append(result)
        # Record the allocation INLINE on the action (idempotent — overwrites the
        # same key), so the manifest itself is the single source of truth. Track
        # every target the DATs have been built into (union across builds).
        if not dry_run and kind != "zone":
            prior = set((action.get("result") or {}).get("targets") or [])
            built = prior | {name for name, _ in target_roots}
            res = _plan_result(action)
            res["targets"] = [n for n in ("pivot", "dir", "hd") if n in built]
            action["result"] = res

    _set_target_root(None)

    if dry_run:
        _print_placements(results, "Planned actions:")
        if pack_actions:
            click.echo("\nWould build into: " + ", ".join(str(r) for _n, r in target_roots))
        if dry_note:
            click.echo(click.style("\nDry run — nothing was written. Re-run without --dry-run to build.",
                                   fg="cyan"))
        return

    # Persist the inline results back into the manifest (self-describing, idempotent).
    _write_manifest(manifest, manifest_data)

    # The client reads lookup tables through the XIPivot overlay (FFXI_PIVOT_DIR)
    # when set — it shadows the base install's FTABLE/VTABLE. Propagate the custom
    # region (the file_ids we just registered) into any table the pivot overrides,
    # so the new gear/entity models resolve there too and the sizes stay uniform
    # (a size mismatch crashes the client). Retail-range pivot entries are kept.
    if pack_actions:
        from xi.ftable.xi_expand import sync_pivot_from_base, pivot_root
        if pivot_root():
            click.echo(f"\nSyncing pivot overlay tables ({pivot_root()}) ...")
            synced = sync_pivot_from_base()
            click.echo(f"  synced {len(synced)} pivot table(s)" if synced
                       else "  (pivot tables already in sync)")

    click.echo()
    if results:
        _print_placements(results, "Placed DATs:")
    for _name, root in target_roots:
        click.echo(click.style(f"\n✓ Built into {root}", fg="green"))


def _project_built_targets(manifest_data: dict) -> list[str]:
    """Targets any of the project's actions have been built into (union), in
    canonical order — read from each action's recorded ``result.targets``."""
    built: set[str] = set()
    for a in manifest_data.get("actions", []):
        built.update((a.get("result") or {}).get("targets") or [])
    return [n for n in ("pivot", "dir", "hd") if n in built]


def _action_placements(action: dict) -> list[tuple[int, str]]:
    """(file_id, ROM-relative DAT) for each thing an action placed, from its
    inline result — one per race for gear, one otherwise."""
    res = action.get("result") or {}
    out: list[tuple[int, str]] = []
    if action.get("type") == "gear":
        for p in res.get("placements", []):
            if p.get("file_id") is not None and p.get("dat"):
                out.append((int(p["file_id"]), _rom_rel(p["dat"])))
    elif res.get("file_id") is not None and res.get("dat"):
        out.append((int(res["file_id"]), _rom_rel(res["dat"])))
    return out


def _unregister_file_id(root: Path, file_id: int, rom: int) -> bool:
    """Clear a file_id's entry (ftval + vtable version -> 0, so it resolves to
    nothing) in ``root``'s base + ROM{rom} tables. No-op (returns False) if the
    target has no FTABLE (a DAT-only overlay)."""
    if not (root / "FTABLE.DAT").exists():
        return False
    _patch_raw_table(root / "FTABLE.DAT", root / "VTABLE.DAT", file_id, 0, 0)
    if rom != 1:
        ft, vt = root / f"ROM{rom}" / f"FTABLE{rom}.DAT", root / f"ROM{rom}" / f"VTABLE{rom}.DAT"
        if ft.exists():
            _patch_raw_table(ft, vt, file_id, 0, 0)
    return True


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


def _project_dat_rels(manifest_data: dict) -> tuple[list[str], bool]:
    """ROM-relative DATs a project's actions produced (from inline results), plus
    whether it has any mount action (whose d_msg string DATs must ship too)."""
    rels: list[str] = []
    has_mount = False
    for action in manifest_data.get("actions", []):
        typ = action.get("type")
        res = action.get("result") or {}
        if typ == "gear":
            rels += [_rom_rel(p["dat"]) for p in res.get("placements", []) if p.get("dat")]
        elif res.get("dat"):
            rels.append(_rom_rel(res["dat"]))
        if typ == "mount":
            has_mount = True
    return rels, has_mount


@group.command("package")
@click.argument("project", required=False, default=None)
@click.option("--from", "source", type=click.Choice(["dir", "pivot", "hd"]), default="dir", show_default=True,
              help="Read the built DATs + tables from the base install ('dir', default), "
                   "FFXI_PIVOT_DIR ('pivot'), or FFXI_HD_DIR ('hd').")
@click.option("--output", "output", type=click.Path(path_type=Path), default=None,
              help="Output zip path (default dats/packages/<project>.zip).")
def package_cmd(project: str | None, source: str, output: Path | None):
    """Zip a project's built DATs + F/V tables into a distributable overlay pack.

    With no PROJECT, lists the dats/*.json projects to pick from. Reads from the base
    install by default (where builds land); collects every DAT the project's actions
    placed (from each action's inline result), the mount string DATs for any mount
    actions, and the full FTABLE/VTABLE set — into a single zip laid out ROM-relative.
    """
    import zipfile

    if not project:
        project = _pick_project("package")
    manifest_path = _resolve_manifest_path(None, project)
    if not manifest_path.exists():
        raise click.ClickException(f"No manifest at {manifest_path}.")
    manifest_data = _read_manifest(manifest_path)
    root = _target_root(source)
    if not (root / "FTABLE.DAT").exists():
        raise click.ClickException(f"No FTABLE.DAT under {root}.")

    dat_rels, has_mount = _project_dat_rels(manifest_data)
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

    Also stages the XIPivot overlay's synced F/V tables (from FFXI_PIVOT_DIR),
    since the client reads those over the base install's tables — the build must
    carry them or the overlay shadows the base with stale/retail tables and
    crashes.
    """
    if not project:
        project = _pick_project("release")
    manifest_path = _resolve_manifest_path(None, project)
    if not manifest_path.exists():
        raise click.ClickException(f"No manifest at {manifest_path}.")
    manifest_data = _read_manifest(manifest_path)
    src_root = _target_root("dir")  # builds land in the base install

    if release_root is None:
        click.echo("\n>> Launcher build release folder")
        release_root = Path(click.prompt("Enter path").strip().strip('"'))
    dest_base = release_root / _LAUNCHER_GAME_SUBPATH

    # What to ship: the project's DATs (+ mount d_msg), the full F/V table set, and
    # the patched DLL (the model->file_id map the client reads at boot).
    dat_rels, has_mount = _project_dat_rels(manifest_data)
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

    # Also stage the XIPivot overlay's F/V tables. The client reads these OVER the
    # base install's tables (redirect_fopens), so the build must carry the same
    # synced/expanded copies or the overlay shadows the base with stale tables and
    # the client crashes on a size mismatch. Mirrors the pivot folder layout under
    # <release>\Ashita\polplugins\DATs\catseyexi (created here, full path).
    from xi.xi_config import FFXI_PIVOT_DIR
    pivot_copied = 0
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

    n_tables = len([r for r in rels if r in table_rels])
    n_dat = len([r for r in rels if r in dat_rels])
    dll = " + FFXiMain.dll" if (not no_dll and "FFXiMain.dll" in rels) else ""
    pivot = f" + {pivot_copied} pivot table(s)" if pivot_copied else ""
    click.echo(click.style(
        f"✓ Released {n_dat} DAT(s) + {n_tables} F/V table file(s){dll}{pivot} -> {dest_base}",
        fg="green"))


@group.command("undo")
@click.argument("project", required=False, default=None)
@click.option("--yes", is_flag=True, default=False, help="Skip the confirmation prompt.")
@click.option("--keep-json", is_flag=True, default=False,
              help="Undo the built DATs + table entries but keep the manifest JSON.")
def undo_cmd(project: str | None, yes: bool, keep_json: bool):
    """Undo a project's build and remove it.

    With no PROJECT, lists the dats/*.json projects to pick from. For every target
    the project was built into (recorded on each action's `result.targets`): delete
    the DAT files it placed, clear their file_id entries from that target's
    FTABLE/VTABLE, and (for mounts) blank the name/help/key-item strings. Then delete
    the `dats/<project>.json` manifest unless `--keep-json`.
    """
    from xi.mount import xi_core as M

    if not project:
        project = _pick_project("undo")

    manifest_path = _resolve_manifest_path(None, project)
    if not manifest_path.exists():
        raise click.ClickException(f"No manifest at {manifest_path}.")
    manifest_data = _read_manifest(manifest_path)
    actions = manifest_data.get("actions", [])
    built = _project_built_targets(manifest_data)
    n_placements = sum(len(_action_placements(a)) for a in actions)

    click.echo(f"\nUndo {manifest_path.stem}:")
    click.echo(f"  built into: {', '.join(built) if built else '(none recorded — nothing to remove in-game)'}")
    click.echo(f"  DATs:       {n_placements} file(s) in each target")
    click.echo(f"  manifest:   {manifest_path}" + ("  (kept)" if keep_json else "  (deleted)"))
    if not yes and not click.confirm("\nProceed?", default=False):
        click.echo("Aborted — nothing changed.")
        return

    removed = cleared = 0
    for name in built:
        root = _target_root(name)
        for action in actions:
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
        click.echo(f"  ✓ {name}: DATs deleted + table entries cleared")

    if not keep_json:
        manifest_path.unlink()

    tail = "" if keep_json else f", removed {manifest_path.name}"
    click.echo(click.style(
        f"\n✓ Undone. Deleted {removed} DAT{'s' if removed != 1 else ''}, "
        f"cleared {cleared} table entr{'ies' if cleared != 1 else 'y'}{tail}.", fg="green"))


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
    return sorted({p.stem for p in d.glob("*.json")})


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


def _check_targets_ready() -> dict[str, bool]:
    """First step of the wizard: verify the base install's FTABLE and whether it's
    expanded for mounts / entity / gear (with sizes). Aborts when there's no FTABLE
    at all (nothing could register file_ids); otherwise returns per-type readiness
    so the wizard can refuse a type whose tables/DLL aren't ready — instead of
    collecting every answer and only failing at build time. Builds patch this
    table directly (no seeding)."""
    root = _target_root("dir")
    entries = _ftable_entries(root / "FTABLE.DAT")
    click.echo(f"\nBase install (FFXI_DIR): {root}")
    if entries == 0:
        raise click.ClickException(
            f"No FTABLE.DAT in {root} — builds register file_ids there, so nothing "
            "can be built. Check FFXI_DIR points at the game install, then re-run "
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
def new_cmd(project: str | None):
    """Interactively inject prebuilt DAT(s) at new model ids.

    A wizard for the "I already have the DATs, just place them at new model ids"
    case: no `mesh export` / GLB rebuild. Asks for the content type (gear /
    mount / entity), collects the source DAT(s), destination, and model id(s),
    writes a `dats/<project>.json` manifest action, then offers to build it.
    """
    click.echo("\nWelcome to the Dat Modification wizard")
    _rule()
    ready = _check_targets_ready()
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
    }[_choose("What type of content is being added?",
              ["Gear", "Mounts", "Entity (NPC / Monster / Object)",
               "NPC (costume: race + gear + weapons)"])]
    # The baked NPC is placed at a custom entity model id, so it needs the entity tables.
    ready_key = "entity" if ctype == "npc" else ctype
    if not ready.get(ready_key, True):
        hint = {"gear": "Run `xi ftable expand gear` (expands the FTABLE and patches "
                        "FFXiMain.dll)",
                "entity": "Run `xi ftable expand entity`",
                "mount": "The FTABLE is smaller than retail — check the install"}[ready_key]
        raise click.ClickException(
            f"The base install isn't ready for {ctype} content (see the report above). "
            f"{hint}, then re-run `xi dats new`.")
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
        else:
            new_actions = [_wizard_entity(slug, prev)]

    by_id = {a.get("id"): a for a in manifest.get("actions", [])}
    for action in new_actions:
        action["result"] = _plan_result(action)  # record the allocation inline (same as build)
        prior_targets = ((by_id.get(action["id"]) or {}).get("result") or {}).get("targets")
        if prior_targets:  # keep the record of where it was last built into
            action["result"]["targets"] = prior_targets
        _add_or_replace_action(manifest, action, replace=True)
    _write_manifest(manifest_path, manifest)
    _rule()
    wrote = ", ".join(a["id"] for a in new_actions)
    click.echo(click.style(f"\n✓ Wrote {wrote} -> {manifest_path}", fg="green"))

    if click.confirm("\n>> Would you like to build this project now?", default=True):
        _rule()
        click.get_current_context().invoke(build_cmd, project=slug)
    else:
        click.echo(f"Run it later with:  xi dats build --project {slug}")

"""Append-only refresh of xi-model-viewer ``ui/public/lists/*.json``.

Current shipped JSON is the base. These updaters only add missing entries
(and fill blank name keys); they never delete or rename curated labels.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from xi.mv import dat_index
from xi.xi_config import FFXI_DIR

# Viewer race id → (xi gear table key, look race byte)
RACE_MAP = {
    "HumeM": ("HumeMale", 1),
    "HumeF": ("HumeFemale", 2),
    "ElvaanM": ("ElvaanMale", 3),
    "ElvaanF": ("ElvaanFemale", 4),
    "Tarutaru": ("TaruMale", 5),
    "Mithra": ("Mithra", 7),
    "Galka": ("Galka", 8),
}
RACE_ALT = {"Tarutaru": ("TaruFemale", 6)}

# Viewer slot key → xi slot name
SLOT_MAP = {
    "face": "face",
    "head": "head",
    "body": "body",
    "hands": "hands",
    "legs": "legs",
    "feet": "feet",
    "main": "main",
    "sub": "sub",
    "range": "ranged",
}

SOUND_ROOTS = (
    "sound", "sound2", "sound3", "sound4", "sound5",
    "sound6", "sound7", "sound8", "sound9",
)

Report = dict[str, Any]

# Progress sink. Updaters call this with a short present-tense phrase ("reading
# race tables") before anything that takes more than a moment; the CLI prints it,
# library callers pass nothing and get silence.
Notify = Callable[[str], None]


def _noop(_msg: str) -> None:
    pass


def _scan_dats(notify: Notify) -> dict[str, Any]:
    """DAT header scan, announced only when the cache is cold (~20s the first time)."""
    if not dat_index.scan_dats.cache_info().currsize:
        notify("scanning DAT headers")
    return dat_index.scan_dats()


def _file_ids(notify: Notify) -> dict[str, int]:
    """Reverse file table (DAT → file_id), announced only when cold."""
    if not dat_index.file_id_by_dat.cache_info().currsize:
        notify("inverting the file table")
    return dat_index.file_id_by_dat()


def _dats_by_file_id(notify: Notify) -> dict[int, str]:
    """Forward file table (file_id → DAT), announced only when cold."""
    if not dat_index.dat_by_file_id.cache_info().currsize:
        notify("reading the file table")
    return dat_index.dat_by_file_id()


def default_lists_dir() -> Path:
    """Default output: ``<XI_TOOLS_DIR>/mv/lists`` (created on write)."""
    from xi.xi_config import XI_TOOLS_DIR
    return Path(XI_TOOLS_DIR) / "mv" / "lists"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _detect_indent(path: Path, default: int = 1) -> int:
    """Indent width the file already uses, so a rewrite is a content-only diff.

    These lists are hand-curated as much as generated; reflowing a file the
    updater has never touched buries the real change in whitespace noise.
    """
    try:
        with open(path, encoding="utf-8") as f:
            f.readline()  # opening brace / bracket
            second = f.readline()
    except OSError:
        return default
    stripped = second.lstrip(" ")
    width = len(second) - len(stripped)
    return width if stripped and 1 <= width <= 8 else default


def _write_json(path: Path, data: Any, *, dry_run: bool, indent: int | None = None) -> None:
    if indent is None:
        indent = _detect_indent(path)
    text = json.dumps(data, indent=indent, ensure_ascii=False) + "\n"
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _norm_dat(p: str) -> str:
    return p.replace("/", "\\").upper()


def _display_dat(p: str) -> str:
    """JSON path form used by characters.json (backslashes)."""
    return p.replace("/", "\\")


def _dat_id_suffix(dat: str) -> str:
    """``ROM\\28\\15.DAT`` → ``28/15``; ``ROM3\\9\\76.DAT`` → ``3:9/76`` style kept simple as path body."""
    s = dat.replace("\\", "/").replace(".DAT", "").replace(".dat", "")
    # strip leading ROM / ROMN
    m = re.match(r"^ROM(\d*)/(.+)$", s, re.I)
    if m:
        rom_n, rest = m.group(1), m.group(2)
        if rom_n:
            return f"ROM{rom_n}/{rest}"
        return rest
    return s


def _file_exists(dat_rel: str) -> bool:
    base = Path(FFXI_DIR)
    p = base / dat_rel.replace("\\", "/")
    if p.is_file():
        return True
    # case variants on Windows usually fine; try upper .DAT
    return p.with_suffix(".DAT").is_file() if p.suffix.lower() == ".dat" else False


# ── gear (characters.json) ───────────────────────────────────────────────────

def _path_out(ndat: str) -> str:
    """Normalize a FTABLE dat string to characters.json path form."""
    m = re.match(r"^(ROM\d*)[/\\](\d+)[/\\](\d+)\.DAT$", ndat, re.I)
    if m:
        return f"{m.group(1).upper()}\\{m.group(2)}\\{m.group(3)}.DAT"
    return _display_dat(ndat)


def _build_gear_maps(mid_cap: int = 1500, notify: Notify = _noop) -> dict[str, dict]:
    """Per viewer race/slot: ordered (mid, NORM_DAT) pairs + alt mid-by-dat.

    Walks FFXiMain race tables by model id (mid), resolves each through FTABLE.
    ``mid_cap`` drops table rows above the soft cap (default 1500).
    """
    from xi.gear.xi_core import RACE_TABLES, SLOTS, parse_race_table, slot_file_ids
    from xi.ftable.xi_core import load_all_tables, scan_file_ids

    notify("reading race tables")
    tables = load_all_tables()
    per_race: dict[str, dict[str, list]] = {}
    all_fids: set[int] = set()
    for race, raw in RACE_TABLES.items():
        parsed = parse_race_table(raw)
        per_race[race] = {}
        for slot in SLOTS:
            rows = [(m, f) for m, f in slot_file_ids(parsed[slot]) if m <= mid_cap]
            per_race[race][slot] = rows
            all_fids.update(fid for _, fid in rows)

    notify(f"resolving {len(all_fids):,} gear file ids")
    dat_by_fid = {e["file_id"]: e["dat"] for e in scan_file_ids(sorted(all_fids), tables)}

    def slot_pairs(xi_race: str) -> dict[str, list[tuple[int, str]]]:
        """{viewer_slot: [(mid, NORM_DAT), ...]} sorted by mid (unique mid)."""
        out: dict[str, list[tuple[int, str]]] = {}
        for view_slot, xi_slot in SLOT_MAP.items():
            pairs: list[tuple[int, str]] = []
            seen_mid: set[int] = set()
            for model_id, file_id in sorted(per_race[xi_race][xi_slot], key=lambda t: t[0]):
                if model_id in seen_mid:
                    continue
                dat = dat_by_fid.get(file_id)
                if not dat:
                    continue
                pairs.append((model_id, _norm_dat(dat)))
                seen_mid.add(model_id)
            if pairs:
                out[view_slot] = pairs
        return out

    def slot_dat_to_mid(xi_race: str) -> dict[str, dict[str, int]]:
        """{viewer_slot: {NORM_DAT: lowest mid}} for midAlt join."""
        out: dict[str, dict[str, int]] = {}
        for view_slot, pairs in slot_pairs(xi_race).items():
            mapping: dict[str, int] = {}
            for mid, ndat in pairs:
                if ndat not in mapping or mid < mapping[ndat]:
                    mapping[ndat] = mid
            out[view_slot] = mapping
        return out

    out: dict[str, dict] = {}
    for race_id, (xi_race, look_race) in RACE_MAP.items():
        entry: dict = {
            "lookRace": look_race,
            "slots": slot_pairs(xi_race),
            "dat_mid": slot_dat_to_mid(xi_race),
        }
        alt = RACE_ALT.get(race_id)
        if alt:
            alt_race, alt_look = alt
            entry["lookRaceAlt"] = alt_look
            entry["slotsAlt"] = slot_pairs(alt_race)
            entry["dat_mid_alt"] = slot_dat_to_mid(alt_race)
        out[race_id] = entry
    return out


def update_gear(
    lists_dir: Path,
    *,
    dry_run: bool = False,
    base_dir: Path | None = None,
    mid_cap: int = 1500,
    notify: Notify = _noop,
) -> Report:
    path = lists_dir / "characters.json"
    src = path if path.is_file() else None
    if src is None and base_dir is not None:
        cand = base_dir / "characters.json"
        if cand.is_file():
            src = cand
    if src is None:
        return {
            "target": "gear",
            "file": str(path),
            "error": (
                f"characters.json not found in {lists_dir}"
                + (f" or base {base_dir}" if base_dir else "")
                + " — copy your base lists into mv/lists first (or pass --base)"
            ),
            "added": 0,
            "wrote": False,
        }
    data = _load_json(src)
    races = data.get("races") or []
    maps = _build_gear_maps(mid_cap=mid_cap, notify=notify)
    notify(f"comparing {len(races)} races against characters.json")

    added = 0
    skipped_missing = 0
    skipped_path_present = 0
    by_race: dict[str, int] = {}
    samples: list[str] = []

    for race in races:
        rid = race.get("id")
        gmap = maps.get(rid)
        if not gmap:
            continue
        slots = race.setdefault("slots", {})
        race_added = 0
        alt_dat_mid = (gmap.get("dat_mid_alt") or {})

        for view_slot, pairs in gmap["slots"].items():
            items = slots.setdefault(view_slot, [])
            existing_paths: set[str] = set()
            existing_mids: set[int] = set()
            for it in items:
                for p in it.get("paths") or []:
                    existing_paths.add(_norm_dat(p))
                # also accept path-like id tails (legacy "N:ROM\\a\\b.DAT")
                iid = str(it.get("id") or "")
                if "ROM" in iid.upper():
                    tail = iid.split(":", 1)[-1]
                    if "ROM" in tail.upper():
                        existing_paths.add(_norm_dat(tail if tail.upper().endswith(".DAT") else tail))
                mid = it.get("mid")
                if isinstance(mid, int):
                    existing_mids.add(mid)
                mid_alt = it.get("midAlt")
                if isinstance(mid_alt, int):
                    existing_mids.add(mid_alt)

            for mid, ndat in pairs:
                if mid in existing_mids:
                    continue
                if ndat in existing_paths:
                    # DAT already listed under another mid — don't duplicate the row
                    skipped_path_present += 1
                    existing_mids.add(mid)
                    continue

                path_out = _path_out(ndat)
                if not _file_exists(path_out):
                    skipped_missing += 1
                    continue

                suffix = _dat_id_suffix(path_out)
                idx = len(items)
                row: dict[str, Any] = {
                    "id": f"{idx}:{suffix}",
                    "label": f"NEW - mid {mid} ({suffix})",
                    "group": None,
                    "paths": [path_out],
                    "mid": mid,
                }
                alt_mid = (alt_dat_mid.get(view_slot) or {}).get(ndat)
                if alt_mid is not None and alt_mid != mid:
                    row["midAlt"] = alt_mid
                items.append(row)
                existing_paths.add(ndat)
                existing_mids.add(mid)
                added += 1
                race_added += 1
                if len(samples) < 12:
                    samples.append(f"{rid}/{view_slot} mid {mid} → {path_out}")

        if race_added:
            by_race[rid] = race_added

    if added and not dry_run:
        _write_json(path, data, dry_run=False)

    return {
        "target": "gear",
        "file": str(path),
        "added": added,
        "skipped_missing_file": skipped_missing,
        "skipped_path_already_listed": skipped_path_present,
        "by_race": by_race,
        "samples": samples,
        "mid_cap": mid_cap,
        "wrote": bool(added) and not dry_run,
    }


# ── music.json ───────────────────────────────────────────────────────────────

def update_music(
    lists_dir: Path, *, dry_run: bool = False, base_dir: Path | None = None,
    notify: Notify = _noop,
) -> Report:
    notify("scanning sound*/win/music")
    path = lists_dir / "music.json"
    src = path if path.is_file() else None
    if src is None and base_dir is not None and (base_dir / "music.json").is_file():
        src = base_dir / "music.json"
    data = _load_json(src) if src else {"names": {}}
    names: dict[str, str] = data.setdefault("names", {})
    base = Path(FFXI_DIR)

    found = 0
    added = 0
    for root in SOUND_ROOTS:
        d = base / root / "win" / "music" / "data"
        if not d.is_dir():
            continue
        for f in sorted(d.glob("music*.bgw")):
            m = re.match(r"^music(\d+)\.bgw$", f.name, re.I)
            if not m:
                continue
            num = int(m.group(1))
            key = f"{root}_{num:03d}"
            found += 1
            if key not in names:
                names[key] = f"NEW - {f.name}"
                added += 1

    if added and not dry_run:
        # keep keys sorted for diff friendliness
        data["names"] = dict(sorted(names.items(), key=lambda kv: kv[0]))
        _write_json(path, data, dry_run=False)

    return {
        "target": "music",
        "file": str(path),
        "scanned": found,
        "added": added,
        "total_names": len(names),
        "wrote": bool(added) and not dry_run,
    }


# ── sfx.json ─────────────────────────────────────────────────────────────────

def update_sfx(
    lists_dir: Path, *, dry_run: bool = False, base_dir: Path | None = None,
    notify: Notify = _noop,
) -> Report:
    notify("scanning sound*/win/se")
    path = lists_dir / "sfx.json"
    src = path if path.is_file() else None
    if src is None and base_dir is not None and (base_dir / "sfx.json").is_file():
        src = base_dir / "sfx.json"
    data = _load_json(src) if src else {"folders": {}, "names": {}}
    folders: dict[str, str] = data.setdefault("folders", {})
    names: dict[str, str] = data.setdefault("names", {})
    base = Path(FFXI_DIR)

    folder_hits = 0
    name_hits = 0
    folders_added = 0
    names_added = 0

    for root in SOUND_ROOTS:
        se_root = base / root / "win" / "se"
        if not se_root.is_dir():
            continue
        for folder in sorted(se_root.iterdir()):
            if not folder.is_dir():
                continue
            fname = folder.name  # se000
            if not re.match(r"^se\d+$", fname, re.I):
                continue
            fkey = f"{root}_{fname}"
            folder_hits += 1
            if fkey not in folders:
                folders[fkey] = f"NEW - {fname}"
                folders_added += 1
            for spw in sorted(folder.glob("se*.spw")):
                m = re.match(r"^se(\d+)\.spw$", spw.name, re.I)
                if not m:
                    continue
                sid = f"{int(m.group(1)):06d}"
                name_hits += 1
                if sid not in names:
                    names[sid] = f"NEW - {spw.name}"
                    names_added += 1

    changed = folders_added + names_added
    if changed and not dry_run:
        data["folders"] = dict(sorted(folders.items(), key=lambda kv: kv[0]))
        data["names"] = dict(sorted(names.items(), key=lambda kv: kv[0]))
        _write_json(path, data, dry_run=False)

    return {
        "target": "sfx",
        "file": str(path),
        "folders_scanned": folder_hits,
        "files_scanned": name_hits,
        "folders_added": folders_added,
        "names_added": names_added,
        "wrote": bool(changed) and not dry_run,
    }


# ── zone_music.json ──────────────────────────────────────────────────────────

_ZONE_ROW = re.compile(
    r"INSERT INTO `zone_settings` VALUES \((\d+),(\d+),'[^']*',(\d+),'([^']*)',"
    r"(\d+),(\d+),(\d+),(\d+),",
    re.I,
)


def _default_zone_settings_sql() -> Path | None:
    from xi.xi_config import XI_SERVER_DIR
    if XI_SERVER_DIR:
        p = Path(XI_SERVER_DIR) / "sql" / "zone_settings.sql"
        if p.is_file():
            return p
    return None


def update_zone_music(
    lists_dir: Path,
    *,
    sql_path: Path | None = None,
    dry_run: bool = False,
    base_dir: Path | None = None,
    notify: Notify = _noop,
) -> Report:
    sql_path = sql_path or _default_zone_settings_sql()
    if sql_path is None or not sql_path.is_file():
        return {
            "target": "zone-music",
            "error": "zone_settings.sql not found (set XI_SERVER_DIR or pass --sql)",
            "added": 0,
            "wrote": False,
        }

    path = lists_dir / "zone_music.json"
    music_names: dict[str, str] = {}
    for cand in (lists_dir / "music.json", (base_dir / "music.json") if base_dir else None):
        if cand is not None and cand.is_file():
            try:
                music_names = _load_json(cand).get("names") or {}
                break
            except Exception:
                music_names = {}

    base = Path(FFXI_DIR)
    # Later roots win (expansion override)
    notify("indexing BGM files")
    root_by_num: dict[str, dict[str, str]] = {}
    for root in SOUND_ROOTS:
        d = base / root / "win" / "music" / "data"
        if not d.is_dir():
            continue
        for f in d.glob("music*.bgw"):
            m = re.match(r"^music(\d+)\.bgw$", f.name, re.I)
            if m:
                root_by_num[str(int(m.group(1)))] = {"root": root, "file": f.name}

    def track(bgm_id: int) -> dict | None:
        if not bgm_id:
            return None
        hit = root_by_num.get(str(bgm_id))
        if not hit:
            return {"id": bgm_id, "root": None, "file": None, "name": None, "missing": True}
        key = f"{hit['root']}_{bgm_id:03d}"
        return {
            "id": bgm_id,
            "root": hit["root"],
            "file": hit["file"],
            "name": music_names.get(key),
        }

    notify("reading zone_settings.sql")
    sql = sql_path.read_text(encoding="utf-8", errors="replace")
    out: dict[str, Any] = {}
    rows = 0
    missing = 0
    for m in _ZONE_ROW.finditer(sql):
        zoneid, _zt, _port, raw_name, day, night, solo, party = m.groups()
        rows += 1
        entry = {
            "name": raw_name.replace("_", " "),
            "day": track(int(day)),
            "night": track(int(night)),
            "battleSolo": track(int(solo)),
            "battleParty": track(int(party)),
        }
        for t in (entry["day"], entry["night"], entry["battleSolo"], entry["battleParty"]):
            if t and t.get("missing"):
                missing += 1
        out[zoneid] = entry

    # Full rebuild from SQL (authoritative) — not append-only merge of stale ids.
    # Still preserves "base" semantics: SQL is the source of truth for zone BGM.
    if not dry_run:
        _write_json(path, out, dry_run=False)

    return {
        "target": "zone-music",
        "file": str(path),
        "sql": str(sql_path),
        "zones": rows,
        "bgm_files": len(root_by_num),
        "missing_ids": missing,
        "wrote": not dry_run,
    }


# ── effects.json ─────────────────────────────────────────────────────────────

# spell_list.@SKILL_* → effects.json category id (Altana buckets).
_SKILL_TO_CAT = {
    "SKILL_DIVINE": "WhiteMagic",
    "SKILL_HEALING": "WhiteMagic",
    "SKILL_ENHANCING": "WhiteMagic",
    "SKILL_ENFEEBLING": "WhiteMagic",
    "SKILL_ELEMENTAL": "BlackMagic",
    "SKILL_DARK": "BlackMagic",
    "SKILL_SUMMONING": "SummoningMagic",
    "SKILL_NINJUTSU": "Ninjutsu",
    "SKILL_SINGING": "Song",
    "SKILL_BLUE": "BlueMagic",
    "SKILL_GEOMANCY": "Geomancer",
}

# VFX file_id bands. Each is `offset + animation`, and each was read off the
# FTABLE itself: walking file_ids 0..12000 and classifying what each one resolves
# to leaves contiguous runs of particle/routine DATs with hard edges.
#
#   spells          2800 (0xAF0) .. 4405   — xi.spell, SpellTables.h kFileTableOffset
#   job abilities   4412 .. 4750           — abilities.animation
#   weapon skills   4912 .. 5157           — weapon_skills.animation
#
# Anchors: ability anim 0 = Berserk → 4412 → ROM/15/89 'bers'; anim 34 Hundred
# Fists → 'hyak'; anim 338 Majesty → 'pld_'. 260 of the 261 distinct ability
# anims and 206 of the 207 weapon-skill anims land on a particle DAT, so both
# offsets hold across their whole range.
_ABILITY_FILE_OFFSET = 4412
_ABILITY_ANIM_MAX = 338  # band ends at file_id 4750

_WS_FILE_OFFSET = 4912
_WS_ANIM_MAX = 245  # band ends at file_id 5157

# `mob_skills.mob_anim_id` is deliberately absent: it indexes an animation inside
# the monster's own model DAT, not a standalone VFX file. Same for
# `item_usable.animation` — that is the player's use motion (it resolves into the
# gear model band), not an effect file.


def _norm_effect_path(p: str) -> str:
    return p.replace("\\", "/").upper()


def _display_effect_path(p: str) -> str:
    """effects.json uses forward-slash ROM paths."""
    return p.replace("\\", "/")


def _default_spell_list_sql() -> Path | None:
    from xi.xi_config import XI_SERVER_DIR
    if XI_SERVER_DIR:
        p = Path(XI_SERVER_DIR) / "sql" / "spell_list.sql"
        if p.is_file():
            return p
    return None


def _default_abilities_sql() -> Path | None:
    from xi.xi_config import XI_SERVER_DIR
    if XI_SERVER_DIR:
        p = Path(XI_SERVER_DIR) / "sql" / "abilities.sql"
        if p.is_file():
            return p
    return None


def _parse_spell_skills(sql_path: Path | None) -> dict[int, str]:
    """spellid → category id from spell_list.sql @SKILL_* macros."""
    if sql_path is None or not sql_path.is_file():
        return {}
    text = sql_path.read_text(encoding="utf-8", errors="replace")
    # INSERT … VALUES (spellid,'name',0x…,group,family,@ELEMENT_*,zonemisc,validTargets,@SKILL_*,…
    row = re.compile(
        r"INSERT INTO `spell_list` VALUES \((\d+),'[^']*',[^,]*,\d+,\d+,"
        r"@ELEMENT_\w+,\d+,\d+,@(SKILL_\w+),",
        re.I,
    )
    out: dict[int, str] = {}
    for m in row.finditer(text):
        sid = int(m.group(1))
        skill = m.group(2).upper()
        cat = _SKILL_TO_CAT.get(skill)
        if cat:
            out[sid] = cat
    return out


def _parse_ability_anims(sql_path: Path | None) -> list[tuple[int, str, int]]:
    """(abilityId, name, animation) from abilities.sql — animation is the 10th value."""
    if sql_path is None or not sql_path.is_file():
        return []
    text = sql_path.read_text(encoding="utf-8", errors="replace")
    # abilityId,name,job,level,validTarget,recast,?,message,?,animation,animationTime,…
    row = re.compile(
        r"INSERT INTO `abilities` VALUES \((\d+),'([^']*)',\d+,\d+,\d+,\d+,\d+,\d+,\d+,(\d+),",
        re.I,
    )
    out: list[tuple[int, str, int]] = []
    for m in row.finditer(text):
        aid, name, anim = int(m.group(1)), m.group(2), int(m.group(3))
        out.append((aid, name, anim))
    return out


def _title_ability(name: str) -> str:
    return " ".join(p.capitalize() for p in name.replace("_", " ").split())


def _ensure_effect_category(data: dict, cat_id: str, label: str | None = None) -> dict:
    cats = data.setdefault("categories", [])
    for c in cats:
        if c.get("id") == cat_id:
            return c
    cat = {"id": cat_id, "label": label or cat_id, "entries": []}
    cats.append(cat)
    return cat


def update_effects(
    lists_dir: Path,
    *,
    dry_run: bool = False,
    base_dir: Path | None = None,
    spell_sql: Path | None = None,
    abilities_sql: Path | None = None,
    scan_spell_band: bool = True,
    notify: Notify = _noop,
) -> Report:
    """Append missing effect DATs from spell/ability animation → file_id → FTABLE.

    Spells: ``file_id = 0xAF0 + animation`` (xi.spell), names from d_msg, category
    from spell_list skill when SQL is available.

    Abilities: ``file_id = 4412 + animation`` across the whole 0–338 band.
    Weapon skills: ``file_id = 4912 + animation`` (weapon_skills.sql).

    Optional: scan the spell band 2800–4405 for effect paths not already listed
    → Unknown Effects.
    """
    path = lists_dir / "effects.json"
    src = path if path.is_file() else None
    if src is None and base_dir is not None and (base_dir / "effects.json").is_file():
        src = base_dir / "effects.json"
    if src is None:
        return {
            "target": "effects",
            "file": str(path),
            "error": (
                f"effects.json not found in {lists_dir}"
                + (f" or base {base_dir}" if base_dir else "")
                + " — copy base lists into mv/lists first"
            ),
            "added": 0,
            "wrote": False,
        }

    data = _load_json(src)
    known: set[str] = set()
    for cat in data.get("categories") or []:
        for e in cat.get("entries") or []:
            if e.get("path"):
                known.add(_norm_effect_path(e["path"]))

    spell_sql = spell_sql or _default_spell_list_sql()
    abilities_sql = abilities_sql or _default_abilities_sql()
    skill_cat = _parse_spell_skills(spell_sql)

    added = 0
    by_cat: dict[str, int] = {}
    samples: list[str] = []

    # ── spells (high confidence) ────────────────────────────────────────────
    from xi.spell.xi_spell import spell_catalog
    from xi.ftable.xi_core import load_all_tables, scan_file_ids

    notify("resolving spell animations")
    for sp in spell_catalog():
        dat = sp.get("dat")
        if not dat:
            continue
        nd = _norm_effect_path(dat)
        if nd in known:
            continue
        path_out = _display_effect_path(dat)
        if not _file_exists(path_out):
            continue
        cat_id = skill_cat.get(sp["index"], "Unknown Effects")
        label = {
            "WhiteMagic": "White Magic",
            "BlackMagic": "Black Magic",
            "SummoningMagic": "Summoning Magic",
            "BlueMagic": "Blue Magic",
            "Unknown Effects": "Unknown Effects",
        }.get(cat_id, cat_id)
        cat = _ensure_effect_category(data, cat_id, label)
        entry = {
            "name": sp["name"],
            "path": path_out,
            "fileId": sp["fileIndex"],
            "spellId": sp["index"],
            "animId": sp["animIndex"],
        }
        cat.setdefault("entries", []).append(entry)
        known.add(nd)
        added += 1
        by_cat[cat_id] = by_cat.get(cat_id, 0) + 1
        if len(samples) < 12:
            samples.append(f"spell {sp['index']} {sp['name']} fid {sp['fileIndex']} → {path_out}")

    # ── job abilities ───────────────────────────────────────────────────────
    notify("resolving job-ability animations")
    tables = load_all_tables()
    abil_rows = _parse_ability_anims(abilities_sql)
    if abil_rows:
        fids = sorted({
            _ABILITY_FILE_OFFSET + anim
            for _, _, anim in abil_rows
            if 0 <= anim <= _ABILITY_ANIM_MAX
        })
        dat_by = {h["file_id"]: h["dat"] for h in scan_file_ids(fids, tables)} if fids else {}
        cat = _ensure_effect_category(data, "Ability", "Ability")
        for aid, name, anim in abil_rows:
            if anim < 0 or anim > _ABILITY_ANIM_MAX:
                continue
            fid = _ABILITY_FILE_OFFSET + anim
            dat = dat_by.get(fid)
            if not dat:
                continue
            nd = _norm_effect_path(dat)
            if nd in known:
                continue
            path_out = _display_effect_path(dat)
            if not _file_exists(path_out):
                continue
            entry = {
                "name": _title_ability(name),
                "path": path_out,
                "fileId": fid,
                "abilityId": aid,
                "animId": anim,
            }
            cat.setdefault("entries", []).append(entry)
            known.add(nd)
            added += 1
            by_cat["Ability"] = by_cat.get("Ability", 0) + 1
            if len(samples) < 12:
                samples.append(f"ability {aid} {name} fid {fid} → {path_out}")

    # ── weapon skills ───────────────────────────────────────────────────────
    from xi.mv.server_names import weapon_skill_anims

    notify("resolving weapon-skill animations")
    ws_rows = weapon_skill_anims()
    if ws_rows:
        fids = sorted({
            _WS_FILE_OFFSET + anim for _, _, anim in ws_rows if 0 <= anim <= _WS_ANIM_MAX
        })
        dat_by = {h["file_id"]: h["dat"] for h in scan_file_ids(fids, tables)} if fids else {}
        cat = _ensure_effect_category(data, "WeaponSkill", "Weapon Skill")
        for wsid, name, anim in ws_rows:
            if anim < 0 or anim > _WS_ANIM_MAX:
                continue
            fid = _WS_FILE_OFFSET + anim
            dat = dat_by.get(fid)
            if not dat:
                continue
            nd = _norm_effect_path(dat)
            if nd in known:
                continue
            path_out = _display_effect_path(dat)
            if not _file_exists(path_out):
                continue
            cat.setdefault("entries", []).append({
                "name": name,
                "path": path_out,
                "fileId": fid,
                "weaponSkillId": wsid,
                "animId": anim,
            })
            known.add(nd)
            added += 1
            by_cat["WeaponSkill"] = by_cat.get("WeaponSkill", 0) + 1
            if len(samples) < 12:
                samples.append(f"ws {wsid} {name} fid {fid} → {path_out}")

    # ── FTABLE spell band scan for orphans ──────────────────────────────────
    scanned = 0
    if scan_spell_band:
        # 2800..4405 — the full contiguous spell/VFX run in the FTABLE, not just
        # the first 1200 ids the earlier pass covered.
        dat_scan = _scan_dats(notify)
        notify("sweeping the spell band for orphans")
        band = list(range(0xAF0, 4406))
        hits = scan_file_ids(band, tables)
        scanned = len(hits)
        cat = _ensure_effect_category(data, "Unknown Effects", "Unknown Effects")
        for h in hits:
            dat = h.get("dat")
            if not dat:
                continue
            nd = _norm_effect_path(dat)
            if nd in known:
                continue
            path_out = _display_effect_path(dat)
            if not _file_exists(path_out):
                continue
            # The band is VFX-dedicated, but a handful of ids in it point at
            # models or menus — confirm the sections before listing, and use the
            # DAT's own FourCC as the label so the row is identifiable.
            info = dat_scan.get(dat_index.norm(dat))
            if info is None or not info.is_effect:
                continue
            fid = h["file_id"]
            tag = (info.fourcc or "").strip(". ")
            entry = {
                "name": f"NEW - {tag} (fileId {fid})" if tag else f"NEW - fileId {fid}",
                "path": path_out,
                "fileId": fid,
                "fourcc": info.fourcc,
            }
            cat.setdefault("entries", []).append(entry)
            known.add(nd)
            added += 1
            by_cat["Unknown Effects"] = by_cat.get("Unknown Effects", 0) + 1
            if len(samples) < 12:
                samples.append(f"scan fid {fid} → {path_out}")

    if added and not dry_run:
        _write_json(path, data, dry_run=False)

    return {
        "target": "effects",
        "file": str(path),
        "added": added,
        "by_cat": by_cat,
        "spell_sql": str(spell_sql) if spell_sql else None,
        "scanned_band": scanned,
        "samples": samples,
        "wrote": bool(added) and not dry_run,
    }


# ── gear sets (characters.json `set`) ────────────────────────────────────────

_GEAR_SETS_PATH = Path(__file__).with_name("gear_sets.json")

# Rows whose label is a placeholder rather than an item name.
_NOT_AN_ITEM = re.compile(r"^(none|unknown|new -|\d+/\d+$)", re.I)


@lru_cache(maxsize=1)
def _gear_sets_doc() -> dict:
    return json.loads(_GEAR_SETS_PATH.read_text(encoding="utf-8"))


def _gear_set_tables() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    doc = _gear_sets_doc()
    return doc.get("byName", {}), doc.get("byPrefix", {}), doc.get("bySuffixKeyword", {})


def _norm_item(name: str) -> str:
    """Item label → match key: lower case, no punctuation, spaces as underscores."""
    n = re.sub(r"\s*\(.*\)$", "", (name or "").strip().lower())
    n = n.replace("'", "").replace(".", "")
    n = re.sub(r"[^a-z0-9+\- ]", " ", n)
    return re.sub(r"\s+", "_", n.strip())


def classify_gear_set(label: str) -> str | None:
    """Which content set a gear label belongs to, or None for plain gear.

    Three signals, in order of authority:

    1. **Name match** against the curated table — the content sets (Prime,
       Aeonic, Mythic, Abjuration, Limbus).
    2. **Label suffix** ``(BLM Artifact)`` — how the curated list already marks
       the reforged job sets, so Artifact / Relic / Empyrean come straight off
       the existing labels rather than a second name list.
    3. **Name prefix** — Ebur, Furia and Ebon are name families, not suffixed
       sets, and are small enough that they read better as one bucket.
    """
    by_name, by_prefix, by_suffix = _gear_set_tables()
    if not label or _NOT_AN_ITEM.match(label.strip()):
        return None

    bare = re.sub(r"\s*\([^()]*\)\s*$", "", label)
    hit = by_name.get(_norm_item(bare))
    if hit:
        return hit

    m = re.search(r"\(([^()]*)\)\s*$", label)
    if m:
        # "(BLM Artifact)" / "(COR-Artifact)" — the set is the last word.
        tail = re.split(r"[\s\-/]+", m.group(1).strip())[-1].lower()
        if tail in by_suffix:
            return by_suffix[tail]

    head = _norm_item(bare).split("_")[0]
    return by_prefix.get(head)


def update_gear_sets(
    lists_dir: Path, *, dry_run: bool = False, base_dir: Path | None = None,
    notify: Notify = _noop,
) -> Report:
    """Stamp ``set`` on every gear row that belongs to a known content set.

    The viewer's existing buckets are spread across two conventions — Artifact /
    Relic / Empyrean live in a ``(JOB Set)`` label suffix, while Ebur, Furia and
    Ebon are name prefixes — and ``group`` is already spoken for (weapon type on
    weapons, a divider string on armour). So this writes one new field that
    carries every bucket, old and new, in one place.

    Purely additive: labels, groups and paths are untouched, and a row that
    matches nothing is left without the key.
    """
    path = lists_dir / "characters.json"
    src = path if path.is_file() else None
    if src is None and base_dir is not None and (base_dir / "characters.json").is_file():
        src = base_dir / "characters.json"
    if src is None:
        return {
            "target": "gear-sets",
            "file": str(path),
            "error": f"characters.json not found in {lists_dir}",
            "added": 0,
            "wrote": False,
        }

    notify("matching gear labels against the set table")
    data = _load_json(src)

    # Section order and the weapon catch-all rule travel with the data, so the
    # viewer never hard-codes a list of set names.
    sections = _gear_sets_doc().get("sections")
    sections_written = bool(sections) and data.get("gearSections") != sections
    if sections_written:
        data["gearSections"] = sections

    # Sets that were tagged once and have since been withdrawn: strip them so a
    # list written by an older run converges instead of keeping a dead bucket.
    retired = set(_gear_sets_doc().get("retiredSets") or [])

    added = 0
    changed = 0
    dropped = 0
    by_cat: dict[str, int] = {}
    samples: list[str] = []
    unknown: set[str] = set()

    for race in data.get("races") or []:
        for slot, items in (race.get("slots") or {}).items():
            for it in items:
                label = it.get("label") or ""
                if it.get("set") in retired:
                    del it["set"]
                    dropped += 1
                found = classify_gear_set(label)
                if found is None:
                    # A job code with a set keyword we don't know is a typo in
                    # the curated label, not a set we're missing. Surface it.
                    parts = _job_suffix_parts(label)
                    if parts is not None:
                        unknown.add(label)
                    continue
                if it.get("set") == found:
                    continue
                if "set" in it:
                    changed += 1
                else:
                    added += 1
                it["set"] = found
                by_cat[found] = by_cat.get(found, 0) + 1
                if len(samples) < 12:
                    samples.append(f"{race.get('id')}/{slot} {it.get('label')} → {found}")

    if (added or changed or dropped or sections_written) and not dry_run:
        _write_json(path, data, dry_run=False)

    if dropped:
        samples.append(f"cleared {dropped} rows of retired set(s): {sorted(retired)}")
    if sections_written:
        samples.append(f"gearSections written ({len(sections.get('order') or [])} sections)")
    if unknown:
        samples.append(f"unrecognised set keyword (check these labels): {sorted(unknown)}")

    return {
        "target": "gear-sets",
        "file": str(path),
        "added": added,
        "retagged": changed,
        "dropped": dropped,
        "unknown_suffix": len(unknown),
        "by_cat": by_cat,
        "samples": samples,
        "wrote": bool(added or changed or dropped or sections_written) and not dry_run,
    }


# ── gear labels (job to the front) ───────────────────────────────────────────

JOB_CODES = frozenset({
    "WAR", "MNK", "WHM", "BLM", "RDM", "THF", "PLD", "DRK", "BST", "BRD", "RNG",
    "SAM", "NIN", "DRG", "SMN", "BLU", "COR", "PUP", "DNC", "SCH", "GEO", "RUN",
})

# "Wizard's Coat (BLM Artifact)" — job and set share the trailing bracket,
# separated by a space, a hyphen or a slash depending on who typed it.
_JOB_SUFFIX = re.compile(r"^(?P<name>.+?)\s*\((?P<job>[A-Za-z]{3})[\s\-/]+(?P<set>[^()]*)\)\s*$")


def _job_suffix_parts(label: str) -> tuple[str, str, str] | None:
    """``Wizard's Coat (BLM Artifact)`` → ``("Wizard's Coat", "BLM", "artifact")``.

    None unless the bracket's first token is a real job code, which is what keeps
    weapon tier markers — ``(119 AG)``, ``(Stage 5)``, ``(SU5)`` — and the
    descriptive ones like ``(Unobtainable)`` out of it.
    """
    m = _JOB_SUFFIX.match(label or "")
    if not m:
        return None
    job = m.group("job").upper()
    name = m.group("name").strip()
    if job not in JOB_CODES or not name:
        return None
    return name, job, m.group("set").strip().lower()


def rename_job_label(label: str) -> str | None:
    """``Wizard's Coat (BLM Artifact)`` → ``BLM - Wizard's Coat``; None if N/A.

    Deliberately refuses rows whose set keyword is not one we recognise. Those
    are nearly always typos in the curated list — ``(RUN AF@)`` sits among six
    identical ``(RUN Relic)`` rows — and renaming one would erase the evidence
    and leave it silently untagged. ``update_gear_sets`` reports them instead.
    """
    parts = _job_suffix_parts(label)
    if parts is None:
        return None
    name, job, keyword = parts
    _by_name, _by_prefix, by_suffix = _gear_set_tables()
    if keyword not in by_suffix:
        return None
    return f"{job} - {name}"


def update_gear_labels(
    lists_dir: Path, *, dry_run: bool = False, base_dir: Path | None = None,
    notify: Notify = _noop,
) -> Report:
    """Move the job code to the front of reforged gear labels.

    ``Wizard's Coat (BLM Artifact)`` becomes ``BLM - Wizard's Coat``, so the list
    sorts and scans by job. The set name is not lost — it moves to the ``set``
    field, which this stamps first for any row that is missing it, so the two
    targets can run in either order.

    Idempotent: a renamed label has no trailing bracket left to match.
    """
    path = lists_dir / "characters.json"
    src = path if path.is_file() else None
    if src is None and base_dir is not None and (base_dir / "characters.json").is_file():
        src = base_dir / "characters.json"
    if src is None:
        return {
            "target": "gear-labels",
            "file": str(path),
            "error": f"characters.json not found in {lists_dir}",
            "added": 0,
            "wrote": False,
        }

    notify("moving job codes to the front of gear labels")
    data = _load_json(src)
    fixes = {k.lower(): v for k, v in (_gear_sets_doc().get("labelFixes") or {}).items()}
    renamed = 0
    corrected = 0
    by_cat: dict[str, int] = {}
    samples: list[str] = []

    for race in data.get("races") or []:
        for items in (race.get("slots") or {}).values():
            for it in items:
                label = it.get("label") or ""

                # Mistyped rows the set keyword can't rescue — corrected from
                # the data file, so the knowledge lives next to the set tables.
                fix = fixes.get(label.strip().lower())
                if fix:
                    it["label"] = fix["label"]
                    if fix.get("set"):
                        it["set"] = fix["set"]
                    corrected += 1
                    if len(samples) < 12:
                        samples.append(f"fix {label} → {fix['label']} ({fix.get('set')})")
                    continue

                new = rename_job_label(label)
                if new is None:
                    continue
                # Preserve the set before the suffix that encodes it disappears.
                if not it.get("set"):
                    found = classify_gear_set(label)
                    if found:
                        it["set"] = found
                it["label"] = new
                renamed += 1
                job = new.split(" - ", 1)[0]
                by_cat[job] = by_cat.get(job, 0) + 1
                if len(samples) < 12:
                    samples.append(f"{label} → {new}")

    if (renamed or corrected) and not dry_run:
        _write_json(path, data, dry_run=False)

    return {
        "target": "gear-labels",
        "file": str(path),
        "added": renamed,
        "corrected": corrected,
        "by_cat": by_cat,
        "samples": samples,
        "wrote": bool(renamed or corrected) and not dry_run,
    }


# ── images.json ──────────────────────────────────────────────────────────────

# Where auto-detected images land. Curated groups are never touched.
_IMAGE_GROUP_ID = "unsorted"
_IMAGE_GROUP_NAME = "Unsorted Images"


def _ensure_image_group(data: list, gid: str, name: str) -> dict:
    for g in data:
        if g.get("id") == gid:
            return g
    g = {"id": gid, "name": name, "entries": []}
    data.append(g)
    return g


def update_images(
    lists_dir: Path, *, dry_run: bool = False, base_dir: Path | None = None,
    notify: Notify = _noop,
) -> Report:
    """Append every texture-only DAT that images.json does not already list.

    An image DAT is one whose sections are textures (0x20) plus UI scaffolding
    (0x01 Directory / 0x30 UiMenu / 0x31 UiElementGroup) and nothing else — no
    mesh, no skeleton, no zone. Raw PNG files with a .DAT extension count too;
    the curated list already carries ~66 of those under "misc".

    That rule reproduces 1,359 of the 1,428 curated entries exactly, so what it
    turns up beyond them is the genuinely-missing remainder.
    """
    path = lists_dir / "images.json"
    src = path if path.is_file() else None
    if src is None and base_dir is not None and (base_dir / "images.json").is_file():
        src = base_dir / "images.json"
    data = _load_json(src) if src else []
    if not isinstance(data, list):
        return {
            "target": "images",
            "file": str(path),
            "error": "images.json is not a list of groups",
            "added": 0,
            "wrote": False,
        }

    known: set[str] = set()
    for g in data:
        for e in g.get("entries") or []:
            if e.get("path"):
                known.add(dat_index.norm(e["path"]))

    scan = _scan_dats(notify)
    fids = _file_ids(notify)

    notify(f"picking texture-only DATs out of {len(scan):,}")
    added = 0
    samples: list[str] = []
    group = None
    for dat in sorted(scan):
        info = scan[dat]
        if not info.is_image or dat in known:
            continue
        if group is None:
            group = _ensure_image_group(data, _IMAGE_GROUP_ID, _IMAGE_GROUP_NAME)
        fid = fids.get(dat)
        tag = "PNG" if info.is_png else (info.fourcc or "").strip(". ")
        label = f"NEW - {dat_index.pretty(dat)}"
        if tag:
            label += f" ({tag})"
        entry: dict[str, Any] = {"name": label, "path": dat_index.back(dat)}
        if fid is not None:
            entry["fileId"] = fid
        group.setdefault("entries", []).append(entry)
        known.add(dat)
        added += 1
        if len(samples) < 12:
            samples.append(f"{dat} fid {fid}")

    if added and not dry_run:
        _write_json(path, data, dry_run=False)

    return {
        "target": "images",
        "file": str(path),
        "added": added,
        "scanned": len(scan),
        "samples": samples,
        "wrote": bool(added) and not dry_run,
    }


# ── npcs.json ────────────────────────────────────────────────────────────────

def _model_dat_index(notify: Notify = _noop) -> dict[int, tuple[int, str]]:
    """``modelId → (file_id, DAT)`` for every modelid that resolves to a model.

    The four monster modelid bands live in ``entity.xi_core.RANGES``; each is a
    flat ``modelid + offset`` into the file table. Anything that resolves but
    holds no SkeletonMesh (a stub, or a slot reused for something else) drops out.
    """
    from xi.entity.xi_core import RANGES, MAX_3500_MODELID

    forward = _dats_by_file_id(notify)
    scan = _scan_dats(notify)
    notify("walking the monster modelid bands")
    out: dict[int, tuple[int, str]] = {}
    for start, end, offset in RANGES:
        hi = end if end is not None else MAX_3500_MODELID
        for model_id in range(start, hi + 1):
            dat = forward.get(model_id + offset)
            if not dat:
                continue
            info = scan.get(dat)
            if info is None or not info.is_model:
                continue
            out[model_id] = (model_id + offset, dat)
    return out


def _ensure_npc_category(data: dict, name: str) -> dict:
    for c in data.setdefault("categories", []):
        if c.get("name") == name:
            return c
    cat = {"name": name, "entries": []}
    data["categories"].append(cat)
    return cat


def update_npcs(
    lists_dir: Path, *, dry_run: bool = False, base_dir: Path | None = None,
    notify: Notify = _noop,
) -> Report:
    """Append every entity model the modelid space reaches that npcs.json lacks.

    Two independent sources meet here. The **client** decides which DATs exist:
    ``modelid → file_id → FTABLE → DAT``, keeping only DATs that actually carry a
    skinned mesh. The **server** supplies names and grouping: ``mob_pools`` and
    ``npc_list`` each store a 20-byte look blob whose modelid field points back
    into that same space, and ``mob_family_system.ecosystem`` is the bucketing
    npcs.json already uses.

    Models with no server row still get listed (under "Unsorted Models") — they
    are real, loadable models, just unused by this server's content.

    A model DAT with a mesh but no skeleton of its own needs a ``base`` to pose
    it. The nearest lower-numbered file in the same ROM directory that does have
    a skeleton is right about half the time (13 of the 24 hand-curated cases —
    the misses are automatons whose frames live in a different directory), so it
    is recorded as ``baseGuess`` rather than as ``base``.
    """
    from xi.mv.server_names import UNNAMED_CATEGORY, model_names

    path = lists_dir / "npcs.json"
    src = path if path.is_file() else None
    if src is None and base_dir is not None and (base_dir / "npcs.json").is_file():
        src = base_dir / "npcs.json"
    if src is None:
        return {
            "target": "npcs",
            "file": str(path),
            "error": (
                f"npcs.json not found in {lists_dir}"
                + (f" or base {base_dir}" if base_dir else "")
                + " — copy base lists into mv/lists first"
            ),
            "added": 0,
            "wrote": False,
        }

    data = _load_json(src)
    known: set[str] = set()
    for c in data.get("categories") or []:
        for e in c.get("entries") or []:
            for v in e.get("variants") or []:
                known.add(dat_index.norm(v))

    models = _model_dat_index(notify)
    notify("reading names from mob_pools / npc_list")
    names = model_names()
    scan = _scan_dats(notify)

    # Skeleton-bearing candidates per ROM directory, for the base guess.
    notify(f"matching {len(models):,} models against npcs.json")
    by_dir: dict[tuple[str, int], list[tuple[int, str]]] = {}
    for dat, info in scan.items():
        if not (info.has_skeleton and info.is_model):
            continue
        try:
            rom, sub, name = dat.split("/")
            idx = int(name.split(".")[0])
        except (ValueError, IndexError):
            continue
        by_dir.setdefault((rom, int(sub)), []).append((idx, dat))
    for rows in by_dir.values():
        rows.sort()

    def guess_base(dat: str) -> str | None:
        try:
            rom, sub, name = dat.split("/")
            idx = int(name.split(".")[0])
        except (ValueError, IndexError):
            return None
        best = None
        for other_idx, other in by_dir.get((rom, int(sub)), ()):
            if other_idx < idx:
                best = other
        return best

    added = 0
    by_cat: dict[str, int] = {}
    samples: list[str] = []
    seen_dat: set[str] = set()

    for model_id, (file_id, dat) in sorted(models.items()):
        if dat in known or dat in seen_dat:
            continue
        seen_dat.add(dat)
        info = scan[dat]
        meta = names.get(model_id)
        cat_name = meta["category"] if meta else UNNAMED_CATEGORY
        label = meta["name"] if meta else f"NEW - model {model_id}"
        entry: dict[str, Any] = {
            "name": label,
            "variants": [dat_index.back(dat)],
            "base": None,
            "modelId": model_id,
            "fileId": file_id,
        }
        if not info.has_skeleton:
            guess = guess_base(dat)
            if guess:
                entry["baseGuess"] = dat_index.back(guess)
        _ensure_npc_category(data, cat_name).setdefault("entries", []).append(entry)
        added += 1
        by_cat[cat_name] = by_cat.get(cat_name, 0) + 1
        if len(samples) < 12:
            samples.append(f"model {model_id} fid {file_id} {label} → {dat}")

    if added and not dry_run:
        _write_json(path, data, dry_run=False)

    return {
        "target": "npcs",
        "file": str(path),
        "added": added,
        "by_cat": by_cat,
        "models_resolved": len(models),
        "server_named": len(names),
        "samples": samples,
        "wrote": bool(added) and not dry_run,
    }


# ── fileId stamping (all lists) ──────────────────────────────────────────────

def update_file_ids(
    lists_dir: Path, *, dry_run: bool = False, base_dir: Path | None = None,
    notify: Notify = _noop,
) -> Report:
    """Stamp ``fileId`` onto every list row whose DAT the file table can address.

    The game never opens a DAT by path — it asks the FTABLE/VTABLE pair for a
    ``file_id`` and gets ``ROM<n>/<sub>/<idx>.DAT`` back. Inverting that map gives
    each list entry the id the client (and every other xi command) uses.

    Purely additive: an existing ``fileId`` is left alone, and a DAT that no
    table points at simply gets no id.
    """
    fids = _file_ids(notify)
    stamped = 0
    unresolved = 0
    per_file: dict[str, int] = {}
    files_written: list[str] = []

    def stamp(entry: dict, path_value: str | None, field: str = "fileId") -> None:
        nonlocal stamped, unresolved
        if not path_value:
            return
        if entry.get(field) is not None:
            return
        fid = fids.get(dat_index.norm(path_value))
        if fid is None:
            unresolved += 1
            return
        entry[field] = fid
        stamped += 1

    def resolve(name: str) -> Path | None:
        p = lists_dir / name
        if not p.is_file():
            if base_dir is None or not (base_dir / name).is_file():
                return None
            p = base_dir / name
        notify(f"stamping {name}")
        return p

    # characters.json — gear rows carry a list of paths; the first is the model.
    src = resolve("characters.json")
    if src:
        data = _load_json(src)
        before = stamped
        for race in data.get("races") or []:
            for items in (race.get("slots") or {}).values():
                for it in items:
                    paths = it.get("paths") or []
                    stamp(it, paths[0] if paths else None)
        if stamped > before:
            per_file["characters.json"] = stamped - before
            if not dry_run:
                _write_json(lists_dir / "characters.json", data, dry_run=False)
                files_written.append("characters.json")

    # images.json — flat groups of {name, path}.
    src = resolve("images.json")
    if src:
        data = _load_json(src)
        before = stamped
        for g in data:
            for e in g.get("entries") or []:
                stamp(e, e.get("path"))
        if stamped > before:
            per_file["images.json"] = stamped - before
            if not dry_run:
                _write_json(lists_dir / "images.json", data, dry_run=False)
                files_written.append("images.json")

    # npcs.json — one id per variant, plus the base model when there is one.
    src = resolve("npcs.json")
    if src:
        data = _load_json(src)
        before = stamped
        for c in data.get("categories") or []:
            for e in c.get("entries") or []:
                variants = e.get("variants") or []
                if variants:
                    stamp(e, variants[0])
                if len(variants) > 1 and e.get("fileIds") is None:
                    ids = [fids.get(dat_index.norm(v)) for v in variants]
                    if any(i is not None for i in ids):
                        e["fileIds"] = ids
                stamp(e, e.get("base"), field="baseFileId")
        if stamped > before:
            per_file["npcs.json"] = stamped - before
            if not dry_run:
                _write_json(lists_dir / "npcs.json", data, dry_run=False)
                files_written.append("npcs.json")

    # effects.json — most rows already carry the fileId they were derived from.
    src = resolve("effects.json")
    if src:
        data = _load_json(src)
        before = stamped
        for c in data.get("categories") or []:
            for e in c.get("entries") or []:
                stamp(e, e.get("path"))
        if stamped > before:
            per_file["effects.json"] = stamped - before
            if not dry_run:
                _write_json(lists_dir / "effects.json", data, dry_run=False)
                files_written.append("effects.json")

    # zones.json — paths are prefixed with 'game/'.
    src = resolve("zones.json")
    if src:
        data = _load_json(src)
        before = stamped
        for z in data:
            raw = (z.get("path") or "").replace("game/", "", 1)
            stamp(z, raw)
        if stamped > before:
            per_file["zones.json"] = stamped - before
            if not dry_run:
                _write_json(lists_dir / "zones.json", data, dry_run=False)
                files_written.append("zones.json")

    return {
        "target": "file-ids",
        "file": str(lists_dir),
        "added": stamped,
        "by_cat": per_file,
        "unresolved": unresolved,
        "table_entries": len(fids),
        "wrote": bool(files_written) and not dry_run,
    }


# ── registry ─────────────────────────────────────────────────────────────────

# gear-sets runs before gear-labels so the set is recorded before the label
# suffix that encodes it is rewritten away (gear-labels also stamps it itself,
# so running them the other way round is safe too).
ALL_TARGETS = (
    "gear", "gear-sets", "gear-labels", "music", "sfx", "zone-music", "effects",
    "images", "npcs", "file-ids",
)

Updater = Callable[..., Report]


def run_updates(
    targets: list[str],
    lists_dir: Path,
    *,
    dry_run: bool = False,
    sql_path: Path | None = None,
    base_dir: Path | None = None,
    mid_cap: int = 1500,
    on_step: Callable[[str, str], None] | None = None,
    on_report: Callable[[Report], None] | None = None,
) -> list[Report]:
    """Run each target in order, returning one report per target.

    ``on_step(target, message)`` fires as a target works through its stages, and
    ``on_report(report)`` fires the moment a target finishes — a full run touches
    ~53k DATs and takes the better part of a minute, so the CLI streams both
    rather than sitting silent until the end. Both default to off.
    """
    lists_dir.mkdir(parents=True, exist_ok=True)
    reports: list[Report] = []
    for t in targets:
        notify: Notify = (lambda m, _t=t: on_step(_t, m)) if on_step else _noop
        if t == "gear":
            report = update_gear(
                lists_dir, dry_run=dry_run, base_dir=base_dir, mid_cap=mid_cap,
                notify=notify,
            )
        elif t in ("gear-sets", "gear_sets"):
            report = update_gear_sets(
                lists_dir, dry_run=dry_run, base_dir=base_dir, notify=notify)
        elif t in ("gear-labels", "gear_labels"):
            report = update_gear_labels(
                lists_dir, dry_run=dry_run, base_dir=base_dir, notify=notify)
        elif t == "music":
            report = update_music(
                lists_dir, dry_run=dry_run, base_dir=base_dir, notify=notify)
        elif t == "sfx":
            report = update_sfx(
                lists_dir, dry_run=dry_run, base_dir=base_dir, notify=notify)
        elif t in ("zone-music", "zone_music"):
            report = update_zone_music(
                lists_dir, sql_path=sql_path, dry_run=dry_run, base_dir=base_dir,
                notify=notify,
            )
        elif t == "effects":
            report = update_effects(
                lists_dir, dry_run=dry_run, base_dir=base_dir, notify=notify)
        elif t == "images":
            report = update_images(
                lists_dir, dry_run=dry_run, base_dir=base_dir, notify=notify)
        elif t == "npcs":
            report = update_npcs(
                lists_dir, dry_run=dry_run, base_dir=base_dir, notify=notify)
        elif t in ("file-ids", "file_ids", "fileids"):
            report = update_file_ids(
                lists_dir, dry_run=dry_run, base_dir=base_dir, notify=notify)
        else:
            report = {"target": t, "error": f"unknown target {t!r}", "wrote": False}
        reports.append(report)
        if on_report:
            on_report(report)
    return reports

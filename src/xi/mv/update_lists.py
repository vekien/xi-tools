"""Append-only refresh of xi-model-viewer ``ui/public/lists/*.json``.

Current shipped JSON is the base. These updaters only add missing entries
(and fill blank name keys); they never delete or rename curated labels.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

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


def default_lists_dir() -> Path:
    """Default output: ``<XI_TOOLS_DIR>/mv/lists`` (created on write)."""
    from xi.xi_config import XI_TOOLS_DIR
    return Path(XI_TOOLS_DIR) / "mv" / "lists"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any, *, dry_run: bool) -> None:
    text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
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


def _build_gear_maps(mid_cap: int = 1500) -> dict[str, dict]:
    """Per viewer race/slot: ordered (mid, NORM_DAT) pairs + alt mid-by-dat.

    Walks FFXiMain race tables by model id (mid), resolves each through FTABLE.
    ``mid_cap`` drops table rows above the soft cap (default 1500).
    """
    from xi.gear.xi_core import RACE_TABLES, SLOTS, parse_race_table, slot_file_ids
    from xi.ftable.xi_core import load_all_tables, scan_file_ids

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
    maps = _build_gear_maps(mid_cap=mid_cap)

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
) -> Report:
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
) -> Report:
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

# Early job-ability VFX band (Berserk anim 0 → file_id 4412 → ROM/15/89.DAT).
_ABILITY_FILE_OFFSET = 4412
_ABILITY_ANIM_MAX = 200  # only trust the classic JA block


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
) -> Report:
    """Append missing effect DATs from spell/ability animation → file_id → FTABLE.

    Spells: ``file_id = 0xAF0 + animation`` (xi.spell), names from d_msg, category
    from spell_list skill when SQL is available.

    Abilities (early JA band only): ``file_id = 4412 + animation`` when anim ≤ 200.

    Optional: scan file_ids 2800–3811 for effect paths not already listed → Unknown Effects.
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

    # ── early abilities (best-effort) ───────────────────────────────────────
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

    # ── FTABLE spell band scan for orphans ──────────────────────────────────
    scanned = 0
    if scan_spell_band:
        band = list(range(0xAF0, 0xAF0 + 1200))  # 2800..3999
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
            # cheap effect sniff: need 0x07 or 0x05 somewhere in first 4KB headers is heavy;
            # spell band is already VFX-dedicated — list it.
            fid = h["file_id"]
            entry = {
                "name": f"NEW - fileId {fid}",
                "path": path_out,
                "fileId": fid,
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


# ── registry ─────────────────────────────────────────────────────────────────

ALL_TARGETS = ("gear", "music", "sfx", "zone-music", "effects")

Updater = Callable[..., Report]


def run_updates(
    targets: list[str],
    lists_dir: Path,
    *,
    dry_run: bool = False,
    sql_path: Path | None = None,
    base_dir: Path | None = None,
    mid_cap: int = 1500,
) -> list[Report]:
    lists_dir.mkdir(parents=True, exist_ok=True)
    reports: list[Report] = []
    for t in targets:
        if t == "gear":
            reports.append(update_gear(
                lists_dir, dry_run=dry_run, base_dir=base_dir, mid_cap=mid_cap,
            ))
        elif t == "music":
            reports.append(update_music(lists_dir, dry_run=dry_run, base_dir=base_dir))
        elif t == "sfx":
            reports.append(update_sfx(lists_dir, dry_run=dry_run, base_dir=base_dir))
        elif t in ("zone-music", "zone_music"):
            reports.append(update_zone_music(
                lists_dir, sql_path=sql_path, dry_run=dry_run, base_dir=base_dir,
            ))
        elif t == "effects":
            reports.append(update_effects(lists_dir, dry_run=dry_run, base_dir=base_dir))
        else:
            reports.append({"target": t, "error": f"unknown target {t!r}", "wrote": False})
    return reports

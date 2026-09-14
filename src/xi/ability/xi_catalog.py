"""The ability catalog — every job ability, spell and weapon skill the model viewer's
Ability Mixer can pick from, with what the inspector knows about each. Baked into the
viewer's lists by ``xi mv update --only abilities`` (``mv/lists/abilities.json``,
``xi.mv.update_lists.update_abilities``); this module is the library that builds it.

Entry shape::

    {"spec": "ws:1", "kind": "ws", "name": "Fast Blade", "names": [...], "cat": "Weapon Skill",
     "path": "ROM/76/30.DAT",                  # the DAT to preview (HumeMale for ws)
     "paths": {"HumeMale": "ROM/76/30.DAT", ...},   # ws only, one per race
     "total": 152, "gens": ["g000", ...], "audio_gens": ["gs00"],
     "sounds": [{"section": "8050", "sound_id": 18050, "title": null, "category": "..."}],
     "clips": ["b000", ...], "carries_clips": true}
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from xi.ability.xi_inspect import ABILITY_FILE_OFFSET
from xi.ability.xi_inspect import Model, _sql_rows, _title
from xi.entity.anim.xi_motion_tables import RACE_NAMES
from xi.ftable.xi_core import load_all_tables, scan_file_ids
from xi.xi_config import FFXI_DIR

JA_ANIM_MAX = 338


def _ability_names() -> Dict[int, List[str]]:
    out: Dict[int, List[str]] = {}
    for aid, n, a in _sql_rows("abilities.sql", "abilities",
                               r"(\d+),'([^']*)',\d+,\d+,\d+,\d+,\d+,\d+,\d+,(\d+),"):
        out.setdefault(int(a), []).append(f"{_title(n)}")
    return out


def _ws_names() -> Dict[int, List[str]]:
    out: Dict[int, List[str]] = {}
    for wid, n, a in _sql_rows("weapon_skills.sql", "weapon_skills",
                               r"(\d+),'([^']*)',0x[0-9A-Fa-f]+,\d+,\d+,\d+,(\d+),"):
        out.setdefault(int(a), []).append(_title(n))
    for mid, a, n in _sql_rows("mob_skills.sql", "mob_skills", r"(\d+),(\d+),'([^']*)',"):
        if int(mid) < 256:
            out.setdefault(int(a), []).append(_title(n))
    return out


def _describe(path: Path) -> Optional[dict]:
    try:
        m = Model.load(path)
    except Exception:  # noqa: BLE001 — a malformed DAT is skipped, not fatal
        return None
    if "main" not in m.routines or b"dumm" in m.data[:4096]:
        return None
    return {
        "total": m.routine_total("main"),
        "gens": [g for g, v in m.gens.items() if not v["sound"]],
        "audio_gens": [g for g, v in m.gens.items() if v["sound"]],
        "sounds": [{"section": n, "sound_id": s["sound_id"], "title": s["title"],
                    "category": s["category"]} for n, s in m.sounds.items()],
        "clips": list(m.clips),
        "carries_clips": bool(m.clips),
        "routines": list(m.routines),
    }


def build_catalog(kinds=("ja", "spell", "ws"), echo=lambda s: None) -> List[dict]:
    base = Path(FFXI_DIR)
    tables = load_all_tables()
    entries: List[dict] = []

    if "ja" in kinds:
        names = _ability_names()
        fids = list(range(ABILITY_FILE_OFFSET, ABILITY_FILE_OFFSET + JA_ANIM_MAX + 1))
        hits = {h["file_id"]: h["dat"] for h in scan_file_ids(fids, tables)}
        for fid in fids:
            rel = hits.get(fid)
            if not rel or not (base / rel).exists():
                continue
            d = _describe(base / rel)
            if d is None:
                continue
            anim = fid - ABILITY_FILE_OFFSET
            nm = names.get(anim, [])
            entries.append({"spec": f"ja:{anim}", "kind": "ja", "cat": "Job Ability",
                            "name": nm[0] if nm else f"ability anim {anim}", "names": nm[:8],
                            "path": rel, **d})
        echo(f"ja: {sum(1 for e in entries if e['kind'] == 'ja')}")

    if "spell" in kinds:
        from xi.spell.xi_spell import spell_catalog
        for s in spell_catalog():
            if not s["dat"] or not (base / s["dat"]).exists():
                continue
            d = _describe(base / s["dat"])
            if d is None:
                continue
            entries.append({"spec": f"spell:{s['index']}", "kind": "spell", "cat": "Spell",
                            "name": s["name"], "names": [s["name"]], "path": s["dat"], **d})
        echo(f"spell: {sum(1 for e in entries if e['kind'] == 'spell')}")

    if "ws" in kinds:
        from xi.entity.anim.xi_motion_tables import load_maindll, weapon_skill_banks, weapon_skill_slot
        banks = weapon_skill_banks(load_maindll())
        names = _ws_names()
        for bank in banks.values():
            for anim in range(bank.first_animation, bank.last_animation + 1):
                paths: Dict[str, str] = {}
                for ri, race in enumerate(RACE_NAMES):
                    slot = weapon_skill_slot(banks, ri, anim)
                    hit = scan_file_ids([slot.file_id], tables)
                    if hit and (base / hit[0]["dat"]).exists():
                        paths[race] = hit[0]["dat"]
                hm = paths.get("HumeMale")
                if not hm:
                    continue
                d = _describe(base / hm)
                if d is None:
                    continue
                nm = names.get(anim, [])
                entries.append({"spec": f"ws:{anim}", "kind": "ws",
                                "cat": "Weapon Skill" if bank.name == "primary" else "Weapon Skill (extended)",
                                "name": nm[0] if nm else f"ws anim {anim}", "names": nm[:8],
                                "path": hm, "paths": paths, **d})
        echo(f"ws: {sum(1 for e in entries if e['kind'] == 'ws')}")
    return entries

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
from xi.entity.anim.xi_motion_tables import RACE_NAMES, category_bases, load_maindll
from xi.ftable.xi_core import load_all_tables, scan_file_ids
from xi.xi_config import FFXI_DIR

JA_ANIM_MAX = 338

# The always-loaded race base (the `movement` table's file +0) carries the cast and
# job-ability clips on every character, so a job ability or spell references one by name
# (compose's `by_reference` lane) and it maps to every race for free — no baking. These
# are the short, curated motion list the Ability Mixer offers for `ja`/`spell`, in place
# of every spell whose motion is really one of these few.
#
# Each family is a clip GROUP whose 4th character wildcards the body part: chant `<x>0`,
# release `<x>1` (the PC basic action set, ``xi.event.xi_compile.CAST_FAMILIES``: black
# mb0/mb1, white mw0/mw1, blue ma0/ma1, ninjutsu mn0/mn1, summon ms0/ms1, item mi0/mi2,
# generic job ability cm0/cm1). v1 exposes the chant group as the representative motion.
#
# NAMES ARE COSMETIC — edit them freely; the clip prefix is what resolves the motion, so
# a renamed row still plays the same animation. (name, kind, chant-clip prefix.)
BASE_MOTIONS = [
    # Confirmed magic casts (xi.event.xi_compile.CAST_FAMILIES: black mb, white mw, blue
    # ma, ninjutsu mn, summon ms, item mi, generic job ability cm).
    ("Black Magic Cast", "spell", "mb0"),
    ("White Magic Cast", "spell", "mw0"),
    ("Blue Magic Cast",  "spell", "ma0"),
    ("Ninjutsu Cast",    "spell", "mn0"),
    ("Summoning",        "spell", "ms0"),
    ("Item Use",         "ja",    "mi0"),
    ("Job Ability",      "ja",    "cm0"),
    # Bard songs, ranged and Geomancy motions the race base also carries. The game does
    # not name these clips, so the LABELS ARE BEST-EFFORT — preview each in the mixer and
    # rename here; the clip prefix (not the name) is what resolves the motion. `yu` = yumi
    # (bow) is certain; the song/geomancy split (sf/sh/sk, gc/gh) is not.
    ("Bard: Flute",          "spell", "sf0"),
    ("Bard: String",         "spell", "sh0"),
    ("Bard: Singing",        "spell", "sk1"),   # the base has sk1/sk2, no sk0
    ("Ranged: Bow",          "ja",    "yu0"),
    ("Ranged: Marksmanship", "ja",    "gu0"),
    ("Geomancy",             "spell", "gc0"),   # tentative — gc/gh are an unlabelled pair
]
# The `movement` table file that carries them (its clips are named, not baked, so it is
# always index 0..4 — a `by_reference` slot in compose). +0 is the race base itself.
_BASE_MOTION_MOVEMENT_INDEX = 0


def _ability_names() -> Dict[int, List[str]]:
    out: Dict[int, List[str]] = {}
    for aid, n, a in _sql_rows("abilities.sql", "abilities",
                               r"(\d+),'([^']*)',\d+,\d+,\d+,\d+,\d+,\d+,\d+,(\d+),"):
        out.setdefault(int(a), []).append(f"{_title(n)}")
    return out


def _ws_names() -> Dict[int, List[str]]:
    """Names for the weapon-skill motion bank, by animation id.

    Three tables index that bank and they are merged in order of how much the
    name says about the motion: player weapon skills first; then job abilities,
    whose `animation` column names a bank slot when the ability has a motion of
    its own — Steal 181, Mug 183, Shield Bash 185, Jump 204, Tomahawk 244,
    Angon 245 (their VFX sit in the job-ability band, the throw or leap here);
    then mob skills (ids below 256 share the bank), whose names are the vaguest
    — `Dancing Chains` sits on 244 and 252 both. A slot named by an earlier
    table keeps that name; the later ones follow in `names` for the tip.
    """
    out: Dict[int, List[str]] = {}
    for wid, n, a in _sql_rows("weapon_skills.sql", "weapon_skills",
                               r"(\d+),'([^']*)',0x[0-9A-Fa-f]+,\d+,\d+,\d+,(\d+),"):
        out.setdefault(int(a), []).append(_title(n))
    for a, names in _ability_names().items():
        out.setdefault(a, []).extend(names)
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


def build_base_motions(echo=lambda s: None) -> List[dict]:
    """The curated always-loaded motions a job ability or spell can reference (see
    ``BASE_MOTIONS``) as catalog rows: name, kind, the clip group, and the race base DAT
    per race (so the viewer previews each race's own copy). The clip refs and lengths come
    from HumeMale's base; a family with no clip there is skipped. Empty when the game dir
    or FFXiMain.dll is unavailable — the mixer then falls back to the full ja/spell list."""
    base = Path(FFXI_DIR)
    try:
        move = category_bases(load_maindll()).get("movement")
    except (FileNotFoundError, ValueError):
        return []
    if not move:
        return []
    tables = load_all_tables()
    fids = [move[ri] + _BASE_MOTION_MOVEMENT_INDEX for ri in range(len(RACE_NAMES))]
    hits = {h["file_id"]: h["dat"] for h in scan_file_ids(fids, tables)}
    paths: Dict[str, str] = {}
    for ri, race in enumerate(RACE_NAMES):
        rel = hits.get(move[ri] + _BASE_MOTION_MOVEMENT_INDEX)
        if rel and (base / rel).exists():
            paths[race] = rel
    hm = paths.get("HumeMale")
    if not hm:
        return []
    try:
        m = Model.load(base / hm)
    except Exception:  # noqa: BLE001 — a malformed base DAT means no base-motion list
        return []
    out: List[dict] = []
    for name, kind, prefix in BASE_MOTIONS:
        # The clip group's body parts: <prefix><digit> (mb0 -> mb00, mb01). The ref the
        # recipe carries wildcards the last character (mb0?), which the client resolves
        # against whichever race is loaded.
        parts = [c for c in m.clips if len(c) == 4 and c[:3] == prefix and c[3:].isdigit()]
        if not parts:
            continue
        frames = max((int(round(m.clips[c].get("frames") or 0)) for c in parts), default=0)
        out.append({"spec": hm, "kind": kind, "name": name, "cat": "Base Motion",
                    "path": hm, "paths": paths, "clip": {"ref": f"{prefix}?", "frames": frames or 1}})
    echo(f"base motions: {len(out)}")
    return out

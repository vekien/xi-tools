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
from xi.ability.xi_inspect import Model, _match_refs, _sql_rows, _title, flatten, resolve_targets
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
# Each family is a run of clip GROUPS, `<xx><stage>`, whose 4th character wildcards the
# body part, and the race base's own schedules say how the game plays them. A magic
# family (black mb, white mw, blue ma, ninjutsu mn, summon ms) has three: stage 0 is the
# LOOPING CHANT the client starts at cast start (`ca<xx>`: `cabk` plays `mb0?` x63),
# stage 1 the release and stage 2 the follow-through, both played at cast finish
# (`ss<xx>`, linked by the spell DAT's `sh<xx>`). Item use has four (`cait` an intro + a
# looping hold, `ssit` two finish clips). Songs, ranged and geomancy run through the
# ranged schedules of their weapon RangeType NN: `lc<NN>` an intro played once then a
# looping hold, `ls<NN>` the finish — singing (00) has no intro. A job ability has no
# cast start: its own `main` plays `cm0?` then `cm1?`.
#
# `clip` on a row stays the family's first group (the representative motion a viewer
# without stage support picks); `stages` lists every group in play order with the dur /
# blend / loops retail plays it with, read from the schedules named here.
#
# NAMES ARE COSMETIC — edit them freely; the clip prefix is what resolves the motion, so
# a renamed row still plays the same animation. (name, kind, first-clip prefix, the
# schedules whose PlayClip commands of that family are its stages, in play order — a
# routine of the race base, or `<spec> <routine>` for one in another DAT.)
BASE_MOTIONS = [
    # Confirmed magic casts (xi.event.xi_compile.CAST_FAMILIES: black mb, white mw, blue
    # ma, ninjutsu mn, summon ms, item mi, generic job ability cm).
    ("Black Magic Cast", "spell", "mb0", ("cabk", "ssbk")),
    ("White Magic Cast", "spell", "mw0", ("cawh", "sswh")),
    ("Blue Magic Cast",  "spell", "ma0", ("cabl", "ssb1")),
    ("Ninjutsu Cast",    "spell", "mn0", ("canj", "ssnj")),
    ("Summoning",        "spell", "ms0", ("casm", "sssm")),
    ("Item Use",         "ja",    "mi0", ("cait", "ssit")),
    ("Job Ability",      "ja",    "cm0", ("ja:0 main",)),     # Berserk and the other plain ones
    # Bard songs, ranged and Geomancy motions the race base also carries. The game does
    # not name these clips; the labels follow the RangeType whose `lc<NN>`/`ls<NN>`
    # schedule plays each (00 singing, 01 wind, 02 string, 03 marksmanship, 06 archery,
    # 11 geomancy — `gh`, RangeType 10, is the same shape and unlisted).
    ("Bard: Flute",          "spell", "sf0", ("lc01", "ls01")),
    ("Bard: String",         "spell", "sh0", ("lc02", "ls02")),
    ("Bard: Singing",        "spell", "sk1", ("lc00", "ls00")),   # the base has sk1/sk2, no sk0
    ("Ranged: Bow",          "ja",    "yu0", ("lc06", "ls06")),
    ("Ranged: Marksmanship", "ja",    "gu0", ("lc03", "ls03")),
    ("Geomancy",             "spell", "gc0", ("lc11", "ls11")),
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


def _stage_labels(n: int) -> List[str]:
    """Stage names by position: Start, Middle (Middle 1, Middle 2… when there are
    several), End. A two-stage set is Start, End."""
    if n < 2:
        return ["Start"] * n
    mid = n - 2
    return ["Start"] + (["Middle"] if mid == 1 else [f"Middle {i + 1}" for i in range(mid)]) + ["End"]


def _group_frames(m: Model, parts: List[str]) -> int:
    """A clip group's length: its longest body part, in whole frames."""
    return max((int(round(m.clips[c].get("frames") or 0)) for c in parts), default=0)


def base_motion_stages(base: Model, prefix: str, schedules,
                       models: Optional[Dict[str, Optional[Model]]] = None) -> List[dict]:
    """One base motion's stages in play order: every PlayClip of the family (``prefix``'s
    first two characters) that ``schedules`` issue themselves, each with the window,
    blend and loop count THAT SCHEDULE plays it with — those are the same on every race,
    where the clip length is not (``mb0`` is 14 frames on a Hume, 28 on an Elvaan, and
    ``cabk`` gives both 33 ticks a cycle). ``frames`` is the group's length in ``base``.

    A schedule is a routine of ``base``, or ``<spec> <routine>`` for one in another DAT (a
    job ability's ``main``; ``models`` caches those). A schedule that is missing, or a
    clip ``base`` does not have, contributes nothing."""
    models = {} if models is None else models
    stages: List[dict] = []
    for sched in schedules:
        spec, _, routine = sched.rpartition(" ")
        m: Optional[Model] = base
        if spec:
            if spec not in models:
                try:
                    models[spec] = Model.load(resolve_targets(spec)[0].path)
                except Exception:  # noqa: BLE001 — a source that does not resolve gives no stages
                    models[spec] = None
            m = models[spec]
        if m is None or routine not in m.routines:
            continue
        for e in flatten(m, routine):
            ref = e["ref"]
            if e["op"] != 0x05 or e["via"] or not ref or ref[:2] != prefix[:2]:
                continue
            parts = _match_refs(ref, base.clips)
            if not parts:
                continue
            stages.append({"ref": ref, "frames": _group_frames(base, parts) or 1, "dur": e["dur"],
                           "blend": list(e["detail"]["blend"]), "loops": e["detail"]["loops"],
                           "schedule": sched})
    return [{"label": label, **s} for label, s in zip(_stage_labels(len(stages)), stages)]


def build_base_motions(echo=lambda s: None) -> List[dict]:
    """The curated always-loaded motions a job ability or spell can reference (see
    ``BASE_MOTIONS``) as catalog rows: name, kind, the clip group, its stages, and the race
    base DAT per race (so the viewer previews each race's own copy). The clip refs and
    lengths come from HumeMale's base; a family with no clip there is skipped. Empty when
    the game dir or FFXiMain.dll is unavailable — the mixer then falls back to the full
    ja/spell list.

    Row shape::

        {"spec": "ROM/27/82.DAT", "kind": "spell", "name": "Black Magic Cast", "cat": "Base Motion",
         "path": "ROM/27/82.DAT", "paths": {"HumeMale": "ROM/27/82.DAT", ...},
         "clip": {"ref": "mb0?", "frames": 14},          # the first group, HumeMale's length
         "stages": [{"label": "Start", "ref": "mb0?", "frames": 14, "dur": 33,
                     "blend": [16, 10], "loops": 63, "schedule": "cabk"}, ...]}

    ``stages`` (see ``base_motion_stages``) is left off a row whose schedules gave none."""
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
    models: Dict[str, Optional[Model]] = {}
    for name, kind, prefix, schedules in BASE_MOTIONS:
        # The clip group's body parts: <prefix><digit> (mb0 -> mb00, mb01). The ref the
        # recipe carries wildcards the last character (mb0?), which the client resolves
        # against whichever race is loaded.
        parts = [c for c in m.clips if len(c) == 4 and c[:3] == prefix and c[3:].isdigit()]
        if not parts:
            continue
        row = {"spec": hm, "kind": kind, "name": name, "cat": "Base Motion", "path": hm, "paths": paths,
               "clip": {"ref": f"{prefix}?", "frames": _group_frames(m, parts) or 1}}
        stages = base_motion_stages(m, prefix, schedules, models)
        if stages:
            row["stages"] = stages
        out.append(row)
    echo(f"base motions: {len(out)}")
    return out

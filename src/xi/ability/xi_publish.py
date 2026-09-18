"""Publishing a composed ability into the custom ROM10 namespace — the library behind
the ``ability`` action of ``xi dats`` (``src/xi/dats/xi_dats.py: _build_ability``) and
the ``xi ability publish`` shortcut, which is that same action prepared and built in
one go::

    xi ability publish recipe.json [--project NAME] [--kind ja|spell|ws] [--animation N]
                                   [--animation-from N] [--subdir S] [--force] [--dry-run]

is exactly::

    xi dats prepare recipe.json --project NAME --type ability --replace [--kind …] […]
    xi dats build NAME --only ability.<name> [--force] [--dry-run]

so a published ability is a manifest action like any gear or mount placement: rebuilt
from Git by ``dats build``, listed by ``dats changelog``, reverted by ``dats undo``.

Kinds and where the client looks (docs/ability/inspect.md, docs/anim/weapon-skills.md):

    ja     file_id = 4412 + animation      retail band 4412..4750 is full; custom = 339+
    spell  file_id = 0xAF0 + animation     retail animations reach 1011; custom = 1012+,
                                           only unregistered ids (others live in between)
    ws     per-race extended bank slots    256..271; 264..271 are dummies on retail
           (body + companion A at +16 + companion B at +32, all per race)

Past those, each kind has a custom band only a client running a band plugin loads
(xi_config FX_*_BAND_*, cexislots' values unless set): spells 1612+, job abilities 500+,
weapon skills 272..527. The stock numbers are handed out first; ``xi ability slots``
lists the weapon-skill ones and what holds each.

The client has no spell table of its own: ``spell_list.animation`` rides in the action
packet (category 4, magic finish) and the client opens file id 0xAF0 + that number — the
same file-table lookup a job ability uses, so a new spell is a new registered id.

A recipe whose motion lane is a weapon skill (``ws:N``) carries per-race clips and must be
published as ``ws``; a spell motion (``spell:N``) publishes as ``spell``; everything else is
a job ability. ``kind`` on the action (or ``target.kind`` in the recipe) overrides that.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, List, Optional

import click

from xi.ability.xi_compose import Composed, _is_race_bound, _lanes, compose, load_recipe, output_name
from xi.ability.xi_inspect import ABILITY_FILE_OFFSET
from xi.entity.anim.xi_motion_tables import (
    WS_EXTENDED_FIRST, WS_EXTENDED_SLOTS, WS_PRIMARY_SLOTS, resolve_weapon_skill)

JA_CUSTOM_FIRST = 339           # first animation number past the retail band
JA_CUSTOM_LAST = 499            # 4412 + 499 = 4911, just below the weapon-skill VFX band
WS_CUSTOM_FIRST, WS_CUSTOM_LAST = 264, 271
# Spells: file_id = 0xAF0 + animation (xi.spell FILE_TABLE_OFFSET). Retail animations run
# 0..1011; the ids above are shared with other content, so only unregistered ones are
# taken (92 free up to 1611, where the job-ability band starts at 4412).
SPELL_FILE_OFFSET = 0xAF0
SPELL_CUSTOM_FIRST = 1012
SPELL_CUSTOM_LAST = 1611

# The action packet carries the animation number in 12 bits, so no number above
# this can reach the client however the file tables are arranged.
ANIMATION_MAX = 4095

# (file-id offset, first custom number, last, what the server column is called)
_SINGLE_DAT_KINDS = {
    "ja": (ABILITY_FILE_OFFSET, JA_CUSTOM_FIRST, JA_CUSTOM_LAST, "abilities.animation"),
    "spell": (SPELL_FILE_OFFSET, SPELL_CUSTOM_FIRST, SPELL_CUSTOM_LAST, "spell_list.animation"),
}


def custom_band(kind: str) -> tuple:
    """``(first_animation, file_id_base)`` of the configured custom band for ``kind``,
    or ``(0, 0)`` when none is set. See xi_config: a band exists only where a
    client-side plugin has patched the animation-to-file-id arithmetic to reach it."""
    import xi.xi_config as cfg
    first, base = {
        "spell": (cfg.FX_SPELL_BAND_FIRST, cfg.FX_SPELL_BAND_BASE),
        "ja": (cfg.FX_JA_BAND_FIRST, cfg.FX_JA_BAND_BASE),
        "ws": (cfg.FX_WS_BAND_FIRST, cfg.FX_WS_BAND_BASE),
    }.get(kind, (0, 0))
    return (first, base) if first and base else (0, 0)


def file_id_for(kind: str, animation: int) -> int:
    """Where the client looks for ``animation``'s DAT. Below the custom band (or with
    none configured) that is the retail arithmetic, untouched.

    Inside the band the number is added to the base whole. The threshold decides
    which arithmetic applies and is not part of it; see xi_config for why each
    base reserves the full 4096."""
    offset = _SINGLE_DAT_KINDS[kind][0]
    first, base = custom_band(kind)
    return base + animation if first and animation >= first else offset + animation


def _candidates(kind: str) -> range:
    """Animation numbers to try, retail band first, then the custom one if configured.

    A configured band runs to the end of the number space, not 4096 past its own
    threshold: the action packet carries the animation in 12 bits, so 4095 is the
    last number that can reach the client whatever the file tables allow."""
    _, first, last, _col = _SINGLE_DAT_KINDS[kind]
    band_first, _base = custom_band(kind)
    return range(first, (ANIMATION_MAX + 1 if band_first else last + 1))
KINDS = ("ja", "spell", "ws")
DEFAULT_SUBDIR = 20
OUT_ROOT = Path("exports") / "ability"      # composed DATs + reports, per recipe name


def _placement(root: Path, file_id: int) -> Optional[str]:
    """Where ``file_id`` resolves for a client reading DATs through ``root``: the custom
    ROM pair first, then the base install's main pair (xi.ftable.xi_core.
    resolve_dat_in_root). Animation numbers are picked against this, so a slot that is
    live through a ROM{n} entry is seen as taken."""
    from xi.ftable.xi_core import resolve_dat_in_root, root_table_pair
    from xi.xi_config import CUSTOM_ROM_IDX, FFXI_DIR
    pairs = (root_table_pair(root, CUSTOM_ROM_IDX), root_table_pair(FFXI_DIR, CUSTOM_ROM_IDX),
             root_table_pair(FFXI_DIR, 1))
    if not any(all(Path(p).exists() for p in pair) for pair in pairs):
        raise click.ClickException(
            f"{root} has no ROM{CUSTOM_ROM_IDX} tables and the base install has no FTABLE.DAT/VTABLE.DAT "
            "— not a DAT root")
    return resolve_dat_in_root(root, file_id)[0]


def is_placeholder(root: Path, rel: Optional[str]) -> bool:
    """What a free slot's file id points at: retail's placeholder DAT (a ``dumm``
    directory), or nothing. The build's dry run tells these apart from a real
    collision, since the placeholder is what makes the slot free."""
    return _is_dummy(root, rel)


def _is_dummy(root: Path, rel: Optional[str]) -> bool:
    """A retail placeholder slot: the DAT exists and carries a ``dumm`` directory."""
    if not rel:
        return True
    from xi.xi_config import FFXI_DIR
    for base in (root, Path(FFXI_DIR)):
        p = base / rel
        if p.exists():
            with open(p, "rb") as f:
                return b"dumm" in f.read(4096)
    return True


def infer_kind(recipe: dict, kind: Optional[str] = None) -> str:
    """The kind a recipe publishes as: an explicit ``kind`` (an action's, then the
    recipe's ``target.kind``), else inferred from the motion lane."""
    if kind in (None, "", "auto"):
        kind = (recipe.get("target") or {}).get("kind")
    # Asked for a job ability or spell, a race-bound motion is baked from one race into the
    # single DAT (xi_compose._lanes — experimental, unverified in game), so only an UNSET
    # kind falls back to ws for such motion.
    race_bound = any(l.race_bound for l in _lanes(recipe, kind=kind).values())
    motion = (recipe.get("sources") or {}).get("motion") or {}
    motion_spec = str(motion.get("spec") if isinstance(motion, dict) else motion or "")
    if kind is None:
        kind = "ws" if race_bound else ("spell" if motion_spec.lower().startswith("spell:") else "ja")
    if kind not in KINDS:
        raise click.ClickException(f"unsupported ability kind {kind!r} (ja, spell or ws)")
    return kind


def _band_off_hint(kind: str) -> str:
    """For a "no free number" error: the custom band was not tried because it is off."""
    name = f"FX_{kind.upper()}_BAND_FIRST"
    return (f"The custom band is switched off ({name}=0), so only the numbers a stock client "
            "loads were tried. A client running a band plugin (cexislots) reaches hundreds "
            "more: tick Settings › XI Tools › Custom animation bands in the model viewer, or "
            f"take {name}=0 out of the environment / .env.")


def ws_band() -> Optional[tuple]:
    """``(first, last)`` of the custom weapon-skill band, or None when it is off."""
    import xi.xi_config as cfg
    if not (cfg.FX_WS_BAND_FIRST and cfg.FX_WS_BAND_BASE and cfg.FX_WS_BAND_SLOTS):
        return None
    return cfg.FX_WS_BAND_FIRST, min(ANIMATION_MAX, cfg.FX_WS_BAND_FIRST + cfg.FX_WS_BAND_SLOTS - 1)


def needs_plugin(kind: str, animation: int) -> bool:
    """True when ``animation`` sits in ``kind``'s custom band, which a stock client cannot load."""
    if kind == "ws":
        band = ws_band()
        return bool(band) and animation > WS_CUSTOM_LAST and band[0] <= animation <= band[1]
    first, _base = custom_band(kind)
    return bool(first) and animation >= first


def ws_candidates(start: Optional[int] = None) -> List[int]:
    """Weapon-skill numbers Publish hands out, in order: the extended bank's dummies any
    client loads, then the custom band. ``start`` drops the numbers below it."""
    band = ws_band()
    nums = list(range(WS_CUSTOM_FIRST, WS_CUSTOM_LAST + 1))
    if band:
        nums += [n for n in range(band[0], band[1] + 1) if n > WS_CUSTOM_LAST]
    return [n for n in nums if start is None or n >= start]


def _pick_animation(root: Path, kind: str, wanted: Optional[int], force: bool,
                    ours: Optional[set] = None, start: Optional[int] = None) -> int:
    """First animation number the client would read as new, or ``wanted`` when it is
    free. A slot registered to one of ``ours`` (a previous build of the same action)
    counts as free, so rebuilds land on the same number. ``start`` is where the search
    begins (``target.animation_from``): nothing below it is handed out."""
    ours = ours or set()
    if kind in _SINGLE_DAT_KINDS:
        offset, first, last, _col = _SINGLE_DAT_KINDS[kind]
        cands = [wanted] if wanted is not None else _candidates(kind)
        if wanted is None and start is not None:
            cands = range(max(cands.start, start), cands.stop)
        for n in cands:
            if last < n < (custom_band(kind)[0] or n):
                continue                     # the gap between the retail band and a custom one
            fid = file_id_for(kind, n)
            cur = _placement(root, fid)
            if cur is None or force or cur.upper() in ours:
                return n
        if wanted is not None:
            raise click.ClickException(
                f"{kind} animation {wanted} (file id {file_id_for(kind, wanted)}) is already "
                f"registered to {_placement(root, file_id_for(kind, wanted))}; pass --force to repoint it")
        band_first, _b = custom_band(kind)
        lo = max(first, start or first)
        if band_first:
            raise click.ClickException(f"no free {kind} animation number between {lo} and {ANIMATION_MAX}")
        raise click.ClickException(
            f"no free {kind} animation number between {lo} and {last}. " + _band_off_hint(kind))
    from xi.entity.anim.xi_motion_tables import load_maindll
    dll = load_maindll()                     # read once: a full band is hundreds of lookups
    band = ws_band()
    last_tried = band[1] if band else WS_CUSTOM_LAST
    cands = [wanted] if wanted is not None else ws_candidates(start)
    for n in cands:
        if force or not _ws_taken(root, resolve_weapon_skill(n, dll=dll), ours):
            return n
    if wanted is not None:
        raise click.ClickException(
            f"Weapon-skill number {wanted} isn't free — some race already has a real motion DAT "
            f"there (a number is free only when every race's body DAT is a retail placeholder). "
            f"Pass --force to overwrite it, pick another in {WS_CUSTOM_FIRST}–{last_tried} "
            f"(`xi ability slots` lists them), or publish the mix as a job ability / spell instead.")
    lo = max(WS_CUSTOM_FIRST, start or WS_CUSTOM_FIRST)
    if lo > last_tried:
        raise click.ClickException(
            f"--animation-from {start} is past the last weapon-skill number this setup reaches "
            f"({last_tried}).")
    from xi.xi_config import FFXI_PIVOT_DIR
    in_pivot = bool(FFXI_PIVOT_DIR) and Path(FFXI_PIVOT_DIR).resolve() == Path(root).resolve()
    ways = ["  • --animation N --force — take a number anyway, overwriting the skill on it "
            "(`xi ability slots` shows what each one holds)"]
    if band:
        ways.append("  • raise FX_WS_BAND_SLOTS, and WS_SLOTS in the client plugin to match")
    if FFXI_PIVOT_DIR and not in_pivot:
        ways.append("  • --pivot — build into FFXI_PIVOT_DIR instead; its tables may have room")
    ways.append("  • publish as a job ability or spell instead, which have far more numbers")
    raise click.ClickException(
        f"No free weapon-skill animation number in {lo}–{last_tried}: on every one of them some "
        "race already has a real motion DAT.\n"
        + ("" if band else _band_off_hint("ws") + "\n")
        + "Otherwise:\n" + "\n".join(ways))


def _ws_taken(root: Path, slots, ours: Optional[set] = None) -> List[tuple]:
    """``(race, dat)`` for every race whose body DAT under this weapon-skill number is a
    real motion: registered, not a retail placeholder and not one of ``ours``. Empty means
    the number is free — the rule a weapon-skill number is allocated by."""
    ours = ours or set()
    taken = []
    for s in slots:
        cur = _placement(root, s.file_id)
        if cur and cur.upper() not in ours and not _is_dummy(root, cur):
            taken.append((s.race, cur))
    return taken


def _free_files(root: Path, subdir: int, count: int, reserved: Optional[set] = None) -> List[int]:
    """First ``count`` unused file numbers in ``ROM10/<subdir>`` of ``root`` — unused
    in the game folder too, since the overlay shadows the base install path for path."""
    from xi.xi_config import FFXI_DIR
    used: set = set(reserved or ())
    for base in {root, Path(FFXI_DIR)}:
        d = base / "ROM10" / str(subdir)
        if d.exists():
            used |= {int(p.stem) for p in d.glob("*.DAT") if p.stem.isdigit()}
    free = [i for i in range(128) if i not in used]
    if len(free) < count:
        raise click.ClickException(f"ROM10/{subdir} has only {len(free)} free file numbers, need {count}")
    return free[:count]


def _ws_file_ids(animation: int, race: str) -> Dict[str, int]:
    s = resolve_weapon_skill(animation, race)[0]
    return {"body": s.file_id, "companion_a": s.companion_a, "companion_b": s.companion_b}


def _source_ws_animation(recipe: dict) -> Optional[int]:
    """The weapon-skill number whose slot the companion (waist) DATs are copied from: a
    ``ws:N`` lane's, or a weapon-skill motion file's own bank slot. None when the
    per-race motion comes from elsewhere (an emote, a battle pack), which carries its
    waist clips in the body DAT."""
    for lane in _lanes(recipe).values():
        if _is_race_bound(lane.spec):
            return int(lane.spec.split(":")[1])
        slot = lane.slot
        if slot is not None and slot.category == "weaponSkill":
            return slot.index % WS_PRIMARY_SLOTS
        if slot is not None and slot.category == "weaponSkillExt":
            return WS_EXTENDED_FIRST + slot.index % WS_EXTENDED_SLOTS
    return None


def plan(recipe: dict, root: Path, *, kind: Optional[str] = None, animation: Optional[int] = None,
         subdir: int = DEFAULT_SUBDIR, force: bool = False, previous: Optional[dict] = None,
         animation_from: Optional[int] = None) -> dict:
    """Compose the recipe and decide where every DAT goes in ``root``: the animation
    number, and per file its file id and ``ROM10/<subdir>/<n>.DAT`` placement.

    ``animation_from`` is where an automatic number starts (``target.animation_from``):
    the first free number at or above it, instead of the first free one of all.

    ``previous`` is the action's last recorded result (``{animation, placements}``): a
    rebuild keeps its animation number and DAT paths, so the manifest stays a stable
    description of where the ability lives — unless that number is now below
    ``animation_from``, which asks for a new one. Nothing is written here except the composed
    DAT bytes, which are returned on each file (``composed``) or as ``copy_from``."""
    kind = infer_kind(recipe, kind)
    # A job ability or spell may carry skeleton clips baked from one race's copy (compose
    # does it when told the kind). Experimental: see xi_compose._lanes for what is unproven.
    composed = compose(recipe, kind=kind)
    prev_places = {(p.get("race"), p.get("role")): p for p in (previous or {}).get("placements") or []}
    ours = {str(p.get("dat", "")).upper() for p in prev_places.values()}
    prev_anim = (previous or {}).get("animation")
    if (animation is None and isinstance(prev_anim, int) and (previous or {}).get("kind", kind) == kind
            and (animation_from is None or prev_anim >= animation_from)):
        animation = prev_anim
    anim = _pick_animation(root, kind, animation, force, ours, start=animation_from)

    def place_for(race, role, pool: List[int]) -> str:
        prev = prev_places.get((race, role))
        if prev and prev.get("dat"):
            return prev["dat"]
        return f"ROM10/{subdir}/{pool.pop(0)}.DAT"

    files: List[dict] = []
    if kind in _SINGLE_DAT_KINDS:
        (c,) = composed
        pool = _free_files(root, subdir, 1)
        files.append({"race": None, "role": "body", "file_id": file_id_for(kind, anim),
                      "place": place_for(None, "body", pool), "composed": c})
    else:
        src_anim = _source_ws_animation(recipe)
        from xi.xi_config import FFXI_DIR
        from xi.ftable.xi_core import scan_file_ids
        pool = _free_files(root, subdir, 3 * len(composed))
        seen_ids: set = set()
        for c in composed:
            ids = _ws_file_ids(anim, c.race)
            if ids["body"] in seen_ids:          # Taru male/female share one bank row
                continue
            seen_ids.add(ids["body"])
            files.append({"race": c.race, "role": "body", "file_id": ids["body"],
                          "place": place_for(c.race, "body", pool), "composed": c})
            # Companion (waist) DATs come from the motion source's slot for the same race.
            # Motion that is not a weapon skill has no such slot: its waist clips ride in
            # the body DAT, and the slot's own companions (retail's placeholders) stay.
            if src_anim is None:
                continue
            src_ids = _ws_file_ids(src_anim, c.race)
            for role in ("companion_a", "companion_b"):
                hits = scan_file_ids([src_ids[role]])
                if not hits:
                    raise click.ClickException(f"cannot resolve source companion {role} for {c.race}")
                files.append({"race": c.race, "role": role, "file_id": ids[role],
                              "place": place_for(c.race, role, pool),
                              "copy_from": Path(FFXI_DIR) / hits[0]["dat"]})
    for f in files:
        f["current"] = _placement(root, f["file_id"])
    warnings = list(dict.fromkeys(w for c in composed for w in c.warnings))
    if needs_plugin(kind, anim):
        warnings.append(f"{kind} animation {anim} is in the custom band: only a client running "
                        "the band plugin (cexislots) loads it")
    return {"root": root, "kind": kind, "animation": anim, "subdir": subdir, "files": files,
            "warnings": warnings}


def write_sources(recipe: dict, p: dict) -> List[Path]:
    """Materialise the plan's DAT bytes under ``exports/ability/<name>/`` (the same
    place ``xi ability compose`` writes) and return one path per planned file, in
    order. The placement step copies from these."""
    out_dir = OUT_ROOT / recipe["name"]
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    for f in p["files"]:
        if "composed" in f:
            c: Composed = f["composed"]
            src = out_dir / output_name(recipe, c)
            src.write_bytes(c.data)
        else:
            src = out_dir / f"{recipe['name']}.{f['race']}.{f['role']}.DAT"
            shutil.copy2(f["copy_from"], src)
        paths.append(src)
    return paths


def server_snippet(recipe: dict, kind: str, animation: int) -> str:
    name = recipe["name"]
    if kind == "spell":
        return (f"-- in-game check, no DB change: target a mob or NPC and   !injectaction 4 {animation}\n"
                f"-- spell: point a spell_list row at the new animation (the client opens file id 0xAF0 + {animation})\n"
                f"UPDATE spell_list SET animation = {animation}, animationTime = 2000 WHERE name = '{name}';\n"
                f"-- or a new row (spellid, name, jobs, group, family, element, zonemisc, validTargets, skill, mpCost,\n"
                f"--   castTime, recastTime, message, magicBurstMessage, animation, animationTime, AOE, base, multiplier,\n"
                f"--   CE, VE, requirements, spell_range, radius, content_tag):\n"
                f"-- INSERT INTO spell_list VALUES (<spellid>,'{name}',<jobs>,<group>,<family>,<element>,0,<validTargets>,<skill>,\n"
                f"--   <mpCost>,<castTime>,<recastTime>,<msg>,<burstMsg>,{animation},2000,0,0,1.00,0,0,0,<range>,0,NULL);")
    if kind == "ja":
        return (f"-- in-game check, no DB change: target a mob or NPC and   !injectaction 6 {animation}\n"
                f"-- job ability: point an abilities row at the new animation\n"
                f"UPDATE abilities SET animation = {animation}, animationTime = 2000 WHERE name = '{name}';\n"
                f"-- or a new row: INSERT INTO abilities VALUES (<abilityId>,'{name}',<job>,<level>,<validTarget>,"
                f"<recast>,<recastId>,<msg1>,<msg2>,{animation},2000,0,0,0,0,0,1,0,0,0,NULL);")
    return (f"-- in-game check, no DB change: target a mob or NPC and   !injectaction 3 {animation}\n"
            f"-- humanoid mob skill (mob_anim_id is 16-bit, works today):\n"
            f"--   INSERT INTO mob_skills VALUES (<id below 256>,{animation},'{name}',0,0.0,5.0,2000,0,4,0,0,0,8,0,0);\n"
            f"-- player weapon skill: weapon_skills.animation is tinyint and the loader reads it as uint8\n"
            f"--   (src/map/utils/battleutils.cpp get<uint8>(\"animation\")), so {animation} needs the column\n"
            f"--   widened (ALTER TABLE weapon_skills MODIFY animation smallint unsigned NOT NULL DEFAULT 0)\n"
            f"--   and a one-line cpp-patch to get<uint16> — then:\n"
            f"--   UPDATE weapon_skills SET animation = {animation} WHERE name = '{name}';")


def permission_hint(root: Path, e: PermissionError) -> str:
    from xi.xi_config import FFXI_PIVOT_DIR
    hint = (f"cannot write {e.filename}: the target's file tables are owned by another account "
            "(a launcher or updater that ran elevated).")
    if FFXI_PIVOT_DIR and Path(FFXI_PIVOT_DIR).resolve() != Path(root).resolve():
        # The ROM10 tables register from the pivot folder too, and building there
        # leaves the install alone — a better answer than taking ownership of it.
        hint += ("\nThe simplest fix is to build into the pivot folder instead: add --pivot "
                 "(Use Pivot Folder in the model viewer's Manage panel). That covers "
                 "placement and registration only — the client's file_id ceiling comes from "
                 "the base install's own tables, so an id past the retail range still needs "
                 "`xi ftable expand` run there once.")
    hint += ("\nOr run this command from an elevated terminal, or grant yourself modify rights "
             "on the install once:\n"
             f'  icacls "{root}" /grant "%USERNAME%":(OI)(CI)M /T')
    return hint + "\nNothing was registered; any DAT already copied is unreferenced and harmless."


# ── `xi ability slots` — what every weapon-skill number holds today ─────────────────

def _root_name(root: Path) -> str:
    from xi.xi_config import FFXI_PIVOT_DIR
    return "pivot" if FFXI_PIVOT_DIR and Path(FFXI_PIVOT_DIR).resolve() == Path(root).resolve() else "dir"


def _ability_owners(root: Path) -> Dict[str, str]:
    """``{DAT path (upper): "project: action id"}`` for the abilities the dats projects
    here have built into ``root`` — what names the skill sitting on a taken number."""
    import json
    target = _root_name(root)
    out: Dict[str, str] = {}
    for mf in sorted(Path("projects").glob("*.json")):
        try:
            actions = json.loads(mf.read_text(encoding="utf-8")).get("actions") or []
        except (OSError, ValueError, AttributeError):
            continue
        for a in actions:
            res = a.get("result") if isinstance(a, dict) and a.get("type") == "ability" else None
            if not res or target not in (res.get("targets") or [target]):
                continue
            for pl in res.get("placements") or []:
                if pl.get("dat"):
                    out[str(pl["dat"]).upper()] = f"{mf.stem}: {a.get('id')}"
    return out


def ws_slots(root: Path, first: Optional[int] = None, last: Optional[int] = None) -> dict:
    """The weapon-skill numbers Publish hands out in ``root`` and what each holds today:
    the extended bank's dummies any client loads, then the custom band (``index`` is the
    number's place inside its bank — 0..255 across cexislots' band). ``free`` is the rule
    ``_pick_animation`` allocates by: no race has a real motion DAT on the number."""
    from xi.entity.anim.xi_motion_tables import load_maindll
    dll = load_maindll()
    band = ws_band()
    owners = _ability_owners(root)
    rows = []
    for n in ws_candidates(first):
        if last is not None and n > last:
            break
        slots = resolve_weapon_skill(n, dll=dll)
        taken = _ws_taken(root, slots)
        dats = list(dict.fromkeys(d for _race, d in taken))
        rows.append({
            "animation": n, "bank": slots[0].bank, "index": slots[0].index,
            "file_id": slots[0].file_id, "plugin": needs_plugin("ws", n), "free": not taken,
            "races": [race for race, _d in taken], "dats": dats,
            "owner": next((owners[d.upper()] for d in dats if d.upper() in owners), None),
        })
    return {"kind": "ws", "root": str(root), "target": _root_name(root),
            "stock": [WS_CUSTOM_FIRST, WS_CUSTOM_LAST], "band": list(band) if band else None,
            "slots": rows}


@click.command("slots")
@click.option("--from", "first", type=int, default=None, help="First animation number to list (default: all).")
@click.option("--to", "last", type=int, default=None, help="Last animation number to list.")
@click.option("--free", "only_free", is_flag=True, help="List only the free numbers.")
@click.option("--pivot", is_flag=True, help="Look in FFXI_PIVOT_DIR instead of the base install (FFXI_DIR).")
@click.option("--json", "as_json", is_flag=True, help="Print the listing as JSON.")
def slots_cmd(first: Optional[int], last: Optional[int], only_free: bool, pivot: bool, as_json: bool):
    """List the weapon-skill animation numbers a mix can publish to, and what holds each.

    \b
    The numbers any client loads (264–271) come first, then the custom band a client
    running a band plugin reaches (cexislots: 272–527, bank index 0–255). A number is
    free when no race has a real motion DAT on it — the rule `dats build` allocates by.
      xi ability slots --pivot            # what the pivot folder's tables hold
      xi ability slots --free --json      # the free numbers, for a script
    """
    import json
    from xi.xi_config import FFXI_DIR, FFXI_PIVOT_DIR
    if pivot and not FFXI_PIVOT_DIR:
        raise click.ClickException("FFXI_PIVOT_DIR is not configured.")
    report = ws_slots(Path(FFXI_PIVOT_DIR if pivot else FFXI_DIR), first, last)
    total = len(report["slots"])
    n_free = sum(1 for r in report["slots"] if r["free"])
    if only_free:
        report["slots"] = [r for r in report["slots"] if r["free"]]
    if as_json:
        click.echo(json.dumps(report, ensure_ascii=False))
        return
    band = report["band"]
    click.echo(f"Weapon-skill numbers in {report['root']} ({report['target']})")
    click.echo(f"  any client: {report['stock'][0]}–{report['stock'][1]}"
               + (f" · custom band (needs the client plugin): {band[0]}–{band[1]}" if band
                  else " · custom band: off (FX_WS_BAND_FIRST=0)"))
    click.echo(f"  {n_free} free of {total}\n")
    click.echo(f"  {'anim':>5}  {'bank':<8} {'#':>3}  {'file id':>7}  holds")
    for r in report["slots"]:
        if r["free"]:
            holds = "free"
        else:
            races = "every race" if len(r["races"]) >= 8 else ", ".join(r["races"])
            holds = ", ".join(r["dats"][:2]) + (" …" if len(r["dats"]) > 2 else "") + f"  ({races})"
            if r["owner"]:
                holds = f"{r['owner']} — {holds}"
        click.echo(f"  {r['animation']:>5}  {r['bank']:<8} {r['index']:>3}  {r['file_id']:>7}  {holds}")


# ── `xi ability publish` — the dats action, prepared and built in one command ────────

@click.command("publish")
@click.argument("recipe_path", type=click.Path(exists=True, path_type=Path))
@click.option("--project", default=None,
              help="dats project to record the action in (projects/<project>.json). Default: the recipe name.")
@click.option("--kind", type=click.Choice(["auto", "ja", "spell", "ws"]), default="auto", show_default=True,
              help="Publish as a job ability, spell or weapon skill (auto = from the recipe).")
@click.option("--animation", type=int, default=None, help="Animation number to use (default: next free).")
@click.option("--animation-from", type=int, default=None,
              help="Where an automatic number starts: the first free one at or above this.")
@click.option("--subdir", type=int, default=DEFAULT_SUBDIR, show_default=True, help="ROM10 folder to place DATs in.")
@click.option("--force", is_flag=True, help="Repoint a file id that is already registered.")
@click.option("--dry-run", is_flag=True, help="Show the plan; write nothing.")
@click.option("--pivot", is_flag=True, help="Build into FFXI_PIVOT_DIR instead of the base install (FFXI_DIR).")
def publish_cmd(recipe_path: Path, project: Optional[str], kind: str, animation: Optional[int],
                animation_from: Optional[int], subdir: int, force: bool, dry_run: bool, pivot: bool = False):
    """Publish RECIPE_PATH through `xi dats`: prepare an ability action, then build it.

    \b
    Shorthand for
      xi dats prepare RECIPE --project NAME --type ability --replace
      xi dats build NAME --only ability.<name> [--pivot]
    The action lands in projects/<NAME>.json beside any gear or mount actions, so the
    ability is rebuilt, listed and undone with the rest of the project.
    """
    from xi.dats.xi_dats import _slug, build_cmd, prepare_cmd
    recipe = load_recipe(recipe_path)
    project = project or recipe["name"]
    ctx = click.get_current_context()
    ctx.invoke(prepare_cmd, source=recipe_path, project=project, action_type="ability", replace=True,
               ability_kind=kind, animation=animation, animation_from=animation_from, subdir=subdir)
    click.echo()
    ctx.invoke(build_cmd, project=project, only=(f"ability.{_slug(recipe['name'])}",),
               force=force, dry_run=dry_run, pivot=pivot)

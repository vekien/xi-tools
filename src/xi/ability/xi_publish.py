"""Publishing a composed ability into the custom ROM10 namespace — the library behind
the ``ability`` action of ``xi dats`` (``src/xi/dats/xi_dats.py: _build_ability``) and
the ``xi ability publish`` shortcut, which is that same action prepared and built in
one go::

    xi ability publish recipe.json [--project NAME] [--kind ja|spell|ws] [--animation N]
                                   [--subdir S] [--force] [--dry-run]

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

# (file-id offset, first custom number, last, what the server column is called)
_SINGLE_DAT_KINDS = {
    "ja": (ABILITY_FILE_OFFSET, JA_CUSTOM_FIRST, JA_CUSTOM_LAST, "abilities.animation"),
    "spell": (SPELL_FILE_OFFSET, SPELL_CUSTOM_FIRST, SPELL_CUSTOM_LAST, "spell_list.animation"),
}
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
            return b"dumm" in p.read_bytes()[:4096]
    return True


def infer_kind(recipe: dict, kind: Optional[str] = None) -> str:
    """The kind a recipe publishes as: an explicit ``kind`` (an action's, then the
    recipe's ``target.kind``), else inferred from the motion lane."""
    if kind in (None, "", "auto"):
        kind = (recipe.get("target") or {}).get("kind")
    race_bound = any(l.race_bound for l in _lanes(recipe).values())
    motion = (recipe.get("sources") or {}).get("motion") or {}
    motion_spec = str(motion.get("spec") if isinstance(motion, dict) else motion or "")
    if kind is None:
        kind = "ws" if race_bound else ("spell" if motion_spec.lower().startswith("spell:") else "ja")
    if race_bound and kind != "ws":
        raise click.ClickException(
            "this recipe's motion is different for every race (a weapon skill, emote, battle, "
            "dance or one race's own motion), so it must be published as kind 'ws', which "
            "builds one DAT per race — a job-ability or spell slot is one DAT for every race, "
            "and the game only loads those motions while they play")
    if kind not in KINDS:
        raise click.ClickException(f"unsupported ability kind {kind!r} (ja, spell or ws)")
    return kind


def _pick_animation(root: Path, kind: str, wanted: Optional[int], force: bool,
                    ours: Optional[set] = None) -> int:
    """First animation number the client would read as new, or ``wanted`` when it is
    free. A slot registered to one of ``ours`` (a previous build of the same action)
    counts as free, so rebuilds land on the same number."""
    ours = ours or set()
    if kind in _SINGLE_DAT_KINDS:
        offset, first, last, _col = _SINGLE_DAT_KINDS[kind]
        cands = [wanted] if wanted is not None else range(first, last + 1)
        for n in cands:
            fid = offset + n
            cur = _placement(root, fid)
            if cur is None or force or cur.upper() in ours:
                return n
        if wanted is not None:
            raise click.ClickException(
                f"{kind} animation {wanted} (file id {offset + wanted}) is already "
                f"registered to {_placement(root, offset + wanted)}; pass --force to repoint it")
        raise click.ClickException(f"no free {kind} animation number between {first} and {last}")
    cands = [wanted] if wanted is not None else range(WS_CUSTOM_FIRST, WS_CUSTOM_LAST + 1)
    for n in cands:
        slots = resolve_weapon_skill(n)
        free = all(_is_dummy(root, _placement(root, s.file_id))
                   or (_placement(root, s.file_id) or "").upper() in ours for s in slots)
        if force or free:
            return n
    if wanted is not None:
        raise click.ClickException(
            f"Weapon-skill slot {wanted} isn't free — some race already has a real motion DAT "
            f"there (a slot is free only when every race's body DAT is a retail dummy). Pass "
            f"--force to overwrite it, pick another slot in {WS_CUSTOM_FIRST}–{WS_CUSTOM_LAST}, "
            f"or publish the mix as a job ability / spell instead.")
    raise click.ClickException(
        f"All {WS_CUSTOM_LAST - WS_CUSTOM_FIRST + 1} custom weapon-skill animation slots "
        f"({WS_CUSTOM_FIRST}–{WS_CUSTOM_LAST}) are taken on this install — none is a retail "
        "dummy on every race, so there's nowhere free to bake a per-race weapon-skill motion. "
        "Three ways forward:\n"
        f"  1. --animation N --force  — overwrite one of {WS_CUSTOM_FIRST}–{WS_CUSTOM_LAST} "
        "(clobbers whatever custom skill is on that slot);\n"
        "  2. --pivot  — build into FFXI_PIVOT_DIR instead, if it has room;\n"
        "  3. give the mix a motion that isn't race-bound (a magic cast, a Bard song, a base "
        "ability motion) so it publishes as a job ability or spell — those have far more room "
        "(and cexislots raises their id ceiling further). Note: cexislots lifts the spell / "
        "job-ability id ceilings, but not this 16-slot weapon-skill motion bank — enlarging "
        "that would need its own client patch.")


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
         subdir: int = DEFAULT_SUBDIR, force: bool = False, previous: Optional[dict] = None) -> dict:
    """Compose the recipe and decide where every DAT goes in ``root``: the animation
    number, and per file its file id and ``ROM10/<subdir>/<n>.DAT`` placement.

    ``previous`` is the action's last recorded result (``{animation, placements}``): a
    rebuild keeps its animation number and DAT paths, so the manifest stays a stable
    description of where the ability lives. Nothing is written here except the composed
    DAT bytes, which are returned on each file (``composed``) or as ``copy_from``."""
    kind = infer_kind(recipe, kind)
    composed = compose(recipe)
    if kind in _SINGLE_DAT_KINDS and any(s.endswith("(0x2B)") for c in composed for s in c.sections):
        raise click.ClickException(
            "this recipe carries skeleton clips, which are one race's; a job-ability or spell "
            "slot is one DAT for every race. Use a ws: lane (published per race) or motion the "
            "actor already has (cm0?, ma2?...)")
    prev_places = {(p.get("race"), p.get("role")): p for p in (previous or {}).get("placements") or []}
    ours = {str(p.get("dat", "")).upper() for p in prev_places.values()}
    prev_anim = (previous or {}).get("animation")
    if animation is None and isinstance(prev_anim, int) and (previous or {}).get("kind", kind) == kind:
        animation = prev_anim
    anim = _pick_animation(root, kind, animation, force, ours)

    def place_for(race, role, pool: List[int]) -> str:
        prev = prev_places.get((race, role))
        if prev and prev.get("dat"):
            return prev["dat"]
        return f"ROM10/{subdir}/{pool.pop(0)}.DAT"

    files: List[dict] = []
    if kind in _SINGLE_DAT_KINDS:
        offset = _SINGLE_DAT_KINDS[kind][0]
        (c,) = composed
        pool = _free_files(root, subdir, 1)
        files.append({"race": None, "role": "body", "file_id": offset + anim,
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
                 "(Use Pivot Folder in the model viewer's Manage panel).")
    hint += ("\nOr run this command from an elevated terminal, or grant yourself modify rights "
             "on the install once:\n"
             f'  icacls "{root}" /grant "%USERNAME%":(OI)(CI)M /T')
    return hint + "\nNothing was registered; any DAT already copied is unreferenced and harmless."


# ── `xi ability publish` — the dats action, prepared and built in one command ────────

@click.command("publish")
@click.argument("recipe_path", type=click.Path(exists=True, path_type=Path))
@click.option("--project", default=None,
              help="dats project to record the action in (projects/<project>.json). Default: the recipe name.")
@click.option("--kind", type=click.Choice(["auto", "ja", "spell", "ws"]), default="auto", show_default=True,
              help="Publish as a job ability, spell or weapon skill (auto = from the recipe).")
@click.option("--animation", type=int, default=None, help="Animation number to use (default: next free).")
@click.option("--subdir", type=int, default=DEFAULT_SUBDIR, show_default=True, help="ROM10 folder to place DATs in.")
@click.option("--force", is_flag=True, help="Repoint a file id that is already registered.")
@click.option("--dry-run", is_flag=True, help="Show the plan; write nothing.")
@click.option("--pivot", is_flag=True, help="Build into FFXI_PIVOT_DIR instead of the base install (FFXI_DIR).")
def publish_cmd(recipe_path: Path, project: Optional[str], kind: str, animation: Optional[int],
                subdir: int, force: bool, dry_run: bool, pivot: bool = False):
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
               ability_kind=kind, animation=animation, subdir=subdir)
    click.echo()
    ctx.invoke(build_cmd, project=project, only=(f"ability.{_slug(recipe['name'])}",),
               force=force, dry_run=dry_run, pivot=pivot)

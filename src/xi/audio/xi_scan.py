#!/usr/bin/env python3
"""`xi audio scan` — where every sound effect is used, across the whole DAT tree.

Walks every ``ROM*/<sub>/<idx>.DAT`` under FFXI_DIR, collects each ``0x3D``
SoundEffectPointer section (``SeSep␠␠`` + u32 sound id — the same reference
`xi audio refs` reads out of one DAT) and writes a single JSON keyed by sound
id: which DATs reference it, how often, and from which sections.

Each DAT is then identified from what the file tables say about it. The client
addresses DATs by file_id and every family of content has its own id formula, so
inverting FTABLE/VTABLE tells us what a path *is*:

    zone           model / event / dialog / NPC-list file of a named zone
    entity         monster or NPC model (modelid; named from the server SQL
                   when XI_SERVER_DIR is set)
    spell          spell effect       (0xAF0 + animation, named from the spell table)
    ability        job-ability effect (4412 + animation)
    weapon_skill   weapon-skill effect (4912 + animation)
    gear           equipment model    (race + slot + model id)
    mount          mount model        (0x19131 + id)
    fishing_rod    rigged rod prop    (per-race rod table)

Anything the tables don't explain falls back to what its own sections say —
``zone`` (0x1C ZoneDef), ``model`` (0x2A SkeletonMesh), ``effect`` (0x05/0x07
only), ``image`` (textures only) — or ``unknown``.
"""

import json
import os
import re
import struct
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import click

from xi.audio import xi_core as core
from xi.audio import xi_names as names
from xi.audio.xi_refs import SECTION_SOUND_POINTER, _MAGIC
from xi.mv.dat_index import DatEntry
from xi.xi_config import FFXI_DIR

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# Cap on sections walked per file — a corrupt header must not become a
# multi-million-iteration loop. Real DATs stay well under this.
_SECTION_CAP = 4000


# ── walking the tree ─────────────────────────────────────────────────────────

def _fourcc(raw: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in raw[:4])


def _rom_index(name: str) -> int:
    digits = name[3:]
    return int(digits) if digits.isdigit() else 1


def dat_sort_key(rel: str) -> tuple:
    """``ROM3/5/7.DAT`` → ``(3, 5, 7)`` so listings run in ROM / subdir / index order."""
    parts = rel.split("/")
    try:
        return (_rom_index(parts[0].upper()), int(parts[1]), int(parts[2].split(".")[0]))
    except (IndexError, ValueError):
        return (1 << 30, 0, 0)


def iter_dats(base: Path, roms=()) -> list:
    """Every ``ROM*/<sub>/<idx>.DAT`` under ``base`` as ``(rom_rel, path)``, sorted.
    ``roms`` limits the walk to those ROM directory names (case-insensitive)."""
    want = {r.upper() for r in roms}
    out = []
    for romdir in sorted(base.glob("ROM*")):
        if not romdir.is_dir():
            continue
        if want and romdir.name.upper() not in want:
            continue
        for sub in romdir.iterdir():
            if not sub.is_dir() or not sub.name.isdigit():
                continue
            for f in sub.iterdir():
                if f.is_file() and f.suffix.upper() == ".DAT" and f.stem.isdigit():
                    out.append((f"{romdir.name}/{sub.name}/{f.stem}.DAT", f))
    out.sort(key=lambda t: dat_sort_key(t[0]))
    return out


def walk_sound_refs(path: Path):
    """Seek-walk one DAT's section headers.

    Returns ``(first FourCC, section type codes, refs)`` where ``refs`` is
    ``[(section_name, sound_id), …]`` in file order. Only the 16-byte headers and
    the 12-byte payload head of each ``0x3D`` section are read, so the whole ROM
    tree (~52k files) goes by in tens of seconds without touching section bodies.
    """
    types: set = set()
    refs: list = []
    first = None
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if f.read(len(_PNG_MAGIC)) == _PNG_MAGIC:
                return "PNG", frozenset(), refs
            pos = 0
            n = 0
            while pos + 16 <= size and n < _SECTION_CAP:
                f.seek(pos)
                hdr = f.read(16)
                if len(hdr) < 16:
                    break
                meta = struct.unpack_from("<I", hdr, 4)[0]
                sec_size = ((meta >> 7) & 0xFFFFF) * 0x10
                if sec_size <= 0:
                    break
                if first is None:
                    first = _fourcc(hdr)
                kind = meta & 0x7F
                types.add(kind)
                if kind == SECTION_SOUND_POINTER and sec_size >= 0x20:
                    head = f.read(12)  # the file position is data_start
                    if head[:len(_MAGIC)] == _MAGIC:
                        refs.append((_fourcc(hdr), struct.unpack_from("<I", head, 8)[0]))
                n += 1
                pos = (pos + sec_size + 15) & ~15
    except OSError:
        return None, frozenset(), refs
    return first, frozenset(types), refs


def section_kind(entry: DatEntry) -> str:
    """What a DAT's own sections say it holds: zone / model / effect / image / unknown."""
    if entry.is_zone:
        return "zone"
    if entry.is_model:
        return "model"
    if entry.is_effect:
        return "effect"
    if entry.is_image:
        return "image"
    return "unknown"


# ── what the file tables know about a DAT ───────────────────────────────────

def file_ids_by_dat(tables: dict) -> dict:
    """Inverse file tables: upper-cased ``ROM/x/y.DAT`` → every file_id addressing
    it, ascending. Per file_id the base table wins, then ROM2…ROM10 — the rule
    ``ftable.xi_core.scan_file_ids`` (and the client) uses."""
    from xi.ftable.xi_core import resolve_dat
    out: dict = defaultdict(list)
    ordered = sorted(tables.items())
    span = max((min(len(f) // 2, len(v)) for f, v in tables.values()), default=0)
    for fid in range(span):
        for _idx, (fdata, vdata) in ordered:
            dat, _vt = resolve_dat(fdata, vdata, fid)
            if dat:
                out[dat.upper()].append(fid)
                break
    return out


def known_file_ids(status: dict) -> dict:
    """``file_id → {kind, name, …}`` for every id a content formula can explain.

    Each source is independent and best-effort: a missing name table or server
    checkout is reported in ``status`` and that family simply comes through
    unnamed (or not at all). Where two sources claim one id the earlier, more
    specific one wins — explicit tables and narrow verified bands before the
    broad entity modelid bands.
    """
    known: dict = {}

    def add(fid: int, info: dict) -> None:
        known.setdefault(fid, info)

    # Zones: the zone-name table indexes by zone id; the four per-zone DATs
    # (model / event / dialog / NPC list) hang off that id by fixed formulas.
    try:
        from xi.zone.xi_inject import (zone_dialog_file_id, zone_event_file_id,
                                       zone_model_file_id, zone_npc_file_id)
        from xi.zone.xi_list import ZONE_NAME_DAT, parse_dmsg
        zone_names = parse_dmsg((Path(FFXI_DIR) / ZONE_NAME_DAT).read_bytes())
        parts = (("model", zone_model_file_id), ("event", zone_event_file_id),
                 ("dialog", zone_dialog_file_id), ("npc_list", zone_npc_file_id))
        count = 0
        for zone_id, name in enumerate(zone_names):
            name = name.strip()
            if not name or name in ("none", "?"):
                continue
            count += 1
            for part, fn in parts:
                add(fn(zone_id), {"kind": "zone", "name": name, "zone_id": zone_id, "part": part})
        status["zones"] = f"{count} named zones"
    except Exception as e:  # noqa: BLE001 — a missing/odd name table must not stop the scan
        status["zones"] = f"skipped: {e}"

    # Spells: 0xAF0 + animation, names from the spell d_msg table.
    try:
        from xi.spell.xi_spell import spell_catalog
        count = 0
        for sp in spell_catalog():
            add(sp["fileIndex"], {"kind": "spell", "name": sp["name"],
                                  "spell_id": sp["index"], "anim_id": sp["animIndex"]})
            count += 1
        status["spells"] = f"{count} spells"
    except Exception as e:  # noqa: BLE001
        status["spells"] = f"skipped: {e}"

    # Job abilities / weapon skills: fixed bands read off the FTABLE (see
    # xi.mv.update_lists); names need the server SQL.
    from xi.mv import update_lists as mv
    ability_names: dict = {}
    try:
        for aid, name, anim in mv._parse_ability_anims(mv._default_abilities_sql()):
            ability_names.setdefault(anim, (aid, mv._title_ability(name)))
    except Exception:  # noqa: BLE001
        pass
    for anim in range(mv._ABILITY_ANIM_MAX + 1):
        info = {"kind": "ability", "name": None, "anim_id": anim}
        if anim in ability_names:
            info["ability_id"], info["name"] = ability_names[anim]
        add(mv._ABILITY_FILE_OFFSET + anim, info)
    status["abilities"] = (f"{mv._ABILITY_ANIM_MAX + 1} animation slots, "
                           f"{len(ability_names)} named"
                           + ("" if ability_names else " (set XI_SERVER_DIR for names)"))

    ws_names: dict = {}
    try:
        from xi.mv.server_names import weapon_skill_anims
        for wsid, name, anim in weapon_skill_anims():
            ws_names.setdefault(anim, (wsid, name))
    except Exception:  # noqa: BLE001
        pass
    for anim in range(mv._WS_ANIM_MAX + 1):
        info = {"kind": "weapon_skill", "name": None, "anim_id": anim}
        if anim in ws_names:
            info["weapon_skill_id"], info["name"] = ws_names[anim]
        add(mv._WS_FILE_OFFSET + anim, info)
    status["weapon_skills"] = (f"{mv._WS_ANIM_MAX + 1} animation slots, {len(ws_names)} named"
                               + ("" if ws_names else " (set XI_SERVER_DIR for names)"))

    # Gear: per (race, slot) group tables from FFXiMain.dll.
    try:
        from xi.gear.xi_core import RACE_TABLES, SLOTS, parse_race_table, slot_file_ids
        count = 0
        for race, raw in RACE_TABLES.items():
            table = parse_race_table(raw)
            for slot in SLOTS:
                for model_id, fid in slot_file_ids(table[slot]):
                    add(fid, {"kind": "gear", "name": None, "race": race, "slot": slot,
                              "model_id": model_id})
                    count += 1
        status["gear"] = f"{count} race/slot entries"
    except Exception as e:  # noqa: BLE001
        status["gear"] = f"skipped: {e}"

    # Mounts: one model per id; names from the EN mount d_msg table.
    try:
        from xi.common import xi_dmsg as D
        from xi.mount import xi_core as M
        table = None
        try:
            table = M.load_table(M.MOUNT_NAME["en"], 0)
        except Exception:  # noqa: BLE001 — names are optional
            pass
        for mount_id in range(M.MODEL_CAP + 1):
            name = None
            if table is not None and mount_id < table.num:
                name = D.get_text(table.blocks[mount_id], 0) or None
            add(M.file_id_for(mount_id), {"kind": "mount", "name": name, "mount_id": mount_id})
        status["mounts"] = (f"{M.MODEL_CAP + 1} ids"
                            + ("" if table is not None else " (name table not found)"))
    except Exception as e:  # noqa: BLE001
        status["mounts"] = f"skipped: {e}"

    # Fishing rods: rigged props in a per-race table (race 0 reuses Hume M's block).
    try:
        from xi.gear.xi_core import FISHING_ROD_BASES, FISHING_ROD_MAX_MID, LOOK_RACE_NAMES
        for look_race, rod_base in FISHING_ROD_BASES.items():
            if look_race == 0:
                continue
            for mid in range(FISHING_ROD_MAX_MID + 1):
                add(rod_base + mid, {"kind": "fishing_rod", "name": None,
                                     "race": LOOK_RACE_NAMES.get(look_race), "model_id": mid})
        status["fishing_rods"] = f"{len(FISHING_ROD_BASES) - 1} races"
    except Exception as e:  # noqa: BLE001
        status["fishing_rods"] = f"skipped: {e}"

    # Entities: the four monster modelid bands (broad formula ranges, so last).
    try:
        from xi.entity.xi_core import MAX_3500_MODELID, RANGES
        entity_names: dict = {}
        try:
            from xi.mv.server_names import model_names
            entity_names = model_names()
        except Exception:  # noqa: BLE001 — names are optional
            pass
        count = 0
        for start, end, offset in RANGES:
            hi = end if end is not None else MAX_3500_MODELID
            for model_id in range(start, hi + 1):
                info = {"kind": "entity", "name": None, "model_id": model_id}
                nm = entity_names.get(model_id)
                if nm:
                    info["name"] = nm["name"]
                    info["category"] = nm["category"]
                    info["source"] = nm["source"]
                add(model_id + offset, info)
                count += 1
        status["entities"] = (f"{count:,} modelid slots, {len(entity_names):,} named"
                              + ("" if entity_names else " (set XI_SERVER_DIR for names)"))
    except Exception as e:  # noqa: BLE001
        status["entities"] = f"skipped: {e}"

    return known


def known_paths() -> dict:
    """Curated DATs the tables cannot name: dev/prototype maps and mog houses."""
    from xi.zone.xi_list import DEV_GROUP, DEV_ZONES, MOG_HOUSE_NAMES
    out = {}
    for dat, name in DEV_ZONES:
        out[dat.upper()] = {"kind": "zone", "name": name, "group": DEV_GROUP}
    for dat, name in MOG_HOUSE_NAMES.items():
        out[dat.upper()] = {"kind": "zone", "name": name, "group": "Rooms"}
    return out


def identify(rel: str, entry: DatEntry, fids: list, known: dict, paths: dict) -> dict:
    """Name one DAT: curated path first, then the lowest file_id a content formula
    explains, else what its sections hold. Always carries ``file_id`` (lowest
    registration, or the identifying one) and ``kind_source``."""
    key = rel.upper()
    hit = paths.get(key)
    source = "curated" if hit else None
    file_id = fids[0] if fids else None
    if hit is None:
        for fid in fids:
            k = known.get(fid)
            if k:
                hit, source, file_id = k, "file_table", fid
                break
    if hit is None:
        hit, source = {"kind": section_kind(entry), "name": None}, "sections"
    out = {"file_id": file_id}
    if len(fids) > 1:
        out["file_ids"] = list(fids)
    out.update(hit)
    out["kind_source"] = source
    return out


# ── the command ──────────────────────────────────────────────────────────────

def _unreferenced(base: Path, referenced: set) -> list:
    """Every ``.spw`` on disk whose id no DAT points at (one row per id)."""
    seen: set = set()
    rows = []
    for e in core.iter_entries(core.SFX, base):
        m = re.match(r"se0*(\d+)$", e.stem, re.I)
        if not m:
            continue
        sid = int(m.group(1))
        if sid in referenced or sid in seen:
            continue
        seen.add(sid)
        rows.append({"sound_id": sid, "file": e.stem, "root": e.root,
                     "title": names.sfx_name(sid), "category": names.sfx_category(sid)})
    rows.sort(key=lambda r: r["sound_id"])
    return rows


@click.command("scan")
@click.option("--out", "out_json", type=click.Path(), default=None,
              help="Write JSON here (default: exports/audio/scan.json).")
@click.option("--stdout", "to_stdout", is_flag=True,
              help="Print the JSON to stdout instead of writing a file.")
@click.option("--rom", "roms", multiple=True, metavar="ROMn",
              help="Only walk these ROM directories (repeatable), e.g. --rom ROM --rom ROM3.")
@click.option("--sound", "sound_ids", multiple=True, type=int, metavar="ID",
              help="Only report these sound ids (repeatable). Every DAT is still walked.")
@click.option("--lookup/--no-lookup", default=True, show_default=True,
              help="Identify each DAT through the file tables (zone / entity / spell / gear …). "
                   "--no-lookup records only the DAT path and what its sections hold.")
@click.option("--unused", is_flag=True,
              help="Also list every .spw on disk that no DAT references.")
@click.option("--limit", type=int, default=0,
              help="Stop after N DATs (0 = all). For a quick test.")
def scan_cmd(out_json, to_stdout, roms, sound_ids, lookup, unused, limit):
    """Find where every sound effect is used: walk every DAT, record each 0x3D
    sound reference, and identify the DAT it sits in (zone, NPC model, spell,
    gear …) — one JSON for the whole install.

    \b
      xi audio scan                          # -> exports/audio/scan.json
      xi audio scan --sound 5048 --stdout    # who plays se005048?
      xi audio scan --rom ROM3 --unused      # one ROM, plus never-referenced .spw
      xi audio scan --no-lookup --limit 500  # quick structural pass
    """
    base = Path(FFXI_DIR)
    if not base.is_dir():
        raise click.ClickException(f"FFXI_DIR not found: {base}  (set the FFXI_DIR env var)")

    t0 = time.time()
    dats = iter_dats(base, roms)
    if limit:
        dats = dats[:limit]
    if not dats:
        raise click.ClickException(f"No ROM*/<sub>/<idx>.DAT files under {base}"
                                   + (f" for {', '.join(roms)}" if roms else ""))

    per_dat: dict = {}
    with click.progressbar(dats, label="scanning DATs", show_pos=True, file=sys.stderr) as bar:
        for rel, path in bar:
            fourcc, types, refs = walk_sound_refs(path)
            if refs:
                per_dat[rel] = (DatEntry(rel.upper(), fourcc, types, 0), refs)

    status: dict = {}
    known: dict = {}
    paths: dict = {}
    reverse: dict = {}
    if lookup:
        try:
            from xi.ftable.xi_core import load_all_tables
            tables = load_all_tables()
            reverse = file_ids_by_dat(tables)
            status["file_table"] = (f"{len(tables)} table(s), "
                                    f"{sum(len(v) for v in reverse.values()):,} registrations")
        except Exception as e:  # noqa: BLE001
            status["file_table"] = f"skipped: {e}"
        known = known_file_ids(status)
        paths = known_paths()

    dat_rows: dict = {}
    by_sound: dict = defaultdict(dict)
    for rel, (entry, refs) in per_dat.items():
        ident = identify(rel, entry, reverse.get(rel.upper(), []), known, paths)
        dat_rows[rel] = {
            "dat": rel,
            **ident,
            "content": section_kind(entry),
            "ref_count": len(refs),
            "sound_ids": sorted({sid for _, sid in refs}),
        }
        for section, sid in refs:
            use = by_sound[sid].setdefault(rel, {"count": 0, "sections": []})
            use["count"] += 1
            if section not in use["sections"]:
                use["sections"].append(section)

    want = set(sound_ids)
    sounds = []
    for sid in sorted(by_sound):
        if want and sid not in want:
            continue
        folder, file = names.sound_id_to_folder_file(sid)
        located = core.locate_sound(base, sid)
        used_in = []
        for rel, use in by_sound[sid].items():
            row = dat_rows[rel]
            used_in.append({"dat": rel, "file_id": row["file_id"], "kind": row["kind"],
                            "name": row["name"], "count": use["count"],
                            "sections": use["sections"]})
        sounds.append({
            "sound_id": sid,
            "folder": f"se{folder}",
            "file": f"se{file}",
            "spw": names.sound_id_to_relpath(sid),
            "title": names.sfx_name(sid),
            "category": names.sfx_category(sid),
            "exists": located is not None,
            "located_root": located[1] if located else None,
            "use_count": sum(u["count"] for u in used_in),
            "dat_count": len(used_in),
            "used_in": used_in,
        })

    dat_list = list(dat_rows.values())
    if want:
        dat_list = [r for r in dat_list if want.intersection(r["sound_ids"])]

    kinds = Counter(r["kind"] for r in dat_rows.values())
    payload = {
        "ffxi_dir": str(base),
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "roms": sorted({rel.split("/")[0] for rel, _ in dats}, key=lambda r: _rom_index(r.upper())),
        "dat_count": len(dats),
        "dats_with_sounds": len(dat_rows),
        "ref_count": sum(len(refs) for _, refs in per_dat.values()),
        "unique_sound_count": len(by_sound),
        "missing_count": sum(1 for s in sounds if not s["exists"]),
        "lookup": status if lookup else None,
        "kinds": {k: n for k, n in kinds.most_common()},
        "sounds": sounds,
        "dats": dat_list,
    }
    if unused:
        rows = _unreferenced(base, set(by_sound))
        payload["unreferenced_count"] = len(rows)
        payload["unreferenced"] = rows

    blob = json.dumps(payload, ensure_ascii=False, indent=2)
    if to_stdout:
        click.echo(blob)
        return

    out = Path(out_json) if out_json else Path("exports") / "audio" / "scan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(blob, encoding="utf-8")

    click.echo(f"Scanned {len(dats):,} DAT(s) in {time.time() - t0:.1f}s: "
               f"{len(dat_rows):,} reference sounds, {payload['ref_count']:,} reference(s), "
               f"{len(by_sound):,} unique sound id(s), {payload['missing_count']} missing on disk"
               f" -> {out}")
    if kinds:
        click.echo("  by kind: " + ", ".join(f"{k} {n:,}" for k, n in kinds.most_common()))
    for key, note in status.items():
        click.echo(f"  {key}: {note}")
    if unused:
        click.echo(f"  unreferenced .spw on disk: {payload['unreferenced_count']:,}")

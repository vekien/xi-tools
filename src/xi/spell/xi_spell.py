"""Spell catalog + spell→effect resolution + EffectRoutine flattening.

Faithful port of the UE5 client's spell→VFX chain (``FFXIEngineRuntime/spell/SpellTables``
+ ``particle/EffectRoutineInstance``):

    spellIndex
      → animationIndex            (spell_anim_table.json, generated from LSB spell_list.sql)
      → fileIndex = 0xAF0 + anim  (SpellTables.h kFileTableOffset)
      → ROM/x/y.DAT               (ftable scan_file_ids)
      → 0x07 "main" EffectRoutine → timed schedule of 0x05 generator spawns.

The spell→animation map is *server* data (the retail client gets the animation id from the
action packet), so it is not present in the client DATs — we carry the table the UE5 reimpl
generated from LandSandBoat's ``sql/spell_list.sql`` (self-contained; no server required).
Spell *names* come from ROM/181/73.DAT (a ``d_msg`` table, **no XOR** — bitmask 0, unlike the
zone-name table which is XOR 0xFF).
"""

from __future__ import annotations

import base64
import json
from functools import lru_cache
from pathlib import Path

from xi.event.xi_event import _routine_sec2_commands, _scene_sections
from xi.ftable.xi_core import load_all_tables, scan_file_ids
from xi.xi_config import FFXI_DIR
from xi.zone.xi_list import parse_dmsg

# ── Constants ───────────────────────────────────────────────────────────────────────
SPELL_NAME_DAT = "ROM/181/73.DAT"        # xim SpellNameTable; d_msg, bitmask 0 (NO xor)
FILE_TABLE_OFFSET = 0xAF0                 # SpellTables.h kFileTableOffset
_ANIM_TABLE_PATH = Path(__file__).with_name("spell_anim_table.json")

# 0x07 EffectRoutine command opcodes we act on (EffectRoutineInstance.cpp dispatch).
_OP_END = 0x00
_OP_GEN = 0x02                            # ParticleGeneratorRoutine: ref=generator, dur=maxEmit
_OP_LINKED = frozenset({0x03, 0x09, 0x3B, 0x3C, 0x57})  # call a child 0x07 routine
_OP_STOP = frozenset({0x1E, 0x2D})        # dampen / stop a generator


# ── Static tables (cached) ──────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def load_anim_table() -> dict[int, int]:
    """spell index → animation-table index (ported from the UE5 builtin fallback)."""
    raw = json.loads(_ANIM_TABLE_PATH.read_text())
    return {int(k): int(v) for k, v in raw.items()}


@lru_cache(maxsize=1)
def load_spell_names() -> list[str]:
    """Spell names indexed by spell index (ROM/181/73.DAT, d_msg, no XOR)."""
    data = (Path(FFXI_DIR) / SPELL_NAME_DAT).read_bytes()
    return parse_dmsg(data, bitmask=0)


@lru_cache(maxsize=1)
def _tables():
    return load_all_tables()


def file_index_for(index: int) -> int | None:
    """0xAF0 + animationIndex, or None when the spell has no animation entry."""
    anim = load_anim_table().get(index)
    return None if anim is None else FILE_TABLE_OFFSET + anim


def resolve_spell_dat_rel(index: int) -> str | None:
    """Resolved ``ROM/x/y.DAT`` (relative) for the spell's effect routine, or None."""
    fid = file_index_for(index)
    if fid is None:
        return None
    hits = scan_file_ids([fid], _tables())
    return hits[0]["dat"] if hits else None


# ── Catalog ─────────────────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def spell_catalog() -> list[dict]:
    """Every named, animation-mapped spell → ``{index, name, animIndex, fileIndex, dat}``.

    Sorted by index.  Spells without a name or without an animation entry are skipped
    (they have no spawnable effect).  ``dat`` is None when the ftable can't resolve the
    file index (rare — keeps the entry visible but flags it unspawnable)."""
    names = load_spell_names()
    anim = load_anim_table()
    tables = _tables()

    # Batch-resolve every candidate file index in one ftable pass.
    candidates = []
    for index, animIndex in anim.items():
        name = names[index].strip() if 0 <= index < len(names) else ""
        if not name or name in ("none", "?", "."):
            continue
        candidates.append((index, animIndex, name))

    fids = {idx: FILE_TABLE_OFFSET + a for idx, a, _ in candidates}
    hits = scan_file_ids(sorted(set(fids.values())), tables)
    dat_by_fid = {h["file_id"]: h["dat"] for h in hits}

    out = []
    for index, animIndex, name in candidates:
        fid = fids[index]
        out.append({
            "index": index,
            "name": name,
            "animIndex": animIndex,
            "fileIndex": fid,
            "dat": dat_by_fid.get(fid),
        })
    out.sort(key=lambda s: s["index"])
    return out


# ── EffectRoutine → timed schedule ──────────────────────────────────────────────────
def parse_spell_schedule(data: bytes, root: str = "main") -> dict:
    """Flatten a spell DAT's ``main`` 0x07 EffectRoutine into a timed schedule.

    Walks the routine command stream maintaining a frame clock (each command's ``delay``
    advances it *before* the command fires, matching ``EffectRoutineInstance::runReadyEffects``),
    following linked sub-routines (Cure/Protect fire their generators via a ``tgt0`` child).

    Returns ``{root, total, generators:[id…], schedule:[{kind, ref, start, dur, op}]}`` where
    ``kind`` is ``"gen"`` (spawn generator ``ref`` for ``dur`` frames at ``start``) or
    ``"stop"`` (stop generator ``ref`` at ``start``).  Generators fired by routines that live
    in another DAT (the shared caster cast/shadow routines like ``shbk``/``shwh``) aren't
    expanded — those are caster-side and not part of a standalone effect preview."""
    routine_tags = {tag for _, tag, tc, _ in _scene_sections(data) if tc == 0x07}
    if not routine_tags:
        return {"root": None, "total": 0, "generators": [], "schedule": []}
    if root not in routine_tags:
        # Prefer 'main', else the first routine in file order.
        root = next((tag for _, tag, tc, _ in _scene_sections(data) if tc == 0x07), None)
        if root is None:
            return {"root": None, "total": 0, "generators": [], "schedule": []}

    schedule: list[dict] = []
    gens: list[str] = []

    def walk(tag: str, base: float, visited: frozenset, depth: int) -> None:
        if depth > 16 or tag in visited:
            return
        visited = visited | {tag}
        clock = base
        for c in _routine_sec2_commands(data, tag):
            clock += c.get("delay", 0)
            op, ref = c["op"], c.get("ref")
            if op == _OP_END:
                break
            if op == _OP_GEN and ref:
                schedule.append({"op": op, "kind": "gen", "ref": ref,
                                 "start": int(clock), "dur": int(c.get("dur", 0))})
                if ref not in gens:
                    gens.append(ref)
            elif op in _OP_LINKED and ref and ref in routine_tags:
                walk(ref, clock, visited, depth + 1)
            elif op in _OP_STOP and ref:
                schedule.append({"op": op, "kind": "stop", "ref": ref,
                                 "start": int(clock), "dur": 0})

    walk(root, 0.0, frozenset(), 0)
    schedule.sort(key=lambda e: e["start"])
    total = max((e["start"] + e["dur"] for e in schedule), default=0)
    return {"root": root, "total": int(total), "generators": gens, "schedule": schedule}


# ── Bridge payloads ─────────────────────────────────────────────────────────────────
def spell_list_payload() -> dict:
    """Catalog for the editor asset browser. Only spawnable (dat-resolved) spells."""
    cat = [s for s in spell_catalog() if s["dat"]]
    return {"ok": True, "count": len(cat), "spells": cat}


def spell_vfx_payload(index: int) -> dict:
    """Resolve a spell → its effect DAT bytes (base64) + flattened routine schedule.

    The frontend parses the bytes with ``parseAllEffects`` (so it owns the generator/mesh/
    texture decode it already has) and drives emission from ``schedule``."""
    cat = {s["index"]: s for s in spell_catalog()}
    info = cat.get(int(index))
    if not info:
        return {"ok": False, "error": f"unknown spell index {index}"}
    dat = info["dat"]
    if not dat:
        return {"ok": False, "error": f"spell {index} ({info['name']}) has no resolvable DAT"}
    path = Path(FFXI_DIR) / dat
    if not path.is_file():
        return {"ok": False, "error": f"spell DAT not found: {dat}"}
    data = path.read_bytes()
    sched = parse_spell_schedule(data)
    return {
        "ok": True,
        "index": info["index"],
        "name": info["name"],
        "dat": dat,
        "bytesBase64": base64.b64encode(data).decode("ascii"),
        "root": sched["root"],
        "total": sched["total"],
        "generators": sched["generators"],
        "schedule": sched["schedule"],
    }

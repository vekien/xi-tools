#!/usr/bin/env python3
"""`xi ftable tables` / `xi ftable info` — see the FTABLE/VTABLE layout.

``tables`` is the per-ROM disk view: allocated entries, how many are registered
(non-zero VTABLE), the highest used file_id, and on-disk sizes — split into the
custom *entity* and *gear* regions so the high gear file_ids aren't mistaken for
entity model ids.

``info`` is the human view: the custom model_id / gear_id ranges, where the
entity↔gear boundary sits, what's already used, and the recommended starting
ids for new content.
"""

import json
from pathlib import Path

import click

from xi.xi_config import (FFXI_DIR, read_path_for, MAX_ENTITY_MODELID,
                            MAX_GEAR_MODELID, GEAR_RECOMMENDED_START, CUSTOM_ROM)
from xi.ftable.xi_core import ftable_path, vtable_path
from xi.entity.xi_core import MODEL_FILE_OFFSET, MODEL_SAFE_START, MODEL_SAFE_END
from xi.gear.xi_inject import CUSTOM_GEAR_BASE

# How many ROM indices to probe. Base ROM is 1; expansions/custom go above.
_MAX_ROM = 20

# First file_id of the CUSTOM entity band (above retail; below the gear floor).
_ENTITY_CUSTOM_FID = MODEL_FILE_OFFSET + MODEL_SAFE_START


def _highest_nonzero(data: bytes, start: int, end: int):
    """Highest index in data[start:end] holding a non-zero byte, or None."""
    end = min(end, len(data))
    if start >= end:
        return None
    trimmed = data[start:end].rstrip(b"\x00")
    return start + len(trimmed) - 1 if trimmed else None


def _count_nonzero(data: bytes, start: int, end: int) -> int:
    """Number of non-zero bytes in data[start:end]."""
    end = min(end, len(data))
    if start >= end:
        return 0
    seg = data[start:end]
    return len(seg) - seg.count(0)


def _table_info(rom_idx: int) -> dict | None:
    """Gather size/registration info for one ROM's FTABLE/VTABLE pair, or None
    if neither file exists. Registration counts are split into the entity-custom
    band and the gear region so the two custom spaces are reported separately."""
    ft_base = Path(ftable_path(rom_idx))
    vt_base = Path(vtable_path(rom_idx))
    ft_read = Path(read_path_for(ft_base))
    vt_read = Path(read_path_for(vt_base))
    ft_exists = ft_read.exists()
    vt_exists = vt_read.exists()
    if not ft_exists and not vt_exists:
        return None

    info = {
        "rom": rom_idx,
        "ftable": str(ft_base.relative_to(FFXI_DIR)).replace("\\", "/")
        if _under(ft_base) else str(ft_base),
        "vtable": str(vt_base.relative_to(FFXI_DIR)).replace("\\", "/")
        if _under(vt_base) else str(vt_base),
        "ftable_exists": ft_exists,
        "vtable_exists": vt_exists,
        "entries": None,
        "registered": None,
        "last_file_id": None,
        # Split registration: custom entity band vs gear region.
        "entity_registered": None,
        "gear_registered": None,
        "highest_entity_modelid": None,
        "max_entity_modelid": None,
        "ftable_bytes": None,
        "vtable_bytes": None,
        # True when the effective file differs from the base path (only the
        # case while a dats-build redirect is active).
        "edited": ft_read.resolve() != ft_base.resolve(),
    }

    if ft_exists:
        fd = ft_read.read_bytes()
        info["ftable_bytes"] = len(fd)
        info["entries"] = len(fd) // 2          # FTABLE is uint16 per file_id
    if vt_exists:
        vd = vt_read.read_bytes()
        info["vtable_bytes"] = len(vd)          # VTABLE is uint8 per file_id
        info["registered"] = len(vd) - vd.count(0)
        trimmed = vd.rstrip(b"\x00")
        info["last_file_id"] = (len(trimmed) - 1) if trimmed else None  # None when empty

        # Custom entity band: [offset+MODEL_SAFE_START, gear floor). Anything
        # registered here is a custom monster/NPC/object — retail content sits
        # below this band, gear sits above it.
        info["entity_registered"] = _count_nonzero(vd, _ENTITY_CUSTOM_FID, CUSTOM_GEAR_BASE)
        he = _highest_nonzero(vd, _ENTITY_CUSTOM_FID, CUSTOM_GEAR_BASE)
        if he is not None:
            info["highest_entity_modelid"] = he - MODEL_FILE_OFFSET
        # Gear region: [gear floor, end). Pointers + injected gear.
        info["gear_registered"] = _count_nonzero(vd, CUSTOM_GEAR_BASE, len(vd))

    # Highest entity modelid this table can ADDRESS — capped at the gear floor,
    # because file_ids at/above it are gear, not entities.
    if info["entries"]:
        addressable_fid = min(info["entries"] - 1, CUSTOM_GEAR_BASE - 1)
        ceiling = addressable_fid - MODEL_FILE_OFFSET
        info["max_entity_modelid"] = ceiling if ceiling >= 0 else None
    return info


def _under(p: Path) -> bool:
    try:
        p.relative_to(FFXI_DIR)
        return True
    except ValueError:
        return False


def _kb(n) -> str:
    return f"{n / 1024:,.1f} KB" if n is not None else "-"


@click.command("tables")
@click.option("--json", "as_json", is_flag=True,
              help="Emit JSON instead of a table.")
def tables_cmd(as_json):
    """List every FTABLE/VTABLE pair and its current size.

    Per ROM: allocated entries, total registered (non-zero) entries, how many of
    those are custom entities vs gear, the highest used file_id, and on-disk
    sizes.

    Run ``xi ftable info`` for the entity/gear model-id ranges and free slots.

    \b
    Examples:
      xi ftable tables
      xi ftable tables --json
    """
    infos = [info for i in range(1, _MAX_ROM + 1) if (info := _table_info(i))]

    if not infos:
        click.echo("No FTABLE/VTABLE pairs found under FFXI_DIR.")
        return

    if as_json:
        click.echo(json.dumps(infos, indent=2))
        return

    def _n(v):
        return f"{v:,}" if v is not None else "-"

    header = (f"{'ROM':<5}{'entries':>10}{'registered':>12}{'ent_custom':>12}"
              f"{'last_fid':>10}{'FTABLE':>11}{'VTABLE':>11}  {'files'}")
    click.echo(header)
    click.echo("-" * len(header))
    for info in infos:
        rom = f"{info['rom']}{'*' if info['edited'] else ''}"
        miss = []
        if not info["ftable_exists"]:
            miss.append("FTABLE missing")
        if not info["vtable_exists"]:
            miss.append("VTABLE missing")
        files = ", ".join(miss) if miss else f"{info['ftable']} / {Path(info['vtable']).name}"
        click.echo(f"{rom:<5}{_n(info['entries']):>10}{_n(info['registered']):>12}"
                   f"{_n(info['entity_registered']):>12}{_n(info['last_file_id']):>10}"
                   f"{_kb(info['ftable_bytes']):>11}{_kb(info['vtable_bytes']):>11}  {files}")

    click.echo("-" * len(header))
    edited = sum(1 for i in infos if i["edited"])
    note = f"  ({edited} from output mirror)" if edited else ""
    click.echo(f"{len(infos)} table pair(s){note}")
    click.echo("ent_custom = registered custom entities (modelid >= "
               f"{MODEL_SAFE_START:,}).")
    click.echo("Run `xi ftable info` for the model-id / gear-id ranges and what's free.")


def _slot_minimums() -> str:
    """Compact 'minimum custom model_id per slot' summary grouped by value."""
    from xi.gear.xi_inject import CUSTOM_MODEL_START
    by_val: dict[int, list[str]] = {}
    for slot, start in CUSTOM_MODEL_START.items():
        by_val.setdefault(start, []).append(slot)
    parts = [f"{'/'.join(slots)} {val:,}" for val, slots in sorted(by_val.items())]
    return " · ".join(parts)


@click.command("info")
def info_cmd():
    """Show the custom model-id / gear-id ranges, what's used, and what's free.

    Prints the entity (monster/NPC/object) and gear id ranges, the entity↔gear
    file_id boundary, how much is already registered, and the recommended
    starting ids for new content.
    """
    from xi.ftable.xi_expand import RETAIL_ENTRIES
    from xi.gear.xi_inject import (gear_ftable_target, _load_state, RACES)
    from xi.gear.xi_core import SLOTS

    ent_lo_fid = MODEL_FILE_OFFSET + MODEL_SAFE_START
    ent_hi_fid = MODEL_FILE_OFFSET + MODEL_SAFE_END        # = CUSTOM_GEAR_BASE - 1
    gear_target = gear_ftable_target(MAX_GEAR_MODELID)

    base = _table_info(1)
    entries = base["entries"] if base else 0
    provisioned = entries >= gear_target

    # What's actually registered right now (base ROM VTABLE).
    cur_ent = base["highest_entity_modelid"] if base else None
    ent_used = base["entity_registered"] if base else 0
    gear_used = base["gear_registered"] if base else 0

    state = _load_state()
    expand_max = state.get("expand_max")
    gear_injected = sum(len(v) for v in state.get("injected", {}).values())

    def line(label, value):
        click.echo(f"    {label:<20}: {value}")

    click.echo("=" * 64)
    click.echo("  xi custom id ranges")
    click.echo("=" * 64)
    line("Tables provisioned", f"{entries:,} entries"
         + ("" if provisioned else f"  (NOT fully expanded — need {gear_target:,}; "
            f"run `xi ftable expand`)"))
    click.echo()

    click.echo("  ENTITY  (monsters / NPCs / objects)      file_id = modelid + "
               f"{MODEL_FILE_OFFSET:,}")
    line("usable modelid", f"{MODEL_SAFE_START:,} - {MAX_ENTITY_MODELID:,}"
         f"   ({MAX_ENTITY_MODELID - MODEL_SAFE_START + 1:,} slots)")
    line("file_id range", f"{ent_lo_fid:,} - {ent_hi_fid:,}")
    line("recommended start", f"{MODEL_SAFE_START:,}+")
    line("registered now", f"{ent_used:,}"
         + (f"  (highest modelid {cur_ent:,})" if cur_ent is not None else "  (none yet)"))
    click.echo()

    click.echo("  GEAR  (per race + slot)                  file_id = "
               f"{CUSTOM_GEAR_BASE:,} + (race*{len(SLOTS)}+slot)*{MAX_GEAR_MODELID + 1:,} + modelid")
    line("usable modelid", f"per (race, slot), up to {MAX_GEAR_MODELID:,}")
    line("per-slot minimum", _slot_minimums())
    line("recommended start", f"{GEAR_RECOMMENDED_START:,}+")
    line("gear file_id range", f"{CUSTOM_GEAR_BASE:,} - {gear_target - 1:,}")
    line("window size", f"{MAX_GEAR_MODELID + 1:,} file_ids x "
         f"{len(RACES)} races x {len(SLOTS)} slots")
    line("registered now", f"{gear_used:,} table entries"
         + (f"  ({gear_injected} injected model(s))" if gear_injected else
            "  (armor pointers only)"))
    if expand_max is not None and expand_max != MAX_GEAR_MODELID:
        line("note", f"tables were last expanded with max gear {expand_max:,}, "
             f"config now {MAX_GEAR_MODELID:,} — reset + re-expand to align")
    click.echo()

    ent_fids = ent_hi_fid - ent_lo_fid + 1
    gear_fids = gear_target - CUSTOM_GEAR_BASE
    windows = len(RACES) * len(SLOTS)
    click.echo("  Layout (file_id):")
    click.echo(f"    {0:>9,} - {RETAIL_ENTRIES - 1:>9,}   retail content")
    click.echo(f"    {ent_lo_fid:>9,} - {ent_hi_fid:>9,}   custom entity band   "
               f"({ent_fids:,} file_ids)")
    click.echo(f"    {CUSTOM_GEAR_BASE:>9,} - {gear_target - 1:>9,}   custom gear bands    "
               f"({gear_fids:,} file_ids, {windows} windows)")
    click.echo("=" * 64)

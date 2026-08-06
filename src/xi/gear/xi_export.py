#!/usr/bin/env python3
"""`xi gear export` — export a gear model (skeleton + mesh + textures) to GLB/FBX.

Resolves the race/slot/model_id to its DAT file via the embedded gear tables, then
delegates to the entity mesh export pipeline — the binary format is identical.

Output mirrors the ROM path (identical to every other xi export):
  exports/gear/rom/<sub>/<file>/<model>.glb
  exports/gear/rom/<sub>/<file>/<texture>.png  (one per texture)
  exports/gear/rom/<sub>/<file>/<model>.fbx    (if --fbx, requires Blender)
"""

from pathlib import Path
from typing import List, Optional

import click

from xi.xi_config import XI_TOOLS_DIR, FFXI_DIR, SCHEMA_GENERATION
from xi.gear.xi_core import (RACE_TABLES, RACE_SKELETON_DATS, SLOTS,
                               parse_race_table, slot_file_ids, match_race, detect_gear)
from xi.ftable.xi_core import load_all_tables, scan_file_ids
from xi.entity.mesh.xi_export import export_dat


def race_skeleton_dat(race: str) -> Path:
    """Full path to a race's body skeleton DAT. Gear mesh DATs have no skeleton
    of their own; they are rigged against this shared race skeleton."""
    rel = RACE_SKELETON_DATS.get(race)
    if rel is None:
        raise ValueError(f"No skeleton DAT mapped for race '{race}'")
    full = Path(FFXI_DIR) / rel
    if not full.exists():
        raise FileNotFoundError(f"Race skeleton DAT not found on disk: {full}")
    return full


def resolve_gear_dat(race: str, slot: str, model_id: int) -> Path:
    """Resolve a gear (race, slot, model_id) triple to its full DAT path."""
    if race not in RACE_TABLES:
        raise ValueError(f"Unknown race '{race}'. Valid: {', '.join(RACE_TABLES)}")
    if slot not in SLOTS:
        raise ValueError(f"Unknown slot '{slot}'. Valid: {', '.join(SLOTS)}")

    race_table = parse_race_table(RACE_TABLES[race])
    entries = slot_file_ids(race_table[slot])
    match = next((fid for mid, fid in entries if mid == model_id), None)
    if match is None:
        raise ValueError(f"model_id {model_id} not found in {race}/{slot} "
                         f"(valid range 0–{max((mid for mid, _ in entries), default=-1)})")

    tables = load_all_tables()
    resolved = scan_file_ids([match], tables)
    if not resolved:
        raise ValueError(f"file_id {match} ({race}/{slot}/{model_id}) not registered in any FTABLE")

    full = Path(FFXI_DIR) / resolved[0]["dat"]
    if not full.exists():
        raise FileNotFoundError(f"DAT not found on disk: {full}")
    return full


def resolve_gear_target(race, slot=None, model_id=None):
    """Resolve a gear target from either an explicit ``(race, slot, model_id)``
    triple or a single DAT-path / file_id (race/slot/model_id auto-detected from
    the gear tables). Returns ``(dat_path: Path, race: str, slot: str, model_id: int)``.

    This lets every gear command make race/slot/model_id optional: pass them
    explicitly, or pass just a DAT path (``ROM/33/17``) or file_id and let the
    reverse lookup identify the race.
    """
    race_key = match_race(race)
    if race_key is not None:
        # Explicit race — slot and model_id are then required.
        if slot is None or model_id is None:
            raise ValueError(
                f"{race_key}: SLOT and MODEL_ID are required when a race is given "
                f"(or pass a DAT path / file_id on its own to auto-detect the race)")
        slot = slot.lower()
        return resolve_gear_dat(race_key, slot, model_id), race_key, slot, model_id

    # First argument is not a race — treat it as a DAT path / file_id and detect.
    if slot is not None or model_id is not None:
        raise ValueError(
            f"Unknown race '{race}'. Valid races: {', '.join(RACE_TABLES)}.\n"
            f"(To auto-detect, pass a DAT path or file_id on its own, "
            f"with no SLOT/MODEL_ID.)")

    race_key, slot, model_id, _file_id = detect_gear(race)
    return resolve_gear_dat(race_key, slot, model_id), race_key, slot, model_id


def gear_file_id(race: str, slot: str, model_id: int):
    """file_id backing a (race, slot, model_id) gear triple, or None if absent."""
    race_table = parse_race_table(RACE_TABLES[race])
    for mid, fid in slot_file_ids(race_table[slot]):
        if mid == model_id:
            return fid
    return None


def gear_metadata(race: str, slot: str, model_id: int) -> dict:
    """Gear-identity fields embedded in the export metadata JSON."""
    return {
        "race": race,
        "slot": slot,
        "model_id": model_id,
        "file_id": gear_file_id(race, slot, model_id),
    }


def default_gear_output_dir(dat_path: Path) -> Path:
    """Default export dir for a gear model: ``exports/gear/rom/<sub>/<file>/`` —
    mirrors the ROM path, identical to every other xi export (mesh, zone, anim,
    object, ...). race/slot are still resolved for the skeleton + metadata, but no
    longer nest the output directory: one DAT maps to exactly one folder. DATs
    outside FFXI_DIR fall back to ``exports/gear/<stem>/``."""
    base = Path(XI_TOOLS_DIR) / "exports" / "gear"
    try:
        rel = dat_path.resolve().relative_to(Path(FFXI_DIR).resolve())
    except ValueError:
        return base / dat_path.stem
    # rel like ROM/33/17.DAT -> rom/33/17
    return base.joinpath(rel.parts[0].lower(), *rel.parts[1:-1], rel.stem)


def legacy_gear_output_dir(race: str, slot: str, dat_path: Path) -> Path:
    """Pre-flatten gear export location (``exports/gear/<race>/<slot>/rom/...``),
    kept only so `gear import` can still auto-find models exported before the
    layout was flattened to match the rest of xi."""
    base = Path(XI_TOOLS_DIR) / "exports" / "gear" / race / slot
    try:
        rel = dat_path.resolve().relative_to(Path(FFXI_DIR).resolve())
    except ValueError:
        return base / dat_path.stem
    return base.joinpath(rel.parts[0].lower(), *rel.parts[1:-1], rel.stem)


def gear_export(race, slot=None, model_id=None, output_dir: Optional[Path] = None,
                fbx: bool = True, all_parts: bool = True, part: Optional[int] = None,
                mesh_merge_dp: int = 2, use_base: bool = True, weld: bool = True,
                split_tex: bool = False, write_schema: bool = SCHEMA_GENERATION) -> List[Path]:
    dat_path, race, slot, model_id = resolve_gear_target(race, slot, model_id)
    out = output_dir or default_gear_output_dir(dat_path)
    _all_parts = all_parts if part is None else False
    _lod = part if part is not None else 0
    return export_dat(dat_path, out, fbx=fbx, skeleton_dat=race_skeleton_dat(race),
                      extra_metadata=gear_metadata(race, slot, model_id),
                      all_parts=_all_parts, lod=_lod, mesh_merge_dp=mesh_merge_dp,
                      use_base=use_base, weld=weld, split_tex=split_tex,
                      write_schema=write_schema)


@click.command("export")
@click.argument("race", metavar="RACE|DAT|FILE_ID")
@click.argument("slot", required=False, metavar="[SLOT]")
@click.argument("model_id", required=False, type=int, metavar="[MODEL_ID]")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None,
              help="Output directory (default: exports/gear/rom/<sub>/<file>/).")
@click.option("--fbx/--no-fbx", default=True, show_default=True,
              help="Also convert to .fbx via Blender (requires Blender on PATH).")
@click.option("--part", type=int, default=None,
              help="Export only this mesh section index (0-based). Default: all parts merged.")
@click.option("--list-parts", is_flag=True, default=False,
              help="Print all mesh sections (index, name, size) and exit without exporting.")
@click.option("--mesh-merge-dp", type=int, default=2, show_default=True,
              help="Decimal places for vertex deduplication threshold (2 = 0.01 unit tolerance).")
@click.option("--no-base", "no_base", is_flag=True, default=False,
              help="Ignore any .base pristine backup and export from the live DAT instead.")
@click.option("--weld/--no-weld", default=True, show_default=True,
              help="Weld vertices by world position + UV across all mesh sections. Produces a fully joined mesh like Noesis. Use --no-weld to preserve per-section splitting.")
@click.option("--split-tex", is_flag=True, default=False,
              help="Unmirror the skin: double each texture into a stacked 2-up atlas (e.g. 256x256 -> "
                   "256x512, top = non-mirror side, bottom = mirror side) and remap the UVs so each "
                   "mirror half samples its own copy. One texture, no overlapping UVs, so you can "
                   "repaint each side independently.")
def cmd(race, slot, model_id, output, fbx, part, list_parts, mesh_merge_dp, no_base, weld, split_tex):
    """Export a gear model (all body parts merged by default) to GLB/FBX.

    Many gear DATs contain multiple mesh sections representing body parts that the
    game shows or hides depending on equipment. By default all parts are merged into
    one GLB. Use --part N to export a single section, or --list-parts to inspect.

    Identify the model explicitly or by DAT path — race is auto-detected when you
    pass a DAT path or file_id alone:

    \b
    RACE      one of: HumeMale HumeFemale ElvaanMale ElvaanFemale TaruMale
                      TaruFemale Mithra Galka
    SLOT      one of: face head body hands legs feet main sub ranged
    MODEL_ID  integer model index within that race/slot

    \b
    DAT       a DAT path — ROM/33/17, ROM/33/17.DAT, or a full path
    FILE_ID   a raw gear file_id, e.g. 10578

    Examples:

    \b
      xi gear export HumeMale body 0
      xi gear export Mithra hands 5 --no-fbx
      xi gear export ROM/33/17            # auto-detects HumeFemale / body / 34
      xi gear export ROM/33/17 --list-parts
      xi gear export ROM/33/17 --part 2   # export only the third mesh section
    """
    try:
        dat_path, race, slot, model_id = resolve_gear_target(race, slot, model_id)
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e))

    click.echo(f"Resolved: {race} / {slot} / model_id {model_id}  ({dat_path.name})")

    if list_parts:
        from xi.entity.mesh.xi_export import list_mesh_parts
        parts = list_mesh_parts(dat_path)
        if not parts:
            raise click.ClickException("No skeleton mesh sections found in DAT")
        click.echo(f"{len(parts)} mesh section(s):")
        for p in parts:
            click.echo(f"  [{p['index']}] name={p['name']!r:12s}  size={p['size']} bytes")
        return

    out = output or default_gear_output_dir(dat_path)
    all_parts_flag = part is None
    lod = part if part is not None else 0
    try:
        paths = export_dat(dat_path, out, fbx=fbx, skeleton_dat=race_skeleton_dat(race),
                           extra_metadata=gear_metadata(race, slot, model_id),
                           all_parts=all_parts_flag, lod=lod, mesh_merge_dp=mesh_merge_dp,
                           use_base=not no_base, weld=weld, split_tex=split_tex,
                           write_schema=SCHEMA_GENERATION)
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e))
    for p in paths:
        click.echo(f"Exported: {p}")

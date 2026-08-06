from __future__ import annotations

import json
from pathlib import Path

import click


def _write_json(payload, output: Path | None = None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        click.echo(f"Wrote {output}")
        return
    click.echo(text)


def _romish(path: Path) -> str:
    from xi.xi_config import FFXI_DIR

    try:
        return path.resolve().relative_to(Path(FFXI_DIR).resolve()).as_posix()
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# model: former entity registry/id surface
# ---------------------------------------------------------------------------


def model_entries() -> list[dict]:
    from xi.entity.xi_core import MAX_3500_MODELID, RANGES, modelid_blob
    from xi.ftable.xi_core import load_all_tables, scan_file_ids

    tables = load_all_tables()
    fid_to_mid: dict[int, int] = {}
    for mid_start, mid_end, offset in RANGES:
        end = mid_end if mid_end is not None else MAX_3500_MODELID
        for model_id in range(mid_start, end + 1):
            fid_to_mid[model_id + offset] = model_id

    entries = []
    for entry in scan_file_ids(fid_to_mid.keys(), tables):
        model_id = fid_to_mid[entry["file_id"]]
        entries.append({
            "type": "model",
            "target": f"model:{model_id}",
            "file_id": entry["file_id"],
            "model_id": model_id,
            "model_id_text": modelid_blob(model_id),
            "rom": entry["rom"],
            "dat": entry["dat"],
        })
    return entries


def _filter_model_entries(entries: list[dict], query: str | None) -> list[dict]:
    if not query:
        return entries
    q = query.lower()
    return [
        e for e in entries
        if q in str(e["model_id"]).lower()
        or q in str(e["file_id"]).lower()
        or q in str(e["dat"]).lower()
    ]


@click.command("json")
@click.argument("query", required=False)
@click.option("--free", is_flag=True, help="Show the next free custom model id and occupied custom slots.")
@click.option("--output", "output", type=click.Path(path_type=Path), default=None)
def model_json_cmd(query: str | None, free: bool, output: Path | None):
    """Emit registered model/id information as JSON."""
    if free:
        from xi.entity.xi_recommend import next_free_and_occupied
        from xi.entity.xi_core import MODEL_FILE_OFFSET, MODEL_SAFE_END, MODEL_SAFE_START, modelid_blob

        next_free, occupied = next_free_and_occupied()
        payload = {
            "range": {"start": MODEL_SAFE_START, "end": MODEL_SAFE_END},
            "next_model_id": next_free,
            "next_file_id": next_free + MODEL_FILE_OFFSET if next_free is not None else None,
            "next_model_id_text": modelid_blob(next_free) if next_free is not None else None,
            "occupied": [
                {"model_id": m, "file_id": fid, "rom": rom, "dat": dat}
                for m, fid, rom, dat in occupied
            ],
        }
    else:
        payload = _filter_model_entries(model_entries(), query)
    _write_json(payload, output)


@click.command("search")
@click.argument("query")
@click.option("--output", "output", type=click.Path(path_type=Path), default=None)
def model_search_cmd(query: str, output: Path | None):
    """Search registered model ids, file ids, and DAT paths."""
    _write_json(_filter_model_entries(model_entries(), query), output)


# ---------------------------------------------------------------------------
# mesh / anim
# ---------------------------------------------------------------------------


@click.command("json")
@click.argument("dat_path")
@click.option("--output", "output", type=click.Path(path_type=Path), default=None)
def mesh_json_cmd(dat_path: str, output: Path | None):
    """Emit mesh section information for a DAT as JSON."""
    from xi.entity.mesh.xi_export import list_mesh_parts, resolve_dat_path

    try:
        dat = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e)) from e
    payload = {"dat": _romish(dat), "parts": list_mesh_parts(dat)}
    _write_json(payload, output)


@click.command("json")
@click.argument("dat_path", required=False, default=None)
@click.option("--output-dir", type=click.Path(path_type=Path), default=None)
@click.option("--compact", is_flag=True, help="Group tracks by DAT.")
def anim_json_cmd(dat_path: str | None, output_dir: Path | None, compact: bool):
    """Emit animation track information as JSON."""
    from xi.entity.anim import xi_export as anim_export

    anim_export.list_cmd.callback(dat_path, True, output_dir, compact)


# ---------------------------------------------------------------------------
# zone
# ---------------------------------------------------------------------------


def _zone_tree_payload(dat: Path) -> dict:
    from xi.entity.mesh.xi_export import parse_sections
    from xi.xi_config import read_path_for

    data = bytearray(read_path_for(dat).read_bytes())
    sections = []
    for idx, section in enumerate(parse_sections(data)):
        raw_name = bytes(data[section.start:section.start + 4])
        name = "".join(chr(b) if 32 <= b < 127 else "." for b in raw_name)
        sections.append({
            "index": idx,
            "name": name,
            "type": section.type_code,
            "type_hex": f"0x{section.type_code:02X}",
            "offset": section.start,
            "data_offset": section.data_start,
            "size": section.size,
        })
    return {"dat": _romish(dat), "count": len(sections), "sections": sections}


@click.command("json")
@click.argument("dat_path", required=False)
@click.option("--tree", is_flag=True, help="Emit the raw section tree for DAT_PATH.")
@click.option("--fx", "with_fx", is_flag=True, help="Emit zone visual effects for DAT_PATH.")
@click.option("--objects", is_flag=True, help="Emit object/placement records for DAT_PATH.")
@click.option("--rooms", is_flag=True, help="When DAT_PATH is omitted, include unnamed room DATs.")
@click.option("--search", "query", default=None, help="When DAT_PATH is omitted, filter zones by name.")
@click.option("--output", "output", type=click.Path(path_type=Path), default=None)
def zone_json_cmd(dat_path: str | None, tree: bool, with_fx: bool, objects: bool,
                  rooms: bool, query: str | None, output: Path | None):
    """Emit zone listings or DAT inspection data as JSON."""
    if dat_path is None:
        from xi.zone.xi_list import get_zone_entries

        zones = get_zone_entries(path_prefix="game/", include_rooms=rooms)
        if query:
            q = query.lower()
            zones = [z for z in zones if q in z["name"].lower()]
        _write_json(zones, output)
        return

    from xi.zone.xi_export import default_output_dir, export_zone_json, resolve_dat_path

    try:
        dat = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e)) from e

    if tree:
        payload = _zone_tree_payload(dat)
    elif with_fx:
        from xi.fx.xi_dump import dump_effects
        payload = dump_effects(dat)
    elif objects:
        from xi.zone.xi_objects import DEFAULT_OBJECT_FOOTPRINT, build_payload, list_objects
        entries = list_objects(dat, object_max_footprint=DEFAULT_OBJECT_FOOTPRINT)
        payload = build_payload(dat, entries, DEFAULT_OBJECT_FOOTPRINT)
    else:
        out_dir = output.parent if output else default_output_dir(dat)
        out_path = export_zone_json(dat, out_dir)
        payload = json.loads(out_path.read_text(encoding="utf-8"))

    _write_json(payload, output)


@click.command("search")
@click.argument("query")
@click.option("--rooms", is_flag=True, help="Include unnamed room DATs.")
@click.option("--output", "output", type=click.Path(path_type=Path), default=None)
def zone_search_cmd(query: str, rooms: bool, output: Path | None):
    """Search zones by name and return reusable DAT targets."""
    from xi.zone.xi_list import get_zone_entries

    q = query.lower()
    zones = [z for z in get_zone_entries(path_prefix="game/", include_rooms=rooms)
             if q in z["name"].lower()]
    _write_json(zones, output)


@click.command("json")
@click.argument("dat_path")
@click.option("--filter", "name_filter", default=None)
@click.option("--max-footprint", type=float, default=None)
@click.option("--objects-only", is_flag=True)
@click.option("--output", "output", type=click.Path(path_type=Path), default=None)
def object_json_cmd(dat_path: str, name_filter: str | None, max_footprint: float | None,
                    objects_only: bool, output: Path | None):
    """Emit zone object/placement records as JSON."""
    from xi.entity.mesh.xi_export import resolve_dat_path
    from xi.zone.xi_objects import DEFAULT_OBJECT_FOOTPRINT, build_payload, list_objects

    try:
        dat = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e)) from e
    footprint = DEFAULT_OBJECT_FOOTPRINT if max_footprint is None else max_footprint
    entries = list_objects(dat, object_max_footprint=footprint)
    if name_filter:
        q = name_filter.lower()
        entries = [e for e in entries if q in e["name"].lower()]
    if objects_only:
        entries = [e for e in entries if "object" in e.get("tags", [])]
    _write_json(build_payload(dat, entries, footprint), output)


# ---------------------------------------------------------------------------
# gear / mount
# ---------------------------------------------------------------------------


def gear_entries(race: str | None = None, slot: str | None = None) -> list[dict]:
    from xi.ftable.xi_core import load_all_tables, scan_file_ids
    from xi.gear.xi_core import RACE_TABLES, SLOTS, match_race, parse_race_table, slot_file_ids

    if race:
        race_key = match_race(race)
        if race_key is None:
            raise click.ClickException(f"Unknown race '{race}'. Valid: {', '.join(RACE_TABLES)}")
        races = [race_key]
    else:
        races = list(RACE_TABLES)

    if slot:
        slot = slot.lower()
        if slot not in SLOTS:
            raise click.ClickException(f"Unknown slot '{slot}'. Valid: {', '.join(SLOTS)}")
        slots = [slot]
    else:
        slots = SLOTS

    tuples = []
    file_ids = set()
    for race_key in races:
        race_table = parse_race_table(RACE_TABLES[race_key])
        for slot_key in slots:
            for model_id, file_id in slot_file_ids(race_table[slot_key]):
                tuples.append((race_key, slot_key, model_id, file_id))
                file_ids.add(file_id)

    tables = load_all_tables()
    datmap = {e["file_id"]: e for e in scan_file_ids(sorted(file_ids), tables)}
    rows = []
    for race_key, slot_key, model_id, file_id in tuples:
        hit = datmap.get(file_id)
        if not hit:
            continue
        rows.append({
            "type": "gear",
            "target": f"gear:{race_key}:{slot_key}:{model_id}",
            "race": race_key,
            "slot": slot_key,
            "model_id": model_id,
            "file_id": file_id,
            "rom": hit["rom"],
            "dat": hit["dat"],
        })
    return rows


def _filter_rows(rows: list[dict], query: str | None) -> list[dict]:
    if not query:
        return rows
    q = query.lower()
    return [r for r in rows if any(q in str(v).lower() for v in r.values())]


@click.command("json")
@click.argument("race", required=False)
@click.argument("slot", required=False)
@click.option("--search", "query", default=None)
@click.option("--output", "output", type=click.Path(path_type=Path), default=None)
def gear_json_cmd(race: str | None, slot: str | None, query: str | None, output: Path | None):
    """Emit gear registry data as JSON."""
    _write_json(_filter_rows(gear_entries(race, slot), query), output)


@click.command("search")
@click.argument("query")
@click.option("--race", default=None)
@click.option("--slot", default=None)
@click.option("--output", "output", type=click.Path(path_type=Path), default=None)
def gear_search_cmd(query: str, race: str | None, slot: str | None, output: Path | None):
    """Search gear race/slot/model/file/DAT entries."""
    _write_json(_filter_rows(gear_entries(race, slot), query), output)


def mount_entries(show_all: bool = False) -> list[dict]:
    from xi.mount import xi_core as M

    high = 256 if show_all else M.MENU_CAP
    cache: dict = {}
    return [M.read_record(mount_id, cache=cache) for mount_id in range(high)]


@click.command("json")
@click.argument("mount_id", type=int, required=False)
@click.option("--all", "show_all", is_flag=True, help="Include ids 0-255 instead of menu-visible ids.")
@click.option("--occupied", is_flag=True)
@click.option("--free", is_flag=True)
@click.option("--output", "output", type=click.Path(path_type=Path), default=None)
def mount_json_cmd(mount_id: int | None, show_all: bool, occupied: bool, free: bool, output: Path | None):
    """Emit mount records as JSON."""
    from xi.mount import xi_core as M

    if mount_id is not None:
        payload = M.read_record(mount_id)
    else:
        rows = mount_entries(show_all)
        if occupied:
            rows = [r for r in rows if r["occupied"]]
        if free:
            rows = [r for r in rows if not r["occupied"]]
        payload = rows
    _write_json(payload, output)


@click.command("search")
@click.argument("query")
@click.option("--all", "show_all", is_flag=True)
@click.option("--output", "output", type=click.Path(path_type=Path), default=None)
def mount_search_cmd(query: str, show_all: bool, output: Path | None):
    """Search mounts by name, key item text, id, or DAT path."""
    _write_json(_filter_rows(mount_entries(show_all), query), output)


# ---------------------------------------------------------------------------
# audio / tex / fx / ftable
# ---------------------------------------------------------------------------


def _audio_kind(kind_name: str):
    from xi.audio import xi_core as core

    if kind_name not in ("music", "sfx"):
        raise click.ClickException("--type must be music or sfx")
    return core.KINDS[kind_name]


def _audio_entries(kind_name: str, patterns, root: str | None, limit: int | None = None) -> list[dict]:
    from xi.audio import xi_core as core
    from xi.audio import xi_names as names
    from xi.xi_config import FFXI_DIR

    kinds = [core.MUSIC, core.SFX] if kind_name == "all" else [_audio_kind(kind_name)]
    roots = (root,) if root else core.SOUND_ROOTS
    rows = []
    for kind in kinds:
        entries = core.list_entries(kind, Path(FFXI_DIR), roots, patterns)
        for entry in entries:
            row = {
                "type": kind.name,
                "root": entry.root,
                "file": entry.path.name,
                "path": str(entry.path),
                "relative": entry.rel.as_posix(),
            }
            try:
                header = core.parse_header_file(entry.path)
                row.update({
                    "id": header.id,
                    "format": header.format_name,
                    "channels": header.channels,
                    "sample_rate": header.sample_rate,
                    "looped": header.looped,
                    # ATRAC3 block fields don't map to a frame count — duration is
                    # only meaningful for natively-decodable formats (matches
                    # xi_catalog / `music list`, which print None / "?" there).
                    "duration_sec": (round(header.duration_sec, 3)
                                     if header.sample_format in (core.FMT_ADPCM, core.FMT_PCM)
                                     else None),
                    "label": names.music_name(header.id) if kind is core.MUSIC
                    else (names.sfx_name(header.id) or names.sfx_category(header.id)),
                })
            except core.AudioError as exc:
                row["error"] = str(exc)
            rows.append(row)
            if limit and len(rows) >= limit:
                return rows
    return rows


@click.command("json")
@click.argument("names", nargs=-1)
@click.option("--type", "kind_name", type=click.Choice(["music", "sfx", "all"]), default="all", show_default=True)
@click.option("--root", default=None)
@click.option("--limit", type=int, default=None)
@click.option("--output", "output", type=click.Path(path_type=Path), default=None)
def audio_json_cmd(names, kind_name: str, root: str | None, limit: int | None, output: Path | None):
    """Emit audio catalog entries as JSON."""
    _write_json(_audio_entries(kind_name, names, root, limit), output)


@click.command("search")
@click.argument("names", nargs=-1)
@click.option("--type", "kind_name", type=click.Choice(["music", "sfx", "all"]), default="all", show_default=True)
@click.option("--root", default=None)
@click.option("--limit", type=int, default=200, show_default=True)
@click.option("--output", "output", type=click.Path(path_type=Path), default=None)
def audio_search_cmd(names, kind_name: str, root: str | None, limit: int | None, output: Path | None):
    """Search music and sound effects."""
    _write_json(_audio_entries(kind_name, names, root, limit), output)


@click.command("export")
@click.argument("names", nargs=-1)
@click.option("--type", "kind_name", type=click.Choice(["music", "sfx"]), required=True)
@click.option("--out", "out_dir", type=click.Path(), default=None)
@click.option("--root", default=None)
@click.option("--limit", type=int, default=None)
@click.option("--loops/--no-loops", default=True)
@click.option("--vgmstream", "vgmstream_opt", type=click.Path(), default=None)
@click.option("--native-only", is_flag=True)
@click.option("--numbered", is_flag=True, help="Mirror the source tree (IDs) instead of names.")
def audio_export_cmd(names, kind_name: str, out_dir, root, limit, loops, vgmstream_opt, native_only, numbered):
    """Decode audio with --type music or --type sfx."""
    from xi.audio.xi_commands import run_export

    run_export(_audio_kind(kind_name), names, out_dir, root, limit, loops, vgmstream_opt, native_only, numbered=numbered)


@click.command("json")
@click.argument("dat_path")
@click.option("--output", "output", type=click.Path(path_type=Path), default=None)
def tex_json_cmd(dat_path: str, output: Path | None):
    """Emit DAT texture sections as JSON."""
    from xi.tex.xi_core import resolve_dat_path
    from xi.tex.xi_list import list_textures

    try:
        dat = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e)) from e
    _write_json({"dat": _romish(dat), "textures": list_textures(dat)}, output)


@click.command("json")
@click.argument("dat_path")
@click.option("--opcodes", is_flag=True)
@click.option("--output", "output", type=click.Path(path_type=Path), default=None)
def fx_json_cmd(dat_path: str, opcodes: bool, output: Path | None):
    """Emit visual effects as JSON."""
    from xi.fx.xi_core import resolve_dat_path
    from xi.fx.xi_dump import dump_effects

    try:
        dat = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e)) from e
    _write_json(dump_effects(dat, include_opcodes=opcodes), output)


@click.command("json")
@click.option("--rom", "rom_filter", type=int, default=None,
              help="Restrict to a single ROM table (1=base, 2-9=expansions). Default: all.")
@click.option("--header", "header_filter", default=None, metavar="AAAA",
              help="Filter by the DAT's 4-byte file header, e.g. --header lobb. "
                   "Forces a header read per file.")
@click.option("--range", "id_range", default=None, metavar="MIN-MAX",
              help="Restrict to a file_id range, e.g. --range 0-200.")
@click.option("--tables", is_flag=True, help="Emit table-size summary instead of entries.")
@click.option("--models", "by_models", is_flag=True,
              help="Emit model_id -> {file_id, dat} grouped by category (gear/mounts/npcs) instead of by file_id.")
@click.option("--header-read", "header_read", is_flag=True,
              help="Also read each DAT's file header (slower: opens every matching "
                   "DAT on disk). Implied by --header.")
@click.option("--progress", is_flag=True, help="Print scan progress to stderr (useful for unfiltered scans).")
@click.option("--flat/--full", default=True, show_default=True,
              help='--flat writes {"file_id": "dat"}; --full writes the entry array (rom, header, etc).')
def ftable_json_cmd(rom_filter: int | None, header_filter: str | None, id_range: str | None,
                    tables: bool, by_models: bool, header_read: bool, progress: bool, flat: bool):
    """Emit FTABLE/VTABLE data as JSON to exports/ftable/."""
    out_dir = Path("exports") / "ftable"

    if tables:
        from xi.ftable import xi_tables
        payload = [info for i in range(1, xi_tables._MAX_ROM + 1) if (info := xi_tables._table_info(i))]
        _write_json(payload, out_dir / "ftable_tables.json")
        return

    if by_models:
        _write_json(_models_payload(), out_dir / "models.json")
        return

    from xi.ftable.xi_list import _iter_entries

    id_min = id_max = None
    if id_range:
        parts = id_range.split("-")
        if len(parts) != 2:
            raise click.ClickException("--range must be MIN-MAX, e.g. 0-200")
        id_min, id_max = int(parts[0]), int(parts[1])
    payload = list(_iter_entries(rom_filter=rom_filter, header_filter=header_filter,
                                 id_min=id_min, id_max=id_max, progress=progress,
                                 read_header=header_read))
    if flat:
        payload = {str(entry["file_id"]): entry["dat"] for entry in payload}

    suffix = f"_header_{header_filter}" if header_filter else ""
    suffix += f"_rom{rom_filter}" if rom_filter else ""
    suffix += f"_range{id_range}" if id_range else ""
    suffix += "" if flat else "_full"
    _write_json(payload, out_dir / f"ftable{suffix}.json")


def _models_payload() -> dict:
    """Registered model_id -> {file_id, dat} for entity (npcs), gear, and mounts."""
    import time
    from xi.entity.xi_list import get_all_entries as _entity_entries
    from xi.gear.xi_list import get_all_entries as _gear_entries
    from xi.gear.xi_core import SLOTS
    from xi.gear.xi_inject import RACES, custom_fid, current_expand_max
    from xi.ftable.xi_core import load_all_tables, scan_file_ids
    from xi.mount import xi_core as M
    from xi.xi_config import FFXI_DIR

    def _phase(label: str) -> float:
        click.echo(f"[{label}]", err=True)
        return time.perf_counter()

    def _done(t0: float, msg: str) -> None:
        click.echo(f"  {msg} ({time.perf_counter() - t0:.2f}s)", err=True)

    # ── Load FTABLE/VTABLE once, reuse across every scan ────────────────
    # Note: `load_all_tables` reads via read_path_for() against the base
    # install (edits live in place), so this reflects the tables that are
    # "live" for the client — no separate pass over each root.
    t0 = _phase(f"Loading FTABLE/VTABLE (source: {FFXI_DIR})")
    tables = load_all_tables()
    for rom_idx, (fdata, _) in sorted(tables.items()):
        click.echo(f"  ROM{rom_idx}: {len(fdata)//2:,} entries", err=True)
    _done(t0, f"loaded {len(tables)} ROM table pair(s)")

    # ── npcs (entity model_ids across the 4 retail ranges) ──────────────
    t0 = _phase("Scanning entity/NPC model_ids")
    entity_entries = _entity_entries(tables=tables, quiet=True)
    npcs = {
        str(e["model_id"]): {"file_id": e["file_id"], "dat": e["dat"]}
        for e in entity_entries
    }
    _done(t0, f"{len(npcs):,} registered entity model_ids")

    # ── gear (retail hardcoded RACE_TABLES groups) ──────────────────────
    t0 = _phase("Scanning retail gear (per-race group tables)")
    gear: dict = {}
    total_retail = 0
    for race, items in _gear_entries(tables=tables, quiet=True).items():
        for e in items:
            gear.setdefault(race, {}).setdefault(e["slot"], {})[str(e["model_id"])] = {
                "file_id": e["file_id"], "dat": e["dat"],
            }
            total_retail += 1
    _done(t0, f"{total_retail:,} retail gear entries across {len(gear)} races")

    # ── gear (custom range: xi gear inject / DLL patched G5) ──────────
    # RACE_TABLES is static / retail-only; injected custom gear lives in
    # CUSTOM_GEAR_BASE + (race,slot)*window + model_id, invisible to that
    # lookup. Walk the whole custom window per (race, slot) and check
    # which file_ids resolve.
    max_model = current_expand_max()
    if max_model is not None:
        t0 = _phase(f"Scanning custom gear window (max_model={max_model} per race/slot)")
        candidates = {}
        for race in RACES:
            for slot in SLOTS:
                for model_id in range(max_model + 1):
                    candidates[custom_fid(race, slot, model_id, max_model)] = (race, slot, model_id)
        click.echo(f"  probing {len(candidates):,} candidate file_ids...", err=True)
        added = 0
        for e in scan_file_ids(candidates.keys(), tables):
            race, slot, model_id = candidates[e["file_id"]]
            slot_map = gear.setdefault(race, {}).setdefault(slot, {})
            if str(model_id) not in slot_map:
                slot_map[str(model_id)] = {"file_id": e["file_id"], "dat": e["dat"]}
                added += 1
        _done(t0, f"{added:,} new custom gear entries (retail entries preserved)")
    else:
        click.echo("[Skipping custom gear scan: `xi ftable expand gear` not run yet]", err=True)

    # ── mounts (fixed 0x019131 + mount_id lookup) ───────────────────────
    # M.rom_path_for_id() reloads all tables per call — 256 mount_ids = 2560
    # table reads = ~8s. Batch it against our already-loaded `tables` via
    # scan_file_ids instead (matches how gear/npcs above resolve).
    t0 = _phase("Scanning mount ids 0-255")
    mount_fids = {M.file_id_for(mid): mid for mid in range(256)}
    mounts = {}
    for e in scan_file_ids(mount_fids.keys(), tables):
        mounts[str(mount_fids[e["file_id"]])] = {"file_id": e["file_id"], "dat": e["dat"]}
    _done(t0, f"{len(mounts):,} registered mounts")

    click.echo("[Building payload]", err=True)
    return {"gear": gear, "mounts": mounts, "npcs": npcs}

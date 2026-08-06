"""`xi event` commands — parse and dump FFXI event DATs."""

import json
import sys
from pathlib import Path

import click

from xi.event import xi_event as core
from xi.xi_config import FFXI_DIR, read_path_for, editable_dat, output_path_for


# ---------------------------------------------------------------------------
# DAT resolution helpers
# ---------------------------------------------------------------------------

def _file_candidates(dat: str):
    specs = [dat] + ([] if dat.lower().endswith(".dat") else [dat + ".DAT"])
    for s in specs:
        yield Path(s)
        yield Path(FFXI_DIR) / s


def _zone_event_path(zone_id: int):
    from xi.zone.xi_inject import zone_event_file_id
    from xi.ftable.xi_core import scan_file_ids
    hits = scan_file_ids([zone_event_file_id(zone_id)])
    if not hits:
        return None, None
    return Path(FFXI_DIR) / hits[0]["dat"], hits[0]["dat"]


def _zone_model_path(zone_id: int):
    from xi.zone.xi_list import zone_file_id
    from xi.ftable.xi_core import scan_file_ids
    hits = scan_file_ids([zone_file_id(zone_id)])
    return hits[0]["dat"] if hits else None


def _zone_table():
    from xi.zone.xi_list import get_zone_entries
    return get_zone_entries(path_prefix="")


def _resolve_event_dat(dat: str) -> tuple:
    """Returns (abs_path, rom_relative, zone_id_or_None, zone_name_or_None)."""
    for cand in _file_candidates(dat):
        if cand.is_file():
            return cand, _rom_rel_str(cand), None, None

    if dat.strip().isdigit():
        zid = int(dat)
        name = next((z["name"] for z in _zone_table() if z["id"] == zid), "?")
        path, rel = _zone_event_path(zid)
        if not path or not path.is_file():
            raise click.ClickException(f"No event DAT found for zone {zid} ({name}).")
        return path, rel, zid, name

    zones = _zone_table()
    q = dat.strip().lower()
    matches = ([z for z in zones if z["name"].lower() == q]
               or [z for z in zones if q in z["name"].lower()])
    if not matches:
        raise click.ClickException(
            f"No DAT or zone matches {dat!r}. "
            f"Give an event DAT path, a zone id, or a zone name.")
    if len(matches) > 1:
        listing = "\n".join(f"  {z['id']:>4}  {z['name']}" for z in matches[:12])
        raise click.ClickException(
            f"{dat!r} matches {len(matches)} zones — narrow it or use the id:\n{listing}")
    z = matches[0]
    path, rel = _zone_event_path(z["id"])
    if not path or not path.is_file():
        raise click.ClickException(f"No event DAT found for zone {z['id']} ({z['name']}).")
    return path, rel, z["id"], z["name"]


def _rom_rel_str(path: Path) -> str:
    parts = list(path.parts)
    for i, p in enumerate(parts):
        if p.upper() == "ROM" and i + 1 < len(parts):
            return "/".join(["ROM", *parts[i + 1:]])
    return path.name


def _npc_names_for_zone(zone_id) -> dict:
    """``{serverId: name}`` from a zone's NPC (entity) DAT, so opcode dumps can label the
    NPCs they act on. ``{}`` when there's no zone id / NPC DAT (export still works)."""
    if zone_id is None:
        return {}
    try:
        from xi.zone.xi_inject import zone_npc_file_id
        from xi.ftable.xi_core import scan_file_ids
        hits = scan_file_ids([zone_npc_file_id(int(zone_id))])
        if not hits:
            return {}
        npc_path = read_path_for(Path(FFXI_DIR) / hits[0]["dat"])
        if not npc_path.exists():
            return {}
        return core.parse_entity_names(npc_path.read_bytes())
    except Exception:
        return {}


def _load(dat: str):
    path, event_rel, zone_id, zone_name = _resolve_event_dat(dat)
    read = read_path_for(path)
    data = read.read_bytes()
    try:
        actors = core.parse_event_dat(data)
    except core.EventDatError as e:
        raise click.ClickException(
            f"{read}: {e}\n  (expected a per-zone event DAT, not the model/dialog/npc DAT)")
    names = _npc_names_for_zone(zone_id)
    return path, event_rel, zone_id, zone_name, actors, names


# ---------------------------------------------------------------------------
# Output path helpers
# ---------------------------------------------------------------------------

def _export_dir(event_rel: str, override: str = None) -> Path:
    """exports/event/cutscene/rom/21/39/  from ROM/21/39.DAT"""
    if override:
        return Path(override)
    parts = event_rel.replace("\\", "/").split("/")
    low = [p.lower() for p in parts]
    if low:
        low[-1] = low[-1].removesuffix(".dat")
    return Path("exports") / "event" / "cutscene" / Path(*low)


def _ensure_metadata(export_dir: Path, event_rel: str, zone_id, zone_name, actors):
    meta_path = export_dir / "metadata.json"
    if meta_path.exists():
        return

    total_events = sum(len(a.events) for a in actors)
    cutscene_count = sum(sum(1 for e in a.events if e.is_cutscene) for a in actors)

    meta = {
        "event_dat": event_rel,
        "stats": {
            "actor_count": len(actors),
            "event_count": total_events,
            "cutscene_count": cutscene_count,
        },
    }
    if zone_id is not None:
        meta["zone_id"] = zone_id
        if zone_name:
            meta["zone_name"] = zone_name
        model_rel = _zone_model_path(zone_id)
        if model_rel:
            meta["model_dat"] = model_rel

    export_dir.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Text serialization
# ---------------------------------------------------------------------------

def _actors_to_txt(event_rel: str, zone_id, zone_name,
                   actors, actor_dicts: list, cutscene_only: bool) -> str:
    lines = []
    if zone_name:
        lines.append(f"Zone: {zone_name} ({zone_id})")
    lines.append(f"Event DAT: {event_rel}")
    total_ev  = sum(len(a.events) for a in actors)
    total_cs  = sum(sum(1 for e in a.events if e.is_cutscene) for a in actors)
    lines.append(f"Actors: {len(actors)}  Events: {total_ev}  Cutscenes: {total_cs}")
    if cutscene_only:
        lines.append("(cutscene events only)")
    lines.append("")

    for d in actor_dicts:
        lines.append("=" * 72)
        nm = f"  {d['actor_name']}" if d.get("actor_name") else ""
        lines.append(f"Actor 0x{d['actor_id_int']:08X}{nm}")
        if d["refs_fourcc"]:
            lines.append(f"  Anim tags in refs[]: {', '.join(d['refs_fourcc'])}")
        lines.append(f"  refs[]: {' '.join(d['refs_hex'])}")
        lines.append("")

        for ev in d["events"]:
            cs = " [CUTSCENE]" if ev["is_cutscene"] else ""
            lines.append(f"  Event {ev['event_id']:>5}  @0x{ev['offset']:04x}"
                         f"  {ev['opcode_count']} opcodes{cs}")
            if ev["dialog_ids"]:
                lines.append(f"    dialog: {ev['dialog_ids']}")
            if ev["animation_tags"]:
                lines.append(f"    anim:   {ev['animation_tags']}")
            if "opcodes" in ev and ev["opcodes"]:
                for op in ev["opcodes"]:
                    ref = f"  → dialog {op['dialog_ref']}" if "dialog_ref" in op else ""
                    if "zone_ref" in op:                       # 0x34/0x35 load_zone target
                        zn = f" {op['zone_name']}" if op.get("zone_name") else ""
                        ref += f"  → zone {op['zone_ref']}{zn}"
                    if op.get("actors"):                       # NPC/entity the opcode acts on
                        ref += "  → " + ", ".join(x["label"] for x in op["actors"])
                    lines.append(f"    +{op['offset']:04x}  {op['name']:<18} {op['args']}{ref}")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# `xi event cutscene` — group
# ---------------------------------------------------------------------------

@click.group("cutscene")
def cutscene_group():
    """Cutscene export / import for zone event DATs."""
    pass


# ---------------------------------------------------------------------------
# `xi event cutscene export`
# ---------------------------------------------------------------------------

@cutscene_group.command("export")
@click.argument("dat")
@click.option("--json", "as_json", is_flag=True,
              help="Save as JSON instead of the default .txt disassembly.")
@click.option("-o", "--output", type=click.Path(dir_okay=True), default=None,
              help="Output directory (overrides default exports/event/cutscene/…).")
@click.option("--all-events", is_flag=True,
              help="Include all events, not just cutscene-flagged ones.")
@click.option("--no-opcodes", is_flag=True,
              help="Omit per-opcode disassembly lines (shorter output).")
@click.option("--actor", "actor_filter", type=lambda x: int(x, 0), default=None,
              help="Filter to a single actor id (hex OK: 0x010E6001).")
def export_cmd(dat, as_json, output, all_events, no_opcodes, actor_filter):
    """Export cutscene event data from a zone event DAT.

    \b
    DAT can be a file path, a zone id, or a zone name:
      xi event cutscene export 230            # → 39.txt  (human-readable)
      xi event cutscene export 230 --json     # → 39.json (structured)
      xi event cutscene export "Southern San d'Oria"
      xi event cutscene export ROM/21/39.DAT

    \b
    Output:   exports/event/cutscene/rom/21/39/39.txt   (or .json with --json)
    Metadata: exports/event/cutscene/rom/21/39/metadata.json  (created once)
    """
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    path, event_rel, zone_id, zone_name, actors, names = _load(dat)

    # Progress line
    if zone_name:
        click.echo(f"Parsing Zone: {zone_name} ({zone_id}) event data from {event_rel}")
    else:
        click.echo(f"Parsing event data from {event_rel}")

    cutscene_only = not all_events

    if actor_filter is not None:
        actors = [a for a in actors if a.actor_id == actor_filter]
        if not actors:
            raise click.ClickException(f"No actor with id 0x{actor_filter:08X} found.")

    actor_dicts = [
        core.actor_to_dict(a, cutscene_only=cutscene_only,
                           include_opcodes=not no_opcodes, names=names)
        for a in actors
    ]
    actor_dicts = [d for d in actor_dicts if d is not None]

    total_cs = sum(sum(1 for e in a.events if e.is_cutscene) for a in actors)
    total_ev = sum(len(a.events) for a in actors)

    stem = Path(event_rel.replace("\\", "/")).stem
    exp_dir = _export_dir(event_rel, output)
    exp_dir.mkdir(parents=True, exist_ok=True)

    if as_json:
        doc = {
            "event_dat": event_rel,
            "actor_count": len(actors),
            "event_count": total_ev,
            "cutscene_count": total_cs,
            "cutscene_only": cutscene_only,
            "actors": actor_dicts,
        }
        if zone_id is not None:
            doc["zone_id"] = zone_id
        if zone_name:
            doc["zone_name"] = zone_name
        out_file = exp_dir / f"{stem}.json"
        out_file.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        text = _actors_to_txt(event_rel, zone_id, zone_name,
                              actors, actor_dicts, cutscene_only)
        out_file = exp_dir / f"{stem}.txt"
        out_file.write_text(text, encoding="utf-8")

    _ensure_metadata(exp_dir, event_rel, zone_id, zone_name, actors)

    click.echo(f"Saved to: {out_file}")


# ---------------------------------------------------------------------------
# `xi event cutscene import`  (WIP placeholder)
# ---------------------------------------------------------------------------

@cutscene_group.command("import")
@click.argument("json_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--dry-run", is_flag=True, help="Show what would be written without writing.")
def import_cmd(json_file, dry_run):
    """Import an edited cutscene export JSON back into the event DAT.  [WIP]

    \b
    Not yet implemented — placeholder for the round-trip authoring workflow.
    """
    raise click.ClickException(
        "xi event cutscene import is not yet implemented.\n"
        "  Export a DAT first with `xi event cutscene export`, edit the JSON,\n"
        "  then re-run import once this command is ready.")


# ---------------------------------------------------------------------------
# `xi event cutscene compile` — JSON → bytecode
# ---------------------------------------------------------------------------

@cutscene_group.command("compile")
@click.argument("cutscene_json", type=click.Path(exists=True, dir_okay=False))
@click.option("--event-dat", "event_override", type=click.Path(exists=True, dir_okay=False),
              help="Event DAT to append to. Default: resolved from cutscene 'zone' or 'actor'.")
@click.option("--dialog-dat", "dialog_override", type=click.Path(exists=True, dir_okay=False),
              help="Dialog DAT to grow. Default: matches the event DAT's zone.")
@click.option("--dry-run", is_flag=True, help="Compile in-memory; print event id + Lua stub, don't write.")
def compile_cmd(cutscene_json, event_override, dialog_override, dry_run):
    """Compile a xi.cutscene.v1 JSON into event/dialog DATs.

    \b
    Reads a cutscene definition (see schema/event_cutscene.json), grows the zone's
    dialog table with the cutscene's lines, synthesizes the event bytecode, splices
    a new event onto the owning actor. Writes the rebuilt DATs in place under
    FFXI_DIR + a .base backup (same convention as `event dialogue new`).

    \b
    Camera / fade steps are supported (scene-DAT writer is shipped). Dialog-only
    cutscenes work too.

    \b
    Example:
      xi event cutscene compile my_cutscene.json --event-dat ROM/21/52.DAT
    """
    import json
    from xi.event import xi_compile

    with open(cutscene_json, "r", encoding="utf-8") as f:
        cutscene = json.load(f)

    if not event_override:
        raise click.ClickException(
            "--event-dat required for now (auto-resolution from cast entity is a TODO).")
    if not dialog_override:
        # Derive dialog DAT path from event DAT by swapping the ROM subdir (21→25).
        ep = Path(event_override).as_posix().upper()
        if "/21/" in ep:
            dialog_override = ep.replace("/21/", "/25/")
        else:
            raise click.ClickException("--dialog-dat required (couldn't auto-derive)")

    event_bytes = Path(event_override).read_bytes()
    dialog_bytes = Path(dialog_override).read_bytes()

    try:
        res = xi_compile.compile_cutscene(cutscene, event_bytes, dialog_bytes)
    except (xi_compile.CutsceneCompileError, NotImplementedError) as e:
        raise click.ClickException(str(e))

    click.echo(f"event_id = {res.event_id}")
    click.echo(f"event_dat: {len(event_bytes)} -> {len(res.event_dat)} bytes")
    click.echo(f"dialog_dat: {len(dialog_bytes)} -> {len(res.dialog_dat)} bytes")
    for w in res.warnings:
        click.echo(f"WARNING: {w}", err=True)
    click.echo()
    click.echo(res.lua_stub)

    if dry_run:
        click.echo("[dry-run] not writing DATs")
        return

    # Write to mirror + .base backup (matches `dialogue new` convention).
    from xi.xi_config import output_path_for
    event_out = output_path_for(event_override)
    dialog_out = output_path_for(dialog_override)
    Path(event_out).parent.mkdir(parents=True, exist_ok=True)
    Path(dialog_out).parent.mkdir(parents=True, exist_ok=True)
    for src, out, blob in [(event_override, event_out, res.event_dat),
                            (dialog_override, dialog_out, res.dialog_dat)]:
        base = Path(str(out) + ".base")
        if not base.exists():
            base.write_bytes(Path(src).read_bytes())
        Path(out).write_bytes(blob)
    click.echo(f"wrote {event_out}")
    click.echo(f"wrote {dialog_out}")


# ---------------------------------------------------------------------------
# `xi event dialogue` — author NPC dialogue events
# ---------------------------------------------------------------------------

def _resolve_zone_dialog_event(dat: str):
    """From a zone id/name or an event-DAT path, resolve the (event_src, dialog_src, zone_id,
    zone_name). Both ``_src`` are the *pristine* FFXI_DIR paths (edits go to the mirror)."""
    from xi.zone.xi_inject import zone_event_file_id, zone_dialog_file_id
    from xi.ftable.xi_core import scan_file_ids
    event_src, event_rel, zone_id, zone_name = _resolve_event_dat(dat)
    if zone_id is None:
        # A raw event-DAT path — reverse-resolve its zone by matching the event file id.
        rel_up = event_rel.upper()
        for z in _zone_table():
            try:
                hits = scan_file_ids([zone_event_file_id(z["id"])])
            except Exception:
                continue
            if hits and hits[0]["dat"].upper() == rel_up:
                zone_id, zone_name = z["id"], z["name"]
                break
        if zone_id is None:
            raise click.ClickException(
                "Couldn't map that DAT to a zone (needed to find its dialogue table).\n"
                "  Pass a zone id or name instead, e.g. `xi event dialogue new 245 …`.")
    dhits = scan_file_ids([zone_dialog_file_id(zone_id)])
    if not dhits:
        raise click.ClickException(f"No dialog DAT found for zone {zone_id} ({zone_name}).")
    return event_src, Path(FFXI_DIR) / dhits[0]["dat"], zone_id, zone_name


@click.group("dialogue")
def dialogue_group():
    """Work with NPC dialogue — the per-zone dialog string table and the events that show it.

    \b
    Edit the dialog string table:  export · search · info · edit · reset
    Author a new dialogue event:   actors · new
    Each takes a zone id, zone name, or DAT path and resolves the right DAT itself.
    """
    pass


@dialogue_group.command("actors")
@click.argument("dat")
@click.option("--limit", type=int, default=40, help="Max actors to list (0 = all).")
def dialogue_actors_cmd(dat, limit):
    """List a zone's actors (NPC ids) so you can pick one for `dialogue new --actor`.

    \b
      xi event dialogue actors 245            # Lower Jeuno: id, name, #events
    """
    path, event_rel, zone_id, zone_name, actors, names = _load(dat)
    if zone_name:
        click.echo(f"Zone: {zone_name} ({zone_id}) — {event_rel}")
    click.echo(f"{len(actors)} actor(s):")
    rows = sorted(actors, key=lambda a: -len(a.events))
    cap = limit if limit > 0 else len(rows)
    for a in rows[:cap]:
        nm = names.get(a.actor_id, "")
        click.echo(f"  0x{a.actor_id:08X}  {len(a.events):>3} event(s)  {nm}")
    if len(rows) > cap:
        click.echo(f"  … {len(rows) - cap} more (raise --limit)")


@dialogue_group.command("new")
@click.argument("dat")
@click.option("--json", "json_file", required=True, type=click.Path(exists=True, dir_okay=False),
              help='JSON file: an array of dialogue lines, e.g. ["line 1","line 2"].')
@click.option("--actor", "actor_id", required=True, type=lambda x: int(x, 0),
              help="Owning NPC's server entity id (hex 0x… or decimal). See `dialogue actors`.")
@click.option("--paged", is_flag=True,
              help="Show all lines in ONE box that pages (▼) instead of one box per line.")
@click.option("--event-id", type=int, default=None,
              help="Force a specific event id (default: next free id on the actor).")
@click.option("--dry-run", is_flag=True, help="Show what would be written without writing.")
def dialogue_new_cmd(dat, json_file, actor_id, paged, event_id, dry_run):
    """Inject dialogue lines + a new event that prints them, returning the event id.

    \b
    Lines support the same escapes as `dialog edit` (\\n newline, \\v prompt ▼,
    {player} {npc} {auto:N}). The lines are appended to the zone's dialog table and a new
    event ` print · wait · … · end ` is spliced onto --actor. Trigger it server-side with
    `player:startCutscene(<id>)` (a Lua stub is printed — use startCutscene so the
    player locks into CUTSCENE mode; startEvent alone does not).

    \b
      xi event dialogue new 245 --json lines.json --actor 0x010F5022
      xi event dialogue new 245 --json lines.json --actor 0x010F5022 --paged
    """
    from xi.event import xi_author

    # 1) Load the lines.
    raw = json.loads(Path(json_file).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("lines") or raw.get("dialogue") or raw.get("text")
    if not isinstance(raw, list) or not raw or not all(isinstance(s, str) for s in raw):
        raise click.ClickException(
            "--json must be a JSON array of strings (or {\"lines\": [...]}).")

    # 2) Resolve both DATs + the zone.
    event_src, dialog_src, zone_id, zone_name = _resolve_zone_dialog_event(dat)
    event_rel = _rom_rel_str(event_src)
    dialog_rel = _rom_rel_str(dialog_src)

    # 3) Append the lines to the dialog table (layering on any prior edits via the mirror).
    dialog_data = read_path_for(dialog_src).read_bytes()
    from xi.dialog import xi_dialog
    if not xi_dialog.looks_like_event_message(dialog_data):
        raise click.ClickException(f"{dialog_rel} is not an event-message dialog DAT.")
    new_dialog, msg_ids = xi_author.append_dialog_lines(dialog_data, raw, paged=paged)

    # 4) Splice the event onto the actor.
    event_data = read_path_for(event_src).read_bytes()
    actors = core.parse_raw_actors(event_data)
    names = _npc_names_for_zone(zone_id)
    actor_name = names.get(actor_id)
    try:
        new_event_id, created = xi_author.add_dialogue_event(actors, actor_id, msg_ids, event_id)
    except ValueError as e:
        raise click.ClickException(str(e))
    new_event = core.build_event_dat(actors)

    # 5) Report.
    if zone_name:
        click.echo(f"Zone: {zone_name} ({zone_id})")
    click.echo(f"Actor: 0x{actor_id:08X}{(' (' + actor_name + ')') if actor_name else ''}"
               f"{'  [new actor block created]' if created else ''}")
    click.echo(f"Lines: {len(raw)} → message id(s) {msg_ids[0]}"
               f"{'–' + str(msg_ids[-1]) if len(msg_ids) > 1 else ''}"
               f"  ({'paged ▼' if paged else 'separate boxes'})")
    click.echo(f"Event id: {new_event_id}")
    click.echo(f"  dialog DAT {dialog_rel}: {len(dialog_data)} → {len(new_dialog)} bytes")
    click.echo(f"  event  DAT {event_rel}: {len(event_data)} → {len(new_event)} bytes")

    if dry_run:
        click.echo("(dry run — nothing written)")
    else:
        editable_dat(dialog_src, fresh=False).write_bytes(new_dialog)
        editable_dat(event_src, fresh=False).write_bytes(new_event)
        click.echo(f"Wrote: {output_path_for(dialog_src)}")
        click.echo(f"Wrote: {output_path_for(event_src)}")

    click.echo("\n─ server trigger (paste into the NPC's Lua) " + "─" * 26)
    click.echo(xi_author.lua_stub(actor_id, new_event_id, actor_name))

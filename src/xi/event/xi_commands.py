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
        # A decompiled JSON carries the zone it came from; resolve both DATs from it.
        zone = cutscene.get("zone")
        if zone is None:
            raise click.ClickException("--event-dat required (the JSON has no 'zone' field to resolve it from).")
        from xi import xi_config
        from xi.event import xi_explain as X
        zf = X.zone_files(Path(xi_config.FFXI_DIR), [int(zone)])[0]
        event_override = str(zf.event)
        dialog_override = dialog_override or str(zf.dialog)
    elif not Path(event_override).is_file():
        event_override = str(_resolve_event_dat(event_override)[0])       # ROM-relative -> absolute
    if not dialog_override:
        # Derive dialog DAT path from event DAT by swapping the ROM subdir (21→25).
        ep = Path(event_override).as_posix()
        if "/21/" in ep.upper():
            dialog_override = ep[:ep.upper().index("/21/")] + "/25/" + ep[ep.upper().index("/21/") + 4:]
        else:
            raise click.ClickException("--dialog-dat required (couldn't auto-derive)")
    elif not Path(dialog_override).is_file():
        dialog_override = str(_resolve_event_dat(dialog_override)[0])

    event_bytes = Path(event_override).read_bytes()
    dialog_bytes = Path(dialog_override).read_bytes()

    try:
        from xi import xi_config
        res = xi_compile.compile_cutscene(cutscene, event_bytes, dialog_bytes,
                                          ffxi_dir=Path(xi_config.FFXI_DIR) if xi_config.FFXI_DIR else None)
    except (xi_compile.CutsceneCompileError, NotImplementedError) as e:
        raise click.ClickException(str(e))

    click.echo(f"event_id = {res.event_id}")
    click.echo(f"event_dat: {len(event_bytes)} -> {len(res.event_dat)} bytes")
    # pre-flight lint of the compiled event (sizes, jumps, selectors, message ids, menu markers)
    from xi.event import xi_lint, xi_compile as _xc
    owner = None
    for c in (cutscene.get("cast") or {}).get("cast") or []:
        if c.get("id") == cutscene.get("actor"):
            owner = _xc._resolve_entity(c["entity"])
    if owner is not None:
        for eid, lr in xi_lint.lint_dat(res.event_dat, res.dialog_dat, owner, res.event_id).items():
            for w in lr.warnings:
                click.echo(f"lint warning: {w}", err=True)
            if not lr.ok:
                for e in lr.errors:
                    click.echo(f"lint error: {e}", err=True)
                raise click.ClickException("lint failed; nothing written")
        click.echo("lint: OK")
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


# ---------------------------------------------------------------------------
# `xi event explain` / `xi event survey` — annotated decodes for any NPC
# ---------------------------------------------------------------------------

@click.command("decompile")
@click.argument("zone")
@click.argument("who")
@click.option("--event", "event_id", type=int, required=True, help="Event id to decompile.")
@click.option("-o", "--output", type=click.Path(dir_okay=False), help="Write the xi.cutscene.v1 JSON here.")
@click.option("--check", "check", is_flag=True, help="Recompile the JSON in bare mode and compare it with the retail event.")
@click.option("--installed", "installed", is_flag=True, help="Read the installed DATs instead of the pristine .base copies (for our own events).")
def decompile_cmd(zone, who, event_id, output, check, installed):
    """Retail event bytecode -> xi.cutscene.v1 JSON (steps, subs, dialog text inline).

    \b
      xi event decompile 252 0x010FC08F --event 9506 -o oseem_9506.json --check
      xi event decompile 243 "Nomad Moogle" --event 10196
    """
    import json as _json
    import sys
    from xi import xi_config
    from xi.event import xi_explain as X, xi_decompile as D
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    _, _, zone_id, zone_name = _resolve_event_dat(zone)
    if zone_id is None:
        raise click.ClickException("give a zone id or name")
    root = Path(xi_config.FFXI_DIR)
    zf = X.zone_files(root, [zone_id])[0]
    actors, blobs, names = X.load_zone(zf)
    q = who.strip()
    if q.lower().startswith("0x"):
        actor_id = int(q, 16)
    elif q.isdigit():
        actor_id = int(q)
    else:
        actor_id = next((aid for aid, nm in names.items() if nm.lower() == q.lower()), None)
        if actor_id is None:
            raise click.ClickException(f"no actor named {who!r} in {zone_name}")
    cs, ctx = D.decompile_event(root, zone_id, actor_id, event_id, installed=installed)
    text = _json.dumps(cs, indent=1, ensure_ascii=False)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
        click.echo(f"wrote {output}: {ctx.total_ops} opcodes, {ctx.raw_ops} raw ({100 * (ctx.total_ops - ctx.raw_ops) // max(1, ctx.total_ops)}% modelled), {len(cs['dialog']['lines'])} lines")
    else:
        click.echo(text)
    for n in ctx.notes:
        click.echo("  note: " + n)
    if check:
        r = D.check_roundtrip(root, zone_id, actor_id, event_id, cs)
        click.echo(f"round trip: retail {r['retail_ops']} ops, ours {r['ours_ops']} ops, {r['mismatches']} mismatch(es); compiled as event {r['compiled_event']}")
        for i, a, b in r["first_mismatches"]:
            click.echo(f"  #{i}: retail {a}\n       ours   {b}")


@click.command("explain")
@click.argument("zone")
@click.argument("who", required=False)
@click.option("--event", "event_id", type=int, help="Only this event id.")
@click.option("--list", "list_only", is_flag=True, help="List the zone's actors (id, name, events) and exit.")
@click.option("-o", "--output", type=click.Path(dir_okay=False), help="Write the listing to a file (UTF-8).")
def explain_cmd(zone, who, event_id, list_only, output):
    """Annotated disassembly of one NPC's events: every operand resolved, dialog text inline,
    conditions spelled out, 0x9D tables expanded, called subroutines decoded, feature summary.

    \b
      xi event explain 235 Isakoth
      xi event explain "Bastok Markets" 0x010EB0B1 --event 26
      xi event explain 243 --list
    """
    import sys
    from xi import xi_config
    from xi.event import xi_explain as X
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    _, _, zone_id, zone_name = _resolve_event_dat(zone)
    if zone_id is None:
        raise click.ClickException("give a zone id or name")
    zfs = X.zone_files(Path(xi_config.FFXI_DIR), [zone_id])
    if not zfs:
        raise click.ClickException(f"no event DAT for zone {zone_id}")
    zf = zfs[0]
    actors, blobs, names = X.load_zone(zf)
    if list_only or not who:
        rows = sorted(actors, key=lambda a: -len(a.event_ids))
        click.echo(f"{zone_name} ({zone_id}): {len(actors)} actors")
        for a in rows:
            real = [e for e in a.event_ids if e not in (0xFFFE, 0xFFFF)]
            click.echo(f"  0x{a.actor_id:08X}  {len(real):3d} event(s)  {names.get(a.actor_id, '')}")
        return
    q = who.strip()
    target = None
    if q.lower().startswith("0x"):
        target = next((a for a in actors if a.actor_id == int(q, 16)), None)
    elif q.isdigit():
        target = next((a for a in actors if a.actor_id == int(q)), None)
    else:
        ids = [sid for sid, n in names.items() if n.lower() == q.lower()] or \
              [sid for sid, n in names.items() if q.lower() in n.lower()]
        cands = [a for a in actors if a.actor_id in ids]
        if len(cands) > 1:
            listing = "\n".join(f"  0x{a.actor_id:08X}  {names.get(a.actor_id, '')}  {len(a.event_ids)} event(s)" for a in cands[:12])
            raise click.ClickException(f"{q!r} matches several actors, pick an id:\n{listing}")
        target = cands[0] if cands else None
    if target is None:
        raise click.ClickException(f"no actor {q!r} in {zone_name}; try --list")
    L = X.explain_actor(bytes(target.scene_data), list(target.references), list(target.event_ids),
                        list(target.event_offsets), blobs, names, only_event=event_id)
    head = [f"{zone_name} ({zone_id})  actor 0x{target.actor_id:08X} {names.get(target.actor_id, '')}",
            f"  references[]: {len(target.references)}   events: {[e for e in target.event_ids if e not in (0xFFFE, 0xFFFF)]}",
            f"  features: {', '.join(sorted(L.features)) or 'plain dialogue'}", ""]
    text = "\n".join(head + L.lines) + "\n"
    if output:
        Path(output).write_text(text, encoding="utf-8")
        click.echo(f"wrote {output} ({len(L.lines)} lines)")
    else:
        click.echo(text)


@click.command("survey")
@click.option("--op", "op_", required=True, help="Opcode, e.g. 0x71.")
@click.option("--sub", "sub_", default=None, help="Sub-opcode byte for variable opcodes, e.g. 0x12.")
@click.option("--zone", "zones", multiple=True, help="Restrict to zone ids (repeatable). Default: every zone.")
@click.option("-o", "--output", type=click.Path(dir_okay=False), help="Write rows to a file (UTF-8).")
def survey_cmd(op_, sub_, zones, output):
    """Every use of an opcode across the zones, operands resolved, with the last dialog line
    printed before it. Used to pin the number-window parameters (xi event survey --op 0x71 --sub 0x12).
    """
    import sys
    from xi import xi_config
    from xi.event import xi_explain as X
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    op = int(op_, 0); sub = int(sub_, 0) if sub_ is not None else None
    zids = [int(z) for z in zones] or None
    zfs = X.zone_files(Path(xi_config.FFXI_DIR), zids)
    rows = []
    for zf in zfs:
        try:
            rows.extend(X.survey_zone(zf, op, sub))
        except Exception as e:  # a broken DAT must not stop the survey
            click.echo(f"skip {zf.zone_id} {zf.name}: {e}", err=True)
    lines = [f"{h.zone_id:3d} {h.zone[:22]:22s} {h.actor[:22]:22s} ev{h.event_id!s:6s} +{h.offset:04x} | {h.operands} | next: {h.next_op[:60]} | prev: {h.previous_text[:110]}"
             for h in rows]
    click.echo(f"{len(rows)} use(s) of 0x{op:02x}" + (f" sub 0x{sub:02x}" if sub is not None else "") + f" in {len(zfs)} zone(s)")
    if output:
        Path(output).write_text("\n".join(lines) + "\n", encoding="utf-8")
        click.echo(f"wrote {output}")
    else:
        for l in lines[:400]:
            click.echo(l)
        if len(lines) > 400:
            click.echo(f"... {len(lines) - 400} more (use -o)")


@click.group("npc")
def npc_group():
    """Zone entity-name table (file 6720+zone): the client names an NPC only if its id is listed here."""
    pass


@npc_group.command("list")
@click.argument("zone")
@click.option("--tail", type=int, default=8, help="Show the last N records (default 8).")
def npc_list_cmd(zone, tail):
    """Show the zone's entity-name records: count, id range, gaps, next free id."""
    import sys
    from xi import xi_config
    from xi.event import xi_explain as X
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    _, _, zone_id, zone_name = _resolve_event_dat(zone)
    zf = X.zone_files(Path(xi_config.FFXI_DIR), [zone_id])[0]
    if not zf.npc:
        raise click.ClickException(f"no entity DAT for zone {zone_id}")
    data = zf.npc.read_bytes()
    recs = X.entity_records(data)
    ids = [sid for sid, _ in recs if sid]
    gaps = [(a, b) for a, b in zip(ids, ids[1:]) if b - a > 1]
    click.echo(f"{zone_name} ({zone_id}) {zf.npc.relative_to(Path(xi_config.FFXI_DIR)).as_posix()}: "
               f"{len(recs)} records, ids 0x{min(ids):08X}..0x{max(ids):08X}, {len(gaps)} gap(s), "
               f"next free 0x{X.next_free_entity_id(data, zone_id):08X} ({X.next_free_entity_id(data, zone_id)})")
    for sid, name in recs[-tail:]:
        click.echo(f"  0x{sid:08X} {sid}  {name}")


@npc_group.command("add")
@click.argument("zone")
@click.argument("name")
@click.option("--id", "sid", default=None, help="Server id (hex 0x… or decimal). Default: last id + --gap.")
@click.option("--gap", type=int, default=1, help="Distance past the last listed id when --id is omitted.")
@click.option("--replace", is_flag=True, help="Overwrite the name if the id is already listed.")
@click.option("--dry-run", is_flag=True)
def npc_add_cmd(zone, name, sid, gap, replace, dry_run):
    """Add an entity-name record so the client shows NAME for a new NPC id.

    \b
      xi event npc add 243 "Specialization Master" --gap 10
      xi event npc add 243 "Specialization Master" --id 0x010F314D
    Prints the id to use in data/zones/<zone>/npcs.yaml and writes the DAT in place under
    FFXI_DIR with a .base backup (same convention as the event/dialog writers).
    """
    import sys
    from xi import xi_config
    from xi.event import xi_explain as X
    from xi.xi_config import output_path_for
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    _, _, zone_id, zone_name = _resolve_event_dat(zone)
    zf = X.zone_files(Path(xi_config.FFXI_DIR), [zone_id])[0]
    if not zf.npc:
        raise click.ClickException(f"no entity DAT for zone {zone_id}")
    data = zf.npc.read_bytes()
    new_id = int(sid, 0) if sid else X.next_free_entity_id(data, zone_id, gap)
    if (new_id >> 12) & 0xFFF != zone_id or not (new_id & 0x01000000):
        raise click.ClickException(f"id 0x{new_id:08X} is not in zone {zone_id}'s range (0x{0x01000000 | zone_id << 12:08X}..)")
    try:
        out = X.add_entity_name(data, new_id, name, replace=replace)
    except ValueError as e:
        raise click.ClickException(str(e))
    rel = zf.npc.relative_to(Path(xi_config.FFXI_DIR)).as_posix()
    click.echo(f"{zone_name} ({zone_id}): {name!r} -> id 0x{new_id:08X} ({new_id}), index {new_id & 0xFFF}")
    click.echo(f"  entity DAT {rel}: {len(data)} -> {len(out)} bytes")
    click.echo(f"  server row: data/zones/<zone>/npcs.yaml  {new_id}:  script: {name.replace(' ', '_')}  display_name: {name}")
    if dry_run:
        click.echo("[dry-run] not writing")
        return
    dst = Path(output_path_for(str(zf.npc)))
    base = Path(str(dst) + ".base")
    if not base.exists():
        base.write_bytes(data)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out)
    click.echo(f"wrote {dst}")


@click.command("lint")
@click.argument("zone")
@click.argument("who")
@click.option("--event", "event_id", type=int, help="Only this event id.")
def lint_cmd(zone, who, event_id):
    """Pre-flight check of an actor's events (sizes, jumps, selectors, message ids, menu markers, end).

    \b
      xi event lint 243 0x010F3075 --event 10196
      xi event lint 235 Isakoth
    """
    import sys
    from xi import xi_config
    from xi.event import xi_explain as X, xi_lint
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    _, _, zone_id, zone_name = _resolve_event_dat(zone)
    zf = X.zone_files(Path(xi_config.FFXI_DIR), [zone_id])[0]
    actors, blobs, names = X.load_zone(zf)
    q = who.strip()
    if q.lower().startswith("0x") or q.isdigit():
        aid = int(q, 0)
    else:
        ids = [sid for sid, n in names.items() if n.lower() == q.lower()] or [sid for sid, n in names.items() if q.lower() in n.lower()]
        if len(ids) != 1:
            raise click.ClickException(f"{q!r} matches {len(ids)} actors; give an id")
        aid = ids[0]
    actor = next((a for a in actors if a.actor_id == aid), None)
    if actor is None:
        raise click.ClickException(f"actor 0x{aid:08X} has no block in {zone_name}")
    results = xi_lint.lint_actor(actor, blobs, event_id)
    bad = 0
    for eid, res in sorted(results.items()):
        status = "OK" if res.ok else "ERROR"
        click.echo(f"event {eid}: {status}  ({res.opcodes} opcodes, {len(res.calls)} call(s), {len(res.warnings)} warning(s))")
        for e in res.errors:
            click.echo(f"    error   {e}")
        for w in res.warnings:
            click.echo(f"    warning {w}")
        bad += not res.ok
    if bad:
        raise click.ClickException(f"{bad} event(s) with errors")


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

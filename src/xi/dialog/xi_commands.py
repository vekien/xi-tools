#!/usr/bin/env python3
"""`xi event dialogue` string-table commands — decode/edit FFXI event-message
("dialog") DATs. Registered under the `event dialogue` group (was the top-level
`xi dialog` group)."""

import json
import re
import shutil
from pathlib import Path

import click

from xi.dialog import xi_dialog as core
from xi.xi_config import FFXI_DIR, read_path_for, editable_dat, output_path_for


def _file_candidates(dat: str):
    """Path candidates for a DAT spec — with and without a `.DAT` suffix, relative
    to the CWD and to FFXI_DIR."""
    specs = [dat] + ([] if dat.lower().endswith(".dat") else [dat + ".DAT"])
    for s in specs:
        yield Path(s)
        yield Path(FFXI_DIR) / s


def _rel_dat(path: Path) -> str:
    """`…/ROM/1/41.DAT` -> `ROM/1/41.DAT` (uppercased) for matching FTABLE paths."""
    parts = list(Path(path).parts)
    for i, p in enumerate(parts):
        if p.upper() == "ROM" and i + 1 < len(parts):
            return "/".join(["ROM", *parts[i + 1:]]).upper()
    return Path(path).name.upper()


def _zone_dialog(zone_id: int):
    """(abs dialog DAT path, ROM-relative path) for a zone, or (None, None)."""
    from xi.zone.xi_inject import zone_dialog_file_id
    from xi.ftable.xi_core import scan_file_ids
    hits = scan_file_ids([zone_dialog_file_id(zone_id)])
    if not hits:
        return None, None
    return Path(FFXI_DIR) / hits[0]["dat"], hits[0]["dat"]


def _zone_table():
    from xi.zone.xi_list import get_zone_entries
    return get_zone_entries(path_prefix="")   # path like 'ROM/1/41.DAT' (the model DAT)


def _route_zone(zone_id: int, name: str, via: str) -> tuple[Path, str]:
    path, rel = _zone_dialog(zone_id)
    if not path or not path.is_file():
        raise click.ClickException(f"No dialog DAT found for zone {zone_id} ({name}).")
    return path, f"{via}zone {zone_id} ({name}) → dialog DAT {rel}"


def _resolve_dialog(dat: str) -> tuple[Path, str]:
    """Resolve a dialog DAT from a path (auto-appends `.DAT`), a zone id, or a zone
    name. Returns (path, note) — note explains any zone→dialog routing, else ``""``."""
    # 1) An existing file (with or without the .DAT suffix)?
    for cand in _file_candidates(dat):
        if cand.is_file():
            if core.looks_like_event_message(cand.read_bytes()):
                return cand, ""
            # Exists but isn't a dialog DAT — maybe a zone companion (model/NPC/event).
            rel = _rel_dat(cand)
            z = next((z for z in _zone_table() if z["path"].upper() == rel), None)
            if z:
                return _route_zone(z["id"], z["name"], f"{rel} is ")
            raise click.ClickException(
                f"{cand.name} is not a dialog DAT (and not a recognized zone DAT).\n"
                f"  Give a dialog DAT, a zone id, or a zone name.")
    # 2) A bare zone id?
    if dat.strip().isdigit():
        zid = int(dat)
        name = next((z["name"] for z in _zone_table() if z["id"] == zid), "?")
        return _route_zone(zid, name, "")
    # 3) A zone name (exact, else substring)?
    zones = _zone_table()
    q = dat.strip().lower()
    matches = [z for z in zones if z["name"].lower() == q] or \
              [z for z in zones if q in z["name"].lower()]
    if not matches:
        raise click.ClickException(
            f"No DAT or zone matches {dat!r}. Give a dialog DAT path, a zone id, or a zone name.")
    if len(matches) > 1:
        listing = "\n".join(f"  {z['id']:>4}  {z['name']}" for z in matches[:12])
        raise click.ClickException(
            f"{dat!r} matches {len(matches)} zones — narrow it or use the id:\n{listing}")
    return _route_zone(matches[0]["id"], matches[0]["name"], "")


def _load(dat: str):
    path, note = _resolve_dialog(dat)
    if note:
        click.echo(note, err=True)        # to stderr so it never pollutes --json stdout
    read = read_path_for(path)            # prefer an edited mirror when one exists
    try:
        entries, obf = core.load(read)
    except core.DialogError as e:
        raise click.ClickException(
            f"{read}: {e}\n  (this command expects a per-zone *dialog* DAT, "
            f"not the event-bytecode or NPC DAT)")
    return path, entries, obf


def _filter(entries, grep, index, prompts_only):
    out = entries
    if index:
        want = set(index)
        out = [e for e in out if e.index in want]
    if grep:
        g = grep.lower()
        out = [e for e in out if g in e.text.lower()]
    if prompts_only:
        out = [e for e in out if any(o.name.startswith("prompt") for o in e.opcodes)]
    return out


def _rom_rel(path: Path) -> str:
    """`.../ROM/25/39.DAT` -> `rom/25/39`; otherwise the file stem. Matches the
    `exports/<group>/<rom>/…` layout used across xi."""
    parts = list(path.parts)
    for i, p in enumerate(parts):
        if p.upper() == "ROM" and i + 1 < len(parts):
            return "/".join(["rom", *parts[i + 1:]]).rsplit(".", 1)[0].lower()
    return path.stem


def _default_out(path: Path) -> Path:
    """Default JSON location, mirroring the ROM path:
    ROM/25/39.DAT -> exports/event/dialogue/rom/25/39/39.json."""
    return Path("exports") / "event" / "dialogue" / _rom_rel(path) / f"{path.stem}.json"


def _preview(path, entries, obf, shown, cap):
    click.echo(f"{path.name}: {len(entries)} entries"
               f"{' (XOR-0x80 obfuscated)' if obf else ''}")
    hist = core.opcode_histogram(entries)
    if hist:
        top = ", ".join(f"{k}={v}" for k, v in list(hist.items())[:8])
        click.echo(f"opcodes: {top}")
    click.echo(f"showing {min(len(shown), cap)} of {len(shown)} matched\n")
    for e in shown[:cap]:
        line = e.text.replace("\n", " / ")
        if len(line) > 100:
            line = line[:100] + "…"
        click.echo(f"#{e.index:<6} @0x{e.offset:06x}  {line}")
        ops = [o for o in e.opcodes if o.name != "newline"]
        if ops:
            click.echo("           " + "  ".join(
                f"{o.name}({o.raw}){'=' + o.note if o.note and o.name.startswith('prompt') else ''}"
                for o in ops[:6]))
    if len(shown) > cap:
        click.echo(f"\n… {len(shown) - cap} more (use --limit, --grep, or the default JSON dump)")


@click.command()
@click.argument("dat")
@click.option("-o", "--output", type=click.Path(dir_okay=False), default=None,
              help="JSON output path (default: exports/event/dialogue/<rom>/<stem>.json).")
@click.option("--json", "to_stdout", is_flag=True,
              help="Emit JSON to stdout instead of writing a file (for piping to jq).")
@click.option("--preview", is_flag=True,
              help="Print a human-readable listing instead of writing JSON.")
@click.option("--grep", help="Only entries whose decoded text contains TEXT (case-insensitive).")
@click.option("--index", type=int, multiple=True, help="Only this entry index (repeatable).")
@click.option("--prompts-only", is_flag=True, help="Only entries that contain a continue-prompt code.")
@click.option("--limit", type=int, default=0, help="Cap entries emitted (0 = no cap).")
@click.option("--no-opcodes", is_flag=True, help="Omit the per-entry opcode list.")
@click.option("--no-raw", is_flag=True, help="Omit the raw hex bytes.")
def export_cmd(dat, output, to_stdout, preview, grep, index, prompts_only, limit, no_opcodes, no_raw):
    """Decode a dialog DAT into easy-to-share JSON.

    \b
    By default writes three sibling files under exports/event/dialogue/<rom>/:
      <stem>.json          clean text (index + offset + text)
      <stem>.opcodes.json  the control codes per entry + histogram
      <stem>.hex.json      raw de-obfuscated bytes per entry
    joinable by "index". E.g. ROM/25/39.DAT -> exports/event/dialogue/rom/25/39/39.json (+ .opcodes/.hex).

    \b
      xi event dialogue export ROM/25/39.DAT                 # the 3 files
      xi event dialogue export ROM/25/39.DAT --no-raw        # skip 39.hex.json
      xi event dialogue export ROM/25/39.DAT -o out.json     # out.json (+ .opcodes/.hex)
      xi event dialogue export ROM/25/39.DAT --json | jq     # one combined doc to stdout
      xi event dialogue export ROM/25/39.DAT --grep "Mog House" --preview
    """
    path, entries, obf = _load(dat)
    shown = _filter(entries, grep, index, prompts_only)

    if preview:
        _preview(path, entries, obf, shown, limit if limit > 0 else 30)
        return

    cap = limit if limit > 0 else len(shown)
    sub = shown[:cap]
    meta = {"file": str(path), "format": "event-message", "obfuscated": obf,
            "entry_count": len(entries), "shown": len(sub)}

    # --json: a single combined doc to stdout (splitting makes no sense on a pipe).
    if to_stdout:
        doc = {**meta, "opcode_histogram": core.opcode_histogram(entries),
               "entries": [e.to_dict(with_raw=not no_raw, with_opcodes=not no_opcodes)
                           for e in sub]}
        click.echo(json.dumps(doc, ensure_ascii=False, indent=2))
        return

    base = Path(output) if output else _default_out(path)
    base.parent.mkdir(parents=True, exist_ok=True)
    stem = str(base.with_suffix(""))   # ".../39.json" -> ".../39"

    def _write(p, doc):
        Path(p).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        return Path(p)

    written = [_write(base, {**meta, "entries": [e.text_dict() for e in sub]})]
    if not no_opcodes:
        written.append(_write(stem + ".opcodes.json",
                              {**meta, "opcode_histogram": core.opcode_histogram(entries),
                               "entries": [e.opcodes_dict() for e in sub]}))
    if not no_raw:
        written.append(_write(stem + ".hex.json",
                              {**meta, "entries": [e.hex_dict() for e in sub]}))

    click.echo(f"Wrote {len(sub)} / {len(entries)} entr(ies):")
    for w in written:
        click.echo(f"  {w}")


@click.command("edit")
@click.argument("dat")
@click.option("--index", type=int, required=True,
              help="Entry index to replace (find it with `event dialogue export … --preview`).")
@click.option("--text", required=True,
              help="New text. Escapes: \\n newline, \\v prompt ▼, \\\\ literal; tokens {player} {npc} {auto:N}.")
@click.option("--dry-run", is_flag=True, help="Show the change without writing.")
def edit_cmd(dat, index, text, dry_run):
    """Replace dialog entry --index with --text, rebuilding the DAT.

    \b
    Escapes:  \\n newline   \\v press-enter prompt (▼)   \\\\ literal backslash
    Tokens:   {player}  {npc}  {auto:N}  (auto-advance after N seconds)

    \b
    Writes the DAT in place under FFXI_DIR; the pristine bytes are kept in a
    <dat>.base backup (restore with `dialogue reset`). Edits layer, so you can
    edit several entries in turn. Example:
      xi event dialogue edit ROM/25/39.DAT --index 0 --text "Welcome home!\\nYour Mog House awaits.\\v"
    """
    src, note = _resolve_dialog(dat)
    if note:
        click.echo(note, err=True)
    data = read_path_for(src).read_bytes()   # mirror if it exists, else pristine — no side effects

    blobs, obf = core.raw_entry_blobs(data)
    if not (0 <= index < len(blobs)):
        raise click.ClickException(f"index {index} out of range (0..{len(blobs) - 1})")

    old_text = core.parse_event_message(data)[0][index].text
    old_blob = blobs[index]
    blobs[index] = core.replace_entry_text(old_blob, text)
    rebuilt = core.build_container(blobs, obf)
    new_text = core.parse_event_message(rebuilt)[0][index].text

    click.echo(f"entry #{index}:")
    click.echo(f"  old: {old_text!r}")
    click.echo(f"  new: {new_text!r}")
    # Heads-up if the entry carried variant sub-strings (preserved, but now stale).
    variants = [p for p in old_blob.split(b"\x00")[1:] if p.strip(b"\x07")]
    if variants:
        click.echo(f"  note: {len(variants)} variant sub-string(s) preserved unchanged")
    click.echo(f"  size: {len(data)} -> {len(rebuilt)} bytes")

    if dry_run:
        click.echo("(dry run — not written)")
        return
    editable_dat(src, fresh=False).write_bytes(rebuilt)   # mirror (seeded once), keeps prior edits
    click.echo(f"Wrote: {output_path_for(src)}")


@click.command("search")
@click.argument("dat")
@click.argument("query")
@click.option("--regex", is_flag=True, help="Treat QUERY as a regular expression.")
@click.option("-s", "--case-sensitive", is_flag=True, help="Case-sensitive match.")
@click.option("--limit", type=int, default=50, help="Max matches to show (0 = all).")
def search_cmd(dat, query, regex, case_sensitive, limit):
    """Find dialog entries whose text matches QUERY — prints index + text.

    \b
    Use the printed #index with `xi event dialogue edit … --index N`.
      xi event dialogue search ROM/25/39.DAT "Mog House"
      xi event dialogue search ROM/25/39.DAT "lies to the"        # matches across line breaks
      xi event dialogue search ROM/25/39.DAT "San d.Oria" --regex
    """
    path, entries, _ = _load(dat)
    if regex:
        try:
            pat = re.compile(query, 0 if case_sensitive else re.IGNORECASE)
        except re.error as e:
            raise click.ClickException(f"bad regex: {e}")
        hit = lambda h: pat.search(h) is not None
    elif case_sensitive:
        hit = lambda h: query in h
    else:
        q = query.lower()
        hit = lambda h: q in h.lower()

    # Match against text with line breaks flattened, so a query can span them.
    matches = [e for e in entries if hit(e.text.replace("\n", " "))]
    cap = limit if limit > 0 else len(matches)
    click.echo(f"{len(matches)} match(es) for {query!r} in {path.name}:")
    for e in matches[:cap]:
        line = e.text.replace("\n", " / ")
        if len(line) > 110:
            line = line[:110] + "…"
        click.echo(f"  #{e.index:<6} {line}")
    if len(matches) > cap:
        click.echo(f"  … {len(matches) - cap} more (raise --limit)")


def _reset_dat(src: Path, dry_run: bool, *, required: bool = True) -> None:
    """Reset a single DAT to pristine: restore <dat> from its <dat>.base backup.

    ``required=False`` makes "nothing to reset" a note instead of an error — used for
    the ``--full`` event DAT, which may never have been edited."""
    base = src.with_name(src.name + ".base")
    rel = _rel_dat(src)   # ROM/25/54.DAT — distinguishes DATs that share a basename

    if dry_run:
        click.echo(f"Would restore {rel} from {base}" if base.exists()
                   else f"{rel}: no .base backup — nothing to reset.")
        return

    if not base.exists():
        msg = f"No .base backup for {rel} — nothing to reset."
        if required:
            raise click.ClickException(msg)
        click.echo(msg)
        return
    shutil.copy2(base, src)
    click.echo(f"Restored {rel} from {base}")


def _zone_event_dat_for(dat: str, dialog_path: Path) -> Path | None:
    """The zone's event DAT (pristine FFXI_DIR path) matching a resolved dialog DAT,
    for `reset --full`. Resolves the zone from a numeric/name input, else reverse-maps
    the dialog file id against the zone table. Returns None if undeterminable."""
    from xi.zone.xi_inject import zone_dialog_file_id, zone_event_file_id
    from xi.ftable.xi_core import scan_file_ids

    s = dat.strip()
    zid = None
    if s.isdigit():
        zid = int(s)
    else:
        zones = _zone_table()
        q = s.lower()
        matches = ([z for z in zones if z["name"].lower() == q]
                   or [z for z in zones if q in z["name"].lower()])
        if len(matches) == 1:
            zid = matches[0]["id"]
        else:
            # Raw dialog-DAT path (or ambiguous name): reverse-map dialog file id → zone.
            rel = _rel_dat(dialog_path)
            id_to_zone = {zone_dialog_file_id(z["id"]): z["id"] for z in zones}
            for h in scan_file_ids(list(id_to_zone.keys())):   # COMPACTS — key off h['file_id']
                if h["dat"].upper() == rel:
                    zid = id_to_zone.get(h["file_id"])
                    if zid is not None:
                        break
    if zid is None:
        return None
    hits = scan_file_ids([zone_event_file_id(zid)])
    return Path(FFXI_DIR) / hits[0]["dat"] if hits else None


@click.command("reset")
@click.argument("dat")
@click.option("--full", is_flag=True,
              help="Also reset the zone's EVENT DAT — fully undoes a `dialogue new` "
                   "(which writes both the dialog and the event DAT), not just `edit`s.")
@click.option("--dry-run", is_flag=True, help="Show what would be reset without doing it.")
def reset_cmd(dat, full, dry_run):
    """Reset a dialog DAT back to the pristine original (undo `edit`s).

    \b
    Restores <dat> from its <dat>.base backup.
    --full also resets the zone's event DAT, so it cleanly undoes a `dialogue new`
    (which writes both DATs); without it, only the dialog string table is reset.
      xi event dialogue reset ROM/25/39.DAT
      xi event dialogue reset 245 --full        # also undo the spliced event
    """
    src, note = _resolve_dialog(dat)
    if note:
        click.echo(note, err=True)

    if full:
        click.echo("dialog DAT:")
    _reset_dat(src, dry_run)

    if full:
        event_src = _zone_event_dat_for(dat, src)
        if event_src is None or not event_src.is_file():
            click.echo("--full: couldn't find the zone's event DAT — skipped.", err=True)
            return
        click.echo("event DAT:")
        _reset_dat(event_src, dry_run, required=False)


@click.command("info")
@click.argument("dat")
def info_cmd(dat):
    """Show a dialog DAT's entry count + opcode histogram (no full dump)."""
    path, entries, obf = _load(dat)
    click.echo(f"file        : {path}")
    click.echo(f"format      : event-message{' (XOR-0x80 obfuscated)' if obf else ''}")
    click.echo(f"entries     : {len(entries)}")
    prompts = sum(1 for e in entries if any(o.name.startswith("prompt") for o in e.opcodes))
    click.echo(f"w/ prompts  : {prompts}")
    click.echo("opcode histogram:")
    for k, v in core.opcode_histogram(entries).items():
        click.echo(f"  {k:<14} {v}")

#!/usr/bin/env python3
"""`xi audio refs <dat>` — list the sound effects a DAT references.

FFXI links sounds through `0x3D` SoundEffectPointer sections: each is the magic
``SeSep␠␠`` followed by a `u32` sound id. They live inside effect/animation DATs
(so a spell's effect DAT names the sounds it plays) and zone environment data (so
a zone DAT names its ambient sounds). This command finds every such reference,
resolves it to a `.spw` file, and (where known) a name + category.
"""

import json
import struct
from pathlib import Path

import click

from xi.audio import xi_core as core
from xi.audio import xi_names as names
from xi.entity.anim.xi_export import parse_sections
from xi.xi_config import FFXI_DIR

SECTION_SOUND_POINTER = 0x3D
_MAGIC = b"SeSep"


def scan_sound_refs(data: bytes) -> list:
    """Every `0x3D` sound pointer in a DAT, in file order. Each entry:
    {section, sound_id, folder, file, spw, title, category, exists, located}."""
    base = Path(FFXI_DIR)
    have_base = base.is_dir()
    refs = []
    for s in parse_sections(data):
        if s.type_code != SECTION_SOUND_POINTER:
            continue
        if data[s.data_start:s.data_start + len(_MAGIC)] != _MAGIC:
            continue
        sound_id = struct.unpack_from("<I", data, s.data_start + 8)[0]
        folder, file = names.sound_id_to_folder_file(sound_id)
        located = core.locate_sound(base, sound_id) if have_base else None
        refs.append({
            "section": s.name,
            "sound_id": sound_id,
            "folder": f"se{folder}",
            "file": f"se{file}",
            "spw": names.sound_id_to_relpath(sound_id),
            "title": names.sfx_name(sound_id),
            "category": names.sfx_category(sound_id),
            "exists": located is not None,
            "located_root": located[1] if located else None,
        })
    return refs


def _resolve(dat_path: str) -> Path:
    """Accept a ROM-relative spec, absolute path, or plain path to a DAT."""
    from xi.entity.mesh.xi_export import resolve_dat_path
    try:
        return Path(resolve_dat_path(dat_path))
    except (FileNotFoundError, ValueError):
        p = Path(dat_path)
        if p.is_file():
            return p
        raise click.ClickException(f"DAT not found: {dat_path}")


@click.command("refs")
@click.argument("dat_path")
@click.option("--out", "out_json", type=click.Path(), default=None,
              help="Write JSON here (default: exports/audio/refs/<stem>.json).")
@click.option("--unique", is_flag=True,
              help="Collapse repeated sound ids to one entry each.")
@click.option("--stdout", "to_stdout", is_flag=True,
              help="Print the JSON to stdout instead of writing a file.")
def refs_cmd(dat_path, out_json, unique, to_stdout):
    """List the sound effects a DAT references (effect/zone/mob DATs), as JSON.

    \b
      xi audio refs ROM/198/24.DAT                 # a spell/effect DAT
      xi audio refs ROM/1/77.DAT --unique          # a zone (ambient sounds)
      xi audio refs path/to/effect.DAT --stdout
    """
    resolved = _resolve(dat_path)
    data = resolved.read_bytes()
    refs = scan_sound_refs(data)

    if unique:
        seen, deduped = set(), []
        for r in refs:
            if r["sound_id"] in seen:
                continue
            seen.add(r["sound_id"])
            deduped.append(r)
        refs = deduped

    payload = {
        "dat": str(resolved),
        "ref_count": len(refs),
        "unique_sound_count": len({r["sound_id"] for r in refs}),
        "missing_count": sum(1 for r in refs if not r["exists"]),
        "sound_refs": refs,
    }
    blob = json.dumps(payload, ensure_ascii=False, indent=2)

    if to_stdout:
        click.echo(blob)
        return

    if out_json:
        out = Path(out_json)
    else:
        out = Path("exports") / "audio" / "refs" / f"{resolved.stem}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(blob, encoding="utf-8")

    click.echo(f"{payload['ref_count']} sound reference(s), "
               f"{payload['unique_sound_count']} unique -> {out}")
    for r in refs[:25]:
        nm = f"  {r['title']}" if r["title"] else ""
        cat = f"  [{r['category']}]" if r["category"] else ""
        miss = "" if r["exists"] else "  (file missing)"
        click.echo(f"  {r['section']}  {r['file']}.spw  (id {r['sound_id']}){cat}{nm}{miss}")
    if len(refs) > 25:
        click.echo(f"  … and {len(refs) - 25} more (see JSON)")

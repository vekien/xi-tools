#!/usr/bin/env python3
"""`xi fx dump` — dump every effect (+ decoded params) to JSON."""

import json
import struct
from pathlib import Path
from typing import Dict, List

import click

from xi.fx.xi_core import (parse_sections, resolve_dat_path, classify, _load_library,
                          EFFECT_TYPE, _fourcc, _mesh_fourccs, _texture_fourccs,
                          _effect_target, _effect_texture, _read_pos_at, _rom_rel,
                          _tag_payload, _TAG_COLOR, _TAG_SCALE, _TAG_CULL,
                          _OFF_INTERVAL, _OFF_COUNT, _OFF_GENFLAGS, _AUTORUN_BIT,
                          _OFF_EMIT_VARIANCE, _OFF_ATTACH, ATTACH_TYPES)
from xi.fx.xi_opcodes import decode_subsections
from xi.event.xi_event import _routine_sec2_commands
from xi.xi_config import read_path_for

ROUTINE_TYPE = 0x07
_OP_GEN  = 0x02
_OP_STOP = frozenset({0x1E, 0x2D})


def _read_params(body: bytes) -> Dict:
    """Decode the parameters we know how to locate (validated against xim)."""
    params: Dict = {}
    if len(body) >= _OFF_ATTACH + 2:                     # attachFlags @ data-start (+0x10)
        params["attach"] = ATTACH_TYPES.get(struct.unpack("<H", body[_OFF_ATTACH:_OFF_ATTACH + 2])[0] & 0x0F, "unknown")
    o = _tag_payload(body, _TAG_COLOR)
    if o is not None:
        b, g, r = body[o], body[o + 1], body[o + 2]
        params["color_rgb"] = f"{r:02X}{g:02X}{b:02X}"
    o = _tag_payload(body, _TAG_SCALE)
    if o is not None:
        params["scale"] = [round(v, 4) for v in struct.unpack("<3f", body[o:o + 12])]
    o = _tag_payload(body, _TAG_CULL)            # sec1 0x0A GeneratorCull: maxEmitDistance
    if o is not None:
        params["draw_distance"] = round(struct.unpack("<f", body[o:o + 4])[0], 2)
    if len(body) > _OFF_GENFLAGS:
        params["emission_variance"] = struct.unpack("<H", body[_OFF_EMIT_VARIANCE:_OFF_EMIT_VARIANCE + 2])[0]
        params["spawn_interval"] = struct.unpack("<H", body[_OFF_INTERVAL:_OFF_INTERVAL + 2])[0]
        params["count"] = body[_OFF_COUNT]
        params["autorun"] = bool(body[_OFF_GENFLAGS] & _AUTORUN_BIT)
    return params


def dump_effects(dat_path: Path, include_opcodes: bool = False) -> Dict:
    """Full structured dump of every `0x05` effect: name, offset/size, library
    label, the mesh it places + position, and the decoded params (color / scale /
    draw range). With ``include_opcodes`` each effect also gets its raw 4 sub-section
    opcode streams. Suitable as a base for offline authoring / diffing."""
    data = bytearray(read_path_for(dat_path).read_bytes())
    sections = parse_sections(data)
    mesh_ccs = _mesh_fourccs(data, sections)
    tex_ccs = _texture_fourccs(data, sections)
    lib = _load_library()
    effects: List[Dict] = []
    for s in sections:
        if s.type_code != EFFECT_TYPE:
            continue
        body = bytes(data[s.start:s.start + s.size])
        name = _fourcc(data, s.start)
        mesh, pos = _effect_target(body, mesh_ccs)
        texture = _effect_texture(body, tex_ccs)
        if pos is None and texture:
            pos = _read_pos_at(body, tex_ccs)
        entry = classify(name, mesh, texture, lib)
        eff: Dict = {
            "name": name,
            "offset": f"0x{s.start:x}",
            "size": s.size,
            "label": entry["label"] if entry else None,
            "category": entry.get("category") if entry else None,
            "verified": bool(entry.get("verified")) if entry else False,
            "mesh": mesh,
            "texture": texture,
            "position": list(pos) if pos else None,
            "params": _read_params(body),
        }
        if include_opcodes:
            eff["opcodes"] = decode_subsections(body)
        effects.append(eff)
    # ── 0x07 EffectRoutine schedules ────────────────────────────────────────────
    schedules: List[Dict] = []
    for s in sections:
        if s.type_code != ROUTINE_TYPE:
            continue
        name = _fourcc(data, s.start)
        cmds = _routine_sec2_commands(bytes(data), name)
        fires: List[Dict] = []
        clock = 0.0
        for c in cmds:
            clock += c.get("delay", 0)
            if c["op"] == _OP_GEN and c.get("ref"):
                fires.append({"ref": c["ref"], "start": int(clock), "dur": int(c.get("dur", 0))})
            elif c["op"] in _OP_STOP and c.get("ref"):
                fires.append({"ref": c["ref"], "start": int(clock), "dur": 0, "op": "stop"})
        schedules.append({"name": name, "offset": f"0x{s.start:x}", "fires": fires})

    cats: Dict[str, int] = {}
    for e in effects:
        cats[e["category"] or "unidentified"] = cats.get(e["category"] or "unidentified", 0) + 1
    return {"dat": str(dat_path), "count": len(effects),
            "categories": dict(sorted(cats.items(), key=lambda kv: -kv[1])),
            "effects": effects, "schedules": schedules}


@click.command()
@click.argument("dat_path")
@click.option("--out", "out_path", type=click.Path(), default=None, help="Output JSON path (default: exports/fx/<rom>.json).")
@click.option("--stdout", "to_stdout", is_flag=True, help="Print JSON to stdout instead of writing a file.")
@click.option("--opcodes", "include_opcodes", is_flag=True, help="Also decode each effect's 4 raw opcode sub-sections (large output).")
def dump_cmd(dat_path, out_path, to_stdout, include_opcodes):
    """Dump all effects to JSON (name, label, placed mesh + position, decoded params)."""
    try:
        resolved = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    data = dump_effects(resolved, include_opcodes=include_opcodes)
    text = json.dumps(data, indent=2)
    if to_stdout:
        click.echo(text)
        return
    out = Path(out_path) if out_path else Path("exports") / "fx" / (_rom_rel(Path(resolved)) + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    click.echo(f"Wrote {data['count']} effects -> {out}")
    click.echo("  " + ", ".join(f"{k}:{v}" for k, v in data["categories"].items()))

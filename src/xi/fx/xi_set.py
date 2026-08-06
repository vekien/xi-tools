#!/usr/bin/env python3
"""`xi fx set` — edit effect parameters in place (pos / scale / color / draw-distance
/ spawn-interval / flow / count), located by opcode tag or header field."""

import struct
from pathlib import Path
from typing import List, Tuple

import click

from xi.xi_config import editable_dat, output_path_for
from xi.fx.xi_core import (parse_sections, resolve_dat_path, EFFECT_TYPE, _fourcc,
                          _mesh_fourccs, _texture_fourccs, _matches, _tag_payload,
                          _pos_offset, _TAG_COLOR, _TAG_SCALE, _TAG_CULL, _TAG_FLOW,
                          _OFF_COUNT, _OFF_INTERVAL, _OFF_GENFLAGS, _AUTORUN_BIT)


def _parse_color(text: str) -> Tuple[int, int, int]:
    t = text.strip().lstrip("#")
    if "," in t:
        r, g, b = (int(x) for x in t.split(","))
    elif len(t) == 6:
        r, g, b = int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16)
    else:
        raise ValueError(f"color must be RRGGBB hex or 'r,g,b' (got {text!r})")
    for v in (r, g, b):
        if not 0 <= v <= 255:
            raise ValueError("color channels must be 0-255")
    return r, g, b


def set_effect_params(dat_path: Path, names, *, pos=None, scale=None, scale_mul=None,
                      color=None, draw_range=None, spawn_interval=None, count=None,
                      flow_mul=None, autorun=None) -> List[dict]:
    """Edit params on every `0x05` effect matching a name/prefix. Params are found
    by opcode tag (color/scale/range) or by the placed-mesh reference (position),
    so this works on any effect that uses the shared format. In-place on the DAT
    (unencrypted), with a `<dat>.base` backup of the pristine bytes.
    Returns a per-effect change log."""
    dat = editable_dat(dat_path, fresh=False)
    data = bytearray(dat.read_bytes())
    sections = parse_sections(data)
    mesh_ccs = _mesh_fourccs(data, sections) | _texture_fourccs(data, sections)
    log: List[dict] = []
    for s in sections:
        if s.type_code != EFFECT_TYPE or not _matches(_fourcc(data, s.start), names):
            continue
        body = bytes(data[s.start:s.start + s.size])
        name = _fourcc(data, s.start)
        changes = {}
        if color is not None:
            off = _tag_payload(body, _TAG_COLOR)
            if off is not None:
                a = s.start + off
                changes["color"] = (bytes(data[a:a + 3]).hex(), bytes((color[2], color[1], color[0])).hex())
                data[a], data[a + 1], data[a + 2] = color[2], color[1], color[0]  # B,G,R
        if scale is not None or scale_mul is not None:
            off = _tag_payload(body, _TAG_SCALE)
            if off is not None:
                a = s.start + off
                old = struct.unpack("<3f", data[a:a + 12])
                new = tuple(scale) if scale is not None else tuple(v * scale_mul for v in old)
                struct.pack_into("<3f", data, a, *new)
                changes["scale"] = (tuple(round(v, 3) for v in old), tuple(round(v, 3) for v in new))
        if draw_range is not None:
            # GeneratorCull (sec1 0x0A): first float = maxEmitDistance = the draw
            # distance (per xim). draw_range = (near, far); we set maxEmitDistance = far.
            cull = _tag_payload(body, _TAG_CULL)
            if cull is not None:
                a = s.start + cull
                old = struct.unpack("<f", data[a:a + 4])[0]
                struct.pack_into("<f", data, a, float(draw_range[1]))
                changes["draw_distance"] = (round(old, 1), float(draw_range[1]))
        if pos is not None:
            off = _pos_offset(body, mesh_ccs)
            if off is not None:
                a = s.start + off
                old = struct.unpack("<3f", data[a:a + 12])
                struct.pack_into("<3f", data, a, *(float(c) for c in pos))
                changes["pos"] = (tuple(round(v, 2) for v in old), tuple(float(c) for c in pos))
        if spawn_interval is not None:
            a = s.start + _OFF_INTERVAL
            old = struct.unpack("<H", data[a:a + 2])[0]
            struct.pack_into("<H", data, a, int(spawn_interval) & 0xFFFF)
            changes["spawn_interval"] = (old, int(spawn_interval))
        if count is not None:
            a = s.start + _OFF_COUNT          # particlesPerEmission (u8)
            old = data[a]
            data[a] = int(count) & 0xFF
            changes["count"] = (old, int(count) & 0xFF)
        if autorun is not None:
            a = s.start + _OFF_GENFLAGS        # genFlags u8; bit 0x10 = autoRun
            old = data[a]
            data[a] = (old | _AUTORUN_BIT) if autorun else (old & ~_AUTORUN_BIT)
            changes["autorun"] = (bool(old & _AUTORUN_BIT), bool(autorun))
        if flow_mul is not None:
            for tag in _TAG_FLOW:
                off = _tag_payload(body, tag)
                if off is not None:
                    a = s.start + off + 4  # skip X float -> Y (flow speed)
                    y = struct.unpack("<f", data[a:a + 4])[0]
                    struct.pack_into("<f", data, a, y * flow_mul)
                    changes.setdefault("flow", (round(y, 4), round(y * flow_mul, 4)))
        if changes:
            log.append({"name": name, **changes})
    if log:
        dat.write_bytes(data)
    return log


@click.command()
@click.argument("dat_path")
@click.argument("names", nargs=-1, required=True)
@click.option("--pos", "--at-pos", "pos", nargs=3, type=float, metavar="X Y Z", help="Set local position (alias: --at-pos). Best with a single effect — a prefix sets them all to the same point.")
@click.option("--scale", nargs=3, type=float, metavar="X Y Z", help="Set scale (width height depth).")
@click.option("--scale-mul", type=float, metavar="F", help="Multiply current scale by F.")
@click.option("--color", metavar="RRGGBB", help="Set tint color (RRGGBB hex or 'r,g,b'). Multiplies the texture — gray/white tints fully, pre-colored textures barely shift.")
@click.option("--range", "draw_range", nargs=2, type=float, metavar="NEAR FAR", help="Set draw distance (GeneratorCull maxEmitDistance = FAR; NEAR currently unused).")
@click.option("--spawn-interval", type=int, metavar="FRAMES", help="framesPerEmission — frames between spawns (lower = faster/denser).")
@click.option("--flow", "flow_mul", type=float, metavar="F", help="Multiply texture/position flow speed by F (observed; opcode TBD).")
@click.option("--count", type=int, metavar="N", help="particlesPerEmission (0-255) — particles emitted per spawn.")
@click.option("--autorun/--no-autorun", "autorun", default=None, help="Set/clear the autoRun flag (genFlags 0x10): make the effect auto-spawn (vs scheduler-triggered) — the fix for transplanted effects that don't render.")
def set_cmd(dat_path, names, pos, scale, scale_mul, color, draw_range, spawn_interval, flow_mul, count, autorun):
    """Edit effect parameters in place (by name/prefix). Params are located by
    opcode (color/scale/draw-distance/flow) or header field (spawn-interval/count/
    autorun), validated against xim — so most work on any effect.

    Examples:
      xi fx set ROM/1/41 tki --color 00FF00 --scale-mul 4 --range 100 500
      xi fx set ROM/1/41 tki --spawn-interval 2 --count 40
      xi fx set ROM/1/41 fir0 --autorun        # make a transplanted effect spawn
    """
    if not any(v is not None for v in (pos, scale, scale_mul, color, draw_range, spawn_interval, flow_mul, count, autorun)):
        raise click.ClickException("Nothing to set — pass at least one of --pos/--scale/--scale-mul/--color/--range/--spawn-interval/--flow/--count/--autorun.")
    if scale and scale_mul is not None:
        raise click.ClickException("Use either --scale or --scale-mul, not both.")
    try:
        resolved = resolve_dat_path(dat_path)
        rgb = _parse_color(color) if color else None
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e))
    log = set_effect_params(resolved, names, pos=pos or None, scale=scale or None,
                            scale_mul=scale_mul, color=rgb, draw_range=draw_range or None,
                            spawn_interval=spawn_interval, count=count, flow_mul=flow_mul, autorun=autorun)
    if not log:
        raise click.ClickException(f"No effects matched (or no editable params found): {', '.join(names)}")
    click.echo(f"Edited {len(log)} effect(s) in {resolved}:")
    for e in log:
        parts = [f"{k}={v[0]}->{v[1]}" for k, v in e.items() if k != "name"]
        click.echo(f"  {e['name']:8s} {'; '.join(parts)}")
    click.echo(f"Wrote: {output_path_for(resolved)}")

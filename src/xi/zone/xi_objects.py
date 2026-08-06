#!/usr/bin/env python3
"""`xi zone object list` — list every placement record in a zone DAT.

Each placement is the full 0x64-byte ZoneObject record (mesh id, TRS, LOD distance
thresholds, flag bytes, culling/effect/environment/file links, point-light indices),
plus the footprint of the mesh it references. Placements whose mesh is physically
small (footprint <= --max-footprint) are auto-tagged ``"object"`` — the decorative
props (boxes, pots, benches, lamps) as opposed to walls / buildings / terrain, which
have no explicit type flag in the DAT but are an order of magnitude larger.
"""

import json
import struct
from collections import Counter
from pathlib import Path

import click

from xi.entity.anim.xi_export import parse_sections
from xi.entity.mesh.xi_export import resolve_dat_path
from xi.xi_config import FFXI_DIR, read_path_for
from xi.zone.xi_decrypt import decrypt_zone_objects, load_key_tables
from xi.zone.xi_export import SECTION_TYPE_ZONE_DEF

# Default mesh footprint (max horizontal extent, yalms) at/under which a placement is
# tagged "object". On Lower Jeuno every prop (box/pot/lamp) sits <= 2.0 and every
# wall/building/terrain piece >= 22, so anything in the ~3-15 gap separates them cleanly.
DEFAULT_OBJECT_FOOTPRINT = 5.0

# ZoneObject record layout (0x64 bytes) — see thirdparty/xim ZoneDefParser.parseZoneObjs.
REC_SIZE     = 0x64
OBJ_ARRAY    = 0x20    # records begin at data_start + 0x20
OFF_ID       = 0x00    # char[0x10] mesh id
OFF_POS      = 0x10    # 3f
OFF_ROT      = 0x1C    # 3f
OFF_SCALE    = 0x28    # 3f
OFF_EFFECT   = 0x34    # DatId (4 bytes) — particle/effect link
OFF_LOD_HIGH = 0x38    # float: distance under which the high-detail mesh draws
OFF_LOD_MID  = 0x3C    # float: distance under which the mid-detail mesh draws
OFF_LOD_LOW  = 0x40    # float: draw distance (engine stops drawing past this)
OFF_FLAGS    = 0x44    # 4 flag bytes (flags1 & 0x2 = skip-during-decal-render)
OFF_CULL     = 0x48    # u32 culling-table link (file offset, 0 = none)
OFF_ENV      = 0x4C    # DatId (4 bytes) — environment link
OFF_FILE     = 0x50    # u32 file-id link (0 = none)
OFF_PLIGHTS  = 0x54    # 4 x u32 point-light indices (1-based, 0 = unused)


def _datid(data: bytes, off: int) -> str | None:
    """Return a 4-byte DatId link as lowercase hex, or None when zero."""
    raw = bytes(data[off:off + 4])
    return None if raw == b"\x00\x00\x00\x00" else raw.hex()


def _read_record(data: bytes, ds: int, index: int) -> dict:
    """Decode one full ZoneObject placement record into a dict."""
    rec = ds + OBJ_ARRAY + index * REC_SIZE
    name = bytes(data[rec:rec + 0x10]).split(b"\x00", 1)[0].decode("ascii", "replace").strip()
    flags = list(data[rec + OFF_FLAGS:rec + OFF_FLAGS + 4])
    plights_raw = struct.unpack_from("<4I", data, rec + OFF_PLIGHTS)
    return {
        "index": index,
        "name": name,
        "pos":   list(struct.unpack_from("<3f", data, rec + OFF_POS)),
        "rot":   list(struct.unpack_from("<3f", data, rec + OFF_ROT)),
        "scale": list(struct.unpack_from("<3f", data, rec + OFF_SCALE)),
        "lod": {
            "high": struct.unpack_from("<f", data, rec + OFF_LOD_HIGH)[0],
            "mid":  struct.unpack_from("<f", data, rec + OFF_LOD_MID)[0],
            "low":  struct.unpack_from("<f", data, rec + OFF_LOD_LOW)[0],
        },
        "flags": flags,
        "skip_decal": bool(flags[1] & 0x2),
        "effect_link":      _datid(data, rec + OFF_EFFECT),
        "culling_table_link": struct.unpack_from("<I", data, rec + OFF_CULL)[0] or None,
        "environment_link": _datid(data, rec + OFF_ENV),
        "file_id_link":     struct.unpack_from("<I", data, rec + OFF_FILE)[0] or None,
        "point_lights":     [v - 1 for v in plights_raw if v > 0],
        "tags": [],
    }


def list_objects(dat_path: Path, with_footprint: bool = True,
                 object_max_footprint: float = DEFAULT_OBJECT_FOOTPRINT) -> list[dict]:
    """Decrypt the 0x1C section and return every placement as a rich dict.

    When ``with_footprint`` is set, each record also carries ``mesh_dims`` (the local
    [dx, dy, dz] of its 0x2E mesh), ``footprint`` (max horizontal extent), ``instances``
    (how many placements share that mesh), and an auto ``"object"`` tag when the mesh
    footprint is <= ``object_max_footprint``.
    """
    dll = Path(FFXI_DIR) / "FFXiMain.dll"
    if not dll.exists():
        raise FileNotFoundError(f"FFXiMain.dll not found: {dll}")
    table1, table2 = load_key_tables(dll)

    data = bytearray(read_path_for(dat_path).read_bytes())

    bboxes: dict = {}
    if with_footprint:
        # _mesh_bboxes decrypts each 0x2E in place and re-encrypts it, leaving data net
        # unchanged; it touches 0x2E only, so it's independent of the 0x1C decrypt below.
        from xi.zone.xi_apply_changes import _mesh_bboxes
        bboxes = _mesh_bboxes(data, table1, table2)

    sections = parse_sections(data)
    zonedef = next((s for s in sections if s.type_code == SECTION_TYPE_ZONE_DEF), None)
    if zonedef is None:
        raise ValueError("DAT has no 0x1C ZoneDef (placement) section")

    node_count = decrypt_zone_objects(data, zonedef.data_start, zonedef.start, zonedef.size, table1)

    records = []
    for i in range(node_count):
        rec = _read_record(data, zonedef.data_start, i)
        if not rec["name"]:
            continue  # blanked / deleted record
        records.append(rec)

    if with_footprint:
        inst = Counter(r["name"] for r in records)
        for r in records:
            r["instances"] = inst[r["name"]]
            b = bboxes.get(r["name"])
            if b is None:
                r["mesh_dims"] = None
                r["footprint"] = None
                continue
            dx, dy, dz = b[1] - b[0], b[3] - b[2], b[5] - b[4]
            r["mesh_dims"] = [dx, dy, dz]
            r["footprint"] = max(dx, dz)
            if r["footprint"] <= object_max_footprint:
                r["tags"].append("object")

    return records


def _rel_dat(resolved: Path) -> str:
    """Game-relative DAT path (e.g. ROM/1/41.DAT) for the JSON payload, or bare name.

    Forward slashes regardless of OS so the JSON is portable."""
    try:
        return resolved.relative_to(FFXI_DIR).as_posix()
    except ValueError:
        return resolved.name


def build_payload(resolved: Path, entries: list[dict], max_footprint: float) -> dict:
    """The JSON document shape shared by `zone object list --json` and the batch dump."""
    return {
        "dat": _rel_dat(resolved),
        "max_footprint": max_footprint,
        "count": len(entries),
        "objects": entries,
    }


@click.command("list")
@click.argument("dat_path")
@click.option("--filter", "-f", "name_filter", default=None,
              help="Case-insensitive substring filter on mesh name.")
@click.option("--pos", "show_pos", is_flag=True,
              help="Also print position/rotation/scale for each placement.")
@click.option("--max-footprint", type=float, default=DEFAULT_OBJECT_FOOTPRINT, show_default=True,
              help="Mesh footprint (yalms) at/under which a placement is tagged 'object'.")
@click.option("--objects-only", is_flag=True,
              help="Only show placements auto-tagged 'object' (small props, not walls).")
@click.option("--json", "as_json", is_flag=True,
              help="Emit the full records (LOD, flags, links, footprint, tags) as JSON.")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None,
              help="Write output to a file instead of stdout.")
def list_cmd(dat_path, name_filter, show_pos, max_footprint, objects_only, as_json, output):
    """List every placement (object) in a zone DAT.

    Each placement is auto-tagged ``object`` when the mesh it references is physically
    small (footprint <= --max-footprint), separating decorative props from walls /
    buildings / terrain (which carry no explicit type flag in the DAT).

    Examples:

    \b
      xi zone object list ROM/1/41
      xi zone object list ROM/1/41 --objects-only
      xi zone object list ROM/1/41 --json -o jeuno_objects.json
    """
    try:
        resolved = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    try:
        entries = list_objects(resolved, object_max_footprint=max_footprint)
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e))

    if name_filter:
        flt = name_filter.lower()
        entries = [e for e in entries if flt in e["name"].lower()]
    if objects_only:
        entries = [e for e in entries if "object" in e["tags"]]

    if as_json:
        payload = build_payload(resolved, entries, max_footprint)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8")
            tagged = sum(1 for e in entries if "object" in e["tags"])
            click.echo(f"Wrote {len(entries)} placement(s) ({tagged} tagged 'object') -> {output}")
        else:
            click.echo(text)
        return

    if not entries:
        click.echo("No placements found.")
        return

    lines = []
    for e in entries:
        tag = " [object]" if "object" in e["tags"] else ""
        fp = e.get("footprint")
        fp_s = f"  foot={fp:.1f}" if fp is not None else "  foot=?"
        if show_pos:
            p, r, s = e["pos"], e["rot"], e["scale"]
            lines.append(f"  [{e['index']:4d}] {e['name']:<24}{fp_s}{tag}  "
                         f"pos=({p[0]:.1f},{p[1]:.1f},{p[2]:.1f})  "
                         f"rot=({r[0]:.2f},{r[1]:.2f},{r[2]:.2f})  "
                         f"scale=({s[0]:.2f},{s[1]:.2f},{s[2]:.2f})")
        else:
            lines.append(f"  [{e['index']:4d}] {e['name']:<24}{fp_s}{tag}")
    tagged = sum(1 for e in entries if "object" in e["tags"])
    lines.append(f"\n{len(entries)} placement(s), {tagged} tagged 'object'.")
    text = "\n".join(lines)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        click.echo(f"Wrote {len(entries)} placement(s) -> {output}")
    else:
        click.echo(text)

#!/usr/bin/env python3
"""Add new object placements to a zone DAT (Phase 4).

Duplicates an existing placement of a mesh that's already in the zone, at a new
world position — the safe form of "add an object" because the mesh (0x2E) and a
template record already exist. The new record is appended to the 0x1C object
array, every internal file offset is relocated, and the object is registered in
the space-tree leaf that renders its source (see xi.zone.xi_zonedef), then the
section is re-encrypted and the DAT rebuilt.

The new index is registered across all four 0x1C visibility structures (space-tree
leaf, mesh-bbox leaf bounds, per-object collision transform, and culling-table/PVS
membership), so the clone renders from every camera position — same as import-object.
See docs/zone/object/import.md.

Limitations (v1):
  * The mesh must already exist in the zone (we copy an existing placement of it).
    Brand-new meshes need xi zone object import.
  * No collision *mesh* is added — the object is visual only (you can walk through it).
    Adding real collision is future work.
  * Place new objects reasonably near an existing instance of the mesh (the source's
    template flags/links are copied).
"""

import struct
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from xi.xi_config import FFXI_DIR, editable_dat
from xi.entity.anim.xi_export import parse_sections
from xi.entity.mesh.xi_export import resolve_dat_path
from xi.zone.xi_decrypt import decrypt_zone_objects, load_key_tables, reencrypt_zone_objects
from xi.zone.xi_zonedef import (SECTION_TYPE_ZONE_DEF, add_placements, parse_zonedef,
                                  zonedef_record_size)


def _record_id(sec: bytes, ds: int, index: int, record_size: int | None = None) -> str:
    if record_size is None:
        n = struct.unpack_from("<I", sec, ds + 4)[0] & 0x00FFFFFF
        record_size = zonedef_record_size(sec, ds, n)
    base = ds + 0x20 + index * record_size
    return sec[base : base + 0x10].split(b"\x00", 1)[0].decode("ascii", "replace").strip()


def add_objects(dat_path: Path, additions: Sequence[Tuple[str, Tuple[float, float, float]]]) -> Tuple[Path, int, int]:
    """Append ``additions`` (each ``(mesh_id, (x,y,z))`` in raw FFXI coords) to a
    zone. Returns (out_path, added, new_total)."""
    # Layer onto prior edits (fresh=False), like import-object / swap-placement /
    # apply-changes — so a sequence of edit commands composes instead of each clone
    # resetting the DAT to pristine. Re-running stacks copies (use `xi zone reset` to
    # start over), which matches every other additive zone command.
    target = editable_dat(dat_path, fresh=False)

    table1, _table2 = load_key_tables(Path(FFXI_DIR) / "FFXiMain.dll")
    data = bytearray(target.read_bytes())
    sections = parse_sections(data)
    z = next((s for s in sections if s.type_code == SECTION_TYPE_ZONE_DEF), None)
    if z is None:
        raise ValueError("DAT has no 0x1C ZoneDef (placement) section")

    sec = bytearray(data[z.start : z.start + z.size])
    decrypt_zone_objects(sec, 0x10, 0, len(sec), table1)
    zd = parse_zonedef(sec, 0x10, 0, len(sec))

    # map mesh id -> first existing placement index (template to copy)
    id_to_index = {}
    for i in range(zd.node_count):
        rid = _record_id(sec, 0x10, i)
        id_to_index.setdefault(rid, i)

    new_objs: List[Tuple[int, Tuple[float, float, float], Optional[str]]] = []
    src_by_k: List[int] = []
    mesh_by_k: List[str] = []
    for mesh_id, pos in additions:
        src = id_to_index.get(mesh_id)
        if src is None:
            raise ValueError(
                f"No existing placement of mesh '{mesh_id}' in this zone to copy. "
                f"The mesh must already exist in the zone (brand-new meshes are not supported yet)."
            )
        # keep the template's rotation/scale (None), just reposition + set id
        new_objs.append((src, (float(pos[0]), float(pos[1]), float(pos[2])), None, None, mesh_id))
        src_by_k.append(src)
        mesh_by_k.append(mesh_id)

    base_index = zd.node_count
    new_sec = add_placements(sec, new_objs)

    # Full visibility registration — the same four 0x1C structures import-object touches
    # (space-tree leaf is done by add_placements; here: leaf→mesh-bbox bounds, per-object
    # collision transform, and culling-table/PVS membership). Without #3/#4 a cloned object
    # vanishes depending on camera position. See docs/zone/object/import.md.
    from xi.zone.xi_zonedef import (add_collision_transforms, add_to_culling_tables,
                                      expand_placement_bounds_points)
    from xi.zone.xi_apply_changes import _mesh_bboxes, _bbox_points
    from xi.zone.xi_export import trs_matrix

    bboxes = _mesh_bboxes(bytearray(data), table1, _table2)

    def _rec_trs(buf: bytes, idx: int):
        b = 0x10 + 0x20 + idx * parse_zonedef(bytearray(buf), 0x10, 0, len(buf)).record_size
        return (struct.unpack_from("<3f", buf, b + 0x10),
                struct.unpack_from("<3f", buf, b + 0x1C),
                struct.unpack_from("<3f", buf, b + 0x28))

    zd_new = parse_zonedef(new_sec, 0x10, 0, len(new_sec))
    xform_entries = []
    for k in range(len(new_objs)):
        idx = base_index + k
        p2, r2, s2 = _rec_trs(new_sec, idx)              # template rot/scale were kept
        if mesh_by_k[k] in bboxes:
            expand_placement_bounds_points(new_sec, zd_new, idx, _bbox_points(bboxes[mesh_by_k[k]], p2, r2, s2))
        xform_entries.append((src_by_k[k], trs_matrix(p2, r2, s2)))
    new_sec = add_collision_transforms(new_sec, xform_entries)
    for k in range(len(new_objs)):
        new_sec = add_to_culling_tables(new_sec, base_index + k)

    reencrypt_zone_objects(new_sec, 0x10, 0, len(new_sec), table1)

    new_data = bytes(data[: z.start]) + bytes(new_sec) + bytes(data[z.start + z.size :])
    target.write_bytes(new_data)
    return target, len(new_objs), zd.node_count + len(new_objs)


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------

import click as _click  # noqa: E402


@_click.command("clone")
@_click.argument("dat_path")
@_click.argument("mesh_id")
@_click.option("--pos", nargs=3, type=float, required=True, metavar="X Y Z",
               help="World position (raw FFXI coords) for the new object")
@_click.option("--count", type=int, default=1, show_default=True,
               help="Add this many copies (all at --pos; nudge later via zone import)")
def cmd(dat_path: str, mesh_id: str, pos, count: int):
    """Add a new placement of an existing zone mesh at a world position.

    DAT_PATH may be a ROM-relative spec like ROM/1/41. MESH_ID must be a mesh that
    already has at least one placement in the zone (its record is copied as a
    template). The new object is appended and registered across all four 0x1C
    visibility structures (space-tree, bounds, collision transform, culling tables)
    so it renders from every camera position.

    Layers onto prior edits (writes the DAT in place, .base backup kept);
    re-running stacks another copy — use `xi zone reset` to start from pristine.
    """
    try:
        resolved = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise _click.ClickException(str(e))
    additions = [(mesh_id, (pos[0], pos[1], pos[2]))] * max(1, count)
    try:
        out_path, added, total = add_objects(resolved, additions)
    except ValueError as e:
        raise _click.ClickException(str(e))
    _click.echo(f"Wrote DAT:      {out_path}")
    _click.echo(f"Added {added} '{mesh_id}' placement(s) at {tuple(pos)} -> {total} objects total")
    _click.echo("Visual only (no collision). Test in AltanaView, then in-game.")

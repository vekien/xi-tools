"""``xi zone check``: does a zone model DAT hold together the way the client needs it to?

Reads a zone's model DAT (or every zone of the install) and checks the structure the
client walks on zone-in without bounds checks of its own: the section list, the 0x1C
ZoneDef (placements, culling tables, space tree) and its collision block (meshes,
transforms, groups, the grid map), plus the limits found by crashing the client with
generated zones. It writes nothing.

    xi zone check ROM/0/124
    xi zone check ROM10/1/8.DAT --base 154
    xi zone check --all

A *problem* is something the client is known or expected to fail on (a pointer to
nowhere, an index past its table, a limit passed): the exit status is 1 when any DAT has
one. A *note* is something unusual that retail zones also do (they load), reported so a
generated zone can be compared. Every retail zone of a legacy client checks clean.

The checks and the limits are ported from Loxley's LPS-modules zone writer
(``zonekit/writer.py``, ``check_zone``), with his permission, onto xi-tools' own readers
(xi.zone.xi_zonedef, xi.zone.xi_collision, xi.zone.xi_decrypt). The limits, measured with
generated zones on a September 2026 client:

- **Collision groups** (the (transform, mesh) lists the grid cells point at; one per cell
  in use) are counted, not limited. LPS stopped at 60,000 after a generated zone with
  79,373 crashed on zone-in and one with 62,909 loaded, guessing a 16-bit count; but
  retail zones load with far more (Western Altepa Desert 124,103, Abyssea - Altepa 123,892,
  ten zones over 60,000), so that crash had another cause. More than retail's most is a note.
- **The block grid must not be smaller than the base zone's** (``--base``). The client
  drops every entity the base zone's event and NPC files place into the collision grid by
  position, unchecked; one outside it crashed the client (ffximain+0x16663B).
- **The ZoneDef section must fit 8,388,592 bytes** (the 19-bit section size; the client
  copies it into a fixed buffer). 8,372,848 loaded.
- **The space tree is the zone's block grid**, leaves all at one depth: a flat tree with
  1,330 objects in one leaf read past a buffer on zone-in, an adaptive one with 85-yalm
  leaves wrote past one. The largest leaf and the leaf depths are reported.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import click

from xi.common.xi_section import MAX_SECTION_BYTES

RETAIL_MOST_COLLISION_GROUPS = 124_103    # Western Altepa Desert, legacy client
RETAIL_LARGEST_LEAF = 328                 # objects in one space-tree leaf: Ceizak Battlegrounds

SECTION_DIR = 0x01
SECTION_END = 0x00
SECTION_ZONEDEF = 0x1C
SECTION_TEXTURE = 0x20
SECTION_ZONE_MESH = 0x2E

NODE_SIZE = 0x80
TRANSFORM_SIZE = 0xC0
OLD_TRANSFORM_SIZE = 0x70


class CheckError(ValueError):
    pass


def _u32(b, p: int) -> int:
    return struct.unpack_from("<I", b, p)[0]


def _box(buf, pos: int):
    """A space-tree node's bounding box from its 8 corner vec3s: (lo, hi)."""
    pts = [struct.unpack_from("<3f", buf, pos + i * 12) for i in range(8)]
    return tuple(min(p[k] for p in pts) for k in range(3)), tuple(max(p[k] for p in pts) for k in range(3))


def _inside(inner, outer, eps: float = 1e-3) -> bool:
    return all(inner[0][k] >= outer[0][k] - eps and inner[1][k] <= outer[1][k] + eps for k in range(3))


def check_zone(data: bytes, table1: bytes, table2: bytes, base_blocks=None) -> dict:
    """``{"problems": [...], "notes": [...], "info": {...}}`` for a zone model DAT's bytes.
    ``table1`` / ``table2``: the zone key tables (xi.zone.xi_decrypt.load_key_tables).
    ``base_blocks``: the base zone's ZoneDef block grid ``(x blocks, z blocks, block width,
    block length)``, to check this zone's grid isn't smaller."""
    from xi.entity.anim.xi_export import parse_sections
    from xi.zone.xi_decrypt import decrypt_zone_mesh
    from xi.zone.xi_export import parse_zone_mesh_section

    problems: list[str] = []
    notes: list[str] = []
    info: dict = {"bytes": len(data)}

    # ── sections ──────────────────────────────────────────────────────────────
    sections = parse_sections(data)
    end = sections[-1].start + sections[-1].size if sections else 0
    if end != len(data):
        problems.append(f"the sections end at {end:#x} but the file is {len(data):#x} bytes "
                        "(a zero-size section, or bytes after the last one)")
    depth = 0
    for s in sections:
        if s.type_code == SECTION_DIR:
            depth += 1
        elif s.type_code == SECTION_END:
            depth -= 1
            if depth < 0:
                notes.append(f"a directory end at {s.start:#x} without a start")
                depth = 0
    if depth:
        notes.append(f"{depth} director{'y is' if depth == 1 else 'ies are'} never ended")
    info["sections"] = len(sections)
    zds = [s for s in sections if s.type_code == SECTION_ZONEDEF]
    if not zds:
        problems.append("no ZoneDef (0x1C) section: not a zone model")
        return {"problems": problems, "notes": notes, "info": info}
    if len(zds) > 1:
        notes.append(f"{len(zds)} ZoneDefs (retail ships carry two); each is checked")
    buf = bytearray(data)
    meshes: dict = {}
    for s in sections:
        if s.type_code == SECTION_ZONE_MESH:
            decrypt_zone_mesh(buf, s.data_start, table1, table2)
            try:
                name, prims = parse_zone_mesh_section(buf, s)
            except (struct.error, IndexError, ValueError):
                problems.append(f"the mesh section at {s.start:#x} doesn't parse")
                continue
            if name:
                meshes.setdefault(name, prims)
    info["meshes"] = len(meshes)
    textures = {bytes(buf[s.data_start + 1:s.data_start + 0x11]).split(b"\0", 1)[0].decode("ascii", "replace").strip()
                for s in sections if s.type_code == SECTION_TEXTURE}
    info["textures"] = len(textures)
    blocks = None
    for k, zs in enumerate(zds):
        p_, n_, i_ = [], [], {}
        grid = _check_zonedef(buf, zs, table1, meshes, p_, n_, i_)
        blocks = grid if blocks is None else blocks
        tag = f"ZoneDef {k + 1}: " if len(zds) > 1 else ""
        problems += [tag + x for x in p_]
        notes += [tag + x for x in n_]
        if k == 0:
            info.update(i_)
        else:
            info.setdefault("more_zonedefs", []).append({"bytes": len(data), **i_})

    if base_blocks is not None:
        bx, bz, bw, bl = base_blocks
        x, z, w, l = blocks
        if x * w < bx * bw or z * l < bz * bl:
            problems.append(f"the block grid ({x}x{z} blocks of {w}x{l} yalms) is smaller than the base "
                            f"zone's ({bx}x{bz} of {bw}x{bl}): entities the base zone places outside it "
                            "crash the client (ffximain+0x16663B)")
    return {"problems": problems, "notes": notes, "info": info}


def _check_zonedef(buf, zs, table1, meshes, problems, notes, info) -> tuple:
    """Check one 0x1C section (decrypting it in ``buf``); returns its block grid."""
    from xi.zone.xi_decrypt import decrypt_zone_objects
    from xi.zone.xi_export import resolve_mesh_name
    from xi.zone.xi_zonedef import OBJ_CULLING_LINK, OBJ_RECORD_SIZE, zonedef_record_size

    info["zonedef_bytes"] = zs.size
    info["zonedef_headroom"] = MAX_SECTION_BYTES - zs.size

    n = decrypt_zone_objects(buf, zs.data_start, zs.start, zs.size, table1)
    ds = zs.data_start
    end_rel = zs.size - 0x10
    rs = zonedef_record_size(buf, ds, n)
    blocks = tuple(buf[ds + 0xC:ds + 0x10])
    info.update(objects=n, record_size=rs, blocks=list(blocks))
    if 0x20 + n * rs > end_rel:
        problems.append(f"{n} objects of {rs:#x} bytes run past the ZoneDef")
        return blocks

    def inside(rel: int, length: int = 4) -> bool:
        return 0 < rel and rel + length <= end_rel

    # ── placements and their meshes ───────────────────────────────────────────
    names = [buf[ds + 0x20 + i * rs:ds + 0x30 + i * rs].split(b"\0", 1)[0].decode("ascii", "replace").strip()
             for i in range(n)]
    unresolved = sorted({nm for nm in names if nm and resolve_mesh_name(nm, meshes) is None})
    if unresolved:
        notes.append(f"{len(unresolved)} placed mesh name(s) with no 0x2E mesh in the file "
                     f"({', '.join(unresolved[:5])}{', …' if len(unresolved) > 5 else ''})")
    # ── culling tables ────────────────────────────────────────────────────────
    cull_rel = _u32(buf, ds + 0x14)
    table_at: set = set()
    if cull_rel:
        if not inside(cull_rel):
            problems.append(f"the culling tables' offset {cull_rel:#x} is outside the ZoneDef")
        else:
            p = cull_rel
            ntab = _u32(buf, ds + p)
            p += 4
            for t in range(ntab):
                if not inside(p):
                    problems.append(f"culling table {t} of {ntab} runs past the ZoneDef")
                    break
                cnt = _u32(buf, ds + p)
                table_at.add(p)
                if not inside(p + 4, 4 * cnt) and cnt:
                    problems.append(f"culling table {t} ({cnt} entries) runs past the ZoneDef")
                    break
                bad = [m for m in struct.unpack_from(f"<{cnt}I", buf, ds + p + 4) if m >= n] if cnt else []
                if bad:
                    problems.append(f"culling table {t} lists object {bad[0]}, past the {n} objects")
                p += 4 + 4 * cnt
            info["culling_tables"] = ntab
    if rs >= OBJ_RECORD_SIZE:
        dangling = [i for i in range(n)
                    if (link := _u32(buf, ds + 0x20 + i * rs + OBJ_CULLING_LINK)) and link not in table_at]
        if dangling:
            problems.append(f"{len(dangling)} object(s) link to no culling table (first: object {dangling[0]})")

    # ── space tree ────────────────────────────────────────────────────────────
    root = _u32(buf, ds + 0x10)
    in_leaf: set = set()
    leaf_sizes: list = []
    leaf_depths: set = set()
    if not root:
        problems.append("no space tree: the client collects what it draws from the tree's leaves")
    elif not inside(root, NODE_SIZE):
        problems.append(f"the space tree's root {root:#x} is outside the ZoneDef")
    else:
        seen: set = set()
        outside_parent = 0
        stack = [(root, 0, None)]
        while stack:
            rel, d, parent_box = stack.pop()
            if rel in seen:
                problems.append(f"space-tree node {rel:#x} is reached twice")
                continue
            seen.add(rel)
            pos = ds + rel
            box = _box(buf, pos)
            if parent_box is not None and not _inside(box, parent_box):
                outside_parent += 1
            idx_ref, cnt = _u32(buf, pos + 0x60), _u32(buf, pos + 0x64)
            if idx_ref and cnt:
                if not inside(idx_ref, 4 * cnt):
                    notes.append(f"space-tree node {rel:#x} lists {cnt} objects past the ZoneDef "
                                 "(the client skips it, as retail's blank-zone template has)")
                else:
                    members = struct.unpack_from(f"<{cnt}I", buf, ds + idx_ref)
                    bad = [m for m in members if m >= n]
                    if bad:
                        problems.append(f"space-tree leaf {rel:#x} lists object {bad[0]}, past the {n} objects")
                    in_leaf.update(members)
                    leaf_sizes.append(cnt)
                    leaf_depths.add(d)
            for c in struct.unpack_from("<4I", buf, pos + 0x68):
                if not c:
                    continue
                if not inside(c, NODE_SIZE):
                    problems.append(f"space-tree node {rel:#x} has a child at {c:#x}, outside the ZoneDef")
                    continue
                stack.append((c, d + 1, box))
        if len(leaf_depths) > 1:
            notes.append(f"leaves at depths {sorted(leaf_depths)}: every retail zone's are at one depth "
                         "(an adaptive tree with 85-yalm leaves crashed the client)")
        if max(leaf_sizes, default=0) > RETAIL_LARGEST_LEAF:
            notes.append(f"a leaf holds {max(leaf_sizes)} objects, more than retail's most ({RETAIL_LARGEST_LEAF}, "
                         "Ceizak Battlegrounds; 1,330 in one leaf crashed the client)")
        if outside_parent:
            notes.append(f"{outside_parent} space-tree node(s) reach outside their parent's box (retail's do too)")
        info["tree_nodes"] = len(seen)
        info["leaves"] = len(leaf_sizes)
        info["largest_leaf"] = max(leaf_sizes, default=0)
        info["leaf_depths"] = sorted(leaf_depths)
    drawn = [i for i in range(n) if i not in in_leaf]
    if root and drawn:
        notes.append(f"{len(drawn)} object(s) in no space-tree leaf (the client never draws them)")

    # ── collision ─────────────────────────────────────────────────────────────
    coll_rel = _u32(buf, ds + 0x08)
    if coll_rel:
        if not inside(coll_rel, 0x20):
            problems.append(f"the collision block's offset {coll_rel:#x} is outside the ZoneDef")
        else:
            _check_collision(buf, ds, coll_rel, end_rel, n, blocks, problems, notes, info)
    else:
        notes.append("no collision (a ship or a cutscene stage has none)")

    return blocks


def _check_collision(buf, ds, coll_rel, end_rel, n, blocks, problems, notes, info) -> None:
    from xi.zone.xi_collision import parse_collision_raw
    cb = ds + coll_rel
    num_meshes, first_mesh, pair_count, pairs_off, map_off, tf_off, idx_count = struct.unpack_from("<7I", buf, cb)
    for name, off in (("first mesh", first_mesh), ("groups", pairs_off), ("grid map", map_off),
                      ("transforms", tf_off)):
        if not (0 < off <= end_rel):
            problems.append(f"the collision {name} offset {off:#x} is outside the ZoneDef")
            return
    if idx_count != n:
        problems.append(f"the collision block counts {idx_count} objects, the ZoneDef {n}")
    span = pairs_off - tf_off
    stride = TRANSFORM_SIZE
    if n and span == n * OLD_TRANSFORM_SIZE:
        # ROM/0/28 (zone 0: a test / title-screen zone, never entered) keeps 0x70-byte
        # transforms, one per object.
        stride = OLD_TRANSFORM_SIZE
        notes.append(f"{OLD_TRANSFORM_SIZE:#x}-byte collision transforms (an older layout; retail's zone 0, a test zone, has it)")
    elif span < 0 or span % TRANSFORM_SIZE:
        problems.append(f"the collision transforms ({span} bytes) aren't whole {TRANSFORM_SIZE:#x}-byte records")
    elif span // TRANSFORM_SIZE != n:
        notes.append(f"{span // TRANSFORM_SIZE} collision transforms for {n} objects (retail Lower Jeuno "
                     "has one spare)")
    try:
        raw = parse_collision_raw(buf, ds, end_rel)
    except (struct.error, IndexError):
        problems.append("the collision meshes run past the ZoneDef")
        return
    mesh_at = {m.old_rel: m for m in raw.meshes}
    last = raw.meshes[-1] if raw.meshes else None
    if last is not None:
        mesh_end = _u32(buf, ds + last.old_rel + 8) + 8 * len(last.tris)
        if mesh_end != tf_off:
            notes.append(f"the collision meshes end at {mesh_end:#x}, the transforms start at {tf_off:#x}")
    bad_idx = 0
    for m in raw.meshes:
        nv, nn = len(m.verts), len(m.norms)
        for a, b, c, d in m.tris:
            # the index masks xi.zone.xi_collision reads them with (the high bits are flags)
            if (a & 0x7FFF) >= nv or (b & 0x3FFF) >= nv or (c & 0x3FFF) >= nv or (d & 0x7FFF) >= nn:
                bad_idx += 1
    if bad_idx:
        problems.append(f"{bad_idx} collision triangle(s) index past their mesh's vertices or normals")
    group_at = {g.old_rel for g in raw.groups}
    groups_end = (raw.groups[-1].old_rel + 8 + 8 * len(raw.groups[-1].pairs)) if raw.groups else pairs_off
    if groups_end != map_off:
        problems.append(f"the collision groups end at {groups_end:#x}, the grid map starts at {map_off:#x}")
    bad_tf = bad_mesh = 0
    for g in raw.groups:
        for tf, mo in g.pairs:
            if not (tf_off <= tf < pairs_off) or (tf - tf_off) % stride:
                bad_tf += 1
            if mo not in mesh_at:
                bad_mesh += 1
    if bad_tf:
        problems.append(f"{bad_tf} collision group pair(s) point at no transform")
    if bad_mesh:
        problems.append(f"{bad_mesh} collision group pair(s) point at no collision mesh")
    zbx, zbz, bw, bl = blocks
    cells = (zbx * (bw // 4)) * (zbz * (bl // 4))
    used = [c for c in raw.map_u32[:cells] if c]
    if len(raw.map_u32) < cells:
        problems.append(f"the grid map holds {len(raw.map_u32)} cells; the block grid needs {cells}")
    outside = sum(1 for c in used if c >= end_rel)
    dangling = sum(1 for c in used if c < end_rel and c not in group_at)
    if outside:
        notes.append(f"{outside} grid cell(s) at the map's edge hold {0xFFFFFFFF:#x}-like values "
                     "pointing outside the ZoneDef (retail does this; the client never visits them)")
    if dangling:
        problems.append(f"{dangling} grid cell(s) point inside the ZoneDef but at no collision group")
    if len(raw.groups) > RETAIL_MOST_COLLISION_GROUPS:
        notes.append(f"{len(raw.groups):,} collision groups: more than any retail zone "
                     f"({RETAIL_MOST_COLLISION_GROUPS:,}, Western Altepa Desert)")
    info.update(collision_meshes=len(raw.meshes), collision_groups=len(raw.groups),
                collision_cells=len(used))


# ── CLI ──────────────────────────────────────────────────────────────────────

def _zone_dats(targets, all_zones: bool) -> list[tuple[str, Path]]:
    import xi.xi_config as cfg
    from xi.entity.mesh.xi_export import resolve_dat_path
    from xi.ftable.xi_core import load_all_tables, scan_file_ids
    from xi.zone.xi_list import get_zone_entries, zone_file_id
    out = []
    if all_zones:
        for z in get_zone_entries(path_prefix=""):
            out.append((f"{z['id']} {z['name']}", Path(cfg.FFXI_DIR) / z["path"]))
    for t in targets:
        if str(t).isdigit():
            hits = scan_file_ids([zone_file_id(int(t))], load_all_tables())
            if not hits:
                raise click.ClickException(f"zone {t} has no model DAT in this install")
            out.append((f"zone {t}", Path(cfg.FFXI_DIR) / hits[0]["dat"]))
        else:
            try:
                out.append((str(t), resolve_dat_path(str(t))))
            except (FileNotFoundError, ValueError) as e:
                raise click.ClickException(str(e))
    return out


def _blocks_of(path: Path, table1: bytes) -> tuple:
    from xi.entity.anim.xi_export import parse_sections
    from xi.zone.xi_decrypt import decrypt_zone_objects
    data = bytearray(path.read_bytes())
    zs = next((s for s in parse_sections(data) if s.type_code == SECTION_ZONEDEF), None)
    if zs is None:
        raise click.ClickException(f"{path} has no ZoneDef")
    decrypt_zone_objects(data, zs.data_start, zs.start, zs.size, table1)
    return tuple(data[zs.data_start + 0xC:zs.data_start + 0x10])


@click.command("check")
@click.argument("targets", nargs=-1, metavar="[DAT|ZONE_ID]...")
@click.option("--all", "all_zones", is_flag=True, help="Check every zone of the install.")
@click.option("--base", default=None, metavar="DAT|ZONE_ID",
              help="The zone this one was built on: its block grid must not be bigger than this one's.")
@click.option("--json", "as_json", is_flag=True, help="Print the findings as JSON.")
@click.option("--notes/--no-notes", default=True, help="Show the notes (unusual, but retail does it too).")
def cmd(targets, all_zones, base, as_json, notes):
    """Check a zone model DAT's structure and the client's limits (writes nothing).

    Problems are what the client fails on (exit status 1); notes are unusual things
    retail zones also do. Every retail zone checks clean.

    \b
    Examples:
      xi zone check ROM/0/124
      xi zone check ROM10/1/8.DAT --base 154
      xi zone check --all --no-notes
    """
    import xi.xi_config as cfg
    from xi.zone.xi_decrypt import load_key_tables
    if not targets and not all_zones:
        raise click.UsageError("give a zone DAT or id, or --all")
    dll = Path(cfg.require_ffxi_dir()) / "FFXiMain.dll"
    if not dll.is_file():
        raise click.ClickException(f"FFXiMain.dll not found at {dll} (the zone key tables are in it)")
    table1, table2 = load_key_tables(dll)
    base_blocks = None
    if base:
        (_, bpath), = _zone_dats([base], False)
        base_blocks = _blocks_of(bpath, table1)
    results, failed = [], 0
    for label, path in _zone_dats(targets, all_zones):
        r = check_zone(path.read_bytes(), table1, table2, base_blocks)
        r.update(target=label, dat=str(path))
        results.append(r)
        failed += bool(r["problems"])
        if as_json:
            continue
        mark = click.style("✗", fg="red") if r["problems"] else click.style("✓", fg="green")
        i = r["info"]
        click.echo(f"{mark} {label}: {i.get('objects', '?')} objects, ZoneDef {i.get('zonedef_bytes', 0):,} bytes, "
                   f"{i.get('collision_cells', 0):,} collision cells, largest leaf {i.get('largest_leaf', 0)}")
        for p in r["problems"]:
            click.echo(click.style(f"    problem: {p}", fg="red"))
        if notes:
            for n_ in r["notes"]:
                click.echo(click.style(f"    note: {n_}", fg="yellow"))
    if as_json:
        click.echo(json.dumps({"schema": "xi.zone-check.v1", "zones": results}, indent=2))
    elif len(results) > 1:
        click.echo(f"{len(results)} zone(s), {failed} with problems")
    if failed:
        raise SystemExit(1)

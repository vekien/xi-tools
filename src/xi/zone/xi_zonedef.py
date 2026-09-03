#!/usr/bin/env python3
"""Structured parse of a decrypted ``0x1C`` ZoneDef section, sufficient to *grow*
it (add object placements) and fix up every internal file offset.

The section is laid out (all offsets are relative to ``data_start`` = section+0x10):

    0x00  header (0x20 bytes)
    0x20  objects[nodeCount]   (0x64 each)
          culling tables
          space-partitioning tree (+ leaf index lists)
          point lights
          collision section     (meshes, transforms, mesh/transform pairs, grid map)

Every cross-reference inside the section is a ``data_start``-relative u32 offset.
To append objects we must (a) splice the new record(s) in at the end of the
object array and (b) add the new object index to a space-tree leaf so the engine
renders it (``Culler`` only collects objects from leaf nodes). Both shift later
bytes, so every stored offset must be relocated.

``collect_offset_fields`` walks the whole structure and returns the absolute file
position of *every* u32 that holds such an offset. Because the object array is the
first thing after the header, appending objects shifts the entire remainder by a
single uniform delta ``D`` — so relocation is just "add D to every offset field".
Missing even one field corrupts the section, so ``verify_relocation`` re-parses a
shifted copy and compares the logical structure to catch omissions.
"""

import struct
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from xi.common.xi_section import encode_section_meta

SECTION_TYPE_ZONE_DEF = 0x1C

OBJ_RECORD_SIZE = 0x64          # retail placement record
OBJ_RECORD_SIZE_PROTO = 0x54    # pre-production zones (ROM/0/29,31..42...): same
                                # name/pos/rot/scale fields packed tighter, and no
                                # fileIdLink at +0x50. See docs/zone/prototype-zones.md.
OBJ_ARRAY_START = 0x20          # objects begin at data_start + 0x20
OBJ_BLOCK_ID = 0x34             # u32 FourCC: groups the parts of an animated multi-part object (doors)
OBJ_DRAW_DISTANCE = 0x40        # float: engine stops drawing the object past this range
OBJ_CULLING_LINK = 0x48         # u32 offset to a culling table (or 0)
NODE_SIZE = 0x80                # space-tree node: 8*vec3 + idxRef + count + 4 children + 2 zero
NODE_IDXREF = 0x60
NODE_IDXCOUNT = 0x64
NODE_CHILDREN = 0x68            # 4 u32 child offsets
TRANSFORM_CULLGROUP = 0xA8      # cullingGroupOffset within a collision transform
TRANSFORM_SIZE = 0xC0           # collision transform record stride (contiguous array)


@dataclass
class SpaceNode:
    pos: int                    # absolute file position of the node
    rel: int                    # data_start-relative offset of the node
    idx_ref: int                # rel offset of the leaf index list (0 if internal)
    idx_count: int
    children_rel: List[int]     # rel offsets of up to 4 children (0 = none)
    contained: List[int]        # object indices (leaf only)


@dataclass
class ZoneDef:
    data_start: int
    section_start: int
    section_size: int
    payload_end_rel: int        # data_start-relative end of the decoded payload
    node_count: int
    blocks: Tuple[int, int, int, int]
    header_offsets: Dict[str, int]      # name -> rel value (collision/spacetree/culling/pointlight)
    offset_fields: List[int]            # absolute positions of every relocatable offset u32
    nodes: List[SpaceNode]
    root_rel: int
    record_size: int = OBJ_RECORD_SIZE   # 0x64 retail / 0x54 proto (zonedef_record_size)


def _u32(buf, pos) -> int:
    return struct.unpack_from("<I", buf, pos)[0]


def record_block_id(sec, index: int, ds: int = 0x10, record_size: int = OBJ_RECORD_SIZE) -> int:
    """The placement's BlockID FourCC @+0x34 (0 for an ordinary static object)."""
    return _u32(sec, ds + OBJ_ARRAY_START + index * record_size + OBJ_BLOCK_ID)


def clear_block_id(sec: bytearray, index: int, ds: int = 0x10, record_size: int = OBJ_RECORD_SIZE) -> None:
    """Zero a placement's BlockID FourCC @+0x34 so the client renders it as an ordinary
    static object.

    A non-zero BlockID whose first byte is ``_`` or ``@`` marks one PART of an animated
    multi-part object (a double door's two halves share e.g. ``_720``). The client
    (``ZoneRenderer::SetRenderTypes`` / ``ZoneLayoutData::InitUnderscoreAtStructs``) pulls
    every record with that FourCC OUT of the normal quad-tree render pass (RenderType 0)
    and draws them through an ``UnderscoreAtStruct`` that holds at most FOUR parts — any
    further record with the same FourCC is silently never drawn. A record cloned from such
    a part (record 0 of the mog houses is a door half) therefore inherits the door's group:
    the first two clones render (as door parts), the rest are invisible in-game while the
    editor, which ignores the field, shows them all. Every appended copy that is not
    deliberately a new part of that animated object must have this zeroed."""
    _pack32(sec, ds + OBJ_ARRAY_START + index * record_size + OBJ_BLOCK_ID, 0)



def zonedef_record_size(buf, data_start: int, node_count: int) -> int:
    """Placement record stride for this 0x1C section: 0x64 (retail) or 0x54 (proto).

    Reading a 0x54 zone at 0x64 misaligns every record after the first, and WRITING
    at the wrong stride shreds neighbouring records' names and scales — so this must
    be resolved before any placement access, not assumed.

    Mode (top byte of the u32 at data_start) is <= 5 on the pre-production zones, the
    same modes that skip 0x1C decryption. Where mode is ambiguous, score both strides
    by how many records yield a printable mesh id and take the clear winner.
    """
    mode = (_u32(buf, data_start) >> 24) & 0xFF
    if node_count < 2:
        return OBJ_RECORD_SIZE_PROTO if 0 < mode <= 5 else OBJ_RECORD_SIZE

    def _score(stride: int) -> int:
        good = 0
        for i in range(min(node_count, 32)):
            b = data_start + OBJ_ARRAY_START + i * stride
            if b + 0x10 > len(buf):
                break
            raw = bytes(buf[b:b + 0x10])
            end = raw.find(0)
            name = raw if end < 0 else raw[:end]
            if len(name) >= 2 and all(0x20 <= c <= 0x7E for c in name):
                good += 1
        return good

    s54, s64 = _score(OBJ_RECORD_SIZE_PROTO), _score(OBJ_RECORD_SIZE)
    if s64 > s54 + 2:
        return OBJ_RECORD_SIZE
    if s54 > s64 + 2:
        return OBJ_RECORD_SIZE_PROTO
    return OBJ_RECORD_SIZE_PROTO if 0 < mode <= 5 else OBJ_RECORD_SIZE


def parse_zonedef(buf: bytearray, data_start: int, section_start: int, section_size: int) -> ZoneDef:
    """Parse a *decrypted* 0x1C section. Returns structure + a full offset-field list."""
    ds = data_start
    payload_end_rel = section_size - 0x10
    node_count = _u32(buf, ds + 4) & 0x00FFFFFF
    record_size = zonedef_record_size(buf, ds, node_count)
    blocks = (buf[ds + 0xC], buf[ds + 0xD], buf[ds + 0xE], buf[ds + 0xF])

    collision_rel = _u32(buf, ds + 0x08)
    spacetree_rel = _u32(buf, ds + 0x10)
    culling_rel = _u32(buf, ds + 0x14)
    pointlight_rel = _u32(buf, ds + 0x18)
    header_offsets = {
        "collision": collision_rel, "spacetree": spacetree_rel,
        "culling": culling_rel, "pointlight": pointlight_rel,
    }

    offset_fields: List[int] = []
    # --- header: the 4 subsection offsets (always nonzero except collision on ships) ---
    for off in (0x08, 0x10, 0x14, 0x18):
        if _u32(buf, ds + off) != 0:
            offset_fields.append(ds + off)

    # --- objects: each record's cullingTableLink @ +0x48 (rel offset into culling) ---
    for i in range(node_count):
        rec = ds + OBJ_ARRAY_START + i * record_size
        if record_size >= OBJ_RECORD_SIZE and _u32(buf, rec + OBJ_CULLING_LINK) != 0:
            offset_fields.append(rec + OBJ_CULLING_LINK)

    # --- space-partitioning tree: walk from root, collect idxRef + 4 child offsets ---
    nodes: List[SpaceNode] = []
    seen_nodes: set = set()

    sect_end = ds + payload_end_rel

    def walk_node(rel: int) -> None:
        if rel in seen_nodes:
            return
        seen_nodes.add(rel)
        pos = ds + rel
        if pos + NODE_SIZE > sect_end:            # node header past section end — corrupt, skip
            return
        idx_ref = _u32(buf, pos + NODE_IDXREF)
        idx_count = _u32(buf, pos + NODE_IDXCOUNT)
        children_rel = list(struct.unpack_from("<4I", buf, pos + NODE_CHILDREN))
        # Treat as a leaf only when its index list lies wholly inside the section. The
        # blank-zone template (carved from Altar Room) carries a corrupt node whose
        # idx_ref points far past the section; the game client bounds-checks and ignores
        # it, so we must too — otherwise parse_zonedef (and every object add that relies
        # on it) crashes reading a bogus leaf list.
        is_leaf = (idx_ref != 0 and 0 < idx_count < 0x10000
                   and ds + idx_ref + idx_count * 4 <= sect_end)
        if is_leaf:
            offset_fields.append(pos + NODE_IDXREF)
        for k in range(4):
            if children_rel[k] != 0:
                offset_fields.append(pos + NODE_CHILDREN + k * 4)
        contained: List[int] = []
        if is_leaf:
            contained = list(struct.unpack_from("<%dI" % idx_count, buf, ds + idx_ref))
        nodes.append(SpaceNode(pos=pos, rel=rel,
                               idx_ref=(idx_ref if is_leaf else 0),
                               idx_count=(idx_count if is_leaf else 0),
                               children_rel=children_rel, contained=contained))
        for c in children_rel:
            if c != 0 and ds + c + NODE_SIZE <= sect_end:   # skip bogus child pointers
                walk_node(c)

    if spacetree_rel != 0:
        walk_node(spacetree_rel)

    # --- collision section: enumerate every internal offset field ---
    if collision_rel != 0:
        _collect_collision_offsets(buf, ds, collision_rel, blocks, payload_end_rel, offset_fields)

    return ZoneDef(
        data_start=ds, section_start=section_start, section_size=section_size,
        payload_end_rel=payload_end_rel, node_count=node_count, blocks=blocks,
        header_offsets=header_offsets, offset_fields=offset_fields, nodes=nodes,
        root_rel=spacetree_rel, record_size=record_size,
    )


def _collect_collision_offsets(buf, ds, collision_rel, blocks, payload_end_rel, offset_fields) -> None:
    cb = ds + collision_rel
    # header
    first_mesh_off = cb + 0x04
    pair_count = _u32(buf, cb + 0x08)
    pairs_off_field = cb + 0x0C
    map_off_field = cb + 0x10
    transforms_off_field = cb + 0x14
    for fld in (cb + 0x04, cb + 0x0C, cb + 0x10, cb + 0x14):
        if _u32(buf, fld) != 0:
            offset_fields.append(fld)

    pairs_rel = _u32(buf, cb + 0x0C)
    map_rel = _u32(buf, cb + 0x10)

    mesh_offsets: set = set()
    transform_offsets: set = set()

    # mesh/transform pairs: pair_count groups, each = countFlags + groupSize*(matrixOff, meshOff) + zero
    p = ds + pairs_rel
    for _ in range(pair_count):
        count_flags = _u32(buf, p); p += 4
        group_size = count_flags & 0x7FF
        for _j in range(group_size):
            matrix_field = p
            mesh_field = p + 4
            offset_fields.append(matrix_field)
            offset_fields.append(mesh_field)
            transform_offsets.add(_u32(buf, matrix_field))
            mesh_offsets.add(_u32(buf, mesh_field))
            p += 8
        p += 4  # trailing zero

    # ALL collision meshes: a contiguous chain of numMeshes variable-stride meshes
    # from firstMeshOff. Each 16-byte header = position/normal/index offsets +
    # triCount(u16) + flags(u16); the next header follows the index buffer at
    # (indexOffset + triCount*8). The engine fixes up every mesh's three offsets,
    # not just the ones reachable via sections — so relocate them all (the
    # ~181 section-unreferenced meshes were the missing relocation that crashed).
    num_meshes = _u32(buf, cb + 0x00)
    mp = ds + _u32(buf, cb + 0x04)  # firstMeshOffset
    section_end = ds + payload_end_rel
    for _ in range(num_meshes):
        if mp + 16 > section_end:
            break
        for off in (0, 4, 8):
            if _u32(buf, mp + off) != 0:
                offset_fields.append(mp + off)
        idx_rel = _u32(buf, mp + 8)
        tri = struct.unpack_from("<H", buf, mp + 0x0C)[0]
        mp = ds + idx_rel + tri * 8  # next mesh header follows the index buffer

    # Collision transforms are a contiguous fixed-stride array (0xC0 each) in
    # [transformsOff, pairsOff). The engine reads ALL of them (indexed by object),
    # not just the pair-referenced subset — so relocate every transform's
    # cullingGroupOffset. Missing the unreferenced ones leaves dangling pointers
    # into the shifted culling region and crashes the zone on load.
    transforms_rel = _u32(buf, cb + 0x14)
    span = pairs_rel - transforms_rel  # pairs immediately follow the transform array
    if 0 < transforms_rel < pairs_rel and span % TRANSFORM_SIZE == 0:
        for i in range(span // TRANSFORM_SIZE):
            fld = ds + transforms_rel + i * TRANSFORM_SIZE + TRANSFORM_CULLGROUP
            if _u32(buf, fld) != 0:
                offset_fields.append(fld)
    else:  # fallback: relocate the pair-referenced transforms only
        for t_rel in transform_offsets:
            fld = ds + t_rel + TRANSFORM_CULLGROUP
            if _u32(buf, fld) != 0:
                offset_fields.append(fld)

    # collision grid map: zoneBlocksZ*subBlocksZ rows x zoneBlocksX*subBlocksX cols of u32 offsets
    zbx, zbz, bw, bl = blocks
    sbx, sbz = bw // 4, bl // 4
    cols = zbx * sbx
    rows = zbz * sbz
    map_pos = ds + map_rel
    total = rows * cols
    # clamp to payload (map is the last region; guard against overrun)
    max_entries = (ds + payload_end_rel - map_pos) // 4
    total = min(total, max_entries)
    for e in range(total):
        fld = map_pos + e * 4
        if _u32(buf, fld) != 0:
            offset_fields.append(fld)


# ---------------------------------------------------------------------------
# Growing the section: append a placement and register it in the space tree
# ---------------------------------------------------------------------------


def _pack32(buf: bytearray, pos: int, value: int) -> None:
    struct.pack_into("<I", buf, pos, value & 0xFFFFFFFF)


def _find_path_to_leaf(zd: ZoneDef, leaf: SpaceNode) -> List[SpaceNode]:
    """Root-to-leaf node path (for bounding-box expansion)."""
    by_rel = {n.rel: n for n in zd.nodes}
    path: List[SpaceNode] = []

    def dfs(rel: int) -> bool:
        node = by_rel.get(rel)
        if node is None:
            return False
        path.append(node)
        if node.rel == leaf.rel:
            return True
        for c in node.children_rel:
            if c != 0 and dfs(c):
                return True
        path.pop()
        return False

    dfs(zd.root_rel)
    return path


def _leaf_distance_sq(buf: bytearray, node_pos: int, point: Sequence[float]) -> float:
    xs, ys, zs = [], [], []
    for i in range(8):
        x, y, z = struct.unpack_from("<3f", buf, node_pos + i * 12)
        xs.append(x); ys.append(y); zs.append(z)
    dx = max(min(xs) - point[0], 0.0, point[0] - max(xs))
    dy = max(min(ys) - point[1], 0.0, point[1] - max(ys))
    dz = max(min(zs) - point[2], 0.0, point[2] - max(zs))
    return dx * dx + dy * dy + dz * dz


def _closest_leaf_for_point(buf: bytearray, zd: ZoneDef, point: Sequence[float]) -> Optional[SpaceNode]:
    leaves = [n for n in zd.nodes if n.idx_count > 0 and n.idx_ref != 0]
    if not leaves:
        return None
    return min(leaves, key=lambda n: _leaf_distance_sq(buf, n.pos, point))


def _placement_pos(buf: bytearray, ds: int, index: int, record_size: int) -> Tuple[float, float, float]:
    return struct.unpack_from("<3f", buf, ds + OBJ_ARRAY_START + index * record_size + 0x10)


def _nearest_placement_leaf(buf: bytearray, zd: ZoneDef, point: Sequence[float],
                            exclude: set[int]) -> Optional[SpaceNode]:
    best: Optional[Tuple[float, SpaceNode]] = None
    for leaf in zd.nodes:
        if leaf.idx_count <= 0:
            continue
        for idx in leaf.contained:
            if idx in exclude or idx >= zd.node_count:
                continue
            p = _placement_pos(buf, zd.data_start, idx, zd.record_size)
            d = ((p[0] - point[0]) * (p[0] - point[0]) +
                 (p[1] - point[1]) * (p[1] - point[1]) +
                 (p[2] - point[2]) * (p[2] - point[2]))
            if best is None or d < best[0]:
                best = (d, leaf)
    return best[1] if best else None


def expand_placement_bounds(buf: bytearray, zd: ZoneDef, index: int, position: Sequence[float],
                            shifted_by: int = 0) -> None:
    """Expand the space-tree leaf that owns ``index`` so a moved placement remains visible."""
    expand_placement_bounds_points(buf, zd, index, [position], shifted_by)


def expand_placement_bounds_points(buf: bytearray, zd: ZoneDef, index: int,
                                   points: Sequence[Sequence[float]], shifted_by: int = 0) -> None:
    """Expand the owning space-tree leaf to contain all world-space mesh bounds points."""
    leaf = next((n for n in zd.nodes if n.idx_count > 0 and index in n.contained), None)
    if leaf is None:
        return
    for node in _find_path_to_leaf(zd, leaf):
        for point in points:
            _expand_node_bbox(buf, node.pos + shifted_by, point)


def assign_placements_to_nearest_leaf(sec: bytearray,
                                      placements: Sequence[Tuple[int, Sequence[float]]]) -> bytearray:
    """Move existing placement indices to the leaf nearest their new position.

    Index lists are append-only: leaves that change get fresh lists at section end,
    avoiding relocation of existing structures.
    """
    ds = 0x10
    zd = parse_zonedef(sec, ds, 0, len(sec))
    index_to_leaf = {idx: leaf for leaf in zd.nodes for idx in leaf.contained}
    changed: Dict[int, SpaceNode] = {}
    exclude = {idx for idx, _pos in placements}
    for idx, pos in placements:
        old = index_to_leaf.get(idx)
        new_leaf = _nearest_placement_leaf(sec, zd, pos, exclude) or _closest_leaf_for_point(sec, zd, pos)
        if old is not None and new_leaf is not None and old.rel != new_leaf.rel:
            if old.rel not in changed:
                old.contained = [i for i in old.contained if i != idx]
                changed[old.rel] = old
            if new_leaf.rel not in changed:
                new_leaf.contained = list(new_leaf.contained)
                changed[new_leaf.rel] = new_leaf
            new_leaf.contained.append(idx)

    if not changed:
        out = bytearray(sec)
        for idx, pos in placements:
            expand_placement_bounds(out, zd, idx, pos)
        return out

    out = bytearray(sec)
    cursor_rel = len(out) - ds
    for leaf in changed.values():
        leaf_pos = ds + leaf.rel
        _pack32(out, leaf_pos + NODE_IDXREF, cursor_rel)
        _pack32(out, leaf_pos + NODE_IDXCOUNT, len(leaf.contained))
        out += struct.pack("<%dI" % len(leaf.contained), *leaf.contained)
        cursor_rel += len(leaf.contained) * 4

    for idx, pos in placements:
        expand_placement_bounds(out, parse_zonedef(out, ds, 0, len(out)), idx, pos)

    total = len(out)
    padded = (total + 15) & ~15
    out += bytearray(padded - total)
    meta = _u32(out, ds)
    _pack32(out, ds, (meta & 0xFF000000) | (((padded - ds) - 8) & 0x00FFFFFF))
    _pack32(out, 4, encode_section_meta(padded, SECTION_TYPE_ZONE_DEF, what="0x1C ZoneDef section"))
    return out


def _expand_node_bbox(buf: bytearray, node_pos: int, point: Sequence[float]) -> None:
    """Rewrite a node's 8 corner points as the AABB of its current extent + point."""
    xs, ys, zs = [point[0]], [point[1]], [point[2]]
    for i in range(8):
        x, y, z = struct.unpack_from("<3f", buf, node_pos + i * 12)
        xs.append(x); ys.append(y); zs.append(z)
    lo = (min(xs), min(ys), min(zs))
    hi = (max(xs), max(ys), max(zs))
    corners = [(lo[0] if (i & 1) else hi[0], lo[1] if (i & 2) else hi[1], lo[2] if (i & 4) else hi[2]) for i in range(8)]
    for i, c in enumerate(corners):
        struct.pack_into("<3f", buf, node_pos + i * 12, *c)


def add_placements(sec: bytearray, new_objs: Sequence[Tuple[int, Tuple[float, float, float], Optional[str]]],
                   register_in_tree: bool = True, prefer_src_leaf: bool = False) -> bytearray:
    """Append placements to a *decrypted* 0x1C section (section-local bytearray,
    data_start = 0x10). Each entry =
    ``(src_index, position, rotation_or_None, scale_or_None, new_id_or_None)``: the
    new record is copied from ``src_index`` (same flags/links), its TRS overwritten
    with the given world position (+ rotation/scale if provided, else the source's),
    optional 16-char mesh id, then registered in the space-tree leaf that holds
    ``src_index`` so the engine renders it.

    Returns a new decrypted section bytearray (counts, decode-length and section
    size meta updated; ready to re-encrypt). All internal offsets are relocated.
    """
    ds = 0x10
    zd = parse_zonedef(sec, ds, 0, len(sec))
    n_add = len(new_objs)
    if n_add == 0:
        return bytearray(sec)

    rsize = zd.record_size
    D = rsize * n_add
    objs_end_rel = OBJ_ARRAY_START + zd.node_count * rsize
    insert_abs = ds + objs_end_rel

    # --- build the new record bytes (copied from each src; TRS / id patched) ---
    # Each entry: (src_index, pos, rot, scale, new_id). rot/scale may be None to
    # keep the source record's values (used when only relocating, not re-orienting).
    blob = bytearray()
    for (src_index, position, rotation, scale, new_id) in new_objs:
        src = ds + OBJ_ARRAY_START + src_index * rsize
        rec = bytearray(sec[src:src + rsize])
        if new_id is not None:
            rec[0:0x10] = new_id.encode("ascii", "replace")[:0x10].ljust(0x10, b" ")
        struct.pack_into("<3f", rec, 0x10, *position)
        if rotation is not None:
            struct.pack_into("<3f", rec, 0x1C, *rotation)
        if scale is not None:
            struct.pack_into("<3f", rec, 0x28, *scale)
        _pack32(rec, OBJ_CULLING_LINK, 0)  # not a member of any culling table
        blob += rec

    # --- splice records in at the end of the object array; shift the rest by D ---
    new = bytearray(sec[:insert_abs]) + blob + bytearray(sec[insert_abs:])

    # --- relocate every stored offset by +D (every target sits at/after objs_end) ---
    for p in zd.offset_fields:
        v = _u32(sec, p)
        np = p if p < insert_abs else p + D
        _pack32(new, np, v + D)

    # --- register each new object index in the space-tree leaf holding its src ---
    # New leaf index lists are appended at the very end of the section, so nothing
    # points past them and no further relocation cascade is needed.
    registered = 0
    if register_in_tree:
        cursor_rel = zd.payload_end_rel + D  # current end of payload in `new`
        for k, (src_index, position, _rot, _scale, _new_id) in enumerate(new_objs):
            new_index = zd.node_count + k
            # prefer_src_leaf: anchor the new object into the TEMPLATE's (src's) leaf
            # instead of the position-nearest one. Used by import_object so a brand-new
            # object lands in a fixed stable block's leaf rather than whatever sits nearest
            # (which, on a deletion spot like the fountain, is the fragile leaf that crashes).
            leaf = None
            if prefer_src_leaf:
                leaf = next((n for n in zd.nodes if n.idx_count > 0 and src_index in n.contained), None)
            if leaf is None:
                leaf = _nearest_placement_leaf(sec, zd, position, set()) or _closest_leaf_for_point(sec, zd, position)
            if leaf is None:
                leaf = next((n for n in zd.nodes if n.idx_count > 0 and src_index in n.contained), None)
            if leaf is None:
                continue  # src not in any leaf (shouldn't happen) -> object simply won't render
            leaf_pos_new = leaf.pos + D
            new_list = list(leaf.contained) + [new_index]
            # point this leaf at a fresh, larger index list at the section end
            _pack32(new, leaf_pos_new + NODE_IDXREF, cursor_rel)
            _pack32(new, leaf_pos_new + NODE_IDXCOUNT, len(new_list))
            new += struct.pack("<%dI" % len(new_list), *new_list)
            cursor_rel += len(new_list) * 4
            registered += 1
            # keep our in-memory copy current so a second add re-using the same leaf chains
            leaf.contained = new_list
            leaf.idx_count = len(new_list)
            # expand the leaf + its ancestors so the new position is inside their AABBs
            expand_placement_bounds(new, zd, new_index, position, shifted_by=D)

    # --- header: bump nodeCount; refresh decode-length; pad + section size meta ---
    kn = _u32(new, ds + 4)
    _pack32(new, ds + 4, (kn & 0xFF000000) | ((zd.node_count + n_add) & 0x00FFFFFF))

    # The collision header carries the space-tree index count (@collision+0x18),
    # which mirrors node_count. Bump it by the number we actually registered in the
    # tree — otherwise the engine under-allocates its visible-object buffer and
    # overruns it on the new index. The collision offset in the header was already
    # relocated (+D) above, so read it fresh.
    coll_rel = _u32(new, ds + 0x08)
    if coll_rel and registered:
        ni_pos = ds + coll_rel + 0x18
        old_idxc = _u32(new, ni_pos)
        # Only bump idxCount on a WELL-FORMED zone, where it mirrors nodeCount (one
        # per-object cull transform per object — every retail zone). A malformed
        # xi-built zone carries idxCount=0 with NO per-object transform array; bumping
        # it there tells the engine to read per-object cull volumes that don't exist
        # (idxCount stays < nodeCount), and an appended collision transform then lands in
        # that phantom range → crash on object+collision. Leaving idxCount=0 keeps such a
        # zone self-consistent — added objects cull via their space-tree leaf, exactly like
        # the zone's existing objects already do.
        if old_idxc == zd.node_count:
            _pack32(new, ni_pos, old_idxc + registered)

    total = len(new)
    padded = (total + 15) & ~15
    new += bytearray(padded - total)

    meta = _u32(new, ds)
    new_payload = padded - ds
    _pack32(new, ds, (meta & 0xFF000000) | ((new_payload - 8) & 0x00FFFFFF))

    section_meta = encode_section_meta(padded, SECTION_TYPE_ZONE_DEF, what="0x1C ZoneDef section")
    _pack32(new, 4, section_meta)
    return new


# ---------------------------------------------------------------------------
# Growing the per-object collision-transform array
# ---------------------------------------------------------------------------
# The collision section holds a contiguous array of 0xC0 "transform" records in
# [transformsOff, pairsOff), **indexed 1:1 by object index**. Each record is:
#   +0x00  world matrix M   (16 f32, column-major = trs_matrix for prop objects)
#   +0x40  inverse M^-1     (16 f32)
#   +0x80  per-object cull data (mesh-derived local bounds; identical across
#          instances of the same mesh) + cullingGroupOffset @ +0xA8
# The engine iterates `indexCount` (== nodeCount) objects for per-object frustum
# culling, so when add_placements grows the object array + bumps indexCount it MUST
# also grow this array — otherwise the new index reads past the array (garbage cull
# volume) and the object pops in/out at certain camera angles.

TRANSFORM_INV_MATRIX = 0x40     # inverse world matrix within a transform record
TRANSFORM_CULL_BOUNDS = 0x80    # per-object collision tail (normal mtx + world-Y AABB)
TRANSFORM_MISC_FLAGS = 0xA4     # u32 miscFlags (zone-map/sub-area); bit1 set on every native


def _invert_affine(m):
    """Inverse of a 4x4 affine matrix in trs_matrix's column-major 16-float layout
    (col3 = translation, last row 0,0,0,1). Returns the same layout."""
    a, b, c = m[0], m[4], m[8]     # linear part, rows
    d, e, f = m[1], m[5], m[9]
    g, h, i = m[2], m[6], m[10]
    A = e * i - f * h
    B = -(d * i - f * g)
    C = d * h - e * g
    det = a * A + b * B + c * C
    tx, ty, tz = m[12], m[13], m[14]
    if abs(det) < 1e-12:          # singular linear part: best-effort (negate translation)
        return [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, -tx, -ty, -tz, 1.0]
    inv = 1.0 / det
    i00 = A * inv;                 i01 = -(b * i - c * h) * inv; i02 = (b * f - c * e) * inv
    i10 = B * inv;                 i11 = (a * i - c * g) * inv;  i12 = -(a * f - c * d) * inv
    i20 = C * inv;                 i21 = -(a * h - b * g) * inv; i22 = (a * e - b * d) * inv
    ntx = -(i00 * tx + i01 * ty + i02 * tz)
    nty = -(i10 * tx + i11 * ty + i12 * tz)
    ntz = -(i20 * tx + i21 * ty + i22 * tz)
    return [i00, i10, i20, 0.0,
            i01, i11, i21, 0.0,
            i02, i12, i22, 0.0,
            ntx, nty, ntz, 1.0]


def _normalized_rot3x3(m16):
    """Extract the rotation (scale removed) from a column-major 4x4 as a flat 9-list
    in the same column-major upper-3x3 order: [c0x,c0y,c0z, c1x,c1y,c1z, c2x,c2y,c2z]."""
    out = []
    for c in range(3):
        vx, vy, vz = m16[c * 4 + 0], m16[c * 4 + 1], m16[c * 4 + 2]
        length = (vx * vx + vy * vy + vz * vz) ** 0.5 or 1.0
        out += [vx / length, vy / length, vz / length]
    return out


def hide_placement(sec: bytearray, ds: int, index: int, coll_rel: int, far: float = -100000.0) -> None:
    """'Hide' an object instead of deleting it: translate it far below the world — both
    the placement record position AND its collision-transform world+inverse matrices —
    while leaving every registration structure (name, space-tree, culling tables,
    transform array) intact.

    Why not blank+remove: the destructive delete path (zero the record name + splice out
    the orphaned mesh) desyncs the retail client when a brand-NEW object is also appended
    to the zone — the moment the camera pulls in near the new object it crashes. The
    append-only state (every object still fully registered) is known-good, so we keep the
    DAT in that state and simply relocate the unwanted object far out of view/reach."""
    rec = ds + OBJ_ARRAY_START + index * parse_zonedef(sec, ds, 0, len(sec)).record_size
    px, py, pz = struct.unpack_from("<3f", sec, rec + 0x10)
    struct.pack_into("<3f", sec, rec + 0x10, px, py + far, pz)
    if coll_rel:
        cb = ds + coll_rel
        trel = _u32(sec, cb + 0x14)
        to = ds + trel + index * TRANSFORM_SIZE
        m = list(struct.unpack_from("<16f", sec, to))
        m[13] += far  # column-major translation Y (col3 = m[12],m[13],m[14])
        struct.pack_into("<16f", sec, to, *m)
        struct.pack_into("<16f", sec, to + TRANSFORM_INV_MATRIX, *_invert_affine(m))


def remove_index_from_its_leaf(sec: bytearray, ds: int, zd: "ZoneDef", index: int) -> bool:
    """Remove an object index from whatever space-partition leaf currently contains it,
    in place (no resize): overwrite its slot with the leaf's last entry and decrement the
    leaf's idxCount. The leftover trailing slot is simply beyond the new count.

    A hidden/relocated object (see hide_placement) MUST leave the tree: if it stays as a
    far-displaced member of a leaf that also holds a freshly-APPENDED object, the client
    crashes when the camera processes that leaf (an appended object sharing a leaf with a
    *normal* object is fine — verified in-game; only the displaced-member combo crashes).
    Updates the parsed ``zd`` node so repeated calls in one pass stay consistent."""
    for leaf in zd.nodes:
        if leaf.idx_ref == 0 or index not in leaf.contained:
            continue
        pos = leaf.contained.index(index)
        last = leaf.idx_count - 1
        lst = ds + leaf.idx_ref
        last_val = _u32(sec, lst + last * 4)
        _pack32(sec, lst + pos * 4, last_val)
        _pack32(sec, ds + leaf.rel + NODE_IDXCOUNT, leaf.idx_count - 1)
        leaf.contained = [i for i in leaf.contained if i != index]
        leaf.idx_count -= 1
        return True
    return False


def add_collision_transforms(sec: bytearray, entries) -> bytearray:
    """Append one 0xC0 collision-transform record per entry to the END of the
    transform array (so each new record's index == the object index appended by
    add_placements). ``entries`` = list of ``(src_transform_index, matrix16[, bbox6])``:
    the src record's bytes are cloned, then the world matrix @+0x00 and inverse @+0x40
    are overwritten.

    The per-object cull bounds at +0x80..0xBF are 16 floats:
      [normalized world-matrix rotation 3x3 (9)][0,0,0,0][mesh bbox Ymin][Ymax][0]
    (verified against native gaitou01/block03/tubo1; cullingGroupOffset@+0xA8 falls in
    the zero run). If ``bbox6`` (xmin,xmax,ymin,ymax,zmin,zmax, mesh-local) is given we
    DERIVE these from this object's own mesh+placement — required for a brand-new custom
    mesh, where the only available template would otherwise donate a DIFFERENT mesh's
    bounds (the cause of the camera-dependent cull crash). Without ``bbox6`` we keep the
    cloned template bounds and just zero the culling group (legacy same-mesh path).
    Every section offset past the insertion is relocated; section is re-padded and its
    meta refreshed. Operates on a *decrypted* section (data_start = 0x10); caller
    re-encrypts. Returns a new bytearray.
    """
    ds = 0x10
    if not entries:
        return bytearray(sec)
    zd = parse_zonedef(sec, ds, 0, len(sec))
    coll_rel = zd.header_offsets.get("collision", 0)
    if not coll_rel:
        return bytearray(sec)          # no collision section (e.g. ships) — nothing to grow
    cb = ds + coll_rel
    transforms_rel = _u32(sec, cb + 0x14)
    pairs_rel = _u32(sec, cb + 0x0C)
    if not (0 < transforms_rel < pairs_rel) or (pairs_rel - transforms_rel) % TRANSFORM_SIZE:
        return bytearray(sec)          # non-uniform array — bail rather than corrupt
    tbase = ds + transforms_rel

    insertion_rel = pairs_rel          # ds-relative insert point = just past the transform array
    insertion_abs = ds + pairs_rel
    delta = TRANSFORM_SIZE * len(entries)

    blob = bytearray()
    for entry in entries:
        src_index, m16 = entry[0], entry[1]
        bbox6 = entry[2] if len(entry) > 2 else None
        src = tbase + src_index * TRANSFORM_SIZE
        rec = bytearray(sec[src:src + TRANSFORM_SIZE])
        struct.pack_into("<16f", rec, 0x00, *m16)
        struct.pack_into("<16f", rec, TRANSFORM_INV_MATRIX, *_invert_affine(m16))
        if bbox6 is not None:
            # NEW visual-only object: write the native NO-COLLISION pattern. The +0x80..0xBF
            # cull tail holds the per-object collision data; the u32 world-Y AABB at
            # +0xB4/+0xB8 is what the client's CollisionManager reads to decide whether to
            # collision-test the object. 169/182 native non-collision objects (b_low02,
            # towers, …) carry the whole tail ZEROED with Y-AABB (0,0) — the "no collision"
            # sentinel: the manager culls them and never tests their triangles. A new object
            # has NO collision mesh, so giving it a REAL Y-AABB (e.g. the mesh extent) makes
            # the client try to test non-existent triangles → null deref → crash the instant
            # the camera/player collides near it. So zero the whole tail; keep ONLY the
            # miscFlags u32 @+0xA4 (zone-map/sub-area — every native record has bit1 set; a
            # zeroed miscFlags crashes too). This makes the object purely visual: rendered &
            # frustum-culled (via the space-tree leaf + culling tables), but collision-inert.
            misc = struct.unpack_from("<I", rec, TRANSFORM_MISC_FLAGS)[0] or 0x2
            for o in range(TRANSFORM_CULL_BOUNDS, TRANSFORM_SIZE, 4):
                struct.pack_into("<I", rec, o, 0)
            struct.pack_into("<I", rec, TRANSFORM_MISC_FLAGS, misc)
        else:
            _pack32(rec, TRANSFORM_CULLGROUP, 0)   # no culling group (matches prop objects)
        blob += rec

    new = bytearray(sec[:insertion_abs]) + blob + bytearray(sec[insertion_abs:])

    # Relocate every stored offset: shift a field's *value* by delta when its target
    # sits at/after the insertion point, and shift the field's *position* by delta when
    # the field itself sits after it. (Same machinery as add_placements, but here only
    # the collision tail moves, so the shift is conditional rather than uniform.)
    for p in zd.offset_fields:
        v = _u32(sec, p)
        np_ = p + delta if p >= insertion_abs else p
        nv = v + delta if v >= insertion_rel else v
        _pack32(new, np_, nv)

    total = len(new)
    padded = (total + 15) & ~15
    new += bytearray(padded - total)
    meta = _u32(new, ds)
    new_payload = padded - ds
    _pack32(new, ds, (meta & 0xFF000000) | ((new_payload - 8) & 0x00FFFFFF))
    _pack32(new, 4, encode_section_meta(padded, SECTION_TYPE_ZONE_DEF, what="0x1C ZoneDef section"))
    return new


# ---------------------------------------------------------------------------
# Growing the culling tables (the camera-floor PVS that actually gates drawing)
# ---------------------------------------------------------------------------
# The client renders a zone object only when the culling table selected by the
# CAMERA's floor lists that object's index (or the floor has no table → all render).
# Layout at cullTablesOff (= header+0x14): indexTableCount(u32), then that many tables,
# each = count(u32) + count object-index u32s. Object record cullingTableLink@0x48 is a
# ds-relative offset to a table's count field. A newly placed/appended object is in NO
# table, so it vanishes wherever the camera floor maps to a (non-null) table — which is
# most of a zone. Add it to the tables so it stays visible.

def add_to_culling_tables(sec: bytearray, new_index: int, table_indices=None) -> bytearray:
    """Insert ``new_index`` into the 0x1C culling tables (PVS). ``table_indices``
    selects which tables (default: ALL → visible from every camera position). Grows each
    selected table by one sorted entry, relocates the object cullingTableLink offsets
    that shift plus every offset past the culling region, repads + fixes meta. Decrypted
    section (data_start = 0x10); caller re-encrypts. Returns a new bytearray.
    """
    ds = 0x10
    zd = parse_zonedef(sec, ds, 0, len(sec))
    cull_rel = zd.header_offsets.get("culling", 0)
    if not cull_rel:
        return bytearray(sec)
    cull = ds + cull_rel
    p = cull
    ntab = _u32(sec, p); p += 4
    if ntab == 0:
        return bytearray(sec)
    tables = []  # (old_count_field_abs, [indices])
    for _ in range(ntab):
        cnt_off = p
        cnt = _u32(sec, p); p += 4
        idxs = list(struct.unpack_from("<%dI" % cnt, sec, p)) if cnt else []
        p += 4 * cnt
        tables.append((cnt_off, idxs))
    cull_end = p
    targets = set(range(ntab)) if table_indices is None else {t for t in table_indices if 0 <= t < ntab}

    # Rebuild the culling region; remember each table's NEW count-field offset.
    new_region = bytearray(struct.pack("<I", ntab))
    new_table_off = {}
    cursor = cull + 4
    for i, (cnt_off, idxs) in enumerate(tables):
        new_table_off[i] = cursor
        lst = sorted(idxs + [new_index]) if (i in targets and new_index not in idxs) else idxs
        new_region += struct.pack("<I", len(lst))
        if lst:
            new_region += struct.pack("<%dI" % len(lst), *lst)
        cursor += 4 + len(lst) * 4
    growth = len(new_region) - (cull_end - cull)
    if growth == 0:
        return bytearray(sec)  # already present in all selected tables

    new = bytearray(sec[:cull]) + new_region + bytearray(sec[cull_end:])

    # cullingTableLink@0x48 values point to a table's count field (ds-relative); remap
    # those whose table shifted. Everything whose target sits past the culling region
    # shifts by +growth; fields located past it shift position too.
    link_remap = {(tables[i][0] - ds): (new_table_off[i] - ds) for i in range(ntab)}
    cull_end_rel = cull_end - ds
    for fpos in zd.offset_fields:
        v = _u32(sec, fpos)
        npos = fpos + growth if fpos >= cull_end else fpos
        if v in link_remap:
            nv = link_remap[v]
        elif v >= cull_end_rel:
            nv = v + growth
        else:
            nv = v
        _pack32(new, npos, nv)

    total = len(new)
    padded = (total + 15) & ~15
    new += bytearray(padded - total)
    meta = _u32(new, ds)
    _pack32(new, ds, (meta & 0xFF000000) | ((padded - ds - 8) & 0x00FFFFFF))
    _pack32(new, 4, encode_section_meta(padded, SECTION_TYPE_ZONE_DEF, what="0x1C ZoneDef section"))
    return new


# ---------------------------------------------------------------------------
# 0x54 -> 0x64 record conversion (make a pre-production zone readable by the client)
# ---------------------------------------------------------------------------

def convert_zonedef_to_retail_stride(sec: bytearray) -> Tuple[bytearray, int]:
    """Rewrite a *decrypted* 0x1C section's placement array from 0x54 to 0x64 records.

    The retail client's ZoneDef reader (FUN_10177ef0 in FFXiMain) advances placement
    records by a hardcoded 0x64 with no branch on the mode byte, so it misreads every
    pre-production zone: only the records that happen to land on a printable name and
    sane floats get drawn, the rest are silently dropped. Widening the records to 0x64
    (extra 16 bytes zeroed, so no fileIdLink / culling link) is what makes the client
    agree with the file.

    The array grows by node_count * 0x10, so everything after it shifts down. The
    collision block is re-serialized at its new base rather than patched, which fixes
    all of its internal offsets at once.

    Returns (new_section, delta_bytes). Raises if the section is already 0x64.
    """
    from xi.zone.xi_collision import parse_collision_raw, serialize_collision_raw

    ds = 0x10
    zd = parse_zonedef(sec, ds, 0, len(sec))
    if zd.record_size == OBJ_RECORD_SIZE:
        raise ValueError("already retail-stride (0x64)")

    n = zd.node_count
    delta = n * (OBJ_RECORD_SIZE - OBJ_RECORD_SIZE_PROTO)
    old_objs_end = OBJ_ARRAY_START + n * OBJ_RECORD_SIZE_PROTO
    new_objs_end = OBJ_ARRAY_START + n * OBJ_RECORD_SIZE

    coll_rel = zd.header_offsets["collision"]
    if coll_rel and coll_rel != old_objs_end:
        raise ValueError(f"collision block is not immediately after the object array "
                         f"(coll={coll_rel:#x}, objs_end={old_objs_end:#x}) — unhandled layout")
    if zd.header_offsets["spacetree"] not in (0, 2):
        raise ValueError("zone has a space tree — relocation of tree nodes is not implemented")

    raw = parse_collision_raw(sec, ds, zd.payload_end_rel) if coll_rel else None

    out = bytearray(sec[:ds + OBJ_ARRAY_START])          # section header + 0x1C header
    for i in range(n):                                   # widen every record
        src = ds + OBJ_ARRAY_START + i * OBJ_RECORD_SIZE_PROTO
        out += sec[src:src + OBJ_RECORD_SIZE_PROTO]
        out += bytes(OBJ_RECORD_SIZE - OBJ_RECORD_SIZE_PROTO)

    if raw is not None:
        raw.coll_rel = new_objs_end                      # serializer recomputes from this
        out += serialize_collision_raw(raw)

    # header offsets that pointed at the collision block
    for off in (0x08, 0x14):                             # collision, culling
        if _u32(out, ds + off) == coll_rel:
            _pack32(out, ds + off, new_objs_end)

    # payload length (low 24 bits at data_start; top byte is the mode, leave it alone)
    meta = _u32(out, ds)
    _pack32(out, ds, (meta & 0xFF000000) | ((meta & 0x00FFFFFF) + delta))
    struct.pack_into("<I", out, ds + 4, (n & 0x00FFFFFF) | (_u32(out, ds + 4) & 0xFF000000))

    # section meta: size must cover the grown payload, 16-byte aligned. Keep the
    # original flag bits (is_shadow / ver_num / ...) — only size and type change.
    out += bytes((-len(out)) & 0xF)
    flags = _u32(sec, 4)
    struct.pack_into("<I", out, 4,
                     encode_section_meta(len(out), SECTION_TYPE_ZONE_DEF, flags))
    return out, delta

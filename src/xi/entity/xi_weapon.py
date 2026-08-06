"""Weapon block swapping and joint remapping for entity DATs.

Humanoid mob DATs contain sub-model blocks delimited by HDR (0x01)
sections. The weapon block contains the weapon's texture + mesh as
a self-contained unit. This module can replace or insert weapon blocks,
remapping joint arrays so gear weapons (joint 5) work on mobs.
"""

import struct
from pathlib import Path

from xi.tex.xi_recolor import recolour_zone_dat


# ── Block parsing ──────────────────────────────────────────────────────

def _parse_blocks(data: bytes) -> list:
    """Parse a mob DAT into sub-model blocks delimited by HDR (0x01) sections."""
    sections = []
    pos = 0
    while pos < len(data) - 16:
        name = data[pos:pos + 4].decode('ascii', errors='replace')
        meta = struct.unpack_from('<I', data, pos + 4)[0]
        tc = meta & 0x7F
        sz = ((meta >> 7) & 0xFFFFF) * 0x10
        if sz <= 0:
            break
        sections.append({'name': name.strip(), 'type': tc, 'pos': pos, 'size': sz})
        pos += sz

    blocks = []
    current = None
    for s in sections:
        if s['type'] == 0x01:  # HDR
            if current:
                current['end'] = s['pos']
                blocks.append(current)
            current = {'name': s['name'], 'start': s['pos'], 'sections': [s]}
        elif current:
            current['sections'].append(s)
    if current:
        last = current['sections'][-1]
        current['end'] = last['pos'] + last['size']
        blocks.append(current)
    return blocks


def _find_weapon_block(blocks: list) -> dict | None:
    """Find the weapon block: has mesh sections, not 'mdl_' or 'skl_'."""
    for b in blocks:
        has_mesh = any(s['type'] == 0x2A for s in b['sections'])
        if has_mesh and b['name'] not in ('mdl_', 'skl_'):
            return b
    return None


# ── Joint remapping ────────────────────────────────────────────────────

def _get_weapon_joint(data: bytes, block: dict) -> int | None:
    """Read the joint array value from a weapon block's mesh section."""
    for s in block['sections']:
        if s['type'] != 0x2A:
            continue
        ds = s['pos'] + 0x10
        flags3 = data[ds + 2]
        if not (flags3 & 0x80):
            return None
        off = ds + 6
        struct.unpack_from('<I', data, off)[0]; off += 6
        ja_off = struct.unpack_from('<I', data, off)[0] * 2; off += 4
        nj = struct.unpack_from('<H', data, off)[0]
        if nj > 0:
            return struct.unpack_from('<H', data, ds + ja_off)[0]
    return None


def _remap_weapon_joint(block_bytes: bytearray, target_joint: int):
    """Overwrite all mesh joint arrays in a weapon block to target_joint."""
    pos = 0
    while pos < len(block_bytes) - 16:
        meta = struct.unpack_from('<I', block_bytes, pos + 4)[0]
        tc = meta & 0x7F
        sz = ((meta >> 7) & 0xFFFFF) * 0x10
        if sz <= 0:
            break
        if tc == 0x2A:
            ds = pos + 0x10
            flags3 = block_bytes[ds + 2]
            if flags3 & 0x80:
                off = ds + 6
                struct.unpack_from('<I', block_bytes, off)[0]; off += 6
                ja_off = struct.unpack_from('<I', block_bytes, off)[0] * 2; off += 4
                nj = struct.unpack_from('<H', block_bytes, off)[0]
                for i in range(nj):
                    struct.pack_into('<H', block_bytes, ds + ja_off + i * 2, target_joint)
        pos += sz


# ── Weapon recoloring ──────────────────────────────────────────────────

def recolor_weapon_dat(source: Path, opts: dict) -> bytes:
    """Recolor and/or scale a weapon DAT, returning the modified bytes.

    opts can contain: hue, saturation, lightness, tint, blend_mode, scale.
    """
    import tempfile

    with tempfile.NamedTemporaryFile(suffix='.DAT', delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        recolour_zone_dat(
            source, tmp_path,
            hue=opts.get('hue', 0),
            saturation=opts.get('saturation', 0),
            lightness=opts.get('lightness', 0),
            tint=opts.get('tint'),
            blend_mode=opts.get('blend_mode', 'normal'),
        )
        data = bytearray(tmp_path.read_bytes())
    finally:
        tmp_path.unlink(missing_ok=True)

    # Scale weapon mesh vertices if requested
    from xi.entity.anim.xi_export import parse_sections
    wep_scale = opts.get('scale', 0)
    if wep_scale and wep_scale != 1.0:
        sections = parse_sections(bytes(data))
        for s in sections:
            if s.type_code == 0x2A:
                _scale_weapon_mesh(data, s, wep_scale)

    return bytes(data)


def _scale_weapon_mesh(data: bytearray, section, scale: float) -> int:
    """Scale vertex positions in a mesh section."""
    ds = section.data_start
    flags3 = data[ds + 0x02]
    cloth = (flags3 & 0x01) != 0
    has_normals = not cloth

    off = ds + 6
    struct.unpack_from('<I', data, off)[0]; off += 6
    struct.unpack_from('<I', data, off)[0]; off += 4
    struct.unpack_from('<H', data, off)[0]; off += 2
    vc_off = struct.unpack_from('<I', data, off)[0] * 2; off += 6
    struct.unpack_from('<I', data, off)[0]; off += 6
    vd_off = struct.unpack_from('<I', data, off)[0] * 2

    single_count = struct.unpack_from('<H', data, ds + vc_off)[0]
    double_count = struct.unpack_from('<H', data, ds + vc_off + 2)[0]

    vertex_base = ds + vd_off
    stride_single = 24 if has_normals else 12
    stride_double = 56 if has_normals else 32

    scaled = 0
    for i in range(single_count):
        base = vertex_base + i * stride_single
        for j in range(3):
            o = base + j * 4
            val = struct.unpack_from('<f', data, o)[0]
            struct.pack_into('<f', data, o, val * scale)
        scaled += 1

    double_base = vertex_base + single_count * stride_single
    for i in range(double_count):
        base = double_base + i * stride_double
        for j in range(6):
            o = base + j * 4
            val = struct.unpack_from('<f', data, o)[0]
            struct.pack_into('<f', data, o, val * scale)
        scaled += 1

    return scaled


# ── Main swap function ─────────────────────────────────────────────────

def swap_weapon(target_data: bytes, source_data: bytes,
                block_name: str = None) -> bytes:
    """Replace a weapon block in target_data with one from source_data.

    block_name: if set, find the target block by HDR name (e.g. 'rng_'
    for offhand). If None, finds the first weapon block.

    If the target doesn't have the requested block, inserts the source
    block after the existing weapon block (for adding offhand to mobs
    that don't have one).

    Remaps the source weapon's joint array to match the target mob's
    weapon joint, so gear weapons (joint 5) work on mobs (joint 33 etc).
    """
    target_blocks = _parse_blocks(target_data)
    source_blocks = _parse_blocks(bytes(source_data))

    # Find target block
    target_wep = None
    if block_name:
        target_wep = next((b for b in target_blocks if b['name'] == block_name), None)
    if not target_wep:
        target_wep = _find_weapon_block(target_blocks)

    source_wep = _find_weapon_block(source_blocks)
    if not source_wep:
        raise ValueError('Source DAT has no weapon block')

    # Find the target joint from the MATCHED block (main or offhand)
    if target_wep:
        target_joint = _get_weapon_joint(target_data, target_wep)
    else:
        main_wep = _find_weapon_block(target_blocks)
        target_joint = _get_weapon_joint(target_data, main_wep) if main_wep else None

    # Extract source weapon block and remap its joint
    src_bytes = bytearray(source_data[source_wep['start']:source_wep['end']])
    if target_joint is not None:
        _remap_weapon_joint(src_bytes, target_joint)

    # Ensure the block ends with an END section
    has_end = any(s['type'] == 0x00 for s in source_wep['sections'])
    if not has_end:
        end_section = b'end ' + struct.pack('<I', 0x80) + b'\x00' * 8
        src_bytes += end_section

    result = bytearray()
    if target_wep:
        result += target_data[:target_wep['start']]
        result += src_bytes
        result += target_data[target_wep['end']:]
    elif main_wep:
        insert_pos = main_wep['end']
        result += target_data[:insert_pos]
        result += src_bytes
        result += target_data[insert_pos:]
    else:
        raise ValueError('Target DAT has no weapon block')

    return bytes(result)


def resolve_weapon_source(spec: str) -> Path:
    """Resolve a weapon source spec to a DAT path.

    Accepts:
      - A model ID (int): resolves via entity tables
      - "gear:RACE:SLOT:MODEL_ID": resolves via gear tables
      - A DAT path (ROM/X/Y.DAT): resolves relative to FFXI_DIR
    """
    from xi.xi_config import FFXI_DIR

    # Try gear: prefix
    if spec.startswith('gear:'):
        parts = spec.split(':')
        if len(parts) != 4:
            raise ValueError(f'gear: format must be gear:RACE:SLOT:MODEL_ID, got {spec}')
        race, slot, model_id = parts[1], parts[2], int(parts[3])
        from xi.gear.xi_export import resolve_gear_dat
        return resolve_gear_dat(race, slot, model_id)

    # Try as model ID (integer)
    try:
        model_id = int(spec)
        from xi.ftable.xi_core import load_all_tables, scan_file_ids
        from xi.entity.xi_core import modelid_to_file_id
        tables = load_all_tables()
        fid = modelid_to_file_id(model_id)
        hits = scan_file_ids([fid], tables)
        if not hits:
            raise ValueError(f'Model {model_id} not found in FTABLE')
        dat = Path(FFXI_DIR) / hits[0]['dat']
        if not dat.exists():
            raise FileNotFoundError(f'DAT not found: {dat}')
        return dat
    except ValueError:
        pass

    # Try as DAT path
    dat = Path(FFXI_DIR) / spec
    if dat.exists():
        return dat

    raise FileNotFoundError(f'Weapon source not found: {spec}')

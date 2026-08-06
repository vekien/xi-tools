"""Shared texture recolouring engine for FFXI DAT files.

Works on any DAT that contains 0x20 texture sections (DXT or paletted) and
optionally 0x2F environment sections.  Used by ``gear edit``, ``gear inject``,
``entity recolor``, ``entity weapon``, and ``zone inject``.

Public API
----------
recolour_zone_dat(source, output, *, hue, saturation, lightness, tint, ...)
    Read *source*, apply colour adjustments to every texture section, write
    *output*.  Returns a stats dict.
"""

import colorsys
import struct
from pathlib import Path


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def _rgb565_unpack(value: int):
    r = (value >> 11 & 0x1F) * 255 // 31
    g = (value >> 5 & 0x3F) * 255 // 63
    b = (value & 0x1F) * 255 // 31
    return r, g, b

def _rgb565_pack(r: int, g: int, b: int) -> int:
    return ((r * 31 // 255) << 11) | ((g * 63 // 255) << 5) | (b * 31 // 255)

def _rgb_to_hsv(r: int, g: int, b: int):
    return colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)

def _hsv_to_rgb(h: float, s: float, v: float):
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(r * 255), int(g * 255), int(b * 255)

def _parse_colour(colour: str):
    c = colour.lstrip('#')
    r = int(c[0:2], 16)
    g = int(c[2:4], 16)
    b = int(c[4:6], 16)
    a = int(c[6:8], 16) if len(c) >= 8 else 255
    return r, g, b, a

def _blend(br, bg, bb, tr, tg, tb, alpha, mode):
    a = alpha / 255.0
    br, bg, bb = br / 255.0, bg / 255.0, bb / 255.0
    tr, tg, tb = tr / 255.0, tg / 255.0, tb / 255.0
    if mode == 'multiply':
        rr, rg, rb = br * tr, bg * tg, bb * tb
    elif mode == 'screen':
        rr = 1 - (1 - br) * (1 - tr)
        rg = 1 - (1 - bg) * (1 - tg)
        rb = 1 - (1 - bb) * (1 - tb)
    elif mode == 'overlay':
        rr = 2 * br * tr if br < 0.5 else 1 - 2 * (1 - br) * (1 - tr)
        rg = 2 * bg * tg if bg < 0.5 else 1 - 2 * (1 - bg) * (1 - tg)
        rb = 2 * bb * tb if bb < 0.5 else 1 - 2 * (1 - bb) * (1 - tb)
    elif mode == 'add':
        rr, rg, rb = min(1, br + tr), min(1, bg + tg), min(1, bb + tb)
    else:  # normal
        rr, rg, rb = tr, tg, tb
    return (int((br + (rr - br) * a) * 255),
            int((bg + (rg - bg) * a) * 255),
            int((bb + (rb - bb) * a) * 255))


def _passes_range(h, s, v, hue_min=None, hue_max=None,
                  sat_min=None, sat_max=None, val_min=None, val_max=None) -> bool:
    """True when an HSV pixel falls within the optional filter bounds.

    h, s, v are in 0..1. hue_min/hue_max are in degrees (0–360) and may wrap.
    A None bound is unbounded on that side.
    """
    if hue_min is not None or hue_max is not None:
        deg = (h * 360.0) % 360.0
        lo = 0.0 if hue_min is None else hue_min % 360.0
        hi = 360.0 if hue_max is None else hue_max % 360.0
        inside = (lo <= deg <= hi) if lo <= hi else (deg >= lo or deg <= hi)
        if not inside:
            return False
    if sat_min is not None and s < sat_min:
        return False
    if sat_max is not None and s > sat_max:
        return False
    if val_min is not None and v < val_min:
        return False
    if val_max is not None and v > val_max:
        return False
    return True


def _adjust_rgb565(value: int, hue=0, saturation=0, lightness=0,
                   tint=None, blend_mode='normal',
                   hue_min=None, hue_max=None, sat_min=None, sat_max=None,
                   val_min=None, val_max=None) -> int:
    r, g, b = _rgb565_unpack(value)
    if r == 0 and g == 0 and b == 0:
        return value
    if any(x is not None for x in (hue_min, hue_max, sat_min, sat_max, val_min, val_max)):
        h, s, v = _rgb_to_hsv(r, g, b)
        if not _passes_range(h, s, v, hue_min, hue_max, sat_min, sat_max, val_min, val_max):
            return value
    if hue or saturation or lightness:
        h, s, v = _rgb_to_hsv(r, g, b)
        if hue:
            h = (h + hue / 360.0) % 1.0
        if saturation:
            s = max(0.0, min(1.0, s * (1.0 + saturation / 100.0)))
        if lightness:
            v = max(0.0, min(1.0, v * (1.0 + lightness / 100.0)))
        r, g, b = _hsv_to_rgb(h, s, v)
    if tint:
        tr, tg, tb, ta = _parse_colour(tint)
        r, g, b = _blend(r, g, b, tr, tg, tb, ta, blend_mode)
    return _rgb565_pack(r, g, b)


# ---------------------------------------------------------------------------
# DAT section parsing (local fast variant — yields tuples, not Section objects)
# ---------------------------------------------------------------------------

SECTION_TYPE_TEXTURE     = 0x20
SECTION_TYPE_ENVIRONMENT = 0x2F

def _parse_sections(data: bytes):
    pos = 0
    while pos + 16 <= len(data):
        meta = struct.unpack_from("<I", data, pos + 4)[0]
        type_code = meta & 0x7F
        size = ((meta >> 7) & 0xFFFFF) * 0x10
        if size <= 0:
            break
        yield type_code, pos, size, pos + 0x10
        pos += size

def _parse_texture_info(data: bytes, data_start: int):
    pos = data_start
    tex_type = data[pos]
    if tex_type not in (0x91, 0xA1, 0xB1):
        return None
    bit_count = struct.unpack_from("<H", data, pos + 0x1F)[0]
    if tex_type == 0xA1:
        dxt_type = data[pos + 0x39:pos + 0x3D].decode('ascii', errors='replace')
        dxt_data_size = struct.unpack_from("<I", data, pos + 0x3D)[0]
        return tex_type, dxt_type, pos + 0x45, dxt_data_size, bit_count
    else:
        data_offset = pos + 0x3D
        width  = struct.unpack_from("<I", data, pos + 0x15)[0]
        height = struct.unpack_from("<I", data, pos + 0x19)[0]
        if bit_count == 32:
            return tex_type, '', data_offset, width * height * 4, bit_count
        else:
            return tex_type, '', data_offset, 1024 + width * height, bit_count


# ---------------------------------------------------------------------------
# Texture recolouring
# ---------------------------------------------------------------------------

def _swap_idx01(word: int) -> int:
    result = 0
    for i in range(16):
        s = i * 2
        idx = (word >> s) & 0x3
        if idx == 0:   idx = 1
        elif idx == 1: idx = 0
        result |= idx << s
    return result

def _adjust_dxt(data: bytearray, dxt_type: str, offset: int, size: int, **kw):
    is_dxt1 = dxt_type == '1TXD'
    block_size = 8 if is_dxt1 else 16
    color_off  = 0 if is_dxt1 else 8
    pos = offset
    end = offset + size
    while pos + block_size <= end:
        cp = pos + color_off
        c0 = struct.unpack_from("<H", data, cp)[0]
        c1 = struct.unpack_from("<H", data, cp + 2)[0]
        nc0 = _adjust_rgb565(c0, **kw)
        nc1 = _adjust_rgb565(c1, **kw)
        if is_dxt1:
            if c0 > c1 and nc0 <= nc1:
                if nc0 == nc1:
                    nc0 = min(nc0 + 1, 0xFFFF) if nc0 < 0xFFFF else nc0
                    nc1 = max(nc1 - 1, 0) if nc0 == nc1 else nc1
                else:
                    nc0, nc1 = nc1, nc0
                    w = struct.unpack_from("<I", data, cp + 4)[0]
                    struct.pack_into("<I", data, cp + 4, _swap_idx01(w))
            elif c0 <= c1 and nc0 > nc1:
                nc0, nc1 = nc1, nc0
                w = struct.unpack_from("<I", data, cp + 4)[0]
                struct.pack_into("<I", data, cp + 4, _swap_idx01(w))
        struct.pack_into("<H", data, cp, nc0)
        struct.pack_into("<H", data, cp + 2, nc1)
        pos += block_size

def _adjust_palette(data: bytearray, offset: int, size: int, bit_count: int, **kw):
    hue = kw.get('hue', 0)
    sat = kw.get('saturation', 0)
    lit = kw.get('lightness', 0)
    tint = kw.get('tint')
    blend_mode = kw.get('blend_mode', 'normal')
    hue_min, hue_max = kw.get('hue_min'), kw.get('hue_max')
    sat_min, sat_max = kw.get('sat_min'), kw.get('sat_max')
    val_min, val_max = kw.get('val_min'), kw.get('val_max')
    has_range = any(x is not None for x in (hue_min, hue_max, sat_min, sat_max, val_min, val_max))
    count = size // 4 if bit_count == 32 else 256
    for i in range(count):
        p = offset + i * 4
        if p + 3 >= len(data):
            break
        b, g, r, a = data[p], data[p+1], data[p+2], data[p+3]
        if a > 0 and (r | g | b):
            if has_range:
                h, s, v = _rgb_to_hsv(r, g, b)
                if not _passes_range(h, s, v, hue_min, hue_max, sat_min, sat_max, val_min, val_max):
                    continue
            if hue or sat or lit:
                h, s, v = _rgb_to_hsv(r, g, b)
                if hue: h = (h + hue / 360.0) % 1.0
                if sat: s = max(0.0, min(1.0, s * (1.0 + sat / 100.0)))
                if lit: v = max(0.0, min(1.0, v * (1.0 + lit / 100.0)))
                r, g, b = _hsv_to_rgb(h, s, v)
            if tint:
                tr, tg, tb, ta = _parse_colour(tint)
                r, g, b = _blend(r, g, b, tr, tg, tb, ta, blend_mode)
            data[p], data[p+1], data[p+2] = b, g, r


# ---------------------------------------------------------------------------
# Environment section (0x2F) colour adjustment
# ---------------------------------------------------------------------------

_ENV_COLOUR_OFFSETS = {
    'model_sun':      0x0C, 'model_moon':    0x10,
    'model_ambient':  0x14, 'model_fog':     0x18,
    'terrain_sun':    0x2C, 'terrain_moon':  0x30,
    'terrain_ambient':0x34, 'terrain_fog':   0x38,
}

def _adjust_bgra(data: bytearray, offset: int, **kw):
    b, g, r, a = data[offset], data[offset+1], data[offset+2], data[offset+3]
    if r == 0 and g == 0 and b == 0:
        return
    hue, sat, lit = kw.get('hue', 0), kw.get('saturation', 0), kw.get('lightness', 0)
    tint, blend_mode = kw.get('tint'), kw.get('blend_mode', 'normal')
    if hue or sat or lit:
        h, s, v = _rgb_to_hsv(r, g, b)
        if hue: h = (h + hue / 360.0) % 1.0
        if sat: s = max(0.0, min(1.0, s * (1.0 + sat / 100.0)))
        if lit: v = max(0.0, min(1.0, v * (1.0 + lit / 100.0)))
        r, g, b = _hsv_to_rgb(h, s, v)
    if tint:
        tr, tg, tb, ta = _parse_colour(tint)
        r, g, b = _blend(r, g, b, tr, tg, tb, ta, blend_mode)
    data[offset], data[offset+1], data[offset+2] = b, g, r

def _set_bgra(data: bytearray, offset: int, colour: str):
    r, g, b, a = _parse_colour(colour)
    data[offset], data[offset+1], data[offset+2], data[offset+3] = b, g, r, a

def _adjust_environments(data: bytearray, kw: dict, fog_tint=None,
                         fog_end=None, fog_start=None) -> int:
    count = 0
    for type_code, start, size, data_start in _parse_sections(bytes(data)):
        if type_code != SECTION_TYPE_ENVIRONMENT:
            continue
        for name, offset in _ENV_COLOUR_OFFSETS.items():
            _adjust_bgra(data, data_start + offset, **kw)
        if fog_tint:
            _set_bgra(data, data_start + 0x18, fog_tint)
            _set_bgra(data, data_start + 0x38, fog_tint)
        if fog_end is not None:
            struct.pack_into('<f', data, data_start + 0x1C, fog_end)
            struct.pack_into('<f', data, data_start + 0x3C, fog_end)
        if fog_start is not None:
            struct.pack_into('<f', data, data_start + 0x20, fog_start)
            struct.pack_into('<f', data, data_start + 0x40, fog_start)
        count += 1
    return count


# ---------------------------------------------------------------------------
# Entity model scaling (skeleton / mesh / animation sections)
# Used by recolour_zone_dat when scale != 1.0 and the DAT contains entity
# sections (0x29 skeleton, 0x2A mesh, 0x2B animation).
# ---------------------------------------------------------------------------

def _scale_skeleton(data: bytearray, data_start: int, scale: float) -> int:
    """Scale all bone translations in a skeleton section (0x29)."""
    num_joints = data[data_start + 0x02]
    joints_start = data_start + 0x04
    for i in range(num_joints):
        base = joints_start + i * 30 + 18
        for j in range(3):
            off = base + j * 4
            val = struct.unpack_from('<f', data, off)[0]
            struct.pack_into('<f', data, off, val * scale)
    return num_joints


def _scale_mesh(data: bytearray, data_start: int, scale: float) -> int:
    """Scale all vertex positions in a mesh section (0x2A)."""
    ds = data_start
    flags3 = data[ds + 0x02]
    cloth_effect = (flags3 & 0x01) != 0
    has_normals = not cloth_effect

    off = ds + 6
    struct.unpack_from('<I', data, off)[0]; off += 6
    struct.unpack_from('<I', data, off)[0]; off += 6
    vc_off = struct.unpack_from('<I', data, off)[0] * 2; off += 6
    struct.unpack_from('<I', data, off)[0]; off += 6
    vd_off = struct.unpack_from('<I', data, off)[0] * 2

    single_count = struct.unpack_from('<H', data, ds + vc_off)[0]
    double_count = struct.unpack_from('<H', data, ds + vc_off + 2)[0]
    vertex_base = ds + vd_off
    scaled = 0

    stride_single = 24 if has_normals else 12
    for i in range(single_count):
        base = vertex_base + i * stride_single
        for j in range(3):
            foff = base + j * 4
            val = struct.unpack_from('<f', data, foff)[0]
            struct.pack_into('<f', data, foff, val * scale)
        scaled += 1

    stride_double = 56 if has_normals else 32
    double_base = vertex_base + single_count * stride_single
    for i in range(double_count):
        base = double_base + i * stride_double
        for j in range(6):
            foff = base + j * 4
            val = struct.unpack_from('<f', data, foff)[0]
            struct.pack_into('<f', data, foff, val * scale)
        scaled += 1

    return scaled


def _scale_animation(data: bytearray, data_start: int, scale: float) -> int:
    """Scale all translation keyframes in an animation section (0x2B)."""
    ds = data_start
    num_joints = struct.unpack_from('<H', data, ds + 2)[0]
    num_frames = struct.unpack_from('<H', data, ds + 4)[0]
    kf_off = ds + 10

    scaled_channels = 0
    cursor = kf_off

    for _ in range(num_joints):
        cursor += 4   # joint_index
        cursor += 32  # rotation (4 offsets + 4 consts)

        trans_start = cursor
        for ch in range(3):
            offset_pos = trans_start + ch * 4
            const_pos  = trans_start + 12 + ch * 4
            offset_val = struct.unpack_from('<i', data, offset_pos)[0]

            if offset_val < 0:
                continue
            elif offset_val == 0:
                val = struct.unpack_from('<f', data, const_pos)[0]
                struct.pack_into('<f', data, const_pos, val * scale)
                scaled_channels += 1
            else:
                arr_base = kf_off + offset_val * 4
                for frame in range(num_frames):
                    foff = arr_base + frame * 4
                    if foff + 4 <= len(data):
                        val = struct.unpack_from('<f', data, foff)[0]
                        struct.pack_into('<f', data, foff, val * scale)
                val = struct.unpack_from('<f', data, const_pos)[0]
                struct.pack_into('<f', data, const_pos, val * scale)
                scaled_channels += 1

        cursor += 24  # translation
        cursor += 24  # scale

    return scaled_channels


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def recolour_zone_dat(source: Path, output: Path, hue=0, saturation=0,
                      lightness=0, tint=None, blend_mode='normal',
                      hue_min=None, hue_max=None, sat_min=None, sat_max=None,
                      val_min=None, val_max=None,
                      env_lightness=None, fog_tint=None,
                      fog_end=None, fog_start=None,
                      scale=0) -> dict:
    """Recolour all textures and optionally adjust environment lighting/scale.

    Works on any FFXI DAT containing 0x20 texture sections (entity, gear, or
    zone models). Writes the result to *output* (creating parent dirs as needed).

    Returns ``{'dxt': N, 'paletted': N, 'environment': N, 'total': N,
               'joints_scaled': N, 'vertices_scaled': N, 'anims_scaled': N}``.

    scale: Uniform model scale factor (e.g. 2.0 = double size). Scales
    skeleton bone translations, mesh vertex positions, and animation
    translation keyframes. Works best with grounded entity models.
    """
    data = bytearray(source.read_bytes())
    kw = dict(hue=hue, saturation=saturation, lightness=lightness,
              tint=tint, blend_mode=blend_mode,
              hue_min=hue_min, hue_max=hue_max, sat_min=sat_min, sat_max=sat_max,
              val_min=val_min, val_max=val_max)
    stats = {'dxt': 0, 'paletted': 0, 'environment': 0, 'total': 0,
             'joints_scaled': 0, 'vertices_scaled': 0, 'anims_scaled': 0}

    do_scale = scale and scale != 1.0

    for type_code, start, size, data_start in _parse_sections(bytes(data)):
        if type_code == SECTION_TYPE_TEXTURE:
            info = _parse_texture_info(bytes(data), data_start)
            if info is None:
                continue
            tex_type, dxt_type, doff, dsize, bit_count = info
            stats['total'] += 1
            if tex_type == 0xA1 and dxt_type in ('1TXD', '3TXD'):
                _adjust_dxt(data, dxt_type, doff, dsize, **kw)
                stats['dxt'] += 1
            elif tex_type in (0x91, 0xB1):
                _adjust_palette(data, doff, dsize, bit_count, **kw)
                stats['paletted'] += 1

        elif type_code == 0x29 and do_scale:
            stats['joints_scaled'] += _scale_skeleton(data, data_start, scale)

        elif type_code == 0x2A and do_scale:
            stats['vertices_scaled'] += _scale_mesh(data, data_start, scale)

        elif type_code == 0x2B and do_scale:
            stats['anims_scaled'] += _scale_animation(data, data_start, scale)

    env_kw = dict(kw)
    if env_lightness is not None:
        env_kw['lightness'] = env_lightness
    if any([env_kw.get('hue'), env_kw.get('saturation'), env_kw.get('lightness'),
            env_kw.get('tint'), fog_tint, fog_end is not None, fog_start is not None]):
        stats['environment'] = _adjust_environments(
            data, env_kw, fog_tint=fog_tint, fog_end=fog_end, fog_start=fog_start)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(bytes(data))
    return stats

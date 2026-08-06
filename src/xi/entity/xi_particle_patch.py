"""Patch particle generator parameters in weapon/entity DATs — Loxley

Modifies particle opcode fields in-place within a DAT's binary data.
Supports per-emitter overrides for color, scale, speed, emission rate, etc.

Used by both xi-tools CLI and LPS config system.

Override format (per emitter):
    {
        "id": "gp00",            # generator section ID to match
        "enabled": true,         # false = zero out emission (hide)
        "overrides": {
            "hue": 180,          # hue shift in degrees
            "scale": 1.5,        # multiply scale XYZ
            "speed": 2.0,        # multiply velocity XYZ
            "emissionRate": 0.5, # multiply emission frequency
            "lifetime": 2.0,     # multiply particle lifetime
            "spread": 1.5,       # multiply position variance radius
            "color": [1.0, 0.5, 0.5],  # RGB multipliers
            "alpha": 0.8,        # alpha multiplier
        }
    }
"""

import struct
import colorsys
import base64
import io


def _align16(v):
    return (v + 0xF) & ~0xF


def _parse_sections(data: bytes) -> list:
    """Parse DAT section headers."""
    sections = []
    pos = 0
    while pos + 16 <= len(data):
        meta = struct.unpack_from('<I', data, pos + 4)[0]
        tc = meta & 0x7F
        sz = ((meta >> 7) & 0xFFFFF) * 0x10
        if sz <= 0:
            break
        sec_id = data[pos:pos + 4].decode('ascii', errors='replace').strip()
        sections.append({
            'id': sec_id,
            'type': tc,
            'start': pos,
            'size': sz,
            'data_start': pos + 0x10,
        })
        pos = _align16(pos + sz)
    return sections


def _walk_opcodes(data: bytes, start: int) -> list:
    """Walk opcode stream, yielding (pos, opcode, size, alloc, payload_start)."""
    ops = []
    pos = start
    for _ in range(256):
        if pos + 4 > len(data):
            break
        config = struct.unpack_from('<I', data, pos)[0]
        opc = config & 0xFF
        size = (config >> 8) & 0x1F
        alloc = config >> 0xD
        if opc == 0 or size == 0:
            break
        ops.append({
            'pos': pos,
            'opcode': opc,
            'size': size,
            'alloc': alloc,
            'payload': pos + 4,
            'payload_len': (size - 1) * 4,
        })
        pos += size * 4
    return ops


def _hue_shift_byte_color(data: bytearray, offset: int, hue_deg: float):
    """Shift hue of a 4-byte RGBA color at the given offset."""
    r, g, b, a = data[offset], data[offset + 1], data[offset + 2], data[offset + 3]
    if r == 0 and g == 0 and b == 0:
        return  # skip pure black
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    h = (h + hue_deg / 360) % 1.0
    nr, ng, nb = colorsys.hsv_to_rgb(h, s, v)
    data[offset] = int(nr * 255)
    data[offset + 1] = int(ng * 255)
    data[offset + 2] = int(nb * 255)


def _multiply_float3(data: bytearray, offset: int, multiplier: float):
    """Multiply 3 consecutive float32 values by a scalar."""
    for i in range(3):
        off = offset + i * 4
        val = struct.unpack_from('<f', data, off)[0]
        struct.pack_into('<f', data, off, val * multiplier)


def _multiply_float(data: bytearray, offset: int, multiplier: float):
    """Multiply a single float32 value by a scalar."""
    val = struct.unpack_from('<f', data, offset)[0]
    struct.pack_into('<f', data, offset, val * multiplier)


def patch_particle_overrides(data: bytes, overrides: list) -> bytes:
    """Apply particle overrides to a DAT's binary data.

    Args:
        data: raw DAT bytes
        overrides: list of per-emitter override dicts

    Returns:
        modified DAT bytes
    """
    if not overrides:
        return data

    result = bytearray(data)
    sections = _parse_sections(data)

    # Build lookup: generator_id → override config
    override_map = {}
    for ov in overrides:
        ov_id = ov.get('id', '')
        if ov_id:
            override_map[ov_id] = ov

    # Process each particle generator section (type 0x05)
    for sec in sections:
        if sec['type'] != 0x05:
            continue
        if sec['id'] not in override_map:
            continue

        ov = override_map[sec['id']]
        params = ov.get('overrides', {})
        enabled = ov.get('enabled', True)
        ss = sec['start']

        # ── Disable emitter by zeroing emission ──
        if not enabled:
            # Set framesPerEmission to max (virtually never emits)
            struct.pack_into('<H', result, ss + 0x76, 0xFFFE)
            continue

        # ── Emission rate override ──
        rate = params.get('emissionRate')
        if rate and rate != 1:
            fpe = struct.unpack_from('<H', data, ss + 0x76)[0]
            new_fpe = max(0, int(fpe / rate))
            struct.pack_into('<H', result, ss + 0x76, min(0xFFFE, new_fpe))

        # ── Lifetime override ──
        # Lifetime is in StandardSetup opcode (0x01) payload at offset 30-31
        lifetime_mult = params.get('lifetime')

        # ── Walk sec2 opcodes for initializer patches ──
        sec2_off = struct.unpack_from('<I', data, ss + 0x84)[0]
        if sec2_off > 0:
            sec2_ops = _walk_opcodes(data, ss + sec2_off)
            for op in sec2_ops:
                p = op['payload']

                # StandardSetup (0x01) — lifetime at payload[30:32]
                if op['opcode'] == 0x01 and lifetime_mult and lifetime_mult != 1:
                    if op['payload_len'] >= 32:
                        life = struct.unpack_from('<H', data, p + 30)[0]
                        if life > 0:
                            new_life = max(1, int(life * lifetime_mult))
                            struct.pack_into('<H', result, p + 30, min(0xFFFF, new_life))

                # TranslationVelocity (0x02) — 3 floats
                if op['opcode'] == 0x02:
                    speed = params.get('speed')
                    if speed and speed != 1:
                        _multiply_float3(result, p, speed)

                # VelocityVariance (0x03) — 3 floats
                if op['opcode'] == 0x03:
                    speed = params.get('speed')
                    if speed and speed != 1:
                        _multiply_float3(result, p, speed)

                # SphPosVarSimple (0x06) — radiusVariance(f32), baseRadius(f32)
                if op['opcode'] == 0x06:
                    spread = params.get('spread')
                    if spread and spread != 1:
                        _multiply_float(result, p, spread)      # radiusVariance
                        _multiply_float(result, p + 4, spread)  # baseRadius

                # SphPosVarMedium (0x07) — same first 2 floats
                if op['opcode'] == 0x07:
                    spread = params.get('spread')
                    if spread and spread != 1:
                        _multiply_float(result, p, spread)
                        _multiply_float(result, p + 4, spread)

                # RelativeVelocity (0x08) — 1 float
                if op['opcode'] == 0x08:
                    speed = params.get('speed')
                    if speed and speed != 1:
                        _multiply_float(result, p, speed)

                # Scale (0x0F) — 3 floats
                if op['opcode'] == 0x0F:
                    scale = params.get('scale')
                    if scale and scale != 1:
                        _multiply_float3(result, p, scale)

                # ScaleVariance (0x10) — 3 floats
                if op['opcode'] == 0x10:
                    scale = params.get('scale')
                    if scale and scale != 1:
                        _multiply_float3(result, p, scale)

                # ScaleVelocity (0x12) — 3 floats
                if op['opcode'] == 0x12:
                    scale = params.get('scale')
                    if scale and scale != 1:
                        _multiply_float3(result, p, scale)

                # Color (0x16) — 4 bytes RGBA
                if op['opcode'] == 0x16 and op['payload_len'] >= 4:
                    hue = params.get('hue')
                    if hue and hue != 0:
                        _hue_shift_byte_color(result, p, hue)
                    color = params.get('color')
                    if color:
                        result[p] = min(255, int(result[p] * color[0]))
                        result[p + 1] = min(255, int(result[p + 1] * color[1]))
                        result[p + 2] = min(255, int(result[p + 2] * color[2]))
                    alpha = params.get('alpha')
                    if alpha is not None and alpha != 1:
                        result[p + 3] = min(255, int(result[p + 3] * alpha))

                # SphPosVarFull (0x1F) — first 2 floats are radius
                if op['opcode'] == 0x1F:
                    spread = params.get('spread')
                    if spread and spread != 1:
                        _multiply_float(result, p, spread)
                        _multiply_float(result, p + 4, spread)

    # ── Custom texture replacement ──
    for ov in overrides:
        custom_tex = ov.get('custom_texture')
        if not custom_tex:
            continue
        gen_id = ov.get('id', '')
        if not gen_id:
            continue

        # Find which texture section this generator references
        tex_id = _find_generator_texture_id(data, sections, gen_id)
        if not tex_id:
            continue

        # Find the texture section
        tex_sec = next((s for s in sections if s['type'] == 0x20 and s['id'] == tex_id), None)
        if not tex_sec:
            continue

        # Decode the PNG
        png_b64 = custom_tex.get('png_base64', '')
        if not png_b64:
            continue
        rgba = _decode_png_to_rgba(png_b64, custom_tex.get('width', 64), custom_tex.get('height', 64))
        if not rgba:
            continue

        # Replace texture data in-place
        _replace_texture_data(result, tex_sec, rgba, custom_tex.get('width', 64), custom_tex.get('height', 64))

    return bytes(result)


def _find_generator_texture_id(data: bytes, sections: list, gen_id: str) -> str | None:
    """Find the texture section ID referenced by a generator's StandardSetup."""
    for sec in sections:
        if sec['type'] != 0x05 or sec['id'] != gen_id:
            continue
        ss = sec['start']
        sec2_off = struct.unpack_from('<I', data, ss + 0x84)[0]
        if sec2_off <= 0:
            continue
        ops = _walk_opcodes(data, ss + sec2_off)
        for op in ops:
            if op['opcode'] == 0x01 and op['payload_len'] >= 12:
                # StandardSetup: linkedDataId at payload[8:12]
                p = op['payload']
                tex_id = data[p + 8:p + 12].decode('ascii', errors='replace').strip().replace('\x00', '')
                if tex_id:
                    return tex_id
    return None


def _decode_png_to_rgba(png_b64: str, w: int, h: int) -> bytes | None:
    """Decode base64 PNG to RGBA bytes, scaled to w×h."""
    try:
        from PIL import Image
        png_data = base64.b64decode(png_b64)
        img = Image.open(io.BytesIO(png_data)).convert('RGBA')
        img = img.resize((w, h), Image.Resampling.LANCZOS)
        return img.tobytes()
    except ImportError:
        # Fallback without PIL — try raw decoding
        return None
    except Exception:
        return None


def _replace_texture_data(data: bytearray, tex_sec: dict, rgba: bytes, w: int, h: int):
    """Replace a texture section's pixel data with new RGBA data.

    Handles both paletted (0x91/0xB1) and DXT (0xA1) textures.
    For DXT, overwrites with paletted format if section is large enough.
    For paletted, quantizes to 256 colors and writes palette + indices.
    """
    ds = tex_sec['data_start']
    tex_type = data[ds]

    if tex_type in (0x91, 0xB1):
        # Paletted texture — replace palette + pixel indices
        header_size = struct.unpack_from('<I', data, ds + 17)[0]
        pal_off = ds + 17 + header_size
        pix_off = pal_off + 1024

        # Check we have enough space
        needed = pix_off + w * h - tex_sec['start']
        if needed > tex_sec['size']:
            return  # can't fit

        palette, indices = _quantize_to_palette(rgba, w, h)

        # Write palette (BGRA, alpha capped at 128)
        for i in range(256):
            po = pal_off + i * 4
            r, g, b, a = palette[i]
            data[po] = b
            data[po + 1] = g
            data[po + 2] = r
            data[po + 3] = min(128, a // 2)

        # Write pixel indices
        for i in range(w * h):
            data[pix_off + i] = indices[i]

    elif tex_type == 0xA1:
        # DXT texture — replace the DXT block data
        header_size = struct.unpack_from('<I', data, ds + 17)[0]
        dxt_off = ds + 17 + header_size
        magic = data[dxt_off:dxt_off + 4]

        if magic == b'1TXD':
            blocks = _encode_dxt1(rgba, w, h)
            block_off = dxt_off + 12
            for i, b in enumerate(blocks):
                if block_off + i < len(data):
                    data[block_off + i] = b
        elif magic == b'3TXD':
            blocks = _encode_dxt3(rgba, w, h)
            block_off = dxt_off + 12
            for i, b in enumerate(blocks):
                if block_off + i < len(data):
                    data[block_off + i] = b


def _quantize_to_palette(rgba: bytes, w: int, h: int) -> tuple:
    """Quantize RGBA image to 256-color palette. Returns (palette, indices)."""
    try:
        from PIL import Image
        img = Image.frombytes('RGBA', (w, h), rgba)
        # Quantize preserving alpha
        rgb = img.convert('RGB')
        quantized = rgb.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
        pal_data = quantized.getpalette()
        indices_img = quantized.convert('P')

        palette = []
        for i in range(256):
            if pal_data and i * 3 + 2 < len(pal_data):
                r, g, b = pal_data[i*3], pal_data[i*3+1], pal_data[i*3+2]
            else:
                r = g = b = 0
            # Find average alpha for this palette entry
            palette.append((r, g, b, 255))

        # Get indices
        idx_data = list(quantized.tobytes())

        # Fix alpha: set palette alpha from source image
        alpha_sums = [0] * 256
        alpha_counts = [0] * 256
        for i in range(w * h):
            ci = idx_data[i]
            alpha_sums[ci] += rgba[i * 4 + 3]
            alpha_counts[ci] += 1
        for i in range(256):
            if alpha_counts[i] > 0:
                avg_a = alpha_sums[i] // alpha_counts[i]
                palette[i] = (palette[i][0], palette[i][1], palette[i][2], avg_a)

        return palette, idx_data

    except ImportError:
        # Simple fallback without PIL
        palette = [(0, 0, 0, 0)] * 256
        indices = [0] * (w * h)
        return palette, indices


def _encode_dxt1(rgba: bytes, w: int, h: int) -> bytes:
    """Simple DXT1 encoder."""
    bw, bh = w // 4, h // 4
    out = bytearray()

    for by in range(bh):
        for bx in range(bw):
            # Extract 4x4 block
            pixels = []
            for py in range(4):
                for px in range(4):
                    i = ((by * 4 + py) * w + (bx * 4 + px)) * 4
                    pixels.append((rgba[i], rgba[i+1], rgba[i+2], rgba[i+3]))

            # Find min/max colors
            min_r = min_g = min_b = 255
            max_r = max_g = max_b = 0
            for r, g, b, a in pixels:
                if r < min_r: min_r = r
                if g < min_g: min_g = g
                if b < min_b: min_b = b
                if r > max_r: max_r = r
                if g > max_g: max_g = g
                if b > max_b: max_b = b

            # Pack to RGB565
            c0 = ((max_r >> 3) << 11) | ((max_g >> 2) << 5) | (max_b >> 3)
            c1 = ((min_r >> 3) << 11) | ((min_g >> 2) << 5) | (min_b >> 3)
            if c0 < c1:
                c0, c1 = c1, c0
                max_r, min_r = min_r, max_r
                max_g, min_g = min_g, max_g
                max_b, min_b = min_b, max_b

            out += struct.pack('<HH', c0, c1)

            # Compute indices
            lut = [
                (max_r, max_g, max_b),
                (min_r, min_g, min_b),
                ((2*max_r+min_r)//3, (2*max_g+min_g)//3, (2*max_b+min_b)//3),
                ((max_r+2*min_r)//3, (max_g+2*min_g)//3, (max_b+2*min_b)//3),
            ]

            bits = 0
            for pi, (r, g, b, a) in enumerate(pixels):
                best = 0
                best_dist = 999999
                for ci, (lr, lg, lb) in enumerate(lut):
                    d = (r-lr)**2 + (g-lg)**2 + (b-lb)**2
                    if d < best_dist:
                        best_dist = d
                        best = ci
                bits |= (best << (pi * 2))

            out += struct.pack('<I', bits)

    return bytes(out)


def _encode_dxt3(rgba: bytes, w: int, h: int) -> bytes:
    """Simple DXT3 encoder (DXT1 color + explicit 4-bit alpha)."""
    bw, bh = w // 4, h // 4
    out = bytearray()

    for by in range(bh):
        for bx in range(bw):
            pixels = []
            for py in range(4):
                for px in range(4):
                    i = ((by * 4 + py) * w + (bx * 4 + px)) * 4
                    pixels.append((rgba[i], rgba[i+1], rgba[i+2], rgba[i+3]))

            # Write 8 bytes of alpha (4 bits per pixel, 16 pixels)
            for row in range(4):
                a0 = pixels[row * 4 + 0][3] >> 4
                a1 = pixels[row * 4 + 1][3] >> 4
                a2 = pixels[row * 4 + 2][3] >> 4
                a3 = pixels[row * 4 + 3][3] >> 4
                out += struct.pack('<BB', a0 | (a1 << 4), a2 | (a3 << 4))

            # DXT1 color block (same as above)
            min_r = min_g = min_b = 255
            max_r = max_g = max_b = 0
            for r, g, b, a in pixels:
                if r < min_r: min_r = r
                if g < min_g: min_g = g
                if b < min_b: min_b = b
                if r > max_r: max_r = r
                if g > max_g: max_g = g
                if b > max_b: max_b = b

            c0 = ((max_r >> 3) << 11) | ((max_g >> 2) << 5) | (max_b >> 3)
            c1 = ((min_r >> 3) << 11) | ((min_g >> 2) << 5) | (min_b >> 3)
            if c0 < c1:
                c0, c1 = c1, c0
                max_r, min_r = min_r, max_r
                max_g, min_g = min_g, max_g
                max_b, min_b = min_b, max_b

            out += struct.pack('<HH', c0, c1)

            lut = [
                (max_r, max_g, max_b),
                (min_r, min_g, min_b),
                ((2*max_r+min_r)//3, (2*max_g+min_g)//3, (2*max_b+min_b)//3),
                ((max_r+2*min_r)//3, (max_g+2*min_g)//3, (max_b+2*min_b)//3),
            ]

            bits = 0
            for pi, (r, g, b, a) in enumerate(pixels):
                best = 0
                best_dist = 999999
                for ci, (lr, lg, lb) in enumerate(lut):
                    d = (r-lr)**2 + (g-lg)**2 + (b-lb)**2
                    if d < best_dist:
                        best_dist = d
                        best = ci
                bits |= (best << (pi * 2))

            out += struct.pack('<I', bits)

    return bytes(out)


def apply_effects_source(target_dat: bytes, effects_dat: bytes) -> bytes:
    """Replace target DAT's particle effects with those from effects_dat.

    Copies the effects block (particles, keyframes, effect textures,
    particle meshes, sprite sheets, effect routines) from effects_dat
    and appends it to the target's weapon mesh sections.

    FFXI weapon DATs have this structure:
        [weapon block] HDR → Texture → Mesh → ... → END
        [effects block] HDR → Particles → Routines → KeyFrames → Textures → ... → END
        [final END]

    The effects block is identified by containing Particle sections (0x05).

    Args:
        target_dat: weapon DAT to receive effects
        effects_dat: source DAT to copy effects from

    Returns:
        new DAT bytes with target's mesh + source's effects
    """
    target_secs = _parse_sections(target_dat)
    source_secs = _parse_sections(effects_dat)

    # ── Keep target sections up to and including the first END ──
    keep_chunks = []
    for sec in target_secs:
        keep_chunks.append(target_dat[sec['start']:_align16(sec['start'] + sec['size'])])
        if sec['type'] == 0x00:
            break

    # ── Find the effects block in the source DAT ──
    # The effects block starts at the HDR that precedes the first Particle (0x05) section.
    # Find the first Particle section index.
    first_particle_idx = None
    for i, sec in enumerate(source_secs):
        if sec['type'] == 0x05:
            first_particle_idx = i
            break

    if first_particle_idx is None:
        return target_dat  # source has no particles

    # Walk backwards from the first Particle to find the HDR that starts the block
    effects_start_idx = first_particle_idx
    for i in range(first_particle_idx - 1, -1, -1):
        if source_secs[i]['type'] == 0x01:  # HDR
            effects_start_idx = i
            break
        elif source_secs[i]['type'] == 0x00:  # END (boundary)
            break

    # Copy from effects_start_idx to the next END (or end of file)
    effect_chunks = []
    for i in range(effects_start_idx, len(source_secs)):
        sec = source_secs[i]
        effect_chunks.append(effects_dat[sec['start']:_align16(sec['start'] + sec['size'])])
        if sec['type'] == 0x00 and i > effects_start_idx:
            break

    # ── Build new DAT ──
    result = bytearray()
    for chunk in keep_chunks:
        result.extend(chunk)
    for chunk in effect_chunks:
        result.extend(chunk)

    # Ensure final END
    last_meta = struct.unpack_from('<I', result, len(result) - 12)[0]
    if (last_meta & 0x7F) != 0x00:
        result.extend(b'end \x80\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00')

    return bytes(result)

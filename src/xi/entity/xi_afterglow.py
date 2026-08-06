"""Build afterglow (particle aura) weapon DATs.

Extracts the relic weapon afterglow particle system from a template DAT
(Ragnarok AG, gear model 546) and wraps any gear weapon DAT with it.
The aura color is configurable via RGB multipliers applied to the particle
textures and generator float parameters.

The afterglow system uses:
- 0x05 ParticleGenerator sections (gp00, fr00) — the glow particles
- 0x19 KeyFrame sections (g0ca, f0ca) — animation curves
- 0x1F ParticleMesh sections (pi1, flm) — particle geometry
- 0x20 Texture sections (pi1, flmy) — paletted particle textures (0xB1)
- 0x07 EffectRoutine sections — trigger/lifecycle management

Color is controlled by:
1. Tinting the 256-entry BGRA palettes in both particle textures
2. Modifying the RGB float multipliers in both ParticleGenerator sections

The aura block is self-contained: container_hdr + aura_front + [weapon DAT] + aura_tail.
The weapon DAT is embedded as-is with its own HDR — no modification needed.
"""

import struct
from pathlib import Path
from typing import Dict

# Cache the template so we only read it once per session
_template_cache: Dict[str, bytes] = {}

# Offsets within the aura_front block (relative to aura_front start)
_PI1_PALETTE_OFF = (2304 - 32) + 16 + 57   # pi1 texture palette
_FLMY_PALETTE_OFF = (7504 - 32) + 16 + 57  # flmy texture palette
_GP00_DATA_OFF = 32 + 16                     # gp00 particle generator data
_FR00_DATA_OFF = (608 - 32) + 16             # fr00 particle generator data
_FLOAT_OFFSETS = [48, 64]                    # RGB float triplets in each generator


def load_template(ffxi_dir: str | Path) -> dict:
    """Load the afterglow template from Ragnarok AG (gear model 546).

    Returns dict with 'container_hdr', 'aura_front', 'aura_tail' bytes.
    """
    if _template_cache:
        return _template_cache

    ffxi_dir = Path(ffxi_dir)

    # Try resolving via gear export
    try:
        from xi.gear.xi_export import resolve_gear_dat
        ag_path = resolve_gear_dat('HumeMale', 'main', 546)
    except Exception:
        ag_path = ffxi_dir / 'ROM' / '273' / '13.DAT'

    ag_data = ag_path.read_bytes()
    _template_cache['container_hdr'] = ag_data[0:32]
    _template_cache['aura_front'] = ag_data[32:12720]
    _template_cache['aura_tail'] = ag_data[25984:]
    return _template_cache


def build_afterglow_dat(weapon_dat: bytes, r: float = 1.0, g: float = 1.0,
                        b: float = 1.0, ffxi_dir: str | Path = None) -> bytes:
    """Wrap a gear weapon DAT with a colored afterglow particle aura.

    Args:
        weapon_dat: The original weapon DAT bytes (complete, with HDR).
        r, g, b: Color multipliers (0.0-1.0). 1.0/1.0/1.0 = white (default).
        ffxi_dir: FFXI install directory (for loading the template).

    Returns:
        New DAT bytes: container_hdr + colored_aura + weapon_dat + aura_tail.
    """
    import os
    if ffxi_dir is None:
        ffxi_dir = os.environ.get('FFXI_DIR', '')

    template = load_template(ffxi_dir)
    aura_front = bytearray(template['aura_front'])

    # Tint particle palettes
    for pal_off in [_PI1_PALETTE_OFF, _FLMY_PALETTE_OFF]:
        for i in range(256):
            po = pal_off + i * 4
            if po + 4 > len(aura_front):
                break
            aura_front[po]     = min(255, int(aura_front[po]     * b))  # B
            aura_front[po + 1] = min(255, int(aura_front[po + 1] * g))  # G
            aura_front[po + 2] = min(255, int(aura_front[po + 2] * r))  # R

    # Tint particle generator float multipliers
    for gen_off in [_GP00_DATA_OFF, _FR00_DATA_OFF]:
        for float_base in _FLOAT_OFFSETS:
            off = gen_off + float_base
            if off + 12 <= len(aura_front):
                struct.pack_into('<fff', aura_front, off, r, g, b)

    return template['container_hdr'] + bytes(aura_front) + weapon_dat + template['aura_tail']


def parse_afterglow_color(color_str: str) -> tuple:
    """Parse a color string like '#ff3333' into (r, g, b) floats 0.0-1.0."""
    hex_color = color_str.lstrip('#')
    if len(hex_color) != 6:
        raise ValueError(f'Afterglow color must be #RRGGBB, got: {color_str}')
    return (
        int(hex_color[0:2], 16) / 255.0,
        int(hex_color[2:4], 16) / 255.0,
        int(hex_color[4:6], 16) / 255.0,
    )

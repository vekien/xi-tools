# Particle Editor

Web-based 3D weapon viewer with live particle effect preview and editing.
Renders FFXI weapon models with their particle effects in-browser using
three.js, with real-time parameter tweaking and export.

## Setup

The editor reads weapon DATs directly from the FFXI game directory via
the browser. You need to create a `game` symlink (or copy) pointing to
your FFXI installation, then generate the weapon index.

```bash
# 1. Link your FFXI game directory
cd web/particleeditor
ln -s /path/to/your/FFXI/installation game

# Example paths:
#   Linux (Ashita):  ln -s <FFXI_DIR> game
#   Linux (native):  ln -s /opt/ffxi game
#   WSL:             ln -s <FFXI_DIR> game

# The symlink should point to the directory containing ROM/, ROM2/, etc.
# Verify: ls game/ROM/0/0.DAT should exist

# 2. Generate the weapon index
uv run python gen_weapons.py

# 3. Start the server
python3 -m http.server 8090
```

Open `http://localhost:8090` in a browser.

## Regenerating the weapon index

The weapon dropdown is populated from `weapons.json`. Regenerate after
game updates or to pick up new weapon models:

```bash
uv run python web/particleeditor/gen_weapons.py
```

## Features

### Weapon viewer
- 3D weapon model rendering with orbit controls
- Texture tinting (hue, saturation, lightness, tint color)
- Background color and ambient lighting controls

### Particle preview
- Accurate particle system ported from FFXI's runtime
- Billboard types (XYZ, Camera, Movement)
- KeyFrame-driven animation curves for color, scale, position
- Child particle generators
- Sprite sheet particles
- Screen-space distortion effects (heat-haze post-processing)

### Editor controls
Per-emitter sliders:
- **Rate** — emission frequency multiplier
- **Life** — particle lifetime multiplier
- **Speed** — velocity multiplier
- **Scale** — particle size multiplier
- **Spread** — position variance radius multiplier
- **Hue** — hue shift (0-360 degrees)
- **Color** — RGB color multiplier
- **Alpha** — opacity multiplier

Each emitter shows a texture thumbnail for identification. Click the
thumbnail to replace it with a custom image (PNG, JPG, etc.). Right-click
the thumbnail to revert to the original texture. Custom textures are
included in the exported config as base64 PNG and baked into the final
DAT by xi-tools.

### Mix and match
Two independent dropdowns:
- **Model** — select the 3D weapon mesh to display
- **Effects** — select which particle effects to apply

Apply any weapon's particle effects onto any other weapon model.

### Weapon state
Toggle between **Sheathed** and **Unsheathed** to see which effects
activate for each weapon state.

### Export
The **Export Config** button downloads a JSON file containing all
current settings. This config is consumed by the hidden legacy gear-inject flow.

The exported config includes:
- Model source and slot
- Texture tinting (hue, saturation, lightness, tint)
- Effects source (if using particles from a different weapon)
- Per-emitter particle overrides

## Architecture

```
web/particleeditor/
  index.html          — app layout
  main.js             — three.js scene, editor UI, export
  style.css           — dark theme
  weapons.json        — weapon index (generated)
  gen_weapons.py      — weapon index generator
  ffxi/
    sections.js       — DAT section parser, texture decoder
    weapon.js         — weapon mesh parser (0x2A instruction stream)
    effects.js        — particle effect parser (generators, keyframes, routines)
    particle_runtime.js — particle system (initializers, updaters, rendering)
```

### Particle system

The runtime ports FFXI's particle generator architecture:

1. **Generator header** — emission rate, particle count, singleton mode
2. **Sec2 initializers** — set up each particle on emit (position, velocity,
   rotation, scale, color, blend mode, keyframe refs, child generators)
3. **Sec3 updaters** — modify particles each frame (movement, acceleration,
   dampening, color transform, keyframe-driven animation)
4. **Sec4 expiration** — cleanup when particles die

Supports ~40 opcode types across initializers and updaters, plus
child generator spawning and keyframe curve interpolation.

### Binary patching

The `xi_particle_patch.py` module modifies particle opcode fields
in-place within DAT binary data. Used by the legacy gear-inject flow when
`--particle-config` or `--effects-from` is specified.

### XIPivot overlay deployment

For quick testing without gear injection setup, the modified DAT can be
placed directly in the XIPivot overlay directory:

```bash
# Build and deploy to overlay (no FTABLE changes needed)
FFXI_DIR=/path/to/ffxi uv run python3 -c "
import json
from pathlib import Path
from xi.entity.xi_particle_patch import patch_particle_overrides, apply_effects_source
from xi.zone.xi_inject import recolour_zone_dat

config = json.loads(Path('my_config.json').read_text())
# ... recolor, swap effects, patch particles ...
# Write to overlay
dst = Path('/path/to/Ashita/polplugins/DATs/ffxi-hd/ROM/120/6.DAT')
dst.parent.mkdir(parents=True, exist_ok=True)
dst.write_bytes(dat)
"
```

Restart the game client to load the modified DAT.

### Requirements

- **Pillow** (`uv add Pillow`) — required for custom texture encoding.
  Without it, custom texture replacement silently skips.

Pipeline order:
1. Recolor textures (hue/saturation/lightness/tint)
2. Swap effects source (`--effects-from`)
3. Afterglow wrapper (`--afterglow`)
4. Patch particle emitters + custom textures (`--particle-config`)
5. Place in ROM10/overlay and register

# xi-tools

DAT modding tools for FFXI private servers — entity models, zone geometry, gear, visual effects, collision, UI textures, and more.

---

## Setup

1. Install Python 3.11+ (3.14 recommended)
2. Install `uv`: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
3. Sync dependencies: `uv sync`
4. Install the CLI entry point (so you can type `xi` instead of `uv run xi`):

```bash
uv tool install -e .
xi --help
```

5. Set `FFXI_DIR` to your `FINAL FANTASY XI` install folder (see Environment
   Variables below). It is required — there is no default.

**Optional — 3D model editing:** Install [Blender](https://www.blender.org/download/) and set the `BLENDER_PATH` env var (needed for FBX export only; GLB works without it).

---

## Environment Variables

| Variable | Purpose |
|---|---|
| `FFXI_DIR` | FFXI game install — tools read AND write here (edits go in place; every edited DAT keeps a pristine `<dat>.base` backup) |
| `BLENDER_PATH` | Path to `blender.exe` — only needed for `--fbx` exports |
| `CUSTOM_FTABLE` | ROM name for the custom namespace (default: `ROM10`) |
| `XI_VGMSTREAM` | Path to `vgmstream-cli` — only needed to decode ATRAC3 audio (`xi audio`); auto-detected from `PATH` otherwise |
| `XI_NAVMESH_DIR` | Directory of server navmesh `.nav` files — shown as a green walkable-area overlay in the zone level editor. Auto-detected from `../xi-server/xiNavmeshes` if present. |

---

## Features

- Custom FFXI Zone Editor: create new zones, edit existing zones, move/clone/import objects, tune VFX/weather, and export JSON change sets.
- Collision and navigation tools: inspect collision, append blockers, bake navmeshes, and preview server walkable areas.
- Model and animation tools: export/import meshes, textures, skeletons, and animations, including new or edited animation tracks.
- Gear and character tools: find gear DATs, edit gear textures, import/export gear models, and assemble full characters from `look` data.
- Visual effects tools: inspect, edit, copy, delete, and export particle/light generators for lamps, fire, fountains, spells, and ambience.
- DAT packaging: build reproducible standard/HD DAT packages with manifests, locked allocations, client files, and server resources.
- Audio tools: search, catalog, decode, import, install music/SFX, and inspect DAT sound references.
- UI and item tools: edit UI textures, strings, item records, icons, spells, layout data, and DDS/PNG assets.
- Event and dialogue tools: inspect cutscenes, find actors, author dialogue, and edit/reset dialogue tables.
- Misc/server tools: discover unused zones, stage server scaffolding, inspect server DB/status, unpack `FFXiMain.dll`, and run batch jobs.

---

## Zone Editor

A browser-based level editor for zone placements, served directly from this repo:

```bash
xi gui zone
# → opens http://localhost:8777/ automatically
```

**Features:**
- Loads zone DATs directly — reads your `FFXI_DIR` install, no export step needed
- 3D viewport with fly camera, gizmos for move / rotate / scale
- Multi-select with copy / paste support
- Snap controls for grid-aligned placement
- Object badge styling and visibility overrides (persisted across sessions)
- Weather, Time-of-Day, and VFX (fog/atmosphere) controls
- **Collision mesh overlay** — shows player-blocking geometry (red = wall/impassable, colour-coded floors by terrain type); opacity slider + isolate mode
- **Navmesh overlay** — shows server walkable area as a green mesh from the zone's `.nav` file; requires `XI_NAVMESH_DIR` or a baked `.nav` from `xi zone navmesh`
- **Settings panes**: Viewport (grid, wireframe, cell wireframe, sky, VFX icons), Collision (overlay opacity, isolate), Selection (outline toggles), Transformation (uniform scale, snap), Auto Save, Import (GLB import scale for C4D / non-FFXI-unit models)
- **Export JSON → `xi zone import-json`** to write changes back to the DAT in one command

### Projects & Workspaces

Editor work is organised into **projects**, stored in a workspaces folder you
pick on your own machine. Whether that folder is backed by git, Dropbox, or
nothing at all is up to you — the tools don't manage or enforce it.

- The **Projects launcher** lists projects from the folder's `projects.json`. **New Project** creates one; **Browse Zones** opens any zone read-only with no project.
- Opening a project points all editor reads/writes at that project's folder (`<project_id>/<zone>/zone-changes.json` + imported GLBs + version snapshots). The **Project Zones** panel (Zone tab) lists every zone you've edited in it.
- Only sources live there (change-sets, GLBs, version snapshots) — baked DATs are rebuilt on Publish. Per-user view-state (object locks, grouping, UI prefs) stays local in `editor.json`.

---

## Particle Editor

A browser-based 3D weapon viewer and live particle effect editor for FFXI weapons:

```bash
xi gui weapon
# → opens http://localhost:8776/ automatically
```

The `game/` symlink into your `FFXI_DIR` is created automatically on first run (requires Administrator / Developer Mode on Windows).  To rebuild the weapon index after a game update:

```bash
uv run python web/particleeditor/gen_weapons.py
```

**Features:**
- 800+ searchable weapons loaded directly from game DATs — no export step needed
- Accurate FFXI particle system rendered in three.js — glow, fire, blade spark, distortion, and more
- Separate **Model** and **Effects** dropdowns — mix any weapon's particle effects onto any weapon's mesh
- Per-emitter live controls: rate, lifetime, speed, scale, spread, hue, RGB color, alpha
- Texture replacement: click an emitter's texture thumbnail to swap in a custom PNG; right-click to revert
- Toggle **sheathed / unsheathed** states — autorun-only particles vs. full draw/idle sequence
- Model tinting: HSL sliders + color overlay blend for full recolor preview in the viewport
- Advanced panels (collapsible): raw generator metadata, texture gallery with format info, effect routines, keyframe curves
- **Export Config** button → JSON downloaded and copied to clipboard; pass directly to `gear inject --particle-config` to bake customised effects into a new weapon DAT

---

## Documentation

Quick command cheat sheet: [QUICKY.md](QUICKY.md)

Full docs: [docs/README.md](docs/README.md)

Godot prototype notes: [research/godot.md](research/godot.md)

---

## Credits

- Built by: Vekien
- Advanced by: Loxley
- Team CatsEyeXI

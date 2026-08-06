# xi-tools

CLI toolkit for FFXI DAT modding on private servers — models, animations, zones,
gear, mounts, VFX, audio, UI, events, and packaging.

```text
xi <group> <command> …
```

Paths can be ROM-relative (e.g. `ROM/1/41`) and resolve against `FFXI_DIR`.
Edits write in place; each changed DAT keeps a pristine `<dat>.base` backup.

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

5. Copy [`.env.sample`](.env.sample) → `.env` and set at least `FFXI_DIR` (required — no default).

**Optional — 3D model editing:** Install [Blender](https://www.blender.org/download/) and set `BLENDER_PATH` (needed for FBX only; GLB works without it).

**Optional — navmesh bake:** build the native lib once under [`misc/tools/xi-navmesh`](misc/tools/xi-navmesh/README.md) (or use the bundled `src/xi/libs/xi_navmesh.dll`).

---

## Environment Variables

| Variable | Purpose |
|---|---|
| `FFXI_DIR` | FFXI game install — tools read **and** write here |
| `FFXI_HD_DIR` | HD asset-pack DAT root (publish-to-HD / HD preview) |
| `FFXI_PIVOT_DIR` | Ashita/XIPivot override DAT root (keeps F/V tables in sync) |
| `BLENDER_PATH` | Path to `blender.exe` — only for `--fbx` exports |
| `CUSTOM_FTABLE` | ROM name for the custom namespace (default: `ROM10`) |
| `XI_VGMSTREAM` | `vgmstream-cli` for ATRAC3 (`xi audio`); else from `PATH` |
| `XI_SERVER_DIR` | Local LSB/server checkout root (`xi zone new`, server helpers) |
| `XI_NAVMESH_DIR` | Directory of server `.nav` files |
| `XI_DB_*` | Optional MySQL settings for `xi server` |

See [`.env.sample`](.env.sample) for the full list.

---

## Features

### FTABLE / custom model space

- Expand base (+ pivot) FTABLE/VTABLE for custom **entity** and **gear** model ranges
- Lookup, range-scan, set/delete entries, reset from `.base` backups
- `xi model search` / `json` — registered modelids, free slots, DAT paths

### Mesh, animation, entities

- `xi mesh export` / `import` — glTF/GLB (+ optional FBX) round-trip for entity meshes
- `xi anim export` / `import` / `schedule` — skeletal clips, layering, `0x07` cutscene routines
- Entity inject, recolor, look-blob decode, NPC bake helpers

### Gear & mounts

- Search / export / import gear models; texture edit; character assemble from `look`
- Inject custom gear (incl. particle-config bake), import-json configs
- Mount search / export / import / delete

### Zones, objects, collision, navmesh

- Zone export / import / import-json / reset / search / json
- `xi zone new`, `make-template`, `scaffold-server`, `delete`, `footsteps`
- `xi zone navmesh` / `navmesh-info` — bake LSB-compatible `.nav` from collision
- Object export/import/replace/clone/delete/set-placement/swap-placement
- FX inspect/set/copy/delete/export (zone particle & light generators)

> **Note:** The browser **zone level editor** is no longer part of this repo. Use the CLI
> (`xi zone` / `xi object` / `xi fx`) and JSON change-sets here; the editor lives in its
> own project.

### DAT packaging

- `xi dats prepare` / `build` / `json` / `changelog` — project manifests, locked
  allocations, standard/HD client packages, reproducible builds

### Audio

- Search/catalog music & SFX, decode/encode BGW/SPW, install into the game tree
- DAT sound-reference inspection (`xi audio refs`)

### UI, items, spells, textures

- UI texture extract/import (`sx`/`si`), layout position tweaks
- Strings, spells, item tables (general/consumable/armor/weapon/mount/custom)
- Item icons; DDS ↔ PNG via `xi utils` / `xi tex`

### Events & dialogue

- Cutscene export/import; dialogue actors, search, edit, reset
- Event authoring / compile helpers under `xi event`

### Client / server utilities

- `xi ffximain` — unpack, text-dump, gear-groups / gear-patch
- `xi launcher ui-themes`
- `xi server` — DB queries / status when `XI_SERVER_DIR` + DB env are set
- `xi misc` — orphans, staging, scans, previews
- `xi batch` — bulk zone/audio/asset jobs

Command cheat sheet: **[QUICKY.md](QUICKY.md)**

---

## Quick examples

```text
# Reserve custom entity + gear model space (once per install)
xi ftable expand

# Entity mesh round-trip
xi mesh export ROM/351/102 --split-tex
xi mesh import ROM/351/102 path/to/edited.glb

# Package a custom mesh into the live install
xi dats prepare exports/mesh/rom/351/102/102_schema.json --project my_mod --replace
xi dats build my_mod

# Zone collision → server navmesh
xi zone navmesh ROM/1/41

# UI title screen textures (pivot override)
xi ui tex sx ROM/119/50.DAT --ffxi "%FFXI_PIVOT_DIR%"
xi ui tex si ROM/119/50.DAT --ffxi "%FFXI_PIVOT_DIR%"
```

---

## Documentation

| Doc | |
|-----|--|
| [QUICKY.md](QUICKY.md) | Full public CLI surface |
| [docs/README.md](docs/README.md) | Format docs + command deep-dives |
| [docs/common_crashes.md](docs/common_crashes.md) | Client crash diagnosis after publishes |

Related: **[xi-model-viewer](https://github.com/vekien/xi-model-viewer)** — WebGL2/Tauri asset browser (zones, NPCs, gear, VFX, audio, DAT inspector).

---

## Credits

- Built by: Vekien
- Advanced by: Loxley
- Team CatsEyeXI

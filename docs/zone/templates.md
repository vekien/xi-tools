# Zone templates (`xi zone new --template`)

Custom zones are stamped from a **template bundle** — a snapshot of a curated
custom zone, packaged with `xi zone make-template`:

```
xi zone make-template 401 --label "Snow Template"     # package zone 401 → prints a bundle id
xi zone new --template a3f9c2 --name "My Snow Zone"   # stamp a new zone from that bundle
```

`--template` is **required** and takes the bundle's 6-char hex id (printed by
`make-template`; the editor's New-Zone dropdown lists bundles by label).
Bundles live under `<workspaces-repo-root>/templates/<id>/` — `zone.dat` plus
optional `event.dat`/`dialog.dat`/`npc.dat`, `metadata.json`, and a `data.sql`
DB snapshot. The source must be a **custom zone (id ≥ 400)**; to template an
original FFXI zone, Duplicate it first, then run `make-template` on the copy.
`--from-pristine` snapshots the untouched `<dat>.base` backup instead of your
edited DAT. Implementation:
[`src/xi/zone/xi_make_template.py`](../../src/xi/zone/xi_make_template.py).

## Why an outdoor base is required for the sky

The retail client renders a zone's sky from data **inside the zone DAT** (the `0x2F` env +
sky meshes under the `weat/` directory subtree) **but only treats the zone as outdoor when it
was built from a real outdoor zone** — an indoor husk like the Altar Room (`d_oz`, no top-level
`0x36` ZoneInteractions) is treated as indoor and the sky is skipped, even though the env bytes
are identical to a working zone. Splicing a sky into an indoor-sourced template (`--sky`) makes
it show in the *editor* (which draws sky by mesh name) but **not in-game**. So outdoor zones
must start from an actual outdoor zone — `make-template` warns when the source has no `weat`
sky / no `0x36`. See [format.md](format.md) and the memory note `sky-not-visible-ingame`.

## Building a flat biome base — `research/build_biome_templates.py`

The original flat-floor biome bases (desert/snow/jungle/fields/city) were built by this
research script: a real zone blanked to a flat floor while keeping its sky + outdoor
structure. The recipe (proven; do NOT reorder steps 4/5):

1. **copy** the source zone DAT
2. **strip sound** (`0x3D`) only — KEEP `0x05` effect generators (they **draw the sky meshes**;
   stripping them leaves the env gradient but no moon/clouds/sun)
3. **inject** the flat floor mesh (`new_floor.glb`)
4. **clear** the source's hilly walkable collision (`clear_zone_collision`) then **add** the
   flat floor collision. `clear_collision` now **PRESERVES the per-object cull transforms** —
   wiping them (the old behaviour) leaves the surviving placements pointing at a 0-length
   transform array → the engine culler reads a garbage cull volume → **instant crash** on large
   outdoor (v21+) zones. ([xi_collision.py](../../src/xi/zone/xi_collision.py))
5. **blank** every original placement (objects invisible) + register the floor — done AFTER the
   collision rewrite, because `add_placements` appends the floor's quadtree leaf-index list at
   the section payload end and the collision rewrite (`sec[:coll_rel]+region`) would TRUNCATE
   anything after it → a dangling leaf `idx_ref` → instant crash in `InitializeQuadTreeNode`.
6. **purge** the terrain meshes/textures OUTSIDE `weat` — the base ground is drawn DIRECTLY from
   `0x2E` sections (not via placements), so blanking placements does NOT hide it; only removing
   the sections does. KEEP the `weat` sky + the `0x36` outdoor marker + the floor.

Every build is **offline-validated**: quadtree leaves in-bounds, collision
`idxCount == node_count`, cull transforms preserved (≈ node_count), and root/`weat`/sky/floor
present. A build that fails any check is reported FAIL and not used.

### Add a new biome base

Add a row to `BIOMES` in `research/build_biome_templates.py` (`biome: (ROM-path, donor_zoneid)`)
and re-run it. The source must be a real outdoor/town zone with a `weat/` subtree (verify:
`f_`/`t_` root, sky meshes under weat). Install the result as a custom zone, then package it
with `xi zone make-template` to get a bundle id.

## Server side — the DB migration

`xi zone new` writes `zone-migration.sql` to the workspace and (by default) **auto-applies it**
to the dev DB. The SQL comes from the bundle's `data.sql` snapshot (the source zone's
`zone_settings` + `zone_weather` rows, with id/name/ip/port overridden); when the bundle has no
`data.sql`, it falls back to cloning those rows live from the template's source zone. Either
way the custom zone inherits a known-good outdoor config + weather rotation — which the server
must send for the dynamic sky/weather to run. See [import-json.md](import-json.md) and the DB
creds in [`xi_config.py`](../../src/xi/xi_config.py) (`XI_DB_*`, `XI_DB_AUTOAPPLY`).
**The map server must be restarted** to load a newly-added zone's weather config.

## Known limitations

- **Collision** is whatever the source zone had. Templates made from the flat biome bases have
  only a flat floor at origin — the source terrain is invisible *and* removed, so walking off
  the floor edge has no ground. Add your own collision via the editor.
- **Water/sea** from a biome base's source zone is intentionally dropped (it references
  resources tied to that zone's layout) — add water as its own placeable feature, not baked
  into the base.
- **Spawn point** is server-side (not in the DAT); set it via the server (GM `!pos`, or a zone
  script's onZoneIn). `zone_settings` has no x/y/z column.

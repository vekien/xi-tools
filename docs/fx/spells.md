# Spell VFX — resolution, playback & the editor spawn feature

How a spell becomes moving particles, and how xi loads + plays spell effects in the web
level editor. This is the **implementation reference** for the `xi.spell` module, the editor's
Spells asset-browser feature, and the JS `SpellRoutinePlayer`.

It complements — does **not** duplicate — the two existing references:

- **[effect_system.md](effect_system.md)** — the system-level chain (spell→effect→geometry) and
  the `0x05`/`0x07` byte formats, written from the xim Kotlin client.
- **[effects.md](effects.md)** — the `0x05` ParticleGenerator byte format deep-dive.
Everything below is **cross-verified against a UE5 C++ client reimplementation**
(modules `spell/` and `particle/`) and validated end-to-end against real game DATs.
Where the UE5 source confirms or corrects the xim-based notes in `effect_system.md`,
that is called out.

---

## 1. The resolution chain (verified)

```
spellIndex                         (the spell's row index = LSB spell_list.spellid)
   │  ← server tells the client which animation to play; this link is SERVER data,
   │    not present in any client DAT (see the gotcha below)
   ▼
animationIndex = SpellAnimationTable[spellIndex]
   ▼
fileIndex = 0xAF0 + animationIndex            (= 2800 + animationIndex)
   ▼
ROM/x/y.DAT  via the ftable                   (scan_file_ids; fallback ROM/{fi//100}/{fi%100}.DAT)
   ▼
0x07 EffectRoutine "main"  →  timed schedule of 0x05 generator spawns
```

Source of truth: `SpellTables.cpp:191-209` (`animationFileIndex`, `resolveEffectDatPath`),
`SpellTables.h:77` (`kFileTableOffset = 0xAF0`).

### The three spell tables

| Table | Where | Maps |
|-------|-------|------|
| **SpellAnimationTable** | **server** data — LSB `sql/spell_list.sql` `animation` column. Carried in xi as `src/xi/spell/spell_anim_table.json` (928 entries, ported from the UE5 builtin fallback). | spellIndex → animationIndex |
| **SpellNameTable** | client `ROM/181/73.DAT` (a `d_msg` string table) | spellIndex → display name |
| **SpellInfoTable** | client `ROM/118/114.DAT` (`0x49` SpellList, 0x64-byte blocks) | spellIndex → element/type/MP/AoE/cast-time |

> ### ⚠ Gotcha — the spell-name table has **no XOR mask**
> `ROM/181/73.DAT` is `d_msg`, but unlike the **zone**-name table (`ROM/165/84.DAT`, XOR 0xFF) it
> is **bitmask 0 — no XOR**. The UE5 client constructs it with the default `std::byte{0}` mask
> (`SpellTables.cpp:128`). In xi: `parse_dmsg(data, bitmask=0)`. Using the default 0xFF garbles
> the block table and throws a buffer-overrun in `parse_dmsg`.

> ### ⚠ Gotcha — `spellIndex → animationIndex` is **server data, not in the client**
> The retail client receives the animation id in the action packet; there is no pure-client spell→anim
> table. Both LSB and the UE5 reimpl carry it as server-sourced data. xi ships the ported table so
> the editor is **self-contained** (no running server required). To give a *new* spell a visual you
> must also point the server's `spell_list.animation` at the new animation index.

### The animation table

`spell_anim_table.json` = 928 entries, spellIndex `1..1019`, animationIndex up to `1011`. The first
78 entries are identity (`{n: n}`); 674 of 928 diverge after that. Regenerate from the UE5 source if
spells change:

```bash
python -c "import re,json; src=open('SpellAnimationTableData.cpp').read(); \
  body=src.split('table = {',1)[1]; \
  m={int(a):int(b) for a,b in re.findall(r'\{\s*(\d+)\s*,\s*(\d+)\s*\}', body)}; \
  json.dump(m, open('src/xi/spell/spell_anim_table.json','w'), separators=(',',':'))"
```

### Sample resolutions (verified on disk)

| Spell | index | anim | fileIndex | DAT | 0x05 gens | 0x07 routines |
|-------|------:|-----:|----------:|-----|----------:|---------------|
| Cure  | 1   | 1   | 2801 | ROM/10/9.DAT  | 8  | main, mai0, tgt0 |
| Dia   | 23  | 23  | 2823 | ROM/10/31.DAT | 17 | main, mai2 |
| Protect | 43 | 43 | 2843 | ROM/10/51.DAT | 12 | s000, tgt0, main |
| Fire  | 144 | 144 | 2944 | ROM/11/17.DAT | 8  | main |
| Blizzard | 149 | 149 | 2949 | ROM/11/22.DAT | 14 | main |
| Stone | 159 | 159 | 2959 | ROM/11/32.DAT | 11 | main |
| Thunder | 164 | 164 | 2964 | ROM/172/75.DAT | 19 | main |

---

## 2. EffectRoutine → timed schedule

A spell DAT is **not one particle puff** — it is a *sequencer*. The `main` `0x07` EffectRoutine fires
several `0x05` generators on a frame clock (cast → travel → hit), each for a finite emit window. This
is the single biggest reason the retail/UE5 client "renders spells correctly" and a naive "spawn every
autoRun generator" approach does not.

### Routine command framing

Each `0x07` routine body: a 16-byte zero header, then `sec1/sec2/sec3` u32 offsets (section-relative)
+ `totalDelay` u32 at `+0x10`. The `sec2` command stream (decoder: `_routine_sec2_commands` in
`src/xi/event/xi_event.py`):

```
op       u8   @+0
combo    u16  @+1     entry length = (combo & 0x1F) dwords; *4 = bytes
delay    u16  @+4     frames to wait before this command fires
dur      u16  @+6     emit window (for 0x02) in frames
ref      4B   @+8     a 4-char DatId (generator / sub-routine), or none
```

### Commands xi acts on

| op | Name | Effect |
|----|------|--------|
| `0x02` | ParticleGeneratorRoutine | spawn generator `ref`, emit for `dur` frames |
| `0x03 0x09 0x3B 0x3C 0x57` | LinkedEffectRoutine | call a child `0x07` routine `ref` (recurse) |
| `0x1E 0x2D` | ParticleDampen / StopGenerator | stop generator `ref` |
| `0x00` | end | terminate the routine |

Other ops (sound `0x0A/0x0B`, skeleton anim `0x05`, locks `0x07/0x52/0x2E/0x2F`, …) are caster-side
and not part of a standalone VFX preview, but their **`delay` still advances the clock** so generator
timing stays correct. Full routine-op semantics: `EffectRoutineInstance.cpp:138-245` (UE5).

### The clock is cumulative

Each command's `delay` advances a running frame clock **before** the command fires — matching
`EffectRoutineInstance::runReadyEffects` (`storedFrames -= entry.delay; runEffect(entry)`). So a
command's absolute start frame = the sum of all `delay`s up to and including it. xi flattens this to
absolute `start` frames in `parse_spell_schedule`.

### Linked sub-routines must be followed

Cure and Protect fire **almost no generators directly in `main`** — they call a `tgt0` child routine
via `0x03`, and the child holds the `0x02` generator spawns. A `main`-only walk shows nothing useful.
`parse_spell_schedule` recurses into linked routines (cycle-guarded, depth-limited), inheriting the
parent clock. Sub-routines whose `ref` is **not a local `0x07` section** (the shared caster cast/shadow
routines `shbk`/`shwh`) are skipped — they live in another DAT and are caster-side.

### Worked example — Fire (`ROM/11/17.DAT`, 8 generators, 200 frames)

```
start  dur   gen   (what)
   1     0   g006   cast flash       (dur 0 = single burst)
   1     0   gs00   cast flash
  10    10   g000   build
  10    60   g005   build / glow
  10    20   g001   build
  50   100   g004   travel
  70    80   g002   fireball
 120    80   g003   impact
```

That `start`/`dur` spread **is** the cast→travel→hit timeline. Linked-routine spells expand similarly:
Cure → `gr00, kr10, kr11, kr12, kr30, prn0, prn1` (all via `tgt0`); Protect → 12 generators.

---

## 3. The `xi.spell` module

`src/xi/spell/xi_spell.py` (+ data file `spell_anim_table.json`). All public functions are pure /
cached and self-contained (client DATs only).

| Function | Returns |
|----------|---------|
| `load_anim_table()` | `{spellIndex: animationIndex}` (cached) |
| `load_spell_names()` | `[name]` by spell index (ROM/181/73, no-XOR d_msg) |
| `file_index_for(index)` | `0xAF0 + animationIndex`, or `None` |
| `resolve_spell_dat_rel(index)` | `ROM/x/y.DAT` (relative), or `None` |
| `spell_catalog()` | `[{index, name, animIndex, fileIndex, dat}]` — named + animation-mapped spells, sorted by index (928) |
| `parse_spell_schedule(data, root='main')` | `{root, total, generators:[id], schedule:[{kind, ref, start, dur, op}]}` |
| `spell_list_payload()` | catalog filtered to dat-resolvable spells (bridge shape) |
| `spell_vfx_payload(index)` | effect DAT bytes (b64) + schedule (bridge shape) |

`parse_spell_schedule` reuses `_scene_sections` and `_routine_sec2_commands` from `xi_event.py` — the
same routine/section decoders the cutscene playback uses.

---

## 4. Bridge RPCs

Both read-only, no lock, lazy-imported (no editor-startup cost). Wired in
`src/xi/zone/xi_bridge.py` (`handle_command`).

### `zone.spellList`
`{}` → `{ok, count, spells: [{index, name, animIndex, fileIndex, dat}]}`. Only dat-resolvable spells.

### `zone.spellVfx`
`{index: int}` →
```jsonc
{
  "ok": true,
  "index": 144, "name": "Fire", "dat": "ROM/11/17.DAT",
  "bytesBase64": "...",          // the whole effect DAT — frontend parses with parseAllEffects
  "root": "main", "total": 200,
  "generators": ["g006", "gs00", ...],
  "schedule": [ { "op": 2, "kind": "gen", "ref": "g006", "start": 1, "dur": 0 }, ... ]
}
```
Errors return `{ok: false, error}` (never raise) — invalid index, no DAT, file missing.

---

## 5. The editor feature (asset browser → viewport)

Drag/click a spell from the **Spells** asset-browser category → its VFX plays looping at the drop
point, as a live test bed for the particle engine.

### Pieces

| File | Role |
|------|------|
| `index.html` | `<button data-cat="spells">` in the asset-browser sidebar |
| `web/leveleditor/main.js` | `loadSpellCatalog` / `spellcRender` / `spellcWireRows` (list, mirrors the SFX list); `dropSpellVfxOnViewport` / `spawnSpellVfx` (spawn); `spellPlayers[]` ticked in `animate()`; `clearSpellVfx()`; the HUD pill |
| `web/leveleditor/ffxi/particle_routine.js` | `SpellRoutinePlayer` — the JS EffectRoutine player |
| `web/leveleditor/ffxi/particle_runtime.js` | `ParticleEmitter` (one generator) — gained `emitting` / `stopEmitting()` / `aliveCount()` |
| `web/leveleditor/ffxi/particle_effects.js` | `parseAllEffects(buffer)` — parses the spell DAT's generators/meshes/textures/keyframes |

### `SpellRoutinePlayer`

A thin scheduler over the backend schedule. Per frame (`update()`): advance a frame clock; for each
schedule entry whose `start` has passed, spawn its generator as a `ParticleEmitter` (pinned to a
`zoneRoot`-local anchor = FFXI world space, identical to the cutscene VFX path); call `stopEmitting()`
at `start + dur`; dispose the emitter once its particles drain (`aliveCount() === 0`). Loops after
`total + tailFrames` (`reset()` force-disposes any still-live emitters, so an infinite-life singleton
particle can't wedge the loop). `dur <= 0` = a single instantaneous burst.

### Interaction & lifecycle

- **Drag** a spell row onto the map → spawn at the drop point. **Click** a row → spawn at screen centre.
- Drag MIME `application/x-xi-spell` (gated in `_isXiDrag` + the canvas `drop` dispatch).
- Spawns are **transient previews** — never registered as placements, never published, not in the
  change-set. A bottom-left HUD pill shows the live count + a **Clear** button. All cleared on zone
  change (`clearSpellVfx()` is called from `clearZoneVfxSystem()`).

### Requires a restart

Backend (new module + RPCs) and frontend JS both change → restart `xi gui zone` (kill orphaned
procs on port 8777 first — see [the install gotcha](../../README.md)) and hard-refresh.

---

## 6. Particle-engine gap analysis (editor JS vs UE5 C++)

The reason spawned spells don't yet look perfect: the editor's JS particle runtime implements only a
subset of the opcodes the UE5 engine handles, and spells lean on the gaps. The authoritative opcode
dispatch is `ParticleGeneratorParser.cpp` (sec1 `:227-248`, sec2 `:250-338`, sec3 `:342-486`, sec4
`:488-494`). The editor's implemented set is in `particle_runtime.js`
(`buildSec2Initializers` / `buildSec3Updaters`); the opcode *names* are in `particle_effects.js:9-93`.

### Coverage by section

| Section | Role | Editor implements | Notable |
|---------|------|-------------------|---------|
| sec1 | generator-level updaters | **0 / 17** | emission-rate ramp, generator pos/rot animation, actor-attach — all stubbed |
| sec2 | per-particle initializers | ~23 / 73 | position/velocity/rotation/scale/color/spherical/keyframe/child done |
| sec3 | per-particle updaters | ~16 / 43 | position/velocity/rotation/scale/color/keyframe/texscroll/child done |
| sec4 | expiration handlers | **0 / 2** | `0x01` EmitChild (burst-on-death), `0x05` Repeat — stubbed |

### What spells need that's missing (Phase 3 priority order)

| Opcode(s) | Feature | Visual cost when stubbed |
|-----------|---------|--------------------------|
| `0x0D` SpriteSheetFrame (sec3) | flipbook sprite animation | sprite frozen on frame 0 — **the most visible wrongness** |
| sec1 `0x04` / `0x0B–0x10` / `0x11` | emission ramp + generator pos/rot + actor-attach | bursts don't ramp/move/follow the caster |
| sec4 `0x01` EmitChild | burst a child generator on particle death | impacts don't "pop" |
| sec3 `0x29–0x2B` + sec2 `0x3D` `0x3E–0x40` | oscillation | wavering/orbiting motes sit still |
| linked types RingMesh (`0x3A`/`0x24`), PointLight (`0x47`/`0x58`) | shockwave rings, magic glow | not rendered at all |
| WeightedMesh, Distortion | morph geometry, heat-shimmer | not rendered |

Linked-data types currently rendered: `Actor`, `StaticMesh`, `SpriteSheet` (static frame), `Distortion`
(collected, not post-processed). Disabled: `PointLight`, `Audio`, `LensFlare`, `Null`, `RingMesh`,
`WeightedMesh`. (`particle_runtime.js` `SKIP_TYPES` + `_resolveMeshAndTexture`.)

Other gaps not specific to spells: time-of-day / moon-phase clock updaters (`0x3C–0x42`, `0x45`,
`0x4F`), draw-distance culling (`0x2E`, `0x48`), camera shake (`0x82`), point-list path following
(`0x34`, `0x54`).

---

## 7. Known limitations / TODO

- **Particle-opcode gaps** above (Phase 3 — fill in priority order, re-testing each vs a known spell).
- **No caster/target split.** All generators spawn at one anchor; the real client splits cast (on the
  caster) from hit (on the target), and projectiles travel between them via a `0x06` Route. For a
  particle test bed, one anchor is fine.
- **No sound / caster animation.** The routine's `0x0A/0x0B` sound and `0x05` skeleton-anim commands
  are not played (clock-advance only).
- **Spell metadata is names-only.** Element/MP/type (from `ROM/118/114.DAT`, a block-ciphered DAT —
  `BlockDecoder` rotate cipher keyed on popcount of bytes 2/0xb/0xc, `Codecs.cpp:103-128`) is **not**
  ported. Add it if the browser wants school/element chips.

---

## 8. Source map

**UE5 C++** (private reimplementation):
- `Private/spell/SpellTables.cpp`, `Public/spell/SpellTables.h` — resolution + `kFileTableOffset`.
- `Private/spell/SpellAnimationTableData.cpp` — the 928-entry animation table (ported to JSON).
- `Private/particle/EffectRoutineInstance.cpp`, `Private/data/EffectRoutineParser.cpp` — `0x07` sequencer.
- `Private/particle/ParticleGeneratorParser.cpp` — the full `0x05` opcode dispatch (sec1–4).
- `Private/data/Codecs.cpp` — `d_msg` StringTable + `BlockDecoder` spell-list cipher.
- `Private/FFXIEffectFacade.cpp` — `SpawnSpell` / `SpawnSpellAttached` entry points.

**xi**:
- `src/xi/spell/xi_spell.py`, `src/xi/spell/spell_anim_table.json` — module + data.
- `src/xi/zone/xi_bridge.py` — `zone.spellList`, `zone.spellVfx`.
- `src/xi/event/xi_event.py` — `_scene_sections`, `_routine_sec2_commands` (reused decoders).
- `web/leveleditor/ffxi/particle_routine.js`, `particle_runtime.js`, `particle_effects.js`, `main.js`.

**See also**: [effect_system.md](effect_system.md) (xim-based system view), [effects.md](effects.md)
(`0x05` byte format), [../ue5/spells-abilities.md](../ue5/spells-abilities.md).

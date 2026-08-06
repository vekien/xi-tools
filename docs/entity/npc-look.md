# NPC "look" — the appearance blob

How an NPC's (and player's) 3D **appearance** is defined: the 20-byte **`look`** structure.
This is what an event/cutscene needs to render the right model for each actor — the
[event bytecode](../events/cutscenes.md) references NPCs by **server id**, the zone Entity
DAT maps that id → **name**, and this `look` (from the server's `npc_list`) maps it → **model**.

> Source: CatsEyeXI `look_t` (`src/common/mmo.h`) ↔ DB column `npc_list.look BINARY(20)`.
> Decoder: `parse_look()` in `src/xi/gear/xi_core.py`. Layout + slot encoding verified
> against the live `npc_list` data (33.9k rows).

## Layout (20 bytes, little-endian)

```
0x00  u16  size          model type (the discriminator) — see table below
0x02  u16  modelid        (when size = 0/standard)  a single fixed model id
0x02  u8   face           (when size = 1/equipped)   character face variant
0x03  u8   race           (when size = 1/equipped)   1-8, see races below
0x04  u16  head           equipped slot model (see slot encoding)
0x06  u16  body
0x08  u16  hands
0x0A  u16  legs
0x0C  u16  feet
0x0E  u16  main           main-hand weapon
0x10  u16  sub            sub-hand weapon / shield
0x12  u16  ranged         ranged weapon
```

Bytes `0x02-0x03` are a **union**: a `u16 modelid` for a fixed-model NPC, or `face`+`race`
for an equipped character. The 8 slot words only matter when `size = 1`.

### `size` — model type

| size | type | meaning |
|---|---|---|
| 0 | **standard** | bytes 2-3 are a fixed **`modelid`** (monsters, objects, simple NPCs) |
| 1 | **equipped** | race + face + the 8 equipment slots (a character, like a player) |
| 2 | door · 3 elevator · 4 ship · 6 automaton · 7 chocobo | special objects/mounts |

### Equipped-slot encoding

Each slot word packs the **slot index in the top nibble** and the **gear model id in the
low 12 bits**:

```
slot_word = (slotIndex << 12) | (model_id & 0x0FFF)
model_id  = slot_word & 0x0FFF          # what resolve_gear_dat() consumes
```

e.g. body `0x20AC` → body slot, model `0xAC` = 172. A slot of `0` means "nothing worn".

### Races

`1` Hume M · `2` Hume F · `3` Elvaan M · `4` Elvaan F · `5` Tarutaru M · `6` Tarutaru F ·
`7` Mithra · `8` Galka. (xi names: `HumeMale`, `HumeFemale`, … — see `LOOK_RACE_NAMES`.)

## Resolving a look → renderable model(s)

- **standard** → `modelid` → model-DAT file_id via the 4-tier formula in
  [../model/json.md](../model/json.md#model-id--file-id) → `xi mesh export` → rigged GLB.
- **equipped** → for each worn slot, `resolve_gear_dat(raceName, slot, model_id)`
  ([../gear/](../gear/export.md)) gives the gear mesh DAT; all slots rig against the one
  race **skeleton** (`race_skeleton_dat(raceName)`).

**`build_character_glb(look, out_dir)`** (`src/xi/gear/xi_character.py`) does the whole
thing: it parses the look, and for an equipped character merges **every worn slot mesh onto
the one race skeleton into a single rigged `.glb`** (posed to idle); for a standard look it
falls back to the entity model DAT. (`build_gltf` already rigs a *list* of meshes to one
skeleton, so multi-slot assembly is just loading each slot + the shared skeleton.) The level
editor calls this per cutscene NPC (`zone.cutsceneActorGlb`) to drop the cast into the 3D view.

From the CLI:

```
xi entity look  <look-hex | npcid> [--resolve] [--json]   # decode → race + slots (+ DATs)
xi gear character <look-hex | npcid> [-o DIR] [--anim ''] # assemble → one rigged .glb
```

An npc id (e.g. `17531170`) is looked up in the server `npc_list`; a 40-char look hex needs
no DB.

Worked (verified, all DATs present on disk):

```
Altair  → equipped HumeMale face=12
  skeleton ROM/27/82.DAT
  head 0 → ROM/27/103.DAT   body 7 → ROM/28/14.DAT   hands 2 → ROM/28/54.DAT
  legs 5 → ROM/28/89.DAT    feet 2 → ROM/28/118.DAT  …
```

## Baking a costume into a standalone NPC DAT

The `look` above is what the **client** assembles at runtime (a **Type-1 PC**: shared race
skeleton + per-slot gear DATs + weapon-typed battle-anim DATs). Many retail NPCs are that
same appearance **frozen into one self-contained Type-0 entity DAT** — verified examples
`ROM/261/56` (Hume-F, unarmed) and `ROM/8/75` (Taru, armed). `xi entity.xi_bake_npc`
reproduces that flattening natively (no GLB round-trip), and the **`xi dats new` → NPC**
wizard drives it end-to-end.

`bake_costume_npc(race, face_id, slot_dats, main, sub, dual_wield)` concatenates **verbatim
DAT sections** inside a synthesized directory (`0x01` push / `0x00`-End pop) frame — valid
because section internals are section-relative and directories are a sequential stack (xim
`DatParser.parse`). Layout mirrors `ROM/261/56`:

```
<root>/ base/  @tr0 @tl0 (turn routines)  mou4 eye3 (face anims)
        mot_/  wlk0/1/2 idl0/1/2 run0/1/2 (locomotion, 3 parts)
        mode/  <textures 0x20>  <skeleton 0x29>  <face+gear+weapon meshes 0x2A>
        info (0x45)
```

Sources, all from the game's own data:

- **skeleton + part-0 locomotion + `mou4`/`eye3` + `@tr0`/`@tl0`** → the race-config DAT
  (`race_skeleton_dat(race)`);
- **part-1/2 locomotion** → the race's other movement DATs, located via the `FFXiMain.dll`
  motion tables (`entity.anim.xi_motion_tables`);
- **face + each worn slot mesh + textures** → `resolve_gear_dat(race, slot, id)` (model id `0`
  = the naked base part, so an empty slot still shows skin); the user may pass a DAT path
  instead of an id per slot. The wizard's **face picker** lists faces by code (`F8A` — "F" =
  *Face*) or NPC-face name (`Maximilian`, `Fomor`) rather than a raw byte, from the static
  table in `entity/xi_pc_faces.py` (transcribed once from AltanaViewer's lists — reference
  only, not read at runtime). Tarutaru is a single model whose "gender" is only the face, so
  it has no gender prompt and its picker combines the male- and female-looking faces
  (`Male 1A` / `Female 1A`, computed from the gear tables);
- **weapon mesh** → the user's weapon DAT (its `0x2A` rigs to the shared weapon joint).

Every child id is re-stamped unique (directories key children by id); textures dedupe by their
16-char name (meshes bind textures by that name, not by section id, so both survive verbatim).

Scope today is **appearance + locomotion + face anims + the weapon mesh**. Weapon-typed
**battle / weapon-skill / dual-wield** motion blocks (selected by `weaponAnimationType`,
Info `0x45` byte 3 — read and recorded at bake time) are a planned follow-up.

```
xi dats new         # → NPC (costume: race + gear + weapons) → bakes projects/custom/<name>.dat,
                      #   then places it at a custom entity model id like any entity action
```

The wizard's first question is **"Do you have a Look String?"** — paste a 20-byte look
(40 hex chars) and it `parse_look`s it and auto-fills race, face and every worn slot (resolving
each slot's model id → DAT via the gear tables, using the look's own race so a Tarutaru female
look picks the female face), skipping straight to the bake. Fixed-model looks (a monster/object)
are rejected — those aren't costumes. Answer **No** to fill everything in by hand.

## Related

- [../events/cutscenes.md](../events/cutscenes.md) — how a cutscene references its NPCs.
- [../gear/export.md](../gear/export.md) — `(race, slot, model_id)` → rigged GLB.
- [../model/json.md](../model/json.md) — model id → file_id formula + the entity table.
- [../dats/README.md](../dats/README.md) — the `xi dats new` wizard (NPC content type).

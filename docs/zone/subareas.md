# Zone sub-areas (shop / building interiors)

In FFXI, walking into most town shops and buildings does **not** change your zone —
the client swaps in a building **interior** in place of the closed-up exterior, then
swaps it back when you leave. These interiors are called **sub-areas**.

> **Client-side only.** Sub-area switching is pure client rendering: you stay in the
> same `zoneId`, and the server is never told. (Contrast a real *zone line*, which
> changes your `zoneId` and is server-coordinated — see the `'z'` rows below.)
> Everything needed to know which interiors belong to a zone lives in the zone's own
> DAT.

Parsing reference: xim `ZoneInteractionSection.kt`, `Scene.kt` (`SubAreaManager`),
`ZoneTables.kt` (`getSubAreaResourcePath`). Verified by ripping Lower Jeuno (`ROM/1/41`,
zone 245).

## The three pieces

A sub-area link is spread across the **main** zone DAT and one **interior** DAT:

1. **Trigger volume** — the main DAT's `0x36` *ZoneInteraction* section lists an OBB
   for each interior; entering it activates that sub-area. This is where the list of
   a zone's sub-areas comes from.
2. **Placeholder link** — the closed-building exterior is an ordinary `0x1C`
   placement whose **file-id link** (record offset `0x50`) points at the sub-area it
   stands in for. The client hides this placeholder while the interior is shown.
3. **Interior geometry** — a *separate* DAT, resolved from the sub-area id by a fixed
   file-table formula. It is a self-contained mini-zone (its own `0x2E` meshes +
   `0x1C` placements, already in this zone's world space).

## 1. Discovery — the `0x36` ZoneInteraction section

Plaintext (unlike `0x2E`/`0x1C` — **not** decrypted). Magic `"RID"`. Treat as hostile
input: a bad/short section should degrade to "no sub-areas", never crash the parse.

Section header (`ds = section.start + 0x10`):

| Off | Field |
|-----|-------|
| 0x00 | magic `"RID…"` (prefix check) |
| 0x04 | unk u32 |
| 0x08 | (skip 8) |
| 0x10 | `dataOffset` u32 → entries begin at `ds + dataOffset` |

At `ds + dataOffset`: `numEntries` u32, then three zero u32 (pad), then
`numEntries` × **0x40-byte** entries:

| Off | Field |
|-----|-------|
| 0x00 | position (3×f32) — OBB centre, raw FFXI world coords |
| 0x0C | orientation (3×f32) |
| 0x18 | size (3×f32) — half-extent after each axis is coerced to ≥ 0.01 |
| 0x24 | `sourceId` (4-byte DatId) |
| 0x28 | `destId` (4-byte DatId; 0 = none) |
| 0x2C | `param` u32 |
| 0x30 | terrain flags u16 |
| 0x32 | mapId u16 |
| 0x34 | elevator bottom i16, 0x36 elevator top i16 (`pos.y + i16/256`) |
| 0x38 | (8 bytes) |

> **Corrections from the PS2 decompile (`KO_RectData`, DWARF field names — see
> [reference/ps2_decomp_crosscheck.md](../reference/ps2_decomp_crosscheck.md) §8):** `0x0C` is `tex_map_no` (u32), the only rotation is `ry`
> at `0x10` (Y axis; `0x14` is padding); `0x2C` is `zone_no`, `0x30` is `arrow_flag` (u32),
> `0x34` is `lift_height` (s16), `0x38` is the runtime `lift_current_height`, `0x3C` is `flag`.
> The hit test scales into a unit cube (`|c| < 0.5`), so `0x18` holds **full extents**, not
> half-extents. The header is `file_id, version, dummy[2], offset_tbl[8]` — `dataOffset` is
> the first of up to eight sub-tables. Kind letters seen in the client: `'z'` zone line,
> `'m'` map/sub-area, `'@'` lift, `'s'` sound region.

The **first character of `sourceId`** classifies the volume:

| `sourceId[0]` | Kind | Meaning |
|---|---|---|
| `'m'` | **sub-area** | building interior; `param` = the **sub-area id** |
| `'z'` | zone line / entrance | `destId` set ⇒ transition (server-coordinated); `destId` 0 ⇒ entrance marker |
| `'_'` | door | animated door |
| `'f'` | fishing area | |

Collect the distinct non-zero `param`s of the `'m'` entries → the zone's sub-area ids.

## 2. The placeholder link (`0x1C` object `0x50`)

Each interior replaces a "closed building" exterior that ships in the main zone as a
normal `0x1C` placement. That placement's **file-id link** (`0x64` record offset
`0x50`, see [format.md](format.md#zonedef-placement-0x1c)) holds the **sub-area id**
it is a placeholder for (`0` = not a placeholder). xi decodes it as
`file_id_link` in [`xi_objects.py`](../../src/xi/zone/xi_objects.py) (`OFF_FILE`).

Runtime visibility rule (the single authority for both rendering and collision):

```
interior section (subAreaId != 0)   → draw only while subAreaId == activeSubArea
placeholder (file_id_link == activeSubArea) → hidden
everything else                      → draw
```

**Key by the link, not the mesh name** — a placeholder mesh name is reused across
several different buildings. In Lower Jeuno `r_shop` is the placeholder for *three*
shops and `r_min2` for *two*; only the per-placement `file_id_link` disambiguates
which exterior a given interior replaces.

## 3. Resolving the interior DAT

The interior lives in a separate DAT whose **global file-table id** is a fixed
function of the sub-area id (mirrors xim `ZoneIdToResourceId.getSubAreaResourcePath`):

```
fileId = subAreaId + 0x64                      (subAreaId  < 0x271)   — the common case
fileId = subAreaId + (0x14768 - 0x271)         (subAreaId >= 0x271)   — [Escha - Ru'Aun] only
```

That `fileId` resolves through the **same FTABLE/VTABLE** as any other file
(xi `scan_file_ids`, [`ftable/xi_core.py`](../../src/xi/ftable/xi_core.py)).
The resolved DAT is parsed exactly like a zone — its `0x1C` placements position its
`0x2E` meshes in this zone's world space, so it can be loaded standalone with no
offset.

## Worked example — Lower Jeuno (`ROM/1/41`, zone 245)

13 sub-areas, declared by `0x36` entries `m6t1`…`m6td`:

| sub-area id | `0x36` src | placeholder (`file_id_link`) | interior fileId | interior DAT |
|---|---|---|---|---|
| 0x1C6 | `m6t1` | `r_choko`  | 0x22A | `ROM/2/86.DAT` |
| 0x1C7 | `m6t2` | `r_yado`   | 0x22B | `ROM/2/87.DAT` |
| 0x1C8 | `m6t3` | `r_honbu`  | 0x22C | `ROM/2/88.DAT` |
| 0x1C9 | `m6t4` | `r_syuryo` | 0x22D | `ROM/2/89.DAT` |
| 0x1CA | `m6t5` | `r_shop`   | 0x22E | `ROM/2/90.DAT` |
| 0x1CB | `m6t6` | `r_shop`   | 0x22F | `ROM/2/91.DAT` |
| 0x1CC | `m6t7` | `r_g_zaka` | 0x230 | `ROM/2/92.DAT` |
| 0x1CD | `m6t8` | `r_shop`   | 0x231 | `ROM/2/93.DAT` |
| 0x1CE | `m6t9` | `r_syoku`  | 0x232 | `ROM/2/94.DAT` |
| 0x1CF | `m6ta` | `r_g_sake` | 0x233 | `ROM/2/95.DAT` |
| 0x1D0 | `m6tb` | `r_syouko` | 0x234 | `ROM/2/96.DAT` |
| 0x1D1 | `m6tc` | `r_min2`   | 0x235 | `ROM/2/97.DAT` |
| 0x1D2 | `m6td` | `r_min2`   | 0x236 | `ROM/2/98.DAT` |

e.g. `ROM/2/94.DAT` (the `r_syoku` food shop interior) is a 45-mesh mini-zone. Showing
sub-area `0x1CE` draws it and hides the `r_syoku` placeholder. Other towns follow the
same pattern (Bastok Markets `ROM/1/35` → 14 interiors `ROM/1/61…74`; Northern
San d'Oria `ROM/1/32` → 13 in `ROM/1/121…127` + `ROM/2/0…5`).

## How the web editor implements it

The [web level editor](../../web/leveleditor/README.md) lists and spawns every
sub-area of the open zone (Zone panel → **Sub-areas**):

| Step | Where |
|---|---|
| Parse `0x36` → `parsed.subAreas` (distinct `'m'` ids + OBBs) | `web/leveleditor/ffxi/zone.js` (`parseZoneInteractions`) |
| Read each placement's `file_id_link` | `zone.js` (`parseZoneDef`, off `0x50`) |
| Resolve sub-area ids → interior DAT paths via FTABLE | backend `zone.subareas` (`src/xi/zone/xi_bridge.py` `_subareas`) |
| Fetch + `parseZone` each interior, spawn as a non-pickable group | `main.js` (`loadSubAreas` / `buildSubAreaGroup`) |
| Per-interior show/hide + frame; show ⇔ hide its placeholder shell | `main.js` (`setSubAreaVisible`, `subAreaPlaceholders`) |
| Click an interior row → open that DAT as its own zone | `main.js` (`goToZone`) |
| **Reverse**: open an interior DAT alone → show a backlink to the owning zone | backend `zone.subareaParent`; `main.js` (`renderSubAreaParent`) |

Interiors spawn visible by default, so each placeholder exterior is hidden on load.
The list degrades gracefully: with the bridge offline the ids still show (parsed
client-side) but can't be resolved/spawned; an id with no FTABLE entry shows as
`unregistered`.

### Reverse lookup — "which zone owns this interior?"

The parent link only exists inside each main zone's `0x36`, so going *interior → parent*
means finding the zone whose `0x36` references this interior. The backend builds a
**reverse index** once — scan every zone's `0x36`, map each `'m'` param's resolved interior
DAT back to its owning zone — and caches it to `workspaces/subarea_index.json` (the full
scan of ~294 zones is ~1–4 s; base-game DATs are read-only, so the cache is rebuilt only if
the FFXI install path changes). `zone.subareaParent {zone}` is then an instant path lookup
returning `{zoneId, zoneName, dat, subAreaId}`. So opening `ROM/2/89.DAT` on its own shows
"↰ Interior of **Lower Jeuno**" with an Open button. (`ROM/2/89` is sub-area `0x1C9`, the
`r_syuryo` interior of Lower Jeuno.)

## Notes & limits

- The `0x36` positions share the placement coordinate frame, so the OBBs line up with
  the rest of the zone under the editor's root correction.
- Sub-area geometry is a **viewer overlay** in the editor — not pickable, not part of
  the editable placement set or the change-set. Editing interiors means opening the
  interior DAT itself as a zone.
- An interior DAT is just a zone DAT, so all the usual tooling (`zone export`,
  `zone object …`) works on it directly once you know its path from the table above.

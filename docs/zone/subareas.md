# Zone sub-areas (shop interiors, Ru'Aun's islands)

In FFXI, walking into most town shops and buildings does **not** change your zone —
the client swaps in a building **interior** in place of the closed-up exterior, then
swaps it back when you leave. These interiors are called **sub-areas**.

The same mechanism carries *detail*, not just interiors: Ru'Aun Gardens keeps every
island platform in a sub-area DAT and ships only low-detail stand-ins in the base
zone, so in game you only ever see the island you are on at full detail (see the
[Ru'Aun worked example](#worked-example--ruaun-gardens-rom212107-zone-130)). A
viewer or exporter that reads only the zone DAT shows those stand-ins everywhere.

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
2. **Placeholder link** — the closed-building exterior (a **stand-in**; in Ru'Aun the
   low-poly island platform) is an ordinary `0x1C` placement whose **file-id link**
   (record offset `0x50`) points at the sub-area it stands in for. The client hides
   this placeholder while the interior is shown (xim `ZoneDrawer.kt`: "Defer to the
   'real' object in the sub-area"). It is *not* part of the sub-area: the base zone
   draws it whenever that sub-area is inactive.
3. **Interior geometry** — a *separate* DAT, resolved from the sub-area id by a fixed
   file-table formula. It is a self-contained mini-zone (its own `0x2E` meshes +
   `0x1C` placements, already in this zone's world space). Its placements resolve
   against **its own** meshes only. It ships only some of its textures and borrows
   the rest from the parent zone — resolve its own first, then the parent's, the way
   xim resolves each area in its own directory. Names can clash with different
   pixels: every Ru'Aun sub-area ships its own `model   tu_w04c` (two versions
   between them), and Lower Jeuno's interiors ship `model   r_2ju02k` in three.
   Most town interiors also carry their own `0x05` effects (lamp glows, hearths) and
   `0x2F` environment; Ru'Aun's carry neither.

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
| `'m'` | **sub-area** | building interior (in Ru'Aun an island platform); `param` = the **sub-area id** |
| `'z'` | zone line / entrance | `destId` set ⇒ transition (server-coordinated); `destId` 0 ⇒ entrance marker |
| `'_'` | door | animated door — `wt_b/door/<id>/open|clos` `0x07` routines; see [doors.md](doors.md) |
| `'@'` | **lift / elevator** | auto-running platform; the tail s16s are its two floor heights — see [elevators.md](elevators.md) |
| `'s'` | sound region | |
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
function of the sub-area id (FFXiMain.dll `0x10177850`):

```
fileId = subAreaId + 0x64                      (subAreaId  < 0x258)   — the common case
fileId = subAreaId + 0x144F7                   (subAreaId >= 0x258)   — [Escha - Ru'Aun] only
```

xim's `ZoneIdToResourceId.getSubAreaResourcePath` switches at `0x271` instead
(`+ (0x14768 - 0x271)`, the same `+0x144F7`); no zone has an id in between —
Escha - Ru'Aun's start at `0x271`.

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

## Worked example — Ru'Aun Gardens (`ROM2/12/107`, zone 130)

16 sub-areas, and none of them is an interior: each is an island platform. The base
zone carries 592 stand-ins for them — low-poly copies on the `tu_l01`/`tu_l02`
low-res atlases — and the sub-area DATs hold the real platforms (Escha - Ru'Aun,
`ROM/337/59`, is the same layout with ids 625–640 → `ROM/337/43…58`).

| sub-area ids | DATs | what | stand-ins in the base zone |
|---|---|---|---|
| 524 | `ROM2/21/117` | the entrance dome and its warp pads | `lnd_sta_doom_*`, `lnd_sta_fld_m`, `lnd_sta_gate`, `com_wrp_e1*_n` |
| 525, 526 | `ROM2/21/118`, `119` | the two platforms between the entrance and the centre | `_tu_ws*` foliage, `com_pol_*_n`, `pip_pol_n`, `lb_ent_*_n`, `com_wrp_e*_n` |
| 527–530 | `ROM2/21/120…123` | the four god islands | `m_osid_*`, `_tu_ws*` |
| 531–535 | `ROM2/21/124…127`, `ROM2/22/0` | the five towers (body and entrance; the shafts below stay in the base zone) | `lnd_m_twr_*`, `com_gate_a_m` |
| 536–539 | `ROM2/22/1…4` | the standing-stone isles | `lnd_rid_fl_*`, `lnd_rid_pol`, `_tu_ws04` |

What stays in the base zone draws everywhere: the centre, the bridges (`bri_*`, with
far copies `m_bri_*` that only the culling tables tell apart — see
[format.md](format.md#visibility--space-tree-culling-tables-collision-transforms)),
the background islands `lnd_a`/`lnd_b`, the tower shafts `lnd_m_twr_b*`, and a mass of
collision-only `id_*` proxies. The god islands' `osid_*_h` pieces are in the base zone
too; their `m_osid_*` stand-ins overlap them and share no culling table with them.

## Zones with sub-areas

Every retail zone whose `0x36` has `'m'` volumes, with the DATs its ids resolve to
(282 sub-areas in 30 zones, all registered):

| zone | name | DAT | sub-areas | ids | sub-area DATs |
|---|---|---|---|---|---|
| 50 | Aht Urhgan Whitegate | `ROM4/0/3` | 11 | 560–570 | `ROM4/0/39` … `ROM4/0/49` |
| 33 | Al'Taieu | `ROM3/0/32` | 4 | 551–554 | `ROM3/7/49` … `ROM3/7/52` |
| 54 | Arrapago Reef | `ROM4/0/7` | 10 | 574–583 | `ROM4/4/123` … `ROM4/5/4` |
| 235 | Bastok Markets | `ROM/1/35` | 14 | 274–287 | `ROM/1/61` … `ROM/1/74` |
| 234 | Bastok Mines | `ROM/1/34` | 13 | 259–271 | `ROM/1/48` … `ROM/1/60` |
| 233 | Chateau d'Oraguille | `ROM/1/33` | 6 | 371–376 | `ROM/2/12` … `ROM/2/17` |
| 257 | Eastern Adoulin | `ROM9/0/4` | 2 | 589–590 | `ROM9/3/84`, `ROM9/3/85` |
| 289 | Escha - Ru'Aun | `ROM/337/59` | 16 | 625–640 | `ROM/337/43` … `ROM/337/58` |
| 242 | Heavens Tower | `ROM/1/38` | 4 | 425–428 | `ROM/2/61` … `ROM/2/64` |
| 250 | Kazham | `ROM2/0/25` | 8 | 505–512 | `ROM2/0/28` … `ROM2/0/35` |
| 245 | Lower Jeuno | `ROM/1/41` | 13 | 454–466 | `ROM/2/86` … `ROM/2/98` |
| 237 | Metalworks | `ROM/1/37` | 19 | 293–311 | `ROM/1/75` … `ROM/1/93` |
| 249 | Mhaura | `ROM/1/44` | 6 | 487–493 | `ROM/2/115` … `ROM/2/121` |
| 53 | Nashmau | `ROM4/0/6` | 1 | 571 | `ROM4/0/50` |
| 252 | Norg | `ROM2/0/27` | 2 | 514–515 | `ROM2/0/36`, `ROM2/0/37` |
| 231 | Northern San d'Oria | `ROM/1/32` | 13 | 349–361 | `ROM/1/121` … `ROM/2/5` |
| 236 | Port Bastok | `ROM/1/36` | 9 | 315–323 | `ROM/1/94` … `ROM/1/102` |
| 246 | Port Jeuno | `ROM/1/42` | 14 | 468–498 | `ROM/2/99` … `ROM/2/125` |
| 232 | Port San d'Oria | `ROM/0/113` | 6 | 364–369 | `ROM/2/6` … `ROM/2/11` |
| 240 | Port Windurst | `ROM/0/80` | 7 | 408–416 | `ROM/2/46` … `ROM/2/52` |
| 122 | Ro'Maeve | `ROM2/0/3` | 3 | 518–523 | `ROM2/15/124` … `ROM2/21/116` |
| 130 | Ru'Aun Gardens | `ROM2/12/107` | 16 | 524–539 | `ROM2/21/117` … `ROM2/22/4` |
| 243 | Ru'Lude Gardens | `ROM/1/39` | 10 | 431–441 | `ROM/2/65` … `ROM/2/75` |
| 248 | Selbina | `ROM/1/43` | 5 | 480–485 | `ROM/2/109` … `ROM/2/114` |
| 230 | Southern San d'Oria | `ROM/1/31` | 18 | 326–344 | `ROM/1/103` … `ROM/1/120` |
| 244 | Upper Jeuno | `ROM/1/40` | 10 | 443–452 | `ROM/2/76` … `ROM/2/85` |
| 256 | Western Adoulin | `ROM9/0/3` | 4 | 585–588 | `ROM9/5/49` … `ROM9/5/52` |
| 239 | Windurst Walls | `ROM/0/79` | 7 | 401–407 | `ROM/2/39` … `ROM/2/45` |
| 238 | Windurst Waters | `ROM/0/78` | 21 | 379–399 | `ROM/2/18` … `ROM/2/38` |
| 241 | Windurst Woods | `ROM/0/81` | 8 | 417–424 | `ROM/2/53` … `ROM/2/60` |

Id ranges are the lowest and highest; some zones skip ids inside them (Port Jeuno's 14
span 468–498).

## Export — `xi zone export --sub-areas`

`xi zone export` reads one DAT, so by default the stand-ins are what you get for every
sub-area. `--sub-areas` also writes each sub-area from its own DAT as `<stem>_<id>`
beside the zone (Lower Jeuno: `41` plus `41_454` … `41_466`) and leaves the stand-ins
it replaces out of the main file, so the files line up as the game shows each one from
inside. `--no-subareas` drops the stand-ins without writing the sub-areas. Details, the
texture naming for clashes, and the limits: [export.md](export.md#sub-areas-as-files---sub-areas).

## How the XI Model Viewer implements it

The model viewer (`D:\xi-model-viewer`) loads every sub-area of a zone with the zone:

| Step | Where (`ui/js/`) |
|---|---|
| `0x36` `'m'` ids → file ids (the client's formula, below) → DAT paths through the merged FTABLEs | `zone.js` `subAreaFileId`; `App.jsx` `loadZone` |
| Fold each sub-area DAT in: its placements against its own meshes, its textures own-first then the zone's, clashing names keyed `<name>@<id>` | `zoneModel.js` `mergeSubAreas` |
| Stand-ins of a loaded sub-area hidden, sub-area rows drawn (Objects › Sub areas) | `zoneModel.js` `zoneToModel` |
| **Graphics › Sub-areas** (on by default) flips between the two without a reload | `zoneModel.js` `setZoneSubAreas` |

Details lists the sub-area DATs and Data Struct opens each one. The viewer draws a
sub-area's static geometry only; its own `0x05` effects and `0x2F` lighting are not run.

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

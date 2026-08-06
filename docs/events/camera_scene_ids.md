# Camera scene ids (`p` → file id → DAT)

**Read this before changing camera-scene allocation, path, or look-at capture.**
Verified 2026-07-15 by A/B on Ru'Lude Gardens event 25000 (Maat).

A cutscene camera shot is **not** embedded in the event DAT. The event fires
`0x45 start_task` with a small ref **`p`**; the client maps that to a global
FTABLE file id and loads a **scene resource DAT** (routes + routines).

```
event 0x45
  → p  (stored in THIS actor's references[])
  → file_id = 30704 + datid_helper(p)     # global catalog
  → FTABLE/VTABLE → ROM/<subdir>/<slot>.DAT
  → section tag s000 / w005 / … (routine inside that DAT)
```

## What is `p` vs file id?

| Name | Where | Meaning |
|------|--------|---------|
| **`p`** | Actor `references[]`, via 0x45 work-selector | Small catalog index the event names |
| **`file_id`** | FTABLE / VTABLE index | Global id → which DAT on disk |
| **DAT path** | e.g. `ROM10/490/50.DAT` (user-chosen) | Actual bytes (evte + Route + EffectRoutine) |

- **`p` is global in effect** (same `p` → same `file_id` → same DAT for every zone/NPC).
- The **slot that holds `p`** is local to each actor's ref table.
- Each **custom** cutscene needs its **own free mid-band file id** so cameras don't clobber each other. Allocator: `_camera_scene_fileid` in [`xi_bridge.py`](../../src/xi/zone/xi_bridge.py).

## `datid_helper` tiers (HARD RULES for custom cameras)

```python
# xi.event.xi_event._datid_helper
if p >= 600:  return p + 39643   # file 70347+p  → 70947..
if p >= 300:  return p + 25937   # file 56641+p  → 56941..57240
return p                         # file 30704+p  → 30704..31003
```

| `p` | file-id band | Custom cameras |
|-----|----------------|----------------|
| 0..299 | 30704..31003 | **Retail-full — do not allocate** |
| **300..599** | **56941..57240** | **SAFE — only band we use** |
| ≥ 600 | 70947..~76k | **CRASHES the client** |

### A/B proof (2026-07-15)

| Setup | Result |
|--------|--------|
| Dialog-only (no camera `0x45`) | Works |
| Retail pack at **original** fid `30757` (`p=53`) | Works |
| **Same retail bytes** at fid `71366` (`p=1019`) | **Instant crash** |
| Same retail bytes at fid `57240` (`p=599`) | Works |
| Custom scene (mode 4, look ~2.5m) at `57240` | Works |

**Content was irrelevant for the high-id crash.** High-tier `p` / 71k file ids
are enough to kill the client. Do **not** “fix” high-tier again.

Code: `_camera_scene_id_safe` / `_camera_scene_fileid` only allow mid band.
Stale defs with `cameraSceneFileId` in the 71k range are rejected on publish.

## Where the DAT lives on disk

**User-controlled** (editor ▸ *Settings ▸ Camera DAT*): three fields **ROM** (volume,
default `10`), **Path** (subdir, e.g. `490`), **Dat Filename** (e.g. `50.dat`) join to
`ROM{rom}/{path}/{file}.DAT`. Required to publish a cutscene with a camera — Publish is
gated on it (frontend + backend both enforce).

The **placement** (disk path + FTABLE value) is independent of the scene's **file id**
(the mid-band `0x45` ref target, still auto-allocated). Registration mirrors
`xi dats build`: the same `(ftval, vt)` is written at that file id into **both** the root
`FTABLE/VTABLE` **and** the `ROM{vt}/FTABLE{vt}/VTABLE{vt}` — in the base-game output mirror
**and** the pivot overlay pack. The scene DAT is written to `FFXI_DIR/ROM{vt}/…` and
copied to `…/pivot/ROM{vt}/…`.

> **Why both tables, and why this is safe on ROM10.** The client consults the loaded
> `(F|V)TABLE` pairs with the VTABLE version byte gating each entry — an overlay entry
> wins (shadows the base) only where its byte names its own ROM. (xim models this as a
> single OR-merged table — `FileTableManager.combine`, `thirdparty/xim/.../table/FTable.kt`
> — but external byte evidence (2026-06-24) shows the real client is volume-direct +
> update-shadows-base; the practical rule is the same.) A **free** file id (the allocator
> guarantees the slot is 0 in every table) set to the same `(ftval, vt)` across root +
> ROM{vt} resolves identically under either model. The earlier "ROM10 crashes" belief conflated this with the high-`p`/71k-file-id
> crash (the only A/B-proven cause) **and** with a stale pivot `ROM10` entry *shadowing* the
> registration — which is exactly what patching (not zeroing) the pivot ROM10 table now
> prevents. See `_write_camera_scene` / `_publish_pivot_tables` in `xi_bridge.py`.

## Scene DAT content (must match retail)

Layout: `evte` → `cNNN` Route(s) → `sNNN` EffectRoutine(s) → `end`.

### Look-at distance

Retail routes keep **look-at ≈ 2 units from eye**. The editor used to record

```js
look = eye + forward * 100   // ~100m — CRASHES in custom scenes
```

Now: capture uses `* 2.5`; compiler `_sanitize_look` clamps far look-ats to ~2.5m
keeping direction. See `csCaptureCameraPose` in `viewport/cutscene.js` and
`_sanitize_look` in `xi_compile.py`.

### `interpMode` on multi-point routes

- Still (1 keyframe): mode `0` is fine.
- Move / curve (2+ keyframes): retail uses **mode 4**.
- Bug: still keyframe `smooth: 0` was stamped onto the whole curve → mode 0 on a
  3-point route → crash. Fixed: multi-point never keeps mode 0.

> ⚠️ **This field's semantics are contested — don't over-trust either camp.** Our own docs
> are split: this file (+`scene_dat_writer.md`, `cutscene-dev-guide.md`, `common_crashes.md`)
> says retail multi-point = **4**, while `cutscene_authoring.md` and the decoder comment in
> `xi_event.py` say **1** is the most common retail multi-point value. An external client-RE
> claim (2026-08 crosscheck, camera player at VA `0x1003D150`) says interpolation is chosen
> by **key count** (2 keys → LINEAR, >2 → CUBIC) and the `+0x14` u32 never feeds it — which
> would make the value cosmetic *except* that our mode-0-on-multi-point crash reproduces (the
> first behavioral effect anyone has pinned on this field). Until someone histograms retail
> routes and reconciles the crash with the key-count claim, treat "mode 4 on writes" as a
> safe convention, not established semantics.

### FOV

Route stores **focal length**, not degrees. Editor stores degrees; compile converts
`focal = 192 / tan(fov_deg/2)`. (Older docs saying “decidegrees” are wrong.)

## Event bytecode pattern (working)

```
0x20 01          lock player (CliEventUcFlag) — first opcode of cinematic prologue
0x42 cancel_set
0x46 01          camera on
0x45 fdo1        fade (p=200 → file 30904) on event entity
0x1C wait
0x38             event mode
0x4A             look_at (optional)
0xBA             place entity (NPC or player 0x7FFFFFF0)
                 — player uses 0xBA + 0x80 only (no NPC show / 0x4E prefix)
0x45 s000        camera shot (p=300..599 → custom DAT)
0x45 fdi1        fade in
… dialog …
0x45 fdo1 / 0x46 00 / 0x45 fdi1 / 0x21 end
```

Retail Maat 93 also uses `0x55 wait_sched` after fades/cameras; our Ambrotien-style
`0x1C` holds work for dialog+camera when the rules above are met.

## Checklist if a camera cutscene crashes

1. Dialog-only publish — if that works, camera path is the suspect.
2. Disasm: `0x45` camera tag → which **file id**? (`resolve_task_resource`)
3. Is `p` in **300..599**? If file id is 71xxx, **that is the bug**.
4. Does that file id resolve to the same DAT in the **base** and **pivot** tables (root
   *and* `ROM{vt}`)? A pivot ROM{vt} entry that differs (or a stale one) shadows the
   registration → wrong/empty DAT → crash. Both must carry the same `(ftval, vt)`.
5. Scene DAT: look dist ~2–3m; multi-point `interpMode == 4`.
6. Pivot: Game and pack root FTABLE/VTABLE agree; scene copied under pack `ROM{vt}/…`.
7. Restart **client** after table/scene path changes (not necessarily the map server).

## Code map

| Piece | Location |
|--------|----------|
| `p` ↔ file id | `xi_event._datid_helper`, `xi_bridge._scene_p_for` |
| Safe allocate | `xi_bridge._camera_scene_fileid` / `_camera_scene_id_safe` |
| Parse + gate placement | `xi_bridge._parse_camera_dat` (ROM/Path/Filename → `vt, subdir, slot`) |
| Write DAT + base tables (root + ROM{vt}) | `xi_bridge._write_camera_scene`, `_ensure_output_rom_table` |
| Pivot mirror (root + ROM{vt}) | `xi_bridge._publish_pivot_tables` |
| Editor UI | `panels/cutscene-author.js` `renderCameraDat` / `wireCameraDat` (Settings tab) |
| Build scene bytes | `xi_compile.build_scene_resource` |
| Look sanitize | `xi_compile._sanitize_look` |
| Capture look | `web/leveleditor/viewport/cutscene.js` `csCaptureCameraPose` |
| Curve smooth | `xi_compile._lower_camera_tracks` / `_lower_camera_track` |

## Related

- [common_crashes.md](../common_crashes.md) — field guide entries
- [scene_dat_writer.md](scene_dat_writer.md) — Route / EffectRoutine layout
- [cutscene-dev-guide.md](cutscene-dev-guide.md) — authoring overview
- [maat_93_study.md](maat_93_study.md) — retail reference cutscene

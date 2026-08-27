# Handover — FFXI login screen & UI texture work

Written 2026-08-25. Covers one long working session across two repos. Everything below is
either measured from the DATs, observed in the running client, or explicitly flagged as
inference. Where something is unproven it says so.

**Repos**

| Repo | Path | Role |
|---|---|---|
| `xi-tools` | `D:\xi-tools` | Python CLI (`uv run xi ...`) — DAT parsing/editing, WebSocket bridge |
| `xi-zone-editor` | `D:\xi-zone-editor` | Tauri 2 + three.js editor, talks to xi-tools over the bridge |

**Client under test:** CatsEyeXI, Ashita pivot/override tree at
`D:\cexi\catseyexi-client\Ashita\polplugins\DATs\catseyexi\`. Every `xi` command takes
`--ffxi DIR` to point at that tree instead of the retail root.

---

# Part 1 — Login screen (`ROM/0/23.DAT`)

## 1.1 What the file is

Magic `titl`. The FFXI login screen is not a still image — it flies **real in-game zones**
as a live 3D background. This file holds which zones appear, the camera routes through
each, and the weather/fog per segment. It carries **no geometry and no textures**.

Byte-level notes live in [docs/dats/ROM_0_23.md](dats/ROM_0_23.md); command reference in
[docs/title/README.md](title/README.md). Parser: [src/xi/title/xi_title.py](../src/xi/title/xi_title.py).

### Layout

32-byte header, then a flat list of 4-byte-tagged nodes:

```
+0x00  char[4]  tag              e.g. "cgu5", "ct20"
+0x04  u32      0x486 or 0x606   <- BOTH are camera nodes
+0x20  u32      keyframe count
+0x30  keyframes, 0x30 stride
```

Keyframe — identical shape to a cutscene camera route's `SplineControlPoint`:

```
+0x00  float3   eye XYZ
+0x0c  float    focal length (FovCalculationParameter, default 350)
+0x10  float3   look-at XYZ
+0x20  float    t   (0.0 .. 1.0 across the track)
```

`+0x0c` is a **focal length, not a view distance**. The client derives vertical FOV as
`2 * atan2(192, focal)`, so a *larger* value is zoomed *in*: 350 → 57.5°, 312 → 63°,
480 → 43°. It varies within a track — that is a zoom move.

Point count decides path shape, same rule as cutscene routes: **2 keyframes = straight
line, 3+ = spline**. That is why the screen appears to fly curves.

Zone sections follow, each introduced by the 8-byte marker `0x67b`:

```
+0x00  u32      zone id
...             stream of typed records: weather, transitions, ambient colour
```

Weather record (long form, `0x037d` — a 28-byte short form `0x0604` also exists):

```
+0x00  char[4]  tag   "suny" "fine" "mist" "clod" "rain" "snow" "thdr" "dryw"
+0x04  u16      0x037d
+0x08  u16      blend in / u16 blend out
+0x0c  u8[3]    fog colour RGB, +1 pad
+0x10  u32      fog flags (0x604)
+0x14  u16      fog near / u16 fog far
+0x18  char[8]  control track name
```

The file has **22 zone sections**, 251 camera nodes, 73,872 bytes.

## 1.2 Findings that cost time to establish

**Both `0x486` and `0x606` are camera nodes.** Matching only `0x486` silently drops 15 of
54 tracks and 45 of 123 keyframes — including `cgu1` and `cgu8`, two of the 3-keyframe
splines. This is why an early version of the tool showed "5 shots" for a segment the user
counted at 12+ in-game. The user was right; the parser was wrong.

**A zone owns a whole camera family, by name prefix, not by pointer.** Nothing in the file
points a section at its cameras. Weather records *name* a control track (`cgu1`), and the
tracks belonging to a section share a 3-char prefix: `cgu*` North Gustaberg, `cqf*` Qufim,
`cga*` Beaucedine, `ct1*`/`ct2*` the two Tahrongi sections. A section plays its **entire**
family end to end, not just the tracks its weather records name — North Gustaberg has 11
shots but only 4 named weather records.

**Weather changes mid-flight because it is keyed to a track, not a segment.** A weather
record naming `cgu8` turns over when shot 8 begins, partway through a continuous move.
This matches the user's in-game observation on Qufim.

**There is no time-of-day field.** The login screen has no clock. What reads as time of day
is authored atmosphere: fog colour tints the scene, near/far sets visibility. A `0x030f`
record carrying RGBA-looking values is *probably* the ambient entry — **inference from byte
patterns, never changed and observed.**

**Camera export → import on an untouched file is byte-identical.** Values are written
unrounded (float32 widened to double); rounding even to 6 dp drifts the small coordinates.

**A track's keyframe count cannot grow.** Nodes sit in a fixed layout with the next node
immediately after; extra frames would overwrite it. Import refuses rather than truncating.
To author a longer move, write across several consecutive nodes of the family — which is
how the vanilla screen produces its long flights.

## 1.3 Commands built

```bash
uv run xi title list                 # zone segments, cameras, weather counts
uv run xi title timeline             # each segment's shot list, in play order
uv run xi title weather              # fog colour and range per segment
uv run xi title export               # everything -> exports/title/data.json
uv run xi title camera export        # camera paths -> exports/title/camera.json
uv run xi title camera import        # write them back
uv run xi title set-zone 12 115      # point segment 12 at another zone
uv run xi title aim 12               # re-aim a section's cameras into its new zone
uv run xi title swap-sections 4 12   # exchange two whole segments  [BROKEN, see 1.6]
```

`aim` and `swap-sections` are **uncommitted working-tree changes** in
`src/xi/title/xi_title_cmds.py` and `src/xi/xi_cli.py`. Everything else is committed.

Import accepts `focal` **or** `fov_deg`, so a path authored in a tool that thinks in
degrees needs no conversion first.

## 1.4 The `aim` framing numbers (measured, not invented)

An early `aim` aimed the camera *down* at the ground and dollied 26 units. It looked
nothing like the real screen. Measuring all 440 vanilla keyframes gave:

| Property | Vanilla value |
|---|---|
| Height above local ground | ~17 units |
| Pitch | **+4.5° median — slightly UP, not down** |
| Distance to subject | 16.7 units |
| Travel across a shot | 11.9 units |

Ground is sampled per keyframe as the median Y of the 12 nearest placements — median so one
object on a ledge doesn't drag the shot with it.

## 1.5 Bridge + editor integration

`xi bridge` ([src/xi/zone/xi_bridge.py](../src/xi/zone/xi_bridge.py)) exposes:

| Method | Params | Returns |
|---|---|---|
| `title.timeline` | `{zoneId?}` | segments with weather, timing, camera routes |
| `title.cameraSet` | `{tracks:[{name, keyframes}]}` | writes keyframes back |
| `title.setZone` | `{section, zoneId}` | points a segment at another zone |

`title.timeline` with a `zoneId` answers "does this zone appear on the title screen" — an
empty `sections` list means no. The editor uses that to decide whether to show its Title
Screen entry at all.

Editor side: `ui/panels/title-panel.js` — a draggable modal with a timeline lane, shot
table, thick `Line2` camera paths in the viewport (plain `THREE.Line` ignores `linewidth`
on most drivers), look-at spurs, and playback that flies the shots in order.

**Known placeholder:** shot duration in the editor is a flat 5 s. The `0x0210` record is
believed to be a duration but the units are unestablished — real timing is not wired up.

## 1.6 THE UNRESOLVED PROBLEM — changing which segment opens

**Goal:** make the client open the login screen on a segment *other* than section 12,
keeping that segment's own zone **and** its own camera tracks.

### What is established

**Section 12 is the opening slot.** The client always plays it first on a fresh launch,
whatever zone id it holds. North Gustaberg was never special — the *slot* is.

Proven by experiment: `set-zone 12 <La Theine>` made La Theine the opening screen. The
camera stayed on the `cgu*` routes (because tracks belong to the section, not the zone), so
it flew Gustaberg's coordinates through La Theine terrain — clipping through the ground.
That is the correct, expected consequence, and it confirms the slot is positional.

**Everything after the first screen is picked at runtime.** The zone changes on each return
from character select. That order is **not in this file**: there is no permutation of the 22
sections anywhere in it as u8, u16 or u32, and every section header is zeros.

### Static analysis — all negative

Across **950,088 instructions** in `FFXiMain_unpacked.dll`, none of the scene's constants
appear as operands: not the `titl` magic, not the `0x67b` section marker, not the
`0x486`/`0x606` node types, not the file id of the title UI DAT. The scene parser reads
type codes **out of the data** and dispatches through jump tables rather than comparing
immediates, so there is nothing to anchor a search on. The unpacked image has no usable
string table. The DLL's only `rand` caller is packet-loss simulation.

**Conclusion: there is no static handle on the picker.** Do not repeat this search.

### The swap attempt, and the failure

`swap-sections` exchanges two segments' whole blocks — so the segment brings its zone id,
its weather, and its control-track names into the opening slot, and the right camera family
plays.

Rationale for believing it safe: **no u32 anywhere in the file equals any segment's
offset**, so nothing points into the section stream; camera nodes live earlier in the file
and are addressed by name, not position.

**Result of `swap-sections 4 12` (Arrapago Reef ↔ North Gustaberg): black screen.**

Post-hoc verification showed the file was structurally intact:

- same length, 73,872 bytes
- 22 sections still parse
- scene graph before the first section byte-identical
- 251 camera nodes unchanged
- end marker present

Which is why the black screen was surprising.

### The isolating test that was queued and never reported

The 4↔12 test changed **two things at once** — the swap mechanism *and* the zone. Zone 54
(Arrapago Reef) is Aht Urhgan content and may simply not be loadable at the login screen.

So a pristine `23.DAT` was restored and `swap-sections 11 12` run instead — Beaucedine
Glacier (zone 111), base-game, with its own `cga*` family:

```
now section 11: zone 106 (North Gustaberg), cameras cgu0 cgu1 ...
now section 12: zone 111 (Beaucedine Glacier), cameras cga0 ... cgad  <- opening screen
```

**This has not been run in the client. That result is the next thing anyone picking this up
needs.** Interpretation:

| Result | Meaning |
|---|---|
| Beaucedine loads with correct cameras | Swap mechanism works; zone 54 was the problem. Enumerate which zones are viable in the opening slot. |
| Black screen again | Sections are **not** freely reorderable despite no offset pointers existing. Abandon block-moving. |
| North Gustaberg still | Position is not what selects the opening segment; the whole model is wrong. |

### Recommended next steps

1. **Run the 11↔12 test.** Cheapest possible discriminator.
2. **If the mechanism is at fault, edit in place instead of moving blocks.** `set-zone`
   rewrites 4 bytes and works. So copy section 4's *contents* into section 12's slot — zone
   id plus its weather records' track names — rather than relocating the block. Nothing
   moves; only bytes change. Needs the two blocks to be size-compatible, or the record
   stream rewritten in place.
3. **If static analysis is needed after all, go runtime.** A Ghidra MCP debugger bridge is
   already wired up and working at `http://127.0.0.1:8089`
   (`debugger_set_breakpoint`, `debugger_trace_function`, `debugger_read_memory`).
   Breakpoint the zone load at the title screen and read the section index directly out of
   memory. This is the route most likely to actually answer the question, and it was never
   attempted.

**Do not** pursue synthesising cameras (`xi title aim`) as the answer to this. It was tried,
and the user explicitly rejected it: the goal is the *real* segment's *real* tracks, not a
generated approximation copied over the top of Gustaberg's.

---

# Part 2 — UI textures (`ROM/119/50.DAT` and friends)

This part is **complete and committed**. Included because it establishes format facts and
because several of the failure modes are traps anyone editing UI DATs will hit.

## 2.1 Container format

UI texture containers (`lobb` for the login screen, `menu` for in-game) hold two entry
kinds:

- **`0xA1`** — DXT entry: 57-byte header, then `xTXD` blob
- **`0xB1`** — palettized: 64-byte header, 256-entry ABGR palette, 8-bit indices,
  **rows stored bottom-up**

DXT1 = 4 bpp; DXT3/DXT5 = 8 bpp. DXT3 alpha is 4-bit (values are multiples of 17).

**FFXI stores alpha at half scale — `0x80` is fully opaque**, dithered between 119 and 136.
This is why raw exports look ~10% opacity. Exports now boost alpha to full range.

## 2.2 Sprite mapping

The `0x31` chunk in a `lobb` container maps sprite rects. Records are
`01 00 <type> <subtype>` + 8-byte parent + 8-byte name + payload. A 41-byte payload puts
the quad at +0, a 42-byte payload at +1:

```
[dest quad 4x(x,y) u16][src_w][src_h][src_x][src_y]
```

Corners are inclusive.

**Trap:** hardcoding the marker as `01 00 01 01` matches only 666 of 1230 records, merges
records into oversized blobs, and corrupts the expansion banner column. The type/subtype
bytes vary.

**Trap:** replacing a texture without updating the chunk's size field desyncs the client's
chunk walk and breaks everything downstream. `_resize_chunk()` in
[src/xi/ui/xi_core.py](../src/xi/ui/xi_core.py) handles this — the size field is 19 bits, in
**16-byte units**.

## 2.3 Dead ends — do not retry

**DXT5 is not supported by the client.** Everything imported as DXT5 renders flat grey,
confirmed in-game. The DLL *does* contain `1TXD…5TXD` and `DXT1…DXT5` name tables — I
initially read those as proof of support and was wrong. Those tables also list DXT2 and
DXT4, which FFXI never uses, so they are diagnostic tables, not a capability list.
**DXT3 is the ceiling.**

**Palettized (`0xB1`) is a dead end for UI art.** The decoder works and is kept as a reader
([src/xi/ui/xi_palette.py](../src/xi/ui/xi_palette.py)), but 256 colours is worse than DXT3
for these images. `--hd` writes DXT.

## 2.4 What the tooling does now

```bash
uv run xi ui tex si ROM/119/50.DAT --hd
```

- **Resizes by default** to the size the game expects, with `--no-resize` to skip. The
  export size is not authoritative — a canonical size sheet
  ([src/xi/ui/data/layout_reference.json](../src/xi/ui/data/layout_reference.json), generated
  by `xi ui gen-sheet`) decides.
- `--hd` / `--hd-only <name>` keeps a texture above vanilla resolution and rescales the
  sprite rects to match, so a 512×512 import into a 256×256 slot displays whole rather than
  cropped to a quarter.
- Alpha boost is undone on import **only when the PNG is an untouched export**, decided by a
  content digest in the sidecar. Un-boosting hand-authored art crushed alpha from 11 levels
  to 3 (7.2 dB PSNR) — that bug is fixed.
- Palettized rows are flipped on read/write (`FLIP_TOP_BOTTOM`); without it banners come out
  mirrored vertically.

---

# Part 3 — Things that will bite you

**FFXI's Y axis points DOWN.** A smaller Y is *higher*. Never treat FFXI coordinates as
Y-up, and never infer an axis mapping — measure it. Verified: vanilla title cameras sit a
median 17 units *below* the Y of nearby ground placements (i.e. 17 above the terrain);
`camera_y - ground_y` was negative for 9 of 11 North Gustaberg shots.

**Converting to a Y-up renderer** (three.js in xi-zone-editor): `zoneRoot` carries a 180°
rotation about X *and* a `(-1, 1, -1)` scale, composing to **`(x, y, z) → (-x, -y, z)`**.
Anything parented to `zoneRoot` gets this for free; world-space objects must go through
`zoneRoot.localToWorld()` rather than hand-written mirroring. I got this wrong twice by
writing `(-x, y, -z)`, which renders as a path mirrored east/west and flying backwards —
three different-looking bugs from one cause.

**`exports/` is the user's master art.** Do not overwrite it. Test on scratch copies.

**Do not restore or back up DATs.** The user resets the client tree at will and finds
restore chatter actively obstructive. Write directly; keep going forward when something
breaks rather than reverting.

---

# Part 4 — File inventory

## xi-tools (`D:\xi-tools`)

| File | State | What |
|---|---|---|
| `src/xi/title/xi_title.py` | committed | Title scene parser: nodes, tracks, zones, weather, shot list |
| `src/xi/title/xi_title_cmds.py` | **modified** | CLI commands; `aim` + `swap-sections` uncommitted |
| `src/xi/xi_cli.py` | **modified** | command registration for the two above |
| `src/xi/zone/xi_bridge.py` | committed | `title.timeline` / `title.cameraSet` / `title.setZone` |
| `src/xi/ui/xi_core.py` | committed | chunk resize, layout records, rect sync, canonical sizes |
| `src/xi/ui/xi_simple.py` | committed | `--hd`, `--no-resize`, `--repair-rects`, alpha handling |
| `src/xi/ui/xi_palette.py` | committed | palettized reader + RGBA median-cut quantizer |
| `src/xi/ui/xi_gen_sheet.py` | committed | `xi ui gen-sheet` |
| `src/xi/ui/data/layout_reference.json` | committed | canonical geometry for 5 DATs |
| `docs/title/README.md` | committed | command reference |
| `docs/dats/ROM_0_23.md` | committed | byte-level format |

## xi-zone-editor (`D:\xi-zone-editor`)

| File | What |
|---|---|
| `ui/panels/title-panel.js` | Title modal: timeline lane, shot table, Line2 paths, playback |
| `ui/panels/events-panel.js` | Title block prepended when the open zone has shots |
| `ui/index.html` | `#title-modal` |
| `ui/main.js` | `initTitlePanel`, `titleRenderTick`, draggable registration |
| `ui/css/events.css` | `.ttl-*` styles |

---

# Part 5 — Commits

## xi-tools (newest first)

```
d9ed5af  docs(title): section 12 is the opening screen, whatever zone it holds
d3439a0  docs(title): add a command reference for xi title
93e0918  feat(bridge): title.timeline / cameraSet / setZone
8cbf034  fix(title): the keyframe field is a focal length, not a view distance
6d31281  feat(title): xi title export - one file for the whole title screen
85ab9f0  fix(title): a zone owns a whole camera family, not just its named tracks
b46bc31  fix(title): drop non-ASCII control-track names
8b5bacb  feat(title): xi title timeline - shot list per segment
6d6d7fb  feat(title): camera export/import default to exports/title/camera.json
2e6b764  feat(title): xi title - edit the login screen's zones, cameras and weather
c93f79a  feat(ui): --hd keeps textures above vanilla resolution
e364d3f  fix(ui): only undo the alpha boost on an untouched export
0dec3d3  fix(ui): fit imported textures to the size the game expects
5ea5772  refactor(ui): scale sprite rects from the import's own size delta
b7b3fb2  feat(ui): resize by default, add --no-resize
1c59a57  feat(ui): resize UI textures and keep sprite mapping in sync
```

## xi-zone-editor

```
1665af52  docs: title screen shots in the Events panel
4addd0b0  fix(events): make the title modal draggable
7351e537  feat(events): open title screen shots in a modal
88961a01  fix(events): correct FFXI coordinate mapping, add a timeline lane, restyle
3f78afef  feat(events): thicker title paths, and play the shots
e6abf1b4  fix(events): say why the Title block is missing
1c45b68f  feat(events): Title Screen section for zones on the login screen
```

---

# Part 6 — Open questions

1. **Does `swap-sections 11 12` load?** Unrun. Everything about the opening-slot problem
   branches on this.
2. **What picks the zone after the first screen?** Not in `23.DAT`, not statically findable
   in the DLL. Runtime debugging is the untried route.
3. **`0x0210` record — is it a shot duration, and in what units?** The editor uses a flat
   5 s placeholder.
4. **`0x030f` — is it the ambient colour?** Byte-pattern inference only; never changed and
   observed.
5. **UI rects: is `240` a size or an inclusive extent?** Affects one edge case in rect
   scaling.

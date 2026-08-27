# `xi title` — the login screen

The FFXI login screen is not a still image. It flies real in-game zones as a live 3D
background, and everything about that lives in **`ROM/0/23.DAT`** (magic `titl`): which
zones appear, where the camera flies through each, and the weather and fog for every
segment. The file holds no geometry or textures of its own.

For the byte-level format see **[dats/ROM_0_23.md](../dats/ROM_0_23.md)**.

Title **UI chrome** (logos, wardrobe badges, race hit-boxes) is a different file:
`ROM/119/50.DAT` (`lobb`).

| Doc | Topic |
|-----|--------|
| **[ui_chrome.md](ui_chrome.md)** | `0x20` / `0x30` UiMenu / `0x31` UiElementGroup, `lobbywin`, ownership rule |
| **[main_menu.md](main_menu.md)** | Move `loby2win` rows (labels follow); text ids; font scope; hide Create |
| **[wardrobe_numbers.md](wardrobe_numbers.md)** | Hide wardrobe 3–8 icons/digits (layout dest quads, not DLL/font) |

---

## Commands

```bash
uv run xi title list                 # zone segments, cameras, weather counts
uv run xi title timeline             # each segment's shot list, in play order
uv run xi title weather              # fog colour and range per segment
uv run xi title export               # everything -> exports/title/data.json
uv run xi title import               # write cameras (and optional timing) back
uv run xi title camera export        # slim camera paths -> exports/title/camera.json
uv run xi title camera import        # write camera.json back
uv run xi title set-zone 12 115      # point segment 12 at another zone
uv run xi title aim 12               # auto re-place cameras above the section's zone
uv run xi title menu                 # title UiMenus (loby): move / size / nav
uv run xi title sprite               # 0x31 sprites (dest/src): logo, ex*, …
```

Every command reads `ROM/0/23.DAT` under `FFXI_DIR` unless given a path, and `--ffxi DIR`
overrides the root for one invocation (a pivot/override tree, for instance).

---

## What is in there

22 zone segments. `xi title list`:

```
  #    offset  zone  name                    weather  cameras
 10  0x00f7cc   126  Qufim Island                  4  cqf0 cqf1 cqf3 cqf4 …
 11  0x00fa90   111  Beaucedine Glacier            8  cga0 cga1 cga2 cga3 …
 12  0x00fdac   106  North Gustaberg               5  cgu0 cgu1 cgu2 cgu3 …
```

Each zone owns a **camera family** — `cgu*` is North Gustaberg, `cqf*` Qufim — played as
consecutive shots. Eleven shots for North Gustaberg, not the four its weather records
name.

---

## Changing a zone

```bash
uv run xi title set-zone 12 115
  section 12 @0x00fdac: 106 (North Gustaberg) -> 115 (West Sarutabaruta)
    cameras to re-aim: cgu0 cgu1 cgu2 …
```

**A zone swap alone is not enough.** Camera routes are absolute world positions authored
for the old zone, so the shot will be underground or outside the map until those are
re-aimed. Use `xi title aim 12` for a quick auto pass, or export/import to set positions
by hand.

---

## Camera round trip (set eye + look-at)

```bash
# Full dump (cameras + weather + timing + UI inventory)
uv run xi title export --section 12
# or slim cameras-only:
uv run xi title camera export --section 12

# edit eye / look on each keyframe in the JSON, then:
uv run xi title import exports/title/data.json
# or:
uv run xi title camera import exports/title/camera.json
```

Export → import on an untouched file is **byte-identical**. Each keyframe:

```json
{ "t": 0.0, "eye": [717.7, -6.6, 412.9], "look": [684.5, -6.7, 375.5],
  "focal": 350.0, "fov_deg": 57.496 }
```

| Field | What to set |
|---|---|
| `eye` | Camera world position `[x, y, z]`. **FFXI Y points DOWN** (smaller Y = higher). |
| `look` | Look-at point `[x, y, z]`. Orientation is `look − eye` (aliases: `look_at`). |
| `focal` **or** `fov_deg` | Zoom. Larger focal = tighter. FOV is vertical degrees. |
| `t` | `0.0` .. `1.0` along the shot. |

Import also accepts a flat file: `{ "tracks": [ { "name": "cgu0", "keyframes": [...] } ] }`.

**A track's keyframe count cannot grow** (`keyframe_slots` in the export is the cap).
Nodes sit end-to-end; import refuses rather than truncating. Fewer frames than a track
holds leaves the remainder untouched. For a longer flight, author across several
consecutive tracks of the family — which is how vanilla produces long moves.

---

## Duration / timing

There **is** a duration-like field: record type **`0x0210`** in each zone section's stream.
It is an 8-byte record whose editable payload is a **u16 at +6**. Exports list them as:

```json
{ "offset": 65004, "value": 20,
  "note": "0x0210 duration/hold; unit unproven (likely engine ticks, not seconds)" }
```

They show up on `xi title timeline` as `timing=[20]` next to weather shots. **The unit is
not proven** against wall-clock playback (the editor still uses a flat 5 s placeholder).
To write edited values back:

```bash
uv run xi title import exports/title/data.json --timing
# or
uv run xi title camera import --timing
```

Only entries that carry both `offset` and `value` are written; the writer checks that the
bytes at `offset` are still a `0x0210` record before patching.

---

## Weather and time of day

```bash
uv run xi title weather --section 12
   0x00fdc4  suny  fog rgb(  0,141, 39) near= 900 far= 900  track=cgu1
   0x00ff18  mist  fog rgb( 64,206, 75) near=1000 far=1000  track=cgu8
```

**There is no time-of-day field.** The screen has no clock. What reads as time of day is
the authored atmosphere per segment: the fog colour tints the scene, the near/far pair
sets visibility. A `0x030f` record carrying RGBA-looking values is the likely ambient
entry, but that is inference from byte patterns — it has not been changed and observed.

A weather record names the camera track it coincides with, which is why the weather
appears to change **mid-flight**: it turns over between shots of a continuous move, not
at segment boundaries.

---

## Play order

**Section 12 is the opening screen.** The client always plays it first on a fresh launch,
whatever zone id it holds — North Gustaberg was never special, the *slot* is.

Established by experiment: pointing section 12 at La Theine Plateau made La Theine the
opening screen. The camera stayed on the `cgu*` routes, because tracks belong to the
section rather than the zone, so it flew Gustaberg's coordinates through La Theine.

So the opening zone is editable without touching the client:

```bash
uv run xi title set-zone 12 <zone id>     # then re-aim the cameras
```

Everything after the first screen is picked at runtime — the zone changes on each return
from character select. That part is not in this file: there is no permutation of the 22
sections anywhere in it as u8, u16 or u32, and every section header is zeros.

Static analysis found no handle on the picker either. Across 950,088 instructions in
`FFXiMain_unpacked.dll`, none of the scene's own constants appear as operands — not the
`titl` magic, the `0x67b` section marker, the `0x486`/`0x606` node types, nor the file id
of the title UI DAT. The scene parser reads type codes out of the data instead of
comparing immediates, so there is nothing to anchor a search on, and the DLL's only
`rand` caller is packet-loss simulation.

---

## Editor integration

`xi bridge` exposes the scene so an editor can drive it:

| Method | Params | Returns |
|---|---|---|
| `title.timeline` | `{zoneId?}` | segments with weather, timing and camera routes |
| `title.cameraSet` | `{tracks:[{name, keyframes}]}` | writes keyframes back |
| `title.setZone` | `{section, zoneId}` | points a segment at another zone |

`title.timeline` with a `zoneId` answers "does this zone appear on the title screen":
an empty `sections` list means no. [xi-zone-editor](https://github.com/vekien/xi-zone-editor)
uses that to decide whether to offer its Title Screen entry for the open zone.

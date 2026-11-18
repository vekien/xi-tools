# `xi zone check` — does a zone model hold together?

Checks a zone model DAT the way the client walks it on zone-in, without bounds checks of
its own, and against the limits found by crashing the client with generated zones. It
writes nothing. Use it on a zone you built (or edited) before loading it in game.

```bash
uv run xi zone check ROM10/1/8.DAT                # a built zone
uv run xi zone check ROM10/1/8.DAT --base 154     # …and the zone it was built on
uv run xi zone check 107                          # a zone by id
uv run xi zone check --all --no-notes             # every zone of the install
uv run xi zone check 107 --json                   # schema/zone_check.json
```

| Option | Default | Meaning |
|---|---|---|
| `DAT` / `ZONE_ID` | — | Zone model DATs (ROM-relative or absolute) or zone ids; several allowed |
| `--all` | off | Every zone of the install (`xi zone json`'s list) |
| `--base DAT\|ZONE_ID` | — | The zone this one was built on: its block grid must not be bigger |
| `--json` | off | Print the report as JSON (`xi.zone-check.v1`) |
| `--notes/--no-notes` | on | Show the notes |

A **problem** is something the client fails on, or is expected to: the exit status is 1
when any zone has one. A **note** is something unusual that retail zones also do (they
load), shown so a built zone can be compared with them.

**Every one of the 294 zones of a legacy (pre-September-2026) client checks clean**,
including the ships (two ZoneDefs each) and zone 0 (`ROM/0/28`, a test / title-screen zone
with an older collision layout).

## What it checks

| Part | Problems | Notes |
|---|---|---|
| Sections | they don't cover the file exactly (a zero-size section, bytes after the last); no ZoneDef (0x1C) | directories (0x01 … 0x00) not balanced; more than one ZoneDef (retail ships carry two; each is checked) |
| Placements | the object records run past the ZoneDef | placed mesh names with no 0x2E mesh in the file (LOD suffixes `_h`/`_m`/`_l` resolved as the exporter does) |
| Culling tables | a table runs past the ZoneDef or lists an object past the count; an object links to no table | — |
| Space tree | no tree; a node or child pointer outside the ZoneDef; a node reached twice; a leaf lists an object past the count | a leaf list past the ZoneDef (the client skips it; the blank-zone template has one); objects in no leaf (never drawn); child boxes reaching outside their parent's; leaves at more than one depth; a leaf larger than retail's largest |
| Collision | header offsets outside the ZoneDef; its object count ≠ the ZoneDef's; transforms that aren't whole 0xC0 records; groups not ending where the grid map starts; a group pair pointing at no transform or no collision mesh; a triangle indexing past its mesh's vertices or normals; the grid map shorter than the block grid; a grid cell pointing inside the ZoneDef but at no group | a spare transform (Lower Jeuno has one); 0x70-byte transforms (zone 0); edge cells holding `0xFFFFFFFF`-like values (retail has 1–4 in many zones); more collision groups than retail's most |
| `--base` | the block grid covers less than the base zone's | — |

## Limits, measured

From retail (this command over a legacy client) and from Loxley's generated zones on a
September 2026 client:

| What | Retail's most | Generated zones | Status |
|---|---|---|---|
| ZoneDef section | 8,269,536 bytes (Ilrusi Atoll) | 8,372,848 loaded; every one over it crashed on zone-in | The section size field's own ceiling, 8,388,592 bytes; `zonedef_headroom` reports what is left |
| Block grid against the base zone's | — | a grid smaller than the base zone's crashed entering the zone (ffximain+0x16663B): the client drops every entity the base zone's event and NPC files place into the collision grid by position, unchecked | `--base` problem |
| Objects in one space-tree leaf | 328 (Ceizak Battlegrounds) | 1,330 in one leaf read past a buffer on zone-in | note above 328 |
| Space-tree leaf depths | one depth per zone (5 or 6) | an adaptive tree (85-yalm leaves) wrote past a buffer on zone-in | note when mixed |
| Collision groups (= grid cells in use) | 124,103 (Western Altepa Desert); ten zones over 60,000 | 79,373 crashed on zone-in, 62,909 loaded | **not a limit**: LPS stopped at 60,000 guessing a 16-bit count, but retail loads twice that, so that crash had another cause. A note above retail's most |

Generated-zone figures are Loxley's (LPS-modules, `zonekit/writer.py`); retail figures are
from `xi zone check --all --json`.

The checks are ported from Loxley's LPS-modules zone writer (`check_zone`), with his
permission, onto xi-tools' own readers: `xi.zone.xi_zonedef`, `xi.zone.xi_collision`,
`xi.zone.xi_decrypt` (the key tables come from `FFXiMain.dll`).

# FFXI Model ID & File ID Reference

How FFXI maps model IDs to DAT files, why monster and gear models don't
collide, and where custom content safely lives.

---

## How the FTABLE Works

Every DAT file in FFXI is indexed by a **file_id** — a flat integer that maps
into two tables:

| Table | Entry size | Contents |
|---|---|---|
| `FTABLE.DAT` | `uint16` at `file_id * 2` | `(subdir << 7) \| file_index` |
| `VTABLE.DAT` | `uint8`  at `file_id` | ROM index (`1` = base ROM, `10` = ROM10, etc.) |

Together they resolve to: `ROM<n>/<subdir>/<file_index>.DAT`

The game **never looks up a file_id directly**. It calls a lookup function with
a model ID, that function computes the file_id, then the FTABLE resolves the
path. Which lookup function is called depends entirely on what is being
rendered — a mob, a piece of gear, a map, etc.

---

## Monster Model Lookup

### Formula (confirmed by decompiling FFXiMain.dll at VA `0x100C513D`)

The monster lookup function takes a `modelid` and returns a `file_id` using a
4-range formula:

| modelid range | file_id formula | flat offset |
|---|---|---|
| 0 – 1499 | `modelid + 1300` | +1300 |
| 1500 – 2999 | `modelid + 50295` | +50295 |
| 3000 – 3499 | `modelid + 96907` | +96907 |
| **3500+** | `modelid + 98239` | **+98239** |

Custom monsters always land in the **3500+ range**. Retail monsters use all four ranges
(Behemoth is modelid 404, Cerberus 1793; 122 retail mobs sit in 3000–3193).

> **Registration extent (byte-checked 2026-08):** range 3 is only *registered* for modelids
> **3000–3193** — every fid for 3194–3499 is VTABLE=0 and no retail `mob_pools` row uses
> those ids; the tail of the nominal 3000–3499 range is unused. Range 4 is anchored by its
> first registered fid `101739 = 3500 + 98239` (and a dense run at `102239 = 4000 + 98239`),
> which also refutes an externally-proposed `+98546` base for this range.

### Why 4 ranges?

Historical growth. Each range represents a batch of monsters added to the
FTABLE at a different point across FFXI's 14-year development. Adding a new
range was cheaper than reshuffling thousands of existing entries. The 3500+
range is open-ended and covers all content from late retail onwards.

### Monster file_id regions

| Region | file_id range | modelid range |
|---|---|---|
| Range 1 (0–1499) | 1300 – 2799 | 0 – 1499 |
| Range 2 (1500–2999) | 51795 – 53294 | 1500 – 2999 |
| Range 3 (3000–3499) | 99907 – 100406 | 3000 – 3499 |
| Range 4 (3500+) | 101739 – 109480 | 3500 – 11241 |

### Retail cap

```
Last non-zero retail FTABLE entry : file_id 109480
Last retail modelid (3500+ range) : 109480 - 98239 = 11241
```

### Verification

```
Tiger skeleton : modelid 308  →  file_id = 308 + 1300 = 1608  →  ROM/5/3.DAT  ✓
```

The 2003 PS2 client (`XiSkeletonActor`, `ReferenceRead(modelid + 0x514)`) has only this
range; gear is `model + model_group_tab[race][slot]` with `equipNum = slot << 12 | model`,
i.e. the per-race/slot base-table mechanism — [ps2_decomp_crosscheck.md](ps2_decomp_crosscheck.md#model-ids--verified-for-the-2003-range).

---

## Gear Model Lookup

### How gear model IDs are structured

Gear does not use the monster formula. Instead, each equipment slot has a base
offset added to the item's `MId` (model index) to form a combined model ID:

| Slot | Offset | Example model_id |
|---|---|---|
| Head | `+ 0x1000` | `0x1000 + MId` |
| Body | `+ 0x2000` | `0x2000 + MId` |
| Hands | `+ 0x3000` | `0x3000 + MId` |
| Legs | `+ 0x4000` | `0x4000 + MId` |
| Feet | `+ 0x5000` | `0x5000 + MId` |
| Main hand | `+ 0x6000` | `0x6000 + MId` |
| Sub | `+ 0x7000` | `0x7000 + MId` |
| Range | `+ 0x8000` | `0x8000 + MId` |

This combined value then goes through a **separate gear lookup** driven by the
per-`(race, slot)` **group tables** in FFXiMain.dll (ported as `RACE_TABLES` in
`src/xi/gear/xi_core.py`). Each slot has up to 6 groups of
`(base_file_id, count)`; `model_id` is a **cumulative** index across those
groups. Resolution is `resolve_gear_dat(race, slot, model_id)` /
`slot_file_ids` — not a single flat offset for every mid.

The simple per-race offsets below apply only to **group 0** (typically
`model_id` 0–511 for a given slot). For `model_id ≥ 512`, later groups use
different base file_ids — always go through the tables / `resolve_gear_dat`.

| Race | file_id offset (main hand, **group 0 only**) |
|---|---|
| Hume Male | -16184 |
| Hume Female | -13008 |
| Elvaan Male | -9832 |
| Elvaan Female | -6656 |
| Galka | +3096 |
| Mithra | -80 |
| Taru Male/Female | -3480 |

### Gear file_id regions

Retail gear file_ids span roughly **8,456 – 81,998** across all races and slots.

Retail gear resolution uses the per-`(race, slot)` group tables embedded in
`FFXiMain.dll` (ported into `src/xi/gear/xi_core.py`). xi injects **custom**
gear by allocating a windowed custom file_id per `(race, slot)` *above* the
entity region — so custom gear lives at file_id `128,240+`, never in the retail
gear range. See [../ftable/expand.md](../ftable/expand.md).

---

## Why Monster and Gear Never Collide

Even if a monster and a gear piece share the same raw number (e.g. `12000`),
they are routed to completely different file_ids:

```
Monster modelid 12000  →  monster formula  →  file_id = 12000 + 98239 = 110239
Gear MId 12000 (body)  →  gear formula     →  file_id = (12000 - 0x2000) + race_offset
                                                       = somewhere in 8k–82k range
```

The game knows which function to call based on **context** — is it rendering a
mob or player equipment? The number alone means nothing.

---

## File ID Space Map

```
file_id 0                                                              109,701+
│                                                                           │
│  8,456 ──────── 81,998         101,739 ── 109,480   113,239+             │
│  [retail gear]                 [retail monsters]     [custom]             │
│                                                      (ROM10)              │
```

| Region | file_id range | Contents |
|---|---|---|
| Retail gear | ~8,456 – ~81,998 | All race/slot gear model DATs |
| Retail monsters (range 1) | 1,300 – 2,799 | Monster skeletons (modelid 0–1499) |
| Retail monsters (range 2) | 51,795 – 53,294 | Monster skeletons (modelid 1500–2999) |
| Retail monsters (range 3) | 99,907 – 100,406 | Monster skeletons (modelid 3000–3499) |
| Retail monsters (range 4) | 101,739 – 109,480 | Monster skeletons (modelid 3500–11241) |
| **Empty** | **109,481 – 113,238** | Gap — safe buffer above retail cap |
| **Custom entities** | **113,239 – 128,239** | ROM10 (modelid 15,000 – 30,000) |
| **Custom gear** | **128,240 – 423,151** | ROM10 (72 windows: 8 races × 9 slots) |

Everything above file_id **109,480** is confirmed empty in retail FTABLE.

---

## Custom Entity & Gear Ranges

xi reserves two custom regions above retail, set up in one pass by
`xi ftable expand` (see [../ftable/expand.md](../ftable/expand.md)). The
ceilings live in `src/xi/xi_config.py`, and the entity↔gear boundary is
**derived** so the two regions can never overlap.

| Boundary | Value | Derivation |
|---|---|---|
| Last retail file_id | 109,480 | Last non-zero VTABLE entry in retail FTABLE |
| Last retail entity modelid | **11,241** | `109,480 - 98,239` |
| First safe custom entity modelid | **15,000** | `MODEL_SAFE_START` — ~3,758-slot buffer above retail |
| Entity ceiling (default) | **30,000** | `MAX_ENTITY_MODELID` (xi_config) |
| Entity custom file_id range | **113,239 – 128,239** | `15,000 + 98,239` … `30,000 + 98,239` |
| Gear floor (derived) | file_id **128,240** | `98,239 + MAX_ENTITY_MODELID + 1` |
| Gear ceiling per (race, slot) | **4,095** | `MAX_GEAR_MODELID` (12-bit field) |
| Gear custom file_id range | **128,240 – 423,151** | 72 windows × 4,096 (8 races × 9 slots) |

**Recommended ranges to import into:** custom **entities** at modelid **15,000+**,
custom **gear** at modelid **3,000+** per `(race, slot)`. Both are deep buffers —
you only ever use a tiny slice.

The entity ceiling is just config, not a hard limit: raise `MAX_ENTITY_MODELID`
(env `XI_MAX_ENTITY_MODELID`) and the gear floor slides up with it
automatically. Builders refuse any modelid that would cross into the gear region.
Run `xi ftable info` for the live ranges.

### mob_pools modelid blob format

The `modelid` column in `mob_pools` is a 20-byte binary `look_t` struct:

```
bytes 0–1  : uint16 LE  size    = 0        (0 = creature model, not humanoid)
bytes 2–3  : uint16 LE  modelid = <modelid>
bytes 4–19 : zeros
```

Example for modelid 15000 (`0x3A98`):

```sql
-- mob_pools.modelid value:
0x0000983A00000000000000000000000000000000
```

---

## Tools

| Command | Purpose |
|---|---|
| `xi ftable expand` / `expand entity [N]` | Expand FTABLE/VTABLE for custom entity (+ gear) buffers |
| `xi ftable lookup` | Look up any file_id or modelid in FTABLE/VTABLE |
| `xi ftable info` | Live custom ranges and free capacity |
| `xi model json` / `xi model json --free` | Dump registered models; next free custom entity slot (JSON) |
| `xi entity inject` | Legacy: inject a custom entity DAT into ROM10 (prefer `xi dats build`) |
| `xi ftable range-scan` | Scan FTABLE for occupied blocks |
| `xi ftable delete` | Free a registered custom slot |

See [ffximain.md](./ffximain.md) for full documentation of the monster formula
derivation from FFXiMain.dll decompilation.

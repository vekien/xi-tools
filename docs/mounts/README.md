# MOUNTS

Everything about FFXI mounts in xi — how the client resolves a mount to a model,
the data that makes one appear and be ridable, and the `xi mount` commands for
listing, exporting, and authoring custom mounts.

- **[mechanism.md](mechanism.md)** — how mounts work: model resolution, the 64-menu /
  255-model limits, the display-vs-ride gates, the 64-slot table, and the >64 client patch.
- **[data.md](data.md)** — per-id reference table (0–255): name, is_mount, key_item, file-id, ROM DAT.

Related: mount models are entity-style DATs, so the model side reuses the
[`model`](../model/json.md) and [`mesh`](../mesh/import.md) tooling
([ftable/set](../ftable/set.md) registers the file-id). The string tables are the
same `d_msg` family as [events/dialogue](../events/dialogue.md).

---

## How a mount works (one paragraph)

The client renders a mount by flat arithmetic into the FTABLE file-id space —
`file_id = 0x019131 + mountId` — and a mount is **owned** (and therefore shown in the
menu) when its **key item `3072 + mountId`** is set, which the server streams to the
client as a 64-bit bitmask (packet `0x0AE`). Retail ships **39 mounts (ids 0–38)** inside
a deliberate **64-slot** table; the menu shows **owned mounts only**, so empty slots never
appear. Ridable ids go to **255** (`uint8`); the menu caps at **64**. See
[mechanism.md](mechanism.md) for the full story.

---

## `xi mount` commands

> Status: **shipped.** The model side uses the [`xi ftable set`/`delete`](../ftable/set.md)
> primitives; the string side uses a byte-exact `d_msg` codec (`xi/common/xi_dmsg.py`).
> Writes go to the game DATs in place and leave every other block in the table byte-identical.

A **mount record** is `{ id, model DAT, name(EN/JP), help(EN/JP), key_item: {id = 3072+id,
name(EN/JP), desc(EN/JP)}, server grant }`.

| Command | What it does |
|---|---|
| `xi mount json` | Enumerate ids 0–63: id, EN/JP name, key-item id, file-id, ROM DAT, occupied/free. `--all` for 0–255. |
| `xi mount export <id>` | Dump one mount's full record (EN/JP name + help, EN/JP key-item name + desc, file-id, DAT) → JSON. |
| `xi mount import <id> --dat <file>` | Override an **existing** mount's model (swap the DAT at `0x019131+id`); `--name`/`--name-jp` to also retitle. Guarded on retail ids 0–38 without `--force`. |
| `xi mount delete <id>` | Remove a custom mount: zero the FTABLE entry, clear the name/help/key-item strings, emit server cleanup. Refuses retail ids 0–38 without `--force`. |
| `xi dats prepare` + `build` | Create a **new** mount as a reproducible package action: register the model, write EN/JP names, place the key item inside Key Items > Mounts, and emit the server bundle. Preview with `build --dry-run`. |

**`--dat` resolution & placement:** `--dat` is tried as-given (absolute/CWD), then as a
ROM-relative path under `FFXI_DIR` (e.g. `ROM10/10/1.DAT`), then the output mirror. If the
DAT already lives in the ROM tree it's registered **in place** (no copy); an external file is
copied to `ROM10/100/<id>.DAT`. Injecting onto an occupied **custom** id (39+) overwrites it;
retail ids 0–38 need `--force`. `--dry-run` previews without writing.

Server-side changes are packaged through `xi dats prepare` + `xi dats build`,
which copies the client files and server resources into the release layout.

Current project custom mount, if it needs to be rebuilt from a manifest entry:

```powershell
xi dats build projects/update.json --only mount.cyakko
```

This keeps id `50` and key item `3122`; it does not move the mount to the next free slot. Pass
`--help-en/-jp` and `--ki-desc/-jp` too if those descriptions need to be preserved or changed,
because omitted key-item descriptions are written blank.

---

## DAT paths (general reference)

A complete mount touches the model table plus **six string DATs** — three resources, each
with an English and a Japanese twin. The language file-ids come from the client's
`DatIndices.cpp` (parallel EN/JP/FR/DE tables); resolve through FTABLE to the ROM paths below.

| Resource | EN file-id → DAT | JP file-id → DAT | format |
|---|---|---|---|
| **Model** | `0x019131 + id` → `ROM…` (scattered) | (same; language-independent) | entity model DAT |
| **Mount name** (menu label) | `0x0D981` → `ROM/351/84.DAT` | `0x0D909` → `ROM/351/82.DAT` | `d_msg`, sequential 80-byte stride, index = id |
| **Mount help text** | `0x0D982` → `ROM/351/85.DAT` | `0x0D90A` → `ROM/351/83.DAT` | `d_msg`, sequential, index = id |
| **Key-item name + desc** | `0x0D999` → `ROM/175/35.DAT` | `0x0D921` → `ROM/175/34.DAT` | `d_msg`, **XOR-`0xFF`**, **marker-keyed** (`sub[0].marker` = key-item id) |

Key-item text slots differ by language: EN uses **4 = singular name, 5 = plural, 6 =
description**; JP uses **1 = name, 2 = description**. The mount-name/help tables are
**sequential** (index = mount id) so adding id 50 means growing them to 51 entries with
39–49 as empty filler (harmless because the menu shows owned mounts only). The key-item
table is **marker-keyed** (sparse), but mount key items must
be physically inserted in the Mounts category before the `-Mounts` separator row; appending
them to the file puts them in the wrong Key Items category. FR/DE twins exist in
`DatIndices.cpp` too if ever needed.

Server-side, the key item lives at **`3072 + mountId`** (`CHOCOBO_COMPANION = 3072`), stored
in `keys.tables[6]` (`xi::bitset<512>`). Retail mount companions run **3072–3110** (= mount
ids 0–38). The native 64-bit mount mask covers key items **3072–3135** (= mount ids 0–63),
and **3136** is the next non-mount key item (`SHEET_OF_SHADOW_LORD_TUNES`) — a fourth
independent confirmation of the 64-mount menu ceiling.

---

## Key facts

- **39 mounts** ship (ids 0–38); the model table reserves **64 slots** (ids 0–63).
- **Menu cap = 64** (the `0x0AE` 8-byte bitmask); **model/ridable cap = 255** (`uint8`).
- The **menu shows owned mounts only** — empty/unnamed slots never appear.
- A mount is owned iff key item **`3072 + id`** is set; that single bit drives both the menu
  display and (via `hasKeyItem`) the ride permission.
- **Custom mounts should use stable ids 39–62** (≤63 = no client patch needed). Project custom:
  **id 50** ("Cyakko"), key item **3122**, file-id **`0x19163`**, model DAT **`ROM10/10/1.DAT`**.

---

## Key Items > Mounts category

No `FFXiMain.dll` patch is needed for a custom mount whose id is inside `0–63`, such as id
50. The Key Items menu does **not** file Mounts by a hardcoded key-item range. It walks the
key-item `d_msg` table in physical order and uses marker-0 separator rows as category
footers. Retail mount key items `3072–3110` are stored immediately before the `-Mounts`
separator row.

The original hidden `xi mount inject` wrote missing key-item blocks by appending them to the end
of the key-item table. That made key item `3122` valid and owned, but physically placed it
after the final category instead of before `-Mounts`, so it did not appear under Key Items >
Mounts. `set_key_item()` now inserts mount key-item blocks into the Mounts section and keeps
the numeric id unchanged.

Live validation for the project custom mount:

- Mount id remains **50**.
- Key item remains **3122** (`3072 + 50`).
- Model remains **`ROM10/10/1.DAT`** via file-id **`0x19163`**.
- EN and JP key-item blocks for `3122` are each present exactly once and sit immediately
  before the Mounts separator.

`FFXiMain.dll` work is still relevant only for ids **64+**, because the native mount menu
packet `0x0AE` carries an 8-byte, 64-bit mount mask. That is separate from key-item category
placement and separate from the 255 model/ridable-id limit.

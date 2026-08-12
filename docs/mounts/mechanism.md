# How FFXI mounts work

Model resolution, the limits, the data that makes a mount appear and be ridable, and
how to add a custom mount. For the per-id table see [data.md](data.md); for the
`xi mount` commands and the DAT-path quick reference see [README.md](README.md).

All numbers were verified against a live retail client install (`FFXI_DIR`).

---

## 1. Model resolution

When an entity is mounted, the client resolves the model by flat arithmetic into the
**FTABLE file-id space**:

```
mount model file_id = 0x019131 + mountId
```

`0x019131` (102705) is a hardcoded base in `FFXiMain.dll`. The result is a global FTABLE
file-id, which VTABLE/FTABLE redirect to an arbitrary `ROM{ver}/{folder}/{file}.DAT`:

```
VTABLE[file_id]  → version byte (which ROM; 0 = unused/free)
FTABLE[file_id]  → (folder << 7) | file
resolved path    → ROM{version}/{folder}/{file}.DAT
```

Reference (xim client reimpl): `ActorMountEvent.kt` → `ModelLook.fileTableIndex(0x019131 + index)`
→ `FileTableManager.getFilePath(fileId)` (`FTable.kt`).

This is **not** the monster/gear model-id space. Monster modelids go through the
`98239 + modelid` formula (see [../ffximain/ffximain.md](../ffximain/ffximain.md)); gear is a
12-bit field (≤4095). Mounts bypass both and index FTABLE directly — the base ≈ 102705 is far
above either range. The "4096" ceiling people cite is the gear/model-id space and is irrelevant
to mounts.

---

## 2. The two limits (do not conflate)

| Limit | Value | Set by |
|---|---|---|
| Distinct mount **models** / ridable id | **255** | `uint8` appearance fields (§4) |
| Mount **menu** (owned-list display) | **64** | `0x0AE` packet = 8 bytes = 64-bit mask |

The menu cap is a client constraint, proven by analogy to spells — same mechanism, different size:

| Subsystem | Packet | Size | Capacity |
|---|---|---|---|
| Spells | `0x0AA` `MagicDataTbl` | **128 bytes** | 1024 (`xi::bitset<1024> m_SpellList`) |
| Mounts | `0x0AE` `MountDataTbl` | **8 bytes** | 64 (8 bytes of `keys.tables[6]`) |

Both `memcpy` a slice of a bitset into a fixed packet; the client reads exactly that many bytes
and builds a UI for exactly that many entries. SE sized the two subsystems differently in the
DLL; you can't borrow the spell width for mounts without patching the client.

---

## 3. The 64-slot table layout

The mount model table is a deliberate **64-slot** region of FTABLE (file-ids `0x19131`–`0x19170`,
ids 0–63), which is exactly why the `0x0AE` ownership mask is 64 bits.

| ids | state |
|---|---|
| 0–38 | the 39 shipped mounts |
| 33 | **empty** (`VTABLE`=0) — Noble Chocobo, which reuses the chocobo model |
| 39–62 | empty / reserved (free to claim — **custom mounts go here**) |
| 63 | **chocobo placeholder** — `ROM/351/104.DAT` is byte-identical to the chocobo (mount 0) and the deprecated Quest Raptor (mount 1) |

id 63 has a model but **no name-table entry**, and it never appears in the menu. Combined with
the confirmed behavior that **the menu shows owned mounts only**, this means empty/unnamed slots
are simply never rendered — so a custom mount at id 50 with 39–49 left empty is harmless.

### Authoritative names

The real names live in `ROM/351/84.DAT` (a `d_msg` table, 39 entries). These are the source of
truth, **not** the server enum:

```
0 Chocobo   8 Crawler  16 Hippogryph    24 Adamantoise  32 Byakko
1 Raptor    9 Fenrir   17 Spectral Chair 25 Dhalmel      33 Noble Chocobo
2 Tiger    10 Beetle   18 Spheroid      26 Doll         34 Ixion
3 Crab     11 Moogle   19 Omega         27 Golden Bomb  35 Phuabo
4 Red Crab 12 Magic Pot 20 Coeurl       28 Buffalo      36 Craklaw
5 Bomb     13 Tulfaire 21 Goobbue       29 Wivre        37 Alicorn
6 Sheep    14 Warmachine 22 Raaz        30 Red Raptor   38 Bubble Crab
7 Morbol   15 Xzomit   23 Levitus       31 Iron Giant
```

> **Server enum drift:** `scripts/enum/mount.lua` (`xi.mount`, `MOUNT_MAX=38`) is stale (missing
> Alicorn=37, Bubble Crab=38) and off-by-one (a phantom `QUEST_RAPTOR=1` shifts ids 2+ by one).
> The `m_mountId = MountId + 1` hack in `0x01a_action.cpp` compensates. Key items, however, use the
> **real** ids (`3072 + realId`). For a new custom mount, use the real id consistently everywhere
> and you sidestep all the legacy off-by-one.

---

## 4. Server-side anatomy (CatsEyeXI / LSB)

Every field in the mount chain and its width:

| Field | Width | Caps | File |
|---|---|---|---|
| `ACTIONBUF_MOUNT.MountId` (c2s 0x01a) | `uint32` | — | `packets/c2s/0x01a_action.h` |
| `m_mountId` | `uint8` | 255 | `entities/charentity.h:350` |
| `mount_id` (s2c 0x037) | `uint8` | 255 | `packets/char_status.cpp` |
| `MountIndex : 8` (s2c 0x00D) | 8-bit | 255 | `packets/char_update.cpp` |
| `MountDataTbl[8]` (s2c 0x0AE) | 8 bytes | 64 menu | `packets/s2c/0x0ae_mount_data.h` |

Ownership = **key items**. A mount is owned iff the player has key item `3072 + mountId`:

```
CHOCOBO_COMPANION = 3072
key items stored as xi::bitset<512> per table; table = id/512, bit = id%512
→ mount unlocks live in keys.tables[6], bit = mountId   (3072 = 6 * 512)
```

`0x0AE` `memcpy`s the first **8 bytes** of `keys.tables[6].keyList` (64 bits) — hence the 64-mount
menu cap. Retail mount companions run **3072–3110** (= mount ids 0–38), the native mask covers
**3072–3135** (= mount ids 0–63), and **3136** is the next non-mount key item
(`SHEET_OF_SHADOW_LORD_TUNES`). So the key-item space independently confirms the 64-mount ceiling.

### Two independent gates (display vs ride)

A mount "appearing in the menu" and "being rideable" are **separate gates**:

- **Display / client access** = the `0x0AE` bit. The client copies `MountDataTbl` into
  `PTR_pGlobalNowZone->MountSys.MountDataTbl` and uses it to decide which mounts it *offers*.
- **Ride / server validation** = `hasKeyItem(3072+id)` in the `0x01a` Mount handler; with no key
  item it silently does nothing — the ride is rejected even if the menu offered it.

Granting key item `3072+id` satisfies **both** at once (simplest, and the native mechanism). A
key-item-less unlock that only sets the display bit will *show but not ride* — you'd also have to
relax the `0x01a` check or force-mount via `addStatusEffectEx(MOUNTED, …, id, …)`.

---

## 5. String tables (names, help, key-item text) — EN + JP

A complete mount has localized text in **six `d_msg` DATs** (three resources × EN/JP). The
language file-ids come from the client's `DatIndices.cpp` (parallel EN/JP/FR/DE tables):

| Resource | EN file-id → DAT | JP file-id → DAT | format |
|---|---|---|---|
| Mount name | `0x0D981` → `ROM/351/84.DAT` | `0x0D909` → `ROM/351/82.DAT` | sequential, 80-byte stride, **index = mount id** |
| Mount help text | `0x0D982` → `ROM/351/85.DAT` | `0x0D90A` → `ROM/351/83.DAT` | sequential, index = mount id |
| Key-item name + desc | `0x0D999` → `ROM/175/35.DAT` | `0x0D921` → `ROM/175/34.DAT` | **XOR-`0xFF`**, **marker-keyed** (`sub[0].marker` = key-item id) |

### String-table formats

Both are `d_msg` tables (plaintext `d_msg` header; decode per `StringTableParser`), but they differ:

- **Mount name / help — sequential, fixed 80-byte stride, indexed by position.** To place a name at
  index 50 the table must have 51 blocks (0–50); 39–49 become empty filler. Harmless because the
  menu shows owned mounts only, so the fillers never display.
- **Key items — XOR-`0xFF` encrypted, marker-keyed, category by table order.** The string section is
  XOR-`0xFF`; each key item is a block whose first entry's marker is the key-item id
  (`associateBlocksById` in `KeyItemTable.kt`), so the table is sparse. Text slots differ by
  language: EN uses **4 = singular name, 5 = plural, 6 = description**; JP uses **1 = name,
  2 = description**. For mount key items, do **not** append the block to the file tail; insert it
  into the Mounts section before the `-Mounts` separator row.

Only the **mount name** (`ROM/351/84.DAT` + JP) is required for the mount to work — it's the menu
label. Mount help and key-item text are optional polish (the help line in the mount window, and the
Key Items menu label/description). The mount unlock/ride run off the key-item *bit*, which needs no
text at all.

### Key Items > Mounts collection category

The Key Items menu category is not a hardcoded `FFXiMain.dll` range for the current mount work. The
client loads the key-item `d_msg` table in physical order and uses marker-0 separator rows as category
footers. In the live EN and JP tables, retail mount key-item ids `3072–3110` are immediately before
the Mounts separator:

| Language | Key-item DAT | Retail mount row positions | Mounts separator |
|---|---|---:|---:|
| EN | `ROM/175/35.DAT` | `2651–2689` | `2690` (`-Mounts`) |
| JP | `ROM/175/34.DAT` | `2651–2689` | `2690` (`-マウント`) |

The old inject behavior appended a missing custom key-item block, so mount id 50 / key item `3122`
landed at the table tail and was filed under the wrong collection category. The current
`set_key_item()` removes any existing `3072+id` block and reinserts it into the Mounts section,
preserving numeric ids. For the project custom mount, the live EN and JP `3122` blocks now sit at row
`2690`, immediately before the separator that shifted to `2691`.

This fixes Key Items > Mounts for ids inside the existing `0–63` menu mask without patching
`FFXiMain.dll`. It does not expand the native 64-mount mask.

---

## 6. Adding a custom mount

### File-id / key-item for a given mount id

```
file_id  = 0x019131 + mountId
key_item = 3072 + mountId
```

Confirm `VTABLE[file_id] == 0` (free) before claiming. In a stock client, ids **39–62 are free**;
38 and 63 are occupied. Project custom: **id 50** → file-id `0x19163` (102755), key item **3122**,
model DAT `ROM10/10/1.DAT`, key-item name `♪Cyakko`.

### Easy path — id ≤ 63 (no client patch)

1. Build the mount model `.DAT`; place it in ROM10 (e.g. `ROM10/50/0.DAT`) and register it:
   `xi ftable set --file-id <0x019131+id> --rom 10 --subdir … --file …`.
2. Write the **mount name** at index = id in `ROM/351/84.DAT` (EN) + `ROM/351/82.DAT` (JP) —
   grow the table and pad lower empty slots as needed.
3. (Optional) Write **mount help** (`ROM/351/85.DAT` / `83.DAT`) and **key-item name + desc**
   (`ROM/175/35.DAT` / `34.DAT`, key item = `3072+id`). The key-item block must be inserted before
   the Mounts separator, not appended to the end of the table.
4. Server: define + grant key item `3072+id` (`npcUtil.giveKeyItem`), and allow the id in the Lua
   validation (§7).

Granting the key item sets the `0x0AE` bit (shows in menu) **and** passes `hasKeyItem` (ridable).
`xi dats build` performs steps 1–3 for mount actions, including Mounts-section key-item placement,
and emits the step-4 server bundle.

### Hard path — id >= 64 (needs a client patch)

The menu can't show more than 64 owned mounts without patching the client. Required additions:

- **Server cpp-patch:** widen `0x0AE` `MountDataTbl[8]` → `[32]` (256-bit mask) and `memcpy` 32 bytes.
- **Client patch via `xidats`** (an Ashita v4 plugin that already memory-patches `FFXiMain.dll`):
  read the wider mask and raise the menu-iteration bound 64 → 256.
  **RE lead (atom0s XiPackets 0x00AE):** the client copies `MountDataTbl` into
  `PTR_pGlobalNowZone->MountSys.MountDataTbl` and reads that buffer for "castable mounts". Search
  `MountSys` / `MountDataTbl` / `pGlobalNowZone` in the `xi dll ffximain unpack` → Ghidra output
  ([../ffximain/ffximain.md](../ffximain/ffximain.md)). Also watch for a clamp-to-63 (chocobo) fallback.

Widening `m_mountId` to a `uint16` is a *further, separate* job, only needed for **>255 distinct
models** — not for the menu.

---

## 7. Lua validation: prefer a registered/key check over `MOUNT_MAX`

The stock Lua gates with `mount >= xi.mount.MOUNT_MAX`. With a **sparse** custom list (real ids
0–38 + 50) a max check is wrong: bumping `MOUNT_MAX` to 51 also lets `!mount 45` through, even
though 45 has no model/name/key item — the client then resolves a null file-id and renders nothing.

Check the right notion in the right place:

- **"Is this a real mount?"** → validate against a **registered-mounts table** (id → name). Use for
  GM/force paths, which intentionally ignore ownership.
- **"Does the player own it?"** → `hasKeyItem(3072 + id)`. The native player path already enforces this.

Make a single registered-mounts table the source of truth and `MOUNT_MAX` disappears — sparse ids
work and gaps fail for the right reason. Files leaning on `MOUNT_MAX`: `scripts/commands/mount.lua`,
`scripts/zones/Upper_Jeuno/npcs/Mapitoto.lua` (the *Full Speed Ahead* unlock NPC),
`scripts/effects/mounted.lua`.

---

## 8. Mechanisms referenced

- **xidats** (`D:\xi-server\xidats`) — Ashita v4 C++ plugin; runtime memory-patches
  `FFXiMain.dll` (signature scan + unprotect + `cmp` rewrites + JMP detours). The place to add
  client-side mount-menu widening. The file on disk stays untouched (it patches memory at load).
- **cpp-patches** (`D:\xi-server\xi-modules\cpp-patches`) — server-side git-diff patches applied
  to the CatsEyeXI source via dbtool. Server only; order-dependent. Use for the `0x0ae` widen and for
  edits to existing upstream Lua so they survive merges. Net-new Lua files can be plain.
- **FFXiMain.dll RE** — [../ffximain/ffximain.md](../ffximain/ffximain.md) (POL1 unpacker, Ghidra/IDA
  workflow, xiclient symbol cross-reference). ROM10 is confirmed to load.

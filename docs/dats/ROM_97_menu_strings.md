# ROM/97/*.DAT — `XISTRING` Menu-String Tables

The `ROM/97/` directory holds a family of **`XISTRING`** files — the localised text pools that
fill in-game menu labels, button captions, config-screen options, lobby messages, and the
**mission/quest menu category names**. This is the answer to "where are the literal mission
menu strings": they are plain text in `ROM/97/41.DAT`, not in `FFXiMain.dll`.

The DLL only contains the generic plumbing that opens a menu *by name* (`"menu    missionm"`,
referenced once at code VA `0x1014e8a4 → call 0x1015e260`). The menu's **layout/rows** live in
`ROM/0/1.DAT` (section tag `mis2`, name `missionm`; see [`xi ui layout menu-pos`](../../src/xi/ui/xi_menu_pos.py)),
and each row's **label** is one of these `XISTRING` entries.

---

## `XISTRING` file format

```
+0x00  char[8]   magic      "XISTRING"
+0x20  uint32    file size
+0x24  uint32    string count  (N)
+0x28  uint32    index size in bytes  (= N * 12)
+0x2c  uint32    (secondary offset — purpose unconfirmed)
+0x34  uint32    id / hash / timestamp (varies per file)
+0x38  index[N]  12 bytes each: { offset:uint32, length:uint32, flags:uint32 }
...    blob      null-terminated strings; entry offsets are relative to the blob base
```

- **Index base:** `0x38`.
- **String blob base:** `0x38 + N*12`. Each index entry's `offset` is relative to this base.
- **`length`** includes the trailing null.
- **`flags`** is `0` for all observed entries.
- Strings are mostly ASCII; some are Shift-JIS (full-width brackets `【】` = `0x8179/0x817a`
  used for section headers, `0x816b/0x816c` `〔〕` used in some files).

### Reference parser

```python
import struct
def parse_xistring(data: bytes):
    assert data[:8] == b'XISTRING'
    n  = struct.unpack_from('<I', data, 0x24)[0]
    sb = 0x38 + n * 12                     # string blob base
    out = []
    for i in range(n):
        off, ln, fl = struct.unpack_from('<III', data, 0x38 + i*12)
        out.append((i, data[sb+off:sb+off+ln].rstrip(b'\x00').decode('latin1')))
    return out
```

### Editing / adding a string

To rename a label: find its index, overwrite the bytes in-place if the new string is the same
length or shorter (keep the null), and leave `length` as-is or update it.

To **add** a string: append the text to the blob, append a 12-byte index entry
`{offset, length, 0}`, increment the count at `+0x24`, bump the index size at `+0x28` by 12,
and fix the file size at `+0x20`. Note that the string blob shifts by 12 bytes when you grow
the index, so all existing entry offsets (which are blob-relative) stay valid — but any code
that hard-codes the blob base must be recomputed.

---

## `ROM/97/41.DAT` — General UI menu labels  ★ mission categories live here

**Size:** 3,425 bytes **Count:** 143

The master pool of short UI menu labels: main-menu buttons, sub-menu captions, config
sections, and the mission/quest category names. Selected entries:

| Index | String | Index | String |
|---|---|---|---|
| 14 | Bazaar | 85 | **Missions** (menu title) |
| 18 | Auction | 100 | Quests |
| 20 | Equipment | 130 | Categories |
| **21** | **San d'Oria** | **131** | **Chains of Promathia** |
| **22** | **Bastok** | 134 | Assault |
| **23** | **Windurst** | **135** | **Treasures of Aht Urhgan** |
| **24** | **Rise of the Zilart** | 136 | Aht Urhgan: Current Quests |
| 25–36 | `<Nation>: Current/Completed Quests` | 137 | Aht Urhgan: Completed Quests |
| 59 | Key Items | 140 | Besieged |

**The six mission categories are indices 21, 22, 23, 24, 131, 135** — i.e.
San d'Oria, Bastok, Windurst, Rise of the Zilart, Chains of Promathia, Treasures of Aht Urhgan.
There is **no "Wings of the Goddess" mission-category string in this file**, which matches the
observed `missionm` menu in `ROM/0/1.DAT`: six rows reference a sequential text-id block, and a
seventh row references a non-adjacent id whose label is not in 97/41 (still to be located).

> **Open item:** the `missionm` row text-ids (observed `616–621` + `660`) are *global* ids,
> not the *local* indices above (which only go 0–142). The global-id → (file, local-index)
> mapping is not yet pinned. Confirming the exact in-game category list will resolve which file
> the seventh row pulls from.

---

## `ROM/97/39.DAT` — Chat-filter & log-config menu

**Size:** 5,582 bytes **Count:** 170

Strings for the chat filter / message-log configuration menu (e.g. *"Attacks by you"*,
*"System Lv. 1 (Check notices)"*). Also contains bracketed section headers used as menu titles:

| Index | String |
|---|---|
| 135 | `【Quests】` |
| 137 | `【Missions】` |

These are **headers**, not the category rows — the rows are the labels in 97/41.

---

## `ROM/97/48.DAT` — Region / area names

**Size:** 867 bytes **Count:** 34

The conquest/region list (used by Region Info, map, and conquest menus). **Not** mission
categories, though the first three entries coincide with nation names.

```
0 San d'Oria   7 Gustaberg   14 Qufim          21 Dynamis             28 Empire of Aht Urhgan
1 Bastok       8 Derfland    15 Li'Telor       22 Movalpolos          29 West Aht Urhgan
2 Windurst     9 Sarutabaruta 16 Kuzotz        23 Tavnazian Marquisate 30 Mamool Ja Savagelands
3 Jeuno       10 Kolshushu    17 Vollbow        24 Tavnazian Archipel. 31 Halvung Territory
4 Ronfaure    11 Aragoneu     18 Elshimo Lowl.  25 Promyvion           32 Arrapago Islands
5 Zulkheim    12 Fauregandi   19 Elshimo Upl.   26 Lumoria             33 Ruins of Alzadaal
6 Norvallen   13 Valdeaunia   20 Tu'Lia         27 Limbus
```

---

## `ROM/97/36.DAT` — Lobby / config / nation-intro text (English)

**Size:** 14,244 bytes **Count:** 271

POL/lobby and configuration text, plus the nation introduction blurbs shown at character
creation. The expansion names appear here only as **descriptive text**, not menu categories:

| Index | Role |
|---|---|
| 29 / 40 / 51 | Nation titles: *The Kingdom of San d'Oria*, *The Republic of Bastok*, *The Federation of Windurst* |
| 30 / 41 / 52 | Nation description paragraphs |
| 210 / 212 / 214 | Lobby "… UNINSTALLED mode" prompts (Zilart / Promathia / Aht Urhgan) |
| 225–228 | "Please install and register …" registration messages |
| 252–256 | Title-music settings, referencing each expansion by name (Vana'diel March, Unity, …, A New Direction / Seekers of Adoulin) |

The *"Wings of the Goddess"* / *"Seekers of Adoulin"* hits here are the **title-music option
labels** (index 255/256), **not** mission categories.

---

## `ROM/97/8.DAT` — Config / options text (Japanese)

**Size:** 13,909 bytes **Count:** 271

The Japanese-language counterpart of much of 97/36 (same count). Entries are predominantly
Shift-JIS. The *"Wings of the Goddess"* hit (index 255) is the Japanese title-music option
string, matching 97/36's index 255. **Not** mission categories.

---

## Summary — which file for what

| File | Count | Role | Mission categories? |
|---|---|---|---|
| `ROM/97/41.DAT` | 143 | General UI menu labels | **Yes — idx 21,22,23,24,131,135** |
| `ROM/97/39.DAT` | 170 | Chat-filter / log-config menu | Headers only (`【Missions】`) |
| `ROM/97/48.DAT` | 34 | Region / area names | No (nations coincide) |
| `ROM/97/36.DAT` | 271 | Lobby / config / nation intros (EN) | No (descriptive text) |
| `ROM/97/8.DAT` | 271 | Config / options (JP) | No (descriptive text) |

To **rename or add a mission category label**, `ROM/97/41.DAT` is the file to edit. Wiring a
new row into the menu also requires editing the `missionm` section in `ROM/0/1.DAT`, and the
row's *behaviour* (which mission log it opens) is governed by the client + the LSB server at
`D:/xi-server/server` — a separate problem from the label text.

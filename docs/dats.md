# DAT File Map

Community research into what lives where across the FFXI ROM file system.
This is a living document — add confirmed entries as they're identified.

---

## UI Textures

Confirmed via XIVIEW source and direct inspection.

| file_id | DAT path | Contents |
|---|---|---|
| 39541 | `ROM/119/50.DAT` | Title / splash screen texture |
| 39542 | `ROM/119/51.DAT` | Main UI sheet — fonts, cursors, buttons |
| 87 | `ROM/119/57.DAT` | Status effect icons |
| 39551 | `ROM/280/15.DAT` | Menu icons — jobs, item categories |

---

## Window Skins (`win0`)

The 8 user-selectable window background designs. Each `win0` file is one complete skin made of
four DXT1+alpha tiles (`newtex` 128×128 fill + `corner`/`hfr1`/`vfr1` 32×32 border pieces,
9-sliced to any window size). Full details: [dats/ROM_0_14-21.md](dats/ROM_0_14-21.md).

| file_id | DAT path | Design |
|---|---|---|
| 14 | `ROM/0/14.DAT` | Window design 1 |
| 15 | `ROM/0/15.DAT` | Window design 2 |
| 16 | `ROM/0/16.DAT` | Window design 3 |
| 17 | `ROM/0/17.DAT` | Window design 4 |
| 18 | `ROM/0/18.DAT` | Window design 5 |
| 19 | `ROM/0/19.DAT` | Window design 6 |
| 20 | `ROM/0/20.DAT` | Window design 7 |
| 21 | `ROM/0/21.DAT` | Window design 8 |

---

## System-Level Files

Low file_ids at the start of the FTABLE. Identified by magic header bytes.

| file_id | DAT path | Magic | Contents |
|---|---|---|---|
| 0 | `ROM/0/0.DAT` | `syst` | System core |
| 1 | `ROM/0/1.DAT` | `menu` | Main menu structure |
| 4 | `ROM/0/4.DAT` | none | Fixed-page graphics data; see [dats/ROM_0_4.md](dats/ROM_0_4.md) |
| 22 | `ROM/0/22.DAT` | none | Japanese player title strings; see [dats/ROM_0_22.md](dats/ROM_0_22.md) |
| 23 | `ROM/0/23.DAT` | `titl` | Title / login screen |
| 24 | `ROM/0/24.DAT` | `sel_` | Character selection screen |
| 27 | `ROM/0/27.DAT` | `damv` | Animation/value curves; see [dats/ROM_0_27.md](dats/ROM_0_27.md) |

---

## Menu / UI Text Strings (`XISTRING`)

Localised menu labels, button captions, config options, and lobby messages. Magic `XISTRING`;
schema confirmed — see [dats/ROM_97_menu_strings.md](dats/ROM_97_menu_strings.md). Mostly under
`ROM/97/`.

| DAT path | Count | Contents |
|---|---|---|
| `ROM/97/41.DAT` | 143 | General UI menu labels — **mission category names** (idx 21–24, 131, 135) |
| `ROM/97/39.DAT` | 170 | Chat-filter / log-config menu (`【Missions】`/`【Quests】` headers) |
| `ROM/97/48.DAT` | 34 | Region / area names (conquest list) |
| `ROM/97/36.DAT` | 271 | Lobby / config / nation-intro text (EN) |
| `ROM/97/8.DAT` | 271 | Config / options text (JP) |

The mission **menu structure** (rows/layout) is separate, in `ROM/0/1.DAT` section `mis2`
(`missionm`). The auto-translate dictionary (`ROM/168/25.DAT`) is a different format —
see [dats/ROM_168_25.md](dats/ROM_168_25.md).

> **Legacy vs. live:** `ROM/0/1.DAT` + `ROM/97/41.DAT` are the **vanilla-era** mission menu and
> are **not loaded** by the modern CatsEyeXI client. The live mission/quest text DB is
> `ROM/118/115.DAT` (`menu` magic, boot-loaded) — see below.

---

## The `ROM/118/11x` live menu / UI family

The modern client's menus, HUD and mission text live here — **not** in `ROM/0/1.DAT` (0 reads).
Read-counts from a boot ProcMon trace in parens.

| DAT | Magic | Role |
|---|---|---|
| `118/111` | `menu` | Main in-game UI widget/texture sheet (gauge, compass, itemslot, fonts, icons) |
| `118/112` | `lobb` | Lobby / logo textures (xilogo, expansion logos) |
| `118/113` | `menu` | Mission / quest text — **JP** (`sd_ms`/`zl_ms`/`pr_sc`/… , no `_e`) |
| `118/114` | `menu` | Boot-loaded `mnc2`/`mon_`/`levc`/`mgc_`/`comm` numeric tables; xiclient label `MENU_Unk1`; **most-read (~65)** but FFXiMain xrefs indicate model/action-style data, not 2D layout; see [dats/ROM_118_114.md](dats/ROM_118_114.md) |
| `118/115` | `menu` | Mission / quest text — **EN** (`*_ms_e`/`*_qs_e`), **ROR-1 encoded**; xiclient label `MENU_MissionQuest` |
| `118/116` | `menu` | `mgc_` icon data |
| `118/103` | `XISTRING` | String table |

Mission/quest **body text** = `118/115` (EN) / `118/113` (JP); text is bit-rotated, decode
ROR-1 ([dat_ror1.md](dat_ror1.md)). Full detail: [dats/ROM_118_115.md](dats/ROM_118_115.md).
Expansion **display names** (Rise of the Zilart … Rhapsodies) come from the auto-translate
Expansions category in `ROM/168/25.DAT`. Top-level group labels (Nations, Altana, Add-ons…) are
not data-driven in any DAT (client-built). Neither 113/115 holds WotG/Rhapsodies/add-on text
(different system — likely `d_msg`/server).

---

## Cutscenes / Event Scripts

Per-zone, 4 files each. Documented by Atom0s / XiEvents project.
Spread across ROM3–ROM9.

Each zone block contains:

| offset | Contents |
|---|---|
| +0 | Entity definitions |
| +1 | Event bytecode |
| +2 | JP string table |
| +3 | NA string table |

> Reference: [https://github.com/atom0s/XiEvents](https://github.com/atom0s/XiEvents)

---

## Zone Maps

| ROM range | Contents |
|---|---|
| `ROM/17–18` | Base game zone maps |
| `ROM/284–286` | Expansion zone maps |

---

## Music

Lives **outside the FTABLE entirely** — not addressable by file_id.

```
sound/win/music/data/music*.bgw
```

BGW format. Not part of the ROM/FTABLE structure.

---

## Gear Models

Race-specific per-slot model DATs resolved via the FFXiMain.dll gear lookup
tables. See `xi gear json` and [gear/json.md](gear/json.md).

---

## Skeletal Entity Models (Monsters / NPCs / Objects)

The 4-range model ID formula from `FFXiMain.dll` covers **all** skeletal
entity types — not just monsters. Named NPCs (Lion, Zeid, etc.) and zone
objects (festival props, interactive objects) share the same file_id space
and DAT format. Confirmed examples:

| DAT | file_id | modelid | Type |
|---|---|---|---|
| `ROM/148/103.DAT` | 2035 | 735 | Zone object (New Years Festival) |
| `ROM/286/112.DAT` | 52498 | 2203 | Named NPC (Lion) |
| `ROM/341/100.DAT` | 100035 | 3128 | Named NPC (Lion — expansion version) |

The `xi model json` command scans all 4 ranges and will include these.
The distinction between monster / NPC / object only exists at the
database/script level (`mob_pools`, `npc_list`, zone event scripts).

See [model/json.md](model/json.md) for the full range breakdown.

---

## File Header Catalogue

First 4 bytes of every occupied DAT, captured by a full scan of all
FTABLE/VTABLE pairs (`research/scan_all_ftables.py`, file_ids 0–500,000).
**86,303** occupied DATs, **4,300** distinct 4-byte headers.

Key insight: most headers are **not** format magics — model and animation
DATs store their *asset name* at offset 0 (e.g. `gob_`, `0hm_`, `mot_`), so
the "header" is really a name prefix. True container/format magics are a small
set; the rest cluster into model-naming families and per-entity name strings.

Raw aggregated data: `research/header_summary.json` (every header with count,
total size, and example file_ids).

### Container / format magics

Genuine 4-byte format identifiers and top-level container types.

| Magic | Example file_id | DAT | Count | Contents (confidence) |
|---|---|---|---|---|
| `syst` | 0 / 1358 | `ROM/3/32.DAT` | 2 | System core |
| `menu` | 1 | `ROM/0/1.DAT` | 10 | Main menu structure |
| `lobb` | 2 | `ROM/0/2.DAT` | 6 | Lobby / character world select |
| `wave` | 3 | `ROM/0/3.DAT` | 1 | Audio wave table |
| `mgc_` | 13 | `ROM/0/13.DAT` | 3 | Magic / spell effect data |
| `titl` | 23 | `ROM/0/23.DAT` | 1 | Title / login screen |
| `sel_` | 24 | `ROM/0/24.DAT` | 24 | Character selection screen |
| `damv` | 27 | `ROM/0/27.DAT` | 1 | Animation / value curves |
| `win0` | 14–21 | `ROM/0/14.DAT` | 8 | Window chrome / skin tiles |
| `XIST` | 28 | `ROM/159/60.DAT` | 91 | Resource list / index (formerly thought "CIST") |
| `SQLE` | 31264 | `ROM/64/32.DAT` | 536 | SQL-style export / data table |
| `evte` | 30704 | `ROM/61/111.DAT` | 979 | Event / cutscene script data |
| `mot_` | 32104 | `ROM/68/76.DAT` | 4378 | Skeletal motion / animation |
| `movc` | 39534 | `ROM/108/93.DAT` | 6 | Movie / cutscene (compiled) |
| `mova` | 2282 | `ROM/146/109.DAT` | 4 | Movie / cutscene (asset) |
| `cg_j` / `cg_e` | 100539 / 101139 | `ROM/333/52.DAT` | 555 ea | CG cutscene text (JP / EN) |
| `memo` | 101739 | `ROM/345/47.DAT` | 64 | Memo / record data (?) |
| `DMB\0` | 31104 | `ROM/63/0.DAT` | 80 | Binary motion-blend container (?) |
| `\x89PNG` | 5542 | `ROM/172/90.DAT` | 66 | Embedded PNG image |
| `none` | 6735 | `ROM/25/80.DAT` | 333 | Placeholder ("none") |
| `dumm` | 3941 | `ROM/13/56.DAT` | 660 | Dummy / reserved slot |
| `dmy_` | 29536 | `ROM/61/34.DAT` | 1055 | Dummy model |

### Text / data tables — `d_*` family

`d_` + 2-char tag. Includes the long-sought string tables.

| Tag | Example file_id | DAT | Count | Contents (inferred) |
|---|---|---|---|---|
| `d_ms` | 55465 | `ROM/165/84.DAT` | 309 | `d_msg` — menu / mission / dialogue strings |
| `d_at` | 322 | `ROM/280/2.DAT` | 10 | Large attribute / data table (7 MB+) |
| `d_me` | 120 | `ROM3/0/19.DAT` | — | Message data |
| `d_si`, `d_zv`, `d_be`, `d_de`, `d_pa`, `d_ri`, `d_ct`, `d_gh` | — | — | — | Assorted `d_*` data tables (uncatalogued) |

### Character / equipment model families

Race + slot + variant naming. The first letters encode race/sex; trailing
`_x` letters are slot/variant; leading digits (`0`–`9`, `00`–`71`) are
equipment model IDs. Prefix `l`/`r`/`d` mark LOD / variant sets. This is the
same model space as `xi gear json` (see [gear/json.md](gear/json.md)).

| Code | Race / sex |
|---|---|
| `hm` | Hume Male |
| `hf` | Hume Female |
| `em` | Elvaan Male |
| `ef` | Elvaan Female |
| `tr` / `tm` / `tf` | TaruTaru (generic / male / female) |
| `mt` | Mithra |
| `gl` / `gal` | Galka |

Representative families (counts are totals across all variants):

| Family | Count | Meaning |
|---|---|---|
| `#hm_`, `#hf_`, `#em_`, `#ef_`, `#tr_`, `#mt_`, `#gl_` | ~3,400 ea | Equipment model, race-keyed, variant digit |
| `##hm` … `##gl` | ~1,550 ea | Equipment model, 2-digit variant |
| `<race>_b` | ~1,160 ea | Base body model |
| `l<race>` (lhm_, lgal …) | ~1,086 ea | LOD / low-detail model |
| `r<race>` (rhm_, rgal …) | ~364 ea | Variant model set |
| `<race>_<slot>` (`_e`,`_g`,`_s`,`_t`,`_k`,`_j`,`_c`,`_d`,`_h`,`_f`,`_l`,`_m`,`_r`,`_y`,`_w`) | varies | Per-slot / per-part model |
| `bp_d`, `bp_f`, `bp_t`, `bp_h`, `bp_e`, `bp_m`, `bp_s` | ~600 total | Blueprint / body-part model set (?) |

### Entity (monster / NPC / object) model names

The largest group by *distinct headers*: short creature/NPC name prefixes
embedded at offset 0 of each skeletal model DAT (`gob_` goblin, `ork_` orc,
`skel`/`skl#` skeleton, `slim` slime, `drk_`/`dra_` dragon, `lion`, `zeid`,
`odin`, `baha` Bahamut, `chao`, etc.). Hundreds of distinct prefixes, mostly
1–50 occurrences each. These are name strings, not format magics, and live in
the entity model space (see [model/json.md](model/json.md) and
`xi model json`).

### Binary / non-magic headers

1,385 distinct headers have no printable ASCII tag. Almost all are
little-endian counts/sizes (`04000000`, `01000000`, `0e000000` …) at the head
of sub-file or texture blobs — these are data DATs with no format magic, not
distinct file types. Notable real magics hiding here are `DMB\0` and `\x89PNG`
(listed above).

---

## Unknown / To Investigate

- `d_*` data tables beyond `d_msg`/`d_at` — purpose of `d_si`, `d_zv`,
  `d_be`, `d_de`, `d_pa`, `d_ri`, `d_ct`, `d_gh` not yet identified.
- `SQLE` / `damv` / `memo` / `DMB` — meanings inferred, need byte-level confirmation.
- Item model DATs beyond gear slots (furniture, key items, etc.)
- NPC model DATs (separate from monster models)
- Ability / spell effect textures
- Loading screen textures

---

## Tools

```
uv run xi ftable lookup --file-id N  # resolve any file_id → DAT + header bytes
uv run xi ftable range-scan          # scan for occupied file_id blocks
uv run xi gear json                  # dump all gear model → DAT mappings
uv run xi model json                  # dump all entity model (monsters, NPCs, objects) → DAT mappings
```

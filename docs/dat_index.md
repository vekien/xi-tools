# DAT Index — what lives where (simple)

Quick, grouped, plain-English index of FFXI DAT files. For detail, follow the linked docs.
For the full evidence-based map see [dats.md](dats.md); for what loads at boot see
[dats_boot.md](dats_boot.md).

> **How files are addressed:** the game uses a `file_id` → FTABLE/VTABLE → `ROM<n>/<sub>/<idx>.DAT`
> lookup, never the path directly. See [reference/model-file-ids.md](reference/model-file-ids.md).

> **Override order:** the live client loads base-game ROM, then server override
> DATs and pivot files, then ffxi-hd overrides. An override replaces the
> base file at the same ROM path.

> **Load-status tags** come from a ProcMon trace (`misc/procmon logs.CSV`,
> parser `research/procmon_dat_counts.py`). That run = boot → login → enter **North Gustaberg**
> (`ROM/0/123` ×52), **EN** client, **window design 1** active. So:
> - `[live ×N]` = read N times in the trace (confirmed loaded).
> - `[legacy]` = not loaded **and** superseded by a newer file — safe to treat as dead.
> - `[conditional]` = not in this trace but loads in other situations (other language, the
>   char-select screen, a different window theme, a different zone). Not dead — just not hit here.
> A trace is one session, so "not loaded" ≠ "never loaded" unless marked `[legacy]`.
>
> ⚠️ **Environment caveat:** the traced install is a **private-server client with Ashita/XIPivot
> overlays**, not clean retail. Load-order and 0-reads observations are environment-specific
> and may not hold on a clean retail install (overlay DATs can satisfy reads the trace
> attributes elsewhere, or shadow files retail would read).

### Load status at a glance (indexed files)

| DAT | Reads | Status | Note |
|---|--:|---|---|
| `118/114` | 65 | live | most-read menu file (`mnc2`) |
| `0/123` | 52 | live | zone (North Gustaberg) — the trace's zone |
| `118/115` | 20 | live | mission/quest text (EN) |
| `119/51` | 18 | live | main UI atlas |
| `119/50` | 7 | live | title / logos |
| `0/14` | 6 | live | window skin — **design 1 (the active theme)** |
| `0/0` | 4 | live | combat/HUD sprites |
| `280/15` | 3 | live | menu icons |
| `0/23` | 2 | live | title scene |
| `0/27` | 2 | live | UI anim curves (`damv`) |
| `97/36` | 2 | live | config / lobby text (EN) |
| `97/38` | 2 | live | string table |
| `168/25` | 2 | live | auto-translate dict |
| `118/103` | 1 | live | string table |
| `119/57` | 1 | live | status-effect icons |
| `0/4`–`0/9`, `0/12` | 1 | live | system / fixed-page graphics |
| `0/1` | 0 | **legacy** | superseded by `119/51` (+ vanilla `missionm`) |
| `97/41` | 0 | **legacy** | vanilla mission labels — superseded |
| `0/15`–`0/21` | 0 | conditional | other window themes (load only when selected) |
| `0/24`–`0/26` | 0 | conditional | char-select screens (not in this trace's path) |
| `118/113` | 0 | conditional | mission text **JP** (EN client loaded `115` instead) |
| `97/8` | 0 | conditional | config/options **JP** |
| `0/2` | 0 | conditional | JP lobby/logos (`119/50` is the EN set) |
| `0/13`, `118/111`, `118/112`, `118/116`, `97/39`, `97/48` | 0 | conditional | not hit in this trace's path |

---

## System / core

### ROM/0/0.DAT
* `syst` — system core. (ffxi-hd overrides it.)

### ROM/0/4.DAT … ROM/0/8.DAT
* No magic. Fixed-page graphics/system data (4096 records × 0xC00 bytes).
* Precompiled low-level boot/UI render data — **not** editable as textures.
* Detail: [dats/ROM_0_4.md](dats/ROM_0_4.md)

### FTABLE/VTABLE pairs (ROM, ROM2…ROM10)
* The model file lookup tables. `FTABLE` = path bits, `VTABLE` = ROM index.
* ROM10 = the custom content slot used by xi tooling.
* Detail: [reference/model-file-ids.md](reference/model-file-ids.md), [ftable/](ftable/lookup.md)

---

## UI textures & window skins

> **All editable** with the `xi ui tex` flow: `uv run xi ui tex sx "ROM\..\..DAT"` (export→PNG),
> edit the PNGs, `uv run xi ui tex si "ROM\..\..DAT"` (rebuild→import). See [QUICKY.md](QUICKY.md).

> Texture/section names below are the per-file export folder names under `exports/ui/<dat>/`.

### ROM/119/51.DAT  — **the live main UI atlas** ★ edit this one
* `menu`, ~61 textures. The full in-game HUD/menu sheet: fonts (`font`, `moji`, `menu2fon`,
  `mn3font`…`mn12font`, `dg_font`), cursors (`acre`/`adcre`/`bcre`/`jcre`/`mcre`/`scre`/`wcre`,
  `yubi` finger), buttons (`buttonto`/`lrbutton`/`xlbutton`/`photobut`), `gauge`, `compass`,
  maps (`mapat`/`maphp`/`mapsg`), markers (`markblue`/`green`/`red`/`marker`), element icons
  (`eldark`/`elfire`/`elice`/…), `itemslot`, `keytop(hd)`, `msgicon`, `news`, `ustats(hd)`.
* HD variants (`keytophd`, `ustatshd`, `itm2genr`) = the live updated set. (ffxi-hd overrides it.)

### ROM/0/1.DAT  — vanilla copy of the UI atlas (NOT loaded)
* `menu`, ~68 textures — **same texture set as `119/51`** (its predecessor). Editing it does
  **nothing** (0 reads — not loaded). Use `119/51` instead. Also holds the legacy `mis2`/`missionm`
  menu layout (see Menus section).

### ROM/0/0.DAT
* `syst`, ~60 textures — combat/HUD sprites: damage/hit numbers (`hit10`…`hit290`), element
  icons (`ei30`…`ei63`, `eisyou*`), `pet1`, `plight`, `torch1`, `asi1/2`.

### ROM/0/13.DAT
* `mgc_`, 8 textures — magic / ability / emote / job icons (`blu`, `card`, `dice`, `emot`,
  `magicon1`, `mnt`, `ninj2`, `ustats`).

### ROM/119/50.DAT
* `lobb`, 9 textures — title/logo art: expansion logos (`ex1us`/`ex2us`/`ex5us`), `titlwin`
  (title window), `abxy360` (controller buttons), `chmkfnt`, `wardrb`, `otp`, `b1n`.
* **`.psd` source files present** in `exports/ui/119/50/` (`ex1us`, `ex2us`, `ex5us`, `titlwin`).

### ROM/0/2.DAT
* `lobb` — JP lobby/logo set: `xilogo`, expansion logos JP (`ex1jp`/`ex2jp`/`ex5jp`), `chmkfnt`,
  `lbfontp`, `otp`. (JP counterpart of `119/50`.)

### ROM/119/57.DAT
* Status-effect icons.

### ROM/280/15.DAT
* Menu icons — jobs, item categories (`mgc_`).

### ROM/0/14.DAT … ROM/0/21.DAT  — the **UI window themes**
* The **8 window background skins / themes** (`win0`) — the window-design choices in config.
  These are the files you edit to re-skin windows (e.g. `ROM/0/15.DAT` = design 2).
* Each = 4 DXT1+alpha tiles (`newtex` 128×128 fill + `corner`/`hfr1`/`vfr1` 32×32 borders),
  9-sliced to any window size.
* Edit with `ui tex sx`/`si` (export is `xi ui tex export`).
* **Only the active theme loads** — the trace read `0/14` (design 1) ×6 and none of `0/15–21`.
  To preview an edit, set that design in-game config (or edit whichever design is active).

| File | Theme | File | Theme |
|---|---|---|---|
| `ROM/0/14.DAT` | design 1 | `ROM/0/18.DAT` | design 5 |
| `ROM/0/15.DAT` | design 2 | `ROM/0/19.DAT` | design 6 |
| `ROM/0/16.DAT` | design 3 | `ROM/0/20.DAT` | design 7 |
| `ROM/0/17.DAT` | design 4 | `ROM/0/21.DAT` | design 8 |

* Detail: [dats/ROM_0_14-21.md](dats/ROM_0_14-21.md)

---

## Menus & on-screen text

### ROM/0/1.DAT  (vanilla, NOT loaded — see UI section)
* `menu` — vanilla main menu structure + UI atlas (same textures as the live `119/51`).
* Holds the legacy `mis2`/`missionm` mission-menu **layout**; modern mission *text* is in
  `ROM/118/115.DAT` and the menu family is `ROM/118/11x`, so this is effectively dead.
* `xi ui layout menu-pos` can parse it, but edits don't reach the live client (0 reads).

### ROM/97/*.DAT — menu label strings (`XISTRING`, plain ASCII)
* `97/41` (143) **[legacy — not loaded]** — general UI labels; **vanilla mission category names**
  (idx 21–24, 131, 135). Superseded; not read by the live client.
* `97/39` (170) — chat-filter / log-config menu (`【Missions】`/`【Quests】` headers).
* `97/48` (34)  — region / area names (conquest list).
* `97/36` (271) — lobby / config / nation-intro text (EN). **Boot-loaded.**
* `97/8`  (271) — config / options text (JP).
* `97/38`, `118/103` — other `XISTRING` string tables (boot-loaded).
* Format + per-file detail: [dats/ROM_97_menu_strings.md](dats/ROM_97_menu_strings.md)

### ROM/118/11x — the **live in-game menu/UI family** (`menu`/`lobb`)
The modern client's menus/HUD/mission text live here (not in `ROM/0/1.DAT`, which has **0 reads**).
ProcMon read-counts from a boot trace in parens — higher = hit more often.

* **`118/111`** (4 MB, `menu`) — main in-game **UI widget / texture sheet**: `gauge`, `buttonto`,
  `compass`, `msgicon`, `itemslot`, `keytop`, `marker`, `ustats`, `menu2fon` (menu font), `cicon`…
  The live HUD elements. (GPT guessed "play/polw/titl/char" here — that's wrong; those are vanilla `0/1`.)
* **`118/112`** (416 KB, `lobb`) — lobby / logo textures: `xilogo`, expansion logos
  (`ex1us`/`ex2us`/`ex5us`), `15logo`, lobby help.
* **`118/113`** (472 KB, `menu`) — **mission/quest text (JP)**: `sd_ms`/`bs_ms`/`ws_ms`/`zl_ms`/
  `pr_sc`/`as_ms`/`at_ms` + `*_qs` (no `_e` suffix = Japanese). Sibling of `115`.
* **`118/114`** (403 KB, `menu`, **65 reads — most-hit menu file**) — `mnc2` "menu construct"
  table: an offset-indexed list of **numeric records** (id/index pairs, e.g. `03 00 04 00`),
  not text, plus `mon_`/`levc`/`mgc_`/`comm` sub-sections. A core lookup the client constantly
  reads; **prime suspect for menu element/layout data** (unconfirmed — not pixel x/y as-is).
* **`118/115`** (481 KB, `menu`) — **mission/quest text (EN)**: `sd_ms_e`/`bs_ms_e`/`ws_ms_e`
  (nations), `zl_ms_e`/`pr_sc_e`/`at_ms_e`/`as_ms_e`, `*_qs_e` quests. **Text is bit-rotated** —
  decode ROR-1, see [dat_ror1.md](dat_ror1.md). Detail: [dats/ROM_118_115.md](dats/ROM_118_115.md).
* **`118/116`** (98 KB, `menu`) — `mgc_` icon data (small).
* **`118/103`** (`XISTRING`) — string table, boot-loaded.

> Neither `113` nor `115` contains WotG/Rhapsodies/add-on mission text — those use a different
> system (likely `d_msg` / server).

### ROM/0/1.DAT — NOT loaded (0 reads)
* Vanilla menu/`missionm` layout + fonts. Edits here have **no effect** on the live client.

### Menu **positions** (still open)
* Edits to `0/1` (0 reads), `119/51`, `0/27` produced no visible movement. `0/1` is ruled out
  (not loaded). Best remaining leads: **`118/114` (`mnc2`)** and the `118/111` widget sheet;
  `0/24–26` (`sel_`) for the login/select screens. Not yet pinned.

### Top-level mission group labels (Nations, Zilart, Altana, Add-ons…)
* **Not found in any DAT or FFXiMain.dll** (plain or ROR-1). Appear client-built/hardcoded. OPEN.

---

## Auto-translate dictionary

### ROM/168/25.DAT (EN), ROM/176/96.DAT (DE), ROM/178/35.DAT (FR)
* The `{auto-translate}` phrase database. **Boot-loaded.** Plain ASCII.
* 41 categories (Greetings, Place Names, **Expansions**, …); entry prefix `02 02 <cat> <idx>`
  is the literal auto-translate insertion code.
* The mission menu sources **expansion display names** (Rise of the Zilart … Rhapsodies of
  Vana'diel) from the **Expansions** category here.
* Detail: [dats/ROM_168_25.md](dats/ROM_168_25.md)

---

## Title / login / character-select scenes

### ROM/0/23.DAT
* `titl` — title-screen 3D scene: 22 background zones + camera splines + weather/fog.
* Detail: [dats/ROM_0_23.md](dats/ROM_0_23.md)

### ROM/0/24.DAT, ROM/0/25.DAT, ROM/0/26.DAT
* `sel_` — character-selection scene controllers (object placement, motion `mov1`/`loop`/`end`).
  Three variants/states. Detail: [dats/ROM_0_24.md](dats/ROM_0_24.md) (+25/+26).

### ROM/0/27.DAT
* `damv` — UI animation / value-curve table (damage, cursor, mission pop/scale/alpha effects).
* Detail: [dats/ROM_0_27.md](dats/ROM_0_27.md)

### ROM/0/2.DAT
* `lobb` — lobby / character-world select.

---

## Zone data

### ROM/17–18 (base), ROM/284–286 (expansions)
* Zone maps.

### `d_msg` family (`d_ms` magic) — huge across ROM/165, 169, 173, 176, 180, 181, 196, 314, 324…
* Menu / mission / dialogue / NPC string tables (per-zone and global). Many boot-loaded.
* e.g. `ROM/165/84.DAT` = zone-name table. The newer expansion mission text likely lives in
  `d_msg` (not yet pinned).
* Other `d_*` tables: `d_at` (large attribute table, e.g. `ROM/280/2.DAT`, 7 MB+),
  `d_me` (message data). `d_si`/`d_zv`/`d_be`/`d_de`/`d_pa`/`d_ri`/`d_ct`/`d_gh` uncatalogued.

---

## Models & animation

### Gear / equipment models
* Race + slot + variant model DATs, resolved via FFXiMain.dll gear lookup tables.
* Named at offset 0 by race/sex code + variant: `hm`/`hf` Hume M/F, `em`/`ef` Elvaan M/F,
  `tr`/`tm`/`tf` Taru, `mt` Mithra, `gl`/`gal` Galka (e.g. `#hm_`, `##gl`, `hm_b` base body,
  `lhm_` LOD). Same space as `xi gear json`.
* List them: `xi gear json`. Detail: [gear/json.md](gear/json.md)

### Skeletal entity models (monsters / NPCs / objects)
* One file_id space + DAT format for all skeletal entities; 4-range modelid → file_id formula.
* Named at offset 0 by creature prefix: `gob_` goblin, `ork_` orc, `skel`/`skl#` skeleton,
  `slim` slime, `dra_`/`drk_` dragon, `lion`, `zeid`, `odin`, `baha`, etc.
* List them: `xi model json`. Detail: [model/json.md](model/json.md),
  [reference/model-file-ids.md](reference/model-file-ids.md)
* Mesh format / Blender round-trip: max **2 joint influences per vertex**
  (mesh import/export gotchas in memory: `entity-mesh-import-export`).

### Animation / motion (`mot_`)
* Skeletal motion/animation DATs. Format: [anim/format.md](anim/format.md)

### Cutscenes / events (`evte`, `movc`, `mova`, `cg_j`/`cg_e`)
* Per-zone event scripts (4 files/zone across ROM3–ROM9): entities, bytecode, JP text, NA text.
* Reference: atom0s/XiEvents.

---

## Audio
* Lives **outside** the FTABLE: `sound/win/music/data/music*.bgw` (`BGW` format).

---

## FFXiMain.dll  (not a DAT, but related)
* Packed with the POL1 packer; `.text` is LZSS-compressed in section `POL1`.
* Unpack with `xi dll ffximain` tools → `pol_decompressed.bin`/`.txt` for analysis.
* Holds the monster modelid→file_id formula and gear lookup tables (not in any DAT).
* Detail: [reference/ffximain.md](reference/ffximain.md)

---

## Format magic quick-reference

| Magic | Meaning |
|---|---|
| `syst` | System core |
| `menu` | Menu structure / mission-quest text (body text is **ROR-1 encoded**) |
| `lobb` | Lobby / world select / title textures |
| `titl` | Title-screen 3D scene |
| `sel_` | Character-select scene controller |
| `damv` | UI animation / value curves |
| `win0` | Window skin (DXT tiles) |
| `XIST` / `XISTRING` | Menu label string table (**plain ASCII**) |
| `d_ms` | `d_msg` string/dialogue table |
| `mgc_` | Menu/magic icon sheet |
| `mot_` | Skeletal motion/animation |
| `evte` | Event / cutscene script |
| `movc` / `mova` | Movie / cutscene (compiled / asset) |
| `cg_j` / `cg_e` | CG cutscene text (JP / EN) |
| `d_at` / `d_me` | Large attribute table / message data |
| `SQLE` | SQL-style export / data table |
| `memo` | Memo / record data (?) |
| `DMB\0` | Binary motion-blend container (?) |
| `mgc_` | Menu / magic icon sheet |
| `\x89PNG` | Embedded PNG image |
| `dumm` / `dmy_` | Dummy / reserved slot / dummy model |
| `02 02 ..` | Auto-translate dictionary (no ASCII magic) |
| none | Fixed-page graphics data (ROM/0/4–8) |

---

## Encoding cheat-sheet
* `XISTRING` (ROM/97) + auto-translate dict (168/25) → **plain ASCII**.
* `menu` body text (118/115) → **ROR-1** (rotate right 1 bit). See [dat_ror1.md](dat_ror1.md).
* To search bit-rotated files: encode the needle with ROL-1, then `find()`.

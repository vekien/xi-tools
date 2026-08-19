# FFXI Character Creation DATs

Source: `D:\xi-tools\research\procmon_character_creation_dats.txt`
(unique paths opened while walking every race × face 1/2 in character creation).

Game root used for classification: `D:\Final Fantasy XI\SquareEnix\FINAL FANTASY XI`

Machine classification + cross-reference against `xi-model-viewer/ui/js/creation.js`
`CREATION_RACES` / clip cluster offsets.

## Summary counts

| kind | count | meaning |
|---|---:|---|
| `sqle_motion` | 176 | SQLE MOTION (FrameChannel or PBChannel) — skeleton clip or camera track |
| `dmb_material` | 72 | DMB material/texture paired with a mesh |
| `mesh_rt_shape` | 72 | RT/SHAPE high-poly mesh; SQLE type 11 skeleton + type 21 skin inside |
| `dialog_msg` | 63 | d_msg dialog/menu strings |
| `file_table` | 18 | FTABLE/VTABLE file-id maps |
| `creation_cue` | 8 | OC:01.00 cue track (frame → action id) |
| `race_action_table` | 8 | per-race action bytecode table (rt**) |
| `unknown` | 7 | not yet identified |
| `font_or_effect` | 3 | fonts / full-screen effects |
| `xistring` | 3 | XI string table |
| `ui_menu` | 3 | character-create / lobby UI menus |
| `system_dat` | 1 | system tables |
| `ui_window` | 1 | window chrome |
| `ui_title` | 1 | title UI |
| `ui_damage` | 1 | damage font/UI |
| `ui_lobby` | 1 | lobby UI |
| `user` | 1 | USER\tig.dat |

**Total unique DATs:** 439

## Key findings (viewer-relevant)

### Eight faces per race (not 4 × A/B)

Retail character creation has **8 faces**. DAT packing after the body pair is a
flat `mat + mesh` stride of 2; motion clusters stride +6 from the first face base.

| face | mesh | mat | motion base |
|---:|---|---|---|
| 1 | mesh0 | mesh0−1 | base0 |
| 2 | mesh0+2 | mesh0+1 | base0+6 |
| … | … | … | … |
| 8 | mesh0+14 | mesh0+13 | base0+42 |

An earlier misread treated face 2’s mesh as “face 1 variant B” — that mixed heads
and broke placement. There is no separate A/B mesh pair in the table.

### Motion cluster layout (per skeleton: body or head variant)

| offset | clip | encoding |
|---:|---|---|
| −4 | Motion 1 | FrameChannel |
| −3 | Motion 2 | FrameChannel |
| −2 | Standing idle | FrameChannel |
| −1 | Motion 3 | FrameChannel |
| 0 | Long creation sequence | PBChannel |
| +1 | Motion 4 (short PB coda) | PBChannel |

`bumpDatIndex` must wrap at **128 files/folder** (`66/0 − 2 → 65/126`).

### Cameras (not a zone)

Two cameras × (1-ch FOV + 16-ch 4×4 matrix). Paths explicit per race in
`CREATION_RACES.cameras` (TaruM is not bodySeq−8..−5).

### Cues / actions (seq still incomplete)

`rom/67/108`–`115` = OC:01.00 cue tracks. Eight `rt**` race action tables in the
capture. Bytecode not decoded → long sequence still pose-only in the viewer.

### ElvaanF face3 base was wrong

Table had `64/52` (face1-B’s cluster). Correct: `64/70` / idle `64/68`
(clusters every +6: f1A46 f1B52 f2A58 f2B64 **f3A70 f3B76** f4A82 f4B88).

### Validation

`D:\xi-tools\research\validate_creation_pairs.py` — **384/384** race×face×A/B×clip
channel pairs match game DATs after the fixes above.

## Zone / stage backdrop

**`ROM/1/5.DAT`** is the character-creation stage/zone package (header `f_ch` /
`effe` — ~10.5 MB). Earlier notes that said “no zone” were wrong; this file is
in the procmon capture and is the map/area behind the high-poly models.

Cameras still come from per-race SQLE FOV+matrix tracks. Lobby/menu DATs under
ROM/118–119 and dialog packs under ROM/165+ are UI chrome on top of the stage.

## Skeleton files (mesh hosts)

Skeletons are **not** standalone files. Each body/head mesh DAT embeds
an SQLE type-11 bone table (64-byte records: bind TRS + 5 channel-group
counts + parent) and type-21 skin clusters.

Capture meshes with embedded skeleton: **72**

| path | size | embeds | viewer role |
|---|---:|---|---|
| `rom/63/103.dat` | 184088 | sqle=[10, 11, 21]; embeds=skeleton,skin | Mithra body mesh (initial equipment) |
| `rom/63/105.dat` | 366280 | sqle=[10, 11, 21]; embeds=skeleton,skin | Mithra face1 mesh |
| `rom/63/107.dat` | 363928 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/109.dat` | 344168 | sqle=[10, 11, 21]; embeds=skeleton,skin | Mithra face2 mesh |
| `rom/63/11.dat` | 323240 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/111.dat` | 363592 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/113.dat` | 342840 | sqle=[10, 11, 21]; embeds=skeleton,skin | Mithra face3 mesh |
| `rom/63/115.dat` | 339832 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/117.dat` | 346296 | sqle=[10, 11, 21]; embeds=skeleton,skin | Mithra face4 mesh |
| `rom/63/119.dat` | 343272 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/123.dat` | 153608 | sqle=[10, 11, 21]; embeds=skeleton,skin | TaruF body mesh (initial equipment) |
| `rom/63/125.dat` | 337776 | sqle=[10, 11, 21]; embeds=skeleton,skin | TaruF face1 mesh |
| `rom/63/127.dat` | 337984 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/13.dat` | 265304 | sqle=[10, 11, 21]; embeds=skeleton,skin | ElvaanF face3 mesh |
| `rom/63/15.dat` | 327736 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/17.dat` | 317520 | sqle=[10, 11, 21]; embeds=skeleton,skin | ElvaanF face4 mesh |
| `rom/63/19.dat` | 298232 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/23.dat` | 186224 | sqle=[10, 11, 21]; embeds=skeleton,skin | ElvaanM body mesh (initial equipment) |
| `rom/63/25.dat` | 322792 | sqle=[10, 11, 21]; embeds=skeleton,skin | ElvaanM face1 mesh |
| `rom/63/27.dat` | 317408 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/29.dat` | 267632 | sqle=[10, 11, 21]; embeds=skeleton,skin | ElvaanM face2 mesh |
| `rom/63/3.dat` | 203392 | sqle=[10, 11, 21]; embeds=skeleton,skin | ElvaanF body mesh (initial equipment) |
| `rom/63/31.dat` | 297584 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/33.dat` | 235984 | sqle=[10, 11, 21]; embeds=skeleton,skin | ElvaanM face3 mesh |
| `rom/63/35.dat` | 287992 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/37.dat` | 331160 | sqle=[10, 11, 21]; embeds=skeleton,skin | ElvaanM face4 mesh |
| `rom/63/39.dat` | 258240 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/43.dat` | 198568 | sqle=[10, 11, 21]; embeds=skeleton,skin | Galka body mesh (initial equipment) |
| `rom/63/45.dat` | 234280 | sqle=[10, 11, 21]; embeds=skeleton,skin | Galka face1 mesh |
| `rom/63/47.dat` | 227056 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/49.dat` | 228128 | sqle=[10, 11, 21]; embeds=skeleton,skin | Galka face2 mesh |
| `rom/63/5.dat` | 309216 | sqle=[10, 11, 21]; embeds=skeleton,skin | ElvaanF face1 mesh |
| `rom/63/51.dat` | 217280 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/53.dat` | 243816 | sqle=[10, 11, 21]; embeds=skeleton,skin | Galka face3 mesh |
| `rom/63/55.dat` | 199768 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/57.dat` | 240760 | sqle=[10, 11, 21]; embeds=skeleton,skin | Galka face4 mesh |
| `rom/63/59.dat` | 241120 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/63.dat` | 192168 | sqle=[10, 11, 21]; embeds=skeleton,skin | HumeF body mesh (initial equipment) |
| `rom/63/65.dat` | 271608 | sqle=[10, 11, 21]; embeds=skeleton,skin | HumeF face1 mesh |
| `rom/63/67.dat` | 287928 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/69.dat` | 277000 | sqle=[10, 11, 21]; embeds=skeleton,skin | HumeF face2 mesh |
| `rom/63/7.dat` | 284984 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/71.dat` | 292040 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/73.dat` | 248752 | sqle=[10, 11, 21]; embeds=skeleton,skin | HumeF face3 mesh |
| `rom/63/75.dat` | 290536 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/77.dat` | 277376 | sqle=[10, 11, 21]; embeds=skeleton,skin | HumeF face4 mesh |
| `rom/63/79.dat` | 287752 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/83.dat` | 164208 | sqle=[10, 11, 21]; embeds=skeleton,skin | HumeM body mesh (initial equipment) |
| `rom/63/85.dat` | 330104 | sqle=[10, 11, 21]; embeds=skeleton,skin | HumeM face1 mesh |
| `rom/63/87.dat` | 324488 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/89.dat` | 267328 | sqle=[10, 11, 21]; embeds=skeleton,skin | HumeM face2 mesh |
| `rom/63/9.dat` | 315104 | sqle=[10, 11, 21]; embeds=skeleton,skin | ElvaanF face2 mesh |
| `rom/63/91.dat` | 308208 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/93.dat` | 167232 | sqle=[10, 11, 21]; embeds=skeleton,skin | HumeM face3 mesh |
| `rom/63/95.dat` | 341784 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/63/97.dat` | 218024 | sqle=[10, 11, 21]; embeds=skeleton,skin | HumeM face4 mesh |
| `rom/63/99.dat` | 279632 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/64/1.dat` | 331400 | sqle=[10, 11, 21]; embeds=skeleton,skin | TaruF face2 mesh |
| `rom/64/11.dat` | 344752 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/64/15.dat` | 153608 | sqle=[10, 11, 21]; embeds=skeleton,skin | TaruM body mesh (initial equipment) |
| `rom/64/17.dat` | 328704 | sqle=[10, 11, 21]; embeds=skeleton,skin | TaruM face1 mesh |
| `rom/64/19.dat` | 367960 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/64/21.dat` | 305696 | sqle=[10, 11, 21]; embeds=skeleton,skin | TaruM face2 mesh |
| `rom/64/23.dat` | 286952 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/64/25.dat` | 328704 | sqle=[10, 11, 21]; embeds=skeleton,skin | TaruM face3 mesh |
| `rom/64/27.dat` | 367960 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/64/29.dat` | 305696 | sqle=[10, 11, 21]; embeds=skeleton,skin | TaruM face4 mesh |
| `rom/64/3.dat` | 356368 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/64/31.dat` | 286952 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/64/5.dat` | 337776 | sqle=[10, 11, 21]; embeds=skeleton,skin | TaruF face3 mesh |
| `rom/64/7.dat` | 337984 | sqle=[10, 11, 21]; embeds=skeleton,skin | — |
| `rom/64/9.dat` | 331400 | sqle=[10, 11, 21]; embeds=skeleton,skin | TaruF face4 mesh |

### Body mesh / material pairs (naked + initial equipment = +2 index)

| race | naked mesh | naked mat | equipped mesh | equipped mat |
|---|---|---|---|---|
| Hume Male | `rom/63/81.dat` | `rom/63/80.dat` | `rom/63/83.dat` | `rom/63/82.dat` |
| Hume Female | `rom/63/61.dat` | `rom/63/60.dat` | `rom/63/63.dat` | `rom/63/62.dat` |
| Elvaan Male | `rom/63/21.dat` | `rom/63/20.dat` | `rom/63/23.dat` | `rom/63/22.dat` |
| Elvaan Female | `rom/63/1.dat` | `rom/63/0.dat` | `rom/63/3.dat` | `rom/63/2.dat` |
| Tarutaru Male | `rom/64/13.dat` | `rom/64/12.dat` | `rom/64/15.dat` | `rom/64/14.dat` |
| Tarutaru Female | `rom/63/121.dat` | `rom/63/120.dat` | `rom/63/123.dat` | `rom/63/122.dat` |
| Mithra | `rom/63/101.dat` | `rom/63/100.dat` | `rom/63/103.dat` | `rom/63/102.dat` |
| Galka | `rom/63/41.dat` | `rom/63/40.dat` | `rom/63/43.dat` | `rom/63/42.dat` |

## Animation / motion files

Each race has a **motion cluster** around `bodyBase` / face `base`:

| offset from base | clip | encoding (typical) |
|---:|---|---|
| -4 | Motion 1 | FrameChannel |
| -3 | Motion 2 | FrameChannel |
| -2 | Standing idle | FrameChannel |
| -1 | Motion 3 | FrameChannel |
| 0 | Long creation sequence | PBChannel |

Body clip at offset B pairs with head clip at B+6 from the head base
(same frame count). Tarutaru male is special: `seqBody = rom/67/58.dat`
while short clips still hang off `bodyBase = rom/67/4.dat`.

Capture skeleton clips (non-camera motions): **144**

### Per-race body motion anchors

| race | bodyBase | bodyIdle | seq body | cam A fov/mat | cam B fov/mat |
|---|---|---|---|---|---|
| Hume Male | `rom/66/16.dat` | `rom/66/14.dat` | `rom/66/16.dat` | `rom/66/8.dat` / `rom/66/9.dat` | `rom/66/10.dat` / `rom/66/11.dat` |
| Hume Female | `rom/65/86.dat` | `rom/65/84.dat` | `rom/65/86.dat` | `rom/65/78.dat` / `rom/65/79.dat` | `rom/65/80.dat` / `rom/65/81.dat` |
| Elvaan Male | `rom/64/98.dat` | `rom/64/96.dat` | `rom/64/98.dat` | `rom/64/90.dat` / `rom/64/91.dat` | `rom/64/92.dat` / `rom/64/93.dat` |
| Elvaan Female | `rom/64/40.dat` | `rom/64/38.dat` | `rom/64/40.dat` | `rom/64/32.dat` / `rom/64/33.dat` | `rom/64/34.dat` / `rom/64/35.dat` |
| Tarutaru Male | `rom/67/4.dat` | `rom/67/2.dat` | `rom/67/58.dat` | `rom/67/54.dat` / `rom/67/55.dat` | `rom/67/56.dat` / `rom/67/57.dat` |
| Tarutaru Female | `rom/67/4.dat` | `rom/67/2.dat` | `rom/67/4.dat` | `rom/66/124.dat` / `rom/66/125.dat` | `rom/66/126.dat` / `rom/66/127.dat` |
| Mithra | `rom/66/74.dat` | `rom/66/72.dat` | `rom/66/74.dat` | `rom/66/66.dat` / `rom/66/67.dat` | `rom/66/68.dat` / `rom/66/69.dat` |
| Galka | `rom/65/28.dat` | `rom/65/26.dat` | `rom/65/28.dat` | `rom/65/20.dat` / `rom/65/21.dat` | `rom/65/22.dat` / `rom/65/23.dat` |

### All skeleton clips in capture

| path | detail | viewer role |
|---|---|---|
| `rom/64/104.dat` | pb; ch=533; frames=1237; time=41.2; role=skeleton_clip | ElvaanM face1 head motion BASE; ElvaanM face1 head seq |
| `rom/64/105.dat` | pb; ch=533; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/64/110.dat` | pb; ch=565; frames=1237; time=41.2; role=skeleton_clip | — |
| `rom/64/111.dat` | pb; ch=565; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/64/116.dat` | pb; ch=329; frames=1237; time=41.2; role=skeleton_clip | ElvaanM face2 head motion BASE; ElvaanM face2 head seq |
| `rom/64/117.dat` | pb; ch=329; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/64/122.dat` | pb; ch=363; frames=1237; time=41.2; role=skeleton_clip | — |
| `rom/64/123.dat` | pb; ch=363; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/64/40.dat` | pb; ch=299; frames=2301; time=76.6667; role=skeleton_clip | ElvaanF body motion BASE (seq / offset 0 cluster); ElvaanF body creation SEQUENCE |
| `rom/64/41.dat` | pb; ch=299; frames=121; time=3.96667; role=skeleton_clip | — |
| `rom/64/46.dat` | pb; ch=382; frames=2301; time=76.6667; role=skeleton_clip | ElvaanF face1 head motion BASE; ElvaanF face1 head seq |
| `rom/64/47.dat` | pb; ch=382; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/64/52.dat` | pb; ch=302; frames=2301; time=76.6667; role=skeleton_clip | ElvaanF face3 head motion BASE; ElvaanF face3 head seq |
| `rom/64/53.dat` | pb; ch=302; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/64/58.dat` | pb; ch=443; frames=2301; time=76.6667; role=skeleton_clip | ElvaanF face2 head motion BASE; ElvaanF face2 head seq |
| `rom/64/59.dat` | pb; ch=443; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/64/64.dat` | pb; ch=402; frames=2301; time=76.6667; role=skeleton_clip | — |
| `rom/64/65.dat` | pb; ch=402; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/64/70.dat` | pb; ch=302; frames=2301; time=76.6667; role=skeleton_clip | — |
| `rom/64/71.dat` | pb; ch=302; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/64/76.dat` | pb; ch=441; frames=2301; time=76.6667; role=skeleton_clip | — |
| `rom/64/77.dat` | pb; ch=441; frames=241; time=7.96667; role=skeleton_clip | — |
| `rom/64/82.dat` | pb; ch=293; frames=2301; time=76.6667; role=skeleton_clip | ElvaanF face4 head motion BASE; ElvaanF face4 head seq |
| `rom/64/83.dat` | pb; ch=293; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/64/88.dat` | pb; ch=413; frames=2301; time=76.6667; role=skeleton_clip | — |
| `rom/64/89.dat` | pb; ch=413; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/64/98.dat` | pb; ch=299; frames=1237; time=41.2; role=skeleton_clip | ElvaanM body motion BASE (seq / offset 0 cluster); ElvaanM body creation SEQUENCE |
| `rom/64/99.dat` | pb; ch=299; frames=121; time=3.96667; role=skeleton_clip | — |
| `rom/65/0.dat` | pb; ch=353; frames=1237; time=41.2; role=skeleton_clip | ElvaanM face3 head motion BASE; ElvaanM face3 head seq |
| `rom/65/1.dat` | pb; ch=353; frames=241; time=7.96667; role=skeleton_clip | — |
| `rom/65/104.dat` | pb; ch=480; frames=1808; time=60.2333; role=skeleton_clip | HumeF face2 head motion BASE; HumeF face2 head seq |
| `rom/65/105.dat` | pb; ch=480; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/65/110.dat` | pb; ch=429; frames=1808; time=60.2333; role=skeleton_clip | — |
| `rom/65/111.dat` | pb; ch=429; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/65/116.dat` | pb; ch=256; frames=1808; time=60.2333; role=skeleton_clip | HumeF face3 head motion BASE; HumeF face3 head seq |
| `rom/65/117.dat` | pb; ch=256; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/65/12.dat` | pb; ch=426; frames=1237; time=41.2; role=skeleton_clip | ElvaanM face4 head motion BASE; ElvaanM face4 head seq |
| `rom/65/122.dat` | pb; ch=316; frames=1808; time=60.2333; role=skeleton_clip | — |
| `rom/65/123.dat` | pb; ch=316; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/65/13.dat` | pb; ch=426; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/65/18.dat` | pb; ch=305; frames=1237; time=41.2; role=skeleton_clip | — |
| `rom/65/19.dat` | pb; ch=305; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/65/28.dat` | pb; ch=389; frames=1726; time=57.5; role=skeleton_clip | Galka body motion BASE (seq / offset 0 cluster); Galka body creation SEQUENCE |
| `rom/65/29.dat` | pb; ch=389; frames=121; time=3.96667; role=skeleton_clip | — |
| `rom/65/34.dat` | pb; ch=253; frames=1726; time=57.5; role=skeleton_clip | Galka face1 head motion BASE; Galka face1 head seq |
| `rom/65/35.dat` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/65/40.dat` | pb; ch=253; frames=1726; time=57.5; role=skeleton_clip | Galka face2 head motion BASE; Galka face2 head seq |
| `rom/65/41.dat` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/65/46.dat` | pb; ch=253; frames=1726; time=57.5; role=skeleton_clip | Galka face3 head motion BASE; Galka face3 head seq |
| `rom/65/47.dat` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/65/52.dat` | pb; ch=253; frames=1726; time=57.5; role=skeleton_clip | Galka face4 head motion BASE; Galka face4 head seq |
| `rom/65/53.dat` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/65/58.dat` | pb; ch=253; frames=1726; time=57.5; role=skeleton_clip | — |
| `rom/65/59.dat` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/65/6.dat` | pb; ch=293; frames=1237; time=41.2; role=skeleton_clip | — |
| `rom/65/64.dat` | pb; ch=253; frames=1726; time=57.5; role=skeleton_clip | — |
| `rom/65/65.dat` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/65/7.dat` | pb; ch=293; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/65/70.dat` | pb; ch=253; frames=1726; time=57.5; role=skeleton_clip | — |
| `rom/65/71.dat` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/65/76.dat` | pb; ch=253; frames=1726; time=57.5; role=skeleton_clip | — |
| `rom/65/77.dat` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/65/86.dat` | pb; ch=299; frames=1808; time=60.2333; role=skeleton_clip | HumeF body motion BASE (seq / offset 0 cluster); HumeF body creation SEQUENCE |
| `rom/65/87.dat` | pb; ch=299; frames=121; time=3.96667; role=skeleton_clip | — |
| `rom/65/92.dat` | pb; ch=341; frames=1808; time=60.2333; role=skeleton_clip | HumeF face1 head motion BASE; HumeF face1 head seq |
| `rom/65/93.dat` | pb; ch=341; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/65/98.dat` | pb; ch=330; frames=1808; time=60.2333; role=skeleton_clip | — |
| `rom/65/99.dat` | pb; ch=330; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/66/0.dat` | pb; ch=256; frames=1808; time=60.2333; role=skeleton_clip | HumeF face4 head motion BASE; HumeF face4 head seq |
| `rom/66/1.dat` | pb; ch=256; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/66/104.dat` | pb; ch=396; frames=3001; time=100; role=skeleton_clip | Mithra face3 head motion BASE; Mithra face3 head seq |
| `rom/66/105.dat` | pb; ch=396; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/66/110.dat` | pb; ch=508; frames=3001; time=100; role=skeleton_clip | — |
| `rom/66/111.dat` | pb; ch=508; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/66/116.dat` | pb; ch=384; frames=3001; time=100; role=skeleton_clip | Mithra face4 head motion BASE; Mithra face4 head seq |
| `rom/66/117.dat` | pb; ch=384; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/66/122.dat` | pb; ch=384; frames=3001; time=100; role=skeleton_clip | — |
| `rom/66/123.dat` | pb; ch=384; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/66/16.dat` | pb; ch=299; frames=2387; time=79.5333; role=skeleton_clip | HumeM body motion BASE (seq / offset 0 cluster); HumeM body creation SEQUENCE |
| `rom/66/17.dat` | pb; ch=299; frames=121; time=3.96667; role=skeleton_clip | — |
| `rom/66/22.dat` | pb; ch=488; frames=2387; time=79.5333; role=skeleton_clip | HumeM face1 head motion BASE; HumeM face1 head seq |
| `rom/66/23.dat` | pb; ch=488; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/66/28.dat` | pb; ch=540; frames=2387; time=79.5333; role=skeleton_clip | — |
| `rom/66/29.dat` | pb; ch=540; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/66/34.dat` | pb; ch=256; frames=2387; time=79.5333; role=skeleton_clip | HumeM face2 head motion BASE; HumeM face2 head seq |
| `rom/66/35.dat` | pb; ch=256; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/66/40.dat` | pb; ch=353; frames=2387; time=79.5333; role=skeleton_clip | — |
| `rom/66/41.dat` | pb; ch=353; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/66/46.dat` | pb; ch=265; frames=2387; time=79.5333; role=skeleton_clip | HumeM face3 head motion BASE; HumeM face3 head seq |
| `rom/66/47.dat` | pb; ch=265; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/66/52.dat` | pb; ch=355; frames=2387; time=79.5333; role=skeleton_clip | — |
| `rom/66/53.dat` | pb; ch=355; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/66/58.dat` | pb; ch=277; frames=2387; time=79.5333; role=skeleton_clip | HumeM face4 head motion BASE; HumeM face4 head seq |
| `rom/66/59.dat` | pb; ch=277; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/66/6.dat` | pb; ch=417; frames=1808; time=60.2333; role=skeleton_clip | — |
| `rom/66/64.dat` | pb; ch=256; frames=2387; time=79.5333; role=skeleton_clip | — |
| `rom/66/65.dat` | pb; ch=256; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/66/7.dat` | pb; ch=417; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/66/74.dat` | pb; ch=407; frames=3001; time=100; role=skeleton_clip | Mithra body motion BASE (seq / offset 0 cluster); Mithra body creation SEQUENCE |
| `rom/66/75.dat` | pb; ch=407; frames=121; time=3.96667; role=skeleton_clip | — |
| `rom/66/80.dat` | pb; ch=392; frames=3001; time=100; role=skeleton_clip | Mithra face1 head motion BASE; Mithra face1 head seq |
| `rom/66/81.dat` | pb; ch=392; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/66/86.dat` | pb; ch=408; frames=3001; time=100; role=skeleton_clip | — |
| `rom/66/87.dat` | pb; ch=408; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/66/92.dat` | pb; ch=448; frames=3001; time=100; role=skeleton_clip | Mithra face2 head motion BASE; Mithra face2 head seq |
| `rom/66/93.dat` | pb; ch=448; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/66/98.dat` | pb; ch=356; frames=3001; time=100; role=skeleton_clip | — |
| `rom/66/99.dat` | pb; ch=356; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/67/10.dat` | pb; ch=469; frames=1315; time=43.8; role=skeleton_clip | TaruF face1 head motion BASE; TaruF face1 head seq |
| `rom/67/100.dat` | pb; ch=343; frames=1624; time=54.1; role=skeleton_clip | TaruM face4 head motion BASE; TaruM face4 head seq |
| `rom/67/101.dat` | pb; ch=343; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/67/106.dat` | pb; ch=389; frames=1624; time=54.1; role=skeleton_clip | — |
| `rom/67/107.dat` | pb; ch=389; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/67/11.dat` | pb; ch=469; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/67/16.dat` | pb; ch=477; frames=1315; time=43.8; role=skeleton_clip | — |
| `rom/67/17.dat` | pb; ch=477; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/67/22.dat` | pb; ch=357; frames=1315; time=43.8; role=skeleton_clip | TaruF face2 head motion BASE; TaruF face2 head seq |
| `rom/67/23.dat` | pb; ch=357; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/67/28.dat` | pb; ch=421; frames=1315; time=43.8; role=skeleton_clip | — |
| `rom/67/29.dat` | pb; ch=421; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/67/34.dat` | pb; ch=469; frames=1315; time=43.8; role=skeleton_clip | TaruF face3 head motion BASE; TaruF face3 head seq |
| `rom/67/35.dat` | pb; ch=469; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/67/4.dat` | pb; ch=251; frames=1315; time=43.8; role=skeleton_clip | TaruM body motion BASE (seq / offset 0 cluster); TaruF body motion BASE (seq / offset 0 cluster); TaruF body creation SEQUENCE |
| `rom/67/40.dat` | pb; ch=477; frames=1315; time=43.8; role=skeleton_clip | — |
| `rom/67/41.dat` | pb; ch=477; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/67/46.dat` | pb; ch=357; frames=1315; time=43.8; role=skeleton_clip | TaruF face4 head motion BASE; TaruF face4 head seq |
| `rom/67/47.dat` | pb; ch=357; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/67/5.dat` | pb; ch=251; frames=121; time=3.96667; role=skeleton_clip | — |
| `rom/67/52.dat` | pb; ch=421; frames=1315; time=43.8; role=skeleton_clip | — |
| `rom/67/53.dat` | pb; ch=421; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/67/58.dat` | pb; ch=251; frames=1624; time=54.1; role=skeleton_clip | TaruM body creation SEQUENCE |
| `rom/67/59.dat` | pb; ch=251; frames=121; time=3.96667; role=skeleton_clip | — |
| `rom/67/64.dat` | pb; ch=413; frames=1624; time=54.1; role=skeleton_clip | TaruM face1 head motion BASE; TaruM face1 head seq |
| `rom/67/65.dat` | pb; ch=413; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/67/70.dat` | pb; ch=537; frames=1624; time=54.1; role=skeleton_clip | — |
| `rom/67/71.dat` | pb; ch=537; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/67/76.dat` | pb; ch=343; frames=1624; time=54.1; role=skeleton_clip | TaruM face2 head motion BASE; TaruM face2 head seq |
| `rom/67/77.dat` | pb; ch=343; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/67/82.dat` | pb; ch=389; frames=1624; time=54.1; role=skeleton_clip | — |
| `rom/67/83.dat` | pb; ch=389; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/67/88.dat` | pb; ch=413; frames=1624; time=54.1; role=skeleton_clip | TaruM face3 head motion BASE; TaruM face3 head seq |
| `rom/67/89.dat` | pb; ch=413; frames=241; time=7.93334; role=skeleton_clip | — |
| `rom/67/94.dat` | pb; ch=537; frames=1624; time=54.1; role=skeleton_clip | — |
| `rom/67/95.dat` | pb; ch=537; frames=241; time=7.93334; role=skeleton_clip | — |

## Camera files

Two cameras per race. Each is a pair:

- **FOV** — SQLE motion, 1 channel, constant degrees
- **Matrix** — SQLE motion, 16 channels = row-major 4×4 world matrix per frame

Usually bodySeq−8..−5; Tarutaru male uses −4..−1 from its seq body
(`67/54`–`67/57`), so paths are listed explicitly in the viewer.

Capture camera tracks: **32**

| path | detail | viewer role |
|---|---|---|
| `rom/64/32.dat` | pb; ch=1; frames=2301; time=76.6667; role=camera_fov | ElvaanF camera1 FOV |
| `rom/64/33.dat` | pb; ch=16; frames=2301; time=76.6667; role=camera_matrix | ElvaanF camera1 matrix |
| `rom/64/34.dat` | pb; ch=1; frames=2301; time=76.6667; role=camera_fov | ElvaanF camera2 FOV |
| `rom/64/35.dat` | pb; ch=16; frames=2301; time=76.6667; role=camera_matrix | ElvaanF camera2 matrix |
| `rom/64/90.dat` | pb; ch=1; frames=1237; time=41.2; role=camera_fov | ElvaanM camera1 FOV |
| `rom/64/91.dat` | pb; ch=16; frames=1237; time=41.2; role=camera_matrix | ElvaanM camera1 matrix |
| `rom/64/92.dat` | pb; ch=1; frames=1237; time=41.2; role=camera_fov | ElvaanM camera2 FOV |
| `rom/64/93.dat` | pb; ch=16; frames=1237; time=41.2; role=camera_matrix | ElvaanM camera2 matrix |
| `rom/65/20.dat` | pb; ch=1; frames=1726; time=57.5; role=camera_fov | Galka camera1 FOV |
| `rom/65/21.dat` | pb; ch=16; frames=1726; time=57.5; role=camera_matrix | Galka camera1 matrix |
| `rom/65/22.dat` | pb; ch=1; frames=1726; time=57.5; role=camera_fov | Galka camera2 FOV |
| `rom/65/23.dat` | pb; ch=16; frames=1726; time=57.5; role=camera_matrix | Galka camera2 matrix |
| `rom/65/78.dat` | pb; ch=1; frames=1808; time=60.2333; role=camera_fov | HumeF camera1 FOV |
| `rom/65/79.dat` | pb; ch=16; frames=1808; time=60.2333; role=camera_matrix | HumeF camera1 matrix |
| `rom/65/80.dat` | pb; ch=1; frames=1808; time=60.2333; role=camera_fov | HumeF camera2 FOV |
| `rom/65/81.dat` | pb; ch=16; frames=1808; time=60.2333; role=camera_matrix | HumeF camera2 matrix |
| `rom/66/10.dat` | pb; ch=1; frames=2387; time=79.5333; role=camera_fov | HumeM camera2 FOV |
| `rom/66/11.dat` | pb; ch=16; frames=2387; time=79.5333; role=camera_matrix | HumeM camera2 matrix |
| `rom/66/124.dat` | pb; ch=1; frames=1315; time=43.8; role=camera_fov | TaruF camera1 FOV |
| `rom/66/125.dat` | pb; ch=16; frames=1315; time=43.8; role=camera_matrix | TaruF camera1 matrix |
| `rom/66/126.dat` | pb; ch=1; frames=1315; time=43.8; role=camera_fov | TaruF camera2 FOV |
| `rom/66/127.dat` | pb; ch=16; frames=1315; time=43.8; role=camera_matrix | TaruF camera2 matrix |
| `rom/66/66.dat` | pb; ch=1; frames=3001; time=100; role=camera_fov | Mithra camera1 FOV |
| `rom/66/67.dat` | pb; ch=16; frames=3001; time=100; role=camera_matrix | Mithra camera1 matrix |
| `rom/66/68.dat` | pb; ch=1; frames=3001; time=100; role=camera_fov | Mithra camera2 FOV |
| `rom/66/69.dat` | pb; ch=16; frames=3001; time=100; role=camera_matrix | Mithra camera2 matrix |
| `rom/66/8.dat` | pb; ch=1; frames=2387; time=79.5333; role=camera_fov | HumeM camera1 FOV |
| `rom/66/9.dat` | pb; ch=16; frames=2387; time=79.5333; role=camera_matrix | HumeM camera1 matrix |
| `rom/67/54.dat` | pb; ch=1; frames=1624; time=54.1; role=camera_fov | TaruM camera1 FOV |
| `rom/67/55.dat` | pb; ch=16; frames=1624; time=54.1; role=camera_matrix | TaruM camera1 matrix |
| `rom/67/56.dat` | pb; ch=1; frames=1624; time=54.1; role=camera_fov | TaruM camera2 FOV |
| `rom/67/57.dat` | pb; ch=16; frames=1624; time=54.1; role=camera_matrix | TaruM camera2 matrix |

## Cue / action tracks

The long sequence is incomplete without the OC:01.00 cue track and the
per-race action table it indexes. Viewer currently parses cues only
(informational); action bytecode not decoded.

| path | size | note |
|---|---:|---|
| `rom/67/108.dat` | 1632 | OC cue |
| `rom/67/109.dat` | 1404 | OC cue |
| `rom/67/110.dat` | 576 | OC cue |
| `rom/67/111.dat` | 972 | OC cue |
| `rom/67/112.dat` | 1560 | OC cue |
| `rom/67/113.dat` | 3264 | OC cue |
| `rom/67/114.dat` | 1332 | OC cue |
| `rom/67/115.dat` | 696 | OC cue |

Cue files in capture are all under `rom/67/108`–`115` (shared cluster).

## Materials (DMB)

Count: **72** — body mats + face A/B mats.
Face A and B share mesh + motions; only the DMB differs.

## Coverage: viewer table vs procmon capture

| set | count |
|---|---:|
| paths in CREATION_RACES (+derived clips/equip) | 358 |
| paths in procmon capture | 439 |
| intersection | 184 |
| in table but never opened in this capture | 174 |
| opened in ROM/63–67 but not in table | 152 |

<details><summary>Table-only paths (expected if you only did face 1/2)</summary>

- `rom/63/0.dat` — ElvaanF body material (naked)
- `rom/63/1.dat` — ElvaanF body mesh (naked)
- `rom/63/100.dat` — Mithra body material (naked)
- `rom/63/101.dat` — Mithra body mesh (naked)
- `rom/63/120.dat` — TaruF body material (naked)
- `rom/63/121.dat` — TaruF body mesh (naked)
- `rom/63/20.dat` — ElvaanM body material (naked)
- `rom/63/21.dat` — ElvaanM body mesh (naked)
- `rom/63/40.dat` — Galka body material (naked)
- `rom/63/41.dat` — Galka body mesh (naked)
- `rom/63/60.dat` — HumeF body material (naked)
- `rom/63/61.dat` — HumeF body mesh (naked)
- `rom/63/80.dat` — HumeM body material (naked)
- `rom/63/81.dat` — HumeM body mesh (naked)
- `rom/64/100.dat` — ElvaanM face1 head motion1
- `rom/64/101.dat` — ElvaanM face1 head motion2
- `rom/64/102.dat` — ElvaanM face1 head idle; ElvaanM face1 head idle
- `rom/64/103.dat` — ElvaanM face1 head motion3
- `rom/64/112.dat` — ElvaanM face2 head motion1
- `rom/64/113.dat` — ElvaanM face2 head motion2
- `rom/64/114.dat` — ElvaanM face2 head idle; ElvaanM face2 head idle
- `rom/64/115.dat` — ElvaanM face2 head motion3
- `rom/64/12.dat` — TaruM body material (naked)
- `rom/64/126.dat` — ElvaanM face3 head idle
- `rom/64/13.dat` — TaruM body mesh (naked)
- `rom/64/36.dat` — ElvaanF body motion1; ElvaanF body motion1; ElvaanF body motion1; ElvaanF body motion1
- `rom/64/37.dat` — ElvaanF body motion2; ElvaanF body motion2; ElvaanF body motion2; ElvaanF body motion2
- `rom/64/38.dat` — ElvaanF body idle; ElvaanF body idle; ElvaanF body idle; ElvaanF body idle; ElvaanF body idle
- `rom/64/39.dat` — ElvaanF body motion3; ElvaanF body motion3; ElvaanF body motion3; ElvaanF body motion3
- `rom/64/42.dat` — ElvaanF face1 head motion1
- `rom/64/43.dat` — ElvaanF face1 head motion2
- `rom/64/44.dat` — ElvaanF face1 head idle; ElvaanF face1 head idle
- `rom/64/45.dat` — ElvaanF face1 head motion3
- `rom/64/48.dat` — ElvaanF face3 head motion1
- `rom/64/49.dat` — ElvaanF face3 head motion2
- `rom/64/50.dat` — ElvaanF face3 head idle; ElvaanF face3 head idle
- `rom/64/51.dat` — ElvaanF face3 head motion3
- `rom/64/54.dat` — ElvaanF face2 head motion1
- `rom/64/55.dat` — ElvaanF face2 head motion2
- `rom/64/56.dat` — ElvaanF face2 head idle; ElvaanF face2 head idle
- `rom/64/57.dat` — ElvaanF face2 head motion3
- `rom/64/78.dat` — ElvaanF face4 head motion1
- `rom/64/79.dat` — ElvaanF face4 head motion2
- `rom/64/80.dat` — ElvaanF face4 head idle; ElvaanF face4 head idle
- `rom/64/81.dat` — ElvaanF face4 head motion3
- `rom/64/94.dat` — ElvaanM body motion1; ElvaanM body motion1; ElvaanM body motion1; ElvaanM body motion1
- `rom/64/95.dat` — ElvaanM body motion2; ElvaanM body motion2; ElvaanM body motion2; ElvaanM body motion2
- `rom/64/96.dat` — ElvaanM body idle; ElvaanM body idle; ElvaanM body idle; ElvaanM body idle; ElvaanM body idle
- `rom/64/97.dat` — ElvaanM body motion3; ElvaanM body motion3; ElvaanM body motion3; ElvaanM body motion3
- `rom/65/-1.dat` — ElvaanM face3 head motion3
- `rom/65/-2.dat` — ElvaanM face3 head idle
- `rom/65/-3.dat` — ElvaanM face3 head motion2
- `rom/65/-4.dat` — ElvaanM face3 head motion1
- `rom/65/10.dat` — ElvaanM face4 head idle; ElvaanM face4 head idle
- `rom/65/100.dat` — HumeF face2 head motion1
- `rom/65/101.dat` — HumeF face2 head motion2
- `rom/65/102.dat` — HumeF face2 head idle; HumeF face2 head idle
- `rom/65/103.dat` — HumeF face2 head motion3
- `rom/65/11.dat` — ElvaanM face4 head motion3
- `rom/65/112.dat` — HumeF face3 head motion1
- `rom/65/113.dat` — HumeF face3 head motion2
- `rom/65/114.dat` — HumeF face3 head idle; HumeF face3 head idle
- `rom/65/115.dat` — HumeF face3 head motion3
- `rom/65/126.dat` — HumeF face4 head idle
- `rom/65/24.dat` — Galka body motion1; Galka body motion1; Galka body motion1; Galka body motion1
- `rom/65/25.dat` — Galka body motion2; Galka body motion2; Galka body motion2; Galka body motion2
- `rom/65/26.dat` — Galka body idle; Galka body idle; Galka body idle; Galka body idle; Galka body idle
- `rom/65/27.dat` — Galka body motion3; Galka body motion3; Galka body motion3; Galka body motion3
- `rom/65/30.dat` — Galka face1 head motion1
- `rom/65/31.dat` — Galka face1 head motion2
- `rom/65/32.dat` — Galka face1 head idle; Galka face1 head idle
- `rom/65/33.dat` — Galka face1 head motion3
- `rom/65/36.dat` — Galka face2 head motion1
- `rom/65/37.dat` — Galka face2 head motion2
- `rom/65/38.dat` — Galka face2 head idle; Galka face2 head idle
- `rom/65/39.dat` — Galka face2 head motion3
- `rom/65/42.dat` — Galka face3 head motion1
- `rom/65/43.dat` — Galka face3 head motion2
- `rom/65/44.dat` — Galka face3 head idle; Galka face3 head idle
- `rom/65/45.dat` — Galka face3 head motion3
- `rom/65/48.dat` — Galka face4 head motion1
- `rom/65/49.dat` — Galka face4 head motion2
- `rom/65/50.dat` — Galka face4 head idle; Galka face4 head idle
- `rom/65/51.dat` — Galka face4 head motion3
- `rom/65/8.dat` — ElvaanM face4 head motion1
- `rom/65/82.dat` — HumeF body motion1; HumeF body motion1; HumeF body motion1; HumeF body motion1
- `rom/65/83.dat` — HumeF body motion2; HumeF body motion2; HumeF body motion2; HumeF body motion2
- `rom/65/84.dat` — HumeF body idle; HumeF body idle; HumeF body idle; HumeF body idle; HumeF body idle
- `rom/65/85.dat` — HumeF body motion3; HumeF body motion3; HumeF body motion3; HumeF body motion3
- `rom/65/88.dat` — HumeF face1 head motion1
- `rom/65/89.dat` — HumeF face1 head motion2
- `rom/65/9.dat` — ElvaanM face4 head motion2
- `rom/65/90.dat` — HumeF face1 head idle; HumeF face1 head idle
- `rom/65/91.dat` — HumeF face1 head motion3
- `rom/66/-1.dat` — HumeF face4 head motion3
- `rom/66/-2.dat` — HumeF face4 head idle
- `rom/66/-3.dat` — HumeF face4 head motion2
- `rom/66/-4.dat` — HumeF face4 head motion1
- `rom/66/100.dat` — Mithra face3 head motion1
- `rom/66/101.dat` — Mithra face3 head motion2
- `rom/66/102.dat` — Mithra face3 head idle; Mithra face3 head idle
- `rom/66/103.dat` — Mithra face3 head motion3
- `rom/66/112.dat` — Mithra face4 head motion1
- `rom/66/113.dat` — Mithra face4 head motion2
- `rom/66/114.dat` — Mithra face4 head idle; Mithra face4 head idle
- `rom/66/115.dat` — Mithra face4 head motion3
- `rom/66/12.dat` — HumeM body motion1; HumeM body motion1; HumeM body motion1; HumeM body motion1
- `rom/66/13.dat` — HumeM body motion2; HumeM body motion2; HumeM body motion2; HumeM body motion2
- `rom/66/14.dat` — HumeM body idle; HumeM body idle; HumeM body idle; HumeM body idle; HumeM body idle
- `rom/66/15.dat` — HumeM body motion3; HumeM body motion3; HumeM body motion3; HumeM body motion3
- `rom/66/18.dat` — HumeM face1 head motion1
- `rom/66/19.dat` — HumeM face1 head motion2
- `rom/66/20.dat` — HumeM face1 head idle; HumeM face1 head idle
- `rom/66/21.dat` — HumeM face1 head motion3
- `rom/66/30.dat` — HumeM face2 head motion1
- `rom/66/31.dat` — HumeM face2 head motion2
- `rom/66/32.dat` — HumeM face2 head idle; HumeM face2 head idle
- `rom/66/33.dat` — HumeM face2 head motion3
- `rom/66/42.dat` — HumeM face3 head motion1
- `rom/66/43.dat` — HumeM face3 head motion2
- `rom/66/44.dat` — HumeM face3 head idle; HumeM face3 head idle
- `rom/66/45.dat` — HumeM face3 head motion3
- `rom/66/54.dat` — HumeM face4 head motion1
- `rom/66/55.dat` — HumeM face4 head motion2
- `rom/66/56.dat` — HumeM face4 head idle; HumeM face4 head idle
- `rom/66/57.dat` — HumeM face4 head motion3
- `rom/66/70.dat` — Mithra body motion1; Mithra body motion1; Mithra body motion1; Mithra body motion1
- `rom/66/71.dat` — Mithra body motion2; Mithra body motion2; Mithra body motion2; Mithra body motion2
- `rom/66/72.dat` — Mithra body idle; Mithra body idle; Mithra body idle; Mithra body idle; Mithra body idle
- `rom/66/73.dat` — Mithra body motion3; Mithra body motion3; Mithra body motion3; Mithra body motion3
- `rom/66/76.dat` — Mithra face1 head motion1
- `rom/66/77.dat` — Mithra face1 head motion2
- `rom/66/78.dat` — Mithra face1 head idle; Mithra face1 head idle
- `rom/66/79.dat` — Mithra face1 head motion3
- `rom/66/88.dat` — Mithra face2 head motion1
- `rom/66/89.dat` — Mithra face2 head motion2
- `rom/66/90.dat` — Mithra face2 head idle; Mithra face2 head idle
- `rom/66/91.dat` — Mithra face2 head motion3
- `rom/67/0.dat` — TaruM body motion1; TaruM body motion1; TaruM body motion1; TaruM body motion1; TaruF body motion1; TaruF body motion1; TaruF body motion1; TaruF body motion1
- `rom/67/1.dat` — TaruM body motion2; TaruM body motion2; TaruM body motion2; TaruM body motion2; TaruF body motion2; TaruF body motion2; TaruF body motion2; TaruF body motion2
- `rom/67/18.dat` — TaruF face2 head motion1
- `rom/67/19.dat` — TaruF face2 head motion2
- `rom/67/2.dat` — TaruM body idle; TaruM body idle; TaruM body idle; TaruM body idle; TaruM body idle; TaruF body idle; TaruF body idle; TaruF body idle; TaruF body idle; TaruF body idle
- `rom/67/20.dat` — TaruF face2 head idle; TaruF face2 head idle
- `rom/67/21.dat` — TaruF face2 head motion3
- `rom/67/3.dat` — TaruM body motion3; TaruM body motion3; TaruM body motion3; TaruM body motion3; TaruF body motion3; TaruF body motion3; TaruF body motion3; TaruF body motion3
- `rom/67/30.dat` — TaruF face3 head motion1
- `rom/67/31.dat` — TaruF face3 head motion2
- `rom/67/32.dat` — TaruF face3 head idle; TaruF face3 head idle
- `rom/67/33.dat` — TaruF face3 head motion3
- `rom/67/42.dat` — TaruF face4 head motion1
- `rom/67/43.dat` — TaruF face4 head motion2
- `rom/67/44.dat` — TaruF face4 head idle; TaruF face4 head idle
- `rom/67/45.dat` — TaruF face4 head motion3
- `rom/67/6.dat` — TaruF face1 head motion1
- `rom/67/60.dat` — TaruM face1 head motion1
- `rom/67/61.dat` — TaruM face1 head motion2
- `rom/67/62.dat` — TaruM face1 head idle; TaruM face1 head idle
- `rom/67/63.dat` — TaruM face1 head motion3
- `rom/67/7.dat` — TaruF face1 head motion2
- `rom/67/72.dat` — TaruM face2 head motion1
- `rom/67/73.dat` — TaruM face2 head motion2
- `rom/67/74.dat` — TaruM face2 head idle; TaruM face2 head idle
- `rom/67/75.dat` — TaruM face2 head motion3
- `rom/67/8.dat` — TaruF face1 head idle; TaruF face1 head idle
- `rom/67/84.dat` — TaruM face3 head motion1
- `rom/67/85.dat` — TaruM face3 head motion2
- `rom/67/86.dat` — TaruM face3 head idle; TaruM face3 head idle
- `rom/67/87.dat` — TaruM face3 head motion3
- `rom/67/9.dat` — TaruF face1 head motion3
- `rom/67/96.dat` — TaruM face4 head motion1
- `rom/67/97.dat` — TaruM face4 head motion2
- `rom/67/98.dat` — TaruM face4 head idle; TaruM face4 head idle
- `rom/67/99.dat` — TaruM face4 head motion3

</details>

### ROM/63–67 opened but not mapped in viewer table

These are the highest-value gaps for fixing the viewer.

| path | kind | detail |
|---|---|---|
| `rom/63/107.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/11.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/111.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/115.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/119.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/127.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/15.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/19.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/27.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/31.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/35.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/39.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/47.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/51.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/55.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/59.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/67.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/7.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/71.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/75.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/79.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/87.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/91.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/95.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/63/99.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/64/105.dat` | `sqle_motion` | pb; ch=533; frames=241; time=7.93334; role=skeleton_clip |
| `rom/64/11.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/64/110.dat` | `sqle_motion` | pb; ch=565; frames=1237; time=41.2; role=skeleton_clip |
| `rom/64/111.dat` | `sqle_motion` | pb; ch=565; frames=241; time=7.93334; role=skeleton_clip |
| `rom/64/117.dat` | `sqle_motion` | pb; ch=329; frames=241; time=7.93334; role=skeleton_clip |
| `rom/64/122.dat` | `sqle_motion` | pb; ch=363; frames=1237; time=41.2; role=skeleton_clip |
| `rom/64/123.dat` | `sqle_motion` | pb; ch=363; frames=241; time=7.93334; role=skeleton_clip |
| `rom/64/19.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/64/23.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/64/27.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/64/3.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/64/31.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/64/41.dat` | `sqle_motion` | pb; ch=299; frames=121; time=3.96667; role=skeleton_clip |
| `rom/64/47.dat` | `sqle_motion` | pb; ch=382; frames=241; time=7.93334; role=skeleton_clip |
| `rom/64/53.dat` | `sqle_motion` | pb; ch=302; frames=241; time=7.93334; role=skeleton_clip |
| `rom/64/59.dat` | `sqle_motion` | pb; ch=443; frames=241; time=7.93334; role=skeleton_clip |
| `rom/64/64.dat` | `sqle_motion` | pb; ch=402; frames=2301; time=76.6667; role=skeleton_clip |
| `rom/64/65.dat` | `sqle_motion` | pb; ch=402; frames=241; time=7.93334; role=skeleton_clip |
| `rom/64/7.dat` | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin |
| `rom/64/70.dat` | `sqle_motion` | pb; ch=302; frames=2301; time=76.6667; role=skeleton_clip |
| `rom/64/71.dat` | `sqle_motion` | pb; ch=302; frames=241; time=7.93334; role=skeleton_clip |
| `rom/64/76.dat` | `sqle_motion` | pb; ch=441; frames=2301; time=76.6667; role=skeleton_clip |
| `rom/64/77.dat` | `sqle_motion` | pb; ch=441; frames=241; time=7.96667; role=skeleton_clip |
| `rom/64/83.dat` | `sqle_motion` | pb; ch=293; frames=241; time=7.93334; role=skeleton_clip |
| `rom/64/88.dat` | `sqle_motion` | pb; ch=413; frames=2301; time=76.6667; role=skeleton_clip |
| `rom/64/89.dat` | `sqle_motion` | pb; ch=413; frames=241; time=7.93334; role=skeleton_clip |
| `rom/64/99.dat` | `sqle_motion` | pb; ch=299; frames=121; time=3.96667; role=skeleton_clip |
| `rom/65/1.dat` | `sqle_motion` | pb; ch=353; frames=241; time=7.96667; role=skeleton_clip |
| `rom/65/105.dat` | `sqle_motion` | pb; ch=480; frames=241; time=7.93334; role=skeleton_clip |
| `rom/65/110.dat` | `sqle_motion` | pb; ch=429; frames=1808; time=60.2333; role=skeleton_clip |
| `rom/65/111.dat` | `sqle_motion` | pb; ch=429; frames=241; time=7.93334; role=skeleton_clip |
| `rom/65/117.dat` | `sqle_motion` | pb; ch=256; frames=241; time=7.93334; role=skeleton_clip |
| `rom/65/122.dat` | `sqle_motion` | pb; ch=316; frames=1808; time=60.2333; role=skeleton_clip |
| `rom/65/123.dat` | `sqle_motion` | pb; ch=316; frames=241; time=7.93334; role=skeleton_clip |
| `rom/65/13.dat` | `sqle_motion` | pb; ch=426; frames=241; time=7.93334; role=skeleton_clip |
| `rom/65/18.dat` | `sqle_motion` | pb; ch=305; frames=1237; time=41.2; role=skeleton_clip |
| `rom/65/19.dat` | `sqle_motion` | pb; ch=305; frames=241; time=7.93334; role=skeleton_clip |
| `rom/65/29.dat` | `sqle_motion` | pb; ch=389; frames=121; time=3.96667; role=skeleton_clip |
| `rom/65/35.dat` | `sqle_motion` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip |
| `rom/65/41.dat` | `sqle_motion` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip |
| `rom/65/47.dat` | `sqle_motion` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip |
| `rom/65/53.dat` | `sqle_motion` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip |
| `rom/65/58.dat` | `sqle_motion` | pb; ch=253; frames=1726; time=57.5; role=skeleton_clip |
| `rom/65/59.dat` | `sqle_motion` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip |
| `rom/65/6.dat` | `sqle_motion` | pb; ch=293; frames=1237; time=41.2; role=skeleton_clip |
| `rom/65/64.dat` | `sqle_motion` | pb; ch=253; frames=1726; time=57.5; role=skeleton_clip |
| `rom/65/65.dat` | `sqle_motion` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip |
| `rom/65/7.dat` | `sqle_motion` | pb; ch=293; frames=241; time=7.93334; role=skeleton_clip |
| `rom/65/70.dat` | `sqle_motion` | pb; ch=253; frames=1726; time=57.5; role=skeleton_clip |
| `rom/65/71.dat` | `sqle_motion` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip |
| `rom/65/76.dat` | `sqle_motion` | pb; ch=253; frames=1726; time=57.5; role=skeleton_clip |
| `rom/65/77.dat` | `sqle_motion` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip |
| `rom/65/87.dat` | `sqle_motion` | pb; ch=299; frames=121; time=3.96667; role=skeleton_clip |
| `rom/65/93.dat` | `sqle_motion` | pb; ch=341; frames=241; time=7.93334; role=skeleton_clip |
| `rom/65/98.dat` | `sqle_motion` | pb; ch=330; frames=1808; time=60.2333; role=skeleton_clip |
| `rom/65/99.dat` | `sqle_motion` | pb; ch=330; frames=241; time=7.93334; role=skeleton_clip |
| `rom/66/1.dat` | `sqle_motion` | pb; ch=256; frames=241; time=7.93334; role=skeleton_clip |
| `rom/66/105.dat` | `sqle_motion` | pb; ch=396; frames=241; time=7.93334; role=skeleton_clip |
| `rom/66/110.dat` | `sqle_motion` | pb; ch=508; frames=3001; time=100; role=skeleton_clip |
| `rom/66/111.dat` | `sqle_motion` | pb; ch=508; frames=241; time=7.93334; role=skeleton_clip |
| `rom/66/117.dat` | `sqle_motion` | pb; ch=384; frames=241; time=7.93334; role=skeleton_clip |
| `rom/66/122.dat` | `sqle_motion` | pb; ch=384; frames=3001; time=100; role=skeleton_clip |
| `rom/66/123.dat` | `sqle_motion` | pb; ch=384; frames=241; time=7.93334; role=skeleton_clip |
| `rom/66/17.dat` | `sqle_motion` | pb; ch=299; frames=121; time=3.96667; role=skeleton_clip |
| `rom/66/23.dat` | `sqle_motion` | pb; ch=488; frames=241; time=7.93334; role=skeleton_clip |
| `rom/66/28.dat` | `sqle_motion` | pb; ch=540; frames=2387; time=79.5333; role=skeleton_clip |
| `rom/66/29.dat` | `sqle_motion` | pb; ch=540; frames=241; time=7.93334; role=skeleton_clip |
| `rom/66/35.dat` | `sqle_motion` | pb; ch=256; frames=241; time=7.93334; role=skeleton_clip |
| `rom/66/40.dat` | `sqle_motion` | pb; ch=353; frames=2387; time=79.5333; role=skeleton_clip |
| `rom/66/41.dat` | `sqle_motion` | pb; ch=353; frames=241; time=7.93334; role=skeleton_clip |
| `rom/66/47.dat` | `sqle_motion` | pb; ch=265; frames=241; time=7.93334; role=skeleton_clip |
| `rom/66/52.dat` | `sqle_motion` | pb; ch=355; frames=2387; time=79.5333; role=skeleton_clip |
| `rom/66/53.dat` | `sqle_motion` | pb; ch=355; frames=241; time=7.93334; role=skeleton_clip |
| `rom/66/59.dat` | `sqle_motion` | pb; ch=277; frames=241; time=7.93334; role=skeleton_clip |
| `rom/66/6.dat` | `sqle_motion` | pb; ch=417; frames=1808; time=60.2333; role=skeleton_clip |
| `rom/66/64.dat` | `sqle_motion` | pb; ch=256; frames=2387; time=79.5333; role=skeleton_clip |
| `rom/66/65.dat` | `sqle_motion` | pb; ch=256; frames=241; time=7.93334; role=skeleton_clip |
| `rom/66/7.dat` | `sqle_motion` | pb; ch=417; frames=241; time=7.93334; role=skeleton_clip |
| `rom/66/75.dat` | `sqle_motion` | pb; ch=407; frames=121; time=3.96667; role=skeleton_clip |
| `rom/66/81.dat` | `sqle_motion` | pb; ch=392; frames=241; time=7.93334; role=skeleton_clip |
| `rom/66/86.dat` | `sqle_motion` | pb; ch=408; frames=3001; time=100; role=skeleton_clip |
| `rom/66/87.dat` | `sqle_motion` | pb; ch=408; frames=241; time=7.93334; role=skeleton_clip |
| `rom/66/93.dat` | `sqle_motion` | pb; ch=448; frames=241; time=7.93334; role=skeleton_clip |
| `rom/66/98.dat` | `sqle_motion` | pb; ch=356; frames=3001; time=100; role=skeleton_clip |
| `rom/66/99.dat` | `sqle_motion` | pb; ch=356; frames=241; time=7.93334; role=skeleton_clip |
| `rom/67/101.dat` | `sqle_motion` | pb; ch=343; frames=241; time=7.93334; role=skeleton_clip |
| `rom/67/106.dat` | `sqle_motion` | pb; ch=389; frames=1624; time=54.1; role=skeleton_clip |
| `rom/67/107.dat` | `sqle_motion` | pb; ch=389; frames=241; time=7.93334; role=skeleton_clip |
| `rom/67/108.dat` | `creation_cue` | OC:01.00 frame→action cue track |
| `rom/67/109.dat` | `creation_cue` | OC:01.00 frame→action cue track |
| `rom/67/11.dat` | `sqle_motion` | pb; ch=469; frames=241; time=7.93334; role=skeleton_clip |
| `rom/67/110.dat` | `creation_cue` | OC:01.00 frame→action cue track |
| `rom/67/111.dat` | `creation_cue` | OC:01.00 frame→action cue track |
| `rom/67/112.dat` | `creation_cue` | OC:01.00 frame→action cue track |
| `rom/67/113.dat` | `creation_cue` | OC:01.00 frame→action cue track |
| `rom/67/114.dat` | `creation_cue` | OC:01.00 frame→action cue track |
| `rom/67/115.dat` | `creation_cue` | OC:01.00 frame→action cue track |
| `rom/67/116.dat` | `race_action_table` | rthu |
| `rom/67/117.dat` | `race_action_table` | rthu |
| `rom/67/118.dat` | `race_action_table` | rtel |
| `rom/67/119.dat` | `race_action_table` | rtel |
| `rom/67/120.dat` | `race_action_table` | rtta |
| `rom/67/121.dat` | `race_action_table` | rtta |
| `rom/67/122.dat` | `race_action_table` | rtmi |
| `rom/67/123.dat` | `race_action_table` | rtga |
| `rom/67/16.dat` | `sqle_motion` | pb; ch=477; frames=1315; time=43.8; role=skeleton_clip |
| `rom/67/17.dat` | `sqle_motion` | pb; ch=477; frames=241; time=7.93334; role=skeleton_clip |
| `rom/67/23.dat` | `sqle_motion` | pb; ch=357; frames=241; time=7.93334; role=skeleton_clip |
| `rom/67/28.dat` | `sqle_motion` | pb; ch=421; frames=1315; time=43.8; role=skeleton_clip |
| `rom/67/29.dat` | `sqle_motion` | pb; ch=421; frames=241; time=7.93334; role=skeleton_clip |
| `rom/67/35.dat` | `sqle_motion` | pb; ch=469; frames=241; time=7.93334; role=skeleton_clip |
| `rom/67/40.dat` | `sqle_motion` | pb; ch=477; frames=1315; time=43.8; role=skeleton_clip |
| `rom/67/41.dat` | `sqle_motion` | pb; ch=477; frames=241; time=7.93334; role=skeleton_clip |
| `rom/67/47.dat` | `sqle_motion` | pb; ch=357; frames=241; time=7.93334; role=skeleton_clip |
| `rom/67/5.dat` | `sqle_motion` | pb; ch=251; frames=121; time=3.96667; role=skeleton_clip |
| `rom/67/52.dat` | `sqle_motion` | pb; ch=421; frames=1315; time=43.8; role=skeleton_clip |
| `rom/67/53.dat` | `sqle_motion` | pb; ch=421; frames=241; time=7.93334; role=skeleton_clip |
| `rom/67/59.dat` | `sqle_motion` | pb; ch=251; frames=121; time=3.96667; role=skeleton_clip |
| `rom/67/65.dat` | `sqle_motion` | pb; ch=413; frames=241; time=7.93334; role=skeleton_clip |
| `rom/67/70.dat` | `sqle_motion` | pb; ch=537; frames=1624; time=54.1; role=skeleton_clip |
| `rom/67/71.dat` | `sqle_motion` | pb; ch=537; frames=241; time=7.93334; role=skeleton_clip |
| `rom/67/77.dat` | `sqle_motion` | pb; ch=343; frames=241; time=7.93334; role=skeleton_clip |
| `rom/67/82.dat` | `sqle_motion` | pb; ch=389; frames=1624; time=54.1; role=skeleton_clip |
| `rom/67/83.dat` | `sqle_motion` | pb; ch=389; frames=241; time=7.93334; role=skeleton_clip |
| `rom/67/89.dat` | `sqle_motion` | pb; ch=413; frames=241; time=7.93334; role=skeleton_clip |
| `rom/67/94.dat` | `sqle_motion` | pb; ch=537; frames=1624; time=54.1; role=skeleton_clip |
| `rom/67/95.dat` | `sqle_motion` | pb; ch=537; frames=241; time=7.93334; role=skeleton_clip |

## Non-character / support DATs in the capture

| path | kind | detail |
|---|---|---|
| `ftable.dat` | `file_table` |  |
| `rom/0/0.dat` | `system_dat` | syst............ |
| `rom/0/123.dat` | `font_or_effect` | f_gu............ |
| `rom/0/125.dat` | `font_or_effect` | f_or............ |
| `rom/0/14.dat` | `ui_window` | win0............ |
| `rom/0/23.dat` | `ui_title` | titl............ |
| `rom/0/27.dat` | `ui_damage` | damv............ |
| `rom/1/5.dat` | `font_or_effect` | f_ch............ |
| `rom/118/103.dat` | `xistring` | XISTRING........ |
| `rom/118/114.dat` | `ui_menu` | menu............ |
| `rom/118/115.dat` | `ui_menu` | menu............ |
| `rom/119/50.dat` | `ui_lobby` | lobb............ |
| `rom/119/51.dat` | `ui_menu` | menu............ |
| `rom/165/69.dat` | `dialog_msg` | menu/dialog text |
| `rom/165/70.dat` | `dialog_msg` | menu/dialog text |
| `rom/165/72.dat` | `dialog_msg` | menu/dialog text |
| `rom/165/74.dat` | `dialog_msg` | menu/dialog text |
| `rom/165/75.dat` | `dialog_msg` | menu/dialog text |
| `rom/165/76.dat` | `dialog_msg` | menu/dialog text |
| `rom/165/77.dat` | `dialog_msg` | menu/dialog text |
| `rom/165/78.dat` | `dialog_msg` | menu/dialog text |
| `rom/165/79.dat` | `dialog_msg` | menu/dialog text |
| `rom/165/80.dat` | `dialog_msg` | menu/dialog text |
| `rom/165/81.dat` | `dialog_msg` | menu/dialog text |
| `rom/165/82.dat` | `dialog_msg` | menu/dialog text |
| `rom/165/83.dat` | `dialog_msg` | menu/dialog text |
| `rom/165/84.dat` | `dialog_msg` | menu/dialog text |
| `rom/165/85.dat` | `dialog_msg` | menu/dialog text |
| `rom/165/86.dat` | `dialog_msg` | menu/dialog text |
| `rom/165/87.dat` | `dialog_msg` | menu/dialog text |
| `rom/168/25.dat` | `unknown` | head='.....yGreetings.' |
| `rom/171/5.dat` | `dialog_msg` | menu/dialog text |
| `rom/171/6.dat` | `dialog_msg` | menu/dialog text |
| `rom/171/7.dat` | `dialog_msg` | menu/dialog text |
| `rom/171/8.dat` | `dialog_msg` | menu/dialog text |
| `rom/175/31.dat` | `dialog_msg` | menu/dialog text |
| `rom/175/33.dat` | `dialog_msg` | menu/dialog text |
| `rom/175/35.dat` | `dialog_msg` | menu/dialog text |
| `rom/176/60.dat` | `dialog_msg` | menu/dialog text |
| `rom/176/61.dat` | `dialog_msg` | menu/dialog text |
| `rom/176/62.dat` | `dialog_msg` | menu/dialog text |
| `rom/176/63.dat` | `dialog_msg` | menu/dialog text |
| `rom/176/64.dat` | `dialog_msg` | menu/dialog text |
| `rom/176/65.dat` | `dialog_msg` | menu/dialog text |
| `rom/176/66.dat` | `dialog_msg` | menu/dialog text |
| `rom/176/67.dat` | `dialog_msg` | menu/dialog text |
| `rom/176/68.dat` | `dialog_msg` | menu/dialog text |
| `rom/176/69.dat` | `dialog_msg` | menu/dialog text |
| `rom/176/70.dat` | `dialog_msg` | menu/dialog text |
| `rom/176/71.dat` | `dialog_msg` | menu/dialog text |
| `rom/176/72.dat` | `dialog_msg` | menu/dialog text |
| `rom/176/73.dat` | `dialog_msg` | menu/dialog text |
| `rom/180/102.dat` | `dialog_msg` | menu/dialog text |
| `rom/180/78.dat` | `dialog_msg` | menu/dialog text |
| `rom/181/72.dat` | `dialog_msg` | menu/dialog text |
| `rom/181/73.dat` | `dialog_msg` | menu/dialog text |
| `rom/181/74.dat` | `dialog_msg` | menu/dialog text |
| `rom/181/75.dat` | `dialog_msg` | menu/dialog text |
| `rom/185/78.dat` | `dialog_msg` | menu/dialog text |
| `rom/187/70.dat` | `dialog_msg` | menu/dialog text |
| `rom/187/95.dat` | `dialog_msg` | menu/dialog text |
| `rom/196/6.dat` | `dialog_msg` | menu/dialog text |
| `rom/196/7.dat` | `dialog_msg` | menu/dialog text |
| `rom/196/8.dat` | `dialog_msg` | menu/dialog text |
| `rom/216/123.dat` | `unknown` | head='prvd............' |
| `rom/222/18.dat` | `dialog_msg` | menu/dialog text |
| `rom/223/12.dat` | `dialog_msg` | menu/dialog text |
| `rom/223/13.dat` | `dialog_msg` | menu/dialog text |
| `rom/242/64.dat` | `dialog_msg` | menu/dialog text |
| `rom/27/81.dat` | `unknown` | head='maru............' |
| `rom/280/15.dat` | `unknown` | head='mgc_............' |
| `rom/293/69.dat` | `dialog_msg` | menu/dialog text |
| `rom/293/70.dat` | `dialog_msg` | menu/dialog text |
| `rom/293/71.dat` | `dialog_msg` | menu/dialog text |
| `rom/324/95.dat` | `unknown` | head='mgc_............' |
| `rom/328/76.dat` | `unknown` | head='effe............' |
| `rom/333/16.dat` | `dialog_msg` | menu/dialog text |
| `rom/333/34.dat` | `dialog_msg` | menu/dialog text |
| `rom/333/4.dat` | `dialog_msg` | menu/dialog text |
| `rom/351/84.dat` | `dialog_msg` | menu/dialog text |
| `rom/351/85.dat` | `dialog_msg` | menu/dialog text |
| `rom/364/36.dat` | `dialog_msg` | menu/dialog text |
| `rom/76/28.dat` | `unknown` | head='effe............' |
| `rom/97/36.dat` | `xistring` | XISTRING........ |
| `rom/97/38.dat` | `xistring` | XISTRING........ |
| `rom2/ftable2.dat` | `file_table` |  |
| `rom2/vtable2.dat` | `file_table` |  |
| `rom3/ftable3.dat` | `file_table` |  |
| `rom3/vtable3.dat` | `file_table` |  |
| `rom4/ftable4.dat` | `file_table` |  |
| `rom4/vtable4.dat` | `file_table` |  |
| `rom5/ftable5.dat` | `file_table` |  |
| `rom5/vtable5.dat` | `file_table` |  |
| `rom6/ftable6.dat` | `file_table` |  |
| `rom6/vtable6.dat` | `file_table` |  |
| `rom7/ftable7.dat` | `file_table` |  |
| `rom7/vtable7.dat` | `file_table` |  |
| `rom8/ftable8.dat` | `file_table` |  |
| `rom8/vtable8.dat` | `file_table` |  |
| `rom9/ftable9.dat` | `file_table` |  |
| `rom9/vtable9.dat` | `file_table` |  |
| `user/tig.dat` | `user` |  |
| `vtable.dat` | `file_table` |  |

## How xi-model-viewer uses these

- Loader: `ui/js/creation.js` + `ui/src/App.jsx` (`loadCreation`)
- Mesh: RT/SHAPE + Y reflect; skeleton/skin from embedded SQLE
- Motion: `parseSqleMotion` (FrameChannel v.4 / PBChannel v.3 sign-magnitude MSB)
- Playback: `CreationAnimator` CPU skin (~300 bones), quat repair, root rebase, head attach bone4↔bone1
- Cameras: `buildCreationCamera` drives fly camera when Creation Sequence + camera toggle on
- Known incomplete: long `seq` without OC action bytecode; PB quat junk frames repaired by Hermite bridge

## Full classified inventory

Machine-readable: `D:\xi-tools\research\procmon_charcreate_classified.csv`

| path | size | kind | detail | in table | viewer role |
|---|---:|---|---|---|---|
| `ftable.dat` | 219402 | `file_table` |  | no | — |
| `rom/0/0.dat` | 1301632 | `system_dat` | syst............ | no | — |
| `rom/0/123.dat` | 19287696 | `font_or_effect` | f_gu............ | no | — |
| `rom/0/125.dat` | 19201072 | `font_or_effect` | f_or............ | no | — |
| `rom/0/14.dat` | 10880 | `ui_window` | win0............ | no | — |
| `rom/0/23.dat` | 73872 | `ui_title` | titl............ | no | — |
| `rom/0/27.dat` | 4960 | `ui_damage` | damv............ | no | — |
| `rom/1/5.dat` | 10960128 | `font_or_effect` | f_ch............ | no | — |
| `rom/118/103.dat` | 465 | `xistring` | XISTRING........ | no | — |
| `rom/118/114.dat` | 403344 | `ui_menu` | menu............ | no | — |
| `rom/118/115.dat` | 481392 | `ui_menu` | menu............ | no | — |
| `rom/119/50.dat` | 1358496 | `ui_lobby` | lobb............ | no | — |
| `rom/119/51.dat` | 4286240 | `ui_menu` | menu............ | no | — |
| `rom/165/69.dat` | 1772 | `dialog_msg` | menu/dialog text | no | — |
| `rom/165/70.dat` | 20336 | `dialog_msg` | menu/dialog text | no | — |
| `rom/165/72.dat` | 28424 | `dialog_msg` | menu/dialog text | no | — |
| `rom/165/74.dat` | 14504 | `dialog_msg` | menu/dialog text | no | — |
| `rom/165/75.dat` | 108608 | `dialog_msg` | menu/dialog text | no | — |
| `rom/165/76.dat` | 12508 | `dialog_msg` | menu/dialog text | no | — |
| `rom/165/77.dat` | 33844 | `dialog_msg` | menu/dialog text | no | — |
| `rom/165/78.dat` | 3556 | `dialog_msg` | menu/dialog text | no | — |
| `rom/165/79.dat` | 2140 | `dialog_msg` | menu/dialog text | no | — |
| `rom/165/80.dat` | 544 | `dialog_msg` | menu/dialog text | no | — |
| `rom/165/81.dat` | 1360 | `dialog_msg` | menu/dialog text | no | — |
| `rom/165/82.dat` | 832 | `dialog_msg` | menu/dialog text | no | — |
| `rom/165/83.dat` | 19556 | `dialog_msg` | menu/dialog text | no | — |
| `rom/165/84.dat` | 19984 | `dialog_msg` | menu/dialog text | no | — |
| `rom/165/85.dat` | 17800 | `dialog_msg` | menu/dialog text | no | — |
| `rom/165/86.dat` | 1864 | `dialog_msg` | menu/dialog text | no | — |
| `rom/165/87.dat` | 1728 | `dialog_msg` | menu/dialog text | no | — |
| `rom/168/25.dat` | 36798 | `unknown` | head='.....yGreetings.' | no | — |
| `rom/171/5.dat` | 616 | `dialog_msg` | menu/dialog text | no | — |
| `rom/171/6.dat` | 780 | `dialog_msg` | menu/dialog text | no | — |
| `rom/171/7.dat` | 2452 | `dialog_msg` | menu/dialog text | no | — |
| `rom/171/8.dat` | 1404 | `dialog_msg` | menu/dialog text | no | — |
| `rom/175/31.dat` | 1180 | `dialog_msg` | menu/dialog text | no | — |
| `rom/175/33.dat` | 956 | `dialog_msg` | menu/dialog text | no | — |
| `rom/175/35.dat` | 2270864 | `dialog_msg` | menu/dialog text | no | — |
| `rom/176/60.dat` | 71744 | `dialog_msg` | menu/dialog text | no | — |
| `rom/176/61.dat` | 64064 | `dialog_msg` | menu/dialog text | no | — |
| `rom/176/62.dat` | 64064 | `dialog_msg` | menu/dialog text | no | — |
| `rom/176/63.dat` | 101824 | `dialog_msg` | menu/dialog text | no | — |
| `rom/176/64.dat` | 83904 | `dialog_msg` | menu/dialog text | no | — |
| `rom/176/65.dat` | 139584 | `dialog_msg` | menu/dialog text | no | — |
| `rom/176/66.dat` | 81984 | `dialog_msg` | menu/dialog text | no | — |
| `rom/176/67.dat` | 15424 | `dialog_msg` | menu/dialog text | no | — |
| `rom/176/68.dat` | 41024 | `dialog_msg` | menu/dialog text | no | — |
| `rom/176/69.dat` | 41024 | `dialog_msg` | menu/dialog text | no | — |
| `rom/176/70.dat` | 41024 | `dialog_msg` | menu/dialog text | no | — |
| `rom/176/71.dat` | 41664 | `dialog_msg` | menu/dialog text | no | — |
| `rom/176/72.dat` | 81984 | `dialog_msg` | menu/dialog text | no | — |
| `rom/176/73.dat` | 41024 | `dialog_msg` | menu/dialog text | no | — |
| `rom/180/102.dat` | 105992 | `dialog_msg` | menu/dialog text | no | — |
| `rom/180/78.dat` | 294464 | `dialog_msg` | menu/dialog text | no | — |
| `rom/181/72.dat` | 471104 | `dialog_msg` | menu/dialog text | no | — |
| `rom/181/73.dat` | 143424 | `dialog_msg` | menu/dialog text | no | — |
| `rom/181/74.dat` | 1507392 | `dialog_msg` | menu/dialog text | no | — |
| `rom/181/75.dat` | 262208 | `dialog_msg` | menu/dialog text | no | — |
| `rom/185/78.dat` | 720 | `dialog_msg` | menu/dialog text | no | — |
| `rom/187/70.dat` | 238980 | `dialog_msg` | menu/dialog text | no | — |
| `rom/187/95.dat` | 7404 | `dialog_msg` | menu/dialog text | no | — |
| `rom/196/6.dat` | 81984 | `dialog_msg` | menu/dialog text | no | — |
| `rom/196/7.dat` | 34624 | `dialog_msg` | menu/dialog text | no | — |
| `rom/196/8.dat` | 327744 | `dialog_msg` | menu/dialog text | no | — |
| `rom/216/123.dat` | 94160 | `unknown` | head='prvd............' | no | — |
| `rom/222/18.dat` | 8384 | `dialog_msg` | menu/dialog text | no | — |
| `rom/223/12.dat` | 9664 | `dialog_msg` | menu/dialog text | no | — |
| `rom/223/13.dat` | 10304 | `dialog_msg` | menu/dialog text | no | — |
| `rom/242/64.dat` | 122944 | `dialog_msg` | menu/dialog text | no | — |
| `rom/27/81.dat` | 262288 | `unknown` | head='maru............' | no | — |
| `rom/280/15.dat` | 403840 | `unknown` | head='mgc_............' | no | — |
| `rom/293/69.dat` | 71744 | `dialog_msg` | menu/dialog text | no | — |
| `rom/293/70.dat` | 63424 | `dialog_msg` | menu/dialog text | no | — |
| `rom/293/71.dat` | 61504 | `dialog_msg` | menu/dialog text | no | — |
| `rom/324/95.dat` | 103984 | `unknown` | head='mgc_............' | no | — |
| `rom/328/76.dat` | 76896 | `unknown` | head='effe............' | no | — |
| `rom/333/16.dat` | 5512 | `dialog_msg` | menu/dialog text | no | — |
| `rom/333/34.dat` | 10656 | `dialog_msg` | menu/dialog text | no | — |
| `rom/333/4.dat` | 63424 | `dialog_msg` | menu/dialog text | no | — |
| `rom/351/84.dat` | 3184 | `dialog_msg` | menu/dialog text | no | — |
| `rom/351/85.dat` | 10048 | `dialog_msg` | menu/dialog text | no | — |
| `rom/364/36.dat` | 30144 | `dialog_msg` | menu/dialog text | no | — |
| `rom/63/10.dat` | 2221572 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | ElvaanF face2 mat B |
| `rom/63/102.dat` | 1065044 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | Mithra body material (initial equipment) |
| `rom/63/103.dat` | 184088 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | Mithra body mesh (initial equipment) |
| `rom/63/104.dat` | 2265912 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | Mithra face1 mat A |
| `rom/63/105.dat` | 366280 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | Mithra face1 mesh |
| `rom/63/106.dat` | 2265912 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | Mithra face1 mat B |
| `rom/63/107.dat` | 363928 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/108.dat` | 2264820 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | Mithra face2 mat A |
| `rom/63/109.dat` | 344168 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | Mithra face2 mesh |
| `rom/63/11.dat` | 323240 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/110.dat` | 2266140 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | Mithra face2 mat B |
| `rom/63/111.dat` | 363592 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/112.dat` | 2265912 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | Mithra face3 mat A |
| `rom/63/113.dat` | 342840 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | Mithra face3 mesh |
| `rom/63/114.dat` | 2265912 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | Mithra face3 mat B |
| `rom/63/115.dat` | 339832 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/116.dat` | 2266140 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | Mithra face4 mat A |
| `rom/63/117.dat` | 346296 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | Mithra face4 mesh |
| `rom/63/118.dat` | 2241516 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | Mithra face4 mat B |
| `rom/63/119.dat` | 343272 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/12.dat` | 2220448 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | ElvaanF face3 mat A |
| `rom/63/122.dat` | 1077396 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | TaruF body material (initial equipment) |
| `rom/63/123.dat` | 153608 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | TaruF body mesh (initial equipment) |
| `rom/63/124.dat` | 2257348 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | TaruF face1 mat A |
| `rom/63/125.dat` | 337776 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | TaruF face1 mesh |
| `rom/63/126.dat` | 2257816 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | TaruF face1 mat B |
| `rom/63/127.dat` | 337984 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/13.dat` | 265304 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | ElvaanF face3 mesh |
| `rom/63/14.dat` | 2220480 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | ElvaanF face3 mat B |
| `rom/63/15.dat` | 327736 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/16.dat` | 2219404 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | ElvaanF face4 mat A |
| `rom/63/17.dat` | 317520 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | ElvaanF face4 mesh |
| `rom/63/18.dat` | 2219404 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | ElvaanF face4 mat B |
| `rom/63/19.dat` | 298232 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/2.dat` | 1113092 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | ElvaanF body material (initial equipment) |
| `rom/63/22.dat` | 1063564 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | ElvaanM body material (initial equipment) |
| `rom/63/23.dat` | 186224 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | ElvaanM body mesh (initial equipment) |
| `rom/63/24.dat` | 2272656 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | ElvaanM face1 mat A |
| `rom/63/25.dat` | 322792 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | ElvaanM face1 mesh |
| `rom/63/26.dat` | 2256272 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | ElvaanM face1 mat B |
| `rom/63/27.dat` | 317408 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/28.dat` | 2273732 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | ElvaanM face2 mat A |
| `rom/63/29.dat` | 267632 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | ElvaanM face2 mesh |
| `rom/63/3.dat` | 203392 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | ElvaanF body mesh (initial equipment) |
| `rom/63/30.dat` | 2258424 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | ElvaanM face2 mat B |
| `rom/63/31.dat` | 297584 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/32.dat` | 2257328 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | ElvaanM face3 mat A |
| `rom/63/33.dat` | 235984 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | ElvaanM face3 mesh |
| `rom/63/34.dat` | 2256252 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | ElvaanM face3 mat B |
| `rom/63/35.dat` | 287992 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/36.dat` | 2219632 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | ElvaanM face4 mat A |
| `rom/63/37.dat` | 331160 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | ElvaanM face4 mesh |
| `rom/63/38.dat` | 2258448 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | ElvaanM face4 mat B |
| `rom/63/39.dat` | 258240 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/4.dat` | 2219404 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | ElvaanF face1 mat A |
| `rom/63/42.dat` | 1326120 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | Galka body material (initial equipment) |
| `rom/63/43.dat` | 198568 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | Galka body mesh (initial equipment) |
| `rom/63/44.dat` | 2251404 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | Galka face1 mat A |
| `rom/63/45.dat` | 234280 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | Galka face1 mesh |
| `rom/63/46.dat` | 2251404 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | Galka face1 mat B |
| `rom/63/47.dat` | 227056 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/48.dat` | 2214548 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | Galka face2 mat A |
| `rom/63/49.dat` | 228128 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | Galka face2 mesh |
| `rom/63/5.dat` | 309216 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | ElvaanF face1 mesh |
| `rom/63/50.dat` | 2214548 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | Galka face2 mat B |
| `rom/63/51.dat` | 217280 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/52.dat` | 2214540 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | Galka face3 mat A |
| `rom/63/53.dat` | 243816 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | Galka face3 mesh |
| `rom/63/54.dat` | 2214540 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | Galka face3 mat B |
| `rom/63/55.dat` | 199768 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/56.dat` | 2214540 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | Galka face4 mat A |
| `rom/63/57.dat` | 240760 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | Galka face4 mesh |
| `rom/63/58.dat` | 2214540 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | Galka face4 mat B |
| `rom/63/59.dat` | 241120 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/6.dat` | 2219404 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | ElvaanF face1 mat B |
| `rom/63/62.dat` | 3430740 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | HumeF body material (initial equipment) |
| `rom/63/63.dat` | 192168 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | HumeF body mesh (initial equipment) |
| `rom/63/64.dat` | 2256484 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | HumeF face1 mat A |
| `rom/63/65.dat` | 271608 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | HumeF face1 mesh |
| `rom/63/66.dat` | 2220660 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | HumeF face1 mat B |
| `rom/63/67.dat` | 287928 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/68.dat` | 2256388 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | HumeF face2 mat A |
| `rom/63/69.dat` | 277000 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | HumeF face2 mesh |
| `rom/63/7.dat` | 284984 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/70.dat` | 2256348 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | HumeF face2 mat B |
| `rom/63/71.dat` | 292040 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/72.dat` | 2219372 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | HumeF face3 mat A |
| `rom/63/73.dat` | 248752 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | HumeF face3 mesh |
| `rom/63/74.dat` | 2256268 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | HumeF face3 mat B |
| `rom/63/75.dat` | 290536 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/76.dat` | 2219404 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | HumeF face4 mat A |
| `rom/63/77.dat` | 277376 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | HumeF face4 mesh |
| `rom/63/78.dat` | 2256268 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | HumeF face4 mat B |
| `rom/63/79.dat` | 287752 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/8.dat` | 2220484 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | ElvaanF face2 mat A |
| `rom/63/82.dat` | 1352764 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | HumeM body material (initial equipment) |
| `rom/63/83.dat` | 164208 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | HumeM body mesh (initial equipment) |
| `rom/63/84.dat` | 2256268 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | HumeM face1 mat A |
| `rom/63/85.dat` | 330104 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | HumeM face1 mesh |
| `rom/63/86.dat` | 2256268 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | HumeM face1 mat B |
| `rom/63/87.dat` | 324488 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/88.dat` | 2219388 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | HumeM face2 mat A |
| `rom/63/89.dat` | 267328 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | HumeM face2 mesh |
| `rom/63/9.dat` | 315104 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | ElvaanF face2 mesh |
| `rom/63/90.dat` | 2305584 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | HumeM face2 mat B |
| `rom/63/91.dat` | 308208 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/92.dat` | 2206180 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | HumeM face3 mat A |
| `rom/63/93.dat` | 167232 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | HumeM face3 mesh |
| `rom/63/94.dat` | 2209660 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | HumeM face3 mat B |
| `rom/63/95.dat` | 341784 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/63/96.dat` | 2257372 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | HumeM face4 mat A |
| `rom/63/97.dat` | 218024 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | HumeM face4 mesh |
| `rom/63/98.dat` | 2483896 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | HumeM face4 mat B |
| `rom/63/99.dat` | 279632 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/64/0.dat` | 2258420 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | TaruF face2 mat A |
| `rom/64/1.dat` | 331400 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | TaruF face2 mesh |
| `rom/64/10.dat` | 2258420 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | TaruF face4 mat B |
| `rom/64/104.dat` | 776077 | `sqle_motion` | pb; ch=533; frames=1237; time=41.2; role=skeleton_clip | yes | ElvaanM face1 head motion BASE; ElvaanM face1 head seq |
| `rom/64/105.dat` | 27833 | `sqle_motion` | pb; ch=533; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/64/11.dat` | 344752 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/64/110.dat` | 852731 | `sqle_motion` | pb; ch=565; frames=1237; time=41.2; role=skeleton_clip | no | — |
| `rom/64/111.dat` | 26345 | `sqle_motion` | pb; ch=565; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/64/116.dat` | 226316 | `sqle_motion` | pb; ch=329; frames=1237; time=41.2; role=skeleton_clip | yes | ElvaanM face2 head motion BASE; ElvaanM face2 head seq |
| `rom/64/117.dat` | 17797 | `sqle_motion` | pb; ch=329; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/64/122.dat` | 228887 | `sqle_motion` | pb; ch=363; frames=1237; time=41.2; role=skeleton_clip | no | — |
| `rom/64/123.dat` | 24499 | `sqle_motion` | pb; ch=363; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/64/14.dat` | 1077396 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | TaruM body material (initial equipment) |
| `rom/64/15.dat` | 153608 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | TaruM body mesh (initial equipment) |
| `rom/64/16.dat` | 2256268 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | TaruM face1 mat A |
| `rom/64/17.dat` | 328704 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | TaruM face1 mesh |
| `rom/64/18.dat` | 2256268 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | TaruM face1 mat B |
| `rom/64/19.dat` | 367960 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/64/2.dat` | 2258420 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | TaruF face2 mat B |
| `rom/64/20.dat` | 2257348 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | TaruM face2 mat A |
| `rom/64/21.dat` | 305696 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | TaruM face2 mesh |
| `rom/64/22.dat` | 2257344 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | TaruM face2 mat B |
| `rom/64/23.dat` | 286952 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/64/24.dat` | 2256268 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | TaruM face3 mat A |
| `rom/64/25.dat` | 328704 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | TaruM face3 mesh |
| `rom/64/26.dat` | 2256268 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | TaruM face3 mat B |
| `rom/64/27.dat` | 367960 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/64/28.dat` | 2257348 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | TaruM face4 mat A |
| `rom/64/29.dat` | 305696 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | TaruM face4 mesh |
| `rom/64/3.dat` | 356368 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/64/30.dat` | 2257344 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | TaruM face4 mat B |
| `rom/64/31.dat` | 286952 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/64/32.dat` | 137 | `sqle_motion` | pb; ch=1; frames=2301; time=76.6667; role=camera_fov | yes | ElvaanF camera1 FOV |
| `rom/64/33.dat` | 147444 | `sqle_motion` | pb; ch=16; frames=2301; time=76.6667; role=camera_matrix | yes | ElvaanF camera1 matrix |
| `rom/64/34.dat` | 137 | `sqle_motion` | pb; ch=1; frames=2301; time=76.6667; role=camera_fov | yes | ElvaanF camera2 FOV |
| `rom/64/35.dat` | 147444 | `sqle_motion` | pb; ch=16; frames=2301; time=76.6667; role=camera_matrix | yes | ElvaanF camera2 matrix |
| `rom/64/4.dat` | 2257348 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | TaruF face3 mat A |
| `rom/64/40.dat` | 939399 | `sqle_motion` | pb; ch=299; frames=2301; time=76.6667; role=skeleton_clip | yes | ElvaanF body motion BASE (seq / offset 0 cluster); ElvaanF body creation SEQUENCE |
| `rom/64/41.dat` | 11231 | `sqle_motion` | pb; ch=299; frames=121; time=3.96667; role=skeleton_clip | no | — |
| `rom/64/46.dat` | 602553 | `sqle_motion` | pb; ch=382; frames=2301; time=76.6667; role=skeleton_clip | yes | ElvaanF face1 head motion BASE; ElvaanF face1 head seq |
| `rom/64/47.dat` | 24300 | `sqle_motion` | pb; ch=382; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/64/5.dat` | 337776 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | TaruF face3 mesh |
| `rom/64/52.dat` | 214553 | `sqle_motion` | pb; ch=302; frames=2301; time=76.6667; role=skeleton_clip | yes | ElvaanF face3 head motion BASE; ElvaanF face3 head seq |
| `rom/64/53.dat` | 21720 | `sqle_motion` | pb; ch=302; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/64/58.dat` | 882074 | `sqle_motion` | pb; ch=443; frames=2301; time=76.6667; role=skeleton_clip | yes | ElvaanF face2 head motion BASE; ElvaanF face2 head seq |
| `rom/64/59.dat` | 25640 | `sqle_motion` | pb; ch=443; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/64/6.dat` | 2257816 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | TaruF face3 mat B |
| `rom/64/64.dat` | 690353 | `sqle_motion` | pb; ch=402; frames=2301; time=76.6667; role=skeleton_clip | no | — |
| `rom/64/65.dat` | 23280 | `sqle_motion` | pb; ch=402; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/64/7.dat` | 337984 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | no | — |
| `rom/64/70.dat` | 214553 | `sqle_motion` | pb; ch=302; frames=2301; time=76.6667; role=skeleton_clip | no | — |
| `rom/64/71.dat` | 20760 | `sqle_motion` | pb; ch=302; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/64/76.dat` | 918824 | `sqle_motion` | pb; ch=441; frames=2301; time=76.6667; role=skeleton_clip | no | — |
| `rom/64/77.dat` | 24638 | `sqle_motion` | pb; ch=441; frames=241; time=7.96667; role=skeleton_clip | no | — |
| `rom/64/8.dat` | 2258420 | `dmb_material` | texture/material for RT/SHAPE mesh | yes | TaruF face4 mat A |
| `rom/64/82.dat` | 214364 | `sqle_motion` | pb; ch=293; frames=2301; time=76.6667; role=skeleton_clip | yes | ElvaanF face4 head motion BASE; ElvaanF face4 head seq |
| `rom/64/83.dat` | 19371 | `sqle_motion` | pb; ch=293; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/64/88.dat` | 789464 | `sqle_motion` | pb; ch=413; frames=2301; time=76.6667; role=skeleton_clip | no | — |
| `rom/64/89.dat` | 25010 | `sqle_motion` | pb; ch=413; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/64/9.dat` | 331400 | `mesh_rt_shape` | sqle=[10, 11, 21]; embeds=skeleton,skin | yes | TaruF face4 mesh |
| `rom/64/90.dat` | 137 | `sqle_motion` | pb; ch=1; frames=1237; time=41.2; role=camera_fov | yes | ElvaanM camera1 FOV |
| `rom/64/91.dat` | 27941 | `sqle_motion` | pb; ch=16; frames=1237; time=41.2; role=camera_matrix | yes | ElvaanM camera1 matrix |
| `rom/64/92.dat` | 137 | `sqle_motion` | pb; ch=1; frames=1237; time=41.2; role=camera_fov | yes | ElvaanM camera2 FOV |
| `rom/64/93.dat` | 27941 | `sqle_motion` | pb; ch=16; frames=1237; time=41.2; role=camera_matrix | yes | ElvaanM camera2 matrix |
| `rom/64/98.dat` | 504292 | `sqle_motion` | pb; ch=299; frames=1237; time=41.2; role=skeleton_clip | yes | ElvaanM body motion BASE (seq / offset 0 cluster); ElvaanM body creation SEQUENCE |
| `rom/64/99.dat` | 13184 | `sqle_motion` | pb; ch=299; frames=121; time=3.96667; role=skeleton_clip | no | — |
| `rom/65/0.dat` | 215703 | `sqle_motion` | pb; ch=353; frames=1237; time=41.2; role=skeleton_clip | yes | ElvaanM face3 head motion BASE; ElvaanM face3 head seq |
| `rom/65/1.dat` | 22251 | `sqle_motion` | pb; ch=353; frames=241; time=7.96667; role=skeleton_clip | no | — |
| `rom/65/104.dat` | 1069252 | `sqle_motion` | pb; ch=480; frames=1808; time=60.2333; role=skeleton_clip | yes | HumeF face2 head motion BASE; HumeF face2 head seq |
| `rom/65/105.dat` | 21685 | `sqle_motion` | pb; ch=480; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/65/110.dat` | 853660 | `sqle_motion` | pb; ch=429; frames=1808; time=60.2333; role=skeleton_clip | no | — |
| `rom/65/111.dat` | 20254 | `sqle_motion` | pb; ch=429; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/65/116.dat` | 204643 | `sqle_motion` | pb; ch=256; frames=1808; time=60.2333; role=skeleton_clip | yes | HumeF face3 head motion BASE; HumeF face3 head seq |
| `rom/65/117.dat` | 15781 | `sqle_motion` | pb; ch=256; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/65/12.dat` | 497694 | `sqle_motion` | pb; ch=426; frames=1237; time=41.2; role=skeleton_clip | yes | ElvaanM face4 head motion BASE; ElvaanM face4 head seq |
| `rom/65/122.dat` | 308884 | `sqle_motion` | pb; ch=316; frames=1808; time=60.2333; role=skeleton_clip | no | — |
| `rom/65/123.dat` | 17641 | `sqle_motion` | pb; ch=316; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/65/13.dat` | 21030 | `sqle_motion` | pb; ch=426; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/65/18.dat` | 170215 | `sqle_motion` | pb; ch=305; frames=1237; time=41.2; role=skeleton_clip | no | — |
| `rom/65/19.dat` | 22085 | `sqle_motion` | pb; ch=305; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/65/20.dat` | 137 | `sqle_motion` | pb; ch=1; frames=1726; time=57.5; role=camera_fov | yes | Galka camera1 FOV |
| `rom/65/21.dat` | 38391 | `sqle_motion` | pb; ch=16; frames=1726; time=57.5; role=camera_matrix | yes | Galka camera1 matrix |
| `rom/65/22.dat` | 137 | `sqle_motion` | pb; ch=1; frames=1726; time=57.5; role=camera_fov | yes | Galka camera2 FOV |
| `rom/65/23.dat` | 38822 | `sqle_motion` | pb; ch=16; frames=1726; time=57.5; role=camera_matrix | yes | Galka camera2 matrix |
| `rom/65/28.dat` | 843789 | `sqle_motion` | pb; ch=389; frames=1726; time=57.5; role=skeleton_clip | yes | Galka body motion BASE (seq / offset 0 cluster); Galka body creation SEQUENCE |
| `rom/65/29.dat` | 27588 | `sqle_motion` | pb; ch=389; frames=121; time=3.96667; role=skeleton_clip | no | — |
| `rom/65/34.dat` | 229602 | `sqle_motion` | pb; ch=253; frames=1726; time=57.5; role=skeleton_clip | yes | Galka face1 head motion BASE; Galka face1 head seq |
| `rom/65/35.dat` | 16198 | `sqle_motion` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/65/40.dat` | 229602 | `sqle_motion` | pb; ch=253; frames=1726; time=57.5; role=skeleton_clip | yes | Galka face2 head motion BASE; Galka face2 head seq |
| `rom/65/41.dat` | 16198 | `sqle_motion` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/65/46.dat` | 231327 | `sqle_motion` | pb; ch=253; frames=1726; time=57.5; role=skeleton_clip | yes | Galka face3 head motion BASE; Galka face3 head seq |
| `rom/65/47.dat` | 16198 | `sqle_motion` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/65/52.dat` | 231327 | `sqle_motion` | pb; ch=253; frames=1726; time=57.5; role=skeleton_clip | yes | Galka face4 head motion BASE; Galka face4 head seq |
| `rom/65/53.dat` | 16198 | `sqle_motion` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/65/58.dat` | 231327 | `sqle_motion` | pb; ch=253; frames=1726; time=57.5; role=skeleton_clip | no | — |
| `rom/65/59.dat` | 16198 | `sqle_motion` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/65/6.dat` | 164404 | `sqle_motion` | pb; ch=293; frames=1237; time=41.2; role=skeleton_clip | no | — |
| `rom/65/64.dat` | 224429 | `sqle_motion` | pb; ch=253; frames=1726; time=57.5; role=skeleton_clip | no | — |
| `rom/65/65.dat` | 16198 | `sqle_motion` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/65/7.dat` | 20033 | `sqle_motion` | pb; ch=293; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/65/70.dat` | 231327 | `sqle_motion` | pb; ch=253; frames=1726; time=57.5; role=skeleton_clip | no | — |
| `rom/65/71.dat` | 16198 | `sqle_motion` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/65/76.dat` | 231327 | `sqle_motion` | pb; ch=253; frames=1726; time=57.5; role=skeleton_clip | no | — |
| `rom/65/77.dat` | 16198 | `sqle_motion` | pb; ch=253; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/65/78.dat` | 137 | `sqle_motion` | pb; ch=1; frames=1808; time=60.2333; role=camera_fov | yes | HumeF camera1 FOV |
| `rom/65/79.dat` | 43808 | `sqle_motion` | pb; ch=16; frames=1808; time=60.2333; role=camera_matrix | yes | HumeF camera1 matrix |
| `rom/65/80.dat` | 137 | `sqle_motion` | pb; ch=1; frames=1808; time=60.2333; role=camera_fov | yes | HumeF camera2 FOV |
| `rom/65/81.dat` | 40195 | `sqle_motion` | pb; ch=16; frames=1808; time=60.2333; role=camera_matrix | yes | HumeF camera2 matrix |
| `rom/65/86.dat` | 752477 | `sqle_motion` | pb; ch=299; frames=1808; time=60.2333; role=skeleton_clip | yes | HumeF body motion BASE (seq / offset 0 cluster); HumeF body creation SEQUENCE |
| `rom/65/87.dat` | 11054 | `sqle_motion` | pb; ch=299; frames=121; time=3.96667; role=skeleton_clip | no | — |
| `rom/65/92.dat` | 533868 | `sqle_motion` | pb; ch=341; frames=1808; time=60.2333; role=skeleton_clip | yes | HumeF face1 head motion BASE; HumeF face1 head seq |
| `rom/65/93.dat` | 18166 | `sqle_motion` | pb; ch=341; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/65/98.dat` | 467243 | `sqle_motion` | pb; ch=330; frames=1808; time=60.2333; role=skeleton_clip | no | — |
| `rom/65/99.dat` | 18415 | `sqle_motion` | pb; ch=330; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/66/0.dat` | 233554 | `sqle_motion` | pb; ch=256; frames=1808; time=60.2333; role=skeleton_clip | yes | HumeF face4 head motion BASE; HumeF face4 head seq |
| `rom/66/1.dat` | 14821 | `sqle_motion` | pb; ch=256; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/66/10.dat` | 137 | `sqle_motion` | pb; ch=1; frames=2387; time=79.5333; role=camera_fov | yes | HumeM camera2 FOV |
| `rom/66/104.dat` | 1050750 | `sqle_motion` | pb; ch=396; frames=3001; time=100; role=skeleton_clip | yes | Mithra face3 head motion BASE; Mithra face3 head seq |
| `rom/66/105.dat` | 33428 | `sqle_motion` | pb; ch=396; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/66/11.dat` | 57704 | `sqle_motion` | pb; ch=16; frames=2387; time=79.5333; role=camera_matrix | yes | HumeM camera2 matrix |
| `rom/66/110.dat` | 1721990 | `sqle_motion` | pb; ch=508; frames=3001; time=100; role=skeleton_clip | no | — |
| `rom/66/111.dat` | 33809 | `sqle_motion` | pb; ch=508; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/66/116.dat` | 987510 | `sqle_motion` | pb; ch=384; frames=3001; time=100; role=skeleton_clip | yes | Mithra face4 head motion BASE; Mithra face4 head seq |
| `rom/66/117.dat` | 36656 | `sqle_motion` | pb; ch=384; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/66/122.dat` | 972510 | `sqle_motion` | pb; ch=384; frames=3001; time=100; role=skeleton_clip | no | — |
| `rom/66/123.dat` | 32645 | `sqle_motion` | pb; ch=384; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/66/124.dat` | 137 | `sqle_motion` | pb; ch=1; frames=1315; time=43.8; role=camera_fov | yes | TaruF camera1 FOV |
| `rom/66/125.dat` | 29349 | `sqle_motion` | pb; ch=16; frames=1315; time=43.8; role=camera_matrix | yes | TaruF camera1 matrix |
| `rom/66/126.dat` | 137 | `sqle_motion` | pb; ch=1; frames=1315; time=43.8; role=camera_fov | yes | TaruF camera2 FOV |
| `rom/66/127.dat` | 31976 | `sqle_motion` | pb; ch=16; frames=1315; time=43.8; role=camera_matrix | yes | TaruF camera2 matrix |
| `rom/66/16.dat` | 954627 | `sqle_motion` | pb; ch=299; frames=2387; time=79.5333; role=skeleton_clip | yes | HumeM body motion BASE (seq / offset 0 cluster); HumeM body creation SEQUENCE |
| `rom/66/17.dat` | 14018 | `sqle_motion` | pb; ch=299; frames=121; time=3.96667; role=skeleton_clip | no | — |
| `rom/66/22.dat` | 1465516 | `sqle_motion` | pb; ch=488; frames=2387; time=79.5333; role=skeleton_clip | yes | HumeM face1 head motion BASE; HumeM face1 head seq |
| `rom/66/23.dat` | 24474 | `sqle_motion` | pb; ch=488; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/66/28.dat` | 1702772 | `sqle_motion` | pb; ch=540; frames=2387; time=79.5333; role=skeleton_clip | no | — |
| `rom/66/29.dat` | 26226 | `sqle_motion` | pb; ch=540; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/66/34.dat` | 296508 | `sqle_motion` | pb; ch=256; frames=2387; time=79.5333; role=skeleton_clip | yes | HumeM face2 head motion BASE; HumeM face2 head seq |
| `rom/66/35.dat` | 17144 | `sqle_motion` | pb; ch=256; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/66/40.dat` | 758957 | `sqle_motion` | pb; ch=353; frames=2387; time=79.5333; role=skeleton_clip | no | — |
| `rom/66/41.dat` | 22539 | `sqle_motion` | pb; ch=353; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/66/46.dat` | 270459 | `sqle_motion` | pb; ch=265; frames=2387; time=79.5333; role=skeleton_clip | yes | HumeM face3 head motion BASE; HumeM face3 head seq |
| `rom/66/47.dat` | 13501 | `sqle_motion` | pb; ch=265; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/66/52.dat` | 355847 | `sqle_motion` | pb; ch=355; frames=2387; time=79.5333; role=skeleton_clip | no | — |
| `rom/66/53.dat` | 17909 | `sqle_motion` | pb; ch=355; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/66/58.dat` | 384026 | `sqle_motion` | pb; ch=277; frames=2387; time=79.5333; role=skeleton_clip | yes | HumeM face4 head motion BASE; HumeM face4 head seq |
| `rom/66/59.dat` | 19143 | `sqle_motion` | pb; ch=277; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/66/6.dat` | 810052 | `sqle_motion` | pb; ch=417; frames=1808; time=60.2333; role=skeleton_clip | no | — |
| `rom/66/64.dat` | 296508 | `sqle_motion` | pb; ch=256; frames=2387; time=79.5333; role=skeleton_clip | no | — |
| `rom/66/65.dat` | 17144 | `sqle_motion` | pb; ch=256; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/66/66.dat` | 137 | `sqle_motion` | pb; ch=1; frames=3001; time=100; role=camera_fov | yes | Mithra camera1 FOV |
| `rom/66/67.dat` | 72440 | `sqle_motion` | pb; ch=16; frames=3001; time=100; role=camera_matrix | yes | Mithra camera1 matrix |
| `rom/66/68.dat` | 137 | `sqle_motion` | pb; ch=1; frames=3001; time=100; role=camera_fov | yes | Mithra camera2 FOV |
| `rom/66/69.dat` | 72440 | `sqle_motion` | pb; ch=16; frames=3001; time=100; role=camera_matrix | yes | Mithra camera2 matrix |
| `rom/66/7.dat` | 19762 | `sqle_motion` | pb; ch=417; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/66/74.dat` | 1808358 | `sqle_motion` | pb; ch=407; frames=3001; time=100; role=skeleton_clip | yes | Mithra body motion BASE (seq / offset 0 cluster); Mithra body creation SEQUENCE |
| `rom/66/75.dat` | 20825 | `sqle_motion` | pb; ch=407; frames=121; time=3.96667; role=skeleton_clip | no | — |
| `rom/66/8.dat` | 137 | `sqle_motion` | pb; ch=1; frames=2387; time=79.5333; role=camera_fov | yes | HumeM camera1 FOV |
| `rom/66/80.dat` | 1020670 | `sqle_motion` | pb; ch=392; frames=3001; time=100; role=skeleton_clip | yes | Mithra face1 head motion BASE; Mithra face1 head seq |
| `rom/66/81.dat` | 34724 | `sqle_motion` | pb; ch=392; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/66/86.dat` | 1113990 | `sqle_motion` | pb; ch=408; frames=3001; time=100; role=skeleton_clip | no | — |
| `rom/66/87.dat` | 33149 | `sqle_motion` | pb; ch=408; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/66/9.dat` | 57704 | `sqle_motion` | pb; ch=16; frames=2387; time=79.5333; role=camera_matrix | yes | HumeM camera1 matrix |
| `rom/66/92.dat` | 1357790 | `sqle_motion` | pb; ch=448; frames=3001; time=100; role=skeleton_clip | yes | Mithra face2 head motion BASE; Mithra face2 head seq |
| `rom/66/93.dat` | 33802 | `sqle_motion` | pb; ch=448; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/66/98.dat` | 794200 | `sqle_motion` | pb; ch=356; frames=3001; time=100; role=skeleton_clip | no | — |
| `rom/66/99.dat` | 29716 | `sqle_motion` | pb; ch=356; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/67/10.dat` | 660145 | `sqle_motion` | pb; ch=469; frames=1315; time=43.8; role=skeleton_clip | yes | TaruF face1 head motion BASE; TaruF face1 head seq |
| `rom/67/100.dat` | 262050 | `sqle_motion` | pb; ch=343; frames=1624; time=54.1; role=skeleton_clip | yes | TaruM face4 head motion BASE; TaruM face4 head seq |
| `rom/67/101.dat` | 36290 | `sqle_motion` | pb; ch=343; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/67/106.dat` | 535596 | `sqle_motion` | pb; ch=389; frames=1624; time=54.1; role=skeleton_clip | no | — |
| `rom/67/107.dat` | 38696 | `sqle_motion` | pb; ch=389; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/67/108.dat` | 1632 | `creation_cue` | OC:01.00 frame→action cue track | no | — |
| `rom/67/109.dat` | 1404 | `creation_cue` | OC:01.00 frame→action cue track | no | — |
| `rom/67/11.dat` | 70035 | `sqle_motion` | pb; ch=469; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/67/110.dat` | 576 | `creation_cue` | OC:01.00 frame→action cue track | no | — |
| `rom/67/111.dat` | 972 | `creation_cue` | OC:01.00 frame→action cue track | no | — |
| `rom/67/112.dat` | 1560 | `creation_cue` | OC:01.00 frame→action cue track | no | — |
| `rom/67/113.dat` | 3264 | `creation_cue` | OC:01.00 frame→action cue track | no | — |
| `rom/67/114.dat` | 1332 | `creation_cue` | OC:01.00 frame→action cue track | no | — |
| `rom/67/115.dat` | 696 | `creation_cue` | OC:01.00 frame→action cue track | no | — |
| `rom/67/116.dat` | 14176 | `race_action_table` | rthu | no | — |
| `rom/67/117.dat` | 9088 | `race_action_table` | rthu | no | — |
| `rom/67/118.dat` | 8176 | `race_action_table` | rtel | no | — |
| `rom/67/119.dat` | 7232 | `race_action_table` | rtel | no | — |
| `rom/67/120.dat` | 11696 | `race_action_table` | rtta | no | — |
| `rom/67/121.dat` | 15440 | `race_action_table` | rtta | no | — |
| `rom/67/122.dat` | 8112 | `race_action_table` | rtmi | no | — |
| `rom/67/123.dat` | 14064 | `race_action_table` | rtga | no | — |
| `rom/67/16.dat` | 676730 | `sqle_motion` | pb; ch=477; frames=1315; time=43.8; role=skeleton_clip | no | — |
| `rom/67/17.dat` | 56141 | `sqle_motion` | pb; ch=477; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/67/22.dat` | 362912 | `sqle_motion` | pb; ch=357; frames=1315; time=43.8; role=skeleton_clip | yes | TaruF face2 head motion BASE; TaruF face2 head seq |
| `rom/67/23.dat` | 42690 | `sqle_motion` | pb; ch=357; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/67/28.dat` | 406288 | `sqle_motion` | pb; ch=421; frames=1315; time=43.8; role=skeleton_clip | no | — |
| `rom/67/29.dat` | 38817 | `sqle_motion` | pb; ch=421; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/67/34.dat` | 660145 | `sqle_motion` | pb; ch=469; frames=1315; time=43.8; role=skeleton_clip | yes | TaruF face3 head motion BASE; TaruF face3 head seq |
| `rom/67/35.dat` | 70035 | `sqle_motion` | pb; ch=469; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/67/4.dat` | 312746 | `sqle_motion` | pb; ch=251; frames=1315; time=43.8; role=skeleton_clip | yes | TaruM body motion BASE (seq / offset 0 cluster); TaruF body motion BASE (seq / offset 0 cluster); TaruF body creation SEQUENCE |
| `rom/67/40.dat` | 676730 | `sqle_motion` | pb; ch=477; frames=1315; time=43.8; role=skeleton_clip | no | — |
| `rom/67/41.dat` | 56141 | `sqle_motion` | pb; ch=477; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/67/46.dat` | 362912 | `sqle_motion` | pb; ch=357; frames=1315; time=43.8; role=skeleton_clip | yes | TaruF face4 head motion BASE; TaruF face4 head seq |
| `rom/67/47.dat` | 41252 | `sqle_motion` | pb; ch=357; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/67/5.dat` | 23406 | `sqle_motion` | pb; ch=251; frames=121; time=3.96667; role=skeleton_clip | no | — |
| `rom/67/52.dat` | 406288 | `sqle_motion` | pb; ch=421; frames=1315; time=43.8; role=skeleton_clip | no | — |
| `rom/67/53.dat` | 38817 | `sqle_motion` | pb; ch=421; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/67/54.dat` | 137 | `sqle_motion` | pb; ch=1; frames=1624; time=54.1; role=camera_fov | yes | TaruM camera1 FOV |
| `rom/67/55.dat` | 36147 | `sqle_motion` | pb; ch=16; frames=1624; time=54.1; role=camera_matrix | yes | TaruM camera1 matrix |
| `rom/67/56.dat` | 137 | `sqle_motion` | pb; ch=1; frames=1624; time=54.1; role=camera_fov | yes | TaruM camera2 FOV |
| `rom/67/57.dat` | 36958 | `sqle_motion` | pb; ch=16; frames=1624; time=54.1; role=camera_matrix | yes | TaruM camera2 matrix |
| `rom/67/58.dat` | 482402 | `sqle_motion` | pb; ch=251; frames=1624; time=54.1; role=skeleton_clip | yes | TaruM body creation SEQUENCE |
| `rom/67/59.dat` | 36676 | `sqle_motion` | pb; ch=251; frames=121; time=3.96667; role=skeleton_clip | no | — |
| `rom/67/64.dat` | 613980 | `sqle_motion` | pb; ch=413; frames=1624; time=54.1; role=skeleton_clip | yes | TaruM face1 head motion BASE; TaruM face1 head seq |
| `rom/67/65.dat` | 56184 | `sqle_motion` | pb; ch=413; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/67/70.dat` | 1022210 | `sqle_motion` | pb; ch=537; frames=1624; time=54.1; role=skeleton_clip | no | — |
| `rom/67/71.dat` | 59268 | `sqle_motion` | pb; ch=537; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/67/76.dat` | 262050 | `sqle_motion` | pb; ch=343; frames=1624; time=54.1; role=skeleton_clip | yes | TaruM face2 head motion BASE; TaruM face2 head seq |
| `rom/67/77.dat` | 36290 | `sqle_motion` | pb; ch=343; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/67/82.dat` | 535596 | `sqle_motion` | pb; ch=389; frames=1624; time=54.1; role=skeleton_clip | no | — |
| `rom/67/83.dat` | 38696 | `sqle_motion` | pb; ch=389; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/67/88.dat` | 613980 | `sqle_motion` | pb; ch=413; frames=1624; time=54.1; role=skeleton_clip | yes | TaruM face3 head motion BASE; TaruM face3 head seq |
| `rom/67/89.dat` | 56184 | `sqle_motion` | pb; ch=413; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/67/94.dat` | 1022210 | `sqle_motion` | pb; ch=537; frames=1624; time=54.1; role=skeleton_clip | no | — |
| `rom/67/95.dat` | 59268 | `sqle_motion` | pb; ch=537; frames=241; time=7.93334; role=skeleton_clip | no | — |
| `rom/76/28.dat` | 560 | `unknown` | head='effe............' | no | — |
| `rom/97/36.dat` | 14244 | `xistring` | XISTRING........ | no | — |
| `rom/97/38.dat` | 6146 | `xistring` | XISTRING........ | no | — |
| `rom2/ftable2.dat` | 219402 | `file_table` |  | no | — |
| `rom2/vtable2.dat` | 109701 | `file_table` |  | no | — |
| `rom3/ftable3.dat` | 219402 | `file_table` |  | no | — |
| `rom3/vtable3.dat` | 109701 | `file_table` |  | no | — |
| `rom4/ftable4.dat` | 219402 | `file_table` |  | no | — |
| `rom4/vtable4.dat` | 109701 | `file_table` |  | no | — |
| `rom5/ftable5.dat` | 219402 | `file_table` |  | no | — |
| `rom5/vtable5.dat` | 109701 | `file_table` |  | no | — |
| `rom6/ftable6.dat` | 219402 | `file_table` |  | no | — |
| `rom6/vtable6.dat` | 109701 | `file_table` |  | no | — |
| `rom7/ftable7.dat` | 219402 | `file_table` |  | no | — |
| `rom7/vtable7.dat` | 109701 | `file_table` |  | no | — |
| `rom8/ftable8.dat` | 219402 | `file_table` |  | no | — |
| `rom8/vtable8.dat` | 109701 | `file_table` |  | no | — |
| `rom9/ftable9.dat` | 219402 | `file_table` |  | no | — |
| `rom9/vtable9.dat` | 109701 | `file_table` |  | no | — |
| `user/tig.dat` | 24 | `user` |  | no | — |
| `vtable.dat` | 109701 | `file_table` |  | no | — |

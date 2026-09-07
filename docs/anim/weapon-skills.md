# Weapon-skill animations: ID spaces, the two motion banks, and what the DATs say

How the client turns the `animation` number in a weapon-skill action packet
into a per-race motion DAT, why adding that number to the race base is wrong
past 255, and what the retail name/ability tables do and do not tell us.
Worked out from `FFXiMain.dll` (static, three builds), the retail DATs, a
LandSandBoat checkout and the `!injectaction` command; research note
`research/cipher notes.md` (2026-09-06), verified against this install on
2026-09-07.

Tooling: `xi anim ws` (resolver), `xi anim list` / `xi anim export` bulk mode
(`weaponSkill` + `weaponSkillExt` categories), `xi ui layout mnc2-pos --records`
(decoded ability table). Code: `xi.entity.anim.xi_motion_tables`,
`xi.common.xi_menu_records`.

## TL;DR

- A weapon skill involves **three unrelated numbers**: the skill/name id
  (name table block, ability record), the server's animation number for it
  (`weapon_skills.animation` / `mob_skills.mob_anim_id`), and the per-race
  file id that number resolves to. Nothing in the client joins the first two.
- The animation number is resolved through **two banks**, chosen by comparing
  with 256. `xi anim ws 259` shows the right DAT for every race.

  ```text
  animation <  256:  file_id = primary_base[race]  + animation
  animation >= 256:  file_id = extended_base[race] + (animation - 256)
  ```

- Each bank is a **body** block plus **two companion blocks** at fixed strides
  (256 for the primary bank, 16 for the extended). Adding 259 to the primary
  base lands in the first companion block of slot 3: a real DAT of part-2
  (waist) clips with no `main` routine. That is the concrete defect this
  document exists to prevent.
- The same number is **race-dependent beyond the skeleton**: 259 is
  `ROM/204/17` for Hume male, `ROM/226/17` for Hume female, `ROM/204/27` for
  Elvaan male and a `dumm` placeholder for Elvaan female. A slot number alone
  is not a skill name.

## 1. The three ID spaces

| Value | Meaning | Evidence |
| --- | --- | --- |
| 255 | Dimensional Death's skill / name id | `ROM/181/72.DAT` name block 255; `ROM/118/114.DAT` ability record 255 (type 3, weapon skill) |
| 190 | LSB's animation number for skill 255 | `sql/mob_skills.sql`: `(255,190,'dimensional_death',…)` |
| 259 | Animation requested by `!injectaction 3 259` | The command copies its 2nd argument straight into the per-result animation field (12 bits) and hardcodes action id 10. No skill lookup, no 255→259 conversion, no `+4` rule |
| 61454 | Hume male's file id for animation 259 | extended bank, then FTABLE/VTABLE → `ROM/204/17.DAT` |

An injected packet with animation 259 therefore proves what 259 *looks like*
on a given race. It cannot establish a retail join from skill 255 to
animation 259; that join lives only in server data.

LSB sends `mob_skills` rows with id **below 256** as category 3
(`SkillFinish`, the weapon-skill packet), which is why hidden humanoid moves
sit in that part of its monster table. Its `weapon_skills.sql` (209 rows in
the local checkout, animations up to 240) is not the complete set of such
presentations. No LSB row with id < 256 uses an animation ≥ 256 today: the
extended slots are reachable only through injection or custom rows.

## 2. The two banks in FFXiMain.dll

The action handler recognises categories 3, 14 and 15 and passes selector 0,
1 or 2 to the motion loader (caller VA `0x100A939F`, loader `0x100D25F0` in
the audited build). For selector 0 the branch after the loader compares the
animation with `0x100` and picks the table.

Both tables are little-endian `u16` per race, in the same order every other
motion category uses (`RACE_NAMES`). Row 0 duplicates Hume male; that
duplicated pair is the 4-byte hint `xi_motion_tables` scans for, exactly as
it already did for the nine other categories.

| Race | Primary body | Extended body |
| --- | ---: | ---: |
| Hume ♂ | 33227 | 61451 |
| Hume ♀ | 33995 | 61499 |
| Elvaan ♂ | 34763 | 61547 |
| Elvaan ♀ | 35531 | 61595 |
| Tarutaru ♂ | 36299 | 61643 |
| Tarutaru ♀ | 36299 | 61643 |
| Mithra | 37067 | 61691 |
| Galka | 37835 | 61739 |

Hint words: primary `CB 81 CB 81` (33227 twice), extended `0B F0 0B F0`
(61451 twice). The extended region sits just below the primary one. **Offsets
move between builds** (note: body tables at `0x368CC` / `0x36894`; this
install, a third build: `0x368C4` / `0x3688C`), so the tooling locates the
tables by hint and then validates their *shape*, never a fixed offset.

### Body + two companion blocks

Each table region is: nine body rows, a zero word, then nine
`(companion A, companion B)` pairs (the pairs the loader reads 2 bytes apart,
`0x1035F4E0`/`0x1035F4E2` primary, `0x1035F4A8`/`0x1035F4AA` extended).

| Bank | Slots per race | Companion A | Companion B |
| --- | ---: | --- | --- |
| primary (0–255) | 256 | body + 256 | body + 512 |
| extended (256–271) | 16 | body + 16 | body + 32 |

Both companion blocks hold the **part-2 (waist) clips** of the same slot:
slot 1 (Fast Blade) is `ROM/76/30` with `b000 b001 b010 b011 b020 b021`,
companion A of slot 1 is `ROM/76/124` and companion B `ROM/77/80`, each a
12–13 KB DAT with `b002 b012 b022` and no `main`; the extended bank is the
same (`ROM/204/17` → A `ROM/204/18`, B `ROM/204/19`, `d2?2` tracks). It is
the body/waist split the emote `+6` sibling and the battle "skirt" pack
use; which lower-body variant selects A rather than B is not pinned down.
The PS2 `XiSkeletonActor::ReadTechRes` supports the primary/companion
reading but is single-bank; the extension is Windows-era.

`xi anim ws` (no argument) prints the tables it found; the enumeration used by
`xi anim list` / bulk export walks the primary window (768 = 3 × 256) as
`weaponSkill` and the extended window (48 = 3 × 16) as `weaponSkillExt`, so
companion DATs appear under the same category as their body slot. On this
install Hume male's extended bank has eight populated body slots (256–263:
`ROM/199/75`, `199/82`, `200/124`, `204/17`, `238/73`, `238/94`, `239/54`,
`335/69`), each with its two waist companions; slots 264–271 are dummies.

The loader rejects actor race indices of 9 and above. Do not extend these
tables to the child / chocobo rows of the general model-race catalog.

### Hume male 259, resolved

```text
61451 + (259 - 256) = 61454 → ROM/204/17.DAT   185,616 B
    dirs hm_s, eff_, mot_; routines main, mot0, tgt0, cst0;
    skeleton motions d200 d201 d230 d231 d250 d251

33227 + 259         = 33486 → ROM/76/126.DAT    13,488 B   (WRONG: companion A, slot 3)
    dir hm_0; motions b022 b012 b002; no main
```

LSB's Hume male animation 190 is `ROM/147/8.DAT` (121,696 B; `dhm_`, `hm_j`,
`ef_j`; `wz00`/`wz01`; `main`). It is a different asset from 259. The owner's
Hume-male observation that 259 looks like Dimensional Death is visual evidence
for *that race and slot*; it should not be substituted for LSB's 190 across
all races, and the model viewer's base list, which labels `ROM/204/17`
"Warden Of Terror" (an unrelated, commented-out LSB monster skill), is an
old guess with no better provenance. Both labels are unverified as a retail
semantic join.

## 3. What the retail DATs reveal

### `ROM/181/72.DAT` — ability / weapon-skill names

Unmasked `d_msg`, **5,888** fixed 80-byte blocks, `sub[0]` = name (JP twin
`ROM/181/68`). Block *i* names ability record *i*: 1 Combo, 32 Fast Blade,
62 Fimbulvetr, 190 Myrkr, 255 Dimensional Death. Blocks 256, 259, 270… hold
`.` — the extended-bank slots have no names. Registered as `abilities` in
`xi mv database` and readable with `xi_dmsg` / `load_ability_names()`.

### `ROM/118/114.DAT` `comm` — ability records

Type `0x53` chunk `comm` at `0x41770`; 2,816 records of `0x30` bytes from
`+0x10`, **each rotated independently** (bytes `+2`, `+0xB`, `+0xC` plain,
the rest rotated by `[1,7,2,6,3][ |pop(b2)+pop(bC)-pop(bB)| % 5 ]`; the
`mgc_` block uses the same scheme with 100-byte records). Decoded, `+0` is a
sequential `u16` id equal to the record index for all 2,816 rows and `+2` is
the type; **3 = weapon skill** (512 rows). Record 255 is type 3; so is 259.
The client iterates only the first `0x700` records. Full layout and the
decoder: `xi.common.xi_menu_records`, [../dats/ROM_118_114.md](../dats/ROM_118_114.md).

```bash
uv run xi ui layout mnc2-pos "ROM\118\114.DAT" --block comm --records --limit 300
```

Of the 511 non-zero type-3 records, 241 are named and 270 carry the `.`
placeholder; the highest named is 255. That is a naming ceiling, not a
parser or packet width. The named set is the 211 catalogued weapon skills
plus 30 more (Fimbulvetr, Blitz, Disaster, Origin, Diarmuid, Zesho Meppo,
Tachi: Mumei, Sarv, Terminus, Dragon Blow, Maru Kala, Ruthless Stroke,
Imperator, Dagda, Oshala, Netherspikes, Carnal Nightmare, Aegis Schism,
Dancing Chains, Barbed Crescent, Shackled Fists, Foxfire, Grim Halo, the
same five prime names again at 249–253, Vulcan Shot, Dimensional Death).

**No field in the decoded 48 bytes is the animation number.** Every offset
was compared as `u8`/`u16`/`u32` against the 211 known animations: best hit
2/211 (byte) and 1/211 (wider). A stride search over the whole DAT, the
packed DLL and the decompressed `.text` for seven non-linear known pairs
(ids 31, 32, 35, 36, 47, 48, 224) also found nothing. Bounded negatives: the
join could still be indirect, but it is not a plain array anyone has found.

### The effect-directory naming pattern (a generator, not an oracle)

Most melee weapon-skill DATs carry a four-character effect directory such as
`0011`, `0111`, `02g1`: two characters of authored family, an ordinal
`1..9,a..i`, and a final `1` in all eight races (**not** a race digit).

| Prefix | Family | First skill id | | Prefix | Family | First skill id |
| --- | --- | ---: | --- | --- | --- | ---: |
| 00 | Sword | 32 | | 06 | Great axe | 80 |
| 01 | Hand-to-hand | 1 | | 07 | Great sword | 48 |
| 02 | Dagger | 16 | | 08 | Polearm | 112 |
| 03 | Axe | 64 | | 09 | Staff | 176 |
| 04 | Scythe | 96 | | 10 | Katana | 128 |
| 05 | Club | 160 | | 11 | Great katana | 144 |

`first_id + ordinal - 1` gives 180 candidates, 174 agreeing with the
catalogue (1,440/1,440 directories present across the eight races). Examples:
`0011` → 32 Fast Blade (anim 1, `ROM/76/30`); `0111` → 1 Combo (anim 16,
`ROM/76/39`); `02g1` → 31 Rudra's Storm (anim 236, `ROM/257/78`); `05g1` →
175 Exudation (anim 75, `ROM/316/17`).

The six misses matter: sword ordinals 4, 5, 6 are Shining, Seraph and Flat
Blade (ids ordered differently), and `00h1`, `00i1`, `02h1` are Chant du
Cygne, Requiescat and Exenterator, whose ids overflow their family blocks.
Naive arithmetic mislabels them Hard Slash, Power Slash and Fast Blade.
Ranged DATs use `rwb*` / `rwg*`; hidden humanoid DATs use `dhm_` / `ef_j`;
extended-bank DATs mostly carry generic `eff_` / `mot_`. Aliases block a
one-to-one inversion (Fast Blade / Fast Blade II, Final Heaven / Final
Paradise, Knights of Round / Rotund, Tachi: Kaiten / Suikawari share DATs).
Use the pattern to propose and check names, never to assert them for hidden
or extended slots.

## 4. Corpus evidence and its limits

All eight races, primary slots 0–255 and extended 256–271, with the
corrected rule and real chunk framing:

| Measure | Count |
| --- | ---: |
| Race × animation combinations | 2,176 |
| Distinct DAT paths / bytes | 1,715 / 366,525,888 |
| Primary slots: non-placeholder with `main` / `dumm` | 1,967 / 81 |
| Extended slots: non-placeholder with `main` / `dumm` | 64 / 64 |
| Missing paths or malformed chunk streams | 0 |
| Catalogue rows with a non-placeholder `main`, all races | 1,688 / 1,688 |

A `main` routine is structural, not a promise that every slot is a weapon
skill (the primary bank also holds other action presentations), and dummy
DATs contain `main` too, so check the `dumm` directory as well. The 64
extended hits are race × slot combinations, not 64 skills. The 16-slot
extent follows this build's tables; it is not a universal maximum. Keep the
received `u16`, and diagnose anything outside the discovered extents instead
of masking to 8 bits or walking into the neighbouring companion block
(`weapon_skill_slot` raises).

The 1,715 provider files and both metadata DATs were byte-identical across
the two installs in the note; the DLL hashes differed but the tables and
branch logic agreed. This install is a third build (different `ROM/118/114`
hash too) and agrees again.

## 5. Open questions

- The retail join skill id → animation number. Not in `comm`, not a plain
  array in the DLL; the server tables are the only source today.
- What distinguishes companion A from companion B (both waist packs; the
  battle "skirt" split suggests a lower-body gear variant).
- Whether extended-bank skills have VFX in the `4912 + animation` band the
  `effects` list uses (4912 + 259 is past that band's end at 5157; the
  extended DATs carry their own `eff_` directories).
- Names for the 16 extended slots per race; the name table has none.

## 6. Provenance

| Input | SHA-256 |
| --- | --- |
| `FFXiMain.dll` (note, build A) | `6f8844eb7f0380f30a3db2fc3c435e1145f5c450bdd0999133cc75c516ec3c3b` |
| `FFXiMain.dll` (note, build B) | `59b9c813ad46350e9b5681e4540a28463f55a33d4a5c938d62e50da95ed1ea67` |
| `FFXiMain.dll` (this install, 2026-09-07) | `f5ed4c3b6aabef3936ac2c1cf49c2475b3ab9a5b7c20c3c2744bdb938ce7a7f6` |
| `ROM/118/114.DAT` (note) | `cea25d834e122d1d4caa38a044cb4f68a4085129b7732edd6df19a02817d5d16` |
| `ROM/181/72.DAT` | `8cdddf5a38b7393b9fc5e02672a10bca5f1eedec1a15764d18c80be811732e3c` |
| Hume ♂ 190 `ROM/147/8.DAT` | `69d827c59020c5efa0294c1d25d39c45f08131071f211907d541d5b529c115d8` |
| Hume ♂ 259 `ROM/204/17.DAT` | `2fad3c0a03af3b7128dcc2cf98e8cffb695ed7d176a31efaa1fadb8fec870537` |

LSB baseline `bf9352465c9fa540cf879631ef433ee79e91175c`
(`scripts/commands/injectaction.lua`, `lua_base_entity.cpp` L985,
`packets/s2c/0x028_battle2.cpp` L73, `battle_entity.cpp` L2923,
`sql/mob_skills.sql` L286). Static inspection used `xi dll ffximain unpack`
and Capstone; no runtime code was changed by the research itself.

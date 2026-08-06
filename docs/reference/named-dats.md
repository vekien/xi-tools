# Known named DATs (fileId catalog)

A catalogue of FFXI resource DATs that have been **identified** — keyed by **fileId**,
with the resource type and what each contains. Sourced from shining fantasia's curated
list (`thirdparty/shining fantasia/src/common/database.ts`), plus the per-zone formulas
it uses to generate the rest.

> **fileId ≠ path.** These are **fileIds**, not `ROM/x/y.DAT` paths. Resolve a fileId to
> its on-disk path with [`xi ftable lookup`](../ftable/lookup.md) (needs a game
> install). The mapping is the merged FileTable — see [../zone/zones.md](../zone/zones.md).
>
> **Trust.** The fileId→contents labels are shining fantasia's identifications (a
> community DAT tool). The well-known ones (items, spells, zone names, quests/missions)
> are reliable; entries it marks `<Unknown>` are unidentified. Confirm against bytes for
> anything load-bearing.

Resource types: **Dmsg** = `d_msg` string table · **EventMessage** = event/UI string
table · **Item** · **XiString** · **ChunkedResource** (see [../events/dialogue.md](../events/dialogue.md)
for the string formats).

---

## Strings & names (`d_msg`)

| fileId | contents |
|---|---|
| `0xD8A9` | Zone Names |
| `0xD8AA` | Zone Names (Short) |
| `0xD8AB` | Job Names |
| `0xD8AC` | Job Names (Short) |
| `0xD8AF` | Slot Names |
| `0xD972` | Equipment Slot Names |
| `0xD8B0` | Einherjar Chambers |
| `0xD8B2` | Chocobo Names |
| `0xD980` | Server Names |
| `0xD96B` | Heading Names |
| `0xD96C` | Moon Phases |
| `0xD996` | Spell Names · `0xD9B6` Spell Help Text |
| `0xD995` | Ability Names · `0xD9B5` Ability Help Text |
| `0xD981` | Mount Names · `0xD982` Mount Help Text |
| `0xD998` | Titles |
| `0xD999` | Key Items |
| `0xD9B4` | Status Names (with adjectives) |
| `0xD98A` | Monster Family Names |
| `0xD98C` | Augment Attributes |
| `0xD989` | Soulplate Attributes |
| `0xD98D` | Trust Messages |
| `0xD986` | Menu Text — Merit Points |
| `0xD98E` | Menu Text — Job Points · `0xD97A` Job Point Gifts |
| `0xD985` | Blue Mage Spell Help Text |
| `0xD97C` | Emote Help Text |
| `0xD987` | Chat Window Command Help Text |
| `0xD98B` | Moblin Maze Mongers Rune Help Text |
| `0xD97F` | The 15th Anniversary Quiz for the Ages |

### Quests & Missions (`d_msg`)

| fileId | contents |
|---|---|
| `0xD99A` | Quests — San d'Oria |
| `0xD99B` | Quests — Bastok |
| `0xD99C` | Quests — Windurst |
| `0xD99D` | Quests — Jeuno |
| `0xD99E` | Quests — Other Areas |
| `0xD99F` | Quests — Outlands |
| `0xD9A0` | Quests — Treasures of Aht Urhgan |
| `0xD9AA` | Quests — Wings of the Goddess |
| `0xD9A1` | Quests — Abyssea |
| `0xD9BB` | Quests — Seekers of Adoulin |
| `0xD9BC` | Quests — Coalition Assignments |
| `0xD9AC` | Quests — Campaign Ops |
| `0xD9A8` | Quests — Assault |
| `0xD9A3` | Missions — San d'Oria |
| `0xD9A4` | Missions — Bastok |
| `0xD9A5` | Missions — Windurst |
| `0xD9A6` | Missions — Rise of the Zilart |
| `0xD9A7` | Missions — Chains of Promathia |
| `0xD9A9` | Missions — Treasures of Aht Urhgan |
| `0xD9AB` | Missions — Wings of the Goddess |
| `0xD9B7` | Missions — A Crystalline Prophecy |
| `0xD9B8` | Missions — A Moogle Kupo d'Etat |
| `0xD9B9` | Missions — A Shantotto Ascension |
| `0xD9BA` | Missions — Seekers of Adoulin |
| `0xD9BD` | Missions — Rhapsodies of Vana'diel |

---

## Items (`Item`)

| fileId | range |
|---|---|
| `0x49` | Items 0–4095 |
| `0x4A` | Items 4096–8191 |
| `0x4D` | Puppetmaster Automaton — Items 8192–8703 |
| `0xD977` | Items 8704–10239 |
| `0x4C` | Armor — Items 10240–16383 |
| `0x4B` | Weapons — Items 16384–23039 |
| `0xD974` | Armor — Items 23040–28671 |
| `0xD973` | Moblin Maze Mongers — Items 28672–29695 |
| `0xD976` | Monstrosity — Items 29696–30719 |
| `0xD975` | Monstrosity — Items 61440–61951 |
| `0x5B` | Gil — Item 65535 |
| `0x5F` | Items 61432–61439 |
| `0xD978` | Records of Eminence — Objectives (57344–61431) |
| `0xD979` | Records of Eminence — Categories (61952–62975) |
| `0xD8D0` | Items 62976–62995 · `0xD97E` 63008–63023 · `0xD97D` 63024–63263 |

---

## EventMessage tables (UI / system)

| fileId | contents |
|---|---|
| `0x1B6D` | Skill Names |
| `0x1B6F` | Modifier Flags |
| `0x1B71` | Emotes |
| `0x1B73` | Ability Messages |
| `0x1B75` | Status Names |
| `0x1B77` | **System Messages** |
| `0x1B7B` | Ability Names (256+) |
| `0x1B7F` | Unity Messages |

### Weather DATs *(catalogued but unidentified — see [../events/weather.md](../events/weather.md))*

| fileId | contents (per shining fantasia, marked `<Unknown>`) |
|---|---|
| `0x1B78` | Base for weather regions 0–99 |
| `0x1B79` | Region table for regions 0–99 |
| `0x1B7C` | Base for weather regions 100+ |
| `0x1B7D` | Region table for regions 100+ |

---

## Per-zone DATs (formula-based)

shining fantasia generates the per-zone string/entity DATs by adding the zone index to a
base fileId (only entries present in the live FileTable are kept):

| Per zone | base fileId | zone range |
|---|---|---|
| Entity List | `0x1A40 + zone` | 0–255 |
| Entity List | `0x150DB + zone` | 256–554 |
| Entity List | `0x1055F + (zone − 1000)` | 2000+ |
| Event Messages **JP** | `0x17E8 + zone` | 0–255 |
| Event Messages **EN** | `0x1914 + zone` | 0–255 |
| Event Messages **EN** | `0x14E57 + (zone − 256)` | 256–554 |
| Event Messages **EN** | `0xE259 + (zone − 1000)` | 1000–1031 |
| Event Messages **EN** | `0x10B9F + (zone − 2000)` | 2000–2299 |
| Event Messages **DE** | `0xDA39 + zone` | 0–255 |
| Event Messages **FR** | `0xDBDD + zone` | 0–255 |

> These are the **string / entity** side of a zone, addressed by **fileId**. The
> **Model / Dialog / NPC / Event** DATs are addressed by a different per-zone-**id**
> formula — see [../events/README.md](../events/README.md#per-zone-file-ids). Both
> ultimately resolve through the FileTable.

---

## Related

- [../ftable/lookup.md](../ftable/lookup.md) — resolve a fileId → `ROM/x/y.DAT` path.
- [../events/dialogue.md](../events/dialogue.md) — the `d_msg` / EventMessage string formats.
- [../events/weather.md](../events/weather.md) — the weather id table (weather DATs above).
- [../dats.md](../dats.md), [../dat_index.md](../dat_index.md) — broader DAT-location research.
- [../zone/zones.md](../zone/zones.md) — the merged FileTable + per-zone Model/Dialog/NPC/Event paths.

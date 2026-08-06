# Weather IDs → names

The fixed table of FFXI **weather ids** and their in-game names, with the elemental
association. Events set weather by id (opcodes [`0x72`/`0x77`/`0x78`/`0xAE`](opcodes.md)),
and the zone weather system / server use the same enum.

> ⚠️ **Trust note.** The **id → name** mapping below is the **standard FFXI weather
> enum** (the one LandSandBoat's `xi.weather` uses and that BG-wiki documents) — it's
> well-established, but it is **not shipped as a clean enum in this repo or in our
> reference sources**, and we have **not** byte-verified it against a client `d_msg`
> table here (no game install was available). Treat the **names/elements as reliable
> retail knowledge** and the **"events use exactly this id" claim as not-yet-confirmed**
> against real event bytes. See [README.md](README.md#source-trust--three-tiers) and
> "Verification status" below.

---

## The table

20 weather types, paired by element: even id = single strength, odd id = the
intensified ("double") version of the same element.

| id | dec | in-game name | DAT `weat/` tag | element |
|----|-----|--------------|-----------------|---------|
| `0x00` | 0 | None | *(none — not `fine`)* | — |
| `0x01` | 1 | Sunshine | `suny` | — |
| `0x02` | 2 | Clouds | `clod` | — |
| `0x03` | 3 | Fog | `mist` | — |
| `0x04` | 4 | Hot Spell | `dryw` | Fire |
| `0x05` | 5 | Heat Wave | `heat` | Fire (×2) |
| `0x06` | 6 | Rain | `rain` | Water |
| `0x07` | 7 | Squall | `squl` | Water (×2) |
| `0x08` | 8 | Dust Storm | `dust` | Earth |
| `0x09` | 9 | Sandstorm | `sand` | Earth (×2) |
| `0x0A` | 10 | Wind | `wind` | Wind |
| `0x0B` | 11 | Gales | `stom` | Wind (×2) |
| `0x0C` | 12 | Snow | `snow` | Ice |
| `0x0D` | 13 | Blizzards | `bliz` | Ice (×2) |
| `0x0E` | 14 | Thunder | `thdr` | Lightning |
| `0x0F` | 15 | Thunderstorms | `bolt` | Lightning (×2) |
| `0x10` | 16 | Auroras | `aura` | Light |
| `0x11` | 17 | Stellar Glare | `ligt` | Light (×2) |
| `0x12` | 18 | Gloom | `fogd` | Dark |
| `0x13` | 19 | Darkness | `dark` | Dark (×2) |

**Id 0 vs DAT `fine`:** LSB / server enum id `0` is **None** (no weather / no 4CC tag).
Zone DATs often still have a `weat/fine/` resource folder — that is a **separate DAT
tag**, not id 0. Do not map `0 → fine` when bridging server weather to zone folders.

### Mnemonic

- **ids 0–3** are **non-elemental** ambience (None, Sunshine, Clouds, Fog).
- **ids 4–19** are elemental: `element = (id − 4) / 2`, in the standard element order
  **Fire, Water, Earth, Wind, Ice, Lightning, Light, Dark**. The **odd** id of each pair
  is the double-strength variant (e.g. `0x06` Rain → `0x07` Squall).

---

## How weather connects to the rest of the system

- **Events / cutscenes** — the event VM can force weather for a scene:
  [`0x72`](opcodes.md) (load event weather), [`0x77`](opcodes.md) (set a specific time
  **and** weather), [`0x78`](opcodes.md) (re-enable the timer and reset zone weather),
  and [`0xAE`](opcodes.md) (a multi-case handler that touches weather among other things).
  The argument is a weather id — *assumed* to be this enum (see verification status).
- **Server** — on a LandSandBoat-style server the same enum is `xi.weather`; the server
  drives a zone's ambient weather rotation and can override it. LSB stores a 2160-day
  packed table and **rolls** normal/common/rare (50/35/15) — that RNG is LSB policy.
  Retail selection may differ (often modelled as deterministic normal-slot only). A custom
  event that sets weather should still use this id enum (client/server contract).
- **Rendering (the *look* of weather)** — separate from the id. Inside each zone DAT,
  `weat/<tag>/` holds **0x2F environment** records (lighting, fog, procedural sky dome)
  plus unplaced sky meshes/effects. Global file regions `0x1B78`/`0x1B79` (zones 0–99)
  and `0x1B7C`/`0x1B7D` (100+) are catalogue stubs in some toolkits — layout still
  largely unverified here. Fan clients (xiclient/xim lineage) are useful runtime models
  but not retail proofs ([trust note](README.md#source-trust--three-tiers)).
- **Effect weather** — note the *visual-effect* side flags weather generators separately:
  a `0x05` generator's `moreFlags` bit `0x20` = "batched (weather)" — see
  [../fx/effects.md](../fx/effects.md#validated-against-xim-authoritative). That's the
  particle layer (rain drops, snow), distinct from the weather **id** here.

---

## Verification status

| Claim | Confidence | How to confirm |
|---|---|---|
| The 20 id→name→element mapping | **High** (standard retail enum; matches LSB `xi.weather` + BG-wiki) | compare against a LandSandBoat `xi.weather` enum |
| The names match the client's own strings | **Unverified here** (no game install in this env) | parse the weather-name `d_msg`/EventMessage system table from the client (e.g. the System-Messages region shining fantasia lists at `0x1B77`) and diff against this table |
| Event opcodes `0x72`/`0x77` take *this exact id* | **Unverified** | decode a real `0x77`/`0x72` instruction's weather byte in an Event DAT and trigger it in-game |
| Per-zone weather DAT region layout (`0x1B78`+) | **Unverified** (shining fantasia marks them `<Unknown>`) | parse a weather region DAT and correlate ids → lighting params |

**To verify the names with xi** once a game install is present: we already have a
working `d_msg` reader (`parse_dmsg` in `src/xi/zone/xi_list.py`) — point it at the
client's weather/system string table to dump the names in id order and diff against this
doc. (A future `xi event extract` would make this a one-liner.)

---

## Related

- [opcodes.md](opcodes.md) — the event opcodes that set weather (`0x72`/`0x77`/`0x78`/`0xAE`).
- [dialogue.md](dialogue.md) — the `d_msg` string format the weather names live in.
- [../fx/effects.md](../fx/effects.md) — the particle side (weather-batched generators).

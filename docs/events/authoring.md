# Authoring NPC dialogue events (`xi event dialogue new`)

Inject dialogue lines into a zone and synthesize a **new event** that prints them — the
first realized slice of the [authoring prototype](prototype.md). Give it a JSON list of
lines and an NPC; it returns an **event id** you trigger from the server.

> **Status: shipped.** `xi event dialogue new` + `xi event dialogue actors`. Builds
> a brand-new event from scratch (no template), byte-exact. Camera / menus / branching
> are still future (see [Limits & what's next](#limits--whats-next)).

---

## TL;DR

```bash
# lines.json:  ["Welcome, traveler.", "We have the finest wares in Jeuno.", "Come back any time!"]

xi event dialogue actors 245                                   # find the NPC's server id
xi event dialogue new 245 --json lines.json --actor 0x010F5022 # author the event → prints the id
```

```
Actor: 0x010F5022 (Kurou-Morou)
Lines: 3 → message id(s) 11344–11346  (separate boxes)
Event id: 10095
  dialog DAT ROM/25/54.DAT: 1238544 → 1238648 bytes
  event  DAT ROM/21/54.DAT: 632940 → 632968 bytes
Wrote: …/FINAL FANTASY XI/ROM/25/54.DAT
Wrote: …/FINAL FANTASY XI/ROM/21/54.DAT

─ server trigger (paste into the NPC's Lua) ─────
function onTrigger(player, npc)
    player:startCutscene(10095)
    …
```

Then add the printed Lua to that NPC's script (`player:startCutscene(10095)`) and reload the
server. The client reads the edited DATs straight from the install (edits are in place).

---

## What an event actually is (recap)

A dialogue event is **two separate things, wired by an id** (see [format.md](format.md) and
[dialogue.md](dialogue.md)):

```
  Dialog DAT (6420+zone)   the STRINGS — an indexed table of message text
        ▲
        │ references[i] = a message id (= index into the dialog table)
        │
  Event DAT (5820+zone)    the BYTECODE — per-actor "scenes". The print opcode doesn't
                           inline text; it names a references[] slot that holds the id.
```

So "make an NPC say these lines" = **append the strings** to the dialog table (get their
indices), then **emit bytecode** on the NPC's actor block that prints those indices.

---

## The bytecode it emits (verified)

The minimal "NPC says lines" event is tiny. Each line is a `print_msg` + a `wait_dismiss`,
terminated by `end`:

```
1D <selector>   print_msg     → shows dialog[ references[i] ]
23              wait_dismiss  → wait for the player to press Enter
…                             (repeat print+wait per line)
21              end
```

**`print_msg`'s operand is a 2-byte work-selector `0x8000 | refIndex` (little-endian)** — the
`0x8000` high bit flags "this is a `references[]` index", the low 15 bits are the index (see
[the work-selector model](format.md#operand-references--the-references--work-selector-model)).
The **message id is not in the opcode** — `references[refIndex]` holds it, and that id is the
index into the dialog table. The speaker is the current event entity (VM state), not encoded.

> **Verified against retail:** 13,655 real `print_msg` opcodes across four zones decode as
> `0x8000 | index`, and **2,760 of them (17%) use an index > 127** — proof the operand is the
> 2-byte selector, not a single byte. (xi's decoder used to read a signed byte here and
> silently dropped those high-index lines as `→ msg -1`; [fixed](#decoder-fix-as-a-side-effect).)

For three separate boxes the scene is `1D s0 · 23 · 1D s1 · 23 · 1D s2 · 23 · 21`; for one
paged box (`--paged`) it's a single `1D s0 · 23 · 21` whose dialog entry joins the lines with
▼ page-prompts.

---

## The two CLI commands

### `xi event dialogue actors <zone|dat>`

Lists a zone's actors (NPC server ids + names + event counts), most-used first, so you can
pick the `--actor` for `new`.

```
xi event dialogue actors 245
  0x7FFFFFF0   38 event(s)
  0x010F5007   33 event(s)  Aldo
  0x010F5022    9 event(s)  Kurou-Morou
  …
```

### `xi event dialogue new <zone|dat> --json <file> --actor <id> [flags]`

| Argument / flag | Meaning |
|---|---|
| `<zone\|dat>` | Zone id, zone name, **or** an event-DAT path. The matching dialog DAT (`6420+zone`) is resolved automatically. |
| `--json <file>` | A JSON **array of strings** (or `{"lines": [...]}`). The dialogue lines. |
| `--actor <id>` | **Required.** The owning NPC's server entity id (`0x…` hex or decimal). The event is appended to that NPC's actor block (the block is created if the NPC has none). |
| `--paged` | Show all lines in **one** box that pages with ▼, instead of one box per line. |
| `--event-id N` | Force a specific event id (default: the next free id on the actor). |
| `--dry-run` | Print what would change without writing. |

Lines accept the same escapes as [`dialog edit`](../dialog/edit.md): `\n` newline, `\v`
prompt ▼, `\\` literal, and tokens `{player}`, `{npc}`, `{auto:N}` (auto-advance after N
seconds).

---

## The pipeline (JSON → DATs)

```
lines.json + --actor
   │ 1. resolve the zone → event DAT (5820+zone) + dialog DAT (6420+zone)
   ▼
append lines to the dialog table          (xi_author.append_dialog_lines)
   │   separate: one entry per line → one message id each
   │   --paged:  join with ▼ into one entry → one message id
   ▼
synthesize the event                       (xi_author.add_dialogue_event)
   │   • add each message id to the actor's references[]  (reused if already present)
   │   • emit  print_msg<selector> · wait_dismiss  per id, then end
   │   • append the bytecode to the actor's scene; new event offset = old scene length
   │   • allocate a free event id (max real id on the actor + 1, unless --event-id)
   ▼
rebuild both DATs                          (xi_event.build_event_dat / xi_dialog.build_container)
   │   only the edited actor block is re-serialized; every other actor is byte-identical
   ▼
write the DATs back in place (+ .base backup)   — pristine bytes preserved in .base
   ▼
print the event id + a server-side Lua startCutscene stub
```

Code: [`src/xi/event/xi_author.py`](../../src/xi/event/xi_author.py) (the authoring logic),
the CLI in [`src/xi/event/xi_commands.py`](../../src/xi/event/xi_commands.py)
(`dialogue_group`), the dialog codec in
[`src/xi/dialog/xi_dialog.py`](../../src/xi/dialog/xi_dialog.py).

---

## The byte-exact Event-DAT writer (foundation)

Authoring needs a writer that round-trips the [Event DAT](format.md#file-layout) byte-for-byte
so untouched data is never disturbed. In `xi_event.py`:

- **`parse_raw_actors(data)`** → a list of `RawActor` (the block's fields **and** its original
  bytes). The opcode disassembler (`parse_event_dat`) throws raw bytes away — this keeps them.
- **`build_event_dat(actors)`** / **`serialize_actor(a)`** → bytes. Untouched actors are written
  **verbatim**; only an edited block is re-serialized.

`build_event_dat(parse_raw_actors(d)) == d` is **verified byte-identical across 11 zones**, as
is `serialize_actor` for every actor (the edit path).

> **Format detail nailed by the writer:** the actor block's `sceneSize` field is the **unpadded**
> bytecode length; the block is then padded up to a 4-byte boundary by a few trailing bytes
> (observed `0xff`) that the engine ignores (it reads `scene = block[sceneStart : sceneStart +
> sceneSize]`). The whole block stays a multiple of 4 because every header field is, and the
> scene is padded. `RawActor` keeps that pad so an untouched re-serialize is exact.

---

## Decoder fix (as a side effect)

Proving the `print_msg` selector exposed a latent bug: `_disassemble_event` resolved dialogue
operands with `_getworkofs` (a **signed byte**), which only sees indices 0–127 and returned
`-1` for the rest. It now uses `_resolve_work_selector` (the 2-byte form) for every dialogue
opcode (`0x1D`/`0x24`/`0x2B`/`0x48`/`0x49`/`0xB0`). In Lower Jeuno that took resolved dialogue
references from "hundreds of `-1`" to **9 unresolved** — so `xi event cutscene export` and the
editor's Events panel now show the high-index lines too.

---

## Caveats — read before you ship

- **It is NOT idempotent.** Each run *appends* another event and more dialogue (it reads the
  current DAT state and adds to it). Run it twice and you get two events + duplicate lines.
  To redo cleanly, run `xi event dialogue reset` (restores the dialog DAT from its `.base`
  backup; `--full` also resets the zone event DAT).
- **A client edit does nothing alone.** The event only fires when the **server** calls
  `player:startCutscene(<id>)` on that NPC — paste the printed Lua stub and **reload the
  server**. (`startCutscene`, not `startEvent` — it locks the player into CUTSCENE mode; see
  [event_mode_bits.md](event_mode_bits.md).)
  The `--actor` id and the NPC the server triggers must be the same entity.
- **The client must read the edited DAT** — edits are written in place under `FFXI_DIR`
  (the pristine bytes live in the `<dat>.base` backup).
- **One language table.** It edits the dialog DAT xi resolves for the zone (typically the
  NA / English table). A JP client reads a different table.
- **Per-actor limits** (both raised cleanly as errors): `references[]` index ≤ `0x7FFF`, and the
  event's entry offset into the scene ≤ `0xFFFF` (a u16). Normal NPCs are nowhere near either.

---

## Limits & what's next

This authors **plain multi-line dialogue** only. The bytecode VM also does menus, branching,
camera, NPC animation, doors, and scene presentation ([opcodes.md](opcodes.md),
[cutscenes.md](cutscenes.md)). The [prototype](prototype.md) sketches the full JSON→bytecode
compiler (`say` / `menu` / `branch` / `camera` steps); the writer + dialog-append foundations
here are what it builds on. Natural next steps: a `menu` step (`0x24`/`0x25`/`0x40`) and
`branch` on the chosen option (`0x02`/`0x3E`). (The reset for clean re-runs shipped as
`xi event dialogue reset [--full]`.)

---

## Related

- [prototype.md](prototype.md) — the full custom-cutscene authoring design this realizes part of.
- [format.md](format.md) — the Event DAT block layout the writer round-trips.
- [dialogue.md](dialogue.md) — the dialog string table the lines are appended to.
- [opcodes.md](opcodes.md) — the print / wait / end opcodes emitted here.
- [../dialog/edit.md](../dialog/edit.md) — the line escapes/tokens (`\n`, `\v`, `{player}`…).

---

## Menus, branches and the server round-trip (`xi event cutscene compile`, 2026-09-03)

The step compiler now lowers prompts and branching. Byte template: Ru'Lude Gardens
Nomad Moogle event 10196 (decode it with `xi event cutscene export 243 --all-events
--actor 0x010F3075`).

| step | emits | notes |
|---|---|---|
| `{"op":"menu","text":"ask","options":["Yes","No"]}` | `0x24 <msgSel> <cursorSel> <flagsSel>` · `0x25` | the question line + options become ONE dialog entry (`question` `07` **`0B`** `option` `07` `option`: the 0x0B byte after the question marks where the option rows start, as in every retail menu string); the choice (0-based, 254 = Escape) lands in `Work_Zone[0]` |
| `{"op":"branch","on":"menu_result","cases":{"0":"yes","1":"no","cancel":"no"},"default":"no"}` | one `0x02 if <0x1000> <const> kind=1 <target>` per case, `0x01 set_exec` for default | targets are ABSOLUTE scene offsets, backpatched after emission; `"on": {"work": n}` tests `Work_Zone[n]` (server replies land from `Work_Zone[2]`) |
| `{"op":"goto","to":"label"}` | `0x01 set_exec <target>` | any step may carry `"label"` |
| `{"op":"set_result","value":1}` | `0x03 get_store Work_Zone[1] = 1` | `Work_Zone[1]` is what packet 0x05B carries as `EndPara` = the Lua `option` |
| `{"op":"server_update","result":1}` | `get_store` · `43 00` · `43 01` | `onEventUpdate(player, csid, option)`; `43 01` blocks until `player:updateEvent(...)` answers; `"wait": false` skips it |
| `{"op":"end","result":0}` | `get_store` · `0x21` | `onEventFinish(player, csid, option)`; an `end` that is not the last step is rewritten to `set_result` + `goto __end` so the event keeps retail's single `end` |

Plain NPC dialogs (`flags.cinematic = false`) now open with `0x4A look_at event_entity → player`
(retail's opener; `flags.facePlayer = false` skips it) and every line gets the ▼ continue
code (`flags.prompt = false` opts out).

Test: `uv run python tests/test_compile_menu.py` compiles a Yes/No prompt onto the
Nomad Moogle, decodes it back, checks every jump target, the menu string and that a
re-publish leaves both DATs the same size.

Not built: `0x40/0x41` option-enable masks (all options enabled) and `load_zone`.

---

## Inputs, item windows, bit fields and the currency shop (2026-09-03, second pass)

Decoded from Bastok Markets (Isakoth 0x010EB0B1 event 26 + page routine 0x1866..0x1e7b,
Home Points, Voidwatch Purveyor). Register specs accepted everywhere a value or
register is expected: `{"param": n}` (server parameter n = `Work_Zone[2+n]`, i.e.
`startEvent` / `updateEvent` p_n), `{"work": n}`, `{"work1700": n}`, `{"local": n}`,
`"result"` (Work_Zone[1]), `"menu_result"` (Work_Zone[0]), `{"state": name}` (the client's
runtime state selectors 0x7F00-0x7F8B: `player_race`, `player_job`, `player_level`, `event_x`, ...;
the full list is in [typed_opcodes.md](typed_opcodes.md)); a bare integer is a constant.

| step | emits | notes |
|---|---|---|
| `{"op":"item_info","item":4181}` / `{"item":{"param":2}}` / `{"item":"close"}` | `93 <sel>` | item description window; 0 closes it |
| `{"op":"number_input","digits":2,"result":{"work":4}}` | `71 12 <style=1> <digits>` · `71 13 <dst>` | numeric window, result = `atoi(input)`. **Surveyed across all 294 zones** (`xi event survey --op 0x71 --sub 0x12`, 1,954 uses): the second operand is the maximum digit count (2 for "two-digit combination"/"one to twelve", 3 for "up to 999", 4 for "quality 1-1000", 9 for the Mog Garden gil deposit), the first is a style value that is 1 in every retail use. `"mode":"plain"` is the older `71 10 <style>` form (705 uses, "enter the quantity to trade", 8-digit window). Send the value on with `set_result {"from": {"work": 4}}` |
| `{"op":"text_input"}` | `71 00` · `71 01` | text window; the client sends the string in packet 0x060 |
| `{"op":"bits_set","from":{"local":5},"lo":16,"hi":23,"into":"result"}` | `40 lo hi dst src` | `dst.bits[lo..hi] = src << lo`, other bits kept — retail packs `category | qty<<10 | selection<<16` this way |
| `{"op":"bits_get","from":{"param":6},"lo":0,"hi":7,"into":{"local":5}}` | `41 lo hi src dst` | `dst = src.bits[lo..hi] >> lo` — Home Points unpack server bitmasks this way |
| `menu` … `"hidden":[1,3]` or `"hide":{"param":6}`, `"cursor":2` | third/second `0x24` operands | **hide mask**: bit i set → row i is not shown (PS2 `CodeQUERY`: `if ((val2 & 1) == 0) AddItem`, `val2 >>= 1`) |
| `set_result` … `"from":{"work":4}` | `03 <Z1> <reg>` | copy a register instead of a constant |
| `{n}` in any dialog line | `0A nn` | shows server parameter n (Isakoth: `(Sparks: {0})`) |

### `shop` — the retail exchange, cloned

```json
{ "op": "shop",
  "currency": { "name": "valor points", "singular": "valor point", "short": "VP", "server": "valor_point" },
  "balanceParam": 1, "limitParam": 5,
  "categories": [
    { "name": "Consumables.", "items": [ { "id": 4181, "price": 10 }, { "id": 4182, "price": 10 } ] },
    { "name": "Rings.",       "items": [ { "id": 28546, "price": 5000 } ] }
  ] }
```

What it emits, in order: a dispatcher (copies the balance and limit parameters, shows the
category menu `Exchange for what? (Valor points: {0})` with cursor memory, sets the
category register, `1A`-calls the routine, loops until "None"), then the **Isakoth page
routine cloned from the player's own `ROM/21/44.DAT`** with every absolute jump relocated,
every `0x9D` table offset remapped and every `references[]` selector rewritten into the
target actor (`xi.event.xi_shop.relocate_routine`), then the row-register table and one
item + price table per category (10 slots, unused ones empty). The four retail strings
(page, quantity, confirm, Yes/No) are cloned byte-for-byte with the currency words swapped
so the item-name (`01 05 …`), number (`0A nn`) and plural (`7F 92 nn [/s]`) codes survive.
Paging (15 rows), the quantity menu (1/3/12/36 with the unit price multiplied in), the
`0x93` item popup and the `category | qty << 10 | selection << 16` result are all retail
behaviour. Server contract (the compile result prints a Lua stub with the price table):
`startEvent(csid, 0, balance, 0, 0, 0, limit)` and, in `onEventUpdate`, decode `option`,
sell, then `updateEvent(balance, 0, 0, 0, 0, limit)`. The currency is whatever the server
counts: `player:getCurrency('...')`, gil, or a character variable.

Needs `FFXI_DIR` (the CLI passes it); prefers a `.base` copy of the Bastok Markets DATs when
one exists. `tests/test_compile_shop.py` verifies the relocation (every jump inside the clone,
all 52 table reads inside our tables, all selectors valid), the strings, re-publish stability
and that the other actors stay byte-identical. Not tested in game yet.

Decoder fix in the same change: `0xD4` sub 0/2 are 8 bytes (they run the `0x24` helper), so
`xi event cutscene export` now disassembles the sparks page correctly instead of desyncing
after the window-open opcode.

---

## Reading any NPC: `xi event explain`, `xi event survey`, `xi event npc` (2026-09-03)

```bash
xi event explain 243 --list                       # actors in Ru'Lude Gardens (id, event count, name)
xi event explain 235 Isakoth --event 26           # annotated decode of one event
xi event explain "Port San d'Oria" "Bonanza Moogle" -o bonanza.txt
xi event survey --op 0x71 --sub 0x12 -o input.txt # every use of an opcode across all zones
xi event npc list 243                             # entity-name table: ids, gaps, next free id
xi event npc add 243 "Specialization Master" --gap 10 --dry-run
```

`explain` resolves every operand: `references[]` values inline, registers named by role
(`Z[0]=choice`, `Z[1]=option`, `Z[2+n]=param n`), `if` conditions spelled out, jump targets
as `@offset`, `0x9D` tables expanded (`table@0d86 [4181, 4182, 4064, ...]`), dialog text
next to every print/menu, and the unlisted subroutines reached through `0x1A` decoded once.
It ends with a feature summary (menu, server round trip, item window, query window, data
tables, input window, bit fields, moogle menu, map, gesture, ...). One-byte events are
reported as participation markers. Uses the corrected `0xD4` sizes.

`survey` is how the number-window parameters were pinned: 1,954 uses of `71 12` across 294
zones, the second operand tracking the prompt ("two-digit combination" → 2, "quality
1-1000" → 4, Mog Garden gil → 9), the first always 1.

`npc` edits the zone entity-name DAT (file 6720+zone, 32-byte records name[28]+id, sorted):
**the client shows an NPC's name only when its id is listed there** — an id past the last
record spawns with a generic name. `add` picks last id + `--gap` (or `--id`), writes in
place under `FFXI_DIR` with a `.base` backup and prints the id for the server row
(LSB `data/zones/<zone>/npcs.yaml`).

String tokens: `{n}` (number from parameter n) and **`{item:n}`** (the name of the item whose
id is in parameter n — the `01 05 25 82 80|n 80 80` code the Bonanza Moogle uses for its menu
rows, so a plain menu can list live item names without the query window);
`{name:0xKK:n}` for the other special-name kinds seen in retail (0x36 key item, 0x38 zone,
0x84 entity, 0x40 RoE objective — to verify).

Tests: `uv run python tests/test_explain.py`.

### `say_indexed` — one of N strings, chosen by the server (Field Manual pattern)

```json
{ "op": "say_indexed", "index": { "param": 7 },
  "texts": [ "Your primary specialization is Warrior.", "Your primary specialization is Monk.", "..." ] }
```

Emits `03 L[79] <first>` · `07 L[79] <index>` · `48 L[79]` (`2B <speaker> L[79]` with a
`speaker`) · `23`: the message id is computed at run time, exactly how the Fields of Valor
book prints `7872 + regimeId` ("{0} member[/s] of the worm family.") after the server's
`updateEvent`. The texts are appended as a contiguous block (re-publish finds the same
block and reuses it); `{n}` placeholders work inside them.

### `effect`, `load_zone`, and `xi event lint`

| step | emits | notes |
|---|---|---|
| `{"op":"effect","id":244,"from":"self","to":"player","wait":60}` | `73 <idSel> <caster u32> <target u32>` · `1C <wait>` | the spell/ability visual (`CodeMAGICSCHEDULOR`): Nomad Moogle's "strange spell" is effect 244 from the event entity onto the player; the Field Manual plays Reraise/Regen/Protect this way after the server confirms. `from`/`to` = `self`, `player` or a cast id |
| `{"op":"load_zone","zoneId":243}` / `"restore": true` | `34 <sel>` / `35 <sel>` | zone graphics swap for a scene (documented, untested) |

`xi event lint <zone> <actor> [--event N]` runs the pre-flight checks the compiler now applies
before writing: known opcode sizes, jumps inside the event, calls inside the scene, selectors
inside `references[]`, message ids inside the dialog table and decodable, menu strings with the
0x0B marker, a single `end` reached before the next event. `xi event explain` labels opcodes the
decoder only knows as `unk_XX` with their PS2 handler names (`CodeMAGICSCHEDULOR`, `CodeEMOT`, …).

## Key-item exchange, augment preview and the vendor's category menu (2026-09-03, third pass)

Studied on the Tenshodo Treasure Coffer (Lower Jeuno 10099) and the Curio Vendor Moogle (Port Bastok 9601).

String tokens:

| token | bytes | meaning |
|---|---|---|
| `{keyitem:n}` | `01 05 36 82 (80|n) 80 80` | name of the key item whose id is in parameter n (the coffer's "Which key will you use?" rows: one string, ids stored into `Z[2..8]` first) |
| `{rowitem:n}` | `01 05 24 82 (80|n) 80 80` | item name by id in parameter n as the coffer's "Obtain which item?" rows use it (kind 0x24; `{item:n}` is kind 0x25, the Bonanza Moogle's) |
| `{index:n}[a/b/c]` | `0C nn` + literal `[a/b/c]` | the client shows alternative number n: "Select your `{index:8}[first/second]` augment" |

Steps:

| step | bytes | notes |
|---|---|---|
| `{"op":"augment_window","item":{"work":4},"a":{"param":0},"b":{"param":1},"c":{"param":2}}` | `CC 01 item a b c` | item window with three augment words (each packs two augments as 5-bit power + 11-bit id, exactly what LSB's `scenarioArmor` sends with `updateEvent`); `"item":"close"` closes it |
| `{"op":"set_bit","local":1,"bit":24}` / `clear_bit` | `3C dst bit 1` / `3D` | flag bits in a WorkLocal word (the coffer packs its main-menu choice this way before `end`); `bit` may be a register spec |
| `{"op":"store","into":{"param":0},"from":13206}` / `{"into":{"local":4},"from":"menu_result"}` | `03 dst src` | any register = constant or register (the coffer loads the item ids its `{rowitem:n}` rows show) |
| `{"op":"if_equal","a":"menu_result","b":{"local":0},"to":"dup"}` | `02 a b 01 target` | jump when two values match (the coffer's duplicate-augment guard) |

`xi event explain` now names `set_bit`, `clear_bit`, `mul`, `div`, `xor`, `shl`, `mod` and `item_window2` (with its item and augment operands), and resolves constants stored into display registers or listed in `0x9D` tables to key item names (LandSandBoat `scripts/enum/key_item.lua` via `XI_SERVER_DIR`) or item names (client item DATs) whenever the actor's strings show names of that kind, e.g. `get_store Z[2]=param0 1105 (KI Crimson Key)`.

The Curio Vendor's first half needs no new tooling: a `menu` whose `end.result` is the category, and a server script that opens the standard shop window (`player:createShop` / `addShopItem` / `sendMenu(xi.menuType.SHOP)`) with whatever stock rule you like. Its key-item page (10 rows with previous/next, `9D 05` register-table copies) is documented but not cloned.

## Gesture banks per skeleton (2026-09-03)

`0x5B` loads a gesture bank file (`32104 + bank`) onto the entity and plays a tag from it. A survey of every retail `0x5B` joined with LandSandBoat's `npcs.yaml` looks shows bank 60 (the "shared humanoid bank", file 32164) is only ever loaded onto fixed-model humanoid NPCs (models 90..100, 126, 153 = Maat, 848..855, 873, 1423, 1454, 1998). Loaded onto a player-skeleton NPC (`npcLook.type: equipped`) it plays, but the legs break; loaded onto a moogle or beast the model freezes.

The compiler therefore picks the bank from the owner's look: race 1 Hume male -> 80, 2 Hume female -> 10, 3 Elvaan male -> 297, 4 Elvaan female -> 75, 5/6 Tarutaru -> 337, 7 Mithra -> 357, 8 Galka -> 377 (`RACE_GESTURE_BANKS`; every one carries tlk0/tlk1/thk1/thk2/pas0). `flags.animBank` still overrides. Fixed models outside the bank-60 family get no shared gestures (warning); give them their own motion tags (`anim schedule`) instead. Print a bank's real routine inventory with `xi.zone.xi_bridge._gesture_bank_tags(bank)`.

## Line endings after a gesture (2026-09-03)

Retail never snaps an NPC back to idle after a spoken line. Akta (Ru'Lude Gardens 116 / 10068) and Maat do `5B bank ent ent tag` (gesture) -> `1D` print -> `23` wait -> **`53 wait_task ent ent tag`**: the VM waits for the gesture routine to finish and the routine blends back to the stance by itself; the next line loads the next gesture. The compiler used `5E stop_action idl0` there, which cut the routine and popped the model into the idle pose between every line. `say` now emits `53 wait_task` on the gesture it played (own routines on cast speakers keep `6B`, lines without a gesture keep `5E`).

## Gesture pairs, alternation and the effect lead-in (2026-09-03)

Retail's bank-60 sequences (every consecutive `5B` on one entity across the zone DATs) show enter/exit pairs: `thk1 -> thk2` (2593x), `ann0 -> ann1`, `han0 -> han1`, `ika0 -> ika1`, `tlb0 -> tlb1`, `yor0 -> ski0`. The first of a pair holds a pose; loading any other gesture over it snaps the model. The compiler now plays the closer (and waits for it) before the next gesture, at `end`, and before an `effect`. Consecutive talk lines alternate `tlk0 -> tlk1 -> tlk0` like retail (2728x / 172x) unless the line names its own `anim`. `effect` emits a short `1C wait` (`delay`, default 30 frames, 0 to skip) before `73`, retail's most common lead-in to a cast.

## Casting a spell (2026-09-03)

`{"op":"effect","id":266,"cast":"black"}` reproduces Shihu-Danhu (Al Zahbi 103): wind-up motion, wait (`delay`, default 200 frames = retail's most common), `73` effect, release motion, hold (`wait`, default 300). `cast` names a spell family: `black` (cabk / shbk), `white` (cawh / shwh), `blue` (cabl / shbl), `ninjutsu` (canj / shnj), `summon` (casm / shsm), `item` (cait / shit), or any two-letter code the base actions carry (`ca<xx>` chant, `sh<xx>` release; `ss<xx>` is the chained two-stage release, not used by the step); `none` (default) keeps the bare effect with a 30-frame lead-in. Player-skeleton owners fire those schedules from their race's own basic action set with `0x2C` (AltanaView lists them under the PC's Basic actions; Elvaan male = ROM/37/31.DAT); fixed humanoid models (bank-60 family) get gesture bank 342 (`cabk` / `cawh`, release `spef`) through `0x5B`; other looks warn and skip the motions. Defaults: 200 frames of chant, 100 after the release. `0x66` is `0x5B` with its first argument set (`ReadTpcEventMotionRes`): per-race Tpc packages in blocks of ten (Taru 40-49 etc.), used by Shihu-Danhu but not needed for casting.

## `item_list` — the Splintery Chest picker (2026-09-03)

`{"op":"item_list","items":[19327, ...],"lowBits":1,"openAnim":"open","closeAnim":"clos"}` emits Ru'Lude 10133's whole state machine: pages of sixteen `{rowitem:i}` rows with previous / next / cancel rows (mask bits 16/17 hide them on the first / last page, empty slots hidden per row), a subroutine that fills `Z[2..9]` / `Z7[0..7]` from a page-padded `0x9D` table, pick -> `93` item window on `Z7[23]` + `previewText` ("You take the {name:0x23:31} in hand..."), `confirmText` menu (`takeText` / `leaveText`), take -> the event ends with `option = lowBits | index << 2` (LandSandBoat: `index = option >> 2`), leave -> back to the list, cancel -> `0x40000000`. `openAnim` / `closeAnim` fire the owner's own routines around it (chest models carry `open` / `clos`). The `{raw:hex}` string token writes verbatim control bytes (the retail row opener `7F 80 01 01 01 01 20`).

## Query windows, row items and the exdata preview (2026-09-04, Oseem)

Oseem (Norg arcane glyptics) uses the query-window flavour of the menu for rows that describe an item, previews two augment sets side by side, and reads row parameters well past the eight server values. What was added:

| step | bytes | notes |
|---|---|---|
| `{"op":"menu","query":true,"cursor":{"local":5},"hide":{"local":1},"text":...,"options":[...]}` | `D4 02 msg cursor hide` + `25` | same operands as `0x24`; rows given an item with `row_item` show its description while highlighted |
| `{"op":"row_item","row":1,"item":9210}` | `D4 03 row item` | 1-based row; issue one per item row before the query |
| `{"op":"augment_preview","window":0,"item":{"local":41},"a":..,"b":..,"c":..}` | `D4 05 window item a b c` | the preview window with the item's exdata words (kind 02, subkind 03, four u16 augments `id \| value << 11`); `"kind":4` for the D4 04 variant |
| `{"param": n}` for n up to 35 | `Z[2 + n]` | the rows' `{17}` / `{rowname:1}` read these; the server only fills 0..7, the event stores the rest |

Tokens: `{rowname:n}` = `7F 80 01 01 05 23 82 8n 80 80` (item name at the start of a row, id in parameter n), `{plural:n}[a/b]` = `7F 92 nn` (alternative by the COUNT in parameter n, "time{plural:17}[/s]"), `{qtyitem:c:i}` = `01 09 29 ...` ("4 pellucid stones": count in c, item id in i).

## Decompiling retail events (2026-09-04)

`xi event decompile <zone> <actor> --event N -o file.json --check` turns a retail event into this JSON and, with `--check`, recompiles it in bare mode (`flags.cinematic false, facePlayer false`) and compares the two events opcode by opcode with resolved operands. Steps added so retail decompiles without `raw`: `cancel` (42 / 2E), `look_talk` (1E), `turn_wait` (6F / 70), `look` (79), `look_at` (4A), `companion` (29), `action` (2C with named entities), `wait_task` (53), `schedule` (5B / 66), `effect_bare` (73, no injected waits), `input_open` / `input_wait` (71), `bits_mask` (3F, layout only), `table` / `table_read` / `table_write` (9D), `mul`, `shl`, `nop`, `wait_dismiss`, `wait_select`, `server_send` / `server_wait`; `raw` accepts `{sel}` placeholders with a `sels` list; `bits_set` / `bits_get` accept register specs for `lo` / `hi`; entity operands accept hex ids. Nine retail events (Oseem 9505-9509, Magian Moogle 10122-10124, Nomad Moogle 10196) round-trip with zero mismatches at 100% modelled.

## Text tokens: what a client must resolve (for executors of the JSON, 2026-09-04)

Every token reads an EVENT PARAMETER: register Z[2 + n], the eight values the server sends with
startEvent/updateEvent (n = 0..7) or values the event itself stored there with `store into {"param": n}`
(n up to 35; retail's query rows use 17..35). A line is rendered when its `say`/`menu` step runs, with the
parameters as they are at that moment, so the `store` steps just before a `say` are part of the line.

| token | bytes | renders as |
|---|---|---|
| `{n}` | `0A nn` | the number in parameter n |
| `{index:n}[a/b/c]` | `0C nn` + literal brackets | alternative number `param n` of the bracket list (0-based) |
| `{plural:n}[a/b]` | `7F 92 nn` + literal brackets | `a` when parameter n == 1, else `b` (`[that/those]`, `time[/s]`) |
| `{item:n}` | `01 05 25 82 (80+n) 80 80` | the name of the item whose id is in parameter n |
| `{rowitem:n}` / `{name:0x23:n}` / `{rowname:n}` | kinds 0x24 / 0x23, `7F 80 01 01 05 23 ...` in rows | item name by id in parameter n (row forms) |
| `{keyitem:n}` | kind 0x36 | key item name by id in parameter n |
| `{qtyitem:c:i}` | `01 09 29 82 (80+c) 80 80 82 (80+i) 80 80` | "<count> <item>", count from parameter c, item id from parameter i, plural name when count != 1 |
| `{gender}[a/b]` | `7F 85` + literal brackets | `a` for a male player, `b` for a female one ("found {gender}[his/her] way"; the client's token table calls 0x85 chocobo-gender-choice) |
| `{player}` / `{npc}` | `08` / `09` | the player's / the speaker's name |
| `
`, ``, `{auto:n}` | `07`, `7F 31`, `7F 34 nn` | line break, "press enter" prompt, auto-advance after n seconds |
| `{options}` | `0B` | the rows of a menu start here |
| `{noprompt}` | (nothing) | marker at the end of a line: the compiler does NOT append the `7F 31` prompt (retail lines such as Oseem 12588 have none); the decompiler emits it for such lines |
| `{raw:hex}` | verbatim | control bytes not modelled yet |

Item names (singular and plural) come from the item DATs; the client resolves them at render time.

Worked example, Oseem 9506 line `m12580`:
`For just {1} silt, I'll take {plural:3}[that/those] {3} {qtyitem:3:2} off your hands. I currently have {0} in storage!`
is preceded by `store param0 = L31` (stones already stored), `store param1 = L27; mul param1 100` (fee),
`store param2 = 9210` (pellucid stone), `store param3 = L27` (stones traded). With 5 stones traded and 5 stored it
renders "For just 500 silt, I'll take those 5 pellucid stones off your hands. I currently have 5 in storage!".

## What a decompiled event declares (2026-09-04, for JSON executors)

Besides `dialog.lines` and `steps`, `xi event decompile` writes three blocks so an executor can validate and play the event without any DAT lookup:

- `dialog.lines[].segments` and `steps[].optionSegments`: every string pre-tokenised into segments (`text`, `newline`, `prompt`, `number`, `select`, `plural`, `gender`, `item`, `keyitem`, `qtyitem`, `player`, `npc`, `auto`, `options`, `raw`) with the parameter index and the bracket choices already split. `text` stays the human template.
- `template.placeholders`: every distinct placeholder the event's strings expect; `template.variables`: every parameter they read, with `readAs`, `sources` (server value, event constant, event register, event arithmetic) and the constants the event stores there. An executor publishes the segment kinds it supports and refuses an event whose placeholders or sources it cannot satisfy.
- `assets`: the dictionary of everything referenced by id, resolved inline: `items` and `keyItems` (only ids that a token actually reads as such), `entities` (every actor a step names, with the zone's entity name), `motions` (tags), `effects` and `scenes` (ids). Custom content replaces these entries and keeps the keys.
- `steps[].resolved` on a say / menu: the parameters whose value is statically known at that point (constant stores earlier in the block), with names.

## Typed retail opcodes (2026-09-05)

The decompiler no longer emits `raw` steps for Ru'Lude Gardens: every opcode there has a typed form in `xi_typed.TYPED`, listed with its fields in [typed_opcodes.md](typed_opcodes.md). Authored JSON may use the same step names; the compiler validates the form by size against the opcode size table.

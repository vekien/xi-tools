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

# Retail events: decompile, edit, recompile, verify

This is the workflow for taking any retail NPC event or cutscene apart into
`xi.cutscene.v1` JSON, changing it, and writing it back into the zone's DATs, with a
checker that proves the round trip is byte-exact before anything reaches the game.
It builds on [authoring.md](authoring.md) (the JSON step vocabulary and the compiler)
and [opcodes.md](opcodes.md) / [typed_opcodes.md](typed_opcodes.md) (the bytecode).

Everything runs from `xi event ...` with `FFXI_DIR` set, the same as the rest of the CLI.

## 1. Look before you touch: `explain`

```bash
uv run xi event explain 243 Laityn                      # every event of the NPC, annotated
uv run xi event explain 243 0x010F3054 --event 10003    # one event
uv run xi event explain 243 --list                      # the zone's actors: id, name, event ids
```

`explain` is an annotated disassembly: every operand is resolved (dialog ids show their
text, entity ids show their names, scheduler tags are printed, work registers are
labelled). Use it to find the event id you want and to read what retail does.

## 2. Decompile to JSON: `decompile`

```bash
uv run xi event decompile 243 0x010F3054 --event 10003 -o laityn_10003.json --check
```

The JSON has the shape the compiler already accepts (`schema/event_cutscene.json`):
`eventId`, `actor`, `cast`, `dialog.lines` (the lines the event prints, text inline),
and `steps`. Control flow comes back as `if` / `goto` / labels, shared code as `sub`
blocks, menus as `menu` steps with their options, and every fixed-layout opcode as a
named step from the typed table (`set_time`, `music_op`, `camera_control`, ...). There is
no `raw` step left in any retail zone; the typed table covers the whole corpus.

`--check` recompiles the JSON straight away and compares it with the retail bytes.
The report has two verdicts:

* **clean**: opcode for opcode, operand for operand, and every dialog string byte for
  byte, the recompiled event equals retail. Padding after a string terminator is ignored,
  nothing else is.
* **stable**: decompiling our recompiled bytes gives the same JSON again, so
  decode -> encode -> decode is a fixed point.

If either fails, the first difference is printed with both sides.

## 3. Prove a whole zone: `sweep`

```bash
uv run xi event sweep 243 --check --jobs 8
uv run xi event sweep 230 231 232 --check --jobs 8 --summary sweep.tsv
uv run xi event sweep 243 --check --only 0x010F3007:58,0x010F30A6:10046
```

`sweep` runs `decompile` (and with `--check`, the round trip) over every event of a
zone and prints the events that still carry a raw step, the mismatches and the
unstable ones. A big city zone takes a few minutes with eight workers. The last full
run over all 293 zones with event DATs came back 62,172 events, all clean and stable,
zero raw, zero errors. Re-run a zone after any decoder or compiler change; the ledger
from `--summary` is the record.

Events you appended yourself live in the same DATs; pass them with `--skip actor:event`
so the sweep only judges retail's.

## 4. Edit the JSON

Anything the compiler accepts can go in (see [authoring.md](authoring.md) for the
full step list). Things that were exercised in game while this tooling was built:

* changing waits, music (`music`, `music_volume`), time of day and weather (`set_time`,
  `reset_time`), entity speed (`set_speed`);
* adding new dialog lines (`dialog.lines` + `say`), system lines (`narrate`), and a
  second NPC as a named speaker (add it to `cast`, then `say` with `speaker`);
* a `menu` with `branch` and labels, so one event plays different scenes per option;
* reusing retail's own scheduler tasks (`task` with the retail `scene` and `tag`),
  which is how a rewritten cutscene keeps retail's camera moves and fades;
* `effect` visuals between entities.

### The one rule about the actor's event table

A `companion` step (opcode `0x29`, XiEvents *ReqSetWait*; `0x27` and `0x28` are the
non-waiting forms) asks another entity to run one of **its own** events, and it names
that event by its **slot in the entity's event offset table**, not by event id. The
client literally does `StackExecPointer = TagOffset[tagnum]`. Laityn's cutscene 10003
drives her walk, her lines and her camera cues through slots 10 to 26 of her own table.

So the order of an actor's table is load-bearing. The compiler keeps a replaced event in
its original slot and refuses a compile that would move any existing slot
(`test_replace_keeps_event_slot`). If you ever write an actor block by other means,
preserve the slot order. The symptom of getting this wrong is an event that decodes
clean but plays the wrong sub-events in game.

## 5. Compile and install: `cutscene compile`

```bash
uv run xi event cutscene compile laityn_10003.json --dry-run    # event id, opcode count, Lua stub; no write
uv run xi event cutscene compile laityn_10003.json              # writes the zone's event + dialog DATs in place
```

With `eventId` set to a retail id the event replaces retail's copy on that actor (same
slot, new bytes appended to the actor's scene). With `"eventId": "auto"` a new event id
is allocated and appended. Every DAT the compiler touches keeps a pristine `<dat>.base`
next to it; the decompiler and the checker read the `.base` copies by default, so your
edits never contaminate the reference (`decompile --installed` reads the live DAT when
you want to inspect your own event).

To revert, copy the `.base` file back over the DAT (`xi event dialogue reset` does it for
the dialog table).

## 6. Verify in game

Run the event the way the server normally would (talk to the NPC, or use the server's
command for starting an event by id). If a scene decodes clean but plays differently
from retail, diff the **whole actor** against `.base`, not just the event: event ids in
order, offsets, and the reference table. That is where table-order bugs show up.

## Layout

| file | role |
|---|---|
| `src/xi/event/xi_decompile.py` | bytecode -> JSON, the checker (`check_roundtrip`), listing normalisation |
| `src/xi/event/xi_typed.py` | the typed opcode table used by both directions (one entry per opcode / size / sub-opcode form) |
| `src/xi/event/xi_compile.py` | JSON -> bytecode; typed forms come from `xi_typed`; keeps table slots |
| `src/xi/event/xi_explain.py` | annotated disassembly, zone loading, entity and item name tables |
| `src/xi/event/xi_lint.py` | pre-flight checks shared by `lint` and the compiler |
| `src/xi/event/xi_sweep.py` | `xi event sweep` |
| `src/xi/event/xi_shop.py` | the retail exchange-shop pattern as a compiler step |
| `tests/test_decompile.py` | round trips on a fixed set of retail events, the typed-table size self-check, the slot-order test |

Opcode semantics and sizes come from the [XiEvents](https://github.com/atom0s/XiEvents)
notes by atom0s on the client's handlers; the step names are ours. Where a size differs
from those notes it was settled on the retail corpus (`0xC4` is 12 bytes).

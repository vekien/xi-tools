# Gear model ids

How a gear model id becomes a DAT, how high it can go with a stock client, and how custom
ids above retail work: expanded FTABLE/VTABLE plus a boot-time gear patch in `cexidats`.

---

## How a model id reaches a DAT

1. **The look value.** Each equipped slot in a character's look is a `u16`: slot in the top
   nibble, model id in the **low 12 bits**. So no model id can pass **4,095**.
2. **FFXiMain's group tables.** Per race and slot there are 6 groups of `(base_file_id,
   count)` (file offset `0x34C80`, ported as `RACE_TABLES` in `src/xi/gear/xi_core.py`). The
   client walks them in order, subtracting counts, until the model id falls inside one:
   `file_id = base + remainder`. Past the last group the slot renders nothing.
3. **FTABLE/VTABLE.** `FTABLE[file_id]` = `subdir << 7 | index`, `VTABLE[file_id]` = ROM
   number → `ROM<n>/<subdir>/<index>.DAT`. The client reads the `ROM10` pair first (the pivot
   folder's copy when it has one), then the main pair. **The file id ceiling is the size of
   the install's main tables**: a `ROM10` entry past it does not load, however long the
   `ROM10` pair is ([../dats/README.md](../dats/README.md)).

Step 2 is the only limit on the model id itself; step 3 limits which file ids can be used.

---

## Stock client: the retail ceiling

Without the gear patch the group tables end here, for every race:

| slot | highest model id | free ids in all 8 races |
|---|---|---|
| face | 31 | none |
| head | **671** | 439–440, 512, 519–521, 608 |
| body | **671** | 500–506, 512, 608 |
| hands | **671** | 400, 444–445, 500–501, 504–505, 512, 608 |
| legs | **671** | 400, 501–504, 507–508, 512, 608 |
| feet | **671** | 400, 500, 502–503, 505, 512, 608 |
| main, sub | 1,195 | 1,196–1,259 are a skip range (base 0) and cannot hold a file |
| ranged | 255 | |

A **free id** has no file registered for any race, so a custom model can go there with a
stock client: register a DAT per race at the file id the group walk gives (`slot_file_ids`
in `xi_core.py`). Growing FTABLE doesn't change this list; only the group tables decide
which model ids exist. Checked against CatsEyeXI's tables on 2026-09-24. A retail update
could fill any of them.

Everything else up to 671 is a retail model; changing one changes every item that uses it.

---

## Above retail: expanded tables + cexidats

### The tables

`xi ftable expand` grows all 10 FTABLE/VTABLE pairs (main, `ROM2`–`ROM10`) to 423,152
entries. That gives every (race, slot) a **window** of 4,096 file ids from 128,240:

```
file_id = 128,240 + (race × 9 + slot) × 4,096 + model_id
```

| race | index | | slot | index |
|---|---|---|---|---|
| HumeMale | 0 | | face | 0 |
| HumeFemale | 1 | | head | 1 |
| ElvaanMale | 2 | | body | 2 |
| ElvaanFemale | 3 | | hands | 3 |
| TaruMale | 4 | | legs | 4 |
| TaruFemale | 5 | | feet | 5 |
| Mithra | 6 | | main | 6 |
| Galka | 7 | | sub | 7 |
| | | | ranged | 8 |

HumeMale head 2,500 → `128,240 + 1 × 4,096 + 2,500` = **134,836**. Backwards:
`n = file_id − 128,240`, model `n % 4,096`, race `n // 4,096 // 9`, slot `n // 4,096 % 9`.

Expand also copies the entries of retail armour 609–671 into the windows at the same model
ids, because the patch moves the group that held them. Ship the tables from an install that
has been expanded, to both the install and the pivot folder, so every copy carries those
entries. `128,240` and `4,096` come from `MAX_ENTITY_MODELID` and `MAX_GEAR_MODELID`
(`xi_config`); change either and every custom gear file id moves.

### The cexidats patch

At load, before login, `cexidats` rewrites the last group (G5) of head, body, hands, legs,
feet, main and sub for all 8 races: 56 entries.

```
table = signature C81B0000 00010000 5BF70000 30000000 (HumeMale head G0+G1) minus 48
        (or FFXiMain.dll base + 0x35D880)
for race r = 0..7, slot s = 1..7:
    start = 608 for armour, 1196 for main/sub      (retail G0–G4 counts)
    at table + r*432 + s*48 + 40:
        u32 base  = 128240 + (r*9 + s)*4096 + start
        u32 count = 4096 - start                   (3488 armour, 2900 weapons)
```

A model id at or past `start` then lands on `base + (model − start)`, which is exactly the
window formula. `xi dll ffximain gear-patch --dry-run` prints the same 56 rows to check
against.

### Limits

- Custom ids start at 672 for armour and 1,196 for main/sub; use 3,000+ to leave room.
- Face and ranged are not covered: their G5 is empty in retail, so the patch skips them.
- Don't load the legacy `gearpatch` addon (`Ashita/addons/gearpatch`). It writes the old
  cexi layout into the same 56 entries.
- `xi dats build` currently looks for the patch in the `FFXiMain.dll` file
  (`dll_expand_max`), and `xi ftable expand` writes it there. Both still assume the file
  patch.

---

## CatsEyeXI client, 2026-09-24

The main pair, `ROM2`–`ROM9` and `FFXiMain.dll` are all at retail. They were rewritten in
the same second at 20:16:59 with their `.base` contents, which looks like the launcher's
file check. Only `ROM10` is expanded (423,152). The ceiling is therefore file id 109,700,
and gear stops at the retail limits above. The only custom gear registered is head 2,500
for all 8 races (`ROM10/25/1`–`8`). The pivot folder's `ROM10` pair lacks the 609–671
entries.

---

## Checking it yourself

```
xi dll ffximain gear-groups          # the G0–G5 table per race and slot
xi ftable info                       # windows, formula, registered counts
xi ftable lookup --file-id 134836    # file id → DAT
xi gear json HumeMale head           # retail model id → file id → DAT
```

## See also

- [../reference/model-file-ids.md](../reference/model-file-ids.md) — entity and gear file
  id ranges side by side
- [../dll/gear-groups.md](../dll/gear-groups.md), [../dll/gear-patch.md](../dll/gear-patch.md)
- [../ftable/expand.md](../ftable/expand.md)

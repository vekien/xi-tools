# xi ftable range-scan

Scans the base `FTABLE.DAT` and prints every contiguous block of occupied
`file_id`s — a sample DAT path from the middle of each block, its start/end
file_ids, and its size.

Use this to see the overall layout of the retail file_id space: which regions
are in use, how large each block is, and where the gaps are.

---

## Usage

```
uv run xi ftable range-scan [--max-entries N]
```

| Option | Default | Description |
|---|---|---|
| `--max-entries N` | `109701` | How many FTABLE entries to scan (retail size) |

---

## Example output

```
Scanning first 109,701 entries of FTABLE

Found 821 occupied runs

 file_id start  file_id end   count  sample DAT path
----------------------------------------------------------------------
             1           96      96  ROM/97/36.DAT
           ...
         1,300        2,799   1,500  ROM/5/3.DAT       ← monster range 1 (modelid 0–1499)
         ...
        51,795       53,294   1,500  ROM/97/15.DAT      ← monster range 2 (modelid 1500–2999)
         ...
        99,907      100,406     500  ROM/374/0.DAT      ← monster range 3 (modelid 3000–3499)
         ...
       101,739      109,480   7,742  ROM/374/1.DAT      ← monster range 4 (modelid 3500–11241)
```

Everything above `109,480` is empty in a retail FTABLE — that is where custom
ROM10 entries live after running `xi ftable expand`.

---

## What the regions mean

The four large blocks of consecutive monster file_ids correspond to the four
ranges in the monster lookup formula:

| file_id block | modelid range | formula |
|---|---|---|
| 1,300 – 2,799 | 0 – 1,499 | `modelid + 1300` |
| 51,795 – 53,294 | 1,500 – 2,999 | `modelid + 50295` |
| 99,907 – 100,406 | 3,000 – 3,499 | `modelid + 96907` |
| 101,739 – 109,480 | 3,500 – 11,241 | `modelid + 98239` |

This scan was the key tool used during the formula reverse-engineering session
that eventually confirmed the 4-range lookup function at VA `0x100C513D` in
`FFXiMain.dll`.

→ See [reference/model-file-ids.md](../reference/model-file-ids.md) for the
full formula explanation and file_id space map.

→ See [ffximain/ffximain.md](../ffximain/ffximain.md) for how the formula
was confirmed from the decompiled DLL.

---

## Scanning beyond retail size

After running `xi ftable expand`, pass `--max-entries` to see your custom
ROM10 entries included:

```
uv run xi ftable range-scan --max-entries 128240   # entity expand default ceiling
# after `ftable expand` (entity+gear): use `xi ftable json --tables` for live size
```


Note: this scans the base `FTABLE.DAT` only. Custom entity/gear entries are
**dual-written** to the base tables **and** the ROM10 overlay (`FTABLE10.DAT` /
`VTABLE10.DAT`) — not exclusively FTABLE10 — so expanded base scans will show
them when present. To list models across the registry, use
`xi model json` or `xi ftable json --models`.

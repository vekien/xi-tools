# xi model json

Builds a complete list of every registered skeletal entity model entry across
all FTABLE/VTABLE pairs (ROM1–ROM10) and prints `file_id`, `model_id`,
`mob_pools` blob, ROM index, and DAT path for each.

Despite the command name, the 4-range model ID formula covers **all** skeletal
entity types — monsters, named NPCs (e.g. Lion, Zeid), and zone objects
(e.g. festival props) share the same file_id space and DAT format. The
distinction is only at the database/script level, not the file level.

Covers all four modelid ranges from the `FFXiMain.dll` formula — retail and
custom — in one pass.

---

## Usage

```
uv run xi model json [QUERY]
uv run xi model json --free
```

| Option | Description |
|---|---|
| `QUERY` | Optional substring filter over model id, file id, or DAT path |
| `--free` | Show the next free custom model id and occupied custom slots |
| `--output PATH` | Write JSON to a file instead of stdout |

The old `xi entity list --json` command is still available as a hidden compatibility alias.

---

## Example output

```
Loaded ROM1 tables  (109,701 entries)
Loaded ROM2 tables  (109,701 entries)
...
Loaded ROM10 tables  (118,240 entries)

Found 9,967 entity model entries (monsters, NPCs, objects)

 file_id  model_id    rom  dat
----------------------------------------------------------------------
    1300         0  ROM 1  ROM/5/0.DAT
    1301         1  ROM 1  ROM/5/1.DAT
    ...
   109480     11241  ROM 1  ROM/374/12.DAT
```

If you have custom models injected into ROM10, they appear at the end of the
3500+ range block:

```
  113239     15000  ROM10  ROM10/1/0.DAT
  113240     15001  ROM10  ROM10/1/1.DAT
```

---

## JSON output

The `model_id_text` field in both export formats is the `binary(20)` blob ready
to paste into `mob_pools.modelid`:

```json
{
  "file_id": 113239,
  "model_id": 15000,
  "model_id_text": "0x0000983A00000000000000000000000000000000",
  "rom": 10,
  "dat": "ROM10/1/0.DAT"
}
```

Use `--output` when you want a committed or cached copy. Otherwise JSON is printed to stdout.

---

## How it works

The scan applies the confirmed 4-range formula from `FFXiMain.dll`:

| modelid range | formula | file_id region |
|---|---|---|
| 0 – 1,499 | `modelid + 1300` | 1,300 – 2,799 |
| 1,500 – 2,999 | `modelid + 50295` | 51,795 – 53,294 |
| 3,000 – 3,499 | `modelid + 96907` | 99,907 – 100,406 |
| 3,500+ | `modelid + 98239` | 101,739+ |

For each modelid in each range it checks all ROM tables (ROM1 first) and records
the first non-zero hit.

→ See [reference/model-file-ids.md](../reference/model-file-ids.md) for a full
explanation of the formula and file_id space.

---

## Related commands

- **`xi model json --free`** — just the next free custom slot, no full scan
- **`xi dats build`** — register new packaged content through a manifest
- **`xi ftable lookup`** — look up a single file_id or modelid

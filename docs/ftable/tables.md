# xi ftable tables

The raw, per-ROM view of every FTABLE/VTABLE pair on disk: how big each table is,
how many slots are registered, and the on-disk byte sizes. Use it to confirm an
expansion landed, or to see which ROM owns how much.

For the *human* view of the custom id ranges (what's free, recommended ids), use
[`xi ftable info`](info.md) instead.

---

## Usage

```
uv run xi ftable tables          # readable table
uv run xi ftable tables --json   # machine-readable, includes the gear-region split
```

---

## Example

```
ROM   entries  registered  ent_custom  last_fid     FTABLE     VTABLE  files
------------------------------------------------------------------------------
1     423,152      85,596           0   407,439   826.5 KB   413.2 KB  FTABLE.DAT / VTABLE.DAT
2     423,152       3,201           0   407,439   826.5 KB   413.2 KB  ROM2/FTABLE2.DAT / VTABLE2.DAT
...
10    423,152       2,520           0   407,439   826.5 KB   413.2 KB  ROM10/FTABLE10.DAT / VTABLE10.DAT
```

---

## Columns

| Column | Meaning |
|---|---|
| `ROM` | ROM index. |
| `entries` | Allocated slots (file_ids) in this table. |
| `registered` | Slots with a non-zero VTABLE value (i.e. pointing at a DAT). |
| `ent_custom` | Of those, how many are **custom entities** (modelid ≥ 15,000). `0` until you inject. |
| `last_fid` | Highest registered file_id (`-` if empty). |
| `FTABLE` / `VTABLE` | On-disk byte sizes. |
| `files` | The table paths (or "missing"). |

> The gear-region registration count is intentionally **not** shown here (it's
> mostly the retail-armor pointers and reads like "available slots" when it
> isn't). It's available in `--json` as `gear_registered`, and explained in
> context by [`xi ftable info`](info.md).

---

## See also

- [info.md](info.md) — custom id ranges, what's free, recommended starts
- [expand.md](expand.md) — create/grow the tables

# xi ftable lookup

Resolve a single `file_id` or `modelid` against a specific FTABLE/VTABLE pair
and print exactly which DAT file the client would load, whether it exists on
disk, and the full table sizes.

Useful for spot-checking a specific entry after injection, verifying a retail
model maps where you expect, or debugging a lookup failure.

---

## Usage

```
uv run xi ftable lookup --file-id N                 # base FTABLE.DAT (default)
uv run xi ftable lookup --table 10 --file-id N      # ROM10/FTABLE10.DAT
uv run xi ftable lookup <ftable_file> --file-id N   # explicit path (overrides --table)
uv run xi ftable lookup --modelid N
```

With no path, the FTABLE is resolved from `--table` (`1` = base, `N` = `ROM{N}/FTABLE{N}.DAT`)
and read from the output mirror when present. An explicit `FTABLE_FILE` path overrides
`--table`. Either way the companion VTABLE is auto-detected:
`FTABLE.DAT` → `VTABLE.DAT`, `FTABLE10.DAT` → `VTABLE10.DAT`.

| Option | Description |
|---|---|
| `--table N` | ROM table index: `1` = base `FTABLE.DAT` (default), `N` = `ROM{N}/FTABLE{N}.DAT`. Ignored when a path is given. |
| `--file-id N` | Raw file_id to look up |
| `--modelid N` | Entity modelid, converted to its file_id with the client's four modelid bands (see below) |

---

## Examples

### Look up a retail monster by modelid

Tiger skeleton (`modelid 308`, first band: `308 + 1300 = 1608`):

```
uv run xi ftable lookup --modelid 308
```

```
========================================================
  FFXI FTABLE Lookup
========================================================
  modelid      : 308  (0x0134)
  file_id      : 1608  (= 308 + 1300)

  FTABLE value : 0x0283
  VTABLE value : 1

  ROM dir      : ROM
  Subdir       : 5
  File index   : 3

  DAT path     : ROM/5/3.DAT
  Full path    : D:\...\ROM\5\3.DAT
  File exists  : YES

  FTABLE size  : 109,701 entries
  VTABLE size  : 109,701 entries
========================================================
```

### Look up a custom injection in ROM10

```
uv run xi ftable lookup ROM10\FTABLE10.DAT --modelid 15000
```

Uses `file_id = 98239 + 15000 = 113239` and checks FTABLE10/VTABLE10.

### Check an empty slot

```
uv run xi ftable lookup FTABLE.DAT --file-id 113239
```

Returns `EMPTY SLOT (vtable=0)` on a retail (unexpanded) table.

---

## How `--modelid` becomes a file_id

The client maps a modelid to a file_id in four bands, each with its own offset.
`--modelid` follows the same bands (`xi.entity.xi_core.modelid_to_file_id`), so
retail and custom modelids both resolve:

| modelid range | formula |
|---|---|
| 0 – 1499 | `file_id = modelid + 1300` |
| 1500 – 2999 | `file_id = modelid + 50295` |
| 3000 – 3499 | `file_id = modelid + 96907` |
| 3500+ | `file_id = modelid + 98239` (all custom monsters, 15000+) |

→ See [reference/model-file-ids.md](../reference/model-file-ids.md) for the full
4-range formula and why it exists.

---

## Picking the right FTABLE file

| Goal | Use |
|---|---|
| Look up a retail monster or any file_id in the base table | `FTABLE.DAT` |
| Verify a custom injection landed in ROM10 | `ROM10\FTABLE10.DAT` |
| Check which ROM owns an entry | run against each FTABLE — first non-zero hit wins |

If the expanded FTABLE is needed for custom modelids but doesn't exist yet, see
[ftable/expand.md](expand.md).

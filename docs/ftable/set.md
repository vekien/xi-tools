# xi ftable set

Register a single `file_id` → `ROM{N}/<subdir>/<file>.DAT` mapping directly in the
FTABLE/VTABLE. This is the low-level primitive for pointing an **arbitrary** file_id
at a DAT — e.g. a custom mount (`0x019131 + mountId`), a hardcoded model slot, or any
client-side file id outside the entity/gear bands that `xi dats build` manages for you.

Where those commands *derive* the file_id from a modelid in their own band, `set`
takes the file_id (and the exact ROM/subdir/file) as-is.

---

## Usage

```
uv run xi ftable set --file-id N  --rom R --subdir D --file F
uv run xi ftable set --modelid N  --rom R --subdir D --file F
uv run xi ftable set ... --no-base
uv run xi ftable set ... --dry-run
```

| Option | Description |
|---|---|
| `--file-id N` | Raw file_id to register |
| `--modelid N` | Entity modelid — converted via `file_id = modelid + 98239` |
| `--rom R` | ROM **version byte** written to VTABLE: `1` = base `ROM/`, `N` = `ROM{N}/`. Default **10** (the custom namespace). |
| `--subdir D` | Folder number under the ROM (0–511 — the upper 9 bits of the FTABLE entry) |
| `--file F` | File number within the folder (0–127 — the low 7 bits) |
| `--no-base` | Write only the `ROM{N}` overlay; leave the base `FTABLE.DAT`/`VTABLE.DAT` untouched |
| `--dry-run` | Print the plan without writing |

The FTABLE entry value is computed as `(subdir << 7) | file`.

---

## What it writes

By default `set` **dual-writes** the same mapping to **two table pairs**, both with
version byte `N`:

1. **Base** `FTABLE.DAT` / `VTABLE.DAT` — always patched (unless `--no-base`).
2. **Overlay** `ROM{N}/FTABLE{N}.DAT` / `VTABLE{N}.DAT` — the per-ROM expansion table,
   kept in step with the base so the namespace stays self-describing.

`--no-base` writes only the overlay. When `--rom 1`, the overlay *is* the base table, so
only one pair is written.

> **Not “base is master, the client merges.”** The live client is **volume-direct**: the
> VTABLE byte names which ROM volume to open (`1` → `ROM/`, `N` → `ROM{N}/`). Overlay
> entries **shadow** the base rather than OR-merge into one master table. (xim /
> `dump_event.py` still use a combine model — that is not the live client.)
>
> When **XIPivot** is present (`FFXI_PIVOT_DIR`), its tables can **shadow** the base
> install’s copies entirely. After bulk registrations (e.g. `xi dats build`),
> `sync_pivot_from_base()` copies the custom region into any pivot tables so sizes stay
> uniform and new file_ids resolve. A lone `ftable set` only patches the base install
> path — re-sync or expand if the pivot is out of date. See [expand.md](expand.md) and
> [reference/model-file-ids.md](../reference/model-file-ids.md).

---

## The tables only hold a pointer

`set` registers a mapping — it does **not** create or copy the DAT. The target file must
physically exist at `ROM{N}/<subdir>/<file>.DAT` or the client will fail to load the
file_id. `set` prints a `DAT on disk : NO` warning when the target is missing.

If you want the DAT copied *and* registered as part of a reproducible patch, use
[`xi dats`](../dats/README.md) instead.

---

## Example — custom mount slot 50

Mount models live at file_id `0x019131 + mountId`. Slot 50 → `0x19163` (102755):

```
uv run xi ftable set --file-id 102755 --rom 10 --subdir 10 --file 1
```

```
============================================================
  FFXI FTABLE Set
============================================================
  file_id      : 102755  (0x19163)
  -> DAT path  : ROM10/10/1.DAT
  FTABLE value : 0x0501  (subdir=10 << 7 | file=1)
  VTABLE value : 10
  DAT on disk  : YES

  Write overlay: ROM10/FTABLE10.DAT
  Write base   : FTABLE.DAT + VTABLE.DAT  (version byte 10)
============================================================

[+] ROM10/FTABLE10.DAT patched (FTABLE @ 0x322C6, VTABLE @ 0x19163)
[+] FTABLE.DAT + VTABLE.DAT patched (version byte 10)

[*] Done.
```

Always `--dry-run` first to confirm the computed `FTABLE value` and DAT path.

---

## Capacity

`set` refuses to write past the end of a table and tells you to expand first:

```
Error: ROM10/FTABLE10.DAT holds 109,701 entries; file_id 200000 is out of range.
Run "xi ftable expand" to grow the tables first.
```

Retail tables cover file_ids up to ~109,700. The mount band (`0x19131+`) and other
hardcoded slots are already inside that range; only the custom entity/gear bands need
[`ftable expand`](expand.md).

---

## Reverting

`xi ftable set` and [`xi ftable delete`](delete.md) are symmetric — `delete` zeros the
same file_id in **both** the base and `ROM{N}` tables:

```
uv run xi ftable delete --file-id 102755
```

Edits go through the output mirror with a `.base` backup, so `xi ftable reset` can
restore the pristine tables (⚠️ reverts *all* custom registrations).

---

## See also

- [delete.md](delete.md) — unregister (the inverse of `set`)
- [lookup.md](lookup.md) — verify what a file_id resolves to
- [expand.md](expand.md) — grow the tables for the custom entity/gear bands
- [../dats/README.md](../dats/README.md) — reproducible package builds
- [reference/model-file-ids.md](../reference/model-file-ids.md) — table structure and the merge model

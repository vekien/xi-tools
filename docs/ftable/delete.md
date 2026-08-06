# xi ftable delete

Zeros a custom registration in **both** the base `FTABLE.DAT`/`VTABLE.DAT` and the
`ROM10/FTABLE10.DAT`/`VTABLE10.DAT` overlay — the exact inverse of
[`xi ftable set`](set.md). Use it to unregister a custom mount, entity, or any
custom-ROM file_id.

The guard keys on the **version byte** (the target ROM), not the table the entry is
found in: an entry that routes to a retail ROM (1–9) is refused, so stock game files
are never touched. A registration that lives only in the overlay is still cleared.

---

## Usage

```
uv run xi ftable delete --file-id N [N ...]
uv run xi ftable delete --modelid N [N ...]
uv run xi ftable delete --file-id N --dry-run
```

| Option | Description |
|---|---|
| `--file-id N` | One or more raw file_ids to zero |
| `--modelid N` | One or more modelids — converted via `file_id = modelid + 98239` |
| `--dry-run` | Show what would be zeroed without writing anything |

Both `--file-id` and `--modelid` can be repeated or mixed in one call.

---

## Example

```
uv run xi ftable delete --file-id 102755
```

```
==========================================================
  FFXI FTABLE Entry Delete (base + ROM10)
==========================================================

  file_id : 102755
  modelid : 4516  (3500+ range)
  Base     : 0x0501 v10  -> ROM10/10/1.DAT
  ROM10    : 0x0501 v10  -> ROM10/10/1.DAT

  Zeroed  : FTABLE.DAT + VTABLE.DAT  [102755]
  Zeroed  : FTABLE10.DAT + VTABLE10.DAT  [102755]
  Done.
```

A registration written with `--no-base` (overlay only) shows just the `ROM10` line and
clears only that table. A stock retail file_id is refused:

```
Error: file_id 1608 routes to ROM1 (retail), not ROM10. Refusing to clear a non-custom entry.
```

### Dry run first

```
uv run xi ftable delete --modelid 15000 --dry-run
```

Always a good idea before a real delete — confirms which DAT the entry points to
before you wipe it.

---

## What gets zeroed

For each table that holds the registration:

- Two bytes in `FTABLE*.DAT` at `file_id * 2` → `0x0000`
- One byte in `VTABLE*.DAT` at `file_id` → `0x00`

By default that's the base `FTABLE.DAT`/`VTABLE.DAT` **and** the `ROM10` overlay (matching
what [`ftable set`](set.md) writes). Edits go through the output mirror with a `.base`
backup.

The DAT file itself is **not deleted** — only the FTABLE registration is removed.
The client will no longer be able to route to it, but the file remains in ROM10
on disk. Delete it manually if you want to reclaim the slot for a new injection.

---

## After deleting

Run `xi model json --free` to confirm the slot is free and see the updated
slot count:

```
uv run xi model json --free
```

Then add the replacement through `xi dats prepare` + `xi dats build` if you
want a reproducible package (`build --dry-run` to preview).


→ See [../dats/README.md](../dats/README.md) for package builds.
→ See [model/free.md](../model/free.md) for checking free slots.
→ See [reference/model-file-ids.md](../reference/model-file-ids.md) for the
file_id → DAT path mapping.

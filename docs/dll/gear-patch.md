# xi dll ffximain gear-patch

Patch FFXiMain's gear model-group tables so the client accepts **custom
`model_id`s up to the 12-bit hardware limit (4095) per (race, slot)** — far
beyond the retail ceiling. This is the client half of custom-gear support; it
tells the client's group walker where to look. The reading counterpart is
[gear-groups](gear-groups.md).

> Writes the live `FFXiMain.dll`. The first run auto-creates a
> **`FFXiMain.dll.base`** backup next to it — reverting = restore that file.

---

## Usage

```
uv run xi dll ffximain gear-patch [--dll PATH] [--max-model N] [--dry-run]
```

| Option | Description |
|---|---|
| `--dll PATH` | FFXiMain.dll to patch. Default: `FFXI_DIR/FFXiMain.dll`. |
| `--max-model N` | Ceiling `model_id` per (race, slot). **Default 4095** (the 12-bit hardware limit). |
| `--dry-run` | Print the plan without writing. |

```
uv run xi dll ffximain gear-patch                 # patch to model_id 4095
uv run xi dll ffximain gear-patch --dry-run       # preview only
uv run xi dll ffximain gear-patch --max-model 2048  # smaller ceiling
```

Always `--dry-run` first to confirm the plan before it touches the DLL.

---

## What it does

Extends **group G5** for `head`, `body`, `hands`, `legs`, `feet`, `main`, and
`sub` across **all 8 races** so its cumulative range reaches `--max-model`. That
widens the top band each (race, slot) will resolve, letting high custom
`model_id`s fall through to the expanded DAT file-id range instead of being
rejected.

It only moves the client-side ceiling. You still need the **file_ids allocated**
on the DAT side, which is the other half of the pipeline:

```
xi ftable expand gear     # grow FTABLE/VTABLE for the custom gear band
xi gear inject ...        # place the custom model DATs at those file_ids
xi dll ffximain gear-patch # tell the client's walker to look there
```

Group semantics (cumulative walk, `file_id = base_fid + (N - cum_start)`) are
explained in [gear-groups.md](gear-groups.md).

---

## Reverting

Restore the backup the first run created:

```
# overwrite the patched DLL with FFXiMain.dll.base
```

Because `gear-patch` edits the **packed** game DLL in place (not an unpacked
PE), there's no repack step — but keep that `.base` file safe; it's your only
clean copy once you've patched.

---

## See also

- [gear-groups.md](gear-groups.md) — inspect the tables this command rewrites
- [../reference/model-file-ids.md](../reference/model-file-ids.md) — model_id → file_id math and custom ID bands
- [../gear/import.md](../gear/import.md) — inject the custom gear model DATs
- [../ftable/expand.md](../ftable/expand.md) — grow the lookup tables for the custom band

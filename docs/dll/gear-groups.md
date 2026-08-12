# xi dll ffximain gear-groups

Dump FFXiMain's **per-race, per-slot gear model-group tables** — the data the
client uses to turn an equipment `model_id` into a DAT `file_id`. Read-only
inspection; the writing counterpart is [gear-patch](gear-patch.md).

---

## Usage

```
uv run xi dll ffximain gear-groups [--dll PATH] [--race NAME] [--slot NAME] [--json]
```

| Option | Description |
|---|---|
| `--dll PATH` | FFXiMain.dll to inspect. Default: `FFXI_DIR/FFXiMain.dll`. |
| `--race NAME` | Filter to one race: `HumeMale`, `HumeFemale`, `ElvaanMale`, `ElvaanFemale`, `TaruMale`, `TaruFemale`, `Mithra`, `Galka`. |
| `--slot NAME` | Filter to one slot: `face`, `head`, `body`, `hands`, `legs`, `feet`, `main`, `sub`, `ranged`. |
| `--json` | Emit JSON instead of the table (pipe to a file for tooling). |

```
uv run xi dll ffximain gear-groups
uv run xi dll ffximain gear-groups --race HumeFemale --slot body
uv run xi dll ffximain gear-groups --json > groups.json
```

---

## What the groups mean

Each **(race, slot)** pair has **6 groups**. The client walks them
**cumulatively**: a `model_id` `N` lands in the group whose cumulative range
covers it —

```
cum_start <= N <= cum_end   →   file_id = base_fid + (N - cum_start)
```

So the groups partition the `model_id` space into bands, each mapped to a
contiguous `file_id` range in the DATs. Reading them tells you exactly which
DAT band a given piece of gear resolves to, and where the headroom ends — which
is what [gear-patch](gear-patch.md) extends.

For the derivation and the file-id math, see
[../ffximain/ffximain.md](../ffximain/ffximain.md) and
[../reference/model-file-ids.md](../reference/model-file-ids.md).

---

## See also

- [gear-patch.md](gear-patch.md) — raise the model_id ceiling these groups allow
- [../reference/model-file-ids.md](../reference/model-file-ids.md) — FTABLE/VTABLE + model formula
- [../gear/import.md](../gear/import.md) — allocate the file_ids the extended groups point at

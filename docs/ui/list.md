# xi ui tex list

List all DAT files that contain UI textures (lobb / menu / win0 / sel_ format).

Uses the pre-scanned header research (`research/ftable_full_scan.json`) for fast
lookups. Falls back to a live FTABLE scan if the research file is absent.

---

## Usage

```
uv run xi ui tex list [--magic AAAA] [--json]
```

| Option | Description |
|---|---|
| `--magic AAAA` | Filter to a specific magic type (repeatable) |
| `--json` | Write `exports/ui/ui_dats.json` instead of printing |

Without `--magic`, all known UI types are shown:
`lobb`, `menu`, `win0`, `sel_`, `titl`, `mgc_`

---

## Examples

```bash
# list all UI DATs
uv run xi ui tex list

# find only menu and lobb containers (the ones ui tex export/import work with)
uv run xi ui tex list --magic menu --magic lobb

# find all window skin DATs (ROM/0/14-21)
uv run xi ui tex list --magic win0

# export to JSON
uv run xi ui tex list --json
```

---

## Example output

```
 file_id  magic        size  DAT path
----------------------------------------------------------------------
       1   menu   3,753,968  ROM/0/1.DAT
       2   lobb     356,464  ROM/0/2.DAT
      14   win0     204,800  ROM/0/14.DAT
      15   win0     204,800  ROM/0/15.DAT
      16   win0     204,800  ROM/0/16.DAT
       ...
   39541   lobb     192,304  ROM/119/50.DAT
   39542   menu   2,881,872  ROM/119/51.DAT
      ...

35 UI DAT(s) found.
```

---

## Magic types

| Magic | Format | Description |
|---|---|---|
| `menu` | lobb/menu container | Main UI sheets (buttons, gauges, fonts, icons) |
| `lobb` | lobb/menu container | Title/login screen textures |
| `win0` | win0 container | Window skin DATs (ROM/0/14–21) |
| `sel_` | sel_ keyframe | Character-select position/animation DATs |
| `titl` | titl | Title screen DAT |
| `mgc_` | mgc_ container | Magic effect UI icons |

`xi ui tex export` and `xi ui tex import` work with **`menu`** and **`lobb`** format DATs.
The `win0` DATs also work with `ui tex sx`/`si` via the `--all-themes` flag on `si`.

---

## Related commands

- **`xi ui tex export`** — extract DXT textures from a UI DAT as DDS files
- **`xi ui tex import`** — re-import edited DDS/PNG files back into a UI DAT
- **`xi ftable list --header lobb`** — same filter via live FTABLE scan

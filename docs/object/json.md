# xi object json

List every placement record (object) in a zone DAT — name, index, and optionally
the full position / rotation / scale.

---

## Usage

```
uv run xi object json <dat> [--filter TEXT] [--objects-only] [--output PATH]
```

| Argument / Option | Description |
|---|---|
| `<dat>` | Zone DAT or ROM-relative spec (e.g. `ROM/1/41`) |
| `--filter TEXT` | Case-insensitive substring filter on mesh name |
| `--objects-only` | Only include small props auto-tagged as objects |
| `--output PATH` | Write JSON to a file instead of stdout |

---

## Examples

```bash
# list all objects in Lower Jeuno
uv run xi object json ROM/1/41

# filter to just bridge objects
uv run xi object json ROM/1/41 --filter hasi

# small decorative props only
uv run xi object json ROM/1/41 --objects-only
```

---

## Example output

```
  [   0] block03
  [   1] block03
  [   2] b_low01
  [   3] b_low02
  ...
  [ 124] hasi
  [ 125] hasi
  ...

666 placement(s).
```

With `--pos`:

```
  [ 124] hasi                      pos=(-22.0,0.0,-48.5)  rot=(0.00,0.00,0.00)  scale=(1.00,1.00,1.00)
```

---

## Notes

- Blanked records (mesh_id zeroed by `xi object delete`) are omitted.
- Index is the record position in the `0x1C` section — pass it to
  `xi object swap-placement`. (`import-json` / `set-placement` instead match by
  **name**, first occurrence.)
- Names are the raw mesh_id strings, matching the Objects panel in the web editor.

---

## Also see

- **`xi zone json --fx`** — list VFX generators in the same zone
- **`xi object delete`** — blank a placement by name
- **`xi zone import-json`** — batch move/delete placements from the web editor

# xi fx json

Inspect every `0x05` visual effect in a DAT as JSON: effect name, offset/size,
library label, placed mesh or texture, position, and decoded generator params.

---

## Usage

```
uv run xi fx json <dat> [OPTIONS]
```

| Argument / Option | Description |
|---|---|
| `<dat>` | DAT path or ROM spec (e.g. `ROM/1/41`) |
| `--output PATH` | Write JSON to a file instead of stdout |
| `--opcodes` | Also decode each effect's raw opcode sub-sections |

---

## Examples

```bash
# print the effect catalog JSON
uv run xi fx json ROM/1/41

# write the effect catalog JSON
uv run xi fx json ROM/1/41 --output exports/fx/rom_1_41.json

# include the full opcode instruction stream per effect
uv run xi fx json ROM/1/41 --opcodes --output fountain_full.json
```

---

## Example entry

```json
{
  "name": "tki5",
  "offset": "0x115970",
  "size": 464,
  "label": "Water splash",
  "category": "water",
  "verified": true,
  "mesh": "sibj",
  "texture": null,
  "position": [-17.86, -7.2, 5.41],
  "params": {
    "attach": "None",
    "color_rgb": "505050",
    "scale": [0.3, 1.5, 0.3],
    "draw_distance": 15.0,
    "emission_variance": 5,
    "spawn_interval": 39,
    "count": 0,
    "autorun": true
  }
}
```

The top-level JSON includes the DAT path, total `count`, and a `categories`
histogram.

---

## Notes

- Effect labels come from [`src/xi/fx/fx_library.json`](../../src/xi/fx/fx_library.json).
- A missing library match is reported as unidentified; a tentative match keeps `verified: false`.
- `params` are located by opcode tag or fixed header field and validated against xim's parser.
- `--opcodes` adds the full instruction stream decoded by [`src/xi/fx/xi_opcodes.py`](../../src/xi/fx/xi_opcodes.py).

---

## Related commands

- **[`xi fx set`](set.md)** — edit the params this command decodes
- **[`xi fx export`](export.md)** — export one effect's mesh, texture, and JSON
- **[`xi fx delete`](delete.md)** — remove effects by exact name or prefix

# `xi zone edit` — recolour an existing zone in place

Changes the colours, lighting and fog of a zone that already exists, retail or custom
(400+). To make a new recoloured copy of a zone instead, with its own zone id, use
[`xi zone inject NAME --clone ZONE`](inject-legacy.md).

```bash
uv run xi zone edit 244 --hue 180 --lightness -30 --dry-run
uv run xi zone edit 244 --hue 180 --lightness -30
uv run xi zone edit "Lower Jeuno" --texture "suny*:lightness=-70" --skip "clod*"
uv run xi zone edit 115 --range hue_min=60,hue_max=150,hue=-40 --fog-tint "#203040" --fog-end 120
uv run xi zone reset ROM/1/40                   # put zone 244's pristine model back
```

| Argument / option | Default | Meaning |
|---|---|---|
| `ZONE_ID\|NAME\|DAT` | — | The zone: an id (`244`), a name (`"Lower Jeuno"`, a unique part is enough) or its model DAT (`ROM10/1/8.DAT`) |
| `--hue`, `--saturation`, `--lightness` | no change | Hue shift (0–360), saturation and brightness (-100 to 100) |
| `--tint #RRGGBB[AA]`, `--blend` | none, `normal` | A tint colour and how it blends (`normal`, `multiply`, `screen`, `overlay`, `add`) |
| `--hue-min` … `--val-max` | all colours | Only change colours in this hue / saturation / brightness range |
| `--texture PATTERN:KEY=VALUE,…` | — | Different settings for the textures whose name matches PATTERN; `environment:…` for lighting and fog. Repeatable |
| `--skip PATTERN` | — | Leave the matching textures as they are. Repeatable |
| `--range KEY=VALUE,…` | — | A pass limited to a colour range, in place of the top-level colour options. Repeatable |
| `--env-lightness` | `--lightness` | Brightness of the zone's environment lighting |
| `--fog-tint #RRGGBB`, `--fog-end`, `--fog-start` | unchanged | Fog colour and distances (retail zones' far distance is 100–500; lower is denser) |
| `--dry-run` | off | Show which DAT would change; write nothing |

The colour options are the same as [`xi gear edit`](../gear/edit.md)'s, and they work the
same way; that page explains `--texture`, `--skip` and `--range` in full. A texture's
name is what `xi tex list <dat>` prints (`suny    sky01`).

## How it writes

- It edits the zone's model DAT in place. The first edit keeps `<dat>.base`.
- Later edits **layer** on the current bytes, so other zone edits (`import`,
  `import-json`, objects) are kept, and two `--hue 90` runs add up to `--hue 180`.
- `xi zone reset <dat>` restores the pristine model and drops every edit.
- It changes the model only. The zone's event, dialogue and NPC files are untouched.

Retail zones are shared by every player who loads the install, so a recoloured retail
zone looks that way everywhere. Not yet checked in game.

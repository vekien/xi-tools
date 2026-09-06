# `xi zone package` — ship a custom zone with everything it needs

A working custom zone is spread across more places than the `ROM10` DAT people
expect, and every one of them is load-bearing. Ship a subset and the zone breaks in a
way that reads like a client bug. `xi zone package` collects all of it into one archive.

```bash
uv run xi zone package                          # every zone registered in FTABLE10 from id 400 up
uv run xi zone package --zone 403 --zone 404    # just these
uv run xi zone package --dry-run                # list the archive contents, write nothing
uv run xi zone package --out dist\my-zones.zip --server-dir D:\server
```

| Option | Default | Meaning |
|---|---|---|
| `--zone ID` | all registered | Zone id to include (repeatable). Must be registered in `FTABLE10` |
| `--min-id N` | `400` | Lowest zone id to auto-include when `--zone` is not given |
| `--out PATH` | `exports/packages/custom-zones.zip` | Output archive |
| `--server-dir DIR` | `XI_SERVER_DIR` | Server checkout, for `zone.lua` and `scripts/zones/` |
| `--dry-run` | off | Print what would be packaged |

Zones are discovered from `FTABLE10`, not a hard-coded list. A registered zone whose
model DAT is missing (the 400–402 template donors, for instance) is reported as a warning
and skipped.

## What goes in the archive

| Archive path | Source | Why it matters |
|---|---|---|
| `client/Game/FINAL FANTASY XI/ROM10/<sub>/<slot>.DAT` … `<slot+3>.DAT` | `FFXI_DIR` | The zone model plus its event, dialog and NPC companions in the next three slots |
| `client/Game/FINAL FANTASY XI/ROM10/FTABLE10.DAT`, `VTABLE10.DAT` | `FFXI_DIR` | The file-id → DAT registration |
| `client/Ashita/polplugins/DATs/<pack>/ROM10/FTABLE10.DAT`, `VTABLE10.DAT` | every override tree under the client root that carries `ROM10` | Ashita's DAT-override tree **shadows** the game folder. Stale tables there and the client resolves the old layout — the zone is simply absent |
| `server/scripts/commands/zone.lua` | `--server-dir` | The `!zone <id>` row. A zone with no row drops you at `0,0,0`, which is underground for most zones since FFXI's +Y points down |
| `server/scripts/zones/<name>/**` | `--server-dir` + `zone_settings` name lookup | `IDs.lua` and `Zone.lua`; the map server errors on a custom zone without `IDs.lua` |
| `manifest.json` | generated | `{id, name, rom10}` per zone plus install note |
| `README.txt` | generated | The list above, in plain text, for whoever installs it |

Zone names for the `scripts/zones/<name>/` lookup come from the server database
(`zone_settings`). The database is optional — when it is unreachable the Lua folders are
skipped with a note and everything else is still packaged.

**Not included:** `zone_settings` / `zone_weather` rows. Generate those with `xi zone new`
on the target server, or copy the workspace `zone-migration.sql`.

## Installing a package

Copy `client/` over the client root (the folder that contains both `Game/` and
`Ashita/`) and `server/` over the server checkout. Ship the Ashita table copies together
with the game-folder copies — replacing one without the other is the single most common
way a custom zone "disappears".

Related: [zones.md](zones.md) (custom zone ids), [templates.md](templates.md) (`xi zone new`),
[../common_crashes.md](../common_crashes.md) (overlay tables shadowing a registration).

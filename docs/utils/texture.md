# xi utils texture

Standalone DDS/PNG texture conversion helpers.

For the common UI editing workflow, you may not need to call these directly.
`uv run xi ui tex sx` runs export + DDS->PNG, and
`uv run xi ui tex si` runs PNG->DDS + import using
the DAT-derived `exports/ui/...` folder automatically.

---

## Commands

### DDS to PNG

```
uv run xi utils dds2png <INPUT_DDS> <OUTPUT_PNG>
```

Reads the DDS header, reports `DXT1` / `DXT3` / `DXT5` if known, and writes a
PNG without requiring `texconv`.

`INPUT_DDS` and `OUTPUT_PNG` may each be either a file path or a directory path.
If both are directories, all `.dds` files in the input directory are converted to
`.png` files with matching basenames in the output directory.

Example:

```bash
uv run xi utils dds2png exports/ui/50/titlwin.dds exports/ui/50/titlwin.png
uv run xi utils dds2png exports/ui/50 exports/ui/50_png
```

### PNG to DDS

```
uv run xi utils png2dds <INPUT_PNG> <OUTPUT_DDS> [OPTIONS]
```

Uses `texconv` to build a DDS file.

`INPUT_PNG` and `OUTPUT_DDS` may each be either a file path or a directory path.
If both are directories, all `.png` files in the input directory are converted to
`.dds` files with matching basenames in the output directory.

If `--match-source` is omitted and the output `.dds` already exists, `png2dds`
automatically reuses that output DDS as the format source. This is the intended
workflow for in-place rebuilds inside an extracted texture folder.

If no output DDS exists yet, you must pass either `--format` or `--match-source`.

| Option | Description |
|---|---|
| `--format auto|dxt1|dxt3|dxt5` | Pick DDS compression explicitly or let the tool choose from alpha usage |
| `--alpha-mode auto|opaque|cutout|sharp|smooth` | Steer auto format selection |
| `--match-source FILE` | Reuse the original DDS compression format from another DDS; in directory mode this may also be a directory of matching `.dds` files |
| `--mipmaps N` | Pass mip count through to `texconv` |
| `--srgb` | Pass `-srgb` to `texconv` |
| `--texconv PATH` | Path to `texconv.exe` (defaults to `TEXCONV_PATH` in `config.py`) |

Examples:

```bash
uv run xi utils png2dds edited.png rebuilt.dds --format dxt3
uv run xi utils png2dds edited.png rebuilt.dds --match-source original.dds
uv run xi utils png2dds exports/ui/1 exports/ui/1
uv run xi utils png2dds exports/ui/50_png exports/ui/50_dds --match-source exports/ui/50
```

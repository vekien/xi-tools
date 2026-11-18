# `xi ui items icon export` / `import` — an item's icon as a PNG

Every item record carries its own 32x32 icon. `export` writes it to a PNG (or BMP);
`import` writes a PNG (or BMP) back into the record.

```bash
uv run xi ui items icon export 16535 -o 16535.png      # an indexed PNG with the icon's palette
uv run xi ui items icon export 16535 --bmp -o 16535.bmp
uv run xi ui items icon import 16535 16535.png --dry-run
uv run xi ui items icon import 16535 16535.png
```

| Option | Command | Default | Meaning |
|---|---|---|---|
| `--output`, `-o PATH` | export | `item_<id>.png` (`.bmp`) in the current folder | Where the file goes |
| `--bmp` | export | off | Write an 8-bit BMP instead of a PNG: the file a `database` edit's `icon.file` takes |
| `--tag TEXT` | import | the PNG's own tag, else the record's current tag | The icon's 16-character tag |
| `--dry-run` | import | off | Report what would be written; write nothing |

`import` edits the item DAT in place and keeps `<dat>.base` the first time, like every
other in-place edit. It writes the English DAT, the one the item id resolves to.

## Round trips

- **An unedited export comes back byte for byte.** The PNG is an indexed image holding
  the record's palette, alpha and pixel indices as they are, with the tag in an
  `xi.icon.tag` text chunk. Verified on all 34,816 icons of a legacy client's item DATs
  (`ROM/118/106`–`110`, `ROM/286/73`, `ROM/301/115`, `ROM/217/21`, `ROM/288/80`,
  `ROM/0/4`): PNG and BMP both.
- **An edited PNG** (any mode, any size) is resized to 32x32 and reduced to 256
  colours. When it has 256 colours or fewer it is exact (pixel for pixel); above that
  Pillow's fast octree quantizer picks the palette, deterministically for one Pillow
  version.

## The format

Measured on those 34,816 icons: every one is 2,105 bytes, after the record's u32 size at
`0x280`.

| Bytes | What |
|---|---|
| 1 | `0x91` |
| 16 | Tag: a kind (`item    `, `armor   `, `weapon  `, `icon    `, `recepi  `, `etc     `) then a name, space-padded |
| 40 | `BITMAPINFOHEADER`: 40, 32 x 32, 1 plane, 8 bits, `BI_RGB`, `biSizeImage` 0, no resolution, `biClrUsed` 0, `biClrImportant` 32 |
| 1,024 | 256 palette entries, BGRA, alpha 0..128 (128 = opaque) |
| 1,024 | Palette indices, bottom row first |

- `biClrImportant` is 32 on every retail icon. An icon with another value is drawn wrong
  the first time it appears in an inventory list (seen by CatsEyeXI).
- A PNG's alpha is twice the record's, capped at 255; import halves it rounding up, which
  gives back every value 0..128 exactly.
- 5,394 icons, all tagged `icon`, have alpha 0 on every palette entry and still show in
  game. Export treats that as "no alpha": the PNG is opaque and carries
  `xi.icon.alpha = none`, and import writes the zeros back.
- The retail (September 2026, `0x1400`) record moved nothing below the icon; the icon
  fits either stride (2,427 / 4,475 bytes of room). The format above was measured on a
  legacy client only.

A `database` action takes the same PNGs (`icon.png`) and can recolour an icon or draw
an HQ border on it (`icon.adjust`): [database/README.md](../database/README.md#icon-adjustments).

The encoding follows Loxley's LPS (`lps/icons.py`), ported into xi-tools with his
permission.

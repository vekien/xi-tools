# xi ui tex import

Imports edited `.dds` textures from an extracted UI folder back into an FFXI UI
container DAT (`lobb` / `menu` format).

---

## Usage

```
uv run xi ui tex import <DAT_FILE> <TEXTURE_DIR> [OPTIONS]
```

Shortcut (registered as `si` only — there is no `simple-import` long name):

```
uv run xi ui tex si <DAT_FILE> [OPTIONS]
```

`si` derives the working folder automatically from the DAT path, rebuilds any
edited `.png` files in that folder back into `.dds`, and then imports the
resulting DDS files into the DAT.

Example:

- `ROM/0/1.DAT -> exports/ui/0/1`
- `ROM/119/50.DAT -> exports/ui/119/50`

| Option | Description |
|---|---|
| `--output-dat PATH` | Write to a new DAT instead of overwriting `DAT_FILE` |
| `--hd` | Keep PNGs larger than the expected size at their own resolution and scale their sprite rects to match |
| `--hd-only NAMES` | Comma-separated textures to apply `--hd` to; implies `--hd` |
| `--no-resize` | Import the textures but leave sprite source rects alone |
| `--repair-rects` | Rebuild every sprite rect from a reference. For a DAT left inconsistent by an earlier edit |
| `--reference DAT` | With `--repair-rects`, the pristine DAT to read from. Defaults to the built-in sheet, then `<dat>.base` |

`--format` and `--all-themes` are **only** on `xi ui tex si`, not on `import`.

| `si`-only option | Description |
|---|---|
| `--format auto\|dxt1\|dxt3\|dxt5` | DDS compression; `auto` preserves the extracted DDS format. **`dxt5` renders as flat grey in the client — see [export.md](export.md#when-each-is-used)** |
| `--all-themes` | Window skins only (`ROM/0/14..21`): apply this DAT's edited PNGs to every skin and import each (see below) |

---

## Examples

```bash
# overwrite the original DAT using edited DDS files from the export folder
uv run xi ui tex import ROM/119/50.DAT exports/ui/50

# write to a different DAT path
uv run xi ui tex import ROM/119/51.DAT exports/ui/51 --output-dat ROM/119/51_mod.DAT

# simplified PNG->DDS + import flow
uv run xi ui tex si ROM/0/1.DAT
uv run xi ui tex si ROM/119/50.DAT --output-dat ROM/119/50_mod.DAT
```

If you used `ui tex sx`, the matching import step is usually just:

```bash
uv run xi ui tex si ROM/0/1.DAT
```

That rebuilds edited PNG files in `exports/ui/0/1` back into DDS and imports them
into `ROM/0/1.DAT`.

## Window skins — `--all-themes` (`si` only)

The 8 window-skin DATs `ROM/0/14`–`ROM/0/21` share the same four texture names
(`newtex`, `hfr1`, `corner`, `vfr1`) — only the colours differ. Edit one skin and
push it to all of them in a single command:

```bash
uv run xi ui tex sx "ROM\0\21.DAT"          # extract one skin
# edit exports/ui/0/21/*.png
uv run xi ui tex si "ROM\0\21.DAT" --all-themes
```

For each skin `14..21`, it extracts that DAT's current DDS (so `auto` format
matching has a reference), copies your edited PNGs in **by name** (correct despite
each DAT storing the textures in a different order), then converts and imports —
overwriting all 8 DATs. The source skin imports from its own folder.

`--all-themes` only works on the `ROM/0/14..21` set; using it elsewhere errors out.

---

## How matching works

`ui tex import` looks for `.dds` files using the same filenames produced by
`ui tex export`.

- matching files are imported
- missing files are skipped and their DAT entries are left unchanged
- if no matching `.dds` files are found, the command errors

---

## Validation

Each replacement `.dds` must:

- be a classic DDS file with `DXT1`, `DXT3`, or `DXT5` FourCC
- have a compressed payload size consistent with its format and dimensions

If a file fails validation, the import stops with an error.

---

## Format changes resize chunks

Each UI texture sits in its own `0x20` chunk: a 16-byte section header, the 57-byte
entry header, the 12-byte `xTXD` header, then the pixels, padded to a 16-byte
boundary. The section header's 19-bit size field is how the client walks from one
chunk to the next (see [xi_section.py](../../src/xi/common/xi_section.py)).

Switching a texture between DXT1 (4 bpp) and DXT3/DXT5 (8 bpp) halves or doubles
that payload, so `replace_texture` splices the chunk to its new length and rewrites
the size field. Leaving it stale would desynchronise the chunk walk for every chunk
after that point — a DAT that still parses fine offline and corrupts or crashes in
the client.

Two consequences:

- **Entries are patched back-to-front.** A resize shifts everything after it, so
  both `import` and `si` iterate in reverse; a `txd_offset` is only used before
  anything ahead of it has moved.
- **The chunk header is verified first.** If the 16 bytes before an entry don't
  decode as a type-`0x20` section of exactly the expected size, the resize is
  refused rather than guessed at.

Same-size replacements (the common case, and everything `--format auto` does) never
touch the chunk header.

---

## Sprite source rects (resizing)

Enlarging a texture is not enough on its own. The sprite mapping lives in the
container's `0x31` chunk (`lobb`), as records introduced by a 4-byte marker, an
8-byte parent tag and an 8-byte texture name. A 41-byte payload decodes as:

```
[dest quad: 4 x,y u16 screen points][src_w][src_h][src_x][src_y]
```

Corners are **inclusive**, so a whole 256x256 texture reads as `src 255x255 @ (0,0)`.
The destination quad is in screen pixels and sets the on-screen size; the source rect
selects which part of the texture fills it.

That is why a bigger texture alone changes nothing useful: the source rect still names
the old extent, so the client samples that sub-region and the sprite renders **cropped
to its top-left corner**, not sharper.

Verified on `ROM/119/50` (2026-08-21): a 1024x1024 marker texture in the `20logo` slot
rendered as its top-left quarter. Patching the two records flanking the `20logo` name
from `255x255` to `1023x1023` made the full texture render at the same on-screen size —
the client downsamples the larger source into the unchanged destination quad, which is
the supersampling that buys sharpness.

### Textures are fitted to the size the game expects

The size of a UI texture is fixed by the client's screen layout, not by whatever PNG is
in the export folder. `titlwin` is 1024x1024 because that is what its sprite records
address; feeding a 2048 or a 512 PNG does not change that.

So import resamples on the way in:

```
rebuilt titlwin.png -> titlwin.dds [DXT3; ...; fit 2048x2048 -> 1024x1024]
rebuilt ex1us.png   -> ex1us.dds   [DXT3; ...; fit 512x512 -> 256x256]
```

The target comes from the reference sheet, falling back to the size the DAT already
holds — **never** from the PNG.

### Going past vanilla resolution — `--hd`

Fitting down caps quality at vanilla, and vanilla is not where the ceiling should be:
these sprites are drawn **magnified**. Each expansion banner is a 240x48 region of
`ex1us` filling roughly 375x74 real pixels, so the source is upscaled before it ever
reaches the screen.

`--hd` keeps a larger PNG at its own resolution and scales that texture's sprite rects
to match, turning the upscale into a downsample:

```bash
uv run xi ui tex si ROM/119/50.DAT --hd
uv run xi ui tex si ROM/119/50.DAT --hd-only ex1us,ex2us
```

```
hd: ex1us 256x256 -> 512x512 (chunk 65632 -> 262240)
layout: ex1us src 240x48@(0,0)   -> 480x96@(0,0)
layout: ex1us src 240x48@(0,48)  -> 480x96@(0,96)
```

`--hd-only NAMES` restricts it to specific textures and implies `--hd`. Everything not
selected takes the normal fit-to-canonical path.

This was the single biggest quality change of anything tried on these textures.

### Why not a palettized texture

UI containers also support a palettized format (`0xB1`, see
[xi_palette.py](../../src/xi/ui/xi_palette.py)) — 8-bit indices into a 256-entry RGBA
palette, with 8-bit alpha per entry instead of DXT3's 4-bit. On paper it wins: measured
on a real expansion-logo texture, 33.9 dB against 30.9 dB for DXT3 at the same size,
with exact alpha.

It renders correctly, and it still lost. 255 palette entries shared across five banners
band visibly on smooth gradients, where DXT3 picks different colours for every 4x4 block
and so has thousands across the image. On a logo with soft gradients the gap ran the
other way by 25 dB (53.5 DXT3 against 28.6 palettized).

**Resolution was the win; the encoder was not.** `--hd` writes DXT. The palettized
reader is kept because retail DATs contain around 2100 such textures — `ROM/0/0` has one
in a UI container — and a tool that cannot see them would silently skip them on import.

### Record header

A record header is `01 00 <type> <subtype>`, followed by an 8-byte parent tag and an
8-byte texture name; the payload runs to the next header. `type`/`subtype` are small
integers -- `(1,1)`, `(2,1)`, `(2,0)` and `(2,2)` all occur in `ROM/119/50`.

An earlier revision hardcoded the header as the literal `01 00 01 01`. That matched
**666 of 1230** records and silently merged the rest into oversized payloads, which is
how one expansion banner's rect ended up buried inside a 166-byte lump labelled
`gauge`. The fixup then found a false-positive rect in that lump and scaled it, visibly
corrupting the banner column. Match on the two-byte magic and validate the tags instead
of assuming a fixed header.

Payloads carry a variable prefix: 41-byte records put the quad at +0, 42-byte at +1,
verified across all 1230 records. Length is checked before any fit test, because a fit
test cannot locate a rect that is already wrong -- a `480x96` rect left on a texture
shrunk back to 256 fits nothing, which would make the record invisible.

### Repairing a DAT that is already inconsistent — `--repair-rects`

The delta mechanism handles every normal edit, but it cannot fix a DAT that arrived with
rects already out of step (enlarged by another tool, or by an earlier version of this
one). There is no delta to work from: the texture and its rects are both simply wrong.

`--repair-rects` rebuilds every rect from a reference — the built-in sheet
(`src/xi/ui/data/layout_reference.json`), a `--reference DAT`, or `<dat>.base`. Sprites
pair with the reference by **destination quad**, which is screen geometry and so does not
move when a texture is resized; pairing them in order breaks as soon as a DAT adds or
removes records (CatsEyeXI's `119/50` carries 37 `titlwin` sprites where retail has 35).

This is a recovery tool, not part of the normal flow. Its reference is only as good as
the DAT it was generated from — see `xi ui gen-sheet`, and point that at a genuinely
pristine install.

### Atlases scale too

### Atlases are not rescaled

An atlas is no harder than a single sprite: `ex1us` stacks five 240x48 expansion banners
in one texture, and doubling the texture doubles all five rects and their y offsets.
`titlwin` carries 37 sprites and all 37 move together.

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
| `--no-resize` | Import the textures but leave sprite source rects alone (the reference sheet is not consulted) |
| `--reference DAT` | Pristine DAT to read original sprite rects from. Defaults to the built-in sheet, then `<dat>.base` |

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

### Record header

A record header is `01 00 <type> <subtype>`, followed by an 8-byte parent tag and an
8-byte texture name; the payload runs to the next header. `type`/`subtype` are small
integers -- `(1,1)`, `(2,1)`, `(2,0)` and `(2,2)` all occur in `ROM/119/50`.

An earlier revision hardcoded the header as the literal `01 00 01 01`. That matched
**666 of 1230** records and silently merged the rest into oversized payloads, which is
how one expansion banner's rect ended up buried inside a 166-byte lump labelled
`gauge`. The fixup then found a false-positive rect in that lump and scaled it,
visibly corrupting the banner column. Match on the two-byte magic and validate the
tags instead of assuming a fixed header.

Payloads carry a variable prefix: 41-byte records put the quad at +0, 42-byte ones at
+1. Rather than trust the length, each candidate offset is validated by checking that
the source rect it yields fits inside the texture.

### Dimension changes are accepted by default

A `.dds`/`.png` whose pixel size differs from the DAT entry is imported as-is: the entry
header, the chunk's section size and the sprite source rects all move with it. There is
no flag to opt in, because with the geometry sheet in place a resize is an ordinary
edit rather than a risky one.

`--no-resize` imports the textures but skips the rect pass entirely. A texture whose size
changed then keeps pointing at its old sub-region and renders cropped — useful only when
you intend to hand-edit the mapping yourself.

### Where the original geometry comes from

Rects are recomputed from a pristine record of the sprite's original geometry, scaled by
each texture's current-vs-original size. That record is resolved in this order:

1. `--reference DAT` — a pristine copy of the same DAT
2. **the built-in sheet** — `src/xi/ui/data/layout_reference.json`
3. `<dat>.base`

The **built-in sheet** is the default and covers `ROM/119/50`, `ROM/119/51`, `ROM/0/1`,
`ROM/0/2` and `ROM/280/15`. It stores each texture's original pixel size and every
sprite's original source rect, generated from pristine retail DATs, plus hand-entered
geometry for sprites no retail DAT ships (CatsEyeXI's `20logo`: a 256x256 texture drawn
by two whole-texture rects).

It is preferred over a reference DAT on disk because it needs no second file, cannot
itself have been mangled by an earlier edit, and still works when the only copy of a DAT
the user owns is already wrong. Sprite geometry is fixed by the client's screen layout,
so these values do not drift between client versions.

Regenerate it against a game install with `xi ui gen-sheet`:

```bash
uv run xi ui gen-sheet ROM/119/50.DAT ROM/119/51.DAT ROM/280/15.DAT
```

Point it at **pristine** DATs — a retail install, not an override that has already been
edited, or the sheet records the mistake as truth. Entries merge by texture name, so
hand-added geometry for a sprite no retail DAT ships survives a regeneration (`--replace`
drops it). The sheet is keyed by ROM path (`ROM/119/50`), derived from the DAT's own path, so a DAT outside a
`ROM/<n>/<m>` tree falls through to the next source.

### Repairing a DAT that is already wrong

Because the geometry comes from the sheet rather than from the live bytes, a DAT whose
rects were overwritten by another tool is corrected, not compounded. Verified by
corrupting all seven `ex1us` and `20logo` rects in a 512px DAT to `7x7@(0,0)` and
re-importing with no reference file present:

```
layout: using built-in sheet [ROM/119/50]
layout: 20logo src 7x7@(0,0) -> 511x511@(0,0)
layout: ex1us  src 7x7@(0,0) -> 480x96@(0,0)
layout: ex1us  src 7x7@(0,0) -> 480x96@(0,96)
layout: ex1us  src 7x7@(0,0) -> 480x96@(0,192)
layout: ex1us  src 7x7@(0,0) -> 480x96@(0,288)
layout: ex1us  src 7x7@(0,0) -> 480x96@(0,384)
```

### The no-reference fallback, and its size floor

With no reference at all, only whole-texture rects can be repaired -- they are the one
kind recognisable on their own, being a square power-of-two-minus-one extent anchored at
(0,0), matching a square power-of-two texture.

That shape test carries a **64px floor**. Without it a `7x7` or `15x15` atlas cell at
(0,0) satisfies the test, and the fallback rewrites it to the full texture size,
destroying the sprite -- observed while testing the corruption case above. 64 sits below
every whole-texture rect seen in these DATs and above every atlas cell.

### Deriving from the reference makes it idempotent

Because every rect is recomputed from the pristine value rather than scaled in place,
running an import twice changes nothing. Scaling in place would double `ex1us` to
960x192 on the second run.

Whole-texture extents are remapped rather than multiplied: `255` on a 256px texture
becomes `511` on a 512px one, not `255 x 2 = 510`. Atlas coordinates scale
proportionally.

### What a reference cannot do

The client stores no table of expected sprite sizes -- it draws whatever rect the record
holds. So once a rect has been overwritten there is nothing in the DAT, and nothing in
`FFXiMain.dll`, that knows what it used to be. Recovery has to come from a pristine
copy. This is why the reference is a real file rather than something inferred.

### Atlases are not rescaled

A sheet like `titlwin` (40 sub-rects) or `ex1us` (3) carries sprite coordinates in
texture pixels. Those are left alone — doubling such a sheet requires editing each
sprite's coordinates, which this cannot infer. Only single whole-texture sprites like
`20logo` are handled automatically.

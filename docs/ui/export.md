# xi ui tex export

Extracts DXT1 / DXT3 / DXT5 textures from FFXI UI container DAT files
(`lobb` / `menu` format) and writes them as standard `.dds` files.

---

## Usage

```
uv run xi ui tex export <DAT_FILE> [OPTIONS]
```

Shortcuts (registered as `sx` / `si` only — there is no `simple-extract` /
`simple-import` long name):

```
uv run xi ui tex sx <DAT_FILE>
uv run xi ui tex si <DAT_FILE>
```

`sx` derives the export folder automatically from the DAT path, extracts the
`.dds` files there, and then converts all extracted DDS files to PNG in the same
folder.

Example:

- `ROM/0/1.DAT -> exports/ui/0/1`
- `ROM/119/50.DAT -> exports/ui/119/50`

| Option | Description |
|---|---|
| `--output-dir DIR` | Directory to write `.dds` files (default: `exports/ui/<stem>/`) |
| `--json` | Also write a `manifest.json` listing all extracted textures |
| `--list` | Print texture list without extracting files |
| `--raw-alpha` (`sx`) | Write FFXI's raw half-scale alpha instead of brightening it for editing — see [below](#alpha-is-stored-at-half-scale) |

---

## Examples

```bash
# list textures (no extraction)
uv run xi ui tex export ROM/119/50.DAT --list
uv run xi ui tex export ROM/119/51.DAT --list

# extract all textures to exports/ui/50/
uv run xi ui tex export ROM/119/50.DAT

# extract with JSON manifest
uv run xi ui tex export ROM/119/51.DAT --json

# extract to a custom directory
uv run xi ui tex export ROM/0/2.DAT --output-dir exports/ui/lobby

# simplified extract + DDS->PNG flow
uv run xi ui tex sx ROM/0/1.DAT
uv run xi ui tex sx ROM/119/50.DAT
```

Paths are relative to the FFXI directory (`FFXI_DIR` in config).

If you plan to edit textures as PNGs, the shortcut is the simplest flow:

```bash
uv run xi ui tex sx ROM/0/1.DAT
```

That produces a working folder like `exports/ui/0/1` containing both the original
`.dds` files and converted `.png` files.

---

## Updating the Title Screen

A complete export → edit → import round-trip for the login / title window
background. The art is `titlwin` (1024×1024), the panel rendered behind the title
screen, stored in `ROM/119/50.DAT` (US / English client).

```bash
# 1. export — writes DDS + PNG into exports/ui/119/50
uv run xi ui tex sx ROM/119/50.DAT

# 2. edit exports/ui/119/50/titlwin.png in Photoshop, re-save as PNG

# 3. import — rebuilds PNG -> DDS and writes it back into the DAT
uv run xi ui tex si ROM/119/50.DAT
```

Notes:

- **Edit the `.png`, not the `.dds`.** `si` re-encodes to DXT3 — the format
  `titlwin` needs for clean alpha edges. If you must save a DDS straight from
  Photoshop, use DXT3 or the alpha breaks.
- **Colour is passed through unchanged.** An editor re-saves the PNG with `sRGB`
  and `gAMA` chunks; texconv reads those, sees the target DXT format is linear,
  and de-gammas — which made re-imported textures come back visibly darker. xi
  now always passes `-srgb` so both sides are tagged sRGB and the conversions
  cancel. Nothing to configure; `--gamma-convert` on `xi utils png2dds` opts
  back into texconv's stock behaviour if you ever need it.
- **Resizing is automatic.** Import a bigger or smaller PNG and the sprite's source
  rect is rescaled with it — see
  [import.md](import.md#sprite-source-rects-resizing).
- **Where it writes:** by default `si` writes the patched DAT back in place
  under `FFXI_DIR` (the pristine bytes are kept in a `<dat>.base` backup).
  Pass `--output-dat PATH` to write elsewhere.
- **Other locales:** edit the matching variant instead — JP `ROM/91/16.DAT`,
  German `ROM/176/74.DAT`, French `ROM/178/13.DAT` (each has the same `titlwin`
  1024×1024 DXT3 background).
- **Other elements** in the same DAT — the expansion logos (`ex1us`, `ex2us`,
  `ex5us`) and the rest — export alongside `titlwin` and edit the same way. See
  the [inventory below](#rom11950dat--title--login-screen).

### CatsEyeXI client — editing override DATs

CatsEyeXI loads modified DATs from its Ashita DAT-override root:

```
<FFXI_PIVOT_DIR>
```

The `catseyexi` folder mirrors the game's `ROM\…` tree, so the title screen lives
at `…\catseyexi\ROM\119\50.DAT`.

To edit an override DAT, point `FFXI_DIR` at the override root — the override DAT
is then both the read source and the write target (in place, `.base` backup kept),
so the standard `sx` / `si` flow operates entirely on the pivot:

```cmd
:: cmd / cmder
set FFXI_DIR=<FFXI_PIVOT_DIR>
```
```powershell
# PowerShell
$env:FFXI_DIR = "<FFXI_PIVOT_DIR>"
```
```bash
uv run xi ui tex sx ROM/119/50.DAT      # reads the override DAT -> exports/ui/119/50
# edit exports/ui/119/50/titlwin.png
uv run xi ui tex si ROM/119/50.DAT      # writes back into the override DAT, in place
```

Notes:

- **cmd vs PowerShell.** cmder defaults to **cmd**, where it's `set VAR=value` with
  **no quotes** and no space around `=` — `set VAR="…"` makes the quotes part of the
  path. PowerShell uses `$env:VAR = "…"`. Either way the variable lasts only for
  that window; use `setx FFXI_DIR "…"` (quotes are fine here) to persist it to new
  shells.
- **Backup.** `si` snapshots the DAT to `<dat>.base` before its first overwrite, so
  a bad PNG can always be undone by restoring that backup.
- **Not a full ROM tree.** The override root only holds the DATs CatsEyeXI ships, so
  only those paths resolve under this `FFXI_DIR`.

---

## Example output

```
Found 9 texture(s) in 50.DAT

  #  name              size   fmt             txd_offset
---  ------------ ---------  --------------  ----------
  0  chmkfnt       256x256   DXT1+alpha      0x000069
  1  titlwin      1024x1024  DXT3            0x0080c9
  2  abxy360        32x32    DXT3            0x108129
  3  otp            64x64    DXT3            0x108589
  4  ex1us         256x256   DXT3            0x1095e9
  5  ex2us         256x256   DXT3            0x119649
  6  b1n           128x128   DXT1+alpha      0x1296a9
  7  ex5us         256x64    DXT3            0x12b709
  8  wardrb         32x32    DXT3            0x12f769

  wrote exports/ui/50/titlwin.dds
  ...

Extracted 9 textures to exports/ui/50/
```

---

## Known DATs

| file_id | DAT | Contents |
|---|---|---|
| 39541 | `ROM/119/50.DAT` | Title/login screen textures — `titlwin` (1024×1024), expansion logos, font |
| 39542 | `ROM/119/51.DAT` | Main UI sheet — buttons, gauges, icons, font, keyboard overlay (28 textures) |
| — | `ROM/0/2.DAT` | Lobby DAT — `xilogo` (PlayOnline/FFXI logo), JP expansion logos, lobby font |
| 39551 | `ROM/280/15.DAT` | Menu icons — job icons, status effects, ability icons (242 textures) |

---

## Alpha is stored at half scale

FFXI stores UI texture alpha at **half scale**: `0x80` (128) means fully opaque, not
`0xFF`. A raw export therefore opens at roughly 50% opacity and is unpleasant to edit.

DXT3's alpha is 4-bit, so it can only encode multiples of 17 and cannot hold 128
exactly. The encoder dithers between `119` (7x17) and `136` (8x17) in equal measure to
average 127.5 — that 119/136 pair is the fingerprint of a half-scale texture:

```
ex1us   alpha values: 0 (7936px), 119 (28800px), 136 (28800px)
```

`ui tex sx` now brightens alpha to full range on export and restores it on import, so
the PNGs look in an editor the way they look in game. `--raw-alpha` opts out.

### Why the factor is per-texture, not a flat x2

The zone and mesh exporters use a flat `--alpha-scale 2.0` (see
[zone/export.md](../zone/export.md#texture-opacity---alpha-scale)). That is wrong for UI
DATs, because a single DAT mixes conventions:

| Texture | Peak alpha | Convention |
|---|--:|---|
| `ex1us`, `ex2us`, `ex5us`, `otp`, `wardrb` | 136 | half scale |
| `abxy360` | 204 | partial |
| `titlwin`, `20logo`, `chmkfnt`, `b1n` | 255 | full scale |

A flat x2 would clamp the already-opaque ones to 255, and the x0.5 inverse would bring
them back as **128** — silently making `20logo` and `titlwin` half transparent.

So the factor is `255 / peak_alpha` per texture, which never clamps and is therefore
exactly invertible. Each factor is written to `alpha-scale.json` in the export folder;
`si` reads it back and divides by the same value. Textures already at 255 get factor
1.0 and are not touched.

Verified round-trip on `ROM/119/50`: `ex1us` returns as `0 / 119 / 136` with identical
pixel counts, and `20logo`'s 16-step alpha gradient is unchanged.

**If the sidecar is missing** (an export made before this change, or a hand-assembled
folder) no scaling is applied on import — the PNGs are taken as-is. Re-run `sx` to
regenerate both the PNGs and the sidecar.

---

## DXT compression formats

FFXI uses three variants of DXT block compression. All store RGB colour the same
way (two RGB565 anchor colours + 2-bit index per pixel in each 4×4 block). They
differ only in how alpha is handled:

| Format | Magic | Bits/px | Alpha | Block size | Used for |
|---|---|---|---|---|---|
| **DXT1** | `1TXD` | 4 | 1-bit punch-through (optional) | 8 bytes | Opaque tiles, hard-cutout UI elements |
| **DXT3** | `3TXD` | 8 | 4-bit explicit per-pixel (0–15 levels) | 16 bytes | Logos, title window, job/status icons |
| **DXT5** | `5TXD` | 8 | Interpolated between 2 anchor values (0–255) | 16 bytes | Smooth alpha gradients (supported, unused by retail) |

The TXD magic byte encodes the format: `1TXD` → DXT1, `3TXD` → DXT3, `5TXD` → DXT5.

### When each is used

**DXT1** appears in `ROM/119/51.DAT` for simple UI elements — gauges, buttons,
tile frames — where alpha is either fully opaque or hard-cutout.

**DXT3** is used everywhere a sharp, precise alpha edge is needed: the
`titlwin` (1024×1024) login window background, the `xilogo` logo, all 242 icons
in `ROM/280/15.DAT`, and every texture in `ROM/0/2.DAT`.  DXT3's 4-bit explicit
alpha gives clean edges without gradient bleeding.

**DXT5** would be preferred for soft alpha (smoke, glow, fur), but **the client does
not render it — do not use it.** Tested 2026-08-20: every texture in `ROM/119/50.DAT`
re-encoded to `5TXD` (chunk sizes correctly resized, chunk walk verified contiguous)
rendered as flat grey on the title screen. Reverted.

This confirms the assertion in
[entity/mesh/xi_import.py](../../src/xi/entity/mesh/xi_import.py) that the game reads
only DXT1 and DXT3, and extends it from the entity path to the UI path.

No retail asset uses DXT5 either. A scan of the whole shipped ROM tree
(`ROM` … `ROM10`, ~10 GB) turned up four raw `5TXD` byte hits, all coincidences
inside compressed payloads — no `0xa1` entry marker, garbage dimensions. Zero real
DXT5 textures ship in the game.

`FFXiMain.dll` does carry format-name tables — `1TXD 2TXD 3TXD 4TXD 5TXD` at
`0x285dc` (twice) and `DXT1 DXT2 DXT3 DXT4 DXT5` at `0x29080`, next to the
`CYyTex` / `CYyTexBase` / `CYyTexMng` class names. **These are not proof of support**;
they include `DXT2` and `DXT4`, which the format never uses, so they are almost
certainly a name lookup for diagnostics rather than a decode dispatch. The grey-screen
test above overrides them.

Untested hypothesis for *why* it fails: the decoder may select on the `pixel_format`
u16 at entry+31 (`4` = DXT1+alpha, `8` = opaque, `32` = seen on `titlwin`/`abxy360`)
rather than on the `xTXD` magic. `replace_texture` preserves that field, so the engine
would have kept decoding as DXT1/DXT3 while the bytes were DXT5. Nobody has traced the
real dispatch site.

### Changing format on import

`xi ui tex si --format dxt5` re-encodes every texture in the DAT — it works
mechanically but produces a DAT the client cannot display, per above. DXT1 → DXT3
doubles a texture's payload (4 bpp → 8 bpp), which resizes its `0x20` chunk; see
[import.md](import.md#format-changes-resize-chunks).

### Pitch and size formulas

For any DXT format:

```
bytes_per_block = 8   (DXT1)  or  16  (DXT3 / DXT5)

pitch     = ceil(width  / 4) * bytes_per_block   # bytes per block row
data_size = ceil(height / 4) * pitch             # total compressed bytes
```

Equivalently, given the `xTXD` header fields:

```
width  = pitch / bytes_per_block * 4
height = data_size / pitch * 4
```

Both the entry header (explicit width/height fields) and the `xTXD` header
(pitch + data_size) encode the same dimensions — they always agree for all
known textures.

---

## Texture inventories

### ROM/119/50.DAT — title / login screen

| name | size | fmt | description |
|---|---|---|---|
| `chmkfnt` | 256×256 | DXT1+alpha | Font / glyph sheet |
| `titlwin` | 1024×1024 | DXT3 | **Title / login window background** |
| `abxy360` | 32×32 | DXT3 | Xbox 360 button icons |
| `otp` | 64×64 | DXT3 | One-time password UI element |
| `ex1us` | 256×256 | DXT3 | Rise of the Zilart logo (US) |
| `ex2us` | 256×256 | DXT3 | Chains of Promathia logo (US) |
| `b1n` | 128×128 | DXT1+alpha | B1 panel texture |
| `ex5us` | 256×64 | DXT3 | Wings of the Goddess logo (US) |
| `wardrb` | 32×32 | DXT3 | Wardrobe icon |

Other `lobb` DATs with the same title-window container pattern:

| DAT | Locale / role | Notable texture |
|---|---|---|
| `ROM/91/16.DAT` | JP title/login variant | `titlwin` 1024×1024 DXT3 |
| `ROM/119/50.DAT` | US/English title/login variant | `titlwin` 1024×1024 DXT3 |
| `ROM/176/74.DAT` | German title/login variant | `titlwin` 1024×1024 DXT3 |
| `ROM/178/13.DAT` | French title/login variant | `titlwin` 1024×1024 DXT3 |

`ROM/0/16.DAT` is not the same kind of background. It is one of the eight `win0` window skins and
contains only the four tiled frame textures `newtex`, `corner`, `hfr1`, and `vfr1`.

### ROM/0/2.DAT — lobby (JP locale)

| name | size | fmt | description |
|---|---|---|---|
| `xilogo` | 256×256 | DXT3 | PlayOnline / FFXI logo |
| `chmkfnt` | 256×256 | DXT1+alpha | Font / glyph sheet |
| `otp` | 64×64 | DXT3 | One-time password UI element |
| `ex1jp` | 256×256 | DXT3 | Rise of the Zilart logo (JP) |
| `ex2jp` | 256×256 | DXT3 | Chains of Promathia logo (JP) |
| `ex5jp` | 256×64 | DXT3 | Wings of the Goddess logo (JP) |
| `lbfontp` | 128×128 | DXT3 | Lobby font sheet |

### ROM/119/51.DAT — main UI sheet

| name | size | fmt | description |
|---|---|---|---|
| `buttonto` | 64×64 | DXT1+alpha | Button / UI element |
| `gauge` | 64×64 | DXT1 | Gauge bar |
| `colorbal` | 64×64 | DXT1 | Color balance / palette |
| `msgicon` | 64×64 | DXT1+alpha | Message icons |
| `keytop` | 256×256 | DXT1+alpha | Keyboard key cap art |
| `feppal` | 64×64 | DXT1 | FEP palette |
| `marker` | 64×64 | DXT1 | Map / UI marker |
| `cicon` | 64×64 | DXT1 | Controller icon |
| `hnf999` | 64×64 | DXT1 | Unknown |
| `dg_font` | 128×128 | DXT1+alpha | Digit/glyph font sheet |
| `yubi` | 32×32 | DXT1+alpha | Finger cursor |
| `itemslot` | 32×32 | DXT1+alpha | Item slot frame |
| `xlbutton` | 32×32 | DXT1 | XL button |
| `scre` | 32×32 | DXT1 | Screen/create element |
| `bcre` | 32×32 | DXT1 | Button create element |
| `wcre` | 32×32 | DXT1 | Window create element |
| `mesicon` | 64×64 | DXT1+alpha | Message icon (alt) |
| `warning` | 32×32 | DXT1+alpha | Warning icon |
| `netmeter` | 32×32 | DXT1+alpha | Network meter |
| `slant` | 32×32 | DXT1+alpha | Slant/diagonal element |
| `photobut` | 64×64 | DXT1+alpha | Photo button |
| `itemgenr` | 32×32 | DXT1 | Item genre icon |
| `jcre` | 32×32 | DXT1 | J create element |
| `scancirc` | 64×64 | DXT1+alpha | Scan circle / loading indicator |
| `c2icon` | 32×32 | DXT1 | C2 icon |
| `mcre` | 32×32 | DXT1 | M create element |
| `b1n` | 128×128 | DXT1+alpha | B1 panel texture |
| `keytophd` | 512×512 | DXT1+alpha | Full keyboard overlay (HD) |

---

## File format

### Container format (`lobb` / `menu`)

UI DAT files are multi-texture containers. Each texture is a named entry: a
57-byte header followed by an `xTXD` block.

```
Entry header (57 bytes, ends immediately before xTXD magic):
  +0   1 byte   0xa1 type marker
  +1   8 bytes  parent name (space-padded ASCII)
  +9   8 bytes  texture name (space-padded ASCII)  <- output filename
  +17  4 bytes  header_size = 40  (LE uint32, always 0x28)
  +21  4 bytes  width             (LE uint32)
  +25  4 bytes  height            (LE uint32)
  +29  2 bytes  mip_count         (LE uint16, usually 1)
  +31  2 bytes  pixel_format      (LE uint16: 4 = DXT1+alpha, 8 = opaque)
  +33  20 bytes padding (zeros)
  +53  4 bytes  extra = 0x10      (LE uint32)

xTXD block:
  +0   4 bytes  magic: "1TXD" / "3TXD" / "5TXD"
  +4   4 bytes  data_size  (LE uint32) — total compressed pixel bytes
  +8   4 bytes  pitch      (LE uint32) — bytes per row of DXT blocks
  +12  N bytes  pixel data (N = data_size)
```

### Opening .dds files

Any DDS-capable tool can open the output files:

- **Paint.NET** — built-in DDS support
- **GIMP** — DDS plugin
- **Photoshop** — NVIDIA DDS plugin
- **DirectXTex texconv** — command-line conversion to PNG/TGA
- **RenderDoc** — texture viewer with mip/channel inspection

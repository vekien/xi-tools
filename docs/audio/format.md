# FFXI Audio Binary Format (`.bgw` / `.spw`)

Reverse-engineered while building `xi audio`, verified **byte-for-byte against
vgmstream** on ~150 sampled files plus every block-size anomaly file (a manual
one-off run — there is no in-tree regression harness). Parsing
reference: the Windower pol-utils codec (`PlayOnline.Core/{AudioFile,
AudioFileStream,ADPCMCodec}.cs`) and the `xim` client reimplementation
(`SoundEffectSection.kt`).

Decoder lives in [`src/xi/audio/xi_core.py`](../../src/xi/audio/xi_core.py).

## TL;DR

- `.bgw` = **music**, `.spw` = **sound effects**. Same container, different header.
- Three sample formats: **ADPCM** (the norm), **PCM** (raw 16-bit), **ATRAC3**.
- ADPCM + PCM decode natively in pure Python (no dependencies), byte-exact to the
  game. ATRAC3 (an MDCT codec) is routed to `vgmstream-cli` when available.
- Audio data always begins at offset **`0x30`**.

## Header

The fields are read **sequentially** right after the file marker, so the shorter
SeWave marker (8 bytes) shifts every later offset down 4 vs BGMStream (12 bytes).

| | `.bgw` music | `.spw` sfx |
|---|---|---|
| Marker | `BGMStream\0\0\0` (12 B) | `SeWave\0\0` (8 B) |
| Next two `int32` | `format`, `size` | `size`, `format` |

After those, both share the same body:

```
int32  id              # the sound's global id (see "id → file" below)
int32  sample_blocks   # number of ADPCM blocks (per channel)
int32  loop_start      # in blocks; < 0 = not looped
int32  sample_rate_high
int32  sample_rate_low
int32  unknown1
u8     unknown2, unknown3, channels, block_size
int32  unknown4        # SeWave only
```

`sample_format`: `0` = ADPCM, `1` = PCM, `3` = ATRAC3.

Because the SeWave marker is 4 bytes shorter, the trailing u8 quartet
(`unknown2, unknown3, channels, block_size`) sits at different *absolute* offsets
per container — bytes `0x28`–`0x2B` for SeWave vs `0x2C`–`0x2F` for BGMStream
(`parse_header` reads them at `body + 0x18`).

### Gotcha 1 — the sample rate is a sum of two signed ints

`sample_rate = sample_rate_high + sample_rate_low`, **as signed `int32`s**. The two
halves look like random garbage until you add them (deliberate obfuscation). E.g.
`se002060.spw`: `-425634300 + 425682300 = 48000`.

### Gotcha 2 — the `block_size` byte is unreliable

A handful of files (32 of 9,700 ADPCM sfx; 0 music) declare `block_size = 16` but
are actually laid out with a *smaller* frame. The game (and vgmstream) ignore the
byte and derive the true frame geometry from the data, so we do too:

```
body_bytes  = size - 0x30
frame_size  = body_bytes / (sample_blocks * channels)   # bytes per block per channel
block_samples = (frame_size - 1) * 2                     # decoded samples per block per channel
```

This always divides evenly for valid ADPCM. xi's derivation is gated to
**ADPCM only** (PCM/ATRAC3 always use the header byte) and additionally requires
the division to be clean and `frame_size >= 2`; otherwise it falls back to the
header byte. Examples:

| File | header `block_size` | derived `frame_size` | samples/block |
|------|--------------------|----------------------|---------------|
| `se002060.spw` | 16 | 9  | 16 ✓ |
| `music025.bgw` | 64 | 33 | 64 ✓ |
| `se032037.spw` | 16 (**lie**) | 3 | 4 |

## ADPCM codec

A 4-coefficient linear predictor. Per block, per channel: **1 header byte** +
`frame_size − 1` data bytes → `block_samples` 16-bit PCM samples.

```python
FILTER0 = (0x0000, 0x00F0, 0x01CC, 0x0188, 0x01E8)
FILTER1 = (0x0000, 0x0000, -0x00D0, -0x00DC, -0x00F0)

hdr   = data[base]
scale = (0x0C - (hdr & 0x0F)) & 0x1F   # 5-bit mask matches C# `int <<`
index = hdr >> 4                       # index >= 5 → channel silent this block, history untouched
for each data byte, for each of its 2 nibbles (low then high):
    v = nibble; if v >= 8: v -= 16
    sample = clamp16( (v << scale) + ((h0*FILTER0[index] + h1*FILTER1[index]) >> 8) )
    h1, h0 = h0, sample            # two-sample history, carried across blocks
```

Output is interleaved by channel.

### Gotcha 3 — the predictor uses an arithmetic shift, not `/256`

The predictor term is `>> 8` (arithmetic right shift, floors toward −∞). pol-utils'
C# `/256` *truncates toward zero* and is off-by-one on negative sums — the two
agree until the first negative-history sample, then silently drift. Using `>> 8`
makes the output byte-identical to the game.

## PCM (`format = 1`)

The body from `0x30` to `size` is raw interleaved 16-bit little-endian PCM. Copied
through as-is (truncated to a whole number of frames).

## ATRAC3 (`format = 3`)

A Sony MDCT codec — not hand-decodable. xi shells out to `vgmstream-cli` (with
`-i`, a single linear pass to match the native single-pass output). Auto-detected
from `$XI_VGMSTREAM`, `PATH`, or a known install path; `--native-only` skips it.
ATRAC3 is **not** rare on the PC client — ~36% of music and ~8.5% of sfx.

## Encrypted / variant SeWave

15 sfx (`se039211`–`se039225`) carry a non-zero flag at byte 7 (`0xFD` instead of
`0x00`). These are an encrypted variant that **vgmstream rejects too** — xi skips
them with a clear message rather than emitting garbage.

## id → file

The header `id` maps to a path deterministically (xim
`SoundEffectPointerSection.soundIdToFolderAndFile`), and the same id is what a
`0x3D` sound-pointer section stores — see [refs.md](refs.md):

```
folder = id // 1000   -> se{folder:03d}     # 2060 -> se002
file   = id           -> se{id:06d}         # 2060 -> se002060
=> sound/win/se/se002/se002060.spw
```

This se-scheme is **sfx-only**. Music is addressed by its own id as
`<root>/win/music/data/music{id:03d}.bgw` (see `locate_music` in `xi_core.py`).

## WAV output

Hand-rolled RIFF/WAVE, PCM 16-bit. For looped audio a `smpl` loop chunk is appended
(`loop_start_frame = loop_start * block_samples`), default on (`--no-loops` to omit).

**Caution:** for **PCM** files `block_samples` comes from the unreliable header
byte (the geometry derivation is ADPCM-only), so the product is not meaningful —
harmless today only because the encoder always writes `loop_start` = 0 or −1.

## On-disk layout

Under `FFXI_DIR`, across 7 sound roots (`sound`, `sound2`–`6`, `sound9`):

- Music: `<root>/win/music/data/*.bgw`
- SFX:   `<root>/win/se/seNNN/*.spw`

## See also

- [README.md](README.md) — the `xi audio` commands
- [refs.md](refs.md) — finding which sounds a spell/zone/mob DAT uses (`0x3D`)
- [../sounds/footsteps.md](../sounds/footsteps.md) — how terrain picks a footstep sound

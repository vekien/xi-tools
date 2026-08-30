# FFXI Opening Movie (`.pmv` / `PMUS`)

Retail ships a short full-motion open under the game root:

```
FINAL FANTASY XI\mov\
  mov999.pmv      # video
  music999.bgw    # audio bed (extension is misleading — see below)
```

`FFXiMain.dll` wires these as the title open (`CXiOpening` / `CXiMovie`), next to
UI screen assets (`screens mvcr1`…`mvcr6`).

Verified on a CatsEyeXI client install (`mov999.pmv` ≈ 301 MB,
`music999.bgw` ≈ 77 MB). Only this pair was present under `mov\` on that build.

## TL;DR

| Piece | Reality |
|-------|---------|
| `.pmv` | Proprietary wrapper: cleartext `PMUS` magic + **XOR-obfuscated MPEG-2 video ES** |
| XOR key | Fixed 8-byte key, indexed by **absolute file offset** |
| Video | MPEG-2 elementary stream — **512×336 @ 29.97 fps**, ~6 Mbps (sequence header) |
| `.bgw` next to it | **Not** FFXI `BGMStream` music. Plain **RIFF WAVE** PCM (48 kHz stereo 16-bit) |
| Audio length | ~420 s on the sample pair (matches a ~7 min open) |

Decode recipe: strip the 4-byte magic, XOR the rest, feed any MPEG-2 decoder;
play/mux the companion WAV.

## File layout

```
offset 0x00  u8[4]   magic = 'P','M','U','S'     # cleartext
offset 0x04  u8[]    ciphertext                  # through EOF
```

There is no length field, fourcc table, or demux index in the wrapper. The body
is a single continuous XOR stream of an MPEG-2 **video elementary stream**
(sequence headers `00 00 01 B3`, GOPs `B8`, pictures `00` — not a full PS/PSS
with pack headers).

### XOR

```
key[8] = { 0xC2, 0xEE, 0x9C, 0xD9, 0xBD, 0x6C, 0x4D, 0x72 }

for i in 4 .. file_size-1:          # skip magic
    plain[i - 4] = file[i] XOR key[i % 8]
```

Key alignment uses the **file offset** (`i`), not the payload index. Using
`key[(i-4) % 8]` misaligns and yields garbage.

After a correct XOR, the first bytes are a standard MPEG sequence header:

```
00 00 01 B3  20 01 50 14  …
             └─ 12-bit width=512, 12-bit height=336, then aspect / frame-rate nibble
```

Parsed from the sample:

| Field | Value |
|-------|--------|
| Width × height | 512 × 336 |
| Aspect (header) | 1:1 (code 1) |
| Frame rate | 29.97 (code 4) |
| Bitrate (header) | ~6 Mbps (`br * 400`) |

Entropy of the ciphertext is low (~3.4 bits/byte on the first 64 KiB) — it is
obfuscation, not strong encryption. Long runs of a repeated 8-byte pad
(`C2 EE 9C D9 BD 6C 4D 72` and close variants) decode to zeros and were how the
key was spotted.

### Pseudocode export

```python
KEY = bytes([0xC2, 0xEE, 0x9C, 0xD9, 0xBD, 0x6C, 0x4D, 0x72])

def pmv_to_m2v(pmv_path: str, m2v_path: str) -> None:
    data = open(pmv_path, "rb").read()
    assert data[:4] == b"PMUS", "not a PMUS movie"
    out = bytearray(len(data) - 4)
    for i in range(4, len(data)):
        out[i - 4] = data[i] ^ KEY[i % 8]
    open(m2v_path, "wb").write(out)
```

Then mux with the companion audio (ffmpeg example):

```bash
ffmpeg -i mov999.m2v -i music999.bgw -c:v copy -c:a copy opening.mkv
# or re-encode if the muxer dislikes raw ES:
ffmpeg -i mov999.m2v -i music999.bgw -c:v libx264 -c:a aac opening.mp4
```

## Companion `music999.bgw`

Despite the `.bgw` extension used for FFXI BGM containers (`BGMStream` + ADPCM /
ATRAC3 — see [format.md](format.md)), **this** file is a stock WAV:

| Field | Value (sample) |
|-------|----------------|
| Container | `RIFF` … `WAVE` |
| Format tag | 1 (PCM) |
| Channels | 2 |
| Sample rate | 48000 |
| Bits | 16 |
| Data size | ≈ 80 720 640 bytes → **≈ 420.4 s** |

Rename/copy to `.wav` if a tool is picky about extensions. Do **not** run it
through the normal FFXI BGM decoder.

## Client references

Strings in `FFXiMain.dll` (opening path):

```
mov999.pmv
CXiOpening
music999.bgw
screens mvcr1 … screens mvcr6
CXiMovie
```

`CXiMovie` also appears near Direct3D texture/source labels (player path that
uploads decoded frames). `PSS` / `IPU` strings exist elsewhere in the binary
(PS2-era lineage); this PC open is **not** a raw PSS file on disk — the on-disk
payload after XOR is MPEG-2 ES.

## What we did not reverse

- Whether the XOR key is global for every `.pmv` or only the opening (only
  `mov999.pmv` was present to test).
- Exact player class beyond the `CXiMovie` / `CXiOpening` names (no frame-accurate
  sync notes, subtitle track, or soft-subtitle format).
- Whether expansion clients ship additional `mov\*.pmv` pairs under other IDs.

## Related

- FFXI BGM/SFX containers (real `.bgw` / `.spw`): [format.md](format.md)
- Title screen scene DATs (non-FMO open): [../dats/ROM_0_23.md](../dats/ROM_0_23.md)

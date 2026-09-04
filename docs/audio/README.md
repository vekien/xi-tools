# AUDIO

Decode FFXI music (`.bgw`) and sound effects (`.spw`) to WAV, browse them with
real names/categories, dump a categorised catalog for a viewer, and find which
sounds any effect/zone/mob DAT uses.

ADPCM and PCM decode in **pure Python, byte-for-byte identical to vgmstream** (no
external tools). ATRAC3 (~36% of music) is routed to `vgmstream-cli` when present.

- Binary format + codec internals: [format.md](format.md)
- Opening movie (`.pmv` / `PMUS` → MPEG-2, companion WAV): [pmv.md](pmv.md)
- Finding which sounds a DAT uses (`0x3D`): [refs.md](refs.md)
- Where every sound is used, across every DAT: [scan.md](scan.md)

## Commands

| Command | What it does |
|---------|--------------|
| `uv run xi audio json --type music [NAME]` | List music tracks with titles, format, duration, loop |
| `uv run xi audio export --type music [NAME]` | Decode music to WAV (`exports/audio/music/…`) |
| `uv run xi audio json --type sfx [NAME]` | List sound effects with their game category |
| `uv run xi audio export --type sfx [NAME]` | Decode sound effects to WAV (`exports/audio/sfx/…`) |
| `uv run xi audio decode FILE…` | Decode explicit `.bgw`/`.spw` paths to `.wav` |
| `uv run xi audio info FILE` | Dump a file's parsed header |
| `uv run xi audio refs <dat>` | List the sounds a DAT references → JSON ([refs.md](refs.md)) |
| `uv run xi audio scan` | Walk every DAT: where each sound is used + what that DAT is (zone / NPC / spell / gear …) → JSON ([scan.md](scan.md)) |
| `uv run xi audio import FILE` | Encode audio → custom **`.spw` only** (sfx; music encode is library-only, not CLI) |
| `uv run xi audio install FILE` | Convert and install a sound as **`.spw`** under `win/se/seNNN/seNNNNNN.spw` |
| `uv run xi batch audio_music` | Decode **all** music + write `catalog.json` |
| `uv run xi batch audio_sfx` | Decode **all** sound effects + write `catalog.json` |

`NAME` is a substring filter on the filename (e.g. `music10`, `se002`). Use
`audio search` for the same catalog output with a default result limit.

## Decode one or inspect

```bash
uv run xi audio info  "…/sound/win/se/se002/se002060.spw"
uv run xi audio decode "…/sound/win/se/se002/se002060.spw" --out out/
```

`info` prints type, format, channels, sample rate, duration, and loop point.

## List with names

`json` reads each header and annotates it, emitting **JSON** (one object per file:
id, format, channels, sample_rate, looped, duration_sec, label). Music gets titles
from `MusicInfo.xml`; sound effects get their folder **category** (the game's own
grouping). `duration_sec` is `null` for ATRAC3 (its block fields don't map to a
frame count).

```bash
uv run xi audio json --type music music10 --root sound
uv run xi audio json --type sfx se019 --root sound
```

(The fixed-width `ROOT FILE FMT …` table an older revision showed here is the
output of the hidden legacy `audio music list` command, not `json`.)

`--root sound` limits to one sound root (default: all 7).

## Export everything (batch) + catalog

```bash
uv run xi batch audio_music          # -> exports/music/<root>/<stem>.wav + catalog.json
uv run xi batch audio_sfx            # -> exports/sfx/<root>/seNNN/<stem>.wav + catalog.json
uv run xi batch audio_sfx -w 8 --skip-existing --filter se002
```

- **Parallel** by default (`-w`, scaled to your CPU) — important for the ~12k sfx.
- **Resumable**: `--skip-existing` skips files already decoded.
- **Batch** output mirrors the source tree under a per-root subfolder so nothing
  collides. (`audio export` is different: it names each WAV after the sanitised
  track title, flat in the output dir — pass `--numbered` to mirror the source
  tree instead.)
- ATRAC3 routes through `vgmstream-cli`; `--native-only` skips it, `--vgmstream
  PATH` points at a specific binary.

Shared batch options: `-o/--output-dir`, `-w/--workers`, `--skip-existing`,
`-f/--filter`, `--limit`, `--loops/--no-loops`, `--vgmstream`, `--native-only`,
`--catalog/--no-catalog`.

### `catalog.json`

Each batch writes a catalog purpose-built for a browser/viewer — files grouped by
the game's own categories:

- **SFX** → grouped by `seNNN` folder category (Spell Sounds, Combat Sounds,
  Skillchain, Weapon Skill Effects, Footstep Effects, Monster SFX, …).
- **Music** → grouped by sound root, each track carrying its title.

```jsonc
{
  "kind": "sfx",
  "count": 11862,
  "group_count": 60,
  "formats": { "ADPCM": 9700, "PCM": 1141, "ATRAC3": 1006, "unparseable": 15 },
  "groups": [
    {
      "key": "se019",
      "label": "Skillchain Sounds",
      "count": 14,
      "files": [
        {
          "id": 19001, "file": "se019001", "root": "sound",
          "wav": "sound/se019/se019001.wav",   // relative to the output dir
          "src": "sound/se019/se019001.spw",
          "title": null, "category": "Skillchain Sounds",
          "format": "ADPCM", "channels": 1, "sample_rate": 48000,
          "duration": 3.312, "looped": false
        }
      ]
    }
  ]
}
```

Every file record includes the relative `.wav` path the batch wrote, so a viewer
loads audio + metadata together.

## ATRAC3 / vgmstream

ATRAC3 files need `vgmstream-cli`. xi auto-detects it from, in order:
`$XI_VGMSTREAM`, your `PATH`, then a known install location. Without it, ATRAC3
files are skipped (clearly reported) and ADPCM/PCM still decode natively.

## See also

- [format.md](format.md) — `.bgw`/`.spw` binary format + ADPCM codec
- [refs.md](refs.md) — `xi audio refs`: which sounds a spell/zone/mob DAT uses
- [../sounds/footsteps.md](../sounds/footsteps.md) — terrain → footstep sound mapping

#!/usr/bin/env python3
"""FFXI audio format: parse + decode `.bgw` / `.spw`, write WAV.

Both file types share one container; only the header differs:

    BGMStream  (music, .bgw)  marker = b"BGMStream\\0\\0\\0" (12 bytes), then
                              int32 format, int32 size, ...
    SeWave     (sfx,   .spw)  marker = b"SeWave\\0\\0"        (8 bytes),  then
                              int32 size,   int32 format, ...

After those two int32s the layout is identical:
    int32  id
    int32  sample_blocks        # number of ADPCM blocks (per channel)
    int32  loop_start           # in blocks; < 0 means "not looped"
    int32  sample_rate_high     # real rate = high + low, read as SIGNED int32s
    int32  sample_rate_low      #   (two random-looking values that sum to e.g. 48000)
    int32  unknown1
    uint8  unknown2, unknown3, channels, block_size
    int32  unknown4             # SeWave only

Audio data always begins at offset 0x30.

sample_format: 0 = ADPCM, 1 = PCM, 3 = ATRAC3. ATRAC3 is NOT console-only —
~36% of PC music and ~8.5% of sfx use it (hence the vgmstream fallback path).

ADPCM is a 4-coefficient predictor (port of pol-utils ADPCMCodec.cs). NB the
header's block_size byte lies in ~32 files: the real frame geometry is derived
from the body (frame_size = body_bytes / (sample_blocks * channels),
block_samples = (frame_size - 1) * 2) and only falls back to the header byte
when that division isn't clean — see _frame_geometry / docs audio/format.md.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

# Audio data always starts here, past the fixed 0x30-byte header.
DATA_OFFSET = 0x30

# Sample formats (see header docs above).
FMT_ADPCM = 0
FMT_PCM = 1
FMT_ATRAC3 = 3

# The seven sound roots a retail/private FFXI install ships, in priority order.
SOUND_ROOTS = ("sound", "sound2", "sound3", "sound4", "sound5", "sound6", "sound9")

# ADPCM predictor coefficients (Q8 fixed point).
_FILTER0 = (0x0000, 0x00F0, 0x01CC, 0x0188, 0x01E8)
_FILTER1 = (0x0000, 0x0000, -0x00D0, -0x00DC, -0x00F0)


# Per-kind config so `music` and `sfx` share one code path.
@dataclass(frozen=True)
class Kind:
    name: str            # "music" / "sfx"
    ext: str             # ".bgw" / ".spw"
    subdir: Tuple[str, ...]  # path under a sound root, e.g. ("win", "music", "data")
    recursive: bool      # sfx live in per-id seNNN/ subfolders


MUSIC = Kind("music", ".bgw", ("win", "music", "data"), recursive=False)
SFX = Kind("sfx", ".spw", ("win", "se"), recursive=True)
KINDS = {"music": MUSIC, "sfx": SFX}


@dataclass
class AudioHeader:
    type: str            # "BGMStream" / "SeWave" / "unknown"
    sample_format: int
    size: int
    id: int
    sample_blocks: int
    loop_start: int      # in blocks, < 0 = not looped
    sample_rate: int
    channels: int
    block_size: int      # raw header byte (unreliable; see frame_size/block_samples)
    frame_size: int = 0  # encoded bytes per block per channel (1 header + data)
    block_samples: int = 0  # decoded samples per block per channel

    @property
    def looped(self) -> bool:
        return self.loop_start >= 0

    @property
    def total_frames(self) -> int:
        """PCM sample frames (per channel)."""
        if self.sample_format == FMT_PCM:
            # raw 16-bit interleaved body
            return max(0, (self.size - DATA_OFFSET)) // (2 * max(1, self.channels))
        return self.sample_blocks * self.block_samples

    @property
    def loop_start_frame(self) -> int:
        return self.loop_start * self.block_samples if self.looped else 0

    @property
    def duration_sec(self) -> float:
        return self.total_frames / self.sample_rate if self.sample_rate else 0.0

    @property
    def format_name(self) -> str:
        return {FMT_ADPCM: "ADPCM", FMT_PCM: "PCM", FMT_ATRAC3: "ATRAC3"}.get(
            self.sample_format, f"fmt{self.sample_format}")


class AudioError(Exception):
    pass


def parse_header(data: bytes) -> AudioHeader:
    """Parse the 0x30-byte header of a .bgw/.spw blob. Raises AudioError if the
    marker is not recognised."""
    if len(data) < DATA_OFFSET:
        raise AudioError("file too small to contain an audio header")

    # The fields are read sequentially right after the marker, so the shorter
    # SeWave marker (8 B) shifts every subsequent offset down by 4 vs BGMStream.
    if data[:8] == b"SeWave\0\0":
        kind = "SeWave"
        # SeWave: size, format come first
        size, sample_format = struct.unpack_from("<ii", data, 8)
        body = 0x10
    elif data[:12] == b"BGMStream\0\0\0":
        kind = "BGMStream"
        # BGMStream: format, size come first
        sample_format, size = struct.unpack_from("<ii", data, 12)
        body = 0x14
    elif data[:6] == b"SeWave":
        # A handful of SeWave files carry a non-zero flag at byte 7 (e.g. 0xFD);
        # these are an encrypted/variant form that even vgmstream rejects.
        raise AudioError("encrypted/variant SeWave (unsupported; vgmstream "
                         "rejects these too)")
    else:
        raise AudioError(f"unrecognised marker {data[:12]!r} (not a .bgw/.spw)")

    # Shared body: id, sample_blocks, loop_start, sr_high, sr_low, unknown1,
    # then the (u2, u3, channels, block_size) byte quartet.
    (id_, sample_blocks, loop_start,
     sr_high, sr_low, _unknown1) = struct.unpack_from("<6i", data, body)
    _u2, _u3, channels, block_size = struct.unpack_from("<4B", data, body + 0x18)

    # The block_size header byte is unreliable (some files declare 16 but are
    # actually laid out with a smaller frame). Derive the true per-channel frame
    # size from the data geometry the way vgmstream does:
    #   frame_size = body_bytes / (sample_blocks * channels)
    # and fall back to the header byte only when the geometry isn't clean.
    frame_size = (1 + block_size // 2) if block_size else 0
    body_bytes = size - DATA_OFFSET
    denom = sample_blocks * channels
    if (sample_format == FMT_ADPCM and denom > 0 and body_bytes > 0
            and body_bytes % denom == 0):
        fs = body_bytes // denom
        if fs >= 2:
            frame_size = fs
    block_samples = (frame_size - 1) * 2

    return AudioHeader(
        type=kind,
        sample_format=sample_format,
        size=size,
        id=id_,
        sample_blocks=sample_blocks,
        loop_start=loop_start,
        # Real rate is the SIGNED sum of the two halves (deliberate obfuscation).
        sample_rate=sr_high + sr_low,
        channels=channels,
        block_size=block_size,
        frame_size=frame_size,
        block_samples=block_samples,
    )


def parse_header_file(path: Path) -> AudioHeader:
    """Parse just the header of a .bgw/.spw without reading the whole file."""
    with open(path, "rb") as f:
        return parse_header(f.read(DATA_OFFSET))


def _clamp16(v: int) -> int:
    if v > 0x7FFF:
        return 0x7FFF
    if v < -0x8000:
        return -0x8000
    return v


def decode(data: bytes, header: Optional[AudioHeader] = None) -> Tuple[AudioHeader, array]:
    """Decode a .bgw/.spw blob to interleaved 16-bit PCM.

    Returns (header, samples) where `samples` is an array('h') of signed 16-bit
    samples interleaved by channel. Raises AudioError on unsupported formats.
    """
    if header is None:
        header = parse_header(data)

    if header.channels < 1 or header.channels > 6:
        raise AudioError(f"bad channel count {header.channels}")

    if header.sample_format == FMT_PCM:
        end = header.size if header.size > DATA_OFFSET else len(data)
        raw = data[DATA_OFFSET:end]
        raw = raw[: len(raw) - (len(raw) % (2 * header.channels))]
        samples = array("h")
        samples.frombytes(raw)
        if sys.byteorder == "big":
            samples.byteswap()
        return header, samples

    if header.sample_format != FMT_ADPCM:
        raise AudioError(
            f"unsupported sample format {header.format_name} "
            f"(only ADPCM and PCM are decodable without vgmstream)")

    ch = header.channels
    # Use the geometry-derived frame size, not the (unreliable) block_size byte.
    frame_size = header.frame_size            # encoded bytes per block per channel
    data_bytes = frame_size - 1               # data bytes after the 1 header byte
    nsamp = header.block_samples              # decoded samples per channel per block
    in_block_len = frame_size * ch            # encoded bytes per block (all channels)
    if frame_size < 2:
        raise AudioError(f"invalid ADPCM frame size {frame_size}")

    # Cap to the blocks actually present in the file.
    avail_blocks = (len(data) - DATA_OFFSET) // in_block_len
    nblocks = min(header.sample_blocks, avail_blocks)

    out = array("h")
    # Two history samples per channel, carried across blocks.
    hist0 = [0] * ch
    hist1 = [0] * ch
    block_pcm = [0] * (nsamp * ch)             # reused scratch buffer

    pos = DATA_OFFSET
    f0, f1 = _FILTER0, _FILTER1
    for _ in range(nblocks):
        # Zero scratch (channels with index >= 5 stay silent for this block).
        for i in range(len(block_pcm)):
            block_pcm[i] = 0
        for c in range(ch):
            base = pos + c * frame_size
            hdr = data[base]
            scale = (0x0C - (hdr & 0x0F)) & 0x1F   # C# masks shift count to 5 bits
            index = hdr >> 4
            if index >= 5:
                continue
            flt0, flt1 = f0[index], f1[index]
            h0, h1 = hist0[c], hist1[c]
            out_i = c                               # interleaved write position
            for s in range(data_bytes):
                sample_byte = data[base + 1 + s]
                for nibble in range(2):
                    v = (sample_byte >> (4 * nibble)) & 0x0F
                    if v >= 8:
                        v -= 16
                    # Predictor term uses an arithmetic right shift (floors toward
                    # -inf), matching the game/vgmstream. pol-utils used C# `/256`
                    # which truncates toward zero and is off by 1 on negatives.
                    temp = (v << scale) + ((h0 * flt0 + h1 * flt1) >> 8)
                    temp = _clamp16(temp)
                    h1 = h0
                    h0 = temp
                    block_pcm[out_i] = temp
                    out_i += ch
            hist0[c], hist1[c] = h0, h1
        out.extend(block_pcm)
        pos += in_block_len

    return header, out


# ── WAV output ─────────────────────────────────────────────────────────────

def _smpl_chunk(sample_rate: int, loop_start: int, loop_end: int) -> bytes:
    """A `smpl` chunk describing one forward loop (widely recognised by audio
    tools and loop-aware players)."""
    sample_period = round(1_000_000_000 / sample_rate) if sample_rate else 0
    body = struct.pack(
        "<9I",
        0,              # manufacturer
        0,              # product
        sample_period,  # sample period (ns)
        60,             # MIDI unity note
        0,              # MIDI pitch fraction
        0,              # SMPTE format
        0,              # SMPTE offset
        1,              # num sample loops
        0,              # sampler data
    )
    body += struct.pack(
        "<6I",
        0,           # cue point id
        0,           # type: 0 = forward
        loop_start,  # start frame
        loop_end,    # end frame
        0,           # fraction
        0,           # play count: 0 = infinite
    )
    return b"smpl" + struct.pack("<I", len(body)) + body


def write_wav(path: Path, header: AudioHeader, samples: array,
              loops: bool = True) -> None:
    """Write interleaved 16-bit PCM to a RIFF/WAVE file. When `loops` and the
    source is looped, append a `smpl` loop chunk."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    pcm = samples
    if sys.byteorder == "big":
        pcm = array("h", samples)
        pcm.byteswap()
    pcm_bytes = pcm.tobytes()

    ch = header.channels
    rate = header.sample_rate
    byte_rate = rate * ch * 2
    block_align = ch * 2

    fmt = struct.pack("<HHIIHH", 1, ch, rate, byte_rate, block_align, 16)
    chunks = [b"fmt " + struct.pack("<I", len(fmt)) + fmt,
              b"data" + struct.pack("<I", len(pcm_bytes)) + pcm_bytes]
    if pcm_bytes and len(pcm_bytes) & 1:
        chunks[-1] += b"\0"  # data chunks are word-aligned

    if loops and header.looped:
        total = len(samples) // ch
        loop_start = min(header.loop_start_frame, max(0, total - 1))
        chunks.append(_smpl_chunk(rate, loop_start, max(loop_start, total - 1)))

    body = b"WAVE" + b"".join(chunks)
    riff = b"RIFF" + struct.pack("<I", len(body)) + body
    path.write_bytes(riff)


# Where to look for an optional vgmstream-cli binary (ATRAC3 fallback). Native
# ADPCM/PCM decoding needs none of this; only ATRAC3 files do.
_VGMSTREAM_KNOWN = (
    Path(r"D:\xidata\AltanaListener_Windows\Dependencies\vgmstream-cli.exe"),
)


def find_vgmstream(explicit: Optional[str] = None) -> Optional[Path]:
    """Locate a vgmstream-cli binary, or None. Checks, in order: an explicit
    path, $XI_VGMSTREAM, PATH, then known install locations."""
    cands: List[Optional[Path]] = []
    if explicit:
        cands.append(Path(explicit))
    env = os.environ.get("XI_VGMSTREAM")
    if env:
        cands.append(Path(env))
    for exe in ("vgmstream-cli", "vgmstream-cli.exe", "vgmstream"):
        w = shutil.which(exe)
        if w:
            cands.append(Path(w))
    cands += list(_VGMSTREAM_KNOWN)
    for c in cands:
        if c and c.is_file():
            return c
    return None


def _vgmstream_decode(vgm: Path, src: Path, dest: Path) -> None:
    """Decode `src` to `dest` via vgmstream. `-i` = single linear pass (no fake
    loop expansion), to match the native ADPCM path's clean stream."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([str(vgm), "-i", "-o", str(dest), str(src)],
                          capture_output=True, text=True)
    if proc.returncode != 0 or not dest.exists():
        msg = (proc.stderr or proc.stdout or "unknown error").strip()
        raise AudioError(f"vgmstream failed: {msg}")


def decode_file(src: Path, dest: Path, loops: bool = True,
                vgmstream: Optional[Path] = None) -> AudioHeader:
    """Decode one .bgw/.spw to a .wav. ADPCM/PCM decode natively (byte-exact);
    ATRAC3 routes to vgmstream when a binary is supplied. Returns the header."""
    data = Path(src).read_bytes()
    header = parse_header(data)
    if header.sample_format in (FMT_ADPCM, FMT_PCM):
        _, samples = decode(data, header)
        write_wav(dest, header, samples, loops=loops)
        return header
    # ATRAC3 (or anything else): needs vgmstream.
    if vgmstream is None:
        raise AudioError(
            f"{header.format_name} needs vgmstream (not bundled). Install "
            f"vgmstream-cli on PATH or set XI_VGMSTREAM, or pass --vgmstream.")
    _vgmstream_decode(Path(vgmstream), Path(src), Path(dest))
    return header


# ── File enumeration ───────────────────────────────────────────────────────

@dataclass
class AudioEntry:
    path: Path           # absolute source path
    root: str            # sound root (e.g. "sound3")
    rel: Path            # path relative to <root>/<subdir>, for mirroring output
    stem: str            # filename without extension


def _matches(name: str, patterns: Tuple[str, ...]) -> bool:
    if not patterns:
        return True
    low = name.lower()
    return any(p.lower() in low for p in patterns)


def iter_entries(kind: Kind, base_dir: Path, roots: Tuple[str, ...] = SOUND_ROOTS,
                 patterns: Tuple[str, ...] = ()) -> Iterator[AudioEntry]:
    """Yield every audio file of `kind` under the given sound roots, optionally
    filtered to those whose filename contains one of `patterns`."""
    base_dir = Path(base_dir)
    for root in roots:
        sub = base_dir / root
        for part in kind.subdir:
            sub = sub / part
        if not sub.is_dir():
            continue
        globber = sub.rglob if kind.recursive else sub.glob
        for f in sorted(globber("*" + kind.ext)):
            if not f.is_file():
                continue
            if not _matches(f.name, patterns):
                continue
            yield AudioEntry(path=f, root=root, rel=f.relative_to(sub), stem=f.stem)


def list_entries(kind: Kind, base_dir: Path, roots=SOUND_ROOTS,
                 patterns=()) -> List[AudioEntry]:
    return list(iter_entries(kind, base_dir, roots, patterns))


def locate_sound(base_dir: Path, sound_id: int, ext: str = ".spw",
                 roots: Tuple[str, ...] = SOUND_ROOTS) -> Optional[Tuple[Path, str]]:
    """Find the on-disk file for a sound id across the sound roots.

    Returns ``(path, root)`` for the first root that has it, or None. The id maps
    to ``<root>/win/se/se{id//1000:03d}/se{id:06d}{ext}`` (the client's scheme)."""
    folder = f"{sound_id // 1000:03d}"
    file = f"{sound_id:06d}"
    base_dir = Path(base_dir)
    for root in roots:
        p = base_dir / root / "win" / "se" / f"se{folder}" / f"se{file}{ext}"
        if p.is_file():
            return p, root
    return None


def locate_music(base_dir: Path, music_id: int,
                 roots: Tuple[str, ...] = SOUND_ROOTS) -> Optional[Tuple[Path, str]]:
    """Find the on-disk ``.bgw`` for a music id across the sound roots.

    Returns ``(path, root)`` for the first root that has it, or None. A zone's
    ``zone_settings`` music id N maps to ``<root>/win/music/data/music{N:03d}.bgw``
    — the value the server sends *is* the file number, zero-padded to three digits
    (so day-music 51 → ``music051.bgw``, 109 → ``music109.bgw``)."""
    base_dir = Path(base_dir)
    name = f"music{music_id:03d}.bgw"
    for root in roots:
        p = base_dir / root / "win" / "music" / "data" / name
        if p.is_file():
            return p, root
    return None

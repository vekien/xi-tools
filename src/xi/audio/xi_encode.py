#!/usr/bin/env python3
"""Encode audio → FFXI ``.spw`` (SeWave): the inverse of :mod:`xi.audio.xi_core`.

Imports a user sound — ``.wav`` natively, ``.ogg`` via vgmstream, anything else via
ffmpeg when present — downmixes to mono 16-bit, and writes a ``.spw`` whose header
matches retail ``se`` exactly. Two encodings:

* **ADPCM** (default) — the 4-coefficient predictor the client uses; byte-format
  identical to retail se. Lossy (4-bit), but round-trips through ``xi_core.decode``
  at a high SNR.
* **PCM** — raw 16-bit; lossless. 25 retail se ship as PCM, so the client plays it.

Header conventions reverse-engineered from real files (see ``parse_header``):
``unknown1`` is always ``0x30``; ADPCM uses 16 samples/block (9-byte frames) with
``sample_blocks`` = block count and ``u3``/``block_size``/``unk4`` = 16; PCM uses
``sample_blocks`` = sample count and ``u3``/``unk4`` = 1. The sample rate is stored
as two signed int32 halves that sum to the rate (we use ``rate`` + ``0``).
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

from xi.audio.xi_core import (
    DATA_OFFSET, FMT_ADPCM, FMT_PCM, _FILTER0, _FILTER1, AudioError, find_vgmstream,
)

_UNK1 = 0x30                       # unknown1 — always the data offset in retail se
_BLOCK_SAMPLES = 16               # ADPCM samples per block (retail se convention)
_FRAME_BYTES = 1 + _BLOCK_SAMPLES // 2   # 1 header byte + 8 data bytes = 9

# Where to look for an optional ffmpeg (used for mp3/flac/… → wav). Not needed for
# .wav (stdlib) or .ogg (vgmstream).
_FFMPEG_KNOWN: Tuple[Path, ...] = ()


def find_ffmpeg(explicit: Optional[str] = None) -> Optional[Path]:
    """Locate an ffmpeg binary, or None. Checks an explicit path, $XI_FFMPEG, PATH."""
    cands = []
    if explicit:
        cands.append(Path(explicit))
    env = os.environ.get("XI_FFMPEG")
    if env:
        cands.append(Path(env))
    for exe in ("ffmpeg", "ffmpeg.exe"):
        w = shutil.which(exe)
        if w:
            cands.append(Path(w))
    cands += list(_FFMPEG_KNOWN)
    for c in cands:
        if c and c.is_file():
            return c
    return None


def _run(cmd) -> bool:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True)
        return p.returncode == 0
    except Exception:
        return False


# ── input decode → (samples, rate) ──────────────────────────────────────────

def _read_wav(path: Path) -> Tuple[np.ndarray, int]:
    """Read a PCM WAV → (int32 array shape (n, channels) scaled to 16-bit, rate)."""
    with wave.open(str(path), "rb") as w:
        ch, sw, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 2:
        a = np.frombuffer(raw, dtype="<i2").astype(np.int32)
    elif sw == 1:                       # unsigned 8-bit → signed 16-bit
        a = (np.frombuffer(raw, dtype=np.uint8).astype(np.int32) - 128) << 8
    elif sw == 3:                       # packed 24-bit little-endian → 16-bit
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        a = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        a = np.where(a & 0x800000, a - (1 << 24), a) >> 8
    elif sw == 4:                       # 32-bit int → 16-bit
        a = (np.frombuffer(raw, dtype="<i4").astype(np.int64) >> 16).astype(np.int32)
    else:
        raise AudioError(f"unsupported WAV sample width {sw} bytes")
    if ch > 1:
        a = a[: (len(a) // ch) * ch]
    return a.reshape(-1, ch), rate


def decode_input(path: Path) -> Tuple[np.ndarray, int]:
    """Decode any supported input to (int32 (n, ch) 16-bit-scaled samples, rate).

    ``.wav`` reads directly; ``.ogg`` routes through vgmstream; everything else
    needs ffmpeg. Raises :class:`AudioError` with an install hint when no decoder
    is available for the format."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".wav":
        try:
            return _read_wav(path)
        except (AudioError, wave.Error, EOFError):
            pass  # odd/float WAV — fall through to a converter
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "in.wav"
        if ext == ".ogg":
            vg = find_vgmstream()
            if vg and _run([str(vg), "-i", "-o", str(tmp), str(path)]) and tmp.is_file():
                return _read_wav(tmp)
        ff = find_ffmpeg()
        if ff and _run([str(ff), "-y", "-i", str(path), "-acodec", "pcm_s16le", "-f", "wav", str(tmp)]) and tmp.is_file():
            return _read_wav(tmp)
    raise AudioError(
        f"can't decode {ext or path.name!r}: pass a .wav, or install ffmpeg "
        f"(set $XI_FFMPEG or put it on PATH). .ogg also works via vgmstream.")


def to_mono16(a: np.ndarray) -> np.ndarray:
    """Downmix (n, ch) → mono int array clamped to the 16-bit range."""
    m = a[:, 0] if a.shape[1] == 1 else a.mean(axis=1)
    return np.clip(np.rint(m), -32768, 32767).astype(np.int64)


# ── header ───────────────────────────────────────────────────────────────────
# Two containers share one 0x30 header; only the marker + (size/format order) and a
# trailing SeWave-only int32 differ. ``kind`` selects SeWave (.spw, sound effects) or
# BGMStream (.bgw, music).

def _as2d(samples: np.ndarray) -> np.ndarray:
    """Coerce a sample array to shape (n, channels) — 1-D becomes mono (n, 1)."""
    return samples.reshape(-1, 1) if samples.ndim == 1 else samples


def _audio_header(kind: str, fmt: int, sound_id: int, sample_blocks: int, loop_start: int,
                  rate: int, channels: int, u2: int, u3: int, block_size: int, unk4: int,
                  total_size: int) -> bytes:
    h = bytearray(DATA_OFFSET)
    if kind == "bgw":
        h[0:12] = b"BGMStream\0\0\0"
        struct.pack_into("<i", h, 0x0C, fmt)
        struct.pack_into("<i", h, 0x10, total_size)
        body = 0x14
    else:  # "spw" / SeWave
        h[0:8] = b"SeWave\0\0"
        struct.pack_into("<i", h, 0x08, total_size)
        struct.pack_into("<i", h, 0x0C, fmt)
        body = 0x10
    struct.pack_into("<i", h, body + 0x00, sound_id)
    struct.pack_into("<i", h, body + 0x04, sample_blocks)
    struct.pack_into("<i", h, body + 0x08, loop_start)
    struct.pack_into("<i", h, body + 0x0C, rate)   # sr_high = rate …
    struct.pack_into("<i", h, body + 0x10, 0)      # … sr_low = 0  (sum = rate)
    struct.pack_into("<i", h, body + 0x14, _UNK1)
    h[body + 0x18] = u2 & 0xFF
    h[body + 0x19] = u3 & 0xFF
    h[body + 0x1A] = channels & 0xFF
    h[body + 0x1B] = block_size & 0xFF
    if kind == "spw":
        struct.pack_into("<i", h, body + 0x1C, unk4)   # SeWave only (lands at 0x2C)
    return bytes(h)


# ── PCM ──────────────────────────────────────────────────────────────────────

def encode_pcm(samples: np.ndarray, rate: int, sound_id: int = 0, loop_start: int = -1,
               kind: str = "spw") -> bytes:
    s = _as2d(samples)
    n, ch = s.shape
    pcm = s.astype("<i2").reshape(-1).tobytes()      # channel-interleaved 16-bit
    total = DATA_OFFSET + len(pcm)
    hdr = _audio_header(kind, FMT_PCM, sound_id, n, loop_start, rate, ch,
                        u2=0, u3=1, block_size=16, unk4=1, total_size=total)
    return hdr + pcm


# ── ADPCM ─────────────────────────────────────────────────────────────────────

def _encode_block(samp, h0: int, h1: int):
    """Encode 16 samples → (9 bytes, new h0, new h1). Brute-forces the best
    predictor index (0-4) and per-block left-shift by simulating the decoder so
    error tracks the reconstructed signal exactly (the predictor is a feedback
    loop, so history must follow what the client will actually reconstruct)."""
    best = None
    for index in range(5):
        f0, f1 = _FILTER0[index], _FILTER1[index]
        for shift in range(0, 13):
            ph0, ph1, err = h0, h1, 0
            nibs = []
            for s in samp:
                pred = (ph0 * f0 + ph1 * f1) >> 8
                resid = s - pred
                v = ((resid + (1 << (shift - 1))) >> shift) if shift else resid  # round-to-nearest
                if v > 7:
                    v = 7
                elif v < -8:
                    v = -8
                rec = (v << shift) + pred
                rec = 0x7FFF if rec > 0x7FFF else (-0x8000 if rec < -0x8000 else rec)
                d = s - rec
                err += d * d
                nibs.append(v & 0xF)
                ph1, ph0 = ph0, rec
            if best is None or err < best[0]:
                best = (err, index, shift, nibs, ph0, ph1)
            if err == 0:
                break
        if best and best[0] == 0:
            break
    _, index, shift, nibs, nh0, nh1 = best
    low = (0x0C - shift) & 0x0F                       # inverse of decode's scale calc
    hdr = ((index & 0xF) << 4) | low
    data = bytes((nibs[2 * i] | (nibs[2 * i + 1] << 4)) for i in range(8))
    return bytes([hdr]) + data, nh0, nh1


def encode_adpcm(samples: np.ndarray, rate: int, sound_id: int = 0, loop_start: int = -1,
                 kind: str = "spw") -> bytes:
    s = _as2d(samples)
    n, ch = s.shape
    nblocks = (n + _BLOCK_SAMPLES - 1) // _BLOCK_SAMPLES
    pad = nblocks * _BLOCK_SAMPLES - n
    if pad:
        s = np.vstack([s, np.zeros((pad, ch), dtype=s.dtype)])
    cols = [s[:, c].tolist() for c in range(ch)]      # per-channel sample streams
    hist = [[0, 0] for _ in range(ch)]                # ADPCM history is per-channel
    out = bytearray()
    for b in range(nblocks):
        lo, hi = b * _BLOCK_SAMPLES, (b + 1) * _BLOCK_SAMPLES
        for c in range(ch):                            # one frame per channel, in order
            enc, h0, h1 = _encode_block(cols[c][lo:hi], hist[c][0], hist[c][1])
            hist[c][0], hist[c][1] = h0, h1
            out += enc
    total = DATA_OFFSET + len(out)
    hdr = _audio_header(kind, FMT_ADPCM, sound_id, nblocks, loop_start, rate, ch,
                        u2=(127 if kind == "bgw" else 0), u3=16, block_size=16, unk4=16,
                        total_size=total)
    return hdr + bytes(out)


# ── high level ────────────────────────────────────────────────────────────────

def import_sound(input_path, out_path, sound_id: int = 0, fmt: str = "adpcm",
                 loop: bool = False) -> dict:
    """Convert ``input_path`` → a ``.spw`` at ``out_path``. Returns a small info dict."""
    a, rate = decode_input(Path(input_path))
    mono = to_mono16(a)
    if mono.size == 0:
        raise AudioError("input decoded to zero samples")
    if fmt == "pcm":
        blob = encode_pcm(mono, rate, sound_id, loop_start=0 if loop else -1)
    elif fmt == "adpcm":
        blob = encode_adpcm(mono, rate, sound_id, loop_start=0 if loop else -1)
    else:
        raise AudioError(f"unknown format {fmt!r} (use 'adpcm' or 'pcm')")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blob)
    return {
        "out": str(out), "format": fmt.upper(), "sound_id": sound_id,
        "rate": int(rate), "channels": 1, "samples": int(mono.size),
        "duration_sec": round(mono.size / rate, 3) if rate else 0.0,
        "bytes": len(blob), "looped": loop,
    }


def import_music(input_path, out_path, music_id: int = 0, fmt: str = "adpcm",
                 loop: bool = True, stereo: bool = True) -> dict:
    """Convert ``input_path`` → a ``.bgw`` (BGMStream) at ``out_path``. Keeps up to two
    channels (music is usually stereo) and loops by default. Returns an info dict."""
    a, rate = decode_input(Path(input_path))
    if a.shape[0] == 0:
        raise AudioError("input decoded to zero samples")
    if stereo and a.shape[1] >= 2:
        samp = a[:, :2]
    else:
        samp = to_mono16(a).reshape(-1, 1)
    samp = np.clip(np.rint(samp), -32768, 32767).astype(np.int64)
    ls = 0 if loop else -1
    if fmt == "pcm":
        blob = encode_pcm(samp, rate, music_id, loop_start=ls, kind="bgw")
    elif fmt == "adpcm":
        blob = encode_adpcm(samp, rate, music_id, loop_start=ls, kind="bgw")
    else:
        raise AudioError(f"unknown format {fmt!r} (use 'adpcm' or 'pcm')")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blob)
    ch = int(samp.shape[1])
    return {
        "out": str(out), "format": fmt.upper(), "music_id": music_id,
        "rate": int(rate), "channels": ch, "samples": int(samp.shape[0]),
        "duration_sec": round(samp.shape[0] / rate, 3) if rate else 0.0,
        "bytes": len(blob), "looped": loop,
    }


# ── install into the game's sound tree ────────────────────────────────────────
# Custom sounds aren't part of the ROM-DAT override, so they go straight into the
# client's sound tree (FFXI_DIR), where the audio system loads se{id:06d}.spw on
# demand. We use a high "custom" id range so a new se{folder} can't collide with
# retail content.

import re as _re  # noqa: E402

_CUSTOM_ID_BASE = 990000


def _scan_sound_ids(base: Path, roots: Tuple[str, ...]) -> set:
    """Every soundId already present on disk across the sound roots."""
    ids = set()
    for r in roots:
        se = base / r / "win" / "se"
        if not se.is_dir():
            continue
        for f in se.rglob("se*.spw"):
            m = _re.match(r"se0*(\d+)$", f.stem, _re.IGNORECASE)
            if m:
                ids.add(int(m.group(1)))
    return ids


def alloc_sound_id(base: Path, roots: Tuple[str, ...], start: int = _CUSTOM_ID_BASE) -> int:
    """First unused soundId at/after ``start`` across the sound roots."""
    used = _scan_sound_ids(base, roots)
    i = start
    while i in used:
        i += 1
    return i


def install_path(base: Path, sound_id: int, root: str = "sound") -> Path:
    """``<base>/<root>/win/se/se{id//1000:03d}/se{id:06d}.spw`` — the client's scheme."""
    folder = f"{sound_id // 1000:03d}"
    return base / root / "win" / "se" / f"se{folder}" / f"se{sound_id:06d}.spw"


# ── custom-sound registry ─────────────────────────────────────────────────────
# pol-utils never named our imports, so we keep a small sidecar (next to the sound
# tree) mapping custom soundId → friendly title, which the editor's SFX catalog
# overlays so an import shows up as e.g. "bark" instead of "se990000".

def custom_registry_path(base: Path) -> Path:
    return Path(base) / "xi_custom_sounds.json"


def load_custom_sounds(base: Path) -> dict:
    """``{str(sound_id): {"title": str, "file": str}}`` — empty if none yet."""
    try:
        return json.loads(custom_registry_path(base).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def register_custom_sound(base: Path, sound_id: int, title: str, file: str) -> None:
    reg = load_custom_sounds(base)
    reg[str(sound_id)] = {"title": title, "file": file}
    custom_registry_path(base).write_text(json.dumps(reg, indent=2), encoding="utf-8")


def install_sound(input_path, base, sound_id: Optional[int] = None, fmt: str = "adpcm",
                  loop: bool = False, root: str = "sound", title: Optional[str] = None,
                  roots: Tuple[str, ...] = None) -> dict:
    """Convert ``input_path`` and write it into the game's sound tree under a soundId.

    Picks the next free id in the custom range when ``sound_id`` is None, records a
    friendly title in the custom-sound registry, and returns the import info dict plus
    ``installed`` (the on-disk path), ``sound_id`` and ``title``."""
    from xi.audio.xi_core import SOUND_ROOTS
    base = Path(base)
    roots = roots or SOUND_ROOTS
    if sound_id is None:
        sound_id = alloc_sound_id(base, roots)
    dest = install_path(base, sound_id, root)
    info = import_sound(input_path, dest, sound_id=sound_id, fmt=fmt, loop=loop)
    info["installed"] = str(dest)
    info["root"] = root
    info["sound_id"] = sound_id
    title = title or Path(input_path).stem
    info["title"] = title
    folder = f"{sound_id // 1000:03d}"
    register_custom_sound(base, sound_id, title, f"se{folder}/se{sound_id:06d}.spw")
    return info


# ── install music into the game's music tree ──────────────────────────────────
_CUSTOM_MUSIC_BASE = 990


def _scan_music_ids(base: Path, roots: Tuple[str, ...]) -> set:
    ids = set()
    for r in roots:
        md = Path(base) / r / "win" / "music" / "data"
        if not md.is_dir():
            continue
        for f in md.glob("music*.bgw"):
            m = _re.match(r"music0*(\d+)$", f.stem, _re.IGNORECASE)
            if m:
                ids.add(int(m.group(1)))
    return ids


def alloc_music_id(base: Path, roots: Tuple[str, ...], start: int = _CUSTOM_MUSIC_BASE) -> int:
    used = _scan_music_ids(base, roots)
    i = start
    while i in used:
        i += 1
    return i


def music_install_path(base: Path, music_id: int, root: str = "sound") -> Path:
    return Path(base) / root / "win" / "music" / "data" / f"music{music_id:03d}.bgw"


def custom_music_registry_path(base: Path) -> Path:
    return Path(base) / "xi_custom_music.json"


def load_custom_music(base: Path) -> dict:
    try:
        return json.loads(custom_music_registry_path(base).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def register_custom_music(base: Path, music_id: int, title: str, file: str) -> None:
    reg = load_custom_music(base)
    reg[str(music_id)] = {"title": title, "file": file}
    custom_music_registry_path(base).write_text(json.dumps(reg, indent=2), encoding="utf-8")


def install_music_file(input_path, base, music_id: Optional[int] = None, fmt: str = "adpcm",
                       loop: bool = True, root: str = "sound", title: Optional[str] = None,
                       roots: Tuple[str, ...] = None) -> dict:
    """Convert ``input_path`` and write it into the game's music tree under a music id.

    Picks the next free id in the custom range when ``music_id`` is None, records a
    friendly title, and returns the import info plus ``installed``/``music_id``/``title``."""
    from xi.audio.xi_core import SOUND_ROOTS
    base = Path(base)
    roots = roots or SOUND_ROOTS
    if music_id is None:
        music_id = alloc_music_id(base, roots)
    dest = music_install_path(base, music_id, root)
    info = import_music(input_path, dest, music_id=music_id, fmt=fmt, loop=loop)
    info["installed"] = str(dest)
    info["root"] = root
    info["music_id"] = music_id
    title = title or Path(input_path).stem
    info["title"] = title
    register_custom_music(base, music_id, title, f"music{music_id:03d}.bgw")
    return info


# ── CLI ───────────────────────────────────────────────────────────────────────

import click  # noqa: E402


@click.command("import")
@click.argument("input_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--out", "out_path", type=click.Path(), default=None,
              help="Output .spw path (default: the input name with a .spw extension).")
@click.option("--id", "sound_id", type=int, default=0,
              help="Sound id stamped into the header (the seNNNNNN number it'll live as).")
@click.option("--format", "fmt", type=click.Choice(["adpcm", "pcm"]), default="adpcm",
              help="Encoding: adpcm (default, matches retail se) or pcm (lossless, larger).")
@click.option("--loop", is_flag=True, help="Mark the sound as looping in the header.")
@click.option("--verify/--no-verify", default=True,
              help="Decode the result and report the round-trip SNR (default: on).")
def import_cmd(input_file, out_path, sound_id, fmt, loop, verify):
    """Convert an audio file to a FFXI .spw (.wav, .ogg, or anything ffmpeg reads).

    \b
      xi audio import jingle.wav
      xi audio import voice.ogg --id 99001 --out se099/se099001.spw
      xi audio import beep.wav --format pcm
    """
    inp = Path(input_file)
    out = Path(out_path) if out_path else inp.with_suffix(".spw")
    try:
        info = import_sound(inp, out, sound_id=sound_id, fmt=fmt, loop=loop)
    except AudioError as e:
        raise click.ClickException(str(e))
    click.echo(f"{inp.name} -> {info['out']}")
    click.echo(f"  {info['format']}  {info['rate']} Hz  mono  {info['duration_sec']}s  "
               f"{info['samples']} samples  {info['bytes']} bytes"
               + (f"  id {sound_id}" if sound_id else "")
               + ("  (looped)" if loop else ""))
    if verify:
        from xi.audio import xi_core as core
        try:
            h, samp = core.decode(out.read_bytes())
            a, rate = decode_input(inp)
            mono = to_mono16(a)
            rec = np.frombuffer(samp.tobytes(), dtype="<i2").astype(np.float64)
            n = min(len(rec), mono.size)
            o = mono[:n].astype(np.float64)
            noise = float(np.sum((o - rec[:n]) ** 2))
            sigp = float(np.sum(o * o))
            snr = 99.0 if noise <= 0 else 10 * np.log10(sigp / max(noise, 1e-9))
            click.echo(f"  verify: re-decoded OK as {h.format_name}, round-trip SNR "
                       f"{snr:.1f} dB" + (" (lossless)" if fmt == "pcm" else ""))
        except Exception as e:
            click.echo(f"  verify skipped: {e}", err=True)


@click.command("install")
@click.argument("input_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--id", "sound_id", type=int, default=None,
              help="soundId to install as (default: next free id in the custom range).")
@click.option("--format", "fmt", type=click.Choice(["adpcm", "pcm"]), default="adpcm",
              help="Encoding (default adpcm).")
@click.option("--loop", is_flag=True, help="Mark the sound as looping.")
@click.option("--root", default="sound", help="Sound root to install into (default: sound).")
@click.option("--dir", "base_dir", type=click.Path(), default=None,
              help="Install base (default: the FFXI client dir from config).")
def install_cmd(input_file, sound_id, fmt, loop, root, base_dir):
    """Convert and install a sound into the game's sound tree under a soundId.

    The file lands at ``<root>/win/se/seNNN/seNNNNNN.spw`` so the client loads it on
    demand. Reference the printed soundId from a zone sound emitter to play it.

    \b
      xi audio install bark.wav                 # auto-assigns a free custom id
      xi audio install theme.ogg --id 990100
    """
    from xi.xi_config import FFXI_DIR
    base = Path(base_dir) if base_dir else Path(FFXI_DIR)
    try:
        info = install_sound(input_file, base, sound_id=sound_id, fmt=fmt, loop=loop, root=root)
    except AudioError as e:
        raise click.ClickException(str(e))
    click.echo(f"installed soundId {info['sound_id']} -> {info['installed']}")
    click.echo(f"  {info['format']}  {info['rate']} Hz  mono  {info['duration_sec']}s  {info['bytes']} bytes"
               + ("  (looped)" if loop else ""))
    click.echo(f"  reference it from a zone sound emitter with soundId {info['sound_id']}.")

"""Title screen scene (`ROM/0/23.DAT`, magic `titl`).

The login screen renders real in-game zones as a live 3D background. This file holds
what it shows: which zones, where the camera flies, and the atmosphere for each segment.
It contains no geometry or textures of its own.

Structure
---------
A 32-byte header, then a flat list of 4-byte-tagged nodes. Every node carries the
constant `0x486` at +4, which is what makes them findable::

    +0x00  char[4]  tag        e.g. "cgu5", "ct20"
    +0x04  u32      0x486
    +0x20  u32      keyframe count
    +0x30  keyframes, 48-byte stride

Camera keyframe::

    +0x00  float3   eye XYZ
    +0x0c  float    view distance (constant across a track)
    +0x10  float3   look-at XYZ
    +0x20  float    t   (0.0 .. 1.0 across the track)

Zone sections follow, each introduced by the 8-byte marker `0x67b`::

    +0x00  u32      zone id
    ...             a stream of typed records: weather, transitions, ambient colour

Weather record::

    +0x00  char[4]  tag        "suny" "fine" "mist" "clod" "rain" "snow" "thdr" "dryw"
    +0x04  u16      0x037d
    +0x08  u16      blend in / u16 blend out
    +0x0c  u8[3]    fog colour RGB, +1 pad
    +0x10  u32      fog flags (0x604)
    +0x14  u16      fog near / u16 fog far
    +0x18  char[8]  control track name

Which camera drives which zone is not stored as a pointer -- a zone's weather records
name their control tracks, and those names share a per-zone prefix (`cgu*` for North
Gustaberg, `cga*` for Gusgen Mines, `ct1*`/`ct2*` for the two Tahrongi sections). That
binding is why changing a zone id alone is not enough: the camera keeps flying the old
zone's coordinates, usually ending up underground.
"""

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

import click

import xi.xi_config as _cfg

MAGIC = b'titl'
# Two node type constants are in use, and both hold the same keyframe layout. Matching
# only 0x486 silently drops every 0x606 node -- which is most of the multi-keyframe
# camera paths, including cgu1 and cgu8 (3 keyframes each against the 2 that 0x486
# nodes carry).
NODE_TYPES = (0x486, 0x606)
NODE_COUNT_OFF = 0x20
NODE_KEYFRAME_OFF = 0x30
KEYFRAME_STRIDE = 0x30
ZONE_MARKER = struct.pack('<Q', 0x67b)

WEATHER_TAGS = (b'clod', b'mist', b'snow', b'suny', b'fine', b'rain', b'thdr',
                b'dryw', b'aura', b'loop')
REC_WEATHER = 0x037D
REC_DURATION = 0x0210
REC_END = 0x037C
REC_AMBIENT = 0x030F
FOG_FLAGS = 0x0604


@dataclass
class Keyframe:
    t: float
    eye: tuple
    look: tuple
    view_distance: float


@dataclass
class Track:
    name: str
    offset: int
    keyframes: list


@dataclass
class Weather:
    tag: str
    offset: int
    blend_in: int
    blend_out: int
    rgb: tuple
    fog_near: int
    fog_far: int
    track: str


@dataclass
class ZoneSection:
    index: int
    offset: int
    zone_id: int
    end: int
    weather: list = field(default_factory=list)

    @property
    def file_table_index(self) -> int:
        """xim file-table index for this zone id."""
        return 0x64 + self.zone_id if self.zone_id < 0x100 else 0x147B3 + (self.zone_id - 0x100)


def _track_name(raw: bytes) -> str:
    """Decode a control-track name, or '' if the bytes are not one.

    The field is a fixed 8 bytes and is often simply absent, leaving whatever the record
    layout put there. Decoding those with errors='replace' produced names like the
    control characters shown as box glyphs, so anything that is not printable ASCII is
    treated as no track rather than passed on as a name.
    """
    text = raw.split(b'\x00')[0]
    if not text or not all(0x20 <= c < 0x7F for c in text):
        return ''
    return text.decode('ascii').strip()


def parse_nodes(data: bytes) -> dict:
    """Every tagged scene node, by tag name."""
    out = {}
    for off in range(0x20, len(data) - 8):
        if struct.unpack_from('<I', data, off + 4)[0] not in NODE_TYPES:
            continue
        tag = data[off:off + 4]
        if all(0x20 <= c < 0x7F for c in tag):
            out.setdefault(tag.decode('ascii'), off)
    return out


def parse_track(data: bytes, name: str, off: int) -> Track:
    count = struct.unpack_from('<I', data, off + NODE_COUNT_OFF)[0]
    frames = []
    if 0 < count <= 64:
        for i in range(count):
            base = off + NODE_KEYFRAME_OFF + i * KEYFRAME_STRIDE
            if base + KEYFRAME_STRIDE > len(data):
                break
            ex, ey, ez, dist = struct.unpack_from('<4f', data, base)
            lx, ly, lz, _ = struct.unpack_from('<4f', data, base + 0x10)
            t = struct.unpack_from('<f', data, base + 0x20)[0]
            frames.append(Keyframe(t, (ex, ey, ez), (lx, ly, lz), dist))
    return Track(name, off, frames)


def parse_zones(data: bytes) -> list:
    """Zone sections with their weather records."""
    marks = [m.end() for m in re.finditer(re.escape(ZONE_MARKER), data)
             if struct.unpack_from('<I', data, m.end())[0] < 0x400]
    zones = []
    for i, off in enumerate(marks):
        end = marks[i + 1] - 8 if i + 1 < len(marks) else len(data)
        zone = ZoneSection(i + 1, off, struct.unpack_from('<I', data, off)[0], end)
        k = off
        while k < end - 32:
            tag = data[k:k + 4]
            if tag not in WEATHER_TAGS:
                k += 1
                continue
            kind = struct.unpack_from('<H', data, k + 4)[0]
            if kind == REC_WEATHER:
                # Long form: a blend pair and a fog colour sit before the fog config.
                blend_in, blend_out = struct.unpack_from('<2H', data, k + 8)
                rgb = (data[k + 12], data[k + 13], data[k + 14])
                near, far = struct.unpack_from('<2H', data, k + 20)
                raw = data[k + 24:k + 32]
                width = 32
            elif kind == FOG_FLAGS:
                # Short form: no blend, no colour -- fog config straight after the tag.
                # Both shapes occur in the same file. Matching only on 0x037d reported
                # no weather at all for La Theine and Valkurm, which use this one.
                blend_in = blend_out = 0
                rgb = None
                near, far = struct.unpack_from('<2H', data, k + 8)
                raw = data[k + 12:k + 20]
                width = 28
            else:
                k += 1
                continue
            zone.weather.append(Weather(tag.decode(), k, blend_in, blend_out,
                                        rgb, near, far, _track_name(raw)))
            k += width
        zones.append(zone)
    return zones


def tracks_for(zone: ZoneSection, nodes: dict, resolved_only: bool = True) -> list:
    """Control track names this zone's weather records reference.

    With `resolved_only`, only names that exist as scene nodes -- those are the ones with
    keyframes to read or write. Otherwise every referenced name, including labels that
    have no node of their own.
    """
    names = {w.track for w in zone.weather if w.track}
    if resolved_only:
        names = {n for n in names if n in nodes}
    return sorted(names)


def family_tracks(zone: ZoneSection, nodes: dict) -> list:
    """Every camera node belonging to this zone, not just the ones weather records name.

    A zone owns a whole prefix family -- `cgu*` is North Gustaberg, `cqf*` is Qufim --
    and the segment plays them as consecutive shots. Weather records name only the few
    that coincide with a weather change, so listing those alone under-reports badly:
    North Gustaberg names 4 but has 11, which matches the dozen shots visible on screen.

    The family is keyed on the first three characters, since `cga`/`cgh`/`cgn`/`cgu` are
    different zones that all begin `cg`. A zone can draw on more than one family, so the
    union over its referenced names is taken rather than assuming a single prefix.
    """
    prefixes = {t[:3] for t in tracks_for(zone, nodes) if len(t) >= 3}
    if not prefixes:
        return []
    return sorted(n for n in nodes if n[:3] in prefixes)


def resolve(dat_path: str | None) -> Path:
    p = Path(dat_path) if dat_path else Path(_cfg.FFXI_DIR) / 'ROM/0/23.DAT'
    if not p.is_absolute():
        p = Path(_cfg.FFXI_DIR) / p
    if not p.exists():
        raise click.ClickException(f'Title scene DAT not found: {p}')
    data = p.read_bytes()
    if data[:4] != MAGIC:
        raise click.ClickException(f'{p} is not a title scene (magic {data[:4]!r}, expected titl)')
    return p


@dataclass
class Record:
    """One entry in a zone section's ordered record stream."""
    kind: str          # 'weather' | 'timing' | 'ambient' | 'end'
    offset: int
    tag: str = ''
    rgb: tuple = None
    fog_near: int = 0
    fog_far: int = 0
    track: str = ''
    value: int = 0
    rgba: tuple = None


def parse_stream(data: bytes, zone: ZoneSection) -> list:
    """Walk a zone section in file order.

    The section is a stream, not a table: weather states, timing entries and ambient
    colours appear in the order the segment plays them, so reading it in order is what
    gives the shot list. Widths come from the record type; anything unrecognised is
    stepped over two bytes at a time rather than guessed at.
    """
    out = []
    k = zone.offset + 16
    while k < zone.end - 8:
        tag = data[k:k + 4]
        kind16 = struct.unpack_from('<H', data, k)[0]

        if tag in WEATHER_TAGS:
            shape = struct.unpack_from('<H', data, k + 4)[0]
            if shape == REC_WEATHER:
                near, far = struct.unpack_from('<2H', data, k + 20)
                out.append(Record('weather', k, tag.decode(),
                                  (data[k + 12], data[k + 13], data[k + 14]), near, far,
                                  _track_name(data[k + 24:k + 32])))
                k += 32
                continue
            if shape == FOG_FLAGS:
                near, far = struct.unpack_from('<2H', data, k + 8)
                out.append(Record('weather', k, tag.decode(), None, near, far,
                                  _track_name(data[k + 12:k + 20])))
                k += 28
                continue

        if kind16 == REC_DURATION:
            out.append(Record('timing', k, value=struct.unpack_from('<H', data, k + 6)[0]))
            k += 8
            continue
        if kind16 == REC_END:
            out.append(Record('end', k))
            k += 8
            continue
        if kind16 == REC_AMBIENT:
            lo, hi = struct.unpack_from('<2H', data, k + 4)
            out.append(Record('ambient', k, value=lo, fog_far=hi,
                              rgba=tuple(data[k + 12:k + 16])))
            k += 16
            continue
        k += 2
    return out


def shot_list(records: list) -> list:
    """Weather segments in play order, each with the camera it hands to.

    A weather record names the control track that flies while it is showing, so the
    stream in order is the sequence of shots.
    """
    return [r for r in records if r.kind == 'weather']

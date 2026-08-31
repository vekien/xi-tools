"""Shared DAT-classification helpers for the ``xi mv update`` list builders.

Two facts do all the work here:

1. **Every DAT is a flat list of 16-byte-headed sections.** The header is
   ``char name[4]`` then a ``u32`` where ``type & 0x7F`` is the section type and
   ``(meta >> 7) & 0xFFFFF`` is the size in 16-byte units (same layout
   ``entity.anim.xi_export.parse_sections`` uses). Walking just the headers —
   seek, read 16 bytes, skip — classifies the whole ROM tree in ~20s without
   reading a single section body.

2. **A DAT's *kind* falls out of the set of section types it holds.** Checked
   against the curated ``mv/lists`` JSONs: every map/UI image is a
   texture-only container, every NPC model carries a ``SkeletonMesh``, every
   VFX file carries a ``ParticleGenerator``/``EffectRoutine``.

The reverse FTABLE map (``DAT path → file_id``) lives here too, since both the
image and NPC builders want it.
"""

from __future__ import annotations

import os
import re
import struct
from functools import lru_cache
from pathlib import Path

from xi.xi_config import FFXI_DIR

# Section type codes (subset of entity.mesh.xi_export.SECTION_TYPE_NAMES).
T_END = 0x00
T_DIRECTORY = 0x01
T_PARTICLE_GEN = 0x05
T_EFFECT_ROUTINE = 0x07
T_ZONE_DEF = 0x1C
T_TEXTURE = 0x20
T_SKELETON = 0x29
T_SKELETON_MESH = 0x2A
T_SKELETON_ANIM = 0x2B
T_ZONE_MESH = 0x2E
T_UI_MENU = 0x30
T_UI_ELEMENT_GROUP = 0x31

# An image DAT holds textures and nothing but UI scaffolding around them.
_IMAGE_ALLOWED = frozenset({T_END, T_DIRECTORY, T_TEXTURE, T_UI_MENU, T_UI_ELEMENT_GROUP})

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Cap on sections walked per file — guards against a corrupt header turning into
# a multi-million-iteration loop. Real DATs stay well under this.
_SECTION_CAP = 4000


def _fourcc(raw: bytes) -> str:
    """FourCC of a section header: its first four bytes, non-ASCII dotted."""
    return "".join(chr(b) if 32 <= b < 127 else "." for b in raw[:4])


def _walk_types(path: Path) -> tuple[str | None, frozenset[int], frozenset[str]]:
    """Seek-walk a DAT's section headers.

    Returns ``(first section FourCC, type codes, Directory-section FourCCs)``.

    Returns ``("\\x89PNG", frozenset())`` shaped output for raw PNG files (a few
    hundred icon DATs are plain PNGs with a .DAT extension, not section containers).
    """
    types: set[int] = set()
    dirs: set[str] = set()
    first: str | None = None
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(16)
            if head.startswith(_PNG_MAGIC):
                return "PNG", frozenset(), frozenset()
            pos = 0
            n = 0
            while pos + 16 <= size and n < _SECTION_CAP:
                f.seek(pos)
                hdr = f.read(16)
                if len(hdr) < 16:
                    break
                meta = struct.unpack_from("<I", hdr, 4)[0]
                sec_size = ((meta >> 7) & 0xFFFFF) * 0x10
                if sec_size <= 0:
                    break
                if first is None:
                    first = _fourcc(hdr)
                kind = meta & 0x7F
                if kind == T_DIRECTORY:
                    dirs.add(_fourcc(hdr))
                types.add(kind)
                n += 1
                pos = (pos + sec_size + 15) & ~15
    except OSError:
        return None, frozenset(), frozenset()
    return first, frozenset(types), frozenset(dirs)


class DatEntry:
    """One DAT on disk: its ROM-relative path, first FourCC and section types."""

    __slots__ = ("dat", "fourcc", "types", "dirs", "size")

    def __init__(
        self, dat: str, fourcc: str | None, types: frozenset[int],
        size: int, dirs: frozenset[str] = frozenset(),
    ):
        self.dat = dat          # 'ROM/17/72.DAT' — forward slashes, upper case
        self.fourcc = fourcc
        self.types = types
        # FourCCs named by this DAT's Directory (0x01) sections — the other
        # content families it pulls in. See :func:`extra_anim_packs`.
        self.dirs = dirs
        self.size = size

    # ── kind predicates ────────────────────────────────────────────────────
    @property
    def is_png(self) -> bool:
        return self.fourcc == "PNG"

    @property
    def is_image(self) -> bool:
        """Texture container (map / UI / cutscene art), or a raw PNG icon."""
        if self.is_png:
            return True
        return T_TEXTURE in self.types and self.types <= _IMAGE_ALLOWED

    @property
    def is_model(self) -> bool:
        """Carries a skinned mesh — an NPC/monster/costume model."""
        return T_SKELETON_MESH in self.types

    @property
    def has_skeleton(self) -> bool:
        """Carries its own skeleton + animations (so it can stand alone)."""
        return T_SKELETON in self.types

    @property
    def is_zone(self) -> bool:
        return T_ZONE_DEF in self.types

    @property
    def is_effect(self) -> bool:
        """Particle/routine VFX file with no model or zone geometry of its own."""
        if T_SKELETON_MESH in self.types or T_ZONE_MESH in self.types:
            return False
        return T_PARTICLE_GEN in self.types or T_EFFECT_ROUTINE in self.types


def _rom_dirs(base: Path):
    for romdir in sorted(base.glob("ROM*")):
        if romdir.is_dir():
            yield romdir


@lru_cache(maxsize=1)
def scan_dats() -> dict[str, DatEntry]:
    """Classify every ``ROM*/<sub>/<idx>.DAT`` under FFXI_DIR. Keyed by upper path.

    ~52k files, header-walk only — about 20 seconds cold, instant once cached.
    """
    base = Path(FFXI_DIR)
    out: dict[str, DatEntry] = {}
    for romdir in _rom_dirs(base):
        for sub in romdir.iterdir():
            if not sub.is_dir() or not sub.name.isdigit():
                continue
            for f in sub.glob("*.DAT"):
                dat = f"{romdir.name}/{sub.name}/{f.stem}.DAT".upper()
                fourcc, types, dirs = _walk_types(f)
                try:
                    size = f.stat().st_size
                except OSError:
                    size = 0
                out[dat] = DatEntry(dat, fourcc, types, size, dirs)
    return out


@lru_cache(maxsize=1)
def file_id_by_dat() -> dict[str, int]:
    """Reverse FTABLE/VTABLE: ``'ROM/17/72.DAT' → 5361``.

    The forward direction (``file_id → DAT``) is what the game uses; this is the
    inverse, so a list entry that only knows a path can report its file_id.

    Tables are searched base-first (FTABLE, then ROM2…ROM10) and the first hit
    wins — the same rule ``ftable.xi_core.scan_file_ids`` uses. That matters:
    plenty of ids are empty in the base table and only registered in a ROMn one
    (file_id 144 is EMPTY in FTABLE but resolves to ROM3/5/7.DAT via FTABLE3), so
    ``xi ftable lookup --file-id N`` will disagree until you pass ``--table N``.

    About 1,535 DATs are registered under more than one file_id (shared dummy /
    placeholder files); the **lowest** id wins, which is the canonical one.
    Roughly 1,475 DATs on disk have no file_id at all — custom drop-ins and
    retired retail files the tables no longer point at.
    """
    from xi.ftable.xi_core import load_all_tables, resolve_dat

    tables = load_all_tables()
    out: dict[str, int] = {}
    span = max((min(len(f) // 2, len(v)) for f, v in tables.values()), default=0)
    for file_id in range(span):
        for _idx, (fdata, vdata) in sorted(tables.items()):
            dat, _vt = resolve_dat(fdata, vdata, file_id)
            if dat:
                out.setdefault(dat.upper(), file_id)
                break
    return out


def file_id_for(dat_path: str) -> int | None:
    """file_id for a list-style DAT path (either slash style), or None."""
    return file_id_by_dat().get(norm(dat_path))


@lru_cache(maxsize=1)
def dat_by_file_id() -> dict[int, str]:
    """Forward map ``file_id → 'ROM/x/y.DAT'`` over every registered id."""
    from xi.ftable.xi_core import load_all_tables, resolve_dat

    tables = load_all_tables()
    out: dict[int, str] = {}
    span = max((min(len(f) // 2, len(v)) for f, v in tables.values()), default=0)
    for file_id in range(span):
        for _idx, (fdata, vdata) in sorted(tables.items()):
            dat, _vt = resolve_dat(fdata, vdata, file_id)
            if dat:
                out[file_id] = dat.upper()
                break
    return out


# ── path helpers ─────────────────────────────────────────────────────────────

def norm(p: str) -> str:
    """Canonical key form: forward slashes, upper case ('ROM/17/72.DAT')."""
    return p.replace("\\", "/").upper()


def back(p: str) -> str:
    """characters.json / images.json / npcs.json style: backslashes."""
    return p.replace("/", "\\")


def fwd(p: str) -> str:
    """effects.json style: forward slashes."""
    return p.replace("\\", "/")


def pretty(p: str) -> str:
    """'ROM/17/72.DAT' → 'ROM/17/72' (label-friendly)."""
    return re.sub(r"\.DAT$", "", norm(p), flags=re.I)


# A Directory id naming more DATs than this is a *shared* vocabulary (the race
# motion set 'mot_' reaches 3658 files), not one entity's own pack. Callers pass
# their own limit; this is the default the npc-anims target ships with.
_FAMILY_CAP = 64


@lru_cache(maxsize=1)
def _dats_by_root() -> dict[str, list[str]]:
    """Every DAT grouped by its own root FourCC (the first section's name)."""
    out: dict[str, list[str]] = {}
    for dat, e in scan_dats().items():
        if e.fourcc:
            out.setdefault(e.fourcc, []).append(dat)
    return out


def extra_anim_packs(
    dat: str, *, cap: int = _FAMILY_CAP, ignore: frozenset[str] = frozenset(),
) -> list[str]:
    """DATs holding extra animations for the entity model at ``dat``.

    A trust — a party NPC with a full player-style move set — does not carry its
    own combat clips. Its model DAT declares them by reference: alongside its
    own root FourCC it holds **Directory (0x01)** sections naming other content
    families, and the animation packs are the DATs whose *root* FourCC is one of
    those names.

    Both Iroha models (``ROM/310/3``, ``ROM/310/4``) name ``iro_``; 17 DATs have
    that root and the 6 carrying a ``SkeletonAnimation`` are ``ROM/338/15``
    through ``ROM/338/20`` — exactly the set found by hand. The type field is
    what keeps this clean: gear ids in the same DAT (``hf_1``, ``hf_e``, …) are
    SkeletonMesh/Texture sections and ``cait``/``corp`` are EffectRoutines, so
    only real content families are typed Directory.

    ``cap`` rejects shared vocabularies: a goblin names ``mot_``, the race-wide
    motion set, which reaches thousands of DATs and belongs to no one entity.
    """
    key = norm(dat)
    scan = scan_dats()
    entry = scan.get(key)
    if entry is None:
        return []
    by_root = _dats_by_root()

    packs: list[str] = []
    for did in sorted(entry.dirs):
        if did == entry.fourcc or did in ignore:
            continue
        family = by_root.get(did) or []
        if not family or len(family) > cap:
            continue
        for d in family:
            if d == key:
                continue
            t = scan[d].types
            # A pack that brings its own skeleton or mesh is a model in its own
            # right, not a motion pack for this one.
            if T_SKELETON in t or T_SKELETON_MESH in t:
                continue
            if T_SKELETON_ANIM in t:
                packs.append(d)

    fids = file_id_by_dat()
    # File-id order is the order the client registers them, which is also the
    # order the packs are meant to be layered.
    return sorted(set(packs), key=lambda d: (fids.get(d, 1 << 30), d))


def anim_clip_ids(dat: str) -> list[str]:
    """Clip ids (SkeletonAnimation section names) in one DAT, in file order.

    Reads the file rather than the cached header scan, which keeps only type
    codes. Callers use it on the handful of packs :func:`extra_anim_packs`
    returns, not across the archive.
    """
    path = Path(FFXI_DIR) / norm(dat)
    out: list[str] = []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            pos = 0
            n = 0
            while pos + 16 <= size and n < _SECTION_CAP:
                f.seek(pos)
                hdr = f.read(16)
                if len(hdr) < 16:
                    break
                meta = struct.unpack_from("<I", hdr, 4)[0]
                sec_size = ((meta >> 7) & 0xFFFFF) * 0x10
                if sec_size <= 0:
                    break
                if (meta & 0x7F) == T_SKELETON_ANIM:
                    out.append(_fourcc(hdr))
                n += 1
                pos = (pos + sec_size + 15) & ~15
    except OSError:
        return []
    return out

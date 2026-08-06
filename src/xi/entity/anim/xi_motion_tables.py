"""
Self-contained PC per-race motion DAT enumeration.

The FFXI client resolves a player character's animation/motion DATs the same way
for every race: it reads a per-race *base file_id* from a lookup table inside
``FFXiMain.dll``, adds a category-specific index, then maps that flat file_id to a
ROM path through FTABLE/VTABLE.  Each lookup table is located at runtime by
scanning the DLL for a 4-byte big-endian "hint" (the table's own first bytes).

This module reproduces that mechanism exactly, so ``xi anim list`` can
enumerate every animation for every race with **no external data files** — only
the game's own ``FFXiMain.dll`` (which ships with FFXI and is already read
elsewhere in xi) plus the FTABLE/VTABLE tables:

    hint-scan FFXiMain.dll   ->  per-race base file_id, per motion category
    base + index             ->  motion DAT file_id
    file_id  (FTABLE/VTABLE) ->  ROM/<dir>/<file>.DAT
    ROM DAT  (0x2B sections) ->  named animation tracks

Confirmed against two faithful client reimplementations: the Kotlin ``xim``
client (``MainDll.getBaseRaceConfigIndex`` etc., located by the same hints) and
the UE5 ``FFXIEngine`` C++ engine.  The ``movement`` (race-config) bases read
here match the C++ ``base_skeleton_no_tab`` table verbatim
(HumeMale 0x01BA0 .. Galka 0x066F0).

Per-category extent
-------------------
Within a race, a category's reachable motion DATs occupy ``[base, base + N)``
where ``N`` (constant across races) is the race's exclusive file_id window —
the distance from the category's HumeMale base to the next base of *any*
race/category.  Two layouts occur inside that window:

* sparse categories (movement / emote / dance) — the real clips are a
  contiguous run starting at ``base``; the rest of the window is unrelated.
  Walk from ``base`` and stop at the first non-animation file_id.
* dense categories (battle / dual-wield / weaponskill / action / fishing) —
  the clips fill the window (possibly 1-based or multi-block).  Collect every
  animation DAT in the window, skipping non-animation slots.

This partitions cleanly: no file_id is attributed to two different races
(TaruMale/TaruFemale legitimately share one base table and are the sole
exception, by game design).
"""

import struct
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from xi.xi_config import FFXI_DIR, read_path_for
from xi.ftable.xi_core import load_all_tables, resolve_dat


# RaceGenderConfig index 1..8 — the lookup-table index for every PC race+gender.
RACE_NAMES = [
    'HumeMale', 'HumeFemale', 'ElvaanMale', 'ElvaanFemale',
    'TaruMale', 'TaruFemale', 'Mithra', 'Galka',
]

# Motion-category lookup tables inside FFXiMain.dll, each found by its 4-byte
# big-endian hint (the first two uint16 table entries — both the HumeMale base).
# Order here is the display order. Values cross-checked against xim's
# DllOffsetHints (MainDll.kt) and FFXIEngine GameDataRepository.cpp.
MOTION_CATEGORY_HINTS: Dict[str, int] = {
    'movement':    0xA01BA01B,  # race-config DAT: skeleton + idl/wlk/run/mv*/ded/cor + block clips
    'emote':       0x48274827,  # /emote clips (bow, wave, ...), scattered across ROM dirs
    'dance':       0xB9E2B9E2,  # /dance clips
    'action':      0xCB96CB96,  # sit / fish-pose / mount action clips
    'fishing':     0x8B998B99,  # fishing-rod clips
    'battle':      0xC825C825,  # engaged/cast clips, per weapon-animation-type
    'dwMain':      0x6F9F6F9F,  # dual-wield main-hand clips
    'dwOff':       0xEF9DEF9D,  # dual-wield off-hand clips
    'weaponSkill': 0xCB81CB81,  # weapon-skill clips, per animation id
}

# Categories whose real clips are a contiguous prefix from the base (rest of the
# window is unrelated). Everything else fills its window densely.
SPARSE_CATEGORIES = frozenset({'movement', 'emote', 'dance'})

# Start DLL hint scan past the PE headers/code to avoid coincidental matches
# (the lookup tables live well past here; matches UE5's 0x30000 scan floor).
_DLL_SCAN_START = 0x30000


def maindll_path() -> Path:
    return Path(FFXI_DIR) / 'FFXiMain.dll'


def load_maindll() -> bytes:
    """Read FFXiMain.dll, or raise FileNotFoundError with guidance."""
    p = maindll_path()
    if not p.exists():
        raise FileNotFoundError(
            f'FFXiMain.dll not found at {p}. Set FFXI_DIR to your FINAL FANTASY XI '
            f'install so per-race motion tables can be read.')
    return p.read_bytes()


def find_table_offset(dll: bytes, hint: int) -> int:
    """File offset of a lookup table, located by its 4-byte big-endian hint."""
    pat = struct.pack('>I', hint)
    idx = dll.find(pat, _DLL_SCAN_START)
    if idx < 0:
        idx = dll.find(pat)
    return idx


def read_race_bases(dll: bytes, table_offset: int) -> List[int]:
    """The 8 per-race base file_ids (RaceGenderConfig index 1..8) at table_offset.

    The table is uint16-LE per index; index 0 and 1 are both HumeMale (that
    duplicate pair is what the hint matches), so the eight PC races are read at
    index 1..8 == ``table_offset + i*2``.
    """
    return [struct.unpack_from('<H', dll, table_offset + i * 2)[0] for i in range(1, 9)]


def category_bases(dll: bytes) -> Dict[str, List[int]]:
    """{category: [base file_id per race]} for every category found in the DLL."""
    out: Dict[str, List[int]] = {}
    for cat, hint in MOTION_CATEGORY_HINTS.items():
        off = find_table_offset(dll, hint)
        if off >= 0:
            out[cat] = read_race_bases(dll, off)
    return out


def _window_sizes(cat_bases: Dict[str, List[int]]) -> Dict[str, int]:
    """Per-category exclusive window ``N`` = distance from each category's
    HumeMale base to the next base of any race/category. Constant across races,
    so the HumeMale value bounds every race's range for that category."""
    all_bases = sorted({b for bases in cat_bases.values() for b in bases})

    def next_global(b: int) -> int:
        for x in all_bases:
            if x > b:
                return x
        return b + 0x1000

    return {cat: next_global(bases[0]) - bases[0] for cat, bases in cat_bases.items()}


class _FileIdResolver:
    """file_id -> ROM-relative DAT path, via the (preloaded) FTABLE/VTABLE set."""

    def __init__(self):
        self._tables = load_all_tables()
        self._order = sorted(self._tables)

    def rom_spec(self, file_id: int) -> Optional[str]:
        for idx in self._order:
            fdata, vdata = self._tables[idx]
            dat, _ = resolve_dat(fdata, vdata, file_id)
            if dat:
                return dat
        return None

    def file_id_for(self, spec: str) -> Optional[int]:
        """Reverse of :meth:`rom_spec`: the lowest file_id that maps to a ROM spec.

        Lazily builds the inverse map once. Used for DATs addressed by file-NUMBER
        rather than the file_id walk (emote waist siblings), which still want a real
        file_id in the output."""
        inv = getattr(self, '_inv', None)
        if inv is None:
            inv = {}
            maxid = max(min(len(fd) // 2, len(vd)) for fd, vd in self._tables.values())
            for fid in range(maxid):
                for idx in self._order:
                    fdata, vdata = self._tables[idx]
                    dat, _ = resolve_dat(fdata, vdata, fid)
                    if dat:
                        inv.setdefault(dat, fid)
                        break
            self._inv = inv
        return inv.get(spec)


# Emote part-2 (waist, 12-joint) clips live in a SIBLING DAT at file-NUMBER +6 in
# the same ROM folder (bow0/bow1 in ROM/61/8 -> bow2 in ROM/61/14). That sibling
# sits in a different file_id cluster than the emote base — past a block of FTABLE
# aliases — so the contiguous file_id walk never reaches it. Matches the client's
# emote motionEnum(6) layout (xi's xi_export.EMOTE_WAIST_OFFSET).
EMOTE_WAIST_OFFSET = 6


def _waist_sibling_spec(spec: str) -> Optional[str]:
    """ROM/<dir>/<file+6>.DAT for an emote DAT spec, or None if not file-numbered."""
    p = Path(spec)
    try:
        nxt = int(p.stem) + EMOTE_WAIST_OFFSET
    except ValueError:
        return None
    return f'{p.parent.as_posix()}/{nxt}.DAT'


def enumerate_race_animations(
    progress: Optional[Callable[[str], None]] = None,
) -> Iterator[Tuple[str, str, int, str, List[Dict[str, object]]]]:
    """Yield ``(race, category, file_id, rom_spec, animations)`` for every PC
    motion DAT, where ``animations`` is the non-empty ``list_animations`` output.

    Self-contained: bases come from FFXiMain.dll, paths from FTABLE/VTABLE.
    ``progress`` (if given) is called with a short status string per race.
    """
    from xi.entity.anim.xi_export import list_animations  # avoid import cycle

    dll = load_maindll()
    cat_bases = category_bases(dll)
    if not cat_bases:
        raise ValueError(
            'No motion lookup tables found in FFXiMain.dll (unexpected DLL build). '
            'Cannot enumerate per-race animations.')
    windows = _window_sizes(cat_bases)
    resolver = _FileIdResolver()

    def dat_animations(file_id: int) -> Tuple[Optional[str], Optional[List[Dict[str, object]]]]:
        spec = resolver.rom_spec(file_id)
        if not spec:
            return None, None
        full = Path(FFXI_DIR) / spec
        if not full.exists():
            return spec, None
        try:
            return spec, list_animations(read_path_for(full).read_bytes())
        except Exception:
            return spec, None

    for ri, race in enumerate(RACE_NAMES):
        if progress:
            progress(f'scanning {race} ...')
        # FTABLE aliases multiple file_ids onto the same physical DAT (filler
        # entries point repeatedly at one ROM file). Those resolve to identical
        # animation tracks, so yield each physical DAT once per race — otherwise a
        # DAT reached by N aliases produces N copies of every track.
        seen: set = set()
        emote_specs: List[str] = []
        for cat, bases in cat_bases.items():
            base = bases[ri]
            window = windows[cat]
            sparse = cat in SPARSE_CATEGORIES
            for off in range(window):
                spec, anims = dat_animations(base + off)
                if anims:
                    if spec not in seen:
                        seen.add(spec)
                        if cat == 'emote':
                            emote_specs.append(spec)
                        yield race, cat, base + off, spec, anims
                    # else: alias of an already-yielded DAT — skip, but it's still
                    # an animation DAT so do NOT end a sparse prefix here.
                elif sparse:
                    break  # contiguous prefix ended

        # Emote part-2 (waist) DATs: the file-NUMBER +6 sibling of each emote DAT,
        # which the file_id walk above can't reach. A genuine waist sibling holds
        # ONLY part-2 clips (names ending in '2', e.g. bow2/poi2); skip siblings
        # that are empty or hold part-0/1 (a different emote 6 files away).
        for spec in emote_specs:
            wspec = _waist_sibling_spec(spec)
            if not wspec or wspec in seen:
                continue
            wfull = Path(FFXI_DIR) / wspec
            if not wfull.exists():
                continue
            try:
                wanims = list_animations(read_path_for(wfull).read_bytes())
            except Exception:
                continue
            if not wanims or not all(
                    str(a['name']).strip().endswith('2') for a in wanims):
                continue
            seen.add(wspec)
            yield race, 'emote', resolver.file_id_for(wspec) or 0, wspec, wanims
                # dense: skip non-animation slot, keep filling the window

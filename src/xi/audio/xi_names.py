#!/usr/bin/env python3
"""Human-readable names + categories for FFXI audio, and the sound-id ↔ file map.

Track titles and per-folder categories come from the Windower pol-utils metadata
(`MusicInfo.xml` / `SFXInfo.xml`, bundled under ``data/``). Coverage is partial for
SFX (system/menu/category sounds) but the per-folder category labels — "Spell
Sounds", "Combat Sounds", "Skillchain Sounds", "Monster SFX - …", "Footstep
Effects", etc. — cover the whole tree and are the game's own grouping.

The sound id is the value stored in a `.bgw`/`.spw` header (and in a `0x3D`
SoundEffectPointer DAT section). It maps to a file deterministically, exactly as
the client does (xim ``SoundEffectPointerSection.soundIdToFolderAndFile``):

    folder = id // 1000   ->  se{folder:03d}
    file   = id           ->  se{id:06d}.spw

(The se-scheme is sfx-only. Music uses a different scheme entirely:
<root>/win/music/data/music{id:03d}.bgw — see xi_core.locate_music.)
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Tuple

_DATA = Path(__file__).parent / "data"


def sound_id_to_folder_file(sound_id: int) -> Tuple[str, str]:
    """``2060`` -> ``("002", "002060")`` (folder digits, 6-digit file stem)."""
    return f"{sound_id // 1000:03d}", f"{sound_id:06d}"


def sound_id_to_relpath(sound_id: int, ext: str = ".spw") -> str:
    """``2060`` -> ``se/se002/se002060.spw`` (relative to a sound root's win/)."""
    folder, file = sound_id_to_folder_file(sound_id)
    return f"se/se{folder}/se{file}{ext}"


@lru_cache(maxsize=1)
def music_titles() -> Dict[int, str]:
    """music id -> song title (e.g. 109 -> 'Ronfaure'). Near-complete."""
    return _load_titles("MusicInfo.xml")


@lru_cache(maxsize=1)
def sfx_titles() -> Dict[int, str]:
    """sound id -> effect title, where pol-utils named it (partial)."""
    return _load_titles("SFXInfo.xml")


@lru_cache(maxsize=1)
def sfx_folder_labels() -> Dict[str, str]:
    """folder digits ('003') -> category label ('Spell Sounds'). Whole-tree."""
    out: Dict[str, str] = {}
    root = _parse(_DATA / "SFXInfo.xml")
    if root is None:
        return out
    for sd in root.iter("subdir"):
        name = sd.get("name") or ""
        digits = re.sub(r"\D", "", name)  # "se003" or stray "129" -> "003"/"129"
        if digits:
            out[digits.zfill(3)] = (sd.text or "").strip()
    return out


def _load_titles(filename: str) -> Dict[int, str]:
    out: Dict[int, str] = {}
    root = _parse(_DATA / filename)
    if root is None:
        return out
    for tr in root.iter("track"):
        tid = tr.get("id")
        title = (tr.findtext("title") or "").strip()
        if not (tid and title):
            continue
        try:
            key = int(tid)
        except ValueError:
            continue
        # Some ids appear twice (canonical zone/battle name first, an alternate
        # sound-test/Mog-House alias later) — keep the first, the useful one.
        out.setdefault(key, title)
    return out


def _parse(path: Path) -> Optional[ET.Element]:
    if not path.is_file():
        return None
    try:
        return ET.parse(path).getroot()
    except ET.ParseError:
        return None


# ── Convenience lookups keyed by sound id ──────────────────────────────────

def music_name(sound_id: int) -> Optional[str]:
    return music_titles().get(sound_id)


def sfx_name(sound_id: int) -> Optional[str]:
    return sfx_titles().get(sound_id)


def sfx_category(sound_id: int) -> Optional[str]:
    folder, _ = sound_id_to_folder_file(sound_id)
    return sfx_folder_labels().get(folder)


def folder_category(folder_digits: str) -> Optional[str]:
    """Category label for a folder given its digits ('003') or 'se003'."""
    digits = re.sub(r"\D", "", folder_digits).zfill(3)
    return sfx_folder_labels().get(digits)

#!/usr/bin/env python3
"""Build a categorised JSON catalog of FFXI audio for a viewer/browser.

Sound effects are grouped by their `seNNN` folder — which is the game's own
category system (Spell Sounds, Combat Sounds, Skillchain Sounds, Monster SFX,
Footstep Effects, …, from SFXInfo). Music is grouped by sound root, with each
track carrying its title from MusicInfo. Every file record includes the relative
`.wav` path a batch run writes, so a viewer can load audio + metadata together.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Optional

from xi.audio import xi_core as core
from xi.audio import xi_names as names


def _file_record(kind: core.Kind, entry: core.AudioEntry,
                 header: Optional[core.AudioHeader]) -> dict:
    sid = header.id if header else None
    rel_wav = f"{entry.root}/{entry.rel.with_suffix('.wav').as_posix()}"
    rel_src = f"{entry.root}/{entry.rel.as_posix()}"

    if sid is not None:
        title = names.music_name(sid) if kind is core.MUSIC else names.sfx_name(sid)
        category = names.sfx_category(sid) if kind is core.SFX else None
    else:
        title = category = None

    decodable = header is not None and header.sample_format in (core.FMT_ADPCM, core.FMT_PCM)
    return {
        "id": sid,
        "file": entry.stem,
        "root": entry.root,
        "wav": rel_wav,
        "src": rel_src,
        "title": title,
        "category": category,
        "format": header.format_name if header else None,
        "channels": header.channels if header else None,
        "sample_rate": header.sample_rate if header else None,
        "duration": round(header.duration_sec, 3) if decodable else None,
        "looped": header.looped if header else None,
    }


def _group_key_label(kind: core.Kind, entry: core.AudioEntry):
    """(key, label) for the group an entry belongs to."""
    if kind is core.SFX:
        # entry.rel is e.g. 'se005/se005048.spw' → folder digits '005'
        folder = entry.rel.parts[0] if entry.rel.parts else ""
        digits = re.sub(r"\D", "", folder).zfill(3)
        return f"se{digits}", (names.folder_category(digits) or f"se{digits}")
    return entry.root, entry.root  # music: group by sound root


def build_catalog(kind: core.Kind, entries) -> dict:
    """Catalog dict: {kind, count, formats, groups:[{key,label,count,files:[…]}]}.

    `entries` are `core.AudioEntry` (from `core.list_entries`). Headers are read
    (header-only) to attach id/title/format/duration to each file."""
    groups: dict = {}
    fmt_counts: Counter = Counter()

    for e in entries:
        try:
            header = core.parse_header_file(e.path)
        except (core.AudioError, OSError):
            header = None
        rec = _file_record(kind, e, header)
        fmt_counts[rec["format"] or "unparseable"] += 1
        gkey, glabel = _group_key_label(kind, e)
        g = groups.setdefault(gkey, {"key": gkey, "label": glabel, "files": []})
        g["files"].append(rec)

    group_list = sorted(groups.values(), key=lambda g: g["key"])
    for g in group_list:
        g["files"].sort(key=lambda r: (r["id"] is None, r["id"] or 0))
        g["count"] = len(g["files"])

    return {
        "kind": kind.name,
        "count": sum(g["count"] for g in group_list),
        "group_count": len(group_list),
        "formats": dict(sorted(fmt_counts.items())),
        "groups": group_list,
    }

#!/usr/bin/env python3
"""Shared helpers for `xi tex` (texture extract / re-import).

Textures are `0x20` sections (DXT or palettized). The list/export/import command
modules build on these helpers; nothing here writes files.
"""

import re
from pathlib import Path

# Re-exported so the command modules import everything from one place.
from xi.entity.anim.xi_export import parse_sections
from xi.entity.mesh.xi_export import resolve_dat_path, parse_texture, SECTION_TYPE_TEXTURE

__all__ = [
    "parse_sections", "resolve_dat_path", "parse_texture", "SECTION_TYPE_TEXTURE",
    "fourcc", "sanitize", "matches", "rom_rel",
]


def fourcc(data: bytes, start: int) -> str:
    return bytes(data[start:start + 4]).decode("latin1")


def sanitize(name: str) -> str:
    """Texture name -> safe filename stem (collapse whitespace to '_')."""
    return re.sub(r"\s+", "_", (name or "").strip()) or "unnamed"


def matches(name: str, cc: str, patterns) -> bool:
    """True if a texture (by name or FourCC) matches any of the given patterns
    (exact or prefix), comparing against the sanitized name, raw name, and FourCC."""
    keys = (sanitize(name), (name or "").strip(), cc)
    return any(k == p or k.startswith(p) for p in patterns for k in keys)


def rom_rel(path: Path) -> str:
    """`.../ROM/1/41.DAT` -> `rom/1/41`; otherwise the file stem. Used to derive the
    default `exports/tex/<rom>/` output dir regardless of DAT kind."""
    parts = list(path.parts)
    for i, p in enumerate(parts):
        if p.upper() == "ROM" and i + 1 < len(parts):
            return "/".join(["rom", *parts[i + 1:]]).rsplit(".", 1)[0].lower()
    return path.stem

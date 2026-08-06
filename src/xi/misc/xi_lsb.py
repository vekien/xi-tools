"""Parse LSB zone metadata to cross-reference against client FTABLE.

Two sources of truth on the LSB side:
  - sql/zone_settings.sql       -> per-zone server config (name is auth)
  - scripts/enum/zone.lua       -> xi.zone.NAME = id (used everywhere in lua)

A zone is considered "known to LSB" when its zone_settings row has a real name
(not 'unknown'/'none'/empty). The lua enum tracks the same set so generated
scaffolding can extend it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PLACEHOLDER_NAMES = {"unknown", "none", "", "?"}


def lsb_root() -> Path:
    """Locate the server (LandSandBoat) checkout via the LSB_DIR env var.

    No hardcoded fallbacks — point LSB_DIR at your checkout.
    """
    env = os.environ.get("LSB_DIR")
    if not env:
        raise FileNotFoundError(
            "LSB_DIR is not set. Point it at your server checkout, e.g.\n"
            "    LSB_DIR=D:\\path\\to\\your\\server-checkout"
        )
    root = Path(env)
    if not (root / "sql" / "zone_settings.sql").exists():
        raise FileNotFoundError(
            f"LSB_DIR does not look like a server checkout (no sql/zone_settings.sql): {root}"
        )
    return root.resolve()


@dataclass(frozen=True, slots=True)
class LsbZone:
    zone_id: int
    name: str       # raw value from SQL (may be 'unknown'/'none'/placeholder)
    is_placeholder: bool

    @property
    def script_dir_name(self) -> str:
        """LSB uses underscores in folder names (e.g. Aht_Urhgan_Whitegate)."""
        return self.name.replace(" ", "_")


_ROW_RE = re.compile(
    r"INSERT\s+INTO\s+`?zone_settings`?\s+VALUES\s*\(\s*"
    r"(\d+)\s*,\s*\d+\s*,\s*'[^']*'\s*,\s*\d+\s*,\s*'([^']*)'",
    re.IGNORECASE,
)


def load_lsb_zones(lsb_root_path: Path | None = None) -> dict[int, LsbZone]:
    """Return {zone_id: LsbZone} parsed from zone_settings.sql."""
    root = lsb_root_path or lsb_root()
    sql = (root / "sql" / "zone_settings.sql").read_text(encoding="utf-8", errors="replace")
    out: dict[int, LsbZone] = {}
    for m in _ROW_RE.finditer(sql):
        zid = int(m.group(1))
        name = m.group(2).strip()
        out[zid] = LsbZone(
            zone_id=zid,
            name=name,
            is_placeholder=name.lower() in PLACEHOLDER_NAMES,
        )
    return out


_ENUM_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(\d+)\s*,?", re.MULTILINE)


def load_lsb_zone_enum(lsb_root_path: Path | None = None) -> dict[int, str]:
    """Return {zone_id: ENUM_NAME} from scripts/enum/zone.lua."""
    root = lsb_root_path or lsb_root()
    src = (root / "scripts" / "enum" / "zone.lua").read_text(encoding="utf-8", errors="replace")
    # Only capture within the xi.zone = { ... } block to avoid stray matches.
    block_start = src.find("xi.zone")
    if block_start < 0:
        return {}
    block = src[block_start:]
    return {int(m.group(2)): m.group(1) for m in _ENUM_RE.finditer(block)}


def existing_zone_script_dirs(lsb_root_path: Path | None = None) -> set[str]:
    """Return the set of zone script directory names that already exist on disk."""
    root = lsb_root_path or lsb_root()
    zones_dir = root / "scripts" / "zones"
    if not zones_dir.is_dir():
        return set()
    return {p.name for p in zones_dir.iterdir() if p.is_dir()}

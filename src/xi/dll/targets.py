"""Known POL1-packed client DLLs and default path resolution."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from xi.xi_config import FFXI_DIR, XI_TOOLS_DIR


@dataclass(frozen=True)
class DllTarget:
    """One client DLL we know how to unpack/pack."""

    key: str
    display: str
    filename: str
    # Paths relative to FFXI_DIR (game install root).
    ffxi_relpaths: tuple[str, ...] = ()
    # Paths relative to FFXI_DIR.parent (e.g. Game/ or SquareEnix/).
    parent_relpaths: tuple[str, ...] = ()
    # Paths relative to FFXI_DIR.parent.parent (e.g. catseyexi-client root).
    grandparent_relpaths: tuple[str, ...] = ()
    description: str = ""

    @property
    def misc_packed(self) -> Path:
        return Path(XI_TOOLS_DIR) / "misc" / self.filename

    @property
    def misc_unpacked(self) -> Path:
        stem = Path(self.filename).stem
        return Path(XI_TOOLS_DIR) / "misc" / f"{stem}_unpacked.dll"

    @property
    def misc_repacked(self) -> Path:
        stem = Path(self.filename).stem
        return Path(XI_TOOLS_DIR) / "misc" / f"{stem}_repacked.dll"

    def candidate_packed_paths(self) -> list[Path]:
        out: list[Path] = []
        if FFXI_DIR:
            base = Path(FFXI_DIR)
            for rel in self.ffxi_relpaths:
                out.append(base / rel)
            parent = base.parent
            for rel in self.parent_relpaths:
                out.append(parent / rel)
            gp = parent.parent
            for rel in self.grandparent_relpaths:
                out.append(gp / rel)
        out.append(self.misc_packed)
        # de-dupe preserving order
        seen: set[str] = set()
        uniq: list[Path] = []
        for p in out:
            key = str(p).lower()
            if key not in seen:
                seen.add(key)
                uniq.append(p)
        return uniq

    def resolve_packed(self) -> Path | None:
        for p in self.candidate_packed_paths():
            if p.is_file():
                return p
        return None


TARGETS: dict[str, DllTarget] = {
    "ffximain": DllTarget(
        key="ffximain",
        display="FFXiMain.dll",
        filename="FFXiMain.dll",
        ffxi_relpaths=("FFXiMain.dll",),
        description="Main FFXI client (inventory, rendering, net). POL1-packed.",
    ),
    "polcore": DllTarget(
        key="polcore",
        display="polcore.dll",
        filename="polcore.dll",
        # Common layouts: next to FFXI under Game/, or under SquareEnix/.
        parent_relpaths=(
            "PlayOnlineViewer/viewer/com/polcore.dll",
            r"PlayOnlineViewer\viewer\com\polcore.dll",
        ),
        grandparent_relpaths=(
            "PlayOnlineViewer/viewer/com/polcore.dll",
            "Game/PlayOnlineViewer/viewer/com/polcore.dll",
        ),
        description="PlayOnline COM host (IPOLCoreCom). POL1-packed; preferred base 0x10000000.",
    ),
    "app": DllTarget(
        key="app",
        display="app.dll",
        filename="app.dll",
        parent_relpaths=(
            "PlayOnlineViewer/viewer/com/app.dll",
            r"PlayOnlineViewer\viewer\com\app.dll",
        ),
        grandparent_relpaths=(
            "PlayOnlineViewer/viewer/com/app.dll",
            "Game/PlayOnlineViewer/viewer/com/app.dll",
        ),
        description="PlayOnline Viewer UI module. POL1-packed (same family).",
    ),
}


def get_target(key: str) -> DllTarget:
    try:
        return TARGETS[key.lower()]
    except KeyError as e:
        known = ", ".join(sorted(TARGETS))
        raise KeyError(f"unknown dll target {key!r} (known: {known})") from e

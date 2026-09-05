"""Shared fixtures: ``root`` = the game folder (FFXI_DIR from the environment or .env)."""
import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def root() -> Path:
    d = os.environ.get("FFXI_DIR")
    if not d:
        env = Path(__file__).resolve().parents[1] / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("FFXI_DIR="):
                    d = line.split("=", 1)[1].strip().strip('"')
    if not d or not Path(d).exists():
        pytest.skip("FFXI_DIR (game folder) not available")
    return Path(d)


ZONE_EVENT = "ROM/21/52.DAT"     # Ru'Lude Gardens: the compile tests build on its pristine copy
ZONE_DIALOG = "ROM/25/52.DAT"


def _pristine(root: Path, rel: str) -> bytes:
    p = root / rel
    base = Path(str(p) + ".base")
    return (base if base.exists() else p).read_bytes()


@pytest.fixture(scope="session")
def ev_bytes(root: Path) -> bytes:
    return _pristine(root, ZONE_EVENT)


@pytest.fixture(scope="session")
def dl_bytes(root: Path) -> bytes:
    return _pristine(root, ZONE_DIALOG)

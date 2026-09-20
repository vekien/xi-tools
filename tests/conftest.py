"""Shared fixtures: ``root`` = the game folder (FFXI_DIR from the environment or .env).

FFXI_LEGACY_DIR / FFXI_RETAIL_DIR (optional) point the item-format tests at a
pre-September-2026 client and a current retail client respectively."""
import os
from pathlib import Path

import pytest


def _env_from_dotenv(key: str) -> str | None:
    """``key`` from the environment, else from the repo ``.env``."""
    d = os.environ.get(key)
    if d:
        return d
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"')
    return None


def pytest_configure(config):
    # Optional second installs for the item-format tests (tests/test_items_reference.py):
    # a pre-September-2026 client (FFXI_LEGACY_DIR) and a current retail one
    # (FFXI_RETAIL_DIR). Surface them from .env the same way FFXI_DIR is.
    for key in ("FFXI_DIR", "FFXI_LEGACY_DIR", "FFXI_RETAIL_DIR"):
        val = _env_from_dotenv(key)
        if val and key not in os.environ:
            os.environ[key] = val


@pytest.fixture(scope="session")
def root() -> Path:
    d = _env_from_dotenv("FFXI_DIR")
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


@pytest.fixture(autouse=True)
def _no_live_server(monkeypatch):
    """No test reaches the real server checkout or database. xi_config loaded XI_DB_*
    and XI_SERVER_DIR from the repo .env at import; drop them, and make
    pymysql.connect refuse."""
    import xi.xi_config as cfg          # first: its import loads .env into os.environ
    for k in ("XI_DB_HOST", "XI_DB_PORT", "XI_DB_USER", "XI_DB_PASSWORD", "XI_DB_NAME", "XI_SERVER_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(cfg, "XI_SERVER_DIR", None, raising=False)
    try:
        import pymysql
    except ImportError:
        return

    def _refuse(*a, **k):
        raise AssertionError("tests never connect to a database")
    monkeypatch.setattr(pymysql, "connect", _refuse)

"""The DATs one ``xi dats build`` reads and writes, as its later steps must see them.

Before it applies them again, a build takes back what the project's zone and database
actions changed last time, newest first. Two actions that grow one zone's dialog table,
or put events on one NPC, then rebuild cleanly: each one gets back the table it
changed. A real build writes each step straight to disk. A dry run can't write, so
its steps are held here. Reads in the same run see them, which means the plan shows
what the build would do.

Also a build's claims: ids an action took in this run that the next one must not take.
In a dry run those aren't in any table yet.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

_held: dict[str, bytes] | None = None
_claims: dict[str, set] | None = None


def _key(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


@contextmanager
def session(dry_run: bool):
    """One build: writes held when ``dry_run``; claims kept either way."""
    global _held, _claims
    was = (_held, _claims)
    _held = {} if dry_run else None
    _claims = {}
    try:
        yield
    finally:
        _held, _claims = was


def read(root, rel: str) -> bytes | None:
    """``rel`` as ``root`` holds it (this run's held copy first); None when it isn't there."""
    from xi.menu.xi_menu_table import dat_path, target_path
    if _held is not None:
        got = _held.get(_key(target_path(root, rel)))
        if got is not None:
            return got
    p = dat_path(root, rel)
    return p.read_bytes() if p.is_file() else None


def write(root, rel: str, data: bytes, dry_run: bool) -> Path | None:
    """Write ``rel`` into ``root`` (a dry run holds it for this run); the path written, or
    None when nothing went to disk."""
    from xi.menu.xi_menu_table import _write, target_path
    if dry_run:
        if _held is not None:
            _held[_key(target_path(root, rel))] = bytes(data)
        return None
    return _write(root, rel, data)


def claim(kind: str, value) -> None:
    if _claims is not None:
        _claims.setdefault(kind, set()).add(value)


def claimed(kind: str) -> set:
    return set((_claims or {}).get(kind) or ())

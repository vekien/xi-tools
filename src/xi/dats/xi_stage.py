"""The DATs one ``xi dats build`` reads and writes, as its later steps must see them.

A real build writes each step straight to disk (a ``--reset`` first puts the tables
back to their ``.base``). A dry run can't write, so its steps, the reset included, are
held here. Reads in the same run see them, which means the plan shows what the build
would do.

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
    """One build: writes held when ``dry_run``; claims kept either way. Inside another
    session (each project of ``xi dats build --list``) it is that one's: the later projects
    see what the earlier ones would write, and don't take the ids they took."""
    global _held, _claims
    if _claims is not None:
        yield
        return
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

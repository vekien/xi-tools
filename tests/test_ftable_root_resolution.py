"""File-id resolution through a DAT root, the way the client does it
(xi.ftable.xi_core.resolve_dat_in_root).

The client resolves a file_id through a ``ROM{n}`` table pair first, then the main
``FTABLE``/``VTABLE``. XIPivot swaps in a pivot folder's copy of a ``ROM{n}`` pair, but
the main pair always comes from the base install — a folder's copy of it is never read.

Verified in game (``!injectaction``) with a job ability, a spell and a weapon skill: each
played its custom DAT when registered only in the pivot folder's ``ROM10`` pair, including
over a main-pair entry still pointing at a retail placeholder, and the same weapon skill
registered only in the pivot folder's main pair played the placeholder. XIPivot's debug
log agrees: it serves ``ROM10/FTABLE10.DAT`` from the pivot folder and never sees
``FTABLE.DAT`` opened.

The allocators pick free slots by asking where a file_id resolves, so these pin that order."""
import struct
from pathlib import Path

import pytest

from xi.ftable.xi_core import forget_tables, resolve_dat, resolve_dat_in_root, root_table_pair

MAIN = 1
CUSTOM_ROM = 10


def _write_pair(root: Path, rom_idx: int, entries: dict[int, tuple[int, int]], size: int = 4096):
    """Create a table pair under ``root`` holding ``{file_id: (subdir, file_idx)}``."""
    ft, vt = (Path(p) for p in root_table_pair(root, rom_idx))
    ft.parent.mkdir(parents=True, exist_ok=True)
    fdata = bytearray(size * 2)
    vdata = bytearray(size)
    for fid, (subdir, file_idx) in entries.items():
        struct.pack_into("<H", fdata, fid * 2, (subdir << 7) | file_idx)
        vdata[fid] = rom_idx
    ft.write_bytes(bytes(fdata))
    vt.write_bytes(bytes(vdata))
    forget_tables()
    return ft, vt


@pytest.fixture
def folders(tmp_path: Path, monkeypatch):
    """A synthetic base install (FFXI_DIR) and a pivot folder beside it."""
    import xi.xi_config as cfg
    game, pivot = tmp_path / "game", tmp_path / "pivot"
    game.mkdir()
    pivot.mkdir()
    monkeypatch.setattr(cfg, "FFXI_DIR", str(game))
    monkeypatch.setattr(cfg, "CUSTOM_ROM_IDX", CUSTOM_ROM)
    return game, pivot


def test_rom_pair_overrides_the_main_pair(folders):
    game, _ = folders
    _write_pair(game, MAIN, {100: (76, 29)})
    _write_pair(game, CUSTOM_ROM, {100: (20, 65)})
    assert resolve_dat_in_root(game, 100) == ("ROM10/20/65.DAT", CUSTOM_ROM)


def test_main_pair_answers_when_the_rom_pair_has_no_entry(folders):
    game, _ = folders
    _write_pair(game, MAIN, {100: (76, 29)})
    _write_pair(game, CUSTOM_ROM, {})
    assert resolve_dat_in_root(game, 100) == ("ROM/76/29.DAT", MAIN)


def test_a_pivot_folders_rom_pair_replaces_the_installs(folders):
    game, pivot = folders
    _write_pair(game, MAIN, {100: (76, 29)})
    _write_pair(game, CUSTOM_ROM, {100: (20, 1)})
    _write_pair(pivot, CUSTOM_ROM, {100: (20, 65)})
    assert resolve_dat_in_root(pivot, 100) == ("ROM10/20/65.DAT", CUSTOM_ROM)
    assert resolve_dat_in_root(game, 100) == ("ROM10/20/1.DAT", CUSTOM_ROM)


def test_a_pivot_folders_main_pair_is_never_read(folders):
    """The weapon skill from the game: registered only in the pivot folder's main pair,
    the client still plays the install's placeholder."""
    game, pivot = folders
    _write_pair(game, MAIN, {100: (76, 29)})
    _write_pair(pivot, MAIN, {100: (20, 21)})
    assert resolve_dat_in_root(pivot, 100) == ("ROM/76/29.DAT", MAIN)


def test_a_pivot_folder_without_rom_tables_reads_the_installs(folders):
    game, pivot = folders
    _write_pair(game, CUSTOM_ROM, {100: (20, 65)})
    assert resolve_dat_in_root(pivot, 100) == ("ROM10/20/65.DAT", CUSTOM_ROM)


def test_unregistered_and_missing_tables_resolve_to_nothing(folders):
    game, pivot = folders
    assert resolve_dat_in_root(game, 100) == (None, None)
    _write_pair(pivot, CUSTOM_ROM, {})
    assert resolve_dat_in_root(pivot, 100) == (None, None)


def test_a_main_only_read_misses_a_rom_registration(folders):
    """Why this matters: reading the main pair alone reports a live custom slot as free
    (or as its retail placeholder), so an allocator would hand it out twice."""
    game, _ = folders
    ft, vt = _write_pair(game, MAIN, {100: (76, 29)})
    _write_pair(game, CUSTOM_ROM, {100: (20, 65)})
    assert resolve_dat(ft.read_bytes(), vt.read_bytes(), 100)[0] == "ROM/76/29.DAT"
    assert resolve_dat_in_root(game, 100)[0] == "ROM10/20/65.DAT"


def test_a_file_id_past_the_table_end_resolves_to_nothing(folders):
    game, _ = folders
    _write_pair(game, CUSTOM_ROM, {100: (20, 65)}, size=128)
    assert resolve_dat_in_root(game, 9999) == (None, None)


def test_a_rewritten_table_is_read_again(folders):
    game, _ = folders
    _write_pair(game, CUSTOM_ROM, {})
    assert resolve_dat_in_root(game, 100) == (None, None)
    _write_pair(game, CUSTOM_ROM, {100: (20, 65)})
    assert resolve_dat_in_root(game, 100) == ("ROM10/20/65.DAT", CUSTOM_ROM)


@pytest.mark.parametrize("rom_idx,expected", [
    (MAIN, ("FTABLE.DAT", "VTABLE.DAT")),
    (CUSTOM_ROM, ("FTABLE10.DAT", "VTABLE10.DAT")),
])
def test_table_pair_paths(tmp_path, rom_idx, expected):
    ft, vt = (Path(p) for p in root_table_pair(tmp_path, rom_idx))
    assert (ft.name, vt.name) == expected
    assert ft.parent == (tmp_path if rom_idx == MAIN else tmp_path / f"ROM{rom_idx}")

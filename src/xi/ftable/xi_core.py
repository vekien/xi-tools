import functools
import os
import struct
from xi.xi_config import FFXI_DIR, editable_dat, read_path_for


def ftable_path(rom_idx: int) -> str:
    if rom_idx == 1:
        return os.path.join(FFXI_DIR, 'FTABLE.DAT')
    return os.path.join(FFXI_DIR, f'ROM{rom_idx}', f'FTABLE{rom_idx}.DAT')


def vtable_path(rom_idx: int) -> str:
    if rom_idx == 1:
        return os.path.join(FFXI_DIR, 'VTABLE.DAT')
    return os.path.join(FFXI_DIR, f'ROM{rom_idx}', f'VTABLE{rom_idx}.DAT')


def load_tables(rom_idx: int):
    # Read the live tables (expanded/injected edits are in place).
    ft = read_path_for(ftable_path(rom_idx))
    vt = read_path_for(vtable_path(rom_idx))
    if not os.path.exists(ft) or not os.path.exists(vt):
        return None
    with open(ft, 'rb') as f:
        fdata = bytearray(f.read())
    with open(vt, 'rb') as f:
        vdata = bytearray(f.read())
    return fdata, vdata


def resolve_dat(fdata, vdata, file_id: int):
    if file_id * 2 + 2 > len(fdata) or file_id >= len(vdata):
        return None, None
    ft_val = struct.unpack_from('<H', fdata, file_id * 2)[0]
    vt_val = vdata[file_id]
    # Registration is gated by the VTABLE version byte only (matches xim's
    # FileTableManager.getFilePath). ft_val == 0 is a valid entry: ROMx/0/0.DAT
    # (subdir 0, file 0) — must NOT be treated as empty.
    if vt_val == 0:
        return None, None
    subdir   = ft_val >> 7
    file_idx = ft_val & 0x7F
    dat = f'ROM/{subdir}/{file_idx}.DAT' if vt_val == 1 else f'ROM{vt_val}/{subdir}/{file_idx}.DAT'
    return dat, vt_val


def root_table_pair(root, rom_idx: int):
    """The (FTABLE, VTABLE) paths for ``rom_idx`` under a DAT root."""
    root = str(root)
    if rom_idx == 1:
        return os.path.join(root, 'FTABLE.DAT'), os.path.join(root, 'VTABLE.DAT')
    return (os.path.join(root, f'ROM{rom_idx}', f'FTABLE{rom_idx}.DAT'),
            os.path.join(root, f'ROM{rom_idx}', f'VTABLE{rom_idx}.DAT'))


@functools.lru_cache(maxsize=8)
def _table_bytes(path: str, mtime_ns: int, size: int) -> bytes:
    # A build asks where hundreds of file_ids resolve; the tables are read once per
    # version. Writers call forget_tables(), since a coarse mtime can miss an edit.
    with open(path, 'rb') as f:
        return f.read()


def forget_tables() -> None:
    """Drop the cached table bytes after writing a table."""
    _table_bytes.cache_clear()


def resolve_dat_in_root(root, file_id: int, rom_idx: int = None):
    """What the client loads for ``file_id`` when it reads DATs through ``root`` — the
    base install, or an XIPivot folder such as FFXI_PIVOT_DIR — as (dat, rom).

    The client resolves a file_id through the ROM{n} pair first and falls back to the
    main FTABLE/VTABLE. XIPivot swaps in a folder's own copy of a ROM{n} pair, but the
    main pair always comes from the base install: a folder's copy of it is never read
    (tested in game with !injectaction, and XIPivot's debug log never shows FTABLE.DAT
    being opened). So the ROM{n} pair is ``root``'s own, or the install's when ``root``
    has none, and the main pair is always the install's. Reading a main pair alone
    misses what a ROM{n} pair registers, and an allocator then hands a live slot out
    again.
    """
    import xi.xi_config as cfg
    if rom_idx is None:
        rom_idx = cfg.CUSTOM_ROM_IDX
    rom_pair = root_table_pair(root, rom_idx)
    if not all(os.path.exists(p) for p in rom_pair):
        rom_pair = root_table_pair(cfg.FFXI_DIR, rom_idx)
    for ft, vt in (rom_pair, root_table_pair(cfg.FFXI_DIR, 1)):
        if not (os.path.exists(ft) and os.path.exists(vt)):
            continue
        sf, sv = os.stat(ft), os.stat(vt)
        dat, rom = resolve_dat(_table_bytes(ft, sf.st_mtime_ns, sf.st_size),
                               _table_bytes(vt, sv.st_mtime_ns, sv.st_size), file_id)
        if dat is not None:
            return dat, rom
    return None, None


def patch_table(ft_path: str, vt_path: str,
                file_id: int, ftable_val: int, vtable_val: int,
                dry_run: bool = False):
    # Patch the table in place (.base backup on first edit); fresh=False so
    # earlier edits (e.g. an expanded table, or prior injects) are preserved.
    out_ft = read_path_for(ft_path) if dry_run else editable_dat(ft_path, fresh=False)
    out_vt = read_path_for(vt_path) if dry_run else editable_dat(vt_path, fresh=False)
    with open(out_ft, 'rb') as f:
        fdata = bytearray(f.read())
    with open(out_vt, 'rb') as f:
        vdata = bytearray(f.read())
    struct.pack_into('<H', fdata, file_id * 2, ftable_val)
    vdata[file_id] = vtable_val
    if not dry_run:
        with open(out_ft, 'wb') as f:
            f.write(fdata)
        with open(out_vt, 'wb') as f:
            f.write(vdata)
        forget_tables()


def all_tables():
    for idx in range(1, 11):
        result = load_tables(idx)
        if result:
            yield idx, result[0], result[1]


def load_all_tables() -> dict:
    """Load all ROM tables into a dict keyed by rom_idx. Reuse across multiple scans."""
    tables = {}
    for idx in range(1, 11):
        result = load_tables(idx)
        if result:
            tables[idx] = result
    return tables


def scan_file_ids(file_ids, tables: dict | None = None) -> list[dict]:
    """
    Scan an iterable of file_ids against ROM tables.
    Returns a list of dicts: {file_id, rom, dat} for every file_id that resolves.
    Loads tables automatically if not provided.
    """
    if tables is None:
        tables = load_all_tables()
    entries = []
    for file_id in file_ids:
        for _rom_idx, (fdata, vdata) in sorted(tables.items()):
            dat, vt_val = resolve_dat(fdata, vdata, file_id)
            if dat:
                entries.append({'file_id': file_id, 'rom': vt_val, 'dat': dat})
                break
    return entries

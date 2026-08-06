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

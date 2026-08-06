"""Core logic for ``xi mount`` — read/write a complete mount "record".

A mount is assembled from several DATs (see docs/mounts/):

  model        FTABLE file-id ``0x019131 + id`` -> a ROM mount DAT
  mount name   d_msg, index = id, sub[0]        EN ROM/351/84  JP ROM/351/82
  mount help   d_msg, index = id, sub[0]        EN ROM/351/85  JP ROM/351/83
  key item     d_msg, flag-keyed = 3072 + id    EN ROM/175/35  JP ROM/175/34

…plus the server-side key item (``3072 + id``). The key-item block layout is
language-specific: EN is n=7 (name=sub4, plural=sub5, desc=sub6); JP is n=3
(name=sub1, desc=sub2, no plural).

All numbers verified against a live client; see docs/mounts/mechanism.md.
"""

import os
import struct

from xi.xi_config import (FFXI_DIR, CUSTOM_ROM_IDX, read_path_for,
                            editable_dat, output_path_for)
from xi.ftable import xi_core as ft
from xi.common import xi_dmsg as D

# ── constants ────────────────────────────────────────────────────────────────
MOUNT_FILE_BASE = 0x019131   # file_id = base + mount_id
KEYITEM_BASE    = 3072       # key item = base + mount_id  (CHOCOBO_COMPANION)
RETAIL_COUNT    = 39         # shipped mounts (ids 0-38)
MENU_CAP        = 64         # 0x0AE mask = 8 bytes
MODEL_CAP       = 255        # uint8 mount id
KEYITEM_FREE    = (3111, 3134)  # free key-item ids (= mount ids 39-62; 63 occupied)

# Where injected mount DATs live in the custom ROM: ROM{N}/<subdir>/<file>.DAT
MOUNT_SUBDIR_BASE = 100      # subdir = base + id//128 ; file = id%128  (id<256)

# Language-dependent d_msg file-ids (from the client's DatIndices.cpp).
MOUNT_NAME = {'en': 0x0D981, 'jp': 0x0D909}
MOUNT_HELP = {'en': 0x0D982, 'jp': 0x0D90A}
KEYITEM    = {'en': 0x0D999, 'jp': 0x0D921}
LANGS      = ('en', 'jp')

# Key-item block sub-string indices per language.
KI_IDX = {
    'en': {'name': 4, 'plural': 5, 'desc': 6},
    'jp': {'name': 1, 'desc': 2},
}
KI_PREFIX = '♪'  # the music-note bullet retail prefixes key-item names with


class MountError(Exception):
    pass


# ── path / table helpers ─────────────────────────────────────────────────────

def file_id_for(mount_id: int) -> int:
    return MOUNT_FILE_BASE + mount_id


def key_item_for(mount_id: int) -> int:
    return KEYITEM_BASE + mount_id


def rom_path_for_id(file_id: int):
    """Resolve a file-id to its ROM-relative DAT path, or None if unregistered."""
    for _idx, (fdata, vdata) in sorted(ft.load_all_tables().items()):
        dat, _ = ft.resolve_dat(fdata, vdata, file_id)
        if dat:
            return dat
    return None


def _abs_for(rom_rel: str) -> str:
    return os.path.join(FFXI_DIR, *rom_rel.split('/'))


def resolve_input_dat(path: str) -> str:
    """Resolve a ``--dat`` argument. Tries it as given (absolute / CWD-relative),
    then as a ROM-relative path under FFXI_DIR.
    So `--dat ROM10/10/1.DAT` finds it in the game install without a full path."""
    rel = path.replace('\\', '/').split('/')
    for cand in (os.path.expanduser(path),
                 os.path.join(FFXI_DIR, *rel)):
        if os.path.isfile(cand):
            return cand
    raise MountError(f'--dat {path!r} not found (looked in CWD, FFXI_DIR)')


def _dmsg_path(file_id: int) -> str:
    rel = rom_path_for_id(file_id)
    if rel is None:
        raise MountError(f'string table file-id 0x{file_id:05X} is not registered '
                         '(wrong client / DatIndices mismatch)')
    return _abs_for(rel)


def load_table(file_id: int, bitmask: int) -> D.DmsgTable:
    """Parse a d_msg string table by its file-id (mirror-aware read)."""
    path = read_path_for(_dmsg_path(file_id))
    with open(path, 'rb') as f:
        return D.parse(f.read(), bitmask)


def save_table(file_id: int, table: D.DmsgTable, dry_run: bool = False) -> str:
    out = _dmsg_path(file_id) if dry_run else str(editable_dat(_dmsg_path(file_id), fresh=False))
    if not dry_run:
        with open(out, 'wb') as f:
            f.write(D.serialize(table))
    return out


def find_ki_block(table: D.DmsgTable, key_item: int):
    """Return (index, block) for the key-item whose sub[0] marker == key_item."""
    for i, blk in enumerate(table.blocks):
        if D.get_marker(blk, 0) == key_item:
            return i, blk
    return None, None


def mount_ki_insert_pos(table: D.DmsgTable, key_item: int) -> int:
    """Position a companion key item inside the Mounts category.

    The retail key-item DAT stores category separators as footer rows: mount
    companion rows appear immediately before the next marker-0 separator.
    """
    start, _ = find_ki_block(table, KEYITEM_BASE)
    if start is None:
        raise MountError(f'no first mount key-item block (id {KEYITEM_BASE})')

    end = None
    for pos in range(start + 1, table.num):
        if D.get_marker(table.blocks[pos], 0) == 0:
            end = pos
            break
    if end is None:
        raise MountError('no Mounts separator after mount key-item blocks')

    for pos in range(start, end):
        marker = D.get_marker(table.blocks[pos], 0)
        if marker > key_item:
            return pos
    return end


# ── reading a record ─────────────────────────────────────────────────────────

def read_record(mount_id: int, cache: dict | None = None) -> dict:
    """Assemble a mount record. ``cache`` (file_id -> DmsgTable) avoids re-parsing
    the big tables when reading many mounts (list)."""
    def tbl(fid, bm):
        if cache is not None and fid in cache:
            return cache[fid]
        t = load_table(fid, bm)
        if cache is not None:
            cache[fid] = t
        return t

    fid = file_id_for(mount_id)
    rec = {
        'id': mount_id,
        'file_id': fid,
        'file_id_hex': f'0x{fid:05X}',
        'key_item': key_item_for(mount_id),
        'model_dat': rom_path_for_id(fid),
    }
    rec['occupied'] = rec['model_dat'] is not None

    for lang in LANGS:
        t = tbl(MOUNT_NAME[lang], 0)
        rec[f'name_{lang}'] = D.get_text(t.blocks[mount_id], 0) if mount_id < t.num else ''
        t = tbl(MOUNT_HELP[lang], 0)
        rec[f'help_{lang}'] = D.get_text(t.blocks[mount_id], 0) if mount_id < t.num else ''
        t = tbl(KEYITEM[lang], 0xFF)
        _, blk = find_ki_block(t, rec['key_item'])
        idx = KI_IDX[lang]
        rec[f'ki_name_{lang}'] = D.get_text(blk, idx['name']) if blk is not None else ''
        rec[f'ki_desc_{lang}'] = D.get_text(blk, idx['desc']) if blk is not None else ''
    return rec


def is_real_mount(rec: dict) -> bool:
    """A real mount = has a model and an English name."""
    return rec['occupied'] and bool(rec['name_en'])


# ── writing the model ────────────────────────────────────────────────────────

def model_dest(mount_id: int):
    """(subdir, file_idx, rom_rel) for where an injected mount DAT is placed."""
    subdir = MOUNT_SUBDIR_BASE + mount_id // 128
    file_idx = mount_id % 128
    return subdir, file_idx, f'ROM{CUSTOM_ROM_IDX}/{subdir}/{file_idx}.DAT'


def rom_rel_of(abs_path: str):
    """ROM-relative path ('ROM10/10/1.DAT') for a DAT living under FFXI_DIR,
    else None."""
    ap = os.path.abspath(abs_path)
    b = os.path.abspath(FFXI_DIR)
    if ap == b or ap.startswith(b + os.sep):
        return os.path.relpath(ap, b).replace('\\', '/')
    return None


def _parse_rom_rel(rom_rel: str):
    """('ROM10/10/1.DAT') -> (rom_idx, subdir, file_idx)."""
    parts = rom_rel.split('/')
    if len(parts) != 3 or not parts[0].startswith('ROM') or not parts[2].upper().endswith('.DAT'):
        raise MountError(f'{rom_rel!r} is not a ROM{{n}}/<subdir>/<file>.DAT path')
    rom_idx = 1 if parts[0] == 'ROM' else int(parts[0][3:])
    subdir = int(parts[1])
    file_idx = int(os.path.splitext(parts[2])[0])
    if not (0 <= file_idx <= 0x7F and 0 <= subdir <= 0x1FF):
        raise MountError(f'{rom_rel}: file must be 0-127 and subdir 0-511 to encode in FTABLE')
    return rom_idx, subdir, file_idx


def register_model(mount_id: int, dat_src: str | None, dry_run: bool = False) -> dict:
    """Register the mount's file-id in the base + custom FTABLE/VTABLE.

    Auto-detects placement: if ``dat_src`` already lives inside the ROM tree as a
    ``ROM{n}/<subdir>/<file>.DAT`` (e.g. a model you placed at ROM10/10/1.DAT),
    the file-id is pointed there **in place** (no copy). Otherwise the DAT is an
    external file and is copied into the custom ROM (ROM10/100/<id>.DAT)."""
    import shutil
    fid = file_id_for(mount_id)

    in_place = None
    rel = rom_rel_of(dat_src) if dat_src else None
    if rel is not None:
        try:
            in_place = _parse_rom_rel(rel)               # (rom_idx, subdir, file_idx)
        except MountError:
            in_place = None                              # under FFXI_DIR but not a ROM slot

    if in_place is not None:
        rom_idx, subdir, file_idx = in_place
        dest_abs, copied = dat_src, False
    else:
        subdir, file_idx, rel = model_dest(mount_id)
        rom_idx = CUSTOM_ROM_IDX
        dest_abs = str(output_path_for(_abs_for(rel)))   # honours the dats-build redirect
        copied = False
        if dat_src is not None and not dry_run:
            os.makedirs(os.path.dirname(dest_abs), exist_ok=True)
            shutil.copy2(dat_src, dest_abs)
            copied = True

    ftval = (subdir << 7) | file_idx
    ft.patch_table(ft.ftable_path(rom_idx), ft.vtable_path(rom_idx),
                   fid, ftval, rom_idx, dry_run=dry_run)
    ft.patch_table(ft.ftable_path(1), ft.vtable_path(1),
                   fid, ftval, rom_idx, dry_run=dry_run)
    return {'file_id': fid, 'ftval': ftval, 'rom_rel': rel,
            'dest': dest_abs, 'copied': copied, 'in_place': in_place is not None}


# ── writing the strings ──────────────────────────────────────────────────────

def set_mount_name(mount_id: int, lang: str, text: str, *, help_text=False,
                   dry_run: bool = False) -> str:
    """Set the mount name (or help) at index = mount_id, growing + padding the
    sequential table as needed. Returns the written path."""
    fid = (MOUNT_HELP if help_text else MOUNT_NAME)[lang]
    t = load_table(fid, 0)
    if mount_id < t.num:
        t.blocks[mount_id] = bytearray(D.set_text(t.blocks[mount_id], 0, text))
    else:
        tmpl = bytearray(t.blocks[0])                 # clone a real block's shape
        D.ensure_len(t, mount_id)                     # pad lower ids with empties
        t.blocks.append(bytearray(D.set_text(tmpl, 0, text)))
    return save_table(fid, t, dry_run=dry_run)


def set_key_item(mount_id: int, lang: str, name: str, desc: str = '',
                 plural: str | None = None, *, dry_run: bool = False) -> str:
    """Create/replace the key-item block (flag = 3072+id) with the given text.
    EN gets name/plural/desc; JP gets name/desc.

    The block is kept inside the Mounts category so the client files it under
    Key Items > Mounts instead of the final category.
    """
    fid = KEYITEM[lang]
    ki = key_item_for(mount_id)
    t = load_table(fid, 0xFF)
    idx = KI_IDX[lang]
    name = name if name.startswith(KI_PREFIX) else KI_PREFIX + name
    # Always overwrite every text slot the template has, so nothing leaks from it.
    texts = {idx['name']: name}
    if 'plural' in idx:
        texts[idx['plural']] = (plural if plural is not None else name)
    texts[idx['desc']] = ('\n' + desc if desc and not desc.startswith('\n') else desc)
    pos, _ = find_ki_block(t, ki)
    # Template = an existing companion block of the right shape (chocobo=3072).
    _, tmpl = find_ki_block(t, KEYITEM_BASE)
    if tmpl is None:
        raise MountError(f'no template key-item block (id {KEYITEM_BASE}) in {fid:#x}')
    blk = D.make_block_from_template(bytearray(tmpl), ki, texts)
    # Calculate before deleting so replacing 3072 still has the mount anchor.
    insert_pos = mount_ki_insert_pos(t, ki)
    if pos is not None:
        del t.blocks[pos]
        if pos < insert_pos:
            insert_pos -= 1
    t.blocks.insert(insert_pos, bytearray(blk))
    return save_table(fid, t, dry_run=dry_run)


def clear_mount_strings(mount_id: int, dry_run: bool = False) -> list:
    """Blank the mount name/help at index = id and drop the key-item block.
    Returns the list of written paths."""
    written = []
    for lang in LANGS:
        for help_text in (False, True):
            fid = (MOUNT_HELP if help_text else MOUNT_NAME)[lang]
            t = load_table(fid, 0)
            if mount_id < t.num:
                t.blocks[mount_id] = bytearray(D.set_text(t.blocks[mount_id], 0, ''))
                written.append(save_table(fid, t, dry_run=dry_run))
        fid = KEYITEM[lang]
        t = load_table(fid, 0xFF)
        pos, _ = find_ki_block(t, key_item_for(mount_id))
        if pos is not None:
            del t.blocks[pos]
            written.append(save_table(fid, t, dry_run=dry_run))
    return written


# ── server bundle ────────────────────────────────────────────────────────────

def server_bundle(mount_id: int, slug: str, name_en: str) -> str:
    """Lua/notes snippet to wire the mount up server-side."""
    ki = key_item_for(mount_id)
    ENUM = slug.upper().replace(' ', '_').replace('-', '_')
    return f"""\
-- xi mount inject — server wiring for mount {mount_id} "{name_en}"
-- File-id 0x{file_id_for(mount_id):05X} is registered; the model + name DATs are written.
-- Apply these server-side. Key item = {ki} (= 3072 + {mount_id}).
-- Snippets to copy into the files below (not a standalone script).

-- 1) scripts/enum/key_item.lua  (in the COMPANION block, for readability)
{ENUM}_COMPANION = {ki},

-- 2) scripts/enum/mount.lua  (register the id; prefer a real-id check over MOUNT_MAX)
{ENUM} = {mount_id},

-- 3) Grant it (sets the 0x0AE bit AND passes the 0x01a hasKeyItem ride check):
player:addKeyItem({ki})
player:messageSpecial(<zoneID>.text.KEYITEM_OBTAINED, {ki})

-- 4) (recommended) replace the `>= MOUNT_MAX` checks with a registered/owned check
--    so sparse custom ids (this one) validate and the gaps (39-49) reject.
"""

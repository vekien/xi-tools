"""Custom gear: FTABLE/VTABLE expansion + reset (under ``xi ftable``) and
``xi gear inject`` (placing a model DAT at a new model_id).

Examples::

    # One-time: make room for custom gear model_ids (table op)
    xi ftable expand gear            # up to 4095 per slot
    xi ftable expand gear 1000       # smaller window

    # Inject your model at a new model_id
    xi gear inject ROM/33/17 --mid 672 --rom 10 --glb edited.glb

    # Undo everything (restore tables from backup)
    xi ftable reset
"""

import json
import os
import shutil
import struct
import time
from pathlib import Path

import click

from xi.xi_config import (CUSTOM_ROM, CUSTOM_ROM_IDX, FFXI_DIR,
                             read_path_for, editable_dat,
                             MAX_ENTITY_MODELID, MAX_GEAR_MODELID)
from xi.gear.xi_core import (BYTES_PER_TABLE, GROUPS_PER_SLOT, SLOTS)
from xi.ftable.xi_core import (ftable_path, vtable_path, load_tables,
                                  resolve_dat)
from xi.entity.xi_core import MODEL_FILE_OFFSET

# ── Constants ────────────────────────────────────────────────────────────

RACES = [
    'HumeMale', 'HumeFemale', 'ElvaanMale', 'ElvaanFemale',
    'TaruMale', 'TaruFemale', 'Mithra', 'Galka',
]

ARMOR_SLOTS = ['head', 'body', 'hands', 'legs', 'feet']
WEAPON_SLOTS = ['main', 'sub']

# DLL offsets (CatsEyeXI FFXiMain.dll — used by gearpatch addon)
DLL_TABLES_FILE_OFFSET = 0x34C80       # start of HumeMale 432-byte table

# File_id allocation for custom gear in ROM10. Starts ONE SLOT ABOVE the entity
# model range, DERIVED from the entity ceiling so the two can never overlap:
#
#   CUSTOM_GEAR_BASE = MODEL_FILE_OFFSET + MAX_ENTITY_MODELID + 1
#
# (e.g. 98239 + 30000 + 1 = 128240 with the default ceiling). Raise
# MAX_ENTITY_MODELID / XI_MAX_ENTITY_MODELID in xi_config and
# this floor slides up automatically. Each (race, slot) owns one contiguous
# WINDOW of (max_model_id + 1) file_ids; the model_id indexes straight into it:
#
#   file_id = CUSTOM_GEAR_BASE + (race_idx*len(SLOTS) + slot_idx)*window + model_id
#
# `window` (= max_model_id + 1) is fixed when you run `xi ftable expand gear`
# and stored in the state file, so `inject` computes the exact same file_ids later.
CUSTOM_GEAR_BASE = MODEL_FILE_OFFSET + MAX_ENTITY_MODELID + 1

# Per-slot model_id ceiling — a gear MId is a 12-bit field, so 0..4095.
DEFAULT_MAX_MODELID = MAX_GEAR_MODELID
MAX_MODELID_CEILING = 4095

# Existing armor G5 models per race; pointed (not copied) into each window so
# they keep resolving after the group is extended.
ORIGINAL_G5_COUNT = 64

# State file — tracks the expansion and what's been injected
STATE_FILE = 'gear_inject_state.json'

# First model_id you may inject per slot type (custom range start).
CUSTOM_MODEL_START = {
    'face': 32,      # after G0's 32
    'head': 672,     # after retail's 672
    'body': 672,
    'hands': 672,
    'legs': 672,
    'feet': 672,
    'main': 1196,    # G5 skip range
    'sub': 1196,
    'ranged': 256,   # after G0's 256
}


# ── Debug logging ──────────────────────────────────────────────────────────
# Toggled by `--debug` on `xi ftable expand gear`. When on, each phase prints
# an elapsed-since-start timestamp so a slow expansion can be traced to the exact
# file/operation that is taking the time. Off by default (normal output stays clean).
_DEBUG = False
_DEBUG_T0 = 0.0


def _set_debug(on: bool):
    """Enable/disable debug logging and (re)start the elapsed-time clock."""
    global _DEBUG, _DEBUG_T0
    _DEBUG = on
    _DEBUG_T0 = time.perf_counter()


def _dbg(msg: str):
    """Print a timestamped debug line (no-op unless --debug was passed)."""
    if _DEBUG:
        click.echo(click.style(
            f'  [debug +{time.perf_counter() - _DEBUG_T0:8.3f}s] {msg}',
            fg='bright_black'))


# ── File-ID allocation ───────────────────────────────────────────────────

def _window_size(max_model: int) -> int:
    return max_model + 1


def custom_fid(race: str, slot: str, model_id: int, max_model: int) -> int:
    """file_id for a custom gear model. Each (race, slot) owns a window of
    (max_model + 1) file_ids; model_id indexes directly into it."""
    ri = RACES.index(race)
    si = SLOTS.index(slot)
    return CUSTOM_GEAR_BASE + (ri * len(SLOTS) + si) * _window_size(max_model) + model_id


def gear_ftable_target(max_model: int) -> int:
    """FTABLE entry count needed to address every (race, slot) window."""
    return CUSTOM_GEAR_BASE + len(RACES) * len(SLOTS) * _window_size(max_model)


def fid_to_ftable_entry(subdir: int, file_idx: int) -> int:
    """Encode a ROM10 path as an FTABLE uint16 value."""
    return (subdir << 7) | (file_idx & 0x7F)


# ── State management ─────────────────────────────────────────────────────

def _state_path() -> Path:
    return Path(FFXI_DIR) / STATE_FILE


def _load_state() -> dict:
    p = _state_path()
    if p.exists():
        return json.loads(p.read_text())
    # expand_max: per-slot model_id ceiling chosen at expand time (None = not expanded)
    # rom10_next: next free compact ROM10 slot index
    # placed:     {file_id: [subdir, file_idx]} for already-injected models
    return {'setup_done': False, 'expand_max': None, 'rom10_next': 0,
            'placed': {}, 'injected': {}}


def _save_state(state: dict):
    _state_path().write_text(json.dumps(state, indent=2))


# ── ROM10 DAT placement ─────────────────────────────────────────────────

def _rom10_dir() -> Path:
    return Path(FFXI_DIR) / CUSTOM_ROM


ROM10_SUBDIR_BASE = 200          # custom gear DATs start at ROM10/200/
ROM10_MAX_SUBDIR = 511           # uint16 FTABLE entry: subdir = ft_val >> 7


def _slot_to_subdir(index: int) -> tuple[int, int]:
    """Compact ROM10 (subdir, file_idx) for the Nth injected DAT. Placement is
    by injection order (tracked in state), NOT by file_id — so windowed file_ids
    can be large without overflowing the 16-bit FTABLE subdir field."""
    subdir = ROM10_SUBDIR_BASE + index // 128
    if subdir > ROM10_MAX_SUBDIR:
        raise click.ClickException(
            f'ROM10 is full ({(ROM10_MAX_SUBDIR - ROM10_SUBDIR_BASE + 1) * 128} custom DATs max).')
    return subdir, index % 128


def _copy_dat_to_rom10(dat_path: Path, subdir: int, file_idx: int):
    """Copy a DAT to ROM10/<subdir>/<file_idx>.DAT."""
    dest_dir = _rom10_dir() / str(subdir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dat_path, dest_dir / f'{file_idx}.DAT')


def _register_fid_raw(file_id: int, ft_val: int, vt_val: int, dry_run: bool = False):
    """Write a raw (ft_val, vt_val) at file_id across ALL FTABLE/VTABLE pairs."""
    for rom_idx in range(1, 11):
        ft = ftable_path(rom_idx)
        vt = vtable_path(rom_idx)
        if not Path(read_path_for(ft)).exists():
            continue
        ft_real = read_path_for(ft)
        size = os.path.getsize(ft_real)
        if file_id * 2 + 2 > size:
            continue
        if not dry_run:
            from xi.ftable.xi_core import patch_table as _pt
            _pt(ft, vt, file_id, ft_val, vt_val, dry_run=False)


def _register_fid(file_id: int, subdir: int, file_idx: int, dry_run: bool = False):
    """Register a file_id pointing to ROM10 at subdir/file_idx."""
    _register_fid_raw(file_id, fid_to_ftable_entry(subdir, file_idx), CUSTOM_ROM_IDX, dry_run)


def _register_many(entries, dry_run: bool = False):
    """Apply many (file_id, ft_val, vt_val) writes to EVERY FTABLE/VTABLE pair,
    reading and writing each table file only once (fast — vs once per entry)."""
    if not entries:
        return
    _dbg(f'_register_many: applying {len(entries):,} entries to every '
         f'FTABLE/VTABLE pair (ROM 1..10)')
    for rom_idx in range(1, 11):
        ft = ftable_path(rom_idx)
        vt = vtable_path(rom_idx)
        if not Path(read_path_for(ft)).exists():
            _dbg(f'ROM{rom_idx}: FTABLE not present - skipped')
            continue
        t0 = time.perf_counter()
        ftw = Path(read_path_for(ft) if dry_run else editable_dat(ft, fresh=False))
        vtw = Path(read_path_for(vt) if dry_run else editable_dat(vt, fresh=False))
        t_seed = time.perf_counter()
        fdata = bytearray(ftw.read_bytes())
        vdata = bytearray(vtw.read_bytes())
        t_read = time.perf_counter()
        applied = skipped = 0
        for file_id, ft_val, vt_val in entries:
            if file_id * 2 + 2 > len(fdata) or file_id >= len(vdata):
                skipped += 1
                continue
            struct.pack_into('<H', fdata, file_id * 2, ft_val)
            vdata[file_id] = vt_val
            applied += 1
        t_pack = time.perf_counter()
        if not dry_run:
            ftw.write_bytes(fdata)
            vtw.write_bytes(vdata)
        _dbg(f'ROM{rom_idx}: seed {t_seed - t0:.3f}s, '
             f'read {len(fdata) + len(vdata):,}B {t_read - t_seed:.3f}s, '
             f'pack {applied:,} (+{skipped:,} out-of-range) {t_pack - t_read:.3f}s, '
             f'write {time.perf_counter() - t_pack:.3f}s')


def _table_rel_paths():
    yield Path('FTABLE.DAT')
    yield Path('VTABLE.DAT')
    for n in range(2, 11):
        yield Path(f'ROM{n}') / f'FTABLE{n}.DAT'
        yield Path(f'ROM{n}') / f'VTABLE{n}.DAT'


def _mirror_tables_to_pivot(dry_run: bool = False) -> list[str]:
    """Sync FTABLE/VTABLE changes from FFXI_DIR into FFXI_PIVOT_DIR when set.

    XIPivot serves whatever file exists at a given ROM-relative path in the pivot
    folder INSTEAD OF the base install's copy — not a merge. So a stale pivot
    FTABLE/VTABLE silently shadows every expand/inject write here, even though
    the base install's own tables are correct. Only syncs tables the pivot
    folder already has an override for (doesn't create new ones), matching
    whatever subset it was actually set up with (e.g. just base + ROM10)."""
    from xi.xi_config import FFXI_PIVOT_DIR

    if not FFXI_PIVOT_DIR or Path(FFXI_PIVOT_DIR).resolve() == Path(FFXI_DIR).resolve():
        return []
    pivot = Path(FFXI_PIVOT_DIR)
    if not pivot.is_dir():
        return []

    mirrored = []
    for rel in _table_rel_paths():
        dst = pivot / rel
        src = Path(read_path_for(Path(FFXI_DIR) / rel))
        if not dst.exists() or not src.exists():
            continue
        if dry_run:
            mirrored.append(str(dst))
            continue
        if dst.exists() and not (dst.parent / f'{dst.name}.base').exists():
            shutil.copy2(dst, dst.parent / f'{dst.name}.base')
        shutil.copy2(src, dst)
        mirrored.append(str(dst))
    return mirrored


def _point_fid(new_fid: int, src_fid: int, dry_run: bool = False) -> bool:
    """Point new_fid at the SAME DAT as src_fid by duplicating its FTABLE/VTABLE
    entry — no file is copied. Returns True if src_fid was registered."""
    result = load_tables(1)
    if not result:
        return False
    fdata, vdata = result
    if src_fid >= len(vdata) or vdata[src_fid] == 0:
        return False
    ft_val = struct.unpack_from('<H', fdata, src_fid * 2)[0]
    vt_val = vdata[src_fid]
    _register_fid_raw(new_fid, ft_val, vt_val, dry_run)
    return True


# ── FTABLE expansion for gear ────────────────────────────────────────────

def _expand_ftables_for_gear(target: int, dry_run: bool = False):
    """Expand all FTABLE/VTABLE pairs to `target` entries for gear injection."""
    from xi.ftable.xi_expand import _all_table_pairs, _expand_pair, _create_custom_rom

    click.echo(f'\n[*] Expanding FTABLEs to {target:,} entries ...')
    pairs = [p for p in _all_table_pairs(FFXI_DIR) if p[2] != CUSTOM_ROM]
    _dbg(f'_all_table_pairs -> {len(pairs)} non-custom pair(s) to grow, then {CUSTOM_ROM}')
    for ft, vt, label, idx in pairs:
        if not os.path.exists(ft):
            _dbg(f'{label}: FTABLE missing ({ft}) - skipped')
            continue
        t = time.perf_counter()
        _expand_pair(ft, vt, label, target, dry_run, dbg=_dbg)
        _dbg(f'{label}: expand done in {time.perf_counter() - t:.3f}s')

    t = time.perf_counter()
    _create_custom_rom(FFXI_DIR, target, dry_run, dbg=_dbg)
    _dbg(f'{CUSTOM_ROM}: create/expand done in {time.perf_counter() - t:.3f}s')


# ── Setup: expand FTABLEs + copy armor G5 DATs to ROM10 ─────────────────

def _resolve_dat_path(file_id: int) -> Path | None:
    """Resolve a file_id to its full DAT path on disk."""
    for rom_idx in range(1, 11):
        result = load_tables(rom_idx)
        if not result:
            continue
        fdata, vdata = result
        dat_rel, vt_val = resolve_dat(fdata, vdata, file_id)
        if dat_rel:
            full = Path(FFXI_DIR) / dat_rel
            if full.exists():
                return full
    return None


def current_expand_max() -> int | None:
    """The per-(race, slot) model_id ceiling the live FTABLE is actually expanded
    to, or None if gear hasn't been expanded on this install. Derived from the
    ACTUAL table size (not gear_inject_state.json) — a manual reset/restore can
    return the tables to retail size while the state file still claims expanded,
    so the table's own entry count is the authoritative source."""
    tables = load_tables(1)
    entries = (len(tables[0]) // 2) if tables else 0
    slots_per_window = len(RACES) * len(SLOTS)
    if entries <= CUSTOM_GEAR_BASE:
        return None
    return (entries - CUSTOM_GEAR_BASE) // slots_per_window - 1


def dll_expand_max(dll_path: Path | None = None) -> int | None:
    """The per-(race, slot) model_id ceiling FFXiMain.dll's gear group tables are
    patched to, or None if the DLL is at retail (no custom gear window). The DLL
    is what the CLIENT maps model_id → file_id with, so it is the authoritative
    "is this install expanded" source — unlike gear_inject_state.json, which is
    machine-local and never ships with a distributed install. Reads HumeMale/head:
    the expand patch gives every (race, slot) the same window, and G5 is the group
    it points into the custom file_id region. max model = sum of the slot's group
    counts − 1 (model_id is the cumulative index across groups)."""
    dll_path = dll_path or (Path(FFXI_DIR) / 'FFXiMain.dll')
    if not dll_path.exists():
        return None
    off = DLL_TABLES_FILE_OFFSET + SLOTS.index('head') * GROUPS_PER_SLOT * 8
    with open(dll_path, 'rb') as f:
        f.seek(off)
        raw = struct.unpack(f'<{GROUPS_PER_SLOT * 2}I', f.read(GROUPS_PER_SLOT * 8))
    g5_base = raw[(GROUPS_PER_SLOT - 1) * 2]
    if g5_base < CUSTOM_GEAR_BASE:
        return None
    return sum(raw[i * 2 + 1] for i in range(GROUPS_PER_SLOT)) - 1


def gear_dll_patched(dll_path: Path | None = None) -> bool:
    """True if FFXiMain.dll's gear group table is patched for custom model_ids
    (HumeMale/head G5 points into the custom file_id window), False if it's at
    retail — e.g. a client/launcher update replaced the DLL and reverted it.
    Independent of `current_expand_max` (the FTABLE), which a launcher won't touch."""
    dll_path = dll_path or (Path(FFXI_DIR) / 'FFXiMain.dll')
    if not dll_path.exists():
        return False
    off = DLL_TABLES_FILE_OFFSET + SLOTS.index('head') * GROUPS_PER_SLOT * 8 + 5 * 8
    with open(dll_path, 'rb') as f:
        f.seek(off)
        base, _count = struct.unpack('<II', f.read(8))
    return base >= CUSTOM_GEAR_BASE


def expand_gear_tables(max_model: int = DEFAULT_MAX_MODELID,
                       dry_run: bool = False, force: bool = False):
    """Expand the FTABLEs so every (race, slot) has a window of (max_model + 1)
    file_ids, and point each armor slot's G5 originals into its window. No DAT
    files are copied — the new file_ids duplicate the originals' table entries.

    One-time: the window size is fixed once set. To change `max_model` later,
    restore the FTABLEs from backup and re-run.
    """
    if max_model < 1 or max_model > MAX_MODELID_CEILING:
        raise click.ClickException(f'--max must be between 1 and {MAX_MODELID_CEILING}.')

    state = _load_state()
    actual_max = current_expand_max()
    dll_path = Path(FFXI_DIR) / 'FFXiMain.dll'

    # FTABLE already expanded (a launcher update won't touch it). The only thing
    # that may still be missing is the FFXiMain.dll gear-patch — client/launcher
    # updates commonly replace the DLL and revert it. Detect that and re-apply the
    # DLL patch (no need to redo the FTABLE), rather than blocking with "already
    # expanded".
    if actual_max is not None and not force:
        from xi.ffximain.xi_gear_patch import patch_gear_groups
        if gear_dll_patched(dll_path):
            click.echo(f"Already set up: FTABLE expanded (model_id max {actual_max}) and "
                       "FFXiMain.dll gear-patch in place. Nothing to do.")
            return state
        if not dll_path.exists():
            raise click.ClickException(f'FFXiMain.dll not found at {dll_path}.')
        click.echo(f"FTABLE is expanded (model_id max {actual_max}) but FFXiMain.dll's "
                   "gear-patch is MISSING — a client/launcher update likely reset the DLL.")
        click.echo("Re-applying the gear-patch to FFXiMain.dll ...")
        if not dry_run:
            patch_gear_groups(dll_path, actual_max)
            click.echo(click.style(
                "  ✓ Patched FFXiMain.dll — restart the client to reload it.", fg='green'))
        else:
            click.echo("  (dry run — would patch FFXiMain.dll gear groups)")
        return state

    # Self-heal: the state file claims expanded but the tables are at retail size
    # (e.g. you reset/restored by hand). Drop the stale window and continue rather
    # than blocking on a JSON that no longer matches the tables.
    if actual_max is None and state.get('expand_max') is not None:
        click.echo(f"[!] Gear state said expand_max={state['expand_max']}, but the tables are "
                   f"not expanded — clearing stale state and continuing.")
        state.update({'expand_max': None, 'setup_done': False, 'placed': {}, 'injected': {}})

    t_total = time.perf_counter()
    _dbg(f'expand_gear_tables start: max_model={max_model}, dry_run={dry_run}, force={force}')
    _dbg(f'FFXI_DIR={FFXI_DIR}  (edits in place, .base backups)')

    dll_path = Path(FFXI_DIR) / 'FFXiMain.dll'
    if not dll_path.exists():
        raise click.ClickException(f'FFXiMain.dll not found at {dll_path}. Set FFXI_DIR.')
    t = time.perf_counter()
    with open(dll_path, 'rb') as f:
        f.seek(DLL_TABLES_FILE_OFFSET)
        dll_data = f.read(8 * BYTES_PER_TABLE)
    if dll_data[:8] != bytes([0xA8, 0x1B, 0x00, 0x00, 0x20, 0x00, 0x00, 0x00]):
        raise click.ClickException('FFXiMain.dll gear table signature mismatch.')
    _dbg(f'read gear table signature from FFXiMain.dll in {time.perf_counter() - t:.3f}s')

    target = gear_ftable_target(max_model)
    _dbg(f'target FTABLE entries = {target:,}  '
         f'(base {CUSTOM_GEAR_BASE:,} + {len(RACES)} races * {len(SLOTS)} slots * '
         f'{_window_size(max_model):,} window)')
    click.echo(f'Preparing custom gear (model_id up to {max_model} per slot).')
    click.echo('This makes room for your custom models and keeps every existing')
    click.echo('armor piece working. No DAT files are copied. First run can take')
    click.echo('a minute or two on big ranges — please wait.\n')

    click.echo(f'  Step 1/2: growing the lookup tables to {target:,} slots ...')
    t = time.perf_counter()
    _expand_ftables_for_gear(target, dry_run)
    _dbg(f'Step 1/2 (grow tables) total {time.perf_counter() - t:.3f}s')

    # Collect the "keep existing armor working" links (no copies), then write them
    # all at once (fast) instead of rewriting the tables per entry.
    t = time.perf_counter()
    ftbase, vtbase = load_tables(1)
    _dbg(f'load_tables(1) -> FTABLE {len(ftbase):,}B / VTABLE {len(vtbase):,}B in '
         f'{time.perf_counter() - t:.3f}s')
    t = time.perf_counter()
    pointers = []
    for ri, race in enumerate(RACES):
        for slot in ARMOR_SLOTS:
            si = SLOTS.index(slot)
            g5_off = ri * BYTES_PER_TABLE + si * GROUPS_PER_SLOT * 8 + 5 * 8
            orig_base = struct.unpack_from('<I', dll_data, g5_off)[0]
            orig_count = struct.unpack_from('<I', dll_data, g5_off + 4)[0]
            g5_base_model = CUSTOM_MODEL_START[slot] - orig_count
            for i in range(orig_count):
                src = orig_base + i
                if src < len(vtbase) and vtbase[src] != 0:
                    ft_val = struct.unpack_from('<H', ftbase, src * 2)[0]
                    pointers.append((custom_fid(race, slot, g5_base_model + i, max_model),
                                     ft_val, vtbase[src]))

    _dbg(f'built {len(pointers):,} armor pointer links in {time.perf_counter() - t:.3f}s')

    click.echo(f'  Step 2/2: keeping {len(pointers):,} existing armor models working '
               f'(no copies) ...')
    t = time.perf_counter()
    _register_many(pointers, dry_run)
    _dbg(f'Step 2/2 (register links) total {time.perf_counter() - t:.3f}s')

    if not dry_run:
        click.echo(f'\n  Done. Linked {len(pointers):,} existing models, copied 0 files.')
        state['expand_max'] = max_model
        state['setup_done'] = True
        state.setdefault('rom10_next', 0)
        state.setdefault('placed', {})
        _save_state(state)

    # Step 3: patch FFXiMain.dll's gear groups so the CLIENT actually resolves the
    # custom model_ids into the windows we just made. Without this the client's
    # group walker stops at retail counts and never finds a custom model.
    from xi.ffximain.xi_gear_patch import patch_gear_groups
    click.echo('\n  Step 3/3: patching FFXiMain.dll gear groups (so the client can '
               'resolve custom model_ids) ...')
    if not dry_run:
        patch_gear_groups(dll_path, max_model)
        click.echo(click.style(
            '  ✓ Patched FFXiMain.dll — restart the client to reload it.', fg='green'))
    else:
        click.echo('  (dry run — would patch FFXiMain.dll gear groups)')
    _dbg(f'expand_gear_tables total wall time {time.perf_counter() - t_total:.3f}s')
    return state


# ── Unified expand (entity buffer + gear windows in one pass) ──────────────

def expand_all(do_gear: bool = True, do_backup: bool = True,
               dry_run: bool = False, debug: bool = False, force: bool = False,
               pivot: bool = True):
    """Provision both custom buffers in a single pass.

    The gear table target is a strict superset of the entity buffer
    (``gear_target = entity_target + 72*window``), so when gear is enabled this
    grows the tables once to the gear target — which automatically provisions the
    entity region as empty slots below ``CUSTOM_GEAR_BASE`` — and then writes the
    gear armor pointers. With ``do_gear=False`` only the entity buffer is grown.

    Ceilings come from xi_config (MAX_ENTITY_MODELID / MAX_GEAR_MODELID). No
    entity models are registered here — that's what ``xi entity inject`` does;
    expand only reserves the slots.
    """
    from xi.entity.xi_core import MODEL_SAFE_START
    from xi.ftable.xi_expand import (_all_table_pairs, _expand_pair,
                                        _create_custom_rom, _backup_all,
                                        max_table_entries, verify_uniform_tables,
                                        sync_pivot_from_base, pivot_root)
    _set_debug(debug)
    t_total = time.perf_counter()

    entity_target = MODEL_FILE_OFFSET + MAX_ENTITY_MODELID + 1
    gear_target = gear_ftable_target(MAX_GEAR_MODELID)

    # Never shrink below the largest existing table (base OR pivot) — grow
    # everything to a single uniform size (client crashes if the tables differ).
    existing_max = max_table_entries(include_pivot=pivot)
    entity_target = max(entity_target, existing_max)
    gear_target = max(gear_target, existing_max)

    click.echo('=' * 62)
    click.echo('  FFXI FTABLE/VTABLE Expander (unified)')
    click.echo('=' * 62)
    click.echo(f'  Entity buffer : modelid {MODEL_SAFE_START:,}-{MAX_ENTITY_MODELID:,}  '
               f'(file_id up to {entity_target - 1:,})')
    if do_gear:
        click.echo(f'  Gear windows  : modelid 0-{MAX_GEAR_MODELID:,} per (race, slot)  '
                   f'(file_id {CUSTOM_GEAR_BASE:,}-{gear_target - 1:,})')
        click.echo(f'  Total entries : {gear_target:,}')
    else:
        click.echo(f'  Gear windows  : skipped (--no-gear)')
        click.echo(f'  Total entries : {entity_target:,}')
    click.echo(f'  Dry run       : {dry_run}')
    click.echo('=' * 62)

    if not dry_run and do_backup:
        click.echo('\n[*] Backing up existing tables ...')
        _backup_all(FFXI_DIR, include_pivot=pivot)

    if do_gear:
        # One grow to the gear target covers the entity region for free.
        expand_gear_tables(max_model=MAX_GEAR_MODELID, dry_run=dry_run, force=force)
        final_target = gear_target
    else:
        click.echo(f'\n[*] Growing tables to the entity buffer ({entity_target:,} entries) ...')
        for ft, vt, label, idx in _all_table_pairs(FFXI_DIR):
            if label == CUSTOM_ROM:
                continue
            if not os.path.exists(ft):
                click.echo(f'  {label}: not found - skipped')
                continue
            _expand_pair(ft, vt, label, entity_target, dry_run, dbg=_dbg)
        _create_custom_rom(FFXI_DIR, entity_target, dry_run, dbg=_dbg)
        final_target = entity_target

    if pivot and pivot_root():
        click.echo(f'\n[*] Syncing pivot pack tables ({pivot_root()}) ...')
        if not sync_pivot_from_base(dry_run):
            click.echo('  (pivot tables already in sync)')

    if not dry_run:
        verify_uniform_tables(include_pivot=pivot)

    click.echo()
    click.echo('=' * 62)
    if dry_run:
        click.echo('  [DRY RUN] No files written.')
    else:
        click.echo('  Expansion complete.')
        click.echo(f'  Entity inject range : modelid {MODEL_SAFE_START:,} - {MAX_ENTITY_MODELID:,}')
        if do_gear:
            click.echo(f'  Gear inject range   : per-slot modelid up to {MAX_GEAR_MODELID:,}')
    click.echo('=' * 62)
    _dbg(f'expand_all total wall time {time.perf_counter() - t_total:.3f}s')


# ── Model injection ──────────────────────────────────────────────────────

def _custom_file_id(race: str, slot: str, target_model_id: int, max_model: int) -> int:
    """Map a target custom model_id to its windowed file_id, validating range."""
    start = CUSTOM_MODEL_START[slot]
    if target_model_id < start or target_model_id > max_model:
        raise click.ClickException(
            f'model_id {target_model_id} out of range for {slot} '
            f'(valid: {start}-{max_model}).')
    return custom_fid(race, slot, target_model_id, max_model)


def inject_model(race: str, slot: str, source_model_id: int,
                 target_model_id: int, rom_idx: int,
                 glb_path: Path | None = None,
                 double_sided: bool = True, manual_scale: float = 1.0,
                 rotate_y_deg: float = 0.0, dry_run: bool = False) -> dict:
    """Create a NEW gear model in the custom ROM from a base DAT + an edited mesh.

    Copies the pristine ``(race, slot, source_model_id)`` gear DAT, replaces its
    mesh + textures with the geometry in ``glb_path`` (the skeleton is borrowed
    from the race body DAT for un-skinning and is otherwise left unchanged), then
    places it in the custom ROM and registers ``target_model_id``. With no
    ``glb_path`` the base model is cloned unchanged.
    """
    state = _load_state()
    max_model = state.get('expand_max')
    if max_model is None:
        if not dry_run:
            raise click.ClickException(
                'Gear tables not expanded. Run `xi ftable expand gear` first.')
        max_model = DEFAULT_MAX_MODELID   # preview assumes the default window

    if rom_idx != CUSTOM_ROM_IDX:
        raise click.ClickException(
            f'--rom {rom_idx} unsupported; the custom gear ROM is {CUSTOM_ROM_IDX} '
            f'(ROM{CUSTOM_ROM_IDX}). Set CUSTOM_FTABLE to change it.')

    from xi.gear.xi_export import resolve_gear_dat, race_skeleton_dat
    base_dat = resolve_gear_dat(race, slot, source_model_id)   # pristine structure

    file_id = _custom_file_id(race, slot, target_model_id, max_model)

    # Compact ROM10 placement: reuse the slot if this file_id was injected before,
    # otherwise take the next free slot (by injection order, not file_id).
    placed = state.setdefault('placed', {})
    reuse = placed.get(str(file_id))
    if reuse:
        subdir, file_idx = reuse
    else:
        subdir, file_idx = _slot_to_subdir(state.get('rom10_next', 0))
    rom_path = f'{CUSTOM_ROM}/{subdir}/{file_idx}.DAT'

    click.echo(f'Base:   {base_dat.name}  ({race} {slot} model {source_model_id})')
    click.echo(f'Mesh:   {Path(glb_path).name if glb_path else "(none — clone base unchanged)"}')
    click.echo(f'Target: model_id={target_model_id}  file_id={file_id}  ROM{rom_idx}  {rom_path}')

    if dry_run:
        click.echo(click.style('Dry run - nothing written.', fg='cyan'))
        return {'model_id': target_model_id, 'file_id': file_id, 'rom_path': rom_path,
                'race': race, 'slot': slot}

    # Copy the base DAT, then inject the edited mesh + textures into the copy.
    data = base_dat.read_bytes()
    if glb_path:
        from xi.entity.mesh.xi_import import build_imported_dat
        skel = race_skeleton_dat(race).read_bytes()
        data, mstats = build_imported_dat(
            data, Path(glb_path), skeleton_data=skel, double_sided=double_sided,
            manual_scale=manual_scale, rotate_y_deg=rotate_y_deg)
        click.echo(f'  Injected mesh: {mstats["vertices"]} verts, '
                   f'{mstats["triangles"]} tris, +{mstats["textures"]} new / '
                   f'{mstats["textures_replaced"]} replaced textures')

    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.DAT', delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        tmp_path.write_bytes(data)
        _copy_dat_to_rom10(tmp_path, subdir, file_idx)
    finally:
        tmp_path.unlink(missing_ok=True)

    if not reuse:
        placed[str(file_id)] = [subdir, file_idx]
        state['rom10_next'] = state.get('rom10_next', 0) + 1

    _register_fid(file_id, subdir, file_idx)
    click.echo(f'  Registered: file_id={file_id} -> {rom_path}')

    key = f'{race}/{slot}'
    state.setdefault('injected', {}).setdefault(key, [])
    if target_model_id not in state['injected'][key]:
        state['injected'][key].append(target_model_id)
    _save_state(state)

    return {
        'model_id': target_model_id,
        'file_id': file_id,
        'rom_path': rom_path,
        'race': race,
        'slot': slot,
    }


# ── CLI commands ─────────────────────────────────────────────────────────

@click.command('gear')
@click.argument('max_model', type=int, default=DEFAULT_MAX_MODELID, required=False,
                metavar=f'[MAX_MODEL={DEFAULT_MAX_MODELID}]')
@click.option('--dry-run', is_flag=True, help='Show plan without writing.')
@click.option('--force', is_flag=True,
              help='Re-expand even if already expanded (you must restore the FTABLEs first).')
@click.option('--debug', '-v', is_flag=True,
              help='Print timed, per-step diagnostics (which file/operation is slow).')
def gear_expand_cmd(max_model, dry_run, force, debug):
    """Expand FTABLE/VTABLE for custom GEAR models, up to MAX_MODEL per slot.

    One-time step. Each (race, slot) gets a window of (MAX_MODEL+1) file_ids and the
    existing armor originals are pointed into it — NO DAT files are copied. The
    window size is fixed once set: to change it later, restore the FTABLEs from
    backup (`xi ftable reset`) and re-run.

    \b
    Examples:
      xi ftable expand gear         # full range, model_id up to 4095 per slot
      xi ftable expand gear 1000    # smaller window (smaller tables)
      xi ftable expand gear --debug # show timed per-step diagnostics
    """
    _set_debug(debug)
    try:
        expand_gear_tables(max_model=max_model, dry_run=dry_run, force=force)
    except click.ClickException:
        raise
    from xi.ftable.xi_expand import sync_pivot_from_base, pivot_root
    if pivot_root():
        click.echo(f'\n[*] Syncing pivot pack tables ({pivot_root()}) ...')
        synced = sync_pivot_from_base(dry_run=dry_run)
        click.echo(f'  synced {len(synced)} pivot table(s)' if synced
                   else '  (pivot tables already in sync)')
    if not dry_run:
        click.echo(click.style(
            f'\nExpanded. Inject up to model_id {max_model} per slot with '
            f'`xi gear inject`.', fg='green'))


@click.command('inject')
@click.argument('race', metavar='RACE|DAT|FILE_ID')
@click.argument('slot', required=False, metavar='[SLOT]')
@click.argument('source_model_id', required=False, type=int, metavar='[SOURCE_MODEL_ID]')
@click.option('--modelid', '--mid', 'modelid', type=int, required=True,
              help='New custom model_id to create (e.g. 672). REQUIRED.')
@click.option('--rom', 'rom_idx', type=int, required=True,
              help=f'Target custom ROM index (currently {CUSTOM_ROM_IDX}). REQUIRED.')
@click.option('--glb', 'glb_path', type=click.Path(path_type=Path), default=None,
              help='Edited GLB to inject (default: the gear export path for this model).')
@click.option('--double-sided/--single-sided', default=True, show_default=True,
              help='Render the imported faces from both sides.')
@click.option('--scale', 'manual_scale', type=float, default=1.0, show_default=True,
              help='Uniform scale applied to the imported geometry.')
@click.option('--rotate-y', 'rotate_y_deg', type=float, default=0.0, show_default=True,
              help='Rotate the imported mesh around Y (degrees).')
@click.option('--dry-run', is_flag=True, help='Show plan without writing.')
def inject_cmd(race, slot, source_model_id, modelid, rom_idx, glb_path,
               double_sided, manual_scale, rotate_y_deg, dry_run):
    """Inject a NEW gear model into a custom ROM (original untouched).

    Copies the base (SOURCE) gear DAT, replaces its mesh + textures with your
    edited GLB (skeleton left unchanged for now), registers it at a new model_id
    in the custom ROM, and prints the SQL to assign it. With no GLB found it
    clones the base model unchanged. Recolouring lives in `xi gear edit`.

    Requires `xi ftable expand gear` first.

    \b
    RACE/SLOT/SOURCE_MODEL_ID  base model to copy, OR
    DAT/FILE_ID                e.g. ROM/33/17 or 10578 (race/slot/source auto-detected)
    --modelid/--mid            new model_id to create   (required)
    --rom                      target custom ROM index  (required)
    --glb                      edited mesh (defaults to the gear export path)

    \b
    Examples:
      xi gear inject ROM/33/17 --mid 672 --rom 10
      xi gear inject HumeFemale body 34 --mid 672 --rom 10 --glb body_edit.glb
    """
    from xi.gear.xi_export import resolve_gear_target, default_gear_output_dir
    try:
        base_dat, race, slot, source_model_id = resolve_gear_target(
            race, slot, source_model_id)
    except (ValueError, FileNotFoundError) as e:
        raise click.ClickException(str(e))

    click.echo(f"Resolved: {race} / {slot} / model_id {source_model_id}  ({base_dat.name})")

    # Default the GLB to the standard export location for this model.
    if glb_path is None:
        cand = default_gear_output_dir(base_dat) / f'{base_dat.stem}.glb'
        if cand.exists():
            glb_path = cand
            click.echo(f"Using exported GLB: {glb_path}")
        else:
            click.echo(click.style(
                f"No --glb and no export found at {cand} — cloning the base "
                f"model unchanged. (export+edit first: "
                f"xi gear export {race} {slot} {source_model_id})", fg='yellow'))
    elif not Path(glb_path).exists():
        raise click.ClickException(f'GLB not found: {glb_path}')

    try:
        result = inject_model(
            race, slot, source_model_id,
            target_model_id=modelid, rom_idx=rom_idx, glb_path=glb_path,
            double_sided=double_sided, manual_scale=manual_scale,
            rotate_y_deg=rotate_y_deg, dry_run=dry_run)
    except (ValueError, FileNotFoundError) as e:
        raise click.ClickException(str(e))

    if dry_run:
        return

    from xi.ftable.xi_expand import sync_pivot_from_base, pivot_root
    if pivot_root():
        synced = sync_pivot_from_base()
        if synced:
            click.echo(f'Synced {len(synced)} pivot table(s) -> {pivot_root()}')

    mid = result['model_id']

    # Save a ready-to-run SQL file next to the model's export folder.
    from xi.gear.xi_export import default_gear_output_dir
    sql_dir = default_gear_output_dir(base_dat)
    sql_dir.mkdir(parents=True, exist_ok=True)
    sql_path = sql_dir / f'inject_model_{mid}.sql'
    sql = (
        f"-- xi gear inject - custom gear model\n"
        f"-- Race/Slot : {race} / {slot}\n"
        f"-- New model : {mid}  (file_id {result['file_id']}, {result['rom_path']})\n"
        f"-- Base      : {base_dat.name} (model {source_model_id})\n"
        f"--\n"
        f"-- 1) Find the item to use this model ({slot} slot):\n"
        f"--    SELECT itemId, name FROM item_basic WHERE name LIKE '%<name>%';\n"
        f"-- 2) Assign the custom model id:\n"
        f"UPDATE item_equipment SET MId = {mid} WHERE itemId = <itemId>;\n"
        f"-- 3) Restart the server and re-equip the item.\n"
    )
    sql_path.write_text(sql, encoding='utf-8')

    click.echo(click.style('Done.', fg='green'))
    click.echo(f'SQL saved: {sql_path}')
    click.echo(f'  UPDATE item_equipment SET MId = {mid} WHERE itemId = <itemId>;   '
               f'-- {race} {slot} slot')


@click.command('import-json')
@click.argument('config_file', type=click.Path(exists=True))
@click.option('--overlay', type=click.Path(), default=None,
              help='XIPivot overlay directory (default: auto-detect ffxi-hd).')
@click.option('--dry-run', is_flag=True, help='Show plan without writing.')
def apply_config_cmd(config_file, overlay, dry_run):
    """Apply a particle editor JSON config to the XIPivot overlay.

    Reads a JSON config exported from the particle editor and builds
    a modified weapon DAT in the overlay directory. No FTABLE changes
    or gear setup required.

    \b
    Examples:
      xi gear import-json ridill_config.json
      xi gear import-json my_weapon.json --overlay /path/to/DATs/ffxi-hd
    """
    import json, shutil, struct, tempfile
    from xi.xi_config import FFXI_DIR
    from xi.entity.xi_particle_patch import (
        patch_particle_overrides, apply_effects_source)

    ffxi = Path(FFXI_DIR)
    config = json.loads(Path(config_file).read_text())

    # Auto-detect overlay
    if not overlay:
        overlay = ffxi.parent / 'Ashita' / 'polplugins' / 'DATs' / 'ffxi-hd'
        if not overlay.exists():
            raise click.ClickException(
                f'Could not find overlay dir. Tried: {overlay}\n'
                'Use --overlay to specify the path.')
    overlay = Path(overlay)

    # Resolve source DAT
    source_mid = config.get('source_model_id')
    source_dat_rel = config.get('source_dat')
    if source_dat_rel:
        src = ffxi / source_dat_rel
    elif source_mid:
        from xi.gear.xi_export import resolve_gear_dat
        slot = config.get('slot', 'main')
        src = resolve_gear_dat('HumeMale', slot, source_mid)
    else:
        raise click.ClickException('Config must have source_model_id or source_dat')

    if not src.exists():
        raise click.ClickException(f'Source DAT not found: {src}')

    dat_rel = src.relative_to(ffxi)
    click.echo(f'Source: {dat_rel} ({src.stat().st_size} bytes)')

    # Step 1: Recolor
    hue = config.get('hue', 0)
    sat = config.get('saturation', 0)
    lit = config.get('lightness', 0)
    tint = config.get('tint')
    blend = config.get('blend_mode', 'normal')

    with tempfile.NamedTemporaryFile(suffix='.DAT', delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        if hue or sat or lit or tint:
            from xi.tex.xi_recolor import recolour_zone_dat
            stats = recolour_zone_dat(src, tmp_path, hue=hue, saturation=sat,
                                       lightness=lit, tint=tint, blend_mode=blend)
            click.echo(f'  Recolored: {stats["dxt"]} DXT + {stats["paletted"]} paletted')
        else:
            shutil.copy2(src, tmp_path)

        dat = tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)

    # Step 2: Swap effects source
    fx = config.get('effects_source')
    if fx:
        fx_path = ffxi / fx['dat']
        if not fx_path.exists():
            raise click.ClickException(f'Effects source not found: {fx_path}')
        dat = apply_effects_source(dat, fx_path.read_bytes())
        end_sec = struct.pack('<4sIII', b'end ', 0x80, 0, 0)
        dat = dat + end_sec
        click.echo(f'  Effects from: {fx["dat"]}')

    # Step 3: Afterglow
    afterglow = config.get('afterglow')
    if afterglow:
        from xi.entity.xi_afterglow import build_afterglow_dat
        dat = build_afterglow_dat(dat, r=afterglow.get('r', 1),
                                   g=afterglow.get('g', 1), b=afterglow.get('b', 1))
        click.echo(f'  Afterglow: RGB({afterglow.get("r",1)},{afterglow.get("g",1)},{afterglow.get("b",1)})')

    # Step 4: Particle overrides
    overrides = config.get('particle_overrides')
    if overrides:
        dat = patch_particle_overrides(dat, overrides)
        custom_count = sum(1 for o in overrides if o.get('custom_texture'))
        click.echo(f'  Particle overrides: {len(overrides)} emitter(s), {custom_count} custom texture(s)')

    if dry_run:
        click.echo(click.style(f'\nDry run — would write {len(dat)} bytes to {overlay / dat_rel}', fg='cyan'))
        return

    dst = overlay / dat_rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(dat)
    click.echo(f'\n  Written: {dst} ({len(dat)} bytes)')
    click.echo(click.style('Restart game client to see changes.', fg='green'))


# ── Reset ──────────────────────────────────────────────────────────────────

def reset_gear(dry_run: bool = False) -> dict:
    """Undo `ftable expand` / `gear inject`: restore every FTABLE/VTABLE from its
    ``.base`` backup (the pristine pre-edit copy), delete the injected ROM10 gear
    DATs, and clear the gear state file.

    NOTE: restoring the tables reverts ALL custom registrations written to them,
    not just gear.
    """
    state = _load_state()
    restored = 0
    for rom_idx in range(1, 11):
        for p in (ftable_path(rom_idx), vtable_path(rom_idx)):
            path = Path(p)
            base = path.with_name(path.name + '.base')
            if base.exists():
                if not dry_run:
                    shutil.copy2(base, path)
                restored += 1

    removed = 0
    for sub_file in state.get('placed', {}).values():
        subdir, file_idx = sub_file
        dat = _rom10_dir() / str(subdir) / f'{file_idx}.DAT'
        if dat.exists():
            if not dry_run:
                dat.unlink()
            removed += 1

    if not dry_run:
        sp = _state_path()
        if sp.exists():
            sp.unlink()

    return {'restored': restored, 'removed': removed}


@click.command('reset')
@click.option('--dry-run', is_flag=True, help='Show what would be reset without doing it.')
@click.option('--yes', is_flag=True, help='Skip the confirmation prompt.')
def reset_cmd(dry_run, yes):
    """Undo gear expand/inject — restore the lookup tables and remove injected gear.

    Restores every FTABLE/VTABLE from its .base backup (pristine), deletes the
    DATs you injected into the custom ROM, and clears the gear state.

    \b
    WARNING: restoring the tables reverts ALL custom registrations written to
    them (entity/monster injects share the same tables), not only gear.
    """
    if not dry_run and not yes:
        click.confirm(
            'This restores F/V Tables. This will remove all custom content! Are you sure?',
            abort=True)
    r = reset_gear(dry_run=dry_run)
    verb_t = 'Would restore' if dry_run else 'Restored'
    verb_d = 'would remove' if dry_run else 'removed'
    click.echo(f'{verb_t} {r["restored"]} table file(s) from .base; '
               f'{verb_d} {r["removed"]} injected DAT(s).')
    if not dry_run:
        click.echo(click.style('Reset. Re-run `xi ftable expand gear` to start over.', fg='green'))

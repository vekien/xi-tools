import os
import shutil
import time
from datetime import datetime
import click
from xi.xi_config import (FFXI_DIR, FFXI_PIVOT_DIR, XI_TOOLS_DIR, CUSTOM_ROM, CUSTOM_ROM_IDX,
                            editable_dat, output_path_for, read_path_for,
                            MAX_ENTITY_MODELID)
from xi.entity.xi_core import MODEL_FILE_OFFSET

DEFAULT_TARGET = MAX_ENTITY_MODELID
RETAIL_ENTRIES = 109701


def _all_table_pairs(ffxi_dir: str):
    yield os.path.join(ffxi_dir, 'FTABLE.DAT'), os.path.join(ffxi_dir, 'VTABLE.DAT'), 'BASE', 1
    for i in range(2, 10):
        if i == CUSTOM_ROM_IDX:
            continue  # handled separately below
        ft = os.path.join(ffxi_dir, f'ROM{i}', f'FTABLE{i}.DAT')
        vt = os.path.join(ffxi_dir, f'ROM{i}', f'VTABLE{i}.DAT')
        if os.path.exists(ft):
            yield ft, vt, f'ROM{i}', i
    ft_custom = os.path.join(ffxi_dir, CUSTOM_ROM, f'FTABLE{CUSTOM_ROM_IDX}.DAT')
    vt_custom = os.path.join(ffxi_dir, CUSTOM_ROM, f'VTABLE{CUSTOM_ROM_IDX}.DAT')
    if os.path.exists(ft_custom):
        yield ft_custom, vt_custom, CUSTOM_ROM, CUSTOM_ROM_IDX


def _current_entries(ft_path: str) -> int:
    return os.path.getsize(ft_path) // 2


def pivot_root() -> str | None:
    """The pivot/override DAT root (e.g. Ashita's catseyexi pack) if it is
    configured AND actually holds lookup tables — else None. The client merges
    these tables with the base install's, so they must stay the same size too."""
    pv = str(FFXI_PIVOT_DIR) if FFXI_PIVOT_DIR else ''
    if not pv or not os.path.isdir(pv):
        return None
    if any(os.path.exists(ft) for ft, _vt, _l, _i in _all_table_pairs(pv)):
        return pv
    return None


def table_roots(include_pivot: bool = True) -> list[str]:
    """Every DAT root whose FTABLE/VTABLEs the client loads (volume-direct lookup
    with later roots shadowing earlier ones). All tables must stay the same size
    so a larger overlay can't overrun a shorter base buffer: base install plus
    (optionally) the pivot/override pack."""
    roots = [FFXI_DIR]
    if include_pivot:
        pv = pivot_root()
        if pv and os.path.abspath(pv) != os.path.abspath(FFXI_DIR):
            roots.append(pv)
    return roots


def table_entry_sizes(include_pivot: bool = True) -> dict:
    """Entry count of every FTABLE the client loads, across the base install and
    (optionally) the pivot pack. Keyed ``<root>:<label>`` so same-named tables in
    different roots don't collide."""
    sizes = {}
    for root in table_roots(include_pivot):
        tag = 'game' if os.path.abspath(root) == os.path.abspath(FFXI_DIR) else 'pivot'
        for ft, _vt, label, _idx in _all_table_pairs(root):
            # Base install honours the output-dir mirror; the pivot pack is edited
            # in place, so read it directly.
            p = read_path_for(ft) if tag == 'game' else ft
            if os.path.exists(p):
                sizes[f'{tag}:{label}'] = os.path.getsize(p) // 2
    return sizes


def max_table_entries(include_pivot: bool = True) -> int:
    """Largest entry count across every FTABLE the client loads (0 if none)."""
    sizes = table_entry_sizes(include_pivot)
    return max(sizes.values()) if sizes else 0


def resolve_uniform_target(requested_entries: int, include_pivot: bool = True) -> int:
    """Effective expansion target: never below the largest existing table.

    Real client is volume-direct (not OR-merge — that model is xim/dump_event only),
    but a table LARGER than its peers can still overrun shared buffers / break
    tools that walk every root. Shrinking is also unsafe (drops registered
    entries). So the target is clamped up to the current max (across base AND
    pivot), and every table is grown to it → all end the same size. Echoes a
    note when the requested target is raised."""
    existing_max = max_table_entries(include_pivot)
    if existing_max > requested_entries:
        click.echo(f'  Note: a table already has {existing_max:,} entries — growing ALL tables to '
                   f'that (can\'t go below the largest; client needs every table the same size).')
        return existing_max
    return requested_entries


def _grow_table_file(path: str, target_entries: int, per_entry: int, dry_run: bool) -> None:
    """Append zero entries to a single FTABLE (per_entry=2) or VTABLE (per_entry=1)
    in place until it holds target_entries. No-op if already >= target."""
    if not os.path.exists(path):
        return
    cur = os.path.getsize(path) // per_entry
    if cur >= target_entries or dry_run:
        return
    with open(path, 'ab') as f:
        f.write(b'\x00' * ((target_entries - cur) * per_entry))


def expand_pivot_tables(target_entries: int, dry_run: bool, dbg=None) -> None:
    """Grow the pivot/override pack's FTABLE/VTABLEs (base + any ROMn) to
    target_entries so they stay the same size as the base install."""
    pv = pivot_root()
    if not pv:
        return
    click.echo(f'\n[*] Expanding pivot pack tables ({pv}) ...')
    for ft, vt, label, _idx in _all_table_pairs(pv):
        if not os.path.exists(ft) or not os.path.exists(vt):
            continue
        cur = os.path.getsize(ft) // 2
        if cur >= target_entries:
            click.echo(f'  pivot {label}: already {cur} entries - no change needed')
            continue
        click.echo(f'  pivot {label}: {cur} -> {target_entries} entries  (+{target_entries - cur})')
        _grow_table_file(ft, target_entries, 2, dry_run)
        _grow_table_file(vt, target_entries, 1, dry_run)


def sync_pivot_from_base(dry_run: bool = False) -> list[str]:
    """Make the pivot/override pack's lookup tables consistent with the base
    install WITHOUT clobbering the pivot's own retail-range entries.

    XIPivot shadows the base install: with ``redirect_fopens`` the client reads
    whichever FTABLE/VTABLE exists in the pivot folder instead of the base copy.
    So every table the pivot overrides must (a) be the SAME SIZE as the base table
    (a size mismatch crashes the client on load) and (b) carry xi's custom
    registrations, or custom gear/entity file_ids won't resolve through the overlay.

    But the pivot pack also has its OWN entries in the retail range (catseyexi
    content), so we must NOT copy the base table wholesale (that was the old
    ``_mirror_tables_to_pivot`` bug — it wiped those). xi only ever writes
    file_ids at or above ``RETAIL_ENTRIES`` (entity/gear/armor windows), so we copy
    just that CUSTOM REGION (``[RETAIL_ENTRIES:]``) from base into each pivot table.
    Slice-assigning that region both grows the pivot table to the base size and
    injects xi's registrations, while leaving the pivot's retail range untouched.

    Only tables the pivot already overrides are touched (never creates new ones).
    Each is backed up once to ``<name>.base`` before its first change."""
    pv = pivot_root()
    if not pv:
        return []
    synced = []
    for pft, pvt, label, idx in _all_table_pairs(pv):
        if idx == 1:
            b_ft = os.path.join(FFXI_DIR, 'FTABLE.DAT')
            b_vt = os.path.join(FFXI_DIR, 'VTABLE.DAT')
        else:
            b_ft = os.path.join(FFXI_DIR, f'ROM{idx}', f'FTABLE{idx}.DAT')
            b_vt = os.path.join(FFXI_DIR, f'ROM{idx}', f'VTABLE{idx}.DAT')
        b_ft = read_path_for(b_ft)
        b_vt = read_path_for(b_vt)
        if not (os.path.exists(pft) and os.path.exists(pvt)
                and os.path.exists(b_ft) and os.path.exists(b_vt)):
            continue
        with open(b_ft, 'rb') as f:
            base_ft = f.read()
        with open(b_vt, 'rb') as f:
            base_vt = f.read()
        with open(pft, 'rb') as f:
            piv_ft = f.read()
        with open(pvt, 'rb') as f:
            piv_vt = f.read()
        base_entries = len(base_ft) // 2
        # Base itself is retail-sized -> no custom region to propagate.
        if base_entries <= RETAIL_ENTRIES:
            continue
        new_ft = piv_ft[:RETAIL_ENTRIES * 2] + base_ft[RETAIL_ENTRIES * 2:]
        new_vt = piv_vt[:RETAIL_ENTRIES] + base_vt[RETAIL_ENTRIES:]
        if new_ft == piv_ft and new_vt == piv_vt:
            continue  # already in sync
        cur = len(piv_vt)
        click.echo(f'  pivot {label}: {cur:,} -> {base_entries:,} entries '
                   f'(custom region synced from base, retail range kept)')
        if not dry_run:
            for p in (pft, pvt):
                base_bak = p + '.base'
                if not os.path.exists(base_bak):
                    shutil.copy2(p, base_bak)
            with open(pft, 'wb') as f:
                f.write(new_ft)
            with open(pvt, 'wb') as f:
                f.write(new_vt)
        synced.append(pft)
    return synced


def verify_uniform_tables(include_pivot: bool = True) -> None:
    """Post-expansion sanity check: every loaded table (base + pivot) must be the
    same size, or the client crashes on load. Raises if a mismatch remains."""
    sizes = table_entry_sizes(include_pivot)
    if len(set(sizes.values())) > 1:
        detail = ', '.join(f'{k}={v:,}' for k, v in sorted(sizes.items()))
        raise click.ClickException(
            f'FTABLE/VTABLE sizes still mismatched after expand ({detail}). This should not '
            f'happen — do not launch the client. If a pivot:* table is the odd one out, re-run '
            f'with --pivot. Otherwise run `xi ftable reset` and report this.')


def _expand_pair(ft_path, vt_path, label, target_entries, dry_run, dbg=None):
    # Expand the table in place (.base backup on first edit); fresh=False so
    # a previously-expanded table is grown further rather than re-seeded.
    # `dbg`, when given, is a logging callback (str -> None) for timed diagnostics.
    t0 = time.perf_counter()
    out_ft = read_path_for(ft_path) if dry_run else editable_dat(ft_path, fresh=False)
    out_vt = read_path_for(vt_path) if dry_run else editable_dat(vt_path, fresh=False)
    t_seed = time.perf_counter()
    if dbg:
        dbg(f'{label}: editable_dat seed/copy took {t_seed - t0:.3f}s -> {out_ft}')
    cur = _current_entries(out_ft)
    if cur >= target_entries:
        click.echo(f'  {label}: already {cur} entries - no change needed')
        return False
    add = target_entries - cur
    click.echo(f'  {label}: {cur} -> {target_entries} entries  (+{add})')
    if not dry_run:
        with open(out_ft, 'ab') as f:
            f.write(b'\x00\x00' * add)
        with open(out_vt, 'ab') as f:
            f.write(b'\x00' * add)
    if dbg:
        dbg(f'{label}: appended +{add:,} entries '
            f'(FTABLE +{add * 2:,}B / VTABLE +{add:,}B) in '
            f'{time.perf_counter() - t_seed:.3f}s')
    return True


def _create_custom_rom(ffxi_dir, target_entries, dry_run, dbg=None):
    # The custom ROM is brand-new content — write it straight to the output dir.
    # `dbg`, when given, is a logging callback (str -> None) for timed diagnostics.
    t0 = time.perf_counter()
    ft_custom  = str(output_path_for(os.path.join(ffxi_dir, CUSTOM_ROM, f'FTABLE{CUSTOM_ROM_IDX}.DAT')))
    vt_custom  = str(output_path_for(os.path.join(ffxi_dir, CUSTOM_ROM, f'VTABLE{CUSTOM_ROM_IDX}.DAT')))
    custom_dir = os.path.dirname(ft_custom)
    if os.path.exists(ft_custom):
        cur = _current_entries(ft_custom)
        if cur >= target_entries:
            click.echo(f'  {CUSTOM_ROM}: already {cur} entries - no change needed')
            return
        click.echo(f'  {CUSTOM_ROM}: expanding {cur} -> {target_entries} entries')
        if not dry_run:
            add = target_entries - cur
            with open(ft_custom, 'ab') as f:
                f.write(b'\x00\x00' * add)
            with open(vt_custom, 'ab') as f:
                f.write(b'\x00' * add)
        if dbg:
            dbg(f'{CUSTOM_ROM}: appended +{target_entries - cur:,} entries in '
                f'{time.perf_counter() - t0:.3f}s')
    else:
        click.echo(f'  {CUSTOM_ROM}: creating fresh ({target_entries} entries)')
        if not dry_run:
            os.makedirs(custom_dir, exist_ok=True)
            with open(ft_custom, 'wb') as f:
                f.write(b'\x00\x00' * target_entries)
            with open(vt_custom, 'wb') as f:
                f.write(b'\x00' * target_entries)
            click.echo(f'        Created {ft_custom}')
            click.echo(f'        Created {vt_custom}')
        if dbg:
            dbg(f'{CUSTOM_ROM}: wrote fresh {target_entries:,} entries '
                f'(FTABLE {target_entries * 2:,}B / VTABLE {target_entries:,}B) in '
                f'{time.perf_counter() - t0:.3f}s')


def _backup_all(ffxi_dir, include_pivot: bool = True):
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_dir = os.path.join(XI_TOOLS_DIR, 'backups', f'ftable_{ts}')
    os.makedirs(backup_dir, exist_ok=True)
    backed = 0
    roots = [(ffxi_dir, '')]
    if include_pivot:
        pv = pivot_root()
        if pv and os.path.abspath(pv) != os.path.abspath(ffxi_dir):
            roots.append((pv, 'PIVOT_'))
    for root, prefix in roots:
        for ft, vt, label, _ in _all_table_pairs(root):
            for src in (ft, vt):
                if os.path.exists(src):
                    dst = os.path.join(backup_dir, f'{prefix}{label}_{os.path.basename(src)}')
                    shutil.copy2(src, dst)
                    backed += 1
    click.echo(f'[+] Backed up {backed} files -> {backup_dir}')


@click.command('entity')
@click.argument('target_modelid', type=int, default=DEFAULT_TARGET, required=False,
                metavar=f'[MAX_MODELID={DEFAULT_TARGET}]')
@click.option('--dry-run', is_flag=True, help='Show changes without writing files')
@click.option('--no-backup', is_flag=True, help='Skip backup step')
@click.option('--pivot/--no-pivot', default=True, show_default=True,
              help='Also grow the pivot/override pack tables (FFXI_PIVOT_DIR) so they stay the same '
                   'size as the base install. The client merges both — a size mismatch crashes it.')
def entity_cmd(target_modelid, dry_run, no_backup, pivot):
    """Expand FTABLE/VTABLE for custom MONSTER/entity models up to MAX_MODELID.

    \b
    Examples:
      xi ftable expand entity            # default (modelid up to MAX_ENTITY_MODELID)
      xi ftable expand entity 18000      # smaller buffer

    The ceiling can't exceed what keeps entity below the gear region; to go
    higher, raise XI_MAX_ENTITY_MODELID in xi_config (the gear floor follows).
    """
    target_file_id = MODEL_FILE_OFFSET + target_modelid
    target_entries = target_file_id + 1
    add_entries    = max(0, target_entries - RETAIL_ENTRIES)

    click.echo('=' * 62)
    click.echo('  FFXI FTABLE/VTABLE Expander')
    click.echo('=' * 62)
    click.echo(f'  Target modelid   : {target_modelid}')
    click.echo(f'  Target file_id   : {target_file_id}')
    click.echo(f'  Required entries : {target_entries:,}  (retail: {RETAIL_ENTRIES:,}, +{add_entries:,})')
    click.echo(f'  Dry run          : {dry_run}')
    click.echo('=' * 62)

    if target_entries <= RETAIL_ENTRIES:
        raise click.ClickException(
            f'Target modelid {target_modelid} fits within the retail FTABLE size. No expansion needed.')

    # Guard: an entity ceiling whose file_id reaches the gear region would let
    # entity injects collide with gear (they share these tables). The gear floor
    # is derived from MAX_ENTITY_MODELID, so the configured default never trips
    # this — only an explicit over-high target does.
    from xi.gear.xi_inject import CUSTOM_GEAR_BASE
    if target_file_id >= CUSTOM_GEAR_BASE:
        safe_max = CUSTOM_GEAR_BASE - MODEL_FILE_OFFSET - 1
        raise click.ClickException(
            f'Target modelid {target_modelid} maps to file_id {target_file_id:,}, which '
            f'reaches into the gear region (starts at file_id {CUSTOM_GEAR_BASE:,}). '
            f'Entity and gear share these tables, so injecting up there would collide. '
            f'Highest safe entity modelid is {safe_max:,} '
            f'(raise XI_MAX_ENTITY_MODELID in xi_config to move the gear floor up).')

    # Never shrink below the largest existing table (base OR pivot), and grow
    # every table to a single uniform size (the client crashes if they differ).
    target_entries = resolve_uniform_target(target_entries, include_pivot=pivot)

    if not dry_run and not no_backup:
        click.echo('\n[*] Backing up existing files ...')
        _backup_all(FFXI_DIR, include_pivot=pivot)

    click.echo('\n[*] Expanding existing FTABLE/VTABLE files ...')
    for ft, vt, label, idx in _all_table_pairs(FFXI_DIR):
        if label == CUSTOM_ROM:
            continue
        if not os.path.exists(ft):
            click.echo(f'  {label}: not found - skipped')
            continue
        _expand_pair(ft, vt, label, target_entries, dry_run)

    click.echo(f'\n[*] Creating / expanding {CUSTOM_ROM} ...')
    _create_custom_rom(FFXI_DIR, target_entries, dry_run)

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
        click.echo(f'  Custom modelid range: 15000 - {target_modelid}')
    click.echo('=' * 62)

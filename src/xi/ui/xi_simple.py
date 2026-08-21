import shutil
from pathlib import Path

import click

import xi.xi_config as _cfg
from xi.xi_config import ensure_base, output_path_for, read_path_for
from xi.ui.xi_core import (canonical_texture_sizes, compression_name, output_file_names,
                           parse_dds, parse_textures, replace_texture,
                           resolve_layout_reference, sync_layout_rects, write_dds)
from xi.utils.xi_core import convert_dds_to_png, convert_png_to_dds


def _apply_ffxi_dir(ffxi: str | None) -> str:
    """Override FFXI_DIR for this invocation if --ffxi was supplied."""
    if ffxi:
        _cfg.FFXI_DIR = ffxi
    return _cfg.FFXI_DIR

# Window-skin theme set: ROM/0/14..21 share the same 4 texture names
# (newtex/hfr1/corner/vfr1), so edited PNGs propagate across them by name.
WIN0_THEME_IDS = list(range(14, 22))


def _resolve_dat_path(dat_path: str) -> Path:
    p = Path(dat_path)
    if not p.is_absolute():
        p = Path(_cfg.FFXI_DIR) / p
    if not p.exists():
        raise click.ClickException(f'DAT not found: {p}')
    return p


def _default_export_dir(dat_file: Path) -> Path:
    try:
        rel = dat_file.resolve().relative_to(Path(_cfg.FFXI_DIR).resolve())
    except ValueError:
        rel = dat_file

    parts = list(rel.parts)
    if parts and parts[0].upper().startswith('ROM'):
        parts = parts[1:]

    if not parts:
        return Path('exports/ui') / dat_file.stem

    if len(parts) == 1:
        return Path('exports/ui') / Path(parts[0]).stem

    return Path('exports/ui').joinpath(*parts[:-1], Path(parts[-1]).stem)


ALPHA_SIDECAR = 'alpha-scale.json'


def _alpha_boost_png(png_path: Path) -> float:
    """Brighten a PNG's alpha to full range, returning the factor applied.

    FFXI stores UI alpha at half scale -- `0x80` means fully opaque -- so an exported
    PNG opens at roughly 50% opacity and is miserable to edit. DXT3's 4-bit alpha
    cannot hold 128 exactly, so the encoder dithers 119/136 (7/17 and 8/17) around it;
    that dither is the fingerprint of a half-scale texture.

    The factor is per-texture `255 / max_alpha` rather than a flat 2.0 because these
    DATs mix conventions: `ex1us` peaks at 136 but `20logo` is already 255. A flat 2.0
    would clamp the latter and the inverse would come back as 128, wrecking it. Deriving
    the factor from the actual peak never clamps, so the round-trip is exact.
    """
    from PIL import Image
    im = Image.open(png_path)
    if im.mode != 'RGBA':
        return 1.0
    peak = im.getchannel('A').getextrema()[1]
    if peak == 0 or peak >= 255:
        return 1.0
    factor = 255.0 / peak
    r, g, b, a = im.split()
    a = a.point(lambda v: min(255, round(v * factor)))
    Image.merge('RGBA', (r, g, b, a)).save(png_path)
    return factor


def _apply_alpha_scale(png_path: Path, factor: float, out_path: Path) -> Path:
    """Write `png_path` to `out_path` with its alpha multiplied by `factor`."""
    from PIL import Image
    im = Image.open(png_path)
    if im.mode != 'RGBA' or factor == 1.0:
        return png_path
    r, g, b, a = im.split()
    a = a.point(lambda v: min(255, round(v * factor)))
    Image.merge('RGBA', (r, g, b, a)).save(out_path)
    return out_path


def _fit_png(png_path: Path, size: tuple, out_path: Path) -> Path:
    """Resample a PNG to `size`. Returns the path to feed the DDS encoder."""
    from PIL import Image
    with Image.open(png_path) as im:
        if (im.width, im.height) == tuple(size):
            return png_path
        im.resize(tuple(size), Image.LANCZOS).save(out_path)
    return out_path


def _png_digest(path: Path) -> str:
    """Content hash of a PNG, used to tell an untouched export from replaced art."""
    import hashlib
    return hashlib.sha1(path.read_bytes()).hexdigest()


def _read_alpha_sidecar(work_dir: Path) -> dict:
    import json
    f = work_dir / ALPHA_SIDECAR
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return {}


@click.command('simple-extract')
@click.argument('dat_path', metavar='DAT_FILE')
@click.option('--raw-alpha', is_flag=True,
              help="Write the PNGs with FFXI's raw half-scale alpha (they will look "
                   "roughly 50% transparent) instead of brightening them for editing.")
@click.option('--ffxi', default=None, metavar='DIR',
              help='Override FFXI_DIR for this command (e.g. a pivot/override root).')
def simple_extract_cmd(dat_path: str, raw_alpha: bool, ffxi: str | None):
    """Extract a UI DAT to DDS and immediately convert all DDS files to PNG.

    The export folder is derived automatically from the DAT path:
    `ROM/0/1.DAT -> exports/ui/0/1`.
    """
    _apply_ffxi_dir(ffxi)
    dat_file = _resolve_dat_path(dat_path)
    out_dir = _default_export_dir(dat_file)

    data = read_path_for(dat_file).read_bytes()
    entries = parse_textures(data)
    if not entries:
        raise click.ClickException('No texture entries found in this DAT.')

    out_dir.mkdir(parents=True, exist_ok=True)

    click.echo(f'File: {dat_file}')
    click.echo(f'Export dir: {out_dir}')
    click.echo()

    written = 0
    alpha_scales: dict[str, float] = {}
    for entry, dds_name in zip(entries, output_file_names(entries)):
        dds_path = out_dir / dds_name
        png_path = out_dir / f'{dds_path.stem}.png'
        write_dds(entry, dds_path)
        convert_dds_to_png(dds_path, png_path)
        boost = 1.0 if raw_alpha else _alpha_boost_png(png_path)
        if boost != 1.0:
            # The digest pins the factor to these exact bytes. Import only undoes the
            # boost for a file that is still the one written here; art the user replaced
            # is taken at face value, since its alpha was authored, not brightened.
            alpha_scales[png_path.stem] = {'factor': boost, 'sha1': _png_digest(png_path)}
        note = '' if boost == 1.0 else f' alpha x{boost:.3g}'
        click.echo(f'  extracted {dds_name} [{compression_name(entry)}]{note} -> {png_path.name}')
        written += 1

    # Palettized (0xB1) textures have no xTXD, so parse_textures cannot see them. Export
    # them too, or the folder silently omits any texture --hd has converted and a
    # re-import would leave it untouched while reporting success on everything else.
    from xi.ui.xi_palette import parse_palettized, to_image
    for tex in parse_palettized(data):
        png_path = out_dir / f'{tex.name}.png'
        to_image(tex).save(png_path)
        # to_image already restores full-range alpha from FFXI's half scale, and the
        # import side halves it again, so this pair needs no sidecar entry.
        alpha_scales.pop(tex.name, None)
        click.echo(f'  extracted {tex.name} [palettized {tex.width}x{tex.height}] '
                   f'-> {png_path.name}')
        written += 1

    import json
    sidecar = out_dir / ALPHA_SIDECAR
    if alpha_scales:
        sidecar.write_text(json.dumps(alpha_scales, indent=2), encoding='utf-8')
    elif sidecar.exists():
        sidecar.unlink()      # stale scales would be re-applied on the next import

    click.echo()
    click.echo(f'Extracted and converted {written} texture(s) to {out_dir}')
    if alpha_scales:
        click.echo(f'Brightened alpha on {len(alpha_scales)} texture(s); '
                   f'{ALPHA_SIDECAR} records the factors so import restores them.')


def _hd_pass(data: bytearray, work_dir: Path, canonical: dict, requested_format: str,
             only: set | None = None) -> list:
    """Write oversized PNGs as DXT textures at their own resolution.

    A UI sprite is normally fitted back to the size its records address, which caps it
    at vanilla. That is the real quality ceiling: the expansion banners are drawn
    magnified -- a 240x48 region fills roughly 375x74 real pixels -- so the source is
    upscaled before it ever reaches the screen. Keeping the texture large and scaling
    its sprite rects turns that into a downsample.

    The encoder stays DXT. A palettized (0xB1) version was tried and measured better on
    paper (33.9 dB against 30.9), but 255 palette entries shared across five banners
    band visibly on smooth gradients, which DXT avoids by choosing different colours per
    4x4 block. Resolution was the win; the encoder was not.

    Chunks are replaced back-to-front so a size change never invalidates an offset still
    to come.
    """
    from PIL import Image
    from xi.ui.xi_core import parse_dds
    from xi.ui.xi_palette import build_dxt_chunk, find_texture_chunk

    targets = []
    for name, (cw, ch) in canonical.items():
        if only and name not in only:
            continue
        png = work_dir / f'{name}.png'
        if not png.exists():
            continue
        with Image.open(png) as probe:
            size = (probe.width, probe.height)
        if size[0] > cw or size[1] > ch:
            targets.append((name, size))

    tmp = work_dir / '.alpha'
    done = []
    for name, size in sorted(targets, key=lambda t: -(find_texture_chunk(data, t[0]) or {}).get('off', 0)):
        loc = find_texture_chunk(data, name)
        if loc is None:
            continue
        tmp.mkdir(parents=True, exist_ok=True)
        scaled = tmp / f'hd_{name}.png'
        Image.open(work_dir / f'{name}.png').convert('RGBA').resize(size, Image.LANCZOS).save(scaled)
        dds_path = tmp / f'hd_{name}.dds'
        fmt = requested_format.lower()
        convert_png_to_dds(scaled, dds_path, requested_format='dxt3' if fmt == 'auto' else fmt)
        chunk = build_dxt_chunk(loc['tag'], loc['info'], loc['parent'], name, parse_dds(dds_path))
        data[loc['off']:loc['off'] + loc['bytes']] = chunk
        done.append((name, (loc['width'], loc['height']), size, loc['bytes'], len(chunk)))
    return done


def _restore_dxt(data: bytearray, work_dir: Path, canonical: dict, hd_names: set,
                 requested_format: str) -> list:
    """Rewrite palettized textures back to DXT at canonical size, unless kept as HD.

    Nothing writes palettized textures any more, but a DAT edited by an earlier build
    can still hold them, and `parse_textures` matches only `xTXD` -- so such a chunk is
    invisible to the normal import loop and would be stuck permanently. This makes the
    state recoverable with a plain import.
    """
    from PIL import Image
    from xi.ui.xi_core import parse_dds
    from xi.ui.xi_palette import build_dxt_chunk, find_texture_chunk, parse_palettized

    stuck = [t for t in parse_palettized(data) if t.name not in hd_names]
    done = []
    for tex in sorted(stuck, key=lambda t: -t.chunk_off):
        png = work_dir / f'{tex.name}.png'
        if not png.exists():
            continue
        loc = find_texture_chunk(data, tex.name)
        if loc is None:
            continue
        target = tuple(canonical.get(tex.name) or (tex.width, tex.height))
        tmp = work_dir / '.alpha'
        tmp.mkdir(parents=True, exist_ok=True)
        fitted = tmp / f'restore_{tex.name}.png'
        Image.open(png).convert('RGBA').resize(target, Image.LANCZOS).save(fitted)
        dds_path = tmp / f'restore_{tex.name}.dds'
        fmt = requested_format.lower()
        convert_png_to_dds(fitted, dds_path, requested_format='dxt3' if fmt == 'auto' else fmt)
        chunk = build_dxt_chunk(loc['tag'], loc['info'], loc['parent'], tex.name,
                                parse_dds(dds_path))
        data[loc['off']:loc['off'] + loc['bytes']] = chunk
        done.append((tex.name, (tex.width, tex.height), target))
    return done


def _import_one(dat_file: Path, out_file: Path, work_dir: Path, requested_format: str,
                fix_layout: bool = True, reference: str | None = None,
                repair: bool = False, hd: bool = False, hd_only_arg: str | None = None) -> None:
    """Rebuild DDS from the PNGs in work_dir and patch them into dat_file -> out_file."""
    if not work_dir.exists() or not work_dir.is_dir():
        raise click.ClickException(f'Working directory not found: {work_dir}')

    data = bytearray(dat_file.read_bytes())
    entries = parse_textures(data)
    if not entries:
        raise click.ClickException('No texture entries found in this DAT.')

    click.echo(f'File: {dat_file}')
    click.echo(f'Working dir: {work_dir}')
    click.echo(f'Output DAT: {out_file}')
    click.echo(f'Format: {requested_format.lower()}')

    converted = 0
    filenames = output_file_names(entries)
    entry_by_filename = dict(zip(filenames, entries))
    alpha_scales = _read_alpha_sidecar(work_dir)
    canonical = canonical_texture_sizes(dat_file)
    hd_only = {n.strip() for n in hd_only_arg.split(',') if n.strip()} if hd_only_arg else None
    hd = hd or bool(hd_only)
    hd_names = set()
    if hd:
        from PIL import Image as _Img
        for _n, (_cw, _ch) in canonical.items():
            if hd_only and _n not in hd_only:
                continue
            _p = work_dir / f'{_n}.png'
            if _p.exists():
                with _Img.open(_p) as _im:
                    if _im.width > _cw or _im.height > _ch:
                        hd_names.add(_n)
    tmp_dir = work_dir / '.alpha'
    for filename in filenames:
        png_path = work_dir / f'{Path(filename).stem}.png'
        dds_path = work_dir / filename
        if not png_path.exists():
            continue

        entry = entry_by_filename[filename]

        # Undo the brightening sx applied -- but only for a PNG that is still byte-for-byte
        # the one sx wrote. Applying it to hand-authored art squashes full-range alpha into
        # the lower half, and DXT3's 4-bit alpha then quantises that to a handful of steps:
        # measured 7.2 dB alpha PSNR (11 distinct levels collapsing to 3) against 56.4 dB
        # when left alone.
        rec = alpha_scales.get(png_path.stem)
        boost = 1.0
        if isinstance(rec, dict):
            if rec.get('sha1') == _png_digest(png_path):
                boost = float(rec.get('factor', 1.0))
        elif rec:
            # Sidecar predates digests, so there is no way to tell an untouched export
            # from replaced art. Leave the alpha alone: over-brightening a texture is a
            # visible but recoverable mistake, while squashing it through DXT3's 4-bit
            # alpha destroys detail that cannot be got back. Re-run sx to get a digest.
            boost = 1.0

        source_png = png_path
        if boost != 1.0:
            tmp_dir.mkdir(parents=True, exist_ok=True)
            source_png = _apply_alpha_scale(png_path, 1.0 / boost, tmp_dir / png_path.name)

        # Resample to the size the game expects. The target comes from the reference
        # sheet, falling back to what the DAT already holds -- never from the PNG. The
        # sprite records address this texture in absolute pixels, so its size is fixed
        # by the client's layout: edit at whatever resolution suits you and it is fitted
        # on the way in, leaving the mapping untouched.
        if entry.name in hd_names:
            continue                    # handled by the palettized pass below
        target = tuple(canonical.get(entry.name) or (entry.width, entry.height))
        fit_note = ''
        from PIL import Image
        with Image.open(source_png) as probe:
            have = (probe.width, probe.height)
        if have != target:
            tmp_dir.mkdir(parents=True, exist_ok=True)
            source_png = _fit_png(source_png, target, tmp_dir / f'fit_{png_path.name}')
            fit_note = f'; fit {have[0]}x{have[1]} -> {target[0]}x{target[1]}'

        _analysis, dds_format, format_reason = convert_png_to_dds(
            source_png,
            dds_path,
            requested_format=requested_format.lower(),
            match_source=str(dds_path) if requested_format.lower() == 'auto' and dds_path.exists() else None,
        )
        note = ('' if boost == 1.0 else f'; alpha /{boost:.3g}') + fit_note
        click.echo(
            f'  rebuilt {png_path.name} -> {dds_path.name} '
            f'[{dds_format}; source {compression_name(entry)}; {format_reason}{note}]'
        )
        converted += 1

    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if converted == 0:
        raise click.ClickException(f'No matching .png files found in {work_dir}')

    replaced = 0
    missing = 0
    for entry, filename in reversed(list(zip(entries, filenames))):
        if entry.name in hd_names:
            continue
        dds_path = work_dir / filename
        if not dds_path.exists():
            missing += 1
            continue
        was = compression_name(entry)   # replace_texture updates the entry in place
        was_size = f'{entry.width}x{entry.height}'
        try:
            dds = parse_dds(dds_path)
            replace_texture(data, entry, dds)
        except ValueError as e:
            raise click.ClickException(str(e))
        now_size = f'{entry.width}x{entry.height}'
        grew = '' if was_size == now_size else f' {was_size} -> {now_size}'
        click.echo(
            f'  imported {filename} [{was} -> {compression_name(entry)}]{grew} '
            f'-> {entry.name or "unnamed"}'
        )
        replaced += 1

    for name, was, now in _restore_dxt(data, work_dir, canonical, hd_names, requested_format):
        click.echo(f'  restored {name} {was[0]}x{was[1]} palettized -> {now[0]}x{now[1]} DXT')

    if hd:
        for name, was, now, oldb, newb in _hd_pass(data, work_dir, canonical,
                                                   requested_format, hd_only):
            click.echo(f'  hd: {name} {was[0]}x{was[1]} -> {now[0]}x{now[1]} '
                       f'(chunk {oldb} -> {newb})')

    if fix_layout or hd:
        ref, how = resolve_layout_reference(dat_file, reference)
        for name, off, w, n in sync_layout_rects(data, ref):
            click.echo(f'  layout: {name} @0x{off:06x} src {w[0]}x{w[1]}@({w[2]},{w[3]}) '
                       f'-> {n[0]}x{n[1]}@({n[2]},{n[3]})')

    if repair:
        _fix_layout(data, dat_file, reference)

    out_file.parent.mkdir(parents=True, exist_ok=True)
    if out_file.exists():
        ensure_base(out_file)   # keep the pristine bytes for undo
    out_file.write_bytes(data)
    click.echo(f'Rebuilt {converted} DDS file(s) and patched {replaced} texture(s) into {out_file}')
    if missing:
        click.echo(f'Left {missing} texture(s) unchanged (no matching .dds present)')


def _fix_layout(data: bytearray, dat_file: Path, reference: str | None) -> None:
    """Repoint whole-texture sprite rects at their texture's current pixel size.

    Runs on every import, not only when this run resized something: a DAT can arrive
    already mismatched (texture enlarged by another tool, rects left at the old
    extent), and an export/re-import round-trip of that DAT changes no dimensions, so
    a resize-triggered fixup would never fire and the sprite stays cropped.
    """
    ref, how = resolve_layout_reference(dat_file, reference)
    click.echo(f'  layout: using {how}')
    for name, off, old, new in sync_layout_rects(data, ref):
        click.echo(f'  layout: {name} @0x{off:06x} src {old[0]}x{old[1]}@({old[2]},{old[3]}) '
                   f'-> {new[0]}x{new[1]}@({new[2]},{new[3]})')


def _seed_theme_from_source(source_dir: Path, theme_dat: Path) -> Path:
    """Copy the source theme's edited PNGs into theme_dat's work dir (by name, so
    they map onto matching texture names regardless of per-DAT order), and extract
    the theme's current DDS first so 'auto' format matching has a reference."""
    theme_work = _default_export_dir(theme_dat)
    theme_work.mkdir(parents=True, exist_ok=True)
    # extract the theme's current DDS (reference for auto format matching)
    tdata = theme_dat.read_bytes()
    tentries = parse_textures(tdata)
    for entry, fn in zip(tentries, output_file_names(tentries)):
        write_dds(entry, theme_work / fn)
    # copy the source PNGs in by filename
    for png in sorted(source_dir.glob('*.png')):
        shutil.copy2(png, theme_work / png.name)
    return theme_work


@click.command('simple-import')
@click.argument('dat_path', metavar='DAT_FILE')
@click.option('--output-dat', default=None,
              help='Write to a different DAT path instead of overwriting DAT_FILE.')
@click.option(
    '--format',
    'requested_format',
    type=click.Choice(['auto', 'dxt1', 'dxt3', 'dxt5'], case_sensitive=False),
    default='auto',
    show_default=True,
    help='DDS compression format. auto preserves the existing extracted DDS format.',
)
@click.option('--reference', default=None, metavar='DAT',
              help='Unmodified DAT to read original sprite rects from. Defaults to the '
                   'built-in reference sheet, then <dat>.base.')
@click.option('--no-resize', 'fix_layout', is_flag=True, default=True, flag_value=False,
              help='Import the textures but leave sprite source rects alone, so a texture '
                   'whose size changed keeps pointing at the old sub-region.')
@click.option('--hd', is_flag=True,
              help='Keep a PNG larger than the size the game expects at its own resolution '
                   'and scale its sprite rects to match, instead of fitting it down. '
                   'Sprites are drawn magnified, so this is the single biggest quality win.')
@click.option('--hd-only', 'hd_only_arg', default=None, metavar='NAMES',
              help='Comma-separated texture names to apply --hd to, e.g. "ex1us,ex2us". '
                   'Implies --hd; everything else takes the normal DXT path.')
@click.option('--repair-rects', 'repair', is_flag=True,
              help='Also rebuild every sprite rect from the reference sheet. For a DAT left '
                   'inconsistent by an earlier edit; not needed for a normal import.')
@click.option('--all-themes', is_flag=True,
              help='Window skins only (ROM/0/14..21): apply this theme\'s edited PNGs to ALL skins and import each.')
@click.option('--ffxi', default=None, metavar='DIR',
              help='Override FFXI_DIR for this command (e.g. a pivot/override root).')
def simple_import_cmd(dat_path: str, output_dat: str | None, requested_format: str,
                      reference: str | None, fix_layout: bool, hd: bool, hd_only_arg: str | None,
                      repair: bool, all_themes: bool, ffxi: str | None):
    """Convert edited PNG files back to DDS and import them into a UI DAT.

    The working folder is derived automatically from the DAT path:
    `ROM/0/1.DAT -> exports/ui/0/1`.

    With --all-themes, the edited PNGs from this DAT's folder are copied onto every
    window-skin DAT (ROM/0/14..21) and imported into each in one go.
    """
    _apply_ffxi_dir(ffxi)
    dat_file = _resolve_dat_path(dat_path)

    if requested_format.lower() == 'dxt5':
        # Tested on ROM/119/50: a correctly-built all-DXT5 DAT renders flat grey.
        # See docs/ui/export.md. Left selectable so the failure can be re-tested.
        click.echo('WARNING: the client does not render DXT5 (5TXD) — textures come '
                   'out flat grey. Use dxt3 for alpha, dxt1 for opaque.')

    if all_themes:
        try:
            stem_id = int(dat_file.stem)
        except ValueError:
            stem_id = None
        if dat_file.parent.name != '0' or stem_id not in WIN0_THEME_IDS:
            raise click.ClickException('--all-themes only supports window skins ROM/0/14..21.')

        source_dir = _default_export_dir(dat_file)
        if not source_dir.is_dir() or not list(source_dir.glob('*.png')):
            raise click.ClickException(f'No edited PNGs found in {source_dir}. Extract + edit this DAT first.')

        click.echo(f'Applying {dat_file.stem} theme PNGs to all window skins {WIN0_THEME_IDS[0]}..{WIN0_THEME_IDS[-1]}')
        click.echo()
        for theme_id in WIN0_THEME_IDS:
            theme_dat = dat_file.with_name(f'{theme_id}.DAT')
            if not theme_dat.exists():
                click.echo(f'-- skip {theme_dat.name}: not found')
                continue
            work = source_dir if theme_id == stem_id else _seed_theme_from_source(source_dir, theme_dat)
            _import_one(theme_dat, output_path_for(theme_dat), work, requested_format,
                        fix_layout, reference, repair, hd, hd_only_arg)
            click.echo()
        click.echo('All themes updated.')
        return

    if output_dat:
        out_file = Path(output_dat)
        if not out_file.is_absolute():
            out_file = Path(_cfg.FFXI_DIR) / out_file
    else:
        # Default: write the DAT back in place.
        out_file = output_path_for(dat_file)
    _import_one(dat_file, out_file, _default_export_dir(dat_file), requested_format,
                fix_layout, reference, repair, hd, hd_only_arg)

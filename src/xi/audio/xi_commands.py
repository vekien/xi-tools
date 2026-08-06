#!/usr/bin/env python3
"""Shared `list` / `export` command bodies for `xi audio music` and
`xi audio sfx` — the two kinds differ only in marker, extension, and the
directory tree they live in (see xi.audio.xi_core.Kind)."""

import re as _re
from pathlib import Path

import click

from xi.audio import xi_core as core
from xi.audio.xi_names import music_titles, sfx_titles
from xi.xi_config import FFXI_DIR


def _fmt_dur(sec: float) -> str:
    m, s = divmod(int(round(sec)), 60)
    return f"{m}:{s:02d}"


def _roots(root):
    return (root,) if root else core.SOUND_ROOTS


def resolve_vgmstream(vgmstream_opt, native_only):
    """Resolve the optional ATRAC3 fallback binary for the command layer.
    Returns a Path or None. Errors if an explicit path was given but not found."""
    if native_only:
        return None
    vgm = core.find_vgmstream(vgmstream_opt)
    if vgmstream_opt and vgm is None:
        raise click.ClickException(f"vgmstream not found at: {vgmstream_opt}")
    return vgm


def run_list(kind: core.Kind, patterns, root):
    from xi.audio import xi_names as names
    base = Path(FFXI_DIR)
    if not base.is_dir():
        raise click.ClickException(
            f"FFXI_DIR not found: {base}  (set the FFXI_DIR env var)")
    entries = core.list_entries(kind, base, _roots(root), patterns)
    if not entries:
        raise click.ClickException("No matching files found.")
    click.echo(f"{'ROOT':<7} {'FILE':<28} {'FMT':<6} {'CH':>2} {'RATE':>6} "
               f"{'DUR':>6} {'LOOP':<4} NAME / CATEGORY")
    shown = 0
    for e in entries:
        try:
            h = core.parse_header_file(e.path)
            # Duration is only meaningful for the formats we decode natively;
            # ATRAC3's block fields don't map to a frame count here.
            dur = (_fmt_dur(h.duration_sec)
                   if h.sample_format in (core.FMT_ADPCM, core.FMT_PCM) else "?")
            # Title where known; else the folder category (SFX) as a fallback.
            label = (names.music_name(h.id) if kind is core.MUSIC
                     else (names.sfx_name(h.id) or names.sfx_category(h.id))) or ""
            info = (f"{h.format_name:<6} {h.channels:>2} {h.sample_rate:>6} "
                    f"{dur:>6} {'yes' if h.looped else '-':<4} {label}")
        except core.AudioError as ex:
            info = f"<{ex}>"
        click.echo(f"{e.root:<7} {e.path.name:<28} {info}")
        shown += 1
        if shown >= 2000:
            click.echo(f"  … and {len(entries) - shown} more "
                       f"(narrow with a NAME filter)")
            break
    click.echo(f"\n{len(entries)} {kind.name} file(s).")


def run_export(kind: core.Kind, patterns, out_dir, root, limit, loops,
               vgmstream_opt=None, native_only=False, numbered=False):
    base = Path(FFXI_DIR)
    if not base.is_dir():
        raise click.ClickException(
            f"FFXI_DIR not found: {base}  (set the FFXI_DIR env var)")
    vgm = resolve_vgmstream(vgmstream_opt, native_only)
    out = Path(out_dir) if out_dir else Path("exports") / "audio" / kind.name
    entries = core.list_entries(kind, base, _roots(root), patterns)
    if not entries:
        raise click.ClickException("No matching files found.")
    if limit:
        entries = entries[:limit]

    click.echo(f"Decoding {len(entries)} {kind.name} file(s) -> {out}"
               + ("" if vgm else "  (ATRAC3 will be skipped: no vgmstream)"))
    ok = atrac = 0
    failed = []

    # Titles default to human-readable names; --numbered mirrors the source tree instead.
    # For music, use music_titles; for sfx, use sfx_titles.
    titles = {}
    if not numbered:
        if kind.name == "music":
            titles = {str(k): v for k, v in music_titles().items()}
        else:  # sfx
            titles = {str(k): v for k, v in sfx_titles().items()}

    with click.progressbar(entries, label=f"{kind.name}", show_pos=True) as bar:
        for e in bar:
            # By default use the human-readable title; --numbered (or no title) mirrors the source tree.
            dest = None
            if not numbered:
                # Extract the ID from the stem (e.g., "music098" -> "98")
                m = _re.match(r"(?:music|se)0*(\d+)$", e.stem, _re.IGNORECASE)
                sound_id = m.group(1) if m else None
                if sound_id and titles.get(sound_id):
                    # Sanitize the title for use as a filename (replace spaces/special chars).
                    safe_title = _re.sub(r'[<>:"/\\|?*]', '_', titles[sound_id]).replace(' ', '_')
                    dest = out / f"{safe_title}.wav"
            if dest is None:
                # --numbered, or no known title: mirror the source tree so the ~12k sfx
                # don't collide — exports/audio/sfx/<root>/seNNN/<stem>.wav
                dest = out / e.root / e.rel.with_suffix(".wav")
            try:
                h = core.decode_file(e.path, dest, loops=loops, vgmstream=vgm)
                ok += 1
                if h.sample_format == core.FMT_ATRAC3:
                    atrac += 1
            except (core.AudioError, OSError) as ex:
                failed.append((e.path.name, str(ex)))

    click.echo(f"Wrote {ok} WAV(s) -> {out}"
               + (f"  ({atrac} via vgmstream)" if atrac else ""))
    if failed:
        click.echo(f"{len(failed)} failed/skipped:")
        for name, msg in failed[:20]:
            click.echo(f"  {name}: {msg}")
        if len(failed) > 20:
            click.echo(f"  … and {len(failed) - 20} more")

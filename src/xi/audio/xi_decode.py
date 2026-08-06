#!/usr/bin/env python3
"""`xi audio decode` / `xi audio info` — one-off ops on explicit
`.bgw` / `.spw` file paths (no FFXI_DIR enumeration)."""

from pathlib import Path

import click

from xi.audio import xi_core as core


@click.command("decode")
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--out", "out_dir", type=click.Path(), default=None,
              help="Output dir (default: alongside each source file).")
@click.option("--loops/--no-loops", default=True,
              help="Embed a WAV smpl loop chunk for looped audio (default on).")
@click.option("--vgmstream", "vgmstream_opt", type=click.Path(), default=None,
              help="Path to vgmstream-cli for ATRAC3 (else auto-detected).")
@click.option("--native-only", is_flag=True,
              help="Decode only ADPCM/PCM natively; ATRAC3 files are skipped.")
def decode_cmd(files, out_dir, loops, vgmstream_opt, native_only):
    """Decode one or more .bgw/.spw files to .wav.

    \b
    ADPCM/PCM decode natively (byte-exact); ATRAC3 routes to vgmstream.
    Example:
      xi audio decode "D:/.../sound/win/se/se002/se002060.spw"
    """
    vgm = None if native_only else core.find_vgmstream(vgmstream_opt)
    if vgmstream_opt and vgm is None:
        raise click.ClickException(f"vgmstream not found at: {vgmstream_opt}")
    out = Path(out_dir) if out_dir else None
    failed = 0
    for f in files:
        src = Path(f)
        dest = (out / (src.stem + ".wav")) if out else src.with_suffix(".wav")
        try:
            h = core.decode_file(src, dest, loops=loops, vgmstream=vgm)
        except core.AudioError as ex:
            # Skip and keep going (matches the batch/export commands and the
            # documented behavior) — one ATRAC3 file without vgmstream should
            # not kill the rest of the invocation.
            click.echo(f"SKIP {src.name}: {ex}", err=True)
            failed += 1
            continue
        click.echo(f"{src.name} -> {dest}  "
                   f"[{h.format_name} {h.channels}ch {h.sample_rate}Hz "
                   f"{h.duration_sec:.1f}s{' loop' if h.looped else ''}]")
    if failed and failed == len(files):
        raise click.ClickException(f"all {failed} file(s) failed to decode")


@click.command("info")
@click.argument("file", type=click.Path(exists=True))
def info_cmd(file):
    """Dump the parsed header of a .bgw/.spw file."""
    data = Path(file).read_bytes()
    try:
        h = core.parse_header(data)
    except core.AudioError as ex:
        raise click.ClickException(str(ex))
    click.echo(f"file          {file}")
    click.echo(f"size on disk  {len(data)} bytes (header says {h.size})")
    click.echo(f"type          {h.type}")
    click.echo(f"format        {h.format_name} ({h.sample_format})")
    click.echo(f"id            {h.id}")
    click.echo(f"channels      {h.channels}")
    click.echo(f"sample rate   {h.sample_rate} Hz")
    click.echo(f"block size    {h.block_size}")
    click.echo(f"sample blocks {h.sample_blocks}")
    click.echo(f"total frames  {h.total_frames}")
    click.echo(f"duration      {h.duration_sec:.3f} s")
    click.echo(f"looped        {'yes, from frame ' + str(h.loop_start_frame) if h.looped else 'no'}")

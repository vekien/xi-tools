#!/usr/bin/env python3
"""`xi audio music` — list and decode FFXI music (`.bgw`) to WAV."""

import click

from xi.audio import xi_core as core
from xi.audio.xi_commands import run_export, run_list


@click.command("list")
@click.argument("names", nargs=-1)
@click.option("--root", default=None,
              help="Limit to one sound root (e.g. sound3). Default: all.")
def list_cmd(names, root):
    """List music tracks (optionally filtered by NAME substring)."""
    run_list(core.MUSIC, names, root)


@click.command("export")
@click.argument("names", nargs=-1)
@click.option("--out", "out_dir", type=click.Path(), default=None,
              help="Output dir (default: exports/audio/music/).")
@click.option("--root", default=None,
              help="Limit to one sound root (e.g. sound3). Default: all.")
@click.option("--limit", type=int, default=None, help="Stop after N files.")
@click.option("--loops/--no-loops", default=True,
              help="Embed a WAV smpl loop chunk for looped tracks (default on).")
@click.option("--vgmstream", "vgmstream_opt", type=click.Path(), default=None,
              help="Path to vgmstream-cli for ATRAC3 (else auto-detected).")
@click.option("--native-only", is_flag=True,
              help="Decode only ADPCM/PCM natively; skip ATRAC3 entirely.")
@click.option("--numbered", is_flag=True,
              help="Mirror the source tree (music###) instead of human-readable names.")
def export_cmd(names, out_dir, root, limit, loops, vgmstream_opt, native_only, numbered):
    """Decode music to WAV (all, or only those matching NAME).

    Files are named by track title (e.g. Ronfaure.wav) by default; pass
    --numbered to mirror the source tree (music###.wav) instead. Tracks
    without a known title always fall back to the source-tree layout.

    ADPCM/PCM tracks decode natively (byte-exact, no dependencies). ATRAC3
    tracks (~36% of music) need vgmstream-cli — auto-detected, or pass
    --vgmstream / set XI_VGMSTREAM.
    """
    run_export(core.MUSIC, names, out_dir, root, limit, loops,
               vgmstream_opt, native_only, numbered=numbered)

#!/usr/bin/env python3
"""`xi audio sfx` — list and decode FFXI sound effects (`.spw`) to WAV."""

import click

from xi.audio import xi_core as core
from xi.audio.xi_commands import run_export, run_list


@click.command("list")
@click.argument("names", nargs=-1)
@click.option("--root", default=None,
              help="Limit to one sound root (e.g. sound3). Default: all.")
def list_cmd(names, root):
    """List sound effects (optionally filtered by NAME substring, e.g. se002)."""
    run_list(core.SFX, names, root)


@click.command("export")
@click.argument("names", nargs=-1)
@click.option("--out", "out_dir", type=click.Path(), default=None,
              help="Output dir (default: exports/audio/sfx/).")
@click.option("--root", default=None,
              help="Limit to one sound root (e.g. sound3). Default: all.")
@click.option("--limit", type=int, default=None, help="Stop after N files.")
@click.option("--loops/--no-loops", default=True,
              help="Embed a WAV smpl loop chunk for looped effects (default on).")
@click.option("--vgmstream", "vgmstream_opt", type=click.Path(), default=None,
              help="Path to vgmstream-cli for ATRAC3 (else auto-detected).")
@click.option("--native-only", is_flag=True,
              help="Decode only ADPCM/PCM natively; skip ATRAC3 entirely.")
@click.option("--numbered", is_flag=True,
              help="Mirror the source tree (seNNNNNN) instead of human-readable names.")
def export_cmd(names, out_dir, root, limit, loops, vgmstream_opt, native_only, numbered):
    """Decode sound effects to WAV (all, or only those matching NAME).

    Named effects use their title by default; pass --numbered to mirror the
    source tree (seNNNNNN.wav) instead. SFX titles are partial (system/menu/
    category sounds), so most effects fall back to the source-tree layout.

    Most effects are ADPCM or PCM (decoded natively, byte-exact). A minority are
    ATRAC3 and need vgmstream-cli — auto-detected, or pass --vgmstream / set
    XI_VGMSTREAM.
    """
    run_export(core.SFX, names, out_dir, root, limit, loops,
               vgmstream_opt, native_only, numbered=numbered)

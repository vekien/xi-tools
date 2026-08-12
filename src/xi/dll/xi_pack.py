"""xi dll <target> pack — re-compress .text into POL1 (game-loadable)."""
from __future__ import annotations

from pathlib import Path

import click

from xi.dll.pol1_io import pack_dll
from xi.dll.targets import DllTarget, get_target


def make_pack_cmd(target: DllTarget):
    help_text = f"Compress .text into POL1 -> packed {target.display} (game-loadable)."

    @click.command("pack", help=help_text)
    @click.option(
        "--template",
        "template_dll",
        type=click.Path(exists=True, path_type=Path),
        default=None,
        help=f"Original packed {target.display} (PE shell + POL1 stub).",
    )
    @click.option(
        "--unpacked",
        type=click.Path(exists=True, path_type=Path),
        default=None,
        help="Unpacked DLL whose .text will be re-compressed.",
    )
    @click.option(
        "--text-bin",
        type=click.Path(exists=True, path_type=Path),
        default=None,
        help="Raw decompressed .text bytes (alternative to --unpacked).",
    )
    @click.option(
        "--output",
        type=click.Path(path_type=Path),
        default=None,
        help=f"Output packed DLL [default: misc/{Path(target.filename).stem}_repacked.dll]",
    )
    @click.option(
        "--verify/--no-verify",
        default=True,
        show_default=True,
        help="Decompress result and compare to input .text.",
    )
    def cmd(
        template_dll: Path | None,
        unpacked: Path | None,
        text_bin: Path | None,
        output: Path | None,
        verify: bool,
    ) -> None:
        tmpl = template_dll or target.resolve_packed()
        if tmpl is None:
            raise click.ClickException(
                f"Could not find template {target.display}. Pass --template PATH."
            )
        out = output or target.misc_repacked
        info = pack_dll(
            tmpl,
            out,
            unpacked=unpacked,
            text_bin=text_bin,
            verify=verify,
        )
        click.echo(f"Template        : {info['template']}")
        click.echo(f".text in        : {info['text_len']:,} bytes")
        click.echo(f"POL1 payload max: {info['payload_max']:#x} ({info['payload_max']:,}) bytes")
        click.echo(f"Stub RVA        : {info['stub_rva']:#x}")
        click.echo(f"Stub dst size   : {info['dst_size']:#x}")
        if info["identity"]:
            click.echo("Identity match with template .text — reusing original POL1 payload")
        else:
            click.echo(f"Compressed      : {info['compressed_len']:,} bytes")
        if verify:
            click.echo("Verify          : OK")
        click.echo(f"\nWrote packed DLL : {info['output']}")
        click.echo(f"  size           : {info['size']:,}")
        click.echo(f"  EP RVA         : {info['ep_rva']:#x}")
        click.echo(f"Done. Drop over game {target.display} to test (keep a backup).")

    return cmd


cmd_ffximain = make_pack_cmd(get_target("ffximain"))
cmd_polcore = make_pack_cmd(get_target("polcore"))
cmd_app = make_pack_cmd(get_target("app"))

"""Click groups: ``xi dll <ffximain|polcore|app> …``."""
from __future__ import annotations

import click

from xi.dll import xi_pack as dll_pack
from xi.dll import xi_patch as dll_patch
from xi.dll import xi_sig as dll_sig
from xi.dll import xi_unpack as dll_unpack
from xi.dll.targets import TARGETS
from xi.ffximain import xi_crashdump as ffximain_crashdump
from xi.ffximain import xi_gear_patch as ffximain_gear_patch
from xi.ffximain import xi_geargroups as ffximain_geargroups
from xi.ffximain import xi_text_dump as ffximain_text_dump


@click.group("dll")
def dll_group():
    """POL1-packed client DLLs (FFXiMain, polcore, app, …).

    \b
    Examples:
      xi dll ffximain unpack
      xi dll polcore unpack
      xi dll app unpack
      xi dll ffximain pack --unpacked misc/FFXiMain_unpacked.dll
      xi dll ffximain crashdump
    """
    pass


@dll_group.command("list")
def dll_list():
    """List known client DLL targets and resolved packed paths."""
    for key, t in TARGETS.items():
        found = t.resolve_packed()
        status = str(found) if found else "(not found — pass --dll)"
        click.echo(f"{key:12} {t.display:16} {status}")
        if t.description:
            click.echo(f"{'':12} {t.description}")


@dll_group.group("ffximain")
def ffximain_group():
    """FFXiMain.dll — main game client (POL1 unpack/pack + RE helpers)."""
    pass


ffximain_group.add_command(dll_unpack.cmd_ffximain, "unpack")
ffximain_group.add_command(dll_pack.cmd_ffximain, "pack")
ffximain_group.add_command(dll_patch.cmd_ffximain, "patch")
ffximain_group.add_command(dll_sig.cmd_gen, "sig-gen")
ffximain_group.add_command(dll_sig.cmd_apply, "sig-apply")
ffximain_group.add_command(ffximain_text_dump.cmd, "text-dump")
ffximain_group.add_command(ffximain_geargroups.cmd, "gear-groups")
ffximain_group.add_command(ffximain_gear_patch.cmd, "gear-patch")
ffximain_group.add_command(ffximain_crashdump.cmd, "crashdump")


@dll_group.group("polcore")
def polcore_group():
    """polcore.dll — PlayOnline COM host (POL1 unpack/pack)."""
    pass


polcore_group.add_command(dll_unpack.cmd_polcore, "unpack")
polcore_group.add_command(dll_pack.cmd_polcore, "pack")


@dll_group.group("app")
def app_group():
    """app.dll — PlayOnline Viewer UI module (POL1 unpack/pack)."""
    pass


app_group.add_command(dll_unpack.cmd_app, "unpack")
app_group.add_command(dll_pack.cmd_app, "pack")

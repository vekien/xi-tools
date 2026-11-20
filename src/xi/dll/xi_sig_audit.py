"""``xi dll ffximain sig-audit``: check a C++ project's byte patterns against two FFXiMain.dll builds.

The command only parses options and prints; the audit is ``xi.dll.sig_audit``. It reads
the two DLLs and the source tree, and writes the unpacked DLLs and ``report.md`` to
``--output-dir``; the game install is never written, so there is no ``--dry-run``.
See ``docs/dll/sig-audit.md``.
"""
from __future__ import annotations

from pathlib import Path

import click

import xi.xi_config as cfg
from xi.dll import sig_audit


@click.command("sig-audit")
@click.argument("old", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("new", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--src", "src", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="The C++ source tree whose hex pattern strings are checked (.cpp .hpp .h .c .inc).")
@click.option("--output-dir", type=click.Path(file_okay=False, path_type=Path), default=None,
              help="Where report.md and the unpacked DLLs go  [default: exports/dll/sig-audit]")
@click.option("--verbose", is_flag=True,
              help="List the patterns with several matches, and every changed instruction, not a few per function.")
def cmd(old: Path, new: Path, src: Path, output_dir: Path | None, verbose: bool) -> None:
    """Check a project's FFXiMain.dll byte patterns against two builds of the DLL, offline.

    OLD and NEW are FFXiMain.dll files (the game's packed file, or one already
    unpacked). Every hex pattern string in --src ("8B 0D ?? ?? ?? ?? 85 C9") is searched
    for in both; each match's function is compared between the builds, and the
    structure offsets that moved are listed. The report is printed and written to
    report.md.

    \b
    Examples:
      xi dll ffximain sig-audit old/FFXiMain.dll new/FFXiMain.dll --src ../myproject/src
      xi dll ffximain sig-audit old.dll new.dll --src src --output-dir build/sig-audit --verbose
    """
    output_dir = output_dir or Path(cfg.XI_TOOLS_DIR) / "exports" / "dll" / "sig-audit"
    click.echo(f"Unpacking and aligning {old.name} and {new.name}…", err=True)
    try:
        result = sig_audit.run(old, new, src, output_dir, verbose=verbose)
    except sig_audit.SigAuditError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(result["report"], nl=False)
    lost = len(result["lost"])
    colour = "yellow" if lost else "green"
    mark = "" if lost else "✓ "
    click.echo(click.style(f"{mark}{len(result['rows'])} patterns, {lost} found in old but not in new; "
                           f"report: {result['report_path']}", fg=colour), err=True)

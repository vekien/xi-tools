#!/usr/bin/env python3
"""`xi fx delete` / `xi fx delete-group` — remove effect(s) by name/prefix or group."""

import re
from pathlib import Path
from typing import List

import click

from xi.xi_config import editable_dat, output_path_for
from xi.fx.xi_core import parse_sections, resolve_dat_path, EFFECT_TYPE, _fourcc, _matches
from xi.fx.xi_list import list_effects


def delete_effects(dat_path: Path, names) -> List[str]:
    """Splice out every `0x05` effect matching a name (exact or prefix). Returns the
    removed names. Operates on the current (already-edited) DAT — does NOT reset to
    pristine — so it stacks on top of mesh-merge/placement edits."""
    dat = editable_dat(dat_path, fresh=False)
    data = bytearray(dat.read_bytes())
    sections = parse_sections(data)
    matched = [s for s in sections
               if s.type_code == EFFECT_TYPE and _matches(_fourcc(data, s.start), names)]
    removed = [_fourcc(data, s.start) for s in matched]
    for s in sorted(matched, key=lambda x: x.start, reverse=True):  # splice from the end
        data = data[:s.start] + data[s.start + s.size:]
    dat.write_bytes(data)
    return removed


@click.command()
@click.argument("dat_path")
@click.argument("names", nargs=-1, required=True)
@click.option("--dry-run", is_flag=True, help="Show what would be removed without writing.")
def delete_cmd(dat_path, names, dry_run):
    """Delete effect(s) by exact name or name-prefix.

    Examples: `xi fx delete ROM/1/41 grid` (one) — `xi fx delete ROM/1/41 tki awa grid`
    (prefixes: removes tki1-5, awa1-6, and grid). Keeps a <dat>.base backup.
    """
    try:
        resolved = resolve_dat_path(dat_path)
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    if dry_run:
        hits = [e["name"] for e in list_effects(resolved) if _matches(e["name"], names)]
        if not hits:
            raise click.ClickException(f"No effects match: {', '.join(names)}")
        click.echo(f"Would remove {len(hits)}: {', '.join(hits)}")
        return
    removed = delete_effects(resolved, names)
    if not removed:
        raise click.ClickException(f"No effects match: {', '.join(names)}")
    click.echo(f"Removed {len(removed)} effect(s): {', '.join(removed)}")
    click.echo(f"Wrote: {output_path_for(resolved)}")


def _group_pattern(seed: str):
    """Return a compiled regex matching the fixture group for *seed*.
    e.g. 'xfd0' -> stem='xf', index='0' -> matches xfm0, xfi0, xfd0, xfl0, xfr0, xfs0."""
    s = seed.strip()
    # Cross-zone names are exactly 4 chars: x<familyChar><roleChar><index>
    # stem = first 2 chars, role = 3rd char, index = 4th char.
    if len(s) != 4 or not s.isalnum():
        raise ValueError(f"Cannot determine group from '{seed}' — expected 4-char name e.g. xfd0.")
    stem, index = s[:2], s[3]
    return re.compile(rf'^{re.escape(stem)}[a-zA-Z]{re.escape(index)}\s*$')


@click.command("delete-group")
@click.argument("dat_path")
@click.argument("seed_name")
@click.option("--dry-run", is_flag=True, help="Show what would be removed without writing.")
def delete_group_cmd(dat_path, seed_name, dry_run):
    """Delete an entire fixture group by any member name.

    Matches all effects sharing the same stem and index suffix — e.g. 'xfd0'
    deletes xfm0, xfi0, xfd0, xfl0, xfr0, xfs0 but leaves xfd1, xfd2 alone.

    \b
    Examples:
      xi fx delete-group ROM/1/41 xfd0 --dry-run
      xi fx delete-group ROM/1/41 xfd0
    """
    try:
        resolved = resolve_dat_path(dat_path)
        pat = _group_pattern(seed_name)
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e))
    names_match = lambda n: bool(pat.match(n))
    if dry_run:
        hits = [e["name"] for e in list_effects(resolved) if names_match(e["name"])]
        if not hits:
            raise click.ClickException(f"No effects match group '{seed_name}'")
        click.echo(f"Would remove {len(hits)}: {', '.join(hits)}")
        return
    dat = editable_dat(resolved, fresh=False)
    data = bytearray(dat.read_bytes())
    sections = parse_sections(data)
    matched = [s for s in sections if s.type_code == EFFECT_TYPE and names_match(_fourcc(data, s.start))]
    if not matched:
        raise click.ClickException(f"No effects match group '{seed_name}'")
    removed = [_fourcc(data, s.start).strip() for s in matched]
    for s in sorted(matched, key=lambda x: x.start, reverse=True):
        data = data[:s.start] + data[s.start + s.size:]
    dat.write_bytes(data)
    click.echo(f"Removed {len(removed)} effect(s): {', '.join(removed)}")
    click.echo(f"Wrote: {output_path_for(resolved)}")

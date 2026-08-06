#!/usr/bin/env python3
"""Copy zone-local footstep sound pointers from a donor DAT.

Footstep terrain selection lives in collision, but the sound lookup resolves a
zone-local 0x3D SoundEffectPointer child under the zone's fses directory. Some
blank/template zones keep fses/fefs visual effects while stripping every 0x3D,
which leaves correct terrain flags but silent footsteps.
"""

from pathlib import Path
from typing import Iterable, List, Tuple

import click

from xi.entity.anim.xi_export import Section, parse_sections
from xi.entity.mesh.xi_export import resolve_dat_path
from xi.xi_config import editable_dat, read_path_for


SECTION_DIRECTORY = 0x01
SECTION_END = 0x00
SECTION_SOUND_POINTER = 0x3D

_TERRAIN_NAMES = [
    "object", "path", "grass", "sand", "snow", "stone",
    "metal", "wood", "shallowwater", "deepwater", "unk0xa",
]


def _clean_name(name: str) -> str:
    return name.rstrip("\x00 ")


def _resolve(dat_path: str) -> Path:
    try:
        return Path(resolve_dat_path(dat_path))
    except (FileNotFoundError, ValueError):
        p = Path(dat_path)
        if p.is_file():
            return p
        raise click.ClickException(f"DAT not found: {dat_path}")


def _find_directory(sections: List[Section], name: str) -> Tuple[int, int]:
    """Return (open_index, close_index) for the first directory named *name*."""
    for i, section in enumerate(sections):
        if section.type_code != SECTION_DIRECTORY or _clean_name(section.name) != name:
            continue
        depth = 1
        for j in range(i + 1, len(sections)):
            s = sections[j]
            if s.type_code == SECTION_DIRECTORY:
                depth += 1
            elif s.type_code == SECTION_END:
                depth -= 1
                if depth == 0:
                    return i, j
        raise ValueError(f"directory {name!r} has no closing end section")
    raise ValueError(f"DAT has no {name!r} directory")


def _top_level_sound_sections(sections: List[Section], open_idx: int, close_idx: int) -> List[Section]:
    """0x3D children directly under a directory, skipping nested directories."""
    out: List[Section] = []
    depth = 0
    for s in sections[open_idx + 1:close_idx]:
        if depth == 0 and s.type_code == SECTION_SOUND_POINTER:
            out.append(s)
        if s.type_code == SECTION_DIRECTORY:
            depth += 1
        elif s.type_code == SECTION_END:
            depth -= 1
    return out


def _section_blob(data: bytes, sections: Iterable[Section]) -> bytes:
    return b"".join(data[s.start:s.start + s.size] for s in sections)


def _terrain_prefix(terrain: str | None) -> str | None:
    if terrain is None:
        return None
    value = terrain.strip().lower()
    if not value:
        return None
    if value in _TERRAIN_NAMES:
        idx = _TERRAIN_NAMES.index(value)
    else:
        try:
            idx = int(value, 0)
        except ValueError as exc:
            raise click.ClickException(f"unknown terrain: {terrain!r}") from exc
    if idx < 0 or idx > 10:
        raise click.ClickException("terrain must be 0..10 or one of: " + ", ".join(_TERRAIN_NAMES))
    return f"0{idx:x}"


def copy_footstep_sound_pointers(target_dat: Path, donor_dat: Path, *, replace: bool = False,
                                 terrain: str | None = None, dry_run: bool = False,
                                 target_path: Path | None = None
                                 ) -> Tuple[Path, int, int, int]:
    """Copy donor fses/*.0x3D sound pointers into target.

    Returns (output_path, copied, skipped_existing, removed_existing).
    """
    donor_data = donor_dat.read_bytes()
    donor_sections = parse_sections(donor_data)
    donor_open, donor_close = _find_directory(donor_sections, "fses")
    donor_sounds = _top_level_sound_sections(donor_sections, donor_open, donor_close)

    prefix = _terrain_prefix(terrain)
    if prefix is not None:
        donor_sounds = [s for s in donor_sounds if _clean_name(s.name).lower().startswith(prefix)]
    if not donor_sounds:
        raise ValueError("donor fses contains no matching 0x3D sound pointers")

    target = Path(target_path) if target_path is not None else (
        read_path_for(target_dat) if dry_run else editable_dat(target_dat, fresh=False)
    )
    target_data = bytearray(target.read_bytes())
    target_sections = parse_sections(target_data)
    target_open, target_close = _find_directory(target_sections, "fses")
    target_sounds = _top_level_sound_sections(target_sections, target_open, target_close)
    existing_names = {_clean_name(s.name) for s in target_sounds}

    if replace:
        selected = donor_sounds
        skipped = 0
    else:
        selected = [s for s in donor_sounds if _clean_name(s.name) not in existing_names]
        skipped = len(donor_sounds) - len(selected)
    if not selected:
        return target, 0, skipped, 0

    if dry_run:
        removed = len(target_sounds) if replace else 0
        return target, len(selected), skipped, removed

    if replace and target_sounds:
        for s in sorted(target_sounds, key=lambda x: x.start, reverse=True):
            del target_data[s.start:s.start + s.size]
        target_sections = parse_sections(target_data)
        target_open, target_close = _find_directory(target_sections, "fses")

    insert_at = target_sections[target_close].start
    target_data[insert_at:insert_at] = _section_blob(donor_data, selected)
    target.write_bytes(target_data)
    removed = len(target_sounds) if replace else 0
    return target, len(selected), skipped, removed


@click.command("footsteps")
@click.argument("target_dat")
@click.option("--from", "donor_dat", required=True,
              help="Donor zone DAT with working fses 0x3D footstep sound pointers, e.g. ROM/0/57.")
@click.option("--terrain", default=None,
              help="Only copy one terrain family (name or 0..10), e.g. snow. Default: all fses sound pointers.")
@click.option("--replace", is_flag=True,
              help="Remove existing top-level fses 0x3D sound pointers before copying donor pointers.")
@click.option("--dry-run", is_flag=True, help="Report what would be copied without writing.")
def cmd(target_dat, donor_dat, terrain, replace, dry_run):
    """Copy footstep sound pointers into a zone DAT.

    Collision terrain selects the footstep family, but the zone must also have
    matching 0x3D SoundEffectPointer children under fses. This command copies
    those pointer sections from a retail/donor zone; it does not copy OGG/SPW
    audio files and does not touch collision, meshes, weather, or fefs VFX.
    """
    target = _resolve(target_dat)
    donor = _resolve(donor_dat)
    try:
        out, copied, skipped, removed = copy_footstep_sound_pointers(
            target, donor, replace=replace, terrain=terrain, dry_run=dry_run)
    except ValueError as exc:
        raise click.ClickException(str(exc))

    action = "Would copy" if dry_run else "Copied"
    click.echo(f"{action} {copied} fses sound pointer(s) from {donor} into {out}")
    if skipped:
        click.echo(f"Skipped {skipped} pointer(s) already present")
    if removed:
        msg = "Would remove" if dry_run else "Removed"
        click.echo(f"{msg} {removed} existing target pointer(s) due to --replace")

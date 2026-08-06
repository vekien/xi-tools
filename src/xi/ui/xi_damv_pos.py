"""Inspect and patch position-like value curves in damv DATs."""

import struct
from dataclasses import dataclass
from pathlib import Path

import click

from xi.xi_config import FFXI_DIR, editable_dat, read_path_for


@dataclass
class DamvPoint:
    index: int
    offset: int
    time: float
    value: float


@dataclass
class DamvCurve:
    tag: str
    offset: int
    type_value: int
    points: list[DamvPoint]


def _resolve_dat_path(dat_path: str) -> Path:
    p = Path(dat_path)
    if not p.is_absolute():
        p = Path(FFXI_DIR) / p
    if not p.exists():
        raise click.ClickException(f'DAT not found: {p}')
    return p


def _is_curve_tag(data: bytes, off: int) -> bool:
    if off + 8 > len(data):
        return False
    tag = data[off:off + 4]
    if not all(0x30 <= b <= 0x7a for b in tag):
        return False
    type_value = struct.unpack_from('<I', data, off + 4)[0]
    return type_value in {0x00000219, 0x00000199}


def parse_damv_curves(data: bytes) -> list[DamvCurve]:
    if len(data) < 0x20 or data[:4] != b'damv':
        raise ValueError('Not a damv DAT file')

    starts = [off for off in range(0x20, len(data) - 8, 4) if _is_curve_tag(data, off)]
    curves: list[DamvCurve] = []
    for i, off in enumerate(starts):
        tag = data[off:off + 4].decode('ascii', 'replace')
        type_value = struct.unpack_from('<I', data, off + 4)[0]
        next_off = starts[i + 1] if i + 1 < len(starts) else len(data)
        end = min(next_off, off + (0x30 if type_value == 0x00000199 else 0x40))

        points: list[DamvPoint] = []
        point_off = off + 0x10
        point_index = 0
        while point_off + 8 <= end:
            time, value = struct.unpack_from('<ff', data, point_off)
            points.append(DamvPoint(point_index, point_off, time, value))
            point_off += 8
            point_index += 1

        curves.append(DamvCurve(tag, off, type_value, points))
    return curves


def find_curve(curves: list[DamvCurve], tag: str) -> DamvCurve | None:
    for curve in curves:
        if curve.tag == tag:
            return curve
    return None


def _print_curves(curves: list[DamvCurve], all_points: bool) -> None:
    click.echo(f'  {"tag":<5} {"off":>8} {"type":>8} {"pts":>3}  pt {"pt_off":>8} {"time":>9} {"value":>9}')
    for curve in curves:
        points = curve.points if all_points else curve.points[:3]
        for point in points:
            click.echo(
                f'  {curve.tag:<5} 0x{curve.offset:06x} 0x{curve.type_value:06x} '
                f'{len(curve.points):>3}  {point.index:>2} 0x{point.offset:06x} '
                f'{point.time:>9.3f} {point.value:>9.3f}'
            )


@click.command('damv-pos')
@click.argument('dat_path', metavar='DAT_FILE')
@click.option('--curve', 'curve_tag', default=None,
              help='Curve tag to inspect or patch, e.g. m0py, c0py, c0sx.')
@click.option('--point', 'point_index', default=1, type=int, show_default=True,
              help='Curve point index to patch.')
@click.option('--time', 'new_time', default=None, type=float, help='New point time value.')
@click.option('--value', 'new_value', default=None, type=float, help='New point value.')
@click.option('--all-points', is_flag=True, help='Show every point for every curve.')
@click.option('--dry-run', is_flag=True, help='Show what would change without writing.')
def cmd(
    dat_path: str,
    curve_tag: str | None,
    point_index: int,
    new_time: float | None,
    new_value: float | None,
    all_points: bool,
    dry_run: bool,
):
    """Inspect and patch value curves in a damv DAT.

    This is for ROM/0/27.DAT. Curve names such as m0py/c0py look position-like,
    while sx/sy/rz/ca names look like scale, rotation, or alpha/color curves.
    """
    p = _resolve_dat_path(dat_path)
    data = bytearray(read_path_for(p).read_bytes())
    try:
        curves = parse_damv_curves(data)
    except ValueError as e:
        raise click.ClickException(str(e))

    if not curves:
        raise click.ClickException('No damv curve records found.')

    patching = new_time is not None or new_value is not None
    if patching:
        if not curve_tag:
            raise click.ClickException('--curve TAG is required when patching.')
        curve = find_curve(curves, curve_tag)
        if curve is None:
            raise click.ClickException(
                f'Curve {curve_tag!r} not found. Known curves: '
                f'{", ".join(c.tag for c in curves)}'
            )
        if point_index < 0 or point_index >= len(curve.points):
            raise click.ClickException(
                f'--point {point_index} out of range for {curve.tag}; valid 0-{len(curve.points) - 1}'
            )

        point = curve.points[point_index]
        click.echo(f'File: {p}')
        click.echo(
            f'[{curve.tag}] point {point.index} at 0x{point.offset:06x}  '
            f'before: time={point.time:.3f} value={point.value:.3f}'
        )
        if new_time is not None:
            click.echo(f'  time: {point.time:.3f} -> {new_time:.3f}')
        if new_value is not None:
            click.echo(f'  value: {point.value:.3f} -> {new_value:.3f}')

        if dry_run:
            click.echo('(dry-run - not written)')
            return

        if new_time is not None:
            struct.pack_into('<f', data, point.offset, new_time)
        if new_value is not None:
            struct.pack_into('<f', data, point.offset + 4, new_value)
        out = editable_dat(p, fresh=False)
        out.write_bytes(data)
        click.echo(f'Written -> {out}')
        return

    click.echo(f'DAMV curves in {p}')
    click.echo()
    if curve_tag:
        curve = find_curve(curves, curve_tag)
        if curve is None:
            raise click.ClickException(
                f'Curve {curve_tag!r} not found. Known curves: '
                f'{", ".join(c.tag for c in curves)}'
            )
        _print_curves([curve], True)
    else:
        _print_curves(curves, all_points)

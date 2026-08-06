"""Inspect and patch position-like keyframes in small sel_ scene-control DATs."""

import struct
from dataclasses import dataclass
from pathlib import Path

import click

from xi.xi_config import FFXI_DIR, editable_dat, read_path_for


@dataclass
class SelKeyframe:
    index: int
    offset: int
    x: float
    y: float
    z: float
    w: float


@dataclass
class SelRecord:
    tag: str
    offset: int
    type_value: int
    key_count: int
    flag: int
    keyframes: list[SelKeyframe]


def _resolve_dat_path(dat_path: str) -> Path:
    p = Path(dat_path)
    if not p.is_absolute():
        p = Path(FFXI_DIR) / p
    if not p.exists():
        raise click.ClickException(f'DAT not found: {p}')
    return p


def parse_sel_records(data: bytes) -> list[SelRecord]:
    if len(data) < 0x20 or data[:4] != b'sel_':
        raise ValueError('Not a sel_ DAT file')

    records: list[SelRecord] = []
    off = 0x20
    while off + 0x28 <= len(data):
        tag = data[off:off + 4].decode('ascii', 'replace')
        if not (len(tag) == 4 and tag[0] == 's' and tag[1:].isdigit()):
            break

        type_value = struct.unpack_from('<I', data, off + 4)[0]
        key_count = struct.unpack_from('<I', data, off + 0x20)[0]
        flag = struct.unpack_from('<I', data, off + 0x24)[0]
        if key_count <= 0 or key_count > 64:
            break

        key_start = off + 0x30
        next_off = key_start + key_count * 0x30
        if next_off > len(data):
            break

        keyframes: list[SelKeyframe] = []
        for i in range(key_count):
            key_off = key_start + i * 0x30
            x, y, z, w = struct.unpack_from('<ffff', data, key_off)
            keyframes.append(SelKeyframe(i, key_off, x, y, z, w))

        records.append(SelRecord(tag, off, type_value, key_count, flag, keyframes))
        off = next_off

    return records


def find_record(records: list[SelRecord], tag: str) -> SelRecord | None:
    for record in records:
        if record.tag == tag:
            return record
    return None


def _print_records(records: list[SelRecord], show_all_keys: bool) -> None:
    click.echo(f'  {"tag":<5} {"off":>8} {"type":>8} {"keys":>4} {"flag":>4}  key  {"key_off":>8} {"x":>10} {"y":>10} {"z":>10} {"w":>10}')
    for record in records:
        keys = record.keyframes if show_all_keys else record.keyframes[:1]
        for key in keys:
            click.echo(
                f'  {record.tag:<5} 0x{record.offset:06x} 0x{record.type_value:06x} '
                f'{record.key_count:>4} {record.flag:>4}  '
                f'{key.index:>3} 0x{key.offset:06x} '
                f'{key.x:>10.3f} {key.y:>10.3f} {key.z:>10.3f} {key.w:>10.3f}'
            )


@click.command('sel-pos')
@click.argument('dat_path', metavar='DAT_FILE')
@click.option('--record', 'record_tag', default=None,
              help='s### record to inspect or patch, e.g. s100.')
@click.option('--key', 'key_index', default=0, type=int, show_default=True,
              help='Keyframe index to patch.')
@click.option('--x', 'new_x', default=None, type=float, help='New x float32 value.')
@click.option('--y', 'new_y', default=None, type=float, help='New y float32 value.')
@click.option('--z', 'new_z', default=None, type=float, help='New z float32 value.')
@click.option('--all-keys', is_flag=True, help='Show every keyframe, not just key 0.')
@click.option('--dry-run', is_flag=True, help='Show what would change without writing.')
def cmd(
    dat_path: str,
    record_tag: str | None,
    key_index: int,
    new_x: float | None,
    new_y: float | None,
    new_z: float | None,
    all_keys: bool,
    dry_run: bool,
):
    """Inspect and patch position-like keyframes in a sel_ DAT.

    This is for small files like ROM/0/24.DAT, ROM/0/25.DAT, and ROM/0/26.DAT.
    It edits the first three float32 values of a selected s### keyframe.
    """
    p = _resolve_dat_path(dat_path)
    data = bytearray(read_path_for(p).read_bytes())
    try:
        records = parse_sel_records(data)
    except ValueError as e:
        raise click.ClickException(str(e))

    if not records:
        raise click.ClickException('No s### keyframe records found.')

    patching = new_x is not None or new_y is not None or new_z is not None
    if patching:
        if not record_tag:
            raise click.ClickException('--record s### is required when patching.')
        record = find_record(records, record_tag)
        if record is None:
            raise click.ClickException(
                f'Record {record_tag!r} not found. Known records: '
                f'{", ".join(r.tag for r in records)}'
            )
        if key_index < 0 or key_index >= len(record.keyframes):
            raise click.ClickException(
                f'--key {key_index} out of range for {record.tag}; valid 0-{len(record.keyframes) - 1}'
            )

        key = record.keyframes[key_index]
        click.echo(f'File: {p}')
        click.echo(
            f'[{record.tag}] key {key.index} at 0x{key.offset:06x}  '
            f'before: x={key.x:.3f} y={key.y:.3f} z={key.z:.3f}'
        )
        if new_x is not None:
            click.echo(f'  x: {key.x:.3f} -> {new_x:.3f}')
        if new_y is not None:
            click.echo(f'  y: {key.y:.3f} -> {new_y:.3f}')
        if new_z is not None:
            click.echo(f'  z: {key.z:.3f} -> {new_z:.3f}')

        if dry_run:
            click.echo('(dry-run - not written)')
            return

        if new_x is not None:
            struct.pack_into('<f', data, key.offset, new_x)
        if new_y is not None:
            struct.pack_into('<f', data, key.offset + 4, new_y)
        if new_z is not None:
            struct.pack_into('<f', data, key.offset + 8, new_z)
        out = editable_dat(p, fresh=False)
        out.write_bytes(data)
        click.echo(f'Written -> {out}')
        return

    click.echo(f'SEL position records in {p}')
    click.echo()
    _print_records(records, all_keys or record_tag is not None)

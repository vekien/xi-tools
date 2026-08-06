"""First-pass inspector for ROM/118/114.DAT mnc2/menu data."""

import struct
from dataclasses import dataclass
from pathlib import Path

import click

from xi.xi_config import FFXI_DIR, editable_dat, read_path_for


@dataclass
class Mnc2Block:
    tag: str
    offset: int
    next_offset: int
    size: int


@dataclass(frozen=True)
class BlockLayout:
    data_skip: int
    record_size: int | None
    record_count: int | None
    note: str


BLOCK_LAYOUTS = {
    # FFXiMain stores each located block pointer as block_start + 0x30.
    'mnc2': BlockLayout(0x30, None, None, 'indexed model/animation tables'),
    'mon_': BlockLayout(0x30, None, None, 'uint16 lookup table'),
    'levc': BlockLayout(0x30, None, None, 'uint16 level/curve table'),
    'mgc_': BlockLayout(0x30, 0x64, 0x400, '1024 fixed records'),
    'comm': BlockLayout(0x30, 0x30, 0x700, '1792 fixed records'),
    'end\\0': BlockLayout(0, None, None, 'terminator'),
}


def _resolve_dat_path(dat_path: str) -> Path:
    p = Path(dat_path)
    if not p.is_absolute():
        p = Path(FFXI_DIR) / p
    if not p.exists():
        raise click.ClickException(f'DAT not found: {p}')
    return p


def find_blocks(data: bytes) -> list[Mnc2Block]:
    known = []
    for off in range(0, max(0, len(data) - 4)):
        tag = bytes(data[off:off + 4])
        if tag in {b'mnc2', b'mon_', b'levc', b'mgc_', b'comm', b'end\0'}:
            known.append((off, tag.decode('ascii', 'replace').replace('\0', '\\0')))

    blocks: list[Mnc2Block] = []
    for i, (off, tag) in enumerate(known):
        next_off = known[i + 1][0] if i + 1 < len(known) else len(data)
        blocks.append(Mnc2Block(tag, off, next_off, next_off - off))
    return blocks


def _candidate_pairs(data: bytes, start: int, end: int) -> list[tuple[int, int, int, int, int]]:
    """Find plausible int16 x/y-ish pairs and following w/h-ish pairs."""
    hits: list[tuple[int, int, int, int, int]] = []
    for off in range(start, max(start, end - 8), 2):
        x, y, w, h = struct.unpack_from('<hhhh', data, off)
        if -2000 <= x <= 2000 and -2000 <= y <= 2000 and 0 <= w <= 2000 and 0 <= h <= 2000:
            # Skip all-zero/padding-like rows and tiny control tables unless they look UI-sized.
            if (x, y, w, h) == (0, 0, 0, 0):
                continue
            if w >= 8 and h >= 8:
                hits.append((off, x, y, w, h))
    return hits


def _print_blocks(blocks: list[Mnc2Block]) -> None:
    click.echo('Blocks:')
    for block in blocks:
        layout = BLOCK_LAYOUTS.get(block.tag)
        extra = ''
        if layout:
            data_off = block.offset + layout.data_skip
            extra = f' data=0x{data_off:06x}'
            if layout.record_size and layout.record_count:
                extra += f' rec=0x{layout.record_size:x}x{layout.record_count}'
            extra += f' ({layout.note})'
        click.echo(
            f'  {block.tag:<5} 0x{block.offset:06x}-0x{block.next_offset:06x} '
            f'size=0x{block.size:x} ({block.size}){extra}'
        )


def _print_record_samples(data: bytes, block: Mnc2Block, limit: int) -> None:
    layout = BLOCK_LAYOUTS.get(block.tag)
    if not layout or not layout.record_size or not layout.record_count:
        click.echo(f'Record listing is not defined for {block.tag}.')
        return

    start = block.offset + layout.data_skip
    end = min(block.next_offset, start + layout.record_size * layout.record_count)
    shown = 0

    click.echo(f'{block.tag} fixed records:')
    if block.tag == 'comm':
        click.echo(f'  {"idx":>5} {"off":>8} {"id":>5} {"b2":>4} {"b3":>4} {"w4":>5} {"w8":>5} {"b0e":>4} {"b0f":>4} raw[0:16]')
        for idx in range(layout.record_count):
            off = start + idx * layout.record_size
            if off + layout.record_size > end:
                break
            rec = data[off:off + layout.record_size]
            rec_id = struct.unpack_from('<H', rec, 0)[0]
            if rec_id in {0, 0xffff}:
                continue
            w4 = struct.unpack_from('<H', rec, 4)[0]
            w8 = struct.unpack_from('<H', rec, 8)[0]
            click.echo(
                f'  {idx:>5} 0x{off:06x} {rec_id:>5} {rec[2]:>4} {rec[3]:>4} '
                f'{w4:>5} {w8:>5} {rec[0x0e]:>4} {rec[0x0f]:>4} {rec[:16].hex(" ")}'
            )
            shown += 1
            if shown >= limit:
                break
    elif block.tag == 'mgc_':
        click.echo(f'  {"idx":>5} {"off":>8} {"id":>5} {"w2":>5} {"b0c":>4} {"b0d":>4} {"w3e":>5} {"w40":>5} {"w42":>5} raw[0:16]')
        for idx in range(layout.record_count):
            off = start + idx * layout.record_size
            if off + layout.record_size > end:
                break
            rec = data[off:off + layout.record_size]
            rec_id = struct.unpack_from('<H', rec, 0)[0]
            if rec_id in {0, 0xffff}:
                continue
            w2 = struct.unpack_from('<H', rec, 2)[0]
            w3e = struct.unpack_from('<H', rec, 0x3e)[0]
            w40 = struct.unpack_from('<H', rec, 0x40)[0]
            w42 = struct.unpack_from('<H', rec, 0x42)[0]
            click.echo(
                f'  {idx:>5} 0x{off:06x} {rec_id:>5} {w2:>5} {rec[0x0c]:>4} {rec[0x0d]:>4} '
                f'{w3e:>5} {w40:>5} {w42:>5} {rec[:16].hex(" ")}'
            )
            shown += 1
            if shown >= limit:
                break

    if shown == 0:
        click.echo('  (no nonzero records shown)')


@click.command('mnc2-pos')
@click.argument('dat_path', metavar='DAT_FILE')
@click.option('--block', 'block_tag', default='mnc2', show_default=True,
              help='Block to scan: mnc2, mgc_, comm, mon_, levc, or all.')
@click.option('--limit', default=80, type=int, show_default=True,
              help='Maximum candidate rows to print.')
@click.option('--records', is_flag=True,
              help='List decoded fixed records for mgc_ or comm instead of heuristic x/y candidates.')
@click.option('--offset', 'patch_offset', default=None,
              help='Absolute hex/decimal offset to patch as int16 x/y/w/h tuple.')
@click.option('--x', 'new_x', default=None, type=int, help='New int16 x value at --offset.')
@click.option('--y', 'new_y', default=None, type=int, help='New int16 y value at --offset + 2.')
@click.option('--w', 'new_w', default=None, type=int, help='New int16 w value at --offset + 4.')
@click.option('--h', 'new_h', default=None, type=int, help='New int16 h value at --offset + 6.')
@click.option('--dry-run', is_flag=True, help='Show what would change without writing.')
def cmd(
    dat_path: str,
    block_tag: str,
    limit: int,
    records: bool,
    patch_offset: str | None,
    new_x: int | None,
    new_y: int | None,
    new_w: int | None,
    new_h: int | None,
    dry_run: bool,
):
    """Inspect ROM/118/114.DAT mnc2/mgc_/comm numeric tables.

    The default x/y/w/h listing is only a heuristic scan and has many false positives. Use
    --records with --block mgc_ or --block comm to inspect known fixed-size records.
    """
    p = _resolve_dat_path(dat_path)
    data = bytearray(read_path_for(p).read_bytes())
    if len(data) < 0x24 or data[:4] != b'menu' or data[0x20:0x24] != b'mnc2':
        raise click.ClickException('Expected menu DAT with mnc2 block at 0x20')

    blocks = find_blocks(data)

    if patch_offset is not None:
        off = int(patch_offset, 0)
        if off < 0 or off + 8 > len(data):
            raise click.ClickException(f'--offset out of range: {patch_offset}')
        old_x, old_y, old_w, old_h = struct.unpack_from('<hhhh', data, off)
        click.echo(f'File: {p}')
        click.echo(f'0x{off:06x} before: x={old_x} y={old_y} w={old_w} h={old_h}')
        if new_x is not None:
            click.echo(f'  x: {old_x} -> {new_x}')
        if new_y is not None:
            click.echo(f'  y: {old_y} -> {new_y}')
        if new_w is not None:
            click.echo(f'  w: {old_w} -> {new_w}')
        if new_h is not None:
            click.echo(f'  h: {old_h} -> {new_h}')
        if dry_run:
            click.echo('(dry-run - not written)')
            return
        if new_x is not None:
            struct.pack_into('<h', data, off, new_x)
        if new_y is not None:
            struct.pack_into('<h', data, off + 2, new_y)
        if new_w is not None:
            struct.pack_into('<h', data, off + 4, new_w)
        if new_h is not None:
            struct.pack_into('<h', data, off + 6, new_h)
        out = editable_dat(p, fresh=False)
        out.write_bytes(data)
        click.echo(f'Written -> {out}')
        return

    click.echo(f'MNC2 candidate position data in {p}')
    click.echo()
    _print_blocks(blocks)
    click.echo()

    selected = blocks if block_tag == 'all' else [b for b in blocks if b.tag == block_tag]
    if not selected:
        raise click.ClickException(f'Block not found: {block_tag}')

    for block in selected:
        if records:
            _print_record_samples(data, block, limit)
            click.echo()
            continue
        hits = _candidate_pairs(data, block.offset, block.next_offset)
        click.echo(f'Candidate int16 x/y/w/h tuples in {block.tag} ({len(hits)} total):')
        click.echo(f'  {"off":>8} {"x":>7} {"y":>7} {"w":>7} {"h":>7}')
        for off, x, y, w, h in hits[:limit]:
            click.echo(f'  0x{off:06x} {x:>7} {y:>7} {w:>7} {h:>7}')
        if len(hits) > limit:
            click.echo(f'  ... {len(hits) - limit} more; increase --limit to show them')
        click.echo()

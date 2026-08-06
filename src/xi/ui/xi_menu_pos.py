"""xi ui menu-pos — inspect and patch menu/element positions in ROM/0/1.DAT.

Menu section layout (within the file):
  +0   char[4]   section tag  e.g. 'titl', 'play', 'polw'
  +4   uint32    section size/id
  +8   8 bytes   zeros (header padding)
  --- section data starts here ---
  +16  char[16]  menu name    e.g. 'menu    playermo'
  +32  uint8     maybeType
  +33  uint8     numElements
  +34  14 bytes  align padding (to 16-byte boundary within section data)
  +48  element   frame  (bounding box / window position)
  +48+frame.size element[0], element[1], ...

Element layout:
  +0   uint16  total element size
  +2   int16   x  (signed, relative to screen origin)
  +4   int16   y
  +6   uint16  unk0
  +8   uint16  unk1
  +10  int16   width
  +12  int16   height
  ... (navigation bytes, option refs, etc.)
"""

import struct
from dataclasses import dataclass
from pathlib import Path

import click

from xi.xi_config import FFXI_DIR, editable_dat, read_path_for


# ── data types ────────────────────────────────────────────────────────────────

@dataclass
class MenuElement:
    file_offset: int    # absolute offset of this element in the DAT
    size:   int
    x:      int
    y:      int
    width:  int
    height: int
    index:  int         # elementIndex field
    selectable: bool


@dataclass
class MenuSection:
    file_offset: int    # absolute offset of the section tag
    tag:         str
    name:        str
    num_elements: int
    frame:       MenuElement
    elements:    list[MenuElement]


# ── parsing ──────────���─────────────────────────��──────────────────────────────

def _parse_element(data: bytes, off: int) -> MenuElement:
    size  = struct.unpack_from('<H', data, off)[0]
    x     = struct.unpack_from('<h', data, off + 2)[0]
    y     = struct.unpack_from('<h', data, off + 4)[0]
    w     = struct.unpack_from('<h', data, off + 10)[0]
    h     = struct.unpack_from('<h', data, off + 12)[0]
    idx   = data[off + 16]
    prev  = struct.unpack_from('b', data, off + 19)[0]
    nxt   = struct.unpack_from('b', data, off + 20)[0]
    return MenuElement(
        file_offset = off,
        size        = size,
        x           = x,
        y           = y,
        width       = w,
        height      = h,
        index       = idx,
        selectable  = (prev != -1 and nxt != -1),
    )


def find_menu_sections(data: bytes) -> list[MenuSection]:
    """Scan for all 'menu    ' sections and parse their frame + elements."""
    sections = []
    i = 0
    while i < len(data) - 48:
        # Look for valid section header: printable[4] + any[4] + 8zeros + 'menu    '
        if data[i + 8:i + 16] == b'\x00' * 8 and data[i + 16:i + 24] == b'menu    ':
            tag_bytes = data[i:i + 4]
            if all(32 <= b < 127 for b in tag_bytes):
                tag  = tag_bytes.decode('ascii')
                name = data[i + 16:i + 32].decode('ascii', errors='?').rstrip()
                num_elements = data[i + 33]

                try:
                    frame = _parse_element(data, i + 48)
                except struct.error:
                    i += 4
                    continue

                elements = []
                off = frame.file_offset + frame.size
                for _ in range(num_elements):
                    if off + 2 > len(data):
                        break
                    try:
                        e = _parse_element(data, off)
                        elements.append(e)
                        off += e.size
                    except struct.error:
                        break

                sections.append(MenuSection(
                    file_offset  = i,
                    tag          = tag,
                    name         = name,
                    num_elements = num_elements,
                    frame        = frame,
                    elements     = elements,
                ))
                i += 4
                continue
        i += 4
    return sections


def find_section(sections: list[MenuSection], tag: str) -> MenuSection | None:
    for s in sections:
        if s.tag == tag:
            return s
    return None


# ── patching ──────────────────────────────────────────────────────────────────

def patch_element_xy(data: bytearray, elem: MenuElement, x: int | None, y: int | None) -> None:
    """Write new x and/or y into the element in-place."""
    if x is not None:
        struct.pack_into('<h', data, elem.file_offset + 2, x)
    if y is not None:
        struct.pack_into('<h', data, elem.file_offset + 4, y)


# ── display ──────────────────────────────────────���────────────────────────────

def _print_section(section: MenuSection) -> None:
    f = section.frame
    click.echo(
        f"  [{section.tag}] {section.name!r}  "
        f"frame: x={f.x} y={f.y} w={f.width} h={f.height}  "
        f"({section.num_elements} elements)"
    )
    if section.elements:
        click.echo(f"  {'idx':>4}  {'x':>6}  {'y':>6}  {'w':>6}  {'h':>6}  sel  offset")
        for i, e in enumerate(section.elements):
            click.echo(
                f"  {i:>4}  {e.x:>6}  {e.y:>6}  {e.width:>6}  {e.height:>6}  "
                f"{'yes' if e.selectable else 'no '}"
                f"  0x{e.file_offset:06x}"
            )


# ── CLI command ────────────────────────────���──────────────────────────────────

_KNOWN_MENUS = {
    'polwindo': ('polw', 'Logo / header window'),
    'titlewin': ('titl', '"Press Enter" title window'),
    'titlehan': ('tit1', 'Title handler window'),
    'netwait':  ('netw', 'Network wait screen'),
    'playermo': ('play', 'Game / expansion select list'),
    'charlnk':  ('char', 'Character link / select'),
}

_DEFAULT_SHOW = {'polw', 'play'}


@click.command('menu-pos')
@click.argument('dat_file', default=None, required=False,
                metavar='[DAT_FILE]')
@click.option('--menu', 'menu_tag', default=None,
              help='4-char section tag to target (e.g. polw, play).  '
                   'Required when patching.')
@click.option('--elem', 'elem_index', default=None, type=int,
              help='Element index to patch (omit to patch the frame).')
@click.option('--x', 'new_x', default=None, type=int,
              help='New x position (signed int16).')
@click.option('--y', 'new_y', default=None, type=int,
              help='New y position (signed int16).')
@click.option('--all', 'show_all', is_flag=True,
              help='Show all 408 menu sections, not just title-screen ones.')
@click.option('--dry-run', is_flag=True,
              help='Parse and show what would change without writing.')
def cmd(
    dat_file:   str | None,
    menu_tag:   str | None,
    elem_index: int | None,
    new_x:      int | None,
    new_y:      int | None,
    show_all:   bool,
    dry_run:    bool,
):
    """Inspect and patch menu/element positions in a UI DAT (default: ROM/0/1.DAT).

    Without patching options, prints current positions for all title-screen menus.

    \b
    Examples:
      uv run xi ui menu-pos
      uv run xi ui menu-pos --menu play --x 456 --y 128
      uv run xi ui menu-pos --menu polw --x 456 --y 16
      uv run xi ui menu-pos --menu play --elem 0 --x 21 --y 5
      uv run xi ui menu-pos --all
      uv run xi ui menu-pos --menu play --x 456 --dry-run
    """
    p = Path(dat_file) if dat_file else Path(FFXI_DIR) / 'ROM' / '0' / '1.DAT'
    if not p.is_absolute():
        p = Path(FFXI_DIR) / p
    if not p.exists():
        raise click.ClickException(f'DAT not found: {p}')

    data = bytearray(read_path_for(p).read_bytes())
    sections = find_menu_sections(data)

    # ── patching mode ─────────────────────────────────────────────────────────
    if new_x is not None or new_y is not None:
        if not menu_tag:
            raise click.ClickException('--menu TAG is required when patching.')

        section = find_section(sections, menu_tag)
        if section is None:
            raise click.ClickException(
                f'Section tag {menu_tag!r} not found.  '
                f'Known tags: {", ".join(s.tag for s in sections[:20])} …'
            )

        if elem_index is None:
            target = section.frame
            target_label = 'frame'
        else:
            if elem_index < 0 or elem_index >= len(section.elements):
                raise click.ClickException(
                    f'elem {elem_index} out of range '
                    f'(0–{len(section.elements) - 1})'
                )
            target = section.elements[elem_index]
            target_label = f'element[{elem_index}]'

        click.echo(
            f'[{menu_tag}] {section.name}  {target_label}  '
            f'before: x={target.x} y={target.y}'
        )

        if new_x is not None:
            click.echo(f'  x: {target.x} → {new_x}')
        if new_y is not None:
            click.echo(f'  y: {target.y} → {new_y}')

        if not dry_run:
            patch_element_xy(data, target, new_x, new_y)
            out = editable_dat(p, fresh=False)
            out.write_bytes(data)
            click.echo(f'Written → {out}')
        else:
            click.echo('(dry-run — not written)')
        return

    # ── display mode ─────────────────────────────────────────────────────────
    click.echo(f'Menu sections in {p}')
    click.echo()

    known_label = {tag: label for name, (tag, label) in _KNOWN_MENUS.items()}

    if show_all:
        to_show = sections
    else:
        to_show = [s for s in sections if s.tag in _DEFAULT_SHOW]

    if not to_show:
        click.echo('No matching sections found.')
        return

    for s in to_show:
        label = known_label.get(s.tag, '')
        if label:
            click.echo(f'-- {label} --')
        _print_section(s)
        click.echo()

    if not show_all:
        click.echo(
            f'(showing {len(to_show)} title-screen menus; '
            f'use --all to see all {len(sections)})'
        )
        click.echo()
        click.echo('Known menu tags:')
        for mname, (tag, label) in _KNOWN_MENUS.items():
            click.echo(f'  {tag}  {mname:<12}  {label}')

"""Read/write FFXI ``d_msg`` string-table DATs (both on-disk layouts).

A ``d_msg`` table is: a 0x40-byte header, then the string *blocks*. The header
(u16 at 0x0A) says whether everything after the header is XOR-encoded with
0xFF (``flip``), and the 8 x u32 field block at 0x10 says which of two layouts
the file uses:

* **fixed stride** — ``bytes_per_entry`` > 0, ``metadata_size`` == 0. ``num``
  blocks of ``stride`` bytes each follow the header, zero-padded. Used by
  titles, key items, spell names, mount names, status names.
* **variable** — ``bytes_per_entry`` == 0, ``metadata_size`` == ``num`` * 8.
  A metadata table of (offset u32, size u32) pairs follows the header; each
  block sits at ``0x40 + metadata_size + offset`` and is ``size`` bytes with no
  padding. Used by job names, the merit and job-point menus, quest and mission
  text, area names and most help-text tables.

The two layouts share the block format. Each block is: ``n`` (u32) sub-string
count, an ``n`` x (offset u32, flag u32) entry table, then the sub-strings laid
out contiguously in offset order. A sub-string is ``marker`` (u32) followed by,
when ``marker == 1``, 0x18 metadata bytes then a NUL-terminated cp932 string
padded to a 4-byte boundary. A sub-string whose marker != 1 carries no text
(e.g. the key-item table stores the key-item id in sub[0]'s marker).

Writing is **surgical**: unedited blocks are kept verbatim (so a round-trip is
byte-exact), and only the block being edited or appended is rebuilt. See
``verify_roundtrip`` — callers should assert it before trusting a write.

Layout references (English client):
  mount name  ROM/351/84.DAT  fixed 80,   plain, sub[0]=name
  key items   ROM/175/35.DAT  fixed 700,  flip,  sub[0].marker=keyitem id,
                                                  sub[4]=name 5=plural 6=desc
  titles      ROM/180/78.DAT  fixed 256,  flip,  sub[0]=title
  job names   ROM/165/86.DAT  variable,   flip,  sub[0]=name
  merit menu  ROM/169/75.DAT  variable,   flip,  sub[0]=name/description
"""

import struct

HEADER_SIZE = 0x40
META_LEN = 0x18  # bytes between a text sub-string's marker and its string
NAME_SUB, PLURAL_SUB, DESC_SUB = 4, 5, 6  # key-item block sub-string indices


def _encode_body(text: str) -> bytes:
    """cp932 bytes + NUL, zero-padded so the string body is a multiple of 4
    (matches the on-disk layout — the next sub-string starts 4-byte aligned)."""
    try:
        enc = text.encode('cp932')
    except UnicodeEncodeError as e:
        raise DmsgError(f'{text!r} is not encodable in cp932 (Shift-JIS): {e}')
    body = enc + b'\x00'
    if len(body) % 4:
        body += b'\x00' * (4 - len(body) % 4)
    return body


class DmsgError(Exception):
    pass


class DmsgTable:
    """Parsed d_msg table. ``blocks`` are de-XOR'd. For a fixed-stride table
    every block is ``stride`` bytes; for a variable table ``stride`` is 0 and
    each block is exactly its on-disk size."""

    def __init__(self, prefix: bytearray, blocks: list, bitmask: int,
                 stride: int, table_offset: int, variable: bool = False):
        self.prefix = prefix            # bytes [0:table_offset] incl. the header
        self.blocks = blocks            # list[bytearray]
        self.bitmask = bitmask          # 0xFF when the header flip flag is set
        self.stride = stride            # bytes per block (0 = variable)
        self.table_offset = table_offset
        self.variable = variable

    @property
    def num(self) -> int:
        return len(self.blocks)


def header_bitmask(data: bytes) -> int:
    """XOR mask implied by the header flip flag (u16 at 0x0A): 0xFF or 0."""
    if len(data) < HEADER_SIZE or data[:5] != b'd_msg':
        raise DmsgError('not a d_msg file')
    flip = struct.unpack_from('<H', data, 0x0A)[0]
    if flip not in (0, 1):
        raise DmsgError(f'unexpected d_msg flip flag {flip}')
    return 0xFF if flip else 0


def parse(data: bytes, bitmask: int = None) -> DmsgTable:
    """Parse either layout. ``bitmask`` is accepted for backwards compatibility
    but the header flip flag is authoritative; a mismatch is ignored."""
    mask = header_bitmask(data)
    (_unk0, file_size, header_size, meta_size, stride,
     data_size, num, _unk1) = struct.unpack_from('<8I', data, 0x10)
    if header_size != HEADER_SIZE:
        raise DmsgError(f'unexpected d_msg header size {header_size:#x}')
    if file_size > len(data):
        raise DmsgError('truncated d_msg: header file_size exceeds data')
    buf = bytearray(data)
    if mask:
        for i in range(HEADER_SIZE, file_size):
            buf[i] ^= mask
    prefix = buf[:HEADER_SIZE]

    if stride > 0 and meta_size == 0:
        blocks = [buf[HEADER_SIZE + i * stride: HEADER_SIZE + (i + 1) * stride]
                  for i in range(num)]
        if any(len(b) != stride for b in blocks):
            raise DmsgError('truncated d_msg: block region shorter than num*stride')
        return DmsgTable(prefix, blocks, mask, stride, HEADER_SIZE, variable=False)

    if stride == 0 and meta_size > 0:
        if meta_size != num * 8:
            raise DmsgError(f'd_msg metadata size {meta_size} != {num} entries * 8')
        base = HEADER_SIZE + meta_size
        blocks = []
        for i in range(num):
            off, size = struct.unpack_from('<II', buf, HEADER_SIZE + i * 8)
            start = base + off
            if start + size > file_size:
                raise DmsgError(f'd_msg block {i} runs past end of file')
            blocks.append(buf[start:start + size])
        return DmsgTable(prefix, blocks, mask, 0, HEADER_SIZE, variable=True)

    raise DmsgError(f'unsupported d_msg layout (stride={stride}, metadata={meta_size})')


def serialize(t: DmsgTable) -> bytes:
    out = bytearray(t.prefix[:HEADER_SIZE])
    if t.variable:
        meta = bytearray()
        body = bytearray()
        off = 0
        for b in t.blocks:
            meta += struct.pack('<II', off, len(b))
            body += b
            off += len(b)
        out += meta + body
        struct.pack_into('<I', out, 0x1C, len(meta))      # metadata_size
        struct.pack_into('<I', out, 0x20, 0)              # bytes_per_entry
    else:
        body = bytearray()
        for b in t.blocks:
            if len(b) != t.stride:
                raise DmsgError(f'block is {len(b)} bytes, expected stride {t.stride}')
            body += b
        out += body
        struct.pack_into('<I', out, 0x1C, 0)              # metadata_size
        struct.pack_into('<I', out, 0x20, t.stride)       # bytes_per_entry
    file_size = len(out)
    struct.pack_into('<I', out, 0x14, file_size)          # file_size
    struct.pack_into('<I', out, 0x18, HEADER_SIZE)        # header_size
    struct.pack_into('<I', out, 0x24, len(body))          # data_size
    struct.pack_into('<I', out, 0x28, len(t.blocks))      # num_entries
    if t.bitmask:
        for i in range(HEADER_SIZE, file_size):
            out[i] ^= t.bitmask
    return bytes(out)


# ── block-level helpers ──────────────────────────────────────────────────────

def _parse_block(block: bytearray) -> list:
    """Return [{flag, marker, raw}] for each sub-string. Lengths are derived from
    the format (not the trailing padding): a non-text sub-string is just its
    4-byte marker; a text sub-string is marker + 0x18 meta + the NUL-terminated
    cp932 string padded to a 4-byte boundary."""
    if len(block) < 4:
        raise DmsgError('block too short to hold a sub-string count')
    n = struct.unpack_from('<I', block, 0)[0]
    if 4 + n * 8 > len(block):
        raise DmsgError(f'block declares {n} sub-strings but is only {len(block)} bytes')
    entries = [struct.unpack_from('<II', block, 4 + i * 8) for i in range(n)]
    subs = []
    for off, flag in entries:
        if off + 4 > len(block):
            raise DmsgError(f'sub-string offset {off} outside block of {len(block)} bytes')
        marker = struct.unpack_from('<I', block, off)[0]
        if marker == 1:                               # text
            sp = off + 4 + META_LEN
            try:
                strlen = block.index(0, sp) - sp      # bytes before the NUL
            except ValueError:
                strlen = len(block) - sp              # unterminated: take the rest
            length = 4 + META_LEN + strlen + 1        # marker + meta + str + NUL
            if length % 4:
                length += 4 - length % 4              # 4-byte align
            length = min(length, len(block) - off)
        else:                                         # non-text: marker only
            length = 4
        subs.append({'flag': flag, 'marker': marker, 'raw': bytes(block[off:off + length])})
    return subs


def _assemble_block(subs: list, stride: int) -> bytes:
    """Rebuild a block from its sub-strings. ``stride`` > 0 pads (and bounds)
    the block for a fixed-stride table; 0 means variable size, no padding."""
    n = len(subs)
    out = bytearray(struct.pack('<I', n))
    body_pos = 4 + 8 * n
    cur = body_pos
    offsets = []
    for s in subs:
        offsets.append(cur)
        cur += len(s['raw'])
    for i, s in enumerate(subs):
        out += struct.pack('<II', offsets[i], s['flag'])
    for s in subs:
        out += s['raw']
    if stride:
        if len(out) > stride:
            raise DmsgError(f'block overflow: {len(out)} > stride {stride}')
        out += b'\x00' * (stride - len(out))
    return bytes(out)


def get_text(block: bytearray, idx: int) -> str:
    """Decoded string of sub-string ``idx`` ('' if absent / non-text)."""
    subs = _parse_block(block)
    if idx >= len(subs) or subs[idx]['marker'] != 1:
        return ''
    body = subs[idx]['raw'][4 + META_LEN:]
    e = body.find(0)
    if e >= 0:
        body = body[:e]
    return body.decode('cp932', 'replace')


def get_marker(block: bytearray, idx: int) -> int:
    subs = _parse_block(block)
    return subs[idx]['marker'] if idx < len(subs) else 0


def set_text(block: bytearray, idx: int, text: str, stride: int = None) -> bytes:
    """Rebuild ``block`` with sub-string ``idx`` set to ``text`` (must be a text
    sub-string, marker == 1). Other sub-strings are preserved verbatim.

    ``stride``: the table's block stride; ``None`` keeps the block's current
    length (correct for fixed-stride tables), ``0`` marks a variable table."""
    subs = _parse_block(block)
    if idx >= len(subs):
        raise DmsgError(f'sub-string {idx} out of range (block has {len(subs)})')
    s = subs[idx]
    if s['marker'] != 1:
        raise DmsgError(f'sub-string {idx} is not a text slot (marker={s["marker"]})')
    meta = s['raw'][4:4 + META_LEN]
    s['raw'] = struct.pack('<I', 1) + meta + _encode_body(text)
    return _assemble_block(subs, len(block) if stride is None else stride)


def make_block_from_template(template: bytearray, marker0: int,
                             texts: dict, stride: int = None) -> bytes:
    """Build a new block by cloning ``template``'s structure: set sub[0]'s marker
    to ``marker0`` (the key/id) and the text sub-strings named in ``texts``
    ({sub_index: string}). Used to append a brand-new key-item block."""
    subs = _parse_block(template)
    # sub[0] carries the id in its marker (raw bytes after the marker are kept).
    subs[0]['raw'] = struct.pack('<I', marker0) + subs[0]['raw'][4:]
    subs[0]['marker'] = marker0
    for idx, txt in texts.items():
        if idx >= len(subs) or subs[idx]['marker'] != 1:
            raise DmsgError(f'template sub-string {idx} is not a text slot')
        meta = subs[idx]['raw'][4:4 + META_LEN]
        subs[idx]['raw'] = struct.pack('<I', 1) + meta + _encode_body(txt)
    return _assemble_block(subs, len(template) if stride is None else stride)


def make_empty_block(stride: int) -> bytes:
    """A minimal valid filler block (n=0) — used to pad a sequential table up to
    a gap id. The menu shows owned mounts only, so fillers never display."""
    out = bytearray(struct.pack('<I', 0))
    if stride:
        out += b'\x00' * (stride - len(out))
    return bytes(out)


def ensure_len(t: DmsgTable, count: int) -> None:
    """Grow the table to at least ``count`` blocks, padding with empty blocks."""
    while len(t.blocks) < count:
        t.blocks.append(bytearray(make_empty_block(t.stride)))


def verify_roundtrip(data: bytes, bitmask: int = None) -> bool:
    """True iff parse->serialize reproduces ``data`` byte-for-byte."""
    try:
        return serialize(parse(data, bitmask)) == data
    except DmsgError:
        return False

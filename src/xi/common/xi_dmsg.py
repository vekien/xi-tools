"""Read/write FFXI ``d_msg`` string-table DATs (the fixed-stride variant).

A ``d_msg`` table is: a plaintext ``d_msg`` header, an 8 x u32 field block at
0x10, then ``num`` fixed-size *blocks* of ``stride`` bytes each. The block region
(``table_offset`` .. ``file_size``) is optionally XOR-encrypted with a one-byte
mask (0xFF for the key-item table, 0 for the mount-name table).

Each block is: ``n`` (u32) sub-string count, an ``n`` x (offset u32, flag u32)
entry table, then the sub-strings laid out contiguously in offset order, then
zero padding to ``stride``. A sub-string is ``marker`` (u32) followed by, when
``marker == 1``, 0x18 metadata bytes then a NUL-terminated cp932 string. A
sub-string whose marker != 1 carries no text (e.g. the key-item table stores the
key-item id in sub[0]'s marker).

Writing is **surgical**: unedited blocks are kept verbatim (so a round-trip is
byte-exact), and only the block being edited or appended is rebuilt. See
``verify_roundtrip`` — callers should assert it before trusting a write.

Layout references (English client):
  mount name  ROM/351/84.DAT  stride 80,  bitmask 0,    sub[0]=name
  key items   ROM/175/35.DAT  stride 700, bitmask 0xFF, sub[0].marker=keyitem id,
                                                         sub[4]=name 5=plural 6=desc
"""

import struct

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
    """Parsed fixed-stride d_msg table. ``blocks`` are de-XOR'd, stride-sized."""

    def __init__(self, prefix: bytearray, blocks: list, bitmask: int,
                 stride: int, table_offset: int):
        self.prefix = prefix            # bytes [0:table_offset] incl. the header
        self.blocks = blocks            # list[bytearray], each `stride` bytes
        self.bitmask = bitmask
        self.stride = stride
        self.table_offset = table_offset

    @property
    def num(self) -> int:
        return len(self.blocks)


def parse(data: bytes, bitmask: int) -> DmsgTable:
    if data[:5] != b'd_msg':
        raise DmsgError('not a d_msg file')
    (_unk0, file_size, table_offset, table_size, stride,
     _sss, num, _unk1) = struct.unpack_from('<8I', data, 0x10)
    if table_size != 0:
        raise DmsgError('only the fixed-stride d_msg variant is supported')
    buf = bytearray(data)
    if bitmask:
        for i in range(table_offset, file_size):
            buf[i] ^= bitmask
    prefix = buf[:table_offset]
    blocks = [buf[table_offset + i * stride: table_offset + (i + 1) * stride]
              for i in range(num)]
    if any(len(b) != stride for b in blocks):
        raise DmsgError('truncated d_msg: block region shorter than num*stride')
    return DmsgTable(prefix, blocks, bitmask, stride, table_offset)


def serialize(t: DmsgTable) -> bytes:
    body = bytearray()
    for b in t.blocks:
        if len(b) != t.stride:
            raise DmsgError(f'block is {len(b)} bytes, expected stride {t.stride}')
        body += b
    out = bytearray(t.prefix) + body
    file_size = t.table_offset + len(body)
    struct.pack_into('<I', out, 0x14, file_size)        # file_size
    struct.pack_into('<I', out, 0x24, len(body))        # string_section_size
    struct.pack_into('<I', out, 0x28, len(t.blocks))    # num_strings
    if t.bitmask:
        for i in range(t.table_offset, file_size):
            out[i] ^= t.bitmask
    return bytes(out)


# ── block-level helpers ──────────────────────────────────────────────────────

def _parse_block(block: bytearray) -> list:
    """Return [{flag, marker, raw}] for each sub-string. Lengths are derived from
    the format (not the trailing padding): a non-text sub-string is just its
    4-byte marker; a text sub-string is marker + 0x18 meta + the NUL-terminated
    cp932 string padded to a 4-byte boundary."""
    n = struct.unpack_from('<I', block, 0)[0]
    entries = [struct.unpack_from('<II', block, 4 + i * 8) for i in range(n)]
    subs = []
    for off, flag in entries:
        marker = struct.unpack_from('<I', block, off)[0]
        if marker == 1:                               # text
            sp = off + 4 + META_LEN
            strlen = block.index(0, sp) - sp          # bytes before the NUL
            length = 4 + META_LEN + strlen + 1        # marker + meta + str + NUL
            if length % 4:
                length += 4 - length % 4              # 4-byte align
        else:                                         # non-text: marker only
            length = 4
        subs.append({'flag': flag, 'marker': marker, 'raw': bytes(block[off:off + length])})
    return subs


def _assemble_block(subs: list, stride: int) -> bytes:
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


def set_text(block: bytearray, idx: int, text: str) -> bytes:
    """Rebuild ``block`` with sub-string ``idx`` set to ``text`` (must be a text
    sub-string, marker == 1). Other sub-strings are preserved verbatim."""
    subs = _parse_block(block)
    if idx >= len(subs):
        raise DmsgError(f'sub-string {idx} out of range (block has {len(subs)})')
    s = subs[idx]
    if s['marker'] != 1:
        raise DmsgError(f'sub-string {idx} is not a text slot (marker={s["marker"]})')
    meta = s['raw'][4:4 + META_LEN]
    s['raw'] = struct.pack('<I', 1) + meta + _encode_body(text)
    return _assemble_block(subs, len(block))


def make_block_from_template(template: bytearray, marker0: int,
                             texts: dict) -> bytes:
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
    return _assemble_block(subs, len(template))


def make_empty_block(stride: int) -> bytes:
    """A minimal valid filler block (n=0) — used to pad a sequential table up to
    a gap id. The menu shows owned mounts only, so fillers never display."""
    out = bytearray(struct.pack('<I', 0))
    out += b'\x00' * (stride - len(out))
    return bytes(out)


def ensure_len(t: DmsgTable, count: int) -> None:
    """Grow the table to at least ``count`` blocks, padding with empty blocks."""
    while len(t.blocks) < count:
        t.blocks.append(bytearray(make_empty_block(t.stride)))


def verify_roundtrip(data: bytes, bitmask: int) -> bool:
    """True iff parse->serialize reproduces ``data`` byte-for-byte."""
    try:
        return serialize(parse(data, bitmask)) == data
    except DmsgError:
        return False

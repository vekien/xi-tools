"""The client's spell and command menu tables (``ROM/118/114.DAT``) and the
string tables that name them — read, grow, and write new records.

``114.DAT`` is a ``menu`` container: a 0x20-byte file header, then sections. Each
section is a 16-byte header — 4-char tag + u32 ``(length_in_16_byte_units << 7) |
resource_type`` (the length includes the header) — followed by its payload, and the
file ends with an ``end\\0`` section of length 1. FFXiMain asks its resource list for
type ``0x49`` (``mgc_``, spells: 0x64-byte records) and ``0x53`` (``comm``, commands:
0x30-byte records) and reads fixed-size records straight from the payload, so a
section may be any length; the client's loops decide how many records it *looks at*
(0x400 spells, 0xB00 commands on the retail binary — the ceilings a client patch
raises). Records are rotated per record (``xi.common.xi_menu_records``); the record
id at ``+0`` equals the index, and an all-zero record is "empty".

Decoded ``mgc_`` (spell) record, verified against a retail client and the server's
``spell_list``:

    +0x00 u16 id            +0x02 u16 kind (1 white, 2 black, 3 summon, 4 ninjutsu,
    5 song, 6 blue, 7 geomancy, 8 trust …)   +0x04 u16 element   +0x06 u16 target bits
    +0x08 u16 skill (33 healing, 36 elemental, 39 ninjutsu, 43 blue …)   +0x0A u16 MP
    +0x0C u8 cast time (¼ s)   +0x0D u8 recast (¼ s)
    +0x0E 24 × u16 level per job (JOBS order; 0xFFFF = cannot learn)
    +0x3E u16 menu index (the slot the known-spell list is sorted into)
    +0x40 u16 icon   +0x42 u16 icon 2   +0x44… flags (kept from the donor)

Decoded ``comm`` (command) record: see ``xi.common.xi_menu_records`` (id, type, icon,
charges, target bits, TP cost, level, range, radius, AoE …).

Names and help text are plain fixed-stride ``d_msg`` tables, block *i* ↔ record *i*:
spells ``ROM/181/73`` (EN) / ``69`` (JP), help ``75`` / ``71``; commands ``72`` / ``68``,
help ``74`` / ``70``. The spell tables have exactly 1024 blocks and grow here; the
command tables already have 5888.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from xi.common import xi_dmsg as D
from xi.common.xi_menu_records import decode_record, encode_record

MENU_DAT = 'ROM/118/114.DAT'
FILE_HEADER = 0x20
SECTION_HEADER = 0x10
END_TAG = b'end\0'

JOBS = ['NONE', 'WAR', 'MNK', 'WHM', 'BLM', 'RDM', 'THF', 'PLD', 'DRK', 'BST', 'BRD', 'RNG',
        'SAM', 'NIN', 'DRG', 'SMN', 'BLU', 'COR', 'PUP', 'DNC', 'SCH', 'GEO', 'RUN', 'MON']
NO_LEVEL = 0xFFFF

SPELL_KINDS = {1: 'white', 2: 'black', 3: 'summon', 4: 'ninjutsu', 5: 'song', 6: 'blue',
               7: 'geomancy', 8: 'trust'}
ELEMENTS = {0: 'fire', 1: 'ice', 2: 'wind', 3: 'earth', 4: 'lightning', 5: 'water',
            6: 'light', 7: 'dark', 15: 'none'}


@dataclass(frozen=True)
class Kind:
    name: str            # definition / action type name
    tag: str             # section tag in 114.DAT
    res_type: int        # resource type in the section header
    stride: int
    retail_count: int    # records the retail client iterates
    ceiling: int         # records a patched client can iterate (xi.menu docs)
    custom_first: int    # first id no retail record can ever take
    names: Dict[str, str]
    help: Dict[str, str]
    fields: Dict[str, tuple]   # name -> (offset, size)


SPELL_FIELDS = {
    'id': (0x00, 2), 'kind': (0x02, 2), 'element': (0x04, 2), 'targets': (0x06, 2),
    'skill': (0x08, 2), 'mp': (0x0A, 2), 'cast': (0x0C, 1), 'recast': (0x0D, 1),
    'menu_index': (0x3E, 2), 'icon': (0x40, 2), 'icon2': (0x42, 2), 'requirements': (0x44, 1),
}
SPELL_LEVELS = 0x0E
COMMAND_FIELDS = {
    'id': (0x00, 2), 'type': (0x02, 1), 'icon': (0x03, 1), 'icon2': (0x04, 2),
    'charges': (0x06, 2), 'targets': (0x0A, 2), 'tp': (0x0C, 2), 'level': (0x0F, 1),
    'range': (0x10, 1), 'radius': (0x11, 1), 'aoe': (0x12, 1), 'valid_targets': (0x13, 2),
    'tp_modifier': (0x15, 1),
}

KINDS: Dict[str, Kind] = {
    'spell': Kind('spell', 'mgc_', 0x49, 0x64, 0x400, 0x1000, 0x400,
                  {'en': 'ROM/181/73.DAT', 'jp': 'ROM/181/69.DAT'},
                  {'en': 'ROM/181/75.DAT', 'jp': 'ROM/181/71.DAT'}, SPELL_FIELDS),
    'command': Kind('command', 'comm', 0x53, 0x30, 0xB00, 0x1000, 0xB00,
                    {'en': 'ROM/181/72.DAT', 'jp': 'ROM/181/68.DAT'},
                    {'en': 'ROM/181/74.DAT', 'jp': 'ROM/181/70.DAT'}, COMMAND_FIELDS),
}
LANGS = ('en', 'jp')


class MenuError(Exception):
    pass


# ── the container ────────────────────────────────────────────────────────────

@dataclass
class Section:
    tag: bytes
    res_type: int
    body: bytearray

    def serialize(self) -> bytes:
        # The header counts 16-byte units, so the body is zero-padded up to one
        # (a partial trailing record is never a record: the client iterates whole ones).
        body = bytes(self.body)
        if (SECTION_HEADER + len(body)) % 16:
            body += b'\0' * (16 - (SECTION_HEADER + len(body)) % 16)
        total = SECTION_HEADER + len(body)
        return (self.tag + struct.pack('<I', ((total // 16) << 7) | (self.res_type & 0x7F))
                + b'\0' * (SECTION_HEADER - 8) + body)


@dataclass
class MenuDat:
    head: bytes
    sections: List[Section] = field(default_factory=list)

    def section(self, tag: str) -> Section:
        t = tag.encode('ascii')
        for s in self.sections:
            if s.tag == t:
                return s
        raise MenuError(f'{tag} section not found')

    def records(self, kind: str) -> List[bytes]:
        """Decoded records of a kind (a trailing partial record is dropped)."""
        k = KINDS[kind]
        body = self.section(k.tag).body
        n = len(body) // k.stride
        return [decode_record(bytes(body[i * k.stride:(i + 1) * k.stride])) for i in range(n)]

    def count(self, kind: str) -> int:
        k = KINDS[kind]
        return len(self.section(k.tag).body) // k.stride

    def ensure_count(self, kind: str, n: int) -> None:
        """Grow the section to at least ``n`` records (all-zero = empty)."""
        k = KINDS[kind]
        s = self.section(k.tag)
        need = n * k.stride - len(s.body)
        if need > 0:
            s.body += b'\0' * need

    def set_record(self, kind: str, idx: int, decoded: bytes) -> None:
        k = KINDS[kind]
        if len(decoded) != k.stride:
            raise MenuError(f'{kind} record must be {k.stride} bytes, got {len(decoded)}')
        if idx < 0 or idx >= k.ceiling:
            raise MenuError(f'{kind} id {idx} is outside 0..{k.ceiling - 1}')
        self.ensure_count(kind, idx + 1)
        s = self.section(k.tag)
        s.body[idx * k.stride:(idx + 1) * k.stride] = encode_record(decoded)

    def serialize(self) -> bytes:
        out = bytearray(self.head)
        for s in self.sections:
            out += s.serialize()
        out += END_TAG + struct.pack('<I', 1 << 7) + b'\0' * (SECTION_HEADER - 8)
        return bytes(out)


def parse(data: bytes) -> MenuDat:
    if len(data) < FILE_HEADER or data[:4] != b'menu':
        raise MenuError('not a menu container (no "menu" magic)')
    m = MenuDat(bytes(data[:FILE_HEADER]))
    off = FILE_HEADER
    while off + SECTION_HEADER <= len(data):
        tag = bytes(data[off:off + 4])
        v = struct.unpack_from('<I', data, off + 4)[0]
        length = (v >> 7) * 16
        if tag == END_TAG:
            return m
        if length < SECTION_HEADER or off + length > len(data):
            raise MenuError(f'section {tag!r} at 0x{off:x} has bad length 0x{length:x}')
        m.sections.append(Section(tag, v & 0x7F, bytearray(data[off + SECTION_HEADER:off + length])))
        off += length
    raise MenuError('menu container has no end section')


def verify_roundtrip(data: bytes) -> bool:
    try:
        return parse(data).serialize() == data
    except MenuError:
        return False


# ── record fields ────────────────────────────────────────────────────────────

def _get(rec: bytes, off: int, size: int) -> int:
    return rec[off] if size == 1 else struct.unpack_from('<H', rec, off)[0]


def _put(rec: bytearray, off: int, size: int, value: int) -> None:
    if size == 1:
        if not 0 <= value <= 0xFF:
            raise MenuError(f'value {value} does not fit a byte')
        rec[off] = value
    else:
        if not 0 <= value <= 0xFFFF:
            raise MenuError(f'value {value} does not fit a word')
        struct.pack_into('<H', rec, off, value)


def read_fields(kind: str, rec: bytes) -> dict:
    """The named fields of a decoded record; spells also carry ``levels``
    ({JOB: level} for the jobs that can learn it)."""
    k = KINDS[kind]
    out = {name: _get(rec, off, size) for name, (off, size) in k.fields.items()}
    if kind == 'spell':
        lv = struct.unpack_from('<24H', rec, SPELL_LEVELS)
        out['levels'] = {JOBS[i]: v for i, v in enumerate(lv) if v != NO_LEVEL}
    return out


def write_fields(kind: str, rec: bytes, fields: dict) -> bytes:
    """A copy of ``rec`` with ``fields`` applied. Spell ``levels`` replaces the
    whole per-job table ({JOB: level}; jobs not named cannot learn the spell).
    ``element`` may be a name from ELEMENTS; ``kind`` a name from SPELL_KINDS."""
    k = KINDS[kind]
    out = bytearray(rec)
    for name, value in fields.items():
        if name == 'levels' and kind == 'spell':
            table = [NO_LEVEL] * 24
            for job, lvl in value.items():
                job = str(job).upper()
                if job not in JOBS:
                    raise MenuError(f'unknown job {job!r} (expected one of {", ".join(JOBS[1:23])})')
                if not (isinstance(lvl, int) and 0 <= lvl <= 0xFFFE):
                    raise MenuError(f'level for {job} must be an integer 0..65534')
                table[JOBS.index(job)] = lvl
            struct.pack_into('<24H', out, SPELL_LEVELS, *table)
            continue
        if name not in k.fields:
            raise MenuError(f'{kind} record has no field {name!r}')
        if isinstance(value, str):
            value = _named_value(name, value)
        off, size = k.fields[name]
        _put(out, off, size, int(value))
    return bytes(out)


def _named_value(name: str, value: str) -> int:
    table = {'element': ELEMENTS, 'kind': SPELL_KINDS}.get(name)
    if table:
        for code, label in table.items():
            if label == value.lower():
                return code
    raise MenuError(f'{name} {value!r} is not a known name')


def is_empty(rec: bytes) -> bool:
    return not any(rec)


def next_menu_index(records: List[bytes]) -> int:
    """One past the highest menu index in use by a non-empty spell record."""
    off, _ = SPELL_FIELDS['menu_index']
    used = [struct.unpack_from('<H', r, off)[0] for r in records if not is_empty(r)]
    return (max(used) + 1) if used else 0


def free_ids(kind: str, menu: MenuDat, first: Optional[int] = None,
             last: Optional[int] = None) -> List[int]:
    """Ids in ``first..last`` (default: the custom band up to the ceiling) whose
    record is empty or beyond the section."""
    k = KINDS[kind]
    first = k.custom_first if first is None else first
    last = k.ceiling - 1 if last is None else last
    recs = menu.records(kind)
    return [i for i in range(first, last + 1) if i >= len(recs) or is_empty(recs[i])]


def pick_id(kind: str, menu: MenuDat, taken=()) -> int:
    """The highest free custom id (top-down keeps clear of anything below)."""
    taken = set(taken)
    for i in reversed(free_ids(kind, menu)):
        if i not in taken:
            return i
    raise MenuError(f'no free {kind} ids in the custom band')


# ── definitions (schema/spell_definition.json, schema/command_definition.json) ──

DEFINITION_SCHEMAS = {'xi.spell.v1': 'spell', 'xi.command.v1': 'command'}
_DEF_KEYS = {'schema', 'name', 'description', 'like', 'id', 'menu_index', 'text', 'fields'}
_TEXT_KEYS = {'name_en', 'name_jp', 'help_en', 'help_jp'}
_NAME_RX = re.compile(r'[A-Za-z0-9_\-]+')


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_definition(d) -> List[str]:
    """Problems with a spell / command definition (empty when it conforms).
    Hand-rolled — jsonschema is not a dependency — and kept in step with the
    schema files, which are the specification."""
    errs: List[str] = []
    if not isinstance(d, dict):
        return ['definition must be a JSON object']
    for k in d:
        if k not in _DEF_KEYS:
            errs.append(f'unknown key {k!r}')
    kind = DEFINITION_SCHEMAS.get(d.get('schema'))
    if kind is None:
        errs.append('schema must be xi.spell.v1 or xi.command.v1')
    name = d.get('name')
    if not isinstance(name, str) or not _NAME_RX.fullmatch(name):
        errs.append('name must be letters, digits, _ or -')
    if not _is_int(d.get('like')) or d['like'] < 0:
        errs.append('like must be the id of the retail record to clone (a non-negative integer)')
    for key in ('id', 'menu_index'):
        v = d.get(key, 'auto')
        if not (v == 'auto' or (_is_int(v) and v >= 0)):
            errs.append(f'{key} must be "auto" or a non-negative integer')
    text = d.get('text')
    if not isinstance(text, dict) or not isinstance(text.get('name_en'), str) or not text['name_en']:
        errs.append('text.name_en is required')
    else:
        for k, v in text.items():
            if k not in _TEXT_KEYS:
                errs.append(f'unknown text key {k!r}')
            elif not isinstance(v, str):
                errs.append(f'text.{k} must be a string')
    fields = d.get('fields', {})
    if not isinstance(fields, dict):
        errs.append('fields must be an object')
    elif kind:
        allowed = set(KINDS[kind].fields) - {'id', 'menu_index'} | ({'levels'} if kind == 'spell' else set())
        for k, v in fields.items():
            if k not in allowed:
                errs.append(f'fields.{k} is not a {kind} field')
            elif k == 'levels':
                if not isinstance(v, dict) or not all(str(j).upper() in JOBS and _is_int(l) for j, l in v.items()):
                    errs.append('fields.levels must be {JOB: level}')
            elif not (_is_int(v) and v >= 0) and not (isinstance(v, str) and k in ('element', 'kind')):
                errs.append(f'fields.{k} must be a non-negative integer')
    return errs


def load_definition(path) -> dict:
    import json
    path = Path(path)
    try:
        d = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as e:
        raise MenuError(f'{path}: not valid JSON ({e})')
    errs = validate_definition(d)
    if errs:
        raise MenuError(f'{path}: not a valid definition (schema/spell_definition.json, '
                        f'schema/command_definition.json): ' + '; '.join(errs))
    return d


def definition_kind(d: dict) -> str:
    return DEFINITION_SCHEMAS[d['schema']]


def build_record(kind: str, menu: MenuDat, d: dict, new_id: int, menu_index: Optional[int]) -> bytes:
    """The decoded record for a definition: the donor's bytes with the id, menu
    index and the definition's field overrides applied."""
    recs = menu.records(kind)
    donor = d['like']
    if donor >= len(recs) or is_empty(recs[donor]):
        raise MenuError(f'{kind} {donor} (like) is not a retail record on this install')
    fields = dict(d.get('fields') or {})
    fields['id'] = new_id
    if kind == 'spell':
        fields['menu_index'] = next_menu_index(recs) if menu_index is None else menu_index
    return write_fields(kind, recs[donor], fields)


# ── files on an install ───────────────────────────────────────────────────────
#
# The client reads these tables through the XIPivot overlay when one is configured
# (FFXI_PIVOT_DIR): an overlay copy shadows the base install's file, and a server that
# ships edited 114.DAT and spell-name tables ships them there. So every read and
# write goes to the copy the client will actually load — the overlay's when it has
# one, else the install's — each with its own ``.base`` backup on first edit.

def dat_path(root, rom_path: str, for_write: bool = False) -> Path:
    """The file the client loads for ``rom_path``: the pivot overlay's copy when
    it exists, else ``root``'s. For a write with an overlay configured, a table
    the overlay lacks is first copied into it from ``root`` and edited there, so
    the base install stays pristine (its DATs are often read-only anyway)."""
    import shutil
    from xi.xi_config import FFXI_PIVOT_DIR
    rel = Path(*rom_path.split('/'))
    base = Path(root) / rel
    if FFXI_PIVOT_DIR:
        cand = Path(FFXI_PIVOT_DIR) / rel
        if cand.exists():
            return cand
        if for_write and base.exists():
            cand.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(base, cand)
            return cand
    return base


def menu_path(root, for_write: bool = False) -> Path:
    return dat_path(root, MENU_DAT, for_write)


def load_menu(root) -> MenuDat:
    """Parse 114.DAT from an install (mirror-aware read, like the other tables)."""
    from xi.xi_config import read_path_for
    return parse(read_path_for(menu_path(root)).read_bytes())


def save_menu(root, menu: MenuDat, dry_run: bool = False) -> Path:
    """Write 114.DAT back in place (``.base`` backup on first edit, later edits
    layer). Returns the path that was (or would be) written."""
    from xi.xi_config import editable_dat
    p = menu_path(root, for_write=not dry_run)
    if dry_run:
        return p
    out = editable_dat(p, fresh=False)
    out.write_bytes(menu.serialize())
    return out


def set_string(root, rom_path: str, idx: int, text: str, dry_run: bool = False) -> Path:
    """Set block ``idx`` of a fixed-stride name/help table to ``text``, growing
    the table past its retail count by cloning a real block's shape. Blocks in
    between are filled with '.' like retail's unnamed rows."""
    from xi.xi_config import editable_dat, read_path_for
    p = dat_path(root, rom_path, for_write=not dry_run)
    src = read_path_for(p)
    if not src.exists():
        raise MenuError(f'string table not found: {p}')
    t = D.parse(src.read_bytes())
    if t.variable:
        raise MenuError(f'{rom_path} is a variable-layout table; names are fixed-stride')
    template = bytearray(t.blocks[0])
    while len(t.blocks) <= idx:
        t.blocks.append(bytearray(D.set_text(template, 0, '.')))
    t.blocks[idx] = bytearray(D.set_text(t.blocks[idx], 0, text))
    if dry_run:
        return p
    out = editable_dat(p, fresh=False)
    out.write_bytes(D.serialize(t))
    return out


def set_texts(kind: str, root, idx: int, text: dict, dry_run: bool = False) -> List[Path]:
    """Names and help for a record in every language table (JP falls back to
    EN, help to '.' so a grown table never shows garbage)."""
    k = KINDS[kind]
    written = []
    name_en = text['name_en']
    help_en = text.get('help_en') or '.'
    for lang in LANGS:
        name = text.get(f'name_{lang}') or name_en
        helptext = text.get(f'help_{lang}') or help_en
        written.append(set_string(root, k.names[lang], idx, name, dry_run))
        written.append(set_string(root, k.help[lang], idx, helptext, dry_run))
    return written


def clear_record(kind: str, root, idx: int, dry_run: bool = False) -> List[Path]:
    """Undo: empty the record and put '.' back in every string table."""
    k = KINDS[kind]
    menu = load_menu(root)
    written = []
    if idx < menu.count(kind):
        menu.set_record(kind, idx, b'\0' * k.stride)
        written.append(save_menu(root, menu, dry_run))
    for lang in LANGS:
        for rom in (k.names[lang], k.help[lang]):
            written.append(set_string(root, rom, idx, '.', dry_run))
    return written


def read_names(kind: str, root, lang: str = 'en') -> List[str]:
    """[name] indexed by record id from the install's table ('' when missing)."""
    from xi.xi_config import read_path_for
    p = read_path_for(dat_path(root, KINDS[kind].names[lang]))
    if not p.exists():
        return []
    t = D.parse(p.read_bytes())
    return [D.get_text(b, 0) for b in t.blocks]


# ── server side ──────────────────────────────────────────────────────────────

_SPELL_COLUMNS = {'mp': 'mpCost', 'element': 'element', 'skill': 'skill'}


def server_snippet(kind: str, d: dict, new_id: int, donor: int) -> str:
    """SQL that clones the donor's server row under the new id, with the
    definition's overrides where the client and server fields correspond. A
    template — the server's own table (stock or a module's) is the operator's
    call, and the animation comes from the ability action that publishes it."""
    name = d['name']
    fields = d.get('fields') or {}
    if kind == 'spell':
        overrides = {'spellid': str(new_id), 'name': f"'{name}'"}
        if 'mp' in fields:
            overrides['mpCost'] = str(int(fields['mp']))
        if 'cast' in fields:
            overrides['castTime'] = str(int(fields['cast']) * 250)
        if 'recast' in fields:
            overrides['recastTime'] = str(int(fields['recast']) * 250)
        cols = ['spellid', 'name', 'jobs', 'group', 'family', 'element', 'zonemisc', 'validTargets',
                'skill', 'mpCost', 'castTime', 'recastTime', 'message', 'magicBurstMessage',
                'animation', 'animationTime', 'AOE', 'base', 'multiplier', 'CE', 'VE',
                'requirements', 'spell_range', 'radius', 'content_tag']
        sel = ', '.join(overrides.get(c, f'`{c}`') for c in cols)
        return (f"-- {name}: spell {new_id}, cloned from spell {donor} (edit jobs/animation as needed)\n"
                f"INSERT INTO `spell_list` ({', '.join(f'`{c}`' for c in cols)})\n"
                f"SELECT {sel} FROM `spell_list` WHERE `spellid` = {donor};")
    # The server's ability ids are the client's command ids minus 512 (Provoke: 547 in
    # the client, 35 on the server), so both the new id and the donor shift.
    server_id, server_donor = new_id - 512, donor - 512
    overrides = {'abilityId': str(server_id), 'name': f"'{name}'"}
    if 'level' in fields:
        overrides['level'] = str(int(fields['level']))
    cols = ['abilityId', 'name', 'job', 'level', 'validTarget', 'recastTime', 'message1', 'message2',
            'animation', 'animationTime', 'castTime', 'actionType', 'range', 'isAOE', 'radius',
            'recastId', 'CE', 'VE', 'meritModID', 'addType', 'content_tag']
    sel = ', '.join(overrides.get(c, f'`{c}`') for c in cols)
    return (f"-- {name}: client command {new_id} = server ability {server_id}, cloned from ability {server_donor} "
            f"(client {donor}); edit job/animation as needed\n"
            f"INSERT INTO `abilities` ({', '.join(f'`{c}`' for c in cols)})\n"
            f"SELECT {sel} FROM `abilities` WHERE `abilityId` = {server_donor};")
